"""`catch_up` round-trips: created, edited and shown, by the Triggers page and by the agent's tools.

What a missed time does is a real choice — run the 3am backup at 9am, or ask first — and it had no
control anywhere: the schedule form had no field, the create and edit handlers read none, the wire
row did not carry it, and the automation tool only listed the key as patchable with no word on what
it does. Every case here reads the value BACK from the store and the projection the UI renders,
because a write that answers 200 and keeps nothing is the failure this closes.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.triggers import tools as T
from personalclaw.triggers.schedule_view import to_schedule_row
from personalclaw.triggers.store import TriggerStore

_NOTIFY = {"provider": "notify", "config": {"title": "hi", "body": "there"}}


class _State:
    def push_refresh(self, *keys: str) -> None:
        pass


class _Req:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload

    def get(self, key: str, default: object = None) -> object:
        return default


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.handlers.triggers.config_dir", lambda: tmp_path)
    return tmp_path


async def _create(body: dict) -> tuple[int, dict]:
    from personalclaw.dashboard.handlers import triggers as handlers

    resp = await handlers._create_schedule(_State(), body, _Req(body))  # type: ignore[arg-type]
    return resp.status, json.loads(resp.body.decode())


def _update(raw: str, body: dict) -> tuple[int, dict]:
    from personalclaw.dashboard.handlers import triggers as handlers

    resp = handlers._update_schedule(_State(), raw, body)  # type: ignore[arg-type]
    return resp.status, json.loads(resp.body.decode())


def _stored(home, raw: str) -> bool:
    return TriggerStore(base_dir=home).get(raw).trigger.catch_up


# ── the Triggers page: create, edit, display ──


@pytest.mark.asyncio
async def test_create_and_edit_round_trip_catch_up(home):
    status, payload = await _create(
        {"name": "Nightly backup", "cron": "0 3 * * *", "catch_up": True, "action": _NOTIFY}
    )
    assert status == 200, payload
    row = payload["trigger"]
    raw = row["raw_id"]
    assert row["catch_up"] is True, "the create response shows it, in the list's projection"
    assert _stored(home, raw) is True

    for value in (False, True, False):
        status, payload = _update(raw, {"catch_up": value})
        assert status == 200, payload
        assert payload["trigger"]["catch_up"] is value
        assert _stored(home, raw) is value
        assert to_schedule_row(TriggerStore(base_dir=home).get(raw).trigger)["catch_up"] is value


@pytest.mark.asyncio
async def test_a_schedule_made_without_it_reviews_its_missed_times(home):
    status, payload = await _create({"name": "Digest", "every": 3600, "action": _NOTIFY})
    assert status == 200, payload
    assert payload["trigger"]["catch_up"] is False
    raw = payload["trigger"]["raw_id"]
    # An edit that does not name it leaves it as it is.
    status, payload = _update(raw, {"name": "Hourly digest"})
    assert status == 200 and payload["trigger"]["catch_up"] is False


@pytest.mark.asyncio
async def test_a_catch_up_that_is_not_true_or_false_is_refused_and_changes_nothing(home):
    status, payload = await _create(
        {"name": "Backup", "cron": "0 3 * * *", "catch_up": "false", "action": _NOTIFY}
    )
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert TriggerStore(base_dir=home).load() == []

    status, payload = await _create({"name": "Backup", "cron": "0 3 * * *", "action": _NOTIFY})
    raw = payload["trigger"]["raw_id"]
    status, payload = _update(raw, {"catch_up": "yes"})
    assert status == 400 and payload["error"]["code"] == "invalid_request"
    assert _stored(home, raw) is False


# ── the agent's tools ──


def test_the_tool_creates_and_patches_catch_up(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    made = T.create(
        store,
        name="Nightly backup",
        kind="clock",
        spec={"kind": "cron", "expr": "0 3 * * *"},
        workflow={"inline": dict(_NOTIFY)},
        created_by="user",
        owner_consented=True,
        catch_up=True,
    )
    assert made.ok, made.text
    tid = made.data["trigger"]["id"]
    assert store.get(tid).trigger.catch_up is True
    assert T.CATCH_UP_ON in made.text, "the announcement says what a missed time will do"

    refused = T.update(store, trigger_id=tid, patch={"catch_up": "yes"})
    assert not refused.ok and "true or false" in refused.text
    assert store.get(tid).trigger.catch_up is True
    assert T.update(store, trigger_id=tid, patch={"catch_up": False}).ok
    assert store.get(tid).trigger.catch_up is False


def test_catch_up_is_refused_for_an_automation_with_no_times():
    refusal = T.catch_up_refusal("event", True)
    assert refusal is not None and "no times to miss" in refusal.text
    assert T.catch_up_refusal("event", False) is None
    assert T.catch_up_refusal("clock", True) is None


def test_the_automation_tools_describe_catch_up_in_her_words():
    from personalclaw.mcp_automation import _list_tools
    from personalclaw.validation import MCP_AUTOMATION_SCHEMAS, validate_tool_args

    tools = {t["name"]: t for t in _list_tools()}
    prop = tools["automation_create"]["inputSchema"]["properties"]["catch_up"]
    assert prop["type"] == "boolean"
    assert T.CATCH_UP_OFF in prop["description"]
    assert T.CATCH_UP_ON in prop["description"]
    assert "catch_up" in tools["automation_update"]["description"]
    assert T.CATCH_UP_OFF in tools["automation_update"]["description"]
    # The argument validator takes it, or the schema would offer a field every call is refused for.
    cleaned = validate_tool_args(
        {"name": "Backup", "catch_up": True}, MCP_AUTOMATION_SCHEMAS["automation_create"]
    )
    assert cleaned["catch_up"] is True
