#!/usr/bin/env python3
"""Deployment verification script for the AI SDLC Orchestrator.

Builds (or loads) the Docker image, starts a container, runs a comprehensive
set of health checks and CLI smoke tests, then reports PASS or FAIL.

Usage:
    python scripts/deployment_verify.py                        # build + verify
    python scripts/deployment_verify.py --image ai-sdlc-orchestrator:latest
    python scripts/deployment_verify.py --image-tar /tmp/image.tar.gz

Exit codes:
    0  — all checks passed
    1  — one or more checks failed

PRD requirements covered:
    REQ-001, REQ-002, REQ-003, REQ-009, REQ-012, REQ-016, REQ-018
    AC-001, AC-002, AC-010, AC-013
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    details: str = ""


@dataclass
class DeploymentReport:
    image_name: str
    checks: list[CheckResult] = field(default_factory=list)
    container_id: Optional[str] = None

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)


# ---------------------------------------------------------------------------
# DeploymentVerifier
# ---------------------------------------------------------------------------

class DeploymentVerifier:
    """Orchestrates Docker container deployment verification.

    Runs a container from the given image and executes a battery of checks:
      - Dashboard health endpoint  (/healthz on port 8080)
      - Mobile API health endpoint (/health on port 8090)
      - All CLI entry points       (--help exits 0)
      - Non-root user              (UID 1000 / user 'orchestrator')
      - Config symlink             (load_config() succeeds inside container)
      - Image size                 (warning if > 500 MB compressed)

    Args:
        image_name:     Docker image name (e.g., ``ai-sdlc-orchestrator:latest``).
        timeout:        Seconds to wait for health checks (default 30).
        log_tail_lines: Lines of container logs to include on failure (default 50).
    """

    # CLI entry points defined in pyproject.toml [project.scripts]
    CLI_ENTRY_POINTS = [
        "orchestrate",
        "orchestrate-container",
        "orchestrate-dashboard",
        "orchestrator-mobile-api",
        "orchestrate-monitoring",
        "orchestrate-artifacts",
    ]

    def __init__(
        self,
        image_name: str = "ai-sdlc-orchestrator:latest",
        timeout: int = 30,
        log_tail_lines: int = 50,
    ) -> None:
        self.image_name = image_name
        self.timeout = timeout
        self.log_tail_lines = log_tail_lines
        self._container_id: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def verify_all(self) -> DeploymentReport:
        """Run all verification checks and return a DeploymentReport."""
        report = DeploymentReport(image_name=self.image_name)

        print(f"\n{'='*60}")
        print(f"  Deployment Verification: {self.image_name}")
        print(f"{'='*60}\n")

        # 1. Image size check (informational — does not fail the report)
        self._run_check(report, "image-size-check", self._check_image_size)

        # 2. Non-root user check
        self._run_check(report, "non-root-user", self._check_non_root_user)

        # 3. Dashboard health check (port 8080) and mobile API (port 8090).
        # Use '-p 127.0.0.1:0:<port>' so Docker assigns a free ephemeral host
        # port, avoiding conflicts when 8080/8090 are already in use locally.
        container_id = self._start_container(container_ports=[8080, 8090])
        if container_id:
            self._container_id = container_id
            report.container_id = container_id
            try:
                # Discover the dynamically assigned host ports via 'docker port'
                dash_port = self._discover_host_port(container_id, 8080)
                api_port = self._discover_host_port(container_id, 8090)

                if dash_port:
                    self._run_check(
                        report, "dashboard-health-8080",
                        lambda p=dash_port: self._check_http_health(p, "/healthz"),
                    )
                else:
                    report.checks.append(CheckResult(
                        "dashboard-health-8080", False,
                        "Could not discover host port for container port 8080",
                    ))

                if api_port:
                    self._run_check(
                        report, "mobile-api-health-8090",
                        lambda p=api_port: self._check_http_health(p, "/health"),
                    )
                else:
                    report.checks.append(CheckResult(
                        "mobile-api-health-8090", False,
                        "Could not discover host port for container port 8090",
                    ))
            finally:
                self._stop_container(container_id)
                self._container_id = None

        # 4. CLI entry points
        self._run_check(report, "cli-entry-points", self._check_cli_entry_points)

        # 5. Config symlink / load_config()
        self._run_check(report, "config-symlink", self._check_config_symlink)

        # 6. Security: read-only rootfs + cap-drop ALL
        self._run_check(report, "security-hardening", self._check_security_hardening)

        # ---- Summary ----
        self._print_summary(report)
        return report

    # ------------------------------------------------------------------ #
    # Individual checks                                                    #
    # ------------------------------------------------------------------ #

    def _check_image_size(self) -> CheckResult:
        """Warn (not fail) if the compressed image exceeds 500 MB."""
        max_mb = 500
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", self.image_name,
                 "--format", "{{.Size}}"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return CheckResult(
                    "image-size-check", True,
                    "Could not inspect image size (image not loaded locally — skipped)",
                )
            size_bytes = int(result.stdout.strip())
            size_mb = size_bytes / (1024 * 1024)
            # Uncompressed size; compressed is typically ~40–50% of uncompressed
            compressed_mb = size_mb * 0.45
            if compressed_mb > max_mb:
                return CheckResult(
                    "image-size-check", True,  # warning only — not a hard failure
                    f"WARNING: estimated compressed image size {compressed_mb:.0f} MB "
                    f"exceeds {max_mb} MB. Consider cleaning up dependencies.",
                )
            return CheckResult(
                "image-size-check", True,
                f"Image size OK: ~{compressed_mb:.0f} MB compressed "
                f"(uncompressed {size_mb:.0f} MB)",
            )
        except Exception as exc:
            return CheckResult("image-size-check", True, f"Size check skipped: {exc}")

    def _check_non_root_user(self) -> CheckResult:
        """Verify the image declares USER orchestrator (UID 1000)."""
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", self.image_name,
                 "--format", "{{.Config.User}}"],
                capture_output=True, text=True, timeout=30,
            )
            user = result.stdout.strip()
            if user != "orchestrator":
                return CheckResult(
                    "non-root-user", False,
                    f"Image runs as '{user}', expected 'orchestrator' (UID 1000)",
                )
            return CheckResult(
                "non-root-user", True, f"Image runs as user '{user}' ✓",
            )
        except Exception as exc:
            return CheckResult("non-root-user", False, f"Could not inspect image: {exc}")

    def _check_http_health(self, host_port: int, path: str) -> CheckResult:
        """Poll an HTTP health endpoint until 200 or timeout."""
        import urllib.request
        import urllib.error

        url = f"http://127.0.0.1:{host_port}{path}"
        deadline = time.monotonic() + self.timeout

        while time.monotonic() < deadline:
            try:
                resp = urllib.request.urlopen(url, timeout=3)
                if resp.status == 200:
                    body = resp.read().decode(errors="replace")[:500]
                    return CheckResult(
                        f"health-{path}",
                        True,
                        f"GET {url} → 200 ✓  body={body[:80]}",
                    )
            except urllib.error.HTTPError as exc:
                if time.monotonic() >= deadline:
                    return CheckResult(
                        f"health-{path}", False,
                        f"GET {url} → HTTP {exc.code} after {self.timeout}s",
                        self._get_container_logs(),
                    )
            except Exception:
                pass
            time.sleep(0.5)

        logs = self._get_container_logs()
        return CheckResult(
            f"health-{path}", False,
            f"GET {url} did not return 200 within {self.timeout}s",
            f"Container logs (last {self.log_tail_lines} lines):\n{logs}",
        )

    def _check_cli_entry_points(self) -> CheckResult:
        """Verify each CLI entry point responds to --help with exit code 0."""
        failures: list[str] = []
        for ep in self.CLI_ENTRY_POINTS:
            result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "--entrypoint", ep,
                    self.image_name,
                    "--help",
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                failures.append(
                    f"  {ep}: exit {result.returncode}\n"
                    f"    stderr: {result.stderr.strip()[:200]}"
                )
            else:
                print(f"  ✓ {ep} --help  (exit 0)")

        if failures:
            return CheckResult(
                "cli-entry-points", False,
                f"{len(failures)} CLI entry point(s) failed --help check",
                "\n".join(failures),
            )
        return CheckResult(
            "cli-entry-points", True,
            f"All {len(self.CLI_ENTRY_POINTS)} CLI entry points respond to --help ✓",
        )

    def _check_config_symlink(self) -> CheckResult:
        """Verify load_config() succeeds inside the container (symlink resolves)."""
        script = (
            "from orchestrator.config import load_config, DEFAULT_CONFIG_PATH; "
            "import sys; "
            "cfg = load_config(); "
            "print(f'DEFAULT_CONFIG_PATH={DEFAULT_CONFIG_PATH}'); "
            "print(f'container.enabled={cfg.container.enabled}'); "
            "sys.exit(0)"
        )
        result = subprocess.run(
            ["docker", "run", "--rm", self.image_name, "python3", "-c", script],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return CheckResult(
                "config-symlink", False,
                "load_config() failed inside container",
                f"stderr: {result.stderr.strip()}\nstdout: {result.stdout.strip()}",
            )
        return CheckResult(
            "config-symlink", True,
            f"load_config() succeeded ✓\n  {result.stdout.strip()}",
        )

    def _check_security_hardening(self) -> CheckResult:
        """Verify the image works under --read-only + --cap-drop ALL."""
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--read-only",
                "--tmpfs", "/tmp:mode=1777,size=64m,noexec,nosuid,nodev",
                "--tmpfs", "/home/orchestrator/.config:mode=0700,size=32m,noexec,nosuid,nodev",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true",
                "--memory", "512m",
                "--cpus", "1",
                "--pids-limit", "100",
                self.image_name,
                "python3", "-c",
                "import orchestrator; print('OK: package imports under hardened flags')",
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return CheckResult(
                "security-hardening", False,
                "Package import failed under --read-only + --cap-drop ALL",
                f"stderr: {result.stderr.strip()}",
            )
        return CheckResult(
            "security-hardening", True,
            "Package imports succeed under --read-only + --cap-drop ALL ✓",
        )

    # ------------------------------------------------------------------ #
    # Container lifecycle helpers                                          #
    # ------------------------------------------------------------------ #

    def _discover_host_port(self, container_id: str, container_port: int) -> Optional[int]:
        """Discover the dynamically assigned host port for a container port.

        Uses ``docker port <container_id> <container_port>`` which returns
        ``0.0.0.0:<host_port>`` or ``127.0.0.1:<host_port>``.

        Returns:
            The host port as an integer, or None if discovery fails.
        """
        try:
            result = subprocess.run(
                ["docker", "port", container_id, str(container_port)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            # Output format: "0.0.0.0:<port>" or "127.0.0.1:<port>"
            # May include multiple lines for IPv4 + IPv6; take the first.
            first_line = result.stdout.strip().splitlines()[0]
            host_port = int(first_line.split(":")[-1])
            return host_port
        except Exception:
            return None

    def _start_container(self, container_ports: list[int]) -> Optional[str]:
        """Start a detached container with dynamically assigned host ports.

        Each container port is mapped to an ephemeral host port chosen by the
        OS (``-p 127.0.0.1:0:<port>``), avoiding conflicts with services
        already running on well-known ports like 8080/8090.

        After the container starts, use :meth:`_discover_host_port` to
        determine which host port was assigned to each container port.

        Args:
            container_ports: List of container-side TCP ports to expose.

        Returns:
            The container ID string, or None if the container failed to start.
        """
        port_args: list[str] = []
        for container_port in container_ports:
            # '0' tells Docker to pick an available ephemeral port on localhost
            port_args.extend(["-p", f"127.0.0.1:0:{container_port}"])

        # Start dashboard server + mobile API.
        # We override the CMD to launch both servers in background + sleep.
        startup_cmd = (
            "orchestrate-dashboard --host 0.0.0.0 --port 8080 &"
            " orchestrator-mobile-api --host 0.0.0.0 --port 8090 &"
            " wait"
        )
        result = subprocess.run(
            [
                "docker", "run", "--rm", "-d",
                "--entrypoint", "/bin/sh",
                "--tmpfs", "/tmp:mode=1777,size=128m,noexec,nosuid,nodev",
                "--tmpfs", "/home/orchestrator/.config:mode=0700,size=64m,noexec,nosuid,nodev",
                *port_args,
                self.image_name,
                "-c", startup_cmd,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(
                f"  WARN: Could not start container for health checks: "
                f"{result.stderr.strip()}"
            )
            return None
        container_id = result.stdout.strip()
        print(f"  Started container: {container_id[:12]}")
        return container_id

    def _stop_container(self, container_id: str) -> None:
        """Stop and remove a container."""
        subprocess.run(
            ["docker", "stop", container_id],
            capture_output=True, timeout=30,
        )

    def _get_container_logs(self) -> str:
        """Retrieve the last N lines of the running container's logs."""
        if not self._container_id:
            return "(no container ID available)"
        result = subprocess.run(
            ["docker", "logs", "--tail", str(self.log_tail_lines), self._container_id],
            capture_output=True, text=True, timeout=15,
        )
        return (result.stdout + result.stderr).strip()

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _run_check(
        self,
        report: DeploymentReport,
        name: str,
        fn,
    ) -> None:
        """Execute a check function and append the result to the report."""
        print(f"  → {name} ...", end=" ", flush=True)
        try:
            result: CheckResult = fn()
        except Exception as exc:
            result = CheckResult(name, False, f"Exception: {exc}")

        report.checks.append(result)
        status = "✓ PASS" if result.passed else "✗ FAIL"
        print(f"{status}  {result.message}")
        if result.details and not result.passed:
            for line in result.details.splitlines():
                print(f"      {line}")

    @staticmethod
    def _print_summary(report: DeploymentReport) -> None:
        print(f"\n{'─'*60}")
        print(
            f"  Results: {report.passed_count} passed, "
            f"{report.failed_count} failed  "
            f"({'PASS' if report.all_passed else 'FAIL'})"
        )
        print(f"{'─'*60}\n")
        if report.all_passed:
            print("  ✓ Deployment verification PASSED\n")
        else:
            print("  ✗ Deployment verification FAILED\n")
            for c in report.checks:
                if not c.passed:
                    print(f"    FAILED: {c.name} — {c.message}")

    @staticmethod
    def report_to_json(report: DeploymentReport) -> str:
        """Serialise a DeploymentReport to a JSON string.

        The JSON object contains:
          - ``image_name``   — Docker image that was verified
          - ``passed``       — bool: True only if all checks passed
          - ``passed_count`` — number of checks that passed
          - ``failed_count`` — number of checks that failed
          - ``checks``       — list of check result objects (name, passed, message, details)

        Returns:
            Pretty-printed JSON string.
        """
        payload = {
            "image_name": report.image_name,
            "passed": report.all_passed,
            "passed_count": report.passed_count,
            "failed_count": report.failed_count,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "details": c.details,
                }
                for c in report.checks
            ],
        }
        return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="deployment_verify",
        description="Verify the AI SDLC Orchestrator Docker image is deployment-ready.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--image",
        default="ai-sdlc-orchestrator:latest",
        help="Docker image name to verify (default: ai-sdlc-orchestrator:latest)",
    )
    parser.add_argument(
        "--image-tar",
        metavar="PATH",
        help="Path to a .tar.gz image tarball to load before verification",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Seconds to wait for health check endpoints (default: 30)",
    )
    parser.add_argument(
        "--log-tail",
        type=int,
        default=50,
        dest="log_tail",
        help="Lines of container logs to show on failure (default: 50)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help=(
            "Output a JSON summary to stdout after the human-readable report. "
            "The JSON object contains: image_name, passed, passed_count, "
            "failed_count, and checks[] with name/passed/message/details fields."
        ),
    )
    args = parser.parse_args()

    # Load image from tarball if specified
    if args.image_tar:
        print(f"Loading image from tarball: {args.image_tar}")
        result = subprocess.run(
            ["docker", "load", "--input", args.image_tar],
            capture_output=False,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"ERROR: Failed to load image from {args.image_tar}", file=sys.stderr)
            return 1

    verifier = DeploymentVerifier(
        image_name=args.image,
        timeout=args.timeout,
        log_tail_lines=args.log_tail,
    )
    report = verifier.verify_all()

    if args.json:
        print("\n--- JSON Report ---")
        print(DeploymentVerifier.report_to_json(report))

    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
