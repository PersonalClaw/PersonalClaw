"""The work an app's agent starts is the app's work: held to its tier, and asking you for its calls.

An app's agent at the ``tools`` tier (a scheduled job's, say, as Research Lab's is) can start more
agents with your tools: one subagent, a batch of them through ``subagent_run`` (Research Lab starts
one every cycle), or a workflow run whose steps are agents. A batch was not held as the app's work:
its start honoured your YOLO, a chat's Trust and the hook setting; its tasks started without the
app's name, so YOLO approved their calls and the app's tier, its memory grant and its audit did not
apply to them; and its ask did not name the app. Now each is the app's work, as the agent that
started it is:

* a batch starts on the app's install consent, never on a grant of yours, and each of its tasks
  carries the app's name and runs at no more than its tier: none of your switches approves their
  calls, each call that needs approval asks you, and every ask names the app and the scheduled job
  that started the work;
* a batch from an app whose tier is ``text``, or that holds none now, starts nothing, and says why;
* a single subagent the app's agent starts runs at the app's tier, and a workflow run it starts is
  recorded as the app's work, so its steps are held the same way, after a restart too, as is a run
  started from one of its runs; an action step of such a run that would start an agent of its own
  is refused.

Driven through the real ``subagent_run`` tool over HTTP into the real workflow and approval routes,
the real batch start, the engine's own stage dispatch, and a real subagent manager over
PersonalClaw's own runtime on a scripted model. Imports of names this change adds sit inside the
tests, so on a tree without them each test fails on its own.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from test_a_batch_that_may_change_things_asks_you_once import (  # noqa: F401 - fixtures, by name
    FIND,
    FIX,
    _approval_rows,
    _asks,
    _run_tool,
    _until,
    gateway,
)
from test_an_apps_agent_is_held_to_its_tier import _Model, _Notes
from test_apps_cannot_run_code_or_bypass_approvals import _home
from test_starting_subagents_asks_you_once import READ_TESTS, relay  # noqa: F401 - a fixture

from personalclaw import approval_grants, mcp_core, mcp_subagents
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.subagent import SubagentInfo
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition
from personalclaw.workflows import batch_start, store
from personalclaw.workflows.models import InstanceState, Node, NodeKind, walk

APP = "probe-garden"
NAMED = "Probe Garden"
CRON = "water-rota"
JOB = f"app:{APP}:{CRON}"
#: The app's own agent: its scheduled job's, started at the app's tier (``app_crons.start_job``).
AGENT_ID = "jobagent1"
AGENT_KEY = f"subagent:{AGENT_ID}"


def _install(home: Path, permissions: dict, *, enabled: bool = True) -> None:
    """The app as an install leaves it on disk."""
    appdir = home / "apps" / APP
    appdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": NAMED,
        "description": "Keeps the allotment's watering rota.",
        "permissions": permissions,
        "crons": [{"name": CRON, "cron_expr": "0 7 * * 1", "message": "Water the plots."}],
    }
    (appdir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (appdir / "installed.json").write_text(
        json.dumps({"name": APP, "version": "1.0.0", "enabled": enabled}), encoding="utf-8"
    )


def _tiered(tier: str) -> dict:
    return {"cron": True, "agent": tier} if tier else {"cron": True}


@pytest_asyncio.fixture
async def app_batch(relay, tmp_path, monkeypatch):  # noqa: F811 - the fixture imported above
    """The gateway's real subagent manager and routes, with the app installed at the ``tools``
    tier and its scheduled job's agent running: ``subagent_run`` is called as that agent. Your
    YOLO is on, and so is the hook setting that starts every subagent without asking."""
    from personalclaw import trust_mode

    apps_home = tmp_path / "apps-home"
    _install(apps_home, _tiered("tools"))
    relay.manager._agents[AGENT_ID] = SubagentInfo(
        id=AGENT_ID,
        task="Water the plots.",
        parent_session_key=f"app:{APP}",
        app=APP,
        trigger_id=JOB,
    )
    for module in (mcp_core, mcp_subagents):
        monkeypatch.setattr(module, "_resolve_session_key", lambda: AGENT_KEY)
    relay.manager._ctx_builder.hooks.auto_approve_subagent_spawn = True
    with _home(apps_home):
        relay.state.enable_yolo()
        try:
            yield SimpleNamespace(**vars(relay), apps_home=apps_home)
        finally:
            trust_mode.disable_yolo()


def _launched_run(batch: Any) -> tuple[Any, dict]:
    (launched,) = batch.supervisor.launched
    run, spec = launched
    stored = store.get(run.id)
    assert stored is not None
    return stored, spec


def _stages(spec: dict) -> list[tuple[str, Node]]:
    return [(p, n) for p, n in walk(Node.from_dict(spec["root"])) if n.kind is NodeKind.STAGE]


class _Recorder:
    """The subagent manager as a step's dispatch sees it: what each start asked for."""

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []

    def spawn(self, **kwargs: Any) -> Any:
        self.started.append(kwargs)
        return SimpleNamespace(id=f"task-{len(self.started)}", error="")


async def _dispatch(run_id: str, spec: dict, subagents: Any, tmp_path: Path) -> list[Any]:
    """Each step of the run *run_id* dispatched as its controller dispatches it."""
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch_stage

    with patch("personalclaw.workflows.leases.config_dir", lambda: tmp_path):
        return [
            await dispatch_stage(
                node, BindingContext(), subagents=subagents, run_id=run_id, instance_path=path
            )
            for path, node in _stages(spec)
        ]


# ── 1. the batch's start ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_your_yolo_does_not_start_an_apps_batch_its_install_consent_does(app_batch):
    """🔴 Before: it started on your YOLO (``decided_by: yolo``), a grant of yours for your own
    agents, and nothing recorded that the run was the app's."""
    out = await _run_tool(FIND, READ_TESTS)

    run, _spec = _launched_run(app_batch)
    consent = run.extra[batch_start.CONSENT_KEY]
    assert consent["by"] == approval_grants.APP, consent
    assert _asks(app_batch.state) == []
    assert '"run_id"' in out, out
    from personalclaw.apps import app_work

    assert app_work.of_run(run) == app_work.AppWork(APP, JOB), run.extra


@pytest.mark.asyncio
async def test_each_task_of_an_apps_batch_carries_the_apps_name_and_its_tier(app_batch, tmp_path):
    """🔴 Before: each task started with no app's name, as your own subagent, so nothing held it."""
    await _run_tool(FIND, READ_TESTS)
    run, spec = _launched_run(app_batch)

    recorder = _Recorder()
    results = await _dispatch(run.id, spec, recorder, tmp_path)

    assert [r.state for r in results] == [InstanceState.RUNNING] * 2
    assert len(recorder.started) == 2
    for started in recorder.started:
        assert started["app"] == APP, started
        assert started["capability_class"] == "research", started
        assert not started.get("approval_mode"), "an app's work approves none of its own calls"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tier", "enabled", "why"),
    [
        ("text", True, "may use no tools"),
        ("", True, "does not declare the 'agent' permission"),
        ("tools", False, "is disabled"),
    ],
    ids=["text tier", "no tier", "switched off"],
)
async def test_a_batch_from_an_app_that_may_use_no_tools_starts_nothing_and_says_why(
    app_batch, tier, enabled, why
):
    """🔴 Before: it started (on your YOLO), or asked you to start it, whatever the app may do."""
    _install(app_batch.apps_home, _tiered(tier), enabled=enabled)

    out = await _run_tool(FIND, READ_TESTS)

    assert app_batch.supervisor.launched == [] and app_batch.provider.saved == {}
    assert _asks(app_batch.state) == [], "a batch the app may not start was put to you"
    assert "the batch did not start" in out and why in out, out
    assert NAMED in out and "none of its 2 tasks started" in out, out


@pytest.mark.asyncio
@pytest.mark.parametrize(("tier", "enabled"), [("text", True), ("tools", False)])
async def test_a_step_of_an_apps_run_starts_nothing_once_the_app_may_use_no_tools(
    app_batch, tmp_path, tier, enabled
):
    """The tier is the one the app holds when the step starts: a step that waited (its turn in the
    lane, a retry, a restart) starts nothing once the app is narrowed or switched off."""
    await _run_tool(FIND, READ_TESTS)
    run, spec = _launched_run(app_batch)
    _install(app_batch.apps_home, _tiered(tier), enabled=enabled)

    recorder = _Recorder()
    results = await _dispatch(run.id, spec, recorder, tmp_path)

    assert recorder.started == [], "a step of the app's run started an agent it may not"
    for result in results:
        assert result.state is InstanceState.FAILED
        assert NAMED in result.failure.cause_plain and "started no agent" in (
            result.failure.cause_plain
        ), result.failure.cause_plain


@pytest.mark.asyncio
async def test_a_batch_that_may_change_things_asks_you_and_the_ask_names_the_app_and_its_job(
    app_batch,
):
    """It still waits for your own Allow, and YOLO does not answer it; what is new is that the
    ask says whose work it is. 🔴 Before: "A batch of 2 subagent tasks is waiting…", from no one."""
    await _run_tool(FIX, FIND)

    (ask,) = _asks(app_batch.state)
    assert app_batch.supervisor.launched == []
    assert app_batch.state.answered_alone(ask["id"])
    whose = f"the app “{NAMED}”'s scheduled job “{CRON}”"
    assert whose in ask["tool_purpose"], ask["tool_purpose"]
    assert ask["source_label"] == f"batch of {whose}", ask["source_label"]
    (row,) = _approval_rows(app_batch.inbox)
    assert f"A batch of 2 subagent tasks from {whose} is waiting" in row.message, row.message


@pytest.mark.asyncio
async def test_at_the_read_tier_a_batch_that_may_change_things_is_refused(app_batch):
    _install(app_batch.apps_home, _tiered("read"))

    out = await _run_tool(FIX, FIND)

    assert _asks(app_batch.state) == [] and app_batch.supervisor.launched == []
    assert "the batch did not start" in out and "Raise the retry ceiling" in out, out
    assert "may only read" in out, out


# ── 2. the tasks' calls ────────────────────────────────────────────────────────────────────


class _Shell(_Notes):
    """Her notes, and the shell: a command that only reads is within a read-only task's tier, and
    still asks before it runs (a tool that declares it only reads asks nobody)."""

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            *await super().list_tools(),
            ToolDefinition(
                name="bash",
                description="Run a shell command.",
                parameters={"type": "object", "properties": {"command": {"type": "string"}}},
                requires_approval=True,
                risk_level=RiskLevel.DESTRUCTIVE,
            ),
        ]


class _Script(_Model):
    """A scripted model whose first answer makes *calls*, each a tool and its arguments."""

    def __init__(self, *calls: tuple[str, dict]) -> None:
        super().__init__()
        self._script = calls

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        from personalclaw.llm.events import (
            EVENT_COMPLETE,
            EVENT_TEXT_CHUNK,
            EVENT_TOOL_CALL,
            AgentEvent,
        )

        self.offered.append(
            sorted(str((t.get("function", t)).get("name", "")) for t in tools or [])
        )
        if len(self.offered) == 1:
            for i, (name, args) in enumerate(self._script):
                yield AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id=f"c{i}",
                    title=name,
                    tool_input=json.dumps(args),
                )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AgentEvent(kind=EVENT_COMPLETE)


async def _run_task(
    run_id: str, spec: dict, path: str, model: _Model, tmp_path: Path
) -> tuple[Any, _Shell, list[str]]:
    """One step of the run dispatched onto a REAL subagent manager whose session is PersonalClaw's
    own runtime, with YOLO on and the hook settings that approve every subagent's calls and start.
    Every ask is answered no, so a call that ran was never asked about."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.session import _push_approval_policy
    from personalclaw.subagent import SubagentManager
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch_stage

    tools = _Shell()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[tools],
    )
    await runtime.start()

    async def _session(*_args: Any, **kwargs: Any) -> tuple[Any, bool, bool]:
        _push_approval_policy(
            runtime, kwargs.get("approval_policy", ""), kwargs.get("approval_source")
        )
        return runtime, True, False

    sessions = _mock_sessions()
    sessions.get_or_create = AsyncMock(side_effect=_session)
    sessions.has_session = MagicMock(return_value=False)
    sessions.get_approval_policy = MagicMock(return_value="")
    ctx = _mock_ctx_builder()
    ctx.hooks.auto_approve_subagent_spawn = True
    ctx.hooks.auto_approve_subagent_tools = True
    asked: list[str] = []

    async def _no(event, parent_session_key=""):
        asked.append(event.title or "")
        return False

    manager = SubagentManager(
        sessions=sessions, ctx_builder=ctx, on_tool_approval=_no, is_yolo=lambda: True
    )
    (node,) = [n for p, n in _stages(spec) if p == path]
    with (
        patch("personalclaw.workflows.leases.config_dir", lambda: tmp_path),
        patch("personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "agents"),
        patch("personalclaw.subagent.check_memory_available", lambda **_kw: (True, 8.0)),
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel"),
    ):
        result = await dispatch_stage(
            node, BindingContext(), subagents=manager, run_id=run_id, instance_path=path
        )
        assert result.state is InstanceState.RUNNING, result
        agent_id = result.output["subagent_id"]
        for _ in range(500):
            info = manager.get(agent_id)
            if info is not None and info.done:
                break
            await asyncio.sleep(0.01)
    info = manager.get(agent_id)
    assert info is not None and info.done, "the task did not end"
    return info, tools, asked


@pytest.mark.asyncio
async def test_your_yolo_and_hook_settings_approve_none_of_an_apps_batch_tasks_calls(
    app_batch, tmp_path
):
    """YOLO is on and the hook setting approves every subagent's calls; a task of the app's batch
    still puts its listing of the plot notes to you. 🔴 Before: YOLO approved it, and it ran,
    asking nobody."""
    await _run_tool(FIND, READ_TESTS)
    run, spec = _launched_run(app_batch)
    (path, _node), _second = _stages(spec)

    model = _Script(("read_notes", {}), ("bash", {"command": "ls plots"}))
    info, tools, asked = await _run_task(run.id, spec, path, model, tmp_path)

    assert asked == ["bash"], "the call that needs approval was not put to you"
    assert tools.ran == ["read_notes"], "and you said no, so it did not run"
    assert info.app == APP and info.capability_class == "research"


@pytest.mark.asyncio
async def test_a_task_that_may_change_things_asks_you_for_each_change_after_you_allowed_it(
    app_batch, tmp_path
):
    """Your Allow started the batch; it approved none of its tasks' changes."""
    await _run_tool(FIX, FIND)
    (ask,) = _asks(app_batch.state)
    resp = await app_batch.client.post(f"/api/approvals/{ask['id']}/approve")
    assert resp.status == 200, await resp.text()
    await _until(lambda: app_batch.supervisor.launched, "the batch never started after the Allow")
    run, spec = _launched_run(app_batch)
    (path,) = [p for p, n in _stages(spec) if (n.config or {}).get("capability") == "mutating"]

    info, tools, asked = await _run_task(
        run.id, spec, path, _Model(calls=("write_note",)), tmp_path
    )

    assert asked == ["write_note"] and tools.ran == [], "a change ran without asking you"
    assert info.app == APP and info.capability_class == "mutating"


@pytest.mark.asyncio
async def test_an_apps_batch_tasks_ask_names_the_app_and_its_job(app_batch):
    """🔴 Before: "The “Find the retry callers” step of a workflow run is waiting…", and its card
    said ``workflow “subagent-batch-…” · step “…”``: nothing said whose work it was."""
    from personalclaw.approval_answer import YOU

    await _run_tool(FIND, READ_TESTS)
    run, spec = _launched_run(app_batch)
    (_path, node), _second = _stages(spec)

    waiter = asyncio.ensure_future(
        app_batch.state.request_approval(
            "subagent:task1:c0",
            "subagent",
            "bash",
            tool_input={"command": "ls plots"},
            session=f"workflow:{run.id}:{node.id}",
        )
    )
    await _until(lambda: "subagent:task1:c0" in app_batch.state._pending_approvals, "listed")
    entry = app_batch.state._pending_approvals["subagent:task1:c0"]
    whose = f"the app “{NAMED}”'s scheduled job “{CRON}”"
    assert entry["source_label"] == f"batch of {whose} · step “{node.label}”", entry
    rows = [i for i in app_batch.inbox.items.values() if i.refs.get("approval") == entry["id"]]
    assert rows and f"The “{node.label}” step of a batch from {whose}" in rows[0].message, rows
    app_batch.state.resolve_approval("subagent:task1:c0", False, by=YOU)
    await asyncio.wait_for(waiter, timeout=5)


# ── 3. the other ways an app's agent starts agents ─────────────────────────────────────────


class _Spawns:
    """The subagent manager the spawn route sees: the app's agent, and what each start asked."""

    max_concurrent = 4

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.agent = SubagentInfo(
            id=AGENT_ID, task="Water the plots.", parent_session_key=f"app:{APP}", app=APP
        )

    def get(self, agent_id: str) -> Any:
        return self.agent if agent_id == AGENT_ID else None

    def spawn(self, task: str, **kwargs: Any) -> Any:
        self.started.append({"task": task, **kwargs})
        return SimpleNamespace(id="spawned1", done=False, error="")


async def _spawn(tmp_path: Path, tier: str) -> tuple[int, dict, _Spawns]:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.handlers.messaging import api_spawn

    _install(tmp_path, _tiered(tier))
    spawns = _Spawns()
    app = web.Application()
    app["state"] = SimpleNamespace(subagents=spawns, session_creating_app=lambda _key: "")
    app.router.add_post("/api/spawn", api_spawn)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/spawn", json={"task": "Check the soil.", "parent_session": AGENT_KEY}
        )
        return resp.status, await resp.json(), spawns


@pytest.mark.asyncio
@pytest.mark.parametrize(("tier", "capability"), [("read", "research"), ("text", "text")])
async def test_a_subagent_an_apps_agent_starts_runs_at_the_apps_tier(tmp_path, tier, capability):
    """🔴 Before: it carried the app's name but no class, so it ran with every tool."""
    with _home(tmp_path):
        status, body, spawns = await _spawn(tmp_path, tier)
    assert status == 200, body
    (started,) = spawns.started
    assert started["app"] == APP and started["capability_class"] == capability, started


@pytest.mark.asyncio
async def test_a_subagent_of_an_app_that_may_run_no_agent_work_does_not_start(tmp_path):
    with _home(tmp_path):
        status, body, spawns = await _spawn(tmp_path, "")
    assert status == 403 and spawns.started == [], body
    assert "does not declare the 'agent' permission" in body["error"]["message"], body


def test_a_subagent_that_did_not_start_is_said_not_to_have_started(monkeypatch):
    """🔴 Before: every refused start was reported as "queued (at capacity)", and "All tasks
    queued — results will arrive", for a task that would never run."""
    monkeypatch.setattr(mcp_subagents, "_resolve_session_key", lambda: AGENT_KEY)
    monkeypatch.setattr(mcp_subagents, "_post", lambda _p, _b: {"error": "the app may not"})
    out = mcp_subagents._call_tool_inner("subagent_run", {"task": "Check the soil."})
    assert "did not start" in out and "the app may not" in out, out
    assert "queued" not in out, out


async def _start_a_workflow(batch: Any, monkeypatch) -> Any:
    """A saved workflow of two steps, started from the app's agent's session, as
    ``workflow_start`` starts it: through the service, on the gateway's supervisor."""
    from personalclaw.workflows import service

    batch.supervisor.state = batch.state
    saved = await service.author_def(
        name="tidy-the-shed",
        root={
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "stage", "id": "sort", "label": "Sort", "config": {"prompt": "Sort it."}},
                {
                    "kind": "action",
                    "id": "brief",
                    "label": "Brief",
                    "config": {"provider": "invoke-agent", "with": {"task_template": "Go on."}},
                },
            ],
        },
        save=True,
        strict=False,
    )
    assert saved.get("ok"), saved
    started = await service.start_run(
        name="tidy-the-shed",
        supervisor=batch.supervisor,
        session_key=AGENT_KEY,
        skip_preflight=True,
    )
    assert started.get("ok"), started
    run = store.get(started["run_id"])
    assert run is not None
    return run


@pytest.mark.asyncio
async def test_a_workflow_run_an_apps_agent_starts_is_the_apps_work_and_so_is_its_fork(
    app_batch, monkeypatch
):
    """🔴 Before: nothing recorded whose work it was, so its steps were yours."""
    from personalclaw.workflows import ownership

    run = await _start_a_workflow(app_batch, monkeypatch)

    from personalclaw.apps import app_work

    assert app_work.of_run(run) == app_work.AppWork(APP, JOB), run.extra
    child = SimpleNamespace(extra=ownership.inherited_extra(run), origin=None)
    assert app_work.of_run(child) == app_work.AppWork(APP, JOB), child.extra


@pytest.mark.asyncio
async def test_an_apps_run_is_still_its_work_once_the_agent_that_started_it_is_gone(app_batch):
    """A run outlives a restart, and the agent that started it does not. 🔴 Before: whose work
    a step was went through that agent, so once it was gone the step was yours, and read all
    of your memory."""
    from personalclaw import memory_reads

    await _run_tool(FIND, READ_TESTS)
    run, spec = _launched_run(app_batch)
    (_path, node), _second = _stages(spec)
    del app_batch.manager._agents[AGENT_ID]

    reach = memory_reads.reach_of(app_batch.state, f"workflow:{run.id}:{node.id}")

    assert reach.app == APP and reach.job == JOB, reach


@pytest.mark.asyncio
async def test_an_action_step_that_would_start_an_agent_of_its_own_is_refused_in_an_apps_run(
    app_batch, monkeypatch
):
    """An app's run starts agents as its own steps, held to the app's tier; an action that starts
    one as an automation does (with the step's own approval and write access) is refused.
    🔴 Before: it ran, and its agent approved its calls on your YOLO."""
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch_action

    run = await _start_a_workflow(app_batch, monkeypatch)
    executed: list[str] = []

    class _InvokeAgent:
        async def execute(self, action_config, ctx, timeout=30):
            executed.append(str(action_config))
            return SimpleNamespace(success=True, outcome="launched", stdout="")

    node = Node(
        kind=NodeKind.ACTION,
        id="brief",
        config={"provider": "invoke-agent", "with": {"task_template": "Go on."}},
    )
    result = await dispatch_action(
        node, BindingContext(), get_provider=lambda _name: _InvokeAgent(), run_id=run.id
    )

    assert executed == [], "the step's own agent started"
    assert result.state is InstanceState.FAILED
    assert NAMED in result.failure.cause_plain, result.failure.cause_plain
