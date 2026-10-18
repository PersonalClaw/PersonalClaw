"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_chat_plan_mode.py``

Its checks:
``tests/rails/test_chat_plan_mode_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from aiohttp import web
from chat_test_helpers import _make_app

from personalclaw.dashboard import chat_plan


def _app(state) -> web.Application:
    """The chat app plus the six plan-mode routes (registered exactly as server.py does)."""
    app = _make_app(state)
    app.router.add_get("/api/chat/sessions/{session}/plan-session", chat_plan.api_chat_plan_session)
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/activate", chat_plan.api_chat_plan_activate
    )
    app.router.add_post("/api/chat/sessions/{session}/plan/edit", chat_plan.api_chat_plan_edit)
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/comment", chat_plan.api_chat_plan_comment
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/plan/approve", chat_plan.api_chat_plan_approve
    )
    app.router.add_post("/api/chat/sessions/{session}/plan/cancel", chat_plan.api_chat_plan_cancel)
    return app


def _seed(state, name="c1"):
    """A two-turn chat: the ordinary state a user is in when they reach for Plan."""
    s = state.get_or_create_session(name)
    s.append("user", "u1", "msg msg-u")
    s.append("assistant", "a1", "msg msg-a")
    s.drain()
    return s


def _no_dispatch(monkeypatch) -> list[str]:
    """Capture the prompts chat_plan would run a turn with, without running one."""
    seen: list[str] = []

    async def _fake_run_chat(state, session, msg, **kw):
        seen.append(msg)

    monkeypatch.setattr("personalclaw.dashboard.chat_runner.run_chat", _fake_run_chat)
    return seen
