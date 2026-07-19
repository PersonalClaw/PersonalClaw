"""Network egress/ingress security layer — the single outbound chokepoint.

One guard, one classifier, policy-per-surface. All outbound HTTP that could reach an
attacker-influenced host goes through :func:`personalclaw.net.client.fetch`, which
evaluates the URL against an :class:`~personalclaw.net.policy.EgressPolicy`, pins the
validated IP (closing the DNS-rebind TOCTOU window), re-checks every redirect hop, and
caps bytes/timeout. :func:`personalclaw.net.guard.classify_host` is the authoritative
"is this IP safe to reach" answer consulted by both outbound and inbound checks.
"""

from personalclaw.net.client import EgressBlocked, FetchResponse, fetch
from personalclaw.net.guard import GuardDecision, IpVerdict, classify_host, evaluate
from personalclaw.net.policy import (
    CONNECTOR,
    LOOPBACK_INTERNAL,
    STRICT,
    WEBHOOK,
    EgressPolicy,
    egress_policy_for,
    get_policy,
)

__all__ = [
    "EgressBlocked",
    "FetchResponse",
    "fetch",
    "GuardDecision",
    "IpVerdict",
    "classify_host",
    "evaluate",
    "EgressPolicy",
    "STRICT",
    "CONNECTOR",
    "WEBHOOK",
    "LOOPBACK_INTERNAL",
    "get_policy",
    "egress_policy_for",
]
