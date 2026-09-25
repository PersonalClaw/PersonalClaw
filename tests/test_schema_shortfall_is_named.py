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

🔴 **The scope trap this file exists to pin.** On `main` no stage output is ever compared against
its declared schema: `RunController._reconcile_dispatched_stages` stores `{"result": "<raw text>"}`
for every stage regardless of `schema`. Measured through the production loader over the bundled
library — **78 nodes declare a `schema`, 47 of them stages and 31 `infer`** — a conformance check
reaching that settle would fire on **47 of 47** stage nodes and be measuring the absence of the
feature rather than a model's behaviour. `test_a_spawned_stage_is_not_annotated_at_the_spawn` and
`test_the_bundled_library_still_declares_stage_schemas` are what keep that gate honest; the seam
that applies a declared schema to a stage output is #3531's second one, and the stage half of this
notice belongs on or after it.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from personalclaw.ledger.reader import read_journal
from personalclaw.workflows import service, store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import (
    NodeResult,
    apply_schema_notice,
    dispatch_infer,
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
        out = apply_schema_notice(_node(NodeKind.INFER, {"schema": DECLARED}), result)
        assert out.schema_shortfall  # named
        assert out.state is InstanceState.DONE  # and nothing else moved
        assert out.failure is None
        assert out.degraded_reason == ""
        assert out.output == {"answer": "x"}

    def test_a_failed_step_is_not_annotated(self) -> None:
        """A FAILED step already carries a `Failure` saying why — `infer`'s own
        `model output was not valid JSON` is exactly that case, and the issue calls it legible.
        A notice there would restate a failure in a second vocabulary."""
        result = NodeResult(state=InstanceState.FAILED, output={"answer": "x"})
        assert (
            apply_schema_notice(
                _node(NodeKind.INFER, {"schema": DECLARED}), result
            ).schema_shortfall
            == ""
        )

    def test_a_degraded_step_that_produced_nothing_is_not_annotated(self) -> None:
        """`dispatch_stage`'s two DEGRADED paths (a restricted-origin skip, a claim already held)
        return `output=None`. Why they produced nothing is already in `degraded_reason`, and
        re-reading that as "the schema was ignored" would be false."""
        result = NodeResult(
            state=InstanceState.DEGRADED, output=None, degraded_reason="another worker holds it"
        )
        out = apply_schema_notice(_node(NodeKind.STAGE, {"schema": DECLARED}), result)
        assert out.schema_shortfall == ""
        assert out.degraded_reason == "another worker holds it"

    def test_a_node_declaring_no_schema_is_never_annotated(self) -> None:
        result = NodeResult(state=InstanceState.DONE, output={"anything": 1})
        assert apply_schema_notice(_node(NodeKind.INFER, {}), result).schema_shortfall == ""

    def test_a_spawned_stage_is_not_annotated_at_the_spawn(self) -> None:
        """🔴 THE SCOPE GUARD, and it is structural rather than a kind test.

        `dispatch_stage` returns RUNNING at the spawn with `{"subagent_id": …}`, and RUNNING is not
        in `SUCCESS_STATES` — so a stage never reaches the comparison. The out-of-band settle that
        produces its real output (`_reconcile_dispatched_stages`) does not pass through this seam
        at all. Both halves matter: on `main` that settle writes `{"result": "<raw text>"}`
        regardless of `schema`, so a check there would fire on 47 of 47 schema-declaring bundled
        stage nodes and measure the absence of the feature rather than a model's behaviour."""
        spawned = NodeResult(state=InstanceState.RUNNING, output={"subagent_id": "sub-1"})
        out = apply_schema_notice(_node(NodeKind.STAGE, {"schema": DECLARED}), spawned)
        assert out.schema_shortfall == ""
        # And the envelope that settle actually stores would have been named, had it come through
        # here — which is what makes the exclusion above a real gate and not a vacuous one.
        assert schema_shortfall(DECLARED, {"result": "some raw subagent text"}) != ""


# ── the dispatcher arm ───────────────────────────────────────────────────────


class TestInferThroughTheDispatcher:
    async def test_a_non_conforming_infer_output_is_named_and_still_done(self) -> None:
        async def fake(
            prompt: str, *, use_case: str = "background", output_type: Any = None
        ) -> str:
            return json.dumps({"answer": "it is fine"})

        node = _node(NodeKind.INFER, {"prompt": "p", "schema": DECLARED})
        # `dispatch_infer` itself does not annotate — the ONE seam in `dispatch` does, after the
        # judge contract has recomputed its keys onto the output.
        raw = await dispatch_infer(node, BindingContext(), completion=fake)
        assert raw.state is InstanceState.DONE
        out = apply_schema_notice(node, raw)
        assert "tier" in out.schema_shortfall and "answer" in out.schema_shortfall
        assert out.state is InstanceState.DONE

    async def test_a_conforming_infer_output_is_silent(self) -> None:
        async def fake(
            prompt: str, *, use_case: str = "background", output_type: Any = None
        ) -> str:
            return json.dumps({"tier": "high", "why": "a real reason"})

        node = _node(NodeKind.INFER, {"prompt": "p", "schema": DECLARED})
        raw = await dispatch_infer(node, BindingContext(), completion=fake)
        assert raw.output == {"tier": "high", "why": "a real reason"}  # the premise, not assumed
        assert apply_schema_notice(node, raw).schema_shortfall == ""


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
        # and conformed" — true here, and false on every stage row, which is never checked.
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


# ── the stage settle, where a stage's output is actually produced ────────────


async def test_a_schema_declaring_stage_settles_with_neither_a_false_alarm_nor_a_false_all_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 The scope guard at the seam that matters, driven rather than argued.

    `test_a_spawned_stage_is_not_annotated_at_the_spawn` proves the dispatch seam skips a stage.
    That is half the claim: a stage's real output is produced later, out of band, by
    `_reconcile_dispatched_stages`, which writes its `step_completed` row through the same journal
    writer. So this drives a REAL stage (real `SubagentManager` over the shipped `ScriptedProvider`)
    that declares a `schema` and whose worker answers in prose, and reads what that settle wrote.

    Two failure modes, and the row must avoid both:

    * a FALSE ALARM — naming a shortfall against the `{"result": …}` envelope `main` stores for
      every stage, which measures the missing #3531 seam rather than the worker (47 of 47 bundled
      schema-declaring stages would fire);
    * a FALSE ALL-CLEAR — an always-written `"schema_shortfall": ""`, which would assert on every
      `general-project` worker row that the worker honoured a schema nothing ever checked.
    """
    from personalclaw.llm.registry import SCRIPTED_PROVIDER_ENV
    from personalclaw.llm.scripted import ScriptedProvider
    from personalclaw.subagent import SubagentManager
    from tests.test_workflows_stage_usage_end_to_end import _ctx_builder, _sessions_over

    prose = "I looked into it and everything seems fine to me."
    declared = {"summary": "string", "meaningful_progress": "boolean"}
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {
                "version": 1,
                "on_exhausted": "repeat_last",
                "turns": [
                    {
                        "text": prose,
                        "stop_reason": "end_turn",
                        "usage": {
                            "input_tokens": 11,
                            "output_tokens": 7,
                            "cache_creation_tokens": 0,
                            "cache_read_tokens": 0,
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv(SCRIPTED_PROVIDER_ENV, str(script))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    spec: dict[str, Any] = {
        "name": "wf3545-stage",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {
                    "kind": "stage",
                    "id": "work",
                    "config": {"prompt": "do the thing", "schema": declared},
                }
            ],
        },
    }
    path = "root.children[0]"
    with _isolated(tmp_path):
        provider = ScriptedProvider()
        manager = SubagentManager(
            sessions=_sessions_over(provider), ctx_builder=_ctx_builder(), is_yolo=lambda: True
        )
        run = store.create(WorkflowRun(id="", workflow_name="wf3545-stage"))
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices(subagents=manager, cwd=""))
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

        # The measured trap, in this very run: the settle stored the unstructured envelope
        # regardless of `schema`, and the comparison WOULD name it — so the absence asserted below
        # is a real gate rather than a check that could not have fired.
        stored = store.read_output(run.id, path)
        assert stored == {"result": prose}
        assert schema_shortfall(declared, stored) != ""

        # Neither a false alarm nor a false all-clear: the key is not on the row at all.
        assert "schema_shortfall" not in rows[0]
        assert inst.schema_shortfall == ""
        rest = [r for r in service._nodes_of(run.id) if r["instance_path"] == path]
        assert rest and "schema_shortfall" not in rest[0]
        # And the run ends exactly as it does on `main`.
        assert status is RunStatus.COMPLETE


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
    """🔴 The non-vacuity control for the scope guard.

    `test_a_spawned_stage_is_not_annotated_at_the_spawn` is only meaningful while schema-declaring
    stages EXIST — if the library stopped shipping them, that test would pass against nothing and
    the 47-of-47 reasoning would be unfalsifiable. Asserted as a floor rather than an exact count
    so adding a template does not red this, while emptying the category does.
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
