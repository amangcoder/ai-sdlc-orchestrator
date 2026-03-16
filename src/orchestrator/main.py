"""CLI entry point for the AI SDLC Orchestrator."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import structlog
from rich.console import Console
from rich.table import Table

from orchestrator.config import load_config
from orchestrator.engine import OrchestratorEngine
from orchestrator.models import PhaseStatus


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
    parser.add_argument("--phase", metavar="PHASE", help="Run a single phase only (pm|architect|engineer|qa|reviewer)")
    parser.add_argument("--from-phase", metavar="PHASE", dest="from_phase", help="Start pipeline from this phase, skipping earlier ones")
    parser.add_argument("--config", type=Path, default=None, metavar="PATH", help="Path to config YAML file")
    parser.add_argument("--log-format", choices=["console", "json"], default="console", help="Log output format")
    return parser


def _print_summary(state, console: Console) -> None:
    table = Table(title=f"Run {state.run_id} — {state.feature_request[:60]}")
    table.add_column("Phase", style="bold")
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
    console.print(f"Total cost: ${state.total_cost_usd:.4f} | Review cycles: {state.review_cycles}")
    console.print(f"Artifacts: {state.workspace_dir}/artifacts/")
    console.print(f"Log: {state.workspace_dir}/logs/run-{state.run_id}.jsonl")


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


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.feature_request == "validate":
        _configure_structlog(json_logs=False)
        if not args.validate_dir:
            parser.error("validate requires an artifacts directory")
        _cmd_validate(args.validate_dir)
        return

    if not args.feature_request:
        parser.print_help()
        sys.exit(1)

    _configure_structlog(json_logs=(args.log_format == "json"))
    config = load_config(args.config)
    console = Console()

    engine = OrchestratorEngine(config=config, dry_run=args.dry_run)
    state = asyncio.run(engine.run(
        args.feature_request,
        single_phase=args.phase,
        from_phase=args.from_phase,
        resume=args.resume,
    ))

    _print_summary(state, console)

    if any(p.status == PhaseStatus.FAILED for p in state.phases.values()):
        sys.exit(1)
