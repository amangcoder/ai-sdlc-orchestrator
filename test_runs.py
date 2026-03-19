import asyncio
from fastapi import FastAPI
from pathlib import Path
from orchestrator.workspace_manager import WorkspaceManager
from orchestrator.config import load_config

# Initialize WorkspaceManager
config = load_config(Path("orchestrator.yaml"))
workspace_root = Path(config.workspace_root or "workspace").resolve()
project_name = config.project_name or Path.cwd().name
manager = WorkspaceManager(workspace_root, project_name)

print("Runs from manager.list_runs():")
for r in manager.list_runs():
    print(r.get("run_id"))

run_id = "ff65cbd843f4"
print(f"\nfind_run_state('{run_id}'):", manager.find_run_state(run_id))
