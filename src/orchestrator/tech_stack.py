"""Tech stack confirmation — pauses after Architecture to let the user review and modify tech decisions."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

logger = logging.getLogger(__name__)


def _ring_alarm() -> None:
    """Play an audible alert so the user knows input is needed."""
    try:
        from orchestrator.main import _ring_alarm as _alarm
        _alarm()
    except ImportError:
        print("\a", end="", flush=True)


def confirm_tech_stack(workspace: Path, dry_run: bool = False) -> str | None:
    """Present the proposed tech stack and get user confirmation.

    Reads ``architecture.json``, extracts ``tech_decisions``, and displays
    them with rationale.  The user can approve, modify, or abort.

    Returns:
        None — if approved (or no tech decisions found, or dry_run).
        str  — the user's modification text (already persisted to architecture.json).

    Raises:
        KeyboardInterrupt if the user chooses to abort.
    """
    if dry_run:
        return None

    architecture_path = workspace / "artifacts" / "architecture.json"
    if not architecture_path.exists():
        logger.debug("No architecture.json found — skipping tech stack confirmation")
        return None

    with open(architecture_path) as f:
        arch = json.load(f)

    tech_decisions = arch.get("tech_decisions", [])
    if not tech_decisions:
        logger.debug("architecture.json has no tech_decisions — skipping confirmation")
        return None

    console = Console()

    # ── Header ──────────────────────────────────────────────────────────
    console.print(
        f"\n\033[1;36m{'━' * 60}\033[0m\n"
        f"\033[1;36m  TECH STACK REVIEW\033[0m\n"
        f"  \033[2mReview the proposed technology choices before implementation.\033[0m\n"
        f"\033[1;36m{'━' * 60}\033[0m",
        highlight=False,
    )

    # ── Table of decisions ──────────────────────────────────────────────
    table = Table(show_header=True, header_style="bold cyan", padding=(0, 1))
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Decision", style="bold", max_width=40)
    table.add_column("Why", max_width=50)
    table.add_column("Alternatives", style="dim", max_width=30)

    for i, td in enumerate(tech_decisions, 1):
        decision = td.get("decision", "—")
        rationale = td.get("rationale", "—")
        alternatives = ", ".join(td.get("alternatives_considered", [])) or "—"
        table.add_row(str(i), decision, rationale, alternatives)

    console.print(table)
    console.print()

    # ── Prompt ──────────────────────────────────────────────────────────
    _ring_alarm()
    try:
        choice = console.input(
            "[bold][A]pprove  /  [M]odify  /  a[B]ort run: [/bold]"
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        raise KeyboardInterrupt("User aborted at tech stack confirmation")

    # ── Approve ─────────────────────────────────────────────────────────
    if not choice or choice in ("a", "approve", "y", "yes"):
        console.print("[green]Tech stack approved — proceeding to next step.[/green]\n")
        return None

    # ── Abort ───────────────────────────────────────────────────────────
    if choice in ("b", "abort", "q", "quit"):
        console.print("[bold red]Aborting run. State will be saved for resume.[/bold red]")
        raise KeyboardInterrupt("User aborted at tech stack confirmation")

    # ── Modify ──────────────────────────────────────────────────────────
    if choice in ("m", "modify", "e", "edit"):
        console.print(
            "\n[dim]Describe your preferred tech stack changes.  These will be\n"
            "communicated to the Principal Engineer and all downstream agents.\n"
            "Examples:\n"
            '  • "Use PostgreSQL instead of MongoDB"\n'
            '  • "Add Redis for session caching"\n'
            '  • "Prefer server-side rendering with Next.js"\n'
            "Type [bold]done[/bold] on its own line when finished.[/dim]\n"
        )
        lines: list[str] = []
        try:
            while True:
                line = console.input("[bold cyan]> [/bold cyan]")
                if line.strip().lower() == "done":
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            pass

        if not lines:
            console.print("[green]No modifications entered — proceeding as-is.[/green]\n")
            return None

        modifications = "\n".join(lines)

        # Persist the user's direction into architecture.json so every
        # downstream agent that reads it will see the override.
        arch["user_tech_stack_direction"] = modifications
        with open(architecture_path, "w") as f:
            json.dump(arch, f, indent=2)

        console.print(
            f"\n[green]Tech stack preferences saved to architecture.json.[/green]"
            f"\n[dim]All downstream agents (Principal Engineer, TPM, Engineers) "
            f"will incorporate your direction.[/dim]\n"
        )
        logger.info(f"User tech stack overrides saved: {modifications[:120]}...")
        return modifications

    # Unknown input — treat as approve
    console.print(f"[yellow]Unknown option '{choice}' — treating as approve.[/yellow]\n")
    return None
