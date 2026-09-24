"""What one loop cost — the prefix-scoped read of the per-turn ledger (MRT-3).

The clause spells this "via ``run_totals``". It is deliberately NOT that, and the deviation is
recorded in MODEL-ROUTING-TELEMETRY: ``loop/journal.py``'s ``step_completed`` rows carry no
``tokens`` and no ``cost_usd``, so ``ledger.run_totals`` over a loop store returns ``0.0``, and
making it return money would mean copying turn dollars into a second store. This reads the record
that already holds them.

Three shapes are load-bearing here, and each one fails by producing a PLAUSIBLE number rather than
an obviously broken one — which is why every test below asserts the figure, not its presence:

* a fan-out loop whose task workers are missed reports a total that is silently too LOW;
* a bare ``startswith`` prefix reports a total that is silently too HIGH, by swallowing a
  different loop whose id extends this one's;
* the planner session sits OUTSIDE the worker prefix (``loop-plan-<id>``), so a figure that
  quietly omitted it would imply a completeness it does not have.
"""

from __future__ import annotations

import pytest

from personalclaw import usage_ledger as ul
from personalclaw.loop.manager import loop_spend, session_key, task_session_key
from personalclaw.loop.plan_walkthrough import planner_session_key
from personalclaw.usage_ledger import TurnUsage

#: Two loop ids where one is a strict prefix of the other, as a STRING. `store._LOOP_ID_RE` mints
#: 8 hex chars, so two real ids cannot collide today — but the ledger accepts whatever
#: `session_key` a writer hands it, and the selection must be unambiguous on its own terms rather
#: than borrow safety from a regex in another module. If the id shape ever widens, this is the test
#: that already covers it.
LOOP = "abc123"
LOOP_LONGER = "abc1234"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Isolated config_dir — the ledger writes under tmp, never the real home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _turn(skey: str, cost: float, *, priced: bool = True, tokens: int = 100) -> None:
    ul.record_turn(
        TurnUsage(
            ts="2026-08-21T12:00:00+00:00",
            session_key=skey,
            source="loop",
            agent="",
            provider="anthropic",
            model="claude-opus-5",
            input_tokens=tokens,
            output_tokens=tokens // 10,
            cost_usd=cost,
            priced=priced,
        )
    )


# ── 1. both worker key shapes ───────────────────────────────────────────────────────────


def test_the_key_shapes_this_reads_are_the_ones_the_manager_mints() -> None:
    """The floor: if these spellings drift, every sum below is measuring the wrong keys."""
    assert session_key(LOOP) == f"loop-{LOOP}"
    assert task_session_key(LOOP, "t1") == f"loop-{LOOP}-t1"
    assert task_session_key(LOOP, "t1").startswith(session_key(LOOP) + "-")
    # The planner is NOT under the worker prefix — the whole reason it is reported separately.
    assert not planner_session_key(LOOP).startswith(session_key(LOOP) + "-")


def test_a_fan_out_loop_sums_the_main_worker_AND_every_task_worker() -> None:
    """The clause's real content: one logical run spanning several session keys.

    Asserted as an exact figure because the failure mode is arithmetic. A read that matched only
    `session_key` would return 0.25 here — a number that renders perfectly and understates the
    loop by 80%.
    """
    _turn(session_key(LOOP), 0.25)
    _turn(task_session_key(LOOP, "t1"), 0.50)
    _turn(task_session_key(LOOP, "t2"), 0.50)

    spend = loop_spend(LOOP)
    assert spend["dollars_est"] == pytest.approx(1.25)
    assert spend["turns"] == 3
    # Vacuity floor: the main worker's own turn is really in there, so this is not three task
    # workers passing for a fan-out sum.
    assert (
        loop_spend(LOOP)["dollars_est"]
        > ul.totals(session_key=task_session_key(LOOP, "t1"))["cost_usd"]
        + ul.totals(session_key=task_session_key(LOOP, "t2"))["cost_usd"]
    )


def test_tokens_sum_across_the_fan_out_too() -> None:
    _turn(session_key(LOOP), 0.1, tokens=100)
    _turn(task_session_key(LOOP, "t1"), 0.1, tokens=200)
    # 100+10 + 200+20
    assert loop_spend(LOOP)["tokens"] == 330


# ── 2. the prefix must not be ambiguous ─────────────────────────────────────────────────


def test_a_loop_whose_id_EXTENDS_another_is_not_swallowed_by_it() -> None:
    """The silently-too-high shape. `loop-abc123` must not collect `loop-abc1234`'s rows.

    A bare `key.startswith(prefix)` passes every other test in this file and fails only here,
    reporting 3.00 for a loop that spent 1.00. Both directions are asserted, because a fix that
    over-corrected (requiring the separator even for the key itself) would return 0.00 for the
    main worker — also plausible, also wrong.
    """
    _turn(session_key(LOOP), 1.00)
    _turn(session_key(LOOP_LONGER), 2.00)

    assert loop_spend(LOOP)["dollars_est"] == pytest.approx(1.00)
    assert loop_spend(LOOP)["turns"] == 1
    assert loop_spend(LOOP_LONGER)["dollars_est"] == pytest.approx(2.00)
    assert loop_spend(LOOP_LONGER)["turns"] == 1


def test_the_extending_loops_own_task_workers_stay_with_it() -> None:
    """The same collision one level down: `loop-abc1234-t1` belongs to the LONGER loop only."""
    _turn(session_key(LOOP), 1.00)
    _turn(task_session_key(LOOP_LONGER, "t1"), 5.00)

    assert loop_spend(LOOP)["dollars_est"] == pytest.approx(1.00)
    assert loop_spend(LOOP_LONGER)["dollars_est"] == pytest.approx(5.00)


def test_the_prefix_match_is_separator_aware_at_the_seam_itself() -> None:
    """Unit-level, so a red points at the predicate rather than at a fixture."""
    prefix = session_key(LOOP)
    assert ul._session_matches(prefix, "", prefix) is True  # the key itself
    assert ul._session_matches(prefix + "-t1", "", prefix) is True  # a child at the separator
    assert ul._session_matches(prefix + "4", "", prefix) is False  # a longer id, NOT a child
    assert ul._session_matches(prefix + "4-t1", "", prefix) is False
    assert ul._session_matches("loop-plan-" + LOOP, "", prefix) is False


def test_an_empty_prefix_selects_everything_rather_than_nothing() -> None:
    """`session_prefix=""` must stay the no-filter default, or every existing caller silently
    starts reading zero rows."""
    _turn("some-other-session", 0.75)
    assert ul.totals()["cost_usd"] == pytest.approx(0.75)
    assert ul.totals(session_prefix="")["cost_usd"] == pytest.approx(0.75)


def test_the_exact_session_filter_still_works_alongside_the_prefix_one() -> None:
    _turn(session_key(LOOP), 1.00)
    _turn(task_session_key(LOOP, "t1"), 2.00)
    assert ul.totals(session_key=session_key(LOOP))["cost_usd"] == pytest.approx(1.00)
    assert ul.totals(session_prefix=session_key(LOOP))["cost_usd"] == pytest.approx(3.00)


def test_rollup_takes_the_prefix_too_and_groups_within_it() -> None:
    _turn(session_key(LOOP), 1.00)
    _turn(task_session_key(LOOP, "t1"), 2.00)
    _turn(session_key(LOOP_LONGER), 9.00)
    rows = ul.rollup(group_by="model", session_prefix=session_key(LOOP))
    assert len(rows) == 1
    assert rows[0]["cost_usd"] == pytest.approx(3.00)


# ── 3. the planning bucket is reported, not folded and not dropped ──────────────────────


def test_planning_spend_is_reported_beside_the_worker_figure_not_inside_it() -> None:
    """`plan_walkthrough` names the planner session `app="loops"` and keys it `loop-plan-<id>`,
    so it is neither under the worker prefix nor in the same purpose bucket. Reporting it
    separately is what lets the surface say what the headline figure covers."""
    _turn(session_key(LOOP), 1.00)
    _turn(planner_session_key(LOOP), 0.40)

    spend = loop_spend(LOOP)
    assert spend["dollars_est"] == pytest.approx(1.00), "planning must not inflate the run figure"
    assert spend["planning"]["dollars_est"] == pytest.approx(0.40)
    assert spend["planning"]["turns"] == 1


def test_a_loop_that_never_planned_reports_zero_planning_not_a_missing_key() -> None:
    """A missing key makes the caller guess; an explicit zero lets it say nothing was spent."""
    _turn(session_key(LOOP), 1.00)
    spend = loop_spend(LOOP)
    assert spend["planning"] == {"dollars_est": 0.0, "turns": 0}


def test_planning_spend_alone_still_reports_the_worker_figure_as_zero() -> None:
    """The inverse: a loop still in planning has a real planning figure and an honest 0 run."""
    _turn(planner_session_key(LOOP), 0.40)
    spend = loop_spend(LOOP)
    assert spend["dollars_est"] == 0.0
    assert spend["turns"] == 0
    assert spend["planning"]["dollars_est"] == pytest.approx(0.40)


# ── 4. honesty of the figure itself ─────────────────────────────────────────────────────


def test_an_unpriced_turn_taints_priced_so_the_caller_can_say_FLOOR() -> None:
    _turn(session_key(LOOP), 1.00, priced=True)
    _turn(task_session_key(LOOP, "t1"), 0.0, priced=False)
    spend = loop_spend(LOOP)
    assert spend["priced"] is False
    assert spend["dollars_est"] == pytest.approx(1.00)


def test_a_loop_with_no_turns_reports_zero_rather_than_raising() -> None:
    spend = loop_spend(LOOP)
    assert spend["dollars_est"] == 0.0
    assert spend["turns"] == 0
    assert spend["priced"] is True


def test_another_loops_spend_never_reaches_this_one() -> None:
    """The isolation floor. Without it every sum above would also pass on a shared total."""
    _turn(session_key("ffffffff"), 7.00)
    _turn(task_session_key("ffffffff", "t1"), 7.00)
    assert loop_spend(LOOP)["dollars_est"] == 0.0
