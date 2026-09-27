"""A gate is a gate: a check that fails ends what follows it.

On `main` only an approval stopped the run (#3728). A judge, expression or verifier gate that
failed was a failure like any other, and `on_error: null_continue` — the default — walked past it,
so the write the check guarded ran anyway: `knowledge-synthesis` stored a synthesis its own
`grounded` judge had rejected, `paper-ingest` stored claims its check had refuted, and
`audit-sweep` applied fixes on a run whose `fix` input said not to.

What these pin, against the real controller:

* a check gate that fails — an expression that is false, a verifier that fails, a judge that rejects
  — ends the run `failed`, with a sentence that names the gate and the check's own reason, and
  every step after it in each sequence that holds it is skipped, saying why, and never runs;
* a judge that escalates stops what follows the same way and ends the run `escalated`;
* the gate continues only when it says so itself: `on_error: null_continue` (the failure still
  counts, so the run ends failed) or `allow_failure: true` (recorded as degraded);
* every check gate in the bundled library, run through its template's real structure, now stops
  what follows it — except `knowledge-lint`'s, which declares that a cluster whose consolidation
  lost detail is recorded and the pass goes on;
* `audit-sweep`'s `fix` switch is a branch now, not a check: a report-only audit reports and goes
  on to its next round, and fixes only run when `fix` is on.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, Node, NodeKind, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio

#: The gate kinds whose passing is a CHECK the engine decides, not a person's approval.
CHECKS = ("judge", "expression", "verify_command", "verify_script", "ladder")

#: Long enough for the judge's free rule tier (`judge_pretier`) to let it through to the model.
EVIDENCE = "A substantial deliverable with plenty of characters for the pre-tier to allow through."


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


def _check(node_id: str = "check", kind: str = "expression", **cfg: Any) -> dict[str, Any]:
    """A check gate of `kind` that FAILS: a false expression, a verifier the test's `verify`
    refuses, a judge the test's `completion` rejects."""
    base: dict[str, Any] = {
        "expression": {"expr": "1 == 2"},
        "verify_command": {"verify": {"command": "false", "label": "tests"}},
        "judge": {"prompt": "Is the draft right?", "evidence": EVIDENCE},
    }[kind]
    return {"kind": "gate", "id": node_id, "config": {"kind": kind, **base, **cfg}}


def _spec(*children: dict[str, Any], name: str = "check") -> dict[str, Any]:
    return {"name": name, "root": {"kind": "sequence", "id": "s", "children": list(children)}}


def _judge_says(verdict: str):
    async def completion(instruction, use_case=None, output_type=None, **_kw):
        return json.dumps(
            {"verdict": verdict, "proof": "read the draft", "reasoning": "not grounded"}
        )

    return completion


async def _refuse(_block: dict[str, Any]) -> bool:
    return False


def _services(**kw: Any) -> EngineServices:
    return EngineServices(completion=kw.get("completion") or _judge_says("REJECT"), verify=_refuse)


async def _until(predicate, *, what: str, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.05)


async def _run(
    spec: dict[str, Any], *, services: EngineServices | None = None, inputs: dict | None = None
) -> RunController:
    spec = copy.deepcopy(spec)
    run = store.create(
        WorkflowRun(id="", workflow_name=spec["name"], mode="background", inputs=dict(inputs or {}))
    )
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=services or _services())
    await c.start()
    await _until(lambda: c.run.is_terminal, what=f"the run to end (it is {c.run.status.value})")
    return c


def _started(run_id: str, *, control: str) -> set[str]:
    """Every node id the run dispatched, off its journal. `control` is a step known to have
    started — the gate itself — so a read that found nothing cannot pass "nothing ran"."""
    started = {
        str(e.get("node_id") or "")
        for e in J.journal_records(run_id, kinds={J.STEP_STARTED})
        if e.get("node_id")
    }
    assert control in started, f"positive control: {control!r} never started ({sorted(started)})"
    return started


# ── each kind of check ───────────────────────────────────────────────────────


async def test_a_false_expression_gate_runs_nothing_after_it_and_fails_the_run() -> None:
    c = await _run(_spec(_transform("draft"), _check(), _transform("publish")))

    # The defect first: on `main` the step after a failed check ran.
    assert "publish" not in _started(c.run.id, control="check"), "a step after a failed check ran"
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == (
        "“check” failed: gate condition is false: 1 == 2, so nothing after it ran."
    )
    publish = c.instances["root.children[2]"]
    assert publish.state == InstanceState.SKIPPED
    assert publish.degraded_reason == "not run: “check” failed"
    skipped = [e for e in J.ledger(c.run.id) if e.get("kind") == J.STEP_SKIPPED]
    assert [(e.get("node_id"), e.get("reason")) for e in skipped] == [
        ("publish", "not run: “check” failed")
    ]


async def test_a_failed_verifier_gate_stops_what_follows() -> None:
    c = await _run(_spec(_check("verify", "verify_command"), _transform("handoff")))
    assert "handoff" not in _started(c.run.id, control="verify")
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == "“verify” failed: verification failed, so nothing after it ran."


async def test_a_rejecting_judge_gate_stops_the_write_it_guards() -> None:
    c = await _run(
        _spec(_transform("synthesize"), _check("grounded", "judge"), _transform("store"))
    )
    assert "store" not in _started(c.run.id, control="grounded"), "the guarded write ran"
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == (
        "“grounded” failed: judge returned REJECT: not grounded, so nothing after it ran."
    )


async def test_an_escalating_judge_stops_what_follows_and_ends_the_run_escalated() -> None:
    """A judge that will not rule either way has not passed the work: what it guards waits for a
    person, and the run says it stopped for one."""
    c = await _run(
        _spec(_check("accept", "judge"), _transform("publish")),
        services=_services(completion=_judge_says("ESCALATE")),
    )
    assert "publish" not in _started(c.run.id, control="accept")
    assert c.run.status == RunStatus.ESCALATED
    assert c.run.error_message == (
        "“accept” escalated: judge returned ESCALATE, so nothing after it ran."
    )
    assert c.instances["root.children[1]"].degraded_reason == "not run: “accept” escalated"


async def test_a_check_that_is_the_last_step_names_itself_as_the_reason() -> None:
    c = await _run(_spec(_transform("audit"), _check("quality_gate")))
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == "“quality_gate” failed: gate condition is false: 1 == 2."


# ── where the check sits ─────────────────────────────────────────────────────


async def test_a_failed_check_stops_every_enclosing_sequence() -> None:
    spec = _spec(
        {
            "kind": "sequence",
            "id": "review",
            "children": [_transform("read"), _check(), _transform("tidy")],
        },
        _transform("publish"),
    )
    c = await _run(spec)
    assert _started(c.run.id, control="check").isdisjoint({"tidy", "publish"})
    assert c.instances["root.children[0].children[2]"].state == InstanceState.SKIPPED
    assert c.instances["root.children[1]"].state == InstanceState.SKIPPED


async def test_a_failed_check_in_one_item_ends_the_fan_out_it_sits_in() -> None:
    """A fan-out's `on_item_error` is a failure policy for its items, not a declaration the GATE
    makes: a check that fails inside an item stops what follows the fan-out too."""
    spec = _spec(
        {
            "kind": "foreach",
            "id": "items",
            "config": {"items": ["a"], "on_item_error": "skip"},
            "body": {
                "kind": "sequence",
                "id": "item",
                "children": [_transform("condense"), _check(), _transform("write")],
            },
        },
        _transform("publish"),
    )
    c = await _run(spec)
    assert _started(c.run.id, control="check").isdisjoint({"write", "publish"})
    assert c.run.status == RunStatus.FAILED


async def test_a_failed_check_in_a_branch_case_stops_what_the_case_would_write() -> None:
    spec = _spec(
        _transform("assess"),
        {
            "kind": "branch",
            "id": "material",
            "config": {"on": "{{inputs.changed}}", "enum": ["true", "false"]},
            "cases": {
                "true": {
                    "kind": "sequence",
                    "id": "speak-up",
                    "children": [_check("evidence", "judge"), _transform("record")],
                },
                "false": _transform("quiet"),
            },
        },
        _transform("after"),
    )
    c = await _run(spec, inputs={"changed": True})
    assert _started(c.run.id, control="evidence").isdisjoint({"record", "after"})
    assert c.run.status == RunStatus.FAILED


async def test_a_failed_check_in_a_loop_body_ends_the_loop_and_the_run() -> None:
    spec = _spec(
        {
            "kind": "loop",
            "id": "search",
            "config": {"mode": "counted", "n": 3},
            "body": {
                "kind": "sequence",
                "id": "iterate",
                "children": [_transform("propose"), _check("in_scope"), _transform("adjudicate")],
            },
        },
        _transform("file"),
    )
    c = await _run(spec)
    assert _started(c.run.id, control="in_scope").isdisjoint({"adjudicate", "file"})
    started = [e for e in J.journal_records(c.run.id, kinds={J.STEP_STARTED})]
    assert sum(1 for e in started if e.get("node_id") == "propose") == 1, "a second round ran"


# ── continuing is the gate's own declaration ─────────────────────────────────


async def test_a_check_that_declares_null_continue_lets_what_follows_run() -> None:
    """The one construct the engine has for "carry on past my failure", declared on the gate. The
    failure still counts, so the run still ends failed — only what follows is allowed to run."""
    c = await _run(_spec(_check(on_error="null_continue"), _transform("publish")))
    assert "publish" in _started(c.run.id, control="check")
    assert c.run.status == RunStatus.FAILED
    assert c.instances["root.children[1]"].state == InstanceState.DONE


async def test_a_check_that_may_fail_is_recorded_as_degraded_and_the_run_goes_on() -> None:
    c = await _run(_spec(_check(allow_failure=True), _transform("publish")))
    assert "publish" in _started(c.run.id, control="check")
    assert c.run.status == RunStatus.COMPLETE
    assert c.instances["root.children[0]"].state == InstanceState.FAILED


async def test_a_run_level_on_error_on_another_node_changes_nothing() -> None:
    """Only the GATE's declaration counts: a step after it that says `null_continue` about ITS
    failures does not make the check's failure something to walk past."""
    c = await _run(
        _spec(
            _check(), {**_transform("publish"), "config": {"expr": {}, "on_error": "null_continue"}}
        )
    )
    assert "publish" not in _started(c.run.id, control="check")


# ── the bundled library ──────────────────────────────────────────────────────


def _walk(node: Node, path: str = "root"):
    yield node, path
    for i, child in enumerate(node.children):
        yield from _walk(child, f"{path}.children[{i}]")
    if node.body is not None:
        yield from _walk(node.body, f"{path}.body")
    for label, case in node.cases.items():
        yield from _walk(case, f"{path}.cases[{label}]")
    if node.default_case is not None:
        yield from _walk(node.default_case, f"{path}.default")


def _root(template: str) -> Node:
    wdef = read_template(template)
    assert wdef is not None, f"{template} does not load"
    return wdef.root if isinstance(wdef.root, Node) else Node.from_dict(wdef.root)


def _check_gates() -> list[tuple[str, str, str]]:
    """(template, gate id, kind) for every check gate in the library, as production reads it."""
    found = []
    for name in sorted(template_names()):
        for node, _path in _walk(_root(name)):
            kind = str((node.config or {}).get("kind") or "")
            if node.kind == NodeKind.GATE and kind in CHECKS:
                found.append((name, node.id, kind))
    return found


GATES = _check_gates()

#: The one bundled check that is RECORDED rather than obeyed, and why (ledger 291: "if a template
#: genuinely intends 'record the failure and continue', give it the explicit declaration"). Each
#: of `knowledge-lint`'s clusters is condensed and then judged for lost detail; nothing after the
#: judge writes, the fan-out declares that one bad cluster must not sink the pass, and the verdict
#: is the pass's report on that cluster.
RECORDED = {("knowledge-lint", "detail-preserved")}


def test_the_census_found_every_check_gate_in_the_library() -> None:
    """Non-vacuity for the two tests below, and a list a reviewer can read."""
    assert GATES == [
        ("code-project", "init_gate", "expression"),
        ("code-project", "feature_gate", "verify_command"),
        ("code-project", "verify", "verify_command"),
        ("dual-sink-watcher", "check-body-against-the-page", "judge"),
        ("gap-healing", "evidence-sufficient", "judge"),
        ("goal-pursuit-open-ended", "accept", "judge"),
        ("goal-pursuit-verifiable", "verify", "verify_command"),
        ("knowledge-lint", "detail-preserved", "judge"),
        ("knowledge-synthesis", "grounded", "judge"),
        ("market-monitor", "check-quoted-evidence", "judge"),
        ("optimize-harness", "verify_scope", "expression"),
        ("paper-ingest", "check-claims-against-the-paper", "judge"),
        ("produce-and-audit", "quality_gate", "expression"),
        ("publish-article", "accuracy-held", "judge"),
        ("rich-ingest", "grounded-in-transcript", "judge"),
        ("thesis-tracker", "falsifiable", "judge"),
        ("trending-repo-digest", "check-picks-came-from-the-page", "judge"),
    ]


def _declares_it_may_fail(template: str, gate_id: str) -> bool:
    for node, _path in _walk(_root(template)):
        if node.id == gate_id:
            cfg = node.config or {}
            return cfg.get("on_error") == "null_continue" or bool(cfg.get("allow_failure"))
    raise AssertionError(f"{template}: no {gate_id}")


def test_only_the_recorded_check_declares_that_it_may_fail() -> None:
    declared = {(t, g) for t, g, _k in GATES if _declares_it_may_fail(t, g)}
    assert declared == RECORDED


def _stubbed(template: str, gate_id: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """The template's REAL structure — its containers, ids, positions, `needs`, and every check
    gate's own declarations — with each step's work replaced by a zero-token transform, one item
    per fan-out and one round per loop. The gate under test keeps its kind and fails (see
    `_check`); every OTHER gate passes. Each branch is routed to the case that holds the gate.

    Returns the spec, the run inputs that route the branches, and the ids after the gate in every
    sequence that holds it — innermost first — which is what must never start."""
    root = _root(template)
    inputs: dict[str, Any] = {}
    followers: list[str] = []

    def holds(node: Node) -> bool:
        return any(n.id == gate_id for n, _p in _walk(node))

    def stub(node: Node) -> dict[str, Any]:
        cfg = node.config or {}
        out: dict[str, Any]
        if node.kind == NodeKind.SEQUENCE:
            # Children first, so an inner sequence records its followers before this one does.
            out = {"kind": "sequence", "id": node.id, "children": [stub(c) for c in node.children]}
            for index, child in enumerate(node.children):
                if holds(child):
                    # Every step in each later sibling — a container never starts; its leaves do.
                    for later in node.children[index + 1 :]:
                        followers.extend(n.id for n, _p in _walk(later) if n.id)
        elif node.kind == NodeKind.PARALLEL:
            out = {"kind": "parallel", "id": node.id, "children": [stub(c) for c in node.children]}
            out["config"] = {k: cfg[k] for k in ("join", "quorum") if k in cfg}
        elif node.kind == NodeKind.FOREACH:
            policy = {"on_item_error": cfg["on_item_error"]} if "on_item_error" in cfg else {}
            assert node.body is not None
            out = {"kind": "foreach", "id": node.id, "config": {"items": ["one"], **policy}}
            out["body"] = stub(node.body)
        elif node.kind == NodeKind.LOOP:
            assert node.body is not None
            out = {"kind": "loop", "id": node.id, "config": {"mode": "counted", "n": 1}}
            out["body"] = stub(node.body)
        elif node.kind == NodeKind.BRANCH:
            labels = list(node.cases)
            taken = next((label for label in labels if holds(node.cases[label])), labels[0])
            key = f"route_{len(inputs)}"
            inputs[key] = taken
            out = {
                "kind": "branch",
                "id": node.id,
                "config": {"on": f"{{{{inputs.{key}}}}}"},
                "cases": {label: stub(case) for label, case in node.cases.items()},
            }
            if node.default_case is not None:
                out["default"] = stub(node.default_case)
        elif node.kind == NodeKind.GATE and str(cfg.get("kind") or "") in CHECKS:
            declared = {k: cfg[k] for k in ("on_error", "allow_failure") if k in cfg}
            if node.id == gate_id:
                out = _check(node.id, str(cfg["kind"]))
            else:
                out = {
                    "kind": "gate",
                    "id": node.id,
                    "config": {"kind": "expression", "expr": "1 == 1"},
                }
            out["config"].update(declared)
        else:
            assert node.body is None and not node.cases, f"{template}: stub cannot hold {node.kind}"
            out = _transform(node.id)
        if node.needs:
            out["needs"] = list(node.needs)
        return out

    spec = {"name": f"stub-{template}", "root": stub(root)}
    return spec, inputs, followers


@pytest.mark.parametrize(("template", "gate_id", "kind"), GATES)
async def test_every_bundled_check_gate_stops_what_follows_it_when_it_fails(
    template: str, gate_id: str, kind: str
) -> None:
    spec, inputs, followers = _stubbed(template, gate_id)
    c = await _run(spec, inputs=inputs)
    started = _started(c.run.id, control=gate_id)
    if (template, gate_id) in RECORDED:
        # Recorded, not obeyed: the pass goes on, and the cluster reads as degraded, not done.
        assert c.run.status == RunStatus.COMPLETE, c.run.error_message
        return
    assert started.isdisjoint(followers), f"{template}: {sorted(started & set(followers))} ran"
    assert c.run.status == RunStatus.FAILED, c.run.status
    assert c.run.error_message.startswith(f"“{gate_id}” failed: "), c.run.error_message
    if template == "publish-article":
        # Its accuracy check guards the approval AND the two knowledge writes behind it.
        assert followers == ["approve", "store", "record-decision"]


# ── audit-sweep: a switch is a branch, not a check ───────────────────────────


def _audit_spec() -> dict[str, Any]:
    """`audit-sweep` as it ships, every step stubbed: the finders, the verify panel, the fix switch
    and the round's critic, inside its sweep loop, cut to one round."""
    root = _root("audit-sweep")

    def stub(node: Node) -> dict[str, Any]:
        cfg = node.config or {}
        if node.kind in (NodeKind.SEQUENCE, NodeKind.PARALLEL):
            return {
                "kind": node.kind.value,
                "id": node.id,
                "children": [stub(c) for c in node.children],
            }
        if node.kind == NodeKind.LOOP:
            assert node.body is not None
            return {
                "kind": "loop",
                "id": node.id,
                "config": {"mode": "counted", "n": 1},
                "body": stub(node.body),
            }
        if node.kind == NodeKind.BRANCH:
            out = {
                "kind": "branch",
                "id": node.id,
                "config": {k: cfg[k] for k in ("on", "enum") if k in cfg},
                "cases": {label: stub(case) for label, case in node.cases.items()},
            }
            if node.default_case is not None:
                out["default"] = stub(node.default_case)
            return out
        if node.kind == NodeKind.GATE:
            raise AssertionError(f"audit-sweep still gates on {node.id}: a switch is not a check")
        return _transform(node.id)

    return {"name": "stub-audit-sweep", "root": stub(root)}


@pytest.mark.parametrize("fix", [False, True])
async def test_an_audit_applies_fixes_only_when_asked_and_always_finishes_its_round(
    fix: bool,
) -> None:
    """`fix` defaults to off — "an audit that edits is no longer an independent check". On `main`
    the switch was an expression gate: off, it FAILED and `apply_fixes` ran anyway; under the
    rule above it would end every report-only audit failed after one round. It routes now."""
    c = await _run(_audit_spec(), inputs={"fix": fix})
    started = _started(c.run.id, control="completeness_critic")
    assert ("apply_fixes" in started) is fix, "fixes ran on a report-only audit" if not fix else ""
    assert c.run.status == RunStatus.COMPLETE, c.run.error_message
