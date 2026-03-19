"""CLI entry point for the Orchestrator Mobile API server.

Usage:
    orchestrator-mobile-api [--port PORT] [--config CONFIG] [--workspace WORKSPACE]

The server binds only to the Tailscale network interface (100.64.0.0/10) when
available, falling back to 0.0.0.0. Always runs with workers=1 because
RunTracker._tasks is in-process asyncio state that cannot be shared across workers.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Mobile API server entry point."""
    parser = argparse.ArgumentParser(
        description="Orchestrator Mobile API server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8090,
        help="Port to listen on (default: 8090)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to orchestrator config YAML (default: config/default.yaml)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspace"),
        metavar="PATH",
        help="Path to workspace directory (default: workspace)",
    )
    args = parser.parse_args()

    # Detect Tailscale bind address
    from orchestrator.mobile_api.tailscale import detect_tailscale_ip

    tailscale_ip = detect_tailscale_ip()
    if tailscale_ip:
        bind_host = tailscale_ip
        logger.info("Bound to Tailscale interface: %s:%d", bind_host, args.port)
    else:
        bind_host = "0.0.0.0"
        logger.warning(
            "Tailscale interface not found, falling back to 0.0.0.0:%d", args.port
        )

    # Create the FastAPI app
    from orchestrator.mobile_api.app import create_mobile_app

    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    # When no --config is provided, look for config/default.yaml relative to CWD
    # rather than relying on the installed package path (which may point to a
    # different copy of the repo).
    config_path = args.config
    if config_path is None:
        cwd_config = Path.cwd() / "config" / "default.yaml"
        if cwd_config.exists():
            config_path = cwd_config
            logger.info("Using config from CWD: %s", config_path)

    app = create_mobile_app(workspace_dir=workspace, config_path=config_path)

    # Launch uvicorn — workers=1 is hardcoded (RunTracker uses in-process asyncio state)
    import uvicorn

    uvicorn.run(app, host=bind_host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
