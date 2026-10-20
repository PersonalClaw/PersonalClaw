"""The ``/rewind-to-turn`` HTTP surface: preview before write, confirm to apply.

A rewind is destructive, so the two properties under test are (1) the preview endpoint
writes nothing, and (2) the apply endpoint refuses without an explicit confirm — and
refuses by returning the preview, not an error the user has to guess at.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from personalclaw import turn_checkpoints as tc
from personalclaw.dashboard.chat import (
    api_chat_session_rewind,
    api_chat_session_rewind_preview,
)


def _app(state) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/chat/sessions/{session}/rewind", api_chat_session_rewind_preview)
    app.router.add_post("/api/chat/sessions/{session}/rewind", api_chat_session_rewind)
    return app


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def scene(tmp_path, monkeypatch):
    """A session with two turns and three files mangled in turn 2."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    session = state.get_or_create_session("s1")
    ws = tmp_path / "ws"
    ws.mkdir()
    names = ("one.py", "two.txt", "three.json")
    for n in names:
        (ws / n).write_text(f"original {n}\n", encoding="utf-8")
    (ws / ".env").write_text("TOKEN=EI8-API-CANARY-9f8e7d6c\n", encoding="utf-8")
    originals = {str(ws / n): _sha(ws / n) for n in names}

    tc.begin_turn(session.key, cwd=ws)
    tc.begin_turn(session.key, cwd=ws)
    for n in names:
        tc.capture_pre_edit(session.key, ws / n, cwd=ws)
        (ws / n).write_text("MANGLED\n", encoding="utf-8")
    tc.capture_pre_edit(session.key, ws / ".env", cwd=ws)
    (ws / ".env").write_text("TOKEN=clobbered\n", encoding="utf-8")
    return state, session, ws, originals


@pytest.mark.asyncio
async def test_preview_lists_exactly_the_mangled_files_and_writes_nothing(scene):
    state, _session, ws, originals = scene
    before = {p: _sha(p) for p in ws.rglob("*") if p.is_file()}
    async with TestClient(TestServer(_app(state))) as client:
        r = await client.get("/api/chat/sessions/s1/rewind?turn=1")
        assert r.status == 200
        body = await r.json()
    restores = {f["path"] for f in body["files"] if f["action"] == "restore"}
    assert restores == set(originals), restores
    assert all(f["diff"] for f in body["files"] if f["action"] == "restore")
    # The .env is reported honestly as never captured, not silently omitted.
    env = [f for f in body["files"] if f["path"].endswith(".env")]
    assert env and env[0]["action"] == "not_captured" and env[0]["reason"] == "secret"
    assert "does NOT rewind the conversation" in body["notice"]
    assert {p: _sha(p) for p in ws.rglob("*") if p.is_file()} == before


@pytest.mark.asyncio
async def test_apply_without_confirm_is_refused_and_returns_the_preview(scene):
    state, _session, ws, _originals = scene
    before = {p: _sha(p) for p in ws.rglob("*") if p.is_file()}
    async with TestClient(TestServer(_app(state))) as client:
        r = await client.post("/api/chat/sessions/s1/rewind", json={"turn": 1})
        assert r.status == 409
        body = await r.json()
    assert body["error"]["code"] == "confirmation_required"
    assert body["preview"]["files"], "the refusal must carry the preview, not just an error"
    assert {p: _sha(p) for p in ws.rglob("*") if p.is_file()} == before, "nothing may be written"


@pytest.mark.asyncio
async def test_apply_with_confirm_restores_byte_identical(scene):
    state, _session, _ws, originals = scene
    async with TestClient(TestServer(_app(state))) as client:
        r = await client.post("/api/chat/sessions/s1/rewind", json={"turn": 1, "confirm": True})
        assert r.status == 200, await r.text()
        body = await r.json()
    assert body["ok"] is True
    assert set(body["restored"]) == set(originals)
    assert body["safety_turn"] > 0
    for path, want in originals.items():
        assert _sha(Path(path)) == want


@pytest.mark.asyncio
async def test_a_confirmed_rewind_does_not_restore_the_dotenv(scene):
    state, _session, ws, _originals = scene
    async with TestClient(TestServer(_app(state))) as client:
        r = await client.post("/api/chat/sessions/s1/rewind", json={"turn": 1, "confirm": True})
        assert r.status == 200
    assert (ws / ".env").read_text(encoding="utf-8") == "TOKEN=clobbered\n"


@pytest.mark.asyncio
async def test_a_bad_turn_and_a_missing_session_use_the_stable_error_envelope(scene):
    state, _session, _ws, _originals = scene
    async with TestClient(TestServer(_app(state))) as client:
        r = await client.get("/api/chat/sessions/s1/rewind?turn=notanint")
        assert r.status == 400 and (await r.json())["error"]["code"] == "invalid_turn"
        r = await client.get("/api/chat/sessions/nope/rewind?turn=0")
        assert r.status == 404 and (await r.json())["error"]["code"] == "not_found"
        r = await client.post("/api/chat/sessions/s1/rewind", json={})
        assert r.status == 400 and (await r.json())["error"]["code"] == "invalid_turn"


@pytest.mark.asyncio
async def test_a_rewind_is_refused_while_a_turn_is_running(scene):
    state, session, ws, _originals = scene
    # `running` is a derived property (task is not None and not done) — so a live turn is
    # simulated by giving the session an unfinished task, the way the real code sees it.
    session.task = asyncio.get_running_loop().create_future()
    assert session.running is True
    before = {p: _sha(p) for p in ws.rglob("*") if p.is_file()}
    async with TestClient(TestServer(_app(state))) as client:
        r = await client.post("/api/chat/sessions/s1/rewind", json={"turn": 1, "confirm": True})
        assert r.status == 409 and (await r.json())["error"]["code"] == "turn_running"
    assert {p: _sha(p) for p in ws.rglob("*") if p.is_file()} == before


@pytest.mark.asyncio
async def test_the_preview_finishes_a_rewind_that_died_mid_commit(scene, monkeypatch):
    """A half-committed tree must not outlive the next interaction with the store."""
    state, session, ws, originals = scene
    real_replace = tc.os.replace
    calls = {"n": 0}
    targets = set(originals)

    def flaky(src, dst):
        if str(dst) in targets:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError(5, "simulated mid-commit failure")
        return real_replace(src, dst)

    monkeypatch.setattr(tc.os, "replace", flaky)
    async with TestClient(TestServer(_app(state))) as client:
        r = await client.post("/api/chat/sessions/s1/rewind", json={"turn": 1, "confirm": True})
        assert r.status == 500
        assert (await r.json())["error"]["code"] == "rewind_incomplete"
        assert tc.pending_rewinds(session.key), "the journal must survive the failure"
        monkeypatch.setattr(tc.os, "replace", real_replace)
        r = await client.get("/api/chat/sessions/s1/rewind?turn=1")
        assert r.status == 200
        assert (await r.json())["resumed"], "the preview must report the resume it performed"
    assert not tc.pending_rewinds(session.key)
    for path, want in originals.items():
        assert _sha(Path(path)) == want


@pytest.mark.asyncio
async def test_a_file_changed_with_no_backup_is_listed_and_the_notice_says_it_was_left(scene):
    """A file a command changed in the turn being undone had no backup. The preview lists it as
    not restored, and once applied the notice says how many files were left as they are, beside
    the count of those restored, so "Restored 3 file(s)" is not read as the whole story."""
    state, _session, ws, originals = scene
    by_a_command = ws / "notes.md"
    by_a_command.write_text("written by a command, never backed up\n", encoding="utf-8")
    async with TestClient(TestServer(_app(state))) as client:
        r = await client.get("/api/chat/sessions/s1/rewind?turn=1")
        body = await r.json()
        listed = {f["path"]: f for f in body["files"]}
        entry = listed.get(str(by_a_command.resolve()))
        assert entry and (entry["action"], entry["reason"]) == ("not_captured", "created"), body
        r = await client.post("/api/chat/sessions/s1/rewind", json={"turn": 1, "confirm": True})
        assert r.status == 200, await r.text()
        applied = await r.json()
    assert set(applied["restored"]) == set(originals)
    assert applied["notice"].startswith(f"Restored {len(originals)} file(s)"), applied["notice"]
    assert "1 file(s) that changed after turn 1 with no backup were left as they are" in (
        applied["notice"]
    )
    assert by_a_command.read_text(encoding="utf-8") == "written by a command, never backed up\n"
