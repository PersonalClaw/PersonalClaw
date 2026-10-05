"""Native inbox source — the always-on, push-based agent→inbox sink (seam S4).

Unlike the poll-based providers (filesystem, plus any channel app's source), the
native source is a
**push** sink: an agent calls :func:`post_to_inbox` and the item is written
straight into the shared :class:`~personalclaw.inbox.InboxStore` (the same
``inbox.json`` every other source feeds) and broadcast live over the dashboard
WS. It is **always available** — independent of ``cfg.inbox.enabled`` and any
external provider — so the Inbox is useful out-of-the-box: any agent (chat,
goal loop, scheduled run, space member) can surface "I finished X", "I need a
decision on Y", "heads up about Z" with no external channel connected.

S4 pattern (shared, not a common base class): a native-always-on provider +
external pluggable providers + per-item ``source`` attribution + per-provider
health. This module owns the native half.

The work of an Incognito or Temporary chat posts nothing here: an item is kept after the chat and
read by the agents of your other chats, so :func:`post_to_inbox` refuses it before anything is
written (``lasting_work``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from personalclaw import attachments, lasting_work
from personalclaw.attachments import Attachment
from personalclaw.inbox import (
    Classification,
    InboxItem,
    InboxState,
    InboxStore,
    ItemKind,
    ItemStatus,
)

if TYPE_CHECKING:
    from personalclaw.subagent_reach import Reader

logger = logging.getLogger(__name__)

SOURCE_NAME = "native"

#: The ``refs`` key of a row held from someone new (:func:`hold_from_someone_new`): the channel
#: they wrote on. A reply to the row goes back through that channel, and Pair lets them talk to
#: the agent there.
SOMEONE_NEW_REF = "someone_new"

#: A held row's ``source``: the channel's key after this prefix. Not a polled source's name, so
#: no polled source is ever asked to send a reply to it.
CHANNEL_SOURCE_PREFIX = "channel:"

# kind → (classification, can_reply). A question wants a decision back (routes to
# the posting agent's session); notification/fyi are read-only heads-ups.
_KIND_MAP = {
    "question": (Classification.NEEDS_REPLY.value, True),
    "notification": (Classification.FYI.value, False),
    "fyi": (Classification.FYI.value, False),
}

# The single process-wide dashboard state, set at startup, used to persist + push.
# Decoupled from the (currently stubbed) inbox service so the native sink works
# even when no polling service runs.
_dashboard_state = None


def set_dashboard_state(state) -> None:
    """Register the dashboard state the native sink writes/broadcasts through."""
    global _dashboard_state
    _dashboard_state = state


def get_dashboard_state():
    """The process-wide dashboard state registered at startup, or None if unset.

    The single reusable hook for code paths that run OUTSIDE an HTTP request (which
    would otherwise read ``request.app['state']``) — e.g. a builtin agent tool that
    needs to create/launch a Code project or Goal Loop on the user's behalf."""
    return _dashboard_state


def _store_from_state(state) -> InboxStore:
    """The live InboxStore — the running service's instance if present, else the
    state's lazily-loaded disk-backed store (mirrors handlers_inbox._get_inbox)."""
    svc = getattr(state, "_inbox_svc", None)
    if svc is not None:
        return svc.inbox
    store = getattr(state, "_inbox_store", None)
    if store is None:
        store = InboxStore()
        store.load()
        state._inbox_store = store
    return store


def _inbox_state_from_state(state) -> InboxState:
    """The live InboxState (dismissed rows, muted threads), as :func:`_store_from_state` finds
    the store."""
    svc = getattr(state, "_inbox_svc", None)
    if svc is not None:
        return svc.state
    inbox_state = getattr(state, "_inbox_state", None)
    if inbox_state is None:
        inbox_state = InboxState()
        inbox_state.load()
        state._inbox_state = inbox_state
    return inbox_state


def hold_from_someone_new(
    state: Any,
    *,
    provider: str,
    channel_name: str,
    channel_id: str,
    sender_id: str,
    text: str,
    sender_name: str = "",
    subject: str = "",
    thread_id: str = "",
    message_id: str = "",
    ts: float = 0.0,
    files: list[Attachment] | None = None,
) -> InboxItem | None:
    """Hold a message from someone a channel does not know in the Inbox, as someone new.

    ``files`` are the files the message came with; the row keeps and lists them
    (``attachments.keep``), as a polled message's row does.

    For a channel that speaks as the owner (``ChannelCapabilities.speaks_as_owner``): nothing
    was sent to the sender, and nothing is until the owner answers the row. Reply sends the
    owner's words back through that channel, threaded under the message (``POST
    /api/inbox/send``); Pair lets the sender talk to the agent there (``POST
    /api/inbox/{id}/pair``); Dismiss ignores it.

    One row per message. Its id comes from the channel, the message's own id and the time the
    channel stamped on it, so a redelivery finds its row, and one the owner dismissed stays
    dismissed; a thread the owner muted holds nothing more. No inbox event is raised for it, so
    a sender the gate refused arms no automation. Returns the row, or None when it was not added
    (no Inbox to hold it in, there already or dismissed, or its thread is muted): the gate tells
    the owner only of a row this returned.
    """
    if state is None:
        logger.warning("no dashboard state: a %s message from someone new was not held", provider)
        return None
    store = _store_from_state(state)
    inbox_state = _inbox_state_from_state(state)
    stamp = float(ts or 0) or time.time()
    if message_id:
        basis = [provider, channel_id, message_id]
    else:
        basis = [provider, channel_id, sender_id, repr(stamp), thread_id, text]
    key = hashlib.sha256("\0".join(basis).encode("utf-8")).hexdigest()[:16]
    item_id = f"someone_new_{key}_{stamp}"
    thread = thread_id or message_id
    if item_id in store.items or item_id in inbox_state.dismissed:
        return None
    if thread and thread in inbox_state.muted_threads:
        return None
    item = InboxItem(
        id=item_id,
        channel=channel_id,
        channel_name="DM",
        thread_ts=thread_id or None,
        message=f"{subject}\n\n{text}" if subject else text,
        sender_id=sender_id,
        sender_name=sender_name or sender_id,
        status=ItemStatus.PENDING.value,
        created_at=stamp,
        source=f"{CHANNEL_SOURCE_PREFIX}{provider}",
        can_reply=True,
        reply_target=message_id,
        item_kind=ItemKind.MESSAGE.value,
        refs={SOMEONE_NEW_REF: provider, "channel_name": channel_name},
        attachments=attachments.keep(item_id, files) if files else [],
    )
    store.add(item)
    store.flush()
    try:
        from personalclaw.inbox import redact_item

        state.broadcast_ws("inbox_new_item", redact_item(item.to_dict()))
    except Exception:
        logger.debug("hold_from_someone_new: broadcast failed", exc_info=True)
    # It arrives unsorted, like any message: the running Inbox sorts it.
    from personalclaw.inbox_sorting import InboxSorter

    sorter = getattr(getattr(state, "_inbox_svc", None), "sorter", None)
    if isinstance(sorter, InboxSorter):
        sorter.wake()
    return item


async def open_inbox_items(reader: Reader, *, kind: str = "") -> list[InboxItem] | None:
    """The Inbox rows still wanting the owner (``OPEN_STATUSES``) that *reader* reads, newest
    first, for an agent: its own chat's and those about no chat (``inbox_reach``), every one for
    you. *reader*'s state is the dashboard state whose Inbox it reads.

    ``None`` when no dashboard state is wired, which is "the Inbox could not be read", never an
    empty Inbox. Reads only: it takes in what another writer put in the file first (as the
    Inbox's own list does), and marks nothing seen. ``kind`` narrows to one item kind. The store
    is read on the loop that owns it, as the Inbox's routes read it: taking in another writer's
    rows changes the store, and nothing changes it off that loop. Whose chat each row is for is
    told off the loop, since the walk to it reads records.
    """
    from personalclaw import inbox_reach

    if reader.state is None:
        return None
    store = _store_from_state(reader.state)
    store.refresh()
    items = [
        i
        for i in store.open_items()
        if not kind or str(getattr(i.item_kind, "value", i.item_kind) or "message") == kind
    ]
    shown = await asyncio.to_thread(inbox_reach.readable, reader, items)
    return sorted(shown, key=lambda i: float(i.created_at or 0.0), reverse=True)


def post_to_inbox(
    message: str,
    *,
    kind: str = "notification",
    sender_name: str = "agent",
    context: str | None = None,
    reply_target: str = "",
    state=None,
) -> InboxItem | None:
    """Push an agent-authored item into the inbox queue (the native source).

    ``kind`` is ``notification`` / ``question`` / ``fyi``: a ``question`` is
    classified ``needs_reply`` and is replyable (the reply routes to
    ``reply_target`` — the posting agent's session); the others are FYI heads-ups.
    That verdict is the posting agent's own word, so it carries no confidence and no
    maker to rate, and the sorter never reads the post (``inbox_sorting.wants_sorting``).
    Returns the created item, or None if no dashboard state is wired. Refused first, for the
    work of an Incognito or Temporary chat (``lasting_work.Refused``).
    """
    lasting_work.refuse(lasting_work.INBOX, lasting_work.CREATE)
    st = state or _dashboard_state
    if st is None:
        logger.debug("post_to_inbox: no dashboard state wired; dropping item")
        return None

    classification, can_reply = _KIND_MAP.get(kind, _KIND_MAP["notification"])
    now = time.time()
    # id is {channel}_{ts}; the ts property splits on the last "_".
    item_id = f"agent_{now:.6f}-{uuid.uuid4().hex[:6]}"
    item = InboxItem(
        id=item_id,
        channel="agent",
        channel_name="agent",
        thread_ts=None,
        message=message,
        sender_id=sender_name,
        sender_name=sender_name,
        classification=classification,
        status=ItemStatus.PENDING.value,
        created_at=now,
        context_summary=context or "",
        source=SOURCE_NAME,
        can_reply=can_reply,
        reply_target=reply_target if can_reply else "",
    )
    store = _store_from_state(st)
    store.add(item)
    store.flush()

    # Alert evaluation at ingestion — same rules as polled sources (keyword /
    # name-mention from the inbox entity settings) so an agent-pushed "urgent"
    # question fires the notification too.
    try:
        from personalclaw.identity import operator_name
        from personalclaw.inbox import evaluate_alert, notify_inbox_alert

        reason = evaluate_alert(item, operator_name())
        if reason:
            notify_inbox_alert(st, item, reason)
    except Exception:
        logger.debug("post_to_inbox: alert evaluation failed", exc_info=True)

    try:
        from personalclaw.dashboard.handlers_inbox import _redact_item

        st.broadcast_ws("inbox_new_item", _redact_item(item.to_dict()))
    except Exception:
        logger.debug("post_to_inbox: broadcast failed", exc_info=True)
    return item
