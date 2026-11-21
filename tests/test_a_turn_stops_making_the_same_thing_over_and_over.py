"""A turn that keeps making the same thing apart from a number is refused, then stopped, and says
what it made.

A project chat could not call the tool it wanted that turn, said "Spawning the batch of five
helpers now.", and called ``automation_create`` 48 times: ``noop-probe-2``, ``noop-probe-3``, … The
first 19 succeeded, leaving 19 throwaway automations waiting for an Allow, and only the cap on
agent-created automations stopped the rest. The loop breaker saw no loop: each call's arguments
differed by a number, and a call that acts is never counted as a repeat.

Now a creating tool (``…_create``) that made :data:`~loop_breaker.MADE_REPEAT_BLOCK` things in a
turn with the same call apart from the numbers in what it names them is refused the next time,
before it runs; the refusals count toward the repeat circuit, and the sentence that stops the turn
names what it made. A number that schedules or dates a thing makes it distinct, so a series of
reminders or tasks at different times runs every call, and so does a week of ``Day 1`` to
``Day 7``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.guardrails import loop_breaker as lb
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult


def _probe(n: int) -> tuple[str, dict]:
    return (
        "automation_create",
        {"kind": "manual", "name": f"noop-probe-{n}", "message": f"noop probe {n}"},
    )


_DISTINCT = [
    {"kind": "manual", "name": "standup", "message": "Draft my standup from yesterday's notes"},
    {"kind": "manual", "name": "water the plants", "message": "Remind me to water the plants"},
    {"kind": "manual", "name": "invoice", "message": "Draft the monthly invoice for the client"},
    {"kind": "manual", "name": "backup", "message": "Check last night's backup finished"},
    {"kind": "manual", "name": "reading", "message": "Pick an article from my reading list"},
]


# ── the rule ─────────────────────────────────────────────────────────────────


def test_the_same_creation_apart_from_a_number_is_refused_past_the_threshold():
    """🔴 Red before: every creation ran; nothing counted a call that acts."""
    breaker = lb.LoopBreaker()
    refused = []
    for n in range(2, 2 + lb.MADE_REPEAT_BLOCK + 3):
        tool, args = _probe(n)
        key = lb.params_key(tool, args)
        text = breaker.refuse(tool, key, reads=False)
        refused.append(bool(text))
        if not text:
            breaker.record(key, failed=False)

    assert refused == [False] * lb.MADE_REPEAT_BLOCK + [True] * 3


def test_distinct_creations_are_never_refused():
    breaker = lb.LoopBreaker()
    for args in _DISTINCT * 2:
        key = lb.params_key("automation_create", args)
        assert breaker.refuse("automation_create", key, reads=False) == ""
        breaker.record(key, failed=False)


def _made_every_one(tool: str, calls: list[dict]) -> list[bool]:
    breaker = lb.LoopBreaker()
    ran = []
    for args in calls:
        key = lb.params_key(tool, args)
        ran.append(breaker.refuse(tool, key, reads=False) == "")
        if ran[-1]:
            breaker.record(key, failed=False)
    return ran


@pytest.mark.parametrize(
    ("tool", "calls"),
    [
        (
            "automation_create",
            [
                {"name": "Drink water", "when": f"every day at {h}", "message": "Drink water"}
                for h in ("8am", "10am", "12pm", "2pm", "4pm", "6pm", "8pm", "10pm", "11pm", "1am")
            ],
        ),
        (
            "automation_create",
            [
                {"name": "Stretch", "when": f"weekdays at {h:02d}:00", "message": "Stretch"}
                for h in range(8, 19)
            ],
        ),
        (
            "task_create",
            [{"title": "Weekly review", "due": f"2026-12-{d:02d}"} for d in range(1, 29, 2)],
        ),
        ("task_create", [{"title": f"Day {n}"} for n in range(1, 8)]),
        ("deck_create", [{"title": f"Q{n} report", "template": "quarterly"} for n in range(1, 5)]),
    ],
    ids=[
        "a reminder at each time of day",
        "a reminder at each hour",
        "a task for each due date",
        "a week of days",
        "a report for each quarter",
    ],
)
def test_a_series_the_user_asks_for_runs_every_call(tool, calls):
    """🔴 Red before: every number in the call counted it off, a time or a date too, so the
    fourth of these was refused."""
    assert _made_every_one(tool, calls) == [True] * len(calls)


def test_a_tool_that_creates_nothing_is_not_counted():
    breaker = lb.LoopBreaker()
    for n in range(10):
        key = lb.params_key("memory_remember", {"rule": f"note {n}"})
        assert breaker.refuse("memory_remember", key, reads=False) == ""
        breaker.record(key, failed=False)


def test_a_failed_creation_made_nothing_and_a_new_turn_starts_the_count_again():
    breaker = lb.LoopBreaker()
    tool, args = _probe(2)
    for _ in range(lb.MADE_REPEAT_BLOCK):
        breaker.record(lb.params_key(tool, args), failed=True)
    assert "same thing" not in breaker.refusal(tool, lb.params_key(*_probe(9)), reads=False)

    for n in range(3, 3 + lb.MADE_REPEAT_BLOCK):
        breaker.record(lb.params_key(*_probe(n)), failed=False)
    assert breaker.refusal(tool, lb.params_key(*_probe(20)), reads=False)
    breaker.begin_turn()
    assert breaker.refusal(tool, lb.params_key(*_probe(20)), reads=False) == ""


# ── a turn ───────────────────────────────────────────────────────────────────


class _Automations(ToolProvider):
    def __init__(self) -> None:
        self.made: list[str] = []

    @property
    def name(self) -> str:
        return "automations-under-test"

    @property
    def display_name(self) -> str:
        return "Automations"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="automation_create",
                description="Create an automation.",
                parameters={"type": "object", "properties": {"name": {"type": "string"}}},
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
            )
        ]

    async def invoke(self, tool_name, arguments):
        self.made.append(str(arguments.get("name")))
        return ToolResult(success=True, output=f"created {arguments.get('name')}")


class _Script:
    """A model that makes the calls it was scripted to, whatever it is told."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self._calls = calls
        self.inferences = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.inferences += 1
        if self.inferences <= len(self._calls):
            name, args = self._calls[self.inferences - 1]
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"c{self.inferences}",
                title=name,
                tool_input=json.dumps(args),
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Done.")
        yield AgentEvent(kind=EVENT_COMPLETE)


def _turn(calls):
    tools = _Automations()
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Script(calls),
        tool_providers=[tools],
    )

    async def _run():
        await runtime.start()
        return [ev async for ev in runtime.stream("hand these four tasks to helpers")]

    return asyncio.run(asyncio.wait_for(_run(), timeout=30)), tools


def test_the_probing_turn_makes_three_then_is_stopped_naming_them():
    """🔴 Red before: it made one automation per call until something else stopped it."""
    events, tools = _turn([_probe(n) for n in range(2, 50)])

    assert tools.made == [f"noop-probe-{n}" for n in range(2, 2 + lb.MADE_REPEAT_BLOCK)]
    results = [str(ev.tool_output) for ev in events if ev.kind == EVENT_TOOL_RESULT]
    assert any("the same thing" in r for r in results)
    final = events[-1]
    assert final.stop_reason == "cancelled"
    assert final.text.startswith("Run aborted by the loop breaker: ")
    for name in tools.made:
        assert name in final.text, final.text


def test_a_turn_making_distinct_things_makes_every_one():
    events, tools = _turn([("automation_create", args) for args in _DISTINCT])

    assert tools.made == [args["name"] for args in _DISTINCT]
    assert events[-1].stop_reason != "cancelled"
