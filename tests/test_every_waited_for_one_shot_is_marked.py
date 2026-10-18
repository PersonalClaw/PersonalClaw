"""Every one-shot call somebody waits for says so, so a busy local model serves it first.

A call is waited for when a chat turn's tool is blocked on it or a page is waiting on its answer.
Unmarked, it waits behind background work (``guardrails.local_queue``), which is how a schedule
took six minutes and a task analysis eight and a half. The census below is that list: a new
waited-for caller joins it, and a site that drops its mark reds here.
"""

from __future__ import annotations

import asyncio

from every_waited_for_one_shot_is_marked_shared import _SRC


def test_a_models_test_is_waited_for():
    """Settings → Models' Test calls the model itself rather than through a one-shot, so the call
    binds who waits around it."""
    source = (_SRC / "providers/model_test.py").read_text(encoding="utf-8")
    assert 'with attending(Attended("Testing the model")):' in source


def test_the_report_delivered_by_hand_is_waited_for():
    """The identity report's narration runs for the page's Deliver button and for the scheduled
    delivery; only the button's is waited for, so the route binds it around the delivery."""
    source = (_SRC / "dashboard/handlers/learning.py").read_text(encoding="utf-8")
    assert 'with attending(Attended("Writing the report")):' in source


def test_the_schedule_a_chat_waits_for_names_its_chat(monkeypatch):
    from personalclaw import mcp_core
    from personalclaw.nl_to_cron import nl_to_cron

    seen: dict = {}

    async def _fake(prompt, *, use_case="background", **kw):
        seen.update(kw)
        return "10 15 * * 1,2,4"

    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", _fake)
    token = mcp_core.set_current_session_key("dashboard:chat-12")
    try:
        schedule = asyncio.run(nl_to_cron("every Monday, Tuesday and Thursday at 15:10"))
    finally:
        mcp_core.reset_current_session_key(token)

    assert schedule.expr == "10 15 * * 1,2,4"
    who = seen["attended"]
    assert who.step == "Working out the schedule"
    assert who.session == "dashboard:chat-12"


def test_the_loop_composers_analysis_is_waited_for(monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import loop_routes

    seen: dict = {}

    async def _fake(prompt, *, use_case="background", **kw):
        seen.update(kw)
        return "{}"

    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", _fake)
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: True
    )
    app = web.Application()
    request = make_mocked_request("POST", "/api/loops/classify", app=app)

    async def _json():
        return {"kind": "goal", "task": "write a regression test for the double escape"}

    request.json = _json  # type: ignore[assignment]
    response = asyncio.run(loop_routes.api_loop_classify(request))

    assert response.status == 200
    assert seen["attended"].step == "Analyzing the task"
