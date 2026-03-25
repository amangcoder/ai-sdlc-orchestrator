# Container Documentation Index

**Last Updated:** 2026-03-24
**Status:** Complete
**Audience:** Developers, DevOps, Security, Operators

This index guides you to the right documentation for containerized orchestration. The feature wraps each orchestration run in an ephemeral, isolated Docker container for security isolation.

---

## Documentation Map

### For Operators & End Users

**Start here if you want to understand how to use containerized orchestration:**

- **[CLAUDE.md — Containerized Orchestration Section](../CLAUDE.md#containerized-orchestration)** (Primary reference)
  - Quick setup instructions
  - Daily workflow examples
  - Security verification checklist
  - macOS & Linux-specific guidance
  - Troubleshooting quick links

- **[CONTAINER_TROUBLESHOOTING.md](./CONTAINER_TROUBLESHOOTING.md)** (Diagnostic & resolution)
  - Common errors and solutions
  - Performance issues
  - Log file locations
  - Escalation procedures

### For Platform & DevOps Engineers

**Start here if you need to maintain or extend the infrastructure:**

- **[CONTAINER_SECURITY_ARCHITECTURE.md](./CONTAINER_SECURITY_ARCHITECTURE.md)** (Deep dive)
  - Threat model and attack surface analysis
  - Control composition and defense-in-depth
  - Operational procedures (setup, maintenance, diagnostics)
  - Platform-specific limitations (macOS vs Linux)
  - References to security standards (NIST, CIS)

- **[docs/adr/0001-containerized-orchestration.md](./adr/0001-containerized-orchestration.md)** (Design rationale)
  - Decision and alternatives considered
  - Consequences (positive and negative)
  - Related decisions and open questions
  - Notes for implementers

### For Backend & Platform Engineers

**Start here if you need to build on the container runtime API:**

- **[CONTAINER_API.md](./CONTAINER_API.md)** (Developer reference)
  - `ArtifactBridgeVolume` class — bind-mount management
  - `ContainerRuntime` class — container lifecycle
  - `orchestrate-container` CLI entry point
  - Configuration models (`ContainerConfig`)
  - Integration examples and test patterns
  - Error handling guide
  - Performance considerations

---

## Quick Navigation

### "I want to..."

| Goal | Document | Section |
|------|----------|---------|
| **Use containerized orchestration** | CLAUDE.md | [Containerized Orchestration](../CLAUDE.md#containerized-orchestration) |
| **Set up Docker containers for the first time** | CLAUDE.md | [Prerequisites](../CLAUDE.md#prerequisites) + [One-Time Setup](../CLAUDE.md#one-time-setup) |
| **Run an orchestration in a container** | CLAUDE.md | [Daily Workflow](../CLAUDE.md#daily-workflow) |
| **Troubleshoot a failed run** | CONTAINER_TROUBLESHOOTING.md | [Common Issues & Solutions](./CONTAINER_TROUBLESHOOTING.md#common-issues--solutions) |
| **Understand the security model** | CONTAINER_SECURITY_ARCHITECTURE.md | [Security Controls](./CONTAINER_SECURITY_ARCHITECTURE.md#security-controls) |
| **Verify security controls are working** | CLAUDE.md | [Security Verification Checklist](../CLAUDE.md#security-verification-checklist) |
| **Understand why containers were chosen** | docs/adr/0001-containerized-orchestration.md | [Decision](./adr/0001-containerized-orchestration.md#decision) + [Rationale](./adr/0001-containerized-orchestration.md#rationale) |
| **Use the ContainerRuntime API** | CONTAINER_API.md | [Module: orchestrator.container_runner](./CONTAINER_API.md#module-orchestratorcontainer_runner) |
| **Call orchestrate-container from Python** | CONTAINER_API.md | [Module: orchestrator.containerized_main](./CONTAINER_API.md#module-orchestratorcontainerized_main) |
| **Understand the threat model** | CONTAINER_SECURITY_ARCHITECTURE.md | [Threat Model](./CONTAINER_SECURITY_ARCHITECTURE.md#threat-model) |
| **Set up network isolation** | CONTAINER_SECURITY_ARCHITECTURE.md | [Network Isolation (iptables + Bridge Network)](./CONTAINER_SECURITY_ARCHITECTURE.md#9-network-isolation-iptables--bridge-network) |
| **Handle AppArmor on Linux** | CLAUDE.md | [AppArmor Profile (Linux only)](../CLAUDE.md#apparmor-profile-linux-only) |
| **Use containers on macOS** | CLAUDE.md | [macOS Network Isolation Warning](../CLAUDE.md#-macos-network-isolation-warning) |
| **Scale containers in production** | docs/adr/0001-containerized-orchestration.md | [Consequences](./adr/0001-containerized-orchestration.md#consequences) |
| **Debug container startup** | CONTAINER_TROUBLESHOOTING.md | [Diagnostics](./CONTAINER_TROUBLESHOOTING.md#check-docker-status) |
| **Monitor container performance** | CONTAINER_TROUBLESHOOTING.md | [Performance Issues](./CONTAINER_TROUBLESHOOTING.md#performance-issues) |

---

## Document Purposes & Structure

### CLAUDE.md — Containerized Orchestration Section

**Purpose:** User-facing operational guide for developers and operators
**Content Type:** Procedural / How-to
**Audience:** Everyone who runs `orchestrate-container`
**Key Sections:**
- Prerequisites (Docker version check)
- One-Time Setup (build image, network policy, env file)
- Daily Workflow (basic usage examples)
- Stale Image Warning (rebuild requirement)
- Network IP Refresh (IP rotation handling)
- Orphaned Container Recovery (cleanup procedures)
- Security Verification Checklist (manual verification steps)
- AppArmor & macOS Notes (platform-specific guidance)
- Container Security Controls Summary (table of controls)

### CONTAINER_SECURITY_ARCHITECTURE.md

**Purpose:** Comprehensive security architecture documentation for security reviews and compliance
**Content Type:** Architecture / Reference
**Audience:** Security engineers, platform engineers, compliance teams
**Key Sections:**
- Executive Summary
- Threat Model (actors, threats, attack surfaces)
- Security Controls (12 layered controls with validation)
- Control Composition & Defense in Depth
- Operational Procedures (setup, verification, maintenance)
- Limitations & Gaps
- References & Contact

### CONTAINER_API.md

**Purpose:** API reference for developers extending or integrating the container runtime
**Content Type:** API Reference / Developer Guide
**Audience:** Backend engineers, platform engineers
**Key Sections:**
- Overview
- `ArtifactBridgeVolume` class (bind-mount management)
- `ContainerRuntime` class (container lifecycle)
- `orchestrate-container` CLI entry point
- Configuration models
- Integration examples
- Error handling
- Testing guide
- Performance considerations

### docs/adr/0001-containerized-orchestration.md

**Purpose:** Architecture Decision Record documenting the design rationale and alternatives
**Content Type:** ADR (Architecture Decision Record)
**Audience:** Architects, senior engineers, future maintainers
**Key Sections:**
- Context (problem statement)
- Decision (what was chosen)
- Rationale (why this design)
- Implementation (how it works)
- Consequences (tradeoffs)
- Alternatives Considered (why not other options)
- Related Decisions & Validation

### CONTAINER_TROUBLESHOOTING.md

**Purpose:** Diagnostic and resolution guide for common operational issues
**Content Type:** Troubleshooting / FAQ
**Audience:** Operators, developers, support engineers
**Key Sections:**
- Quick Diagnostics (basic checks)
- Common Issues & Solutions (20+ scenarios)
- Performance Issues (monitoring and optimization)
- Log Files & Debugging (locations and inspection)
- Escalation & Support (when/how to get help)

---

## How These Documents Relate

```
┌─────────────────────────────────────────────────────────┐
│ CLAUDE.md (Containerized Orchestration Section)        │
│ ├─ User-facing operational guide                       │
│ └─ Links to deeper docs for specific topics            │
└─────────┬──────────────────────────────────────────────┘
          │
    ┌─────┴──────┬──────────────┬─────────────────┐
    │            │              │                 │
    ▼            ▼              ▼                 ▼
┌──────────┐ ┌─────────┐ ┌──────────┐ ┌──────────────┐
│ CONTAINER│ │CONTAINER│ │docs/adr/ │ │  CONTAINER  │
│SECURITY  │ │  API    │ │  0001    │ │TROUBLESHOOT │
│ARCHITECT │ │  .md    │ │  .md     │ │   ING.md    │
│  URE.md  │ │         │ │          │ │             │
└──────────┘ └─────────┘ └──────────┘ └──────────────┘
     ▲            ▲            ▲             ▲
     │            │            │             │
     └────────────┴────────────┴─────────────┘
            Referenced when
        users click links or
          need more detail
```

---

## Implementation Status

| Component | Status | Documentation |
|-----------|--------|-----------------|
| ContainerConfig model | ✅ Complete | CONTAINER_API.md, CLAUDE.md |
| ContainerRuntime class | ✅ Complete | CONTAINER_API.md, ADR-0001 |
| ArtifactBridgeVolume class | ✅ Complete | CONTAINER_API.md |
| orchestrate-container CLI | ✅ Complete | CONTAINER_API.md, CLAUDE.md |
| Dockerfile + build script | ✅ Complete | CLAUDE.md, CONTAINER_TROUBLESHOOTING.md |
| Network policy (iptables) | ✅ Complete | CONTAINER_SECURITY_ARCHITECTURE.md, CLAUDE.md |
| Seccomp profile | ✅ Complete | CONTAINER_SECURITY_ARCHITECTURE.md, ADR-0001 |
| AppArmor profile (Linux) | ✅ Complete | CONTAINER_SECURITY_ARCHITECTURE.md, CLAUDE.md |
| Unit tests | ✅ Complete | CONTAINER_API.md |
| Integration tests | ✅ Complete | CONTAINER_TROUBLESHOOTING.md |

---

## Audience-Specific Reading Paths

### Path 1: "I'm a developer running `orchestrate-container` for the first time"
1. Read: [CLAUDE.md — Prerequisites](../CLAUDE.md#prerequisites)
2. Read: [CLAUDE.md — One-Time Setup](../CLAUDE.md#one-time-setup)
3. Read: [CLAUDE.md — Daily Workflow](../CLAUDE.md#daily-workflow)
4. Bookmark: [CONTAINER_TROUBLESHOOTING.md](./CONTAINER_TROUBLESHOOTING.md) for when things go wrong

### Path 2: "I'm a security engineer reviewing the design"
1. Read: [docs/adr/0001-containerized-orchestration.md](./adr/0001-containerized-orchestration.md)
2. Read: [CONTAINER_SECURITY_ARCHITECTURE.md](./CONTAINER_SECURITY_ARCHITECTURE.md)
3. Review: [CLAUDE.md — Security Verification Checklist](../CLAUDE.md#security-verification-checklist)
4. Reference: [CONTAINER_API.md — Error Handling](./CONTAINER_API.md#error-handling)

### Path 3: "I'm a backend engineer extending the container runtime"
1. Read: [CONTAINER_API.md — Overview](./CONTAINER_API.md#overview)
2. Study: [CONTAINER_API.md — ArtifactBridgeVolume](./CONTAINER_API.md#artifactbridgevolume)
3. Study: [CONTAINER_API.md — ContainerRuntime](./CONTAINER_API.md#containerruntime)
4. Reference: [CONTAINER_API.md — Integration Examples](./CONTAINER_API.md#integration-examples)
5. Check: [CONTAINER_API.md — Testing](./CONTAINER_API.md#testing)

### Path 4: "I'm debugging a container issue"
1. Jump to: [CONTAINER_TROUBLESHOOTING.md — Common Issues & Solutions](./CONTAINER_TROUBLESHOOTING.md#common-issues--solutions)
2. Run: [CONTAINER_TROUBLESHOOTING.md — Quick Diagnostics](./CONTAINER_TROUBLESHOOTING.md#quick-diagnostics)
3. Check: [CONTAINER_TROUBLESHOOTING.md — Log Files & Debugging](./CONTAINER_TROUBLESHOOTING.md#log-files--debugging)
4. Escalate: [CONTAINER_TROUBLESHOOTING.md — Escalation & Support](./CONTAINER_TROUBLESHOOTING.md#escalation--support)

### Path 5: "I'm setting up production infrastructure"
1. Read: [docs/adr/0001-containerized-orchestration.md — Consequences](./adr/0001-containerized-orchestration.md#consequences)
2. Review: [CONTAINER_SECURITY_ARCHITECTURE.md — Operational Procedures](./CONTAINER_SECURITY_ARCHITECTURE.md#operational-procedures)
3. Study: [CONTAINER_SECURITY_ARCHITECTURE.md — Limitations & Gaps](./CONTAINER_SECURITY_ARCHITECTURE.md#limitations--gaps)
4. Plan: Network policy refresh strategy from [CONTAINER_SECURITY_ARCHITECTURE.md](./CONTAINER_SECURITY_ARCHITECTURE.md#network-policy)
5. Implement: Deployment automation from [CONTAINER_API.md — Integration Examples](./CONTAINER_API.md#integration-examples)

---

## Key Concepts Cross-Reference

### "Ephemeral Isolation"
- **Why it matters:** Prevents state leakage between runs
- **Implemented via:** `docker run --rm`
- **Documented in:**
  - [CONTAINER_SECURITY_ARCHITECTURE.md — Ephemeral Isolation](./CONTAINER_SECURITY_ARCHITECTURE.md#1-ephemeral-isolation)
  - [ADR-0001 — Threats](./adr/0001-containerized-orchestration.md#threats)
  - [CLAUDE.md — Ephemeral container](../CLAUDE.md#container-security-controls-summary)

### "Bind-Mount Isolation"
- **Why it matters:** Limits container access to only its run artifacts
- **Implemented via:** `ArtifactBridgeVolume` class
- **Documented in:**
  - [CONTAINER_API.md — ArtifactBridgeVolume](./CONTAINER_API.md#artifactbridgevolume)
  - [ADR-0001 — Bind-Mount Design](./adr/0001-containerized-orchestration.md#why-bind-mount-not-full-workspace)
  - [CONTAINER_SECURITY_ARCHITECTURE.md — Read-Only Rootfs](./CONTAINER_SECURITY_ARCHITECTURE.md#2-read-only-rootfs)

### "Network Isolation"
- **Why it matters:** Prevents unauthorized network calls and exfiltration
- **Implemented via:** iptables rules + DNS injection
- **Documented in:**
  - [CONTAINER_SECURITY_ARCHITECTURE.md — Network Isolation](./CONTAINER_SECURITY_ARCHITECTURE.md#9-network-isolation-iptables--bridge-network)
  - [CLAUDE.md — Network isolation](../CLAUDE.md#network-isolation-orchestrator-net)
  - [CONTAINER_TROUBLESHOOTING.md — API calls fail](./CONTAINER_TROUBLESHOOTING.md#api-calls-fail-with-connection-timeout)

### "Seccomp Filtering"
- **Why it matters:** Blocks dangerous syscalls (ptrace, mount, etc.)
- **Implemented via:** Custom seccomp profile
- **Documented in:**
  - [CONTAINER_SECURITY_ARCHITECTURE.md — Seccomp Filter](./CONTAINER_SECURITY_ARCHITECTURE.md#4-seccomp-filter-custom)
  - [ADR-0001 — Seccomp Design](./adr/0001-containerized-orchestration.md#rationale)
  - [CONTAINER_TROUBLESHOOTING.md — Seccomp profile not found](./CONTAINER_TROUBLESHOOTING.md#seccomp-profile-not-found)

---

## Related Documentation

- [USAGE.md](./USAGE.md) — General orchestrator usage guide
- [Speed Modes Developer Guide](./speed-modes.md) — Speed mode implementation details
- [Log Stream Intelligence Analyzer](./features/log-stream-intelligence-analyzer.md) — Log analysis feature

---

## Feedback & Contributions

This documentation is maintained alongside the implementation. If you find:

- **Errors or inconsistencies** — File an issue with the exact location and correction
- **Missing examples** — Suggest additional integration examples
- **Unclear explanations** — Propose clarifications with context
- **Security gaps** — Report via security@ (not public issues)

---

## Document Maintenance

- **Last reviewed:** 2026-03-24
- **Next review:** Whenever src/orchestrator/container_*.py changes
- **Update checklist:**
  - [ ] Update ADR if design changes
  - [ ] Update API docs if interfaces change
  - [ ] Update troubleshooting if new errors appear
  - [ ] Update CLAUDE.md if operational procedures change
  - [ ] Update this index if new docs are added
