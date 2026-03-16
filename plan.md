# AI SDLC Orchestrator — Implementation Plan

## Architecture: Native Features vs. Custom Glue

This project is already well-aligned with Claude's existing features. The architecture is:

| Layer | Mechanism | Custom? |
|-------|-----------|---------|
| Agent intelligence | `.claude/agents/*.md` (Claude Code sub-agent system) | No — native |
| Parallel isolation | `isolation: worktree` in `AgentInvocation` | No — native Claude Code feature |
| Agent invocation | `claude_agent_sdk.query()` → CLI fallback | No — native SDK |
| Quality gates | `.claude/hooks/` via `PreToolUse`/`PostToolUse`/`TaskCompleted` | No — native hook system |
| User interface | `.claude/skills/run-sdlc/SKILL.md` (Claude Code skills) | No — native |
| Orchestration loop | `engine.py` Python | **Yes — necessary** (bounded retry, cost tracking, state persistence) |
| Artifact validation | `validation.py` Python | **Yes — necessary** (quality gates need code, not agents) |
| Observability | `observability.py` using `structlog` | **Yes — thin wrapper** around existing structlog |
| CLI | `main.py` using `argparse` + `rich` | **Yes — necessary** entry point |

The prompt builders in `phases.py` are **glue**: the agent `.md` files supply the role/persona (native), prompt builders inject dynamic context (artifact paths, task data, review feedback). This two-layer split is the intended design.

---

## Context

Core framework is ~65-70% complete (Steps 1-9 of 14). The import chain is broken: `engine.py` imports `from orchestrator.observability import RunLogger` which doesn't exist yet. `main.py` (the `orchestrate` CLI entry point) also doesn't exist. Tests directory is empty.

**Goal:** Fill the 5 remaining gaps so `orchestrate --dry-run "Build a todo app"` runs end-to-end and `pytest` passes.

## Already Built — Do Not Touch

`models.py`, `config.py`, `validation.py`, `agents.py`, `phases.py`, `engine.py` (95%), all 5 schemas, all 5 `.claude/agents/*.md`, `config/default.yaml`

---

## Implementation Steps

### Step 1 — `src/orchestrator/observability.py` [BLOCKING]

Fixes the broken import in `engine.py`. Thin wrapper around `structlog` that also persists JSONL logs to disk.

**`RunLogger` class:**
```python
class RunLogger:
    def __init__(self, log_dir: Path, run_id: str) -> None:
        self.run_id = run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / f"run-{run_id}.jsonl"
        self._log_path.touch()
        self._cumulative_cost: float = 0.0
        self._lock = threading.Lock()  # asyncio.to_thread requires thread safety
        self._log = structlog.get_logger(__name__)

    def log_event(self, event_type: str, data: dict) -> None:
        # Track cost from agent_result events
        if event_type == "agent_result":
            self._cumulative_cost += data.get("cost_usd", 0.0)
        # Append to JSONL file
        record = {"ts": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id, "event": event_type, **data}
        with self._lock:
            with self._log_path.open("a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        # Live terminal output via structlog
        self._log.info(event_type, **data)

    def check_budget(self, max_budget_usd: float) -> str | None:
        if max_budget_usd <= 0:
            return None
        ratio = self._cumulative_cost / max_budget_usd
        if ratio >= 1.0: return "exceeded"
        if ratio >= 0.8: return "warning"
        return None

    @property
    def cumulative_cost_usd(self) -> float:
        return self._cumulative_cost
```

**Do NOT call `structlog.configure()` here** — `main.py` owns that.

**Verify:** `python -c "from orchestrator.observability import RunLogger; print('OK')"`

---

### Step 2 — Budget guard in `engine.py` lines 355-358

Replace the `pass` placeholder. No other engine changes.

```python
# Before each invoke_agent call in _invoke_with_retry:
if self.config.max_budget_usd and self.run_logger:
    status = self.run_logger.check_budget(self.config.max_budget_usd)
    if status == "exceeded":
        return AgentResult(success=False,
            error=f"Budget exceeded: ${self.run_logger.cumulative_cost_usd:.2f} >= ${self.config.max_budget_usd:.2f}")
    if status == "warning":
        self.run_logger.log_event("budget_warning", {
            "cumulative_cost_usd": self.run_logger.cumulative_cost_usd,
            "max_budget_usd": self.config.max_budget_usd,
        })
```

`AgentResult(success=False)` naturally flows through the existing failure path — no structural changes.

---

### Step 3 — `src/orchestrator/main.py`

CLI entry point + `validate` subcommand (used by the hook).

**Commands:**
- `orchestrate "feature request"` — full pipeline
- `orchestrate --dry-run "..."` — prints prompts, no SDK calls
- `orchestrate --phase pm "..."` — single phase
- `orchestrate --config path/to/config.yaml "..."`
- `orchestrate validate ARTIFACTS_DIR` — validates all present artifacts; used by `validate-task-completion.sh`

**Structure:**
```python
def _configure_structlog(json_logs: bool) -> None: ...   # structlog.configure() — ConsoleRenderer or JSONRenderer

def _build_parser() -> argparse.ArgumentParser: ...      # feature_request, --dry-run, --phase, --config, --log-format

def _print_summary(state, console: Console) -> None: ... # Rich table: phase / status (color) / cost / error

def _cmd_validate(args) -> None:                         # validate subcommand for hook use
    from orchestrator.models import ARTIFACT_MODELS        # ARTIFACT_MODELS.keys() has the artifact names
    from orchestrator.validation import validate_artifact_file
    # iterate artifacts_dir, validate each present .json artifact, print results, sys.exit(1) on failure

def main() -> None:
    args = parser.parse_args()
    _configure_structlog(json_logs=(args.log_format == "json"))
    config = load_config(args.config)
    # dispatch to _cmd_validate or engine.run()
    state = asyncio.run(engine.run(args.feature_request, single_phase=args.phase))
    _print_summary(state, console)
    sys.exit(1 if any failed phases else 0)
```

**Note:** The `validate` subcommand eliminates the need for inline Python in `validate-task-completion.sh` — the hook just calls `orchestrate validate "$ARTIFACTS_DIR"`.

**Verify:**
```bash
pip install -e ".[dev]"
orchestrate --help
orchestrate --dry-run "Build a todo app"
```

---

### Step 4 — Hook scripts in `.claude/hooks/`

All must be `chmod +x`. Claude Code hooks receive input via **stdin JSON** (not env vars). Parse with `jq`. Exit code **2** blocks and sends stderr as feedback to Claude. Exit code 0 allows.

**Dropped `log-tool-use.sh`** — the Python `RunLogger` already logs `agent_invoke`/`agent_result` events to the JSONL file. A PostToolUse hook would duplicate this and has no way to find the active `run_id`. Don't reinvent what the engine already does.

**`block-writes.sh`** (PreToolUse, blocking — exit 2 to block):
```bash
#!/usr/bin/env bash
# Reads stdin JSON from Claude Code hook system
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
AGENT=$(echo "$INPUT" | jq -r '.agent_type // empty')

# Only restrict the QA agent (agent file stem = "qa")
[[ "$AGENT" != "qa" ]] && exit 0

# Block write-capable tools for QA (read-only enforcement)
case "$TOOL" in
    Write|Edit|MultiEdit)
        echo "BLOCKED: QA agent is read-only — cannot use $TOOL" >&2
        exit 2 ;;
    *) exit 0 ;;
esac
```

**`validate-task-completion.sh`** (TaskCompleted, blocking — calls `orchestrate validate`):
```bash
#!/usr/bin/env bash
set -euo pipefail
INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
ARTIFACTS_DIR="${CWD:-.}/artifacts"
[[ -d "$ARTIFACTS_DIR" ]] || exit 0  # no artifacts dir yet = early phase, skip
orchestrate validate "$ARTIFACTS_DIR"
```

Uses the installed `orchestrate` CLI's `validate` subcommand rather than inline Python.

**Note:** `jq` is required. Available on macOS via `brew install jq` (already commonly installed). Add to CLAUDE.md prerequisites if needed.

---

### Step 5 — `.claude/settings.json`

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Write|Edit|MultiEdit", "command": ".claude/hooks/block-writes.sh" }
    ],
    "TaskCompleted": [
      { "command": ".claude/hooks/validate-task-completion.sh" }
    ]
  }
}
```

- `matcher` on PreToolUse filters to write-capable tools only — no need to run the hook for Read/Grep/Glob calls
- `log-tool-use.sh` is dropped (Python `RunLogger` already handles observability)
- Keep separate from `settings.local.json` (which holds permissions). Do not merge.

---

### Step 6 — Tests

**`pyproject.toml` change** — add under `[tool.pytest.ini_options]`:
```toml
asyncio_mode = "auto"
```

**`tests/conftest.py`** — shared fixtures: `tmp_workspace` (tmp_path + artifacts/ subdir), `valid_prd_data`, `valid_tasks_data`

**`tests/test_models.py`** — Pydantic model validation:
- `Requirement`: valid id, invalid id pattern, invalid priority
- `PRD`: valid, overview < 50 chars fails, empty goals fails, `model_dump()` round-trip
- `Task`: valid, invalid `assigned_role`
- `RunState`: zero-cost defaults, `model_dump()` shape
- Enum spot-checks (`PhaseStatus.FAILED.value == "failed"`)

**`tests/test_validation.py`** — Two-layer validation:
- Missing file → invalid + "not found"
- Invalid JSON → invalid + "Invalid JSON"
- Valid PRD → valid
- Missing required field → JSON schema layer fails
- Short overview → Pydantic layer fails
- Unknown artifact name → invalid + "No JSON schema"
- `validate_all_artifacts` partial (prd present, architecture missing)

**`tests/test_phases.py`** — Prompt builders:
- `build_pm_prompt` includes feature_request, references `prd.json`
- `build_engineer_prompt` with task_data embeds `task_id`; without it references `tasks.json`
- `build_reviewer_prompt` cycle 1 has no "Previous Review"; cycle 2 with prior review includes it
- `get_engineer_tasks` returns empty list without tasks.json; filters to `assigned_role == "engineer"`
- `PHASE_DEFINITIONS` has all 5 phases with non-empty `agent_name`

**`tests/test_engine.py`** — Engine orchestration (mock `invoke_agent` with `AsyncMock`):
- `PHASE_ORDER == ["pm", "architect", "engineer", "qa", "reviewer"]`
- `_tasks_have_file_conflicts`: no conflict, with conflict, empty files, empty list
- Dry-run full run: all phases COMPLETED, `state.json` written
- Dry-run single phase: only that phase in `state.phases`
- Mocked failing `invoke_agent`: phase marked FAILED, pipeline stops
- Approve review ends cycle loop immediately (`state.review_cycles == 0`)

**Verify:** `pytest tests/ -v`

---

### Step 7 — `.claude/skills/run-sdlc/SKILL.md`

Uses Claude Code's native skill system. Directory already exists.

```markdown
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
```

---

## Verification Checklist

| Step | Command |
|------|---------|
| 1 | `python -c "from orchestrator.observability import RunLogger; print('OK')"` |
| 2 | `python -c "from orchestrator.engine import OrchestratorEngine; print('OK')"` |
| 3 | `orchestrate --help` then `orchestrate --dry-run "Build a todo app"` |
| 4 | `bash -n .claude/hooks/*.sh` (syntax check); verify `jq` is installed |
| 5 | `python -c "import json; json.load(open('.claude/settings.json'))"` |
| 6 | `pytest tests/ -v` |
| 7 | `/run-sdlc` visible in Claude Code |

**End-to-end smoke test:**
```bash
orchestrate --phase pm "Add dark mode toggle to settings page"
cat workspace/artifacts/prd.json
```

---

## Files to Create / Edit

| File | Action |
|------|--------|
| `src/orchestrator/observability.py` | **Create** |
| `src/orchestrator/main.py` | **Create** (includes `validate` subcommand) |
| `src/orchestrator/engine.py` | **Edit** lines 355-358 only |
| `.claude/hooks/block-writes.sh` | **Create** |
| `.claude/hooks/validate-task-completion.sh` | **Create** |
| `.claude/settings.json` | **Create** |
| `tests/conftest.py` | **Create** |
| `tests/test_models.py` | **Create** |
| `tests/test_validation.py` | **Create** |
| `tests/test_phases.py` | **Create** |
| `tests/test_engine.py` | **Create** |
| `.claude/skills/run-sdlc/SKILL.md` | **Create** |
| `pyproject.toml` | **Edit** — add `asyncio_mode = "auto"` |
