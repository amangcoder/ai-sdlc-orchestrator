# ADR: QA-Browser Phase and Fixer Agent for Runtime Validation

**Date:** 2026-04-06
**Status:** Proposed
**Decision Makers:** Orchestrator Core Team

## Problem Statement

The orchestrator pipeline generates full-stack applications through the PM → Architect → Engineer → QA workflow, but **QA is limited to static analysis**: running tests, linting, type-checking, and tracing code logic. This means **runtime-visible defects are undetected**:

- Routes declared but returning 404 (missing controller)
- UI components that don't render (missing props, API calls)
- JavaScript runtime errors (event handling, DOM access)
- Form submission failures (API integration issues)
- Backend-frontend contract mismatches

**Current situation:** Generated apps pass static QA but fail when a human actually uses them. Detection of these bugs happens late in the release cycle or post-deployment, adding cost and risk.

## Context

### Constraints

1. **No human intervention** — Must be fully automated (no manual browser clicking)
2. **Deterministic** — Must produce repeatable, verifiable results
3. **Isolated** — Generated apps must run in isolated environments (Docker)
4. **Cost-conscious** — Running Docker + Playwright for every commit is expensive; gate by speed mode
5. **Infrastructure-agnostic** — Must gracefully degrade if Docker is unavailable
6. **No code modification** — Must not modify the generated app to make tests pass (no test-only hacks)

### Alternative Approaches Considered

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **Headless Browser + Playwright (Proposed)** | Automated, natural; tests real browser behavior; mature ecosystem (Playwright); can capture screenshots/videos | Requires Docker; slow (~2-5 min per run); expensive infrastructure | ✅ **CHOSEN** |
| E2E test runner in Jest/pytest | Lightweight; doesn't require browser | Doesn't test real browser (doesn't catch CSS, DOM, event issues); harder to parallelize | ❌ Rejected |
| Visual regression testing (Percy, Chromatic) | Catches visual bugs; CI/CD friendly | Expensive SaaS; requires image storage; overkill for PRD validation | ❌ Rejected |
| Lighthouse audits (performance/accessibility) | Lightweight; automated; useful metrics | Doesn't test acceptance criteria; no functional validation | ❌ Rejected |
| Static analysis + manual QA checklist | Zero infrastructure cost | Manual work defeats "fully automated"; error-prone | ❌ Rejected |

## Decision

### 1. Implement QA-Browser Phase with Two-Stage Architecture

**Stage 1: Environment Setup** — Claude sub-agent writes:
- `docker-compose.yml` with all declared dependencies
- Seed data scripts (SQL, Python, TypeScript) with realistic sample data

**Stage 2: Browser Testing** — Claude sub-agent generates:
- TypeScript Playwright `.spec.ts` test files mapped to PRD acceptance criteria
- Deterministic harness executes them and parses results

### 2. Gate by Speed Mode

**Speed modes control behavior:**

| Mode | Env Setup | QA Browser | Fixer |
|------|-----------|------------|-------|
| turbo | ❌ skip | ❌ skip | ❌ skip |
| standard | ❌ skip | ❌ skip | ✅ run |
| thorough | ✅ run | ✅ run | ✅ run |
| paranoid | ✅ run | ✅ run | ✅ run |

**Rationale:**
- `turbo` mode (~5 min): Intended for rapid iteration; browser testing adds 2-5 min overhead
- `standard` mode (~10 min): Default; includes Fixer to auto-recover from failures
- `thorough` mode (~15 min): Full validation; includes browser testing
- `paranoid` mode (~20 min): All checks; Fixer enabled for recovery

### 3. Implement Fixer Agent for Inline Failure Recovery

When **any step fails**, the Fixer is automatically invoked (if enabled and speed mode allows):

1. **Reads all artifacts** produced so far in pipeline order (prd → architecture → tasks → qa_report → env_setup_report → qa_browser_report)
2. **Traces decision chain** from PM requirement → Architect design → Engineer implementation → failure point
3. **Diagnoses root cause** — categorizes as: env_config, logic_bug, missing_dep, brittle_test, schema_mismatch, other
4. **Applies minimal fix** — edits only the responsible file(s); no refactoring or new features
5. **Retries the step** — if confident (verdict="fixed"), engine retries; if unsure (verdict="escalate"), routes to Reviewer

**Rationale:**
- **Reduces waste:** Failed runs don't require full re-execution; Fixer fixes the root cause and retries
- **Learns from context:** Fixer has access to the full artifact chain, not just error output
- **Cheap recovery:** Sonnet (default) is fast; escalates to Opus only for complex root causes
- **Optional & safe:** Can be disabled entirely; never blocks the pipeline (if Fixer crashes, fall through to on_fail)

### 4. Choose TypeScript + Playwright for Generated Tests

**Why TypeScript for test code (not Python)?**

| Language | Pros | Cons | Decision |
|----------|------|------|----------|
| **TypeScript** | Native to JS/TS frontend projects; Playwright examples in TS; rich ecosystem; familiar to most frontend engineers | Requires node_modules; slightly heavier | ✅ **CHOSEN** |
| Python | Lightweight; good for API testing | Unfamiliar to JS-first frontend engineers; weaker Playwright ecosystem (newer binding) | ❌ Rejected |
| Go | Lightweight; fast | Overkill for test authoring; unfamiliar to most developers | ❌ Rejected |

**Why Playwright (not Cypress/Selenium)?**

| Tool | Pros | Cons | Decision |
|------|------|------|----------|
| **Playwright** | Supports all browsers (Chromium, Firefox, WebKit); excellent selector support (`getByRole`, `getByLabel`); JSON reporter; fast; modern async/await; good for accessibility testing | Requires dev dependency installation | ✅ **CHOSEN** |
| Cypress | Great DX; good debugging | Slower; doesn't support all browsers natively; verbose JSON reporter | ❌ Rejected |
| Selenium | Most mature | Slow; verbose; poor selector API; Python/JS bindings inconsistent | ❌ Rejected |

### 5. Use Deterministic Harness + Claude Agent (Hybrid)

**Harness responsibilities (deterministic, retryable):**
- Stack detection (identifies framework, ports, install commands)
- Docker Compose lifecycle (up, seed, health checks, down)
- Package installation (npm/pip with timeouts)
- Playwright test execution (npx playwright test)
- JSON result parsing and report generation
- Cleanup (graceful shutdown, atexit safety)

**Agent responsibilities (creative, dynamic):**
- Map PRD acceptance criteria to Playwright tests
- Write natural test code with accessible selectors
- Handle project-specific UI patterns (routing, state management)
- Document test intent via test titles and comments

**Rationale:**
- **Determinism:** Infrastructure concerns (Docker, ports, timeouts) are handled by Python code, not Claude
- **Predictability:** Test execution is reproducible; results are consistent
- **Claude focus:** Agent focuses on creative work (understanding PRD, mapping criteria, generating tests), not infrastructure
- **Separation of concerns:** Harness is framework-agnostic; agent is context-aware

### 6. Graceful Degradation Strategy

**If Docker is unavailable:**
- Skip `docker compose up -d`
- Log warning
- Attempt to start dev server directly on localhost
- Continue testing (may fail if app requires services, but don't crash)

**If stack is unrecognizable (`has_frontend=false`):**
- Skip browser testing
- Produce stub `qa_browser_report.json` with `verdict="pass"` and info note
- Pipeline continues

**If speed mode gates the phase:**
- Produce stub reports with `verdict="pass"`
- Pipeline continues (don't block on missing infrastructure)

**Guarantee:** Never crash the pipeline. Worst case: phase is skipped or escalates through `on_fail` routing.

## Implementation Sequence

1. **Models & Enums** → Add `EnvSetupReport`, `QABrowserReport`, `FixerReport`, `FixerConfig` to `models.py`; add roles to `AgentRole` enum
2. **Stack Detector** → Implement `src/orchestrator/stack_detector.py` (zero-dependency, fully testable)
3. **App Server Lifecycle** → Implement `src/orchestrator/app_server.py` (Docker Compose, installation, health checks)
4. **Agent Definitions** → Write `.claude/agents/env_setup.md`, `qa_browser.md`, `fixer.md`
5. **Prompt Builders** → Add `build_env_setup_prompt()`, `build_qa_browser_prompt()`, `build_fixer_prompt()` to `phases.py`
6. **Schema Files** → Create `src/schemas/env_setup_report.schema.json`, `qa_browser_report.schema.json`, `fixer_report.schema.json`
7. **Workflow Wiring** → Update `engine.py` PHASE_ORDER, `workflows.py` FEATURE_DEVELOPMENT, `roles.py` ROLE_REGISTRY, `model_routing.py`
8. **Harness Hooks** → Add pre/post hooks in `workflow_engine.py._execute_step()` and `_invoke_fixer()` method
9. **Config** → Add phase, agent, and fixer config blocks to `default.yaml`
10. **Tests** → Write unit tests for `stack_detector.py`, `app_server.py`; integration tests for full phase

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Docker not available in CI environment** | Phase silently degrades; tests may fail | Graceful fallback to direct server start; clear logging |
| **Test flakiness** (timeouts, race conditions) | False negatives; erodes trust in QA Browser | Generous timeouts (60s server, 120s tests); retry-friendly design; screenshot/error capture |
| **Cost of running Playwright for every change** | Slow feedback loop; discourages testing | Gated by speed mode; turbo/standard skip it; thorough/paranoid include it |
| **Fixer applies wrong fix, masks real bug** | Silent failure; bug shipped | Fixer escalates to Reviewer on low confidence; max_attempts prevents infinite loops; all fixes logged |
| **Generated tests don't match real user behavior** | False positives; tests pass but app still fails IRL | Use accessible selectors (`getByRole`, `getByLabel`); test real user workflows, not implementation |
| **Agent generates no tests (misunderstands PRD)** | Phase reports `verdict="fail"`; unnecessary escalation | Agent receives clear AC list and Playwright examples; PRD context injected into prompt |

## Metrics & Success Criteria

### Phase 1: Environment Setup
- ✅ Docker Compose files are generated for >90% of supported stacks
- ✅ Seed scripts contain realistic sample data matching PRD entities
- ✅ Compose health checks pass for all services
- ✅ Step produces valid `env_setup_report.json` in <5 min

### Phase 2: QA Browser
- ✅ Playwright tests are generated for >80% of acceptance criteria
- ✅ Tests use accessible selectors (no fragile XPath/CSS selectors)
- ✅ Test execution completes in <5 min (Chromium + tests)
- ✅ Reports per-AC verdicts with screenshots on failure
- ✅ Phase gracefully handles backend-only projects (`has_frontend=false`)

### Fixer Agent
- ✅ Root cause categories are accurate in >85% of cases
- ✅ Applied fixes resolve the failure in >70% of cases (first attempt)
- ✅ Fixer never blocks the pipeline (worst case: escalate to Reviewer)
- ✅ Average fix time <30 seconds (Sonnet inference)

## Trade-offs

| Decision | Trade-off |
|----------|-----------|
| Gate by speed mode | Slower feedback in default mode, but acceptable overhead (10 min → 15 min in thorough) |
| TypeScript for tests | Requires Node.js, not pure Python; but natural fit for JS/TS projects |
| Playwright (not custom HTTP client) | Heavier infrastructure (browser), but tests real browser behavior |
| Fixer auto-invocation | Complexity in workflow engine; but eliminates manual retry loops |
| Docker Compose (not docker run) | Requires compose.yml authoring; but enables multi-service orchestration |

## Future Enhancements

1. **Visual regression testing** — Capture screenshots on first run; compare future runs
2. **Performance benchmarks** — Lighthouse audits embedded in QA Browser phase
3. **Accessibility audit** — Axe-core integration to detect WCAG violations
4. **Load testing** — Gauge script generation for performance validation
5. **Cross-browser testing** — Run tests in Firefox, WebKit in addition to Chromium
6. **Fixer escalation model** — Route to Opus for complex root causes; human review for edge cases

## References

- [Playwright Docs](https://playwright.dev/)
- [Docker Compose Docs](https://docs.docker.com/compose/)
- [Accessible Selectors in Playwright](https://playwright.dev/docs/locators)
- [ADR Template](../adr/README.md)
