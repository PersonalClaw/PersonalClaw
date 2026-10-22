"""`GET /api/triggers` lists the automations in the Automations list's named order (#3816).

The list merges what used to be two lists ordered two ways, so it names one order and the route
states it, once (`triggers/listing.py`): **Needs you** (a failed last run, a missed run waiting for
a decision, a change waiting for the owner's Allow), oldest first; **Next to run**, soonest first;
**Not on a clock**, by name, ignoring case. Every group breaks a tie by the row's id. The page shows
the rows as served (`web/src/pages/automations/theListShowsItsNamedOrder.test.tsx`).
"""

from __future__ import annotations

import json
import random
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.triggers import listing
from personalclaw.triggers import review as R
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


def _schedule(rid: str, name: str, **more) -> dict:
    row = {
        "kind": "schedule",
        "id": f"schedule:{rid}",
        "name": name,
        "enabled": True,
        "state": "active",
    }
    return {**row, **more}


def _store(rid: str, name: str, **more) -> dict:
    row = {"kind": "store", "id": f"store:{rid}", "name": name, "enabled": True, "state": "active"}
    return {**row, **more}


#: One of each, with the order the list owes them.
FIXTURE = [
    # Needs you, oldest first: a missed slot at NOW-9000, a failure at NOW-5000, an import waiting
    # for review (no recorded start, so the oldest of all), an action not allowed to run yet and a
    # callback waiting for her yes since NOW-7000, and two failures at the same time, by id.
    _schedule(
        "imported", "Imported digest", enabled=False, needs_review=True, next_run_ts=NOW + 60
    ),
    _schedule("missed", "Morning brief", next_run_ts=NOW + 600),
    {
        "kind": "callback",
        "id": "callback:cb1",
        "name": "cb1",
        "enabled": False,
        "needs_grant": ["Run an agent turn"],
        "registered_at": NOW - 7000,
    },
    _schedule(
        "failed-b",
        "Backup",
        last_run_status="failure",
        last_run_ts=NOW - 5000,
        next_run_ts=NOW + 30,
    ),
    _store("file:failed-a", "Notes watcher", last_run_status="timeout", last_run_ts=NOW - 5000),
    {
        "kind": "lifecycle",
        "id": "lifecycle:h1",
        "name": "After a reply",
        "enabled": True,
        "last_status": "error",
        "last_run": NOW - 4000,
    },
    # Next to run, soonest first, equal times by id.
    _schedule("weekly", "Weekly review", next_run_ts=NOW + 7200),
    _schedule("hourly-b", "Hourly sync", next_run_ts=NOW + 3600),
    _schedule(
        "hourly-a",
        "Hourly check",
        next_run_ts=NOW + 3600,
        last_run_status="success",
        last_run_ts=NOW - 10,
    ),
    # Not on a clock, by name ignoring case, equal names by id: an event trigger, a file watch, a
    # webhook, one switched off (even with a next run), one paused, someone else's schedule, and a
    # one-shot whose time has passed.
    _store("event:mail", "inbox mail", store_kind="event"),
    _store("file:zz", "Zettel inbox", store_kind="file"),
    _store("webhook:b", "Deploy hook", store_kind="webhook"),
    _store("webhook:a", "deploy hook", store_kind="webhook"),
    _schedule("off", "Archive", enabled=False, next_run_ts=NOW + 10),
    _schedule("paused", "Cleanup", state="paused", next_run_ts=NOW + 20),
    _schedule(
        "theirs", "Bob's report", read_only=True, next_run_ts=NOW + 5, last_run_status="failure"
    ),
    _schedule("done", "Once", next_run_ts=None),
]

#: A missed run waits on this row, oldest slot NOW-9000.
DECISIONS = {"schedule:missed": NOW - 9000}

EXPECTED = [
    ("needs_you", "schedule:imported"),
    ("needs_you", "schedule:missed"),
    ("needs_you", "callback:cb1"),
    ("needs_you", "schedule:failed-b"),
    ("needs_you", "store:file:failed-a"),
    ("needs_you", "lifecycle:h1"),
    ("next_to_run", "schedule:hourly-a"),
    ("next_to_run", "schedule:hourly-b"),
    ("next_to_run", "schedule:weekly"),
    ("not_on_a_clock", "schedule:off"),
    ("not_on_a_clock", "schedule:theirs"),
    ("not_on_a_clock", "schedule:paused"),
    ("not_on_a_clock", "store:webhook:a"),
    ("not_on_a_clock", "store:webhook:b"),
    ("not_on_a_clock", "store:event:mail"),
    ("not_on_a_clock", "schedule:done"),
    ("not_on_a_clock", "store:file:zz"),
]


def _placed(rows: list[dict]) -> list[tuple[str, str]]:
    return [(r["group"], r["id"]) for r in rows]


@pytest.mark.parametrize("seed", range(6))
def test_a_shuffled_list_comes_back_in_the_three_groups_and_their_orders(seed):
    rows = [dict(r) for r in FIXTURE]
    random.Random(seed).shuffle(rows)
    assert _placed(listing.ordered(rows, decisions=DECISIONS)) == EXPECTED


def test_a_row_moves_only_when_its_state_changes():
    """The same rows, read again in another order, come back the same; a failure that is fixed by
    the next good run moves its row to the clock group, and nothing else moves."""
    first = _placed(listing.ordered([dict(r) for r in FIXTURE], decisions=DECISIONS))
    again = _placed(listing.ordered([dict(r) for r in reversed(FIXTURE)], decisions=DECISIONS))
    assert again == first
    fixed = [dict(r) for r in FIXTURE]
    for row in fixed:
        if row["id"] == "schedule:failed-b":
            row["last_run_status"] = "success"
    moved = _placed(listing.ordered(fixed, decisions=DECISIONS))
    assert ("next_to_run", "schedule:failed-b") == moved[5]
    assert [p for p in moved if p[1] != "schedule:failed-b"] == [
        p for p in first if p[1] != "schedule:failed-b"
    ]


def test_what_counts_as_failed_is_what_the_run_history_says():
    """A suppressed or stopped run is no failure; a status the history cannot classify is one."""
    for status, failed in (
        ("success", False),
        ("skipped_gate", False),
        ("stopped", False),
        ("failure", True),
        ("timeout", True),
        ("a-status-nobody-mapped", True),
    ):
        row = _schedule("s", "S", last_run_status=status, last_run_ts=NOW, next_run_ts=NOW + 60)
        group = listing.ordered([row])[0]["group"]
        assert group == ("needs_you" if failed else "next_to_run"), status


def test_where_a_row_reports():
    assert listing.reports_to(session="conversation:chat-1-1792387100") == {
        "kind": "thread",
        "session": "chat-1-1792387100",
    }
    assert listing.reports_to(session="fresh", delivery="channel:slack:C123") == {
        "kind": "channel",
        "channel": "slack",
    }
    assert listing.reports_to(delivery="inbox") == {"kind": "inbox"}
    assert listing.reports_to(delivery="") == {"kind": "inbox"}
    assert listing.reports_to(delivery="none") == {"kind": "none"}
    # A thread wins over the result route: the fire renders into the conversation.
    assert listing.reports_to(session="conversation:c-9", delivery="inbox")["kind"] == "thread"


# ── through the route ───────────────────────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    monkeypatch.setattr(T, "_used_by_index", lambda: {})

    class _NoHooks:
        def list_all(self):
            return []

    monkeypatch.setattr(T, "_hook_store", lambda s: _NoHooks())
    return tmp_path


def _clock(rid: str, name: str, every: int, *, enabled: bool = True):
    from personalclaw.triggers.models import Trigger

    return Trigger(
        id=rid,
        name=name,
        kind="clock",
        enabled=enabled,
        spec={"kind": "interval", "interval_secs": every},
        workflow={"inline": {"provider": "notify", "config": {"message": "hi"}}},
        delivery="inbox",
    )


async def _listed(query: str = "") -> list[dict]:
    app = web.Application()
    app["state"] = SimpleNamespace(_sessions={})
    req = make_mocked_request("GET", "/api/triggers" + query, app=app)
    return json.loads((await T.api_triggers(req)).body.decode())["triggers"]


@pytest.mark.asyncio
async def test_the_route_states_the_order_and_each_rows_group(home, monkeypatch):
    store = TriggerStore(base_dir=home)
    for row in (
        _clock("zz-hourly", "Hourly", 3600),
        _clock("aa-daily", "Daily", 86400),
        _clock("mm-missed", "Missed brief", 86400),
        _clock("off", "Archive", 60, enabled=False),
    ):
        store.upsert(row)
    R.record(
        [R.ReviewCard(trigger_id="mm-missed", kind=R.MISSED, count=1, latest=NOW, oldest=NOW)],
        base_dir=home,
    )
    rows = await _listed()
    assert [(r["group"], r["id"]) for r in rows] == [
        ("needs_you", "schedule:mm-missed"),
        ("next_to_run", "schedule:zz-hourly"),
        ("next_to_run", "schedule:aa-daily"),
        ("not_on_a_clock", "schedule:off"),
    ]
    assert [r["rank"] for r in rows] == [0, 1, 2, 3]
    assert all(r["reports_to"] == {"kind": "inbox"} for r in rows)
    # A filter of the list keeps its order.
    assert [r["id"] for r in await _listed("?type=schedule")] == [r["id"] for r in rows]


# ── where a row reports, as an app reads the list ──────────────────────────────────────────────


class _Chat:
    """A live conversation as the gateway holds it: its key, its title, the app that started it."""

    def __init__(self, key: str, title: str, created_by_app: str = "") -> None:
        self.key = key
        self.title = title
        self.created_by_app = created_by_app
        self._titled = True


class _Gateway:
    """The two things the list reads of the gateway's conversations."""

    def __init__(self, *chats: _Chat) -> None:
        self._sessions = {c.key: c for c in chats}

    def session_creating_app(self, name: str) -> str:
        chat = self._sessions.get(name)
        return chat.created_by_app if chat else ""


def _bound(rid: str, key: str):
    from personalclaw.triggers.models import Trigger

    return Trigger(
        id=rid,
        name=rid,
        kind="manual",
        spec={},
        workflow={"inline": {"provider": "notify", "config": {"message": "hi"}}},
        session=f"conversation:{key}",
    )


async def _reports(state, app: str = "") -> dict[str, dict]:
    server = web.Application()
    server["state"] = state
    req = make_mocked_request("GET", "/api/triggers", app=server)
    if app:
        req["app"] = app
    rows = json.loads((await T.api_triggers(req)).body.decode())["triggers"]
    return {r["name"]: r["reports_to"] for r in rows}


@pytest.mark.asyncio
async def test_an_app_reads_the_thread_only_of_a_conversation_it_started(home):
    """`GET /api/triggers` is a read an app may declare, and an app reads only the conversations it
    started (`test_apps_see_only_their_own_conversations.py`). So a row bound to one of yours names
    no thread to an app: it says it reports to a thread, and not which, or what it is called."""
    store = TriggerStore(base_dir=home)
    store.upsert(_bound("yours", "chat-1"))
    store.upsert(_bound("the-apps", "chat-2"))
    gateway = _Gateway(_Chat("chat-1", "Tax return"), _Chat("chat-2", "Meeting notes", "minutes"))

    owner = await _reports(gateway)
    assert owner["yours"] == {"kind": "thread", "session": "chat-1", "title": "Tax return"}
    assert owner["the-apps"]["title"] == "Meeting notes"

    app = await _reports(gateway, app="minutes")
    assert app["the-apps"] == {
        "kind": "thread",
        "session": "chat-2",
        "title": "Meeting notes",
    }
    assert app["yours"] == {"kind": "thread"}, "an app reads nothing of your conversation"
    assert "Tax return" not in json.dumps(app) and "chat-1" not in json.dumps(app)
