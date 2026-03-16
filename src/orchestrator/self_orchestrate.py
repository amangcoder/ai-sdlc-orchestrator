"""Self-Orchestrate — LLM-powered pipeline design from a feature request.

Dynamically discovers available agents, assesses codebase complexity,
and uses both to design the optimal orchestration pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from orchestrator.models import ModelTier, WorkflowType

logger = logging.getLogger(__name__)

# Agent definitions directory
_AGENTS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "agents"


@dataclass
class ClarificationResult:
    """Result of a clarification round."""

    questions: list[str]
    ready: bool  # True = no more questions, proceed to pipeline design
    refined_request: str  # The original request enriched with clarification context
    cost_usd: float = 0.0


@dataclass
class OrchestrationPlan:
    """The result of self-orchestration analysis."""

    workflow_type: WorkflowType
    custom_workflow: str | None = None  # STEP-format definition, if custom
    enhanced_perception: bool = False
    rationale: str = ""
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Dynamic agent discovery
# ---------------------------------------------------------------------------

def _discover_agents() -> str:
    """Scan .claude/agents/ and build a formatted catalog of available agents."""
    if not _AGENTS_DIR.is_dir():
        return "(no agents directory found)"

    agents: list[tuple[str, str]] = []  # (file_stem, display_name)
    for md_file in sorted(_AGENTS_DIR.glob("*.md")):
        stem = md_file.stem
        content = md_file.read_text(errors="replace")

        # Extract name from YAML frontmatter
        display_name = stem.replace("_", " ").title()
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                frontmatter = content[3:end]
                m = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
                if m:
                    display_name = m.group(1).strip()

        agents.append((stem, display_name))

    if not agents:
        return "(no agents found)"

    lines = [f"- **{name}** (agent file: `{stem}`)" for stem, name in agents]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Codebase complexity assessment
# ---------------------------------------------------------------------------

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".next",
    ".nuxt", "coverage", ".eggs", "*.egg-info",
}

_LANG_EXTENSIONS: dict[str, str] = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript (React)", ".jsx": "JavaScript (React)",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".cpp": "C++",
    ".c": "C", ".swift": "Swift", ".dart": "Dart",
    ".sql": "SQL", ".sh": "Shell", ".yaml": "YAML", ".yml": "YAML",
    ".json": "JSON", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".md": "Markdown", ".toml": "TOML", ".tf": "Terraform",
    ".proto": "Protobuf", ".graphql": "GraphQL",
}


def _assess_codebase(project_root: Path | None) -> str:
    """Quick scan of the project to assess size and complexity."""
    if project_root is None or not project_root.is_dir():
        return "(could not assess codebase)"

    lang_counts: dict[str, int] = {}
    lang_lines: dict[str, int] = {}
    total_files = 0
    has_tests = False
    has_ci = False
    has_docker = False
    has_db = False
    config_files: list[str] = []

    for dirpath, dirnames, filenames in os.walk(project_root):
        # Prune skipped directories
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.endswith(".egg-info")]

        rel_dir = Path(dirpath).relative_to(project_root)

        for fname in filenames:
            fpath = Path(dirpath) / fname
            ext = fpath.suffix.lower()
            total_files += 1

            # Detect project signals
            fname_lower = fname.lower()
            if "test" in str(rel_dir) or fname_lower.startswith("test_") or fname_lower.endswith("_test.py"):
                has_tests = True
            if fname_lower in (".github", "jenkinsfile", ".gitlab-ci.yml", ".circleci"):
                has_ci = True
            if fname_lower in ("dockerfile", "docker-compose.yml", "docker-compose.yaml"):
                has_docker = True
            if "migration" in str(rel_dir).lower() or ext == ".sql":
                has_db = True
            if fname_lower in (
                "package.json", "pyproject.toml", "setup.py", "cargo.toml",
                "go.mod", "gemfile", "pom.xml", "build.gradle",
            ):
                config_files.append(fname)

            lang = _LANG_EXTENSIONS.get(ext)
            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
                try:
                    lines = fpath.read_text(errors="replace").count("\n")
                    lang_lines[lang] = lang_lines.get(lang, 0) + lines
                except (OSError, UnicodeDecodeError):
                    pass

    if total_files == 0:
        return "(empty project)"

    # Build summary
    parts: list[str] = []
    parts.append(f"- **Total files**: {total_files}")

    # Languages sorted by line count
    if lang_lines:
        sorted_langs = sorted(lang_lines.items(), key=lambda x: -x[1])
        total_lines = sum(lang_lines.values())
        lang_summary = ", ".join(
            f"{lang} ({lines:,} lines, {lines*100//total_lines}%)"
            for lang, lines in sorted_langs[:6]
        )
        parts.append(f"- **Languages**: {lang_summary}")
        parts.append(f"- **Total code lines**: ~{total_lines:,}")

    # Size classification
    total_code_lines = sum(lang_lines.values())
    if total_code_lines < 500:
        complexity = "Small (< 500 lines)"
    elif total_code_lines < 5_000:
        complexity = "Medium (500-5K lines)"
    elif total_code_lines < 50_000:
        complexity = "Large (5K-50K lines)"
    else:
        complexity = f"Very Large ({total_code_lines:,} lines)"
    parts.append(f"- **Complexity**: {complexity}")

    # Signals
    signals = []
    if has_tests:
        signals.append("has tests")
    if has_ci:
        signals.append("has CI/CD")
    if has_docker:
        signals.append("has Docker")
    if has_db:
        signals.append("has database/migrations")
    if config_files:
        signals.append(f"build: {', '.join(config_files[:3])}")
    if signals:
        parts.append(f"- **Signals**: {', '.join(signals)}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Clarification phase
# ---------------------------------------------------------------------------

_CLARIFY_PROMPT = """\
You are a **Requirements Analyst** preparing a feature request for an AI SDLC pipeline.

Your job is to identify ambiguities, missing information, and implicit assumptions \
in the feature request that, if left unresolved, would lead to poor architecture or \
wasted engineering effort.

## Codebase Profile

{codebase_profile}

## Available Agents

{agent_catalog}

## Feature Request

{feature_request}

## Conversation So Far

{conversation_history}

## Instructions

Analyze the feature request (and any prior Q&A above) and decide:

1. If there are important ambiguities or missing details that would significantly \
affect how the pipeline is designed or how agents do their work, ask **up to 3** \
focused, numbered questions. Prioritize questions that would change which agents \
are needed or how the workflow is structured.

2. If the request (combined with any answers already given) is clear enough to \
design an effective pipeline, set `ready` to `true`.

**Question priorities** (ask about these first):
- Scope boundaries — what's in/out of scope?
- Tech stack constraints — specific frameworks, databases, or infra requirements?
- Non-functional requirements — performance targets, security/compliance needs, \
accessibility requirements?
- Integration points — external APIs, third-party services, existing systems?
- User-facing vs internal — who uses this? What's the deployment target?

**Do NOT ask about:**
- Implementation details the engineers will figure out
- Things obvious from the codebase profile above
- Style preferences or naming conventions

Respond with ONLY this JSON:

```json
{{
  "questions": ["Question 1?", "Question 2?"],
  "ready": false,
  "summary": "Brief summary of what you now understand about the request"
}}
```

If no more questions are needed:
```json
{{
  "questions": [],
  "ready": true,
  "summary": "Complete understanding: ..."
}}
```"""


async def clarify(
    feature_request: str,
    conversation: list[tuple[str, str]],
    project_root: Path | None = None,
) -> ClarificationResult:
    """Run one round of clarification on a feature request.

    Args:
        feature_request: The original user request.
        conversation: List of (questions_asked, user_answer) from prior rounds.
        project_root: Project root for codebase assessment.

    Returns:
        ClarificationResult with questions (if any) or ready=True.
    """
    from orchestrator.agents import AgentInvocation, _invoke_via_cli, _invoke_via_sdk

    agent_catalog = _discover_agents()
    codebase_profile = _assess_codebase(project_root)

    # Build conversation history
    if conversation:
        history_parts = []
        for i, (questions, answer) in enumerate(conversation, 1):
            history_parts.append(f"### Round {i}\n**Questions asked:**\n{questions}\n\n**User answered:**\n{answer}")
        history = "\n\n".join(history_parts)
    else:
        history = "(First round — no prior conversation)"

    prompt = _CLARIFY_PROMPT.format(
        feature_request=feature_request,
        codebase_profile=codebase_profile,
        agent_catalog=agent_catalog,
        conversation_history=history,
    )

    logger.info("[Self-Orchestrate] Running clarification round...")
    start = time.monotonic()

    invocation = AgentInvocation(
        agent_name="pm",  # PM agent for requirements-style thinking
        prompt=prompt,
        model=ModelTier.SONNET,
        max_turns=2,
        project_root=str(project_root) if project_root else None,
    )

    try:
        try:
            result = await _invoke_via_sdk(invocation)
        except ImportError:
            result = await _invoke_via_cli(invocation)

        elapsed = time.monotonic() - start

        if not result.success:
            logger.warning(f"[Self-Orchestrate] Clarification failed ({elapsed:.1f}s): {result.error}")
            return ClarificationResult(
                questions=[], ready=True,
                refined_request=feature_request,
                cost_usd=result.cost_usd,
            )

        parsed = _parse_clarification(result.output)
        logger.info(
            f"[Self-Orchestrate] Clarification round ({elapsed:.1f}s, "
            f"${result.cost_usd:.4f}): "
            f"{'ready' if parsed['ready'] else str(len(parsed['questions'])) + ' questions'}"
        )

        # Build refined request with all context accumulated
        refined = feature_request
        if conversation or not parsed["ready"]:
            context_parts = [feature_request]
            for questions, answer in conversation:
                context_parts.append(f"\nClarifications:\n{answer}")
            if parsed.get("summary"):
                context_parts.append(f"\nUnderstood scope: {parsed['summary']}")
            refined = "\n".join(context_parts)

        return ClarificationResult(
            questions=parsed["questions"],
            ready=parsed["ready"],
            refined_request=refined,
            cost_usd=result.cost_usd,
        )

    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.warning(f"[Self-Orchestrate] Clarification error ({elapsed:.1f}s): {exc}")
        return ClarificationResult(
            questions=[], ready=True,
            refined_request=feature_request,
        )


def _parse_clarification(raw_output: str) -> dict:
    """Parse the clarification LLM response."""
    text = raw_output.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start_idx = text.find("{")
        end_idx = text.rfind("}") + 1
        if start_idx >= 0 and end_idx > start_idx:
            data = json.loads(text[start_idx:end_idx])
        else:
            return {"questions": [], "ready": True, "summary": ""}

    return {
        "questions": data.get("questions", []),
        "ready": bool(data.get("ready", True)),
        "summary": data.get("summary", ""),
    }


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SELF_ORCHESTRATE_PROMPT = """\
You are a **Pipeline Architect** — an expert at analyzing software tasks and designing \
the optimal AI agent orchestration pipeline to accomplish them.

## Built-in workflow types

1. **feature_development** — Full SDLC: PM → Architect → Principal Engineer → TPM → \
Implementation (parallel) → Code Review → QA → Release
   Best for: New features, greenfield development, significant new capabilities

2. **bugfix** — Bug Analysis → Root Cause → Fix Plan → Implementation → Code Review → QA
   Best for: Bug reports, error fixes, regression fixes

3. **refactor** — Scope Analysis → Architecture Review → Task Breakdown → Implementation → Code Review → QA
   Best for: Code restructuring, tech debt cleanup, pattern migrations

4. **performance_optimization** — Profiling → Bottleneck Analysis → Optimization Plan → \
Implementation → Benchmarking → Code Review
   Best for: Speed improvements, memory optimization, latency reduction

5. **security_audit** — Threat Model → Code Scan → Fix Plan → Implementation → Verification
   Best for: Security reviews, vulnerability fixes, compliance hardening

## Available specialist agents

These are the agents currently installed and available for use in custom workflows:

{agent_catalog}

## Codebase profile

{codebase_profile}

## Custom workflow format

```
STEP: <Step Name>
  agent: <agent display name from the catalog above>
  inputs: <comma-separated artifact names>
  outputs: <comma-separated artifact names>
  next: <next step name>
  parallel: true|false
  on_fail: <step name or "escalate">
```

Available artifact types: prd, architecture, engineering_plan, tasks, review, \
qa_report, benchmark_report, threat_model, vulnerability_report

## Your task

Analyze the feature request below **together with the codebase profile** and produce a \
JSON response. Consider:

- **Codebase complexity** → larger/more complex codebases benefit from more planning \
steps, enhanced perception, and specialist agents (security, observability, etc.)
- **Languages & stack** → choose frontend/backend/database specialists that match \
the actual tech stack
- **Existing signals** → if the codebase has tests, include QA; if it has Docker, \
consider DevOps; if it has migrations, consider Database Engineer
- **Agent availability** → only use agents from the catalog above in custom workflows
- **Task scope** → simple tasks get simple pipelines; complex multi-concern tasks \
get richer custom workflows with the right specialists

```json
{{
  "workflow_type": "feature_development|bugfix|refactor|performance_optimization|security_audit|custom",
  "custom_workflow": null or "STEP: ...\\n  agent: ...\\n...",
  "enhanced_perception": true or false,
  "rationale": "Brief explanation referencing codebase complexity and why you chose this pipeline"
}}
```

**Decision guidelines:**
- Use a **built-in workflow** when it closely matches the task — don't over-engineer
- Use a **custom workflow** when the task spans multiple concerns, needs specialists \
the built-in doesn't include, or when codebase signals suggest additional steps
- Enable **enhanced_perception** for ambiguous/complex/domain-heavy requests, or when \
the codebase is large (agents need more context to be effective)
- For custom workflows, always include an Implementation step with `parallel: true` \
and a Code Review step
- Ensure artifact chains are valid: each step's inputs must be produced by a prior step's outputs

## Feature Request

{feature_request}

---

Respond with ONLY the JSON object. No markdown fences, no commentary."""


_REVISE_PROMPT = """\
You are a **Pipeline Architect** revising an orchestration plan based on user feedback.

## Original Feature Request

{feature_request}

## Codebase Profile

{codebase_profile}

## Available Agents

{agent_catalog}

## Current Plan (JSON)

```json
{current_plan_json}
```

## User Feedback

{user_feedback}

---

Produce a revised JSON plan that addresses the user's feedback. Same format:

```json
{{
  "workflow_type": "feature_development|bugfix|refactor|performance_optimization|security_audit|custom",
  "custom_workflow": null or "STEP: ...\\n  agent: ...\\n...",
  "enhanced_perception": true or false,
  "rationale": "Brief explanation of what changed and why"
}}
```

Custom workflow STEP format:
```
STEP: <Step Name>
  agent: <agent display name>
  inputs: <comma-separated artifact names>
  outputs: <comma-separated artifact names>
  next: <next step name>
  parallel: true|false
  on_fail: <step name or "escalate">
```

Available artifact types: prd, architecture, engineering_plan, tasks, review, \
qa_report, benchmark_report, threat_model, vulnerability_report.

Respond with ONLY the JSON object. No markdown fences, no commentary."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def revise_plan(
    feature_request: str,
    current_plan: OrchestrationPlan,
    user_feedback: str,
    project_root: Path | None = None,
) -> OrchestrationPlan:
    """Revise an orchestration plan based on user feedback."""
    from orchestrator.agents import AgentInvocation, _invoke_via_cli, _invoke_via_sdk

    current_plan_json = json.dumps({
        "workflow_type": current_plan.workflow_type.value,
        "custom_workflow": current_plan.custom_workflow,
        "enhanced_perception": current_plan.enhanced_perception,
        "rationale": current_plan.rationale,
    }, indent=2)

    agent_catalog = _discover_agents()
    codebase_profile = _assess_codebase(project_root)

    prompt = _REVISE_PROMPT.format(
        feature_request=feature_request,
        codebase_profile=codebase_profile,
        agent_catalog=agent_catalog,
        current_plan_json=current_plan_json,
        user_feedback=user_feedback,
    )

    logger.info(f"[Self-Orchestrate] Revising plan based on feedback: {user_feedback[:80]}...")
    start = time.monotonic()

    invocation = AgentInvocation(
        agent_name="architect",
        prompt=prompt,
        model=ModelTier.SONNET,
        max_turns=2,
        project_root=str(project_root) if project_root else None,
    )

    try:
        try:
            result = await _invoke_via_sdk(invocation)
        except ImportError:
            result = await _invoke_via_cli(invocation)

        elapsed = time.monotonic() - start

        if not result.success:
            logger.warning(f"[Self-Orchestrate] Revision failed ({elapsed:.1f}s): {result.error}")
            return current_plan

        plan = _parse_plan(result.output, current_plan.cost_usd + result.cost_usd)
        logger.info(
            f"[Self-Orchestrate] Plan revised ({elapsed:.1f}s, "
            f"${result.cost_usd:.4f}): {plan.workflow_type.value}"
        )
        return plan

    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.warning(f"[Self-Orchestrate] Revision error ({elapsed:.1f}s): {exc}")
        return current_plan


async def self_orchestrate(
    feature_request: str,
    project_root: Path | None = None,
) -> OrchestrationPlan:
    """Analyze a feature request and generate the optimal orchestration plan.

    Dynamically discovers available agents and assesses codebase complexity
    to inform the pipeline design.
    """
    from orchestrator.agents import AgentInvocation, _invoke_via_cli, _invoke_via_sdk

    agent_catalog = _discover_agents()
    codebase_profile = _assess_codebase(project_root)

    logger.info(f"[Self-Orchestrate] Discovered agents:\n{agent_catalog}")
    logger.info(f"[Self-Orchestrate] Codebase profile:\n{codebase_profile}")

    prompt = _SELF_ORCHESTRATE_PROMPT.format(
        feature_request=feature_request,
        agent_catalog=agent_catalog,
        codebase_profile=codebase_profile,
    )

    logger.info("[Self-Orchestrate] Analyzing feature request to design optimal pipeline...")
    start = time.monotonic()

    invocation = AgentInvocation(
        agent_name="architect",
        prompt=prompt,
        model=ModelTier.SONNET,
        max_turns=2,
        project_root=str(project_root) if project_root else None,
    )

    try:
        try:
            result = await _invoke_via_sdk(invocation)
        except ImportError:
            result = await _invoke_via_cli(invocation)

        elapsed = time.monotonic() - start

        if not result.success:
            logger.warning(
                f"[Self-Orchestrate] LLM call failed ({elapsed:.1f}s): {result.error}. "
                "Falling back to feature_development workflow."
            )
            return OrchestrationPlan(
                workflow_type=WorkflowType.FEATURE_DEVELOPMENT,
                rationale="Self-orchestrate failed, using default workflow",
                cost_usd=result.cost_usd,
            )

        plan = _parse_plan(result.output, result.cost_usd)
        logger.info(
            f"[Self-Orchestrate] Pipeline designed ({elapsed:.1f}s, "
            f"${result.cost_usd:.4f}): {plan.workflow_type.value}"
            f"{' (custom)' if plan.custom_workflow else ''}"
            f"{' +perception' if plan.enhanced_perception else ''}"
        )
        logger.info(f"[Self-Orchestrate] Rationale: {plan.rationale}")
        return plan

    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.warning(
            f"[Self-Orchestrate] Error ({elapsed:.1f}s): {exc}. "
            "Falling back to feature_development workflow."
        )
        return OrchestrationPlan(
            workflow_type=WorkflowType.FEATURE_DEVELOPMENT,
            rationale=f"Self-orchestrate error: {exc}",
        )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_plan(raw_output: str, cost_usd: float) -> OrchestrationPlan:
    """Parse the LLM's JSON response into an OrchestrationPlan."""
    text = raw_output.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
        else:
            raise ValueError(f"Could not parse JSON from LLM output: {text[:200]}")

    wf_str = data.get("workflow_type", "feature_development")
    try:
        workflow_type = WorkflowType(wf_str)
    except ValueError:
        workflow_type = WorkflowType.FEATURE_DEVELOPMENT

    custom_workflow = data.get("custom_workflow")
    if workflow_type == WorkflowType.CUSTOM and not custom_workflow:
        workflow_type = WorkflowType.FEATURE_DEVELOPMENT
        custom_workflow = None

    return OrchestrationPlan(
        workflow_type=workflow_type,
        custom_workflow=custom_workflow,
        enhanced_perception=bool(data.get("enhanced_perception", False)),
        rationale=data.get("rationale", ""),
        cost_usd=cost_usd,
    )
