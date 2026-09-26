"""Rail: every tool schema a chat turn carries is valid for the strictest mainstream consumer.

The incident: a user whose chat model was Gemini (through an OpenAI-compatible router) could not
chat at all — every turn came back ``400 … function_declarations[15].parameters.properties
[deliverables].items: missing field`` because ``project_run_create`` declared five arrays with no
``items``. A provider validates the WHOLE tool block, so one bad schema fails every turn.

The rail builds the tool list the way a chat turn does —
:func:`provider_bridge._build_native_runtime` over the bundled apps' registered providers — and
checks it against the portable profile
(:mod:`personalclaw.tool_providers.portable_schema`) twice: every schema AS DECLARED (a built-in
must never lean on the seam's repairs), and every ``tools=`` payload a real turn hands the model.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from personalclaw.llm.events import EVENT_COMPLETE, AgentEvent
from personalclaw.tool_providers.portable_schema import schema_issues

# ── the tool list a chat turn builds ─────────────────────────────────────────


class _RecordingModel:
    """A tool-capable model that records every ``tools=`` payload it is handed."""

    supports_tools = True

    def __init__(self) -> None:
        self.tool_payloads: list[list[dict[str, Any]] | None] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.tool_payloads.append(tools)
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


@pytest.fixture
def bundled_tool_registry():
    """The tool-provider registry as gateway startup leaves it: every bundled app registered."""
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.providers import registry as prov_reg
    from personalclaw.providers.loader import BUNDLED_DIR
    from personalclaw.tool_providers import registry as tool_reg

    tool_reg._providers.clear()
    prov_reg._registry = None
    try:
        reg = prov_reg.get_provider_registry()
        for d in sorted(BUNDLED_DIR.iterdir()):
            mf = d / "app.json"
            if mf.exists():
                manifest = AppManifest.from_json_file(mf)
                if manifest.provider:
                    reg.register(manifest, enabled=True)
        yield tool_reg
    finally:
        tool_reg._providers.clear()
        prov_reg._registry = None


def _chat_runtime(monkeypatch, cwd, *, tool_groups=None):
    """The native runtime a chat turn builds, with only the model resolution faked."""
    from personalclaw.providers import provider_bridge

    model = _RecordingModel()
    monkeypatch.setattr(provider_bridge, "resolve_provider_for_use_case", lambda *a, **k: model)
    monkeypatch.setattr(provider_bridge, "_fallback_chat_model", lambda **k: "rail-model")
    runtime = provider_bridge._build_native_runtime(
        use_case="chat",
        session_key="schema-rail",
        agent=None,
        model_override=None,
        cwd=str(cwd),
        tool_groups=tool_groups,
    )
    return runtime, model


def _declared_tools(runtime) -> list[tuple[str, Any]]:
    """(provider, ToolDefinition) for every tool the runtime's providers declare, un-gated."""
    out: list[tuple[str, Any]] = []
    for prov in runtime._tool_providers:
        for tool in asyncio.run(prov.list_tools()):
            out.append((getattr(prov, "name", "?"), tool))
    return out


def _turn_payload(runtime, model, message: str = "hello") -> list[dict[str, Any]]:
    async def _one_turn() -> None:
        async for _ in runtime.stream(message):
            pass

    asyncio.run(_one_turn())
    payload = model.tool_payloads[-1]
    assert payload, "the turn carried no tools at all — the runtime did not assemble a tool block"
    return payload


def _wire_problems(payload: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    for entry in payload:
        fn = entry.get("function") or {}
        name = fn.get("name", "?")
        params = fn.get("parameters")
        if not isinstance(params, dict):
            problems.append(f"{name}: carries no `parameters` schema, which OpenRouter requires")
            continue
        problems += [f"{name}: {issue.render()}" for issue in schema_issues(params)]
    return problems


class TestTheRail:
    def test_every_declared_tool_schema_is_portable(
        self, bundled_tool_registry, monkeypatch, tmp_path
    ):
        """Every tool a chat turn's providers declare is inside the profile AS DECLARED."""
        runtime, _ = _chat_runtime(monkeypatch, tmp_path)
        declared = _declared_tools(runtime)
        assert len(declared) >= 90, f"the census is suspiciously small ({len(declared)})"
        problems = [
            f"{prov} :: {tool.name} — {issue.render()}"
            for prov, tool in declared
            for issue in schema_issues(tool.parameters)
        ]
        assert not problems, (
            "tool schemas outside the portable profile — a strict provider rejects the WHOLE "
            "request for any one of these:\n  " + "\n  ".join(problems)
        )

    def test_the_seam_changes_no_built_in_tool(self, bundled_tool_registry, monkeypatch, tmp_path):
        """The runtime offers every declared tool unchanged: no built-in leans on a repair.

        Compared by VALUE: the built-in providers build fresh definitions on every listing, so
        identity would call every one of them altered.
        """
        runtime, _ = _chat_runtime(monkeypatch, tmp_path)
        declared = {tool.name: tool for _, tool in _declared_tools(runtime)}
        asyncio.run(runtime.start())
        offered = {t.name: t for t in runtime._tool_defs}
        assert set(offered) == set(declared), sorted(set(declared) ^ set(offered))
        altered = [n for n, t in offered.items() if t.parameters != declared[n].parameters]
        assert not altered, f"the seam had to repair built-in schemas: {altered}"

    def test_a_full_turn_sends_only_portable_schemas(
        self, bundled_tool_registry, monkeypatch, tmp_path
    ):
        """The complete tool block (retrieval not reducing) is what the model is handed."""
        runtime, model = _chat_runtime(monkeypatch, tmp_path)
        asyncio.run(runtime.start())
        runtime._tool_retriever._k = 10**6  # surface every tool's schema this turn
        payload = _turn_payload(runtime, model)
        assert len(payload) == len(runtime._tool_defs)
        assert not _wire_problems(payload), _wire_problems(payload)

    def test_a_reduced_turn_sends_only_portable_schemas(
        self, bundled_tool_registry, monkeypatch, tmp_path
    ):
        """A retrieval-reduced turn adds the discovery tools; they must be portable too."""
        runtime, model = _chat_runtime(monkeypatch, tmp_path)
        asyncio.run(runtime.start())
        payload = _turn_payload(runtime, model)
        names = {e["function"]["name"] for e in payload}
        assert {"tool_search", "tool_schema"} <= names, "the census no longer reduces a turn"
        assert not _wire_problems(payload), _wire_problems(payload)

    def test_a_grouped_turn_sends_only_portable_schemas(
        self, bundled_tool_registry, monkeypatch, tmp_path
    ):
        """A grouped session carries the `reset_tools` meta-tool; it must be portable too."""
        runtime, model = _chat_runtime(monkeypatch, tmp_path, tool_groups=[])
        asyncio.run(runtime.start())
        payload = _turn_payload(runtime, model)
        names = {e["function"]["name"] for e in payload}
        assert "reset_tools" in names
        assert not _wire_problems(payload), _wire_problems(payload)


# ── the profile's rules ──────────────────────────────────────────────────────


def _obj(**props: Any) -> dict[str, Any]:
    return {"type": "object", "properties": props}


def _rules(params: Any) -> list[tuple[str, str, bool]]:
    """(rule, path, repaired) for every issue — the shape the assertions read."""
    return [(i.rule, i.path, bool(i.repair)) for i in schema_issues(params)]


class TestTheProfile:
    def test_the_incident_shape_is_blocking_and_names_its_path(self):
        from personalclaw.tool_providers.portable_schema import conform_parameters

        verdict = conform_parameters(_obj(deliverables={"type": "array"}))
        assert verdict.parameters is None
        assert _rules(_obj(deliverables={"type": "array"})) == [
            ("array_items_missing", "properties.deliverables", False)
        ]

    def test_every_defect_is_reported_in_one_pass(self):
        params = _obj(**{n: {"type": "array"} for n in ("a", "b", "c")}, d={"type": "object"})
        assert [(r, p) for r, p, _ in _rules(params)] == [
            ("array_items_missing", "properties.a"),
            ("array_items_missing", "properties.b"),
            ("array_items_missing", "properties.c"),
            ("object_properties_missing", "properties.d"),
        ]

    def test_a_portable_schema_passes_as_the_same_object(self):
        from personalclaw.tool_providers.portable_schema import conform_parameters

        params = _obj(
            q={"type": "string", "enum": ["a", "b"], "description": "x"},
            n={"type": "integer", "minimum": 1},
            tags={"type": "array", "items": {"type": "string"}, "maxItems": 5},
            when={"type": "string", "format": "date-time"},
            nested={"type": "object", "properties": {"k": {"type": "boolean"}}, "required": ["k"]},
            either={"anyOf": [{"type": "string"}, {"type": "integer"}]},
        )
        verdict = conform_parameters(params)
        assert verdict.issues == () and verdict.parameters is params

    @pytest.mark.parametrize("params", [None, {}, {"type": "object", "properties": {}}])
    def test_an_argument_less_root_is_portable(self, params):
        from personalclaw.tool_providers.portable_schema import conform_parameters

        assert schema_issues(params) == []
        assert conform_parameters(params).parameters == {"type": "object", "properties": {}}

    def test_a_nested_free_form_object_is_blocking(self):
        assert _rules(_obj(meta={"type": "object", "description": "anything"})) == [
            ("object_properties_missing", "properties.meta", False)
        ]

    def test_a_map_is_blocking_nested_and_at_the_root(self):
        nested = _obj(env={"type": "object", "additionalProperties": {"type": "string"}})
        root_map = {"type": "object", "additionalProperties": {"type": "string"}}
        assert _rules(nested) == [("object_properties_missing", "properties.env", False)]
        assert _rules(root_map) == [("object_properties_missing", "", False)]

    def test_enum_rules(self):
        assert _rules(_obj(n={"type": "integer", "enum": [1, 2]})) == [
            ("enum_invalid", "properties.n.enum", True)
        ]
        assert _rules(_obj(s={"type": "string", "enum": []})) == [
            ("enum_invalid", "properties.s.enum", False)
        ]
        assert _rules(_obj(s={"type": "string", "enum": ["a", 3]})) == [
            ("enum_invalid", "properties.s.enum", False)
        ]
        from personalclaw.tool_providers.portable_schema import conform_parameters

        repaired = conform_parameters(_obj(s={"type": "string", "enum": ["", "a", "a"]}))
        assert repaired.parameters["properties"]["s"]["enum"] == ["a"]

    def test_required_naming_an_absent_property_is_dropped(self):
        from personalclaw.tool_providers.portable_schema import conform_parameters

        verdict = conform_parameters({**_obj(a={"type": "string"}), "required": ["a", "ghost"]})
        assert verdict.parameters["required"] == ["a"]
        assert _rules({**_obj(a={"type": "string"}), "required": ["a", "ghost"]}) == [
            ("required_invalid", "required", True)
        ]

    def test_only_date_time_format_survives(self):
        from personalclaw.tool_providers.portable_schema import conform_parameters

        verdict = conform_parameters(
            _obj(u={"type": "string", "format": "uri"}, n={"type": "number", "format": "double"})
        )
        props = verdict.parameters["properties"]
        assert "format" not in props["u"] and "format" not in props["n"]

    def test_non_portable_keywords_are_dropped_from_the_copy(self):
        from personalclaw.tool_providers.portable_schema import conform_parameters

        params = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "a": {"type": "string", "examples": ["x"], "x-meta": {}, "nullable": True},
                "b": {"type": "number", "exclusiveMinimum": 0, "multipleOf": 2},
            },
        }
        verdict = conform_parameters(params)
        assert verdict.parameters == _obj(a={"type": "string"}, b={"type": "number"})
        assert all(i.repair for i in verdict.issues)
        assert params["additionalProperties"] is False, "the input must never be mutated"

    def test_a_local_ref_is_inlined(self):
        from personalclaw.tool_providers.portable_schema import conform_parameters

        params = {
            "type": "object",
            "properties": {"who": {"$ref": "#/$defs/Person", "description": "the person"}},
            "$defs": {"Person": _obj(name={"type": "string"})},
        }
        verdict = conform_parameters(params)
        assert verdict.parameters == _obj(
            who={
                "type": "object",
                "description": "the person",
                "properties": {"name": {"type": "string"}},
            }
        )

    def test_a_recursive_ref_is_blocking(self):
        params = {
            "type": "object",
            "properties": {"node": {"$ref": "#/$defs/Node"}},
            "$defs": {"Node": _obj(child={"$ref": "#/$defs/Node"})},
        }
        assert ("keyword_unsupported", "properties.node.properties.child", False) in _rules(params)

    def test_an_exponential_ref_fan_out_is_refused_quickly(self):
        """Twenty definitions, each referencing the next twice, expand to 2**20 nodes; the walk
        must refuse on its budget instead of following them."""
        import time

        defs = {
            f"D{i}": _obj(l={"$ref": f"#/$defs/D{i + 1}"}, r={"$ref": f"#/$defs/D{i + 1}"})
            for i in range(20)
        }
        defs["D20"] = {"type": "string"}
        params = {"type": "object", "properties": {"x": {"$ref": "#/$defs/D0"}}, "$defs": defs}
        started = time.monotonic()
        issues = schema_issues(params)
        assert time.monotonic() - started < 5
        assert any("too deep or too large" in i.detail for i in issues)

    def test_a_nullable_union_collapses_and_a_real_union_is_kept(self):
        from personalclaw.tool_providers.portable_schema import conform_parameters

        verdict = conform_parameters(
            _obj(
                a={"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                b={"type": ["integer", "null"]},
                c={"oneOf": [{"type": "string"}, {"type": "integer"}]},
            )
        )
        props = verdict.parameters["properties"]
        assert props["a"] == {"type": "string", "default": None}
        assert props["b"] == {"type": "integer"}
        assert props["c"] == {"anyOf": [{"type": "string"}, {"type": "integer"}]}

    def test_a_union_branch_obeys_the_rules_too(self):
        assert _rules(_obj(x={"anyOf": [{"type": "string"}, {"type": "array"}]})) == [
            ("array_items_missing", "properties.x.anyOf[1]", False)
        ]

    @pytest.mark.parametrize("combinator", ["anyOf", "oneOf", "allOf"])
    def test_a_root_combinator_is_blocking(self, combinator):
        params = {combinator: [_obj(a={"type": "string"}), _obj(b={"type": "string"})]}
        assert _rules(params) == [("root_combinator", "", False)]

    def test_const_and_type_inference(self):
        from personalclaw.tool_providers.portable_schema import conform_parameters

        verdict = conform_parameters(_obj(k={"const": "fixed"}, e={"enum": ["a", "b"]}))
        assert verdict.parameters["properties"] == {
            "k": {"type": "string", "enum": ["fixed"]},
            "e": {"type": "string", "enum": ["a", "b"]},
        }
        assert _rules(_obj(k={"type": "integer", "const": 3})) == [
            ("keyword_unsupported", "properties.k", False)
        ]
        assert _rules(_obj(anything={"description": "any value"})) == [
            ("type_missing", "properties.anything", False)
        ]


# ── the wire ─────────────────────────────────────────────────────────────────


class TestTheWire:
    def test_an_argument_less_tool_still_carries_a_parameters_schema(self):
        """OpenRouter's and Mistral's request contracts declare `parameters` required, so a tool
        with no arguments sends the empty object schema rather than omitting it — even though
        Gemini's native API documents omission (see the profile's "not encoded" note)."""
        from personalclaw.agents.native.tools import tool_definitions_to_openai_schema
        from personalclaw.tool_providers.base import ToolDefinition

        params = _obj(q={"type": "string"})
        wire = tool_definitions_to_openai_schema(
            [
                ToolDefinition(name="bare", description="d"),
                ToolDefinition(name="some", description="d", parameters=params),
            ]
        )
        assert wire[0]["function"]["parameters"] == {"type": "object", "properties": {}}
        assert wire[1]["function"]["parameters"] is params


# ── the tool seam ────────────────────────────────────────────────────────────


from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult  # noqa: E402

_GOOD = ToolDefinition(name="app_good", description="fine", parameters=_obj(q={"type": "string"}))
_REPAIRABLE = ToolDefinition(
    name="app_sloppy",
    description="repairable",
    parameters={**_obj(q={"type": "string"}), "additionalProperties": False},
)
_BROKEN = ToolDefinition(
    name="app_broken", description="no items", parameters=_obj(xs={"type": "array"})
)


class _AppProvider(ToolProvider):
    def __init__(self, tools: list[ToolDefinition], name: str = "demo-provider") -> None:
        self._tools = tools
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "Demo"

    async def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools)

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output=tool_name)


class _GeminiRulesModel:
    """A tool-capable model that enforces two of Gemini's function-declaration rules the way
    Gemini does — independently of the profile module, so it is a real control: an array with no
    ``items`` or an object with no ``properties`` fails the WHOLE request with Gemini's text."""

    supports_tools = True

    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def _violations(i: int, schema: dict[str, Any], path: str) -> list[str]:
        out = []
        if schema.get("type") == "array" and "items" not in schema:
            out.append(f"function_declarations[{i}].{path}.items: missing field.")
        if schema.get("type") == "object" and not schema.get("properties"):
            out.append(
                f"function_declarations[{i}].{path}.properties: should be non-empty for OBJECT type"
            )
        for name, sub in (schema.get("properties") or {}).items():
            out += _GeminiRulesModel._violations(i, sub, f"{path}.properties[{name}]")
        if isinstance(schema.get("items"), dict):
            out += _GeminiRulesModel._violations(i, schema["items"], f"{path}.items")
        return out

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        errors = []
        for i, entry in enumerate(tools or []):
            fn = entry["function"]
            if "parameters" in fn:
                errors += self._violations(i, fn["parameters"], "parameters")
        if errors:
            raise RuntimeError(
                "Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400, "
                "'metadata': {'raw': '* GenerateContentRequest.tools[0]."
                + "\\n* GenerateContentRequest.tools[0].".join(errors)
                + "'}}}"
            )
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")


def _runtime_over(provider, model):
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition

    definition = AgentRuntimeDefinition(name="seam", provider="native", model="m")
    return NativeAgentRuntime(
        definition=definition, model_provider=model, tool_providers=[provider]
    )


def _run_turn(runtime, message: str = "hello") -> None:
    async def _go() -> None:
        async for _ in runtime.stream(message):
            pass

    asyncio.run(_go())


@pytest.fixture
def fresh_reports(monkeypatch):
    """A clean once-per-process log ledger for the seam, so each test sees its own first line."""
    from personalclaw.tool_providers import portable_schema

    monkeypatch.setattr(portable_schema, "_reported", set())


class TestTheSeam:
    def test_a_portable_tool_passes_as_the_same_object(self, fresh_reports):
        from personalclaw.tool_providers.portable_schema import offered_tool_definitions

        assert offered_tool_definitions([_GOOD], provider="p")[0] is _GOOD

    def test_a_repairable_tool_is_repaired_and_logged_once(self, fresh_reports, caplog):
        from personalclaw.tool_providers.portable_schema import offered_tool_definitions

        caplog.set_level("WARNING", logger="personalclaw.tool_providers.portable_schema")
        [offered] = offered_tool_definitions(
            [_REPAIRABLE], provider="demo-provider", app="demo-app"
        )
        assert offered.parameters == _obj(q={"type": "string"})
        assert _REPAIRABLE.parameters["additionalProperties"] is False, "never mutate the app's def"
        offered_tool_definitions([_REPAIRABLE], provider="demo-provider", app="demo-app")
        lines = [r.getMessage() for r in caplog.records]
        assert len(lines) == 1, lines
        assert (
            "app 'demo-app' (provider 'demo-provider') tool 'app_sloppy' was repaired" in lines[0]
        )

    def test_an_unrepairable_tool_is_left_out_and_named(self, fresh_reports, caplog):
        from personalclaw.tool_providers.portable_schema import offered_tool_definitions

        caplog.set_level("WARNING", logger="personalclaw.tool_providers.portable_schema")
        assert offered_tool_definitions([_BROKEN, _GOOD], provider="demo-app", app="demo-app") == [
            _GOOD
        ]
        [line] = [r.getMessage() for r in caplog.records]
        assert "app 'demo-app' tool 'app_broken' is NOT offered to models" in line
        assert "properties.xs: an array must declare the schema of its `items`" in line

    def test_one_bad_app_tool_cannot_take_down_a_turn(self, fresh_reports):
        """The owner's failure, from an app: against a model enforcing Gemini's rules, the turn
        still completes, because the broken tool never reaches the request."""
        from personalclaw.agents.native.tools import tool_definitions_to_openai_schema

        model = _GeminiRulesModel()

        async def _direct() -> None:  # the control: handed the raw schema, the model rejects it
            async for _ in model.complete([], tools=tool_definitions_to_openai_schema([_BROKEN])):
                pass

        with pytest.raises(RuntimeError, match=r"function_declarations\[0\]\.parameters"):
            asyncio.run(_direct())
        runtime = _runtime_over(_AppProvider([_GOOD, _BROKEN, _REPAIRABLE]), model)
        asyncio.run(runtime.start())
        _run_turn(runtime)
        assert sorted(runtime._tool_index) == ["app_good", "app_sloppy"]
        assert {t.name for t in runtime._tool_defs} == {"app_good", "app_sloppy"}

    def test_the_catalog_says_why_a_tool_is_not_offered(self, fresh_reports):
        from personalclaw.tool_providers import registry

        provider = _AppProvider([_GOOD, _BROKEN], name="demo-catalog")
        registry.register_provider(provider, app="demo-app")
        registry.clear_load_failures()
        try:
            tools = asyncio.run(registry.list_all_tools())
            assert {"app_good", "app_broken"} <= {t.name for t in tools}, "the catalog keeps both"
            [failure] = [f for f in registry.get_load_failures() if f["provider"] == "demo-catalog"]
            assert "not offered to models" in failure["error"] and "app_broken" in failure["error"]
            assert registry.app_of("demo-catalog") == "demo-app"
        finally:
            registry.unregister_provider(provider)
            registry.clear_load_failures()
        assert registry.app_of("demo-catalog") == ""


# ── a provider refusing a tool definition ────────────────────────────────────


def _owner_error(index: int) -> str:
    """The owner's error, as their instance logged it (five fields, one declaration)."""
    lines = "\\n".join(
        f"* GenerateContentRequest.tools[0].function_declarations[{index}].parameters.properties"
        f"[{field}].items: missing field."
        for field in ("deliverables", "scope", "rubric", "stage_plan", "sub_goals")
    )
    return (
        "Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400, "
        '\'metadata\': {\'raw\': \'{ "error": { "code": 400, "message": "' + lines + "\" } }'}}}"
    )


def _payload(*defs: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
    from personalclaw.agents.native.tools import tool_definitions_to_openai_schema

    return tool_definitions_to_openai_schema(
        [ToolDefinition(name=n, description="d", parameters=p) for n, p in defs]
    )


_PROJECT_RUN = _obj(**{f: {"type": "array", "items": {"type": "string"}} for f in (
    "deliverables", "scope", "rubric", "stage_plan", "sub_goals")})  # fmt: skip


class TestAProviderRejection:
    def test_the_owner_error_names_project_run_create(self):
        from personalclaw.tool_providers.portable_schema import tools_named_in_rejection

        filler = [(f"t{i}", _obj(q={"type": "string"})) for i in range(15)]
        payload = _payload(*filler, ("project_run_create", _PROJECT_RUN))
        assert tools_named_in_rejection(_owner_error(15), payload) == ["project_run_create"]

    def test_an_index_is_trusted_only_when_the_named_property_matches(self):
        """A translator that re-numbered the list must never make the message name the wrong
        tool: index 15 here is a tool with no `deliverables`, so nothing is named."""
        from personalclaw.tool_providers.portable_schema import tools_named_in_rejection

        filler = [(f"t{i}", _obj(q={"type": "string"})) for i in range(16)]
        assert tools_named_in_rejection(_owner_error(15), _payload(*filler)) == []

    @pytest.mark.parametrize(
        "message",
        [
            "Error code: 400 - Invalid schema for function 'b': In context=('properties', 'q'), "
            "array schema missing items.",
            "tools.1.custom.input_schema: JSON schema is invalid",
            "The value at toolConfig.tools.1.member.toolSpec.inputSchema.json failed to satisfy "
            "constraint",
            "* GenerateContentRequest.tools[0].function_declarations[1].parameters.properties: "
            "should be non-empty for OBJECT type",
        ],
    )
    def test_the_wire_shapes_of_a_rejection(self, message):
        from personalclaw.tool_providers.portable_schema import tools_named_in_rejection

        payload = _payload(("a", _obj(q={"type": "string"})), ("b", _obj(q={"type": "string"})))
        assert tools_named_in_rejection(message, payload) == ["b"]

    @pytest.mark.parametrize(
        "message",
        [
            "Error code: 429 - rate limit exceeded",
            "Error code: 400 - messages[3].tool_calls[0] is invalid",
            "Error code: 400 - prompt is too long: 250000 tokens > 200000 maximum",
        ],
    )
    def test_an_error_that_is_not_about_a_tool_definition_names_nothing(self, message):
        from personalclaw.tool_providers.portable_schema import tools_named_in_rejection

        payload = _payload(("a", _obj(q={"type": "string"})), ("b", _obj(q={"type": "string"})))
        assert tools_named_in_rejection(message, payload) == []

    def test_the_sentence(self):
        """Measured in the running product: a session keeps the toolset it started with, so
        switching a tool off helps a NEW conversation only — the sentence says exactly that."""
        from personalclaw.llm_helpers import humanize_provider_error
        from personalclaw.tool_providers.portable_schema import ToolSchemaRejected

        rejected = ToolSchemaRejected(["project_run_create"], can_turn_off=True)
        assert str(rejected) == (
            'The model provider rejected PersonalClaw\'s definition of the "project_run_create" '
            "tool, so this turn could not run — that is a bug in PersonalClaw, not something you "
            "did; turn that tool off on the Tools page and start a new chat to keep going until it "
            "is fixed."
        )
        in_a_room = humanize_provider_error(rejected, room_member="researcher")
        assert in_a_room.endswith("start a new room to keep going until it is fixed.")
        locked = str(ToolSchemaRejected(["bash", "grep"], can_turn_off=False))
        assert 'definitions of the "bash" and "grep" tools' in locked
        assert "Tools page" not in locked, "a core-locked tool cannot be turned off"

    def test_the_turn_raises_the_sentence_without_a_blind_retry(self, fresh_reports):
        """A provider rule the profile does not encode still ends in the sentence: the runtime
        maps the index to the tool it sent, and does not re-send an identical request."""
        from personalclaw.llm_helpers import humanize_provider_error
        from personalclaw.tool_providers.portable_schema import ToolSchemaRejected

        quirky = ToolDefinition(
            name="quirky", description="d", parameters=_obj(q={"type": "string"})
        )

        class _Rejecting:
            supports_tools = True
            calls = 0

            async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
                type(self).calls += 1
                index = [e["function"]["name"] for e in tools].index("quirky")
                raise RuntimeError(
                    f"Error code: 400 - * GenerateContentRequest.tools[0].function_declarations"
                    f"[{index}].parameters.properties[q].format: not supported"
                )
                yield  # pragma: no cover

        runtime = _runtime_over(_AppProvider([_GOOD, quirky]), _Rejecting())
        asyncio.run(runtime.start())
        with pytest.raises(ToolSchemaRejected) as caught:
            _run_turn(runtime)
        assert caught.value.tools == ("quirky",)
        assert _Rejecting.calls == 1, "an identical request fails identically — no retry"
        assert humanize_provider_error(caught.value) == str(caught.value)
        assert '"quirky" tool' in str(caught.value) and "Tools page" in str(caught.value)


# ── JSON-text arguments ──────────────────────────────────────────────────────


class TestJsonTextArguments:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ('{"a": 1}', {"a": 1}),
            ("[1, 2]", [1, 2]),
            ("true", True),
            ('```json\n{"a": 1}\n```', {"a": 1}),
            ({"a": 1}, {"a": 1}),
            ("not json", "not json"),
            ("", ""),
            (None, None),
        ],
    )
    def test_decode_json_text(self, raw, expected):
        from personalclaw.validation import decode_json_text

        assert decode_json_text(raw) == expected

    def test_a_container_field_accepts_its_json_text(self):
        from personalclaw.validation import FieldSpec, ValidationError, validate_field

        assert validate_field('{"a": 1}', FieldSpec("root", dict)) == {"a": 1}
        assert validate_field('[{"op": "x"}]', FieldSpec("ops", list, item_type=dict)) == [
            {"op": "x"}
        ]
        with pytest.raises(ValidationError, match="expected dict, got str"):
            validate_field("not json", FieldSpec("root", dict))
        with pytest.raises(ValidationError, match="expected dict, got str"):
            validate_field("[1]", FieldSpec("root", dict))
        # A string field is never decoded — JSON-looking TEXT stays text.
        assert validate_field('{"a": 1}', FieldSpec("note", str)) == '{"a": 1}'

    def test_workflow_tools_decode_their_json_text(self, monkeypatch):
        from personalclaw import mcp_workflows

        cleaned = mcp_workflows._validate_args(
            "workflow_start", {"name": "triage-inbox", "inputs": '{"since": "1h"}'}
        )
        assert cleaned["inputs"] == {"since": "1h"}
        seen: dict[str, Any] = {}

        def _resume(run_id, **kw):
            seen.update(kw)
            return {"ok": True}

        monkeypatch.setattr(mcp_workflows.service, "resume_run", _resume)
        mcp_workflows._dispatch("workflow_resume", {"run_id": "a1b2c3d4", "answer": "true"})
        assert seen["answer"] is True

    def test_prompt_render_decodes_its_vars(self):
        from unittest.mock import patch

        from personalclaw import mcp_prompts

        with patch("personalclaw.mcp_prompts._post", return_value={"rendered": "ok"}) as post:
            mcp_prompts._call_tool(
                "prompt_render", {"prompt_id": "review", "vars": '{"file": "a.py"}'}
            )
        assert post.call_args[0][1] == {"variables": {"file": "a.py"}}

    def test_sheet_rows_as_json_text_keep_their_numbers(self, tmp_path, monkeypatch):
        from personalclaw.artifacts.native import NativeArtifactProvider
        from personalclaw.documents import model as doc_model
        from personalclaw.mcp_artifacts import _document_create

        seen: list[Any] = []
        real = doc_model.SheetModel.from_rows.__func__

        def _capture(cls, sheets):
            seen.append(sheets)
            return real(cls, sheets)

        monkeypatch.setattr(doc_model.SheetModel, "from_rows", classmethod(_capture))
        prov = NativeArtifactProvider(root=tmp_path / "artifacts")
        reply = _document_create(
            prov,
            "sheet_create",
            {"name": "Sales", "format": "csv", "rows": '[["Region", "Q1"], ["EMEA", 120]]'},
            None,
            lambda *a, **k: None,
        )
        assert "Error" not in reply, reply
        assert seen == [{"Sheet1": [["Region", "Q1"], ["EMEA", 120]]}]


# ── the reference examples tell the truth ────────────────────────────────────

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def test_every_reference_example_matches_the_declared_types(bundled_tool_registry):
    """An example is what the offline reference tells an agent to COPY, so each argument value
    must have the type its tool declares — a parameter that became JSON text is a string."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
    from personalclaw.manifest_meta import TOOL_META

    tools = asyncio.run(bundled_tool_registry.list_all_tools())
    tools += asyncio.run(
        NativeBuiltinToolProvider(cwd=".", agent="r", session_key="r").list_tools()
    )
    declared = {t.name: (t.parameters or {}).get("properties") or {} for t in tools}
    wrong = []
    for name, meta in TOOL_META.items():
        for i, example in enumerate(meta.get("examples") or []):
            for arg, value in (example.get("args") or {}).items():
                kind = (declared.get(name, {}).get(arg) or {}).get("type")
                wanted = _JSON_TYPES.get(kind)
                if wanted is None:
                    continue
                if isinstance(value, bool) and bool not in wanted:
                    wrong.append(f"{name} example[{i}].{arg}: bool for a {kind}")
                elif not isinstance(value, wanted):
                    wrong.append(f"{name} example[{i}].{arg}: {type(value).__name__} for a {kind}")
    assert not wrong, "reference examples contradict their tool's schema:\n  " + "\n  ".join(wrong)
