"""One setting decides when a chat compacts.

Settings → Chat → "Auto-compact threshold" (``session.autocompact_pct``, 90 by default) says it
is the context usage that triggers compaction. Measured before this change, it governed neither
of the two things that compact a chat:

* the native loop compacted its own history at a fixed 70% of its own, whatever Settings said;
* the session manager restarted the session at the value the gateway had STARTED with, so a
  change in Settings did nothing until a restart. And because it ran after every turn, a native
  chat that ended a turn over the threshold was restarted before its own loop could compact it,
  throwing away the history the loop keeps.

Now the loop and the manager read the same value, as config.json holds it now. A runtime that
still compacts itself is left to do so; the manager restarts only a runtime that cannot, or one
whose own compaction has stopped helping.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config.loader import AppConfig
from personalclaw.context_compaction import total_chars
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.session import SessionManager


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home whose config.json the test writes, the way Settings writes it."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))

    def set_threshold(pct: float) -> None:
        (tmp_path / "config.json").write_text(
            json.dumps({"session": {"autocompact_pct": pct}}), encoding="utf-8"
        )

    return set_threshold


class _Model:
    """A model that reports the context usage the test gives it and records what it was sent."""

    supports_tools = False
    _model = "fake"

    def __init__(self, context_pct: float) -> None:
        self.context_pct = context_pct
        self.sent: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.sent.append([dict(m) for m in messages])
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield AgentEvent(
            kind=EVENT_COMPLETE,
            input_tokens=1,
            output_tokens=1,
            context_usage_pct=self.context_pct,
        )


def _runtime(model: _Model | None = None) -> NativeAgentRuntime:
    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="fake"),
        model_provider=model or _Model(0.0),
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


def _compacted(messages: list[dict]) -> bool:
    return any("[CONTEXT COMPACTION" in str(m.get("content", "")) for m in messages)


class TestTheNativeLoopCompactsAtTheSetting:
    def test_it_compacts_at_a_threshold_set_below_the_old_fixed_70(self, home):
        home(50)
        rt = _runtime()
        rt._messages = _convo()
        rt._last_context_pct = 60.0
        before = total_chars(rt._messages)
        rt._maybe_compact()
        assert total_chars(rt._messages) < before, "60% is over the 50% the user set"

    def test_it_waits_for_a_threshold_set_above_the_old_fixed_70(self, home):
        home(85)
        rt = _runtime()
        rt._messages = _convo()
        rt._last_context_pct = 80.0
        before = total_chars(rt._messages)
        rt._maybe_compact()
        assert total_chars(rt._messages) == before, "80% is under the 85% the user set"
        assert rt._compaction_saves == []

    def test_a_change_in_settings_applies_to_an_open_chat(self, home):
        home(85)
        rt = _runtime()
        rt._messages = _convo()
        rt._last_context_pct = 80.0
        rt._maybe_compact()
        assert not _compacted(rt._messages)
        home(75)
        rt._maybe_compact()
        assert _compacted(rt._messages), "the same chat compacts once the threshold is lowered"

    @pytest.mark.asyncio
    async def test_the_turn_sends_the_compacted_history(self, home):
        """Through the loop itself: the setting decides what the model is sent next."""
        home(50)
        model = _Model(60.0)
        rt = _runtime(model)
        await rt.start()
        rt._messages = _convo()
        [ev async for ev in rt.stream("turn one")]
        assert not _compacted(model.sent[0]), "nothing measured yet when turn one went out"
        [ev async for ev in rt.stream("turn two")]
        assert _compacted(model.sent[1]), "turn one measured 60%, over the 50% the user set"


def _manager() -> SessionManager:
    return SessionManager(AppConfig(), provider_factory=lambda *a, **k: AsyncMock())


class TestTheManagerLeavesASelfCompactingChatAlone:
    @pytest.mark.asyncio
    async def test_a_native_chat_over_the_threshold_is_compacted_not_restarted(self, home):
        """The order that lost history: the manager ran first, after the turn, and restarted
        the session at the same value the loop was about to compact at."""
        home(90)
        model = _Model(92.0)
        rt = _runtime(model)
        mgr = SessionManager(AppConfig(), provider_factory=lambda *a, **k: rt)
        key = "dashboard:chat-1"
        provider, _, _ = await mgr.get_or_create(key)
        assert provider is rt
        rt._messages = _convo()
        [ev async for ev in rt.stream("turn one")]
        mgr.release(key)

        assert mgr.check_context_usage(key, rt) == 92.0
        await asyncio.gather(*mgr._background_tasks, return_exceptions=True)
        assert mgr.has_session(key), "the chat's runtime was restarted instead of compacted"

        [ev async for ev in rt.stream("turn two")]
        assert _compacted(model.sent[-1]), "its own loop compacted it before the next call"
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_it_is_restarted_once_its_own_compaction_stops_helping(self, home):
        """The control, and the backstop kept: two passes that each reclaimed under a tenth
        mean compacting again will not help, so the restart is the way out."""
        home(90)
        rt = _runtime(_Model(0.0))
        mgr = SessionManager(AppConfig(), provider_factory=lambda *a, **k: rt)
        key = "dashboard:chat-2"
        await mgr.get_or_create(key)
        mgr.release(key)
        rt._compaction_saves = [0.02, 0.03]
        rt._last_context_pct = 95.0
        assert rt.compacts_automatically is False

        mgr.check_context_usage(key, rt)
        await asyncio.gather(*mgr._background_tasks, return_exceptions=True)
        assert not mgr.has_session(key)
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_an_agent_that_cannot_compact_itself_restarts_at_the_value_settings_holds(
        self, home
    ):
        """The manager read the threshold the gateway started with. Settings wrote a lower one
        and the chat carried on past it until the next restart."""
        mgr = _manager()  # built while the default, 90, was in effect
        key = "dashboard:chat-3"
        provider, _, _ = await mgr.get_or_create(key)
        mgr.release(key)
        provider.compacts_automatically = False
        provider.context_usage_pct = lambda: 70.0
        home(60)

        mgr.check_context_usage(key, provider)
        await asyncio.gather(*mgr._background_tasks, return_exceptions=True)
        assert not mgr.has_session(key), "70% is over the 60% Settings now holds"
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_under_the_threshold_nothing_is_restarted(self, home):
        home(90)
        mgr = _manager()
        key = "dashboard:chat-4"
        provider, _, _ = await mgr.get_or_create(key)
        mgr.release(key)
        provider.compacts_automatically = False
        provider.context_usage_pct = lambda: 89.0

        mgr.check_context_usage(key, provider)
        await asyncio.gather(*mgr._background_tasks, return_exceptions=True)
        assert mgr.has_session(key)
        await mgr.close_all()
