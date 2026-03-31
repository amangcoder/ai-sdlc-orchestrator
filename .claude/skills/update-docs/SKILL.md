---
name: update-docs
description: Sync documentation with current code behavior including docstrings, README, and CLAUDE.md
---

Scan for stale documentation -- docstrings, README sections, and CLAUDE.md -- that no longer match the current code. Update docs to reflect actual behavior and verify code examples still work.

## When to Activate

- User says "update docs", "sync docs", "docs are stale", "fix documentation"
- After a significant feature addition or API change
- Before a release to ensure docs are accurate

## Steps

1. **Inventory documentation** -- Identify all documentation files:
   - `CLAUDE.md` (project instructions)
   - `README.md` (if present)
   - `docs/` directory (if present)
   - Docstrings in `src/orchestrator/*.py`
   - Inline comments describing behavior

2. **Scan for staleness** -- For each documentation source:
   - **Docstrings**: Compare function signatures and described behavior against actual code. Flag docstrings that reference parameters that no longer exist, or miss new parameters.
   - **CLAUDE.md**: Check that CLI examples, project structure, and architecture descriptions match reality.
   - **README**: Verify install instructions, usage examples, and feature lists are current.
   - **Code examples**: Extract any code snippets from docs and verify they would work against the current codebase.

3. **Identify gaps** -- Find:
   - Public functions/classes with no docstring
   - New CLI flags or options not documented
   - New files or modules not mentioned in project structure
   - Changed default values not updated in docs

4. **Propose updates** -- Present a summary of what needs changing:
   ```
   ## Documentation Updates Needed

   ### Stale
   - `CLAUDE.md` line 45 — project structure missing `src/orchestrator/trajectory.py`
   - `src/engine.py:run()` docstring — missing `speed` parameter

   ### Missing
   - `src/orchestrator/claude_flow_bridge.py` — no module docstring
   - CLI flag `--speed auto` not documented in README

   ### Broken examples
   - `CLAUDE.md` line 30 — `orchestrate --dry-run` example uses old flag format
   ```

5. **Apply updates** -- After user confirms (or immediately if `--auto`), update each documentation source:
   - Add missing docstrings using Google-style format
   - Update CLAUDE.md sections to match current code
   - Fix broken code examples
   - Preserve existing documentation tone and style

6. **Verify** -- Run any code examples to ensure they are syntactically valid. Check that `python -c "import orchestrator"` still works after docstring changes.

7. **Report** -- Summarize: files updated, docstrings added/fixed, sections corrected.

## Options

- `--auto` -- Apply all updates without confirmation
- `--docstrings-only` -- Only update Python docstrings
- `--claude-md-only` -- Only update CLAUDE.md
- `--check` -- Report staleness without making changes (dry run)

## Examples

```
/update-docs
/update-docs --check
/update-docs --docstrings-only
/update-docs --claude-md-only
/update-docs --auto
```
