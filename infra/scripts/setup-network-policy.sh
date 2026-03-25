#!/usr/bin/env bash
# infra/scripts/setup-network-policy.sh — Create orchestrator-net Docker network
# and apply iptables egress filtering rules.
#
# WHAT THIS DOES:
#   1. Creates a Docker bridge network named 'orchestrator-net' (idempotent).
#   2. Discovers the network's CIDR subnet from Docker inspect.
#   3. Resolves api.anthropic.com to its current IPv4 addresses.
#   4. Adds iptables FORWARD ACCEPT rules for those IPs on TCP 443.
#   5. Blocks DNS (UDP/TCP 53) from the subnet (containers use --add-host to
#      resolve api.anthropic.com without DNS, eliminating DNS tunneling risk).
#   6. Drops all other FORWARD traffic from the orchestrator-net subnet.
#
# SECURITY MODEL:
#   Subnet-based rules (not interface-based) are used because the Docker bridge
#   interface name is assigned dynamically. Subnet filtering is both more robust
#   and more correct for FORWARD chain rules.
#
#   Containers on orchestrator-net can reach ONLY api.anthropic.com:443.
#   All other outbound TCP/UDP is dropped at the FORWARD chain.
#
# REQUIREMENTS:
#   - Linux host with iptables (macOS Docker Desktop does not support this).
#   - Run as root or with sudo.
#   - Docker Engine must be running.
#   - getent, dig, or host must be available for DNS resolution.
#
# USAGE:
#   sudo bash infra/scripts/setup-network-policy.sh [--dry-run] [--refresh]
#
# DNS IP ROTATION WARNING:
#   This script resolves api.anthropic.com at setup time. Anthropic's CDN
#   (Cloudflare) may rotate IPs. Re-run with --refresh if container API calls fail:
#     sudo bash infra/scripts/setup-network-policy.sh --refresh

set -euo pipefail

# ── Constants ────────────────────────────────────────────────────────────────
NETWORK_NAME="orchestrator-net"
BRIDGE_OPTION_NAME="br-orchestrator"
ANTHROPIC_HOST="api.anthropic.com"
CHAIN="FORWARD"

# ── Argument parsing ─────────────────────────────────────────────────────────
DRY_RUN=false
REFRESH=false

usage() {
    cat <<EOF
Usage: sudo $(basename "$0") [--dry-run] [--refresh]

Options:
  --dry-run   Print intended actions without executing them
  --refresh   Flush existing orchestrator-net FORWARD rules and recreate them
  --help      Show this message

IMPORTANT: Requires root (sudo). Linux only (iptables not available on macOS).

DNS IP ROTATION: api.anthropic.com is fronted by Cloudflare CDN — IPs rotate.
  Re-run with --refresh if container API calls begin failing:
    sudo bash infra/scripts/setup-network-policy.sh --refresh
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --refresh) REFRESH=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "ERROR: Unknown argument: $1" >&2; usage; exit 1 ;;
    esac
done

# ── Platform check ────────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Linux" ]]; then
    echo "WARNING: This script is for Linux only. macOS Docker Desktop does not" >&2
    echo "         support host iptables rules for container network filtering." >&2
    if [[ "$DRY_RUN" == "false" ]]; then
        exit 1
    fi
fi

# ── Root check ───────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 && "$DRY_RUN" == "false" ]]; then
    echo "ERROR: This script requires root privileges for iptables operations." >&2
    echo "       Re-run: sudo bash infra/scripts/setup-network-policy.sh $*" >&2
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

# ── Helper: idempotent iptables insert ────────────────────────────────────────
# Inserts an iptables rule only if it does not already exist.
ipt_insert_if_missing() {
    local check_args=()
    for arg in "$@"; do
        if [[ "$arg" == "-I" ]]; then
            check_args+=("-C")
        else
            check_args+=("$arg")
        fi
    done

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "[dry-run] iptables $*"
        return 0
    fi

    if iptables "${check_args[@]}" &>/dev/null 2>&1; then
        echo "    [exists] iptables $*"
    else
        iptables "$@"
        echo "    [added]  iptables $*"
    fi
}

# ── Step 1: Create Docker bridge network ─────────────────────────────────────
echo "==> Checking Docker network: ${NETWORK_NAME}"

if docker network inspect "${NETWORK_NAME}" &>/dev/null 2>&1; then
    echo "    Network '${NETWORK_NAME}' already exists — skipping creation."
else
    echo "    Creating Docker bridge network '${NETWORK_NAME}'..."
    # --ipv6=false explicitly disables IPv6 on this bridge network.
    # Without this, Docker may assign an IPv6 ULA prefix (fd00::/64) when the
    # daemon has global IPv6 enabled, allowing containers to bypass the IPv4-only
    # iptables FORWARD rules below.  Disabling IPv6 at the network level is the
    # primary control; ip6tables DROP rules added later provide defense-in-depth.
    run docker network create \
        --driver bridge \
        --ipv6=false \
        --opt "com.docker.network.bridge.name=${BRIDGE_OPTION_NAME}" \
        --opt "com.docker.network.bridge.enable_icc=false" \
        --opt "com.docker.network.bridge.enable_ip_masquerade=true" \
        "${NETWORK_NAME}"
    echo "    Created: ${NETWORK_NAME}"
fi

# ── Step 2: Discover subnet ───────────────────────────────────────────────────
echo "==> Discovering subnet for '${NETWORK_NAME}'..."

if [[ "$DRY_RUN" == "false" ]]; then
    SUBNET=$(docker network inspect "${NETWORK_NAME}" \
        --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null || echo "")

    if [[ -z "$SUBNET" ]]; then
        echo "ERROR: Could not determine subnet for '${NETWORK_NAME}'." >&2
        echo "       Ensure the network exists: docker network ls" >&2
        exit 1
    fi
else
    SUBNET="172.20.0.0/16"
    echo "[dry-run] Using placeholder subnet: ${SUBNET}"
fi

echo "    Subnet: ${SUBNET}"

# ── Step 3: Resolve api.anthropic.com IPs ─────────────────────────────────────
echo "==> Resolving ${ANTHROPIC_HOST} to IPv4 addresses..."

ANTHROPIC_IPS=""
if command -v getent &>/dev/null; then
    ANTHROPIC_IPS=$(getent hosts "${ANTHROPIC_HOST}" 2>/dev/null \
        | awk '{print $1}' \
        | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' \
        | sort -u || true)
elif command -v dig &>/dev/null; then
    ANTHROPIC_IPS=$(dig +short "${ANTHROPIC_HOST}" 2>/dev/null \
        | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' \
        | sort -u || true)
elif command -v host &>/dev/null; then
    ANTHROPIC_IPS=$(host -t A "${ANTHROPIC_HOST}" 2>/dev/null \
        | awk '/has address/ {print $4}' \
        | sort -u || true)
fi

if [[ -z "$ANTHROPIC_IPS" ]]; then
    echo "ERROR: Could not resolve ${ANTHROPIC_HOST} to any IPv4 addresses." >&2
    echo "       Check network connectivity and DNS: dig ${ANTHROPIC_HOST}" >&2
    exit 1
fi

echo "    Resolved IPs:"
while IFS= read -r ip; do
    echo "      ${ip}"
done <<< "${ANTHROPIC_IPS}"

# ── Step 4: Remove existing orchestrator rules (on --refresh) ─────────────────
if [[ "$REFRESH" == "true" ]]; then
    echo "==> Removing existing orchestrator-net FORWARD rules (--refresh)..."
    if [[ "$DRY_RUN" == "false" ]]; then
        # Remove all FORWARD rules that contain our orchestrator-net comment
        iptables-save | grep "orchestrator-net" | grep "^-A FORWARD" | while IFS= read -r rule; do
            delete_rule="${rule/-A FORWARD/-D FORWARD}"
            iptables $delete_rule 2>/dev/null || true
            echo "    [removed] iptables ${delete_rule}"
        done
    else
        echo "[dry-run] iptables -D FORWARD ... (all orchestrator-net rules)"
    fi
fi

# ── Step 5: Allow ESTABLISHED/RELATED return traffic ─────────────────────────
echo ""
echo "==> Installing iptables FORWARD rules..."

ipt_insert_if_missing -I "${CHAIN}" \
    -s "${SUBNET}" \
    -m conntrack --ctstate ESTABLISHED,RELATED \
    -j ACCEPT \
    -m comment --comment "orchestrator-net: allow return traffic"

# ── Step 6: Block DNS (UDP/TCP 53) from orchestrator-net ─────────────────────
# Docker's embedded DNS resolver runs on the bridge gateway (e.g. 172.20.0.1)
# and is processed in the INPUT/OUTPUT chains, NOT FORWARD. So blocking DNS
# in FORWARD prevents containers from reaching external recursive resolvers
# (8.8.8.8, 1.1.1.1, etc.) while Docker's embedded resolver still works.
# Containers use --add-host (injected by ContainerRuntime) so DNS is not needed.
ipt_insert_if_missing -I "${CHAIN}" \
    -s "${SUBNET}" \
    -p udp --dport 53 \
    -j DROP \
    -m comment --comment "orchestrator-net: block external DNS UDP"

ipt_insert_if_missing -I "${CHAIN}" \
    -s "${SUBNET}" \
    -p tcp --dport 53 \
    -j DROP \
    -m comment --comment "orchestrator-net: block external DNS TCP"

# ── Step 7: Allow TCP 443 to each resolved Anthropic IP ──────────────────────
while IFS= read -r ip; do
    ipt_insert_if_missing -I "${CHAIN}" \
        -s "${SUBNET}" \
        -d "${ip}" \
        -p tcp --dport 443 \
        -j ACCEPT \
        -m comment --comment "orchestrator-net: allow HTTPS to ${ANTHROPIC_HOST} (${ip})"
done <<< "${ANTHROPIC_IPS}"

# ── Step 8: Drop all other FORWARD traffic from orchestrator-net ──────────────
# MUST come AFTER the ACCEPT rules. Use -A (append) so it sits at the end of
# the chain after all the -I (insert at top) ACCEPT rules above.
DROP_COMMENT="orchestrator-net: drop all other outbound"
if [[ "$DRY_RUN" == "false" ]]; then
    if iptables -C "${CHAIN}" -s "${SUBNET}" -j DROP \
        -m comment --comment "${DROP_COMMENT}" &>/dev/null 2>&1; then
        echo "    [exists] DROP rule for all other traffic from ${SUBNET}"
    else
        iptables -A "${CHAIN}" \
            -s "${SUBNET}" \
            -j DROP \
            -m comment --comment "${DROP_COMMENT}"
        echo "    [added]  DROP rule for all other traffic from ${SUBNET}"
    fi
else
    echo "[dry-run] iptables -A ${CHAIN} -s ${SUBNET} -j DROP -m comment --comment '${DROP_COMMENT}'"
fi

# ── Step 9: IPv6 defense-in-depth — drop ALL FORWARD from orchestrator bridge ─
# Primary control: the network was created with --ipv6=false, so Docker should
# not assign an IPv6 prefix to orchestrator-net containers.
# Defense-in-depth: if the Docker daemon has IPv6 enabled globally (daemon.json
# "ipv6":true) it may override the per-network flag.  These ip6tables rules
# unconditionally DROP all IPv6 FORWARD traffic from the bridge interface,
# closing the gap where IPv6 packets would bypass the IPv4-only iptables rules.
#
# We use the bridge interface name (br-orchestrator) rather than subnet-based
# rules because an IPv6 prefix may not be assigned yet (or may change).
echo ""
echo "==> Installing ip6tables IPv6 FORWARD drop rules..."

if ! command -v ip6tables &>/dev/null; then
    echo "    [skip] ip6tables not found — IPv6 filtering not available on this host."
    echo "           IPv6 is disabled on the network level (--ipv6=false) as primary control."
elif [[ "$(uname -s)" != "Linux" ]]; then
    echo "    [skip] ip6tables is Linux-only."
else
    IP6_BRIDGE_IFACE="${BRIDGE_OPTION_NAME}"
    IP6_COMMENT="orchestrator-net: drop all IPv6 FORWARD"

    # Drop IPv6 FORWARD traffic arriving on (from) the bridge interface
    if [[ "$DRY_RUN" == "false" ]]; then
        if ip6tables -C "${CHAIN}" -i "${IP6_BRIDGE_IFACE}" -j DROP \
            -m comment --comment "${IP6_COMMENT}" &>/dev/null 2>&1; then
            echo "    [exists] ip6tables DROP rule for inbound from ${IP6_BRIDGE_IFACE}"
        else
            ip6tables -I "${CHAIN}" \
                -i "${IP6_BRIDGE_IFACE}" \
                -j DROP \
                -m comment --comment "${IP6_COMMENT}"
            echo "    [added]  ip6tables -I ${CHAIN} -i ${IP6_BRIDGE_IFACE} -j DROP"
        fi

        # Drop IPv6 FORWARD traffic departing on (to) the bridge interface
        if ip6tables -C "${CHAIN}" -o "${IP6_BRIDGE_IFACE}" -j DROP \
            -m comment --comment "${IP6_COMMENT}" &>/dev/null 2>&1; then
            echo "    [exists] ip6tables DROP rule for outbound to ${IP6_BRIDGE_IFACE}"
        else
            ip6tables -I "${CHAIN}" \
                -o "${IP6_BRIDGE_IFACE}" \
                -j DROP \
                -m comment --comment "${IP6_COMMENT}"
            echo "    [added]  ip6tables -I ${CHAIN} -o ${IP6_BRIDGE_IFACE} -j DROP"
        fi
    else
        echo "[dry-run] ip6tables -I ${CHAIN} -i ${IP6_BRIDGE_IFACE} -j DROP (inbound)"
        echo "[dry-run] ip6tables -I ${CHAIN} -o ${IP6_BRIDGE_IFACE} -j DROP (outbound)"
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY-RUN] No changes applied."
else
    echo "  ✓ Network policy applied at $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo ""
    echo "  Current FORWARD rules for subnet ${SUBNET}:"
    iptables -L "${CHAIN}" -n --line-numbers 2>/dev/null \
        | grep -E "${SUBNET}|orchestrator-net" || echo "  (none found)"
fi
echo ""
echo "  Network:        ${NETWORK_NAME}"
echo "  Subnet:         ${SUBNET}"
echo "  Allowed egress: ${ANTHROPIC_HOST}:443 TCP only"
echo "  Blocked:        DNS (UDP/TCP 53), all other FORWARD traffic"
echo "  IPv6:           Disabled at network level (--ipv6=false) + ip6tables DROP"
echo ""
echo "  ⚠  IP ROTATION: ${ANTHROPIC_HOST} is fronted by Cloudflare CDN."
echo "     IPs are resolved at setup time and may rotate. Re-run --refresh if"
echo "     container API calls start failing:"
echo "       sudo bash infra/scripts/setup-network-policy.sh --refresh"
echo ""
echo "  To persist rules across reboots:"
echo "  To persist iptables rules across reboots (choose your distro):"
echo "    Debian/Ubuntu:  sudo apt install iptables-persistent"
echo "                    sudo netfilter-persistent save"
echo "    RHEL/CentOS:    sudo service iptables save"
echo "    Manual:         sudo iptables-save  > /etc/iptables/rules.v4"
echo "                    sudo ip6tables-save > /etc/iptables/rules.v6"
echo ""
echo "  To persist via systemd (distro-agnostic one-time setup):"
echo "    sudo bash infra/scripts/install-iptables-restore-service.sh"
echo "═══════════════════════════════════════════════════════════════"
