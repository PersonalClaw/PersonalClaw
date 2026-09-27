"""The operator ceiling is a ceiling: no agent, spawn argument or switch loosens it.

``governance/ceiling.json``'s ``approval`` scope is the operator's hard bound. Under
``{"approval": {"value": "ask"}}`` no run on this machine approves a call without a person. Only the
subagent's own tool-approval grant consulted it (and an app's conversation), so every other grant
approved on its own:

* 🔴 an agent spawned ``approval_mode: "auto"`` skipped its spawn approval, and so did YOLO;
* a session given a policy that never asks (``auto``/``yolo``) handed it to its runtime as is;
* the gateway's relay approved a background call on YOLO, a trusted chat or the ``--approval``
  flag without asking the ceiling;
* the chat's Trust, YOLO and Trust reads switches, a card's wider scope, an agent's "Always
  allow", a Trust chat's own calls and an operator's hook pattern all approved;
* an unattended ACP session kept ``bypassPermissions``; an unattended desktop drive needed no
  standing grant; a subagent's announce turn in your chat, and a background turn given
  ``AUTO_APPROVE``, approved every call; a workflow gate approved on its run's policy or a
  remembered "always allow"; the triage digest ran its trivial actions.

Each of those now asks :func:`approval_grants.stands`, and a refusal is audited
(``approval.grant_refused``), so a downgraded grant is never indistinguishable from none. Every test
here has a control: the same grant, with no ceiling, still approves — so a test cannot pass by the
grant never having worked at all.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_a_tool_call_is_audited_as_it_was_decided import (
    ASKS,
    _answer_when_asked,
    _background,
    _chat,
    _decided,
    _rows,
    _subagents,
)

from personalclaw.guardrails import ceiling as C
from personalclaw.sel import sel


@pytest.fixture
def ceiling(tmp_path, monkeypatch):
    """Install an operator ceiling for this test: ``ceiling("ask")``, ``ceiling("hook_based")``."""

    def install(value: str = "", **scopes: Any) -> None:
        if value:
            scopes["approval"] = {"value": value}
        path = tmp_path / "operator" / "ceiling.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "scopes": scopes}))
        monkeypatch.setenv(C.CEILING_PATH_ENV, str(path))
        C.reset_ceiling()

    C.reset_ceiling()
    yield install
    C.reset_ceiling()


def _refusals(grant: str) -> list[dict[str, Any]]:
    return [
        r
        for r in sel().recent(500)
        if r.get("operation") == "approval.grant_refused"
        and f"grant={grant}," in str(r.get("resources", ""))
    ]


# ── the spawn gate ────────────────────────────────────────────────────────────────────────────


def _manager(**kw: Any):
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentManager

    sessions = _mock_sessions()
    sessions.get_approval_policy = MagicMock(return_value="")
    return SubagentManager(sessions=sessions, ctx_builder=_mock_ctx_builder(), **kw)


async def _spawned(manager: Any, **kw: Any) -> None:
    with patch("personalclaw.subagent.SubagentManager._run", new=AsyncMock()):
        info = manager.spawn("rewrite the report", **kw)
        assert info is not None and not info.error, info
        await asyncio.wait_for(manager._tasks[info.id], timeout=10)


@pytest.mark.asyncio
async def test_an_auto_spawn_asks_under_an_ask_ceiling(ceiling):
    asked = AsyncMock(return_value=False)
    await _spawned(_manager(on_spawn_approval=asked, is_yolo=lambda: False), approval_mode="auto")
    asked.assert_not_awaited()  # the control: with no ceiling the grant starts it

    ceiling("ask")
    await _spawned(_manager(on_spawn_approval=asked, is_yolo=lambda: False), approval_mode="auto")
    asked.assert_awaited_once()
    assert _refusals("approval_mode"), "the refused grant must be audited"


@pytest.mark.asyncio
async def test_yolo_does_not_start_a_spawn_under_an_ask_ceiling(ceiling):
    asked = AsyncMock(return_value=False)
    await _spawned(_manager(on_spawn_approval=asked, is_yolo=lambda: True))
    asked.assert_not_awaited()

    ceiling("ask")
    await _spawned(_manager(on_spawn_approval=asked, is_yolo=lambda: True))
    asked.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_auto_agent_s_calls_ask_the_relay_under_an_ask_ceiling(ceiling):
    """A trigger agent spawned ``approval_mode: auto`` and granted writes: its runtime answered
    each ask itself. Under an ``ask`` ceiling its calls ask you, through the relay, rather than
    running — or being declined with nobody asked."""
    from personalclaw.subagent import SubagentInfo

    ceiling("ask")
    relay = AsyncMock(return_value=False)
    manager, tools = _subagents(ASKS, relay=relay)
    info = SubagentInfo(id="sa-c", task="t", approval_mode="auto", capability_class="mutating")
    await asyncio.wait_for(manager._run_inner(info, "subagent:sa-c"), timeout=20)
    relay.assert_awaited_once()
    assert tools.ran == []
    assert [_decided(r) for r in _rows(ASKS)] == [("rejected", "you")], _rows(ASKS)


@pytest.mark.asyncio
async def test_a_read_only_run_s_standing_grant_does_not_admit_a_write_tool():
    """An auto-fired spawn is a research run: read tools only, whatever approves its calls.

    🔴 Its runtime answered the ask itself (the grant stands, so its policy says ``auto``), and
    the class was enforced only where the host answers an ask, so the write ran.
    """
    from personalclaw.subagent import SubagentInfo

    manager, tools = _subagents(ASKS)
    await asyncio.wait_for(
        manager._run_inner(
            SubagentInfo(id="sa-ro", task="t", approval_mode="auto"), "subagent:sa-ro"
        ),
        timeout=20,
    )
    assert tools.ran == []
    assert [_decided(r) for r in _rows(ASKS)] == [("denied", "tool_grants")], _rows(ASKS)


@pytest.mark.asyncio
async def test_the_ceiling_s_tool_allowlist_holds_under_a_standing_grant(ceiling):
    """A run granted writes (``mutating``) and approving its own calls still uses only the tools
    the operator's ``tools`` scope allows."""
    from personalclaw.subagent import SubagentInfo

    ceiling(tools={"allow": ["read_*"]})
    manager, tools = _subagents(ASKS)
    info = SubagentInfo(id="sa-cl", task="t", approval_mode="auto", capability_class="mutating")
    await asyncio.wait_for(manager._run_inner(info, "subagent:sa-cl"), timeout=20)
    assert tools.ran == []
    assert [_decided(r) for r in _rows(ASKS)] == [("denied", "tool_grants")], _rows(ASKS)


# ── a session's policy ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_policy_that_never_asks_is_not_handed_to_the_runtime_under_an_ask_ceiling(ceiling):
    from test_native_runtime import _defn, _ScriptedModel

    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.config import AppConfig
    from personalclaw.session import SessionManager

    built: dict[str, Any] = {}

    def factory(key: Any = None, **_kw: Any) -> NativeAgentRuntime:
        built[key] = NativeAgentRuntime(definition=_defn(), model_provider=_ScriptedModel([]))
        return built[key]

    sessions = SessionManager(AppConfig(), provider_factory=factory)
    await sessions.get_or_create("subagent:free", approval_policy="auto")
    assert built["subagent:free"]._approval_policy == "auto"  # the control

    ceiling("ask")
    await sessions.get_or_create("subagent:bound", approval_policy="auto")
    assert built["subagent:bound"]._approval_policy == ""
    sessions.set_approval_policy("subagent:bound", "yolo")
    assert built["subagent:bound"]._approval_policy == ""
    assert _refusals("session_policy")


# ── the relay: a background agent's call ──────────────────────────────────────────────────────


def _relay():
    from test_gateway import _make_orchestrator, _mock_dashboard_state

    orch = _make_orchestrator()
    orch.dashboard_state = _mock_dashboard_state()
    orch.dashboard_state.request_approval = AsyncMock(return_value=False)
    return orch


def _event() -> Any:
    event = MagicMock()
    event.request_id = "subagent:ab12:call-1"
    event.title = "write_file"
    event.tool_input = ""
    event.tool_purpose = ""
    return event


@pytest.mark.asyncio
async def test_yolo_does_not_answer_a_background_call_under_an_ask_ceiling(ceiling):
    orch = _relay()
    orch.dashboard_state._yolo = True
    assert await orch._interactive_approval("subagent")(_event(), "")  # the control
    orch.dashboard_state.request_approval.assert_not_awaited()

    ceiling("ask")
    assert not await orch._interactive_approval("subagent")(_event(), "")
    orch.dashboard_state.request_approval.assert_awaited_once()
    assert _refusals("yolo")


@pytest.mark.asyncio
async def test_a_trusted_chat_does_not_answer_its_agent_s_call_under_an_ask_ceiling(ceiling):
    orch = _relay()
    orch.dashboard_state._sessions = {"chat-a": MagicMock(_trust=True, running=True)}
    relay = orch._interactive_approval("subagent", session_resolver=lambda _rid: "chat-a")
    assert await relay(_event(), "")  # the control
    orch.dashboard_state.request_approval.assert_not_awaited()

    ceiling("ask")
    assert not await relay(_event(), "")
    orch.dashboard_state.request_approval.assert_awaited_once()
    assert _refusals("parent_trust")


@pytest.mark.asyncio
async def test_the_cli_flag_does_not_answer_under_an_ask_ceiling(ceiling):
    orch = _relay()
    orch._approval_mode = "yolo"
    assert await orch._interactive_approval("cron")(_event(), "")  # the control

    ceiling("ask")
    assert not await orch._interactive_approval("cron")(_event(), "")
    orch.dashboard_state.request_approval.assert_awaited_once()


# ── the chat ──────────────────────────────────────────────────────────────────────────────────


async def _post(state: Any, path: str, body: dict) -> tuple[int, dict]:
    from aiohttp.test_utils import TestClient, TestServer
    from chat_test_helpers import _make_app

    async with TestClient(TestServer(_make_app(state))) as client:
        resp = await client.post(path, json=body)
        return resp.status, await resp.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["trust", "trust_reads", "yolo"])
async def test_a_switch_that_approves_on_its_own_is_refused_saying_why(tmp_path, ceiling, mode):
    from chat_test_helpers import _make_state

    ceiling("ask")
    state = _make_state(tmp_path)
    session = state.get_or_create_session("c1")
    status, body = await _post(state, "/api/chat/mode", {"mode": mode, "session": "c1"})
    assert status == 409, body
    assert body["error"]["code"] == "approval_grant_refused"
    assert "operator ceiling" in body["error"]["message"]
    assert (session._trust, session._trust_reads, state.is_yolo_active()) == (False, False, False)


@pytest.mark.asyncio
async def test_normal_is_never_refused(tmp_path, ceiling):
    from chat_test_helpers import _make_state

    ceiling("ask")
    state = _make_state(tmp_path)
    state.get_or_create_session("c1")
    status, _ = await _post(state, "/api/chat/mode", {"mode": "normal", "session": "c1"})
    assert status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["trust", "trust_agent", "trust_reads", "yolo"])
async def test_a_card_s_wider_scope_is_refused_and_the_call_still_asks(tmp_path, ceiling, scope):
    from chat_test_helpers import _make_state

    ceiling("ask")
    state = _make_state(tmp_path)
    session = state.get_or_create_session("c1")
    fut = asyncio.get_running_loop().create_future()
    session._approval_futures["rq-1"] = fut
    status, body = await _post(
        state, "/api/chat/sessions/c1/approve", {"action": scope, "request_id": "rq-1"}
    )
    assert status == 409, body
    assert body["error"]["code"] == "approval_grant_refused"
    assert not fut.done(), "the call is still asking, so Allow once still answers it"
    assert session._trust is False


async def _asked_and_declined(state: Any, session: Any) -> None:
    """Run one turn whose call must ask; decline it when it does."""
    session.append("user", "go", "msg msg-u")
    answering = asyncio.ensure_future(_answer_when_asked(state, session, "rejected"))
    from personalclaw.dashboard.chat_runner import run_chat

    await asyncio.wait_for(run_chat(state, session, "go"), timeout=20)
    await asyncio.wait_for(answering, timeout=5)


@pytest.mark.asyncio
async def test_a_trusted_chat_s_call_asks_under_an_ask_ceiling(tmp_path, ceiling):
    ceiling("ask")
    state, _, tools = await _chat(tmp_path, ASKS)
    session = state.get_or_create_session("c-trusted")
    session._trust = True
    await _asked_and_declined(state, session)
    assert tools.ran == []
    assert [_decided(r) for r in _rows(ASKS)] == [("rejected", "you")], _rows(ASKS)
    assert _refusals("trust")


@pytest.mark.asyncio
async def test_an_agent_s_always_allow_seeds_nothing_under_an_ask_ceiling(
    tmp_path, ceiling, monkeypatch
):
    from personalclaw.dashboard import chat_runner

    real = chat_runner.resolve_agent_bindings
    monkeypatch.setattr(
        chat_runner,
        "resolve_agent_bindings",
        lambda cfg, agent=None: dataclasses.replace(real(cfg, agent), approval_mode="auto"),
    )
    ceiling("ask")
    state, _, tools = await _chat(tmp_path, ASKS)
    session = state.get_or_create_session("c-floor")
    await _asked_and_declined(state, session)
    assert session._trust is False
    assert tools.ran == []
    assert _refusals("agent_floor")


@pytest.mark.asyncio
async def test_an_operator_hook_pattern_stands_under_hook_based_and_not_under_ask(
    tmp_path, ceiling
):
    from personalclaw.hooks import HookManager, HooksConfig

    patterns = HookManager(HooksConfig(auto_approve_tools=[ASKS]))

    ceiling("hook_based")
    state, _, tools = await _chat(tmp_path, ASKS)
    state.context_builder.hooks = patterns
    session = state.get_or_create_session("c-hook")
    session.append("user", "go", "msg msg-u")
    from personalclaw.dashboard.chat_runner import run_chat

    await asyncio.wait_for(run_chat(state, session, "go"), timeout=20)
    assert tools.ran == [ASKS], "a `hook_based` ceiling lets an operator's pattern decide"

    ceiling("ask")
    state, _, tools = await _chat(tmp_path / "ask", ASKS)
    state.context_builder.hooks = patterns
    await _asked_and_declined(state, state.get_or_create_session("c-hook-ask"))
    assert tools.ran == []
    assert _refusals("hook_pattern")


# ── the other doors ───────────────────────────────────────────────────────────────────────────


def test_an_unattended_acp_session_keeps_the_host_as_its_authority(ceiling):
    from personalclaw.acp.permission_authority import HOST_AUTHORITY_MODE, sanitize_mode

    assert sanitize_mode("bypassPermissions", unattended=True).mode == "bypassPermissions"
    ceiling("ask")
    decision = sanitize_mode("bypassPermissions", unattended=True)
    assert decision.mode == HOST_AUTHORITY_MODE
    assert decision.downgraded and "operator ceiling" in decision.reason


def test_nor_does_one_under_a_ceiling_that_narrows_the_tools(ceiling):
    """The host enforces a `tools` scope on each call it is asked about, and a CLI approving its
    own calls asks about none."""
    from personalclaw.acp.permission_authority import HOST_AUTHORITY_MODE, sanitize_mode

    ceiling(tools={"allow": ["read_*"]})
    assert sanitize_mode("bypassPermissions", unattended=True).mode == HOST_AUTHORITY_MODE


def test_an_unattended_desktop_drive_still_needs_its_standing_grant(ceiling):
    from personalclaw.computer_use.policy import ComputerUsePolicyRefusal, check_autonomy

    ceiling("ask")
    with pytest.raises(ComputerUsePolicyRefusal):
        check_autonomy("computer_snapshot", caller_identity="cron:nightly")
    check_autonomy("computer_snapshot", caller_identity="dashboard:c1")  # a person is watching


def test_an_announce_in_your_chat_asks_the_ceiling(ceiling):
    from personalclaw.gateway import injection_approval_policy
    from personalclaw.llm_helpers import ToolApprovalPolicy

    assert injection_approval_policy("dashboard:c1") is ToolApprovalPolicy.AUTO_APPROVE
    ceiling("hook_based")
    assert injection_approval_policy("dashboard:c1") is ToolApprovalPolicy.HOOK_BASED
    ceiling("ask")
    assert injection_approval_policy("dashboard:c1") is ToolApprovalPolicy.REJECT_ALL


@pytest.mark.asyncio
async def test_a_background_turn_given_auto_approve_runs_nothing_under_an_ask_ceiling(ceiling):
    from personalclaw.llm_helpers import ToolApprovalPolicy

    ceiling("ask")
    tools = await _background(ASKS, approval_policy=ToolApprovalPolicy.AUTO_APPROVE)
    assert tools.ran == []
    assert [_decided(r) for r in _rows(ASKS)] == [("rejected", "nobody")], _rows(ASKS)
    assert _refusals("session_policy")


def test_a_workflow_gate_asks_under_an_ask_ceiling(ceiling):
    from personalclaw.workflows import gate_policy as GP
    from personalclaw.workflows.models import OriginKind

    safe = {"kind": "approval", "risk": "safe"}
    memory = GP.AllowMemory()
    memory.remember(safe, "g")
    assert GP.decide(safe, "g", origin_kind=OriginKind.SCHEDULE).approved  # the controls
    assert GP.decide(safe, "g", memory=memory).approved

    ceiling("ask")
    assert GP.decide(safe, "g", origin_kind=OriginKind.SCHEDULE).decision is GP.Decision.ASK
    assert GP.decide(safe, "g", memory=memory).decision is GP.Decision.ASK
    assert _refusals("gate_policy") and _refusals("remembered")


@pytest.mark.asyncio
async def test_the_triage_digest_auto_executes_nothing_under_an_ask_ceiling(ceiling):
    from personalclaw.proactive import autoexec
    from personalclaw.proactive.manifest import SOURCE_INBOX, CollectedItem, build_manifest
    from personalclaw.proactive.proposals import Proposal

    manifest = build_manifest(
        [
            CollectedItem(
                source=SOURCE_INBOX,
                source_id="inbox-a",
                title="newsletter",
                ts="2026-09-27T01:00:00+00:00",
            )
        ]
    )
    archive = Proposal(item_id=manifest.items[0].ordinal, action_type="archive", tier="trivial")

    async def digest() -> tuple[AsyncMock, Any]:
        dispatch = AsyncMock(return_value=MagicMock(success=True, reversal="", error=""))
        result = await autoexec.auto_execute(
            [archive],
            manifest=manifest,
            now=datetime.now(UTC),
            enabled=True,
            cap=5,
            dispatch=dispatch,
            budget_check=lambda: (False, ""),
        )
        return dispatch, result

    dispatch, _ = await digest()
    dispatch.assert_awaited_once()  # the control: a trivial archive runs on its own

    ceiling("ask")
    dispatch, result = await digest()
    dispatch.assert_not_awaited()
    assert [d.reason for d in result.deferred] == ["refused_by_ceiling"]
