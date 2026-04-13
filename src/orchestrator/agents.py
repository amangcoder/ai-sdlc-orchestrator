"""SDK wrapper for invoking Claude Code sub-agents."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.models import ModelTier, RunState

logger = logging.getLogger(__name__)

# Spinner frames for activity indicator
_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

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
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    error_code: str | None = None
    session_id: str | None = None
    written_files: list[str] = field(default_factory=list)


class AgentInvocation(BaseModel):
    """Parameters for invoking a sub-agent.

    Validated at construction time to prevent silent failures:
    - agent_name must be non-empty and contain only word chars and hyphens
      (prevents path traversal like '../secret' and empty name bugs)
    - prompt must be non-empty (prevents silent no-op invocations)
    - max_turns must be 1..200 (prevents SDK rejection for 0/-1 and runaway agents)
    """

    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(
        min_length=1,
        pattern=r"^[\w-]+$",
        description="Agent file stem in .claude/agents/. Must contain only word chars and hyphens.",
    )
    prompt: str = Field(min_length=1, description="Task prompt for the sub-agent.")
    model: ModelTier = ModelTier.SONNET
    max_turns: int = Field(
        default=40,
        ge=1,
        le=200,
        description="Maximum turns for the agent. Must be between 1 and 200.",
    )
    workspace_dir: str | None = None
    project_root: str | None = None  # cwd for agent — project root so it can explore the codebase
    isolation: str | None = None  # "worktree" for parallel engineers
    display_name: str | None = None  # codename for parallel agents
    mcp_servers: dict[str, Any] | None = None  # MCP server config for direct SDK injection
    resume_session_id: str | None = None  # Resume a prior conversation by session ID


def _create_worktree(project_root: Path, branch_suffix: str) -> tuple[Path, str]:
    """Create a git worktree for isolated parallel execution."""
    worktree_dir = Path(tempfile.mkdtemp(prefix=f"orch-wt-{branch_suffix}-"))
    branch_name = f"worktree/{branch_suffix}"
    subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, str(worktree_dir), "HEAD"],
        cwd=project_root, check=True, capture_output=True,
    )
    return worktree_dir, branch_name


def _merge_worktree(project_root: Path, branch_name: str) -> None:
    """Merge a worktree branch back into the current branch."""
    subprocess.run(
        ["git", "merge", "--no-edit", branch_name],
        cwd=project_root, check=True, capture_output=True,
    )


def _cleanup_worktree(project_root: Path, worktree_dir: Path, branch_name: str) -> None:
    """Remove a worktree and its branch."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_dir)],
        cwd=project_root, capture_output=True,
    )
    subprocess.run(["git", "worktree", "prune"], cwd=project_root, capture_output=True)
    subprocess.run(
        ["git", "branch", "-D", branch_name],
        cwd=project_root, capture_output=True,
    )


# OPTIMIZATION (Phase 2): Async versions for parallel worktree creation
async def _create_worktree_async(project_root: Path, branch_suffix: str) -> tuple[Path, str]:
    """Create a git worktree asynchronously (parallelizable).

    Phase 2 optimization: Allows multiple worktrees to be created in parallel
    via asyncio.gather() instead of sequentially via subprocess.run().
    """
    worktree_dir = Path(tempfile.mkdtemp(prefix=f"orch-wt-{branch_suffix}-"))
    branch_name = f"worktree/{branch_suffix}"

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "add", "-b", branch_name, str(worktree_dir), "HEAD",
            cwd=project_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode,
                "git worktree add",
                stdout=stdout,
                stderr=stderr,
            )
    except Exception:
        # Clean up temp directory if git command failed
        import shutil
        if worktree_dir.exists():
            try:
                shutil.rmtree(worktree_dir)
            except Exception:
                pass
        raise

    return worktree_dir, branch_name


async def _merge_worktree_async(project_root: Path, branch_name: str) -> None:
    """Merge a worktree branch asynchronously (can be parallelized)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "merge", "--no-edit", branch_name,
        cwd=project_root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode,
            "git merge",
            stdout=stdout,
            stderr=stderr,
        )


async def _cleanup_worktree_async(project_root: Path, worktree_dir: Path, branch_name: str) -> None:
    """Remove a worktree and its branch asynchronously."""
    # Run all three cleanup commands (they're independent)
    tasks = [
        asyncio.create_subprocess_exec(
            "git", "worktree", "remove", "--force", str(worktree_dir),
            cwd=project_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ),
        asyncio.create_subprocess_exec(
            "git", "worktree", "prune",
            cwd=project_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ),
        asyncio.create_subprocess_exec(
            "git", "branch", "-D", branch_name,
            cwd=project_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ),
    ]

    procs = await asyncio.gather(*tasks, return_exceptions=True)

    # Log any errors but don't raise (cleanup is best-effort)
    for i, result in enumerate(procs):
        if isinstance(result, Exception):
            logger.warning(f"Cleanup subprocess {i} failed: {result}")


async def invoke_agent(
    invocation: AgentInvocation,
    run_state: RunState | None = None,
) -> AgentResult:
    """Invoke a Claude Code sub-agent via the SDK.

    Uses claude_agent_sdk.query() when available, falls back to CLI subprocess.
    If run_state is provided, worktrees are tracked for crash recovery cleanup.
    """
    # Defense-in-depth: even though AgentInvocation.agent_name is validated by
    # Pydantic to match ^[\w-]+$, verify the resolved path stays within AGENTS_DIR.
    # This catches any edge case where the pattern validation is bypassed or
    # symlinks/unusual filesystem behaviour could redirect the path.
    agent_file = AGENTS_DIR / f"{invocation.agent_name}.md"
    resolved_agent_file = agent_file.resolve()
    resolved_agents_dir = AGENTS_DIR.resolve()
    if not resolved_agent_file.is_relative_to(resolved_agents_dir):
        raise ValueError(
            f"Security violation: agent path '{resolved_agent_file}' is outside "
            f"AGENTS_DIR '{resolved_agents_dir}'. Refusing to invoke agent."
        )

    # Worktree isolation: run in a separate git worktree if requested
    worktree_dir = None
    branch_name = None
    project_root = Path(invocation.project_root) if invocation.project_root else None

    if invocation.isolation == "worktree" and project_root:
        import uuid as _uuid
        suffix = _uuid.uuid4().hex[:8]
        try:
            # Phase 2 optimization: use async worktree creation for parallelization
            worktree_dir, branch_name = await _create_worktree_async(project_root, suffix)
            invocation.project_root = str(worktree_dir)
            logger.info(f"Created worktree for {invocation.agent_name}: {worktree_dir}")
            # Register worktree in state for crash recovery cleanup
            if run_state is not None:
                from orchestrator.models import WorktreeRecord
                from datetime import datetime, timezone
                run_state.active_worktrees.append(WorktreeRecord(
                    worktree_dir=str(worktree_dir),
                    branch_name=branch_name,
                    task_id=invocation.display_name or invocation.agent_name,
                    created_at=datetime.now(timezone.utc),
                ))
        except (subprocess.CalledProcessError, Exception) as e:
            logger.warning(f"Failed to create worktree: {e}. Running without isolation.")
            worktree_dir = None

    try:
        try:
            result = await _invoke_via_sdk(invocation)
        except ImportError:
            logger.info("claude_agent_sdk not available, falling back to CLI")
            result = await _invoke_via_cli(invocation)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Agent invocation interrupted by user")
            result = AgentResult(success=False, error="Agent interrupted by user (SIGINT)")
        except Exception as e:
            logger.error("Unexpected error invoking agent %s: %s", invocation.agent_name, e)
            result = AgentResult(success=False, error=str(e))

        # Save partial output for crash recovery context
        if invocation.workspace_dir and result.output:
            _save_partial_output(
                workspace=Path(invocation.workspace_dir),
                task_name=invocation.display_name or invocation.agent_name,
                output=result.output,
            )
    finally:
        # Merge and cleanup worktree
        if worktree_dir and project_root and branch_name:
            try:
                if result.success:
                    # Phase 2 optimization: use async merge for consistency
                    await _merge_worktree_async(project_root, branch_name)
                    logger.info(f"Merged worktree branch {branch_name}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to merge worktree {branch_name}: {e}")
                if result.success:
                    result = AgentResult(
                        success=False,
                        error=f"Worktree merge failed: {e}",
                        cost_usd=result.cost_usd,
                        turns_used=result.turns_used,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                    )
            finally:
                # Phase 2 optimization: use async cleanup
                await _cleanup_worktree_async(project_root, worktree_dir, branch_name)
                # Unregister worktree from state after cleanup
                if run_state is not None:
                    run_state.active_worktrees = [
                        wt for wt in run_state.active_worktrees
                        if wt.worktree_dir != str(worktree_dir)
                    ]

    return result


def _save_partial_output(workspace: Path, task_name: str, output: str) -> None:
    """Save truncated agent output for crash recovery context.

    On resume after crash, the retried agent can use this as context
    to avoid redoing completed analysis.
    """
    try:
        partial_dir = workspace / "artifacts" / ".partial"
        partial_dir.mkdir(parents=True, exist_ok=True)
        # Sanitize task name for filesystem
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_name)
        partial_path = partial_dir / f"{safe_name}_output.txt"
        # Truncate to last 4000 chars to bound file size
        partial_path.write_text(output[-4000:])
    except OSError as exc:
        logger.debug(f"Failed to save partial output for {task_name}: {exc}")


class AgentActivityTracker:
    """Tracks and displays live activity for a running agent.

    Runs a background thread that continuously rotates a spinner on the
    current line so the user always sees movement. When a real SDK message
    arrives, the spinner line is replaced with the message content.
    """

    def __init__(self, agent_name: str, model: str, max_turns: int) -> None:
        self.agent_name = agent_name
        self.model = model
        self.max_turns = max_turns
        self.turn_count = 0
        self.tool_calls = 0
        self.stitch_tool_calls = 0
        self.start_time = time.time()
        self.written_files: list[str] = []
        self._spinner_idx = 0
        self._last_activity: str = "starting..."
        # Threading for continuous spinner
        self._lock = threading.Lock()
        self._spinning = False
        self._spinner_thread: threading.Thread | None = None
        self._has_spinner_line = False  # True when spinner occupies the current line

    def _elapsed(self) -> str:
        secs = int(time.time() - self.start_time)
        if secs < 60:
            return f"{secs}s"
        mins, secs = divmod(secs, 60)
        return f"{mins}m{secs:02d}s"

    def _next_spinner(self) -> str:
        frame = _SPINNER[self._spinner_idx % len(_SPINNER)]
        self._spinner_idx += 1
        return frame

    def _spinner_line(self) -> str:
        """Build the spinner status line (no newline)."""
        elapsed = self._elapsed()
        return (
            f"  {self._next_spinner()} \033[2m[{elapsed}]"
            f" turn {self.turn_count}/{self.max_turns}\033[0m"
            f"  \033[1m{self.agent_name}\033[0m"
            f"  \033[2m{self._last_activity}\033[0m"
        )

    def _clear_spinner(self) -> None:
        """Overwrite the spinner line with blanks."""
        if self._has_spinner_line:
            sys.stdout.write(f"\r\033[K")
            sys.stdout.flush()
            self._has_spinner_line = False

    def _write_spinner(self) -> None:
        """Write/update the spinner on the current line (no newline)."""
        line = self._spinner_line()
        sys.stdout.write(f"\r\033[K{line}")
        sys.stdout.flush()
        self._has_spinner_line = True

    def _spin_loop(self) -> None:
        """Background thread: rotate spinner every 100ms."""
        while self._spinning:
            with self._lock:
                if self._spinning:
                    self._write_spinner()
            time.sleep(0.1)

    def start_spinner(self) -> None:
        """Start the background spinner thread."""
        self._spinning = True
        self._spinner_thread = threading.Thread(target=self._spin_loop, daemon=True)
        self._spinner_thread.start()

    def stop_spinner(self) -> None:
        """Stop the background spinner and clear its line."""
        self._spinning = False
        if self._spinner_thread:
            self._spinner_thread.join(timeout=1)
            self._spinner_thread = None
        with self._lock:
            self._clear_spinner()

    def print_start(self) -> None:
        print(
            f"\n  \033[36m{'─'*50}\033[0m\n"
            f"  \033[1;36m▶ {self.agent_name}\033[0m  "
            f"\033[2m(model: {self.model}, max_turns: {self.max_turns})\033[0m\n"
            f"  \033[36m{'─'*50}\033[0m",
            flush=True,
        )

    def print_done(self, success: bool, cost: float) -> None:
        self.stop_spinner()
        elapsed = self._elapsed()
        status = "\033[32m✔ done\033[0m" if success else "\033[31m✘ failed\033[0m"
        stitch_info = f" │ \033[35m{self.stitch_tool_calls} stitch\033[0m\033[2m" if self.stitch_tool_calls else ""
        print(
            f"  \033[36m{'─'*50}\033[0m\n"
            f"  {status}  \033[2m{self.agent_name} │ "
            f"{self.turn_count} turns │ {self.tool_calls} tool calls{stitch_info} │ "
            f"{elapsed} │ ${cost:.4f}\033[0m\n",
            flush=True,
        )

    def log_message(self, message: Any) -> None:
        """Print live progress from the SDK stream with activity context."""
        from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock

        if not isinstance(message, AssistantMessage):
            return

        is_new_turn = False
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                is_new_turn = True

        if is_new_turn:
            self.turn_count += 1

        with self._lock:
            self._clear_spinner()

            elapsed = self._elapsed()
            prefix = (
                f"  {self._next_spinner()} \033[2m[{elapsed}]"
                f" turn {self.turn_count}/{self.max_turns}\033[0m"
                f"  \033[1m{self.agent_name}\033[0m"
            )

            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    text = block.text.strip()[:200]
                    self._last_activity = text[:50]
                    print(f"{prefix} {text}", flush=True)
                elif isinstance(block, ToolUseBlock):
                    self.tool_calls += 1
                    self._last_activity = f"→ {block.name}"
                    # Track files written by Write tool for artifact rescue
                    if block.name == "Write" and isinstance(block.input, dict):
                        file_path = block.input.get("file_path", "")
                        if file_path:
                            self.written_files.append(file_path)
                    input_summary = str(block.input)[:80]
                    # Highlight Stitch MCP tool calls with a distinct color
                    if block.name.startswith("mcp__stitch__"):
                        self.stitch_tool_calls += 1
                        tool_display = f"\033[35m{block.name}\033[0m"  # magenta
                    else:
                        tool_display = f"\033[33m{block.name}\033[0m"
                    print(
                        f"{prefix} \033[2m→\033[0m {tool_display} {input_summary}",
                        flush=True,
                    )


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

    # Inject shared MCP tool documentation (if present) between the
    # autonomous preamble and the role-specific system prompt.
    tools_file = AGENTS_DIR / "_tools.md"
    tools_section = ""
    if tools_file.exists():
        tools_section = tools_file.read_text().strip() + "\n\n"

    # Prepend a no-clarification directive so the agent never pauses to ask questions
    autonomous_prefix = (
        "You are operating autonomously in a pipeline. "
        "Never ask the user for clarification or confirmation — make reasonable assumptions "
        "and proceed. If something is ambiguous, choose the most sensible default and continue.\n\n"
        "CRITICAL: When instructed to write output to a file path, you MUST use the Write tool "
        "to physically create the file on disk. Do NOT just output the content in your response text — "
        "downstream phases depend on the file existing at the specified path. After writing, verify "
        "the file exists using the Read tool.\n\n"
        "NEVER use EnterPlanMode or ExitPlanMode tools. You are not in plan mode — you are executing. "
        "Do NOT write your output to a plan file. Write it to the exact artifact path specified in the instructions.\n\n"
        "TOOL PRIORITY: If mcp__ai-code-knowledge__* tools are available, you MUST use them for ALL "
        "codebase exploration (finding files, locating symbols, understanding code). "
        "DO NOT use Glob, Grep, or Bash (cat/grep/sed/find) for exploration — use the MCP tools instead. "
        "Start with mcp__ai-code-knowledge__get_project_overview, then use find_symbol/semantic_search/get_implementation_context. "
        "Only use Read/Edit/Write for actually reading or modifying specific files.\n\n"
        "NEVER USE BASH FOR FILE OPERATIONS. Use Read (not cat/head/tail), Grep (not grep/rg), "
        "Glob (not find/ls), Edit (not sed/awk), Write (not echo/cat heredoc). "
        "The Bash tool is ONLY for running tests, git commands, and build tools.\n\n"
    )
    # Inject claude-flow tool instructions if bridge is available
    claude_flow_section = ""
    try:
        from orchestrator.claude_flow_bridge import build_claude_flow_prompt_section
        # Extract role from agent name (e.g., "backend-engineer" -> "backend_engineer")
        agent_role = invocation.agent_name.replace("-", "_")
        cf_prompt = build_claude_flow_prompt_section(agent_role)
        if cf_prompt:
            claude_flow_section = cf_prompt + "\n\n"
    except ImportError:
        pass

    # Inject Stitch MCP tool instructions when the server is present
    stitch_section = ""
    if invocation.mcp_servers and "stitch" in invocation.mcp_servers:
        stitch_section = (
            "## Google Stitch MCP Tools\n\n"
            "You have access to the Google Stitch MCP server (`mcp__stitch__*`).\n\n"
            "**RESTRICTION:** Only fetch screen IDs explicitly listed in your task's "
            "`stitch_screens` field or the \"Your Assigned Screens\" section of your prompt. "
            "Do NOT browse, list, or fetch any other screens. Other engineers handle those.\n\n"
            "**Adapt Stitch output** to match the project's existing patterns, components, "
            "and styling conventions. Never paste Stitch output verbatim.\n\n"
        )

    full_system_prompt = autonomous_prefix + tools_section + stitch_section + claude_flow_section + (system_prompt or "")

    # Use project root as cwd so the agent can explore the actual codebase.
    # Artifact paths in the prompt are absolute, so cwd only affects exploration.
    cwd = invocation.project_root or invocation.workspace_dir

    # All pipeline agents need acceptEdits to write artifacts to workspace/artifacts/.
    # READ_ONLY access is enforced at the prompt level (agents are told not to modify code).
    # Using "plan" mode blocks the Write tool entirely, which prevents artifact creation.
    permission_mode = "acceptEdits"

    # Auto-approve MCP tools so agents don't block on permission prompts.
    # Without this, MCP tool calls require interactive approval which breaks
    # automated pipeline execution.
    allowed_tools: list[str] = []
    if invocation.mcp_servers:
        for server_name in invocation.mcp_servers:
            allowed_tools.append(f"mcp__{server_name}__*")
        if "stitch" in invocation.mcp_servers:
            logger.info(
                "Stitch MCP server injected for agent %s (url: %s)",
                invocation.agent_name,
                invocation.mcp_servers["stitch"].get("url", "unknown"),
            )

    options = ClaudeAgentOptions(
        model=model,
        max_turns=invocation.max_turns,
        cwd=cwd,
        system_prompt=full_system_prompt,
        permission_mode=permission_mode,
        **({"resume": invocation.resume_session_id} if invocation.resume_session_id else {}),
        **({"mcp_servers": invocation.mcp_servers} if invocation.mcp_servers else {}),
        **({"allowed_tools": allowed_tools} if allowed_tools else {}),
    )

    tracker_name = invocation.display_name or invocation.agent_name
    tracker = AgentActivityTracker(tracker_name, model, invocation.max_turns)
    tracker.print_start()
    tracker.start_spinner()

    result_msg: ResultMessage | None = None
    # Capture session_id early from any message that carries it so we have it
    # even if the agent is interrupted before ResultMessage arrives.
    captured_session_id: str | None = None
    try:
        async for message in query(prompt=invocation.prompt, options=options):
            if isinstance(message, ResultMessage):
                result_msg = message
                captured_session_id = message.session_id
            else:
                # SystemMessage subclasses (TaskStarted, TaskProgress, etc.)
                # carry session_id — grab it on the first message we see.
                if captured_session_id is None:
                    sid = getattr(message, "session_id", None)
                    if sid:
                        captured_session_id = sid
                tracker.log_message(message)
    except (KeyboardInterrupt, asyncio.CancelledError):
        tracker.stop_spinner()
        tracker.print_done(success=False, cost=0.0)
        return AgentResult(
            success=False,
            error="Agent interrupted by user (SIGINT)",
            cost_usd=0.0,
            session_id=captured_session_id,
        )
    except Exception as e:
        error_msg = str(e)
        # SDK subprocess killed by SIGINT returns exit code -2
        if "exit code -2" in error_msg or "exit code: -2" in error_msg:
            tracker.stop_spinner()
            tracker.print_done(success=False, cost=0.0)
            return AgentResult(
                success=False,
                error="Agent interrupted by user (SIGINT)",
                cost_usd=0.0,
                session_id=captured_session_id,
            )
        # Any other SDK/subprocess error — return a failed result instead of
        # crashing the entire pipeline (e.g. output token limit exceeded).
        logger.error("Agent %s failed: %s", invocation.agent_name, error_msg)
        tracker.stop_spinner()
        tracker.print_done(success=False, cost=0.0)
        return AgentResult(
            success=False,
            error=error_msg,
            cost_usd=0.0,
            error_code="INFRA_ERROR",
            session_id=captured_session_id,
        )
    finally:
        tracker.stop_spinner()

    if result_msg is None:
        tracker.print_done(success=False, cost=0.0)
        return AgentResult(
            success=False,
            error="No result message received from SDK",
            error_code="INFRA_ERROR",
            session_id=captured_session_id,
        )

    cost = result_msg.total_cost_usd or 0.0
    success = not result_msg.is_error
    tracker.print_done(success=success, cost=cost)

    # Extract token counts from SDK result if available
    input_tokens = getattr(result_msg, "input_tokens", 0) or 0
    output_tokens = getattr(result_msg, "output_tokens", 0) or 0

    return AgentResult(
        success=success,
        output=result_msg.result or "",
        cost_usd=cost,
        turns_used=result_msg.num_turns,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error=result_msg.result if result_msg.is_error else None,
        session_id=captured_session_id,
        written_files=tracker.written_files,
    )


async def _invoke_via_cli(invocation: AgentInvocation) -> AgentResult:
    """Invoke agent via Claude Code CLI subprocess as fallback."""
    model = MODEL_MAP[invocation.model]
    cmd = [
        "claude",
        "--model", model,
        "--max-turns", str(invocation.max_turns),
        "--output-format", "json",
    ]

    # Resume a prior conversation if session ID is available
    if invocation.resume_session_id:
        cmd.extend(["--resume", invocation.resume_session_id])

    cmd.extend(["-p", invocation.prompt])

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
    session_id: str | None = None
    try:
        result_data = json.loads(output_text)
        cost = result_data.get("cost_usd", 0.0)
        session_id = result_data.get("session_id")
        output_text = result_data.get("result", output_text)
    except (json.JSONDecodeError, KeyError):
        pass

    return AgentResult(
        success=True,
        output=output_text,
        cost_usd=cost,
        session_id=session_id,
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
