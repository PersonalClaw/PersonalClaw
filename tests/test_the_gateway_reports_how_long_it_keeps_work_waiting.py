"""``GET /api/resilience/loop`` reports how long the gateway kept work waiting: its loop's lag
and its worker pool's wait, for the last minute and the last ten.

A reload of Needs you on production v0.3.9 had reads that answer in 0.2 s alone answer in 1.7 to
9.6 s together, while healthz stayed at 3 ms: either the loop was held by work that grows with the
owner's data, or the default worker pool was full, so every read that awaits ``to_thread`` waited
for a worker. Nothing measured which. The gauge does, always on: a tick that sleeps and measures how
late it woke, and a no-op handed to the default executor, timed until it starts.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard import loop_gauge


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop_gauge, "TICK", 0.02)


async def _until(check, within: float) -> bool:  # type: ignore[no-untyped-def]
    end = time.monotonic() + within
    while time.monotonic() < end:
        if check():
            return True
        await asyncio.sleep(0.01)
    return check()


def test_a_loop_held_by_synchronous_work_shows_as_lag() -> None:
    gauge = loop_gauge.LoopGauge()

    async def scenario() -> dict[str, object]:
        gauge.start()
        try:
            await asyncio.sleep(0.1)
            time.sleep(0.3)  # a handler's synchronous work, on the loop
            await asyncio.sleep(0.1)
            return gauge.report()
        finally:
            await gauge.stop()

    report = asyncio.run(scenario())
    lag = report["loop_lag"]
    assert isinstance(lag, dict)
    assert lag["max_1m_ms"] >= 250, f"a 300 ms hold read as {lag['max_1m_ms']} ms of lag"
    assert lag["max_10m_ms"] >= lag["max_1m_ms"]


def test_a_full_worker_pool_shows_as_wait_and_a_queue() -> None:
    gauge = loop_gauge.LoopGauge()
    release = threading.Event()

    async def scenario() -> dict[str, object]:
        loop = asyncio.get_running_loop()
        gauge.start()
        try:
            await asyncio.sleep(0.1)
            # Every worker of the default pool held, and more work queued behind them.
            pool = loop_gauge._pool_of(loop)
            workers = pool._max_workers if pool is not None else 32
            held = [loop.run_in_executor(None, release.wait, 10) for _ in range(workers + 3)]
            await asyncio.sleep(0.3)
            release.set()
            await asyncio.gather(*held)
            # The probe handed over while the pool was full started once a worker came free.
            await asyncio.sleep(0.1)
            return gauge.report()
        finally:
            release.set()
            await gauge.stop()

    report = asyncio.run(scenario())
    wait, pool = report["pool_wait"], report["pool"]
    assert isinstance(wait, dict) and isinstance(pool, dict)
    assert (
        wait["max_1m_ms"] >= 200
    ), f"a pool full for 300 ms read as {wait['max_1m_ms']} ms of wait"
    assert (
        pool["queued_max_1m"] >= 3
    ), f"{pool['queued_max_1m']} queued behind a full pool of {pool['workers']}"


def test_a_calm_gateway_reads_calm() -> None:
    gauge = loop_gauge.LoopGauge()

    async def scenario() -> dict[str, object]:
        gauge.start()
        try:
            await asyncio.sleep(0.3)
            return gauge.report()
        finally:
            await gauge.stop()

    report = asyncio.run(scenario())
    lag, wait = report["loop_lag"], report["pool_wait"]
    assert isinstance(lag, dict) and isinstance(wait, dict)
    assert lag["max_1m_ms"] < 100 and wait["max_1m_ms"] < 100, report


def test_a_sample_leaves_the_minute_once_it_is_older_than_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_gauge, "WINDOW", 0.2)
    series = loop_gauge._Series()
    series.add(time.monotonic() - 1.0, 5.0)  # a stall a second ago, past the window
    series.add(time.monotonic(), 0.001)
    report = series.report()
    assert report["max_1m_ms"] == 1.0, "a sample past the window still counted in it"
    assert report["max_10m_ms"] == 5000.0, "the ten minutes forgot a stall inside them"


def test_the_gauge_stops_with_the_gateway() -> None:
    gauge = loop_gauge.LoopGauge()

    async def scenario() -> tuple[int, int]:
        gauge.start()
        await asyncio.sleep(0.1)
        await gauge.stop()
        stopped = len(gauge.lag.samples)
        await asyncio.sleep(0.15)
        return stopped, len(gauge.lag.samples)

    stopped, later = asyncio.run(scenario())
    assert later == stopped, f"{later - stopped} samples taken after the stop"


def test_the_gateway_serves_the_gauge_and_starts_and_stops_it() -> None:
    from gateway_routes_shared import gateway_app

    app = gateway_app()
    routes = [
        r
        for r in app.router.routes()
        if r.method == "GET"
        and r.resource is not None
        and r.resource.canonical == "/api/resilience/loop"
    ]
    assert len(routes) == 1
    assert loop_gauge._start in app.on_startup
    assert loop_gauge._stop in app.on_cleanup


def test_the_route_answers_the_report() -> None:
    async def scenario() -> dict[str, object]:
        import json

        response = await loop_gauge.api_loop_gauge(
            make_mocked_request("GET", "/api/resilience/loop")
        )
        assert response.text is not None
        data: dict[str, object] = json.loads(response.text)
        return data

    data = asyncio.run(scenario())
    assert set(data) >= {
        "sampling",
        "loop_lag",
        "pool_wait",
        "pool",
        "sampled_every_ms",
        "running_for_s",
    }


def test_the_ten_minutes_keep_only_their_own_minutes() -> None:
    series = loop_gauge._Series()
    base = 1_000 * 60.0  # a minute's start
    for minute in range(12):
        series.add(base + minute * 60, 9.0 if minute < 2 else 0.5)
    assert [m for m, _ in series.minutes] == list(range(1_002, 1_012)), "minutes past the ten"
    assert series.report()["max_10m_ms"] == 500.0, "a stall twelve minutes ago is still the worst"


def test_a_new_loop_starts_the_gauge_again_after_one_that_ended_without_a_stop() -> None:
    gauge = loop_gauge.LoopGauge()

    async def first() -> None:
        gauge.start()
        await asyncio.sleep(0.1)  # and the loop ends, with no stop

    async def second() -> tuple[int, int]:
        gauge.start()
        try:
            before = len(gauge.lag.samples)
            await asyncio.sleep(0.15)
            return before, len(gauge.lag.samples)
        finally:
            await gauge.stop()

    asyncio.run(first())
    before, after = asyncio.run(second())
    assert after > before, "the gauge took no sample on the new loop"


def test_a_failed_pool_probe_is_followed_by_the_next(caplog: pytest.LogCaptureFixture) -> None:
    gauge = loop_gauge.LoopGauge()

    async def scenario() -> int:
        loop = asyncio.get_running_loop()
        real = loop.run_in_executor
        failed: list[int] = []

        def flaky(executor, func, *args):  # type: ignore[no-untyped-def]
            if not failed:
                failed.append(1)
                raise RuntimeError("cannot schedule new futures after shutdown")
            return real(executor, func, *args)

        loop.run_in_executor = flaky  # type: ignore[method-assign]
        gauge.start()
        try:
            await _until(lambda: len(gauge.pool_wait.samples) >= 3, 2)
            return len(gauge.pool_wait.samples)
        finally:
            await gauge.stop()

    with caplog.at_level("WARNING", logger=loop_gauge.__name__):
        samples = asyncio.run(scenario())
    assert samples >= 3, "one failed probe ended the pool's series"
    assert any(
        "tick failed" in r.getMessage() for r in caplog.records
    ), "the failure was not logged"


def test_a_stopped_gauge_says_it_is_not_sampling() -> None:
    gauge = loop_gauge.LoopGauge()

    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        gauge.start()
        await _until(lambda: len(gauge.pool_wait.samples) >= 2, 2)
        running = gauge.report()
        await gauge.stop()
        await asyncio.sleep(loop_gauge.TICK * (loop_gauge.MISSED_TICKS + 1))
        return running, gauge.report()

    running, stopped = asyncio.run(scenario())
    assert running["sampling"] is True, running
    assert stopped["sampling"] is False, "a gauge that stopped still read as sampling"
    lag = stopped["loop_lag"]
    assert isinstance(lag, dict) and isinstance(lag["last_sample_s_ago"], float), lag


def test_a_full_pool_reads_as_a_rising_wait_while_it_lasts() -> None:
    """While every worker is held, the probe waits for one and takes no sample: the gauge still
    samples, and its wait now rises with the hold, never a calm 0.1 ms until it ends."""
    gauge = loop_gauge.LoopGauge()
    release = threading.Event()

    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        loop = asyncio.get_running_loop()
        gauge.start()
        try:
            await _until(lambda: len(gauge.pool_wait.samples) >= 2, 2)
            pool = loop_gauge._pool_of(loop)
            workers = pool._max_workers if pool is not None else 32
            held = [loop.run_in_executor(None, release.wait, 10) for _ in range(workers)]
            await asyncio.sleep(0.3)
            early = gauge.report()
            await asyncio.sleep(0.2)
            later = gauge.report()
            release.set()
            await asyncio.gather(*held)
            return early, later
        finally:
            release.set()
            await gauge.stop()

    early, later = asyncio.run(scenario())
    assert (
        early["sampling"] is True and later["sampling"] is True
    ), "a full pool read as not sampling"
    e, lt = early["pool_wait"], later["pool_wait"]
    assert isinstance(e, dict) and isinstance(lt, dict)
    assert e["now_ms"] >= 150, f"0.3 s into a full pool the wait read {e['now_ms']} ms"
    assert lt["now_ms"] > e["now_ms"], "the wait did not rise while the pool stayed full"
    assert lt["max_1m_ms"] >= lt["now_ms"], "the minute's worst is under the wait now"


def test_a_calm_pool_between_probes_reads_calm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Between two probes nothing waits for a worker: the wait now is the last probe's, never the
    time since it was handed over."""
    monkeypatch.setattr(loop_gauge, "TICK", 0.2)
    gauge = loop_gauge.LoopGauge()

    async def scenario() -> dict[str, object]:
        gauge.start()
        try:
            await _until(lambda: len(gauge.pool_wait.samples) >= 1, 2)
            landed = len(gauge.pool_wait.samples)
            await _until(lambda: len(gauge.pool_wait.samples) > landed, 2)
            await asyncio.sleep(0.1)  # halfway to the next probe
            return gauge.report()
        finally:
            await gauge.stop()

    report = asyncio.run(scenario())
    wait = report["pool_wait"]
    assert isinstance(wait, dict)
    assert wait["now_ms"] < 50, f"a calm pool read {wait['now_ms']} ms of wait between probes"
