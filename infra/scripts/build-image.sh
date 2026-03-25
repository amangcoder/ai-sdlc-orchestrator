#!/usr/bin/env bash
# infra/scripts/build-image.sh — Build and tag the AI SDLC Orchestrator Docker image.
#
# USAGE:
#   bash infra/scripts/build-image.sh [--tag <custom-tag>]
#
# WHAT IT DOES:
#   1. Computes VERSION from `git describe --tags --always` (bypasses hatch-vcs
#      inside Docker where .git is absent via .dockerignore).
#   2. Builds a multi-stage image from infra/docker/Dockerfile.
#   3. Tags the image as: ai-sdlc-orchestrator:latest AND ai-sdlc-orchestrator:<git-sha>
#   4. Optionally applies a third tag via --tag <custom-tag>.
#
# IMPORTANT: Re-run this script after any change to src/orchestrator/ or
# pyproject.toml — stale images will silently run outdated code.
#
# REQUIREMENTS: Docker Engine >= 20.10 must be installed and running.

set -euo pipefail

# ── Constants ────────────────────────────────────────────────────────────────
IMAGE_NAME="ai-sdlc-orchestrator"
DOCKERFILE="infra/docker/Dockerfile"

# ── Argument parsing ─────────────────────────────────────────────────────────
EXTRA_TAG=""

usage() {
    cat <<EOF
Usage: $(basename "$0") [--tag <custom-tag>]

Options:
  --tag <tag>   Apply an additional tag to the built image (e.g. v1.2.3, staging)
  --help        Show this message

Examples:
  bash infra/scripts/build-image.sh
  bash infra/scripts/build-image.sh --tag v1.0.0
  bash infra/scripts/build-image.sh --tag ci-\${GITHUB_SHA}
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --tag requires a value" >&2
                usage
                exit 1
            fi
            EXTRA_TAG="$2"
            shift 2
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
# Must be run from the project root (where pyproject.toml lives).
if [[ ! -f "pyproject.toml" ]]; then
    echo "ERROR: pyproject.toml not found. Run this script from the project root." >&2
    echo "       e.g.: bash infra/scripts/build-image.sh" >&2
    exit 1
fi

if [[ ! -f "$DOCKERFILE" ]]; then
    echo "ERROR: Dockerfile not found at $DOCKERFILE" >&2
    exit 1
fi

# Check Docker is available
if ! command -v docker &>/dev/null; then
    echo "ERROR: docker is not on PATH. Install Docker Engine >= 20.10." >&2
    exit 1
fi

# ── Version computation ──────────────────────────────────────────────────────
# Compute version BEFORE building so hatch-vcs inside Docker doesn't need .git.
# git describe --tags --always returns:
#   - "v1.2.3"           if HEAD is tagged
#   - "v1.2.3-4-gabcdef" if 4 commits after tag
#   - "gabcdef"          if no tags exist
VERSION=$(git describe --tags --always 2>/dev/null || echo "0.0.0")
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

echo "═══════════════════════════════════════════════════"
echo "  Building: ${IMAGE_NAME}"
echo "  Version:  ${VERSION}"
echo "  Git SHA:  ${GIT_SHA}"
echo "  Tags:     ${IMAGE_NAME}:latest, ${IMAGE_NAME}:${GIT_SHA}"
if [[ -n "$EXTRA_TAG" ]]; then
    echo "            ${IMAGE_NAME}:${EXTRA_TAG}"
fi
echo "═══════════════════════════════════════════════════"

# ── Build ────────────────────────────────────────────────────────────────────
# Use BuildKit for faster layer caching and parallel stage execution.
export DOCKER_BUILDKIT=1

docker build \
    --file "$DOCKERFILE" \
    --build-arg "VERSION=${VERSION}" \
    --tag "${IMAGE_NAME}:latest" \
    --tag "${IMAGE_NAME}:${GIT_SHA}" \
    --progress=plain \
    .

# ── Apply extra tag ──────────────────────────────────────────────────────────
if [[ -n "$EXTRA_TAG" ]]; then
    docker tag "${IMAGE_NAME}:latest" "${IMAGE_NAME}:${EXTRA_TAG}"
    echo "Tagged: ${IMAGE_NAME}:${EXTRA_TAG}"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "✓ Build succeeded!"
echo ""
echo "  Image:     ${IMAGE_NAME}:latest"
echo "  SHA tag:   ${IMAGE_NAME}:${GIT_SHA}"
if [[ -n "$EXTRA_TAG" ]]; then
    echo "  Extra tag: ${IMAGE_NAME}:${EXTRA_TAG}"
fi
echo ""
echo "  To run containerized orchestration:"
echo "    orchestrate-container \"Your feature\" --env-file ~/.orchestrator.env"
echo ""
echo "  Or using the shell wrapper:"
echo "    bash infra/scripts/run-containerized.sh --feature \"Your feature\""
echo ""
echo "REMINDER: Rebuild this image after any change to src/orchestrator/ or pyproject.toml."
echo "  Stale images silently run outdated code."
