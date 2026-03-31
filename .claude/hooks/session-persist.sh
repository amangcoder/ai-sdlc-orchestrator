#!/usr/bin/env bash
# Stop hook (async)
# Saves a brief session summary to .claude/sessions/last-session.md

# Find repo root
if ! command -v git &>/dev/null; then
    exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$REPO_ROOT" ]]; then
    exit 0
fi

SESSION_DIR="$REPO_ROOT/.claude/sessions"
mkdir -p "$SESSION_DIR" 2>/dev/null || exit 0

SESSION_FILE="$SESSION_DIR/last-session.md"

# Gather info
DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%Y-%m-%d")
BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
LAST_COMMIT=$(git -C "$REPO_ROOT" log -1 --oneline 2>/dev/null || echo "none")

# Get modified files
MODIFIED=$(git -C "$REPO_ROOT" status --short 2>/dev/null || echo "unable to determine")

cat > "$SESSION_FILE" <<EOF
# Last Session Summary

- **Date:** $DATE
- **Branch:** $BRANCH
- **Last Commit:** $LAST_COMMIT

## Modified Files

\`\`\`
$MODIFIED
\`\`\`
EOF

exit 0
