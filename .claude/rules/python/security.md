# Python Security

## Pickle Deserialization

- **Never use `pickle.loads()` or `pickle.load()` on untrusted data.** Pickle can execute arbitrary code during deserialization.
- Use JSON, YAML (with `safe_load`), or MessagePack for serialization instead.
- If pickle is unavoidable for internal caching, validate the source and use `hmac` to verify integrity.

## eval/exec Avoidance

- **Never use `eval()`, `exec()`, or `compile()` with any input derived from users or external sources.**
- For dynamic attribute access, use `getattr()` with a whitelist of allowed attributes.
- For expression evaluation, use `ast.literal_eval()` which only allows literal Python values.
- For template rendering, use Jinja2 with sandboxed environment, never f-strings with user input.

## Subprocess Injection

- **Never pass unsanitized input to `subprocess.run()` with `shell=True`.**
- Always use the list form: `subprocess.run(["git", "log", "--oneline"], check=True)`.
- If shell features are needed, use `shlex.quote()` on all user-supplied arguments.
- Validate command arguments against an allowlist when possible.

```python
# DANGEROUS - shell injection
subprocess.run(f"git checkout {branch_name}", shell=True)

# SAFE - list form
subprocess.run(["git", "checkout", branch_name], check=True)
```

## Path Traversal

- **Validate all file paths to prevent directory traversal attacks.**
- Use `Path.resolve()` and verify the resolved path starts with the expected base directory.
- Never concatenate user input directly into file paths.

```python
def safe_artifact_path(base_dir: Path, filename: str) -> Path:
    resolved = (base_dir / filename).resolve()
    if not resolved.is_relative_to(base_dir.resolve()):
        raise ValueError(f"Path traversal detected: {filename}")
    return resolved
```

## Server-Side Template Injection (SSTI)

- If using Jinja2 or any template engine, use the `SandboxedEnvironment`.
- Never render user input as a template. User input should only be passed as template variables.
- Disable autoescape only when explicitly generating non-HTML output.

## YAML Safety

- **Always use `yaml.safe_load()`, never `yaml.load()`** (which can execute arbitrary Python).
- For writing YAML, use `yaml.safe_dump()`.

## Secrets in Memory

- Minimize the lifetime of secrets in memory. Do not store API keys in long-lived objects.
- Use `os.environ.get()` to read secrets at the point of use, not at import time.
- Never include secrets in exception messages, log output, or error artifacts.

## HTTP Client Security

- Validate URLs before making requests. Reject private IP ranges to prevent SSRF.
- Set explicit timeouts on all HTTP requests.
- Verify TLS certificates. Never set `verify=False` in production.

## Dependency Safety

- Run `pip audit` in CI to catch known vulnerabilities.
- Review new dependencies for supply chain risk before adding.
- Prefer well-maintained packages with active security response teams.
- Avoid dependencies that require native compilation unless strictly necessary.

## File Permissions

- Config files with secrets: `chmod 600` (owner read/write only).
- Executable scripts: `chmod 755`.
- Generated artifacts: `chmod 644` (owner read/write, others read).
- Never create world-writable files.
