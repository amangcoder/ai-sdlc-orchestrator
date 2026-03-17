"""Dynamic agent spawning — allows agents to request sub-agents mid-execution.

Flow:
1. Agent runs and includes SPAWN_REQUESTS JSON block in its output
2. Engine detects spawn requests, validates them against spawn policy
3. Sub-agents execute in parallel (respecting max_concurrent)
4. Results are formatted and fed back to the original agent as a continuation prompt
5. Agent incorporates results and produces final output
6. Repeat up to max_spawn_rounds times
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from orchestrator.agents import AgentInvocation, AgentResult, invoke_agents_parallel
from orchestrator.models import AgentRole, ModelTier, SpawnConfig

logger = logging.getLogger(__name__)

# Regex to extract SPAWN_REQUESTS JSON block from agent output
_SPAWN_BLOCK_RE = re.compile(
    r"SPAWN_REQUESTS:\s*```json\s*(\[.*?\])\s*```",
    re.DOTALL,
)

# Fallback: look for raw JSON array after SPAWN_REQUESTS: marker
_SPAWN_BLOCK_FALLBACK_RE = re.compile(
    r"SPAWN_REQUESTS:\s*(\[.*?\])",
    re.DOTALL,
)

# Maps role strings to AgentRole enums (reuse from workflow_engine pattern)
_ROLE_STRING_TO_ENUM: dict[str, AgentRole] = {
    role.value: role for role in AgentRole
}


@dataclass
class SpawnRequest:
    """A single sub-agent spawn request from a parent agent."""
    role: str                    # target role string (e.g., "market_researcher")
    prompt: str                  # task prompt for the sub-agent
    reason: str = ""             # why the parent agent needs this
    model_override: str | None = None  # optional model override


@dataclass
class SpawnResult:
    """Result from a spawned sub-agent."""
    role: str
    reason: str
    success: bool
    output: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class SpawnRoundSummary:
    """Summary of one round of spawn execution."""
    round_number: int
    requests: list[SpawnRequest]
    results: list[SpawnResult]
    total_cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Spawn policy — which roles can spawn which sub-agents
# ---------------------------------------------------------------------------

# Default spawn permissions: parent_role -> set of allowed target roles
DEFAULT_SPAWN_PERMISSIONS: dict[str, set[str]] = {
    # PM can spawn research and user-facing specialists
    "product_manager": {
        "market_researcher", "competitor_researcher", "field_specialist",
        "end_user_simulator", "legal_advisor", "user_behavior_psychologist",
        "deep_researcher", "brainstormer",
    },
    # Architect can spawn technical specialists
    "software_architect": {
        "security_engineer", "llm_specialist", "agentic_ai_specialist",
        "ml_specialist", "database_engineer", "aws_specialist",
        "azure_specialist", "gcp_specialist", "runpod_specialist",
        "tech_debt_assessor", "field_specialist", "api_contract_designer",
        "compliance_auditor", "deep_researcher",
    },
    # Principal engineer can spawn deep technical specialists
    "principal_engineer": {
        "security_engineer", "llm_specialist", "agentic_ai_specialist",
        "ml_specialist", "database_engineer", "load_test_engineer",
        "tech_debt_assessor", "deep_researcher", "field_specialist",
        "dependency_auditor",
    },
    # TPM can spawn analysis roles
    "technical_project_manager": {
        "tech_debt_assessor", "deep_researcher", "field_specialist",
        "dependency_auditor",
    },
    # QA planner can spawn specialized test planners
    "qa_planner": {
        "security_engineer", "load_test_engineer", "accessibility_auditor",
        "integration_test_engineer",
    },
    # --- Implementation roles: engineers can spawn specialists for guidance ---
    "engineer": {
        "security_engineer", "database_engineer", "api_contract_designer",
        "caching_performance_engineer", "deep_researcher",
        "llm_specialist", "agentic_ai_specialist", "ml_specialist",
        "dependency_auditor",
    },
    "frontend_engineer": {
        "security_engineer", "accessibility_auditor", "ux_specifier",
        "deep_researcher", "api_contract_designer",
        "user_behavior_psychologist",
    },
    "backend_engineer": {
        "security_engineer", "database_engineer", "api_contract_designer",
        "caching_performance_engineer", "load_test_engineer",
        "deep_researcher", "llm_specialist", "dependency_auditor",
    },
    "database_engineer": {
        "security_engineer", "caching_performance_engineer",
        "load_test_engineer", "deep_researcher", "compliance_auditor",
    },
    "automation_engineer": {
        "security_engineer", "load_test_engineer", "accessibility_auditor",
        "integration_test_engineer", "deep_researcher",
    },
    "devops_engineer": {
        "security_engineer", "aws_specialist", "azure_specialist",
        "gcp_specialist", "runpod_specialist", "cicd_specialist",
        "observability_engineer", "deep_researcher",
    },
    "caching_performance_engineer": {
        "load_test_engineer", "database_engineer", "deep_researcher",
        "observability_engineer",
    },
    "observability_engineer": {
        "deep_researcher", "aws_specialist", "gcp_specialist",
        "azure_specialist", "load_test_engineer",
    },
    # --- QA execution: can spawn specialists for targeted testing ---
    "qa_executor": {
        "security_engineer", "load_test_engineer", "accessibility_auditor",
        "integration_test_engineer", "deep_researcher",
    },
    # --- Reviewers: can spawn specialists for deep analysis ---
    "backend_code_reviewer": {
        "security_engineer", "load_test_engineer", "database_engineer",
        "deep_researcher", "compliance_auditor", "dependency_auditor",
    },
    "frontend_code_reviewer": {
        "security_engineer", "accessibility_auditor", "ux_specifier",
        "deep_researcher", "user_behavior_psychologist",
    },
}


def validate_spawn_request(
    parent_role: str,
    request: SpawnRequest,
    config: SpawnConfig,
) -> str | None:
    """Validate a spawn request against the spawn policy.

    Returns None if valid, or an error message string if invalid.
    """
    if not config.enabled:
        return "Dynamic spawning is disabled"

    target_role = request.role
    if target_role not in _ROLE_STRING_TO_ENUM:
        return f"Unknown target role: {target_role}"

    # Check permissions
    allowed = DEFAULT_SPAWN_PERMISSIONS.get(parent_role, set())
    # Merge with any config-level overrides
    extra = set(config.extra_permissions.get(parent_role, []))
    allowed = allowed | extra

    if target_role not in allowed:
        return (
            f"Role '{parent_role}' is not permitted to spawn '{target_role}'. "
            f"Allowed: {sorted(allowed)}"
        )

    return None


# ---------------------------------------------------------------------------
# Parsing spawn requests from agent output
# ---------------------------------------------------------------------------

def extract_spawn_requests(output: str) -> list[SpawnRequest]:
    """Parse SPAWN_REQUESTS from agent output text.

    Expected format in agent output:
        SPAWN_REQUESTS:
        ```json
        [
          {
            "role": "market_researcher",
            "prompt": "Research the market for ...",
            "reason": "Need market sizing data"
          }
        ]
        ```
    """
    match = _SPAWN_BLOCK_RE.search(output)
    if not match:
        match = _SPAWN_BLOCK_FALLBACK_RE.search(output)
    if not match:
        return []

    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse SPAWN_REQUESTS JSON: {e}")
        return []

    if not isinstance(raw, list):
        logger.warning("SPAWN_REQUESTS must be a JSON array")
        return []

    requests = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "")
        prompt = item.get("prompt", "")
        if not role or not prompt:
            logger.warning(f"Skipping spawn request with missing role/prompt: {item}")
            continue
        requests.append(SpawnRequest(
            role=role,
            prompt=prompt,
            reason=item.get("reason", ""),
            model_override=item.get("model"),
        ))

    return requests


def strip_spawn_block(output: str) -> str:
    """Remove the SPAWN_REQUESTS block from agent output (for clean artifact writing)."""
    result = _SPAWN_BLOCK_RE.sub("", output)
    result = _SPAWN_BLOCK_FALLBACK_RE.sub("", result)
    return result.strip()


# ---------------------------------------------------------------------------
# Executing spawn requests
# ---------------------------------------------------------------------------

async def execute_spawn_requests(
    requests: list[SpawnRequest],
    parent_role: str,
    config: SpawnConfig,
    workspace_dir: str,
    project_root: str,
    max_concurrent: int = 10,
    agents_config: dict[str, Any] | None = None,
) -> list[SpawnResult]:
    """Execute a batch of spawn requests in parallel.

    Args:
        requests: Validated spawn requests to execute.
        parent_role: The role of the parent agent (for logging).
        config: Spawn configuration.
        workspace_dir: Workspace directory path.
        project_root: Project root path.
        max_concurrent: Max parallel agents.
        agents_config: Agent configurations dict for model/turn lookup.

    Returns:
        List of SpawnResult, one per request, in same order as requests.
    """
    from orchestrator.codenames import generate_codename
    from orchestrator.roles import role_to_legacy_agent_name

    if not requests:
        return []

    invocations: list[AgentInvocation] = []
    used_names: set[str] = set()

    for req in requests:
        agent_role = _ROLE_STRING_TO_ENUM[req.role]
        agent_name = role_to_legacy_agent_name(agent_role)

        # Determine model: request override > agent config > default sonnet
        model = ModelTier.SONNET
        max_turns = config.spawned_agent_max_turns

        if agents_config and agent_name in agents_config:
            ac = agents_config[agent_name]
            model = ac.model if hasattr(ac, "model") else ModelTier.SONNET
            max_turns = min(
                ac.max_turns if hasattr(ac, "max_turns") else max_turns,
                config.spawned_agent_max_turns,
            )

        if req.model_override:
            try:
                model = ModelTier(req.model_override)
            except ValueError:
                pass

        # Build the sub-agent prompt with context about why it was spawned
        spawn_prompt = (
            f"You have been dynamically spawned by the {parent_role} agent "
            f"to assist with a specific research/analysis task.\n\n"
            f"## Why you were spawned\n\n{req.reason}\n\n"
            f"## Your Task\n\n{req.prompt}\n\n"
            f"## Instructions\n\n"
            f"1. Focus exclusively on the task above\n"
            f"2. Be thorough but concise in your findings\n"
            f"3. Structure your output clearly so the parent agent can incorporate it\n"
            f"4. Do NOT spawn additional sub-agents\n"
            f"5. Write your findings as plain text (not JSON artifacts)\n"
        )

        codename = generate_codename(exclude=used_names)
        used_names.add(codename)

        invocations.append(AgentInvocation(
            agent_name=agent_name,
            prompt=spawn_prompt,
            model=model,
            max_turns=max_turns,
            workspace_dir=workspace_dir,
            project_root=project_root,
            display_name=f"{codename} (spawned:{req.role})",
        ))

    logger.info(
        f"Executing {len(invocations)} spawned agent(s) for {parent_role}:"
    )
    for inv, req in zip(invocations, requests):
        logger.info(f"  - {inv.display_name}: {req.reason[:80]}")

    results = await invoke_agents_parallel(invocations, max_concurrent=max_concurrent)

    spawn_results = []
    for req, result in zip(requests, results):
        spawn_results.append(SpawnResult(
            role=req.role,
            reason=req.reason,
            success=result.success,
            output=result.output if result.success else (result.error or "Agent failed"),
            cost_usd=result.cost_usd,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        ))

    return spawn_results


# ---------------------------------------------------------------------------
# Building continuation prompt with spawn results
# ---------------------------------------------------------------------------

def build_continuation_prompt(
    original_output: str,
    spawn_results: list[SpawnResult],
) -> str:
    """Build a continuation prompt that feeds spawn results back to the parent agent.

    The parent agent receives its own prior output plus all sub-agent results,
    and is asked to incorporate the findings into its final output.
    """
    results_section = []
    for i, sr in enumerate(spawn_results, 1):
        status = "SUCCESS" if sr.success else "FAILED"
        results_section.append(
            f"### Sub-Agent {i}: {sr.role} [{status}]\n"
            f"**Reason spawned:** {sr.reason}\n\n"
            f"{sr.output}\n"
        )

    results_text = "\n---\n".join(results_section)

    return (
        f"You previously produced output and requested {len(spawn_results)} "
        f"sub-agent(s) to be spawned. They have now completed their work. "
        f"Below are their results.\n\n"
        f"## Sub-Agent Results\n\n{results_text}\n\n"
        f"## Instructions\n\n"
        f"Incorporate the sub-agent findings into your work. Revise and improve "
        f"your output based on these results. Produce your final, complete output "
        f"including all artifacts.\n\n"
        f"If you need additional sub-agents, you may include another SPAWN_REQUESTS block. "
        f"Otherwise, produce your final output without a SPAWN_REQUESTS block.\n\n"
        f"## Your Previous Output (for reference)\n\n"
        f"{strip_spawn_block(original_output)}"
    )


# ---------------------------------------------------------------------------
# High-level spawn loop — used by both workflow engine and legacy engine
# ---------------------------------------------------------------------------

async def run_spawn_loop(
    initial_result: AgentResult,
    agent_name: str,
    parent_role: str,
    original_prompt: str,
    config: SpawnConfig,
    model: ModelTier,
    max_turns: int,
    workspace_dir: str,
    project_root: str,
    max_concurrent: int = 10,
    agents_config: dict[str, Any] | None = None,
    run_logger: Any | None = None,
    max_budget_usd: float = 0.0,
) -> tuple[AgentResult, list[SpawnRoundSummary]]:
    """Run the spawn-continue loop after an initial agent result.

    Returns:
        Tuple of (final AgentResult, list of spawn round summaries).
        If no spawns were requested, returns (initial_result, []).
    """
    from orchestrator.agents import invoke_agent

    if not config.enabled or not initial_result.success:
        return initial_result, []

    current_result = initial_result
    rounds: list[SpawnRoundSummary] = []
    total_spawn_cost = 0.0

    for round_num in range(1, config.max_spawn_rounds + 1):
        # Budget guard — skip spawn round if budget nearly exhausted
        if run_logger and max_budget_usd > 0:
            if run_logger.cumulative_cost_usd >= max_budget_usd * 0.95:
                logger.warning("Budget nearly exhausted — skipping spawn round")
                break

        # Check for spawn requests in agent output
        requests = extract_spawn_requests(current_result.output)
        if not requests:
            break

        # Validate each request
        valid_requests = []
        for req in requests:
            error = validate_spawn_request(parent_role, req, config)
            if error:
                logger.warning(f"Spawn request rejected: {error}")
            else:
                valid_requests.append(req)

        if not valid_requests:
            logger.info("All spawn requests were rejected by policy")
            break

        # Cap at max_spawns_per_round
        if len(valid_requests) > config.max_spawns_per_round:
            logger.warning(
                f"Capping spawn requests from {len(valid_requests)} "
                f"to {config.max_spawns_per_round}"
            )
            valid_requests = valid_requests[:config.max_spawns_per_round]

        logger.info(
            f"Spawn round {round_num}: {len(valid_requests)} sub-agent(s) "
            f"requested by {parent_role}"
        )

        if run_logger:
            run_logger.log_event("spawn_round_start", {
                "parent_role": parent_role,
                "round": round_num,
                "requests": [
                    {"role": r.role, "reason": r.reason[:100]}
                    for r in valid_requests
                ],
            })

        # Execute sub-agents
        spawn_results = await execute_spawn_requests(
            requests=valid_requests,
            parent_role=parent_role,
            config=config,
            workspace_dir=workspace_dir,
            project_root=project_root,
            max_concurrent=max_concurrent,
            agents_config=agents_config,
        )

        round_cost = sum(sr.cost_usd for sr in spawn_results)
        total_spawn_cost += round_cost

        round_summary = SpawnRoundSummary(
            round_number=round_num,
            requests=valid_requests,
            results=spawn_results,
            total_cost_usd=round_cost,
        )
        rounds.append(round_summary)

        if run_logger:
            # Log each spawned agent result so cumulative cost tracking picks them up
            for sr in spawn_results:
                run_logger.log_event("agent_result", {
                    "agent": f"spawn:{sr.role}",
                    "model": "sonnet",
                    "success": sr.success,
                    "cost_usd": sr.cost_usd,
                    "input_tokens": sr.input_tokens,
                    "output_tokens": sr.output_tokens,
                    "parent_role": parent_role,
                    "spawn_round": round_num,
                })
            run_logger.log_event("spawn_round_complete", {
                "parent_role": parent_role,
                "round": round_num,
                "sub_agents": [
                    {"role": sr.role, "success": sr.success, "cost": sr.cost_usd}
                    for sr in spawn_results
                ],
                "round_cost_usd": round_cost,
            })

        # Build continuation prompt and re-invoke parent agent
        continuation = build_continuation_prompt(current_result.output, spawn_results)

        logger.info(
            f"Re-invoking {agent_name} with {len(spawn_results)} sub-agent result(s)"
        )

        continuation_result = await invoke_agent(AgentInvocation(
            agent_name=agent_name,
            prompt=continuation,
            model=model,
            max_turns=max_turns,
            workspace_dir=workspace_dir,
            project_root=project_root,
        ))

        # Accumulate costs from continuation
        current_result = AgentResult(
            success=continuation_result.success,
            output=continuation_result.output,
            cost_usd=(
                current_result.cost_usd
                + round_cost
                + continuation_result.cost_usd
            ),
            turns_used=current_result.turns_used + continuation_result.turns_used,
            input_tokens=(
                current_result.input_tokens
                + sum(sr.input_tokens for sr in spawn_results)
                + continuation_result.input_tokens
            ),
            output_tokens=(
                current_result.output_tokens
                + sum(sr.output_tokens for sr in spawn_results)
                + continuation_result.output_tokens
            ),
            error=continuation_result.error,
        )

        if not continuation_result.success:
            logger.warning(
                f"Parent agent {agent_name} failed after spawn round {round_num}"
            )
            break

    if rounds:
        total_spawned = sum(len(r.requests) for r in rounds)
        logger.info(
            f"Spawn loop complete: {len(rounds)} round(s), "
            f"{total_spawned} sub-agent(s), "
            f"spawn cost: ${total_spawn_cost:.4f}"
        )

    return current_result, rounds
