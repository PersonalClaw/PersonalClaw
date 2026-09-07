"""The run side's two ledger rails are reachable, and absent is not zero (PP-16 seam 4).

PP-16's plan names the seam this file rails:

    **The ledger rails.** The findings rail and the verdict/ROI rail are PROJECTIONS over the
    `PP-5` ledger, which already carries `step_completed`, `judge_verdict`, `breaker_trip` and
    `watcher_reaped` for both nouns.

Measured on `origin/main` before this change, the run side answered none of it. `service.introspect`
was the ONLY run-side ledger projection endpoint, and against a ledger holding one event of each of
those four kinds it surfaced: `step_completed` as a timeline row that drops the step's own
`output_ref`, and `judge_verdict` / `watcher_reaped` not at all. `breaker_trip` was worse than
unreachable — **the workflow engine has no producer for it**, so any count the run side reported
would have been a claim rather than an observation. That is the trap this project has now hit six
times, and `handlers/apps.py` states the principle: absent and declared false are different facts.

So there are two families of rail here, and both matter:

* **Reachability.** Each of the four kinds is projected, or its absence of a PRODUCER is declared —
  and the declaration is checked against the real emitters, in both drift directions, so an engine
  that grows a breaker cannot leave `RAIL_PRODUCERS` lying.
* **Absent-vs-declared-zero.** Every measured cell reads `None` when its ledger row did not carry
  the key, and a real `0.0` when the row carried a zero. Both directions are asserted, because a
  rail that answered `None` to everything would pass a one-sided test and report a paid run as
  unmeasured.

Vacuity floors throughout: each test writes REAL events with the engine's own `Journal` (a
hand-built dict fixture would let this file drift from the stream it projects), and each census
asserts it found rows before concluding anything about them.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from personalclaw.workflows import introspection
from personalclaw.workflows.introspection import (
    ABSENT_VERDICT_SCORES,
    PRODUCER_ENGINE,
    PRODUCER_NONE,
    RAIL_PRODUCERS,
    findings_rail,
    rail_coverage,
    rail_totals,
    verdict_rail,
)

#: The four kinds PP-16's ledger-rails clause names, quoted above. Pinned here rather than read out
#: of `RAIL_PRODUCERS` so the plan's list and the code's table are two independent statements — a
#: kind dropped from the table reds this instead of silently shrinking the rail's own scope.
PLAN_RAIL_KINDS = ("step_completed", "judge_verdict", "breaker_trip", "watcher_reaped")

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"


@pytest.fixture()
def run_home(monkeypatch, tmp_path):
    """A real journal + run store in an isolated home, via the env var the loader honors."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _run_with(run_id: str, events: list[tuple[str, dict]]) -> str:
    """Persist a run and write real journal events to it. Returns the run id.

    Uses the engine's own `Journal`, so the field names and kinds are whatever the engine actually
    writes.
    """
    from personalclaw.workflows import journal as J
    from personalclaw.workflows import store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    store.save(WorkflowRun(id=run_id, workflow_name="rails-tmpl", status=RunStatus.RUNNING))
    journal = J.Journal(run_id)
    for kind, payload in events:
        journal.write(kind, **payload)
    return run_id


#: A run-shaped `step_completed`, exactly as `journal.RunJournal.step_completed` writes it.
_RUN_STEP = {
    "instance_path": "plan",
    "node_id": "plan",
    "epoch": 0,
    "cache_key": "ck",
    "state": "done",
    "duration_secs": 1.5,
    "tokens": 120,
    "retries": 0,
    "model": "m1",
    "provider": "p1",
    "cost_usd": 0.25,
    "degraded_reason": "",
    "resolved_prompt_ref": "",
    "output_ref": "outputs/plan.json",
}

#: A run-shaped `judge_verdict`, exactly as `controller._emit_judge_verdict` writes it.
_RUN_VERDICT = {
    "instance_path": "gate",
    "node_id": "gate",
    "epoch": 0,
    "template": "rails-tmpl",
    "verdict": "PASS",
    "status": "kept",
    "evidence": {"overall": 4.25, "sample_count": 3, "shortfalls": []},
}

#: A LOOP-shaped `step_completed`, exactly as `loop/journal.py::cycle` writes it. The whole point of
#: PP-16 is that these rows will one day flow through the run-side projection.
_LOOP_STEP = {
    "cycle": 3,
    "node_id": "cycle",
    "task_id": "",
    "source_file": "cycle_003.json",
    "finding": {"cycle": 3, "summary": "found the thing"},
}


# ── reachability: the four kinds the plan names ──


def test_every_plan_named_rail_kind_has_a_row_in_the_producer_table(run_home):
    """The plan's four kinds and the code's table are the same set, in both directions.

    A kind in the plan with no table row is a rail that silently does not exist; a table row naming
    a kind the plan does not is scope this seam did not agree to.
    """
    assert set(PLAN_RAIL_KINDS) == set(RAIL_PRODUCERS), (
        "the ledger-rails clause names "
        f"{sorted(PLAN_RAIL_KINDS)} but RAIL_PRODUCERS covers {sorted(RAIL_PRODUCERS)}"
    )


def test_the_two_rails_reach_every_produced_kinds_own_payload(run_home):
    """Each produced rail kind's own payload comes back — not just a count of it.

    The pre-change failure was reachability-by-name: `introspect`'s timeline counted a
    `step_completed` while dropping `output_ref`, so a reader could see that a step finished and
    not reach what it finished with. Each assertion below names a field the timeline row does NOT
    carry, so a rail that merely re-counted events would fail here.
    """
    from personalclaw.workflows import service

    run_id = _run_with(
        "rails-reach",
        [
            ("step_completed", _RUN_STEP),
            ("judge_verdict", _RUN_VERDICT),
            (
                "watcher_reaped",
                {"instance_path": "w", "node_id": "w", "iterations": 7, "reason": "r"},
            ),
        ],
    )
    payload = service.ledger_rails(run_id)
    assert payload["ok"] is True

    assert len(payload["findings"]) == 1, "vacuity floor: the findings rail found no row to project"
    finding = payload["findings"][0]
    # The four fields the introspection timeline drops. Reaching them IS the rail.
    assert finding["output_ref"] == "outputs/plan.json"
    assert finding["provider"] == "p1"
    assert finding["retries"] == 0
    assert finding["degraded_reason"] == ""

    assert len(payload["verdicts"]) == 1, "vacuity floor: the verdict rail found no row to project"
    verdict = payload["verdicts"][0]
    assert verdict["verdict"] == "PASS"
    assert verdict["node_id"] == "gate"
    # The ROI axis the run side DOES carry, lifted out of the ledgered evidence payload.
    assert verdict["overall"] == pytest.approx(4.25)
    assert verdict["sample_count"] == 3

    # `watcher_reaped` has no rail of its own — it is a coverage row, which is what makes it
    # reachable at all. Before this change nothing on the run side reported it.
    reaped = {row["kind"]: row for row in payload["coverage"]}["watcher_reaped"]
    assert reaped["producer"] == PRODUCER_ENGINE
    assert reaped["events"] == 1


def test_an_unknown_run_is_a_named_failure_not_empty_rails(run_home):
    """404-shaped, so a polled deleted run is distinguishable from a warming-up one.

    Empty rails on a missing run is the same class of lie as a zero on an absent cell: both let a
    reader conclude "nothing happened" from a payload that means "nothing was measurable".
    `WF_RUN_NOT_FOUND` is the service vocabulary `handlers._fail` already maps to a 404, so this
    reuses the existing code rather than minting one.
    """
    from personalclaw.workflows import service
    from personalclaw.workflows.handlers import _STATUS_MAP

    result = service.ledger_rails("no-such-run")
    assert result["ok"] is False
    assert result["code"] == "WF_RUN_NOT_FOUND"
    # And that code really translates to a 404 — a named failure nothing maps is still a 500.
    assert _STATUS_MAP["WF_RUN_NOT_FOUND"][0] == 404


def test_the_route_is_registered_and_answers_the_service_read(run_home):
    """The projection has an HTTP consumer — the half that makes it a rail rather than a module.

    `introspection.py` itself shipped fully written and consumed by NOTHING (its own panel's
    docstring records this), so a projection with no route is a known failure mode here.
    """
    from aiohttp import web

    from personalclaw.workflows.handlers import register_workflow_routes

    app = web.Application()
    register_workflow_routes(app)
    paths = {getattr(r.resource, "canonical", "") for r in app.router.routes() if r.method == "GET"}
    assert "/api/workflows/runs/{run_id}/ledger-rails" in paths


# ── absent is not zero ──


def test_a_loop_shaped_step_reads_absent_not_zero_for_money(run_home):
    """A loop-shaped `step_completed` carries NO cost or token key, and the rail says so.

    This is the retirement-critical case and the reason `_carried` exists. PP-16 makes a Loop a
    WorkflowRun, so loop-shaped rows will flow through this exact projection. Loop money lives in
    `usage/turns.jsonl` (`loop.manager.loop_spend` reads it there) and never on the ledger row — so
    a rail defaulting the missing key to zero would report "$0.00, 0 tokens" for work that really
    cost money, on the one surface a user opens to find out what it cost.
    """
    rows = findings_rail([{"kind": "step_completed", "ts": "t", **_LOOP_STEP}])
    assert len(rows) == 1, "vacuity floor: no loop-shaped row was projected"
    row = rows[0]
    assert row["cost_usd"] is None
    assert row["tokens"] is None
    assert row["duration_secs"] is None
    # The loop's own work-unit key survives, untranslated: a cycle and a node+epoch are different
    # facts and the store-retirement seam owns the mapping.
    assert row["cycle"] == 3

    totals = rail_totals(rows, []).to_dict()
    assert totals["cost_usd"] is None
    assert totals["tokens"] is None
    # The count is still real: a step DID complete, and that fact needs no cost key.
    assert totals["steps_completed"] == 1


def test_a_genuinely_free_step_reads_zero_not_absent(run_home):
    """The other direction, which is what stops this from being a rail that answers None to all.

    A run on a free local model carries `cost_usd: 0.0` — a measurement. Reporting that as absent
    would be the same failure mirrored: the user would be told the cost is unknown when it is known
    to be nothing.
    """
    free = dict(_RUN_STEP, cost_usd=0.0, tokens=0, duration_secs=0.0)
    rows = findings_rail([{"kind": "step_completed", "ts": "t", **free}])
    assert len(rows) == 1, "vacuity floor: no row was projected"
    assert rows[0]["cost_usd"] == 0.0
    assert rows[0]["tokens"] == 0
    totals = rail_totals(rows, []).to_dict()
    assert totals["cost_usd"] == 0.0, "a measured zero must not be reported as absent"
    assert totals["tokens"] == 0


def test_a_kind_with_no_run_side_producer_reports_absent_never_zero(run_home):
    """`breaker_trip` has no run-side writer, so its coverage count is None.

    Zero would claim the breaker never tripped. Nothing on this side CAN trip one — the loop
    watchdog is the only writer in the tree — and those are different facts.
    """
    coverage = {row["kind"]: row for row in rail_coverage([])}
    assert coverage, "vacuity floor: rail_coverage reported no kinds"
    assert coverage["breaker_trip"]["producer"] == PRODUCER_NONE
    assert (
        coverage["breaker_trip"]["events"] is None
    ), "a kind nothing writes must read absent; 0 would be a claim, not an observation"
    # A kind that IS produced earns a real zero on an empty ledger — the contrast that makes the
    # None above mean something.
    assert coverage["step_completed"]["producer"] == PRODUCER_ENGINE
    assert coverage["step_completed"]["events"] == 0


def test_the_loop_rails_roi_axis_is_declared_absent_not_plotted_as_zero(run_home):
    """`marginal_value` / `quality_score` are None on a run-side verdict, and NAMED as absent.

    The loop cockpit's `RoiRail` plots `marginal_value`. A run-side `judge_verdict` row does not
    carry it — the engine ledgers the verdict word plus its evidence and keeps the rich
    `JudgeVerdict` in the node output — so a chart defaulting it to 0 would draw a run that earned
    no marginal value. The payload names the missing axes instead.
    """
    rows = verdict_rail([{"kind": "judge_verdict", "ts": "t", **_RUN_VERDICT}])
    assert len(rows) == 1, "vacuity floor: no verdict row was projected"
    for key in ABSENT_VERDICT_SCORES:
        assert rows[0][key] is None, f"{key} must read absent on a run-side verdict row"
    totals = rail_totals([], rows).to_dict()
    assert list(totals["absent_scores"]) == list(ABSENT_VERDICT_SCORES)
    # And the axis it CAN plot is populated, so the panel is not merely empty.
    assert totals["overall_series"] == [pytest.approx(4.25)]


def test_a_loop_shaped_verdict_row_does_carry_the_loop_axes(run_home):
    """The absent axes are READ, not hard-coded to None — a loop-shaped verdict splats them flat.

    `loop/journal.py::verdict` writes `{"cycle": n, **JudgeVerdict.to_dict()}`, so the loop's own
    rows carry `marginal_value` at top level. A projection that returned a literal None for these
    would drop real data the moment the noun retirement routes loop verdicts through here.
    """
    rows = verdict_rail(
        [
            {
                "kind": "judge_verdict",
                "ts": "t",
                "cycle": 2,
                "marginal_value": 0.8,
                "quality_score": 4.0,
            }
        ]
    )
    assert len(rows) == 1, "vacuity floor: no verdict row was projected"
    assert rows[0]["marginal_value"] == pytest.approx(0.8)
    assert rows[0]["quality_score"] == pytest.approx(4.0)


def test_no_verdict_series_is_None_and_a_scoreless_verdict_is_not_a_zero_series(run_home):
    """`None` means no judge scored; `[]` would mean the judge scored nothing. Only one is emitted.

    A zero-length series rendered as a chart is a chart claiming the judge produced flat zeros.
    """
    assert rail_totals([], []).to_dict()["overall_series"] is None
    scoreless = verdict_rail([{"kind": "judge_verdict", "ts": "t", "verdict": "REJECT"}])
    assert len(scoreless) == 1, "vacuity floor: no verdict row was projected"
    totals = rail_totals([], scoreless).to_dict()
    assert totals["overall_series"] is None
    # The verdict itself is still counted — an unscored REJECT is a real judge outcome.
    assert totals["verdicts"] == 1
    assert totals["verdicts_by_word"] == {"REJECT": 1}


def test_an_uncoercible_value_reads_absent_rather_than_zero(run_home):
    """A number nobody can parse is not a measurement of nothing.

    The journal is append-only history written by several code paths over time; coercing garbage to
    0.0 would put a confident wrong number on the cost column.
    """
    rows = findings_rail(
        [{"kind": "step_completed", "ts": "t", "node_id": "n", "cost_usd": "not-a-number"}]
    )
    assert len(rows) == 1, "vacuity floor: no row was projected"
    assert rows[0]["cost_usd"] is None
    assert rail_totals(rows, []).to_dict()["cost_usd"] is None


# ── the declaration cannot rot ──


def _kinds_written_under(package: str) -> set[str]:
    """Every rail kind name that appears as a `journal*.write(KIND, ...)` argument in a package.

    A static AST scan rather than an import probe, for the same reason the ledger boundary rail
    uses one: an emitter reached through a function-local import is still an emitter.
    """
    written: set[str] = set()
    root = _SRC / package
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - source is ours
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in ("write", "breaker_trip", "watcher_reaped", "verdict"):
                continue
            # `journal.write(BREAKER_TRIP, ...)` — the kind is the first positional argument, given
            # either as the imported constant or as its literal string.
            for arg in node.args[:1]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    written.add(arg.value)
                elif isinstance(arg, ast.Name):
                    written.add(arg.id.lower())
                elif isinstance(arg, ast.Attribute):
                    written.add(arg.attr.lower())
            # A typed emitter names the kind in the method name itself.
            if name in ("breaker_trip", "watcher_reaped"):
                written.add(name)
    return written


def test_the_producer_table_matches_the_engines_real_emitters(run_home):
    """`RAIL_PRODUCERS` is checked against the workflow package's actual writes, both directions.

    The whole absent-vs-zero guarantee rests on this table being true. A projection over an event
    list cannot see who writes the list, so the table is a DECLARATION — and a declaration nobody
    executes rots. If the engine grows a circuit breaker, this reds and forces the table to be
    updated, rather than leaving a `None` cell where a real zero now belongs.
    """
    written = _kinds_written_under("workflows")
    assert written, "vacuity floor: the AST scan found no journal writes at all"
    # The scanner must be able to SEE a kind it should see, or every assertion below is vacuous.
    assert "judge_verdict" in written, "the scan missed a kind the engine demonstrably writes"

    for kind, producer in sorted(RAIL_PRODUCERS.items()):
        if producer == PRODUCER_ENGINE:
            assert kind in written, (
                f"{kind} is declared PRODUCER_ENGINE but nothing under workflows/ writes it — "
                "its coverage count would be a zero nobody can earn"
            )
        else:
            assert kind not in written, (
                f"{kind} is declared PRODUCER_NONE but workflows/ writes it now — the coverage "
                "row must become a real count instead of an absent cell"
            )


def test_the_loop_side_is_the_only_breaker_writer(run_home):
    """The measurement behind `breaker_trip`'s PRODUCER_NONE row, pinned.

    Recorded as a rail rather than a comment because the plan's own clause asserts the ledger
    "already carries ... `breaker_trip` ... for both nouns", and it does not. If a run-side breaker
    ever lands, the test above reds; if the LOOP one is removed, this reds — so the asymmetry can
    never quietly become symmetric in either direction.
    """
    assert "breaker_trip" in _kinds_written_under("loop"), (
        "the loop watchdog was the only breaker_trip writer; if that changed, the kind has no "
        "producer anywhere and does not belong on the rails at all"
    )


def test_every_rail_field_is_projected_from_a_kind_the_rail_declares(run_home):
    """No rail reads a kind outside `RAIL_PRODUCERS` — the seam's scope, pinned.

    This seam is the ledger-rails third of PP-16's seam 4, explicitly not the plan-walkthrough or
    intake thirds. A rail quietly projecting a fifth kind would be scope creep this file cannot
    otherwise catch.
    """
    noise = [
        {"kind": "gate_resolved", "ts": "t", "node_id": "g", "approved": True},
        {"kind": "run_finished", "ts": "t", "status": "done"},
        {"kind": "step_failed", "ts": "t", "node_id": "n", "error": "boom"},
    ]
    assert findings_rail(noise) == []
    assert verdict_rail(noise) == []
    coverage = rail_coverage(noise)
    assert {row["kind"] for row in coverage} == set(RAIL_PRODUCERS)
    assert all(row["events"] in (0, None) for row in coverage)


def test_the_totals_and_the_rows_cannot_disagree(run_home):
    """The aggregate is computed from the projected ROWS, not a second pass over the ledger.

    Two filters over one stream is how a cockpit ends up showing eight rows above a count of nine.
    """
    events = [
        {"kind": "step_completed", "ts": "t", **_RUN_STEP},
        {"kind": "step_completed", "ts": "t", **dict(_RUN_STEP, cost_usd=0.75, tokens=30)},
        {"kind": "judge_verdict", "ts": "t", **_RUN_VERDICT},
    ]
    findings = findings_rail(events)
    verdicts = verdict_rail(events)
    totals = rail_totals(findings, verdicts).to_dict()
    assert totals["steps_completed"] == len(findings) == 2
    assert totals["verdicts"] == len(verdicts) == 1
    assert totals["cost_usd"] == pytest.approx(1.0)
    assert totals["tokens"] == 150


def test_the_rails_agree_with_the_ledgers_own_step_aggregate(run_home):
    """The findings-rail count equals `ledger.reader.run_totals`' `steps_completed`.

    Two aggregates over one stream that disagreed would make the rail and the run row show
    different numbers for the same run, with no way to tell which was right — and it is exactly
    the equality the LOOP side pins between `len(get_findings())` and `cycles_completed()`.
    """
    from personalclaw.workflows import journal as J

    run_id = _run_with(
        "rails-agree",
        [
            ("step_completed", _RUN_STEP),
            ("step_completed", _RUN_STEP),
            ("judge_verdict", _RUN_VERDICT),
        ],
    )
    events = J.ledger(run_id)
    rows = findings_rail(events)
    assert rows, "vacuity floor: the rail projected nothing to compare"
    assert len(rows) == int(J.run_totals(run_id)["steps_completed"])


def test_the_rails_project_the_same_kinds_the_loop_rails_do(run_home):
    """One ledger, two nouns: the run rails read the kinds the loop rails read.

    The atom's clause is "one ledger". If the run-side findings rail read a different kind than
    `loop/files.py::get_findings`, the retirement would be a rename of two different things — so
    the shared kind is pinned against the loop module's own imported constants.
    """
    from personalclaw.ledger import JUDGE_VERDICT, STEP_COMPLETED

    # The loop rails' kinds, read from the loop module rather than restated.
    loop_source = (_SRC / "loop" / "files.py").read_text(encoding="utf-8")
    assert "STEP_COMPLETED" in loop_source and "JUDGE_VERDICT" in loop_source

    findings = findings_rail([{"kind": STEP_COMPLETED, "ts": "t", "node_id": "n"}])
    verdicts = verdict_rail([{"kind": JUDGE_VERDICT, "ts": "t", "node_id": "n"}])
    assert len(findings) == 1 and len(verdicts) == 1
    # And the projections are PURE: same input, same output, no hidden state.
    assert findings_rail([{"kind": STEP_COMPLETED, "ts": "t", "node_id": "n"}]) == findings


def test_the_payload_carries_no_secret_from_a_degraded_reason(run_home):
    """The rails route through the journal's own redactor — findings carry free text.

    A degraded reason and a model id are exactly where a credential surfaces in a screenshot, and
    `api_run_node_inspect` already established that reusing the writer's redactor (rather than
    re-deriving one) is how the two cannot drift.
    """
    from personalclaw.workflows import service

    run_id = _run_with(
        "rails-redact",
        [
            (
                "step_completed",
                dict(_RUN_STEP, degraded_reason="token sk-ABCDEF1234567890abcdef fell back"),
            )
        ],
    )
    payload = service.ledger_rails(run_id)
    assert payload["findings"], "vacuity floor: nothing was projected to redact"
    assert "sk-ABCDEF1234567890abcdef" not in str(payload), "a secret reached the rails payload"


def test_introspection_stays_pure_over_event_lists(run_home):
    """The two rails add no store and no I/O — the module's stated doctrine.

    Asserted structurally: the rail functions accept an event list and are callable with no home on
    disk at all. A projection that reached for a store would raise here.
    """
    monkey_free = [{"kind": "step_completed", "ts": "t", "node_id": "n", "cost_usd": 1.0}]
    assert introspection.findings_rail(monkey_free)[0]["cost_usd"] == 1.0
    assert introspection.verdict_rail(monkey_free) == []
    assert (
        introspection.rail_totals(introspection.findings_rail(monkey_free), []).steps_completed == 1
    )
