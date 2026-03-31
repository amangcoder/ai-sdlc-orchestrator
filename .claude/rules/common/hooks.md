# Hooks

## Hook Architecture

Hooks are lifecycle interceptors that run at specific points during Claude Code execution. They enable quality gates, observability, and policy enforcement without modifying core logic.

## Hook Types

### PreToolUse

Runs **before** a tool is invoked. Use for:

- **Input validation** -- reject malformed or dangerous tool inputs before execution.
- **Policy enforcement** -- block disallowed file paths, prevent writes to protected directories.
- **Logging** -- record what tool is about to be called and with what arguments.
- **Rate limiting** -- throttle expensive operations.

Return `{ "decision": "block", "reason": "..." }` to prevent the tool call.

### PostToolUse

Runs **after** a tool completes. Use for:

- **Output validation** -- verify tool output meets expectations (e.g., schema validation on generated artifacts).
- **Metrics collection** -- record latency, token usage, success/failure.
- **Side effects** -- trigger notifications, update dashboards, write audit logs.
- **Artifact capture** -- save intermediate outputs for debugging.

### Stop

Runs **when the agent signals completion**. Use for:

- **Final quality gates** -- verify all required artifacts exist and are valid before declaring success.
- **Summary generation** -- produce a run report or cost summary.
- **Cleanup** -- remove temporary files, close connections.
- **Notification** -- alert on completion or failure.

## When to Use Which Hook

| Scenario | Hook Type |
|----------|-----------|
| Validate schema before writing artifact | PreToolUse |
| Block writes outside workspace directory | PreToolUse |
| Measure API call latency | PostToolUse |
| Validate generated code compiles | PostToolUse |
| Ensure all phases produced artifacts | Stop |
| Generate cost report | Stop |
| Prevent accidental deletion of source files | PreToolUse |
| Log agent decision rationale | PostToolUse |

## Implementation Guidelines

- Hooks must be fast. Target under 100ms execution time.
- Hooks must not throw exceptions that crash the pipeline. Catch and log errors internally.
- Hooks should be stateless when possible. If state is needed, use the run's artifact directory.
- Define hooks in `.claude/hooks/` as executable scripts or Python modules.
- Configure hook bindings in `config/default.yaml` under the `hooks:` key.

## Hook Configuration

```yaml
hooks:
  pre_tool_use:
    - path: .claude/hooks/validate_input.py
      tools: ["Write", "Edit"]
  post_tool_use:
    - path: .claude/hooks/capture_metrics.py
      tools: ["*"]
  stop:
    - path: .claude/hooks/quality_gate.py
```

## Testing Hooks

- Unit test hooks in isolation with mock tool inputs/outputs.
- Integration test hooks by running the pipeline in `--dry-run` mode.
- Never test hooks against live API calls in CI.
