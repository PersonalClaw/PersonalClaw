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
``dm_policy="owner_only"`` down to ``pairing``; see :func:`admit`.

**One turn per message.** A channel delivers a message again whenever it is not sure its first
delivery landed: an acknowledgement it did not see, an update its next poll did not confirm, a
mailbox read again from the last place saved before a crash. The door takes each message ONCE, by
the channel's own id for it in the chat it came in (``message_id`` with ``channel_id``: a chat
service numbers its messages per chat), recorded in the home's record of deliveries already
received (:mod:`personalclaw.received`) before the gate is asked, so a restart reads it back. A
delivery made again (while the first one's turn runs, after it finished, after it failed, after a
restart) is answered :data:`ALREADY_RECEIVED` and changes nothing: no turn, no queued message, no
owner notification, no pairing reply, no answer to a digest. The owner's Retry in the chat is how
a message runs again, when she asks for it.

A message with no id of its own is refused (:data:`NO_MESSAGE_ID`), and the gate's reporter names
the channel that sent it: the door cannot keep its word for a message it cannot tell from the
next one, so every channel names its messages.

A channel that acts on a message itself before the door, or instead of it (a conversation it runs
itself, a pairing code it finds inside a mail, mail it hands only to its own automations), claims
the message at the top of its inbound path (:func:`claim_message`, ``personalclaw.sdk.channel``),
and the door then takes that message from the claim, once.

**One message, one owner notification.** A denied unknown sender has side effects:
:func:`~personalclaw.channel_trust.note_unknown_sender` raises an actionable owner notification
and writes a ``sender_denied`` SEL row. Two independent mechanisms keep a message to one, and
they are deliberately not the same mechanism:

1. **The record above** never hands a message the door took to the gate again, so "one inbound
   message produces at most one notification" holds *per message*, independent of any time window.
2. **The store's renotify window**
   (:data:`~personalclaw.channel_trust.UNKNOWN_SENDER_RENOTIFY_SECS`, 24h) dedupes on persisted
   per-sender state and returns ``False`` *before* emitting the SEL row or calling
   ``state.notify``, so even a call that bypasses this module entirely (a transport that calls
   ``guard_inbound`` with its own hands) cannot double-notify.

Mechanism 2 alone would leave the per-message property as a mere corollary of a flood-control
window — shorten the window and double-notification returns. Mechanism 1 is what makes it a
property. ``tests/test_channel_inbound_chokepoint.py`` asserts both, including with the window
monkeypatched to zero.

**Fail-closed.** A message is routed only on an explicit ``allowed`` verdict. There is no
branch that routes on an error, an unreadable store, or an unknown policy: the trust
store's own read path falls back to defaults, and the default DM policy is ``pairing`` —
absence of data means "not trusted".

**Fail-closed, not fail-quiet.** Failing closed silently made a healthy socket that
discards a message indistinguishable from a dead one. Every disposition is announced by the
gate's single reporter, :func:`~personalclaw.channel_trust.report_inbound_verdict`, reached
from inside :func:`~personalclaw.channel_trust.guard_inbound` — the one function every
verdict is now minted in, pairing redemption included, and a message with no id is refused
through it too. The two paths this module owns that the gate never sees are reported here
instead: a message delivered again (INFO: routine, since a channel retries and may announce one
message twice, so nothing is for an operator to do; the line names the channel and the message,
so a log kept at INFO shows a channel that gives two messages one id) and a missing dashboard
state (WARNING in :func:`_route_to_session` — an allowed message that still cannot reach a
session).
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from personalclaw import received
from personalclaw.channel_trust import TrustVerdict, guard_inbound, report_inbound_verdict

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from personalclaw.channel_transports.base import ChannelMessage
    from personalclaw.gateway_services import GatewayServices

logger = logging.getLogger(__name__)

#: The reason of a verdict whose message the door already took: a delivery of it made again.
ALREADY_RECEIVED = "already_received"

#: The reason of a verdict whose message came with no id of its own, which the door refuses.
NO_MESSAGE_ID = "no_message_id"

#: The reason of a verdict whose message was the owner's answer to the Morning triage digest
#: (`proactive.channel_reply`). Not allowed: it reached no session and is no message for a trigger
#: source, as a pairing code is none. It was answered, and the DM was told what it did.
ANSWERED_DIGEST = "answered_digest"

#: How many messages channels claimed themselves (:func:`claim_message`) the door remembers until
#: each is handed to it. One a channel handles without the door is never handed on, so the oldest
#: go; a message a channel queues behind a turn is handed on long before four thousand more come.
_CLAIMED_MAX = 4096

#: The messages channels claimed themselves and have not handed to the door yet, by home: the door
#: takes each of them once. In memory only: a restart ends every delivery in flight, and a message
#: delivered again after it is in the record, so its channel's claim says it arrived before.
_CLAIMED: "OrderedDict[tuple[str, str, str, str], None]" = OrderedDict()


def _identity(msg: "ChannelMessage") -> tuple[str, str]:
    """The chat *msg* came in and the channel's own id for it: what names it among every message
    its channel delivers. An id alone names nothing: a chat service numbers each chat's messages
    from one."""
    return str(msg.channel_id or ""), str(msg.message_id or "").strip()


def _door(provider: str) -> str:
    """The door the record of deliveries knows *provider*'s messages by."""
    return f"channel:{provider}"


def _claim_key(provider: str, msg: "ChannelMessage") -> tuple[str, str, str, str]:
    return (str(received.path()), provider, *_identity(msg))


def _refused_without_id(provider: str, msg: "ChannelMessage", *, is_dm: bool) -> TrustVerdict:
    """The verdict for a message its channel sent with no id: refused, and said by the gate's
    reporter (WARNING, once per sender or group a day), so the channel that sent it is named."""
    return report_inbound_verdict(
        provider,
        TrustVerdict(allowed=False, reason=NO_MESSAGE_ID),
        sender_id=str(msg.sender or ""),
        channel_id=str(msg.channel_id or ""),
        is_dm=is_dm,
    )


def _received_again(provider: str, msg: "ChannelMessage") -> TrustVerdict:
    """The verdict for a delivery of a message already taken: nothing runs, and the log says so."""
    chat, mid = _identity(msg)
    logger.info(
        "channel inbound already received: provider=%s channel=%s message=%s — delivered "
        "again; nothing runs for it a second time",
        provider,
        chat or "-",
        mid,
    )
    again = TrustVerdict(allowed=False, reason=ALREADY_RECEIVED)
    return again


def claim_message(provider: str, msg: "ChannelMessage") -> bool:
    """Claim *msg* for *provider*: True when its channel delivers it for the first time, False
    for a delivery of it made again, and for a message with no id, which is refused.

    For a channel that acts on a message itself before the door, or instead of it: a
    conversation it runs itself, a pairing code it finds inside a mail, mail it hands only to its
    own automations. Claimed at the top of its inbound path, before anything acts on the message,
    so no delivery after the first changes anything there either. A message the channel then
    hands to the door (``services.deliver_channel_inbound``) is taken from its claim, once. A
    channel whose every message goes straight to the door claims nothing: the door takes each
    message once on its own.
    """
    chat, mid = _identity(msg)
    if not mid:
        _refused_without_id(provider, msg, is_dm=True)
        return False
    if not received.first_arrival(_door(provider), chat, mid):
        _received_again(provider, msg)
        return False
    _CLAIMED[_claim_key(provider, msg)] = None
    while len(_CLAIMED) > _CLAIMED_MAX:
        _CLAIMED.popitem(last=False)
    return True


def _take(provider: str, msg: "ChannelMessage", *, is_dm: bool) -> TrustVerdict | None:
    """Take *msg* through the door: None when it arrives for the first time (or comes from its
    channel's claim), else the verdict that says why it does not go in."""
    chat, mid = _identity(msg)
    if not mid:
        return _refused_without_id(provider, msg, is_dm=is_dm)
    claimed = _claim_key(provider, msg)
    if claimed in _CLAIMED:
        del _CLAIMED[claimed]
        return None
    if received.first_arrival(_door(provider), chat, mid):
        return None
    return _received_again(provider, msg)


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
    """The trust decision for one inbound message — the gate, and nothing but the gate.

    This function deliberately holds NO branch of its own. It unpacks the transport's
    message into the gate's keyword shape and returns whatever the gate decided, so the
    door cannot reach a verdict the gate would not have reached. It is asked once per message:
    the door takes a message once (:func:`_take`) before it asks.

    ``hold_for_owner`` goes to the gate as it is: for a channel that speaks as the owner, how to
    hold a stranger's message for them (:func:`_hold_for_the_owner`).

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

    The door takes each message once, first of all (:func:`_take`): a delivery of a message it
    already took comes back not allowed, with :data:`ALREADY_RECEIVED`, no reply and nothing
    else done, and a message with no id comes back refused, with :data:`NO_MESSAGE_ID`.

    On ``allowed``, the text that enters the session is ``fenced_text`` when the gate
    produced one (non-owner group content, wrapped so a model reads it as DATA) and the
    raw text otherwise — the fence is applied by the gate, so a transport cannot forget it.

    A channel that speaks as the owner (:func:`speaks_as_owner`) is handed no pairing note for
    a stranger, and the stranger's direct message is held in the Inbox as someone new instead
    (:func:`_hold_for_the_owner`), where the owner replies to it, pairs them, or ignores it. The
    gate holds it, with the hold this door hands it, before it tells the owner, so the notice
    names only a message that is in the Inbox.

    An admitted direct message that is the channel owner's answer to the Morning triage digest
    that DM received (``3 yes``) is answered as the digest's card answers it
    (`proactive.channel_reply.answer_on_channel`) and reaches no session: the verdict comes back
    not allowed, with :data:`ANSWERED_DIGEST`, and the DM has been told what the answer did.

    ``turn_runner`` is INJECTED, never imported: driving a turn means calling
    ``dashboard.chat_runner.run_chat``, and importing that here would make ``channel_inbound``
    (domain) depend on the HTTP surface — the ``core-must-not-import-the-http-surface``
    inversion the structural gate exists to catch. The composition root (the gateway, which
    legitimately faces downward) hands the callable in, exactly as
    ``inbound.openai_dialect.register_routes`` takes its own ``turn_runner``.
    """
    refused = _take(provider, msg, is_dm=is_dm)
    if refused is not None:
        return refused
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
    # The owner's answer to the Morning triage digest this DM received (`3 yes`): the digest's
    # card's own answer path takes it and says what it did here, and no turn runs. The door took
    # the message before it asked, so a delivery of it made again answers nothing a second time.
    from personalclaw.proactive.channel_reply import answer_on_channel

    if await answer_on_channel(services, provider, msg, is_dm=is_dm):
        answered = TrustVerdict(allowed=False, reason=ANSWERED_DIGEST, meta={"answered": True})
        return answered
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
        state.link_channel(session.key, thread_key, msg.channel_id, provider=provider)

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
    from personalclaw.turn_source import arrived_on

    paths = keep_for_chat(files) if files else []
    # Where the message came from, recorded on its row (in the queue as well): the thread, its
    # sender and this channel, which every save of the chat writes back as they are. Memory reads
    # them to tell the owner's words from those of anyone else the door let in.
    source = arrived_on(thread_key, msg.sender, provider)

    if getattr(session, "running", False):
        # Queued the way a message typed in the dashboard mid-turn is: the queue adds it to
        # the chat when it runs it. Adding it here too put it in the chat twice.
        queue_id = (
            session.queue_append(text, channel=provider, files=paths, source=source)
            if paths
            else session.queue_append(text, channel=provider, source=source)
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
        session.append("user", safe, "msg msg-u", meta={"files": paths}, source=source)
    else:
        session.append("user", safe, "msg msg-u", source=source)
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
