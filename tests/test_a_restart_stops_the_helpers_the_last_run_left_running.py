"""A restart stops the helpers the last run left running, on macOS as on Linux, and its notice
says only what it did.

The start-up pass kills a leftover helper only when it can tell the process is still the one the
helper ran in, not another the pid was given to since. That check read ``/proc/<pid>``'s creation
time, and macOS has no ``/proc``, so there every leftover was judged someone else's and left
running, while the notice said the restart had stopped it. These cases run the real check on a
real child process, on whichever platform runs them: nothing here patches it out.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from personalclaw import process_facts
from personalclaw import subagent_orphans as orphans
from personalclaw.subagent_persistence import create_agent_folder, record_pid, update_state

pytestmark = pytest.mark.skipif(
    process_facts.process(os.getpid()) is None, reason="this host's process facts can't be read"
)


@pytest.fixture
def agent_root(tmp_path, monkeypatch):
    """Point the agents' folders at a temp directory."""
    monkeypatch.setattr("personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def child():
    """A helper's process: a child that would run for a minute if nothing stopped it."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def _stopped(proc: subprocess.Popen, within: float = 5.0) -> bool:
    try:
        proc.wait(timeout=within)
    except subprocess.TimeoutExpired:
        return False
    return True


def _left(agent_id: str, pid: int) -> None:
    create_agent_folder(agent_id, task=f"the task of {agent_id}")
    record_pid(agent_id, pid)


@pytest.mark.asyncio
async def test_a_helper_still_running_is_stopped(agent_root, child):
    """🔴 Red before, on macOS: the check judged every leftover someone else's, so the child ran
    on, and the notice still counted it stopped."""
    _left("left1", child.pid)
    (settled,) = await orphans.reconcile_orphans(())
    assert _stopped(child), "the helper the last run left is still running"
    assert settled.process == orphans.STOPPED


@pytest.mark.asyncio
async def test_a_pid_now_running_another_process_is_left_alone(agent_root, child):
    """The control: the pid is alive, but not the process the helper ran in (its start mark
    differs), as when the system gave the pid to another program. A fix that stops any live pid
    fails here."""
    _left("reused1", child.pid)
    recorded = process_facts.process(child.pid)
    assert recorded is not None
    update_state("reused1", pid_started=recorded.started + 1)
    (settled,) = await orphans.reconcile_orphans(())
    assert not _stopped(child, within=0.5), "a process that was never the helper's was stopped"
    assert settled.process == orphans.GONE


@pytest.mark.asyncio
async def test_a_folder_from_before_the_start_mark_is_judged_by_when_its_process_started(
    agent_root, child
):
    """A folder an older build wrote has no start mark, only when the pid was recorded. A process
    that started before then is the helper's and is stopped; one that started after is not."""
    create_agent_folder("old1", task="t")
    update_state("old1", pid=child.pid, pid_recorded_at=time.time() - 3600)
    (settled,) = await orphans.reconcile_orphans(())
    assert not _stopped(child, within=0.5), "a process that started after the record was stopped"
    assert settled.process == orphans.GONE

    create_agent_folder("old2", task="t")
    update_state("old2", pid=child.pid, pid_recorded_at=time.time())
    (settled,) = await orphans.reconcile_orphans(())
    assert _stopped(child)
    assert settled.process == orphans.STOPPED


def test_the_start_mark_reads_the_same_from_another_process(child, tmp_path):
    """The mark outlives the run that recorded it: a second process reads the same one for the
    same child, so it is the kernel's count and never a hash salted per process."""
    here = process_facts.process(child.pid)
    assert here is not None
    src = Path(process_facts.__file__).parents[1]
    there = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from personalclaw import process_facts; "
            "print(process_facts.process(int(sys.argv[1])).started)",
            str(child.pid),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        # A home of its own, as the real-home guard asks of every PersonalClaw child a test starts.
        env={
            **{k: v for k, v in os.environ.items() if k != "PERSONALCLAW_HOME"},
            "HOME": str(tmp_path),
            "PYTHONPATH": str(src),
        },
    )
    assert int(there.stdout) == here.started


def test_a_process_start_reads_as_a_time_it_could_have_started(child):
    found = process_facts.process(child.pid)
    assert found is not None
    started = process_facts.started_at(found)
    assert started is not None
    assert time.time() - 120 < started <= time.time() + 2


@pytest.mark.asyncio
async def test_a_helper_that_had_ended_is_named_as_ended(agent_root):
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait(timeout=30)
    _left("ended1", done.pid)
    (settled,) = await orphans.reconcile_orphans(())
    assert settled.process == orphans.GONE


class _Notes:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def notify(self, kind, title, body, **_kw):
        self.sent.append((kind, title, body))


def test_the_notice_says_which_it_stopped_and_which_had_ended():
    """🔴 Red before: every settled helper read as stopped."""
    notes = _Notes()
    orphans.announce_orphans(
        notes,
        [
            orphans.Orphan("a1", "Research the venues", process=orphans.STOPPED),
            orphans.Orphan("b2", "Summarize the thread", "/tmp/b2/result.txt"),
            orphans.Orphan("c3", "Draft the note", process=orphans.UNCHECKED, pid=4242),
        ],
    )
    ((_kind, title, body),) = notes.sent
    assert title == (
        "A restart stopped a background agent, found one that had already ended and left one"
        " running that it could not check"
    )
    assert body.split("\n\n") == [
        "a1 — Research the venues: stopped; it saved no result.",
        "b2 — Summarize the thread: it had already ended; its result so far is saved at"
        " /tmp/b2/result.txt.",
        "c3 — Draft the note: still running as process 4242, which PersonalClaw could not check"
        " was its own; it saved no result.",
    ]


def test_a_linux_start_is_dated_from_when_the_host_booted(tmp_path, monkeypatch):
    """Linux counts a start in clock ticks since boot: ``/proc/stat``'s ``btime`` dates it. Read
    from a stand-in ``/proc``, so the Linux arithmetic runs on any host."""
    proc = tmp_path / "proc"
    (proc / "4242").mkdir(parents=True)
    (proc / "stat").write_text("cpu  1 2 3\nbtime 1700000000\nprocesses 9\n")
    ticks = os.sysconf("SC_CLK_TCK")
    fields = ["S", "1", "4242"] + ["0"] * 16 + [str(30 * ticks)]
    (proc / "4242" / "stat").write_text(f"4242 (sleep 60) {' '.join(fields)} 0 0\n")
    monkeypatch.setattr(process_facts, "PROC", proc)
    found = process_facts.process(4242)
    assert found is not None and found.started == 30 * ticks
    assert process_facts.started_at(found) == 1700000030
