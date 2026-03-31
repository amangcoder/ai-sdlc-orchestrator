# Design Patterns

## Repository Pattern

Use the repository pattern to abstract data access for artifacts and configuration:

- `ArtifactRepository` -- load, save, list, and validate artifacts.
- `ConfigRepository` -- load and merge configuration from YAML, environment, and CLI overrides.
- Repositories are the only code that touches the filesystem for persistence. Business logic never does direct file I/O.

## Service Layer

Each orchestrator capability is encapsulated in a service:

- `PipelineService` -- orchestrates the full SDLC pipeline.
- `ClassificationService` -- determines speed mode from feature requests.
- `ValidationService` -- validates artifacts against JSON schemas.
- Services depend on repositories and other services, never on CLI or framework code.
- Services are stateless. Session state lives in the `RunContext` object passed through the pipeline.

## Strategy Pattern

Use the strategy pattern for behaviors that vary by mode or configuration:

- `SpeedStrategy` -- different pipeline configurations for turbo/standard/thorough/paranoid.
- `IsolationStrategy` -- worktree vs. branch vs. in-place isolation for parallel agents.
- `ModelStrategy` -- model selection logic per agent role and task complexity.

Strategies are selected at pipeline initialization and remain fixed for the duration of a run.

## Builder Pattern

Use builders for constructing complex objects with many optional parameters:

- `PipelineBuilder` -- fluent API for configuring a pipeline run.
- `AgentBuilder` -- configure agent role, model, input artifacts, and isolation mode.

## Skeleton Project Approach

When creating new modules or features:

1. **Define the interface first** -- write the abstract base class or Protocol.
2. **Write the tests** -- test against the interface, not the implementation.
3. **Implement the skeleton** -- methods that raise `NotImplementedError` or return stubs.
4. **Fill in the logic** -- implement one method at a time, running tests after each.
5. **Refactor** -- clean up once all tests pass.

## Configuration Cascade

Configuration merges in this priority order (highest wins):

1. CLI arguments (`--speed paranoid`)
2. Environment variables (`ORCHESTRATOR_SPEED=paranoid`)
3. Project config (`config/default.yaml`)
4. Built-in defaults (hardcoded in source)

## Error Recovery Patterns

- **Retry with backoff** for transient failures (network, rate limits).
- **Circuit breaker** for repeated failures to the same service.
- **Crash recovery** via checkpointing -- resume from the last successful phase.
- **Heartbeat monitoring** to detect hung agents.
