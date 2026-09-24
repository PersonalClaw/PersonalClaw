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


# ── Context Economy §1.1: content-addressed ids + idempotent storage ──────────


def test_content_hash_id_form():
    rid = result_store.store_result("sessH", "some raw output")
    # r_ + 12 hex sha prefix
    assert rid.startswith("r_") and len(rid) == 14
    assert all(c in "0123456789abcdef" for c in rid[2:])


def test_identical_content_dedupes_to_one_file():
    raw = "the same big output\n" * 500
    rid1 = result_store.store_result("sessD", raw, content_type="log")
    rid2 = result_store.store_result("sessD", raw, content_type="log")
    assert rid1 == rid2  # content-addressed → same id
    # exactly one file on disk for that content
    import personalclaw.session_workspace as ws

    store_dir = ws.workspace_dir("sessD") / "tool_results"
    assert len(list(store_dir.glob(f"{rid1}.json"))) == 1


def test_different_content_distinct_ids():
    a = result_store.store_result("sessX", "output one")
    b = result_store.store_result("sessX", "output two")
    assert a != b


# ── Context Economy §1.2: line addressing ─────────────────────────────────────


def test_fetch_slice_line_range():
    raw = "\n".join(f"line{i}" for i in range(1, 101))  # 100 lines
    rid = result_store.store_result("sessL", raw)
    res = result_store.fetch_slice("sessL", rid, line_start=10, line_end=12)
    assert res["ok"] and res["mode"] == "lines"
    assert res["content"] == "line10\nline11\nline12"
    assert res["line_start"] == 10 and res["line_end"] == 12 and res["total_lines"] == 100


def test_fetch_slice_line_start_only():
    raw = "\n".join(f"L{i}" for i in range(1, 21))
    rid = result_store.store_result("sessL2", raw)
    res = result_store.fetch_slice("sessL2", rid, line_start=18)
    assert res["ok"] and res["content"] == "L18\nL19\nL20"


def test_fetch_slice_line_start_past_end_errors():
    rid = result_store.store_result("sessL3", "one\ntwo\nthree")
    res = result_store.fetch_slice("sessL3", rid, line_start=99)
    assert res["ok"] is False and "past the result" in res["error"]


def test_line_and_char_modes_mutually_exclusive():
    # Passing a line range routes to line mode and ignores char start/end.
    raw = "\n".join(f"row{i}" for i in range(1, 6))
    rid = result_store.store_result("sessL4", raw)
    res = result_store.fetch_slice("sessL4", rid, start=0, end=3, line_start=2, line_end=3)
    assert res["mode"] == "lines" and res["content"] == "row2\nrow3"


# ── OP4-analog: projection retains raw with NO double-loss on the new id form ──


def test_project_and_retain_no_double_loss_content_hash():
    # A large log projects to a preview AND retains the full raw under a content-hash id;
    # the buried line is recoverable — the OP4 "no double loss" contract on the new id.
    from personalclaw.tool_providers.projection import project_and_retain

    lines = [f"ok {i}" for i in range(5000)]
    lines[2500] = "ERROR: buried needle"
    raw = "\n".join(lines)
    preview, meta = project_and_retain(raw, session_key="sessND", content_type="log", cap=1000)
    assert meta["truncated"] and meta["raw_ref"].startswith("r_")
    # the needle is NOT in the preview (it was past the head) but IS recoverable
    res = result_store.fetch_slice("sessND", meta["raw_ref"], grep="needle")
    assert res["ok"] and "buried needle" in res["content"]
    # storing the identical projection again reuses the same raw file (idempotent)
    _, meta2 = project_and_retain(raw, session_key="sessND", content_type="log", cap=1000)
    assert meta2["raw_ref"] == meta["raw_ref"]


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
    # Affordance named — the recovery hint now names all three access modes (§1.2):
    # a char/line range or grep. Assert the id + the line-range mode are surfaced.
    assert f'tool_result_get(result_id="{rid}"' in res.output
    assert "line_start" in res.output and "grep" in res.output

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
