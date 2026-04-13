# Changelog

All notable changes to this project will be documented in this file.

## [0.19.0] - 2026-04-13

### Added

**Google Stitch MCP Integration — Design-to-Code for Frontend Agents**
- `StitchMcpConfig` model — configures Stitch MCP server URL, API key, and role injection targets
- `StitchScreen` model — structured screen reference (`name` + `screen_id`) assigned to tasks by the TPM
- `_stitch_mcp_config()` in both `OrchestratorEngine` and `WorkflowEngine` — builds the HTTP MCP server config with API key from config or `STITCH_API_KEY` env var
- `_mcp_servers_for_role()` in both engines — merges base MCP servers with role-specific Stitch injection for frontend/flutter/designer/TPM roles
- `_inject_stitch_section()` in `phases.py` — generates role-scoped Stitch guidance: TPM gets browsing + screen assignment instructions; engineers get only their assigned screen IDs
- `stitch_screens` field on `WorkflowTaskState` — carries screen assignments from TPM to downstream engineers
- Stitch MCP tool call tracking in `AgentActivityTracker` — counts and highlights `mcp__stitch__*` calls in magenta
- Frontend engineer agent updated with Stitch design fetch workflow (step 2) and `Stitch MCP Integration` section
- `config/default.yaml` — `stitch_mcp:` block with URL, API key, and `inject_into_roles` list

**Session Resumption — Resume Agent Conversations Across Retries**
- `session_id` field on `AgentResult` — captured from SDK messages (including early `TaskStarted`/`TaskProgress` messages)
- `session_id` field on `WorkflowTaskState` — persisted even on failure for crash recovery resumption
- `resume_session_id` on `AgentInvocation` — passed to both SDK (`resume`) and CLI (`--resume`) invocation paths
- On task retry, the workflow engine resumes the prior conversation instead of starting fresh

### Changed
- Task scheduler `rebuild_frequency` changed from `len(tasks) // 3` to `1` — readiness graph rebuilds after every task completion for more accurate dependency tracking
- Knowledge base build now runs with `skip_vectors=False` and `skip_features=False` (previously skipped both)
- Verdict rework fix agent now uses the step's own `agent_role` instead of always defaulting to `BACKEND_ENGINEER`
- All `mcp_servers` passed to agent invocations now go through `_mcp_servers_for_role()` for role-aware injection (including spawn, fixer, and rework paths)

---

## [0.18.0] - 2026-04-08

### Added

**Verdict Gating — Block Pipeline on Failing Reviews/QA/Security**
- Verdict gate in `WorkflowEngine`: checks review, QA, and security artifact verdicts before advancing to the next step
- `_check_verdict_gate()` — inspects verdict-bearing artifacts (`review`, `qa_report`, `qa_browser_report`, `env_setup_report`, `threat_model`, `vulnerability_report`, `fixer_report`)
- Severity-aware gating: only blocks on `critical` or `major` issues; minor/nit feedback passes through
- `verdict_rejected` step result triggers automatic fix → re-review rework loop via `_handle_verdict_rework()`

**Verdict Rework — Targeted Fix Loop for Negative Verdicts**
- `--rework-verdicts <RUN_ID>` CLI flag — re-enter the fix → re-review loop for a previous run with unresolved negative verdicts
- `OrchestratorEngine.rework_verdicts()` — loads saved state and delegates to the workflow engine
- `WorkflowEngine.rework_unresolved_verdicts()` — scans all completed steps, identifies failing verdicts, runs targeted rework, and verifies all pass afterward

**Security Verdict Model**
- `SecurityVerdict` enum (`pass` / `fail`) in `models.py`
- `ThreatModel.verdict` field — defaults to `pass`
- `VulnerabilityReport.verdict` field — defaults to `pass`
- `normalize_verdict()` helper for mapping AICoder verdict values to Orchestrator equivalents

**Schemas**
- `threat_model.schema.json` — added `verdict` field (`pass` / `fail`)
- `vulnerability_report.schema.json` — added `verdict` field (`pass` / `fail`)
- Removed stale `default: []` from array fields in `env_setup_report`, `fixer_report`, and `qa_browser_report` schemas

**Role Mapping Completeness**
- Added 30+ missing human-friendly role aliases to `_ROLE_MAP` in `workflows.py` (FinOps, resilience, MCP, designer, Flutter, data engineer, brainstormer, mediator, etc.)
- Added `ENV_SETUP_ENGINEER`, `QA_BROWSER_ENGINEER`, `FIXER` to `role_to_legacy_agent_name()` in `roles.py`
- Improved `_resolve_role()` substring matching to check all normalized forms

### Fixed
- Fixer invocation now catches exceptions instead of crashing the pipeline — treats fixer crash as escalation
- `_env_setup_post_hook()` guards against missing `phase_key` in state before setting error
- Model ordering: moved `FixerReport` and `FixerConfig` before `OrchestratorConfig` to resolve forward reference issues

### Changed
- `PHASE_ORDER` now includes `env_setup` and `qa_browser` between `qa` and `reviewer`
- Test fixtures updated: `WorkflowType` added to `WorkflowDefinition` constructors

### Tests
- `tests/test_verdict_gate.py` — 594-line test suite covering `_check_verdict_gate`, `_archive_verdict_artifact`, `_handle_verdict_rework`, and `verdict_rejected` integration with the main loop

---

## [0.17.0] - 2026-04-06

### Added

**QA Browser Phase — Headless Browser Testing for Generated Apps**
- New pipeline phases: `Env Setup` → `QA Browser` inserted between QA and Release in both `FEATURE_DEVELOPMENT` and `BUGFIX` workflows
- `ENV_SETUP_ENGINEER` agent role — writes `docker-compose.yml` and seed scripts, produces `env_setup_report.json`
- `QA_BROWSER_ENGINEER` agent role — generates Playwright tests mapped to PRD acceptance criteria, produces `qa_browser_report.json`
- `FIXER` agent role — diagnoses pipeline step failures, applies minimal targeted fixes, produces `fixer_report.json` with retry-or-escalate verdict
- `src/orchestrator/stack_detector.py` — detects project stack (React, Next.js, Flask, etc.) for dev server auto-start
- `src/orchestrator/app_server.py` — manages dev server lifecycle for browser testing

**Runtime Validation & Repair Prompts**
- `phases.py` — `build_env_setup_prompt()`, `build_qa_browser_prompt()`, `build_fixer_prompt()` with full artifact-chain context
- Helper functions for extracting PRD acceptance criteria, architecture services, and domain entities from upstream artifacts

**Fixer Agent Loop**
- `workflow_engine.py` — fixer retry loop: on step failure, spawns Fixer agent to diagnose and repair before falling through to `on_fail` routing
- `WorkflowStepDefinition.skip_fixer` — opt-out flag to bypass Fixer for specific steps
- `OrchestratorConfig.fixer` — `FixerConfig` with `enabled` and `max_attempts` settings

**QA Browser Lifecycle Hooks**
- `workflow_engine.py` — `_qa_browser_pre_hook()` starts dev server + Playwright, stubs report on infra failure
- `workflow_engine.py` — `_qa_browser_post_hook()` runs browser tests and tears down server
- `workflow_engine.py` — `_env_setup_post_hook()` validates `docker-compose.yml` after Env Setup step

**E2E Test Infrastructure (Playwright)**
- `tests/e2e/` — full E2E test suite: `test_dashboard_pages.py`, `test_interactive_flows.py`
- `tests/e2e/conftest.py` — Playwright fixtures with screenshot-on-failure capture
- `tests/e2e/utils/server.py` — dashboard server lifecycle management for E2E tests
- `tests/e2e/utils/console_interceptor.py` — browser console error interception
- `pyproject.toml` — new `[e2e]` optional dependency group (pytest-playwright, playwright, httpx)
- `pyproject.toml` — `e2e` pytest marker registered

**CI/CD Pipeline**
- `.github/workflows/ci.yml` — Job 7: E2E Browser Tests (Playwright/Chromium) with cached browser binaries and failure screenshot upload
- `.github/workflows/ci.yml` — Job 8: Deployment Verification (health checks, CLI smoke tests, security hardening validation)
- `scripts/deployment_verify.py` — deployment verification script for Docker image validation

**Makefile Targets**
- `make e2e` — install Playwright + Chromium and run E2E suite headless
- `make e2e-headed` — run E2E suite with visible browser for debugging
- `make e2e-install` — install Playwright + Chromium (skips if cached)
- `make deployment-verify` — verify Docker image is deployment-ready

**Schemas**
- `src/schemas/env_setup_report.schema.json` — schema for Env Setup artifacts
- `src/schemas/fixer_report.schema.json` — schema for Fixer artifacts
- `src/schemas/qa_browser_report.schema.json` — schema for QA Browser artifacts

**Models**
- `EnvSetupReport`, `QABrowserReport`, `BrowserTestResult`, `FixerReport` Pydantic models

**Documentation**
- `docs/features/QA_BROWSER_PHASE.md` — feature specification
- `docs/adr/qa_browser_phase.md` — architecture decision record
- `docs/PLAYWRIGHT_TESTING_GUIDE.md` — E2E testing guide
- `docs/QA_BROWSER_CONFIGURATION.md` — QA browser configuration reference
- `docs/QA_BROWSER_IMPLEMENTATION_GUIDE.md` — implementation guide
- `docs/STACK_DETECTOR_API.md` — stack detector API reference

### Changed

- `engine.py` — `PHASE_ORDER` extended: `env_setup` and `qa_browser` phases added between `qa` and `reviewer`
- `model_routing.py` — `ROLE_CATEGORY` mapping: `ENV_SETUP_ENGINEER` → `CODING`, `QA_BROWSER_ENGINEER` → `VERIFICATION`, `FIXER` → `VERIFICATION`
- `roles.py` — `ROLE_REGISTRY` extended with `ENV_SETUP_ENGINEER`, `QA_BROWSER_ENGINEER`, and `FIXER` role definitions
- `workflows.py` — `FEATURE_DEVELOPMENT` and `BUGFIX` workflows gain `Env Setup` and `QA Browser` steps
- `workflow_engine.py` — step execution restructured into fixer-aware retry loop with pre/post hooks for QA Browser lifecycle

### Tests

- `tests/test_phases.py` — tests for `build_env_setup_prompt()`, `build_qa_browser_prompt()`, `build_fixer_prompt()`, phase definition validation
- `tests/test_model_routing.py` — model routing coverage for new agent roles
- `tests/test_workflows.py` — workflow step ordering and definition tests
- `tests/test_task007_workflow_engine_hooks.py` — QA Browser and Env Setup hook tests
- `tests/test_task008_fixer_invocation.py` — Fixer invocation and retry logic tests
- `tests/unit/test_app_server.py` — app server unit tests
- `tests/unit/test_stack_detector.py` — stack detector unit tests

---

## [0.16.0] - 2026-04-03

### Added

**RAG (Retrieval-Augmented Generation) for Artifact Search**
- `src/orchestrator/rag/` — new module with semantic search over pipeline artifacts using LlamaIndex or LangChain providers
- `rag/indexer.py` — FAISS-backed vector indexing with fastembed embeddings
- `rag/search.py` — top-k semantic search across artifact history
- `rag/mcp_tool.py` — exposes RAG search as an MCP tool for agents
- `rag/mcp_server.py` — standalone MCP server for the RAG index
- `config/default.yaml` — new `rag:` section (disabled by default) with provider, embedding model, chunk size, and top-k settings
- `pyproject.toml` — new `[rag]` and `[rag-langchain]` optional dependency groups
- `artifact_manager.py` — post-save RAG indexing callback for real-time index updates

**MCP Server Health Checks**
- `src/orchestrator/mcp_health.py` — startup health probe for all registered MCP servers; results logged as structured events
- Engine now validates MCP server connectivity before pipeline execution begins

**Mobile App — Alerts, SLO Compliance, Cost Analytics, Artifact Search Screens**
- `mobile/lib/screens/alerts_screen.dart` — real-time alert list with severity filtering
- `mobile/lib/screens/slo_compliance_screen.dart` — SLO compliance dashboard
- `mobile/lib/screens/cost_analytics_screen.dart` — per-run and per-agent cost breakdowns
- `mobile/lib/screens/artifact_search_screen.dart` — full-text artifact search UI
- `mobile/lib/models/` — `alert_model.dart`, `slo_report_model.dart`, `cost_analytics_model.dart`, `artifact_search_result_model.dart`
- `mobile/lib/providers/` — Riverpod providers for alerts, SLO, cost analytics, artifact search
- `mobile/lib/app.dart` — route registrations for the four new screens

**Mobile API — New Backend Routes**
- `src/orchestrator/mobile_api/routes/alerts.py` — alert CRUD with status lifecycle (active/acknowledged/resolved)
- `src/orchestrator/mobile_api/routes/slo.py` — SLO compliance reporting endpoint
- `src/orchestrator/mobile_api/routes/cost_analytics.py` — cost aggregation by run, agent, and phase
- `src/orchestrator/mobile_api/routes/artifact_search.py` — full-text and semantic artifact search
- `src/orchestrator/mobile_api/routes/metrics.py` — Prometheus-style metrics endpoint
- `src/orchestrator/mobile_api/routes/observability.py` — structured log and trace query endpoint
- `src/orchestrator/mobile_api/request_logger.py` — request/response logging middleware

**Database Enhancements**
- `db/migrations/versions/0002_add_alert_status_phase_duration.py` — adds `status` column to alerts, `duration_seconds` to run phases
- `db/models.py` — new indexes: `idx_runs_project`, `idx_phases_name`, `idx_artifacts_agent`, `idx_alerts_severity`
- `Alert` model gains `status` field with lifecycle semantics (active → acknowledged → resolved)
- `RunPhase` model gains `duration_seconds` for wall-clock phase timing

**Knowledge Base MCP for Implementation Roles**
- `phases.py` — knowledge-base-mcp prompt injection now supports `backend_engineer`, `frontend_engineer`, `flutter_engineer` in addition to planning roles
- `config/default.yaml` — `knowledge_base_mcp.inject_into_phases` extended with implementation roles

### Changed

- `engine.py` — run completion now wrapped in try/finally: heartbeat stop, crash recovery deactivation, monitoring notification, and artifact status marking all execute regardless of success/failure
- `engine.py` — RAG MCP tool injected into agent server config when `rag.enabled=True`
- `workflow_engine.py` — `TaskReadinessTracker` pre-satisfies external (cross-step) dependencies from prior completed steps
- `workflow_engine.py` — parallel step task assignment: multi-step workflows now filter tasks by `assigned_role` to prevent duplication across parallel steps
- `monitoring/loki.py`, `monitoring/tracing.py`, `monitoring/__init__.py` — observability integration improvements
- `models.py` — `RAGConfig` added to `OrchestratorConfig`; `RunContext` updated for new fields
- `db/repositories/alerts.py` — `create_alert` accepts `status` parameter; response includes `triggered_at` alias
- `db/repositories/artifacts.py` — artifact queries support agent-based filtering

### Tests

- `mobile/test/screens/` — widget tests for alerts, SLO compliance, cost analytics, and artifact search screens
- `tests/test_mobile_alerts_route.py`, `test_mobile_slo_route.py`, `test_mobile_cost_analytics.py`, `test_mobile_artifact_search.py` — API route tests
- `tests/test_rag_indexer.py`, `tests/test_rag_search.py` — RAG indexing and search unit tests
- `tests/test_mcp_health.py` — MCP health check validation tests
- `tests/test_db_migration_0002.py` — migration 0002 schema tests
- `tests/test_monitoring_event_wiring.py` — monitoring event pipeline tests
- `tests/integration/test_flutter_api_data_contract.py` — Flutter ↔ API data contract tests
- `tests/integration/test_mcp_servers_tools_contract.py` — MCP server tool contract tests
- `tests/integration/test_mobile_monitoring_contract.py` — mobile monitoring integration tests
- `tests/integration/test_monitoring_pipeline_boundary.py` — monitoring pipeline boundary tests
- `tests/integration/test_observability_e2e.py` — observability end-to-end tests
- `tests/integration/test_rag_config_boundary.py` — RAG config boundary tests

---

## [0.15.0] - 2026-03-31

### Added

**SQLAlchemy / Alembic DB Persistence Layer**
- `src/orchestrator/db/` — full database package with SQLAlchemy async engine, session management, ORM models, and Alembic migrations
- `db/engine.py` — async SQLAlchemy engine factory with connection-pool configuration
- `db/session.py` — async session context manager and dependency injection helpers
- `db/models.py` — ORM models: `Run`, `Event`, `Artifact`, `Alert`, `TimelineEntry` (227 lines)
- `db/migrations/` — Alembic env with `0001_initial` migration covering all five tables
- `db/repositories/runs.py`, `events.py`, `artifacts.py`, `alerts.py`, `timeline.py` — repository pattern; each exposes async CRUD + query operations matching the filesystem equivalents
- `db/__init__.py` — public re-exports: `get_engine`, `get_session`, repository classes, `init_db`

**Hosted Mode for RunDataReader**
- `RunDataReader.__init__` now accepts optional `run_repo`, `event_repo_factory`, `alert_repo`, `timeline_repo` injected dependencies; when present, all queries route to the DB and the filesystem is used only as a fallback for legacy runs
- `tail_events` and `get_events` respect the injected `EventRepository` before falling back to log-file parsing

**Run Registry & Persistence Improvements**
- `run_registry.py` — run state now persisted to DB when a `RunRepository` is available; added `list_active_runs()`, `get_run_summary()`, and heartbeat-based liveness tracking
- `persistence.py` — dual-write to both filesystem and DB during phase transitions; `load_run_state` tries DB first then falls back to JSONL
- `workspace_manager.py` — `get_or_create_run_dir` wired to emit a `Run` row on first access

**Observability & Monitoring**
- `observability.py` — metrics emitter now writes structured events to DB `Event` table when a session is available
- `monitoring/alerting.py` — `AlertRepository` integration: alerts persisted to DB alongside filesystem alert log
- `monitoring/timeline.py` — `TimelineRepository` integration: timeline entries persisted to DB

**File-to-DB Migration Script**
- `scripts/migrate_files_to_db.py` — standalone script that reads existing JSONL/JSON workspace files and back-fills the DB tables; idempotent (skips rows already present)

**Development Tooling (`.claude/`)**
- `.claude/hooks/` — quality-gate hooks: `block-no-verify.sh`, `console-log-check.sh`, `cost-tracker.sh`, `mcp-health-check.sh`, `post-edit-lint.sh`, `pre-push-review.sh`, `secret-detection.sh`, `session-persist.sh`
- `.claude/rules/` — coding standards documentation: common rules (agents, style, git, hooks, patterns, performance, security, testing) and Python-specific rules (async, packages, security, style, testing, typing)
- `.claude/skills/` — reusable skill definitions: `build-fix`, `checkpoint`
- `.claude/contexts/` — persona contexts: `dev.md`, `research.md`, `review.md`

### Changed

- `config/default.yaml` — DB connection URL, pool size, and migration settings added under `database:` key
- `engine.py` — pipeline engine initialises DB session on startup when `database.url` is configured
- `models.py` — `RunContext` extended with `db_session` optional field for passing the active DB session through the pipeline
- `infra/docker/docker-compose.monitoring.yml` — minor service ordering fix

### Tests

- `tests/test_db_persistence.py` — 501-line integration test suite covering all five repositories, dual-write behaviour, and fallback logic
- `tests/integration/test_mcp_protocol_compliance.py`, `test_research_cache_integration.py` — updated fixtures for new `RunContext` fields
- `tests/test_cumulative_context_integration.py` — updated for `RunContext` schema changes
- `tests/test_research_cache.py` — minor fixture alignment

---

## [0.14.0] - 2026-03-31

### Added

**Dashboard Overview Page**
- `routes/dashboard_overview.py` — `/dashboard` landing page with four KPI cards (Active Runs, Runs Today, Cost Today, Burn Rate) and SLO compliance summary row
- `GET /api/v1/dashboard/overview` — JSON snapshot of consolidated real-time KPIs
- `GET /api/v1/dashboard/sse` — Server-sent event stream pushing KPI updates every 5 seconds (30-minute max duration to prevent resource exhaustion)
- `static/dashboard.js`, `static/sse-client.js` — client-side SSE subscription with automatic reconnection and patch-in-place DOM updates
- `templates/dashboard.html` — responsive KPI card layout with live badge indicators

**Settings Page**
- `routes/settings.py` — `/settings` page with four tabbed sections: Monitoring (service URLs), Artifacts (versioning, retention, GC controls), SLOs (per-SLI targets), Advanced (remaining config fields)
- `static/settings.js` — fetches current config on load, diffs changed fields on save, inline validation, toast notifications, GC preview/execute flow
- `templates/settings.html` — tabbed form UI with section-level save buttons

**Global Artifact Search**
- `routes/search.py` — `GET /artifacts` HTML search page; `GET /api/v1/artifacts/search` JSON API across all runs
- Full-text substring match on artifact name and schema name; filterable by type and agent; bookmarkable query-string form
- `static/artifacts.js` — progressive enhancement for the search page
- `templates/artifacts_search.html` — results table with run/agent/type columns

**Log Analysis**
- `routes/log_analysis.py` — `GET /runs/{run_id}/log-analysis` HTML page; `GET /api/v1/runs/{run_id}/log-analysis` JSON API
- Surfaces: identified patterns with frequency counts, errors with severity badges and timestamps, actionable recommendations
- `templates/log_analysis.html` — three-section layout with severity-coded error cards

**Monitoring Health Probe**
- `routes/monitoring_health.py` — `GET /api/v1/monitoring/health` probes Prometheus (9090), Grafana (3000), Jaeger (16686), Loki (3100), Promtail (9080) and returns structured status array

**Prompt REST API**
- `routes/prompt.py` — `GET /api/v1/runs/{run_id}/prompt` returns pending prompt state; `POST /api/v1/runs/{run_id}/prompt` writes user response for the engine's `poll_for_response()` loop
- Response sanitisation: Unicode control/format characters stripped, 10 000-char max enforced, `run_id` validated via allowlist regex

**Runs Page**
- `routes/runs.py` — dedicated runs-list route extracted from app.py for cleaner separation

**Shared Validation Utilities**
- `routes/validators.py` — `_validate_run_id()` allowlist regex shared across all run-scoped routes to prevent path traversal

**Monitoring Infrastructure**
- `infra/monitoring/loki.yml` — Loki log aggregation config for the monitoring stack
- `infra/docker/docker-compose.monitoring.yml` — Loki service added to compose stack

### Changed

- `dashboard/app.py` — all new routers registered; route registration refactored into helper functions
- `dashboard/data.py` — `RunDataReader` extended with `get_dashboard_overview()`, `search_artifacts_global()`, `get_log_analysis()`, `check_monitoring_health()`; major additions (~750 lines)
- `dashboard/runner.py` — runner wired to new route modules
- `dashboard/cli.py` — minor CLI flag additions
- `dashboard/static/app.js` — navigation links for new pages; SSE integration in live-run view
- `dashboard/static/style.css` — major UI refresh; KPI card styles, alert severity badges, tabbed settings layout, responsive artifact search table (~1 000 lines net)
- All templates — consistent `base.html` navigation with links to Dashboard, Runs, Artifacts, Settings, SLOs, Cost, Observability, Alerts; live-run view SSE reconnection indicator
- `config/default.yaml` — monitoring service URL defaults; artifact GC defaults
- `main.py` — minor startup additions

### Tests

- `tests/test_dashboard_overview.py`, `test_task002_dashboard_overview.py` — KPI endpoint and SSE stream tests
- `tests/test_dashboard_search.py`, `tests/test_task009_artifact_search.py` — global search route tests
- `tests/test_dashboard_settings.py`, `tests/test_settings_routes.py` — settings GET/PUT tests
- `tests/test_monitoring_health_route.py` — health probe tests (mocked socket connections)
- `tests/test_prompt_routes.py` — prompt GET/POST including sanitisation edge cases
- `tests/test_sse_reconnection.py`, `tests/test_task006_sse_reconnection.py` — SSE client reconnection logic tests
- `tests/test_run_data_reader_task001.py` — RunDataReader extension tests
- `tests/test_task000_security_fixes.py` — run_id validation and response sanitisation tests
- `tests/test_task003_runs_page.py`, `tests/test_task004_new_run_form.py`, `tests/test_task005_live_run_view.py` — page render smoke tests
- `tests/test_task007_artifact_browser.py`, `tests/test_task008_diff_viewer.py` — artifact browser and diff view tests
- `tests/test_task011_alerts_dashboard.py`, `tests/test_task017_log_analysis.py` — alerts and log analysis route tests

---

## [0.13.0] - 2026-03-31

### Added

**Trajectory Tracking**
- `src/orchestrator/trajectory.py` — structured action→observation→reward records per agent invocation
- `TrajectoryStore` with JSONL persistence, per-run and global cross-run storage, and run summary aggregation
- Trajectory verdict classification (`success`, `failure`, `partial`, `pending`) with cost and token attribution per agent
- Adaptive routing foundation: trajectory history enables future model-tier selection based on past agent performance
- Engine integration: trajectory tracking auto-initialized at pipeline start, summary emitted to run metadata on completion

**Claude-flow MCP Bridge**
- `src/orchestrator/claude_flow_bridge.py` — optional integration with Ruflo/claude-flow MCP tools for cross-agent coordination
- Auto-discovery of claude-flow installation from `~/Projects/AITools/ruflo` and fallback paths
- Role-based tool filtering: each agent role receives only the MCP tools relevant to its responsibilities (PM → memory only; engineers → memory + tasks; reviewers → read-only)
- Allowed tool set: `memory/store`, `memory/search`, `memory/list`, `session/save`, `session/restore`, `tasks/create`, `tasks/list`, `tasks/status`, `tasks/dependencies`, and related task operations
- Engine integration: claude-flow MCP config injected into all agent invocations when available
- Agent integration: claude-flow prompt section auto-injected into system prompts per agent role

**Claude Code Developer Tooling** (`.claude/`)
- Contexts: `dev.md`, `research.md`, `review.md` — role-specific behavioral instructions for Claude Code sessions
- Hooks: `block-no-verify.sh`, `console-log-check.sh`, `cost-tracker.sh`, `mcp-health-check.sh`, `post-edit-lint.sh`, `pre-push-review.sh`, `secret-detection.sh`, `session-persist.sh`
- Rules: comprehensive Python and common coding rules covering async patterns, testing, typing, style, packages, security, git workflow, hooks, performance, patterns, and agents
- Skills: `build-fix`, `checkpoint`, `code-review`, `learn`, `plan`, `refactor-clean`, `security-scan`, `tdd`, `update-docs`, `verify`

**Configuration**
- `config/default.yaml` — `trajectory` section: `enabled`, `global_dir`
- `config/default.yaml` — `claude_flow` section: `enabled`, `ruflo_path`, tool category toggles (`memory`, `session`, `tasks`)

### Changed

- `src/orchestrator/engine.py` — trajectory and claude-flow bridge initialization in `PipelineEngine.__init__`; trajectory summary included in run completion metadata
- `src/orchestrator/agents.py` — claude-flow prompt section injected into agent system prompts when bridge is available
- `src/orchestrator/models.py` — new config models for `TrajectoryConfig` and `ClaudeFlowConfig`
- `src/orchestrator/config.py` — `TrajectoryConfig` and `ClaudeFlowConfig` wired into `OrchestratorConfig`

### Tests

- `tests/test_task011_artifact_versioning.py` — artifact versioning and lifecycle tests

---

## [0.12.0] - 2026-03-27

### Added

**Observability & Monitoring Stack**
- Full Prometheus + Grafana + Loki + Promtail monitoring stack via `docker-compose.monitoring.yml`
- `src/orchestrator/monitoring/slo.py` — SLO engine with configurable objectives, burn-rate alerting, and error-budget tracking
- `src/orchestrator/monitoring/loki.py` — Loki log shipper with batch buffering, retry logic, and structured label injection
- `src/orchestrator/monitoring/cli.py` — `orchestrate-monitoring` CLI entry point for managing the monitoring stack
- `src/orchestrator/monitoring/metrics.py` — extended Prometheus metrics (histograms, counters, gauges) for agent performance and cost tracking
- `src/orchestrator/monitoring/config.py` — monitoring configuration with Prometheus, Loki, and Grafana endpoint management
- `src/orchestrator/observability.py` — enhanced observability layer with structured logging and trace correlation
- Prometheus alerting rules (`infra/monitoring/prometheus-rules.yml`) for error rate, latency, and SLO burn-rate
- Promtail pipeline configuration (`infra/monitoring/promtail.yml`) for log collection and labeling

**Grafana Dashboards**
- `agent-performance.json` — per-agent latency, token usage, and success rate panels
- `cost-analysis.json` — cost breakdown by agent, phase, and model with trend tracking
- `error-analysis.json` — error classification, retry rates, and failure pattern visualization
- `run-overview.json` — pipeline run status, duration, and throughput overview
- `slo-overview.json` — SLO compliance, error budgets, and burn-rate alerting panels
- Grafana provisioning for auto-discovery of dashboards and datasources

**Artifact Management**
- `src/orchestrator/artifact_manager.py` — artifact lifecycle manager with metadata tracking, versioning, and cleanup policies
- `src/orchestrator/cli_artifacts.py` — `orchestrate-artifacts` CLI for listing, inspecting, and managing pipeline artifacts
- `src/orchestrator/dashboard/routes/artifacts.py` — dashboard routes for artifact browsing and detail views
- `src/orchestrator/dashboard/templates/artifacts.html` — artifact browser UI template

**Dashboard Extensions**
- `src/orchestrator/dashboard/routes/cost.py` — cost analytics dashboard route with per-run and per-agent breakdowns
- `src/orchestrator/dashboard/routes/observability.py` — observability dashboard route with health checks and metrics
- `src/orchestrator/dashboard/routes/slo.py` — SLO dashboard route with compliance and error-budget views
- `cost_analytics.html`, `observability.html`, `slo.html` — new dashboard templates
- Dashboard route registration via `routes/__init__.py` blueprint pattern

**Configuration & Infrastructure**
- `observability` extras group in `pyproject.toml` (`httpx`, `prometheus-client`)
- `orchestrate-monitoring` and `orchestrate-artifacts` CLI entry points registered in `pyproject.toml`
- Monitoring config section added to `config/default.yaml`
- `infra/scripts/start-monitoring.sh` — one-command monitoring stack launcher

**Engine & Model Extensions**
- `engine.py` — observability hooks for phase lifecycle events
- `models.py` — extended models for SLO objectives, monitoring config, and artifact metadata
- `workflow_engine.py` — monitoring integration points and metric emission during workflow execution

### Tests
- `test_artifact_manager.py`, `test_artifact_manager_integration.py` — artifact manager unit and integration tests
- `test_artifact_routes.py` — artifact dashboard route tests
- `test_cli_artifacts.py` — artifact CLI tests
- `test_cli_monitoring.py` — monitoring CLI tests
- `test_cost_analytics.py` — cost analytics dashboard tests
- `test_dashboard_extended.py` — extended dashboard route tests
- `test_log_shipper.py` — Loki log shipper tests
- `test_monitoring_config.py` — monitoring configuration tests
- `test_monitoring_integration.py` — end-to-end monitoring integration tests
- `test_monitoring_loki.py` — Loki client tests
- `test_monitoring_metrics.py` — Prometheus metrics tests (extended)
- `test_monitoring_slo.py` — SLO engine tests
- `test_observability_routes.py` — observability dashboard tests
- `test_slo.py`, `test_slo_routes.py` — SLO route and logic tests
- `test_task008_observability_monitoring.py` — full observability feature validation suite

### Documentation
- `docs/observability_verification.md` — observability setup verification guide

---

## [0.11.0] - 2026-03-26

### Added

**Crash Recovery & Resilience**
- New `crash_recovery` module (`src/orchestrator/crash_recovery.py`) — heartbeat-based crash detection, SIGTERM/atexit emergency flush, and automatic recovery on resume
- `CrashRecoveryManager` maintains a 10-second heartbeat loop, installs SIGTERM and atexit handlers, and performs emergency state flush on unexpected termination
- `detect_crash()` determines if a loaded `RunState` represents a crashed run via PID liveness check and heartbeat staleness (>30s threshold)
- `recover_from_crash()` resets CRASHED/IN_PROGRESS tasks to PENDING, cleans orphaned worktrees, and prepares state for re-execution
- `cleanup_orphaned_worktrees()` force-removes registered worktrees and prunes stale git references
- `WorktreeRecord` and `CrashEvent` Pydantic models in `models.py` for structured crash tracking
- `CRASHED` status added to both `RunStatus` and `TaskStatus` enums
- Per-task cost tracking (`cost_usd`, `input_tokens`, `output_tokens`) on `WorkflowTaskState` for crash-recovery cost accounting
- `RunState` extended with `pid`, `last_heartbeat_at`, `crash_count`, `active_worktrees`, and `crash_history` fields
- Partial agent output saved to `.partial/` directory for crash recovery context injection on retry
- Prior-attempt context automatically injected into agent prompts when resuming crashed tasks
- Worktree lifecycle tracked in `RunState.active_worktrees` for reliable cleanup after crashes
- `InterruptManager` now handles SIGTERM in addition to SIGINT for graceful container shutdown
- Watchdog `_validate_state_file()` detects and logs crash indicators in saved state

### Tests
- MCP protocol compliance integration tests (`tests/integration/test_mcp_protocol_compliance.py`)
- Main recommendations test suite (`tests/test_main_recommendations.py`)
- Config validation script (`validate_config.py`)

---

## [0.10.0] - 2026-03-26

### Added

**Two-Tier Research Cache**
- New `research_cache` module (`src/orchestrator/research_cache.py`) — persistent two-tier (global + per-project) research cache to reduce redundant agent research across pipeline runs
- `ResearchCacheConfig`, `ResearchCacheContext`, `ResearchEntry`, `ResearchCache`, and `Finding` Pydantic models in `models.py`
- MCP server integration: `lookup_research`, `save_research`, and `flag_finding` tools injected into PM, Architect, and Principal Engineer prompts
- Automatic research extraction from phase artifacts via `extract_research_from_artifact()` with configurable TTLs (90 days stable, 7 days volatile)
- End-of-run findings summary printed when actionable issues are flagged during pipeline execution
- `_inject_research_context()` in `phases.py` — cache-first protocol instructions wrapped in `<research-cache-data>` delimiters with byte-level truncation
- `_inject_mcp_role_guidance()` extended with Research Cache tool rows for eligible roles
- `research_cache` config section in `config/default.yaml` with all tuning knobs (TTL, max entries, inject phases, auto-extract)
- Research cache MCP config cleanup on pipeline completion (mirrors test-runner cleanup pattern)
- `WorkflowEngine._mcp_servers` now merges knowledge + test-runner + research-cache configs (parity with `engine.py`)

### Fixed
- `self_orchestrate.py`: zero-division guard in language stats when `total_lines` is 0

### Tests
- Comprehensive unit tests for `_inject_research_context()` and `_inject_mcp_role_guidance()` with research cache
- Unit tests for `ResearchCacheConfig`, `ResearchCacheContext`, `ResearchEntry`, `Finding` models
- Integration tests for MCP protocol compliance and research cache lifecycle

---

## [0.9.0] - 2026-03-25

### Added

**Containerized Orchestration (opt-in)**
- New `orchestrate-container` CLI entrypoint — runs each orchestration in an ephemeral, isolated Docker container
- `ContainerRuntime` manages full container lifecycle: argument assembly, output streaming, signal forwarding, and post-exit artifact validation
- `ArtifactBridgeVolume` manages the run-specific bind-mount with TOCTOU-safe path validation
- Security hardening: read-only rootfs, `--cap-drop ALL`, seccomp profile, AppArmor (Linux), PID/memory/CPU limits, no-new-privileges
- Network isolation via `orchestrator-net` Docker network with iptables egress rules limiting outbound traffic to `api.anthropic.com:443`
- DNS injection via `--add-host` to allow API access when container DNS (port 53) is blocked
- Forbidden-file scanner checks bind-mounted output for suspicious files (`.bashrc`, `.ssh`, `.gitconfig`, etc.) after container exit
- `ContainerConfig` Pydantic model in `models.py` with full validation (image name, network mode, tmpfs path allowlist)
- `container` section added to `config/default.yaml` (disabled by default — set `enabled: true` to activate)
- Infrastructure: `infra/docker/Dockerfile`, `seccomp-profile.json`, `apparmor-profile`, `docker-compose.yml`
- Scripts: `build-image.sh`, `run-containerized.sh`, `setup-network-policy.sh`, `load-apparmor-profile.sh`, `install-iptables-restore-service.sh`
- CI workflow (`.github/workflows/ci.yml`) and `Makefile` with standard dev targets
- Comprehensive test coverage: `test_container_config.py`, `test_container_runner.py`

---

## [0.8.0] - 2026-03-24

### Added

**12 New Specialist Agent Roles**
- MCP Tool Designer, MCP Server Engineer, MCP Protocol Reviewer, MCP Integration Test Engineer
- Chatbot Engineer, Social Media Integration Engineer, Data Engineer
- Resilience Tester, Change Impact Analyzer, FinOps Estimator, Runbook Author, Refactoring Planner
- Generic `_build_generic_specialist_prompt()` factory in `phases.py` for consistent prompt structure
- All new roles registered in `PROMPT_BUILDERS` and `ROLE_CATEGORY` model routing map

**Mobile API Hardening**
- Auth middleware registered as Starlette HTTP middleware (fail-closed Bearer token validation)
- Auth returns `JSONResponse(401)` instead of raising `HTTPException` — prevents FastAPI exception-handler leaks
- Active-run conflict guard on `POST /runs/{id}/resume` returns `409` when another run is already active
- Run listing sorted by `start_time` with `_mtime` fallback for more accurate ordering

**Config & Workflow Fixes**
- `allowed_directories` parsed from config YAML into typed `AllowedDirectoryConfig` objects
- `TaskReadinessTracker.mark_completed()` now clears tasks from `pending` set (previously only cleared `in_progress`)
- `build_cli_args()` uses `isinstance` check for `max_concurrent_agents` instead of truthy comparison

---

## [0.7.0] - 2026-03-24

### Fixed

**MCP Phase Name Normalization**
- `get_cumulative_context(phase=...)` calls across all 30+ agent roles in `phases.py` now use canonical phase names (`prd`, `architecture`, `engineering_plan`, `task_breakdown`, `implementation`, `qa`, `reviewer`) instead of legacy role-based names (`pm`, `architect`, `engineer`, etc.)
- `update_cumulative_context()` in `knowledge.py` extended with canonical phase keys alongside legacy backward-compat aliases
- `engine.py` routes phase names through `map_phase_for_mcp()` before calling `update_cumulative_context()`, ensuring the MCP server receives the correct phase identifier

---

## [0.6.0] - 2026-03-23

### Added

**Flutter Engineer Role**
- `FLUTTER_ENGINEER` agent role with dedicated `build_flutter_engineer_prompt()` targeting the `mobile/` directory
- Enforces Riverpod state management and ConsumerWidget patterns; writes widget and unit tests alongside implementation
- Registered in `ROLE_REGISTRY` and `role_to_legacy_agent_name()` mapper

**Artifact Prompt Hardening**
- `api_contract`: replaced loosely-described fields with strict required keys (`base_url`, `endpoints` with typed sub-fields, optional `auth`/`schemas`)
- `migration_plan`: strict required fields — `risk_level`, `strategy`, `phases` (with per-phase `rollback_steps`, `verification_queries`, `requires_downtime`), `rollback_plan`, `data_backup`
- `ux_spec`: locked to exact top-level keys (`flows`, `components`, `responsive_behavior`); explicitly forbids legacy keys (`user_flows`, `screens`, `accessibility_requirements`, etc.) to end repeated schema drift
- `tech_debt_inventory`: structured `debt_items` array with `quadrant`, `priority`, `test_coverage`; added `health_score`, `quick_wins`, `do_not_touch`
- `release_plan`: required `version_bump` (`major|minor|patch`), `release_notes`, structured `changelog` dict and `rollout_plan`
- `incident_report`: required `title`, `severity`, `symptom`, `expected_behavior`, `five_whys`, `affected_code` (with per-entry `file`/`line`/`description`)
- `load_test_report`: required `test_profiles` (with per-profile pass/degraded/fail result) and `capacity_recommendation`
- `compliance_report`: required `applicable_regulations` and `summary`; structured `compliance_gaps` with per-gap `id`, `risk_level`, `remediation`

**Validation Auto-Normalization**
- `_normalize_artifact_values()` in `validation.py` translates common LLM output variants before schema validation (e.g. review verdict `"pass"` → `"approve"`, `"fail"` → `"reject"`)
- Normalization runs before both JSON Schema and Pydantic layers; rewritten file is persisted to disk so the fix is permanent
- `_VERDICT_NORMALIZE` map imported from `models.py` and consumed by the validator

**JSON Schema Tightening**
- All 18 artifact schemas updated: stricter `required` arrays, tighter `enum` constraints, `additionalProperties: false` where appropriate, and corrected `$schema` declarations

**Tests**
- Extended `tests/test_validation.py` with verdict normalization round-trip tests and schema-edge cases

### Changed

- `ux_specifier.md` agent definition updated to match the new locked `ux_spec.json` schema

---

## [0.5.0] - 2026-03-19

### Added

**Mobile App — Projects & SSH**
- Project-centric dashboard with 2-column grid replacing the flat runs list (REQ-001)
- Project detail screen (`project_detail_screen.dart`) with per-project run history
- SSH terminal screen (`ssh_terminal_screen.dart`) with quick-connect from dashboard AppBar (REQ-022)
- SSH credentials management in Settings with host reachability probing
- Directory browser bottom sheet widget for workspace navigation
- Prompt card widget for displaying pending orchestrator prompts
- `ProjectsProvider` for project state management
- `SshService` for SSH connection handling
- Models: `PendingPrompt`, `ProjectEntry`, `SshCredentials`, `SshConfigResponse`, `DirectoryChildrenResponse`

**Mobile API — Projects, SSH & Prompts**
- `GET /api/v1/projects` and project routes (`routes/projects.py`)
- `GET /api/v1/ssh/config` and SSH routes (`routes/ssh.py`) with TCP reachability probe
- `GET /api/v1/runs/{run_id}/pending-prompt` — poll for orchestrator prompts awaiting user input
- `POST /api/v1/runs/{run_id}/respond` — submit user response to pending prompt
- `PromptManager` (`prompt_manager.py`) — file-based prompt/response exchange between mobile client and orchestrator
- `DynamicDirectoryService` (`dynamic_directory_service.py`) — rglob fallback for workspace resolution
- `SystemRunner` (`system_runner.py`) — subprocess orchestration for mobile-triggered runs
- `SpeedEnum` type added to models (turbo/standard/thorough/paranoid/auto)
- WebSocket prompt sanitization (truncation, bidi char stripping) and run_id format validation

**Performance Optimizations (Phase 2 & 3)**
- `ArtifactCache` — phase-scoped in-memory cache eliminating 60+ redundant disk reads per phase
- `TaskReadinessTracker` — fine-grained dynamic task scheduling replacing fixed wave barriers (10–150s savings)
- Async worktree creation/merge/cleanup via `asyncio.create_subprocess_exec` for parallel engineer setup
- Artifact digest functions (`_digest_prd`, `_digest_architecture`, `_digest_tasks`, `_digest_engineering_plan`) operating on cached data without disk I/O

**Tests**
- Contract tests for projects, prompts, dynamic directories, and system orchestration
- SSH storage tests, directory browser widget tests, prompt card widget tests
- `PendingPrompt` and `ProjectEntry` model tests
- Screen-level tests for mobile app
- Phase 2 and Phase 3 performance test suites
- LRU cache unit tests

### Changed

- Dashboard screen now project-centric with pull-to-refresh for both projects and runs
- Settings screen extended with SSH credentials section (host, port, username, key/password)
- Runs route refactored with shared `_resolve_workspace_path` helper eliminating copy-paste
- WebSocket route validates run_id format before accepting connections
- `RunStartRequest` now supports `speed` field; removed `enhanced_perception` field
- `AgentInvocation` model dropped `enhanced_perception` field

### Removed

- `perception.py` — enhanced perception meta-cognitive prompt enrichment layer (replaced by artifact digest optimizations)

---

## [0.4.0] - 2026-03-19

### Added

**Mobile App (Flutter)**
- New `mobile/` Flutter application for iOS and Android
- Screens: Dashboard, Run Detail, New Run, Live Events, Artifact Viewer, Config Editor, Settings
- Providers: `AuthProvider`, `RunsProvider` for state management
- Services: `ApiService`, `WebSocketService`, `SecureStorageService`, `FlagPreferencesService`
- Real-time run monitoring via WebSocket event stream

**Mobile API Backend**
- New `mobile_api/` FastAPI server (`server.py`) exposing the orchestrator over HTTP/WebSocket
- REST routes: runs (`/runs`), config (`/config`), directories (`/directories`)
- WebSocket route (`/ws`) for live phase/event streaming
- Token-based auth (`auth.py`) with rate limiting (`rate_limit.py`)
- QR-code setup flow (`qr_setup.py`) for easy mobile onboarding
- Tailscale integration (`tailscale.py`) for secure remote access without port forwarding
- Directory service (`directory_service.py`) for workspace navigation from mobile

**Integration Test Suite**
- Full contract test coverage for all mobile API routes: runs, config, directories, auth, WebSocket
- Round-trip data tests, artifact security tests, E2E scenario tests
- Shared `conftest.py` with reusable fixtures for the integration suite

**Performance & Caching**
- Caching layer across pipeline phases (see `CACHE_PATTERNS.md`)
- Performance optimizations documented in `PERFORMANCE_OPTIMIZATION_PLAN.md`
- Phase 2 and Phase 3 performance test suites

### Changed

- `models.py` — extended run/phase models with fields required by mobile API
- `config/default.yaml` — added mobile API server configuration block
- `model_routing.py` — routing integration fixes merged from master
- `phases.py`, `engine.py`, `workflow_engine.py`, `agents.py` — minor updates supporting mobile API hooks
- `self_orchestrate.py` / `interruption.py` — compatibility fixes

## [0.3.1] - 2026-03-18

### Fixed

**MCP Propagation**
- Propagate `mcp_servers` config into `DebateEngine` so debate-phase agents (advocates, critics, mediator) can access MCP knowledge tools
- Pass `mcp_servers` from `OrchestratorEngine` into `DebateEngine` constructor

**Knowledge Watcher Robustness**
- Add `_rebuild_lock` and `_rebuild_pending` flag to serialize rebuilds and coalesce back-to-back requests — prevents concurrent rebuilds from corrupting the index
- Expose `failed`, `failure_error`, and `alive` properties for health inspection
- Auto-restart watcher in `WorkflowEngine` if it dies unexpectedly instead of silently losing live-index updates
- Log full traceback (`exc_info=True`) when the watch loop dies

**Validation Fixes**
- Fix `Architecture.data_flow` validator: switch from `mode="before"` to `mode="after"` and remove `min_length` constraint from the `Field()` so list-format data flows no longer fail validation
- Move length check into the validator body (only enforced for string format)

### Added

**Role-Specific MCP Tool Guidance**
- Add per-role MCP tool guidance (`_MCP_ROLE_GUIDANCE` dict) covering all SDLC roles: PM, architect, principal engineer, TPM, engineers, QA, reviewers, security, and specialized roles
- Each role gets tailored instructions on which MCP tools to use and in what order
- Reusable `_ARTIFACT_VALIDATION_BLOCK` template injected into all artifact-producing roles

**Expanded MCP Tool Documentation in Prompts**
- Document pipeline artifact tools (`get_artifact_schema`, `get_artifact_store_path`, `validate_artifact_draft`, `get_cumulative_context`) in the shared MCP reference block
- Document directory/pattern/search tools (`get_directory_tree`, `get_code_patterns`, `find_template_file`, `semantic_search`, `explore_graph`, `get_feature_context`, `get_static_data_schema`)
- Add `semantic_search` and `explore_graph` references to exploration instructions for all depth levels

**Inter-Wave Knowledge Rebuild**
- Rebuild the knowledge index between implementation waves so later-wave agents discover symbols created by earlier waves
- Uses fast mode (`skip_vectors=True, skip_features=True`) to minimize rebuild latency

**Agent Tool Discipline**
- Inject MCP tool priority and Bash restriction guidance into the autonomous agent prefix — agents now prefer MCP tools over Glob/Grep/Bash for exploration

---

## [0.3.0] - 2026-03-18

### Added

**Speed Modes (`--speed` flag)**
- Four concrete pipeline-depth modes: `turbo` (4 steps, $0.01–0.03), `standard` (6 steps, $0.05–0.15), `thorough` (8+ steps, $0.20–0.50), `paranoid` (10+ steps, $0.50–2.00)
- `auto` (default) — single Claude Haiku call classifies the feature request into a complexity tier and maps it to a concrete speed mode in ~1–2 seconds at ~$0.001/run
- Risk-signal escalation: any mention of auth, payments, PII, encryption, or compliance automatically bumps low tiers (turbo/standard) to `thorough`
- `SpeedMode` enum in `models.py` with `AUTO` sentinel kept separate from concrete modes
- `apply_speed_mode()` in `model_routing.py` — validates that `AUTO` is never applied directly
- `auto_classify_speed()` in `model_routing.py` — async Haiku classifier with 2 s timeout, JSON extraction via regex, risk-flag escalation, and safe fallback to `standard` on any failure
- `_build_speed_mode_section()` in `self_orchestrate.py` — injects mode-specific advisory instructions into the planning and feedback prompts
- `speed_mode` field added to `OrchestratorConfig` and `OrchestrationPlan`

**Tests**
- `tests/test_self_orchestrate_speed.py` — unit tests for `_build_speed_mode_section` covering all five modes
- `tests/test_model_routing.py` — extended with `apply_speed_mode` and `auto_classify_speed` coverage

**Docs**
- `CLAUDE.md` updated with Speed Modes section: mode table, auto-classification flow, examples, cost/performance notes, and implementation details

### Changed
- `.gitignore` extended to exclude `*.new` workspace artefacts

---

## [0.2.0] - 2026-03-17

### Added

**Agent Roster (50+ specialized agents)**
- Core SDLC: `pm`, `architect`, `engineer`, `qa`, `qa_planner`, `qa_executor`, `reviewer`, `principal_engineer`, `tpm`, `release_engineer`
- Infrastructure: `devops_engineer`, `aws_specialist`, `azure_specialist`, `gcp_specialist`, `cicd_specialist`, `automation_engineer`, `migration_engineer`
- Engineering specialisms: `backend_engineer`, `frontend_engineer`, `database_engineer`, `caching_engineer`, `ml_specialist`, `llm_specialist`, `agentic_ai_specialist`, `mcp_server_engineer`, `mcp_tool_designer`, `mcp_protocol_reviewer`, `mcp_integration_test_engineer`, `chatbot_engineer`, `data_engineer`, `social_media_integration_engineer`
- Quality & security: `backend_reviewer`, `frontend_reviewer`, `security_engineer`, `compliance_auditor`, `dependency_auditor`, `accessibility_auditor`, `load_test_engineer`, `integration_test_engineer`, `resilience_tester`, `incident_analyst`, `observability_engineer`, `tech_debt_assessor`, `change_impact_analyzer`, `refactoring_planner`
- Research & planning: `deep_researcher`, `market_researcher`, `competitor_researcher`, `brainstormer`, `mediator`, `field_specialist`, `legal_advisor`, `finops_estimator`, `runbook_author`, `ux_specifier`, `user_behavior_psychologist`, `end_user_simulator`, `runpod_specialist`, `documentation_engineer`

**JSON Schemas (36 artifact types)**
- New schemas: `change_impact_analysis`, `cost_estimate`, `data_pipeline_design`, `mcp_test_report`, `mcp_tool_spec`, `refactoring_plan`, `resilience_test_plan`, `runbook`
- Extended existing schemas for structured inter-agent communication

**Workflow Engine**
- Multi-phase SDLC orchestration with structured validation checkpoints
- Parallel execution support with `isolation: worktree` for engineer agents
- Artifact rescue system for partial failure recovery
- Resume capability for interrupted workflow runs

**Model Routing**
- Complexity-based routing: Opus 4.6 for deep reasoning (principal engineer, reviewers, security), Sonnet 4.6 for implementation/QA, Haiku 4.5 for docs/git ops

**Observability & Monitoring**
- Structured JSON run logs via `structlog`
- OpenTelemetry tracing support
- Prometheus metrics endpoint
- Real-time dashboard (FastAPI + Jinja2)

**Configuration**
- Expanded `config/default.yaml` with per-phase timeouts, model overrides, exploration depth, artifact paths, and monitoring settings

**Core Engine Improvements**
- Pydantic v2 models for all inter-agent data structures (`src/orchestrator/models.py`)
- Role definitions with capability metadata (`src/orchestrator/roles.py`)
- Configurable spawning strategies (`src/orchestrator/spawning.py`)
- Knowledge watcher for live context updates (`src/orchestrator/knowledge_watcher.py`)

**CLI**
- `orchestrate` — full pipeline or single-phase execution
- `orchestrate-watchdog` — file-system watch mode
- `orchestrate-dashboard` — start the monitoring dashboard

### Removed
- Knowledge base and workspace directories (replaced by artifact-based communication)

## [0.1.0] - 2026-01-01

### Added
- Initial pre-release scaffolding
