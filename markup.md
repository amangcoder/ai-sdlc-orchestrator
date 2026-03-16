# AI SDLC Orchestrator — PRD & Development Plan

## 1. Product Overview

### 1.1 Problem Statement

Building software features with AI agents today requires manual coordination: invoking agents one at a time, copying context between sessions, validating outputs by hand, and managing the flow from requirements through code review. This is slow, error-prone, and doesn't scale.

### 1.2 Solution

An **AI SDLC Orchestrator** — a Python-based pipeline that coordinates specialized AI agents through a complete Software Development Life Cycle. Each agent has a defined role (PM, Architect, Engineer, QA, Reviewer), receives structured inputs, produces schema-validated artifacts, and hands off to the next phase automatically.

### 1.3 Design Principles

- **Structured workflow with validation checkpoints** — not "deterministic" (LLMs are inherently non-deterministic), but reproducible workflows with enforced quality gates
- **Bounded statefulness** — agents maintain session context during their active phase but checkpoint results as artifacts for cross-phase communication
- **Model routing by task complexity** — sonnet for architecture/review, Sonnet for implementation/QA, Haiku for simple tasks. Route at assignment time, not by failure-retry
- **Isolated parallel execution** — engineers use `isolation: worktree` to prevent file conflicts during parallel work
- **Schema-validated artifacts** — every inter-agent artifact has a JSON schema; phase completion blocks until validation passes
- **Cost-controlled** — per-agent `max_turns`, per-run budget ceiling, cost tracking in observability logs

---

## 2. Pipeline Architecture

### 2.1 Phase Flow

```
User Feature Request
        │
        v
  [Orchestrator (Python)]
        │
        v
  Phase 1: PM Agent ──────> artifacts/prd.json
        │
        v
  Phase 2: Architect Agent ─> artifacts/architecture.json + artifacts/tasks.json
        │
        v
  Phase 3: Engineers (parallel, isolated worktrees) ──> code changes
        │
        v
  Phase 4: QA Agent ────────> artifacts/qa_report.json
        │
        v
  Phase 5: Reviewer Agent ──> artifacts/review.json
        │
        │──[reject/request_changes]──> Phase 3 (with feedback, max 3 cycles)
        │
        v
  Phase 6: Merge & Report
```

### 2.2 Agent Definitions

| Agent | Model | Max Turns | Input Artifacts | Output Artifacts | Notes |
|---|---|---|---|---|---|
| PM | sonnet | 30 | — | `prd.json` | Read-only access to codebase |
| Architect | sonnet | 40 | `prd.json` | `architecture.json`, `tasks.json` | Read-only access to codebase |
| Engineer | sonnet | 80 | `prd.json`, `architecture.json`, `tasks.json` | code changes | Write access; parallel with worktrees |
| QA | sonnet | 40 | `prd.json`, `tasks.json` | `qa_report.json` | Read-only; blocked from writes via hook |
| Reviewer | sonnet | 30 | `prd.json`, `architecture.json`, `tasks.json`, `qa_report.json` | `review.json` | Read-only access |

### 2.3 Model Routing & Escalation

| Agent | Default Model | On Retry | Rationale |
|---|---|---|---|
| PM | sonnet | human escalation | Requirements need deep reasoning |
| Architect | sonnet | human escalation | System design is highest complexity |
| Engineer | sonnet | sonnet | Volume task; balance cost/quality |
| QA | sonnet | sonnet | Needs reasoning but not architecture-level |
| Reviewer | sonnet | human escalation | Final quality gate must be highest quality |

### 2.4 Review Cycle

- Reviewer produces a verdict: `approve`, `reject`, or `request_changes`
- On `reject` or `request_changes`: feed review feedback back to Engineer → QA → Reviewer loop
- Maximum 3 review cycles before escalating to human
- Each cycle re-validates all artifacts

---

## 3. Artifact Schemas

### 3.1 PRD (`prd.json`)

```json
{
  "title": "string (min 1 char)",
  "overview": "string (min 50 chars)",
  "goals": ["string (min 1 item)"],
  "requirements": [
    {
      "id": "REQ-NNN (pattern: ^REQ-\\d+$)",
      "description": "string",
      "priority": "must | should | could"
    }
  ],
  "constraints": ["string"],
  "acceptance_criteria": ["string (min 1 item)"]
}
```

### 3.2 Architecture (`architecture.json`)

```json
{
  "components": [
    {
      "name": "string",
      "responsibility": "string",
      "interfaces": ["string"],
      "dependencies": ["string"]
    }
  ],
  "data_flow": "string (min 20 chars)",
  "tech_decisions": [
    {
      "decision": "string",
      "rationale": "string",
      "alternatives_considered": ["string"]
    }
  ],
  "constraints": ["string"]
}
```

### 3.3 Tasks (`tasks.json`)

```json
{
  "tasks": [
    {
      "task_id": "TASK-NNN (pattern: ^TASK-\\d+$)",
      "title": "string",
      "description": "string (min 10 chars)",
      "assigned_role": "engineer | qa",
      "dependencies": ["TASK-NNN"],
      "acceptance_criteria": ["string (min 1 item)"],
      "files_to_modify": ["string"],
      "estimated_complexity": "low | medium | high"
    }
  ]
}
```

### 3.4 QA Report (`qa_report.json`)

```json
{
  "test_results": {
    "passed": "integer >= 0",
    "failed": "integer >= 0",
    "skipped": "integer >= 0"
  },
  "lint_clean": "boolean",
  "type_check_clean": "boolean",
  "issues": [
    {
      "severity": "critical | major | minor",
      "file": "string (optional)",
      "line": "integer >= 1 (optional)",
      "description": "string",
      "suggestion": "string (optional)"
    }
  ],
  "verdict": "pass | fail"
}
```

### 3.5 Review (`review.json`)

```json
{
  "verdict": "approve | reject | request_changes",
  "issues": [
    {
      "severity": "critical | major | minor | nit",
      "file": "string",
      "line": "integer >= 1 (optional)",
      "description": "string",
      "suggestion": "string (optional)"
    }
  ],
  "summary": "string (min 20 chars)"
}
```

---

## 4. Project Structure

```
Orchestrator/
├── .claude/
│   ├── settings.json                # Project hooks and permissions
│   ├── agents/
│   │   ├── pm.md                    # Product Manager sub-agent
│   │   ├── architect.md             # System Architect sub-agent
│   │   ├── engineer.md              # Engineer sub-agent
│   │   ├── qa.md                    # QA sub-agent
│   │   └── reviewer.md             # Code Reviewer sub-agent
│   ├── skills/
│   │   └── run-sdlc/
│   │       └── SKILL.md             # /run-sdlc slash command
│   └── hooks/
│       ├── validate-task-completion.sh  # Validates artifact schemas on phase completion
│       ├── log-tool-use.sh              # Logs all tool invocations (async)
│       └── block-writes.sh             # Blocks QA agent from writing files
├── src/
│   └── orchestrator/
│       ├── __init__.py
│       ├── main.py                  # CLI entry point (argparse)
│       ├── engine.py                # Core orchestration loop
│       ├── phases.py                # Phase definitions + prompt builders
│       ├── models.py                # Pydantic models for artifacts and state
│       ├── config.py                # YAML configuration loading
│       ├── agents.py                # SDK query() wrapper + CLI fallback
│       ├── validation.py            # JSON schema + Pydantic validation
│       └── observability.py         # Structured logging + cost tracking
├── src/schemas/
│   ├── prd.schema.json
│   ├── tasks.schema.json
│   ├── architecture.schema.json
│   ├── qa_report.schema.json
│   └── review.schema.json
├── config/
│   └── default.yaml                 # Orchestrator configuration
├── tests/
│   ├── test_engine.py
│   ├── test_phases.py
│   ├── test_validation.py
│   └── test_models.py
├── pyproject.toml
└── CLAUDE.md
```

---

## 5. Key Dependencies

```
claude-agent-sdk>=0.1.0    # Core agent invocation via SDK
pydantic>=2.0              # Artifact schema models + validation
pyyaml>=6.0                # Configuration file parsing
jsonschema>=4.0            # JSON Schema validation (structural layer)
rich>=13.0                 # Terminal progress display
structlog>=24.0            # Structured logging + observability
```

Dev dependencies:
```
pytest>=8.0
pytest-asyncio>=0.23
```

---

## 6. Component Specifications

### 6.1 `main.py` — CLI Entry Point

- Uses `argparse` for CLI interface
- Commands:
  - `orchestrate "feature request"` — full pipeline run
  - `orchestrate --dry-run "feature request"` — walks all phases, prints prompts, no SDK calls
  - `orchestrate --phase pm "feature request"` — runs a single phase only
  - `orchestrate --config path/to/config.yaml "feature request"` — custom config
- Configures structured logging via `structlog`
- Calls `asyncio.run()` on the engine

### 6.2 `engine.py` — Core Orchestration Engine

**Class: `OrchestratorEngine`**

- Constructor: takes `OrchestratorConfig` and `dry_run: bool`
- `async run(feature_request, single_phase=None) -> RunState`: Main entry point
  - Creates workspace directory and `artifacts/` subdirectory
  - Generates a `run_id` (12-char hex UUID)
  - Iterates through phases in order: pm → architect → engineer → qa → reviewer
  - Stops pipeline on phase failure
  - After reviewer phase, enters review cycle loop if verdict != approve

**Phase execution (`_run_phase`)**:
  - Gets phase definition and agent config
  - Sets phase status to RUNNING
  - Builds prompt via phase definition's prompt builder
  - In dry-run mode: logs prompt and marks COMPLETED
  - Otherwise: invokes agent with retry, validates output artifacts, marks COMPLETED or FAILED

**Engineer phase (`_run_engineer_phase`)**:
  - Loads tasks from `tasks.json`, filters to `assigned_role == "engineer"`
  - Checks for file conflicts between tasks (overlapping `files_to_modify`)
  - If no conflicts and config allows parallel: runs all tasks concurrently with `isolation: worktree`
  - If conflicts or sequential config: runs tasks one by one
  - Each task gets its own `AgentInvocation` with the specific task data in the prompt

**Review cycle (`_handle_review_cycle`)**:
  - Reads `review.json` verdict
  - If `approve`: done
  - If `reject` or `request_changes`: re-run engineer (with review feedback appended to prompt) → QA → reviewer
  - Maximum cycles controlled by `max_review_cycles` (default 3)
  - On max cycles reached: logs warning about human escalation

**Retry with escalation (`_invoke_with_retry`)**:
  - Tries up to `max_retries + 1` attempts
  - On failure: if agent has `escalation_model`, upgrades model tier for next attempt
  - Logs each attempt and result via `RunLogger`

**File conflict detection (`_tasks_have_file_conflicts`)**:
  - Takes list of tasks, collects all `files_to_modify`
  - Returns `True` if any file appears in multiple tasks

### 6.3 `phases.py` — Phase Definitions & Prompt Builders

**Each phase has a prompt builder function** that takes `(feature_request, workspace, config)` and returns a string prompt.

- `build_pm_prompt`: Instructs agent to analyze the feature request, explore the codebase, and produce `prd.json`
- `build_architect_prompt`: Points to `prd.json`, instructs agent to produce `architecture.json` and `tasks.json`
- `build_engineer_prompt`: Accepts optional `task_data` dict for a specific task; points to all upstream artifacts
- `build_qa_prompt`: Points to `prd.json` and `tasks.json`; instructs running tests/linters; reminds agent it's read-only
- `build_reviewer_prompt`: Points to all artifacts; accepts `review_cycle` number and `previous_review` data for iteration context

**`get_engineer_tasks(workspace)`**: Loads `tasks.json` and returns only tasks with `assigned_role == "engineer"`

**`PHASE_DEFINITIONS` dict**: Maps phase name → `PhaseDefinition` dataclass with name, agent_name, build_prompt callable, output_artifacts list, and parallel flag.

### 6.4 `models.py` — Pydantic Models

**Enums**: `Priority`, `Complexity`, `IssueSeverity`, `QAVerdict`, `ReviewVerdict`, `PhaseStatus`, `TaskStatus`, `ModelTier`

**Artifact models** (mirror JSON schemas):
- `PRD` — title, overview, goals, requirements (list of `Requirement`), constraints, acceptance_criteria
- `Architecture` — components (list of `Component`), data_flow, tech_decisions (list of `TechDecision`), constraints
- `TaskList` — tasks (list of `Task`)
- `QAReport` — test_results (`TestResults`), lint_clean, type_check_clean, issues (list of `QAIssue`), verdict
- `Review` — verdict, issues (list of `ReviewIssue`), summary

**State models**:
- `PhaseState` — status, retry_count, model_tier, cost_usd, error
- `EngTaskState` — task_id, status, worktree_path, retry_count
- `RunState` — run_id, feature_request, workspace_dir, phases dict, engineering_tasks list, total_cost_usd, review_cycles, max_review_cycles

**Config models**:
- `AgentConfig` — name, model, max_turns, escalation_model, input_artifacts, output_artifacts
- `PhaseConfig` — agent, parallel, max_retries, timeout_minutes
- `OrchestratorConfig` — workspace_dir, max_review_cycles, max_budget_usd, phases dict, agents dict

**`ARTIFACT_MODELS`**: Dict mapping artifact name strings to their Pydantic model classes for validation dispatch.

### 6.5 `config.py` — Configuration Loading

- `load_config(config_path=None) -> OrchestratorConfig`
- Reads YAML file (defaults to `config/default.yaml`)
- Parses phases and agents sections into typed Pydantic models
- Converts string model names to `ModelTier` enums

### 6.6 `agents.py` — SDK Wrapper

**`AgentInvocation` dataclass**: agent_name, prompt, model, max_turns, workspace_dir, isolation

**`AgentResult` dataclass**: success, output, cost_usd, turns_used, error

**`invoke_agent(invocation) -> AgentResult`**:
- Tries `claude_agent_sdk.query()` first (SDK mode)
- Falls back to `claude` CLI subprocess if SDK not installed
- SDK mode: maps model tier to model string, passes agent file path, workspace dir, isolation mode
- CLI mode: builds `claude -p` command with `--model`, `--max-turns`, `--output-format json`, `--agent` flags
- Parses CLI JSON output for cost data

**`invoke_agents_parallel(invocations) -> list[AgentResult]`**:
- Uses `asyncio.gather()` to run multiple agent invocations concurrently

### 6.7 `validation.py` — Schema Validation

**Two-layer validation**:
1. **JSON Schema** (structural) — validates against `src/schemas/{name}.schema.json` using `jsonschema.Draft202012Validator`
2. **Pydantic** (semantic) — validates against the corresponding Pydantic model from `ARTIFACT_MODELS`

**`validate_artifact_file(path, name) -> ValidationResult`**:
- Reads the JSON file
- Runs both validation layers
- Returns `ValidationResult` with valid flag and error list

**`validate_all_artifacts(dir, expected) -> dict[str, ValidationResult]`**:
- Validates multiple artifacts at once

### 6.8 `observability.py` — Logging & Cost Tracking

**Class: `RunLogger`**:
- Creates log directory and opens a JSONL file (`logs/run-{run_id}.jsonl`)
- `log_event(event_type, data)`: Appends timestamped JSON line
- Event types: `run_start`, `run_complete`, `agent_invoke`, `agent_result`, `phase_start`, `phase_complete`, `validation_result`, `budget_warning`
- Tracks cumulative cost and emits `budget_warning` when approaching limit

### 6.9 Hook Scripts

**`validate-task-completion.sh`**:
- Triggered by `TaskCompleted` hook in `.claude/settings.json`
- Runs `python -m orchestrator.validation` on the expected output artifacts
- Exits non-zero (blocking) if validation fails

**`log-tool-use.sh`**:
- Triggered by `PostToolUse` hook (async, non-blocking)
- Appends tool invocation details to the run log

**`block-writes.sh`**:
- Triggered by `PreToolUse` hook matching `Write|Edit` tools
- Checks if the calling agent is QA; if so, exits non-zero to block the write

### 6.10 `.claude/settings.json`

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "command": ".claude/hooks/log-tool-use.sh",
        "async": true
      }
    ],
    "TaskCompleted": [
      {
        "command": ".claude/hooks/validate-task-completion.sh"
      }
    ]
  }
}
```

### 6.11 Skill: `/run-sdlc`

`.claude/skills/run-sdlc/SKILL.md` — provides a Claude Code slash command that:
1. Prompts user for a feature request if not provided
2. Invokes `orchestrate` CLI
3. Streams progress to the terminal
4. Summarizes results when complete

### 6.12 `config/default.yaml`

```yaml
workspace_dir: workspace
max_review_cycles: 3
max_budget_usd: 50.0

phases:
  pm:
    agent: pm
    parallel: false
    max_retries: 2
    timeout_minutes: 15
  architect:
    agent: architect
    parallel: false
    max_retries: 2
    timeout_minutes: 20
  engineer:
    agent: engineer
    parallel: true
    max_retries: 2
    timeout_minutes: 30
  qa:
    agent: qa
    parallel: false
    max_retries: 1
    timeout_minutes: 15
  reviewer:
    agent: reviewer
    parallel: false
    max_retries: 1
    timeout_minutes: 15

agents:
  pm:
    name: Product Manager
    model: sonnet
    max_turns: 30
    escalation_model: null
    input_artifacts: []
    output_artifacts: [prd]
  architect:
    name: System Architect
    model: sonnet
    max_turns: 40
    escalation_model: null
    input_artifacts: [prd]
    output_artifacts: [architecture, tasks]
  engineer:
    name: Engineer
    model: sonnet
    max_turns: 80
    escalation_model: sonnet
    input_artifacts: [prd, architecture, tasks]
    output_artifacts: []
  qa:
    name: QA Engineer
    model: sonnet
    max_turns: 40
    escalation_model: sonnet
    input_artifacts: [prd, tasks]
    output_artifacts: [qa_report]
  reviewer:
    name: Code Reviewer
    model: sonnet
    max_turns: 30
    escalation_model: null
    input_artifacts: [prd, architecture, tasks, qa_report]
    output_artifacts: [review]
```

---

## 7. Agent Prompt Specifications

### 7.1 PM Agent (`.claude/agents/pm.md`)

**Model**: sonnet | **Max turns**: 30 | **Access**: read-only

**System prompt instructs**:
- Analyze the feature request thoroughly
- Research the existing codebase (Read, Grep, Glob)
- Research external context if needed (WebSearch, WebFetch)
- Produce a structured PRD as JSON conforming to the PRD schema
- Be specific and actionable — vague requirements cause downstream failures
- Consider edge cases, error states, and non-functional requirements
- Do NOT modify any code files

### 7.2 Architect Agent (`.claude/agents/architect.md`)

**Model**: sonnet | **Max turns**: 40 | **Access**: read-only

**Inputs**: `artifacts/prd.json`

**System prompt instructs**:
- Read and understand the PRD
- Analyze existing codebase structure
- Design system architecture (components, data flow, tech decisions)
- Break work into implementable tasks with IDs, dependencies, acceptance criteria
- Order tasks so dependencies come before dependents
- Keep tasks small enough for a single engineer
- Identify which files each task will modify (for conflict detection)
- Produce `architecture.json` and `tasks.json`

### 7.3 Engineer Agent (`.claude/agents/engineer.md`)

**Model**: sonnet (escalate to sonnet) | **Max turns**: 80 | **Access**: read-write

**Inputs**: Specific task data + `prd.json`, `architecture.json`, `tasks.json`

**System prompt instructs**:
- Read assigned task details and requirements
- Check codebase for existing patterns and conventions
- Implement the task following the architecture
- Write or update tests
- Keep changes focused on the assigned task
- Document blockers in `artifacts/blocker-{task_id}.md`
- Do not modify files outside the task's `files_to_modify` unless necessary

**Isolation**: Runs with `isolation: worktree` when executing in parallel

### 7.4 QA Agent (`.claude/agents/qa.md`)

**Model**: sonnet (escalate to sonnet) | **Max turns**: 40 | **Access**: read-only (enforced by hook)

**Inputs**: `prd.json`, `tasks.json`, implemented code

**System prompt instructs**:
- Run the test suite
- Run linters if configured
- Run type checkers if configured
- Review code for bugs, security issues, missing edge cases
- Check every acceptance criterion from the PRD
- Produce `qa_report.json` with verdict `pass` or `fail`
- MUST NOT modify any code files

### 7.5 Reviewer Agent (`.claude/agents/reviewer.md`)

**Model**: sonnet | **Max turns**: 30 | **Access**: read-only

**Inputs**: All artifacts + implemented code

**System prompt instructs**:
- Review for correctness, architecture adherence, code quality, security, performance, test coverage
- Produce structured verdict: `approve`, `reject`, or `request_changes`
- Be specific — reference exact files and lines
- Provide actionable suggestions
- `reject` only for critical/blocking issues
- `approve` is OK with minor/nit issues noted

---

## 8. Build Sequence

Implementation should follow this order (dependencies flow downward):

| Step | What to Build | Key Files | Dependencies |
|---|---|---|---|
| 1 | Project scaffold | `pyproject.toml`, `CLAUDE.md`, directory structure | — |
| 2 | Artifact JSON schemas | `src/schemas/*.schema.json` (5 files) | — |
| 3 | Pydantic models | `src/orchestrator/models.py` | Step 2 (schema alignment) |
| 4 | Agent definitions | `.claude/agents/*.md` (5 files) | Step 2 (schema references) |
| 5 | Configuration | `config/default.yaml`, `src/orchestrator/config.py` | Step 3 |
| 6 | SDK wrapper | `src/orchestrator/agents.py` | Step 3 |
| 7 | Schema validation | `src/orchestrator/validation.py` | Steps 2, 3 |
| 8 | Phase definitions | `src/orchestrator/phases.py` | Steps 3, 5 |
| 9 | Orchestration engine | `src/orchestrator/engine.py` | Steps 6, 7, 8 |
| 10 | Quality gate hooks | `.claude/hooks/*.sh`, `.claude/settings.json` | Step 7 |
| 11 | Observability | `src/orchestrator/observability.py` | Step 3 |
| 12 | CLI entry point | `src/orchestrator/main.py` | Steps 5, 9, 11 |
| 13 | Tests | `tests/test_*.py` | Steps 3, 5, 7, 8 |
| 14 | Skill | `.claude/skills/run-sdlc/SKILL.md` | Step 12 |

Steps 1-4 can be parallelized. Steps 5-8 can be partially parallelized. Steps 9-14 are mostly sequential.

---

## 9. Testing & Verification

### 9.1 Unit Tests

| Test File | What It Tests |
|---|---|
| `test_models.py` | Pydantic model validation — valid/invalid artifact data, enum handling, serialization |
| `test_validation.py` | JSON schema + Pydantic two-layer validation, missing files, invalid JSON, schema mismatches |
| `test_phases.py` | Prompt builder functions produce correct prompts with artifact paths, task data injection |
| `test_engine.py` | Engine orchestration flow, phase ordering, review cycles, file conflict detection, budget checks |

### 9.2 Integration Verification

1. **Dry run**: `orchestrate --dry-run "Build a todo app"` — walks all phases printing prompts without invoking SDK
2. **Single-phase test**: `orchestrate --phase pm "Build a todo app"` — runs PM phase only, validates artifact output
3. **Full run**: `orchestrate "Build a todo app"` — end-to-end SDLC execution
4. **Cost verification**: Check `workspace/logs/run-*.jsonl` for per-phase and total cost
5. **Hook verification**: Trigger QA agent attempting a file write — should be blocked by PreToolUse hook
6. **Review cycle test**: Force a reviewer rejection and verify the engineer→QA→reviewer loop works correctly

---

## 10. Concurrency & Safety

- **Parallel engineers**: Use `isolation: worktree` — each gets an isolated git worktree copy. Merge results after all complete.
- **File conflict detection**: Before running parallel, check `files_to_modify` across tasks. If any overlap, fall back to sequential execution.
- **QA write protection**: `PreToolUse` hook blocks `Write|Edit` tools for the QA agent — enforces read-only access.
- **Budget ceiling**: Engine tracks cumulative `cost_usd` across all agent invocations. Emits `budget_warning` at 80% and stops at 100% of `max_budget_usd`.
- **Retry bounds**: Each phase has `max_retries` (default 2). Review loop has `max_review_cycles` (default 3). Both prevent infinite loops.

---

## 11. Cost Model

Estimated per-run costs (rough, varies by feature complexity):

| Phase | Model | Est. Tokens | Est. Cost |
|---|---|---|---|
| PM | sonnet | ~10K | $1-3 |
| Architect | sonnet | ~15K | $2-5 |
| Engineer (per task) | Sonnet | ~20K | $1-3 |
| QA | Sonnet | ~10K | $0.50-2 |
| Reviewer | sonnet | ~10K | $1-3 |
| **Total (3 tasks, no review cycles)** | | | **$7-20** |
| **Total (3 tasks, 2 review cycles)** | | | **$15-40** |

Default budget ceiling: $50 per run. Configurable in `default.yaml`.
