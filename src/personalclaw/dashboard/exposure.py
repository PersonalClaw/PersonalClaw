"""Is this instance internet-exposed?

**One signal, two surfaces.** There is one "this instance is internet-exposed" signal serving
two surfaces (the human dashboard and the inbound API). This module is that one signal, so
`Secure`-cookie, CSP and forwarded-header decisions cannot drift apart from each other or from
the inbound surface's own boundary.

**Why not reuse `dashboard.url`.** It already exists, but it means "a URL to put in links we
send to Slack", and people legitimately set it to a LAN address or an `http://` host. Deriving
`Secure` from that would set a flag that makes the cookie undeliverable over plain http — the
user would be silently unable to log in, with nothing pointing at the cause. Exposure has to be
its own deliberate statement.

**Trusted proxies are the load-bearing part.** `X-Real-IP` — the one client-IP header
`token_auth._resolved_client_ip` reads — is attacker-controlled unless the peer that set it is
one you named. Trusting it by shape ("looks like a private address") is the classic mistake:
any container neighbour, any LAN device, any SSRF-able local service is on a private address.

**Why not `X-Forwarded-For`/`-Proto`.** Neither is read anywhere in `src/`, and this
docstring used to claim otherwise — the third surface carrying that stale promise after
`config/loader.py` and `docs/guides/remote-access.md`, which
`tests/test_forwarded_header_docs_match_code.py` now keeps honest. `X-Forwarded-For` is a *list*
whose trustworthy element depends on a hop count, and the resolved address feeds an admission
decision (the `PERSONALCLAW_BYPASS_LOCAL_NETWORKS` grant reads it through `is_private_network`),
so honoring it would widen who gets in — an owner call, not a docs fix. `X-Forwarded-Proto` has
no consumer to inform: `is_https()` below derives the `Secure`/`wss` posture from the operator's
own declared `public_url`, deliberately, so a caller cannot assert it per-request.
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

    Prefers `dashboard.public_url` (the dashboard's field, describing the human dashboard) and falls
    back to `external_access.public_url` (the inbound seam's field — `inbound.public_url` before
    the section was renamed). The fallback is deliberate: an operator who already declared their
    public URL for the inbound surface has already told us the box is exposed, and making them say
    it twice would mean the dashboard stayed unhardened on an instance already known to be
    reachable.
    """
    try:
        c = cfg if cfg is not None else _cfg()
        declared = str(getattr(c.dashboard, "public_url", "") or "").strip()
        if declared:
            return declared
        return str(getattr(c.external_access, "public_url", "") or "").strip()
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


#: The private address shapes the session rule takes for a proxy on an instance not declared
#: exposed (:func:`client_address`).
_PRIVATE_PEERS = ("127.", "10.", "172.1", "172.2", "172.3", "192.168.", "::1", "fc", "fd")


def client_address(request: Any, *, private_peers: bool = True) -> str:
    """The address of the client behind *request*: a proxy's `X-Real-IP` where the proxy is
    believed, the connection's own address otherwise. The one rule sessions bind to and the
    sign-in lockout counts by.

    nginx (or any reverse proxy in the same compose network) sees the gateway's container IP as
    the TCP remote, not the actual client, so a forwarded header is the only way to recover the
    real one.

    **An exposed instance tightens who may set it.** Once the operator declares this instance
    internet-exposed (`dashboard.public_url`), only a peer listed in `dashboard.trusted_proxies`
    is believed. Trusting a peer by shape ("starts with 10./172.1x/192.168.") is the classic
    mistake: on an exposed box every container neighbour, LAN device and SSRF-able local service
    sits on a private address, so any of them could set `X-Real-IP`.

    **Not exposed: the session rule is unchanged**, deliberately. Home/compose installs depend on
    the private-address heuristic for a session's binding. *private_peers* ``False`` drops that
    heuristic, and the sign-in lockout passes it: a caller on the LAN of an unexposed instance
    must not choose the address its failures are counted under, so there too only a configured
    trusted proxy is believed. With no trusted proxy configured, the lockout counts the
    connection's address, as it always did.
    """
    raw = getattr(request, "remote", None) or "unknown"
    forwarded = request.headers.get("X-Real-IP", "").strip()
    if not forwarded:
        return raw
    try:
        if is_exposed() or not private_peers:
            # Strict: an explicitly trusted peer, or the header is ignored entirely.
            if is_trusted_proxy(raw):
                return forwarded
            logger.debug("ignoring X-Real-IP from a peer that is not a trusted proxy")
            return raw
    except Exception:  # noqa: BLE001 — never let this decision break a request
        if not private_peers:
            return raw
        logger.debug("exposure check failed; using the legacy proxy heuristic", exc_info=True)
    return forwarded if raw.startswith(_PRIVATE_PEERS) else raw


def counts_every_visitor_together(request: Any) -> bool:
    """Whether the sign-in lockout counts *request* under a trusted proxy's own address, which
    it does when the proxy sends no `X-Real-IP` (a tunnel that sets another header): every
    visitor through that proxy then shares one count, and a lockout says so."""
    raw = getattr(request, "remote", None) or ""
    return not request.headers.get("X-Real-IP", "").strip() and is_trusted_proxy(raw)


def proxy_not_listed(request: Any) -> bool:
    """Whether *request* came through a proxy the sign-in lockout does not believe: a loopback or
    private peer that sent `X-Real-IP` but is not in `dashboard.trusted_proxies`. The lockout
    counts it under the proxy's own address, so every visitor through it shares one count until
    it is listed."""
    raw = getattr(request, "remote", None) or ""
    return (
        bool(request.headers.get("X-Real-IP", "").strip())
        and raw.startswith(_PRIVATE_PEERS)
        and not is_trusted_proxy(raw)
    )
