"""The running list says it moved in one frame per window, never per chunk or liveness re-send.

``GET /api/running`` is read again when the list moves (#3889). A reader learns that it moved from
one ``running_changed`` frame, sent at most once per window (``running_now.WINDOW_SECS``, 150 ms):
a doorbell that names the work that moved, ``{"changed": [{"work", "session"}, ...]}``, and
carries nothing to render. It rings when a row is added or removed or its phase or line changes,
and when what waits on the owner for a row does; a streamed chunk, a turn's liveness re-send of an
unchanged phase and a command's output ring nothing.

Everything runs through the real producers: a chat's real turn, the gateway's subagent event
handler, the real approval registry, the trigger claim store and the webhook door's real turn.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_helpers_loops_and_runs_say_their_phases import _helper
from test_work_phase_classifier import CHAT, _answer, _gateway_events, _World

from personalclaw.dashboard import chat_phase
from personalclaw.llm.events import EVENT_TEXT_CHUNK, EVENT_THINKING_CHUNK, AgentEvent

#: A window short enough for a test, the same rule as the gateway's.
WINDOW = 0.05


@pytest.fixture(autouse=True)
def _window(monkeypatch):
    from personalclaw import running_now

    monkeypatch.setattr(running_now, "WINDOW_SECS", WINDOW)


def _rings(w: _World) -> list[dict]:
    return [d for k, d in w.frames if k == "running_changed"]


def _moved(w: _World) -> list[str]:
    return [c["work"] for ring in _rings(w) for c in ring["changed"]]


async def _a_window() -> None:
    await asyncio.sleep(WINDOW * 3)


async def _streamed(*events: AgentEvent) -> AsyncIterator[AgentEvent]:
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_a_burst_of_phase_changes_rings_once_per_window(tmp_path):
    w = _World(tmp_path, lambda: _streamed())
    on_event, manager = _gateway_events(w.state)
    helper = _helper("h1")
    manager.running = [helper]
    await on_event("subagent_spawn", helper, {"task": helper.task, "agent": ""})
    for n in range(10):
        await on_event("subagent_tool", helper, {"tool": f"step {n}", "tool_kind": ""})
    assert _rings(w) == [], "nothing rings before its window closes"
    await _a_window()
    assert len(_rings(w)) == 1, "ten changes, one frame"
    assert _rings(w)[0] == {"changed": [{"work": "subagent:h1", "session": CHAT}]}

    await on_event("subagent_tool", helper, {"tool": "one more", "tool_kind": ""})
    await _a_window()
    assert len(_rings(w)) == 2, "a change after the window rings again"


@pytest.mark.asyncio
async def test_a_streaming_chats_chunks_ring_only_its_phase_changes(tmp_path):
    """Each chunk lands in a window of its own, so a chunk that rang would be its own frame: the
    rings are no more than the turn's phase changes."""

    async def stream() -> AsyncIterator[AgentEvent]:
        for n in range(6):
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text=f"w{n} ")
            await _a_window()

    w = _World(tmp_path, stream)
    await w.turn()
    await _a_window()
    phases = len(w.phases())
    assert w.count("chat_chunk") >= 6
    assert 1 <= len(_rings(w)) <= phases < 6, "chunks ring nothing of their own"
    assert set(_moved(w)) == {f"chat:{CHAT}"}


@pytest.mark.asyncio
async def test_a_loop_workers_turn_rings_only_through_its_loop(tmp_path):
    """A loop's worker is its loop's: its own turn's frames never ring as a chat's row."""
    w = _World(tmp_path, lambda: _streamed())
    worker = w.state.get_or_create_session("loop-0a1b2c3d")
    chat_phase.begin(w.state, worker)
    chat_phase.end(w.state, worker)
    await _a_window()
    assert "chat:loop-0a1b2c3d" not in _moved(w)


@pytest.mark.asyncio
async def test_a_change_off_the_event_loop_is_handed_to_it(tmp_path):
    from personalclaw import running_now

    w = _World(tmp_path, lambda: _streamed())
    w.state._ws_loop = asyncio.get_running_loop()
    await asyncio.to_thread(running_now.changed, w.state, "trigger:t-9")
    await _a_window()
    assert _moved(w) == ["trigger:t-9"]


@pytest.mark.asyncio
async def test_a_question_asked_in_a_runs_step_rings_for_its_run(tmp_path):
    """What waits on her for a row is part of it: a question a run's step asks moves the run's row
    as it is asked and answered, though no phase frame of the run says it."""
    from personalclaw.owner_questions import normalize
    from personalclaw.workflows import store as runs
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    w = _World(tmp_path, lambda: _streamed())
    run = runs.create(WorkflowRun(id="", workflow_name="nightly", status=RunStatus.RUNNING))
    step = f"workflow:{run.id}:fetch"
    questions = normalize({"questions": [{"question": "Which day?", "options": [{"label": "a"}]}]})
    asked = asyncio.ensure_future(w.state.owner_questions.ask(step, questions, asked_by="A step"))
    for _ in range(200):
        if w.state.owner_questions.pending_for(step):
            break
        await asyncio.sleep(0.01)
    await _a_window()
    (card,) = w.state.owner_questions.pending_for(step)
    w.state.owner_questions.answer(step, card["id"], answers=[{"selected": [0]}])
    await asyncio.wait_for(asked, timeout=5)
    await _a_window()
    assert _moved(w).count(f"workflow:{run.id}") == 2, _moved(w)


@pytest.mark.asyncio
async def test_liveness_resends_ring_nothing_and_a_phase_change_rings_once(tmp_path, monkeypatch):
    """A silent model for sixty seconds: the turn's phase is said again four times on the
    gateway's clock (``chat_phase.RESEND_SECS``), each the same phase, and the list does not move.
    The answer's first words then change the phase, and the list rings once."""
    ticks: asyncio.Queue[None] = asyncio.Queue()

    async def sleep(_secs: float) -> None:
        await ticks.get()

    monkeypatch.setattr(chat_phase, "_sleep", sleep)
    gate = asyncio.Event()

    async def stream() -> AsyncIterator[AgentEvent]:
        yield AgentEvent(kind=EVENT_THINKING_CHUNK, text="Let me think")
        await gate.wait()
        for event in _answer():
            yield event

    w = _World(tmp_path, stream)
    task = w.start()
    await w.until("thinking")
    await _a_window()
    rung, said = len(_rings(w)), len(w.phases())

    for _tick in range(4):
        ticks.put_nowait(None)
        for _ in range(500):
            if len(w.phases()) > said:
                break
            await asyncio.sleep(0.01)
        said = len(w.phases())
    await _a_window()
    assert len(w.phases()) >= said and [f["phase"] for f in w.phases()[-4:]] == ["thinking"] * 4
    assert len(_rings(w)) == rung, "four liveness re-sends, and the list never moved"

    gate.set()
    await asyncio.wait_for(task, timeout=10)
    await _a_window()
    assert len(_rings(w)) > rung, "its answer moved it"


@pytest.mark.asyncio
async def test_an_approval_for_a_runs_step_rings_for_its_run_as_it_is_asked_and_answered(tmp_path):
    """What waits on the owner for a row is part of the row: a step's approval moves its run's row,
    though the run's own phase frame is not sent for it, when it is asked and when it is
    answered."""
    from personalclaw.approval_answer import YOU
    from personalclaw.workflows import store as runs
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    w = _World(tmp_path, lambda: _streamed())
    run = runs.create(WorkflowRun(id="", workflow_name="nightly", status=RunStatus.RUNNING))
    asked = asyncio.ensure_future(
        w.state.request_approval("a-run", "workflow", "shell", session=f"workflow:{run.id}:fetch")
    )
    for _ in range(200):
        if "a-run" in w.state._pending_approvals:
            break
        await asyncio.sleep(0.01)
    await _a_window()
    assert w.state.resolve_approval("a-run", True, by=YOU) is True
    await asyncio.wait_for(asked, timeout=5)
    await _a_window()
    assert _moved(w) == [f"workflow:{run.id}", f"workflow:{run.id}"]
    assert {c["session"] for ring in _rings(w) for c in ring["changed"]} == {""}


@pytest.mark.asyncio
async def test_a_triggers_fire_starting_and_ending_rings_for_it(tmp_path):
    from personalclaw.triggers import claims
    from personalclaw.triggers.scheduling import Claim

    w = _World(tmp_path, lambda: _streamed())
    with patch(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", return_value=w.state
    ):
        claims.write_claim(Claim(trigger_id="t-1", holder="cron:1", claimed_at=time.time()))
        await _a_window()
        claims.release_claim("t-1")
        await _a_window()
    assert _moved(w) == ["trigger:t-1", "trigger:t-1"]


@pytest.mark.asyncio
async def test_a_webhooks_turn_rings_as_it_starts_and_ends(tmp_path):
    from personalclaw.dashboard.handlers import hooks

    w = _World(tmp_path, lambda: _streamed())
    w.state.sessions.release = MagicMock()
    w.state.sessions.reset = AsyncMock()

    async def inner(*_a: Any, **_kw: Any) -> str:
        await _a_window()
        return "done"

    await hooks._hook_semaphore.acquire()
    with patch.object(hooks, "_run_hook_inner", inner), patch.object(hooks, "_sel", MagicMock()):
        await hooks._run_hook_agent(w.state, "hook:deploy", "go", "Deploy hook", "", False, 30)
    await _a_window()
    assert _moved(w) == ["webhook:deploy", "webhook:deploy"]
