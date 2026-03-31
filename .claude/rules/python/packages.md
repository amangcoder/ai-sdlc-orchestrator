# Python Packages

## pyproject.toml Conventions

All project metadata, dependencies, and tool configuration live in `pyproject.toml`. No `setup.py` or `setup.cfg`.

```toml
[build-system]
requires = ["setuptools>=68.0", "setuptools-scm>=8.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "ai-sdlc-orchestrator"
dynamic = ["version"]
requires-python = ">=3.11"
```

### Dependency Sections

- `[project.dependencies]` -- Runtime dependencies only. Keep this minimal.
- `[project.optional-dependencies.dev]` -- Testing, linting, formatting tools.
- `[project.optional-dependencies.container]` -- Docker-specific dependencies.

### Version Pinning

- Pin exact versions for direct dependencies: `pydantic==2.6.0`.
- Use `>=` only for libraries you publish for others to consume (not applicable here).
- Regenerate lock files after any dependency change.

## Virtual Environments

- Always develop inside a virtual environment. Never install to system Python.
- Use `python -m venv .venv` or `uv venv` for environment creation.
- Install in editable mode: `pip install -e ".[dev]"`.
- The `.venv/` directory is gitignored.

## Import Organization

Imports are ordered in three groups, separated by blank lines:

```python
# 1. Standard library
import asyncio
import logging
from pathlib import Path

# 2. Third-party
import anthropic
from pydantic import BaseModel

# 3. Local
from orchestrator.models import RunContext, SpeedMode
from orchestrator.engine import PipelineEngine
```

### Import Rules

- Use absolute imports for all cross-module references.
- Use `from __future__ import annotations` at the top of every module.
- Avoid circular imports. If two modules need each other, extract shared types to a `models.py` or `types.py`.
- Use `TYPE_CHECKING` guard for imports needed only by type checkers.

## Package Structure

```
src/
  orchestrator/
    __init__.py          # Public API exports
    _version.py          # Single source of version truth
    engine.py            # Pipeline orchestration
    agents.py            # Agent definitions and spawning
    models.py            # Data models (dataclasses, Pydantic)
    observability.py     # Metrics, logging, tracing
    exceptions.py        # Custom exception hierarchy
    cli.py               # Click/Typer CLI entry points
```

### Rules

- Every directory with Python files has an `__init__.py`.
- `__init__.py` exports the public API of the package. Internal modules use `_` prefix.
- Entry points are defined in `pyproject.toml` under `[project.scripts]`.

## Tooling Configuration

All tool configs in `pyproject.toml`:

```toml
[tool.black]
line-length = 88

[tool.ruff]
line-length = 88
select = ["E", "F", "I", "N", "W", "UP"]

[tool.mypy]
python_version = "3.11"
strict = true
```
