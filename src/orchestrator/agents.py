"""SDK wrapper for invoking Claude Code sub-agents."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.models import ModelTier

logger = logging.getLogger(__name__)

# Agent definition directory
AGENTS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "agents"

MODEL_MAP: dict[ModelTier, str] = {
    ModelTier.HAIKU: "haiku",
    ModelTier.SONNET: "sonnet",
    ModelTier.OPUS: "opus",
}


@dataclass
class AgentResult:
    """Result from a sub-agent invocation."""

    success: bool
    output: str = ""
    cost_usd: float = 0.0
    turns_used: int = 0
    error: str | None = None


@dataclass
class AgentInvocation:
    """Parameters for invoking a sub-agent."""

    agent_name: str
    prompt: str
    model: ModelTier = ModelTier.SONNET
    max_turns: int = 40
    workspace_dir: str | None = None
    project_root: str | None = None  # cwd for agent — project root so it can explore the codebase
    isolation: str | None = None  # "worktree" for parallel engineers
    enhanced_perception: bool = False


async def invoke_agent(invocation: AgentInvocation) -> AgentResult:
    """Invoke a Claude Code sub-agent via the SDK.

    Uses claude_agent_sdk.query() when available, falls back to CLI subprocess.
    When enhanced_perception is enabled, enriches the prompt via a lightweight
    Haiku pre-processing call before the main agent invocation.
    """
    perception_cost = 0.0
    if invocation.enhanced_perception:
        from orchestrator.perception import enhance_prompt
        invocation, perception_cost = await enhance_prompt(invocation)

    try:
        result = await _invoke_via_sdk(invocation)
    except ImportError:
        logger.info("claude_agent_sdk not available, falling back to CLI")
        result = await _invoke_via_cli(invocation)

    result.cost_usd += perception_cost
    return result


def _log_sdk_message(agent_name: str, message: Any) -> None:
    """Print live progress from the SDK stream."""
    from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock

    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                print(f"  [{agent_name}] {block.text.strip()[:200]}", flush=True)
            elif isinstance(block, ToolUseBlock):
                input_summary = str(block.input)[:80]
                print(f"  [{agent_name}] tool={block.name} {input_summary}", flush=True)


async def _invoke_via_sdk(invocation: AgentInvocation) -> AgentResult:
    """Invoke agent using the Claude Agent SDK."""
    from claude_agent_sdk import query
    from claude_agent_sdk.types import ClaudeAgentOptions, ResultMessage

    model = MODEL_MAP[invocation.model]
    agent_file = AGENTS_DIR / f"{invocation.agent_name}.md"

    # Extract system prompt from agent .md file (strip YAML frontmatter)
    system_prompt: str | None = None
    if agent_file.exists():
        content = agent_file.read_text()
        if content.startswith("---"):
            end = content.find("---", 3)
            system_prompt = content[end + 3:].strip() if end != -1 else content
        else:
            system_prompt = content

    # Prepend a no-clarification directive so the agent never pauses to ask questions
    autonomous_prefix = (
        "You are operating autonomously in a pipeline. "
        "Never ask the user for clarification or confirmation — make reasonable assumptions "
        "and proceed. If something is ambiguous, choose the most sensible default and continue.\n\n"
    )
    full_system_prompt = (autonomous_prefix + system_prompt) if system_prompt else autonomous_prefix

    # Use project root as cwd so the agent can explore the actual codebase.
    # Artifact paths in the prompt are absolute, so cwd only affects exploration.
    cwd = invocation.project_root or invocation.workspace_dir

    options = ClaudeAgentOptions(
        model=model,
        max_turns=invocation.max_turns,
        cwd=cwd,
        system_prompt=full_system_prompt,
        permission_mode="acceptEdits",  # auto-approve file edits, no permission prompts
    )

    result_msg: ResultMessage | None = None
    async for message in query(prompt=invocation.prompt, options=options):
        if isinstance(message, ResultMessage):
            result_msg = message
        else:
            _log_sdk_message(invocation.agent_name, message)

    if result_msg is None:
        return AgentResult(success=False, error="No result message received from SDK")

    return AgentResult(
        success=not result_msg.is_error,
        output=result_msg.result or "",
        cost_usd=result_msg.total_cost_usd or 0.0,
        turns_used=result_msg.num_turns,
        error=result_msg.result if result_msg.is_error else None,
    )


async def _invoke_via_cli(invocation: AgentInvocation) -> AgentResult:
    """Invoke agent via Claude Code CLI subprocess as fallback."""
    model = MODEL_MAP[invocation.model]
    cmd = [
        "claude",
        "--model", model,
        "--max-turns", str(invocation.max_turns),
        "--output-format", "json",
        "-p", invocation.prompt,
    ]

    agent_file = AGENTS_DIR / f"{invocation.agent_name}.md"
    if agent_file.exists():
        cmd.extend(["--agent", str(agent_file)])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=invocation.workspace_dir,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return AgentResult(
            success=False,
            output=stdout.decode(),
            error=stderr.decode() or f"CLI exited with code {proc.returncode}",
        )

    output_text = stdout.decode()
    cost = 0.0
    try:
        result_data = json.loads(output_text)
        cost = result_data.get("cost_usd", 0.0)
        output_text = result_data.get("result", output_text)
    except (json.JSONDecodeError, KeyError):
        pass

    return AgentResult(
        success=True,
        output=output_text,
        cost_usd=cost,
    )


async def invoke_agents_parallel(
    invocations: list[AgentInvocation],
    max_concurrent: int = 0,
) -> list[AgentResult]:
    """Invoke multiple agents in parallel.

    Args:
        max_concurrent: Maximum number of agents to run at the same time.
            0 means unlimited (all agents launch concurrently).
    """
    if max_concurrent <= 0 or max_concurrent >= len(invocations):
        return await asyncio.gather(
            *(invoke_agent(inv) for inv in invocations)
        )

    semaphore = asyncio.Semaphore(max_concurrent)
    logger.info(f"Throttling parallel agents to max {max_concurrent} concurrent")

    async def _throttled(inv: AgentInvocation) -> AgentResult:
        async with semaphore:
            return await invoke_agent(inv)

    return await asyncio.gather(*(_throttled(inv) for inv in invocations))
