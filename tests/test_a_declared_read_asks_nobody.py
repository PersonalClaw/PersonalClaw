"""A tool that declares it only reads asks nobody, whichever constructor built it.

Two fields of a tool's definition answer the approval question: what it DECLARES a call does
(``risk_level``, where ``RiskLevel.SAFE`` is the read-only declaration) and whether it asks before
it runs (``requires_approval``). Every tool PersonalClaw defines in Python states both, and every
one that declares a read asks nobody: ``read_file``, ``grep``, ``knowledge_search``, ``task_list``.

The two constructors that build tools from a declaration did not follow it. PersonalClaw's own tool
modules (memory, workflows, artifacts, skills, automations, the desktop) and an app's declared
routes were built with ``requires_approval=True`` whatever they declared. So a declared read:

* asked in a chat where ``read_file`` does not. ``memory_recall``, ``workflow_status`` and
  ``workflow_output`` each raised an approval card, and ``--approval reads``, which answers for
  background agents only, could not answer it there;
* was declined with "needs approval but the run is unattended" wherever nobody can be asked:
  ``personalclaw run``, a dry run, an unattended agent. Those are the postures that exist to run
  reads.

The posture tests could not see it, because each sets the runtime's policy to ``yolo`` so the
posture is the only gate, and ``yolo`` answers every ask. These drive the runtime with no policy.

A listing that shared a tool with its writes could not declare a read at all: ``triage_rules``
listed, added and revoked rules, so its list asked like a write. The list is ``triage_rules_list``.

Four previews were an argument of the change they preview, so they asked like it: checking a spec
(``workflow_author`` with ``save=false``), a run edit's cascade (``workflow_edit`` with
``preview_only``), the drift report (``workflow_audit``, whose default only reported) and an
automation's dry run (``automation_run`` with ``dry_run``). Each is now a read of its own
(``workflow_check``, ``workflow_edit_preview``, ``workflow_audit``, ``automation_dry_run``), and the
change takes no preview argument, so a call is what its tool declares.

And the rule holds whoever built the definition: a ``ToolDefinition`` that declares a read asks
nobody (``requires_approval`` is false), so an app's tools and a trusted MCP server's reads, which
PersonalClaw's own constructors do not build, run as every other read does.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from personalclaw import mcp_core
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.tool_providers.base import RiskLevel, ToolProvider, ToolResult

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: Every module whose tools the in-process provider builds: core's own and the categories the
#: `mcp-core` server aggregates.
_MODULES = ("personalclaw.mcp_core", *mcp_core._AGGREGATED_CATEGORY_MODULES)

#: The reads a chat and a headless run were asked about, and a listing of another category.
_READS = [
    ("personalclaw.mcp_workflows", "workflow_status", '{"run_id": "r1"}'),
    ("personalclaw.mcp_workflows", "workflow_output", '{"run_id": "r1", "node_id": "n1"}'),
    ("personalclaw.mcp_memory", "memory_recall", '{"query": "the dishwasher"}'),
    ("personalclaw.mcp_memory", "triage_rules_list", "{}"),
    ("personalclaw.mcp_core", "skill_search", '{"query": "release notes"}'),
    # The previews, each a read of its own.
    ("personalclaw.mcp_workflows", "workflow_check", '{"name": "triage", "root": "{}"}'),
    ("personalclaw.mcp_workflows", "workflow_edit_preview", '{"run_id": "a1b2c3d4", "ops": "[]"}'),
    ("personalclaw.mcp_workflows", "workflow_audit", "{}"),
    ("personalclaw.mcp_automation", "automation_dry_run", '{"id": "file:notes"}'),
]
#: The control: a change from the same registry still asks, and still fails closed unattended.
_CHANGE = ("personalclaw.mcp_memory", "memory_remember", '{"rule": "always x", "category": "tool"}')
#: The changes the previews preview, which still ask.
_PREVIEWED = [
    ("personalclaw.mcp_workflows", "workflow_author", '{"name": "triage", "root": "{}"}'),
    ("personalclaw.mcp_workflows", "workflow_edit", '{"run_id": "a1b2c3d4", "ops": "[]"}'),
    ("personalclaw.mcp_workflows", "workflow_repair", "{}"),
    ("personalclaw.mcp_automation", "automation_run", '{"id": "file:notes"}'),
]


# ── the declaration and the flag agree, on every constructor ─────────────────────────────


def _tool_definition_calls() -> list[tuple[str, ast.Call]]:
    """Every call that builds a ``ToolDefinition`` in the package, under any alias it is
    imported as (the runtime builds its meta-tools as ``_TD``)."""
    found: list[tuple[str, ast.Call]] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {"ToolDefinition"} | {
            alias.asname
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name == "ToolDefinition" and alias.asname
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if called in names:
                    found.append((f"{path.relative_to(_SRC)}:{node.lineno}", node))
    return found


def _is_safe_literal(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Attribute) and expr.attr == "SAFE"


def _is_level_literal(expr: ast.expr) -> bool:
    return isinstance(expr, ast.Attribute) and expr.attr in ("SAFE", "CAUTION", "DESTRUCTIVE")


def test_the_census_finds_the_tool_definitions():
    """The floor: a walk that found none would pass every rule below on nothing."""
    assert len(_tool_definition_calls()) >= 40


def test_every_tool_core_defines_states_what_it_does_and_whether_it_asks():
    """A tool that states nothing is a tool nobody decided about. The defaults fail closed (a
    change that asks), which is safe, but silent: a new read that forgets to say so asks in a
    chat and is declined in every headless run, so the rail makes the decision explicit."""
    silent = [
        where
        for where, call in _tool_definition_calls()
        if not {"risk_level", "requires_approval"} <= {kw.arg for kw in call.keywords}
    ]
    assert not silent, f"definitions that leave risk_level or requires_approval unsaid: {silent}"


def test_a_tool_that_declares_a_read_asks_nobody():
    """A literal `SAFE` comes with a literal `requires_approval=False`, and a declaration that is
    computed decides whether the tool asks from the same value, never a hard-coded `True`."""
    wrong = []
    for where, call in _tool_definition_calls():
        kw = {k.arg: k.value for k in call.keywords}
        risk, asks = kw.get("risk_level"), kw.get("requires_approval")
        if risk is None or asks is None:
            continue  # the rule above names these
        if _is_safe_literal(risk):
            if not (isinstance(asks, ast.Constant) and asks.value is False):
                wrong.append(f"{where}: declares SAFE but asks")
        elif not _is_level_literal(risk):
            risk_names = {n.id for n in ast.walk(risk) if isinstance(n, ast.Name)}
            asks_names = {n.id for n in ast.walk(asks) if isinstance(n, ast.Name)}
            if not risk_names & asks_names:
                wrong.append(f"{where}: its declaration is computed but whether it asks is not")
    assert not wrong, wrong


@pytest.mark.asyncio
@pytest.mark.parametrize("module", _MODULES)
async def test_personalclaws_own_tools_ask_exactly_when_they_declare_a_change(module):
    """🔴 Red before the fix: every one of these was built with `requires_approval=True`. A change
    whose work asks the owner itself (`WORK_ASKS_META_KEY`: `subagent_run`) is the one change the
    call is not asked about, since what it starts asks."""
    import importlib

    from personalclaw.tool_providers.base import WORK_ASKS_META_KEY

    defs = await InProcessMcpToolProvider(module=module, provider_name="under-test").list_tools()
    assert defs, f"{module} listed no tools"
    asks_itself = {
        str(t.get("name"))
        for t in importlib.import_module(module)._list_tools()
        if (t.get("_meta") or {}).get(WORK_ASKS_META_KEY) is True
    }
    wrong = sorted(
        d.name
        for d in defs
        if d.requires_approval
        is not (d.risk_level is not RiskLevel.SAFE and d.name not in asks_itself)
    )
    assert not wrong, f"asking does not follow the declaration for: {wrong}"


@pytest.mark.asyncio
async def test_the_named_reads_ask_nobody_and_a_change_still_asks():
    by_name = {}
    for module in {m for m, _, _ in [*_READS, _CHANGE]}:
        provider = InProcessMcpToolProvider(module=module, provider_name="under-test")
        by_name.update({d.name: d for d in await provider.list_tools()})
    for _, tool, _ in _READS:
        assert by_name[tool].risk_level is RiskLevel.SAFE, tool
        assert by_name[tool].requires_approval is False, tool
    assert by_name[_CHANGE[1]].requires_approval is True


# ── the gate, with no policy answering for it ────────────────────────────────────────────


class _Recording(InProcessMcpToolProvider):
    """A real registry's definitions, which are what is under test, recording each call it
    would have made."""

    def __init__(self, module: str) -> None:
        super().__init__(module=module, provider_name="under-test")
        self.invoked: list[str] = []

    async def invoke(self, tool_name, arguments):
        self.invoked.append(tool_name)
        return ToolResult(success=True, output="ran")


class _OneCall:
    """A model that calls one tool, then answers."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, tool: str, args: str) -> None:
        self._turns = [
            [
                AgentEvent(kind=EVENT_TOOL_CALL, tool_call_id="c1", title=tool, tool_input=args),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        for ev in turn:
            yield ev


async def _turn(module: str, tool: str, args: str, **runtime_kw):
    """One native turn in which the model calls *tool*, with no approval policy set. Every ask
    is declined, so a call ran only if nobody had to be asked. Returns what ran, what was
    asked, and the call's result as the model got it."""
    provider = _Recording(module)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_OneCall(tool, args),
        tool_providers=[provider],
        **runtime_kw,
    )
    await rt.start()
    asked: list[str] = []
    results: list[str] = []

    async def drain() -> None:
        async for ev in rt.stream("go"):
            if ev.kind == EVENT_PERMISSION_REQUEST:
                asked.append(ev.title)
                await rt.reject_tool(ev.request_id)
            elif ev.kind == EVENT_TOOL_RESULT:
                results.append(str(ev.tool_output))

    await asyncio.wait_for(drain(), timeout=10)
    return provider.invoked, asked, results


@pytest.mark.asyncio
@pytest.mark.parametrize("module,tool,args", _READS, ids=[r[1] for r in _READS])
async def test_a_chat_is_not_asked_about_a_declared_read(module, tool, args):
    """🔴 Red before the fix: the call raised an approval card, like a write."""
    invoked, asked, _ = await _turn(module, tool, args)
    assert asked == []
    assert invoked == [tool]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,tool,args", [_CHANGE, *_PREVIEWED], ids=[r[1] for r in [_CHANGE, *_PREVIEWED]]
)
async def test_a_chat_is_still_asked_about_a_change(module, tool, args):
    invoked, asked, _ = await _turn(module, tool, args)
    assert asked == [tool]
    assert invoked == []


@pytest.mark.asyncio
@pytest.mark.parametrize("module,tool,args", _READS, ids=[r[1] for r in _READS])
async def test_a_run_with_nobody_to_ask_runs_a_declared_read(module, tool, args):
    """🔴 Red before the fix: `personalclaw run`, a dry run and an unattended agent declined it
    with "needs approval but the run is unattended"."""
    invoked, asked, results = await _turn(module, tool, args, unattended=True)
    assert asked == []
    assert invoked == [tool]
    assert results == ["ran"]


@pytest.mark.asyncio
async def test_a_run_with_nobody_to_ask_still_declines_a_change():
    """The fail-closed half this must not widen: a change with nobody to ask does not run."""
    invoked, asked, results = await _turn(*_CHANGE, unattended=True)
    assert asked == [] and invoked == []
    assert len(results) == 1 and "unattended" in results[0]


@pytest.mark.asyncio
async def test_a_headless_read_only_run_runs_a_declared_read():
    """`personalclaw run` without `--allow`: Ask mode, and nobody to ask."""
    module, tool, args = _READS[2]
    provider = _Recording(module)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_OneCall(tool, args),
        tool_providers=[provider],
        unattended=True,
    )
    rt.set_task_mode("ask")
    await rt.start()
    await asyncio.wait_for(_consume(rt), timeout=10)
    assert provider.invoked == [tool]


async def _consume(rt: NativeAgentRuntime) -> None:
    async for _ev in rt.stream("go"):
        pass


# ── a preview is a read of its own ───────────────────────────────────────────────────────


_PREVIEW_ARGUMENTS = [
    ("personalclaw.mcp_workflows", "workflow_author", "save", {"name": "t", "save": False}),
    ("personalclaw.mcp_workflows", "workflow_edit", "preview_only", {"preview_only": True}),
    ("personalclaw.mcp_workflows", "workflow_audit", "dry_run", {"dry_run": False}),
    ("personalclaw.mcp_automation", "automation_run", "dry_run", {"id": "x", "dry_run": True}),
]


@pytest.mark.parametrize(
    "module,tool,argument,args", _PREVIEW_ARGUMENTS, ids=[r[1] for r in _PREVIEW_ARGUMENTS]
)
def test_no_tool_switches_between_reading_and_changing_on_an_argument(module, tool, argument, args):
    """🔴 Before: each of these took the argument, so a call's effect was not its tool's
    declaration. The argument is gone and a call that sends it is refused, never run as the
    other half: `workflow_author` with `save=false` does not save, and `automation_run` with
    `dry_run` does not fire."""
    import importlib

    registry = importlib.import_module(module)
    schema = {t["name"]: t for t in registry._list_tools()}[tool]["inputSchema"]
    assert argument not in schema.get("properties", {})
    assert f"{argument}: unknown field" in registry._call_tool(tool, args)


# ── whoever built the definition ─────────────────────────────────────────────────────────


def test_a_definition_that_declares_a_read_asks_nobody_whoever_built_it():
    from personalclaw.tool_providers.base import ToolDefinition

    def asks(risk, requires_approval):
        return ToolDefinition(
            name="t", description="d", risk_level=risk, requires_approval=requires_approval
        ).requires_approval

    assert asks(RiskLevel.SAFE, True) is False
    assert asks("safe", True) is False  # a constructor that passes the level's value
    assert asks(RiskLevel.CAUTION, True) is True
    assert asks(RiskLevel.DESTRUCTIVE, True) is True
    assert asks(RiskLevel.CAUTION, False) is False  # a change may still say it asks nobody


class _AppProvider(ToolProvider):
    """An app's provider that declares a read and says it asks, as the MCP server adapter did."""

    def __init__(self, risk: RiskLevel) -> None:
        self._risk = risk
        self.invoked: list[str] = []

    @property
    def name(self) -> str:
        return "an-app"

    @property
    def display_name(self) -> str:
        return "An app"

    async def list_tools(self):
        from personalclaw.tool_providers.base import ToolDefinition

        return [
            ToolDefinition(
                name="docs_search",
                description="Search the docs.",
                provider="an-app",
                requires_approval=True,
                risk_level=self._risk,
            )
        ]

    async def invoke(self, tool_name, arguments):
        self.invoked.append(tool_name)
        return ToolResult(success=True, output="ran")


@pytest.mark.asyncio
@pytest.mark.parametrize(("risk", "asked"), [(RiskLevel.SAFE, False), (RiskLevel.CAUTION, True)])
async def test_an_apps_declared_read_runs_unasked_and_its_change_asks(risk, asked):
    """🔴 Before: a trusted MCP server's read, which the adapter built with
    `requires_approval=True`, raised a card in a chat that runs every other read, and was
    declined wherever nobody could be asked."""
    provider = _AppProvider(risk)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_OneCall("docs_search", '{"query": "x"}'),
        tool_providers=[provider],
    )
    await rt.start()
    seen: list[str] = []

    async def drain() -> None:
        async for ev in rt.stream("go"):
            if ev.kind == EVENT_PERMISSION_REQUEST:
                seen.append(ev.title)
                await rt.reject_tool(ev.request_id)

    await asyncio.wait_for(drain(), timeout=10)
    assert (seen == ["docs_search"]) is asked
    assert (provider.invoked == ["docs_search"]) is not asked
