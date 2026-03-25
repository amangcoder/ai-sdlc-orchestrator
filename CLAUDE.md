# AI SDLC Orchestrator

## Overview

Python-based orchestrator that coordinates AI agents (PM, Architect, Engineer, QA, Reviewer) through a structured SDLC pipeline using the Claude Agent SDK.

## Project Structure

- `src/orchestrator/` — Core Python package
- `src/schemas/` — JSON Schema definitions for inter-agent artifacts
- `.claude/agents/` — Claude Code sub-agent definitions
- `.claude/hooks/` — Quality gate and observability hooks
- `config/default.yaml` — Orchestrator configuration

## Development

```bash
# Install in dev mode
pip install -e ".[dev]"

# Run tests
pytest

# Dry run
orchestrate --dry-run "Build a todo app"

# Single phase
orchestrate --phase pm "Build a todo app"

# Full run
orchestrate "Build a todo app"
```

## Architecture Principles

- **Structured workflow with validation checkpoints** — not "deterministic" (LLMs are inherently non-deterministic)
- **Bounded statefulness** — agents maintain session context during active phase, checkpoint as artifacts for cross-phase communication
- **Model routing by complexity** — Opus for deep reasoning (principal engineer, reviewers, security), Sonnet for implementation/QA/planning, Haiku for docs/git
- **Isolated parallel execution** — engineers use `isolation: worktree` to prevent file conflicts
- **Schema-validated artifacts** — every inter-agent artifact has a JSON schema; phase completion blocks until validation passes

## Speed Modes

The `--speed` flag controls pipeline depth and defaults to `auto` — intelligent speed selection powered by a fast Claude Haiku classifier.

### Speed Mode Options

| Mode | Pipeline Depth | Use Case | Cost |
|------|--------|----------|------|
| **turbo** | 4 steps | Trivial changes: typos, renames, single-file edits, doc updates | $0.01–0.03 |
| **standard** | 6 steps | Bug fixes, simple features, single endpoints | $0.05–0.15 |
| **thorough** | 8+ steps | Multi-file refactors, new modules, DB schema changes. Adds security review. | $0.20–0.50 |
| **paranoid** | 10+ steps | Security-critical work: auth systems, payments, PII handling, encryption. Includes debate phase and dual reviewers. | $0.50–2.00 |
| **auto** (default) | Automatic | Claude Haiku analyzes your request and selects the optimal mode. Adds ~1–2 seconds and ~$0.001 per run. | Varies |

### Auto Classification

When you run `orchestrate --speed auto` (or just `orchestrate` with no --speed flag), the orchestrator:

1. **Analyzes your feature request** in under 2 seconds using Claude Haiku
2. **Detects risk signals** — any mention of auth, payments, PII, encryption, or compliance automatically escalates to `thorough` or `paranoid`
3. **Maps complexity tiers:**
   - **trivial** (typo, rename, config) → `turbo`
   - **small** (bug fix, simple feature) → `standard`
   - **medium** (new module, refactor) → `thorough`
   - **large** (new subsystem, auth system) → `paranoid`
4. **Returns a concrete speed mode** (never stores the `auto` sentinel internally)

### Examples

```bash
# Auto-select the best speed mode (default)
orchestrate "Fix typo in error message"                    # → turbo (auto-selected)

orchestrate "Add OAuth2 login with GitHub"                 # → thorough (auto-selected, auth risk detected)

orchestrate "Implement Stripe payment processing"          # → paranoid (auto-selected, payment risk detected)

# Explicit speed selection (skip auto-classification)
orchestrate --speed turbo "Update README"                  # → turbo (explicit, skips classifier)

orchestrate --speed paranoid "Add JWT token refresh"       # → paranoid (explicit, skips classifier)
```

### Cost & Performance

- **Auto classification** adds ~$0.001 per run and takes 1–2 seconds
- **Falls back safely** — if classification fails, defaults to `standard` mode
- **Orthogonal to --mode** — you can combine `--speed paranoid --mode overkill` for maximum scrutiny

### Implementation Details

The auto-classifier:
- Makes a **single Haiku call** with the feature request and codebase context
- Uses **lazy imports** to avoid circular dependencies
- **Wraps user input** in XML delimiters for security
- **Extracts JSON** from the LLM response using regex (robust to preamble/epilogue)
- **Escalates on risk** — if any security signals detected, bumps the speed tier up
- **Never raises** — all errors gracefully return `standard` mode

See [Speed Mode Developer Guide](docs/speed-modes.md) for implementation details.

---

## Containerized Orchestration

Each `orchestrate-container` run spins up an **ephemeral, isolated Docker container** on the local Docker host. The container is removed automatically on exit — no state persists between runs.

### Prerequisites

- **Docker Engine >= 20.10** must be installed and running.
  - Verify: `docker version`
  - macOS: [Docker Desktop](https://www.docker.com/products/docker-desktop/) satisfies this via socket forwarding to `/var/run/docker.sock`
- The `orchestrate-container` CLI is installed via `pip install -e "."` (registered in `pyproject.toml`)

### One-Time Setup

```bash
# 1. Make scripts executable (first time only)
chmod +x infra/scripts/build-image.sh infra/scripts/run-containerized.sh infra/scripts/setup-network-policy.sh

# 2. Build the Docker image (re-run after every src/ or pyproject.toml change)
bash infra/scripts/build-image.sh

# 3. Create the orchestrator-net network + apply iptables egress rules
#    (requires sudo; allows ONLY api.anthropic.com:443 outbound)
sudo bash infra/scripts/setup-network-policy.sh

# 4. Create your secrets file (NEVER commit this file)
echo "ANTHROPIC_API_KEY=sk-ant-..." > ~/.orchestrator.env
chmod 600 ~/.orchestrator.env
```

### Daily Workflow

```bash
# Run a full orchestration in an isolated container
orchestrate-container "Add user authentication" --env-file ~/.orchestrator.env

# Preview the exact docker run command without launching
orchestrate-container "Add user authentication" --env-file ~/.orchestrator.env --dry-run

# Skip orchestrator-net (development only — no egress filtering)
orchestrate-container "..." --no-network-isolation --env-file ~/.orchestrator.env

# Using the shell wrapper (non-Python callers)
bash infra/scripts/run-containerized.sh --feature "Add user authentication"
```

Artifacts are written to **`workspace/runs/<run_id>/artifacts/`** on the host via bind-mount and are available immediately after the container exits.

### ⚠️ Stale Image Warning

**Rebuild the Docker image after every change to `src/orchestrator/` or `pyproject.toml`.**
Stale images will silently run outdated code — there is no automatic detection.

```bash
bash infra/scripts/build-image.sh
```

### Network IP Refresh

`setup-network-policy.sh` resolves `api.anthropic.com` IPs **at setup time** and hardcodes them in iptables rules. If Anthropic rotates their IPs and container API calls begin failing, re-run with `--refresh`:

```bash
sudo bash infra/scripts/setup-network-policy.sh --refresh
```

### Orphaned Container Recovery

If `orchestrate-container` crashes mid-run (SIGKILL, power loss, OOM), the container may keep running:

```bash
# List all active orchestrator containers
docker ps --filter name=orchestrator-

# Remove a specific stuck container
docker rm -f orchestrator-<run_id>
```

### Security Verification Checklist

Run these manually on Linux to verify full hardening (macOS lacks AppArmor enforcement):

```bash
CNAME=orchestrator-test  # use a running container name

# 1. Verify non-root user (must show uid=1000)
docker exec $CNAME id

# 2. Verify read-only rootfs (must fail with "Read-only file system")
docker exec $CNAME sh -c "touch /test"

# 3. Verify network isolation (google.com must fail, api.anthropic.com must succeed)
docker exec $CNAME sh -c "curl -s --max-time 3 https://google.com || echo 'BLOCKED (expected)'"
docker exec $CNAME sh -c "curl -s --max-time 5 -o /dev/null -w '%{http_code}' https://api.anthropic.com || true"

# 4. Verify PID limit (fork bomb should be killed)
docker exec $CNAME sh -c ":(){ :|:& };:" || echo "Killed by PID limit (expected)"

# 5. Verify memory limit (must OOM at > 8GB)
docker stats $CNAME --no-stream

# 6. Verify seccomp blocks ptrace (must fail with "Operation not permitted")
docker exec $CNAME sh -c "strace echo test" 2>&1 | head -3
```

### AppArmor Profile (Linux only)

An AppArmor profile (`infra/docker/apparmor-profile`) is provided for Linux hosts. It enforces MAC policies on filesystem access, network operations, and capability use.

```bash
# Load the profile into the kernel (Linux only, requires sudo)
sudo bash infra/scripts/load-apparmor-profile.sh

# Verify it loaded
sudo apparmor_status | grep ai-sdlc-orchestrator

# Enable in config/default.yaml
# container:
#   apparmor_profile: ai-sdlc-orchestrator

# To persist across reboots
sudo cp infra/docker/apparmor-profile /etc/apparmor.d/ai-sdlc-orchestrator
sudo systemctl reload apparmor
```

The AppArmor profile:
- Denies `sys_ptrace`, `sys_admin`, `net_raw`, `mknod`, `setuid`, `setgid`
- Restricts filesystem write access to `/tmp/`, `/home/orchestrator/.config/`, and `/workspace/runs/<run_id>/`
- Denies Docker socket access from within the container

### ⚠️ macOS Network Isolation Warning

**iptables egress filtering does NOT work on macOS Docker Desktop.** The `setup-network-policy.sh` script requires a Linux host — on macOS it exits with a warning.

**macOS users have no iptables-based egress filtering.** The container can reach all network endpoints on port 443, not just `api.anthropic.com`. The following controls still apply on macOS:
- `--read-only` rootfs
- `--cap-drop ALL`
- Seccomp profile (custom kernel call filter)
- PID/memory/CPU resource limits
- `no-new-privileges`

To achieve egress filtering on macOS, use a local HTTPS proxy with an ACL that permits only `api.anthropic.com:443` and set `HTTPS_PROXY` in your env file. Example using mitmproxy:

```bash
mitmweb --mode transparent --allowlist "api.anthropic.com"
# Add HTTPS_PROXY=http://127.0.0.1:8080 to ~/.orchestrator.env
```

### macOS Note

**AppArmor enforcement is not available on macOS Docker Desktop** — the `apparmor_profile` config field is silently skipped. All other hardening controls (seccomp, capability drops, read-only rootfs, resource limits, network isolation) still apply on macOS.

> **Summary of macOS vs Linux hardening gap:**
>
> | Control | Linux | macOS |
> |---------|-------|-------|
> | iptables egress filtering | ✅ | ❌ |
> | AppArmor MAC enforcement | ✅ (optional) | ❌ |
> | Seccomp profile | ✅ | ✅ |
> | Capability drops | ✅ | ✅ |
> | Read-only rootfs | ✅ | ✅ |
> | Resource limits | ✅ | ✅ |

### Container Security Controls Summary

| Control | Flag | Purpose |
|---------|------|---------|
| Ephemeral container | `--rm` | No state persists between runs |
| Read-only rootfs | `--read-only` | Prevents runtime code modification |
| No capabilities | `--cap-drop ALL` | Eliminates Linux privilege escalation |
| Seccomp profile | `--security-opt seccomp=...` | Blocks container-escape syscalls (ptrace, mount, mknod) |
| AppArmor (Linux) | `--security-opt apparmor=...` | MAC enforcement (Linux only) |
| PID limit | `--pids-limit 500` | Fork-bomb prevention |
| Memory limit | `--memory 8g` | Resource exhaustion prevention |
| CPU limit | `--cpus 4` | Resource exhaustion prevention |
| Network isolation | `--network orchestrator-net` | Egress limited to api.anthropic.com:443 |
| DNS injection | `--add-host` | Blocks DNS tunneling exfiltration |
| Run directory only | `--mount type=bind,...` | Container sees only its own run artifacts |
| Secrets via env file | `--env-file` | API key never appears in process listing |
| Init process | `--init` | Zombie process reaping |
