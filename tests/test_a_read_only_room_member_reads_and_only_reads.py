"""A room member shown as "Read-only tools" is offered the tools that read, and only those.

Driven through PersonalClaw's own loop (``NativeAgentRuntime``) with the platform's real file
tools, behind the shipped ``rooms.turn.run_member_turn``, so what is asserted is what the member's
model is handed and what its calls do — not what a stand-in was told to answer. Three claims:

* **What it is offered.** The tool block its model receives names the tools that declare they only
  read. A tool that writes, a shell, and a write that never asks anybody are left out.
* **Why a call is refused.** A call to a tool it was not offered is refused on the transcript as
  outside its read-only tools, including a shell command that only reads, and including a write
  that would otherwise run without asking.
* **Where it reaches.** The folders the owner allowed in Settings → Agent defaults → Allowed
  working directories are within its file tools' reach, as they are a subagent's: it reads a file
  there, and it still cannot write one.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from personalclaw.agents.native.builtin_tools import (
    PLATFORM_CATEGORIES,
    PLATFORM_PROVIDER_NAME,
    NativeBuiltinToolProvider,
)
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    AgentEvent,
)
from personalclaw.rooms import posture, store, turn
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

MEMBER = "talk-editor"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """The home, the user's own folder and the operator ceiling are this test's own."""
    import personalclaw.config.loader as cfg
    from personalclaw.guardrails.budgets import reset_meter
    from personalclaw.guardrails.ceiling import reset_ceiling

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path / "home")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "home").mkdir()
    reset_ceiling()
    reset_meter()
    yield tmp_path
    reset_ceiling()
    reset_meter()


@pytest.fixture
def garden(tmp_path) -> Path:
    """A notes folder in the user's own home, outside the workspace, with one note in it."""
    folder = tmp_path / "user" / "Notes" / "Writing" / "Talks"
    folder.mkdir(parents=True)
    (folder / "outline.md").write_text("Section 6: the live crash demo.\n", encoding="utf-8")
    return folder


@pytest.fixture
def enabled(monkeypatch):
    """Rooms on, one agent binding, and the notes folder allowed in Agent defaults."""
    from personalclaw.config.loader import AgentProfile, AppConfig

    cfg = AppConfig()
    cfg.rooms.enabled = True
    cfg.agents = {MEMBER: AgentProfile()}
    cfg.agent.subagent_cwd_allowed_roots = ["~/Notes/Writing"]
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: cfg))
    return cfg


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
    """A tool that writes and asks nobody first, as the knowledge and task tools do."""

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
            )
        ]

    async def invoke(self, tool_name, arguments):
        self.invoked.append(arguments)
        return ToolResult(success=True, output="created")


class _NativeSessions:
    """A SessionManager stand-in that builds each member PersonalClaw's own loop over the platform's
    file tools, rooted where the session is asked to root them — the bridge's construction."""

    def __init__(self, workspace: Path, model: _ScriptedModel) -> None:
        self.workspace = workspace
        self.model = model
        self.knowledge = _Knowledge()
        self.runtimes: dict[str, NativeAgentRuntime] = {}
        self.asked_with: dict[str, dict] = {}

    async def get_or_create(self, key, agent=None, **kwargs):
        self.asked_with[key] = dict(kwargs)
        is_new = key not in self.runtimes
        if is_new:
            platform = NativeBuiltinToolProvider(
                cwd=self.workspace,
                agent=agent or "",
                session_key=key,
                extra_roots=[Path(r) for r in (kwargs.get("extra_tool_roots") or [])],
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
            self.runtimes[key] = runtime
        return self.runtimes[key], is_new, False

    def release(self, key, *, cleanup=False):
        pass


def _call(name: str, args: dict, call_id: str = "c1") -> AgentEvent:
    return AgentEvent(
        kind=EVENT_TOOL_CALL, tool_call_id=call_id, title=name, tool_input=json.dumps(args)
    )


def _turns(*calls: AgentEvent) -> list[list[AgentEvent]]:
    """One turn that makes *calls*, then one that answers in text."""
    return [
        [*calls, AgentEvent(kind=EVENT_COMPLETE)],
        [
            AgentEvent(kind=EVENT_TEXT_CHUNK, text="Cut the live demo."),
            AgentEvent(kind=EVENT_COMPLETE),
        ],
    ]


def _drive(tmp_path, model: _ScriptedModel) -> tuple[_NativeSessions, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    room = store.create_room("Is the live crash demo worth the risk?")
    store.add_member(room.id, MEMBER, role_blurb="tough editor")
    store.append_message(
        room.id, role="user", content="My talk notes are in ~/Notes/Writing/Talks.", speaker=""
    )
    sessions = _NativeSessions(workspace, model)
    asyncio.run(turn.run_member_turn(sessions, room.id, MEMBER))
    return sessions, room.id


def _notes(room_id: str) -> list[str]:
    return [m["content"] for m in store.read_messages(room_id) if m["role"] == store.ROOM_NOTE_ROLE]


def _tool_results(model: _ScriptedModel) -> list[str]:
    """What the second model call was handed back from the first call's tools."""
    return [str(m.get("content", "")) for m in model.messages_seen[1] if m.get("role") == "tool"]


def test_a_read_only_member_is_offered_only_the_tools_that_read(enabled, tmp_path, garden):
    model = _ScriptedModel(_turns())
    _drive(tmp_path, model)

    offered = set(model.tools_seen[0])
    assert {"read_file", "list_dir", "glob", "grep"} <= offered, offered
    for tool in ("write_file", "edit_file", "bash", "knowledge_create"):
        assert tool not in offered, f"a read-only member was offered {tool}: {sorted(offered)}"


def test_a_read_only_members_shell_command_is_refused_as_read_only(enabled, tmp_path, garden):
    """A command that only reads is still a shell, and a shell is not among its tools: refused as
    outside its read-only tools, never as a missing approver."""
    model = _ScriptedModel(_turns(_call("bash", {"command": "ls ~/Notes/Writing/Talks"})))
    _, room_id = _drive(tmp_path, model)

    notes = _notes(room_id)
    assert len(notes) == 1, notes
    assert f"{MEMBER} was refused bash" in notes[0], notes
    assert "read-only" in notes[0], notes
    assert posture.NO_APPROVER_REASON not in notes[0], notes


def test_a_write_that_asks_nobody_is_refused_not_run(enabled, tmp_path, garden):
    model = _ScriptedModel(_turns(_call("knowledge_create", {"title": "Demo plan"})))
    sessions, room_id = _drive(tmp_path, model)

    assert sessions.knowledge.invoked == [], "a read-only member's write ran"
    notes = _notes(room_id)
    assert len(notes) == 1 and f"{MEMBER} was refused knowledge_create" in notes[0], notes
    assert "read-only" in notes[0], notes


def test_the_owners_allowed_folder_is_within_the_members_reach(enabled, tmp_path, garden):
    """It reads a note in the folder the owner allowed, by the path the owner wrote."""
    model = _ScriptedModel(_turns(_call("read_file", {"path": "~/Notes/Writing/Talks/outline.md"})))
    sessions, room_id = _drive(tmp_path, model)

    (key,) = sessions.asked_with
    # The reach is the owner's setting as it stands at each call (`file_scope`), not a copy handed
    # to the session when it opened, which would outlive the folder's removal from the setting.
    assert not sessions.asked_with[key].get("extra_tool_roots")
    results = _tool_results(model)
    assert any("Section 6: the live crash demo." in r for r in results), results
    assert _notes(room_id) == []


def test_the_allowed_folder_is_still_read_only_for_a_read_only_member(enabled, tmp_path, garden):
    target = garden / "outline.md"
    model = _ScriptedModel(
        _turns(_call("write_file", {"path": str(target), "content": "rewritten"}))
    )
    _, room_id = _drive(tmp_path, model)

    assert target.read_text(encoding="utf-8") == "Section 6: the live crash demo.\n"
    notes = _notes(room_id)
    assert len(notes) == 1 and f"{MEMBER} was refused write_file" in notes[0], notes
