---
name: learn
description: Extract reusable patterns from the current session and save to .claude/learned/ for future reference
---

After a development session, review the work done and extract reusable patterns, error resolutions, and user preferences. Save them as small markdown files in `.claude/learned/` so future sessions can reference them.

## When to Activate

- User says "learn", "save what we learned", "remember this", "extract patterns"
- At the end of a productive session with notable discoveries
- After resolving a tricky bug that others might hit
- When the user corrects Claude's approach and the correction should persist

## Steps

1. **Review session history** -- Look at what was done in the current session:
   - Files created or modified
   - Errors encountered and how they were resolved
   - User corrections or preferences expressed
   - Patterns discovered in the codebase

2. **Categorize learnings** -- Group findings into categories:
   - **error_resolution** -- Errors encountered and their fixes (e.g., "mypy fails on Optional without None check")
   - **user_corrections** -- Times the user corrected an approach (e.g., "always use Google-style docstrings, not NumPy")
   - **project_patterns** -- Conventions discovered in this codebase (e.g., "all agent configs use YAML, not JSON")
   - **tool_usage** -- Effective tool combinations or flags discovered (e.g., "ruff check --fix handles most lint issues")

3. **Create learning files** -- For each notable learning, create a markdown file:
   - Path: `.claude/learned/<category>/<slug>.md`
   - Format:
     ```markdown
     # <Title>

     ## Context
     <When does this apply?>

     ## Pattern
     <What to do>

     ## Example
     <Concrete example from the session>

     ## Date
     <YYYY-MM-DD>
     ```
   - Use descriptive slugs: `error_resolution/circular-import-agents-engine.md`

4. **Ensure directory exists** -- Create `.claude/learned/` and category subdirectories if they do not exist.

5. **Avoid duplicates** -- Check if a similar learning already exists before creating a new file. If it exists, update it with additional context instead.

6. **Store in Ruflo memory** -- Use the claude-flow MCP tools to persist learnings for cross-session semantic search:
   ```
   For each learning, call mcp__claude-flow__memory_store with:
   - key: "learned/<category>/<slug>" (e.g., "learned/error_resolution/circular-import")
   - namespace: "orchestrator-learned"
   - value: The full learning content (context, pattern, example)
   - tags: [category, relevant keywords]
   - upsert: true
   ```
   This enables future sessions to search learnings semantically via `mcp__claude-flow__memory_search`.

7. **Report** -- Summarize what was learned and where it was saved:
   ```
   ## Learnings Saved

   - `.claude/learned/error_resolution/mypy-optional-none.md` — mypy requires explicit None checks for Optional types
   - `.claude/learned/project_patterns/yaml-config-convention.md` — all config uses YAML with SafeLoader
   - `.claude/learned/user_corrections/docstring-style.md` — use Google-style docstrings

   ## Ruflo Memory
   - Stored 3 learnings in namespace `orchestrator-learned` for semantic search
   ```

## Options

- `--category <cat>` -- Only extract learnings of a specific category
- `--dry-run` -- Show what would be saved without writing files
- `--from-error <msg>` -- Create a learning from a specific error message

## Examples

```
/learn
/learn --dry-run
/learn --category error_resolution
/learn --from-error "ImportError: circular import between engine and agents"
```
