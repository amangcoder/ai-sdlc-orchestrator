#!/usr/bin/env bash
# Stop hook (async)
# Appends a session cost tracking line to ~/.orchestrator/cost-log.csv.
# Fields: date, project, working_directory, branch, duration_estimate

LOG_DIR="$HOME/.orchestrator"
LOG_FILE="$LOG_DIR/cost-log.csv"

# Create directory if needed
mkdir -p "$LOG_DIR" 2>/dev/null || exit 0

# Create CSV header if file doesn't exist
if [[ ! -f "$LOG_FILE" ]]; then
    echo "date,project,directory,branch,session_type" > "$LOG_FILE"
fi

# Gather info
DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date +"%Y-%m-%d")

# Try to get project name from git remote or directory name
if command -v git &>/dev/null; then
    PROJECT=$(git remote get-url origin 2>/dev/null | sed 's|.*/||;s|\.git$||' || basename "$PWD")
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
else
    PROJECT=$(basename "$PWD")
    BRANCH="unknown"
fi

CWD="$PWD"
SESSION_TYPE="claude-code"

# Append to log (CSV-safe: quote fields that might contain commas)
echo "\"$DATE\",\"$PROJECT\",\"$CWD\",\"$BRANCH\",\"$SESSION_TYPE\"" >> "$LOG_FILE" 2>/dev/null

exit 0
