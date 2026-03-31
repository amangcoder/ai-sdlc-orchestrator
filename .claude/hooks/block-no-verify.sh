#!/usr/bin/env bash
# PreToolUse hook — matcher: Bash
# Blocks git commands that use --no-verify to bypass pre-commit hooks.
# Exit 2 = block; Exit 0 = allow.

set -euo pipefail

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

[[ "$TOOL" == "Bash" ]] || exit 0

COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Check if this is a git command with --no-verify
if echo "$COMMAND" | grep -qE '\bgit\b.*--no-verify'; then
    echo "BLOCKED: --no-verify bypasses pre-commit hooks" >&2
    exit 2
fi

exit 0
