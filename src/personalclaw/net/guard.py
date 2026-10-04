"""Egress decision plane — the ONE authoritative host classifier + URL evaluator.

Synchronous and pure aside from DNS resolution and the egress tier of the run a call is
made for (which ``evaluate`` reads, so the tier holds at every door that asks), so it is
trivially unit-testable with a fake resolver. ``classify_host`` is the single source of
truth for "is this IP safe to reach" — both outbound (``net.client``) and inbound
(``dashboard.origin``) consult it, so there is one answer to "what is a private IP", not
the divergent definitions that existed across the webhook guard and origin checks.

``evaluate(url, policy)`` resolves the host, classifies *every* A/AAAA record, and
returns the validated IPs so the client connects to *those exact IPs* — closing the
DNS-rebind TOCTOU window a validate-then-reconnect guard leaves open.
"""

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

from personalclaw.net.policy import METADATA_SERVICE_HOSTS, EgressPolicy, egress_policy_for_run

logger = logging.getLogger(__name__)


def _ip_literals(hosts: tuple[str, ...]) -> frozenset[str]:
    """The subset of ``hosts`` that are IP literals, for post-resolution matching."""
    out = set()
    for h in hosts:
        try:
            out.add(str(ipaddress.ip_address(h)))
        except ValueError:
            continue
    return frozenset(out)


#: Where the owner changes what this guard lets through. Every refusal the owner can lift points
#: here, in these words, so a sentence follows the page if it is renamed — and none sends a user
#: to a config key (``tests/test_egress_refusals_name_the_control.py`` holds it to the page).
EGRESS_SETTINGS = "Settings → Security → Network egress"
#: The control in that section that lifts a refusal for one host.
ALLOWED_HOSTS = f"Allowed hosts in {EGRESS_SETTINGS}"

#: The refusals an owner can lift for one host by listing it: an address on this machine or on a
#: private network they vouch for. Never the cloud metadata service or a link-local address, which
#: stay refused for a listed host too, and never an address that is neither.
OWNER_CAN_ALLOW = frozenset({"loopback", "unspecified", "private"})

#: Where a refused address is, in the words a sentence about it uses (a
#: :attr:`GuardDecision.category` → a place).
PLACES = {
    "loopback": "this computer",
    "unspecified": "this computer",  # 0.0.0.0 and :: reach this machine's own services
    "private": "a private network",
    "link_local": "a link-local address",
}


def allow_host_step(host: str) -> str:
    """The one step that lets *host* through a refusal the owner can lift, as a clause.

    The narrow control, deliberately: one host in Allowed hosts. The same section's "Allow all
    private networks" switch would also lift it, and opens every private address with it.
    """
    return f"add {host} to {ALLOWED_HOSTS}"


#: Metadata-service endpoints as IPs, matched against RESOLVED addresses. `deny_hosts`
#: matches the URL's hostname before DNS, so it cannot see an allow-listed NAME that
#: resolves (or rebinds) to a credential endpoint — this set is the post-resolution
#: half of the same block. Derived from :data:`METADATA_SERVICE_HOSTS` so a cloud
#: added there is covered here without a second hand-maintained copy.
_METADATA_SERVICE_IPS = _ip_literals(METADATA_SERVICE_HOSTS)


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
    # On a deny, WHAT was refused, for a caller that writes its own sentence (the Store says
    # "this computer" where ``reason`` says "loopback"). ``category`` is an :class:`IpVerdict`
    # category (``loopback``, ``private``, ``link_local``, …), or ``metadata`` for a cloud
    # metadata endpoint, ``deny_list`` for an operator deny, ``not_listed`` for a host off an
    # exclusive allow-list, ``egress_off`` for a request made inside a run whose egress tier
    # allows no network at all, ``unresolvable`` for a name with no answer, and ``malformed``
    # for a URL the guard could not read. ``address`` is the offending address when there is one.
    category: str = ""
    address: str = ""


#: Why a request made inside a run whose egress tier is ``off`` is refused: the words the agent's
#: page fetch has always used for it, now the guard's for every request such a run makes.
EGRESS_OFF_REASON = "egress is off for this run (safety profile egress tier 'off')"
#: What such a refusal sent, in its first hint.
EGRESS_OFF_NOTHING_SENT = (
    "This run's safety settings give it no network access, so nothing was sent."
)


def egress_off_decision(url: str, host: str = "") -> GuardDecision:
    """The guard's refusal of *url* for a run that may not reach the network at all.

    Refused before the host is looked up, since a DNS query is egress too. The hints say that
    nothing was sent, and where the tier comes from (:func:`where_egress_is_off`)."""
    return GuardDecision(
        allow=False,
        url=url,
        host=host,
        reason=EGRESS_OFF_REASON,
        risk_level="destructive",
        recovery_hints=[EGRESS_OFF_NOTHING_SENT, where_egress_is_off()],
        category="egress_off",
    )


def where_egress_is_off() -> str:
    """Which bound turns a run's egress off, in a hint: the operator ceiling's file when its
    ``egress`` scope says ``off`` (for every run on this machine), else the run's own safety
    profile. No host setting lifts either, so a surface that says how to undo an ``egress_off``
    refusal says this, never Allowed hosts."""
    try:
        from personalclaw.guardrails.ceiling import active_ceiling, ceiling_path

        ceiling = active_ceiling()
        if getattr(ceiling.control("egress"), "value", "") == "off":
            where = ceiling.source or str(ceiling_path())
            return (
                f'The operator ceiling ({where}) sets "egress": "off" for every run on this '
                "machine; a change to that file applies when PersonalClaw restarts."
            )
    except Exception:  # noqa: BLE001 - a hint must not fail the refusal it explains
        logger.debug("could not read the ceiling for an egress refusal's hint", exc_info=True)
    return "This run's own safety profile allows it no network access."


def egress_refusal(url: str, decision: GuardDecision) -> str:
    """The sentence for this guard refusing *url*, for the owner who reads it.

    It names the control that lifts the refusal, in the guard's own words for it — Allowed hosts
    in Settings → Security → Network egress, for the one host, or Denied hosts, where the owner
    put it — and names nothing when no setting lifts it. One sentence for every surface that
    says it: a model provider's Test and the apps that fetch through the SDK
    (``personalclaw.sdk.net.egress_refusal``). It used to name two config keys, one of them the
    switch that opens every private address at once, which is not what an owner vouching for one
    server should reach for.
    """
    return refusal_for(url, decision, then="then test again")


def refusal_for(url: str, decision: GuardDecision, *, then: str) -> str:
    """:func:`egress_refusal`'s sentence, ending in the caller's own next step once the host is
    allowed: a Test button says "then test again", and a web watch, which checks again by
    itself, says its next check reaches the page. One sentence, so the two cannot drift apart."""
    host = decision.host or "its host"
    if decision.category == "unresolvable":
        return (
            f"{host} could not be found, so {url} was not reached — check the address, and this "
            "computer's network connection."
        )
    if decision.category == "deny_list":
        return f"{url} was not reached: {host} is on Denied hosts in {EGRESS_SETTINGS}."
    if decision.category == "egress_off":
        # The run's tier, not a host: nothing on the Network egress page lifts it, so the
        # sentence names none (the decision's hints say which bound set it).
        return f"{url} was not reached: {decision.reason}."
    if decision.category == "not_listed":
        return (
            f"PersonalClaw's network settings refused {url}: this run reaches only the hosts "
            f"it lists. If this endpoint is yours, {allow_host_step(host)}, {then}."
        )
    if decision.category in OWNER_CAN_ALLOW:
        where = PLACES[decision.category]
        address = decision.address
        place = (
            f"{where} ({host})"
            if not address or address == host
            else f"{where} ({host}, which resolves to {address})"
        )
        return (
            f"PersonalClaw's network settings refused {url}, which is on {place}. If this "
            f"endpoint is yours, {allow_host_step(host)}, {then}."
        )
    # The metadata service, a link-local or reserved address, a URL the guard cannot read: no
    # setting reaches these, so the guard's own reason is the whole answer.
    return f"PersonalClaw's network settings refused {url}: {decision.reason}."


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


def reaches_this_machine(url: str) -> bool:
    """Whether a request to *url* goes to this machine, so what it carries never leaves it.

    Decided by the URL's host alone, and never resolved: ``localhost``, a loopback address, or
    the unspecified address (``0.0.0.0``, ``::``), which a connection reaches this machine
    through. An address on the network, any other name (it may point anywhere), a URL that does
    not parse and no URL at all are not this machine. The one answer for "is this endpoint on this
    machine", asked of a provider entry's endpoints by the two rules built on it
    (``llm.registry.served_on_this_machine``, where only a type that runs its models where its
    endpoint is makes one here a model here, and ``llm.registry.sends_to_this_machine``). An
    endpoint here alone does not say what runs behind it.
    """
    text = str(url or "").strip()
    if not text:
        return False
    try:
        host = (urlparse(text).hostname or "").rstrip(".")
    except ValueError:
        return False
    if host == "localhost":
        return True
    return bool(host) and classify_host(host).category in ("loopback", "unspecified")


def host_matches(host: str, patterns: tuple[str, ...]) -> bool:
    """Anthropic-rule host match: a bare domain covers its subdomains.

    ``example.com`` matches ``example.com`` and ``api.example.com`` (but not
    ``notexample.com``). Case-insensitive.

    There is exactly one rule and no glob support, so a pattern containing ``*`` — or one
    that normalises to nothing, like ``""`` or ``"."`` — can never match anything. The
    config write boundary refuses those now (``config/edit_spec.py``, issue 2956), but a
    hand-edited ``config.json`` bypasses it entirely, so an unmatchable pattern is logged
    once here rather than skipped in silence. A ``deny_hosts`` entry that quietly matches
    nothing is a security control the user believes is armed.
    """
    h = host.lower().rstrip(".")
    for pat in patterns:
        p = pat.lower().rstrip(".")
        if not p or "*" in p:
            _warn_unmatchable(pat)
            continue
        if h == p or h.endswith("." + p):
            return True
    return False


#: Patterns already reported by :func:`_warn_unmatchable`. ``host_matches`` runs on every
#: fetch, so the warning is per unique pattern for the process, not per request.
_UNMATCHABLE_SEEN: set[str] = set()


def _warn_unmatchable(pattern: str) -> None:
    if pattern in _UNMATCHABLE_SEEN:
        return
    _UNMATCHABLE_SEEN.add(pattern)
    logger.warning(
        "security.egress host %r can never match any host and is being IGNORED. There is "
        "no glob support: write the bare domain, which already covers its subdomains. "
        "Fix it in %s, or in config.json.",
        pattern,
        EGRESS_SETTINGS,
    )


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


def _literal(host: str) -> str:
    """``host`` as a normalised IP string when it is an IP literal, else ``""``."""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return ""


def evaluate(url: str, policy: EgressPolicy, *, resolver=None) -> GuardDecision:
    """Evaluate a URL against a policy, narrowed by the run the call is made for.

    Returns a :class:`GuardDecision`. On allow, ``pinned_ips`` carries the validated
    IPs the client must dial. ``resolver`` is injectable for testing (fake DNS); left out, the
    module's :func:`_resolve` is looked up at CALL time, so a test that replaces it reaches every
    caller, including one that runs on a thread of its own (the git tunnel).

    *policy* is narrowed first by the egress tier of the run the call is being made for
    (:func:`~personalclaw.net.policy.egress_policy_for_run`), so that tier holds for every
    request a run makes through any door that asks the guard, whatever policy that door
    built: a tier of ``off`` refuses every host (``egress_off``) before it is looked up. Pure
    aside from that reading and the DNS resolve.
    """
    resolve = resolver or _resolve
    try:
        parsed = urlparse(url)
    except Exception as exc:
        return GuardDecision(
            allow=False,
            url=url,
            reason=f"invalid URL: {exc}",
            risk_level="caution",
            recovery_hints=["Pass a well-formed http(s) URL."],
            category="malformed",
        )

    scheme = (parsed.scheme or "").lower()
    if scheme not in policy.allow_schemes:
        return GuardDecision(
            allow=False,
            url=url,
            reason=f"scheme {scheme!r} not allowed (only {list(policy.allow_schemes)})",
            risk_level="caution",
            recovery_hints=["Use an http or https URL."],
            category="malformed",
        )
    host = parsed.hostname or ""
    if not host:
        return GuardDecision(
            allow=False,
            url=url,
            reason="URL is missing a host",
            risk_level="caution",
            recovery_hints=["Include a host in the URL."],
            category="malformed",
        )

    # The run this call is made for may narrow where it goes, whatever this door built: its
    # tier is read here, once, for every request any door asks about.
    narrowed = egress_policy_for_run(policy)
    if narrowed is None:
        return egress_off_decision(url, host)
    policy = narrowed

    # Operator deny always wins, before any resolution.
    if host_matches(host, policy.deny_hosts):
        metadata = host_matches(host, METADATA_SERVICE_HOSTS)
        return GuardDecision(
            allow=False,
            url=url,
            host=host,
            reason=f"host {host!r} is on the egress deny list",
            risk_level="destructive",
            category="metadata" if metadata else "deny_list",
            address=_literal(host),
        )
    # An operator allow-listed host bypasses the private-range block (the homelab
    # LAN-webhook opt-in) — but still resolves + pins so the connection is honest.
    operator_allowed = host_matches(host, policy.allow_hosts)

    # EXCLUSIVE allow-list (a run's egress tier narrowed to "listed"/"registry"): only a
    # listed host is reachable at all. Checked BEFORE resolution so an off-list host is
    # never even looked up — a DNS query is itself an egress signal.
    if policy.allow_only and not operator_allowed:
        return GuardDecision(
            allow=False,
            url=url,
            host=host,
            reason=(
                f"host {host!r} is not on the {policy.name!r} egress allow-list "
                f"({len(policy.allow_hosts)} host(s) allowed)"
            ),
            risk_level="destructive",
            recovery_hints=[
                "This run's safety profile limits egress to an allow-list.",
                f"To allow it, {allow_host_step(host)}, or widen the egress tier in the "
                "governance ceiling.",
            ],
            category="not_listed",
        )

    try:
        ips = resolve(host)
    except socket.gaierror:
        return GuardDecision(
            allow=False,
            url=url,
            host=host,
            reason=f"host {host!r} is not resolvable",
            risk_level="caution",
            recovery_hints=["Check the hostname; the fetch fails closed on an unresolvable host."],
            category="unresolvable",
        )
    if not ips:
        return GuardDecision(
            allow=False,
            url=url,
            host=host,
            reason=f"host {host!r} resolved to no addresses",
            risk_level="caution",
            category="unresolvable",
        )

    verdicts = [classify_host(ip) for ip in ips]

    # The credential-endpoint block survives the operator allow-list at the IP layer.
    # `deny_hosts` refuses the metadata HOSTNAMES before resolution, but an allow-listed
    # name that resolves (or DNS-rebinds) to the endpoint arrives here as just another
    # non-public address — and the homelab waiver below would admit it. A link-local
    # resolution (169.254.0.0/16 IMDS, fe80::/10) or a literal metadata-service IP is
    # therefore refused unconditionally, for every policy: no legitimate allow-listed
    # service lives on a link-local address, so this cannot break the LAN opt-in.
    metadata_hits = [
        v for v in verdicts if v.category == "link_local" or v.ip in _METADATA_SERVICE_IPS
    ]
    if metadata_hits:
        return GuardDecision(
            allow=False,
            url=url,
            host=host,
            reason=(
                f"host {host!r} resolves to a cloud metadata / link-local address "
                f"({metadata_hits[0].ip}); the instance-credential endpoint is never "
                "reachable, even for an allow-listed host"
            ),
            risk_level="destructive",
            recovery_hints=[
                "The cloud metadata endpoint is never fetchable; use the runtime's "
                "own credential tooling instead.",
                "If this hostname should be legitimate, its DNS is resolving to a "
                "metadata/link-local address - investigate the record before retrying.",
            ],
            category=("metadata" if metadata_hits[0].ip in _METADATA_SERVICE_IPS else "link_local"),
            address=metadata_hits[0].ip,
        )

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
                category=non_loopback[0].category,
                address=non_loopback[0].ip,
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
                    *(
                        [f"If {host} is yours, {allow_host_step(host)}."]
                        if bad[0].category in OWNER_CAN_ALLOW
                        else []
                    ),
                ],
                category=bad[0].category,
                address=bad[0].ip,
            )

    return GuardDecision(
        allow=True, url=url, host=host, pinned_ips=[v.ip for v in verdicts], risk_level="safe"
    )
