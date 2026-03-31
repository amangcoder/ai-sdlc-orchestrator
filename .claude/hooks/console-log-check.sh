#!/usr/bin/env bash
# Stop hook
# Checks modified files for leftover debugging artifacts.
# Warns if found but always exits 0 (non-blocking).

if ! command -v git &>/dev/null; then
    exit 0
fi

# Find the git repo root
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$REPO_ROOT" ]]; then
    exit 0
fi

# Get list of modified files (staged + unstaged)
MODIFIED_FILES=$(git -C "$REPO_ROOT" diff --name-only HEAD 2>/dev/null || true)
STAGED_FILES=$(git -C "$REPO_ROOT" diff --cached --name-only 2>/dev/null || true)
ALL_FILES=$(echo -e "${MODIFIED_FILES}\n${STAGED_FILES}" | sort -u | grep -v '^$' || true)

if [[ -z "$ALL_FILES" ]]; then
    exit 0
fi

WARNINGS=()

while IFS= read -r FILE; do
    FULL_PATH="$REPO_ROOT/$FILE"
    [[ -f "$FULL_PATH" ]] || continue

    case "$FILE" in
        *.py)
            # Check Python debugging artifacts
            if grep -nE '^\s*(print\(|breakpoint\(\)|pdb\.set_trace\(\)|import pdb)' "$FULL_PATH" 2>/dev/null | head -5; then
                WARNINGS+=("$FILE: Python debugging artifact(s) found")
            fi > /dev/null
            # Re-check and capture output
            HITS=$(grep -nE '^\s*(print\(|breakpoint\(\)|pdb\.set_trace\(\)|import pdb)' "$FULL_PATH" 2>/dev/null | head -5 || true)
            if [[ -n "$HITS" ]]; then
                WARNINGS+=("$FILE:")
                while IFS= read -r LINE; do
                    WARNINGS+=("  $LINE")
                done <<< "$HITS"
            fi
            ;;
        *.js|*.ts|*.jsx|*.tsx)
            # Check JS/TS debugging artifacts
            HITS=$(grep -nE '^\s*console\.(log|debug|warn|error)\(' "$FULL_PATH" 2>/dev/null | head -5 || true)
            if [[ -n "$HITS" ]]; then
                WARNINGS+=("$FILE:")
                while IFS= read -r LINE; do
                    WARNINGS+=("  $LINE")
                done <<< "$HITS"
            fi
            ;;
    esac
done <<< "$ALL_FILES"

if [[ ${#WARNINGS[@]} -gt 0 ]]; then
    echo "WARNING: Debugging artifacts found in modified files:" >&2
    for W in "${WARNINGS[@]}"; do
        echo "  $W" >&2
    done
    echo "Consider removing these before committing." >&2
fi

exit 0
