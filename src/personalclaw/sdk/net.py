"""SDK: the guarded network-egress chokepoint + the high-level web fetch/extract.

Stable re-export of ``personalclaw.net`` (the egress policy layer: ``fetch`` + the
``CONNECTOR`` policy — an app's outbound traffic is subject to the same guard core
uses) and ``personalclaw.web.fetch`` (the SSRF-guarded page fetch + content
extraction pipeline: ``web_fetch``/``web_extract`` + ``record_seen_urls`` provenance).
Generic, provider-agnostic infrastructure a web-capable app/tool builds on.
"""

from personalclaw.net import (  # noqa: F401
    CONNECTOR,
    SYNC,
    WEBHOOK,
    EgressBlocked,
    EgressPolicy,
    GuardDecision,
    SyncEndpointRefused,
    egress_policy_for,
    evaluate,
    fetch,
    sync_egress_policy,
)
from personalclaw.web.fetch import (  # noqa: F401
    record_seen_urls,
    web_extract,
    web_fetch,
)

# ``evaluate(url, policy) -> GuardDecision`` is the SYNCHRONOUS egress guard (resolve
# + host-classify + scheme check) that ``fetch`` runs internally. Promoted to the SDK
# facade (#45) so an app with a SYNC surface that can't await ``fetch`` — e.g.
# openai-tools' ``connected`` property, skills-sh's SkillsMarketplace ABC (_get) —
# can still guard an operator-configured endpoint before a raw request, WITHOUT
# reaching into ``personalclaw.net`` directly (the app import-boundary forbids that).
__all__ = [
    "fetch",
    "CONNECTOR",
    "EgressPolicy",
    "WEBHOOK",
    "EgressBlocked",
    "egress_policy_for",
    "evaluate",
    "GuardDecision",
    # DURABILITY-AND-SYNC §4.3 (DAS-8): the sync transport's egress posture. `SYNC` is
    # host-pinned-by-absence (it reaches nothing until `sync_egress_policy` pins the
    # configured endpoint onto it), so a transport cannot widen its own reach by forgetting
    # a step — the failure mode of a base profile that defaults to "all public hosts".
    "SYNC",
    "sync_egress_policy",
    "SyncEndpointRefused",
    "web_fetch",
    "web_extract",
    "record_seen_urls",
]
