from pathlib import Path
from orchestrator.config import load_config
from orchestrator.workspace_manager import WorkspaceManager
import json

config = load_config(Path("NOTHING"))
workspace_root = Path(config.workspace_root or "workspace").resolve()
project_name = config.project_name or Path.cwd().name
manager = WorkspaceManager(workspace_root, project_name)

runs = manager.list_runs()
print(f"Found {len(runs)} runs:")
for r in runs:
    print(r.get("run_id"))
