"""A truncated completion is reported as truncation, not as a missing argument.

Nothing in the LLM layer inspected a completion's stop reason for truncation. A response cut at
``max_tokens`` was accepted as a complete answer, and a tool call whose arguments were cut
mid-stream became ``{}`` — so the tool's own validation told the model it had **omitted a required
argument for a call it had made correctly**.

That misattribution is worse than a visible error. There was no retry, no counter and no event, so
it was invisible in the ledger and in any transcript review (issue 1773).

The pieces that were already there and unused:

* ``LLMEvent.stop_reason`` was declared in ``llm/events.py``. ``llm/anthropic.py`` never wrote it,
  and ``llm/openai.py`` read ``finish_reason`` only to decide when to flush — ``"length"`` was
  never a case, so a truncated call fell through to the DEFENSIVE flush with no indication.
* ``FailureMode.TOKEN_OVERFLOW`` and its correction note ("Your previous response was too long and
  was cut off…") existed with **zero writers** anywhere in ``src/``. This closes an inert control
  rather than adding a new one.

🪤 THE ISSUE NAMES THE FALSE GREEN AND IT IS RIGHT: *"assert the call site, not just the helper."*
A tolerant ``read_tool_arguments`` proves nothing on its own — the defect was that the runtime
collapsed its answer. So the legs below drive ``_prepare_call`` and ``_unrunnable_result``, which is
where the observation the model actually receives is decided.

**What this deliberately does NOT do: retry.** The issue's step 2 asks for one. ``FailureMode``
belongs to ``ModelCallGuard``, which owns the retry machinery (``is_retryable``,
``correction_note``, ``NON_RETRYABLE``) — and the native agent loop does not go through it, so
retrying here means wiring the loop into the guard. That is new control flow with cost and
loop-interaction consequences, not a wiring fix, and it is filed separately. What ships here is the
part that makes the failure TRUE: the model is told what actually happened.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.agents.native.tools import ARGUMENTS_UNREADABLE, read_tool_arguments
from personalclaw.guardrails.failure import FailureMode, correction_note


class TestReadingArguments:
    def test_a_plain_object_is_returned(self) -> None:
        assert read_tool_arguments('{"path": "a.md"}') == {"path": "a.md"}

    def test_an_already_parsed_dict_passes_through(self) -> None:
        assert read_tool_arguments({"path": "a.md"}) == {"path": "a.md"}

    @pytest.mark.parametrize("empty", ["", None, 0])
    def test_genuinely_absent_arguments_are_an_EMPTY_dict(self, empty) -> None:
        """Not `ARGUMENTS_UNREADABLE`. A call that legitimately takes no arguments must not be
        reported as broken — collapsing those two is the bug, in the other direction."""
        assert read_tool_arguments(empty) == {}

    def test_a_TRUNCATED_argument_string_is_unreadable_not_empty(self) -> None:
        """🔴 The defect. A prefix of valid JSON is what a `max_tokens` cut produces."""
        assert read_tool_arguments('{"path": "notes/q3-recon') is ARGUMENTS_UNREADABLE

    def test_markdown_fences_are_stripped(self) -> None:
        """The non-agentic path already does this (`llm_helpers._parse_llm`), so the agent path was
        behind our own standard for the same input."""
        assert read_tool_arguments('```json\n{"path": "a.md"}\n```') == {"path": "a.md"}
        assert read_tool_arguments('```\n{"n": 1}\n```') == {"n": 1}

    def test_double_serialised_arguments_are_read(self) -> None:
        """A JSON *string* whose content is the object. Parsing once yields a string, which the old
        code discarded as "not a dict" and reported as no arguments at all."""
        assert read_tool_arguments(json.dumps('{"path": "a.md"}')) == {"path": "a.md"}

    @pytest.mark.parametrize("raw", ['"just a string"', "[1, 2]", "42", "```json\n```", 12.5])
    def test_a_non_object_is_unreadable(self, raw) -> None:
        assert read_tool_arguments(raw) is ARGUMENTS_UNREADABLE


class TestTheCallSiteAttributesIt:
    """The half the issue warns about. These read the runtime's own decision."""

    @staticmethod
    def _prep(tool_input: str, stop_reason: str = ""):
        from personalclaw.agents.native.runtime import NativeAgentRuntime
        from personalclaw.llm.events import AgentEvent

        call = AgentEvent(kind="tool_call", tool_call_id="tc-1", title="read_file")
        call.tool_input = tool_input
        call.stop_reason = stop_reason
        runtime = NativeAgentRuntime.__new__(NativeAgentRuntime)
        # Only what `_prepare_call` touches — constructing a whole runtime would drag a provider,
        # a cwd and a tool registry into a test about one branch.
        runtime._resolve_name = lambda n: n  # type: ignore[method-assign]
        runtime._tool_risk = {}  # type: ignore[attr-defined]
        runtime._requires_approval = lambda n: False  # type: ignore[method-assign]
        runtime._cwd = None  # type: ignore[attr-defined]
        return NativeAgentRuntime._prepare_call(runtime, call)

    def test_a_TRUNCATED_call_is_answered_with_the_overflow_note(self) -> None:
        prep = self._prep('{"path": "notes/q3-recon', stop_reason="length")
        assert prep.arg_error == correction_note(FailureMode.TOKEN_OVERFLOW)
        # `_unrunnable_result` is the ONE seam both dispatch paths consult before invoking, so an
        # observation here means the tool is not run and the call is still answered.
        from personalclaw.agents.native.runtime import NativeAgentRuntime

        observation = NativeAgentRuntime._unrunnable_result(prep, [])
        assert observation is not None
        assert "too long and was cut off" in observation[0]
        assert "read_file was not run" in observation[0]

    def test_anthropics_max_tokens_spelling_is_recognised_too(self) -> None:
        """Two wire vocabularies for one fact: OpenAI says `length`, Anthropic `max_tokens`.
        Recognising one and not the other would fix half the installs."""
        prep = self._prep('{"path": "notes/q3', stop_reason="max_tokens")
        assert prep.arg_error == correction_note(FailureMode.TOKEN_OVERFLOW)

    def test_MALFORMED_but_complete_arguments_are_not_blamed_on_truncation(self) -> None:
        """🪤 The floor. Calling everything truncation would be a second misattribution, pointing
        the model at length when the real defect is its JSON."""
        prep = self._prep("{'path': 'a.md'}", stop_reason="stop")
        assert prep.arg_error
        assert "not valid JSON" in prep.arg_error
        assert "too long" not in prep.arg_error

    def test_a_GOOD_call_is_untouched_and_still_runs(self) -> None:
        """The other floor, and the one that matters most: this branch sits in front of every tool
        call the agent makes. `_unrunnable_result` returning None is what lets it run."""
        from personalclaw.agents.native.runtime import NativeAgentRuntime

        prep = self._prep('{"path": "a.md"}', stop_reason="tool_calls")
        assert prep.arg_error == ""
        assert prep.args == {"path": "a.md"}
        assert NativeAgentRuntime._unrunnable_result(prep, []) is None

    def test_a_call_with_no_arguments_still_runs(self) -> None:
        from personalclaw.agents.native.runtime import NativeAgentRuntime

        prep = self._prep("", stop_reason="tool_calls")
        assert prep.arg_error == ""
        assert NativeAgentRuntime._unrunnable_result(prep, []) is None


def test_the_overflow_correction_note_now_has_a_writer() -> None:
    """It had none. A taxonomy entry with a correction note and no writer is an inert control, and
    the issue's framing is right: this closes one rather than adding a control."""
    import subprocess

    from personalclaw.agents.native import runtime as runtime_mod

    src = open(runtime_mod.__file__, encoding="utf-8").read()
    assert "FailureMode.TOKEN_OVERFLOW" in src, "the runtime no longer writes the overflow mode"
    # And it is reachable from the argument branch specifically, not merely imported somewhere.
    assert "correction_note(FailureMode.TOKEN_OVERFLOW)" in src
    del subprocess


def test_the_openai_flush_recognises_length() -> None:
    """`"length"` was never a case, so a truncated tool call reached the defensive flush with no
    stop reason attached — the signal existed on the wire and was dropped at the boundary."""
    from personalclaw.llm import openai as openai_mod

    src = open(openai_mod.__file__, encoding="utf-8").read()
    assert '{"tool_calls", "stop", "length"}' in src
    assert 'stop_reason=str(finish_reason or "")' in src


def test_anthropic_reads_a_stop_reason_at_all() -> None:
    """It read none. The field was declared in `llm/events.py` and never written on this path."""
    from personalclaw.llm import anthropic as anthropic_mod

    src = open(anthropic_mod.__file__, encoding="utf-8").read()
    assert "stop_reason = str(reason)" in src
    assert src.count("stop_reason=stop_reason") >= 2, "both streaming regions must carry it"
