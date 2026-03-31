---
name: tdd
description: Test-driven development enforcing RED-GREEN-REFACTOR cycle with pytest
---

Implement features using strict test-driven development. Write failing tests first, implement minimal code to pass, then refactor. Uses pytest with 80%+ coverage target.

## When to Activate

- User says "tdd", "test first", "test-driven", "write tests first"
- When implementing a new function, class, or module where correctness matters
- When fixing a bug (write a test that reproduces it first)

## Steps

1. **RED -- Write failing tests first**
   - Create or update the test file (e.g., `tests/test_<module>.py`)
   - Write test cases covering:
     - Happy path (expected inputs and outputs)
     - Edge cases (empty inputs, None, boundary values)
     - Error cases (invalid inputs, expected exceptions)
   - Use descriptive test names: `test_<function>_<scenario>_<expected_result>`
   - Run `pytest <test_file> -v` to confirm all new tests FAIL (red)

2. **GREEN -- Write minimal implementation**
   - Write the simplest code that makes all tests pass
   - Do NOT optimize or refactor yet
   - Do NOT add functionality beyond what the tests require
   - Run `pytest <test_file> -v` to confirm all tests PASS (green)

3. **REFACTOR -- Clean up while green**
   - Remove duplication, improve naming, simplify logic
   - Run `pytest <test_file> -v` after each refactor step to ensure tests still pass
   - Do NOT add new behavior during refactor

4. **Coverage check**
   - Run `pytest --cov=src/orchestrator --cov-report=term-missing <test_file>`
   - If coverage is below 80%, identify untested paths and add tests (back to RED)

5. **Report** -- Summarize: tests added, coverage percentage, any gaps remaining

## Patterns

### Fixtures
```python
@pytest.fixture
def sample_config():
    return {"speed": "standard", "phases": ["pm", "architect"]}
```

### Parametrize
```python
@pytest.mark.parametrize("speed,expected_phases", [
    ("turbo", 4),
    ("standard", 6),
    ("thorough", 8),
])
def test_phase_count(speed, expected_phases):
    assert get_phase_count(speed) == expected_phases
```

### Mocking
```python
from unittest.mock import patch, MagicMock

@patch("orchestrator.engine.anthropic.Client")
def test_agent_call(mock_client):
    mock_client.return_value.messages.create.return_value = MagicMock(content="ok")
    result = run_agent("test prompt")
    assert result == "ok"
```

## Options

- `--bug <description>` -- Start by writing a test that reproduces the bug, then fix
- `--coverage-target <N>` -- Override the default 80% coverage target
- `--unit-only` -- Skip integration tests, focus on unit tests only

## Examples

```
/tdd Implement the SpeedClassifier class
/tdd --bug "Auto speed mode returns None when API key is missing"
/tdd --coverage-target 90 Add artifact validation to the engine
```
