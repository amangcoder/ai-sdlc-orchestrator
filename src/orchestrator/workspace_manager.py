"""Centralized workspace management and path resolution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional


class WorkspaceManager:
    """Manages project and per-run workspaces.

    Hierarchy:
        workspaces/                 <-- workspace_root (configurable)
          project_name/             <-- project_workspace
            runs/
              run_id/               <-- run_workspace
                state.json
                artifacts/
                logs/
                context.md
            latest -> runs/run_id   <-- convenience symlink
    """

    def __init__(self, workspace_root: Path, project_name: str):
        self.workspace_root = workspace_root.resolve()
        self.project_name = project_name

    @property
    def project_workspace(self) -> Path:
        """Return the directory for this project's workspaces: root/project_name/"""
        return self.workspace_root / self.project_name

    def run_workspace(self, run_id: str) -> Path:
        """Return the isolated directory for a specific run: project/runs/run_id/"""
        return self.project_workspace / "runs" / run_id

    def artifacts_dir(self, run_id: str) -> Path:
        """Return the artifacts directory for a specific run."""
        return self.run_workspace(run_id) / "artifacts"

    def logs_dir(self, run_id: str) -> Path:
        """Return the logs directory for a specific run."""
        return self.run_workspace(run_id) / "logs"

    def ensure_run_dirs(self, run_id: str) -> Path:
        """Create the run directory and its subdirectories (artifacts, logs)."""
        run_dir = self.run_workspace(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "artifacts").mkdir(exist_ok=True)
        (run_dir / "logs").mkdir(exist_ok=True)
        return run_dir

    def find_run_state(self, run_id: str) -> Optional[Path]:
        """Locate the state.json file for a run.

        Checks:
        1. project/runs/run_id/state.json (new style)
        2. project/state-run_id.json (backward-compat / flat layout)
        """
        # 1. New style
        new_style = self.run_workspace(run_id) / "state.json"
        if new_style.exists():
            return new_style

        # 2. Flat layout / backward compat (checks project root for state-*.json)
        flat_style = self.project_workspace / f"state-{run_id}.json"
        if flat_style.exists():
            return flat_style

        return None

    def list_runs(self) -> list[dict[str, Any]]:
        """Scan both new-style and old-style layouts for runs.

        Returns metadata sorted newest-first.
        """
        import json
        runs = []

        # 1. New style: project/runs/*/state.json
        runs_root = self.project_workspace / "runs"
        if runs_root.exists():
            for run_dir in runs_root.iterdir():
                if not run_dir.is_dir():
                    continue
                state_file = run_dir / "state.json"
                if state_file.exists():
                    try:
                        data = json.loads(state_file.read_text())
                        # Add filesystem metadata
                        data["_mtime"] = state_file.stat().st_mtime
                        runs.append(data)
                    except (json.JSONDecodeError, OSError):
                        continue

        # 2. Old style: project/state-*.json
        if self.project_workspace.exists():
            for state_file in self.project_workspace.glob("state-*.json"):
                try:
                    data = json.loads(state_file.read_text())
                    # Deduplicate if already found in new style
                    run_id = data.get("run_id")
                    if run_id and not any(r.get("run_id") == run_id for r in runs):
                        data["_mtime"] = state_file.stat().st_mtime
                        runs.append(data)
                except (json.JSONDecodeError, OSError):
                    continue

        # Sort newest-first by mtime
        runs.sort(key=lambda x: x.get("_mtime", 0), reverse=True)
        return runs

    def update_latest_symlink(self, run_id: str) -> None:
        """Create/update project/latest symlink pointing to the run dir.

        Note: symlinks are best-effort (may fail on Windows without privs).
        """
        latest = self.project_workspace / "latest"
        target = Path("runs") / run_id
        try:
            if latest.lexists():
                latest.unlink()
            latest.symlink_to(target, target_is_directory=True)
        except (OSError, AttributeError):
            pass
