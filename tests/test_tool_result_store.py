"""OP2 — per-session raw tool-result store + tool_result_get round-trip.

A large tool output is projected (preview to the model) and its raw is retained;
the agent recovers the dropped slice via tool_result_get(result_id, range|grep).
"""

from __future__ import annotations

import pytest

from personalclaw.tool_providers import result_store


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.session_workspace as ws
    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    return tmp_path


def test_store_and_get_roundtrip():
    raw = "line A\n" + ("filler\n" * 1000) + "line Z"
    rid = result_store.store_result("sess1", raw, content_type="log", tool="bash")
    assert rid.startswith("r_")
    rec = result_store.get_result("sess1", rid)
    assert rec is not None and rec["raw"] == raw and rec["content_type"] == "log"


def test_fetch_slice_range():
    raw = "".join(f"{i:05d}\n" for i in range(1000))  # 6000 chars
    rid = result_store.store_result("sess2", raw)
    res = result_store.fetch_slice("sess2", rid, start=0, end=60)
    assert res["ok"] and res["mode"] == "range"
    assert res["content"] == raw[:60] and res["length"] == len(raw)


def test_fetch_slice_grep_recovers_dropped_line():
    # the signal line is buried in the middle — grep pulls just it
    lines = [f"noise {i}" for i in range(2000)]
    lines[900] = "ERROR: the needle"
    raw = "\n".join(lines)
    rid = result_store.store_result("sess3", raw, content_type="log")
    res = result_store.fetch_slice("sess3", rid, grep="needle")
    assert res["ok"] and res["mode"] == "grep"
    assert res["matches"] == 1 and "the needle" in res["content"]


def test_get_missing_returns_none():
    assert result_store.get_result("sess4", "r_nope") is None
    res = result_store.fetch_slice("sess4", "r_nope")
    assert res["ok"] is False


def test_store_bounded_eviction(monkeypatch):
    monkeypatch.setattr(result_store, "_MAX_PER_SESSION", 5)
    ids = [result_store.store_result("sessE", f"output {i}" * 100) for i in range(10)]
    # only the newest 5 survive; the oldest were evicted
    alive = [i for i in ids if result_store.get_result("sessE", i) is not None]
    assert len(alive) <= 5
    assert result_store.get_result("sessE", ids[-1]) is not None  # newest kept


def test_pathlike_id_rejected():
    assert result_store.get_result("sessP", "../escape") is None
    assert result_store.fetch_slice("sessP", "a/b")["ok"] is False


@pytest.mark.asyncio
async def test_builtin_tool_result_get_roundtrip(tmp_path, monkeypatch):
    """End-to-end: a bash run that overflows the cap stores raw + names a
    result_id; the tool_result_get builtin pulls the buried line back."""
    import personalclaw.config.loader as cfg
    import personalclaw.session_workspace as ws
    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider, _ok_capped

    # Build a large log-typed result the way a tool would, with a session key.
    big = "\n".join(["ok line"] * 5000 + ["ERROR: buried failure"] + ["ok line"] * 5000)
    res = _ok_capped(big, content_type="log", session_key="sessRT")
    assert res.truncated and res.metadata.get("raw_ref")
    rid = res.metadata["raw_ref"]
    assert f'tool_result_get(result_id="{rid}")' in res.output  # affordance named

    prov = NativeBuiltinToolProvider(cwd=tmp_path, session_key="sessRT")
    got = await prov.invoke("tool_result_get", {"result_id": rid, "grep": "buried"})
    assert got.success and "buried failure" in got.output


@pytest.mark.asyncio
async def test_tool_result_endpoint_canonicalizes_session_key(tmp_path, monkeypatch):
    """The "Show full result" UI button fetches with the BARE session id, but the
    projection write path keys the store by the canonical dashboard:-prefixed key
    (chat_runner sets session_key=_history_key_for(session.key)). The endpoint must
    canonicalize so the button resolves — else a stored result 404s as "expired".
    Regression guard for the projection UI-button key mismatch."""
    import personalclaw.config.loader as cfg
    import personalclaw.session_workspace as ws
    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard import chat_handlers

    # Store the raw the way a live turn does: under the dashboard:-prefixed key.
    raw = "\n".join(["ok line"] * 3000 + ["ERROR: buried"] + ["ok line"] * 3000)
    rid = result_store.store_result("dashboard:chat-9-42", raw, content_type="log", tool="bash")

    app = web.Application()
    app.router.add_get(
        "/api/chat/sessions/{session}/tool-result/{rid}",
        chat_handlers.api_chat_tool_result,
    )
    async with TestClient(TestServer(app)) as client:
        # UI form: BARE session id in the URL (what sessionRef.current holds).
        r = await client.get(f"/api/chat/sessions/chat-9-42/tool-result/{rid}?grep=buried")
        assert r.status == 200, "bare-id fetch must resolve via canonicalization (not 404)"
        body = await r.json()
        assert body["ok"] and "buried" in body["content"]
        # Already-prefixed form still works (idempotent canonicalization).
        r2 = await client.get(f"/api/chat/sessions/dashboard:chat-9-42/tool-result/{rid}")
        assert r2.status == 200
        # A genuinely-missing id still 404s.
        r3 = await client.get("/api/chat/sessions/chat-9-42/tool-result/r_nope")
        assert r3.status == 404
