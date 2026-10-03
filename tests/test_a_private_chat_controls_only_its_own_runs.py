"""A Temporary or Incognito chat's agent changes only the runs it started, and every call
PersonalClaw makes with its internal credential names the work it is for.

Two doors reach the workflow engine. An agent CLI's tool server calls the gateway's routes, which
hold a Temporary or Incognito chat's call to one rule: it starts a run or a batch, which keeps the
chat's mode, and it controls only a run it started that keeps nothing. A native agent's workflow
tools reach the engine inside the gateway, with no route, and they held it to nothing: such an
agent stopped, edited and resumed the owner's runs, and saved workflows into her library.

And a call made with the internal credential that named no session was taken for the owner's own:
it read and changed her memory, and a run it started belonged to nobody and kept everything. Every
process that holds the credential names the work it does now (an agent CLI's tool server its chat,
a pooled one the chat that claimed it, a tool the gateway runs for a request that request's work,
the CLI its command), and a call that names none is refused.

Driven as each is driven: a real gateway asking for a sign-in with its workflow supervisor
started, a native agent's turn whose scripted model calls the real tool, the real ``mcp-core``
process an agent CLI runs, and the gateway's own HTTP routes.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from test_an_agent_clis_workflow_tools_reach_the_gateway import (  # noqa: F401 - a fixture
    CLI_MODEL,
    KEY,
    TEMPORARY_KEY,
    TWO_STEPS,
    WAITS_LONG,
    _body,
    _ends,
    _state,
    _tool_server,
    _until,
    gateway,
)

from personalclaw import mcp_core, mcp_workflows, memory_writes, session_restrictions
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.agents.provider import AgentProvider, AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.workflows import ownership, service, store, template_store
from personalclaw.workflows.models import RunStatus

#: The owner's fact, saved on her Memory page, and the words a call that reads it would find.
FACT = "An oat flat white, no sugar."

#: The model the Temporary chat's native agent runs on (an invented name), which its turn names.
CHAT_MODEL = "local:scripted-chat-model"

#: A spec a valid check would freeze as a candidate for the chat that checked it.
CHECKED = {
    "name": "count-the-notes",
    "description": "Count the notes and say how many there are",
    "root": json.dumps(TWO_STEPS["root"]),
}


class _Scripted:
    """A native agent's model that calls the tool it is told to, once a turn, then answers."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self._call: tuple[str, dict[str, Any]] | None = None
        self._turn = 0

    def next(self, tool: str, arguments: dict[str, Any]) -> None:
        self._call = (tool, arguments)

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        if self._call is not None:
            tool, arguments = self._call
            self._call = None
            self._turn += 1
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"call-{self._turn}",
                title=tool,
                tool_input=json.dumps(arguments),
            )
            yield AgentEvent(kind=EVENT_COMPLETE)
            return
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AgentEvent(kind=EVENT_COMPLETE)


class _NativeAgent:
    """The Temporary chat's native agent: its runtime, with the gateway's workflow tools, and the
    turn the turn engine runs it in (``memory_writes.runs_as_its_session``, ``answered_by``)."""

    def __init__(self, key: str, mode: str) -> None:
        self.key = key
        self.mode = mode
        self.model = _Scripted()
        self.runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
            model_provider=self.model,
            tool_providers=[
                InProcessMcpToolProvider(
                    module="personalclaw.mcp_workflows", provider_name="personalclaw-workflows"
                )
            ],
            session_key=key,
        )
        self.runtime.set_task_mode("agent")

    async def calls(self, tool: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """One turn in which the agent calls *tool*: its owner allows the call when asked, as she
        allows any tool call. Returns whether it succeeded and what the model was answered."""
        self.model.next(tool, arguments)
        results: list[tuple[bool, str]] = []
        with memory_writes.derived_from(self.key, memory_mode=self.mode):
            memory_writes.answered_by(CHAT_MODEL)
            async for event in self.runtime.stream("go"):
                if event.kind == EVENT_PERMISSION_REQUEST:
                    await self.runtime.approve_tool(event.request_id)
                elif event.kind == EVENT_TOOL_RESULT:
                    output = str(event.tool_output)
                    results.append((not output.startswith("Error"), output))
        assert len(results) == 1, results
        return results[0]


async def _temporary_native_agent(gw: SimpleNamespace) -> _NativeAgent:
    """The Temporary chat live in the gateway, as it is while its agent's turn runs, and its
    agent."""
    gw.state.get_or_create_session(
        TEMPORARY_KEY.removeprefix("dashboard:"), memory_mode="temporary"
    )
    session_restrictions.mark_own_model(TEMPORARY_KEY, CHAT_MODEL)
    agent = _NativeAgent(TEMPORARY_KEY, "temporary")
    await agent.runtime.start()
    return agent


async def _yours(gw: SimpleNamespace, name: str = WAITS_LONG["name"]) -> str:
    """A run you started, from your own Workflows page."""
    started = await service.start_run(name=name, supervisor=gw.supervisor, skip_preflight=True)
    assert started.get("ok"), started
    return str(started["run_id"])


def _secret(gw: SimpleNamespace) -> str:
    return (gw.home / ".local_secret").read_text(encoding="utf-8").strip()


def _url(gw: SimpleNamespace, path: str) -> str:
    return f"http://127.0.0.1:{gw.port}{path}"


async def _owner_token(gw: SimpleNamespace) -> str:
    """What ``personalclaw token`` does: trade the local secret for a sign-in of yours."""
    async with aiohttp.ClientSession() as http:
        resp = await http.get(_url(gw, "/api/token/local"), headers={"X-Local-Secret": _secret(gw)})
        assert resp.status == 200, await resp.text()
        return str((await resp.json())["token"])


async def _owner_saves_a_fact(gw: SimpleNamespace) -> None:
    """You save a fact on the Memory page."""
    token = await _owner_token(gw)
    async with aiohttp.ClientSession() as http:
        resp = await http.put(
            _url(gw, "/api/memory/semantic"),
            json={"key": "user.coffee_order", "value": FACT},
            headers={"Authorization": f"Bearer {token}", "X-Session-Key": "dashboard:ui"},
        )
        assert resp.status == 200, await resp.text()


async def _internal(
    gw: SimpleNamespace, method: str, path: str, *, work: str = "", body: Any = None
) -> tuple[int, Any]:
    """A call made with the gateway's internal credential, naming *work* when it is given."""
    headers = {"X-Internal-Secret": _secret(gw)}
    if work:
        headers["X-Session-Key"] = work
    async with aiohttp.ClientSession() as http:
        resp = await http.request(method, _url(gw, path), json=body, headers=headers)
        return resp.status, await resp.json(content_type=None)


@pytest.fixture
def your_library() -> Iterator[dict[str, dict]]:
    """Your workflow library, where a saved definition lands, read back by the test."""
    from personalclaw.workflows import defs as defs_mod

    saved: dict[str, dict] = {}

    class _Library(defs_mod.WorkflowDefProvider):
        @property
        def name(self) -> str:
            return "aaa-your-library"

        @property
        def readonly(self) -> bool:
            return False

        async def list_defs(self, *, limit: int = 200, offset: int = 0):
            return list(saved.values())[offset : offset + limit], len(saved)

        async def get_def(self, name: str):
            return saved.get(name)

        async def save_def(self, **fields):
            saved[fields["name"]] = {**fields, "source": "chat", "version": 1}
            return saved[fields["name"]]

    defs_mod.register_provider(_Library())
    try:
        yield saved
    finally:
        defs_mod.unregister_provider("aaa-your-library")


# ── a Temporary chat's native agent, in the gateway ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_temporary_native_agent_cannot_stop_your_run_and_controls_its_own(
    gateway,  # noqa: F811 - the imported fixture
):
    """🔴 Before: its `workflow_cancel` stopped your run. A native agent's workflow tools reach the
    engine in the gateway with no route between, and nothing asked what its chat may change."""
    agent = await _temporary_native_agent(gateway)
    yours = await _yours(gateway)
    ok, started = await agent.calls("workflow_start", {"name": WAITS_LONG["name"]})
    assert ok, started
    its_own = _body(started)["run_id"]
    assert ownership.run_mode(store.get(its_own)) is ownership.MemoryMode.TEMPORARY
    await _until(lambda: _state(its_own, "root.children[1]") == "waiting")
    await _until(lambda: _state(yours, "root.children[1]") == "waiting")

    stopped_ok, refused = await agent.calls("workflow_cancel", {"run_id": yours})

    assert not stopped_ok, refused
    assert (
        "this session cannot mutate: it keeps nothing, as a Temporary chat does, so it changes "
        "only a run it started, which keeps nothing as it does" in refused
    ), refused
    assert store.get(yours).status == RunStatus.RUNNING

    paused_ok, paused = await agent.calls("workflow_pause", {"run_id": its_own})
    assert paused_ok, paused
    await _until(lambda: store.get(its_own).status == RunStatus.PAUSED)
    resumed_ok, resumed = await agent.calls("workflow_resume", {"run_id": its_own})
    assert resumed_ok and '"resumed": true' in resumed, resumed
    await _until(lambda: store.get(its_own).status == RunStatus.RUNNING)
    cancelled_ok, cancelled = await agent.calls("workflow_cancel", {"run_id": its_own})
    assert cancelled_ok, cancelled
    assert await _ends(its_own) == RunStatus.CANCELLED

    assert service.cancel_run(yours, supervisor=gateway.supervisor)["ok"]
    assert await _ends(yours) == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_a_temporary_native_agent_keeps_no_workflow_in_your_library(
    gateway, your_library  # noqa: F811 - the imported fixture
):
    """A definition in your library keeps what it keeps. 🔴 Before: the agent's `workflow_author`
    saved it, and its valid `workflow_check` froze its spec on disk as a candidate for later plans,
    though the chat promises to keep nothing."""
    agent = await _temporary_native_agent(gateway)

    saved_ok, saved = await agent.calls(
        "workflow_author", {"name": "kept-for-later", "root": json.dumps(TWO_STEPS["root"])}
    )
    checked_ok, checked = await agent.calls("workflow_check", CHECKED)

    assert not saved_ok, saved
    assert "this session cannot mutate: it keeps nothing, as a Temporary chat does" in saved, saved
    assert your_library == {}
    assert checked_ok and '"valid": true' in checked, checked
    assert [
        c for c in template_store.load_candidates() if c.origin_goal == CHECKED["description"]
    ] == []


@pytest.mark.asyncio
async def test_a_temporary_chat_previews_an_edit_of_any_run_it_can_read(
    gateway,  # noqa: F811 - the imported fixture
):
    """A preview changes nothing, so it is answered for any run the chat can read, through either
    door. 🔴 Before: the gateway's route guarded it as an edit and refused it for your run, while the
    same preview from a native agent was answered."""
    agent = await _temporary_native_agent(gateway)
    yours = await _yours(gateway)
    ops = [{"op": "update_node", "node_id": "after", "fields": {"expr": "later"}}]

    native_ok, native = await agent.calls("workflow_edit_preview", {"run_id": yours, "ops": ops})
    status, answer = await _internal(
        gateway,
        "POST",
        f"/api/workflows/runs/{yours}/edit",
        work=TEMPORARY_KEY,
        body={"ops": ops, "preview_only": True},
    )

    assert native_ok, native
    assert status == 200, answer
    assert store.get(yours).status == RunStatus.RUNNING
    assert service.cancel_run(yours, supervisor=gateway.supervisor)["ok"]
    assert await _ends(yours) == RunStatus.CANCELLED


def test_every_workflow_tool_that_changes_something_is_held_to_the_rule():
    """Every workflow tool that is not read-only asks the one rule in the gateway, under the
    operation the gateway's route for the same call names, so a new one cannot be added without."""
    import inspect

    from personalclaw.workflows import handlers

    tools = {tool["name"] for tool in mcp_workflows._list_tools()}
    assert set(mcp_workflows._CHANGES) == tools - mcp_workflows.READ_ONLY_TOOLS
    routes = inspect.getsource(handlers)
    for operation in set(mcp_workflows._CHANGES.values()):
        assert f'(request, "{operation}"' in routes, operation


# ── a call that names no work ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_call_with_the_internal_credential_that_names_no_session_starts_nothing(
    gateway,  # noqa: F811 - the imported fixture
):
    """🔴 Before: 202, and a run that belonged to nobody and kept everything."""
    status, answer = await _internal(
        gateway, "POST", "/api/workflows/runs", body={"name": "two-steps"}
    )

    assert status == 403, answer
    assert answer["error"]["code"] == "internal_call_names_no_work", answer
    assert "names no work it is for" in answer["error"]["message"], answer
    assert store.list_runs(workflow_name="two-steps")[1] == 0


@pytest.mark.asyncio
async def test_a_call_with_the_internal_credential_that_names_no_session_reads_no_memory(
    gateway,  # noqa: F811 - the imported fixture
):
    """🔴 Before: it read your memory as your own pages do."""
    await _owner_saves_a_fact(gateway)

    status, answer = await _internal(gateway, "GET", "/api/memory/recall?q=coffee+order")

    assert status == 403, answer
    assert answer["error"]["code"] == "internal_call_names_no_work", answer
    assert FACT not in json.dumps(answer)


@pytest.mark.asyncio
async def test_an_agent_clis_tool_server_that_cannot_name_its_chat_makes_no_call(
    gateway,  # noqa: F811 - the imported fixture
):
    """A tool server started with no chat named, which finds no tie to one either, reads nothing,
    starts nothing and says why. 🔴 Before: its recall read your memory, and its start began a run
    that belonged to nobody."""
    await _owner_saves_a_fact(gateway)

    [(recalled_ok, recalled), (started_ok, started)] = await _tool_server(
        gateway,
        ("memory_recall", {"query": "coffee order"}),
        ("workflow_start", {"name": "two-steps"}),
        key="",
    )

    assert not recalled_ok and mcp_core.UNNAMED_CALL in recalled, recalled
    assert FACT not in recalled
    assert not started_ok and mcp_core.UNNAMED_CALL in started, started
    assert store.list_runs(workflow_name="two-steps")[1] == 0


class _PooledCli(AgentProvider):
    """An agent CLI's process from the warm pool, started before any chat."""

    def __init__(self, pid: int) -> None:
        self._pid = pid
        self.key = ""

    @property
    def provider_id(self) -> str:
        return "acp:stub-cli"

    @property
    def pid(self) -> int | None:
        return self._pid

    async def start(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def stream(self, message):  # type: ignore[override]
        if False:  # pragma: no cover - an empty stream
            yield None

    async def approve_tool(self, request_id) -> None: ...

    async def reject_tool(self, request_id) -> None: ...

    def is_process_alive(self) -> bool:
        return True

    def set_session_key(self, session_key: str, channel_id: str | None = None) -> None:
        self.key = session_key


@pytest.mark.asyncio
async def test_a_pooled_agent_cli_names_the_chat_that_claimed_it(tmp_path, monkeypatch):
    """A room member's turn claims a warm-pool process, which no dashboard turn ever ties to its
    session. Its tool server, a child of that process, names the member's session on every call.
    🔴 Before: nothing tied it, so its calls named no session and ran as yours."""
    from personalclaw.config import AppConfig
    from personalclaw.session import SessionManager

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
    pid = os.getpid() + 7919  # a process id the walk below starts from; nothing signals it
    cfg = AppConfig()
    manager = SessionManager(cfg, provider_factory=lambda *a, **kw: _PooledCli(pid + 1))
    manager._pool_size = 1
    pooled = _PooledCli(pid)
    manager._warm_pool.put_nowait((pooled, time.monotonic()))
    member = "room:standup:researcher"
    try:
        provider, _is_new, _resumed = await manager.get_or_create(member)
        assert provider is pooled and pooled.key == member

        # The tool server's own walk up its process tree, from the pooled process.
        monkeypatch.setattr(mcp_core.os, "getppid", lambda: pid)
        monkeypatch.setattr(mcp_core, "_get_ppid", lambda _pid: 1)
        assert mcp_core._resolve_session_key() == member
    finally:
        manager._pool_size = 0
        await manager.close_all()


# ── the gateway's own work still names itself and runs ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_scheduled_scripts_tool_call_reads_what_its_job_may(
    gateway,  # noqa: F811 - the imported fixture
):
    """A scheduled script calls a tool through the gateway, naming its job; the tool's own call back
    to the gateway names that job too, so the gateway does the work."""
    await _owner_saves_a_fact(gateway)

    status, answer = await _internal(
        gateway,
        "POST",
        "/api/tools/invoke",
        work="cron:clock:morning-brief",
        body={"tool": "memory_recall", "arguments": {"query": "coffee order"}},
    )

    assert status == 200 and answer.get("ok"), answer
    assert FACT in answer["output"], answer


@pytest.mark.asyncio
async def test_a_tool_you_try_from_the_tools_page_reads_your_memory(
    gateway,  # noqa: F811 - the imported fixture
):
    """Tools → Try it runs the tool as your own work, and what it asks of the gateway is yours."""
    await _owner_saves_a_fact(gateway)
    token = await _owner_token(gateway)

    async with aiohttp.ClientSession() as http:
        resp = await http.post(
            _url(gateway, "/api/tools/invoke"),
            json={"tool": "memory_recall", "arguments": {"query": "coffee order"}},
            headers={"Authorization": f"Bearer {token}", "X-Session-Key": "dashboard:ui"},
        )
        answer = await resp.json()

    assert resp.status == 200 and answer.get("ok"), answer
    assert FACT in answer["output"], answer


@contextmanager
def _a_tool_that_notes_whose_work_it_runs_as(monkeypatch) -> Iterator[list[str]]:
    """A tool on the route's surface that notes the session its calls back would name."""
    from test_tools_handler import _disable, _install_provider, _RecordingProvider

    seen: list[str] = []

    class _Notes(_RecordingProvider):
        async def invoke(self, name, arguments):
            seen.append(mcp_core._resolve_session_key())
            return await super().invoke(name, arguments)

    monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
    _install_provider(monkeypatch, _Notes("memory_recall", risk="safe"))
    _disable(monkeypatch)
    yield seen


class _AppRequest:
    """A request the gateway admitted with an app's token: the app's identity, and what it sends."""

    def __init__(self, body: dict[str, Any], app: str, headers: dict[str, str]) -> None:
        self._body = body
        self._app = app
        self.headers = headers

    async def json(self) -> dict[str, Any]:
        return self._body

    def get(self, key: str, default: Any = None) -> Any:
        return self._app if key == "app" else default


@pytest.mark.asyncio
async def test_an_apps_tool_call_is_the_apps_work_whatever_it_names(monkeypatch):
    """The tool an app calls through the gateway asks the gateway in turn as the app, so what the
    app was not given stays out of its reach. 🔴 Before: the tool's call back named nothing and ran
    as yours, so an app with a memory tool read your memory without the memory permission."""
    from personalclaw.dashboard.handlers import tools as tools_mod

    monkeypatch.setattr(
        "personalclaw.apps.permissions.checker_for",
        lambda app: SimpleNamespace(can_use_mcp_tool=lambda tool: True),
    )
    with _a_tool_that_notes_whose_work_it_runs_as(monkeypatch) as seen:
        resp = await tools_mod.api_tool_invoke(
            _AppRequest(
                {"tool": "memory_recall", "arguments": {}},
                app="probe-app",
                headers={"X-Session-Key": KEY},
            )
        )

    assert resp.status == 200
    assert seen == ["app:probe-app"]


@pytest.mark.asyncio
async def test_the_cli_names_the_work_of_the_job_it_fires(
    gateway,  # noqa: F811 - the imported fixture
):
    """`personalclaw cron trigger` names the job it fires, so the gateway's route answers it (here,
    that there is no such job) rather than refusing a call that names no work."""
    from personalclaw.schedule_trigger import trigger_schedule_job

    ok, message = await asyncio.to_thread(trigger_schedule_job, "clock:not-a-job")

    assert not ok
    assert "names no work" not in message, message
    assert "not found" in message, message


@pytest.mark.asyncio
async def test_the_cli_replaces_the_sign_in_key_through_the_gateway(
    gateway, capsys  # noqa: F811 - the imported fixture
):
    """`personalclaw auth rotate-key` names its own work, and the gateway replaces the key."""
    from personalclaw.auth.cli import _rotate_key_cmd

    code = await asyncio.to_thread(_rotate_key_cmd, SimpleNamespace(port=gateway.port))

    printed = capsys.readouterr()
    assert code == 0, printed.err
    assert "Replaced the sign-in key" in printed.out, printed


@pytest.mark.asyncio
async def test_a_named_temporary_chats_call_is_still_held_to_its_mode(
    gateway,  # noqa: F811 - the imported fixture
):
    """Naming a session is not a way around its mode: a call made with the internal credential
    for the agent CLI's Temporary chat still reads none of your memory."""
    await _owner_saves_a_fact(gateway)
    gateway.state.get_or_create_session(
        TEMPORARY_KEY.removeprefix("dashboard:"), memory_mode="temporary"
    )
    session_restrictions.mark_own_model(TEMPORARY_KEY, CLI_MODEL)

    status, answer = await _internal(
        gateway, "GET", "/api/memory/recall?q=coffee+order", work=TEMPORARY_KEY
    )

    assert status == 200, answer
    assert FACT not in json.dumps(answer)
