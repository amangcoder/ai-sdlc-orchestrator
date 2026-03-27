# Changelog

All notable changes to this project will be documented in this file.

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
