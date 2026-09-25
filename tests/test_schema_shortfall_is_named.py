"""#3545 — a step whose output ignored its declared `schema` says so, and nothing else changes.

The defect: a stage/node declares a `schema`, the model returns something else, and NOTHING
anywhere says so. The first symptom arrives two or three steps later as a *binding* error about a
field in a different node's prompt — and since #3544 softened the reading side to fall back to its
declared `default`, not even that. Measured on `general-project` with a worker returning prose, the
run shows 12 of 12 steps Done and escalates on its iteration ceiling with no hint that the worker
never honoured its schema on any of the six rounds.

The fix is an OBSERVATION and nothing more: the producing step is the only place that knows both
what the schema declared and what came back, so it names it on its own journal row and status
detail. It does not fail the step (that would break runs which complete today) and it does not
retry it (that would spend more money on the same non-conforming worker).

Two traps this file exists to pin, both measured over the bundled library through the production
loader (78 nodes declare a `schema`: 47 stages, 31 `infer`):

🔴 **A check must never fire on conforming output.** Before #3531 a stage's output was stored as
`{"result": "<raw text>"}` whatever its `schema`, so a check at the stage settle fired on 47 of 47
stages and measured the missing seam rather than the model. #3531 applies the declared schema at
the settle; a conforming answer now fires on 0 of 47, and
`test_every_bundled_stage_is_silent_when_it_conforms_and_named_when_it_does_not` keeps it that way.

🔴 **A check must read what the WORKER returned, not what the engine filled in.** The judge contract
writes every key a judge schema declares whatever the model said, so a judge that answered in prose
settles carrying all of them (`verdict="REJECT"`, `reasoning=""`, ...). A check on the settled
output was silent on all 7 bundled judge stages. `apply_schema_notice` therefore compares the
node's own output taken before the engine's seams, at both the dispatch seam and the stage settle.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from personalclaw.ledger.reader import read_journal
from personalclaw.workflows import journal as J
from personalclaw.workflows import service, store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import (
    NodeResult,
    apply_schema_notice,
    dispatch,
    release_execution_claim,
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
        """A model that answers the question AND adds context honoured the schema. Reporting the
        extras would make the notice fire on output an author is happy with."""
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
        """The notice reaches a journal row, so its size is bounded by construction."""
        schema = {f"k{i}": "string" for i in range(50)}
        msg = schema_shortfall(schema, {f"j{i}": i for i in range(400)})
        assert "+42 more" in msg and "+392 more" in msg
        assert len(msg) < 400

    def test_types_are_deliberately_not_compared(self) -> None:
        """KEYS ONLY. A declared `"number"` against a string is a judgement call, and a notice
        that fires on output an author considers conforming is worse than no notice at all."""
        assert schema_shortfall({"n": "number", "o": "object"}, {"n": "7", "o": []}) == ""


# ── the seam: an observation, never a verdict ────────────────────────────────


class TestTheSeamOnlyObserves:
    def test_a_non_conforming_success_is_annotated_without_changing_state(self) -> None:
        result = NodeResult(state=InstanceState.DONE, output={"answer": "x"})
        node = _node(NodeKind.INFER, {"schema": DECLARED})
        out = apply_schema_notice(node, result, result.output)
        assert out.schema_shortfall  # named
        assert out.state is InstanceState.DONE  # and nothing else moved
        assert out.failure is None
        assert out.degraded_reason == ""
        assert out.output == {"answer": "x"}

    def test_it_reads_what_the_work_returned_not_what_the_engine_filled_in(self) -> None:
        """🔴 The judge trap, at unit scale. The final output carries every declared key because
        the engine wrote them (the judge contract does exactly this); the worker said something
        else. Named from `observed`, so the engine's keys cannot pass for the worker's."""
        filled = NodeResult(state=InstanceState.DONE, output={"tier": "", "why": ""})
        node = _node(NodeKind.STAGE, {"schema": DECLARED})
        # The premise: read off the final output, the check would call this conforming.
        assert schema_shortfall(DECLARED, filled.output) == ""
        out = apply_schema_notice(node, filled, "Looks fine to me.")
        assert "and got text, not an object" in out.schema_shortfall
        assert out.output == {"tier": "", "why": ""}  # observed, never rewritten

    def test_a_failed_step_is_not_annotated(self) -> None:
        """A FAILED step already carries a `Failure` saying why — `infer`'s own
        `model output was not valid JSON` is exactly that case, and the issue calls it legible.
        A notice there would restate a failure in a second vocabulary."""
        result = NodeResult(state=InstanceState.FAILED, output={"answer": "x"})
        node = _node(NodeKind.INFER, {"schema": DECLARED})
        assert apply_schema_notice(node, result, result.output).schema_shortfall == ""

    def test_a_degraded_step_that_produced_nothing_is_not_annotated(self) -> None:
        """`dispatch_stage`'s two DEGRADED paths (a restricted-origin skip, a claim already held)
        return `output=None`. Why they produced nothing is already in `degraded_reason`, and
        re-reading that as "the schema was ignored" would be false."""
        result = NodeResult(
            state=InstanceState.DEGRADED, output=None, degraded_reason="another worker holds it"
        )
        out = apply_schema_notice(_node(NodeKind.STAGE, {"schema": DECLARED}), result, None)
        assert out.schema_shortfall == ""
        assert out.degraded_reason == "another worker holds it"

    def test_a_node_declaring_no_schema_is_never_annotated(self) -> None:
        result = NodeResult(state=InstanceState.DONE, output={"anything": 1})
        assert (
            apply_schema_notice(_node(NodeKind.INFER, {}), result, "prose").schema_shortfall == ""
        )

    def test_a_spawned_stage_is_not_annotated_at_the_spawn(self) -> None:
        """`dispatch_stage` returns RUNNING at the spawn with `{"subagent_id": …}`, and RUNNING is
        not a success state, so the dispatch seam names nothing. The stage is named at its settle,
        through this same helper (`TestAStageIsNamedAtItsSettle`). The placeholder lacks every
        declared key, so the comparison WOULD fire on it; the state gate is what stops it."""
        spawned = NodeResult(state=InstanceState.RUNNING, output={"subagent_id": "sub-1"})
        node = _node(NodeKind.STAGE, {"schema": DECLARED})
        assert schema_shortfall(DECLARED, spawned.output) != ""
        assert apply_schema_notice(node, spawned, spawned.output).schema_shortfall == ""


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

    async def test_a_non_conforming_infer_output_is_named_and_still_done(self) -> None:
        node = _node(NodeKind.INFER, {"prompt": "p", "schema": DECLARED})
        out = await dispatch(
            node, BindingContext(), completion=_completion(json.dumps({"answer": "fine"}))
        )
        assert out.state is InstanceState.DONE
        assert "tier" in out.schema_shortfall and "answer" in out.schema_shortfall

    async def test_a_conforming_infer_output_is_silent(self) -> None:
        good = {"tier": "high", "why": "a real reason"}
        node = _node(NodeKind.INFER, {"prompt": "p", "schema": DECLARED})
        out = await dispatch(node, BindingContext(), completion=_completion(json.dumps(good)))
        assert out.output == good  # the premise, not assumed
        assert out.schema_shortfall == ""

    async def test_an_infer_judge_that_ignored_its_schema_is_named_though_the_contract_filled_it(
        self,
    ) -> None:
        """🔴 The judge trap at the dispatch seam. #3578 read the output AFTER the judge contract,
        which writes every key a judge schema declares, so this was silent. No bundled `infer`
        node declares `judge_contract`, so the library never showed it; a user's judge would."""
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
        assert f"{len(schema)} of {len(schema)} declared keys are missing" in out.schema_shortfall
        assert "got answer" in out.schema_shortfall


# ── both arms, driven through a real run ─────────────────────────────────────


class TestBothArmsOfARealRun:
    async def test_the_non_conforming_arm_reaches_the_journal_row_and_the_rest_row(
        self, tmp_path: Path
    ) -> None:
        got = await _drive(tmp_path / "bad", json.dumps({"answer": "it is fine"}))
        # Non-vacuity first: the node really ran, so "the notice is present" is about this step
        # rather than about a run that never reached it.
        assert len(got["completed"]) == 1, got
        assert got["failed"] == []
        row = got["completed"][0]
        assert row["state"] == "done"
        notice = str(row["schema_shortfall"])
        assert "tier" in notice and "why" in notice and "answer" in notice
        # …and the surface a user actually looks at.
        rest = [r for r in got["rows"] if r["node_id"] == "triage"]
        assert len(rest) == 1
        assert rest[0]["schema_shortfall"] == notice

    async def test_the_conforming_arm_says_nothing_on_either_surface(self, tmp_path: Path) -> None:
        """THE arm that decides whether the check is worth having. A notice that fires on
        conforming output is a false-alarm generator and worse than nothing."""
        good = json.dumps({"tier": "high", "why": "a real reason"})
        got = await _drive(tmp_path / "good", good)
        assert len(got["completed"]) == 1, got  # the premise: it ran
        assert got["completed"][0]["state"] == "done"
        # ABSENT, not "", on BOTH surfaces. An always-written empty value would read as "checked,
        # and conformed" — true here, and false on every row that declares no schema.
        assert "schema_shortfall" not in got["completed"][0]
        rest = [r for r in got["rows"] if r["node_id"] == "triage"]
        assert "schema_shortfall" not in rest[0]

    async def test_neither_arm_changes_the_runs_terminal_status(self, tmp_path: Path) -> None:
        """The scope ceiling, asserted rather than assumed. The notice may not fail a step, so the
        two runs must end identically — only the notice differs."""
        bad = await _drive(tmp_path / "t_bad", json.dumps({"answer": "x"}))
        good = await _drive(tmp_path / "t_good", json.dumps({"tier": "a", "why": "b"}))
        assert bad["status"] is RunStatus.COMPLETE
        assert good["status"] is RunStatus.COMPLETE
        assert bad["completed"][0]["state"] == good["completed"][0]["state"] == "done"
        assert bad["failed"] == good["failed"] == []

    async def test_bare_prose_is_still_the_legible_protocol_failure_it_already_was(
        self, tmp_path: Path
    ) -> None:
        """The issue's own boundary: text that does not parse at all already fails with
        PROTOCOL / `model output was not valid JSON`. This change must not reclassify it into a
        softer notice — that would turn a legible failure into a run that completes."""
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

    A stage's output is produced out of band, at `_reconcile_dispatched_stages`, so this is the only
    kind of drive that reaches the settle the stage notice lives on. Returns what that settle wrote
    to each surface: the instance, the journal row, the REST row and the live event.
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
            if r.get("kind") == "step_completed" and r.get("instance_path") == path
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


class TestAStageIsNamedAtItsSettle:
    async def test_a_stage_that_answered_in_prose_is_named_on_every_surface(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The issue's opening case: the model returns prose and the output keeps the
        `{"result": …}` envelope. Named as prose, not as a `result` key the worker never wrote."""
        prose = "I looked into it and everything seems fine to me."
        got = await _drive_stage(tmp_path, monkeypatch, prose, {"schema": STAGE_DECLARED})
        want = (
            "the output ignored its declared schema: it asked for meaningful_progress, summary "
            "and got text, not an object"
        )
        assert got["inst"].schema_shortfall == want
        assert got["row"]["schema_shortfall"] == want
        assert got["rest"]["schema_shortfall"] == want
        assert got["event"]["schema_shortfall"] == want
        # And nothing else moved: the settle stored what it stores without the notice.
        assert got["stored"] == {"result": prose}
        assert got["row"]["state"] == "done"
        assert got["status"] is RunStatus.COMPLETE

    async def test_a_stage_whose_json_lacks_a_declared_key_is_named(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answer = json.dumps({"summary": "did it"})
        got = await _drive_stage(tmp_path, monkeypatch, answer, {"schema": STAGE_DECLARED})
        assert got["row"]["schema_shortfall"] == (
            "the output ignored its declared schema: "
            "1 of 2 declared keys is missing (meaningful_progress); got summary"
        )
        assert got["stored"] == {"summary": "did it"}
        assert got["status"] is RunStatus.COMPLETE

    async def test_a_conforming_stage_says_nothing_on_any_surface(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE arm that decides whether the check is worth having."""
        good = {"summary": "did it", "meaningful_progress": True}
        got = await _drive_stage(
            tmp_path, monkeypatch, json.dumps(good), {"schema": STAGE_DECLARED}
        )
        assert got["stored"] == good  # the premise: #3531 stored the declared shape
        assert got["inst"].schema_shortfall == ""
        for surface in ("row", "rest", "event"):
            assert "schema_shortfall" not in got[surface], (surface, got[surface])
        assert got["status"] is RunStatus.COMPLETE

    async def test_a_judge_that_answered_in_prose_is_named_though_the_contract_filled_every_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 The judge trap, on the real settle. The contract writes all six declared keys onto a
        prose answer, so a check on the stored output reads it as conforming."""
        schema = _judge_schema()
        config = {"schema": schema, "judge_contract": True}
        got = await _drive_stage(tmp_path, monkeypatch, "Looks good to me, I'd pass it.", config)
        # The premise, measured in this run: every declared key IS on the stored output.
        assert set(schema) <= set(got["stored"]), sorted(got["stored"])
        assert got["stored"]["verdict"] == "REJECT"
        assert schema_shortfall(schema, got["stored"]) == ""
        notice = got["row"]["schema_shortfall"]
        assert notice.endswith("and got text, not an object"), notice
        assert all(key in notice for key in schema), notice
        assert got["status"] is RunStatus.COMPLETE

    async def test_a_stage_declaring_no_schema_is_never_checked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        got = await _drive_stage(tmp_path, monkeypatch, "free-form notes", {})
        assert got["stored"] == {"result": "free-form notes"}
        for surface in ("row", "rest", "event"):
            assert "schema_shortfall" not in got[surface], (surface, got[surface])


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
    `test_pp16_general_kind_as_run` does.
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


class TestTheIssuesOwnRun:
    async def test_a_prose_worker_is_named_on_every_round(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Before: 12 of 12 steps Done, six rounds, and not one row said the worker never honoured
        its schema. Every settled step now says it, on its own row."""
        got = await _drive_general_project(tmp_path / "prose", monkeypatch, prose=True)
        done = got["completed"]
        work = [r for r in done if r["node_id"] == "work"]
        judge = [r for r in done if r["node_id"] == "judge"]
        # Non-vacuity: the loop really ran more than one round of both stages.
        assert len(work) >= 2 and len(judge) >= 2, [r["node_id"] for r in done]
        unnamed = [r["node_id"] for r in done if not r.get("schema_shortfall")]
        assert not unnamed, f"steps that ignored their schema and said nothing: {unnamed}"
        assert {r["schema_shortfall"] for r in work} == {
            "the output ignored its declared schema: it asked for evidence, meaningful_progress, "
            "summary and got text, not an object"
        }
        assert all("reasoning" in r["schema_shortfall"] for r in judge)

    async def test_a_conforming_worker_is_named_nowhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        got = await _drive_general_project(tmp_path / "good", monkeypatch, prose=False)
        done = got["completed"]
        assert {"work", "judge"} <= {r["node_id"] for r in done}  # the premise: both ran
        named = [(r["node_id"], r["schema_shortfall"]) for r in done if "schema_shortfall" in r]
        assert not named, named
        assert got["status"] is RunStatus.COMPLETE

    async def test_the_notice_changes_nothing_else_about_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """🔴 The scope ceiling, as a differential. The same prose run twice, once with the
        comparison switched off: status, every journal row, every stored output and every prompt a
        later round received must be identical, and the ONLY difference is the notice itself."""
        on = await _drive_general_project(tmp_path / "on", monkeypatch, prose=True)
        monkeypatch.setattr("personalclaw.workflows.engine.schema_shortfall", lambda s, v: "")
        off = await _drive_general_project(tmp_path / "off", monkeypatch, prose=True)

        # Non-vacuity both ways: the notice was really on in one run and really off in the other.
        assert all(r.get("schema_shortfall") for r in on["completed"])
        assert not any("schema_shortfall" in r for r in off["completed"])

        assert on["status"] is off["status"]
        assert on["outputs"] == off["outputs"]
        assert on["prompts"] == off["prompts"]  # the notice never leaks into a later round
        without_notice = [
            {k: v for k, v in r.items() if k != "schema_shortfall"} for r in on["records"]
        ]
        assert without_notice == off["records"]


# ── a notice is per attempt ──────────────────────────────────────────────────


class _Canned:
    """One canned answer for every spawn, optionally a failed one."""

    def __init__(self, text: str, *, error: str = "") -> None:
        self.text = text
        self.error = error
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"sub{len(self.infos) + 1}", self.text)
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        """The error arrives when the child FINISHES, as a real subagent's failure does. Set at
        spawn, `dispatch_stage` reads it as a rejected spawn and fails synchronously instead."""
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
            info.error = self.error
        return info


async def test_a_re_run_that_fails_does_not_keep_the_previous_attempts_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_launch` clears the notice per attempt, as it clears `cached`. Only a stage's DONE settle
    sets it, so without the reset a stage that ignored its schema, was re-run and then failed would
    keep the first attempt's notice on its failed row, persisted, on every page load."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    stage = {"kind": "stage", "id": "work", "config": {"prompt": "do it", "schema": STAGE_DECLARED}}
    spec: dict[str, Any] = {
        "name": "wf3545-rerun",
        "root": {"kind": "sequence", "id": "root", "children": [stage]},
    }
    path = "root.children[0]"
    run = store.create(WorkflowRun(id="", workflow_name="wf3545-rerun"))
    store.write_spec(run.id, spec)
    first = RunController(run, spec, services=EngineServices(subagents=_Canned("I made progress.")))
    await first.run_to_completion(timeout=20)
    assert first.instances[path].schema_shortfall  # the vacuity floor: attempt 1 was named
    attempts = first.instances[path].attempt
    # A DONE stage keeps its no-double-execution claim for its TTL (#3531), and a re-run inside it
    # is refused at dispatch as DEGRADED, a path `_apply` clears on its own. Released here as the
    # TTL would release it, so attempt 2 spawns and settles out of band: the path only the reset in
    # `_launch` covers.
    release_execution_claim(first.instances[path].claim_target, first.instances[path].claim_holder)

    # Attempt 2 of the SAME instance, in the crash-resume shape that
    # `test_workflow_cached_node_surfacing` uses, and this time the worker fails.
    resumed = store.get(run.id)
    assert resumed is not None
    resumed.status = RunStatus.RUNNING
    failing = _Canned("", error="the worker crashed")
    second = RunController(resumed, spec, services=EngineServices(subagents=failing))
    # Loaded back from the state file, which is what makes the reset load-bearing.
    assert second.instances[path].schema_shortfall
    for inst in second.instances.values():
        inst.state = InstanceState.PENDING
    await second.run_to_completion(timeout=20)

    inst = second.instances[path]
    # It really re-ran and failed OUT OF BAND, at the settle: the cause is the manager's sentence
    # verbatim. A refusal at dispatch ("spawn rejected: …", or a claim still held) goes through
    # `_apply`, which clears the notice on its own and would leave the reset untested.
    assert inst.attempt > attempts and inst.state is InstanceState.FAILED, (
        inst.attempt,
        inst.state,
    )
    assert (
        inst.failure is not None and inst.failure.cause_plain == "the worker crashed"
    ), inst.failure
    assert inst.schema_shortfall == ""
    rows = [r for r in service._nodes_of(run.id) if r["instance_path"] == path]
    assert rows and "schema_shortfall" not in rows[0], rows


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


def test_every_bundled_stage_is_silent_when_it_conforms_and_named_when_it_does_not(
    tmp_path: Path,
) -> None:
    """🔴 The census the stage half rests on, through the PRODUCTION settle.

    Before #3531 a stage's output was `{"result": text}` whatever its schema, and a check at the
    settle fired on every schema-declaring stage, conforming or not. Each bundled stage node, under
    its own template's `runtime_hints`, is settled twice: with a conforming answer (named by none)
    and with prose (named by all, the judge stages included although the contract fills in their
    keys).
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
    silent: list[str] = []
    controllers: dict[str, RunController] = {}
    with _isolated(tmp_path):
        for name, node in stages:
            if name not in controllers:
                run = store.create(WorkflowRun(id="", workflow_name=name))
                controllers[name] = RunController(run, specs[name])
            controller, schema = controllers[name], node.config["schema"]
            good = controller._settled_stage_output(node, json.dumps(_conforming_answer(schema)))
            if good.schema_shortfall:
                false_alarms.append(f"{name}:{node.id}: {good.schema_shortfall}")
            prose = controller._settled_stage_output(node, "I did the work and it went well.")
            if not prose.schema_shortfall:
                silent.append(f"{name}:{node.id}")
    assert false_alarms == [], false_alarms
    assert silent == [], silent
