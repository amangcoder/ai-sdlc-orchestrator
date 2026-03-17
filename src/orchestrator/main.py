"""CLI entry point for the AI SDLC Orchestrator."""

from __future__ import annotations

import argparse
import asyncio
import json
import re as _re
import subprocess
import sys
from pathlib import Path

import structlog
from rich.console import Console
from rich.table import Table

from orchestrator.config import load_config
from orchestrator.engine import OrchestratorEngine
from orchestrator.agents import AgentInvocation
from orchestrator.models import ModelTier, PhaseStatus, WorkflowType

MAX_FEATURE_REQUEST_LEN = 10_000
_SUSPICIOUS_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"read.*\.(ssh|env|key|pem|credentials)",
    r"write.*exfil",
    r"system\s*prompt",
]


def sanitize_feature_request(text: str) -> str:
    """Validate and sanitize user-provided feature request text."""
    if len(text) > MAX_FEATURE_REQUEST_LEN:
        raise ValueError(f"Feature request too long ({len(text)} chars, max {MAX_FEATURE_REQUEST_LEN})")
    for pattern in _SUSPICIOUS_PATTERNS:
        if _re.search(pattern, text, _re.IGNORECASE):
            structlog.get_logger(__name__).warning(
                "suspicious_pattern_in_feature_request", pattern=pattern
            )
    return text


WORKFLOW_ALIASES: dict[str, WorkflowType] = {
    "feature": WorkflowType.FEATURE_DEVELOPMENT,
    "feature_development": WorkflowType.FEATURE_DEVELOPMENT,
    "bugfix": WorkflowType.BUGFIX,
    "bug": WorkflowType.BUGFIX,
    "refactor": WorkflowType.REFACTOR,
    "perf": WorkflowType.PERFORMANCE_OPTIMIZATION,
    "performance": WorkflowType.PERFORMANCE_OPTIMIZATION,
    "performance_optimization": WorkflowType.PERFORMANCE_OPTIMIZATION,
    "security": WorkflowType.SECURITY_AUDIT,
    "security_audit": WorkflowType.SECURITY_AUDIT,
}


def _ring_alarm() -> None:
    """Play an audible alert. Uses macOS system sound, falls back to terminal bell."""
    try:
        sound = Path("/System/Library/Sounds/Glass.aiff")
        if sound.exists():
            subprocess.Popen(
                ["afplay", str(sound)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            print("\a", end="", flush=True)
    except OSError:
        print("\a", end="", flush=True)


def _confirm_agent_invocation(invocation: AgentInvocation) -> AgentInvocation | None:
    """Interactive confirmation loop before sending a prompt to an agent.

    Returns the (possibly modified) invocation on approval, or None to abort the run.
    The loop continues until the user approves or aborts — there is no "skip".
    """
    from copy import deepcopy

    from rich.panel import Panel
    from rich.syntax import Syntax

    console = Console()
    inv = deepcopy(invocation)

    while True:
        # Build prompt preview (first 20 lines)
        prompt_lines = inv.prompt.splitlines()
        preview_lines = prompt_lines[:20]
        preview = "\n".join(preview_lines)
        if len(prompt_lines) > 20:
            preview += f"\n... ({len(prompt_lines) - 20} more lines)"

        # Build info panel
        model_str = inv.model.value if isinstance(inv.model, ModelTier) else str(inv.model)
        display_name = inv.display_name or inv.agent_name
        info = (
            f"[bold]Agent:[/bold]   {display_name}\n"
            f"[bold]Model:[/bold]   {model_str}\n"
            f"[bold]Turns:[/bold]   {inv.max_turns} max\n"
            f"[bold]Prompt:[/bold]  {len(inv.prompt):,} chars, {len(prompt_lines)} lines\n"
        )
        if inv.isolation:
            info += f"[bold]Isolation:[/bold] {inv.isolation}\n"

        info += f"\n[dim]--- Prompt Preview (first 20 lines) ---[/dim]\n{preview}"

        console.print(Panel(info, title="AGENT CONFIRMATION", border_style="cyan"))

        _ring_alarm()
        try:
            choice = console.input(
                "[bold][Y]es / [v]iew full prompt / [e]dit / [a]bort run: [/bold]"
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None

        if not choice or choice in ("y", "yes"):
            return inv

        if choice in ("v", "view"):
            console.print(Syntax(inv.prompt, "markdown", word_wrap=True))
            continue

        if choice in ("e", "edit"):
            console.print(
                "[dim]Type your modifications. Use 'model=opus' or 'turns=50' to change settings.\n"
                "Any other text is appended as extra instructions to the prompt.\n"
                "Type 'done' on a new line when finished.[/dim]"
            )
            edit_lines: list[str] = []
            extra_instructions: list[str] = []
            try:
                while True:
                    line = console.input("[bold]edit> [/bold]")
                    if line.strip().lower() == "done":
                        break
                    edit_lines.append(line)
            except (EOFError, KeyboardInterrupt):
                continue

            for line in edit_lines:
                stripped = line.strip()
                # Parse model= command
                if stripped.startswith("model="):
                    model_val = stripped.split("=", 1)[1].strip().lower()
                    try:
                        new_model = ModelTier(model_val)
                        console.print(f"[green]Model changed: {model_str} → {new_model.value}[/green]")
                        inv.model = new_model
                    except ValueError:
                        console.print(f"[red]Unknown model: {model_val}. Use: haiku, sonnet, opus[/red]")
                # Parse turns= command
                elif stripped.startswith("turns="):
                    try:
                        new_turns = int(stripped.split("=", 1)[1].strip())
                        console.print(f"[green]Max turns changed: {inv.max_turns} → {new_turns}[/green]")
                        inv.max_turns = new_turns
                    except ValueError:
                        console.print(f"[red]Invalid turns value: {stripped}[/red]")
                else:
                    extra_instructions.append(line)

            if extra_instructions:
                addition = "\n".join(extra_instructions)
                inv.prompt += f"\n\n## Additional Instructions (from user)\n\n{addition}\n"
                console.print(f"[green]Appended {len(extra_instructions)} line(s) to prompt.[/green]")

            console.print()
            continue

        if choice in ("a", "abort"):
            console.print("[bold red]Aborting run. State will be saved for resume.[/bold red]")
            return None

        console.print("[yellow]Unknown option. Use y/v/e/a.[/yellow]")


def _configure_structlog(json_logs: bool) -> None:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestrate",
        description="AI SDLC Orchestrator — coordinate AI agents through the full SDLC pipeline",
    )
    parser.add_argument("feature_request", nargs="?", default=None,
                        help="Feature request to implement, or 'validate <dir>' to validate artifacts")
    parser.add_argument("validate_dir", nargs="?", default=None, type=Path,
                        help="Artifacts directory (only used when feature_request is 'validate')")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts without calling agents")
    parser.add_argument("--resume", action="store_true", help="Resume from last saved state, skipping completed phases")
    parser.add_argument("--resume-run", metavar="RUN_ID", dest="resume_run_id",
                        help="Resume a specific run by its ID (e.g. orchestrate --resume-run abc123def456 \"...\")")
    parser.add_argument("--phase", metavar="PHASE", help="Run a single phase only (pm|architect|engineer|qa|reviewer)")
    parser.add_argument("--from-phase", metavar="PHASE", dest="from_phase", help="Start pipeline from this phase, skipping earlier ones")
    parser.add_argument("--workflow", metavar="TYPE",
                        help="Workflow type: feature, bugfix, refactor, perf, security")
    parser.add_argument("--workflow-file", type=Path, metavar="PATH",
                        help="Path to custom workflow definition file")
    parser.add_argument("--config", type=Path, default=None, metavar="PATH", help="Path to config YAML file")
    parser.add_argument("--log-format", choices=["console", "json"], default="console", help="Log output format")
    parser.add_argument("--enhanced-perception", action="store_true",
                        help="Enable enhanced perception mode: enrich prompts via meta-cognitive pre-processing")
    parser.add_argument("--self-orchestrate", action="store_true",
                        help="Let the AI design the optimal pipeline for your request before executing")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Skip confirmation prompts (auto-approve self-orchestrate plan)")
    parser.add_argument("-c", "--confirm", action="store_true",
                        help="Review and approve each agent prompt before execution")
    parser.add_argument("--no-confirm-tech-stack", dest="tech_stack_confirmation",
                        action="store_false", default=None,
                        help="Skip the tech stack review prompt after Architecture")
    parser.add_argument("--max-concurrent-agents", type=int, default=None, metavar="N",
                        help="Max agents to run in parallel (0=unlimited, default from config)")
    parser.add_argument("--list-runs", action="store_true",
                        help="List all resumable runs in the workspace, then optionally select one to resume")
    # Debate phase arguments
    parser.add_argument("--debate", action="store_true",
                        help="Enable pre-pipeline debate phase: researchers and brainstormers argue, mediator synthesizes")
    parser.add_argument("--researchers", type=int, default=None, metavar="N",
                        help="Number of Deep Researcher agents in debate (default: 2)")
    parser.add_argument("--brainstormers", type=int, default=None, metavar="N",
                        help="Number of Brainstormer agents in debate (default: 2)")
    parser.add_argument("--debate-rounds", type=int, default=None, metavar="N",
                        help="Max debate rounds before mediation (default: 3)")
    # Knowledge integration arguments
    parser.add_argument("--knowledge", action="store_true", default=None,
                        help="Enable AICoder knowledge integration for faster agent exploration")
    parser.add_argument("--no-knowledge", dest="knowledge", action="store_false",
                        help="Disable AICoder knowledge integration")
    parser.add_argument("--no-checklist-verify", dest="checklist_verify",
                        action="store_false", default=None,
                        help="Skip post-write quality checklist verification in planning agents")
    return parser


def _print_summary(state, console: Console) -> None:
    table = Table(title=f"Run {state.run_id} — {state.feature_request[:60]}")
    table.add_column("Phase / Step", style="bold")
    table.add_column("Status")
    table.add_column("Cost (USD)", justify="right")
    table.add_column("Error")

    status_colors = {
        PhaseStatus.COMPLETED: "green",
        PhaseStatus.FAILED: "red",
        PhaseStatus.RUNNING: "yellow",
        PhaseStatus.PENDING: "dim",
        PhaseStatus.SKIPPED: "dim",
    }

    for phase_name, phase_state in state.phases.items():
        color = status_colors.get(phase_state.status, "white")
        status_str = f"[{color}]{phase_state.status.value}[/{color}]"
        cost_str = f"${phase_state.cost_usd:.4f}" if phase_state.cost_usd else "-"
        error_str = (phase_state.error or "")[:60]
        table.add_row(phase_name, status_str, cost_str, error_str)

    console.print(table)
    tokens_str = ""
    total_tokens = state.total_input_tokens + state.total_output_tokens
    if total_tokens > 0:
        tokens_str = f" | Tokens: {total_tokens:,} ({state.total_input_tokens:,} in / {state.total_output_tokens:,} out)"
    console.print(f"Workflow: {state.workflow_type.value} | Total cost: ${state.total_cost_usd:.4f}{tokens_str} | Review cycles: {state.review_cycles}")
    console.print(f"Artifacts: {state.workspace_dir}/artifacts/")
    console.print(f"Log: {state.workspace_dir}/logs/run-{state.run_id}.jsonl")


def _print_orchestration_plan(plan, console: Console) -> None:
    """Display the self-orchestration plan for user confirmation."""
    from rich.panel import Panel
    from rich.text import Text

    from orchestrator.workflows import BUILTIN_WORKFLOWS, parse_custom_workflow

    # Header
    wf_label = plan.workflow_type.value.replace("_", " ").title()
    flags = []
    if plan.custom_workflow:
        flags.append("custom pipeline")
    if plan.enhanced_perception:
        flags.append("enhanced perception")
    flags_str = f"  ({', '.join(flags)})" if flags else ""

    console.print(f"[bold cyan]Proposed Pipeline:[/bold cyan] [bold]{wf_label}[/bold]{flags_str}")
    console.print(f"[dim]Rationale: {plan.rationale}[/dim]\n")

    # Resolve steps to display
    if plan.custom_workflow:
        try:
            workflow = parse_custom_workflow(plan.custom_workflow)
        except Exception:
            console.print(f"[yellow]Custom workflow definition:[/yellow]\n{plan.custom_workflow}\n")
            return
    else:
        workflow = BUILTIN_WORKFLOWS.get(plan.workflow_type)
        if workflow is None:
            return

    # Build a step table
    step_table = Table(title=f"Workflow: {workflow.name} ({len(workflow.steps)} steps)", show_lines=False)
    step_table.add_column("#", style="dim", width=3)
    step_table.add_column("Step", style="bold")
    step_table.add_column("Agent")
    step_table.add_column("Inputs", style="dim")
    step_table.add_column("Outputs", style="dim")
    step_table.add_column("Flags", style="dim")

    for i, step in enumerate(workflow.steps, 1):
        role_label = step.agent_role.value.replace("_", " ").title()
        inputs = ", ".join(step.inputs) if step.inputs else "-"
        outputs = ", ".join(step.outputs) if step.outputs else "-"
        step_flags = []
        if step.parallel:
            step_flags.append("parallel")
        if step.gate:
            step_flags.append(f"gate:{step.gate}")
        if step.on_fail != "escalate":
            step_flags.append(f"on_fail:{step.on_fail}")
        flags_cell = ", ".join(step_flags) if step_flags else "-"
        step_table.add_row(str(i), step.name, role_label, inputs, outputs, flags_cell)

    console.print(step_table)
    console.print()


def _cmd_validate(artifacts_dir: Path) -> None:
    from orchestrator.models import ARTIFACT_MODELS
    from orchestrator.validation import validate_artifact_file
    console = Console()
    failed = False

    for artifact_name in ARTIFACT_MODELS.keys():
        artifact_path = artifacts_dir / f"{artifact_name}.json"
        if not artifact_path.exists():
            continue
        result = validate_artifact_file(artifact_path, artifact_name)
        if result.valid:
            console.print(f"[green]OK[/green]  {artifact_name}.json")
        else:
            console.print(f"[red]FAIL[/red] {artifact_name}.json")
            for err in result.errors:
                console.print(f"     {err}")
            failed = True

    sys.exit(1 if failed else 0)


def _cmd_list_runs(workspace: Path, interactive: bool = True) -> str | None:
    """List all resumable runs in the workspace.

    Returns a run_id if the user selects one interactively, else None.
    """
    console = Console()
    state_files = sorted(workspace.glob("state-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    # Also include state.json if it exists and its run_id isn't already covered
    generic_state = workspace / "state.json"
    if generic_state.exists():
        try:
            generic_data = json.loads(generic_state.read_text())
            generic_run_id = generic_data.get("run_id")
            covered_ids = {sf.stem.removeprefix("state-") for sf in state_files}
            if generic_run_id and generic_run_id not in covered_ids:
                state_files.append(generic_state)
        except (json.JSONDecodeError, OSError):
            pass

    if not state_files:
        console.print("[dim]No saved runs found.[/dim]")
        return None

    table = Table(title="Resumable Runs")
    table.add_column("#", style="dim", width=3)
    table.add_column("Run ID", style="bold")
    table.add_column("Feature Request")
    table.add_column("Workflow")
    table.add_column("Status")
    table.add_column("Cost", justify="right")
    table.add_column("Last Modified", style="dim")

    runs: list[dict] = []
    for sf in state_files:
        try:
            data = json.loads(sf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        run_id = data.get("run_id", sf.stem.removeprefix("state-"))
        feature = data.get("feature_request", "?")[:50]
        wf = data.get("workflow_type", "?")
        cost = f"${data.get('total_cost_usd', 0):.2f}"
        phases = data.get("phases", {})
        completed = sum(1 for p in phases.values() if p.get("status") == "completed")
        failed = sum(1 for p in phases.values() if p.get("status") == "failed")
        total = len(phases)
        if failed:
            status = f"[red]{completed}/{total} ({failed} failed)[/red]"
        elif completed == total and total > 0:
            status = f"[green]{completed}/{total} done[/green]"
        else:
            status = f"[yellow]{completed}/{total}[/yellow]"
        mtime = sf.stat().st_mtime
        from datetime import datetime, timezone
        modified = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        runs.append({"run_id": run_id, "feature": feature})
        table.add_row(str(len(runs)), run_id, feature, wf, status, cost, modified)

    console.print(table)

    if not interactive or not runs:
        return None

    console.print("\n[dim]Enter a number to resume that run, or press Enter to cancel.[/dim]")
    try:
        choice = console.input("[bold]> [/bold]").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not choice:
        return None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(runs):
            return runs[idx]["run_id"]
    except ValueError:
        # Maybe they typed a run_id directly
        for r in runs:
            if r["run_id"] == choice:
                return choice

    console.print("[red]Invalid selection.[/red]")
    return None


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.resume_run_id and not _re.match(r'^[0-9a-f]{12}$', args.resume_run_id):
        parser.error(f"Invalid run ID format: {args.resume_run_id}")

    if args.feature_request == "validate":
        _configure_structlog(json_logs=False)
        if not args.validate_dir:
            parser.error("validate requires an artifacts directory")
        _cmd_validate(args.validate_dir)
        return

    # --list-runs: show resumable runs and optionally pick one
    if args.list_runs:
        _configure_structlog(json_logs=False)
        config = load_config(args.config)
        workspace = Path(config.workspace_dir).resolve()
        selected = _cmd_list_runs(workspace, interactive=True)
        if selected:
            # Re-invoke with --resume-run
            args.resume_run_id = selected
            args.resume = False  # resume_run_id implies resume
            if not args.feature_request:
                # Load feature_request from state so user doesn't have to re-type it
                state_path = workspace / f"state-{selected}.json"
                if not state_path.exists():
                    # Fall back to generic state.json if it contains this run
                    state_path = workspace / "state.json"
                data = json.loads(state_path.read_text())
                args.feature_request = data.get("feature_request", "")
        else:
            sys.exit(0)

    if not args.feature_request:
        parser.print_help()
        sys.exit(1)

    _configure_structlog(json_logs=(args.log_format == "json"))
    config = load_config(args.config)
    args.feature_request = sanitize_feature_request(args.feature_request)
    if args.enhanced_perception:
        config.enhanced_perception = True
    if args.confirm:
        config.confirm = True
    if args.tech_stack_confirmation is not None:
        config.tech_stack_confirmation = args.tech_stack_confirmation
    if args.max_concurrent_agents is not None:
        config.max_concurrent_agents = args.max_concurrent_agents
    if args.checklist_verify is not None:
        config.checklist_verify = args.checklist_verify
    console = Console()

    # Self-orchestrate: let the AI design the pipeline
    workflow_type = None
    custom_workflow = None

    if args.self_orchestrate:
        from orchestrator.self_orchestrate import clarify, revise_plan, self_orchestrate

        feature_request = args.feature_request
        project_root = Path.cwd()

        # Phase 1: Clarifying questions loop
        if not args.yes:
            conversation: list[tuple[str, str]] = []
            console.print("[bold cyan]Self-Orchestrate:[/bold cyan] Analyzing your request...\n")

            while True:
                result = asyncio.run(clarify(
                    feature_request=args.feature_request,
                    conversation=conversation,
                    project_root=project_root,
                ))

                if result.ready or not result.questions:
                    if result.refined_request != args.feature_request:
                        feature_request = result.refined_request
                    if conversation:
                        console.print("[green]All clear — proceeding to pipeline design.[/green]\n")
                    break

                # Display questions
                console.print("[bold cyan]Clarifying Questions:[/bold cyan]")
                for i, q in enumerate(result.questions, 1):
                    console.print(f"  {i}. {q}")
                console.print()

                try:
                    _ring_alarm()
                    answer = console.input(
                        "[bold]Your answers (or 'skip' to proceed without answering): [/bold]"
                    ).strip()
                except (EOFError, KeyboardInterrupt):
                    console.print("\nAborted.")
                    sys.exit(0)

                if answer.lower() in ("skip", "s", "proceed", "done"):
                    feature_request = result.refined_request
                    console.print()
                    break

                # Record Q&A and loop
                questions_text = "\n".join(f"{i}. {q}" for i, q in enumerate(result.questions, 1))
                conversation.append((questions_text, answer))
                feature_request = result.refined_request
                console.print()

        # Phase 2: Pipeline design
        console.print("[bold cyan]Self-Orchestrate:[/bold cyan] Designing the optimal pipeline...\n")
        plan = asyncio.run(self_orchestrate(feature_request, project_root=project_root))

        # Phase 3: Interactive approval / revision loop
        while True:
            _print_orchestration_plan(plan, console)

            if args.yes:
                break

            try:
                _ring_alarm()
                answer = console.input(
                    "[bold]Proceed? [Y]es / [n]o / or type changes: [/bold]"
                ).strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\nAborted.")
                sys.exit(0)

            if not answer or answer.lower() in ("y", "yes"):
                console.print()
                break
            if answer.lower() in ("n", "no", "q", "quit"):
                console.print("Aborted.")
                sys.exit(0)

            # User typed modification feedback — revise the plan
            console.print(f"\n[bold cyan]Self-Orchestrate:[/bold cyan] Revising plan...\n")
            plan = asyncio.run(revise_plan(
                feature_request=feature_request,
                current_plan=plan,
                user_feedback=answer,
                project_root=project_root,
            ))

        workflow_type = plan.workflow_type
        custom_workflow = plan.custom_workflow
        if plan.enhanced_perception:
            config.enhanced_perception = True

        # Use the enriched feature request for the actual run
        args.feature_request = feature_request
    else:
        # Resolve workflow type from CLI args
        if args.workflow:
            wf_key = args.workflow.lower().replace("-", "_")
            workflow_type = WORKFLOW_ALIASES.get(wf_key)
            if workflow_type is None:
                parser.error(f"Unknown workflow type: {args.workflow}. Choose from: {list(WORKFLOW_ALIASES.keys())}")

        # Load custom workflow definition
        if args.workflow_file:
            if not args.workflow_file.exists():
                parser.error(f"Workflow file not found: {args.workflow_file}")
            custom_workflow = args.workflow_file.read_text()
            workflow_type = WorkflowType.CUSTOM

    # Apply debate CLI overrides
    if args.debate:
        config.debate.enabled = True
    if args.researchers is not None:
        config.debate.researcher_count = args.researchers
    if args.brainstormers is not None:
        config.debate.brainstormer_count = args.brainstormers
    if args.debate_rounds is not None:
        config.debate.max_rounds = args.debate_rounds

    # Knowledge CLI overrides
    if args.knowledge is not None:
        config.knowledge.enabled = args.knowledge

    # Set up interrupt manager for graceful Ctrl+C pausing
    from orchestrator.interruption import InterruptManager

    interrupt_manager = InterruptManager()
    interrupt_manager.setup_signal_handler()
    workspace = Path(config.workspace_dir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    interrupt_manager.setup_sentinel(workspace)

    confirm_callback = _confirm_agent_invocation if config.confirm else None
    engine = OrchestratorEngine(
        config=config, dry_run=args.dry_run, interrupt_manager=interrupt_manager,
        confirm_callback=confirm_callback,
    )

    try:
        state = asyncio.run(engine.run(
            args.feature_request,
            single_phase=args.phase,
            from_phase=args.from_phase,
            resume=args.resume or bool(args.resume_run_id),
            resume_run_id=args.resume_run_id,
            workflow_type=workflow_type,
            custom_workflow=custom_workflow,
        ))
    except KeyboardInterrupt:
        # Hard interrupt (double Ctrl+C) — state was saved at last checkpoint
        console.print(f"\n[bold red]Hard interrupt.[/bold red] Resume with: orchestrate --resume \"...\" or --resume-run <RUN_ID>")
        sys.exit(130)
    finally:
        interrupt_manager.restore_signal_handler()

    _ring_alarm()
    _print_summary(state, console)

    if any(p.status == PhaseStatus.FAILED for p in state.phases.values()):
        sys.exit(1)
