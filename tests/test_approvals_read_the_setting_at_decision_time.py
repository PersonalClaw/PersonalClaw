"""An approval decision reads the owner's CURRENT setting: a Settings change reaches the next call.

`SubagentManager` read `agent.approval_mode` once, when the gateway started, and every parentless
agent after that (a trigger's, a webhook's, a cron's) was decided by that copy. Switching Settings
→ Agent defaults → Approvals from "Auto-approve" to "Ask me" did not reach them until a restart:
the next trigger agent still approved its own tool calls. The stale copy fails toward the looser
posture, which is the direction a security setting must never fail in.

The same rule covers every other approval or safety setting a long-lived object used to copy at
startup; each case here changes the setting on disk after the object exists and asserts the next
decision follows it.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_subagent import _mock_ctx_builder, _mock_sessions

import personalclaw.config.loader as loader
from personalclaw.subagent import SubagentInfo, SubagentManager


def _write_config(**agent) -> None:
    path = loader.config_dir() / "config.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.setdefault("agent", {}).update(agent)
    path.write_text(json.dumps(data))


async def _policy_of_a_parentless_agent(manager: SubagentManager, sessions) -> str:
    """The approval policy the next parentless agent's session is created with."""
    sessions.get_or_create.reset_mock()
    info = SubagentInfo(id="trig01", task="write the digest", parent_session_key="")
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await manager._run_inner(info, "subagent:trig01")
    return sessions.get_or_create.call_args.kwargs.get("approval_policy")


def _manager():
    sessions = _mock_sessions()
    sessions.get_approval_policy = MagicMock(return_value="")
    ctx = _mock_ctx_builder()
    ctx.hooks.auto_approve_subagent_tools = False
    manager = SubagentManager(sessions=sessions, ctx_builder=ctx, is_yolo=lambda: False)
    return manager, sessions


@pytest.mark.asyncio
async def test_switching_auto_approve_off_reaches_the_next_trigger_agent():
    """🔴 The security direction: "Auto-approve" → "Ask me" must stop the next agent approving."""
    _write_config(approval_mode="auto")
    manager, sessions = _manager()
    assert await _policy_of_a_parentless_agent(manager, sessions) == "auto", "premise"

    _write_config(approval_mode="interactive")  # the owner changes it in Settings
    assert await _policy_of_a_parentless_agent(manager, sessions) == "", (
        "the next parentless agent still approved its own tool calls on the setting the gateway "
        "started with"
    )


@pytest.mark.asyncio
async def test_switching_it_on_reaches_the_next_one_too():
    """The same read, the other way: nothing is decided by a copy of the setting."""
    _write_config(approval_mode="interactive")
    manager, sessions = _manager()
    assert await _policy_of_a_parentless_agent(manager, sessions) == "", "premise"

    _write_config(approval_mode="auto")
    assert await _policy_of_a_parentless_agent(manager, sessions) == "auto"


@pytest.mark.asyncio
async def test_an_agent_with_a_chat_behind_it_is_decided_by_that_chat_not_the_setting():
    """The control: the global setting decides only a PARENTLESS agent; one a chat started takes
    the chat's own policy, before and after the change."""
    _write_config(approval_mode="auto")
    manager, sessions = _manager()
    sessions.get_approval_policy = MagicMock(return_value="")
    sessions.get_or_create.reset_mock()
    info = SubagentInfo(id="chat01", task="t", parent_session_key="dashboard:chat-a")
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await manager._run_inner(info, "subagent:chat01")
    assert sessions.get_or_create.call_args.kwargs.get("approval_policy") == ""


def test_the_manager_keeps_no_copy_of_the_setting():
    """The shape of the fix, pinned: no attribute holds the setting for later decisions."""
    _write_config(approval_mode="auto")
    manager, _ = _manager()
    assert not hasattr(manager, "_global_approval_mode")


# The spawn gate above is not the only reader: the tool-call gate and the relay read the hook
# settings, and the relay read `auto_approve_sources`, from objects built at startup.


def _write_hooks(**hooks) -> None:
    path = loader.config_dir() / "config.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.setdefault("hooks", {}).update(hooks)
    path.write_text(json.dumps(data))


def test_the_tool_gate_reads_the_hook_settings_as_they_are_now():
    """`HookManager` was built from `config.hooks` once and `reload` had no caller, so a deny or
    auto-approve pattern changed after startup changed nothing until a restart."""
    from personalclaw.hooks import TOOL_AUTO_APPROVE, TOOL_DENY, live_hook_manager

    _write_hooks(auto_approve_tools=["git status"], auto_deny_tools=[])
    hooks = live_hook_manager()
    assert hooks.on_tool_call("Running: git status").action == TOOL_AUTO_APPROVE, "premise"

    _write_hooks(auto_approve_tools=[], auto_deny_tools=["git status*"])
    assert (
        hooks.on_tool_call("Running: git status").action == TOOL_DENY
    ), "a pattern the owner removed kept auto-approving, and the deny they added did nothing"


def test_the_subagent_spawn_flags_are_read_now_too():
    from personalclaw.hooks import live_hook_manager

    _write_hooks(auto_approve_subagent_spawn=True, auto_approve_subagent_tools=True)
    hooks = live_hook_manager()
    assert hooks.auto_approve_subagent_spawn and hooks.auto_approve_subagent_tools, "premise"

    _write_hooks(auto_approve_subagent_spawn=False, auto_approve_subagent_tools=False)
    assert not hooks.auto_approve_subagent_spawn
    assert not hooks.auto_approve_subagent_tools


@pytest.mark.asyncio
async def test_a_source_the_owner_stopped_auto_approving_asks_again():
    """The relay's `hooks.auto_approve_sources` came from the config the gateway started with."""
    from test_gateway import _mock_dashboard_state

    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, LLMEvent

    _write_hooks(auto_approve_sources=["subagent"])
    cfg = loader.AppConfig.load()  # what the gateway started with
    with patch.object(cfg, "load_credentials", return_value={"PERSONALCLAW_OWNER_ID": "U1"}):
        orch = GatewayOrchestrator(cfg, no_dashboard=False, no_crons=True, no_open=True)
    from personalclaw.hooks import live_hook_manager

    orch.ctx_builder = MagicMock()
    orch.ctx_builder.hooks = live_hook_manager()  # what `_init_services` builds
    orch.dashboard_state = _mock_dashboard_state()
    orch.dashboard_state.request_approval = AsyncMock(return_value=False)
    orch.dashboard_state.ended_as = MagicMock(return_value="rejected")
    orch.dashboard_state.is_yolo_active = MagicMock(return_value=False)
    orch.dashboard_state._sessions = {}
    orch._channel_delivery = None
    approve = orch._interactive_approval("subagent")
    ask = LLMEvent(kind=EVENT_PERMISSION_REQUEST, request_id="subagent:ab12:tc-1", title="Bash")
    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        assert await approve(ask, ""), "premise: the listed source auto-approves"
        _write_hooks(auto_approve_sources=[])  # the owner takes it off the list
        assert not await approve(ask, ""), "the source the owner removed still auto-approved"
    orch.dashboard_state.request_approval.assert_awaited_once()


# ── A grant is read at each CALL, not once per agent or chat ───────────────────────────────────


@pytest.mark.asyncio
async def test_a_setting_switched_off_mid_run_stops_waiving_that_agent_s_next_call():
    """🔴 The copy was per agent too: a trigger agent's runtime was handed "auto" once, when it
    started, so switching Auto-approve off reached only the agents started after it."""
    import asyncio

    from test_a_tool_call_is_audited_as_it_was_decided import (
        ASKS,
        _decided,
        _rows,
        _subagents,
        _Tools,
    )

    class _SwitchedOffMidRun(_Tools):
        async def invoke(self, tool_name, arguments):
            _write_config(approval_mode="interactive")  # the owner changes it while this runs
            return await super().invoke(tool_name, arguments)

    _write_config(approval_mode="auto")
    manager, tools = _subagents(ASKS, each=2, tools=_SwitchedOffMidRun())
    info = SubagentInfo(id="trig-mid", task="t")
    await asyncio.wait_for(manager._run_inner(info, "subagent:trig-mid"), timeout=20)
    assert tools.ran == [ASKS], "the call after the switch still approved itself"
    assert [_decided(r) for r in _rows(ASKS)] == [
        ("auto_approved", "setting"),
        ("denied", "unattended_no_one_to_ask"),
    ], _rows(ASKS)


@pytest.mark.asyncio
async def test_an_agent_s_always_allow_taken_back_takes_its_chat_back_to_asking(
    tmp_path, monkeypatch
):
    """The chat's copy: the agent's "Always allow" seeded the chat's Trust once, and taking it back
    left the chat approving on its own until it was closed."""
    import asyncio
    import dataclasses

    from test_a_tool_call_is_audited_as_it_was_decided import ASKS, _answer_when_asked, _chat

    from personalclaw.dashboard import chat_runner

    floor = {"mode": "auto"}
    real = chat_runner.resolve_agent_bindings
    monkeypatch.setattr(
        chat_runner,
        "resolve_agent_bindings",
        lambda cfg, agent=None: dataclasses.replace(real(cfg, agent), approval_mode=floor["mode"]),
    )
    state, _, tools = await _chat(tmp_path, ASKS, turns=2)
    session = state.get_or_create_session("c-floor")
    session.append("user", "go", "msg msg-u")
    await asyncio.wait_for(chat_runner.run_chat(state, session, "go"), timeout=20)
    assert tools.ran == [ASKS] and session._trust, "premise: the floor let it run unasked"

    floor["mode"] = ""  # the owner takes "Always allow for this agent" back
    session.append("user", "again", "msg msg-u2")
    answering = asyncio.ensure_future(_answer_when_asked(state, session, "rejected"))
    await asyncio.wait_for(chat_runner.run_chat(state, session, "again"), timeout=20)
    await asyncio.wait_for(answering, timeout=5)
    assert tools.ran == [ASKS]
    assert session._trust is False


# ── The other safety settings a long-lived object copied ───────────────────────────────────────


def test_a_lowered_rate_limit_binds_a_client_already_seen():
    """An inbound client's bucket was made with the caps of its first request and kept them."""
    from personalclaw.inbound import caps as K

    wide, narrow = K.Caps(rps=0.01, burst=5), K.Caps(rps=0.01, burst=1)
    surface, client = "mcp_inbound", "ide-rate-retune"
    assert [K.check_rate_for_client(surface, client, caps=wide) for _ in range(2)] == [True, True]
    # The owner lowers the burst: the bank the first caps filled holds one request now.
    assert [K.check_rate_for_client(surface, client, caps=narrow) for _ in range(2)] == [
        True,
        False,
    ]


@pytest.mark.asyncio
async def test_a_server_whose_question_grant_was_taken_back_cannot_ask_you():
    """The grant was read at the connection's handshake, and a connection outlives a Settings
    change, so a server kept asking after its grant was switched off."""
    import asyncio
    import types

    from mcp import types as mcp_types

    from personalclaw import mcp_elicitation as ME
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.inbox_providers import native_source

    def _write_grant(*servers: str) -> None:
        path = loader.config_dir() / "config.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        data.setdefault("security", {})["mcp_elicitation_servers"] = list(servers)
        path.write_text(json.dumps(data))

    question = types.SimpleNamespace(
        message="Delete the staging database?",
        mode="form",
        requestedSchema={
            "type": "object",
            "properties": {"confirm": {"type": "boolean"}},
            "required": ["confirm"],
        },
    )
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
    prior = native_source.get_dashboard_state()
    native_source.set_dashboard_state(state)
    try:
        _write_grant("asker")
        asking = asyncio.ensure_future(ME._handle_elicitation("asker", question))
        for _ in range(200):
            if state._pending_approvals:
                break
            await asyncio.sleep(0.01)
        (approval_id,) = list(state._pending_approvals)  # the control: a granted server asks
        state.resolve_approval(approval_id, False)
        assert (await asyncio.wait_for(asking, 5)).action == "decline"

        _write_grant()  # the owner switches the grant off; the connection stays up
        answer = await asyncio.wait_for(ME._handle_elicitation("asker", question), 5)
        assert not state._pending_approvals, "a server whose grant was revoked asked you anyway"
        assert isinstance(answer, mcp_types.ErrorData)
        assert answer.message == ME.NOT_GRANTED_MESSAGE
    finally:
        native_source.set_dashboard_state(prior)


@pytest.mark.asyncio
async def test_a_model_call_guard_reads_its_ceiling_and_scan_mode_at_each_call(
    tmp_path, monkeypatch
):
    """A guard lives as long as the runtime holding it (the background session, a loop worker),
    and it held the spend ceiling and the scan mode it was built with."""
    from test_guardrails_budgets import FakeProvider, _drain

    from personalclaw.guardrails.budgets import Budget, SpendMeter
    from personalclaw.guardrails.failure import BudgetExceededError, SecretLeakBlocked
    from personalclaw.guardrails.model_call import ModelCallGuard

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    meter = SpendMeter(config_dir=tmp_path)
    meter.charge(1000, 0.0)
    now = {"budget": Budget(), "scan": "warn"}
    guard = ModelCallGuard(
        FakeProvider(),
        use_case="reasoning",
        provider_name="P",
        model="m",
        meter=meter,
        budget_source=lambda: now["budget"],
        scan_mode_source=lambda: now["scan"],
    )
    await guard.start()
    assert await _drain(guard, "my key AKIAIOSFODNN7EXAMPLE") == "ok"  # the controls

    now["scan"] = "block"
    with pytest.raises(SecretLeakBlocked):
        await _drain(guard, "my key AKIAIOSFODNN7EXAMPLE")
    now["budget"] = Budget(max_tokens=500)
    with pytest.raises(BudgetExceededError):
        await _drain(guard)


def test_the_bridge_hands_its_guards_where_to_read_them():
    """The resolution seam is what builds every guard a runtime holds; it passes the readers."""
    import inspect

    from personalclaw.providers import provider_bridge

    source = inspect.getsource(provider_bridge._resolve_from_config_registry)
    for wired in (
        "budget_source=budget_from_config",
        "run_budget_source=run_budget_from_config",
        "scan_mode_source=_scan_mode_now",
    ):
        assert wired in source, wired


@pytest.mark.asyncio
async def test_turning_verification_on_refuses_a_cached_unattended_runner():
    """`agents.unattended_requires_verified_adapter` was checked only when a runner was created;
    an unattended run on a runner launched before the owner turned it on kept running."""
    from personalclaw.agents.runners import UnverifiedAdapterError
    from personalclaw.session import SessionManager

    class _Runner:
        async def start(self):
            return None

        async def shutdown(self):
            return None

        def is_process_alive(self):
            return True

    _write_config(unattended_requires_verified_adapter=False)
    sessions = SessionManager(loader.AppConfig.load(), provider_factory=lambda *a, **k: _Runner())
    kw = {"provider_kind": "acp:not-a-cataloged-runner", "unattended": True}
    await sessions.get_or_create("cron:nightly", **kw)
    sessions.release("cron:nightly")

    _write_config(unattended_requires_verified_adapter=True)
    with pytest.raises(UnverifiedAdapterError):
        await sessions.get_or_create("cron:nightly", **kw)


def test_the_subagent_limits_are_read_at_each_decision():
    """The gateway built its subagent manager with the limits read at startup."""
    from personalclaw.gateway import _live_subagent_limits

    _write_config(max_subagents=3, subagent_max_turns=7, subagent_timeout_secs=90)
    manager = SubagentManager(
        sessions=_mock_sessions(), ctx_builder=_mock_ctx_builder(), limits=_live_subagent_limits
    )
    assert (manager._max_concurrent, manager._default_turn_limit, manager._default_timeout) == (
        3,
        7,
        90,
    )
    _write_config(max_subagents=1, subagent_max_turns=2, subagent_timeout_secs=30)
    assert (manager._max_concurrent, manager._default_turn_limit, manager._default_timeout) == (
        1,
        2,
        30,
    )
