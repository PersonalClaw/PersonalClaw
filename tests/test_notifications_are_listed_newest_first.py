"""`GET /api/notifications` lists the log newest first, the order every list of it shows.

The log is kept in time order, oldest first: notes are appended as they arrive, and a merge writes
the file in time order (`bounded_log`). The endpoint answered in that order, so every list had to
turn it around, and the phone's Recent list, which shows the first six, did not: it listed the six
oldest notes of the log and none of the morning's. The order is the endpoint's to state now, newest
first by each note's own time, and every list shows it as served (the web side of the contract is
`web/src/pages/notifications/everyListShowsTheNewestFirst.test.tsx`).
"""

from __future__ import annotations

import json

import pytest

from personalclaw import notification_kinds as nk
from personalclaw.dashboard.handlers.messaging import api_notifications


class _Request:
    """A request to the handler: the owner's, or the app's named by *app* (the token middleware
    stores the calling app on the request)."""

    def __init__(self, state, app: str = "") -> None:
        self.app = {"state": state}
        self._app = app

    def get(self, key, default=None):
        return self._app if key == "app" and self._app else default


async def _listed(state, app: str = "") -> dict:
    return json.loads((await api_notifications(_Request(state, app))).body.decode())


def _titles(data: dict) -> list[str]:
    return [n["title"] for n in data["notifications"]]


@pytest.fixture()
def state(tmp_path, monkeypatch):
    from tests.chat_test_helpers import _make_state

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    return _make_state(tmp_path)


@pytest.mark.asyncio
async def test_the_notes_that_just_arrived_are_listed_first(state):
    for title in ("Approval needed", "Loop stopped", "Morning brief finished"):
        state.notify(nk.INFO, title, "What happened.")
    assert _titles(await _listed(state)) == [
        "Morning brief finished",
        "Loop stopped",
        "Approval needed",
    ]


@pytest.mark.asyncio
async def test_the_order_is_each_notes_own_time_not_its_place_in_the_log(state):
    """A note the log holds out of time order (one appended while the clock stood behind) lists
    where its time puts it: turning the log around would put it first."""
    state._notification_log = [
        {"ts": "2026-10-01T05:48:00+00:00", "kind": "info", "title": "Yesterday's approval"},
        {"ts": "2026-10-02T07:14:13+00:00", "kind": "info", "title": "Morning brief finished"},
        {"ts": "2026-10-02T07:12:00+00:00", "kind": "info", "title": "Brief needs an answer"},
    ]
    assert _titles(await _listed(state)) == [
        "Morning brief finished",
        "Brief needs an answer",
        "Yesterday's approval",
    ]


@pytest.mark.asyncio
async def test_an_app_is_answered_with_its_own_notes_newest_first(state):
    def note(at: str, title: str, **more) -> dict:
        return {"ts": f"2026-10-02T{at}:00+00:00", "kind": "info", "title": title, **more}

    state._notification_log = [
        note("07:00", "first", raised_by_app="a"),
        note("07:05", "yours"),
        note("07:10", "second", raised_by_app="a"),
    ]
    data = await _listed(state, app="a")
    assert _titles(data) == ["second", "first"]
    assert data["unread"] == 2


@pytest.mark.asyncio
async def test_listing_leaves_the_log_in_time_order(state, tmp_path):
    """The order is the answer's, not the log's: a list leaves the log oldest first, and the next
    write puts the file back in time order."""
    for title in ("first", "second", "third"):
        state.notify(nk.INFO, title, "What happened.")
    await _listed(state)
    assert [n["title"] for n in state._notification_log] == ["first", "second", "third"]

    assert state.ack_notification(state._notification_log[0]["ts"])
    lines = (tmp_path / "notifications.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["title"] for line in lines if line] == ["first", "second", "third"]
