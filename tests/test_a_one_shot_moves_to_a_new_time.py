"""A one-shot's time is edited like any other cadence: Edit shows it, and Save moves it.

The Triggers page's edit form sent the new time as `at` (epoch seconds, the create path's own
field) and the update read only `cron` and `every`, so a new time answered 200 and the row kept the
old one — the form had opened with the time blank, because the row never carried it either. A
reminder could be made for Monday 09:50 and never moved.

Both kinds of one-shot are covered: the page's (retires after its run) and the chat's (stays
listed after its run, carries a zone and an expiry). Each keeps what it is while its time moves,
and the next fire follows the new time.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.triggers import service as SVC
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    monkeypatch.setattr(T, "_hook_store", lambda s: _EmptyStore())
    monkeypatch.setattr(T, "_used_by_index", lambda: {})
    return tmp_path


class _EmptyStore:
    def list_all(self):
        return []

    def load(self):
        return []


@pytest.fixture
def state(home):
    from unittest.mock import MagicMock

    st = MagicMock()
    st.crons.list_jobs.return_value = []
    st._hook_store = None
    return st


def _req(method, path, state, *, body=None, match_info=None, headers=None):
    app = web.Application()
    app["state"] = state
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app, headers=headers)
    req["user"] = "tester"
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


def _body(resp):
    return json.loads(resp.body.decode())


def _store(home) -> TriggerStore:
    return TriggerStore(base_dir=home)


def _listed(state, raw_id: str) -> dict:
    resp = asyncio.run(T.api_triggers(_req("GET", "/api/triggers?type=schedule", state)))
    return next(r for r in _body(resp)["triggers"] if r.get("raw_id") == raw_id)


def _put(state, raw_id: str, body: dict):
    """The edit form's save: the whole draft, naming the revision the list read reported."""
    row = _listed(state, raw_id)
    return asyncio.run(
        T.api_trigger_detail(
            _req(
                "PUT",
                "/api/triggers/x",
                state,
                body=body,
                match_info={"id": f"schedule:{raw_id}"},
                headers={"If-Match": f'"{row["revision"]}"'},
            )
        )
    )


def _page_one_shot(state, at: float) -> str:
    """A one-shot made on the Triggers page, as the create form posts it."""
    resp = asyncio.run(
        T.api_trigger_create(
            _req(
                "POST",
                "/api/triggers",
                state,
                body={
                    "trigger_type": "schedule",
                    "name": "Handoff note",
                    "at": at,
                    "action": {"provider": "notify", "config": {"title_template": "Write it."}},
                },
            )
        )
    )
    assert resp.status == 200, _body(resp)
    return "clock:handoff-note"


def _chat_one_shot(home, at: float) -> str:
    """A one-time task the chat made (`set_onetime_task`): kept after its run, zoned, expiring."""
    from personalclaw.triggers import tools

    result = tools.create(
        _store(home),
        name="Stretch",
        kind="clock",
        spec={"kind": "at", "at": at, "delete_after_run": False, "timezone": "America/Toronto"},
        workflow={"provider": "notify", "config": {"title_template": "Stretch."}},
        created_by="agent",
    )
    assert result.ok, result.text
    return str(result.data["trigger"]["id"])


def _form_body(row: dict, at: object) -> dict:
    """What `draftToPayload` sends for a one-shot: the delivery block, and the time it shows."""
    return {
        "name": row["name"],
        "timezone": row.get("timezone") or "",
        "silent": bool(row.get("silent")),
        "strict_schedule": bool(row.get("strict_schedule")),
        "channel": row.get("channel") or "",
        "skip_dates": row.get("skip_dates") or [],
        "failure_delivery": row.get("failure_delivery", "inbox"),
        "failure_dedupe": bool(row.get("failure_dedupe")),
        "at": at,
    }


def test_the_row_carries_the_one_shots_time_so_edit_can_show_it(home, state):
    """🔴 Red on integration: the row had `cron_expr` and `every_secs` and no time for a one-shot,
    so the edit form opened with 'Run once at date and time' blank."""
    at = time.time() + 5 * 86400
    raw = _page_one_shot(state, at)
    assert _listed(state, raw)["at_ts"] == at


def test_a_page_one_shot_moves_to_the_new_time_and_its_next_fire_follows(home, state):
    """🔴 Red on integration: 200, and the store kept the old time and the old next fire."""
    at = time.time() + 5 * 86400
    raw = _page_one_shot(state, at)
    moved = time.time() + 2 * 3600

    resp = _put(state, raw, _form_body(_listed(state, raw), moved))

    assert resp.status == 200, _body(resp)
    trigger = _store(home).get(raw).trigger
    assert trigger.spec["at"] == moved
    # Still the page's one-shot: it leaves the list once its run has done its work.
    assert trigger.spec["delete_after_run"] is True
    assert SVC.to_epoch(trigger.next_fire_at) == pytest.approx(moved)
    assert _body(resp)["trigger"]["at_ts"] == moved


def test_a_chat_one_shot_moves_and_keeps_its_zone_its_kind_and_a_chance_to_run(home, state):
    """The chat's one-time task keeps its zone and stays listed after its run, and an expiry that
    would now end before its new time moves with it: a one-time task that expires before its own
    time never runs, and says nothing (the rule `set_onetime_task` itself follows)."""
    at = time.time() + 3600
    raw = _chat_one_shot(home, at)
    expires = SVC.to_epoch(_store(home).get(raw).trigger.expires_at)
    moved = expires + 3 * 86400

    resp = _put(state, raw, _form_body(_listed(state, raw), moved))

    assert resp.status == 200, _body(resp)
    trigger = _store(home).get(raw).trigger
    assert trigger.spec["at"] == moved
    assert trigger.spec["delete_after_run"] is False
    assert trigger.spec["timezone"] == "America/Toronto"
    assert SVC.to_epoch(trigger.next_fire_at) == pytest.approx(moved)
    assert SVC.to_epoch(trigger.expires_at) > moved


def test_saving_the_time_it_already_has_does_not_re_arm(home, state):
    """A rename sends the time back as it was read; the armed fire stays exactly where it is."""
    at = time.time() + 5 * 86400
    raw = _page_one_shot(state, at)
    before = _store(home).get(raw).trigger.next_fire_at
    body = _form_body(_listed(state, raw), at)
    body["name"] = "Handoff note, renamed"

    assert _put(state, raw, body).status == 200
    trigger = _store(home).get(raw).trigger
    assert trigger.name == "Handoff note, renamed"
    assert trigger.next_fire_at == before


def test_a_time_that_has_passed_is_refused_and_nothing_changes(home, state):
    """Moving a one-shot into the past would leave it listed and never firing, so the save says
    so instead, in the words the chat's one-time task uses."""
    at = time.time() + 5 * 86400
    raw = _page_one_shot(state, at)

    resp = _put(state, raw, _form_body(_listed(state, raw), time.time() - 3600))

    assert resp.status == 400
    assert "already passed" in json.dumps(_body(resp))
    assert _store(home).get(raw).trigger.spec["at"] == at


def test_a_one_shot_whose_time_has_passed_can_still_be_renamed(home, state):
    """The chat's one-time task stays listed after its run, and its form sends the time back with
    every save: that is its own time, not a new one, so it is not refused for having passed."""
    raw = _chat_one_shot(home, time.time() + 3600)
    store = _store(home)
    trigger = store.get(raw).trigger
    ran_at = time.time() - 60
    trigger.spec = {**trigger.spec, "at": ran_at}
    trigger.enabled, trigger.next_fire_at = False, ""
    store.upsert(trigger)
    body = _form_body(_listed(state, raw), ran_at)
    body["name"] = "Stretch, done"

    assert _put(state, raw, body).status == 200
    assert _store(home).get(raw).trigger.name == "Stretch, done"


def test_a_time_that_is_not_a_timestamp_is_refused(home, state):
    at = time.time() + 5 * 86400
    raw = _page_one_shot(state, at)

    resp = _put(state, raw, _form_body(_listed(state, raw), "2026-10-05T09:50"))

    assert resp.status == 400
    assert "'at'" in json.dumps(_body(resp))
    assert _store(home).get(raw).trigger.spec["at"] == at
