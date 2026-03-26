"""Core orchestration engine — runs the SDLC pipeline via workflow engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from orchestrator.agents import AgentInvocation, AgentResult, invoke_agent, invoke_agents_parallel
from orchestrator.knowledge import (
    build_knowledge,
    cleanup_mcp_config,
    ensure_gitignore_entries,
    ensure_mcp_config,
    get_mcp_server_config,
    map_phase_for_mcp,
    synthesize_brief,
    update_cumulative_context,
)
from orchestrator.test_runner import (
    cleanup_test_runner_mcp_config,
    ensure_test_runner_mcp_config,
    get_test_runner_mcp_config,
)
from orchestrator.research_cache import (
    cleanup_research_mcp_config,
    extract_research_from_artifact,
    format_recommendations,
    get_research_mcp_config,
    save_entry,
)
from orchestrator.models import (
    EngTaskState,
    Finding,
    KnowledgeContext,
    ModelTier,
    OrchestratorConfig,
    PhaseState,
    PhaseStatus,
    ResearchCacheContext,
    ReviewVerdict,
    RunState,
    SpawnRecord,
    TaskStatus,
    WorkflowType,
    normalize_verdict,
)
from orchestrator.observability import RunLogger
from orchestrator.phases import (
    PHASE_DEFINITIONS,
    build_engineer_prompt,
    build_reviewer_prompt,
    get_engineer_tasks,
)
from orchestrator.validation import validate_artifact_file
from orchestrator.workflows import BUILTIN_WORKFLOWS, parse_custom_workflow, select_workflow
from orchestrator.workflow_engine import (
    WorkflowEngine,
    _rescue_artifacts_from_output,
    _rescue_misplaced_artifacts,
)
from orchestrator.workspace_manager import WorkspaceManager

logger = logging.getLogger(__name__)

PHASE_ORDER = ["pm", "architect", "engineer", "qa", "reviewer"]

# Keys whose values should be redacted before logging config data
_SENSITIVE_KEYS = {"api_key", "api_token", "secret", "password", "webhook", "url", "token"}


def _redact_sensitive(obj: Any, *, _parent_key: str = "") -> None:
    """Recursively redact sensitive values in a dict/list in place."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            lower_key = key.lower()
            if any(s in lower_key for s in _SENSITIVE_KEYS) and isinstance(value, str) and value:
                obj[key] = value[:8] + "..." if len(value) > 8 else "***"
            else:
                _redact_sensitive(value, _parent_key=key)
    elif isinstance(obj, list):
        for item in obj:
            _redact_sensitive(item, _parent_key=_parent_key)

# Maps legacy phase names to workflow step names for backward compat
_PHASE_TO_STEP: dict[str, str] = {
    "pm": "PRD",
    "architect": "Architecture",
    "engineer": "Implementation",
    "qa": "QA",
    "reviewer": "Code Review",
}


class OrchestratorEngine:
    """Runs the full SDLC orchestration pipeline.

    Supports two execution modes:
    1. Workflow mode (new) — uses WorkflowEngine with workflow definitions
    2. Legacy mode — uses the original hardcoded PHASE_ORDER pipeline

    Legacy mode is used when single_phase or from_phase are specified
    with old phase names.
    """

    def __init__(
        self,
        config: OrchestratorConfig,
        dry_run: bool = False,
        interrupt_manager: Any | None = None,
        confirm_callback: Any | None = None,
    ) -> None:
        self.config = config
        self.dry_run = dry_run
        self.interrupt_manager = interrupt_manager
        self.confirm_callback = confirm_callback
        self.run_logger: RunLogger | None = None
        self._test_runner_mcp_config: dict[str, Any] | None = None
        self._research_mcp_config: dict[str, Any] | None = None

    @property
    def _mcp_servers(self) -> dict[str, Any] | None:
        """Return merged MCP server config for agent invocations."""
        servers: dict[str, Any] = {}
        kc = self.config.knowledge_context
        if kc and kc.mcp_server_config:
            servers.update(kc.mcp_server_config)
        if self._test_runner_mcp_config:
            servers.update(self._test_runner_mcp_config)
        rc = self.config.research_cache_context
        if rc and rc.mcp_server_config:
            servers.update(rc.mcp_server_config.get("mcpServers", {}))
        return servers or None

    async def run(
        self,
        feature_request: str,
        single_phase: str | None = None,
        from_phase: str | None = None,
        resume: bool = False,
        resume_run_id: str | None = None,
        workflow_type: WorkflowType | None = None,
        custom_workflow: str | None = None,
        run_id: str | None = None,
    ) -> RunState:
        """Execute the orchestration pipeline."""
        # Apply routing mode if set (ensures API-triggered runs respect mode)
        if self.config.routing_mode:
            from orchestrator.model_routing import RoutingMode, apply_routing_mode
            apply_routing_mode(self.config, RoutingMode(self.config.routing_mode))

        self.project_root = Path.cwd()

        # Workspace resolution
        workspace_root = Path(self.config.workspace_root or self.config.workspace_dir).resolve()
        project_name = self.config.project_name or self.project_root.name
        self.manager = WorkspaceManager(workspace_root, project_name, project_root=self.project_root)

        # For backward compat, if workspace_root isn't set, we use the flat project_workspace
        if not self.config.workspace_root:
            workspace = Path(self.config.workspace_dir).resolve()
        else:
            # New style: we will resolve the specific run_workspace later once we have run_id
            workspace = self.manager.project_workspace

        workspace.mkdir(parents=True, exist_ok=True)

        # Ensure .knowledge/ and workspace_root are in the target project's .gitignore
        ensure_gitignore_entries(self.project_root)

        # Resume from saved state, or start fresh
        if resume or resume_run_id:
            state = self._load_state(workspace, run_id=resume_run_id)
            if resume_run_id and state is None:
                raise ValueError(
                    f"No saved state found for run '{resume_run_id}'. "
                    f"Check workspace: {workspace}"
                )
        else:
            state = None
        if state:
            logger.info(f"Resuming run {state.run_id} — skipping completed phases: "
                        f"{[p for p, s in state.phases.items() if s.status == PhaseStatus.COMPLETED]}")
        else:
            final_run_id = run_id or uuid.uuid4().hex[:12]
            wf_type = workflow_type or self.config.default_workflow
            state = RunState(
                run_id=final_run_id,
                feature_request=feature_request,
                workspace_dir=str(self.manager.run_workspace(final_run_id)) if self.config.workspace_root else str(workspace),
                max_review_cycles=self.config.max_review_cycles,
                workflow_type=wf_type,
            )

        # Resolve the final execution workspace (isolated run dir vs legacy project dir)
        if self.config.workspace_root:
            workspace = self.manager.run_workspace(state.run_id)
            self.manager.ensure_run_dirs(state.run_id)
            self.manager.update_latest_symlink(state.run_id)
        else:
            workspace = Path(state.workspace_dir)
            (workspace / "artifacts").mkdir(exist_ok=True)
            (workspace / "logs").mkdir(exist_ok=True)

        # Detect config changes between runs
        current_hash = hashlib.md5(
            json.dumps(self.config.model_dump(), sort_keys=True, default=str).encode()
        ).hexdigest()
        if state.config_hash and state.config_hash != current_hash:
            logger.warning("Config has changed since original run — some settings may behave differently")
        state.config_hash = current_hash

        self.run_logger = RunLogger(workspace / "logs", state.run_id)

        # Register in global registry so the mobile API can discover this run
        # regardless of whether it was started from CLI, IDE, or mobile app.
        from orchestrator.run_registry import register_run
        state_path = workspace / f"state-{state.run_id}.json"
        register_run(
            run_id=state.run_id,
            project_dir=self.project_root,
            jsonl_path=self.run_logger._log_path,
            state_path=state_path,
            feature_request=feature_request,
            workflow_type=state.workflow_type.value if hasattr(state.workflow_type, "value") else str(state.workflow_type),
            source="cli",
        )

        # Bootstrap AICoder knowledge (if enabled)
        if self.config.knowledge.enabled:
            knowledge_result = await build_knowledge(
                project_root=self.project_root,
                aicoder_path=self.config.knowledge.aicoder_path,
                timeout_seconds=self.config.knowledge.build_timeout_seconds,
                skip_if_fresh_minutes=self.config.knowledge.skip_if_fresh_minutes,
                richness=self.config.knowledge.richness,
                skip_vectors=self.config.knowledge.skip_vectors,
                skip_features=self.config.knowledge.skip_features,
            )
            if knowledge_result.success:
                brief = ""
                if self.config.knowledge.inject_brief:
                    brief = synthesize_brief(
                        knowledge_result.knowledge_root,
                        max_files=self.config.knowledge.brief_max_files,
                        max_symbols=self.config.knowledge.brief_max_symbols,
                    )

                mcp_configured = False
                mcp_server_config = None
                if self.config.knowledge.mcp_tools:
                    mcp_configured = ensure_mcp_config(
                        aicoder_path=self.config.knowledge.aicoder_path,
                        target_project=self.project_root,
                        project_root=self.project_root,
                    )
                    if mcp_configured:
                        mcp_server_config = get_mcp_server_config(
                            aicoder_path=self.config.knowledge.aicoder_path,
                            project_root=self.project_root,
                        )

                self.config.knowledge_context = KnowledgeContext(
                    brief=brief,
                    knowledge_root=str(knowledge_result.knowledge_root),
                    mcp_configured=mcp_configured,
                    mcp_server_config=mcp_server_config,
                    build_time_ms=knowledge_result.build_time_ms,
                    file_count=knowledge_result.file_count,
                )
                logger.info(
                    f"Knowledge ready: {knowledge_result.file_count} files indexed, "
                    f"brief={len(brief)} chars, MCP={'yes' if mcp_configured else 'no'}"
                )
            else:
                logger.warning(
                    f"Knowledge build failed: {knowledge_result.error}. "
                    "Agents will explore codebase manually."
                )

        # Configure test-runner MCP server (structured test execution for QA agents)
        if self.config.test_runner.enabled:
            tr_config = get_test_runner_mcp_config(
                server_path=self.config.test_runner.server_path,
                project_root=self.project_root,
            )
            if tr_config:
                self._test_runner_mcp_config = tr_config
                ensure_test_runner_mcp_config(
                    server_path=self.config.test_runner.server_path,
                    target_project=self.project_root,
                    project_root=self.project_root,
                )
                logger.info("Test-runner MCP server configured")
            else:
                logger.info("Test-runner MCP server not found — QA agents will run tests via Bash")

        # Configure research cache MCP server (two-tier persistent research cache)
        if self.config.research_cache.enabled:
            rc_config = get_research_mcp_config(
                server_path=self.config.research_cache.server_path,
                project_root=self.project_root,
            )
            if rc_config is not None:
                self._research_mcp_config = rc_config
                self.config.research_cache_context = ResearchCacheContext(
                    cache_loaded=True,
                    mcp_configured=True,
                    mcp_server_config=rc_config,
                    global_entry_count=0,
                    local_entry_count=0,
                    findings=[],
                )
                logger.info("Research cache MCP server configured")
            else:
                self.config.research_cache_context = ResearchCacheContext(
                    cache_loaded=False,
                    mcp_configured=False,
                    mcp_server_config=None,
                    global_entry_count=0,
                    local_entry_count=0,
                    findings=[],
                )
                logger.warning(
                    "Research cache MCP server not found — agents will perform fresh research each run"
                )

        # Initialize monitoring stack (opt-in)
        self._monitoring = None
        monitoring_raw = self.config.monitoring
        if monitoring_raw and any(monitoring_raw.get(k) for k in ("metrics_enabled", "tracing_enabled", "webhooks")):
            try:
                from orchestrator.monitoring import MonitoringStack
                from orchestrator.monitoring.config import MonitoringConfig
                mon_config = MonitoringConfig(**monitoring_raw)
                self._monitoring = MonitoringStack(
                    config=mon_config,
                    run_id=state.run_id,
                    workspace=workspace,
                    max_budget_usd=self.config.max_budget_usd,
                )
                self.run_logger.set_monitoring_stack(self._monitoring)
            except Exception as e:
                logger.warning(f"Monitoring stack initialization failed: {e}")

        # Redact sensitive fields from config before logging
        config_data = self.config.model_dump()
        _redact_sensitive(config_data)

        self.run_logger.log_event("run_start", {
            "feature_request": feature_request,
            "config": config_data,
            "resume": resume,
            "workflow_type": state.workflow_type.value,
        })

        from orchestrator.models import RunStatus

        # Optional debate phase — runs before the main pipeline
        if self.dry_run:
            logger.info("Dry run enabled — planning only, no agents will be invoked.")
            state.status = RunStatus.COMPLETED
            # In legacy mode with single_phase, only that phase completes
            if not workflow_type and single_phase:
                state.phases[single_phase] = PhaseState(status=PhaseStatus.COMPLETED)
            else:
                # Mock at least one completed step for dry-run tests
                state.completed_steps.append("Dry Run Planning")
            
            self._save_state(state, workspace)
            return state

        if self.config.debate.enabled:
            debate_completed = (
                resume
                and state.phases.get("debate")
                and state.phases["debate"].status == PhaseStatus.COMPLETED
            )
            if debate_completed:
                # Load enriched feature request from debate conclusion
                conclusion_path = workspace / "artifacts" / "debate_conclusion.json"
                if conclusion_path.exists():
                    from orchestrator.debate import DebateEngine
                    from orchestrator.models import DebateConclusion
                    with open(conclusion_path) as f:
                        conclusion = DebateConclusion.model_validate(json.load(f))
                    de = DebateEngine(self.config, workspace, self.project_root)
                    feature_request = de.enrich_feature_request(feature_request, conclusion)
                    logger.info("Loaded debate conclusions from previous run")
            else:
                feature_request = await self._run_debate_phase(
                    state, workspace, feature_request,
                )

        # Persist custom workflow definition in state for resume
        if custom_workflow:
            state.custom_workflow_definition = custom_workflow
        elif state.workflow_type == WorkflowType.CUSTOM and state.custom_workflow_definition:
            custom_workflow = state.custom_workflow_definition

        # Decide execution mode
        use_legacy = single_phase in PHASE_ORDER or from_phase in PHASE_ORDER

        if use_legacy:
            await self._run_legacy(state, workspace, feature_request, single_phase, from_phase, resume)
        else:
            await self._run_workflow(state, workspace, feature_request, custom_workflow)

        self.run_logger.log_event("run_complete", {
            "total_cost_usd": state.total_cost_usd,
            "workflow_type": state.workflow_type.value,
            "phases": {k: v.model_dump() for k, v in state.phases.items()},
        })

        if self._monitoring:
            self._monitoring.shutdown()

        # Clean up MCP config if we added it
        if (
            self.config.knowledge.enabled
            and self.config.knowledge.cleanup_mcp_config
            and self.config.knowledge_context
            and self.config.knowledge_context.mcp_configured
        ):
            cleanup_mcp_config(self.project_root)
        if (
            self.config.test_runner.enabled
            and self.config.test_runner.cleanup_mcp_config
            and self._test_runner_mcp_config
        ):
            cleanup_test_runner_mcp_config(self.project_root)

        # Print end-of-run research recommendations if any findings were flagged
        rc = self.config.research_cache_context
        if rc and rc.findings:
            findings_objs = [
                Finding(**f) if isinstance(f, dict) else f
                for f in rc.findings
            ]
            print(format_recommendations([f.model_dump() for f in findings_objs]))

        # Clean up research-cache MCP config from project
        if (
            self.config.research_cache.enabled
            and self.config.research_cache.cleanup_mcp_config
            and self.config.research_cache_context
        ):
            cleanup_research_mcp_config(self.project_root)

        self._save_state(state, workspace)

        # Update registry with final status
        from orchestrator.run_registry import update_run
        update_run(state.run_id, status="completed", total_cost_usd=state.total_cost_usd)

        return state

    async def _run_workflow(
        self,
        state: RunState,
        workspace: Path,
        feature_request: str,
        custom_workflow: str | None = None,
    ) -> None:
        """Execute using the new workflow engine."""
        if custom_workflow:
            workflow = parse_custom_workflow(custom_workflow)
        else:
            workflow = select_workflow(state.workflow_type)

        engine = WorkflowEngine(
            workflow=workflow,
            state=state,
            config=self.config,
            run_logger=self.run_logger,
            project_root=self.project_root,
            dry_run=self.dry_run,
            interrupt_manager=self.interrupt_manager,
            confirm_callback=self.confirm_callback,
        )

        await engine.execute()

    async def _run_debate_phase(
        self,
        state: RunState,
        workspace: Path,
        feature_request: str,
    ) -> str:
        """Run the optional debate phase and return enriched feature request."""
        from orchestrator.debate import DebateEngine

        logger.info("=== Debate Phase: Starting adversarial debate ===")
        state.phases["debate"] = PhaseState(status=PhaseStatus.RUNNING)

        debate_engine = DebateEngine(
            config=self.config,
            workspace_dir=workspace,
            project_root=self.project_root,
            run_logger=self.run_logger,
            dry_run=self.dry_run,
            mcp_servers=self._mcp_servers,
        )

        try:
            conclusion = await debate_engine.run_debate(
                feature_request=feature_request,
            )
            enriched = debate_engine.enrich_feature_request(feature_request, conclusion)
            state.phases["debate"].status = PhaseStatus.COMPLETED

            # Load the debate state cost into the phase
            debate_state_path = workspace / "debate_state.json"
            if debate_state_path.exists():
                import json as _json
                ds = _json.loads(debate_state_path.read_text())
                debate_cost = ds.get("total_cost_usd", 0.0)
                state.phases["debate"].cost_usd = debate_cost
                state.total_cost_usd += debate_cost

            self._save_state(state, workspace)
            logger.info(f"Debate concluded with confidence {conclusion.overall_confidence}%")
            return enriched

        except Exception as e:
            logger.error(f"Debate phase failed: {e}")
            state.phases["debate"].status = PhaseStatus.FAILED
            state.phases["debate"].error = str(e)
            self._save_state(state, workspace)
            # Fall through with original feature request if debate fails
            return feature_request

    async def _run_legacy(
        self,
        state: RunState,
        workspace: Path,
        feature_request: str,
        single_phase: str | None,
        from_phase: str | None,
        resume: bool,
    ) -> None:
        """Execute using the legacy phase-based pipeline (backward compat)."""
        if single_phase:
            phases = [single_phase]
        elif from_phase:
            if from_phase not in PHASE_ORDER:
                raise ValueError(f"Unknown phase: {from_phase}. Choose from: {PHASE_ORDER}")
            phases = PHASE_ORDER[PHASE_ORDER.index(from_phase):]
        else:
            phases = PHASE_ORDER

        for phase_name in phases:
            if phase_name not in PHASE_DEFINITIONS:
                logger.error(f"Unknown phase: {phase_name}")
                continue

            if resume and state.phases.get(phase_name, PhaseState()).status == PhaseStatus.COMPLETED:
                logger.info(f"Skipping {phase_name} (already completed)")
                continue

            # Interrupt checkpoint: between legacy phases
            if self.interrupt_manager and self.interrupt_manager.should_interrupt():
                from orchestrator.interruption import handle_interruption
                state.current_step = phase_name
                result = await handle_interruption(
                    state=state,
                    config=self.config,
                    interrupt_manager=self.interrupt_manager,
                    run_logger=self.run_logger,
                    project_root=self.project_root,
                    context="between_phases",
                )
                if result == "abort":
                    break

            state.phases[phase_name] = PhaseState()
            await self._run_phase(phase_name, state, workspace, feature_request)

            # Tech stack confirmation: pause after architect phase so the
            # user can review proposed tech decisions before implementation.
            if (
                phase_name == "architect"
                and state.phases[phase_name].status == PhaseStatus.COMPLETED
                and self.config.tech_stack_confirmation
            ):
                # Try file-based prompt IPC first (for mobile API-triggered runs)
                try:
                    from orchestrator.prompt_manager import (
                        cleanup_prompt_files,
                        poll_for_response,
                        write_prompt,
                    )

                    prompt_id = write_prompt(
                        run_id=state.run_id,
                        workspace=workspace,
                        question="Confirm tech stack? Review the architecture decisions before proceeding to implementation.",
                        prompt_type="single_choice",
                        options=["Confirm", "Abort"],
                    )
                    response = poll_for_response(
                        run_id=state.run_id,
                        workspace=workspace,
                        prompt_id=prompt_id,
                        timeout_seconds=600,
                    )
                    cleanup_prompt_files(state.run_id, workspace)

                    if response is None or response.lower() == "abort":
                        logger.info(
                            "User %s at tech stack confirmation for run %s",
                            "timed out" if response is None else "aborted",
                            state.run_id,
                        )
                        state.status = "cancelled"
                        self._save_state(state, workspace)
                        break
                except Exception:
                    # Fall back to CLI-based confirmation if prompt IPC fails
                    pass

                try:
                    from orchestrator.tech_stack import confirm_tech_stack
                    confirm_tech_stack(workspace, dry_run=self.dry_run)
                except KeyboardInterrupt:
                    logger.info("User aborted at tech stack confirmation")
                    self._save_state(state, workspace)
                    break

            # Update cumulative context after successful phase
            if (
                state.phases[phase_name].status == PhaseStatus.COMPLETED
                and self.config.knowledge.enabled
                and self.config.knowledge.cumulative_context
            ):
                update_cumulative_context(workspace, map_phase_for_mcp(phase_name))

            # Re-index knowledge after code-modifying phases
            if state.phases[phase_name].status == PhaseStatus.COMPLETED:
                await self._refresh_knowledge(phase_name)

            # Post-phase research extraction (populate research cache from artifacts)
            if (
                state.phases[phase_name].status == PhaseStatus.COMPLETED
                and self.config.research_cache.enabled
                and self.config.research_cache.auto_extract
                and self.config.research_cache_context
                and self.config.research_cache_context.mcp_configured
            ):
                artifacts_dir = workspace / "artifacts"
                # Derive artifact path from phase name
                phase_to_artifact = {
                    "architect": "architecture",
                    "principal_engineer": "engineering_plan",
                }
                artifact_stem = phase_to_artifact.get(phase_name, phase_name)
                artifact_path = artifacts_dir / f"{artifact_stem}.json"
                if artifact_path.exists():
                    from orchestrator.models import ResearchCache
                    global_dir = Path(self.config.research_cache.global_dir).expanduser()
                    local_dir = self.project_root / self.config.research_cache.local_dir
                    rc_cache = ResearchCache()
                    extracted = extract_research_from_artifact(artifact_path, phase_name, state.run_id)
                    for entry in extracted:
                        try:
                            save_entry(
                                rc_cache,
                                entry,
                                global_dir=global_dir,
                                local_dir=local_dir,
                                max_entries=self.config.research_cache.max_entries,
                            )
                        except Exception as e:
                            logger.debug(f"Failed to save research entry: {e}")

            self._save_state(state, workspace)

            if state.phases[phase_name].status == PhaseStatus.FAILED:
                logger.error(f"Phase {phase_name} failed, stopping pipeline")
                break

            if phase_name == "reviewer" and not single_phase:
                await self._handle_review_cycle(state, workspace, feature_request)

    async def _run_phase(
        self,
        phase_name: str,
        state: RunState,
        workspace: Path,
        feature_request: str,
    ) -> None:
        """Run a single phase of the pipeline."""
        phase_def = PHASE_DEFINITIONS[phase_name]
        phase_config = self.config.phases.get(phase_name)
        agent_config = self.config.agents.get(phase_def.agent_name)
        phase_state = state.phases[phase_name]

        phase_state.status = PhaseStatus.RUNNING
        if agent_config:
            phase_state.model_tier = agent_config.model

        logger.info(f"=== Phase: {phase_name} (model: {phase_state.model_tier.value}) ===")

        if phase_name == "engineer":
            await self._run_engineer_phase(state, workspace, feature_request)
            return

        prompt = phase_def.build_prompt(feature_request, workspace, self.config)

        if self.dry_run:
            logger.info(f"[DRY RUN] Would invoke {phase_def.agent_name} with prompt:\n{prompt[:500]}...")
            phase_state.status = PhaseStatus.COMPLETED
            return

        max_retries = phase_config.max_retries if phase_config else 2
        result = await self._invoke_with_retry(
            agent_name=phase_def.agent_name,
            prompt=prompt,
            model=phase_state.model_tier,
            max_turns=agent_config.max_turns if agent_config else 40,
            max_retries=max_retries,
            escalation_model=agent_config.escalation_model if agent_config else None,
            workspace=workspace,
        )

        # Run dynamic spawn loop for eligible phases
        if result.success and self.config.spawn.enabled:
            result = await self._run_spawn_loop_for_phase(
                phase_name, state, result,
                agent_name=phase_def.agent_name,
                model=phase_state.model_tier,
                max_turns=agent_config.max_turns if agent_config else 40,
                workspace=workspace,
            )

        phase_state.cost_usd = result.cost_usd
        state.total_cost_usd += result.cost_usd
        state.total_input_tokens += result.input_tokens
        state.total_output_tokens += result.output_tokens

        if not result.success:
            phase_state.status = PhaseStatus.FAILED
            phase_state.error = result.error
            return

        # Artifact rescue & validation retry loop.
        max_art_retries = 2
        for artifact_attempt in range(max_art_retries + 1):
            artifact_errors = self._validate_phase_artifacts(phase_def, workspace)

            if artifact_errors:
                # Layer 1: Search for misplaced files (now with fuzzy matching)
                missing_names = list(artifact_errors.keys())
                _rescue_misplaced_artifacts(missing_names, workspace, self.project_root)
                artifact_errors = self._validate_phase_artifacts(phase_def, workspace)

            if artifact_errors and result.output:
                # Layer 2: Extract artifact JSON from agent output text
                missing_names = list(artifact_errors.keys())
                extracted = _rescue_artifacts_from_output(result.output, missing_names, workspace)
                if extracted:
                    logger.info(f"Auto-rescued {len(extracted)} artifact(s) from agent output: {extracted}")
                artifact_errors = self._validate_phase_artifacts(phase_def, workspace)

            if artifact_errors:
                # Layer 3: Artifact writer retry with schema injection
                missing_names = list(artifact_errors.keys())
                missing_files = ", ".join(missing_names)
                logger.warning(
                    f"Artifact(s) not found after {phase_name} (attempt {artifact_attempt + 1}) — "
                    f"running artifact-writer retry: {missing_files}"
                )
                prior_output_section = ""
                if result.output:
                    prior = result.output[:16000] if len(result.output) > 16000 else result.output
                    prior_output_section = (
                        f"\n\n## Previous Agent Output (extract artifact from this)\n\n{prior}\n"
                    )
                artifacts_dir = workspace / "artifacts"

                # Inject schema for each missing artifact
                schema_sections = ""
                from orchestrator.models import ARTIFACT_MODELS
                for a_name in missing_names:
                    model_cls = ARTIFACT_MODELS.get(a_name)
                    if model_cls:
                        try:
                            schema_json = json.dumps(model_cls.model_json_schema(), indent=2)
                            if len(schema_json) <= 4000:
                                schema_sections += (
                                    f"\n### Schema for {a_name}.json\n"
                                    f"```json\n{schema_json}\n```\n"
                                )
                        except Exception:
                            pass

                file_list = "\n".join(f"  {artifacts_dir}/{a}.json" for a in missing_names)
                retry_prompt = (
                    f"ARTIFACT WRITER TASK — your ONLY job is to create these files:\n\n"
                    f"{file_list}\n\n"
                    f"Use the Write tool to create each file at exactly the path above.\n"
                    f"Each file must contain valid JSON. Use snake_case for all JSON keys.\n"
                    f"Do NOT output JSON in your response — you MUST call the Write tool.\n"
                    f"After writing, use Read to verify.\n"
                    f"{schema_sections}"
                    f"\nOriginal task: {prompt[:2000]}\n"
                    f"{prior_output_section}"
                )
                # Escalate model on second+ attempt
                retry_model = phase_state.model_tier
                if artifact_attempt > 0 and agent_config and agent_config.escalation_model:
                    retry_model = agent_config.escalation_model

                retry_result = await self._invoke_with_retry(
                    agent_name=phase_def.agent_name,
                    prompt=retry_prompt,
                    model=retry_model,
                    max_turns=10,
                    max_retries=0,
                    escalation_model=agent_config.escalation_model if agent_config else None,
                    workspace=workspace,
                )
                phase_state.cost_usd += retry_result.cost_usd
                state.total_cost_usd += retry_result.cost_usd
                state.total_input_tokens += retry_result.input_tokens
                state.total_output_tokens += retry_result.output_tokens
                artifact_errors = self._validate_phase_artifacts(phase_def, workspace)

            if not artifact_errors:
                break  # All artifacts valid

            if artifact_attempt >= max_art_retries:
                break  # Exhausted retries

        if artifact_errors:
            for artifact_name in artifact_errors:
                artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
                invalid_path = artifact_path.with_suffix(".invalid.json")
                if artifact_path.exists():
                    artifact_path.rename(invalid_path)
                    logger.warning(f"Invalid artifact moved to {invalid_path}")
            phase_state.status = PhaseStatus.FAILED
            phase_state.error = f"Artifact validation: {list(artifact_errors.values())}"
            return

        phase_state.status = PhaseStatus.COMPLETED

    def _validate_phase_artifacts(
        self, phase_def, workspace: Path,
    ) -> dict[str, list[str]]:
        """Validate all output artifacts for a phase. Returns {name: errors} for failures."""
        errors: dict[str, list[str]] = {}
        for artifact_name in phase_def.output_artifacts:
            artifact_path = workspace / "artifacts" / f"{artifact_name}.json"
            validation = validate_artifact_file(artifact_path, artifact_name)
            if not validation.valid:
                logger.warning(f"Artifact {artifact_name} validation failed: {validation.errors}")
                errors[artifact_name] = validation.errors
        return errors

    async def _run_engineer_phase(
        self,
        state: RunState,
        workspace: Path,
        feature_request: str,
    ) -> None:
        """Run the engineer phase — potentially parallel tasks."""
        phase_state = state.phases["engineer"]
        agent_config = self.config.agents.get("engineer")
        tasks = get_engineer_tasks(workspace)

        if not tasks:
            logger.warning("No engineer tasks found, skipping engineer phase")
            phase_state.status = PhaseStatus.COMPLETED
            return

        # Recover existing task states on resume instead of creating fresh ones.
        # This preserves COMPLETED status so already-done tasks are skipped.
        existing_by_id = {t.task_id: t for t in state.engineering_tasks}
        new_tasks: list[EngTaskState] = []
        for t in tasks:
            tid = t["task_id"]
            if tid in existing_by_id and existing_by_id[tid].status == TaskStatus.COMPLETED:
                new_tasks.append(existing_by_id[tid])
                logger.info(f"Skipping task {tid} (already completed in prior run)")
            else:
                new_tasks.append(EngTaskState(task_id=tid))
        state.engineering_tasks = new_tasks

        if self.dry_run:
            for task in tasks:
                prompt = build_engineer_prompt(feature_request, workspace, self.config, task)
                logger.info(f"[DRY RUN] Would invoke engineer for {task['task_id']}:\n{prompt[:300]}...")
            phase_state.status = PhaseStatus.COMPLETED
            return

        parallel_ok = self.config.phases.get("engineer", None)
        use_parallel = parallel_ok and parallel_ok.parallel and not _tasks_have_file_conflicts(tasks)

        if use_parallel:
            await self._run_engineers_parallel(state, workspace, feature_request, tasks)
        else:
            await self._run_engineers_sequential(state, workspace, feature_request, tasks)

        failed = [t for t in state.engineering_tasks if t.status == TaskStatus.FAILED]
        if failed:
            phase_state.status = PhaseStatus.FAILED
            phase_state.error = f"Tasks failed: {[t.task_id for t in failed]}"
        else:
            phase_state.status = PhaseStatus.COMPLETED

    async def _run_engineers_parallel(
        self,
        state: RunState,
        workspace: Path,
        feature_request: str,
        tasks: list[dict[str, Any]],
    ) -> None:
        """Run engineer tasks in parallel with isolated worktrees."""
        # Filter out already-completed tasks (resume support)
        pending_indices: list[int] = []
        pending_tasks: list[dict[str, Any]] = []
        for i, task in enumerate(tasks):
            if state.engineering_tasks[i].status == TaskStatus.COMPLETED:
                logger.info(f"Skipping task {task['task_id']} in parallel batch (already completed)")
                continue
            pending_indices.append(i)
            pending_tasks.append(task)

        if not pending_tasks:
            return

        invocations = []
        for task in pending_tasks:
            prompt = build_engineer_prompt(feature_request, workspace, self.config, task)
            agent_config = self.config.agents.get("engineer")
            invocations.append(AgentInvocation(
                agent_name="engineer",
                prompt=prompt,
                model=agent_config.model if agent_config else ModelTier.SONNET,
                max_turns=agent_config.max_turns if agent_config else 80,
                workspace_dir=str(workspace),
                project_root=str(self.project_root),
                isolation="worktree",

                mcp_servers=self._mcp_servers,
            ))

        if self.confirm_callback:
            confirmed_invocations = []
            for inv in invocations:
                confirmed = self.confirm_callback(inv)
                if confirmed is None:
                    raise KeyboardInterrupt("User aborted at confirmation")
                confirmed_invocations.append(confirmed)
            invocations = confirmed_invocations

        results = await invoke_agents_parallel(
            invocations, max_concurrent=self.config.max_concurrent_agents,
        )

        failed_tasks: list[tuple[int, dict[str, Any]]] = []
        for orig_idx, task, result in zip(pending_indices, pending_tasks, results):
            task_state = state.engineering_tasks[orig_idx]
            task_state.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
            state.total_cost_usd += result.cost_usd
            state.total_input_tokens += result.input_tokens
            state.total_output_tokens += result.output_tokens
            if not result.success:
                failed_tasks.append((orig_idx, task))

        # Retry failed tasks once sequentially
        if failed_tasks:
            agent_config = self.config.agents.get("engineer")
            max_retries = self.config.phases.get("engineer", None).max_retries if self.config.phases.get("engineer") else 1
            if max_retries > 0:
                logger.info(f"Retrying {len(failed_tasks)} failed task(s) from parallel batch")
                for idx, task in failed_tasks:
                    task_state = state.engineering_tasks[idx]
                    prompt = build_engineer_prompt(feature_request, workspace, self.config, task)
                    result = await self._invoke_with_retry(
                        agent_name="engineer",
                        prompt=prompt,
                        model=agent_config.model if agent_config else ModelTier.SONNET,
                        max_turns=agent_config.max_turns if agent_config else 80,
                        max_retries=1,
                        escalation_model=agent_config.escalation_model if agent_config else None,
                        workspace=workspace,
                    )
                    task_state.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
                    state.total_cost_usd += result.cost_usd
                    state.total_input_tokens += result.input_tokens
                    state.total_output_tokens += result.output_tokens

    async def _run_engineers_sequential(
        self,
        state: RunState,
        workspace: Path,
        feature_request: str,
        tasks: list[dict[str, Any]],
    ) -> None:
        """Run engineer tasks sequentially."""
        agent_config = self.config.agents.get("engineer")
        for i, task in enumerate(tasks):
            task_state = state.engineering_tasks[i]
            if task_state.status == TaskStatus.COMPLETED:
                continue
            task_state.status = TaskStatus.IN_PROGRESS

            prompt = build_engineer_prompt(feature_request, workspace, self.config, task)
            result = await self._invoke_with_retry(
                agent_name="engineer",
                prompt=prompt,
                model=agent_config.model if agent_config else ModelTier.SONNET,
                max_turns=agent_config.max_turns if agent_config else 80,
                max_retries=self.config.phases.get("engineer", None).max_retries if self.config.phases.get("engineer") else 2,
                escalation_model=agent_config.escalation_model if agent_config else None,
                workspace=workspace,
            )

            task_state.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
            state.total_cost_usd += result.cost_usd
            state.total_input_tokens += result.input_tokens
            state.total_output_tokens += result.output_tokens

            if not result.success:
                logger.error(f"Task {task['task_id']} failed: {result.error}")

    async def _handle_review_cycle(
        self,
        state: RunState,
        workspace: Path,
        feature_request: str,
    ) -> None:
        """Handle review feedback loop: reviewer -> engineer -> qa -> reviewer."""
        review_path = workspace / "artifacts" / "review.json"

        while state.review_cycles < state.max_review_cycles:
            if not review_path.exists():
                break

            with open(review_path) as f:
                review_data = json.load(f)

            verdict = normalize_verdict(review_data.get("verdict", "approve"))
            if verdict == ReviewVerdict.APPROVE.value:
                logger.info("Review approved!")
                break

            state.review_cycles += 1
            logger.info(f"Review cycle {state.review_cycles}/{state.max_review_cycles}: {verdict}")

            if state.review_cycles >= state.max_review_cycles:
                logger.warning("Max review cycles reached, escalating to human")
                break

            state.phases["engineer"] = PhaseState()
            engineer_prompt = build_engineer_prompt(
                feature_request, workspace, self.config
            ) + f"\n\n## Review Feedback\n\n```json\n{json.dumps(review_data, indent=2)}\n```\n\nAddress the issues raised in the review."

            if not self.dry_run:
                agent_config = self.config.agents.get("engineer")
                result = await self._invoke_with_retry(
                    agent_name="engineer",
                    prompt=engineer_prompt,
                    model=agent_config.model if agent_config else ModelTier.SONNET,
                    max_turns=agent_config.max_turns if agent_config else 80,
                    max_retries=2,
                    escalation_model=agent_config.escalation_model if agent_config else None,
                    workspace=workspace,
                )
                state.total_cost_usd += result.cost_usd
                state.total_input_tokens += result.input_tokens
                state.total_output_tokens += result.output_tokens
                if not result.success:
                    state.phases["engineer"].status = PhaseStatus.FAILED
                    break

            state.phases["qa"] = PhaseState()
            await self._run_phase("qa", state, workspace, feature_request)
            if state.phases["qa"].status == PhaseStatus.FAILED:
                break

            state.phases["reviewer"] = PhaseState()
            reviewer_prompt = build_reviewer_prompt(
                feature_request, workspace, self.config,
                review_cycle=state.review_cycles + 1,
                previous_review=review_data,
            )

            if not self.dry_run:
                agent_config = self.config.agents.get("reviewer")
                result = await self._invoke_with_retry(
                    agent_name="reviewer",
                    prompt=reviewer_prompt,
                    model=agent_config.model if agent_config else ModelTier.OPUS,
                    max_turns=agent_config.max_turns if agent_config else 30,
                    max_retries=1,
                    escalation_model=None,
                    workspace=workspace,
                )
                state.total_cost_usd += result.cost_usd
                state.total_input_tokens += result.input_tokens
                state.total_output_tokens += result.output_tokens

    async def _run_spawn_loop_for_phase(
        self,
        phase_name: str,
        state: RunState,
        result: AgentResult,
        agent_name: str,
        model: ModelTier,
        max_turns: int,
        workspace: Path,
    ) -> AgentResult:
        """Run the dynamic spawn loop for a legacy phase."""
        from orchestrator.spawning import run_spawn_loop

        # Map legacy phase/agent names to role strings used in spawn permissions
        _LEGACY_TO_ROLE = {
            "pm": "product_manager",
            "architect": "software_architect",
            "principal_engineer": "principal_engineer",
            "tpm": "technical_project_manager",
            "qa_planner": "qa_planner",
        }
        parent_role = _LEGACY_TO_ROLE.get(agent_name, phase_name)

        final_result, rounds = await run_spawn_loop(
            initial_result=result,
            agent_name=agent_name,
            parent_role=parent_role,
            original_prompt="",
            config=self.config.spawn,
            model=model,
            max_turns=max_turns,
            workspace_dir=str(workspace),
            project_root=str(self.project_root),
            max_concurrent=self.config.max_concurrent_agents,
            agents_config=self.config.agents,
            run_logger=self.run_logger,
        )

        # Record spawn history in run state
        for round_summary in rounds:
            for req, sr in zip(round_summary.requests, round_summary.results):
                state.spawn_history.append(SpawnRecord(
                    parent_step=phase_name,
                    parent_role=parent_role,
                    round_number=round_summary.round_number,
                    spawned_role=sr.role,
                    reason=sr.reason,
                    success=sr.success,
                    cost_usd=sr.cost_usd,
                ))

        return final_result

    async def _invoke_with_retry(
        self,
        agent_name: str,
        prompt: str,
        model: ModelTier,
        max_turns: int,
        max_retries: int,
        escalation_model: ModelTier | None,
        workspace: Path,
    ) -> AgentResult:
        """Invoke an agent with retry and model escalation."""
        current_model = model

        for attempt in range(max_retries + 1):
            if self.run_logger:
                self.run_logger.log_event("agent_invoke", {
                    "agent": agent_name,
                    "model": current_model.value,
                    "attempt": attempt + 1,
                })

            if self.config.max_budget_usd and self.run_logger:
                status = self.run_logger.check_budget(self.config.max_budget_usd)
                if status == "exceeded":
                    return AgentResult(
                        success=False,
                        error=f"Budget exceeded: ${self.run_logger.cumulative_cost_usd:.2f} >= ${self.config.max_budget_usd:.2f}",
                    )
                if status == "warning":
                    self.run_logger.log_event("budget_warning", {
                        "cumulative_cost_usd": self.run_logger.cumulative_cost_usd,
                        "max_budget_usd": self.config.max_budget_usd,
                    })

            invocation = AgentInvocation(
                agent_name=agent_name,
                prompt=prompt,
                model=current_model,
                max_turns=max_turns,
                workspace_dir=str(workspace),
                project_root=str(self.project_root),

                mcp_servers=self._mcp_servers,
            )

            if self.confirm_callback:
                confirmed = self.confirm_callback(invocation)
                if confirmed is None:
                    raise KeyboardInterrupt("User aborted at confirmation")
                invocation = confirmed

            result = await invoke_agent(invocation)

            if self.run_logger:
                self.run_logger.log_event("agent_result", {
                    "agent": agent_name,
                    "success": result.success,
                    "cost_usd": result.cost_usd,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "attempt": attempt + 1,
                    "error_code": "MAX_RETRIES_EXHAUSTED" if not result.success and attempt == max_retries else None,
                })

            if result.success:
                return result

            is_infra_failure = result.error_code == "INFRA_ERROR"
            logger.warning(
                f"Agent {agent_name} failed (attempt {attempt + 1})"
                f"{' [infra]' if is_infra_failure else ''}: {result.error}"
            )

            # Check if the error is non-retriable before spending another attempt
            NON_RETRIABLE_PATTERNS = [
                "Budget exceeded",
                "Artifact validation failed",
                "Missing input artifact",
            ]
            if result.error and any(
                p.lower() in result.error.lower() for p in NON_RETRIABLE_PATTERNS
            ):
                logger.error(f"Non-retriable error for {agent_name}: {result.error}")
                return result

            if self.run_logger:
                self.run_logger.log_event("agent_retry", {"agent": agent_name, "attempt": attempt + 1})

            if is_infra_failure:
                # SDK/MCP crash — wait briefly to let MCP server recover
                logger.info(f"Infrastructure failure for {agent_name} — waiting 3s before retry")
                await asyncio.sleep(3)

            if escalation_model and current_model != escalation_model:
                logger.info(f"Escalating {agent_name} from {current_model.value} to {escalation_model.value}")
                if self.run_logger:
                    self.run_logger.log_event("model_escalation", {
                        "agent": agent_name,
                        "from_model": current_model.value,
                        "to_model": escalation_model.value,
                    })
                current_model = escalation_model

        return result

    async def _refresh_knowledge(self, phase_name: str) -> None:
        """Re-index knowledge base between phases so later agents see fresh data.

        Triggered after phases that modify code (engineer) or produce artifacts
        that change the codebase understanding. Skips if knowledge is disabled
        or the rebuild would be redundant (< skip_if_fresh_minutes).
        """
        if not self.config.knowledge.enabled:
            return

        # Phases after which re-indexing is valuable
        _REINDEX_AFTER = {"engineer", "implementation"}
        if phase_name.lower() not in _REINDEX_AFTER:
            return

        logger.info(f"Re-indexing knowledge base after {phase_name} phase...")
        result = await build_knowledge(
            project_root=self.project_root,
            aicoder_path=self.config.knowledge.aicoder_path,
            timeout_seconds=self.config.knowledge.build_timeout_seconds,
            skip_if_fresh_minutes=0,  # Force rebuild after code changes
            richness=self.config.knowledge.richness,
            skip_vectors=self.config.knowledge.skip_vectors,
            skip_features=self.config.knowledge.skip_features,
        )
        if result.success:
            # Update the knowledge context with fresh data
            if self.config.knowledge.inject_brief:
                brief = synthesize_brief(
                    result.knowledge_root,
                    max_files=self.config.knowledge.brief_max_files,
                    max_symbols=self.config.knowledge.brief_max_symbols,
                )
                if self.config.knowledge_context:
                    self.config.knowledge_context.brief = brief
                    self.config.knowledge_context.file_count = result.file_count
            logger.info(
                f"Knowledge re-indexed: {result.file_count} files in {result.build_time_ms:.0f}ms"
            )
        else:
            logger.warning(f"Knowledge re-index failed: {result.error}")

    def _save_state(self, state: RunState, workspace: Path) -> None:
        """Save the run state to disk.

        Writes both ``state.json`` (latest run, backward compat) and
        ``state-<run_id>.json`` (durable per-run copy for resume-by-id).
        """
        from orchestrator.persistence import save_run_state
        save_run_state(state, workspace)

    def _load_state(self, workspace: Path, run_id: str | None = None) -> RunState | None:
        """Load a prior run state from disk.

        If *run_id* is given, loads ``state-<run_id>.json``.
        Otherwise falls back to the latest ``state.json``.
        """
        if run_id:
            state_path = workspace / f"state-{run_id}.json"
            if not state_path.exists():
                # Fall back to state.json if it matches the requested run ID
                fallback = workspace / "state.json"
                if fallback.exists():
                    with open(fallback) as f:
                        data = json.load(f)
                    if data.get("run_id") == run_id:
                        return RunState.model_validate(data)
                return None
        else:
            state_path = workspace / "state.json"
            if not state_path.exists():
                return None
        with open(state_path) as f:
            data = json.load(f)
        return RunState.model_validate(data)


def _tasks_have_file_conflicts(tasks: list[dict[str, Any]]) -> bool:
    """Check if any tasks modify the same files."""
    seen_files: set[str] = set()
    for task in tasks:
        files = set(task.get("files_to_modify", []))
        if files & seen_files:
            return True
        seen_files |= files
    return False
