"""CLI entry point for the AI SDLC Orchestrator monitoring stack.

Provides a thin wrapper around ``docker compose`` for the monitoring
infrastructure defined in ``infra/docker/docker-compose.monitoring.yml``.

Usage::

    orchestrate-monitoring start    # Start all monitoring services (daemon)
    orchestrate-monitoring stop     # Stop all services (keep volumes)
    orchestrate-monitoring status   # Print service status + health checks
    orchestrate-monitoring reset    # Stop + delete all data volumes (fresh start)
    orchestrate-monitoring logs     # Tail live logs from all services

The underlying ``infra/scripts/start-monitoring.sh`` can also be used
directly from a shell for the same functionality.

Entry point registered in pyproject.toml::

    [project.scripts]
    orchestrate-monitoring = "orchestrator.monitoring.cli:main"
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path


# Shell metacharacters that must not appear in WORKSPACE_ROOT to prevent
# command injection.  We intentionally block a broad set so that even
# obscure injection vectors (e.g. process substitution, globbing) are caught.
_SHELL_METACHAR_RE = re.compile(r"[;|&$`<>()\[\]{}'\"\\!?*\n\r\t]")


def _validate_workspace_root(path: str) -> str:
    """Validate that *path* contains no shell metacharacters.

    Args:
        path: The value of the WORKSPACE_ROOT environment variable.

    Returns:
        The validated path (unchanged).

    Raises:
        SystemExit(1): If the path contains shell metacharacters.
    """
    if _SHELL_METACHAR_RE.search(path):
        print(
            f"Error: WORKSPACE_ROOT contains shell metacharacters: {path!r}\n"
            "WORKSPACE_ROOT must be a plain filesystem path with no shell special characters.",
            file=sys.stderr,
        )
        sys.exit(1)
    return path


def _find_compose_file() -> Path:
    """Locate docker-compose.monitoring.yml relative to this module or CWD."""
    # Walk up from this file's directory to find the project root
    # src/orchestrator/monitoring/cli.py → walk up 3 levels to project root
    here = Path(__file__).resolve().parent
    for candidate in [here, here.parent, here.parent.parent, here.parent.parent.parent]:
        compose = candidate / "infra" / "docker" / "docker-compose.monitoring.yml"
        if compose.exists():
            return compose

    # Fallback: relative to CWD
    compose = Path.cwd() / "infra" / "docker" / "docker-compose.monitoring.yml"
    if compose.exists():
        return compose

    raise FileNotFoundError(
        "Could not locate infra/docker/docker-compose.monitoring.yml. "
        "Run this command from the project root, or ensure the file exists."
    )


def _run_compose(*args: str, compose_file: Path, env: dict | None = None) -> int:
    """Run ``docker compose -f <file> <args>`` and return the exit code.

    Always uses ``shell=False`` (no shell expansion) to prevent injection.
    """
    cmd = ["docker", "compose", "-f", str(compose_file), *args]
    run_env = {**os.environ, **(env or {})}
    result = subprocess.run(cmd, env=run_env)  # shell=False is the default
    return result.returncode


def _health_check(url: str, timeout: float = 3.0) -> bool:
    """Return True if the URL responds with an HTTP 2xx/3xx status code."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 400
    except (urllib.error.URLError, OSError):
        return False


def _check_container_running(container_name: str) -> bool:
    """Return True if the named Docker container exists and is running.

    Uses ``docker inspect`` with ``shell=False`` to avoid injection.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container_name],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def cmd_start(compose_file: Path) -> int:
    """Start all monitoring services detached."""
    workspace_root = os.environ.get("WORKSPACE_ROOT", str(Path.cwd() / "workspace"))
    _validate_workspace_root(workspace_root)

    print("Starting orchestrator monitoring stack...")
    rc = _run_compose(
        "up", "-d",
        compose_file=compose_file,
        env={"WORKSPACE_ROOT": workspace_root},
    )
    if rc == 0:
        print()
        print("Monitoring stack is running:")
        print("  Prometheus  → http://localhost:9091")
        print("  Grafana     → http://localhost:3000  (admin / admin)")
        print("  Jaeger      → http://localhost:16686")
        print("  Loki        → http://localhost:3100")
        print("  Promtail    → (log shipper, no HTTP UI)")
        print()
        print("Enable metrics in your orchestrator by setting:")
        print("  monitoring.metrics_enabled: true   in config/default.yaml")
        print("  monitoring.tracing_enabled: true   for distributed tracing")
        print("  monitoring.loki_enabled: true      for direct log shipping")
    return rc


def cmd_stop(compose_file: Path) -> int:
    """Stop all services; data volumes are preserved."""
    print("Stopping monitoring stack...")
    rc = _run_compose("down", compose_file=compose_file)
    if rc == 0:
        print("Monitoring stack stopped (volumes retained).")
    return rc


def cmd_reset(compose_file: Path) -> int:
    """Stop all services and delete all persistent data volumes."""
    print("Stopping monitoring stack and removing all data volumes...")
    rc = _run_compose("down", "-v", compose_file=compose_file)
    if rc == 0:
        print("Monitoring stack reset. All historical data removed.")
    return rc


def cmd_status(compose_file: Path) -> int:
    """Show container status and run HTTP health checks on each service.

    Checks all 5 monitoring services:
      1. Prometheus  — HTTP health endpoint
      2. Grafana     — HTTP health endpoint
      3. Jaeger      — HTTP reachability check
      4. Loki        — HTTP ready endpoint
      5. Promtail    — Docker container running state (no HTTP endpoint)
    """
    print("Monitoring stack container status:")
    print()
    rc = _run_compose(
        "ps", "--format", "table {{.Name}}\t{{.Status}}\t{{.Ports}}",
        compose_file=compose_file,
    )
    print()
    print("Service health:")

    # Services with HTTP health endpoints
    http_endpoints = [
        ("Prometheus", "http://localhost:9091/-/healthy"),
        ("Grafana",    "http://localhost:3000/api/health"),
        ("Jaeger",     "http://localhost:16686/"),
        ("Loki",       "http://localhost:3100/ready"),
    ]
    all_healthy = True
    for name, url in http_endpoints:
        if _health_check(url):
            print(f"  {name:12s}: \033[32mhealthy\033[0m")
        else:
            print(f"  {name:12s}: \033[31munreachable\033[0m")
            all_healthy = False

    # Promtail has no HTTP endpoint — check Docker container state directly
    promtail_running = _check_container_running("orchestrator-promtail")
    if promtail_running:
        print(f"  {'Promtail':12s}: \033[32mrunning\033[0m")
    else:
        print(f"  {'Promtail':12s}: \033[31mnot running\033[0m")
        all_healthy = False

    if not all_healthy:
        print()
        print("One or more services are unreachable or not running.")
        print("Start the stack with: orchestrate-monitoring start")

    return rc


def cmd_logs(compose_file: Path, follow: bool = True) -> int:
    """Tail live logs from all monitoring services."""
    args = ["logs"]
    if follow:
        args.append("-f")
    return _run_compose(*args, compose_file=compose_file)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``orchestrate-monitoring`` CLI command."""
    parser = argparse.ArgumentParser(
        prog="orchestrate-monitoring",
        description=(
            "Manage the AI SDLC Orchestrator monitoring stack "
            "(Prometheus + Grafana + Jaeger + Loki + Promtail)."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("start", help="Start all monitoring services in the background")
    sub.add_parser("stop", help="Stop all services (data volumes are preserved)")
    sub.add_parser("reset", help="Stop all services and DELETE all data volumes")
    sub.add_parser("status", help="Show container status and service health checks")
    logs_p = sub.add_parser("logs", help="Tail live logs from all monitoring services")
    logs_p.add_argument(
        "--no-follow",
        action="store_true",
        help="Print existing logs and exit (don't follow)",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        compose_file = _find_compose_file()
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    dispatch = {
        "start":  lambda: cmd_start(compose_file),
        "stop":   lambda: cmd_stop(compose_file),
        "reset":  lambda: cmd_reset(compose_file),
        "status": lambda: cmd_status(compose_file),
        "logs":   lambda: cmd_logs(compose_file, follow=not getattr(args, "no_follow", False)),
    }

    rc = dispatch[args.command]()
    sys.exit(rc)


if __name__ == "__main__":
    main()
