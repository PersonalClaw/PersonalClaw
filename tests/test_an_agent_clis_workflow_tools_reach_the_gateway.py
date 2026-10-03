"""An agent on an agent CLI runs your workflows through the gateway, as an agent in a chat does.

An agent CLI reaches PersonalClaw's tools through its tool server (``personalclaw mcp-core``), a
process of its own, which holds no workflow engine and no workflow definitions. So its
``workflow_start`` answered "no workflow definition named …" for a workflow that exists, its
``workflow_list_defs`` listed nothing, a plan from a named template found no template, and a pause
it lifted was recorded where nothing drove the run, which stayed paused.

Every workflow tool call that process makes is now the gateway's, made through the gateway's own
route for it with the internal credential and the session of the chat it serves, and the routes take
it as that chat's agent's: the run it starts is the chat's, so the chat's Stop ends it, and its edit
carries no yes of the owner's.

Driven as an agent CLI drives it: the real gateway, asking for a sign-in, with its workflow
supervisor started, and the real ``personalclaw mcp-core`` process the gateway declares to the CLI,
spoken to over stdio.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from personalclaw import mcp_core, session_restrictions, started_work
from personalclaw.action_providers import services as action_services
from personalclaw.workflows import controller as controller_mod
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import ownership, service, store
from personalclaw.workflows.controller import EngineServices
from personalclaw.workflows.models import (
    TERMINAL_RUN_STATUSES,
    OriginKind,
    RunStatus,
    WorkflowRun,
)
from personalclaw.workflows.watchdog import WorkflowWatchdog

#: The chat the agent CLI's tool server serves, as the gateway names it to that process.
KEY = "dashboard:chat-cli-workflows"
#: A Temporary chat on the agent CLI, and the model its turn runs on (an invented name).
TEMPORARY_KEY = "dashboard:chat-cli-temporary"
CLI_MODEL = "acp:stub-cli"
#: A chat the gateway no longer holds and nothing records, as for a Temporary chat that has ended.
ENDED_KEY = "dashboard:chat-cli-ended"

#: Every auth shortcut a test process might inherit: each would admit a call before the internal
#: credential is looked at.
_AUTH_SHORTCUTS = (
    "PERSONALCLAW_AUTH_MODE",
    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
    "PERSONALCLAW_DEV_NO_AUTH",
    "PERSONALCLAW_SESSION_KEY",
)

#: Two steps, the second bound to the first's output, so a run that completes ran both in order.
TWO_STEPS = {
    "name": "two-steps",
    "description": "Seeds a number, then says it.",
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "seed", "config": {"expr": {"n": 1}}},
            {"kind": "transform", "id": "after", "config": {"expr": "saw {{nodes.seed.output.n}}"}},
        ],
    },
}

#: A run that waits a moment between its steps, long enough to be paused or stopped in the middle.
WAITS = {
    "name": "waits-a-moment",
    "description": "Waits a moment between two steps.",
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "seed", "config": {"expr": "first"}},
            {"kind": "wait", "id": "pause", "config": {"duration_secs": 1}},
            {"kind": "transform", "id": "after", "config": {"expr": "carried on"}},
        ],
    },
}

#: A run that waits long enough to be paused, lifted and stopped while it waits.
WAITS_LONG = {
    "name": "waits-a-minute",
    "description": "Waits a minute between two steps.",
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "seed", "config": {"expr": "first"}},
            {"kind": "wait", "id": "pause", "config": {"duration_secs": 60}},
            {"kind": "transform", "id": "after", "config": {"expr": "carried on"}},
        ],
    },
}

#: A run whose last step is an agent's, which an edit could let approve its own tool calls.
REPORTS = {
    "name": "reports-back",
    "root": {
        "kind": "sequence",
        "id": "main",
        "children": [
            {"kind": "transform", "id": "gather", "config": {"expr": {"said": "notes"}}},
            {
                "kind": "stage",
                "id": "report",
                "label": "Report",
                "config": {"prompt": "Say what changed."},
            },
        ],
    },
}


class _Defs(defs_mod.WorkflowDefProvider):
    """The gateway's workflows, read-only, as a shipped pack serves them."""

    @property
    def name(self) -> str:
        return "cli-workflow-defs"

    @property
    def readonly(self) -> bool:
        return True

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        return [TWO_STEPS, WAITS, WAITS_LONG], 3

    async def get_def(self, name: str):
        return {d["name"]: d for d in (TWO_STEPS, WAITS, WAITS_LONG)}.get(name)


@pytest_asyncio.fixture
async def gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The real gateway (``start_dashboard``, asking for a sign-in), its home in a folder of its
    own and ``HOME`` elsewhere, its workflows registered and its supervisor started on this loop
    and published where the routes and the tools find it, as the gateway publishes it."""
    home = tmp_path / "data"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in _AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)
    # The services the gateway wires are this test's, and go with it.
    monkeypatch.setattr(action_services, "_services", action_services._services)
    monkeypatch.setattr(controller_mod, "TICK_WAKE_SECS", 0.02)
    saved = dict(defs_mod._providers)
    defs_mod._providers.clear()
    defs_mod.register_provider(_Defs())

    runner, state = await server_mod.start_dashboard(sessions=MagicMock(count=0), port=0)
    port = runner.addresses[0][1]
    # The chat the tool server serves is live in the gateway, as a chat is while its agent's turn
    # runs: its mode is read there first (`memory_writes.session_mode`).
    state.get_or_create_session(KEY.removeprefix("dashboard:"), memory_mode="persistent")
    monkeypatch.setenv("PERSONALCLAW_PORT", str(port))
    supervisor = WorkflowWatchdog(state, EngineServices())
    supervisor.start()
    state.workflows = supervisor
    wired = action_services.get_action_services()
    assert wired is not None
    wired.workflows = supervisor
    try:
        yield SimpleNamespace(
            home=home, user_home=user_home, port=port, supervisor=supervisor, state=state
        )
    finally:
        for key in (TEMPORARY_KEY, ENDED_KEY):
            session_restrictions.clear(key)
        await supervisor.stop()
        await runner.cleanup()
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


async def _tool_server(
    gw: SimpleNamespace, *calls: tuple[str, dict[str, Any]], key: str = KEY
) -> list[Any]:
    """*calls* made as an agent CLI makes them: to the process the gateway declares to the CLI
    (``personalclaw mcp-core``, with the session of the chat ``key`` names, the gateway's home and
    its port), over stdio. Each answer is ``(ok, text)``."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(gw.user_home),
        "PERSONALCLAW_HOME": str(gw.home),
        "PERSONALCLAW_PORT": str(gw.port),
        "PERSONALCLAW_SESSION_KEY": key,
    }
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}] + [
        {
            "jsonrpc": "2.0",
            "id": number,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        for number, (name, arguments) in enumerate(calls, start=2)
    ]
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "personalclaw",
        "mcp-core",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdin = "".join(json.dumps(r) + "\n" for r in requests).encode()
    out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=90)
    replies = {m.get("id"): m for m in map(json.loads, out.decode().splitlines()) if m}
    answers = []
    for number in range(2, len(calls) + 2):
        assert number in replies, err.decode()[-3000:]
        result = replies[number]["result"]
        text = " ".join(part.get("text", "") for part in result.get("content", []))
        answers.append((not result.get("isError"), text))
    return answers


def _body(text: str) -> dict[str, Any]:
    """The JSON a successful answer carries, after its summary line when it has one."""
    return json.loads(text[text.index("{") :])


async def _ends(run_id: str, *, within: float = 15.0) -> RunStatus:
    """The status the run ends with, or the one it still reads once *within* seconds are up."""
    deadline = time.monotonic() + within
    while True:
        run = store.get(run_id)
        assert run is not None
        if run.status in TERMINAL_RUN_STATUSES or time.monotonic() > deadline:
            return run.status
        await asyncio.sleep(0.05)


async def _until(condition: Any, *, within: float = 10.0) -> None:
    deadline = time.monotonic() + within
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        await asyncio.sleep(0.02)


def _state(run_id: str, path: str) -> str:
    instance = store.read_state(run_id).get(path)
    return instance.state.value if instance is not None else ""


# ── a run the CLI's agent starts ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_workflow_an_agent_cli_starts_runs_to_completion_as_its_chats(gateway):
    """🔴 Before: "no workflow definition named 'two-steps'", for a workflow the gateway has."""
    [(ok, text)] = await _tool_server(gateway, ("workflow_start", {"name": "two-steps"}))

    assert ok, text
    assert text.startswith("Workflow run started."), text
    run_id = _body(text)["run_id"]
    assert await _ends(run_id) == RunStatus.COMPLETE
    assert store.read_output(run_id, "root.children[1]") == "saw 1"
    run = store.get(run_id)
    assert run is not None
    # Started by the chat the tool server serves, as a start the tool makes in the gateway is.
    assert run.origin.session_key == KEY
    assert run.origin.kind == OriginKind.CHAT


@pytest.mark.asyncio
async def test_the_cli_chats_stop_ends_the_run_its_agent_started(gateway):
    """The run names the chat, so what a Stop of that chat's turn ends is this run.
    🔴 Before: nothing was started to end."""
    [(ok, text)] = await _tool_server(gateway, ("workflow_start", {"name": "waits-a-moment"}))
    assert ok, text
    run_id = _body(text)["run_id"]

    ended = await started_work.end_started(
        subagents=None,
        supervisor=gateway.supervisor,
        owns={KEY}.__contains__,
        clause=started_work.TURN_STOPPED,
    )

    assert ended.runs == 1
    assert await _ends(run_id) == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_a_blocking_start_answers_with_the_runs_ending(gateway):
    [(ok, text)] = await _tool_server(
        gateway, ("workflow_start", {"name": "two-steps", "mode": "blocking"})
    )

    assert ok, text
    body = _body(text)
    assert body["blocking"] is True and body["status"] == RunStatus.COMPLETE.value, body
    assert body["announcement"]["text"].startswith("two-steps → complete"), body


# ── a pause the CLI's agent lifts ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_pause_an_agent_cli_lifts_lets_the_run_carry_on(gateway):
    """Through the route the owner's Resume uses, where the run's controller is woken.
    🔴 Before: the tool server cleared the pause where nothing drove the run, answered "resumed",
    and the run stayed paused."""
    started = await service.start_run(
        name=WAITS["name"], supervisor=gateway.supervisor, skip_preflight=True, session_key=KEY
    )
    run_id = str(started["run_id"])
    await _until(lambda: _state(run_id, "root.children[1]") == "waiting")
    assert service.pause_run(run_id)["ok"]
    await _until(lambda: store.get(run_id).status == RunStatus.PAUSED)

    [(ok, text)] = await _tool_server(gateway, ("workflow_resume", {"run_id": run_id}))

    assert ok and '"resumed": true' in text, text
    assert await _ends(run_id) == RunStatus.COMPLETE
    assert store.read_output(run_id, "root.children[2]") == "carried on"


# ── a Temporary chat on the agent CLI ─────────────────────────────────────────────────────────


def _temporary_chat(gw: SimpleNamespace) -> None:
    """The Temporary chat live in the gateway, its turn on the CLI's model: what the turn engine
    records for a chat that keeps nothing once its turn names the model it runs on
    (`memory_writes.answered_by`)."""
    gw.state.get_or_create_session(
        TEMPORARY_KEY.removeprefix("dashboard:"), memory_mode="temporary"
    )
    session_restrictions.mark_own_model(TEMPORARY_KEY, CLI_MODEL)


@pytest.mark.asyncio
async def test_a_temporary_cli_chat_starts_a_workflow_that_keeps_nothing_on_its_model(gateway):
    """Its run keeps the chat's mode and stays on the model its turn runs on, as a run its agent
    starts in the gateway does. A save of a workflow is still refused: a definition in your
    library keeps what it keeps. 🔴 Before: the start was refused too, "this session cannot
    mutate: it keeps nothing, as a Temporary chat does"."""
    _temporary_chat(gateway)

    [(ok, text), (saved, saved_text)] = await _tool_server(
        gateway,
        ("workflow_start", {"name": "two-steps"}),
        ("workflow_author", {"name": "kept-for-later", "root": json.dumps(TWO_STEPS["root"])}),
        key=TEMPORARY_KEY,
    )

    assert ok, text
    run_id = _body(text)["run_id"]
    assert await _ends(run_id) == RunStatus.COMPLETE
    assert store.read_output(run_id, "root.children[1]") == "saw 1"
    run = store.get(run_id)
    assert run is not None and run.origin.session_key == TEMPORARY_KEY
    assert ownership.run_mode(run) is ownership.MemoryMode.TEMPORARY
    assert ownership.run_model(run) == CLI_MODEL
    assert not saved and "restricted_session" in saved_text, saved_text
    assert "as a Temporary chat does" in saved_text, saved_text


@pytest.mark.asyncio
async def test_a_temporary_cli_chat_controls_its_own_run_and_no_other(gateway):
    """It pauses the run it started, lifts the pause and stops it. A run it did not start (yours)
    it cannot stop: what it asks of a run that keeps things would carry its chat's words into
    work that keeps them. 🔴 Before: its start was refused, so there was no run of its own."""
    _temporary_chat(gateway)
    [(ok, text)] = await _tool_server(
        gateway, ("workflow_start", {"name": WAITS_LONG["name"]}), key=TEMPORARY_KEY
    )
    assert ok, text
    run_id = _body(text)["run_id"]
    yours = await service.start_run(
        name=WAITS_LONG["name"], supervisor=gateway.supervisor, skip_preflight=True
    )
    yours_id = str(yours["run_id"])
    await _until(lambda: _state(run_id, "root.children[1]") == "waiting")
    await _until(lambda: _state(yours_id, "root.children[1]") == "waiting")

    [(paused, paused_text), (refused_ok, refused)] = await _tool_server(
        gateway,
        ("workflow_pause", {"run_id": run_id}),
        ("workflow_cancel", {"run_id": yours_id}),
        key=TEMPORARY_KEY,
    )

    assert paused, paused_text
    await _until(lambda: store.get(run_id).status == RunStatus.PAUSED)
    assert not refused_ok and "restricted_session" in refused, refused
    assert "it changes only a run it started" in refused, refused
    assert store.get(yours_id).status == RunStatus.RUNNING

    [(resumed, resumed_text)] = await _tool_server(
        gateway, ("workflow_resume", {"run_id": run_id}), key=TEMPORARY_KEY
    )
    assert resumed and '"resumed": true' in resumed_text, resumed_text
    await _until(lambda: store.get(run_id).status == RunStatus.RUNNING)

    [(stopped, stopped_text)] = await _tool_server(
        gateway, ("workflow_cancel", {"run_id": run_id}), key=TEMPORARY_KEY
    )
    assert stopped, stopped_text
    assert await _ends(run_id) == RunStatus.CANCELLED
    assert service.cancel_run(yours_id, supervisor=gateway.supervisor)["ok"]
    assert await _ends(yours_id) == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_a_chat_whose_mode_cannot_be_read_still_starts_no_workflow(gateway):
    """A chat the gateway no longer holds and nothing records (a Temporary chat that has ended)
    is refused, saying why, and nothing starts."""
    [(ok, text)] = await _tool_server(
        gateway, ("workflow_start", {"name": "two-steps"}), key=ENDED_KEY
    )

    assert not ok and "restricted_session" in text, text
    assert "the memory setting of the chat it is for cannot be read" in text, text
    assert store.list_runs(workflow_name="two-steps")[1] == 0


# ── the workflows the CLI's agent sees ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_cli_agent_sees_your_workflows_and_an_unknown_one_is_named_unknown(gateway):
    """🔴 Before: the list was empty; the unknown name was answered the same way a known one was."""
    [(listed_ok, listed), (unknown_ok, unknown)] = await _tool_server(
        gateway,
        ("workflow_list_defs", {}),
        ("workflow_start", {"name": "no-such-workflow"}),
    )

    assert listed_ok, listed
    assert {d["name"] for d in _body(listed)["defs"]} == {
        "two-steps",
        "waits-a-moment",
        "waits-a-minute",
    }
    assert not unknown_ok
    assert unknown.startswith("Error [WF_DEF_NOT_FOUND]"), unknown
    assert "no workflow definition named 'no-such-workflow'" in unknown
    assert store.list_runs(workflow_name="no-such-workflow")[1] == 0


@pytest.mark.asyncio
async def test_the_cli_agent_plans_from_one_of_your_workflows(gateway):
    """🔴 Before: "no workflow definition named 'two-steps'. Available: none." """
    [(ok, text)] = await _tool_server(
        gateway, ("workflow_plan", {"goal": "say a number", "template": "two-steps"})
    )

    assert ok, text
    assert text.startswith("Plan for 'say a number' from template 'two-steps'"), text
    body = _body(text)
    assert body["planner"] == "template-v1"
    assert [child["id"] for child in body["proposed_root"]["children"]] == ["seed", "after"]


@pytest.mark.asyncio
async def test_a_check_the_cli_agent_makes_is_its_chats_candidate(gateway):
    """A valid spec it checks is frozen as a candidate private to its chat."""
    from personalclaw.workflows import template_store

    spec = {
        "name": "count-the-notes",
        "description": "Count the notes and say how many",
        "root": json.dumps(TWO_STEPS["root"]),
    }
    [(ok, text)] = await _tool_server(gateway, ("workflow_check", spec))

    assert ok, text
    assert _body(text)["valid"] is True and _body(text)["saved"] is False
    frozen = [c for c in template_store.load_candidates() if c.origin_goal == spec["description"]]
    assert [c.session_id for c in frozen] == [KEY]


# ── what the routes take an agent's call as ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_agents_edit_through_the_gateway_carries_no_yes_of_hers(gateway, monkeypatch):
    """An edit made with the internal credential is an agent's, whatever its body says: one that
    would let a step approve its own tool calls is refused as the engine refuses an agent's, and
    nothing is queued. 🔴 Before: the route did not take the credential; given it, it read the
    body's `confirm` as her yes and queued the edit."""
    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", KEY)
    run = store.create(WorkflowRun(id="", workflow_name=REPORTS["name"], mode="background"))
    store.write_spec(run.id, REPORTS)
    store.request_pause(run.id)
    await gateway.supervisor.launch(run, REPORTS)
    await _until(lambda: store.get(run.id).status == RunStatus.PAUSED)
    act_alone = {"op": "update_node", "node_id": "report", "fields": {"approval_mode": "auto"}}

    reply = await asyncio.to_thread(
        mcp_core._post,
        f"/api/workflows/runs/{run.id}/edit",
        {"ops": [act_alone], "confirm": True},
    )

    assert reply.get("error_detail", {}).get("service_code") == "WF_MUT_NEEDS_OWNER_YES", reply
    controller = gateway.supervisor.controller(run.id)
    assert controller is not None and controller._pending_mutations == []
    assert service.cancel_run(run.id, supervisor=gateway.supervisor)["ok"]


# ── every tool, and nothing of the engine in the tool server ──────────────────────────────────

_STEP = json.dumps({"kind": "transform", "id": "t", "config": {"expr": "x"}})
_OPS = json.dumps([{"op": "skip", "node_id": "t"}])

#: Each tool, called with what it needs, and the one call it makes on the gateway.
_ROUTES = [
    ("workflow_author", {"name": "w", "root": _STEP}, "POST", "/api/workflows/agent-saves"),
    ("workflow_check", {"name": "w", "root": _STEP}, "POST", "/api/workflows/agent-saves"),
    ("workflow_plan", {"goal": "count the notes"}, "POST", "/api/workflows/agent-plans"),
    ("workflow_list_defs", {}, "GET", "/api/workflows"),
    ("workflow_get_def", {"name": "w"}, "GET", "/api/workflows/w"),
    ("workflow_start", {"name": "w"}, "POST", "/api/workflows/runs"),
    ("workflow_status", {"run_id": "a1b2c3d4"}, "GET", "/api/workflows/runs/a1b2c3d4"),
    ("workflow_observe", {"run_id": "a1b2c3d4"}, "GET", "/api/workflows/runs/a1b2c3d4/observe"),
    (
        "workflow_edit",
        {"run_id": "a1b2c3d4", "ops": _OPS},
        "POST",
        "/api/workflows/runs/a1b2c3d4/edit",
    ),
    (
        "workflow_edit_preview",
        {"run_id": "a1b2c3d4", "ops": _OPS},
        "POST",
        "/api/workflows/runs/a1b2c3d4/edit",
    ),
    (
        "workflow_skip",
        {"run_id": "a1b2c3d4", "node_ids": ["t"]},
        "POST",
        "/api/workflows/runs/a1b2c3d4/edit",
    ),
    (
        "workflow_rewind",
        {"run_id": "a1b2c3d4", "node_id": "t"},
        "POST",
        "/api/workflows/runs/a1b2c3d4/rewind",
    ),
    (
        "workflow_run_from",
        {"run_id": "a1b2c3d4", "node_id": "t"},
        "POST",
        "/api/workflows/runs/a1b2c3d4/run-from",
    ),
    ("workflow_fork", {"run_id": "a1b2c3d4"}, "POST", "/api/workflows/runs/a1b2c3d4/fork"),
    ("workflow_start_draft", {"run_id": "a1b2c3d4"}, "POST", "/api/workflows/runs/a1b2c3d4/start"),
    ("workflow_pause", {"run_id": "a1b2c3d4"}, "POST", "/api/workflows/runs/a1b2c3d4/pause"),
    ("workflow_resume", {"run_id": "a1b2c3d4"}, "POST", "/api/workflows/runs/a1b2c3d4/resume"),
    ("workflow_cancel", {"run_id": "a1b2c3d4"}, "POST", "/api/workflows/runs/a1b2c3d4/cancel"),
    (
        "workflow_output",
        {"run_id": "a1b2c3d4", "node_id": "t"},
        "GET",
        "/api/workflows/runs/a1b2c3d4/outputs/t",
    ),
    ("workflow_audit", {}, "GET", "/api/workflows/audit"),
    ("workflow_repair", {}, "GET", "/api/workflows/audit?dry_run=false"),
    ("workflow_manifest", {}, "GET", "/api/workflows/manifest"),
    ("workflow_delete_def", {"name": "w"}, "DELETE", "/api/workflows/w"),
]


def test_the_workflow_routes_import_first_in_a_process_of_their_own(tmp_path):
    """The server reads the operations it opens to the credential as it is imported, from a module
    of their own: read from the routes, which import the dashboard, a process that imported the
    routes first had them half-made, and the import failed."""
    import subprocess

    done = subprocess.run(
        [sys.executable, "-c", "import personalclaw.workflows.handlers"],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(tmp_path),
            "PERSONALCLAW_HOME": str(tmp_path / "data"),
        },
    )

    assert done.returncode == 0, done.stderr[-3000:]


def test_one_workflows_read_opens_no_route_beside_it():
    """The credential opens one workflow's read, and none of the routes the router serves at a name
    beside it (the runs list, the attention list, the suggestions), read off the routes registered:
    a template naming any one segment admitted each."""
    import re

    from aiohttp import web

    from personalclaw.dashboard.token_auth import InternalRoute
    from personalclaw.workflows.agent_routes import AGENT_TOOL_ROUTES
    from personalclaw.workflows.handlers import register_workflow_routes

    app = web.Application()
    register_workflow_routes(app)
    beside = sorted(
        {
            route.resource.canonical
            for route in app.router.routes()
            if route.method == "GET"
            and route.resource is not None
            and re.fullmatch(r"/api/workflows/[^/{]+", route.resource.canonical)
        }
    )
    assert "/api/workflows/runs" in beside, "vacuity: no route beside it was read"
    [read] = [
        InternalRoute.parse(entry)
        for entry in AGENT_TOOL_ROUTES
        if entry.startswith("GET /api/workflows/{name")
    ]
    assert read.admits("GET", "/api/workflows/two-steps")
    assert [path for path in beside if read.admits("GET", path)] == []


def test_every_workflow_tool_makes_its_call_on_the_gateway():
    from personalclaw import mcp_workflows, mcp_workflows_gateway

    listed = {tool["name"] for tool in mcp_workflows._list_tools()}
    assert set(mcp_workflows_gateway.CALLS) == listed
    assert {tool for tool, *_ in _ROUTES} == listed


class _NotHere:
    """The engine, as the tool server an agent CLI runs holds it: not at all."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the tool server asked its own engine for {name!r}")


@pytest.mark.parametrize("tool,arguments,method,path", _ROUTES, ids=[r[0] for r in _ROUTES])
def test_the_tool_server_asks_the_gateway_and_never_an_engine_of_its_own(
    monkeypatch, tool, arguments, method, path
):
    """🔴 Before: each of these was answered by the tool server's own engine, which holds no
    definitions and drives no run."""
    from personalclaw import mcp_workflows, mcp_workflows_gateway

    monkeypatch.setattr(mcp_core, "_SERVES_AN_AGENT_CLI", True)
    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", KEY)
    monkeypatch.setattr(mcp_workflows, "service", _NotHere())
    monkeypatch.setattr(mcp_workflows, "_on_engine", _NotHere().__getattr__)
    monkeypatch.setattr(mcp_workflows, "_run", _NotHere().__getattr__)
    made: list[tuple[str, str]] = []

    def answering(verb: str):
        def call(where: str, body: dict | None = None, **_kw: Any) -> dict:
            made.append((verb, where))
            return {"run_id": "a1b2c3d4"}

        return call

    for name, verb in (("_get", "GET"), ("_post", "POST"), ("_delete", "DELETE")):
        monkeypatch.setattr(mcp_workflows_gateway, name, answering(verb))
        monkeypatch.setattr(mcp_core, name, answering(verb))

    out = mcp_workflows._call_tool(tool, arguments)

    assert not out.startswith("Error"), out
    route, _, asked = path.partition("?")
    assert [(verb, where.partition("?")[0]) for verb, where in made] == [(method, route)], made
    assert asked in made[0][1].partition("?")[2], made


# ── the session a call runs under ──────────────────────────────────────────────────────────────


def test_a_native_agents_call_runs_under_its_chats_session(monkeypatch):
    """The native runtime binds its session around each call it makes in the gateway, where no
    environment names one. 🔴 Before: ""."""
    from personalclaw import mcp_workflows

    monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
    token = mcp_core.set_current_session_key(KEY)
    try:
        assert mcp_workflows._current_session_id() == KEY
    finally:
        mcp_core.reset_current_session_key(token)


def test_an_agent_clis_call_runs_under_the_session_it_was_given(monkeypatch):
    from personalclaw import mcp_workflows

    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", KEY)
    assert mcp_workflows._current_session_id() == KEY


def test_work_run_as_a_sessions_runs_under_that_session(monkeypatch):
    """A request made for a session runs as that session's work (`memory_write_gate`).
    🔴 Before: ""."""
    from personalclaw import mcp_workflows, memory_writes

    monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
    with memory_writes.as_work_of(KEY):
        assert mcp_workflows._current_session_id() == KEY


@pytest.mark.asyncio
async def test_a_spec_a_native_agent_checks_is_its_chats_candidate(tmp_path, monkeypatch):
    """Driven through a real native turn whose scripted model calls the real tool.
    🔴 Before: the candidate named no session, so every other chat's plans matched it."""
    from test_a_declared_read_asks_nobody import _OneCall

    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.native.tools import InProcessMcpToolProvider
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.llm.events import EVENT_TOOL_RESULT
    from personalclaw.workflows import template_store

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
    spec = {
        "name": "count-the-notes",
        "description": "Count the notes and say how many",
        "root": json.dumps(TWO_STEPS["root"]),
    }
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_OneCall("workflow_check", json.dumps(spec)),
        tool_providers=[
            InProcessMcpToolProvider(
                module="personalclaw.mcp_workflows", provider_name="personalclaw-workflows"
            )
        ],
        session_key=KEY,
    )
    runtime.set_task_mode("agent")
    await runtime.start()
    results = [
        str(event.tool_output)
        async for event in runtime.stream("check it")
        if event.kind == EVENT_TOOL_RESULT
    ]

    assert len(results) == 1 and '"valid": true' in results[0], results
    frozen = [c for c in template_store.load_candidates() if c.origin_goal == spec["description"]]
    assert [c.session_id for c in frozen] == [KEY]
