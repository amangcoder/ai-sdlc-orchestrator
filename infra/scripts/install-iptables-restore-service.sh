#!/usr/bin/env bash
# infra/scripts/install-iptables-restore-service.sh
#
# Installs a distro-agnostic systemd service that restores iptables and
# ip6tables rules on every boot.  Rules are saved from the current live state
# immediately after setup-network-policy.sh has been run.
#
# WHAT IT DOES:
#   1. Saves current iptables  rules to /etc/iptables/rules.v4
#   2. Saves current ip6tables rules to /etc/iptables/rules.v6
#   3. Installs /etc/systemd/system/iptables-restore-orchestrator.service
#      (runs iptables-restore + ip6tables-restore at boot, Before=docker.service)
#   4. Enables and starts the service
#
# REQUIREMENTS:
#   - Linux host with systemd
#   - iptables / ip6tables installed
#   - Run AFTER setup-network-policy.sh has applied the rules
#   - Run as root or with sudo
#
# USAGE:
#   sudo bash infra/scripts/install-iptables-restore-service.sh [--dry-run] [--uninstall]
#
# VERIFICATION:
#   sudo systemctl status iptables-restore-orchestrator
#   sudo iptables -L FORWARD -n | grep orchestrator-net
#
# UNINSTALL:
#   sudo bash infra/scripts/install-iptables-restore-service.sh --uninstall

set -euo pipefail

RULES_V4="/etc/iptables/rules.v4"
RULES_V6="/etc/iptables/rules.v6"
SERVICE_NAME="iptables-restore-orchestrator"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
DRY_RUN=false
UNINSTALL=false

# ── Argument parsing ──────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: sudo $(basename "$0") [--dry-run] [--uninstall]

Options:
  --dry-run    Print intended actions without executing
  --uninstall  Remove the systemd service and saved rule files
  --help       Show this message

Prerequisites:
  Run setup-network-policy.sh FIRST — this script saves its output.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)   DRY_RUN=true;   shift ;;
        --uninstall) UNINSTALL=true; shift ;;
        --help|-h)   usage; exit 0 ;;
        *) echo "ERROR: Unknown argument: $1" >&2; usage; exit 1 ;;
    esac
done

# ── Platform checks ───────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Linux" ]]; then
    echo "ERROR: This script is Linux-only (requires systemd + iptables)." >&2
    exit 1
fi

if [[ $EUID -ne 0 && "$DRY_RUN" == "false" ]]; then
    echo "ERROR: This script requires root privileges." >&2
    echo "       Re-run: sudo bash infra/scripts/install-iptables-restore-service.sh $*" >&2
    exit 1
fi

if ! command -v systemctl &>/dev/null; then
    echo "ERROR: systemctl not found. This script requires systemd." >&2
    exit 1
fi

if ! command -v iptables &>/dev/null; then
    echo "ERROR: iptables not found. Install it first." >&2
    exit 1
fi

# ── Helper: run or echo ───────────────────────────────────────────────────────
run() {
    if [[ "$DRY_RUN" == "true" ]]; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

# ── Uninstall path ────────────────────────────────────────────────────────────
if [[ "$UNINSTALL" == "true" ]]; then
    echo "==> Uninstalling ${SERVICE_NAME}..."
    run systemctl stop    "${SERVICE_NAME}" 2>/dev/null || true
    run systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    run rm -f "${SERVICE_FILE}"
    run rm -f "${RULES_V4}" "${RULES_V6}"
    run systemctl daemon-reload
    echo "  Removed: ${SERVICE_FILE}"
    echo "  Removed: ${RULES_V4}, ${RULES_V6}"
    echo "  Service '${SERVICE_NAME}' stopped and disabled."
    exit 0
fi

# ── Verify orchestrator-net rules exist before saving ─────────────────────────
echo "==> Verifying orchestrator-net FORWARD rules are present..."
if [[ "$DRY_RUN" == "false" ]]; then
    if ! iptables -L FORWARD -n 2>/dev/null | grep -q "orchestrator-net"; then
        echo "ERROR: No orchestrator-net rules found in iptables FORWARD chain." >&2
        echo "       Run setup-network-policy.sh first, then re-run this script." >&2
        exit 1
    fi
    echo "    OK: orchestrator-net rules are present"
else
    echo "[dry-run] Skipping rule presence check"
fi

# ── Save rules ────────────────────────────────────────────────────────────────
echo "==> Saving iptables rules to ${RULES_V4}..."
run mkdir -p "$(dirname "${RULES_V4}")"

if [[ "$DRY_RUN" == "false" ]]; then
    iptables-save > "${RULES_V4}"
    echo "    Saved: ${RULES_V4}"
else
    echo "[dry-run] iptables-save > ${RULES_V4}"
fi

echo "==> Saving ip6tables rules to ${RULES_V6}..."
if command -v ip6tables &>/dev/null; then
    if [[ "$DRY_RUN" == "false" ]]; then
        ip6tables-save > "${RULES_V6}"
        echo "    Saved: ${RULES_V6}"
    else
        echo "[dry-run] ip6tables-save > ${RULES_V6}"
    fi
else
    echo "    [skip] ip6tables not found — skipping IPv6 rule save"
fi

# ── Install systemd service ───────────────────────────────────────────────────
echo "==> Installing systemd service: ${SERVICE_FILE}..."

SERVICE_CONTENT="[Unit]
Description=Restore iptables rules for orchestrator-net container isolation
# Must run after the network is up but before Docker starts,
# so that the FORWARD rules are in place before containers attach.
After=network-pre.target
Before=docker.service
DefaultDependencies=no

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'iptables-restore  < ${RULES_V4} 2>/dev/null || true'
ExecStart=/bin/sh -c 'ip6tables-restore < ${RULES_V6} 2>/dev/null || true'
ExecStop=/bin/sh -c 'iptables-save  > ${RULES_V4}'
ExecStop=/bin/sh -c 'ip6tables-save > ${RULES_V6}'

[Install]
WantedBy=multi-user.target
"

if [[ "$DRY_RUN" == "false" ]]; then
    echo "${SERVICE_CONTENT}" > "${SERVICE_FILE}"
    chmod 644 "${SERVICE_FILE}"
    echo "    Wrote: ${SERVICE_FILE}"
else
    echo "[dry-run] Would write service file to ${SERVICE_FILE}:"
    echo "---"
    echo "${SERVICE_CONTENT}"
    echo "---"
fi

# ── Enable and start service ──────────────────────────────────────────────────
echo "==> Enabling and starting ${SERVICE_NAME}..."
run systemctl daemon-reload
run systemctl enable "${SERVICE_NAME}"
run systemctl start  "${SERVICE_NAME}"

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY-RUN] No changes applied."
else
    echo "  ✓ iptables persistence installed at $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo ""
    echo "  Service:     ${SERVICE_NAME}"
    echo "  Rules saved: ${RULES_V4}"
    echo "               ${RULES_V6}"
    echo "  Service file:${SERVICE_FILE}"
    echo ""
    echo "  Verification:"
    echo "    sudo systemctl status ${SERVICE_NAME}"
    echo "    sudo iptables -L FORWARD -n | grep orchestrator-net"
    echo ""
    echo "  To uninstall:"
    echo "    sudo bash infra/scripts/install-iptables-restore-service.sh --uninstall"
    echo ""
    echo "  NOTE: Rules are re-saved on service stop (e.g. on shutdown),"
    echo "  so new IPs added by --refresh are automatically persisted."
fi
echo "═══════════════════════════════════════════════════════════════"
