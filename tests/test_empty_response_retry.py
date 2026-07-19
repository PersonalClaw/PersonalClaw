"""Tests for empty-response detection (the auto-retry predicate in chat_runner)."""

from personalclaw.acp.types import STOP_REASON_CANCELLED, STOP_REASON_END_TURN
from personalclaw.dashboard.chat_runner import is_empty_turn


def _call(**over):
    base = dict(
        assistant_text="",
        stop_reason=STOP_REASON_END_TURN,
        saw_compaction=False,
        needs_session_reset=False,
        is_slash=False,
        tool_call_count=0,
        is_loop=False,
    )
    base.update(over)
    return is_empty_turn(**base)


def test_blank_turn_with_no_tools_is_empty():
    assert _call() is True


def test_whitespace_only_is_empty():
    assert _call(assistant_text="   \n\t ") is True


def test_text_turn_is_not_empty():
    assert _call(assistant_text="here is the answer") is False


def test_tool_only_turn_is_not_empty():
    """A turn that ran tools but produced no closing prose did real work."""
    assert _call(assistant_text="", tool_call_count=3) is False


def test_cancelled_turn_is_not_empty():
    assert _call(stop_reason=STOP_REASON_CANCELLED) is False


def test_compaction_turn_is_not_empty():
    assert _call(saw_compaction=True) is False


def test_agent_switch_turn_is_not_empty():
    """Agent switch / clear set needs_session_reset and append their own line."""
    assert _call(needs_session_reset=True) is False


def test_slash_command_turn_is_not_empty():
    assert _call(is_slash=True) is False


def test_loop_turn_excluded_even_when_blank():
    """Goal loops own a dedicated re-prompt loop — the generic retry stands aside."""
    assert _call(is_loop=True) is False
