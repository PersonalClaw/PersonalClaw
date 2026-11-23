"""What a chat raised in your Inbox and your notifications, once the chat goes: deleted
(``chat_forget.delete_chats``) or a Temporary chat ended (``chat_forget.forget_temporary_chat``),
both through ``chat_forget.purge_chat``.

The owner's call (2026-10-14). Each decision the chat asked of you stays in the Inbox as a short
record: an approval, however it ended, and a call denied because nobody answered or nobody was
there to ask. That's a consent record, and the Audit log keeps each call and decision as well. The
record says what was asked (the tool), when, and how it ended. It holds nothing of the chat: not
the purpose, the arguments, who asked, or the chat's name. It opens nothing (:data:`CHAT_DELETED`),
and it keeps the chat's key, so no agent reads it (``inbox_reach``). Everything else the chat
raised there goes: a question, a note, a helper's report. So does every notification that names
the chat, the decisions' own included. Open tabs hear what moved. A proposal learned from the
chat is its own store's to withdraw, which keeps it as a withdrawn row without its words.

The Inbox and the notification log are shared stores, keyed by row and not by chat, so the
chat's own purge never reached them.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from typing import Any

from personalclaw.proposals_contract import REFS_KEY

logger = logging.getLogger(__name__)

#: The ref a record kept from a chat that is gone carries (``"1"``). Its other refs keep nothing
#: that says where it was answered (no ``place_*``, no ``chat``), so the Inbox offers nothing to
#: open and nothing to ask again.
CHAT_DELETED = "chat_deleted"

#: What a kept record's refs keep: what it was (an approval's id, a denied call's kind and tool)
#: and the chat's key.
_KEPT_REFS = ("approval", "auto_denied", "tool", "session")

#: The refs that mark a proposal learned from the chat (a learning proposal, a skill proposal,
#: the shared proposals contract's): its own store withdraws it as the chat goes.
_WITHDRAWN_BY_THEIR_STORE = ("learning_proposal", "skill_proposal", REFS_KEY)

#: What a tool's own name looks like (``bash``, ``web_fetch``, ``mcp__notes__search``). The
#: approval's tool is the call's display title, and an agent CLI's is the call itself (the
#: command, or the path it reads: ``acp.translate``), so the record keeps it only in this shape.
_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")

#: How an approval ended, by the word its settled title starts with (``approval_state.SETTLED_AS``).
_ENDED_AS = {
    "Approved": "It was approved.",
    "Denied": "It was denied.",
    "Expired": "Nobody answered, so it expired.",
    "Cancelled": "It was cancelled.",
}


def forget_chat_rows(state: Any, keys: Iterable[str]) -> tuple[int, int, int]:
    """For the chat every spelling of whose key *keys* holds: keep each decision it asked for as
    a record, delete its other Inbox rows, and delete every notification that names it. Returns
    how many rows were kept as records, how many were deleted, and how many notifications went."""
    from personalclaw.inbox import OPEN_STATUSES, ItemStatus, live_store, set_item_status

    gone = frozenset(k for k in keys if k)
    store = live_store(state) or _stored()
    if not gone or store is None:
        return 0, 0, 0
    store.refresh()
    of_the_chat = _chat_test(state, gone)
    mine = [item for item in list(store.items.values()) if _of_the_chat(item, of_the_chat)]
    kept = [item for item in mine if _is_a_decision(item)]
    dropped = [item.id for item in mine if not _is_a_decision(item)]
    for item in kept:
        _as_a_record(item)
    closing = [item for item in kept if item.status in OPEN_STATUSES]
    # An open decision ends here: its chat is gone, so nobody can answer it there.
    moved = {item.id for item in set_item_status(state, store, closing, ItemStatus.EXPIRED.value)}
    if kept:
        store.save()
    removed = store.remove(dropped, state=state)
    _announce(state, [item for item in kept if item.id not in moved], removed)
    ids = frozenset(item.id for item in mine)
    notes = 0
    try:
        notes = state.delete_notifications_for_chat(of_the_chat, items=ids)
    except (
        Exception
    ):  # noqa: BLE001 - the rows have gone already; the bell is told on its next read
        logger.warning("forget: the chat's notifications could not be removed", exc_info=True)
    return len(kept), len(removed), notes


def _stored() -> Any:
    """The Inbox as its file holds it, when no Inbox service runs here (a CLI, a start-up pass)."""
    from personalclaw.inbox import InboxStore

    store = InboxStore()
    store.load()
    return store


def _chat_test(state: Any, gone: frozenset[str]) -> Any:
    """Whether a session key is the chat's whose key *gone* spells: one of its spellings, or a
    helper that worked for it, however deep (``subagent_reach.chat_of``)."""
    from personalclaw import session_keys
    from personalclaw.subagent_reach import chat_of

    def of_the_chat(session: str) -> bool:
        if session in gone:
            return True
        return session_keys.SUBAGENT.names(session) and chat_of(state, session) in gone

    return of_the_chat


def _of_the_chat(item: Any, of_the_chat: Any) -> bool:
    """Whether the Inbox row *item* is the chat's (*of_the_chat*, :func:`_chat_test`). Not a
    proposal learned from it: its own store withdraws it, keeping the row as a withdrawn record
    without its words (``chat_forget``'s proposal step, each store's ``withdraw_sessions``)."""
    refs = item.refs if isinstance(item.refs, dict) else {}
    session = str(refs.get("session") or "")
    if not session or any(refs.get(key) for key in _WITHDRAWN_BY_THEIR_STORE):
        return False
    return bool(of_the_chat(session))


def _is_a_decision(item: Any) -> bool:
    """Whether *item* records a decision asked of you: an approval, or a call denied because
    nobody answered or nobody was there to ask (``auto_denials``)."""
    refs = item.refs if isinstance(item.refs, dict) else {}
    return bool(refs.get("approval") or refs.get("auto_denied"))


def _as_a_record(item: Any) -> None:
    """Rewrite the decision *item* as the record a gone chat leaves: its title, when it was asked
    and how it ended, and nothing else of the chat (what for, with what, who asked, where)."""
    from personalclaw.dashboard.approval_state import settled_row_text
    from personalclaw.inbox import OPEN_STATUSES

    refs = item.refs if isinstance(item.refs, dict) else {}
    message = str(item.message or "")
    if refs.get("approval") and item.status in OPEN_STATUSES:
        # One still asking ends with its chat, as an approval cancelled does.
        message = settled_row_text(message, "cancelled")
    title = message.partition("\n\n")[0].strip()
    how = title.partition(":")[0].strip() or "Asked"
    # The tool by its own name alone: its display title can be the call itself.
    tool = _tool_name(title.partition(":")[2].strip()) or _tool_name(str(refs.get("tool") or ""))
    said = f"Asked in a chat that is gone, {_when(item)}. {_ended(title, refs)}"
    item.message = f"{how}: {tool or 'a tool call'}\n\n{said}"
    kept = {key: str(refs[key]) for key in _KEPT_REFS if refs.get(key)}
    if "tool" in kept and not _tool_name(kept["tool"]):
        del kept["tool"]
    item.refs = kept | {CHAT_DELETED: "1"}
    item.thread_context = []
    item.draft = ""
    item.context_summary = ""


def _tool_name(text: str) -> str:
    """*text* when it is a tool's own name (:data:`_TOOL_NAME`), else ``""``."""
    return text if _TOOL_NAME.fullmatch(text) else ""


def _ended(title: str, refs: dict[str, Any]) -> str:
    """How the decision ended, in a sentence."""
    from personalclaw import auto_denials

    denied = str(refs.get("auto_denied") or "")
    if denied == auto_denials.EXPIRED:
        return "Nobody answered, so it was denied and did not run."
    if denied == auto_denials.UNATTENDED:
        return "It ran with nobody there to ask, so it was denied and did not run."
    word = title.partition(":")[0].strip()
    return _ENDED_AS.get(word, "It was cancelled.")


def _when(item: Any) -> str:
    """When the decision was asked, as the record says it."""
    stamp = float(getattr(item, "created_at", 0.0) or 0.0)
    if stamp <= 0:
        return "at a time not recorded"
    at = time.localtime(stamp)
    return f"on {at.tm_mday} {time.strftime('%b %Y at %H:%M', at)}"


def _announce(state: Any, kept: list[Any], removed: list[Any]) -> None:
    """Tell every open surface: each record as it reads now, and each row that went, by its id."""
    from personalclaw.inbox import redact_item

    try:
        for item in kept:
            state.broadcast_ws("inbox_item_updated", redact_item(item.to_dict()))
        for item in removed:
            state.broadcast_ws(
                "inbox_item_updated", {"id": item.id, "source": item.source, "removed": True}
            )
    except Exception:  # noqa: BLE001 - the rows moved; a page reads them again on its next load
        logger.debug("forget: could not announce the Inbox rows that moved", exc_info=True)
