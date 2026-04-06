"""AppTestServer — lifecycle manager for generated app dev servers.

Manages the full lifecycle of a generated application's dev server:

1. Docker Compose startup  (``docker compose up -d`` + health checks)
2. Package installation    (``npm install`` / ``pip install`` with 120 s timeout)
3. Dev server startup      (free-port allocation, subprocess launch)
4. Health-check polling    (HTTP 200 on ``health_check_path``, 60 s timeout)
5. Seed script execution   (optional seed script after server is healthy)
6. Graceful shutdown       (``stop()`` + ``compose_down()`` + atexit safety net)

Graceful degradation
--------------------
If Docker is not available on the host, ``compose_up()`` logs a warning and
returns ``False`` instead of raising.  ``start()`` then proceeds directly to
package installation and dev server startup so the QA Browser phase can still
run against a locally-started process.

Usage::

    from pathlib import Path
    from orchestrator.stack_detector import detect_stack
    from orchestrator.app_server import AppTestServer

    stack = detect_stack(Path("/path/to/generated/project"))
    server = AppTestServer(project_root=Path("/path/to/generated/project"), stack=stack)
    base_url = await server.start()
    # ... run QA ...
    await server.stop()
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Port helper (mirrors tests/e2e/utils/server.py for consistency) ───────────


def find_free_port() -> int:
    """Find an available TCP port on localhost (avoids race conditions)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Docker availability check ─────────────────────────────────────────────────


def _docker_available() -> bool:
    """Return True if the ``docker`` CLI is present and the daemon is running."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001
        return False


# ── AppTestServer ─────────────────────────────────────────────────────────────


class AppTestServer:
    """Manage the full dev-server lifecycle for a generated application.

    Args:
        project_root: Absolute path to the generated project directory.
            This is the directory that contains ``package.json`` /
            ``requirements.txt`` / ``docker-compose.yml``, etc.
        stack: A :class:`~orchestrator.stack_detector.StackInfo` instance
            (produced by :func:`~orchestrator.stack_detector.detect_stack`).
            Provides install command, dev-server command, default port, and
            health-check path.
        timeout: Overall timeout in seconds for the *start* sequence
            (compose up + package install + server health).  Defaults to 300 s.
        host: Interface the dev server should bind to.  Defaults to
            ``"127.0.0.1"``.
        port: TCP port for the dev server.  ``None`` means "pick a free port
            automatically."
    """

    def __init__(
        self,
        project_root: Path,
        stack,  # orchestrator.stack_detector.StackInfo (avoid circular import)
        timeout: float = 300.0,
        host: str = "127.0.0.1",
        port: Optional[int] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.stack = stack
        self.timeout = timeout
        self.host = host
        self.port: int = port if port is not None else find_free_port()

        self._server_proc: Optional[subprocess.Popen] = None
        self._compose_started: bool = False
        self._atexit_registered: bool = False

    # ── Public properties ─────────────────────────────────────────────────────

    @property
    def base_url(self) -> str:
        """Full base URL for the running dev server, e.g. ``http://127.0.0.1:3001``."""
        return f"http://{self.host}:{self.port}"

    # ── High-level start / stop ───────────────────────────────────────────────

    async def start(self) -> str:
        """Start the full server lifecycle.

        Sequence:
        1. ``start_dependencies()``  — Docker Compose (skipped if Docker absent)
        2. ``install_packages()``    — npm install / pip install (120 s timeout)
        3. Start dev server subprocess
        4. ``wait_healthy()``        — poll health endpoint (60 s timeout)

        Returns:
            The ``base_url`` of the running dev server.

        Raises:
            RuntimeError: If the server does not become healthy within the
                allocated timeout.
        """
        # Register safety-net atexit handler before doing anything that needs cleanup
        if not self._atexit_registered:
            atexit.register(self._atexit_cleanup)
            self._atexit_registered = True

        # Step 1: Docker Compose
        await self.start_dependencies()

        # Step 2: Install packages
        await self.install_packages()

        # Step 3: Launch dev server subprocess
        await self._launch_server()

        # Step 4: Wait until healthy
        healthy = await self.wait_healthy(timeout=60.0)
        if not healthy:
            stderr_snippet = self._read_proc_stderr()
            await self.stop()
            raise RuntimeError(
                f"Dev server did not become healthy within 60 s on {self.base_url}. "
                f"stderr: {stderr_snippet}"
            )

        logger.info("Dev server healthy at %s", self.base_url)
        return self.base_url

    async def stop(self) -> None:
        """Gracefully shut down the dev server and Docker Compose services.

        Safe to call multiple times (idempotent).
        """
        await self._stop_server_proc()
        await self.compose_down()

    # ── Dependencies (Docker Compose) ─────────────────────────────────────────

    async def start_dependencies(self) -> bool:
        """Start Docker Compose services if ``docker-compose.yml`` is present.

        Checks Docker availability first; logs a warning and returns ``False``
        when Docker is not available rather than raising an exception.

        Returns:
            ``True`` if compose started successfully (or no compose file found),
            ``False`` if Docker is unavailable.
        """
        compose_file = self.project_root / "docker-compose.yml"
        if not compose_file.exists():
            logger.debug("No docker-compose.yml found — skipping compose startup")
            return True

        return await self.compose_up()

    async def compose_up(self) -> bool:
        """Run ``docker compose up -d`` in the project root.

        Returns:
            ``True`` on success, ``False`` if Docker is unavailable.

        Raises:
            RuntimeError: If Docker is available but ``docker compose up`` fails.
        """
        if not _docker_available():
            logger.warning(
                "Docker not available — skipping 'docker compose up -d'. "
                "Will attempt direct server start."
            )
            return False

        logger.info("Running 'docker compose up -d' in %s", self.project_root)
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["docker", "compose", "up", "-d"],
                    cwd=self.project_root,
                    capture_output=True,
                    timeout=120,
                ),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "'docker compose up -d' timed out after 120 s"
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            raise RuntimeError(
                f"'docker compose up -d' failed (exit {result.returncode}).\n"
                f"stderr: {stderr[-2000:]}"
            )

        self._compose_started = True
        logger.info("Docker Compose services started successfully")
        return True

    async def compose_down(self) -> None:
        """Run ``docker compose down`` if compose was previously started."""
        if not self._compose_started:
            return

        compose_file = self.project_root / "docker-compose.yml"
        if not compose_file.exists():
            return

        if not _docker_available():
            logger.debug("Docker unavailable — skipping compose down")
            return

        logger.info("Running 'docker compose down' in %s", self.project_root)
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["docker", "compose", "down"],
                    cwd=self.project_root,
                    capture_output=True,
                    timeout=60,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("docker compose down failed (non-fatal): %s", exc)
        finally:
            self._compose_started = False

    # ── Package installation ──────────────────────────────────────────────────

    async def install_packages(self) -> None:
        """Install project dependencies using the detected package manager.

        Timeout: 120 seconds.

        Raises:
            RuntimeError: If the install command fails or times out.
        """
        install_cmd = self.stack.install_command
        if not install_cmd:
            logger.debug("No install command for stack '%s' — skipping", self.stack.framework)
            return

        logger.info("Installing packages: %s", install_cmd)
        cmd_parts = install_cmd.split()

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd_parts,
                    cwd=self.project_root,
                    capture_output=True,
                    timeout=120,
                ),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Package installation timed out after 120 s "
                f"(command: {install_cmd!r})"
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            stdout = result.stdout.decode(errors="replace")
            raise RuntimeError(
                f"Package installation failed (exit {result.returncode}).\n"
                f"command: {install_cmd!r}\n"
                f"stdout: {stdout[-1000:]}\n"
                f"stderr: {stderr[-2000:]}"
            )

        logger.info("Package installation complete")

    # ── Dev server startup ────────────────────────────────────────────────────

    async def _launch_server(self) -> None:
        """Launch the dev server subprocess.

        The port is injected via environment variables so frameworks that
        respect ``PORT`` (Next.js, Vite, CRA, Express, Flask …) will bind
        to the free port we selected rather than their defaults.
        """
        dev_cmd = self.stack.dev_server_command
        if not dev_cmd:
            raise RuntimeError(
                f"No dev_server_command for stack '{self.stack.framework}'. "
                "Cannot start dev server."
            )

        # Build environment with PORT override
        import os
        env = os.environ.copy()
        env["PORT"] = str(self.port)
        env["HOST"] = "0.0.0.0"  # bind to all interfaces

        # For Python servers (FastAPI/Flask/Django) inject the port differently
        framework = self.stack.framework
        cmd_with_port = self._inject_port_into_command(dev_cmd)

        logger.info("Starting dev server: %s (port %d)", cmd_with_port, self.port)
        cmd_parts = cmd_with_port.split()

        self._server_proc = subprocess.Popen(
            cmd_parts,
            cwd=self.project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        logger.debug("Dev server process started (pid=%d)", self._server_proc.pid)

    def _inject_port_into_command(self, dev_cmd: str) -> str:
        """Rewrite ``dev_cmd`` to bind to ``self.port`` instead of the default port.

        For frameworks that hardcode a port in their CLI argument, we substitute
        the detected default port with the chosen free port.
        """
        default_port = self.stack.dev_server_port
        # Replace plain port number occurrences in known flag positions
        # e.g. "uvicorn main:app --reload" → "uvicorn main:app --reload --port 3001"
        # e.g. "npm run dev" stays as-is (PORT env var handles it)
        framework = self.stack.framework

        if framework in ("fastapi",):
            # uvicorn main:app --reload  →  uvicorn main:app --reload --port <N>
            if "--port" not in dev_cmd:
                return f"{dev_cmd} --port {self.port}"
            return dev_cmd.replace(f"--port {default_port}", f"--port {self.port}")

        if framework in ("django",):
            # python manage.py runserver 0.0.0.0:8000
            return dev_cmd.replace(
                f"0.0.0.0:{default_port}", f"0.0.0.0:{self.port}"
            ).replace(
                f":{default_port}", f":{self.port}"
            )

        if framework in ("flask",):
            # flask run --host=0.0.0.0 --port=5000
            return dev_cmd.replace(
                f"--port={default_port}", f"--port={self.port}"
            ).replace(
                f"--port {default_port}", f"--port {self.port}"
            )

        if framework in ("rails",):
            # rails server -b 0.0.0.0 -p 3000
            return dev_cmd.replace(f"-p {default_port}", f"-p {self.port}")

        if framework in ("go", "gin", "echo", "chi"):
            # go run ./...  — port is set via PORT env var; no CLI arg to inject
            return dev_cmd

        # JS frameworks (nextjs, react-vite, cra, express, node):
        # PORT env var is respected by all of them; no CLI rewrite needed.
        return dev_cmd

    # ── Health checking ───────────────────────────────────────────────────────

    async def wait_healthy(self, timeout: float = 60.0) -> bool:
        """Poll the health endpoint until HTTP 200 is returned or timeout expires.

        The health-check path is taken from ``self.stack.health_check_path``.

        Args:
            timeout: Maximum seconds to wait (default 60 s per acceptance criteria).

        Returns:
            ``True`` if the server returned HTTP 200, ``False`` on timeout.
        """
        health_url = f"{self.base_url}{self.stack.health_check_path}"
        logger.debug("Polling health endpoint: %s (timeout=%ss)", health_url, timeout)

        # Try httpx first; fall back to stdlib urllib
        try:
            import httpx as _httpx  # noqa: PLC0415

            deadline = time.monotonic() + timeout
            async with _httpx.AsyncClient(timeout=2.0) as client:
                while time.monotonic() < deadline:
                    try:
                        r = await client.get(health_url)
                        if r.status_code == 200:
                            logger.debug("Health check passed (HTTP 200)")
                            return True
                    except Exception:  # noqa: BLE001
                        pass
                    await asyncio.sleep(0.5)
            return False

        except ImportError:
            # httpx not available — use stdlib
            import urllib.request as _req  # noqa: PLC0415

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    resp = _req.urlopen(health_url, timeout=2)
                    if resp.status == 200:
                        logger.debug("Health check passed (HTTP 200) via stdlib")
                        return True
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(0.5)
            return False

    # ── Seed script ───────────────────────────────────────────────────────────

    async def run_seed_script(self, seed_script: Optional[str] = None) -> None:
        """Execute an optional seed script after the server is healthy.

        The seed script is located relative to ``self.project_root``.  If no
        script is provided and no standard seed file exists, this is a no-op.

        Args:
            seed_script: Relative path to a seed script (e.g. ``"seed.js"``
                or ``"scripts/seed.py"``).  ``None`` means auto-detect.

        Raises:
            RuntimeError: If the seed script exits with a non-zero code.
        """
        # Auto-detect common seed file locations
        if seed_script is None:
            candidates = [
                "seed.js",
                "seed.ts",
                "scripts/seed.js",
                "scripts/seed.ts",
                "seed.py",
                "scripts/seed.py",
                "db/seed.rb",
                "db/seeds.rb",
            ]
            for candidate in candidates:
                if (self.project_root / candidate).exists():
                    seed_script = candidate
                    break

        if seed_script is None:
            logger.debug("No seed script found — skipping")
            return

        seed_path = self.project_root / seed_script
        if not seed_path.exists():
            logger.warning("Seed script not found: %s — skipping", seed_path)
            return

        logger.info("Running seed script: %s", seed_script)

        # Determine how to run it based on extension
        ext = seed_path.suffix.lower()
        if ext in (".js", ".ts"):
            cmd = ["node", str(seed_path)]
        elif ext == ".py":
            cmd = [sys.executable, str(seed_path)]
        elif ext == ".rb":
            cmd = ["ruby", str(seed_path)]
        else:
            cmd = [str(seed_path)]

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    cwd=self.project_root,
                    capture_output=True,
                    timeout=60,
                ),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Seed script timed out after 60 s: {seed_script}"
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            raise RuntimeError(
                f"Seed script failed (exit {result.returncode}): {seed_script}\n"
                f"stderr: {stderr[-2000:]}"
            )

        logger.info("Seed script completed successfully")

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _stop_server_proc(self) -> None:
        """Terminate the dev server subprocess gracefully."""
        if self._server_proc is None:
            return

        proc = self._server_proc
        self._server_proc = None

        if proc.poll() is not None:
            # Already exited
            return

        logger.debug("Terminating dev server (pid=%d)", proc.pid)
        proc.terminate()
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: proc.wait(timeout=10)
            )
        except subprocess.TimeoutExpired:
            logger.warning("Dev server did not terminate — killing (pid=%d)", proc.pid)
            proc.kill()
            proc.wait()

    def _read_proc_stderr(self, max_bytes: int = 4000) -> str:
        """Read a non-blocking snippet from the server process stderr for diagnostics."""
        if self._server_proc is None or self._server_proc.stderr is None:
            return ""
        try:
            import fcntl  # noqa: PLC0415 — Unix-only
            import os  # noqa: PLC0415

            fd = self._server_proc.stderr.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            data = self._server_proc.stderr.read() or b""
            return data.decode(errors="replace")[-max_bytes:]
        except Exception:  # noqa: BLE001
            return ""

    def _atexit_cleanup(self) -> None:
        """Synchronous cleanup called by ``atexit`` when the interpreter exits.

        This is a safety net for abnormal exits (SIGKILL, ``pytest -x``, etc.)
        where the normal async teardown path is not reached.
        """
        # Kill dev server subprocess
        if self._server_proc is not None and self._server_proc.poll() is None:
            try:
                self._server_proc.terminate()
                self._server_proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self._server_proc.kill()
                    self._server_proc.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    pass

        # Best-effort compose down (synchronous)
        if self._compose_started:
            compose_file = self.project_root / "docker-compose.yml"
            if compose_file.exists() and _docker_available():
                try:
                    subprocess.run(
                        ["docker", "compose", "down"],
                        cwd=self.project_root,
                        capture_output=True,
                        timeout=30,
                    )
                except Exception:  # noqa: BLE001
                    pass
