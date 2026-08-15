"""The trajectory signature and template regression detection (PP-7).

The atom's bar, verbatim: a `trajectory_signature` derived as a PURE projection over the existing
ledger (ordered node/lane/verdict tuples) with no new store; a regression signal that fires when a
template's runs shift to a signature class that historically failed more often; and verified by —
two runs of one template with the same inputs producing EQUAL signatures, a rewind producing a
DISTINGUISHABLE one, and the projection proven pure by computing it twice over a frozen ledger.

Three falsification hooks live here as real assertions, so the mutations in the PR description have
something to turn red:

* ``test_the_signature_is_pure_over_a_frozen_ledger`` — put a timestamp (or any per-call value) in
  the signature and this reds: the projection stops being a pure function of the events.
* ``test_two_runs_with_the_same_inputs_have_equal_signatures`` — perturb the signature with anything
  that varies per run (the run_id, say) and two identical runs stop matching, so this reds.
* ``test_a_young_template_is_not_flagged_as_regressed`` — drop the sample floor to 0 and a template
  that has barely run gets flagged on its first new path, so this reds.

The projection-level tests write REAL journal events with the real ``Journal`` (the convention this
module's sibling ``test_workflows_introspection`` established) rather than hand-built dicts, so the
field names and event kinds are whatever the engine actually writes. The end-to-end tests drive a
real controller run and read its genuine ledger back.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from personalclaw.workflows import introspection as intro
from personalclaw.workflows import journal as journal_mod

# ── the projection: ordered node/lane/verdict tuples ───────────────────────────


def _ev(kind: str, **kw: Any) -> dict[str, Any]:
    return {"kind": kind, **kw}


def test_a_completed_step_projects_node_lane_and_terminal_state() -> None:
    """`lane` is read off the `step_started` a node emitted — the one event that carries it — and
    the verdict is the completed step's terminal state."""
    steps = intro.trajectory_steps(
        [
            _ev("step_started", instance_path="a", node_id="a", lane="llm"),
            _ev("step_completed", instance_path="a", node_id="a", state="done"),
        ]
    )
    assert steps == [("a", "llm", "done")]


def test_a_degraded_success_is_a_different_verdict_than_a_clean_one() -> None:
    """A `degraded` completion took a different path and must not collapse into a clean `done`."""
    clean = intro.trajectory_signature("r1", [_ev("step_completed", node_id="a", state="done")])
    degraded = intro.trajectory_signature(
        "r2", [_ev("step_completed", node_id="a", state="degraded")]
    )
    assert clean.signature != degraded.signature


def test_a_skipped_branch_leg_has_no_lane_but_still_marks_the_path() -> None:
    """The engine skips an untaken branch leg WITHOUT launching it, so there is no `step_started`
    and no recorded lane. It contributes "" rather than a guess — which is deterministic."""
    steps = intro.trajectory_steps([_ev("step_skipped", instance_path="b", node_id="b")])
    assert steps == [("b", "", "skipped")]


def test_gate_and_judge_verdicts_are_on_the_path() -> None:
    steps = intro.trajectory_steps(
        [
            _ev("gate_resolved", node_id="g", approved=True),
            _ev("gate_resolved", node_id="h", approved=False),
            _ev("judge_verdict", node_id="j", verdict="pass"),
        ]
    )
    assert steps == [
        ("g", "", "gate:approved"),
        ("h", "", "gate:rejected"),
        ("j", "", "judge:PASS"),
    ]


def test_the_projection_is_NOT_deduped_by_path() -> None:
    """A rewind re-runs a node and appends its terminal event again. The signature must keep BOTH
    — that non-dedup is the whole mechanism that makes a rewound run distinguishable. If this
    collapsed to one step, a rewind that lands on the same node would read as the clean run."""
    one_event = [_ev("step_completed", node_id="a", state="done")]
    two_events = [
        _ev("step_completed", node_id="a", state="done"),
        _ev("step_completed", node_id="a", state="done"),
    ]
    assert len(intro.trajectory_steps(one_event)) == 1
    assert len(intro.trajectory_steps(two_events)) == 2
    sig_one = intro.trajectory_signature("r", one_event).signature
    sig_two = intro.trajectory_signature("r", two_events).signature
    assert sig_two != sig_one


def test_a_malformed_event_is_skipped() -> None:
    steps = intro.trajectory_steps(
        [None, "not a dict", _ev("step_completed", node_id="a", state="ok")]
    )
    assert steps == [("a", "", "ok")]


def test_an_empty_ledger_projects_to_a_stable_empty_signature() -> None:
    a = intro.trajectory_signature("r1", [])
    b = intro.trajectory_signature("r2", [])
    assert a.steps == [] and a.length == 0
    assert a.signature == b.signature  # the empty path is one class, not per-run noise


# ── purity: compute twice over a frozen ledger ─────────────────────────────────


def _frozen_ledger() -> list[dict[str, Any]]:
    return [
        _ev("step_started", instance_path="a", node_id="a", lane="llm"),
        _ev("step_completed", instance_path="a", node_id="a", state="done"),
        _ev("gate_resolved", instance_path="g", node_id="g", approved=True),
        _ev("step_started", instance_path="b", node_id="b", lane="io"),
        _ev("step_completed", instance_path="b", node_id="b", state="done"),
    ]


def test_the_signature_is_pure_over_a_frozen_ledger() -> None:
    """FALSIFICATION 1: introduce nondeterminism into the signature (a timestamp, an unordered set)
    and computing it twice over the SAME frozen ledger stops returning the same string — this reds.

    The ledger is deep-frozen so the projection cannot mutate its own input either."""
    ledger = _frozen_ledger()
    snapshot = copy.deepcopy(ledger)

    first = intro.trajectory_signature("run-x", ledger).signature
    second = intro.trajectory_signature("run-x", ledger).signature

    assert first == second, "the signature is not a pure function of the events"
    assert ledger == snapshot, "the projection mutated its own input"


def test_the_signature_ignores_the_run_id_it_is_told() -> None:
    """The signature is a property of the PATH, not of which run took it — so the run_id passed for
    labelling must never reach the hash. (Also the mechanism behind same-inputs-equal.)"""
    ledger = _frozen_ledger()
    assert intro.trajectory_signature("run-A", ledger).signature == (
        intro.trajectory_signature("run-B", ledger).signature
    )


# ── the regression signal ──────────────────────────────────────────────────────


def _runs(*specs: tuple[str, bool, int]) -> list[tuple[str, bool]]:
    """Expand (signature, failed, count) triples into a flat oldest-first run list."""
    out: list[tuple[str, bool]] = []
    for sig, failed, count in specs:
        out.extend([(sig, failed)] * count)
    return out


def test_a_shift_to_a_worse_failing_class_fires() -> None:
    """A template that used to run the `stable` path (rarely failing) and has now shifted onto the
    `broken` path (always failing) is exactly the regression this signal exists to catch."""
    history = _runs(("stable", False, 5), ("stable", True, 1), ("broken", True, 6))
    reg = intro.trajectory_regression("nightly-digest", history)
    assert reg is not None
    assert reg.current_signature == "broken"
    assert reg.prior_signature == "stable"
    assert reg.current_failure_rate > reg.prior_failure_rate
    assert "shifted to a new path" in reg.message()


def test_no_shift_no_signal_even_at_a_high_failure_rate() -> None:
    """A template that has always failed on its ONE path did not regress — it was never good.
    The signal is about a SHIFT, not about a bad absolute rate (that is the failure_rate card)."""
    history = _runs(("only", True, 12))
    assert intro.trajectory_regression("t", history) is None


def test_a_shift_to_a_class_that_fails_no_more_often_does_not_fire() -> None:
    """Shifting paths is normal — a template edit re-routes runs all the time. Only a shift to a
    path that fails MORE is a regression; an equally-reliable new path is just a new path."""
    history = _runs(("old", False, 6), ("new", False, 6))
    assert intro.trajectory_regression("t", history) is None


def test_a_young_template_is_not_flagged_as_regressed() -> None:
    """FALSIFICATION 3: drop the sample floor to 0 and this reds.

    Eight runs — four clean on `old`, then four failing on `new` — is a real shift to a worse path,
    but eight runs is below `TRAJECTORY_REGRESSION_MIN_RUNS`: "the last few runs took a new path and
    failed" is a template that has barely run, not evidence it regressed. With the default floor the
    signal stays silent; set `TRAJECTORY_REGRESSION_MIN_RUNS = 0` and it fires on this young
    history, which is precisely the false alarm the gate prevents."""
    history = _runs(("old", False, 4), ("new", True, 4))
    # The substantive assertion: a young template's shift is NOT surfaced. Set the floor to 0 and
    # this reds because the signal fires on the young history.
    assert intro.trajectory_regression("t", history) is None
    # And the fixture really is a young sample under the default floor — this documents WHY the
    # signal is silent, and reds too if the floor is dropped.
    assert len(history) < intro.TRAJECTORY_REGRESSION_MIN_RUNS


def test_a_single_run_regime_is_below_the_per_class_floor() -> None:
    """Even with a long history, ONE run on the new path is an anecdote — a 100% failure rate over a
    single run is not a measurement. The tail must clear `MIN_CLASS_RUNS` before it counts."""
    history = _runs(("old", False, 11), ("new", True, 1))
    assert intro.trajectory_regression("t", history) is None


def test_the_regression_is_deterministic_over_a_frozen_history() -> None:
    history = _runs(("stable", False, 6), ("broken", True, 6))
    a = intro.trajectory_regression("t", history)
    b = intro.trajectory_regression("t", history)
    assert a is not None and b is not None and a.to_dict() == b.to_dict()


# ── end-to-end over real controller runs ───────────────────────────────────────


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def _isolated_home(tmp_path, monkeypatch):
    """Own config dir per test — these tests drive real runs and read the run store, and
    PERSONALCLAW_HOME is unset in the gate, so a real home must never be touched."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


async def _echo(prompt: str, *, use_case: str = "background", output_type: Any = None) -> str:
    """Deterministic recorded response: echo the resolved prompt, so two runs of one spec with the
    same inputs take a byte-identical path."""
    return f"[{prompt}]"


def _pipeline_spec() -> dict[str, Any]:
    return {
        "name": "traj-pipeline",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "infer", "id": "a", "config": {"prompt": "seed"}},
                {"kind": "infer", "id": "b", "config": {"prompt": "use {{nodes.a.output}}"}},
                {"kind": "infer", "id": "c", "config": {"prompt": "use {{nodes.b.output}}"}},
            ],
        },
    }


async def _drive(spec: dict[str, Any]) -> str:
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    run = store.create(WorkflowRun(id="", workflow_name=spec.get("name", "wf")))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(completion=_echo))
    status = await controller.run_to_completion(timeout=20)
    assert status.value == "complete", f"run did not complete: {status}"
    return run.id


@pytest.mark.anyio
async def test_two_runs_with_the_same_inputs_have_equal_signatures(_isolated_home) -> None:
    """FALSIFICATION 2: perturb the signature with anything that varies per run (the run_id) and two
    identical runs stop matching — this reds.

    Two real runs of one template with the same inputs take the same decision path, so their
    trajectory signatures are the same CLASS."""
    first = await _drive(_pipeline_spec())
    second = await _drive(_pipeline_spec())

    sig_first = intro.trajectory_signature(first, journal_mod.ledger(first))
    sig_second = intro.trajectory_signature(second, journal_mod.ledger(second))

    assert first != second  # genuinely two different runs
    assert sig_first.length >= 3  # the run actually did work — not a fake-clean empty ledger
    assert sig_first.signature == sig_second.signature


@pytest.mark.anyio
async def test_a_rewind_produces_a_distinguishable_signature(_isolated_home) -> None:
    """A rewind re-executes nodes and appends their terminal events to the run's ledger. Because the
    signature is projected over the ordered, non-deduped event stream, those extra events change it
    — so a rewound run is distinguishable from the clean run it started as.

    The rewind's ledger footprint is written here with the real `Journal` (a re-completed node),
    which is exactly what a rewind-and-re-run appends."""
    run_id = await _drive(_pipeline_spec())
    before = intro.trajectory_signature(run_id, journal_mod.ledger(run_id)).signature

    # Rewind at `b` re-runs b and c: the engine appends their re-execution to the same ledger.
    j = journal_mod.Journal(run_id)
    j.write(journal_mod.STEP_STARTED, instance_path="s/b", node_id="b", lane="llm")
    j.write(journal_mod.STEP_COMPLETED, instance_path="s/b", node_id="b", state="done")
    j.write(journal_mod.STEP_STARTED, instance_path="s/c", node_id="c", lane="llm")
    j.write(journal_mod.STEP_COMPLETED, instance_path="s/c", node_id="c", state="done")

    after = intro.trajectory_signature(run_id, journal_mod.ledger(run_id)).signature
    assert after != before


@pytest.mark.anyio
async def test_the_run_projection_exposes_the_trajectory_signature(_isolated_home) -> None:
    """`introspect(run_id)` carries the run's signature and its steps — exposed on the run
    projection, per the atom."""
    from personalclaw.workflows import service

    run_id = await _drive(_pipeline_spec())
    payload = service.introspect(run_id)

    assert payload["ok"] is True
    traj = payload["trajectory"]
    assert traj["signature"]
    assert traj["length"] >= 3
    assert {"node", "lane", "verdict"} <= set(traj["steps"][0])
    # The template distribution is a per-template projection carried on the run surface.
    assert traj["signature"] in traj["distribution"]
    # A template with a handful of runs has no regression finding.
    assert traj["regression"] is None


@pytest.mark.anyio
async def test_the_signature_is_queryable_per_template(_isolated_home) -> None:
    """`template_trajectory(name)` answers per template WITHOUT a run in hand — the distribution of
    signature classes across the template's runs, plus the (here empty) regression signal."""
    from personalclaw.workflows import service

    await _drive(_pipeline_spec())
    await _drive(_pipeline_spec())

    result = service.template_trajectory("traj-pipeline")
    assert result["ok"] is True
    assert result["runs"] == 2
    # Both runs took the same path with the same inputs → one signature class, seen twice.
    assert len(result["distribution"]) == 1
    assert sum(result["distribution"].values()) == 2
    assert result["regression"] is None
    assert {"run_id", "signature", "failed"} <= set(result["signatures"][0])
