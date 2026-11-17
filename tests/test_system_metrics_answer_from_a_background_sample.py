"""``GET /api/system`` answers the last sample at once, and an aging one starts the next behind it.

A collection spawns ``ps``, ``netstat``, ``pgrep`` and ``sysctl``, and on a loaded host it takes
seconds: on production v0.3.9 one request took 1.1 s at a 1-minute load of 33 and 5.0 s at 46.
Every request that found the 2 s cache cold waited for one, and on a reload the request held one
of the browser's six connections to the gateway for all of it, so the page's other reads queued
behind it.

Now a request answers the newest sample at once, if it was taken in the last minute. When that
sample is over 5 s old, the request also starts the next collection in the background, off the
event loop and shared with any other request (one at a time), and does not wait for it. So
collections follow the asks: none runs while nobody asks, and a page polling every 30 s gets one
a poll, never a timer's every 5 s. A request that finds no sample, or only one older than a
minute, waits for a collection, as before. When the gateway stops, it waits a few seconds for a
collection in flight to finish its probes, rather than leaving its thread behind unwatched.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard import handlers_system

#: The slowest a request that has a sample may answer, with every collection held.
_PROMPT = 0.1


@pytest.fixture(autouse=True)
def _cold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers_system, "_metrics_cache", {})
    monkeypatch.setattr(handlers_system, "_metrics_cache_ts", 0.0)
    monkeypatch.setattr(handlers_system, "_metrics_inflight", None, raising=False)


def _request() -> web.Request:
    return make_mocked_request("GET", "/api/system")


def _body(response: web.Response) -> dict[str, object]:
    assert response.text is not None
    data: dict[str, object] = json.loads(response.text)
    return data


class _Collector:
    """A stand-in collection that counts its runs, how many ran at once, and can be held."""

    def __init__(self, hold: threading.Event | None = None) -> None:
        self.calls = 0
        self.running = 0
        self.most_at_once = 0
        self.finished = 0
        self.hold = hold
        self._lock = threading.Lock()

    def __call__(self) -> dict[str, object]:
        with self._lock:
            self.calls += 1
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
            n = self.calls
        try:
            if self.hold is not None:
                self.hold.wait(10)
            else:
                time.sleep(0.01)
            return {"collection": n}
        finally:
            with self._lock:
                self.running -= 1
                self.finished += 1


async def _until(check: Callable[[], bool], within: float) -> bool:
    end = time.monotonic() + within
    while time.monotonic() < end:
        if check():
            return True
        await asyncio.sleep(0.01)
    return check()


def test_a_request_with_a_sample_answers_it_while_a_collection_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cold-cache wait was the whole of a request's time on a loaded host."""
    hold = threading.Event()
    monkeypatch.setattr(handlers_system, "_collect_system_metrics", _Collector(hold))
    # A sample taken 10 s ago: past the old 2 s cache, inside the minute a sample is answered.
    monkeypatch.setattr(handlers_system, "_metrics_cache", {"collection": 0})
    monkeypatch.setattr(handlers_system, "_metrics_cache_ts", time.monotonic() - 10)

    async def scenario() -> tuple[dict[str, object], float]:
        try:
            t0 = time.monotonic()
            try:
                response = await asyncio.wait_for(handlers_system.api_system(_request()), 1)
            except TimeoutError:
                return {}, time.monotonic() - t0
            return _body(response), time.monotonic() - t0
        finally:
            hold.set()
            await asyncio.sleep(0.05)

    body, took = asyncio.run(scenario())

    assert took < _PROMPT, f"a request with a 10 s old sample waited {took:.3f} s for the probes"
    assert body == {"collection": 0}


def test_an_aging_sample_starts_the_next_in_the_background(monkeypatch: pytest.MonkeyPatch) -> None:
    collect = _Collector()
    monkeypatch.setattr(handlers_system, "_collect_system_metrics", collect)
    monkeypatch.setattr(handlers_system, "_METRICS_FRESH_FOR", 0.05, raising=False)

    async def scenario() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        first = _body(await handlers_system.api_system(_request()))  # no sample: it waits
        await asyncio.sleep(0.08)  # the sample ages past its freshness
        aging = _body(await handlers_system.api_system(_request()))  # answers it, starts the next
        # Until the next sample is the one cached: the collection's thread ends before the loop
        # stores what it returned, and on a loaded host a request can run in between.
        await _until(lambda: handlers_system._metrics_cache.get("collection") == 2, 2)
        newer = _body(await handlers_system.api_system(_request()))
        return first, aging, newer

    first, aging, newer = asyncio.run(scenario())

    assert first == {"collection": 1}
    assert aging == {"collection": 1}, "the aging request waited for the next sample"
    assert newer == {"collection": 2}, "an aging sample started no collection behind it"


def test_collections_follow_the_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shell's heartbeat asks every 30 s: one collection a heartbeat at most, none between,
    and none at all while nobody asks (a fixed 5 s timer kept ~58 for ten heartbeats)."""
    collect = _Collector()
    monkeypatch.setattr(handlers_system, "_collect_system_metrics", collect)
    # The ratios of 5 s freshness to a 30 s heartbeat, scaled down.
    monkeypatch.setattr(handlers_system, "_METRICS_FRESH_FOR", 0.02, raising=False)

    async def scenario() -> tuple[int, int]:
        for _ in range(10):
            await handlers_system.api_system(_request())
            await asyncio.sleep(0.12)
        asked = collect.calls
        await asyncio.sleep(0.5)  # nobody asks
        return asked, collect.calls

    asked, after = asyncio.run(scenario())

    assert asked <= 10, f"{asked} collections for 10 heartbeats"
    assert after == asked, f"{after - asked} collections ran with nobody asking"


def test_asks_inside_the_freshness_start_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every open card's poll, the heartbeat and a reload ask: a fresh sample answers them all."""
    collect = _Collector()
    monkeypatch.setattr(handlers_system, "_collect_system_metrics", collect)
    monkeypatch.setattr(handlers_system, "_metrics_cache", {"collection": 0})
    monkeypatch.setattr(handlers_system, "_metrics_cache_ts", time.monotonic())

    async def scenario() -> list[dict[str, object]]:
        bodies = [_body(await handlers_system.api_system(_request())) for _ in range(20)]
        await asyncio.sleep(0.1)
        return bodies

    bodies = asyncio.run(scenario())

    assert collect.calls == 0, f"{collect.calls} collections for asks a fresh sample answers"
    assert bodies == [{"collection": 0}] * 20


def test_a_failed_background_refresh_is_followed_by_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refresh that fails leaves nothing in flight: the next aging ask starts another."""
    attempts = 0

    def collect() -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("ps timed out")
        return {"collection": attempts}

    monkeypatch.setattr(handlers_system, "_collect_system_metrics", collect)
    monkeypatch.setattr(handlers_system, "_METRICS_FRESH_FOR", 0.0, raising=False)
    monkeypatch.setattr(handlers_system, "_metrics_cache", {"collection": 0})
    monkeypatch.setattr(handlers_system, "_metrics_cache_ts", time.monotonic())

    async def scenario() -> dict[str, object]:
        await handlers_system.api_system(_request())  # starts a refresh, which fails
        await _until(lambda: attempts >= 1 and handlers_system._metrics_inflight is None, 2)
        await handlers_system.api_system(_request())  # starts the next
        await _until(lambda: attempts >= 2, 2)
        await asyncio.sleep(0.05)
        return _body(await handlers_system.api_system(_request()))

    assert asyncio.run(scenario()) == {"collection": 2}


def test_one_collection_at_a_time_whoever_starts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cold requests and aging ones, together: the background refresh shares the single flight."""
    hold = threading.Event()
    collect = _Collector(hold)
    monkeypatch.setattr(handlers_system, "_collect_system_metrics", collect)
    monkeypatch.setattr(handlers_system, "_METRICS_FRESH_FOR", 0.0, raising=False)

    async def scenario() -> None:
        cold = [asyncio.ensure_future(handlers_system.api_system(_request())) for _ in range(3)]
        await _until(lambda: collect.calls >= 1, 2)
        hold.set()
        await asyncio.gather(*cold)
        hold.clear()
        # Every request now finds the sample aging, and each would start a refresh of its own.
        for _ in range(5):
            await handlers_system.api_system(_request())
        hold.set()
        # An answer from the cache never yields to the loop, so the refresh those requests started
        # has not begun its collection yet: wait for it to run and end, then give any other
        # collection a request wrongly started the time to show.
        await _until(
            lambda: collect.calls >= 2
            and collect.running == 0
            and collect.finished == collect.calls,
            2,
        )
        await asyncio.sleep(0.05)

    asyncio.run(scenario())

    assert collect.most_at_once == 1, f"{collect.most_at_once} collections ran at once"
    assert collect.calls == 2, f"{collect.calls} collections: the aging requests did not share one"


def test_the_gateway_s_stop_waits_for_the_collection_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hold = threading.Event()
    collect = _Collector(hold)
    monkeypatch.setattr(handlers_system, "_collect_system_metrics", collect)
    app = web.Application()
    handlers_system.register_system_metrics(app)

    async def scenario() -> tuple[int, float]:
        cold = asyncio.ensure_future(handlers_system.api_system(_request()))
        await _until(lambda: collect.calls >= 1, 2)
        asyncio.get_running_loop().call_later(0.2, hold.set)
        app.freeze()
        t0 = time.monotonic()
        await app.cleanup()
        took = time.monotonic() - t0
        await cold
        return collect.finished, took

    finished, took = asyncio.run(scenario())

    assert finished == 1, "the gateway stopped with a collection's probes still running"
    assert took >= 0.15, f"the stop returned after {took:.3f} s, before the collection finished"


def test_the_gateway_s_stop_waits_no_longer_than_its_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    hold = threading.Event()
    monkeypatch.setattr(handlers_system, "_collect_system_metrics", _Collector(hold))
    monkeypatch.setattr(handlers_system, "_METRICS_STOP_GRACE", 0.2, raising=False)

    async def scenario() -> float:
        cold = asyncio.ensure_future(handlers_system.api_system(_request()))
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        await handlers_system.stop_metrics_collection(web.Application())
        took = time.monotonic() - t0
        hold.set()
        await cold
        return took

    took = asyncio.run(scenario())

    assert took < 1, f"the stop waited {took:.3f} s for a collection past its grace"


def test_the_gateway_serves_the_metrics_with_the_stop() -> None:
    from gateway_routes_shared import gateway_app

    app = gateway_app()
    routes = [
        r
        for r in app.router.routes()
        if r.method == "GET" and r.resource is not None and r.resource.canonical == "/api/system"
    ]
    assert len(routes) == 1
    assert handlers_system.stop_metrics_collection in app.on_cleanup


def test_a_sample_older_than_a_minute_is_not_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gateway nobody has asked in a while answers a new reading, not a stale one."""
    monkeypatch.setattr(handlers_system, "_collect_system_metrics", _Collector())
    monkeypatch.setattr(handlers_system, "_metrics_cache", {"collection": 0})
    monkeypatch.setattr(handlers_system, "_metrics_cache_ts", time.monotonic() - 120)

    async def scenario() -> dict[str, object]:
        return _body(await handlers_system.api_system(_request()))

    assert asyncio.run(scenario()) == {"collection": 1}
