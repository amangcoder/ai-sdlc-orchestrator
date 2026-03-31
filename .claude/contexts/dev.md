# Development Context

You are in **development mode**. Your primary goal is to write clean, working code.

## Behavioral Instructions

- Write clean, maintainable code that follows existing project conventions
- Practice TDD: write or update tests before or alongside implementation changes
- After making changes, verify correctness by running relevant tests
- Keep functions small and focused on a single responsibility
- Use meaningful variable and function names that convey intent
- Add type hints to all function signatures (Python) or TypeScript types where applicable
- Handle errors explicitly -- never silently swallow exceptions
- Prefer editing existing files over creating new ones
- Follow the existing import style and module organization
- When adding dependencies, check if an existing dependency already covers the need

## Workflow

1. Understand the requirement fully before writing code
2. Identify which files need changes
3. Write or update tests for the expected behavior
4. Implement the changes
5. Run tests to verify correctness
6. Review your own diff for obvious issues before finishing

## Quality Gates

- All new functions must have docstrings
- No hardcoded secrets or credentials
- No `print()` statements in production code -- use logging
- Imports must be organized (stdlib, third-party, local)

## Ruflo MCP Tools

Use claude-flow MCP tools throughout development:

- **Before starting work**: Search memory for relevant past learnings:
  `mcp__claude-flow__memory_search` with query describing what you're about to build
- **Track tasks**: Create tasks for each unit of work via `mcp__claude-flow__task_create`
  and update status as you progress via `mcp__claude-flow__task_update`
- **Store decisions**: When making architectural decisions, store them in memory:
  `mcp__claude-flow__memory_store` with namespace "orchestrator" and tags ["decision"]
- **Save session**: Before finishing, save session state via `mcp__claude-flow__session_save`
  so the next session can pick up context
