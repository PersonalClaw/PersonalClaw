"""Compacting a conversation never separates a tool call from its results.

Measured on a running chat: an agent read a release list (``find_releases`` → "created
2026-10-02T13:05:12Z") and a note, answered "That release was created at 13:05Z", and after
``/compact`` told the user "My release lookup never returned a result, so I had no source for
that", retracting a fact it had sourced. The compaction had cut the history between a batch of
calls and their results: the tail kept the results, the folded middle took the call, so the
results were dropped as orphans, and the record of the folded region said the call had "no result
recorded — this call may not have finished". A call at the head's edge lost its results the same
way, and the provider then answers such a call as interrupted.

The rule now: the protected head and the protected tail each hold whole exchanges (a call and
every result it got), so what compaction keeps it keeps whole, and what it folds it folds whole.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.context_compaction import compact
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, AgentEvent
from personalclaw.resume_account import FENCE_START
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

RELEASES = "# Releases\n- svc@2026.10.02-1, created 2026-10-02T13:05:12Z"
ISSUE = "# ISSUE-4F2\nReadTimeout\nRelease: svc@2026.10.02-1\nlog error giving_up attempts=5"
#: Results long enough that folding the middle saves something (a pass that saves nothing is not
#: applied), as the real run's schema dump and note read were.
NOTE = "# On call, week of 28 Sep\n" + "- a watch line about the carrier and its retries\n" * 20
SCHEMA = "(schema)\n" + "field text " * 100


def _call(cid: str, name: str) -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": '{"q": "x"}'}}


def _batch(calls: list[tuple[str, str, str]]) -> list[dict]:
    """One model turn's tool batch, as the native loop records it: the assistant message with
    its calls, then one result per call."""
    out: list[dict] = [
        {"role": "assistant", "content": "", "tool_calls": [_call(c, n) for c, n, _ in calls]}
    ]
    out += [{"role": "tool", "tool_call_id": c, "content": r} for c, _, r in calls]
    return out


def _the_triage_conversation() -> list[dict]:
    """The routed triage chat's history at its ``/compact``, shape for shape: four batches, the
    last of which (the issue and the release list) sits across the tail's edge, a steer, the
    answer that cited them, and two short turns."""
    msgs: list[dict] = [{"role": "user", "content": "[AGENT SYSTEM PROMPT]\nrules\n[END]\nq"}]
    msgs += _batch(
        [
            ("c1", "shell_probe", "Error: fatal: not a git repository"),
            ("c2", "search_text", "notes/oncall.md:7: the breaker ticket is TICKET-1"),
            ("c3", "issue_details", "Error: issue not found"),
        ]
    )
    msgs += _batch([("c4", "issue_details", "Error: issue not found")])
    msgs += _batch([("c5", "list_files", SCHEMA), ("c6", "read_note", NOTE)])
    msgs += _batch([("c7", "issue_details", ISSUE), ("c8", "find_releases", RELEASES)])
    msgs += [
        {"role": "user", "content": "[Steering]\nignore the EU region"},
        {"role": "assistant", "content": "That release was created at 13:05Z."},
        {"role": "user", "content": "write a runbook"},
        {"role": "assistant", "content": "# Runbook\nFrom the issue and your on-call note."},
        {"role": "user", "content": "where did you stop?"},
        {"role": "assistant", "content": "Section 3."},
    ]
    return msgs


def _calls_and_results(msgs: list[dict]) -> tuple[list[str], list[str]]:
    calls = [str(c.get("id")) for m in msgs for c in (m.get("tool_calls") or [])]
    results = [str(m.get("tool_call_id")) for m in msgs if m.get("role") == "tool"]
    return calls, results


def _folded(msgs: list[dict]) -> bool:
    return any("CONTEXT COMPACTION" in str(m.get("content", "")) for m in msgs)


def test_a_batch_across_the_tails_edge_keeps_its_results():
    before = _the_triage_conversation()
    after = compact(before)
    assert _folded(after), "the fixture must actually fold a middle, or this proves nothing"

    sent = "\n".join(str(m.get("content", "")) for m in after)
    assert RELEASES in sent, "the release list the answer cited was dropped by the compaction"
    assert ISSUE in sent
    calls, results = _calls_and_results(after)
    assert set(results) <= set(calls), f"a kept result lost its call: {calls} vs {results}"
    # The answer that cited them is still there, and so is everything after it.
    assert after[-6:] == before[-6:]


def test_no_call_is_left_without_its_results():
    """The head's edge: the first batch's call stays in the head, so its results must stay too —
    a call kept without them is answered by the provider as an interrupted no-op."""
    after = compact(_the_triage_conversation())
    assert _folded(after)
    calls, results = _calls_and_results(after)
    assert set(calls) == set(results), f"calls {calls} were kept with results {results}"


def test_the_record_of_the_folded_part_never_says_a_kept_result_was_missing():
    after = compact(_the_triage_conversation())
    accounts = [str(m["content"]) for m in after if FENCE_START in str(m.get("content", ""))]
    assert accounts, "the folded region recorded calls, so an account must say what they did"
    for line in accounts[0].splitlines():
        assert "no result recorded" not in line, f"the account invents a missing result: {line}"
    assert "read_note" in accounts[0], "the folded note read is still recorded as done"


def test_whole_exchanges_still_fold_when_they_fit():
    """VACUITY FLOOR: keeping exchanges whole does not stop compaction. A conversation whose
    batches all fall inside the middle is folded exactly as before."""
    msgs: list[dict] = [{"role": "user", "content": f"head {i}"} for i in range(3)]
    for i in range(6):
        msgs += _batch([(f"m{i}", "read_note", NOTE)])
    msgs += [{"role": "user", "content": f"tail {i}"} for i in range(8)]
    after = compact(msgs)
    assert _folded(after)
    assert after[:3] == msgs[:3] and after[-8:] == msgs[-8:]
    assert _calls_and_results(after) == ([], [])


# ── through the native loop: what the next request carries after /compact ──


_OUTPUTS = {
    "shell_probe": (False, "fatal: not a git repository"),
    "search_text": (True, "notes/oncall.md:7: the breaker ticket is TICKET-1"),
    "issue_details": (True, ISSUE),
    "list_files": (True, SCHEMA),
    "read_note": (True, NOTE),
    "find_releases": (True, RELEASES),
}


class _Tools(ToolProvider):
    @property
    def name(self) -> str:
        return "triage"

    @property
    def display_name(self) -> str:
        return "Triage"

    async def list_tools(self):
        return [
            ToolDefinition(
                name=name,
                description=f"{name} tool",
                parameters={"type": "object", "properties": {"q": {"type": "string"}}},
                requires_approval=False,
                provider="triage",
            )
            for name in _OUTPUTS
        ]

    async def invoke(self, tool_name, arguments):
        ok, text = _OUTPUTS[tool_name]
        return ToolResult(success=ok, output=text if ok else "", error="" if ok else text)


class _Model:
    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self.turns = turns
        self.requests: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append([dict(m) for m in messages])
        for event in self.turns[min(len(self.requests), len(self.turns)) - 1]:
            yield event


def _tool_turn(*calls: tuple[str, str]) -> list[AgentEvent]:
    events = [
        AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=cid, title=name, tool_input='{"q": "x"}')
        for cid, name in calls
    ]
    return [*events, AgentEvent(kind=EVENT_COMPLETE)]


def _text_turn(text: str) -> list[AgentEvent]:
    return [AgentEvent(kind=EVENT_TEXT_CHUNK, text=text), AgentEvent(kind=EVENT_COMPLETE)]


@pytest.mark.asyncio
async def test_after_compact_the_model_still_sees_where_its_answer_came_from():
    model = _Model(
        [
            _tool_turn(("c1", "shell_probe"), ("c2", "search_text"), ("c3", "issue_details")),
            _tool_turn(("c4", "issue_details")),
            _tool_turn(("c5", "list_files"), ("c6", "read_note")),
            _tool_turn(("c7", "issue_details"), ("c8", "find_releases")),
            _text_turn("That release was created at 13:05Z."),
            _text_turn("# Runbook\nFrom the issue and your on-call note."),
            _text_turn("Section 3."),
            _text_turn("The root cause is unconfirmed."),
        ]
    )
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="oncall-triage", provider="native", model="s"),
        model_provider=model,
        tool_providers=[_Tools()],
    )
    await rt.start()
    steer = ["ignore the EU region"]
    rt.set_steer_source(lambda: [steer.pop()] if steer and len(model.requests) >= 4 else [])
    for text in ("sentry is lighting up again", "write a runbook", "where did you stop?"):
        [e async for e in rt.stream(text)]
        rt.set_steer_source(None)
    before = rt._messages[:]
    await rt.compact()
    assert len(rt._messages) < len(before), "the history must actually be compacted"

    [e async for e in rt.stream("what's the root cause?")]
    sent = model.requests[-1]
    blob = json.dumps(sent)
    assert "2026-10-02T13:05:12Z" in blob, "the release list behind the cited time is gone"
    assert "no result recorded" not in blob
    calls, results = _calls_and_results(sent)
    assert set(calls) == set(results), f"calls {calls} were sent with results {results}"
