---
name: verify
description: Run a 6-phase verification loop (build, types, lint, test, security, diff) with structured PASS/FAIL report
---

Execute a comprehensive verification pipeline across six phases. Produce a structured report with PASS/FAIL per phase and an overall READY/NOT READY verdict.

## When to Activate

- User says "verify", "check everything", "run all checks", "is this ready"
- Before committing, pushing, or creating a PR
- After implementing a feature to confirm nothing is broken

## Steps

1. **Phase 1: Build**
   - Run `pip install -e ".[dev]"` (or verify the package is already installed)
   - Check for import errors: `python -c "import orchestrator"`
   - Result: PASS if no errors, FAIL with error output

2. **Phase 2: Type Check**
   - Run `mypy src/orchestrator/ --ignore-missing-imports`
   - Result: PASS if exit code 0 or only notes/warnings, FAIL if errors

3. **Phase 3: Lint**
   - Run `ruff check src/orchestrator/`
   - Run `ruff format --check src/orchestrator/`
   - Result: PASS if no violations, FAIL with violation list

4. **Phase 4: Test**
   - Run `pytest tests/ -v --tb=short`
   - Result: PASS if all tests pass, FAIL with failure summary

5. **Phase 5: Security Scan**
   - Scan for hardcoded secrets (grep for patterns: API keys, passwords, tokens)
   - Check for dangerous patterns: `eval(`, `exec(`, `pickle.loads(`, `subprocess.call(shell=True`
   - If `pip-audit` is available, run `pip-audit`
   - Result: PASS if no findings, FAIL with finding list

6. **Phase 6: Diff Review**
   - Run `git diff --stat` to summarize changes
   - Check for accidentally committed files (`.env`, `__pycache__`, `.pyc`, large binaries)
   - Verify no merge conflict markers remain (`<<<<<<<`, `=======`, `>>>>>>>`)
   - Result: PASS if clean, FAIL with issues

7. **Produce report**:
   ```
   ## Verification Report

   | Phase          | Status | Details          |
   |----------------|--------|------------------|
   | Build          | PASS   |                  |
   | Type Check     | PASS   |                  |
   | Lint           | FAIL   | 3 violations     |
   | Test           | PASS   | 42 passed        |
   | Security Scan  | PASS   |                  |
   | Diff Review    | PASS   |                  |

   ## Verdict: NOT READY
   Failing phases: Lint
   ```

8. **Verdict**: READY only if all 6 phases PASS. NOT READY if any phase FAILs.

## Options

- `--fix` -- Attempt to auto-fix lint and format issues (run `ruff check --fix` and `ruff format`)
- `--skip <phase>` -- Skip a specific phase (e.g., `--skip security`)
- `--fast` -- Skip Phase 2 (type check) and Phase 5 (security scan) for speed

## Examples

```
/verify
/verify --fix
/verify --fast
/verify --skip security
```
