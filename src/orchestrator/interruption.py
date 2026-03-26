"""Pipeline interruption manager — graceful pause, ad-hoc task injection, and resume."""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

from orchestrator.agents import AgentInvocation, AgentResult, invoke_agent
from orchestrator.models import InterruptEvent, ModelTier

if TYPE_CHECKING:
    from orchestrator.models import OrchestratorConfig, RunState
    from orchestrator.observability import RunLogger

logger = logging.getLogger(__name__)

# How quickly a second Ctrl+C must follow the first to trigger hard exit
_DOUBLE_SIGINT_WINDOW = 2.0


class InterruptManager:
    """Manages pipeline interruption via Ctrl+C or sentinel file.

    Detection happens at safe boundaries only (between steps, between waves,
    between sequential tasks). The manager never forcefully kills running agents.

    Two trigger mechanisms:
    1. SIGINT (Ctrl+C) — first press pauses, second within 2s hard-exits
    2. Sentinel file (workspace/.interrupt) — for external/headless triggering
    """

    def __init__(self) -> None:
        self._interrupted = threading.Event()
        self._reason: str | None = None
        self._sentinel_path: Path | None = None
        self._last_sigint_time: float = 0.0
        self._original_handler = None
        self._console = Console()

    def request_interrupt(self, reason: str = "user_request") -> None:
        """Signal that the pipeline should pause at the next safe boundary."""
        now = time.time()

        # Double Ctrl+C within window -> hard exit
        if self._interrupted.is_set() and (now - self._last_sigint_time) < _DOUBLE_SIGINT_WINDOW:
            self._console.print("\n[bold red]Hard interrupt — exiting immediately.[/bold red]")
            raise KeyboardInterrupt

        if not self._interrupted.is_set():
            self._interrupted.set()
            self._reason = reason
            self._console.print(
                "\n[bold yellow]⚠ Pausing pipeline at next safe point...[/bold yellow]"
                "\n[dim](current agent/wave will finish, then pause. Press Ctrl+C again to force quit)[/dim]"
            )

        self._last_sigint_time = now

    def should_interrupt(self) -> bool:
        """Check if an interrupt has been requested (signal or sentinel file)."""
        if self._interrupted.is_set():
            return True
        # Check sentinel file
        if self._sentinel_path and self._sentinel_path.exists():
            self._interrupted.set()
            self._reason = "sentinel_file"
            logger.info("Interrupt triggered via sentinel file")
            # Remove sentinel file after detection
            try:
                self._sentinel_path.unlink()
            except OSError:
                pass
            return True
        return False

    def clear(self) -> None:
        """Reset interrupt state after handling."""
        self._interrupted.clear()
        self._reason = None

    def setup_signal_handler(self) -> None:
        """Register SIGINT and SIGTERM handlers on the current asyncio event loop.

        SIGINT (Ctrl+C) triggers graceful interrupt. SIGTERM (docker stop, kill -TERM)
        also triggers interrupt for graceful shutdown before crash handler takes over.

        Must be called from within a running event loop (i.e., inside an async function).
        Falls back to signal.signal() if no event loop is available.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, self.request_interrupt)
            loop.add_signal_handler(signal.SIGTERM, lambda: self.request_interrupt(reason="sigterm"))
            logger.info("Interrupt handler registered (asyncio: SIGINT, SIGTERM)")
        except RuntimeError:
            # No running loop — use traditional signal handler
            self._original_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, lambda *_: self.request_interrupt())
            signal.signal(signal.SIGTERM, lambda *_: self.request_interrupt(reason="sigterm"))
            logger.info("Interrupt handler registered (signal: SIGINT, SIGTERM)")

    def restore_signal_handler(self) -> None:
        """Restore the original SIGINT and SIGTERM handlers."""
        try:
            loop = asyncio.get_running_loop()
            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGTERM)
        except (RuntimeError, ValueError):
            pass
        if self._original_handler is not None:
            signal.signal(signal.SIGINT, self._original_handler)
            self._original_handler = None

    def setup_sentinel(self, workspace: Path) -> None:
        """Enable sentinel file detection for external interrupt triggering.

        Any process can write to ``workspace/.interrupt`` to trigger a pause.
        """
        self._sentinel_path = workspace / ".interrupt"
        # Clean up stale sentinel from previous run
        if self._sentinel_path.exists():
            logger.info("Removing stale .interrupt sentinel file from previous run")
            try:
                self._sentinel_path.unlink()
            except OSError:
                pass


async def handle_interruption(
    state: RunState,
    config: OrchestratorConfig,
    interrupt_manager: InterruptManager,
    run_logger: RunLogger | None,
    project_root: Path,
    context: str = "between_steps",
) -> str:
    """Handle a pipeline interruption interactively.

    Saves state, shows status, prompts user for action, optionally runs an
    injected task, then returns "continue" or "abort".
    """
    console = Console()
    workspace = Path(state.workspace_dir)

    # Save state immediately
    state.interrupted = True
    _save_state_quick(state, workspace)

    if run_logger:
        run_logger.log_event("interrupt", {
            "step": state.current_step,
            "completed_steps": state.completed_steps,
            "reason": interrupt_manager._reason or "user_request",
            "context": context,
        })

    # Show status panel
    completed = ", ".join(state.completed_steps) if state.completed_steps else "(none)"
    current = state.current_step or "(between steps)"
    cost = f"${state.total_cost_usd:.2f}"

    panel_lines = [
        f"[bold]Run:[/bold]        {state.run_id}",
        f"[bold]Completed:[/bold]  {completed}",
        f"[bold]Paused at:[/bold]  {current} ({context})",
        f"[bold]Cost:[/bold]       {cost}",
    ]
    console.print()
    console.print(Panel(
        "\n".join(panel_lines),
        title="[bold yellow]Pipeline Paused[/bold yellow]",
        border_style="yellow",
    ))

    # Ring alarm to notify user that input is needed
    from orchestrator.main import _ring_alarm
    _ring_alarm()

    # Interactive prompt
    console.print("\nWhat would you like to do?")
    console.print("  [bold][1][/bold] Run an ad-hoc task")
    console.print("  [bold][2][/bold] Resume pipeline")
    console.print("  [bold][3][/bold] Abort pipeline")

    try:
        choice = console.input("\n[bold]> [/bold]").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\nAborting.")
        _record_interrupt_event(state, injected_prompt=None, resumed=False)
        return "abort"

    if choice in ("2", "resume", "r"):
        _record_interrupt_event(state, injected_prompt=None, resumed=True)
        state.interrupted = False
        interrupt_manager.clear()
        console.print("\n[green]Resuming pipeline...[/green]\n")
        return "continue"

    if choice in ("3", "abort", "q", "quit"):
        _record_interrupt_event(state, injected_prompt=None, resumed=False)
        _save_state_quick(state, workspace)
        console.print("\n[red]Pipeline aborted.[/red] Resume later with --resume or --resume-run")
        return "abort"

    # Choice 1: run ad-hoc task
    try:
        task_prompt = console.input("\n[bold]Enter your task:[/bold]\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\nAborting.")
        _record_interrupt_event(state, injected_prompt=None, resumed=False)
        return "abort"

    if not task_prompt:
        console.print("[dim]No task entered — resuming pipeline.[/dim]\n")
        _record_interrupt_event(state, injected_prompt=None, resumed=True)
        state.interrupted = False
        interrupt_manager.clear()
        return "continue"

    # Run the injected task
    console.print(f"\n[bold cyan]Running injected task...[/bold cyan]\n")
    result = await run_injected_task(
        prompt=task_prompt,
        config=config,
        workspace=workspace,
        project_root=project_root,
        run_logger=run_logger,
    )

    if result.success:
        console.print(f"\n[green]✓ Injected task complete[/green] (${result.cost_usd:.2f})")
    else:
        console.print(f"\n[red]✗ Injected task failed:[/red] {result.error}")

    state.total_cost_usd += result.cost_usd

    # Ask whether to continue
    try:
        resume_choice = console.input("\n[bold]Continue pipeline? [Y/n] [/bold]").strip()
    except (EOFError, KeyboardInterrupt):
        _record_interrupt_event(state, injected_prompt=task_prompt, resumed=False,
                                cost=result.cost_usd)
        return "abort"

    resumed = resume_choice.lower() not in ("n", "no")
    _record_interrupt_event(state, injected_prompt=task_prompt, resumed=resumed,
                            cost=result.cost_usd)

    if resumed:
        state.interrupted = False
        interrupt_manager.clear()
        console.print("\n[green]Resuming pipeline...[/green]\n")
        return "continue"
    else:
        _save_state_quick(state, workspace)
        console.print("\n[red]Pipeline aborted.[/red] Resume later with --resume or --resume-run")
        return "abort"


async def run_injected_task(
    prompt: str,
    config: OrchestratorConfig,
    workspace: Path,
    project_root: Path,
    run_logger: RunLogger | None = None,
) -> AgentResult:
    """Run a standalone agent for an ad-hoc injected task.

    Uses Sonnet model with READ_WRITE access in the same project context.
    """
    if run_logger:
        run_logger.log_event("injection_start", {"prompt": prompt[:200]})

    invocation = AgentInvocation(
        agent_name="injected_task",
        prompt=prompt,
        model=ModelTier.SONNET,
        max_turns=40,
        workspace_dir=str(workspace),
        project_root=str(project_root),
    )

    result = await invoke_agent(invocation)

    if run_logger:
        run_logger.log_event("injection_complete", {
            "success": result.success,
            "cost_usd": result.cost_usd,
            "error": result.error,
        })

    return result


def _record_interrupt_event(
    state: RunState,
    injected_prompt: str | None,
    resumed: bool,
    cost: float = 0.0,
) -> None:
    """Append an InterruptEvent to the run state history."""
    state.interrupt_history.append(InterruptEvent(
        timestamp=datetime.now(timezone.utc),
        step_paused_at=state.current_step or "(between steps)",
        reason="user_request",
        injected_prompt=injected_prompt,
        injection_cost_usd=cost,
        resumed=resumed,
    ))


def _save_state_quick(state: RunState, workspace: Path) -> None:
    """Quick state save — used during interruption before prompting user.

    Writes both ``state.json`` and ``state-<run_id>.json`` so the run
    can be resumed by ID later.
    """
    from orchestrator.persistence import save_run_state
    save_run_state(state, workspace)
