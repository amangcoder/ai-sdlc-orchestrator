"""Tests for ContainerRuntime, ArtifactBridgeVolume, and container hardening logic.

Covers TASK-007 acceptance criteria for container runtime:
  - is_docker_available() checks Docker Engine version >= MIN_DOCKER_VERSION
  - build_run_args() includes all required security flags
  - Image name validation rejects invalid references
  - Network mode validation enforces the allowlist
  - ArtifactBridgeVolume path traversal detection
  - Forbidden file names are detected in the output directory

All Docker subprocess calls are mocked — zero real Docker dependency for CI.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.container_runner import (
    MIN_DOCKER_VERSION,
    ArtifactBridgeVolume,
    ContainerRuntime,
    FORBIDDEN_NAMES,
)
from orchestrator.models import ContainerConfig


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_config(**overrides) -> ContainerConfig:
    """Return a ContainerConfig with sensible test defaults."""
    defaults = {
        "enabled": True,
        "image": "ai-sdlc-orchestrator:latest",
        "memory_limit": "8g",
        "cpu_limit": 4.0,
        "pids_limit": 500,
        "network_mode": "orchestrator-net",
        "seccomp_profile_path": None,
        "apparmor_profile": None,
        "env_file": None,
        "extra_tmpfs": [],
    }
    defaults.update(overrides)
    return ContainerConfig(**defaults)


def _mock_docker_version(version: str = "24.0.5") -> MagicMock:
    """Return a mock subprocess.CompletedProcess for 'docker version --format ...'."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = version
    mock.stderr = ""
    return mock


# ── TestIsDockerAvailable ─────────────────────────────────────────────────────


class TestIsDockerAvailable:
    """Verify Docker version detection and minimum version enforcement."""

    def test_returns_true_for_supported_version(self):
        with patch("subprocess.run", return_value=_mock_docker_version("24.0.5")):
            assert ContainerRuntime.is_docker_available() is True

    def test_returns_true_for_exactly_minimum_version(self):
        min_ver = f"{MIN_DOCKER_VERSION[0]}.{MIN_DOCKER_VERSION[1]}.0"
        with patch("subprocess.run", return_value=_mock_docker_version(min_ver)):
            assert ContainerRuntime.is_docker_available() is True

    def test_raises_for_old_version(self):
        old_ver = "19.03.0"
        with patch("subprocess.run", return_value=_mock_docker_version(old_ver)):
            with pytest.raises(RuntimeError, match=r"(below|upgrade|Docker Engine 19)"):
                ContainerRuntime.is_docker_available()

    def test_raises_for_version_18(self):
        with patch("subprocess.run", return_value=_mock_docker_version("18.09.0")):
            with pytest.raises(RuntimeError):
                ContainerRuntime.is_docker_available()

    def test_raises_when_docker_not_on_path(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match=r"[Nn]ot.*install|[Nn]ot.*[Pp]ath"):
                ContainerRuntime.is_docker_available()

    def test_raises_when_docker_daemon_not_running(self):
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        mock.stderr = "Cannot connect to the Docker daemon"
        with patch("subprocess.run", return_value=mock):
            with pytest.raises(RuntimeError, match=r"[Dd]aemon|unreachable"):
                ContainerRuntime.is_docker_available()

    def test_raises_on_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 10)):
            with pytest.raises(RuntimeError, match=r"[Tt]imeout|respond"):
                ContainerRuntime.is_docker_available()


# ── TestBuildRunArgs ──────────────────────────────────────────────────────────


class TestBuildRunArgs:
    """Verify the assembled docker run argument list contains all required flags."""

    def _build(self, tmp_path: Path, **config_overrides) -> list[str]:
        config = _make_config(**config_overrides)
        host_run_dir = tmp_path / "workspace" / "runs" / "abc123def456"
        host_run_dir.mkdir(parents=True)
        with patch.object(ContainerRuntime, "_resolve_anthropic_ips", return_value=[]):
            return ContainerRuntime.build_run_args(
                run_id="abc123def456",
                feature_request="Build a todo app",
                config=config,
                host_run_dir=host_run_dir,
            )

    def test_starts_with_docker_run(self, tmp_path):
        args = self._build(tmp_path)
        assert args[0] == "docker"
        assert args[1] == "run"

    def test_rm_flag_present(self, tmp_path):
        args = self._build(tmp_path)
        assert "--rm" in args

    def test_read_only_flag_present(self, tmp_path):
        args = self._build(tmp_path)
        assert "--read-only" in args

    def test_init_flag_present(self, tmp_path):
        args = self._build(tmp_path)
        assert "--init" in args

    def test_cap_drop_all_present(self, tmp_path):
        args = self._build(tmp_path)
        assert "--cap-drop" in args
        idx = args.index("--cap-drop")
        assert args[idx + 1] == "ALL"

    def test_tmpfs_slash_tmp_present(self, tmp_path):
        args = self._build(tmp_path)
        # Find all --tmpfs flags and their values
        tmpfs_values = [args[i + 1] for i, a in enumerate(args) if a == "--tmpfs"]
        # /tmp must be in the tmpfs list
        assert any("/tmp" in v for v in tmpfs_values), \
            f"--tmpfs /tmp not found. Actual tmpfs values: {tmpfs_values}"

    def test_tmpfs_slash_tmp_has_hardening_options(self, tmp_path):
        """Critical: /tmp tmpfs must include noexec,nosuid,nodev to prevent fileless execution."""
        args = self._build(tmp_path)
        tmpfs_values = [args[i + 1] for i, a in enumerate(args) if a == "--tmpfs"]
        tmp_entry = next((v for v in tmpfs_values if v.startswith("/tmp")), None)
        assert tmp_entry is not None, f"No /tmp tmpfs entry found; got: {tmpfs_values}"
        for opt in ("noexec", "nosuid", "nodev"):
            assert opt in tmp_entry, (
                f"tmpfs /tmp is missing '{opt}' option (required to prevent fileless payload "
                f"execution and privilege escalation). Got: {tmp_entry!r}"
            )

    def test_tmpfs_orchestrator_config_has_hardening_options(self, tmp_path):
        """Critical: ~/.config tmpfs must include noexec,nosuid,nodev."""
        args = self._build(tmp_path)
        tmpfs_values = [args[i + 1] for i, a in enumerate(args) if a == "--tmpfs"]
        config_entry = next(
            (v for v in tmpfs_values if "orchestrator" in v and ".config" in v), None
        )
        assert config_entry is not None, (
            f"No /home/orchestrator/.config tmpfs entry found; got: {tmpfs_values}"
        )
        for opt in ("noexec", "nosuid", "nodev"):
            assert opt in config_entry, (
                f"tmpfs /home/orchestrator/.config is missing '{opt}' option. "
                f"Got: {config_entry!r}"
            )

    def test_pids_limit_present(self, tmp_path):
        args = self._build(tmp_path)
        assert "--pids-limit" in args
        idx = args.index("--pids-limit")
        assert args[idx + 1] == "500"

    def test_memory_flag_present(self, tmp_path):
        args = self._build(tmp_path)
        assert "--memory" in args
        idx = args.index("--memory")
        assert args[idx + 1] == "8g"

    def test_cpus_flag_present(self, tmp_path):
        args = self._build(tmp_path)
        assert "--cpus" in args
        idx = args.index("--cpus")
        assert args[idx + 1] == "4.0"

    def test_network_flag_present(self, tmp_path):
        args = self._build(tmp_path)
        assert "--network" in args
        idx = args.index("--network")
        assert args[idx + 1] == "orchestrator-net"

    def test_container_name_contains_run_id(self, tmp_path):
        args = self._build(tmp_path)
        assert "--name" in args
        idx = args.index("--name")
        assert "abc123def456" in args[idx + 1]

    def test_mount_arg_present(self, tmp_path):
        args = self._build(tmp_path)
        assert "--mount" in args

    def test_image_present(self, tmp_path):
        args = self._build(tmp_path)
        assert "ai-sdlc-orchestrator:latest" in args

    def test_orchestrate_command_present(self, tmp_path):
        args = self._build(tmp_path)
        assert "orchestrate" in args

    def test_feature_request_present(self, tmp_path):
        args = self._build(tmp_path)
        # Feature request should appear after the orchestrate command
        orch_idx = args.index("orchestrate")
        remaining = args[orch_idx:]
        assert "Build a todo app" in remaining

    def test_args_is_list_not_string(self, tmp_path):
        """Critical: args must be a list, never a shell string."""
        args = self._build(tmp_path)
        assert isinstance(args, list)
        for arg in args:
            assert isinstance(arg, str), f"Non-string arg: {arg!r}"

    def test_no_new_privileges_always_present(self, tmp_path):
        """--security-opt no-new-privileges:true must appear unconditionally."""
        args = self._build(tmp_path)
        security_opts = [args[i + 1] for i, a in enumerate(args) if a == "--security-opt"]
        assert any(opt == "no-new-privileges:true" for opt in security_opts), (
            f"no-new-privileges:true not found in security-opts. Got: {security_opts}"
        )

    def test_seccomp_flag_added_when_profile_set(self, tmp_path):
        # Create a fake seccomp profile file
        profile_path = tmp_path / "seccomp.json"
        profile_path.write_text('{"defaultAction": "SCMP_ACT_ALLOW"}')
        args = self._build(tmp_path, seccomp_profile_path=str(profile_path))
        assert "--security-opt" in args
        security_opts = [args[i + 1] for i, a in enumerate(args) if a == "--security-opt"]
        assert any("seccomp=" in opt for opt in security_opts), \
            f"seccomp --security-opt not found. Got: {security_opts}"

    def test_seccomp_flag_absent_when_profile_not_set(self, tmp_path):
        args = self._build(tmp_path, seccomp_profile_path=None)
        security_opts = [args[i + 1] for i, a in enumerate(args) if a == "--security-opt"]
        assert not any("seccomp=" in opt for opt in security_opts)

    def test_apparmor_flag_added_on_linux(self, tmp_path):
        with patch("sys.platform", "linux"):
            args = self._build(tmp_path, apparmor_profile="orchestrator-profile")
        security_opts = [args[i + 1] for i, a in enumerate(args) if a == "--security-opt"]
        assert any("apparmor=" in opt for opt in security_opts), \
            f"AppArmor --security-opt not added on linux. Got: {security_opts}"

    def test_apparmor_flag_absent_on_darwin(self, tmp_path):
        with patch("sys.platform", "darwin"):
            args = self._build(tmp_path, apparmor_profile="orchestrator-profile")
        security_opts = [args[i + 1] for i, a in enumerate(args) if a == "--security-opt"]
        assert not any("apparmor=" in opt for opt in security_opts), \
            "AppArmor flag must NOT be added on macOS"

    def test_add_host_flags_injected_for_resolved_ips(self, tmp_path):
        config = _make_config()
        host_run_dir = tmp_path / "workspace" / "runs" / "abc123def456"
        host_run_dir.mkdir(parents=True)
        with patch.object(
            ContainerRuntime, "_resolve_anthropic_ips", return_value=["1.2.3.4", "5.6.7.8"]
        ):
            args = ContainerRuntime.build_run_args(
                run_id="abc123def456",
                feature_request="test",
                config=config,
                host_run_dir=host_run_dir,
            )
        add_host_values = [args[i + 1] for i, a in enumerate(args) if a == "--add-host"]
        assert any("api.anthropic.com:1.2.3.4" in v for v in add_host_values)
        assert any("api.anthropic.com:5.6.7.8" in v for v in add_host_values)

    def test_env_file_flag_added_when_set(self, tmp_path):
        env_file = tmp_path / ".orchestrator.env"
        env_file.write_text("ANTHROPIC_API_KEY=sk-test\n")
        args = self._build(tmp_path, env_file=env_file)
        assert "--env-file" in args

    def test_stop_timeout_present(self, tmp_path):
        """--stop-timeout must be set to give in-flight LLM calls time to complete."""
        args = self._build(tmp_path)
        assert "--stop-timeout" in args
        idx = args.index("--stop-timeout")
        # Value must be parseable as a positive integer
        assert int(args[idx + 1]) > 0, f"--stop-timeout value must be a positive int, got: {args[idx + 1]!r}"

    def test_memory_swap_matches_memory(self, tmp_path):
        """--memory-swap must be set to disable swap (container cannot exceed RAM limit)."""
        args = self._build(tmp_path)
        assert "--memory-swap" in args
        mem_idx = args.index("--memory")
        swap_idx = args.index("--memory-swap")
        assert args[mem_idx + 1] == args[swap_idx + 1], (
            "--memory-swap should equal --memory to disable swap entirely"
        )

    def test_log_driver_local_present(self, tmp_path):
        """--log-driver=local must be set to prevent unbounded json-file log growth."""
        args = self._build(tmp_path)
        assert "--log-driver" in args
        idx = args.index("--log-driver")
        assert args[idx + 1] == "local", (
            f"--log-driver must be 'local' to cap log size; got: {args[idx + 1]!r}"
        )

    def test_log_opt_max_size_present(self, tmp_path):
        """--log-opt max-size=... must limit per-run log disk usage."""
        args = self._build(tmp_path)
        log_opts = [args[i + 1] for i, a in enumerate(args) if a == "--log-opt"]
        assert any(opt.startswith("max-size=") for opt in log_opts), (
            f"--log-opt max-size not found. Actual log-opts: {log_opts}"
        )

    def test_container_labels_present(self, tmp_path):
        """--label orchestrator.run_id=<run_id> must be set for observability."""
        args = self._build(tmp_path)
        labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
        assert any("orchestrator.run_id=" in lbl for lbl in labels), (
            f"orchestrator.run_id label not found in labels: {labels}"
        )

    def test_extra_cli_args_appended(self, tmp_path):
        config = _make_config()
        host_run_dir = tmp_path / "workspace" / "runs" / "abc123def456"
        host_run_dir.mkdir(parents=True)
        with patch.object(ContainerRuntime, "_resolve_anthropic_ips", return_value=[]):
            args = ContainerRuntime.build_run_args(
                run_id="abc123def456",
                feature_request="test",
                config=config,
                host_run_dir=host_run_dir,
                extra_cli_args=["--speed", "turbo"],
            )
        assert "--speed" in args
        idx = args.index("--speed")
        assert args[idx + 1] == "turbo"


# ── TestImageNameValidation ───────────────────────────────────────────────────


class TestImageNameValidation:
    """Verify image name validation prevents injection attacks."""

    def test_valid_simple_image(self):
        ContainerRuntime._validate_image("ai-sdlc-orchestrator:latest")  # no exception

    def test_valid_image_with_registry(self):
        ContainerRuntime._validate_image("myrepo/orchestrator:v1.0")  # no exception

    def test_valid_image_with_sha256(self):
        ContainerRuntime._validate_image("myimage@sha256:abc123")  # no exception (if RE allows)

    def test_invalid_image_with_privileged_flag(self):
        with pytest.raises(ValueError, match=r"[Ii]nvalid"):
            ContainerRuntime._validate_image("--privileged evil:latest")

    def test_invalid_image_uppercase(self):
        with pytest.raises(ValueError):
            ContainerRuntime._validate_image("UPPERCASE/Image:tag")

    def test_invalid_image_with_space(self):
        with pytest.raises(ValueError):
            ContainerRuntime._validate_image("my image:latest")

    def test_invalid_image_empty_string(self):
        with pytest.raises(ValueError):
            ContainerRuntime._validate_image("")

    def test_invalid_image_injection_attempt(self):
        with pytest.raises(ValueError):
            ContainerRuntime._validate_image("image; rm -rf /")


# ── TestNetworkModeValidation ─────────────────────────────────────────────────


class TestNetworkModeValidation:
    """Verify network_mode validation enforces the allowlist."""

    def test_valid_orchestrator_net(self):
        ContainerRuntime._validate_network_mode("orchestrator-net")  # no exception

    def test_valid_bridge(self):
        ContainerRuntime._validate_network_mode("bridge")  # no exception

    def test_valid_none(self):
        ContainerRuntime._validate_network_mode("none")  # no exception

    def test_valid_host(self):
        ContainerRuntime._validate_network_mode("host")  # no exception

    def test_invalid_custom_network(self):
        with pytest.raises(ValueError):
            ContainerRuntime._validate_network_mode("my-custom-network")

    def test_invalid_empty_string(self):
        with pytest.raises(ValueError):
            ContainerRuntime._validate_network_mode("")

    def test_invalid_injection_attempt(self):
        with pytest.raises(ValueError):
            ContainerRuntime._validate_network_mode("bridge; iptables -F")


# ── TestArtifactBridgeVolume ──────────────────────────────────────────────────


class TestArtifactBridgeVolume:
    """Verify run directory creation and path traversal prevention."""

    def test_creates_run_directory(self, tmp_path):
        workspace = tmp_path / "workspace"
        run_dir = ArtifactBridgeVolume.prepare_host_run_dir(workspace, "abc123def456")
        assert run_dir.exists()
        assert run_dir.is_dir()

    def test_run_dir_inside_workspace_runs(self, tmp_path):
        workspace = tmp_path / "workspace"
        run_dir = ArtifactBridgeVolume.prepare_host_run_dir(workspace, "abc123def456")
        expected = (workspace / "runs" / "abc123def456").resolve()
        assert run_dir == expected

    def test_raises_for_path_traversal_run_id(self, tmp_path):
        workspace = tmp_path / "workspace"
        with pytest.raises(ValueError):
            ArtifactBridgeVolume.prepare_host_run_dir(workspace, "../etc")

    def test_raises_for_run_id_with_slashes(self, tmp_path):
        workspace = tmp_path / "workspace"
        with pytest.raises(ValueError):
            ArtifactBridgeVolume.prepare_host_run_dir(workspace, "foo/bar")

    def test_raises_for_uppercase_run_id(self, tmp_path):
        workspace = tmp_path / "workspace"
        with pytest.raises(ValueError):
            ArtifactBridgeVolume.prepare_host_run_dir(workspace, "ABC123DEF456")

    def test_raises_for_too_short_run_id(self, tmp_path):
        workspace = tmp_path / "workspace"
        with pytest.raises(ValueError):
            ArtifactBridgeVolume.prepare_host_run_dir(workspace, "abc123")

    def test_raises_for_too_long_run_id(self, tmp_path):
        workspace = tmp_path / "workspace"
        with pytest.raises(ValueError):
            ArtifactBridgeVolume.prepare_host_run_dir(workspace, "abc123def456789")

    def test_raises_for_non_hex_run_id(self):
        with pytest.raises(ValueError):
            ArtifactBridgeVolume.prepare_host_run_dir(Path("/tmp"), "xyz123abc456")

    def test_raises_for_dotdot_traversal(self, tmp_path):
        workspace = tmp_path / "workspace"
        with pytest.raises(ValueError):
            ArtifactBridgeVolume.prepare_host_run_dir(workspace, "../../passwd")

    def test_build_mount_arg_format(self, tmp_path):
        host_dir = tmp_path / "workspace" / "runs" / "abc123def456"
        host_dir.mkdir(parents=True)
        mount_arg = ArtifactBridgeVolume.build_mount_arg(host_dir, "abc123def456")
        assert "type=bind" in mount_arg
        assert str(host_dir.resolve()) in mount_arg
        assert "abc123def456" in mount_arg

    def test_idempotent_directory_creation(self, tmp_path):
        workspace = tmp_path / "workspace"
        # Call twice — must not raise on second call
        ArtifactBridgeVolume.prepare_host_run_dir(workspace, "abc123def456")
        run_dir = ArtifactBridgeVolume.prepare_host_run_dir(workspace, "abc123def456")
        assert run_dir.exists()


# ── TestForbiddenNamesCheck ───────────────────────────────────────────────────


class TestForbiddenNamesCheck:
    """Verify post-exit scan detects hostile files in the bind-mounted output dir."""

    def test_forbidden_names_set_is_not_empty(self):
        """FORBIDDEN_NAMES must contain the known hostile dotfiles."""
        assert ".bashrc" in FORBIDDEN_NAMES
        assert ".ssh" in FORBIDDEN_NAMES

    def test_check_logs_warning_for_bashrc(self, tmp_path, caplog):
        import logging

        output_dir = tmp_path / "run_output"
        output_dir.mkdir()
        # Create a legitimate file and a hostile dotfile
        (output_dir / "prd.json").write_text('{"title": "test"}')
        (output_dir / ".bashrc").write_text("alias ls='rm -rf /'")

        with caplog.at_level(logging.WARNING):
            ContainerRuntime._check_forbidden_names(output_dir)

        # Should have logged a warning about .bashrc
        assert any(".bashrc" in record.message or "forbidden" in record.message.lower()
                   for record in caplog.records), \
            f"Expected warning about .bashrc but got: {[r.message for r in caplog.records]}"

    def test_check_does_not_warn_for_legitimate_files(self, tmp_path, caplog):
        import logging

        output_dir = tmp_path / "run_output"
        output_dir.mkdir()
        (output_dir / "prd.json").write_text('{"title": "test"}')
        (output_dir / "architecture.json").write_text('{}')

        with caplog.at_level(logging.WARNING):
            ContainerRuntime._check_forbidden_names(output_dir)

        # No forbidden-file warnings expected
        forbidden_warnings = [
            r for r in caplog.records
            if "forbidden" in r.message.lower() or ".bashrc" in r.message
        ]
        assert len(forbidden_warnings) == 0, \
            f"Unexpected forbidden warnings: {[r.message for r in forbidden_warnings]}"

    def test_check_handles_nonexistent_directory(self):
        """Must not raise if the output directory does not exist."""
        nonexistent = Path("/tmp/definitely-does-not-exist-xyz123")
        # Should complete without exception
        ContainerRuntime._check_forbidden_names(nonexistent)

    def test_check_detects_ssh_directory(self, tmp_path, caplog):
        import logging

        output_dir = tmp_path / "run_output"
        output_dir.mkdir()
        (output_dir / ".ssh").mkdir()

        with caplog.at_level(logging.WARNING):
            ContainerRuntime._check_forbidden_names(output_dir)

        assert any(".ssh" in record.message or "forbidden" in record.message.lower()
                   for record in caplog.records)


class TestResolveAnthropicIps:
    """Verify _resolve_anthropic_ips() IPv4 filtering and fallback behaviour.

    These tests exercise the logic added to close the IPv6 iptables bypass:
    the method must return only AF_INET addresses so that injected --add-host
    entries cannot circumvent IPv4-only iptables FORWARD rules.
    """

    def _make_addr_info(self, family: int, addr: str) -> tuple:
        """Return a minimal getaddrinfo result tuple (family, type, proto, canonname, sockaddr)."""
        return (family, socket.SOCK_STREAM, 0, "", (addr, 443))

    def test_returns_only_ipv4_when_mixed(self):
        """When getaddrinfo returns both IPv4 and IPv6, only IPv4 are kept."""
        mixed = [
            self._make_addr_info(socket.AF_INET, "1.2.3.4"),
            self._make_addr_info(socket.AF_INET6, "2001:db8::1"),
            self._make_addr_info(socket.AF_INET, "5.6.7.8"),
        ]
        with patch("socket.getaddrinfo", return_value=mixed):
            ips = ContainerRuntime._resolve_anthropic_ips()
        assert set(ips) == {"1.2.3.4", "5.6.7.8"}
        assert "2001:db8::1" not in ips

    def test_fallback_to_all_when_no_ipv4(self):
        """On an IPv6-only host (no AF_INET results), fall back to all addresses."""
        ipv6_only = [
            self._make_addr_info(socket.AF_INET6, "2001:db8::1"),
            self._make_addr_info(socket.AF_INET6, "2001:db8::2"),
        ]
        with patch("socket.getaddrinfo", return_value=ipv6_only):
            ips = ContainerRuntime._resolve_anthropic_ips()
        assert set(ips) == {"2001:db8::1", "2001:db8::2"}

    def test_returns_empty_on_resolution_failure(self):
        """DNS resolution failure must return [] gracefully (no exception propagated)."""
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("network unreachable")):
            ips = ContainerRuntime._resolve_anthropic_ips()
        assert ips == []

    def test_deduplicates_ips(self):
        """Duplicate IPs from multiple address families are collapsed."""
        duplicates = [
            self._make_addr_info(socket.AF_INET, "1.2.3.4"),
            self._make_addr_info(socket.AF_INET, "1.2.3.4"),
        ]
        with patch("socket.getaddrinfo", return_value=duplicates):
            ips = ContainerRuntime._resolve_anthropic_ips()
        assert ips.count("1.2.3.4") == 1


# ── TestValidateExtraTmpfs ────────────────────────────────────────────────────


class TestValidateExtraTmpfs:
    """Verify extra_tmpfs path allowlist prevents configuration injection attacks.

    The extra_tmpfs field accepts user-supplied mount paths.  A crafted path like
    /proc or /workspace/runs/abc123 could shadow kernel virtual filesystems or
    the artifact bind-mount, so paths must be restricted to an allowlist of
    safe prefixes.
    """

    def test_valid_home_orchestrator_path(self):
        """Paths under /home/orchestrator/ are permitted."""
        ContainerRuntime._validate_extra_tmpfs(["/home/orchestrator/cache"])

    def test_valid_tmp_subpath(self):
        """Paths under /tmp/ are permitted."""
        ContainerRuntime._validate_extra_tmpfs(["/tmp/scratch"])

    def test_valid_path_with_options(self):
        """Paths with mount options (size, mode, noexec) are permitted."""
        ContainerRuntime._validate_extra_tmpfs([
            "/home/orchestrator/cache:size=128m,noexec,nosuid,nodev",
            "/var/tmp/scratch:mode=1777,size=256m",
        ])

    def test_rejects_proc_filesystem(self):
        """Mounting tmpfs over /proc would hide kernel virtual filesystem — must be denied."""
        with pytest.raises(ValueError, match=r"allowed prefix"):
            ContainerRuntime._validate_extra_tmpfs(["/proc"])

    def test_rejects_sys_filesystem(self):
        with pytest.raises(ValueError, match=r"allowed prefix"):
            ContainerRuntime._validate_extra_tmpfs(["/sys"])

    def test_rejects_workspace_bind_mount_shadow(self):
        """Shadowing the artifact bind-mount path silently discards run output — must be denied."""
        with pytest.raises(ValueError, match=r"allowed prefix"):
            ContainerRuntime._validate_extra_tmpfs(["/workspace/runs/abc123def456"])

    def test_rejects_root_path(self):
        with pytest.raises(ValueError, match=r"allowed prefix"):
            ContainerRuntime._validate_extra_tmpfs(["/"])

    def test_rejects_etc_path(self):
        with pytest.raises(ValueError, match=r"allowed prefix"):
            ContainerRuntime._validate_extra_tmpfs(["/etc"])

    def test_rejects_dotdot_traversal(self):
        """Path traversal via .. must be rejected."""
        with pytest.raises(ValueError, match=r"\.\.|allowed"):
            ContainerRuntime._validate_extra_tmpfs(["/home/orchestrator/../../../etc"])

    def test_rejects_null_byte_in_path(self):
        with pytest.raises(ValueError, match=r"null bytes"):
            ContainerRuntime._validate_extra_tmpfs(["/home/orchestrator/\x00evil"])

    def test_rejects_shell_metacharacters_in_options(self):
        """Shell metacharacters in mount options could be injection vectors."""
        with pytest.raises(ValueError, match=r"characters outside"):
            ContainerRuntime._validate_extra_tmpfs([
                "/home/orchestrator/cache:size=128m;rm -rf /"
            ])

    def test_empty_list_is_valid(self):
        """Empty extra_tmpfs list must not raise."""
        ContainerRuntime._validate_extra_tmpfs([])  # no exception

    def test_build_run_args_rejects_invalid_extra_tmpfs(self, tmp_path):
        """build_run_args must call _validate_extra_tmpfs and raise on invalid paths."""
        config = _make_config(extra_tmpfs=["/proc"])
        host_run_dir = tmp_path / "workspace" / "runs" / "abc123def456"
        host_run_dir.mkdir(parents=True)
        with patch.object(ContainerRuntime, "_resolve_anthropic_ips", return_value=[]):
            with pytest.raises(ValueError, match=r"allowed prefix"):
                ContainerRuntime.build_run_args(
                    run_id="abc123def456",
                    feature_request="test",
                    config=config,
                    host_run_dir=host_run_dir,
                )


# ── TestForbiddenNamesRecursive ───────────────────────────────────────────────


class TestForbiddenNamesRecursive:
    """Verify that _check_forbidden_names scans subdirectories recursively.

    A shallow top-level scan can be evaded by placing hostile files inside
    subdirectories (e.g. artifacts/.ssh/).  The check must use rglob to
    detect forbidden content at any depth.
    """

    def test_detects_bashrc_in_subdirectory(self, tmp_path, caplog):
        import logging

        output_dir = tmp_path / "run_output"
        output_dir.mkdir()
        # Place .bashrc in a subdirectory — evades iterdir() shallow scan
        sub = output_dir / "artifacts" / "hidden"
        sub.mkdir(parents=True)
        (sub / ".bashrc").write_text("alias ls='rm -rf /'")

        with caplog.at_level(logging.WARNING):
            ContainerRuntime._check_forbidden_names(output_dir)

        assert any(".bashrc" in r.message or "forbidden" in r.message.lower()
                   for r in caplog.records), \
            "Recursive scan must detect .bashrc in subdirectory"

    def test_detects_ssh_in_subdirectory(self, tmp_path, caplog):
        import logging

        output_dir = tmp_path / "run_output"
        output_dir.mkdir()
        nested = output_dir / "level1" / "level2" / "level3"
        nested.mkdir(parents=True)
        (nested / ".ssh").mkdir()

        with caplog.at_level(logging.WARNING):
            ContainerRuntime._check_forbidden_names(output_dir)

        assert any(".ssh" in r.message or "forbidden" in r.message.lower()
                   for r in caplog.records)
