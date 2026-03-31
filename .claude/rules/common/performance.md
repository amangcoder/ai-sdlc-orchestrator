# Performance

## Model Selection Strategy

Route tasks to the right model tier to optimize cost and quality:

| Model | Use For | Cost Tier |
|-------|---------|-----------|
| **Haiku** | Speed classification, doc generation, git operations, simple transformations, routing decisions | Low |
| **Sonnet** | Implementation, QA, planning, code generation, test writing, standard analysis | Medium |
| **Opus** | Principal engineer review, security review, architecture decisions, complex reasoning, debate phases | High |

### Selection Rules

- Default to Sonnet for coding tasks.
- Use Haiku when the task is classification, routing, or templated output.
- Escalate to Opus only for tasks requiring deep multi-step reasoning or high-stakes decisions (security, architecture).
- Never use Opus for tasks that Sonnet handles adequately. Cost scales ~5x.

## Context Window Management

- Keep prompts focused. Do not dump entire files when a relevant snippet suffices.
- Use schema-validated artifacts for cross-phase communication instead of passing raw conversation history.
- Summarize long outputs before passing to the next phase.
- Monitor token usage per phase and alert if approaching context limits.

## Extended Thinking

- Enable extended thinking for Opus-tier tasks that benefit from chain-of-thought reasoning.
- Do not enable extended thinking for Haiku tasks (wastes tokens with minimal benefit).
- For Sonnet tasks, enable extended thinking only for complex multi-file refactors or architectural decisions.

## Pipeline Optimization

- Use speed modes (`turbo`, `standard`, `thorough`, `paranoid`) to skip unnecessary phases.
- Parallel execution for independent tasks (e.g., multiple engineer agents on separate files).
- Cache classification results within a session to avoid redundant Haiku calls.
- Fail fast: if a phase produces invalid artifacts, halt immediately rather than propagating errors downstream.

## Resource Limits

- Container memory: 8GB max.
- Container CPU: 4 cores max.
- Container PID limit: 500 processes.
- API call timeout: respect provider rate limits with exponential backoff.

## Monitoring

- Track per-phase latency and token consumption.
- Log model selection decisions for auditing.
- Alert on phases exceeding 2x their expected duration.
- Use observability hooks to capture metrics without polluting business logic.
