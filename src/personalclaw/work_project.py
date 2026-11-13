"""Which project a piece of work is in: one rule for every list that names work.

The waiting list (``GET /api/waiting``) and the running list (``GET /api/running``) name each
piece of work by its work name (``workref``), and a surface that shows a project's work reads them
filtered to that project. So each item and each row carries the project its work is in, decided
here once:

- ``chat:<key>``: the ``project_id`` of a chat the gateway holds, fixed when it was made. A chat
  it does not hold (kept only on disk, as most are after a restart until they are opened) reads
  as in none.
- ``loop:<id>``: ``loop_store.loop_project_id``: the project it was launched into, else the one made
  for its tasks.
- ``workflow:<run>``: the run's ``project_id``.
- ``subagent:<id>``: its parent's: the chat that started it, or the loop or the run whose session
  did, or the run it works in. The gateway holds a helper from before its start is asked until it
  lets it go once it has ended, so only a helper it has let go (in the just-finished tail) is
  placed by the chat it is shown in.
- ``trigger:``, ``webhook:``, ``job:`` and work of no kind: none. An automation is in no project.

An id that names no project reads as none, so a deleted project never shows, and nothing here
fails a read: any error reads as none. A loop's work carries its kind too (``loop_kind``: ``code``,
``goal``, ``research``, ``design`` or ``general``), from the same read of the loop, and so does a
run started as a loop; any other work carries ``""``.

The gateway's memory (each chat's project, each helper's parent) is taken on its event loop
(:func:`taken`), and the stores are read off it (:class:`ProjectOf`), as each list reads them. The
stores are handed in (:class:`Readers`, which ``waiting.project_readers`` makes) by the lists that
read them anyway, so this rule imports none of them, and the lists' import cycle does not grow.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from personalclaw import workref

logger = logging.getLogger(__name__)

#: The fields a list's item or row gains.
FIELDS = ("project_id", "loop_kind")


class Placed(NamedTuple):
    """The project a piece of work is in, and its loop kind: each ``""`` for none."""

    project_id: str = ""
    loop_kind: str = ""


NONE = Placed()


class Readers(NamedTuple):
    """What a read places work by: the loop store's and the run store's readers, the one loop rule
    (``loop_store.loop_project_id``), whether an id names a project (``projects.project_exists``),
    where a session's work is (``approval_place.place_of``), and the loop kinds there are."""

    loop: Callable[[str], Any]
    run: Callable[[str], Any]
    loop_project_id: Callable[[Any, Callable[[str], bool]], str]
    project_exists: Callable[[str], bool]
    place_of: Callable[[str], Mapping[str, str]]
    loop_kinds: frozenset[str]


@dataclass
class Memory:
    """What the gateway's memory says of where work belongs, taken on its event loop: each chat's
    project, by its work name (``chat:<key>``), and the work each helper works for."""

    chats: dict[str, str] = field(default_factory=dict)
    helpers: dict[str, str] = field(default_factory=dict)


def helper_parent(info: Any, place_of: Callable[[str], Mapping[str, str]]) -> str:
    """The work a helper works for: the work its parent session runs (a chat, a loop's worker, a
    run's step, by *place_of*), else the run it was started in (``parent_run``, already a
    ``workflow:<run>`` name, or, for an agent a run's step started with no parent session, the
    step's run, ``workflow_run``), else ``""``."""
    parent = str(place_of(str(getattr(info, "parent_session_key", "") or "")).get("work") or "")
    run = str(getattr(info, "parent_run", "") or getattr(info, "workflow_run", "") or "")
    if not parent and run:
        parent = run if workref.read(run)[0] == "workflow" else workref.work_ref("workflow", run)
    return parent


def taken(state: Any, readers: Readers) -> Memory:
    """Each chat's project and each helper's parent, from the gateway's memory. Call on its event
    loop. Never raises: what cannot be read is left out, and its work reads as in no project."""
    memory = Memory()
    try:
        for session in list(getattr(state, "_sessions", {}).values()):
            work = _chat_work(str(session.key), readers)
            if work:
                memory.chats[work] = str(getattr(session, "project_id", "") or "")
    except Exception:  # noqa: BLE001 - a chat whose project cannot be read is in none
        logger.warning("work_project: the chats could not be read", exc_info=True)
    try:
        helpers = getattr(getattr(state, "subagents", None), "all_agents", None) or []
        for info in list(helpers):
            memory.helpers[str(info.id)] = helper_parent(info, readers.place_of)
    except Exception:  # noqa: BLE001 - as above
        logger.warning("work_project: the helpers could not be read", exc_info=True)
    return memory


def _chat_work(key: str, readers: Readers) -> str:
    """The chat a session key names, as its work name (``chat:<key>``, whatever prefix the key was
    written with), or ``""`` for a session that is no chat's own (a loop's worker, a run's step)."""
    work = str(readers.place_of(key).get("work") or "")
    return work if workref.read(work)[0] == "chat" else ""


class ProjectOf:
    """``project_of(work)`` for one read of a list: :class:`Placed` for each work name, reading each
    loop, run and project at most once per read. Call off the event loop: it reads the loop and run
    stores and each project's record."""

    def __init__(self, memory: Memory, readers: Readers) -> None:
        self._memory = memory
        self._readers = readers
        self._known: dict[str, bool] = {}
        self._placed: dict[str, Placed] = {}
        self._loops: dict[str, Any] = {}
        self._runs: dict[str, Any] = {}

    def knows(self, *, loops: Iterable[Any] = (), runs: Iterable[Any] = ()) -> None:
        """Keep *loops* and *runs* the list has read already, so none is read twice."""
        for loop in loops:
            self._loops[str(loop.id)] = loop
        for run in runs:
            self._runs[str(run.id)] = run

    def exists(self, project_id: str) -> bool:
        """Whether *project_id* names a project, read once."""
        if not project_id:
            return False
        if project_id not in self._known:
            try:
                self._known[project_id] = bool(self._readers.project_exists(project_id))
            except Exception:  # noqa: BLE001 - an id that cannot be read names none
                self._known[project_id] = False
        return self._known[project_id]

    def __call__(self, work: str, session: str = "") -> Placed:
        """Where *work* is. *session* is the chat it is shown in, which a helper the gateway no
        longer holds works for."""
        key = f"{work}\n{session}"
        if key not in self._placed:
            try:
                self._placed[key] = self._place(work, session, depth=0)
            except Exception:  # noqa: BLE001 - work whose project cannot be read is in none
                logger.warning("work_project: %r could not be placed", work, exc_info=True)
                self._placed[key] = NONE
        return self._placed[key]

    def _project(self, project_id: str) -> str:
        return project_id if self.exists(project_id) else ""

    def _place(self, work: str, session: str, *, depth: int) -> Placed:
        kind, ident = workref.read(work)
        if kind == "chat":
            chat = _chat_work(ident, self._readers) or work
            return Placed(self._project(self._memory.chats.get(chat, "")))
        if kind == "loop":
            if ident not in self._loops:
                self._loops[ident] = self._readers.loop(ident)
            loop = self._loops[ident]
            if loop is None:
                return NONE
            return Placed(
                self._readers.loop_project_id(loop, self.exists),
                self._loop_kind(getattr(loop, "kind", "")),
            )
        if kind == "workflow":
            if ident not in self._runs:
                self._runs[ident] = self._readers.run(ident)
            run = self._runs[ident]
            if run is None:
                return NONE
            return Placed(
                self._project(str(getattr(run, "project_id", "") or "")),
                self._loop_kind(getattr(run, "loop_kind", "")),
            )
        if kind == "subagent" and depth == 0:
            parent = self._memory.helpers.get(ident, "")
            if not parent and session:
                parent = _chat_work(session, self._readers)
            if parent and workref.read(parent)[0] != "subagent":
                return Placed(self._place(parent, "", depth=1).project_id)
        return NONE

    def _loop_kind(self, kind: Any) -> str:
        value = str(getattr(kind, "value", kind) or "")
        return value if value in self._readers.loop_kinds else ""


def stamp(rows: Iterable[MutableMapping[str, Any]], project_of: ProjectOf) -> None:
    """Give each of *rows* (a waiting item, a running row) the project its ``work`` is in and its
    loop kind (:data:`FIELDS`)."""
    for row in rows:
        placed = project_of(str(row.get("work") or ""), str(row.get("session") or ""))
        row["project_id"], row["loop_kind"] = placed.project_id, placed.loop_kind
