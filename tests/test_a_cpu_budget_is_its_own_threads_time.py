"""A CPU budget (``tests/cpu_budget.py``) counts the timed call's own thread, and no other (#5629).

The credential screen's 256 KB budget went red twice in breadth's coverage leg, with coverage's
tracer already set aside for the timed call: the process's CPU time counted another thread in the
worker. These hold the budget to the calling thread, and to all of its work, so a budget reads only
its own thread's work wherever it runs (what a tracer adds is allowed for by
``cpu_budget.limit``).
"""

from __future__ import annotations

import hashlib
import threading
import time

import cpu_budget


def test_another_thread_busy_beside_the_call_is_not_its_cost():
    """🔴 Red before (``time.process_time``): a call that only sleeps read about 0.3 s while a
    second thread hashed beside it, since hashing a large buffer releases the GIL and runs on
    another core."""
    stop = threading.Event()
    data = b"x" * (8 * 1024 * 1024)

    def hash_until_stopped() -> None:
        while not stop.is_set():
            hashlib.sha256(data).digest()

    worker = threading.Thread(target=hash_until_stopped, daemon=True)
    worker.start()
    try:
        took = cpu_budget.seconds(lambda: time.sleep(0.3))
    finally:
        stop.set()
        worker.join()
    assert took < 0.1, f"{took:.2f}s"


def test_the_calls_own_work_is_all_counted():
    """The budget still reads the work the call does on its own thread: a call that spins for
    0.2 s of its thread's CPU reads at least that."""

    def spin() -> None:
        until = time.thread_time() + 0.2
        while time.thread_time() < until:
            pass

    assert cpu_budget.seconds(spin) >= 0.2
