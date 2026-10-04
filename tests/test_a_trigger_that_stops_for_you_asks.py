"""A trigger whose action stops for you asks you, once, and your answer runs it.

Measured on `main` before this was written, with a browse trigger pointed at a sign-in page:

* its history row read `success` — the action had done nothing it was asked, it had stopped at
  the sign-in page and said so (`outcome="needs_input"`), and the recorders read only `success`;
* nothing anyone could answer was raised. A workflow run parks such a step and asks through its
  gate; a trigger's action had no such seam, so the question existed only in a JSON blob in the
  history row's summary;
* a session that had EXPIRED raised a site-level Inbox row of its own, which resumed
  nothing: answering it ran no trigger and no step;
* the site-level row and the mirror's banner told the person to sign in "in the handoff window" —
  a window nothing opens.

The contract pinned here, against the real handlers, the real browse provider and the real Inbox:

* the run's history row is `waiting`, and says so in words: "Waiting for you. Sign in to …";
* ONE Inbox row carries the action's own card, and a second run before anyone answers asks
  nothing new — the same row, the same token;
* Approve runs the trigger's action again with your answer on that one dispatch, the way an
  approved in-run park runs its step again: past the sign-in check, once, however often you click;
* Deny closes the question; a stale or spent token answers nothing (409);
* a later run that goes through withdraws the question; a scheduled fire asks exactly as Run now.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import types
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.action_providers as AP
import personalclaw.config.loader as loader
from personalclaw.action_providers.base import ActionResult
from personalclaw.dashboard.handlers import trigger_runs
from personalclaw.dashboard.handlers import triggers as T
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

TID = "balance"
SIGN_IN_URL = "http://127.0.0.1:9/login"
_BROWSE = {
    "inline": {
        "provider": "browse",
        "config": {"goal": "read my balance", "start_url": SIGN_IN_URL},
    }
}


# ── harness ──


@pytest.fixture
def home(tmp_path, monkeypatch):
    """`PERSONALCLAW_HOME` as well as the handler's own seams: the browse profile, the Inbox and the
    park file all resolve the home per call, and every one of them lands here."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    return tmp_path


class _Answers:
    """The REAL browse provider, with the `ActionContext.answer` of every dispatch it served."""

    def __init__(self) -> None:
        from personalclaw.action_providers.registry import _ensure_default_providers_registered

        _ensure_default_providers_registered()
        self.real = AP.get_action_provider("browse")
        assert self.real is not None, "the browse provider must be registered"
        self.seen: list[object] = []

    async def execute(self, config, ctx, timeout=30):
        self.seen.append(ctx.answer)
        return await self.real.execute(config, ctx, timeout=timeout)


@pytest.fixture
def browse(monkeypatch):
    answers = _Answers()
    real = AP.get_action_provider
    monkeypatch.setattr(
        AP, "get_action_provider", lambda name: answers if name == "browse" else real(name)
    )
    return answers


def _trigger(home: Path, *, workflow=_BROWSE) -> Trigger:
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=TID,
            name="Check my balance",
            kind="clock",
            enabled=True,
            created_by="user",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow=copy.deepcopy(workflow),
            capabilities={"providers": ["browse"]},
        )
    )
    loaded = TriggerStore(base_dir=home).get(TID)
    assert loaded is not None
    return loaded.trigger


def _state() -> types.SimpleNamespace:
    return types.SimpleNamespace(push_refresh=lambda *k: None, _background_tasks=set())


def _req(path: str, *, body: dict, match_info: dict, headers: dict | None = None):
    app = web.Application()
    app["state"] = _state()
    req = make_mocked_request("POST", path, match_info=match_info, app=app, headers=headers)
    req["user"] = "owner"

    async def _json():
        return body

    req.json = _json  # type: ignore[assignment]
    return req


def _run() -> dict:
    resp = asyncio.run(
        trigger_runs.api_trigger_run(
            _req(f"/api/triggers/schedule:{TID}/run", body={}, match_info={"id": f"schedule:{TID}"})
        )
    )
    return json.loads(resp.body.decode())


def _answer(token: str, answer: bool) -> web.Response:
    """The Inbox card's answer, addressed as the web client addresses it (`store:<id>`)."""
    return asyncio.run(
        trigger_runs.api_trigger_answer(
            _req(
                f"/api/triggers/store:{TID}/answer",
                body={"resume_token": token, "answer": answer},
                match_info={"id": f"store:{TID}"},
            )
        )
    )


def _body(resp: web.Response) -> dict:
    return json.loads(resp.body.decode())


def _history(home: Path) -> list[dict]:
    """The trigger's run rows, newest first — what its History panel lists."""
    rows, _total = asyncio.run(ScheduleRunStore(home).list_for_job(TID, 0, 50))
    return rows


def _rows(*, open_only: bool = True) -> list:
    from personalclaw.inbox import OPEN_STATUSES, InboxStore

    store = InboxStore()
    store.load()
    return [i for i in store.items.values() if not open_only or i.status in OPEN_STATUSES]


def _park_rows(**kw) -> list:
    return [i for i in _rows(**kw) if i.refs.get("trigger_park") == TID]


def _token() -> str:
    (row,) = _park_rows()
    token = str(row.refs.get("resume_token") or "")
    assert token, "the row must carry the token that answers it"
    return token


# ── the run is waiting, not a success ──


def test_a_run_that_stops_at_a_sign_in_page_is_waiting_for_you_not_a_success(home, browse):
    """🔴 Red on main: the row read `success`, with the handoff's JSON as its summary."""
    _trigger(home)

    assert _run()["ok"] is True, "the action ran — it stopped for you, it did not fail"

    (row,) = _history(home)
    assert row["status"] == "waiting", row
    assert row["summary"] == (
        "Waiting for you. Sign in to 127.0.0.1-9, then confirm — the browse run resumes with "
        "that session."
    )
    assert browse.seen == [None], "a plain run carries no answer"


def test_the_runs_feed_reads_it_as_deferred_with_the_same_words(home, browse):
    """The dashboard's runs feed projects the same row: DEFERRED (parked awaiting a human), never
    the `failed` an unmapped status falls back to."""
    from personalclaw.triggers.history import schedule_run_to_record

    _trigger(home)
    _run()
    (row,) = _history(home)

    record = schedule_run_to_record(row, trigger_id=f"schedule:{TID}")
    assert record.outcome == "deferred"
    assert record.reason.startswith("Waiting for you. Sign in to 127.0.0.1-9")


# ── one row, the action's own card ──


def test_it_asks_you_with_one_inbox_row_carrying_the_sign_in_card(home, browse):
    """🔴 Red on main: no row at all — the question lived only inside the history row."""
    _trigger(home)
    _run()

    rows = _rows()
    assert len(rows) == 1, [(r.source, r.message[:60]) for r in rows]
    (row,) = rows
    assert row.item_kind == "needs_input"
    assert row.refs["trigger_park"] == TID and row.refs["trigger"] == TID
    assert row.refs["trigger_name"] == "Check my balance"
    card = row.refs["needs_input"]
    assert card["blocker"].startswith("Sign in to 127.0.0.1-9, then confirm")
    # Answered with Approve or Deny, like the in-run card — never the card's prose choices.
    assert card["block_kind"] == "approval" and card.get("choices", []) == []
    assert card["resume_token"] == row.refs["resume_token"]
    assert "Tried: opened 127.0.0.1-9" in row.message
    assert "never been signed in" in row.message


def test_a_second_run_before_anyone_answers_asks_nothing_new(home, browse):
    _trigger(home)
    _run()
    first = _token()
    _run()

    assert [r["status"] for r in _history(home)] == ["waiting", "waiting"]
    assert len(_park_rows()) == 1, "a second fire of the same question stacked a second row"
    assert _token() == first, "the open row's token must still answer it"


# ── the answer ──


def test_approve_runs_it_again_with_your_answer_and_past_the_sign_in_check(home, browse):
    """🔴 Red on main: there was no answer route. The re-run carries `answer=True` — the one fact
    that lets browse leave its pre-run sign-in check, exactly as an approved in-run park does — and
    it goes on to open the page (here: no browser is configured, so that is where it stops)."""
    _trigger(home)
    _run()
    token = _token()

    resp = _answer(token, True)

    assert resp.status == 200
    answer = _body(resp)
    assert answer["approved"] is True and answer["waiting"] is False, answer
    assert browse.seen == [None, True], "Approve must dispatch once, with the answer"
    newest = _history(home)[0]
    assert newest["status"] == "failure", newest
    assert "no `cdp_url` is configured" in newest["error"], "it must get past the sign-in check"
    assert _park_rows() == [], "the answered question's row must close"
    (closed,) = _park_rows(open_only=False)
    assert closed.status == "handled"


def test_an_agents_tool_answers_nothing_and_leaves_the_question_to_you(home, browse, monkeypatch):
    """🔴 Only you answer a trigger's question (`approval_answer`). `/api/triggers` admits the
    gateway's internal secret (for an agent's `/run`), so an agent's tool could post the answer
    and run the action past the sign-in check it had stopped at. Refused before anything about the
    trigger is read, audited, and the token still answers for you."""
    rows: list[dict] = []
    monkeypatch.setattr(
        "personalclaw.sel.sel",
        lambda: types.SimpleNamespace(log_api_access=lambda **kw: rows.append(kw)),
    )
    _trigger(home)
    _run()
    token = _token()

    resp = asyncio.run(
        trigger_runs.api_trigger_answer(
            _req(
                f"/api/triggers/store:{TID}/answer",
                body={"resume_token": token, "answer": True},
                match_info={"id": f"store:{TID}"},
                headers={"X-Internal-Secret": "s", "X-Session-Key": "dashboard:c1"},
            )
        )
    )

    assert resp.status == 403, _body(resp)
    assert _body(resp)["error"]["code"] == "approval_owner_only"
    assert browse.seen == [None], "the agent's answer ran the action"
    assert len(_park_rows()) == 1, "the question must still be yours to answer"
    (refused,) = [r for r in rows if r.get("operation") == "approval.answer_refused"]
    assert refused["caller"] == "agent:dashboard:c1"
    assert refused["resources"] == f"park:{TID} asked_by=trigger:{TID}"
    assert _answer(token, True).status == 200


def test_approve_spends_the_token_once_however_often_you_click(home, browse):
    _trigger(home)
    _run()
    token = _token()

    assert _answer(token, True).status == 200
    again = _answer(token, True)

    assert again.status == 409
    assert _body(again)["error"]["code"] == "trigger_park_gone"
    assert browse.seen == [None, True], "a second click ran the action again"


def test_deny_closes_the_question_and_runs_nothing(home, browse):
    _trigger(home)
    _run()
    token = _token()

    resp = _answer(token, False)

    assert resp.status == 200
    assert _body(resp) == {
        "ok": True,
        "approved": False,
        "name": "Check my balance",
        "result": "declined",
    }
    assert browse.seen == [None], "Deny ran the action"
    assert _park_rows() == []
    assert _answer(token, True).status == 409, "a spent token must not run anything later"
    assert browse.seen == [None]


def test_a_token_that_is_not_the_questions_answers_nothing(home, browse):
    _trigger(home)
    _run()

    resp = _answer("not-the-token", True)

    assert resp.status == 409
    assert browse.seen == [None]
    assert len(_park_rows()) == 1, "a wrong token must leave the real question answerable"
    assert _answer(_token(), True).status == 200


def test_an_answer_that_is_not_a_yes_or_no_is_refused(home, browse):
    from personalclaw.request_validation import RequestValidationError

    _trigger(home)
    _run()

    with pytest.raises(RequestValidationError) as refused:
        asyncio.run(
            trigger_runs.api_trigger_answer(
                _req(
                    f"/api/triggers/store:{TID}/answer",
                    body={"resume_token": _token(), "answer": "I have signed in"},
                    match_info={"id": f"store:{TID}"},
                )
            )
        )

    assert refused.value.status == 400 and refused.value.code == "field_not_a_boolean"
    assert len(_park_rows()) == 1 and browse.seen == [None]


def test_a_refused_approve_leaves_the_question_answerable(home, browse, monkeypatch):
    """The refusals a Run button honours (incident mode, the kill switch) are read BEFORE the token
    is spent: a refused Approve ran nothing and must not have used the answer up."""
    _trigger(home)
    _run()
    token = _token()
    monkeypatch.setattr(
        "personalclaw.triggers.tools.manual_refusal", lambda: "incident mode is active"
    )

    answer = _body(_answer(token, True))

    assert answer["refused"] == "incident mode is active"
    assert browse.seen == [None]
    assert _token() == token, "the question must still be open, with the same token"


def test_answering_a_trigger_that_was_deleted_clears_its_question(home, browse):
    _trigger(home)
    _run()
    token = _token()
    TriggerStore(base_dir=home).delete(TID)

    resp = _answer(token, True)

    assert resp.status == 404
    assert "no longer exists" in _body(resp)["error"]["message"]
    assert _park_rows() == [], "a question about a deleted trigger must not stay answerable"


# ── an expired session ──


def test_an_expired_session_asks_once_and_raises_no_row_of_its_own(home, browse):
    """🔴 Red on main: the expired session raised a site-level row that resumed nothing, and
    the trigger asked nothing — so the one row there was could not run anything. Now the trigger's
    row is the only one, and it says the session went stale."""
    from personalclaw.browse.handoff import mark_expired

    mark_expired(SIGN_IN_URL)
    _trigger(home)
    _run()

    rows = _rows()
    assert len(rows) == 1, [(r.source, r.message[:60]) for r in rows]
    assert rows[0].refs.get("trigger_park") == TID
    assert "sign in again" in rows[0].message


# ── the scheduled fire, and a later run that goes through ──


def _fire(trigger: Trigger, result: ActionResult) -> None:
    import time

    from personalclaw.triggers.run_record import record_run

    asyncio.run(record_run(trigger, started_at=time.time(), result=result, source="schedule"))


def _parked_result(home: Path, trigger: Trigger) -> ActionResult:
    """What the real browse provider returns for this trigger's action at the sign-in page."""
    from personalclaw.action_providers import ActionContext, get_action_provider
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    provider = get_action_provider("browse")
    assert provider is not None
    result = asyncio.run(
        provider.execute(dict(trigger.workflow["inline"]["config"]), ActionContext(event="clock"))
    )
    assert result.success and result.outcome == "needs_input", result
    return result


def test_a_scheduled_fire_that_stops_for_you_asks_the_same_way(home):
    """🔴 Red on main: the fire recorder wrote `success` and raised nothing."""
    trigger = _trigger(home)

    _fire(trigger, _parked_result(home, trigger))

    (row,) = _history(home)
    assert row["status"] == "waiting"
    assert row["summary"].startswith("Waiting for you. Sign in to 127.0.0.1-9")
    assert len(_park_rows()) == 1


def test_a_later_run_that_goes_through_withdraws_the_question(home):
    """The question asked is moot once the action has run through (a session signed in since): an
    answerable row would then run the action again for nothing."""
    trigger = _trigger(home)
    _fire(trigger, _parked_result(home, trigger))
    token = _token()

    _fire(trigger, ActionResult(success=True, stdout="balance: 12"))

    assert _park_rows() == []
    assert _answer(token, True).status == 409


def test_a_failed_run_keeps_the_question(home):
    """A failure did not get past what the question asks about, so the question stands."""
    trigger = _trigger(home)
    _fire(trigger, _parked_result(home, trigger))
    token = _token()

    _fire(trigger, ActionResult(success=False, error="the kill switch is engaged"))

    assert _token() == token


# ── a run that waits is not a success, and it still clears the Run button ──


def _live(home: Path) -> Trigger:
    loaded = TriggerStore(base_dir=home).get(TID)
    assert loaded is not None
    return loaded.trigger


def _last_run_ts(trigger: Trigger) -> float | None:
    """The `last_run_ts` both list projections publish — what the Run button's completion watcher
    waits on (`ScheduleDetail`: finished once it moves past where it was at the click)."""
    from personalclaw.triggers.schedule_view import _last_run_ts

    return _last_run_ts(trigger)


def _epoch(stamp: str) -> float:
    from personalclaw.triggers.service import to_epoch

    return to_epoch(stamp)


def test_a_run_now_that_waits_for_you_is_not_a_success_and_still_clears_the_run_button(
    home, browse
):
    """🔴 Red on main: the Run button's recorder stamped `last_success_at` for a run that had done
    nothing it was asked — and that stamp was the only thing that moved `last_run_ts`, so the
    button cleared only because the run was recorded as a success. The run is stamped
    `last_waiting_at` now, and `last_run_ts` reads it."""
    trigger = _trigger(home)
    assert _last_run_ts(trigger) is None

    _run()

    live = _live(home)
    assert live.last_success_at == "", "a run that stopped for you is not a success"
    assert live.last_waiting_at, "the run must be stamped, or the Run button never clears"
    assert _last_run_ts(live) == _epoch(live.last_waiting_at)
    assert live.last_failure_at == "" and live.last_run_id.startswith("manual-")


def test_a_scheduled_fire_that_waits_for_you_is_not_a_success_either(home):
    trigger = _trigger(home)

    _fire(trigger, _parked_result(home, trigger))

    live = _live(home)
    assert live.last_success_at == ""
    assert live.last_waiting_at
    assert _last_run_ts(live) == _epoch(live.last_waiting_at)
    assert live.health_status == "ok" and live.last_failure_at == "", "a park is not a failure"


def test_the_run_that_goes_through_afterwards_is_the_success(home):
    trigger = _trigger(home)
    _fire(trigger, _parked_result(home, trigger))
    waited = _live(home).last_waiting_at

    _fire(trigger, ActionResult(success=True, stdout="balance: 12"))

    live = _live(home)
    assert live.last_success_at and live.last_waiting_at == waited
    assert _epoch(live.last_success_at) >= _epoch(waited)
    assert _last_run_ts(live) == _epoch(live.last_success_at)


def test_a_run_that_goes_through_stamps_success_and_not_waiting(home, finishes):
    """CONTROL for the three above: the same recorder, over a browse run that finished."""
    _trigger(home, workflow=_PUBLIC)

    _run()

    live = _live(home)
    assert live.last_success_at and live.last_waiting_at == ""
    assert _last_run_ts(live) == _epoch(live.last_success_at)


# ── what a Run button is told: the run's own status, in its row's words ──


def test_run_now_answers_that_its_run_waits_for_you_in_the_rows_own_words(home, browse):
    """🔴 Red on main: `/run` answered `{"ok": true, "result": "ran"}` for a run that stopped for
    you, so both Run buttons flashed a finished run, and the `automation_run` tool — which relays
    this answer — told the agent it ran. It answers the status its run recorded, and the row's own
    line."""
    _trigger(home)

    body = _run()

    (row,) = _history(home)
    assert body["ok"] is True
    assert body["status"] == row["status"] == "waiting"
    assert body["result"] == row["summary"] and body["result"].startswith("Waiting for you. ")


def test_run_now_answers_a_run_that_went_through_as_the_success_it_recorded(home, finishes):
    """CONTROL: a run that did its work answers `success`, and "ran"."""
    _trigger(home, workflow=_PUBLIC)
    body = _run()
    assert (body["ok"], body["status"], body["result"]) == (True, "success", "ran")


def test_the_restart_reviews_run_now_answers_that_its_run_waits_too(home, browse):
    """The review's Run now reaches the action through the same dispatch, and its card said the
    automation "ran now" for a run that had stopped for you."""
    from personalclaw.triggers import review

    _trigger(home)
    review.record(
        [review.ReviewCard(trigger_id=TID, kind="missed", count=1, latest=1.0, oldest=1.0)],
        base_dir=home,
    )
    request = _req(
        "/api/triggers/review",
        body={"trigger_id": TID, "kind": "missed", "action": "run_now"},
        match_info={},
    )

    body = _body(asyncio.run(T.api_trigger_review(request)))

    assert body["ok"] is True and body["status"] == "waiting"
    assert body["result"].startswith("Waiting for you. ")


def test_the_waiting_stamp_survives_a_reload_and_stays_in_this_home(home, browse):
    """It is written to the row, read back from it, and — like every stamp of what has happened to
    a trigger here — dropped when a snapshot brings the row into another home."""
    from personalclaw.triggers.store import RUNTIME_FIELDS

    _trigger(home)
    _run()

    stored = json.loads((home / "triggers.json").read_text())["triggers"][0]
    assert stored["last_waiting_at"] == _live(home).last_waiting_at != ""
    assert "last_waiting_at" in RUNTIME_FIELDS


# ── a run that went through says what it did, not its JSON ──

_PUBLIC = {
    "inline": {
        "provider": "browse",
        "config": {"goal": "read my balance", "start_url": "http://127.0.0.1:9/balance"},
    }
}
NOTES = ("The balance is $12.34", "The last payment was on Monday.")
SAID = (
    "Browse finished in 3 steps at 127.0.0.1:9. Noted: The balance is $12.34; The last payment "
    "was on Monday."
)


@pytest.fixture
def finishes(monkeypatch):
    """The real browse provider, over a loop that finished in three steps and noted two things. No
    browser: `_open` hands back placeholders, and the loop returns the account a real run would."""
    import personalclaw.action_providers.browse_provider as bp
    from personalclaw.browse.loop import BrowseLoopResult, BrowseStep

    async def _open(_self, _cfg, _ctx, *, cdp_url=""):
        return object(), object(), None

    async def _loop(**kw):
        return BrowseLoopResult(
            ok=True,
            goal=kw["goal"],
            final_url=kw["start_url"] + "?tab=summary",
            steps=tuple(
                BrowseStep(index=i, url=kw["start_url"], action="read the page", fenced=True)
                for i in range(3)
            ),
            notes=NOTES,
            visited_urls=(kw["start_url"],),
        )

    monkeypatch.setattr(bp.BrowseActionProvider, "_open", _open)
    monkeypatch.setattr(bp, "run_browse_loop", _loop)


def _trace(home: Path, run_id: str) -> str:
    """The run's trace — what its row opens to; the list read leaves it out."""
    run = asyncio.run(ScheduleRunStore(home).get_run(TID, run_id))
    assert run is not None
    return str(run.get("trace") or "")


def _finished_result(trigger: Trigger) -> ActionResult:
    from personalclaw.action_providers import ActionContext, get_action_provider
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    provider = get_action_provider("browse")
    assert provider is not None
    result = asyncio.run(
        provider.execute(dict(trigger.workflow["inline"]["config"]), ActionContext(event="clock"))
    )
    assert result.success and result.outcome == "", result
    return result


def test_a_run_now_that_finished_says_what_it_did_not_its_json(home, finishes):
    """🔴 Red on main: the row's line was the loop's whole account, as JSON."""
    _trigger(home, workflow=_PUBLIC)

    assert _run()["ok"] is True

    (row,) = _history(home)
    assert row["status"] == "success"
    assert row["summary"] == SAID
    # What the action printed is still the trace a person can open.
    assert json.loads(_trace(home, row["run_id"]))["notes"] == list(NOTES)


def test_a_scheduled_fire_that_finished_says_the_same(home, finishes):
    trigger = _trigger(home, workflow=_PUBLIC)

    _fire(trigger, _finished_result(trigger))

    (row,) = _history(home)
    assert (row["status"], row["summary"]) == ("success", SAID)
    assert json.loads(_trace(home, row["run_id"]))["notes"] == list(NOTES)


def test_the_runs_feed_reads_the_sentence_too(home, finishes):
    from personalclaw.triggers.history import schedule_run_to_record

    _trigger(home, workflow=_PUBLIC)
    _run()
    (row,) = _history(home)

    assert schedule_run_to_record(row, trigger_id=f"schedule:{TID}").reason == SAID


def test_an_action_with_no_sentence_of_its_own_still_shows_what_it_printed(home):
    """CONTROL: `summary` is the action's to write, and an action that writes none — a bash
    script's output, most actions today — keeps its row exactly as before."""
    trigger = _trigger(home)

    _fire(trigger, ActionResult(success=True, stdout="balance: 12"))

    (row,) = _history(home)
    assert row["summary"] == "balance: 12"
    assert _trace(home, row["run_id"]) == "balance: 12"


# ── no surface tells anyone to look for a window nothing opens ──

_ROOT = Path(__file__).resolve().parent.parent
_SCANNED = (
    (_ROOT / "src" / "personalclaw", (".py", ".md", ".json", ".html", ".ts", ".tsx")),
    (_ROOT / "web" / "src", (".ts", ".tsx", ".css", ".md")),
    (_ROOT / "docs", (".md",)),
    (_ROOT / "tests", (".py",)),
)
_SKIP_DIRS = {"node_modules", "__pycache__", "dist", ".venv"}
#: A string literal split across a join: `"… handoff "\n    "window"` (Python), `'handoff ' +\n
#: 'window'` (TypeScript), with an optional Python prefix on the second half.
_JOIN = re.compile(r"""["'`]\s*\+?\s*(?:[rRbBfFuU]{1,2})?["'`]""")
#: A comment continued on the next line: `# … handoff\n    # window`.
_COMMENT_LEADER = re.compile(r"\n\s*(?:#|//|\*)\s?")
_PHRASE = "handoff window"


def _prose(text: str) -> str:
    """The text as a reader meets it: string joins and comment leaders removed, whitespace (JSX
    text wraps across lines) collapsed."""
    return re.sub(r"\s+", " ", _COMMENT_LEADER.sub(" ", _JOIN.sub("", text))).lower()


def _scanned_files() -> list[Path]:
    found = []
    for root, suffixes in _SCANNED:
        for path in root.rglob("*"):
            if path.suffix not in suffixes or not path.is_file():
                continue
            if _SKIP_DIRS.intersection(path.relative_to(_ROOT).parts):
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            found.append(path)
    return found


def test_the_phrase_scan_sees_the_phrase_however_the_source_breaks_it():
    """Positive control: each way the phrase shipped on `main`, as the source broke it."""
    python_join = 'x = (\n    f"Open the site in the handoff "\n    "window and sign in."\n)'
    jsx_wrap = "<p>\n            site in the handoff\n            window and sign in;\n</p>"
    ts_join = "const s = 'open the handoff ' +\n  'window'"
    comment = "# caller cannot accidentally launch the handoff\n    # window against another"
    for sample in (python_join, jsx_wrap, ts_join, comment):
        assert _PHRASE in _prose(sample), sample


def test_no_surface_or_doc_names_a_handoff_window():
    """🔴 Red on main: `browse/mirror.py` (the expired-session row), `BrowseMirror.tsx` (its banner)
    and `browse/handoff.py` (the headful-launch helper nothing called) all named it."""
    files = _scanned_files()
    # Non-vacuity: the three files that carried it are among those read, and the scan is broad.
    names = {p.relative_to(_ROOT).as_posix() for p in files}
    for carried in (
        "src/personalclaw/browse/mirror.py",
        "src/personalclaw/browse/handoff.py",
        "web/src/pages/dashboard/widgets/BrowseMirror.tsx",
    ):
        assert carried in names, carried
    assert len(files) > 2000, len(files)

    hits = [
        p.relative_to(_ROOT).as_posix()
        for p in files
        if _PHRASE in _prose(p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert hits == []
