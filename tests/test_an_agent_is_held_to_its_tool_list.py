"""An agent is offered exactly the tools its tool list allows, and a call to any other is refused.

An agent's tool list (``agents.<name>.tools``, Tools on the Agents page) is a least-privilege
setting: "Tools this agent may call. None selected = all available tools." Measured on a running
gateway before this: the list was copied into the runtime's definition and read by nothing, so two
agents that declared different lists were both offered the same full set of tools, and either could
call any of them.

Now PersonalClaw's own runtime reads the list where it builds a turn's tools
(``NativeAgentRuntime._build_catalog``), so the model is shown only what the list allows, and
refuses a call to anything else before anyone is asked (``_guard_and_invoke``), whatever would
otherwise have answered it: the session's approval policy, a dry run's "would have run". An entry
is a tool's name or a pattern over it. An agent with no list keeps every tool, the default agent's
list holds a chat that names no agent, a built-in agent is held to the list PersonalClaw gives it,
and a list that cannot be read allows nothing.

Every runtime here is built by the production factory (``provider_bridge._build_native_runtime``)
over the platform's real file and shell tools and one provider of the test's own. Only the model is
a fake: it calls what each test scripts and keeps the tool block each request carried.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from personalclaw.agents.native.tool_names import model_safe_name
from personalclaw.config.loader import AgentProfile, AppConfig
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    AgentEvent,
)
from personalclaw.providers import provider_bridge as pb
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

#: The platform's own tools, which every native session is built with.
PLATFORM = {
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "glob",
    "grep",
    "repo_map",
    "bash",
    "tool_result_get",
}
NOTES = {"notes_read", "notes_write"}
SERVER = {"mcp/fixture/hello", "mcp/fixture/goodbye"}
EVERY_TOOL = PLATFORM | NOTES | SERVER


class _Notes(ToolProvider):
    """A provider of the test's own: a note it only reads, and one it writes without asking."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    @property
    def name(self) -> str:
        return "notes"

    @property
    def display_name(self) -> str:
        return "Notes"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="notes_read",
                description="Read the shared notes.",
                parameters={"type": "object", "properties": {}},
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
            ),
            ToolDefinition(
                name="notes_write",
                description="Write to the shared notes.",
                parameters={"type": "object", "properties": {"text": {"type": "string"}}},
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
            ),
            *(
                ToolDefinition(
                    name=name,
                    description=f"{name.rsplit('/', 1)[-1]} from the fixture server.",
                    parameters={"type": "object", "properties": {}},
                    requires_approval=False,
                    risk_level=RiskLevel.SAFE,
                )
                for name in sorted(SERVER)
            ),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.ran.append(tool_name)
        return ToolResult(success=True, output=f"{tool_name} done")


class _Model:
    """A model that makes the scripted calls on its first request, then answers, and keeps the
    tool names each request carried (in the form a request names them)."""

    supports_tools = True
    _model = "recorder"

    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self._calls = calls
        self.offered: list[set[str]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.offered.append({t["function"]["name"] for t in tools or []})
        if len(self.offered) == 1 and self._calls:
            for i, (name, args) in enumerate(self._calls):
                yield AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id=f"c{i}",
                    title=name,
                    tool_input=json.dumps(args),
                )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="done")
        yield AgentEvent(kind=EVENT_COMPLETE)


def _sent(names: set[str]) -> set[str]:
    """*names* as a request carries them."""
    return {model_safe_name(n) for n in names}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A home with the agents the tests name, a workspace, and the tool surface every native
    session here is built from: the platform's tools and the provider of the test's own."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    notes = _Notes()
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_providers", lambda: [notes])
    monkeypatch.setattr(pb, "_fallback_chat_model", lambda **_k: "recorder")
    cfg = AppConfig.load()
    cfg.agents.update(
        {
            "researcher": AgentProfile(provider="native", tools=["read_file", "notes_*"]),
            "writer": AgentProfile(provider="native", tools=["write_file"]),
            "helper": AgentProfile(provider="native"),
            "listener": AgentProfile(provider="native", tools=["mcp/fixture/*", "READ_FILE"]),
        }
    )
    cfg.save()
    return {"workspace": workspace, "notes": notes}


async def _turn(
    monkeypatch: pytest.MonkeyPatch,
    agent: str | None,
    calls: list[tuple[str, dict]] | None = None,
    *,
    policy: str = "yolo",
    **build: Any,
) -> tuple[_Model, list[AgentEvent]]:
    """One turn of *agent*'s runtime, built by the production factory, whose model makes *calls*.
    *policy* is the session's approval policy: ``yolo`` answers every ask, so nothing asks."""
    model = _Model(list(calls or []))
    monkeypatch.setattr(pb, "resolve_provider_for_use_case", lambda *a, **k: model)
    runtime = pb._build_native_runtime(
        use_case="chat",
        session_key=f"dashboard:{agent or 'default'}",
        agent=agent,
        model_override=None,
        cwd=None,
        **build,
    )
    runtime.set_approval_policy(policy)
    await runtime.start()
    events = [event async for event in runtime.stream("go")]
    return model, events


def _results(events: list[AgentEvent]) -> dict[str, AgentEvent]:
    return {e.title: e for e in events if e.kind == EVENT_TOOL_RESULT}


@pytest.mark.asyncio
async def test_two_agents_with_different_lists_are_offered_different_tools(
    world, monkeypatch, caplog
):
    """🔴 Red before: both were offered every tool. The agent with no list is the control: it keeps
    every tool, as every agent did before."""
    caplog.set_level(logging.INFO, logger="personalclaw.agents.tool_list")
    researcher, _ = await _turn(monkeypatch, "researcher")
    writer, _ = await _turn(monkeypatch, "writer")
    helper, _ = await _turn(monkeypatch, "helper")

    assert researcher.offered[0] == _sent({"read_file", "tool_result_get"} | NOTES)
    assert writer.offered[0] == _sent({"write_file", "tool_result_get"})
    assert helper.offered[0] == _sent(EVERY_TOOL)
    # The gateway log says what each narrowed catalog keeps of what was on offer, as a routine
    # line: every entry of these lists matches a tool.
    lines = {r.getMessage(): r.levelno for r in caplog.records}
    (researchers,) = [m for m in lines if "the agent researcher may use 4 of the 13 tools" in m]
    (writers,) = [m for m in lines if "the agent writer may use 2 of the 13 tools on offer" in m]
    assert lines[researchers] == lines[writers] == logging.INFO
    assert not [m for m in lines if "the agent helper may use" in m]


@pytest.mark.asyncio
async def test_an_entry_is_a_name_or_a_pattern_over_the_whole_name(world, monkeypatch, caplog):
    """``mcp/fixture/*`` is every tool of that server; an entry is matched case-sensitively, so
    ``READ_FILE`` is no tool here, and the log says so."""
    caplog.set_level(logging.INFO, logger="personalclaw.agents.tool_list")
    listener, _ = await _turn(monkeypatch, "listener")
    assert listener.offered[0] == _sent(SERVER | {"tool_result_get"})
    # The list allows less than it says, which the gateway log's default level shows.
    (said,) = [r for r in caplog.records if "match no tool on offer" in r.getMessage()]
    assert "its entries READ_FILE match no tool on offer" in said.getMessage()
    assert said.levelno == logging.WARNING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "build",
    [
        pytest.param({}, id="its-approval-policy-answers-every-ask"),
        pytest.param({"dry_run": True}, id="a-dry-run"),
    ],
)
async def test_a_call_to_a_tool_outside_the_list_is_refused_and_never_runs(
    world, monkeypatch, caplog, build
):
    """🔴 Red before: the researcher's ``write_file`` and ``notes_write`` ran (a dry run said they
    "would have run here"). A model can name a tool it was not shown, from an earlier turn, a
    document or a guess, and the name must not reach it. The researcher's own tool beside them runs
    (the control)."""
    workspace: Path = world["workspace"]
    caplog.set_level(logging.WARNING, logger="personalclaw.agents.tool_list")
    _model, events = await _turn(
        monkeypatch,
        "researcher",
        [
            ("write_file", {"path": "draft.md", "content": "a note"}),
            ("bash", {"command": "echo hi > shell.txt"}),
            ("notes_read", {}),
        ],
        **build,
    )
    results = _results(events)
    for tool in ("write_file", "bash"):
        refused = results[tool]
        said = str(refused.tool_output)
        assert said.startswith(f"Error: tool `{tool}` blocked by a security policy"), said
        assert (
            f"the agent researcher may use only the tools on its tool list, set on the Agents "
            f"page, and {tool} is not one of them"
        ) in said
        assert refused.tool_meta["refused_by"] == "agent_tools"
        assert refused.tool_meta["ok"] is False
    assert not (workspace / "draft.md").exists() and not (workspace / "shell.txt").exists()
    assert "notes_read done" in str(results["notes_read"].tool_output)
    assert world["notes"].ran == ["notes_read"]
    # Each refusal is one WARNING line, which the gateway log's default level shows.
    refusals = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert refusals == [
        f"native: refused {tool}, which the agent researcher may not use"
        for tool in ("write_file", "bash")
    ]


@pytest.mark.asyncio
async def test_a_tool_it_was_not_shown_has_no_schema_to_find_either(world, monkeypatch):
    """The runtime's own discovery tools search only what the list allows: asked for the schema
    of a tool the list leaves out, ``tool_schema`` answers that there is none. 🔴 Red before: it
    handed back the schema."""
    _model, events = await _turn(
        monkeypatch,
        "writer",
        [
            ("tool_schema", {"tool_name": "notes_write"}),
            ("tool_schema", {"tool_name": "write_file"}),
        ],
    )
    answers = [str(e.tool_output) for e in events if e.kind == EVENT_TOOL_RESULT]
    assert answers[0].startswith("No tool named 'notes_write'"), answers[0]
    assert '"name": "write_file"' in answers[1]


@pytest.mark.asyncio
async def test_the_default_agents_list_holds_a_chat_that_names_no_agent(world, monkeypatch):
    """A chat that names no agent runs as the default agent, so the list set on it is that chat's.
    🔴 Red before: such a chat read no agent's list at all."""
    cfg = AppConfig.load()
    cfg.agents[cfg.default_agent].tools = ["read_file", "notes_read"]
    cfg.save()
    model, events = await _turn(monkeypatch, None, [("notes_write", {"text": "x"})])
    assert model.offered[0] == _sent({"read_file", "notes_read", "tool_result_get"})
    refused = _results(events)["notes_write"]
    assert refused.tool_meta["refused_by"] == "agent_tools"
    assert f"the agent {cfg.default_agent} may use only" in str(refused.tool_output)
    assert world["notes"].ran == []


@pytest.mark.asyncio
async def test_the_built_in_template_refiner_is_held_to_its_read_and_propose_pair(
    world, monkeypatch
):
    """The one built-in agent with a list: the template refiner, which the refine-template and
    optimize-harness workflows run, may use PersonalClaw's evidence and proposal tools and nothing
    else, and its instructions there tell it that its tool set enforces that. Its list is
    PersonalClaw's own, which no page edits, so the refusal does not send anyone to the Agents
    page. 🔴 Red before: it was offered every tool, the platform's file and shell tools among
    them. Built over the real core tool surface, which serves the pair."""
    from personalclaw.agents.defaults import TEMPLATE_REFINER_AGENT_NAME, TEMPLATE_REFINER_TOOLS
    from personalclaw.tool_providers.registry import create_native_provider

    core = create_native_provider()
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.list_providers", lambda: [core, world["notes"]]
    )
    model, events = await _turn(
        monkeypatch,
        TEMPLATE_REFINER_AGENT_NAME,
        [("read_file", {"path": "x.md"}), ("refiner_evidence", {"workflow_name": "absent"})],
    )
    assert model.offered[0] == _sent({*TEMPLATE_REFINER_TOOLS, "tool_result_get"})
    results = _results(events)
    refused = results["read_file"]
    said = str(refused.tool_output)
    assert (
        f"the built-in agent {TEMPLATE_REFINER_AGENT_NAME} may use only the tools PersonalClaw "
        "gives it, and read_file is not one of them"
    ) in said, said
    assert "Agents page" not in said
    assert refused.tool_meta["refused_by"] == "agent_tools"
    # Its evidence tool, on its list, reached the tool.
    assert "refused_by" not in results["refiner_evidence"].tool_meta


@pytest.mark.asyncio
async def test_a_list_that_cannot_be_read_allows_nothing_and_says_why(world, monkeypatch, caplog):
    """A hand-edited list that is not a list of names: what it was meant to allow is unknown, so
    the agent may use nothing but read back an answer, and the refusal says where to fix it."""
    from personalclaw.config import loader as config_loader

    raw = json.loads(config_loader.config_path().read_text(encoding="utf-8"))
    raw["agents"]["researcher"]["tools"] = "read_file"
    config_loader.config_path().write_text(json.dumps(raw), encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="personalclaw.agents.tool_list")

    model, events = await _turn(monkeypatch, "researcher", [("read_file", {"path": "x.md"})])
    assert model.offered[0] == _sent({"tool_result_get"})
    said = str(_results(events)["read_file"].tool_output)
    assert "the tool list of the agent researcher could not be read" in said
    assert "fixed on the Agents page" in said
    assert "it is not a list of tool names: str" in caplog.text


@pytest.mark.asyncio
async def test_while_the_configuration_file_cannot_be_read_no_agent_may_use_a_tool(
    world, monkeypatch, caplog
):
    """A ``config.json`` cut short is discarded for the defaults, which know nothing of the lists
    set in it: read as the defaults, the researcher (no profile there) and the default agent (no
    list there) would each have every tool. Every list in the file is unknown, so every agent may
    use only ``tool_result_get``, and the refusal says why."""
    from personalclaw.config import loader as config_loader

    # The discard is remembered process-wide until the next read; leave none behind.
    monkeypatch.setattr(config_loader, "_LAST_CONFIG_DISCARD", None)
    path = config_loader.config_path()
    path.write_text(path.read_text(encoding="utf-8")[:64], encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="personalclaw.agents.tool_list")

    researcher, events = await _turn(monkeypatch, "researcher", [("read_file", {"path": "x.md"})])
    default, _ = await _turn(monkeypatch, None)
    assert researcher.offered[0] == default.offered[0] == _sent({"tool_result_get"})
    said = str(_results(events)["read_file"].tool_output)
    assert (
        "the tool list of the agent researcher could not be read (the configuration file could "
        "not be read), so it may use no tool until the configuration file is repaired"
    ) in said, said
    assert "the tool list of the agent researcher could not be read" in caplog.text


@pytest.mark.asyncio
async def test_a_runtime_built_with_no_list_offers_every_tool(world, monkeypatch):
    """The control on the definition itself: a runtime whose definition names no list (every
    caller that builds one by hand) is offered everything on its surface, as before."""
    from personalclaw.agents.native.builtin_tools import (
        PLATFORM_CATEGORIES,
        PLATFORM_PROVIDER_NAME,
        NativeBuiltinToolProvider,
    )
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition

    model = _Model([])
    platform = NativeBuiltinToolProvider(
        cwd=world["workspace"],
        session_key="dashboard:by-hand",
        categories=PLATFORM_CATEGORIES,
        provider_name=PLATFORM_PROVIDER_NAME,
    )
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="by-hand", model="recorder"),
        model_provider=model,  # type: ignore[arg-type]
        tool_providers=[platform, world["notes"]],
        cwd=world["workspace"],
    )
    await runtime.start()
    _ = [event async for event in runtime.stream("go")]
    assert model.offered[0] == _sent(EVERY_TOOL)
