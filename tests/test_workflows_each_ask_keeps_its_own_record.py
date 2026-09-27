"""Every question a run asks is its own question, and none is lost or left open (ledger 249).

Measured on `main` before this was written:

* a step that asked AGAIN in the same epoch — a parked step approved and run again that stopped
  again, a rewind that did not force — minted the same confirmation id as its first ask, because
  the id was `(run, gate, epoch)`. The first ask's answer then graded the second, and the second
  ask's answer re-graded the first;
* two steps waiting at once (a parallel of two gates) both asked whatever the later one had
  written to `run.attention`, one slot for the whole run: "Ship B?" twice;
* an edit queued on a PAUSED run lived only in its controller's memory: a gateway restart before
  the resume lost it, and the run resumed without it and said nothing;
* a run cancelled at its gate left the gate's `confirmation_pending` open for good — nothing ever
  wrote its `confirmation_resolved`;
* the answer to a parked step lives in memory until the dispatch it starts. That is kept, and
  pinned here in the one direction that matters: a restart in between asks again, and never runs
  the step as if it had been answered.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pytest

from personalclaw.action_providers.base import ActionContext, ActionResult
from personalclaw.approval_answer import YOU
from personalclaw.ledger import outcomes
from personalclaw.workflows import human_input as HI
from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio

STEP = "root.children[0]"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: home, raising=False)
    return home


class _Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class _State:
    """A dashboard state whose Inbox is a real `InboxStore`, read as the live service's is."""

    def __init__(self) -> None:
        from personalclaw.inbox import InboxStore

        class Svc:
            def __init__(self) -> None:
                self.inbox = InboxStore()

        self._inbox_svc = Svc()

    def notify(self, *a: Any, **k: Any) -> None:
        pass

    def broadcast_ws(self, *a: Any, **k: Any) -> None:
        pass


class _SignIn:
    """An action that stops at a sign-in page unless told the person signed in — or, with
    `always`, stops even then (the page asks for a password mid-run). Records each answer."""

    def __init__(self, *, always: bool = False) -> None:
        self.always = always
        self.answers: list[Any] = []

    async def execute(self, cfg: dict[str, Any], ctx: ActionContext, timeout: int = 30):
        self.answers.append(ctx.answer)
        if ctx.answer is None or self.always:
            return ActionResult(
                success=True,
                outcome="needs_input",
                stdout=json.dumps({"notes": ["reached the sign-in page"]}),
                stderr="Sign in to example.com, then confirm.",
            )
        return ActionResult(success=True, stdout=json.dumps({"balance": 12}))


def _transform(node_id: str) -> dict[str, Any]:
    return {"kind": "transform", "id": node_id, "config": {"expr": {"ran": node_id}}}


def _gate(node_id: str = "approve", prompt: str = "Publish the draft?") -> dict[str, Any]:
    return {"kind": "gate", "id": node_id, "config": {"kind": "approval", "prompt": prompt}}


def _action(node_id: str = "signin") -> dict[str, Any]:
    return {"kind": "action", "id": node_id, "config": {"provider": "signin-probe", "with": {}}}


def _spec(*children: dict[str, Any], name: str = "asks", kind: str = "sequence") -> dict[str, Any]:
    return {"name": name, "root": {"kind": kind, "id": "s", "children": list(children)}}


def _controller(
    spec: dict[str, Any],
    *,
    provider: Any = None,
    clock: _Clock | None = None,
    state: Any = None,
    run: WorkflowRun | None = None,
) -> RunController:
    if run is None:
        run = store.create(WorkflowRun(id="", workflow_name=spec["name"], mode="background"))
        store.write_spec(run.id, copy.deepcopy(spec))
    return RunController(
        run,
        copy.deepcopy(spec),
        services=EngineServices(
            clock=clock or _Clock(),
            get_provider=(lambda name: provider) if provider is not None else None,
            attention_state=state,
        ),
    )


def _restarted(c: RunController, **kw: Any) -> RunController:
    """The same run, as a gateway that restarted builds it: from what is on disk, nothing else."""
    run = store.get(c.run.id)
    spec = store.read_spec(c.run.id)
    assert run is not None and spec is not None
    return _controller(spec, run=run, **kw)


async def _until(predicate, *, what: str, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        await asyncio.sleep(0.05)


async def _asking(c: RunController, count: int = 1) -> list[HI.Continuation]:
    """Start the run and return once `count` questions are open."""
    await c.start()
    await _until(
        lambda: c.run.status == RunStatus.NEEDS_INPUT
        and len(HI.list_continuations(c.run.id)) == count,
        what=f"{count} open question(s)",
    )
    return HI.list_continuations(c.run.id)


async def _ended(c: RunController) -> None:
    await _until(lambda: c.run.is_terminal, what=f"the run to end (it is {c.run.status.value})")


def _rows(run_id: str, kind: str) -> list[dict[str, Any]]:
    return [e for e in J.ledger(run_id) if e.get("kind") == kind]


def _questions(run_id: str) -> list[outcomes.OutcomeQuestion]:
    return [q for q in (outcomes.parse(e) for e in J.ledger(run_id)) if q is not None]


def _started(run_id: str, *, control: str) -> set[str]:
    """Every node id the run dispatched, off its journal (`step_started` is not a ledger record).
    `control` is a step known to have started — the positive control an empty read would fail."""
    started = {
        str(e.get("node_id") or "")
        for e in J.journal_records(run_id, kinds={J.STEP_STARTED})
        if e.get("node_id")
    }
    assert control in started, f"positive control: {control!r} never started ({sorted(started)})"
    return started


# ── a second ask in the same epoch is a new question ─────────────────────────


async def test_a_step_that_asks_again_in_the_same_epoch_asks_a_new_question() -> None:
    """🔴 Red on main: the step's second ask carried its first ask's confirmation id, so the
    first ask's answer read as the answer to the question now open."""
    c = _controller(_spec(_action()), provider=_SignIn(always=True))
    (first,) = await _asking(c)
    assert c.resume(first.token, True, by=YOU)["ok"] is True
    await _until(
        lambda: len(_rows(c.run.id, J.CONFIRMATION_PENDING)) == 2
        and len(HI.list_continuations(c.run.id)) == 1,
        what="the step to stop and ask again",
    )
    (second,) = HI.list_continuations(c.run.id)
    assert second.epoch == first.epoch, "the premise: the second ask is in the SAME epoch"

    asked = [row["confirmation_id"] for row in _rows(c.run.id, J.CONFIRMATION_PENDING)]
    assert asked[0] != asked[1], "a new ask reused the old ask's confirmation id"
    # Each ask carries its own id, and the one answer given so far answers only the first.
    assert [first.confirmation_id, second.confirmation_id] == asked
    answered = [row["confirmation_id"] for row in _rows(c.run.id, J.CONFIRMATION_RESOLVED)]
    assert answered == [asked[0]], "the old answer must never answer the new ask"


async def test_the_second_asks_answer_never_regrades_the_first() -> None:
    """🔴 Red on main: with one id for both, the first ask's escalation bet was graded by the LAST
    matching answer — the second ask's Deny — and a yes was scored as a no."""
    c = _controller(_spec(_action()), provider=_SignIn(always=True))
    (first,) = await _asking(c)
    c.resume(first.token, True, by=YOU)
    await _until(
        lambda: len(_rows(c.run.id, J.CONFIRMATION_PENDING)) == 2
        and len(HI.list_continuations(c.run.id)) == 1,
        what="the second ask",
    )
    (second,) = HI.list_continuations(c.run.id)
    c.resume(second.token, False, by=YOU)
    await _ended(c)

    events = J.ledger(c.run.id)
    bet_on_first, bet_on_second = _questions(c.run.id)
    assert outcomes.measure_from_events(bet_on_first, events) == 1.0, "the first ask was a yes"
    assert outcomes.measure_from_events(bet_on_second, events) == 0.0, "the second was a no"


async def test_a_rewind_that_keeps_the_epoch_withdraws_the_ask_and_asks_a_new_one() -> None:
    """A rewind that does not force keeps the epoch (the memoization tier decides), so the gate's
    re-ask is the other same-epoch ask. 🔴 Red on main: the withdrawn ask was never closed, and its
    re-ask carried its id."""
    c = _controller(_spec(_gate(), _transform("publish")))
    (first,) = await _asking(c)
    body = c.submit_mutation([{"op": "rewind", "node_id": "approve"}], confirm=True)
    assert body.get("queued") is True, body
    await _until(
        lambda: len(_rows(c.run.id, J.CONFIRMATION_PENDING)) == 2
        and len(HI.list_continuations(c.run.id)) == 1,
        what="the rewound gate to ask again",
    )
    (second,) = HI.list_continuations(c.run.id)
    assert second.epoch == first.epoch and second.token != first.token

    (withdrawn,) = _rows(c.run.id, J.CONFIRMATION_RESOLVED)
    assert withdrawn["confirmation_id"] == first.confirmation_id
    assert (withdrawn["verb"], withdrawn["answered"], withdrawn["reason"]) == (
        "withdrawn",
        False,
        "the step was rewound",
    )
    assert second.confirmation_id not in (first.confirmation_id, "")


# ── two steps waiting at once ────────────────────────────────────────────────


async def test_two_steps_waiting_at_once_each_ask_their_own_question() -> None:
    """🔴 Red on main: both gates asked "Ship B?" — the later one's ask, read off the run's one
    `attention` slot — so one of them could not be told apart from the other anywhere."""
    state = _State()
    c = _controller(
        _spec(_gate("ship_a", "Ship A?"), _gate("ship_b", "Ship B?"), kind="parallel"),
        state=state,
    )
    await _asking(c, count=2)

    asked = {cont.node_id: cont.ask.get("prompt") for cont in HI.list_continuations(c.run.id)}
    assert asked == {"ship_a": "Ship A?", "ship_b": "Ship B?"}
    from personalclaw.inbox import OPEN_STATUSES

    titles = {
        item.refs.get("workflow_node"): item.message.split("\n\n")[0]
        for item in state._inbox_svc.inbox.items.values()
        if item.status in OPEN_STATUSES
    }
    assert titles == {"ship_a": "Ship A?", "ship_b": "Ship B?"}
    # Kept on each step, with the run's state, so a question minted after a restart is its own.
    kept = {
        path: inst.ask.get("prompt")
        for path, inst in store.read_state(c.run.id).items()
        if inst.ask
    }
    assert kept == {"root.children[0]": "Ship A?", "root.children[1]": "Ship B?"}


# ── an edit on a paused run survives a restart ───────────────────────────────


async def test_an_edit_queued_on_a_paused_run_is_applied_after_a_restart() -> None:
    """🔴 Red on main: the queued edit was held in the controller's memory only; the restarted
    run resumed without it, ran the step the edit skipped, and said nothing."""
    c = _controller(_spec(_transform("draft"), _transform("publish")))
    store.request_pause(c.run.id)
    await c.start()
    await _until(lambda: c.run.status == RunStatus.PAUSED, what="the run to pause")
    body = c.submit_mutation([{"op": "skip", "node_id": "publish"}], confirm=True)
    assert body.get("queued") is True, body

    # The gateway restarts before anyone resumes the run: nothing of `c` survives but the disk.
    run_id = c.run.id
    store.clear_pause(run_id)
    fresh = _controller(store.read_spec(run_id) or {}, run=store.get(run_id))
    await fresh.start()
    await _ended(fresh)

    assert "publish" not in _started(run_id, control="draft"), "the queued edit was lost"
    assert fresh.instances["root.children[1]"].state == InstanceState.SKIPPED
    assert fresh.run.status == RunStatus.COMPLETE
    # Applied once: the queue on disk is gone once the edit applied.
    assert store.read_pending_mutations(run_id) == []


async def test_a_queued_edit_that_no_longer_applies_is_rejected_not_lost() -> None:
    """The queue a restart finds is prepared again against the spec the run now has. An edit
    written against a step that is gone is journaled rejected — never silently dropped."""
    c = _controller(_spec(_transform("draft")))
    store.write_pending_mutations(
        c.run.id, [{"ops": [{"op": "skip", "node_id": "ghost"}], "actor": "user"}]
    )
    fresh = _restarted(c)
    await fresh.start()
    await _ended(fresh)

    rejected = J.journal_records(fresh.run.id, kinds={J.MUTATION_REJECTED})
    assert len(rejected) == 1 and rejected[0]["actor"] == "user"
    assert rejected[0]["issues"], "a rejection says why"
    assert store.read_pending_mutations(fresh.run.id) == []


# ── a question the run stops asking is closed ────────────────────────────────


async def test_cancelling_a_run_at_its_gate_closes_its_confirmation() -> None:
    """🔴 Red on main: the ask's `confirmation_pending` stayed open for good — nothing wrote its
    resolution, so "how long did this wait" had no end."""
    c = _controller(_spec(_gate(), _transform("publish")))
    (ask,) = await _asking(c)
    store.request_cancel(c.run.id)
    c.wake()
    await _ended(c)

    (closed,) = _rows(c.run.id, J.CONFIRMATION_RESOLVED)
    assert closed["confirmation_id"] == ask.confirmation_id
    assert closed["verb"] == "withdrawn" and closed["approved"] is False
    assert closed["answered"] is False and closed["reason"] == "the run was cancelled"
    # Withdrawn, not answered: it grades no escalation bet as the person's no.
    (bet,) = _questions(c.run.id)
    assert outcomes.measure_from_events(bet, J.ledger(c.run.id)) is None


async def test_an_approval_nobody_gave_closes_its_confirmation_saying_why() -> None:
    clock = _Clock()
    c = _controller(_spec(_gate(), _transform("publish")), clock=clock)
    (ask,) = await _asking(c)
    clock.t += 2 * 3600 + 1  # past the owner's approval window, two hours by default
    c.wake()
    await _ended(c)

    (closed,) = _rows(c.run.id, J.CONFIRMATION_RESOLVED)
    assert closed["confirmation_id"] == ask.confirmation_id
    assert (closed["verb"], closed["reason"]) == ("withdrawn", "no answer within 2 hours")


# ── the answer to a parked step lives in memory; a restart asks again ────────


async def test_a_restart_between_the_answer_and_its_dispatch_asks_again() -> None:
    """The answer rides only the dispatch it starts, and is held in memory until then. A restart
    in between loses it — acceptable because the step then ASKS AGAIN, as a new question, and never
    runs as if it had been answered. The re-ask is the same epoch, so it is also the case above."""
    provider = _SignIn()
    c = _controller(_spec(_action()), provider=provider)
    (first,) = await _asking(c)
    # The answer lands, and the gateway stops before the step it re-runs is dispatched.
    c._resume_loop = lambda: None  # type: ignore[method-assign]
    assert c.resume(first.token, True, by=YOU)["ok"] is True
    assert store.read_state(c.run.id)[STEP].state == InstanceState.PENDING

    fresh = _restarted(c, provider=provider)
    (second,) = await _asking(fresh)

    assert provider.answers == [None, None], "the lost answer was claimed by the re-run"
    assert fresh.run.status == RunStatus.NEEDS_INPUT
    assert second.token != first.token
    assert second.confirmation_id != first.confirmation_id
