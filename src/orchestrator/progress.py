"""Real-time progress tracking with ETA calculation."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from orchestrator.models import (
    PhaseStatus,
    RunState,
    TaskStatus,
    WorkflowDefinition,
)

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Tracks and displays workflow progress with ETA estimation."""

    def __init__(self, workflow: WorkflowDefinition, state: RunState) -> None:
        self.workflow = workflow
        self.state = state
        self.console = Console()
        self._start_time = time.time()
        self._task_completions: list[float] = []
        self._tasks_since_last_show = 0

    def show(self, event: str = "update") -> None:
        """Display the progress report if appropriate for the event type."""
        if not self._should_show(event):
            return
        self._tasks_since_last_show = 0
        report = self.render()
        self.console.print(report)

    def on_task_complete(self) -> None:
        """Track a task completion for ETA calculation."""
        self._task_completions.append(time.time())
        self._tasks_since_last_show += 1
        if self._tasks_since_last_show >= 3:
            self.show("task_batch")

    def render(self) -> Panel:
        """Produce the formatted progress report."""
        total_steps = len(self.workflow.steps)
        completed_steps = len(self.state.completed_steps)
        pct = int((completed_steps / total_steps) * 100) if total_steps > 0 else 0

        lines: list[str] = []

        # Header
        lines.append(f"WORKFLOW: {self.workflow.name}")
        lines.append(f"TASK: {self.state.feature_request[:60]}")
        lines.append("")

        # Workflow progress bar
        bar = self._progress_bar(pct)
        lines.append(f"WORKFLOW PROGRESS")
        lines.append(f"{bar}  {pct}%  ({completed_steps}/{total_steps} steps)")
        lines.append("")

        # Step list
        for step in self.workflow.steps:
            phase_key = step.name.lower().replace(" ", "_")
            phase_state = self.state.phases.get(phase_key)

            if step.name in self.state.completed_steps:
                indicator = "[green]\u2714[/green]"
            elif self.state.current_step == step.name:
                indicator = "[yellow]\u2192[/yellow]"
            else:
                indicator = "[dim]\u00b7[/dim]"

            status_str = ""
            if phase_state:
                status_str = f" \u2014 {phase_state.status.value}"
            elif step.name in self.state.completed_steps:
                status_str = " \u2014 complete"
            else:
                status_str = " \u2014 pending"

            lines.append(f"  {indicator} {step.name:<30}{status_str}")

        # Current step detail
        if self.state.current_step:
            lines.append("")
            current_step_def = next(
                (s for s in self.workflow.steps if s.name == self.state.current_step), None
            )
            if current_step_def:
                from orchestrator.roles import get_role
                role_def = get_role(current_step_def.agent_role)
                lines.append(f"CURRENT STEP: {self.state.current_step}")
                lines.append(f"  Assigned to: {role_def.title}")

        # Task progress within current step
        step_tasks = [
            t for t in self.state.workflow_tasks
            if t.workflow_step == self.state.current_step
        ]
        if step_tasks:
            completed_tasks = sum(1 for t in step_tasks if t.status == TaskStatus.COMPLETED)
            total_tasks = len(step_tasks)
            task_pct = int((completed_tasks / total_tasks) * 100) if total_tasks > 0 else 0

            lines.append("")
            lines.append(f"TASK PROGRESS (this step)")
            bar = self._progress_bar(task_pct)
            lines.append(f"  {bar}  {task_pct}%  ({completed_tasks}/{total_tasks} tasks)")
            lines.append("")

            for task in step_tasks:
                if task.status == TaskStatus.COMPLETED:
                    t_indicator = "[green]\u2714[/green]"
                elif task.status in (TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED):
                    t_indicator = "[yellow]\u2192[/yellow]"
                elif task.status == TaskStatus.FAILED:
                    t_indicator = "[red]\u2718[/red]"
                elif task.status == TaskStatus.BLOCKED:
                    t_indicator = "[red]\u25a0[/red]"
                else:
                    t_indicator = "[dim]\u00b7[/dim]"
                lines.append(f"  {t_indicator} {task.task_id}  {task.description[:50]}")

        # ETA
        eta_step, eta_total = self.calculate_eta()
        if eta_total > 0:
            lines.append("")
            lines.append("ESTIMATED REMAINING")
            if eta_step > 0:
                lines.append(f"  This step: ~{self._format_duration(eta_step)}")
            lines.append(f"  Full workflow: ~{self._format_duration(eta_total)}")

        # Budget
        if self.state.total_cost_usd > 0:
            lines.append("")
            lines.append(f"BUDGET")
            lines.append(f"  Spent: ${self.state.total_cost_usd:.2f}")

        content = "\n".join(lines)
        return Panel(content, title="[bold]Orchestrator Progress[/bold]", border_style="blue")

    def calculate_eta(self) -> tuple[float, float]:
        """Calculate estimated time remaining.

        Returns (this_step_seconds, total_workflow_seconds).
        """
        if not self._task_completions:
            return 0.0, 0.0

        # Average time per task
        elapsed = self._task_completions[-1] - self._start_time
        completed_count = len(self._task_completions)
        avg_task_time = elapsed / completed_count if completed_count > 0 else 0

        # Remaining tasks in current step
        step_tasks = [
            t for t in self.state.workflow_tasks
            if t.workflow_step == self.state.current_step
        ]
        remaining_step = sum(1 for t in step_tasks if t.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED))
        eta_step = remaining_step * avg_task_time

        # Remaining steps
        total_steps = len(self.workflow.steps)
        completed_steps = len(self.state.completed_steps)
        remaining_steps = total_steps - completed_steps

        # Estimate: each remaining step takes avg_task_time * estimated_tasks_per_step
        tasks_per_step = completed_count / max(completed_steps, 1)
        eta_total = remaining_steps * tasks_per_step * avg_task_time

        return eta_step, eta_total

    def _should_show(self, event: str) -> bool:
        """Determine if progress should be shown for this event."""
        always_show = {"workflow_start", "workflow_complete", "step_complete",
                       "approval_gate", "failure", "retry"}
        if event in always_show:
            return True
        if event == "task_batch" and self._tasks_since_last_show >= 3:
            return True
        return False

    @staticmethod
    def _progress_bar(pct: int, width: int = 20) -> str:
        """Render a text progress bar."""
        filled = int(width * pct / 100)
        empty = width - filled
        return f"[{'█' * filled}{'░' * empty}]"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format seconds as a human-readable duration."""
        if seconds < 60:
            return f"{int(seconds)}s"
        minutes = int(seconds / 60)
        secs = int(seconds % 60)
        if minutes < 60:
            return f"{minutes}m {secs}s" if secs else f"{minutes}m"
        hours = int(minutes / 60)
        mins = minutes % 60
        return f"{hours}h {mins}m"
