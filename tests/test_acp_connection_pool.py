"""AcpConnectionPool — the shared connections a user's concurrent chats open sessions on.

Drives the pool with a fake connection (no real subprocess) to assert: installing the pool
starts no agent CLI; the first chat that needs a runtime spawns ONE shared connection and
every later chat opens a session on it; and shutdown closes what was spawned.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.acp.connection_pool import AcpConnectionPool, init_acp_pool, set_acp_pool


class _FakeConn:
    def __init__(self) -> None:
        self._alive = True
        self.opened = 0
        self.closed = False

    async def initialize(self, params):
        return {}

    async def new_session(self, params, *, session_files_dir=None):
        self.opened += 1
        return type("S", (), {"session_id": f"sess-{self.opened}"})()

    def is_process_alive(self) -> bool:
        return self._alive

    async def close(self) -> None:
        self.closed = True
        self._alive = False


@pytest.fixture
def spawns(monkeypatch):
    """Every ``AcpConnection.spawn`` the pool makes, answered by one shared fake."""
    import personalclaw.acp.session as sess_mod
    import personalclaw.llm.acp_session_provider as prov_mod

    calls: list[dict] = []
    shared = _FakeConn()

    async def fake_spawn(**kw):
        calls.append(kw)
        return shared

    async def fake_opener(conn, **kw):
        s = await conn.new_session({"cwd": kw.get("cwd"), "mcpServers": []})
        return type("P", (), {"session_id": s.session_id, "_conn": conn})()

    monkeypatch.setattr(sess_mod.AcpConnection, "spawn", staticmethod(fake_spawn))
    monkeypatch.setattr(prov_mod, "open_acp_session_provider", fake_opener)
    return calls, shared


@pytest.mark.asyncio
async def test_installing_the_pool_at_gateway_start_starts_no_agent_cli(spawns):
    """The pool used to warm one live connection per installed runtime at start, respawn it
    from a health loop, and refresh it every ten minutes — each a start of another agent's
    CLI that nobody asked for. Installing it now starts nothing, however long it runs."""
    calls, _shared = spawns
    pool = await init_acp_pool(asyncio.Semaphore(4))
    try:
        await asyncio.sleep(0.05)
        assert calls == [], "installing the pool started an agent CLI"
        assert pool._slots == {}, "the pool holds a runtime nobody opened a chat on"
    finally:
        await pool.shutdown()
        set_acp_pool(None)


@pytest.mark.asyncio
async def test_open_session_reuses_shared_connection(spawns):
    """Two open_session calls on the same runtime spawn ONE shared AcpConnection and
    open TWO sessions on it (the concurrency win) — and only because two chats asked."""
    calls, shared = spawns
    pool = AcpConnectionPool(start_sem=asyncio.Semaphore(4))
    p1 = await pool.open_session("acp:demo-cli", cwd="/tmp", command=["demo"], dialect="default")
    p2 = await pool.open_session("acp:demo-cli", cwd="/tmp", command=["demo"], dialect="default")

    assert len(calls) == 1  # ONE process spawned
    assert shared.opened == 2  # TWO sessions on it
    assert p1.session_id == "sess-1" and p2.session_id == "sess-2"
    assert p1._conn is p2._conn is shared
    await pool.shutdown()


@pytest.mark.asyncio
async def test_shutdown_closes_the_shared_connection_and_refuses_more(spawns):
    calls, shared = spawns
    pool = AcpConnectionPool(start_sem=asyncio.Semaphore(4))
    await pool.open_session("acp:demo-cli", cwd="/tmp", command=["demo"], dialect="default")
    await pool.shutdown()
    assert shared.closed is True
    assert (
        await pool.open_session("acp:demo-cli", cwd="/tmp", command=["demo"], dialect="default")
        is None
    )
    assert len(calls) == 1
