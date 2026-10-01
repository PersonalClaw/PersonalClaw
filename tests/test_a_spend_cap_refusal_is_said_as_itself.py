"""A spend cap's refusal is a known outcome, said in its own words wherever a turn or a run ends.

Measured on a Code loop's planner on a paid model, past the daily dollar cap: the planner session's
error, the same line in the gateway log, read "The turn failed with an error PersonalClaw doesn't
recognize. Try again; if it keeps failing, check the gateway log. Details: day dollars budget has
no room for this call: spent 3.944 of 4, …". The cap is not a failure nobody recognizes, and the
gateway log is no place to fix it. Every surface now asks one question of the failure
(``failure.budget_refusal``) and shows one sentence for it (``BudgetExceededError.sentence``):
which cap, what was spent of how much, what the refused call needed, and how to lift it — raise or
remove the cap in Settings → Guardrails, or wait for it to reset.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.guardrails.failure import NO_ROOM, SPENT, BudgetExceededError, budget_refusal
from personalclaw.llm_helpers import humanize_provider_error

#: The refusal as it was measured: 3.944 of a $4.00 daily cap spent, eleven calls unpriced, and a
#: call to a paid model that may cost $0.299. The model is invented.
_SAID = (
    "The daily dollar budget has $3.94 of $4.00 spent (not counting 11 calls that had no price), "
    "and a call to cloud:example-large may cost $0.30, more than the $0.06 left: raise Max "
    "dollars / day in Settings → Guardrails (0 removes the cap), or wait for it to reset at "
    "midnight."
)


def _refusal(**over) -> BudgetExceededError:
    kw = dict(unpriced=11, why=NO_ROOM, needed=0.299, ref="cloud:example-large")
    kw.update(over)
    return BudgetExceededError("day", "dollars", 4.0, 3.944, **kw)


# ── one sentence, from one mapping ───────────────────────────────────────────────────────────


def test_the_refusal_says_which_cap_what_was_spent_what_the_call_needed_and_the_fix():
    assert _refusal().sentence() == _SAID


def test_a_spent_cap_says_so_and_names_its_control():
    spent = BudgetExceededError("day", "tokens", 50_000, 50_400, why=SPENT)
    assert spent.sentence() == (
        "The daily token budget is spent (50,400 of 50,000 tokens): raise Max tokens / day in "
        "Settings → Guardrails (0 removes the cap), or wait for it to reset at midnight."
    )
    assert spent.settings_page == "guardrails"


def test_the_chat_shows_the_cap_sentence_not_an_unrecognized_error():
    said = humanize_provider_error(_refusal())
    assert said == _SAID
    assert "doesn't recognize" not in said and "gateway log" not in said


def test_a_failure_raised_from_a_refusal_is_that_refusal():
    refusal = _refusal()
    try:
        raise RuntimeError("the model call did not run") from refusal
    except RuntimeError as wrapped:
        assert budget_refusal(wrapped) is refusal
        assert humanize_provider_error(wrapped) == _SAID


def test_an_ordinary_failure_is_no_refusal():
    # The control: only a spend ceiling's refusal is read as one.
    assert budget_refusal(RuntimeError("upstream said something specific")) is None
    assert budget_refusal(None) is None


# ── the chat turn ────────────────────────────────────────────────────────────────────────────


def _state_whose_turn_raises(tmp_path, exc: BaseException):
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.record_failure = AsyncMock()
    sessions.get_or_create = AsyncMock(side_effect=exc)
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    state.context_builder = MagicMock()
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    state.push_refresh = MagicMock()
    return state


@pytest.mark.asyncio
async def test_a_chat_turn_the_cap_refused_says_the_sentence_and_links_where_it_is_changed(
    tmp_path,
):
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import _ChatSession

    refusal = _refusal()
    state = _state_whose_turn_raises(tmp_path, refusal)
    session = _ChatSession("chat-1-test")
    session._titled = True
    with (
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await run_chat(state, session, "hi")

    (error,) = [m for m in session.messages if m.get("role") == "error"]
    assert error["content"] == _SAID
    assert error.get("meta") == {"settings": "guardrails"}, "the notice links the cap's page"
    # What retries a failed turn by itself reads this, and does not send the refused call again.
    assert session._last_turn_refusal is refusal
    assert session._last_turn_errored is True


@pytest.mark.asyncio
async def test_the_next_turn_starts_with_no_refusal(tmp_path):
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import _ChatSession

    state = _state_whose_turn_raises(tmp_path, RuntimeError("upstream said something specific"))
    session = _ChatSession("chat-1-test")
    session._titled = True
    session._last_turn_refusal = _refusal()
    with (
        patch("personalclaw.dashboard.chat_runner.maybe_offer_check_work"),
        patch("personalclaw.dashboard.chat_runner._maybe_followups", new=AsyncMock()),
        patch("personalclaw.dashboard.chat_plan.maybe_submit_plan_draft"),
    ):
        await run_chat(state, session, "hi")

    assert session._last_turn_refusal is None, "an ordinary failure read as the old refusal"
    assert session._last_turn_errored is True
    (error,) = [m for m in session.messages if m.get("role") == "error"]
    assert "meta" not in error


# ── a workflow step, and a subagent ──────────────────────────────────────────────────────────


def test_a_workflow_step_the_cap_refused_shows_the_same_two_halves():
    from personalclaw.workflows.failure_taxonomy import classify_exception
    from personalclaw.workflows.models import FailureClass

    refusal = _refusal()
    failure = classify_exception(refusal)
    assert failure.failure_class == FailureClass.BUDGET
    assert failure.cause_plain == refusal.headline()
    assert failure.cause_plain.startswith("The daily dollar budget has $3.94 of $4.00 spent")
    assert failure.remediation.startswith(refusal.fix())
    assert "BudgetExceededError" not in failure.cause_plain
    # Nothing in the workflow is wrong: the run page offers Retry once the cap has room, never a
    # change to the step.
    assert failure.remediation == f"{refusal.fix()}, then Retry"
    assert not failure.retryable


@pytest.mark.asyncio
async def test_a_best_of_n_step_the_cap_refused_says_the_refusal_as_itself(tmp_path, monkeypatch):
    import personalclaw.llm_helpers as llm_helpers
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.best_of_n_provider import BestOfNActionProvider
    from personalclaw.workflows.failure_taxonomy import classify_action_result
    from personalclaw.workflows.models import FailureClass

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    refusal = _refusal()

    async def _refused(*_a, **_kw):
        raise refusal

    monkeypatch.setattr(llm_helpers, "one_shot_completion", _refused)
    result = await BestOfNActionProvider().execute(
        {"prompt": "Name one primary color.", "n": 2}, ActionContext(event="workflow")
    )

    assert not result.success
    assert refusal.headline() in result.error
    assert "BudgetExceededError" not in result.error
    failure = classify_action_result(result)
    assert failure.failure_class == FailureClass.BUDGET
    assert failure.remediation == f"{refusal.fix()}, then Retry"


@pytest.mark.asyncio
async def test_a_subagent_the_cap_refused_reports_the_sentence(monkeypatch):
    from personalclaw.subagent import SubagentInfo, SubagentManager

    sessions = MagicMock()
    sessions.reset = AsyncMock()
    manager = SubagentManager(sessions=sessions, ctx_builder=MagicMock())
    refusal = _refusal()

    async def _refused(info, session_key):
        raise refusal

    monkeypatch.setattr(manager, "_run_inner", _refused)
    monkeypatch.setattr(manager, "_write_tombstone", lambda *_a, **_kw: None)
    monkeypatch.setattr(manager, "_fire_event", AsyncMock())
    info = SubagentInfo(id="a1b2c3d4", task="summarise the notes")

    await asyncio.wait_for(manager._run(info), timeout=5)

    assert info.done and info.error == _SAID


def test_a_trigger_action_the_cap_refused_reports_the_refusal_not_a_provider_fault():
    from personalclaw.action_providers.base import provider_failure
    from personalclaw.errors import ERROR_CODES

    refusal = _refusal()
    envelope = provider_failure("run-prompt", refusal)

    assert envelope.code == "ERR_SPEND_CAP_REFUSED" and envelope.code in ERROR_CODES
    assert envelope.why == refusal.reason() and envelope.fix == refusal.fix()
    rendered = envelope.render()
    assert "raised an exception" not in rendered and "check the action config" not in rendered
    # An ordinary raise keeps the generic envelope (the control).
    assert provider_failure("run-prompt", RuntimeError("boom")).code == "ERR_ACTION_PROVIDER_FAILED"
