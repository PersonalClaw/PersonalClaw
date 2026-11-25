"""`POST /api/triggers/preview` reads a schedule as the save reads it, and answers its next runs.

The form asks on every pause in typing, so a spec the save would refuse is a 200 whose outcome
says so; only a body that is not a schedule is a 400, and an id that names nothing is a 404. The
route is the owner's alone, and it writes nothing.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers.triggers import api_triggers_preview


def _app() -> web.Application:
    app = web.Application()
    app["state"] = MagicMock()
    app.router.add_post("/api/triggers/preview", api_triggers_preview)
    return app


def _post(body):
    async def go():
        async with TestClient(TestServer(_app())) as c:
            resp = await c.post("/api/triggers/preview", json=body)
            return resp.status, await resp.json()

    return asyncio.run(go())


def test_a_weekday_cron_answers_its_next_runs() -> None:
    status, body = _post({"cron": "0 9 * * 1-5", "timezone": "America/New_York", "count": 3})
    assert status == 200, body
    assert body["outcome"] == "ok" and body["zone"] == "America/New_York"
    assert len(body["items"]) == 3 and all(i["runs"] for i in body["items"])
    assert body["next_cursor"] is None
    assert {"at", "wall", "runs", "note_kind", "note"} <= set(body["items"][0])


def test_a_spec_the_save_would_refuse_is_a_200_that_says_so() -> None:
    status, body = _post({"cron": "99 99 * * *"})
    assert status == 200, body
    assert body["outcome"] == "refused" and body["problem"]
    assert body["items"] == []


def test_a_one_time_run_that_has_passed_never_runs() -> None:
    status, body = _post({"at": time.time() - 60, "timezone": "UTC"})
    assert status == 200, body
    assert (body["outcome"], body["problem"]) == ("never", "That time has passed.")


def test_a_body_that_is_not_a_schedule_is_a_400() -> None:
    for sent in (
        {"count": 11, "cron": "0 9 * * *"},
        {"count": 0, "cron": "0 9 * * *"},
        {"timezone": "UTC"},
        {"every": "often"},
    ):
        status, body = _post(sent)
        assert status == 400, (sent, body)
        assert body["error"]["code"] == "invalid_request", (sent, body)


def test_an_id_that_names_nothing_is_a_404() -> None:
    status, body = _post({"trigger_id": "schedule:no-such-automation", "cron": "0 9 * * *"})
    assert status == 404
    assert body["error"] == {"code": "not_found", "message": "No automation with that id."}


def test_an_automation_being_edited_is_previewed_with_its_own_spec() -> None:
    from personalclaw.dashboard.handlers.triggers import _trigger_store
    from personalclaw.triggers.models import Trigger

    store = _trigger_store()
    store.upsert(
        Trigger(
            id="clock:morning-summary",
            name="Morning summary",
            kind="clock",
            spec={
                "kind": "cron",
                "expr": "0 9 * * *",
                "timezone": "Europe/London",
                "skip_dates": [],
            },
        )
    )
    status, body = _post({"trigger_id": "schedule:clock:morning-summary", "count": 2})
    assert status == 200, body
    assert body["zone"] == "Europe/London" and len(body["items"]) == 2
    status, body = _post({"trigger_id": "clock:morning-summary", "cron": "0 7 * * *", "count": 1})
    assert status == 200, body
    assert body["items"][0]["wall"].endswith("07:00"), "the body's cadence over the stored one"
    assert (
        store.get("clock:morning-summary").trigger.spec["expr"] == "0 9 * * *"
    ), "it writes nothing"


def test_a_cron_no_calendar_reaches_answers_never_in_plain_words() -> None:
    for cron, why in (
        ("0 0 30 2 *", "February has no 30th."),
        ("* * 31 2 *", "February has no 31st."),
        ("0 9 31 4,6 *", "April and June have no 31st."),
        ("0 0 30 2 1", "February has no 30th."),
    ):
        status, body = _post({"cron": cron, "timezone": "UTC"})
        assert status == 200, (cron, body)
        assert (body["outcome"], body["problem"], body["items"]) == ("never", why, []), cron


def test_a_saved_automation_no_calendar_reaches_previews_never() -> None:
    # Saved before the save refused one: it previews the same plain answer, and nothing raises.
    from personalclaw.dashboard.handlers.triggers import _trigger_store
    from personalclaw.triggers.models import Trigger

    _trigger_store().upsert(
        Trigger(
            id="clock:leap-report",
            name="Leap report",
            kind="clock",
            spec={"kind": "cron", "expr": "0 0 30 2 *", "timezone": "UTC", "skip_dates": []},
        )
    )
    status, body = _post({"trigger_id": "clock:leap-report"})
    assert status == 200, body
    assert (body["outcome"], body["problem"]) == ("never", "February has no 30th.")


def test_a_walk_the_stepper_refuses_answers_never(monkeypatch) -> None:
    # Past the named days, a refusal from the stepper itself is a "never", not a 500.
    import personalclaw.cron_clock as cron_clock

    monkeypatch.setattr(cron_clock, "never_runs", lambda expr: "")
    status, body = _post({"cron": "0 0 30 2 *", "timezone": "UTC"})
    assert status == 200, body
    assert (body["outcome"], body["problem"]) == ("never", cron_clock.NO_DATE)


def test_the_route_is_the_owner_s_alone() -> None:
    from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly

    assert isinstance(ROUTE_AUTHZ["POST /api/triggers/preview"], OwnerOnly)
