"""A process a run's command started ends with the run, even one that left the command's process
group and session, as a daemon does.

Measured on a running gateway: a loop's command ran a test suite whose fixture started an embedded
database. The server detached itself (its start command forks it into a session of its own), so the
loop's Stop, which ends the command's process group, ended the test that would have stopped the
server and missed the server: it ran on, unseen, for 13 hours and 46 minutes after the Stop.

These drive a REAL native runtime whose scripted model runs a REAL bash command. The command starts
a daemon by a double fork with ``setsid`` and returns once it is up, as a server's start command
does: the daemon is in a session and a group of its own and its parent has exited, so nothing of the
command's process tree holds it. Each test then reads the OS's process table: the daemon is alive
before, and gone after, the ending that ends its run. The daemon is this test's own Python, whose
environment the OS shows on macOS as on Linux; a background job of one of macOS's own programs,
whose environment it withholds, is the group backstop's test.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_a_stopped_loop_ends_what_it_started import loop_home  # noqa: F401 - a fixture
from test_a_stopped_loop_ends_what_it_started import _NudgeService, _running_loop
from test_ended_owner_ends_its_approvals import world  # noqa: F401 - a fixture
from test_stop_means_stop import _Driver, _ScriptedModel

from personalclaw import process_facts, run_processes
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.bash_provider import BashActionProvider
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.loop import manager as loop_manager
from personalclaw.loop import store as loop_store
from personalclaw.loop.loop import LoopStatus

_TIMEOUT = 15.0
_POLL = 0.02

#: Forks, starts a session, forks again (the daemon), points its stdio at /dev/null and writes its
#: pid; the process the command ran waits for that pid before it exits.
_DAEMON = """\
import os, sys, time
pidfile = sys.argv[1]
if os.fork():
    deadline = time.monotonic() + 30
    while not os.path.exists(pidfile) and time.monotonic() < deadline:
        time.sleep(0.02)
    os._exit(0)
os.setsid()
if os.fork():
    os._exit(0)
null = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(null, fd)
with open(pidfile + ".part", "w") as fh:
    fh.write(str(os.getpid()))
os.replace(pidfile + ".part", pidfile)
time.sleep(300)
"""


# ── harness ──────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def daemons():
    """The pid files of the daemons a test starts. Whatever is still running at teardown, a
    failing test's daemon or an outsider a test kept alive on purpose, is ended: only a pid whose
    command line is this test's own daemon writing that very pid file, never another process."""
    started: list[Path] = []
    yield started
    for pidfile in started:
        try:
            pid = int(pidfile.read_text())
        except (OSError, ValueError):
            continue
        command = process_facts.command_line(pid)
        if str(pidfile.parent / "daemonize.py") in command and str(pidfile) in command:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


def _daemonizing(tmp_path: Path, daemons: list[Path], name: str = "daemon") -> tuple[str, Path]:
    script = tmp_path / "daemonize.py"
    script.write_text(_DAEMON)
    pidfile = tmp_path / f"{name}.pid"
    daemons.append(pidfile)
    words = (sys.executable, str(script), str(pidfile))
    return " ".join(shlex.quote(word) for word in words), pidfile


def _marker(pid: int) -> str | None:
    return process_facts.environment_value(pid, run_processes.RUN_VARIABLE)


def _alive(pid: int) -> bool:
    """While *pid* exists, a zombie included: a stopped run leaves no zombie either."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _until(predicate, what: str, timeout: float = _TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(_POLL)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


async def _pid_in(pidfile: Path, what: str = "the daemon's pid") -> int:
    text = await _until(lambda: pidfile.read_text().strip() if pidfile.exists() else "", what)
    pid = int(text)
    assert pid > 1 and pid != os.getpid(), f"refusing to treat {pid} as a fixture"
    return pid


def _assert_detached(daemon: int, shell: int) -> None:
    """The premise every test here rests on: the daemon is beyond the command's process group,
    its session and its tree, so the Stop's group kill cannot reach it."""
    assert _alive(daemon), "the daemon should be running before the ending"
    assert os.getpgid(daemon) != shell, "the daemon is still in its command's process group"
    assert os.getsid(daemon) != shell, "the daemon is still in its command's session"
    facts = process_facts.process(daemon)
    assert facts is not None and facts.ppid != shell, "the daemon is still the shell's child"


def _bash(command: str, cid: str = "c1") -> AgentEvent:
    return AgentEvent(
        kind=EVENT_TOOL_CALL,
        tool_call_id=cid,
        title="bash",
        tool_input=json.dumps({"command": command, "timeout": 120}),
    )


def _one_command(command: str) -> list[list[AgentEvent]]:
    """A turn that runs *command* and then answers."""
    return [
        [_bash(command), AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)],
    ]


async def _runtime(tmp_path: Path, session_key: str, turns) -> NativeAgentRuntime:
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_ScriptedModel(turns),
        tool_providers=[NativeBuiltinToolProvider(tmp_path, sandbox_mode="none")],
        cwd=tmp_path,
        session_key=session_key,
    )
    await rt.start()
    # This file is about what an ending reaches, so no approval parks the call first.
    rt.set_approval_policy("auto")
    return rt


async def _run_turn(rt: NativeAgentRuntime) -> list[AgentEvent]:
    return [event async for event in rt.stream("go")]


def _zombie_children() -> list[str]:
    """This process's children that exited and were never collected."""
    out = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,stat="], capture_output=True, text=True, check=False
    ).stdout
    me = str(os.getpid())
    return [line for line in out.splitlines() if line.split()[1:2] == [me] and "Z" in line]


def _outside(tmp_path: Path, daemons: list[Path], name: str, marker: str | None) -> int:
    """A daemon started outside any run of this test, carrying *marker* (or none)."""
    command, pidfile = _daemonizing(tmp_path, daemons, name)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    if marker is not None:
        env[run_processes.RUN_VARIABLE] = marker
    subprocess.run(["/bin/sh", "-c", command], env=env, check=True, timeout=30)
    return int(pidfile.read_text())


# ── a chat's turn ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stopping_a_turn_ends_the_daemon_its_command_started(tmp_path, daemons):
    start, pidfile = _daemonizing(tmp_path, daemons)
    shell_file = tmp_path / "shell.pid"
    rt = await _runtime(
        tmp_path,
        "dashboard:chat-a",
        [[_bash(f"echo $$ > {shlex.quote(str(shell_file))}; {start}; sleep 300")]],
    )
    async with _Driver(rt) as driver:
        daemon = await _pid_in(pidfile)
        shell = await _pid_in(shell_file, "the shell's pid")
        _assert_detached(daemon, shell)
        assert _marker(daemon), "the daemon should carry its run's marker"

        assert await rt.cancel() == "acked"

        await _until(lambda: not _alive(shell), "the shell to be reaped")
        await _until(lambda: not _alive(daemon), "the daemon to end with the stopped turn")
        await driver.finish()

    assert rt.last_stop_report()["children_reaped"] == 1
    assert _zombie_children() == [], "the stop left a child of this process uncollected"


@pytest.mark.asyncio
async def test_a_stop_answers_without_waiting_for_what_it_set_ending(
    tmp_path, daemons, monkeypatch
):
    """Measured on a gateway: a Stop that waited for the detached process to end answered after
    its turn had finished and saved its transcript, so the stop card was never resolved and read
    "stopping" for good. The Stop answers once the work it reached is stopped; the detached
    process's end follows it."""
    held = threading.Event()
    sweep = run_processes.sweep

    def held_sweep(accepts, *, grace=None):
        held.wait(timeout=_TIMEOUT)
        return sweep(accepts, grace=grace)

    monkeypatch.setattr(run_processes, "sweep", held_sweep)
    start, pidfile = _daemonizing(tmp_path, daemons)
    rt = await _runtime(tmp_path, "dashboard:chat-a", [[_bash(f"{start}; sleep 300")]])
    try:
        async with _Driver(rt) as driver:
            daemon = await _pid_in(pidfile)
            assert await asyncio.wait_for(rt.cancel(), timeout=5) == "acked"
            assert _alive(daemon), "the Stop waited for the detached process to end"
            await driver.finish()
            held.set()
            await _until(lambda: not _alive(daemon), "the daemon to end after the Stop answered")
    finally:
        held.set()


@pytest.mark.asyncio
async def test_a_turn_that_ends_ends_the_daemon_its_command_left_running(tmp_path, daemons):
    start, pidfile = _daemonizing(tmp_path, daemons)
    rt = await _runtime(tmp_path, "dashboard:chat-a", _one_command(start))
    events = await _run_turn(rt)
    assert events[-1].kind == EVENT_COMPLETE and events[-1].stop_reason == "end_turn"

    daemon = await _pid_in(pidfile)
    await _until(lambda: not _alive(daemon), "the daemon to end with its turn")
    assert _zombie_children() == []


@pytest.mark.asyncio
async def test_a_background_job_the_os_hides_ends_with_its_turn_through_its_group(
    tmp_path, monkeypatch
):
    """A background job that stays in its command's process group, of one of the system's own
    programs: on macOS the kernel withholds its environment, so the marker cannot be read and the
    group its command led is how it is found. On Linux the marker is read as well. The turn's
    sweep is held until the premise is read on the live job."""
    held = threading.Event()
    sweep = run_processes.sweep

    def held_sweep(accepts, *, grace=None):
        held.wait(timeout=_TIMEOUT)
        return sweep(accepts, grace=grace)

    monkeypatch.setattr(run_processes, "sweep", held_sweep)
    pidfile = tmp_path / "sleep.pid"
    command = f"sleep 300 > /dev/null 2>&1 & echo $! > {shlex.quote(str(pidfile))}"
    rt = await _runtime(tmp_path, "dashboard:chat-a", _one_command(command))
    sleeper = 0
    try:
        await _run_turn(rt)
        sleeper = await _pid_in(pidfile, "the background job's pid")
        assert _alive(sleeper), "the premise: the job outlived its command"
        facts = process_facts.process(sleeper)
        assert facts is not None and facts.pgid != os.getpgrp()
        if sys.platform == "darwin":
            assert _marker(sleeper) is None, "the premise: macOS shows no environment for it"
        held.set()
        await _until(lambda: not _alive(sleeper), "the background job to end with its turn")
    finally:
        held.set()
        if sleeper and _alive(sleeper) and process_facts.command_line(sleeper) == "sleep 300":
            os.kill(sleeper, signal.SIGKILL)


# ── a loop ───────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_daemon_a_loop_tasks_command_started_ends_with_the_loops_stop(
    world, loop_home, tmp_path, daemons  # noqa: F811 - the imported fixtures
):
    loop = _running_loop()
    start, pidfile = _daemonizing(tmp_path, daemons)
    rt = await _runtime(tmp_path, f"dashboard:loop-{loop.id}-t-1", _one_command(start))
    await _run_turn(rt)
    daemon = await _pid_in(pidfile)

    # The cycle's turn has ended; the loop has not, and what its cycles leave running is the
    # loop's: a server the next cycle tests stays up.
    await asyncio.sleep(0.5)
    assert _alive(daemon), "a loop's process ended with its cycle's turn"
    assert run_processes.owner_of(_marker(daemon) or "") == f"loop-{loop.id}-t-1"

    await loop_manager.stop(world.state, _NudgeService(), loop.id)

    assert loop_store.get(loop.id).status == LoopStatus.STOPPED.value
    await _until(lambda: not _alive(daemon), "the daemon to end with the loop's Stop")


@pytest.mark.asyncio
async def test_stopping_a_loops_turn_ends_what_that_turn_started(tmp_path, daemons):
    """A pause or a Stop stops the cycle in flight through the turn's Stop: what that turn's
    command started ends with it, whoever owns the turn's work."""
    start, pidfile = _daemonizing(tmp_path, daemons)
    rt = await _runtime(tmp_path, "dashboard:loop-0a1b2c3d", [[_bash(f"{start}; sleep 300")]])
    async with _Driver(rt) as driver:
        daemon = await _pid_in(pidfile)
        assert await rt.cancel() == "acked"
        await _until(lambda: not _alive(daemon), "the daemon to end with the stopped cycle")
        await driver.finish()


@pytest.mark.asyncio
async def test_a_task_workers_teardown_ends_its_own_and_leaves_the_loops(
    world, loop_home, tmp_path, daemons  # noqa: F811 - the imported fixtures
):
    loop = _running_loop()
    stage_start, stage_file = _daemonizing(tmp_path, daemons, "stage")
    task_start, task_file = _daemonizing(tmp_path, daemons, "task")
    await _run_turn(
        await _runtime(tmp_path, f"dashboard:loop-{loop.id}", _one_command(stage_start))
    )
    await _run_turn(
        await _runtime(tmp_path, f"dashboard:loop-{loop.id}-t-2", _one_command(task_start))
    )
    stage, task = await _pid_in(stage_file), await _pid_in(task_file)

    await loop_manager.teardown_task_worker(_NudgeService(), loop.id, "t-2")

    await _until(lambda: not _alive(task), "the task worker's daemon to end with its task")
    assert _alive(stage), "a task's teardown ended its loop's other work"

    await loop_manager.stop(world.state, _NudgeService(), loop.id)
    await _until(lambda: not _alive(stage), "the stage worker's daemon to end with the loop")


# ── what is not the run's is never touched ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_process_outside_the_run_is_never_touched(
    world, loop_home, tmp_path, daemons  # noqa: F811 - the imported fixtures
):
    """Detached processes that carry no marker, another run's marker of this home, and this
    run's very owner and token under another home all outlive both kinds of ending: a turn's
    (by its exact marker) and a loop's (by its owner). The run's own daemon, ended each time,
    is the positive control."""
    loop = _running_loop()
    other_home = "0" * 12 + ":" + f"loop-{loop.id}:1.2-feedfacecafebeef"
    outsiders = {
        "no marker": _outside(tmp_path, daemons, "bare", None),
        "another run": _outside(tmp_path, daemons, "other", run_processes.mark("turn")),
        "another home": _outside(tmp_path, daemons, "home", other_home),
    }

    start, pidfile = _daemonizing(tmp_path, daemons, "turn")
    await _run_turn(await _runtime(tmp_path, "dashboard:chat-a", _one_command(start)))
    await _until(lambda: not _alive(int(pidfile.read_text())), "the turn's own daemon to end")

    start, pidfile = _daemonizing(tmp_path, daemons, "loop")
    await _run_turn(await _runtime(tmp_path, f"dashboard:loop-{loop.id}", _one_command(start)))
    daemon = await _pid_in(pidfile)
    await loop_manager.stop(world.state, _NudgeService(), loop.id)
    await _until(lambda: not _alive(daemon), "the loop's own daemon to end")

    for what, pid in outsiders.items():
        assert _alive(pid), f"the process with {what} was ended"


# ── an agent CLI ─────────────────────────────────────────────────────────────────────────────

#: A stand-in agent CLI: it runs one command the way an agent's shell tool does, then waits.
_AGENT = """\
import subprocess, sys, time
subprocess.run(sys.argv[1], shell=True, check=True)
time.sleep(300)
"""


async def _agent_cli(tmp_path: Path, command: str, session_key: str):
    from personalclaw.acp.transport import AcpProcess

    stub = tmp_path / "agent_cli.py"
    stub.write_text(_AGENT)
    agent = AcpProcess(
        command=[sys.executable, str(stub), command],
        work_dir=tmp_path,
        sandbox_mode="off",
        session_key=session_key,
    )
    await agent.spawn()
    return agent


@pytest.mark.asyncio
async def test_what_an_agent_clis_command_detached_ends_when_the_cli_is_taken_down(
    tmp_path, daemons
):
    """An agent CLI runs its own commands, so no turn's marker reaches them: the CLI's process
    carries one, and what its commands detached ends when it is taken down (`close`'s order)."""
    start, pidfile = _daemonizing(tmp_path, daemons)
    agent = await _agent_cli(tmp_path, start, "dashboard:chat-a")
    try:
        daemon = await _pid_in(pidfile)
        assert run_processes.owner_of(_marker(daemon) or "") == run_processes.AGENT
        await agent.kill(force=True)
        assert _alive(daemon), "the premise: the CLI's own kill does not reach a detached daemon"
    finally:
        await agent.kill(force=True)
        agent.teardown()
    await _until(lambda: not _alive(daemon), "the daemon to end with the agent CLI")


@pytest.mark.asyncio
async def test_a_loop_workers_agent_cli_and_what_it_detached_end_with_the_loop(
    world, loop_home, tmp_path, daemons  # noqa: F811 - the imported fixtures
):
    loop = _running_loop()
    start, pidfile = _daemonizing(tmp_path, daemons)
    agent = await _agent_cli(tmp_path, start, f"dashboard:loop-{loop.id}")
    try:
        daemon = await _pid_in(pidfile)
        assert agent.is_alive(), "the premise: the loop's agent CLI is running"

        await loop_manager.stop(world.state, _NudgeService(), loop.id)

        await _until(lambda: not _alive(daemon), "the daemon to end with the loop's Stop")
        await _until(lambda: not agent.is_alive(), "the loop's agent CLI to end with the loop")
    finally:
        await agent.kill(force=True)
        agent.teardown()


# ── a command that is a run of its own ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_automations_command_ends_what_it_left_running_when_it_exits(tmp_path, daemons):
    start, pidfile = _daemonizing(tmp_path, daemons)
    result = await BashActionProvider().execute(
        {"command": start}, ActionContext(event="cron", context="", payload={})
    )
    assert result.success, result
    daemon = await _pid_in(pidfile)
    await _until(lambda: not _alive(daemon), "the daemon to end when its command exited")


def test_a_trigger_payload_cannot_name_the_run():
    from personalclaw.action_providers.bash_provider import PROTECTED_ENV_NAMES

    assert run_processes.RUN_VARIABLE in PROTECTED_ENV_NAMES


# ── the gateway's start and stop ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_at_the_gateways_start_what_no_run_holds_ends_and_a_running_loops_stays(
    loop_home, tmp_path, daemons, monkeypatch  # noqa: F811 - the imported fixture
):
    """No turn outlives the gateway process that ran it (a restart re-executes it in place, under
    the same pid), and an ended loop holds nothing; a loop still going keeps what it started, and
    so does a live gateway's turn on the same home."""
    tag = run_processes.home_tag()
    me = run_processes._runner()
    running, stopped = _running_loop(), _running_loop()
    loop_store.update_status(stopped.id, LoopStatus.STOPPED)
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        stamp = process_facts.process(live.pid)
        assert stamp is not None
        another = f"{live.pid}.{stamp.started}"
        pids = {
            "this gateway's turn": _outside(tmp_path, daemons, "t1", f"{tag}:turn:{me}-aa"),
            "a gone gateway's command": _outside(tmp_path, daemons, "t2", f"{tag}:command:1.1-bb"),
            "a stopped loop's": _outside(
                tmp_path, daemons, "l1", f"{tag}:loop-{stopped.id}:{me}-cc"
            ),
            "a running loop's": _outside(
                tmp_path, daemons, "l2", f"{tag}:loop-{running.id}:{me}-dd"
            ),
            "a live gateway's turn": _outside(tmp_path, daemons, "t3", f"{tag}:turn:{another}-ee"),
        }
        monkeypatch.setenv(run_processes.RUN_VARIABLE, f"{tag}:turn:{me}-ff")

        assert await asyncio.to_thread(run_processes.end_what_no_run_holds) == 3

        assert run_processes.RUN_VARIABLE not in os.environ, "the gateway kept a run's marker"
        for what in ("this gateway's turn", "a gone gateway's command", "a stopped loop's"):
            await _until(lambda pid=pids[what]: not _alive(pid), f"{what} process to end")
        for what in ("a running loop's", "a live gateway's turn"):
            assert _alive(pids[what]), f"{what} process was ended"
    finally:
        live.kill()
        live.wait()


# ── the marker ───────────────────────────────────────────────────────────────────────────────


def test_a_marker_names_its_home_owner_and_run():
    value = run_processes.mark("loop-0a1b2c3d-t-1")
    assert run_processes.owner_of(value) == "loop-0a1b2c3d-t-1"
    assert run_processes.owner_of(value, "0" * 12) == "", "another home's marker was read as ours"
    assert run_processes.owner_of("not a marker") == ""
    assert run_processes.mark("turn") != run_processes.mark("turn")


def test_a_loop_workers_turns_are_its_loops_and_every_other_turn_is_its_own():
    assert run_processes.turn_owner("dashboard:loop-0a1b2c3d") == "loop-0a1b2c3d"
    assert (
        run_processes.turn_owner("dashboard:loop-0a1b2c3d-t-2b9c41fe") == "loop-0a1b2c3d-t-2b9c41fe"
    )
    for key in ("dashboard:chat-a", "subagent:abc", "cron:daily", "dashboard:loop-plan-0a1b2c3d"):
        assert run_processes.turn_owner(key) == run_processes.TURN, key
    owns = run_processes.loop_owns("0a1b2c3d")
    assert owns("loop-0a1b2c3d") and owns("loop-0a1b2c3d-t-1")
    assert not owns("loop-0a1b2c3e") and not owns("turn")
    assert run_processes.loop_owns("0a1b2c3d", "t-1")("loop-0a1b2c3d-t-1")
    assert not run_processes.loop_owns("0a1b2c3d", "t-1")("loop-0a1b2c3d")


def test_the_environment_is_read_from_the_area_the_kernel_hands_over():
    """macOS's argument area: the count, the program and its padding, the arguments, then the
    environment; for a program whose environment it withholds, the area ends after the arguments."""
    area = (2).to_bytes(
        4, sys.byteorder
    ) + b"/usr/bin/x\0\0\0\0x\0-v\0A=1\0PERSONALCLAW_RUN=t:o:r\0\0"
    assert process_facts.environment_in_procargs(area, "PERSONALCLAW_RUN") == "t:o:r"
    assert process_facts.environment_in_procargs(area, "A") == "1"
    withheld = (2).to_bytes(4, sys.byteorder) + b"/bin/sleep\0\0\0sleep\0300\0"
    assert process_facts.environment_in_procargs(withheld, "PERSONALCLAW_RUN") is None
    assert process_facts.environment_in_procargs(b"", "A") is None
