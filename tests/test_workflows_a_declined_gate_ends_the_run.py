"""Only an approval lets what follows an approval gate run.

On `main` a Deny made the gate FAILED, and `on_error: null_continue` — the default — walked past it:
`publish` after `approve` ran, and the run ended `failed` with no sentence. The same happened when
nobody answered: a background gate times out in 45 s, `publish-article`'s in 24 h, and the next
step ran either way. `needs` means AFTER, not after-approval, so nothing in the engine stopped it.

What these pin, against the real controller:

* a Deny DECLINES the gate — not a failure — and the run ends `declined`, naming the gate and who;
* every step after it, in each sequence that holds it, is skipped with that reason, and runs
  nothing — whatever `on_error` the gate declares, and through a `needs` edge in a parallel too;
* an approval nobody gave (the deadline passed) stops what follows the same way, and ends the run
  `failed` with how long it waited;
* every bundled template's approval gate, run through its real structure, now stops what follows.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from personalclaw.workflows import human_input as HI
from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, Node, NodeKind, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


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


class _Clock:
    """The controller's clock seam, so a deadline passes without a test waiting for it."""

    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


def _transform(node_id: str) -> dict[str, Any]:
    return {"kind": "transform", "id": node_id, "config": {"expr": {"ran": node_id}}}


def _gate(node_id: str = "approve", **cfg: Any) -> dict[str, Any]:
    return {
        "kind": "gate",
        "id": node_id,
        "config": {"kind": "approval", "prompt": "Publish the draft?", **cfg},
    }


def _spec(*children: dict[str, Any], name: str = "decline") -> dict[str, Any]:
    return {"name": name, "root": {"kind": "sequence", "id": "s", "children": list(children)}}


async def _until(predicate, *, what: str, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.05)


async def _parked(spec: dict[str, Any], *, clock: _Clock | None = None) -> RunController:
    """Start the run and return once it waits at its gate. Started rather than driven to
    completion: a background gate carries a deadline, so its loop keeps ticking while it waits."""
    spec = copy.deepcopy(spec)
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], mode="background"))
    store.write_spec(run.id, spec)
    c = RunController(run, spec, services=EngineServices(clock=clock or _Clock()))
    await c.start()
    await _until(
        lambda: c.run.status == RunStatus.NEEDS_INPUT and bool(HI.list_continuations(c.run.id)),
        what="the run to wait at its gate",
    )
    return c


async def _ended(c: RunController) -> None:
    await _until(lambda: c.run.is_terminal, what=f"the run to end (it is {c.run.status.value})")


def _started(run_id: str, *, control: str = "approve") -> set[str]:
    """Every node id the run ever dispatched — what 'ran' means, read off its journal
    (`step_started` is a journal record; the ledger mirror does not carry it).

    `control` is a step the run is known to have started — the gate itself, which starts before it
    waits. It is the positive control: a read that found nothing would otherwise pass every
    "nothing after it ran" below."""
    started = {
        str(e.get("node_id") or "")
        for e in J.journal_records(run_id, kinds={J.STEP_STARTED})
        if e.get("node_id")
    }
    assert control in started, f"positive control: {control!r} never started ({sorted(started)})"
    return started


def _deny(c: RunController, **kw: Any) -> dict[str, Any]:
    pending = HI.list_continuations(c.run.id)
    assert len(pending) == 1, [p.node_id for p in pending]
    return c.resume(pending[0].token, False, **kw)


async def test_denying_a_gate_runs_nothing_after_it_and_ends_the_run_declined(monkeypatch) -> None:
    monkeypatch.setattr("personalclaw.identity.operator_name", lambda: "Keyur")
    c = await _parked(_spec(_transform("draft"), _gate(), _transform("publish")))
    assert _deny(c)["ok"] is True
    await _ended(c)

    # The defect first: on `main` the step after a denied gate ran.
    assert "publish" not in _started(c.run.id), "a step after a denied approval ran"
    assert c.run.status.value == "declined", c.run.status
    assert c.run.error_message == "“approve” was declined by Keyur, so nothing after it ran."
    gate, publish = c.instances["root.children[1]"], c.instances["root.children[2]"]
    assert gate.state.value == "declined" and gate.failure is None, "a decline is not a failure"
    assert gate.degraded_reason == "declined by Keyur"
    assert publish.state == InstanceState.SKIPPED
    assert publish.degraded_reason == "not run: “approve” was declined"
    # The gate's record for Inspect: the answer, and that it was not an approval.
    record = store.read_output(c.run.id, "root.children[1]")
    assert record == {"answer": False, "approved": False, "declined_by": "Keyur"}
    # The ledger keeps the decision as the answer it was, and says why `publish` did not run.
    resolved = [e for e in J.ledger(c.run.id) if e.get("kind") == J.GATE_RESOLVED]
    assert [e.get("approved") for e in resolved] == [False]
    skipped = [e for e in J.ledger(c.run.id) if e.get("kind") == J.STEP_SKIPPED]
    assert [(e.get("node_id"), e.get("reason")) for e in skipped] == [
        ("publish", "not run: “approve” was declined")
    ]


async def test_with_no_name_given_the_record_says_you(monkeypatch) -> None:
    monkeypatch.setattr("personalclaw.identity.operator_name", lambda: "")
    c = await _parked(_spec(_gate(), _transform("publish")))
    _deny(c)
    await _ended(c)
    assert c.run.error_message == "“approve” was declined by you, so nothing after it ran."


def test_a_channel_or_trigger_answer_is_named_for_what_it_is(monkeypatch) -> None:
    from personalclaw.workflows.gate_answers import decliner

    monkeypatch.setattr("personalclaw.identity.operator_name", lambda: "Keyur")
    assert decliner("", "") == "Keyur"
    assert decliner("keyur", "slack") == "keyur in slack"
    assert decliner("trigger:t-9", "") == "the trigger t-9"


async def test_a_decline_is_not_a_failure_that_on_error_can_walk_past() -> None:
    """`on_error` is a FAILURE policy. Declared on the gate itself, it still cannot carry the run
    past a person's no."""
    c = await _parked(
        _spec(_gate(on_error="null_continue"), _transform("publish"), _transform("notify"))
    )
    _deny(c)
    await _ended(c)
    assert _started(c.run.id).isdisjoint({"publish", "notify"})
    assert c.run.status.value == "declined"
    assert [c.instances[f"root.children[{i}]"].state for i in (1, 2)] == [
        InstanceState.SKIPPED,
        InstanceState.SKIPPED,
    ]


async def test_a_decline_stops_every_enclosing_sequence() -> None:
    """A gate in a nested sequence stops what follows it there AND what follows the sequence."""
    spec = _spec(
        {
            "kind": "sequence",
            "id": "review",
            "children": [_transform("check"), _gate(), _transform("tidy")],
        },
        _transform("publish"),
    )
    c = await _parked(spec)
    _deny(c)
    await _ended(c)
    assert _started(c.run.id).isdisjoint({"tidy", "publish"})
    assert c.instances["root.children[0].children[2]"].state == InstanceState.SKIPPED
    assert c.instances["root.children[1]"].state == InstanceState.SKIPPED


async def test_a_decline_stops_what_needs_it_in_a_parallel() -> None:
    """`needs` means after — and after a refused approval nothing runs, even on a plain edge."""
    spec = {
        "name": "decline-parallel",
        "root": {
            "kind": "parallel",
            "id": "p",
            "children": [
                _gate(),
                {**_transform("publish"), "needs": ["approve"]},
            ],
        },
    }
    c = await _parked(spec)
    _deny(c)
    await _ended(c)
    assert "publish" not in _started(c.run.id)
    assert c.run.status.value == "declined"


async def test_an_approval_nobody_gave_runs_nothing_after_it_and_fails_the_run() -> None:
    """Silence is not consent. A gate waits the owner's approval window (two hours unless they
    chose otherwise); past that, what follows it must not run, and the run says how long it
    waited — loud (`failed`), because nobody chose this."""
    clock = _Clock()
    c = await _parked(_spec(_transform("draft"), _gate(), _transform("publish")), clock=clock)
    clock.t += 2 * 3600 + 1
    c.wake()
    await _ended(c)
    assert "publish" not in _started(c.run.id), "a step after an unanswered approval ran"
    assert c.run.status == RunStatus.FAILED
    assert c.run.error_message == (
        "“approve” was not approved: no answer within 2 hours, so nothing after it ran."
    )
    gate = c.instances["root.children[1]"]
    assert gate.failure is not None and gate.failure.cause_plain == "no answer within 2 hours"
    assert c.instances["root.children[2]"].degraded_reason == "not run: “approve” was not approved"


# ── every bundled template's approval gate ───────────────────────────────────


def _approval_gates() -> list[tuple[str, str]]:
    """(template, gate id) for every approval gate in the bundled library, as production reads it
    (macros expanded)."""
    found = []
    for name in template_names():
        wdef = read_template(name)
        if wdef is None:
            continue
        root = wdef.root if isinstance(wdef.root, Node) else Node.from_dict(wdef.root)
        stack = [root]
        while stack:
            node = stack.pop()
            cfg = node.config or {}
            if node.kind == NodeKind.GATE and cfg.get("kind") == "approval":
                found.append((name, node.id))
            stack.extend(node.children)
            stack.extend(n for n in (node.body, node.default_case) if n is not None)
            stack.extend(node.cases.values())
    return found


def _stubbed(template: str) -> dict[str, Any]:
    """The template's REAL structure — its containers, ids, positions and `needs` — with every
    step's work replaced by a zero-token transform, so what follows the gate is exactly what the
    template would have run. Only the approval gate keeps its kind (and its declared deadline)."""
    wdef = read_template(template)
    assert wdef is not None
    root = wdef.root if isinstance(wdef.root, Node) else Node.from_dict(wdef.root)

    def stub(node: Node) -> dict[str, Any]:
        if node.kind in (NodeKind.SEQUENCE, NodeKind.PARALLEL):
            out = {
                "kind": node.kind.value,
                "id": node.id,
                "children": [stub(c) for c in node.children],
            }
        elif node.kind == NodeKind.GATE and (node.config or {}).get("kind") == "approval":
            deadline = (node.config or {}).get("timeout_secs")
            out = _gate(node.id, **({} if deadline is None else {"timeout_secs": deadline}))
        else:
            assert node.body is None and not node.cases, f"{template}: stub cannot hold {node.kind}"
            out = _transform(node.id)
        if node.needs:
            out["needs"] = list(node.needs)
        return out

    return {"name": f"stub-{template}", "root": stub(root)}


GATES = _approval_gates()


def test_the_census_found_every_approval_gate_in_the_library() -> None:
    """Non-vacuity for the two tests below: the library's approval gates, by name."""
    assert GATES == [
        ("design-review", "accept"),
        ("project-planning", "accept_plan"),
        ("publish-article", "approve"),
    ]


def _followers(spec: dict[str, Any], gate_id: str) -> list[str]:
    """The step ids after the gate in the root sequence — the work the gate guards."""
    ids = [c["id"] for c in spec["root"]["children"]]
    return ids[ids.index(gate_id) + 1 :]


@pytest.mark.parametrize(("template", "gate_id"), GATES)
async def test_every_bundled_approval_gate_stops_what_follows_it_when_denied(
    template: str, gate_id: str
) -> None:
    spec = _stubbed(template)
    c = await _parked(spec)
    _deny(c)
    await _ended(c)
    followers = set(_followers(spec, gate_id))
    assert _started(c.run.id, control=gate_id).isdisjoint(followers), f"{template}: ran"
    assert c.run.status.value == "declined"
    if template == "publish-article":
        # The one bundled approval that guards irreversible writes: both knowledge writes.
        assert followers == {"store", "record-decision"}


@pytest.mark.parametrize(("template", "gate_id"), GATES)
async def test_every_bundled_approval_gate_stops_what_follows_it_when_nobody_answers(
    template: str, gate_id: str
) -> None:
    clock = _Clock()
    spec = _stubbed(template)
    c = await _parked(spec, clock=clock)
    clock.t += 2 * 86400  # past any declared deadline, including publish-article's 24 h
    c.wake()
    await _ended(c)
    assert _started(c.run.id, control=gate_id).isdisjoint(set(_followers(spec, gate_id)))
    assert c.run.status == RunStatus.FAILED
    assert "no answer within" in c.run.error_message
