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
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

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

    `remediation` is the ACTIONABLE half, carried from the raise site because only that site
    knows which failure mode this is. The dispatcher's generic fallback ("…or add a
    `| default(...)` pipe if the value is genuinely optional") is wrong for an unresolved
    path — `_walk_path` raises before any pipe runs — and when the expression already carries
    that pipe it names an act the author has already performed, which is worse than silence.
    Measured: six bundled templates shipped the guarded idiom and the engine answered every
    one of them by asking for the guard they had written.
    """

    def __init__(self, message: str, expr: str = "", remediation: str = "") -> None:
        self.expr = expr
        self.remediation = remediation
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
    #: The previous ITERATION of the loop this node is in, LAYERED across the whole body — see
    #: `RunController._last_output`, which is the one definition of that value for a body node
    #: and for the loop's own `condition` alike.
    last_output: Any = None
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
    if value is None:
        return []
    if not isinstance(value, list):
        raise BindingError("window expects a list")
    try:
        # A bare `| window` means "the default window", which is the user's configured one —
        # the same value `_default_sibling_view` applies. An explicit `| window(N)` is the
        # template overriding it, and stays exactly that.
        n = _synthesis_window() if size is None else int(size)
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


def _pipe_fenced(
    value: Any,
    source: Any = "",
    source_type: Any = "",
    source_id: Any = "",
    transformation_path: Any = "",
) -> str:
    """`fenced` — ANY value, wrapped by `security.fence_untrusted`.

    The shape-agnostic sibling of `fenced_sources`. A template that interpolates stored or
    fetched content into a prompt has exactly one correct way to do it: run it through the
    platform's fence. `fenced_sources` is that way for a RETRIEVED-KNOWLEDGE list
    (`{title, content|summary}`), because it also numbers the set `[1]..[n]` and attaches the
    citation instruction — but a value of any other shape is silently destroyed by it. Measured
    on `knowledge-persist`'s `conflict_candidates` (`[{"item_id", "statement"}]`):
    `fenced_sources` emits `[1]` and NOTHING else, because neither `content` nor `summary` is
    present — so the model loses both the `item_id` it must copy back and the claim it must
    judge. That is why this pipe exists rather than `fenced_sources` growing a second shape:
    numbering a list whose identity is an opaque id is the wrong rendering, not a missing key.

    A non-string value is JSON-dumped first (the same `json` pipe the sanitization set already
    accepts), then fenced whole. JSON escaping is NOT a fence — a dumped `\\n</untrusted_content>`
    arrives at the model as a real close marker, and `<|im_start|>` survives a dump untouched —
    so the dump is the serialization and `fence_untrusted` is the control. Fencing the dump ONCE
    (rather than per item) is deliberate: the fence's contract is a single balanced span, and a
    per-item fence over an opaque shape would have to invent a per-item rendering.

    `source`/`source_type`/`source_id`/`transformation_path` are forwarded as the fence's
    provenance attributes; all four are optional literals, e.g. `| fenced('knowledge')`.
    """
    from personalclaw.security import fence_untrusted

    text = value if isinstance(value, str) else _pipe_json(value)
    return fence_untrusted(
        text,
        source=str(source or ""),
        source_type=str(source_type or ""),
        source_id=str(source_id or ""),
        transformation_path=str(transformation_path or ""),
    )


def _pipe_fenced_sources(value: Any) -> str:
    """`fenced_sources` — retrieved knowledge, fenced and numbered, with a citation instruction.

    Shape-BOUND: it reads `title` + `content`/`summary`. For any other shape reach for `fenced`,
    which fences the value whole — see its docstring for what this pipe does to a list whose
    items carry neither key (it emits the bare numbering and drops the content).

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
    import personalclaw.knowledge.citations as kcit
    from personalclaw.security import fence_untrusted

    items = value if isinstance(value, list) else ([] if value is None else [value])
    if not items:
        # An explicit "nothing was retrieved" rather than an empty fence. A blank fence reads as
        # "the sources were consulted and were silent", which is a different and wrong claim from
        # "there were no sources" — and the second is the one a coverage gap should convey.
        return "No stored knowledge matched. Answer from first principles and say so."

    blocks: list[str] = []
    for index, item in enumerate(items, start=1):
        # Markers OUT of the source body before this turn's `[1]..[n]` goes in. A retrieved
        # item is often itself a synthesis carrying its own `[1]`, and left in place that
        # inherited marker reads as a citation of THIS turn's source 1 — a marker that
        # resolves to the wrong item is worse than no marker, because it looks answerable.
        # Only the bodies are stripped; the numbering is untouched, because it is the contract
        # `citations.register_sources` mirrors.
        if isinstance(item, dict):
            title = str(item.get("title", "") or "").strip()
            raw_body = str(item.get("content") or item.get("summary") or "")
            body = kcit.strip_markers(raw_body).strip()
            head = f"[{index}] {title}" if title else f"[{index}]"
            text = f"{head}\n{body}" if body else head
        else:
            text = f"[{index}] {kcit.strip_markers(str(item))}"
        blocks.append(fence_untrusted(text, source="knowledge"))
    return "\n".join(
        [
            "Numbered sources follow. Cite them as [n] when you use them, and if they do not "
            "answer the question, say so rather than filling the gap.",
            *blocks,
        ]
    )


def _pipe_source_refs(value: Any) -> list[dict[str, Any]]:
    """`source_refs` — the same numbering `fenced_sources` shows the model, as data.

    `fenced_sources` numbers the retrieved set `[1]..[n]` in the prompt, but nothing carried
    that numbering anywhere else, so a template could only satisfy the synthesized-kind
    citation rule by storing the WHOLE retrieved set. That records what was retrieved and can
    never answer "which source supports this sentence". Handing the refs to the persist step
    lets it resolve the model's `[n]` markers against the same list the model was reading.

    Registered through `citations.register_sources` rather than re-enumerated here: two
    enumerations that agree today is exactly how the numbering silently drifts apart later.
    Emitted as plain dicts because a binding value crosses into an action config as JSON.
    """
    import personalclaw.knowledge.citations as kcit

    items = value if isinstance(value, list) else ([] if value is None else [value])
    return [
        {
            "marker": int(ref.marker),
            "item_id": str(ref.item_id),
            "chunk_index": int(ref.chunk_index),
            "excerpt": str(ref.excerpt),
        }
        for ref in kcit.register_sources(items)
    ]


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
    "fenced": _pipe_fenced,
    "fenced_sources": _pipe_fenced_sources,
    "source_refs": _pipe_source_refs,
}

#: Pipes that suppress the default sibling view. `window` and `significant` count: a template
#: that stated its own bound has said what it wants, and silently applying the default on top
#: would make an explicit `window(50)` mean 20.
#:
#: `source_refs` is in for a different reason: it must see the SAME items `fenced_sources`
#: numbered, and `fenced_sources` opts out. If one pipe read the bounded view and the other the
#: full list, `[3]` in the prompt and ref 3 in the persist step would name different items — a
#: citation that resolves to the wrong source, which is the failure this pipe exists to prevent.
_EXPLICIT_VIEW_PIPES = frozenset(
    {"full", "window", "significant", "unseen", "fenced_sources", "source_refs"}
)


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

#: Every root beyond `inputs`/`nodes`, and what it holds. Named in a remediation because a
#: missing ROOT is a CONTEXT error, not a spelling error: the value does not exist where the
#: reference is being read, and no amount of checking the path will surface that.
_ROOT_HOLDS = {
    "brief": "the project Session Brief",
    "item": "the current `foreach` item",
    "iter": "the current iteration index",
    "last": "the previous iteration of the loop this node is in",
    "output": "this node's own output, and only inside `success_when`",
    "previous": "the previous cycle of the enclosing `until_cancelled` body",
    "siblings": "the accumulated outputs of this node's `parallel` siblings",
}


#: The two roots that carry a prior cycle's produced value. Spelled once: `_first_cycle_miss`,
#: `_prior_cycle_field_miss` and the remediation above all key on the same pair, and three
#: literal tuples would drift the day a third prior-cycle root appears.
_PRIOR_CYCLE_ROOTS = ("last", "previous")


def _is_prior_cycle_output_path(head: str) -> bool:
    """Does this head read a field INSIDE a prior cycle's output? Shape only, no context."""
    segs = [s for s in head.split(".") if s]
    return len(segs) >= 3 and segs[0] in _PRIOR_CYCLE_ROOTS and segs[1] == "output"


def reads_prior_cycle_output(expr: str) -> bool:
    """Does this whole expression body (pipes included) read a prior cycle's output field?

    Public because a FAILURE CLASS depends on the answer: `engine_support.resolve_config` files
    a binding failure here as INTERNAL rather than USER. The shape test lives with the rule it
    shares (`_prior_cycle_field_miss`) so the two cannot drift into disagreeing about which
    reads are prior-cycle reads.
    """
    return _is_prior_cycle_output_path((expr or "").split("|")[0].strip())


def _unresolved_remediation(seg: str, *, is_root: bool, head: str = "") -> str:
    """The actionable half of an `unresolved reference` failure.

    Never "add a `| default(...)` pipe" — with ONE exception, carried here rather than left to
    the reader to infer. `_walk_path` raises on the FIRST missing segment, before any pipe runs
    — `validator._validate_output_contract` states the same for a `{{nodes.…}}` read ("a
    `| default(…)` pipe does NOT rescue it") — so in general a default cannot save a missing
    path. It saves a path that RESOLVES TO NULL, which is a different thing, and saying so is
    the difference between a next step and a dead end.

    The exception is a field inside a PRIOR CYCLE's output (`last.output.x`,
    `previous.output.x`), where `_prior_cycle_field_miss` does honour a `default` — because the
    key set of a model-produced output is a runtime fact, not something the author got wrong.
    Reaching this text with such a head therefore means the expression has NO default, and
    "add one" is the correct and only next step. Telling that author to check the spelling
    instead sends them looking for a typo that is not there.

    A missing ROOT and a missing leaf are different fixes, so they get different text: the
    root case means the reference is being read somewhere the value does not exist at all.
    """
    if not is_root and _is_prior_cycle_output_path(head):
        root = head.split(".")[0].strip()
        return (
            f"the previous {'cycle' if root == 'previous' else 'iteration'} produced no "
            f"{seg!r}. A model that ignored its step's declared schema is the usual reason, so "
            f"add a `| default(...)` pipe to say what to use when the field is absent — for a "
            f"prior-cycle field, and only there, a default DOES rescue the missing path."
        )
    if is_root:
        holds = _ROOT_HOLDS.get(seg)
        if holds:
            return (
                f"{seg!r} is not available to this node — it holds {holds}. A "
                "`| default(...)` pipe cannot rescue it: pipes run only after the reference "
                "resolves, and this failed before that."
            )
        return (
            f"there is no {seg!r} root here — check the spelling against `inputs`, `nodes` "
            f"and {', '.join(sorted(_ROOT_HOLDS))}."
        )
    return (
        f"check that the value really carries {seg!r}. A `| default(...)` pipe does not "
        "rescue a missing path, only one that resolves to null."
    )


def _walk_path(root: Any, path: str, expr: str) -> Any:
    """Follow a dotted path. Missing segments raise — see the module docstring on why
    this is not a silent empty string."""
    cur: Any = root
    for index, seg in enumerate([s for s in path.split(".") if s]):
        if isinstance(cur, dict):
            nxt = cur.get(seg, _MISSING)
        elif isinstance(cur, list) and seg.isdigit():
            idx = int(seg)
            nxt = cur[idx] if 0 <= idx < len(cur) else _MISSING
        else:
            raise BindingError(f"cannot read {seg!r} from a {type(cur).__name__}", expr)
        if nxt is _MISSING:
            raise BindingError(
                f"unresolved reference at {seg!r}",
                expr,
                _unresolved_remediation(seg, is_root=index == 0, head=path),
            )
        cur = nxt
    return cur


def resolve_expr(expr: str, ctx: BindingContext) -> Any:
    """Resolve ONE expression body (no braces) through its pipe chain."""
    parts = [p.strip() for p in expr.split("|")]
    head = parts[0]
    pipe_names = {m.group(1) for m in (_PIPE_RE.match(p) for p in parts[1:]) if m}

    # A root the FIRST cycle legitimately lacks short-circuits to None so the expression's
    # `| default(...)` pipe runs. Nothing else can rescue it: `_walk_path` raises on the first
    # missing segment, before any pipe. Distinct from `nodes.typo.output`, which really is an
    # authoring error and must still fail.
    if _first_cycle_miss(head, ctx):
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
        try:
            value = _walk_path(ctx.as_root(), head, expr)
        except BindingError:
            # A FIELD the prior cycle's output does not carry, in an expression that already
            # says what to use instead. See `_prior_cycle_field_miss` for why this cannot
            # swallow `nodes.typo.output`.
            if not _prior_cycle_field_miss(head, ctx, pipe_names):
                raise
            return _run_pipes(None, parts[1:], expr, ctx)

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


def _first_cycle_miss(head: str, ctx: BindingContext) -> bool:
    """Is this a prior-cycle root that the FIRST cycle legitimately does not have yet?

    Two roots carry a prior cycle's value — `previous` (the prior cycle of the enclosing
    `until_cancelled` body) and `last` (the prior iteration of the loop this node is in) — and
    on a first cycle neither exists. Raising there would make every diff-aware template fail
    on its own first pass unless it grew a branch node for the case, and it is the documented
    idiom (`{{last.output.summary | default("(this is the first pass)")}}`) that six bundled
    templates already ship. So the miss resolves to None and the pipe chain runs.

    **Keyed on a POSITIVE in-a-loop signal, never on absence alone**, because
    `absent-is-not-zero`: a rescue that fired wherever the root happened to be missing would
    render "(this is the first pass)" for a `last` read somewhere no iteration exists at all — a
    prompt quietly missing its input while the run reports success, which is the exact failure
    this module's docstring exists to prevent.

    * `previous` — `has_previous` is the context's own per-node declaration.
    * `last` — `iter_index is not None and not has_item` is the positive signal: this node is
      executing inside a loop BODY, so `last` names something real here. `has_item` excludes a
      `foreach`, which rebinds `iter_index` to an ITEM index — item 0 of a fan-out is not
      iteration 0 of a loop, and `last` means nothing there.

    Given that signal, `not has_last` is a MEASUREMENT rather than an unwired seam, which is what
    changed in #3524 and why the rule is no longer pinned to `iter_index == 0`. Both `previous`
    and `last` are now computed for every node the controller dispatches
    (`RunController._last_output` / `_previous_output`), so "the engine did not supply it" and
    "the engine looked and there was nothing to supply" are the same statement: either this is
    the first iteration, or the previous one produced no output at all. Both are honest
    `| default(...)` cases, and before #3524 the second rendered
    `unresolved reference at 'last'` on iterations 2..N of six bundled templates.

    A `last` read OUTSIDE any loop body (`iter_index is None`) and a typo'd `lastt` still raise —
    those are authoring errors, and nothing in the engine will ever supply them.

    This rule is about the ROOT being absent. The prior cycle being PRESENT but not carrying a
    field is a different fact with a different predicate — see `_prior_cycle_field_miss`.
    """
    segs = [s for s in head.split(".") if s]
    if not segs:
        return False
    if segs[0] == "previous":
        return not ctx.has_previous
    if segs[0] == "last":
        return not ctx.has_last and ctx.iter_index is not None and not ctx.has_item
    return False


def _prior_cycle_field_miss(head: str, ctx: BindingContext, pipe_names: set[str]) -> bool:
    """Is this a FIELD the prior cycle's output legitimately does not carry?

    `_first_cycle_miss` rescues an absent prior-cycle ROOT. This rescues a path INTO a
    prior-cycle root that IS present — `{{last.output.summary | default("(first pass)")}}`
    resolving `last`, resolving `last.output`, and finding no `summary` on it. Measured on a
    real loop: a body stage declaring `schema {summary, meaningful_progress, evidence}` whose
    model returned prose instead of that JSON keeps an unstructured `{"result": "<text>"}`
    output, so every `last.output.<field>` read in the NEXT iteration is unresolvable and the
    iteration fails on a binding error naming a field nobody typed wrong. With a small local
    model that is the common case, not an edge case, and the template author had already
    written the fallback for exactly this — a `default` that `_pipe_default`'s own contract
    ("an unresolvable *reference* still raises") could never fire.

    **Why this cannot swallow an authoring error.** All five conditions must hold:

    1. the root is `last` or `previous` — a prior CYCLE, so `nodes.typo.output`,
       `inputs.typo`, `siblings.x.y` and a typo'd `lastt` are all outside this rule and raise
       exactly as before. That distinction is the same one `_first_cycle_miss` draws, and it is
       load-bearing for the same reason: a node id is statically knowable, so a typo in one is
       an authoring error that `validator._validate_binding_targets` already rejects before
       any run, while the key set of a model-produced output is knowable only at runtime;
    2. that root is actually PRESENT (`has_last` / `has_previous`) — the engine really did hand
       over a prior cycle. An unwired seam still raises, which is what keeps this from
       degenerating into the absence-keyed rescue `_first_cycle_miss` refuses;
    3. the second segment is literally `output` and a third segment exists — the miss is
       strictly INSIDE the value the prior cycle produced. `last.typo.summary` raises;
    4. the expression carries a `default(...)` pipe — the author has stated the fallback. With
       no default nobody has said what to use instead, and inventing one is the silent-empty-
       string failure this module exists to prevent, so it still raises. That is why the four
       bundled loop `condition`s reading `{{last.output.<field>}}` carry `| default(false)`
       rather than relying on this;
    5. not a `foreach` (`has_item`) — `last` means nothing over an item index.

    The residue, named rather than implied: a typo in the FIELD name (`last.output.sumary |
    default(…)`) is rescued too, because at this layer it is indistinguishable from a model
    that omitted the key. That is undecidable HERE, and the layer that can decide it is the
    producing step — it knows both the declared schema and what came back.
    """
    segs = [s for s in head.split(".") if s]
    if len(segs) < 3 or segs[1] != "output" or "default" not in pipe_names:
        return False
    if segs[0] == "previous":
        return ctx.has_previous
    if segs[0] == "last":
        return ctx.has_last and not ctx.has_item
    return False


def _flatten_sibling(value: Any) -> Any:
    """Iteration envelopes → items. See `longrun._flatten_outputs` on which carrier keys."""
    from personalclaw.workflows import longrun

    if not isinstance(value, list):
        return value
    return longrun._flatten_outputs(value)


def _synthesis_window() -> int:
    """`knowledge.synthesis_window`, or `longrun.DEFAULT_SYNTHESIS_WINDOW`. Its FIRST reader.

    `longrun`'s own note on that constant said "`KnowledgeConfig.synthesis_window` overrides
    it" — and nothing did. The field round-tripped and sat on the PATCH allowlist while every
    sibling read used the module default, so the one knob for the cost regression its `_meta`
    describes ("a run that gets slower and more expensive until it hits a context limit") could
    not be turned. The reader lives HERE rather than in `longrun` because that module is pure
    over explicit state by contract; this is already the impure boundary that defaults the view.

    Falls back to the module default when config is unreadable: an unbounded sibling view is
    the failure the window exists to prevent, so a bad read must not remove the bound.
    """
    from personalclaw.workflows import longrun

    try:
        from personalclaw.config.loader import AppConfig

        configured = int(getattr(AppConfig.load().knowledge, "synthesis_window", 0) or 0)
    except Exception:
        logger.debug("synthesis window config unreadable — using the default", exc_info=True)
        return longrun.DEFAULT_SYNTHESIS_WINDOW
    return configured if configured > 0 else longrun.DEFAULT_SYNTHESIS_WINDOW


def _default_sibling_view(value: Any) -> Any:
    """The bounded, significance-filtered default for a sibling read.

    Bounded by default because the unbounded failure is invisible: nothing errors, the run
    just costs more every cycle until it hits a context limit hours in. An explicit `| full`
    is a template author saying they accept that.

    The window comes from `knowledge.synthesis_window` (see `_synthesis_window`), so the
    user's configured bound is what a sibling read actually applies.
    """
    from personalclaw.workflows import longrun

    if not isinstance(value, list):
        return value
    return longrun.sibling_view(value, window=_synthesis_window())


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
