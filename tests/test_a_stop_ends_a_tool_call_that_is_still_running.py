"""A stop ends the turn while one of its in-process tool calls is still running.

``NativeAgentRuntime.cancel`` released the turn's approvals, aborted the model request and reaped
the child processes the turn started, then answered. It never reached the call in flight: the
turn awaited ``prov.invoke`` itself, and noticed the stop only once the tool returned by itself.
So a provider call that hung (a slow network read, a stuck library) kept the turn running for as
long as it did, the soft stop never escalated, and ``personalclaw run``'s stop waited with it: a
run whose 5 s timeout passed during a held call ended when the call's own 30 s hold ran out.

Now each in-process call runs as a task raced against the turn's stop signal
(``CancelScope.run_call``). On a stop the task is cancelled, so the tool sees ``CancelledError``
at its next await, and the turn ends. A call that does not finish within the grace after its
cancel is left running with its result discarded, named in the log, and the turn ends anyway.
Either way the call is answered as stopped, which the failure breaker does not count.

Driven through a real ``NativeAgentRuntime`` turn over a scripted model and a tool provider of
its own; every stop goes through ``cancel()``, the seam a user's stop arrives on.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from test_stop_means_stop import _APPEAR_TIMEOUT, _defn, _Driver, _ScriptedModel, _wait_for

from personalclaw import cancellation
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

pytestmark = pytest.mark.asyncio

#: How long a stopped turn may take to end once its stop answered. Well under the grace a call
#: that ignores its cancel is given, so only a stop that reached the call passes.
ENDS_WITHIN = 2.0


class _Hangs(ToolProvider):
    """``fetch_page``, a call that never returns by itself, as a hung network read, and
    ``read_file``, which does the same and which the plan runs side by side with another read;
    with *ignores_cancel*, each catches its cancel and keeps going until it is let go."""

    def __init__(self, *, ignores_cancel: bool = False) -> None:
        self.ignores_cancel = ignores_cancel
        self.running = 0
        self.cancelled = 0
        self.release = asyncio.Event()

    @property
    def name(self) -> str:
        return "pages"

    @property
    def display_name(self) -> str:
        return "Pages"

    async def list_tools(self):
        return [
            ToolDefinition(name=name, description="d", parameters={"type": "object"})
            for name in ("fetch_page", "read_file")
        ]

    async def invoke(self, tool_name, arguments):
        self.running += 1
        while True:
            try:
                await self.release.wait()
                return ToolResult(success=True, output="the page")
            except asyncio.CancelledError:
                self.cancelled += 1
                if not self.ignores_cancel:
                    raise


def _fetch(call_id: str, url: str = "https://example.org/a") -> AgentEvent:
    return AgentEvent(
        kind=EVENT_TOOL_CALL,
        tool_call_id=call_id,
        title="fetch_page",
        tool_input=f'{{"url": "{url}"}}',
    )


def _read(call_id: str, path: str) -> AgentEvent:
    return AgentEvent(
        kind=EVENT_TOOL_CALL,
        tool_call_id=call_id,
        title="read_file",
        tool_input=f'{{"path": "{path}"}}',
    )


async def _runtime(tool: _Hangs, *calls: AgentEvent) -> NativeAgentRuntime:
    """A turn whose model asks for *calls*, then, once they are answered, says it is done."""
    said = [AgentEvent(kind=EVENT_TEXT_CHUNK, text="Done."), AgentEvent(kind=EVENT_COMPLETE)]
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=_ScriptedModel([[*calls, AgentEvent(kind=EVENT_COMPLETE)], said]),
        tool_providers=[tool],
    )
    await rt.start()
    rt.set_approval_policy("auto")
    return rt


async def _stop_while_it_runs(rt: NativeAgentRuntime, tool: _Hangs, *, running: int = 1):
    """Start the turn, stop it once *running* calls are in flight, and return the turn's events
    and how long it took to end after the stop answered. Whatever the turn did, the tool is let
    go before the turn is put away, so a stop that never reaches the call fails the row instead of
    leaving the call holding the run."""
    async with _Driver(rt) as driver:
        assert driver.task is not None
        try:
            await _wait_for(lambda: tool.running >= running, _APPEAR_TIMEOUT, "the call running")
            assert await rt.cancel() == "acked"
            answered = time.monotonic()
            # Waited for without cancelling it: a cancel would reach the call, and one that
            # ignores it would hold the wait.
            ended, _ = await asyncio.wait({driver.task}, timeout=8.0)
            took = time.monotonic() - answered
        finally:
            tool.release.set()
        assert ended, "the turn was still running 8 s after the stop"
        events = await driver.finish(timeout=15.0)
    return events, took


def _results(events: list[AgentEvent]) -> list[AgentEvent]:
    return [e for e in events if e.kind == EVENT_TOOL_RESULT]


async def test_a_stop_ends_the_turn_while_its_call_still_runs():
    """🔴 Red before: the turn ran on until the call returned by itself, which this one never
    does, and the stop's answer changed nothing."""
    tool = _Hangs()
    rt = await _runtime(tool, _fetch("c1"))

    events, took = await _stop_while_it_runs(rt, tool)

    assert took < ENDS_WITHIN, f"the turn ended {took:.1f}s after the stop"
    assert tool.cancelled == 1, "the call was never told to stop"
    (result,) = _results(events)
    assert result.tool_meta.get("ok") is False
    assert "fetch_page was stopped while it ran" in result.tool_output
    assert "check before running it again" in result.tool_output
    assert rt.last_stop_report()["tool_calls_stopped"] == 1
    assert rt.last_stop_report()["tool_calls_escaped"] == 0
    # The call is still answered in the history, or the next turn's replay breaks.
    called = sum(1 for m in rt._messages for _ in (m.get("tool_calls") or []))
    assert called == sum(1 for m in rt._messages if m.get("role") == "tool") == 1


async def test_every_call_of_a_wave_running_together_is_stopped():
    """Two calls the plan runs side by side, each still running at the stop."""
    tool = _Hangs()
    rt = await _runtime(tool, _read("c1", "a.txt"), _read("c2", "b.txt"))

    events, took = await _stop_while_it_runs(rt, tool, running=2)

    assert took < ENDS_WITHIN
    assert tool.cancelled == 2
    assert ["read_file was stopped while it ran" in e.tool_output for e in _results(events)] == [
        True,
        True,
    ]
    assert rt.last_stop_report()["tool_calls_stopped"] == 2


async def test_a_call_that_ignores_its_cancel_is_left_and_the_turn_ends_anyway(monkeypatch, caplog):
    """🔴 Red before: the turn waited for it, however long it ran."""
    monkeypatch.setattr(cancellation, "CANCEL_GRACE_SECS", 0.3)
    tool = _Hangs(ignores_cancel=True)
    rt = await _runtime(tool, _fetch("c1"))

    with caplog.at_level(logging.WARNING, logger="personalclaw.cancellation"):
        events, took = await _stop_while_it_runs(rt, tool)

    assert took < ENDS_WITHIN
    (result,) = _results(events)
    assert "did not stop when asked and may still be running" in result.tool_output
    assert rt.last_stop_report()["tool_calls_escaped"] == 1
    assert any("tool call fetch_page" in r.getMessage() for r in caplog.records), caplog.text
    # The call was let go as the turn was put away: give it the moment it needs to end, so
    # nothing is left running after the test.
    await asyncio.sleep(0.05)


async def test_a_stopped_call_is_not_counted_as_a_failure_of_its_tool():
    """A stop is not evidence about the tool: the breaker and the turn's outcome list are left
    as they were."""
    tool = _Hangs()
    rt = await _runtime(tool, _fetch("c1"))

    await _stop_while_it_runs(rt, tool)

    assert rt._tool_outcomes == []
    assert rt._breaker.total_failures == 0


async def test_a_call_that_returned_before_the_stop_keeps_its_result():
    tool = _Hangs()
    tool.release.set()
    rt = await _runtime(tool, _fetch("c1"))

    async with _Driver(rt) as driver:
        events = await driver.finish(timeout=15.0)

    (result,) = _results(events)
    assert result.tool_output == "the page"
    assert tool.cancelled == 0
