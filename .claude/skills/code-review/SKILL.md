---
name: code-review
description: Review changed files for security, quality, and pattern violations with confidence-based filtering
---

Review code changes (from git diff) for security vulnerabilities, code quality issues, and pattern violations. Only reports issues above 80% confidence. Categorizes findings by severity and blocks merge recommendation on CRITICAL issues.

## When to Activate

- User says "review", "code review", "review my changes", "check this code"
- Before creating a PR or merging a branch
- After a large refactor to catch regressions

## Steps

1. **Collect changes** -- Run `git diff` (or `git diff --cached` for staged changes, or `git diff main...HEAD` for branch changes). Identify all modified files.
2. **Read full context** -- For each changed file, read the complete file (not just the diff) to understand surrounding context.
3. **Analyze each change** against these categories:
   - **Security**: Hardcoded secrets, injection vulnerabilities, unsafe deserialization, path traversal, missing input validation
   - **Correctness**: Logic errors, off-by-one, race conditions, unhandled exceptions, null/None dereference
   - **Performance**: N+1 queries, unnecessary copies, blocking calls in async context, unbounded collections
   - **Maintainability**: Dead code, duplicated logic, overly complex functions (>50 lines), missing type hints
   - **Conventions**: Naming inconsistencies, import ordering, docstring format, project pattern violations
4. **Filter by confidence** -- Only report issues where you are >80% confident it is a real problem. Discard speculative or stylistic nitpicks.
5. **Categorize severity**:
   - **CRITICAL** -- Security vulnerability, data loss risk, or crash in production path
   - **HIGH** -- Bug that will manifest under normal usage
   - **MEDIUM** -- Code smell, maintainability concern, or minor bug in edge case
   - **LOW** -- Style, naming, or minor improvement suggestion
6. **Output structured report**:
   ```
   ## Code Review Report

   ### CRITICAL (blocks merge)
   - [ ] `src/engine.py:42` — SQL injection via f-string interpolation [confidence: 95%]

   ### HIGH
   - [ ] `src/agents.py:128` — Unhandled KeyError when config key missing [confidence: 85%]

   ### MEDIUM
   - [ ] `src/models.py:55` — Duplicate validation logic, extract to helper [confidence: 82%]

   ### LOW
   - [ ] `src/observability.py:10` — Unused import `logging` [confidence: 90%]

   ## Verdict: BLOCK / APPROVE / APPROVE WITH COMMENTS
   ```
7. **Store findings in Ruflo** -- Persist review results for trend tracking:
   ```
   Call mcp__claude-flow__memory_store with:
   - key: "review/<branch-or-date-slug>"
   - namespace: "orchestrator-reviews"
   - value: {verdict, critical_count, high_count, findings_summary}
   - tags: ["review", verdict]
   - upsert: true
   ```
   Search past reviews first with `mcp__claude-flow__memory_search` query "recurring issues" to flag repeat offenders.
8. **Verdict**:
   - **BLOCK** if any CRITICAL issues exist
   - **APPROVE WITH COMMENTS** if HIGH or MEDIUM issues exist
   - **APPROVE** if only LOW issues or no issues

## Options

- `--staged` -- Review only staged changes (`git diff --cached`)
- `--branch <base>` -- Review all changes since diverging from base branch
- `--security-only` -- Focus exclusively on security issues
- `--file <path>` -- Review a specific file regardless of git status

## Examples

```
/code-review
/code-review --staged
/code-review --branch main
/code-review --security-only
/code-review --file src/orchestrator/engine.py
```
