"""``G7`` / AAP-8 — procedural memory must learn from ACP turns.

The measured gap (``O12``): a six-tool-call ACP turn produced **zero** procedural rows.
Root cause was a two-link break, and both links are pinned here:

1. ``drain_tool_outcomes`` existed on exactly ONE provider (the native runtime). The
   dashboard reads it duck-typed (``chat_runner.py``: ``getattr(provider,
   "drain_tool_outcomes", None)``), so on an ACP provider it returned ``None``,
   ``tool_outcomes`` stayed ``[]`` and ``record_procedural_outcomes`` was handed nothing.
2. ``acp/translate.py`` stamps the failure bit (``tool_meta = {"ok": False}``) but
   ``acp/adapter.py`` did not map ``tool_meta``, so the bit died one line after it was
   authored — taking the loop breaker's only failure signal with it (``G6``).

Every test here drives the REAL path — a JSON-RPC ``session/update`` frame through
``translate`` → ``AcpEvent`` → the provider's ``stream()`` → ``adapter`` → the
accumulator — rather than hand-building the event the production path is supposed to
produce. A hand-built ``AgentEvent(tool_meta={"ok": False})`` is exactly what let link 2
sit broken under a green suite.

Never touches the real home: the memory store is a ``tmp_path`` sqlite file.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from personalclaw.acp.adapter import acp_event_to_agent_event
from personalclaw.acp.outcomes import ToolOutcomeAccumulator
from personalclaw.acp.translate import extract_tool_event, extract_tool_update_events
from personalclaw.acp.types import (
    EVENT_COMPLETE,
    EVENT_TOOL_RESULT,
    AcpEvent,
    AcpPromptStats,
    JsonRpcMessage,
)
from personalclaw.llm.acp_session_provider import AcpSessionProvider
from personalclaw.memory_record import MemoryKind
from personalclaw.memory_service import PROCEDURAL_OUTCOMES, MemoryService
from personalclaw.vector_memory import VectorMemoryStore

# ── real ACP frames (what a CLI actually puts on the wire) ────────────────────


def _call_frame(call_id: str, title: str) -> JsonRpcMessage:
    return JsonRpcMessage(
        method="session/update",
        params={
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": call_id,
                "title": title,
                "kind": "read",
                "rawInput": {"path": f"/tmp/{call_id}.txt"},
            }
        },
    )


def _result_frame(call_id: str, status: str) -> JsonRpcMessage:
    return JsonRpcMessage(
        method="session/update",
        params={
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": call_id,
                "status": status,
                "content": [{"type": "content", "content": {"type": "text", "text": "out"}}],
            }
        },
    )


def _turn_events(calls: list[tuple[str, str, str]]) -> list[AcpEvent]:
    """Translate a whole turn's frames into AcpEvents. ``calls`` = (id, title, status)."""
    events: list[AcpEvent] = []
    inputs: dict[str, str] = {}
    for call_id, title, status in calls:
        call_event = extract_tool_event(_call_frame(call_id, title), inputs, [])
        assert call_event is not None, "translate did not decode the tool_call frame"
        events.append(call_event)
        events.extend(extract_tool_update_events(_result_frame(call_id, status), inputs))
    events.append(AcpEvent(kind=EVENT_COMPLETE, stop_reason="end_turn"))
    return events


# ── the provider under test, over a fake session that replays those frames ───


class _FakeSession:
    def __init__(self, turns: list[list[AcpEvent]]) -> None:
        self.session_id = "S1"
        self.last_prompt_stats = AcpPromptStats()
        self._turns = list(turns)
        self.turns_streamed = 0

    async def stream_events(self, message):
        events = self._turns[self.turns_streamed]
        self.turns_streamed += 1
        for e in events:
            yield e

    stream_command = stream_events


class _FakeConn:
    agent_capabilities: dict = {}
    supports_native_commands = True

    def is_process_alive(self):
        return True


def _provider(turns: list[list[AcpEvent]]) -> AcpSessionProvider:
    return AcpSessionProvider(
        _FakeConn(), _FakeSession(turns), runtime_id="acp:demo-cli", model="opus"
    )


async def _drive(provider: AcpSessionProvider, message: str = "go") -> None:
    async for _ in provider.stream(message):
        pass


@pytest.fixture
def svc(tmp_path):
    """Procedural memory over a tmp_path store — NEVER the real ~/.personalclaw."""
    vs = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    vs.init()
    vs.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return MemoryService.over_vector_store(vs)


def _state_for(svc):
    """Minimal dashboard state, same shape test_chat_runner_procedural_wiring.py uses."""
    memory = SimpleNamespace(vector_store=svc._vs)
    return SimpleNamespace(
        context_builder=SimpleNamespace(get_memory_for=lambda *_a, **_k: memory),
        broadcast_ws=lambda *_a, **_k: None,
    )


def _session():
    return SimpleNamespace(
        key="dashboard:chat-acp",
        workspace_dir=None,
        memory_store=None,
        _ephemeral=False,
    )


# ── link 2: the failure bit has to survive the adapter ───────────────────────


class TestFailureBitCrossesTheAdapter:
    """``acp_event_to_agent_event`` used to omit ``tool_meta`` entirely. Everything
    downstream then read the dataclass default ``{}``: the tool card could not colour a
    failure and ``chat_runner``'s ACP loop breaker (`_acp_failed = _tool_ok is False`)
    could never be True, so a fully implemented warn/block/circuit path was inert."""

    def test_failed_result_keeps_ok_false_through_translation(self):
        events = extract_tool_update_events(_result_frame("t1", "failed"), {})
        results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
        assert results, "translate produced no tool_result"
        assert acp_event_to_agent_event(results[0]).tool_meta.get("ok") is False

    def test_passing_result_carries_no_ok_key(self):
        """Vacuity floor: an adapter that stamped ``ok`` unconditionally would pass the
        test above while telling the breaker every call failed."""
        events = extract_tool_update_events(_result_frame("t1", "completed"), {})
        results = [e for e in events if e.kind == EVENT_TOOL_RESULT]
        assert results
        assert "ok" not in acp_event_to_agent_event(results[0]).tool_meta


# ── link 1: the hook, and the O12 reproduction ───────────────────────────────


class TestAcpTurnProducesProceduralRows:
    @pytest.mark.asyncio
    async def test_six_tool_call_turn_yields_six_outcomes(self):
        """``O12`` verbatim: six tool calls in one ACP turn. Before the fix the drain
        hook did not exist on any ACP provider, so this list was empty."""
        calls = [
            ("c1", "Read", "completed"),
            ("c2", "Bash", "failed"),
            ("c3", "Edit", "completed"),
            ("c4", "Grep", "failed"),
            ("c5", "Write", "completed"),
            ("c6", "Glob", "completed"),
        ]
        provider = _provider([_turn_events(calls)])
        await _drive(provider)
        outcomes = provider.drain_tool_outcomes()
        assert outcomes == [
            ("Read", "success"),
            ("Bash", "failed"),
            ("Edit", "success"),
            ("Grep", "failed"),
            ("Write", "success"),
            ("Glob", "success"),
        ]

    @pytest.mark.asyncio
    async def test_rows_land_in_procedural_memory(self, svc):
        """The end of the chain: the drained pairs actually become stored priors.
        Asserting on ROWS, not on the presence of a method — a hook that returned the
        right shape into a store that rejected it would still be zero rows."""
        from personalclaw import after_turn_review as atr

        provider = _provider(
            [_turn_events([("c1", "Read", "completed"), ("c2", "Bash", "failed")])]
        )
        await _drive(provider)
        n = atr.record_procedural_outcomes(svc, provider.drain_tool_outcomes())
        assert n == 2
        rows = svc.get_records(kinds={MemoryKind.PROCEDURAL.value})
        assert rows, "no procedural rows stored after a multi-tool ACP turn"
        texts = " ".join(r.text for r in rows)
        assert "Read" in texts and "Bash" in texts
        assert "success" in texts and "failed" in texts

    @pytest.mark.asyncio
    async def test_o12_reproduction_through_the_duck_typed_read(self, svc):
        """``O12`` end to end, read the way ``chat_runner.py`` reads it — the same
        ``getattr(provider, "drain_tool_outcomes", None)``, not a direct call. This is
        the test that reds on ZERO ROWS if the hook is ever removed from the ACP
        providers again, instead of failing on a missing attribute."""
        from personalclaw import after_turn_review as atr

        provider = _provider([_turn_events([(f"c{i}", "Read", "completed") for i in range(6)])])
        await _drive(provider)

        # verbatim chat_runner.py shape
        drain = getattr(provider, "drain_tool_outcomes", None)
        tool_outcomes: list[tuple[str, str]] = []
        if callable(drain):
            tool_outcomes = list(drain() or [])
        atr.record_procedural_outcomes(svc, tool_outcomes, scope_ref=None)

        rows = svc.get_records(kinds={MemoryKind.PROCEDURAL.value})
        assert rows, "O12: zero procedural rows after a six-tool-call ACP turn"

    @pytest.mark.asyncio
    async def test_every_emitted_outcome_is_in_the_closed_vocabulary(self):
        """One vocabulary. `record_procedural_outcomes` DROPS an unknown outcome, so a
        third spelling here would be silent zero-rows, not an error."""
        provider = _provider(
            [_turn_events([("c1", "Read", "completed"), ("c2", "Bash", "failed")])]
        )
        await _drive(provider)
        for _tool, outcome in provider.drain_tool_outcomes():
            assert outcome in PROCEDURAL_OUTCOMES

    @pytest.mark.asyncio
    async def test_slash_command_turn_also_accumulates(self):
        """`stream_command` is the second turn entry point; an accumulator wired to only
        one of them silently learns from half the turns."""
        provider = _provider([_turn_events([("c1", "Read", "failed")])])
        async for _ in provider.stream_command("/review"):
            pass
        assert provider.drain_tool_outcomes() == [("Read", "failed")]


# ── the two disciplines the drain contract rests on ──────────────────────────


class TestDrainOnceAndTurnBoundary:
    @pytest.mark.asyncio
    async def test_second_drain_is_empty(self):
        """``drain_*`` means read-and-clear (matching the native runtime). The dashboard
        relies on this: it drains ONCE and shares the one list between procedural memory
        and the self-model observer."""
        provider = _provider([_turn_events([("c1", "Read", "completed")])])
        await _drive(provider)
        assert provider.drain_tool_outcomes() == [("Read", "success")]
        assert provider.drain_tool_outcomes() == []

    @pytest.mark.asyncio
    async def test_one_turns_failures_do_not_leak_into_the_next(self):
        """The turn boundary. Turn 1 is NOT drained — exactly what happens when the
        learning gate decides the turn was not worthwhile and returns before the drain.
        Turn 2 must still be judged on its own tools only; an accumulator that never
        cleared would report turn 1's failure as turn 2's."""
        turn1 = _turn_events([("c1", "Bash", "failed")])
        turn2 = _turn_events([("c9", "Read", "completed")])
        provider = _provider([turn1, turn2])
        await _drive(provider, "first")
        # deliberately NOT drained
        await _drive(provider, "second")
        assert provider.drain_tool_outcomes() == [("Read", "success")]


# ── the second reader at chat_runner.py's self-model observer ────────────────


class TestSelfModelObserverStillSeesTheTools:
    """``chat_runner`` computes ``tools=tuple(sorted({t for t, _ in tool_outcomes}))`` and
    ``succeeded=all(outcome == "success" ...)`` from the SAME drained list. Breaking the
    drain-once discipline (a second drain in between) starves this consumer silently."""

    @pytest.mark.asyncio
    async def test_tools_and_succeeded_derive_from_the_one_drained_list(self):
        provider = _provider(
            [_turn_events([("c1", "Read", "completed"), ("c2", "Bash", "failed")])]
        )
        await _drive(provider)
        tool_outcomes = provider.drain_tool_outcomes()
        assert tuple(sorted({t for t, _o in tool_outcomes})) == ("Bash", "Read")
        assert all(o == "success" for _t, o in tool_outcomes) is False

    @pytest.mark.asyncio
    async def test_a_clean_turn_reads_as_succeeded(self):
        """Vacuity floor for the assertion above."""
        provider = _provider([_turn_events([("c1", "Read", "completed")])])
        await _drive(provider)
        tool_outcomes = provider.drain_tool_outcomes()
        assert all(o == "success" for _t, o in tool_outcomes) is True

    @pytest.mark.asyncio
    async def test_chat_runner_feeds_both_readers_from_one_drain(self, svc, monkeypatch):
        """The real ``_maybe_after_turn_review``, over a real ACP provider. BOTH consumers
        must be fed from the single drained list: procedural memory gets rows, AND the
        self-model observer gets the tool names. Adding a second ``drain()`` between them
        clears the accumulator and starves whichever reader comes second."""
        from personalclaw.dashboard import chat_runner as cr
        from personalclaw.learning import self_model_observer

        monkeypatch.setattr("personalclaw.memory_service.service_for", lambda _m: svc)
        seen: dict = {}
        monkeypatch.setattr(
            self_model_observer,
            "observe_turn",
            lambda _svc, **kw: seen.update(kw),
        )
        provider = _provider(
            [
                _turn_events(
                    [
                        ("c1", "Read", "completed"),
                        ("c2", "Bash", "failed"),
                        ("c3", "Edit", "completed"),
                        ("c4", "Grep", "completed"),
                    ]
                )
            ]
        )
        await _drive(provider)
        cr._maybe_after_turn_review(
            _state_for(svc),
            _session(),
            user_message="do the thing",
            assistant_text="done",
            tool_calls=4,
            provider=provider,
        )
        # reader 1 — procedural memory
        assert svc.get_records(kinds={MemoryKind.PROCEDURAL.value}), "procedural rows missing"
        # reader 2 — the self-model observer
        assert seen.get("tools") == ("Bash", "Edit", "Grep", "Read"), seen
        assert seen.get("succeeded") is False


# ── accumulator unit rails ───────────────────────────────────────────────────


class TestAccumulatorRails:
    def test_out_of_vocabulary_outcome_is_dropped_and_warned(self, monkeypatch, caplog):
        """A third spelling must never reach the store. Forced by patching the closed
        vocabulary to exclude ``success`` — the accumulator then has to refuse its own
        output rather than file a row nothing will surface."""
        monkeypatch.setattr("personalclaw.acp.outcomes._vocabulary", lambda: frozenset({"denied"}))
        acc = ToolOutcomeAccumulator()
        acc.observe(AcpEvent(kind="tool_call", tool_call_id="c1", title="Read"))
        with caplog.at_level(logging.WARNING, logger="personalclaw.acp.outcomes"):
            acc.observe(AcpEvent(kind=EVENT_TOOL_RESULT, tool_call_id="c1"))
        assert acc.drain() == []
        assert any("not a procedural outcome" in r.message for r in caplog.records)

    def test_result_without_a_preceding_call_is_dropped(self):
        """No tool_call means no tool identity. Filing it under a placeholder would merge
        every unnamed call into one row the surfacing rules read as evidence."""
        acc = ToolOutcomeAccumulator()
        acc.observe(AcpEvent(kind=EVENT_TOOL_RESULT, tool_call_id="ghost"))
        assert acc.drain() == []

    def test_tool_call_update_title_does_not_rename_the_tool(self):
        """An update's title is a progress DETAIL when it differs from the name. Folding
        it in would fragment one tool's priors across every argument it saw."""
        acc = ToolOutcomeAccumulator()
        acc.observe(AcpEvent(kind="tool_call", tool_call_id="c1", title="Bash"))
        acc.observe(AcpEvent(kind="tool_call_update", tool_call_id="c1", title="npm run build"))
        acc.observe(AcpEvent(kind=EVENT_TOOL_RESULT, tool_call_id="c1"))
        assert acc.drain() == [("Bash", "success")]

    def test_accumulation_is_bounded(self):
        """Same 200 ceiling the native accumulator enforces."""
        from personalclaw.acp.outcomes import MAX_OUTCOMES

        acc = ToolOutcomeAccumulator()
        for i in range(MAX_OUTCOMES + 25):
            acc.observe(AcpEvent(kind="tool_call", tool_call_id=f"c{i}", title="Read"))
            acc.observe(AcpEvent(kind=EVENT_TOOL_RESULT, tool_call_id=f"c{i}"))
        assert len(acc.drain()) == MAX_OUTCOMES
