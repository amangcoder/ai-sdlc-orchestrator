"""Execution timeline recording and export (JSON + HTML Gantt chart)."""

from __future__ import annotations

import json
import html as html_module
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.db.repositories.timeline import TimelineRepository


@dataclass
class TimelineEntry:
    task_id: str
    agent: str
    step: str
    start_time: str  # ISO 8601
    end_time: str | None = None
    duration_seconds: float = 0.0
    status: str = "running"
    cost_usd: float = 0.0
    dependencies: list[str] = field(default_factory=list)
    model_tier: str = ""
    error_code: str | None = None


class TimelineRecorder:
    """Records execution timeline entries and exports to JSON/HTML."""

    def __init__(
        self,
        run_id: str = "",
        timeline_repo: "TimelineRepository | None" = None,
    ) -> None:
        self._entries: dict[str, TimelineEntry] = {}
        self._run_start: str | None = None
        self._run_end: str | None = None
        self._run_id = run_id
        self._timeline_repo = timeline_repo

    def set_run_start(self) -> None:
        self._run_start = datetime.now(timezone.utc).isoformat()

    def set_run_end(self) -> None:
        self._run_end = datetime.now(timezone.utc).isoformat()

    def record_start(
        self,
        task_id: str,
        agent: str,
        step: str,
        dependencies: list[str] | None = None,
        model_tier: str = "",
    ) -> None:
        self._entries[task_id] = TimelineEntry(
            task_id=task_id,
            agent=agent,
            step=step,
            start_time=datetime.now(timezone.utc).isoformat(),
            dependencies=dependencies or [],
            model_tier=model_tier,
        )

    def record_end(
        self,
        task_id: str,
        status: str,
        cost_usd: float = 0.0,
        error_code: str | None = None,
    ) -> None:
        entry = self._entries.get(task_id)
        if not entry:
            return
        entry.end_time = datetime.now(timezone.utc).isoformat()
        entry.status = status
        entry.cost_usd = cost_usd
        entry.error_code = error_code
        start = datetime.fromisoformat(entry.start_time)
        end = datetime.fromisoformat(entry.end_time)
        entry.duration_seconds = round((end - start).total_seconds(), 2)

    def get_entries(self) -> list[TimelineEntry]:
        return list(self._entries.values())

    def export_json(self, path: Path) -> None:
        entries = [asdict(e) for e in self._entries.values()]
        data = {
            "run_start": self._run_start,
            "run_end": self._run_end,
            "entries": entries,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

        # DB sync (best-effort)
        if self._timeline_repo is not None and self._run_id:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                for entry in entries:
                    coro = self._timeline_repo.upsert_entry(self._run_id, entry)
                    if loop.is_running():
                        asyncio.ensure_future(coro)
                    else:
                        loop.run_until_complete(coro)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).debug("DB timeline upsert failed: %s", exc)

    def export_html(self, path: Path) -> None:
        entries = sorted(self._entries.values(), key=lambda e: e.start_time)
        if not entries:
            path.write_text("<html><body><p>No timeline data.</p></body></html>")
            return

        # Build timeline data for the Gantt chart
        timeline_json = json.dumps([asdict(e) for e in entries], default=str)

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Orchestrator Execution Timeline</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }}
  h1 {{ margin-bottom: 20px; color: #58a6ff; }}
  .gantt {{ width: 100%; overflow-x: auto; }}
  .row {{ display: flex; align-items: center; margin: 2px 0; height: 28px; }}
  .label {{ width: 200px; min-width: 200px; font-size: 12px; padding-right: 10px; text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .bar-container {{ flex: 1; position: relative; height: 100%; }}
  .bar {{ position: absolute; height: 22px; top: 3px; border-radius: 3px; font-size: 11px; line-height: 22px; padding: 0 6px; white-space: nowrap; overflow: hidden; color: #fff; cursor: pointer; }}
  .bar:hover {{ opacity: 0.8; }}
  .bar.completed {{ background: #238636; }}
  .bar.failed {{ background: #da3633; }}
  .bar.running {{ background: #d29922; }}
  .bar.blocked {{ background: #6e7681; }}
  .legend {{ margin-top: 20px; display: flex; gap: 20px; }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; font-size: 13px; }}
  .legend-swatch {{ width: 14px; height: 14px; border-radius: 2px; }}
  .tooltip {{ display: none; position: absolute; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 10px; font-size: 12px; z-index: 10; min-width: 220px; }}
  .tooltip.show {{ display: block; }}
  .tooltip dt {{ font-weight: 600; color: #58a6ff; }}
  .tooltip dd {{ margin: 0 0 6px 0; }}
  .summary {{ margin-top: 20px; font-size: 13px; color: #8b949e; }}
</style>
</head>
<body>
<h1>Execution Timeline</h1>
<div class="summary" id="summary"></div>
<div class="gantt" id="gantt"></div>
<div class="legend">
  <div class="legend-item"><div class="legend-swatch" style="background:#238636"></div> Completed</div>
  <div class="legend-item"><div class="legend-swatch" style="background:#da3633"></div> Failed</div>
  <div class="legend-item"><div class="legend-swatch" style="background:#d29922"></div> Running</div>
  <div class="legend-item"><div class="legend-swatch" style="background:#6e7681"></div> Blocked</div>
</div>
<div class="tooltip" id="tooltip"></div>
<script>
const entries = {timeline_json};
if (entries.length === 0) {{
  document.getElementById('gantt').innerHTML = '<p>No entries.</p>';
}} else {{
  const starts = entries.map(e => new Date(e.start_time).getTime());
  const ends = entries.map(e => e.end_time ? new Date(e.end_time).getTime() : Date.now());
  const minT = Math.min(...starts);
  const maxT = Math.max(...ends);
  const range = maxT - minT || 1;
  const totalCost = entries.reduce((s, e) => s + (e.cost_usd || 0), 0);
  const totalDuration = ((maxT - minT) / 1000).toFixed(1);

  document.getElementById('summary').textContent =
    `${{entries.length}} tasks | ${{totalDuration}}s total | $${{totalCost.toFixed(4)}} cost`;

  const gantt = document.getElementById('gantt');
  const tooltip = document.getElementById('tooltip');

  entries.forEach(e => {{
    const row = document.createElement('div');
    row.className = 'row';
    const label = document.createElement('div');
    label.className = 'label';
    label.textContent = e.task_id;
    label.title = `${{e.agent}} (${{e.step}})`;
    row.appendChild(label);

    const barContainer = document.createElement('div');
    barContainer.className = 'bar-container';
    const bar = document.createElement('div');
    const s = new Date(e.start_time).getTime();
    const en = e.end_time ? new Date(e.end_time).getTime() : Date.now();
    const left = ((s - minT) / range) * 100;
    const width = Math.max(0.5, ((en - s) / range) * 100);
    bar.className = `bar ${{e.status}}`;
    bar.style.left = left + '%';
    bar.style.width = width + '%';
    bar.textContent = `${{e.duration_seconds}}s`;

    bar.addEventListener('mouseenter', (ev) => {{
      tooltip.innerHTML = `<dl>
        <dt>Task</dt><dd>${{e.task_id}}</dd>
        <dt>Agent</dt><dd>${{e.agent}}</dd>
        <dt>Step</dt><dd>${{e.step}}</dd>
        <dt>Model</dt><dd>${{e.model_tier || 'N/A'}}</dd>
        <dt>Duration</dt><dd>${{e.duration_seconds}}s</dd>
        <dt>Cost</dt><dd>$${{(e.cost_usd || 0).toFixed(4)}}</dd>
        <dt>Status</dt><dd>${{e.status}}</dd>
        ${{e.error_code ? `<dt>Error</dt><dd>${{e.error_code}}</dd>` : ''}}
        ${{e.dependencies.length ? `<dt>Deps</dt><dd>${{e.dependencies.join(', ')}}</dd>` : ''}}
      </dl>`;
      tooltip.classList.add('show');
      tooltip.style.left = (ev.pageX + 10) + 'px';
      tooltip.style.top = (ev.pageY + 10) + 'px';
    }});
    bar.addEventListener('mouseleave', () => tooltip.classList.remove('show'));

    barContainer.appendChild(bar);
    row.appendChild(barContainer);
    gantt.appendChild(row);
  }});
}}
</script>
</body>
</html>"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_content)
