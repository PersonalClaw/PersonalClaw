"""Tests for cross-process single-flight locks + the orphan-reaper seam."""

import multiprocessing

import pytest

from personalclaw.concurrency import lock_path, reap_orphans, single_flight


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    """Point config_dir() at a tmp dir so lock files land in isolation."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


# ── single_flight ──────────────────────────────────────────────────────────


def test_single_flight_grants_when_free():
    with single_flight("job:a") as acquired:
        assert acquired is True


def test_single_flight_reentrant_after_release():
    with single_flight("job:a") as acquired:
        assert acquired is True
    # Released on exit — a second acquisition of the same key succeeds.
    with single_flight("job:a") as acquired:
        assert acquired is True


def test_single_flight_distinct_keys_independent():
    with single_flight("job:a") as a:
        with single_flight("job:b") as b:
            assert a is True and b is True


def test_lock_path_distinct_for_colliding_prefixes():
    # Two keys that sanitize to the same readable prefix must not collide,
    # because the digest suffix differs.
    p1 = lock_path("consolidate:a/b")
    p2 = lock_path("consolidate:a:b")
    assert p1 != p2
    assert p1.parent == p2.parent


def _hold_lock(key: str, home: str, hold_secs: float, ready, done):
    """Child process: grab the lock, signal ready, hold until told, then exit."""
    import os

    os.environ["PERSONALCLAW_HOME"] = home
    from personalclaw.concurrency import single_flight as sf

    with sf(key) as acquired:
        ready.put(acquired)
        if acquired:
            done.wait(hold_secs)


def test_single_flight_blocks_across_processes(tmp_path):
    """A second OS process cannot acquire a lock the first still holds."""
    key = "job:cross-proc"
    ctx = multiprocessing.get_context("spawn")
    ready: multiprocessing.Queue = ctx.Queue()
    done = ctx.Event()
    child = ctx.Process(target=_hold_lock, args=(key, str(tmp_path), 5.0, ready, done))
    child.start()
    try:
        assert ready.get(timeout=15) is True  # child holds the lock
        # While the child holds it, this process must NOT acquire it.
        with single_flight(key) as acquired:
            assert acquired is False
    finally:
        done.set()
        child.join(timeout=10)

    # Once the child has released (exited), the lock is free again.
    assert not child.is_alive()
    with single_flight(key) as acquired:
        assert acquired is True


def test_single_flight_ignores_stale_lock_file():
    """A leftover lock FILE from a crashed process must not block acquisition.

    We guard with ``fcntl.flock`` on the file, not the file's existence — flock
    is released when its holder dies, so a stale ``.lock`` file on disk (the only
    artifact a crashed process leaves) is freely re-acquirable. This is the
    crash-zombie resistance that motivated a file lock over a DB lock row.
    """
    key = "job:stale"
    # Simulate the artifact a dead holder leaves: the lock file exists, but no
    # process holds an flock on it.
    lock_path(key).write_text("")
    assert lock_path(key).exists()
    with single_flight(key) as acquired:
        assert acquired is True


# ── reap_orphans ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reap_orphans_empty_is_noop():
    calls = []

    async def _reap(item):
        calls.append(item)

    n = await reap_orphans("x", [], _reap)
    assert n == 0
    assert calls == []


@pytest.mark.asyncio
async def test_reap_orphans_reaps_each():
    seen = []

    async def _reap(item):
        seen.append(item)

    n = await reap_orphans("x", [1, 2, 3], _reap)
    assert n == 3
    assert seen == [1, 2, 3]


@pytest.mark.asyncio
async def test_reap_orphans_isolates_failures():
    """One failing reap is logged + skipped; the rest still run."""
    seen = []

    async def _reap(item):
        if item == 2:
            raise RuntimeError("boom")
        seen.append(item)

    n = await reap_orphans("x", [1, 2, 3], _reap)
    assert n == 2  # 1 and 3 succeeded; 2 failed
    assert seen == [1, 3]
