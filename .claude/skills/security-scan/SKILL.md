---
name: security-scan
description: OWASP-based security scan for Python codebases with dependency CVE checks
---

Perform a security-focused scan of the codebase against OWASP Top 10 categories for Python. Check for hardcoded secrets, injection vulnerabilities, unsafe deserialization, and known dependency CVEs.

## When to Activate

- User says "security scan", "security check", "audit security", "check for vulnerabilities"
- Before deploying to production
- After adding authentication, payment, or PII-handling code
- As part of the `paranoid` speed mode pipeline

## Steps

1. **Hardcoded secrets scan**
   - Search for patterns: `sk-ant-`, `sk-`, `AKIA`, `ghp_`, `password\s*=\s*["']`, `secret\s*=\s*["']`, `token\s*=\s*["']`
   - Check `.env` files are in `.gitignore`
   - Scan for private keys (`-----BEGIN.*PRIVATE KEY-----`)
   - Severity: CRITICAL

2. **SQL injection**
   - Search for f-string or %-format SQL: `execute(f"`, `execute("...%s" %`, `cursor.execute(.*+.*)`
   - Verify parameterized queries are used
   - Severity: CRITICAL

3. **Command injection**
   - Search for: `subprocess.call(.*shell=True`, `os.system(`, `os.popen(`
   - Check for user input flowing into shell commands
   - Severity: CRITICAL

4. **Path traversal**
   - Search for: `open(.*+`, file paths constructed from user input without sanitization
   - Check for `../` handling in file operations
   - Severity: HIGH

5. **Server-Side Template Injection (SSTI)**
   - Search for: `render_template_string(`, `Template(.*).render(`, `jinja2.Template(`
   - Check if user input flows into template rendering
   - Severity: HIGH

6. **Unsafe deserialization**
   - Search for: `pickle.loads(`, `pickle.load(`, `yaml.load(` without `Loader=SafeLoader`, `marshal.loads(`
   - Severity: HIGH

7. **Dangerous code execution**
   - Search for: `eval(`, `exec(`, `compile(`, `__import__(`
   - Check if any take user-controlled input
   - Severity: HIGH

8. **Dependency CVE check**
   - If `pip-audit` is available, run `pip-audit --format=json`
   - If not available, check `requirements.txt` or `pyproject.toml` for known vulnerable version ranges
   - Severity: varies by CVE

9. **Additional checks**
   - CORS misconfiguration (`Access-Control-Allow-Origin: *`)
   - Missing CSRF protection
   - Debug mode in production (`DEBUG=True`, `debug=True`)
   - Insecure HTTP usage where HTTPS expected
   - Severity: MEDIUM

10. **Output structured report**:
    ```
    ## Security Scan Report

    ### CRITICAL
    - `src/engine.py:42` — Hardcoded API key found: `sk-ant-...redacted`
    - `src/db.py:18` — SQL injection via f-string in execute()

    ### HIGH
    - `src/utils.py:30` — pickle.loads() on untrusted input

    ### MEDIUM
    - `config/default.yaml` — Debug mode enabled

    ### Dependencies
    - pyyaml 5.3.1 — CVE-2020-14343 (arbitrary code execution)

    ### Summary
    CRITICAL: 2 | HIGH: 1 | MEDIUM: 1 | Dependencies: 1
    Recommendation: DO NOT DEPLOY — fix CRITICAL issues first
    ```

## Options

- `--path <dir>` -- Scan a specific directory instead of the whole project
- `--deps-only` -- Only run dependency CVE check
- `--ignore <pattern>` -- Ignore findings matching a pattern (e.g., test files)

## Examples

```
/security-scan
/security-scan --path src/orchestrator/
/security-scan --deps-only
/security-scan --ignore "tests/"
```
