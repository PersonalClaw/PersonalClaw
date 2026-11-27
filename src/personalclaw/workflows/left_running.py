"""What a workflow step's last attempt left running when its gateway died, ended before it reruns.

A ``stage`` runs in a helper: an agent CLI the gateway starts, which leads a process group and a
session of its own (`acp.transport`), or the in-process runtime, whose bash tool starts each
command in a group of its own. When the gateway dies, those processes may live on: they are in
sessions of their own, and on macOS the kernel does not show a system program's environment
(``sh``, ``bash``, ``curl``), so no run marker finds the commands they run (`run_processes`). A
restarted run puts the step back in the queue (`run_start.requeue_lost_steps`,
`stage_settlement.requeue_orphaned_stages`), and it would then run twice at once: the old helper's
command and the new attempt.

So before either puts a step back, :func:`end_left_running` ends what that step's own last attempt
left running, and when it cannot (a process it cannot read, or one still there after the grace),
the step fails saying which process may still be doing its work, rather than running beside it.

**Only what the attempt's own folder records** (`subagent_persistence`), and only once that folder
is the attempt's: it names the claim holder the claim being given back names
(`subagent_persistence.tie_to_claim`, written before the agent's task first runs), and, for an
attempt the run recorded, it is the agent the run names. Never a pid from anywhere else. The agent
CLI the folder records is signalled only while it is still that process (the kernel's start mark)
and an agent CLI of this home (its run marker); each group only while a member seen when it was
read is still in it (`run_processes.end_groups`).

**What it ends:** the CLI's group; the group of each process below it that started after it and
left its group (a command a CLI starts ``detached``); and each group the in-process runtime
recorded for a command it runs (`subagent_persistence.tool_group`). Every attempt of the run at
once, with one grace.

What it cannot end: a command that left its group and whose parent died before the restart is in
no group recorded and below no process alive, and on macOS a system program carries its marker
unread, so nothing finds it.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from personalclaw import process_facts, run_processes, subagent_persistence
from personalclaw.workflows.models import (
    Failure,
    FailureClass,
    InstanceState,
    NodeKind,
    now_stamp,
)
from personalclaw.workflows.models import spec_path as _spec_path
from personalclaw.workflows.models import (
    walk,
)

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)

#: A process group to end: ``(pgid, {(pid, started), ...})``, each member as it was read.
Group = tuple[int, frozenset[tuple[int, int]]]

#: How much earlier than its agent's folder a command may read as started: the kernel's start
#: mark counts in clock ticks, and the folder's ``started`` is the wall clock when it was made.
_START_SLACK_SECS = 1.0


@dataclass
class Attempt:
    """One step's last attempt, as its folder records it: what of it may still run."""

    path: str
    node_id: str
    claim_target: str
    claim_holder: str
    groups: list[Group] = field(default_factory=list)
    #: Why what it left cannot be told from what it did not, or "": the step then fails.
    unread: str = ""


async def end_left_running(ctl: RunController) -> list[str]:
    """End what each step of *ctl*'s run that a restart puts back in the queue left running; return
    the paths of the steps that failed because what they left could not be ended.

    Called when a resumed run's controller starts, before the steps go back in the queue. A step
    whose attempt left nothing goes back as before."""
    attempts = left_by(ctl)
    if not attempts:
        return []
    groups = [group for attempt in attempts for group in attempt.groups]
    try:
        still = set(await asyncio.to_thread(run_processes.end_groups, groups)) if groups else set()
    except Exception:  # noqa: BLE001 - what could not be ended is reported below as still there
        logger.warning("run %s: could not end what its steps left running", ctl.run.id)
        still = {pgid for pgid, _members in groups}
    failed = []
    for attempt in attempts:
        why = attempt.unread or _still_running(attempt, still)
        if why:
            _fail(ctl, attempt, why)
            failed.append(attempt.path)
    if failed:
        ctl._persist_state()
    return failed


def left_by(ctl: RunController) -> list[Attempt]:
    """Each step of *ctl*'s run a restart puts back in the queue whose folder records a process of
    its last attempt that may still run."""
    table = process_facts.processes()
    out = [*_unrecorded(ctl, table), *_recorded(ctl, table)]
    return [attempt for attempt in out if attempt.groups or attempt.unread]


def _unrecorded(ctl: RunController, table: dict[int, process_facts.Process]) -> list[Attempt]:
    """The attempts a dead controller dispatched and never recorded
    (`run_start.requeue_lost_steps`): their folder names the holder of the claim it took."""
    from personalclaw.workflows import engine, leases, ownership

    nodes = dict(walk(ctl.root))
    out = []
    for path, inst in ctl.instances.items():
        node = nodes.get(_spec_path(path))
        if (
            inst.state is not InstanceState.RUNNING
            or inst.subagent_id
            or path in ctl._inflight
            or node is None
            or node.kind is not NodeKind.STAGE
        ):
            continue
        target = engine.claim_key(ctl.run.id, path)
        held = leases.read_claim(target) if target else None
        own = f"{ownership.owned_key(ctl.run.id, node.id or 'node')}#"
        if held is None or not held.holder.startswith(own):
            continue
        state = subagent_persistence.holding(held.holder)
        if state is not None:
            out.append(_read(Attempt(path, node.id, target, held.holder), state, table))
    return out


def _recorded(ctl: RunController, table: dict[int, process_facts.Process]) -> list[Attempt]:
    """The attempts the run recorded whose agent this process does not know
    (`stage_settlement.requeue_orphaned_stages`): their folder is that agent's, when it names the
    holder the run recorded."""
    from personalclaw.workflows.stage_settlement import awaiting_out_of_band_work

    manager = ctl.services.subagents
    if manager is None or not hasattr(manager, "get"):
        return []
    nodes = dict(walk(ctl.root))
    out = []
    for path in awaiting_out_of_band_work(ctl):
        inst = ctl._instance(path)
        try:
            if manager.get(inst.subagent_id) is not None:
                continue
        except Exception:  # noqa: BLE001 - an unreadable manager is not evidence the work is gone
            continue
        state = subagent_persistence.read_state(inst.subagent_id)
        if state is None:
            continue
        node = nodes.get(_spec_path(path))
        node_id = (node.id if node is not None else "") or path
        attempt = Attempt(path, node_id, inst.claim_target, inst.claim_holder)
        if state.get(subagent_persistence.CLAIM_HOLDER) != inst.claim_holder:
            out.append(_unread_if_live(attempt, state, table))
        else:
            out.append(_read(attempt, state, table))
    return out


def _unread_if_live(attempt: Attempt, state: dict[str, Any], table: dict) -> Attempt:
    """An attempt whose folder does not name its holder (a folder from before folders named one):
    nothing of it is signalled, and when its agent may still run, it cannot be told as its."""
    pid, started = state.get("pid"), state.get("pid_started")
    found = table.get(pid) if isinstance(pid, int) else None
    if found is not None and not (isinstance(started, int) and found.started != started):
        attempt.unread = _maybe(found.pid)
    return attempt


def _read(attempt: Attempt, state: dict[str, Any], table: dict) -> Attempt:
    """*attempt* with the groups its folder *state* records that still run, read from *table*.

    A command group is the attempt's only while it is the process recorded (its start mark), it
    leads its own group and not this gateway's, and it started no earlier than the attempt's agent
    (the folder's ``started``): a shell carries no marker to read on macOS, so a folder naming a
    process started before it, or this gateway's own group, names nothing of the attempt's."""
    _read_leader(attempt, state, table)
    since = state.get("started")
    for entry in state.get(subagent_persistence.TOOL_GROUPS) or []:
        if isinstance(entry, list) and len(entry) == 2 and all(isinstance(v, int) for v in entry):
            pid, started = entry
            found = table.get(pid)
            if found is not None and found.started == started and found.pgid == pid:
                if pid == os.getpgrp():
                    continue
                began = process_facts.started_at(found)
                if began is None or not isinstance(since, (int, float)):
                    attempt.unread = _maybe(pid)  # nothing can tell whether it is the attempt's
                elif began >= since - _START_SLACK_SECS:
                    attempt.groups.append(_group(pid, started, table))
            elif found is None and _ours_unread(pid):
                attempt.unread = _maybe(pid)
    return attempt


def _ours_unread(pid: int) -> bool:
    """Whether *pid* runs as this user though its facts could not be read: it may be what the
    folder recorded, and nothing can tell. Another user's process is not one an agent of this
    user's left, so it is no reason to hold a step, and nor is a zombie: it has ended, and only
    waits for its parent (a gateway restarted in place keeps the old one's children) to collect
    it."""
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except OSError:  # gone, or another user's (PermissionError)
        return False
    stat = process_facts.run_probe(["ps", "-o", "stat=", "-p", str(pid)]).strip()
    return not stat.startswith("Z")


def _read_leader(attempt: Attempt, state: dict[str, Any], table: dict) -> None:
    """The agent CLI the folder records, its group, and the group of each process below it that
    left it: only while it is the process the folder recorded and an agent CLI of this home."""
    pid, started = state.get("pid"), state.get("pid_started")
    found = table.get(pid) if isinstance(pid, int) and pid > 1 else None
    if found is None:
        if isinstance(pid, int) and _ours_unread(pid):
            attempt.unread = _maybe(pid)
        return
    if not isinstance(started, int) or isinstance(started, bool):
        attempt.unread = _maybe(found.pid)
        return
    if found.started != started:
        return  # the system gave the pid to another program: the agent is gone
    marker = process_facts.environment_value(found.pid, run_processes.RUN_VARIABLE)
    if run_processes.owner_of(marker or "") != run_processes.AGENT or found.pgid != found.pid:
        attempt.unread = _maybe(found.pid)
        return
    attempt.groups.append(_group(found.pid, started, table))
    below = process_facts.descendants(found.pid, {p: (q.ppid, "") for p, q in table.items()})
    leaders = {table[p].pgid for p in below if p in table and table[p].started >= started}
    for pgid in sorted(leaders - {found.pgid, 0, 1, os.getpgrp()}):
        attempt.groups.append(_group(pgid, started, table))


def _group(pgid: int, since: int, table: dict[int, process_facts.Process]) -> Group:
    """The group *pgid* as *table* has it: this user's members that started no earlier than
    *since*, each with its start mark."""
    uid = os.getuid()
    return pgid, frozenset(
        (pid, p.started)
        for pid, p in table.items()
        if p.pgid == pgid and p.uid == uid and p.started >= since
    )


def _maybe(pid: int) -> str:
    return (
        f"its earlier attempt's helper may still be running as process {pid}, which PersonalClaw "
        "could not check was its own after the restart"
    )


def _still_running(attempt: Attempt, still: set[int]) -> str:
    left = sorted({pgid for pgid, _members in attempt.groups} & still)
    if not left:
        return ""
    return (
        f"what its earlier attempt left running (process group {', '.join(map(str, left))}) "
        "was still running after PersonalClaw ended it at the restart"
    )


def _fail(ctl: RunController, attempt: Attempt, why: str) -> None:
    """Fail the step at *attempt*'s path, saying *why*, and give back its claim: her retry of it,
    once what it left has ended, runs it again."""
    from personalclaw.workflows.engine import release_execution_claim
    from personalclaw.workflows.step_usage import StepUsage

    inst = ctl.instances[attempt.path]
    failure = Failure(
        failure_class=FailureClass.USER,
        cause_plain=f"The step was not run again: {why}.",
        remediation=(
            "make sure that process has ended (Activity Monitor, or `kill` it), then fork this "
            "run to run the step again"
        ),
        recoverable=True,
    )
    inst.state = InstanceState.FAILED
    inst.failure = failure
    inst.completed_at = now_stamp()
    ctl.journal.step_failed(
        attempt.path,
        attempt.node_id,
        epoch=inst.epoch,
        failure=failure,
        usage=StepUsage(tokens=None, cost_usd=None),
        attempt=inst.attempt,
        retries_exhausted=True,
    )
    release_execution_claim(attempt.claim_target, attempt.claim_holder)
    inst.claim_target = ""
    inst.claim_holder = ""
    inst.subagent_id = ""
