"""A step of the event loop is held by the work it did, not by a runner that stopped the process
(``tests/loop_held.py``, #5809).

The plants are the regressions the loop bounds exist for, each made on the loop for
:data:`_PLANTED`: a sleep, a file read that waits, a wait in ``select``, a read from a blocking
socket, CPU spent on the loop, and a worker that holds the interpreter's lock. Each must still hold
the loop for about that long. The stall is the runner's: the whole process stopped in the middle of
a step that makes no call, which held the loop in wall time and must not now.
"""

from __future__ import annotations

import asyncio
import json
import os
import select
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from loop_held import LoopSteps, held

#: How long each plant holds the loop.
_PLANTED = 0.2
#: How much of a plant's hold a step may miss and still be counted as holding it: the CPU clock of
#: a busy runner reads a spin short.
_CAUGHT = 0.75 * _PLANTED


def _sleep() -> None:
    time.sleep(_PLANTED)


def _read_a_slow_file(where: Path) -> None:
    """A read that waits, as one from a busy disk does, using no CPU: from a pipe whose writer
    writes only after :data:`_PLANTED`."""
    fifo = where / "slow-read"
    os.mkfifo(fifo)

    def feed() -> None:
        with open(fifo, "wb") as writer:
            time.sleep(_PLANTED)
            writer.write(b"\0" * 4096)

    feeder = threading.Thread(target=feed)
    feeder.start()
    with open(fifo, "rb") as reader:
        assert len(reader.read(4096)) == 4096
    feeder.join()


def _wait_in_select() -> None:
    ours, theirs = socket.socketpair()
    with ours, theirs:
        readable, _, _ = select.select([ours], [], [], _PLANTED)
    assert not readable


def _read_a_blocking_socket() -> None:
    ours, theirs = socket.socketpair()
    with ours, theirs:
        ours.settimeout(5)
        sender = threading.Timer(_PLANTED, theirs.sendall, args=(b"x",))
        sender.start()
        assert ours.recv(1) == b"x"
        sender.join()


def _spend_cpu() -> None:
    until = time.thread_time() + _PLANTED
    while time.thread_time() < until:
        pass


def _held_by_a_worker() -> None:
    """A worker that holds the interpreter's lock for about :data:`_PLANTED` (an integer raised to a
    large power: one call that never lets the lock go) while this step wants to go on. The step is
    held by the worker's CPU beside it, not by its own."""
    done = threading.Event()
    exponent = _holding_exponent()

    def work() -> None:
        (_BASE**exponent).bit_length()
        done.set()

    worker = threading.Thread(target=work)
    worker.start()
    while not done.is_set():
        pass  # the step goes on only when the worker lets the lock go
    worker.join()


#: The base :func:`_held_by_a_worker` raises to a power, and the exponent that takes about
#: :data:`_PLANTED` of CPU here, measured once.
_BASE = 7
_EXPONENT: list[int] = []


def _holding_exponent() -> int:
    if not _EXPONENT:
        exponent = 1 << 16
        while True:
            began = time.thread_time()
            (_BASE**exponent).bit_length()
            if time.thread_time() - began >= _PLANTED:
                break
            exponent *= 2
        _EXPONENT.append(exponent)
    return _EXPONENT[0]


_PLANTS = {
    "a sleep": lambda _where: _sleep(),
    "a file read that waits": _read_a_slow_file,
    "a wait in select": lambda _where: _wait_in_select(),
    "a read from a blocking socket": lambda _where: _read_a_blocking_socket(),
    "CPU spent on the loop": lambda _where: _spend_cpu(),
    "a worker holding the interpreter's lock": lambda _where: _held_by_a_worker(),
}


@pytest.mark.parametrize("plant", list(_PLANTS))
@pytest.mark.asyncio
async def test_each_planted_block_still_holds_the_loop(plant, tmp_path):
    _holding_exponent()  # measured before the watch, so its own CPU is no step's
    block = _PLANTS[plant]
    loop = asyncio.get_running_loop()
    with LoopSteps() as steps:
        await asyncio.sleep(0.02)
        done = loop.create_future()
        loop.call_soon(lambda: (block(tmp_path), done.set_result(None)))
        await done
    assert steps.held >= _CAUGHT, f"{plant}: {steps.says()}"
    if plant == "a worker holding the interpreter's lock":
        worst = steps.worst
        assert worst is not None and worst.beside >= _CAUGHT, f"held by the worker: {steps.says()}"


@pytest.mark.asyncio
async def test_the_loops_own_socket_reads_and_writes_are_no_blocking_call():
    """The loop's sockets never block, so a client and a server talking over them make no call,
    and no step of theirs is held for its wall time."""

    async def answer(reader, writer):
        writer.write(await reader.readline())
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    with LoopSteps() as steps:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"ping\n")
        assert await reader.readline() == b"ping\n"
        writer.close()
        await writer.wait_closed()
    server.close()
    await server.wait_closed()
    assert steps.steps, "premise: the steps were recorded"
    assert not [s for s in steps.steps if s.calls], steps.says()


def test_another_threads_calls_are_not_the_loops():
    async def scenario() -> LoopSteps:
        with LoopSteps() as steps:
            await asyncio.to_thread(time.sleep, _PLANTED)
        return steps

    steps = asyncio.run(scenario())
    assert steps.held < _CAUGHT, steps.says()


def test_a_step_is_held_for_its_wall_time_only_when_it_made_a_call():
    assert held(0.3, 0.001, 0.002, ("open",)) == 0.3
    assert held(0.3, 0.001, 0.002, ()) == pytest.approx(0.003)
    assert held(0.3, 0.1, 0.25, ()) == 0.3, "never more than its wall time"
    assert held(0.3, 0.0, -0.01, ()) == 0.0, "a clock read short leaves nothing below zero"


#: The child the stall test stops, in the middle of a step that makes no call. It prints the step.
_STOPPED_CHILD = textwrap.dedent("""
    import asyncio, json, os, signal, sys
    sys.path.insert(0, sys.argv[1])
    from loop_held import LoopSteps

    async def main():
        loop = asyncio.get_running_loop()
        with LoopSteps() as steps:
            await asyncio.sleep(0.02)
            done = loop.create_future()
            # The parent sends SIGCONT once it has seen this process stop.
            loop.call_soon(lambda: (os.kill(os.getpid(), signal.SIGSTOP), done.set_result(None)))
            await done
        step = steps.longest
        print(json.dumps({"wall": step.wall, "held": step.held, "says": steps.says()}))

    asyncio.run(main())
    """)


@pytest.mark.skipif(not hasattr(signal, "SIGSTOP"), reason="POSIX: a process stopped and resumed")
def test_a_process_the_runner_stops_in_the_middle_of_a_step_does_not_hold_the_loop():
    """🔴 Red before: the whole process stopped for :data:`_PLANTED` inside a step, as a queue
    runner stops it, was a step of that long in wall time, over every loop bound it ran beside. The
    step made no call and used no CPU while stopped, so it holds the loop for next to nothing."""
    child = subprocess.Popen(
        [sys.executable, "-c", _STOPPED_CHILD, str(Path(__file__).parent)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        _, status = os.waitpid(child.pid, os.WUNTRACED)
        assert os.WIFSTOPPED(status), f"premise: the child stopped itself ({status})"
        time.sleep(_PLANTED)
        os.kill(child.pid, signal.SIGCONT)
        out, _ = child.communicate(timeout=60)
    finally:
        if child.poll() is None:
            child.kill()
    step = json.loads(out)
    assert step["wall"] >= _PLANTED, f"premise: the step was stopped that long; {step['says']}"
    assert step["held"] < 0.05, step["says"]
