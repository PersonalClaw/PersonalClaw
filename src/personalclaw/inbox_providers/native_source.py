"""Native inbox source — the always-on, push-based agent→inbox sink (seam S4).

Unlike the poll-based providers (filesystem, slack), the native source is a
**push** sink: an agent calls :func:`post_to_inbox` and the item is written
straight into the shared :class:`~personalclaw.inbox.InboxStore` (the same
``inbox.json`` every other source feeds) and broadcast live over the dashboard
WS. It is **always available** — independent of ``cfg.inbox.enabled`` and any
external provider — so the Inbox is useful out-of-the-box: any agent (chat,
goal loop, scheduled run, space member) can surface "I finished X", "I need a
decision on Y", "heads up about Z" with no Slack/email connected.

S4 pattern (shared, not a common base class): a native-always-on provider +
external pluggable providers + per-item ``source`` attribution + per-provider
health. This module owns the native half.
"""

from __future__ import annotations

import logging
import time
import uuid

from personalclaw.inbox import (
    Classification,
    Confidence,
    InboxItem,
    InboxStore,
    ItemStatus,
)

logger = logging.getLogger(__name__)

SOURCE_NAME = "native"

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
    Returns the created item, or None if no dashboard state is wired.
    """
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
        confidence=Confidence.HIGH.value,
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
        from personalclaw.config.loader import AppConfig
        from personalclaw.inbox import evaluate_alert, notify_inbox_alert
        from personalclaw.providers.entity_routes import load_inbox_settings

        reason = evaluate_alert(
            item, load_inbox_settings(), AppConfig.load().dashboard.user_name or ""
        )
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
