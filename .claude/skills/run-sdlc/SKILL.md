---
name: run-sdlc
description: Run the AI SDLC orchestration pipeline for a feature request
---

Invoke the AI SDLC Orchestrator through the full PM → Architect → Engineer → QA → Reviewer pipeline.

## Steps
1. If no feature request provided as argument, ask: "What feature would you like to build?"
2. Run: `orchestrate "<feature_request>"` and stream output to terminal
3. When complete, summarize: phases passed/failed, total cost, review cycles,
   artifacts at `workspace/artifacts/`, run log at `workspace/logs/run-{run_id}.jsonl`

## Options
- `--dry-run` — test without calling agents
- `--phase <name>` — single phase only
- `--config <path>` — custom config
