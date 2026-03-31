#!/usr/bin/env bash
# PostToolUse hook — matcher: Edit|Write
# After a Python file is edited, runs ruff check --fix on it (non-blocking).
# Skips non-Python files. Gracefully handles missing ruff.

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Only run for Edit and Write tools
case "$TOOL" in
    Edit|Write) ;;
    *) exit 0 ;;
esac

# Extract file path
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty')

# Skip non-Python files
if [[ "$FILE_PATH" != *.py ]]; then
    exit 0
fi

# Skip if file doesn't exist
[[ -f "$FILE_PATH" ]] || exit 0

# Check if ruff is available
if ! command -v ruff &>/dev/null; then
    exit 0
fi

# Run ruff in background (non-blocking)
(ruff check --fix --quiet "$FILE_PATH" 2>/dev/null || true) &
disown 2>/dev/null

exit 0
