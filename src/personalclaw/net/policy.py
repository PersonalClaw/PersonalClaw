"""Egress policy plane — declarative, per-surface network posture.

A caller picks a named :class:`EgressPolicy` profile (STRICT for agent fetch,
CONNECTOR for knowledge scrape, WEBHOOK for user-configured POSTs, LOOPBACK_INTERNAL
for gateway↔mcp self-calls) instead of re-implementing checks. The guard
(``net/guard.py``) reads the policy to decide; the client (``net/client.py``)
enforces the byte/timeout/redirect caps.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EgressPolicy:
    """A network egress posture for one surface.

    ``allow_private`` flips the whole stance: STRICT/public profiles keep it False
    (public hosts only — block loopback/RFC-1918/link-local/etc.); LOOPBACK_INTERNAL
    sets it True AND ``loopback_only`` so it *expects* 127.0.0.1 and denies public.

    ``allow_hosts`` / ``deny_hosts`` are operator opt-in overrides matched by the
    Anthropic rule (bare domain covers its subdomains): a deny always wins; an allow
    permits an otherwise-private host (the homelab LAN-webhook case).
    """

    name: str = "strict"
    allow_schemes: tuple[str, ...] = ("http", "https")
    allow_private: bool = False
    # Invert the stance to loopback-only (gateway↔mcp self-calls): allow loopback,
    # deny everything public. Implies allow_private for the loopback range.
    loopback_only: bool = False
    allow_hosts: tuple[str, ...] = ()
    deny_hosts: tuple[str, ...] = ()
    # Invert ``allow_hosts`` from ADDITIVE to EXCLUSIVE: only a listed host is reachable,
    # public or not. Without this, ``allow_hosts`` merely waives the private-range block,
    # so REGISTRY (a 22-host preset) reached every public host exactly like STRICT and an
    # egress "tier" could not narrow anything — the whole tier plane was decorative. A
    # tier that means "only these hosts" needs the exclusive stance; the guard enforces it
    # before DNS resolution, so a denied host is never even looked up.
    allow_only: bool = False
    max_redirects: int = 5
    max_bytes: int = 5_000_000
    timeout_s: float = 30.0
    pin_resolved_ip: bool = True
    # deny → block on violation; warn → audit but allow (operator escape hatch).
    on_violation: str = "deny"

    def with_overrides(self, **kw) -> "EgressPolicy":
        """A copy with fields replaced (operator config layering)."""
        from dataclasses import replace

        return replace(self, **kw)


# ── Named profiles ────────────────────────────────────────────────────────────

# Default for agent-driven fetch/scrape/browse: public hosts only, pin IP,
# re-check redirects, byte cap.
STRICT = EgressPolicy(name="strict")

# Knowledge web-url / bookmark scrape: STRICT posture, connector-tuned caps.
CONNECTOR = EgressPolicy(name="connector", max_bytes=10_000_000, timeout_s=20.0)

# WatchedSource poll fetch (WATCHED-SOURCES §11): a scheduled, unattended pull of a feed
# or web listing page. Same STRICT public-only posture and CONNECTOR caps (10MB/20s) — a
# source poll is a knowledge scrape on a timer — as a distinct profile so its egress
# audits are attributable to the source engine and its caps can diverge from an
# interactive bookmark scrape later without disturbing CONNECTOR's callers.
SOURCE = EgressPolicy(name="source", max_bytes=10_000_000, timeout_s=20.0)

# User-configured outbound POST: STRICT, but the operator may allow-list internal
# hosts (a homelab user POSTing to a LAN service — opt-in via allow_hosts).
WEBHOOK = EgressPolicy(name="webhook", timeout_s=30.0)

# Gateway↔mcp self-calls: inverted — loopback expected, public denied.
LOOPBACK_INTERNAL = EgressPolicy(
    name="loopback_internal",
    allow_private=True,
    loopback_only=True,
    pin_resolved_ip=False,
    max_bytes=50_000_000,
    timeout_s=60.0,
)

# AUTONOMY-GUARDRAILS §4.2: a curated package-registry allow-list for sandboxed code
# runs / loop workers that need to reach the common dev registries WITHOUT opening the
# whole internet. STRICT posture (public-only, pin IP, redirect re-check) + this
# preset in allow_hosts. A safety profile's egress tier "registry" selects this;
# "all" selects STRICT (public), "listed" a user allow-list, "off" denies all egress.
REGISTRY_HOSTS: tuple[str, ...] = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "npmjs.com",
    "crates.io",
    "static.crates.io",
    "index.crates.io",
    "docker.io",
    "registry-1.docker.io",
    "ghcr.io",
    "github.com",
    "raw.githubusercontent.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "repo1.maven.org",
    "repo.maven.apache.org",
    "rubygems.org",
    "proxy.golang.org",
    "sum.golang.org",
    "packagist.org",
    "nuget.org",
    "api.nuget.org",
)
REGISTRY = EgressPolicy(
    name="registry",
    allow_hosts=REGISTRY_HOSTS,
    allow_only=True,  # dev registries ONLY — the point of the tier
    max_bytes=100_000_000,  # a wheel/image layer is large
    timeout_s=60.0,
)

# The "listed" tier: reachable = exactly the operator's ``security.egress.allow_hosts``
# (unioned in by ``egress_policy_for``) and nothing else. Its own allow list is empty, so
# a "listed" tier with no configured hosts denies every host — the fail-closed reading of
# "only what is listed", and visible as a refusal rather than a silent widening.
LISTED = EgressPolicy(name="listed", allow_only=True)

# DURABILITY-AND-SYNC §4.3/§4.4 + Plug-in Map: the sync transport's egress posture. A sync
# transport talks to EXACTLY ONE operator-configured object-store endpoint, forever — so the
# base is `allow_only` with an EMPTY host list, which denies every host until
# :func:`sync_egress_policy` pins the configured endpoint onto it. That ordering is the point:
# the fail-closed reading of "host-pinned" is that an unpinned SYNC policy reaches nothing, so
# a transport that forgets to pin cannot silently inherit STRICT's whole-public-internet reach.
#
# `max_bytes` is raised DELIBERATELY, not removed: a whole-database shard copy is much larger
# than a 5 MB page fetch but is still bounded, so a hostile or misconfigured endpoint cannot
# stream an unbounded body into memory. 200 MB is the same order as REGISTRY's 100 MB wheel cap
# with headroom for a multi-hundred-MB memory/knowledge DB shard.
#
# `allow_private` stays False in the base, but MEASURED BEHAVIOUR (driven, not assumed): the
# pinned endpoint is itself an `allow_hosts` entry, and the guard's documented `allow_hosts`
# semantics waive the private-range block for a listed host. So a self-hosted endpoint on
# loopback or a LAN — `http://127.0.0.1:9000`, `http://nas.local:9000` — IS reachable with no
# further operator opt-in. That is deliberate and correct for this surface: §4.4's whole premise
# is user-owned storage, a self-hosted MinIO/NAS is a first-class target, and the endpoint comes
# from operator provider settings, never from anything an agent can influence.
#
# What that reachability does NOT get to include is the cloud metadata service, which is the one
# private address where "fetch whatever the operator configured" turns into credential theft. A
# `deny_hosts` entry is evaluated BEFORE the allow-list and before DNS resolution, so it survives
# the pin; a legitimate MinIO host is unaffected.
SYNC_DENY_HOSTS: tuple[str, ...] = (
    "169.254.169.254",  # AWS/Azure/GCP/OpenStack IMDS
    "metadata.google.internal",
    "metadata.goog",
    "100.100.100.200",  # Alibaba Cloud
)
SYNC = EgressPolicy(
    name="sync",
    allow_only=True,
    allow_hosts=(),
    deny_hosts=SYNC_DENY_HOSTS,
    max_bytes=200_000_000,
    timeout_s=120.0,
)

_PROFILES: dict[str, EgressPolicy] = {
    p.name: p
    for p in (STRICT, CONNECTOR, SOURCE, WEBHOOK, LOOPBACK_INTERNAL, REGISTRY, LISTED, SYNC)
}


def egress_policy_for_tier(tier: str) -> "EgressPolicy | None":
    """Resolve a safety-profile egress TIER to a base :class:`EgressPolicy` (§4.2).

    * ``off``      → ``None`` (the caller denies all egress — no policy applies).
    * ``listed``   → LISTED: exclusively the operator's ``security.egress.allow_hosts``
                     (unioned in by ``egress_policy_for`` at the call site).
    * ``registry`` → the curated REGISTRY preset, exclusively (dev registries only).
    * ``all``      → STRICT (public hosts, the normal agent posture).

    An unknown tier falls back to STRICT (the safe public-only default); the ceiling
    rejects an off-scale tier at boot, so an unknown value cannot arrive from there."""
    if tier == "off":
        return None
    if tier == "registry":
        return REGISTRY
    if tier == "listed":
        return LISTED
    return STRICT


def egress_policy_for_profile(base: EgressPolicy, tier: str) -> "EgressPolicy | None":
    """Narrow a surface's ``base`` policy by a run's egress TIER — tightest wins.

    The surface keeps its own caps and stance (a knowledge scrape stays CONNECTOR-shaped);
    the tier can only take reach away. ``None`` means the run may not egress at all and the
    caller must refuse — never fall through to the base.

    Composition, per field:

    * ``off`` → ``None``.
    * a tier with an exclusive host set (``listed``/``registry``) → the base becomes
      exclusive too, with the tier's hosts unioned onto the base's own (a surface that
      already allow-listed a host keeps it; the tier adds its preset).
    * ``all`` → the base is already at least this narrow, so it is returned unchanged.
    * caps (``max_bytes``/``timeout_s``) take the tighter of the two, so a tier can never
      raise a surface's ceiling — REGISTRY's 100 MB does not widen a 5 MB fetch.
    """
    tier_policy = egress_policy_for_tier(tier)
    if tier_policy is None:
        return None
    if not tier_policy.allow_only:
        return base
    return base.with_overrides(
        allow_only=True,
        allow_hosts=tuple(dict.fromkeys([*base.allow_hosts, *tier_policy.allow_hosts])),
        max_bytes=min(base.max_bytes, tier_policy.max_bytes),
        timeout_s=min(base.timeout_s, tier_policy.timeout_s),
    )


def get_policy(name: str) -> EgressPolicy:
    """Look up a named profile (defaults to STRICT for an unknown name)."""
    return _PROFILES.get(name, STRICT)


def egress_policy_for(base: EgressPolicy) -> EgressPolicy:
    """Layer the operator's ``security.egress`` config onto a base profile.

    A self-hoster can allow-list LAN hosts (homelab webhook), deny specific hosts, or
    opt the whole instance into private-network egress. The guard's built-in public-only
    default is unchanged when no config is set. Operator ``allow_hosts``/``deny_hosts``
    are UNIONed with the profile's own; ``allow_private`` ORs in. Config read is lazy +
    best-effort so ``net`` stays importable without a loaded config (tests, early boot)."""
    try:
        from personalclaw.config.loader import AppConfig

        eg = AppConfig.load().security.egress
    except Exception:
        return base
    return base.with_overrides(
        allow_hosts=tuple(dict.fromkeys([*base.allow_hosts, *eg.allow_hosts])),
        deny_hosts=tuple(dict.fromkeys([*base.deny_hosts, *eg.deny_hosts])),
        allow_private=base.allow_private or bool(eg.allow_private),
    )


class SyncEndpointRefused(ValueError):
    """A sync endpoint that cannot be pinned — so no policy is derived and nothing egresses.

    Raised instead of returning a wide-open policy: "I could not work out which host you
    meant" must never resolve to "reach any host". The caller surfaces it as a setup error.
    """


def sync_egress_policy(endpoint: str) -> EgressPolicy:
    """The SYNC policy pinned to one configured object-store ``endpoint`` (§4.3, Plug-in Map).

    Derived, never hand-written: :data:`SYNC` supplies the raised caps and the exclusive
    stance, :func:`egress_policy_for` layers the operator's ``security.egress`` posture
    (``allow_private`` for a LAN MinIO, ``deny_hosts`` for a host the operator has banned),
    and only then is the endpoint's host pinned as the sole reachable host.

    The pin is applied AFTER the operator layering on purpose. `egress_policy_for` UNIONs the
    operator's ``allow_hosts`` into whatever base it is given, which for an exclusive policy
    would widen the transport's reach to every host the operator listed for other surfaces —
    hosts that have no business being an S3 endpoint. A sync transport speaks to one endpoint,
    so ``allow_hosts`` ends as exactly that endpoint. ``deny_hosts`` is UNIONed rather than
    replaced, so both :data:`SYNC_DENY_HOSTS` and the operator's own denies survive — and a
    deny outranks the pin, including when the operator bans their own configured endpoint.

    Because the pinned host is its own allow-list entry, a private/loopback endpoint is
    reachable without further opt-in (see :data:`SYNC`'s note — that is the intended posture
    for user-owned storage, and the metadata service is denied separately).

    Raises :class:`SyncEndpointRefused` when no host can be parsed out of ``endpoint``, or when
    the endpoint names a denied host — refusing at derivation is more legible than handing back
    a policy whose only permitted host is one the guard will reject on every request.
    """
    from urllib.parse import urlparse

    raw = (endpoint or "").strip()
    if not raw:
        raise SyncEndpointRefused("no sync endpoint configured — nothing to pin egress to")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise SyncEndpointRefused(f"cannot parse a host out of sync endpoint {raw!r}")
    if parsed.scheme not in SYNC.allow_schemes:
        raise SyncEndpointRefused(
            f"sync endpoint scheme {parsed.scheme!r} is not one of {SYNC.allow_schemes}"
        )
    layered = egress_policy_for(SYNC)
    denies = tuple(dict.fromkeys([*SYNC_DENY_HOSTS, *layered.deny_hosts]))
    from personalclaw.net.guard import host_matches

    if host_matches(host, denies):
        raise SyncEndpointRefused(
            f"sync endpoint host {host!r} is on the egress deny list and cannot be a sync target"
        )
    return layered.with_overrides(allow_only=True, allow_hosts=(host,), deny_hosts=denies)
