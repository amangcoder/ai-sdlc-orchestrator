#!/usr/bin/env bash
# PreToolUse hook — matcher: Write|Edit
# Scans content being written for hardcoded secrets and API keys.
# Exit 2 = block the tool call; Exit 0 = allow.

set -euo pipefail

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Only check Write and Edit tools
case "$TOOL" in
    Write|Edit) ;;
    *) exit 0 ;;
esac

# Extract the file path being written to
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty')

# Allow .env.example files (they contain placeholder values)
if [[ "$FILE_PATH" == *.env.example ]]; then
    exit 0
fi

# Extract content to scan — for Write it's "content", for Edit it's "new_string"
CONTENT=$(echo "$INPUT" | jq -r '.tool_input.content // .tool_input.new_string // empty')

if [[ -z "$CONTENT" ]]; then
    exit 0
fi

# Secret patterns to detect
PATTERNS=(
    'sk-ant-[a-zA-Z0-9_-]{20,}'
    'sk-[a-zA-Z0-9_-]{20,}'
    'ghp_[a-zA-Z0-9]{36}'
    'AKIA[0-9A-Z]{16}'
    'password\s*=\s*["\x27][^"\x27]{4,}'
    'api[_-]?key\s*=\s*["\x27][^"\x27]{8,}'
    'token\s*=\s*["\x27][^"\x27]{8,}'
    'secret\s*=\s*["\x27][^"\x27]{8,}'
    'Bearer\s+[a-zA-Z0-9_-]{20,}'
    'xox[bporas]-[a-zA-Z0-9-]+'
)

FOUND=()
for PATTERN in "${PATTERNS[@]}"; do
    if echo "$CONTENT" | grep -qEi "$PATTERN" 2>/dev/null; then
        FOUND+=("$PATTERN")
    fi
done

if [[ ${#FOUND[@]} -gt 0 ]]; then
    echo "BLOCKED: Potential secret detected in content being written to $FILE_PATH" >&2
    echo "Matched patterns:" >&2
    for P in "${FOUND[@]}"; do
        echo "  - $P" >&2
    done
    echo "If this is a false positive, use a placeholder value or add to .env.example instead." >&2
    exit 2
fi

exit 0
