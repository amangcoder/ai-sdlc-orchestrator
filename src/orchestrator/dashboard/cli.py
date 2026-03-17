"""CLI entry point for the orchestrator dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="orchestrate-dashboard",
        description="Launch the Orchestrator monitoring dashboard",
    )
    parser.add_argument(
        "--workspace", type=Path, default=Path("workspace"),
        help="Workspace directory containing logs, state, and timeline files",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Port to serve the dashboard on (default: 8080)",
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("Dashboard requires uvicorn. Install with: pip install ai-sdlc-orchestrator[dashboard]")
        raise SystemExit(1)

    from orchestrator.dashboard.app import create_app

    workspace = args.workspace.resolve()
    if not workspace.exists():
        print(f"Workspace directory not found: {workspace}")
        raise SystemExit(1)

    app = create_app(workspace)
    print(f"Dashboard: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
