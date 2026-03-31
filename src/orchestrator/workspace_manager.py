"""Centralized workspace management and path resolution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from orchestrator.db.repositories.runs import RunRepository


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

    def __init__(
        self,
        workspace_root: Path,
        project_name: str,
        project_root: Path | None = None,
        run_repo: "RunRepository | None" = None,
    ):
        self.workspace_root = workspace_root.resolve()
        self.project_name = project_name
        # project_root is the actual project directory (cwd when orchestrate runs).
        # Needed because the engine's backward-compat path writes state files
        # to the workspace_dir (which may be a subdir of project_root), but
        # older runs or different configs may have written to project_root directly.
        self.project_root = project_root.resolve() if project_root is not None else None
        # When set, DB is queried first for run discovery (hosted mode)
        self._run_repo: RunRepository | None = run_repo

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

        Checks (in order):
        1. project/runs/run_id/state.json (new style)
        2. project_workspace/state-run_id.json (backward-compat / flat layout)
        3. workspace_root/state-run_id.json (flat root)
        4. project_root/state-run_id.json (legacy — older runs wrote here)
        """
        # 1. New style
        new_style = self.run_workspace(run_id) / "state.json"
        if new_style.exists():
            return new_style

        # 2. Flat layout under project_workspace
        flat_style = self.project_workspace / f"state-{run_id}.json"
        if flat_style.exists():
            return flat_style

        # 3. Flat layout under workspace_root (different from project_workspace)
        if self.workspace_root != self.project_workspace:
            root_flat = self.workspace_root / f"state-{run_id}.json"
            if root_flat.exists():
                return root_flat

        # 4. Project root (legacy: older config or different workspace_dir)
        if self.project_root is not None and self.project_root != self.workspace_root:
            proj_flat = self.project_root / f"state-{run_id}.json"
            if proj_flat.exists():
                return proj_flat

        return None

    def list_runs(self) -> list[dict[str, Any]]:
        """Return all runs, sorted newest-first.

        When a ``RunRepository`` is configured, queries the DB (authoritative).
        Falls back to filesystem scan for legacy/file-only deployments.
        """
        if self._run_repo is not None:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_running():
                    return loop.run_until_complete(
                        self._run_repo.list(
                            limit=200,
                            project_name=self.project_name or None,
                        )
                    )
            except Exception:
                pass  # fall through to filesystem scan

        import json
        runs = []
        seen_ids: set[str] = set()

        def _ingest(state_file: Path) -> None:
            try:
                data = json.loads(state_file.read_text())
                run_id = data.get("run_id")
                if run_id and run_id not in seen_ids:
                    seen_ids.add(run_id)
                    data["_mtime"] = state_file.stat().st_mtime
                    runs.append(data)
            except (json.JSONDecodeError, OSError):
                pass

        # 1. New style: project/runs/*/state.json
        runs_root = self.project_workspace / "runs"
        if runs_root.exists():
            for run_dir in runs_root.iterdir():
                if run_dir.is_dir():
                    _ingest(run_dir / "state.json")

        # 2. Old style: project_workspace/state-*.json
        if self.project_workspace.exists():
            for state_file in self.project_workspace.glob("state-*.json"):
                _ingest(state_file)

        # 3. Flat root: workspace_root/state-*.json and workspace_root/state.json
        #    (engine writes here when workspace_root is not explicitly configured)
        if self.workspace_root != self.project_workspace and self.workspace_root.exists():
            for state_file in self.workspace_root.glob("state-*.json"):
                _ingest(state_file)
            _ingest(self.workspace_root / "state.json")

        # 4. Project root (legacy — older runs or different workspace_dir)
        if self.project_root is not None and self.project_root != self.workspace_root and self.project_root.exists():
            for state_file in self.project_root.glob("state-*.json"):
                _ingest(state_file)
            _ingest(self.project_root / "state.json")

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
