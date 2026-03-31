---
name: plan
description: Create a phased implementation plan with risk assessment before writing any code
---

Analyze requirements, read existing code for context, assess risks, and produce a numbered step-by-step implementation plan. WAIT for user confirmation before any code is written.

## When to Activate

- User says "plan", "create a plan", "how should we implement", "break this down"
- Before any large feature or multi-file change
- When the user wants to understand impact before committing to code changes

## Steps

1. **Restate requirements** -- Summarize what the user is asking for in your own words. Ask for clarification if anything is ambiguous.
2. **Read existing code** -- Identify and read all files that will be affected. Understand current architecture, patterns, and conventions in use.
3. **Assess risks** -- For each area of change, note:
   - Breaking change risk (API contracts, schema changes, import paths)
   - Test coverage gaps
   - Security implications
   - Performance concerns
   - Dependencies that may need updating
4. **Identify dependencies** -- Map out which changes depend on others. Flag any circular dependencies or ordering constraints.
5. **Create phased plan** -- Output a numbered plan with phases:
   ```
   ## Phase 1: <title>
   - Files to modify: `src/foo.py`, `src/bar.py`
   - What changes: <description>
   - Risk: LOW/MEDIUM/HIGH — <reason>
   - Dependencies: None / Phase N

   ## Phase 2: <title>
   ...
   ```
6. **Estimate scope** -- Provide a rough estimate: number of files, lines of code, and whether tests need updating.
7. **STOP and ask for confirmation** -- Print: "Ready to proceed? Reply 'go' to start implementation, or suggest changes to the plan."
8. **Store plan in Ruflo memory** -- Save the plan for cross-session reference:
   ```
   Call mcp__claude-flow__memory_store with:
   - key: "plan/<feature-slug>"
   - namespace: "orchestrator"
   - value: The full plan (phases, risks, dependencies)
   - tags: ["plan", "active"]
   - upsert: true
   ```
   Also create a task to track implementation: `mcp__claude-flow__task_create` with type "feature".
9. **Do NOT write any code** until the user explicitly confirms.

## Options

- `--scope minimal` -- Only plan the smallest change that satisfies the requirement
- `--scope full` -- Include test updates, doc updates, and migration steps
- `--risks-only` -- Skip the plan, just output risk assessment for proposed changes

## Examples

```
/plan Add WebSocket support for real-time agent status updates
/plan --scope full Migrate from YAML config to TOML
/plan --risks-only Replace pickle serialization with JSON
```
