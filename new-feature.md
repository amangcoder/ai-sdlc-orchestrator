# QA-Browser Phase: Headless Browser Testing for Generated Apps

## Context

The orchestrator can generate full-stack apps via its PM -> Architect -> Engineer pipeline, but the QA phase only does **static analysis** (runs tests, lints, type checks, traces code logic). It cannot start the generated app in a real browser and verify that user flows actually work. This means bugs visible only at runtime (broken routes, missing UI elements, JS errors, form submission failures) go undetected until a human manually tests.

This plan adds a **QA-Browser phase** that starts the generated app's dev server, launches a headless Chromium browser via Playwright, and verifies acceptance criteria from the PRD through automated browser interactions.

---

## Architecture

**Hybrid approach**: Deterministic Python harness for stack detection + server lifecycle + test execution. Claude sub-agent for generating **TypeScript Playwright tests** (`.spec.ts`) from acceptance criteria. TypeScript chosen for natural fit with JS/TS frontend apps and richer Playwright ecosystem.

```
Engineer writes code
       |
  QA (static)        ← existing: tests, lint, type check
       |
  Env Setup          ← NEW: writes docker-compose.yml + seed data
       |
  QA Browser         ← NEW: starts app via compose, seeds DB, runs browser tests
       |
    Reviewer
```

**Pipeline position**: Two new steps inserted between QA and Reviewer in `FEATURE_DEVELOPMENT`:
1. **"Env Setup"** — an agent that writes `docker-compose.yml` and seed data scripts
2. **"QA Browser"** — starts the environment, runs browser tests against acceptance criteria

**Speed mode gating**: Only runs in `thorough` and `paranoid` modes. Skipped in `turbo`/`standard` (produces a stub report with `server_status: "skipped"`, `verdict: "pass"`).

---

## Fixer: Optional Failure-Recovery Agent

The **Fixer** is not a workflow step — it is an **inline recovery agent** invoked automatically when any pipeline step fails, before a dumb retry or route-back occurs. It is optional and can be toggled via config.

### What the Fixer Does

When a step fails (error, assertion failure, unexpected behavior), instead of blindly retrying:

1. **Traces the causal chain** — reads all artifacts produced so far (`prd.json`, `architecture.json`, `tasks.json`, `qa_report.json`, `env_setup_report.json`, etc.) plus the raw error output from the failed step
2. **Reconstructs the decision path** — understands what the PM required → what the Architect designed → what the Engineer implemented → where it broke
3. **Identifies root cause** — categorizes: logic bug, missing dependency, wrong env config, brittle test selector, compose port conflict, missing seed data, schema mismatch, etc.
4. **Applies a targeted fix** — edits only the specific files responsible for the failure (not a full reimplementation)
5. **Reports what it changed** — writes `artifacts/fixer_report.json`
6. **Signals the engine to retry the failed step** — the engine re-runs the same step with the fix in place

If the fix fails to resolve the error after `fixer.max_attempts`, the Fixer escalates to the Reviewer with a structured diagnosis.

### What makes it "Optional"

- **Config flag** `fixer.enabled: true/false` — if false, pipeline falls back to existing `on_fail` routing
- **Speed mode gated** — active in `standard`, `thorough`, `paranoid`; disabled in `turbo` (speed matters more than recovery)
- **Max attempts** `fixer.max_attempts: 2` — prevents infinite fix loops; after limit, escalates
- **Step-level opt-out** — individual workflow steps can set `fixer: false` to skip Fixer for that step

### Integration Point

In `workflow_engine._execute_step()`, when a step fails and Fixer is enabled:

```python
if self.config.fixer.enabled and phase_state.retry_count < self.config.fixer.max_attempts:
    fixer_ok = await self._invoke_fixer(step, failure_context)
    if fixer_ok:
        return await self._execute_step(step)  # retry with fix applied
# else: fall through to existing on_fail routing
```

This keeps Fixer **invisible to the workflow DAG** — steps don't need to know about it.

---

## Files to Create

### 1. `src/orchestrator/stack_detector.py` — Stack Detection Utility

Detects the tech stack of the generated project by examining project files. Returns a `StackInfo` dataclass:

```python
@dataclass(frozen=True)
class StackInfo:
    framework: str            # "nextjs", "react-vite", "flask", "django", "fastapi", etc.
    language: str             # "javascript", "typescript", "python"
    package_manager: str      # "npm", "yarn", "pnpm", "pip", "poetry"
    install_command: str      # "npm install", "pip install -r requirements.txt"
    dev_server_command: str   # "npm run dev", "uvicorn main:app --reload"
    dev_server_port: int      # 3000, 5173, 8000
    base_url: str             # "http://localhost:3000"
    health_check_path: str    # "/"
    has_frontend: bool        # False for pure API/CLI projects
```

**Detection chain** (priority order):
1. `package.json` → parse `dependencies` + `scripts.dev`/`scripts.start` → detect Next.js, Vite, CRA, Express, etc.
2. `pyproject.toml` / `requirements.txt` → detect Django, Flask, FastAPI, Streamlit
3. `Gemfile` → Rails
4. `go.mod` → Go
5. `architecture.json` artifact (if available) → explicit tech stack from Architect
6. Fallback: scan for common entry files (`app.py`, `server.js`, `main.py`)

If no startable app detected → `has_frontend = False`, phase skips browser tests gracefully.

### 2. `src/orchestrator/app_server.py` — Dev Server Lifecycle Manager

Generalized version of the existing `tests/e2e/utils/server.py` (`DashboardTestServer`). Reuses the `find_free_port()` pattern and health-check polling.

```python
class AppTestServer:
    def __init__(self, project_root: Path, stack: StackInfo, timeout: float = 60.0): ...
    async def start_dependencies(self) -> tuple[bool, str]:     # docker compose up -d
    async def install_packages(self) -> tuple[bool, str]:        # npm install / pip install
    async def start(self) -> tuple[str, int]:                    # (host, port)
    async def wait_healthy(self, timeout: float) -> bool:
    async def stop(self) -> None:                                # stop server + docker compose down
```

Key behaviors:
- **Docker Compose**: If `docker-compose.yml` / `compose.yml` exists, runs `docker compose up -d` to start dependent services (DB, Redis, etc.) before the app, and `docker compose down` on teardown
- Runs `stack.install_command` via `asyncio.create_subprocess_exec` with 120s timeout
- Starts `stack.dev_server_command` as a background subprocess, overriding port to a free port
- Polls `stack.health_check_path` until HTTP 200 or timeout
- Captures stderr for diagnostics on failure
- `atexit` cleanup safety net (same pattern as `DashboardTestServer`)
- On stop: gracefully shuts down dev server, then runs `docker compose down` if compose was used

### 3. `.claude/agents/env_setup.md` — Env Setup Agent Definition

System prompt for the Environment Setup agent. This agent runs **after static QA, before QA Browser**. It reads the architecture, PRD, and generated code to produce two outputs:

**`docker-compose.yml`** (written to project root):
- Services: the app itself + all dependencies (PostgreSQL, MySQL, Redis, etc.) inferred from architecture.json and code
- Health checks on each service
- Correct port mappings, env vars, volume mounts
- Named network so services resolve by hostname

**`scripts/seed.sql` / `scripts/seed.py` / `scripts/seed.ts`** (language matches stack):
- Realistic seed data matching the PRD's domain (e.g., for a todo app: 5–10 sample todos, a test user account)
- Seeds enough data for browser tests to find real content (list pages, detail pages, search results)
- Idempotent: can be re-run safely (uses INSERT OR IGNORE / upserts)

The agent is instructed to:
1. Read `artifacts/architecture.json` for declared services/dependencies
2. Read `artifacts/prd.json` for domain entities to seed
3. Explore code to confirm DB models, ORM, connection strings
4. Write `docker-compose.yml` at project root
5. Write `scripts/seed.*` appropriate to the stack

### 4. `.claude/agents/qa_browser.md` — QA Browser Agent Definition

System prompt for the QA Browser agent. Instructs the agent to:
1. Read PRD acceptance criteria
2. Explore the generated project briefly to understand routes/pages/components
3. Write Playwright test files (one per AC group) to `tests/e2e/generated/`
4. Use accessible selectors (`getByRole`, `getByText`, `getByLabel`)
5. NOT attempt to install or run Playwright itself — the harness does that
6. Write `qa_browser_report.json` artifact

### 5. `src/schemas/env_setup_report.schema.json` — Env Setup Artifact Schema

```json
{
  "docker_compose_written": true,
  "compose_services": ["app", "postgres", "redis"],
  "seed_script_written": true,
  "seed_script_path": "scripts/seed.sql",
  "issues": [],
  "verdict": "pass|fail"
}
```

### 6. `src/schemas/qa_browser_report.schema.json` — QA Browser Artifact Schema

Generated from the Pydantic model. Structure:

```json
{
  "server_status": "running|failed|skipped",
  "server_error": "string|null",
  "stack_detected": "nextjs",
  "base_url": "http://localhost:3000",
  "test_results": [
    {
      "ac_id": "AC-001",
      "criteria": "GIVEN ... WHEN ... THEN ...",
      "status": "pass|fail|skip|error",
      "test_file": "tests/e2e/generated/ac_001.spec.ts",
      "duration_ms": 2340,
      "error_message": "string|null",
      "screenshot_path": "string|null"
    }
  ],
  "tests_passed": 3,
  "tests_failed": 1,
  "tests_skipped": 0,
  "console_errors": ["TypeError: ..."],
  "issues": [{ "severity": "major", "file": "...", "description": "...", "suggestion": "..." }],
  "verdict": "pass|fail"
}
```

### 7. `.claude/agents/fixer.md` — Fixer Agent Definition

System prompt for the Fixer agent. Instructs the agent to:
1. **Not write new features** — only diagnose and fix the specific failure
2. Read all artifacts in order to reconstruct the decision chain
3. Identify the single most likely root cause (not a list of possibilities)
4. Apply a minimal targeted fix — fewest files changed, no refactoring
5. Write `artifacts/fixer_report.json` explaining what failed, why, and what was changed
6. Stop after the fix — the harness retries the failed step

The agent is given the failed step name, its error output (stderr/assertion message/schema validation error), and the full artifact context.

### 8. `src/schemas/fixer_report.schema.json` — Fixer Artifact Schema

```json
{
  "failed_step": "QA Browser",
  "error_summary": "Server failed to start: port 3000 already in use",
  "root_cause_category": "env_config|logic_bug|missing_dep|brittle_test|schema_mismatch|other",
  "root_cause_description": "docker-compose.yml maps port 3000 but Next.js hardcodes 3000 without respecting PORT env var",
  "files_changed": ["docker-compose.yml", "package.json"],
  "fix_description": "Updated compose to expose port 3001, set PORT=3001 in environment, updated scripts.dev to use $PORT",
  "confidence": "high|medium|low",
  "verdict": "fixed|escalate"
}
```

`verdict: "escalate"` means the Fixer could not confidently fix the issue and the Reviewer should see it.

### 9. `tests/unit/test_stack_detector.py` — Unit Tests

Parametrized tests using `tmp_path` fixtures with synthetic project structures (package.json with next/vite/react-scripts, pyproject.toml with django/flask/fastapi, etc.)

### 10. `tests/unit/test_app_server.py` — Unit Tests

Mock subprocess tests for docker compose up/down, install, start, health-check, seed, and stop lifecycle.

---

## Files to Modify

### 11. `src/orchestrator/models.py`

- **Add** `ENV_SETUP_ENGINEER = "env_setup_engineer"` to `AgentRole` enum
- **Add** `QA_BROWSER_ENGINEER = "qa_browser_engineer"` to `AgentRole` enum
- **Add** `FIXER = "fixer"` to `AgentRole` enum (~line 153)
- **Add** `EnvSetupReport` Pydantic model (compose_written, services, seed_written, seed_path, verdict)
- **Add** `BrowserTestResult` and `QABrowserReport` Pydantic models
- **Add** `FixerReport` Pydantic model (failed_step, root_cause_category, root_cause_description, files_changed, fix_description, confidence, verdict)
- **Add** `FixerConfig` to `OrchestratorConfig`:
  ```python
  class FixerConfig(BaseModel):
      enabled: bool = True
      max_attempts: int = 2
      speed_modes: list[SpeedMode] = [SpeedMode.STANDARD, SpeedMode.THOROUGH, SpeedMode.PARANOID]
      model: str = "sonnet"
      escalation_model: str = "opus"
  ```
- **Add** all three to `ARTIFACT_MODELS`: `"env_setup_report"`, `"qa_browser_report"`, `"fixer_report"`

### 12. `src/orchestrator/phases.py`

- **Add** `build_env_setup_prompt()` — injects architecture.json services, PRD domain entities, stack info
- **Add** `build_qa_browser_prompt()` — injects stack info, base URL, seed confirmation, AC list, Playwright template
- **Add** `build_fixer_prompt()` — injects: failed step name, raw error output, all available artifacts in chronological order, instruction to trace the decision chain and apply a minimal targeted fix
- **Add** all three to `PROMPT_BUILDERS` (~line 3924)
- **Add** `"env_setup"`, `"qa_browser"`, and `"fixer"` entries to `PHASE_DEFINITIONS` (~line 3965)

### 13. `src/orchestrator/engine.py`

- **Add** `"env_setup"` and `"qa_browser"` to `PHASE_ORDER` between `"qa"` and `"reviewer"` (line 77):
  ```python
  PHASE_ORDER = ["pm", "architect", "engineer", "qa", "env_setup", "qa_browser", "reviewer"]
  ```
  Note: `"fixer"` is **not** in `PHASE_ORDER` — it is invoked inline by the engine on failure, not as a sequential phase.

### 14. `src/orchestrator/workflows.py`

- **Add** two new steps to `FEATURE_DEVELOPMENT` between QA and Release:
  ```python
  WorkflowStepDefinition(
      name="Env Setup",
      agent_role=AgentRole.ENV_SETUP_ENGINEER,
      inputs=["prd", "architecture", "qa_report"],
      outputs=["env_setup_report"],
      next="QA Browser",
      on_fail="Implementation",   # missing compose/seed is a code problem
  ),
  WorkflowStepDefinition(
      name="QA Browser",
      agent_role=AgentRole.QA_BROWSER_ENGINEER,
      inputs=["prd", "qa_report", "env_setup_report"],
      outputs=["qa_browser_report"],
      next="Release",
      on_fail="Implementation",
  ),
  ```
- **Update** existing QA step's `next` from `"Release"` → `"Env Setup"`
- **Add** same two steps to `BUGFIX` workflow (between QA and end)

### 15. `src/orchestrator/roles.py`

- **Add** `ENV_SETUP_ENGINEER` to `ROLE_REGISTRY`:
  ```python
  AgentRole.ENV_SETUP_ENGINEER: RoleDefinition(
      role=AgentRole.ENV_SETUP_ENGINEER,
      title="Environment Setup Engineer",
      responsibility="Writes docker-compose.yml and seed data scripts so the app can run in an isolated test environment",
      access=RoleAccess.READ_WRITE,
      agent_file="env_setup.md",
  )
  ```
- **Add** `QA_BROWSER_ENGINEER` to `ROLE_REGISTRY`:
  ```python
  AgentRole.QA_BROWSER_ENGINEER: RoleDefinition(
      role=AgentRole.QA_BROWSER_ENGINEER,
      title="QA Browser Engineer",
      responsibility="Validates application behavior via headless browser tests against acceptance criteria",
      access=RoleAccess.READ_WRITE,
      agent_file="qa_browser.md",
  )
  ```
- **Add** `FIXER` to `ROLE_REGISTRY`:
  ```python
  AgentRole.FIXER: RoleDefinition(
      role=AgentRole.FIXER,
      title="Fixer",
      responsibility="Diagnoses root cause of pipeline failures, traces the decision chain from artifacts, and applies targeted fixes",
      access=RoleAccess.READ_WRITE,
      agent_file="fixer.md",
  )
  ```

### 16. `src/orchestrator/model_routing.py`

- **Add** `AgentRole.ENV_SETUP_ENGINEER: AgentCategory.CODING` to `ROLE_CATEGORY`
- **Add** `AgentRole.QA_BROWSER_ENGINEER: AgentCategory.VERIFICATION` to `ROLE_CATEGORY`
- **Add** `AgentRole.FIXER: AgentCategory.VERIFICATION` to `ROLE_CATEGORY` — uses Sonnet by default, escalates to Opus for complex root causes

### 17. `src/orchestrator/workflow_engine.py`

- **Add** pre/post hooks in `_execute_step()` (~line 872) for both new steps:

**For "Env Setup" step** (no special pre-hook needed — agent writes files directly):
- Post-hook: validate that `docker-compose.yml` was actually written; if not, fail the step

**For "QA Browser" step** (deterministic harness wraps the agent):
1. Run `StackDetector.detect()`
2. If `stack.has_frontend` is False → skip with stub report
3. If speed mode is `turbo`/`standard` → skip with stub report
4. Read `env_setup_report.json` to confirm compose + seed files exist
5. Run `docker compose up -d` (using written `docker-compose.yml`)
6. Run `AppTestServer.install_packages()`
7. Run seed script (`scripts/seed.*`) via appropriate runtime
8. Run `AppTestServer.start()` → get `base_url`
9. Inject `base_url` + `stack` info into agent prompt context
10. After agent completes → run generated Playwright tests via `npx playwright test`
11. Parse JSON results → write `qa_browser_report.json`
12. `AppTestServer.stop()`
13. `docker compose down`

**Add `_invoke_fixer()` method** called from within `_execute_step()` on any step failure:

```python
async def _invoke_fixer(
    self,
    failed_step: WorkflowStepDefinition,
    error_output: str,
    attempt: int,
) -> bool:
    """Invoke the Fixer agent to diagnose and fix a step failure.
    Returns True if a fix was applied (step should be retried), False to escalate."""
    if not self.config.fixer.enabled:
        return False
    if self.config.speed_mode not in self.config.fixer.speed_modes:
        return False

    # Collect all artifacts produced so far as context
    artifact_context = self._collect_all_artifacts()

    prompt = build_fixer_prompt(
        failed_step=failed_step.name,
        error_output=error_output,
        artifact_context=artifact_context,
        workspace=Path(self.state.workspace_dir),
    )
    result = await invoke_agent(AgentInvocation(
        agent_name="fixer",
        prompt=prompt,
        model=ModelTier.SONNET,
        max_turns=25,
        workspace_dir=self.state.workspace_dir,
        project_root=self.config.project_root,
    ))
    # Read fixer_report.json to determine outcome
    fixer_report = self._load_artifact("fixer_report")
    return fixer_report and fixer_report.get("verdict") == "fixed"
```

The `_execute_step()` failure path becomes:
```python
# On step failure:
if fixer_enabled and phase_state.retry_count < config.fixer.max_attempts:
    fixed = await self._invoke_fixer(step, error_output, attempt=phase_state.retry_count)
    if fixed:
        phase_state.retry_count += 1
        return await self._execute_step(step)  # retry with fix in place
# Fall through to on_fail routing
```

### 18. `config/default.yaml`

- **Add** phase configs:
  ```yaml
  env_setup:
    agent: env_setup
    parallel: false
    max_retries: 2
    timeout_minutes: 15
  qa_browser:
    agent: qa_browser
    parallel: false
    max_retries: 1
    timeout_minutes: 25    # includes docker compose up + seed + browser run
  ```
- **Add** agent configs:
  ```yaml
  env_setup:
    name: Environment Setup Engineer
    model: sonnet
    max_turns: 30
    input_artifacts: [prd, architecture, qa_report]
    output_artifacts: [env_setup_report]
  qa_browser:
    name: QA Browser Engineer
    model: sonnet
    max_turns: 40
    escalation_model: opus
    input_artifacts: [prd, qa_report, env_setup_report]
    output_artifacts: [qa_browser_report]
  fixer:
    name: Fixer
    model: sonnet
    max_turns: 25
    escalation_model: opus
    output_artifacts: [fixer_report]
  ```
- **Add** top-level Fixer config block:
  ```yaml
  fixer:
    enabled: true
    max_attempts: 2
    speed_modes: [standard, thorough, paranoid]  # disabled in turbo
  ```

---

## Agent Workflow (Step by Step)

### Phase A: Env Setup Agent

**Agent execution (Claude sub-agent — `env_setup` role, Sonnet):**
1. Reads `artifacts/architecture.json` for declared services (DB type, cache, message queue, etc.)
2. Reads `artifacts/prd.json` for domain entities and sample data to seed
3. Explores generated code for DB models, ORM configuration, connection strings
4. Writes `docker-compose.yml` at project root (app service + all dependencies, health checks, named network)
5. Writes `scripts/seed.*` (language matches stack — SQL for raw DB, Python/TS for ORM-based seeding)
6. Writes `artifacts/env_setup_report.json`

**Post-agent (harness):** validates compose file was written; fails step if missing.

---

### Phase B: QA Browser

**Pre-agent (deterministic harness):**
1. **Stack detection** → `StackInfo`
2. **Speed mode check** → skip if turbo/standard
3. **Frontend check** → skip if `has_frontend=False`
4. **Docker Compose up** → `docker compose up -d` using the written compose file (wait for health checks)
5. **Install packages** → `npm install` / `pip install` (120s timeout)
6. **Seed database** → run `scripts/seed.*` via appropriate runtime (psql, python, node)
7. **Start dev server** → subprocess on free port, poll health endpoint (60s timeout)
8. **Inject context** → base_url, stack info, AC list into agent prompt

**Agent execution (Claude sub-agent — `qa_browser` role, Sonnet):**
9. Reads PRD acceptance criteria
10. Explores generated project (routes, pages, components)
11. Writes TypeScript Playwright `.spec.ts` files to `tests/e2e/generated/`
12. Each test maps to one or more ACs (test title convention: `AC-001: ...`)
13. Scaffolds a minimal `playwright.config.ts` if one doesn't exist

**Post-agent (deterministic harness):**
14. **Run Playwright** → `npx playwright test tests/e2e/generated/ --reporter=json` (120s timeout)
15. **Parse JSON results** → map each test to its AC ID
16. **Construct report** → `qa_browser_report.json` with per-AC verdicts, screenshots, console errors
17. **Stop dev server** → graceful shutdown
18. **Docker Compose down** → stop all services
19. **Write artifact** → validated against `qa_browser_report.schema.json`

---

### Phase C: Fixer (on failure of any step)

**Triggered**: Automatically when any step fails and `fixer.enabled = true` and speed mode is not `turbo`.

**Agent execution (Claude sub-agent — `fixer` role, Sonnet → Opus on escalation):**
1. Receives: failed step name + full raw error output (stderr, assertion message, schema validation error)
2. Reads all available artifacts in pipeline order: prd → architecture → tasks → qa_report → env_setup_report → qa_browser_report (whatever exists)
3. Reconstructs the decision chain: "PM required X → Architect designed Y → Engineer implemented Z → failure occurred because..."
4. Identifies the single most likely root cause and categorizes it
5. Applies a **minimal targeted fix** — edits only the specific file(s) responsible
6. Does NOT add new features, refactor, or change unrelated code
7. Writes `artifacts/fixer_report.json` (see schema above)
8. Signals `verdict: "fixed"` (retry) or `verdict: "escalate"` (Reviewer must see this)

**After Fixer returns:**
- If `verdict: "fixed"` → engine retries the failed step
- If `verdict: "escalate"` or `max_attempts` reached → engine follows original `on_fail` routing (routes to Reviewer or back to Implementation)

---

## Error Handling

### Env Setup phase
| Scenario | Behavior |
|----------|----------|
| No `docker-compose.yml` written | Fail step, route back to Implementation |
| No seed script written | Warn in report but don't fail (some apps don't need seed data) |
| Compose file is invalid YAML | Fail step with parse error |

### QA Browser phase
| Scenario | Behavior |
|----------|----------|
| No recognizable stack | `server_status: "skipped"`, `verdict: "fail"`, critical issue |
| Docker Compose fails to start | `server_status: "failed"`, `verdict: "fail"`, compose stderr captured |
| Docker not available on host | Skip compose, attempt server start anyway; report warning |
| Install fails | `server_status: "failed"`, `verdict: "fail"`, stderr captured |
| Seed script fails | Report as major issue but continue — tests may find missing data |
| Server won't start | `server_status: "failed"`, `verdict: "fail"`, stderr captured |
| Agent generates no tests | `tests_skipped = len(ACs)`, `verdict: "fail"` |
| Playwright not installed | Skip phase, stub report, warning issue |
| Tests time out | Kill after 120s, report partial results |
| Backend-only project | `server_status: "skipped"`, `verdict: "pass"`, info note |

All failures produce a valid `qa_browser_report.json` — the phase never crashes the pipeline.

### Fixer error handling
| Scenario | Behavior |
|----------|----------|
| Fixer disabled in config | Skip Fixer, use existing `on_fail` routing |
| Speed mode is turbo | Skip Fixer, use existing `on_fail` routing |
| Fixer max_attempts exceeded | Stop trying, route to `on_fail` (Reviewer or Implementation) |
| Fixer writes `verdict: "escalate"` | Route to Reviewer immediately with fixer_report attached |
| Fixer itself crashes | Swallow the error, fall through to `on_fail` — never block the pipeline |
| Fixer applies a fix but retry still fails | Fixer invoked again (up to max_attempts), then escalates |

---

## Implementation Order

1. **Models** — Add `ENV_SETUP_ENGINEER`, `QA_BROWSER_ENGINEER`, `FIXER` roles; `EnvSetupReport`, `QABrowserReport`, `FixerReport`, `FixerConfig` models; `ARTIFACT_MODELS` entries
2. **Stack detector** — `stack_detector.py` + unit tests
3. **App server** — `app_server.py` + unit tests (adapts `DashboardTestServer`; adds docker compose + seed steps)
4. **Schemas** — `env_setup_report.schema.json` + `qa_browser_report.schema.json` + `fixer_report.schema.json`
5. **Agent definitions** — `.claude/agents/env_setup.md` + `.claude/agents/qa_browser.md` + `.claude/agents/fixer.md`
6. **Prompt builders** — `build_env_setup_prompt()` + `build_qa_browser_prompt()` + `build_fixer_prompt()` in `phases.py`
7. **Pipeline wiring** — `engine.py` PHASE_ORDER, `workflows.py` steps, `roles.py`, `model_routing.py`
8. **Workflow engine hooks** — Env Setup post-hook + QA Browser lifecycle + `_invoke_fixer()` in `workflow_engine.py`
9. **Config** — `default.yaml` phase + agent + fixer config block

---

## Verification

1. **Unit tests**: `pytest tests/unit/test_stack_detector.py tests/unit/test_app_server.py -v`
2. **Dry run**: `orchestrate --dry-run --speed thorough "Build a todo app"` — verify "Env Setup" and "QA Browser" steps appear; Fixer should not appear (it's not a DAG step)
3. **Env Setup smoke test**: Point env_setup agent at a Django+Postgres project, verify `docker-compose.yml` + `scripts/seed.sql` written
4. **Compose + seed**: Run `docker compose up -d` + seed, verify services start and data is present
5. **Full run**: `orchestrate --speed thorough "Build a simple todo list"` — verify `env_setup_report.json` + `qa_browser_report.json` produced with per-AC verdicts
6. **Fixer triggered**: Introduce a deliberate bug (wrong port in compose), run pipeline — verify Fixer is invoked, `fixer_report.json` written, bug fixed, and step retried successfully
7. **Fixer disabled**: Set `fixer.enabled: false`, re-run — verify Fixer is skipped and `on_fail` routing applies directly
8. **Skip behavior**: `orchestrate --speed standard "Fix typo"` — verify Env Setup + QA Browser skip (stub reports), Fixer not invoked
9. **Backend-only**: `orchestrate --speed thorough "Add a REST API endpoint"` — verify graceful skip for no-frontend projects
