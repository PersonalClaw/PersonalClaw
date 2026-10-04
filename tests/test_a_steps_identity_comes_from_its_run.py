"""A workflow step's identity comes from the run that executes it, and only from that run.

The engine stamps an action step's payload with whose work the step is: the run's id, the step's
node id and instance path, the run's project and working folder, and the attempt's idempotency
key. Providers read them to attribute and confine what they do: `artifact_inspect` reads only the
run's own `artifacts/`, `knowledge-persist` files what it writes under the run's project, and a
ledger row lands in the step's own slice of the run.

🔴 Red before: the engine filled them in with `setdefault`, so a value already in the template's
own payload won, and a step whose payload named another run read that run's artifacts. Now:

* the engine sets every one of them from the run, after the step's payload is built, so neither a
  value the template wrote nor one a binding resolved stands in for the run's own;
* a template that writes one is refused where it is validated (saving it) and at run start, before
  any step runs, with a sentence naming the key;
* every other payload key is the step's own input and reaches its provider as written;
* an agent a step starts, and the audit of a handoff a step asks for, are not made another
  session's by a value in the step's payload, nor is a triage digest step another automation's,
  and a step's question is the question of the step that asked it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.action_providers.artifact_inspect_provider import ArtifactInspectActionProvider
from personalclaw.action_providers.base import ActionResult
from personalclaw.workflows import service, store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import dispatch_action
from personalclaw.workflows.journal import MAX_INLINE_OUTPUT_BYTES, Journal
from personalclaw.workflows.models import (
    InstanceState,
    Node,
    NodeKind,
    RunStatus,
    WorkflowRun,
    walk,
)
from personalclaw.workflows.validator import validate_spec

pytestmark = pytest.mark.anyio

#: The step's identity as a template might write it, naming somewhere else for every key.
ELSEWHERE: dict[str, str] = {
    "run_id": "run-elsewhere",
    "node_id": "elsewhere",
    "instance_path": "root.children[9]",
    "project_id": "project-elsewhere",
    "workspace": "/srv/elsewhere",
    "idempotency_key": "key-elsewhere",
}

#: The run the step belongs to, as the controller hands it to the dispatch.
OWN_RUN = {
    "run_id": "run-own",
    "instance_path": "root.children[0]",
    "project_id": "project-own",
    "cwd": "/srv/own",
    "idempotency_key": "key-own",
}

#: What the step's provider must be told, whatever the template wrote.
OWN_IDENTITY: dict[str, str] = {
    "run_id": "run-own",
    "node_id": "fetch",
    "instance_path": "root.children[0]",
    "project_id": "project-own",
    "workspace": "/srv/own",
    "idempotency_key": "key-own",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


def _action(provider: str, args: dict[str, Any], *, payload: Any, node_id: str = "fetch") -> Node:
    return Node.from_dict(
        {
            "kind": "action",
            "id": node_id,
            "config": {"provider": provider, "with": args, "payload": payload},
        }
    )


class _Recorder:
    """A provider that keeps the payload it was handed and changes nothing."""

    def __init__(self, answer: str = "{}") -> None:
        self.payloads: list[dict[str, Any]] = []
        self.answer = answer

    def getter(self, name: str) -> Any:
        return self

    async def execute(self, action_config: dict[str, Any], ctx: Any, timeout: int = 30) -> Any:
        self.payloads.append(dict(ctx.payload))
        return ActionResult(success=True, stdout=self.answer)


def _offloaded(workflow: str, path: str, body: str) -> tuple[str, str]:
    """A run whose step at *path* left *body* offloaded to its `artifacts/`: (run id, ref)."""
    run = store.create(WorkflowRun(id="", workflow_name=workflow))
    ref, _preview = Journal(run.id).store_output(path, body)
    assert ref.startswith("artifacts/"), "the fixture must produce an offloaded body"
    return run.id, ref


# ── the step acts only on its own run ───────────────────────────────────────


async def test_a_step_whose_payload_names_another_run_reads_none_of_that_runs_artifacts() -> None:
    """🔴 Red before: the template's `run_id` won, so the step read the other run's artifact."""
    pad = "x" * (MAX_INLINE_OUTPUT_BYTES + 10)
    other_run, other_ref = _offloaded("other", "root.children[3]", "OTHER RUN " + pad)
    own_run, _own_ref = _offloaded("own", "root.children[0]", "OWN RUN " + pad)

    result = await dispatch_action(
        _action("artifact_inspect", {"ref": other_ref}, payload={"run_id": other_run}),
        BindingContext(),
        get_provider=lambda name: ArtifactInspectActionProvider(),
        run_id=own_run,
        instance_path="root.children[1]",
    )

    assert result.state == InstanceState.FAILED
    assert "OTHER RUN" not in json.dumps(result.output, default=str)
    assert "not a readable artifact of this run" in (result.failure.cause_plain or "")


async def test_the_same_step_still_reads_its_own_runs_artifact() -> None:
    """CONTROL: the provider still works on the run the step belongs to."""
    pad = "x" * (MAX_INLINE_OUTPUT_BYTES + 10)
    own_run, own_ref = _offloaded("own", "root.children[0]", "OWN RUN " + pad)

    result = await dispatch_action(
        _action("artifact_inspect", {"ref": own_ref}, payload={}),
        BindingContext(),
        get_provider=lambda name: ArtifactInspectActionProvider(),
        run_id=own_run,
        instance_path="root.children[1]",
    )

    assert result.state == InstanceState.DONE
    assert result.output["content"].startswith("OWN RUN ")


@pytest.mark.parametrize("key", sorted(ELSEWHERE))
async def test_no_identity_key_a_template_writes_stands_in_for_the_runs_own(key: str) -> None:
    """🔴 Red before, for every key: the template's value reached the provider."""
    seen = _Recorder()

    await dispatch_action(
        _action("probe", {}, payload={key: ELSEWHERE[key]}),
        BindingContext(),
        get_provider=seen.getter,
        **OWN_RUN,
    )

    assert seen.payloads[0][key] == OWN_IDENTITY[key]


async def test_a_payload_bound_from_another_steps_output_cannot_name_the_run_either() -> None:
    """🔴 Red before: a payload bound whole from a step's output carried that output's ids."""
    seen = _Recorder()
    carried = {**ELSEWHERE, "label": "nightly"}

    await dispatch_action(
        _action("probe", {}, payload="{{nodes.prepare.output}}"),
        BindingContext(node_outputs={"prepare": carried}),
        get_provider=seen.getter,
        **OWN_RUN,
    )

    assert {k: seen.payloads[0][k] for k in OWN_IDENTITY} == OWN_IDENTITY
    assert seen.payloads[0]["label"] == "nightly"


async def test_a_step_of_a_run_with_no_project_names_no_project_whatever_its_template_says() -> (
    None
):
    """🔴 Red before: a run with no project left the template's `project_id` in place."""
    seen = _Recorder()

    await dispatch_action(
        _action("probe", {}, payload={"project_id": "project-elsewhere"}),
        BindingContext(),
        get_provider=seen.getter,
        run_id="run-own",
        instance_path="root.children[0]",
    )

    assert "project_id" not in seen.payloads[0]
    assert seen.payloads[0]["run_id"] == "run-own"


async def test_a_steps_own_inputs_reach_its_provider_as_written() -> None:
    """CONTROL: what is not the step's identity is the step's input, and passes unchanged."""
    seen = _Recorder()
    inputs = {"PC_SANDBOX": "/srv/sandbox", "label": "nightly", "count": 3, "items": ["a", "b"]}

    await dispatch_action(
        _action("probe", {}, payload=dict(inputs)),
        BindingContext(),
        get_provider=seen.getter,
        **OWN_RUN,
    )

    assert {k: seen.payloads[0][k] for k in inputs} == inputs
    assert {k: seen.payloads[0][k] for k in OWN_IDENTITY} == OWN_IDENTITY
    assert set(seen.payloads[0]) == set(inputs) | set(OWN_IDENTITY)


def test_the_engine_names_exactly_the_keys_it_stamps() -> None:
    """The one list the engine and the validator both read is the list of what it stamps."""
    from personalclaw.workflows.engine import RUN_IDENTITY_KEYS

    assert set(RUN_IDENTITY_KEYS) == set(OWN_IDENTITY)


# ── a template that writes one is refused, saying which ─────────────────────


def _spec(payload: dict[str, Any], *, name: str = "identity-probe") -> dict[str, Any]:
    return {
        "name": name,
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "action",
                    "id": "fetch",
                    "config": {"provider": "probe", "with": {}, "payload": payload},
                }
            ],
        },
    }


@pytest.mark.parametrize("key", sorted(ELSEWHERE))
def test_saving_a_template_whose_step_writes_an_identity_key_is_refused_naming_it(
    key: str,
) -> None:
    """🔴 Red before: the template validated, and its value was used at every run."""
    result = validate_spec(_spec({key: ELSEWHERE[key], "label": "nightly"}))

    refusals = [issue for issue in result.errors if issue.code == "WF_PAYLOAD_RUN_IDENTITY"]
    assert len(refusals) == 1, [issue.to_dict() for issue in result.issues]
    assert f"`{key}`" in refusals[0].message
    assert "the run that executes the step" in refusals[0].message
    assert refusals[0].path == "root.children[0]"


async def test_the_save_door_answers_with_the_refusal() -> None:
    """🔴 Red before: the definition saved."""
    result = await service.author_def(
        name="identity-probe", root=_spec({"run_id": "run-elsewhere"})["root"], save=False
    )

    assert result["ok"] is False
    assert result["code"] == "WF_DEF_INVALID"
    refusals = [issue for issue in result["issues"] if issue["code"] == "WF_PAYLOAD_RUN_IDENTITY"]
    assert len(refusals) == 1
    assert "`run_id`" in refusals[0]["message"]


def test_a_template_whose_step_payload_carries_only_its_own_inputs_validates() -> None:
    """CONTROL: an ordinary payload is not refused."""
    result = validate_spec(_spec({"PC_SANDBOX": "/srv/sandbox", "label": "nightly"}))

    assert result.ok, [issue.to_dict() for issue in result.issues]
    assert not [issue for issue in result.issues if issue.code == "WF_PAYLOAD_RUN_IDENTITY"]


async def test_a_run_of_such_a_template_is_refused_before_any_step_runs(caplog) -> None:
    """🔴 Red before: the run started and its step was handed the other run's id. A definition
    can reach a run without passing the save door (one an app contributes, one saved before this
    rule, one edited on disk), so the run's start asks the same question, and says it in the
    run's record and in the gateway's log."""
    seen = _Recorder()
    spec = _spec({"run_id": "run-elsewhere"})
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(get_provider=seen.getter))

    with caplog.at_level("WARNING", logger="personalclaw.workflows.run_start"):
        status = await controller.run_to_completion(timeout=30)

    assert status == RunStatus.FAILED
    assert seen.payloads == []
    assert controller.run.error_message.startswith("The run did not start: its step “fetch” sets")
    assert "`run_id`" in controller.run.error_message
    assert "the run that executes the step" in controller.run.error_message
    logged = [r.getMessage() for r in caplog.records if run.id in r.getMessage()]
    assert logged and controller.run.error_message in logged[0]


# ── every bundled template still loads and runs its first step ─────────────


def _inputs_for(spec: dict[str, Any]) -> dict[str, Any]:
    """The inputs a start would give the run: one of its type for each required input, and every
    other one its declared default (`service.with_declared_defaults`, as `start_run` applies)."""
    by_type = {"number": 1, "integer": 1, "boolean": False, "array": ["alpha"], "object": {}}
    required = {
        key: by_type.get(str(param.get("type", "string")), "example")
        for key, param in (spec.get("inputs") or {}).items()
        if isinstance(param, dict) and param.get("required")
    }
    return service.with_declared_defaults(spec, required)


#: The kinds of step that call a service: what the services below stand in for, and what every
#: bundled template's first step is.
_STEP_KINDS = {NodeKind.ACTION, NodeKind.INFER, NodeKind.STAGE}


class _FirstStep:
    """Every service a template's first step can call. It keeps what it was asked and asks for the
    run to be cancelled before it answers, so nothing after the first step runs, and no model, agent
    or command is ever reached."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _saw(self, kind: str, **what: Any) -> None:
        self.calls.append((kind, what))
        store.request_cancel(self.run_id, reason="the first step was reached")

    def get_provider(self, name: str) -> Any:
        first = self

        class _Provider:
            async def execute(self, action_config: dict, ctx: Any, timeout: int = 30) -> Any:
                first._saw("action", provider=name, payload=dict(ctx.payload))
                return ActionResult(success=True, stdout="{}")

        return _Provider()

    async def completion(self, prompt: str, *, output_type: Any = None, **_kw: Any) -> str:
        self._saw("infer", prompt=prompt)
        return "{}" if output_type is dict else "noted"

    def spawn(self, **kw: Any) -> None:
        self._saw("stage", task=kw.get("task", ""))
        return None  # at capacity, so the step stays ready and the cancel ends the run


@pytest.mark.parametrize("name", template_names())
async def test_every_bundled_template_loads_and_runs_its_first_step(name: str) -> None:
    """CONTROL: no shipped template writes a step's identity, so each one loads, validates, gets
    past its run's start and dispatches its first step, and an action step among them is told the
    run's own identity."""
    loaded = read_template(name)
    assert loaded is not None, f"{name} did not load"
    spec = loaded.to_dict()
    result = validate_spec(spec, strict=True)
    assert result.ok, [issue.to_dict() for issue in result.issues]

    run = store.create(WorkflowRun(id="", workflow_name=name, inputs=_inputs_for(spec)))
    store.write_spec(run.id, spec)
    first = _FirstStep(run.id)
    controller = RunController(
        run,
        spec,
        services=EngineServices(
            get_provider=first.get_provider,
            completion=first.completion,
            subagents=SimpleNamespace(spawn=first.spawn),
        ),
    )

    status = await controller.run_to_completion(timeout=60)

    assert first.calls, f"{name} ended {status.value} before its first step ran: " + str(
        controller.run.error_message
    )
    assert status == RunStatus.CANCELLED, controller.run.error_message
    path, step = next((p, n) for p, n in walk(controller.root) if n.kind in _STEP_KINDS)
    kind, what = first.calls[0]
    assert kind == step.kind.value, f"{name}: its first step is {path}, a {step.kind.value}"
    if kind == "action":
        assert {k: what["payload"][k] for k in ("run_id", "node_id", "instance_path")} == {
            "run_id": run.id,
            "node_id": step.id,
            "instance_path": path,
        }


# ── the family: an agent a step starts, and the question a step asks ────────


def _spawn_sink(spawned: dict[str, Any]) -> SimpleNamespace:
    def _spawn(**kw: Any) -> Any:
        spawned.update(kw)
        return SimpleNamespace(id="c0ffee01", done=False, error="")

    return SimpleNamespace(subagents=SimpleNamespace(spawn=_spawn))


async def test_an_agent_a_step_starts_is_not_made_the_work_of_a_session_its_payload_names(
    monkeypatch,
) -> None:
    """🔴 Red before: `invoke-agent` started its agent as the named chat's work (that chat's
    Trust approving its calls, its results posted into that chat)."""
    import personalclaw.action_providers.invoke_agent_provider as invoke_agent

    spawned: dict[str, Any] = {}
    monkeypatch.setattr(invoke_agent, "get_action_services", lambda: _spawn_sink(spawned))

    result = await dispatch_action(
        _action(
            "invoke-agent",
            {"task_template": "Summarise the notes"},
            payload={"session_key": "chat-1-a1b2c3"},
        ),
        BindingContext(),
        get_provider=lambda name: invoke_agent.InvokeAgentActionProvider(),
        run_id="run-own",
        instance_path="root.children[0]",
    )

    assert result.state != InstanceState.FAILED, result.failure
    assert spawned.get("task", "").startswith("Summarise the notes")
    assert not spawned.get("parent_session_key")


async def test_a_saved_prompt_a_step_runs_is_not_made_the_work_of_a_session_its_payload_names(
    monkeypatch,
) -> None:
    """🔴 Red before: `run-prompt` fell back to the payload's `session_key` for its agent's
    session when its config pinned none."""
    import personalclaw.action_providers.run_prompt_provider as run_prompt

    spawned: dict[str, Any] = {}
    monkeypatch.setattr(run_prompt, "render_saved_prompt", lambda prompt_id, values: "the body")
    monkeypatch.setattr(run_prompt, "get_action_services", lambda: _spawn_sink(spawned))

    await dispatch_action(
        _action("run-prompt", {"prompt_id": "standup"}, payload={"session_key": "chat-1-a1b2c3"}),
        BindingContext(),
        get_provider=lambda name: run_prompt.RunPromptActionProvider(),
        run_id="run-own",
        instance_path="root.children[0]",
    )

    assert "the body" in spawned.get("task", "")
    assert not spawned.get("parent_session_key")


async def test_a_handoff_a_step_asks_for_is_not_audited_as_a_session_its_payload_names(
    monkeypatch,
) -> None:
    """🔴 Red before: `second-opinion` fell back to the payload's `session_key`, so its audit rows
    named whatever session the step's payload said. They name the step's run."""
    import personalclaw.proposer.service as proposer
    from personalclaw.action_providers.second_opinion_provider import (
        SecondOpinionActionProvider,
    )

    asked: dict[str, Any] = {}

    async def _handoff(**kw: Any) -> Any:
        asked.update(kw)
        return SimpleNamespace(accepted=True, rejection="", to_dict=lambda: {"accepted": True})

    monkeypatch.setattr(proposer, "run_second_opinion", _handoff)

    await dispatch_action(
        _action(
            "second-opinion",
            {"goal": "green suite", "stuck_at": "one failing test", "origin_runner": "runner-a"},
            payload={"session_key": "chat-1-a1b2c3"},
        ),
        BindingContext(),
        get_provider=lambda name: SecondOpinionActionProvider(),
        run_id="run-own",
        instance_path="root.children[0]",
        cwd="/srv/own",
    )

    assert asked["goal"] == "green suite"
    assert asked["session_key"] == "unattended:workflow:run-own"


async def test_a_triage_digest_step_is_no_automation_its_payload_names(monkeypatch) -> None:
    """🔴 Red before: the digest took `trigger_id` from the payload, so a step's payload named the
    automation its auto-actions were judged and audited as, and the one its digest linked to."""
    import personalclaw.action_providers.triage_digest_provider as triage
    import personalclaw.guardrails.policy as policy
    import personalclaw.proactive.pipeline as pipeline
    from personalclaw.action_providers.triage_digest_provider import TriageDigestActionProvider

    judged_as: list[str] = []
    real_key = policy.unattended_dispatch_key
    monkeypatch.setattr(
        policy, "unattended_dispatch_key", lambda key: judged_as.append(key) or real_key(key)
    )
    monkeypatch.setattr(triage, "_proactive_config", lambda: SimpleNamespace(triage_enabled=True))
    triaged: dict[str, Any] = {}

    class _Stop(Exception):
        """Raised once the digest has said whose it is: the rest of its pipeline is not this
        test's business."""

    async def _run_triage(items: Any, **kw: Any) -> Any:
        triaged.update(kw)
        raise _Stop

    monkeypatch.setattr(pipeline, "run_triage", _run_triage)

    await dispatch_action(
        _action("triage-digest", {}, payload={"trigger_id": "trigger-elsewhere"}),
        BindingContext(),
        get_provider=lambda name: TriageDigestActionProvider(),
        run_id="run-own",
        instance_path="root.children[0]",
    )

    assert triaged["run_id"] == "run-own"
    assert triaged["trigger_id"] == ""
    assert "trigger:run-own" in judged_as
    assert not [key for key in judged_as if "trigger-elsewhere" in key]


async def test_a_steps_question_is_the_question_of_the_step_that_asked_it() -> None:
    """🔴 Red before: an action's answer naming another step made the run's question that step's."""
    asking = _Recorder(
        answer=json.dumps({"needs_input": {"node_id": "elsewhere", "prompt": "Which folder?"}})
    )
    spec = {
        "name": "asks",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {"kind": "action", "id": "ask", "config": {"provider": "probe", "with": {}}},
            ],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(get_provider=asking.getter))
    await controller.start()

    status = await controller.wait_for_terminal(timeout=30)

    assert status == RunStatus.NEEDS_INPUT
    assert controller.run.attention["node_id"] == "ask"
    assert controller.instances["root.children[0]"].ask["node_id"] == "ask"
    store.request_cancel(run.id)
    await controller.run_to_completion(timeout=30)
