#!/usr/bin/env bash
# Reads stdin JSON from Claude Code hook system
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
AGENT=$(echo "$INPUT" | jq -r '.agent_type // empty')

# Only restrict the QA agent (agent file stem = "qa")
[[ "$AGENT" != "qa" ]] && exit 0

# Block write-capable tools for QA (read-only enforcement)
case "$TOOL" in
    Write|Edit|MultiEdit)
        echo "BLOCKED: QA agent is read-only — cannot use $TOOL" >&2
        exit 2 ;;
    *) exit 0 ;;
esac
