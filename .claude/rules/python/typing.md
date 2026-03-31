# Python Typing

## Type Annotations

Every public function, method, and class attribute must have type annotations:

```python
from __future__ import annotations

def run_phase(
    phase: Phase,
    context: RunContext,
    artifacts: list[Artifact],
    timeout: float = 30.0,
) -> PhaseResult:
    ...
```

## Union Types

Use the `X | Y` syntax (Python 3.10+):

```python
def get_config(key: str) -> str | int | None:
    ...
```

## Protocol (Structural Subtyping)

Use `Protocol` for duck typing instead of ABCs when you only care about interface shape:

```python
from typing import Protocol

class ArtifactStore(Protocol):
    def save(self, artifact: Artifact) -> Path: ...
    def load(self, artifact_id: str) -> Artifact: ...
    def list(self, phase: str) -> list[Artifact]: ...
```

Any class implementing `save`, `load`, and `list` with matching signatures satisfies this protocol without explicit inheritance.

## TypeVar and Generic

Use `TypeVar` for generic functions and classes:

```python
from typing import TypeVar, Generic

T = TypeVar("T")

class Repository(Generic[T]):
    def __init__(self, model_class: type[T]) -> None:
        self._model_class = model_class

    def get(self, id: str) -> T | None:
        ...

    def save(self, entity: T) -> None:
        ...
```

Use bound TypeVar when constraining the type:

```python
ArtifactT = TypeVar("ArtifactT", bound=BaseArtifact)

def validate(artifact: ArtifactT) -> ArtifactT:
    ...
```

## overload

Use `@overload` when a function's return type depends on input types:

```python
from typing import overload

@overload
def parse_config(raw: str) -> dict[str, Any]: ...
@overload
def parse_config(raw: Path) -> dict[str, Any]: ...
@overload
def parse_config(raw: dict[str, Any]) -> dict[str, Any]: ...

def parse_config(raw: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, Path):
        raw = raw.read_text()
    return yaml.safe_load(raw)
```

## TYPE_CHECKING Guard

Use `TYPE_CHECKING` to avoid circular imports and runtime import costs for type-only imports:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.engine import PipelineEngine
    from orchestrator.models import RunContext

class Agent:
    def __init__(self, engine: PipelineEngine, context: RunContext) -> None:
        ...
```

This import is erased at runtime but available to mypy and other type checkers.

## TypeAlias

Use explicit type aliases for complex types:

```python
from typing import TypeAlias

ArtifactMap: TypeAlias = dict[str, list[Artifact]]
PhaseCallback: TypeAlias = Callable[[Phase, RunContext], Awaitable[PhaseResult]]
```

## Common Patterns

### Optional parameters

```python
# Prefer X | None over Optional[X]
def find_artifact(name: str, phase: str | None = None) -> Artifact | None:
    ...
```

### Callable types

```python
from collections.abc import Callable, Awaitable

# Sync callback
Validator = Callable[[Artifact], bool]

# Async callback
AsyncValidator = Callable[[Artifact], Awaitable[bool]]
```

### Collection types

```python
# Use lowercase built-in types (Python 3.9+)
items: list[str]
mapping: dict[str, int]
unique: set[Path]
immutable: tuple[str, ...]
```

## mypy Configuration

```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_ignores = true
disallow_untyped_defs = true
```

Run `mypy src/` before every commit. Zero mypy errors is the standard.
