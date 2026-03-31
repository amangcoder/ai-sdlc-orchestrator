# Python Style

## PEP 8 Compliance

- Follow PEP 8 for all Python code. Use a formatter (Black) and linter (Ruff) to enforce.
- Line length: 88 characters (Black default).
- Use 4 spaces for indentation. Never tabs.

## Type Hints

Type hints are mandatory on all public functions and methods:

```python
def classify_speed(feature_request: str, context: CodebaseContext | None = None) -> SpeedMode:
    ...
```

- Use `X | Y` union syntax (Python 3.10+), not `Union[X, Y]`.
- Use `from __future__ import annotations` for forward references.
- Use `None` return type explicitly: `-> None`.
- Annotate instance variables in `__init__` or use dataclass fields.

## Data Classes

Prefer dataclasses or Pydantic models over plain dicts for structured data:

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class PhaseResult:
    phase: str
    artifacts: list[Path]
    duration_seconds: float
    token_usage: int = 0
```

- Use `frozen=True` by default for immutability.
- Use `field(default_factory=list)` for mutable defaults.
- Use Pydantic `BaseModel` when you need validation, serialization, or schema generation.

## Async/Await

- Use `async def` for any function that performs I/O (API calls, file operations, subprocess).
- Use `asyncio.gather()` for concurrent operations.
- Never mix `asyncio` with synchronous blocking calls. Use `asyncio.to_thread()` for CPU-bound work.
- Use `async with` for context managers that manage async resources.

## String Formatting

- Use f-strings for all string interpolation: `f"Phase {phase.name} completed in {duration:.2f}s"`.
- Use `%` formatting only in logging calls for lazy evaluation: `logger.debug("Result: %s", result)`.
- Never use `.format()` or `+` concatenation for building strings.

## Constants and Enums

- Use `enum.Enum` or `enum.StrEnum` for fixed sets of values:

```python
class SpeedMode(StrEnum):
    TURBO = "turbo"
    STANDARD = "standard"
    THOROUGH = "thorough"
    PARANOID = "paranoid"
```

## Exception Handling

```python
try:
    result = await api_client.call(prompt)
except anthropic.RateLimitError as e:
    logger.warning("Rate limited, retrying: %s", e)
    await asyncio.sleep(backoff)
    result = await api_client.call(prompt)
except anthropic.APIError as e:
    raise OrchestratorError(f"API call failed for phase {phase}") from e
```

## Logging

- Use the `logging` module, never `print()` for operational output.
- Use structured logging with key-value context where possible.
- Log levels: DEBUG for internals, INFO for phase transitions, WARNING for recoverable issues, ERROR for failures.
