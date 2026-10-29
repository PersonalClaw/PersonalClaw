#!/usr/bin/env python3
"""The typed turn's JSON schema, generated from ``personalclaw.turns``, and the web types from it.

``src/personalclaw/turns.py`` is the one definition of a chat turn's shape (shared contracts §3.3).
This script renders it as a JSON schema (``schemas/turns.schema.json``), and renders that schema as
TypeScript (``web/src/lib/generated/turns.ts``), so the web reads the same field names and the same
closed words without anyone copying them by hand (§3.11: the frontend's types are the backend's).
``tests/test_turns_schema_current.py`` fails until both files match the classes, so a change to a
turn's shape is a reviewed diff in all three. Nothing reads the schema at run time, so it sits
beside its generator, outside the package.

A field is required in both when ``to_dict`` always writes it: it has no default, or the class
names it in ``_KEEP``. A part carries its ``kind`` as a constant, and ``Part`` is the union of
every kind in ``turns.PART_KINDS``. It needs nothing outside the standard library.

Usage (from the repo root)::

    python scripts/generate_turn_types.py          # rewrite both files
    python scripts/generate_turn_types.py --check  # exit 1, naming the file, if either has drifted
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import types
import typing
from pathlib import Path
from typing import Any, Literal, Union

from personalclaw import turns

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "schemas" / "turns.schema.json"
WEB = REPO / "web" / "src" / "lib" / "generated" / "turns.ts"


def _classes() -> list[type]:
    """Every type of the module, in a fixed order: the shapes, then the parts, then the message."""
    found: list[type] = []

    def visit(cls: type) -> None:
        if cls in found or cls is turns.Part:
            return
        for tp in typing.get_type_hints(cls).values():
            for inner in _named(tp):
                visit(inner)
        found.append(cls)

    for cls in (*turns.PART_KINDS.values(), turns.Message, turns.EndingSummary):
        visit(cls)
    return found


def _named(tp: Any) -> list[type]:
    """The classes of this module a type annotation refers to."""
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return [tp]
    return [c for arg in typing.get_args(tp) for c in _named(arg)]


def _fields(cls: type) -> list[tuple[str, Any, bool]]:
    """``(name, annotation, required)`` for each field of *cls*, in declaration order."""
    hints = typing.get_type_hints(cls)
    keep: frozenset[str] = getattr(cls, "_KEEP", frozenset())
    out = []
    for f in dataclasses.fields(cls):
        has_default = f.default is not dataclasses.MISSING or (
            f.default_factory is not dataclasses.MISSING
        )
        out.append((f.name, hints[f.name], f.name in keep or not has_default))
    return out


def _is_optional(tp: Any) -> bool:
    return typing.get_origin(tp) in (Union, types.UnionType) and type(None) in typing.get_args(tp)


# ── JSON schema ────────────────────────────────────────────────────────────────────────────────


def _schema_of(tp: Any) -> dict[str, Any]:
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if tp is Any:
        return {}
    if origin in (Union, types.UnionType):
        return {"anyOf": [_schema_of(a) for a in args]}
    if origin is Literal:
        return {"enum": list(args)}
    if origin is tuple:
        return {"type": "array", "items": _schema_of(args[0])}
    if origin is dict:
        return {"type": "object"}
    if tp is type(None):
        return {"type": "null"}
    if tp is turns.Part:
        return {"$ref": "#/$defs/Part"}
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return {"$ref": f"#/$defs/{tp.__name__}"}
    simple = {str: "string", int: "integer", float: "number", bool: "boolean"}
    return {"type": simple[tp]}


def render_schema() -> str:
    defs: dict[str, Any] = {}
    for cls in _classes():
        props: dict[str, Any] = {}
        required: list[str] = []
        kind = getattr(cls, "KIND", "")
        if kind:
            props["kind"] = {"const": kind}
            required.append("kind")
        for name, tp, needed in _fields(cls):
            props[name] = _schema_of(tp)
            if needed:
                required.append(name)
        defs[cls.__name__] = {
            "type": "object",
            "properties": props,
            "required": required,
            "additionalProperties": False,
        }
    defs["Part"] = {"oneOf": [{"$ref": f"#/$defs/{c.__name__}"} for c in turns.PART_KINDS.values()]}
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "A chat message and its parts (generated from personalclaw.turns)",
        "$ref": "#/$defs/Message",
        "$defs": defs,
    }
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


# ── TypeScript ─────────────────────────────────────────────────────────────────────────────────


def _ts_of(node: dict[str, Any]) -> str:
    """A JSON schema node as a TypeScript type."""
    if "$ref" in node:
        return str(node["$ref"]).rsplit("/", 1)[1]
    if "const" in node:
        return json.dumps(node["const"])
    if "enum" in node:
        return " | ".join(json.dumps(v) for v in node["enum"])
    if "anyOf" in node:
        return " | ".join(_ts_of(n) for n in node["anyOf"])
    if node.get("type") == "array":
        inner = _ts_of(node["items"])
        return f"({inner})[]" if "|" in inner else f"{inner}[]"
    simple = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean"}
    if node.get("type") in simple:
        return simple[node["type"]]
    if node.get("type") == "null":
        return "null"
    if node.get("type") == "object":
        return "Record<string, unknown>"
    return "unknown"


def render_ts(schema: dict[str, Any] | None = None) -> str:
    """*schema* (the rendered schema, by default) as the web's TypeScript."""
    defs = (schema or json.loads(render_schema()))["$defs"]
    lines = [
        "// Generated by scripts/generate_turn_types.py from schemas/turns.schema.json, itself",
        "// generated from src/personalclaw/turns.py: do not edit. Run `make turns-schema` after",
        "// changing a turn's shape; tests/test_turns_schema_current.py fails until this matches.",
        "",
    ]
    for name, node in defs.items():
        if "oneOf" in node:
            union = " | ".join(_ts_of(n) for n in node["oneOf"])
            lines += [f"export type {name} = {union}", ""]
            continue
        lines.append(f"export interface {name} {{")
        required = set(node["required"])
        for field_name, prop in node["properties"].items():
            lines.append(f"  {field_name}{'' if field_name in required else '?'}: {_ts_of(prop)}")
        lines += ["}", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a file has drifted")
    args = parser.parse_args(argv)
    drift = []
    for path, text in ((SCHEMA, render_schema()), (WEB, render_ts())):
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == text:
            continue
        drift.append(path.relative_to(REPO).as_posix())
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    if args.check and drift:
        print("out of date, run `make turns-schema`: " + ", ".join(drift), file=sys.stderr)
        return 1
    print("rewrote " + ", ".join(drift) if drift else "both files are current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
