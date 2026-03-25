"""CLI entry point for containerized orchestration runs.

Each invocation of ``orchestrate-container`` spins up an ephemeral, isolated
Docker container on the local Docker host and runs the full orchestration
pipeline inside it.  Artifacts are written to ``workspace/runs/<run_id>/`` on
the host via a bind-mount and are available immediately after the container exits.

Usage
-----
    orchestrate-container "Build an auth system" --env-file ~/.orchestrator.env
    orchestrate-container "..." --dry-run            # preview docker command
    orchestrate-container "..." --no-network-isolation  # skip orchestrator-net

See ``orchestrate-container --help`` for the full flag reference.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from pathlib import Path

import structlog

from orchestrator.config import load_config
from orchestrator.container_runner import ArtifactBridgeVolume, ContainerRuntime

log = structlog.get_logger(__name__)


def _configure_structlog(json_logs: bool = False) -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer()
    )
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


def _generate_run_id() -> str:
    """Generate a 12-character lowercase hex run identifier."""
    return secrets.token_hex(6)  # 6 bytes → 12 hex chars


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestrate-container",
        description=(
            "Run the AI SDLC Orchestrator inside an ephemeral, isolated Docker "
            "container.  Each invocation spins up a fresh container, executes the "
            "orchestration pipeline, streams logs to your terminal, and removes the "
            "container on exit.  Artifacts are written to workspace/runs/<run_id>/ "
            "via a bind-mount and persist on the host after the container exits."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--feature-request",
        required=True,
        metavar="TEXT",
        dest="feature_request",
        help="The orchestration prompt (e.g. 'Build an authentication system')",
    )
    parser.add_argument(
        "--container-image",
        metavar="IMAGE",
        default=None,
        help="Override the Docker image (default: config.container.image)",
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        type=Path,
        default=None,
        help="Path to a .env file injected into the container via --env-file "
        "(must contain ANTHROPIC_API_KEY=sk-...)",
    )
    parser.add_argument(
        "--workspace",
        metavar="DIR",
        type=Path,
        default=Path("./workspace"),
        help="Host workspace root directory (default: ./workspace)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        type=Path,
        default=None,
        help="Path to the orchestrator config YAML file "
        "(default: config/default.yaml)",
    )
    parser.add_argument(
        "--run-id",
        metavar="RUN_ID",
        default=None,
        help="Explicit 12-character hex run ID (auto-generated if not provided)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the complete docker run command and exit without launching a container",
    )
    parser.add_argument(
        "--no-network-isolation",
        action="store_true",
        default=False,
        help="Use the default 'bridge' network instead of 'orchestrator-net'. "
        "Disables egress filtering — use only for development/debugging.",
    )
    parser.add_argument(
        "--log-format",
        choices=["console", "json"],
        default="console",
        help="Log output format (default: console)",
    )

    return parser


def main() -> None:
    """Entry point for the ``orchestrate-container`` CLI command."""
    import shlex

    parser = _build_parser()
    args = parser.parse_args()

    _configure_structlog(json_logs=(args.log_format == "json"))

    # ── Load config ───────────────────────────────────────────────────────────
    config = load_config(args.config)

    # ── Apply CLI overrides to container config ───────────────────────────────
    if args.container_image:
        config.container.image = args.container_image

    if args.env_file:
        if not args.env_file.exists():
            print(
                f"Error: --env-file path does not exist: {args.env_file}",
                file=sys.stderr,
            )
            sys.exit(1)
        config.container.env_file = args.env_file

    if args.no_network_isolation:
        config.container.network_mode = "bridge"
        log.info(
            "network_isolation_disabled",
            network_mode="bridge",
            message=(
                "--no-network-isolation: using 'bridge' network. "
                "Egress is NOT filtered."
            ),
        )

    # ── Resolve workspace root ────────────────────────────────────────────────
    workspace_root = args.workspace.resolve()

    # ── Resolve run ID ────────────────────────────────────────────────────────
    run_id = args.run_id or _generate_run_id()

    # Validate explicit run IDs supplied by the caller
    import re

    if not re.match(r"^[0-9a-f]{12}$", run_id):
        print(
            f"Error: --run-id must be a 12-character lowercase hex string; "
            f"got {run_id!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Docker availability check ─────────────────────────────────────────────
    try:
        ContainerRuntime.is_docker_available()
    except RuntimeError as exc:
        print(f"Error: Docker is unavailable — {exc}", file=sys.stderr)
        print(
            "\nTo fix this:\n"
            "  1. Install Docker Engine >= 20.10 (https://docs.docker.com/get-docker/)\n"
            "  2. Start the Docker daemon (e.g. 'open -a Docker' on macOS)\n"
            "  3. Verify with: docker version",
            file=sys.stderr,
        )
        sys.exit(1)

    runtime = ContainerRuntime()

    # ── Prepare run directory (required before build_run_args for mount path) ─
    host_run_dir = ArtifactBridgeVolume.prepare_host_run_dir(workspace_root, run_id)

    # ── Assemble run arguments ────────────────────────────────────────────────
    run_args = runtime.build_run_args(
        run_id=run_id,
        feature_request=args.feature_request,
        config=config.container,
        host_run_dir=host_run_dir,
    )

    # ── Dry-run: print command and exit ───────────────────────────────────────
    if args.dry_run:
        print(shlex.join(run_args))
        sys.exit(0)

    # ── Execute the container ─────────────────────────────────────────────────
    log.info(
        "orchestrate_container_starting",
        run_id=run_id,
        image=config.container.image,
        network_mode=config.container.network_mode,
        workspace=str(workspace_root),
    )

    try:
        returncode = asyncio.run(
            runtime.run(
                run_id=run_id,
                feature_request=args.feature_request,
                config=config.container,
                workspace_root=workspace_root,
                host_run_dir=host_run_dir,  # already prepared above
            )
        )
    except KeyboardInterrupt:
        print(
            "\nInterrupted.  The container may still be running; "
            f"stop it with: docker stop orchestrator-{run_id}",
            file=sys.stderr,
        )
        sys.exit(130)

    sys.exit(returncode)
