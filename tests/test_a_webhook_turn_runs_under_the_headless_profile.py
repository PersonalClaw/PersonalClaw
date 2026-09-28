"""A webhook's agent turn runs under the headless profile: read tools only, and nobody is asked.

A webhook fires an agent turn for whoever holds its token (``POST /api/hooks/agent``), in a
``hook:`` session nobody watches. That key was none of the unattended families
(``guardrails.policy``), so the turn resolved the interactive profile, the posture of a chat
someone is reading: every tool granted, the tools that ask a person something offered, and a call
that needed approval parked on a prompt nobody would see.

Now a ``hook:`` session is unattended and gets the headless profile, whose tool grants are
``read``. A call whose tool declares neither a read nor a proposal is refused before anything could
approve it, a read runs, a call that needs approval (a proposal) is declined at once, and a tool
that asks a person is not offered. A turn on an agent CLI, which runs its tools where the host
cannot hold them to the grants, is refused before its message is sent, and says why.

Driven through the real ``_run_hook_agent`` and ``SessionManager`` on the real
``NativeAgentRuntime``, answered by a scripted model.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.config import AppConfig
from personalclaw.dashboard.handlers import hooks as hooks_mod
from personalclaw.guardrails.policy import is_unattended_session, profile_for_session
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.session import SessionManager
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

#: A tool that changes something and asks nobody first: only the tool grants stand between a
#: webhook's turn and it.
WRITES = "write_note"
#: A tool that declares it only reads.
READS = "read_note"
#: A tool whose only effect is a proposal for the owner: the read grant admits it, and it asks
#: first, since it is not a read (a read asks nobody). Nobody can answer on a webhook's turn.
ASKS = "propose_tidy"
#: A tool whose whole job is to ask a person something.
INTERACTIVE = "ask_owner"


class _Tools(ToolProvider):
    def __init__(self) -> None:
        self.ran: list[str] = []

    @property
    def name(self) -> str:
        return "notes"

    @property
    def display_name(self) -> str:
        return "Notes"

    async def list_tools(self) -> list[ToolDefinition]:
        def tool(name: str, **kw: Any) -> ToolDefinition:
            return ToolDefinition(name=name, description="d", parameters={"type": "object"}, **kw)

        return [
            tool(WRITES, requires_approval=False),
            tool(READS, requires_approval=False, risk_level=RiskLevel.SAFE),
            tool(ASKS, requires_approval=True, proposes=True),
            tool(INTERACTIVE, requires_approval=False, risk_level=RiskLevel.SAFE, interactive=True),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.ran.append(tool_name)
        return ToolResult(success=True, output=f"{tool_name} done")


def _calling(*tools: str) -> _ScriptedModel:
    """A model that calls each of *tools*, one per step, and then answers."""
    script = [
        [
            AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=f"call-{n}", title=t, tool_input="{}"),
            AgentEvent(kind=EVENT_COMPLETE),
        ]
        for n, t in enumerate(tools, 1)
    ]
    script.append([AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)])
    return _ScriptedModel(script)


def _observations(model: _ScriptedModel) -> dict[str, str]:
    """What each tool call answered, as the model was handed it on its last step."""
    answered: dict[str, str] = {}
    for message in model.seen_messages[-1]:
        if message.get("role") == "tool":
            answered[str(message.get("tool_call_id", ""))] = str(message.get("content", ""))
    return answered


@pytest.fixture
def hook(monkeypatch):
    """Fire a webhook's turn through `_run_hook_agent`: returns what ran, what the model was told,
    what the turn's audit outcome was and what the owner was notified."""
    audit: list[str] = []
    notes: list[str] = []
    monkeypatch.setattr(
        hooks_mod,
        "_sel",
        lambda: SimpleNamespace(log_tool_invocation=lambda **kw: audit.append(kw["outcome"])),
    )
    monkeypatch.setattr("personalclaw.channel_delivery.deliver_to_owner", AsyncMock())

    def fire(factory, session_key: str = "hook:nightly") -> SimpleNamespace:
        state = SimpleNamespace(
            sessions=SessionManager(AppConfig(), provider_factory=factory),
            context_builder=None,
            notify=lambda _kind, _title, body, meta=None: notes.append(body),
        )

        async def _go() -> None:
            await hooks_mod._hook_semaphore.acquire()
            await hooks_mod._run_hook_agent(
                state, session_key, "tidy the notes", "Nightly", None, True, 60
            )

        asyncio.run(_go())
        return SimpleNamespace(audit=audit, notes=notes)

    return fire


def _native(model: _ScriptedModel, tools: _Tools):
    def factory(_key: Any = None, **kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(),
            model_provider=model,
            tool_providers=[tools],
            unattended=bool(kw.get("unattended")),
        )

    return factory


def test_a_hook_session_is_unattended_and_resolves_the_headless_profile():
    """🔴 Red before the fix: `hook:` was no unattended family, so it resolved INTERACTIVE."""
    assert is_unattended_session("hook:nightly") is True
    profile = profile_for_session("hook:nightly")
    assert (profile.name, profile.tool_grants) == ("headless", "read")


def test_a_webhook_turn_s_write_is_refused_and_its_read_runs(hook):
    """🔴 Red before the fix: nothing held the turn to read tools, so the write ran."""
    tools, model = _Tools(), _calling(WRITES, READS)
    fired = hook(_native(model, tools))

    assert tools.ran == [READS]
    told = _observations(model)
    assert "write-class and the 'headless' profile grants 'read' tools only" in told["call-1"]
    assert told["call-2"] == f"{READS} done"
    assert fired.audit == ["completed"]


def test_the_same_write_runs_in_a_session_a_person_watches(hook):
    """The control: the tool works, and a watched session's profile grants it, so the refusal
    above is the headless profile's doing."""
    tools, model = _Tools(), _calling(WRITES)
    hook(_native(model, tools), session_key="dashboard:watched")

    assert tools.ran == [WRITES]


def test_a_webhook_turn_s_call_that_needs_approval_is_declined_at_once(hook):
    """🔴 Red before the fix: the call waited on an approval prompt nobody was shown."""
    tools, model = _Tools(), _calling(ASKS)
    hook(_native(model, tools))

    assert tools.ran == []
    assert "the run is unattended" in _observations(model)["call-1"]


def test_a_webhook_turn_is_not_offered_a_tool_that_asks_a_person(hook):
    tools, model = _Tools(), _calling(READS)
    hook(_native(model, tools))

    offered = {str((t.get("function") or t).get("name", "")) for t in model.last_tools or []}
    assert READS in offered, f"vacuity: the offered tools were not read ({offered})"
    assert INTERACTIVE not in offered


def test_a_webhook_turn_on_an_agent_cli_is_refused_before_it_is_sent(hook):
    """An agent CLI runs its tools where the host never sees them, so nothing could hold it to
    the grants: the turn is refused, and the owner is told why and what to change."""
    sent: list[str] = []

    class _AgentCli:
        """An agent CLI runtime's shape: it streams, and has no tool grants to set."""

        async def start(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

        async def stream(self, message: str):
            sent.append(message)
            yield AgentEvent(kind=EVENT_COMPLETE)

    fired = hook(lambda _key=None, **_kw: _AgentCli())

    assert sent == []
    assert fired.audit == ["refused_not_headless"]
    (note,) = fired.notes
    assert note.startswith("Hook agent not run: The default agent runs on an agent CLI")
    assert "Point the webhook at an agent on the native runtime." in note
