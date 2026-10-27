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
import threading
import time

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
