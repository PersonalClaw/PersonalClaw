"""SDK: the guarded network-egress chokepoint + the high-level web fetch/extract.

Stable re-export of ``personalclaw.net`` (the egress policy layer: ``fetch`` + the
``CONNECTOR`` policy — an app's outbound traffic is subject to the same guard core
uses) and ``personalclaw.web.fetch`` (the SSRF-guarded page fetch + content
extraction pipeline: ``web_fetch``/``web_extract`` + ``record_seen_urls`` provenance).
Generic, provider-agnostic infrastructure a web-capable app/tool builds on.
"""

# EXTERNAL-ACCESS §5 (EA-8): the egress posture for an OUTBOUND A2A call, promoted here so
# the `a2a-action` app can USE the policy without composing one. That split is deliberate and
# is the whole reason this export exists: `outbound_policy` is `allow_only=True`
# (deny-by-default — an empty allow-list reaches NOWHERE), which an app free to build its own
# `EgressPolicy` could quietly relax to the additive `egress_policy_for(CONNECTOR)` shape that
# reaches every public host. The app supplies a URL; core decides where a URL may point.
from personalclaw.inbound.a2a import outbound_policy as a2a_outbound_policy  # noqa: F401
from personalclaw.net import (  # noqa: F401
    CONNECTOR,
    SYNC,
    WEBHOOK,
    EgressBlocked,
    EgressPolicy,
    FetchResponse,
    GuardDecision,
    SyncEndpointRefused,
    egress_policy_for,
    evaluate,
    fetch,
    sync_egress_policy,
)

# One vocabulary for what a failure to reach an endpoint SAYS (what failed, the likely cause,
# the fix — the container-localhost case included). Exported so a provider app composes its
# connection-test and discovery failures in core's words instead of relaying `str(exc)`,
# which is how a bare `[Errno 111]` reached the Providers page (ollama-models reads it).
from personalclaw.providers.failure_copy import relayed_failure_copy  # noqa: F401
from personalclaw.web.fetch import (  # noqa: F401
    ExtractOutcome,
    FetchOutcome,
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
    # #3511: the three RETURN types of the three fetch/extract entry points above. All three
    # published functions were uncallable type-safely — an app could `await fetch(...)` and then
    # not name what it was holding, so every consumer of a guarded fetch read `.status`/`.text`
    # off an `Any`. `FetchResponse` is `fetch`'s; `FetchOutcome`/`ExtractOutcome` are the
    # higher-level pipeline's, which also carry the refusal reason a caller has to branch on.
    "FetchResponse",
    "FetchOutcome",
    "ExtractOutcome",
    # EA-8: see the import comment above — exported so `a2a-action` consumes core's
    # deny-by-default posture instead of composing its own.
    "a2a_outbound_policy",
    "relayed_failure_copy",
]
