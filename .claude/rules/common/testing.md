# Testing

## Coverage Target

- **Minimum 80% line coverage** on every PR. No exceptions for "it's just a config change."
- Coverage is measured per-package. A new module must ship with tests.
- Use `pytest --cov=src/orchestrator --cov-report=term-missing` to verify locally before pushing.

## TDD Workflow

Follow RED-GREEN-REFACTOR strictly for new features and bug fixes:

1. **RED** -- Write a failing test that describes the desired behavior. Commit it.
2. **GREEN** -- Write the minimum code to make the test pass. No gold-plating.
3. **REFACTOR** -- Clean up the implementation while keeping tests green.

For bug fixes: write a test that reproduces the bug first, then fix it.

## Test Types

### Unit Tests (`tests/unit/`)

- Test a single function or class in isolation.
- Mock all external dependencies (API calls, filesystem, database).
- Should run in under 1 second per test.
- Name pattern: `test_<function_name>_<scenario>_<expected_result>`.

### Integration Tests (`tests/integration/`)

- Test interactions between two or more real components.
- May use real file I/O, real config parsing, real schema validation.
- Mock only external services (Anthropic API, Docker).
- Acceptable runtime: up to 10 seconds per test.

### End-to-End Tests (`tests/e2e/`)

- Test the full pipeline from CLI invocation to artifact output.
- Use `--dry-run` mode to avoid real API calls in CI.
- Mark with `@pytest.mark.e2e` and exclude from default test runs.

## Test Structure

Every test follows Arrange-Act-Assert:

```python
def test_speed_classifier_returns_turbo_for_typo_fix():
    # Arrange
    request = "Fix typo in error message"

    # Act
    result = classify_speed(request)

    # Assert
    assert result == SpeedMode.TURBO
```

## What Not to Test

- Private methods directly (test through the public interface).
- Third-party library internals.
- Exact log message strings (test that logging occurs, not the exact wording).
- Implementation details that may change during refactoring.

## Test Data

- Use factories or builders for complex test objects, not raw dict literals.
- Keep fixture data in `tests/fixtures/` as JSON or YAML files.
- Never use production data or real API keys in tests.
