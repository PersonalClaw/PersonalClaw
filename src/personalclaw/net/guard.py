"""Egress decision plane — the ONE authoritative host classifier + URL evaluator.

Pure and synchronous (DNS resolution aside) so it is trivially unit-testable with a
fake resolver. ``classify_host`` is the single source of truth for "is this IP safe to
reach" — both outbound (``net.client``) and inbound (``dashboard.origin``) consult it,
so there is one answer to "what is a private IP", not the divergent definitions that
existed across the webhook guard and origin checks.

``evaluate(url, policy)`` resolves the host, classifies *every* A/AAAA record, and
returns the validated IPs so the client connects to *those exact IPs* — closing the
DNS-rebind TOCTOU window a validate-then-reconnect guard leaves open.
"""

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

from personalclaw.net.policy import EgressPolicy

logger = logging.getLogger(__name__)


@dataclass
class IpVerdict:
    """Classification of a single resolved IP."""

    ip: str
    public: bool
    category: str  # "public" | "loopback" | "private" | "link_local" | "multicast" | ...


@dataclass
class GuardDecision:
    """Outcome of evaluating a URL against a policy.

    ``pinned_ips`` are the already-resolved, validated IPs the client must dial — no
    second resolution (that is the rebind window). On a deny, ``reason`` is a
    user/agent-facing explanation and ``recovery_hints`` offer next steps.
    """

    allow: bool
    url: str = ""
    host: str = ""
    pinned_ips: list[str] = field(default_factory=list)
    reason: str = ""
    risk_level: str = "safe"
    recovery_hints: list[str] = field(default_factory=list)


def classify_host(ip_str: str) -> IpVerdict:
    """Classify a literal IP into a forbidden-range category, authoritatively.

    Covers the full forbidden set: loopback, RFC-1918 private, link-local
    (incl. 169.254.0.0/16 IMDS), ULA fc00::/7, multicast, reserved, unspecified —
    and the IPv4-mapped-IPv6 (``::ffff:0:0/96``) SSRF bypass the older per-caller
    guards missed (an attacker maps a private v4 into a v6 literal to dodge a v4-only
    check). Mapped/compat addresses are unwrapped to their embedded v4 and re-judged.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        # Not a parseable IP → treat as non-public (fail closed).
        return IpVerdict(ip=ip_str, public=False, category="invalid")

    # Unwrap an IPv4-mapped IPv6 address (::ffff:a.b.c.d) so a private v4 hidden in a
    # v6 literal is judged on its real v4 address, not waved through as "global" — the
    # SSRF bypass v4-only guards miss. (::1 loopback / :: unspecified are NOT mapped
    # addresses, so they fall through to the range checks below, correctly.)
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped

    if ip.is_loopback:
        return IpVerdict(str(ip), False, "loopback")
    if ip.is_link_local:  # includes 169.254.0.0/16 (IMDS) + fe80::/10
        return IpVerdict(str(ip), False, "link_local")
    if ip.is_multicast:
        return IpVerdict(str(ip), False, "multicast")
    if ip.is_unspecified:
        return IpVerdict(str(ip), False, "unspecified")
    if ip.is_private:  # RFC-1918, ULA fc00::/7, and other private ranges
        return IpVerdict(str(ip), False, "private")
    if ip.is_reserved:
        return IpVerdict(str(ip), False, "reserved")
    return IpVerdict(str(ip), True, "public")


def _host_matches(host: str, patterns: tuple[str, ...]) -> bool:
    """Anthropic-rule host match: a bare domain covers its subdomains.

    ``example.com`` matches ``example.com`` and ``api.example.com`` (but not
    ``notexample.com``). Case-insensitive.
    """
    h = host.lower().rstrip(".")
    for pat in patterns:
        p = pat.lower().rstrip(".")
        if not p:
            continue
        if h == p or h.endswith("." + p):
            return True
    return False


def _resolve(host: str) -> list[str]:
    """Resolve a host to all its A/AAAA addresses. Raises ``socket.gaierror`` on
    failure (the caller fails closed). Factored out so tests inject a fake resolver."""
    infos = socket.getaddrinfo(host, None)
    out: list[str] = []
    seen: set[str] = set()
    for info in infos:
        ip = str(info[4][0])
        # Strip an IPv6 scope/zone id (fe80::1%eth0) before classification.
        ip = ip.split("%", 1)[0]
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def evaluate(url: str, policy: EgressPolicy, *, resolver=_resolve) -> GuardDecision:
    """Evaluate a URL against a policy. Pure aside from the DNS resolve (injectable).

    Returns a :class:`GuardDecision`. On allow, ``pinned_ips`` carries the validated
    IPs the client must dial. ``resolver`` is injectable for testing (fake DNS).
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        return GuardDecision(
            allow=False,
            url=url,
            reason=f"invalid URL: {exc}",
            risk_level="caution",
            recovery_hints=["Pass a well-formed http(s) URL."],
        )

    scheme = (parsed.scheme or "").lower()
    if scheme not in policy.allow_schemes:
        return GuardDecision(
            allow=False,
            url=url,
            reason=f"scheme {scheme!r} not allowed (only {list(policy.allow_schemes)})",
            risk_level="caution",
            recovery_hints=["Use an http or https URL."],
        )
    host = parsed.hostname or ""
    if not host:
        return GuardDecision(
            allow=False,
            url=url,
            reason="URL is missing a host",
            risk_level="caution",
            recovery_hints=["Include a host in the URL."],
        )

    # Operator deny always wins, before any resolution.
    if _host_matches(host, policy.deny_hosts):
        return GuardDecision(
            allow=False,
            url=url,
            host=host,
            reason=f"host {host!r} is on the egress deny list",
            risk_level="destructive",
        )
    # An operator allow-listed host bypasses the private-range block (the homelab
    # LAN-webhook opt-in) — but still resolves + pins so the connection is honest.
    operator_allowed = _host_matches(host, policy.allow_hosts)

    try:
        ips = resolver(host)
    except socket.gaierror:
        return GuardDecision(
            allow=False,
            url=url,
            host=host,
            reason=f"host {host!r} is not resolvable",
            risk_level="caution",
            recovery_hints=["Check the hostname; the fetch fails closed on an unresolvable host."],
        )
    if not ips:
        return GuardDecision(
            allow=False,
            url=url,
            host=host,
            reason=f"host {host!r} resolved to no addresses",
            risk_level="caution",
        )

    verdicts = [classify_host(ip) for ip in ips]

    # Loopback-inverted policy (gateway↔mcp): require loopback, deny public.
    if policy.loopback_only:
        non_loopback = [v for v in verdicts if v.category != "loopback"]
        if non_loopback:
            return GuardDecision(
                allow=False,
                url=url,
                host=host,
                reason=f"LOOPBACK_INTERNAL policy: {host!r} resolves to non-loopback {non_loopback[0].ip}",  # noqa: E501
                risk_level="destructive",
            )
        return GuardDecision(
            allow=True, url=url, host=host, pinned_ips=[v.ip for v in verdicts], risk_level="safe"
        )

    # Public-only policy: every resolved IP must be public, unless the host is
    # operator-allow-listed or the policy opts into private ranges.
    if not (operator_allowed or policy.allow_private):
        bad = [v for v in verdicts if not v.public]
        if bad:
            return GuardDecision(
                allow=False,
                url=url,
                host=host,
                reason=(
                    f"host {host!r} resolves to a non-public address ({bad[0].ip}, {bad[0].category}); "  # noqa: E501
                    "egress guard blocks loopback, private, link-local, multicast, and reserved IPs"
                ),
                risk_level="destructive",
                recovery_hints=[
                    "Fetch a public URL.",
                    "An operator can allow-list an internal host via security.egress allow_hosts.",
                ],
            )

    return GuardDecision(
        allow=True, url=url, host=host, pinned_ips=[v.ip for v in verdicts], risk_level="safe"
    )
