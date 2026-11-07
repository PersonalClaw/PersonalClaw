"""A step whose provider comes from its run keeps its secrets as references, as one naming it does.

A workflow step whose provider hands its config to a model (Run Prompt, Invoke Agent, an app's
model turn) or to a run it starts (Run workflow) keeps each ``{{secret:NAME}}`` as the reference
(``node_bindings._secrets_for``), whether the step names its provider or a binding chooses it, and
a secret never chooses it. The dispatch refuses a step whose provider hands its config on when a
secret's value was filled into it, or when the run it starts wasn't handed counted references.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.config import credentials as cred
from personalclaw.config import loader

pytestmark = pytest.mark.security

NAME = "GARDEN_TOKEN"
#: Long, unique, sharing no substring with a key name, so a match can only be the VALUE.
VALUE = "gv-7e1c0b2a-BOUND-PROVIDER-VALUE"
REFERENCE = "{{secret:" + NAME + "}}"

#: The step's provider as a binding: the run's input `which` names it.
BOUND = "{{inputs.which}}"


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An isolated home. These tests write a secret, so never the real one."""
    cfg = tmp_path / "home"
    cfg.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(cfg))
    monkeypatch.setattr(loader, "config_dir", lambda: cfg)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: cfg)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: cfg)
    monkeypatch.delenv(cred.CREDENTIAL_BACKEND_ENV, raising=False)
    # Registered first, so monkeypatch removes whatever a save mirrors in.
    monkeypatch.setenv(NAME, "x")
    monkeypatch.delenv(NAME)
    for key in ("__wf_depth", "__wf_run_id", "__wf_project_id", "__wf_node_id"):
        monkeypatch.delenv(key, raising=False)
    assert loader.env_path() == cfg / ".env", "the .env redirect must hold"
    return cfg


def _store() -> None:
    """The secret as Settings → Secrets keeps it, out of this process's environment."""
    cred.save_credential(NAME, VALUE)
    os.environ.pop(NAME, None)


class _Result:
    success = True
    stdout = '{"ok": true}'
    outcome = ""
    error = ""
    exit_code = 0
    stderr = ""
    agent_error = None
    failure_class = ""
    retry_after = 0.0


class _Recorder:
    """An action provider that keeps the config it is handed."""

    def __init__(self, *, to_a_model: bool = False, to_a_run: bool = False) -> None:
        self.sent: list[dict] = []
        self.hands_config_to_a_model = to_a_model
        self.hands_config_to_a_run = to_a_run

    async def execute(self, cfg, ctx, timeout=30):
        self.sent.append(json.loads(json.dumps(cfg, default=str)))
        return _Result()


class _Info:
    """What ``SubagentManager.spawn`` answers: an agent that ends at its first lookup."""

    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = "done"
        self.reaped = False
        self.agent = ""


class _Subagents:
    """The agent service, recording what each agent is started with."""

    def __init__(self) -> None:
        self.spawned: list[dict] = []
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        self.spawned.append(kw)
        info = _Info(f"sub{len(self.infos) + 1}")
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


def _spec(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "garden-bound",
        "inputs": {"which": {"type": "string", "required": True}},
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "action", "id": "send", "config": step}],
        },
    }


def _controller(
    spec: dict[str, Any], providers: dict[str, Any], *, extra: dict | None = None, **inputs: Any
) -> Any:
    """A run of *spec* with *inputs* (and *extra* on its record), its providers looked up by their
    exact names, as the registry looks them up: a name it doesn't hold, a binding among them, is
    no provider."""
    from personalclaw.workflows import store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    run = store.create(
        WorkflowRun(id="", workflow_name=spec["name"], inputs=inputs, extra=dict(extra or {}))
    )
    store.write_spec(run.id, spec)
    return RunController(run, spec, services=EngineServices(get_provider=providers.get))


async def _run(spec: dict[str, Any], providers: dict[str, Any], **inputs: Any) -> tuple[Any, Any]:
    controller = _controller(spec, providers, **inputs)
    status = await controller.run_to_completion(timeout=20)
    return status, controller


def _failure_of(controller: Any) -> str:
    inst = controller.instances["root.children[0]"]
    failure = inst.failure
    return f"{failure.cause_plain} {failure.remediation}" if failure else ""


def _secret_rows(run_id: str) -> list[dict]:
    from personalclaw.workflows import journal

    return [e for e in journal.ledger(run_id) if e.get("kind") == "secret_read"]


def _holders(home: Path) -> list[str]:
    """Every file under the home that holds the value, but the credential store's own."""
    return sorted(
        str(p)
        for p in home.rglob("*")
        if p.is_file() and p.name != ".env" and VALUE.encode() in p.read_bytes()
    )


# ── an agent step, and a model turn ──────────────────────────────────────────

_AGENT_STEPS = {
    "invoke-agent": {"task_template": f"water the beds with {REFERENCE}"},
    "run-prompt": {"message": f"water the beds with {REFERENCE}"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", sorted(_AGENT_STEPS))
async def test_an_agent_step_whose_provider_comes_from_an_input_is_handed_the_reference(
    home, monkeypatch, provider_name
):
    """🔴 Red before: the agent's task held the secret's value."""
    from personalclaw.action_providers import invoke_agent_provider, run_prompt_provider
    from personalclaw.workflows.models import RunStatus

    module = {"invoke-agent": invoke_agent_provider, "run-prompt": run_prompt_provider}[
        provider_name
    ]
    subagents = _Subagents()
    monkeypatch.setattr(module, "get_action_services", lambda: SimpleNamespace(subagents=subagents))
    _store()
    step = {"provider": BOUND, "with": _AGENT_STEPS[provider_name]}

    status, controller = await _run(
        _spec(step), {provider_name: module.create_provider()}, which=provider_name
    )

    assert status == RunStatus.COMPLETE, _failure_of(controller)
    assert len(subagents.spawned) == 1
    assert REFERENCE in subagents.spawned[0]["task"], "kept as the reference"
    assert VALUE not in json.dumps(subagents.spawned, default=str), "the value never reaches it"
    rows = [(r["name"], r.get("handed_on")) for r in _secret_rows(controller.run.id)]
    assert rows == [(NAME, True)], rows
    assert _holders(home) == []


def _named_by(form: str, step: dict[str, Any], name: str) -> dict[str, Any]:
    """A spec whose step's provider *form* names *name*: a run input, another step's output, or
    a fan-out's item."""
    if form == "an-input":
        return _spec({**step, "provider": BOUND})
    if form == "another-steps-output":
        children = [
            {"kind": "transform", "id": "pick", "config": {"expr": name}},
            {
                "kind": "action",
                "id": "send",
                "config": {**step, "provider": "{{nodes.pick.output}}"},
            },
        ]
    else:
        children = [
            {"kind": "transform", "id": "src", "config": {"expr": [name]}},
            {
                "kind": "foreach",
                "id": "fan",
                "config": {"items": "{{nodes.src.output}}"},
                "body": {
                    "kind": "action",
                    "id": "send",
                    "config": {**step, "provider": "{{item}}"},
                },
            },
        ]
    return {
        "name": "garden-bound",
        "inputs": {"which": {"type": "string", "required": False}},
        "root": {"kind": "sequence", "id": "s", "children": children},
    }


_FORMS = ["an-input", "another-steps-output", "a-fan-outs-item"]


@pytest.mark.asyncio
@pytest.mark.parametrize("form", _FORMS)
async def test_a_model_turn_whose_provider_a_binding_names_is_handed_the_reference(home, form):
    """🔴 Red before: the app's model turn was handed the value."""
    from personalclaw.workflows.models import RunStatus

    _store()
    model = _Recorder(to_a_model=True)
    step = {"with": {"task": f"sync with {REFERENCE}"}}

    status, controller = await _run(
        _named_by(form, step, "an-apps-model"), {"an-apps-model": model}, which="an-apps-model"
    )

    assert status == RunStatus.COMPLETE, controller.run.error_message
    assert model.sent == [{"task": f"sync with {REFERENCE}"}]
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_fan_out_hands_each_items_provider_what_that_provider_is_owed(home, monkeypatch):
    """Its items name two providers through ``{{item.p}}``. The one that uses its config itself gets
    the value; the agent gets the reference, and the run records it once as handed on. 🔴 Red
    before: the agent was handed the value."""
    from personalclaw.action_providers import invoke_agent_provider
    from personalclaw.workflows.models import RunStatus

    subagents = _Subagents()
    monkeypatch.setattr(
        invoke_agent_provider, "get_action_services", lambda: SimpleNamespace(subagents=subagents)
    )
    _store()
    sender = _Recorder()
    spec = {
        "name": "garden-fan",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                {
                    "kind": "transform",
                    "id": "src",
                    "config": {"expr": [{"p": "sends-it"}, {"p": "invoke-agent"}]},
                },
                {
                    "kind": "foreach",
                    "id": "fan",
                    "config": {"items": "{{nodes.src.output}}"},
                    "body": {
                        "kind": "action",
                        "id": "send",
                        "config": {
                            "provider": "{{item.p}}",
                            "with": {"task_template": f"water the beds with {REFERENCE}"},
                        },
                    },
                },
            ],
        },
    }
    providers = {"sends-it": sender, "invoke-agent": invoke_agent_provider.create_provider()}

    status, controller = await _run(spec, providers)

    assert status == RunStatus.COMPLETE, controller.run.error_message
    assert sender.sent == [{"task_template": f"water the beds with {VALUE}"}]
    assert len(subagents.spawned) == 1
    assert REFERENCE in subagents.spawned[0]["task"]
    assert VALUE not in json.dumps(subagents.spawned, default=str)
    handed = [r["name"] for r in _secret_rows(controller.run.id) if r.get("handed_on")]
    assert handed == [NAME], _secret_rows(controller.run.id)


@pytest.mark.asyncio
async def test_a_bound_model_facing_steps_bindings_fill_no_value(home):
    """The step's own context, as its dispatch resolves it: nothing filled, the reference kept.
    🔴 Red before: ``ctx.filled`` held the value."""
    from personalclaw.workflows import engine_support, node_bindings
    from personalclaw.workflows.models import walk
    from personalclaw.workflows.tick import ReadyNode

    _store()
    step = {"provider": BOUND, "with": {"task": f"sync with {REFERENCE}"}}
    controller = _controller(
        _spec(step), {"an-apps-model": _Recorder(to_a_model=True)}, which="an-apps-model"
    )
    path = "root.children[0]"
    node = dict(walk(controller.root))[path]

    ctx = node_bindings.context_for(controller, ReadyNode(path=path, node=node, lane="compute"))
    cfg, failure = engine_support.resolve_config(node, ctx)

    assert failure is None
    assert cfg["provider"] == "an-apps-model"
    assert cfg["with"] == {"task": f"sync with {REFERENCE}"}
    assert ctx.filled == []


# ── a step that runs its config itself ───────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("written", ["sends-it", BOUND], ids=["named", "from-an-input"])
async def test_a_step_that_runs_its_config_itself_gets_the_value_however_its_provider_is_written(
    home, written
):
    """The control: a step whose provider uses its config itself (a shell, a fetch, a notify
    action) is handed the value, whether it names the provider or an input does."""
    from personalclaw.workflows.models import RunStatus

    _store()
    sender = _Recorder()
    step = {"provider": written, "with": {"token": f"Bearer {REFERENCE}"}}

    status, controller = await _run(_spec(step), {"sends-it": sender}, which="sends-it")

    assert status == RunStatus.COMPLETE, _failure_of(controller)
    assert sender.sent == [{"token": f"Bearer {VALUE}"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("written", ["no-such-provider", BOUND], ids=["named", "from-an-input"])
async def test_a_step_whose_provider_resolves_to_none_reads_no_secret(home, written):
    """A provider nothing registers fails the step, and its secrets stay references: nothing is
    read for a step whose provider can't be known. 🔴 Red before: the secret was read."""
    from personalclaw.workflows.models import RunStatus

    _store()
    step = {"provider": written, "with": {"token": f"Bearer {REFERENCE}"}}

    status, controller = await _run(_spec(step), {}, which="no-such-provider")

    assert status == RunStatus.FAILED
    assert "unknown action provider" in _failure_of(controller)
    assert _secret_rows(controller.run.id) == []
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_secret_never_chooses_a_steps_provider(home):
    """A provider written as a secret's reference is refused by what it is, and reads nothing:
    whatever the secret holds would choose where the step's other secrets go. 🔴 Red before: the
    secret named a provider, which was handed the step's secrets."""
    from personalclaw.workflows.models import RunStatus

    _store()
    cred.save_credential("PICKER_NAME", "sends-it")
    os.environ.pop("PICKER_NAME", None)
    sender = _Recorder()
    step = {"provider": "{{secret:PICKER_NAME}}", "with": {"token": f"Bearer {REFERENCE}"}}

    status, controller = await _run(_spec(step), {"sends-it": sender}, which="sends-it")

    assert status == RunStatus.FAILED
    assert sender.sent == [], "nothing is sent"
    assert _secret_rows(controller.run.id) == []
    said = _failure_of(controller)
    assert "a step's provider can't name the secret 'PICKER_NAME'" in said, said
    assert "unknown action provider" not in said
    assert _holders(home) == []


#: Each form a provider reads a secret through itself.
_PIPED = {
    "slugified": "{{secret:PICKER_NAME | slugify}}",
    "defaulted-to-a-sender": "{{secret:PICKER_NAME | slice(0,0) | default('sends-it')}}",
    "defaulted-to-an-agent": "{{secret:PICKER_NAME | slice(0,0) | default('invoke-agent')}}",
    "a-held-input-defaulted-to-a-sender": "{{inputs.which | slice(0,0) | default('sends-it')}}",
    "a-held-input-defaulted-to-an-agent": "{{inputs.which | slice(0,0) | default('invoke-agent')}}",
    "emptied": "{{secret:PICKER_NAME | slice(0,0)}}",
    "a-held-input-emptied": "{{inputs.which | slice(0,0)}}",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("form", sorted(_PIPED))
async def test_a_secret_never_chooses_a_steps_provider_through_a_pipe(home, monkeypatch, form):
    """However the provider reads the secret, through a pipe or a run input that holds one, it is
    refused in the secret's own words and reads nothing. 🔴 Red before: the step ran, or read its
    secrets and failed as having no provider."""
    from personalclaw.action_providers import invoke_agent_provider
    from personalclaw.workflows import input_secrets
    from personalclaw.workflows.models import RunStatus

    subagents = _Subagents()
    monkeypatch.setattr(
        invoke_agent_provider, "get_action_services", lambda: SimpleNamespace(subagents=subagents)
    )
    _store()
    cred.save_credential("PICKER_NAME", "sends-it")
    os.environ.pop("PICKER_NAME", None)
    sender = _Recorder()
    agent = "agent" in form
    task = {"task_template": f"water the beds with {REFERENCE}"}
    step = {"provider": _PIPED[form], "with": task if agent else {"token": f"Bearer {REFERENCE}"}}
    held = {input_secrets.EXTRA_KEY: {"which": ["PICKER_NAME"]}} if "held" in form else {}
    providers = {"sends-it": sender, "invoke-agent": invoke_agent_provider.create_provider()}

    controller = _controller(_spec(step), providers, extra=held, which="{{secret:PICKER_NAME}}")
    status = await controller.run_to_completion(timeout=20)

    assert status == RunStatus.FAILED
    assert sender.sent == [] and subagents.spawned == [], "nothing is sent"
    assert _secret_rows(controller.run.id) == []
    said = _failure_of(controller)
    assert "a step's provider can't name the secret 'PICKER_NAME'" in said, said
    assert _holders(home) == []


@pytest.mark.asyncio
async def test_a_plain_input_emptied_still_names_no_provider(home):
    """The control: a provider read from an input that holds no secret, emptied, is no provider
    at all, as it was."""
    from personalclaw.workflows.models import RunStatus

    sender = _Recorder()
    step = {"provider": "{{inputs.which | slice(0,0)}}", "with": {"token": "plain"}}

    controller = _controller(_spec(step), {"sends-it": sender}, which="sends-it")
    status = await controller.run_to_completion(timeout=20)

    assert status == RunStatus.FAILED
    assert sender.sent == []
    assert "action node has no `provider`" in _failure_of(controller)


def test_a_named_provider_is_looked_up_by_its_name_alone(home, monkeypatch):
    """A provider written by name needs no binding context: nothing about the run is read to
    find it. A control, pinning this change's own lookup."""
    from personalclaw.workflows import node_bindings
    from personalclaw.workflows.models import walk
    from personalclaw.workflows.tick import ReadyNode

    sender = _Recorder()
    controller = _controller(_spec({"provider": "sends-it", "with": {}}), {"sends-it": sender})
    path = "root.children[0]"
    node = dict(walk(controller.root))[path]

    def _no_context(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("a binding context was built for a provider written by name")

    monkeypatch.setattr(node_bindings, "item_context", _no_context)
    item = ReadyNode(path=path, node=node, lane="compute")
    assert node_bindings._step_provider(controller, item, {}) is sender


# ── the dispatch ─────────────────────────────────────────────────────────────


def _node(provider: str = "hands-on") -> Any:
    from personalclaw.workflows.models import Node, NodeKind

    return Node(
        kind=NodeKind.ACTION,
        id="send",
        config={"provider": provider, "with": {"task": f"use {REFERENCE}"}},
    )


def _filling(key: str) -> str:
    """A resolver that fills a secret in, as one for a step that runs its config itself does."""
    return VALUE


def _keeping(key: str) -> str:
    """A resolver that keeps the reference and counts nothing."""
    return "{{secret:" + key + "}}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hands", "resolver"),
    [
        ({"to_a_model": True}, _filling),
        ({"to_a_run": True}, _filling),
        ({"to_a_run": True}, _keeping),
    ],
    ids=["a-model-filled", "a-run-filled", "a-run-uncounted"],
)
async def test_the_dispatch_refuses_a_step_whose_provider_would_hand_on_what_it_wasnt_given(
    hands, resolver
):
    """Whatever chose the step's resolver, the provider that hands its config on is never handed a
    filled value, nor a run's inputs whose references nothing counted. 🔴 Red before: the provider
    was dispatched."""
    from personalclaw.workflows import engine
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.models import REFUSED_TO_START, FailureClass, InstanceState

    provider = _Recorder(**hands)
    ctx = BindingContext(secret_resolver=resolver)

    result = await engine.dispatch_action(_node(), ctx, get_provider={"hands-on": provider}.get)

    assert result.state == InstanceState.FAILED
    assert result.failure.failure_class == FailureClass.PERMISSION
    assert result.failure.terminal_reason == REFUSED_TO_START
    assert provider.sent == [], "the provider is never dispatched"
    assert VALUE not in f"{result.failure.cause_plain} {result.failure.remediation}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hands", "resolver", "sent"),
    [
        ({"to_a_model": True}, "keeping", f"use {REFERENCE}"),
        ({"to_a_run": True}, "handed", f"use {REFERENCE}"),
        ({}, "filling", f"use {VALUE}"),
    ],
    ids=["a-model-kept", "a-run-counted", "runs-it-itself-filled"],
)
async def test_the_dispatch_hands_on_what_its_step_was_given_for_it(hands, resolver, sent):
    """The controls: a model is handed references kept for it, a run its counted references, and a
    provider that uses its config itself the value."""
    from personalclaw.workflows import engine, input_secrets
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.models import InstanceState

    resolvers = {"keeping": _keeping, "handed": input_secrets.Handed(), "filling": _filling}
    provider = _Recorder(**hands)
    ctx = BindingContext(secret_resolver=resolvers[resolver])

    result = await engine.dispatch_action(_node(), ctx, get_provider={"hands-on": provider}.get)

    assert result.state == InstanceState.DONE, result.failure
    assert provider.sent == [{"task": sent}]


@pytest.mark.asyncio
async def test_a_run_handed_no_reference_is_started_whatever_resolved_the_step(home):
    """What a run's inputs keep is counted only when they keep a reference: a step that hands a
    run no secret is dispatched, whichever resolver its bindings had. A control."""
    from personalclaw.workflows import engine
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.models import InstanceState, Node, NodeKind

    provider = _Recorder(to_a_run=True)
    node = Node(
        kind=NodeKind.ACTION, id="send", config={"provider": "hands-on", "with": {"task": "plain"}}
    )
    ctx = BindingContext(secret_resolver=_keeping)

    result = await engine.dispatch_action(node, ctx, get_provider={"hands-on": provider}.get)

    assert result.state == InstanceState.DONE, result.failure
    assert provider.sent == [{"task": "plain"}]


@pytest.mark.asyncio
async def test_the_refusal_says_to_run_the_workflow_again(home):
    """The refusal's remedy: run it again, and write a bound provider by name. 🔴 Red before: the
    provider was dispatched."""
    from personalclaw.workflows import engine
    from personalclaw.workflows.bindings import BindingContext

    provider = _Recorder(to_a_model=True)
    ctx = BindingContext(secret_resolver=_filling)

    result = await engine.dispatch_action(_node(), ctx, get_provider={"hands-on": provider}.get)

    assert result.failure.remediation == (
        "run the workflow again; if its provider is a binding, write it by name"
    )
