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
