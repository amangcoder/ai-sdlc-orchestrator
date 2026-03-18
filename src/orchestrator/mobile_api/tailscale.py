"""Tailscale IP detection for mobile API server binding.

Detects the machine's active Tailscale IPv4 address by scanning all network
interfaces for addresses in the 100.64.0.0/10 CGNAT range used by Tailscale.
Works transparently on both Linux (tailscale0 interface) and macOS (utun*).
"""

from __future__ import annotations

import ipaddress
import logging
import socket

logger = logging.getLogger(__name__)

# Tailscale uses 100.64.0.0/10 CGNAT address space
_TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def detect_tailscale_ip() -> str | None:
    """Detect the active Tailscale IPv4 address.

    Scans all network interfaces using psutil and returns the first IPv4
    address found in the 100.64.0.0/10 CIDR range.

    Returns:
        The Tailscale IPv4 address as a string, or None if not found.
    """
    try:
        import psutil

        for _iface_name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                # Only IPv4 addresses
                if addr.family != socket.AF_INET:
                    continue
                try:
                    ip = ipaddress.ip_address(addr.address)
                    if ip in _TAILSCALE_NETWORK:
                        return str(ip)
                except ValueError:
                    continue
    except ImportError:
        logger.warning("psutil not installed; cannot detect Tailscale interface")
    except Exception as exc:
        logger.warning("Error detecting Tailscale IP: %s", exc)

    return None


def get_bind_host() -> str:
    """Return the host address to bind the mobile API server.

    Returns the Tailscale IP if a Tailscale interface is active,
    otherwise falls back to 0.0.0.0 (all interfaces) with a warning.
    """
    ip = detect_tailscale_ip()
    if ip:
        logger.info("Tailscale interface detected, binding to %s", ip)
        return ip
    else:
        logger.warning(
            "Tailscale interface not found; falling back to 0.0.0.0 "
            "(server will be accessible on all interfaces)"
        )
        return "0.0.0.0"
