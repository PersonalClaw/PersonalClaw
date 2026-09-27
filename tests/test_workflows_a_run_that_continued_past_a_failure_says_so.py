"""A run that continued past a failure says what failed, and that it went on.

`on_error: null_continue` is the default, so when a step fails the steps after it still run, and
the run ends `failed`. On `main` that ending carried no sentence at all: the terminal write on the
completion path (`RunController._step`, when the frontier is complete) passed no `error`, so the run
page showed "Failed" with an empty line under it, the Workflows list showed "Failed" and nothing
else, and the journal's `run_finished` said nothing either. A person had to open the steps one by
one to learn which one broke, and nothing said that the steps after it had run anyway.

What these pin, against the real controller:

* the run's error names the step that failed and why, and says the run continued past it — on the
  run row, in the journal's `run_finished`, and in the live `workflow_run_update`;
* a failure that nothing ran after does not claim the run went on, and neither does one in a
  `parallel` leg, whose siblings ran beside it rather than after it;
* every failure is named, a fan-out item by its item, and a judge that escalated says it escalated;
* a failure the run tolerated (`allow_failure`) is not a reason the run failed, and a clean run
  ends with no sentence at all.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio

#: Long enough for the judge's free rule tier (`judge_pretier`) to let it through to the model.
EVIDENCE = "A substantial deliverable with plenty of characters for the pre-tier to allow through."

#: What the fake model raises for a step whose prompt names it — one ordinary failure, the kind
#: the default `on_error` walks past.
BROKE = "RuntimeError: the source answered 503"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    return home


def _transform(node_id: str) -> dict[str, Any]:
    return {"kind": "transform", "id": node_id, "config": {"expr": {"ran": node_id}}}


def _breaks(node_id: str, **cfg: Any) -> dict[str, Any]:
    """An `infer` step whose model call raises — an ordinary failure with the default `on_error`."""
    return {"kind": "infer", "id": node_id, "config": {"prompt": f"BREAK {node_id}", **cfg}}


def _spec(*children: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "continued",
        "root": {"kind": "sequence", "id": "s", "children": list(children)},
    }


async def _completion(instruction, use_case=None, output_type=None, **_kw):
    text = str(instruction)
    if "BREAK fetch-each" in text and "item-b" not in text:
        # A fan-out's items share one prompt: only the item `item-b` breaks.
        return json.dumps({"ok": True})
    if "BREAK" in text:
        raise RuntimeError("the source answered 503")
    # The judge gate's call: a judge that will not rule either way.
    return json.dumps({"verdict": "ESCALATE", "proof": "read it", "reasoning": "cannot tell"})


async def _until(predicate, *, what: str, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.05)


async def _run(spec: dict[str, Any]) -> tuple[RunController, list[dict[str, Any]]]:
    spec = copy.deepcopy(spec)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], mode="background"))
    store.write_spec(run.id, spec)
    published: list[dict[str, Any]] = []

    def publish(event: str, payload: dict[str, Any]) -> None:
        if event == "workflow_run_update":
            published.append(dict(payload))

    c = RunController(run, spec, services=EngineServices(completion=_completion, publish=publish))
    await c.start()
    await _until(lambda: c.run.is_terminal, what=f"the run to end (it is {c.run.status.value})")
    return c, published


def _ending(published: list[dict[str, Any]]) -> dict[str, Any]:
    """The last `workflow_run_update`'s status and error — the live feed's word on the ending."""
    return {key: published[-1].get(key) for key in ("status", "error")}


def _finished(run_id: str) -> dict[str, Any]:
    (record,) = J.journal_records(run_id, kinds={J.RUN_FINISHED})
    return record


# ── the defect ───────────────────────────────────────────────────────────────


async def test_a_run_that_continued_past_a_failed_step_names_it_and_says_it_went_on() -> None:
    c, published = await _run(_spec(_transform("draft"), _breaks("fetch"), _transform("publish")))

    # The continuation is real — `publish` ran after the failure — so the sentence can say so.
    assert c.instances["root.children[2]"].state == InstanceState.DONE
    assert c.run.status == RunStatus.FAILED
    said = f"The run continued past “fetch”, which failed: {BROKE}."
    assert c.run.error_message == said, "a failed run ended with no sentence"
    # The same sentence everywhere the ending is read: the stored row, the journal, the live feed.
    assert store.get(c.run.id).error_message == said
    assert _finished(c.run.id).get("error") == said
    assert _ending(published) == {"status": "failed", "error": said}


async def test_a_check_gate_that_declares_null_continue_says_so_too() -> None:
    """#3739's own case: the gate carries on past its failure, the run still ends failed, and now
    the ending says which check failed and that the run went on."""
    check = {
        "kind": "gate",
        "id": "check",
        "config": {"kind": "expression", "expr": "1 == 2", "on_error": "null_continue"},
    }
    c, _ = await _run(_spec(check, _transform("publish")))
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == (
        "The run continued past “check”, which failed: gate condition is false: 1 == 2."
    )


# ── what the sentence may and may not claim ──────────────────────────────────


async def test_a_failure_nothing_ran_after_does_not_claim_the_run_went_on() -> None:
    c, _ = await _run(_spec(_transform("draft"), _breaks("fetch")))
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == f"“fetch” failed: {BROKE}."


async def test_a_failed_parallel_leg_is_named_without_claiming_a_continuation() -> None:
    """Its sibling ran BESIDE it, not after it: nothing was continued past."""
    c, _ = await _run(
        _spec(
            {"kind": "parallel", "id": "legs", "children": [_breaks("fetch"), _transform("other")]}
        )
    )
    assert c.instances["root.children[0].children[1]"].state == InstanceState.DONE
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == f"“fetch” failed: {BROKE}."


async def test_every_failed_step_is_named() -> None:
    c, _ = await _run(_spec(_breaks("fetch"), _breaks("parse"), _transform("publish")))
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == (
        f"The run continued past “fetch” and “parse”. “fetch” failed: {BROKE}. “parse” failed too."
    )


async def test_only_the_failures_the_run_went_on_past_are_said_to_be_continued_past() -> None:
    c, _ = await _run(_spec(_breaks("fetch"), _transform("publish"), _breaks("close")))
    assert c.run.error_message == (
        f"The run continued past “fetch”. “fetch” failed: {BROKE}. “close” failed too."
    )


async def test_a_failed_fan_out_item_is_named_by_its_item() -> None:
    fan_out = {
        "kind": "foreach",
        "id": "each",
        "config": {"items": ["item-a", "item-b"], "on_item_error": "collect"},
        "body": {
            "kind": "infer",
            "id": "fetch-each",
            "config": {"prompt": "BREAK fetch-each {{item}}"},
        },
    }
    c, _ = await _run(_spec(fan_out, _transform("publish")))
    assert c.instances["root.children[1]"].state == InstanceState.DONE
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == (
        f"The run continued past “fetch-each” (item-b), which failed: {BROKE}."
    )


async def test_a_judge_that_escalated_and_was_continued_past_says_it_escalated() -> None:
    judge = {
        "kind": "gate",
        "id": "accept",
        "config": {
            "kind": "judge",
            "prompt": "Is the draft right?",
            "evidence": EVIDENCE,
            "on_error": "null_continue",
        },
    }
    c, _ = await _run(_spec(judge, _transform("publish")))
    assert c.run.status == RunStatus.ESCALATED
    assert c.run.error_message == (
        "The run continued past “accept”, which escalated: judge returned ESCALATE."
    )


# ── controls ─────────────────────────────────────────────────────────────────


async def test_a_failure_the_run_tolerated_is_not_a_reason_it_failed() -> None:
    """CONTROL: `allow_failure` records the failure as degraded and the run completes, so there is
    nothing to explain — and a tolerated failure is never named as though it had ended the run."""
    c, _ = await _run(_spec(_breaks("fetch", allow_failure=True), _transform("publish")))
    assert c.run.status == RunStatus.COMPLETE
    assert c.run.error_message == ""


async def test_a_run_that_finished_cleanly_ends_with_no_sentence() -> None:
    c, published = await _run(_spec(_transform("draft"), _transform("publish")))
    assert c.run.status == RunStatus.COMPLETE
    assert c.run.error_message == ""
    assert _ending(published) == {"status": "complete", "error": ""}


# ── the wording, over states a live run reaches rarely ───────────────────────


def _ended(states: dict[str, InstanceState], *, cause: str = "") -> str:
    """`for_failures` over a five-step sequence standing in the given states — the engine's own
    derivation and naming, without engineering a refused redo or a vanished worker."""
    from types import SimpleNamespace

    from personalclaw.workflows import ending_sentence
    from personalclaw.workflows.models import Failure, Node, NodeInstance

    root = Node.from_dict(_spec(*(_transform(n) for n in "abcdef"))["root"])
    instances = {}
    for index, node_id in enumerate("abcdef"):
        path = f"root.children[{index}]"
        inst = NodeInstance(path=path, state=states.get(node_id, InstanceState.DONE))
        if node_id == "a" and cause:
            inst.failure = Failure(cause_plain=cause)
        instances[path] = inst
    ctl = SimpleNamespace(
        root=root,
        instances=instances,
        _declined_edges=set(),
        _outputs={},
        _iterations={},
        run=SimpleNamespace(inputs={}),
    )
    return ending_sentence.for_failures(ctl)


def test_each_way_a_step_did_not_succeed_is_worded_as_itself() -> None:
    said = _ended(
        {
            "a": InstanceState.FAILED,
            "b": InstanceState.BLOCKED,
            "c": InstanceState.BLOCKED,
            "d": InstanceState.ESCALATED,
        },
        cause="the source answered 503.\n",
    )
    assert said == (
        "The run continued past “a”, “b”, “c” and 1 more. “a” failed: the source answered 503. "
        "“d” escalated too. “b” and “c” were blocked too."
    )


def test_a_long_list_of_failures_is_counted_past_the_third() -> None:
    failed = {node_id: InstanceState.FAILED for node_id in "abcdef"}
    assert _ended(failed) == (
        "The run continued past “a”, “b”, “c” and 2 more. “a” failed. “b”, “c”, “d” and 2 more "
        "failed too."
    )
