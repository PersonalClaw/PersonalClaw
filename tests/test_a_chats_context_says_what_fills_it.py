"""A chat's context reading says what fills the window, part by part (#3823).

The context reading used to be one number. The approved addition lists each measured part with
its tokens and its action: the instructions and tool definitions (review the tools), memory and
profile (review what is recalled), attached files, the conversation and tool results (compact).
The parts are counted where the request is assembled, never guessed in the browser, and a part the
gateway does not measure is left out of the frame, so the page says "not measured" for it.

- The turn's assembly names its components ("system prompt", "active recall", "the user's
  request", ...); ``assembly_parts`` groups them into kinds, and an engine that labelled nothing
  measures nothing.
- The native loop holds the whole conversation, so it measures it at each request: each turn's
  message split in its own assembly's proportions, what the model said as conversation, tool
  results as tool results, and the tool definitions it sends as instructions. A message a
  compaction rewrote has no assembly behind it any more, so it is conversation.
- An agent CLI holds its own history, so it measures no parts.
- The frame and session detail carry the parts beside the reading; a restart that clears the
  reading clears its parts too.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentProvider, AgentRuntimeDefinition
from personalclaw.context_headroom import Component, count_tokens
from personalclaw.context_parts import PART_ACTIONS, PART_KINDS, assembly_parts, parts_said
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent

pytestmark = pytest.mark.asyncio


class _Model:
    supports_tools = True
    _model = "recorder"

    def __init__(self) -> None:
        self.requests: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Here is the digest, by day.")
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


async def _runtime(tmp_path: Path) -> NativeAgentRuntime:
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="aide", provider="native", model="recorder"),
        model_provider=_Model(),
        tool_providers=[],
        cwd=tmp_path,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")
    return runtime


async def _turn(runtime: NativeAgentRuntime, components: list[Component]) -> None:
    runtime.stage_context_parts(assembly_parts(components))
    async for _ in runtime.stream("".join(c.text for c in components)):
        pass


SYSTEM = "You are Aide, a careful assistant. " * 30
RECALL = "Robin prefers digests grouped by day. " * 40
REQUEST = "Plan this week's reading digest."


def _components() -> list[Component]:
    return [
        Component(name="system prompt", text=SYSTEM),
        Component(name="active recall", text=RECALL),
        Component(name="the user's request", text=REQUEST, is_request=True),
    ]


def _by_kind(parts: list[dict] | None) -> dict[str, int]:
    assert parts is not None
    return {p["kind"]: p["tokens"] for p in parts}


async def test_the_assembly_groups_its_named_components_into_kinds():
    parts = assembly_parts(_components())
    assert parts == {
        "instructions": count_tokens(SYSTEM),
        "memory": count_tokens(RECALL),
        "conversation": count_tokens(REQUEST),
    }


async def test_an_assembly_that_labelled_nothing_measures_nothing():
    unlabelled = [
        Component(name="assembled prompt (components unlabelled by this engine)", text=SYSTEM)
    ]
    assert assembly_parts(unlabelled) is None
    assert assembly_parts([]) is None


async def test_the_parts_are_said_in_one_order_each_with_its_action_and_unmeasured_ones_left_out():
    said = parts_said({"conversation": 30, "instructions": 10, "tool_results": 5})
    assert [p["kind"] for p in said] == ["instructions", "conversation", "tool_results"]
    assert [p["kind"] for p in said] == [
        k for k in PART_KINDS if k in {"instructions", "conversation", "tool_results"}
    ]
    assert all(p["action"] == PART_ACTIONS[p["kind"]] for p in said)
    assert "files" not in {p["kind"] for p in said}, "files are not measured, so not said"


async def test_the_native_loop_measures_its_whole_history_part_by_part(tmp_path):
    runtime = await _runtime(tmp_path)
    await _turn(runtime, _components())
    parts = _by_kind(runtime.context_parts())
    # The turn's message, split as its assembly measured it; what the model said is conversation.
    reply = count_tokens("Here is the digest, by day.")
    assert parts["memory"] == count_tokens(RECALL)
    assert parts["instructions"] >= count_tokens(SYSTEM)
    assert parts["conversation"] == count_tokens(REQUEST) + reply
    assert "files" not in parts
    # A second turn adds its own split, not a re-count of the first.
    await _turn(
        runtime,
        [
            Component(name="active recall", text=RECALL),
            Component(name="the user's request", text=REQUEST, is_request=True),
        ],
    )
    again = _by_kind(runtime.context_parts())
    assert again["memory"] == 2 * count_tokens(RECALL)
    assert again["conversation"] == 2 * (count_tokens(REQUEST) + reply)


async def test_a_message_a_compaction_rewrote_is_conversation(tmp_path):
    runtime = await _runtime(tmp_path)
    await _turn(runtime, _components())
    # A compaction replaces the history with its digest: no assembly is behind that text.
    digest = "Summary of the conversation so far: Robin asked for a reading digest. " * 5
    runtime._messages[:] = [{"role": "user", "content": digest}]
    parts = _by_kind(runtime.context_parts())
    assert parts.get("memory", 0) == 0
    assert parts["conversation"] == count_tokens(digest)


async def test_tool_results_are_their_own_part(tmp_path):
    runtime = await _runtime(tmp_path)
    await _turn(runtime, _components())
    result = "file contents line\n" * 60
    runtime._messages.append({"role": "tool", "tool_call_id": "c1", "content": result})
    assert _by_kind(runtime.context_parts())["tool_results"] == count_tokens(result)


async def test_a_runtime_that_holds_its_own_history_measures_no_parts():
    # The default an agent CLI's runtime keeps: its history is the CLI's, not the gateway's.
    assert AgentProvider.context_parts(MagicMock()) is None


async def test_the_frame_and_the_detail_carry_the_parts_and_a_cleared_reading_clears_them():
    from personalclaw.dashboard.state import DashboardState

    sent: list[tuple[str, dict]] = []
    state = MagicMock()
    state.broadcast_ws = lambda kind, data: sent.append((kind, data))
    session = MagicMock(key="dashboard:chat-1-x", context_usage={})
    parts = parts_said({"instructions": 12_000, "conversation": 40_000})
    DashboardState.say_context_usage(state, session, 26.0, parts=parts)
    assert sent[-1] == (
        "context_usage",
        {"session": "dashboard:chat-1-x", "pct": 26.0, "parts": parts},
    )
    assert session.context_usage["parts"] == parts
    # A cleared reading (a restart at the threshold, a runtime that measured nothing) clears its
    # parts too, even when a caller hands some over: they are never left as current.
    DashboardState.say_context_usage(state, session, None, parts=parts)
    assert "parts" not in session.context_usage
    assert "parts" not in sent[-1][1]
    # A reading measured without parts says none: the frame has no parts, not an empty list.
    DashboardState.say_context_usage(state, session, 12.0)
    assert "parts" not in sent[-1][1]
