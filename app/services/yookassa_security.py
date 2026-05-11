"""Security helpers for the YooKassa HTTP-notification (webhook) endpoint.

YooKassa do not sign their webhooks. The two recommended defences in
https://yookassa.ru/developers/using-api/webhooks are:

  1. Reject requests whose source IP is outside their published
     allowlist (the CIDR ranges baked in below).
  2. Independently fetch the payment via the YooKassa REST API and
     trust *its* `.status` over what the webhook body claims.

This module owns (1). Layer (2) is implemented inline in the webhook
handler because it pulls in DB/SQLAlchemy state.

The helpers stay free of FastAPI / SQLAlchemy imports so they can be
unit-tested in isolation.
"""
from __future__ import annotations

import ipaddress
from typing import Optional


# Published list as of 2026-05 (https://yookassa.ru/developers/using-api/webhooks#ip).
# Update if YooKassa changes the source ranges — failures will surface as
# legitimate webhooks bouncing with 403.
_YOOKASSA_ALLOWED_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "185.71.76.0/27",
        "185.71.77.0/27",
        "77.75.153.0/25",
        "77.75.156.11/32",
        "77.75.156.35/32",
        "77.75.154.128/25",
        "2a02:5180::/32",
    )
)


def _is_yookassa_ip(value: Optional[str]) -> bool:
    """True iff ``value`` parses as an IP that falls inside YooKassa's allowlist.

    Bad input (None, empty, garbage) returns False — caller treats those
    the same as a hostile IP.
    """
    if not value:
        return False
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(ip in net for net in _YOOKASSA_ALLOWED_NETWORKS)


def client_ip_from_headers(real_ip: Optional[str], forwarded_for: Optional[str]) -> Optional[str]:
    """Pull the originating client IP out of nginx-set headers.

    Our nginx config (deploy_vps.sh) sets X-Real-IP from $remote_addr,
    so that header is the trustworthy source. X-Forwarded-For is checked
    only as a fallback if the deployment ever drops X-Real-IP.
    """
    if real_ip:
        return real_ip.strip() or None
    if forwarded_for:
        first = forwarded_for.split(",", 1)[0].strip()
        return first or None
    return None
