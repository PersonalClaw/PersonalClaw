"""An Attended loop's model spend is its owner's, like a chat's; an Unattended loop's counts.

Measured: an Attended Code loop's planner spent the owner's whole daily cap for unattended work
in eight minutes, and its next call was refused by it — while the owner was answering its asks.
The cap binds unattended work (Settings → Guardrails), and a session its owner answers is not
that. The model a loop runs on is still the one bound to Loops in Settings → Models, whatever its
Mode, and it is still behind the model-call guard (the breaker, the clock, the attempt audit and
the outbound scan): only whether the cap counts it follows the Mode (``loop.posture``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_automation_spend_is_metered import (  # noqa: F401 - calls is a fixture
    HEAD,
    HEAD_REF,
    NAMED_REF,
    _daily_dollar_cap,
    _price,
    _turn,
    calls,
)

from personalclaw.loop import posture


@pytest.fixture
def loops_bound(calls, monkeypatch):  # noqa: F811 - the imported fixture, extended
    """Loops bound to its own model in Settings → Models, apart from Chat's."""
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [NAMED_REF], "loops": [HEAD_REF]},
    )
    return calls


async def _loop_session_runtime(*, unmetered: bool):
    """The runtime a loop session's turn is given, built as ``SessionManager`` builds it."""
    from personalclaw.providers.provider_bridge import create_provider_factory

    runtime = create_provider_factory()("loop-0a1b2c3d", model_axis="loops", unmetered=unmetered)
    await runtime.start()
    return runtime


@pytest.mark.asyncio
async def test_an_attended_loops_turn_runs_on_the_loops_model_and_the_cap_does_not_count_it(
    loops_bound,
):
    from personalclaw.guardrails.budgets import get_meter

    runtime = await _loop_session_runtime(unmetered=True)
    await _turn(runtime)

    assert loops_bound == [HEAD], "the Loops binding stopped governing an Attended loop"
    assert get_meter().day_totals().dollars == 0.0


@pytest.mark.asyncio
async def test_an_unattended_loops_turn_counts_against_the_cap(loops_bound):
    from personalclaw.guardrails.budgets import get_meter

    runtime = await _loop_session_runtime(unmetered=False)
    await _turn(runtime)

    assert loops_bound == [HEAD]
    assert get_meter().day_totals().dollars == pytest.approx(_price("gpt-4o-mini"))


@pytest.mark.asyncio
async def test_a_spent_cap_stops_an_unattended_loop_and_not_an_attended_one(loops_bound):
    """The ceiling as the owner sets it: once the day is spent, an Unattended loop's next call is
    refused, and an Attended loop's goes on, as a chat's does."""
    from personalclaw.guardrails.failure import BudgetExceededError

    _daily_dollar_cap(0.0001)
    unattended = await _loop_session_runtime(unmetered=False)
    await _turn(unattended)  # the day's first call, which nothing could price yet: charged
    with pytest.raises(BudgetExceededError):
        await _turn(unattended)

    attended = await _loop_session_runtime(unmetered=True)
    await _turn(attended)
    await _turn(attended)
    assert loops_bound == [HEAD, HEAD, HEAD]


@pytest.mark.asyncio
async def test_an_attended_loops_model_is_still_behind_the_guard(loops_bound):
    """Not counting its spend is all the Mode changes: an Attended loop's model calls keep the
    breaker, the clock, the outbound scan and the attempt audit every automated call has."""
    from personalclaw.guardrails import audit
    from personalclaw.guardrails.model_call import ModelCallGuard

    runtime = await _loop_session_runtime(unmetered=True)
    assert isinstance(runtime._model, ModelCallGuard), type(runtime._model)
    before = len(audit.read_recent())
    await _turn(runtime)

    (row,) = audit.read_recent()[before:]
    assert (row["use_case"], row["provider"], row["passed"]) == ("loops", HEAD, True), row


def test_an_agent_cli_on_an_attended_loop_is_not_metered_either():
    from personalclaw.session import _meter_agent_turns

    told: list[str] = []
    cli = SimpleNamespace(set_spend_axis=told.append)

    _meter_agent_turns(cli, "loops", unmetered=True)
    _meter_agent_turns(cli, "loops")

    assert told == ["", "loops"]


@pytest.mark.parametrize(
    ("loop_posture", "unmetered"),
    [(posture.ATTENDED, True), (posture.UNATTENDED, False), (posture.UNREADABLE, False)],
    ids=["attended", "unattended", "a mode nobody can read"],
)
@pytest.mark.asyncio
async def test_a_loop_sessions_turn_asks_for_the_runtime_its_mode_names(
    tmp_path, loop_posture, unmetered
):
    from unittest.mock import patch

    from test_an_attended_loop_asks_before_it_acts import _chat_state

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import _ChatSession

    state, _client = _chat_state(tmp_path)
    worker = _ChatSession("loop-0a1b2c3d")
    worker._app = "loop"
    posture.arm(worker, loop_posture)
    _client.stream = MagicMock(side_effect=lambda *a, **kw: _no_events())

    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, worker, "run the next cycle")

    asked = state.sessions.get_or_create.call_args.kwargs
    assert (asked["model_axis"], asked["unmetered"]) == ("loops", unmetered)
    assert asked["unattended"] is (not loop_posture.asks)


async def _no_events():
    from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

    yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


@pytest.mark.asyncio
async def test_a_cached_runtime_is_rebuilt_when_its_loops_mode_changes():
    """A loop's Mode can change between its runs, and a runtime keeps who answers it and whose
    spend it is from when it was built: the next turn after a change gets a runtime built for the
    new Mode, not the old one."""
    from personalclaw.config import AppConfig
    from personalclaw.session import SessionManager

    built: list[dict] = []

    def _factory(session_key=None, agent=None, channel_id=None, **kwargs):
        built.append(kwargs)
        rt = AsyncMock()
        rt.start = AsyncMock()
        rt.shutdown = AsyncMock()
        rt.context_usage_pct = lambda: 0.0
        rt.compacts_automatically = False
        rt.resolved_from = None
        return rt

    manager = SessionManager(AppConfig(), provider_factory=_factory)

    async def _acquire(**posture_kwargs):
        await manager.get_or_create("dashboard:loop-0a1b2c3d", model_axis="loops", **posture_kwargs)
        manager.release("dashboard:loop-0a1b2c3d")

    await _acquire(unattended=False, unmetered=True)
    await _acquire(unattended=False, unmetered=True)
    assert len(built) == 1, "the same Mode rebuilt the runtime"
    await _acquire(unattended=True, unmetered=False)
    assert len(built) == 2, "a runtime built for an Attended loop served it Unattended"
    assert (built[-1]["unattended"], built[-1]["unmetered"]) == (True, False)
    await manager.close_all()
