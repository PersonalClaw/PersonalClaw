"""A turn carries the tool schemas its request needs, and no more than its window affords.

Measured on a local model serving a 32,768-token window: a chat turn whose request was three
bullets about two pasted logs sent 50 full tool schemas — 43,191 characters, more than the rest
of the 78 KB request — and the model took about two minutes to read it before its first word.
Two causes. The catalog was ranked against the whole assembled prompt, so every tool the
prompt's own boilerplate names ("run", "task", a path, "remember") looked relevant to every
turn; and nothing bounded the schemas by the window, only by a count sized for a catalog a
quarter of today's.
"""

from __future__ import annotations

import asyncio
import json

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tool_retrieval import ToolRetriever
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.context import USER_REQUEST_MARKER
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

#: The tools every turn keeps whatever it asks (``tool_retrieval._CORE_NAMES``).
_CORE = ("bash", "read_file", "write_file", "edit_file", "grep", "glob", "list_dir")


def _tool(name: str, size: int, description: str = "") -> ToolDefinition:
    """A tool whose schema is about ``size`` characters, the way a real one's grows: arguments."""
    properties = {}
    for n in range(max(1, size // 120)):
        properties[f"arg{n}"] = {"type": "string", "description": "x" * 80}
    return ToolDefinition(
        name=name,
        description=description or f"Does {name.replace('_', ' ')}.",
        parameters={"type": "object", "properties": properties},
        requires_approval=False,
        risk_level=RiskLevel.SAFE,
    )


def _catalog() -> list[ToolDefinition]:
    """About 120 tools, shaped like the product's own: the core, big run/automation tools, the
    task and memory families, the scheduling pair, and a long tail."""
    tools = [_tool(name, 600) for name in _CORE]
    tools += [
        _tool("project_run_create", 3000),
        _tool("project_run_start", 400),
        _tool("project_run_status", 400),
        _tool("automation_create", 2600),
        _tool("automation_run", 450),
        _tool("automation_dry_run", 400),
        _tool("subagent_run", 1400),
        _tool("workflow_run_from", 700),
        _tool("set_onetime_task", 2000, "Remind the user once at a given time."),
        _tool("set_recurring_task", 1400, "Run a prompt on a recurring schedule."),
        _tool("memory_remember", 1200),
        _tool("memory_forget", 300),
    ]
    tools += [_tool(f"task_{verb}", 900) for verb in ("create", "update", "list", "get", "search")]
    tools += [_tool(f"knowledge_tool_{n}", 700) for n in range(100)]
    return tools


class _Tools(ToolProvider):
    @property
    def name(self) -> str:
        return "catalog-under-test"

    @property
    def display_name(self) -> str:
        return "Catalog"

    async def list_tools(self):
        return _catalog()

    async def invoke(self, tool_name, arguments):
        return ToolResult(success=True, output="ok")


class _Model:
    """A local model that answers at once and records the tools it was sent."""

    supports_tools = True
    _model = "local-model"

    def __init__(self, window: int | None) -> None:
        self._window = window
        self.tools: list[dict] = []
        self.messages: list[dict] = []

    async def served_context_window(self) -> int | None:
        return self._window

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.tools = list(tools or [])
        self.messages = list(messages)
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="- one\n- two\n- three")
        yield AgentEvent(kind=EVENT_COMPLETE)


#: An assembled chat turn: the prompt's own boilerplate names running, tasks, memory, schedules
#: and paths; what the user asked names none of them.
_ASSEMBLED = (
    "[AGENT SYSTEM PROMPT]\nYou are the agent. Use bash to run commands; create a task for "
    "follow-ups; remember lessons with memory_remember; schedule a reminder every day if asked; "
    "files live under /data/workspace.\n[END AGENT SYSTEM PROMPT]\n\n"
    "[SESSION CONTEXT -- background reference only]\nToday's tasks: none.\n"
    "[END OF SESSION CONTEXT]\n\n"
    + USER_REQUEST_MARKER
    + "\nThree bullets: the failure pattern.\n\n"
    "ERROR fetch timed out after 10.0s\nERROR fetch timed out after 10.0s\n\n"
    "[WIDGETS] You can render rich HTML inline."
)


def _turn(window: int | None, message: str = _ASSEMBLED) -> _Model:
    model = _Model(window)
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="local-model"),
        model_provider=model,
        tool_providers=[_Tools()],
    )

    async def _run() -> None:
        await runtime.start()
        async for _ev in runtime.stream(message):
            pass

    asyncio.run(asyncio.wait_for(_run(), timeout=30))
    return model


def _sent(model: _Model) -> dict[str, int]:
    """The tools a turn sent in full, by name, with the characters each took."""
    return {t["function"]["name"]: len(json.dumps(t)) for t in model.tools}


def test_a_32k_turn_sends_no_more_schema_than_its_window_affords():
    sent = _sent(_turn(32_768))
    own = {n: c for n, c in sent.items() if n not in ("tool_search", "tool_schema")}

    # A 32,768-token window affords an eighth of itself, at four characters a token.
    assert sum(own.values()) <= 16_384, (sum(own.values()), sorted(own))
    assert set(_CORE) <= set(own), sorted(own)


def test_what_the_prompt_says_about_itself_does_not_pick_the_tools():
    """No budget at all (an unknown window): the request names no run, task or schedule, so no
    run, task or schedule tool rides in full — the prompt's boilerplate chose them before."""
    sent = _sent(_turn(None))

    for name in ("project_run_create", "automation_run", "subagent_run", "task_create"):
        assert name not in sent, sorted(sent)
    for name in _CORE:
        assert name in sent, sorted(sent)


def test_a_deferred_tool_is_still_listed_by_name():
    model = _turn(32_768)
    catalog = "\n".join(
        str(m.get("content", "")) for m in model.messages if m.get("role") == "system"
    )
    assert "project_run_create" in catalog and "tool_schema" in catalog


def test_a_request_to_be_reminded_gets_the_tool_that_reminds():
    message = _ASSEMBLED.replace(
        "Three bullets: the failure pattern.", "Remind me tomorrow at 9 to call the dentist."
    )
    assert "set_onetime_task" in _sent(_turn(32_768, message))


# ── the selector's rules, unit by unit ────────────────────────────────────────


def test_the_budget_is_an_eighth_of_the_window_and_none_without_one():
    from personalclaw.agents.native.tool_retrieval import schema_budget_chars

    assert schema_budget_chars(32_768) == 16_384
    assert schema_budget_chars(None) is None
    assert schema_budget_chars(0) is None


def test_on_a_large_window_the_budget_changes_nothing():
    from personalclaw.agents.native.tool_retrieval import schema_budget_chars

    retriever = ToolRetriever(_catalog())
    for query in ("Three bullets: the failure pattern.", "remind me to run the task tests"):
        unbounded = [d.name for d in retriever.select(query)]
        assert [
            d.name for d in retriever.select(query, budget_chars=schema_budget_chars(200_000))
        ] == unbounded


def test_the_core_rides_even_when_it_alone_is_over_the_budget():
    retriever = ToolRetriever(_catalog())
    names = {d.name for d in retriever.select("anything", budget_chars=100)}
    assert names == set(_CORE)


def test_a_tool_this_session_already_called_is_kept_first():
    """Room for the core and one more: the tool in use beats the ones the request hints at."""
    retriever = ToolRetriever(_catalog())
    retriever.mark_used("project_run_create")
    room = sum(retriever._chars[n] for n in (*_CORE, "project_run_create")) + 10
    names = {d.name for d in retriever.select("schedule it every day", budget_chars=room)}
    assert "project_run_create" in names
    assert not names & {"set_onetime_task", "set_recurring_task"}, sorted(names)


def test_only_a_shell_is_a_shell():
    """ "Run the tests" hints at the shells, not at every tool with "run" in its name."""
    tools = _catalog() + [_tool("run_script", 500), _tool("mcp__box__run_command", 500)]
    names = {d.name for d in ToolRetriever(tools).select("please run the tests")}

    assert {"run_script", "mcp__box__run_command", "bash"} <= names
    assert not names & {"project_run_create", "automation_run", "subagent_run", "workflow_run_from"}
