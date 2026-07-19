"""Tests for MCP connection pooling, session isolation, idle eviction, and the
spawn circuit-breaker (rel-mcp-server-pooling #46).

These exercise the registry's routing/lifecycle logic directly — no real MCP
subprocess is spawned (connections are created but never `ensure_started`), so
they run without the optional ``mcp`` SDK extra.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.mcp_client import (
    _BREAKER_THRESHOLD,
    McpClientRegistry,
    McpServerConn,
    _conn_key,
    _is_poolable,
    with_mcp_session_eviction,
)

_POOLABLE = {"command": "x", "poolable": True}
_STATEFUL = {"command": "y"}  # no poolable → isolated per session


# ── poolable classification + key derivation ──
def test_poolable_flag():
    assert _is_poolable({"command": "x", "poolable": True}) is True
    assert _is_poolable({"command": "x"}) is False  # safe-by-default: NOT shared
    assert _is_poolable({"command": "x", "poolable": False}) is False


def test_conn_key_poolable_shares_scope():
    # Poolable server → scope "" regardless of session → shared connection. The key is
    # now (name, scope, spec_hash) (P23e); the scope component (k[1]) is still "".
    k1 = _conn_key("s", _POOLABLE, "sess-1")
    k2 = _conn_key("s", _POOLABLE, "sess-2")
    assert k1 == k2                       # same spec → identical key → one shared conn
    assert k1[0] == "s" and k1[1] == ""   # name + shared scope
    assert len(k1) == 3 and k1[2]         # carries a non-empty content hash


def test_conn_key_stateful_scopes_by_session():
    assert _conn_key("s", _STATEFUL, "sess-1")[:2] == ("s", "sess-1")
    assert _conn_key("s", _STATEFUL, "sess-2")[:2] == ("s", "sess-2")
    assert _conn_key("s", _STATEFUL, "")[:2] == ("s", "")  # no session → canonical


def test_conn_key_same_name_different_spec_disambiguates():
    # P23e: two servers sharing a NAME but differing in command/args/env must get
    # DISTINCT keys (no collision on one pooled connection).
    a = _conn_key("dup", {"command": "server-a", "poolable": True}, "")
    b = _conn_key("dup", {"command": "server-b", "poolable": True}, "")
    assert a != b and a[0] == b[0] == "dup" and a[1] == b[1] == ""  # differ only in hash
    # env / args also change the hash
    c = _conn_key("dup", {"command": "server-a", "args": ["--x"], "poolable": True}, "")
    assert c != a


# ── registry routing: pooling vs isolation ──
def test_poolable_server_shared_across_sessions():
    reg = McpClientRegistry()
    reg.load_from_specs({"shared": _POOLABLE})
    c1 = reg.get("shared", "sess-1")
    c2 = reg.get("shared", "sess-2")
    assert c1 is c2  # one connection for all sessions (optimal pooling)
    assert c1.scope == ""


def test_stateful_server_isolated_per_session():
    reg = McpClientRegistry()
    reg.load_from_specs({"browser": _STATEFUL})
    c1 = reg.get("browser", "sess-1")
    c2 = reg.get("browser", "sess-2")
    assert c1 is not c2  # separate connections → no state leak
    assert c1.scope == "sess-1"
    assert c2.scope == "sess-2"
    # Same session reuses its own connection.
    assert reg.get("browser", "sess-1") is c1


def test_unknown_server_returns_none():
    reg = McpClientRegistry()
    reg.load_from_specs({"known": _POOLABLE})
    assert reg.get("nope", "sess-1") is None


def test_canonical_connection_created_eagerly():
    reg = McpClientRegistry()
    reg.load_from_specs({"a": _STATEFUL})
    # items() lists the canonical (scope "") connection right after load.
    names = {n for n, _ in reg.items()}
    assert names == {"a"}


def test_items_lists_each_server_once_despite_per_session_conns():
    reg = McpClientRegistry()
    reg.load_from_specs({"browser": _STATEFUL})
    reg.get("browser", "sess-1")
    reg.get("browser", "sess-2")  # two per-session conns + the canonical
    names = [n for n, _ in reg.items()]
    assert names == ["browser"]  # deduped to one row for the Tools page


# ── reconcile / removal ──
def test_removed_server_drops_all_scoped_conns():
    reg = McpClientRegistry()
    reg.load_from_specs({"browser": _STATEFUL, "keep": _POOLABLE})
    reg.get("browser", "sess-1")
    reg.get("browser", "sess-2")
    assert len(reg._conns) == 4  # 2 canonical + 2 per-session
    reg.load_from_specs({"keep": _POOLABLE})  # browser removed
    remaining = {k[0] for k in reg._conns}
    assert remaining == {"keep"}


def test_disabled_server_not_registered():
    reg = McpClientRegistry()
    reg.load_from_specs({"off": {"command": "x", "disabled": True}})
    assert reg.get("off", "s") is None
    assert reg.items() == []


# ── session eviction ──
def test_evict_session_drops_only_that_sessions_conns():
    reg = McpClientRegistry()
    reg.load_from_specs({"browser": _STATEFUL, "shared": _POOLABLE})
    reg.get("browser", "sess-1")
    reg.get("browser", "sess-2")
    reg.get("shared", "sess-1")
    reg.evict_session("sess-1")
    # keys are (name, scope, spec_hash) now — match on the (name, scope) prefix.
    prefixes = {(k[0], k[1]) for k in reg._conns}
    # sess-1's browser conn gone; sess-2's stays; shared (scope "") stays.
    assert ("browser", "sess-1") not in prefixes
    assert ("browser", "sess-2") in prefixes
    assert ("shared", "") in prefixes


def test_evict_empty_session_is_noop():
    reg = McpClientRegistry()
    reg.load_from_specs({"browser": _STATEFUL})
    before = set(reg._conns)
    reg.evict_session("")
    assert set(reg._conns) == before


# ── idle eviction ──
def test_sweep_idle_reaps_stale_only():
    reg = McpClientRegistry()
    reg.load_from_specs({"a": _STATEFUL})
    conn = reg.get("a", "sess-1")
    # Force this connection stale; the canonical "a" stays fresh.
    conn._last_used -= 10_000
    reaped = reg.sweep_idle(ttl_secs=600)
    assert reaped == 1
    prefixes = {(k[0], k[1]) for k in reg._conns}
    assert ("a", "sess-1") not in prefixes
    assert ("a", "") in prefixes  # fresh canonical survived


def test_sweep_idle_keeps_fresh():
    reg = McpClientRegistry()
    reg.load_from_specs({"a": _POOLABLE})
    reg.get("a", "sess-1")
    assert reg.sweep_idle(ttl_secs=600) == 0


# ── circuit breaker ──
def test_breaker_trips_after_threshold_failures():
    conn = McpServerConn("bad", _STATEFUL)
    for _ in range(_BREAKER_THRESHOLD):
        conn._note_failure()
    import time
    assert conn._breaker_until > time.monotonic()  # cooldown armed


@pytest.mark.asyncio
async def test_breaker_blocks_respawn_during_cooldown():
    conn = McpServerConn("bad", _STATEFUL)
    import time
    conn._consecutive_failures = _BREAKER_THRESHOLD
    conn._breaker_until = time.monotonic() + 60
    # ensure_started must refuse without spawning a task.
    ok = await conn.ensure_started()
    assert ok is False
    assert conn._task is None
    assert "circuit breaker" in conn.error


def test_touch_updates_last_used():
    conn = McpServerConn("a", _POOLABLE)
    conn._last_used = 0.0
    conn.touch()
    assert conn._last_used > 0.0


# ── session-eviction composition helper ──
@pytest.mark.asyncio
async def test_with_mcp_session_eviction_runs_prior_then_evicts():
    calls: list[str] = []

    async def prior(sk: str) -> None:
        calls.append(f"prior:{sk}")

    wrapped = with_mcp_session_eviction(prior)
    await wrapped("sess-9")
    assert calls == ["prior:sess-9"]  # prior ran; eviction best-effort after


@pytest.mark.asyncio
async def test_with_mcp_session_eviction_prior_failure_isolated():
    async def boom(sk: str) -> None:
        raise RuntimeError("consolidation broke")

    wrapped = with_mcp_session_eviction(boom)
    # Must not raise — prior failure is swallowed so teardown continues.
    await wrapped("sess-9")


@pytest.mark.asyncio
async def test_with_mcp_session_eviction_none_prior():
    wrapped = with_mcp_session_eviction(None)
    await wrapped("sess-9")  # no prior → just eviction, no error


# ── P23e: reconcile drops a stale-hash canonical conn on a spec change ──
def test_reconcile_replaces_conn_when_spec_content_changes():
    reg = McpClientRegistry()
    reg.load_from_specs({"srv": {"command": "old", "poolable": True}})
    c_old = reg.get("srv", "")
    # Re-load with a DIFFERENT command → new content hash → the old conn is stale.
    reg.load_from_specs({"srv": {"command": "new", "poolable": True}})
    c_new = reg.get("srv", "")
    assert c_new is not c_old            # spawned fresh for the new spec
    # only ONE canonical "srv" conn remains (the old-hash orphan was dropped)
    canon = [k for k in reg._conns if k[0] == "srv" and k[1] == ""]
    assert len(canon) == 1


# ── P23d: pool-stats counters ──
def test_pool_stats_counts_spawns_reuse_and_shape():
    reg = McpClientRegistry()
    reg.load_from_specs({"shared": _POOLABLE, "browser": _STATEFUL})
    # 2 canonical conns spawned on load. Serve the shared one twice (reuse) + a session.
    reg.get("shared", "sess-1")
    reg.get("shared", "sess-2")   # reuses the shared conn (no new spawn)
    reg.get("browser", "sess-1")  # new session conn (spawn)
    s = reg.pool_stats()
    assert s["configured_servers"] == 2
    assert s["shared_conns"] >= 1 and s["session_conns"] >= 1
    assert s["spawns"] >= 3 and s["served"] >= 3
    assert s["reused"] == max(0, s["served"] - s["spawns"])
