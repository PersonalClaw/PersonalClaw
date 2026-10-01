"""Whether a session may leave anything in long-term memory: the one check every write passes.

An Incognito or Temporary session writes nothing to long-term memory (working, episodic or
semantic records, persona facts, commitments, lessons, knowledge or vocabulary) by any path: when
it idles out, closes, or is consolidated on request; through the agent's memory and knowledge
tools; through learning, reflection or summarising. Nothing from it is embedded either, so none of
it is indexed or recalled into another session.

That promise is kept at the stores, not by each caller remembering to ask:

* :func:`blocks_memory_writes` is the answer, keyed on the session's mode. It reads every record
  of that mode (what the caller holds, the in-process registry a channel marks, the mode the
  transcript records) and fails closed: a session keeps memory only when its mode is known to be
  ``persistent``. A mode it cannot read, or one it does not know, keeps nothing.
* :func:`derived_from` names the session the current work derives from. The name travels with
  the work: into every task the work spawns, into ``asyncio.to_thread``, and on the gateway into
  every worker thread it hands work to (:func:`carry_scope_into_worker_threads`).
* The stores refuse inside such a scope. The memory, knowledge and vocabulary databases refuse
  every statement that would change them (:func:`check_statement`, run by their connection), the
  markdown memory files are not written (:func:`refuse_write`), and the embedding functions return
  no vector without calling the model (:func:`writes_refused`).

Nothing of such a session is handed to a background model either: its title, tags and suggested
follow-ups, a condensed copy of its history, the suggestions built from recent chats. Each of those
chores asks :func:`blocks_background_models`, the same answer, before it reads the session to a
model.

Work that derives from no session (the owner's own edit in Memory Studio, an import, the
maintenance passes) runs outside any scope and is not affected.
"""

from __future__ import annotations

import contextvars
import functools
import logging
import re
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    import asyncio

    from personalclaw.memory import MemoryStore
    from personalclaw.vector_memory import VectorMemoryStore

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: The one mode that keeps memory. Every other value, known or not, keeps nothing.
PERSISTENT = "persistent"

#: The modes a session is created in that keep nothing.
RESTRICTED_MODES = frozenset({"incognito", "temporary"})

#: What a transcript's mode reads as when the transcript exists and its metadata cannot be read.
#: Not a mode, so it keeps nothing.
UNREADABLE = "unreadable"

#: The refusal, in the words the API has always answered a restricted session's write with.
REFUSAL = "Memory writes are not allowed in this session mode."


class MemoryWriteRefused(Exception):
    """A write to long-term memory was refused because the work derives from a session that keeps
    nothing (see the module docstring). Raised by the stores, answered 403 on the API."""

    def __init__(self, what: str = "") -> None:
        super().__init__(f"{REFUSAL} ({what})" if what else REFUSAL)
        self.what = what


@dataclass(frozen=True)
class _Source:
    """One session the current work derives from: the spellings of its key and the mode the code
    that named it holds (``None`` when it holds none)."""

    keys: tuple[str, ...]
    mode: str | None


_SCOPE: contextvars.ContextVar[_Source | None] = contextvars.ContextVar(
    "personalclaw_memory_write_scope", default=None
)


def blocks_memory_writes(session_key: str, *, memory_mode: str | None = None) -> bool:
    """Whether the session ``session_key`` must leave nothing in long-term memory.

    True when any record of its mode says so: the ``memory_mode`` the caller holds, the
    in-process registry a channel marks (:mod:`personalclaw.session_restrictions`), or the mode
    its transcript records. A session keeps memory only when its mode is known to be
    ``persistent``: an unknown value, or a transcript whose metadata cannot be read, keeps nothing.

    A key with no record anywhere (no transcript, no mark) is not a restricted session: there is
    nothing that says so, and a transcript that records no mode was written before modes existed
    or by a channel that has none. Work naming no key at all, with no mode, is refused.
    """
    if memory_mode is not None and memory_mode != PERSISTENT:
        return True
    key = (session_key or "").strip()
    if not key:
        return memory_mode is None
    from personalclaw import session_restrictions

    if session_restrictions.is_restricted(key):
        return True
    from personalclaw.history import read_memory_mode, session_path

    recorded = read_memory_mode(session_path(key))
    return recorded is not None and recorded != PERSISTENT


def blocks_background_models(
    session_key: str, *aliases: str, memory_mode: str | None = None
) -> bool:
    """Whether nothing of the session ``session_key`` may be handed to a background model.

    A background model is one the session's own turn did not ask for: the model that titles a
    chat, proposes its tags, follow-ups or folder, condenses its history, or builds suggestions
    from recent chats. An Incognito or Temporary chat's turns go to the model it runs on, and none
    of those chores reads it to a model: each does without (a title its mode names, the history
    cut to fit, no follow-ups) or leaves the chat out.

    The answer :func:`blocks_memory_writes` gives, for the key and for each of ``aliases`` (other
    spellings of the same session's key), so it reads the same records and fails closed the same
    way. Work that derives from such a session (:func:`writes_refused`) hands nothing on either,
    whichever session it names.
    """
    if writes_refused():
        return True
    keys = [key for key in (session_key, *aliases) if key and key.strip()] or [""]
    return any(blocks_memory_writes(key, memory_mode=memory_mode) for key in keys)


@contextmanager
def derived_from(session_key: str, *aliases: str, memory_mode: str | None = None) -> Iterator[None]:
    """Run the enclosed work as deriving from ``session_key``.

    ``aliases`` are other spellings of the same session's key (a chat's name and its transcript's
    key); the session is restricted when any of them is. ``memory_mode`` is the mode the caller
    holds for it, if any.

    Scopes nest, and the innermost names the session the work is for: a pass that consolidates
    one session is that session's work wherever it was started from, and a long-lived loop that a
    restricted chat's turn happened to start still does every other session's work as theirs.
    Work a restricted session hands on runs under a key that carries its mode (a subagent's key
    is marked when it is spawned, a workflow run's sessions inherit their origin's), so it keeps
    nothing either.
    """
    keys = tuple(k for k in ((session_key or "").strip(), *(a.strip() for a in aliases if a)) if k)
    token = _SCOPE.set(_Source(keys or ("",), memory_mode))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def _source_blocks(source: _Source) -> bool:
    return any(blocks_memory_writes(key, memory_mode=source.mode) for key in source.keys)


def writes_refused() -> bool:
    """Whether the current work derives from a session that keeps nothing."""
    source = _SCOPE.get()
    return source is not None and _source_blocks(source)


def refuse_write(what: str) -> None:
    """Raise :class:`MemoryWriteRefused` when the current work may not write ``what``."""
    if writes_refused():
        raise MemoryWriteRefused(what)


_Turn = TypeVar("_Turn", bound=Callable[..., Awaitable[None]])


def runs_as_its_session(turn: _Turn) -> _Turn:
    """Run each call of a turn engine ``turn(state, session, message, ...)`` as deriving from
    ``session``, under the mode the session holds: the turn and every task and worker thread it
    starts. The chat is named by its transcript's key first (a channel thread's own key when that
    is the one with a transcript, else the dashboard's) and by the key it was given."""

    @functools.wraps(turn)
    async def _as_its_session(state: Any, session: Any, message: str, *args: Any, **kw: Any):
        from personalclaw.constants import dashboard_history_key
        from personalclaw.history import session_path

        mode = getattr(session, "memory_mode", None)
        key = str(getattr(session, "key", "") or "")
        transcript = key if key and session_path(key).exists() else dashboard_history_key(key)
        with derived_from(transcript, key, memory_mode=mode if isinstance(mode, str) else None):
            await turn(state, session, message, *args, **kw)

    return _as_its_session  # type: ignore[return-value]


def hand_on(child_key: str, parent_key: str) -> None:
    """Mark ``child_key`` (a subagent working for ``parent_key``) as keeping nothing when the work
    starting it, or its parent, keeps nothing, so its agent's calls back over the API are refused
    writes as its parent's are."""
    if writes_refused() or (parent_key and blocks_memory_writes(parent_key)):
        from personalclaw import session_restrictions

        session_restrictions.mark_incognito(child_key)


def source_session() -> str:
    """The session the current work derives from, or ``""`` outside any.

    Stamped on every record the memory store writes, so what a session left in memory can always
    be found by the session it came from.
    """
    source = _SCOPE.get()
    return source.keys[0] if source is not None else ""


# ── what an earlier version kept ────────────────────────────────────────────────────────────


def transcript_keeps_nothing(session_key: str) -> bool:
    """Whether ``session_key``'s transcript records it as Incognito or Temporary.

    The record a cleanup acts on. Unlike :func:`blocks_memory_writes` it does not read a missing
    or unreadable mode as "keeps nothing": refusing a write is the safe way to be unsure, and
    removing a record is not.
    """
    from personalclaw.history import read_memory_mode, session_path

    return read_memory_mode(session_path(session_key)) in RESTRICTED_MODES


def forget_what_restricted_sessions_left(
    store: "VectorMemoryStore", markdown: "MemoryStore | None" = None
) -> int:
    """Remove every record an Incognito or Temporary session left in ``store``, and the daily
    history entries in ``markdown`` that repeat one of them word for word. Returns how many went.

    An earlier version consolidated such a session like any other when it idled out. Run when
    the gateway starts, before anything recalls; idempotent. A record that names no session (a
    persona note or a lesson an earlier version wrote) cannot be traced to one and is left.
    """
    try:
        texts = store.purge_records_from(transcript_keeps_nothing)
        removed = len(texts)
        if markdown is not None and texts:
            removed += markdown.forget_history_entries(set(texts))
    except Exception:  # noqa: BLE001 - a failed sweep must not stop the gateway; it runs again
        logger.warning(
            "Could not remove what Incognito or Temporary chats left in memory", exc_info=True
        )
        return 0
    if removed:
        logger.warning(
            "Removed %d memory record(s) an Incognito or Temporary chat had left in memory",
            removed,
        )
    return removed


# ── the databases' statement check ──────────────────────────────────────────────────────────

# The first word of a statement, past any whitespace and comments.
_LEAD = re.compile(r"(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*([A-Za-z]+)", re.S)
# The words a statement that changes the database carries. Searched for only in a ``WITH``
# statement, whose first word does not say whether it reads or writes.
_CHANGES = re.compile(r"\b(?:INSERT|UPDATE|DELETE|REPLACE|UPSERT|CREATE|DROP|ALTER)\b", re.I)
# Statements that read, or close a transaction. Everything else is taken for a change, including
# BEGIN and SAVEPOINT: refused work opens no transaction it would leave open on a shared connection.
_READS = frozenset(
    {"SELECT", "VALUES", "EXPLAIN", "PRAGMA", "COMMIT", "END", "ROLLBACK", "RELEASE"}
)


def reads_only(sql: str) -> bool:
    """Whether one SQL statement only reads (or ends a transaction). Unrecognised means no."""
    match = _LEAD.match(sql)
    if match is None:
        return False
    lead = match.group(1).upper()
    if lead in _READS:
        return True
    return lead == "WITH" and _CHANGES.search(sql) is None


def check_statement(sql: str, *, script: bool = False) -> None:
    """The memory, knowledge and vocabulary databases' check, run before every statement.

    Outside any scope it costs one context lookup. Inside one, a statement that reads passes and
    any other is refused when the scope's session keeps nothing. A ``script`` (several statements
    at once) is never taken for a read.
    """
    if _SCOPE.get() is None:
        return
    if not script and reads_only(sql):
        return
    refuse_write("a change to the database")


# ── the scope follows work into worker threads ──────────────────────────────────────────────


class ScopeCarryingExecutor(ThreadPoolExecutor):
    """A worker pool that runs each piece of work in the context of the code that handed it over.

    ``loop.run_in_executor`` does not carry context variables into the worker thread (and
    ``asyncio.to_thread`` does). Installed as the gateway's default executor, this makes the two
    agree, so a store a handler reaches from a worker thread knows which session the work derives
    from, as it does on the loop.
    """

    def submit(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> Future[_T]:
        context = contextvars.copy_context()
        return super().submit(context.run, fn, *args, **kwargs)


def carry_scope_into_worker_threads(loop: "asyncio.AbstractEventLoop") -> None:
    """Make ``loop``'s default executor carry the caller's context (see the class above)."""
    loop.set_default_executor(ScopeCarryingExecutor(thread_name_prefix="asyncio"))
