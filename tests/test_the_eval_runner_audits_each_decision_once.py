"""The eval runner writes ONE audit row per tool call, after it is decided, naming who decided.

It wrote an ``invoked`` row for every tool call the moment the call appeared, before any gate had
run, and then a second row for a call that asked. So a call its own allowlist refused was on the
record as invoked as well as refused, and an approval its allowlist gave read ``approved`` — the
word for a person's Allow. The rule the chat, the subagent manager, the background helper and the
suggestions turn follow (`approval_grants`, rule 3): a call that asks is audited once, after its
answer took effect, and a call nobody was asked about once, from its result.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from personalclaw import approval_grants
from personalclaw.eval.runner import EvalRunner
from personalclaw.eval.scenario import Turn
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
)


class _Timeline:
    """The provider's approve/reject calls and the audit rows, in the order they happened."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def rows(self) -> list[dict]:
        return [row for kind, row in self.events if kind == "row"]  # type: ignore[misc]

    def log_tool_invocation(self, **row) -> None:
        self.events.append(("row", row))


class _Provider:
    """Three calls: one its allowlist approves, one it refuses, and one that asks nobody."""

    def __init__(self, timeline: _Timeline) -> None:
        self._timeline = timeline

    async def stream(self, message: str):
        yield LLMEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="read_file", text="read_file")
        yield LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            tool_call_id="c1",
            title="read_file",
            tool_input='{"path": "/tmp/eval-notes.txt"}',
            request_id="r1",
        )
        yield LLMEvent(kind=EVENT_TOOL_RESULT, tool_call_id="c1", title="read_file")
        yield LLMEvent(
            kind=EVENT_TOOL_CALL, tool_call_id="c2", title="write_file", text="write_file"
        )
        yield LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            tool_call_id="c2",
            title="write_file",
            tool_input='{"path": "/tmp/eval-notes.txt"}',
            request_id="r2",
        )
        yield LLMEvent(kind=EVENT_TOOL_RESULT, tool_call_id="c2", title="write_file")
        yield LLMEvent(
            kind=EVENT_TOOL_CALL, tool_call_id="c3", title="knowledge_stats", text="knowledge_stats"
        )
        yield LLMEvent(kind=EVENT_TOOL_RESULT, tool_call_id="c3", title="knowledge_stats")
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield LLMEvent(kind=EVENT_COMPLETE)

    async def approve_tool(self, request_id) -> None:
        self._timeline.events.append(("approve", request_id))

    async def reject_tool(self, request_id) -> None:
        self._timeline.events.append(("reject", request_id))


async def _run(timeline: _Timeline):
    runner = EvalRunner(provider_factory=lambda key, **kw: _Provider(timeline))
    with patch("personalclaw.eval.runner.sel", return_value=timeline):
        return await runner._run_turn(_Provider(timeline), Turn(user="go"), "eval_s1")


@pytest.mark.asyncio
async def test_one_row_per_call_and_none_before_the_decision() -> None:
    timeline = _Timeline()
    result = await _run(timeline)

    assert result.tool_calls == ["read_file", "write_file", "knowledge_stats"]
    rows = timeline.rows()
    assert [row["tool_name"] for row in rows] == [
        "read_file",
        "write_file",
        "knowledge_stats",
    ], rows
    approved, refused, unasked = rows
    assert approved["outcome"] == "auto_approved"
    assert approved["metadata"]["decided_by"] == approval_grants.EVAL_SAFE_TOOLS
    assert refused["outcome"] == "denied"
    assert refused["metadata"]["decided_by"] == approval_grants.EVAL_SAFE_TOOLS
    assert refused["metadata"]["reason"] == "not_read_only"
    assert unasked["outcome"] == "invoked"
    assert unasked["metadata"]["decided_by"] == "no_approval_needed"


@pytest.mark.asyncio
async def test_each_decision_is_written_after_it_took_effect() -> None:
    timeline = _Timeline()
    await _run(timeline)

    kinds = [
        f"row:{what['tool_name']}" if kind == "row" else kind  # type: ignore[index]
        for kind, what in timeline.events
    ]
    assert kinds.index("row:read_file") > kinds.index("approve")
    assert kinds.index("row:write_file") > kinds.index("reject")


@pytest.mark.asyncio
async def test_an_ask_ceiling_refuses_the_allowlist_too() -> None:
    """The allowlist approves without asking anyone, so it is a grant, and the operator ceiling
    bounds it like every other grant (`approval_grants`, rule 2)."""
    timeline = _Timeline()
    with patch("personalclaw.guardrails.policy.ceiling_permits_approval", return_value=False):
        await _run(timeline)

    assert ("approve", "r1") not in timeline.events
    assert ("reject", "r1") in timeline.events
    read = timeline.rows()[0]
    assert read["tool_name"] == "read_file"
    assert read["outcome"] == "denied"
    assert read["metadata"] == {
        "reason": "refused_by_ceiling",
        "decided_by": approval_grants.NOBODY,
    }
