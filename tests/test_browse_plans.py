"""Tests for the scheduled-actuator browse plans (BROWSE-AUTOMATION §(d), BA-6)."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.browse import plans as bp
from personalclaw.browse.target import TARGET_USER_BROWSER
from personalclaw.guardrails.autonomy import RUNG_AUTONOMOUS, RUNG_DRAFT_ONLY, RUNG_ONE_TAP


@pytest.fixture()
def plan_home(tmp_path, monkeypatch):
    """An isolated home so a plan write never touches the operator's real ``browse/plans``."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _watch(**over) -> bp.BrowsePlan:
    base = dict(
        id="w1", goal="watch the changelog", kind=bp.KIND_WATCH_PAGE, start_url="https://x/c"
    )
    base.update(over)
    return bp.BrowsePlan(**base)


def _walk(**over) -> bp.BrowsePlan:
    base = dict(
        id="f1",
        goal="file the form",
        kind=bp.KIND_WALK_FLOW,
        start_url="https://x/f",
        submits=True,
    )
    base.update(over)
    return bp.BrowsePlan(**base)


class StubRunner:
    """A counting tick runner. ``reply_for(plan, calls)`` decides each outcome."""

    def __init__(self, reply_for):
        self.calls: list[str] = []
        self._reply_for = reply_for

    async def __call__(self, plan: bp.BrowsePlan) -> bp.TickOutcome:
        n = len(self.calls)
        self.calls.append(plan.id)
        return self._reply_for(plan, n)


# ── model + persistence ───────────────────────────────────────────────────────


def test_plan_round_trips_through_dict():
    p = _walk(cursor={"step": 3}, notes=("a", "b"))
    assert bp.BrowsePlan.from_dict(p.to_dict()) == p


def test_save_and_load_in_isolated_home(plan_home):
    p = _watch()
    bp.save_plan(p)
    loaded = bp.load_plan("w1")
    assert loaded is not None and loaded.id == "w1" and loaded.kind == bp.KIND_WATCH_PAGE
    assert p.id in [q.id for q in bp.list_plans()]


# ── the floor + the registration refusal (§(d) rung cap) ───────────────────────


def test_floor_is_draft_only_for_submit_plans_and_one_tap_for_read_only():
    assert _walk(submits=True).floor() == RUNG_DRAFT_ONLY
    assert _watch(submits=False).floor() == RUNG_ONE_TAP


def test_a_user_browser_plan_is_refused_at_registration(plan_home):
    p = _watch(target=TARGET_USER_BROWSER)
    with pytest.raises(bp.PlanError, match="unattended"):
        bp.save_plan(p)
    # and it was NOT written
    assert bp.load_plan("w1") is None


def test_unknown_kind_and_missing_fields_are_refused(plan_home):
    with pytest.raises(bp.PlanError, match="unknown plan kind"):
        bp.validate_plan(_watch(kind="teleport"))
    with pytest.raises(bp.PlanError, match="goal"):
        bp.validate_plan(_watch(goal=""))


# ── watch_page: idempotent content diff ────────────────────────────────────────


def test_watch_page_reports_change_then_no_change_on_identical_content(plan_home):
    bp.save_plan(_watch())
    runner = StubRunner(lambda plan, n: bp.TickOutcome(content="v1", ok=True))
    first = asyncio.run(bp.execute_tick(_watch(), run=runner, granted_rung=RUNG_ONE_TAP))
    assert first.changed is True  # empty cursor → first content is a change
    # Re-fire with the SAME content at the persisted cursor → no-op.
    persisted = bp.load_plan("w1")
    second = asyncio.run(bp.execute_tick(persisted, run=runner, granted_rung=RUNG_ONE_TAP))
    assert second.changed is False
    assert second.cursor["content_hash"] == first.cursor["content_hash"]


def test_watch_page_detects_a_real_change(plan_home):
    bp.save_plan(_watch(cursor={"content_hash": bp.sha256(b"old").hexdigest()}))
    runner = StubRunner(lambda plan, n: bp.TickOutcome(content="new", ok=True))
    r = asyncio.run(bp.execute_tick(bp.load_plan("w1"), run=runner, granted_rung=RUNG_ONE_TAP))
    assert r.changed is True


# ── walk_flow: cursor advances only on verified success ────────────────────────


def test_walk_flow_advances_only_on_verified_success(plan_home):
    bp.save_plan(_walk())
    # Unverified step: cursor does NOT advance; re-firing retries the same step (idempotent).
    unver = StubRunner(lambda plan, n: bp.TickOutcome(ok=True, verified=False))
    r1 = asyncio.run(bp.execute_tick(bp.load_plan("f1"), run=unver, granted_rung=RUNG_AUTONOMOUS))
    assert r1.advanced is False and r1.cursor.get("step", 0) == 0
    assert bp.load_plan("f1").cursor.get("step", 0) == 0  # persisted, still step 0

    # Verified step: cursor advances 0→1 and is persisted (survives a crash after this write).
    ver = StubRunner(lambda plan, n: bp.TickOutcome(ok=True, verified=True))
    r2 = asyncio.run(bp.execute_tick(bp.load_plan("f1"), run=ver, granted_rung=RUNG_AUTONOMOUS))
    assert r2.advanced is True and r2.cursor["step"] == 1
    assert bp.load_plan("f1").cursor["step"] == 1


# ── the autonomy floor gate ────────────────────────────────────────────────────


def test_a_submit_plan_is_refused_unattended_without_a_promoting_grant(plan_home):
    bp.save_plan(_walk())  # submits=True → floor draft_only
    runner = StubRunner(lambda plan, n: bp.TickOutcome(ok=True, verified=True, submitted=True))
    r = asyncio.run(
        bp.execute_tick(bp.load_plan("f1"), run=runner, unattended=True, granted_rung="")
    )
    assert r.refused is True and r.ok is False
    assert runner.calls == []  # the browser was never driven
    # step unchanged
    assert bp.load_plan("f1").cursor.get("step", 0) == 0


def test_a_submit_plan_runs_unattended_once_promoted(plan_home):
    bp.save_plan(_walk())
    runner = StubRunner(lambda plan, n: bp.TickOutcome(ok=True, verified=True))
    r = asyncio.run(
        bp.execute_tick(
            bp.load_plan("f1"), run=runner, unattended=True, granted_rung=RUNG_AUTONOMOUS
        )
    )
    assert r.refused is False and r.advanced is True
    assert runner.calls == ["f1"]


def test_a_read_only_plan_runs_unattended_at_its_own_floor(plan_home):
    bp.save_plan(_watch())  # floor one_tap
    runner = StubRunner(lambda plan, n: bp.TickOutcome(content="v", ok=True))
    r = asyncio.run(
        bp.execute_tick(bp.load_plan("w1"), run=runner, unattended=True, granted_rung=RUNG_ONE_TAP)
    )
    assert r.refused is False and runner.calls == ["w1"]


# ── a deleted plan is not resurrected by a cursor write ─────────────────────────


def test_cursor_write_does_not_recreate_a_deleted_plan(plan_home):
    bp.save_plan(_watch())
    bp._plan_path("w1").unlink()  # operator deleted it mid-run
    runner = StubRunner(lambda plan, n: bp.TickOutcome(content="v", ok=True))
    asyncio.run(bp.execute_tick(_watch(), run=runner, granted_rung=RUNG_ONE_TAP))
    assert bp.load_plan("w1") is None
