"""A loop worker is told where its own files are, and a cycle that repeats its reads is stopped.

Measured on a Code loop over an existing repository: the per-task worker works in its own
checkout, while the loop's status file and brief sit in the loop's own folder. Its instructions
said to "Read status.json in the project dir" and its cycle message never named the folder, so
it ran ``find / -name status.json`` six times and listed its checkout fourteen times in one
cycle, each answered exactly as before.

So: every worker's cycle message names the loop's status file and brief by their full path, the
worker's instructions say the message names them, and a loop worker's cycle gets the same
repeat guard a chat turn does, whether the loop is attended or not.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from personalclaw.agents.defaults import CODER_SYSTEM_PROMPT
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.guardrails import loop_breaker as lb
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.loop import kinds, manager
from personalclaw.loop.loop import Loop
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

LOOP_DIR = "/data/loop/0a1b2c3d"
CHECKOUT = "/data/projects/p-1/worktrees/t-1a2b3c4d"


def _task():
    return SimpleNamespace(
        id="t-1a2b3c4d",
        title="Stop escaping digest titles twice",
        description="",
        action_plan=[],
        exit_criteria=[],
    )


def _code_loop() -> Loop:
    return Loop(id="0a1b2c3d", name="digest titles", kind="code", task="fix the digest titles")


# ── the cycle message names the files ─────────────────────────────────────────


@pytest.mark.parametrize("store", ["bundled prompt", "shipped fallback"])
def test_a_task_workers_cycle_message_names_the_loops_status_file_and_brief(store, monkeypatch):
    if store == "shipped fallback":
        monkeypatch.setattr(
            "personalclaw.prompt_providers.runtime.render_use_case_prompt", lambda *a, **k: None
        )

    nudge = manager._task_cycle_nudge(_code_loop(), _task(), CHECKOUT, LOOP_DIR)

    assert f"{LOOP_DIR}/status.json" in nudge
    assert f"{LOOP_DIR}/brief.md" in nudge
    assert "not in this checkout" in nudge


@pytest.mark.parametrize("kind", ["code", "goal", "design", "general", "research"])
def test_every_kinds_cycle_message_names_the_status_file_by_its_full_path(kind):
    kinds.ensure_loaded()
    loop = Loop(id="0a1b2c3d", name="n", kind=kind, task="do the thing")

    nudge = kinds.get(kind).cycle_nudge(loop, LOOP_DIR)

    assert f"{LOOP_DIR}/status.json" in nudge
    assert f"{LOOP_DIR}/brief.md" in nudge


def test_the_code_workers_instructions_point_at_the_path_the_message_names():
    assert "in the project dir" not in CODER_SYSTEM_PROMPT
    assert "at the path this cycle's message names" in CODER_SYSTEM_PROMPT
    assert "never search the disk for it" in CODER_SYSTEM_PROMPT


# ── the repeat guard covers a loop worker's cycle ─────────────────────────────

_LISTING = f"ls -F {CHECKOUT}/"
_SEARCH = "find / -name status.json 2>/dev/null"


@pytest.mark.parametrize("command", [_SEARCH, "ls -F >/dev/null 2>&1", "git log --oneline 2>&1"])
def test_a_read_that_throws_its_output_away_still_only_reads(command):
    assert lb.only_reads("bash", "", {"command": command}, RiskLevel.DESTRUCTIVE)


@pytest.mark.parametrize("command", ["ls -F > listing.txt", "rm -rf build 2>/dev/null"])
def test_a_command_that_writes_or_removes_still_acts(command):
    assert not lb.only_reads("bash", "", {"command": command}, RiskLevel.DESTRUCTIVE)


class _Shell(ToolProvider):
    def __init__(self) -> None:
        self.ran: list[str] = []

    @property
    def name(self) -> str:
        return "shell-under-test"

    @property
    def display_name(self) -> str:
        return "Shell"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="bash",
                description="Run a shell command.",
                parameters={"type": "object", "properties": {"command": {"type": "string"}}},
                requires_approval=False,
                risk_level=RiskLevel.DESTRUCTIVE,
            )
        ]

    async def invoke(self, tool_name, arguments):
        command = arguments.get("command", "")
        self.ran.append(command)
        return ToolResult(
            success=True, output="CHANGELOG.md\nsrc/\ntests/\n" if "ls" in command else ""
        )


class _Script:
    """A model that makes the calls it was scripted to, whatever it is told."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, commands: list[str]) -> None:
        self._commands = commands
        self.inferences = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.inferences += 1
        if self.inferences <= len(self._commands):
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"c{self.inferences}",
                title="bash",
                tool_input=json.dumps({"command": self._commands[self.inferences - 1]}),
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Cycle done.")
        yield AgentEvent(kind=EVENT_COMPLETE)


@pytest.mark.parametrize("unattended", [True, False], ids=["unattended-loop", "attended-loop"])
def test_a_loop_workers_cycle_that_repeats_its_reads_is_refused_then_stopped(unattended):
    # The measured cycle: the checkout listed fourteen times, the disk searched six times.
    commands = [_LISTING, _LISTING, _SEARCH] * 6 + [_LISTING, _LISTING]
    shell = _Shell()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="coder", provider="native", model="scripted"),
        model_provider=_Script(commands),
        tool_providers=[shell],
        unattended=unattended,
    )
    nudge = manager._task_cycle_nudge(_code_loop(), _task(), CHECKOUT, LOOP_DIR)

    async def _cycle():
        await runtime.start()
        return [ev async for ev in runtime.stream(nudge)]

    events = asyncio.run(asyncio.wait_for(_cycle(), timeout=30))

    final = events[-1]
    assert final.kind == EVENT_COMPLETE and final.stop_reason == "cancelled"
    assert final.text.startswith("Run aborted by the loop breaker: "), final.text
    assert shell.ran.count(_LISTING) == 3 and shell.ran.count(_SEARCH) == 3, shell.ran
    refused = [
        ev for ev in events if ev.kind == EVENT_TOOL_RESULT and "was not run" in str(ev.tool_output)
    ]
    assert refused, "no repeated read was refused"
