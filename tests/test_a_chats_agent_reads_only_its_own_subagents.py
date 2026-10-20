"""A chat's agent reads only its own chat's subagents.

A subagent (a helper) works for the chat that started it: its task is made of what was said there,
and its report of what it read for that chat. ``subagent_list`` and ``subagent_status`` answered
every caller with every chat's helpers while the gateway held them, so one chat's agent read
another chat's helper ids, tasks and whole reports: a Temporary or an Incognito chat's, and a turn
someone else started in a shared channel thread. A helper's folder, left behind when a restart
stopped it, was read by any chat too. Only the copy of a report kept in its chat after it was
handed on was limited to that chat.

One rule now answers every read, while the gateway holds a helper, from its folder, and from the
copy kept in its chat: a call reads the helpers of the chat its work is for, as its sign-in proves
it (an agent's tools name their chat with the gateway's internal credential), and a helper is the
chat's at the top of the work it was started for. A nested helper's chat is its parent's, and a
batch's task is the chat's whose batch started it. Another chat's helper reads as not found, in the
words an id that never existed reads in. You see every helper.

Each read is made the way its caller makes it, against the gateway's real sign-in and routes on a
live server: the built-in agent's tools (in the gateway, with the chat's session bound), the tool
server an agent CLI runs (``personalclaw mcp-core``'s dispatch, its chat named in its environment),
and the route itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from personalclaw import mcp_core
from personalclaw.dashboard import session_store, token_auth
from personalclaw.dashboard.server import INTERNAL_ROUTES, MIXED_INTERNAL_ROUTES
from personalclaw.subagent import SubagentInfo

SECRET = "the-internal-credential-for-these-tests"
PORT = 10000

#: An ordinary chat, a Temporary one, an Incognito one, and a shared channel's thread.
A, B, C = "chat-1-1700000001", "chat-2-1700000002", "chat-3-1700000003"
KEY = {name: f"dashboard:{name}" for name in (A, B, C)}
THREAD = "slack:T0:C0:1700000000.000100"
APP_NAME = "garden-notes"

#: Chat A's helpers: one done, one running, one the running one started, a batch's task.
A_DONE, A_RUNNING, A_NESTED, A_BATCH = "a1d0e000", "a2b0e000", "a3c0e000", "a4d0e000"
B_DONE, C_DONE, THREAD_DONE, APP_DONE = "b1d0e000", "c1d0e000", "d1d0e000", "e1d0e000"
NEVER = "0badc0de"
REPORT = {
    A_DONE: "Landlord note: the kitchen tap drips; the plumber can come on Friday.",
    B_DONE: "Party plan: the back room at the bakery, Saturday at seven.",
    C_DONE: "Clinic hours: the walk-in opens at eight.",
    THREAD_DONE: "Release summary: two fixes and one new setting.",
    APP_DONE: "Watering plan: the tomatoes every other day.",
}
TASK = {
    A_DONE: "Draft a note to my landlord about the kitchen tap",
    A_RUNNING: "Check when the boiler was last serviced",
    A_NESTED: "Read the boiler manual's service section",
    A_BATCH: "List the bills due this month",
    B_DONE: "Plan a surprise party for Sam",
    C_DONE: "Find the clinic's walk-in hours",
    THREAD_DONE: "Summarize the release notes for the team",
    APP_DONE: "Plan the watering for the vegetable beds",
}
#: Whose each helper is.
CHAT_OF = {
    A_DONE: KEY[A],
    A_RUNNING: KEY[A],
    A_NESTED: KEY[A],
    A_BATCH: KEY[A],
    B_DONE: KEY[B],
    C_DONE: KEY[C],
    THREAD_DONE: THREAD,
    APP_DONE: f"app:{APP_NAME}",
}
_ID = re.compile(r"^([0-9a-f]{8})  \[", re.MULTILINE)


def _helpers(batch_step: str) -> list[SubagentInfo]:
    """Every chat's helpers, as the gateway holds them."""
    parent = {
        A_DONE: KEY[A],
        A_RUNNING: KEY[A],
        A_NESTED: f"subagent:{A_RUNNING}",
        A_BATCH: batch_step,
        B_DONE: KEY[B],
        C_DONE: KEY[C],
        THREAD_DONE: THREAD,
        APP_DONE: f"app:{APP_NAME}",
    }
    infos = []
    for agent_id, task in TASK.items():
        running = agent_id == A_RUNNING
        infos.append(
            SubagentInfo(
                id=agent_id,
                task=task,
                parent_session_key=parent[agent_id],
                done=not running,
                result="" if running else REPORT.get(agent_id, "Done."),
                app=APP_NAME if agent_id == APP_DONE else "",
            )
        )
    return infos


@pytest_asyncio.fixture
async def gateway(tmp_path, monkeypatch):
    """The gateway's sign-in and its subagent routes on a live server, every chat's helpers in its
    table, and both kinds of tool pointed at it."""
    import personalclaw.config.loader as loader
    from personalclaw.dashboard.chat_handlers import api_chat_session_model_reach
    from personalclaw.dashboard.handlers.messaging import api_spawn_list, api_spawn_status
    from personalclaw.dashboard.memory_write_gate import memory_write_middleware
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.subagent import SubagentManager
    from personalclaw.workflows import store
    from personalclaw.workflows.models import OriginKind, RunOrigin, WorkflowRun

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "subagents"
    )
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
    monkeypatch.setattr(mcp_core, "_SESSION_MODES", {})
    token_auth.use_ephemeral_secret(b"a-chats-agent-reads-its-own-helpers")
    token_auth.revoke_all_sessions()

    # The batch chat A started: a run whose step started the batch's task.
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="subagent-batch-1",
            origin=RunOrigin(kind=OriginKind.SUBAGENT_TOOL, session_key=KEY[A]),
        )
    )
    manager = SubagentManager(sessions=MagicMock(), ctx_builder=None)
    for info in _helpers(f"workflow:{run.id}:leaf-0"):
        manager._agents[info.id] = info
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0, subagents=manager)
    state.push_sessions_update = MagicMock()
    for name, mode in ((A, "persistent"), (B, "temporary"), (C, "incognito")):
        state.get_or_create_session(name, memory_mode=mode)

    app = web.Application(
        middlewares=[
            token_auth.token_auth_middleware(
                port=PORT,
                internal_routes=INTERNAL_ROUTES,
                mixed_internal_routes=MIXED_INTERNAL_ROUTES,
                internal_secret=SECRET,
            ),
            memory_write_middleware(),
        ]
    )
    app["state"] = state
    app.router.add_get("/api/chat/sessions/model-reach", api_chat_session_model_reach)
    app.router.add_get("/api/spawn", api_spawn_list)
    app.router.add_get("/api/spawn/{agent_id}", api_spawn_status)
    client = TestClient(TestServer(app))
    await client.start_server()
    base = str(client.make_url("")).rstrip("/")
    monkeypatch.setattr(mcp_core, "_api_base", lambda: base)
    monkeypatch.setattr(mcp_core, "_internal_secret", lambda: SECRET)
    try:
        yield _Gateway(client, state, tmp_path)
    finally:
        await client.close()
        token_auth.revoke_all_sessions()
        token_auth.use_persistent_secret()


class _Gateway:
    """The reads a caller makes, by the path it makes them on."""

    def __init__(self, client: TestClient, state: Any, home: Any) -> None:
        self.client = client
        self.state = state
        self.home = home

    async def _tool(self, path: str, session: str, name: str, args: dict[str, Any]) -> str:
        if path == "native":
            # The built-in agent's tool, in the gateway: the runtime binds the chat's session for
            # the call (`mcp_core.set_current_session_key`), and the provider runs it off the loop.
            from personalclaw.tool_providers.registry import create_subagents_provider

            token = mcp_core.set_current_session_key(session)
            try:
                result = await create_subagents_provider().invoke(name, args)
            finally:
                mcp_core.reset_current_session_key(token)
            return result.output if result.success else f"Error: {result.error}"
        # The tool server an agent CLI runs for the chat, which names it in its environment.
        with patch.dict(os.environ, {"PERSONALCLAW_SESSION_KEY": session}):
            return str(await asyncio.to_thread(mcp_core._call_as_its_session, name, args))

    async def _route(self, path: str, session: str) -> tuple[int, Any]:
        resp = await self.client.get(
            path, headers={"X-Internal-Secret": SECRET, "X-Session-Key": session}
        )
        return resp.status, await resp.json()

    async def listed(self, path: str, session: str) -> tuple[set[str], str]:
        """The helper ids *session*'s agent is listed on *path*, and everything it was shown."""
        if path == "route":
            status, body = await self._route("/api/spawn", session)
            assert status == 200, body
            return {a["id"] for a in body["agents"]}, json.dumps(body)
        said = await self._tool(path, session, "subagent_list", {})
        return set(_ID.findall(said)), said

    async def read(self, path: str, session: str, agent_id: str) -> Any:
        """What *session*'s agent reads of the helper *agent_id* on *path*."""
        if path == "route":
            return await self._route(f"/api/spawn/{agent_id}", session)
        return await self._tool(path, session, "subagent_status", {"agent_id": agent_id})

    async def owners_list(self) -> set[str]:
        """The helper ids the Background agents page is listed: your own signed-in session."""
        owner = token_auth.generate_token("owner", ttl_seconds=300)
        resp = await self.client.get("/api/spawn", headers={"Authorization": f"Bearer {owner}"})
        assert resp.status == 200, await resp.text()
        return {a["id"] for a in (await resp.json())["agents"]}

    def restart(self) -> None:
        """A gateway that holds none of the helpers any more: what is left of them is the folder
        of the one a restart stopped while it worked, and the report handed to its chat."""
        from personalclaw import subagent_report
        from personalclaw.subagent import SubagentManager
        from personalclaw.subagent_persistence import create_agent_folder, write_tombstone

        self.state.subagents = SubagentManager(sessions=MagicMock(), ctx_builder=None)
        create_agent_folder(A_RUNNING, task=TASK[A_RUNNING], parent_session=KEY[A])
        write_tombstone(A_RUNNING, cause="gateway_restart", recovery_action="none")
        assert subagent_report.keep(KEY[A], A_DONE, REPORT[A_DONE])


PATHS = ["native", "tool_server", "route"]
A_HELPERS = {A_DONE, A_RUNNING, A_NESTED, A_BATCH}


# ── a chat lists its own helpers, and no other chat's ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_a_chat_lists_its_own_helpers_and_no_other_chats(gateway, path):
    """🔴 Red on integration: every chat's agent was listed every chat's helpers, with their tasks
    (the Temporary chat was listed the ordinary chat's, the ordinary chat the Incognito chat's)."""
    for chat, own in (
        (KEY[A], A_HELPERS),
        (KEY[B], {B_DONE}),
        (KEY[C], {C_DONE}),
        (THREAD, {THREAD_DONE}),
    ):
        ids, shown = await gateway.listed(path, chat)
        assert ids == own, f"{chat} on {path} was listed {sorted(ids)}"
        for agent_id, task in TASK.items():
            if agent_id not in own:
                assert task[:40] not in shown, f"{chat} on {path} was shown {task!r}"
    # Your own list is every chat's.
    assert await gateway.owners_list() == set(TASK)


# ── another chat's helper reads as one that never existed ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_another_chats_helper_reads_as_an_id_that_never_existed(gateway, path):
    """🔴 Red on integration: the Temporary chat read the ordinary chat's whole report, and the
    ordinary chat the Temporary chat's."""
    never = await gateway.read(path, KEY[B], NEVER)
    with patch("personalclaw.dashboard.handlers.sel") as sel:
        for agent_id in (A_DONE, A_RUNNING, C_DONE, THREAD_DONE):
            theirs = await gateway.read(path, KEY[B], agent_id)
            assert theirs == never, f"{KEY[B]} on {path} read {agent_id}: {theirs}"
    # Each refused read is written down for you, naming who asked and for which subagent.
    rows = [c.kwargs for c in sel.return_value.log_api_access.call_args_list]
    assert [(r["caller"], r["resources"], r["outcome"]) for r in rows] == [
        (KEY[B], f"subagent:{agent_id}", "denied")
        for agent_id in (A_DONE, A_RUNNING, C_DONE, THREAD_DONE)
    ]
    assert await gateway.read(path, KEY[A], B_DONE) == await gateway.read(path, KEY[A], NEVER)
    # The chat that started a helper reads its report whole.
    assert REPORT[A_DONE] in str(await gateway.read(path, KEY[A], A_DONE))
    assert REPORT[B_DONE] in str(await gateway.read(path, KEY[B], B_DONE))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_after_a_restart_another_chats_helper_still_reads_as_never_existed(gateway, path):
    """🔴 Red on integration: the folder of a helper the restart stopped was read by any chat: its
    task, and that it had been stopped."""
    gateway.restart()
    never = await gateway.read(path, KEY[B], NEVER)
    for agent_id in (A_RUNNING, A_DONE):
        theirs = await gateway.read(path, KEY[B], agent_id)
        assert theirs == never, f"{KEY[B]} on {path} read {agent_id}: {theirs}"
    # The chat that started them reads both: the report kept in it, and the stopped one's folder.
    assert REPORT[A_DONE] in str(await gateway.read(path, KEY[A], A_DONE))
    stopped = await gateway.read(path, KEY[A], A_RUNNING)
    assert stopped != never
    if path == "route":
        assert stopped[0] == 200 and stopped[1]["task"] == TASK[A_RUNNING]


# ── whose chat a helper's own call, a batch's task and a call naming no chat are ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_a_helpers_own_call_reads_as_its_chat(gateway, path):
    """🔴 Red on integration: a helper's own call was listed every chat's helpers, and read no
    report kept in its chat once the gateway let the helper that wrote it go."""
    caller = f"subagent:{A_RUNNING}"
    ids, _shown = await gateway.listed(path, caller)
    assert ids == A_HELPERS
    assert await gateway.read(path, caller, B_DONE) == await gateway.read(path, caller, NEVER)
    # A report handed to its chat outlives the helper in the gateway's table.
    from personalclaw import subagent_report

    assert subagent_report.keep(KEY[A], A_DONE, REPORT[A_DONE])
    del gateway.state.subagents._agents[A_DONE]
    assert REPORT[A_DONE] in str(await gateway.read(path, caller, A_DONE))


@pytest.mark.asyncio
async def test_a_batch_task_is_the_chats_whose_batch_started_it(gateway):
    """🔴 Red on integration: a batch's task was listed to every chat, its step's run read by
    nobody."""
    for path in PATHS:
        ids, _shown = await gateway.listed(path, KEY[A])
        assert A_BATCH in ids, path
        ids, _shown = await gateway.listed(path, KEY[B])
        assert A_BATCH not in ids, path


@pytest.mark.asyncio
async def test_a_call_whose_chat_cannot_be_known_reads_no_helper(gateway):
    """🔴 Red on integration: a step of a run nobody can read, an app's token and a request that
    proved no sign-in were each answered every chat's helpers."""
    from personalclaw.dashboard.handlers.messaging import api_spawn_list, api_spawn_status

    ids, _shown = await gateway.listed("route", "workflow:feedf00d:step")
    assert ids == set()
    status, _body = await gateway.read("route", "workflow:feedf00d:step", B_DONE)
    assert status == 404

    def _request(path: str, agent_id: str = "", app: str = "") -> web.Request:
        served = web.Application()
        served["state"] = gateway.state
        request = make_mocked_request(
            "GET", path, match_info={"agent_id": agent_id} if agent_id else {}, app=served
        )
        if app:
            request["app"] = app
        return request

    nobody = json.loads((await api_spawn_list(_request("/api/spawn"))).body)
    assert nobody == {"agents": []}
    apps = json.loads((await api_spawn_list(_request("/api/spawn", app=APP_NAME))).body)
    assert {a["id"] for a in apps["agents"]} == {APP_DONE}
    refused = await api_spawn_status(_request(f"/api/spawn/{A_DONE}", A_DONE, app=APP_NAME))
    assert refused.status == 404 and json.loads(refused.body) == {"error": "not found"}
