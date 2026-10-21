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


#: One model turn that calls `read_file` with these arguments, cut or not, then answers: (the
#: provider's stop reason, the arguments as they reached the runtime, the note the model is told,
#: or None when the call runs). A call cut at the token limit, in either wire's spelling, is
#: answered with the failure taxonomy's own correction note for TOKEN_OVERFLOW; a call whose
#: complete arguments are not JSON is told so, and not that it was cut; a readable call runs.
_CALLS = {
    "cut, OpenAI's spelling": ("length", '{"path": "notes/q3-recon', "TOKEN_OVERFLOW"),
    "cut, Anthropic's spelling": ("max_tokens", '{"path": "notes/q3', "TOKEN_OVERFLOW"),
    "malformed, not cut": ("stop", "{'path': 'a.md'}", "not valid JSON"),
    "readable": ("tool_calls", '{"path": "a.md"}', None),
}


@pytest.mark.asyncio
@pytest.mark.parametrize(("stop_reason", "raw", "told"), _CALLS.values(), ids=list(_CALLS))
async def test_the_model_is_told_its_call_was_cut_in_the_taxonomys_words(
    monkeypatch, stop_reason, raw, told
) -> None:
    """It had no writer. A taxonomy entry with a correction note and no writer is an inert
    control, and the issue's framing is right: this closes one rather than adding a control.
    Driven through the real native loop, with a stand-in for `correction_note` under every name
    that holds it, the next request the model receives answers its call with what the stand-in
    says for TOKEN_OVERFLOW, asked for that mode alone, and the tool never runs on a cut call."""
    from every_name import put_in_every_name
    from test_native_runtime import _defn, _ScriptedModel, _Tool

    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.guardrails import failure
    from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent

    asked: list[FailureMode] = []

    def note(mode: FailureMode) -> str:
        asked.append(mode)
        return f"<the taxonomy's note for {mode.name}>"

    put_in_every_name(monkeypatch, failure.correction_note, note)
    call = AgentEvent(kind="tool_call", tool_call_id="tc-1", title="read_file", tool_input=raw)
    call.stop_reason = stop_reason
    model = _ScriptedModel(
        [
            [call, AgentEvent(kind=EVENT_COMPLETE)],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )
    tool = _Tool(name="read_file")
    runtime = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool])
    await runtime.start()
    async for _event in runtime.stream("read my notes"):
        pass

    answered = [
        str(m.get("content") or "")
        for m in model.seen_messages[-1]
        if m.get("role") == "tool" and m.get("tool_call_id") == "tc-1"
    ]
    assert len(answered) == 1, model.seen_messages[-1]
    if told is None:
        assert tool.invoked == [{"path": "a.md"}] and asked == []
        return
    assert tool.invoked == [], "a call whose arguments could not be read was run"
    assert answered[0].startswith("Error: read_file was not run."), answered[0]
    if told == "TOKEN_OVERFLOW":
        assert asked == [FailureMode.TOKEN_OVERFLOW]
        assert answered[0].endswith("<the taxonomy's note for TOKEN_OVERFLOW>"), answered[0]
    else:
        assert asked == [] and told in answered[0] and "cut off" not in answered[0]
