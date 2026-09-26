#!/usr/bin/env python3
"""The app SDK's signatures, checked in, so an SDK change is a diff somebody reviewed.

``personalclaw.sdk.*`` is the only import path an app may use, and it is released separately from
the apps that use it. #3599 changed ``compress_thread_history`` from taking a ``ConversationLog``
to taking a list of turns and kept the parameter where it was. Every app import still resolved
and every call still bound; the Slack app then raised ``TypeError`` inside the call, its handler
swallowed it, and every restored Slack thread lost its history. Nothing in core's review showed
that a published signature had changed at all.

This module renders every published symbol — each name in each ``sdk`` submodule's ``__all__``
— to a JSON record: a function's parameters (name, kind, default, annotation as text) and return
annotation; a class's bases, constructor and public methods and properties; an enum's members; a
constant's type and value. The records live in ``src/personalclaw/sdk/signatures.json``, and
``tests/test_sdk_signature_snapshot.py`` fails until the file matches the code. Because the file
sits in ``src/personalclaw/sdk/``, a change to a re-exported core function (#3599 touched only
``context.py``) still touches the SDK directory, which is what ``ci.yml``'s ``apps-contract`` job
keys on.

THE LOUD-BREAK RULE. A parameter that keeps its place and changes its type breaks old callers
silently: they still bind, and fail inside the body, where an app can swallow the error. When a
parameter's type changes, rename it AND make it keyword-only, so an old call fails at bind time,
where the apps' contract rails see it. Writing the snapshot refuses such an in-place change unless
``--allow-in-place-type-change`` says it is a widening (every value an old caller passes is still
valid).

Usage (from the repo root)::

    python scripts/sdk_signature_snapshot.py          # rewrite the snapshot, print what changed
    python scripts/sdk_signature_snapshot.py --check  # exit 1 with the diff if it has drifted
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import importlib
import inspect
import json
import pkgutil
import re
import sys
import types
import typing
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = REPO / "src" / "personalclaw" / "sdk" / "signatures.json"
SDK_PACKAGE = "personalclaw.sdk"

#: What a reviewer is told to do when the snapshot is stale. One sentence, shared by the
#: generator, the test and the CI job, so the three never give different instructions.
REGENERATE = (
    "run `python scripts/sdk_signature_snapshot.py`, review the diff, and add a CHANGELOG entry "
    "naming the apps the change affects (CONTRIBUTING.md#sdk-changes)"
)

_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")

_POSITIONAL = ("positional_only", "positional_or_keyword")
_KEYWORD = ("positional_or_keyword", "keyword_only")


# ── rendering ────────────────────────────────────────────────────────────────────────────────


def render_annotation(ann: Any) -> str:
    """An annotation as stable text: source text for a string, a normal form for an object.

    Classes render by qualified name without their module, so moving a class between core
    modules is not an SDK change; ``Optional[X]`` and ``X | None`` render alike.
    """
    if isinstance(ann, str):
        text = " ".join(ann.split())
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
            text = text[1:-1]
        return text
    if ann is None or ann is type(None):
        return "None"
    if ann is Ellipsis:
        return "..."
    if isinstance(ann, typing.ForwardRef):
        return render_annotation(ann.__forward_arg__)
    if isinstance(ann, typing.TypeVar):
        return ann.__name__
    if isinstance(ann, (list, tuple)):
        return "[" + ", ".join(render_annotation(a) for a in ann) + "]"
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)
    if origin is typing.Union or origin is types.UnionType:
        return " | ".join(render_annotation(a) for a in args)
    if origin is typing.Literal:
        return "Literal[" + ", ".join(repr(a) for a in args) + "]"
    if origin is typing.Annotated:
        return render_annotation(args[0])
    if origin is not None:
        head = render_annotation(origin)
        return f"{head}[{', '.join(render_annotation(a) for a in args)}]" if args else head
    if isinstance(ann, type):
        return ann.__qualname__
    return _ADDRESS.sub("", repr(ann)).replace("typing.", "")


def render_value(value: Any) -> str:
    """A default or a constant as stable text (set members sorted, addresses dropped)."""
    if isinstance(value, (set, frozenset)):
        inner = ", ".join(sorted(render_value(v) for v in value))
        return f"{type(value).__name__}({{{inner}}})"
    if isinstance(value, dict):
        inner = ", ".join(
            f"{render_value(k)}: {render_value(v)}"
            for k, v in sorted(value.items(), key=lambda kv: repr(kv[0]))
        )
        return "{" + inner + "}"
    if isinstance(value, (list, tuple)):
        inner = ", ".join(render_value(v) for v in value)
        return (
            f"({inner}{',' if len(value) == 1 else ''})"
            if isinstance(value, tuple)
            else (f"[{inner}]")
        )
    if isinstance(value, enum.Enum):
        return f"{type(value).__qualname__}.{value.name}"
    if isinstance(value, type):
        return value.__qualname__
    if callable(value) and hasattr(value, "__qualname__"):
        return f"<callable {value.__qualname__}>"
    return _ADDRESS.sub("", repr(value))


def signature_record(fn: Any, *, bound: bool = False) -> dict[str, Any] | None:
    """``{"params": [...], "returns": ...}`` for a callable, or ``None`` when it has none.

    ``bound`` drops the first parameter (``self`` / ``cls``): a caller never passes it, so it is
    not part of what a call binds to, and positions are counted the way a caller counts them.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    params = []
    for i, p in enumerate(sig.parameters.values()):
        if bound and i == 0 and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            continue
        entry: dict[str, Any] = {"name": p.name, "kind": p.kind.name.lower()}
        if p.annotation is not inspect.Parameter.empty:
            entry["annotation"] = render_annotation(p.annotation)
        if p.default is not inspect.Parameter.empty:
            entry["default"] = render_value(p.default)
        params.append(entry)
    out: dict[str, Any] = {"params": params}
    if sig.return_annotation is not inspect.Signature.empty:
        out["returns"] = render_annotation(sig.return_annotation)
    return out


def _function_kind(fn: Any) -> str:
    if inspect.isasyncgenfunction(fn):
        return "async generator function"
    if inspect.iscoroutinefunction(fn):
        return "async function"
    return "function"


def _defining_class(cls: type, name: str) -> type | None:
    for klass in cls.__mro__:
        if name in vars(klass):
            return klass
    return None


def _members(cls: type) -> dict[str, Any]:
    """Public methods and properties a caller of ``cls`` reaches: its own and core-inherited."""
    out: dict[str, Any] = {}
    # A dataclass field's default lives on the class too; a callable default is not a method.
    fields = {f.name for f in dataclasses.fields(cls)} if dataclasses.is_dataclass(cls) else set()
    for name in sorted(dir(cls)):
        if name.startswith("_") or name in fields:
            continue
        owner = _defining_class(cls, name)
        if owner is None or not str(getattr(owner, "__module__", "")).startswith("personalclaw"):
            continue  # object / ABC / Protocol / Exception machinery is not this contract
        raw = vars(owner)[name]
        if isinstance(raw, property):
            prop: dict[str, Any] = {"kind": "property", "settable": raw.fset is not None}
            ret = signature_record(raw.fget) if raw.fget is not None else None
            if ret and "returns" in ret:
                prop["returns"] = ret["returns"]
            out[name] = prop
        elif isinstance(raw, staticmethod):
            out[name] = {"kind": "staticmethod", **(signature_record(raw.__func__) or {})}
        elif isinstance(raw, classmethod):
            out[name] = {
                "kind": "classmethod",
                **(signature_record(raw.__func__, bound=True) or {}),
            }
        elif inspect.isfunction(raw):
            out[name] = {
                "kind": _function_kind(raw).replace("function", "method"),
                **(signature_record(raw, bound=True) or {}),
            }
        elif isinstance(raw, type):
            out[name] = {"kind": "nested class"}
    return out


def record(obj: Any) -> dict[str, Any]:
    """The JSON record for one published object."""
    if isinstance(obj, types.ModuleType):
        return {"kind": "module", "module": obj.__name__}
    if isinstance(obj, type) and issubclass(obj, enum.Enum):
        return {
            "kind": "enum",
            "bases": [b.__qualname__ for b in obj.__bases__],
            "members": {m.name: render_value(m.value) for m in obj},
        }
    if isinstance(obj, type):
        rec: dict[str, Any] = {
            "kind": "dataclass" if dataclasses.is_dataclass(obj) else "class",
            "bases": [b.__qualname__ for b in obj.__bases__],
        }
        init = signature_record(obj)
        if init is not None:
            rec["init"] = init
        members = _members(obj)
        if members:
            rec["members"] = members
        return rec
    if inspect.isfunction(obj) or inspect.isbuiltin(obj):
        return {"kind": _function_kind(obj), **(signature_record(obj) or {})}
    if typing.get_origin(obj) is not None:
        return {"kind": "type alias", "value": render_annotation(obj)}
    if callable(obj):
        return {"kind": f"callable {type(obj).__qualname__}", **(signature_record(obj) or {})}
    return {"kind": "value", "type": type(obj).__qualname__, "value": render_value(obj)}


def published() -> dict[str, Any]:
    """``{"personalclaw.sdk.<module>.<name>": object}`` for every name any SDK module publishes."""
    sdk = importlib.import_module(SDK_PACKAGE)
    modules = {SDK_PACKAGE: sdk}
    for info in pkgutil.iter_modules(sdk.__path__):
        name = f"{SDK_PACKAGE}.{info.name}"
        modules[name] = importlib.import_module(name)
    out: dict[str, Any] = {}
    for mod_name, module in sorted(modules.items()):
        for name in getattr(module, "__all__", ()):
            out[f"{mod_name}.{name}"] = getattr(module, name)
    return out


def snapshot() -> dict[str, Any]:
    """The live SDK's records, keyed by the dotted name an app imports.

    A class or function published under two names (``sdk.channel.ModelProvider`` and
    ``sdk.model.ModelProvider``) is recorded once, under the first name, and the second is an
    ``alias`` of it: one object is one contract, and a change to it is one diff.
    """
    out: dict[str, Any] = {}
    first: dict[int, str] = {}
    for key, obj in sorted(published().items()):
        if isinstance(obj, type) or inspect.isfunction(obj):
            if id(obj) in first:
                out[key] = {"kind": "alias", "of": first[id(obj)]}
                continue
            first[id(obj)] = key
        out[key] = record(obj)
    return out


def render(snap: dict[str, Any]) -> str:
    """The snapshot as JSON, one parameter per line: a changed parameter is a one-line diff."""
    return _render_node(snap, 0) + "\n"


def _render_node(node: Any, depth: int) -> str:
    flat = json.dumps(node, sort_keys=True, ensure_ascii=False)
    if not isinstance(node, (dict, list)) or not any(
        isinstance(v, (dict, list)) for v in (node.values() if isinstance(node, dict) else node)
    ):
        return flat  # scalars, and containers of scalars (a parameter, a bases list): one line
    pad, inner = " " * depth, " " * (depth + 1)
    if isinstance(node, list):
        body = ",\n".join(inner + _render_node(v, depth + 1) for v in node)
        return f"[\n{body}\n{pad}]"
    body = ",\n".join(
        f"{inner}{json.dumps(k, ensure_ascii=False)}: {_render_node(node[k], depth + 1)}"
        for k in sorted(node)
    )
    return f"{{\n{body}\n{pad}}}"


def load(path: Path = SNAPSHOT_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ── the diff ─────────────────────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class Change:
    """One symbol whose record differs between two snapshots."""

    symbol: str
    kind: str  # "added" | "removed" | "changed"
    details: list[str]
    #: Nothing an existing caller relies on moved: only new members, or new parameters that have
    #: a default. Additions still need a CHANGELOG entry; they affect no app.
    additive: bool
    #: The loud-break rule's violations (:func:`silent_breaks`): an old call that still binds
    #: and now means something else. Writing the snapshot refuses these.
    silent_breaks: list[str]
    #: The same shape on a dataclass constructor, where callers construct by keyword: listed so
    #: a reviewer sees it, not refused.
    shifted_fields: list[str] = dataclasses.field(default_factory=list)

    def describe(self) -> str:
        lines = [f"{self.kind.upper()}: {self.symbol}"]
        lines += [f"    {d}" for d in self.details]
        lines += [
            f"    ⚠ silent break: {t} — an old call still binds and fails or misbehaves inside; "
            "rename the parameter and make it keyword-only"
            for t in self.silent_breaks
        ]
        lines += [
            f"    · dataclass field moved: {t} — only a POSITIONAL constructor call shifts"
            for t in self.shifted_fields
        ]
        return "\n".join(lines)


def _params_by_position(sig: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in sig.get("params", []) if p["kind"] in _POSITIONAL]


def _params_by_keyword(sig: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in sig.get("params", []) if p["kind"] in _KEYWORD}


def silent_breaks(
    old: dict[str, Any], new: dict[str, Any], where: str, *, dataclass_init: bool = False
) -> tuple[list[str], list[str]]:
    """``(breaks, shifted_fields)``: calls to ``old`` that still bind to ``new`` but mean otherwise.

    Three shapes, all invisible to a bind check and so to every static rail:

    * **retyped** — same parameter, same place, new annotation (``Permissions.memory``,
      ``str`` → ``bool``): the old value is still accepted, and wrong;
    * **replaced** — the old name is gone and a new parameter with a different annotation took
      its position (#3599: ``conversation_log: ConversationLog`` → ``prior_turns: list[dict]``);
    * **shifted** — a parameter was inserted or moved, so an old positional argument lands in a
      different parameter (#3599 inserted ``prior_transcript`` at position 11 of
      ``build_message``).

    A shift on a DATACLASS constructor is reported separately: those are built by keyword (a
    field is inserted wherever it belongs), and refusing every such insertion would make the
    override routine. A retyped field is still a break — a keyword caller passes the old type.
    """
    breaks: list[str] = []
    shifted: list[str] = []
    retyped: set[str] = set()
    new_all = {p["name"]: p for p in new.get("params", [])}
    old_pos, new_pos = _params_by_position(old), _params_by_position(new)
    for i, p in enumerate(old_pos):
        if i >= len(new_pos):
            break  # fewer positional parameters now: an old call that passes one fails loudly
        q = new_pos[i]
        pa, qa = p.get("annotation"), q.get("annotation")
        if p["name"] == q["name"]:
            if pa and qa and pa != qa:
                breaks.append(f"{where}: {p['name']} (position {i}) retyped: {pa} → {qa}")
                retyped.add(p["name"])
        elif p["name"] in new_all:
            line = f"{where}: position {i} was {p['name']}, is now {q['name']}"
            (shifted if dataclass_init else breaks).append(line)
        elif pa and qa and pa != qa:
            # On a dataclass the old field NAME is gone, so a keyword constructor call fails
            # loudly; only a positional one shifts — the same exposure as an insertion.
            line = f"{where}: position {i} replaced: {p['name']}: {pa} → {q['name']}: {qa}"
            (shifted if dataclass_init else breaks).append(line)
    new_kw = _params_by_keyword(new)
    for name, p in _params_by_keyword(old).items():
        q = new_kw.get(name)
        if q is None or name in retyped:
            continue
        pa, qa = p.get("annotation"), q.get("annotation")
        if pa and qa and pa != qa:
            breaks.append(f"{where}: {name} (keyword) retyped: {pa} → {qa}")
    return breaks, shifted


def _signature_additive(
    old: dict[str, Any], new: dict[str, Any], *, keyword_callers: bool = False
) -> bool:
    """Every call that bound to ``old`` binds to ``new`` with the same meaning.

    ``keyword_callers`` (a dataclass constructor) ignores where a parameter sits.
    """
    if old.get("returns") != new.get("returns"):
        return False
    old_params, new_params = old.get("params", []), new.get("params", [])
    new_by_name = {p["name"]: p for p in new_params}
    for i, p in enumerate(old_params):
        q = new_by_name.get(p["name"])
        if q is None or q.get("annotation") != p.get("annotation"):
            return False
        if "default" in p and q.get("default") != p["default"]:
            return False
        if p["kind"] in _KEYWORD and q["kind"] not in _KEYWORD:
            return False
        if keyword_callers:
            continue
        if p["kind"] in _POSITIONAL and (i >= len(new_params) or new_params[i] is not q):
            return False
        if p["kind"] in _POSITIONAL and q["kind"] not in _POSITIONAL:
            return False
    old_names = {p["name"] for p in old_params}
    return all(
        "default" in q or q["kind"] in ("var_positional", "var_keyword")
        for q in new_params
        if q["name"] not in old_names
    )


def _compare_members(short: str, om: dict[str, Any], nm: dict[str, Any], change: Change) -> None:
    for name in sorted(set(om) | set(nm)):
        a, b = om.get(name), nm.get(name)
        if a == b:
            continue
        if a is None:
            change.details.append(f"+ {name}: {_member_text(b)}")
            continue
        if b is None:
            change.additive = False
            change.details.append(f"- {name}: {_member_text(a)}")
            continue
        change.details.append(f"~ {name}: {_member_text(a)} → {_member_text(b)}")
        if a.get("kind") != b.get("kind") or a.get("kind") == "property":
            change.additive = False
        if "params" in a or "params" in b:
            if not _signature_additive(a, b):
                change.additive = False
            breaks, _ = silent_breaks(a, b, f"{short}.{name}")
            change.silent_breaks += breaks


def _compare_enum(old: dict[str, Any], new: dict[str, Any], change: Change) -> None:
    om, nm = old.get("members", {}), new.get("members", {})
    for name in sorted(set(om) | set(nm)):
        if om.get(name) == nm.get(name):
            continue
        if name not in om:
            change.details.append(f"+ {name} = {nm[name]}")
            continue
        change.additive = False
        if name not in nm:
            change.details.append(f"- {name} = {om[name]}")
        else:
            change.details.append(f"~ {name}: {om[name]} → {nm[name]}")


def _compare(symbol: str, old: dict[str, Any], new: dict[str, Any]) -> Change | None:
    if old == new:
        return None
    short = symbol.rsplit(".", 1)[-1]
    change = Change(symbol, "changed", [], old.get("kind") == new.get("kind"), [])
    if old.get("kind") != new.get("kind"):
        change.details.append(f"kind: {old.get('kind')} → {new.get('kind')}")
    if old.get("bases") != new.get("bases"):
        change.additive = False
        change.details.append(f"bases: {old.get('bases')} → {new.get('bases')}")
    if old.get("kind") == "enum" and new.get("kind") == "enum":
        _compare_enum(old, new, change)
        return change
    if "params" in old or "params" in new:
        if not _signature_additive(old, new):
            change.additive = False
        change.details.append(f"signature: {_sig_text(old)} → {_sig_text(new)}")
        breaks, _ = silent_breaks(old, new, short)
        change.silent_breaks += breaks
    oi, ni = old.get("init", {}), new.get("init", {})
    if oi != ni:
        dataclass_init = new.get("kind") == "dataclass" and old.get("kind") == "dataclass"
        if not _signature_additive(oi, ni, keyword_callers=dataclass_init):
            change.additive = False
        change.details.append(f"constructor: {_sig_text(oi)} → {_sig_text(ni)}")
        breaks, shifted = silent_breaks(oi, ni, f"{short}()", dataclass_init=dataclass_init)
        change.silent_breaks += breaks
        change.shifted_fields += shifted
    _compare_members(short, old.get("members", {}), new.get("members", {}), change)
    for key in ("value", "type", "module", "of", "returns"):
        if key == "returns" and ("params" in old or "params" in new):
            continue  # already part of the signature line above
        if old.get(key) != new.get(key):
            change.additive = False
            change.details.append(f"{key}: {old.get(key)} → {new.get(key)}")
    if not change.details:
        change.additive = False
        change.details.append(
            f"{json.dumps(old, sort_keys=True)} → {json.dumps(new, sort_keys=True)}"
        )
    if change.silent_breaks:
        change.additive = False
    return change


def _sig_text(sig: dict[str, Any]) -> str:
    parts = []
    seen_kw_marker = False
    for p in sig.get("params", []):
        if p["kind"] == "keyword_only" and not seen_kw_marker:
            parts.append("*")
            seen_kw_marker = True
        prefix = {"var_positional": "*", "var_keyword": "**"}.get(p["kind"], "")
        if p["kind"] == "var_positional":
            seen_kw_marker = True
        text = prefix + p["name"]
        if "annotation" in p:
            text += f": {p['annotation']}"
        if "default" in p:
            text += f" = {p['default']}"
        parts.append(text)
    out = f"({', '.join(parts)})"
    if "returns" in sig:
        out += f" -> {sig['returns']}"
    return out


def _member_text(member: dict[str, Any]) -> str:
    if member.get("kind") == "property":
        return f"property -> {member.get('returns', '?')}" + (
            " (settable)" if member.get("settable") else ""
        )
    if "params" in member:
        return f"{member['kind']} {_sig_text(member)}"
    return member.get("kind", "?")


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    """Every symbol whose record differs, sorted by name."""
    changes: list[Change] = []
    for symbol in sorted(set(old) | set(new)):
        if symbol not in old:
            changes.append(Change(symbol, "added", [_summary(new[symbol])], True, []))
        elif symbol not in new:
            changes.append(Change(symbol, "removed", [_summary(old[symbol])], False, []))
        else:
            change = _compare(symbol, old[symbol], new[symbol])
            if change is not None:
                changes.append(change)
    return changes


def _summary(rec: dict[str, Any]) -> str:
    if "params" in rec:
        return f"{rec['kind']} {_sig_text(rec)}"
    if "init" in rec:
        return f"{rec['kind']} {_sig_text(rec['init'])}"
    if "value" in rec:
        return f"{rec['kind']} {rec['value']}"
    return rec.get("kind", "?")


def report(changes: list[Change]) -> str:
    return "\n".join(c.describe() for c in changes)


# ── the command ──────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the snapshot drifted")
    parser.add_argument(
        "--allow-silent-break",
        action="store_true",
        help="record a retyped, replaced or shifted parameter anyway: only for a WIDENING, where "
        "every call an old caller makes still means what it meant",
    )
    parser.add_argument("--path", type=Path, default=SNAPSHOT_PATH, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    live = snapshot()
    stored = load(args.path) if args.path.is_file() else {}
    changes = diff(stored, live)
    if args.check:
        if not changes:
            print(f"SDK snapshot current: {len(live)} published symbols")
            return 0
        print(f"The SDK changed; {REGENERATE}:\n\n{report(changes)}")
        return 1
    if not changes:
        print(f"SDK snapshot already current: {len(live)} published symbols")
        return 0
    if any(c.silent_breaks for c in changes) and not args.allow_silent_break:
        print(
            "Refusing to record a parameter change an old caller cannot see. Its call still binds "
            "and then fails, or means something else, inside the function, where an app can "
            "swallow the error (#3599). Rename the parameter and make it keyword-only (or add a "
            "new parameter at the end, keyword-only); if the change is a widening, re-run with "
            "--allow-silent-break.\n\n" + report(changes)
        )
        return 1
    args.path.write_text(render(live), encoding="utf-8")
    shown = args.path.relative_to(REPO) if args.path.is_relative_to(REPO) else args.path
    print(f"Wrote {shown} ({len(live)} symbols):\n\n{report(changes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
