"""The ONE guarded door an inbound channel message enters the platform through.

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
and acts on it. Nothing here is a policy — including pairing redemption, which happens
inside the gate under the provider's DM policy and *not* on the way to it. This module
once redeemed a code before the gate and regardless of policy, which silently levelled
``dm_policy="owner_only"`` down to ``pairing``; see :func:`_decide`.

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

**Fail-closed, not fail-quiet.** Failing closed silently made a healthy socket that
discards a message indistinguishable from a dead one. Every disposition is announced by the
gate's single reporter, :func:`~personalclaw.channel_trust.report_inbound_verdict`, reached
from inside :func:`~personalclaw.channel_trust.guard_inbound` — the one function every
verdict is now minted in, pairing redemption included. The two paths this module owns that
the gate never sees are reported here instead: an admission-cache hit (DEBUG — a redelivery
of an already-decided message is the routine dedup) and a missing dashboard state (WARNING
in :func:`_route_to_session` — an allowed message that still cannot reach a session).
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from personalclaw.channel_trust import TrustVerdict, guard_inbound

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


def speaks_as_owner(provider: str) -> bool:
    """Whether the channel ``provider`` sends as the owner themselves, not as a bot
    (``ChannelCapabilities.speaks_as_owner``): a stranger is then answered by nobody but the owner.

    Read from the channel registered under ``provider``. One whose capabilities cannot be read
    counts as speaking for the owner, the side on which nothing is sent to a stranger."""
    from personalclaw.channel_transports import get_transport

    transport = get_transport(provider)
    if transport is None:
        return False
    try:
        return bool(transport.capabilities().speaks_as_owner)
    except Exception:  # noqa: BLE001 - fail closed: answer no stranger in the owner's name
        logger.warning("channel %s: its capabilities could not be read", provider, exc_info=True)
        return True


def admit(
    state: Any,
    provider: str,
    msg: "ChannelMessage",
    *,
    is_dm: bool = True,
    hold_for_owner: "Callable[[], bool] | None" = None,
) -> TrustVerdict:
    """The trust decision for one inbound message — idempotent per message.

    Calls :func:`~personalclaw.channel_trust.guard_inbound` at most ONCE per
    ``(provider, message)``; a repeat presentation of the same message returns the first
    verdict with no side effects (no second owner notification, no second SEL row, no second
    hold). See the module docstring for why that is a property of this cache and not of the
    store's renotify window.

    ``hold_for_owner`` goes to the gate as it is: for a channel that speaks as the owner, how to
    hold a stranger's message for them (:func:`_hold_for_the_owner`).

    Pairing redemption is NOT attempted here: it lives inside the gate, which applies the
    provider's DM policy to it. See :func:`_decide`.
    """
    key = _message_key(provider, msg)
    cached = _ADMITTED.get(key)
    if cached is not None:
        # DEBUG, and deliberately not a re-report: the disposition was already announced at
        # its derived level when it was first reached, and a provider redelivering the same
        # message must not be able to repeat an operator-visible line. This is the genuinely
        # routine dedup in the inbound path — unlike a denied sender, which is a decision.
        logger.debug(
            "channel inbound already decided: provider=%s reason=%s allowed=%s",
            provider,
            cached.reason,
            cached.allowed,
        )
        return cached
    verdict = _decide(state, provider, msg, is_dm=is_dm, hold_for_owner=hold_for_owner)
    _remember(key, verdict)
    return verdict


def _decide(
    state: Any,
    provider: str,
    msg: "ChannelMessage",
    *,
    is_dm: bool,
    hold_for_owner: "Callable[[], bool] | None",
) -> TrustVerdict:
    """One uncached trust decision — the gate, and nothing but the gate.

    This function deliberately holds NO branch of its own. It unpacks the transport's
    message into the gate's keyword shape and returns whatever the gate decided, so the
    door cannot reach a verdict the gate would not have reached.

    It used to redeem a pairing code here, *before* calling the gate and without consulting
    the provider's ``dm_policy`` — so an 8-digit-shaped DM paired its sender even under
    ``dm_policy="owner_only"``, where the owner's Allow is meant to be the only door. That
    made ``owner_only`` no stronger than ``pairing``: a policy decision (the gate's) was
    pre-empted by a credential-redemption side effect (this module's). Redemption now
    happens only inside :func:`~personalclaw.channel_trust.guard_inbound`, which redeems
    under policy ``pairing`` and refuses — without consuming the code — under ``owner_only``.
    """
    meta = msg.metadata if isinstance(msg.metadata, dict) else {}

    return guard_inbound(
        state,
        provider,
        msg.sender,
        sender_name=str(meta.get("sender_name", "") or ""),
        channel_id=msg.channel_id,
        is_dm=is_dm,
        text=msg.text,
        channel_name=str(meta.get("channel_name", "") or ""),
        hold_for_owner=hold_for_owner,
    )


def _hold_for_the_owner(state: Any, provider: str, msg: "ChannelMessage") -> bool:
    """Hold a stranger's message in the Inbox as someone new, for a channel that speaks as the
    owner (``native_source.hold_from_someone_new``); returns whether it was added. Nothing was
    sent to them, and the owner answers the row.

    The gate calls it, as ``hold_for_owner``, before it tells the owner anything, and tells them
    only of a message this added: one the Inbox did not take (its thread muted, its row
    dismissed) is named in no notice."""
    from personalclaw.channel_delivery import channel_shown_as
    from personalclaw.inbox_providers.native_source import hold_from_someone_new

    meta = msg.metadata if isinstance(msg.metadata, dict) else {}
    row = hold_from_someone_new(
        state,
        provider=provider,
        channel_name=channel_shown_as(provider),
        channel_id=msg.channel_id,
        sender_id=msg.sender,
        sender_name=str(meta.get("sender_name", "") or ""),
        subject=str(meta.get("subject", "") or ""),
        text=msg.text,
        thread_id=msg.thread_id,
        message_id=msg.message_id,
        ts=float(msg.ts or 0),
        files=list(msg.files),
    )
    return row is not None


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

    A channel that speaks as the owner (:func:`speaks_as_owner`) is handed no pairing note for
    a stranger, and the stranger's direct message is held in the Inbox as someone new instead
    (:func:`_hold_for_the_owner`), where the owner replies to it, pairs them, or ignores it. The
    gate holds it, with the hold this door hands it, before it tells the owner, so the notice
    names only a message that is in the Inbox.

    ``turn_runner`` is INJECTED, never imported: driving a turn means calling
    ``dashboard.chat_runner.run_chat``, and importing that here would make ``channel_inbound``
    (domain) depend on the HTTP surface — the ``core-must-not-import-the-http-surface``
    inversion the structural gate exists to catch. The composition root (the gateway, which
    legitimately faces downward) hands the callable in, exactly as
    ``inbound.openai_dialect.register_routes`` takes its own ``turn_runner``.
    """
    state = getattr(services, "dashboard_state", None)
    hold = (
        functools.partial(_hold_for_the_owner, state, provider, msg)
        if speaks_as_owner(provider)
        else None
    )
    verdict = admit(state, provider, msg, is_dm=is_dm, hold_for_owner=hold)
    if not verdict.allowed:
        # No log line here: this used to be a bare DEBUG that named neither the channel nor
        # the sender, so it could not answer "which channel do I have to track?" — and it
        # fired on an admission-cache hit too, re-announcing a decision already reported.
        # `report_inbound_verdict` owns the announcement now, at a level derived from the
        # verdict, which is also why the three transports that call `guard_inbound` directly
        # (and therefore never reach this line) are fixed by the same change.
        return verdict
    # The files ride along as the turn's own attached files only when the message enters as
    # someone the owner trusts (no fence): an attachment's text is read into the turn as the
    # user's, and a fenced sender's words are not the user's.
    await _route_to_session(
        services,
        provider,
        msg,
        verdict.fenced_text or msg.text,
        turn_runner,
        files=[] if verdict.fenced_text else list(msg.files),
    )
    return verdict


async def _route_to_session(
    services: "GatewayServices",
    provider: str,
    msg: "ChannelMessage",
    text: str,
    turn_runner: "Callable[[Any, Any, str], Awaitable[None]]",
    *,
    files: "list[Any] | None" = None,
) -> None:
    """Link a dashboard session to this channel thread and drive one turn.

    Reached only from :func:`deliver_inbound`, and only past an ``allowed`` verdict — the
    reason this is private. Every channel app used to carry its own copy of this routing;
    core owning it is what makes the trust check unavoidable rather than conventional.

    *files* become the turn's attached files (``attachments.keep_for_chat``), as a file the
    owner attaches in the dashboard does: the chat lists each one, and the turn reads it.
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

    # The links in the message are the user's for the chat's web_fetch, as a message typed in the
    # dashboard is — when it entered as the user's words. Text the gate fenced (someone the owner
    # has not trusted, in a tracked group) is data, and the recorder skips what is inside a fence.
    from personalclaw.constants import dashboard_history_key
    from personalclaw.web.fetch import record_user_message_urls

    record_user_message_urls(dashboard_history_key(session.key), text)

    safe, _ = redact_exfiltration_urls(text)
    safe, _ = redact_credentials(safe)
    broadcast = getattr(state, "broadcast_ws", None)
    push = getattr(state, "push_sessions_update", None)
    from personalclaw.attachments import keep_for_chat

    paths = keep_for_chat(files) if files else []

    if getattr(session, "running", False):
        # Queued the way a message typed in the dashboard mid-turn is: the queue adds it to
        # the chat when it runs it. Adding it here too put it in the chat twice.
        queue_id = (
            session.queue_append(text, channel=provider, files=paths)
            if paths
            else session.queue_append(text, channel=provider)
        )
        if broadcast is not None:
            broadcast(
                "queue_push",
                {
                    "session": session.key,
                    "content": safe,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "queue_id": queue_id,
                },
            )
        if push is not None:
            push()
        return

    if paths:
        session.append("user", safe, "msg msg-u", meta={"files": paths})
    else:
        session.append("user", safe, "msg msg-u")
    # ``Session.append`` skips the global broadcast for role="user" because the
    # dashboard frontend adds its OWN sends optimistically — but this user line
    # originated in a channel, so no frontend has it. Broadcast it explicitly, and
    # refresh the session list, so a dashboard watching the linked session sees the
    # channel message arrive live. Slack's pre-door intercept always did this; the
    # other channels' hand-rolled copies never did — the door gives every channel
    # the slack behavior.
    if broadcast is not None:
        broadcast(
            "chat_message",
            {"session": session.key, "role": "user", "content": safe, "cls": "msg msg-u"},
        )
    if push is not None:
        push()

    task = asyncio.ensure_future(turn_runner(state, session, text))
    session.task = task
    tasks = getattr(state, "_background_tasks", None)
    if tasks is not None:
        tasks.add(task)
        task.add_done_callback(tasks.discard)
