# Changelog

All notable changes to this project will be documented in this file.

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
