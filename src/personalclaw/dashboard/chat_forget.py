"""Forgetting a chat: deleting one, and a Temporary chat's end.

:func:`delete_chats` is the one way a chat is deleted: the Delete button
(``DELETE /api/chat/sessions/{session}``) and the history routes (``DELETE /api/sessions/{key}``,
``DELETE /api/sessions``) all go through it. It stops the chat's turn, deletes what the chat keeps
on disk (:func:`purge_chat`), then what memory drew from the chat alone
(:func:`forget_what_memory_drew_from`), and lets its runtime go.

**What memory drew from a chat alone** is what memory filed under it
(``VectorMemoryStore.purge_records_from``): its episodes and its sealed summary, its running
summary, and every fact, lesson, persona note, check-in or tool-outcome record its own work wrote
(its consolidation, its turns' memory tools, its after-turn review, the subagents and runs working
for it) that no other work has written or confirmed since. With them go the daily-history entries
that repeat their words, the days' digests that quoted their episodes (built again from the rest),
and their pages in a memory's vault. **What stays** cannot be traced to the chat alone: a record
another chat also gave memory, or that you wrote or confirmed yourself (a lesson you taught again
elsewhere, a fact you edited in Memory), what PersonalClaw's own passes drew from many chats
together, and what you kept elsewhere (a download, a file saved to Knowledge, an artifact, a
skill). The Delete dialog says so.

:func:`purge_chat` deletes what a chat keeps on disk (``personalclaw.chat_traces`` says what that
is): for a deleted chat, which keeps a kept chat's attachments since Files › Uploads lists them
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

The workflow runs a Temporary chat started are its own work and end with it: the workflow
supervisor stops each once the chat has ended and then deletes it with what it produced
(``workflows.private_runs``), however the session ended. So do an Incognito chat's once it is
deleted.

What the owner kept elsewhere is hers and stays: a download, a file saved to Knowledge, an
artifact. While the session runs, the transcript is written as any chat's is, so a reload keeps
it; it stays out of the chat list and search, nothing learns from it, and no snapshot or shard
export copies it (``chat_traces.kept_by_temporary_chats``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
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
    from personalclaw.history import ConversationLog
    from personalclaw.memory import MemoryStore

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


@dataclass(frozen=True)
class Purge:
    """What :func:`purge_chat` did."""

    #: How many attached files it deleted.
    attachments: int = 0
    #: Whether the chat's transcript is still on disk, its removal having failed.
    transcript_kept: bool = False


def purge_chat(
    state: DashboardState,
    history_key: str,
    *,
    keys: Iterable[str],
    attachments: Iterable[str] = (),
) -> Purge:
    """Delete what the chat persisted under *history_key* keeps on disk, what the learning log
    kept of its turns, the skills it was taught and not yet kept, and the links it was given (held
    in memory for web_fetch). Says how many attached files were deleted, and whether the
    transcript stayed.

    ``keys`` are every form of the chat's key the per-session stores may have been written under
    (a turn's tool results and checkpoints are keyed by the canonical ``dashboard:`` key, some
    paths by the bare name), and ``attachments`` the attached files to delete with it — only those
    inside an attachment folder are touched. Best-effort, step by step: one store's failure never
    keeps the others' traces.
    """
    kept = False
    if state.conversation_log:
        try:
            # True when it removed the file; False when no file was there to remove.
            removed = state.conversation_log.delete_session(history_key)
        except Exception:
            logger.warning("forget: transcript removal failed for %s", history_key, exc_info=True)
            removed = False
        kept = not removed and _transcript_left(state.conversation_log, history_key)
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
    # What the learning log kept of its turns: each one that surfaced a skill, with its message.
    try:
        from personalclaw.learning.surfacing_events import SurfacingEventStore

        events = SurfacingEventStore()
        try:
            events.forget_sessions(_spellings(names | {history_key}).__contains__)
        finally:
            events.close()
    except Exception:
        logger.warning("forget: learning log purge failed for %s", history_key, exc_info=True)
    from personalclaw.constants import dashboard_history_key

    # The skills it was taught and not yet kept, under the key its tools saw: a chat named again
    # after this one would otherwise be handed them as its own (`skills.ephemeral.context_block`).
    try:
        from personalclaw.skills import ephemeral

        for sid in names | {dashboard_history_key(sid) for sid in names}:
            ephemeral.clear_session(sid)
    except Exception:
        logger.warning("forget: skill draft removal failed for %s", history_key, exc_info=True)
    # Held in memory, not on disk, but kept by the chat all the same: the links the user gave it,
    # under the key its turns hand their runtime (a channel thread persists under another).
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
    return Purge(attachments=deleted, transcript_kept=kept)


def _transcript_left(log: ConversationLog, key: str) -> bool:
    """Whether a transcript is still kept under *key*; one that cannot be checked is."""
    try:
        return bool(log.has_log(key))
    except Exception:  # noqa: BLE001
        return True


@dataclass(frozen=True)
class Deletion:
    """What :func:`delete_chats` did: the chats it deleted, and those it found but could not
    delete, their transcript still on disk."""

    deleted: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


async def delete_chats(state: DashboardState, chats: Iterable[str], *, by: str) -> Deletion:
    """Delete each chat *chats* names, by its name or by the key its transcript is kept under,
    and what memory drew from it alone. One neither running here nor kept on disk is not found,
    and is neither deleted nor failed; one whose transcript could not be removed failed, and the
    rest of what it kept goes all the same.

    For each chat, in order: it leaves the gateway's chats and its turn is stopped; what it keeps
    on disk goes (:func:`purge_chat`), its transcript first, so a pass of memory still answering
    for it keeps nothing it would write after (``HistoryConsolidator._was_deleted``); then what
    memory filed under it goes from every memory (:func:`forget_what_memory_drew_from`), once for
    all of them; and its runtime is let go for good. One audit row per chat, naming it and who
    deleted it (*by*), never what it held.
    """
    from personalclaw.cancellation import cancel_and_wait
    from personalclaw.dashboard.chat_utils import persisted_history_key
    from personalclaw.dashboard.handlers.sessions import _live_session_key

    log = state.conversation_log
    deleted: list[str] = []
    failed: list[str] = []
    keys: set[str] = set()
    runtimes: list[tuple[str, bool]] = []
    for chat in dict.fromkeys(c for c in chats if c):
        live = _live_session_key(state, chat)
        session = state._sessions.get(live) if live is not None else None
        name = session.key if session is not None else chat
        # The key the caller named, when a transcript is kept under it: a history listing names
        # each transcript by its own file, which may carry no metadata to resolve it by.
        try:
            named = log is not None and bool(log.has_log(chat))
        except Exception:  # noqa: BLE001 — the resolver answers instead
            named = False
        history_key = chat if named else persisted_history_key(log, name)
        try:
            on_disk = log is not None and (
                bool(log.get_metadata(history_key)) or log.has_log(history_key)
            )
        except Exception:  # noqa: BLE001 — unreadable reads as not kept, as every existence check
            on_disk = False
        if session is None and not on_disk:
            continue
        files = temporary_attachments(state, history_key, session)
        if live is not None:
            state._sessions.pop(live, None)
        if session is not None:
            state._ephemeral_keys.discard(f"dashboard:{session.key}")
        if session is not None and session.running and session.task is not None:
            # `wait_for` on a task it cancelled is no bound: at its timeout it cancels the task
            # again and waits for that, and a turn starting an agent's process may not leave.
            await cancel_and_wait([session.task], what=f"chat {name}", grace=2.0)
        spelled = {history_key, name, chat}
        kept = purge_chat(state, history_key, keys=spelled, attachments=files).transcript_kept
        keys |= spelled
        runtimes.append((history_key, kept))
        (failed if kept else deleted).append(name)
    forgotten = await forget_what_memory_drew_from(state, keys) if keys else 0
    for history_key, kept in runtimes:
        # Best-effort: the chat's records are gone already, and a runtime that will not stop
        # cannot bring them back. Destroyed, not removed: nothing may resume a deleted chat. One
        # whose transcript stayed is still a chat, and may be resumed from it.
        try:
            await (state.sessions.remove if kept else state.sessions.destroy)(history_key)
        except Exception:  # noqa: BLE001
            logger.debug("delete: runtime teardown failed for %s", history_key, exc_info=True)
    if deleted or failed:
        state.push_sessions_update()
        state.push_refresh("history")
    if deleted:
        logger.info(
            "Deleted %d chat(s), and %d memory record(s) and history entries drawn from them alone",
            len(deleted),
            forgotten,
        )
    if failed:
        logger.warning(
            "Could not delete %d chat(s): their transcripts are still on disk", len(failed)
        )
    outcomes = [(n, "success") for n in deleted] + [(n, "failure") for n in failed]
    try:
        from personalclaw.sel import sel

        for name, outcome in outcomes:
            sel().log_api_access(
                caller=by,
                operation="chat.deleted",
                outcome=outcome,
                source="dashboard",
                resources=name,
            )
    except Exception:  # noqa: BLE001 — an audit failure must never keep a chat's traces
        logger.debug("delete: audit row failed", exc_info=True)
    return Deletion(deleted=tuple(deleted), failed=tuple(failed))


async def forget_what_memory_drew_from(state: DashboardState, keys: Iterable[str]) -> int:
    """Remove from every memory this home holds what is filed under the chats *keys* name, in each
    spelling a record may carry (``memory_writes.forget_what_sessions_left``): the global memory
    every chat outside a folder of its own keeps, and each folder's that keeps records, through
    the stores this gateway reads them with, so no recall still finds one. Returns how many records
    and history entries went.

    The stores are found here, on the gateway's loop, where every chat opens them; the purge runs
    on a worker thread, since a digest built again may wait on the embedding model."""
    gone = _spellings(keys)
    if not gone:
        return 0
    memories = _every_memory(state)
    forgotten = await asyncio.to_thread(_forget_in, memories, gone)
    if forgotten:
        state.push_refresh("lessons")
    return forgotten


def _forget_in(memories: list[MemoryStore], gone: frozenset[str]) -> int:
    """Remove what is filed under the sessions *gone* from each of *memories*."""
    from personalclaw.memory_writes import forget_what_sessions_left

    forgotten = 0
    for memory in memories:
        store = memory.vector_store
        if store is None:
            continue
        try:
            removed = forget_what_sessions_left(store, memory, gone.__contains__)
        except Exception:  # noqa: BLE001 — one memory's failure must not keep the others' records
            logger.warning(
                "forget: could not remove what a deleted chat left in %s",
                store.db_path,
                exc_info=True,
            )
            continue
        if removed:
            _write_vault_again(memory)
        forgotten += removed
    return forgotten


def _write_vault_again(memory: MemoryStore) -> None:
    """Write *memory*'s vault again when one is kept (``memory.vault_mode``), so no page of what
    was forgotten stays in it: a sync removes the page of a record that is gone. Best-effort, as
    every sync is: the next one removes it all the same."""
    from personalclaw.memory_service import MemoryService
    from personalclaw.memory_vault import vault_for

    try:
        vault = vault_for(MemoryService.over_vector_store(memory.vector_store))
        if vault is not None:
            vault.sync()
    except Exception:  # noqa: BLE001
        logger.warning("forget: the memory vault could not be written again", exc_info=True)


def _spellings(keys: Iterable[str]) -> frozenset[str]:
    """Every spelling of the chats *keys* a memory record may be filed under: each key as given,
    and a dashboard chat's both as its bare name and in its ``dashboard:`` namespace."""
    from personalclaw.constants import DASHBOARD_SESSION_PREFIX, dashboard_history_key

    out: set[str] = set()
    for key in keys:
        if not key:
            continue
        out |= {key, dashboard_history_key(key)}
        out.add(dashboard_history_key(key).removeprefix(DASHBOARD_SESSION_PREFIX))
    return frozenset(k for k in out if k)


def _every_memory(state: DashboardState) -> list[MemoryStore]:
    """Every memory this home holds with a record store: the global memory the gateway's chats
    and its memory routes share (``handlers._shared._get_memory``), then each folder's that keeps
    records, as this gateway has it open (``memory_locality.open_partition``)."""
    from personalclaw import memory_locality
    from personalclaw.dashboard.handlers._shared import _get_memory
    from personalclaw.memory import INDEX_FILE

    main = _get_memory(state)
    memories: list[MemoryStore] = []
    if main.vector_store is not None:
        memories.append(main)
    for part in memory_locality.partitions():
        if part.is_global or not (part.path / INDEX_FILE).is_file():
            continue
        memory = memory_locality.open_partition(part)
        store = memory.vector_store
        if store is not None and all(store is not m.vector_store for m in memories):
            memories.append(memory)
    return memories


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
    purged = purge_chat(state, history_key, keys={history_key, name}, attachments=files)
    _said(name, purged.attachments, why)


def _forget_transcript(state: DashboardState, key: str, *, why: str) -> None:
    """Forget the Temporary transcript at *key* whose chat is not running here."""
    from personalclaw.dashboard.chat_utils import _history_key_for

    name = name_of(key)
    try:
        messages = state.conversation_log.read_messages(key) if state.conversation_log else []
    except Exception:
        messages = []
    purged = purge_chat(
        state,
        key,
        keys={key, _history_key_for(name), name},
        attachments=attached_files(messages),
    )
    _said(name, purged.attachments, why)


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
