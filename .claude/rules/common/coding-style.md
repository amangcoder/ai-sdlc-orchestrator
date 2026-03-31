# Coding Style

## Immutability and Data Flow

- Prefer immutable data structures. Use `frozen=True` on dataclasses and `model_config = ConfigDict(frozen=True)` on Pydantic models unless mutation is explicitly required.
- Never mutate function arguments. Return new objects instead.
- Use `tuple` over `list` for fixed-size collections that should not change.
- Avoid global mutable state. Pass dependencies explicitly via constructors or function parameters.

## File Organization

- **Target 200-400 lines per file.** Files over 400 lines should be reviewed for splitting opportunities.
- **Hard maximum: 800 lines.** If a file exceeds 800 lines, it must be refactored before merging.
- One class per file for substantial classes. Small helper classes or dataclasses can share a file.
- Order within a module: imports, constants, type aliases, exceptions, classes, functions, `if __name__ == "__main__"`.

## Naming Conventions

- Modules and packages: `snake_case` (e.g., `speed_classifier.py`).
- Classes: `PascalCase` (e.g., `PipelineEngine`).
- Functions and variables: `snake_case`.
- Constants: `UPPER_SNAKE_CASE`.
- Private members: single leading underscore `_internal_method`.
- Avoid abbreviations unless universally understood (`id`, `url`, `api`).

## Error Handling

- Use specific exception types. Never catch bare `Exception` unless re-raising or logging at a top-level boundary.
- Define custom exceptions in a dedicated `exceptions.py` module per package.
- Always include context in error messages: what failed, what was expected, what was received.
- Use `raise ... from e` to preserve exception chains.
- Log errors at the point of handling, not at the point of raising.

## Input Validation

- Validate all external input at system boundaries (CLI args, API payloads, config files, environment variables).
- Use Pydantic models or explicit validation functions for structured input.
- Fail fast with clear error messages. Do not silently coerce invalid data.
- Internal function calls between trusted modules do not need redundant validation -- rely on type hints and tests.

## Function Design

- Functions should do one thing. If a docstring needs "and" to describe it, split it.
- Maximum 5 parameters. Use a config object or dataclass for more.
- Return early to reduce nesting. Avoid deep conditional chains.
- Pure functions are preferred. Side effects should be explicit and isolated.

## Comments and Documentation

- Docstrings on all public functions and classes (Google style).
- Comments explain "why," not "what." The code should be self-documenting for "what."
- TODO comments must include a ticket reference or author: `# TODO(aman): migrate to async`.
- Remove commented-out code. Use version control instead.

## Imports

- Standard library, then third-party, then local -- separated by blank lines.
- Use absolute imports for cross-package references.
- Use `from __future__ import annotations` for forward references.
- Avoid wildcard imports (`from module import *`).
