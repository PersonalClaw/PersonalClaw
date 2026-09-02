"""The ONE guarded door an inbound channel message enters the platform through (EA-7).

``channel_trust`` shipped a complete trust store — ``guard_inbound``, pairing codes, the
unknown-sender flow, the content fence — and then shipped with **zero
production callers**. Every transport was expected to call the gate itself, at the top of
its own inbound path, by convention. Three app transports did. That is fail-OPEN in
aggregate: a transport that simply *omits* the call reaches a live agent session with no
check at all, and nothing in core notices. For a control whose entire purpose is to be
fail-CLOSED, "every implementor remembered" is not a property, it is a hope.

**What this module is.** The single function a transport hands an inbound message to —
:func:`deliver_inbound` — which applies trust and *then* routes to a session. A transport
no longer decides whether to check; it decides only whether to use the platform's inbound
door, and the door is guarded. The gate is reached through the gateway's
:class:`~personalclaw.gateway_services.GatewayServices` handle
(``services.deliver_channel_inbound``), which is what the transport already holds from
``start_inbound`` — so no transport ABC changed and a transport that never calls
``guard_inbound`` itself is still checked.

**It is a chokepoint, not a second copy of the policy.**
:func:`~personalclaw.channel_trust.guard_inbound` remains the one decision function and
this module never re-derives a verdict: it calls the gate, caches the answer per message,
and acts on it. Pairing redemption likewise delegates to the shipped
:func:`~personalclaw.channel_trust.redeem_pairing_code`. Nothing here is a policy.

**Idempotent per message — the double-notification hazard.** A denied unknown sender has
side effects: :func:`~personalclaw.channel_trust.note_unknown_sender` raises an actionable
owner notification and writes a ``sender_denied`` SEL row. Three app transports already
call ``guard_inbound`` themselves, so while they migrate to this door a single inbound
message can be presented to the trust vocabulary twice. Two independent mechanisms make
that safe, and they are deliberately not the same mechanism:

1. **This module's admission cache** (:data:`_ADMISSION_CACHE_MAX` entries, keyed by
   provider + message identity) returns the FIRST verdict for a message and never
   re-enters the gate — so the property "one inbound message produces at most one
   notification" holds *per message*, independent of any time window.
2. **The store's renotify window**
   (:data:`~personalclaw.channel_trust.UNKNOWN_SENDER_RENOTIFY_SECS`, 24h) dedupes on
   persisted per-sender state and returns ``False`` *before* emitting the SEL row or
   calling ``state.notify``, so even a second call that bypasses this module entirely
   (an un-migrated transport's own ``guard_inbound``) cannot double-notify.

Mechanism 2 alone would leave the per-message property as a mere corollary of a
flood-control window — shorten the window and double-notification returns. Mechanism 1 is
what makes it a property. ``tests/test_channel_inbound_chokepoint.py`` asserts both,
including with the window monkeypatched to zero.

**Fail-closed.** A message is routed only on an explicit ``allowed`` verdict. There is no
branch that routes on an error, an unreadable store, or an unknown policy: the trust
store's own read path falls back to defaults, and the default DM policy is ``pairing`` —
absence of data means "not trusted".
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from personalclaw.channel_trust import (
    TrustVerdict,
    guard_inbound,
    is_allowed_sender,
    redeem_pairing_code,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from personalclaw.channel_transports.base import ChannelMessage
    from personalclaw.gateway_services import GatewayServices

logger = logging.getLogger(__name__)

#: How many recent (provider, message) admissions to remember. Bounded so a busy channel
#: cannot grow this without limit; FIFO eviction. Sized well above any plausible burst of
#: re-presentations of the SAME message (a transport double-calling, a provider redelivery
#: after a network blip), which is all the cache exists to absorb.
_ADMISSION_CACHE_MAX = 512

#: provider + message identity → the verdict already reached for it.
_ADMITTED: "OrderedDict[str, TrustVerdict]" = OrderedDict()

#: The reply a sender gets when the message they sent WAS a valid pairing code. The code
#: is consumed and the sender becomes trusted, but this message is not a turn for the
#: agent — so it is reported ``allowed=False`` with ``reason="paired"``.
PAIRED_REPLY = "Paired — you can talk to me now."


def _message_key(provider: str, msg: "ChannelMessage") -> str:
    """A stable identity for one inbound message, for admission caching.

    Prefers the provider's own ``message_id`` (every real transport sets one). Falls back
    to channel + timestamp + a digest of the text, which is stable for the same message
    object re-presented and distinct for a genuinely different message — the property the
    cache needs. The text is hashed rather than stored so a long or sensitive body never
    sits in an in-memory key.
    """
    if msg.message_id:
        return f"{provider}|id|{msg.message_id}"
    digest = hashlib.sha256((msg.text or "").encode("utf-8")).hexdigest()[:32]
    return f"{provider}|syn|{msg.channel_id}|{msg.ts}|{digest}"


def _remember(key: str, verdict: TrustVerdict) -> None:
    _ADMITTED[key] = verdict
    while len(_ADMITTED) > _ADMISSION_CACHE_MAX:
        _ADMITTED.popitem(last=False)


def reset_admissions() -> None:
    """Forget every cached admission. For tests and for a transport restart."""
    _ADMITTED.clear()


def admit(state: Any, provider: str, msg: "ChannelMessage", *, is_dm: bool = True) -> TrustVerdict:
    """The trust decision for one inbound message — idempotent per message.

    Calls :func:`~personalclaw.channel_trust.guard_inbound` at most ONCE per
    ``(provider, message)``; a repeat presentation of the same message returns the first
    verdict with no side effects (no second owner notification, no second SEL row). See
    the module docstring for why that is a property of this cache and not of the store's
    renotify window.

    Before the gate, a DM from an unpaired sender whose entire text is an 8-digit-shaped
    numeric code is offered to :func:`~personalclaw.channel_trust.redeem_pairing_code` —
    the shipped redemption function, not a copy. A successful redemption trusts the sender
    for every LATER message and consumes this one (``allowed=False``, ``reason="paired"``),
    because a pairing code is not something the agent should be asked to answer.
    """
    key = _message_key(provider, msg)
    cached = _ADMITTED.get(key)
    if cached is not None:
        return cached
    verdict = _decide(state, provider, msg, is_dm=is_dm)
    _remember(key, verdict)
    return verdict


def _decide(state: Any, provider: str, msg: "ChannelMessage", *, is_dm: bool) -> TrustVerdict:
    """One uncached trust decision. The ONLY place this module enters the trust gate."""
    sender_name = ""
    if isinstance(msg.metadata, dict):
        sender_name = str(msg.metadata.get("sender_name", "") or "")

    if is_dm and not is_allowed_sender(provider, msg.sender):
        candidate = (msg.text or "").strip()
        if candidate.isdigit() and redeem_pairing_code(provider, msg.sender, candidate):
            return TrustVerdict(
                allowed=False,
                reason="paired",
                canned_reply=PAIRED_REPLY,
                meta={"paired": True},
            )

    return guard_inbound(
        state,
        provider,
        msg.sender,
        sender_name=sender_name,
        channel_id=msg.channel_id,
        is_dm=is_dm,
        text=msg.text,
    )


async def deliver_inbound(
    services: "GatewayServices",
    provider: str,
    msg: "ChannelMessage",
    *,
    is_dm: bool = True,
    turn_runner: "Callable[[Any, Any, str], Awaitable[None]]",
) -> TrustVerdict:
    """Apply trust to one inbound message and, only if allowed, drive an agent turn.

    This is the platform's inbound door. The returned :class:`TrustVerdict` tells the
    transport what happened so it can render the channel-specific outbound half itself:
    a non-empty ``canned_reply`` is text the transport SHOULD deliver back to the sender
    (the pairing-needed prompt, or the paired confirmation). Core does not render it,
    because outbound formatting belongs in the channel's own bundle.

    On ``allowed``, the text that enters the session is ``fenced_text`` when the gate
    produced one (non-owner group content, wrapped so a model reads it as DATA) and the
    raw text otherwise — the fence is applied by the gate, so a transport cannot forget it.

    ``turn_runner`` is INJECTED, never imported: driving a turn means calling
    ``dashboard.chat_runner.run_chat``, and importing that here would make ``channel_inbound``
    (domain) depend on the HTTP surface — the ``core-must-not-import-the-http-surface``
    inversion the structural gate exists to catch. The composition root (the gateway, which
    legitimately faces downward) hands the callable in, exactly as
    ``inbound.openai_dialect.register_routes`` takes its own ``turn_runner``.
    """
    state = getattr(services, "dashboard_state", None)
    verdict = admit(state, provider, msg, is_dm=is_dm)
    if not verdict.allowed:
        logger.debug("channel inbound denied: provider=%s reason=%s", provider, verdict.reason)
        return verdict
    await _route_to_session(services, provider, msg, verdict.fenced_text or msg.text, turn_runner)
    return verdict


async def _route_to_session(
    services: "GatewayServices",
    provider: str,
    msg: "ChannelMessage",
    text: str,
    turn_runner: "Callable[[Any, Any, str], Awaitable[None]]",
) -> None:
    """Link a dashboard session to this channel thread and drive one turn.

    Reached only from :func:`deliver_inbound`, and only past an ``allowed`` verdict — the
    reason this is private. Every channel app used to carry its own copy of this routing;
    core owning it is what makes the trust check unavoidable rather than conventional.
    """
    state = getattr(services, "dashboard_state", None)
    if state is None:
        logger.warning("channel inbound: no dashboard state — cannot route %s message", provider)
        return

    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    thread_key = msg.thread_id or msg.channel_id
    session = state.get_linked_session(thread_key)
    if session is None:
        session = state.get_or_create_session(app=provider)
        state.link_channel(session.key, thread_key, msg.channel_id)

    safe, _ = redact_exfiltration_urls(text)
    safe, _ = redact_credentials(safe)
    session.append("user", safe, "msg msg-u")

    if getattr(session, "running", False):
        session.queue_append(text)
        return

    task = asyncio.ensure_future(turn_runner(state, session, text))
    session.task = task
    tasks = getattr(state, "_background_tasks", None)
    if tasks is not None:
        tasks.add(task)
        task.add_done_callback(tasks.discard)
