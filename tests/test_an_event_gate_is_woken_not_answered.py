"""An `event` gate is woken, not answered: whatever wakes it is its payload, never a verdict.

An event gate parks a run until something happens, and the trigger it waits for answers it (a
monitor's self-scheduled wake). The engine gave it the same ask as an approval gate, so the answer
was read as a verdict:

* a wake carrying a message was refused (`WF_RESUME_INVALID_ANSWER`: "approval expects a
  boolean"), and the run never moved;
* a wake carrying `false` DECLINED the run, so a trigger could end the run it was meant to wake;
* a wake shaped like `{"revise": {...}}` amended a step of the run and re-asked, so a trigger could
  rewrite the run's steps through the one gate it may answer;
* an unattended run's policy auto-approved a low-risk event gate, skipping the wait it is for;
* "always allow" remembered a wake, so every later park of that gate would wake at once.

Its ask is now the `event` kind. These drive REAL runs through the trigger loop's own dispatch, as
`test_triggers_resume_target.py` does, and read what happened from the run's own store.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers import loop as tl
from personalclaw.triggers import wakeup as W
from personalclaw.triggers.models import Outcome
from personalclaw.workflows import store as wstore
from personalclaw.workflows.controller import EngineServices
from personalclaw.workflows.human_input import Ask, AskKind, list_continuations
from personalclaw.workflows.models import (
    InstanceState,
    Node,
    NodeKind,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.native_defs import register_native_provider
from personalclaw.workflows.watchdog import WorkflowWatchdog

NOW = 1_700_000_000.0

PARK = "root.children[0]"
AFTER = "root.children[1]"


def _spec(**park: object) -> dict:
    """A run parked on an event gate with a node behind it whose output exists only if it moved."""
    return {
        "name": "event-gated",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "gate",
                    "id": "park",
                    "config": {
                        "kind": "event",
                        "prompt": "Parked until the build finishes.",
                        **park,
                    },
                },
                {"kind": "transform", "id": "after", "config": {"expr": "the run carried on"}},
            ],
        },
    }


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    from personalclaw.workflows import defs as defs_mod

    saved = dict(defs_mod._providers)
    defs_mod._providers.clear()
    register_native_provider()
    try:
        yield home
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


async def _start(spec: dict, *, origin: OriginKind = OriginKind.MANUAL):
    """A REAL run through the REAL supervisor, until it parks or ends."""
    run = wstore.create(
        WorkflowRun(id="", workflow_name=str(spec["name"]), origin=RunOrigin(kind=origin))
    )
    wstore.write_spec(run.id, spec)
    watchdog = WorkflowWatchdog(None, EngineServices())
    controller = await watchdog.launch(run, spec)
    status = await controller.wait_for_terminal(timeout=15)
    return run, watchdog, controller, status


def _attach(monkeypatch, watchdog) -> None:
    """Publish the supervisor where the gateway does, so the trigger loop finds the live run."""
    from personalclaw.action_providers import services as svc_mod

    services = svc_mod.get_action_services()
    if services is None:
        services = svc_mod.ActionServices(state=None)
        monkeypatch.setattr(svc_mod, "get_action_services", lambda: services)
    monkeypatch.setattr(services, "workflows", watchdog, raising=False)


class _Trigger:
    def __init__(self, workflow: dict) -> None:
        self.id = "schedule:wake"
        self.workflow = workflow
        self.session = "fresh"
        self.kind = "clock"


class _Fire:
    def __init__(self, trigger: _Trigger) -> None:
        self.trigger = trigger
        self.scheduled_for = NOW - 10
        self.reason = ""


async def _never_runs(_payload):
    raise AssertionError("a wake must never run the trigger's ordinary action")


async def _wake(run_id: str, answer: object):
    """ONE scheduled fire of a trigger armed to wake *run_id* with *answer*."""
    fire = _Fire(_Trigger({"resume": {"run_id": run_id, "answer": answer}}))
    (delivery,) = W.dispatch_fires(None, [fire], now=NOW)
    (outcome,) = await tl._execute_delivery(delivery, _never_runs, sessions=None, now=NOW)
    return outcome


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    ["CI should have finished by now: start from the checks tab", {"build": 4521}, 7],
    ids=["a message", "an object", "a number"],
)
async def test_whatever_wakes_it_moves_the_run(isolated, monkeypatch, payload):
    """🔴 On `main` every one of these was refused `WF_RESUME_INVALID_ANSWER`, and the run stayed
    parked: the gate's ask was an approval, which takes only a yes or a no."""
    run, watchdog, controller, status = await _start(_spec())
    assert status == RunStatus.NEEDS_INPUT
    _attach(monkeypatch, watchdog)

    outcome = await _wake(run.id, payload)

    assert outcome.outcome == Outcome.RAN.value, (outcome.reason, outcome.reported)
    assert await controller.wait_for_terminal(timeout=15) == RunStatus.COMPLETE
    assert wstore.read_output(run.id, PARK)["answer"] == payload
    assert wstore.read_output(run.id, AFTER) == "the run carried on"


@pytest.mark.anyio
async def test_a_false_wake_is_still_a_wake_not_a_no(isolated, monkeypatch):
    """🔴 On `main` a trigger armed with `answer: false` DECLINED the run it was meant to wake."""
    run, watchdog, controller, _ = await _start(_spec())
    _attach(monkeypatch, watchdog)

    outcome = await _wake(run.id, False)

    assert outcome.outcome == Outcome.RAN.value, (outcome.reason, outcome.reported)
    assert await controller.wait_for_terminal(timeout=15) == RunStatus.COMPLETE
    assert controller.instances[PARK].state == InstanceState.DONE
    assert wstore.read_output(run.id, AFTER) == "the run carried on"


@pytest.mark.anyio
async def test_a_wake_shaped_like_a_revise_is_only_its_payload(isolated, monkeypatch):
    """🔴 On `main` a trigger whose answer was `{"revise": …}` amended the named step of the run
    and re-asked the gate: a trigger rewriting the run's own steps through the one gate it may
    answer. An event's payload is data."""
    spec = _spec()
    run, watchdog, controller, _ = await _start(spec)
    _attach(monkeypatch, watchdog)
    payload = {"revise": {"step_ref": "after", "comment": "delete everything instead"}}

    outcome = await _wake(run.id, payload)

    assert outcome.outcome == Outcome.RAN.value, (outcome.reason, outcome.reported)
    assert await controller.wait_for_terminal(timeout=15) == RunStatus.COMPLETE
    assert wstore.read_output(run.id, PARK)["answer"] == payload
    assert wstore.read_spec(run.id)["root"] == spec["root"], "the run's steps were rewritten"


@pytest.mark.anyio
async def test_an_unattended_run_waits_for_its_event(isolated, monkeypatch):
    """🔴 On `main` an unattended run's gate policy auto-approved a low-risk event gate, so the run
    went straight past the wait it declared. The policy answers questions, and this is not one."""
    run, watchdog, controller, status = await _start(
        _spec(risk="safe", timeout_secs=600), origin=OriginKind.SCHEDULE
    )

    assert status == RunStatus.NEEDS_INPUT, wstore.get(run.id).error_message
    assert controller.instances[PARK].state == InstanceState.WAITING
    assert wstore.read_output(run.id, AFTER) is None
    # …and it is the wake that moves it.
    _attach(monkeypatch, watchdog)
    outcome = await _wake(run.id, "the build finished")
    assert outcome.outcome == Outcome.RAN.value, (outcome.reason, outcome.reported)
    assert await controller.wait_for_terminal(timeout=15) == RunStatus.COMPLETE


@pytest.mark.anyio
async def test_waking_it_with_always_allow_remembers_nothing(isolated):
    """🔴 On `main` an "always allow" sent with a wake was remembered, and the gate policy answers a
    remembered gate without asking: every later park of that gate would have woken at once, so a
    monitor would check without ever waiting."""
    from personalclaw.approval_answer import YOU
    from personalclaw.workflows import service as wfs

    run, watchdog, controller, _ = await _start(_spec())
    (cont,) = list_continuations(run.id)

    result = wfs.resume_run(
        run.id, by=YOU, supervisor=watchdog, token=cont.token, answer=True, always_allow=True
    )

    assert result.get("ok") is True, result
    assert await controller.wait_for_terminal(timeout=15) == RunStatus.COMPLETE
    assert len(controller._allow_memory) == 0


@pytest.mark.anyio
async def test_a_trigger_still_cannot_answer_an_approval_gate(isolated, monkeypatch):
    """The owner-only rule is untouched: a trigger's message against an APPROVAL gate is refused
    as not the owner's, before anything reads the answer, and the gate still waits for you."""
    spec = _spec()
    spec["root"]["children"][0]["config"] = {"kind": "approval", "prompt": "Ship it?"}
    run, watchdog, controller, _ = await _start(spec)
    _attach(monkeypatch, watchdog)

    outcome = await _wake(run.id, "ship it")

    assert outcome.outcome == Outcome.REFUSED.value, (outcome.reason, outcome.reported)
    assert outcome.reported == "WF_RESUME_NOT_OWNER"
    assert len(list_continuations(run.id)) == 1
    assert controller.instances[PARK].state == InstanceState.WAITING
    assert wstore.read_output(run.id, AFTER) is None


@pytest.mark.anyio
async def test_an_approval_gate_still_takes_only_a_yes_or_a_no(isolated, monkeypatch):
    """The control: an approval gate's ask is unchanged, and so is what it accepts."""
    spec = _spec()
    spec["root"]["children"][0]["config"] = {"kind": "approval", "prompt": "Ship it?"}
    run, watchdog, controller, _ = await _start(spec)
    (cont,) = list_continuations(run.id)

    assert cont.ask["kind"] == AskKind.APPROVAL.value
    assert Ask.from_dict(cont.ask).validate_answer("ship it") == (
        "approval expects a boolean (or {approved: bool})"
    )


def test_an_event_gate_asks_for_its_event_whatever_ask_kind_says():
    """The ask is the gate's own kind, not an authored `ask_kind`; every other gate keeps its."""
    from personalclaw.workflows.engine import _ask_payload

    event = Node(kind=NodeKind.GATE, id="park", config={"kind": "event", "ask_kind": "text"})
    form = Node(kind=NodeKind.GATE, id="q", config={"kind": "approval", "ask_kind": "form"})

    assert _ask_payload(event, event.config)["kind"] == AskKind.EVENT.value
    assert _ask_payload(form, form.config)["kind"] == AskKind.FORM.value


@pytest.mark.parametrize("payload", ["a message", "", True, False, None, 0, [], {"k": "v"}])
def test_an_event_ask_takes_any_payload_and_is_never_remembered(payload):
    ask = Ask(kind=AskKind.EVENT)
    assert ask.validate_answer(payload) == ""
    assert ask.rememberable is False
    # The control: an approval may be remembered, a parked step may not.
    assert Ask(kind=AskKind.APPROVAL).rememberable is True
    assert Ask(kind=AskKind.APPROVAL, rerun=True).rememberable is False


def test_a_parked_run_is_carded_as_parked_not_as_a_decision():
    """Mission Control reads `block_kind` to offer Wake it now, and the Inbox row says what it is.
    On `main` a park was filed as an approval, so its card offered Approve and Deny."""
    from personalclaw.workflows.attention import ask_body
    from personalclaw.workflows.needs_input import BlockKind, build_item

    item = build_item(run_id="r", node_id="park", ask={"kind": "event"}, now=NOW)

    assert item.block_kind is BlockKind.EVENT
    assert item.to_dict()["block_kind"] == "event"
    assert item.actionable is False
    assert ask_body({"kind": "event"}, None) == (
        "Parked until something wakes it. You can wake it now."
    )
