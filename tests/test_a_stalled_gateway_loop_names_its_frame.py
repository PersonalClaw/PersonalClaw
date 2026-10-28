"""A gateway whose event loop stops answering names the frame it is stuck in (``loop_watch``).

#5638's browser shard hung: from one point on, no request the specs made got a response, and
nothing said where the gateway's loop had stopped. The browser harness now arms a watch
(``PERSONALCLAW_LOOP_STALL_SECONDS``), which appends every thread's stack to gateway.log while the
loop does not answer.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
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


def test_an_unarmed_gateway_starts_no_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(loop_watch.STALL_ENV, raising=False)

    async def main() -> threading.Thread | None:
        return loop_watch.arm_from_env()

    assert asyncio.run(main()) is None


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
