"""A running turn says its phase again on the gateway's clock, so silence never reads as work.

A model can think for minutes without a word, and a turn whose phase was said once could not be
told from a turn that died without saying so. So while a turn runs, the gateway sends its chat's
``work_phase`` again every fifteen seconds (``chat_phase.RESEND_SECS``) — the same phase, with the
time of its latest step — as a frame that carries data, never a keep-alive comment. A surface
that hears nothing for longer than that knows the turn's state is unconfirmed and reads the chat
again. The timer is one per running turn, and it stops where the turn's last frame is sent.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from test_work_phase_classifier import _answer, _World

from personalclaw.dashboard import chat_phase
from personalclaw.instants import utc_iso
from personalclaw.llm.events import EVENT_THINKING_CHUNK, AgentEvent

#: Where the test's clock starts: an instant, in seconds.
T0 = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)


@pytest.mark.asyncio
async def test_a_silent_models_turn_is_said_again_with_its_latest_step(tmp_path, monkeypatch):
    clock = {"now": T0}
    ticks: asyncio.Queue[None] = asyncio.Queue()
    asked: list[float] = []

    async def sleep(secs: float) -> None:
        asked.append(secs)
        await ticks.get()
        clock["now"] += secs

    monkeypatch.setattr(chat_phase, "_now", lambda: clock["now"])
    monkeypatch.setattr(chat_phase, "_sleep", sleep)
    gate = asyncio.Event()

    async def stream() -> AsyncIterator[AgentEvent]:
        yield AgentEvent(kind=EVENT_THINKING_CHUNK, text="Let me think")
        await gate.wait()  # forty seconds without a word
        for event in _answer():
            yield event

    w = _World(tmp_path, stream)
    task = w.start()
    for _ in range(500):
        if w.count("chat_thinking"):
            break
        await asyncio.sleep(0.01)
    said = len(w.phases())

    for _tick in range(2):
        ticks.put_nowait(None)
        for _ in range(500):
            if len(w.phases()) > said:
                break
            await asyncio.sleep(0.01)
        said = len(w.phases())
    clock["now"] += 10  # forty seconds since the step, two re-sends in

    again = w.phases()[-2:]
    assert [f["phase"] for f in again] == ["thinking", "thinking"]
    assert {f["last_step_at"] for f in again} == {utc_iso(T0)}
    assert again[0]["seq"] < again[1]["seq"]
    assert again[0]["turn"] == again[1]["turn"] is not None
    assert asked[:2] == [chat_phase.RESEND_SECS, chat_phase.RESEND_SECS] == [15.0, 15.0]

    gate.set()
    await asyncio.wait_for(task, timeout=10)
    assert w.phases()[-1]["turn"] is None
    # The timer stopped with the turn: another tick says nothing more.
    ended = len(w.phases())
    ticks.put_nowait(None)
    await asyncio.sleep(0.05)
    assert len(w.phases()) == ended
    assert "heartbeat" not in {kind for kind, _ in w.frames}


@pytest.mark.asyncio
async def test_a_turn_that_moves_on_is_said_once_per_change_not_per_tick(tmp_path, monkeypatch):
    """The clock only re-says the phase; a change is said when it happens."""
    monkeypatch.setattr(chat_phase, "_sleep", lambda _secs: asyncio.Event().wait())
    w = _World(tmp_path, lambda: _streamed(_answer(words=3)))
    await w.turn()
    assert [f["phase"] for f in w.phases()] == ["queued", "thinking", "streaming", "done"]


async def _streamed(events: list[AgentEvent]) -> AsyncIterator[AgentEvent]:
    for event in events:
        yield event
