"""Opening a chat that lives only on disk reads its plan session, instead of a 404.

The chat page mounts its plan review gate (`ui/chat/ChatPlanGate.tsx`) for every chat, and the
gate reads ``GET /api/chat/sessions/{session}/plan-session`` at once to learn whether there is a
plan to show. That route looked the chat up among the sessions the gateway holds in memory
(``state._sessions``) and answered ``404 session_not_found`` for any other. A chat you brought
over from another tool is on disk and in the history list, but nothing loads it until you open it
— and the page's own read of the transcript, which is what loads it, waits for the page's socket
first. So the gate's read always arrived first, and every imported chat logged a 404 in the
console before a second read (after the transcript painted) got ``{"session": null}``. A chat
from before a restart that was not restored did the same.

The chat exists; the answer is its plan session, read from where it is kept
(``config_dir()/chat_plans``), which is ``null`` for one that never used plan mode. The route now
finds the chat the way the transcript read and the session map do (``resolve_session``: in
memory, or loaded from history), and still answers 404 for a key that names no chat.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _api_app, _make_state

import personalclaw.config.loader as loader
from personalclaw.dashboard import chat_plan
from personalclaw.dashboard.chat_persistence import resolve_session
from personalclaw.history import import_conversation
from personalclaw.planning import session as PS

KEY = "claude-code-83c14d7c-1d87-46d5-ae48-c8a43655fda7"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    return tmp_path


def _state(home):
    # The transcript log reads the same `sessions/` the importer writes into.
    return _make_state(home / "sessions")


def _app(state) -> web.Application:
    app = _api_app(state)
    app.router.add_get("/api/chat/sessions/{session}/plan-session", chat_plan.api_chat_plan_session)
    return app


def _import(key: str = KEY) -> None:
    """What `onboarding_import.writers._write_conversation` writes for a Claude Code session."""
    import_conversation(
        f"dashboard_{key}",
        metadata={"created_at": "2026-07-10T21:38:00Z", "title": "Plan the Portugal trip"},
        messages=[
            {"role": "user", "content": "Plan Oct 23 to Nov 1", "ts": "", "cls": "msg msg-u"},
            {"role": "assistant", "content": "Here is a plan.", "ts": "", "cls": "msg msg-a"},
        ],
    )


@pytest.mark.asyncio
async def test_an_imported_chat_answers_that_it_has_no_plan_session(home) -> None:
    _import()
    state = _state(home)
    assert KEY not in state._sessions, "precondition: nothing has loaded the imported chat yet"

    async with TestClient(TestServer(_app(state))) as c:
        resp = await c.get(f"/api/chat/sessions/{KEY}/plan-session")
        assert resp.status == 200, await resp.text()
        body = await resp.json()
    assert body["session"] is None
    assert body["awaiting_step_id"] == ""


@pytest.mark.asyncio
async def test_a_chat_back_from_a_restart_shows_the_plan_it_was_waiting_on(home) -> None:
    """Not just "null for everything": a chat that is only on disk reads the plan it has."""
    _import()
    before = _state(home)
    chat = resolve_session(before, KEY)  # the chat opened, before the restart
    sess, binding = chat_plan.activate(chat, running=False)
    step = sess.steps[0]
    assert PS.submit_artifact(sess, step.id, {"markdown": "1. Lisbon\n2. Porto"})
    chat_plan.write(sess, binding)

    after = _state(home)  # the gateway restarted, and this chat was not restored
    assert KEY not in after._sessions
    async with TestClient(TestServer(_app(after))) as c:
        resp = await c.get(f"/api/chat/sessions/{KEY}/plan-session")
        assert resp.status == 200, await resp.text()
        body = await resp.json()
    assert body["awaiting_step_id"] == step.id
    assert body["session"]["steps"][0]["artifact"]["markdown"] == "1. Lisbon\n2. Porto"


@pytest.mark.asyncio
async def test_a_key_that_names_no_chat_is_still_not_found(home) -> None:
    async with TestClient(TestServer(_app(_state(home)))) as c:
        resp = await c.get("/api/chat/sessions/nothing-here/plan-session")
        assert resp.status == 404
        assert (await resp.json())["error"]["code"] == "session_not_found"
