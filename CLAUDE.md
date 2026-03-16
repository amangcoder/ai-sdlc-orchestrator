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
- **Model routing by complexity** — Opus for architecture/review, Sonnet for implementation/QA, Haiku for simple tasks
- **Isolated parallel execution** — engineers use `isolation: worktree` to prevent file conflicts
- **Schema-validated artifacts** — every inter-agent artifact has a JSON schema; phase completion blocks until validation passes
