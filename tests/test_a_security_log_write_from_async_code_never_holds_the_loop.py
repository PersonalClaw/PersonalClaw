"""A security event written from async code is written in a worker thread, awaited
(``sel_log_async``, #5605).

The write is disk work: the log's file opened, appended to and closed, and on a home's first event
its key made with an fsync. On the event loop it held every other request for as long as the disk
took, which on a Linux runner beside a large upload was up to 0.35 s. Awaited in a thread, the
event is on disk before the caller goes on, as before, and the rows keep their chain and their
caller scope. The gateway makes the log before it serves anything (``dashboard.server``), so the
key's fsync is never a handler's.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from personalclaw.guardrails.audit import reset_current_caller, set_current_caller
from personalclaw.sel import SecurityEventLog, sel, sel_log_async


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _write(operation: str) -> None:
    sel().log_api_access(caller="test", operation=operation, outcome="success")


@pytest.mark.asyncio
async def test_the_write_runs_in_a_worker_and_is_on_disk_when_the_await_returns(home, monkeypatch):
    seen: list[str] = []
    real = SecurityEventLog.log

    def recorded(self, event):
        seen.append(threading.current_thread().name)
        return real(self, event)

    loop_thread = threading.current_thread().name
    monkeypatch.setattr(SecurityEventLog, "log", recorded)
    await sel_log_async(_write, "probe.write")
    assert seen and seen[0] != loop_thread, f"the write ran on {seen}, the loop is {loop_thread}"
    [row] = [r for r in sel().recent(10) if r.get("operation") == "probe.write"]
    assert row["outcome"] == "success"


@pytest.mark.asyncio
async def test_the_loop_answers_while_a_slow_write_waits_on_the_disk(home, monkeypatch):
    """A write the disk takes 0.2 s for holds no turn of the loop: a 10 ms tick keeps ticking."""
    real = SecurityEventLog.log

    def slow(self, event):
        time.sleep(0.2)
        return real(self, event)

    ticks: list[float] = []
    stop = asyncio.Event()

    async def tick() -> None:
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0.01)

    monkeypatch.setattr(SecurityEventLog, "log", slow)
    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0.02)
    await sel_log_async(_write, "probe.slow")
    stop.set()
    await ticker
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) >= 10, f"premise: the tick ran beside the write ({len(ticks)} ticks)"
    assert max(gaps) < 0.1, f"the loop went {max(gaps):.3f}s without a turn during the write"


@pytest.mark.asyncio
async def test_writes_at_once_keep_their_chain_and_each_its_caller_scope(home):
    """Twenty handlers writing at once: each append takes the log's lock, so every row verifies,
    and each row names the caller scope its own context bound, which the thread carries."""
    sel()  # made once, as the gateway makes it before it serves

    async def handler(n: int) -> None:
        token = set_current_caller("knowledge" if n % 2 else "inbox_triage")
        try:
            await sel_log_async(_write, f"probe.chain.{n}")
        finally:
            reset_current_caller(token)

    await asyncio.gather(*(handler(n) for n in range(20)))
    rows = {
        r["operation"]: r for r in sel().recent(50) if r["operation"].startswith("probe.chain.")
    }
    assert len(rows) == 20, sorted(rows)
    for n in range(20):
        want = "knowledge" if n % 2 else "inbox_triage"
        assert rows[f"probe.chain.{n}"]["caller_scope"] == want, rows[f"probe.chain.{n}"]
    checked, valid = sel().verify_integrity()
    assert checked >= 20 and valid == checked, (checked, valid)


@pytest.mark.asyncio
async def test_a_failed_write_reaches_the_caller(home):
    """A write that raises raises from the await, as it did when it was called on the loop."""

    def broken() -> None:
        raise OSError(28, "No space left on device")

    with pytest.raises(OSError, match="No space left"):
        await sel_log_async(broken)


def test_two_threads_building_the_log_at_once_build_it_once(home, monkeypatch):
    """Async code writes from worker threads, so the first two writes can build the log at once.
    It is built once: its key read or made once, and one lock for every row, so the chain cannot
    fork. A key that takes 0.2 s to make holds the second builder until the first is done."""
    made: list[str] = []
    real = SecurityEventLog._load_or_create_hmac_key

    def slow_key(self):
        made.append(threading.current_thread().name)
        time.sleep(0.2)
        return real(self)

    monkeypatch.setattr(SecurityEventLog, "_load_or_create_hmac_key", slow_key)
    start = threading.Barrier(2)
    built: list[SecurityEventLog] = []

    def build() -> None:
        start.wait()
        built.append(SecurityEventLog())

    threads = [threading.Thread(target=build, name=f"builder-{n}") for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(built) == 2, built
    assert len(made) == 1, f"the key was read or made {len(made)} times: {made}"
    assert built[0] is built[1] and built[0]._lock is built[1]._lock


#: The loop's default executor on a 4-CPU runner: ``min(32, CPUs + 4)`` threads.
_RUNNERS_POOL = 8

#: How long an unrelated call to a thread may wait while security-log writes are held up.
_OTHER_WORK = 2.0


@pytest.mark.asyncio
async def test_writes_held_up_on_the_disk_leave_the_loops_threads_to_other_work(home, monkeypatch):
    """🔴 Red before: each write took a thread of the loop's default executor, and the writes take
    the log's lock one at a time. On a 4-CPU runner that pool is 8 threads, and 8 writes held up
    on the disk held all 8, so an unrelated ``asyncio.to_thread`` call (a file read, an upload's
    hashing) waited for them. The writes now run on the log's own thread, one at a time as the
    lock would have them, and leave the loop's threads to everything else."""
    sel()  # made, as the gateway makes it before it serves
    pool = ThreadPoolExecutor(max_workers=_RUNNERS_POOL)
    asyncio.get_running_loop().set_default_executor(pool)
    disk = threading.Event()
    real = SecurityEventLog.log

    def held_up(self, event):
        disk.wait(10)
        return real(self, event)

    monkeypatch.setattr(SecurityEventLog, "log", held_up)
    writes = [
        asyncio.create_task(sel_log_async(_write, f"probe.held.{n}")) for n in range(_RUNNERS_POOL)
    ]
    await asyncio.sleep(0)  # each write's task starts and asks for its thread
    try:
        began = time.monotonic()
        await asyncio.wait_for(asyncio.to_thread(time.monotonic), _OTHER_WORK)
    except TimeoutError:
        pytest.fail(
            f"an unrelated to_thread call waited {time.monotonic() - began:.1f}s behind"
            f" {_RUNNERS_POOL} security-log writes held up on the disk"
        )
    finally:
        disk.set()
        await asyncio.gather(*writes)
        pool.shutdown()
    held = [r for r in sel().recent(50) if r["operation"].startswith("probe.held.")]
    assert len(held) == _RUNNERS_POOL, f"premise: every held write was written ({len(held)})"


def _held_up_until(disk: threading.Event, monkeypatch, only: str = "") -> None:
    """Each write (or only the one whose operation is *only*) waits for *disk* before it is
    made, as a write the disk is slow to take does."""
    real = SecurityEventLog.log

    def held_up(self, event):
        if not only or event.operation == only:
            disk.wait(10)
        return real(self, event)

    monkeypatch.setattr(SecurityEventLog, "log", held_up)


@pytest.mark.asyncio
async def test_writes_asked_while_one_is_held_up_are_written_in_the_order_asked(home, monkeypatch):
    """🔴 Red before: the writes waiting on a slow disk each had a thread of their own, and when
    it let go they raced for the log's lock, so the chain took them in whatever order the threads
    won. They now wait their turn on the log's one thread, in the order the handlers asked."""
    sel()
    disk = threading.Event()
    _held_up_until(disk, monkeypatch)
    asked = [f"probe.order.{n:02d}" for n in range(20)]
    writes = [asyncio.create_task(sel_log_async(_write, operation)) for operation in asked]
    await asyncio.sleep(0.05)  # every write is under way, or waiting its turn
    disk.set()
    await asyncio.gather(*writes)
    rows = reversed(sel().recent(50))
    assert [r["operation"] for r in rows if r["operation"].startswith("probe.order.")] == asked


@pytest.mark.asyncio
async def test_a_write_whose_caller_is_cancelled_while_it_waits_its_turn_is_still_made(
    home, monkeypatch
):
    """🔴 Red before: a write waiting for a thread was dropped when the handler that asked for it
    was cancelled (a client that went away), and the event it recorded was never written. The
    caller still goes on at once, and the write is made in its turn."""
    sel()
    pool = ThreadPoolExecutor(max_workers=1)  # the loop's threads all taken, as a burst takes them
    asyncio.get_running_loop().set_default_executor(pool)
    disk = threading.Event()
    _held_up_until(disk, monkeypatch, only="probe.first")
    try:
        first = asyncio.create_task(sel_log_async(_write, "probe.first"))
        await asyncio.sleep(0.05)  # the first is under way, held up on the disk
        second = asyncio.create_task(sel_log_async(_write, "probe.given-up"))
        await asyncio.sleep(0.05)  # the second waits its turn
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
    finally:
        disk.set()
    await first
    await sel_log_async(_write, "probe.after")  # in turn after the given-up one, if it is made
    pool.shutdown()
    written = [r["operation"] for r in sel().recent(10)]
    assert "probe.given-up" in written, f"the cancelled caller's write was dropped: {written}"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="a process is forked only where there is fork")
def test_a_process_forked_after_a_write_writes_on_a_thread_of_its_own(home):
    """A process forked from one that has written has none of its threads. The log's thread there
    is a new one: it inherited one it could not start, the write waited for it, and never came."""
    asyncio.run(sel_log_async(_write, "probe.before-fork"))  # the log's thread is up, and idle
    pid = os.fork()
    if pid == 0:
        try:
            asyncio.run(asyncio.wait_for(sel_log_async(_write, "probe.in-the-child"), 5))
        except BaseException:
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0, "the forked process's write never came back"
    assert "probe.in-the-child" in [r["operation"] for r in sel().recent(10)]
