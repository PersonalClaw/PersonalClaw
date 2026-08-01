"""Binding expressions — how a node's output becomes another node's input.

`{{nodes.classify.output.findings | filter('verdict','CONFIRMED') | count}}`

Two resolution paths, deliberately distinct (WF2-R9):

* **Whole-value** — the string is exactly one `{{…}}` ref, so the SOURCE TYPE is
  preserved. `{{nodes.x.output}}` yields the dict, not `"{'a': 1}"`. A `foreach` over
  `{{nodes.x.output.items}}` needs a real list.
* **Interpolated** — the ref sits inside a larger string, so it stringifies. Scalars
  render bare; containers go through `json.dumps`, because embedding a Python `repr`
  into a prompt produces single-quoted pseudo-JSON that models reproduce badly.

The failure modes are also deliberately different. "The node produced null" flows
through as a value (`filter` drops nulls, a declarative `.filter(Boolean)`), while
"this reference does not resolve" — unknown node id, missing path — raises a typed
`BindingError` that gets journaled on the node. A silent empty string here is how a
prompt ends up quietly missing its input and the run produces confident nonsense.

Pipes are a CLOSED set of pure functions. No arbitrary expressions: a spec is data the
flywheel will later propose diffs to, and an eval-shaped hole in it is a remote-code
path with extra steps.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

#: One `{{ … }}` occurrence. Non-greedy so adjacent refs don't merge.
_REF_RE = re.compile(r"\{\{(.+?)\}\}")

#: The whole string is exactly one ref (whole-value path).
_WHOLE_RE = re.compile(r"^\s*\{\{(.+?)\}\}\s*$")

#: A pipe call: `name` or `name('a','b')` / `name(3)`.
_PIPE_RE = re.compile(r"^([a-z_]+)\s*(?:\((.*)\))?$")


class BindingError(Exception):
    """A reference that cannot be resolved, or a pipe misuse.

    Carries the expression so the journal entry names what broke rather than just
    where. The engine turns this into a typed node failure — never an empty string.
    """

    def __init__(self, message: str, expr: str = "") -> None:
        self.expr = expr
        super().__init__(f"{message} (in {{{{{expr}}}}})" if expr else message)


@dataclass
class BindingContext:
    """Everything a binding may read. Anything absent is a resolution failure, not a
    default — the caller decides what is optional by pre-populating it."""

    inputs: dict[str, Any] | None = None
    #: node id → its structured output.
    node_outputs: dict[str, Any] | None = None
    #: node id → artifact pointer, for outputs offloaded out of the run state.
    node_artifacts: dict[str, str] | None = None
    item: Any = None  # foreach current item
    has_item: bool = False
    iter_index: int | None = None  # loop iteration
    last_output: Any = None  # loop body's previous iteration
    has_last: bool = False
    #: Resolver for `{{secret:KEY}}`. Injected so nothing here reads the credential
    #: store directly — that also keeps secrets out of unit tests by default.
    secret_resolver: Any = None

    def as_root(self) -> dict[str, Any]:
        root: dict[str, Any] = {
            "inputs": dict(self.inputs or {}),
            "nodes": {nid: {"output": out} for nid, out in (self.node_outputs or {}).items()},
        }
        for nid, ref in (self.node_artifacts or {}).items():
            root["nodes"].setdefault(nid, {})["artifact"] = ref
        if self.has_item:
            root["item"] = self.item
        if self.iter_index is not None:
            root["iter"] = self.iter_index
        if self.has_last:
            root["last"] = {"output": self.last_output}
        return root


# ── pipes (closed set, pure) ─────────────────────────────────────────────────


def _pipe_filter(value: Any, key: str = "", expected: Any = None) -> Any:
    """`filter('verdict','CONFIRMED')` keeps matching dicts. With no args it is the
    declarative `.filter(Boolean)` — which is also how a null-producing node's output
    disappears instead of poisoning the list."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise BindingError("filter expects a list")
    if not key:
        return [v for v in value if v]
    return [v for v in value if isinstance(v, dict) and v.get(key) == expected]


def _pipe_map(value: Any, key: str = "") -> Any:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BindingError("map expects a list")
    if not key:
        raise BindingError("map requires a key: map('field')")
    return [v.get(key) if isinstance(v, dict) else None for v in value]


def _pipe_flatten(value: Any) -> Any:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BindingError("flatten expects a list")
    out: list[Any] = []
    for v in value:
        out.extend(v) if isinstance(v, list) else out.append(v)
    return out


def _pipe_slice(value: Any, start: Any = 0, stop: Any = None) -> Any:
    if value is None:
        return []
    if not isinstance(value, (list, str)):
        raise BindingError("slice expects a list or string")
    try:
        s = int(start)
        e = None if stop is None else int(stop)
    except (TypeError, ValueError) as exc:
        raise BindingError("slice bounds must be integers") from exc
    return value[s:e]


def _pipe_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, str, dict)):
        return len(value)
    raise BindingError("count expects a list, string, or object")


def _pipe_default(value: Any, fallback: Any = "") -> Any:
    """Substitutes for a null/empty value. This is the ONLY sanctioned way a binding
    yields a fallback — an unresolvable *reference* still raises."""
    return fallback if value in (None, "", [], {}) else value


def _pipe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _pipe_xml_escape(value: Any) -> str:
    s = value if isinstance(value, str) else _pipe_json(value)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _pipe_truncate(value: Any, limit: Any = 1000) -> str:
    s = value if isinstance(value, str) else _pipe_json(value)
    try:
        n = int(limit)
    except (TypeError, ValueError) as exc:
        raise BindingError("truncate limit must be an integer") from exc
    if n <= 0:
        raise BindingError("truncate limit must be positive")
    return s if len(s) <= n else s[:n] + "…"


def _pipe_slugify(value: Any) -> str:
    s = value if isinstance(value, str) else str(value)
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


#: Sanitization pipes exist so a template author can neutralize untrusted content
#: inline; the spec lint (validator) is what makes their use non-optional on
#: untrusted-origin bindings.
PIPES: dict[str, Any] = {
    "filter": _pipe_filter,
    "map": _pipe_map,
    "flatten": _pipe_flatten,
    "slice": _pipe_slice,
    "count": _pipe_count,
    "default": _pipe_default,
    "json": _pipe_json,
    "tojson": _pipe_json,  # alias — templates use both spellings
    "xml_escape": _pipe_xml_escape,
    "truncate": _pipe_truncate,
    "slugify": _pipe_slugify,
}


def _parse_pipe_args(raw: str) -> list[Any]:
    """Parse a pipe's argument list. Literals only — quoted strings, ints, floats,
    bools, null. No identifiers, so an argument can never name a variable."""
    if not (raw or "").strip():
        return []
    args: list[Any] = []
    for part in _split_args(raw):
        tok = part.strip()
        if not tok:
            continue
        if (tok[0] == tok[-1] == "'" or tok[0] == tok[-1] == '"') and len(tok) >= 2:
            args.append(tok[1:-1])
            continue
        low = tok.lower()
        if low in ("true", "false"):
            args.append(low == "true")
            continue
        if low in ("null", "none"):
            args.append(None)
            continue
        try:
            args.append(int(tok))
            continue
        except ValueError:
            pass
        try:
            args.append(float(tok))
            continue
        except ValueError as exc:
            raise BindingError(f"pipe argument {tok!r} is not a literal") from exc
    return args


def _split_args(raw: str) -> list[str]:
    """Split on top-level commas, respecting quotes."""
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    for ch in raw:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            continue
        if ch == ",":
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return out


# ── path resolution ──────────────────────────────────────────────────────────

_MISSING = object()


def _walk_path(root: Any, path: str, expr: str) -> Any:
    """Follow a dotted path. Missing segments raise — see the module docstring on why
    this is not a silent empty string."""
    cur: Any = root
    for seg in [s for s in path.split(".") if s]:
        if isinstance(cur, dict):
            nxt = cur.get(seg, _MISSING)
        elif isinstance(cur, list) and seg.isdigit():
            idx = int(seg)
            nxt = cur[idx] if 0 <= idx < len(cur) else _MISSING
        else:
            raise BindingError(f"cannot read {seg!r} from a {type(cur).__name__}", expr)
        if nxt is _MISSING:
            raise BindingError(f"unresolved reference at {seg!r}", expr)
        cur = nxt
    return cur


def resolve_expr(expr: str, ctx: BindingContext) -> Any:
    """Resolve ONE expression body (no braces) through its pipe chain."""
    parts = [p.strip() for p in expr.split("|")]
    head = parts[0]

    if head.startswith("secret:"):
        key = head[len("secret:") :].strip()
        if not key:
            raise BindingError("secret reference needs a key", expr)
        if ctx.secret_resolver is None:
            raise BindingError("no secret resolver available", expr)
        value: Any = ctx.secret_resolver(key)
        if value is None:
            raise BindingError(f"secret {key!r} is not set", expr)
    else:
        value = _walk_path(ctx.as_root(), head, expr)

    for raw_pipe in parts[1:]:
        m = _PIPE_RE.match(raw_pipe)
        if not m:
            raise BindingError(f"malformed pipe {raw_pipe!r}", expr)
        name, arg_src = m.group(1), m.group(2) or ""
        fn = PIPES.get(name)
        if fn is None:
            raise BindingError(f"unknown pipe {name!r}", expr)
        try:
            value = fn(value, *_parse_pipe_args(arg_src))
        except BindingError as be:
            raise BindingError(str(be), expr) from be
        except TypeError as exc:
            raise BindingError(f"bad arguments for pipe {name!r}", expr) from exc
    return value


def _stringify(value: Any) -> str:
    """Interpolation rendering. Containers go through json.dumps rather than str() —
    a Python repr's single quotes are not JSON and models reproduce them badly."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def resolve(template: Any, ctx: BindingContext) -> Any:
    """Resolve a value that may contain bindings.

    A non-string passes through. A whole-value ref preserves its source type. Anything
    else interpolates. Dicts and lists resolve recursively so a node's whole `config`
    can be resolved in one call.
    """
    if isinstance(template, dict):
        return {k: resolve(v, ctx) for k, v in template.items()}
    if isinstance(template, list):
        return [resolve(v, ctx) for v in template]
    if not isinstance(template, str):
        return template

    whole = _WHOLE_RE.match(template)
    if whole:
        return resolve_expr(whole.group(1).strip(), ctx)

    def _sub(m: re.Match[str]) -> str:
        return _stringify(resolve_expr(m.group(1).strip(), ctx))

    return _REF_RE.sub(_sub, template)


def refs_in(template: Any) -> list[str]:
    """Every expression body in a value, for dependency analysis. Used by the validator
    and by the mutation cascade — which follows BINDING dependencies, not tree
    descendants."""
    out: list[str] = []
    if isinstance(template, dict):
        for v in template.values():
            out.extend(refs_in(v))
    elif isinstance(template, list):
        for v in template:
            out.extend(refs_in(v))
    elif isinstance(template, str):
        out.extend(m.group(1).strip() for m in _REF_RE.finditer(template))
    return out


def node_deps(template: Any) -> set[str]:
    """The node ids a value depends on. `{{nodes.x.output.y}}` → `{"x"}`."""
    deps: set[str] = set()
    for expr in refs_in(template):
        head = expr.split("|")[0].strip()
        segs = [s for s in head.split(".") if s]
        if len(segs) >= 2 and segs[0] == "nodes":
            deps.add(segs[1])
    return deps
