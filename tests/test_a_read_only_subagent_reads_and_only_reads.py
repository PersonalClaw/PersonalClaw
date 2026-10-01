"""A read-only subagent is offered what reads, refused the rest, and says so when it did nothing.

Driven through PersonalClaw's own loop (``NativeAgentRuntime``) with the platform's real file and
shell tools, behind the shipped ``SubagentManager``, so what is asserted is what the subagent's
model is handed and what its calls do. A spawn with nobody to approve it (``approval_mode="auto"``)
runs read-only, and its runtime answers its own asks, so the tier is the only thing in the way.

* **What it is offered.** The tool block names the tools a read can pass: the ones that declare
  they only read, and the shell, whose every command is read before it runs. A tool that writes,
  and a write that never asks anybody, are left out.
* **Why a call is refused.** A call to a tool it was not offered is refused as outside its
  read-only tools, and so is a shell command that does more than read; a command that only reads
  runs.
* **How it ends.** A subagent whose every call was refused did nothing it was asked, so it ends as
  not done, with the reason, and the step it ran for fails for that reason. One that got any call
  through, or answered without tools, finished.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.agents.native.builtin_tools import (
    PLATFORM_CATEGORIES,
    PLATFORM_PROVIDER_NAME,
    NativeBuiltinToolProvider,
)
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.hooks import TOOL_ALLOW, ToolHookResult
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    AgentEvent,
)
from personalclaw.subagent import SubagentManager
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """The home, the user's own folder, the subagent records and the operator ceiling are this
    test's own."""
    import personalclaw.config.loader as cfg
    from personalclaw.guardrails.budgets import reset_meter
    from personalclaw.guardrails.ceiling import reset_ceiling

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path / "home")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "home").mkdir()
    (tmp_path / "user").mkdir()
    monkeypatch.setattr(
        "personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "agents"
    )
    monkeypatch.setattr("personalclaw.subagent.check_memory_available", lambda **_kw: (True, 8.0))
    reset_ceiling()
    reset_meter()
    yield tmp_path
    reset_ceiling()
    reset_meter()


class _ScriptedModel:
    """Replays its turns, and keeps the tool block and the messages each turn was handed."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.calls = 0
        self.tools_seen: list[list[str]] = []
        self.messages_seen: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.tools_seen.append([t["function"]["name"] for t in tools or []])
        self.messages_seen.append(list(messages))
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev


class _Knowledge(ToolProvider):
    """A provider's own tools: one that writes and asks nobody first, as the knowledge and task
    tools do, and one that declares it only reads, as a trusted MCP server's read does."""

    def __init__(self) -> None:
        self.invoked: list[dict] = []

    @property
    def name(self) -> str:
        return "knowledge"

    @property
    def display_name(self) -> str:
        return "Knowledge"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="knowledge_create",
                description="Create a knowledge page.",
                parameters={"type": "object", "properties": {"title": {"type": "string"}}},
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
            ),
            ToolDefinition(
                name="knowledge_search",
                description="Search the knowledge pages.",
                parameters={"type": "object", "properties": {"query": {"type": "string"}}},
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
        ]

    async def invoke(self, tool_name, arguments):
        self.invoked.append(arguments)
        if tool_name == "knowledge_search":
            return ToolResult(success=True, output="Retry notes: the ceiling is three.")
        return ToolResult(success=True, output="created")


class _NativeSessions:
    """A SessionManager stand-in that builds each subagent PersonalClaw's own loop over the
    platform's tools, rooted in one workspace, with the approval source the spawn hands it — the
    bridge's construction."""

    def __init__(self, workspace: Path, model: _ScriptedModel) -> None:
        self.workspace = workspace
        self.model = model
        self.knowledge = _Knowledge()
        self.runtimes: dict[str, NativeAgentRuntime] = {}

    async def get_or_create(self, key, agent=None, **kwargs):
        platform = NativeBuiltinToolProvider(
            cwd=self.workspace,
            agent=agent or "",
            session_key=key,
            categories=PLATFORM_CATEGORIES,
            provider_name=PLATFORM_PROVIDER_NAME,
        )
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name=agent or "", provider="native"),
            model_provider=self.model,
            tool_providers=[platform, self.knowledge],
            cwd=self.workspace,
            session_key=key,
        )
        await runtime.start()
        if kwargs.get("approval_source") is not None:
            runtime.set_approval_source(kwargs["approval_source"])
        self.runtimes[key] = runtime
        return runtime, True, False

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


def _call(name: str, args: dict, call_id: str) -> AgentEvent:
    return AgentEvent(
        kind=EVENT_TOOL_CALL, tool_call_id=call_id, title=name, tool_input=json.dumps(args)
    )


def _turns(*calls: AgentEvent, answer: str = "Here is what I found.") -> list[list[AgentEvent]]:
    """One turn that makes *calls* (none: it answers at once), then one that answers in text."""
    said = [AgentEvent(kind=EVENT_TEXT_CHUNK, text=answer), AgentEvent(kind=EVENT_COMPLETE)]
    return [[*calls, AgentEvent(kind=EVENT_COMPLETE)], said] if calls else [said]


def _drive(tmp_path: Path, model: _ScriptedModel):
    """One read-only subagent nobody is asked about, run to its end."""
    import asyncio

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "notes.md").write_text("The retry ceiling is three.\n", encoding="utf-8")
    sessions = _NativeSessions(workspace, model)
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
            info = manager.spawn("Review the notes.", parent_session_key="", approval_mode="auto")
            assert info is not None and not info.error, info
            await manager._tasks[info.id]
            return info

    return asyncio.run(_go()), sessions, workspace


def _tool_results(model: _ScriptedModel) -> list[str]:
    """What the second model call was handed back from the first call's tools."""
    return [str(m.get("content", "")) for m in model.messages_seen[1] if m.get("role") == "tool"]


# ── what it is offered ────────────────────────────────────────────────────────────────────────


def test_a_read_only_subagent_is_offered_the_tools_that_read_and_the_shell(tmp_path):
    model = _ScriptedModel(_turns())
    _drive(tmp_path, model)

    offered = set(model.tools_seen[0])
    assert {"read_file", "list_dir", "glob", "grep", "bash", "knowledge_search"} <= offered, sorted(
        offered
    )
    for tool in ("write_file", "edit_file", "knowledge_create"):
        assert tool not in offered, f"a read-only subagent was offered {tool}: {sorted(offered)}"


# ── why a call is refused ─────────────────────────────────────────────────────────────────────


def test_a_write_it_was_not_offered_is_refused_as_read_only(tmp_path):
    target = tmp_path / "workspace" / "notes.md"
    model = _ScriptedModel(
        _turns(
            _call("write_file", {"path": "notes.md", "content": "rewritten"}, "c1"),
            _call("read_file", {"path": "notes.md"}, "c2"),
        )
    )
    info, _, _ = _drive(tmp_path, model)

    assert target.read_text(encoding="utf-8") == "The retry ceiling is three.\n"
    write_result, read_result = _tool_results(model)
    assert "read-only" in write_result and "write_file" in write_result, write_result
    assert "write-class" not in write_result, write_result
    assert "The retry ceiling is three." in read_result, read_result
    assert info.error == "", "a subagent that read what it was asked about finished"


def test_a_providers_declared_read_runs(tmp_path):
    model = _ScriptedModel(_turns(_call("knowledge_search", {"query": "retry"}, "c1")))
    info, sessions, _ = _drive(tmp_path, model)

    assert sessions.knowledge.invoked == [{"query": "retry"}]
    assert "the ceiling is three" in _tool_results(model)[0]
    assert info.error == ""


def test_a_write_that_asks_nobody_is_refused_not_run(tmp_path):
    model = _ScriptedModel(
        _turns(
            _call("knowledge_create", {"title": "Retry notes"}, "c1"),
            _call("read_file", {"path": "notes.md"}, "c2"),
        )
    )
    _, sessions, _ = _drive(tmp_path, model)

    assert sessions.knowledge.invoked == [], "a read-only subagent's write ran"
    assert "read-only" in _tool_results(model)[0]


def test_a_shell_command_that_only_reads_runs_and_one_that_writes_is_refused(tmp_path):
    model = _ScriptedModel(
        _turns(
            _call("bash", {"command": "cat notes.md"}, "c1"),
            _call("bash", {"command": "touch made-by-the-subagent.txt"}, "c2"),
        )
    )
    _, _, workspace = _drive(tmp_path, model)

    read_result, write_result = _tool_results(model)
    assert "The retry ceiling is three." in read_result, read_result
    assert "read-only" in write_result, write_result
    assert not (workspace / "made-by-the-subagent.txt").exists()


# ── how it ends ───────────────────────────────────────────────────────────────────────────────


def test_a_subagent_whose_every_call_was_refused_did_not_do_its_task(tmp_path):
    """🔴 Before: both review subagents of a batch had every call refused, and each ended
    "completed" with its apology as the result, so the batch reported success."""
    model = _ScriptedModel(
        _turns(
            _call("write_file", {"path": "review.md", "content": "x"}, "c1"),
            _call("bash", {"command": "rm -f notes.md"}, "c2"),
            _call("knowledge_create", {"title": "Review"}, "c3"),
            answer="I couldn't read anything, so I have no findings.",
        )
    )
    info, _, _ = _drive(tmp_path, model)

    from personalclaw.subagent_tier import couldnt_do_it

    assert info.done is True
    assert couldnt_do_it(info.error), info.error
    for tool in ("write_file", "bash", "knowledge_create"):
        assert tool in info.error, info.error
    assert "read-only" in info.error, info.error
    assert "no findings" in info.result, "its own account of what it tried is kept"


def test_looking_for_a_tool_is_not_doing_the_task(tmp_path):
    """A search for a tool is answered by the runtime itself and does none of the task, so a
    subagent that found tools and was refused every one of them still did nothing it was asked."""
    model = _ScriptedModel(
        _turns(
            _call("tool_search", {"query": "write a file"}, "c1"),
            _call("write_file", {"path": "review.md", "content": "x"}, "c2"),
        )
    )
    info, _, _ = _drive(tmp_path, model)

    from personalclaw.subagent_tier import couldnt_do_it

    assert couldnt_do_it(info.error), info.error
    assert "tool_search" not in info.error


def test_a_subagent_that_answered_without_tools_finished(tmp_path):
    info, _, _ = _drive(tmp_path, _ScriptedModel(_turns()))
    assert info.done is True and info.error == ""


# ── the offer and the refusal, tier by tier ───────────────────────────────────────────────────


def _profile(tier: str, allow: tuple[str, ...] = ()):
    from personalclaw.guardrails.policy import SafetyProfile

    return SafetyProfile(name="spawn_research", tool_grants=tier, tool_allowlist=allow)


def test_a_read_tier_is_shown_what_a_call_could_pass_and_nothing_else(tmp_path):
    from personalclaw.guardrails.policy import offer_refusal

    read = _profile("read")
    assert offer_refusal(read, "read_file", RiskLevel.SAFE) == ""
    assert offer_refusal(read, "bash", RiskLevel.DESTRUCTIVE) == ""
    assert offer_refusal(read, "propose_change", RiskLevel.CAUTION, proposes=True) == ""
    assert "read-only" in offer_refusal(read, "write_file", RiskLevel.CAUTION)
    assert "read-only" in offer_refusal(read, "run_script", RiskLevel.CAUTION), "only `bash` is"
    note = tmp_path / "kitchen.md"
    assert offer_refusal(read, "write_file", RiskLevel.CAUTION, may_change=(str(note),)) == ""
    assert (
        offer_refusal(read, "notify", RiskLevel.CAUTION, tells_owner=True, owner_notices=True) == ""
    )
    assert "read-only" in offer_refusal(read, "notify", RiskLevel.CAUTION, tells_owner=True)


def test_an_allowlist_tier_is_shown_its_allowlist_and_a_full_tier_everything():
    from personalclaw.guardrails.policy import offer_refusal

    custom = _profile("custom", ("read_*",))
    assert offer_refusal(custom, "read_file", RiskLevel.SAFE) == ""
    assert "allowlist" in offer_refusal(custom, "list_dir", RiskLevel.SAFE)
    assert offer_refusal(_profile("read_write"), "write_file", RiskLevel.CAUTION) == ""


def test_a_read_tier_refuses_in_its_own_words():
    from personalclaw.guardrails.policy import granted_call_refusal

    read = _profile("read")
    said = granted_call_refusal(read, "write_file", RiskLevel.CAUTION, "", {"path": "a.md"})
    assert said == "its tools are read-only, and write_file is not one of them"
    said = granted_call_refusal(read, "bash", RiskLevel.DESTRUCTIVE, "", {"command": "rm a.md"})
    assert said == "its tools are read-only, and this command does more than read"
    assert granted_call_refusal(read, "bash", RiskLevel.DESTRUCTIVE, "", {"command": "ls"}) == ""
    scoped = granted_call_refusal(
        read, "edit_file", RiskLevel.CAUTION, "", {"path": "/x/other.md"}, may_change=("/x/a.md",)
    )
    assert "read-only" in scoped and "this run may change only /x/a.md" in scoped
