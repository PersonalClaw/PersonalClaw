"""The portable tool-parameter schema profile — the subset every model request may carry.

A model provider validates the WHOLE tool block of a request, and one schema it cannot accept
fails the request — not that tool, the request. The incident that made this a module: a user
whose chat model was Gemini (through an OpenAI-compatible router) could not chat at all, because
``project_run_create`` declared five ``{"type": "array"}`` parameters without ``items`` and every
turn came back ``400 … function_declarations[15].parameters.properties[deliverables].items:
missing field``. Any tool can do that to every turn, so the rule is enforced at the tool seam
rather than remembered per tool.

The profile is the INTERSECTION of what the strictest mainstream consumers accept, so a schema
inside it is accepted by all of them. Core never branches on which provider is bound — that
would be vendor logic in core — it keeps every schema inside the subset they all share. This
docstring is the as-built statement of the rules; ``docs/architecture/tool-schema-wire.md`` maps
where they are enforced.

The rules (the ``rule`` id a :class:`SchemaIssue` carries is in brackets):

* **The root is an object schema** [``root_not_object``] with no top-level combinator
  [``root_combinator``: ``anyOf``/``oneOf``/``allOf``/``not``/``enum``]. A root with no
  properties is an argument-less tool, and it travels as the empty object schema
  ``{"type": "object", "properties": {}}``
  (:func:`personalclaw.agents.native.tools.tool_definitions_to_openai_schema`) — see "not
  encoded" below for why the wire does not omit it.
* **Every node is a schema object** [``node_not_schema``] with exactly one ``type`` from
  ``object``/``array``/``string``/``number``/``integer``/``boolean`` [``type_missing``,
  ``type_invalid``], unless it is an ``anyOf`` of such nodes.
* **Every array declares its element schema** [``array_items_missing``] — the incident's rule.
* **Every nested object declares at least one property** [``object_properties_missing``]. A
  free-form object or map has NO portable schema: declare its keys, or carry the value as JSON
  text (a ``string`` parameter, decoded by :func:`personalclaw.validation.decode_json_text`).
* **``required`` names declared properties only** [``required_invalid``].
* **``enum`` is a non-empty list of distinct, non-empty strings on a ``string`` node**
  [``enum_invalid``].
* **``format`` is ``date-time`` on a string, or absent** [``format_unsupported``].
* **Only portable keywords** [``keyword_unsupported``, ``keyword_misplaced``]: ``type``,
  ``description``, ``title``, ``default``, ``enum``, ``format``, ``properties``, ``required``,
  ``items``, ``anyOf`` and the per-type bounds (``minimum``/``maximum`` on numbers,
  ``minLength``/``maxLength``/``pattern`` on strings, ``minItems``/``maxItems`` on arrays,
  ``minProperties``/``maxProperties`` on objects). ``$ref``/``$defs``, ``additionalProperties``,
  ``oneOf``/``allOf``/``not``, ``const``, ``nullable``, ``examples``, ``$schema``, ``x-*`` and the
  rest are outside it.

Where each rule comes from — observed, not assumed:

* Google's published ``Schema`` message (``google/ai/generativelanguage/v1beta/content.proto``) is
  the exact field set Gemini's parser accepts. Probed against the live endpoint with synthetic
  schemas and no credentials (the JSON→proto parse runs before auth, so a clean parse answers 403
  and a bad one 400 naming the field): ``additionalProperties``, ``$ref``, ``$defs``, ``$schema``,
  ``const``, ``exclusiveMinimum``, ``multipleOf``, ``uniqueItems``, ``examples``,
  ``patternProperties``, ``readOnly``, ``deprecated`` and ``x-*`` are ``Unknown name … Cannot find
  field``; a type LIST, a numeric ``enum`` value, a tuple or boolean ``items`` and a boolean
  property schema are ``Invalid value``. Google's own SDK refuses ``additionalProperties`` outside
  its enterprise mode and turns only a STRING ``const`` into an enum.
* Gemini's semantic checks run after auth, so they come from the rejections themselves: ``items:
  missing field`` (this incident), ``properties: should be non-empty for OBJECT type`` (on a
  nested object; see below for the root), ``enum: only allowed for STRING type``,
  ``required[i]: property is not defined``, and ``format: only 'enum' and 'date-time' are
  supported for STRING type``. LiteLLM's Gemini converter carries the same list, plus "no empty
  strings in an enum" and "``items`` is required even inside ``anyOf``".
* OpenAI function calling: ``parameters`` must be an object schema, with no
  ``anyOf``/``oneOf``/``allOf``/``enum``/``not`` at the top level, and an array schema must have
  ``items``. Anthropic: ``input_schema`` is an object schema with no top-level combinator.

Deliberately NOT encoded:

* Gemini's rejection of an EMPTY ROOT object. Its native API wants a function with no arguments
  to omit ``parameters``, but OpenRouter's request contract (its ``FunctionDescription`` type)
  and Mistral's (its SDK's ``Function`` model) both declare ``parameters`` required, so no single
  wire form satisfies all three — and PersonalClaw reaches Gemini only through OpenAI-compatible
  layers (OpenRouter, Google's OpenAI-compatible endpoint), never the native API. The owner's
  failing request fits that: its instance log named only ``project_run_create``'s five arrays,
  while a local reproduction of that turn, with ``project_run_create`` at the same index, also
  carries the argument-less ``memory_list``. A route found to enforce the native rule is fixed in
  its own adapter, the way ``llm/anthropic.py`` already reshapes the schema for its API.
* OpenAI *strict mode*'s every-property-required and ``additionalProperties: false`` rules
  (PersonalClaw never sends ``strict``, and meeting them would make every optional argument
  mandatory).
* Tool NAME rules (``docs/architecture/tool-name-wire.md`` owns the name wire), and size or depth
  ceilings beyond the walk's own safety bounds.

Built-in tools must satisfy the profile AS DECLARED (``tests/test_tool_schema_portability.py`` is
the rail). A tool from an installed app is conformed at the tool seam instead
(:func:`offered_tool_definitions`): what can be fixed without changing what the tool accepts is
repaired, anything else excludes that one tool from model requests, and either way ONE log line
names the app and the tool — so a sloppy app schema can never take down a turn.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from typing import Any, Iterable, NoReturn, Sequence

from personalclaw.tool_providers.base import ToolDefinition

logger = logging.getLogger(__name__)

_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean"})

#: Keywords kept on EVERY node type — annotations every consumer accepts.
_ANY_NODE = frozenset({"type", "description", "title", "default", "enum", "format"})

#: The per-type keywords the profile keeps; a type-specific keyword on another type is dropped.
_BY_TYPE: dict[str, frozenset[str]] = {
    "object": frozenset({"properties", "required", "minProperties", "maxProperties"}),
    "array": frozenset({"items", "minItems", "maxItems"}),
    "string": frozenset({"minLength", "maxLength", "pattern"}),
    "number": frozenset({"minimum", "maximum"}),
    "integer": frozenset({"minimum", "maximum"}),
    "boolean": frozenset(),
}
_TYPE_SPECIFIC = frozenset().union(*_BY_TYPE.values())
_NUMERIC_BOUNDS = _TYPE_SPECIFIC - {"properties", "required", "items", "pattern"}

#: Keywords that only NARROW what validates, or only annotate. Dropping one never changes what the
#: tool itself accepts — its handler still enforces its own input — so dropping is a safe repair.
_DROPPABLE = frozenset(
    {
        "$schema", "$id", "$comment", "$anchor", "$dynamicAnchor", "examples", "example",
        "nullable", "readOnly", "writeOnly", "deprecated", "contentMediaType",
        "contentEncoding", "contentSchema", "propertyOrdering", "uniqueItems", "multipleOf",
        "exclusiveMinimum", "exclusiveMaximum", "minContains", "maxContains", "not", "if",
        "then", "else", "dependentRequired", "dependentSchemas", "dependencies",
        "propertyNames", "unevaluatedProperties", "unevaluatedItems", "additionalItems",
    }
)  # fmt: skip

#: Keywords that describe an object's content OTHER than through ``properties``. Beside declared
#: properties they only narrow (dropped); on their own they ARE the description of a map, which
#: has no portable form.
_MAP_KEYWORDS = frozenset({"additionalProperties", "patternProperties"})

#: Keywords with no portable reading at all — the element typing they carry cannot be dropped.
_NO_PORTABLE_FORM = frozenset({"prefixItems", "contains"})

#: Walk bounds. A schema is app-supplied, and local ``$ref``s can fan a small document out into an
#: exponential tree, so the walk refuses rather than follows one that large.
_MAX_DEPTH = 32
_MAX_NODES = 4000
_DEF_REF = re.compile(r"^#/(\$defs|definitions)/([^/]+)$")


@dataclasses.dataclass(frozen=True)
class SchemaIssue:
    """One way a parameter schema falls outside the profile.

    ``repair`` says what the conformer did about it; empty means it could not be repaired without
    changing what the tool accepts, so the tool is excluded from model requests.
    """

    path: str
    rule: str
    detail: str
    repair: str = ""

    def render(self) -> str:
        where = self.path or "parameters"
        return f"{where}: {self.detail}" + (f" — {self.repair}" if self.repair else "")


@dataclasses.dataclass(frozen=True)
class Conformance:
    """The profile's verdict on one parameter schema.

    ``parameters`` is the schema to send: the input itself when there were no issues, a repaired
    copy when every issue was repairable, and ``None`` when at least one was not.
    """

    parameters: dict[str, Any] | None
    issues: tuple[SchemaIssue, ...]

    @property
    def blocking(self) -> tuple[SchemaIssue, ...]:
        return tuple(i for i in self.issues if not i.repair)


class _Unportable(Exception):
    """Raised once an issue with no repair is recorded. The node that raised it yields a
    placeholder (:meth:`_Walk.node`), so the walk still reports the node's siblings."""


class _Walk:
    """One conformance pass over one schema: collects issues, inlines local ``$ref``s."""

    def __init__(self, root: dict[str, Any]) -> None:
        self.issues: list[SchemaIssue] = []
        self._defs: dict[str, Any] = {}
        for key in ("$defs", "definitions"):
            table = root.get(key)
            if isinstance(table, dict):
                self._defs.update(table)
        self._nodes = 0

    def repair(self, path: str, rule: str, detail: str, repair: str) -> None:
        self.issues.append(SchemaIssue(path, rule, detail, repair))

    def refuse(self, path: str, rule: str, detail: str) -> NoReturn:
        self.issues.append(SchemaIssue(path, rule, detail))
        raise _Unportable

    def node(self, raw: Any, path: str, depth: int, chain: tuple[str, ...]) -> dict[str, Any]:
        """The portable form of one schema node.

        A node with no portable form records its issue and yields a placeholder, so the walk goes
        on to its siblings: an author sees every defect in one report, not one per fix. The
        verdict is decided by the recorded issues, never by the placeholder, which no caller sends.

        ``chain`` is the ``$ref`` names being expanded along THIS path, so a definition that
        reaches itself is caught where it recurs rather than inlined until the depth bound.
        """
        try:
            return self._node(raw, path, depth, chain)
        except _Unportable:
            if self._nodes > _MAX_NODES:
                raise  # the budget is spent: stop walking, the verdict is already decided
            return {"type": "string"}

    def _node(self, raw: Any, path: str, depth: int, chain: tuple[str, ...]) -> dict[str, Any]:
        self._nodes += 1
        if depth > _MAX_DEPTH or self._nodes > _MAX_NODES:
            self.refuse(path, "node_not_schema", "the schema is too deep or too large to carry")
        if not isinstance(raw, dict):
            self.refuse(path, "node_not_schema", f"a schema must be an object, not {raw!r}")
        node = dict(raw)
        if "$ref" in node:
            merged, name = self._resolve(node, path, chain)
            return self.node(merged, path, depth + 1, (*chain, name))
        node = self._collapse_combinators(node, path)
        if "$ref" in node:  # a merged `allOf`/`anyOf` branch brought a reference with it
            return self.node(node, path, depth + 1, chain)
        if "anyOf" in node:
            return self._any_of(node, path, depth, chain)
        return self._typed(self._normalize_type(node, path), path, depth, chain)

    def _resolve(
        self, node: dict[str, Any], path: str, chain: tuple[str, ...]
    ) -> tuple[dict[str, Any], str]:
        ref = node.get("$ref")
        match = _DEF_REF.match(ref) if isinstance(ref, str) else None
        if match is None or match.group(2) not in self._defs:
            self.refuse(path, "keyword_unsupported", f"`$ref` {ref!r} does not resolve locally")
        name = match.group(2)
        if name in chain:
            self.refuse(path, "keyword_unsupported", f"`$ref` {ref!r} is recursive")
        target = self._defs[name]
        if not isinstance(target, dict):
            self.refuse(path, "node_not_schema", f"`$ref` {ref!r} is not a schema object")
        self.repair(path, "keyword_unsupported", "`$ref` is not portable", "inlined it")
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        # A shallow merge is enough: the walk never mutates a node it was handed (every level
        # works on its own copy), so the definition table's nested dicts can be shared safely.
        return {**target, **siblings}, name

    def _collapse_combinators(self, node: dict[str, Any], path: str) -> dict[str, Any]:
        """Reduce ``oneOf``/``allOf`` and ``null`` branches to the profile's single ``anyOf``."""
        if "oneOf" in node:
            if "anyOf" in node:
                self.refuse(
                    path, "keyword_unsupported", "`oneOf` beside `anyOf` has no portable form"
                )
            node["anyOf"] = node.pop("oneOf")
            self.repair(
                path, "keyword_unsupported", "`oneOf` is not portable", "widened it to `anyOf`"
            )
        if "allOf" in node:
            branches = node.pop("allOf")
            if not (isinstance(branches, list) and len(branches) == 1):
                self.refuse(
                    path, "keyword_unsupported", "`allOf` of several schemas is not portable"
                )
            if not isinstance(branches[0], dict):
                self.refuse(path, "node_not_schema", "the `allOf` branch is not a schema object")
            self.repair(path, "keyword_unsupported", "`allOf` is not portable", "merged its branch")
            node = {**branches[0], **node}
        if "anyOf" not in node:
            return node
        branches = node["anyOf"]
        if not isinstance(branches, list) or not branches:
            self.refuse(path, "node_not_schema", "`anyOf` must be a non-empty list of schemas")
        kept = [b for b in branches if not (isinstance(b, dict) and b.get("type") == "null")]
        if len(kept) < len(branches):
            self.repair(
                path, "type_invalid", "a `null` branch is not portable",
                "dropped it (the argument can be omitted instead)",
            )  # fmt: skip
        if not kept:
            self.refuse(path, "type_invalid", "an `anyOf` of only `null` accepts no value")
        if len(kept) > 1:
            node["anyOf"] = kept
            return node
        if not isinstance(kept[0], dict):
            self.refuse(path, "node_not_schema", "the `anyOf` branch is not a schema object")
        rest = {k: v for k, v in node.items() if k != "anyOf"}
        return {**kept[0], **rest}

    def _any_of(
        self, node: dict[str, Any], path: str, depth: int, chain: tuple[str, ...]
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "anyOf":
                continue
            if key == "description" and isinstance(value, str):
                out[key] = value
                continue
            self.repair(
                _at(path, key), "keyword_misplaced", f"`{key}` beside `anyOf` is not portable",
                "dropped it",
            )  # fmt: skip
        out["anyOf"] = [
            self.node(branch, f"{path}.anyOf[{i}]", depth + 1, chain)
            for i, branch in enumerate(node["anyOf"])
        ]
        return out

    def _normalize_type(self, node: dict[str, Any], path: str) -> dict[str, Any]:
        declared = node.get("type")
        if isinstance(declared, list):
            non_null = [t for t in declared if t != "null"]
            if len(non_null) != 1 or not isinstance(non_null[0], str):
                self.refuse(path, "type_invalid", f"a type list {declared!r} is not portable")
            node["type"] = non_null[0]
            self.repair(
                path, "type_invalid", f"a type list {declared!r} is not portable",
                f"narrowed it to {non_null[0]!r}",
            )  # fmt: skip
        elif declared is None:
            inferred = _infer_type(node)
            if inferred is None:
                self.refuse(
                    path, "type_missing", "declares no `type`, so it has no portable schema"
                )
            node["type"] = inferred
            self.repair(path, "type_missing", "declares no `type`", f"inferred {inferred!r}")
        if node["type"] not in _TYPES:
            self.refuse(path, "type_invalid", f"`type` {node['type']!r} is not portable")
        if "const" in node:
            const = node.pop("const")
            if node["type"] != "string" or not isinstance(const, str) or not const:
                self.refuse(
                    path, "keyword_unsupported", f"a non-string `const` {const!r} is not portable"
                )
            node["enum"] = [const]
            self.repair(
                path, "keyword_unsupported", "`const` is not portable", "made it a one-value enum"
            )
        return node

    def _typed(
        self, node: dict[str, Any], path: str, depth: int, chain: tuple[str, ...]
    ) -> dict[str, Any]:
        kind = node["type"]
        allowed = _ANY_NODE | _BY_TYPE[kind]
        raw_props = node.get("properties")
        declared_props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
        has_properties = bool(declared_props)
        for key in node:
            if key in allowed:
                continue  # handled below, keyword by keyword
            if key in ("$defs", "definitions"):
                self.repair(
                    _at(path, key), "keyword_unsupported", f"`{key}` is not portable",
                    "dropped it (every reference into it is inlined)",
                )  # fmt: skip
                continue
            if key in _MAP_KEYWORDS and kind == "object" and not has_properties:
                continue  # a map: reported once, as the object with no properties, below
            if key in _NO_PORTABLE_FORM:
                self.refuse(_at(path, key), "keyword_unsupported", f"`{key}` has no portable form")
            if key in _TYPE_SPECIFIC:
                self.repair(
                    _at(path, key), "keyword_misplaced", f"`{key}` does not apply to a {kind}",
                    "dropped it",
                )  # fmt: skip
                continue
            self.repair(
                _at(path, key), "keyword_unsupported", f"`{key}` is not portable", "dropped it"
            )

        out: dict[str, Any] = {"type": kind}
        for key in ("description", "title"):
            if key in node:
                if isinstance(node[key], str):
                    out[key] = node[key]
                else:
                    self.repair(
                        _at(path, key),
                        "keyword_misplaced",
                        f"`{key}` must be a string",
                        "dropped it",
                    )
        if "default" in node:
            out["default"] = node["default"]
        for key in sorted(_NUMERIC_BOUNDS & allowed):
            if key in node:
                value = node[key]
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out[key] = value
                else:
                    self.repair(
                        _at(path, key),
                        "keyword_misplaced",
                        f"`{key}` must be a number",
                        "dropped it",
                    )
        if kind == "string" and "pattern" in node:
            if isinstance(node["pattern"], str):
                out["pattern"] = node["pattern"]
            else:
                self.repair(
                    _at(path, "pattern"),
                    "keyword_misplaced",
                    "`pattern` must be a string",
                    "dropped it",
                )
        if "format" in node:
            if kind == "string" and node["format"] == "date-time":
                out["format"] = "date-time"
            else:
                self.repair(
                    _at(path, "format"), "format_unsupported",
                    f"format {node['format']!r} on a {kind} is not portable", "dropped it",
                )  # fmt: skip
        if "enum" in node:
            enum = self._enum(node["enum"], kind, _at(path, "enum"))
            if enum is not None:
                out["enum"] = enum

        if kind == "array":
            items = node.get("items")
            if not isinstance(items, dict) or not items:
                self.refuse(
                    _at(path, "items") if "items" in node else path, "array_items_missing",
                    "an array must declare the schema of its `items`",
                )  # fmt: skip
            out["items"] = self.node(items, _at(path, "items"), depth + 1, chain)
        elif kind == "object":
            if raw_props is not None and not isinstance(raw_props, dict):
                self.refuse(
                    _at(path, "properties"), "node_not_schema", "`properties` must be an object"
                )
            if has_properties:
                out["properties"] = {
                    name: self.node(sub, f"{_at(path, 'properties')}.{name}", depth + 1, chain)
                    for name, sub in declared_props.items()
                }
            elif path or any(k in node for k in _MAP_KEYWORDS):
                # The root alone may be empty — that is an argument-less tool. A nested object,
                # or a root that is really a map, has nothing portable to say about its keys.
                self.refuse(
                    path, "object_properties_missing",
                    "an object must declare at least one property (a free-form object or map has "
                    "no portable schema: declare its keys, or take the value as JSON text)",
                )  # fmt: skip
            else:
                out["properties"] = {}
            if "required" in node:
                required = self._required(
                    node["required"], out["properties"], _at(path, "required")
                )
                if required:
                    out["required"] = required
        return out

    def _enum(self, raw: Any, kind: str, path: str) -> list[str] | None:
        """The portable enum, or ``None`` to drop the keyword."""
        if kind != "string":
            self.repair(
                path, "enum_invalid", f"an `enum` on a {kind} is not portable", "dropped it"
            )
            return None
        if not isinstance(raw, list) or not raw:
            self.refuse(path, "enum_invalid", "an `enum` must be a non-empty list")
        values: list[str] = []
        for value in raw:
            if value is None or value == "":
                self.repair(
                    path, "enum_invalid", f"enum value {value!r} is not portable", "dropped it"
                )
            elif not isinstance(value, str):
                self.refuse(path, "enum_invalid", f"enum value {value!r} is not a string")
            elif value in values:
                self.repair(path, "enum_invalid", f"enum value {value!r} is repeated", "dropped it")
            else:
                values.append(value)
        if not values:
            self.refuse(path, "enum_invalid", "no portable enum value is left")
        return values

    def _required(self, raw: Any, props: dict[str, Any], path: str) -> list[str]:
        if isinstance(raw, str):
            self.repair(path, "required_invalid", "`required` must be a list", "wrapped the name")
            raw = [raw]
        if not isinstance(raw, list):
            self.repair(path, "required_invalid", "`required` must be a list", "dropped it")
            return []
        kept: list[str] = []
        for name in raw:
            if not isinstance(name, str) or name not in props:
                self.repair(
                    path, "required_invalid",
                    f"{name!r} is required but is not a declared property", "dropped it",
                )  # fmt: skip
            elif name in kept:
                self.repair(path, "required_invalid", f"{name!r} is listed twice", "dropped it")
            else:
                kept.append(name)
        return kept


def _at(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _infer_type(node: dict[str, Any]) -> str | None:
    """The one type a typeless node unambiguously has, or ``None``."""
    if isinstance(node.get("properties"), dict):
        return "object"
    if "items" in node:
        return "array"
    enum = node.get("enum")
    if isinstance(enum, list) and enum and all(isinstance(v, str) for v in enum):
        return "string"
    if isinstance(node.get("const"), str):
        return "string"
    return None


def conform_parameters(parameters: Any) -> Conformance:
    """Bring one tool's ``parameters`` inside the profile, or say why it cannot be.

    Never mutates its input. An empty or absent schema is an argument-less tool.
    """
    if parameters is None or parameters == {}:
        return Conformance(parameters={"type": "object", "properties": {}}, issues=())
    if not isinstance(parameters, dict):
        issue = SchemaIssue(
            "", "root_not_object", f"parameters must be an object, not {parameters!r}"
        )
        return Conformance(parameters=None, issues=(issue,))
    walk = _Walk(parameters)
    try:
        for key in ("anyOf", "oneOf", "allOf", "not", "enum"):
            if key in parameters:
                walk.refuse("", "root_combinator", f"`{key}` at the top level is not portable")
        root = dict(parameters)
        declared = root.get("type")
        if declared is None and "$ref" not in root:
            root["type"] = "object"
            walk.repair(
                "", "root_not_object", "the root declares no `type`", "declared it an object"
            )
        elif declared is not None and declared != "object":
            walk.refuse("", "root_not_object", f"the root must be an object, not {declared!r}")
        conformed = walk.node(root, "", 0, ())
        blocked = any(not issue.repair for issue in walk.issues)
        if not blocked and conformed.get("type") != "object":
            # e.g. a root `$ref` that resolved to a non-object. When the walk already blocked,
            # `conformed` is a placeholder and says nothing about the root.
            walk.refuse("", "root_not_object", "the root must be an object schema")
    except _Unportable:
        return Conformance(parameters=None, issues=tuple(walk.issues))
    issues = tuple(walk.issues)
    if blocked:
        return Conformance(parameters=None, issues=issues)
    return Conformance(parameters=conformed if issues else parameters, issues=issues)


def schema_issues(parameters: Any) -> list[SchemaIssue]:
    """Every way ``parameters`` falls outside the profile AS DECLARED, repairable or not."""
    return list(conform_parameters(parameters).issues)


# ── the tool seam ────────────────────────────────────────────────────────────

#: (provider, tool, verdict) already reported by this process. A runtime starts per chat
#: session, loop cycle and subagent, so without this the same line would print every time.
_reported: set[tuple[str, str, str]] = set()


def _origin(provider: str, app: str) -> str:
    if app and app != provider:
        return f"app {app!r} (provider {provider!r})"
    return f"app {app!r}" if app else f"provider {provider!r}"


def exclusion_reason(conformance: Conformance) -> str:
    """The clause naming what makes a schema unofferable: its unrepairable issues."""
    return "; ".join(issue.render() for issue in conformance.blocking)


def offered_tool_definitions(
    tools: Iterable[ToolDefinition], *, provider: str, app: str = ""
) -> list[ToolDefinition]:
    """The tools a model request may carry, each with a portable schema.

    A tool already inside the profile passes through unchanged (the same object). One that only
    needed repairs is replaced by a copy carrying the repaired schema; one that cannot be repaired
    is left out. Each repair or exclusion is logged ONCE per process, naming the app and the tool —
    the author's signal, and the operator's answer to "why can the model not see this tool".
    """
    offered: list[ToolDefinition] = []
    for tool in tools:
        verdict = conform_parameters(tool.parameters)
        if not verdict.issues:
            offered.append(tool)
            continue
        key = (provider, tool.name, json.dumps([dataclasses.astuple(i) for i in verdict.issues]))
        first = key not in _reported
        _reported.add(key)
        if verdict.parameters is None:
            if first:
                logger.warning(
                    "tool schema: %s tool %r is NOT offered to models, because its parameter "
                    "schema has no portable form: %s. The tool's author has to fix the schema.",
                    _origin(provider, app),
                    tool.name,
                    exclusion_reason(verdict),
                )
            continue
        if first:
            logger.warning(
                "tool schema: %s tool %r was repaired before being offered to models: %s. The "
                "tool's author should declare a portable schema.",
                _origin(provider, app),
                tool.name,
                "; ".join(i.render() for i in verdict.issues),
            )
        offered.append(dataclasses.replace(tool, parameters=verdict.parameters))
    return offered


# ── a provider refusing a tool definition ────────────────────────────────────

#: A path into one function declaration: ``function_declarations[15].parameters.properties[x]``.
_DECLARATION_PATH = re.compile(r"function_declarations\[(\d+)\]((?:\.\w+(?:\[[^\]]*\])?)*)")
#: A path into the request's ``tools`` array: ``tools[3].function…`` / ``tools.3.custom…``.
_TOOLS_PATH = re.compile(r"\btools(?:\[(\d+)\]|\.(\d+)(?=\.))")
_FIRST_PROPERTY = re.compile(r"\.properties\[([^\]]+)\]")
#: A rejection that names the function outright: ``Invalid schema for function 'x'``.
_NAMED_FUNCTION = re.compile(r"\b(?:function|tool)\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)
#: Words that make a ``tools[i]`` mention a DEFINITION error rather than, say, a tool choice.
_DEFINITION_WORDS = ("parameters", "input_schema", "inputschema", "schema", "properties")


def _declares(entry: dict[str, Any], prop: str) -> bool:
    params = (entry.get("function") or {}).get("parameters") or {}
    return prop.strip("'\"") in (params.get("properties") or {})


def tools_named_in_rejection(message: str, tools: Sequence[dict[str, Any]]) -> list[str]:
    """The tools of THIS request that a provider's rejection points at, in request order.

    ``tools`` is the exact ``tools=`` payload the request carried, so an index in the error maps
    to a name. Provider-agnostic on purpose: it reads the shapes a rejection takes on the wire — a
    ``function_declarations[i]`` path, a ``tools[i]``/``tools.i.`` path, or a quoted function name
    — never which provider sent it. An index is only trusted when the property the error names
    (``…properties[deliverables]…``) is one that tool really declares, so a translator that
    re-numbered the list can never make the message name the wrong tool. ``[]`` means the error
    is not about a tool definition this request sent.
    """
    if not message or not tools:
        return []
    names = [str((t.get("function") or {}).get("name", "")) for t in tools]
    named: set[str] = set()
    declarations = _DECLARATION_PATH.findall(message)
    for index, path in declarations:
        i = int(index)
        if not 0 <= i < len(tools):
            continue
        prop = (
            _FIRST_PROPERTY.match(path.split(".parameters", 1)[-1])
            if ".parameters" in path
            else None
        )
        if prop is None or _declares(tools[i], prop.group(1)):
            named.add(names[i])
    lowered = message.lower()
    # Only a message with NO declaration path reads `tools[i]` as a request index: in the
    # declaration form, `tools[0]` is the wrapper around every declaration, not tool 0.
    if not declarations and any(word in lowered for word in _DEFINITION_WORDS):
        for bracketed, dotted in _TOOLS_PATH.findall(message):
            i = int(bracketed or dotted)
            if 0 <= i < len(tools):
                named.add(names[i])
        for quoted in _NAMED_FUNCTION.findall(message):
            if quoted in names:
                named.add(quoted)
    return [name for name in names if name in named]


def _listed(names: Sequence[str]) -> str:
    quoted = [f'"{n}"' for n in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


class ToolSchemaRejected(Exception):
    """A model provider refused a request because of a tool definition PersonalClaw sent.

    The string IS the user-facing sentence (``llm_helpers.humanize_provider_error`` passes it
    through), because the raw provider dump is unreadable and blames nobody: this says which tool
    and that it is PersonalClaw's bug. The workaround it offers is exactly what works: a session
    keeps the toolset it started with, so switching the tool off helps a NEW conversation, not the
    open one — and a core-locked tool (``can_turn_off=False``) cannot be switched off at all, so
    for it the sentence offers nothing.
    """

    def __init__(self, tools: Sequence[str], *, can_turn_off: bool) -> None:
        self.tools = tuple(tools)
        self.can_turn_off = can_turn_off
        super().__init__(self.sentence())

    def sentence(self, *, room: bool = False) -> str:
        """The sentence for a chat, or for a room (where the fresh start is a new room)."""
        plural = len(self.tools) > 1
        sentence = (
            f"The model provider rejected PersonalClaw's definition{'s' if plural else ''} of the "
            f"{_listed(self.tools)} tool{'s' if plural else ''}, so this turn could not run — that "
            "is a bug in PersonalClaw, not something you did"
        )
        if self.can_turn_off:
            which = "those tools" if plural else "that tool"
            sentence += (
                f"; turn {which} off on the Tools page and start a new "
                f"{'room' if room else 'chat'} to keep going until it is fixed"
            )
        return sentence + "."
