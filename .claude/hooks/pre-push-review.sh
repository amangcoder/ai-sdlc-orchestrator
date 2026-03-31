#!/usr/bin/env bash
# PreToolUse hook — matcher: Bash
# When a git push is detected, prints a summary of what's being pushed.
# Always allows the push (exit 0) but shows info on stderr.

set -euo pipefail

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

[[ "$TOOL" == "Bash" ]] || exit 0

COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

# Check if this is a git push command
if echo "$COMMAND" | grep -qE '^\s*git\s+push\b'; then
    echo "--- Pre-push review ---" >&2

    # Show what's being pushed
    if command -v git &>/dev/null && [[ -n "$CWD" ]] && [[ -d "$CWD/.git" || -d "$CWD/../.git" ]]; then
        BRANCH=$(git -C "$CWD" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
        echo "Branch: $BRANCH" >&2

        DIFF_STAT=$(git -C "$CWD" diff --stat HEAD~1 2>/dev/null || echo "(unable to compute diff)")
        echo "$DIFF_STAT" >&2

        COMMIT_MSG=$(git -C "$CWD" log -1 --oneline 2>/dev/null || echo "(no commits)")
        echo "Latest commit: $COMMIT_MSG" >&2
    else
        echo "(Could not determine git repository)" >&2
    fi

    echo "--- End pre-push review ---" >&2
fi

exit 0
