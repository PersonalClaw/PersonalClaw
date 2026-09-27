"""No gate carries words nobody is shown.

Two bundled templates wrote an `expression` gate with an approval's prompt: `optimize-harness`'s
frozen-region check said "Approve only if you have personally checked which path it touched",
and `produce-and-audit`'s quality gate said "Approve to accept the artifact as-is, or deny …".
An expression gate asks nobody — the engine decides it and never reads `prompt` — so neither
question ever reached a person: the check just failed, and the run ended on the raw condition.

Each was decided for what it is:

* `optimize-harness`'s `verify_scope` is a CHECK. A candidate that wrote into the frozen region is
  dead whatever it scored; letting a person approve it would undo the one thing the template
  promises. Its words are now the check's `message`, which is what the step's failure and the
  run's ending say when the condition is false.
* `produce-and-audit`'s `quality_gate` is an APPROVAL. Accepting an artifact the audit did not pass
  is a person's call, and its words said so. It is a real approval now, asked only when the audit
  did not pass: a branch accepts a passing artifact and asks about any other.

What these pin: every gate's text is a key the engine reads for that gate's kind, an expression
gate's `message` is its failure's cause, and both templates behave as decided through their real
structure.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from personalclaw.approval_answer import YOU
from personalclaw.workflows import human_input as HI
from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import Node, NodeKind, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio

#: The words each gate kind is shown or graded by — the keys the engine reads for it. An
#: approval's and an event's are its ask (`engine._ask_payload`: `prompt`, else `message`); a
#: judge's `prompt` is the rubric its model is sent; an expression's `message` is its failure. A
#: verifier or a ladder reads no words at all.
READ_TEXT: dict[str, frozenset[str]] = {
    "approval": frozenset({"prompt", "message"}),
    "event": frozenset({"prompt", "message"}),
    "judge": frozenset({"prompt"}),
    "expression": frozenset({"message"}),
    "verify_command": frozenset(),
    "verify_script": frozenset(),
    "ladder": frozenset(),
}

#: Keys that hold words meant for someone, on any gate.
TEXT_KEYS = ("prompt", "message")


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


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)
    if node.body is not None:
        yield from _walk(node.body)
    for case in node.cases.values():
        yield from _walk(case)
    if node.default_case is not None:
        yield from _walk(node.default_case)


def _root(template: str) -> Node:
    wdef = read_template(template)
    assert wdef is not None, f"{template} does not load"
    return wdef.root if isinstance(wdef.root, Node) else Node.from_dict(wdef.root)


def _gates() -> list[tuple[str, str, str, frozenset[str]]]:
    """(template, gate id, kind, text keys it carries) for every gate, as production reads it."""
    found = []
    for name in sorted(template_names()):
        for node in _walk(_root(name)):
            if node.kind == NodeKind.GATE:
                cfg = node.config or {}
                carried = frozenset(k for k in TEXT_KEYS if k in cfg)
                found.append((name, node.id, str(cfg.get("kind") or ""), carried))
    return found


# ── the census ───────────────────────────────────────────────────────────────


def test_no_bundled_gate_carries_words_the_engine_never_shows() -> None:
    """🔴 Red on main: `optimize-harness`'s `verify_scope` and `produce-and-audit`'s
    `quality_gate` were expression gates carrying an approval's `prompt`."""
    gates = _gates()
    assert len(gates) >= 20, f"census found too few gates to mean anything: {gates}"
    kinds = {kind for _t, _g, kind, _k in gates}
    assert kinds <= set(
        READ_TEXT
    ), f"a gate kind this census does not know: {kinds - set(READ_TEXT)}"
    unshown = [
        (template, gate_id, kind, sorted(carried - READ_TEXT[kind]))
        for template, gate_id, kind, carried in gates
        if carried - READ_TEXT[kind]
    ]
    assert unshown == [], f"words on a gate the engine never reads: {unshown}"


def test_the_census_sees_words_where_the_engine_reads_them() -> None:
    """Positive control: the census would see a gate's text if it were there. Every approval and
    judge in the library carries its `prompt`, and the frozen-region check its `message`."""
    gates = {(t, g): (kind, carried) for t, g, kind, carried in _gates()}
    assert gates[("optimize-harness", "verify_scope")] == ("expression", frozenset({"message"}))
    assert gates[("produce-and-audit", "quality_gate")] == ("approval", frozenset({"prompt"}))
    assert gates[("publish-article", "approve")] == ("approval", frozenset({"prompt"}))
    assert gates[("goal-pursuit-monitor", "park")] == ("event", frozenset({"message"}))
    assert all(
        carried == frozenset({"prompt"})
        for (_t, _g), (kind, carried) in gates.items()
        if kind == "judge"
    )


# ── an expression gate's message ─────────────────────────────────────────────


async def _until(predicate, *, what: str, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.05)


async def _start(spec: dict[str, Any], *, inputs: dict[str, Any] | None = None) -> RunController:
    spec = copy.deepcopy(spec)
    run = store.create(
        WorkflowRun(id="", workflow_name=spec["name"], mode="background", inputs=dict(inputs or {}))
    )
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=EngineServices())
    await c.start()
    return c


async def _run(spec: dict[str, Any], *, inputs: dict[str, Any] | None = None) -> RunController:
    c = await _start(spec, inputs=inputs)
    await _until(lambda: c.run.is_terminal, what=f"the run to end (it is {c.run.status.value})")
    return c


def _transform(node_id: str, expr: Any = None) -> dict[str, Any]:
    return {"kind": "transform", "id": node_id, "config": {"expr": expr or {"ran": node_id}}}


def _started(run_id: str) -> set[str]:
    return {
        str(e.get("node_id") or "")
        for e in J.journal_records(run_id, kinds={J.STEP_STARTED})
        if e.get("node_id")
    }


async def test_a_failed_expression_gate_says_its_message_rather_than_its_condition() -> None:
    check = {
        "kind": "gate",
        "id": "in_scope",
        "config": {
            "kind": "expression",
            "expr": "{{nodes.scan.output.outcome}} != 'scope_violation'",
            "message": "the candidate wrote outside its sandbox",
        },
    }
    spec = {
        "name": "said",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                _transform("scan", {"outcome": "scope_violation"}),
                check,
                _transform("score"),
            ],
        },
    }
    c = await _run(spec)
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == (
        "“in_scope” failed: the candidate wrote outside its sandbox, so nothing after it ran."
    )
    failure = c.instances["root.children[1]"].failure
    assert failure is not None and failure.cause_plain == "the candidate wrote outside its sandbox"


async def test_an_expression_gate_with_no_message_still_names_its_condition() -> None:
    """CONTROL: `message` is the author's to write; without it the condition is the cause."""
    spec = {
        "name": "unsaid",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "gate", "id": "check", "config": {"kind": "expression", "expr": "1 == 2"}}
            ],
        },
    }
    c = await _run(spec)
    assert c.run.error_message == "“check” failed: gate condition is false: 1 == 2."


# ── both templates, through their real structure ─────────────────────────────


def _stub_work(template: str, outputs: dict[str, Any]) -> dict[str, Any]:
    """The template as production reads it — every container, gate, id, label and `needs` — with
    each step's WORK replaced by a zero-token transform returning `outputs[id]` (else a marker)."""

    def stub(node: Node) -> dict[str, Any]:
        out: dict[str, Any]
        if node.kind == NodeKind.GATE:
            out = {"kind": "gate", "id": node.id, "config": copy.deepcopy(node.config or {})}
        elif node.is_container:
            out = {
                "kind": node.kind.value,
                "id": node.id,
                "config": copy.deepcopy(node.config or {}),
            }
            if node.children:
                out["children"] = [stub(child) for child in node.children]
            if node.body is not None:
                out["body"] = stub(node.body)
            if node.cases:
                out["cases"] = {label: stub(case) for label, case in node.cases.items()}
            if node.default_case is not None:
                out["default"] = stub(node.default_case)
        else:
            out = _transform(node.id, outputs.get(node.id))
        if node.label:
            out["label"] = node.label
        if node.needs:
            out["needs"] = list(node.needs)
        return out

    return {"name": f"stub-{template}", "root": stub(_root(template))}


PRODUCE_INPUTS = {"subject": "a migration plan", "artifact_kind": "plan", "acceptance": ""}
FINDINGS = [{"severity": "major", "title": "two jobs share state and the plan misses it"}]


def _produce(verdict: str) -> dict[str, Any]:
    return _stub_work(
        "produce-and-audit",
        {
            "triage": {"tier": "light", "why": "bounded"},
            "produce": {"artifact": "the plan"},
            "audit": {"verdict": verdict, "findings": FINDINGS if verdict != "pass" else []},
        },
    )


async def test_produce_and_audit_accepts_an_artifact_the_audit_passed_without_asking() -> None:
    c = await _run(_produce("pass"), inputs=PRODUCE_INPUTS)
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert "accepted" in _started(c.run.id)
    assert "quality_gate" not in _started(c.run.id), "a passing artifact asked a person anyway"
    assert HI.list_continuations(c.run.id) == []


async def test_produce_and_audit_asks_you_about_an_artifact_the_audit_did_not_pass() -> None:
    """🔴 Red on main: the expression gate failed and the run ended `failed` asking nobody."""
    c = await _start(_produce("revise"), inputs=PRODUCE_INPUTS)
    await _until(
        lambda: c.run.status == RunStatus.NEEDS_INPUT or c.run.is_terminal,
        what="the run to ask about the artifact",
    )
    assert c.run.status == RunStatus.NEEDS_INPUT, c.run.error_message
    (ask,) = HI.list_continuations(c.run.id)
    assert ask.node_id == "quality_gate"
    prompt = str((ask.ask or {}).get("prompt") or "")
    assert prompt.startswith("The audit found issues worth your decision:")
    assert "two jobs share state and the plan misses it" in prompt, "the findings are the decision"
    assert prompt.endswith(
        "Approve to accept the plan as it is. Deny ends the run without accepting it."
    )
    assert c.resume(ask.token, True, by=YOU)["ok"] is True
    await _until(lambda: c.run.is_terminal, what="the approved run to end")
    assert c.run.status == RunStatus.COMPLETE


async def test_produce_and_audit_ends_declined_when_you_do_not_accept() -> None:
    c = await _start(_produce("revise"), inputs=PRODUCE_INPUTS)
    await _until(lambda: bool(HI.list_continuations(c.run.id)), what="the run to ask")
    (ask,) = HI.list_continuations(c.run.id)
    assert c.resume(ask.token, False, by=YOU)["ok"] is True
    await _until(lambda: c.run.is_terminal, what="the declined run to end")
    assert c.run.status == RunStatus.DECLINED


def _optimize(outcome: str) -> dict[str, Any]:
    return _stub_work(
        "optimize-harness",
        {
            "preflight": {"ok": True, "best_ever": 0.5, "rows_considered": 3},
            "propose": {"fix_fingerprint": "f1", "score": 0.9, "diff_text": "", "ops": []},
            "scope_check": {
                "ok": True,
                "outcome": outcome,
                "clean": outcome != "scope_violation",
                "frozen_touched": [".pclaw-lock.json"] if outcome == "scope_violation" else [],
            },
            "adjudicate": {"halt": True, "admitted": False},
            "file-proposal": {"proposed": False},
        },
    )


async def test_optimize_harness_refuses_a_frozen_region_touch_and_says_why() -> None:
    """🔴 Red on main: the run ended on the raw condition, under a label that read as though the
    refusal itself had failed."""
    c = await _run(_optimize("scope_violation"))
    started = _started(c.run.id)
    assert "verify_scope" in started, "positive control: the check ran"
    assert started.isdisjoint({"adjudicate", "file-proposal"}), "a dead candidate was carried on"
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == (
        "“Frozen region untouched” failed: the candidate wrote into the frozen region (the live "
        "artifact or its .pclaw-lock.json), which makes it dead whatever it scored, so nothing "
        "after it ran."
    )


async def test_optimize_harness_carries_a_clean_candidate_on() -> None:
    """CONTROL: a candidate that left the frozen region alone is adjudicated and filed."""
    c = await _run(_optimize("clean"))
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
    assert {"adjudicate", "file-proposal"} <= _started(c.run.id)
