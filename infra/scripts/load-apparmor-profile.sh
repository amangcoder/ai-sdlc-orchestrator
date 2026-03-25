#!/usr/bin/env bash
# infra/scripts/load-apparmor-profile.sh — Load the orchestrator AppArmor profile.
#
# WHAT THIS DOES:
#   Loads infra/docker/apparmor-profile into the kernel using apparmor_parser.
#   Once loaded, the profile name 'ai-sdlc-orchestrator' can be referenced via:
#     docker run --security-opt apparmor=ai-sdlc-orchestrator ...
#   or via the apparmor_profile setting in config/default.yaml.
#
# USAGE:
#   sudo bash infra/scripts/load-apparmor-profile.sh [--dry-run] [--unload]
#
# REQUIREMENTS:
#   - Linux host with AppArmor kernel module loaded (Ubuntu 18.04+, Debian 10+)
#   - apparmor_parser installed (apt install apparmor)
#   - Run as root or with sudo
#
# VERIFICATION:
#   sudo apparmor_status | grep ai-sdlc-orchestrator
#
# PERSISTENCE:
#   To auto-load on boot, copy the profile to /etc/apparmor.d/:
#     sudo cp infra/docker/apparmor-profile /etc/apparmor.d/ai-sdlc-orchestrator
#     sudo systemctl reload apparmor
#
# NOTE:
#   AppArmor is Linux-only. macOS and Windows Docker Desktop do not support it.
#   The profile is automatically skipped on non-Linux platforms by ContainerRuntime.

set -euo pipefail

PROFILE_FILE="infra/docker/apparmor-profile"
PROFILE_NAME="ai-sdlc-orchestrator"
DRY_RUN=false
UNLOAD=false

# ── Argument parsing ──────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: sudo $(basename "$0") [--dry-run] [--unload]

Options:
  --dry-run   Print intended actions without executing
  --unload    Remove the profile from the kernel (enforcing → unloaded)
  --help      Show this message

After loading, enable AppArmor enforcement by adding to config/default.yaml:
  container:
    apparmor_profile: ai-sdlc-orchestrator
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --unload)  UNLOAD=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "ERROR: Unknown argument: $1" >&2; usage; exit 1 ;;
    esac
done

# ── Platform check ────────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Linux" ]]; then
    echo "WARNING: AppArmor is Linux-only. This script does nothing on $(uname -s)." >&2
    echo "         AppArmor enforcement is skipped automatically on non-Linux platforms." >&2
    exit 0
fi

# ── Root check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 && "$DRY_RUN" == "false" ]]; then
    echo "ERROR: This script requires root privileges." >&2
    echo "       Re-run: sudo bash infra/scripts/load-apparmor-profile.sh" >&2
    exit 1
fi

# ── AppArmor availability check ───────────────────────────────────────────────
if ! command -v apparmor_parser &>/dev/null; then
    echo "ERROR: apparmor_parser not found." >&2
    echo "       Install AppArmor: sudo apt install apparmor apparmor-utils" >&2
    exit 1
fi

# ── Validate profile file exists ──────────────────────────────────────────────
if [[ ! -f "$PROFILE_FILE" ]]; then
    echo "ERROR: Profile file not found: $PROFILE_FILE" >&2
    echo "       Run this script from the project root directory." >&2
    exit 1
fi

# ── Run or print ──────────────────────────────────────────────────────────────
run() {
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

# ── Unload path ───────────────────────────────────────────────────────────────
if [[ "$UNLOAD" == "true" ]]; then
    echo "==> Removing AppArmor profile: ${PROFILE_NAME}"
    run apparmor_parser --remove "$PROFILE_FILE"
    echo "    Profile '${PROFILE_NAME}' removed from kernel."
    exit 0
fi

# ── Load path ─────────────────────────────────────────────────────────────────
echo "==> Loading AppArmor profile: ${PROFILE_FILE}"
echo "    Profile name: ${PROFILE_NAME}"

# Parse and load in enforcing mode
run apparmor_parser --replace --write-cache "$PROFILE_FILE"

if [[ "$DRY_RUN" == "false" ]]; then
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo "  ✓ AppArmor profile loaded: ${PROFILE_NAME}"
    echo ""
    echo "  Verify:"
    echo "    sudo apparmor_status | grep ${PROFILE_NAME}"
    echo ""
    echo "  Enable in config/default.yaml:"
    echo "    container:"
    echo "      apparmor_profile: ${PROFILE_NAME}"
    echo ""
    echo "  To persist across reboots:"
    echo "    sudo cp ${PROFILE_FILE} /etc/apparmor.d/${PROFILE_NAME}"
    echo "    sudo systemctl reload apparmor"
    echo ""
    echo "  To unload:"
    echo "    sudo bash infra/scripts/load-apparmor-profile.sh --unload"
    echo "═══════════════════════════════════════════════════════════════"
fi
