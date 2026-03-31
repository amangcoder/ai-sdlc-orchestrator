---
name: refactor-clean
description: Find and clean up dead code, unused imports, and duplicate logic while preserving behavior
---

Identify and remove dead code, unused imports, and duplicate logic. Propose minimal changes that preserve all existing behavior. Run tests before and after to verify no regressions.

## When to Activate

- User says "refactor", "clean up", "remove dead code", "deduplicate", "simplify"
- After a large feature is complete and the codebase needs tidying
- When code review flagged maintainability issues

## Steps

1. **Snapshot current state** -- Run `pytest tests/ -v --tb=short` and record the result. All tests must pass before refactoring begins. If tests fail, stop and report -- do not refactor broken code.

2. **Scan for unused imports**
   - Run `ruff check src/orchestrator/ --select F401` to find unused imports
   - List each unused import with file and line number

3. **Scan for dead code**
   - Search for functions/methods that are defined but never called
   - Search for variables that are assigned but never read
   - Search for unreachable code after `return`, `raise`, `break`, `continue`
   - Check for commented-out code blocks (more than 3 consecutive commented lines)

4. **Scan for duplicate logic**
   - Identify functions or code blocks with similar structure that could be extracted into a shared helper
   - Look for copy-pasted patterns across files

5. **Propose changes** -- Present a summary before making changes:
   ```
   ## Refactoring Proposal

   ### Unused imports to remove (auto-fix)
   - `src/engine.py:3` — `import json` (unused)

   ### Dead code to remove
   - `src/agents.py:45-60` — `_legacy_parse()` never called

   ### Duplication to extract
   - `src/engine.py:100-115` and `src/agents.py:80-95` — same validation logic, extract to `src/utils.py:validate_config()`

   Estimated impact: 3 files modified, ~40 lines removed, 1 helper added
   ```

6. **Wait for confirmation** -- Do not make changes until the user approves.

7. **Apply changes** -- Make the approved changes one category at a time:
   - First: remove unused imports
   - Second: remove dead code
   - Third: extract duplicates (if approved)

8. **Verify** -- Run `pytest tests/ -v --tb=short` again. Compare with the snapshot from step 1. All tests that passed before must still pass.

9. **Report** -- Summarize: lines removed, files modified, test results before/after.

## Options

- `--imports-only` -- Only clean up unused imports
- `--auto` -- Skip confirmation, apply all safe changes (unused imports, dead code)
- `--path <dir>` -- Limit scan to a specific directory

## Examples

```
/refactor-clean
/refactor-clean --imports-only
/refactor-clean --auto
/refactor-clean --path src/orchestrator/
```
