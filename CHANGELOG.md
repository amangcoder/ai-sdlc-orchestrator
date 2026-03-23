# Changelog

All notable changes to this project will be documented in this file.

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
