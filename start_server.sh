#!/usr/bin/env bash
# Start the Orchestrator Mobile API server.
# Usage: ./start_server.sh [--port PORT] [--config PATH] [--workspace PATH]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default values
PORT="${PORT:-8090}"
CONFIG="${CONFIG:-}"
WORKSPACE="${WORKSPACE:-${SCRIPT_DIR}/workspace}"

# Parse optional CLI overrides
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)      PORT="$2";      shift 2 ;;
    --config)    CONFIG="$2";    shift 2 ;;
    --workspace) WORKSPACE="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Activate virtual environment if present
if [[ -f "${SCRIPT_DIR}/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/.venv/bin/activate"
fi

# Build argument list
ARGS=(--port "$PORT" --workspace "$WORKSPACE")
if [[ -n "$CONFIG" ]]; then
  ARGS+=(--config "$CONFIG")
fi

echo "Starting Orchestrator Mobile API on port ${PORT}..."
echo "  Workspace : ${WORKSPACE}"
echo "  Config    : ${CONFIG:-config/default.yaml (default)}"
echo ""

exec orchestrator-mobile-api "${ARGS[@]}"
