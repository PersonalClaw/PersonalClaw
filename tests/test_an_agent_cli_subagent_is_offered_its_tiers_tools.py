"""A subagent on an agent CLI is offered the core tools its tier offers, and refused the rest.

A subagent's tier (`subagent_tier.tier_for`) decides what its model is SHOWN as well as what it may
use, and on PersonalClaw's own loop both hold. An agent CLI gets PersonalClaw's tools from the
`personalclaw-core` server it starts, and that server listed every tool to every CLI: a read-only
subagent on an agent CLI was shown the write tools, and only a call that asked its owner was ever
refused. The tier now travels with the session to that server, declared in the server's own
environment rather than left to whatever the CLI passes on, and the server lists exactly the tools
the same tier is offered on PersonalClaw's own loop, and refuses a call to any other in the tier's
own words.

A fake agent-CLI peer (a runtime the host cannot hold) and the real tool server's stdio loop.
"""

from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from personalclaw import mcp_core, mcp_shared
from personalclaw.guardrails.policy import (
    READ_ONLY_REASON,
    TOOL_READ,
    SafetyProfile,
    offer_refusal,
)
from personalclaw.hooks import TOOL_ALLOW, ToolHookResult
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.subagent import SubagentManager
from personalclaw.tool_providers.base import (
    PROPOSES_META_KEY,
    TELLS_OWNER_META_KEY,
    RiskLevel,
    risk_from_annotations,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    from personalclaw.guardrails.budgets import reset_meter
    from personalclaw.guardrails.ceiling import reset_ceiling

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path / "home")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(
        "personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "agents"
    )
    monkeypatch.setattr("personalclaw.subagent.check_memory_available", lambda **_kw: (True, 8.0))
    monkeypatch.delenv(mcp_shared.TOOL_TIER_KEY, raising=False)
    reset_ceiling()
    reset_meter()
    yield tmp_path
    reset_ceiling()
    reset_meter()


class _CliPeer:
    """An agent CLI's session: it answers in text, and takes no tool grants (its tools run where
    the host never sees them)."""

    session_id = "cli-session"

    async def stream(self, _message):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Read what I was sent.")
        yield LLMEvent(kind=EVENT_COMPLETE)


class _CliSessions:
    """A SessionManager stand-in that starts every subagent on an agent CLI, keeping what each
    session was started with."""

    def __init__(self) -> None:
        self.started: list[dict] = []

    async def get_or_create(self, key, agent=None, **kwargs):
        self.started.append({"key": key, **kwargs})
        return _CliPeer(), True, False

    def get_pid(self, _key):
        return None

    def get_agent(self, _key):
        return ""

    def has_session(self, _key):
        return False

    def get_approval_policy(self, _key):
        return ""

    def record_success(self, _key):
        pass

    def release(self, _key, *, cleanup=False):
        pass

    async def reset(self, _key):
        pass


def _spawn(**kw) -> dict:
    """One subagent on an agent CLI, run to its end; what its session was started with."""
    import asyncio

    sessions = _CliSessions()
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("Review the notes.", None))
    ctx.hooks.on_tool_call = MagicMock(return_value=ToolHookResult(action=TOOL_ALLOW))
    ctx.hooks.auto_approve_subagent_spawn = True
    ctx.hooks.auto_approve_subagent_tools = False
    manager = SubagentManager(sessions=sessions, ctx_builder=ctx)

    async def _go():
        with (
            patch("personalclaw.subagent.Stats"),
            patch("personalclaw.subagent.sel"),
            patch("personalclaw.guardrails.policy.ceiling_permits_approval", lambda _v: True),
        ):
            info = manager.spawn("Review the notes.", parent_session_key="", **kw)
            assert info is not None and not info.error, info
            await manager._tasks[info.id]

    asyncio.run(_go())
    (started,) = sessions.started
    return started


def _offered_to_a_reader() -> list[str]:
    """What PersonalClaw's own loop shows a read-only subagent of the core tool server's tools:
    each tool judged by what it declares (`guardrails.policy.offer_refusal`)."""
    profile = SafetyProfile(name="spawn_research", tool_grants=TOOL_READ)
    shown = []
    for tool in mcp_core._aggregated_list_tools():
        meta = tool.get("_meta") or {}
        why = offer_refusal(
            profile,
            tool["name"],
            risk_from_annotations(tool.get("annotations"), trusted=True),
            proposes=meta.get(PROPOSES_META_KEY) is True,
            tells_owner=bool(meta.get(TELLS_OWNER_META_KEY)),
        )
        if not why:
            shown.append(tool["name"])
    return shown


def _served(method: str, params: dict | None = None) -> dict:
    """One JSON-RPC request through the core tool server's real stdio loop; its response."""
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    stdin = SimpleNamespace(buffer=io.BytesIO((json.dumps(request) + "\n").encode()))
    stdout = io.StringIO()
    with (
        patch.object(sys, "stdin", stdin),
        patch.object(sys, "stdout", stdout),
        patch.object(mcp_shared, "_use_content_length", False),
        patch.object(mcp_shared, "_resolve_excluded_tools", lambda: set()),
    ):
        mcp_shared.run_mcp_stdio_loop(
            "personalclaw-core",
            "1.0.0",
            mcp_core._aggregated_list_tools,
            mcp_core._aggregated_call_tool,
        )
    return json.loads(stdout.getvalue().strip().splitlines()[-1])


# ── the session carries its tier to its tool server ─────────────────────────────────────────────


def test_a_read_only_subagent_on_an_agent_cli_hands_its_tier_to_its_tool_server():
    from personalclaw.acp.mcp_servers import core_mcp_servers

    started = _spawn(approval_mode="auto")  # nobody to ask, so it only reads

    tier = (started.get("extra_env") or {}).get(mcp_shared.TOOL_TIER_KEY)
    assert tier, f"the session was started without its tier: {started}"
    (server,) = core_mcp_servers(session_key=started["key"], env=started["extra_env"])
    declared = {e["name"]: e["value"] for e in server["env"]}
    assert declared.get(mcp_shared.TOOL_TIER_KEY) == tier, declared


def test_a_subagent_that_may_write_is_offered_every_tool():
    started = _spawn(capability_class="mutating")

    assert mcp_shared.TOOL_TIER_KEY not in (started.get("extra_env") or {}), started


# ── its tool server lists the tier's tools, and refuses the rest ────────────────────────────────


def _reading_session(monkeypatch) -> None:
    """The tool server of a read-only subagent's agent CLI, as its session declares it."""
    started = _spawn(approval_mode="auto")
    monkeypatch.setenv(mcp_shared.TOOL_TIER_KEY, started["extra_env"][mcp_shared.TOOL_TIER_KEY])


def test_its_tool_server_lists_exactly_what_its_tier_is_offered(monkeypatch):
    _reading_session(monkeypatch)

    listed = [t["name"] for t in _served("tools/list")["result"]["tools"]]

    assert listed == _offered_to_a_reader()
    assert "memory_list" in listed and "artifact_get" in listed, listed
    for write in ("artifact_update", "automation_create"):
        assert write not in listed, f"a read-only subagent's server listed {write}"


def test_a_server_no_tier_holds_lists_every_tool():
    listed = [t["name"] for t in _served("tools/list")["result"]["tools"]]

    assert listed == [t["name"] for t in mcp_core._aggregated_list_tools()]


def test_a_call_outside_its_tier_is_refused_in_the_tiers_own_words(monkeypatch):
    _reading_session(monkeypatch)
    ran: list[str] = []
    monkeypatch.setattr(
        "personalclaw.mcp_artifacts._call_tool_inner",
        lambda name, _args: ran.append(name) or "updated",
    )

    answer = _served(
        "tools/call", {"name": "artifact_update", "arguments": {"slug": "a1", "content": "x"}}
    )["result"]

    assert answer.get("isError") is True, answer
    text = answer["content"][0]["text"]
    assert READ_ONLY_REASON.format(tool="artifact_update") in text, text
    assert "write-class" not in text, text
    assert ran == [], "the refused call ran"


# ── the tier the session carries, read back where its tools are served ─────────────────────────


@pytest.mark.parametrize("capability", ["research", "mutating", "text"])
def test_a_tier_reads_back_as_the_tier_it_was(capability):
    from personalclaw.subagent_tier import _tier, tier_from_env

    sent = _tier(capability, owner_notices=False, may_change=())
    read = tier_from_env(sent.as_env())
    assert read.capability_class == capability
    assert read.profile.tool_grants == sent.profile.tool_grants
    shown = {
        name: read.offer(name, RiskLevel.SAFE) == "" for name in ("memory_list", "artifact_get")
    }
    assert shown == {name: sent.offer(name, RiskLevel.SAFE) == "" for name in shown}


@pytest.mark.parametrize(
    "raw", ["not json", "[]", json.dumps({"capability": "every tool"}), json.dumps({})]
)
def test_a_tier_it_cannot_read_offers_no_tool(raw):
    """Fail closed: an unreadable tier is the narrowest one, which offers not even a read."""
    from personalclaw.subagent_tier import tier_from_env

    tier = tier_from_env(raw)
    assert tier.narrows()
    assert tier.offer("memory_list", RiskLevel.SAFE), raw
