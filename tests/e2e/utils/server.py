"""DashboardTestServer — subprocess-based dashboard server fixture for E2E tests.

Uses a real uvicorn subprocess so Playwright's Chromium browser can make genuine
HTTP requests (TestClient / AsyncClient cannot serve real browser connections).

Usage:
    server = DashboardTestServer(workspace=Path("workspace"))
    host, port = await server.start()
    # ... run tests ...
    await server.stop()
"""

from __future__ import annotations

import asyncio
import atexit
import socket
import subprocess
import sys
import time
from pathlib import Path


def find_free_port() -> int:
    """Find an available TCP port on localhost (avoids race conditions)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DashboardTestServer:
    """Manage a dashboard FastAPI server subprocess for E2E testing.

    The server is started once per pytest session (session-scoped fixture) so
    startup overhead (~1-2 s) is paid only once across all page tests, keeping
    the full suite well under the 5-minute budget.

    Health is polled via GET /healthz until HTTP 200 is received (or timeout).

    Args:
        workspace:  Path to the orchestrator workspace directory.
                    Created automatically if it does not exist.
                    Defaults to a temporary workspace dir.
        port:       TCP port to bind to.  If None, a free port is chosen
                    automatically to avoid conflicts.
        host:       Interface to bind to (default ``127.0.0.1``).
    """

    def __init__(
        self,
        workspace: Path | None = None,
        port: int | None = None,
        host: str = "127.0.0.1",
    ) -> None:
        self.workspace = (workspace or Path("workspace")).resolve()
        self.port = port if port is not None else find_free_port()
        self.host = host
        self._proc: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        """Base URL for the running dashboard."""
        return f"http://{self.host}:{self.port}"

    async def start(self) -> tuple[str, int]:
        """Start the dashboard server and wait until it is healthy.

        Returns:
            Tuple of (host, port) for the running server.

        Raises:
            RuntimeError: If the server does not become healthy within 15 seconds.
        """
        self.workspace.mkdir(parents=True, exist_ok=True)

        # Build the Python inline command so we don't depend on the
        # orchestrate-dashboard CLI being on PATH (works in editable installs too).
        startup_script = (
            "import uvicorn, sys\n"
            "from pathlib import Path\n"
            "from orchestrator.dashboard.app import create_app\n"
            f"workspace = Path({str(self.workspace)!r}).resolve()\n"
            "workspace.mkdir(parents=True, exist_ok=True)\n"
            "app = create_app(workspace, workspace.name)\n"
            f"uvicorn.run(app, host={self.host!r}, port={self.port},\n"
            "            log_level='warning', access_log=False)\n"
        )

        self._proc = subprocess.Popen(
            [sys.executable, "-c", startup_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Register atexit handler so the subprocess is killed on KeyboardInterrupt
        # or unexpected Python exit (e.g. pytest -x with --forked).
        atexit.register(self._atexit_cleanup)

        healthy = await self.wait_healthy(timeout=15.0)
        if not healthy:
            stderr_output = b""
            if self._proc.stderr:
                # Read non-blocking snippet for diagnostics
                import os
                import fcntl
                flags = fcntl.fcntl(self._proc.stderr, fcntl.F_GETFL)
                fcntl.fcntl(self._proc.stderr, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                try:
                    stderr_output = self._proc.stderr.read() or b""
                except Exception:
                    pass
            await self.stop()
            raise RuntimeError(
                f"Dashboard server did not become healthy within 15 s on "
                f"{self.base_url}. "
                f"stderr: {stderr_output.decode(errors='replace')[-2000:]}"
            )

        return self.host, self.port

    async def wait_healthy(self, timeout: float = 15.0) -> bool:
        """Poll GET /healthz until HTTP 200 or timeout expires.

        Returns:
            True if the server is healthy, False if the timeout elapsed.
        """
        try:
            import httpx
        except ImportError:
            # Fall back to stdlib if httpx not available
            import urllib.request as _req

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    resp = _req.urlopen(f"{self.base_url}/healthz", timeout=2)
                    if resp.status == 200:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.25)
            return False

        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.monotonic() < deadline:
                try:
                    r = await client.get(f"{self.base_url}/healthz")
                    if r.status_code == 200:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.25)
        return False

    async def stop(self) -> None:
        """Gracefully terminate the server subprocess."""
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None

    def _atexit_cleanup(self) -> None:
        """Synchronous cleanup called by atexit handler on interpreter exit.

        This is a safety net for cases where the async teardown is not reached
        (e.g. pytest -x exits on first failure before session teardown).
        """
        if self._proc is not None and self._proc.poll() is None:
            # Process is still running — terminate it synchronously
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
