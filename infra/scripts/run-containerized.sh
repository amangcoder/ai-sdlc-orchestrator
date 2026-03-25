#!/usr/bin/env bash
# infra/scripts/run-containerized.sh — Shell wrapper for non-Python callers.
#
# Delegates entirely to the `orchestrate-container` Python CLI entry point.
# This script is a thin adapter — all container lifecycle logic lives in
# src/orchestrator/container_runner.py and containerized_main.py.
#
# USAGE:
#   bash infra/scripts/run-containerized.sh --feature "Build a todo app" [OPTIONS]
#
# REQUIREMENTS:
#   - orchestrate-container CLI installed (pip install -e ".")
#   - Docker Engine >= 20.10 running
#   - ~/.orchestrator.env file with ANTHROPIC_API_KEY (chmod 600)
#   - setup-network-policy.sh run once (for network isolation)
#   - Docker image built: bash infra/scripts/build-image.sh

set -euo pipefail

# ── Usage ────────────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $(basename "$0") --feature <text> [OPTIONS]

Required:
  --feature <text>     Feature request to implement (quoted string)

Optional:
  --workspace <dir>    Workspace directory (default: ./workspace)
  --env-file <path>    Path to env file with ANTHROPIC_API_KEY
                       (default: ~/.orchestrator.env if it exists)
  --config <path>      Path to config YAML file (default: config/default.yaml)
  --run-id <id>        Explicit run ID (auto-generated if not provided)
  --dry-run            Print the docker run command without executing it
  --no-network-isolation  Use 'bridge' network instead of orchestrator-net
  --help               Show this message

Examples:
  # Minimal usage (uses ~/.orchestrator.env automatically if present):
  bash infra/scripts/run-containerized.sh --feature "Add user authentication"

  # With explicit env file:
  bash infra/scripts/run-containerized.sh \\
    --feature "Add user authentication" \\
    --env-file ~/.orchestrator.env

  # Dry run to preview the docker command:
  bash infra/scripts/run-containerized.sh \\
    --feature "Add user authentication" \\
    --dry-run
EOF
}

# ── Argument parsing ─────────────────────────────────────────────────────────
FEATURE=""
WORKSPACE=""
ENV_FILE=""
CONFIG=""
RUN_ID=""
DRY_RUN=false
NO_NETWORK=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --feature)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --feature requires a value" >&2
                usage
                exit 1
            fi
            FEATURE="$2"
            shift 2
            ;;
        --workspace)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --workspace requires a path" >&2
                usage
                exit 1
            fi
            WORKSPACE="$2"
            shift 2
            ;;
        --env-file)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --env-file requires a path" >&2
                usage
                exit 1
            fi
            ENV_FILE="$2"
            shift 2
            ;;
        --config)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --config requires a path" >&2
                usage
                exit 1
            fi
            CONFIG="$2"
            shift 2
            ;;
        --run-id)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --run-id requires a value" >&2
                usage
                exit 1
            fi
            RUN_ID="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --no-network-isolation)
            NO_NETWORK=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            usage
            exit 1
            ;;
    esac
done

# ── Validation ───────────────────────────────────────────────────────────────
if [[ -z "$FEATURE" ]]; then
    echo "ERROR: --feature is required" >&2
    echo ""
    usage
    exit 1
fi

# Check orchestrate-container is available
if ! command -v orchestrate-container &>/dev/null; then
    echo "ERROR: orchestrate-container not found on PATH." >&2
    echo "       Install the orchestrator package: pip install -e \".\"" >&2
    exit 1
fi

# ── Auto-detect env file ──────────────────────────────────────────────────────
if [[ -z "$ENV_FILE" && -f "$HOME/.orchestrator.env" ]]; then
    ENV_FILE="$HOME/.orchestrator.env"
fi

# ── Build CLI argument list ───────────────────────────────────────────────────
# Delegate entirely to the orchestrate-container Python CLI.
CLI_ARGS=("--feature-request" "$FEATURE")

if [[ -n "$WORKSPACE" ]]; then
    CLI_ARGS+=("--workspace" "$WORKSPACE")
fi

if [[ -n "$ENV_FILE" ]]; then
    CLI_ARGS+=("--env-file" "$ENV_FILE")
fi

if [[ -n "$CONFIG" ]]; then
    CLI_ARGS+=("--config" "$CONFIG")
fi

if [[ -n "$RUN_ID" ]]; then
    CLI_ARGS+=("--run-id" "$RUN_ID")
fi

if [[ "$DRY_RUN" == "true" ]]; then
    CLI_ARGS+=("--dry-run")
fi

if [[ "$NO_NETWORK" == "true" ]]; then
    CLI_ARGS+=("--no-network-isolation")
fi

# ── Execute ───────────────────────────────────────────────────────────────────
# exec replaces this shell process with orchestrate-container, so the exit
# code of orchestrate-container is propagated directly to the caller.
exec orchestrate-container "${CLI_ARGS[@]}"
