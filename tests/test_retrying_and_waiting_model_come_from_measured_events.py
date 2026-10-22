"""A chat says ``retrying`` and ``waiting_model`` from what the gateway measured (#3833).

A retry and a wait on a busy local model used to happen where the owner could not see them: the
native loop's retry of a failed inference, its move to the next model of the chain, an agent CLI
sent again after its connection was lost, and a call held behind other calls on a local model.
Each is now the turn's phase, said from the event that measured it and never from a timer: the
retry pending with its attempt and the provider's reason, back to thinking when the retry's call
is made; and the wait with its model and how many calls are ahead, back to thinking when the model
takes the call. The payload carries ``retry`` and ``queue`` only in those phases.

Runs through the real chat runner and the real local queue; only the runtimes are fakes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from test_work_phase_classifier import _answer, _home, _World  # noqa: F401 — the home fixture

from personalclaw.dashboard import chat_phase
from personalclaw.guardrails import local_queue
from personalclaw.guardrails.local_queue import Attended, attending
from personalclaw.llm.events import EVENT_RETRYING, AgentEvent

#: A provider failure, as the native loop retries after it.
_TIMEOUT = TimeoutError("the provider took too long")


def _retry_pending(attempt: int = 2, of: int = 2, next_in: float = 0.5) -> AgentEvent:
    return AgentEvent(
        kind=EVENT_RETRYING,
        tool_meta={"attempt": attempt, "of": of, "next_in": next_in, "error": _TIMEOUT},
    )


def _retry_started() -> AgentEvent:
    return AgentEvent(kind=EVENT_RETRYING, tool_meta={"attempt": 2, "of": 2, "started": True})


def _phases(w: _World) -> list[str]:
    return [f["phase"] for f in w.phases()]


@pytest.mark.asyncio
async def test_a_failed_inference_says_retrying_then_thinking(tmp_path):
    """thinking → retrying → thinking, with the attempt, when it is made and why, then the reply."""

    async def stream() -> AsyncIterator[AgentEvent]:
        yield _retry_pending()
        yield _retry_started()
        for event in _answer():
            yield event

    w = _World(tmp_path, stream)
    await w.turn()
    said = _phases(w)
    assert said.index("thinking") < said.index("retrying") < len(said) - 1
    after = said[said.index("retrying") + 1 :]
    assert after[0] == "thinking", said
    retry = next(f for f in w.phases() if f["phase"] == "retrying")
    assert retry["line"] == "Retrying (2 of 2)"
    assert retry["retry"]["attempt"] == 2 and retry["retry"]["of"] == 2
    assert retry["retry"]["next_at"], "when the retry is made"
    assert retry["retry"]["reason"], "the provider's failure, in its sentence"
    assert "Traceback" not in retry["retry"]["reason"]
    # Only the retrying frame carries it.
    assert all(f["retry"] is None for f in w.phases() if f["phase"] != "retrying")


@pytest.mark.asyncio
async def test_a_fail_over_names_the_next_model_and_its_answer_ends_the_retry(tmp_path):
    from personalclaw.llm.events import EVENT_MODEL_SUBSTITUTION

    async def stream() -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            kind=EVENT_RETRYING, tool_meta={"attempt": 2, "of": 3, "model": "qwen3:8b"}
        )
        yield AgentEvent(kind=EVENT_MODEL_SUBSTITUTION, text="Ran on qwen3:8b instead.")
        for event in _answer():
            yield event

    w = _World(tmp_path, stream)
    await w.turn()
    retry = next(f for f in w.phases() if f["phase"] == "retrying")
    assert retry["line"] == "Trying qwen3:8b"
    assert retry["retry"]["attempt"] == 2 and retry["retry"]["of"] == 3
    assert retry["retry"]["next_at"] is None, "a fail-over asks the next model at once"
    said = _phases(w)
    assert said[said.index("retrying") + 1] == "thinking", said


@pytest.mark.asyncio
async def test_a_retry_is_said_only_from_its_event(tmp_path):
    """No event, no retrying: a turn that does not retry never says it, whatever its timing."""

    async def stream() -> AsyncIterator[AgentEvent]:
        await asyncio.sleep(0.05)
        for event in _answer():
            yield event

    w = _World(tmp_path, stream)
    await w.turn()
    assert "retrying" not in _phases(w)


# The native loop's own retry and fail-over send these events as they happen: a real run of the
# loop, a model that fails and then the next one that answers, is in
# `test_a_turn_fails_over_and_names_the_fix.py`.


# ── A wait on a busy local model ─────────────────────────────────────────────────────────────


def _key(model: str = "qwen3:8b") -> str:
    """A queue of the test's own: one local model's line, keyed as the queue keys its lines."""
    return f"test-local:{model}"


@pytest.mark.asyncio
async def test_a_call_held_behind_others_says_waiting_model_then_thinking(tmp_path):
    """A call that waits behind another on a local model says so, with how many are ahead, and is
    thinking again once the model takes it: both from the queue's own change."""
    w = _World(tmp_path, lambda: _hold())
    held = asyncio.Event()
    go = asyncio.Event()

    async def _hold() -> AsyncIterator[AgentEvent]:
        held.set()
        await go.wait()
        for event in _answer():
            yield event

    task = w.start()
    await held.wait()
    key = _key()
    holder = await local_queue.take_turn(key, provider="ollama", model="qwen3:8b", within=None)

    async def waiter() -> local_queue.Turn:
        with attending(Attended("your reply", session=f"dashboard:{w.session.key}")):
            return await local_queue.take_turn(
                key, provider="ollama", model="qwen3:8b", within=None
            )

    wait = asyncio.ensure_future(waiter())
    await asyncio.sleep(0.01)
    rows = local_queue.waits()
    assert rows and rows[0]["ahead"] == 1, rows
    chat_phase.model_waits_changed(w.state, rows)
    waiting = w.phases()[-1]
    assert (waiting["phase"], waiting["line"]) == (
        "waiting_model",
        "Waiting for qwen3:8b · 1 ahead",
    )
    assert waiting["queue"] == {"model": "ollama:qwen3:8b", "behind": 1}
    assert waiting["retry"] is None

    holder.release()
    turn = await asyncio.wait_for(wait, 2)
    chat_phase.model_waits_changed(w.state, local_queue.waits())
    after = w.phases()[-1]
    assert after["phase"] == "thinking" and after["queue"] is None
    turn.release()
    go.set()
    await asyncio.wait_for(task, 10)


def test_waits_count_the_calls_ahead_in_line():
    """The holders and every attended wait before it; a background call waits behind it."""

    async def scenario() -> list[dict[str, Any]]:
        key = _key("llama3:8b")
        held = await local_queue.take_turn(key, provider="ollama", model="llama3:8b", within=None)
        waits = []
        for n in range(2):

            async def one(n: int = n) -> local_queue.Turn:
                with attending(Attended("your reply", session=f"dashboard:c{n}")):
                    return await local_queue.take_turn(
                        key, provider="ollama", model="llama3:8b", within=None
                    )

            waits.append(asyncio.ensure_future(one()))
            await asyncio.sleep(0.01)
        rows = local_queue.waits()
        held.release()
        for t in waits:
            (await asyncio.wait_for(t, 2)).release()
        return rows

    rows = asyncio.run(scenario())
    assert sorted(r["ahead"] for r in rows) == [1, 2]
