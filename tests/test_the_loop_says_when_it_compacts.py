"""The native loop says when it compacts a conversation on its own, in `/compact`'s words.

The loop compacts its own history when the context crosses the Settings threshold, and again when
a model rejects a prompt as too long — and said nothing either time. The conversation lost its
middle without a word: the notice the dashboard posts when it restarts a session
(`DashboardState.wire_session_compact_callback`) is never reached by a loop that compacts itself,
because the session manager leaves that loop alone. `/compact` answered "Conversation compacted:
freed 42% of the conversation (…)"; the same pass run on its own now says exactly that, where it
happened, and keeps the answer that streamed before it.
"""

from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.agents.native import runtime as runtime_mod
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.dashboard.chat_utils import _broadcast_compaction_result
from personalclaw.llm.events import (
    COMPACTION_AUTOMATIC,
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    AgentEvent,
)

#: `/compact`'s own sentence, which the automatic notice must match word for word.
_SUMMARY = re.compile(r"^freed \d+% of the conversation \([\d,]+ → [\d,]+ characters\)$")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home whose config.json sets the threshold, the way Settings writes it."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))

    def set_threshold(pct: float) -> None:
        (tmp_path / "config.json").write_text(
            json.dumps({"session": {"autocompact_pct": pct}}), encoding="utf-8"
        )

    return set_threshold


class _Model:
    """A model that reports the context usage it is given, or fails its first call with *first*."""

    supports_tools = False
    _model = "fake"

    def __init__(self, context_pct: float = 0.0, *, first: BaseException | None = None) -> None:
        self.context_pct = context_pct
        self.first = first
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        if self.first is not None and self.calls == 1:
            raise self.first
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield AgentEvent(
            kind=EVENT_COMPLETE,
            input_tokens=1,
            output_tokens=1,
            context_usage_pct=self.context_pct,
        )


def _runtime(model: _Model) -> NativeAgentRuntime:
    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="fake"),
        model_provider=model,
        tool_providers=[],
    )


def _convo(rounds: int = 10) -> list[dict]:
    """A history the structured pass can shrink: verbose tool results in the middle."""
    msgs: list[dict] = [{"role": "user", "content": "first message"}]
    for i in range(rounds):
        msgs.append(
            {
                "role": "assistant",
                "content": f"step {i}",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "X" * 2000})
    msgs.append({"role": "user", "content": "latest request"})
    msgs.append({"role": "assistant", "content": "latest reply"})
    return msgs


def _said(event: AgentEvent) -> str | None:
    """What the chat posts for *event*."""
    return _broadcast_compaction_result(MagicMock(), MagicMock(key="s1"), event)


class TestTheLoopAnnouncesItsOwnPass:
    @pytest.mark.asyncio
    async def test_a_turn_it_compacts_says_so_before_it_answers(self, home):
        """🔴 Red before: the threshold pass rewrote the history and the stream carried no word
        of it."""
        home(50)
        model = _Model(60.0)
        rt = _runtime(model)
        await rt.start()
        rt._messages = _convo()
        [ev async for ev in rt.stream("turn one")]  # measures 60%, over the 50% set

        events = [ev async for ev in rt.stream("turn two")]

        kinds = [ev.kind for ev in events]
        assert EVENT_COMPACTION_STATUS in kinds
        notice = events[kinds.index(EVENT_COMPACTION_STATUS)]
        assert notice.text == COMPACTION_AUTOMATIC
        assert _SUMMARY.match(notice.title), notice.title
        assert kinds.index(EVENT_COMPACTION_STATUS) < kinds.index(EVENT_TEXT_CHUNK)

    @pytest.mark.asyncio
    async def test_a_turn_under_the_threshold_says_nothing(self, home):
        home(90)
        rt = _runtime(_Model(10.0))
        await rt.start()
        rt._messages = _convo()
        [ev async for ev in rt.stream("turn one")]
        events = [ev async for ev in rt.stream("turn two")]
        assert EVENT_COMPACTION_STATUS not in [ev.kind for ev in events]

    @pytest.mark.asyncio
    async def test_the_overflow_retry_says_so_too(self, home, monkeypatch):
        """🔴 Red before: a prompt rejected as too long was compacted and retried in silence."""
        home(90)
        monkeypatch.setattr(runtime_mod, "record_attempt", lambda *a, **k: None)
        monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
        model = _Model(first=RuntimeError("context_length_exceeded"))
        rt = _runtime(model)
        await rt.start()
        rt._messages = _convo()

        events = [ev async for ev in rt.stream("go")]

        assert model.calls == 2, "the retry ran"
        notices = [ev for ev in events if ev.kind == EVENT_COMPACTION_STATUS]
        assert [ev.text for ev in notices] == [COMPACTION_AUTOMATIC]
        assert _SUMMARY.match(notices[0].title), notices[0].title

    @pytest.mark.asyncio
    async def test_it_is_compacts_own_sentence(self, home):
        """The same pass over the same history reads the same, whoever started it."""
        home(50)
        by_itself, by_command = _runtime(_Model()), _runtime(_Model())
        by_itself._messages, by_command._messages = _convo(), _convo()
        by_itself._last_context_pct = 60.0

        automatic = by_itself._compacted_on_its_own(*by_itself._maybe_compact())
        (command,) = [ev async for ev in by_command.stream_command("/compact")]

        assert automatic.title == command.title
        assert _said(automatic) == _said(command) == f"Conversation compacted: {command.title}"


# ── the chat: said where it happened, and the answer before it stays ─────────────────────────


def _client(stream) -> AsyncMock:
    client = AsyncMock()
    client.provider_id = "scripted"
    client.context_usage_pct = MagicMock(return_value=10.0)
    client.stream = MagicMock(side_effect=lambda *a, **kw: stream())
    return client


def _state(tmp_path, client):
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.record_success = MagicMock()
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    cb.conversation_log = MagicMock()
    cb.conversation_log.read_messages = MagicMock(return_value=[])
    state.context_builder = cb
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.push_refresh = MagicMock()
    return state


@pytest.mark.asyncio
async def test_the_chat_says_it_where_it_happened_and_keeps_the_answer(tmp_path, monkeypatch):
    """🔴 Red before: the chat posted nothing for the loop's own pass. A `completed` status —
    `/compact`'s — would have been worse: the runner lets a command's result replace what
    streamed before it, which mid-turn is the answer."""
    from personalclaw.dashboard.chat_runner import run_chat

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    summary = "freed 40% of the conversation (10,000 → 6,000 characters)"

    async def stream():
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Looking through the logs.")
        yield AgentEvent(kind=EVENT_COMPACTION_STATUS, text=COMPACTION_AUTOMATIC, title=summary)
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Here is what I found.")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    state = _state(tmp_path, _client(stream))
    session = state.get_or_create_session("s1")
    session._trust = True
    session._titled = True
    with (
        patch("personalclaw.dashboard.chat_runner.sel", MagicMock()),
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await run_chat(state, session, "why did the job fail?")
        while session.task is not None and not session.task.done():
            await asyncio.wait_for(session.task, timeout=10)

    said = [m["content"] for m in session.messages if m.get("role") == "assistant"]
    assert said == [
        "Looking through the logs.",
        f"Conversation compacted: {summary}",
        "Here is what I found.",
    ]
