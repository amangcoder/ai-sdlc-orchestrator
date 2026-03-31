# Security

## Pre-Commit Security Checklist

Before every commit, verify:

1. **No hardcoded secrets.** No API keys, tokens, passwords, or connection strings in source code. Use environment variables or `~/.orchestrator.env`.
2. **Input validation.** All user-supplied input (CLI args, feature requests, config values) is validated and sanitized before use.
3. **No SQL injection.** Use parameterized queries exclusively. Never interpolate user input into query strings.
4. **No XSS.** If generating any HTML output (reports, dashboards), escape all dynamic content.
5. **Auth checks.** Any endpoint or function that accesses protected resources verifies authorization.
6. **Rate limiting.** API-calling code respects rate limits and implements exponential backoff.
7. **No sensitive data in logs.** Scrub API keys, tokens, and PII before logging. Log request IDs, not payloads.

## Secret Management

- Store secrets in `~/.orchestrator.env` with `chmod 600`.
- Never pass secrets as CLI arguments (visible in process listings).
- Use `--env-file` for container runs.
- The `.gitignore` must include `*.env`, `.env*`, and `~/.orchestrator.env` patterns.

## Secret Rotation Protocol

When a secret is suspected compromised:

1. Rotate the key immediately at the provider (Anthropic dashboard).
2. Update `~/.orchestrator.env` with the new key.
3. Audit git history: `git log --all -p -- '*.env' '*.key' '*.pem'`.
4. If found in history, consider the key fully compromised regardless of branch.
5. Run `git filter-branch` or BFG Repo Cleaner to purge from history if committed.

## Dependency Security

- Pin all dependencies to exact versions in `pyproject.toml`.
- Run `pip audit` or `safety check` before releases.
- Review changelogs before upgrading dependencies.
- No dependencies with known CVEs in production.

## Container Security

- Containers run as non-root (`uid=1000`).
- Read-only root filesystem (`--read-only`).
- All capabilities dropped (`--cap-drop ALL`).
- Network egress restricted to `api.anthropic.com:443` only.
- See `CLAUDE.md` for the full security verification checklist.

## Code Injection Prevention

- Never use `eval()`, `exec()`, or `compile()` with user-supplied input.
- Never use `pickle.loads()` on untrusted data.
- Never pass unsanitized input to `subprocess.run()` or `os.system()`.
- Use `shlex.quote()` when constructing shell commands.
- Validate file paths to prevent directory traversal (`..` sequences).
