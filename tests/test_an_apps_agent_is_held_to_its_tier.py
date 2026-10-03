"""An app's agent works at the tier its manifest declares, and never outranks you.

``permissions.agent`` was one boolean, and holding it gave an app's background agents every tool
with nothing asked: an app that only summarises the text it sends got agents that could change
files and run commands, and install consent said so. It is now a tier, each worded on its own at
install and each held where the app's agent work runs:

* ``text`` — the model is handed the task the app sends and nothing else of yours, and gets no
  tools;
* ``read`` — an agent with read-only tools;
* ``tools`` — an agent with your tools; the app approves none of its calls, so each one that
  needs approval asks you.

``true`` names no tier, so it is refused at install, and an installed app that still declares it
runs no agent work until it is updated. Imports of names this change adds sit inside the tests,
so on a tree without them each test fails on its own rather than the module failing to collect.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_apps_cannot_run_code_or_bypass_approvals import _home, _install

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.apps.manifest import AppManifest
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

APP = "probe-notes"
TASK = "Summarise these notes in two lines: the gutters are cleared on Fridays."

#: What a manifest that declares nothing about agents looks like.
_UNDECLARED = object()


# ── 1. what the manifest declares ──────────────────────────────────────────────────────────


def _declared(agent: object = _UNDECLARED) -> AppManifest:
    permissions = {} if agent is _UNDECLARED else {"agent": agent}
    return AppManifest.from_dict(
        {
            "name": APP,
            "version": "1.0.0",
            "displayName": "Probe Notes",
            "description": "x",
            "permissions": permissions,
        }
    )


@pytest.mark.parametrize("tier", ["text", "read", "tools"])
def test_the_manifest_declares_a_tier_and_consent_is_handed_it(tier):
    manifest = _declared(tier)
    assert manifest.permissions.agent_tier == tier
    assert manifest.permissions.to_dict() == {"agent": tier}
    assert manifest.validate() == []


@pytest.mark.parametrize("agent", [_UNDECLARED, False], ids=["absent", "false"])
def test_declaring_no_agent_is_no_agent_grant(agent):
    manifest = _declared(agent)
    assert manifest.permissions.agent_tier == ""
    assert "agent" not in manifest.permissions.to_dict()
    assert manifest.validate() == []


@pytest.mark.parametrize("agent", [True, "unattended", "yes", 1, ["text"]])
def test_true_or_anything_but_a_tier_is_refused_at_install_and_grants_nothing(agent):
    manifest = _declared(agent)
    assert manifest.permissions.agent_tier == "", "a declaration that names no tier grants nothing"
    assert "agent" not in manifest.permissions.to_dict(), "consent shows no grant it would not keep"
    errors = [e for e in manifest.validate() if "permissions.agent" in e]
    assert len(errors) == 1, manifest.validate()
    assert all(f'"{tier}"' in errors[0] for tier in ("text", "read", "tools")), errors[0]
    assert json.dumps(agent) in errors[0], "the refusal names what was written"


def test_the_refusal_record_is_no_permission_anyone_can_declare():
    from personalclaw.apps.manifest import PERMISSION_KEYS

    refused = _declared(True)
    assert refused.permissions.agent_declared_raw == "true"
    assert "agent" in PERMISSION_KEYS
    assert "agent_declared_raw" not in PERMISSION_KEYS
    assert "agent_declared_raw" not in json.dumps(refused.to_dict())
    # Written into a manifest by hand, it is an unknown key like any other, never a grant.
    smuggled = AppManifest.from_dict(
        {**refused.to_dict(), "permissions": {"agent_declared_raw": "tools"}}
    )
    assert smuggled.permissions.unknown_keys == ("agent_declared_raw",)
    assert smuggled.permissions.agent_tier == ""


def test_a_call_written_for_the_boolean_fails_where_it_is_made():
    """``Permissions.agent`` was the boolean grant. Had the field kept that name and changed its
    type, an SDK caller's old ``Permissions(agent=True)`` would still bind and then grant nothing,
    so the tier has a name of its own, keyword-only (the SDK's loud-break rule), and the old call
    fails where it is made. A manifest still declares the tier as ``permissions.agent``."""
    import inspect

    from personalclaw.apps.manifest import PERMISSION_KEYS, Permissions

    with pytest.raises(TypeError, match="unexpected keyword argument 'agent'"):
        Permissions(agent=True)  # type: ignore[call-arg]
    tier = inspect.signature(Permissions).parameters["agent_tier"]
    assert tier.kind is inspect.Parameter.KEYWORD_ONLY and tier.default == ""
    assert Permissions(agent_tier="read").to_dict() == {"agent": "read"}
    assert "agent" in PERMISSION_KEYS and "agent_tier" not in PERMISSION_KEYS
    # The field's own name is no manifest key: written there, it is refused and grants nothing.
    spelled = AppManifest.from_dict(
        {**_declared().to_dict(), "permissions": {"agent_tier": "tools"}}
    )
    assert spelled.permissions.unknown_keys == ("agent_tier",)
    assert spelled.permissions.agent_tier == ""


@pytest.mark.parametrize("value", [True, "shared", "Tools", None])
def test_the_tier_field_holds_a_tier_or_nothing(value):
    """Every reader takes the field as a tier or ``""``: the permission checker, the agent-run
    route and install consent's sentence for it. A value that is neither is refused where the
    permissions are built, never handed to them."""
    from personalclaw.apps.manifest import Permissions

    with pytest.raises((TypeError, ValueError), match="names a tier"):
        Permissions(agent_tier=value)  # type: ignore[arg-type]


def test_the_tiers_widen_in_order_and_a_name_that_is_none_covers_nothing():
    from personalclaw.apps.agent_tiers import AGENT_TIERS, agent_tier_covers
    from personalclaw.sdk.manifest import AGENT_TIERS as SDK_TIERS

    assert AGENT_TIERS == ("text", "read", "tools")
    assert SDK_TIERS == AGENT_TIERS, "an app reads the tiers it may declare through the SDK"
    for i, held in enumerate(AGENT_TIERS):
        for j, needed in enumerate(AGENT_TIERS):
            assert agent_tier_covers(held, needed) is (i >= j), (held, needed)
    assert agent_tier_covers("", "text") is False
    assert agent_tier_covers("tools", "") is False
    assert agent_tier_covers("unattended", "text") is False


# ── 2. what a task asks for, at the route ──────────────────────────────────────────────────


class _Recorder:
    """The subagents store, recording what each start asked for."""

    max_concurrent = 4

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self._runs: dict[str, Any] = {}

    def spawn(self, task: str, **kwargs: Any) -> Any:
        self.started.append({"task": task, **kwargs})
        info = SimpleNamespace(
            id=f"run-{len(self.started)}",
            task=task,
            done=False,
            error="",
            result="",
            result_path="",
            turns=0,
            started=0.0,
            parent_session_key=kwargs.get("parent_session_key", ""),
        )
        self._runs[info.id] = info
        return info

    def get(self, run_id: str) -> Any:
        return self._runs.get(run_id)


def _gateway(state: Any, caller: str) -> web.Application:
    """The app routes, with the calling app's identity stamped as an app-scoped token sets it."""
    from personalclaw.dashboard.handlers.apps import register_app_routes

    @web.middleware
    async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
        if caller:
            request["app"] = caller
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app["state"] = state
    register_app_routes(app)
    return app


async def _post(tmp_path, held: object, body: Any, subagents: Any) -> tuple[int, Any]:
    with _home(tmp_path):
        _install(tmp_path, APP, {} if held is None else {"agent": held})
        state = SimpleNamespace(subagents=subagents)
        async with TestClient(TestServer(_gateway(state, APP))) as client:
            resp = await client.post(f"/api/apps/{APP}/agent-run", json=body)
            return resp.status, await resp.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tier", "capability"), [("text", "text"), ("read", "research"), ("tools", "mutating")]
)
async def test_a_task_runs_at_its_apps_tier_and_approves_nothing_on_its_own(
    tmp_path, tier, capability
):
    subagents = _Recorder()
    status, body = await _post(tmp_path, tier, {"task": TASK}, subagents)
    assert status == 202, body
    assert body["tier"] == tier
    (started,) = subagents.started
    assert started["capability_class"] == capability
    assert started["app"] == APP
    assert started["parent_session_key"] == f"app:{APP}"
    assert not started.get("approval_mode"), "an app's agent approves none of its own calls"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("held", "asked", "capability"),
    [("tools", "read", "research"), ("tools", "text", "text"), ("read", "text", "text")],
)
async def test_a_task_may_ask_for_less_than_its_app_holds(tmp_path, held, asked, capability):
    subagents = _Recorder()
    status, body = await _post(tmp_path, held, {"task": TASK, "tier": asked}, subagents)
    assert status == 202, body
    assert body["tier"] == asked
    assert subagents.started[0]["capability_class"] == capability


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("held", "asked"), [("text", "read"), ("text", "tools"), ("read", "tools")]
)
async def test_a_task_that_asks_for_more_than_its_tier_is_refused(tmp_path, held, asked):
    subagents = _Recorder()
    status, body = await _post(tmp_path, held, {"task": TASK, "tier": asked}, subagents)
    assert status == 403, body
    assert body["error"]["code"] == "agent_tier_exceeded"
    message = body["error"]["message"]
    assert f'"{held}"' in message and f'"{asked}"' in message, message
    assert subagents.started == [], "nothing was started"


@pytest.mark.asyncio
async def test_the_refusal_is_audited_as_the_app_that_asked(tmp_path):
    """The audit row of a refused task names the calling app, not the owner whose gateway it is."""
    rows = MagicMock()
    with patch("personalclaw.sel.sel", return_value=rows):
        status, _body = await _post(tmp_path, "text", {"task": TASK, "tier": "tools"}, _Recorder())
    assert status == 403
    (denied,) = [
        c.kwargs
        for c in rows.log_api_access.call_args_list
        if c.kwargs.get("operation") == "apps.agent_run" and c.kwargs.get("outcome") == "denied"
    ]
    assert denied["caller"] == f"app:{APP}", denied
    assert '"tools"' in denied["error"], denied


@pytest.mark.asyncio
async def test_a_text_task_names_no_agent(tmp_path):
    """A text task runs on no agent of yours, so naming one asks for an agent and its tools."""
    subagents = _Recorder()
    status, body = await _post(tmp_path, "text", {"task": TASK, "agent": "researcher"}, subagents)
    assert status == 403, body
    assert body["error"]["code"] == "agent_tier_exceeded"
    assert subagents.started == []


@pytest.mark.asyncio
@pytest.mark.parametrize("asked", ["unattended", "all", 3])
async def test_a_tier_that_is_not_one_is_a_bad_request(tmp_path, asked):
    subagents = _Recorder()
    status, body = await _post(tmp_path, "tools", {"task": TASK, "tier": asked}, subagents)
    assert status == 400, body
    assert body["error"]["code"] == "agent_tier_unknown"
    assert subagents.started == []


@pytest.mark.asyncio
async def test_an_app_installed_declaring_true_runs_no_agent_work_until_it_is_updated(tmp_path):
    """An install made before tiers: its manifest still says ``true``, which names none."""
    subagents = _Recorder()
    status, body = await _post(tmp_path, True, {"task": TASK}, subagents)
    assert status == 403, body
    assert subagents.started == []
    # What the app shows her says what happened and what to do, and is true: the app DOES
    # declare the permission, as `true`, which names no tier.
    said = body["error"]
    assert "true" in said and "update" in said, said
    assert "does not declare" not in said, said


# ── 3. what the run is shown, handed and may use ───────────────────────────────────────────


class _Notes(ToolProvider):
    """One tool that reads her notes and one that changes them; ``ran`` names each call that ran."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    @property
    def name(self) -> str:
        return "notes"

    @property
    def display_name(self) -> str:
        return "Notes"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="read_notes",
                description="Read a note.",
                parameters={"type": "object"},
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="write_note",
                description="Change a note.",
                parameters={"type": "object"},
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
            ),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.ran.append(tool_name)
        return ToolResult(success=True, output="ok")


class _Model:
    """A scripted model: its first answer makes *calls*, its next says it is done. It records the
    tools each inference was offered and the messages it was handed."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, calls: tuple[str, ...] = ()) -> None:
        self._calls = calls
        self.offered: list[list[str]] = []
        self.handed: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        names = []
        for tool in tools or []:
            fn = tool.get("function", tool) if isinstance(tool, dict) else {}
            names.append(str(fn.get("name", "")))
        self.offered.append(sorted(names))
        self.handed.append(list(messages))
        if len(self.offered) == 1 and self._calls:
            for i, name in enumerate(self._calls):
                yield AgentEvent(
                    kind=EVENT_TOOL_CALL, tool_call_id=f"c{i}", title=name, tool_input="{}"
                )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AgentEvent(kind=EVENT_COMPLETE)


#: What the context builder would put ahead of a task: her memory, lessons and history.
_HER_CONTEXT = "[her memory, lessons and history]"


async def _run(
    tmp_path,
    held: str,
    model: _Model,
    *,
    body: dict[str, Any] | None = None,
    runtime: Any = None,
    yolo: bool = False,
) -> tuple[Any, _Notes, MagicMock, list[str], Any]:
    """Start a task through the route on a REAL subagent manager, whose session is a native
    runtime with ``_Notes`` and *model* (or *runtime*), and wait for it to end.

    Returns ``(the run, the notes tool, the session manager, the calls you were asked about,
    the poll's body)``. Every ask is answered no, so a call that ran was never asked about."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentManager

    notes = _Notes()
    if runtime is None:
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
            model_provider=model,
            tool_providers=[notes],
        )
        await runtime.start()

    async def _session(*_args: Any, **kwargs: Any) -> tuple[Any, bool, bool]:
        # What the session manager does for a runtime that gates its own tools: it is handed the
        # spawn's approval source, read at each call (`session._push_approval_policy`).
        if isinstance(runtime, NativeAgentRuntime):
            from personalclaw.session import _push_approval_policy

            _push_approval_policy(
                runtime, kwargs.get("approval_policy", ""), kwargs.get("approval_source")
            )
        return runtime, True, False

    sessions = _mock_sessions()
    sessions.get_or_create = AsyncMock(side_effect=_session)
    sessions.has_session = MagicMock(return_value=False)
    sessions.get_approval_policy = MagicMock(return_value="")
    ctx = _mock_ctx_builder()
    ctx.build_message = MagicMock(
        side_effect=lambda msg, *_a, **_k: (f"{_HER_CONTEXT}\n{msg}", None)
    )
    asked: list[str] = []

    async def _no(event, parent_session_key=""):
        asked.append(event.title or "")
        return False

    manager = SubagentManager(
        sessions=sessions,
        ctx_builder=ctx,
        on_tool_approval=_no,
        is_yolo=(lambda: True) if yolo else None,
    )
    with (
        _home(tmp_path),
        patch("personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "runs"),
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent.sel"),
    ):
        _install(tmp_path, APP, {"agent": held})
        state = SimpleNamespace(subagents=manager)
        async with TestClient(TestServer(_gateway(state, APP))) as client:
            resp = await client.post(f"/api/apps/{APP}/agent-run", json=body or {"task": TASK})
            assert resp.status == 202, await resp.text()
            run_id = (await resp.json())["id"]
            for _ in range(500):
                info = manager.get(run_id)
                if info is not None and info.done:
                    break
                await asyncio.sleep(0.01)
            polled = await (await client.get(f"/api/apps/{APP}/agent-run/{run_id}")).json()
    info = manager.get(run_id)
    assert info is not None and info.done, "the run did not end"
    return info, notes, sessions, asked, polled


@pytest.mark.asyncio
async def test_a_text_tasks_model_is_handed_the_task_alone_and_shown_no_tools(tmp_path):
    model = _Model()
    info, notes, sessions, asked, polled = await _run(tmp_path, "text", model)
    from personalclaw.agents.defaults import LITE_AGENT_NAME

    assert info.error == "", info.error
    assert polled["result"] == "done"
    assert model.offered == [[]], "a text task's model is shown no tools at all"
    sent = [m for m in model.handed[0] if m.get("role") == "user"]
    assert [m.get("content") for m in sent] == [TASK], "handed the app's task, and nothing else"
    assert not any(_HER_CONTEXT in str(m.get("content")) for m in model.handed[0])
    assert (
        sessions.get_or_create.call_args.kwargs["agent"] == LITE_AGENT_NAME
    ), "it runs on the worker that has no tools to call"
    assert info.agent == LITE_AGENT_NAME, "the background-agents list names the agent it ran on"
    assert asked == [] and notes.ran == []


@pytest.mark.asyncio
async def test_a_call_a_text_tasks_model_makes_anyway_is_refused(tmp_path):
    model = _Model(calls=("read_notes", "write_note"))
    info, notes, _sessions, asked, _polled = await _run(tmp_path, "text", model)
    assert notes.ran == [], "no tool of hers ran for a text task"
    assert asked == [], "and none was put to her: a text task has nothing to ask about"


@pytest.fixture
def ceiling(tmp_path, monkeypatch):
    """Install an operator ceiling for this test, as ``governance/ceiling.json`` declares one."""
    from personalclaw.guardrails import ceiling as C

    def install(**scopes: Any) -> None:
        path = tmp_path / "operator" / "ceiling.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "scopes": scopes}), encoding="utf-8")
        monkeypatch.setenv(C.CEILING_PATH_ENV, str(path))
        C.reset_ceiling()

    C.reset_ceiling()
    yield install
    C.reset_ceiling()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tools",
    [{"allow": ["read_notes", "write_note"]}, {"enabled": False}],
    ids=["an allowlist", "read-only tools"],
)
async def test_no_operator_ceiling_hands_a_text_task_a_tool(tmp_path, ceiling, tools):
    """A ceiling only narrows, and a text task already may use nothing: an operator's tool
    allowlist, or a ceiling holding every run to read-only tools, gives it none."""
    ceiling(tools=tools)
    model = _Model(calls=("read_notes", "write_note"))
    _info, notes, _sessions, asked, _polled = await _run(tmp_path, "text", model)
    assert model.offered[0] == [], model.offered
    assert notes.ran == [] and asked == []


@pytest.mark.asyncio
async def test_a_read_tasks_agent_is_shown_and_runs_read_tools_only(tmp_path):
    model = _Model(calls=("read_notes", "write_note"))
    info, notes, _sessions, asked, _polled = await _run(tmp_path, "read", model)
    assert model.offered[0] == ["read_notes"], model.offered
    assert notes.ran == ["read_notes"], "the read ran; the change did not"
    assert asked == [], "a change is refused by the tier, never put to her as a question"


@pytest.mark.asyncio
async def test_a_tools_tasks_change_asks_you_whatever_your_switches_say(tmp_path):
    """YOLO is on, and the app's agent still approves nothing on its own: its call asks you."""
    model = _Model(calls=("read_notes", "write_note"))
    info, notes, _sessions, asked, _polled = await _run(tmp_path, "tools", model, yolo=True)
    assert model.offered[0] == ["read_notes", "write_note"], model.offered
    assert asked == ["write_note"], "the change was put to you"
    assert notes.ran == ["read_notes"], "and you said no, so it did not run"


@pytest.mark.asyncio
@pytest.mark.parametrize("held", ["text", "read"])
async def test_a_task_held_to_fewer_tools_never_runs_where_they_cannot_be_held(tmp_path, held):
    """An agent CLI runs its own tools where the host never sees them, so a read or text task is
    refused there before it starts, rather than run on tools nobody can hold it to."""
    from test_subagent import _mock_sessions

    cli = _mock_sessions().get_or_create.return_value[0]
    model = _Model()
    body = {"task": TASK, "agent": "a-cli"} if held == "read" else None
    with (
        patch("personalclaw.subagent._validate_agent", lambda name: (name, "")),
        patch("personalclaw.subagent_tier.Stats"),
        patch("personalclaw.subagent_tier.sel") as audit,
    ):
        info, notes, _sessions, asked, polled = await _run(
            tmp_path, held, model, runtime=cli, body=body
        )
    assert info.error, "the run ended refused"
    assert "PersonalClaw's own agent" in info.error, info.error
    cli.stream.assert_not_called()
    assert polled["error"] == info.error
    (row,) = audit.return_value.log_tool_invocation.call_args_list
    assert row.kwargs["outcome"] == "refused" and row.kwargs["metadata"]["app"] == APP, row


# ── 4. agent work at the gateway's routes ──────────────────────────────────────────────────


@pytest.mark.parametrize(("tier", "refused"), [("text", True), ("read", True), ("tools", False)])
def test_a_turn_in_its_own_chat_needs_the_tools_tier(tmp_path, tier, refused):
    from personalclaw.apps.permissions import app_request_denial

    with _home(tmp_path):
        _install(tmp_path, APP, {"api": ["/api/chat"], "agent": tier})
        why = app_request_denial(APP, "/api/chat", method="POST", route="/api/chat")
    if refused:
        assert '"tools"' in why and f'"{tier}"' in why, why
    else:
        assert why == ""


def test_summarising_links_it_sends_needs_only_the_text_tier(tmp_path):
    from personalclaw.apps.permissions import app_request_denial

    route = "/api/chat/nav/resolve-links"
    with _home(tmp_path):
        _install(tmp_path, APP, {"api": ["/api/chat"], "agent": "text"})
        assert app_request_denial(APP, route, method="POST", route=route) == ""
        _install(tmp_path, APP, {"api": ["/api/chat"]})
        why = app_request_denial(APP, route, method="POST", route=route)
    assert '"text"' in why, f"the refusal names the tier the work needs: {why}"


# ── 5. a call that asks, at the gateway ────────────────────────────────────────────────────


def _relay(info: Any, yolo_flag: str = "") -> Any:
    """The gateway's approval relay for background agents' calls, its subagents store holding
    *info*, with nobody on a channel and her answer on the dashboard always no."""
    from personalclaw.gateway import GatewayOrchestrator

    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gateway.sessions = MagicMock()
    gateway._channel_delivery = None
    gateway.dashboard_state = MagicMock()
    gateway.dashboard_state._sessions = {}
    gateway.dashboard_state.is_yolo_active.return_value = False
    gateway.dashboard_state.request_approval = AsyncMock(return_value=False)
    gateway.dashboard_state.ended_as = MagicMock(return_value="rejected")
    gateway._approval_mode = yolo_flag or None
    gateway.subagent_mgr = MagicMock()
    gateway.subagent_mgr.get = MagicMock(side_effect=lambda run: info if run == info.id else None)
    return gateway


@pytest.mark.asyncio
@pytest.mark.parametrize("grant", ["yolo", "every background call", "the --approval flag"])
async def test_none_of_your_standing_grants_approves_an_app_agents_call(tmp_path, grant):
    """Each grant approves her own agent's call without asking her, and an app's is still asked:
    the relay every background agent's call that asks goes through reads none of them for it."""
    import personalclaw.config.loader as loader
    from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, LLMEvent
    from personalclaw.subagent import SubagentInfo, tool_approval_id

    if grant == "every background call":
        path = loader.config_dir() / "config.json"
        path.write_text(json.dumps({"hooks": {"auto_approve_sources": ["subagent"]}}))
    yolo = grant == "yolo"
    flag = "yolo" if grant == "the --approval flag" else ""
    for app, asked in (("", False), (APP, True)):
        parent = f"app:{APP}" if app else "dashboard:hers"
        info = SubagentInfo(id="ab12", task=TASK, parent_session_key=parent, app=app)
        gateway = _relay(info, flag)
        call = LLMEvent(
            kind=EVENT_PERMISSION_REQUEST, request_id=tool_approval_id("ab12", 1), title="Bash"
        )
        with patch("personalclaw.trust_mode.is_yolo_active", return_value=yolo):
            decision = await gateway._interactive_approval("subagent")(call, parent)
        if asked:
            gateway.dashboard_state.request_approval.assert_awaited_once()
            assert not decision, "an app's agent's call was approved without asking her"
        else:
            assert decision, f"premise: {grant} approves her own agent's call"
            gateway.dashboard_state.request_approval.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parent", "app"),
    [("dashboard:its-chat", APP), ("subagent:its-run", APP), ("dashboard:hers", ""), ("", "")],
    ids=["a conversation it started", "a run of its agent", "her conversation", "no parent"],
)
async def test_an_agent_spawned_by_an_apps_work_is_that_apps_agent_work(parent, app):
    """An agent its conversation or its run spawns carries the app, so the start and the calls
    are held as the app's (`SubagentManager._grant_now`, the relay above), never as hers."""
    from personalclaw.dashboard.handlers.messaging import api_spawn

    started: list[dict[str, Any]] = []

    class _Store:
        max_concurrent = 4

        def spawn(self, task: str, **kwargs: Any) -> Any:
            started.append(kwargs)
            return SimpleNamespace(id="kid", done=False, error="")

        def get(self, run_id: str) -> Any:
            return SimpleNamespace(app=APP) if run_id == "its-run" else None

    # Whose each conversation is, as the gateway's state answers it (`session_creating_app`).
    started_by = {"its-chat": APP, "hers": ""}
    state = SimpleNamespace(
        subagents=_Store(), session_creating_app=lambda name: started_by.get(name, "")
    )
    gateway = web.Application()
    gateway["state"] = state
    gateway.router.add_post("/api/spawn", api_spawn)
    async with TestClient(TestServer(gateway)) as client:
        resp = await client.post("/api/spawn", json={"task": TASK, "parent_session": parent})
        assert resp.status == 200, await resp.text()
    (spawned,) = started
    assert spawned["app"] == app, spawned


def test_an_app_agents_ask_names_the_app_it_works_for(tmp_path):
    """Her Inbox says whose agent is waiting on her: the app's, by the name she installed it by."""
    from personalclaw.dashboard.approval_state import _who_asked

    asked = {"source": "subagent", "session": f"app:{APP}", "tool": "write_file"}
    with _home(tmp_path):
        assert _who_asked(asked) == f"A background agent of the app “{APP}”"
        _install(tmp_path, APP, {"agent": "tools"})
        from personalclaw.apps.app_manager import display_name_of

        named = display_name_of(APP)
        assert _who_asked(asked) == f"A background agent of the app “{named}”"
    assert _who_asked({"source": "subagent", "session": "dashboard:hers"}) == "A subagent"


@pytest.mark.asyncio
async def test_an_app_agents_result_is_the_apps_and_no_agent_of_hers_is_handed_it():
    """When an app's task ends, its result waits for the app's poll. It was announced to a turn of
    her own agent instead, in a session keyed by the app: her memory put ahead of it, her tools,
    and the announce's auto-approval, so the app's text steered an agent that approved itself."""
    from test_gateway import _make_orchestrator, _mock_dashboard_state, _mock_sessions

    from personalclaw.subagent import SubagentInfo

    orch = _make_orchestrator()
    orch.sessions = _mock_sessions()
    orch.ctx_builder = MagicMock()
    orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
    orch.dashboard_state = _mock_dashboard_state()
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager,
    ):
        manager.return_value = MagicMock(running=[], get=MagicMock(return_value=None))
        orch._init_subagents()
    on_done = manager.call_args.kwargs["on_done"]
    info = SubagentInfo(
        id="ab12",
        task=TASK,
        parent_session_key=f"app:{APP}",
        silent=True,
        app=APP,
        done=True,
        result="Gutters: Fridays.",
    )
    await on_done([info])
    orch.sessions.get_or_create.assert_not_called()
    orch.ctx_builder.build_message.assert_not_called()
    orch.dashboard_state.notify.assert_not_called()
