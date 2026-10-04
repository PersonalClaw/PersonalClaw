"""A step after a loop reads what the loop produced, and validation accepts only reads a run can
resolve.

🔴 Before: the engine recorded no output for a loop, so a step that read `{{nodes.<loop>.output}}`
failed "unresolved reference" once the loop had run, which is where three bundled templates
(optimize-harness, design-project, goal-pursuit-open-ended) stopped. Validation accepted a read of
any node, so nothing said so when the workflow was saved.

Now a loop that ends done records its last cycle's output under its id, stored with its instance so
a resumed run reads it back; and a read of a kind that records nothing (a sequence, parallel or
foreach) is refused when the spec is saved, from the one definition the engine records outputs by
(`models.NO_OUTPUT_KINDS`). The table test runs every node kind through both the validator and
the engine and holds them to the same answer. And a value that begins and ends with a reference
is split into references the way validation splits it, so a value holding two resolves both.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun
from personalclaw.workflows.validator import validate_spec

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


#: A loop of two cycles, each producing which cycle it is. Its last cycle produced `{"cycle": 1}`.
LOOP = {
    "kind": "loop",
    "id": "p",
    "config": {"mode": "counted", "n": 2},
    "body": {"kind": "transform", "id": "cycle", "config": {"expr": {"cycle": "{{iter}}"}}},
}

#: One producer of each kind, every one of them able to finish on its own (transforms inside).
PRODUCERS: dict[str, dict[str, Any]] = {
    "loop": LOOP,
    "branch": {
        "kind": "branch",
        "id": "p",
        "config": {"on": "{{inputs.k}}", "enum": ["a"]},
        "cases": {"a": {"kind": "transform", "id": "taken", "config": {"expr": "taken"}}},
    },
    "transform": {"kind": "transform", "id": "p", "config": {"expr": "a step's own value"}},
    "sequence": {
        "kind": "sequence",
        "id": "p",
        "children": [{"kind": "transform", "id": "inner", "config": {"expr": "inside"}}],
    },
    "parallel": {
        "kind": "parallel",
        "id": "p",
        "children": [{"kind": "transform", "id": "inner", "config": {"expr": "inside"}}],
    },
    "foreach": {
        "kind": "foreach",
        "id": "p",
        "config": {"items": "{{inputs.xs}}"},
        "body": {"kind": "transform", "id": "inner", "config": {"expr": "{{item}}"}},
    },
}

INPUTS = {"k": "a", "xs": ["one", "two"]}


def _then_read(producer: dict[str, Any], read: str = "{{nodes.p.output}}") -> dict[str, Any]:
    return {
        "name": "reads-a-step",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [producer, {"kind": "transform", "id": "reader", "config": {"expr": read}}],
        },
    }


async def _run(spec: dict[str, Any]) -> tuple[RunController, RunStatus]:
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs=dict(INPUTS)))
    store.write_spec(run.id, spec)
    ctl = RunController(run, spec, services=EngineServices())
    return ctl, await ctl.run_to_completion(timeout=20)


def _codes(spec: dict[str, Any]) -> list[str]:
    return [issue.code for issue in validate_spec(spec).issues]


# ── a step after a loop reads what its last cycle produced ───────────────────


async def test_a_step_after_a_loop_reads_what_its_last_cycle_produced() -> None:
    """🔴 Red before: the reader failed "unresolved reference at 'p'" and the run failed."""
    ctl, status = await _run(_then_read(LOOP, "{{nodes.p.output.cycle}}"))

    assert status == RunStatus.COMPLETE, ctl.run.error_message
    assert ctl._outputs["reader"] == 1


async def test_the_loop_output_is_kept_with_its_step_and_read_back_on_resume() -> None:
    """Recorded as any step's output is: stored with the loop's instance, so a run that restarts
    reads the same value. 🔴 Red before: the loop's instance held no output, and a restarted run
    read the loop as having produced nothing at all (None)."""
    ctl, status = await _run(_then_read(LOOP))
    assert status == RunStatus.COMPLETE, ctl.run.error_message

    loop_inst = ctl.instances["root.children[0]"]
    assert loop_inst.state == InstanceState.DONE
    assert loop_inst.output_ref, "the loop's output was not stored with its step"
    assert store.read_output(ctl.run.id, "root.children[0]") == {"cycle": 1}

    resumed = RunController(store.get(ctl.run.id), ctl.spec, services=EngineServices())
    assert resumed._outputs["p"] == {"cycle": 1}


async def test_a_loop_handed_to_a_person_records_nothing_and_its_reader_does_not_run() -> None:
    """Only a loop that ended DONE records an output: one that stopped at its budget without
    meeting its exit produced no result, so the step that reads it is skipped, never run on a
    value the loop did not stand behind."""
    unfinished = {
        "kind": "loop",
        "id": "p",
        "config": {"mode": "until", "condition": "{{last.output.done}}", "max_iterations": 2},
        "body": {"kind": "transform", "id": "cycle", "config": {"expr": {"done": False}}},
    }
    ctl, status = await _run(_then_read(unfinished))

    assert status == RunStatus.ESCALATED, ctl.run.error_message
    assert ctl.instances["root.children[0]"].output_ref == ""
    assert ctl.instances["root.children[1]"].state == InstanceState.SKIPPED
    assert "p" not in ctl._outputs


# ── a read that can never resolve is refused when the spec is saved ──────────


@pytest.mark.parametrize("kind", ["sequence", "parallel", "foreach"])
def test_a_read_of_a_step_that_records_nothing_is_refused_when_saved(kind: str) -> None:
    """🔴 Red before: the spec validated, and every run of it failed at the reader."""
    result = validate_spec(_then_read(PRODUCERS[kind]))

    assert not result.ok
    refused = [i for i in result.errors if i.code == "WF_UNSATISFIABLE_OUTPUT_REF"]
    assert [i.path for i in refused] == ["root.children[1]"], result.to_dict()
    assert f"'p' is a {kind}, which records no output of its own" in refused[0].message
    # One defect, one error: moving the producer would not help, so the ordering rule says
    # nothing about the same read.
    assert "WF_UNORDERED_DEP" not in _codes(_then_read(PRODUCERS[kind]))


def test_an_artifact_read_of_a_step_that_records_nothing_is_refused_too() -> None:
    """Every root under `nodes.<id>` is unresolvable for such a step, not only `output`."""
    spec = _then_read(PRODUCERS["parallel"], "{{nodes.p.artifact}}")
    assert "WF_UNSATISFIABLE_OUTPUT_REF" in _codes(spec)


@pytest.mark.parametrize("kind", ["loop", "branch", "transform"])
def test_a_read_of_a_step_that_records_an_output_validates(kind: str) -> None:
    """The other side: a loop records its last cycle's output and a branch its routing."""
    assert validate_spec(_then_read(PRODUCERS[kind]), strict=True).ok


async def test_a_run_that_meets_a_read_of_a_sequence_says_why_it_cannot_resolve() -> None:
    """A spec saved before the rule still runs, and its reader's failure names the cause rather
    than asking the author to check a value that was never there. 🔴 Red before: "check that the
    value really carries 'p'"."""
    ctl, status = await _run(_then_read(PRODUCERS["sequence"]))

    assert status == RunStatus.FAILED
    failure = ctl.instances["root.children[1]"].failure
    assert failure is not None
    assert "unresolved reference at 'p'" in failure.cause_plain
    assert "A sequence, parallel or foreach records none of its own" in failure.remediation


# ── the validator and the engine give the same answer, kind by kind ─────────


async def test_the_validator_accepts_exactly_the_reads_the_engine_resolves() -> None:
    """One table, every node kind that can be read: what validation accepts is what a run
    resolves, and what it refuses is what a run cannot. 🔴 Red before: validation accepted all
    six while the run resolved three (the loop among the three it could not)."""
    table: dict[str, tuple[bool, bool]] = {}
    for kind, producer in PRODUCERS.items():
        spec = _then_read(producer)
        accepted = validate_spec(spec).ok
        ctl, status = await _run(spec)
        resolved = (
            status == RunStatus.COMPLETE
            and ctl.instances["root.children[1]"].state == InstanceState.DONE
        )
        table[kind] = (accepted, resolved)

    disagree = {kind: verdicts for kind, verdicts in table.items() if verdicts[0] != verdicts[1]}
    assert disagree == {}, table
    # Both answers occur, and the refused ones are exactly the definition's kinds: a table in
    # which everything resolved, or nothing did, would agree for the wrong reason.
    refused = {kind for kind, (accepted, _resolved) in table.items() if not accepted}
    assert refused == {"sequence", "parallel", "foreach"}
    assert set(table) - refused == {"loop", "branch", "transform"}
    from personalclaw.workflows.models import NO_OUTPUT_KINDS

    assert {kind.value for kind in NO_OUTPUT_KINDS} == refused


# ── positive control ─────────────────────────────────────────────────────────


async def test_an_ordinary_multi_step_workflow_still_runs() -> None:
    """Steps reading steps, the shape almost every workflow has, unchanged."""
    spec = {
        "name": "ordinary",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "transform", "id": "seed", "config": {"expr": {"n": 7, "word": "seven"}}},
                {
                    "kind": "transform",
                    "id": "double",
                    "config": {"expr": "{{nodes.seed.output.n}}"},
                },
                {
                    "kind": "transform",
                    "id": "say",
                    "config": {"expr": "So {{nodes.seed.output.word}} is {{nodes.double.output}}"},
                },
            ],
        },
    }
    assert validate_spec(spec, strict=True).ok
    ctl, status = await _run(spec)

    assert status == RunStatus.COMPLETE, ctl.run.error_message
    assert ctl._outputs["double"] == 7, "a value that is one reference keeps its type"
    assert ctl._outputs["say"] == "So seven is 7"


# ── a value holding two references is read as two ───────────────────────────


async def test_a_value_that_begins_and_ends_with_a_reference_resolves_each_of_them() -> None:
    """Validation splits a value into references with one scan and the run used another, which
    read anything beginning with `{{` and ending with `}}` as ONE reference. 🔴 Red before: the
    step failed "unresolved reference at 'output}} changed: {{nodes'" for a value validation had
    accepted, the shape of the bundled market-monitor's alert title."""
    spec = {
        "name": "two-references",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "transform", "id": "watch", "config": {"expr": "the price"}},
                {"kind": "transform", "id": "assess", "config": {"expr": {"headline": "it rose"}}},
                {
                    "kind": "transform",
                    "id": "title",
                    "config": {
                        "expr": "{{nodes.watch.output}} changed: {{nodes.assess.output.headline}}"
                    },
                },
            ],
        },
    }
    assert validate_spec(spec, strict=True).ok
    ctl, status = await _run(spec)

    assert status == RunStatus.COMPLETE, ctl.run.error_message
    assert ctl._outputs["title"] == "the price changed: it rose"
