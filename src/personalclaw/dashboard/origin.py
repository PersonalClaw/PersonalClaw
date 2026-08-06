"""Shared origin-validation helpers for CSRF and WebSocket checks.

Centralises dashboard URL parsing, bind-address resolution, origin-set
construction, and per-request origin validation so that ``server.py``
(CSRF middleware), ``ws.py`` (WebSocket handshake), and ``gateway.py``
(startup messages) all share a single source of truth.

The only user-facing config is ``dashboard.url`` — a single URL like
``http://my-host.example.com:8080``.  Everything
else (port, bind address, allowed origins) is derived from it.
"""

import ipaddress
import logging
import os
import shutil
import socket
from collections.abc import Iterable
from urllib.parse import parse_qs, quote, urlparse

from aiohttp import web

from personalclaw.auth.modes import AuthConfig, AuthMode, effective_bind
from personalclaw.config.loader import _DEFAULT_PORT

logger = logging.getLogger(__name__)

_BIND_LOCAL = "127.0.0.1"
_BIND_ALL = "0.0.0.0"

# Explicit corp-host escape hatch. Set to ``0.0.0.0`` (or any host) to
# override the bind decision derived from ``AuthConfig``. Operators in
# Development proxy environments (e.g. Gitpod, Codespaces) set this to expose
# the gateway on a non-loopback interface.
_BIND_HOST_ENV = "PERSONALCLAW_BIND_HOST"


# ---------------------------------------------------------------------------
# Hostname / IP helpers
# ---------------------------------------------------------------------------


def machine_hostname() -> str | None:
    """Return the machine hostname, or ``None`` on failure."""
    try:
        return socket.gethostname()
    except Exception:
        return None


def is_loopback(host: str) -> bool:
    """Return ``True`` if *host* is a loopback address (127.0.0.1, ::1, etc.)."""
    if host in ("localhost", "127.0.0.1", "::1", "personalclaw.localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_private_network(host: str) -> bool:
    """Return ``True`` if *host* is a non-public address (loopback, RFC1918, link-local,
    ULA, multicast, reserved, unspecified).

    Used by the optional ``PERSONALCLAW_BYPASS_LOCAL_NETWORKS`` token-auth bypass so
    requests from trusted home/dev LANs can skip the token gate. Delegates to
    ``net.guard.classify_host`` — the ONE authoritative "is this IP public" table shared
    with the outbound egress guard — so inbound and outbound agree on what "private" means
    (the old local definition covered only private+link-local, missing e.g. the
    IPv4-mapped-IPv6 case the shared classifier handles).
    """
    if is_loopback(host):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    from personalclaw.net.guard import classify_host

    return not classify_host(host).public


# ---------------------------------------------------------------------------
# Dashboard URL parsing
# ---------------------------------------------------------------------------


def parse_dashboard_url(url: str) -> tuple[str, int]:
    """Parse ``dashboard.url`` into ``(hostname, port)``.

    Returns ``("", _DEFAULT_PORT)`` when *url* is empty.
    ``PERSONALCLAW_PORT`` env var always overrides the port (dev mode).
    """
    if not url:
        host, port = "", _DEFAULT_PORT
    else:
        url = _ensure_scheme(url)
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or _DEFAULT_PORT
    env_port = os.environ.get("PERSONALCLAW_PORT")
    if env_port:
        try:
            port = int(env_port)
        except ValueError:
            logger.warning(
                "PERSONALCLAW_PORT=%r is not a valid integer; using port %d from config",
                env_port,
                port,
            )
    return host, port


def _ensure_scheme(url: str) -> str:
    """Prepend ``http://`` if *url* has no scheme."""
    return url if "://" in url else f"http://{url}"


def dashboard_origin(url: str) -> str:
    """Return the browser-facing origin for *url*, or ``""`` if invalid.

    Reuses the same scheme-defaulting logic as :func:`parse_dashboard_url`
    so that bare hostnames (``myhost:8080``) are normalised to ``http://``.
    Default ports (80 for http, 443 for https) are stripped to match
    browser ``Origin`` header behaviour.
    """
    if not url:
        return ""
    url = _ensure_scheme(url)
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        logger.warning("Ignoring malformed dashboard_url: %s", url)
        return ""
    if not host:
        return ""
    if scheme not in ("http", "https"):
        logger.warning("Ignoring non-HTTP dashboard_url scheme: %s", scheme)
        return ""
    # urlparse strips [] from IPv6 — re-wrap to match browser Origin header
    if ":" in host:
        host = f"[{host}]"
    default_port = {"http": 80, "https": 443}.get(scheme)
    if port == default_port:
        port = None
    return f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"


# ---------------------------------------------------------------------------
# Development proxy detection
# ---------------------------------------------------------------------------


def devspaces_proxy_url(port: int) -> str | None:
    """Return the DevSpaces proxy base URL, or ``None`` if not running in DevSpaces."""
    ds_id = os.environ.get("DEVPROXY_ID")
    region = os.environ.get("AWS_REGION")
    if ds_id and region:
        return f"https://{ds_id}--{port}.{region}.prod.proxy.devproxy.example.com"
    return None


# ---------------------------------------------------------------------------
# Bind-host resolution
# ---------------------------------------------------------------------------


def resolve_bind_host(auth_cfg: AuthConfig | None = None) -> str:
    """Return the TCP bind address string for aiohttp.

    Resolution order:

    1. ``PERSONALCLAW_BIND_HOST`` env var (explicit corp-host escape hatch)
       — preserved for dev proxy / reverse-proxy setups
       where the gateway must listen on a non-loopback interface.
    2. ``effective_bind(auth_cfg)`` — when ``auth_cfg.mode == AuthMode.NONE``
       this always returns ``127.0.0.1`` (loopback invariant: auth-disabled
       must never bind a non-loopback interface).
    3. ``127.0.0.1`` when *auth_cfg* is omitted.
    """
    env_host = os.environ.get(_BIND_HOST_ENV, "").strip()
    if env_host:
        return env_host
    if auth_cfg is None:
        return _BIND_LOCAL
    return effective_bind(auth_cfg)


def is_local_bind(bind_host: str) -> bool:
    """Return ``True`` if *bind_host* is the loopback address."""
    return bind_host == _BIND_LOCAL


# ---------------------------------------------------------------------------
# Tailnet reachability (MOBILE-COMPANION S1)
# ---------------------------------------------------------------------------

# Tailscale assigns every node an address in the documented CGNAT range
# 100.64.0.0/10 (RFC 6598). That address is the stable, dependency-free signal
# that this machine is *on a tailnet right now* — no tailscale library, no CLI
# call required. The presence of the ``tailscale`` binary is corroborating
# evidence only (it may be installed but logged out / down).
_TAILNET_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _local_addresses() -> list[str]:
    """Best-effort enumeration of this machine's local IPv4/IPv6 addresses.

    Uses ``getaddrinfo(gethostname())`` — stdlib only, no network round-trip.
    Never raises: address discovery failing is an empty list, not an error (the
    caller treats "no tailnet found" the same either way).
    """
    addrs: set[str] = set()
    try:
        host = socket.gethostname()
    except Exception:
        return []
    try:
        for info in socket.getaddrinfo(host, None):
            sockaddr = info[4]
            if sockaddr and sockaddr[0]:
                addrs.add(str(sockaddr[0]))
    except Exception:
        pass
    return sorted(addrs)


def tailnet_ip(addresses: Iterable[str] | None = None) -> str:
    """Return this machine's tailnet address (100.64.0.0/10), or ``""`` if none.

    *addresses* is an injectable seam: pass an explicit iterable to test without
    touching the network (a fixture feeds ``["100.101.102.103"]`` for present,
    ``["192.168.1.5"]`` for absent). When omitted, local addresses are discovered
    via :func:`_local_addresses`.
    """
    candidates = list(addresses) if addresses is not None else _local_addresses()
    for addr in candidates:
        # Strip a zone id (e.g. fe80::1%en0) before parsing.
        bare = addr.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(bare)
        except ValueError:
            continue
        if ip.version == 4 and ip in _TAILNET_CGNAT:
            return str(ip)
    return ""


def tailscale_cli_present() -> bool:
    """Return ``True`` if the ``tailscale`` CLI is on PATH.

    Corroborating evidence only — the CLI being installed does NOT mean this
    machine is currently on a tailnet (it may be logged out). :func:`tailnet_ip`
    is the authoritative "on a tailnet now" signal.
    """
    return shutil.which("tailscale") is not None


def auth_is_off(auth_cfg: AuthConfig | None = None) -> bool:
    """Return ``True`` when the gateway serves requests with NO authentication.

    Two ways auth is genuinely off: ``AuthMode.NONE`` (pass-through), or the
    blanket ``PERSONALCLAW_DEV_NO_AUTH=1`` middleware skip. Both are dev-only and
    both are normally safe because ``effective_bind`` forces NONE to loopback —
    but the ``PERSONALCLAW_BIND_HOST`` escape hatch can override that bind, which
    is exactly the exposed-without-auth misconfiguration the reachability probe
    warns about. ``local_token``/``api_key``/``oauth2`` are NOT "off": a
    non-loopback bind under those still requires a token/credential.
    """
    if os.environ.get("PERSONALCLAW_DEV_NO_AUTH") == "1":
        return True
    cfg = auth_cfg if auth_cfg is not None else AuthConfig.from_env()
    return cfg.mode == AuthMode.NONE


# ---------------------------------------------------------------------------
# Dashboard host / URL helpers
# ---------------------------------------------------------------------------


def resolve_dashboard_host(local_only: bool, configured_host: str = "") -> str:
    """Return the hostname users should use to reach the dashboard.

    For the auto-open URL (browser on the same machine), ``localhost`` is always
    correct — it works whether binding to 127.0.0.1 or 0.0.0.0. Using
    ``machine_hostname()`` was wrong: raw system hostnames (e.g. Docker-style
    ``b0f1d879fa5a``) aren't DNS-resolvable from the browser → the auto-open tab
    can't connect. The machine hostname is only useful for the "Remote: ssh -L…"
    log hint (``format_dashboard_urls`` handles that separately).
    """
    if configured_host:
        return configured_host
    # Prefer personalclaw.localhost (nice subdomain) → localhost fallback.
    # This is correct for BOTH local_only=True (loopback bind) and
    # local_only=False (0.0.0.0 bind) — the browser is local either way.
    try:
        socket.getaddrinfo("personalclaw.localhost", None)
        return "personalclaw.localhost"
    except socket.gaierror:
        return "localhost"


def build_dashboard_url(base_url: str, token: str = "", *, local_only: bool = True) -> str:
    """Build the authenticated dashboard URL."""
    if local_only is not True and not token:
        raise ValueError("token is required when dashboard is not local-only")
    return f"{base_url}?token={quote(token, safe='')}" if token else base_url


def format_dashboard_urls(
    authed_url: str,
    *,
    port: int,
    local_only: bool = True,
    has_custom_host: bool = False,
) -> list[str]:
    """Return startup log lines describing how to reach the dashboard."""
    parsed_query = urlparse(authed_url).query
    _qs = f"?{parsed_query}" if parsed_query else ""
    if local_only is not True and "token" not in parse_qs(parsed_query):
        raise ValueError("token is required when dashboard is not local-only")
    _is_remote = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))

    if _is_remote:
        mh = machine_hostname() or "localhost"
        lines: list[str] = [
            f"Dashboard: ssh -L {port}:localhost:{port} {mh}",
            f"             then open http://localhost:{port}{_qs}",
        ]
    else:
        lines = ["Dashboard:", f"   {authed_url}"]

    if local_only and not has_custom_host and not _is_remote:
        mh_local = machine_hostname()
        if mh_local and mh_local != "localhost":
            try:
                ip = socket.gethostbyname(mh_local)
                if ip and ip != "127.0.0.1":
                    lines.append(f"Remote:    ssh -L {port}:localhost:{port} {mh_local}")
            except Exception:
                pass

    proxy = devspaces_proxy_url(port)
    if proxy and not local_only:
        lines.append(f"Proxy:     {proxy}{_qs}")

    if _is_remote:
        lines.append("Run 24/7:  see docs/REMOTE_DESKTOP_SETUP.md for systemd service setup")

    return lines


# ---------------------------------------------------------------------------
# Allowed-origin set
# ---------------------------------------------------------------------------


def build_allowed_origins(
    port: int, local_only: bool, configured_host: str = "", dashboard_url: str = ""
) -> set[str]:
    """Compute the set of allowed origins for the dashboard.

    When *dashboard_url* is provided, its origin (scheme + host + port)
    is added as-is so that reverse-proxy setups (e.g. Caddy with TLS on
    a custom domain) pass the CSRF check without code changes.
    """
    origins: set[str] = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://personalclaw.localhost:{port}",
    }
    if os.environ.get("PERSONALCLAW_HOME"):
        origins.add("http://localhost:3000")
    if configured_host:
        origins.add(f"http://{configured_host}:{port}")
    if dashboard_url:
        origin = dashboard_origin(dashboard_url)
        if origin:
            origins.add(origin)
    if not local_only:
        mh = machine_hostname()
        if mh:
            origins.add(f"http://{mh}:{port}")
    # Dev proxy origin
    proxy = devspaces_proxy_url(port)
    if proxy:
        origins.add(proxy)
    # Manual CORS override for future environments
    for _co in os.environ.get("PERSONALCLAW_CORS_ORIGINS", "").split(","):
        if _co.strip():
            origins.add(_co.strip())
    return origins


# ---------------------------------------------------------------------------
# Per-request origin check
# ---------------------------------------------------------------------------


def check_origin(
    request: web.Request,
    *,
    require: bool = True,
    fallback_header: str | None = None,
) -> bool:
    """Validate the request origin against ``app["allowed_origins"]``.

    Loopback requests (127.0.0.1, ::1) without an Origin header are
    always trusted — local processes like mcp-core and doctor don't
    send Origin headers but are not cross-origin attacks.  A browser
    on the same machine would always send an Origin header.
    """
    allowed: set[str] = request.app["allowed_origins"]
    origin = request.headers.get("Origin") or ""
    if not origin and fallback_header:
        origin = request.headers.get(fallback_header, "")
    if not origin:
        # No Origin header: trust loopback (local processes), reject others
        if is_loopback(request.remote or ""):
            return True
        return not require
    origin_base = "/".join(origin.split("/")[:3]) if "://" in origin else ""
    if origin_base in allowed:
        return True
    # Trust any loopback origin regardless of port — SSH tunnels commonly
    # forward a different local port (e.g. -L 8777:localhost:10000) causing
    # the browser to send an Origin with a port not in the allowed set.
    # Token auth is the real security boundary; CSRF from localhost is not
    # a realistic threat.
    #
    # SECURITY: defend against urlparse confusion. An origin like
    # ``http://localhost:3000.evil.com`` parses to hostname=``localhost`` in
    # Python because the ``:3000.evil.com`` part fails port parsing and the
    # parser falls back to just the hostname before the colon. This would
    # incorrectly trust an attacker-controlled origin if we accepted
    # ``is_loopback(parsed_host)`` alone. Require that ``parsed_host`` appear
    # as a complete component in ``origin_base`` and that there's no
    # subdomain-style suffix making the real host different.
    if origin_base:
        parsed = urlparse(origin_base)
        parsed_host = parsed.hostname or ""
        # Reject when port parsing failed — that means the netloc had a
        # malformed colon-suffix (e.g. ``localhost:3000.evil.com``) and the
        # urlparse hostname is not a faithful representation of the netloc.
        try:
            _ = parsed.port  # raises ValueError on malformed port
        except ValueError:
            return False
        # Extra sanity: netloc must look like ``host`` or ``host:port`` exactly
        # (no extra dots, no @ tricks) — token auth is the deeper defense but
        # we still want CSRF to be conservative.
        netloc = parsed.netloc.lower()
        if "@" in netloc:
            return False
        # Count colons on the host:port boundary only. IPv6 literals carry
        # their colons inside brackets (e.g. ``[::1]:8777``), so strip the
        # bracketed host before counting — otherwise a valid IPv6 loopback
        # origin trips the "extra colon" guard meant for ``host:port:extra``.
        if netloc.startswith("["):
            # ``[ipv6]`` or ``[ipv6]:port`` — everything after the closing
            # bracket must be empty or a single ``:port`` segment.
            after = netloc.rsplit("]", 1)[-1] if "]" in netloc else ":"
            if after.count(":") > 1:
                return False
        elif netloc.count(":") > 1:
            return False
        if is_loopback(parsed_host):
            return True
    return False
