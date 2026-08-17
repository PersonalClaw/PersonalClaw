"""WF2LOO-7 — the decision layers wired into the RunController tick.

These are the end-to-end assertions for LOOPS-EVOLUTION criteria 3 and 8, driven through a
real controller against a temp home with only the model call faked:

* **steering is consumed at the iteration boundary** (R14, criterion 8) — an instruction queued
  mid-run lands in the next iteration's prompt and is journaled as a `steering` ledger event,
  single-use;
* **judge verdicts reach the ledger with evidence** (R3, criterion 3) — a judge gate emits
  `judge_verdict` with its evidence chain and discard status, on both pass and reject;
* **a human override records `judge_divergence`** with the right direction;
* **a nodding loop is blocked from becoming its kind's default** (R6a) — a 100%-pass gate over
  enough runs fails `may_become_default`, a discriminating one passes;
* **the breaker is not double-run** — `resilience.check_breaker` remains the sole trip authority.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.workflows import controller as controller_mod
from personalclaw.workflows import journal as J
from personalclaw.workflows import judge_calibration as jc
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _make_run(spec: dict, inputs: dict | None = None, **kw) -> WorkflowRun:
    run = store.create(
        WorkflowRun(id="", workflow_name=spec.get("name", "wf"), inputs=inputs or {}, **kw)
    )
    store.write_spec(run.id, spec)
    return run


def _noop():
    async def fn(prompt, *, use_case="background", output_type=None):
        return "ok"

    return fn


class TestSteeringConsumedAtBoundary:
    async def test_a_queued_instruction_reaches_the_next_iteration_and_is_journaled(self) -> None:
        """Criterion 8: a mid-run steer is consumed at the boundary, re-plans, and is recorded.

        A `fresh`-session counted loop runs 3 iterations. Before iteration 2 the second
        iteration's prompt must carry the re-plan block; the ledger must carry one `steering`
        event; and the queue must be emptied (single-use).
        """
        prompts: list[str] = []

        async def recorder(prompt, *, use_case="background", output_type=None):
            prompts.append(prompt)
            # Queue a steer while the first iteration is running, so it is pending at the boundary.
            if len(prompts) == 1:
                store_run = store.get(run.id)
                store_run.extra["steering_queue"] = [
                    {"text": "focus on the login flow", "queued_at": "t"}
                ]
                store.save(store_run)
                # The live controller holds its own run object; write through to it too.
                c.run.extra["steering_queue"] = [
                    {"text": "focus on the login flow", "queued_at": "t"}
                ]
            return f"out{len(prompts)}"

        spec = {
            "name": "steerable",
            "root": {
                "kind": "loop",
                "id": "l",
                "config": {"mode": "counted", "n": 3, "session": "fresh"},
                "body": {"kind": "infer", "id": "b", "config": {"prompt": "work {{iter}}"}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices(completion=recorder))
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE

        steer_events = [r for r in J.ledger(run.id) if r["kind"] == J.STEERING]
        assert len(steer_events) == 1, "exactly one steering event for one queued instruction"
        assert steer_events[0]["texts"] == ["focus on the login flow"]

        # The re-plan block reached a later iteration's prompt (R14: re-rank, don't append).
        assert any("focus on the login flow" in p for p in prompts)
        assert any("re-rank your remaining sub-goals" in p for p in prompts)

        # Single-use: the durable queue is empty after consumption.
        assert store.get(run.id).extra.get("steering_queue") == []


class TestJudgeVerdictLedger:
    def _judge_spec(self) -> dict:
        return {
            "name": "judged",
            "root": {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {
                        "kind": "transform",
                        "id": "work",
                        "config": {"expr": "the deliverable is a complete and substantial report"},
                    },
                    {
                        "kind": "gate",
                        "id": "acc",
                        "config": {
                            "kind": "judge",
                            "prompt": "Is the deliverable acceptable?",
                            "evidence": "{{nodes.work.output}}",
                        },
                    },
                ],
            },
        }

    async def test_a_passing_judge_emits_a_verdict_with_evidence(self) -> None:
        async def judge(prompt, *, use_case="reasoning", output_type=None):
            # The contract object, not a bare word (WF2LOO-13): a PASS must cite proof or the
            # engine refuses it, and this stub is standing in for a judge that did the work.
            return '{"verdict": "PASS", "proof": "read the report; 4 sections present"}'

        run = _make_run(self._judge_spec())
        c = RunController(run, self._judge_spec(), services=EngineServices(completion=judge))
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE
        verdicts = [r for r in J.ledger(run.id) if r["kind"] == J.JUDGE_VERDICT]
        assert len(verdicts) == 1
        assert verdicts[0]["verdict"] == "PASS"
        assert verdicts[0]["status"] == "kept"
        assert verdicts[0]["evidence"]["samples"] == ["PASS"]
        assert verdicts[0]["template"] == "judged"

    async def test_a_rejecting_judge_emits_a_verdict_over_the_run(self) -> None:
        """Criterion 3: judges reject at least once, with evidence, on the ledger."""

        async def judge(prompt, *, use_case="reasoning", output_type=None):
            return '{"verdict": "REJECT", "reasoning": "section 3 cites nothing"}'

        run = _make_run(self._judge_spec())
        c = RunController(run, self._judge_spec(), services=EngineServices(completion=judge))
        await c.run_to_completion(timeout=25)
        verdicts = [r for r in J.ledger(run.id) if r["kind"] == J.JUDGE_VERDICT]
        assert verdicts, "a rejecting judge still records its verdict"
        assert any(v["verdict"] == "REJECT" for v in verdicts)
        assert all("evidence" in v for v in verdicts)

    async def test_a_human_override_records_a_divergence(self) -> None:
        """A judge PASS the human then rejects is a `false_pass` on the ledger (R3)."""
        run = _make_run(self._judge_spec())
        c = RunController(run, self._judge_spec(), services=EngineServices(completion=_noop()))
        # The judge already passed this node earlier in the run's history.
        c.journal.write(J.JUDGE_VERDICT, instance_path="s/acc", node_id="acc", verdict="PASS")
        c._emit_judge_divergence("s/acc", "acc", human_approved=False)
        div = [r for r in J.ledger(run.id) if r["kind"] == J.JUDGE_DIVERGENCE]
        assert len(div) == 1
        assert div[0]["direction"] == "false_pass"
        assert div[0]["judge_verdict"] == "PASS"
        assert div[0]["human_verdict"] == "REJECT"

    async def test_agreement_records_no_divergence(self) -> None:
        run = _make_run(self._judge_spec())
        c = RunController(run, self._judge_spec(), services=EngineServices(completion=_noop()))
        c.journal.write(J.JUDGE_VERDICT, instance_path="s/acc", node_id="acc", verdict="PASS")
        c._emit_judge_divergence("s/acc", "acc", human_approved=True)  # human agrees
        assert not [r for r in J.ledger(run.id) if r["kind"] == J.JUDGE_DIVERGENCE]


class TestNoddingLoopBlocksDefault:
    def _verdicts(self, verdict: str, n: int) -> list[jc.VerdictRecord]:
        return [
            jc.VerdictRecord(run_id=f"r{i}", node_id="acc", template="cadence", verdict=verdict)
            for i in range(n)
        ]

    def test_a_hundred_percent_pass_gate_is_refused_promotion(self) -> None:
        allowed, reason = jc.may_become_default(
            self._verdicts("PASS", jc.NODDING_MIN_RUNS), template="cadence"
        )
        assert allowed is False
        assert "nodding" in reason.lower()

    def test_a_discriminating_gate_is_allowed(self) -> None:
        records = self._verdicts("PASS", jc.NODDING_MIN_RUNS) + self._verdicts("REJECT", 1)
        allowed, reason = jc.may_become_default(records, template="cadence")
        assert allowed is True
        assert reason == ""

    def test_too_few_runs_is_not_blocked(self) -> None:
        # A new template with only a couple of clean runs is UNPROVEN, not a nodder.
        allowed, _ = jc.may_become_default(self._verdicts("PASS", 2), template="cadence")
        assert allowed is True


class TestBreakerNotDoubleRun:
    async def test_the_thrashing_loop_trips_via_check_breaker_only(self) -> None:
        """A loop repeating identical output escalates on the shipped breaker — and there is no
        second DETECTOR (`loop_middleware`'s counter breaker was deleted in PP-15, not wired).

        PP-15 changed what a trip MEANS, so this test changed with it. It used to assert the run
        stopped in "far fewer than 20" iterations, which encoded the BINARY failure: the first
        trip went straight to a human, making every middle rung of the declared ladder
        unreachable in production. The properties that actually matter survive and are now
        asserted more strictly:

        * the breaker is still the only thing that DETECTS the thrash;
        * the response walks the ladder before spending a human; and
        * the run still terminates in `ESCALATED`, by SURFACING rather than by drifting into its
          `max_iterations` cap — a ladder that merely delayed the cap would be no better than
          the binary stop it replaced, and the cap-vs-surface assertion is what tells them apart.
        """
        spec = {
            "name": "thrash",
            "root": {
                "kind": "loop",
                "id": "l",
                "config": {
                    "mode": "until_dry",
                    "streak": 5,
                    "max_iterations": 20,
                    "identical_output": 3,
                },
                "body": {"kind": "transform", "id": "b", "config": {"expr": "same"}},
            },
        }
        run = _make_run(spec)
        c = RunController(run, spec, services=EngineServices())
        await c.run_to_completion(timeout=25)
        iters = [r for r in J.ledger(run.id) if r["kind"] == J.ITERATION]
        outcomes = [str(r.get("outcome", "")) for r in iters]

        # The breaker DETECTED it, and it is the only detector that did.
        assert any("breaker:identical_output" in o for o in outcomes), outcomes
        assert not any(
            "max_iterations" in o for o in outcomes
        ), f"it drifted into its cap instead of surfacing — the ladder only delayed: {outcomes}"

        # The response walked the ladder instead of spending a human on the first trip.
        entry = next(iter((run.extra.get("convergence") or {}).values()), {})
        rungs = [d.get("rung") for d in entry.get("log") or []]
        assert rungs, f"a tripped breaker produced no convergence decision: {run.extra}"
        assert rungs[0] is None, f"the first trip cost a rung instead of a nudge: {rungs}"
        assert rungs[-1] == "surface", f"the ladder never reached its terminal rung: {rungs}"
        assert (
            len([r for r in rungs if r and r != "surface"]) >= 2
        ), f"the middle of the ladder is still unreachable: {rungs}"

        # And it still ends where a human can act on it.
        assert run.status == RunStatus.ESCALATED, run.status


# ── until_dry reads the DECLARED progress field (WF2LOO-14) ──


def _progress_spec(*, field: str = "new_findings_count", streak: int = 2, cap: int = 6) -> dict:
    """A loop shaped like the two shipped templates that declare a progress field.

    The shape is the point: the field is emitted by the FIRST stage of a sequence body, and
    the iteration ENDS on a judge stage whose schema has no such key. A dryness rule that
    only reads the last leaf's output can never see it.
    """
    return {
        "name": "progress-field",
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {
                "mode": "until_dry",
                "streak": streak,
                "progress_field": field,
                "max_iterations": cap,
            },
            "body": {
                "kind": "sequence",
                "id": "cycle-body",
                "children": [
                    {
                        "kind": "infer",
                        "id": "cycle",
                        "config": {
                            "prompt": "work {{iter}}",
                            "schema": {"summary": "string", field: "integer"},
                        },
                    },
                    {
                        "kind": "infer",
                        "id": "judge",
                        "config": {
                            "prompt": "judge {{iter}}",
                            "schema": {"verdict": "string", "reasoning": "string"},
                        },
                    },
                ],
            },
        },
    }


def _cycles(values: list, *, field: str = "new_findings_count", emit: bool = True):
    """Fake completion: the work stage reports `values` in order, the judge always passes.

    The judge's output carries the iteration counter so it is never byte-identical twice
    running — otherwise `check_breaker`'s `identical_output` rule ends the loop first and the
    test would prove nothing about dryness.
    """
    seen: list = []

    async def fn(prompt, *, use_case="background", output_type=None):
        if prompt.startswith("work"):
            idx = len(seen)
            seen.append(idx)
            body: dict = {"summary": f"cycle {idx}"}
            if emit:
                body[field] = values[idx] if idx < len(values) else values[-1]
            return json.dumps(body)
        return json.dumps({"verdict": "PASS", "reasoning": f"pass {len(seen)}"})

    return fn


def _iteration_outcomes(run_id: str) -> list[str]:
    return [str(r.get("outcome", "")) for r in J.ledger(run_id) if r["kind"] == J.ITERATION]


class TestProgressFieldDecidesDryness:
    """Driven through a real controller, not a unit call on the predicate.

    Before this, `_is_dry` read the whole last output — the judge's non-empty dict — so a
    cycle reporting `new_findings_count: 0` counted as progress and the loop always ran to
    its cap, paying for a model call per iteration to learn nothing.
    """

    async def test_two_zero_progress_iterations_end_the_loop(self) -> None:
        spec = _progress_spec(streak=2, cap=6)
        run = _make_run(spec)
        services = EngineServices(completion=_cycles([0, 0, 0, 0, 0, 0]))
        c = RunController(run, spec, services=services)
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE
        outcomes = _iteration_outcomes(run.id)
        assert len(outcomes) == 2, f"streak 2 must stop at 2 iterations, not the cap: {outcomes}"
        assert outcomes[-1] == "dry_streak"

    async def test_reported_progress_does_not_end_the_loop(self) -> None:
        spec = _progress_spec(streak=2, cap=4)
        run = _make_run(spec)
        services = EngineServices(completion=_cycles([3, 3, 3, 3]))
        c = RunController(run, spec, services=services)
        await c.run_to_completion(timeout=25)
        outcomes = _iteration_outcomes(run.id)
        assert len(outcomes) == 4, f"a loop reporting progress runs to its cap: {outcomes}"
        assert "dry_streak" not in outcomes

    async def test_progress_resets_the_streak(self) -> None:
        """streak semantics are CONSECUTIVE — one productive cycle clears the count."""
        spec = _progress_spec(streak=2, cap=6)
        run = _make_run(spec)
        services = EngineServices(completion=_cycles([0, 4, 0, 0]))
        c = RunController(run, spec, services=services)
        assert await c.run_to_completion(timeout=25) == RunStatus.COMPLETE
        outcomes = _iteration_outcomes(run.id)
        assert len(outcomes) == 4, f"dry, progress, dry, dry: {outcomes}"
        assert outcomes[-1] == "dry_streak"

    async def test_an_absent_field_does_not_cut_the_loop_short(self) -> None:
        """The body forgot the key. Falling back to the whole-output rule keeps working;
        reading absence as dryness would end a productive run after `streak` iterations."""
        spec = _progress_spec(streak=2, cap=4)
        run = _make_run(spec)
        services = EngineServices(completion=_cycles([0, 0, 0, 0], emit=False))
        c = RunController(run, spec, services=services)
        await c.run_to_completion(timeout=25)
        outcomes = _iteration_outcomes(run.id)
        assert len(outcomes) == 4, f"absence must not end the loop early: {outcomes}"
        assert "dry_streak" not in outcomes


class TestProgressReadingTable:
    """The per-type rule, stated exhaustively — no default branch guesses for anyone."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, controller_mod._DRY),
            (False, controller_mod._DRY),
            (True, controller_mod._PROGRESS),
            (0, controller_mod._DRY),
            (0.0, controller_mod._DRY),
            (7, controller_mod._PROGRESS),
            (-1, controller_mod._PROGRESS),
            ("", controller_mod._DRY),
            ("   \n", controller_mod._DRY),
            ("two new files", controller_mod._PROGRESS),
            (b"", controller_mod._DRY),
            (b"x", controller_mod._PROGRESS),
            ([], controller_mod._DRY),
            ([1], controller_mod._PROGRESS),
            ((), controller_mod._DRY),
            (set(), controller_mod._DRY),
            ({}, controller_mod._DRY),
            ({"a": 1}, controller_mod._PROGRESS),
            (object(), controller_mod._UNREADABLE),
        ],
    )
    def test_each_type_has_a_stated_reading(self, value, expected) -> None:
        assert controller_mod._progress_reading(value) == expected
