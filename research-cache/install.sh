#!/usr/bin/env bash
# install.sh — copies the research-cache MCP server to ../sdlc-mcp-servers/research-cache/
# and installs Node.js dependencies.
#
# Run from the orchestrator project root:
#   bash research-cache/install.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$(dirname "$SCRIPT_DIR")/sdlc-mcp-servers/research-cache"

echo "Installing research-cache MCP server to: $TARGET_DIR"

mkdir -p "$TARGET_DIR"

# Copy source files
cp "$SCRIPT_DIR/package.json"  "$TARGET_DIR/package.json"
cp "$SCRIPT_DIR/tsconfig.json" "$TARGET_DIR/tsconfig.json"
cp "$SCRIPT_DIR/index.ts"      "$TARGET_DIR/index.ts"
cp "$SCRIPT_DIR/README.md"     "$TARGET_DIR/README.md"

echo "Files copied. Running npm install && npm run build ..."
cd "$TARGET_DIR"
npm install
npm run build

echo ""
echo "Done! MCP server entry point: $TARGET_DIR/dist/index.js"
echo ""
echo "Add to .mcp.json:"
echo '  "research-cache": {'
echo '    "type": "stdio",'
echo '    "command": "node",'
echo "    \"args\": [\"$TARGET_DIR/dist/index.js\"],"
echo '    "env": {'
echo '      "GLOBAL_RESEARCH_DIR": "~/.orchestrator/research",'
echo '      "PROJECT_RESEARCH_DIR": ".knowledge/research",'
echo '      "PROJECT_ROOT": "<your-project-root>"'
echo '    }'
echo '  }'
