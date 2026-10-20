"""``GET /api/system`` collects the host metrics once, however many ask at the same moment.

A collection runs several subprocess probes (``ps``, ``netstat``, ``pgrep``, ``sysctl``,
``vm_stat``), and on a loaded host each one takes seconds. Every poller asked for its own:
the shell's status dot, Home's system rail, the Usage page and every open tab each missed
the cache together and each started a collection, so the probes multiplied exactly when the
host could least afford them. And the 2 s cache was stamped with the time a collection
STARTED, so a collection slower than 2 s was already stale when it was stored and the next
request started another.

Now one collection is in flight at a time and everyone waiting shares its answer, and the
cache's 2 s count from when that answer arrived. A reading a probe could not take is left
out of the payload, never written as a zero (the page shows its placeholder for it).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard import handlers_system


@pytest.fixture(autouse=True)
def _cold_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers_system, "_metrics_cache", {})
    monkeypatch.setattr(handlers_system, "_metrics_cache_ts", 0.0)
    # Absent on a build without the shared collection; `raising=False` lets the tests below
    # fail on what they assert rather than on this line.
    monkeypatch.setattr(handlers_system, "_metrics_inflight", None, raising=False)


def _request() -> web.Request:
    return make_mocked_request("GET", "/api/system")


def _body(response: web.Response) -> dict[str, object]:
    assert response.text is not None
    data: dict[str, object] = json.loads(response.text)
    return data


def test_concurrent_requests_share_one_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    started = threading.Event()
    release = threading.Event()

    def slow_collect() -> dict[str, object]:
        nonlocal calls
        calls += 1
        started.set()
        # Held until every request has arrived, so they all find the cache cold together.
        release.wait(10)
        return {"hostname": "studio.example.test", "cpu_count": 8}

    monkeypatch.setattr(handlers_system, "_collect_system_metrics", slow_collect)

    async def scenario() -> list[dict[str, object]]:
        tasks = [asyncio.ensure_future(handlers_system.api_system(_request())) for _ in range(5)]
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, started.wait, 10), "no collection started"
        # Let every request reach its wait before the collection answers.
        for _ in range(20):
            await asyncio.sleep(0)
        release.set()
        return [_body(r) for r in await asyncio.gather(*tasks)]

    bodies = asyncio.run(scenario())
    release.set()

    assert calls == 1, f"{calls} collections ran for 5 concurrent requests"
    assert bodies == [{"hostname": "studio.example.test", "cpu_count": 8}] * 5


def test_a_collection_slower_than_the_cache_is_still_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache counts from when the answer ARRIVED, so a slow host still gets cache hits."""
    calls = 0

    def collect() -> dict[str, object]:
        nonlocal calls
        calls += 1
        time.sleep(1.5)
        return {"collection": calls}

    monkeypatch.setattr(handlers_system, "_collect_system_metrics", collect)
    monkeypatch.setattr(handlers_system, "_METRICS_CACHE_TTL", 1.0)

    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        first = await handlers_system.api_system(_request())
        # Straight after the first answer: well inside the 1 s, but 1.5 s after it STARTED.
        second = await handlers_system.api_system(_request())
        return _body(first), _body(second)

    first, second = asyncio.run(scenario())

    assert calls == 1, "the answer was already stale when it was cached"
    assert first == second == {"collection": 1}


def test_a_failed_collection_is_not_shared_with_the_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def collect() -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("the working directory is gone")
        return {"collection": attempts}

    monkeypatch.setattr(handlers_system, "_collect_system_metrics", collect)

    async def scenario() -> dict[str, object]:
        with pytest.raises(OSError):
            await handlers_system.api_system(_request())
        return _body(await handlers_system.api_system(_request()))

    assert asyncio.run(scenario()) == {"collection": 2}


def test_a_cpu_reading_the_probe_could_not_take_is_left_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ps`` timing out under load meant "CPU 0%" on a host at load 64; now the key is absent."""

    def probe(args, *a, **kw):  # type: ignore[no-untyped-def]
        if list(args[:3]) == ["ps", "-A", "-o"]:
            raise subprocess.TimeoutExpired(args, 2)
        # Every other probe answers empty: no host process is run by this test.
        return b""

    monkeypatch.setattr(handlers_system, "_get_static_system_info", lambda: {})
    with patch("personalclaw.dashboard.handlers_system.subprocess.check_output", side_effect=probe):
        data = handlers_system._collect_system_metrics()

    assert "cpu_pct" not in data
    # The readings that were taken are still there.
    assert "proc_mem_mb" in data
    assert "thread_count" in data
