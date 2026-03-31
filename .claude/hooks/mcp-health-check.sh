#!/usr/bin/env bash
# PreToolUse hook — matcher for MCP tools (tool names starting with "mcp__")
# Before MCP tool calls, checks if the MCP server process is likely running.
# Always allows the call (exit 0) but warns if the server seems down.

set -euo pipefail

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Only check MCP tools (they follow the mcp__<server>__<method> naming convention)
if [[ "$TOOL" != mcp__* ]]; then
    exit 0
fi

# Extract the MCP server name from the tool name (second segment)
MCP_SERVER=$(echo "$TOOL" | cut -d'_' -f4)

if [[ -z "$MCP_SERVER" ]]; then
    exit 0
fi

# Check if a process matching the MCP server name is running
# This is a best-effort heuristic — MCP servers may run under different process names
MCP_RUNNING=false

# Check common MCP process patterns
if pgrep -f "mcp.*${MCP_SERVER}" &>/dev/null; then
    MCP_RUNNING=true
elif pgrep -f "${MCP_SERVER}.*mcp" &>/dev/null; then
    MCP_RUNNING=true
elif pgrep -f "${MCP_SERVER}" &>/dev/null; then
    MCP_RUNNING=true
fi

if [[ "$MCP_RUNNING" == "false" ]]; then
    echo "WARNING: MCP server '$MCP_SERVER' may not be running. Tool call '$TOOL' might fail." >&2
    echo "If the call fails, check that the MCP server is started and configured." >&2
fi

# Always allow — this is informational only
exit 0
