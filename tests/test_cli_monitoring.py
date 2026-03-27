"""Tests for orchestrate-monitoring CLI (TASK-016).

Acceptance criteria covered:
  1. orchestrate-monitoring CLI exists with start, stop, status, reset commands.
  2. start executes docker compose up -d with WORKSPACE_ROOT env var.
  3. stop executes docker compose down.
  4. reset executes docker compose down -v.
  5. status checks all 5 services (Prometheus, Grafana, Jaeger, Loki, Promtail).
  6. Both subprocess calls use shell=False (no shell=True).
  7. WORKSPACE_ROOT validation rejects paths with shell metacharacters.
  8. Promtail health checked via docker inspect (container state).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_compose_file(tmp_path: Path) -> Path:
    """Create a minimal docker-compose.monitoring.yml for path resolution."""
    compose_dir = tmp_path / "infra" / "docker"
    compose_dir.mkdir(parents=True)
    compose_file = compose_dir / "docker-compose.monitoring.yml"
    compose_file.write_text("# stub\n")
    return compose_file


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


class TestImport:
    def test_main_importable(self):
        from orchestrator.monitoring.cli import main  # noqa: F401

    def test_alias_importable(self):
        """cli_monitoring.py delegates to monitoring.cli."""
        from orchestrator.cli_monitoring import main  # noqa: F401

    def test_all_commands_present_in_argparse(self, tmp_path):
        from orchestrator.monitoring.cli import main

        # Calling with --help raises SystemExit(0); we just check parsing
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# _validate_workspace_root
# ---------------------------------------------------------------------------


class TestValidateWorkspaceRoot:
    def _validate(self, path: str):
        from orchestrator.monitoring.cli import _validate_workspace_root
        return _validate_workspace_root(path)

    @pytest.mark.parametrize("valid_path", [
        "/home/user/workspace",
        "/tmp/my-workspace",
        "workspace",
        "/Users/foo/project/workspace",
        "/path/with/numbers123",
    ])
    def test_valid_paths_pass(self, valid_path):
        result = self._validate(valid_path)
        assert result == valid_path

    @pytest.mark.parametrize("bad_path", [
        "/workspace; rm -rf /",
        "/workspace | cat /etc/passwd",
        "/workspace$(evil)",
        "/workspace`cmd`",
        "/workspace && bad",
        "/workspace > /etc/shadow",
        "/workspace < /dev/null",
        "/path/with spaces'and'quotes",
        '/path/with"double"quotes',
        "/path/with\\backslash",
        "/path/with\nnewline",
        "/path/with\ttab",
    ])
    def test_metacharacters_rejected(self, bad_path):
        with pytest.raises(SystemExit) as exc_info:
            self._validate(bad_path)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# cmd_start
# ---------------------------------------------------------------------------


class TestCmdStart:
    def test_start_calls_compose_up_d(self, tmp_path):
        from orchestrator.monitoring.cli import cmd_start

        compose_file = _fake_compose_file(tmp_path)
        with patch("orchestrator.monitoring.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch.dict("os.environ", {"WORKSPACE_ROOT": str(tmp_path / "ws")}, clear=False):
                rc = cmd_start(compose_file)

        assert rc == 0
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "docker" in cmd
        assert "compose" in cmd
        assert "up" in cmd
        assert "-d" in cmd
        assert str(compose_file) in cmd

    def test_start_passes_workspace_root_env(self, tmp_path):
        from orchestrator.monitoring.cli import cmd_start

        compose_file = _fake_compose_file(tmp_path)
        workspace = str(tmp_path / "my-workspace")

        with patch("orchestrator.monitoring.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch.dict("os.environ", {"WORKSPACE_ROOT": workspace}, clear=False):
                cmd_start(compose_file)

        env_passed = mock_run.call_args[1]["env"]
        assert env_passed["WORKSPACE_ROOT"] == workspace

    def test_start_uses_shell_false(self, tmp_path):
        """subprocess.run must not be called with shell=True."""
        from orchestrator.monitoring.cli import cmd_start

        compose_file = _fake_compose_file(tmp_path)
        with patch("orchestrator.monitoring.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch.dict("os.environ", {"WORKSPACE_ROOT": str(tmp_path)}, clear=False):
                cmd_start(compose_file)

        kwargs = mock_run.call_args[1]
        assert kwargs.get("shell", False) is False

    def test_start_rejects_metachar_workspace_root(self, tmp_path):
        from orchestrator.monitoring.cli import cmd_start

        compose_file = _fake_compose_file(tmp_path)
        with patch.dict("os.environ", {"WORKSPACE_ROOT": "/evil; rm -rf /"}, clear=False):
            with pytest.raises(SystemExit) as exc_info:
                cmd_start(compose_file)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# cmd_stop
# ---------------------------------------------------------------------------


class TestCmdStop:
    def test_stop_calls_compose_down(self, tmp_path):
        from orchestrator.monitoring.cli import cmd_stop

        compose_file = _fake_compose_file(tmp_path)
        with patch("orchestrator.monitoring.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            rc = cmd_stop(compose_file)

        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "down" in cmd
        assert "-v" not in cmd  # volumes retained

    def test_stop_uses_shell_false(self, tmp_path):
        from orchestrator.monitoring.cli import cmd_stop

        compose_file = _fake_compose_file(tmp_path)
        with patch("orchestrator.monitoring.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            cmd_stop(compose_file)

        kwargs = mock_run.call_args[1]
        assert kwargs.get("shell", False) is False


# ---------------------------------------------------------------------------
# cmd_reset
# ---------------------------------------------------------------------------


class TestCmdReset:
    def test_reset_calls_compose_down_v(self, tmp_path):
        from orchestrator.monitoring.cli import cmd_reset

        compose_file = _fake_compose_file(tmp_path)
        with patch("orchestrator.monitoring.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            rc = cmd_reset(compose_file)

        assert rc == 0
        cmd = mock_run.call_args[0][0]
        assert "down" in cmd
        assert "-v" in cmd


# ---------------------------------------------------------------------------
# cmd_status — all 5 services
# ---------------------------------------------------------------------------


class TestCmdStatus:
    def test_status_checks_all_5_services(self, tmp_path, capsys):
        """status must report on all 5 services including Promtail."""
        from orchestrator.monitoring.cli import cmd_status

        compose_file = _fake_compose_file(tmp_path)

        # docker compose ps returns 0
        # _health_check returns True for all HTTP endpoints
        # _check_container_running returns True for Promtail
        with (
            patch("orchestrator.monitoring.cli.subprocess.run") as mock_run,
            patch("orchestrator.monitoring.cli._health_check", return_value=True),
            patch("orchestrator.monitoring.cli._check_container_running", return_value=True),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            cmd_status(compose_file)

        out = capsys.readouterr().out
        # All 5 service names must appear
        assert "Prometheus" in out
        assert "Grafana" in out
        assert "Jaeger" in out
        assert "Loki" in out
        assert "Promtail" in out

    def test_status_promtail_unhealthy(self, tmp_path, capsys):
        """When Promtail container is not running, status output should say not running."""
        from orchestrator.monitoring.cli import cmd_status

        compose_file = _fake_compose_file(tmp_path)
        with (
            patch("orchestrator.monitoring.cli.subprocess.run") as mock_run,
            patch("orchestrator.monitoring.cli._health_check", return_value=True),
            patch("orchestrator.monitoring.cli._check_container_running", return_value=False),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            cmd_status(compose_file)

        out = capsys.readouterr().out
        assert "Promtail" in out
        assert "not running" in out

    def test_status_4_http_services_checked(self, tmp_path):
        """_health_check is called exactly 4 times (the 4 HTTP services)."""
        from orchestrator.monitoring.cli import cmd_status

        compose_file = _fake_compose_file(tmp_path)
        with (
            patch("orchestrator.monitoring.cli.subprocess.run") as mock_run,
            patch("orchestrator.monitoring.cli._health_check", return_value=True) as mock_health,
            patch("orchestrator.monitoring.cli._check_container_running", return_value=True),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            cmd_status(compose_file)

        assert mock_health.call_count == 4

    def test_status_promtail_checked_via_docker_inspect(self, tmp_path):
        """Promtail is checked via _check_container_running, not _health_check."""
        from orchestrator.monitoring.cli import cmd_status

        compose_file = _fake_compose_file(tmp_path)
        with (
            patch("orchestrator.monitoring.cli.subprocess.run") as mock_run,
            patch("orchestrator.monitoring.cli._health_check", return_value=True),
            patch(
                "orchestrator.monitoring.cli._check_container_running",
                return_value=True,
            ) as mock_container,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            cmd_status(compose_file)

        mock_container.assert_called_once_with("orchestrator-promtail")


# ---------------------------------------------------------------------------
# _check_container_running
# ---------------------------------------------------------------------------


class TestCheckContainerRunning:
    def test_running_container_returns_true(self):
        from orchestrator.monitoring.cli import _check_container_running

        with patch("orchestrator.monitoring.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="true\n")
            result = _check_container_running("orchestrator-promtail")

        assert result is True

    def test_stopped_container_returns_false(self):
        from orchestrator.monitoring.cli import _check_container_running

        with patch("orchestrator.monitoring.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="false\n")
            result = _check_container_running("orchestrator-promtail")

        assert result is False

    def test_nonexistent_container_returns_false(self):
        from orchestrator.monitoring.cli import _check_container_running

        with patch("orchestrator.monitoring.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = _check_container_running("orchestrator-promtail")

        assert result is False

    def test_oserror_returns_false(self):
        from orchestrator.monitoring.cli import _check_container_running

        with patch("orchestrator.monitoring.cli.subprocess.run", side_effect=OSError):
            result = _check_container_running("orchestrator-promtail")

        assert result is False

    def test_uses_shell_false(self):
        """docker inspect must be called with shell=False."""
        from orchestrator.monitoring.cli import _check_container_running

        with patch("orchestrator.monitoring.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="true\n")
            _check_container_running("orchestrator-promtail")

        kwargs = mock_run.call_args[1]
        assert kwargs.get("shell", False) is False


# ---------------------------------------------------------------------------
# main() dispatch
# ---------------------------------------------------------------------------


class TestMainDispatch:
    def test_no_command_prints_help(self, capsys):
        from orchestrator.monitoring.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 0

    def test_unknown_command_exits_nonzero(self):
        from orchestrator.monitoring.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main(["bogus"])
        assert exc_info.value.code != 0

    def test_status_command_dispatched(self, tmp_path):
        from orchestrator.monitoring.cli import main

        compose_file = _fake_compose_file(tmp_path)
        with (
            patch("orchestrator.monitoring.cli._find_compose_file", return_value=compose_file),
            patch("orchestrator.monitoring.cli.cmd_status", return_value=0) as mock_status,
            pytest.raises(SystemExit),
        ):
            main(["status"])

        mock_status.assert_called_once_with(compose_file)

    def test_start_command_dispatched(self, tmp_path):
        from orchestrator.monitoring.cli import main

        compose_file = _fake_compose_file(tmp_path)
        with (
            patch("orchestrator.monitoring.cli._find_compose_file", return_value=compose_file),
            patch("orchestrator.monitoring.cli.cmd_start", return_value=0) as mock_start,
            pytest.raises(SystemExit),
        ):
            main(["start"])

        mock_start.assert_called_once_with(compose_file)

    def test_missing_compose_file_exits_1(self):
        from orchestrator.monitoring.cli import main

        with (
            patch(
                "orchestrator.monitoring.cli._find_compose_file",
                side_effect=FileNotFoundError("not found"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["start"])

        assert exc_info.value.code == 1
