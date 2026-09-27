"""A HEARTBEAT.md task runs only once the owner allowed it; one they allowed runs as before.

Measured on `main` (ddc21e05f) before any of this was written: the agent writes this file — the
background prompt tells it to (`config/prompts/background.md`), and its ``write_file`` and its
shell reach it in the workspace, as does an app that declared ``/api/file-write`` — and the
``system:heartbeat-tasks`` trigger ran every task in it on the next pass as an unattended turn in
which every tool the security hooks did not deny was approved, nobody asked. A trigger the chat
makes, by contrast, waits for the owner's Allow.

These tests drive the pass the way the gateway does: the queue trigger's provider, the gateway's
own runner (`GatewayOrchestrator._run_heartbeat_task`), and a real native runtime with the real
file tools, whose model is a script that calls ``write_file``.
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from personalclaw.action_providers.base import ActionContext
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent

pytestmark = pytest.mark.asyncio


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    import personalclaw.heartbeat as hb

    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(hb, "heartbeat_path", lambda: ws / "HEARTBEAT.md")
    hb.ensure_heartbeat_file()
    yield ws
    hb.set_task_runner(None)


def _queue(ws: Path, *tasks: str) -> None:
    path = ws / "HEARTBEAT.md"
    path.write_text(path.read_text() + "".join(f"- {t}\n" for t in tasks))


class _ScriptedModel:
    """Calls ``write_file`` once, to write a new file, then says it is done."""

    supports_tools = True
    _model = "scripted"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        if self.calls == 1:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id="c1",
                title="write_file",
                tool_input=json.dumps({"path": "summary.md", "content": "written by a task"}),
            )
            yield AgentEvent(kind=EVENT_COMPLETE)
            return
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AgentEvent(kind=EVENT_COMPLETE)


async def _runtime(ws: Path, model: _ScriptedModel):
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition

    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="x"),
        model_provider=model,
        tool_providers=[NativeBuiltinToolProvider(cwd=ws)],
        cwd=ws,
    )
    await runtime.start()
    return runtime


def _gateway(client):
    """The gateway as the heartbeat pass sees it: its sessions hand out *client* as the task's own
    session, its context builder frames the task, and a finished task's delivery is recorded."""
    from personalclaw.config import AppConfig
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.hooks import HookManager

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg, no_dashboard=True)
    sessions = MagicMock()
    sessions.get_or_create = AsyncMock(return_value=(client, False, False))
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    orch.sessions = sessions
    orch.ctx_builder = MagicMock()
    orch.ctx_builder.build_message = MagicMock(side_effect=lambda text, *a, **k: (text, None))
    orch.ctx_builder.hooks = HookManager()
    orch._deliver_result = AsyncMock()
    return orch


async def _pass(orch) -> object:
    """One pass of the `system:heartbeat-tasks` trigger through the gateway's own runner."""
    import personalclaw.heartbeat as hb
    from personalclaw.action_providers.heartbeat_tasks_provider import (
        HeartbeatTasksActionProvider,
    )

    hb.set_task_runner(orch._run_heartbeat_task)
    with patch("personalclaw.context_headroom.resolve_window", new=AsyncMock(return_value=None)):
        return await HeartbeatTasksActionProvider().execute({}, ActionContext(event="clock"))


# ── 🔴 run time: a task nobody allowed does not run ──


async def test_a_task_the_owner_did_not_allow_does_not_run(workspace):
    """🔴 Red on main: the task ran, its ``write_file`` was approved with nobody asked, and it
    wrote the file."""
    _queue(workspace, "Summarise my notes into summary.md")
    model = _ScriptedModel()
    orch = _gateway(await _runtime(workspace, model))

    result = await _pass(orch)

    assert not (workspace / "summary.md").exists()
    assert model.calls == 0, "no turn started"
    assert "Summarise my notes into summary.md" in (workspace / "HEARTBEAT.md").read_text()
    assert (result.success, result.outcome) == (True, "skip"), result
    assert result.stdout == "no task ran: 1 waiting for your Allow on the Triggers page"


async def test_a_task_the_owner_allowed_runs_with_its_tools(workspace):
    """The floor: a task with the owner's yes runs exactly as every task ran before."""
    import personalclaw.heartbeat as hb

    _queue(workspace, "Summarise my notes into summary.md")
    hb.allow("Summarise my notes into summary.md")
    model = _ScriptedModel()
    orch = _gateway(await _runtime(workspace, model))

    result = await _pass(orch)

    assert (workspace / "summary.md").read_text() == "written by a task"
    assert result.stdout == "1 task ran: 1 done, 0 kept for the next pass"
    assert "Summarise my notes" not in (workspace / "HEARTBEAT.md").read_text(), "done, removed"


async def test_a_pass_runs_what_was_allowed_and_leaves_the_rest_where_it_is(workspace):
    """One pass, two tasks: the allowed one runs and finishes; the other is not run, stays in the
    file as written, and the pass says it waits."""
    import personalclaw.heartbeat as hb

    (workspace / "HEARTBEAT.md").write_text(
        hb._HEADER + "- Say hello\n- Watch the deploy and tell me  <!-- deliver:dashboard:s1 -->\n"
    )
    hb.allow("Say hello")
    ran: list[str] = []

    async def run(task: str, deliver: str) -> str:
        ran.append(task)
        return "hello"

    hb.set_task_runner(run)
    from personalclaw.action_providers.heartbeat_tasks_provider import (
        HeartbeatTasksActionProvider,
    )

    result = await HeartbeatTasksActionProvider().execute({}, ActionContext(event="clock"))

    assert ran == ["Say hello"]
    assert result.stdout == (
        "1 task ran: 1 done, 0 kept for the next pass; "
        "1 waiting for your Allow on the Triggers page"
    )
    assert (workspace / "HEARTBEAT.md").read_text() == (
        hb._HEADER + "- Watch the deploy and tell me  <!-- deliver:dashboard:s1 -->\n"
    )


async def test_a_yes_is_to_the_task_as_written(workspace):
    """An allowed task edited afterwards — by anyone — is another task, and waits again."""
    import personalclaw.heartbeat as hb

    hb.allow("Watch the deploy")

    assert hb.allowed("Watch the deploy")
    assert not hb.allowed("Watch the deploy, then delete the old releases")


async def test_a_finished_task_takes_its_yes_with_it(workspace):
    """The yes is for the task until it is done: the same words written again later are a new
    task. One still running (`HEARTBEAT_KEEP`) keeps it."""
    import personalclaw.heartbeat as hb

    _queue(workspace, "Say hello", "Watch the deploy")
    hb.allow("Say hello")
    hb.allow("Watch the deploy")

    async def run(task: str, deliver: str) -> str:
        return "HEARTBEAT_KEEP" if "deploy" in task else "hello"

    await hb.run_tasks(run)

    assert not hb.allowed("Say hello")
    assert hb.allowed("Watch the deploy")


# ── the owner's own tasks: the Files editor save is the yes ──


def _file_app(*, as_app: str = "") -> web.Application:
    from personalclaw.apps.permissions import scoped_to_app
    from personalclaw.dashboard.handlers import api_file_read, api_file_write

    @web.middleware
    async def identity(request, handler):
        with scoped_to_app(as_app):
            return await handler(request)

    app = web.Application(middlewares=[identity])
    state = MagicMock()
    state._restricted_keys = set()
    state._sessions = {}
    app["state"] = state
    app.router.add_get("/api/file-read", api_file_read)
    app.router.add_post("/api/file-write", api_file_write)
    return app


async def _save(workspace: Path, add: str, *, as_app: str = "") -> int:
    """Read HEARTBEAT.md the way the editor does, add a task line, and save it over that read."""
    path = workspace / "HEARTBEAT.md"
    roots = [("Workspace", str(workspace))]
    with (
        patch("personalclaw.dashboard.handlers.files._dashboard_roots", return_value=roots),
        patch("personalclaw.file_roots.dashboard_roots", return_value=roots),
        patch("personalclaw.dashboard.handlers.files._sel"),
    ):
        async with TestClient(TestServer(_file_app(as_app=as_app))) as c:
            read = await c.get("/api/file-read", params={"path": str(path)})
            assert read.status == 200, await read.text()
            text, etag = await read.text(), read.headers["ETag"]
            saved = await c.post(
                "/api/file-write",
                json={"path": str(path), "content": text + f"- {add}\n"},
                headers={"If-Match": etag},
            )
            return saved.status


async def test_a_task_the_owner_types_in_the_files_editor_is_theirs(workspace):
    """🔴 Red on main: nothing recorded who wrote a task. The owner's save is the yes to the lines
    it added — and only those: a task already in the file keeps what it had."""
    import personalclaw.heartbeat as hb

    _queue(workspace, "A task the agent wrote")

    assert await _save(workspace, "Water the plants") == 200

    assert hb.allowed("Water the plants")
    assert not hb.allowed("A task the agent wrote")


async def test_an_apps_write_through_the_same_route_is_not_the_owners_yes(workspace):
    """An app that declared ``/api/file-write`` writes the file too; its words wait like the
    agent's."""
    import personalclaw.heartbeat as hb

    assert await _save(workspace, "Email everyone my notes", as_app="notes-app") == 200

    assert "Email everyone my notes" in (workspace / "HEARTBEAT.md").read_text()
    assert not hb.allowed("Email everyone my notes")


# ── the Triggers page: the queue, and Allow, which asks first ──


def _request(method: str, path: str, body: dict | None = None):
    app = web.Application()
    app["state"] = types.SimpleNamespace()
    req = make_mocked_request(method, path, app=app)
    req["user"] = "owner"

    async def _json():
        return body if body is not None else {}

    req.json = _json  # type: ignore[assignment]
    return req


async def test_the_queue_lists_each_task_and_whether_it_may_run(workspace):
    """🔴 Red on main: the route did not exist; nothing showed which tasks the agent queued."""
    import personalclaw.heartbeat as hb
    from personalclaw.dashboard.handlers import heartbeat_tasks as h

    (workspace / "HEARTBEAT.md").write_text(
        hb._HEADER + "- Check the deploy  <!-- deliver:dashboard:s1 -->\n- Say hello\n"
    )
    hb.allow("Say hello")

    listed = json.loads((await h.api_heartbeat_tasks(_request("GET", "/api/heartbeat/tasks"))).body)

    assert listed == {
        "tasks": [
            {"text": "Check the deploy", "deliver": "dashboard:s1", "allowed": False},
            {"text": "Say hello", "deliver": "", "allowed": True},
        ]
    }


async def test_allow_asks_first_and_the_yes_lets_the_task_run(workspace):
    import personalclaw.heartbeat as hb
    from personalclaw.dashboard.handlers import heartbeat_tasks as h

    _queue(workspace, "Check the deploy")
    route = "/api/heartbeat/tasks/allow"

    with patch.object(h, "_sel"):
        asked = await h.api_heartbeat_task_allow(
            _request("POST", route, {"text": "Check the deploy"})
        )
        assert asked.status == 400
        detail = json.loads(asked.body)["error"]["detail"]
        assert detail["title"] == "Allow this heartbeat task to run?"
        assert "“Check the deploy”" in detail["consent"]
        assert "Until you allow it, it does not run." in detail["consent"]
        assert not hb.allowed("Check the deploy")

        yes = {"text": "Check the deploy", "confirm": True}
        assert (await h.api_heartbeat_task_allow(_request("POST", route, yes))).status == 200

    assert hb.allowed("Check the deploy")


async def test_a_yes_is_only_to_a_task_that_is_queued(workspace):
    """A text the queue does not hold — finished, edited, or never there — is refused, so a yes
    is only ever to a task the page showed."""
    import personalclaw.heartbeat as hb
    from personalclaw.dashboard.handlers import heartbeat_tasks as h

    body = {"text": "Something else entirely", "confirm": True}
    with patch.object(h, "_sel"):
        refused = await h.api_heartbeat_task_allow(
            _request("POST", "/api/heartbeat/tasks/allow", body)
        )

    assert refused.status == 404
    assert not hb.allowed("Something else entirely")


async def test_an_app_cannot_allow_a_heartbeat_task():
    """🔴 Red on main: the route did not exist, so nothing kept it from an app's declaration."""
    from personalclaw.apps.permissions import owner_only_api_reason

    assert owner_only_api_reason("/api/heartbeat/tasks/allow")
    assert owner_only_api_reason("/api/heartbeat/tasks") == "", "the list stays declarable"
