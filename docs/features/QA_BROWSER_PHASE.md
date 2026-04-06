# QA-Browser Phase: Runtime Validation of Generated Applications

## Overview

The **QA-Browser Phase** extends the orchestrator pipeline to validate generated applications through automated browser-based testing. While the static QA phase (tests, linting, type-checking) catches code-level defects, the QA-Browser phase detects runtime issues that are only visible when the application is actually running:

- **Broken routes** — navigation that returns 404
- **Missing UI elements** — components that don't render
- **JavaScript errors** — runtime exceptions, console errors
- **Form failures** — submission errors, validation issues
- **API integration issues** — backend endpoints unreachable or returning wrong data

### What Problem Does It Solve?

Generated applications pass static QA but fail when actually used because:
- A route is declared but returns 404 (missing controller/handler)
- A UI component exists but doesn't render (missing props, data)
- CSS is broken or missing (responsive design fails on mobile)
- JavaScript event handlers don't work (event binding issues)
- The API contract between frontend and backend is mismatched

**Result:** Bugs visible only at runtime go undetected until manual testing, adding time and cost to the release cycle.

## Architecture

The QA-Browser phase consists of **two stages**:

### Stage 1: Environment Setup (`env_setup_engineer` agent)

**What it does:**
1. Reads `architecture.json` to identify all declared dependencies (database, cache, message queue, etc.)
2. Reads `prd.json` to understand domain entities and sample data needed
3. Writes `docker-compose.yml` with:
   - Service definitions for app + all dependencies
   - Health checks for each service
   - Correct port mappings and environment variables
   - A named network so services resolve by hostname
4. Writes seed data scripts (SQL, Python, or TypeScript) with realistic sample data matching the PRD's domain
5. Produces `env_setup_report.json` artifact

**Output artifacts:**
- `docker-compose.yml` (written to project root)
- `scripts/seed.*` (language-specific seed script)
- `artifacts/env_setup_report.json` (metadata and validation)

### Stage 2: Browser Testing (`qa_browser_engineer` agent + deterministic harness)

The harness runs **before** and **after** the agent:

```
┌─────────────────────────────────────────────────────┐
│ Pre-Agent Harness                                   │
├─────────────────────────────────────────────────────┤
│ 1. Stack Detection (StackDetector.detect_stack())   │
│ 2. Speed Mode Gate (thorough/paranoid only)         │
│ 3. Frontend Check (has_frontend=true required)      │
│ 4. Docker Compose Up (docker compose up -d)         │
│ 5. Install Packages (npm install / pip install)     │
│ 6. Seed Database (run seed scripts)                 │
│ 7. Start Dev Server (on free port, wait healthy)    │
│ 8. Inject Context (base_url, stack info into agent) │
└─────────────────────────────────────────────────────┘
              Agent Runs Here
              (generates test files)
┌─────────────────────────────────────────────────────┐
│ Post-Agent Harness                                  │
├─────────────────────────────────────────────────────┤
│ 9. Run Playwright Tests (npx playwright test)       │
│ 10. Parse Results (JSON reporter)                   │
│ 11. Construct Report (qa_browser_report.json)       │
│ 12. Stop Dev Server                                 │
│ 13. Docker Compose Down                             │
└─────────────────────────────────────────────────────┘
```

**The agent's role:**
1. Reads PRD acceptance criteria
2. Explores generated project (routes, pages, components)
3. Writes TypeScript Playwright test files to `tests/e2e/generated/`
4. Uses accessible selectors (`getByRole`, `getByText`, `getByLabel`)
5. Maps each test to one or more acceptance criteria

**Output artifacts:**
- `tests/e2e/generated/*.spec.ts` (Playwright test files)
- `artifacts/qa_browser_report.json` (test results and diagnostics)

## Speed Mode Gating

The QA-Browser phase is **expensive** (starts services, installs deps, runs browser). It only runs in specific speed modes:

| Speed Mode | Env Setup | QA Browser | Fixer  |
|------------|-----------|------------|--------|
| turbo      | ❌ skip   | ❌ skip    | ❌ skip |
| standard   | ❌ skip   | ❌ skip    | ✅ runs |
| thorough   | ✅ runs   | ✅ runs    | ✅ runs |
| paranoid   | ✅ runs   | ✅ runs    | ✅ runs |

**Stub behavior:** When skipped, both phases produce stub reports with:
- `server_status: "skipped"`
- `verdict: "pass"` (doesn't block the pipeline)
- Informational note explaining why

## Artifact Models

### EnvSetupReport

```python
class EnvSetupReport(BaseModel):
    docker_compose_written: bool          # Was docker-compose.yml created?
    compose_services: list[str]           # ["postgres", "redis", "app", ...]
    seed_script_written: bool             # Was seed script created?
    seed_script_path: str | None          # "scripts/seed.sql", "scripts/seed.py"
    issues: list[str]                     # Non-fatal warnings
    verdict: Literal["pass", "fail"]      # Overall success
```

### QABrowserReport

```python
class BrowserTestResult(BaseModel):
    ac_id: str                            # "AC-001"
    criteria: str                         # Full acceptance criteria text
    status: Literal["passed", "failed", "skipped"]
    test_file: str | None                 # "tests/e2e/generated/ac_001.spec.ts"
    duration_ms: int | None               # Test execution time
    error_message: str | None             # Failure reason if status="failed"
    screenshot_path: str | None           # Screenshot on failure

class QABrowserReport(BaseModel):
    server_status: Literal["started", "failed", "skipped"]
    server_error: str | None              # Error details if failed
    stack_detected: str | None            # "nextjs", "fastapi", etc.
    base_url: str | None                  # "http://localhost:3001" (assigned at runtime)
    test_results: list[BrowserTestResult] # Per-AC results
    tests_passed: int
    tests_failed: int
    tests_skipped: int
    console_errors: list[str]             # Console errors captured during tests
    issues: list[str]                     # Structured diagnostics
    verdict: Literal["pass", "fail"]
```

## The Fixer Agent

When **any step in the pipeline fails**, the **Fixer agent** is automatically invoked (if enabled). It:

1. **Diagnoses** the failure by reading all artifacts in pipeline order
2. **Traces** the decision chain: PM required X → Architect designed Y → Engineer implemented Z → failure happened
3. **Identifies** the single most likely root cause (env_config, logic_bug, missing_dep, brittle_test, schema_mismatch, etc.)
4. **Applies** a minimal targeted fix (fewest files changed, no refactoring)
5. **Reports** what changed in `artifacts/fixer_report.json`

### FixerReport Artifact

```python
class FixerReport(BaseModel):
    failed_step: str                      # "QA Browser", "Env Setup", etc.
    error_summary: str                    # Concise error description
    root_cause_category: str              # "env_config", "logic_bug", etc.
    root_cause_description: str           # Full explanation
    files_changed: list[str]              # ["docker-compose.yml", "package.json"]
    fix_description: str                  # What was fixed and why
    confidence: float                     # 0.0 to 1.0 confidence in the fix
    verdict: Literal["fixed", "escalate"] # "fixed" → retry the step; "escalate" → reviewer
```

### Configuration

The Fixer is configurable via `config/default.yaml`:

```yaml
fixer:
  enabled: true                    # Global enable/disable
  max_attempts: 2                  # Max retries before escalation
  speed_modes:                     # Active in these modes
    - standard
    - thorough
    - paranoid
  model: sonnet                    # Primary model (faster, cost-efficient)
  escalation_model: opus           # Used for complex root causes
```

## Workflow Integration

The two new steps are inserted into the `FEATURE_DEVELOPMENT` workflow between QA and Reviewer:

```
┌──────────────────────────────────────────────────┐
│ PM                                               │
└────────────┬──────────────────────────────────────┘
             ↓
┌──────────────────────────────────────────────────┐
│ Architect                                        │
└────────────┬──────────────────────────────────────┘
             ↓
┌──────────────────────────────────────────────────┐
│ Engineer                                         │
└────────────┬──────────────────────────────────────┘
             ↓
┌──────────────────────────────────────────────────┐
│ QA (static: tests, lint, type-check)             │
└────────────┬──────────────────────────────────────┘
             ↓
┌──────────────────────────────────────────────────┐
│ ★ Env Setup (NEW)                                │
│   └─ Writes docker-compose.yml + seed scripts    │
└────────────┬──────────────────────────────────────┘
             ↓
┌──────────────────────────────────────────────────┐
│ ★ QA Browser (NEW)                               │
│   └─ Starts app, runs Playwright tests            │
└────────────┬──────────────────────────────────────┘
             ↓
      ┌──────┴──────┐
      ↓             ↓
   Reviewer    Fixer (on failure)
      ↓             ↓
   Release    ┌─────┴─────┐
              ↓           ↓
            Fix        Escalate
```

## Error Handling

### Env Setup Phase

| Scenario | Behavior |
|----------|----------|
| No `docker-compose.yml` written | **Fail step** — return to Implementation |
| No seed script written | **Warn** but don't fail (some apps don't need seed data) |
| Compose file is invalid YAML | **Fail step** with parse error |

### QA Browser Phase

| Scenario | Behavior |
|----------|----------|
| No recognizable stack | `server_status: "skipped"`, issue flagged |
| `has_frontend: false` | `server_status: "skipped"`, info note |
| Docker Compose fails to start | `server_status: "failed"`, compose stderr captured |
| Docker not available | **Warn**, attempt direct server start without compose |
| Package install fails | `server_status: "failed"`, stderr captured |
| Seed script fails | **Warn** as major issue, continue (tests may still run) |
| Server won't start | `server_status: "failed"`, stderr captured |
| Agent generates no tests | `tests_skipped: len(ACs)`, `verdict: "fail"` |
| Playwright timeout | Kill tests after 120s, report partial results |
| Browser crashes | `server_status: "failed"`, error captured |

**Guarantee:** All scenarios produce a valid `qa_browser_report.json` — the phase never crashes the pipeline.

### Fixer Error Handling

| Scenario | Behavior |
|----------|----------|
| Fixer disabled in config | Fixer skipped, existing `on_fail` routing applies |
| Speed mode is turbo | Fixer disabled (speed matters more than recovery) |
| `max_attempts` exceeded | Stop retrying, route to `on_fail` |
| Fixer writes `verdict: "escalate"` | Route to Reviewer immediately |
| Fixer agent crashes | Swallow error, fall through to `on_fail` |
| Fix applied but retry still fails | Fixer invoked again (up to `max_attempts`), then escalates |

**Guarantee:** Fixer **never blocks the pipeline** — worst case, the step fails and escalates through normal `on_fail` routing.

## Usage Examples

### Example 1: Running with Browser Testing (thorough mode)

```bash
orchestrate --speed thorough "Build a todo list app with React and PostgreSQL"
```

Output artifacts:
- `workspace/artifacts/env_setup_report.json` — Docker Compose + seed scripts created
- `workspace/artifacts/qa_browser_report.json` — All 5 acceptance criteria passed

### Example 2: Skipped in Standard Mode

```bash
orchestrate --speed standard "Fix typo in header component"
```

Output artifacts:
- `workspace/artifacts/env_setup_report.json` — stub report with `verdict: "pass"`
- `workspace/artifacts/qa_browser_report.json` — stub report with `verdict: "pass"`
- (Both skipped due to speed mode; Fixer runs on any failure)

### Example 3: Fixer Auto-Recovery

A step fails with: "Port 3000 already in use"

Fixer automatically:
1. Reads `env_setup_report.json` to see that compose maps port 3000
2. Modifies `docker-compose.yml` to use port 3001
3. Updates `PORT=3001` in environment
4. Writes `artifacts/fixer_report.json` with `verdict: "fixed"`
5. Engine retries the failed step → succeeds

### Example 4: Backend-Only Project (Graceful Skip)

StackDetector identifies a FastAPI-only project (`has_frontend: false`)

QA Browser phase:
- Runs stack detection
- Sees `has_frontend: false`
- Skips browser tests
- Produces `qa_browser_report.json` with:
  - `server_status: "skipped"`
  - `verdict: "pass"`
  - Info note: "Backend-only project; browser testing not applicable"

Pipeline continues without blocking.

## Stack Detection

The `StackDetector.detect_stack()` function examines a project in **priority order**:

1. **package.json** → Next.js, Vite, CRA, Express, Svelte, Vue, etc.
2. **pyproject.toml** → FastAPI, Django, Flask (Poetry-based)
3. **requirements.txt** → FastAPI, Django, Flask (pip-based)
4. **Gemfile** → Rails
5. **go.mod** → Go, Gin, Echo, Chi
6. **architecture.json** → Explicit tech stack from Architect artifact
7. **Fallback** → Scan for common entry files (`main.py`, `server.js`, etc.)

### StackInfo Dataclass

```python
@dataclass(frozen=True)
class StackInfo:
    framework: str                # "nextjs", "react-vite", "fastapi", etc.
    language: str                 # "javascript", "typescript", "python"
    package_manager: str          # "npm", "yarn", "pnpm", "pip", "poetry"
    install_command: str          # "npm install", "pip install -r requirements.txt"
    dev_server_command: str       # "npm run dev", "uvicorn main:app --reload"
    dev_server_port: int          # 3000, 5173, 8000
    base_url: str                 # "http://localhost:3000"
    health_check_path: str        # "/" or "/health"
    has_frontend: bool            # True for browser-facing UI; False for API/CLI
```

## Extensibility

### Adding Support for New Frameworks

1. **Extend `stack_detector.py`:**
   ```python
   # Add detection in _detect_from_package_json() or similar
   if "my-framework" in pkg_data.get("dependencies", {}):
       return StackInfo(
           framework="my-framework",
           language="javascript",
           package_manager="npm",
           install_command="npm install",
           dev_server_command="npm run dev",
           dev_server_port=3000,
           base_url="http://localhost:3000",
           health_check_path="/",
           has_frontend=True,
       )
   ```

2. **Add unit test:**
   ```python
   def test_detect_my_framework(tmp_path):
       pkg = {"dependencies": {"my-framework": "^1.0"}}
       (tmp_path / "package.json").write_text(json.dumps(pkg))
       info = detect_stack(tmp_path)
       assert info.framework == "my-framework"
   ```

### Customizing Playwright Tests

The `qa_browser_engineer` agent receives context injection:

```python
prompt = build_qa_browser_prompt(
    feature_request="...",
    workspace=Path("/workspace"),
    config=config,
    task_data={
        "stack": stack,
        "base_url": base_url,
        "acceptance_criteria": [...],
        "playwright_examples": "...",  # Agent receives pattern templates
    },
)
```

Customize by:
1. Modifying `.claude/agents/qa_browser.md` system prompt
2. Adding Playwright template examples to `PLAYWRIGHT_EXAMPLES` in `phases.py`
3. Adjusting timeout (120s) in `workflow_engine.py`

## Troubleshooting

### "Server failed to start: Port 3000 already in use"

**Cause:** Generated app hardcodes port 3000; docker-compose also maps 3000.

**Solution:** Fixer (if enabled) will:
- Update `docker-compose.yml` to map 3001
- Set `PORT=3001` environment variable
- Retry the step

Or manually:
1. Edit `docker-compose.yml` at project root
2. Change port mapping: `3001:3000`
3. Update `dev_server_command` to accept PORT from environment

### "npm install failed: peer dependency not satisfied"

**Cause:** Generated code has peer dependency mismatch.

**Solution:** Fixer will identify and fix `package.json` peer deps, or:
1. Edit `package.json` in generated project
2. Add `--no-legacy-peer-deps` to install command in `docker-compose.yml`
3. Retry

### "Tests timed out after 120s"

**Cause:** Tests take longer than 120 seconds (large dataset, slow API).

**Solution:**
1. Adjust timeout in `workflow_engine.py` (default 120s)
2. Simplify test data (fewer records, shorter seed data)
3. Optimize slow database queries or API endpoints

### "QA Browser phase skipped in standard mode"

**Expected behavior.** Speed modes are intentional:
- `turbo` — fastest, skips browser testing
- `standard` — default, skips browser testing (Fixer enabled)
- `thorough` — includes browser testing (slower, more thorough)
- `paranoid` — all validations, Fixer enabled, all artifacts generated

To enable browser testing: `orchestrate --speed thorough "..."`

## See Also

- [Stack Detector API Reference](../STACK_DETECTOR_API.md)
- [ADR: QA-Browser Phase Architecture](../adr/qa_browser_phase.md)
- [Configuration Guide](../QA_BROWSER_CONFIGURATION.md)
- [Testing Playwright Patterns](../PLAYWRIGHT_TESTING_GUIDE.md)
