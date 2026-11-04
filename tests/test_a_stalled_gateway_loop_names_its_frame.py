"""A gateway whose event loop stops answering names the frame it is stuck in (``loop_watch``).

#5638's browser shard hung: from one point on, no request the specs made got a response, and
nothing said where the gateway's loop had stopped. The browser harness now arms a watch
(``PERSONALCLAW_LOOP_STALL_SECONDS``), which appends every thread's stack to gateway.log while the
loop does not answer.

The harness gateway has gone on losing runs that way since, with a gateway.log of 0 bytes: the
watch never fired. So it now names the two stalls the loop's answer cannot show, a worker pool that
runs no job and a stall that holds the GIL, and answers a signal with every thread's stack. These
tests re-arm faulthandler's one watchdog timer, so pytest's own (``faulthandler_timeout``) is off
for the rest of each.
"""

from __future__ import annotations

import asyncio
import ctypes
import faulthandler
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from personalclaw import cli_server, loop_watch

_MARK = "The event loop has not answered for"


@pytest.fixture
def log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A file handler on the root logger, as gateway.log's is, taken off after."""
    path = tmp_path / "gateway.log"
    handler = logging.FileHandler(path)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    monkeypatch.setattr(loop_watch, "ASK_EVERY", 0.05)
    yield path
    root.removeHandler(handler)
    handler.close()


def _run_watched(
    seconds: str, monkeypatch: pytest.MonkeyPatch, body: Callable[[], Awaitable[None]]
) -> None:
    """Run *body* on a loop of its own, watched with a *seconds* limit, and wait for the watch to
    end with the loop."""
    monkeypatch.setenv(loop_watch.STALL_ENV, seconds)
    watches: list[threading.Thread | None] = []

    async def main() -> None:
        watches.append(loop_watch.arm_from_env())
        await body()

    asyncio.run(main())
    watch = watches[0]
    assert watch is not None
    watch.join(timeout=5)
    assert not watch.is_alive(), "the watch outlived its loop"


def _a_frame_that_holds_the_loop(seconds: float) -> None:
    time.sleep(seconds)


def test_a_loop_that_stops_answering_names_the_frame_it_is_stuck_in(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def body() -> None:
        _a_frame_that_holds_the_loop(1.0)

    _run_watched("0.3", monkeypatch, body)
    text = log_file.read_text(encoding="utf-8")
    assert f"ERROR personalclaw.loop_watch: {_MARK}" in text
    # The loop's thread comes first, by name, and its stack ends in the frame that holds it.
    loops = text.split(_MARK, 1)[1].split("\nThread ")[1]
    assert loops.startswith("'MainThread' (0x"), loops
    assert "the event loop's:" in loops.splitlines()[0]
    assert "in _a_frame_that_holds_the_loop" in loops
    assert "The event loop answered after" in text


def test_the_dump_is_written_while_the_stuck_loop_holds_the_log_files_lock(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = next(
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler) and h.baseFilename == str(log_file)
    )
    seen: list[str] = []

    async def body() -> None:
        handler.acquire()
        try:
            _a_frame_that_holds_the_loop(1.0)
            seen.append(log_file.read_text(encoding="utf-8"))
        finally:
            handler.release()

    _run_watched("0.3", monkeypatch, body)
    assert _MARK in seen[0], "the dump waited for the lock the stuck loop held"


def test_a_loop_that_keeps_answering_writes_nothing(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def body() -> None:
        for _ in range(20):
            await asyncio.sleep(0.1)

    _run_watched("1", monkeypatch, body)
    assert log_file.read_text(encoding="utf-8") == ""


def _a_worker_that_waits(release: threading.Event) -> None:
    release.wait(5)


def test_a_worker_pool_that_runs_no_job_is_named_while_the_loop_answers(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A handler that waits on ``asyncio.to_thread`` waits for a free worker. With every worker
    stuck, every such request goes unanswered while the loop itself answers, and the loop's watch
    alone wrote nothing: the harness gateway's lost runs left a gateway.log of 0 bytes."""

    async def body() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        release = threading.Event()
        busy = loop.run_in_executor(None, _a_worker_that_waits, release)
        for _ in range(10):  # the loop keeps answering while its one worker is held
            await asyncio.sleep(0.1)
        release.set()
        await busy

    _run_watched("0.3", monkeypatch, body)
    text = log_file.read_text(encoding="utf-8")
    assert _MARK not in text, "the loop answered, so the loop's watch has nothing to say"
    assert "The worker pool has not run a job for" in text
    assert "in _a_worker_that_waits" in text, "the dump names the frame the worker waits in"
    assert "The worker pool ran a job after" in text


def _a_frame_that_holds_the_gil(seconds: float) -> None:
    # A call through PyDLL keeps the GIL for its whole length, as a stuck C call does.
    ctypes.PyDLL(None).usleep(int(seconds * 1_000_000))


@pytest.mark.skipif(sys.platform == "win32", reason="no usleep in the process's own symbols")
def test_a_stall_that_holds_the_gil_is_named_by_the_watchs_own_watchdog(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stall that holds the GIL stops the watch's thread too. faulthandler's watchdog is a C
    thread that needs no GIL, re-armed by the watch at every slice, so it writes every thread's
    stack only when the watch itself is held."""

    async def body() -> None:
        _a_frame_that_holds_the_gil(1.5)

    _run_watched("0.3", monkeypatch, body)
    text = log_file.read_text(encoding="utf-8")
    assert "Timeout (" in text, "faulthandler's watchdog wrote no dump"
    assert "in _a_frame_that_holds_the_gil" in text


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="no SIGUSR1 here")
def test_the_dump_signal_writes_every_threads_stack_and_is_let_go_with_the_loop(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whoever holds a watched gateway's pid (the harness, when its shell guard finds the gateway
    unanswering) can ask for every thread's stack, whatever holds the gateway."""
    dump = getattr(loop_watch, "DUMP_SIGNAL", None)
    # Asked before anything is sent: a signal no handler takes would end this process.
    assert dump == signal.SIGUSR1, "the watch names no signal that asks for every thread's stack"

    async def body() -> None:
        os.kill(os.getpid(), dump)
        await asyncio.sleep(0.3)

    _run_watched("5", monkeypatch, body)
    text = log_file.read_text(encoding="utf-8")
    assert "(most recent call first)" in text, "the signal wrote no stack"
    assert " in body" in text, "the stack names the frame the loop was in"
    assert faulthandler.unregister(dump) is False, "the signal outlived the loop"


_A_SECRET = "fake-value-no-dump-may-hold-0123456789"


def _a_worker_that_holds_a_secret(release: threading.Event) -> None:
    secret = _A_SECRET[::-1][::-1]  # a local, as a request's data would be
    assert secret
    release.wait(5)


def _a_frame_that_holds_the_gil_and_a_secret(seconds: float) -> None:
    secret = _A_SECRET[::-1][::-1]
    assert secret
    ctypes.PyDLL(None).usleep(int(seconds * 1_000_000))


@pytest.mark.skipif(sys.platform == "win32", reason="no usleep in the process's own symbols")
def test_every_dump_names_frames_and_never_a_value(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pool's dump, the watchdog's and the signal's name files, lines and functions only: a
    value a frame holds (a request's data, a credential) is never written."""

    async def body() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        release = threading.Event()
        busy = loop.run_in_executor(None, _a_worker_that_holds_a_secret, release)
        for _ in range(8):
            await asyncio.sleep(0.1)
        if loop_watch.DUMP_SIGNAL is not None:
            os.kill(os.getpid(), loop_watch.DUMP_SIGNAL)
        _a_frame_that_holds_the_gil_and_a_secret(1.0)
        release.set()
        await busy

    _run_watched("0.3", monkeypatch, body)
    text = log_file.read_text(encoding="utf-8")
    assert "in _a_worker_that_holds_a_secret" in text, "the pool's dump names the frame"
    assert "in _a_frame_that_holds_the_gil_and_a_secret" in text, "the watchdog's names it"
    assert _A_SECRET not in text, "a dump wrote a value a frame held"


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="no SIGUSR1 here")
def test_the_watch_lets_go_of_everything_when_the_loop_closes(
    log_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The signal goes back to no handler, the watchdog's timer is cancelled after its last arming,
    and the file faulthandler writes is closed."""
    calls: list[str] = []
    sinks: list[object] = []
    real_arm, real_cancel = (
        faulthandler.dump_traceback_later,
        faulthandler.cancel_dump_traceback_later,
    )
    real_sink = loop_watch._faulthandler_sink

    def arm(*args: object, **kwargs: object) -> None:
        calls.append("arm")
        real_arm(*args, **kwargs)

    def cancel() -> None:
        calls.append("cancel")
        real_cancel()

    def sink() -> object:
        sinks.append(real_sink())
        return sinks[-1]

    monkeypatch.setattr(loop_watch.faulthandler, "dump_traceback_later", arm)
    monkeypatch.setattr(loop_watch.faulthandler, "cancel_dump_traceback_later", cancel)
    monkeypatch.setattr(loop_watch, "_faulthandler_sink", sink)

    async def body() -> None:
        await asyncio.sleep(0.3)

    _run_watched("5", monkeypatch, body)
    assert "arm" in calls, "the watchdog was never armed"
    assert calls[-1] == "cancel", f"the watchdog was left armed: {calls[-3:]}"
    assert faulthandler.unregister(signal.SIGUSR1) is False, "the signal outlived the loop"
    assert sinks and getattr(
        sinks[0], "closed", False
    ), "the file faulthandler writes was left open"


@pytest.mark.parametrize("value", [None, "", "soon", "0"])
def test_an_unarmed_gateway_starts_no_watch(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """Without the harness's arming variable (or with a value that is no number of seconds) the
    gateway takes no signal, arms no watchdog and hands its worker pool nothing: nothing runs."""
    if value is None:
        monkeypatch.delenv(loop_watch.STALL_ENV, raising=False)
    else:
        monkeypatch.setenv(loop_watch.STALL_ENV, value)
    done: list[str] = []
    for name in ("register", "dump_traceback_later", "enable"):
        monkeypatch.setattr(loop_watch.faulthandler, name, lambda *a, _n=name, **k: done.append(_n))
    before = {t.name for t in threading.enumerate()}

    async def main() -> threading.Thread | None:
        watch = loop_watch.arm_from_env()
        await asyncio.sleep(0.1)
        return watch

    assert asyncio.run(main()) is None
    assert done == [], f"an unarmed gateway called faulthandler: {done}"
    assert "loop-stall-watch" not in {t.name for t in threading.enumerate()} - before
    if hasattr(signal, "SIGUSR1"):
        assert signal.getsignal(signal.SIGUSR1) == signal.SIG_DFL, "an unarmed gateway took SIGUSR1"


@pytest.mark.parametrize("value", ["soon", "0", "-1", "nan"])
def test_a_value_that_is_no_number_of_seconds_is_named_and_starts_no_watch(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
) -> None:
    monkeypatch.setenv(loop_watch.STALL_ENV, value)
    caplog.set_level(logging.WARNING, logger=loop_watch.logger.name)

    async def main() -> threading.Thread | None:
        return loop_watch.arm_from_env()

    assert asyncio.run(main()) is None
    assert f"{loop_watch.STALL_ENV}={value!r}" in caplog.text


def test_the_gateway_arms_the_watch_before_it_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(loop_watch.STALL_ENV, "30")
    monkeypatch.setattr(loop_watch, "ASK_EVERY", 0.05)
    at_boot: list[list[str]] = []

    class _Stop(Exception):
        pass

    def boot() -> None:
        at_boot.append([t.name for t in threading.enumerate()])
        raise _Stop

    monkeypatch.setattr(cli_server, "_boot_config", boot)
    with pytest.raises(_Stop):
        asyncio.run(cli_server._gateway(no_dashboard=True))
    assert "loop-stall-watch" in at_boot[0]
    for thread in threading.enumerate():
        if thread.name == "loop-stall-watch":
            thread.join(timeout=5)
            assert not thread.is_alive(), "the watch outlived the gateway's loop"
