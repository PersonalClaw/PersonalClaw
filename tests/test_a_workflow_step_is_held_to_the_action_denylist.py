"""A workflow's action step asks the action denylist before its provider runs, as a fire does.

The action denylist (`guardrails.denylist.enforce_action`) refuses unattended work a credential
path, the operator's denied paths, a command the shell denylist refuses and, above all here, a
command that would stop, restart, update or reinstall the PersonalClaw gateway running it
(`guardrails.self_destruct`). A trigger's fire, a hook, a tile's refresh and the triage digest's
auto-execution ask it, and so does every workflow step (`engine.dispatch_action`): a run lives in
the gateway it runs on, so a step that stopped it would leave its own run in flight with nothing
left to end it.

What these tests hold the step to:

* a step that would stop or update PersonalClaw never reaches its provider, fails `permission`, and
  says why in the words a trigger's fire records for the same command: the rule's code and its
  sentence, one composition (`DenyDecision.refusal`), so the two cannot drift apart;
* the run it belongs to ends saying that, not "action failed";
* the refusal is in the security log;
* who started the run does not matter: a run is unattended work, so a run started by hand is held
  to it too, and so is a provider lookup handed to the engine from outside (`EngineServices.
  get_provider`), which is why the check is in the dispatch and not in the lookup;
* an ordinary step still runs (the real `bash` provider, through the real registry), and the
  same command from a trigger is still refused there.

No test here lets a refused command near a real shell: a refusal is proven against a recording
provider, so a missing check records a call instead of running the command.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from personalclaw.action_providers.base import ActionResult
from personalclaw.workflows import store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import dispatch_action
from personalclaw.workflows.models import (
    FailureClass,
    InstanceState,
    Node,
    NodeKind,
    OriginKind,
    RunOrigin,
    RunStatus,
    WorkflowRun,
)

STOP = "personalclaw stop"
UPDATE = "personalclaw update"
ORDINARY = "echo nightly backup done"

#: What the refusal of each command says it would do, in the sentence a trigger's fire records.
WOULD = {
    STOP: "it would stop the PersonalClaw gateway that is executing it",
    UPDATE: "it would update the PersonalClaw gateway that is executing it",
}


class _Recorder:
    """Stands in for a provider, so "did the step reach it?" is a list, never a command run."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, action_config, ctx, timeout=30):
        self.calls.append(dict(action_config))
        return ActionResult(success=True, stdout="done")


@pytest.fixture
def recorder(monkeypatch) -> _Recorder:
    """The provider every name resolves to in the registry the engine looks a step's provider up
    in, when nothing is handed to it."""
    rec = _Recorder()
    monkeypatch.setattr(
        "personalclaw.action_providers.registry.get_action_provider", lambda name: rec
    )
    return rec


def _step(command: str, *, step_id: str = "step") -> Node:
    return Node(
        kind=NodeKind.ACTION,
        id=step_id,
        config={"provider": "bash", "with": {"command": command}},
    )


def _run(*, origin: RunOrigin | None = None, spec: dict | None = None) -> WorkflowRun:
    spec = spec or {"name": "chores", "root": {"kind": "sequence", "id": "s", "children": []}}
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=spec["name"],
            origin=origin or RunOrigin(kind=OriginKind.MANUAL),
        )
    )
    store.write_spec(run.id, spec)
    return run


def _dispatch(command: str, *, run_id: str = "", get_provider=None):
    return asyncio.run(
        dispatch_action(_step(command), BindingContext(), get_provider=get_provider, run_id=run_id)
    )


@pytest.mark.parametrize("command", [STOP, UPDATE], ids=["stop", "update"])
def test_a_step_that_would_stop_or_update_personalclaw_never_reaches_its_provider(
    recorder, command
):
    result = _dispatch(command, run_id=_run().id)

    assert recorder.calls == [], f"the step's provider was handed {command!r}"
    assert result.state is InstanceState.FAILED
    assert result.failure.failure_class is FailureClass.PERMISSION
    assert not result.failure.retryable, "a retry is refused the same way"
    cause = result.failure.cause_plain
    assert cause.startswith("blocked by the guardrails denylist: self_destruct:"), cause
    assert WOULD[command] in cause, cause
    assert f"`{command}`" in cause, "the refusal names the command it refused"
    assert result.failure.remediation, "the step says what to do about it"


def test_an_ordinary_step_still_runs():
    """The positive control, end to end: the real `bash` provider, through the registry the
    gateway fills, so the check is shown to pass an ordinary command and not merely to exist."""
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    result = _dispatch(ORDINARY, run_id=_run().id)

    assert result.state is InstanceState.DONE, result.failure
    assert "nightly backup done" in result.output["stdout"], result.output


def test_a_provider_lookup_handed_to_the_engine_cannot_skip_it():
    """The gateway hands the engine its provider lookup (`EngineServices.get_provider`), and any
    caller can hand another. A check living in the lookup would be skipped by a replaced one; it
    lives in the dispatch, so this lookup's provider is refused too."""
    rec = _Recorder()

    result = _dispatch(STOP, run_id=_run().id, get_provider=lambda name: rec)

    assert rec.calls == []
    assert result.failure.failure_class is FailureClass.PERMISSION


@pytest.mark.parametrize(
    "origin",
    [
        RunOrigin(kind=OriginKind.MANUAL),
        RunOrigin(kind=OriginKind.CHAT, session_key="main"),
        RunOrigin(kind=OriginKind.SCHEDULE, trigger_id="clock:nightly"),
    ],
    ids=["by-hand", "from-a-chat", "from-a-trigger"],
)
def test_who_started_the_run_does_not_matter(recorder, origin):
    """A run is unattended work whoever pressed Run: each step is dispatched with nobody approving
    it, and the run lives in the gateway the step would stop. So a run started by hand or from a
    chat is held to it as a trigger's run is."""
    result = _dispatch(STOP, run_id=_run(origin=origin).id)

    assert recorder.calls == []
    assert result.failure.failure_class is FailureClass.PERMISSION


def test_the_run_ends_saying_why():
    """Driven through the controller as the gateway drives a run: the run ends failed, and its
    ending names the refusal rather than "action failed"."""
    spec = {
        "name": "shutdown-chore",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "action",
                    "id": "stop",
                    "config": {"provider": "bash", "with": {"command": STOP}},
                }
            ],
        },
    }
    run = _run(spec=spec)
    rec = _Recorder()
    controller = RunController(run, spec, services=EngineServices(get_provider=lambda name: rec))

    status = asyncio.run(controller.run_to_completion(timeout=20))

    assert rec.calls == [], "the step's provider was handed the command"
    assert status == RunStatus.FAILED
    ending = controller.run.error_message
    assert "action failed" not in ending
    assert "self_destruct:stop" in ending, ending
    assert WOULD[STOP] in ending, ending


def test_the_refusal_is_in_the_security_log(recorder):
    from personalclaw.sel import sel

    _dispatch(STOP, run_id=_run().id)

    rows = [e for e in sel().recent(50) if e.get("operation") == "guardrails.denylist"]
    assert rows, "the refusal left no row in the security log"
    row = rows[0]
    assert row["caller_identity"] == "action:bash"
    assert row["outcome"] == "blocked"
    assert "self_destruct:stop" in row["resources"], row
    assert STOP in row["metadata"]["command"], row


# ── the control: the trigger's fire is refused as it was, in the same words ─────────────────────


def _fire_a_trigger(command: str, monkeypatch) -> _Recorder:
    """A granted clock trigger's fire through the gateway's one store dispatch, as
    `test_guardrails_self_destruct` drives it."""
    import personalclaw.action_providers as AP
    from personalclaw.gateway import GatewayOrchestrator

    rec = _Recorder()
    monkeypatch.setattr(AP, "get_action_provider", lambda name: rec)
    trigger = types.SimpleNamespace(
        id="clock:nightly",
        kind="clock",
        workflow={"inline": {"provider": "bash", "config": {"command": command}}},
        capabilities={"providers": ["bash"]},
    )
    asyncio.run(
        object.__new__(GatewayOrchestrator)._fire_store_trigger(trigger, {"trigger_id": trigger.id})
    )
    return rec


def test_the_same_command_from_a_trigger_is_refused_in_the_same_words(recorder, monkeypatch):
    from personalclaw.config.loader import config_dir
    from personalclaw.schedule_history import ScheduleRunStore

    fired = _fire_a_trigger(STOP, monkeypatch)
    rows, _total = asyncio.run(ScheduleRunStore(config_dir()).list_for_job("clock:nightly"))
    step = _dispatch(STOP, run_id=_run().id)

    assert fired.calls == [], "the trigger's fire reached its provider"
    assert [row["status"] for row in rows] == ["skipped_gate"]
    assert recorder.calls == [], "the workflow step reached its provider"
    assert (
        step.failure.cause_plain == rows[0]["error"]
    ), "a step and a trigger's fire refused by the same rule say different things"
