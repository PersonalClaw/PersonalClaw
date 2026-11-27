"""A restart ends what a workflow step's last attempt left running before the step runs again.

A ``stage`` runs in a helper: an agent CLI that leads a process group and a session of its own, or
the in-process runtime, whose bash tool runs each command in a group of its own. 🔴 Before, a
gateway that died left them running, and the restart put the step back in the queue
(`run_start.requeue_lost_steps`, `stage_settlement.requeue_orphaned_stages`) beside them: the
step ran twice at once. The boot's sweeps end an agent CLI, but not a command it runs in a system
shell on macOS (its marker is not shown), nor one it started detached, nor a command the
in-process runtime runs, and a sweep that fails is not seen.

Now `workflows.left_running` ends, before the step goes back, what the step's own last attempt's
folder records: the CLI's group, each detached group below it, and each command group the
in-process runtime ran, and fails the step, naming the process, when it cannot tell or cannot end
them. Only what that folder records, once the folder is the attempt's (its claim holder), and the
CLI only while it is the process recorded and an agent CLI of this home.

Driven through a real ``RunController`` over the real lease and agent folders in a scratch home,
with real processes standing in for the helper, and only the helper manager faked.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any

import pytest
from test_a_stage_killed_before_its_claim_is_recorded_runs_again import (  # noqa: F401
    STAGE_PATH,
    _Helpers,
    _Info,
    _spec,
    home,
)

from personalclaw import process_facts, run_processes, subagent_persistence
from personalclaw.workflows import engine, leases, left_running, store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    FailureClass,
    InstanceState,
    Node,
    NodeInstance,
    RunStatus,
    WorkflowRun,
    now_stamp,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")

#: The stand-in agent CLI: it leads its own group (as `acp.transport` starts one), runs a tool
#: command in a system shell inside its group, and, with ``detached``, one in a session of its own
#: (node's ``detached: true``); it prints their pids, then waits.
CLI = """
import signal, subprocess, sys, time
if "ignore-term" in sys.argv:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
kids = [subprocess.Popen(["/bin/sh", "-c", "sleep 300; :"])]
if "detached" in sys.argv:
    kids.append(subprocess.Popen(["/bin/sh", "-c", "sleep 300; :"], start_new_session=True))
if "curl" in sys.argv:
    kids.append(subprocess.Popen(["curl", "-s", "-o", "/dev/null", "file:///dev/zero"]))
print(" ".join(str(k.pid) for k in kids), flush=True)
time.sleep(300)
"""


class _Stood:
    """The processes a test stands in for a helper with, ended when the test ends whatever it
    found."""

    def __init__(self) -> None:
        self.procs: list[subprocess.Popen[str]] = []
        self.pids: list[int] = []

    def cli(self, *flags: str, marked: bool = True) -> tuple[subprocess.Popen[str], list[int]]:
        env = dict(os.environ)
        if marked:
            env[run_processes.RUN_VARIABLE] = run_processes.mark(run_processes.AGENT)
        proc = subprocess.Popen(
            [sys.executable, "-c", CLI, *flags],
            stdout=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
        self.procs.append(proc)
        assert proc.stdout is not None
        kids = [int(p) for p in proc.stdout.readline().split()]
        self.pids += [proc.pid, *kids]
        _until(lambda: all(process_facts.process(p) is not None for p in [proc.pid, *kids]))
        return proc, kids

    def end(self) -> None:
        for pid in self.pids:
            for kill in (os.killpg, os.kill):
                try:
                    kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        for proc in self.procs:
            proc.wait(timeout=10)


@pytest.fixture
def stood():
    held = _Stood()
    yield held
    held.end()


def _until(ready, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not ready():
        assert time.monotonic() < deadline, "the stand-in never got there"
        time.sleep(0.02)


def _alive(pid: int) -> bool:
    """Whether *pid* still runs: a zombie, ended and waiting for its parent, does not."""
    return process_facts.process(pid) is not None


class _Folders(_Helpers):
    """A helper manager whose spawn makes the agent's folder, as `SubagentManager` does."""

    def __init__(self, prefix: str, **kw: Any) -> None:
        super().__init__(**kw)
        self.prefix = prefix

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"{self.prefix}{len(self.spawns) + 1}")
        subagent_persistence.create_agent_folder(info.id, task=str(kw.get("task", "")))
        self.spawns.append(kw)
        self.infos[info.id] = info
        return info


def _lost(spec: dict[str, Any] | None = None, *, recorded: bool = False) -> tuple[WorkflowRun, str]:
    """A run whose gateway died after its stage took its claim and its helper spawned; returns it
    and the lost helper's agent id. *recorded*: the controller had recorded the dispatch."""
    spec = spec or _spec()
    run = store.create(WorkflowRun(id="", workflow_name="stage-claim"))
    store.write_spec(run.id, spec)
    lost = asyncio.run(
        engine.dispatch_stage(
            Node.from_dict(spec["root"]["children"][0]),
            BindingContext(),
            subagents=_Folders("lost", settles=False),
            run_id=run.id,
            instance_path=STAGE_PATH,
        )
    )
    assert lost.state is InstanceState.RUNNING and lost.claim_holder, lost
    inst = NodeInstance(
        path=STAGE_PATH, state=InstanceState.RUNNING, attempt=1, started_at=now_stamp()
    )
    if recorded:
        inst.subagent_id = "lost1"
        inst.claim_target = engine.claim_key(run.id, STAGE_PATH)
        inst.claim_holder = lost.claim_holder
    store.write_state(run.id, {STAGE_PATH: inst})
    run.started_at = now_stamp()
    run.status = RunStatus.RUNNING
    store.save(run)
    return run, "lost1"


def _ran_by(agent_id: str, pid: int, *, started: int | None = None) -> None:
    """Record in the folder that the agent runs in process *pid*, as `record_pid` does."""
    subagent_persistence.record_pid(agent_id, pid)
    if started is not None:
        subagent_persistence.update_state(agent_id, pid_started=started)


def _restart(run: WorkflowRun) -> tuple[RunStatus, NodeInstance, _Folders]:
    helpers = _Folders("new")
    run = store.get(run.id)
    controller = RunController(
        run, store.read_spec(run.id), services=EngineServices(subagents=helpers)
    )
    status = asyncio.run(controller.run_to_completion(timeout=20.0))
    return status, controller.instances[STAGE_PATH], helpers


@pytest.mark.parametrize("recorded", [False, True], ids=["unrecorded", "recorded"])
def test_a_restart_ends_the_cli_and_its_shell_commands_before_the_step_runs_again(stood, recorded):
    """🔴 R2, the coordinator's ask 2, and N2. Before: the CLI's shell command (unseen on macOS)
    and one it started detached ran on, beside the step's new attempt."""
    run, agent = _lost(recorded=recorded)
    cli, kids = stood.cli("detached")
    _ran_by(agent, cli.pid)
    status, inst, helpers = _restart(run)
    cli.wait(timeout=10)
    assert not any(_alive(pid) for pid in kids), [p for p in kids if _alive(p)]
    assert len(helpers.spawns) == 1, "the step ran again, once"
    assert inst.state is InstanceState.DONE and status is RunStatus.COMPLETE, inst.failure


@pytest.mark.skipif(shutil.which("curl") is None, reason="no curl here")
def test_a_restart_ends_a_curl_the_cli_runs(stood):
    run, agent = _lost()
    cli, kids = stood.cli("curl")
    _ran_by(agent, cli.pid)
    _restart(run)
    assert not any(_alive(pid) for pid in kids)


def test_a_restart_ends_a_command_the_in_process_runtime_runs(tmp_path):
    """🔴 N1. A native stage has no CLI: its bash tool runs each command in a session of its own,
    kept in the agent's folder while it runs (`subagent_persistence.tool_group`)."""
    from personalclaw.agents.native import builtin_tools as bt

    run, agent = _lost()
    tools = bt.NativeBuiltinToolProvider(
        cwd=tmp_path, categories=bt.PLATFORM_CATEGORIES, session_key=f"subagent:{agent}"
    )
    done: list[Any] = []
    worker = threading.Thread(
        target=lambda: done.append(asyncio.run(tools._t_bash({"command": "sleep 300"}))),
        daemon=True,
    )
    worker.start()
    _until(lambda: (subagent_persistence.read_state(agent) or {}).get("tool_groups"))
    [[pid, _started]] = subagent_persistence.read_state(agent)["tool_groups"]
    assert _alive(pid), "vacuity: the command runs"
    status, inst, _helpers = _restart(run)
    worker.join(timeout=20)
    assert not _alive(pid) and done, "the command ended, and the tool returned"
    assert inst.state is InstanceState.DONE and status is RunStatus.COMPLETE
    assert subagent_persistence.read_state(agent)["tool_groups"] == [], "forgotten once ended"


def _a_group(stood: _Stood) -> tuple[int, int]:
    """A stand-in command leading a group of its own, as the bash tool's are: (pid, start mark)."""
    proc = subprocess.Popen(["/bin/sh", "-c", "sleep 300; :"], start_new_session=True, text=True)
    stood.procs.append(proc)
    stood.pids.append(proc.pid)
    _until(lambda: process_facts.process(proc.pid) is not None)
    found = process_facts.process(proc.pid)
    assert found is not None
    return proc.pid, found.started


def test_a_command_started_before_the_attempt_is_not_its_own(stood):
    """CORE's hardening: a shell carries no marker to read on macOS, so a command group was
    signalled on its start mark alone. One that started before the attempt's agent (its folder's
    ``started``) is no command of the attempt's, so nothing is signalled and the step runs again."""
    pid, started = _a_group(stood)
    found = process_facts.process(pid)
    began = process_facts.started_at(found) if found is not None else None
    assert began is not None, "vacuity: its start can be read"
    # The attempt's agent starts once the wall clock is past the command's start by more than the
    # slack the start marks' counts allow.
    _until(lambda: time.time() > began + left_running._START_SLACK_SECS + 0.05, timeout=5.0)
    run, agent = _lost()
    subagent_persistence.update_state(agent, **{subagent_persistence.TOOL_GROUPS: [[pid, started]]})
    status, inst, helpers = _restart(run)
    assert _alive(pid), "not signalled"
    assert len(helpers.spawns) == 1 and status is RunStatus.COMPLETE, inst.failure


def test_this_gateways_own_group_is_never_one_to_end(stood, monkeypatch):
    """A folder naming the gateway's own group names nothing of the attempt's, as
    `_read_leader`'s own reading already leaves it out."""
    run, agent = _lost()
    pid, started = _a_group(stood)
    monkeypatch.setattr(left_running.os, "getpgrp", lambda: pid)
    subagent_persistence.update_state(agent, **{subagent_persistence.TOOL_GROUPS: [[pid, started]]})
    status, inst, helpers = _restart(run)
    assert _alive(pid), "not signalled"
    assert len(helpers.spawns) == 1 and status is RunStatus.COMPLETE, inst.failure


def test_a_command_started_inside_the_attempt_is_ended(stood):
    """The control: the same command started after the attempt's agent is its own, and ended."""
    run, agent = _lost()
    pid, started = _a_group(stood)
    subagent_persistence.update_state(agent, **{subagent_persistence.TOOL_GROUPS: [[pid, started]]})
    status, _inst, helpers = _restart(run)
    _until(lambda: not _alive(pid))
    assert len(helpers.spawns) == 1 and status is RunStatus.COMPLETE


def test_a_cli_whose_facts_cannot_be_read_fails_the_step_naming_it(stood, monkeypatch):
    """Fail closed: a process of this user's the folder records, whose facts cannot be read, is
    not signalled, and the step is not run again beside it."""
    run, agent = _lost()
    cli, _kids = stood.cli()
    _ran_by(agent, cli.pid)
    real = process_facts.processes
    monkeypatch.setattr(
        process_facts,
        "processes",
        lambda: {pid: p for pid, p in real().items() if pid != cli.pid},
    )
    status, inst, helpers = _restart(run)
    assert _alive(cli.pid), "not signalled"
    assert not helpers.spawns, "not run again"
    assert inst.state is InstanceState.FAILED and status is RunStatus.FAILED
    assert inst.failure.failure_class is FailureClass.USER
    assert f"process {cli.pid}" in inst.failure.cause_plain, inst.failure.cause_plain
    assert leases.read_claim(engine.claim_key(run.id, STAGE_PATH)) is None, "her retry can run"


def test_a_process_not_an_agent_cli_of_this_home_is_not_signalled(stood):
    """N3, the coordinator's ask 1: a folder that names a process with its start mark but no
    agent CLI's marker aims at nothing of PersonalClaw's, so nothing is signalled, and the step
    fails rather than run beside what may be its helper."""
    run, agent = _lost()
    cli, kids = stood.cli(marked=False)
    _ran_by(agent, cli.pid)
    _status, inst, helpers = _restart(run)
    assert _alive(cli.pid) and all(_alive(p) for p in kids)
    assert inst.state is InstanceState.FAILED and not helpers.spawns


def test_a_folder_another_attempt_holds_is_not_read_as_this_ones(stood):
    """The coordinator's ask 1: only the folder naming the claim being given back is the
    attempt's. A folder naming another holder is left alone, and the step runs again as before."""
    run, _agent = _lost()
    cli, kids = stood.cli()
    subagent_persistence.create_agent_folder("a-other1")
    subagent_persistence.tie_to_claim("a-other1", "workflow:someone-else:work#x")
    _ran_by("a-other1", cli.pid)
    subagent_persistence.update_state("lost1", **{subagent_persistence.CLAIM_HOLDER: "gone"})
    status, inst, helpers = _restart(run)
    assert _alive(cli.pid) and all(_alive(p) for p in kids)
    assert len(helpers.spawns) == 1 and inst.state is InstanceState.DONE


def test_a_pid_given_to_another_program_is_not_signalled(stood):
    """The control: the folder's start mark is not the process's now, so the agent is gone."""
    run, agent = _lost()
    cli, _kids = stood.cli()
    found = process_facts.process(cli.pid)
    assert found is not None
    _ran_by(agent, cli.pid, started=found.started - 1)
    status, inst, helpers = _restart(run)
    assert _alive(cli.pid)
    assert len(helpers.spawns) == 1 and status is RunStatus.COMPLETE


def test_an_attempt_that_left_nothing_runs_again_as_before():
    """The control: e15a35b18's case, a claim with a folder whose helper never started."""
    run, _agent = _lost()
    status, inst, helpers = _restart(run)
    assert len(helpers.spawns) == 1 and inst.state is InstanceState.DONE
    assert status is RunStatus.COMPLETE


def test_a_group_still_there_after_the_grace_fails_the_step(stood, monkeypatch):
    run, agent = _lost()
    cli, _kids = stood.cli()
    _ran_by(agent, cli.pid)
    monkeypatch.setattr(
        run_processes, "end_groups", lambda groups, **kw: [pgid for pgid, _m in groups]
    )
    _status, inst, helpers = _restart(run)
    assert inst.state is InstanceState.FAILED and not helpers.spawns
    assert f"process group {cli.pid}" in inst.failure.cause_plain


def test_every_step_a_run_left_is_ended_within_one_grace(stood, monkeypatch):
    """N5: a run with two steps left running waits one grace, not one for each."""
    monkeypatch.setattr(run_processes, "GRACE_SECS", 1.0)
    stage = {"kind": "stage", "config": {"prompt": "do the thing"}}
    spec = {
        "name": "stage-claim",
        "root": {
            "kind": "parallel",
            "id": "root",
            "children": [{**stage, "id": "a"}, {**stage, "id": "b"}],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name="stage-claim"))
    store.write_spec(run.id, spec)
    helpers = _Folders("lost", settles=False)
    paths = ["root.children[0]", "root.children[1]"]
    states = {}
    for path, child in zip(paths, spec["root"]["children"], strict=True):
        asyncio.run(
            engine.dispatch_stage(
                Node.from_dict(child),
                BindingContext(),
                subagents=helpers,
                run_id=run.id,
                instance_path=path,
            )
        )
        states[path] = NodeInstance(
            path=path, state=InstanceState.RUNNING, attempt=1, started_at=now_stamp()
        )
    store.write_state(run.id, states)
    for agent in ("lost1", "lost2"):
        cli, _kids = stood.cli("ignore-term")
        _ran_by(agent, cli.pid)
    ctl = RunController(store.get(run.id), spec, services=EngineServices(subagents=_Folders("n")))
    began = time.monotonic()
    assert asyncio.run(left_running.end_left_running(ctl)) == []
    took = time.monotonic() - began
    assert 1.0 <= took < 1.9, f"{took:.2f}s: one grace for both, not one each"


def test_the_holder_is_in_the_folder_before_the_agents_task_first_runs():
    """The ordering pin: no await stands between the spawn and the holder's write, so a folder
    no holder names was no process of that attempt's."""
    seen: list[Any] = []

    class _Starts(_Folders):
        def spawn(self, **kw: Any) -> _Info:
            info = super().spawn(**kw)

            async def first_step() -> None:
                seen.append((subagent_persistence.read_state(info.id) or {}).get("claim_holder"))

            asyncio.get_running_loop().create_task(first_step())
            return info

    async def dispatch() -> Any:
        result = await engine.dispatch_stage(
            Node.from_dict(_spec()["root"]["children"][0]),
            BindingContext(),
            subagents=_Starts("t", settles=False),
            run_id="r1",
            instance_path=STAGE_PATH,
        )
        await asyncio.sleep(0)
        return result

    result = asyncio.run(dispatch())
    assert seen == [result.claim_holder], seen


def test_a_spawn_that_waits_gets_its_holder_when_its_folder_is_made():
    """A spawn that waits for a slot or her Allow makes its folder only when it starts."""
    subagent_persistence.tie_to_claim("waits1", "workflow:r:work#1")
    assert subagent_persistence.read_state("waits1") is None
    subagent_persistence.create_agent_folder("waits1")
    assert subagent_persistence.read_state("waits1")["claim_holder"] == "workflow:r:work#1"


def test_a_zombie_the_folder_names_is_an_ended_process(stood):
    """N4: a process that ended and waits for its parent to collect it is gone, not unread."""
    run, agent = _lost()
    zombie = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    _until(lambda: process_facts.process(zombie.pid) is None)
    _ran_by(agent, zombie.pid, started=1)
    try:
        status, _inst, helpers = _restart(run)
        assert len(helpers.spawns) == 1 and status is RunStatus.COMPLETE
    finally:
        zombie.wait(timeout=5)


# ── what is the attempt's own (CORE's M4–M14 on add219c14) ─────────────────────────────────────


def test_a_command_group_whose_pid_is_now_another_process_is_not_signalled(stood):
    """M4: a recorded ``[pid, started]`` whose pid the system has given another process (another
    start mark) names nothing of the attempt's, even one that started inside it."""
    run, agent = _lost()
    pid, started = _a_group(stood)
    subagent_persistence.update_state(
        agent, **{subagent_persistence.TOOL_GROUPS: [[pid, started - 1]]}
    )
    status, inst, helpers = _restart(run)
    assert _alive(pid), "not signalled"
    assert len(helpers.spawns) == 1 and status is RunStatus.COMPLETE, inst.failure


def test_a_recorded_attempts_folder_naming_another_holder_signals_nothing(stood):
    """M5: the attempt the run recorded, whose folder names another claim holder than the one the
    run recorded, is not read as its: nothing of it is signalled, and while the agent it names may
    still run, the step fails rather than run beside it."""
    run, agent = _lost(recorded=True)
    cli, kids = stood.cli()
    _ran_by(agent, cli.pid)
    other = "workflow:someone-else:work#x"
    subagent_persistence.update_state(agent, **{subagent_persistence.CLAIM_HOLDER: other})
    _status, inst, helpers = _restart(run)
    assert _alive(cli.pid) and all(_alive(p) for p in kids), "not signalled"
    assert inst.state is InstanceState.FAILED and not helpers.spawns, "unread, so not run again"
    assert f"process {cli.pid}" in inst.failure.cause_plain, inst.failure.cause_plain


def test_a_group_holds_only_this_users_members_started_inside_the_attempt(stood):
    """M6: a group's members are this user's processes that started no earlier than the attempt,
    so a number the system reused for an older group, or another user's process in it, is never
    one to signal: a group with no such member is not signalled at all."""
    pid, started = _a_group(stood)
    me, made = os.getuid(), process_facts.Process
    older, theirs = pid + 100_000, pid + 200_000
    table = {
        pid: made(pid=pid, ppid=1, pgid=pid, uid=me, started=started),
        older: made(pid=older, ppid=1, pgid=pid, uid=me, started=started - 5),
        theirs: made(pid=theirs, ppid=1, pgid=pid, uid=me + 1, started=started + 5),
    }
    assert left_running._group(pid, started, table) == (pid, frozenset({(pid, started)}))
    reused = left_running._group(pid, started + 1, {pid: table[pid]})
    assert reused == (pid, frozenset()), "an older group: no member of the attempt's"
    assert run_processes.end_groups([reused]) == []
    assert _alive(pid), "not signalled"


def test_a_group_that_outlives_every_signal_fails_the_step_naming_it(stood, monkeypatch):
    """M7, through the real `run_processes.end_groups`: a group it signals that is still there
    once the grace and the kernel's settle have passed comes back still there, and the step fails
    naming it, rather than run beside it. A group no signal ends is stood in for by signals that
    reach nothing."""
    monkeypatch.setattr(run_processes, "GRACE_SECS", 0.2)
    monkeypatch.setattr(run_processes, "_SETTLE_SECS", 0.2)
    monkeypatch.setattr(run_processes, "_end_these", lambda *_a, **_k: 0)
    run, agent = _lost()
    cli, _kids = stood.cli()
    _ran_by(agent, cli.pid)
    _status, inst, helpers = _restart(run)
    assert _alive(cli.pid), "vacuity: nothing ended it"
    assert inst.state is InstanceState.FAILED and not helpers.spawns
    assert f"process group {cli.pid}" in inst.failure.cause_plain, inst.failure.cause_plain


def test_a_claim_another_node_or_run_holds_is_not_read_as_this_attempts(stood):
    """M9: an attempt no controller recorded is found by the claim it took, and only a claim held
    under this run's and this node's own key is its: one another node or run holds names a folder
    that is not the attempt's, so nothing it records is read or signalled."""
    from personalclaw.workflows import ownership

    run, _agent = _lost()
    target = engine.claim_key(run.id, STAGE_PATH)
    held = leases.read_claim(target)
    assert held is not None
    leases.release_claim(target, held.holder)
    other = f"{ownership.owned_key('another-run', 'work')}#1"
    assert leases.acquire_claim(target, other)[0] is not None
    cli, kids = stood.cli()
    subagent_persistence.create_agent_folder("a-other2")
    subagent_persistence.tie_to_claim("a-other2", other)
    _ran_by("a-other2", cli.pid)
    assert subagent_persistence.holding(other) is not None, "vacuity: its folder names it"
    ctl = RunController(
        store.get(run.id), store.read_spec(run.id), services=EngineServices(subagents=_Folders("n"))
    )
    assert left_running.left_by(ctl) == []
    assert _alive(cli.pid) and all(_alive(p) for p in kids)


def test_a_command_group_whose_start_cannot_be_read_fails_the_step_unsignalled(stood, monkeypatch):
    """M14: a recorded command group whose start time cannot be read cannot be told from one the
    attempt did not start, so it is not signalled, and the step fails, naming it, rather than run
    beside what may be its command."""
    run, agent = _lost()
    pid, started = _a_group(stood)
    subagent_persistence.update_state(agent, **{subagent_persistence.TOOL_GROUPS: [[pid, started]]})
    monkeypatch.setattr(process_facts, "started_at", lambda _found: None)
    _status, inst, helpers = _restart(run)
    assert _alive(pid), "not signalled"
    assert inst.state is InstanceState.FAILED and not helpers.spawns
    assert f"process {pid}" in inst.failure.cause_plain, inst.failure.cause_plain
