# Containerization Guide: Complete Integration Overview

**Document Version:** 1.0
**Last Updated:** 2026-03-24
**Purpose:** Comprehensive integration guide for the containerized orchestration feature
**Audience:** All stakeholders (users, developers, security teams, operators)

---

## Executive Summary

The orchestrator now supports **containerized orchestration**, where each feature request execution runs inside an ephemeral, isolated Docker container on the local Docker host. This feature prevents agent maliciousness through defense-in-depth security controls while maintaining full isolation between runs.

### Key Features

| Feature | Benefit | Documentation |
|---------|---------|---|
| **Per-Run Containerization** | Each run is independent; no state leaks between executions | [CLAUDE.md §Containerized Orchestration](../CLAUDE.md#containerized-orchestration) |
| **Ephemeral Isolation** | Container destroyed on exit; `--rm` ensures zero persistence | [CONTAINER_SECURITY_ARCHITECTURE.md §Ephemeral Isolation](./CONTAINER_SECURITY_ARCHITECTURE.md#1-ephemeral-isolation) |
| **Filesystem Hardening** | Read-only rootfs with explicit tmpfs mounts for `/tmp` and `/home` | [CONTAINER_SECURITY_ARCHITECTURE.md §Read-Only Rootfs](./CONTAINER_SECURITY_ARCHITECTURE.md#2-read-only-rootfs) |
| **Network Isolation** | Egress limited to `api.anthropic.com:443` via iptables + DNS injection | [CONTAINER_SECURITY_ARCHITECTURE.md §Network Isolation](./CONTAINER_SECURITY_ARCHITECTURE.md#9-network-isolation-iptables--bridge-network) |
| **Resource Limits** | PID, memory, CPU quotas prevent exhaustion attacks | [CONTAINER_SECURITY_ARCHITECTURE.md §Resource Controls](./CONTAINER_SECURITY_ARCHITECTURE.md#6-pid-limit) |
| **Syscall Filtering** | Custom seccomp profile blocks container-escape syscalls (ptrace, mount, mknod) | [CONTAINER_SECURITY_ARCHITECTURE.md §Seccomp Filter](./CONTAINER_SECURITY_ARCHITECTURE.md#4-seccomp-filter-custom) |
| **Privilege Isolation** | Runs as non-root user (UID 1000); `--cap-drop ALL` removes all Linux capabilities | [CONTAINER_SECURITY_ARCHITECTURE.md §Capability Drops](./CONTAINER_SECURITY_ARCHITECTURE.md#3-capability-drops) |

---

## Quick Start (5 minutes)

### Prerequisites

```bash
# Verify Docker is installed and >= 20.10
docker version
# Expected: Server version >= 20.10.0
```

### One-Time Setup

```bash
# 1. Make scripts executable
chmod +x infra/scripts/build-image.sh infra/scripts/setup-network-policy.sh

# 2. Build the Docker image (2-5 minutes, cached after first run)
bash infra/scripts/build-image.sh

# 3. Create orchestrator-net and iptables rules (Linux; requires sudo)
sudo bash infra/scripts/setup-network-policy.sh

# 4. Create secrets file (NEVER commit this)
echo "ANTHROPIC_API_KEY=sk-ant-..." > ~/.orchestrator.env
chmod 600 ~/.orchestrator.env
```

### Run Your First Containerized Orchestration

```bash
# Execute a feature request in an isolated container
orchestrate-container "Add user authentication" --env-file ~/.orchestrator.env

# Artifacts are available in workspace/runs/<run_id>/artifacts/
ls workspace/runs/*/artifacts/
```

---

## Documentation Structure

This feature is documented across **5 key documents** designed for different audiences:

### 1. **[CLAUDE.md — Containerized Orchestration](../CLAUDE.md#containerized-orchestration)** ⭐ START HERE
- **Audience:** End users, developers, operators
- **Content:** Setup instructions, daily workflow, troubleshooting quick links
- **Sections:** Prerequisites, One-Time Setup, Daily Workflow, Stale Image Warning, Network IP Refresh, Orphaned Container Recovery, Security Verification Checklist
- **Best for:** Getting started and basic operations

### 2. **[CONTAINER_API.md](./CONTAINER_API.md)** — API Reference
- **Audience:** Backend engineers, platform engineers extending the runtime
- **Content:** Complete API documentation for `ContainerRuntime`, `ArtifactBridgeVolume`, and `orchestrate-container` CLI
- **Sections:** ArtifactBridgeVolume, ContainerRuntime, ContainerizedOrchestratorCLI, Configuration Models, Integration Examples, Error Handling, Testing
- **Best for:** Integrating containerization into your own code or extending the runtime

### 3. **[CONTAINER_SECURITY_ARCHITECTURE.md](./CONTAINER_SECURITY_ARCHITECTURE.md)** — Security Deep Dive
- **Audience:** Security engineers, platform engineers, compliance teams
- **Content:** Detailed threat model, 12 layered security controls, attack surface analysis, defense-in-depth strategy
- **Sections:** Threat Model, Security Controls (with validation steps), Control Composition, Operational Procedures, Limitations & Gaps
- **Best for:** Understanding the security model and verifying controls are working

### 4. **[CONTAINER_TROUBLESHOOTING.md](./CONTAINER_TROUBLESHOOTING.md)** — Diagnostic Guide
- **Audience:** Operators, developers, support engineers
- **Content:** Common issues with solutions, performance tuning, debug procedures
- **Sections:** Quick Diagnostics, 15+ Common Issues & Solutions, Performance Issues, Log Files, Escalation Procedures
- **Best for:** Fixing problems when things go wrong

### 5. **[docs/adr/0001-containerized-orchestration.md](./adr/0001-containerized-orchestration.md)** — Architecture Decision Record
- **Audience:** Architects, senior engineers, future maintainers
- **Content:** Design rationale, alternatives considered, consequences, implementation details
- **Sections:** Context, Decision, Rationale, Implementation, Consequences, Alternatives, Validation, Notes for Implementers
- **Best for:** Understanding why containerization was chosen and what tradeoffs were made

---

## Security Model at a Glance

The containerization feature implements **defense-in-depth** with 12 orthogonal security controls:

```
┌─────────────────────────────────────────────────────────────┐
│         Containerized Orchestration Security Model         │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  Layer 1: Ephemeral Isolation                               │
│  ├─ --rm destroys container on exit                        │
│  └─ tmpfs mounts in-memory only                            │
│                                                               │
│  Layer 2: Filesystem Hardening                              │
│  ├─ --read-only rootfs prevents modifications              │
│  └─ Bind-mount only workspace/runs/<run_id>/               │
│                                                               │
│  Layer 3: Privilege Restriction                             │
│  ├─ --cap-drop ALL removes all capabilities                │
│  ├─ Non-root user (UID 1000)                               │
│  └─ Seccomp blocks ptrace, mount, mknod, etc.              │
│                                                               │
│  Layer 4: Resource Isolation                                │
│  ├─ --pids-limit 500 (fork bomb prevention)                │
│  ├─ --memory 8g (OOM prevention)                           │
│  └─ --cpus 4 (runaway process prevention)                  │
│                                                               │
│  Layer 5: Network Isolation                                 │
│  ├─ --network orchestrator-net (bridge network)             │
│  ├─ iptables rules allow only api.anthropic.com:443        │
│  ├─ DNS injection via --add-host                            │
│  └─ Port 53 blocked (no DNS resolution)                    │
│                                                               │
│  Layer 6: Mandatory Access Control (Linux)                  │
│  └─ AppArmor profile (optional, Linux only)                │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Feature Checklist

Use this checklist to verify the containerization feature is fully operational:

- [ ] **Installation**
  - [ ] Docker Engine >= 20.10 is installed
  - [ ] `orchestrate-container` CLI is available (`which orchestrate-container`)
  - [ ] `infra/scripts/build-image.sh` is executable

- [ ] **Image & Network Setup**
  - [ ] Docker image built: `docker image ls | grep ai-sdlc-orchestrator`
  - [ ] `orchestrator-net` network exists: `docker network ls | grep orchestrator-net`
  - [ ] iptables rules configured (Linux): `sudo iptables -vnL | grep orchestrator`
  - [ ] Secrets file created: `ls ~/.orchestrator.env` (chmod 600)

- [ ] **Basic Functionality**
  - [ ] Dry run works: `orchestrate-container "..." --dry-run --env-file ~/.orchestrator.env`
  - [ ] Container runs: `orchestrate-container "..." --env-file ~/.orchestrator.env`
  - [ ] Artifacts written to host: `ls workspace/runs/*/artifacts/`
  - [ ] Container cleaned up: `docker ps -a | grep orchestrator-` (should be empty)

- [ ] **Security Controls**
  - [ ] Non-root user: `docker run ... id` shows uid=1000
  - [ ] Read-only rootfs: `docker run ... touch /test` fails
  - [ ] Network isolation: `docker run ... curl google.com` fails, `curl api.anthropic.com` succeeds
  - [ ] Resource limits: `docker inspect <container> | grep Memory` shows 8g

- [ ] **Operations**
  - [ ] Stale image detected after code change
  - [ ] Image rebuilt successfully: `bash infra/scripts/build-image.sh`
  - [ ] Network policy refreshed (if needed): `sudo bash infra/scripts/setup-network-policy.sh --refresh`
  - [ ] Stuck containers cleaned: `docker rm -f orchestrator-<run_id>`

---

## Audience-Specific Reading Paths

### Path A: "I want to use containerized orchestration" (5 min)
1. Read: [Prerequisites](../CLAUDE.md#prerequisites)
2. Do: [One-Time Setup](../CLAUDE.md#one-time-setup)
3. Do: [Daily Workflow](../CLAUDE.md#daily-workflow)
4. Bookmark: [CONTAINER_TROUBLESHOOTING.md](./CONTAINER_TROUBLESHOOTING.md) for emergencies

### Path B: "I need to understand the security model" (30 min)
1. Read: [Threat Model](./CONTAINER_SECURITY_ARCHITECTURE.md#threat-model)
2. Read: [Security Controls](./CONTAINER_SECURITY_ARCHITECTURE.md#security-controls) (skim each control)
3. Read: [Control Composition](./CONTAINER_SECURITY_ARCHITECTURE.md#control-composition--defense-in-depth)
4. Do: [Security Verification Checklist](../CLAUDE.md#security-verification-checklist)

### Path C: "I'm extending the runtime in code" (45 min)
1. Read: [CONTAINER_API.md — Overview](./CONTAINER_API.md#overview)
2. Study: [ArtifactBridgeVolume](./CONTAINER_API.md#artifactbridgevolume) (bind-mount management)
3. Study: [ContainerRuntime](./CONTAINER_API.md#containerruntime) (lifecycle management)
4. Study: [Integration Examples](./CONTAINER_API.md#integration-examples)
5. Run: [Tests](./CONTAINER_API.md#testing)

### Path D: "I'm troubleshooting a problem" (15 min)
1. Run: [Quick Diagnostics](./CONTAINER_TROUBLESHOOTING.md#quick-diagnostics)
2. Find your issue: [Common Issues & Solutions](./CONTAINER_TROUBLESHOOTING.md#common-issues--solutions)
3. If stuck: [Log Files & Debugging](./CONTAINER_TROUBLESHOOTING.md#log-files--debugging)
4. Escalate: [Escalation & Support](./CONTAINER_TROUBLESHOOTING.md#escalation--support)

### Path E: "I'm designing production infrastructure" (1 hour)
1. Read: [Architecture Decision Record](./adr/0001-containerized-orchestration.md) (Rationale & Implementation)
2. Review: [Consequences](./adr/0001-containerized-orchestration.md#consequences)
3. Review: [Limitations & Gaps](./CONTAINER_SECURITY_ARCHITECTURE.md#limitations--gaps)
4. Plan: Network IP rotation strategy (see ADR-0002)
5. Plan: Image rebuild automation (see CONTAINER_API.md § Performance)

---

## Key Operational Procedures

### Building & Deploying

```bash
# Build the image (after code changes)
bash infra/scripts/build-image.sh

# Verify image was created
docker image ls ai-sdlc-orchestrator:latest

# Tag with custom version
bash infra/scripts/build-image.sh --tag v1.2.3
```

### Running Containerized Orchestrations

```bash
# Standard run
orchestrate-container "Your feature request" --env-file ~/.orchestrator.env

# Preview docker command before executing
orchestrate-container "..." --env-file ~/.orchestrator.env --dry-run

# Specify custom image
orchestrate-container "..." --container-image myrepo/orchestrator:v1.0 --env-file ~/.orchestrator.env

# Run single phase (orchestrate-container still supports all orchestrate flags)
orchestrate-container "..." --env-file ~/.orchestrator.env -- --phase pm

# Skip network isolation (development only)
orchestrate-container "..." --no-network-isolation --env-file ~/.orchestrator.env
```

### Maintenance

```bash
# Check Docker status
docker ps
docker network ls
docker version

# Refresh network policy (if Anthropic IPs rotated)
sudo bash infra/scripts/setup-network-policy.sh --refresh

# Clean up stuck containers
docker ps -a --filter name=orchestrator- --filter status=exited -q | xargs docker rm

# Inspect a running container
docker exec -it orchestrator-<run_id> sh
docker logs orchestrator-<run_id>
docker stats orchestrator-<run_id>
```

---

## Threat Model Summary

| Threat | Severity | Control | Validation |
|--------|----------|---------|-----------|
| **Host Filesystem Access** | Critical | Read-only rootfs + bind-mount isolation | `touch /test` fails inside container |
| **State Persistence** | High | `--rm` + ephemeral tmpfs | No files persist after container exit |
| **Network Exfiltration** | High | iptables rules + DNS injection | `curl google.com` fails; `curl api.anthropic.com` succeeds |
| **Resource Exhaustion** | High | PID/memory/CPU limits | `:(){ :|:& };:` killed by PID limit |
| **Privilege Escalation** | Medium | `--cap-drop ALL` + seccomp | `strace` fails (requires CAP_SYS_PTRACE) |
| **Container Escape** | Medium | Seccomp (ptrace/mount/mknod blocked) | `mount` fails with "Operation not permitted" |

---

## Common Scenarios

### Scenario 1: Running a Containerized Orchestration for the First Time

```bash
# 1. Verify prerequisites
docker version  # Must be >= 20.10

# 2. Do one-time setup
bash infra/scripts/build-image.sh
sudo bash infra/scripts/setup-network-policy.sh
echo "ANTHROPIC_API_KEY=sk-..." > ~/.orchestrator.env
chmod 600 ~/.orchestrator.env

# 3. Run your first containerized orchestration
orchestrate-container "Build a REST API" --env-file ~/.orchestrator.env

# 4. Check artifacts
ls workspace/runs/*/artifacts/
```

### Scenario 2: Code Changes & Rebuild

```bash
# 1. Modify src/orchestrator/ or pyproject.toml
vim src/orchestrator/models.py

# 2. Rebuild the image
bash infra/scripts/build-image.sh

# 3. Run with new code
orchestrate-container "Your feature" --env-file ~/.orchestrator.env
```

### Scenario 3: Network Policy Rotation

```bash
# If Anthropic rotates IPs and API calls start timing out:

# 1. Diagnose
docker run --rm --network orchestrator-net ai-sdlc-orchestrator:latest \
  curl -s --max-time 5 https://api.anthropic.com

# 2. Refresh network rules
sudo bash infra/scripts/setup-network-policy.sh --refresh

# 3. Verify
docker run --rm --network orchestrator-net ai-sdlc-orchestrator:latest \
  curl -s --max-time 5 https://api.anthropic.com
```

### Scenario 4: Debugging a Failed Run

```bash
# 1. Run with debugging
LOGLEVEL=DEBUG orchestrate-container "..." --env-file ~/.orchestrator.env

# 2. Inspect logs
tail -100 workspace/logs/<run_id>.log

# 3. Inspect container (if still running)
docker exec -it orchestrator-<run_id> sh
  > cat /etc/hosts | grep api.anthropic.com
  > mount | grep workspace
  > cat /sys/fs/cgroup/memory/memory.limit_in_bytes

# 4. Check for docker logs
docker logs orchestrator-<run_id>
```

---

## Implementation Status

| Component | Status | Test Coverage | Documentation |
|-----------|--------|---|---|
| ContainerConfig model | ✅ Complete | 8 unit tests | CONTAINER_API.md |
| ContainerRuntime class | ✅ Complete | 12 unit tests + integration tests | CONTAINER_API.md |
| ArtifactBridgeVolume class | ✅ Complete | 6 unit tests | CONTAINER_API.md |
| orchestrate-container CLI | ✅ Complete | 4 integration tests | CLAUDE.md + CONTAINER_API.md |
| Dockerfile + build script | ✅ Complete | Manual validation | CLAUDE.md |
| Network policy (iptables) | ✅ Complete | Manual validation | CONTAINER_SECURITY_ARCHITECTURE.md |
| Seccomp profile | ✅ Complete | Manual validation | CONTAINER_SECURITY_ARCHITECTURE.md |
| AppArmor profile (Linux) | ✅ Complete | Manual validation | CONTAINER_SECURITY_ARCHITECTURE.md |
| Shell scripts | ✅ Complete | Manual validation | CLAUDE.md |

---

## FAQ

### Q: Do I have to use containerized orchestration?
**A:** No. The original `orchestrate` CLI runs in-process (fast, no isolation). The new `orchestrate-container` CLI is opt-in and provides security isolation. Choose based on your threat model.

### Q: What's the performance overhead?
**A:** ~2–5 seconds per run for Docker startup and image pull (cached after first run). The orchestration itself has no performance penalty inside the container.

### Q: Does this work on macOS?
**A:** Yes, but with a caveat: **iptables egress filtering is not available on macOS Docker Desktop**. All other controls (read-only rootfs, capability drops, seccomp, resource limits) apply. For network isolation on macOS, use a local HTTPS proxy with an allowlist.

### Q: What if I modify the code?
**A:** Rebuild the Docker image: `bash infra/scripts/build-image.sh`. Stale images silently run outdated code.

### Q: How do I verify the security controls are working?
**A:** Run the [Security Verification Checklist](../CLAUDE.md#security-verification-checklist) in CLAUDE.md.

### Q: Can I run multiple containerized orchestrations in parallel?
**A:** Yes. Each run gets a unique run_id and its own container; they don't interfere with each other.

### Q: What if a container gets stuck?
**A:** Remove it: `docker rm -f orchestrator-<run_id>`. The `--rm` flag ensures containers clean up on normal exit.

---

## References

### Documentation Files
- **[CLAUDE.md](../CLAUDE.md#containerized-orchestration)** — User guide
- **[CONTAINER_API.md](./CONTAINER_API.md)** — API reference
- **[CONTAINER_SECURITY_ARCHITECTURE.md](./CONTAINER_SECURITY_ARCHITECTURE.md)** — Security architecture
- **[CONTAINER_TROUBLESHOOTING.md](./CONTAINER_TROUBLESHOOTING.md)** — Troubleshooting
- **[docs/adr/0001-containerized-orchestration.md](./adr/0001-containerized-orchestration.md)** — Architecture decision record
- **[CONTAINER_DOCUMENTATION_INDEX.md](./CONTAINER_DOCUMENTATION_INDEX.md)** — Documentation navigation

### External References
- [Docker Security Best Practices](https://docs.docker.com/engine/security/)
- [NIST Container Security Recommendations](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-190.pdf)
- [seccomp Documentation](https://man7.org/linux/man-pages/man2/seccomp.2.html)
- [AppArmor Documentation](https://gitlab.com/apparmor/apparmor/-/wikis/home)

---

## Support & Escalation

If you encounter issues:

1. **Check prerequisites** — `docker version` must be >= 20.10
2. **Check quick diagnostics** — See [CONTAINER_TROUBLESHOOTING.md § Quick Diagnostics](./CONTAINER_TROUBLESHOOTING.md#quick-diagnostics)
3. **Search common issues** — See [CONTAINER_TROUBLESHOOTING.md § Common Issues](./CONTAINER_TROUBLESHOOTING.md#common-issues--solutions)
4. **Enable debug logging** — `LOGLEVEL=DEBUG orchestrate-container ...`
5. **Collect diagnostics** — See [CONTAINER_TROUBLESHOOTING.md § Escalation](./CONTAINER_TROUBLESHOOTING.md#escalation--support)

---

## Document Maintenance

| Last Reviewed | Next Review | Trigger |
|---|---|---|
| 2026-03-24 | Q2 2026 | Any change to `src/orchestrator/container_*.py` or `infra/docker/` |

**Checklist for updates:**
- [ ] Architecture changed? Update docs/adr/0001-containerized-orchestration.md
- [ ] API changed? Update CONTAINER_API.md
- [ ] New errors? Update CONTAINER_TROUBLESHOOTING.md
- [ ] New operational procedures? Update CLAUDE.md
- [ ] New document? Update this index and CONTAINER_DOCUMENTATION_INDEX.md

