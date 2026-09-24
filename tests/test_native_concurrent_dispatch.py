"""Concurrent tool dispatch in the native loop (HC-6).

**No test here asserts a duration.** A threshold on wall-clock measures the machine, and a
fixed sleep measures the skeleton — so overlap is proved two ways that are exact instead:

* an :class:`asyncio.Barrier` that only completes if the calls really are in flight
  together. Serial dispatch cannot satisfy it, so the *negative control* is a real assertion
  and not a comment: the same turn under ``max_tool_concurrency=1`` must time out.
* an interval LOG of ``("start"|"end", key)`` pairs. "Two reads overlap" is
  "something else appears between one read's start and its end"; "a write serializes" is
  "nothing appears between the write's start and its end". Both are statements about order,
  which is what the atom actually promises.
"""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from personalclaw.agents.native import dispatch_plan
from personalclaw.agents.native.approval import APPROVE
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

# A turn's requested calls, as (tool_name, json args).
Call = tuple[str, str]


class _ScriptedModel:
    """Asks for ``turn`` once, then makes a no-tool-call turn so the loop stops."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, turn: list[Call]) -> None:
        self._turn = turn
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        if self.calls == 1:
            for i, (name, args) in enumerate(self._turn):
                yield AgentEvent(
                    kind=EVENT_TOOL_CALL, tool_call_id=f"c{i}", title=name, tool_input=args
                )
        yield AgentEvent(kind=EVENT_COMPLETE)


class _IntervalTool(ToolProvider):
    """Filesystem-shaped tools that record when each invocation starts and ends.

    Named ``read_file``/``write_file``/``glob``/``grep`` on purpose: the reservation table
    is keyed on those real names, so this exercises the shipped classifier rather than a
    test-only vocabulary.
    """

    def __init__(
        self,
        *,
        rendezvous: int = 0,
        rendezvous_on: frozenset[str] = frozenset(),
        raise_on: frozenset[str] = frozenset(),
        gated: frozenset[str] = frozenset(),
    ) -> None:
        self.log: list[tuple[str, str]] = []
        self._barrier = asyncio.Barrier(rendezvous) if rendezvous else None
        self._rendezvous_on = rendezvous_on
        self._raise_on = raise_on
        self._gated = gated

    @property
    def name(self) -> str:
        return "mock-fs"

    @property
    def display_name(self) -> str:
        return "Mock FS"

    async def list_tools(self):
        def d(name: str, risk: RiskLevel) -> ToolDefinition:
            return ToolDefinition(
                name=name,
                description="d",
                parameters={"type": "object"},
                requires_approval=name in self._gated,
                risk_level=risk,
            )

        return [
            d("read_file", RiskLevel.SAFE),
            d("write_file", RiskLevel.CAUTION),
            d("glob", RiskLevel.SAFE),
            d("grep", RiskLevel.SAFE),
            # Declared so the turn can name it and land in the UNCLASSIFIED bucket for a
            # resource reason. Left ungated on purpose: with a gate it would run alone
            # because of the gate, and the test would prove nothing about classification.
            d("bash", RiskLevel.SAFE),
        ]

    async def invoke(self, tool_name, arguments):
        arg = str(arguments.get("path") or arguments.get("pattern") or arguments.get("query") or "")
        # Keyed by TOOL:ARG, not by arg alone, so a read and a write of one path are
        # distinguishable intervals — otherwise "the write intersected nothing" could not be
        # stated at all.
        key = f"{tool_name}:{arg}"
        self.log.append(("start", key))
        try:
            if key in self._raise_on:
                raise RuntimeError(f"boom on {arg}")
            if self._barrier is not None and key in self._rendezvous_on:
                # Completes only if every rendezvous member is in flight at the same time.
                await self._barrier.wait()
            else:
                # One scheduling point so a serial run still yields the event loop — without
                # it "serial" would be indistinguishable from "synchronous", and the overlap
                # assertions would pass for the wrong reason.
                await asyncio.sleep(0)
        finally:
            self.log.append(("end", key))
        return ToolResult(success=True, output=f"OUT:{arg}")


def _defn():
    return AgentRuntimeDefinition(name="T", provider="native", model="scripted")


async def _run(turn: list[Call], tool: _IntervalTool, *, concurrency: int = 8):
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=_ScriptedModel(turn),
        tool_providers=[tool],
        max_tool_concurrency=concurrency,
    )
    await rt.start()
    events = [ev async for ev in rt.stream("go")]
    return rt, events


def _intervals(log: list[tuple[str, str]]) -> list[tuple[str, int, int]]:
    """The log as ``(key, start_index, end_index)`` intervals.

    Pairs each ``end`` with the most recent unclosed ``start`` of the same key, so two
    simultaneous invocations of the SAME key still produce two intervals.
    """
    open_: dict[str, list[int]] = {}
    out: list[tuple[str, int, int]] = []
    for i, (phase, key) in enumerate(log):
        if phase == "start":
            open_.setdefault(key, []).append(i)
        else:
            assert open_.get(key), f"end without a start for {key}: {log}"
            out.append((key, open_[key].pop(), i))
    assert not any(open_.values()), f"unclosed interval in {log}"
    return out


def _intersect(a: tuple[str, int, int], b: tuple[str, int, int]) -> bool:
    """Do two intervals overlap in time? NESTING COUNTS — an interval entirely inside
    another is the most common shape of a real overlap, and the assertion that misses it
    ("something appeared between my start and my end") reads as a serial run."""
    return a[1] < b[2] and b[1] < a[2]


def _overlapping_pairs(log: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Every pair of intervals that overlapped, each pair sorted by key.

    Sorted rather than in log order on purpose: WHICH of two overlapping calls finishes
    first is scheduler weather, and an assertion that depends on it would be a flake. That
    they overlapped at all is the fact.
    """
    ivs = _intervals(log)
    return sorted(
        tuple(sorted((a[0], b[0])))  # type: ignore[misc]
        for i, a in enumerate(ivs)
        for b in ivs[i + 1 :]
        if _intersect(a, b)
    )


def _outputs(events: list[AgentEvent]) -> dict[str, str]:
    return {
        ev.tool_call_id or "": ev.tool_output or "" for ev in events if ev.kind == EVENT_TOOL_RESULT
    }


# ── overlap is real ──


@pytest.mark.asyncio
async def test_two_reads_of_different_files_are_in_flight_together():
    tool = _IntervalTool(
        rendezvous=2, rendezvous_on=frozenset({"read_file:a.py", "read_file:b.py"})
    )
    turn: list[Call] = [
        ("read_file", '{"path": "a.py"}'),
        ("read_file", '{"path": "b.py"}'),
    ]
    _rt, events = await asyncio.wait_for(_run(turn, tool), timeout=10)
    assert _overlapping_pairs(tool.log) == [("read_file:a.py", "read_file:b.py")]
    assert set(_outputs(events).values()) == {"OUT:a.py", "OUT:b.py"}


@pytest.mark.asyncio
async def test_the_rendezvous_cannot_be_satisfied_serially():
    """The negative control for the test above. Under ``max_tool_concurrency=1`` the two
    reads are in different waves, so the barrier can never fill and the turn hangs — which
    is exactly why the barrier is a valid overlap detector."""
    tool = _IntervalTool(
        rendezvous=2, rendezvous_on=frozenset({"read_file:a.py", "read_file:b.py"})
    )
    turn: list[Call] = [
        ("read_file", '{"path": "a.py"}'),
        ("read_file", '{"path": "b.py"}'),
    ]
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_run(turn, tool, concurrency=1), timeout=1.0)


@pytest.mark.asyncio
async def test_two_reads_of_the_SAME_file_are_in_flight_together():
    """Same path, both reading — the atom names this case explicitly."""
    tool = _IntervalTool(rendezvous=2, rendezvous_on=frozenset({"read_file:same.py"}))
    turn: list[Call] = [
        ("read_file", '{"path": "same.py"}'),
        ("read_file", '{"path": "same.py"}'),
    ]
    await asyncio.wait_for(_run(turn, tool), timeout=10)
    assert _overlapping_pairs(tool.log) == [("read_file:same.py", "read_file:same.py")]


# ── a write serializes against every reader and writer of its path ──


@pytest.mark.asyncio
async def test_a_write_intersects_nothing_while_its_readers_overlap_each_other():
    tool = _IntervalTool(
        rendezvous=2, rendezvous_on=frozenset({"read_file:a.py", "read_file:b.py"})
    )
    turn: list[Call] = [
        ("read_file", '{"path": "a.py"}'),
        ("read_file", '{"path": "b.py"}'),
        ("write_file", '{"path": "a.py"}'),
    ]
    await asyncio.wait_for(_run(turn, tool), timeout=10)
    # The two reads overlapped, and that is the ONLY overlap: the write's interval
    # intersects nothing, which is the reader/writer rule stated as an ordering fact.
    assert _overlapping_pairs(tool.log) == [("read_file:a.py", "read_file:b.py")]
    # …and it ran after both readers finished rather than before them.
    ivs = dict((k, (s, e)) for k, s, e in _intervals(tool.log))
    assert ivs["write_file:a.py"][0] > max(ivs["read_file:a.py"][1], ivs["read_file:b.py"][1])


@pytest.mark.asyncio
async def test_a_read_after_a_write_of_the_same_path_waits_for_it():
    tool = _IntervalTool()
    turn: list[Call] = [
        ("read_file", '{"path": "a.py"}'),
        ("write_file", '{"path": "a.py"}'),
        ("read_file", '{"path": "a.py"}'),
    ]
    await asyncio.wait_for(_run(turn, tool), timeout=10)
    # Three disjoint intervals in the requested order: nothing raced anything.
    assert _overlapping_pairs(tool.log) == []
    assert [k for k, _s, _e in _intervals(tool.log)] == [
        "read_file:a.py",
        "write_file:a.py",
        "read_file:a.py",
    ]


@pytest.mark.asyncio
async def test_a_glob_read_serializes_the_write_it_matches():
    """The pattern trap at the runtime level: normalize the glob into a path key and these
    two stop conflicting, so the write lands in the SAME wave as the read."""
    tool = _IntervalTool()
    turn: list[Call] = [
        ("glob", '{"pattern": "**/*.py"}'),
        ("write_file", '{"path": "pkg/mod.py"}'),
    ]
    await asyncio.wait_for(_run(turn, tool), timeout=10)
    assert _overlapping_pairs(tool.log) == []
    assert [k for k, _s, _e in _intervals(tool.log)] == ["glob:**/*.py", "write_file:pkg/mod.py"]


@pytest.mark.asyncio
async def test_an_unclassified_tool_runs_alone():
    tool = _IntervalTool()
    turn: list[Call] = [
        ("read_file", '{"path": "a.py"}'),
        ("bash", '{"command": "ls"}'),
        ("read_file", '{"path": "b.py"}'),
    ]
    rt, _events = await asyncio.wait_for(_run(turn, tool), timeout=10)
    prepped = [
        rt._prepare_call(
            AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=f"c{i}", title=n, tool_input=a)
        )
        for i, (n, a) in enumerate(turn)
    ]
    assert dispatch_plan.plan([p.reservations for p in prepped]).waves == ((0,), (1,), (2,))


# ── the audit trail of a concurrent turn matches the serial one ──


@pytest.mark.asyncio
async def test_a_concurrent_turn_emits_the_same_events_and_history_as_a_serial_one():
    turn: list[Call] = [
        ("read_file", '{"path": "a.py"}'),
        ("read_file", '{"path": "b.py"}'),
        ("grep", '{"query": "x", "glob": "doc/*.md"}'),
        ("read_file", '{"path": "c.py"}'),
    ]
    rt_ser, ser = await _run(turn, _IntervalTool(), concurrency=1)
    rt_con, con = await _run(turn, _IntervalTool(), concurrency=8)

    def trail(evs):
        return Counter((e.kind, e.tool_call_id, e.title) for e in evs)

    assert trail(con) == trail(ser)
    # Every call's card still precedes its own result…
    for i in range(len(turn)):
        cid = f"c{i}"
        kinds = [e.kind for e in con if e.tool_call_id == cid]
        assert kinds == [EVENT_TOOL_CALL, EVENT_TOOL_RESULT]
    # …and history is byte-identical, which is the only reason the next inference sees the
    # same conversation.
    assert rt_con._messages == rt_ser._messages


@pytest.mark.asyncio
async def test_the_dispatch_timing_line_reports_the_plan(caplog):
    from harness.tool_dispatch_bench import parse_timing_line

    turn: list[Call] = [
        ("read_file", '{"path": "a.py"}'),
        ("read_file", '{"path": "b.py"}'),
        ("write_file", '{"path": "a.py"}'),
    ]
    with caplog.at_level("INFO", logger="personalclaw.agents.native.runtime"):
        await _run(turn, _IntervalTool())
    rows = [r for r in (parse_timing_line(m) for m in caplog.messages) if r is not None]
    assert len(rows) == 1
    assert rows[0].mode == dispatch_plan.MODE_CONCURRENT
    assert (rows[0].calls, rows[0].waves, rows[0].widest) == (3, 2, 2)
    assert rows[0].ms >= 0


@pytest.mark.asyncio
async def test_the_timing_line_reports_serial_at_concurrency_one(caplog):
    from harness.tool_dispatch_bench import parse_timing_line

    turn: list[Call] = [
        ("read_file", '{"path": "a.py"}'),
        ("read_file", '{"path": "b.py"}'),
    ]
    with caplog.at_level("INFO", logger="personalclaw.agents.native.runtime"):
        await _run(turn, _IntervalTool(), concurrency=1)
    rows = [r for r in (parse_timing_line(m) for m in caplog.messages) if r is not None]
    assert [(r.mode, r.calls, r.waves, r.widest) for r in rows] == [
        (dispatch_plan.MODE_SERIAL, 2, 2, 1)
    ]


# ── failure semantics ──


@pytest.mark.asyncio
async def test_a_failure_spares_independent_siblings_but_stops_a_dependent_call():
    tool = _IntervalTool(raise_on=frozenset({"read_file:boom.py"}))
    turn: list[Call] = [
        ("read_file", '{"path": "ok1.py"}'),
        ("read_file", '{"path": "boom.py"}'),
        ("read_file", '{"path": "ok2.py"}'),
        ("write_file", '{"path": "boom.py"}'),  # DEPENDENT: same path as the failure
        ("read_file", '{"path": "other.py"}'),  # INDEPENDENT of it
    ]
    _rt, events = await asyncio.wait_for(_run(turn, tool), timeout=10)
    out = _outputs(events)
    # Siblings in the failing call's own wave still ran and still reported.
    assert out["c0"] == "OUT:ok1.py"
    assert out["c2"] == "OUT:ok2.py"
    # The raiser became an observation instead of taking the turn down.
    assert out["c1"].startswith("Error:") and "RuntimeError" in out["c1"]
    # The dependent call was NOT run…
    assert "was not run" in out["c3"]
    assert ("start", "read_file:boom.py") in tool.log
    assert ("start", "write_file:boom.py") not in tool.log
    # …while an independent later call was unaffected.
    assert out["c4"] == "OUT:other.py"


@pytest.mark.asyncio
async def test_every_call_still_gets_exactly_one_result_message_when_one_fails():
    """History pairing is what the next inference replays; an unpaired tool_call breaks
    every tool-using provider, so a raise must still produce a result message."""
    tool = _IntervalTool(raise_on=frozenset({"read_file:boom.py"}))
    turn: list[Call] = [
        ("read_file", '{"path": "ok.py"}'),
        ("read_file", '{"path": "boom.py"}'),
    ]
    rt, _events = await asyncio.wait_for(_run(turn, tool), timeout=10)
    tool_msgs = [m for m in rt._messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 2


# ── ordering-sensitive semantics are preserved ──


@pytest.mark.asyncio
async def test_an_approval_gated_call_reserves_everything_and_runs_alone():
    """A gate is a round trip to a human. Two prompts racing would change the order the
    user is asked in, so a gated call is planned as if it touched everything — which is also
    what keeps any pre-write admission gate looking at the serial world it was written for.
    """
    tool = _IntervalTool(gated=frozenset({"write_file"}))
    turn: list[Call] = [
        ("read_file", '{"path": "a.py"}'),
        ("write_file", '{"path": "z.py"}'),  # a DIFFERENT path — only the gate isolates it
        ("read_file", '{"path": "b.py"}'),
    ]
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=_ScriptedModel(turn),
        tool_providers=[tool],
        max_tool_concurrency=8,
    )
    await rt.start()
    prepped = [
        rt._prepare_call(
            AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=f"c{i}", title=n, tool_input=a)
        )
        for i, (n, a) in enumerate(turn)
    ]
    assert prepped[1].reservations == (dispatch_plan.EVERYTHING,)
    assert dispatch_plan.plan([p.reservations for p in prepped]).waves == ((0,), (1,), (2,))

    # …and the permission request still surfaces per call, exactly as it does serially.
    events: list[AgentEvent] = []

    async def drive():
        async for ev in rt.stream("go"):
            events.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                rt._approval.resolve(ev.request_id or "", APPROVE)

    await asyncio.wait_for(drive(), timeout=10)
    reqs = [e for e in events if e.kind == EVENT_PERMISSION_REQUEST]
    assert [e.tool_call_id for e in reqs] == ["c1"]
    assert _outputs(events)["c1"] == "OUT:z.py"
