"""A tool a model request carries is one every mainstream provider documents it accepts.

The defect: an MCP server that lists a tool with no description made every Bedrock turn fail before
anything was sent. Converse declares ``toolSpec.description`` with a minimum length of 1, the
native loop wrote a missing description as ``""``, and the AWS client refused the whole request
("Invalid length for parameter toolConfig.tools[0].toolSpec.description, value: 0, valid min
length: 1"). One server with a sparse tool list broke every chat turn on Bedrock.

The same seam let through a second shape that fails a whole turn: an external MCP tool is named
``mcp/<server>/<tool>``, and Converse, the Messages API and OpenAI function calling all take a tool
name of letters, digits, ``_`` and ``-`` only (64 characters on Converse and OpenAI, 128 on the
Messages API).

So every tool is made valid where the turn's tools are assembled, once for every provider: a tool
with no description is offered with one written from what is known (its name and its inputs, and
saying that its source gave none), every name travels in the form all three accept, and a tool that
cannot be made valid is left out of the request with a logged reason instead of failing the turn.

Each wire is checked against its own published contract, never against the module that does the
repair: Converse against the AWS SDK's own service model for ``ConverseStream`` (the validator that
raised the error above, plus the name pattern and length the same model declares), the Messages API
and OpenAI against the name rules their references publish.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TOOL_CALL, AgentEvent
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult


def _tool(name: str, description: str, parameters: dict | None = None) -> ToolDefinition:
    """A tool that asks nobody before it runs, so a scripted call runs straight away."""
    return ToolDefinition(
        name=name, description=description, parameters=parameters or {}, requires_approval=False
    )


_LOOKUP_PARAMS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "the text to find"},
        "limit": {"type": "integer"},
    },
    "required": ["query"],
}

#: What a server with a sparse tool list hands the gateway: one tool with no description, one
#: with only whitespace, and one that describes itself.
_SPARSE = [
    _tool("mcp/notes/lookup", "", _LOOKUP_PARAMS),
    _tool("mcp/notes/ping", "   "),
    _tool(
        "mcp/notes/search",
        "Search the notes.",
        {"type": "object", "properties": {"q": {"type": "string"}}},
    ),
]


class _Server(ToolProvider):
    """An external server's tools, as the MCP provider surfaces them."""

    def __init__(self, tools: list[ToolDefinition], name: str = "mcp") -> None:
        self._tools = tools
        self._name = name
        self.invoked: list[tuple[str, dict]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "MCP Servers"

    async def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools)

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.invoked.append((tool_name, arguments))
        return ToolResult(success=True, output=f"ran {tool_name}")


class _Model:
    """A tool-capable model that records each ``tools=`` payload and replays scripted turns."""

    supports_tools = True

    def __init__(self, turns: list[list[AgentEvent]] | None = None) -> None:
        self._turns = turns or [[AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")]]
        self.payloads: list[list[dict[str, Any]]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.payloads.append(list(tools or []))
        turn = self._turns[min(len(self.payloads), len(self._turns)) - 1]
        for event in turn:
            yield event


@pytest.fixture(autouse=True)
def _fresh_reports(monkeypatch):
    from personalclaw.tool_providers import portable_schema

    monkeypatch.setattr(portable_schema, "_reported", set())


def _turn(tools: list[ToolDefinition], model: _Model | None = None) -> tuple[_Model, _Server]:
    model = model or _Model()
    server = _Server(tools)
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="sparse", provider="native", model="m"),
        model_provider=model,
        tool_providers=[server],
    )

    async def _go() -> None:
        await runtime.start()
        async for _ in runtime.stream("look something up"):
            pass

    asyncio.run(_go())
    return model, server


def _functions(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry["function"] for entry in payload]


# ── each provider's published contract, applied to what a turn hands it ──────


def _converse_problems(payload: list[dict[str, Any]]) -> list[str]:
    """What the AWS SDK and Bedrock refuse in the Converse request a turn's payload becomes.

    The request is built field for field as a Converse adapter maps the OpenAI-shaped payload
    (``toolSpec`` = name, description, ``inputSchema.json``). The AWS SDK's validator checks it
    client-side, exactly as it did when the turn failed; it does not apply ``pattern`` or ``max``,
    which the service enforces, so those are read from the same service model and applied here.
    """
    botocore_session = pytest.importorskip("botocore.session")
    from botocore.validate import ParamValidator

    operation = (
        botocore_session.get_session()
        .get_service_model("bedrock-runtime")
        .operation_model("ConverseStream")
    )
    request = {
        "modelId": "a-model",
        "messages": [{"role": "user", "content": [{"text": "look something up"}]}],
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": fn["name"],
                        "description": fn.get("description", "") or "",
                        "inputSchema": {"json": fn["parameters"]},
                    }
                }
                for fn in _functions(payload)
            ]
        },
    }
    report = ParamValidator().validate(request, operation.input_shape)
    problems = report.generate_report().splitlines() if report.has_errors() else []
    spec = operation.input_shape.members["toolConfig"].members["tools"].member.members["toolSpec"]
    for i, tool in enumerate(request["toolConfig"]["tools"]):
        for field in ("name", "description"):
            meta = spec.members[field].metadata
            value = tool["toolSpec"][field]
            if "max" in meta and len(value) > meta["max"]:
                problems.append(
                    f"tools[{i}].toolSpec.{field}: longer than {meta['max']}: {value!r}"
                )
            if "pattern" in meta and not re.fullmatch(meta["pattern"], value):
                problems.append(
                    f"tools[{i}].toolSpec.{field}: outside {meta['pattern']}: {value!r}"
                )
    return problems


#: The Messages API's custom tool: ``name`` matches ``^[a-zA-Z0-9_-]{1,128}$`` and
#: ``input_schema`` is an object schema.
_MESSAGES_API_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
#: OpenAI function calling: a name of a-z, A-Z, 0-9, underscores and dashes, at most 64 long.
_OPENAI_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _messages_api_problems(payload: list[dict[str, Any]]) -> list[str]:
    from personalclaw.llm.anthropic import _translate_tools

    problems = []
    for tool in _translate_tools(payload):
        if not _MESSAGES_API_NAME.fullmatch(tool["name"]):
            problems.append(f"name {tool['name']!r} is outside ^[a-zA-Z0-9_-]{{1,128}}$")
        if tool["input_schema"].get("type") != "object":
            problems.append(f"{tool['name']}: input_schema is not an object schema")
    return problems


def _openai_problems(payload: list[dict[str, Any]]) -> list[str]:
    problems = []
    for fn in _functions(payload):
        if not _OPENAI_NAME.fullmatch(fn["name"]):
            problems.append(f"name {fn['name']!r} is outside ^[a-zA-Z0-9_-]{{1,64}}$")
        if not isinstance(fn.get("description", ""), str):
            problems.append(f"{fn['name']}: description is not a string")
    return problems


def _duplicate_names(payload: list[dict[str, Any]]) -> list[str]:
    names = [fn["name"] for fn in _functions(payload)]
    return sorted({n for n in names if names.count(n) > 1})


class TestEveryWireAcceptsTheTurn:
    def test_a_turn_with_a_tool_that_has_no_description_builds_a_valid_converse_request(self):
        model, _ = _turn(_SPARSE)
        [payload] = model.payloads
        assert len(payload) == 3, "every tool of the sparse server is offered"
        assert not _converse_problems(payload), _converse_problems(payload)

    def test_the_same_turn_meets_the_messages_api_tool_contract(self):
        model, _ = _turn(_SPARSE)
        assert not _messages_api_problems(model.payloads[0]), _messages_api_problems(
            model.payloads[0]
        )

    def test_the_same_turn_meets_openais_function_contract(self):
        model, _ = _turn(_SPARSE)
        payload = model.payloads[0]
        assert not _openai_problems(payload), _openai_problems(payload)
        assert not _duplicate_names(payload)

    def test_the_control_the_converse_check_refuses_what_the_turn_used_to_send(self):
        """The check is real: the payload a turn sent before this fix fails it with the AWS SDK's
        own sentence."""
        before = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": p},
            }
            for t, p in zip(_SPARSE, (_LOOKUP_PARAMS, {"type": "object", "properties": {}}, {}))
        ]
        problems = _converse_problems(before)
        assert (
            "Invalid length for parameter toolConfig.tools[0].toolSpec.description, value: 0, "
            "valid min length: 1"
        ) in problems
        assert any("toolSpec.name: outside" in p for p in problems)
        assert _messages_api_problems(before) and _openai_problems(before)


class TestWhatTheModelIsTold:
    def test_a_missing_description_is_written_from_the_tools_name_and_inputs(self):
        model, _ = _turn(_SPARSE)
        by_name = {fn["name"]: fn for fn in _functions(model.payloads[0])}
        lookup = by_name["mcp_notes_lookup"]["description"]
        assert '"mcp/notes/lookup"' in lookup, "it names the tool, server included"
        assert "no description" in lookup, "it says its source gave none"
        assert "query (string, required) — the text to find" in lookup
        assert "limit (integer)" in lookup
        ping = by_name["mcp_notes_ping"]["description"]
        assert '"mcp/notes/ping"' in ping and "no arguments" in ping

    def test_a_described_tool_keeps_its_own_words(self):
        model, _ = _turn(_SPARSE)
        by_name = {fn["name"]: fn for fn in _functions(model.payloads[0])}
        assert by_name["mcp_notes_search"]["description"] == "Search the notes."

    def test_the_model_calls_the_name_it_was_given_and_the_real_tool_runs(self):
        model = _Model(
            [
                [
                    AgentEvent(
                        kind=EVENT_TOOL_CALL,
                        tool_call_id="c1",
                        title="mcp_notes_lookup",
                        tool_input=json.dumps({"query": "plans"}),
                    ),
                    AgentEvent(kind=EVENT_COMPLETE, stop_reason="tool_use"),
                ],
                [AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")],
            ]
        )
        model, server = _turn(_SPARSE, model)
        given = {fn["name"] for fn in _functions(model.payloads[0])}
        assert "mcp_notes_lookup" in given, sorted(given)
        assert server.invoked == [("mcp/notes/lookup", {"query": "plans"})]
        # The next request of the turn still meets the contract: same names, same tools.
        assert not _converse_problems(model.payloads[1]), _converse_problems(model.payloads[1])


class TestAToolThatCannotBeMadeValid:
    def test_two_names_that_meet_on_the_wire_are_left_out_and_the_turn_runs(self, caplog):
        """``mcp/a_b/x`` and ``mcp/a.b/x`` would both travel as ``mcp_a_b_x``, so a call to that
        name could not be told apart: neither is offered, and the rest of the turn runs."""
        caplog.set_level("WARNING", logger="personalclaw.agents.native.tool_names")
        tools = [
            _tool("mcp/a_b/x", "one"),
            _tool("mcp/a.b/x", "two"),
            _tool("mcp/kept/y", "kept"),
        ]
        model, _ = _turn(tools)
        names = [fn["name"] for fn in _functions(model.payloads[0])]
        assert names == ["mcp_kept_y"]
        [line] = [r.getMessage() for r in caplog.records if "NOT offered" in r.getMessage()]
        assert "'mcp/a.b/x'" in line and "'mcp/a_b/x'" in line and "'mcp_a_b_x'" in line

    def test_a_name_already_in_the_accepted_form_keeps_it_over_one_that_would_take_it(self):
        tools = [
            _tool("mcp_x", "legal as it is"),
            _tool("mcp/x", "would travel as mcp_x"),
        ]
        model, _ = _turn(tools)
        assert [fn["name"] for fn in _functions(model.payloads[0])] == ["mcp_x"]
        assert [fn["description"] for fn in _functions(model.payloads[0])] == ["legal as it is"]

    def test_a_tool_named_as_the_runtimes_own_is_left_out(self, caplog):
        """``tool_search`` and ``tool_schema`` are the runtime's (they ride a reduced turn), so a
        provider's tool called that, or sent under that name, could never be called, and a request
        carrying both would name a tool twice."""
        caplog.set_level("WARNING", logger="personalclaw.agents.native.tool_names")
        tools = [
            _tool("tool_search", "an app's own search"),
            _tool("tool/schema", "would be sent as tool_schema"),
            _tool("mcp/notes/search", "Search."),
        ]
        model, _ = _turn(tools)
        assert [fn["name"] for fn in _functions(model.payloads[0])] == ["mcp_notes_search"]
        lines = [r.getMessage() for r in caplog.records if "NOT offered" in r.getMessage()]
        assert any("'tool_search'" in line for line in lines), lines
        assert any("'tool/schema'" in line for line in lines), lines

    def test_a_tool_with_no_name_is_left_out_and_logged(self, caplog):
        caplog.set_level("WARNING", logger="personalclaw.tool_providers.portable_schema")
        tools = [
            _tool("", "nameless"),
            _tool("mcp/notes/search", "Search."),
        ]
        model, _ = _turn(tools)
        assert [fn["name"] for fn in _functions(model.payloads[0])] == ["mcp_notes_search"]
        assert any("has no name" in r.getMessage() for r in caplog.records)

    def test_a_name_longer_than_every_provider_takes_travels_cut_to_64_and_still_runs(self):
        long_name = "mcp/notes/" + "a" * 80
        model = _Model(
            [
                [
                    AgentEvent(
                        kind=EVENT_TOOL_CALL,
                        tool_call_id="c1",
                        title=("mcp_notes_" + "a" * 80)[:64],
                        tool_input="{}",
                    ),
                    AgentEvent(kind=EVENT_COMPLETE, stop_reason="tool_use"),
                ],
                [AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")],
            ]
        )
        model, server = _turn([_tool(long_name, "long")], model)
        [fn] = _functions(model.payloads[0])
        assert len(fn["name"]) == 64
        assert not _converse_problems(model.payloads[0])
        assert server.invoked == [(long_name, {})]


class TestTheSeam:
    def test_a_derived_description_is_logged_once_and_the_source_is_not_changed(self, caplog):
        from personalclaw.tool_providers.portable_schema import offered_tool_definitions

        caplog.set_level("WARNING", logger="personalclaw.tool_providers.portable_schema")
        source = _tool("mcp/notes/lookup", "", _LOOKUP_PARAMS)
        [offered] = offered_tool_definitions([source], provider="mcp", app="mcp-tools")
        offered_tool_definitions([source], provider="mcp", app="mcp-tools")
        assert source.description == "", "the provider's own definition is never mutated"
        assert offered.description.startswith('"mcp/notes/lookup" came with no description')
        lines = [r.getMessage() for r in caplog.records]
        assert len(lines) == 1, lines
        assert "app 'mcp-tools' (provider 'mcp') tool 'mcp/notes/lookup'" in lines[0]
        assert "no description" in lines[0]

    def test_the_description_summary_is_bounded(self):
        from personalclaw.tool_providers.portable_schema import derived_description

        wide = {
            "type": "object",
            "properties": {
                f"p{i}": {"type": "string", "description": "x" * 500} for i in range(40)
            },
        }
        text = derived_description("mcp/wide/tool", wide)
        assert len(text) <= 1200
        assert "and 28 more" in text


class TestTheToolsPage:
    def test_a_tool_with_no_description_shows_the_text_models_are_shown(self, monkeypatch):
        """The Tools page lists an external server's tool with the description a model is offered,
        which says that none came with it — never a blank row."""
        import personalclaw.dashboard.handlers.tools as tools_mod
        from personalclaw.tool_providers.portable_schema import offered_tool_definitions

        class _ServerTool:
            name = "lookup"
            description = ""
            input_schema = _LOOKUP_PARAMS

        class _Connection:
            async def list_tools(self):
                return [_ServerTool()]

        class _Servers:
            def items(self):
                return {"notes": _Connection()}.items()

        async def _no_registry_tools():
            return []

        monkeypatch.setattr(
            "personalclaw.mcp_client.get_mcp_client_registry", lambda: _Servers(), raising=False
        )
        monkeypatch.setattr(
            "personalclaw.tool_providers.registry.list_all_tools", _no_registry_tools
        )
        response = asyncio.run(tools_mod.api_tools_list(object()))
        rows = {row["name"]: row for row in json.loads(response.body.decode())["tools"]}
        [offered] = offered_tool_definitions(
            [_tool("mcp/notes/lookup", "", _LOOKUP_PARAMS)], provider="mcp"
        )
        assert rows["mcp/notes/lookup"]["description"] == offered.description
        assert "came with no description" in offered.description


class TestTheCatalog:
    def test_the_catalog_says_why_a_tool_sent_under_a_taken_name_is_not_offered(self):
        from personalclaw.tool_providers import registry

        provider = _Server(
            [_tool("demo/a_b/x", "one"), _tool("demo/a.b/x", "two"), _tool("demo/kept", "kept")],
            name="demo-clash",
        )
        registry.register_provider(provider, app="demo-app")
        registry.clear_load_failures()
        try:
            tools = asyncio.run(registry.list_all_tools())
            assert {"demo/a_b/x", "demo/a.b/x", "demo/kept"} <= {t.name for t in tools}
            [failure] = [f for f in registry.get_load_failures() if f["provider"] == "demo-clash"]
            assert "not offered to models" in failure["error"]
            assert "demo/a_b/x (sent as 'demo_a_b_x')" in failure["error"]
            assert "demo/a.b/x (sent as 'demo_a_b_x')" in failure["error"]
            assert "demo/kept" not in failure["error"]
        finally:
            registry.unregister_provider(provider)
            registry.clear_load_failures()
