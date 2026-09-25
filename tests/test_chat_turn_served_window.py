"""The chat turn resolves ONE served window and every consumer of the turn reads it.

Driven through the real ``run_chat`` with a mocked client, because the contract is what a
finished turn does: the window is resolved once, the assembler and the budget check receive
that same object, a reply cut at the output cap is marked on the persisted message, and the
skills record describes what was actually sent after the budget check compressed it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from chat_test_helpers import _make_state

from personalclaw.context_engine import AssembledContext
from personalclaw.context_headroom import (
    Compressed,
    Headroom,
    HeadroomState,
    Window,
)
from personalclaw.dashboard import chat_runner
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.skills.allocation import SkillLoadState

_WINDOW = Window(
    tokens=4096,
    output_reserve_tokens=320,
    input_tokens=3776,
    source="served",
    ref="bundled-chat:SmolLM2-135M-Instruct-Q8_0",
)


def _client(*, stop_reason: str = "end_turn") -> AsyncMock:
    client = AsyncMock()
    client.context_usage_pct = MagicMock(return_value=10.0)

    async def _stream(_msg):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="A reply that stops in the")
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason=stop_reason, output_tokens=320)

    client.stream = _stream
    client.stream_command = _stream
    return client


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """``run(client, metadata, verdict) -> (session, seen)`` — one real turn."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.context_builder = MagicMock()
    state.consolidator = None
    state._hook_store = None
    import personalclaw.trust_mode as _tm

    _tm.disable_yolo()
    session = state.get_or_create_session("s-window")
    seen: dict = {"resolved": []}

    async def _resolve(model_ref="", *, serving=None):  # noqa: ANN001, ANN202
        seen["resolved"].append((model_ref, serving))
        return _WINDOW

    monkeypatch.setattr(chat_runner, "resolve_window", _resolve)

    async def run(client, metadata: dict | None = None, verdict: Headroom | None = None):
        state.sessions.get_or_create = AsyncMock(return_value=(client, True, False))

        def _assemble(*_a, **kw):  # noqa: ANN002, ANN003, ANN202
            seen["assembled_window"] = kw.get("window")
            return AssembledContext(message="hello", metadata=dict(metadata or {}))

        def _check(_assembled, **kw):  # noqa: ANN001, ANN003, ANN202
            seen["checked_window"] = kw.get("window")
            return verdict or Headroom(
                state=HeadroomState.FITS,
                window=kw["window"],
                assembled_tokens=1,
                raw_tokens=1,
                text="hello",
            )

        monkeypatch.setattr(chat_runner, "assemble_context", _assemble)
        monkeypatch.setattr(chat_runner, "check_headroom", _check)
        await chat_runner.run_chat(state, session, "hello")
        return session, seen

    return run


def _last_assistant_meta(session) -> dict:
    msgs = [m for m in session.messages if m.get("role") == "assistant"]
    assert msgs, "the turn produced no assistant message"
    meta = msgs[-1].get("meta")
    return meta if isinstance(meta, dict) else {}


@pytest.mark.asyncio
async def test_the_turn_resolves_one_window_and_hands_it_to_the_assembler_and_the_check(harness):
    client = _client()
    _session, seen = await harness(client)
    assert len(seen["resolved"]) == 1, seen["resolved"]
    assert seen["resolved"][0][1] is client, "the window was not asked of the client serving it"
    assert seen["assembled_window"] is _WINDOW
    assert seen["checked_window"] is _WINDOW


@pytest.mark.asyncio
async def test_a_reply_cut_at_the_length_limit_is_marked_on_the_persisted_message(harness):
    """A 320-token cap ended replies mid-sentence with nothing to say it had happened."""
    session, _seen = await harness(_client(stop_reason="max_tokens"))
    assert _last_assistant_meta(session).get("finish_reason") == "length"


@pytest.mark.asyncio
async def test_a_reply_that_finished_carries_no_cut_mark(harness):
    session, _seen = await harness(_client(stop_reason="end_turn"))
    assert "finish_reason" not in _last_assistant_meta(session)


@pytest.mark.asyncio
async def test_a_skill_the_budget_check_compressed_is_recorded_as_what_was_sent(harness):
    """The record said a skill loaded 4,200 tokens when the prompt that went out carried 900."""
    decisions = [
        {
            "name": "git-review",
            "state": SkillLoadState.ADMITTED.value,
            "tier": "standard",
            "cap_tokens": 4000,
            "body_tokens": 4200,
            "loaded_tokens": 4200,
            "reason": "",
            "forced": False,
        }
    ]
    verdict = Headroom(
        state=HeadroomState.FITS_AFTER_COMPRESSION,
        window=_WINDOW,
        assembled_tokens=3000,
        raw_tokens=6300,
        text="hello (compressed)",
        compressed=(Compressed("skill: git-review", 4200, 900, ""),),
    )
    session, _seen = await harness(_client(), {"skill_decisions": decisions}, verdict)
    used = _last_assistant_meta(session).get("skills_used")
    assert used == [{"name": "git-review", "state": "reduced", "loaded_tokens": 900}], used
