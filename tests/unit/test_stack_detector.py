"""Unit tests for src/orchestrator/stack_detector.py.

All tests are pure in-memory using ``tmp_path`` fixtures — no network,
no real project directories, no orchestrator-core imports.

Covers:
- AC-001: Next.js detection from package.json
- AC-002: FastAPI detection from pyproject.toml
- AC-003: has_frontend=False for unknown projects
- Full detection chain: Vite, CRA, Express, Django, Flask (req.txt),
  Rails, Go / Gin, architecture.json fallback, entry-file fallback
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.stack_detector import StackInfo, detect_stack


# ── Helpers ────────────────────────────────────────────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    _write(path, json.dumps(data))


def _pkg(
    deps: dict | None = None,
    dev_deps: dict | None = None,
    scripts: dict | None = None,
) -> dict:
    return {
        "name": "test-project",
        "version": "0.0.1",
        "dependencies": deps or {},
        "devDependencies": dev_deps or {},
        "scripts": scripts or {},
    }


# ── Stage 1 — package.json ─────────────────────────────────────────────────────


class TestNextJsDetection:
    """AC-001: Next.js projects detected from package.json."""

    def test_nextjs_in_dependencies(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"next": "14.0.0", "react": "18.0.0"}))
        info = detect_stack(tmp_path)
        assert info.framework == "nextjs"

    def test_nextjs_sets_correct_port(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"next": "14.0.0"}))
        info = detect_stack(tmp_path)
        assert info.dev_server_port == 3000
        assert info.base_url == "http://localhost:3000"

    def test_nextjs_has_frontend_true(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"next": "14.0.0"}))
        info = detect_stack(tmp_path)
        assert info.has_frontend is True

    def test_nextjs_typescript_when_tsconfig_present(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"next": "14.0.0"}))
        (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
        info = detect_stack(tmp_path)
        assert info.language == "typescript"

    def test_nextjs_javascript_when_no_tsconfig(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"next": "14.0.0"}))
        info = detect_stack(tmp_path)
        assert info.language == "javascript"

    def test_nextjs_npm_by_default(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"next": "14.0.0"}))
        info = detect_stack(tmp_path)
        assert info.package_manager == "npm"
        assert info.install_command == "npm install"

    def test_nextjs_pnpm_when_lockfile_present(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"next": "14.0.0"}))
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 6\n", encoding="utf-8")
        info = detect_stack(tmp_path)
        assert info.package_manager == "pnpm"
        assert info.install_command == "pnpm install"
        assert info.dev_server_command == "pnpm run dev"

    def test_nextjs_yarn_when_lockfile_present(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"next": "14.0.0"}))
        (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n", encoding="utf-8")
        info = detect_stack(tmp_path)
        assert info.package_manager == "yarn"
        assert info.install_command == "yarn install"

    def test_nextjs_in_dev_dependencies(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(dev_deps={"next": "14.0.0"}))
        info = detect_stack(tmp_path)
        assert info.framework == "nextjs"


class TestViteDetection:
    """Vite projects detected from package.json."""

    def test_vite_in_dependencies(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"vite": "5.0.0", "react": "18.0.0"}))
        info = detect_stack(tmp_path)
        assert info.framework == "react-vite"

    def test_vite_port_5173(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"vite": "5.0.0"}))
        info = detect_stack(tmp_path)
        assert info.dev_server_port == 5173
        assert info.base_url == "http://localhost:5173"

    def test_vite_has_frontend_true(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"vite": "5.0.0"}))
        info = detect_stack(tmp_path)
        assert info.has_frontend is True

    def test_vitejs_react_plugin_detected(self, tmp_path: Path):
        _write_json(
            tmp_path / "package.json",
            _pkg(dev_deps={"@vitejs/plugin-react": "4.0.0"}),
        )
        info = detect_stack(tmp_path)
        assert info.framework == "react-vite"

    def test_vitejs_vue_plugin_detected(self, tmp_path: Path):
        _write_json(
            tmp_path / "package.json",
            _pkg(dev_deps={"@vitejs/plugin-vue": "4.0.0"}),
        )
        info = detect_stack(tmp_path)
        assert info.framework == "react-vite"


class TestCRADetection:
    """Create React App detected from package.json."""

    def test_react_scripts_detected(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"react-scripts": "5.0.1", "react": "18.0.0"}))
        info = detect_stack(tmp_path)
        assert info.framework == "cra"

    def test_cra_has_frontend_true(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"react-scripts": "5.0.1"}))
        info = detect_stack(tmp_path)
        assert info.has_frontend is True

    def test_cra_dev_server_port_3000(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"react-scripts": "5.0.1"}))
        info = detect_stack(tmp_path)
        assert info.dev_server_port == 3000

    def test_cra_start_command_npm(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"react-scripts": "5.0.1"}))
        info = detect_stack(tmp_path)
        assert info.dev_server_command == "npm start"


class TestExpressDetection:
    """Express (Node.js API) detected from package.json."""

    def test_express_detected(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"express": "4.18.0"}))
        info = detect_stack(tmp_path)
        assert info.framework == "express"

    def test_express_has_frontend_false(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"express": "4.18.0"}))
        info = detect_stack(tmp_path)
        assert info.has_frontend is False

    def test_express_health_check_path(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"express": "4.18.0"}))
        info = detect_stack(tmp_path)
        assert info.health_check_path == "/health"

    def test_express_uses_dev_script_when_present(self, tmp_path: Path):
        _write_json(
            tmp_path / "package.json",
            _pkg(deps={"express": "4.18.0"}, scripts={"dev": "nodemon server.js"}),
        )
        info = detect_stack(tmp_path)
        assert info.dev_server_command == "npm run dev"

    def test_express_falls_back_to_start_script(self, tmp_path: Path):
        _write_json(
            tmp_path / "package.json",
            _pkg(deps={"express": "4.18.0"}, scripts={"start": "node server.js"}),
        )
        info = detect_stack(tmp_path)
        assert info.dev_server_command == "npm start"

    def test_express_falls_back_to_node_server(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"express": "4.18.0"}))
        info = detect_stack(tmp_path)
        assert info.dev_server_command == "node server.js"


# ── Stage 2a — pyproject.toml ─────────────────────────────────────────────────


class TestFastAPIFromPyproject:
    """AC-002: FastAPI detected from pyproject.toml."""

    def test_fastapi_detected(self, tmp_path: Path):
        _write(
            tmp_path / "pyproject.toml",
            '[tool.poetry.dependencies]\nfastapi = "^0.104.0"\n',
        )
        info = detect_stack(tmp_path)
        assert info.framework == "fastapi"

    def test_fastapi_language_python(self, tmp_path: Path):
        _write(tmp_path / "pyproject.toml", '[project]\ndependencies = ["fastapi"]\n')
        info = detect_stack(tmp_path)
        assert info.language == "python"

    def test_fastapi_has_frontend_false(self, tmp_path: Path):
        _write(tmp_path / "pyproject.toml", 'dependencies = ["fastapi>=0.100"]\n')
        info = detect_stack(tmp_path)
        assert info.has_frontend is False

    def test_fastapi_dev_server_command(self, tmp_path: Path):
        _write(tmp_path / "pyproject.toml", 'dependencies = ["fastapi"]\n')
        info = detect_stack(tmp_path)
        assert "uvicorn" in info.dev_server_command

    def test_fastapi_port_8000(self, tmp_path: Path):
        _write(tmp_path / "pyproject.toml", 'dependencies = ["fastapi"]\n')
        info = detect_stack(tmp_path)
        assert info.dev_server_port == 8000

    def test_fastapi_poetry_package_manager(self, tmp_path: Path):
        _write(tmp_path / "pyproject.toml", 'dependencies = ["fastapi"]\n')
        (tmp_path / "poetry.lock").write_text("# poetry lock\n", encoding="utf-8")
        info = detect_stack(tmp_path)
        assert info.package_manager == "poetry"
        assert info.install_command == "poetry install"

    def test_fastapi_pip_package_manager_without_poetry_lock(self, tmp_path: Path):
        _write(tmp_path / "pyproject.toml", 'dependencies = ["fastapi"]\n')
        info = detect_stack(tmp_path)
        assert info.package_manager == "pip"

    def test_django_from_pyproject(self, tmp_path: Path):
        _write(tmp_path / "pyproject.toml", 'dependencies = ["Django>=4.0"]\n')
        info = detect_stack(tmp_path)
        assert info.framework == "django"
        assert info.has_frontend is False
        assert "manage.py" in info.dev_server_command

    def test_flask_from_pyproject(self, tmp_path: Path):
        _write(tmp_path / "pyproject.toml", 'dependencies = ["Flask>=2.0"]\n')
        info = detect_stack(tmp_path)
        assert info.framework == "flask"
        assert info.dev_server_port == 5000

    def test_fastapi_takes_priority_over_django_in_pyproject(self, tmp_path: Path):
        _write(
            tmp_path / "pyproject.toml",
            'dependencies = ["fastapi", "django"]\n',
        )
        info = detect_stack(tmp_path)
        assert info.framework == "fastapi"


# ── Stage 2b — requirements.txt ───────────────────────────────────────────────


class TestRequirementsTxt:
    """Python stacks detected from requirements.txt."""

    def test_fastapi_from_requirements(self, tmp_path: Path):
        _write(tmp_path / "requirements.txt", "fastapi==0.104.0\nuvicorn[standard]==0.24.0\n")
        info = detect_stack(tmp_path)
        assert info.framework == "fastapi"
        assert info.package_manager == "pip"
        assert info.install_command == "pip install -r requirements.txt"

    def test_django_from_requirements(self, tmp_path: Path):
        _write(tmp_path / "requirements.txt", "Django>=4.2\npsycopg2-binary\n")
        info = detect_stack(tmp_path)
        assert info.framework == "django"
        assert info.dev_server_port == 8000

    def test_flask_from_requirements(self, tmp_path: Path):
        _write(tmp_path / "requirements.txt", "Flask==2.3.0\nSQLAlchemy\n")
        info = detect_stack(tmp_path)
        assert info.framework == "flask"
        assert info.dev_server_port == 5000

    def test_fastapi_case_insensitive(self, tmp_path: Path):
        _write(tmp_path / "requirements.txt", "FastAPI>=0.100\n")
        info = detect_stack(tmp_path)
        assert info.framework == "fastapi"

    def test_requirements_has_frontend_false_all_frameworks(self, tmp_path: Path):
        for pkg in ("fastapi", "django", "flask"):
            project = tmp_path / pkg
            project.mkdir()
            _write(project / "requirements.txt", f"{pkg}\n")
            info = detect_stack(project)
            assert info.has_frontend is False, f"Expected has_frontend=False for {pkg}"


# ── Stage 3 — Gemfile ─────────────────────────────────────────────────────────


class TestRailsDetection:
    """Rails detected from Gemfile."""

    def test_rails_detected(self, tmp_path: Path):
        _write(tmp_path / "Gemfile", "source 'https://rubygems.org'\ngem 'rails', '~> 7.0'\n")
        info = detect_stack(tmp_path)
        assert info.framework == "rails"

    def test_rails_language_ruby(self, tmp_path: Path):
        _write(tmp_path / "Gemfile", "gem 'rails'\n")
        info = detect_stack(tmp_path)
        assert info.language == "ruby"

    def test_rails_has_frontend_true(self, tmp_path: Path):
        _write(tmp_path / "Gemfile", "gem 'rails'\n")
        info = detect_stack(tmp_path)
        assert info.has_frontend is True

    def test_rails_bundler_package_manager(self, tmp_path: Path):
        _write(tmp_path / "Gemfile", "gem 'rails'\n")
        info = detect_stack(tmp_path)
        assert info.package_manager == "bundler"
        assert info.install_command == "bundle install"

    def test_gemfile_without_rails_ignored(self, tmp_path: Path):
        _write(tmp_path / "Gemfile", "source 'https://rubygems.org'\ngem 'sinatra'\n")
        info = detect_stack(tmp_path)
        # Should fall through to fallback, not return rails
        assert info.framework != "rails"


# ── Stage 4 — go.mod ──────────────────────────────────────────────────────────


class TestGoDetection:
    """Go projects detected from go.mod."""

    def test_go_detected(self, tmp_path: Path):
        _write(tmp_path / "go.mod", "module example.com/myapp\n\ngo 1.21\n")
        info = detect_stack(tmp_path)
        assert info.language == "go"

    def test_go_generic_framework(self, tmp_path: Path):
        _write(tmp_path / "go.mod", "module example.com/myapp\n\ngo 1.21\n")
        info = detect_stack(tmp_path)
        assert info.framework == "go"

    def test_gin_framework_detected(self, tmp_path: Path):
        _write(
            tmp_path / "go.mod",
            "module example.com/myapp\n\ngo 1.21\n\nrequire github.com/gin-gonic/gin v1.9.0\n",
        )
        info = detect_stack(tmp_path)
        assert info.framework == "gin"

    def test_echo_framework_detected(self, tmp_path: Path):
        _write(
            tmp_path / "go.mod",
            "module example.com/myapp\n\ngo 1.21\n\nrequire github.com/labstack/echo/v4 v4.11.0\n",
        )
        info = detect_stack(tmp_path)
        assert info.framework == "echo"

    def test_chi_framework_detected(self, tmp_path: Path):
        _write(
            tmp_path / "go.mod",
            "module example.com/myapp\n\ngo 1.21\n\nrequire github.com/go-chi/chi/v5 v5.0.8\n",
        )
        info = detect_stack(tmp_path)
        assert info.framework == "chi"

    def test_go_has_frontend_false(self, tmp_path: Path):
        _write(tmp_path / "go.mod", "module example.com/myapp\n\ngo 1.21\n")
        info = detect_stack(tmp_path)
        assert info.has_frontend is False

    def test_go_package_manager_and_install(self, tmp_path: Path):
        _write(tmp_path / "go.mod", "module example.com/myapp\n\ngo 1.21\n")
        info = detect_stack(tmp_path)
        assert info.package_manager == "go"
        assert info.install_command == "go mod download"

    def test_go_port_8080(self, tmp_path: Path):
        _write(tmp_path / "go.mod", "module example.com/myapp\n\ngo 1.21\n")
        info = detect_stack(tmp_path)
        assert info.dev_server_port == 8080

    def test_empty_gomod_ignored(self, tmp_path: Path):
        _write(tmp_path / "go.mod", "")
        info = detect_stack(tmp_path)
        assert info.framework != "go"


# ── Stage 5 — architecture.json ───────────────────────────────────────────────


class TestArchitectureJsonDetection:
    """Explicit tech hints from architecture.json used as fallback."""

    def test_nextjs_from_architecture_json(self, tmp_path: Path):
        arch = {"tech_decisions": ["Use Next.js 14 for SSR"]}
        _write_json(tmp_path / "architecture.json", arch)
        info = detect_stack(tmp_path)
        assert info.framework == "nextjs"

    def test_fastapi_from_architecture_json(self, tmp_path: Path):
        arch = {"tech_stack": ["fastapi", "postgresql"]}
        _write_json(tmp_path / "architecture.json", arch)
        info = detect_stack(tmp_path)
        assert info.framework == "fastapi"

    def test_flask_from_architecture_json(self, tmp_path: Path):
        arch = {"framework": "flask"}
        _write_json(tmp_path / "architecture.json", arch)
        info = detect_stack(tmp_path)
        assert info.framework == "flask"

    def test_architecture_json_in_artifacts_subdir(self, tmp_path: Path):
        arch = {"tech_stack": ["fastapi"]}
        _write_json(tmp_path / "artifacts" / "architecture.json", arch)
        info = detect_stack(tmp_path)
        assert info.framework == "fastapi"

    def test_package_json_takes_priority_over_architecture(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"next": "14.0.0"}))
        _write_json(tmp_path / "architecture.json", {"tech_stack": ["fastapi"]})
        info = detect_stack(tmp_path)
        # package.json wins
        assert info.framework == "nextjs"


# ── Stage 6 — Fallback ────────────────────────────────────────────────────────


class TestFallback:
    """AC-003: Unknown / unrecognisable projects return has_frontend=False."""

    def test_empty_directory_returns_unknown(self, tmp_path: Path):
        info = detect_stack(tmp_path)
        assert info.framework == "unknown"

    def test_empty_directory_has_frontend_false(self, tmp_path: Path):
        info = detect_stack(tmp_path)
        assert info.has_frontend is False

    def test_empty_directory_language_unknown(self, tmp_path: Path):
        info = detect_stack(tmp_path)
        assert info.language == "unknown"

    def test_python_entry_file_fastapi_import(self, tmp_path: Path):
        _write(tmp_path / "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        info = detect_stack(tmp_path)
        assert info.framework == "fastapi"

    def test_python_entry_file_flask_import(self, tmp_path: Path):
        _write(tmp_path / "app.py", "from flask import Flask\napp = Flask(__name__)\n")
        info = detect_stack(tmp_path)
        assert info.framework == "flask"

    def test_python_entry_file_django_import(self, tmp_path: Path):
        _write(tmp_path / "manage.py", "")  # manage.py alone won't trigger
        _write(tmp_path / "app.py", "import django\ndjango.setup()\n")
        info = detect_stack(tmp_path)
        assert info.framework == "django"

    def test_node_entry_file_detected(self, tmp_path: Path):
        _write(tmp_path / "server.js", "const http = require('http');\n")
        info = detect_stack(tmp_path)
        assert info.framework == "node"
        assert info.language == "javascript"
        assert info.has_frontend is False


# ── Priority ordering ──────────────────────────────────────────────────────────


class TestPriorityOrdering:
    """Files are examined in the documented priority order."""

    def test_package_json_beats_requirements_txt(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"next": "14.0.0"}))
        _write(tmp_path / "requirements.txt", "fastapi\n")
        info = detect_stack(tmp_path)
        assert info.framework == "nextjs"

    def test_pyproject_beats_requirements_txt(self, tmp_path: Path):
        _write(tmp_path / "pyproject.toml", 'dependencies = ["fastapi"]\n')
        _write(tmp_path / "requirements.txt", "flask\n")
        info = detect_stack(tmp_path)
        assert info.framework == "fastapi"

    def test_requirements_beats_gemfile(self, tmp_path: Path):
        _write(tmp_path / "requirements.txt", "flask\n")
        _write(tmp_path / "Gemfile", "gem 'rails'\n")
        info = detect_stack(tmp_path)
        assert info.framework == "flask"

    def test_gemfile_beats_go_mod(self, tmp_path: Path):
        _write(tmp_path / "Gemfile", "gem 'rails'\n")
        _write(tmp_path / "go.mod", "module example.com/myapp\n\ngo 1.21\n")
        info = detect_stack(tmp_path)
        assert info.framework == "rails"


# ── StackInfo dataclass contract ──────────────────────────────────────────────


class TestStackInfoContract:
    """StackInfo must be a frozen dataclass with all required fields."""

    def test_all_required_fields_present(self, tmp_path: Path):
        info = detect_stack(tmp_path)
        # All fields must be accessible
        assert isinstance(info.framework, str)
        assert isinstance(info.language, str)
        assert isinstance(info.package_manager, str)
        assert isinstance(info.install_command, str)
        assert isinstance(info.dev_server_command, str)
        assert isinstance(info.dev_server_port, int)
        assert isinstance(info.base_url, str)
        assert isinstance(info.health_check_path, str)
        assert isinstance(info.has_frontend, bool)

    def test_stack_info_is_frozen(self, tmp_path: Path):
        info = detect_stack(tmp_path)
        with pytest.raises((AttributeError, TypeError)):
            info.framework = "hacked"  # type: ignore[misc]

    def test_base_url_contains_port(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"next": "14.0.0"}))
        info = detect_stack(tmp_path)
        assert str(info.dev_server_port) in info.base_url

    def test_no_orchestrator_core_imports(self):
        """Verify the module has zero orchestrator-core imports at the source level."""
        import ast
        import importlib.util

        spec = importlib.util.find_spec("orchestrator.stack_detector")
        assert spec is not None, "orchestrator.stack_detector not found"
        assert spec.origin is not None

        source = Path(spec.origin).read_text(encoding="utf-8")
        tree = ast.parse(source)

        forbidden_prefixes = (
            "orchestrator.models",
            "orchestrator.phases",
            "orchestrator.engine",
            "orchestrator.workflow_engine",
            "orchestrator.roles",
            "orchestrator.config",
        )

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for prefix in forbidden_prefixes:
                        assert not node.module.startswith(prefix), (
                            f"Forbidden import found: {node.module}"
                        )


# ── Architecture JSON — additional branches ───────────────────────────────────


class TestArchitectureJsonAdditionalBranches:
    """Cover vite, django, rails and empty-tech branches in _detect_from_architecture_json."""

    def test_vite_from_architecture_json(self, tmp_path: Path):
        arch = {"tech_stack": ["vite", "react"]}
        _write_json(tmp_path / "architecture.json", arch)
        info = detect_stack(tmp_path)
        assert info.framework == "react-vite"
        assert info.dev_server_port == 5173
        assert info.has_frontend is True

    def test_django_from_architecture_json(self, tmp_path: Path):
        arch = {"tech_stack": ["django", "postgresql"]}
        _write_json(tmp_path / "architecture.json", arch)
        info = detect_stack(tmp_path)
        assert info.framework == "django"
        assert info.has_frontend is False

    def test_rails_from_architecture_json(self, tmp_path: Path):
        arch = {"framework": "rails"}
        _write_json(tmp_path / "architecture.json", arch)
        info = detect_stack(tmp_path)
        assert info.framework == "rails"
        assert info.has_frontend is True

    def test_ruby_keyword_in_architecture_json(self, tmp_path: Path):
        arch = {"language": "ruby"}
        _write_json(tmp_path / "architecture.json", arch)
        info = detect_stack(tmp_path)
        assert info.framework == "rails"

    def test_architecture_json_with_no_recognised_tech_falls_through(self, tmp_path: Path):
        """architecture.json with irrelevant content returns unknown (no match)."""
        arch = {"description": "A C++ embedded system"}
        _write_json(tmp_path / "architecture.json", arch)
        info = detect_stack(tmp_path)
        # Falls through to fallback — should be unknown with no other project files
        assert info.framework == "unknown"

    def test_architecture_json_empty_object_falls_through(self, tmp_path: Path):
        _write_json(tmp_path / "architecture.json", {})
        info = detect_stack(tmp_path)
        assert info.framework == "unknown"

    def test_architecture_json_invalid_json_falls_through(self, tmp_path: Path):
        (tmp_path / "architecture.json").write_text("NOT VALID JSON", encoding="utf-8")
        info = detect_stack(tmp_path)
        # _read_json returns {} → _detect_from_architecture_json returns None
        assert info.framework == "unknown"

    def test_nextjs_via_next_and_react_keywords(self, tmp_path: Path):
        arch = {"tech_decisions": ["next", "react"]}
        _write_json(tmp_path / "architecture.json", arch)
        info = detect_stack(tmp_path)
        assert info.framework == "nextjs"


# ── Fallback — additional entry-file scenarios ────────────────────────────────


class TestFallbackAdditionalEntryFiles:
    """Additional _detect_fallback coverage for various Python and Node entry filenames."""

    def test_server_py_with_fastapi(self, tmp_path: Path):
        _write(tmp_path / "server.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        info = detect_stack(tmp_path)
        assert info.framework == "fastapi"

    def test_run_py_with_flask(self, tmp_path: Path):
        _write(tmp_path / "run.py", "from flask import Flask\napp = Flask(__name__)\n")
        info = detect_stack(tmp_path)
        assert info.framework == "flask"

    def test_wsgi_py_with_django(self, tmp_path: Path):
        _write(tmp_path / "wsgi.py", "import django\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')\n")
        info = detect_stack(tmp_path)
        assert info.framework == "django"

    def test_asgi_py_with_fastapi(self, tmp_path: Path):
        _write(tmp_path / "asgi.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        info = detect_stack(tmp_path)
        assert info.framework == "fastapi"

    def test_index_js_detected_as_node(self, tmp_path: Path):
        _write(tmp_path / "index.js", "const http = require('http');\n")
        info = detect_stack(tmp_path)
        assert info.framework == "node"
        assert info.has_frontend is False

    def test_app_js_detected_as_node(self, tmp_path: Path):
        _write(tmp_path / "app.js", "const express = require('express');\n")
        info = detect_stack(tmp_path)
        assert info.framework == "node"

    def test_index_ts_detected_as_node(self, tmp_path: Path):
        _write(tmp_path / "index.ts", "import http from 'http';\n")
        info = detect_stack(tmp_path)
        assert info.framework == "node"

    def test_python_entry_no_framework_falls_through_to_node_if_server_js(self, tmp_path: Path):
        """Python entry file with no recognisable framework, but server.js present → node."""
        _write(tmp_path / "app.py", "# just a stub\n")
        _write(tmp_path / "server.js", "const http = require('http');\n")
        info = detect_stack(tmp_path)
        # app.py content has no fastapi/flask/django → continues to node entries
        assert info.framework == "node"


# ── JS run helper coverage ────────────────────────────────────────────────────


class TestJsRunHelperEdgeCases:
    """Cover _js_run 'start' script edge cases with pnpm/yarn package managers."""

    def test_cra_start_with_pnpm(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"react-scripts": "5.0.1"}))
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 6\n", encoding="utf-8")
        info = detect_stack(tmp_path)
        assert info.package_manager == "pnpm"
        # CRA uses 'start' script → pnpm start
        assert info.dev_server_command == "pnpm start"

    def test_cra_start_with_yarn(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"react-scripts": "5.0.1"}))
        (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n", encoding="utf-8")
        info = detect_stack(tmp_path)
        assert info.package_manager == "yarn"
        assert info.dev_server_command == "yarn start"

    def test_vite_with_pnpm(self, tmp_path: Path):
        _write_json(tmp_path / "package.json", _pkg(deps={"vite": "5.0.0"}))
        (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 6\n", encoding="utf-8")
        info = detect_stack(tmp_path)
        assert info.dev_server_command == "pnpm run dev"

    def test_express_dev_script_with_yarn(self, tmp_path: Path):
        _write_json(
            tmp_path / "package.json",
            _pkg(deps={"express": "4.18.0"}, scripts={"dev": "nodemon server.js"}),
        )
        (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n", encoding="utf-8")
        info = detect_stack(tmp_path)
        assert info.dev_server_command == "yarn run dev"
