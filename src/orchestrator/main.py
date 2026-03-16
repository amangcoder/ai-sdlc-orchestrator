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
from orchestrator.models import PhaseStatus, WorkflowType


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
    parser.add_argument("--max-concurrent-agents", type=int, default=None, metavar="N",
                        help="Max agents to run in parallel (0=unlimited, default from config)")
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
    console.print(f"Workflow: {state.workflow_type.value} | Total cost: ${state.total_cost_usd:.4f} | Review cycles: {state.review_cycles}")
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
    if args.enhanced_perception:
        config.enhanced_perception = True
    if args.max_concurrent_agents is not None:
        config.max_concurrent_agents = args.max_concurrent_agents
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

    engine = OrchestratorEngine(config=config, dry_run=args.dry_run)
    state = asyncio.run(engine.run(
        args.feature_request,
        single_phase=args.phase,
        from_phase=args.from_phase,
        resume=args.resume,
        workflow_type=workflow_type,
        custom_workflow=custom_workflow,
    ))

    _print_summary(state, console)

    if any(p.status == PhaseStatus.FAILED for p in state.phases.values()):
        sys.exit(1)
