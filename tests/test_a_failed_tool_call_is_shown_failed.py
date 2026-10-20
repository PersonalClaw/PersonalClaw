"""A tool call that failed is shown failed, whoever failed it.

🔴 THE DEFECT (measured on ``origin/main``). The chat's tool card reads a result's ``ok`` (present
and ``False`` only on failure, absent on success: ``acp/outcomes.py`` states the contract, and the
ACP seam keeps it). The native runtime stamped ``ok: false`` only when a PROVIDER returned
``success=False``. Every failure the runtime writes itself went out with no ``ok`` at all, so the
card drew a green check for ``Error: unknown tool 'x'``, a call the deny-list or a hook refused, one
you declined, one the loop breaker blocked, and one a stop dropped before it ran.

The runtime's own bookkeeping disagreed with the card in the other direction. The breaker and
procedural memory decided failure by ``result.startswith("Error:")``, so a provider failure that
carries a WHAT/WHY/FIX envelope (its text starts ``WHAT:``) counted as a success, and a successful
call whose output happened to start with ``Error:`` (``cat error.log``) counted as a failure.

Now each failure the runtime writes stamps ``ok: false`` where it is written, and the breaker and
the outcome list read that one bit, as the ACP seam's accumulator already does.

The bit has to be set wherever a failure starts, so two more places set it now. Three answers of
PersonalClaw's own tools were plain strings rather than a ``ToolFailure`` (#3487), so they reached
the card as successes. And the MCP server PersonalClaw runs for an ACP agent never set
``isError``, the one field that agent can tell a failure by.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from test_native_runtime import _defn, _ScriptedModel

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.errors import AgentError
from personalclaw.guardrails.loop_breaker import BLOCK_THRESHOLD
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.tool_providers.base import ToolDefinition, ToolFailure, ToolProvider, ToolResult


class _Provider(ToolProvider):
    """One tool, answering with a fixed result."""

    def __init__(self, tool: str, result: ToolResult, *, requires_approval: bool = False) -> None:
        self._tool = tool
        self._result = result
        self._requires_approval = requires_approval
        self.invoked = 0

    @property
    def name(self) -> str:
        return "probe"

    @property
    def display_name(self) -> str:
        return "Probe"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=self._tool,
                description="d",
                parameters={"type": "object"},
                requires_approval=self._requires_approval,
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.invoked += 1
        return self._result


def _calls(tool: str, times: int = 1, *, args: str = "{}") -> _ScriptedModel:
    """A model that calls *tool* with *args* once per turn for *times* turns, then answers."""
    turns = [
        [
            AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id=f"c{i}", title=tool, tool_input=args),
            AgentEvent(kind=EVENT_COMPLETE),
        ]
        for i in range(times)
    ]
    turns.append([AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)])
    return _ScriptedModel(turns)


async def _results(
    runtime: NativeAgentRuntime, *, answer: str = ""
) -> list[tuple[str, dict[str, Any]]]:
    """Drive one turn; answer each approval with *answer* (``approve``/``reject``)."""
    await runtime.start()
    seen: list[AgentEvent] = []

    async def pump() -> None:
        async for ev in runtime.stream("go"):
            seen.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                if answer == "reject":
                    await runtime.reject_tool(ev.request_id)
                else:
                    await runtime.approve_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=10)
    return [
        (str(e.tool_output), dict(e.tool_meta or {})) for e in seen if e.kind == EVENT_TOOL_RESULT
    ]


def _runtime(model: _ScriptedModel, *providers: ToolProvider) -> NativeAgentRuntime:
    return NativeAgentRuntime(
        definition=_defn(), model_provider=model, tool_providers=list(providers)
    )


# ── failures the runtime writes itself ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unknown_tool_reads_failed():
    ok = _Provider("probe_tool", ToolResult(success=True, output="fine"))
    [(output, meta)] = await _results(_runtime(_calls("no_such_tool"), ok))

    assert output.startswith("Error: unknown tool")
    assert meta.get("ok") is False, meta


@pytest.mark.asyncio
async def test_a_call_the_deny_list_refuses_reads_failed():
    denied = _Provider("delete_stack", ToolResult(success=True, output="deleted"))
    [(output, meta)] = await _results(_runtime(_calls("delete_stack"), denied))

    assert denied.invoked == 0 and "blocked by a security policy" in output
    assert meta.get("ok") is False, meta


@pytest.mark.asyncio
async def test_a_call_you_declined_reads_failed():
    asks = _Provider("probe_tool", ToolResult(success=True, output="ran"), requires_approval=True)
    [(output, meta)] = await _results(_runtime(_calls("probe_tool"), asks), answer="reject")

    assert asks.invoked == 0 and "declined" in output
    assert meta.get("ok") is False, meta


@pytest.mark.asyncio
async def test_a_call_the_loop_breaker_blocks_reads_failed():
    broken = _Provider("probe_tool", ToolResult(success=False, error="it broke"))
    results = await _results(_runtime(_calls("probe_tool", times=BLOCK_THRESHOLD + 1), broken))

    output, meta = results[-1]
    assert "was blocked" in output, output
    assert broken.invoked == BLOCK_THRESHOLD, "the blocked call ran anyway"
    assert meta.get("ok") is False, meta


# ── the one bit, read by the runtime's own bookkeeping ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_provider_failure_with_an_envelope_counts_as_a_failure():
    """Its text starts ``WHAT:``, not ``Error:``. The card read it failed; the breaker did not."""
    envelope = AgentError(code="ERR_PROBE", what="it broke", why="because", fix="retry later")
    broken = _Provider("probe_tool", ToolResult(success=False, agent_error=envelope))
    runtime = _runtime(_calls("probe_tool"), broken)
    [(output, meta)] = await _results(runtime)

    assert output.startswith("WHAT: it broke") and meta.get("ok") is False
    assert runtime.drain_tool_outcomes() == [("probe_tool", "failed")]


@pytest.mark.asyncio
async def test_a_success_whose_output_starts_with_error_is_a_success():
    """``cat error.log`` succeeded. Its output is the file, and the file starts ``Error:``."""
    log = _Provider("probe_tool", ToolResult(success=True, output="Error: disk full at 03:12"))
    runtime = _runtime(_calls("probe_tool"), log)
    [(output, meta)] = await _results(runtime)

    assert output == "Error: disk full at 03:12"
    assert "ok" not in meta, meta
    assert runtime.drain_tool_outcomes() == [("probe_tool", "success")]


@pytest.mark.asyncio
async def test_a_call_that_worked_reads_worked():
    """BASELINE (passes before and after): success stays absent-``ok``, as the contract says."""
    ok = _Provider("probe_tool", ToolResult(success=True, output="fine"))
    runtime = _runtime(_calls("probe_tool"), ok)
    [(output, meta)] = await _results(runtime)

    assert output == "fine" and "ok" not in meta
    assert runtime.drain_tool_outcomes() == [("probe_tool", "success")]


# ── failures PersonalClaw's own tools handle ───────────────────────────────────────────────────
#
# A handler in an ``mcp_*`` module answers a failure it handles with a ``ToolFailure`` (#3487),
# which the bridge turns into ``success=False`` and so into ``ok: false``. Three answers still
# reached the bridge as plain strings, so each read as a success: a refused automation, a
# ``resume_run_id: "self"`` outside a run, and a subagent batch that did not compile. The
# runtime's breaker used to catch them by their ``Error:`` text; it reads the bit now, so the
# bit has to be there.


def test_a_refused_automation_is_a_tool_failure():
    from personalclaw import mcp_automation

    out = mcp_automation._call_tool_inner("automation_create", {})

    assert out == "Error: name is required."
    assert isinstance(out, ToolFailure)


def test_resume_self_outside_a_run_is_a_tool_failure(monkeypatch):
    from personalclaw import mcp_automation

    monkeypatch.delenv("__wf_run_id", raising=False)
    out = mcp_automation._call_tool_inner(
        "set_onetime_task", {"name": "later", "when": "in 5 minutes", "resume_run_id": "self"}
    )

    assert out.startswith("Error: resume_run_id='self' only works from inside a workflow run")
    assert isinstance(out, ToolFailure)


def test_a_batch_that_did_not_compile_is_a_tool_failure(monkeypatch):
    from personalclaw import mcp_subagents

    monkeypatch.setattr(mcp_subagents, "_post", lambda path, body: {})
    monkeypatch.setattr(mcp_subagents, "_resolve_session_key", lambda: "chat:1")
    out = mcp_subagents._call_tool_inner("subagent_run", {"tasks": ["do a thing", "do another"]})

    assert out.startswith("Error: the batch did not compile")
    assert isinstance(out, ToolFailure)


@pytest.mark.asyncio
async def test_a_refused_automation_reads_failed_on_its_card():
    """The automation tools as an agent gets them, one call they refuse, and its card."""
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    automations = InProcessMcpToolProvider(
        module="personalclaw.mcp_automation", provider_name="automations", display="Automations"
    )
    runtime = _runtime(_calls("automation_create", args='{"name": "nightly"}'), automations)
    [(output, meta)] = await _results(runtime)

    assert output.startswith("Error: "), output
    assert meta.get("ok") is False, meta
    assert runtime.drain_tool_outcomes() == [("automation_create", "failed")]


# ── the MCP server PersonalClaw runs for an ACP agent ──────────────────────────────────────────


def test_the_mcp_server_marks_a_failed_call_as_an_error(monkeypatch):
    """An ACP agent reaches PersonalClaw's tools over this server and can tell a failure only by
    the answer's ``isError`` (``acp/translate.py`` reads it), which the server never set."""
    import io
    import json
    import sys

    from personalclaw import mcp_shared
    from personalclaw.tool_providers.base import tool_failure

    frames = [
        {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name}}
        for i, name in enumerate(("refused", "fine", "hidden"), start=1)
    ]
    stdin = type(
        "Stdin",
        (),
        {"buffer": io.BytesIO(b"".join(json.dumps(f).encode() + b"\n" for f in frames))},
    )
    out = io.StringIO()
    monkeypatch.setattr(mcp_shared, "_use_content_length", False)
    monkeypatch.setattr(mcp_shared, "_resolve_excluded_tools", lambda: {"hidden"})
    monkeypatch.setattr(sys, "stdin", stdin())
    monkeypatch.setattr(sys, "stdout", out)

    def call(name: str, args: dict) -> str:
        return tool_failure("no automation with that id") if name == "refused" else "done"

    mcp_shared.run_mcp_stdio_loop("personalclaw", "1", lambda: [], call)

    answers = {r["id"]: r["result"] for r in map(json.loads, out.getvalue().splitlines())}
    assert answers[1].get("isError") is True, answers[1]
    assert answers[3].get("isError") is True, "a call to a tool this agent may not use"
    assert "isError" not in answers[2], "a success says nothing, as before"
