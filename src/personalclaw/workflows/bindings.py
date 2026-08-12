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
    #: sibling node id → the outputs it has accumulated across iterations. A LIST, because a
    #: watcher reads a sibling that is still producing: a single "current output" would show
    #: only the newest cycle and the synthesizer would never see a trend
    #: (KNOWLEDGE-SYNTHESIS §4.2).
    sibling_outputs: dict[str, list[Any]] | None = None
    #: The prior successful cycle/run of this template, for diff-aware synthesis. `has_previous`
    #: distinguishes "the first run, legitimately" from "the reference is wrong" — the first is a
    #: `| default(...)` case and the second must raise.
    previous_output: Any = None
    has_previous: bool = False
    #: The engine-maintained seen-set, for the `unseen` pipe. A callable rather than the set
    #: itself so bindings hold no engine state.
    seen_filter: Any = None
    #: The project Session Brief (KNOWLEDGE-SYNTHESIS §5.3), exposed as `{{brief.text}}` and
    #: `{{brief.items}}`. RUN context only — see the controller's note on the chat invariant.
    brief: Any = None
    #: Resolver for `{{secret:KEY}}`. Injected so nothing here reads the credential
    #: store directly — that also keeps secrets out of unit tests by default.
    secret_resolver: Any = None
    #: The node's OWN output, exposed as `output.*` — for `success_when` only, which is
    #: evaluated AFTER the node produced it (LOOPS-EVOLUTION R5f). Deliberately absent
    #: everywhere else: a prompt that could read its own output does not have one yet, and
    #: `has_self_output` keeps "the node produced nothing" distinguishable from "this root
    #: is not available here" instead of resolving to a silent empty string.
    self_output: Any = None
    has_self_output: bool = False

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
        if self.sibling_outputs is not None:
            # RAW here. The default filter/window is applied at RESOLUTION time instead — see
            # `_default_sibling_view`. Filtering here would make `| full` inert: the opt-out
            # would only ever see items the default had already dropped, which is a control
            # that looks present and does nothing.
            root["siblings"] = {
                sid: {"output": list(outs)} for sid, outs in self.sibling_outputs.items()
            }
        if self.has_previous:
            root["previous"] = {"output": self.previous_output}
        if self.has_self_output:
            root["output"] = self.self_output
        if self.brief is not None:
            # `text` is pre-fenced and citation-instructed, so a template writes
            # `{{brief.text}}` and cannot accidentally interpolate raw knowledge into a prompt.
            root["brief"] = {
                "text": self.brief.render() if hasattr(self.brief, "render") else "",
                "items": [i.item_id for i in getattr(self.brief, "items", [])],
                "count": len(getattr(self.brief, "items", [])),
                "dropped": int(getattr(self.brief, "dropped", 0)),
            }
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


# ── long-run pipes (KNOWLEDGE-SYNTHESIS §4.2) ──


def _pipe_window(value: Any, size: Any = None) -> Any:
    """`window(20)` — the most recent N items.

    Distinct from `slice(-20)` because the intent is different and the intent is what a
    reader needs: a window BOUNDS growth, and naming it that way is what makes a template
    review notice its absence on an unbounded sibling read.
    """
    from personalclaw.workflows import longrun

    if value is None:
        return []
    if not isinstance(value, list):
        raise BindingError("window expects a list")
    try:
        n = longrun.DEFAULT_SYNTHESIS_WINDOW if size is None else int(size)
    except (TypeError, ValueError) as exc:
        raise BindingError("window size must be an integer") from exc
    if n <= 0:
        raise BindingError("window size must be positive")
    return value[-n:] if len(value) > n else value


def _pipe_unseen(value: Any, *, _seen: Any = None) -> Any:
    """`unseen` — the engine's persistent seen-set applied.

    Resolution injects the filter; with none available this raises rather than passing
    everything through. A silently inert `unseen` is the whole failure it exists to prevent:
    the watcher keeps working, costs grow every cycle, and nothing indicates why.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise BindingError("unseen expects a list")
    if _seen is None:
        raise BindingError("unseen needs an engine seen-set (only valid inside a loop body)")
    result = _seen(value)
    return result if isinstance(result, list) else []


def _pipe_significant(value: Any, threshold: Any = None) -> Any:
    """`significant(0.7)` — drop items a producer marked unimportant."""
    from personalclaw.workflows import longrun

    if value is None:
        return []
    if not isinstance(value, list):
        raise BindingError("significant expects a list")
    try:
        cut = longrun.DEFAULT_SIGNIFICANCE_THRESHOLD if threshold is None else float(threshold)
    except (TypeError, ValueError) as exc:
        raise BindingError("significance threshold must be a number") from exc
    return [i for i in value if longrun.significance_of(i) >= cut]


def _pipe_full(value: Any) -> Any:
    """`full` — the explicit opt-out from the default sibling view.

    A no-op ON the value: what it really does is suppress the default filter/window, which
    resolution detects by seeing this pipe in the chain. It exists as a named pipe because
    "I know this is unbounded and I want it" should be visible in the template, not implied
    by the absence of something.
    """
    return value


def _pipe_hygiene(value: Any) -> Any:
    """`hygiene` — the web-item junk filter, so monitoring templates don't each write one."""
    from personalclaw.workflows import longrun

    if value is None:
        return []
    if not isinstance(value, list):
        raise BindingError("hygiene expects a list")
    return longrun.web_hygiene(value)


def _pipe_fenced_sources(value: Any) -> str:
    """`fenced_sources` — retrieved knowledge, fenced and numbered, with a citation instruction.

    Knowledge items partly derive from web and inbox content, so interpolating them raw into a
    stage prompt bypasses the platform's fencing doctrine: an ingested page that says "ignore
    previous instructions" becomes an instruction the moment a template writes
    `{{nodes.known.output.items}}` into a prompt. Knowledge already fences at INGEST
    (`knowledge/insights.py`) and redacts on the way out of `search-for-context`; this extends
    the same doctrine to workflow interpolation.

    Numbered, and with the "say so if the sources do not answer" instruction attached, because a
    fence alone tells the model the span is data but not what to do with it — and a model handed
    unattributed context answers from memory when the context comes up short.
    """
    from personalclaw.security import fence_untrusted

    items = value if isinstance(value, list) else ([] if value is None else [value])
    if not items:
        # An explicit "nothing was retrieved" rather than an empty fence. A blank fence reads as
        # "the sources were consulted and were silent", which is a different and wrong claim from
        # "there were no sources" — and the second is the one a coverage gap should convey.
        return "No stored knowledge matched. Answer from first principles and say so."

    blocks: list[str] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            title = str(item.get("title", "") or "").strip()
            body = str(item.get("content") or item.get("summary") or "").strip()
            head = f"[{index}] {title}" if title else f"[{index}]"
            text = f"{head}\n{body}" if body else head
        else:
            text = f"[{index}] {item}"
        blocks.append(fence_untrusted(text, source="knowledge"))
    return "\n".join(
        [
            "Numbered sources follow. Cite them as [n] when you use them, and if they do not "
            "answer the question, say so rather than filling the gap.",
            *blocks,
        ]
    )


def _pipe_clamp(value: Any, low: Any = 0, high: Any = 1) -> Any:
    """`clamp(30, 86400)` — bound a number a model proposed."""
    try:
        lo, hi = float(low), float(high)
    except (TypeError, ValueError) as exc:
        raise BindingError("clamp bounds must be numbers") from exc
    if lo > hi:
        raise BindingError("clamp lower bound exceeds upper bound")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise BindingError("clamp expects a number") from exc
    else:
        number = float(value)
    bounded = max(lo, min(hi, number))
    return int(bounded) if float(bounded).is_integer() else bounded


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
    "window": _pipe_window,
    "unseen": _pipe_unseen,
    "significant": _pipe_significant,
    "full": _pipe_full,
    "hygiene": _pipe_hygiene,
    "clamp": _pipe_clamp,
    "fenced_sources": _pipe_fenced_sources,
}

#: Pipes that suppress the default sibling view. `window` and `significant` count: a template
#: that stated its own bound has said what it wants, and silently applying the default on top
#: would make an explicit `window(50)` mean 20.
_EXPLICIT_VIEW_PIPES = frozenset({"full", "window", "significant", "unseen", "fenced_sources"})


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
    pipe_names = {m.group(1) for m in (_PIPE_RE.match(p) for p in parts[1:]) if m}

    # `previous` absent is the FIRST cycle/run, which is normal — not an unresolvable
    # reference. Every diff-aware template in the plan is written as
    # `{{previous.output.summary | default('None yet')}}`, and raising here would make each one
    # fail on its own first cycle unless it grew a branch node for the case. Distinct from
    # `nodes.typo.output`, which really is an authoring error.
    if _is_previous_ref(head) and not ctx.has_previous:
        return _run_pipes(None, parts[1:], expr, ctx)

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

    # `siblings.<id>.output` always FLATTENS iteration envelopes to items — that is what the
    # reference means, and without it `| full` / `| window(N)` / `| unseen` each operated on a
    # list of N envelopes: measured, `| full` returned 1 item out of 60 and `| unseen` returned
    # nothing, because an envelope carries no item identity.
    #
    # The bounded VIEW is separate, and defaulted here rather than in `as_root` so `| full` can
    # genuinely opt out instead of filtering an already-filtered list (§4.2).
    if _is_sibling_ref(head):
        value = _flatten_sibling(value)
        if not (pipe_names & _EXPLICIT_VIEW_PIPES):
            value = _default_sibling_view(value)

    return _run_pipes(value, parts[1:], expr, ctx)


def _run_pipes(value: Any, raw_pipes: list[str], expr: str, ctx: BindingContext) -> Any:
    for raw_pipe in raw_pipes:
        m = _PIPE_RE.match(raw_pipe)
        if not m:
            raise BindingError(f"malformed pipe {raw_pipe!r}", expr)
        name, arg_src = m.group(1), m.group(2) or ""
        fn = PIPES.get(name)
        if fn is None:
            raise BindingError(f"unknown pipe {name!r}", expr)
        try:
            if name == "unseen":
                value = _pipe_unseen(value, _seen=ctx.seen_filter)
            else:
                value = fn(value, *_parse_pipe_args(arg_src))
        except BindingError as be:
            raise BindingError(str(be), expr) from be
        except TypeError as exc:
            raise BindingError(f"bad arguments for pipe {name!r}", expr) from exc
    return value


def _is_sibling_ref(head: str) -> bool:
    segs = [s for s in head.split(".") if s]
    return len(segs) >= 1 and segs[0] == "siblings"


def _is_previous_ref(head: str) -> bool:
    segs = [s for s in head.split(".") if s]
    return len(segs) >= 1 and segs[0] == "previous"


def _flatten_sibling(value: Any) -> Any:
    """Iteration envelopes → items. See `longrun._flatten_outputs` on which carrier keys."""
    from personalclaw.workflows import longrun

    if not isinstance(value, list):
        return value
    return longrun._flatten_outputs(value)


def _default_sibling_view(value: Any) -> Any:
    """The bounded, significance-filtered default for a sibling read.

    Bounded by default because the unbounded failure is invisible: nothing errors, the run
    just costs more every cycle until it hits a context limit hours in. An explicit `| full`
    is a template author saying they accept that.
    """
    from personalclaw.workflows import longrun

    if not isinstance(value, list):
        return value
    return longrun.sibling_view(value)


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
