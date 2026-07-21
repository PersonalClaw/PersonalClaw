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

_PROFILES: dict[str, EgressPolicy] = {
    p.name: p for p in (STRICT, CONNECTOR, WEBHOOK, LOOPBACK_INTERNAL)
}


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
