# Container Runtime API Reference

**Document Version:** 1.0
**Last Updated:** 2026-03-24
**Audience:** Backend engineers, platform engineers
**Source Code:** `src/orchestrator/container_runner.py`, `src/orchestrator/containerized_main.py`

## Overview

The container runtime provides Python APIs for managing isolated Docker container execution of the orchestrator. This document describes the public API, usage patterns, and extension points.

---

## Module: `orchestrator.container_runner`

### ArtifactBridgeVolume

Manages bind-mount configuration for exposing the run-specific workspace directory to containers.

#### Overview

```python
class ArtifactBridgeVolume:
    """Manages the bind-mount that exposes the run-specific workspace to the container.

    Security invariants enforced
    ----------------------------
    * `run_id` must match `^[0-9a-f]{12}$` — no slashes, dots, or shell
      metacharacters are accepted.
    * The resolved host directory is asserted to be a *child* of
      `workspace_root/runs/` before and after creation to defend against
      TOCTOU / symlink attacks.
    """
```

#### Methods

##### `prepare_host_run_dir(workspace_root: Path, run_id: str) -> Path`

Create and validate the host directory for a single run.

**Parameters:**
- `workspace_root` (Path) — Absolute path to the workspace root directory (e.g., `./workspace` resolved to an absolute path)
- `run_id` (str) — 12-character lowercase hex run identifier (e.g., `0b60f2baf4b5`)

**Returns:**
- Path — Resolved absolute path to the newly created run directory (e.g., `/path/to/workspace/runs/0b60f2baf4b5`)

**Raises:**
- `ValueError` — If `run_id` does not match `^[0-9a-f]{12}$`, or if the resolved path is not a child of `workspace_root/runs/`

**Example:**
```python
from pathlib import Path
from orchestrator.container_runner import ArtifactBridgeVolume

workspace = Path("./workspace").resolve()
run_id = "0b60f2baf4b5"

run_dir = ArtifactBridgeVolume.prepare_host_run_dir(workspace, run_id)
# Returns: /path/to/workspace/runs/0b60f2baf4b5
# Side effect: Directory is created if it doesn't exist
```

**Security Note:**
- Input validation is strict; any run_id containing `/`, `.`, or uppercase letters is rejected
- The resolved path is asserted twice (before and after creation) to defend against TOCTOU symlink attacks
- This function MUST NOT be called with user-provided run_id without first validating the format

##### `build_mount_arg(host_run_dir: Path, run_id: str) -> str`

Return the `--mount` argument string for `docker run`.

**Parameters:**
- `host_run_dir` (Path) — Absolute path to the host run directory (typically from `prepare_host_run_dir()`)
- `run_id` (str) — 12-character lowercase hex run identifier (must match the directory)

**Returns:**
- str — A Docker `--mount` flag value in the format `type=bind,source=<resolved_path>,target=/workspace/runs/<run_id>,consistency=delegated`

**Example:**
```python
from pathlib import Path
from orchestrator.container_runner import ArtifactBridgeVolume

host_dir = Path("/path/to/workspace/runs/0b60f2baf4b5")
run_id = "0b60f2baf4b5"

mount_arg = ArtifactBridgeVolume.build_mount_arg(host_dir, run_id)
# Returns: "type=bind,source=/path/to/workspace/runs/0b60f2baf4b5,target=/workspace/runs/0b60f2baf4b5,consistency=delegated"
```

**Docker Integration:**
```python
# Example docker run command assembly
docker_args = [
    "docker", "run",
    "--mount", mount_arg,
    "ai-sdlc-orchestrator:latest",
    "orchestrate", "Build a todo app"
]
```

---

### ContainerRuntime

Manages the full lifecycle of an ephemeral Docker container for orchestration runs.

#### Overview

```python
class ContainerRuntime:
    """Owns the full lifecycle of one ephemeral container per orchestration run.

    All Docker interaction goes through subprocess (not docker-py) per the
    architecture decision to avoid the large SDK dependency and to ensure
    argument lists are never passed through a shell.
    """
```

#### Constants

```python
MIN_DOCKER_VERSION = (20, 10)  # Minimum required Docker Engine version
_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[a-zA-Z0-9._-]+)?$")  # OCI image name regex
_RUN_ID_RE = re.compile(r"^[0-9a-f]{12}$")  # Run ID format validation
```

#### Methods

##### `is_docker_available() -> bool` (static)

Check that Docker Engine >= MIN_DOCKER_VERSION is reachable.

**Returns:**
- bool — `True` if Docker is available and meets the version requirement

**Raises:**
- `RuntimeError` — If Docker is not installed, the daemon is unreachable, or the version is below MIN_DOCKER_VERSION

**Example:**
```python
from orchestrator.container_runner import ContainerRuntime

try:
    if ContainerRuntime.is_docker_available():
        print("Docker is available")
except RuntimeError as e:
    print(f"Docker check failed: {e}")
```

**Implementation Details:**
- Runs `docker version --format {{.Server.Version}}` to check version
- Parses semantic version and compares against `MIN_DOCKER_VERSION`
- Error message includes required vs. found version for debugging

##### `build_run_args(run_id: str, feature_request: str, config: ContainerConfig, host_run_dir: Path, extra_cli_args: Sequence[str] | None = None) -> list[str]` (static)

Assemble the complete `docker run` argument list with all hardening flags.

**Parameters:**
- `run_id` (str) — 12-character lowercase hex run identifier
- `feature_request` (str) — The orchestration prompt (user feature request)
- `config` (ContainerConfig) — Container hardening configuration (see models.py)
- `host_run_dir` (Path) — Absolute path to the run directory (from `prepare_host_run_dir()`)
- `extra_cli_args` (Sequence[str] | None) — Additional arguments to pass to the `orchestrate` CLI inside the container

**Returns:**
- list[str] — A list of arguments suitable for passing to `subprocess.Popen(..., args=run_args)`

**Raises:**
- `ValueError` — If image name is invalid, network mode is not allowed, or other validation fails

**Example:**
```python
from pathlib import Path
from orchestrator.container_runner import ContainerRuntime
from orchestrator.models import ContainerConfig

config = ContainerConfig(
    enabled=True,
    image="ai-sdlc-orchestrator:latest",
    memory_limit="8g",
    cpu_limit=4.0,
    pids_limit=500,
    network_mode="orchestrator-net",
    seccomp_profile_path="/path/to/seccomp-profile.json",
    apparmor_profile=None,
    env_file=Path("/home/user/.orchestrator.env"),
    extra_tmpfs=[]
)

host_run_dir = Path("/path/to/workspace/runs/0b60f2baf4b5")
run_id = "0b60f2baf4b5"
feature_request = "Build a REST API for user management"

args = ContainerRuntime.build_run_args(
    run_id=run_id,
    feature_request=feature_request,
    config=config,
    host_run_dir=host_run_dir,
    extra_cli_args=["--phase", "pm"]
)

# Returns something like:
# [
#   "docker", "run", "--rm", "--read-only", "--tmpfs", "/tmp",
#   "--cap-drop", "ALL", "--memory", "8g", "--cpus", "4.0",
#   "--pids-limit", "500", "--network", "orchestrator-net",
#   "--security-opt", "seccomp=/path/to/seccomp-profile.json",
#   "--env-file", "/home/user/.orchestrator.env",
#   "--mount", "type=bind,source=...",
#   "--add-host", "api.anthropic.com=135.25.123.45",
#   "--add-host", "api.anthropic.com=135.25.123.46",
#   "--name", "orchestrator-0b60f2baf4b5",
#   "--init",
#   "ai-sdlc-orchestrator:latest",
#   "orchestrate", "Build a REST API for user management",
#   "--phase", "pm"
# ]
```

**Security Notes:**
- **No shell execution** — Arguments are a list, never a string; prevents shell injection
- **Image validation** — Rejects images with uppercase or path traversal characters
- **Network allowlist** — Only allows `orchestrator-net`, `bridge`, `none`, `host`
- **Seccomp profile** — If provided, file must exist and be readable
- **AppArmor profile** — Only added on Linux; skipped silently on macOS

**Hardening Flags Included:**
```
--rm                                      # Ephemeral container (cleaned up on exit)
--read-only                               # Read-only rootfs
--tmpfs /tmp                              # Writable in-memory /tmp
--tmpfs /home/orchestrator/.config        # Writable in-memory config dir
--cap-drop ALL                            # Drop all Linux capabilities
--memory <memory_limit>                   # Memory quota
--cpus <cpu_limit>                        # CPU quota
--pids-limit <pids_limit>                 # Process limit
--network <network_mode>                  # Network isolation
--security-opt seccomp=<path>             # Seccomp filter (if provided)
--security-opt apparmor=<profile>         # AppArmor profile (Linux only, if provided)
--env-file <path>                         # Environment file (if provided)
--mount type=bind,source=...,target=/workspace/runs/<run_id>,consistency=delegated
--add-host api.anthropic.com=<IPs>        # DNS injection for resolved IPs
--name orchestrator-<run_id>               # Container name for identification
--init                                    # Init process (tini) for zombie reaping
<image>                                   # Container image
orchestrate <feature_request> [extra_cli_args]  # Command inside container
```

##### `async run(run_id: str, feature_request: str, config: ContainerConfig, workspace_root: Path, extra_cli_args: Sequence[str] | None = None, host_run_dir: Path | None = None) -> int`

Launch the orchestration container and manage its lifecycle.

**Parameters:**
- `run_id` (str) — 12-character lowercase hex run identifier
- `feature_request` (str) — The orchestration prompt
- `config` (ContainerConfig) — Container configuration
- `workspace_root` (Path) — Workspace root path (used if `host_run_dir` is not provided)
- `extra_cli_args` (Sequence[str] | None) — Extra CLI arguments for `orchestrate`
- `host_run_dir` (Path | None) — Pre-created host run directory; if None, created via `prepare_host_run_dir()`

**Returns:**
- int — The container's exit code (0 for success, non-zero for failure)

**Raises:**
- `RuntimeError` — If Docker is unavailable or subprocess launch fails
- `ValueError` — If input validation fails

**Example:**
```python
import asyncio
from pathlib import Path
from orchestrator.container_runner import ContainerRuntime
from orchestrator.models import ContainerConfig

async def main():
    config = ContainerConfig(enabled=True)

    exit_code = await ContainerRuntime.run(
        run_id="0b60f2baf4b5",
        feature_request="Build a REST API for user management",
        config=config,
        workspace_root=Path("./workspace").resolve()
    )

    print(f"Container exited with code: {exit_code}")

    # Artifacts are now available at workspace/runs/0b60f2baf4b5/artifacts/

asyncio.run(main())
```

**Behavior:**

1. **Validate inputs** — Checks Docker availability, validates run_id, config
2. **Prepare directory** — Calls `ArtifactBridgeVolume.prepare_host_run_dir()` if needed
3. **Build arguments** — Calls `build_run_args()` to assemble docker command
4. **Log command** — Logs the full docker run command (with `shlex.join()`) for auditability
5. **Launch subprocess** — Calls `subprocess.Popen(args)` with `stdout=PIPE, stderr=PIPE`
6. **Stream output** — Background thread streams container stdout/stderr to host sys.stdout/sys.stderr in real-time
7. **Handle signals** — Registers `SIGTERM` and `SIGINT` handlers to forward to the container via `docker stop`
8. **Wait for exit** — Blocks until container exits
9. **Validate output** — Checks for FORBIDDEN_NAMES (`.bashrc`, `.ssh`, etc.) in the bind-mounted directory
10. **Return exit code** — Returns the container's exit code

**Signal Handling:**
- `SIGTERM` — Forwarded to container via `docker stop`
- `SIGINT` (Ctrl+C) — Forwarded to container via `docker stop`
- After signal, container is given 5 seconds to gracefully stop before force-kill

**Output Validation:**
- Post-exit check for suspicious files in `workspace/runs/<run_id>/`
- WARNING logged if FORBIDDEN_NAMES found (indicates possible rootkit attempt)
- Does NOT prevent container exit; validation is advisory

---

## Module: `orchestrator.containerized_main`

### main() function

Entry point for the `orchestrate-container` CLI command.

#### Synopsis

```bash
orchestrate-container "Build a REST API" [OPTIONS]
```

#### CLI Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--feature-request` | str | (required) | The orchestration prompt |
| `--container-image` | str | from config | Override `config.container.image` |
| `--env-file` | Path | from config | Override `config.container.env_file` |
| `--workspace` | Path | ./workspace | Workspace root directory |
| `--config` | Path | config/default.yaml | Config file path |
| `--run-id` | str | auto-generated | Run identifier; auto-generated if not provided |
| `--dry-run` | bool | False | Print docker run command and exit without running |
| `--no-network-isolation` | bool | False | Skip orchestrator-net; use bridge network instead |

#### Examples

**Basic usage:**
```bash
orchestrate-container "Add user authentication" --env-file ~/.orchestrator.env
```

**Preview the docker command without running:**
```bash
orchestrate-container "Add user authentication" --env-file ~/.orchestrator.env --dry-run
```

**Skip network isolation (development only):**
```bash
orchestrate-container "Add user authentication" --env-file ~/.orchestrator.env --no-network-isolation
```

**Run a single phase:**
```bash
orchestrate-container "Add user authentication" --env-file ~/.orchestrator.env -- --phase pm
```
(Note: Extra orchestrate flags come after `--`)

**Override container image:**
```bash
orchestrate-container "Add user authentication" --container-image ai-sdlc-orchestrator:v1.2.3 --env-file ~/.orchestrator.env
```

#### Implementation Details

1. **Argument parsing** — Uses argparse with the same patterns as `orchestrate` CLI
2. **Config loading** — Loads `OrchestratorConfig` from `--config` path (or default)
3. **Config merging** — CLI overrides (`--container-image`, `--env-file`) are applied to `config.container`
4. **Docker availability check** — Calls `ContainerRuntime.is_docker_available()`; exits with error if Docker is unavailable
5. **Run ID generation** — Auto-generates run_id using `generate_codename()` if not provided
6. **Dry-run mode** — If `--dry-run`, prints `shlex.join(build_run_args(...))` and exits(0)
7. **Container launch** — Calls `asyncio.run(ContainerRuntime.run(...))`
8. **Exit code propagation** — Propagates container exit code via `sys.exit()`

#### Configuration (config/default.yaml)

```yaml
container:
  enabled: false                                  # Opt-in containerization
  image: ai-sdlc-orchestrator:latest              # Container image name
  memory_limit: 8g                                # Memory quota
  cpu_limit: 4.0                                  # CPU cores
  pids_limit: 500                                 # Max process count
  network_mode: orchestrator-net                  # Network isolation
  seccomp_profile_path: /path/to/seccomp-profile.json  # Seccomp filter (optional)
  apparmor_profile: null                          # AppArmor profile (Linux only, optional)
  env_file: ~/.orchestrator.env                   # Secrets file (optional)
  extra_tmpfs: []                                 # Additional tmpfs mounts (optional)
```

---

## Data Models

### ContainerConfig (from orchestrator.models)

```python
class ContainerConfig(BaseModel):
    """Configuration for containerized orchestration runs.

    When enabled=True, each orchestration run is executed inside an ephemeral
    Docker container with resource limits and security hardening applied.
    """

    enabled: bool = False
    image: str = "ai-sdlc-orchestrator:latest"
    memory_limit: str = "8g"
    cpu_limit: float = 4.0
    pids_limit: int = 500
    network_mode: str = "orchestrator-net"
    seccomp_profile_path: str | None = None
    apparmor_profile: str | None = None
    env_file: Path | None = None
    extra_tmpfs: list[str] = Field(default_factory=list)
```

**Fields:**

- `enabled` — If False, `orchestrate` CLI uses in-process mode; if True, `orchestrate-container` is required
- `image` — OCI image name (must match regex `^[a-z0-9][a-z0-9._/-]*(:[a-zA-Z0-9._-]+)?$`)
- `memory_limit` — Docker memory limit (e.g., `8g`, `4096m`)
- `cpu_limit` — CPU cores (float, e.g., 4.0, 1.5)
- `pids_limit` — Max process count (prevents fork bombs)
- `network_mode` — Docker network (allowed: `orchestrator-net`, `bridge`, `none`, `host`)
- `seccomp_profile_path` — Path to seccomp JSON profile (optional; if provided, must exist and be readable)
- `apparmor_profile` — AppArmor profile name (Linux only; ignored on macOS)
- `env_file` — Path to file containing environment variables (must be chmod 600)
- `extra_tmpfs` — Additional tmpfs mount paths (e.g., `/var/cache/orchestrator`)

---

## Integration Examples

### Example 1: Basic Container Execution

```python
import asyncio
from pathlib import Path
from orchestrator.container_runner import ContainerRuntime
from orchestrator.models import ContainerConfig

async def run_containerized():
    config = ContainerConfig(
        enabled=True,
        image="ai-sdlc-orchestrator:latest",
        memory_limit="8g",
        cpu_limit=4.0,
        pids_limit=500,
        network_mode="orchestrator-net",
        seccomp_profile_path="/path/to/seccomp-profile.json",
        env_file=Path(Path.home() / ".orchestrator.env")
    )

    exit_code = await ContainerRuntime.run(
        run_id="0b60f2baf4b5",
        feature_request="Build a REST API",
        config=config,
        workspace_root=Path("./workspace").resolve()
    )

    return exit_code

if __name__ == "__main__":
    exit_code = asyncio.run(run_containerized())
    exit(exit_code)
```

### Example 2: Dry-Run Preview

```python
from pathlib import Path
from orchestrator.container_runner import ContainerRuntime, ArtifactBridgeVolume
from orchestrator.models import ContainerConfig
import shlex

config = ContainerConfig(enabled=True)
workspace = Path("./workspace").resolve()
run_id = "0b60f2baf4b5"
host_run_dir = ArtifactBridgeVolume.prepare_host_run_dir(workspace, run_id)

args = ContainerRuntime.build_run_args(
    run_id=run_id,
    feature_request="Build a REST API",
    config=config,
    host_run_dir=host_run_dir
)

print("Would execute:")
print(shlex.join(args))
```

### Example 3: Custom Configuration from YAML

```python
from pathlib import Path
from orchestrator.config import load_config

# Load config from YAML
config = load_config(Path("config/default.yaml"))

# Access container configuration
if config.container.enabled:
    print(f"Container image: {config.container.image}")
    print(f"Memory limit: {config.container.memory_limit}")
    print(f"Network mode: {config.container.network_mode}")
```

---

## Error Handling

### Common Errors

| Error | Cause | Resolution |
|-------|-------|-----------|
| `RuntimeError: Docker is not installed or not running` | Docker daemon unreachable | `docker ps` to verify; install/start Docker |
| `RuntimeError: Docker version X.Y.Z < 20.10.0` | Docker too old | Upgrade Docker Engine to >= 20.10 |
| `ValueError: Invalid image name` | Image regex validation failed | Use lowercase image names; no special chars except `:` and `/` |
| `ValueError: Invalid run_id format` | run_id not 12-char hex | Auto-generate run_id or provide 12-char lowercase hex string |
| `ValueError: Path escapes workspace_root/runs/` | TOCTOU defense triggered | Verify run_id and workspace path; check for symlinks |
| `FileNotFoundError: seccomp profile not found` | Seccomp file doesn't exist | Verify seccomp_profile_path exists and is readable |
| `PermissionError: env_file not readable` | Env file permissions wrong | `chmod 600 ~/.orchestrator.env` |

---

## Testing

### Unit Tests

```python
# tests/test_container_runner.py

def test_artifact_bridge_volume_creates_directory(tmp_path):
    """Verify directory is created under workspace/runs/"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    run_id = "0b60f2baf4b5"
    run_dir = ArtifactBridgeVolume.prepare_host_run_dir(workspace, run_id)

    assert run_dir.exists()
    assert run_dir.parent.name == "runs"

def test_build_mount_arg_format():
    """Verify mount arg has correct Docker format"""
    host_dir = Path("/path/to/workspace/runs/0b60f2baf4b5")
    run_id = "0b60f2baf4b5"

    mount_arg = ArtifactBridgeVolume.build_mount_arg(host_dir, run_id)

    assert mount_arg.startswith("type=bind,source=")
    assert "/workspace/runs/0b60f2baf4b5" in mount_arg

def test_build_run_args_includes_security_flags():
    """Verify all hardening flags are in docker args"""
    config = ContainerConfig(enabled=True)
    host_run_dir = Path("/workspace/runs/test")

    args = ContainerRuntime.build_run_args(
        run_id="0b60f2baf4b5",
        feature_request="Test",
        config=config,
        host_run_dir=host_run_dir
    )

    assert "--rm" in args
    assert "--read-only" in args
    assert "--cap-drop" in args
    assert "ALL" in args
    assert "--memory" in args
    assert "8g" in args
```

### Integration Tests

```bash
# Run all container tests
pytest tests/test_container_config.py tests/test_container_runner.py -v

# Run a specific test
pytest tests/test_container_runner.py::TestBuildRunArgs::test_includes_seccomp -v

# Run with coverage
pytest tests/test_container_*.py --cov=src/orchestrator/container_runner
```

---

## Performance Considerations

### Startup Time

- Container pull (first run): 10–30 seconds (depends on network)
- Container creation + start: 2–5 seconds (typically)
- Image layers cached after first run: negligible

### Resource Usage

- Memory: 1–2 GB typical for orchestrator workload
- CPU: Proportional to parallelization (`--max-concurrent-agents`)
- Disk: ~100 MB per run for artifacts

### Optimization

- Build the image locally before running: `bash infra/scripts/build-image.sh`
- Use `orchestrate-container --dry-run` to preview command without launching
- Run multiple orchestrations in parallel for independent feature requests

---

## See Also

- [CLAUDE.md — Containerized Orchestration (User Guide)](../CLAUDE.md#containerized-orchestration)
- [CONTAINER_SECURITY_ARCHITECTURE.md — Security Model](./CONTAINER_SECURITY_ARCHITECTURE.md)
- [ADR-0001 — Containerized Orchestration (Design Rationale)](./adr/0001-containerized-orchestration.md)
- [CONTAINER_TROUBLESHOOTING.md — Common Issues](./CONTAINER_TROUBLESHOOTING.md)
