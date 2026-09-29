"""A tool accepts every argument it teaches, and passes each one on.

Measured in a chat on a local model: the session context teaches "Lessons have two scopes (use
memory_remember tool to save): scope=global … scope=workspace …", and the tool's own input schema
declares ``scope`` and ``workspace``. The call was refused before it reached the tool:
"Error: scope: unknown field for tool 'memory_remember'". The argument validator every call goes
through first (``validation.LEARN_ADD_SCHEMA``) was a declaration behind the published schema, so
the model spent a ``tool_schema`` lookup and a retry to save one lesson, and a workspace lesson
could not be saved through the tool at all. The existing scope tests drove the handler below the
validator, which is how the gap stayed invisible. ``negative``, published and validated, was
dropped on the way to the lessons endpoint.

The same family has a second door: the worked examples an agent reads for a tool
(``manifest_meta.TOOL_META``). Two of them taught calls the tools refuse.

The rails here cover the whole family, not the one tool: every published argument of every tool
that has a validator is one the validator accepts, and every worked example is a call the tool
accepts.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import personalclaw
from personalclaw import mcp_memory, validation
from personalclaw.manifest_meta import TOOL_META

#: Every validator map a dispatcher consults (``validation.validated_tool_names``).
_VALIDATORS = {
    **validation.MCP_CORE_SCHEMAS,
    **validation.MCP_WORKFLOW_SCHEMAS,
    **validation.MCP_AUTOMATION_SCHEMAS,
}


def _published_tools() -> dict[str, dict]:
    """``{tool name: inputSchema}`` from every in-process tool module that publishes tools."""
    published: dict[str, dict] = {}
    for info in pkgutil.iter_modules(personalclaw.__path__):
        if not info.name.startswith("mcp_"):
            continue
        module = importlib.import_module(f"personalclaw.{info.name}")
        lister = getattr(module, "_list_tools", None)
        if lister is None:
            continue
        for tool in lister():
            published.setdefault(tool["name"], tool.get("inputSchema") or {})
    return published


@pytest.fixture()
def posted(monkeypatch):
    """What ``memory_remember`` sends the lessons endpoint, captured instead of sent."""
    sent: list[tuple[str, dict]] = []

    def _capture(path, payload):
        sent.append((path, dict(payload)))
        return {"ok": True}

    monkeypatch.setattr(mcp_memory, "_post", _capture)
    return sent


def test_a_workspace_lesson_goes_through_the_validator_to_the_endpoint(posted):
    out = mcp_memory._call_tool(
        "memory_remember",
        {
            "rule": "this repo runs its tests with uv run pytest",
            "category": "tool",
            "scope": "workspace",
            "workspace": "/srv/example/project",
        },
    )

    assert "unknown field" not in out, out
    assert out == "Saved lesson (workspace): this repo runs its tests with uv run pytest"
    assert posted == [
        (
            "/api/lessons",
            {
                "rule": "this repo runs its tests with uv run pytest",
                "category": "tool",
                "scope": "workspace",
                "workspace": "/srv/example/project",
            },
        )
    ]


def test_the_scope_the_prompt_calls_the_default_is_accepted_by_name(posted):
    out = mcp_memory._call_tool(
        "memory_remember",
        {"rule": "answer in British English", "category": "preference", "scope": "global"},
    )

    assert out == "Saved lesson (global): answer in British English"
    assert posted[0][1]["scope"] == "global"


def test_an_unknown_scope_is_still_refused_by_the_validator(posted):
    out = mcp_memory._call_tool(
        "memory_remember", {"rule": "x", "category": "preference", "scope": "everywhere"}
    )

    assert "scope" in out and "not allowed" in out, out
    assert posted == []


def test_what_not_to_do_reaches_the_endpoint(posted):
    mcp_memory._call_tool(
        "memory_remember",
        {
            "rule": "leave secrets out of a quoted log",
            "category": "preference",
            "negative": "never paste a street address back",
        },
    )

    assert posted[0][1]["negative"] == "never paste a street address back"


def test_every_published_argument_is_one_its_validator_accepts():
    published = _published_tools()
    checked = [name for name in _VALIDATORS if name in published]
    # Positive control: the rail reads real tools, so an empty census cannot pass it.
    assert len(checked) > 30, f"only {len(checked)} validated tools were found published"

    refused = {
        name: sorted(
            set((published[name].get("properties") or {}))
            - {field.name for field in _VALIDATORS[name].fields}
        )
        for name in checked
    }
    assert {name: fields for name, fields in refused.items() if fields} == {}


def test_every_published_choice_is_one_its_validator_accepts():
    """The other way a published schema can teach a refused call: an ``enum`` value the
    validator's ``allowed`` set does not hold. A validator may accept MORE than it publishes;
    it may never refuse what it publishes."""
    published = _published_tools()
    compared = 0
    refused = []
    for name, schema in _VALIDATORS.items():
        properties = (published.get(name) or {}).get("properties") or {}
        for field in schema.fields:
            choices = (properties.get(field.name) or {}).get("enum")
            if not choices or not field.allowed:
                continue
            compared += 1
            refused += [(name, field.name, c) for c in choices if c not in field.allowed]
    assert compared >= 5, f"only {compared} published choices were compared"
    assert refused == []


def test_every_worked_example_is_a_call_the_tool_accepts():
    examples = [
        (tool, example["args"])
        for tool, meta in TOOL_META.items()
        if tool in _VALIDATORS
        for example in meta.get("examples", [])
    ]
    assert len(examples) > 30, f"only {len(examples)} examples were checked"

    refused = []
    for tool, args in examples:
        try:
            validation.validate_tool_args(dict(args), _VALIDATORS[tool])
        except validation.ValidationError as exc:
            refused.append((tool, args, str(exc).splitlines()[0]))
    assert refused == []
