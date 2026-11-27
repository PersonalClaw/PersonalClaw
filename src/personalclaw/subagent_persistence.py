"""Subagent persistence — disk I/O for agent folders.

Each subagent gets a folder at ``~/.personalclaw/subagents/{id}/`` containing:
- ``state.json``   — its state (task, PID, turns, last_tool), and its ``status``: ``running``, or,
  for a spawn still waiting to start, ``queued`` (for a free slot) or ``asking`` (for its owner's
  Allow), with the ``request`` it is started again from after a restart (``subagent_waiting``)
- ``result.txt``   — streamed result text
- ``tombstone.json`` — written on abnormal exit only
"""

import json
import logging
import shutil
import threading
import time
from collections.abc import Callable, Container, Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from personalclaw import process_facts
from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader
from personalclaw.llm.cleanup import _is_safe_path
from personalclaw.session_keys import SUBAGENT


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)


def _subagents_dir() -> Path:
    """Resolve ``<home>/subagents`` at CALL time.

    This was a module-level constant (``_SUBAGENTS_DIR = config_dir() / "subagents"``),
    which froze the home at first import. Two consequences, one of each kind: a
    ``$PERSONALCLAW_HOME`` established after this module was imported was ignored for the
    rest of the process, and under pytest no fixture could redirect it — a full suite wrote
    147 entries into the developer's real ``~/.personalclaw/subagents``. Resolving
    per call means the active home always wins.
    """
    return config_dir() / "subagents"


def _agent_dir(agent_id: str) -> Path:
    if (
        not agent_id
        or agent_id == "."
        or ".." in agent_id
        or "/" in agent_id
        or "\\" in agent_id
        or "\0" in agent_id
    ):
        raise ValueError(f"Invalid agent_id: {agent_id!r}")
    root = _subagents_dir()
    resolved = (root / agent_id).resolve()
    parent = root.resolve()
    if resolved == parent or not resolved.is_relative_to(parent):
        raise ValueError(f"Path traversal blocked for agent_id: {agent_id!r}")
    return resolved


# ── create ───────────────────────────────────────────────────────────

#: The ``status`` of an agent that has started.
RUNNING = "running"
#: The ``status`` of a spawn waiting for a free slot.
QUEUED = "queued"
#: The ``status`` of a spawn waiting for its owner to allow its start.
ASKING = "asking"
#: The statuses of a spawn that has not started yet.
WAITING = (QUEUED, ASKING)


def create_agent_folder(
    agent_id: str,
    *,
    task: str = "",
    agent: str = "",
    parent_session: str = "",
    max_turns: int = 0,
    title: str = "",
    status: str = RUNNING,
    request: dict | None = None,
) -> Path:
    """Create ``~/.personalclaw/subagents/{id}/`` with ``state.json``.

    ``title`` is what the run is called (``SubagentInfo.title``), kept so a restart that finds it
    left behind can name it as its completion would have. ``status`` and ``request`` are a waiting
    spawn's (``subagent_waiting``); the folder of one that starts is written again as ``running``,
    with no ``request``."""
    d = _agent_dir(agent_id)
    d.mkdir(parents=True, exist_ok=True)
    state = {
        "id": agent_id,
        "task": task,
        "title": title,
        "agent": agent,
        "parent_session": parent_session,
        "started": time.time(),
        "max_turns": max_turns,
        "status": status,
        "pid": None,
        "turns": 0,
        "last_tool": "",
        "updated_at": time.time(),
    }
    if request is not None:
        state["request"] = request
    if holder := _PENDING_TIES.pop(agent_id, ""):
        state[CLAIM_HOLDER] = holder
    _atomic_write(d / "state.json", state)
    return d


# ── read / update ────────────────────────────────────────────────────


def read_state(agent_id: str) -> dict | None:
    """Read state.json. Returns None on missing/corrupt."""
    try:
        p = _agent_dir(agent_id) / "state.json"
    except ValueError:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def update_state(agent_id: str, **fields: object) -> None:
    """Merge *fields* into state.json (atomic rewrite)."""
    p = _agent_dir(agent_id) / "state.json"
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        logger.debug("update_state: cannot read state for %s, skipping", agent_id)
        return
    state.update(fields)
    state["updated_at"] = time.time()
    _atomic_write(p, state)


def record_pid(agent_id: str, pid: int) -> None:
    """Record the process an agent runs in, so a later run can stop it if this one leaves it
    behind: its pid, when the pid was recorded, and the kernel's start mark for it
    (``process_facts``). The mark names that one process for as long as it runs, so a later run
    can tell it from another program the system gave the pid to since."""
    found = process_facts.process(pid)
    update_state(
        agent_id,
        pid=pid,
        pid_recorded_at=time.time(),
        pid_started=found.started if found is not None else None,
    )


#: The key of a workflow step's attempt in its agent's state: the claim holder the step took its
#: no-double-execution claim with (`engine.claim_holder`), written as the spawn returns, before the
#: agent's task first runs, so a folder that holds none was no process of that attempt's.
CLAIM_HOLDER = "claim_holder"
#: The key of the commands an agent's in-process runtime runs, each in a process group it leads:
#: ``[[pid, started], ...]``, kept while each runs, so a later run can end one it left behind.
TOOL_GROUPS = "tool_groups"
_TOOL_GROUPS_LOCK = threading.Lock()
#: The holder of each spawn whose folder is not made yet (:func:`tie_to_claim`), by agent id.
_PENDING_TIES: dict[str, str] = {}


def tie_to_claim(agent_id: str, holder: str) -> None:
    """Record in the agent *agent_id*'s folder the claim holder its workflow step took its claim
    with (:data:`CLAIM_HOLDER`). Synchronous, so a spawn's caller writes it before the agent's
    task first runs: a folder no holder names was then no process of that attempt's. A spawn
    that waits (for a slot, or for her Allow) makes its folder only when it starts, so the holder
    waits here and goes into the folder as it is made (:func:`create_agent_folder`)."""
    if not (agent_id and holder):
        return
    if read_state(agent_id) is None:
        _PENDING_TIES[agent_id] = holder
        return
    update_state(agent_id, **{CLAIM_HOLDER: holder})


def holding(holder: str) -> dict | None:
    """The state of the agent folder whose :data:`CLAIM_HOLDER` is *holder*, tombstoned or not, or
    None when no folder holds it."""
    if not holder:
        return None
    try:
        dirs = sorted(_subagents_dir().iterdir())
    except (FileNotFoundError, OSError):
        return None
    for d in dirs:
        state = read_state(d.name) if d.is_dir() else None
        if state is not None and state.get(CLAIM_HOLDER) == holder:
            return state
    return None


def record_tool_group(agent_id: str, pid: int) -> None:
    """Record that the agent *agent_id* runs a command in the process group *pid* leads, with the
    kernel's start mark for it, so a later run can end it if this one leaves it behind."""
    found = process_facts.process(pid)
    if found is None or not agent_id:
        return
    with _TOOL_GROUPS_LOCK:
        state = read_state(agent_id)
        if state is None:
            return
        kept = [g for g in state.get(TOOL_GROUPS) or [] if isinstance(g, list)]
        update_state(agent_id, **{TOOL_GROUPS: [*kept, [pid, found.started]]})


def forget_tool_group(agent_id: str, pid: int) -> None:
    """Drop the command in group *pid* from what the agent *agent_id* runs: it has ended."""
    with _TOOL_GROUPS_LOCK:
        state = read_state(agent_id) if agent_id else None
        if state is None:
            return
        kept = [g for g in state.get(TOOL_GROUPS) or [] if isinstance(g, list) and g[:1] != [pid]]
        update_state(agent_id, **{TOOL_GROUPS: kept})


@contextmanager
def tool_group(session_key: str, pid: int) -> Iterator[None]:
    """While a command runs in the process group *pid* leads, keep it in the folder of the subagent
    whose session is *session_key* (:func:`record_tool_group`); another session keeps nothing.

    A gateway that dies leaves the group running: it is a session of its own, and on macOS a
    system shell's marker is not shown (`run_processes`), so the restart ends it from here before
    the step that ran it runs again (`workflows.left_running`). Called as the spawn returns, with
    no await between, so the group is kept before the command can do much of its work."""
    agent_id = session_key.removeprefix(SUBAGENT.prefix) if SUBAGENT.names(session_key) else ""
    if agent_id:
        record_tool_group(agent_id, pid)
    try:
        yield
    finally:
        if agent_id:
            forget_tool_group(agent_id, pid)


# ── result streaming ─────────────────────────────────────────────────


def write_result_chunk(agent_id: str, text: str) -> None:
    """Append *text* to ``result.txt``."""
    p = _agent_dir(agent_id) / "result.txt"
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        logger.debug("write_result_chunk failed for %s", agent_id, exc_info=True)


# ── tombstone ────────────────────────────────────────────────────────


def _check_result_available(path: Path) -> bool:
    """Check if result file exists and is non-empty (TOCTOU-safe)."""
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def write_tombstone(
    agent_id: str,
    *,
    cause: str,
    recovery_action: str,
    **extra: object,
) -> None:
    """Write ``tombstone.json`` for an abnormally exited agent."""
    d = _agent_dir(agent_id)
    state = read_state(agent_id) or {}
    tombstone = {
        "id": agent_id,
        "task": state.get("task", ""),
        "agent": state.get("agent", ""),
        "parent_session": state.get("parent_session", ""),
        "started": state.get("started"),
        "died": time.time(),
        "cause": cause,
        "recovery_action": recovery_action,
        "result_available": _check_result_available(d / "result.txt"),
        "result_path": str(d / "result.txt"),
        **extra,
    }
    try:
        _atomic_write(d / "tombstone.json", tombstone)
    except OSError:
        logger.warning("write_tombstone failed for %s", agent_id, exc_info=True)


# ── delete ───────────────────────────────────────────────────────────


def delete_agent_folder(agent_id: str) -> None:
    """Remove the entire agent directory."""
    d = _agent_dir(agent_id)
    shutil.rmtree(d, ignore_errors=True)


# ── a chat's helpers ─────────────────────────────────────────────────


def _recorded(folder: Path, key: str) -> str:
    """What agent *folder* recorded under *key*: in its state, else in its tombstone."""
    for name in ("state.json", "tombstone.json"):
        try:
            data = json.loads((folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get(key):
            return str(data[key])
    return ""


def started_by(home: Path | None = None) -> dict[str, str]:
    """The session each agent folder in *home*'s ``subagents/`` (the active home's when None) was
    started from, by agent id (its ``parent_session``): a helper's helper names its helper's
    session, ``subagent:<id>``."""
    root = (Path(home) if home is not None else config_dir()) / "subagents"
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return {}
    return {folder.name: _recorded(folder, "parent_session") for folder in folders}


def helpers_of(owns: Callable[[str], bool], parents: Mapping[str, str]) -> set[str]:
    """The agents among *parents* (:func:`started_by`) a session *owns* claims started, and every
    one those started in turn, by id."""
    found: set[str] = set()
    while more := {
        agent_id
        for agent_id, parent in parents.items()
        if agent_id not in found and (owns(parent) or parent in {SUBAGENT.key(f) for f in found})
    }:
        found |= more
    return found


def chat_of(agent_id: str, parents: Mapping[str, str]) -> str:
    """The session at the top of agent *agent_id*'s chain of helpers (:func:`started_by`): the
    first in it that is no helper's. ``""`` when a helper in the chain has no folder left to say
    whose work it was."""
    seen: set[str] = set()
    key = parents.get(agent_id, "")
    while SUBAGENT.names(key):
        above = key.removeprefix(SUBAGENT.prefix)
        if above in seen or above not in parents:
            return ""
        seen.add(above)
        key = parents[above]
    return key


def kept_by_helpers_of(owns: Callable[[str], bool], home: Path) -> set[Path]:
    """The folders in *home* of the helpers a session *owns* claims started (:func:`helpers_of`),
    and the agent session files each kept (``sessions/<session_id>``), resolved. A session id that
    names a path outside ``sessions/`` names none, as when the files are deleted
    (:func:`_cleanup_session_files_sync`): the id is the agent client's own."""
    root, sessions = Path(home) / "subagents", Path(home) / "sessions"
    kept: set[Path] = set()
    for agent_id in helpers_of(owns, started_by(home)):
        kept.add((root / agent_id).resolve())
        session_id = _recorded(root / agent_id, "session_id")
        if session_id in ("", ".", ".."):
            continue
        files = [sessions / f"{session_id}{suffix}" for suffix in _SESSION]
        kept |= {f.resolve() for f in files if _is_safe_path(f, sessions)}
    return kept


def forget_helpers(
    owns: Callable[[str], bool], *, ended: Callable[[str], bool | None]
) -> list[str]:
    """Remove the folder of every helper a session *owns* claims started, and of every one those
    started in turn (:func:`helpers_of`), tombstoned or not, with the agent session files each
    kept, as the prune does. Returns the ids it removed.

    *ended* says whether this gateway's helper by an id has ended, or ``None`` when it runs none by
    it. A folder stays while its helper may still run: one still going here, and one with no
    tombstone this gateway does not run, which a previous one left, its process maybe still alive,
    and which the start settles first (``subagent_orphans``). So does each folder above one that
    stays in its chain, which says whose work it is (:func:`chat_of`)."""
    parents = started_by()
    found = helpers_of(owns, parents)
    root = _subagents_dir()
    going = {
        agent_id
        for agent_id in found
        if (done := ended(agent_id)) is False
        or (done is None and not (root / agent_id / "tombstone.json").exists())
    }
    for agent_id in list(going):
        key = parents.get(agent_id, "")
        while SUBAGENT.names(key) and (above := key.removeprefix(SUBAGENT.prefix)) in found:
            if above in going:
                break  # walked already, from its own folder or from another below it
            going.add(above)
            key = parents.get(above, "")
    for agent_id in sorted(found - going):
        _remove(root / agent_id)
    return sorted(found - going)


def forget_settled(agent_ids: Iterable[str], *, tracked: Container[str]) -> list[str]:
    """Remove the folder of each of *agent_ids* that has a tombstone and is not one of *tracked*
    (the agents this gateway runs), with the agent session files it kept: the start's, once it has
    settled what a previous gateway left (``subagent_orphans``). Returns the ids it removed."""
    root = _subagents_dir()
    removed = [
        agent_id
        for agent_id in sorted(agent_ids)
        if agent_id not in tracked and (root / agent_id / "tombstone.json").is_file()
    ]
    for agent_id in removed:
        _remove(root / agent_id)
    return removed


def _remove(folder: Path) -> None:
    """Remove agent *folder*, and the agent session files it kept (its ``session_id``)."""
    if session_id := _recorded(folder, "session_id"):
        _cleanup_session_files_sync(session_id)
    shutil.rmtree(folder, ignore_errors=True)


# ── list orphans ─────────────────────────────────────────────────────


def _untombstoned() -> list[dict]:
    """Parsed state of every agent folder with no tombstone."""
    results: list[dict] = []
    try:
        dirs = sorted(_subagents_dir().iterdir())
    except (FileNotFoundError, OSError):
        return results
    for d in dirs:
        if not d.is_dir():
            continue
        if (d / "tombstone.json").exists():
            continue
        state = read_state(d.name)
        if state is None:
            logger.debug("list_orphans: skipping corrupt state in %s", d.name)
            continue
        results.append(state)
    return results


def list_orphans() -> list[dict]:
    """Return parsed state for all non-tombstoned folders of agents that started: the ones a
    previous run was running when it stopped."""
    return [s for s in _untombstoned() if s.get("status") not in WAITING]


def list_waiting() -> list[dict]:
    """Return parsed state for all non-tombstoned folders of spawns that had not started: the ones
    a previous run left waiting for a slot or for its owner's Allow (``subagent_waiting``)."""
    return [s for s in _untombstoned() if s.get("status") in WAITING]


#: The ``cause`` of a tombstone a restore writes (:func:`settle_restored`).
RESTORED = "restored"


def settle_restored(home: Path) -> list[str]:
    """Tombstone every agent folder in *home* that has no tombstone, as a restore has just
    written them. Returns their ids.

    🔴 A restore brings back the folders of the agents that were running when the archive was
    taken, and the next start settles every folder without a tombstone as an agent the previous
    gateway left running: it kills a live process under the recorded pid that started before the
    agent did, and tells the owner the restart stopped it. For another machine's archive, the pid
    and its start time were recorded there, so the process they name here is one of this
    machine's own. A restore's own tombstone says where the agent went, and the start leaves the
    folder alone. So does a spawn the archive held waiting to start, which the start would
    otherwise start again: nothing a snapshot held starts until someone asks for it again.

    Reads and writes *home*'s own files, not the active home's: the restore names the home it
    wrote.
    """
    root = Path(home) / "subagents"
    settled: list[str] = []
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return settled
    now = time.time()
    for folder in folders:
        if (folder / "tombstone.json").exists() or not (folder / "state.json").is_file():
            continue
        try:
            state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state = state if isinstance(state, dict) else {}
        tombstone = {
            "id": folder.name,
            "task": state.get("task", ""),
            "agent": state.get("agent", ""),
            "parent_session": state.get("parent_session", ""),
            "started": state.get("started"),
            "died": now,
            "cause": RESTORED,
            "recovery_action": "none",
            "result_available": _check_result_available(folder / "result.txt"),
            "result_path": str(folder / "result.txt"),
        }
        try:
            _atomic_write(folder / "tombstone.json", tombstone)
        except OSError:
            logger.warning("settle_restored: could not tombstone %s", folder.name, exc_info=True)
            continue
        settled.append(folder.name)
    return settled


# ── prune ────────────────────────────────────────────────────────────


def prune_stale_tombstones(max_age_days: int = 7) -> int:
    """Delete tombstoned folders older than *max_age_days*. Returns count pruned."""
    cutoff = time.time() - (max_age_days * 86400)
    pruned = 0
    try:
        dirs = sorted(_subagents_dir().iterdir())
    except (FileNotFoundError, OSError):
        return 0
    for d in dirs:
        if not d.is_dir():
            continue
        ts_path = d / "tombstone.json"
        if not ts_path.exists():
            continue
        try:
            ts = json.loads(ts_path.read_text(encoding="utf-8"))
            if ts.get("died", 0) < cutoff:
                try:
                    state = read_state(d.name)
                    session_id = ts.get("session_id") or (
                        state.get("session_id", "") if state else ""
                    )
                    if session_id:
                        _cleanup_session_files_sync(session_id)
                except Exception:
                    logger.debug("prune: session cleanup failed for %s", d.name, exc_info=True)
                shutil.rmtree(d, ignore_errors=True)
                pruned += 1
        except (json.JSONDecodeError, OSError):
            logger.debug("prune: skipping corrupt tombstone in %s", d.name)
    return pruned


# ── session file cleanup ──────────────────────────────────────────────


#: The suffixes of the files an agent session keeps under ``sessions/<session_id>``.
_SESSION = (".json", ".jsonl")


def _cleanup_session_files_sync(session_id: str) -> None:
    """Delete ACP agent session files for a completed subagent.

    Synchronous — used during tombstone pruning (which runs in the reaper loop).
    Best-effort: logs warnings on failure, never raises.
    """
    if not session_id or session_id in (".", ".."):
        return
    try:
        sessions_dir = config_loader.config_dir() / "sessions"
        for suffix in _SESSION:
            target = sessions_dir / f"{session_id}{suffix}"
            if not _is_safe_path(target, sessions_dir):
                logger.error(
                    "_cleanup_session_files_sync: path traversal blocked for %s",
                    target,
                )
                return
            try:
                target.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "_cleanup_session_files_sync: failed to delete %s",
                    target,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "_cleanup_session_files_sync: unexpected error cleaning session %s",
            session_id,
            exc_info=True,
        )


# ── helpers ──────────────────────────────────────────────────────────


def _atomic_write(path: Path, data: dict) -> None:
    """Write *data* as JSON atomically (durable, fsync'd)."""
    atomic_write(path, json.dumps(data, ensure_ascii=False), fsync=True)
