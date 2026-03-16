"""Enhanced Perception — meta-cognitive prompt enrichment layer.

Before an agent executes, its prompt is sent through a lightweight LLM (Haiku)
that enriches, refines, and sharpens the prompt. The enhancer surfaces implicit
requirements, identifies edge cases, and adds technical considerations while
preserving all original instructions verbatim.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from orchestrator.models import ModelTier

if TYPE_CHECKING:
    from orchestrator.agents import AgentInvocation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Role-aware enhancement contexts
# ---------------------------------------------------------------------------

_ROLE_CONTEXTS: dict[str, str] = {
    "pm": (
        "The target agent is a **Product Manager**. Focus enhancements on: "
        "user personas, market context, success metrics, prioritization rationale, "
        "edge cases in user workflows, and non-functional requirements (performance, "
        "accessibility, i18n) that PMs often overlook."
    ),
    "architect": (
        "The target agent is a **System Architect**. Focus enhancements on: "
        "scalability implications, failure modes, data consistency boundaries, "
        "API contract clarity, migration paths, and technology trade-off questions "
        "that should be explicitly addressed."
    ),
    "principal_engineer": (
        "The target agent is a **Principal Engineer**. Focus enhancements on: "
        "risk prioritization, implementation sequencing rationale, testing strategy gaps, "
        "cross-cutting concerns (logging, monitoring, feature flags), and areas where "
        "the plan should address rollback procedures."
    ),
    "tpm": (
        "The target agent is a **Technical Project Manager**. Focus enhancements on: "
        "task granularity (are tasks small enough?), dependency accuracy, "
        "parallel execution opportunities, risk of scope creep in task descriptions, "
        "and whether acceptance criteria are truly independently verifiable."
    ),
    "engineer": (
        "The target agent is a **Software Engineer**. Focus enhancements on: "
        "error handling edge cases, input validation boundaries, concurrency issues, "
        "test coverage gaps, backward compatibility with existing code, and "
        "performance implications of the implementation approach."
    ),
    "frontend_engineer": (
        "The target agent is a **Frontend Engineer**. Focus enhancements on: "
        "accessibility (WCAG compliance), responsive design breakpoints, "
        "state management edge cases, loading/error states, keyboard navigation, "
        "and cross-browser compatibility considerations."
    ),
    "backend_engineer": (
        "The target agent is a **Backend Engineer**. Focus enhancements on: "
        "error handling, input validation, SQL injection prevention, rate limiting, "
        "database transaction boundaries, API versioning, and idempotency of operations."
    ),
    "database_engineer": (
        "The target agent is a **Database Engineer**. Focus enhancements on: "
        "migration safety (zero-downtime), index cardinality, query plan analysis, "
        "data integrity constraints, backup/restore considerations, and foreign key "
        "cascade behavior."
    ),
    "caching_engineer": (
        "The target agent is a **Caching & Performance Engineer**. Focus enhancements on: "
        "cache invalidation strategies, TTL trade-offs, thundering herd prevention, "
        "memory bounds, serialization overhead, and cold-start behavior."
    ),
    "qa": (
        "The target agent is a **QA Engineer**. Focus enhancements on: "
        "boundary value analysis, equivalence partitioning, negative test cases, "
        "race conditions to test, security-focused test scenarios, and data "
        "corruption scenarios."
    ),
    "qa_planner": (
        "The target agent is a **QA Planner**. Focus enhancements on: "
        "test strategy completeness, risk-based test prioritization, environment "
        "requirements, test data management, and coverage metrics definition."
    ),
    "qa_executor": (
        "The target agent is a **QA Executor**. Focus enhancements on: "
        "boundary value analysis, equivalence partitioning, negative test cases, "
        "race conditions to test, and data corruption scenarios."
    ),
    "reviewer": (
        "The target agent is a **Code Reviewer**. Focus enhancements on: "
        "security review checklist items (OWASP), performance anti-patterns to watch for, "
        "architectural drift from the design, missing error handling, and "
        "test coverage adequacy assessment criteria."
    ),
    "backend_reviewer": (
        "The target agent is a **Backend Code Reviewer**. Focus enhancements on: "
        "OWASP security patterns, N+1 query detection, transaction safety, "
        "API contract adherence, and error propagation correctness."
    ),
    "frontend_reviewer": (
        "The target agent is a **Frontend Code Reviewer**. Focus enhancements on: "
        "accessibility compliance, render performance, bundle size impact, "
        "state management patterns, and XSS prevention."
    ),
    "security_engineer": (
        "The target agent is a **Security Engineer**. Focus enhancements on: "
        "STRIDE threat categories, OWASP Top 10 mapping, authentication/authorization "
        "boundary analysis, data exposure risks, and supply chain security considerations."
    ),
    "devops_engineer": (
        "The target agent is a **DevOps Engineer**. Focus enhancements on: "
        "deployment rollback procedures, health check completeness, secret management, "
        "resource limits, and observability integration."
    ),
    "observability_engineer": (
        "The target agent is an **Observability Engineer**. Focus enhancements on: "
        "log level discipline, metric cardinality bounds, trace context propagation, "
        "alert fatigue prevention, and dashboard actionability."
    ),
    "documentation_engineer": (
        "The target agent is a **Documentation Engineer**. Focus enhancements on: "
        "audience clarity, example completeness, API reference accuracy, "
        "migration guide coverage, and troubleshooting sections."
    ),
    "automation_engineer": (
        "The target agent is an **Automation Engineer**. Focus enhancements on: "
        "CI pipeline reliability, flaky test prevention, parallel test execution, "
        "environment isolation, and artifact caching strategies."
    ),
    "git_manager": (
        "The target agent is a **Git Manager**. Focus enhancements on: "
        "branch naming conventions, merge strategy clarity, commit message standards, "
        "conflict resolution procedures, and tag/release management."
    ),
}

_DEFAULT_ROLE_CONTEXT = (
    "Focus enhancements on: completeness of requirements, edge cases, "
    "error handling, and technical considerations relevant to the agent's role."
)


# ---------------------------------------------------------------------------
# Enhancement meta-prompt
# ---------------------------------------------------------------------------

_ENHANCEMENT_PROMPT = """\
You are a **Perception Enhancer** — a meta-cognitive layer that refines and enriches \
prompts before they are sent to specialized AI agents in an SDLC pipeline.

Your job: take the ORIGINAL PROMPT below and produce an ENHANCED VERSION that makes \
the receiving agent more effective.

## What to enhance

1. **Surface implicit requirements** — identify things the prompt assumes but doesn't state
2. **Add edge cases** — failure modes, boundary conditions, error scenarios the agent should consider
3. **Sharpen acceptance criteria** — make vague criteria specific and testable
4. **Add technical considerations** — performance, security, accessibility, backward compatibility
5. **Improve structure** — add section headers, numbered steps, or checklists where helpful

## Role-specific focus

{role_context}

## Critical rules — DO NOT VIOLATE

- PRESERVE ALL original file paths, artifact paths, and output format specs VERBATIM
- PRESERVE ALL JSON schema definitions, field names, and validation rules exactly
- DO NOT remove or reword any original instruction — you may only ADD to the prompt
- DO NOT add instructions that contradict the original prompt
- DO NOT add meta-commentary or explain what you changed — output ONLY the enhanced prompt
- Your output must be the COMPLETE enhanced prompt, ready to send to the agent as-is

## ORIGINAL PROMPT

{original_prompt}

---

Produce the enhanced prompt now. Output ONLY the enhanced prompt text."""


async def enhance_prompt(
    invocation: AgentInvocation,
) -> tuple[AgentInvocation, float]:
    """Enhance an agent's prompt via a lightweight Haiku pre-processing call.

    Returns ``(new_invocation, enhancement_cost_usd)``.
    On any failure the original invocation is returned unchanged with zero cost.
    """
    from orchestrator.agents import AgentInvocation as _AI, _invoke_via_cli, _invoke_via_sdk

    agent_name = invocation.agent_name
    original_prompt = invocation.prompt

    role_context = _ROLE_CONTEXTS.get(agent_name, _DEFAULT_ROLE_CONTEXT)
    meta_prompt = _ENHANCEMENT_PROMPT.format(
        role_context=role_context,
        original_prompt=original_prompt,
    )

    logger.info(
        f"[Perception] Enhancing prompt for '{agent_name}' "
        f"(original: {len(original_prompt)} chars)"
    )
    start = time.monotonic()

    try:
        enhancement_invocation = _AI(
            agent_name=agent_name,
            prompt=meta_prompt,
            model=ModelTier.HAIKU,
            max_turns=2,
            workspace_dir=invocation.workspace_dir,
            project_root=invocation.project_root,
            enhanced_perception=False,  # prevent recursion
        )

        try:
            result = await _invoke_via_sdk(enhancement_invocation)
        except ImportError:
            result = await _invoke_via_cli(enhancement_invocation)

        elapsed = time.monotonic() - start

        if result.success and result.output.strip():
            enhanced = result.output.strip()
            logger.info(
                f"[Perception] Done for '{agent_name}' — "
                f"{len(original_prompt)} -> {len(enhanced)} chars, "
                f"${result.cost_usd:.4f}, {elapsed:.1f}s"
            )
            return replace(invocation, prompt=enhanced), result.cost_usd

        logger.warning(
            f"[Perception] Enhancement failed for '{agent_name}', "
            f"using original prompt. Error: {result.error}"
        )
        return invocation, 0.0

    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.warning(
            f"[Perception] Error for '{agent_name}' ({elapsed:.1f}s): {exc}. "
            f"Using original prompt."
        )
        return invocation, 0.0
