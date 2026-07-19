"""Schema-driven numeric-arg coercion for MCP tool calls.

A model (notably Claude-on-Bedrock via Converse) sometimes emits a numeric tool
argument as a STRING ("128") even when the tool's inputSchema types the field as
number/integer; a strict MCP server then rejects the call with -32602. The MCP
client coerces such values back to numbers, driven by the tool's inputSchema.

These test the PURE helper (no live server / SDK needed), so they run everywhere.
"""

from __future__ import annotations

from personalclaw.mcp_client import _coerce_args_to_schema, _schema_numeric_kind


_SUM_SCHEMA = {
    "type": "object",
    "properties": {
        "a": {"type": "number"},
        "b": {"type": "integer"},
        "label": {"type": "string"},
    },
}


def test_numeric_string_args_coerced_per_schema():
    out = _coerce_args_to_schema({"a": "128", "b": "256", "label": "42"}, _SUM_SCHEMA)
    assert out == {"a": 128.0, "b": 256, "label": "42"}
    assert isinstance(out["a"], float) and isinstance(out["b"], int)
    # The string-typed field is left a string even though it looks numeric.
    assert out["label"] == "42"


def test_already_numeric_args_untouched():
    out = _coerce_args_to_schema({"a": 128, "b": 256}, _SUM_SCHEMA)
    assert out == {"a": 128, "b": 256}


def test_non_numeric_string_left_as_is():
    # "not-a-number" for a number field stays as-is so the server's -32602 fires.
    out = _coerce_args_to_schema({"a": "not-a-number"}, _SUM_SCHEMA)
    assert out == {"a": "not-a-number"}


def test_tricky_numeric_looking_strings_not_coerced():
    # Python int()/float() would accept these; the strict regex must reject them.
    for bad in ("1_000", "inf", "nan", "0x1F", " 12", "12 ", "+", ""):
        out = _coerce_args_to_schema({"b": bad}, _SUM_SCHEMA)
        assert out == {"b": bad}, f"{bad!r} should not be coerced"


def test_scientific_notation_coerced_for_number_not_integer():
    # A "number" field accepts scientific notation; an "integer" field does not.
    assert _coerce_args_to_schema({"a": "1e3"}, _SUM_SCHEMA) == {"a": 1000.0}
    assert _coerce_args_to_schema({"b": "1e3"}, _SUM_SCHEMA) == {"b": "1e3"}


def test_union_type_with_string_not_coerced():
    schema = {"properties": {"x": {"type": ["string", "integer"]}}}
    assert _coerce_args_to_schema({"x": "128"}, schema) == {"x": "128"}


def test_union_type_without_string_coerced():
    schema = {"properties": {"x": {"type": ["integer", "null"]}}}
    assert _coerce_args_to_schema({"x": "128"}, schema) == {"x": 128}


def test_anyof_oneof_ref_not_coerced():
    for branch in ("anyOf", "oneOf", "allOf", "$ref"):
        schema = {"properties": {"x": {branch: [{"type": "integer"}]}}}
        assert _coerce_args_to_schema({"x": "128"}, schema) == {"x": "128"}


def test_negative_and_float_literals():
    assert _coerce_args_to_schema({"a": "-3.14", "b": "-7"}, _SUM_SCHEMA) == {"a": -3.14, "b": -7}


def test_empty_or_missing_schema_returns_args_unchanged():
    assert _coerce_args_to_schema({"a": "1"}, None) == {"a": "1"}
    assert _coerce_args_to_schema({"a": "1"}, {}) == {"a": "1"}
    assert _coerce_args_to_schema({"a": "1"}, {"properties": {}}) == {"a": "1"}


def test_schema_numeric_kind_classification():
    assert _schema_numeric_kind({"type": "integer"}) == "integer"
    assert _schema_numeric_kind({"type": "number"}) == "number"
    assert _schema_numeric_kind({"type": "string"}) is None
    assert _schema_numeric_kind({"type": ["number", "null"]}) == "number"
    assert _schema_numeric_kind({"type": ["string", "number"]}) is None
    assert _schema_numeric_kind({"anyOf": [{"type": "integer"}]}) is None
    assert _schema_numeric_kind("nonsense") is None
