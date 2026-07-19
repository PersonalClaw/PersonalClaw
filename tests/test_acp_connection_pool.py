"""AcpConnectionPool — one live ACP connection per ready runtime.

Drives the pool with a fake provider (no real subprocess) to assert: warming is
readiness-gated, the discovery snapshot is served off the live connection and
expires on TTL / death, claim detaches the connection + re-warms a replacement,
and invalidate/shutdown tear down cleanly.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.acp.connection_pool import AcpConnectionPool


class _FakeProvider:
    """Minimal stand-in for AcpAgentProvider: a started, alive connection with a
    session_snapshot, that can be shut down."""

    def __init__(self, runtime_id: str, snapshot: dict | None = None) -> None:
        self.runtime_id = runtime_id
        self._alive = True
        self._started = False
        self._snapshot = snapshot if snapshot is not None else {
            "sessionId": f"sid-{runtime_id}",
            "modes": {"availableModes": [{"id": "gpu-dev", "name": "gpu-dev"}]},
            "models": {"availableModels": [{"modelId": "auto"}]},
        }

    async def start(self) -> None:
        self._started = True

    @property
    def session_snapshot(self) -> dict:
        return self._snapshot

    def is_process_alive(self) -> bool:
        return self._alive

    async def shutdown(self) -> None:
        self._alive = False


def _make_pool(**over):
    built: list[_FakeProvider] = []

    def builder(runtime_id: str) -> _FakeProvider:
        p = _FakeProvider(runtime_id)
        built.append(p)
        return p

    pool = AcpConnectionPool(
        provider_builder=over.get("builder", builder),
        start_sem=asyncio.Semaphore(4),
        readiness_check=over.get("readiness"),
        snapshot_ttl_secs=over.get("ttl", 600.0),
    )
    return pool, built


@pytest.mark.asyncio
async def test_warm_creates_live_connection_and_snapshot():
    pool, built = _make_pool()
    assert await pool.warm("acp:test-cli") is True
    assert len(built) == 1 and built[0]._started
    snap = pool.snapshot("acp:test-cli")
    assert snap and snap["modes"]["availableModes"][0]["id"] == "gpu-dev"


@pytest.mark.asyncio
async def test_warm_is_idempotent_while_alive():
    pool, built = _make_pool()
    await pool.warm("acp:test-cli")
    await pool.warm("acp:test-cli")  # already live → no new build
    assert len(built) == 1


@pytest.mark.asyncio
async def test_warm_gated_on_readiness():
    async def not_ready(_rt):
        return False

    pool, built = _make_pool(readiness=not_ready)
    assert await pool.warm("acp:codex") is False
    assert built == []
    assert pool.snapshot("acp:codex") is None


@pytest.mark.asyncio
async def test_snapshot_none_when_dead_or_expired():
    pool, built = _make_pool(ttl=0.0)  # immediately stale
    await pool.warm("acp:test-cli")
    # TTL=0 → snapshot considered stale right away.
    assert pool.snapshot("acp:test-cli") is None

    pool2, built2 = _make_pool()
    await pool2.warm("acp:test-cli")
    built2[0]._alive = False  # process died
    assert pool2.snapshot("acp:test-cli") is None


@pytest.mark.asyncio
async def test_claim_detaches_and_rewarms():
    pool, built = _make_pool()
    await pool.warm("acp:test-cli")
    first = built[0]
    claimed = await pool.claim("acp:test-cli")
    assert claimed is first
    # Claimed connection left the pool — snapshot no longer served from it.
    assert pool.snapshot("acp:test-cli") is None
    # A replacement is re-warmed in the background.
    await asyncio.sleep(0.05)
    assert len(built) == 2
    assert pool.snapshot("acp:test-cli") is not None  # replacement live


@pytest.mark.asyncio
async def test_claim_returns_none_when_empty():
    pool, _ = _make_pool()
    assert await pool.claim("acp:test-cli") is None  # never warmed


@pytest.mark.asyncio
async def test_invalidate_shuts_down_and_clears():
    pool, built = _make_pool()
    await pool.warm("acp:test-cli")
    await pool.invalidate("acp:test-cli")
    assert built[0]._alive is False  # shut down
    assert pool.snapshot("acp:test-cli") is None


@pytest.mark.asyncio
async def test_warm_all_parallel():
    pool, built = _make_pool()
    n = await pool.warm_all(["acp:test-cli", "acp:claude-code"])
    assert n == 2
    assert pool.snapshot("acp:test-cli") and pool.snapshot("acp:claude-code")


@pytest.mark.asyncio
async def test_shutdown_drains_all():
    pool, built = _make_pool()
    await pool.warm_all(["acp:test-cli", "acp:claude-code"])
    await pool.shutdown()
    assert all(p._alive is False for p in built)
    # Closed pool refuses further warms.
    assert await pool.warm("acp:test-cli") is False


@pytest.mark.asyncio
async def test_warm_failure_is_swallowed():
    def bad_builder(_rt):
        raise RuntimeError("spawn boom")

    pool, _ = _make_pool(builder=bad_builder)
    assert await pool.warm("acp:test-cli") is False
    assert pool.snapshot("acp:test-cli") is None


@pytest.mark.asyncio
async def test_warm_failure_sets_exponential_backoff():
    """A repeatedly-failing runtime accrues backoff so the health loop stops
    re-spawning it every interval (the npx/CLI process-leak source)."""
    def bad_builder(_rt):
        raise RuntimeError("delegate CLI cannot start")

    pool, _ = _make_pool(builder=bad_builder)
    await pool.warm("acp:codex")
    slot = pool._slots["acp:codex"]
    assert slot.fail_count == 1
    first_retry = slot.next_retry_at
    assert first_retry > 0  # a future retry window was set

    await pool.warm("acp:codex")
    assert slot.fail_count == 2
    # Backoff grows (second window is further out than the first's base delay).
    assert slot.next_retry_at >= first_retry


@pytest.mark.asyncio
async def test_health_loop_skips_runtime_within_backoff_window(monkeypatch):
    """The health loop must NOT re-warm a runtime whose backoff window is open."""
    calls = {"n": 0}

    def counting_bad_builder(_rt):
        calls["n"] += 1
        raise RuntimeError("still broken")

    pool, _ = _make_pool(builder=counting_bad_builder)
    # First warm fails and arms a long backoff.
    await pool.warm("acp:codex")
    assert calls["n"] == 1
    slot = pool._slots["acp:codex"]
    assert slot.next_retry_at > 0

    # Drive ONE health-loop pass with a near-zero interval; the open backoff
    # window must cause it to skip (no new build).
    import personalclaw.acp.connection_pool as cp
    monkeypatch.setattr(cp, "_HEALTH_INTERVAL_SECS", 0.01)
    task = asyncio.ensure_future(pool._health_loop())
    await asyncio.sleep(0.1)
    pool._closed = True
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    assert calls["n"] == 1, "health loop re-warmed a runtime still inside its backoff window"


@pytest.mark.asyncio
async def test_invalidate_clears_backoff():
    """invalidate() (enable/disable/auth change) resets backoff so the next warm
    retries immediately rather than waiting out a stale window."""
    def bad_builder(_rt):
        raise RuntimeError("broken")

    pool, _ = _make_pool(builder=bad_builder)
    await pool.warm("acp:codex")
    slot = pool._slots["acp:codex"]
    assert slot.fail_count == 1 and slot.next_retry_at > 0
    await pool.invalidate("acp:codex")
    assert slot.fail_count == 0
    assert slot.next_retry_at == 0.0


@pytest.mark.asyncio
async def test_successful_warm_clears_prior_backoff():
    """A runtime that recovers clears its failure count + retry gate."""
    state = {"fail": True}

    def flaky_builder(rt):
        if state["fail"]:
            raise RuntimeError("not yet")
        return _FakeProvider(rt)

    pool, _ = _make_pool(builder=flaky_builder)
    await pool.warm("acp:flaky")
    slot = pool._slots["acp:flaky"]
    assert slot.fail_count == 1
    # Runtime becomes healthy; an explicit warm now succeeds and clears backoff.
    state["fail"] = False
    assert await pool.warm("acp:flaky") is True
    assert slot.fail_count == 0
    assert slot.next_retry_at == 0.0


# ── concurrent sessions (P9): open_session reuses ONE shared connection ──────

@pytest.mark.asyncio
async def test_open_session_reuses_shared_connection(monkeypatch):
    """Two open_session calls on the same runtime spawn ONE shared AcpConnection and
    open TWO sessions on it (the P9 concurrency win)."""
    spawns: list = []
    sessions: list = []

    class _FakeConn:
        def __init__(self):
            self._alive = True
            self.opened = 0

        async def initialize(self, params):
            return {}

        async def new_session(self, params, *, session_files_dir=None):
            self.opened += 1
            s = type("S", (), {"session_id": f"sess-{self.opened}"})()
            sessions.append(s)
            return s

        def is_process_alive(self):
            return self._alive

    shared = _FakeConn()

    async def fake_spawn(**kw):
        spawns.append(kw)
        return shared

    # Patch AcpConnection.spawn + the provider opener (avoid real AcpSessionProvider deps)
    import personalclaw.acp.session as sess_mod
    monkeypatch.setattr(sess_mod.AcpConnection, "spawn", staticmethod(fake_spawn))

    async def fake_opener(conn, **kw):
        s = await conn.new_session({"cwd": kw.get("cwd"), "mcpServers": []})
        return type("P", (), {"session_id": s.session_id, "_conn": conn})()

    import personalclaw.llm.acp_session_provider as prov_mod
    monkeypatch.setattr(prov_mod, "open_acp_session_provider", fake_opener)

    pool, _ = _make_pool()
    p1 = await pool.open_session("acp:demo-cli", cwd="/tmp", command=["demo"], dialect="default")
    p2 = await pool.open_session("acp:demo-cli", cwd="/tmp", command=["demo"], dialect="default")

    assert len(spawns) == 1              # ONE process spawned
    assert shared.opened == 2            # TWO sessions on it
    assert p1.session_id == "sess-1" and p2.session_id == "sess-2"
    assert p1._conn is p2._conn is shared
