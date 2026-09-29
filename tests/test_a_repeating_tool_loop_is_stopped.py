"""A tool loop that keeps getting the same answers is refused, then stopped, and says so.

Measured in a chat on a local model: asked for a standup without the path it needed, the agent
ran 65 shell calls in one turn — 38 distinct commands, ``ls -F`` eight times, ``ls -d */`` six,
``find . -name .git -type d`` five — each answered exactly as before, for 1.2M tokens and eight
minutes, and nothing stopped it. The loop breaker's success path saw only a call repeated
IN A ROW (it happened once) or an exact two- or three-call rotation (never), and it only ever
warned.

What a repeat is, here: a call that only reads, answering exactly what it answered last time,
wherever else in the turn the two calls fell. A read repeated a fourth time is refused before it
runs (it cannot tell the model anything new by being run again), and a turn with more repeats
than the ceiling is stopped, with a sentence the chat shows. A call that acts is never refused or
counted: an agent that edits and re-runs its tests is re-checking, and an unchanged answer is news.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.guardrails import loop_breaker as lb
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult


def _sig(tool: str, args: dict, result: str) -> str:
    return f"{lb.params_key(tool, args)}\x1f{lb.result_digest(result)}"


def _shell(command: str) -> dict:
    return {"command": command}


def _reads(command: str) -> bool:
    return lb.only_reads("bash", "", _shell(command), RiskLevel.DESTRUCTIVE)


def _ran(breaker: lb.LoopBreaker, command: str, result: str) -> str:
    """Record one successful shell call the way a runtime does; return any warning."""
    return breaker.record_structural(_sig("bash", _shell(command), result), reads=_reads(command))


# ── what only reads ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "ls -F",
        "find . -name .git -type d",
        # Reads the approval screen cannot prove (a quoted alternation, sed/xargs in a pipe):
        # what they do to the workspace is still only read it.
        'ls -R | grep -E "feed|git" | cut -d/ -f1 | sort -u',
        'find . -type d -name ".git" | sed "s|.*/||" | sort -u',
        "ls -d */ | xargs -I {} find {} -name .git -type d",
    ],
)
def test_a_read_only_reads(command):
    assert _reads(command)


@pytest.mark.parametrize(
    "command",
    ["sed -i 's/a/b/' app.py", "pytest -q", "mkdir -p out", "ls > listing.txt", "cd x && make"],
)
def test_anything_else_acts(command):
    assert not _reads(command)


def test_a_tool_reads_only_when_it_declares_so():
    assert not lb.only_reads("edit_file", "", {"path": "a.py"}, RiskLevel.CAUTION)
    assert lb.only_reads("read_file", "", {"path": "a.py"}, RiskLevel.SAFE)


# ── the breaker ───────────────────────────────────────────────────────────────


def test_the_same_answer_three_times_in_a_turn_is_no_progress_even_with_calls_between():
    breaker = lb.LoopBreaker()
    notes = []
    for _ in range(3):
        notes.append(_ran(breaker, "ls -F", "notes/\nsrc/\n"))
        notes.append(_ran(breaker, "find . -name .git -type d", ""))
    assert notes[4] and "same result 3 times" in notes[4], notes
    assert "will be refused" in notes[4]


def test_a_read_that_returned_the_same_result_three_times_is_refused():
    breaker = lb.LoopBreaker()
    key = lb.params_key("bash", _shell("ls -F"))
    for _ in range(3):
        assert breaker.refusal("bash", key, reads=True) == ""
        _ran(breaker, "ls -F", "notes/\nsrc/\n")
        _ran(breaker, "ls -d */", "notes/\nsrc/\n")

    refusal = breaker.refusal("bash", key, reads=True)
    assert refusal.startswith("Error: tool `bash` was not run")
    assert "the same result the last 3 times it ran this turn" in refusal


def test_a_new_answer_starts_the_count_again():
    breaker = lb.LoopBreaker()
    key = lb.params_key("bash", _shell("ls -F"))
    for listing in ("a\n", "a\n", "a\nb\n", "a\nb\n"):
        _ran(breaker, "ls -F", listing)
    assert breaker.repeat_count(key) == 2
    assert breaker.refusal("bash", key, reads=True) == ""


def test_re_running_a_call_that_acts_is_never_refused_or_counted():
    """Edit, run the tests, edit, run them again: the unchanged answer is the check passing."""
    breaker = lb.LoopBreaker()
    tests = lb.params_key("bash", _shell("pytest -q"))
    notes = []
    for n in range(6):
        breaker.record_structural(_sig("edit_file", {"path": f"mod{n}.py"}, "edited"), reads=False)
        notes.append(_ran(breaker, "pytest -q", "12 passed"))
    assert breaker.refusal("bash", tests, reads=False) == ""
    assert breaker.total_repeats == 0
    assert not any(notes), notes


def test_a_call_that_acts_is_still_warned_when_it_repeats_back_to_back():
    breaker = lb.LoopBreaker()
    notes = [_ran(breaker, "pytest -q", "1 failed") for _ in range(3)]
    assert notes[2] == "the same tool call produced the same result 3 times in a row", notes
    assert breaker.refusal("bash", lb.params_key("bash", _shell("pytest -q")), reads=False) == ""


def test_a_status_poll_is_waiting_and_is_never_refused_or_counted():
    breaker = lb.LoopBreaker()
    key = lb.params_key("bash", _shell("git status"))
    for _ in range(12):
        _ran(breaker, "git status", "nothing to commit, working tree clean")
    assert breaker.refusal("bash", key, reads=True) == ""
    assert breaker.total_repeats == 0
    assert breaker.stop_sentence() == ""


@pytest.mark.parametrize(
    "pause",
    [("wait", {"seconds": 60}), ("bash", {"command": "sleep 30"})],
    ids=["wait-tool", "sleep"],
)
def test_a_wait_in_between_makes_the_next_answer_news(pause):
    breaker = lb.LoopBreaker()
    key = lb.params_key("bash", _shell("cat build/status.txt"))
    for _ in range(5):
        _ran(breaker, "cat build/status.txt", "building")
        breaker.record_structural(_sig(*pause, ""), reads=False)
    assert breaker.refusal("bash", key, reads=True) == ""


def test_repeats_past_the_ceiling_stop_the_turn_with_a_sentence():
    breaker = lb.LoopBreaker()
    key = lb.params_key("bash", _shell("ls -F"))
    for _ in range(3):
        _ran(breaker, "ls -F", "notes/\n")
    assert breaker.stop_sentence() == ""
    while breaker.total_repeats <= lb.REPEAT_CIRCUIT_THRESHOLD:
        assert breaker.refuse("bash", key, reads=True)

    sentence = breaker.stop_sentence()
    assert sentence.startswith("Run aborted by the loop breaker: ")
    assert f"{lb.REPEAT_CIRCUIT_THRESHOLD + 1} tool calls in this turn repeated" in sentence


def test_a_compaction_rearms_the_repeat_path_but_keeps_its_count():
    breaker = lb.LoopBreaker()
    key = lb.params_key("bash", _shell("ls -F"))
    for _ in range(3):
        _ran(breaker, "ls -F", "notes/\n")
    counted = breaker.total_repeats
    breaker.reset_structural()
    assert breaker.refusal("bash", key, reads=True) == ""
    assert breaker.total_repeats == counted


# ── the native runtime, on the measured loop ──────────────────────────────────

#: The order a local model issued its shell calls in, in a measured turn looking for
#: repositories in a workspace that had none. Indexes into ``_COMMANDS``.
_ORDER = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 9, 13, 14, 15, 9, 16, 17, 18, 0, 19, 20, 21, 22, 9,
    17, 0, 23, 24, 25, 9, 0, 18, 26, 27, 28, 13, 29, 24, 24, 0, 17, 30, 31, 17, 22, 32, 33, 34, 5,
    35, 0, 26, 24, 17, 0, 24, 0, 36, 37, 35, 17, 22,
]  # fmt: skip

#: The commands it repeated, as it wrote them; every other index was a one-off `find` variant.
_REPEATED = {
    0: "ls -F",
    5: 'find . -maxdepth 3 -name ".git" -type d',
    9: "find . -name .git -type d",
    13: "ls -d */ | xargs -I {} find {} -name .git -type d",
    17: "ls -d */",
    18: "ls -R | grep feed | sort -u",
    22: 'ls -R | grep "feed" | cut -d/ -f1 | sort -u',
    24: 'ls -R | grep -E "feed|git" | cut -d/ -f1 | sort -u',
    25: "git -C . log --oneline",
    26: 'find . -name ".git" -type d',
    35: 'ls -R | grep -E "git" | cut -d/ -f1 | sort -u',
}


def _command(index: int) -> str:
    return _REPEATED.get(index, f"find . -maxdepth 2 -type d -name 'v{index}'")


def _output(command: str) -> str:
    """What the workspace answered: a listing for a listing, nothing for a search."""
    if command.startswith(("ls -F", "ls -d")):
        return "HEARTBEAT.md\nknowledge/\nmemory/\noutbox/\n"
    if command.startswith("ls -R"):
        return "knowledge\nmemory\noutbox\n"
    return ""


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
            ),
            ToolDefinition(
                name="edit_file",
                description="Edit a file.",
                parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
            ),
        ]

    async def invoke(self, tool_name, arguments):
        if tool_name == "edit_file":
            return ToolResult(success=True, output=f"edited {arguments.get('path')}")
        command = arguments.get("command", "")
        self.ran.append(command)
        return ToolResult(
            success=True, output="12 passed" if command == "pytest -q" else _output(command)
        )


class _Script:
    """A model that makes the calls it was scripted to, whatever it is told — the worst case."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self._calls = calls
        self.inferences = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.inferences += 1
        if self.inferences <= len(self._calls):
            name, args = self._calls[self.inferences - 1]
            import json

            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"c{self.inferences}",
                title=name,
                tool_input=json.dumps(args),
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Here is your standup.")
        yield AgentEvent(kind=EVENT_COMPLETE)


def _turn(calls):
    shell = _Shell()
    model = _Script(calls)
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[shell],
    )

    async def _run():
        await runtime.start()
        return [ev async for ev in runtime.stream("draft my standup")]

    return asyncio.run(asyncio.wait_for(_run(), timeout=30)), shell, model


def test_the_measured_loop_is_refused_then_stopped_well_before_its_end():
    events, shell, model = _turn([("bash", _shell(_command(i))) for i in _ORDER])

    final = events[-1]
    assert final.kind == EVENT_COMPLETE
    assert final.stop_reason == "cancelled"
    assert final.text.startswith("Run aborted by the loop breaker: ")
    assert "repeated an earlier call" in final.text
    # Stopped at about half of the 65 calls it made unchecked.
    assert model.inferences < 40, model.inferences
    results = [str(ev.tool_output) for ev in events if ev.kind == EVENT_TOOL_RESULT]
    refused = [r for r in results if "was not run" in r]
    assert refused, "no repeated read was refused"
    assert len(shell.ran) == len(results) - len(refused)


def test_editing_and_re_running_the_tests_is_never_a_loop():
    calls = []
    for n in range(12):
        calls += [("edit_file", {"path": f"src/mod{n}.py"}), ("bash", _shell("pytest -q"))]
    events, shell, _model = _turn(calls)

    final = events[-1]
    assert final.stop_reason != "cancelled" and not final.text
    assert shell.ran.count("pytest -q") == 12
    assert not any(
        "was not run" in str(ev.tool_output) for ev in events if ev.kind == EVENT_TOOL_RESULT
    )


# ── the chat shows why ────────────────────────────────────────────────────────


def _chat_state(tmp_path, provider_id: str):
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    client = AsyncMock()
    client.provider_id = provider_id
    client.context_usage_pct = MagicMock(return_value=None)
    del client.cancel_session
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = ToolHookResult.allow()
    builder.build_message.return_value = ("hello", None)
    state.context_builder = builder
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state, client


async def _events(items):
    for item in items:
        yield item


async def _chat_turn(state, session):
    from personalclaw.dashboard.chat_runner import run_chat

    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "draft my standup")


@pytest.mark.asyncio
async def test_the_chat_shows_the_sentence_a_native_turn_was_stopped_with(tmp_path):
    state, client = _chat_state(tmp_path, "native")
    stopped = lb.repeat_circuit_message(lb.REPEAT_CIRCUIT_THRESHOLD + 1)
    client.stream = MagicMock(
        side_effect=lambda *a, **kw: _events(
            [AgentEvent(kind=EVENT_COMPLETE, stop_reason="cancelled", text=stopped)]
        )
    )
    session = _ChatSession("chat-4-loop")

    await _chat_turn(state, session)

    errors = [m for m in session.messages if m.get("role") == "error"]
    assert [m.get("content") for m in errors] == [stopped]
    # A turn the breaker stopped did not do its work: a loop must not advance on it.
    assert session._last_turn_errored is True


@pytest.mark.asyncio
async def test_an_acp_turn_repeating_one_answer_is_aborted(tmp_path):
    state, client = _chat_state(tmp_path, "acp:test-cli")
    frames = []
    for i in range(lb.REPEAT_CIRCUIT_THRESHOLD + 4):
        frames += [
            AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"t{i}",
                title="bash",
                tool_input='{"command": "ls -F"}',
            ),
            AgentEvent(
                kind=EVENT_TOOL_RESULT,
                tool_call_id=f"t{i}",
                tool_output="knowledge/\nmemory/\n",
                tool_meta={"ok": True},
            ),
        ]
    frames.append(AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"))
    client.stream = MagicMock(side_effect=lambda *a, **kw: _events(frames))
    session = _ChatSession("chat-5-loop")
    session.acp_provider = "acp:test-cli"

    await _chat_turn(state, session)

    texts = [str(m.get("content", "")) for m in session.messages]
    aborted = [t for t in texts if t.startswith("Run aborted by the loop breaker: ")]
    assert len(aborted) == 1 and "repeated an earlier call" in aborted[0], texts[-4:]
    client.cancel.assert_awaited()
    assert session._last_turn_errored is True
