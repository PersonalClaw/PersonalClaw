"""Pressing stop stops the WORK, not just the stream (PR2-12).

THE CENSUS
==========
Measured on ``origin/main`` (df8cdb5e) before any code here changed, by reading the
call paths rather than by asserting a flag. Each row's closure has a behavioural test
below; each of those tests reds on the pre-fix line.

=========================== ============ =====================================================
layer                       reached?     evidence (pre-fix file:line)
=========================== ============ =====================================================
PR2-3 mid-turn cancel path  PARTLY       ``chat_handlers.py:1030`` sets ``session._stop_state``
                                         and ``session.py:1829`` calls ``provider.cancel``. But
                                         ``_stop_state`` had NO reader in ``chat_runner.py`` —
                                         its only readers were ``chat_handlers``,
                                         ``chat_plan.py:305`` and ``state.py:444/700``. The
                                         running turn never learned a stop had happened; it
                                         learned only that a flag it does not read was set.
in-flight model request     NO           ``runtime.py:1287-1290`` — ``cancel()`` set
                                         ``self._cancelled = True`` and returned "acked". It
                                         never called ``self._model.cancel()``, although every
                                         provider implements one (``openai.py:565``,
                                         ``anthropic.py:788``, ``acp_agent.py:605``). The loop
                                         at ``runtime.py:670-676`` then checked the flag only
                                         when the NEXT event arrived — awaited-and-discarded,
                                         which is exactly what the clause forbids.
dispatched tool subprocess  NO           ``builtin_tools.py:1512-1529`` — the bash child was
                                         reachable only by its own TIMEOUT. No registry existed
                                         for a cancel to consult, and the timeout's
                                         ``proc.kill()`` signalled one pid, so the shell's own
                                         children survived even there. The authoritative spawn
                                         census (``tests/test_spawn_ceiling_audit.py``) lists
                                         two per-turn agent-influenced async spawns a stop must
                                         reach — this one and
                                         ``sandbox_providers/none.py::_NoneHandle.exec``, which
                                         it funnels through; everything else in that file is a
                                         long-lived server (MCP), a sibling runtime (sidecar,
                                         app backend) or operator-exempt.
spawned subagent            NO           ``subagent.py`` had the machinery — ``_force_reap``
                                         (:768, kill→cancel→reap→tombstone→audit),
                                         ``cancel``/``cancel_fanout`` (:2246/:2263) and a
                                         parent-keyed enumeration (:987) — and NOTHING called
                                         any of it on a stop. ``SessionManager.stop_turn``
                                         (``session.py:1796``) cleared the queue, cancelled the
                                         provider and reset the session; the fan-out kept
                                         running, kept spending, and later delivered results
                                         into a session the user had stopped.
queued-but-unstarted calls  NO           ``runtime.py:740-743`` — ``for call in tool_calls:``
                                         with no cancel check in the body. A model turn emits a
                                         BATCH; a stop arriving mid-batch let every remaining
                                         call execute. (The sibling check at :711/:719 covers
                                         only a batch that had not STARTED.)
=========================== ============ =====================================================

DELIBERATELY OUT OF SCOPE (recorded, not silently omitted)
----------------------------------------------------------
* **The prior turn's follow-up-chip generation** (``chat_runner.py:1447-1454``, fired at
  ``:4012``). It is fire-and-forget background spend, but it belongs to the turn that
  ALREADY COMPLETED — chips are generated after the terminal event and cancelled by the
  next dispatch. A stop on turn N killing turn N-1's chips would remove chips the user
  can still see, for ~200 tokens. Not one of the five layers the clause enumerates.
* **An ACP-backed session reports ``cancelled``, not ``stopped_by_user``.** Its stop
  reason arrives on the wire from the external agent, so the distinction cannot be made
  locally without a second bookkeeping flag — which is the defect this atom is written
  against. What the ACP path DOES satisfy already: ``cancel()`` returns "no_turn" when
  no turn is active (``acp_session_provider.py:152-153``), the in-flight turn is aborted
  by ``session/cancel`` rather than awaited, and ``stop_turn``'s hard-kill path reaches
  the agent's whole process group via ``_sigkill_session``. It cannot register the
  external agent's own tool subprocesses, and no in-process mechanism could.

Two further defects the same reading turned up, both closed here:

* **Spend was dropped.** ``runtime.py:632-639`` yielded the cancelled turn's
  ``EVENT_COMPLETE`` with no ``input_tokens``/``output_tokens``/``cost_usd``, so a stop
  between ReAct cycles threw away every token the earlier cycles burned. The other
  cancelled exit (:723) carried them, so the two exits disagreed.
* **A post-completion stop was not a no-op — it corrupted the next turn.**
  ``NativeAgentRuntime.cancel()`` returned "acked" unconditionally, so
  ``stop_turn`` (``session.py:1832``) set ``session.prev_turn_cancelled = True`` and the
  NEXT turn opened with a bogus cancelled-turn preamble (``chat_runner.py:2056-2063``).
  "Not a failure" is not the same as "no effect".

THE BAR
=======
The clause asks for "a real driven stop asserting no child process survives and no
further tool call is dispatched after the signal — not an assertion that a flag was
set". So the load-bearing tests here drive a real ``NativeAgentRuntime`` over a real
``bash`` child, stop it, and then poll the OS for the child (and its grandchild) and
count tool dispatches. Nothing below asserts ``_cancelled is True`` as a conclusion.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time

import pytest

from personalclaw import cancellation
from personalclaw.acp.types import (
    STOP_REASON_CANCELLED,
    STOP_REASON_STOPPED_BY_USER,
    is_cancelled_stop,
)
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.cancellation import (
    CANCEL_INTERNAL,
    CANCEL_USER,
    REQUEST_FIRST,
    REQUEST_NO_TURN,
    REQUEST_REPEAT,
    CancelScope,
)
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

# Poll budgets. Generous enough for a loaded CI box, bounded so a wedge fails the test
# instead of hanging the suite.
_APPEAR_TIMEOUT = 15.0
_DEATH_TIMEOUT = 15.0
_POLL = 0.02


# ── helpers ───────────────────────────────────────────────────────────────────


def _pid_alive(pid: int) -> bool:
    """True while *pid* exists (signal 0 probes without delivering).

    A zombie still "exists" to ``kill -0``. That is deliberate: the clause says a
    stopped turn leaves no orphan, and an unreaped zombie IS the shape of orphan that
    still holds its end of a pipe — so this probe must not forgive one. The reaping in
    ``terminate_and_reap`` is what makes it go away.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for(predicate, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(_POLL)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def _assert_ours(pid: int) -> None:
    """The safety rail: never assert on a pid we did not spawn.

    A test that kills processes must be unable to reach anything but its own fixture,
    so this refuses the two pids that would be catastrophic — this process and its
    process-group leader — before any test treats a pid as a victim.
    """
    assert pid > 1, f"refusing to treat pid {pid} as a child"
    assert pid != os.getpid(), "refusing to treat the test process as a child"
    assert pid != os.getpgid(0), "refusing to treat our own group leader as a child"


class _ScriptedModel:
    """A ModelProvider that replays scripted turns and records cancel() calls."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.calls = 0
        self.cancels = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        self.cancels += 1
        return "acked"


class _CountingTool(ToolProvider):
    """A tool that records every dispatch, so "no further call" is a COUNT."""

    def __init__(self, name: str = "count_me") -> None:
        self._name = name
        self.dispatches: list[dict] = []

    @property
    def name(self) -> str:
        return "counter"

    @property
    def display_name(self) -> str:
        return "Counter"

    async def list_tools(self):
        return [ToolDefinition(name=self._name, description="d", parameters={"type": "object"})]

    async def invoke(self, tool_name, arguments):
        self.dispatches.append(dict(arguments))
        return ToolResult(success=True, output="counted")


def _defn() -> AgentRuntimeDefinition:
    return AgentRuntimeDefinition(name="T", provider="native", model="scripted")


def _bash_call(cid: str, command: str) -> AgentEvent:
    import json

    return AgentEvent(
        kind=EVENT_TOOL_CALL,
        tool_call_id=cid,
        title="bash",
        tool_input=json.dumps({"command": command, "timeout": 120}),
    )


class _WavePlanTool(ToolProvider):
    """Two tools chosen to make HC-6's dispatch PLAN wide rather than serial.

    ``blocker`` is unclassified, so ``dispatch_plan`` gives it EVERYTHING and it lands
    alone in wave 1 — and it parks there until released, which is what lets a stop arrive
    mid-batch. ``read_file`` IS classified (it reads exactly the path in its arguments), so
    three of them on three different paths are provably disjoint and share wave 2.
    """

    def __init__(self) -> None:
        self.dispatches: list[str] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def name(self) -> str:
        return "waveplan"

    @property
    def display_name(self) -> str:
        return "WavePlan"

    async def list_tools(self):
        return [
            ToolDefinition(name="blocker", description="d", parameters={"type": "object"}),
            ToolDefinition(name="read_file", description="d", parameters={"type": "object"}),
        ]

    async def invoke(self, tool_name, arguments):
        self.dispatches.append(tool_name)
        if tool_name == "blocker":
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), 20)
        return ToolResult(success=True, output="ok")


def _count_call(cid: str) -> AgentEvent:
    return AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=cid, title="count_me", tool_input="{}")


def _read_call(cid: str, path: str) -> AgentEvent:
    import json

    return AgentEvent(
        kind=EVENT_TOOL_CALL,
        tool_call_id=cid,
        title="read_file",
        tool_input=json.dumps({"path": path}),
    )


async def _build_runtime(tmp_path, turns, counter: _CountingTool | None = None):
    tools: list[ToolProvider] = [NativeBuiltinToolProvider(tmp_path, sandbox_mode="none")]
    if counter is not None:
        tools.append(counter)
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=_ScriptedModel(turns),
        tool_providers=tools,
        cwd=tmp_path,
    )
    await rt.start()
    # Auto-approve: this file is about what a STOP reaches, so the approval gate must
    # not park the turn before the child it is meant to reap ever spawns.
    rt.set_approval_policy("auto")
    return rt


class _Driver:
    """Drive ``rt.stream()`` in a task so a stop can arrive mid-turn."""

    def __init__(self, rt: NativeAgentRuntime, message: str = "go") -> None:
        self._rt = rt
        self._message = message
        self.events: list[AgentEvent] = []
        self.task: asyncio.Task | None = None

    async def __aenter__(self) -> "_Driver":
        async def _run() -> None:
            async for ev in self._rt.stream(self._message):
                self.events.append(ev)

        self.task = asyncio.create_task(_run())
        return self

    async def __aexit__(self, *exc) -> None:
        assert self.task is not None
        if not self.task.done():
            self.task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.gather(self.task, return_exceptions=True), 20)
        # Never leave a fixture child behind, whatever the assertions did.
        with contextlib.suppress(Exception):
            await self._rt._cancel.reap_children()

    async def finish(self, timeout: float = 20.0) -> list[AgentEvent]:
        assert self.task is not None
        await asyncio.wait_for(self.task, timeout)
        return self.events

    @property
    def terminal(self) -> AgentEvent:
        done = [e for e in self.events if e.kind == EVENT_COMPLETE]
        assert done, "the turn produced no terminal EVENT_COMPLETE"
        return done[-1]


# ── the primitive ─────────────────────────────────────────────────────────────


class TestCancelScopeIsOneSignal:
    def test_a_stop_with_no_turn_in_flight_is_a_no_op(self):
        scope = CancelScope()
        assert scope.request() == REQUEST_NO_TURN
        assert scope.cancelled is False  # nothing was in flight to cancel

    def test_a_stop_after_the_turn_finished_is_a_no_op_not_a_failure(self):
        scope = CancelScope()
        scope.begin_turn()
        scope.end_turn()
        assert scope.request() == REQUEST_NO_TURN

    def test_a_second_stop_is_a_repeat_not_a_raise_or_a_second_record(self):
        scope = CancelScope()
        scope.begin_turn()
        assert scope.request(CANCEL_USER) == REQUEST_FIRST
        assert scope.request(CANCEL_USER) == REQUEST_REPEAT
        assert scope.request(CANCEL_USER) == REQUEST_REPEAT
        assert scope.report.reason == CANCEL_USER

    def test_the_cause_survives_the_turn_ending(self):
        """end_turn() must not erase the reason: the terminal event and the stop card
        both read it AFTER the turn is over."""
        scope = CancelScope()
        scope.begin_turn()
        scope.request(CANCEL_USER)
        scope.end_turn()
        assert scope.stopped_by_user is True
        assert scope.report.reason == CANCEL_USER

    def test_an_internal_give_up_is_not_a_user_stop(self):
        scope = CancelScope()
        scope.begin_turn()
        scope.request(CANCEL_INTERNAL)
        assert scope.cancelled is True
        assert scope.stopped_by_user is False

    def test_a_new_turn_rearms_the_signal(self):
        scope = CancelScope()
        scope.begin_turn()
        scope.request(CANCEL_USER)
        scope.begin_turn()
        assert scope.cancelled is False
        assert scope.report.reason == ""


class TestStopReasonVocabulary:
    def test_stopped_by_user_is_a_distinct_value_in_the_cancelled_family(self):
        assert STOP_REASON_STOPPED_BY_USER != STOP_REASON_CANCELLED
        assert is_cancelled_stop(STOP_REASON_STOPPED_BY_USER)
        assert is_cancelled_stop(STOP_REASON_CANCELLED)

    def test_it_is_neither_an_error_nor_a_normal_completion(self):
        assert not is_cancelled_stop("end_turn")
        assert not is_cancelled_stop("max_turns")
        assert not is_cancelled_stop("error: agent died")
        assert not is_cancelled_stop(None)

    def test_the_sdk_names_the_new_outcome(self):
        """An app reading a turn outcome must be able to NAME the new value, or it
        will re-derive the family locally and drift."""
        from personalclaw.sdk import channel

        assert channel.STOP_REASON_STOPPED_BY_USER == STOP_REASON_STOPPED_BY_USER
        assert channel.is_cancelled_stop(STOP_REASON_STOPPED_BY_USER)


# ── the real driven stop ──────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestARealDrivenStop:
    async def test_no_child_process_survives_the_stop(self, tmp_path):
        """The load-bearing test. A real bash child AND a real grandchild, a real
        stop, then the OS is polled for both.

        The grandchild is the point: ``bash -lc`` is a shell, so a stop that signals
        only the shell's pid leaves the actual work running — an orphan still holding
        whatever lock or file handle the user pressed stop to release.
        """
        pidfile = tmp_path / "grandchild.pid"
        command = f"sleep 300 & echo $! > {pidfile}; sleep 300"
        rt = await _build_runtime(
            tmp_path,
            [[_bash_call("c1", command), AgentEvent(kind=EVENT_COMPLETE)]],
        )
        async with _Driver(rt) as driver:
            pids = await _wait_for(
                lambda: list(rt._cancel._children), _APPEAR_TIMEOUT, "the bash child"
            )
            assert len(pids) == 1
            child = pids[0]
            _assert_ours(child)
            grandchild = int(
                (
                    await _wait_for(
                        lambda: pidfile.read_text().strip() if pidfile.exists() else "",
                        _APPEAR_TIMEOUT,
                        "the grandchild pid file",
                    )
                )
            )
            _assert_ours(grandchild)
            assert _pid_alive(child), "the child should be running before the stop"
            assert _pid_alive(grandchild), "the grandchild should be running before the stop"

            assert await rt.cancel() == "acked"

            # Poll for the OS to report BOTH gone — reaped, not merely signalled.
            await _wait_for(lambda: not _pid_alive(child), _DEATH_TIMEOUT, "the child to be reaped")
            await _wait_for(
                lambda: not _pid_alive(grandchild),
                _DEATH_TIMEOUT,
                "the grandchild to be reaped",
            )
            await driver.finish()

        assert rt.last_stop_report()["children_reaped"] == 1
        assert rt.last_stop_report()["children_escaped"] == 0

    async def test_no_further_tool_call_is_dispatched_after_the_signal(self, tmp_path):
        """A batch of four calls: one long bash, then three counted ones. The stop
        lands while bash runs, so the count must FREEZE at zero."""
        counter = _CountingTool()
        rt = await _build_runtime(
            tmp_path,
            [
                [
                    _bash_call("c1", "sleep 300"),
                    _count_call("c2"),
                    _count_call("c3"),
                    _count_call("c4"),
                    AgentEvent(kind=EVENT_COMPLETE),
                ]
            ],
            counter=counter,
        )
        async with _Driver(rt) as driver:
            await _wait_for(lambda: list(rt._cancel._children), _APPEAR_TIMEOUT, "the bash child")
            dispatched_before = len(counter.dispatches)
            await rt.cancel()
            await driver.finish()

        assert dispatched_before == 0
        assert counter.dispatches == [], (
            "queued-but-unstarted calls must be dropped WITHOUT executing; "
            f"{len(counter.dispatches)} ran after the signal"
        )
        # Each dropped call is still answered, or the next turn's history replay breaks.
        dropped = [
            e
            for e in driver.events
            if e.kind == EVENT_TOOL_RESULT and "cancelled before this tool ran" in e.tool_output
        ]
        assert len(dropped) == 3
        assert rt.last_stop_report()["tool_calls_dropped"] == 3
        # And the history is well-formed: every tool_call has a paired result.
        called = sum(1 for m in rt._messages for _ in (m.get("tool_calls") or []))
        results = sum(1 for m in rt._messages if m.get("role") == "tool")
        assert called == results == 4

    async def test_a_stop_in_one_wave_drops_the_calls_in_every_later_wave(self, tmp_path, caplog):
        """The same property on HC-6's WAVE dispatcher, which is where it now lives.

        The test above is a batch the planner serializes (unclassified tools each land in
        a wave of one), so it cannot tell a per-call check apart from a per-WAVE one. This
        one makes the plan genuinely wide: one unclassified blocker alone in wave 1, then
        three ``read_file`` calls on three different paths — which ``dispatch_plan`` knows
        are disjoint — sharing wave 2. The stop lands while wave 1 blocks, so wave 2 must
        be dropped ENTIRELY: three calls that were queued, concurrent with each other, and
        never dispatched.
        """
        tool = _WavePlanTool()
        rt = NativeAgentRuntime(
            definition=_defn(),
            model_provider=_ScriptedModel(
                [
                    [
                        AgentEvent(
                            kind=EVENT_TOOL_CALL,
                            tool_call_id="w1",
                            title="blocker",
                            tool_input="{}",
                        ),
                        _read_call("w2", "a.txt"),
                        _read_call("w3", "b.txt"),
                        _read_call("w4", "c.txt"),
                        AgentEvent(kind=EVENT_COMPLETE),
                    ]
                ]
            ),
            tool_providers=[tool],
            cwd=tmp_path,
        )
        await rt.start()
        rt.set_approval_policy("auto")

        with caplog.at_level(logging.INFO, logger="personalclaw.agents.native.runtime"):
            async with _Driver(rt) as driver:
                await _wait_for(lambda: tool.entered.is_set(), _APPEAR_TIMEOUT, "the blocker")
                await rt.cancel()
                tool.release.set()
                await driver.finish()

        # The shipped timing line is the evidence that the wave really was wide — HC-1's
        # contract: read the line production emits, don't keep a second stopwatch.
        timing = [r.getMessage() for r in caplog.records if r.getMessage().startswith("tool batch")]
        assert timing, "the batch dispatcher's timing line did not ship"
        assert "waves=2" in timing[-1] and "widest=3" in timing[-1], timing[-1]

        assert tool.dispatches == ["blocker"], (
            "queued calls in a LATER wave must be dropped WITHOUT executing; "
            f"{tool.dispatches} ran"
        )
        dropped = [
            e
            for e in driver.events
            if e.kind == EVENT_TOOL_RESULT and "cancelled before this tool ran" in e.tool_output
        ]
        assert len(dropped) == 3
        assert rt.last_stop_report()["tool_calls_dropped"] == 3
        # A call that never ran never claimed on screen that it did.
        cards = [e for e in driver.events if e.kind == EVENT_TOOL_CALL]
        assert len(cards) == 1, f"a dropped call must not emit a tool-call card: {cards}"
        # History still well-formed for the next inference.
        called = sum(1 for m in rt._messages for _ in (m.get("tool_calls") or []))
        results = sum(1 for m in rt._messages if m.get("role") == "tool")
        assert called == results == 4

    async def test_the_in_flight_model_request_is_aborted_not_awaited(self, tmp_path):
        """A stop must reach the PROVIDER, not just stop reading from it."""
        rt = await _build_runtime(
            tmp_path,
            [[_bash_call("c1", "sleep 300"), AgentEvent(kind=EVENT_COMPLETE)]],
        )
        async with _Driver(rt) as driver:
            await _wait_for(lambda: list(rt._cancel._children), _APPEAR_TIMEOUT, "the bash child")
            assert rt._model.cancels == 0
            await rt.cancel()
            await driver.finish()

        assert rt._model.cancels == 1, "cancel() must propagate to the model provider"
        assert rt.last_stop_report()["model_request_aborted"] is True

    async def test_the_turn_ends_with_an_explicit_stopped_by_user_outcome(self, tmp_path):
        rt = await _build_runtime(
            tmp_path,
            [[_bash_call("c1", "sleep 300"), AgentEvent(kind=EVENT_COMPLETE)]],
        )
        async with _Driver(rt) as driver:
            await _wait_for(lambda: list(rt._cancel._children), _APPEAR_TIMEOUT, "the bash child")
            await rt.cancel()
            events = await driver.finish()

        terminal = [e for e in events if e.kind == EVENT_COMPLETE][-1]
        assert terminal.stop_reason == STOP_REASON_STOPPED_BY_USER
        assert (
            terminal.stop_reason != STOP_REASON_CANCELLED
        ), "a user stop must be distinguishable from an internal give-up"
        assert not terminal.stop_reason.startswith("error")

    async def test_an_internal_give_up_still_reads_as_cancelled_not_stopped_by_user(self, tmp_path):
        """The other side of the distinction: a shutdown is not a user stop."""
        rt = await _build_runtime(
            tmp_path,
            [[_bash_call("c1", "sleep 300"), AgentEvent(kind=EVENT_COMPLETE)]],
        )
        async with _Driver(rt) as driver:
            await _wait_for(lambda: list(rt._cancel._children), _APPEAR_TIMEOUT, "the bash child")
            await rt.shutdown()
            await rt._cancel.reap_children()  # shutdown() does not own the tool children
            events = await driver.finish()

        terminal = [e for e in events if e.kind == EVENT_COMPLETE][-1]
        assert terminal.stop_reason == STOP_REASON_CANCELLED
        assert terminal.stop_reason != STOP_REASON_STOPPED_BY_USER

    async def test_a_stop_is_idempotent_and_does_not_double_record(self, tmp_path):
        """Twice does not raise and does not double-record.

        The ANSWER to a repeat press is deliberately not pinned to one value: once the
        first stop lands, the turn is winding down, so a second press legitimately sees
        either a still-active turn ("acked") or an already-finished one ("no_turn")
        depending on how fast the child died. Pinning "acked" here made this test a
        stopwatch — it failed under load for the RIGHT behaviour. What must hold either
        way is the invariant: nothing raises, nothing is killed twice, nothing is
        recorded twice. The deterministic proof of the "acked" repeat branch is
        ``test_a_repeat_press_on_a_pinned_turn_is_acked`` below.
        """
        rt = await _build_runtime(
            tmp_path,
            [[_bash_call("c1", "sleep 300"), AgentEvent(kind=EVENT_COMPLETE)]],
        )
        async with _Driver(rt) as driver:
            pids = await _wait_for(
                lambda: list(rt._cancel._children), _APPEAR_TIMEOUT, "the bash child"
            )
            _assert_ours(pids[0])
            assert await rt.cancel() == "acked"
            first = dict(rt.last_stop_report())
            assert first["children_reaped"] == 1
            for _ in range(2):
                assert await rt.cancel() in ("acked", "no_turn")
            assert rt.last_stop_report() == first, "a repeat press must not re-record"
            assert rt._model.cancels == 1, "a repeat press must not re-abort the request"
            await driver.finish()

    async def test_a_repeat_press_on_a_pinned_turn_is_acked(self, tmp_path):
        """The "acked" repeat branch, deterministically.

        The turn is PINNED inside a tool that will not return until released, so the
        turn cannot finish between the two presses and the answer cannot race.
        """
        release = asyncio.Event()

        class _BlockingTool(_CountingTool):
            async def invoke(self, tool_name, arguments):
                self.dispatches.append(dict(arguments))
                await release.wait()
                return ToolResult(success=True, output="released")

        tool = _BlockingTool()
        rt = await _build_runtime(
            tmp_path, [[_count_call("c1"), AgentEvent(kind=EVENT_COMPLETE)]], counter=tool
        )
        async with _Driver(rt) as driver:
            await _wait_for(lambda: tool.dispatches, _APPEAR_TIMEOUT, "the tool to be entered")
            assert await rt.cancel() == "acked"
            assert await rt.cancel() == "acked", "the turn is still in flight — still acked"
            assert await rt.cancel() == "acked"
            assert rt._model.cancels == 1
            release.set()
            await driver.finish()

    async def test_a_stop_after_the_turn_finished_is_a_no_op(self, tmp_path):
        """ "No-op, not a failure" — and specifically not "acked".

        Reporting "acked" here is what made ``stop_turn`` set
        ``prev_turn_cancelled`` on a session whose turn had ALREADY completed, so the
        next turn opened with a bogus cancelled-turn preamble.
        """
        rt = await _build_runtime(
            tmp_path,
            [
                [
                    AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"),
                    AgentEvent(kind=EVENT_COMPLETE, input_tokens=5, output_tokens=2),
                ]
            ],
        )
        events = [ev async for ev in rt.stream("go")]
        assert events[-1].stop_reason == "end_turn"

        assert await rt.cancel() == "no_turn"
        assert await rt.cancel() == "no_turn"  # still a no-op, still not a raise
        assert rt._model.cancels == 0, "a no-op stop must not abort a request that isn't there"

    async def test_spend_before_the_stop_is_attributed_not_dropped(self, tmp_path):
        """A stop between ReAct cycles used to report zero tokens for a turn that had
        already burned two inferences. A stop that hides spend is why users distrust
        the button."""
        counter = _CountingTool()
        rt = await _build_runtime(
            tmp_path,
            [
                # cycle 1: a cheap tool call, real usage reported
                [
                    _count_call("c1"),
                    AgentEvent(
                        kind=EVENT_COMPLETE, input_tokens=100, output_tokens=40, cost_usd=0.01
                    ),
                ],
                # cycle 2: the long bash the stop lands on, more usage
                [
                    _bash_call("c2", "sleep 300"),
                    AgentEvent(
                        kind=EVENT_COMPLETE, input_tokens=70, output_tokens=30, cost_usd=0.02
                    ),
                ],
            ],
            counter=counter,
        )
        async with _Driver(rt) as driver:
            await _wait_for(lambda: list(rt._cancel._children), _APPEAR_TIMEOUT, "the bash child")
            await rt.cancel()
            events = await driver.finish()

        terminal = [e for e in events if e.kind == EVENT_COMPLETE][-1]
        assert terminal.stop_reason == STOP_REASON_STOPPED_BY_USER
        assert terminal.input_tokens == 170, "both cycles' input tokens must be attributed"
        assert terminal.output_tokens == 70
        assert terminal.cost_usd == pytest.approx(0.03)

    async def test_the_stopped_turns_spend_reaches_the_usage_ledger(self, tmp_path, monkeypatch):
        """End of the attribution chain: the row the cost surfaces read.

        Driven through the same seam the chat write-site uses
        (``usage_ledger.record_from_event``) rather than asserting on the event again,
        because "attributed" means a durable row exists, not that a field was set.
        """
        from personalclaw import usage_ledger
        from personalclaw.config import loader

        # `usage_ledger._path()` imports config_dir per call, so patching it on the
        # loader really redirects the write — the real home is never touched.
        home = tmp_path / "home"
        monkeypatch.setattr(loader, "config_dir", lambda: home)
        assert usage_ledger._path().is_relative_to(home), "the ledger must write under tmp_path"

        rt = await _build_runtime(
            tmp_path,
            [
                [
                    _bash_call("c1", "sleep 300"),
                    AgentEvent(
                        kind=EVENT_COMPLETE, input_tokens=90, output_tokens=25, cost_usd=0.05
                    ),
                ]
            ],
        )
        async with _Driver(rt) as driver:
            await _wait_for(lambda: list(rt._cancel._children), _APPEAR_TIMEOUT, "the bash child")
            await rt.cancel()
            events = await driver.finish()

        terminal = [e for e in events if e.kind == EVENT_COMPLETE][-1]
        usage_ledger.record_from_event(
            terminal,
            source="chat",
            session_key="dashboard:stoptest",
            agent="t",
            provider="scripted",
            model="scripted",
            estimate_if_missing=False,
        )
        spend = usage_ledger.totals(session_key="dashboard:stoptest")
        assert spend["turns"] == 1, "the stopped turn must appear in the usage ledger"
        assert spend["input_tokens"] == 90
        assert spend["output_tokens"] == 25


# ── the kill path's own safety ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestTheKillPathIsSafe:
    async def test_it_terminates_AND_reaps(self, tmp_path):
        proc = await asyncio.create_subprocess_exec(
            "sleep", "300", start_new_session=True, stdout=asyncio.subprocess.DEVNULL
        )
        _assert_ours(proc.pid)
        assert await cancellation.terminate_and_reap(proc) is True
        assert proc.returncode is not None, "reaped means wait() completed, not just signalled"
        await _wait_for(lambda: not _pid_alive(proc.pid), _DEATH_TIMEOUT, "the child to be gone")

    async def test_it_kills_a_child_that_ignores_sigterm(self, tmp_path):
        """SIGTERM then SIGKILL. A child trapping TERM must still die."""
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            "trap '' TERM; sleep 300",
            start_new_session=True,
            stdout=asyncio.subprocess.DEVNULL,
        )
        _assert_ours(proc.pid)
        assert await cancellation.terminate_and_reap(proc, grace=0.3) is True
        assert proc.returncode is not None
        await _wait_for(lambda: not _pid_alive(proc.pid), _DEATH_TIMEOUT, "the trapping child")

    async def test_an_already_exited_child_is_still_reaped(self):
        proc = await asyncio.create_subprocess_exec("true", stdout=asyncio.subprocess.DEVNULL)
        await asyncio.sleep(0.1)
        assert await cancellation.terminate_and_reap(proc) is True
        assert proc.returncode is not None

    async def test_a_child_that_finished_on_its_own_is_untracked(self):
        scope = CancelScope()
        scope.begin_turn()
        proc = await asyncio.create_subprocess_exec("true", stdout=asyncio.subprocess.DEVNULL)
        token = cancellation.bind_scope(scope)
        try:
            with cancellation.track_child(proc):
                await proc.wait()
                assert scope.child_count == 1
            assert scope.child_count == 0, "the tracker must untrack on the way out"
        finally:
            cancellation.reset_scope(token)


class TestTheKillPathRefusesWhatItDidNotSpawn:
    """Sync guards on the signal target. Deliberately NOT asyncio-marked: nothing here
    needs a loop, and the mark would only add a warning."""

    def test_it_refuses_to_signal_a_group_it_did_not_lead(self, monkeypatch):
        """The rail that keeps a kill inside the fixture.

        A child sharing OUR process group must never be reached by ``killpg`` — that
        signal set includes the gateway. Such a pid gets a single-pid terminate.
        """
        calls: list[tuple] = []
        monkeypatch.setattr(cancellation.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
        monkeypatch.setattr(cancellation, "_is_group_leader", lambda pid: False)

        class _Fake:
            pid = 999999

            def terminate(self):
                calls.append(("terminate", self.pid))

            def kill(self):
                calls.append(("kill", self.pid))

        cancellation._signal_child(_Fake(), signal.SIGTERM)
        assert calls == [("terminate", 999999)], "a non-leader must not be killpg'd"

    def test_it_never_signals_pid_zero_or_one(self, monkeypatch):
        """``killpg(0, …)`` means "my own group" — suicide. Guarded structurally."""
        calls: list[tuple] = []
        monkeypatch.setattr(cancellation.os, "killpg", lambda pid, sig: calls.append((pid, sig)))

        class _Fake:
            def __init__(self, pid):
                self.pid = pid

            def terminate(self):
                calls.append(("terminate", self.pid))

            def kill(self):
                calls.append(("kill", self.pid))

        for bad in (0, 1):
            cancellation._signal_child(_Fake(bad), signal.SIGKILL)
        assert calls == []

    def test_track_child_outside_a_turn_is_a_no_op(self):
        """The helper is safe at every spawn site, including ones no turn drives."""

        class _Fake:
            pid = 4242

        assert cancellation.current_scope() is None
        with cancellation.track_child(_Fake()):
            pass


# ── the spawn site declares its own group ─────────────────────────────────────


def test_the_bash_tool_spawns_into_its_own_process_group():
    """Structural, and load-bearing for the kill path.

    ``start_new_session=True`` is what makes the child a group leader, which is the
    ONLY condition under which ``_signal_child`` will killpg it. Drop it and the stop
    silently degrades to a single-pid terminate that leaves grandchildren running —
    a regression no unit test on the scope itself would notice.
    """
    import ast
    import inspect

    from personalclaw.agents.native import builtin_tools

    src = inspect.getsource(builtin_tools.NativeBuiltinToolProvider._t_bash)
    tree = ast.parse(src.lstrip() if src.startswith(" ") else src)
    spawns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_subprocess_limited"
    ]
    assert len(spawns) == 1
    kwargs = {kw.arg: kw.value for kw in spawns[0].keywords}
    assert "start_new_session" in kwargs, "the bash child must lead its own process group"
    assert isinstance(kwargs["start_new_session"], ast.Constant)
    assert kwargs["start_new_session"].value is True


def test_the_wave_loop_checks_the_signal_before_each_call():
    """Structural guard on the queued-call drop, at the seam HC-6 moved it to.

    The check has to be INSIDE the per-call dispatch decision. Two weaker placements
    both pass every behavioural test that stops BETWEEN batches:

    * only before the batch (which is what main had, at the
      ``if not tool_calls or self._cancelled`` exit) — a mid-batch stop runs the rest;
    * only in ``_execute_tool_batch``, around or before the wave loop — a stop that lands
      while wave 1 runs still executes waves 2..n.

    ``_execute_wave`` is entered once per wave and owns every dispatch site, so the check
    belonging to it is what makes "no further dispatch" hold for the whole batch.
    """
    import inspect
    import re

    from personalclaw.agents.native.runtime import NativeAgentRuntime

    src = inspect.getsource(NativeAgentRuntime._execute_wave)
    # Every site in this method that can hand a call to an invocation, in source order.
    dispatch_sites = [
        m.start()
        for m in re.finditer(r"self\._(?:run_tool|prefetch)\(", src)
        # …except the one that IS the drop (it runs nothing).
        if "_drop_queued_call" not in src[max(0, m.start() - 200) : m.start()]
    ]
    assert dispatch_sites, "the wave loop must still dispatch something"
    checks = [m.start() for m in re.finditer(r"if self\._cancelled:", src)] + [
        m.start() for m in re.finditer(r"self\._cancelled\)", src)
    ]
    assert checks, "the wave loop must check the stop signal"
    assert min(checks) < min(
        dispatch_sites
    ), "the stop check must precede the first dispatch site in _execute_wave"
    # And it must NOT have been hoisted up to the batch, where it would only fire once.
    batch_src = inspect.getsource(NativeAgentRuntime._execute_tool_batch)
    assert "_cancelled" not in batch_src, (
        "a check in _execute_tool_batch fires once for the whole batch; it belongs in "
        "_execute_wave, which is entered per wave"
    )


def test_cancelled_is_a_read_only_view_of_the_one_scope():
    """One notion of "cancelled", not two.

    A plain ``self._cancelled = True`` is what let a circuit-breaker trip and a user's
    stop become indistinguishable. Keeping the attribute read-only forces every writer
    to name a cause.
    """
    from personalclaw.agents.native.runtime import NativeAgentRuntime

    assert isinstance(NativeAgentRuntime.__dict__["_cancelled"], property)
    assert NativeAgentRuntime.__dict__["_cancelled"].fset is None


# ── the subagent layer ────────────────────────────────────────────────────────


def _sub_sessions():
    """A SessionManager-shaped double whose reset() records the keys it killed."""
    from unittest.mock import AsyncMock, MagicMock

    sessions = MagicMock()
    sessions.reset = AsyncMock()
    sessions.release = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock()
    return sessions


def _sub_ctx():
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("m", None))
    ctx.hooks.auto_approve_subagent_spawn = True
    return ctx


def _live_child(mgr, agent_id: str, parent: str, *, queued: bool = False):
    """Register a live SubagentInfo directly — the state a mid-fan-out stop finds."""
    from personalclaw.subagent import SubagentInfo

    info = SubagentInfo(id=agent_id, task="t", parent_session_key=parent)
    info.queued = queued
    mgr._agents[agent_id] = info
    if queued:
        mgr._queue.append(info)
    return info


@pytest.mark.asyncio
class TestASpawnedSubagentIsReachedByAStop:
    async def test_it_stops_running_and_queued_children_of_the_parent(self, monkeypatch):
        from personalclaw.subagent import SubagentManager

        sessions = _sub_sessions()
        mgr = SubagentManager(sessions=sessions, ctx_builder=_sub_ctx(), is_yolo=lambda: True)
        monkeypatch.setattr(mgr, "_write_tombstone", lambda info, cause: None)
        running = _live_child(mgr, "a1", "dashboard:main")
        queued = _live_child(mgr, "a2", "dashboard:main", queued=True)
        other = _live_child(mgr, "b1", "dashboard:other")

        assert await mgr.stop_children_of("dashboard:main") == 2

        assert running.done and running.cancelled
        assert queued.done and queued.cancelled
        assert queued not in mgr._queue, "a queued child must be dropped, not started"
        assert not other.done, "a stop must not reach another session's children"
        # The RUNNING one goes through the one kill path — session reset, not a flag.
        assert any(
            c.args and c.args[0] == "subagent:a1" for c in sessions.reset.await_args_list
        ), "the running child's session must actually be reset (killed), not just marked"

    async def test_a_second_stop_finds_nothing_to_stop(self, monkeypatch):
        from personalclaw.subagent import SubagentManager

        mgr = SubagentManager(
            sessions=_sub_sessions(), ctx_builder=_sub_ctx(), is_yolo=lambda: True
        )
        monkeypatch.setattr(mgr, "_write_tombstone", lambda info, cause: None)
        _live_child(mgr, "a1", "dashboard:main")
        assert await mgr.stop_children_of("dashboard:main") == 1
        assert await mgr.stop_children_of("dashboard:main") == 0

    async def test_a_stop_refuses_further_spawns_for_that_fanout(self, monkeypatch):
        """A spawn already in flight toward the queue must be refused, not started
        after the signal — reusing the existing fan-out stop rather than a new gate."""
        from personalclaw.subagent import SubagentManager

        mgr = SubagentManager(
            sessions=_sub_sessions(), ctx_builder=_sub_ctx(), is_yolo=lambda: True
        )
        monkeypatch.setattr(mgr, "_write_tombstone", lambda info, cause: None)
        _live_child(mgr, "a1", "dashboard:main")

        # Observed AT the kill, not after: `_maybe_clear_fanout` deliberately drops the
        # stop state once the lane is fully drained (so a LATER fan-out reusing the key
        # starts clean), which is why a post-hoc assertion on the dict is vacuous.
        seen: list[dict] = []
        real_cancel = mgr.cancel

        async def _spy(agent_id):
            seen.append(dict(mgr._fanout_stops))
            return await real_cancel(agent_id)

        monkeypatch.setattr(mgr, "cancel", _spy)
        await mgr.stop_children_of("dashboard:main")
        assert seen and "dashboard:main" in seen[0]
        assert seen[0]["dashboard:main"] == "parent turn stopped by user"

    async def test_an_empty_or_unknown_parent_is_a_no_op(self):
        from personalclaw.subagent import SubagentManager

        mgr = SubagentManager(
            sessions=_sub_sessions(), ctx_builder=_sub_ctx(), is_yolo=lambda: True
        )
        assert await mgr.stop_children_of("") == 0
        assert await mgr.stop_children_of("dashboard:nobody") == 0

    async def test_the_manager_registers_itself_with_the_session_manager(self):
        """The wiring, not just the method: an unregistered stopper is a closed gap
        that still looks closed from the subagent side."""
        from personalclaw.config.loader import AppConfig
        from personalclaw.session import SessionManager
        from personalclaw.subagent import SubagentManager

        sessions = SessionManager(AppConfig())
        assert sessions._stop_children is None
        mgr = SubagentManager(sessions=sessions, ctx_builder=_sub_ctx(), is_yolo=lambda: True)
        assert sessions._stop_children == mgr.stop_children_of


def _fake_session(provider):
    """The minimum a session needs for `stop_turn` — its queue, its cancelled set and
    its provider. Deliberately not a MagicMock: `prev_turn_cancelled` must be a real
    attribute so a test can assert the post-completion stop did NOT set it (a mock
    would answer truthy for anything and hide the bug)."""

    class _S:
        def __init__(self):
            self.queue: list = []
            self.cancelled: set = set()
            self.provider = provider
            self.prev_turn_cancelled = False

    return _S()


@pytest.mark.asyncio
class TestStopTurnReachesSpawnedWork:
    async def test_stop_turn_stops_the_sessions_spawned_children(self):
        from unittest.mock import AsyncMock

        from personalclaw.config.loader import AppConfig
        from personalclaw.session import SessionManager

        sessions = SessionManager(AppConfig())
        stopped: list[str] = []

        async def _stopper(key: str) -> int:
            stopped.append(key)
            return 2

        sessions.register_child_stopper(_stopper)

        provider = AsyncMock()
        provider.cancel = AsyncMock(return_value="acked")
        noted: list[int] = []
        provider.note_subagents_stopped = noted.append
        session = _fake_session(provider)
        sessions._sessions["dashboard:main"] = session  # type: ignore[assignment]

        outcome = await sessions.stop_turn("dashboard:main")

        assert outcome == "soft"
        assert stopped == ["dashboard:main"], "stop_turn must reach the spawned children"
        assert noted == [2], "the count must land on the turn's stop record"

    async def test_a_child_stopper_that_raises_does_not_block_the_parents_stop(self):
        """Fail-open: the user pressed stop on the PARENT."""
        from unittest.mock import AsyncMock

        from personalclaw.config.loader import AppConfig
        from personalclaw.session import SessionManager

        sessions = SessionManager(AppConfig())

        async def _boom(key: str) -> int:
            raise RuntimeError("child stop exploded")

        sessions.register_child_stopper(_boom)
        provider = AsyncMock()
        provider.cancel = AsyncMock(return_value="acked")
        session = _fake_session(provider)
        sessions._sessions["dashboard:main"] = session  # type: ignore[assignment]

        assert await sessions.stop_turn("dashboard:main") == "soft"

    async def test_a_post_completion_stop_leaves_the_next_turn_uncorrupted(self):
        """The whole point of "no_turn" rather than "acked".

        ``stop_turn`` sets ``prev_turn_cancelled`` on "acked", and the next turn opens
        with a cancelled-turn preamble when it is set. So a provider that lies about
        having stopped something makes the NEXT turn's context wrong.
        """
        from unittest.mock import AsyncMock

        from personalclaw.config.loader import AppConfig
        from personalclaw.session import SessionManager

        sessions = SessionManager(AppConfig())
        provider = AsyncMock()
        provider.cancel = AsyncMock(return_value="no_turn")  # the turn already finished
        session = _fake_session(provider)
        sessions._sessions["dashboard:main"] = session  # type: ignore[assignment]

        assert await sessions.stop_turn("dashboard:main") == "idle"
        assert (
            session.prev_turn_cancelled is False
        ), "a stop after the turn finished must not inject a cancelled-turn preamble"


def test_the_stop_card_reports_what_the_stop_reached():
    """Clause 4's record, at the surface that renders it (the shape PR2-13 consumes)."""
    from personalclaw.dashboard.chat_handlers import _stop_reach_report

    class _Prov:
        def last_stop_report(self):
            return {"reason": "user", "children_reaped": 2, "tool_calls_dropped": 3}

    session = type("_S", (), {"provider": _Prov()})()
    assert _stop_reach_report(session) == {
        "reason": "user",
        "children_reaped": 2,
        "tool_calls_dropped": 3,
    }


def test_a_provider_that_cannot_report_yields_nothing_rather_than_a_guess():
    from personalclaw.dashboard.chat_handlers import _stop_reach_report

    class _Boom:
        def last_stop_report(self):
            raise RuntimeError("no")

    assert _stop_reach_report(type("_S", (), {"provider": object()})()) == {}
    assert _stop_reach_report(type("_S", (), {"provider": _Boom()})()) == {}
    assert _stop_reach_report(type("_S", (), {})()) == {}
