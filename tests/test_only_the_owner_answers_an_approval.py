"""Only you answer an approval, and never the party that asked for it (`approval_answer`).

Measured on `main` before this was written, each of these answered what it should not:

* an agent's `workflow_resume` tool approved the gate its own run was waiting on;
* an agent's `triage_rules` tool posted an `approve` rule, which answers every matching proposal
  of the proactive digest before it is asked;
* an app that declared `/api/proactive` answered the digest's proposals;
* an agent's tool, which presents the gateway's internal secret, answered a chat's pending tool
  call through `POST /api/approvals/{id}/approve` and the chat card's own route, answered a run's
  gate through its resume and confirm routes, and taught an approve rule through its route. Those
  routes are not internal paths, so in the default auth mode the middleware refuses the secret
  first. The requests here are admitted the way `auth_mode=none` and the local-network bypass
  admit every loopback caller, as you, which is where the handler's own refusal is the one that
  holds;
* a control-bridge action waiting for confirmation had no way for you to confirm it at all, only
  the asking client's own `/confirm` (tested in `test_ea4_control_bridge.py`).

The same rule is held at the other doors in their own suites: a trigger's question
(`test_a_trigger_that_stops_for_you_asks.py`), an Inbox proposal (`test_inbox_app_proposals.py`),
a trigger answering an approval gate (`test_triggers_resume_target.py`), an app's relay through
`/api/approvals` (`test_apps_cannot_post_into_your_chats.py`) and a run's `/confirm`
(`test_apps_cannot_run_code_or_bypass_approvals.py`).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state
from test_apps_cannot_post_into_your_chats import _gateway, _pending
from test_apps_cannot_run_code_or_bypass_approvals import APP, _home, _install

#: What an agent's tool presents: the gateway's internal secret, for the session it acts for.
AGENT_TOOL = {"X-Internal-Secret": "s", "X-Session-Key": "dashboard:mine"}


@pytest.fixture
def refused_rows(monkeypatch):
    """The audit rows a refused answer writes (`approval_answer.refuse` reads `sel` per call)."""
    rows: list[dict] = []
    monkeypatch.setattr(
        "personalclaw.sel.sel",
        lambda: SimpleNamespace(
            log_api_access=lambda **kw: rows.append(kw),
            log_tool_invocation=lambda **kw: None,
        ),
    )
    return lambda: [r for r in rows if r.get("operation") == "approval.answer_refused"]


# ── the rule ────────────────────────────────────────────────────────────────────────────────


def test_only_you_and_you_on_a_channel_answer() -> None:
    from personalclaw import approval_answer as A

    assert A.refusal(A.YOU) == ""
    assert A.refusal(A.on_channel("telegram")) == ""
    for other in (
        A.app("companion"),
        A.agent("dashboard:c1"),
        A.bridge("client-1"),
        A.trigger("t1"),
        A.run("r1"),
        A.Principal(A.UNKNOWN),
    ):
        assert A.refusal(other), f"{other.label} answered an approval"


def test_a_trigger_answers_an_event_gate_and_nothing_else() -> None:
    from personalclaw import approval_answer as A

    assert A.refusal(A.trigger("t1"), event=True) == ""
    assert A.refusal(A.trigger("t1"))
    assert A.refusal(A.agent("dashboard:c1"), event=True), "an event gate is not an agent's"


def test_the_party_that_asked_never_answers() -> None:
    from personalclaw import approval_answer as A

    assert A.refusal(A.on_channel("slack"), asked_by="channel:slack") == A.ASKER_REFUSAL


def test_a_request_is_the_principal_it_proved() -> None:
    """The internal secret is an agent's in every auth mode, even where the middleware adopted a
    user for it (`auth_mode=none`, the local-network bypass)."""
    from personalclaw import approval_answer as A

    def req(headers: dict | None = None, **items: Any) -> Any:
        class _R(dict):
            pass

        r = _R(items)
        r.headers = headers or {}
        return r

    assert A.of_request(req(user="owner")) == A.YOU
    assert A.of_request(req(user="owner", app="companion")) == A.app("companion")
    assert A.of_request(req(AGENT_TOOL, user="dev-local")) == A.agent("dashboard:mine")
    assert A.of_request(req()).kind == A.UNKNOWN


# ── an agent's tool answers no chat approval ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_agents_tool_answers_no_approval_through_the_approvals_list(
    tmp_path, refused_rows
) -> None:
    from personalclaw.dashboard.approval_state import chat_approval_id
    from personalclaw.dashboard.handlers.sessions import api_approval_resolve

    state = _make_state(tmp_path)
    mine, pending = _pending(state, "mine")
    approval_id = chat_approval_id(mine.key, "req-1")
    state._pending_approvals[approval_id] = {
        "id": approval_id,
        "session": mine.key,
        "request_id": "req-1",
        "asked_by": "agent:dashboard:mine",
    }
    route = ("POST", "/api/approvals/{id}/{action}", api_approval_resolve)
    async with TestClient(TestServer(_gateway(state, "", [route]))) as client:
        resp = await client.post(f"/api/approvals/{approval_id}/approve", headers=AGENT_TOOL)
        body = await resp.json()
    assert resp.status == 403, body
    assert body["error"]["code"] == "approval_owner_only"
    assert not pending.done(), "an agent's tool approved a tool call"
    (row,) = refused_rows()
    assert row["caller"] == "agent:dashboard:mine"
    assert row["resources"] == f"approval:{approval_id} asked_by=agent:dashboard:mine"


@pytest.mark.asyncio
async def test_an_agents_tool_answers_no_approval_through_the_chat_card(tmp_path) -> None:
    from personalclaw.dashboard import chat

    state = _make_state(tmp_path)
    _session, pending = _pending(state, "mine")
    route = ("POST", "/api/chat/sessions/{session}/approve", chat.api_chat_session_approve)
    async with TestClient(TestServer(_gateway(state, "", [route]))) as client:
        resp = await client.post(
            "/api/chat/sessions/mine/approve",
            json={"action": "approved", "request_id": "req-1"},
            headers=AGENT_TOOL,
        )
        body = await resp.json()
    assert resp.status == 403, body
    assert not pending.done(), "an agent's tool approved its own chat's call"


# ── a workflow's gate ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """An isolated workflow home, patched where the store imports it (see
    `test_triggers_resume_target.isolated`)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows.native_defs import register_native_provider

    saved = dict(defs_mod._providers)
    defs_mod._providers.clear()
    register_native_provider()
    try:
        yield home
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


async def _parked_on_an_approval(monkeypatch):
    from test_triggers_resume_target import APPROVAL_SPEC, _attach, _parked_run

    run, watchdog = await _parked_run(APPROVAL_SPEC)
    _attach(monkeypatch, watchdog)
    return run, watchdog


@pytest.mark.anyio
async def test_an_agent_cannot_answer_its_own_runs_gate(isolated, monkeypatch, refused_rows):
    """🔴 `workflow_resume` from inside the run (or any agent): the gate still waits for you. The
    tool says who answers it, and the attempt is audited. Lifting a pause still works."""
    from personalclaw import mcp_workflows
    from personalclaw.workflows import store as wstore
    from personalclaw.workflows.human_input import list_continuations
    from personalclaw.workflows.models import RunStatus

    run, _watchdog = await _parked_on_an_approval(monkeypatch)
    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", f"workflow:{run.id}:worker")

    out = mcp_workflows._call_tool_inner("workflow_resume", {"run_id": run.id, "answer": "true"})

    assert "WF_RESUME_NOT_OWNER" in str(out), out
    assert "Only the owner answers" in str(out)
    assert len(list_continuations(run.id)) == 1, "the agent answered the gate"
    assert wstore.get(run.id).status == RunStatus.NEEDS_INPUT
    (row,) = refused_rows()
    assert row["caller"] == f"agent:workflow:{run.id}:worker"
    assert row["resources"] == f"gate:{run.id} asked_by=run:{run.id}"

    lifted = mcp_workflows._call_tool_inner("workflow_resume", {"run_id": run.id})
    assert "resumed" in str(lifted) and "WF_RESUME_NOT_OWNER" not in str(lifted)
    assert len(list_continuations(run.id)) == 1, "lifting a pause answered the gate"


@pytest.mark.anyio
async def test_an_agents_tool_cannot_answer_a_gate_over_http(isolated, monkeypatch) -> None:
    from personalclaw.workflows import handlers as wf_handlers
    from personalclaw.workflows.human_input import list_continuations

    run, watchdog = await _parked_on_an_approval(monkeypatch)
    state = SimpleNamespace(workflows=watchdog, _restricted_keys=set(), _sessions={})

    @web.middleware
    async def signed_in(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[signed_in])
    app["state"] = state
    app.router.add_post("/api/workflows/runs/{run_id}/resume", wf_handlers.api_run_resume)
    app.router.add_post("/api/workflows/runs/{run_id}/confirm", wf_handlers.api_run_confirm)
    async with TestClient(TestServer(app)) as client:
        resume = await client.post(
            f"/api/workflows/runs/{run.id}/resume",
            json={"answer": True},
            headers={"X-Internal-Secret": "s"},
        )
        confirm = await client.post(
            f"/api/workflows/runs/{run.id}/confirm",
            json={"verb": "approve"},
            headers={"X-Internal-Secret": "s"},
        )
        resume_body, confirm_body = await resume.json(), await confirm.json()
    assert resume.status == 403, resume_body
    assert resume_body["error"]["code"] == "approval_owner_only"
    assert confirm.status == 403, confirm_body
    assert "approved" not in confirm_body["error"].get("detail", {}), "a refusal read as approved"
    assert len(list_continuations(run.id)) == 1, "the gate was answered by an agent's tool"


# ── a control-bridge action: you confirm it, in PersonalClaw ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["you", "app", "agent"])
async def test_you_confirm_a_bridge_action_and_nobody_else_can(tmp_path, monkeypatch, caller):
    """The owner's door (`POST /api/external-access/bridge/confirmations/{id}`) behind the real
    app-permission middleware: you confirm it and it runs once; an app is refused at the door
    (`/api/external-access` is yours); an agent's tool is refused by the answer rule."""
    from personalclaw.dashboard.handlers.external_access import api_bridge_confirmation
    from personalclaw.inbound import bridge

    ran: list[dict] = []

    async def _record(state, params):
        ran.append(params)
        return {"ok": True}

    monkeypatch.setattr(
        bridge,
        "_REGISTRY",
        tuple(
            bridge.Action(**{**a.__dict__, "handler": _record}) if a.name == "create_task" else a
            for a in bridge.actions()
        ),
    )
    monkeypatch.setattr("personalclaw.inbox.resolve_attention_items", lambda s, refs, **_k: 1)
    bridge._pending.clear()
    action = next(a for a in bridge.actions() if a.name == "create_task")
    token = bridge._mint_confirmation(action, {"title": "t"}, asked_by="bridge:surface")
    route = (
        "POST",
        "/api/external-access/bridge/confirmations/{id}",
        api_bridge_confirmation,
    )
    state = _make_state(tmp_path)
    with _home(tmp_path):
        _install(tmp_path, APP, {"api": ["/api/external-access"]})
        gw = _gateway(state, APP if caller == "app" else "", [route])
        async with TestClient(TestServer(gw)) as client:
            resp = await client.post(
                f"/api/external-access/bridge/confirmations/{token}",
                json={"confirm": True},
                headers=AGENT_TOOL if caller == "agent" else {},
            )
            text = await resp.text()
    if caller == "you":
        assert resp.status == 200, text
        assert json.loads(text)["status"] == "ok"
        assert ran == [{"title": "t"}]
    else:
        assert resp.status == 403, text
        assert ran == []
        assert bridge.pending_count() == 1, "a refused answer spent your confirmation"
    bridge._pending.clear()


# ── the digest's proposals ──────────────────────────────────────────────────────────────────


def _rule_request(headers: dict, body: dict) -> Any:
    from aiohttp.test_utils import make_mocked_request

    req = make_mocked_request("POST", "/api/memory/approval-rules", headers=headers)
    req.app["state"] = object()
    req["user"] = "owner"

    async def _json() -> dict:
        return body

    req.json = _json  # type: ignore[method-assign]
    return req


@pytest.mark.asyncio
async def test_an_agent_teaches_no_approve_rule(tmp_path, monkeypatch, refused_rows) -> None:
    """An approve rule answers every matching proposal before it is asked. An agent's tool that
    could write one would approve the digest's work ahead of time; a deny rule only takes away."""
    from personalclaw.dashboard.handlers import memory as mem_handlers
    from personalclaw.memory_service import MemoryService
    from personalclaw.vector_memory import VectorMemoryStore

    store = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    store.init()
    monkeypatch.setattr(
        mem_handlers, "_get_service", lambda state: MemoryService.over_vector_store(store)
    )
    monkeypatch.setattr(mem_handlers, "_is_restricted_session", lambda state, req: False)
    try:
        approve = await mem_handlers.api_memory_approval_rule_add(
            _rule_request(AGENT_TOOL, {"pattern": "send:to:anyone", "verdict": "approve"})
        )
        deny = await mem_handlers.api_memory_approval_rule_add(
            _rule_request(AGENT_TOOL, {"pattern": "send:to:anyone", "verdict": "deny"})
        )
        yours = await mem_handlers.api_memory_approval_rule_add(
            _rule_request({}, {"pattern": "archive:sender:x", "verdict": "approve"})
        )
    finally:
        store.close()
    assert approve.status == 403, approve.body
    assert json.loads(approve.body)["error"]["code"] == "approval_owner_only"
    assert deny.status == 200, deny.body
    assert yours.status == 200, yours.body
    assert [r["caller"] for r in refused_rows()] == ["agent:dashboard:mine"]


def test_the_triage_rules_tool_teaches_no_approve_rule(monkeypatch) -> None:
    from personalclaw import mcp_memory

    posted: list[dict] = []
    monkeypatch.setattr(mcp_memory, "_post", lambda path, payload: posted.append(payload) or {})
    out = mcp_memory._call_tool_inner(
        "triage_rules", {"action": "add", "pattern": "send:to:anyone", "verdict": "approve"}
    )
    assert "only the owner teaches an approve rule" in str(out)
    assert posted == [], "the tool wrote an approve rule"


@pytest.mark.asyncio
async def test_an_app_answers_none_of_the_digests_proposals(tmp_path, refused_rows) -> None:
    from personalclaw.dashboard.handlers.proactive import api_proactive_reply

    state = _make_state(tmp_path)
    route = ("POST", "/api/proactive/digest/reply", api_proactive_reply)
    with _home(tmp_path):
        _install(tmp_path, APP, {"api": ["/api/proactive"]})
        async with TestClient(TestServer(_gateway(state, APP, [route]))) as client:
            resp = await client.post(
                "/api/proactive/digest/reply", json={"run_id": "r-digest", "text": "approve 1"}
            )
            body = await resp.json()
    assert resp.status == 403, body
    assert body["error"]["code"] == "approval_owner_only"
    (row,) = refused_rows()
    assert row["caller"] == f"app:{APP}"
    assert row["resources"] == "digest:r-digest asked_by=run:r-digest"


def test_every_door_answers_a_refusal_the_same_way() -> None:
    """One wire code for a refused answer, whichever door refused it."""
    from personalclaw.http_errors import HTTP_ERROR_CODES
    from personalclaw.workflows.handlers import _STATUS_MAP

    assert "approval_owner_only" in HTTP_ERROR_CODES
    assert _STATUS_MAP["WF_RESUME_NOT_OWNER"] == (403, "approval_owner_only")
