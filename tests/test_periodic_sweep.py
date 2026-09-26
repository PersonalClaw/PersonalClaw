"""The gateway's watchdogs end with the gateway (``personalclaw.periodic_sweep``).

The defect: the three watchdogs a boot starts were ``while True: sleep(30); sweep()`` daemon
threads with no way to end them, so every gateway a process booted and stopped left three
sweepers behind. In the test suite, where 25 tests boot a real gateway, those sweepers made
18,761 reads of the developer's real ``~/.personalclaw`` (installed apps, ``config.json``)
between and during unrelated tests — found by the real-home guard. And in any process that
stops a gateway and carries on, the backend watchdog revived the backends the shutdown had just
terminated.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from personalclaw.periodic_sweep import PeriodicSweep

_WATCHDOG_NAMES = ("app-backend-watchdog", "app-worker-watchdog", "model-sidecar-watchdog")


def _alive(name: str) -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == name and t.is_alive()]


def test_a_sweep_runs_every_interval_until_stopped() -> None:
    ran = threading.Event()
    count = [0]

    def sweep() -> None:
        count[0] += 1
        ran.set()

    sweeper = PeriodicSweep("test-sweep-runs", 0.01, sweep)
    thread = sweeper.start()
    assert ran.wait(5), "the sweep never ran"
    assert sweeper.stop() is True
    assert not thread.is_alive()
    settled = count[0]
    time.sleep(0.05)
    assert count[0] == settled, "a stopped sweeper swept again"


def test_stop_ends_the_thread_promptly_even_on_a_long_interval() -> None:
    """The watchdogs sleep 30s between sweeps; a stop must not wait that out."""
    sweeper = PeriodicSweep("test-sweep-long-interval", 3600, lambda: None)
    thread = sweeper.start()
    began = time.monotonic()
    assert sweeper.stop() is True
    assert time.monotonic() - began < 2
    assert not thread.is_alive()


def test_start_is_idempotent_so_two_sweepers_never_race() -> None:
    sweeper = PeriodicSweep("test-sweep-idempotent", 3600, lambda: None)
    try:
        first = sweeper.start()
        assert sweeper.start() is first
        assert len(_alive("test-sweep-idempotent")) == 1
    finally:
        sweeper.stop()


def test_a_stopped_sweeper_can_be_started_again() -> None:
    """A second boot in the same process gets live supervision back."""
    sweeper = PeriodicSweep("test-sweep-restart", 3600, lambda: None)
    first = sweeper.start()
    sweeper.stop()
    second = sweeper.start()
    try:
        assert second is not first and second.is_alive()
    finally:
        sweeper.stop()


def test_a_failing_sweep_does_not_end_supervision() -> None:
    passes = [0]
    second_pass = threading.Event()

    def sweep() -> None:
        passes[0] += 1
        if passes[0] >= 2:
            second_pass.set()
        raise RuntimeError("one bad pass")

    sweeper = PeriodicSweep("test-sweep-survives", 0.01, sweep)
    sweeper.start()
    try:
        assert second_pass.wait(5), "the first failure ended the sweeper"
    finally:
        sweeper.stop()


def test_stop_waits_for_an_in_flight_sweep() -> None:
    inside, release = threading.Event(), threading.Event()
    finished = []

    def sweep() -> None:
        inside.set()
        release.wait(5)
        finished.append(True)

    sweeper = PeriodicSweep("test-sweep-in-flight", 0.01, sweep)
    thread = sweeper.start()
    assert inside.wait(5)
    threading.Timer(0.1, release.set).start()
    assert sweeper.stop(timeout=5) is True
    assert finished == [True] and not thread.is_alive()


def test_stop_without_start_is_a_no_op() -> None:
    assert PeriodicSweep("test-sweep-never-started", 1, lambda: None).stop() is True


def test_stop_extension_watchdogs_ends_all_three() -> None:
    from personalclaw.apps.backend_runtime import start_backend_watchdog
    from personalclaw.apps.worker_runtime import start_worker_watchdog
    from personalclaw.local_models.sidecar import start_sidecar_watchdog
    from personalclaw.providers.loader import stop_extension_watchdogs

    threads = [start_backend_watchdog(), start_worker_watchdog(), start_sidecar_watchdog()]
    assert [t.name for t in threads] == list(_WATCHDOG_NAMES)
    stop_extension_watchdogs()
    assert [t for t in threads if t.is_alive()] == []


@pytest.mark.asyncio
async def test_a_gateway_s_cleanup_stops_the_watchdogs_its_boot_started(
    tmp_path, monkeypatch
) -> None:
    """The end-to-end half: boot the real gateway, clean it up, and nothing it started sweeps.

    Before the fix all three threads were still alive here — and stayed alive for the life of
    the process, sweeping the real home once the test's isolation was undone.
    """
    from personalclaw.dashboard.server import start_dashboard

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    before = {name: set(map(id, _alive(name))) for name in _WATCHDOG_NAMES}
    runner, _state = await start_dashboard(sessions=MagicMock(count=0), port=0)
    try:
        started = {
            name: [t for t in _alive(name) if id(t) not in before[name]] for name in _WATCHDOG_NAMES
        }
        assert all(started.values()), f"the boot did not start every watchdog: {started}"
    finally:
        await runner.cleanup()
    survivors = {name: [t for t in threads if t.is_alive()] for name, threads in started.items()}
    assert not any(survivors.values()), f"alive after the gateway stopped: {survivors}"
