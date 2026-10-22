"""A goal loop reports as its owner asked when it started (#3824).

The start flow asks once how the loop should report, and the answer is kept on the loop record
(``report``):

* ``as_it_goes``: today's per-cycle progress note, then the outcome's.
* ``when_done``: no progress notes; one note when the loop completes or fails.
* ``if_stuck``: no progress notes and no completion note; the owner hears only when the loop waits
  on them, stalls or fails.

Whatever the choice, a loop that waits on its owner still raises its Inbox item and its one note:
the choice quiets reports, never asks. A new loop reports ``when_done`` unless its creator says
otherwise, and a loop stored before the field existed reads ``as_it_goes``, so nothing an owner
already started changes under them.

Driven through the real watchdog over a three-cycle monitor loop (its cycle budget is its watch
window, so it completes cleanly on the third), counting what it sent.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from personalclaw.inbox import InboxStore
from personalclaw.loop import files as loop_files
from personalclaw.loop import manager, store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    return tmp_path


class _Session:
    def __init__(self, key: str) -> None:
        self.key = key
        self._trust = True
        self.messages: list = []

    @property
    def running(self) -> bool:
        return False


class _Svc:
    def __init__(self, inbox: InboxStore) -> None:
        self.inbox = inbox


class _State:
    """What the watchdog reads (sessions, the loop stream, refresh, notify) and what the Inbox
    helper writes through (`_inbox_svc.inbox`, a real `InboxStore`)."""

    def __init__(self) -> None:
        from personalclaw.dashboard.sse import SseRegistry

        self._sessions: dict[str, _Session] = {}
        self._sse = SseRegistry()
        inbox = InboxStore()
        inbox.load()
        self._inbox_svc = _Svc(inbox)
        self.notes: list[tuple[str, str]] = []

    def loop_sse(self):
        return self._sse

    def push_refresh(self, *kinds: Any) -> None:
        pass

    def notify(self, kind, title, body, *, meta=None) -> None:
        self.notes.append((kind, title))


class _NudgeSvc:
    async def add(self, **kw: Any):
        return None

    def get_by_session(self, session_name):
        return None

    def list_all(self):
        return []

    async def update(self, loop_id, **kw):
        pass

    async def remove(self, loop_id):
        pass


@pytest.fixture
def live(monkeypatch) -> _State:
    state = _State()
    from personalclaw.inbox_providers import native_source

    monkeypatch.setattr(native_source, "_dashboard_state", state, raising=False)
    return state


def _running(report: str | None, **over: Any) -> Loop:
    base: dict[str, Any] = dict(
        id="",
        name="Watch the soil delivery",
        kind="goal",
        task="watch for the soil delivery and say when it is here",
        kind_config={"goal_type": "monitor"},
        idle_secs=120,
        max_cycles=3,
        attended=False,
    )
    if report is not None:
        base["report"] = report
    base.update(over)
    loop = store.create(Loop(**base))
    store.update_status(loop.id, LoopStatus.RUNNING)
    return store.get(loop.id)


def _finding(lid: str, cycle: int) -> None:
    (loop_files.loop_dir(lid) / "findings" / f"cycle_{cycle:03d}.json").write_text(
        json.dumps({"cycle": cycle, "new_findings_count": 1})
    )


def _three_cycles(state: _State, loop: Loop) -> W.LoopWatchdog:
    wd = W.LoopWatchdog(state, _NudgeSvc())
    key = manager.session_key(loop.id)
    state._sessions[key] = _Session(key)
    _run(wd._poll_once())
    for cycle in (1, 2, 3):
        _finding(loop.id, cycle)
        _run(wd._poll_once())
    assert store.get(loop.id).status == LoopStatus.COMPLETE.value
    return wd


def _count(state: _State, title: str) -> int:
    return sum(1 for _kind, t in state.notes if t == title)


def _open_inbox(state: _State) -> list:
    return state._inbox_svc.inbox.open_items()


class TestWhatEachChoiceSends:
    def test_as_it_goes_sends_a_note_each_cycle_and_the_outcomes(self, live):
        _three_cycles(live, _running("as_it_goes"))
        # A note for each cycle that did not end it, and the completion's.
        assert _count(live, "Loop progress") == 2
        assert _count(live, "Loop complete") == 1

    def test_when_done_sends_the_outcome_alone(self, live):
        _three_cycles(live, _running("when_done"))
        assert _count(live, "Loop progress") == 0
        assert _count(live, "Loop complete") == 1

    def test_if_stuck_sends_nothing_for_a_loop_that_ran_clean(self, live):
        _three_cycles(live, _running("if_stuck"))
        assert live.notes == []

    def test_if_stuck_still_raises_the_wait_once(self, live):
        loop = _running("if_stuck", attended=True)
        wd = W.LoopWatchdog(live, _NudgeSvc())
        key = manager.session_key(loop.id)
        live._sessions[key] = _Session(key)
        loop_files.write_question(loop.id, "which supplier?")
        _run(wd._poll_once())
        _run(wd._poll_once())
        assert store.get(loop.id).status == LoopStatus.NEEDS_INPUT.value
        assert len(_open_inbox(live)) == 1
        assert _count(live, "Loop needs your input") == 1

    def test_if_stuck_still_says_it_failed(self, live):
        loop = _running("if_stuck")
        wd = W.LoopWatchdog(live, _NudgeSvc())
        wd._publish(loop.id, "failed")
        assert _count(live, "Loop failed") == 1


class TestTheRecord:
    def test_a_new_loop_reports_when_done(self, live):
        assert _running(None).report == "when_done"

    def test_the_choice_is_kept(self, live):
        assert store.get(_running("if_stuck").id).report == "if_stuck"

    def test_a_loop_stored_before_the_field_reads_as_it_goes(self, live, _home):
        loop = _running(None)
        db = sqlite3.connect(str(store._db_path()))
        try:
            # The row as a store from before the field wrote it: no report column at all.
            db.execute("ALTER TABLE loops DROP COLUMN report")
            db.commit()
        finally:
            db.close()
        assert store.get(loop.id).report == "as_it_goes"

    def test_a_choice_the_record_does_not_know_reads_when_done(self, live):
        loop = _running(None)
        db = sqlite3.connect(str(store._db_path()))
        try:
            db.execute("UPDATE loops SET report = 'every_minute' WHERE id = ?", (loop.id,))
            db.commit()
        finally:
            db.close()
        assert store.get(loop.id).report == "when_done"


def _request(method: str, path: str, body: dict[str, Any], match: dict[str, str] | None = None):
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    app = web.Application()
    app["state"] = _State()
    request = make_mocked_request(method, path, app=app, match_info=match or {})
    request["user"] = "owner"

    async def _json() -> dict[str, Any]:
        return body

    request.json = _json  # type: ignore[assignment]
    return request


def _body(response) -> dict[str, Any]:
    return json.loads(response.body.decode())


class TestTheRoutes:
    """`POST /api/loops` takes the choice the start flow asked for; `PUT /api/loops/{id}` changes it
    at any time, as a rename is allowed, so the strip can change it while the loop runs."""

    TASK = "watch for the soil delivery and say when it is here"

    def _create(self, monkeypatch, body: dict[str, Any]):
        from personalclaw.dashboard.handlers import loop_routes as H

        class _Ok:
            can_start = True

            def to_dict(self) -> dict[str, Any]:
                return {}

        async def _no_block(_body: dict[str, Any]) -> None:
            return None

        monkeypatch.setattr(H.validation, "validate", lambda _b, **_kw: _Ok())
        monkeypatch.setattr(H.validation, "tools_blocker", _no_block)
        return _run(H.api_loop_create(_request("POST", "/api/loops", body)))

    def test_create_keeps_the_choice_and_defaults_to_when_done(self, live, monkeypatch):
        asked = self._create(monkeypatch, {"kind": "goal", "task": self.TASK, "report": "if_stuck"})
        assert asked.status == 201
        assert store.get(_body(asked)["id"]).report == "if_stuck"
        unsaid = self._create(monkeypatch, {"kind": "goal", "task": self.TASK})
        assert store.get(_body(unsaid)["id"]).report == "when_done"

    def test_create_refuses_a_choice_it_does_not_know(self, live, monkeypatch):
        refused = self._create(monkeypatch, {"kind": "goal", "task": self.TASK, "report": "hourly"})
        assert refused.status == 400
        assert "report must be one of" in _body(refused)["error"]

    def test_a_running_loop_changes_how_it_reports(self, live):
        from personalclaw.dashboard.handlers import loop_routes as H

        loop = _running("as_it_goes")
        changed = _run(
            H.api_loop_update(
                _request("PUT", f"/api/loops/{loop.id}", {"report": "when_done"}, {"id": loop.id})
            )
        )
        assert changed.status == 200, _body(changed)
        assert store.get(loop.id).report == "when_done"
        refused = _run(
            H.api_loop_update(
                _request("PUT", f"/api/loops/{loop.id}", {"report": "hourly"}, {"id": loop.id})
            )
        )
        assert refused.status == 400
        assert store.get(loop.id).report == "when_done"
