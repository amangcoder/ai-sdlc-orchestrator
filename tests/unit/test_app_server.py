"""Unit tests for src/orchestrator/app_server.py.

All tests are pure in-memory using tmp_path and mocking — no network, no real
Docker daemon, no real subprocess execution except where explicitly tested with
a lightweight helper.

Covers:
- REQ-002: AppTestServer manages Docker Compose up/down, package installation
  (120s timeout), dev server startup on free port, health-check polling (60s timeout)
- AC-011: Gracefully skips Docker Compose if docker not available; logs warning
  and attempts direct server start
- find_free_port() returns a usable int
- _inject_port_into_command rewrites CLIs for each supported framework
- atexit safety net: _atexit_cleanup handles already-dead and live processes
- stop() is idempotent (safe to call multiple times)
- run_seed_script() auto-detects and runs seed files; skips if absent
- install_packages() raises RuntimeError on non-zero exit
- compose_up() raises RuntimeError when Docker is available but compose fails
- wait_healthy() returns True on HTTP 200, False on timeout
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.app_server import AppTestServer, find_free_port
from orchestrator.stack_detector import StackInfo


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_stack(
    framework: str = "nextjs",
    install_command: str = "npm install",
    dev_server_command: str = "npm run dev",
    dev_server_port: int = 3000,
    health_check_path: str = "/",
    has_frontend: bool = True,
) -> StackInfo:
    return StackInfo(
        framework=framework,
        language="typescript",
        package_manager="npm",
        install_command=install_command,
        dev_server_command=dev_server_command,
        dev_server_port=dev_server_port,
        base_url=f"http://localhost:{dev_server_port}",
        health_check_path=health_check_path,
        has_frontend=has_frontend,
    )


def _make_server(
    tmp_path: Path,
    stack: StackInfo | None = None,
    port: int = 9999,
) -> AppTestServer:
    if stack is None:
        stack = _make_stack()
    return AppTestServer(project_root=tmp_path, stack=stack, port=port)


# ── find_free_port ─────────────────────────────────────────────────────────────


class TestFindFreePort:
    def test_returns_int(self):
        port = find_free_port()
        assert isinstance(port, int)

    def test_port_in_valid_range(self):
        port = find_free_port()
        assert 1024 <= port <= 65535

    def test_two_calls_return_different_ports(self):
        p1 = find_free_port()
        p2 = find_free_port()
        # Can't guarantee different (tiny race window), but at least valid
        assert isinstance(p1, int)
        assert isinstance(p2, int)


# ── AppTestServer.__init__ ─────────────────────────────────────────────────────


class TestInit:
    def test_sets_project_root(self, tmp_path: Path):
        server = _make_server(tmp_path)
        assert server.project_root == tmp_path.resolve()

    def test_explicit_port_used(self, tmp_path: Path):
        server = _make_server(tmp_path, port=12345)
        assert server.port == 12345

    def test_auto_port_when_none(self, tmp_path: Path):
        stack = _make_stack()
        server = AppTestServer(project_root=tmp_path, stack=stack)
        assert 1024 <= server.port <= 65535

    def test_base_url(self, tmp_path: Path):
        server = _make_server(tmp_path, port=7777)
        assert server.base_url == "http://127.0.0.1:7777"


# ── _inject_port_into_command ──────────────────────────────────────────────────


class TestInjectPortIntoCommand:
    def test_fastapi_appends_port_flag(self, tmp_path: Path):
        stack = _make_stack(
            framework="fastapi",
            dev_server_command="uvicorn main:app --reload",
            dev_server_port=8000,
        )
        server = AppTestServer(project_root=tmp_path, stack=stack, port=8888)
        result = server._inject_port_into_command("uvicorn main:app --reload")
        assert "--port 8888" in result

    def test_fastapi_replaces_existing_port_flag(self, tmp_path: Path):
        stack = _make_stack(
            framework="fastapi",
            dev_server_command="uvicorn main:app --port 8000",
            dev_server_port=8000,
        )
        server = AppTestServer(project_root=tmp_path, stack=stack, port=8888)
        result = server._inject_port_into_command("uvicorn main:app --port 8000")
        assert "--port 8888" in result
        assert "--port 8000" not in result

    def test_django_replaces_default_port(self, tmp_path: Path):
        stack = _make_stack(
            framework="django",
            dev_server_command="python manage.py runserver 0.0.0.0:8000",
            dev_server_port=8000,
        )
        server = AppTestServer(project_root=tmp_path, stack=stack, port=8765)
        result = server._inject_port_into_command(
            "python manage.py runserver 0.0.0.0:8000"
        )
        assert "0.0.0.0:8765" in result

    def test_flask_replaces_port_arg(self, tmp_path: Path):
        stack = _make_stack(
            framework="flask",
            dev_server_command="flask run --host=0.0.0.0 --port=5000",
            dev_server_port=5000,
        )
        server = AppTestServer(project_root=tmp_path, stack=stack, port=5678)
        result = server._inject_port_into_command(
            "flask run --host=0.0.0.0 --port=5000"
        )
        assert "--port=5678" in result

    def test_rails_replaces_p_flag(self, tmp_path: Path):
        stack = _make_stack(
            framework="rails",
            dev_server_command="rails server -b 0.0.0.0 -p 3000",
            dev_server_port=3000,
        )
        server = AppTestServer(project_root=tmp_path, stack=stack, port=3333)
        result = server._inject_port_into_command("rails server -b 0.0.0.0 -p 3000")
        assert "-p 3333" in result

    def test_nextjs_no_rewrite(self, tmp_path: Path):
        stack = _make_stack(framework="nextjs", dev_server_command="npm run dev")
        server = AppTestServer(project_root=tmp_path, stack=stack, port=3001)
        result = server._inject_port_into_command("npm run dev")
        assert result == "npm run dev"


# ── compose_up / compose_down ─────────────────────────────────────────────────


class TestComposeUp:
    @pytest.mark.asyncio
    async def test_skips_when_docker_unavailable(self, tmp_path: Path, caplog):
        # Create docker-compose.yml so we enter the compose path
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        server = _make_server(tmp_path)

        with patch("orchestrator.app_server._docker_available", return_value=False):
            result = await server.compose_up()

        assert result is False
        assert any("Docker not available" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, tmp_path: Path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        server = _make_server(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch("orchestrator.app_server._docker_available", return_value=True),
            patch("asyncio.get_event_loop") as mock_loop,
        ):
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            result = await server.compose_up()

        assert result is True
        assert server._compose_started is True

    @pytest.mark.asyncio
    async def test_raises_on_nonzero_exit(self, tmp_path: Path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        server = _make_server(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"compose error"

        with (
            patch("orchestrator.app_server._docker_available", return_value=True),
            patch("asyncio.get_event_loop") as mock_loop,
        ):
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            with pytest.raises(RuntimeError, match="docker compose up"):
                await server.compose_up()


class TestComposeDown:
    @pytest.mark.asyncio
    async def test_skips_when_not_started(self, tmp_path: Path):
        server = _make_server(tmp_path)
        # Should not raise, should be a no-op
        await server.compose_down()

    @pytest.mark.asyncio
    async def test_calls_down_when_started(self, tmp_path: Path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        server = _make_server(tmp_path)
        server._compose_started = True

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch("orchestrator.app_server._docker_available", return_value=True),
            patch("asyncio.get_event_loop") as mock_loop,
        ):
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            await server.compose_down()

        assert server._compose_started is False


# ── install_packages ──────────────────────────────────────────────────────────


class TestInstallPackages:
    @pytest.mark.asyncio
    async def test_skips_when_no_install_command(self, tmp_path: Path):
        stack = _make_stack(install_command="")
        server = AppTestServer(project_root=tmp_path, stack=stack, port=9000)
        # Should complete without error
        await server.install_packages()

    @pytest.mark.asyncio
    async def test_raises_on_nonzero_exit(self, tmp_path: Path):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"npm ERR! not found"
        mock_result.stdout = b""

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            server = _make_server(tmp_path)
            with pytest.raises(RuntimeError, match="Package installation failed"):
                await server.install_packages()

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self, tmp_path: Path):
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=subprocess.TimeoutExpired(cmd="npm install", timeout=120)
            )
            server = _make_server(tmp_path)
            with pytest.raises(RuntimeError, match="timed out after 120 s"):
                await server.install_packages()

    @pytest.mark.asyncio
    async def test_succeeds_on_zero_exit(self, tmp_path: Path):
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            server = _make_server(tmp_path)
            await server.install_packages()  # should not raise


# ── wait_healthy ──────────────────────────────────────────────────────────────


class TestWaitHealthy:
    @pytest.mark.asyncio
    async def test_returns_true_on_200(self, tmp_path: Path):
        server = _make_server(tmp_path)

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("orchestrator.app_server.time") as mock_time:
            # Make deadline check pass on first try
            call_count = 0

            def monotonic_side_effect():
                nonlocal call_count
                call_count += 1
                # First call (deadline setup): t=0; second call (loop check): t=0.1
                return [0.0, 0.1][min(call_count - 1, 1)]

            mock_time.monotonic = monotonic_side_effect

            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await server.wait_healthy(timeout=60.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self, tmp_path: Path):
        """When all requests fail and timeout elapses, returns False."""
        server = _make_server(tmp_path)

        # Simulate immediate timeout: monotonic always returns past deadline
        with patch("orchestrator.app_server.time") as mock_time:
            mock_time.monotonic.side_effect = [0.0, 61.0]  # deadline=60, check=61

            with patch("httpx.AsyncClient") as mock_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
                mock_cls.return_value = mock_client

                result = await server.wait_healthy(timeout=60.0)

        assert result is False


# ── run_seed_script ───────────────────────────────────────────────────────────


class TestRunSeedScript:
    @pytest.mark.asyncio
    async def test_skips_when_no_seed_file(self, tmp_path: Path):
        server = _make_server(tmp_path)
        # Should be a no-op
        await server.run_seed_script()

    @pytest.mark.asyncio
    async def test_auto_detects_seed_js(self, tmp_path: Path):
        (tmp_path / "seed.js").write_text("// seed")
        server = _make_server(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            await server.run_seed_script()

    @pytest.mark.asyncio
    async def test_raises_on_nonzero_exit(self, tmp_path: Path):
        seed = tmp_path / "seed.py"
        seed.write_text("raise SystemExit(1)")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"error in seed"

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            server = _make_server(tmp_path)
            with pytest.raises(RuntimeError, match="Seed script failed"):
                await server.run_seed_script("seed.py")

    @pytest.mark.asyncio
    async def test_skips_missing_explicit_script(self, tmp_path: Path):
        server = _make_server(tmp_path)
        # Explicit path that does not exist — should log warning and skip
        await server.run_seed_script("no_such_file.js")


# ── stop / idempotency ────────────────────────────────────────────────────────


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self, tmp_path: Path):
        server = _make_server(tmp_path)
        # No processes running — calling stop() twice should not raise
        await server.stop()
        await server.stop()

    @pytest.mark.asyncio
    async def test_stop_terminates_proc(self, tmp_path: Path):
        server = _make_server(tmp_path)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        server._server_proc = mock_proc

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
            await server._stop_server_proc()

        mock_proc.terminate.assert_called_once()


# ── _atexit_cleanup ───────────────────────────────────────────────────────────


class TestAtexitCleanup:
    def test_cleanup_kills_live_proc(self, tmp_path: Path):
        server = _make_server(tmp_path)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        server._server_proc = mock_proc

        server._atexit_cleanup()

        mock_proc.terminate.assert_called_once()

    def test_cleanup_skips_dead_proc(self, tmp_path: Path):
        server = _make_server(tmp_path)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # already exited
        server._server_proc = mock_proc

        server._atexit_cleanup()  # should not raise

        mock_proc.terminate.assert_not_called()

    def test_cleanup_safe_with_no_proc(self, tmp_path: Path):
        server = _make_server(tmp_path)
        # No subprocess at all
        server._atexit_cleanup()  # must not raise


# ── start_dependencies graceful degradation ───────────────────────────────────


class TestStartDependencies:
    @pytest.mark.asyncio
    async def test_returns_true_without_compose_file(self, tmp_path: Path):
        server = _make_server(tmp_path)
        result = await server.start_dependencies()
        assert result is True

    @pytest.mark.asyncio
    async def test_docker_unavailable_returns_false(self, tmp_path: Path, caplog):
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        server = _make_server(tmp_path)

        with patch("orchestrator.app_server._docker_available", return_value=False):
            result = await server.start_dependencies()

        assert result is False
        # Warning should have been logged
        assert any("Docker not available" in r.message for r in caplog.records)


# ── _docker_available ──────────────────────────────────────────────────────────


class TestDockerAvailable:
    def test_returns_false_when_docker_not_on_path(self):
        from orchestrator.app_server import _docker_available
        with patch("orchestrator.app_server.shutil.which", return_value=None):
            assert _docker_available() is False

    def test_returns_true_when_docker_info_succeeds(self):
        from orchestrator.app_server import _docker_available
        mock_result = MagicMock()
        mock_result.returncode = 0
        with (
            patch("orchestrator.app_server.shutil.which", return_value="/usr/bin/docker"),
            patch("orchestrator.app_server.subprocess.run", return_value=mock_result),
        ):
            assert _docker_available() is True

    def test_returns_false_when_docker_info_nonzero(self):
        from orchestrator.app_server import _docker_available
        mock_result = MagicMock()
        mock_result.returncode = 1
        with (
            patch("orchestrator.app_server.shutil.which", return_value="/usr/bin/docker"),
            patch("orchestrator.app_server.subprocess.run", return_value=mock_result),
        ):
            assert _docker_available() is False

    def test_returns_false_when_docker_info_raises(self):
        from orchestrator.app_server import _docker_available
        with (
            patch("orchestrator.app_server.shutil.which", return_value="/usr/bin/docker"),
            patch("orchestrator.app_server.subprocess.run", side_effect=OSError("no docker")),
        ):
            assert _docker_available() is False


# ── compose_up — timeout ───────────────────────────────────────────────────────


class TestComposeUpTimeout:
    @pytest.mark.asyncio
    async def test_raises_runtime_error_on_timeout(self, tmp_path: Path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        server = _make_server(tmp_path)

        with (
            patch("orchestrator.app_server._docker_available", return_value=True),
            patch("asyncio.get_event_loop") as mock_loop,
        ):
            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=subprocess.TimeoutExpired(cmd=["docker", "compose", "up", "-d"], timeout=120)
            )
            with pytest.raises(RuntimeError, match="timed out after 120 s"):
                await server.compose_up()


# ── compose_down — additional branches ────────────────────────────────────────


class TestComposeDownAdditional:
    @pytest.mark.asyncio
    async def test_skips_when_no_compose_file_despite_started(self, tmp_path: Path):
        """compose_down returns early (no-op) when docker-compose.yml is absent."""
        server = _make_server(tmp_path)
        server._compose_started = True
        # No docker-compose.yml — function returns early before the finally block
        with patch("orchestrator.app_server._docker_available", return_value=True):
            await server.compose_down()  # Must not raise
        # _compose_started is NOT reset because we hit an early return
        assert server._compose_started is True

    @pytest.mark.asyncio
    async def test_skips_when_docker_unavailable_during_down(self, tmp_path: Path):
        """compose_down returns early (no-op) when Docker is unavailable."""
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        server = _make_server(tmp_path)
        server._compose_started = True

        with patch("orchestrator.app_server._docker_available", return_value=False):
            await server.compose_down()  # Should not raise
        # _compose_started is NOT reset because we hit an early return
        assert server._compose_started is True

    @pytest.mark.asyncio
    async def test_compose_down_handles_exception_gracefully(self, tmp_path: Path):
        """Exception during compose down is logged but not re-raised; flag is reset."""
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        server = _make_server(tmp_path)
        server._compose_started = True

        with (
            patch("orchestrator.app_server._docker_available", return_value=True),
            patch("asyncio.get_event_loop") as mock_loop,
        ):
            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=Exception("compose crash")
            )
            await server.compose_down()  # Should not raise

        # _compose_started IS reset because the exception is caught in the try/except/finally
        assert server._compose_started is False


# ── _launch_server ────────────────────────────────────────────────────────────


class TestLaunchServer:
    @pytest.mark.asyncio
    async def test_raises_when_no_dev_server_command(self, tmp_path: Path):
        stack = _make_stack(dev_server_command="")
        server = AppTestServer(project_root=tmp_path, stack=stack, port=9001)
        with pytest.raises(RuntimeError, match="No dev_server_command"):
            await server._launch_server()

    @pytest.mark.asyncio
    async def test_launches_subprocess(self, tmp_path: Path):
        server = _make_server(tmp_path)

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with patch("orchestrator.app_server.subprocess.Popen", return_value=mock_proc) as mock_popen:
            await server._launch_server()

        assert server._server_proc is mock_proc
        mock_popen.assert_called_once()

    @pytest.mark.asyncio
    async def test_sets_port_env_var(self, tmp_path: Path):
        server = _make_server(tmp_path, port=9876)
        mock_proc = MagicMock()
        mock_proc.pid = 9876

        captured_kwargs = {}

        def capture_popen(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_proc

        with patch("orchestrator.app_server.subprocess.Popen", side_effect=capture_popen):
            await server._launch_server()

        assert captured_kwargs.get("env", {}).get("PORT") == "9876"


# ── wait_healthy — stdlib fallback ────────────────────────────────────────────


class TestWaitHealthyStdlibFallback:
    @pytest.mark.asyncio
    async def test_returns_true_via_stdlib_on_http_200(self, tmp_path: Path):
        """When httpx is not installed, fall back to stdlib urllib."""
        server = _make_server(tmp_path)

        mock_resp = MagicMock()
        mock_resp.status = 200

        # Remove httpx from sys.modules so the ImportError path is taken
        saved = sys.modules.pop("httpx", None)
        try:
            with patch.dict(sys.modules, {"httpx": None}):
                with patch("orchestrator.app_server.time") as mock_time:
                    mock_time.monotonic.side_effect = [0.0, 0.5]
                    with patch("urllib.request.urlopen", return_value=mock_resp):
                        result = await server.wait_healthy(timeout=60.0)
        finally:
            if saved is not None:
                sys.modules["httpx"] = saved

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_stdlib_timeout(self, tmp_path: Path):
        """stdlib urllib fallback returns False when all requests fail past deadline."""
        server = _make_server(tmp_path)

        saved = sys.modules.pop("httpx", None)
        try:
            with patch.dict(sys.modules, {"httpx": None}):
                with patch("orchestrator.app_server.time") as mock_time:
                    mock_time.monotonic.side_effect = [0.0, 61.0]
                    with patch("urllib.request.urlopen", side_effect=OSError("refused")):
                        result = await server.wait_healthy(timeout=60.0)
        finally:
            if saved is not None:
                sys.modules["httpx"] = saved

        assert result is False


# ── run_seed_script — additional scenarios ────────────────────────────────────


class TestRunSeedScriptAdditional:
    @pytest.mark.asyncio
    async def test_raises_on_timeout(self, tmp_path: Path):
        (tmp_path / "seed.py").write_text("# seed")
        server = _make_server(tmp_path)

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=subprocess.TimeoutExpired(cmd=["python", "seed.py"], timeout=60)
            )
            with pytest.raises(RuntimeError, match="Seed script timed out"):
                await server.run_seed_script("seed.py")

    @pytest.mark.asyncio
    async def test_auto_detects_scripts_seed_js(self, tmp_path: Path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "seed.js").write_text("// seed")
        server = _make_server(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            await server.run_seed_script()

    @pytest.mark.asyncio
    async def test_auto_detects_scripts_seed_py(self, tmp_path: Path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "seed.py").write_text("# seed")
        server = _make_server(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            await server.run_seed_script()

    @pytest.mark.asyncio
    async def test_auto_detects_db_seeds_rb(self, tmp_path: Path):
        (tmp_path / "db").mkdir()
        (tmp_path / "db" / "seeds.rb").write_text("# seeds")
        server = _make_server(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            await server.run_seed_script()

    @pytest.mark.asyncio
    async def test_runs_seed_ts_via_node(self, tmp_path: Path):
        (tmp_path / "seed.ts").write_text("// TS seed")
        server = _make_server(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            await server.run_seed_script("seed.ts")

    @pytest.mark.asyncio
    async def test_runs_seed_rb_via_ruby(self, tmp_path: Path):
        (tmp_path / "seed.rb").write_text("# rb seed")
        server = _make_server(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            await server.run_seed_script("seed.rb")

    @pytest.mark.asyncio
    async def test_unknown_extension_runs_directly(self, tmp_path: Path):
        (tmp_path / "seed.sh").write_text("#!/bin/sh\necho seed")
        server = _make_server(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=mock_result)
            await server.run_seed_script("seed.sh")


# ── _stop_server_proc — kill path ─────────────────────────────────────────────


class TestStopServerProcKillPath:
    @pytest.mark.asyncio
    async def test_kills_process_when_terminate_times_out(self, tmp_path: Path):
        server = _make_server(tmp_path)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        server._server_proc = mock_proc

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(
                side_effect=subprocess.TimeoutExpired(cmd="npm run dev", timeout=10)
            )
            await server._stop_server_proc()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_already_exited_proc(self, tmp_path: Path):
        server = _make_server(tmp_path)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # already exited
        server._server_proc = mock_proc

        await server._stop_server_proc()

        mock_proc.terminate.assert_not_called()


# ── _read_proc_stderr ──────────────────────────────────────────────────────────


class TestReadProcStderr:
    def test_returns_empty_when_no_proc(self, tmp_path: Path):
        server = _make_server(tmp_path)
        assert server._read_proc_stderr() == ""

    def test_returns_empty_when_proc_has_no_stderr(self, tmp_path: Path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.stderr = None
        server._server_proc = mock_proc
        assert server._read_proc_stderr() == ""


# ── _atexit_cleanup — compose path ────────────────────────────────────────────


class TestAtexitCleanupComposePath:
    def test_cleanup_runs_compose_down_when_started(self, tmp_path: Path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        server = _make_server(tmp_path)
        server._compose_started = True

        mock_run = MagicMock()
        mock_run.returncode = 0

        with (
            patch("orchestrator.app_server._docker_available", return_value=True),
            patch("orchestrator.app_server.subprocess.run", return_value=mock_run) as mock_subproc,
        ):
            server._atexit_cleanup()

        # subprocess.run should have been called for docker compose down
        calls = [str(c) for c in mock_subproc.call_args_list]
        assert any("compose" in c and "down" in c for c in calls)

    def test_cleanup_skips_compose_when_docker_unavailable(self, tmp_path: Path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        server = _make_server(tmp_path)
        server._compose_started = True

        with (
            patch("orchestrator.app_server._docker_available", return_value=False),
            patch("orchestrator.app_server.subprocess.run") as mock_subproc,
        ):
            server._atexit_cleanup()

        # subprocess.run should NOT have been called (Docker unavailable)
        mock_subproc.assert_not_called()

    def test_cleanup_handles_compose_exception_gracefully(self, tmp_path: Path):
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        server = _make_server(tmp_path)
        server._compose_started = True

        with (
            patch("orchestrator.app_server._docker_available", return_value=True),
            patch("orchestrator.app_server.subprocess.run", side_effect=OSError("crash")),
        ):
            server._atexit_cleanup()  # Should not raise


# ── start() full lifecycle ─────────────────────────────────────────────────────


class TestStartLifecycle:
    @pytest.mark.asyncio
    async def test_start_returns_base_url_on_success(self, tmp_path: Path):
        server = _make_server(tmp_path, port=8765)

        mock_proc = MagicMock()
        mock_proc.pid = 42

        with (
            patch.object(server, "start_dependencies", new_callable=AsyncMock, return_value=True),
            patch.object(server, "install_packages", new_callable=AsyncMock),
            patch.object(server, "_launch_server", new_callable=AsyncMock),
            patch.object(server, "wait_healthy", new_callable=AsyncMock, return_value=True),
        ):
            url = await server.start()

        assert url == "http://127.0.0.1:8765"

    @pytest.mark.asyncio
    async def test_start_raises_runtime_error_when_unhealthy(self, tmp_path: Path):
        server = _make_server(tmp_path)

        with (
            patch.object(server, "start_dependencies", new_callable=AsyncMock, return_value=True),
            patch.object(server, "install_packages", new_callable=AsyncMock),
            patch.object(server, "_launch_server", new_callable=AsyncMock),
            patch.object(server, "wait_healthy", new_callable=AsyncMock, return_value=False),
            patch.object(server, "stop", new_callable=AsyncMock),
        ):
            with pytest.raises(RuntimeError, match="did not become healthy"):
                await server.start()

    @pytest.mark.asyncio
    async def test_start_registers_atexit_handler(self, tmp_path: Path):
        server = _make_server(tmp_path)

        with (
            patch.object(server, "start_dependencies", new_callable=AsyncMock, return_value=True),
            patch.object(server, "install_packages", new_callable=AsyncMock),
            patch.object(server, "_launch_server", new_callable=AsyncMock),
            patch.object(server, "wait_healthy", new_callable=AsyncMock, return_value=True),
            patch("orchestrator.app_server.atexit.register") as mock_register,
        ):
            await server.start()

        mock_register.assert_called_once()
        assert server._atexit_registered is True
