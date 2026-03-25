# ADR-0001: Containerized Orchestration for Security Isolation

**Status:** Accepted
**Date:** 2026-03-24
**Decision Makers:** Security Team, Principal Architect
**Supersedes:** None
**Related:** ADR-0002 (Network Policy Implementation), ADR-0003 (Seccomp Profile Design)

## Context

The AI SDLC Orchestrator runs untrusted LLM-generated code (agents) that invoke tools and potentially malicious user inputs through feature requests. While the codebase is carefully audited, the attack surface includes:

1. **Agent Code Injection** — An LLM response that crafts a tool call could attempt unauthorized actions (network exfiltration, host filesystem access, resource exhaustion)
2. **State Persistence Between Runs** — Cached state from one orchestration run could leak into the next, violating run isolation guarantees
3. **Resource Exhaustion** — A runaway agent could consume all host memory, CPU, or PID resources
4. **Network Exploitation** — An agent could attempt SSRF attacks, DNS exfiltration, or unauthorized API calls

## Decision

Wrap each orchestration run in an **ephemeral, isolated Docker container** deployed on the local Docker host. The container is removed immediately on exit (no persistent state), enforced to run as non-root, restricted to read-only rootfs with explicit tmpfs mounts, subject to syscall filtering (seccomp), capability drops, resource limits (memory, CPU, PID), and network isolation to only `api.anthropic.com:443`.

### Key Choices

1. **Per-run containerization** — Every `orchestrate-container` invocation spawns a new container, destroyed on exit
2. **Local Docker only** — No Kubernetes, Swarm, or remote Docker — keep deployment simple for end users
3. **Subprocess-based CLI interaction** — Use `docker run` via subprocess, not the docker-py SDK
4. **Bind-mount only run artifacts** — Container sees only `workspace/runs/<run_id>/`, no project source or prior runs
5. **Hardening via composition** — Apply multiple orthogonal controls (seccomp, capability drops, read-only rootfs, network policy) rather than relying on a single strong isolation mechanism

## Rationale

### Why Containerization?

**Threat Model Coverage:**

| Threat | In-Process (Current) | Containerized |
|--------|-----------------|------------------|
| Host filesystem access | ⚠️ Agent code has full read/write | ✅ Bind-mount limited to `runs/<run_id>/` |
| Resource exhaustion | ⚠️ Shares host resources; fork bomb affects host | ✅ PID/memory/CPU limits isolated per container |
| State persistence | ⚠️ Python process state persists between runs | ✅ `--rm` destroys container and all state |
| Network exfiltration | ⚠️ Agent can reach any host/port | ✅ iptables egress filtering to api.anthropic.com:443 only |
| Privilege escalation | ⚠️ Runs as same user as host orchestrator | ✅ `--cap-drop ALL`, no setuid/setgid in seccomp |
| Rootkit installation | ⚠️ Can modify host filesystem | ✅ `--read-only` rootfs + seccomp blocks mount/init_module |

### Why Subprocess (Not docker-py)?

- **No extra dependencies** — docker-py is ~3MB and introduces a version pin; docker CLI is co-installed with Docker Engine
- **Simple invocation surface** — One `docker run` call per orchestration; CLI args are naturally a list (no shell injection risk)
- **Direct stdout/stderr streaming** — Subprocess allows real-time output without SDK buffer management
- **Auditability** — The exact docker run command is visible in logs and can be manually reproduced

### Why Bind-Mount (Not Full Workspace)?

Mounting the full workspace would expose:
- Prior run artifacts (from previous `orchestrate-container` invocations)
- Project source code (potential for code injection in subsequent runs)
- Config secrets and `.env` files (credential exposure)

Mounting only `workspace/runs/<run_id>/` enforces least-privilege filesystem access while still allowing the container to read and write artifacts.

### Why Opt-In (Not Always-On)?

- **Backward compatibility** — Existing users running `orchestrate` directly should not be surprised by container overhead (~2–5 seconds)
- **Gradual adoption** — Docker is optional; users without Docker can continue using in-process mode
- **Predictable behavior** — Auto-detection ("use container if Docker is available") would lead to non-deterministic performance
- **Clear mental model** — `orchestrate` = in-process (fast, no isolation); `orchestrate-container` = hardened, isolated (slower, safer)

## Implementation

### Architecture

1. **ContainerConfig** (Pydantic model in `src/orchestrator/models.py`)
   - `enabled: bool` (default False)
   - `image: str` (default "ai-sdlc-orchestrator:latest")
   - `memory_limit: str` (default "8g")
   - `cpu_limit: float` (default 4.0)
   - `pids_limit: int` (default 500)
   - `network_mode: str` (default "orchestrator-net")
   - `seccomp_profile_path: str | None`
   - `apparmor_profile: str | None`
   - `env_file: Path | None`
   - `extra_tmpfs: list[str]`

2. **ContainerRuntime** (`src/orchestrator/container_runner.py`)
   - `build_run_args(...)` — Assemble docker run argument list with all hardening flags
   - `async run(...)` — Launch subprocess, stream stdout/stderr, forward signals, validate output on exit
   - `is_docker_available()` — Check Docker Engine version >= 20.10

3. **ArtifactBridgeVolume** (`src/orchestrator/container_runner.py`)
   - `prepare_host_run_dir(workspace_root, run_id)` — Create and validate run directory
   - `build_mount_arg(...)` — Generate `--mount type=bind,...` flag with TOCTOU defense

4. **ContainerizedOrchestratorCLI** (`src/orchestrator/containerized_main.py`)
   - New entry point: `orchestrate-container "Your feature" --env-file ~/.orchestrator.env`
   - Delegates to `ContainerRuntime.run()`

5. **Security Controls**

   | Control | Implementation | Threat Addressed |
   |---------|---|---|
   | Ephemeral container | `--rm` — Docker removes container on exit | State persistence |
   | Read-only rootfs | `--read-only` — No modifications to image layers | Rootkit installation |
   | Explicit tmpfs mounts | `/tmp`, `/home/orchestrator/.config` writable in memory only | Backdoor staging |
   | Capability drops | `--cap-drop ALL` — No Linux privileges | Privilege escalation |
   | Seccomp filter | Custom profile blocking ptrace, mount, mknod, init_module | Container escape |
   | AppArmor MAC | `--security-opt apparmor=...` (Linux only) | Fine-grained access control |
   | Resource limits | PID, memory, CPU via `--pids-limit`, `--memory`, `--cpus` | Resource exhaustion |
   | Network isolation | Docker bridge network + iptables FORWARD rules | Network exfiltration |
   | DNS injection | `--add-host api.anthropic.com=<IPs>` | DNS tunneling |
   | Non-root user | UID 1000 orchestrator user | Privilege escalation |
   | Init process | `--init` | Zombie reaping |

### Docker Image

- **Multi-stage Dockerfile** — builder stage installs dependencies; runtime stage copies only the installed package
- **Non-root user** — Image runs as UID 1000 (`orchestrator`) by default
- **Minimal base** — python:3.11-slim to reduce attack surface
- **No secrets baked in** — ANTHROPIC_API_KEY injected at runtime via `--env-file`

### Network Policy

`infra/scripts/setup-network-policy.sh`:
- Creates Docker bridge network `orchestrator-net`
- Resolves `api.anthropic.com` IPs at setup time
- Configures iptables FORWARD rules to allow only resolved Anthropic IPs on TCP 443
- Blocks all other egress from `orchestrator-net`
- Blocks DNS (UDP/TCP 53) since IPs are injected via `--add-host`

**Note:** macOS Docker Desktop does not support host iptables; egress filtering is skipped on macOS.

### Shell Scripts

- **`build-image.sh`** — Builds and tags the Docker image with git SHA
- **`run-containerized.sh`** — Thin wrapper for non-Python callers
- **`setup-network-policy.sh`** — One-time setup for orchestrator-net and iptables rules
- **`load-apparmor-profile.sh`** — Loads AppArmor profile into kernel (Linux only)

## Consequences

### Positive

✅ **High security isolation** — Reduces attack surface for untrusted LLM agent code
✅ **State isolation** — No cross-run state leakage; each run is independent
✅ **Resource isolation** — Fork bombs and memory exhaustion are bounded
✅ **Auditability** — Container configuration is explicit and reproducible
✅ **Optional adoption** — Existing users can continue with in-process mode

### Negative

⚠️ **Container overhead** — ~2–5 seconds startup per run for image pull, container creation
⚠️ **Docker dependency** — Requires Docker Engine >= 20.10 on host
⚠️ **Network policy limitation** — iptables egress filtering unavailable on macOS; requires root for initial setup on Linux
⚠️ **Rebuild requirement** — Docker image must be rebuilt after every change to `src/orchestrator/` or `pyproject.toml`

### Mitigation

- Overhead is acceptable for the security benefit; users concerned about latency can use in-process mode
- Docker is industry-standard; most developers have it installed
- macOS users can use HTTPS proxy with ACL as alternative to iptables
- Clear documentation in CLAUDE.md and build scripts remind users to rebuild image

## Alternatives Considered

### A1: No Containerization (Status Quo)

Agents run in-process in the orchestrator's Python runtime.

**Pros:** Fastest; no extra dependencies
**Cons:** No state isolation; resource exhaustion affects host; filesystem access unrestricted

**Rejected:** Insufficient security for production use.

### A2: Podman (Instead of Docker)

Use `podman` CLI instead of `docker`.

**Pros:** Daemonless; rootless by default
**Cons:** Podman socket path differs from Docker; additional setup burden on users; smaller ecosystem

**Rejected:** Docker is more widely deployed; adds unnecessary complexity.

### A3: docker-py SDK (Instead of Subprocess)

Use the docker-py Python SDK for container lifecycle management.

**Pros:** Type-safe Python API; cleaner code
**Cons:** ~3MB dependency; buffer management for stdout/stderr; version churn

**Rejected:** Subprocess approach is simpler and avoids large dependency.

### A4: Full Workspace Mount (Instead of Run-Scoped)

Mount the entire `workspace/` directory into the container.

**Pros:** Containers could access prior run artifacts for analysis
**Cons:** Exposes all prior runs; violates least-privilege; enables cross-run state leakage

**Rejected:** Security risk outweighs potential utility.

### A5: gVisor/gVisor runsc (Kernel-Level Sandbox)

Use gVisor or seccomp-enabled runtimes for stronger isolation.

**Pros:** Stronger isolation than standard containers
**Cons:** Adds kernel-level dependency; significant performance overhead; adds complexity

**Rejected:** Not proportional to threat level; standard Docker + multiple controls sufficient.

## Related Decisions

- **ADR-0002** — Custom seccomp profile derived from Docker default, blocking ptrace/mount/mknod
- **ADR-0003** — Iptables-based network policy with DNS injection for egress filtering
- **ADR-0004** — AppArmor profile for Linux hosts (optional, complements Docker controls)

## Validation

- ✅ Unit tests in `tests/test_container_config.py` and `tests/test_container_runner.py`
- ✅ Integration tests in `tests/integration/test_runs_contract.py`
- ✅ Manual verification of security controls (see "Security Verification Checklist" in CLAUDE.md)
- ✅ Zero Docker dependency for CI (all subprocess calls mocked)

## Open Questions

1. Should we support resume runs from within a container? (Answer: Yes — container mounts the prior run directory and passes `--resume-run-id` to internal orchestrate)
2. How do we handle Anthropic IP rotation? (Answer: Provide `setup-network-policy.sh --refresh` to update iptables rules)
3. What if a user's Docker storage driver is aufs (deprecated)? (Answer: Warn and proceed — Docker handles driver compatibility)

## Notes for Implementers

- **Subprocess calls must never pass a shell string** — always use a list to avoid injection
- **Run ID validation is critical** — regex `^[0-9a-f]{12}$` prevents path traversal and command injection
- **TOCTOU defense** — Resolve paths before and after directory creation to catch symlink attacks
- **Forbidden names check** — Post-exit validation for suspicious files (`.bashrc`, `.ssh`, etc.) in output directory
- **DNS resolution happens on host** — `_resolve_anthropic_ips()` runs before container launch to inject exact IPs
- **AppArmor is Linux-only** — Skip apparmor flags on macOS with INFO-level log
- **Test isolation** — All subprocess calls must be mocked; tests should not require Docker
