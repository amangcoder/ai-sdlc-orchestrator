# Feature: Log Stream Intelligence Analyzer

## Feature Prompt

Add a post-run log analysis command that reads JSONL run logs produced by
RunLogger and surfaces two categories of inefficiency:

1. **INDEXABLE DATA** — Files, directories, or knowledge queries that multiple
   agents fetched independently during the same run (or across recent runs),
   which could have been pre-indexed via AICoder/MCP before the pipeline starts.

2. **REPETITIVE ACTIONS** — Tool calls or prompt patterns that appear 2+ times
   within a single run with identical or near-identical inputs (same file path,
   same grep pattern, same MCP query, same sub-prompt fragment), which are
   candidates for caching or summarization.

## CLI Interface

```bash
orchestrate --analyze-logs [run-id | --last N]
```

Output a human-readable (and optionally `--json`) report:
- Top redundant file reads (path → count, agents that read it)
- Top repeated tool calls (tool_name + normalized args → count)
- Prompt fragments appearing in 3+ agent prompts verbatim
- Suggested AICoder pre-index targets (files/dirs worth indexing before the next similar run)
- Estimated token waste (sum of input_tokens for duplicate reads)

## Implementation Constraints

- **Source data:** `run-{id}.jsonl` files written by `RunLogger.log_event()`
  Events of interest: `agent_invoke` (has "prompt"), `task_invoke`, `tool_use`
  (if logged), `agent_result` (has input_tokens cost).
- **Cross-run analysis:** optionally compare last N runs (`--last N` flag) to find
  patterns that repeat across runs, not just within one.
- **Near-duplicate detection for prompts:** use 8-gram fingerprint or MinHash
  (no heavy ML deps) — flag fragments shared by >50% of agent prompts.
- **Output must be usable offline** (no LLM call required for basic analysis).
  Optional: `--llm` flag to pass the report through Haiku for a narrative
  "top 3 recommendations" summary.
- **New module:** `src/orchestrator/log_analyzer.py`
- **New CLI flag** wired in `main.py` / cli entry point.
- No changes to `RunLogger` or the hot path.

## What Each Part Targets

| Insight | Signal in logs | Action surfaced |
|---|---|---|
| Indexable data | Same file path in `agent_invoke` prompts of 2+ agents | Pre-index with AICoder before run |
| Repetitive reads | `tool_use` events with identical `Read`/`Grep` args | Cache result in artifact or summarize once |
| Prompt bloat | Common verbatim fragments across agent prompts | Extract to shared context / prompt template |
| Token waste | `input_tokens` on duplicate fetches | Quantified cost for prioritization |

## Notes

- Run as `orchestrate --speed turbo "..."` — single new module + CLI flag, no schema changes.
- Cross-run patterns are the higher-value signal; within-run is easier to detect first.
