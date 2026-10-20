"""A workflow an agent working for an allowed automation's run starts is a version that automation
was allowed.

An automation's Allow records the version of its workflow, and of each workflow it runs as a step,
and the run a fire starts carries them, so each of its steps starts the version allowed with it.
An agent one of those steps starts (an "Invoke agent" or "Run prompt" step) can start a workflow
too, with ``workflow_start``, and that started the workflow as it is: a version an agent saved,
which the owner never allowed, ran under her yes to the automation.

So an agent a step of such a run starts records the run it works for, and a workflow it starts —
a subagent of its own included, through its tool in the gateway or an agent CLI's route — is held
to what that run may start: the version allowed with the automation, or a newer one the owner saved
herself, and the run it starts carries the same versions on to its own steps. A workflow the Allow
did not cover is refused in words, as a step that starts one is: nobody is asked in the middle of an
unattended run, and the agent says why. A draft run it would start is no version the owner allowed,
and is refused the same way.

Driven through the doors: the ``run-workflow`` fire that starts the allowed run, an "Invoke agent"
and a "Run prompt" step's spawn, the service every start takes, the agent's ``workflow_start`` tool
and the route an agent CLI's tool server calls, and the draft start.
"""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw import mcp_core, mcp_workflows
from personalclaw.action_providers.base import WORKFLOW_STEP_EVENT, ActionContext
from personalclaw.action_providers.invoke_agent_provider import InvokeAgentActionProvider
from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider
from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider
from personalclaw.config import loader as config_loader
from personalclaw.stale_write import revision_of
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore
from personalclaw.workflows import automation_version
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import handlers as H
from personalclaw.workflows import service, store
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.native_defs import NativeWorkflowDefProvider

pytestmark = pytest.mark.anyio

NAME = "weekly-report"
PART = "report-part"
OTHER = "monthly-report"
TRIGGER_ID = "manual:weekly-report"
AGENT = "subagent:a1"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Agents:
    """The gateway's running agents, as its state holds them (``state.subagents``)."""

    def __init__(self) -> None:
        self.by_id: dict[str, Any] = {}

    def get(self, agent_id: str) -> Any:
        return self.by_id.get(agent_id)

    def add(self, key: str, *, workflow_run: str = "", parent: str = "") -> None:
        self.by_id[key.removeprefix("subagent:")] = SimpleNamespace(
            id=key.removeprefix("subagent:"),
            workflow_run=workflow_run,
            parent_session_key=parent,
            trigger_id="",
            app="",
        )


class _Engine:
    """The workflow supervisor, recording what each launch was handed instead of running it, in
    the gateway whose state holds its agents."""

    event_loop = None

    def __init__(self) -> None:
        self.launched: list[tuple[Any, dict[str, Any]]] = []
        self.agents = _Agents()
        self.state = SimpleNamespace(
            subagents=self.agents, workflows=self, push_refresh=lambda *_a: None
        )

    async def launch(self, run: Any, spec: dict[str, Any], **_kw: Any) -> None:
        self.launched.append((run, copy.deepcopy(spec)))


@pytest.fixture
def engine(monkeypatch) -> _Engine:
    engine = _Engine()
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(workflows=engine, state=engine.state, subagents=engine.agents),
    )
    return engine


@pytest.fixture(autouse=True)
def _native_store():
    before = defs_mod.get_provider("native")
    defs_mod.register_provider(NativeWorkflowDefProvider())
    yield
    defs_mod.unregister_provider("native")
    if before is not None:
        defs_mod.register_provider(before)


def _root(said: str) -> dict[str, Any]:
    """A workflow whose one step says *said*: what tells one version from another."""
    return {
        "kind": "sequence",
        "id": "main",
        "children": [{"kind": "transform", "id": "tail", "config": {"expr": said}}],
    }


def _runs_the_part(said: str) -> dict[str, Any]:
    return {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "head", "config": {"expr": said}},
            {"kind": "subworkflow", "id": "part", "config": {"ref": PART, "inputs": {}}},
        ],
    }


def _request(
    method: str,
    path: str,
    body: dict[str, Any],
    *,
    state: Any,
    match: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> web.Request:
    app = web.Application()
    app["state"] = state
    request = make_mocked_request(
        method, path, headers=headers or {}, app=app, match_info=match or {}
    )
    request["user"] = "owner"

    async def _json() -> dict[str, Any]:
        return body

    request.json = _json  # type: ignore[method-assign]
    return request


def _reply(response: web.StreamResponse) -> dict[str, Any]:
    return json.loads(response.body.decode())  # type: ignore[attr-defined]


async def _owner_saves(name: str, root: dict[str, Any]) -> None:
    """Save *name* from its editor: the owner's door, naming the revision it edited."""
    claim: dict[str, str] = {}
    stored = await service.get_def(name)
    if stored.get("ok"):
        claim["If-Match"] = revision_of(stored["definition"])
    body = {"name": name, "root": root}
    saved = _reply(
        await H.api_def_save(
            _request("POST", "/api/workflows", body, headers=claim, state=SimpleNamespace())
        )
    )
    assert saved.get("saved"), saved


def _agent_saves(name: str, root: dict[str, Any]) -> None:
    """Save *name* with the agent's own tool, ``workflow_author``."""
    from personalclaw.mcp_workflows import _call_tool

    answer = _call_tool("workflow_author", {"name": name, "root": json.dumps(root)})
    assert '"saved": true' in str(answer), answer


def _allowed_automation() -> None:
    """A "Run workflow" automation for the workflow, allowed for it and the part it runs as a
    step, as they are now: the grant the owner's Allow records."""
    now = automation_version.current_now(NAME)
    assert now is not None
    automation_version.keep(now)
    calls = automation_version.closure_now(now.spec, root=NAME)
    allowed = automation_version.Allowed(NAME, now.version, now.digest, calls)
    TriggerStore(base_dir=config_loader.config_dir()).upsert(
        Trigger(
            id=TRIGGER_ID,
            name="Weekly report",
            kind="manual",
            created_by="user",
            workflow={"inline": {"provider": "run-workflow", "config": {"workflow": NAME}}},
            capabilities={"providers": ["run-workflow"], "workflow": allowed.to_dict()},
        )
    )


async def _allowed_run(engine: _Engine) -> str:
    """The run a fire of the allowed automation starts: the owner's versions, then an agent saves
    newer ones of both workflows, and of a third the Allow never covered."""
    await _owner_saves(PART, _root("the owner's part"))
    await _owner_saves(NAME, _runs_the_part("the owner's report"))
    await _owner_saves(OTHER, _root("the owner's monthly report"))
    _allowed_automation()
    fired = await RunWorkflowActionProvider().execute(
        {"workflow": NAME}, ActionContext(event="manual", trigger_id=TRIGGER_ID)
    )
    assert fired.success, fired.error
    run, _spec = engine.launched[-1]
    assert automation_version.bound_in(store.get(run.id)) is not None
    _agent_saves(PART, _root("the agent's part"))
    _agent_saves(NAME, _runs_the_part("the agent's report"))
    _agent_saves(OTHER, _root("the agent's monthly report"))
    return str(run.id)


def _said(spec: dict[str, Any]) -> str:
    return spec["root"]["children"][0]["config"]["expr"]


# ── the agent's start, at the service every start takes ──────────────────────────────────────


async def test_an_agent_working_for_an_allowed_run_starts_the_version_allowed(engine):
    """🔴 Before: it started the part as it is, the agent's version, under the owner's yes."""
    run_id = await _allowed_run(engine)
    engine.agents.add(AGENT, workflow_run=run_id)

    started = await service.start_run(name=PART, session_key=AGENT, supervisor=engine)

    assert started.get("ok"), started
    run, spec = engine.launched[-1]
    assert run.id == started["run_id"]
    assert (run.spec_version, _said(spec)) == (1, "the owner's part")
    # The run it starts holds its own steps to the same versions.
    assert automation_version.bound_in(store.get(run.id)) is not None


async def test_the_automations_own_workflow_starts_allowed_and_holds_its_steps(engine):
    run_id = await _allowed_run(engine)
    engine.agents.add(AGENT, workflow_run=run_id)

    started = await service.start_run(name=NAME, session_key=AGENT, supervisor=engine)

    assert started.get("ok"), started
    run, spec = engine.launched[-1]
    assert (run.spec_version, _said(spec)) == (1, "the owner's report")
    bound = automation_version.bound_in(store.get(run.id))
    assert bound is not None and bound[PART].version == 1


async def test_a_workflow_the_allow_did_not_cover_is_refused_in_words(engine):
    """🔴 Before: it started, as it is, a workflow the owner's Allow never named."""
    run_id = await _allowed_run(engine)
    engine.agents.add(AGENT, workflow_run=run_id)
    launched = len(engine.launched)

    refused = await service.start_run(name=OTHER, session_key=AGENT, supervisor=engine)

    assert refused.get("ok") is False and refused["code"] == "WF_RUN_NOT_ALLOWED", refused
    assert f"was not allowed to run “{OTHER}”" in refused["message"], refused
    assert f"Start “{OTHER}” yourself" in refused["message"], refused
    assert len(engine.launched) == launched
    assert not [r for r in store.all_runs() if r.workflow_name == OTHER]


async def test_a_subagent_of_that_agent_is_held_the_same_way(engine):
    run_id = await _allowed_run(engine)
    engine.agents.add(AGENT, workflow_run=run_id)
    engine.agents.add("subagent:a2", parent=AGENT)

    started = await service.start_run(name=PART, session_key="subagent:a2", supervisor=engine)
    refused = await service.start_run(name=OTHER, session_key="subagent:a2", supervisor=engine)

    assert started.get("ok"), started
    assert _said(engine.launched[-1][1]) == "the owner's part"
    assert refused["code"] == "WF_RUN_NOT_ALLOWED", refused


async def test_an_agent_whose_run_cannot_be_read_starts_nothing(engine):
    """What binds it cannot be read, so which workflows it may start cannot be told."""
    await _owner_saves(OTHER, _root("the owner's monthly report"))
    engine.agents.add(AGENT, workflow_run="run-that-is-gone")

    refused = await service.start_run(name=OTHER, session_key=AGENT, supervisor=engine)

    assert refused["code"] == "WF_RUN_NOT_ALLOWED", refused
    assert engine.launched == []


# ── the controls: work no allowed run binds starts the workflow as it is ─────────────────────


async def test_a_chats_agent_starts_the_workflow_as_it_is(engine):
    await _allowed_run(engine)

    started = await service.start_run(name=PART, session_key="dashboard:chat-1", supervisor=engine)

    assert started.get("ok"), started
    assert _said(engine.launched[-1][1]) == "the agent's part"


async def test_an_agent_of_a_run_nobody_allowed_starts_the_workflow_as_it_is(engine):
    await _allowed_run(engine)
    by_hand = store.create(WorkflowRun(id="", workflow_name=NAME, status=RunStatus.RUNNING))
    engine.agents.add(AGENT, workflow_run=by_hand.id)

    started = await service.start_run(name=OTHER, session_key=AGENT, supervisor=engine)

    assert started.get("ok"), started
    assert _said(engine.launched[-1][1]) == "the agent's monthly report"


# ── the doors: the agent's tool, an agent CLI's route, a draft start ─────────────────────────


async def test_the_agents_workflow_start_tool_is_held_the_same_way(engine):
    run_id = await _allowed_run(engine)
    engine.agents.add(AGENT, workflow_run=run_id)

    def _call() -> str:
        token = mcp_core.set_current_session_key(AGENT)
        try:
            return str(mcp_workflows._call_tool("workflow_start", {"name": OTHER}))
        finally:
            mcp_core.reset_current_session_key(token)

    answer = await asyncio.to_thread(_call)

    assert "WF_RUN_NOT_ALLOWED" in answer and f"“{OTHER}”" in answer, answer
    assert not [r for r in store.all_runs() if r.workflow_name == OTHER]


async def test_an_agent_clis_start_on_the_gateway_route_is_held_the_same_way(engine):
    run_id = await _allowed_run(engine)
    engine.agents.add(AGENT, workflow_run=run_id)
    agent_cli = {"X-Internal-Secret": "the-gateway-s-own", "X-Session-Key": AGENT}

    refused = await H.api_run_start(
        _request(
            "POST", "/api/workflows/runs", {"name": OTHER}, state=engine.state, headers=agent_cli
        )
    )
    started = await H.api_run_start(
        _request(
            "POST", "/api/workflows/runs", {"name": PART}, state=engine.state, headers=agent_cli
        )
    )

    assert refused.status == 403, _reply(refused)
    assert _reply(refused)["error"]["service_code"] == "WF_RUN_NOT_ALLOWED"
    assert started.status == 202, _reply(started)
    assert _said(engine.launched[-1][1]) == "the owner's part"


async def test_a_draft_that_agent_would_start_is_refused(engine):
    run_id = await _allowed_run(engine)
    engine.agents.add(AGENT, workflow_run=run_id)
    forked = service.fork_run(run_id)
    assert forked.get("ok"), forked
    draft = forked["child_run_id"]
    launched = len(engine.launched)
    agent_cli = {"X-Internal-Secret": "the-gateway-s-own", "X-Session-Key": AGENT}

    refused = await H.api_run_start_draft(
        _request(
            "POST",
            f"/api/workflows/runs/{draft}/start",
            {},
            state=engine.state,
            match={"run_id": draft},
            headers=agent_cli,
        )
    )

    assert refused.status == 403, _reply(refused)
    assert _reply(refused)["error"]["service_code"] == "WF_RUN_NOT_ALLOWED"
    assert len(engine.launched) == launched
    assert store.get(draft).status == RunStatus.DRAFT


# ── an agent a step of a run starts records the run it works for ─────────────────────────────


class _Spawner:
    """The gateway's agent manager, recording what each spawn was handed."""

    def __init__(self) -> None:
        self.spawned: list[dict[str, Any]] = []

    def spawn(self, **kwargs: Any) -> Any:
        self.spawned.append(kwargs)
        return SimpleNamespace(id=f"a{len(self.spawned)}", done=False, error="")


@pytest.fixture
def spawner(monkeypatch) -> _Spawner:
    spawner = _Spawner()
    monkeypatch.setattr(
        "personalclaw.action_providers.invoke_agent_provider.get_action_services",
        lambda: SimpleNamespace(subagents=spawner),
    )
    monkeypatch.setattr(
        "personalclaw.action_providers.run_prompt_provider.get_action_services",
        lambda: SimpleNamespace(subagents=spawner),
    )
    return spawner


def _step(run_id: str) -> ActionContext:
    """A step of the run *run_id*, as the engine dispatches one."""
    return ActionContext(event=WORKFLOW_STEP_EVENT, payload={"run_id": run_id, "node_id": "ask"})


@pytest.mark.parametrize(
    "provider, config",
    [
        (InvokeAgentActionProvider, {"task_template": "Summarise the week."}),
        (RunPromptActionProvider, {"message": "Summarise the week."}),
    ],
)
async def test_an_agent_a_step_starts_records_the_run_it_works_for(spawner, provider, config):
    """🔴 Before: the agent recorded nothing of the run, so nothing could hold what it started."""
    stepped = await provider().execute(config, _step("run-1"))
    fired = await provider().execute(config, ActionContext(event="manual", trigger_id=TRIGGER_ID))

    assert stepped.success and fired.success, (stepped.error, fired.error)
    assert spawner.spawned[0]["workflow_run"] == "run-1"
    # An automation's own agent works for no run.
    assert spawner.spawned[1].get("workflow_run", "") == ""
