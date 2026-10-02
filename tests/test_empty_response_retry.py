"""How the chat runner reads a completed turn that wrote nothing (`unanswered_turn`)."""

from personalclaw.acp.types import STOP_REASON_CANCELLED, STOP_REASON_END_TURN
from personalclaw.dashboard.turn_endings import (
    UNANSWERED_AFTER_STEPS,
    UNANSWERED_BLANK,
    no_answer_notice,
    unanswered_turn,
)


def _call(**over):
    base = dict(
        wrote_text=False,
        stop_reason=STOP_REASON_END_TURN,
        saw_compaction=False,
        needs_session_reset=False,
        is_slash=False,
        tool_call_count=0,
        is_loop=False,
    )
    base.update(over)
    return unanswered_turn(**base)


def test_blank_turn_with_no_tools_is_blank():
    assert _call() == UNANSWERED_BLANK


def test_text_turn_is_answered():
    assert _call(wrote_text=True) == ""


def test_a_turn_that_ran_steps_and_wrote_nothing_has_no_answer():
    """Fifteen commands and no reply is not a finished turn: it is never resent, and it is said."""
    assert _call(tool_call_count=15) == UNANSWERED_AFTER_STEPS


def test_a_turn_that_answered_before_its_last_step_is_answered():
    """An answer followed by one closing call (a memory save, say) is still an answer."""
    assert _call(wrote_text=True, tool_call_count=3) == ""


def test_cancelled_turn_is_not_unanswered():
    assert _call(stop_reason=STOP_REASON_CANCELLED) == ""
    assert _call(stop_reason=STOP_REASON_CANCELLED, tool_call_count=4) == ""


def test_compaction_turn_is_not_unanswered():
    assert _call(saw_compaction=True) == ""


def test_agent_switch_turn_is_not_unanswered():
    """Agent switch / clear set needs_session_reset and append their own line."""
    assert _call(needs_session_reset=True) == ""


def test_slash_command_turn_is_not_unanswered():
    assert _call(is_slash=True) == ""


def test_loop_turn_stands_aside_even_when_blank_or_after_steps():
    """Goal loops own a dedicated re-prompt loop — the chat's handling stands aside."""
    assert _call(is_loop=True) == ""
    assert _call(is_loop=True, tool_call_count=6) == ""


def test_the_notice_counts_the_steps_and_says_how_to_retry_in_words():
    assert no_answer_notice(15, asked_by_person=True) == (
        "The agent ran 15 steps but did not write an answer. Send your message again to retry."
    )
    assert no_answer_notice(1, asked_by_person=True).startswith("The agent ran 1 step but")
    assert no_answer_notice(0, asked_by_person=True) == (
        "The agent did not write an answer. Send your message again to retry."
    )


def test_a_turn_nobody_typed_is_not_told_to_send_a_message_again():
    # An automation, a subagent's report or an auto-nudge started it: there is no message of
    # the person's to send, and the chat's Retry runs the turn again.
    assert no_answer_notice(3, asked_by_person=False) == (
        "The agent ran 3 steps but did not write an answer. Retry it from the chat."
    )
    assert no_answer_notice(0, asked_by_person=False) == (
        "The agent did not write an answer. Retry it from the chat."
    )
