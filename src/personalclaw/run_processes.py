"""What a run's commands start ends when the run does, wherever it went.

Measured on a running gateway: a loop's command ran a test suite whose fixture started an embedded
database. The database server detached itself, as a daemon does: it left the command's process
group and its session. The loop's Stop ends the command's whole process group, and the test process
that would have stopped the database was in it, so the Stop ended the one thing that knew about the
server and missed the server. It ran on, unseen, for 13 hours and 46 minutes after the loop read
"Stopped".

**The marker.** Every command a run starts runs with ``PERSONALCLAW_RUN`` naming that run:
``<home>:<owner>:<runner>-<token>``. An environment is inherited through every fork and exec, a
``setsid`` and a double fork included, so a server that detached itself still carries it. *home*
names this PersonalClaw home (two homes on one machine, the owner's and a test's, never end each
other's), *owner* says whose the run is, *runner* names the gateway process that ran it, and
*token* tells one run from another.

**Whose a command is.**

* A turn's commands are the turn's (owner ``turn``): its Stop ends what they started, and so does
  its end. A chat's, a subagent's (a workflow's step is one), an automation's prompt, a heartbeat.
* A loop worker's turns are its loop's (owner: the worker's session, ``loop-<id>`` for its stage
  worker and ``loop-<id>-<task>`` for a task's): what a cycle leaves running, a dev server the next
  cycle tests, goes on until the loop ends (stopped, failed, finished or deleted) or that task's
  worker is taken down. A Stop of one of its turns (a pause) ends what that turn started.
* A command a hook, an automation or a loop's check runs is a run of its own (owner ``command``):
  what it leaves running ends when it exits.
* A workflow run's setup and teardown commands are its workspace's (owner ``workflow-<id>``): a
  service its setup starts runs until the workspace's teardown, when the run is deleted.
* An agent CLI runs its own commands, from a process PersonalClaw starts once for a session, so
  no turn's marker can reach them: the process carries one of its own (owner ``agent``, a loop
  worker's its loop's), and what still carries it ends when the process is taken down (its
  session expires or is reset, its chat goes, the gateway stops). A Stop of its turn is the
  CLI's to carry out, and what that turn's command detached ends with the process.

**Ending them** (:func:`end`, :func:`end_owned`): every process of this user carrying a matching
marker is sent SIGTERM, then SIGKILL if it is still there after :data:`GRACE_SECS`. A process is
signalled only after its start and its marker are read again, and on Linux through a pidfd opened
before that reading, so a pid the system gave another program is never signalled. Never by name,
and never this process or one it runs under.

**What the marker cannot see**, and what backs it up:

* On macOS the kernel withholds the environment of the system's own programs (``/bin/sh``,
  ``bash``, ``sleep``, ``tail``, ``curl``) even from the user running them, so a background job
  running one carries the marker unread.
* A program that starts its child with an empty environment (``env -i``), or writes over the
  memory its environment was in (a server setting its process title, as some database and web
  server workers do: their parent still carries it, and they end with it).
* What another user runs (``sudo``), what is handed to a service manager to start (``launchctl``,
  ``systemd-run``, ``open``), a container's processes and another host's.
* A command can change its own environment: the marker ends what a run starts, it does not fence
  it (the OS sandbox is the fence).

The process-group backstop covers the first two for a background job that stayed in its command's
process group, which a background job does unless it detaches: a command leads its own group, and
when it ends with members of the group still running, they are recorded with its run and the
group ends with the run, whatever their environment says, while one of the recorded members is
still in it (which proves the number still names that group). A session sweep would add nothing: a
process leaves its command's group and session together, by ``setsid``. Recorded groups are held
in memory, so a restart forgets them; the markers it does not forget.

**At the gateway's start and stop** (:func:`end_what_no_run_holds`): no turn, no command and no
agent CLI process outlives the gateway process that ran it, a restart in place included, and an
ended loop holds nothing, so what they started ends then. A loop still going keeps what it
started through a restart, and a workflow run's workspace keeps its services until its teardown.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from personalclaw import process_facts

logger = logging.getLogger(__name__)

#: The variable every command a run starts carries, naming the run.
RUN_VARIABLE = "PERSONALCLAW_RUN"

#: The owner of a turn's commands, for every turn but a loop worker's.
TURN = "turn"
#: The owner of a command that is a run of its own: a hook's, an automation's, a loop's check.
COMMAND = "command"
#: The owner of an agent CLI's process and of what its commands start, but a loop worker's.
AGENT = "agent"

#: How long a process gets to honour SIGTERM before SIGKILL: a database server stops cleanly in
#: it, and one that ignores SIGTERM is not owed longer. Read when a sweep starts.
GRACE_SECS = 3.0

#: How often a sweep looks again while it waits out the grace.
_POLL_SECS = 0.05


# ── the marker ───────────────────────────────────────────────────────────────────────────────


def home_tag() -> str:
    """This home's part of a marker: a digest of where the home is, never the path itself. A home
    that cannot be resolved still gets a tag: a turn must never fail for want of a marker."""
    from personalclaw.config.loader import resolve_config_dir

    try:
        home = str(resolve_config_dir().resolve())
    except Exception:  # noqa: BLE001 - see above
        logger.debug("run marker: the home could not be resolved", exc_info=True)
        home = ""
    return hashlib.sha256(home.encode("utf-8")).hexdigest()[:12]


def _runner() -> str:
    """This gateway process, as a marker names it: its pid and its start, which an in-place
    restart keeps (the process re-executes itself), and a process that reuses the pid does not."""
    me = process_facts.process(os.getpid())
    return f"{os.getpid()}.{me.started if me is not None else 0}"


def mark(owner: str) -> str:
    """The marker a new run of *owner* gives its commands."""
    return f"{home_tag()}:{owner}:{_runner()}-{secrets.token_hex(8)}"


def owner_of(value: str, home: str = "") -> str:
    """The owner marker *value* names, when it is a marker of this home's (*home*, else the home
    in use); ``""`` for another home's, and for anything that is not a marker."""
    tag, sep, rest = (value or "").partition(":")
    owner, sep2, token = rest.rpartition(":")
    if not (sep and sep2 and owner and token) or tag != (home or home_tag()):
        return ""
    return owner


def turn_owner(session_key: str) -> str:
    """Whose a turn of *session_key* is: a loop worker's is its loop's, named by the worker's
    session (``loop-<id>``, ``loop-<id>-<task>``); every other turn is its own (:data:`TURN`)."""
    from personalclaw.constants import DASHBOARD_SESSION_PREFIX
    from personalclaw.loop.manager import worker_ids

    name = str(session_key or "").removeprefix(DASHBOARD_SESSION_PREFIX)
    return name if worker_ids(name)[0] else TURN


def agent_owner(session_key: str) -> str:
    """Whose an agent CLI's process for *session_key* is: a loop worker's is its loop's, every
    other is its own (:data:`AGENT`)."""
    owner = turn_owner(session_key)
    return AGENT if owner == TURN else owner


def loop_owns(loop_id: str, task_id: str = "") -> Callable[[str], bool]:
    """Whether an owner is loop *loop_id*'s: its stage worker or any task worker, or only the
    worker of task *task_id* when one is named."""
    from personalclaw.loop.manager import session_key, task_session_key

    if task_id:
        task = task_session_key(loop_id, task_id)
        return lambda owner: owner == task
    worker = session_key(loop_id)
    return lambda owner: owner == worker or owner.startswith(f"{worker}-")


def workflow_owner(run_id: str) -> str:
    """The owner of workflow run *run_id*'s setup and teardown commands."""
    return f"workflow-{run_id}"


# ── a command a run starts ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Command:
    """One command a run is about to start: the marker its environment carries, and whether it is
    a run of its own, so that what it leaves running ends when it exits."""

    mark: str
    own: bool

    async def ended(self, pgid: int) -> None:
        """The command, which led process group *pgid*, has ended. What is still running in its
        group is recorded with its run (the group backstop), and when it is a run of its own, what
        it left is set ending, not waited for: its caller has its answer at once (a hook answers
        before every tool call). Never raises: what a command left running must not change what
        it answered."""
        try:
            await asyncio.to_thread(note_group, self.mark, pgid)
        except Exception:
            logger.warning("could not record what a command left running", exc_info=True)
        if self.own:
            end_soon(self.mark)


def command() -> Command:
    """A command a call starts now: the turn's when a turn is dispatching it (the turn's stop scope
    is bound for its calls, ``cancellation``), else a run of its own."""
    from personalclaw import cancellation

    scope = cancellation.current_scope()
    run = scope.command_started() if scope is not None else ""
    return Command(run, own=False) if run else own()


def own() -> Command:
    """A command that is a run of its own, whatever is dispatching it."""
    return Command(mark(COMMAND), own=True)


# ── the group backstop ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Group:
    pgid: int
    #: ``(pid, started)`` of each member still running when its command ended: one of them still
    #: in the group proves the number still names that group, not a later one given it.
    members: frozenset[tuple[int, int]]


_GROUPS: dict[str, list[_Group]] = {}
_GROUPS_LOCK = threading.Lock()


def note_group(value: str, pgid: int) -> None:
    """Record with run *value* what is still running in process group *pgid*, the group a command
    of the run led and has just left by ending: a background job it started that did not detach.
    One call finds that there is none, which is what nearly every command leaves."""
    if pgid <= 1 or pgid == os.getpgrp():
        return
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return
    except PermissionError:
        pass  # a member another user runs; this user's own are still recorded
    uid = os.getuid()
    members = frozenset(
        (pid, p.started)
        for pid, p in process_facts.processes().items()
        if p.pgid == pgid and p.uid == uid
    )
    if members:
        with _GROUPS_LOCK:
            _GROUPS.setdefault(value, []).append(_Group(pgid, members))


def _take_groups(accepts: Callable[[str], bool]) -> list[_Group]:
    with _GROUPS_LOCK:
        taken = [value for value in _GROUPS if accepts(value)]
        return [group for value in taken for group in _GROUPS.pop(value)]


def _group_still(group: _Group) -> bool:
    for pid, started in group.members:
        p = process_facts.process(pid)
        if p is not None and p.started == started and p.pgid == group.pgid:
            return True
    return False


def _signal_group(group: _Group, sig: int) -> None:
    if group.pgid <= 1 or group.pgid == os.getpgrp() or not _group_still(group):
        return
    try:
        os.killpg(group.pgid, sig)
    except OSError:
        pass


# ── ending what a run started ────────────────────────────────────────────────────────────────


def _spared() -> set[int]:
    """This process and every process above it: never signalled, whatever they carry."""
    spared = {os.getpid()}
    pid = os.getppid()
    while pid > 1 and pid not in spared:
        spared.add(pid)
        above = process_facts.process(pid)
        pid = above.ppid if above is not None else 0
    return spared


def _carrying(accepts: Callable[[str], bool]) -> dict[int, tuple[str, int]]:
    """``{pid: (marker, started)}`` for this user's processes whose marker *accepts* takes."""
    uid = os.getuid()
    spared = _spared()
    found: dict[int, tuple[str, int]] = {}
    for pid, p in process_facts.processes().items():
        if pid <= 1 or pid in spared or p.uid != uid:
            continue
        value = process_facts.environment_value(pid, RUN_VARIABLE)
        if value and accepts(value):
            found[pid] = (value, p.started)
    return found


def _still(pid: int, value: str, started: int) -> bool:
    """Whether *pid* is still the process that carried *value*: the same start, the same marker."""
    p = process_facts.process(pid)
    return (
        p is not None
        and p.started == started
        and process_facts.environment_value(pid, RUN_VARIABLE) == value
    )


def _signal(pid: int, value: str, started: int, sig: int) -> None:
    """Send *sig* to *pid* only if it is still the process that carried *value*. On Linux the pidfd
    is opened before the check, so the signal reaches that process or nobody."""
    pidfd = None
    opener = getattr(os, "pidfd_open", None)
    sender = getattr(signal, "pidfd_send_signal", None)
    if opener is not None and sender is not None:
        try:
            pidfd = opener(pid)
        except OSError:
            pidfd = None
    try:
        if not _still(pid, value, started):
            return
        if pidfd is not None and sender is not None:
            sender(pidfd, sig)
        else:
            os.kill(pid, sig)
    except OSError:
        pass
    finally:
        if pidfd is not None:
            os.close(pidfd)


#: How many times a sweep looks: a process that forked between one look and its signal (a daemon
#: half-way through its double fork) carries the marker too, and the next look finds it.
_ROUNDS = 3


def sweep(accepts: Callable[[str], bool], *, grace: float | None = None) -> int:
    """End every process of this user whose run marker *accepts* takes, and every recorded group
    of those runs: SIGTERM, then SIGKILL for what is still there after *grace*
    (:data:`GRACE_SECS`), and again for what appeared meanwhile. Returns how many processes it
    ended. Blocking: it reads every process this user runs and waits out the grace, so a
    coroutine calls :func:`end` or :func:`end_owned`."""
    grace = GRACE_SECS if grace is None else grace
    groups = _take_groups(accepts)
    ended: set[int] = set()
    killed = 0
    for _round in range(_ROUNDS):
        carrying = _carrying(accepts)
        groups = [group for group in groups if _group_still(group)]
        if not carrying and not groups:
            break
        ended |= set(carrying) | {pid for group in groups for pid, _started in group.members}
        killed += _end_these(carrying, groups, grace)
    if ended:
        logger.info(
            "ended %d process(es) a run had left running (%d were killed after %.1fs)",
            len(ended),
            killed,
            grace,
        )
    return len(ended)


def _end_these(carrying: dict[int, tuple[str, int]], groups: list[_Group], grace: float) -> int:
    """SIGTERM to each, then SIGKILL to what is still there after *grace*; how many needed it."""
    for pid, (value, started) in carrying.items():
        _signal(pid, value, started, signal.SIGTERM)
    for group in groups:
        _signal_group(group, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while True:
        left = {pid: seen for pid, seen in carrying.items() if _still(pid, *seen)}
        held = [group for group in groups if _group_still(group)]
        if not (left or held) or time.monotonic() >= deadline:
            break
        time.sleep(_POLL_SECS)
    for pid, (value, started) in left.items():
        _signal(pid, value, started, signal.SIGKILL)
    for group in held:
        _signal_group(group, signal.SIGKILL)
    return len(left) + len(held)


async def _swept(accepts: Callable[[str], bool]) -> int:
    try:
        return await asyncio.to_thread(sweep, accepts)
    except Exception:
        logger.warning("could not end what a run left running", exc_info=True)
        return 0


async def end(value: str) -> int:
    """End what the run whose marker is *value* started. Returns how many. Never raises."""
    if not value:
        return 0
    return await _swept(lambda carried: carried == value)


def end_now(value: str) -> int:
    """:func:`end`, for code already running off the event loop (a scheduled script's thread)."""
    try:
        return sweep(lambda carried: carried == value) if value else 0
    except Exception:
        logger.warning("could not end what a run left running", exc_info=True)
        return 0


async def end_owned(owns: Callable[[str], bool]) -> int:
    """End what every run of this home whose owner *owns* accepts started (:func:`loop_owns`,
    :func:`workflow_owner`). Returns how many processes. Never raises."""
    home = home_tag()

    def accepts(carried: str) -> bool:
        owner = owner_of(carried, home)
        return bool(owner) and owns(owner)

    return await _swept(accepts)


_PENDING: set[asyncio.Task[int]] = set()


def end_soon(value: str) -> None:
    """:func:`end`, from code that cannot wait for it: a turn's last step, in a ``finally``. The
    task is kept until it is done, so it is not collected half-way."""
    try:
        task = asyncio.get_running_loop().create_task(end(value), name="end what a run started")
    except RuntimeError:
        return  # no loop runs here now: the gateway's stop ends what is left
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


def end_what_no_run_holds() -> int:
    """At the gateway's start and at its stop: end what this home's runs started that no run can
    still hold. A turn, a command or an agent CLI's process whose gateway process is this one (a
    restart re-executes it in place, keeping its pid) or is no longer running is over, and so is
    a loop that has ended. A loop still going keeps what it started, and so does a workflow run's
    workspace until its teardown. Returns how many processes it ended. Blocking; never raises.

    A marker of this home's in the gateway's own environment came from the command that started it
    (a restart run from one of its turns): the gateway is no run's, and the children it starts
    with its own environment must not carry it on, so it is dropped first."""
    try:
        home = home_tag()
        if owner_of(os.environ.get(RUN_VARIABLE, ""), home):
            os.environ.pop(RUN_VARIABLE, None)
        me = _runner()
        return sweep(lambda carried: _held_by_nothing(carried, home, me))
    except Exception:
        logger.warning("could not end what ended runs had left running", exc_info=True)
        return 0


def _held_by_nothing(carried: str, home: str, me: str) -> bool:
    """Whether no run can hold what carries *carried* any more. A loop whose state cannot be read
    is taken to be going: an ending must never end what may still be a running loop's."""
    owner = owner_of(carried, home)
    if owner in (TURN, COMMAND, AGENT):
        runner = carried.rpartition(":")[2].partition("-")[0]
        return runner == me or _gone(runner)
    if owner.startswith("loop-"):
        from personalclaw.loop import children
        from personalclaw.loop.manager import worker_ids

        try:
            loop_id = worker_ids(owner)[0]
            return bool(loop_id) and bool(children.why_over(loop_id))
        except Exception:
            logger.debug("loop %s: its state could not be read", owner, exc_info=True)
    return False


def _gone(runner: str) -> bool:
    """Whether the gateway process *runner* (``<pid>.<started>``) no longer runs."""
    pid, _sep, started = runner.partition(".")
    try:
        p = process_facts.process(int(pid))
        return p is None or p.started != int(started)
    except ValueError:
        return False
