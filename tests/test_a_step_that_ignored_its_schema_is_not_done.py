"""A step whose answer ignored its declared `schema` is not Done: it fails, saying why.

The defect: a stage/node declares a `schema`, the model returns something else, and the step was
marked Done. Measured on the first-run loop (`general-project` on a hosted model): the work step
read Done although its answer "ignored its declared schema: it asked for evidence,
meaningful_progress, summary and got text, not an object" — the run then judged and bound a step
that had produced none of what its prompt asked for, and the declared keys resolved to their
defaults downstream.

So a step that ignored its schema FAILS, as PROTOCOL — the class `infer` already gives a model whose
JSON would not parse — naming what the schema declared and what came instead, and the retry policy
reads it like any other failure. Its answer is kept for the inspector, never bound.

Two traps this file exists to pin, both measured over the bundled library through the production
loader (78 nodes declare a `schema`: 47 stages, 31 `infer`):

🔴 **A gate must never fire on conforming output.** Before #3531 a stage's output was stored as
`{"result": "<raw text>"}` whatever its `schema`, so a check at the stage settle fired on 47 of 47
stages and measured the missing seam rather than the model. #3531 applies the declared schema at
the settle; a conforming answer now trips 0 of 47, and
`test_every_bundled_stage_is_done_when_it_conforms_and_fails_when_it_does_not` keeps it that way.

🔴 **A gate must read what the WORKER returned, not what the engine filled in.** The judge contract
writes every key a judge schema declares whatever the model said, so a judge that answered in prose
settles carrying all of them (`verdict="REJECT"`, `reasoning=""`, ...). `apply_declared_schema`
therefore compares the node's own output taken before the engine's seams, at both the dispatch seam
and the stage settle. A judge is held to its contract rather than to every key: prose fails, a
verdict that left out a key the contract defaults does not.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from personalclaw.ledger.reader import read_journal
from personalclaw.workflows import journal as J
from personalclaw.workflows import service, stage_settlement, store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import (
    NodeResult,
    apply_declared_schema,
    dispatch,
    schema_shortfall,
)
from personalclaw.workflows.models import (
    InstanceState,
    Node,
    NodeKind,
    RunStatus,
    WorkflowRun,
)

pytestmark = pytest.mark.anyio

#: What the fixture node asks the model for. Two keys, so "1 of 2" and "2 of 2" are distinguishable
#: — a notice that cannot count is a notice an author cannot act on.
DECLARED = {"tier": "string", "why": "string"}

_SPEC: dict[str, Any] = {
    "name": "wf3545",
    "root": {
        "kind": "infer",
        "id": "triage",
        "config": {"prompt": "classify this", "schema": DECLARED},
    },
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _node(kind: NodeKind, config: dict[str, Any]) -> Node:
    return Node(kind=kind, id="n", config=config)


@contextlib.contextmanager
def _isolated(home: Path) -> Iterator[None]:
    """Point the run store at a tmp home and put it back. Nothing here may touch the real home."""
    home.mkdir(parents=True, exist_ok=True)
    original = store.config_dir
    store.config_dir = lambda: home  # type: ignore[assignment]
    try:
        yield
    finally:
        store.config_dir = original  # type: ignore[assignment]


async def _drive(home: Path, payload: str) -> dict[str, Any]:
    """Run the fixture spec to completion against a scripted worker, zero network.

    Returns the run's terminal status, its `step_completed` rows and its REST node rows — the two
    surfaces the notice has to reach, read the way a reader actually reads them.
    """

    async def fake(prompt: str, *, use_case: str = "background", output_type: Any = None) -> str:
        return payload

    with _isolated(home):
        run = store.create(WorkflowRun(id="", workflow_name="wf3545"))
        store.write_spec(run.id, _SPEC)
        controller = RunController(
            run, _SPEC, services=EngineServices(publish=lambda e, p: None, completion=fake)
        )
        status = await controller.run_to_completion(timeout=60)
        journal = read_journal(store, run.id)
        return {
            "status": status,
            "completed": [r for r in journal if r.get("kind") == "step_completed"],
            "failed": [r for r in journal if r.get("kind") == "step_failed"],
            "rows": list(service._nodes_of(run.id)),
        }


# ── the pure comparison ──────────────────────────────────────────────────────


class TestTheComparison:
    def test_a_conforming_object_has_no_shortfall(self) -> None:
        assert schema_shortfall(DECLARED, {"tier": "high", "why": "because"}) == ""

    def test_extra_keys_are_not_a_shortfall(self) -> None:
        """A model that answers the question AND adds context honoured the schema. Failing on the
        extras would fail output an author is happy with."""
        assert schema_shortfall(DECLARED, {"tier": "a", "why": "b", "confidence": 0.9}) == ""

    def test_a_missing_key_names_what_was_asked_for_and_what_arrived(self) -> None:
        """Both facts, because either alone sends an author hunting: a generic 'schema mismatch'
        does not say which key, and naming only the key does not say what the model returned
        instead (the `{{last.output.sumary}}` typo and an omitted `summary` look identical without
        it — that is #3544's named residue)."""
        msg = schema_shortfall(DECLARED, {"answer": "it is fine", "notes": "n"})
        assert "2 of 2" in msg
        assert "tier" in msg and "why" in msg  # what the schema declared
        assert "answer" in msg and "notes" in msg  # what actually arrived

    def test_a_partial_shortfall_counts_only_the_absent_keys(self) -> None:
        msg = schema_shortfall(DECLARED, {"tier": "high"})
        assert msg == (
            "the output ignored its declared schema: "
            "1 of 2 declared keys is missing (why); got tier"
        )
        one = schema_shortfall({"summary": "string"}, {"text": "x"})
        assert "1 of 1 declared key is missing (summary); got text" in one
        assert "got no keys" in schema_shortfall(DECLARED, {})

    def test_a_non_object_names_what_arrived_in_words_not_python_types(self) -> None:
        """The reader is a template author: "got text" says what happened, "got a str" makes them
        translate Python's type name first."""
        msg = schema_shortfall(DECLARED, json.dumps("my analysis, in prose"))
        assert msg == (
            "the output ignored its declared schema: it asked for tier, why and got text, "
            "not an object"
        )
        assert "got a list" in schema_shortfall(DECLARED, [1, 2])
        assert "got a number" in schema_shortfall(DECLARED, 7)

    def test_a_blank_answer_is_named_as_nothing_not_as_text(self) -> None:
        """A stage's subagent can return an empty result; "got text" would describe prose that
        does not exist."""
        assert "and got nothing, not an object" in schema_shortfall(DECLARED, "")
        assert "and got nothing, not an object" in schema_shortfall(DECLARED, "  \n")

    def test_a_json_string_answer_is_named_as_not_an_object(self) -> None:
        """A model that returns a bare JSON string parses fine and can never carry the keys."""
        msg = schema_shortfall(DECLARED, json.dumps("here is my analysis, in prose"))
        assert "not an object" in msg and "tier" in msg and "why" in msg

    def test_a_list_answer_is_named_as_not_an_object(self) -> None:
        assert "not an object" in schema_shortfall(DECLARED, [{"tier": "high", "why": "b"}])

    def test_no_declared_schema_is_never_a_shortfall(self) -> None:
        """The gate the whole change rests on: nothing declared means nothing to ignore."""
        assert schema_shortfall({}, {"anything": 1}) == ""
        assert schema_shortfall(None, {"anything": 1}) == ""
        assert schema_shortfall("not a dict", {"anything": 1}) == ""

    def test_a_pathological_output_does_not_write_a_megabyte_into_a_ledger_row(self) -> None:
        """The sentence is a failure's cause on a journal row, so its size is bounded by
        construction."""
        schema = {f"k{i}": "string" for i in range(50)}
        msg = schema_shortfall(schema, {f"j{i}": i for i in range(400)})
        assert "+42 more" in msg and "+392 more" in msg
        assert len(msg) < 400

    def test_types_are_deliberately_not_compared(self) -> None:
        """KEYS ONLY. A declared `"number"` against a string is a judgement call, and a gate that
        fails output an author considers conforming is worse than no gate at all."""
        assert schema_shortfall({"n": "number", "o": "object"}, {"n": "7", "o": []}) == ""


# ── the seam: a gate ─────────────────────────────────────────────────────────


class TestTheSeamIsAGate:
    def test_a_non_conforming_success_fails_naming_what_was_asked_and_what_came(self) -> None:
        """🔴 Before: annotated, and left Done."""
        result = NodeResult(state=InstanceState.DONE, output={"answer": "x"})
        node = _node(NodeKind.INFER, {"schema": DECLARED})
        out = apply_declared_schema(node, result, result.output)
        assert out.state is InstanceState.FAILED
        assert out.failure is not None and out.failure.failure_class.value == "protocol"
        assert out.failure.cause_plain == (
            "the output ignored its declared schema: 2 of 2 declared keys are missing (tier, why); "
            "got answer"
        )
        assert not out.failure.retryable, "a non-conforming answer is not a transient failure"
        assert out.output == {"answer": "x"}  # kept for the inspector

    def test_it_reads_what_the_work_returned_not_what_the_engine_filled_in(self) -> None:
        """🔴 The judge trap, at unit scale. The final output carries every declared key because
        the engine wrote them (the judge contract does exactly this); the worker said something
        else. Read from `observed`, so the engine's keys cannot pass for the worker's."""
        filled = NodeResult(state=InstanceState.DONE, output={"tier": "", "why": ""})
        node = _node(NodeKind.STAGE, {"schema": DECLARED})
        # The premise: read off the final output, the check would call this conforming.
        assert schema_shortfall(DECLARED, filled.output) == ""
        out = apply_declared_schema(node, filled, "Looks fine to me.")
        assert out.state is InstanceState.FAILED
        assert out.failure is not None
        assert "and got text, not an object" in out.failure.cause_plain

    def test_a_judge_in_prose_fails_but_a_verdict_missing_a_defaulted_key_does_not(self) -> None:
        """A judge is held to its contract. Prose is no verdict at all; a verdict object that left
        out a key the contract defaults (an empty `cannot_judge`) is a verdict, already read."""
        schema = {"reasoning": "string", "verdict": "string", "cannot_judge": "string"}
        node = _node(NodeKind.STAGE, {"schema": schema, "judge_contract": True})
        prose = apply_declared_schema(
            node, NodeResult(state=InstanceState.DONE, output={}), "Looks good, I would pass it."
        )
        assert prose.state is InstanceState.FAILED
        verdict = {"reasoning": "re-ran it", "verdict": "PASS"}
        kept = apply_declared_schema(
            node, NodeResult(state=InstanceState.DONE, output=dict(verdict)), dict(verdict)
        )
        assert kept.state is InstanceState.DONE and kept.failure is None

    def test_a_failed_step_is_left_as_it_failed(self) -> None:
        """A FAILED step already carries a `Failure` saying why — `infer`'s own
        `model output was not valid JSON` is exactly that case."""
        result = NodeResult(state=InstanceState.FAILED, output={"answer": "x"})
        node = _node(NodeKind.INFER, {"schema": DECLARED})
        assert apply_declared_schema(node, result, result.output).failure is None

    def test_a_degraded_step_that_produced_nothing_is_not_gated(self) -> None:
        """`dispatch_stage`'s two DEGRADED paths (a restricted-origin skip, a claim already held)
        return `output=None`. Why they produced nothing is already in `degraded_reason`, and
        re-reading that as "the schema was ignored" would be false."""
        result = NodeResult(
            state=InstanceState.DEGRADED, output=None, degraded_reason="another worker holds it"
        )
        out = apply_declared_schema(_node(NodeKind.STAGE, {"schema": DECLARED}), result, None)
        assert out.state is InstanceState.DEGRADED
        assert out.degraded_reason == "another worker holds it"

    def test_a_node_declaring_no_schema_is_never_gated(self) -> None:
        result = NodeResult(state=InstanceState.DONE, output={"anything": 1})
        assert apply_declared_schema(_node(NodeKind.INFER, {}), result, "prose").state is (
            InstanceState.DONE
        )

    def test_a_spawned_stage_is_not_gated_at_the_spawn(self) -> None:
        """`dispatch_stage` returns RUNNING at the spawn with `{"subagent_id": …}`, and RUNNING is
        not a success state, so the dispatch seam gates nothing. The stage is gated at its settle,
        through this same helper (`TestAStageIsGatedAtItsSettle`). The placeholder lacks every
        declared key, so the comparison WOULD fire on it; the state gate is what stops it."""
        spawned = NodeResult(state=InstanceState.RUNNING, output={"subagent_id": "sub-1"})
        node = _node(NodeKind.STAGE, {"schema": DECLARED})
        assert schema_shortfall(DECLARED, spawned.output) != ""
        assert apply_declared_schema(node, spawned, spawned.output).state is InstanceState.RUNNING


# ── the dispatcher arm ───────────────────────────────────────────────────────


def _completion(payload: str) -> Any:
    async def fake(prompt: str, *, use_case: str = "background", output_type: Any = None) -> str:
        return payload

    return fake


#: `general-project`'s judge schema, read through the production loader rather than restated, so a
#: change to the shipped judge moves these tests with it.
def _judge_schema() -> dict[str, Any]:
    loaded = read_template("general-project")
    assert loaded is not None and loaded.root.body is not None
    judge = [c for c in loaded.root.body.children if c.id == "judge"]
    assert judge, "general-project no longer has a judge stage"
    return dict(judge[0].config["schema"])


class TestInferThroughTheDispatcher:
    """Through `engine.dispatch`, the real seam, so the capture of `observed` is under test."""

    async def test_a_non_conforming_infer_output_fails(self) -> None:
        node = _node(NodeKind.INFER, {"prompt": "p", "schema": DECLARED})
        out = await dispatch(
            node, BindingContext(), completion=_completion(json.dumps({"answer": "fine"}))
        )
        assert out.state is InstanceState.FAILED
        assert out.failure is not None
        assert "tier" in out.failure.cause_plain and "answer" in out.failure.cause_plain

    async def test_a_conforming_infer_output_is_done(self) -> None:
        good = {"tier": "high", "why": "a real reason"}
        node = _node(NodeKind.INFER, {"prompt": "p", "schema": DECLARED})
        out = await dispatch(node, BindingContext(), completion=_completion(json.dumps(good)))
        assert out.output == good  # the premise, not assumed
        assert out.state is InstanceState.DONE and out.failure is None

    async def test_an_infer_judge_answer_is_read_by_its_contract(self) -> None:
        """An object answer missing the judge's keys is a verdict the contract reads (an unknown
        verdict, so a protocol REJECT), not a step the schema gate fails on top of it."""
        schema = _judge_schema()
        node = _node(
            NodeKind.INFER, {"prompt": "judge it", "schema": schema, "judge_contract": True}
        )
        out = await dispatch(
            node, BindingContext(), completion=_completion(json.dumps({"answer": "fine"}))
        )
        # The premise: the contract really did fill in every declared key.
        assert set(schema) <= set(out.output), sorted(out.output)
        assert out.state is InstanceState.DONE
        assert out.output["contract_valid"] is False


# ── both arms, driven through a real run ─────────────────────────────────────


class TestBothArmsOfARealRun:
    async def test_the_non_conforming_arm_fails_on_the_journal_row_and_the_rest_row(
        self, tmp_path: Path
    ) -> None:
        got = await _drive(tmp_path / "bad", json.dumps({"answer": "it is fine"}))
        assert got["completed"] == [], "a step that ignored its schema was recorded as done"
        assert len(got["failed"]) == 1, got
        cause = str(got["failed"][0]["failure"]["cause_plain"])
        assert "tier" in cause and "why" in cause and "answer" in cause
        assert got["failed"][0]["failure"]["class"] == "protocol"
        # …and the surface a user actually looks at.
        rest = [r for r in got["rows"] if r["node_id"] == "triage"]
        assert len(rest) == 1
        assert rest[0]["state"] == "failed"
        assert rest[0]["failure"]["cause_plain"] == cause
        assert got["status"] is RunStatus.FAILED

    async def test_the_conforming_arm_is_done(self, tmp_path: Path) -> None:
        """THE arm that decides whether the gate is worth having: it never fails conforming
        output."""
        good = json.dumps({"tier": "high", "why": "a real reason"})
        got = await _drive(tmp_path / "good", good)
        assert len(got["completed"]) == 1, got  # the premise: it ran
        assert got["completed"][0]["state"] == "done"
        assert got["failed"] == []
        assert got["status"] is RunStatus.COMPLETE

    async def test_bare_prose_is_still_the_legible_protocol_failure_it_already_was(
        self, tmp_path: Path
    ) -> None:
        """Text that does not parse at all already fails with PROTOCOL / `model output was not
        valid JSON`, and still does."""
        got = await _drive(tmp_path / "prose", "I had a look and I think this is high tier.")
        assert got["completed"] == []
        assert len(got["failed"]) == 1
        assert got["failed"][0]["failure"]["class"] == "protocol"
        assert got["status"] is RunStatus.FAILED


# ── a real stage, settled out of band ────────────────────────────────────────


#: What the fixture stage asks its worker for — `general-project`'s own work-stage vocabulary.
STAGE_DECLARED = {"summary": "string", "meaningful_progress": "boolean"}


async def _drive_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, config: dict[str, Any]
) -> dict[str, Any]:
    """One stage through the REAL `SubagentManager` over the shipped `ScriptedProvider`.

    A stage's output is produced out of band, at `stage_settlement.reconcile_dispatched_stages`, so
    this is the only kind of drive that reaches the settle the stage gate lives on. Returns what
    that settle wrote to each surface: the instance, the journal row (`step_completed` or
    `step_failed`), the REST row and the live event.
    """
    from personalclaw.llm.registry import SCRIPTED_PROVIDER_ENV
    from personalclaw.llm.scripted import ScriptedProvider
    from personalclaw.subagent import SubagentManager
    from tests.test_workflows_stage_usage_end_to_end import _ctx_builder, _sessions_over

    usage = {"input_tokens": 11, "output_tokens": 7}
    usage.update(cache_creation_tokens=0, cache_read_tokens=0)
    turn = {"text": text, "stop_reason": "end_turn", "usage": usage}
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"version": 1, "on_exhausted": "repeat_last", "turns": [turn]}))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv(SCRIPTED_PROVIDER_ENV, str(script))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    stage = {"kind": "stage", "id": "work", "config": {"prompt": "do the thing", **config}}
    spec: dict[str, Any] = {
        "name": "wf3545-stage",
        "root": {"kind": "sequence", "id": "root", "children": [stage]},
    }
    path = "root.children[0]"
    published: list[tuple[str, dict[str, Any]]] = []
    with _isolated(tmp_path):
        manager = SubagentManager(
            sessions=_sessions_over(ScriptedProvider()),
            ctx_builder=_ctx_builder(),
            is_yolo=lambda: True,
        )
        run = store.create(WorkflowRun(id="", workflow_name="wf3545-stage"))
        store.write_spec(run.id, spec)
        controller = RunController(
            run,
            spec,
            services=EngineServices(
                subagents=manager,
                cwd="",
                publish=lambda event, payload: published.append((event, dict(payload))),
            ),
        )
        status = await controller.run_to_completion(timeout=25.0)

        # The premises, so a green cannot be a stage that never spawned or never settled.
        inst = controller.instances[path]
        assert inst.subagent_id, "no subagent was spawned — the settle was never reached"
        child = manager.get(inst.subagent_id)
        assert child is not None and child.done and not child.error, child
        rows = [
            r
            for r in read_journal(store, run.id)
            if r.get("kind") in ("step_completed", "step_failed") and r.get("instance_path") == path
        ]
        assert len(rows) == 1, rows
        rest = [r for r in service._nodes_of(run.id) if r["instance_path"] == path]
        events = [
            p
            for event, p in published
            if event == "workflow_node_done" and p.get("instance_path") == path
        ]
        assert rest and events, (rest, events)
        return {
            "status": status,
            "inst": inst,
            "row": rows[0],
            "rest": rest[0],
            "event": events[-1],
            "stored": store.read_output(run.id, path),
        }


class TestAStageIsGatedAtItsSettle:
    async def test_a_stage_that_answered_in_prose_fails_on_every_surface(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The run's own case: the model returns prose. 🔴 Before: Done, with a notice beside it."""
        prose = "I looked into it and everything seems fine to me."
        got = await _drive_stage(tmp_path, monkeypatch, prose, {"schema": STAGE_DECLARED})
        want = (
            "the output ignored its declared schema: it asked for meaningful_progress, summary "
            "and got text, not an object"
        )
        assert got["inst"].state is InstanceState.FAILED
        assert got["inst"].failure is not None and got["inst"].failure.cause_plain == want
        assert got["row"]["kind"] == "step_failed"
        assert got["row"]["failure"]["cause_plain"] == want
        assert got["rest"]["state"] == "failed"
        assert got["event"]["status"] == "failed"
        # What came back is kept for the inspector.
        assert got["stored"] == {"result": prose}
        assert got["status"] is RunStatus.FAILED

    async def test_a_stage_whose_json_lacks_a_declared_key_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answer = json.dumps({"summary": "did it"})
        got = await _drive_stage(tmp_path, monkeypatch, answer, {"schema": STAGE_DECLARED})
        assert got["row"]["failure"]["cause_plain"] == (
            "the output ignored its declared schema: "
            "1 of 2 declared keys is missing (meaningful_progress); got summary"
        )
        assert got["status"] is RunStatus.FAILED

    async def test_a_conforming_stage_is_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE arm that decides whether the gate is worth having."""
        good = {"summary": "did it", "meaningful_progress": True}
        got = await _drive_stage(
            tmp_path, monkeypatch, json.dumps(good), {"schema": STAGE_DECLARED}
        )
        assert got["stored"] == good  # the premise: #3531 stored the declared shape
        assert got["inst"].state is InstanceState.DONE
        assert got["row"]["kind"] == "step_completed"
        assert got["status"] is RunStatus.COMPLETE

    async def test_a_judge_that_answered_in_prose_fails_though_the_contract_filled_every_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 The judge trap, on the real settle. The contract writes all six declared keys onto a
        prose answer, so a check on the stored output reads it as conforming."""
        schema = _judge_schema()
        config = {"schema": schema, "judge_contract": True}
        got = await _drive_stage(tmp_path, monkeypatch, "Looks good to me, I'd pass it.", config)
        # The premise, measured in this run: every declared key IS on the stored output.
        assert set(schema) <= set(got["stored"]), sorted(got["stored"])
        assert schema_shortfall(schema, got["stored"]) == ""
        cause = got["row"]["failure"]["cause_plain"]
        assert cause.endswith("and got text, not an object"), cause
        assert all(key in cause for key in schema), cause
        assert got["status"] is RunStatus.FAILED

    async def test_a_stage_declaring_no_schema_is_never_gated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        got = await _drive_stage(tmp_path, monkeypatch, "free-form notes", {})
        assert got["stored"] == {"result": "free-form notes"}
        assert got["row"]["kind"] == "step_completed"
        assert got["status"] is RunStatus.COMPLETE


# ── the issue's own run: `general-project`, six rounds of a worker that ignores its schema ──


class _Info:
    """The subset of `SubagentInfo` a stage settle reads."""

    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _Workers:
    """Stands in for `SubagentManager` on `spawn` + `get`, exactly as `dispatch_stage` calls them.

    `prose=True` is the issue's worker: both stages answer in prose every round. Otherwise each
    stage returns the JSON its schema asks for, told apart by the judge's prompt the way
    `test_general_kind_as_run` does.
    """

    def __init__(self, *, prose: bool) -> None:
        self.prose = prose
        self.infos: dict[str, _Info] = {}
        self.prompts: list[str] = []

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        self.prompts.append(prompt)
        judge = "You are verifying work you did not do" in prompt
        if self.prose:
            text = "Looks good to me." if judge else "I made some progress on the task."
        elif judge:
            text = json.dumps(
                {
                    "reasoning": "re-ran the cited command; the line is there",
                    "verdict": "PASS",
                    "scores": {"the step accomplished something real": 2},
                    "evidence_refs": ["README.md"],
                    "proof": "cat README.md printed the added line",
                    "cannot_judge": "",
                }
            )
        else:
            work = {"summary": "wrote the line", "meaningful_progress": False, "evidence": "README"}
            text = json.dumps(work)
        info = _Info(f"sub{len(self.prompts)}", text)
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


#: Fields that differ between any two runs of one spec (ids, clocks) — never what a run DID.
#: `idempotency_key` is `effects.idempotency_key(run_id, …)` hashed, so replacing the run id in the
#: text cannot normalize it; `origin_harness` is a per-run id.
_VOLATILE = {
    "ts",
    "event_id",
    "run_id",
    "duration_secs",
    "elapsed_secs",
    "idempotency_key",
    "origin_harness",
}


async def _drive_general_project(
    home: Path, monkeypatch: pytest.MonkeyPatch, *, prose: bool
) -> dict[str, Any]:
    """The REAL bundled `general-project` through a REAL `RunController`, zero network."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    loaded = read_template("general-project")
    assert loaded is not None
    spec = loaded.to_dict()
    inputs = {"task": "add a line to README.md", "exit_condition": "README.md contains the line"}
    run = store.create(WorkflowRun(id="", workflow_name="general-project", inputs=inputs))
    store.write_spec(run.id, spec)
    workers = _Workers(prose=prose)
    controller = RunController(run, spec, services=EngineServices(subagents=workers))
    status = await controller.run_to_completion(timeout=60)

    def scrub(value: Any) -> Any:
        return json.loads(json.dumps(value, default=str).replace(run.id, "RUN"))

    records = [
        scrub({k: v for k, v in r.items() if k not in _VOLATILE}) for r in J.journal_records(run.id)
    ]
    return {
        "status": status,
        "records": records,
        "completed": [r for r in records if r.get("kind") == "step_completed"],
        "outputs": {p: scrub(store.read_output(run.id, p)) for p in sorted(controller.instances)},
        "prompts": scrub(workers.prompts),
    }


class TestTheRunsOwnLoop:
    async def test_a_prose_worker_is_not_done_and_the_loop_says_which_step_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 Before: every step Done, six rounds, and the loop escalated on its ceiling. Now the
        work step fails on the first round, and the loop's ending names it and why."""
        got = await _drive_general_project(tmp_path / "prose", monkeypatch, prose=True)
        failed = [r for r in got["records"] if r.get("kind") == "step_failed"]
        work = [r for r in failed if r["node_id"] == "work"]
        assert work, [r.get("kind") for r in got["records"]]
        assert {r["failure"]["cause_plain"] for r in work} == {
            "the output ignored its declared schema: it asked for evidence, meaningful_progress, "
            "summary and got text, not an object"
        }
        assert not [r for r in got["completed"] if r["node_id"] == "work"], "a prose step was Done"
        # And the loop did not read its failed cycles as dry and finish: two of them used to end
        # it on its own exit, Completed over four failed steps.
        assert got["status"] is RunStatus.ESCALATED, got["status"]
        escalated = [r for r in got["records"] if r.get("kind") == "step_escalated"]
        assert escalated, "the loop did not hand the run over"
        assert escalated[-1]["reason"] == "iterations_failed", escalated[-1]
        assert escalated[-1]["cause"] == "step", escalated[-1]
        assert "ignored its declared schema" in escalated[-1]["detail"], escalated[-1]

    async def test_a_conforming_worker_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        got = await _drive_general_project(tmp_path / "good", monkeypatch, prose=False)
        done = got["completed"]
        assert {"work", "judge"} <= {r["node_id"] for r in done}  # the premise: both ran
        assert not [r for r in got["records"] if r.get("kind") == "step_failed"]
        assert got["status"] is RunStatus.COMPLETE


# ── the census the gate rests on ─────────────────────────────────────────────


def _schema_nodes() -> list[tuple[str, str, str]]:
    """Every bundled node declaring a `schema`, through the PRODUCTION loader.

    `read_template`, never a raw `json.load`: macros are expanded on read, so a raw load reads a
    spec production never sees — a `judge_panel` or `research_sweep` template would contribute
    none of its expanded nodes to the count.
    """
    rows: list[tuple[str, str, str]] = []

    def walk(n: Any, tmpl: str) -> None:
        if n is None:
            return
        if "schema" in (getattr(n, "config", None) or {}):
            rows.append((tmpl, getattr(n, "id", "") or "", n.kind.value))
        for child in getattr(n, "children", None) or []:
            walk(child, tmpl)
        walk(getattr(n, "body", None), tmpl)
        for case in (getattr(n, "cases", None) or {}).values():
            walk(case, tmpl)
        walk(getattr(n, "default_case", None), tmpl)

    for name in template_names():
        loaded = read_template(name)
        if loaded is not None:
            walk(loaded.root, name)
    return rows


def test_the_bundled_library_still_declares_stage_schemas() -> None:
    """🔴 The non-vacuity control for the census below.

    `test_every_bundled_stage_is_silent_when_it_conforms_and_named_when_it_does_not` is only
    meaningful while schema-declaring stages EXIST. If the library stopped shipping them it would
    pass against nothing. Asserted as a floor rather than an exact count so adding a template does
    not red this, while emptying the category does.
    """
    rows = _schema_nodes()
    assert len(rows) >= 40, f"the census collapsed: {len(rows)} schema-declaring nodes"
    stages = [r for r in rows if r[2] == "stage"]
    infers = [r for r in rows if r[2] == "infer"]
    assert len(stages) >= 40, f"no stage declares a schema any more: {stages}"
    assert len(infers) >= 20, f"no infer node declares a schema any more: {infers}"


def test_every_bundled_schema_is_an_object_of_declared_keys() -> None:
    """The shape the comparison assumes, asserted against what actually ships. A schema authored
    as a JSON-Schema document (`{"type": "object", "properties": {...}}`) would make the key
    comparison compare the wrong names, and nothing else would notice."""
    for tmpl, nid, _kind in _schema_nodes():
        loaded = read_template(tmpl)
        assert loaded is not None

        found: list[Any] = []

        def walk(n: Any, want: str = nid) -> None:
            if n is None:
                return
            if getattr(n, "id", "") == want and "schema" in (getattr(n, "config", None) or {}):
                found.append(n.config["schema"])
            for child in getattr(n, "children", None) or []:
                walk(child)
            walk(getattr(n, "body", None))
            for case in (getattr(n, "cases", None) or {}).values():
                walk(case)
            walk(getattr(n, "default_case", None))

        walk(loaded.root)
        assert found, f"{tmpl}:{nid} vanished from the loader"
        for schema in found:
            assert isinstance(schema, dict) and schema, f"{tmpl}:{nid} schema is not an object"
            assert "properties" not in schema, (
                f"{tmpl}:{nid} looks like a JSON-Schema document, not a key map — the key "
                f"comparison in `schema_shortfall` would compare the wrong names"
            )


def _conforming_answer(schema: dict[str, Any]) -> dict[str, Any]:
    """A value of the declared type for every declared key: what a worker honouring the schema
    returns. `verdict` is a real verdict so the judge contract has something valid to validate."""

    def value(declared: Any) -> Any:
        kind = str(declared)
        if kind == "array" or kind.startswith("["):
            return []
        return {"boolean": False, "object": {}, "number": 1.0, "integer": 1}.get(kind, "x")

    answer = {key: value(declared) for key, declared in schema.items()}
    if "verdict" in answer:
        answer["verdict"] = "PASS"
    return answer


def test_every_bundled_stage_is_done_when_it_conforms_and_fails_when_it_does_not(
    tmp_path: Path,
) -> None:
    """🔴 The census the stage half rests on, through the PRODUCTION settle.

    Before #3531 a stage's output was `{"result": text}` whatever its schema, and a check at the
    settle fired on every schema-declaring stage, conforming or not. Each bundled stage node, under
    its own template's `runtime_hints`, is settled twice: with a conforming answer (Done, every one)
    and with prose (failed, every one, the judge stages included although the contract fills in
    their keys).
    """
    stages: list[tuple[str, Node]] = []

    def walk(n: Any, tmpl: str) -> None:
        if n is None:
            return
        if n.kind is NodeKind.STAGE and isinstance((n.config or {}).get("schema"), dict):
            stages.append((tmpl, n))
        for child in n.children or []:
            walk(child, tmpl)
        walk(n.body, tmpl)
        for case in (n.cases or {}).values():
            walk(case, tmpl)
        walk(n.default_case, tmpl)

    specs: dict[str, dict[str, Any]] = {}
    for name in template_names():
        loaded = read_template(name)
        if loaded is not None:
            specs[name] = loaded.to_dict()
            walk(loaded.root, name)
    judges = [n for _, n in stages if n.config.get("judge_contract")]
    assert len(stages) >= 40 and len(judges) >= 5, (len(stages), len(judges))

    false_alarms: list[str] = []
    passed: list[str] = []
    controllers: dict[str, RunController] = {}
    with _isolated(tmp_path):
        for name, node in stages:
            if name not in controllers:
                run = store.create(WorkflowRun(id="", workflow_name=name))
                controllers[name] = RunController(run, specs[name])
            controller, schema = controllers[name], node.config["schema"]
            good = stage_settlement._settled_stage_output(
                controller, node, json.dumps(_conforming_answer(schema))
            )
            if good.state is not InstanceState.DONE:
                false_alarms.append(f"{name}:{node.id}: {good.failure}")
            prose = stage_settlement._settled_stage_output(
                controller, node, "I did the work and it went well."
            )
            if prose.state is not InstanceState.FAILED:
                passed.append(f"{name}:{node.id}")
    assert false_alarms == [], false_alarms
    assert passed == [], passed
