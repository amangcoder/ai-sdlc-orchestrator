"""Container lifecycle management for isolated orchestration runs.

Each orchestration run is executed inside an ephemeral Docker container with
full security hardening:  read-only rootfs, capability drops, seccomp/AppArmor
profiles, resource limits, and a restricted egress network policy.

Classes
-------
ArtifactBridgeVolume
    Manages the bind-mount that exposes only the run-specific workspace
    subdirectory to the container.

ContainerRuntime
    Owns the full lifecycle of one ephemeral container per orchestration run:
    assembles docker run arguments, streams output, forwards signals, and
    validates post-exit output.
"""

from __future__ import annotations

import re
import shlex
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Sequence

import structlog

# ── Module constants ──────────────────────────────────────────────────────────

#: Minimum Docker Engine version required.  Versions below this may lack the
#: security features (seccomp v2, rootfs read-only, --init) used here.
MIN_DOCKER_VERSION: tuple[int, int] = (20, 10)

#: Files that must never appear in the bind-mounted output directory.  Their
#: presence suggests the container process attempted to modify the host user's
#: shell/SSH/git configuration.
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {".bashrc", ".profile", ".ssh", ".gitconfig", ".env"}
)

# Regex for validating Docker image names (subset of the OCI distribution spec).
# Supports:
#   - name:tag         e.g. ai-sdlc-orchestrator:latest
#   - name@digest      e.g. myimage@sha256:abc123def456
#   - registry/name:tag  e.g. myrepo/orchestrator:v1.0
_IMAGE_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/\-]*"
    r"(@[a-z][a-z0-9]*:[a-zA-Z0-9._\-:]+)?"  # optional @digest (e.g. @sha256:abc)
    r"(:[a-zA-Z0-9._\-]+)?$"  # optional :tag
)

# Regex matching the project's standard run-ID format (12 hex digits)
_RUN_ID_RE = re.compile(r"^[0-9a-f]{12}$")

# Allowed values for container network_mode
_ALLOWED_NETWORKS: frozenset[str] = frozenset(
    {"orchestrator-net", "bridge", "none", "host"}
)

# extra_tmpfs path allowlist: only paths under these prefixes are permitted.
# This prevents config-injection attacks where a crafted extra_tmpfs value
# shadows the artifact bind-mount or a kernel virtual filesystem (/proc, /sys).
_ALLOWED_TMPFS_PREFIXES: tuple[str, ...] = (
    "/home/orchestrator/",
    "/tmp/",
    "/var/tmp/",
    "/run/orchestrator/",
)

# Regex for validating a single tmpfs spec: /allowed/path[:options]
# The path component must start with one of _ALLOWED_TMPFS_PREFIXES.
# Options (after the colon) are a comma-separated list of key=value pairs
# or bare words (e.g. "noexec,nosuid,size=64m").
_TMPFS_OPTIONS_RE = re.compile(r"^[a-zA-Z0-9=,._-]{0,200}$")

log = structlog.get_logger(__name__)


# ── ArtifactBridgeVolume ──────────────────────────────────────────────────────


class ArtifactBridgeVolume:
    """Manages the bind-mount that exposes the run-specific workspace to the container.

    Security invariants enforced
    ----------------------------
    * ``run_id`` must match ``^[0-9a-f]{12}$`` — no slashes, dots, or shell
      metacharacters are accepted.
    * The resolved host directory is asserted to be a *child* of
      ``workspace_root/runs/`` before and after creation to defend against
      TOCTOU / symlink attacks.
    """

    @staticmethod
    def prepare_host_run_dir(workspace_root: Path, run_id: str) -> Path:
        """Create and validate the host directory for a single run.

        Parameters
        ----------
        workspace_root:
            Absolute path to the workspace root directory (e.g. ``./workspace``).
        run_id:
            12-character lowercase hex run identifier.

        Returns
        -------
        Path
            Resolved absolute path to the newly created run directory.

        Raises
        ------
        ValueError
            If ``run_id`` does not match the expected format, or if the
            resolved path escapes ``workspace_root/runs/``.
        """
        if not _RUN_ID_RE.match(run_id):
            raise ValueError(
                f"Invalid run_id {run_id!r}: must match ^[0-9a-f]{{12}}$"
            )

        runs_root = workspace_root.resolve() / "runs"
        target = runs_root / run_id

        # Pre-creation TOCTOU check: resolve() follows symlinks, so if a
        # symlink was planted before mkdir this will expose the redirect.
        # We must check parent first since the directory may not exist yet.
        runs_root.mkdir(parents=True, exist_ok=True)
        resolved_runs = runs_root.resolve()

        # Build the expected resolved path before mkdir
        expected_resolved = resolved_runs / run_id

        target.mkdir(parents=True, exist_ok=True)

        # Post-creation check: resolve after mkdir catches any symlink swap
        actual_resolved = target.resolve()

        if actual_resolved != expected_resolved:
            raise ValueError(
                f"Path traversal detected: resolved path {actual_resolved} "
                f"does not match expected {expected_resolved}"
            )

        # Final assertion: resolved path must be a child of workspace_root/runs/
        try:
            actual_resolved.relative_to(resolved_runs)
        except ValueError:
            raise ValueError(
                f"Security violation: run directory {actual_resolved} "
                f"is not inside {resolved_runs}"
            )

        log.info("run_dir_prepared", path=str(actual_resolved), run_id=run_id)
        return actual_resolved

    @staticmethod
    def build_mount_arg(host_run_dir: Path, run_id: str) -> str:
        """Return the ``--mount`` argument string for ``docker run``.

        Uses the type=bind form with resolved absolute paths to avoid any
        Docker daemon path interpretation surprises.
        """
        resolved = host_run_dir.resolve()
        container_path = f"/workspace/runs/{run_id}"
        return (
            f"type=bind,source={resolved},target={container_path},consistency=delegated"
        )


# ── ContainerRuntime ─────────────────────────────────────────────────────────


class ContainerRuntime:
    """Owns the full lifecycle of one ephemeral container per orchestration run.

    All Docker interaction goes through ``subprocess`` (not docker-py) per the
    architecture decision to avoid the large SDK dependency and to ensure
    argument lists are never passed through a shell.
    """

    # ── Validation helpers ────────────────────────────────────────────────────

    @staticmethod
    def _validate_image(image: str) -> None:
        """Raise ValueError if *image* does not match the OCI image name regex."""
        if not _IMAGE_RE.match(image):
            raise ValueError(
                f"Invalid Docker image name {image!r}: "
                "must match ^[a-z0-9][a-z0-9._/-]*(:[a-zA-Z0-9._-]+)?$"
            )

    @staticmethod
    def _validate_extra_tmpfs(specs: list[str]) -> None:
        """Raise ValueError if any extra_tmpfs spec is outside the allowed path prefixes.

        This prevents configuration injection attacks where a crafted tmpfs spec
        could shadow the artifact bind-mount, a kernel virtual filesystem, or
        inject mount options that weaken container isolation.

        Valid examples:
            /home/orchestrator/cache
            /home/orchestrator/cache:size=128m,noexec,nosuid,nodev
            /var/tmp/scratch:mode=1777,size=256m

        Invalid examples (rejected):
            /proc                      — kernel virtual filesystem
            /sys                       — kernel virtual filesystem
            /workspace/runs/abc123     — would shadow artifact bind-mount
            /tmp/../etc                — path traversal attempt
        """
        for spec in specs:
            # Split into path and options components
            parts = spec.split(":", 1)
            path = parts[0]
            options = parts[1] if len(parts) == 2 else ""

            # Reject path traversal attempts
            if ".." in path or "\x00" in path:
                raise ValueError(
                    f"extra_tmpfs path {path!r} contains '..' or null bytes — "
                    "path traversal is not permitted."
                )

            # Enforce path prefix allowlist
            if not any(path.startswith(prefix) for prefix in _ALLOWED_TMPFS_PREFIXES):
                raise ValueError(
                    f"extra_tmpfs path {path!r} is not under an allowed prefix. "
                    f"Allowed prefixes: {sorted(_ALLOWED_TMPFS_PREFIXES)}"
                )

            # Validate options string against a conservative character allowlist
            if options and not _TMPFS_OPTIONS_RE.match(options):
                raise ValueError(
                    f"extra_tmpfs options {options!r} contain characters outside the "
                    "allowed set [a-zA-Z0-9=,._-]. "
                    "This prevents injection of shell metacharacters."
                )

    @staticmethod
    def _validate_network_mode(mode: str) -> None:
        """Raise ValueError if *mode* is not in the allowed network allowlist."""
        if mode not in _ALLOWED_NETWORKS:
            raise ValueError(
                f"Invalid network_mode {mode!r}: "
                f"must be one of {sorted(_ALLOWED_NETWORKS)}"
            )

    # ── Docker availability check ─────────────────────────────────────────────

    @staticmethod
    def is_docker_available() -> bool:
        """Check that Docker Engine >= MIN_DOCKER_VERSION is reachable.

        Returns
        -------
        bool
            ``True`` if Docker is available and meets the version requirement.

        Raises
        ------
        RuntimeError
            If Docker is not installed, the daemon is unreachable, or the
            version is below MIN_DOCKER_VERSION.
        """
        try:
            result = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "Docker is not installed or not on PATH. "
                "Install Docker Engine >= 20.10 to use containerized orchestration."
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "Docker daemon did not respond within 10 seconds. "
                "Is Docker running?"
            )

        if result.returncode != 0:
            raise RuntimeError(
                f"Docker daemon is unreachable (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )

        version_str = result.stdout.strip()
        # Parse "24.0.5" → (24, 0)
        try:
            parts = version_str.split(".")
            found_version = (int(parts[0]), int(parts[1]))
        except (IndexError, ValueError):
            raise RuntimeError(
                f"Could not parse Docker version {version_str!r}. "
                f"Expected format: MAJOR.MINOR.PATCH"
            )

        if found_version < MIN_DOCKER_VERSION:
            min_str = ".".join(str(v) for v in MIN_DOCKER_VERSION)
            raise RuntimeError(
                f"Docker Engine {version_str} is below the required version "
                f"{min_str}. Please upgrade Docker."
            )

        log.info(
            "docker_available",
            version=version_str,
            min_version=".".join(str(v) for v in MIN_DOCKER_VERSION),
        )
        return True

    # ── Argument assembly ─────────────────────────────────────────────────────

    @staticmethod
    def build_run_args(
        run_id: str,
        feature_request: str,
        config,  # ContainerConfig
        host_run_dir: Path,
        extra_cli_args: Sequence[str] | None = None,
    ) -> list[str]:
        """Assemble the complete ``docker run`` argument list.

        All arguments are returned as a Python list — never a shell string —
        to prevent shell injection.  The caller passes this list directly to
        ``subprocess.Popen``.

        Parameters
        ----------
        run_id:
            12-character hex run identifier, used to name the container.
        feature_request:
            The orchestration prompt passed as the ``orchestrate`` positional
            argument inside the container.
        config:
            A ``ContainerConfig`` instance with image, limits, security opts.
        host_run_dir:
            Resolved absolute host path created by ``ArtifactBridgeVolume``.
        extra_cli_args:
            Additional flags appended after the ``orchestrate`` command inside
            the container (e.g. ``["--config", "/tmp/override.yaml"]``).

        Raises
        ------
        ValueError
            If ``config.image`` or ``config.network_mode`` fails validation.
        """
        # Validate user-controlled config values before constructing args
        ContainerRuntime._validate_image(config.image)
        ContainerRuntime._validate_network_mode(config.network_mode)
        ContainerRuntime._validate_extra_tmpfs(list(config.extra_tmpfs))

        container_name = f"orchestrator-{run_id}"
        mount_arg = ArtifactBridgeVolume.build_mount_arg(host_run_dir, run_id)

        args: list[str] = ["docker", "run"]

        # ── Lifecycle ──────────────────────────────────────────────────────
        args += ["--rm"]
        args += ["--name", container_name]
        args += ["--init"]  # reap zombie processes with tini
        # Give the orchestrator process 60s to finish in-flight LLM API calls
        # and flush artifact writes before SIGKILL is sent.  Docker's default
        # is 10s which is too short for a live agent call.
        args += ["--stop-timeout", "60"]

        # ── Observability labels ────────────────────────────────────────────
        # Labels are read-only metadata; they never affect container behaviour.
        args += ["--label", f"orchestrator.run_id={run_id}"]
        args += ["--label", "orchestrator.component=pipeline"]

        # ── Filesystem hardening ───────────────────────────────────────────
        args += ["--read-only"]
        # Hardened tmpfs mounts: noexec prevents fileless payload execution,
        # nosuid prevents setuid escalation, nodev prevents device file creation.
        # Size limits bound memory consumption from within the container.
        # Options mirror infra/docker/docker-compose.yml for consistency.
        args += ["--tmpfs", "/tmp:mode=1777,size=512m,noexec,nosuid,nodev"]
        args += ["--tmpfs", "/home/orchestrator/.config:mode=0700,size=64m,noexec,nosuid,nodev"]

        # Add any extra tmpfs mounts from config
        for tmpfs_path in config.extra_tmpfs:
            args += ["--tmpfs", tmpfs_path]

        # ── Capability drops ───────────────────────────────────────────────
        args += ["--cap-drop", "ALL"]

        # ── Security profiles ──────────────────────────────────────────────
        # Prevent privilege escalation via setuid/setgid binaries.  Applied
        # unconditionally — no performance cost, meaningful defence-in-depth.
        args += ["--security-opt", "no-new-privileges:true"]

        if config.seccomp_profile_path:
            args += ["--security-opt", f"seccomp={config.seccomp_profile_path}"]

        if config.apparmor_profile:
            if sys.platform == "linux":
                args += ["--security-opt", f"apparmor={config.apparmor_profile}"]
            else:
                log.info(
                    "apparmor_skipped_non_linux",
                    platform=sys.platform,
                    profile=config.apparmor_profile,
                    message="AppArmor enforcement is only available on Linux; "
                    "the apparmor_profile setting is ignored on this platform.",
                )

        # ── Resource limits ────────────────────────────────────────────────
        args += ["--memory", config.memory_limit]
        args += ["--memory-swap", config.memory_limit]  # disable swap (same as memory → swap=0)
        args += ["--cpus", str(config.cpu_limit)]
        args += ["--pids-limit", str(config.pids_limit)]

        # ── Logging ────────────────────────────────────────────────────────
        # Use the local log driver with a capped file size.  Logs are already
        # streamed to host stdout via the _stream threads; this prevents the
        # Docker json-file driver from accumulating unbounded log data on disk.
        # The local driver uses a compressed binary format (more space-efficient
        # than json-file) and rolls over at max-size.
        args += ["--log-driver", "local"]
        args += ["--log-opt", "max-size=50m"]
        args += ["--log-opt", "max-file=3"]

        # ── Networking ─────────────────────────────────────────────────────
        args += ["--network", config.network_mode]

        # ── DNS pre-resolution: inject --add-host so the container can reach
        # api.anthropic.com even when container DNS (port 53) is blocked by
        # the egress iptables policy.
        anthropic_ips = ContainerRuntime._resolve_anthropic_ips()
        for ip in anthropic_ips:
            args += ["--add-host", f"api.anthropic.com:{ip}"]

        # ── Secrets injection ──────────────────────────────────────────────
        if config.env_file:
            args += ["--env-file", str(config.env_file)]

        # ── Artifact bind-mount ────────────────────────────────────────────
        args += ["--mount", mount_arg]

        # ── Image ──────────────────────────────────────────────────────────
        args.append(config.image)

        # ── Container command ──────────────────────────────────────────────
        args += ["orchestrate", feature_request]

        if extra_cli_args:
            args.extend(extra_cli_args)

        return args

    # ── DNS pre-resolution ────────────────────────────────────────────────────

    @staticmethod
    def _resolve_anthropic_ips() -> list[str]:
        """Resolve api.anthropic.com IPs on the host for --add-host injection.

        Pre-resolving on the host and injecting via --add-host lets us block
        container DNS (port 53) while still allowing HTTPS to the Anthropic API.
        """
        try:
            results = socket.getaddrinfo("api.anthropic.com", 443, type=socket.SOCK_STREAM)
            # Filter to IPv4 addresses only.  The iptables FORWARD rules in
            # setup-network-policy.sh are IPv4-only; including IPv6 addresses here
            # would inject --add-host entries that bypass iptables filtering entirely
            # (there are no ip6tables rules for the orchestrator-net subnet).
            ips = list({r[4][0] for r in results if r[0] == socket.AF_INET})
            if not ips:
                # Fallback: include all results if no IPv4 addresses were found
                # (e.g. IPv6-only host).  Log a warning because iptables rules
                # will not filter these connections on Linux.
                ips = list({r[4][0] for r in results})
                log.warning(
                    "anthropic_ips_no_ipv4",
                    ips=ips,
                    message=(
                        "No IPv4 addresses found for api.anthropic.com. "
                        "IPv6 addresses are not filtered by the iptables egress rules. "
                        "Run setup-network-policy.sh --refresh to add ip6tables rules."
                    ),
                )
            log.info("anthropic_ips_resolved", ips=ips)
            return ips
        except OSError as exc:
            log.warning("anthropic_ip_resolution_failed", error=str(exc))
            return []

    # ── Orphan detection ──────────────────────────────────────────────────────

    @staticmethod
    def _check_orphaned_containers() -> None:
        """Log a warning for any lingering orchestrator containers."""
        try:
            result = subprocess.run(
                ["docker", "ps", "-q", "--filter", "name=orchestrator-"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                orphan_ids = result.stdout.strip().splitlines()
                log.warning(
                    "orphaned_containers_detected",
                    count=len(orphan_ids),
                    container_ids=orphan_ids,
                    hint="Run 'docker rm -f <id>' to remove lingering containers.",
                )
        except Exception as exc:
            log.debug("orphan_check_failed", error=str(exc))

    # ── Forbidden-file check ──────────────────────────────────────────────────

    @staticmethod
    def _check_forbidden_names(output_dir: Path) -> None:
        """Warn if any FORBIDDEN_NAMES appear anywhere in the bind-mounted output directory.

        Uses ``rglob`` to scan the full directory tree, not just the top level.
        A malicious container process could create a subdirectory (e.g.
        ``artifacts/.ssh/``) to evade a shallow top-level check.
        """
        try:
            for entry in output_dir.rglob("*"):
                if entry.name in FORBIDDEN_NAMES:
                    log.warning(
                        "forbidden_file_in_output",
                        file=str(entry),
                        name=entry.name,
                        depth=len(entry.relative_to(output_dir).parts),
                        message=(
                            f"Container wrote suspicious file {entry.name!r} to the "
                            "bind-mounted output directory.  This may indicate a "
                            "container escape attempt."
                        ),
                    )
        except OSError as exc:
            log.debug("forbidden_check_failed", error=str(exc))

    # ── Main run entrypoint ───────────────────────────────────────────────────

    async def run(
        self,
        run_id: str,
        feature_request: str,
        config,  # ContainerConfig
        workspace_root: Path,
        extra_cli_args: Sequence[str] | None = None,
        host_run_dir: Path | None = None,
    ) -> int:
        """Spin up and supervise one ephemeral container.

        Parameters
        ----------
        run_id:
            12-character hex identifier for this orchestration run.
        feature_request:
            The orchestration prompt.
        config:
            ``ContainerConfig`` with image, limits, and security settings.
        workspace_root:
            Host workspace root; the run directory is created here.
        extra_cli_args:
            Additional flags forwarded to ``orchestrate`` inside the container.
        host_run_dir:
            Pre-prepared host run directory (optional).  If not provided,
            ``ArtifactBridgeVolume.prepare_host_run_dir()`` is called.

        Returns
        -------
        int
            The container's exit code (0 = success).
        """
        import asyncio
        import signal

        if host_run_dir is None:
            host_run_dir = ArtifactBridgeVolume.prepare_host_run_dir(workspace_root, run_id)

        # build_run_args handles DNS pre-resolution and --add-host injection internally
        args = self.build_run_args(
            run_id=run_id,
            feature_request=feature_request,
            config=config,
            host_run_dir=host_run_dir,
            extra_cli_args=extra_cli_args,
        )

        container_name = f"orchestrator-{run_id}"
        log.info(
            "container_starting",
            run_id=run_id,
            command=shlex.join(args),
        )

        self._check_orphaned_containers()

        # Launch the container
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Stream stdout and stderr in background threads
        def _stream(src, dst) -> None:
            try:
                for line in src:
                    dst.buffer.write(line)
                    dst.buffer.flush()
            except Exception:
                pass

        stdout_thread = threading.Thread(
            target=_stream, args=(proc.stdout, sys.stdout), daemon=True
        )
        stderr_thread = threading.Thread(
            target=_stream, args=(proc.stderr, sys.stderr), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()

        # SIGTERM/SIGINT handler: forward stop signal to the container
        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        def _forward_signal(signum, frame):
            log.info("forwarding_signal_to_container", container=container_name, signal=signum)
            subprocess.run(
                ["docker", "stop", container_name],
                capture_output=True,
                timeout=30,
            )

        signal.signal(signal.SIGTERM, _forward_signal)
        signal.signal(signal.SIGINT, _forward_signal)

        try:
            # Wait for the process in a non-blocking manner compatible with asyncio
            loop = asyncio.get_event_loop()
            returncode = await loop.run_in_executor(None, proc.wait)
        finally:
            signal.signal(signal.SIGTERM, original_sigterm)
            signal.signal(signal.SIGINT, original_sigint)

        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

        log.info(
            "container_exited",
            run_id=run_id,
            returncode=returncode,
        )

        # Post-exit: check for suspicious files in the bind-mounted output dir
        self._check_forbidden_names(host_run_dir)

        return returncode
