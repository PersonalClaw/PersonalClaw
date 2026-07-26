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
    max_bytes=100_000_000,  # a wheel/image layer is large
    timeout_s=60.0,
)

_PROFILES: dict[str, EgressPolicy] = {
    p.name: p for p in (STRICT, CONNECTOR, WEBHOOK, LOOPBACK_INTERNAL, REGISTRY)
}


def egress_policy_for_tier(tier: str) -> "EgressPolicy | None":
    """Resolve a safety-profile egress TIER to a base :class:`EgressPolicy` (§4.2).

    * ``off``      → ``None`` (the caller denies all egress — no policy applies).
    * ``listed``   → STRICT + the operator's ``security.egress.allow_hosts`` (via
                     ``egress_policy_for`` at the call site) — a user allow-list.
    * ``registry`` → the curated REGISTRY preset (dev registries only).
    * ``all``      → STRICT (public hosts, the normal agent posture).

    An unknown tier falls back to STRICT (the safe public-only default)."""
    if tier == "off":
        return None
    if tier == "registry":
        return REGISTRY
    return STRICT  # "listed" and "all" both start from STRICT; operator layering adds hosts


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
