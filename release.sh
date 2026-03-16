#!/usr/bin/env bash
# Usage: ./release.sh 0.2.0
set -euo pipefail

NEW_VERSION="${1:?Usage: ./release.sh <version>}"

# Bump version in pyproject.toml
sed -i '' "s/^version = \".*\"/version = \"$NEW_VERSION\"/" pyproject.toml

# Build wheel + sdist into dist/
pip install build -q
python -m build

echo ""
echo "Built: dist/ai_sdlc_orchestrator-${NEW_VERSION}-py3-none-any.whl"
echo ""
echo "To freeze a project at this version, run in that project's venv:"
echo "  pip install /Users/amangupta/Projects/Orchestrator/dist/ai_sdlc_orchestrator-${NEW_VERSION}-py3-none-any.whl"
