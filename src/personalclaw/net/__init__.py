"""Network egress/ingress security layer — the single outbound chokepoint.

One guard, one classifier, policy-per-surface. All outbound HTTP that could reach an
attacker-influenced host goes through :func:`personalclaw.net.client.fetch`, which
evaluates the URL against an :class:`~personalclaw.net.policy.EgressPolicy`, pins the
validated IP (closing the DNS-rebind TOCTOU window), re-checks every redirect hop, and
caps bytes/timeout. A download too large to buffer streams through
:func:`personalclaw.net.client.open_url`, which asks the same guard about every request and
redirect hop before it is sent. A client library that sends its own requests (a model
provider's SDK, an app's own HTTP code) is handed a client from
:mod:`personalclaw.net.http_clients`, which asks the same guard about every request it sends, each
redirect hop included; one that takes no client asks a
:class:`~personalclaw.net.http_clients.RequestGuard` from a hook of its own before each request.
:func:`personalclaw.net.guard.classify_host` is the authoritative "is this IP safe to reach" answer
consulted by both outbound and inbound checks.
"""

from personalclaw.net.client import EgressBlocked, FetchResponse, fetch, open_url
from personalclaw.net.guard import GuardDecision, IpVerdict, classify_host, evaluate
from personalclaw.net.http_clients import (
    RequestGuard,
    http_client,
    http_session,
    sync_http_client,
)
from personalclaw.net.policy import (
    CONNECTOR,
    LOOPBACK_INTERNAL,
    MODEL_PROVIDER,
    STRICT,
    SYNC,
    WEBHOOK,
    EgressPolicy,
    SyncEndpointRefused,
    egress_policy_for,
    get_policy,
    provider_egress_policy,
    sync_egress_policy,
)

__all__ = [
    "EgressBlocked",
    "FetchResponse",
    "fetch",
    "open_url",
    "http_client",
    "sync_http_client",
    "http_session",
    "RequestGuard",
    "GuardDecision",
    "IpVerdict",
    "classify_host",
    "evaluate",
    "EgressPolicy",
    "STRICT",
    "CONNECTOR",
    "WEBHOOK",
    "LOOPBACK_INTERNAL",
    "MODEL_PROVIDER",
    "SYNC",
    "get_policy",
    "egress_policy_for",
    "provider_egress_policy",
    "sync_egress_policy",
    "SyncEndpointRefused",
]
