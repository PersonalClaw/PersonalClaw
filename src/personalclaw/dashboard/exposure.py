"""Is this instance internet-exposed? (REMOTE-USER-AUTH T4.1)

**One signal, two surfaces.** The plan's own coordination note with EXTERNAL-ACCESS says there
is one "this instance is internet-exposed" signal serving two surfaces (the human dashboard and
the inbound API). This module is that one signal, so `Secure`-cookie, CSP and forwarded-header
decisions cannot drift apart from each other or from the inbound surface's own boundary.

**Why not reuse `dashboard.url`.** It already exists, but it means "a URL to put in links we
send to Slack", and people legitimately set it to a LAN address or an `http://` host. Deriving
`Secure` from that would set a flag that makes the cookie undeliverable over plain http — the
user would be silently unable to log in, with nothing pointing at the cause. Exposure has to be
its own deliberate statement.

**Trusted proxies are the load-bearing part.** `X-Forwarded-Proto`/`X-Forwarded-For` are
attacker-controlled unless the peer that set them is one you named. Trusting them by shape
("looks like a private address") is the classic mistake: any container neighbour, any LAN
device, any SSRF-able local service is on a private address.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _cfg() -> Any:
    from personalclaw.config.loader import AppConfig

    return AppConfig.load()


def public_url(cfg: Any | None = None) -> str:
    """The configured public URL, or "" when this instance is not declared exposed.

    Prefers `dashboard.public_url` (this plan's field, describing the human dashboard) and falls
    back to `inbound.public_url` (MCP-READONLY-INBOUND's field). The fallback is deliberate: an
    operator who already declared their public URL for the inbound surface has already told us
    the box is exposed, and making them say it twice would mean the dashboard stayed unhardened
    on an instance already known to be reachable.
    """
    try:
        c = cfg if cfg is not None else _cfg()
        declared = str(getattr(c.dashboard, "public_url", "") or "").strip()
        if declared:
            return declared
        return str(getattr(c.inbound, "public_url", "") or "").strip()
    except Exception:  # noqa: BLE001
        logger.debug("could not read the public URL", exc_info=True)
        return ""


def is_exposed(cfg: Any | None = None) -> bool:
    """Whether the operator has declared this instance internet-exposed."""
    return bool(public_url(cfg))


def public_host(cfg: Any | None = None) -> str:
    """The host[:port] of the public URL, for the WS CSP entry. "" when not exposed."""
    url = public_url(cfg)
    if not url:
        return ""
    candidate = url if "://" in url else f"https://{url}"
    try:
        return str(urlparse(candidate).netloc or "")
    except ValueError:
        logger.warning("public_url is not parseable — ignoring it for the CSP")
        return ""


def is_https(cfg: Any | None = None) -> bool:
    """Whether the public URL is https.

    Drives the `Secure` cookie flag. An operator who declares an `http://` public URL gets an
    insecure-by-nature deployment, but NOT a broken one — setting `Secure` there would make the
    cookie undeliverable and the login silently impossible. The remote-access guide is where we
    say plainly that TLS termination is a precondition; the code refuses to make it unusable.
    """
    url = public_url(cfg)
    if not url:
        return False
    if "://" not in url:
        # A bare host (`pc.example.com`) is assumed https: the documented deployment terminates
        # TLS at the tunnel, and assuming the SAFE side of an ambiguity is the right default.
        return True
    return url.lower().startswith("https://")


def trusted_proxies(cfg: Any | None = None) -> list[str]:
    """The operator's declared trusted proxy addresses/CIDRs (empty = trust nothing)."""
    try:
        c = cfg if cfg is not None else _cfg()
        raw = getattr(c.dashboard, "trusted_proxies", []) or []
        return [str(x).strip() for x in raw if str(x).strip()]
    except Exception:  # noqa: BLE001
        logger.debug("could not read the trusted proxy list", exc_info=True)
        return []


def is_trusted_proxy(peer: str, cfg: Any | None = None) -> bool:
    """Whether *peer* is a configured trusted proxy.

    **Empty list means trust nothing**, which is the safe default and the reason this is a
    separate function rather than an inline check: "no proxies configured" must never
    accidentally read as "trust everyone", and a bug in one branch of an `if` shouldn't be able
    to make that happen quietly.

    Accepts single addresses and CIDR blocks. A malformed entry is skipped with a warning
    rather than widening or crashing the request path.
    """
    if not peer:
        return False
    allowed = trusted_proxies(cfg)
    if not allowed:
        return False
    try:
        addr = ipaddress.ip_address(peer.strip())
    except ValueError:
        return False
    for entry in allowed:
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            logger.warning("ignoring unparseable trusted_proxies entry %r", entry)
    return False
