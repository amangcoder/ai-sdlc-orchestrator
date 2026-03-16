#!/usr/bin/env bash
set -euo pipefail
INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
ARTIFACTS_DIR="${CWD:-.}/artifacts"
[[ -d "$ARTIFACTS_DIR" ]] || exit 0  # no artifacts dir yet = early phase, skip
orchestrate validate "$ARTIFACTS_DIR"
