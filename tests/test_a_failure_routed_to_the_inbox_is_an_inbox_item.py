"""A trigger's failure routed to the Inbox is an Inbox item; a result stays a notification.

The Triggers page offers "If it fails: Inbox" (the default, "Failures can reach you even when
results stay silent"), and the automations guide promises that a run that fails reaches the Inbox.
It did not: a failure routed to the Inbox was a notification, in the bell and nowhere else, so the
Inbox never listed it and nothing counted it as waiting on the owner. The same page says a result
"still reaches the dashboard", and that was true.

Now a failure whose route is the Inbox is filed there, as one item whose notification is its view
(one bell entry, under the owner's own rule for the kind). "Same as results" follows the results,
"Don't tell me" says nothing, and a result is still only a notification.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import personalclaw.action_providers as AP
from personalclaw import notification_kinds
from personalclaw.action_providers.base import ActionResult
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.inbox import InboxStore
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

TRIGGER_ID = "clock:feed-digest"
_ACTION = "create-task"
FAILED = ActionResult(success=False, exit_code=2, stderr="feedsmith: error: feeds.opml not found")


class _State:
    def __init__(self) -> None:
        self.notes: list[dict[str, Any]] = []

    def notify(self, kind, title, body, *, meta=None, raised_by_app=""):
        self.notes.append({"kind": kind, "title": title, "body": body, "meta": dict(meta or {})})

    def broadcast_ws(self, *_a: Any, **_k: Any) -> None:
        return None


class _Result:
    def __init__(self, result: ActionResult) -> None:
        self.result = result

    async def execute(self, config, ctx, timeout=30):
        return self.result


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _store(home, *, delivery: str = "inbox", failure_delivery: str = "inbox") -> None:
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=TRIGGER_ID,
            name="feed digest",
            kind="clock",
            enabled=True,
            spec={"kind": "cron", "expr": "45 6 * * *"},
            delivery=delivery,
            failure_delivery=failure_delivery,
            capabilities={"providers": [_ACTION]},
            workflow={"inline": {"provider": _ACTION, "config": {}}},
        )
    )


def _orchestrator(state: _State) -> GatewayOrchestrator:
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = state
    return orch


def _fire(home, monkeypatch, result: ActionResult, **routes: str) -> _State:
    _store(home, **routes)
    monkeypatch.setattr(AP, "get_action_provider", lambda name: _Result(result))
    state = _State()
    trigger = TriggerStore(base_dir=home).get(TRIGGER_ID).trigger
    asyncio.run(_orchestrator(state)._fire_store_trigger(trigger, {"trigger_id": TRIGGER_ID}))
    return state


def _items(home) -> list[Any]:
    store = InboxStore()
    store.load()
    return list(store.items.values())


def test_a_failure_routed_to_the_inbox_is_filed_there(home, monkeypatch):
    """🔴 Before: no Inbox item at all; the failure was one bell entry."""
    state = _fire(home, monkeypatch, FAILED)
    [item] = _items(home)
    assert item.message == (
        "feed digest failed\n\nexited with code 2: feedsmith: error: feeds.opml not found"
    )
    assert item.item_kind == "system"
    assert item.refs["trigger"] == TRIGGER_ID
    # Its one notification is the item's view: one bell entry, linked to the item and the trigger.
    [note] = state.notes
    assert note["kind"] == notification_kinds.CRON_FAILED
    assert note["title"] == "feed digest failed"
    assert note["meta"]["inbox_item"] == item.id
    assert note["meta"]["statusUrl"] == f"#/triggers?open={TRIGGER_ID}"
    assert note["meta"]["event"] == "automation.run.failed"


def test_an_agent_the_trigger_started_that_failed_is_filed_too(home):
    """The work a fire started reports on the same route when it ends."""
    _store(home)
    state = _State()
    reported = _orchestrator(state)._report_to_its_trigger(
        TRIGGER_ID, error="the model provider answered 503"
    )
    assert reported is True
    [item] = _items(home)
    assert item.message == "feed digest failed\n\nthe model provider answered 503"
    assert [n["meta"]["inbox_item"] for n in state.notes] == [item.id]


def test_same_as_results_goes_where_results_go(home, monkeypatch):
    """A blank failure route follows the results, and a result is a notification."""
    state = _fire(home, monkeypatch, FAILED, failure_delivery="")
    assert _items(home) == []
    [note] = state.notes
    assert note["title"] == "feed digest failed"
    assert "inbox_item" not in note["meta"]


def test_dont_tell_me_says_nothing(home, monkeypatch):
    state = _fire(home, monkeypatch, FAILED, failure_delivery="none")
    assert _items(home) == [] and state.notes == []


def test_a_result_is_a_notification_not_an_inbox_item(home, monkeypatch):
    """The page's words for a result are "It still reaches the dashboard": the bell."""
    state = _fire(home, monkeypatch, ActionResult(success=True, stdout="23 feeds: 3 new entries"))
    assert _items(home) == []
    [note] = state.notes
    assert (note["title"], note["body"]) == ("feed digest finished", "23 feeds: 3 new entries")


def test_a_redelivered_failure_is_filed_once(home):
    """The same event redelivered is not a second item (`delivery.event_id`)."""
    from personalclaw.triggers import delivery as D

    _store(home)
    trigger = TriggerStore(base_dir=home).get(TRIGGER_ID).trigger
    state = _State()
    note = D.build_delivery(
        trigger_id=TRIGGER_ID,
        trigger_name="feed digest",
        ok=False,
        summary="exited with code 2",
        destination=D.route_for(trigger, ok=False),
        inbox=D.files_in_inbox(trigger, ok=False),
        run_id="r1",
    )
    seen: set[str] = set()
    assert D.deliver(state, note, delivered_ids=seen) is True
    assert D.deliver(state, note, delivered_ids=seen) is False
    assert len(_items(home)) == 1 and len(state.notes) == 1
