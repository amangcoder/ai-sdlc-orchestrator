# Python Testing

## pytest Configuration

Configure in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "e2e: end-to-end tests (deselect with '-m not e2e')",
    "slow: tests that take more than 10 seconds",
]
asyncio_mode = "auto"
```

## Fixtures

### Scope and Organization

- Define shared fixtures in `tests/conftest.py`.
- Define module-specific fixtures in the test file or a local `conftest.py`.
- Use `session` scope for expensive setup (Docker containers, database connections).
- Use `function` scope (default) for test isolation.

### Common Fixtures

```python
@pytest.fixture
def run_context(tmp_path: Path) -> RunContext:
    """Provide a fresh RunContext with a temp artifact directory."""
    return RunContext(
        run_id="test-001",
        artifact_dir=tmp_path / "artifacts",
        speed_mode=SpeedMode.STANDARD,
    )

@pytest.fixture
def mock_api_client(mocker):
    """Mock the Anthropic API client."""
    return mocker.patch("orchestrator.engine.AnthropicClient")
```

## Parametrize

Use `@pytest.mark.parametrize` for testing multiple inputs against the same logic:

```python
@pytest.mark.parametrize("request_text,expected_speed", [
    ("Fix typo in README", SpeedMode.TURBO),
    ("Add user authentication with OAuth2", SpeedMode.THOROUGH),
    ("Implement Stripe payment processing", SpeedMode.PARANOID),
])
def test_speed_classification(request_text: str, expected_speed: SpeedMode):
    result = classify_speed(request_text)
    assert result == expected_speed
```

## Mocking Patterns

- Use `pytest-mock` (`mocker` fixture) for patching.
- Patch at the point of use, not the point of definition: `mocker.patch("orchestrator.engine.api_call")`, not `mocker.patch("anthropic.Client.call")`.
- Use `mocker.AsyncMock()` for async functions.
- Avoid over-mocking. If you are mocking more than 3 things, the code under test may need refactoring.

## Async Tests

```python
@pytest.mark.asyncio
async def test_pipeline_runs_all_phases(run_context, mock_api_client):
    engine = PipelineEngine(run_context)
    result = await engine.run("Build a todo app")
    assert result.status == "completed"
    assert len(result.phases) == 6
```

## conftest.py Organization

```
tests/
  conftest.py            # Shared fixtures: run_context, mock clients, temp dirs
  unit/
    conftest.py          # Unit-specific: isolated mocks, fast fixtures
    test_engine.py
    test_agents.py
  integration/
    conftest.py          # Integration-specific: real config, real schemas
    test_pipeline.py
  e2e/
    conftest.py          # E2E-specific: CLI runners, Docker fixtures
    test_full_run.py
  fixtures/
    sample_artifacts/    # JSON artifact samples for testing
    sample_configs/      # YAML config samples
```

## Coverage Configuration

```toml
[tool.coverage.run]
source = ["src/orchestrator"]
omit = ["*/__main__.py", "*/cli.py"]

[tool.coverage.report]
fail_under = 80
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
]
```

## Assertion Patterns

- Use plain `assert` statements, not `unittest` methods.
- For floating point: `assert result == pytest.approx(expected, rel=1e-3)`.
- For exceptions: `with pytest.raises(ValidationError, match="invalid schema")`.
- For logs: use the `caplog` fixture.
- For CLI output: use `capsys` or `click.testing.CliRunner`.
