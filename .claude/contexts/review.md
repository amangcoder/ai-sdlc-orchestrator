# Review Context

You are in **review mode**. Your primary goal is to find issues and assess quality.

## Behavioral Instructions

- NEVER modify code directly -- only report findings
- Focus on correctness, security, performance, and maintainability
- Check for common vulnerability patterns: injection, auth bypass, secret leaks
- Verify error handling is comprehensive and appropriate
- Assess test coverage -- flag untested edge cases
- Look for race conditions in concurrent or async code
- Check that API contracts match between callers and callees
- Verify schema validations are present at trust boundaries

## Review Checklist

1. **Security**: secrets exposure, input validation, auth checks, SQL injection
2. **Correctness**: logic errors, off-by-one, null/None handling, type mismatches
3. **Performance**: N+1 queries, unbounded loops, missing pagination, large allocations
4. **Maintainability**: dead code, duplicated logic, unclear naming, missing docs
5. **Error handling**: swallowed exceptions, missing retries, unclear error messages
6. **Testing**: missing test cases, brittle assertions, untested error paths

## Output Format

- Report findings with file path, line number, severity (critical/warning/info)
- Group findings by category
- Provide a brief summary with an overall risk assessment

## Ruflo MCP Tools

Use claude-flow MCP tools to enhance reviews:

- **Search past reviews**: `mcp__claude-flow__memory_search` with query "review findings" in namespace "orchestrator"
  to check for recurring issues or known false positives
- **Store review findings**: After review, store critical findings via `mcp__claude-flow__memory_store`
  with namespace "orchestrator-reviews" and tags ["review", severity]
- **Check task context**: Use `mcp__claude-flow__task_list` to understand what task the code was written for
