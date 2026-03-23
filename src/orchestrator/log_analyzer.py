"""Log Stream Intelligence Analyzer — post-run analysis of JSONL run logs.

Surfaces two categories of inefficiency:
  1. INDEXABLE DATA   — steps that appear in many runs (AICoder pre-index candidates)
  2. REPETITIVE ACTIONS — task retries and duplicate-role invocations within a run

Usage (programmatic):
    from orchestrator.log_analyzer import analyze_runs, format_report_text, format_report_json
    report = analyze_runs(log_dir, last_n=5)
    print(format_report_text(report, log_dir))

Usage (CLI):
    orchestrate --analyze-logs [run-id | --last N] [--json]
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class _TaskInvocation:
    run_id: str
    task_id: str
    step: str
    agent: str
    role: str
    model: str
    attempt: int


@dataclass
class _TaskResult:
    task_id: str
    success: bool
    cost_usd: float
    attempt: int


@dataclass
class _AgentResult:
    agent: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    attempt: int


@dataclass
class _RunSummary:
    run_id: str
    feature_request: str = ""
    workflow_type: str = ""
    total_cost_usd: float = 0.0
    task_invocations: list[_TaskInvocation] = field(default_factory=list)
    task_results: dict[str, _TaskResult] = field(default_factory=dict)
    agent_results: list[_AgentResult] = field(default_factory=list)


@dataclass
class AnalysisReport:
    """Populated by analyze_runs(); consumed by format_report_text/json."""

    run_ids: list[str]

    # REPETITIVE ACTIONS: steps retried 2+ times in the same run
    repeated_tasks: list[dict]

    # REPETITIVE ACTIONS: same agent role used for 2+ parallel steps in one run
    duplicate_role_steps: list[dict]

    # INDEXABLE DATA: steps appearing across multiple runs → pre-index candidates
    cross_run_step_freq: list[dict]

    # Cost breakdown (top-10 steps by cumulative cost)
    top_cost_steps: list[dict]

    # Token breakdown (top-10 agents by input tokens, when agent_result data exists)
    top_token_agents: list[dict]

    # Estimated waste from retried steps
    estimated_retry_cost_waste: float
    estimated_retry_token_waste: int


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------


def _load_run(log_path: Path) -> _RunSummary | None:
    """Parse a single run-{id}.jsonl file into a _RunSummary."""
    run_id = log_path.stem.removeprefix("run-")
    summary = _RunSummary(run_id=run_id)

    try:
        with log_path.open() as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                etype = ev.get("event", "")
                # run_id may be embedded in the event (old engine format)
                if ev.get("run_id"):
                    summary.run_id = ev["run_id"]

                if etype == "run_start":
                    summary.feature_request = ev.get("feature_request", "")
                    summary.workflow_type = ev.get("workflow_type", "")

                elif etype == "run_complete":
                    summary.total_cost_usd = ev.get("total_cost_usd", 0.0)

                elif etype == "task_invoke":
                    summary.task_invocations.append(
                        _TaskInvocation(
                            run_id=summary.run_id,
                            task_id=ev.get("task_id", ""),
                            step=ev.get("step", ""),
                            agent=ev.get("agent", ""),
                            role=ev.get("role", ev.get("agent", "")),
                            model=ev.get("model", ""),
                            attempt=ev.get("attempt", 1),
                        )
                    )

                elif etype == "task_result":
                    tid = ev.get("task_id", "")
                    res = _TaskResult(
                        task_id=tid,
                        success=ev.get("success", False),
                        cost_usd=ev.get("cost_usd", 0.0),
                        attempt=ev.get("attempt", 1),
                    )
                    # Keep the result with the highest attempt number
                    prev = summary.task_results.get(tid)
                    if prev is None or res.attempt > prev.attempt:
                        summary.task_results[tid] = res

                elif etype == "agent_result":
                    summary.agent_results.append(
                        _AgentResult(
                            agent=ev.get("agent", ""),
                            input_tokens=ev.get("input_tokens", 0),
                            output_tokens=ev.get("output_tokens", 0),
                            cost_usd=ev.get("cost_usd", 0.0),
                            attempt=ev.get("attempt", 1),
                        )
                    )

    except OSError:
        return None

    return summary


def _collect_logs(log_dir: Path, run_id: str | None, last_n: int | None) -> list[Path]:
    """Return JSONL paths matching the selection criteria, newest first."""
    all_logs = sorted(
        log_dir.glob("run-*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not all_logs:
        return []
    if run_id:
        return [p for p in all_logs if run_id in p.stem]
    if last_n:
        return all_logs[: last_n]
    return all_logs[:1]  # default: most recent run


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------


def _analyze(runs: list[_RunSummary]) -> AnalysisReport:
    # ------------------------------------------------------------------
    # REPETITIVE ACTIONS — retried tasks (attempt >= 2)
    # ------------------------------------------------------------------
    repeated_tasks: list[dict] = []
    for run in runs:
        # Map (step, agent) → list of invocations
        groups: dict[tuple[str, str], list[_TaskInvocation]] = defaultdict(list)
        for inv in run.task_invocations:
            groups[(inv.step, inv.agent)].append(inv)

        for (step, agent), invs in groups.items():
            max_attempt = max(i.attempt for i in invs)
            if max_attempt < 2:
                continue
            # Sum cost from all task_result entries that belong to these invocations
            task_ids = {i.task_id for i in invs}
            total_cost = sum(
                r.cost_usd
                for tid, r in run.task_results.items()
                if tid in task_ids
            )
            repeated_tasks.append(
                {
                    "run_id": run.run_id,
                    "step": step,
                    "agent": agent,
                    "attempts": max_attempt,
                    "cost_usd": total_cost,
                    "feature": run.feature_request[:60],
                }
            )

    repeated_tasks.sort(key=lambda x: x["attempts"], reverse=True)

    # ------------------------------------------------------------------
    # REPETITIVE ACTIONS — same role on multiple parallel steps in one run
    # ------------------------------------------------------------------
    duplicate_role_steps: list[dict] = []
    for run in runs:
        role_to_steps: dict[str, set[str]] = defaultdict(set)
        for inv in run.task_invocations:
            if inv.attempt == 1:
                role_to_steps[inv.role].add(inv.step)

        for role, steps in role_to_steps.items():
            if len(steps) >= 2:
                duplicate_role_steps.append(
                    {
                        "run_id": run.run_id,
                        "role": role,
                        "steps": sorted(steps),
                        "count": len(steps),
                        "feature": run.feature_request[:60],
                    }
                )

    duplicate_role_steps.sort(key=lambda x: x["count"], reverse=True)

    # ------------------------------------------------------------------
    # INDEXABLE DATA — steps appearing in multiple runs
    # ------------------------------------------------------------------
    step_run_ids: dict[str, set[str]] = defaultdict(set)
    step_agent: dict[str, str] = {}
    step_cost: dict[str, float] = defaultdict(float)

    for run in runs:
        # Map task_id → step for cost aggregation
        tid_to_step: dict[str, str] = {
            inv.task_id: inv.step
            for inv in run.task_invocations
            if inv.attempt == 1
        }
        for inv in run.task_invocations:
            if inv.attempt == 1:
                step_run_ids[inv.step].add(run.run_id)
                step_agent[inv.step] = inv.agent
        for tid, res in run.task_results.items():
            step = tid_to_step.get(tid)
            if step:
                step_cost[step] += res.cost_usd

    cross_run_step_freq = [
        {
            "step": step,
            "agent": step_agent.get(step, "unknown"),
            "run_count": len(rids),
            "total_cost_usd": step_cost.get(step, 0.0),
        }
        for step, rids in step_run_ids.items()
        if len(rids) >= 2
    ]
    cross_run_step_freq.sort(
        key=lambda x: (x["run_count"], x["total_cost_usd"]), reverse=True
    )

    # ------------------------------------------------------------------
    # Cost breakdown — top steps by cumulative cost
    # ------------------------------------------------------------------
    step_cost_sorted = [
        {
            "step": step,
            "agent": step_agent.get(step, "?"),
            "total_cost_usd": cost,
        }
        for step, cost in sorted(step_cost.items(), key=lambda x: x[1], reverse=True)
        if cost > 0
    ][:10]

    # ------------------------------------------------------------------
    # Token breakdown — top agents by input tokens (from agent_result events)
    # ------------------------------------------------------------------
    agent_input_tokens: dict[str, int] = defaultdict(int)
    for run in runs:
        for ar in run.agent_results:
            if ar.attempt == 1:
                agent_input_tokens[ar.agent] += ar.input_tokens

    top_token_agents = [
        {"agent": agent, "total_input_tokens": tokens}
        for agent, tokens in sorted(
            agent_input_tokens.items(), key=lambda x: x[1], reverse=True
        )
        if tokens > 0
    ][:10]

    # ------------------------------------------------------------------
    # Estimated retry waste
    # ------------------------------------------------------------------
    retry_cost_waste = 0.0
    retry_token_waste = 0
    for run in runs:
        for ar in run.agent_results:
            if ar.attempt >= 2:
                retry_cost_waste += ar.cost_usd
                retry_token_waste += ar.input_tokens

    return AnalysisReport(
        run_ids=[r.run_id for r in runs],
        repeated_tasks=repeated_tasks,
        duplicate_role_steps=duplicate_role_steps[:10],
        cross_run_step_freq=cross_run_step_freq[:10],
        top_cost_steps=step_cost_sorted,
        top_token_agents=top_token_agents,
        estimated_retry_cost_waste=retry_cost_waste,
        estimated_retry_token_waste=retry_token_waste,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def analyze_runs(
    log_dir: Path,
    run_id: str | None = None,
    last_n: int | None = None,
) -> AnalysisReport:
    """Load and analyze runs from *log_dir*.

    Args:
        log_dir:  Directory containing ``run-*.jsonl`` files.
        run_id:   Analyze a specific run (partial match on file stem).
        last_n:   Analyze the N most-recently modified runs.
                  Omit both to analyze only the single most recent run.
    """
    paths = _collect_logs(log_dir, run_id, last_n)
    runs = [r for p in paths if (r := _load_run(p)) is not None]
    return _analyze(runs)


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def format_report_text(report: AnalysisReport, log_dir: Path) -> str:
    """Return a human-readable report string."""
    W = 70
    lines: list[str] = []

    def rule(label: str = "") -> None:
        if label:
            lines.append(f"── {label} " + "─" * max(0, W - 4 - len(label)))
        else:
            lines.append("─" * W)

    lines.append("=" * W)
    lines.append("  LOG STREAM INTELLIGENCE ANALYZER")
    lines.append("=" * W)
    n = len(report.run_ids)
    lines.append(f"Runs analyzed : {n}  ({', '.join(report.run_ids[:5])}{'…' if n > 5 else ''})")
    lines.append(f"Log directory : {log_dir}")
    lines.append("")

    # ── REPETITIVE ACTIONS ─────────────────────────────────────────────
    rule("REPETITIVE ACTIONS")
    lines.append("")

    if report.repeated_tasks:
        lines.append("Steps retried 2+ times (fix root cause to avoid wasted spend):\n")
        for t in report.repeated_tasks[:10]:
            lines.append(f"  ● {t['step']}  [{t['agent']}]")
            lines.append(
                f"    run:{t['run_id']}  attempts:{t['attempts']}  cost:${t['cost_usd']:.4f}"
            )
            if t["feature"]:
                lines.append(f"    context: {t['feature']}")
            lines.append("")
    else:
        lines.append("  No step retries detected.\n")

    if report.duplicate_role_steps:
        lines.append("Agent roles dispatched to 2+ parallel steps (cache or consolidate):\n")
        for d in report.duplicate_role_steps[:5]:
            steps_display = ", ".join(d["steps"][:4])
            if len(d["steps"]) > 4:
                steps_display += f" … (+{len(d['steps']) - 4} more)"
            lines.append(f"  ● role:{d['role']}  ×{d['count']} steps  run:{d['run_id']}")
            lines.append(f"    steps: {steps_display}\n")

    # ── INDEXABLE DATA ─────────────────────────────────────────────────
    rule("INDEXABLE DATA  (AICoder pre-index candidates)")
    lines.append("")

    if report.cross_run_step_freq:
        lines.append(
            "Steps repeated across multiple runs — pre-index their context with AICoder:\n"
        )
        for s in report.cross_run_step_freq[:10]:
            lines.append(f"  ● {s['step']}  [{s['agent']}]")
            lines.append(
                f"    appears in {s['run_count']} run(s)  cumulative cost:${s['total_cost_usd']:.4f}\n"
            )
    else:
        lines.append(
            "  No cross-run patterns detected.\n"
            "  (Run with --last N covering multiple runs to enable this analysis.)\n"
        )

    # ── COST / TOKEN ANALYSIS ──────────────────────────────────────────
    rule("COST / TOKEN ANALYSIS")
    lines.append("")

    if report.top_cost_steps:
        lines.append("Top steps by cost (USD):\n")
        for s in report.top_cost_steps:
            lines.append(f"  ${s['total_cost_usd']:>8.4f}  {s['step']}  [{s['agent']}]")
        lines.append("")

    if report.top_token_agents:
        lines.append("Top agents by input tokens:\n")
        for s in report.top_token_agents:
            lines.append(f"  {s['total_input_tokens']:>12,}  {s['agent']}")
        lines.append("")

    if report.estimated_retry_cost_waste > 0 or report.estimated_retry_token_waste > 0:
        lines.append(
            f"Estimated retry waste: ${report.estimated_retry_cost_waste:.4f}"
            + (
                f"  ({report.estimated_retry_token_waste:,} input tokens)"
                if report.estimated_retry_token_waste
                else ""
            )
        )
        lines.append("")

    # ── SUGGESTIONS ────────────────────────────────────────────────────
    rule("TOP RECOMMENDATIONS")
    lines.append("")
    suggestions = _build_suggestions(report)
    if suggestions:
        for i, s in enumerate(suggestions, 1):
            lines.append(f"  {i}. {s}")
    else:
        lines.append("  Not enough data for actionable suggestions.")
    lines.append("")
    lines.append("=" * W)

    return "\n".join(lines)


def _build_suggestions(report: AnalysisReport) -> list[str]:
    suggestions: list[str] = []

    if report.repeated_tasks:
        worst = report.repeated_tasks[0]
        suggestions.append(
            f"Fix retry root cause for '{worst['step']}' "
            f"({worst['attempts']}x retries, ${worst['cost_usd']:.4f} wasted)"
        )

    if report.cross_run_step_freq:
        top = report.cross_run_step_freq[0]
        suggestions.append(
            f"Pre-index context for '{top['step']}' with AICoder "
            f"(runs in {top['run_count']} pipelines, ${top['total_cost_usd']:.4f} total)"
        )

    if report.top_cost_steps:
        top = report.top_cost_steps[0]
        suggestions.append(
            f"Optimize '{top['step']}' — highest single-step cost ${top['total_cost_usd']:.4f}"
        )

    if report.duplicate_role_steps:
        top = report.duplicate_role_steps[0]
        suggestions.append(
            f"Role '{top['role']}' runs {top['count']}x in one pipeline — "
            "consider shared context or summarization"
        )

    return suggestions[:3]


def format_report_json(report: AnalysisReport) -> str:
    """Return a JSON-serializable dict as a formatted string."""
    data = {
        "run_ids": report.run_ids,
        "repetitive_actions": {
            "retried_steps": report.repeated_tasks,
            "duplicate_role_steps": report.duplicate_role_steps,
        },
        "indexable_data": {
            "cross_run_step_frequency": report.cross_run_step_freq,
        },
        "cost_analysis": {
            "top_cost_steps": report.top_cost_steps,
            "top_token_agents": report.top_token_agents,
            "estimated_retry_cost_waste_usd": round(report.estimated_retry_cost_waste, 6),
            "estimated_retry_token_waste": report.estimated_retry_token_waste,
        },
        "recommendations": _build_suggestions(report),
    }
    return json.dumps(data, indent=2)
