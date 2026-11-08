"""This chat's own explanations choice: its route, its meta line and the session's detail.

``PATCH /api/chat/sessions/{session}/explain`` sets this chat's choice (``""`` follows the owner's
default again) and answers with the re-resolved state, as the reply style's route does. An absent
or unknown value is refused and keeps the choice already made. The choice rides the session's meta
line across a restart, and the detail carries it beside the reply style. An app may set it only on
a chat it owns, as it may the reply style.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw.config.loader import AppConfig


@pytest.fixture()
def chat_app(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return _make_state(tmp_path)


def _home_default(explain: str) -> None:
    cfg = AppConfig.load()
    cfg.agent.explain = explain
    cfg.save()


@pytest.mark.asyncio
async def test_the_patch_sets_this_chats_choice_and_answers_the_resolved_state(chat_app):
    chat_app.get_or_create_session("ex")
    async with TestClient(TestServer(_make_app(chat_app))) as client:
        resp = await client.patch("/api/chat/sessions/ex/explain", json={"explain": "step_by_step"})
        assert resp.status == 200
        body = await resp.json()
    assert (body["explain"], body["explain_effective"], body["explain_source"]) == (
        "step_by_step",
        "step_by_step",
        "conversation",
    )
    assert body["explain_default"] == "default"
    assert chat_app._sessions["ex"].explain == "step_by_step"


@pytest.mark.asyncio
async def test_an_absent_or_unknown_value_is_refused_and_keeps_the_choice(chat_app):
    session = chat_app.get_or_create_session("ex")
    session.explain = "briefly"
    async with TestClient(TestServer(_make_app(chat_app))) as client:
        for body in ({}, {"explain": "sideways"}, {"explain": 3}, {"explain": None}):
            resp = await client.patch("/api/chat/sessions/ex/explain", json=body)
            assert resp.status == 400, body
    assert session.explain == "briefly"


@pytest.mark.asyncio
async def test_an_empty_choice_follows_the_homes_default_again(chat_app):
    _home_default("with_background")
    session = chat_app.get_or_create_session("ex")
    session.explain = "briefly"
    async with TestClient(TestServer(_make_app(chat_app))) as client:
        resp = await client.patch("/api/chat/sessions/ex/explain", json={"explain": ""})
        body = await resp.json()
    assert session.explain == ""
    assert (
        body["explain"],
        body["explain_effective"],
        body["explain_source"],
        body["explain_default"],
    ) == (
        "",
        "with_background",
        "platform",
        "with_background",
    )


@pytest.mark.asyncio
async def test_an_unknown_chat_is_not_found(chat_app):
    async with TestClient(TestServer(_make_app(chat_app))) as client:
        resp = await client.patch("/api/chat/sessions/nope/explain", json={"explain": "briefly"})
        assert resp.status == 404


def test_the_choice_survives_a_meta_round_trip_and_a_restore(chat_app):
    from personalclaw.dashboard.chat_persistence import (
        restore_recent_sessions,
        save_session_to_history,
    )

    session = chat_app.get_or_create_session("ex")
    session.explain = "with_background"
    session.messages.append({"role": "user", "content": "hi"})
    save_session_to_history(chat_app, session)
    assert (
        chat_app.conversation_log.get_metadata("dashboard:ex").get("explain") == "with_background"
    )
    chat_app._sessions.clear()
    restore_recent_sessions(chat_app)
    assert chat_app._sessions["ex"].explain == "with_background"


def test_a_chat_that_states_none_writes_no_explain_to_its_meta_line(chat_app):
    from personalclaw.dashboard.chat_persistence import save_session_to_history

    session = chat_app.get_or_create_session("ex")
    session.messages.append({"role": "user", "content": "hi"})
    save_session_to_history(chat_app, session)
    assert "explain" not in chat_app.conversation_log.get_metadata("dashboard:ex")


def test_a_hand_edited_meta_line_restores_only_a_known_choice(chat_app):
    from personalclaw.dashboard.chat_persistence import (
        restore_recent_sessions,
        save_session_to_history,
    )

    session = chat_app.get_or_create_session("ex")
    session.messages.append({"role": "user", "content": "hi"})
    save_session_to_history(chat_app, session)
    chat_app.conversation_log.update_metadata("dashboard:ex", {"explain": "sideways"})
    chat_app._sessions.clear()
    restore_recent_sessions(chat_app)
    assert chat_app._sessions["ex"].explain == ""


def test_an_app_may_set_it_only_on_a_chat_it_owns():
    from personalclaw.apps import permissions

    explain = permissions.ROUTE_AUTHZ["PATCH /api/chat/sessions/{session}/explain"]
    style = permissions.ROUTE_AUTHZ["PATCH /api/chat/sessions/{session}/reply-style"]
    assert explain == style
