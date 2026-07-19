"""The native in-process agent loop.

Covers the ReAct loop's contract with a mocked ModelProvider + ToolProvider:
- terminates on a no-tool-call turn;
- loops tool-call → result → re-inference with the result in history;
- respects max_turns;
- aggregates usage across inner model calls;
- the approval gate parks the loop, EVENT_PERMISSION_REQUEST is surfaced, and
  approve/reject resolves it;
- the deny-list blocks a denied tool pre-execution (no invoke);
- a tool-less model degrades to a single-shot answer.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.agents.native.approval import APPROVE, REJECT, ApprovalGate
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult


class _ScriptedModel:
    """A ModelProvider whose `complete()` replays a scripted list of turns."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.calls = 0
        self.last_tools = None
        self.seen_messages: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.last_tools = tools
        self.seen_messages.append(list(messages))
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev


class _Tool(ToolProvider):
    def __init__(self, name="echo", requires_approval=False, interactive=False) -> None:
        self._name = name
        self._req = requires_approval
        self._interactive = interactive
        self.invoked: list[dict] = []

    @property
    def name(self) -> str:
        return "mock"

    @property
    def display_name(self) -> str:
        return "Mock"

    async def list_tools(self):
        return [ToolDefinition(name=self._name, description="d", parameters={"type": "object"}, requires_approval=self._req, interactive=self._interactive)]

    async def invoke(self, tool_name, arguments):
        self.invoked.append(arguments)
        return ToolResult(success=True, output=f"OUT:{arguments.get('x', '')}")


def _defn():
    return AgentRuntimeDefinition(name="T", provider="native", model="scripted")


async def _drain(rt, msg="hi"):
    return [ev async for ev in rt.stream(msg)]


@pytest.mark.asyncio
async def test_stops_on_no_tool_call():
    model = _ScriptedModel([[AgentEvent(kind=EVENT_TEXT_CHUNK, text="hello"),
                             AgentEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)]])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[])
    await rt.start()
    evs = await _drain(rt)
    assert [e.kind for e in evs] == [EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    assert evs[-1].stop_reason == "end_turn"
    assert evs[-1].num_turns == 1


@pytest.mark.asyncio
async def test_steer_drains_at_model_boundary():
    """A steer message buffered during the tool batch is injected as a user message
    before the next inference (#37)."""
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input='{"x":"hi"}'),
         AgentEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=5)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"),
         AgentEvent(kind=EVENT_COMPLETE, input_tokens=8, output_tokens=3)],
    ])
    tool = _Tool(requires_approval=False)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool])
    await rt.start()
    # one steer, delivered once (then the source is empty)
    pending = ["also check the logs"]
    rt.set_steer_source(lambda: [pending.pop(0)] if pending else [])
    await _drain(rt)
    # the SECOND inference's message list must contain the steer as a user message
    second = model.seen_messages[1]
    assert any(m["role"] == "user" and "also check the logs" in str(m["content"]) for m in second)


@pytest.mark.asyncio
async def test_steer_capped_per_turn():
    """No more than _MAX_STEERS_PER_TURN steers are injected within one turn (#37)."""
    from personalclaw.agents.native.runtime import _MAX_STEERS_PER_TURN

    # Many tool turns so there are many model boundaries to drain at.
    turns = [[AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=f"c{i}", title="echo", tool_input='{"x":"y"}'),
              AgentEvent(kind=EVENT_COMPLETE)] for i in range(_MAX_STEERS_PER_TURN + 5)]
    turns.append([AgentEvent(kind=EVENT_TEXT_CHUNK, text="fin"), AgentEvent(kind=EVENT_COMPLETE)])
    model = _ScriptedModel(turns)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[_Tool()])
    await rt.start()
    # the source always has a steer available
    rt.set_steer_source(lambda: ["nudge"])
    await _drain(rt)
    injected = sum(
        1 for msgs in model.seen_messages for m in msgs
        if m["role"] == "user" and "Steering" in str(m["content"])
    )
    # each boundary re-sends the full history, so count DISTINCT steer messages in
    # the final message list instead.
    final = model.seen_messages[-1]
    steer_msgs = [m for m in final if m["role"] == "user" and "Steering" in str(m["content"])]
    assert len(steer_msgs) == _MAX_STEERS_PER_TURN


@pytest.mark.asyncio
async def test_no_steer_source_is_noop():
    model = _ScriptedModel([[AgentEvent(kind=EVENT_TEXT_CHUNK, text="hi"), AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[])
    await rt.start()
    evs = await _drain(rt)  # no steer source set → loop runs normally
    assert evs[-1].kind == EVENT_COMPLETE


class _FailingTool(_Tool):
    """A tool that fails with recovery_hints (TC5) — the runtime must surface them."""
    async def invoke(self, tool_name, arguments):
        return ToolResult(success=False, error="file not found",
                          recovery_hints=["try list_dir to see available files",
                                          "check the path is relative to the workspace"])


@pytest.mark.asyncio
async def test_tool_result_carries_recovery_hints_on_failure():
    """TC5: a failed tool's recovery_hints reach the TOOL_RESULT event's tool_meta so
    the chat card can surface a 'Next steps' note (they were dropped at the WS boundary)."""
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input='{"x":"hi"}'),
         AgentEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=5)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"),
         AgentEvent(kind=EVENT_COMPLETE, input_tokens=8, output_tokens=3)],
    ])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[_FailingTool()])
    await rt.start()
    evs = await _drain(rt)
    result_ev = next(e for e in evs if e.kind == EVENT_TOOL_RESULT)
    hints = (result_ev.tool_meta or {}).get("recovery_hints")
    assert hints and "try list_dir to see available files" in hints


@pytest.mark.asyncio
async def test_tool_loop_feeds_result_back():
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input='{"x":"hi"}'),
         AgentEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=5)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"),
         AgentEvent(kind=EVENT_COMPLETE, input_tokens=8, output_tokens=3)],
    ])
    tool = _Tool(requires_approval=False)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool])
    await rt.start()
    evs = await _drain(rt)
    assert [e.kind for e in evs] == [EVENT_TOOL_CALL, EVENT_TOOL_RESULT, EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    assert tool.invoked == [{"x": "hi"}]
    # the 2nd inference saw the tool result in history
    second = model.seen_messages[1]
    assert any(m.get("role") == "tool" and "OUT:hi" in str(m.get("content")) for m in second)
    # usage aggregated across both model calls
    assert evs[-1].input_tokens == 18 and evs[-1].output_tokens == 8


@pytest.mark.asyncio
async def test_complete_carries_turn_telemetry():
    """The terminal EVENT_COMPLETE reports event_count + tool_call_count so the
    chat runner can render the 'Turn complete' stats line for native turns."""
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input="{}"),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"),
         AgentEvent(kind=EVENT_COMPLETE)],
    ])
    tool = _Tool(requires_approval=False)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool])
    await rt.start()
    evs = await _drain(rt)
    final = evs[-1]
    assert final.kind == EVENT_COMPLETE
    # One tool call across the two turns.
    assert final.tool_call_count == 1
    # Every event observed this prompt is tallied: turn-1 (tool_call + complete)
    # + the tool-execution events (tool_call card + tool_result) + turn-2
    # (text_chunk + complete) = 6.
    assert final.event_count == 6
    # A plain (tool-less) turn reports zero tool calls but still a positive
    # event count, so the stats line still appears.
    model2 = _ScriptedModel([[AgentEvent(kind=EVENT_TEXT_CHUNK, text="hi"),
                              AgentEvent(kind=EVENT_COMPLETE)]])
    rt2 = NativeAgentRuntime(definition=_defn(), model_provider=model2, tool_providers=[])
    await rt2.start()
    final2 = (await _drain(rt2))[-1]
    assert final2.tool_call_count == 0
    assert final2.event_count == 2


@pytest.mark.asyncio
async def test_cancel_midturn_pairs_pending_tool_calls():
    """A turn cancelled mid-inference (watchdog/circuit-breaker) must not leave a
    tool_call unanswered in history — every pending call gets a synthetic tool
    result, so the next cycle's history replay stays well-paired (otherwise
    Bedrock Converse rejects the unanswered toolUse)."""

    class _CancellingModel(_ScriptedModel):
        def __init__(self, rt_box, turns):
            super().__init__(turns)
            self._rt_box = rt_box

        async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
            self.seen_messages.append(list(messages))
            self.calls += 1
            yield AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input="{}")
            # Simulate the watchdog cancelling the turn before the tool runs.
            self._rt_box[0]._cancelled = True
            yield AgentEvent(kind=EVENT_COMPLETE)

    box: list = [None]
    model = _CancellingModel(box, [[]])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[_Tool()])
    box[0] = rt
    await rt.start()
    evs = await _drain(rt)
    assert evs[-1].stop_reason == "cancelled"

    # Every assistant tool_call id is answered by a tool message in history.
    history = rt._messages
    call_ids = {
        tc["id"]
        for m in history
        if m.get("role") == "assistant"
        for tc in m.get("tool_calls", []) or []
    }
    answered = {m.get("tool_call_id") for m in history if m.get("role") == "tool"}
    assert call_ids and call_ids <= answered


@pytest.mark.asyncio
async def test_respects_max_turns():
    # model always calls a tool → would loop forever without max_turns.
    loop_turn = [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c", title="echo", tool_input="{}"),
                 AgentEvent(kind=EVENT_COMPLETE)]
    model = _ScriptedModel([loop_turn])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[_Tool()], max_turns=3)
    await rt.start()
    evs = await _drain(rt)
    assert evs[-1].kind == EVENT_COMPLETE and evs[-1].stop_reason == "max_turns"
    assert evs[-1].num_turns == 3


@pytest.mark.asyncio
async def test_passes_tool_schema_to_model():
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[_Tool(name="echo")])
    await rt.start()
    await _drain(rt)
    assert model.last_tools and model.last_tools[0]["function"]["name"] == "echo"


@pytest.mark.asyncio
async def test_approval_gate_parks_then_approves():
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input='{"x":"y"}'),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    tool = _Tool(requires_approval=True)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool])
    await rt.start()

    seen, gen = [], rt.stream("go")
    # Pump until the permission request surfaces, then approve out-of-band.
    async def pump():
        async for ev in gen:
            seen.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                await rt.approve_tool(ev.request_id)
    await asyncio.wait_for(pump(), timeout=5)
    kinds = [e.kind for e in seen]
    assert EVENT_PERMISSION_REQUEST in kinds
    assert EVENT_TOOL_RESULT in kinds
    assert tool.invoked == [{"x": "y"}]  # approved → invoked


@pytest.mark.asyncio
async def test_approval_reject_skips_invoke():
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input="{}"),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    tool = _Tool(requires_approval=True)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool])
    await rt.start()
    seen = []
    async def pump():
        async for ev in rt.stream("go"):
            seen.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                await rt.reject_tool(ev.request_id)
    await asyncio.wait_for(pump(), timeout=5)
    result = next(e for e in seen if e.kind == EVENT_TOOL_RESULT)
    # The reject feeds back a recovery observation (why + adapt-don't-repeat),
    # not a bare error, so an unattended agent can self-correct.
    _out = str(result.tool_output).lower()
    assert "declined" in _out and "do not retry" in _out
    assert tool.invoked == []  # rejected → never invoked


@pytest.mark.asyncio
async def test_denylist_blocks_before_invoke():
    # "rm -rf" style — use a tool name the deny-list rejects.
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input="{}"),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    tool = _Tool(name="echo", requires_approval=False)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool],
                            extra_deny_patterns=["echo"])
    await rt.start()
    seen = await _drain(rt, "go")
    result = next(e for e in seen if e.kind == EVENT_TOOL_RESULT)
    assert "error" in str(result.tool_output).lower()
    assert tool.invoked == []  # deny-list → never invoked


@pytest.mark.asyncio
async def test_task_mode_ask_blocks_mutation_even_when_auto_approved():
    """The task-mode gate runs in _guard_and_invoke BEFORE approval, so an
    auto-approved (requires_approval=False) mutating tool is still blocked in Ask
    mode — a Trust/YOLO auto-approve can't bypass the read-only posture."""
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="write_file", tool_input='{"path":"x","content":"y"}'),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    tool = _Tool(name="write_file", requires_approval=False)  # auto-approved
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool])
    rt.set_approval_policy("yolo")  # most permissive approval
    rt.set_task_mode("ask")         # most restrictive task mode
    await rt.start()
    seen = await _drain(rt, "write a file")
    result = next(e for e in seen if e.kind == EVENT_TOOL_RESULT)
    assert "ask mode" in str(result.tool_output).lower()
    assert tool.invoked == []  # gated before invoke despite yolo auto-approve


@pytest.mark.asyncio
async def test_task_mode_plan_allows_read_blocks_write():
    """Plan mode allows read-only tools (so the plan is grounded) but blocks writes."""
    # A read-only tool runs; a write tool is gated. Two separate runtimes.
    read_model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="read_file", tool_input='{"path":"x"}'),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    read_tool = _Tool(name="read_file", requires_approval=False)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=read_model, tool_providers=[read_tool])
    rt.set_task_mode("plan")
    await rt.start()
    seen = await _drain(rt, "read it")
    assert read_tool.invoked == [{"path": "x"}]  # read RAN in plan mode

    write_model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="write_file", tool_input='{"path":"x","content":"y"}'),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    write_tool = _Tool(name="write_file", requires_approval=False)
    rt2 = NativeAgentRuntime(definition=_defn(), model_provider=write_model, tool_providers=[write_tool])
    rt2.set_task_mode("plan")
    await rt2.start()
    seen2 = await _drain(rt2, "write it")
    result = next(e for e in seen2 if e.kind == EVENT_TOOL_RESULT)
    assert "plan mode" in str(result.tool_output).lower()
    assert write_tool.invoked == []  # write GATED in plan mode


# ── T5: no-interaction toolset for unattended runs ──

@pytest.mark.asyncio
async def test_unattended_strips_interactive_tools():
    """An unattended run must never see an option-prompt-shaped tool — it would
    block waiting for a human who isn't there."""
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    interactive = _Tool(name="AskUserQuestion", interactive=True)
    normal = _Tool(name="echo")
    rt = NativeAgentRuntime(
        definition=_defn(), model_provider=model,
        tool_providers=[interactive, normal], unattended=True,
    )
    await rt.start()
    names = {t.name for t in rt._tool_defs}
    assert "AskUserQuestion" not in names  # stripped
    assert "echo" in names                 # kept


@pytest.mark.asyncio
async def test_attended_keeps_interactive_tools():
    """The default (attended) run keeps interactive tools available."""
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(), model_provider=model,
        tool_providers=[_Tool(name="AskUserQuestion", interactive=True)],
    )
    await rt.start()
    assert "AskUserQuestion" in {t.name for t in rt._tool_defs}


@pytest.mark.asyncio
async def test_unattended_strips_interactive_by_name_hint():
    """A tool that never set the flag but whose NAME is option-prompt-shaped
    (external MCP) is still stripped via the name-hint fallback."""
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(), model_provider=model,
        tool_providers=[_Tool(name="request_user_input"), _Tool(name="echo")],
        unattended=True,
    )
    await rt.start()
    names = {t.name for t in rt._tool_defs}
    assert "request_user_input" not in names
    assert "echo" in names


@pytest.mark.asyncio
async def test_unattended_approval_fails_fast_no_park():
    """An approval-needing tool in an unattended run must NOT park on the gate
    (no human will approve) — it fails fast with a recoverable denial and the
    tool is never invoked."""
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input="{}"),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    tool = _Tool(name="echo", requires_approval=True)
    rt = NativeAgentRuntime(
        definition=_defn(), model_provider=model, tool_providers=[tool], unattended=True,
    )
    await rt.start()
    # No out-of-band approver: an attended run would park here and time out at
    # 300s; the unattended path returns immediately. Bound at 5s to prove it.
    seen = await asyncio.wait_for(_drain(rt, "go"), timeout=5)
    kinds = [e.kind for e in seen]
    assert EVENT_PERMISSION_REQUEST not in kinds  # never surfaced a prompt
    result = next(e for e in seen if e.kind == EVENT_TOOL_RESULT)
    _out = str(result.tool_output).lower()
    assert "unattended" in _out and "do not retry" in _out
    assert tool.invoked == []  # never invoked


@pytest.mark.asyncio
async def test_unattended_auto_policy_still_runs_approval_tool():
    """unattended + an 'auto' approval policy: the tool auto-approves and runs
    (unattended is about never blocking, not about denying everything)."""
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="echo", tool_input='{"x":"z"}'),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    tool = _Tool(name="echo", requires_approval=True)
    rt = NativeAgentRuntime(
        definition=_defn(), model_provider=model, tool_providers=[tool], unattended=True,
    )
    rt.set_approval_policy("auto")
    await rt.start()
    seen = await asyncio.wait_for(_drain(rt, "go"), timeout=5)
    assert EVENT_PERMISSION_REQUEST not in [e.kind for e in seen]
    assert tool.invoked == [{"x": "z"}]  # auto-approved → invoked


# ── T9: dry-run observe-mode ──

class _RiskyTool(ToolProvider):
    """A write-capable tool (CAUTION) + a read-only tool (SAFE) from one provider."""

    def __init__(self) -> None:
        self.writes = 0
        self.reads = 0

    @property
    def name(self) -> str:
        return "mock"

    @property
    def display_name(self) -> str:
        return "Mock"

    async def list_tools(self):
        from personalclaw.tool_providers.base import RiskLevel
        return [
            ToolDefinition(name="write_thing", description="d", parameters={"type": "object"},
                           requires_approval=False, risk_level=RiskLevel.CAUTION),
            ToolDefinition(name="read_thing", description="d", parameters={"type": "object"},
                           requires_approval=False, risk_level=RiskLevel.SAFE),
        ]

    async def invoke(self, tool_name, arguments):
        if tool_name == "write_thing":
            self.writes += 1
            return ToolResult(success=True, output="WROTE")
        self.reads += 1
        return ToolResult(success=True, output="READ")


@pytest.mark.asyncio
async def test_dry_run_intercepts_write_tool():
    """In dry-run, a write-capable (non-SAFE) tool is NOT executed — it returns a
    'would have' observation."""
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="write_thing", tool_input="{}"),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    tool = _RiskyTool()
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool], dry_run=True)
    await rt.start()
    seen = await _drain(rt, "go")
    result = next(e for e in seen if e.kind == EVENT_TOOL_RESULT)
    assert "DRY RUN" in str(result.tool_output)
    assert tool.writes == 0  # never executed


@pytest.mark.asyncio
async def test_dry_run_allows_read_tool():
    """A read-only SAFE tool still runs in dry-run, so the agent sees real state."""
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="read_thing", tool_input="{}"),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    tool = _RiskyTool()
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[tool], dry_run=True)
    await rt.start()
    seen = await _drain(rt, "go")
    result = next(e for e in seen if e.kind == EVENT_TOOL_RESULT)
    assert "READ" in str(result.tool_output)  # really ran
    assert tool.reads == 1


@pytest.mark.asyncio
async def test_dry_run_is_unattended():
    """A dry run implies unattended (interactive tools stripped, gate fails fast)."""
    rt = NativeAgentRuntime(definition=_defn(), model_provider=_ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]]),
                            tool_providers=[], dry_run=True)
    assert rt._unattended is True


@pytest.mark.asyncio
async def test_toolless_model_single_shot():
    class _NoTools(_ScriptedModel):
        supports_tools = False
    model = _NoTools([[AgentEvent(kind=EVENT_TEXT_CHUNK, text="hi"), AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[_Tool()])
    await rt.start()
    await _drain(rt)
    assert model.last_tools is None  # no tool schema sent to a tool-less model


# ── ApprovalGate unit ──

@pytest.mark.asyncio
async def test_approval_gate_resolve_and_timeout():
    gate = ApprovalGate()
    # resolve
    fut = asyncio.ensure_future(gate.request("r1", timeout=5))
    await asyncio.sleep(0.01)
    assert gate.approve("r1") is True
    assert await fut == APPROVE
    # timeout → REJECT (fail-closed)
    assert await gate.request("r2", timeout=0.05) == REJECT
    # resolve unknown id
    assert gate.reject("nope") is False


# ── progressive tool disclosure (PT1): catalog tier + tool_schema ──

class _ManyTools(ToolProvider):
    """A provider with N tools so the catalog exceeds K and retrieval reduces."""

    def __init__(self, n: int) -> None:
        self._n = n
        self.invoked: list[tuple[str, dict]] = []

    @property
    def name(self) -> str:
        return "many"

    @property
    def display_name(self) -> str:
        return "Many"

    async def list_tools(self):
        return [ToolDefinition(name=f"niche_tool_{i}", description=f"does niche thing {i}",
                               parameters={"type": "object", "properties": {"q": {"type": "string"}}},
                               requires_approval=False, provider="many")
                for i in range(self._n)]

    async def invoke(self, tool_name, arguments):
        self.invoked.append((tool_name, arguments))
        return ToolResult(success=True, output=f"ran {tool_name}")


@pytest.mark.asyncio
async def test_reduced_turn_injects_catalog_and_both_meta_tools():
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[_ManyTools(80)])
    await rt.start()
    await _drain(rt, "do something unrelated to any niche tool")
    names = {t["function"]["name"] for t in (model.last_tools or [])}
    # both discovery tools are always in the full-schema set on a reduced turn
    assert "tool_search" in names and "tool_schema" in names
    # the long tail is disclosed as a catalog in a system message (not hidden)
    sys_msgs = [m["content"] for m in model.seen_messages[-1] if m.get("role") == "system"]
    catalog = "\n".join(sys_msgs)
    assert "[tool catalog]" in catalog
    assert "niche_tool_" in catalog  # a non-surfaced tool is still NAMED


@pytest.mark.asyncio
async def test_tool_schema_expands_a_catalog_tool():
    # model: turn 1 calls tool_schema("niche_tool_42"); turn 2 stops.
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="tool_schema",
                    tool_input='{"tool_name":"niche_tool_42"}'),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[_ManyTools(80)])
    await rt.start()
    seen = await _drain(rt, "unrelated")
    results = [e for e in seen if e.kind == EVENT_TOOL_RESULT]
    assert results, "tool_schema should produce a result"
    out = str(results[0].tool_output or "")
    assert "niche_tool_42" in out and "parameters" in out  # returned the real schema


@pytest.mark.asyncio
async def test_catalog_only_tool_is_dispatchable():
    # A tool that was NOT in the surfaced full-schema set is still callable by name
    # (dispatch via _tool_index is independent of the per-turn schema).
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="niche_tool_7", tool_input='{"q":"hi"}'),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    many = _ManyTools(80)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[many])
    await rt.start()
    await _drain(rt, "unrelated query")
    assert any(name == "niche_tool_7" for name, _ in many.invoked)


@pytest.mark.asyncio
async def test_tool_schema_unknown_points_at_search():
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title="tool_schema",
                    tool_input='{"tool_name":"does_not_exist"}'),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_COMPLETE)],
    ])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[_ManyTools(80)])
    await rt.start()
    seen = await _drain(rt, "unrelated")
    out = "".join(str(e.tool_output or "") for e in seen if e.kind == EVENT_TOOL_RESULT)
    assert "tool_search" in out  # recovery hint names the discovery tool


class _McpTool(ToolProvider):
    """Provider with slash-namespaced MCP-style tool names (e.g. Bedrock must
    rewrite the "/" to "_" to satisfy its name constraint)."""

    def __init__(self, names: list[str]) -> None:
        self._names = names
        self.invoked: list[tuple[str, dict]] = []

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def display_name(self) -> str:
        return "MCP"

    async def list_tools(self):
        return [ToolDefinition(name=n, description="d",
                               parameters={"type": "object", "properties": {"x": {"type": "string"}}},
                               requires_approval=False, provider="mcp")
                for n in self._names]

    async def invoke(self, tool_name, arguments):
        self.invoked.append((tool_name, arguments))
        return ToolResult(success=True, output=f"ran {tool_name}")


@pytest.mark.asyncio
async def test_sanitized_name_falls_back_to_real_tool():
    """Regression: a provider (e.g. Bedrock) that rewrote mcp/everything/echo ->
    mcp_everything_echo but failed to reverse-map it must still dispatch — the
    runtime resolves the sanitized name back to the real tool id."""
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1",
                    title="mcp_everything_echo", tool_input='{"x":"hi"}'),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    mcp = _McpTool(["mcp/everything/echo", "mcp/everything/get-sum"])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[mcp])
    await rt.start()
    seen = await _drain(rt, "echo something")
    # Dispatched to the REAL tool id, not the sanitized name.
    assert any(name == "mcp/everything/echo" for name, _ in mcp.invoked)
    out = "".join(str(e.tool_output or "") for e in seen if e.kind == EVENT_TOOL_RESULT)
    assert "unknown tool" not in out


@pytest.mark.asyncio
async def test_exact_name_still_primary_path():
    """Exact real names keep dispatching directly even when a sanitized fallback
    exists — the fallback never shadows an exact match."""
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1",
                    title="mcp/everything/echo", tool_input='{"x":"hi"}'),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
    ])
    mcp = _McpTool(["mcp/everything/echo"])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[mcp])
    await rt.start()
    await _drain(rt, "echo")
    assert mcp.invoked and mcp.invoked[0][0] == "mcp/everything/echo"


@pytest.mark.asyncio
async def test_ambiguous_sanitized_collision_not_remapped():
    """Two real names that sanitize to the SAME safe form are dropped from the
    fallback map, so an ambiguous sanitized name yields the unknown-tool error
    rather than silently dispatching the wrong tool."""
    model = _ScriptedModel([
        [AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1",
                    title="mcp_everything_a_b", tool_input='{"x":"hi"}'),
         AgentEvent(kind=EVENT_COMPLETE)],
        [AgentEvent(kind=EVENT_COMPLETE)],
    ])
    # Both sanitize to "mcp_everything_a_b".
    # Both sanitize to "mcp_everything_a_b" ("/" -> "_", "_" already legal).
    mcp = _McpTool(["mcp/everything/a/b", "mcp/everything/a_b"])
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[mcp])
    await rt.start()
    # Auto-approve so an unknown tool reaches _invoke (returns the error) instead
    # of parking on the approval gate.
    rt.set_approval_policy("auto")
    assert "mcp_everything_a_b" not in rt._tool_sanitized_index
    seen = await _drain(rt, "go")
    assert not mcp.invoked  # neither tool dispatched
    out = "".join(str(e.tool_output or "") for e in seen if e.kind == EVENT_TOOL_RESULT)
    assert "unknown tool" in out
