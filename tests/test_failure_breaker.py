"""Per-run consecutive-failure breaker in the native loop."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.agents.native.runtime import (
    _BREAKER_BLOCK,
    _STRUCT_REPEAT,
    NativeAgentRuntime,
    _FailureBreaker,
    _params_key,
    _result_digest,
)
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

# ── unit: _FailureBreaker + _params_key ──


def test_params_key_is_params_aware():
    assert _params_key("bash", {"command": "ls"}) != _params_key("bash", {"command": "pwd"})
    assert _params_key("bash", {"command": "ls"}) == _params_key("bash", {"command": "ls"})


def test_params_key_stable_for_unusual_args():
    # Non-JSON-native args are coerced via default=str, so the key is still
    # stable + deterministic (same object repr → same key) and never raises.
    k1 = _params_key("t", {"x": (1, 2)})
    k2 = _params_key("t", {"x": (1, 2)})
    assert k1 == k2 and k1.startswith("t:")


def test_breaker_counts_and_resets_per_key():
    b = _FailureBreaker()
    k = _params_key("t", {"a": 1})
    assert b.record(k, True) == 1
    assert b.record(k, True) == 2
    assert b.record(k, False) == 0  # success clears the streak
    assert b.count(k) == 0


def test_breaker_total_independent_of_per_key():
    b = _FailureBreaker()
    b.record(_params_key("t", {"a": 1}), True)
    b.record(_params_key("t", {"a": 2}), True)
    assert b.total_failures == 2
    assert b.count(_params_key("t", {"a": 1})) == 1


def test_breaker_reset_clears_all():
    b = _FailureBreaker()
    b.record("k", True)
    b.reset()
    assert b.total_failures == 0 and b.count("k") == 0


# ── unit: structural loop detection (E3.1) ──


def test_result_digest_strips_volatile_fields():
    a = _result_digest("done in 1.23s pid=4012 at 2026-06-27T10:11:12Z id=0xdeadbeef")
    b = _result_digest("done in 9.99s pid=88 at 2026-06-27T23:00:00Z id=0xcafef00d")
    assert a == b  # volatile bits normalized → same digest


def test_structural_no_progress_detected_on_third_repeat():
    b = _FailureBreaker()
    sig = "tool:args\x1fSAME"
    assert b.record_structural(sig) == ""  # 1st
    assert b.record_structural(sig) == ""  # 2nd
    reason = b.record_structural(sig)  # 3rd → no-progress
    assert reason and "same result" in reason


def test_structural_no_progress_reported_once():
    b = _FailureBreaker()
    sig = "t\x1fX"
    [b.record_structural(sig) for _ in range(3)]
    # Already reported; subsequent identical calls don't re-warn (dedup).
    assert b.record_structural(sig) == ""


def test_structural_distinct_results_not_flagged():
    b = _FailureBreaker()
    for i in range(6):
        assert b.record_structural(f"t\x1fresult-{i}") == ""  # each unique → progress


def test_structural_ping_pong_detected():
    b = _FailureBreaker()
    a, c = "t\x1fA", "t\x1fB"
    reasons = [b.record_structural(a if i % 2 == 0 else c) for i in range(6)]
    assert any(r and "alternating" in r for r in reasons)


def test_structural_reset_rearms():
    b = _FailureBreaker()
    sig = "t\x1fY"
    [b.record_structural(sig) for _ in range(3)]
    b.reset_structural()
    # After re-arm, the same loop is detectable fresh (post-compaction guard).
    assert b.record_structural(sig) == ""
    assert b.record_structural(sig) == ""
    assert b.record_structural(sig)  # detected again


def test_reset_structural_leaves_failure_counts():
    b = _FailureBreaker()
    b.record("k", True)
    b.record("k", True)
    b.reset_structural()
    assert b.count("k") == 2  # failure path untouched by structural re-arm


# ── integration: the loop blocks a repeatedly-failing tool ──


class _AlwaysFailTool(ToolProvider):
    def __init__(self) -> None:
        self.invoked = 0

    @property
    def name(self) -> str:
        return "mock"

    @property
    def display_name(self) -> str:
        return "Mock"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="flaky",
                description="d",
                parameters={"type": "object"},
                requires_approval=False,
            )
        ]

    async def invoke(self, tool_name, arguments):
        self.invoked += 1
        return ToolResult(success=False, error="always fails")


class _RepeatModel:
    """Calls `flaky` with identical args every turn, then stops."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, n_calls: int) -> None:
        self._n = n_calls
        self.calls = 0
        self.last_tools = None

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.last_tools = tools
        self.calls += 1
        if self.calls <= self._n:
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"c{self.calls}",
                title="flaky",
                tool_input='{"x": 1}',
            )
            yield AgentEvent(kind=EVENT_COMPLETE)
        else:
            yield AgentEvent(kind=EVENT_COMPLETE)


@pytest.mark.asyncio
async def test_repeated_failure_gets_blocked_and_stops_invoking():
    # Model tries flaky(x=1) more times than the block threshold.
    model = _RepeatModel(n_calls=_BREAKER_BLOCK + 3)
    tool = _AlwaysFailTool()
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=model,
        tool_providers=[tool],
    )
    rt._max_turns = _BREAKER_BLOCK + 5
    await rt.start()
    results = []

    async def pump():
        async for ev in rt.stream("go"):
            if ev.kind == EVENT_TOOL_RESULT:
                results.append(str(ev.tool_output))

    await asyncio.wait_for(pump(), timeout=5)

    # The tool is invoked at most _BREAKER_BLOCK times — past that it's blocked
    # pre-invoke, so the underlying tool stops being called.
    assert tool.invoked == _BREAKER_BLOCK, tool.invoked
    # A warning appears once the streak hits the warn threshold.
    assert any("change approach" in r for r in results)
    # The block message appears once over the threshold.
    assert any("was blocked" in r for r in results)


@pytest.mark.asyncio
async def test_success_resets_streak_no_block():
    """A tool that fails twice then succeeds is never blocked."""

    class _FlipTool(ToolProvider):
        def __init__(self):
            self.calls = 0

        @property
        def name(self):
            return "mock"

        @property
        def display_name(self):
            return "Mock"

        async def list_tools(self):
            return [
                ToolDefinition(
                    name="flip",
                    description="d",
                    parameters={"type": "object"},
                    requires_approval=False,
                )
            ]

        async def invoke(self, tool_name, arguments):
            self.calls += 1
            if self.calls <= 2:
                return ToolResult(success=False, error="transient")
            return ToolResult(success=True, output="ok")

    class _FlipModel:
        supports_tools = True
        _model = "scripted"

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
            self.calls += 1
            if self.calls <= 4:
                yield AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id=f"c{self.calls}",
                    title="flip",
                    tool_input='{"x": 1}',
                )
                yield AgentEvent(kind=EVENT_COMPLETE)
            else:
                yield AgentEvent(kind=EVENT_COMPLETE)

    tool = _FlipTool()
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_FlipModel(),
        tool_providers=[tool],
    )
    rt._max_turns = 10
    await rt.start()

    async def pump():
        async for _ in rt.stream("go"):
            pass

    await asyncio.wait_for(pump(), timeout=5)
    # All 4 calls invoked — the success on call 3 reset the streak so it never blocked.
    assert tool.calls == 4


@pytest.mark.asyncio
async def test_structural_loop_warns_on_successful_repetition():
    """A tool that SUCCEEDS with the same result every call (going nowhere) trips
    the structural detector — the failure breaker never would (nothing failed)."""

    class _SameResultTool(ToolProvider):
        def __init__(self):
            self.calls = 0

        @property
        def name(self):
            return "mock"

        @property
        def display_name(self):
            return "Mock"

        async def list_tools(self):
            return [
                ToolDefinition(
                    name="spin",
                    description="d",
                    parameters={"type": "object"},
                    requires_approval=False,
                )
            ]

        async def invoke(self, tool_name, arguments):
            self.calls += 1
            # Identical output every time (only a volatile pid differs → normalized).
            return ToolResult(success=True, output=f"same output pid={1000 + self.calls}")

    class _SpinModel:
        supports_tools = True
        _model = "scripted"

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
            self.calls += 1
            if self.calls <= _STRUCT_REPEAT + 1:
                yield AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id=f"c{self.calls}",
                    title="spin",
                    tool_input='{"x": 1}',
                )
                yield AgentEvent(kind=EVENT_COMPLETE)
            else:
                yield AgentEvent(kind=EVENT_COMPLETE)

    tool = _SameResultTool()
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_SpinModel(),
        tool_providers=[tool],
    )
    rt._max_turns = _STRUCT_REPEAT + 3
    await rt.start()
    results = []

    async def pump():
        async for ev in rt.stream("go"):
            if ev.kind == EVENT_TOOL_RESULT:
                results.append(str(ev.tool_output))

    await asyncio.wait_for(pump(), timeout=5)
    # The tool kept succeeding (never blocked), but the structural warning fired.
    assert tool.calls >= _STRUCT_REPEAT
    assert any("looping without making progress" in r for r in results)
