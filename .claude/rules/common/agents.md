# Agents

## Subagent Delegation Guidelines

The orchestrator coordinates five core agent roles: PM, Architect, Engineer, QA, and Reviewer. Each agent operates within a bounded context and communicates through schema-validated artifacts.

## When to Spawn a Subagent

Spawn a dedicated subagent when:

- The task requires a **different model tier** (e.g., security review needs Opus while implementation uses Sonnet).
- The task can run **in parallel** with other work (e.g., multiple engineers on separate files).
- The task requires **isolation** (e.g., engineers using git worktrees to avoid file conflicts).
- The task has a **distinct output artifact** with its own schema.

## When to Inline

Keep work in the current agent when:

- The task is a small subtask that does not justify spawn overhead.
- The output feeds directly into the next line of the current agent's logic.
- The task shares the same model tier and context as the current agent.

## Model Routing by Role

| Agent Role | Default Model | Rationale |
|------------|---------------|-----------|
| PM | Sonnet | Planning and requirements extraction |
| Architect | Sonnet / Opus | Design decisions; escalate to Opus for complex systems |
| Engineer | Sonnet | Code generation and implementation |
| QA | Sonnet | Test generation and validation |
| Reviewer | Opus | Deep code review requiring multi-file reasoning |
| Security Reviewer | Opus | Security analysis requiring adversarial thinking |
| Speed Classifier | Haiku | Fast, cheap classification task |
| Doc Generator | Haiku | Templated output, low complexity |

## Agent Communication

- Agents communicate exclusively through **artifacts** -- JSON files validated against schemas in `src/schemas/`.
- No agent reads another agent's internal state or conversation history.
- Phase transitions are gated by artifact validation. A phase does not start until all input artifacts pass schema validation.

## Parallel Execution

- Engineer agents run in parallel using `isolation: worktree` to prevent file conflicts.
- Each parallel agent gets its own git worktree, merged back after completion.
- Set a maximum concurrency limit to avoid resource exhaustion (default: 4 parallel agents).
- If any parallel agent fails, decide per-policy whether to fail-fast or let others complete.

## Agent Lifecycle

1. **Spawn** -- Create the agent with its role, model, and input artifacts.
2. **Execute** -- The agent runs its task, producing output artifacts.
3. **Validate** -- Output artifacts are schema-validated.
4. **Checkpoint** -- Valid artifacts are saved to the run directory.
5. **Terminate** -- The agent's session ends. No state carries over except through artifacts.

## Error Handling

- If an agent fails, capture the error in a structured error artifact.
- Retry transient failures (API timeouts, rate limits) with exponential backoff.
- Do not retry logic errors (invalid output, schema validation failures). Fail the phase instead.
- The orchestrator engine decides whether to retry, skip, or abort based on the phase and error type.
