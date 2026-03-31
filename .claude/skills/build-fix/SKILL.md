---
name: build-fix
description: Diagnose and fix build/test failures by analyzing error output and applying targeted fixes
---

When tests or build fail, analyze the error output, identify the root cause, implement a fix, and re-run verification to confirm the fix works. Handles common Python errors including ImportError, SyntaxError, TypeError, and test assertion failures.

## When to Activate

- User says "fix build", "fix tests", "why is this failing", "build is broken"
- After a test or build failure during development
- When `pytest` or `mypy` or `ruff` exits with errors

## Steps

1. **Capture the failure** -- If the user provides error output, use it. Otherwise, run the failing command to reproduce:
   - `pytest tests/ -v --tb=long 2>&1` for test failures
   - `python -c "import orchestrator" 2>&1` for import errors
   - `mypy src/orchestrator/ 2>&1` for type errors
   - `ruff check src/orchestrator/ 2>&1` for lint errors

2. **Classify the error** -- Identify the error type:
   - **ImportError / ModuleNotFoundError** -- Missing dependency, circular import, wrong module path
   - **SyntaxError** -- Malformed code, missing colon/bracket/parenthesis
   - **TypeError** -- Wrong argument count, incompatible types, None where object expected
   - **AttributeError** -- Accessing attribute that does not exist, often after refactoring
   - **AssertionError / test failure** -- Expected vs actual mismatch, test needs updating or code has a bug
   - **KeyError / IndexError** -- Missing dict key or out-of-bounds access
   - **Lint violation** -- Formatting, import order, unused variable

3. **Trace the root cause** -- Read the full traceback. Identify:
   - The exact file and line number where the error originates
   - Whether the issue is in source code or test code
   - Whether this is a regression (code that previously worked) or a new feature bug

4. **Read surrounding context** -- Read the failing file and any files it imports to understand the full picture.

5. **Propose and implement fix** -- Make the minimal change that resolves the error:
   - For ImportError: fix the import path, add missing dependency, resolve circular import
   - For TypeError: fix argument count or types
   - For test failures: determine if the test or the code is wrong, fix the correct one
   - For lint: apply auto-fix where possible (`ruff check --fix`)

6. **Re-run verification** -- Run the originally failing command again to confirm the fix works.

7. **If still failing** -- Repeat steps 2-6 up to 3 times. If still failing after 3 attempts, report what was tried and what the remaining issue is.

8. **Report** -- Summarize: what failed, root cause, what was changed, verification result.

## Options

- `--error <paste>` -- Provide the error output directly instead of re-running
- `--test-only` -- Only fix test failures, do not modify source code
- `--source-only` -- Only fix source code, do not modify tests
- `--no-verify` -- Skip the re-verification step

## Examples

```
/build-fix
/build-fix --error "ImportError: cannot import name 'SpeedClassifier' from 'orchestrator.engine'"
/build-fix --test-only
```
