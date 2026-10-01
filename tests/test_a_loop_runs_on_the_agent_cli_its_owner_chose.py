"""A loop runs on the agent CLI its owner chose, and a choice it cannot honour is refused.

The loop composer and Plan Review send the runtime a loop runs on as ``provider`` (``acp:<cli>``)
and ``provider_agent`` (the agent that CLI offered). Driven here through the real create and start
routes, the real session manager, the real provider bridge and the real ACP client, against a
scripted agent CLI: a process that speaks the protocol over stdio and records every frame it is
sent, so the test reads what reached the CLI rather than what PersonalClaw meant to send.

* A Code loop created on a CLI runs its worker there, as the agent that was chosen.
* A runtime that is not set up here is refused when the loop is created or edited, never stored
  to fail later; so is a malformed agent name.
* A kind whose loop runs as a workflow cannot run on a CLI, and says so instead of dropping it.
* Starting a loop whose runtime is not ready is refused with why, and nothing is started.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from test_dashboard_approval import _context_builder, _make_hook_store

from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.chat import run_chat
from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog
from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY
from personalclaw.llm.acp_agent import _factory as acp_factory
from personalclaw.llm.registry import ProviderEntry, get_default_registry, reset_default_registry
from personalclaw.loop import store
from personalclaw.session import SessionManager

RUNTIME = "acp:scripted-cli"
CHOSEN = "careful-coder"

#: The scripted agent CLI. Standard library only; it reads and writes nothing but its record file.
_AGENT = r"""
import json, os, sys

record = open(sys.argv[1], "a", encoding="utf-8", buffering=1)
record.write(json.dumps({"kind": "spawn", "pid": os.getpid()}) + "\n")
SESSION = "scripted-loop-session"


def send(frame):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **frame}) + "\n")
    sys.stdout.flush()


for line in iter(sys.stdin.readline, ""):
    try:
        frame = json.loads(line)
    except ValueError:
        continue
    method, rid, params = frame.get("method"), frame.get("id"), frame.get("params") or {}
    record.write(json.dumps({"kind": "received", "method": method or "", "params": params}) + "\n")
    if method == "initialize":
        send({"id": rid, "result": {"protocolVersion": params.get("protocolVersion", 1),
                                    "agentCapabilities": {"loadSession": False}}})
    elif method == "session/new":
        send({"id": rid, "result": {"sessionId": SESSION, "modes": {
            "currentModeId": "everyday",
            "availableModes": [{"id": "everyday", "name": "Everyday"},
                               {"id": "careful-coder", "name": "Careful coder"}]}}})
    elif method == "session/prompt":
        send({"method": "session/update", "params": {"sessionId": SESSION, "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "Read the brief; starting the first task."}}}})
        send({"id": rid, "result": {"stopReason": "end_turn"}})
    elif method is not None and method.startswith("session/set_"):
        send({"id": rid, "result": {}})
    elif rid is not None and method is not None:
        send({"id": rid, "error": {"code": -32601, "message": "no " + method}})
"""


@pytest.fixture(autouse=True)
def _loop_home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    # The handshake's settle wait for MCP start-up notices: this agent sends none.
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)


@pytest.fixture
def scripted_cli(tmp_path):
    """The scripted agent CLI, set up here as ``acp:scripted-cli``; yields its record file."""
    script = tmp_path / "scripted_cli.py"
    script.write_text(_AGENT)
    record = tmp_path / "wire.jsonl"
    # A registry of its own, knowing only the agent-runtime type (conftest restores the
    # process-wide one afterwards).
    reset_default_registry()
    get_default_registry().register_type(ACP_AGENT_CAPABILITY, acp_factory)
    get_default_registry().register_entry(
        ProviderEntry(
            name=RUNTIME,
            type=ACP_AGENT_CAPABILITY.type,
            model="",
            options={"command": [sys.executable, str(script), str(record)], "dialect": "default"},
            credential=None,
            declared_capabilities=ACP_AGENT_CAPABILITY.capabilities,
        )
    )
    try:
        yield record
    finally:
        reset_default_registry()


@pytest.fixture
def ready(monkeypatch):
    """What the runtime's last Test found: ready, unless a test says otherwise."""
    answer = {"ready": True, "state": "ready", "detail": ""}

    def readiness(entry):
        return {**answer, "login_command": None, "tested_at": "2026-09-30T10:00:00Z"}

    monkeypatch.setattr("personalclaw.agents.runtime_tests.readiness", readiness)
    return answer


class _Nudges:
    """The autonudge service: records what each worker is told; drives nothing on its own."""

    def __init__(self):
        self.added: list[dict] = []

    async def add(self, **kw):
        self.added.append(kw)
        return type("Nudge", (), {"id": f"N{len(self.added)}", **kw})()

    async def update(self, loop_id, **kw):
        return None

    async def remove(self, loop_id):
        return None

    def list_all(self):
        return []

    def get_by_session(self, name):
        return None


def _app(state) -> web.Application:
    app = web.Application()
    app["state"] = state
    return app


async def _call(handler, method: str, path: str, state, body: dict | None = None, **match):
    req = make_mocked_request(method, path, app=_app(state), match_info=match)
    payload = json.dumps(body or {}).encode()

    async def _json():
        return json.loads(payload)

    async def _read():
        return payload

    req.json = _json  # type: ignore[method-assign]
    req.read = _read  # type: ignore[method-assign]
    resp = await handler(req)
    return resp.status, json.loads(resp.body.decode())


def _code_loop(workspace: Path, **fields) -> dict:
    """The body the loop composer sends for a Code loop, plus *fields*."""
    workspace.mkdir(exist_ok=True)
    return {
        "kind": "code",
        "task": "add a health endpoint that reports the build version",
        "name": "Health endpoint",
        "intake_rigor": "minimal",
        "attended": False,
        "workspace_dir": str(workspace),
        "kind_config": {"project_kind": "brownfield"},
        **fields,
    }


def _wire(record: Path, method: str) -> list[dict]:
    if not record.exists():
        return []
    rows = [json.loads(line) for line in record.read_text().splitlines() if line]
    return [r for r in rows if r.get("kind") == "received" and r.get("method") == method]


def _spawns(record: Path) -> int:
    if not record.exists():
        return 0
    return sum(1 for line in record.read_text().splitlines() if '"spawn"' in line)


@pytest.mark.asyncio
async def test_a_code_loop_on_a_cli_runs_its_worker_there_as_the_chosen_agent(
    tmp_path, monkeypatch, scripted_cli, ready
):
    cfg = AppConfig()
    sessions = SessionManager(cfg, provider_factory=cfg.create_provider_factory())
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path / "history"),
    )
    state.context_builder = _context_builder()
    state._hook_store = _make_hook_store()
    state.broadcast_ws = lambda *a, **k: None
    state.push_sessions_update = lambda *a, **k: None
    nudges = _Nudges()
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: nudges)
    try:
        status, created = await _call(
            H.api_loop_create,
            "POST",
            "/api/loops",
            state,
            _code_loop(tmp_path / "ws", provider=RUNTIME, provider_agent=CHOSEN),
        )
        assert status == 201, created
        assert (created["provider"], created["provider_agent"]) == (RUNTIME, CHOSEN)

        status, started = await _call(
            H.api_loop_action,
            "PATCH",
            f"/api/loops/{created['id']}",
            state,
            {"action": "start"},
            id=created["id"],
        )
        assert status == 200, started
        worker = state._sessions[f"loop-{created['id']}"]
        assert (worker.acp_provider, worker.acp_provider_agent) == (RUNTIME, CHOSEN)
        assert _spawns(scripted_cli) == 0, "starting the loop is not the worker's turn"

        # The worker's first cycle, as the autonudge service runs it.
        assert worker.enqueue_or_run_prompt(nudges.added[0]["message"], run_chat, state)
        await asyncio.wait_for(asyncio.shield(worker.task), timeout=30)

        assert _spawns(scripted_cli) == 1, "the worker's turn did not run on the chosen CLI"
        assert [f["params"].get("modeId") for f in _wire(scripted_cli, "session/set_mode")] == [
            CHOSEN
        ]
        assert len(_wire(scripted_cli, "session/prompt")) == 1
        said = [m["content"] for m in worker.messages if m.get("role") == "assistant"]
        assert any("starting the first task" in s for s in said), worker.messages
    finally:
        await sessions.close_all()


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"provider": "acp:no-such-cli"}, "isn't set up here"),
        ({"provider": "some-cli"}, "isn't an agent runtime"),
        ({"provider": RUNTIME, "provider_agent": "two words"}, "isn't an agent name"),
    ],
)
@pytest.mark.asyncio
async def test_a_runtime_the_loop_cannot_run_on_is_refused_when_it_is_created(
    tmp_path, scripted_cli, ready, fields, says
):
    state = object()
    status, body = await _call(
        H.api_loop_create, "POST", "/api/loops", state, _code_loop(tmp_path / "ws", **fields)
    )
    assert status == 400, body
    assert any(says in e for e in body["errors"]), body
    assert store.list_redacted() == [], "a refused loop was stored"

    status, verdict = await _call(
        H.api_loop_validate,
        "POST",
        "/api/loops/validate",
        state,
        _code_loop(tmp_path / "ws", **fields),
    )
    assert verdict["can_start"] is False and any(says in e for e in verdict["errors"]), verdict


@pytest.mark.asyncio
async def test_plan_review_cannot_move_a_loop_onto_a_runtime_that_is_not_set_up(
    tmp_path, scripted_cli, ready
):
    state = object()
    status, created = await _call(
        H.api_loop_create, "POST", "/api/loops", state, _code_loop(tmp_path / "ws")
    )
    assert status == 201, created
    status, body = await _call(
        H.api_loop_update,
        "PUT",
        f"/api/loops/{created['id']}",
        state,
        {"provider": "acp:no-such-cli", "provider_agent": ""},
        id=created["id"],
    )
    assert status == 400, body
    assert store.get(created["id"]).provider == ""

    status, moved = await _call(
        H.api_loop_update,
        "PUT",
        f"/api/loops/{created['id']}",
        state,
        {"provider": RUNTIME, "provider_agent": CHOSEN},
        id=created["id"],
    )
    assert status == 200, moved
    assert (moved["provider"], moved["provider_agent"]) == (RUNTIME, CHOSEN)


@pytest.mark.asyncio
async def test_a_loop_that_runs_as_a_workflow_says_it_cannot_run_on_a_cli(
    tmp_path, scripted_cli, ready
):
    body = {
        "kind": "general",
        "task": "tidy the notes folder into one index page",
        "provider": RUNTIME,
        "provider_agent": CHOSEN,
    }
    status, verdict = await _call(
        H.api_loop_validate, "POST", "/api/loops/validate", object(), body
    )
    assert verdict["can_start"] is False, verdict
    assert any("runs as a workflow" in e for e in verdict["errors"]), verdict
    # Ordinary content still validates: the same loop on PersonalClaw is fine.
    status, verdict = await _call(
        H.api_loop_validate,
        "POST",
        "/api/loops/validate",
        object(),
        {k: v for k, v in body.items() if not k.startswith("provider")},
    )
    assert verdict["can_start"] is True, verdict


@pytest.mark.asyncio
async def test_starting_a_loop_whose_runtime_is_not_ready_says_why_and_starts_nothing(
    tmp_path, monkeypatch, scripted_cli, ready
):
    nudges = _Nudges()
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: nudges)
    state = object()
    status, created = await _call(
        H.api_loop_create,
        "POST",
        "/api/loops",
        state,
        _code_loop(tmp_path / "ws", provider=RUNTIME, provider_agent=CHOSEN),
    )
    assert status == 201, created
    ready.update(ready=False, state="needs_login", detail="Sign in to the CLI first.")

    status, body = await _call(
        H.api_loop_action,
        "PATCH",
        f"/api/loops/{created['id']}",
        state,
        {"action": "start"},
        id=created["id"],
    )
    assert status == 422, body
    assert (
        "This loop runs on Scripted Cli, which isn't ready: Sign in to the CLI first."
        in body["error"]
    ), body
    assert nudges.added == [], "a worker was started on a runtime that is not ready"
    assert store.get(created["id"]).status == "ready"
    assert _spawns(scripted_cli) == 0
