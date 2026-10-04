"""Whether a session may leave anything in long-term memory: the one check every write passes.

An Incognito or Temporary session writes nothing to long-term memory (working, episodic or
semantic records, persona facts, commitments, lessons, knowledge or vocabulary) by any path: when
it idles out, closes, or is consolidated on request; through the agent's memory and knowledge
tools; through learning, reflection or summarising. Nothing from it is embedded either, so none of
it is indexed or recalled into another session.

That promise is kept at the stores, not by each caller remembering to ask:

* :func:`blocks_memory_writes` is the answer, keyed on the session's mode as :func:`session_mode`
  reads it. That is the one reader of a session's mode, which every other one asks too (what work
  may read, the mode a workflow run inherits, what a subagent is handed): it reads every record of
  the mode, the live chat first, then the in-process registry a channel, a run or a subagent's
  start marks, then the mode the transcript records, and for a workflow step its run's. It fails
  closed: a session keeps memory only when its mode is known to be ``persistent``. A mode it
  cannot read, or one it does not know, keeps nothing and reads nothing.
* :func:`derived_from` names the session the current work derives from. The name travels with
  the work: into every task the work spawns, into ``asyncio.to_thread``, and on the gateway into
  every worker thread it hands work to (:func:`carry_scope_into_worker_threads`).
* The stores refuse inside such a scope. The memory, knowledge and vocabulary databases refuse
  every statement that would change them (:func:`check_statement`, run by their connection) and
  the markdown memory files are not written (:func:`refuse_write`).

An app's work changes your memory only when the app holds the ``memory`` permission, the one grant
that lets it read your memory too (install consent: "Read and change your memory"): a conversation
the app started, an agent run it asked for, an agent its scheduled job started, every agent working
for any of them, and the app's own requests. The scope names the app whose work it is
(``derived_from(app=...)``, the app :func:`personalclaw.memory_reads.reach_of` finds for the work)
and why it may change nothing (:func:`app_change_refusal`), found when the work first asks.
The memory database refuses its changes (:func:`check_memory_statement`) and the markdown memory
files are not written (:func:`refuse_memory_write`), the knowledge library and the vocabulary being
your content rather than your memory. What such work writes with the grant names the app as its
source (:func:`written_by`), so it never reads as yours: not as a fact you set or a lesson you
taught, which outrank everything else written, nor as one of PersonalClaw's own passes.

Nothing of such a session is handed to a background model either: its title, tags and suggested
follow-ups, a condensed copy of its history, the suggestions built from recent chats. Each of those
chores asks :func:`blocks_background_models`, the same answer, before it reads the session to a
model, and the one way a chore reaches a model (``chores.run_chore``) refuses one made for such a
session, or in its work, anywhere but its own turn.

Nor to any model but the one its turn runs on. :func:`model_may_read` is the one answer to "may
this work hand what it carries to that model", asked by every seam that does: the embedding
functions (so such a session's memory is searched by keyword, its tools are ranked by their words,
and nothing of it is embedded), the guard every model built for anything but a person's own turn
passes (a tool's model, a subagent's, a knowledge node's), the image reader, the image and video
tools, and the models a turn falls back to. Inside work that derives from a restricted session it
allows only the model the session's turn named (:func:`answered_by`). A one-shot call such work
makes runs on that model, stamped as serving in the bound model's place
(``llm_helpers.one_shot_completion``); anything else is refused with
:class:`OtherModelRefused` before anything is sent. The scope follows the work into the worker
threads it hands work to only through :class:`ScopeCarryingExecutor`, so every worker pool is one,
and into the tool process an agent CLI runs only because that process asks the gateway what the
chat it serves is (``mcp_core._call_as_its_session``, answered by :func:`restricted_mode`).

The work such a turn starts away from its own context stays on that model too, handed on with the
mode. The turn's model is recorded for its session (:func:`answered_by`, read by :func:`model_of`):
a request its agent's tool makes runs as the chat on it (:func:`as_work_of`), a subagent is handed
it with its mark when it is spawned (:func:`hand_on`) and runs on it (:func:`spawn_model`) as its
own work wherever its start comes from (:func:`work_context`), and a workflow run records it with
the mode it inherits, so its steps run on it after a restart too. A start that cannot run on it says
so before anything is sent. A side question asked beside the chat reads its conversation, so it is
the chat's own work as a turn is (:func:`as_its_session`), answered on the model the chat's own
choice of model builds, and named as such.

What the person gives such a chat themselves in a form its model cannot read is the exception, and
the only one: a file they attach, read for its text, and a screen they share, described. The model
they set up for that form reads it, as in any chat (:func:`reading_their_input`); the chat's notice
says so. Their own voice never runs inside the chat's work at all: dictation and reading a reply
aloud are requests of their own.

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
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    import asyncio

    from personalclaw.memory import MemoryStore
    from personalclaw.memory_reads import Reach
    from personalclaw.vector_memory import VectorMemoryStore

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: The one mode that keeps memory. Every other value, known or not, keeps nothing.
PERSISTENT = "persistent"

#: The modes a session is created in that keep nothing.
RESTRICTED_MODES = frozenset({"incognito", "temporary"})

#: What a session's mode reads as when nothing can say what it is (:func:`session_mode`): its
#: records are there and cannot be read, or name a mode this build does not know, or there are
#: none for a chat that must have one. Also what a transcript's mode reads as when its metadata
#: cannot be read. Not a mode: work under it reads none of your memory and keeps nothing, as a
#: Temporary chat's work does, and what it is refused says that its chat's setting cannot be read.
UNREADABLE = "unreadable"

#: A dashboard chat's key (``dashboard:<name>``), and the key the dashboard's own pages send: the
#: owner, not a chat.
_DASHBOARD = "dashboard:"
_DASHBOARD_UI = "dashboard:ui"

#: What one record of a session's mode can say beside a mode: that it is there and cannot be read,
#: and, of a transcript, that it records no mode (one written before modes existed, or by a channel
#: that has none). Neither is said by :func:`session_mode`, which weighs them below every mode.
_GARBLED = "garbled"
_NO_MODE = "no mode"

#: What a record can say, the strictest first: the order :func:`session_mode` weighs them in.
_BY_STRICTNESS = ("temporary", UNREADABLE, "incognito", PERSISTENT, _GARBLED, _NO_MODE)

#: The refusal, in the words the API has always answered a restricted session's write with.
REFUSAL = "Memory writes are not allowed in this session mode."

#: The codes a refused write is recorded under (:attr:`MemoryWriteRefused.code`): work that derives
#: from a session that keeps nothing, and an app's work that may not change your memory.
RESTRICTED_SESSION_BLOCK = "restricted_session_block"
APP_MEMORY_NOT_GRANTED = "app_memory_not_granted"


class MemoryWriteRefused(Exception):
    """A write to long-term memory was refused because the work derives from a session that keeps
    nothing, or is an app's that may not change your memory (see the module docstring). Raised by
    the stores, answered 403 on the API with :attr:`reason`."""

    def __init__(self, what: str = "", *, reason: str = "") -> None:
        self.reason = reason or REFUSAL
        super().__init__(f"{self.reason} ({what})" if what else self.reason)
        self.what = what

    @property
    def code(self) -> str:
        """Why, as the security log and a refused tool call's audit row name it."""
        return RESTRICTED_SESSION_BLOCK if self.reason == REFUSAL else APP_MEMORY_NOT_GRANTED


class OtherModelRefused(RuntimeError):
    """A model other than the one an Incognito or Temporary session's turn runs on was asked to
    read that session's work (:func:`model_may_read`). Raised before anything is sent; its text is
    the sentence that says so."""

    def __init__(self, model_ref: str, *, unknown: bool = False) -> None:
        super().__init__(other_model_refusal(model_ref, unknown=unknown))
        self.model_ref = model_ref


@dataclass(frozen=True)
class _Source:
    """One session the current work derives from: the spellings of its key, the mode the code
    that named it holds (``None`` when it holds none), the model its turn runs on as
    ``"<entry>:<model>"`` once the turn has named it (``""`` until then), whether the work is
    reading what the person gave the chat (:func:`reading_their_input`), and the app whose work it
    is (:class:`_Whose`; ``None`` for yours)."""

    keys: tuple[str, ...]
    mode: str | None
    model: str = ""
    their_input: bool = False
    whose: "_Whose | None" = None


class _Whose:
    """Whose work a scope is: the app's (``""`` for yours), why that app's work may change none of
    your memory (``""`` when it may), and the session at the top of the chain the work is done for
    (``""`` when nothing says: the scope's own session then). Found when first asked and kept for
    the work's whole length, so work that writes nothing (most of a tool's calls) never looks.
    *find* answers ``(app, session the work is for)``."""

    __slots__ = ("_find", "_found")

    def __init__(self, find: Callable[[], tuple[str, str]]) -> None:
        self._find = find
        self._found: tuple[str, str, str] | None = None

    def get(self) -> tuple[str, str, str]:
        if self._found is None:
            app, works_for = self._find()
            app = (app or "").strip()
            self._found = (app, _app_may_not_change(app), (works_for or "").strip())
        return self._found


_SCOPE: contextvars.ContextVar[_Source | None] = contextvars.ContextVar(
    "personalclaw_memory_write_scope", default=None
)


def blocks_memory_writes(
    session_key: str, *, memory_mode: str | None = None, state: Any = None
) -> bool:
    """Whether the session ``session_key`` must leave nothing in long-term memory: its mode, as
    :func:`session_mode` reads it from every record of it (``memory_mode`` is the one the caller
    holds; ``state``, the gateway's dashboard state, whose live chats it reads), is not known to be
    ``persistent``. An unknown value, or a record that cannot be read, keeps nothing.

    Work that is no chat's (a key no record names, an automation's or an app's own) is not
    restricted. Work naming no key at all, with no mode, is refused.
    """
    mode = session_mode(session_key, state=state, held=memory_mode)
    if mode is None:
        return not (session_key or "").strip()
    return mode != PERSISTENT


def session_mode(session_key: str, *, state: Any = None, held: str | None = None) -> str | None:
    """The memory mode the work of the session ``session_key`` runs under: ``"persistent"``,
    ``"incognito"``, ``"temporary"``, or :data:`UNREADABLE` when nothing can say which. ``None`` for
    work that is no chat's (an automation's, an app's own, the owner's own pages): no record names
    a mode for it.

    The one reader of a session's mode: the writes (:func:`blocks_memory_writes`), the reads
    (``memory_reads.reach_of``), the mode a workflow run inherits (``ownership.inherit_mode``) and
    what a subagent is handed (:func:`hand_on`) all ask it. It reads every record of the mode, the
    live chat first:

    1. the live chat: the mode the caller holds of it (``held``), the mode the current work holds
       when it is that session's own work (the turn's, a request's: :func:`derived_from`,
       :func:`as_work_of`), and the dashboard chat of that name the gateway holds now (``state``,
       its dashboard state: :func:`live_mode`);
    2. the in-process registry a channel, a run or a subagent's start marks
       (:mod:`personalclaw.session_restrictions`);
    3. the mode its transcript records;
    4. for a step of a workflow run, the mode its run inherited when it started.

    A dashboard chat's marks and transcript are read under its bare name too, which is the key
    a chat a channel's thread started is kept under.

    Every one is read because each can be the only record there is: a chat's transcript is written
    when its first turn ends, the dashboard marks no registry, and after a restart only the
    transcripts and the runs' records are left. The strictest mode a record names wins, so no
    record opens what another closed. A record that is there and cannot be read, or names a mode
    this build does not know, reads :data:`UNREADABLE` unless another names a mode, and so does a
    step whose run has no record. Where the gateway's chats can be read (``state``), so does a
    dashboard chat it does not hold that nothing records: such a chat is live while it works, and
    its transcript records its mode from the end of its first turn, so one with neither is one
    whose mode nothing can say (a Temporary chat that has ended, a chat that never existed). A
    transcript that records no mode was written before modes existed, or by a channel that has
    none: ``persistent``, unless another record says otherwise.
    """
    key = (session_key or "").strip()
    found: set[str] = set()
    for record in _records_of(key, state, held):
        if record == "temporary":
            return record
        if record is not None:
            found.add(record)
    for said in _BY_STRICTNESS:
        if said in found:
            return {_GARBLED: UNREADABLE, _NO_MODE: PERSISTENT}.get(said, said)
    if _live_chats(state) is not None and key.startswith(_DASHBOARD) and key != _DASHBOARD_UI:
        return UNREADABLE
    return None


def _records_of(key: str, state: Any, held: str | None) -> Iterator[str | None]:
    """What each record of the session ``key``'s mode says, in the order :func:`session_mode` reads
    them: a mode, :data:`_GARBLED`, :data:`_NO_MODE`, or ``None`` when the record is not there."""
    yield None if held is None else _as_mode(held)
    source = _SCOPE.get()
    if source is not None and source.mode is not None and key in source.keys:
        yield _as_mode(source.mode)
    yield live_mode(state, key)
    if not key or key == _DASHBOARD_UI:
        return
    # A dashboard chat is also kept under its bare name: a chat a channel's thread started keeps
    # the thread's own key (``dashboard.chat_utils.persisted_history_key``).
    spellings = (key, key.removeprefix(_DASHBOARD)) if key.startswith(_DASHBOARD) else (key,)
    for spelling in spellings:
        yield _marked(spelling)
        yield _transcribed(spelling)
    yield _inherited(key)


def _as_mode(value: object) -> str:
    """A recorded value as what it says: one of the modes, or :data:`_GARBLED`."""
    return value if isinstance(value, str) and value in _BY_STRICTNESS[:4] else _GARBLED


def live_mode(state: Any, session_key: str) -> str | None:
    """The mode of the live dashboard chat ``session_key`` names (``dashboard:<name>``, or its
    bare name): the chat of that name ``state``, the gateway's dashboard state, holds now. ``None``
    when it holds none, or there is no state to ask."""
    sessions = _live_chats(state)
    key = (session_key or "").strip()
    if sessions is None or not (key.startswith(_DASHBOARD) or ":" not in key):
        return None
    chat = sessions.get(key.removeprefix(_DASHBOARD))
    if chat is None:
        return None
    mode = getattr(chat, "memory_mode", None)
    return _as_mode(mode) if isinstance(mode, str) else _GARBLED


def _live_chats(state: Any) -> dict[str, Any] | None:
    """The live dashboard chats ``state`` holds, by name; ``None`` when it holds none to read."""
    sessions = getattr(state, "_sessions", None)
    return sessions if isinstance(sessions, dict) else None


def _marked(key: str) -> str | None:
    """The mode the in-process registry marks ``key`` with (:mod:`session_restrictions`)."""
    from personalclaw import session_restrictions

    try:
        if session_restrictions.is_temporary(key):
            return "temporary"
        if session_restrictions.is_unreadable(key):
            return UNREADABLE
        if session_restrictions.is_incognito(key):
            return "incognito"
    except Exception:  # noqa: BLE001 - a registry that cannot be read says nothing
        return _GARBLED
    return None


def _transcribed(key: str) -> str | None:
    """The mode ``key``'s transcript records (``history.read_memory_mode``): ``None`` when there is
    no transcript, :data:`_NO_MODE` when it records none, :data:`_GARBLED` when its metadata
    cannot be read (which that reader calls :data:`UNREADABLE`)."""
    from personalclaw.history import read_memory_mode, session_path

    try:
        path = session_path(key)
        recorded = read_memory_mode(path)
        if recorded is None:
            return _NO_MODE if path.exists() else None
    except Exception:  # noqa: BLE001 - a transcript that cannot be read says nothing either
        return _GARBLED
    return _GARBLED if recorded == UNREADABLE else _as_mode(recorded)


def _inherited(key: str) -> str | None:
    """The mode the run whose step ``key`` names inherited when it started (``None`` for a key that
    names no step). A step whose run has no record, or one that cannot be read, is
    :data:`_GARBLED`: a step always has a run."""
    run = run_of_step(key)
    if run is NOT_A_STEP:
        return None
    if run is None or run is RUN_UNREADABLE:
        return _GARBLED
    from personalclaw.workflows import ownership

    mode = ownership.run_mode(run)
    return PERSISTENT if mode is ownership.MemoryMode.NORMAL else mode.value


#: What :func:`run_of_step` answers for a key that names no step of a workflow run, and for one
#: whose run's record cannot be read.
NOT_A_STEP = object()
RUN_UNREADABLE = object()


def run_of_step(session_key: str) -> Any:
    """The workflow run whose step ``session_key`` names (``workflows.ownership.owned_key``):
    the run, ``None`` when there is no such run, :data:`RUN_UNREADABLE` when its record cannot be
    read, :data:`NOT_A_STEP` for a key that names no step."""
    from personalclaw.workflows import ownership

    owned = ownership.parse_owned(session_key)
    if owned is None:
        return NOT_A_STEP
    try:
        from personalclaw.workflows import store

        return store.get(owned[0])
    except Exception:  # noqa: BLE001 - a run that cannot be read is not known to keep anything
        return RUN_UNREADABLE


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
def derived_from(
    session_key: str,
    *aliases: str,
    memory_mode: str | None = None,
    app: str = "",
) -> Iterator[None]:
    """Run the enclosed work as deriving from ``session_key``.

    ``aliases`` are other spellings of the same session's key (a chat's name and its transcript's
    key); the session is restricted when any of them is. ``memory_mode`` is the mode the caller
    holds for it, if any. ``app`` is the app whose work it is (``""`` for yours): whether it may
    change your memory is asked when the work first needs it, once, for the work's whole length.

    Scopes nest, and the innermost names the session the work is for: a pass that consolidates
    one session is that session's work wherever it was started from, and a long-lived loop that a
    restricted chat's turn happened to start still does every other session's work as theirs.
    Work a restricted session hands on runs under a key that carries its mode (a subagent's key
    is marked when it is spawned, a workflow run's sessions inherit their origin's), so it keeps
    nothing either.
    """
    keys = tuple(k for k in ((session_key or "").strip(), *(a.strip() for a in aliases if a)) if k)
    token = _SCOPE.set(_Source(keys or ("",), memory_mode, whose=_whose_of(app)))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def _whose_of(app: str = "", reach: "Callable[[], Reach] | None" = None) -> _Whose | None:
    """Whose work a scope is (:class:`_Whose`), from what its caller names: *reach*, the walk that
    finds the app and the chat at the top the work is done for (``memory_reads.reach_of``), else
    the app (``""`` for yours: ``None``)."""
    if reach is not None:
        found = reach

        def find() -> tuple[str, str]:
            whose = found()
            return whose.app, whose.keys[-1] if whose.keys else ""

        return _Whose(find)
    named = (app or "").strip()
    return _Whose(lambda: (named, "")) if named else None


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


# ── an app's work ───────────────────────────────────────────────────────────────────────────

#: The source a record an app's work writes names: ``app:<name>``.
APP_SOURCE_PREFIX = "app:"


def _app_may_not_change(app: str) -> str:
    """Why the app *app*'s work may change none of your memory, ``""`` when it may or there is no
    app (``memory_reads.app_refusal``, the one check of the app's grant)."""
    if not app:
        return ""
    from personalclaw.memory_reads import app_refusal

    return app_refusal(app, changing=True)


def _whose() -> tuple[str, str, str]:
    """The current work's app, why it may change none of your memory, and the session at the top it
    is done for (:class:`_Whose`)."""
    source = _SCOPE.get()
    if source is None or source.whose is None:
        return "", "", ""
    return source.whose.get()


def app_change_refusal() -> str:
    """Why the current work, an app's, may change none of your memory: the app does not hold the
    ``memory`` permission, or is not installed, or is off. ``""`` when it may, and for work that
    is no app's or derives from no session."""
    return _whose()[1]


def changes_no_memory() -> bool:
    """Whether the current work may change none of your memory: it derives from a session that
    keeps nothing (:func:`writes_refused`), or it is an app's not given your memory. Asked where a
    read would leave a mark on memory (a recall's count, an episode's last read), so such work
    reads without leaving one rather than being refused part-way through a read."""
    return writes_refused() or bool(app_change_refusal())


def refuse_memory_write(what: str) -> None:
    """Raise :class:`MemoryWriteRefused` when the current work may not write ``what`` to your
    memory: it derives from a session that keeps nothing (:func:`refuse_write`), or it is an app's
    that may change none of your memory, refused in the app's words."""
    refuse_write(what)
    refused = app_change_refusal()
    if refused:
        raise MemoryWriteRefused(what, reason=refused)


def memory_write_refusal(what: str = "") -> MemoryWriteRefused | None:
    """The refusal a write of ``what`` to your memory would get now (:func:`refuse_memory_write`),
    or ``None`` when it would be made. Asked by a check made before anything is written: a file
    tool's or the shell's, before anyone is asked to approve the call."""
    try:
        refuse_memory_write(what)
    except MemoryWriteRefused as refused:
        return refused
    return None


def written_by(source: str) -> str:
    """The source a record the current work writes names: ``app:<name>`` for an app's work,
    whatever *source* the code writing it gives (``user_explicit`` included), else *source*. Asked
    where the memory store decides a record's source, before anything reads it: so an app's write
    is weighed as an app's, never as yours."""
    app = _whose()[0]
    return f"{APP_SOURCE_PREFIX}{app}" if app else source


# ── the models a session's work reaches ─────────────────────────────────────────────────────


def answered_by(model_ref: str) -> None:
    """Name ``model_ref`` (``"<entry>:<model>"``, or an agent CLI's ``acp:<cli>``) as the model the
    current work's turn runs on.

    The turn engine says it once the turn's runtime is built and before anything is sent
    (``chat_runner.run_chat``). It holds for the rest of the scope it is said in. Work started
    before it was said does not see it, so that work may hand nothing on. In the work of a session
    that keeps nothing it is also recorded for the session (:func:`model_of`), so the work the turn
    starts away from its own context stays on it as well. Outside any scope it does nothing.
    """
    source = _SCOPE.get()
    if source is None:
        return
    ref = (model_ref or "").strip()
    _SCOPE.set(replace(source, model=ref))
    if _source_blocks(source):
        from personalclaw import session_restrictions

        for key in source.keys:
            if key:
                session_restrictions.mark_own_model(key, ref)


def model_of(session_key: str) -> str:
    """The one model the work of ``session_key`` may reach when the session keeps nothing: the
    model its turn named (:func:`answered_by`), the one it was handed when it was started
    (:func:`hand_on`), or, for a step of a workflow run, the one its run recorded when it started.
    ``""`` when nothing records one, so its work reaches none."""
    from personalclaw import session_restrictions

    key = (session_key or "").strip()
    recorded = session_restrictions.own_model(key) if key else ""
    if recorded:
        return recorded
    run = run_of_step(key)
    if run is NOT_A_STEP or run is None or run is RUN_UNREADABLE:
        return ""
    from personalclaw.workflows import ownership

    return ownership.run_model(run)


def _work_of(
    session_key: str, memory_mode: str | None, reach: "Callable[[], Reach] | None" = None
) -> _Source:
    key = (session_key or "").strip()
    return _Source((key,), memory_mode, model_of(key), whose=_whose_of(reach=reach))


@contextmanager
def as_work_of(
    session_key: str, *, memory_mode: str | None = None, reach: "Callable[[], Reach] | None" = None
) -> Iterator[None]:
    """Run the enclosed work as the work of ``session_key`` away from that session's own turn: a
    request its agent's tool makes (``dashboard/memory_write_gate``). *reach* finds whose work it
    is (``memory_reads.reach_of``), asked when the work first needs it: the app's, if an app's, and
    the chat at the top it is done for, which what it writes is filed under (:func:`filed_under`).
    When the session keeps nothing, it runs on the one model its work stays on (:func:`model_of`).
    """
    token = _SCOPE.set(_work_of(session_key, memory_mode, reach))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def work_context(session_key: str, *, memory_mode: str | None = None) -> contextvars.Context:
    """A copy of the current context in which a task runs as the work of ``session_key``
    (:func:`as_work_of`), whatever work starts it: a subagent's run, started by its spawn's
    request, by another agent's freed slot or after its owner's answer, and a workflow run, started
    by a chat's request or resumed after a restart."""
    context = contextvars.copy_context()
    context.run(_SCOPE.set, _work_of(session_key, memory_mode))
    return context


def handed_model(parent_key: str = "") -> str:
    """The one model work started now for ``parent_key`` may reach when it keeps nothing: the one
    the current work stays on when it keeps nothing (:func:`own_model`), and the one
    ``parent_key``'s work stays on (:func:`model_of`) when a session that keeps nothing is named.
    Work for two such sessions reaches a model only when both stay on it: ``""`` when they differ,
    or when neither names one."""
    models = {model_of(parent_key)} if parent_key else set()
    own = own_model()
    if own is not None:
        models.add(own)
    return models.pop() if len(models) == 1 else ""


def is_agent_cli(model_ref: str) -> bool:
    """Whether ``model_ref`` names an agent CLI's runtime (``acp:<cli>``), which an agent CLI's chat
    runs on, rather than a model: only an agent started on that CLI runs on it, never a call."""
    return (model_ref or "").startswith("acp:")


def spawn_model(named: str = "") -> tuple[str, str]:
    """What an agent started inside the current work runs on, as ``(model, runtime)``.

    ``named`` (the model its start names, if any) and no runtime of its own, for work that may use
    every model. Inside an Incognito or Temporary chat's work, the one model that work stays on
    (:func:`own_model`): an agent CLI's runtime as its runtime, any other as its model. A start
    naming another model is refused with :class:`OtherModelRefused` before anything is built, and
    so is one in work that was not told which model it stays on, saying so.
    """
    own = own_model()
    if own is None:
        return named, ""
    if not own:
        raise OtherModelRefused(named, unknown=True)
    if named and named != own:
        raise OtherModelRefused(named)
    return ("", own) if is_agent_cli(own) else (own, "")


def model_may_read(model_ref: str) -> bool:
    """Whether the current work may hand what it carries to the model ``model_ref``.

    THE answer every seam that hands work to a model asks (see the module docstring). Always,
    outside work that derives from an Incognito or Temporary session, and while such work reads
    what the person gave the chat (:func:`reading_their_input`). Otherwise inside such work only
    the model the session's turn runs on (:func:`answered_by`): not before the turn has named it,
    and never a model no ref names (an embedding function pinned on a store).
    """
    source = _SCOPE.get()
    if source is None or source.their_input or not _source_blocks(source):
        return True
    ref = (model_ref or "").strip()
    return bool(ref) and ref == source.model


def require_model(model_ref: str) -> None:
    """Raise :class:`OtherModelRefused` when the current work may not hand its text to
    ``model_ref`` (:func:`model_may_read`)."""
    if not model_may_read(model_ref):
        raise OtherModelRefused(model_ref)


def own_model() -> str | None:
    """The model work that derives from an Incognito or Temporary session stays on: the one its
    turn named (``""`` before it has). ``None`` for any other work, which may use every model."""
    source = _SCOPE.get()
    if source is None or source.their_input or not _source_blocks(source):
        return None
    return source.model


@contextmanager
def reading_their_input() -> Iterator[None]:
    """Run the enclosed work as reading what the person gave the chat themselves in a form its
    model cannot read: a file they attached, read for its text, or a screen they shared, described.

    The model they set up for that form reads it in an Incognito or Temporary chat as in any other
    (:func:`model_may_read`): they handed it to the chat themselves, the chat's own model cannot
    read it, and the chat's notice says so. Nothing else changes: the work still writes nothing for
    such a chat (:func:`writes_refused`) and hands nothing to a background model. Outside any scope
    it changes nothing. The readings that run under it are named in
    ``tests/test_model_reach_census.py``, which fails for any other.
    """
    source = _SCOPE.get()
    if source is None:
        yield
        return
    token = _SCOPE.set(replace(source, their_input=True))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def _restricted_label() -> str:
    """What the current work's session is, as its chat is called ("Incognito", "Temporary"): its
    mode as :func:`session_mode` reads it under each spelling of its key, the strictest. ``""`` when
    nothing can say which (:data:`UNREADABLE`)."""
    source = _SCOPE.get()
    if source is None:
        return ""
    modes = {session_mode(key, held=source.mode) for key in source.keys}
    for mode in ("temporary", UNREADABLE, "incognito"):
        if mode in modes:
            return "" if mode == UNREADABLE else mode.capitalize()
    return ""


def restricted_mode() -> str | None:
    """The mode of the session the current work derives from when that session keeps nothing:
    ``"incognito"``, ``"temporary"``, or :data:`UNREADABLE` when no record says which. ``None``
    for any other work. What the gateway tells a tool process outside it that asks about its own
    session (``GET /api/chat/sessions/model-reach``), so the tool runs under the same answer."""
    if not writes_refused():
        return None
    label = _restricted_label()
    return label.lower() if label else UNREADABLE


def own_model_reason() -> str:
    """Why the current work stays on its chat's model, as a clause: "this chat is Incognito, so
    nothing from it is sent to any model but the one it runs on", or, when nothing can say what the
    chat is, that its memory setting cannot be read."""
    label = _restricted_label()
    who = f"this chat is {label}" if label else "this chat's memory setting cannot be read"
    return f"{who}, so nothing from it is sent to any model but the one it runs on"


def other_model_refusal(model_ref: str, *, unknown: bool = False) -> str:
    """What a refused model call says: "This chat is Incognito, so nothing from it is sent to any
    model but the one it runs on: <model> was not asked." Work that was not told which model that
    is (*unknown*) says so instead of naming one."""
    reason = own_model_reason()
    if unknown:
        named = ", and this work was not told which model that is"
    else:
        named = f": {model_ref} was not asked" if model_ref else ""
    return f"{reason[:1].upper()}{reason[1:]}{named}."


_Turn = TypeVar("_Turn", bound=Callable[..., Awaitable[None]])


@contextmanager
def as_its_session(session: Any) -> Iterator[None]:
    """Run the enclosed work as the chat ``session``'s own: deriving from it, under the mode it
    holds and as the work of the app that started it, if one did, and so every task and worker
    thread the work starts. A turn of the chat runs so (:func:`runs_as_its_session`), and so does a
    side question asked beside it, which reads its conversation. The chat is named by its
    transcript's key first (a channel thread's own key when that is the one with a transcript,
    else the dashboard's) and by the key it was given. The work names the model it runs on
    (:func:`answered_by`) once that model is built."""
    from personalclaw.constants import dashboard_history_key
    from personalclaw.history import session_path

    mode = getattr(session, "memory_mode", None)
    key = str(getattr(session, "key", "") or "")
    app = getattr(session, "created_by_app", "")
    transcript = key if key and session_path(key).exists() else dashboard_history_key(key)
    with derived_from(
        transcript,
        key,
        memory_mode=mode if isinstance(mode, str) else None,
        app=app if isinstance(app, str) else "",
    ):
        yield


def runs_as_its_session(turn: _Turn) -> _Turn:
    """Run each call of a turn engine ``turn(state, session, message, ...)`` as the chat
    ``session``'s own work (:func:`as_its_session`): the turn and every task and worker thread it
    starts."""

    @functools.wraps(turn)
    async def _as_its_session(state: Any, session: Any, message: str, *args: Any, **kw: Any):
        with as_its_session(session):
            await turn(state, session, message, *args, **kw)

    return _as_its_session  # type: ignore[return-value]


def hand_on(
    child_key: str, parent_key: str, *, reach: Callable[[str], Reach] | None = None
) -> None:
    """Mark ``child_key`` (a subagent working for ``parent_key``) with the strictest of what the
    work starting it is (:func:`restricted_mode`) and what its parent is: the parent's mode as its
    records read (*reach*, ``memory_reads.reach_of`` over the gateway's state, so the live chat
    first; without it the registry, the transcripts and the runs), and whether anything it works
    for reads no memory. Work for a Temporary chat is Temporary: it reads no memory and writes
    none. Work for a chat whose mode nothing can say is marked so, and runs by the same rules,
    saying why. Work for any other session that keeps nothing is Incognito, so its agent's calls
    back over the API are refused writes as its parent's are. Either way it is handed the one model
    it may reach (:func:`handed_model`), its chat's own, which its runtime is built on and its own
    calls back over the API stay on."""
    from personalclaw import memory_reads, session_restrictions

    asked = reach or functools.partial(memory_reads.reach_of, None)
    parent = asked(parent_key) if parent_key else memory_reads.Reach()
    modes = {restricted_mode(), parent.mode}
    if parent.blank:
        modes.add("temporary" if parent.temporary else UNREADABLE)
    marks = {
        "temporary": session_restrictions.mark_temporary,
        UNREADABLE: session_restrictions.mark_unreadable,
        "incognito": session_restrictions.mark_incognito,
    }
    mode = next((m for m in marks if m in modes), None)
    if mode is None:
        return
    marks[mode](child_key)
    parent_keeps_nothing = bool(parent.blank) or parent.mode not in (None, PERSISTENT)
    session_restrictions.mark_own_model(
        child_key, handed_model(parent_key if parent_keeps_nothing else "")
    )


def source_session() -> str:
    """The session the current work derives from, or ``""`` outside any."""
    source = _SCOPE.get()
    return source.keys[0] if source is not None else ""


def filed_under() -> str:
    """The session a record the current work writes is filed under, ``""`` outside any work.

    Stamped on every record the memory store writes (its ``source_session``), so what a session
    left in memory can always be found by the session it came from. That is the chat at the top of
    the chain the work is done for, when the work names one: a request a subagent makes is the
    work it does for its chat, as a step's is for the chat that started its run, so what it keeps
    is filed under that chat (``memory_reads.reach_of``, asked once for the work's whole length).
    Otherwise it is the session the work derives from (:func:`source_session`).
    """
    source = _SCOPE.get()
    if source is None:
        return ""
    return _whose()[2] or source.keys[0]


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


def check_memory_statement(sql: str, *, script: bool = False) -> None:
    """The memory database's check, run before every statement: :func:`check_statement`, and an
    app's work that may change none of your memory changes nothing in it either. The knowledge and
    vocabulary databases are not your memory, so they run :func:`check_statement` alone."""
    if _SCOPE.get() is None:
        return
    if not script and reads_only(sql):
        return
    refuse_memory_write("a change to the database")


# ── the scope follows work into worker threads ──────────────────────────────────────────────


class ScopeCarryingExecutor(ThreadPoolExecutor):
    """A worker pool that runs each piece of work in the context of the code that handed it over.

    A plain ``ThreadPoolExecutor`` (and so ``loop.run_in_executor``) does not carry context
    variables into the worker thread, while ``asyncio.to_thread`` does. So every worker pool is one
    of these, and it is the gateway's default executor: a store, an embedding function or a model
    reached from a worker thread knows which session the work derives from, as it does on the loop.
    A memory read bounded by a timeout once ran on a plain pool, and an Incognito chat's message
    reached the embedding model from its worker.
    """

    def submit(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> Future[_T]:
        context = contextvars.copy_context()
        return super().submit(context.run, fn, *args, **kwargs)


def carry_scope_into_worker_threads(loop: "asyncio.AbstractEventLoop") -> None:
    """Make ``loop``'s default executor carry the caller's context (see the class above)."""
    loop.set_default_executor(ScopeCarryingExecutor(thread_name_prefix="asyncio"))
