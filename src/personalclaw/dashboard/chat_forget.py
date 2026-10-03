"""Forgetting a chat: the Delete button, and a Temporary chat's end.

:func:`purge_chat` deletes what a chat keeps on disk (``personalclaw.chat_traces`` says what that
is): for the Delete button, which keeps a kept chat's attachments since Files › Uploads lists them
as files in their own right, and for the end of a Temporary chat, which takes its attachments too.

**A Temporary chat is forgotten when its session ends** — the product says so where the mode is
chosen, and it is a privacy promise, so it holds however the session ends. The session lives in
the gateway that is running it, so it ends when that gateway stops or restarts, when the chat is
deleted, and when cleanup evicts it as inactive:

* the last save before the gateway stops (``chat_persistence.save_all_sessions_to_history``)
  forgets each resident Temporary chat instead of saving it (:func:`forget_temporary_chat`);
* a gateway that ended without that save — a crash, a kill, a power cut — or a backup restored
  over the home leaves transcripts behind, and the next start forgets every Temporary transcript
  it finds before it restores anything (:func:`forget_ended_temporary_chats`);
* anything that finds a Temporary transcript whose chat is not running here finds a session that
  has ended, forgets it, and answers that it does not exist (:func:`forget_if_ended`) — so its
  page never opens again and a send to it cannot bring it back.

What the owner kept elsewhere is hers and stays: a download, a file saved to Knowledge, an
artifact. While the session runs, the transcript is written as any chat's is, so a reload keeps
it; it stays out of the chat list and search, nothing learns from it, and no snapshot or shard
export copies it (``chat_traces.kept_by_temporary_chats``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import TYPE_CHECKING

from personalclaw.chat_traces import (
    TEMPORARY,
    attached_files,
    attachment_roots,
    is_temporary,
    name_of,
)

if TYPE_CHECKING:  # pragma: no cover — typing only
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)


def temporary_attachments(
    state: DashboardState, history_key: str, session: _ChatSession | None
) -> list[str]:
    """The attached files of the Temporary chat persisted under *history_key* (resident as
    *session*, or only on disk), from its live buffer and its transcript. Empty for any other
    chat — a kept chat's uploads are files in their own right."""
    messages: list[dict] = list(session.messages) if session is not None else []
    temporary = session is not None and session.memory_mode == TEMPORARY
    try:
        if state.conversation_log:
            temporary = temporary or is_temporary(state.conversation_log.get_metadata(history_key))
            if temporary:
                messages += state.conversation_log.read_messages(history_key)
    except Exception:
        logger.debug("forget: could not read %s", history_key, exc_info=True)
    return attached_files(messages) if temporary else []


def purge_chat(
    state: DashboardState,
    history_key: str,
    *,
    keys: Iterable[str],
    attachments: Iterable[str] = (),
) -> int:
    """Delete what the chat persisted under *history_key* keeps on disk, and the links it was
    given (held in memory for web_fetch). Returns how many attached files were deleted.

    ``keys`` are every form of the chat's key the per-session stores may have been written under
    (a turn's tool results and checkpoints are keyed by the canonical ``dashboard:`` key, some
    paths by the bare name), and ``attachments`` the attached files to delete with it — only those
    inside an attachment folder are touched. Best-effort, step by step: one store's failure never
    keeps the others' traces.
    """
    try:
        if state.conversation_log:
            state.conversation_log.delete_session(history_key)
    except Exception:
        logger.warning("forget: transcript removal failed for %s", history_key, exc_info=True)
    names = {k for k in keys if k}
    try:
        from personalclaw.tool_providers import result_store

        for sid in names:
            result_store.purge_session(sid)
    except Exception:
        logger.warning("forget: workspace purge failed for %s", history_key, exc_info=True)
    try:
        from personalclaw import turn_checkpoints

        for sid in names:
            turn_checkpoints.prune_session(sid)
    except Exception:
        logger.warning("forget: checkpoint purge failed for %s", history_key, exc_info=True)
    # Held in memory, not on disk, but kept by the chat all the same: the links the user gave it,
    # under the key its turns hand their runtime (a channel thread persists under another).
    from personalclaw.constants import dashboard_history_key
    from personalclaw.web.fetch import clear_session

    for sid in names:
        clear_session(sid)
        clear_session(dashboard_history_key(sid))
    roots = attachment_roots()
    deleted = 0
    for path in attachments:
        real = os.path.realpath(path)
        if not real.startswith(roots):
            continue
        try:
            os.unlink(real)
            deleted += 1
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("forget: could not delete an attached file of %s", history_key)
    return deleted


def _said(name: str, attachments: int, why: str) -> None:
    """One log line and one audit row per forgotten chat: that it happened, never what it held."""
    logger.info(
        "Forgot Temporary chat %s (%s): its transcript and %d attached file(s) are deleted",
        name,
        why,
        attachments,
    )
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="dashboard",
            operation="chat.temporary_forgotten",
            outcome="success",
            source="dashboard",
            resources=f"{name} attachments={attachments} ({why})",
        )
    except Exception:  # noqa: BLE001 — an audit failure must never keep a chat's traces
        logger.debug("forget: audit row failed for %s", name, exc_info=True)


def forget_temporary_chat(state: DashboardState, session: _ChatSession, *, why: str) -> None:
    """End a resident Temporary chat's session: drop it from memory, then delete what it kept.

    Taken out of the gateway's sessions first, so nothing that walks them later (the flush loop,
    a shutdown save) writes it back.
    """
    from personalclaw.dashboard.chat_utils import persisted_history_key

    name = session.key
    state._sessions.pop(name, None)
    state._ephemeral_keys.discard(f"dashboard:{name}")
    history_key = persisted_history_key(state.conversation_log, name)
    files = temporary_attachments(state, history_key, session)
    deleted = purge_chat(state, history_key, keys={history_key, name}, attachments=files)
    _said(name, deleted, why)


def _forget_transcript(state: DashboardState, key: str, *, why: str) -> None:
    """Forget the Temporary transcript at *key* whose chat is not running here."""
    from personalclaw.dashboard.chat_utils import _history_key_for

    name = name_of(key)
    try:
        messages = state.conversation_log.read_messages(key) if state.conversation_log else []
    except Exception:
        messages = []
    deleted = purge_chat(
        state,
        key,
        keys={key, _history_key_for(name), name},
        attachments=attached_files(messages),
    )
    _said(name, deleted, why)


def forget_ended_temporary_chats(state: DashboardState) -> int:
    """Forget every Temporary transcript on disk whose chat is not running here. Returns how many.

    Run when the gateway starts, before its chats are restored: every session a previous gateway
    ran has ended, so a Temporary transcript still on disk is one whose end the shutdown never
    reached — a crash, a kill, a restored backup.
    """
    log = state.conversation_log
    if log is None:
        return 0
    try:
        listed = log.list_sessions_with_metadata()
    except Exception:
        logger.warning("forget: could not list the transcripts to forget", exc_info=True)
        return 0
    forgotten = 0
    for entry, meta in listed:
        key = str(entry.get("key") or "")
        if not key or not is_temporary(dict(meta)):
            continue
        if name_of(key) in state._sessions:
            continue
        _forget_transcript(state, key, why="its session ended with the last gateway")
        forgotten += 1
    return forgotten


def forget_if_ended(state: DashboardState, name: str) -> bool:
    """Whether *name* is a Temporary chat whose session has ended — forgotten now if so.

    A Temporary chat that is not running in this gateway has ended, so a transcript of one that is
    still on disk (a stop that could not finish its last save) is forgotten here, and the caller
    answers as it does for a chat that does not exist. Fails open — "not one" — when the
    transcript's first line cannot be read, like every other existence check on a chat.
    """
    if name in state._sessions or state.conversation_log is None:
        return False
    from personalclaw.dashboard.chat_utils import candidate_history_keys

    for key in candidate_history_keys(name):
        try:
            meta = state.conversation_log.get_metadata(key)
        except Exception:  # noqa: BLE001
            return False
        if is_temporary(meta):
            _forget_transcript(state, key, why="it was opened after its session ended")
            return True
    return False
