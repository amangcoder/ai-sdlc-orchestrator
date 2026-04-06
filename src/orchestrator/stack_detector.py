"""Stack detection utility for generated project directories.

This module is intentionally zero-dependency with respect to the orchestrator
core — it imports **nothing** from ``src/orchestrator/*`` — so it can be unit-
tested in complete isolation without spinning up the full application.

Priority order for detection
-----------------------------
1. ``package.json``          → Next.js / Vite / CRA / Express
2. ``pyproject.toml``        → FastAPI / Django / Flask  (Poetry or pip-based)
3. ``requirements.txt``      → FastAPI / Django / Flask  (bare pip)
4. ``Gemfile``               → Rails
5. ``go.mod``                → Go / Gin / Echo / Chi
6. ``artifacts/architecture.json`` (or ``architecture.json``)
7. Fallback: entry-file scan then ``unknown`` sentinel

Usage::

    from pathlib import Path
    from orchestrator.stack_detector import detect_stack, StackInfo

    info: StackInfo = detect_stack(Path("/path/to/generated/project"))
    if info.has_frontend:
        print(f"Start with: {info.dev_server_command}  →  {info.base_url}")
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


# ── Public data class ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StackInfo:
    """Immutable description of a detected project tech stack."""

    framework: str
    """Short identifier: ``"nextjs"``, ``"react-vite"``, ``"cra"``,
    ``"express"``, ``"fastapi"``, ``"django"``, ``"flask"``,
    ``"rails"``, ``"go"``, ``"gin"``, ``"echo"``, ``"chi"``,
    ``"node"``, ``"unknown"``."""

    language: str
    """Primary language: ``"javascript"``, ``"typescript"``, ``"python"``,
    ``"ruby"``, ``"go"``, ``"unknown"``."""

    package_manager: str
    """Package manager: ``"npm"``, ``"yarn"``, ``"pnpm"``, ``"pip"``,
    ``"poetry"``, ``"bundler"``, ``"go"``, ``"unknown"``."""

    install_command: str
    """Shell command to install dependencies, e.g. ``"npm install"``."""

    dev_server_command: str
    """Shell command to start the dev server, e.g. ``"npm run dev"``."""

    dev_server_port: int
    """Default port the dev server listens on, e.g. ``3000``."""

    base_url: str
    """Full base URL, e.g. ``"http://localhost:3000"``."""

    health_check_path: str
    """HTTP path used for health polling, e.g. ``"/"`` or ``"/health"``."""

    has_frontend: bool
    """``True`` when the project serves a browser-facing UI.
    ``False`` for pure API / CLI / backend-only projects."""


# ── Internal helpers ──────────────────────────────────────────────────────────


def _read_json(path: Path) -> dict:
    """Read and parse a JSON file; return an empty dict on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _read_text(path: Path) -> str:
    """Read a text file; return an empty string on any error."""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _contains_any(text: str, *substrings: str) -> bool:
    """Return True if *text* contains at least one of the given substrings."""
    return any(s in text for s in substrings)


def _js_package_manager(project_root: Path) -> str:
    """Infer the JS package manager from lock-file presence."""
    if (project_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_root / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _js_install_command(pm: str) -> str:
    return {"pnpm": "pnpm install", "yarn": "yarn install"}.get(pm, "npm install")


def _js_run(pm: str, script: str) -> str:
    """Return ``<pm> run <script>`` (or just ``<pm> <script>`` for npm start)."""
    if pm == "npm":
        return f"npm run {script}" if script != "start" else "npm start"
    return f"{pm} run {script}" if script != "start" else f"{pm} start"


def _has_tsconfig(project_root: Path) -> bool:
    return (project_root / "tsconfig.json").exists()


# ── Stage 1 — package.json ────────────────────────────────────────────────────


def _detect_from_package_json(project_root: Path) -> StackInfo | None:
    pkg_path = project_root / "package.json"
    if not pkg_path.exists():
        return None

    pkg = _read_json(pkg_path)
    deps: dict[str, str] = {
        **pkg.get("dependencies", {}),
        **pkg.get("devDependencies", {}),
    }
    scripts: dict[str, str] = pkg.get("scripts", {})
    pm = _js_package_manager(project_root)
    install_cmd = _js_install_command(pm)
    lang = "typescript" if _has_tsconfig(project_root) else "javascript"

    # ── Next.js ───────────────────────────────────────────────────────────────
    if "next" in deps:
        return StackInfo(
            framework="nextjs",
            language=lang,
            package_manager=pm,
            install_command=install_cmd,
            dev_server_command=_js_run(pm, "dev"),
            dev_server_port=3000,
            base_url="http://localhost:3000",
            health_check_path="/",
            has_frontend=True,
        )

    # ── Vite (React, Vue, Svelte …) ───────────────────────────────────────────
    if "vite" in deps or "@vitejs/plugin-react" in deps or "@vitejs/plugin-vue" in deps:
        return StackInfo(
            framework="react-vite",
            language=lang,
            package_manager=pm,
            install_command=install_cmd,
            dev_server_command=_js_run(pm, "dev"),
            dev_server_port=5173,
            base_url="http://localhost:5173",
            health_check_path="/",
            has_frontend=True,
        )

    # ── Create React App ──────────────────────────────────────────────────────
    if "react-scripts" in deps:
        return StackInfo(
            framework="cra",
            language=lang,
            package_manager=pm,
            install_command=install_cmd,
            dev_server_command=_js_run(pm, "start"),
            dev_server_port=3000,
            base_url="http://localhost:3000",
            health_check_path="/",
            has_frontend=True,
        )

    # ── Express (Node API / SSR) ──────────────────────────────────────────────
    if "express" in deps:
        if "dev" in scripts:
            dev_cmd = _js_run(pm, "dev")
        elif "start" in scripts:
            dev_cmd = _js_run(pm, "start")
        else:
            dev_cmd = "node server.js"
        return StackInfo(
            framework="express",
            language=lang,
            package_manager=pm,
            install_command=install_cmd,
            dev_server_command=dev_cmd,
            dev_server_port=3000,
            base_url="http://localhost:3000",
            health_check_path="/health",
            has_frontend=False,
        )

    return None


# ── Stage 2a — pyproject.toml ─────────────────────────────────────────────────


def _detect_from_pyproject(project_root: Path) -> StackInfo | None:
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.exists():
        return None

    content = _read_text(pyproject_path)
    is_poetry = (project_root / "poetry.lock").exists()
    pm = "poetry" if is_poetry else "pip"
    install_cmd = "poetry install" if is_poetry else "pip install -e ."

    # FastAPI — check before Flask/Django to avoid false positives
    if _contains_any(content, "fastapi", "FastAPI"):
        return StackInfo(
            framework="fastapi",
            language="python",
            package_manager=pm,
            install_command=install_cmd,
            dev_server_command="uvicorn main:app --reload",
            dev_server_port=8000,
            base_url="http://localhost:8000",
            health_check_path="/",
            has_frontend=False,
        )

    # Django
    if _contains_any(content, "django", "Django"):
        return StackInfo(
            framework="django",
            language="python",
            package_manager=pm,
            install_command=install_cmd,
            dev_server_command="python manage.py runserver 0.0.0.0:8000",
            dev_server_port=8000,
            base_url="http://localhost:8000",
            health_check_path="/",
            has_frontend=False,
        )

    # Flask
    if _contains_any(content, "flask", "Flask"):
        return StackInfo(
            framework="flask",
            language="python",
            package_manager=pm,
            install_command=install_cmd,
            dev_server_command="flask run --host=0.0.0.0 --port=5000",
            dev_server_port=5000,
            base_url="http://localhost:5000",
            health_check_path="/",
            has_frontend=False,
        )

    return None


# ── Stage 2b — requirements.txt ───────────────────────────────────────────────


def _detect_from_requirements(project_root: Path) -> StackInfo | None:
    req_path = project_root / "requirements.txt"
    if not req_path.exists():
        return None

    content = _read_text(req_path).lower()
    install_cmd = "pip install -r requirements.txt"

    if "fastapi" in content:
        return StackInfo(
            framework="fastapi",
            language="python",
            package_manager="pip",
            install_command=install_cmd,
            dev_server_command="uvicorn main:app --reload",
            dev_server_port=8000,
            base_url="http://localhost:8000",
            health_check_path="/",
            has_frontend=False,
        )

    if "django" in content:
        return StackInfo(
            framework="django",
            language="python",
            package_manager="pip",
            install_command=install_cmd,
            dev_server_command="python manage.py runserver 0.0.0.0:8000",
            dev_server_port=8000,
            base_url="http://localhost:8000",
            health_check_path="/",
            has_frontend=False,
        )

    if "flask" in content:
        return StackInfo(
            framework="flask",
            language="python",
            package_manager="pip",
            install_command=install_cmd,
            dev_server_command="flask run --host=0.0.0.0 --port=5000",
            dev_server_port=5000,
            base_url="http://localhost:5000",
            health_check_path="/",
            has_frontend=False,
        )

    return None


# ── Stage 3 — Gemfile ─────────────────────────────────────────────────────────


def _detect_from_gemfile(project_root: Path) -> StackInfo | None:
    gemfile_path = project_root / "Gemfile"
    if not gemfile_path.exists():
        return None

    content = _read_text(gemfile_path)
    if _contains_any(content, "'rails'", '"rails"', "gem 'rails'", 'gem "rails"'):
        return StackInfo(
            framework="rails",
            language="ruby",
            package_manager="bundler",
            install_command="bundle install",
            dev_server_command="rails server -b 0.0.0.0 -p 3000",
            dev_server_port=3000,
            base_url="http://localhost:3000",
            health_check_path="/",
            has_frontend=True,
        )

    return None


# ── Stage 4 — go.mod ──────────────────────────────────────────────────────────


def _detect_from_go_mod(project_root: Path) -> StackInfo | None:
    gomod_path = project_root / "go.mod"
    if not gomod_path.exists():
        return None

    content = _read_text(gomod_path)
    if not content.strip():
        return None  # Empty / corrupt go.mod — skip

    # Identify Go web framework
    if "gin-gonic/gin" in content:
        framework = "gin"
    elif "labstack/echo" in content:
        framework = "echo"
    elif "go-chi/chi" in content:
        framework = "chi"
    else:
        framework = "go"

    return StackInfo(
        framework=framework,
        language="go",
        package_manager="go",
        install_command="go mod download",
        dev_server_command="go run ./...",
        dev_server_port=8080,
        base_url="http://localhost:8080",
        health_check_path="/health",
        has_frontend=False,
    )


# ── Stage 5 — architecture.json ──────────────────────────────────────────────


def _detect_from_architecture_json(project_root: Path) -> StackInfo | None:
    """Use the Architect's ``architecture.json`` as an explicit hint source."""
    for candidate in (
        project_root / "artifacts" / "architecture.json",
        project_root / "architecture.json",
    ):
        if candidate.exists():
            arch_path = candidate
            break
    else:
        return None

    arch = _read_json(arch_path)
    if not arch:
        return None

    # Combine all text content for keyword scanning
    parts: list[str] = []
    for key in ("tech_stack", "tech_decisions", "framework", "language", "stack"):
        value = arch.get(key)
        if value:
            parts.append(json.dumps(value).lower() if not isinstance(value, str) else value.lower())

    tech_str = " ".join(parts)
    if not tech_str.strip():
        return None

    if "nextjs" in tech_str or "next.js" in tech_str or (
        "next" in tech_str and "react" in tech_str
    ):
        return StackInfo(
            framework="nextjs",
            language="typescript",
            package_manager="npm",
            install_command="npm install",
            dev_server_command="npm run dev",
            dev_server_port=3000,
            base_url="http://localhost:3000",
            health_check_path="/",
            has_frontend=True,
        )

    if "vite" in tech_str:
        return StackInfo(
            framework="react-vite",
            language="typescript",
            package_manager="npm",
            install_command="npm install",
            dev_server_command="npm run dev",
            dev_server_port=5173,
            base_url="http://localhost:5173",
            health_check_path="/",
            has_frontend=True,
        )

    if "fastapi" in tech_str:
        return StackInfo(
            framework="fastapi",
            language="python",
            package_manager="pip",
            install_command="pip install -r requirements.txt",
            dev_server_command="uvicorn main:app --reload",
            dev_server_port=8000,
            base_url="http://localhost:8000",
            health_check_path="/",
            has_frontend=False,
        )

    if "django" in tech_str:
        return StackInfo(
            framework="django",
            language="python",
            package_manager="pip",
            install_command="pip install -r requirements.txt",
            dev_server_command="python manage.py runserver 0.0.0.0:8000",
            dev_server_port=8000,
            base_url="http://localhost:8000",
            health_check_path="/",
            has_frontend=False,
        )

    if "flask" in tech_str:
        return StackInfo(
            framework="flask",
            language="python",
            package_manager="pip",
            install_command="pip install -r requirements.txt",
            dev_server_command="flask run --host=0.0.0.0 --port=5000",
            dev_server_port=5000,
            base_url="http://localhost:5000",
            health_check_path="/",
            has_frontend=False,
        )

    if "rails" in tech_str or "ruby" in tech_str:
        return StackInfo(
            framework="rails",
            language="ruby",
            package_manager="bundler",
            install_command="bundle install",
            dev_server_command="rails server -b 0.0.0.0 -p 3000",
            dev_server_port=3000,
            base_url="http://localhost:3000",
            health_check_path="/",
            has_frontend=True,
        )

    return None


# ── Stage 6 — Fallback entry-file scan ───────────────────────────────────────


def _detect_fallback(project_root: Path) -> StackInfo:
    """Scan for well-known entry files; return an ``unknown`` sentinel last."""

    # Python entry files
    python_entries = ["app.py", "main.py", "server.py", "run.py", "wsgi.py", "asgi.py"]
    for fname in python_entries:
        fpath = project_root / fname
        if fpath.exists():
            content = _read_text(fpath).lower()
            if "fastapi" in content:
                return StackInfo(
                    framework="fastapi",
                    language="python",
                    package_manager="pip",
                    install_command="pip install -r requirements.txt",
                    dev_server_command="uvicorn main:app --reload",
                    dev_server_port=8000,
                    base_url="http://localhost:8000",
                    health_check_path="/",
                    has_frontend=False,
                )
            if "flask" in content:
                return StackInfo(
                    framework="flask",
                    language="python",
                    package_manager="pip",
                    install_command="pip install -r requirements.txt",
                    dev_server_command="flask run --host=0.0.0.0 --port=5000",
                    dev_server_port=5000,
                    base_url="http://localhost:5000",
                    health_check_path="/",
                    has_frontend=False,
                )
            if "django" in content:
                return StackInfo(
                    framework="django",
                    language="python",
                    package_manager="pip",
                    install_command="pip install -r requirements.txt",
                    dev_server_command="python manage.py runserver 0.0.0.0:8000",
                    dev_server_port=8000,
                    base_url="http://localhost:8000",
                    health_check_path="/",
                    has_frontend=False,
                )

    # Node.js entry files
    node_entries = ["server.js", "index.js", "app.js", "server.ts", "index.ts", "app.ts"]
    for fname in node_entries:
        if (project_root / fname).exists():
            return StackInfo(
                framework="node",
                language="javascript",
                package_manager="npm",
                install_command="npm install",
                dev_server_command="node server.js",
                dev_server_port=3000,
                base_url="http://localhost:3000",
                health_check_path="/",
                has_frontend=False,
            )

    # Truly unknown — has_frontend=False so the QA Browser phase skips gracefully
    return StackInfo(
        framework="unknown",
        language="unknown",
        package_manager="unknown",
        install_command="",
        dev_server_command="",
        dev_server_port=8080,
        base_url="http://localhost:8080",
        health_check_path="/",
        has_frontend=False,
    )


# ── Public API ────────────────────────────────────────────────────────────────


def detect_stack(project_root: Path) -> StackInfo:
    """Detect the tech stack of a generated project.

    Examines project files in priority order and returns a fully-populated
    :class:`StackInfo`.  Never raises — the last stage returns an
    ``"unknown"`` sentinel with ``has_frontend=False`` so callers can skip
    gracefully when nothing is recognisable.

    Args:
        project_root: Absolute (or relative) path to the root of the
            generated project directory.

    Returns:
        A frozen :class:`StackInfo` dataclass.
    """
    for _detector in (
        _detect_from_package_json,
        _detect_from_pyproject,
        _detect_from_requirements,
        _detect_from_gemfile,
        _detect_from_go_mod,
        _detect_from_architecture_json,
    ):
        result = _detector(project_root)
        if result is not None:
            return result

    return _detect_fallback(project_root)
