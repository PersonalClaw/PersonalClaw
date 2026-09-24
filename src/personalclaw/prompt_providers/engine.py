"""Template rendering engine — a small, self-contained mini-language.

One render path serves BOTH system-prompt injection and user-prompt fill-in, so a
preview can never drift from runtime. No Jinja2 dependency.

Grammar (ordered passes over the text):

  1. includes      {{> snippet-name}}                     inlined (recursive, depth<=5,
                                                           cycle-detected)
  2. whitespace    {{- … -}} / {%- … -%} / {#- … -#}      trim adjacent whitespace
  3. comments      {# ... #}                              stripped
  4. loops         {% for x in list %} ... {% endfor %}   loop.index/index1/first/last/length;
                                                           iterates list/tuple/dict-values/string
  5. conditionals  {% if A %}…{% elif B %}…{% else %}…{% endif %}
                     A/B: comparisons (== != > < >= <=), membership (x in y / y contains x),
                     boolean combinators (and / or / not) with ( ) grouping, or bare truthiness
  6. functions     {{ upper(x) }} / {{ upper(trim(x)) }}  built-in registry (call-style, nestable)
  7. variables     {{ name }} / {{ a.b.c }} / {{ a.0 }}   dot-path + numeric list index
                   {{ name::type }} / {{ name::select::[a,b] }}  inline typed-variable decl
                                                           (type used at render; for the UI)

Built-in functions (call-style only, no pipes): string — upper lower capitalize
title trim replace length truncate split substring; array — join first last count
sort slice push filter map contains min max; object — keys values entries get;
util — json parse default uuid date timestamp; math — add subtract multiply
divide round abs; ternary — if(cond,a,b) unless(cond,a,b); type — isString
isNumber isBoolean isArray isObject isEmpty.

Missing required variable / unknown function / depth or iteration overflow /
malformed block → ``PromptRenderError``. An undeclared bare ``{{x}}`` with no
matching variable passes through untouched so literal braces survive.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, cast

from personalclaw.prompt_providers.base import (
    PromptRenderError,
    PromptSnippet,
    PromptTemplate,
    PromptVariable,
    VariableType,
)

MAX_INCLUDE_DEPTH = 5
MAX_LOOP_ITERATIONS = 1000

# A resolver maps a snippet name → its PromptSnippet (or None when unknown).
SnippetResolver = Callable[[str], "PromptSnippet | None"]

_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
_INCLUDE_RE = re.compile(r"\{\{>\s*([a-zA-Z0-9_-]+)\s*\}\}")
# {% ... %} block tags and {{ ... }} expressions. We scan with a tokenizer rather
# than a single regex so nested if/for blocks pair correctly.
_TAG_RE = re.compile(r"\{%\s*(.*?)\s*%\}", re.DOTALL)
_EXPR_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}", re.DOTALL)
_FOR_RE = re.compile(r"^for\s+([a-zA-Z_]\w*)\s+in\s+(.+)$")
_IF_RE = re.compile(r"^if\s+(.+)$")
_COND_RE = re.compile(r"^(.*?)\s*(==|!=|>=|<=|>|<)\s*(.*)$")
_MEMBERSHIP_RE = re.compile(r"^(.+?)\s+(in|contains)\s+(.+)$", re.IGNORECASE)
_FUNC_RE = re.compile(r"^([a-zA-Z_]\w*)\s*\((.*)\)$", re.DOTALL)
# Inline typed-variable declaration inside {{ }}: a dotted/underscored name
# followed by a `::` type suffix. Group 1 = the bare name (what resolves at
# render); group 2 = the raw suffix (parsed for the UI variable type).
_TYPE_DECL_RE = re.compile(r"^([a-zA-Z_][\w.]*)\s*::\s*(.+)$")
# Canonical UI type names + the legacy aliases OpenForge/PromptForge accept.
_TYPE_ALIASES = {
    "string": "text",
    "str": "text",
    "text": "text",
    "longtext": "textarea",
    "long_text": "textarea",
    "multiline": "textarea",
    "textarea": "textarea",
    "numeric": "number",
    "integer": "number",
    "int": "number",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "select": "select",
    "multiselect": "select",
    "enum": "select",
}


def _strip_type_suffix(expr: str) -> str:
    """Return the bare variable name when *expr* is a `name::type` declaration,
    else *expr* unchanged. Never touches function calls or comparisons (those
    don't match the bare-name pattern)."""
    m = _TYPE_DECL_RE.match(expr)
    return m.group(1) if m else expr


def parse_type_decl(suffix: str) -> tuple[VariableType, list[str]]:
    """Parse a `::` suffix into ``(canonical_type, options)``.

    Forms: ``text``, ``number``, ``select::[a, b]``, or a bare ``[a, b]`` (→ select).
    Unknown type names fall back to ``text``; an option list implies ``select``."""
    s = suffix.strip()
    options: list[str] = []
    type_part = s
    # An option list `[a, b, c]` may appear alone or after `type::`.
    bracket = re.search(r"\[(.*)\]", s)
    if bracket:
        options = [o.strip() for o in bracket.group(1).split(",") if o.strip()]
        type_part = s[: bracket.start()].rstrip(": ").strip()
    canonical = _TYPE_ALIASES.get(type_part.lower(), "") if type_part else ""
    if options and not canonical:
        canonical = "select"
    return (cast(VariableType, canonical or "text"), options)


# ── value coercion (typed variables) ────────────────────────────────────────


def _coerce(var: PromptVariable, raw: Any) -> Any:
    if raw is None:
        return None
    t = var.type
    try:
        if t == "number":
            if isinstance(raw, bool):  # bool is subclass of int — reject explicitly
                raise PromptRenderError(f"variable {var.name!r} expects number, got bool")
            if isinstance(raw, str):
                return float(raw) if "." in raw else int(raw)
            return raw
        if t == "boolean":
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, str):
                low = raw.strip().lower()
                if low in ("true", "yes", "1"):
                    return True
                if low in ("false", "no", "0"):
                    return False
            raise PromptRenderError(f"variable {var.name!r} expects boolean, got {raw!r}")
        if t == "select":
            s = str(raw)
            if var.options and s not in var.options:
                raise PromptRenderError(
                    f"variable {var.name!r} value {s!r} not in options {var.options}"
                )
            return s
        return str(raw)  # text | textarea → string
    except PromptRenderError:
        raise
    except Exception as exc:
        raise PromptRenderError(f"failed to coerce {var.name!r}: {exc}") from exc


# ── built-in functions ───────────────────────────────────────────────────────


def _fn_default(value: Any, fallback: Any = "") -> Any:
    return value if value not in (None, "") else fallback


BUILT_IN_FUNCTIONS: dict[str, Callable[..., Any]] = {
    # string
    "upper": lambda s: str(s).upper(),
    "lower": lambda s: str(s).lower(),
    "capitalize": lambda s: str(s).capitalize(),
    "title": lambda s: str(s).title(),
    "trim": lambda s: str(s).strip(),
    "replace": lambda s, a, b: str(s).replace(str(a), str(b)),
    "length": lambda s: len(s) if hasattr(s, "__len__") else 0,
    "truncate": lambda s, n=80: str(s) if len(str(s)) <= int(n) else str(s)[: int(n)] + "…",
    "split": lambda s, sep=",": str(s).split(str(sep)),
    "substring": lambda s, start, end=None: (
        str(s)[int(start) :] if end is None else str(s)[int(start) : int(end)]
    ),
    # array
    "join": lambda xs, sep=", ": str(sep).join(str(x) for x in (xs or [])),
    "first": lambda xs: (xs[0] if xs else ""),
    "last": lambda xs: (xs[-1] if xs else ""),
    "count": lambda xs: len(xs) if hasattr(xs, "__len__") else 0,
    "sort": lambda xs: sorted(xs or [], key=str),
    "slice": lambda xs, start, end=None: (
        (list(xs)[int(start) :] if end is None else list(xs)[int(start) : int(end)]) if xs else []
    ),
    "push": lambda xs, item: [*(xs or []), item],
    "filter": lambda xs: [x for x in (xs or []) if x],
    "map": lambda xs, prop: [(x.get(prop) if isinstance(x, dict) else None) for x in (xs or [])],
    "contains": lambda coll, item: _fn_contains(coll, item),
    "min": lambda xs: (min(xs) if xs else ""),
    "max": lambda xs: (max(xs) if xs else ""),
    # object
    "keys": lambda o: list(o.keys()) if isinstance(o, dict) else [],
    "values": lambda o: list(o.values()) if isinstance(o, dict) else [],
    "entries": lambda o: [[k, v] for k, v in o.items()] if isinstance(o, dict) else [],
    "get": lambda o, path, fallback=None: _fn_get(o, path, fallback),
    # object / util
    "json": lambda v: json.dumps(v, ensure_ascii=False),
    "parse": lambda s: json.loads(s) if isinstance(s, str) else s,
    "default": _fn_default,
    "uuid": lambda: __import__("uuid").uuid4().hex,
    "date": lambda: __import__("datetime")
    .datetime.now(__import__("datetime").timezone.utc)
    .isoformat(),
    "timestamp": lambda: int(
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc).timestamp()
    ),
    # math
    "add": lambda a, b: _num(a) + _num(b),
    "subtract": lambda a, b: _num(a) - _num(b),
    "multiply": lambda a, b: _num(a) * _num(b),
    "divide": lambda a, b: (_num(a) / _num(b)) if _num(b) != 0 else 0,
    "round": lambda a, n=0: round(_num(a), int(n)),
    "abs": lambda a: abs(_num(a)),
    # conditional (expression-level ternaries — fill the elif/boolean gap inline)
    "if": lambda c, t, f="": (t if _truthy(c) else f),
    "unless": lambda c, t, f="": (f if _truthy(c) else t),
    # type checks
    "isString": lambda v: isinstance(v, str),
    "isNumber": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "isBoolean": lambda v: isinstance(v, bool),
    "isArray": lambda v: isinstance(v, (list, tuple)),
    "isObject": lambda v: isinstance(v, dict),
    "isEmpty": lambda v: v is None or v == "" or v == [] or v == {},
}


def _fn_contains(coll: Any, item: Any) -> bool:
    if coll is None:
        return False
    if isinstance(coll, str):
        return str(item) in coll
    if isinstance(coll, dict):
        return item in coll
    if isinstance(coll, (list, tuple)):
        for el in coll:
            if el == item:
                return True
            if isinstance(el, dict) and (el.get("id") == item or el.get("name") == item):
                return True
        return False
    try:
        return item in coll
    except TypeError:
        return False


def _fn_get(obj: Any, path: Any, fallback: Any = None) -> Any:
    """Nested dot-path access with a fallback (OpenForge's get())."""
    cur = obj
    for seg in str(path).split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        elif (
            isinstance(cur, (list, tuple))
            and seg.lstrip("-").isdigit()
            and -len(cur) <= int(seg) < len(cur)
        ):
            cur = cur[int(seg)]
        else:
            return fallback
    return cur


def _num(v: Any) -> float | int:
    if isinstance(v, bool):
        raise PromptRenderError("expected a number, got bool")
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    try:
        return float(s) if "." in s else int(s)
    except ValueError as exc:
        raise PromptRenderError(f"expected a number, got {v!r}") from exc


# ── expression evaluation ────────────────────────────────────────────────────


def _lookup(path: str, ctx: dict[str, Any]) -> Any:
    """Dot-path lookup into the context. Returns None when any segment is missing."""
    cur: Any = ctx
    for seg in path.split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            return None
    return cur


def _parse_literal(token: str) -> tuple[bool, Any]:
    """Try to read a literal (quoted string / number / bool). (is_literal, value)."""
    t = token.strip()
    if len(t) >= 2 and t[0] in "\"'" and t[-1] == t[0]:
        return True, t[1:-1]
    if t in ("true", "false"):
        return True, t == "true"
    if t == "null" or t == "none":
        return True, None
    if re.fullmatch(r"-?\d+", t):
        return True, int(t)
    if re.fullmatch(r"-?\d+\.\d+", t):
        return True, float(t)
    return False, None


def _eval_expr(expr: str, ctx: dict[str, Any]) -> Any:
    """Evaluate a value expression: a literal, a function call, or a dot-path."""
    expr = expr.strip()
    is_lit, val = _parse_literal(expr)
    if is_lit:
        return val
    m = _FUNC_RE.match(expr)
    if m:
        fname, argstr = m.group(1), m.group(2)
        fn = BUILT_IN_FUNCTIONS.get(fname)
        if fn is None:
            raise PromptRenderError(f"unknown function: {fname}")
        args = [_eval_expr(a, ctx) for a in _split_args(argstr)] if argstr.strip() else []
        try:
            return fn(*args)
        except PromptRenderError:
            raise
        except Exception as exc:
            raise PromptRenderError(f"function {fname}() failed: {exc}") from exc
    return _lookup(expr, ctx)


def _split_args(argstr: str) -> list[str]:
    """Split a function arg list on top-level commas (respecting quotes + parens)."""
    args: list[str] = []
    depth = 0
    quote = ""
    cur = ""
    for ch in argstr:
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch in "([":
            depth += 1
            cur += ch
        elif ch in ")]":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            args.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur)
    return args


def _truthy(v: Any) -> bool:
    return bool(v) and v not in ("false", "0", 0)


# Boolean-combinator tokenizer for conditions. We split on word-boundary
# ``and``/``or``/``not`` and on parentheses, but ONLY at the top level (quotes +
# nested parens are respected by _split_bool). A bare comparison or truthiness
# check still parses exactly as before — the combinators are strictly additive.
_BOOL_WORD_RE = re.compile(r"^(and|or|not)\b", re.IGNORECASE)


def _eval_condition(cond: str, ctx: dict[str, Any]) -> bool:
    """Evaluate a condition supporting and/or/not + grouping, on top of the
    base comparison / membership / bare-truthiness leaf."""
    return _eval_or(cond.strip(), ctx)


def _eval_or(expr: str, ctx: dict[str, Any]) -> bool:
    parts = _split_bool(expr, "or")
    if len(parts) > 1:
        return any(_eval_and(p, ctx) for p in parts)
    return _eval_and(expr, ctx)


def _eval_and(expr: str, ctx: dict[str, Any]) -> bool:
    parts = _split_bool(expr, "and")
    if len(parts) > 1:
        return all(_eval_not(p, ctx) for p in parts)
    return _eval_not(expr, ctx)


def _eval_not(expr: str, ctx: dict[str, Any]) -> bool:
    e = expr.strip()
    m = _BOOL_WORD_RE.match(e)
    if m and m.group(1).lower() == "not":
        return not _eval_not(e[m.end() :], ctx)
    return _eval_comparison(e, ctx)


def _split_bool(expr: str, word: str) -> list[str]:
    """Split *expr* on the top-level boolean keyword *word* (case-insensitive),
    respecting quotes and nested parentheses. Returns [expr] when absent."""
    parts: list[str] = []
    depth = 0
    quote = ""
    cur = ""
    i = 0
    n = len(expr)
    wl = len(word)
    while i < n:
        ch = expr[i]
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            cur += ch
            i += 1
            continue
        if ch == "(":
            depth += 1
            cur += ch
            i += 1
            continue
        if ch == ")":
            depth -= 1
            cur += ch
            i += 1
            continue
        # Top-level keyword match on word boundaries.
        if (
            depth == 0
            and expr[i : i + wl].lower() == word
            and (i == 0 or not (expr[i - 1].isalnum() or expr[i - 1] == "_"))
            and (i + wl >= n or not (expr[i + wl].isalnum() or expr[i + wl] == "_"))
        ):
            parts.append(cur)
            cur = ""
            i += wl
            continue
        cur += ch
        i += 1
    parts.append(cur)
    return parts if len(parts) > 1 else [expr]


def _eval_comparison(cond: str, ctx: dict[str, Any]) -> bool:
    """The leaf condition: parenthesized group, comparison, membership, or bare
    truthiness — the original single-comparison semantics, extended with
    ``in`` / ``contains``."""
    s = cond.strip()
    # Unwrap a fully-parenthesized group: (….)
    if s.startswith("(") and _matching_paren(s) == len(s) - 1:
        return _eval_or(s[1:-1], ctx)

    # Membership: "x in y" / "y contains x" (checked before the comparison regex
    # so an `in`/`contains` operand that itself contains '==' is handled right).
    mem = _MEMBERSHIP_RE.match(s)
    if mem:
        a, kw, b = (
            _eval_expr(mem.group(1), ctx),
            mem.group(2).lower(),
            _eval_expr(mem.group(3), ctx),
        )
        needle, haystack = (a, b) if kw == "in" else (b, a)
        return _contains(haystack, needle)

    m = _COND_RE.match(s)
    if not m:
        return _truthy(_eval_expr(s, ctx))
    left, op, right = _eval_expr(m.group(1), ctx), m.group(2), _eval_expr(m.group(3), ctx)
    try:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        # numeric comparisons coerce; fall back to string compare on failure
        ln: Any
        rn: Any
        try:
            ln, rn = _num(left), _num(right)
        except PromptRenderError:
            ln, rn = str(left), str(right)
        if op == ">":
            return ln > rn
        if op == "<":
            return ln < rn
        if op == ">=":
            return ln >= rn
        if op == "<=":
            return ln <= rn
    except TypeError:
        return False
    return False


def _matching_paren(s: str) -> int:
    """Index of the ')' matching the '(' at s[0], respecting quotes. -1 if none."""
    depth = 0
    quote = ""
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _contains(haystack: Any, needle: Any) -> bool:
    if haystack is None:
        return False
    if isinstance(haystack, str):
        return str(needle) in haystack
    if isinstance(haystack, dict):
        return needle in haystack
    try:
        return needle in haystack
    except TypeError:
        return False


# ── block parsing (if / for) — nesting-aware ────────────────────────────────


def _render_blocks(text: str, ctx: dict[str, Any], depth: int = 0) -> str:
    """Resolve {% if %}/{% for %} blocks then {{ expr }} expressions in `text`."""
    if depth > 50:
        raise PromptRenderError("template nesting too deep")
    out = []
    i = 0
    for m in _TAG_RE.finditer(text):
        if m.start() < i:
            continue
        out.append(_render_exprs(text[i : m.start()], ctx))
        tag = m.group(1).strip()
        if tag.startswith("for "):
            body, end = _extract_block(text, m.start(), "for", "endfor")
            out.append(_render_for(tag, body, ctx, depth))
            i = end
        elif tag.startswith("if "):
            body, end = _extract_block(text, m.start(), "if", "endif")
            out.append(_render_if(tag, body, ctx, depth))
            i = end
        elif tag in ("endif", "endfor", "else") or tag.startswith("elif"):
            # A stray closing/else tag with no opener — emit nothing (malformed but
            # non-fatal), advance past it.
            out.append("")
            i = m.end()
        else:
            raise PromptRenderError(f"unknown block tag: {{% {tag} %}}")
    out.append(_render_exprs(text[i:], ctx))
    return "".join(out)


def _extract_block(text: str, start: int, open_kw: str, close_kw: str) -> tuple[str, int]:
    """Return (inner_body, index_after_close) for a balanced open/close block whose
    opening tag begins at `start`. Nesting-aware."""
    open_tag = _TAG_RE.match(text, start)
    assert open_tag is not None
    body_start = open_tag.end()
    level = 1
    for m in _TAG_RE.finditer(text, body_start):
        kw = m.group(1).strip().split()[0] if m.group(1).strip() else ""
        if kw == open_kw:
            level += 1
        elif kw == close_kw:
            level -= 1
            if level == 0:
                return text[body_start : m.start()], m.end()
    raise PromptRenderError(f"unterminated {{% {open_kw} %}} block")


def _render_if(tag: str, body: str, ctx: dict[str, Any], depth: int) -> str:
    m = _IF_RE.match(tag)
    if not m:
        raise PromptRenderError(f"malformed if: {tag!r}")
    # Split the body into an if/elif*/else chain at the TOP level (nested if/for
    # ignored), then render the first branch whose condition is true (else, if
    # present, is the fallthrough).
    branches = _split_if_chain(m.group(1), body)
    for cond, branch_body in branches:
        if cond is None or _eval_condition(cond, ctx):
            return _render_blocks(branch_body, ctx, depth + 1)
    return ""


def _split_if_chain(if_cond: str, body: str) -> list[tuple[str | None, str]]:
    """Split an if-block body into ordered branches.

    Returns ``[(if_cond, body0), (elif_cond, body1), …, (None, else_body)]``.
    ``elif``/``else`` are recognised only at nesting level 0, so inner if/for
    blocks are left intact for the recursive render of the chosen branch."""
    branches: list[tuple[str | None, str]] = []
    cur_cond: str | None = if_cond
    seg_start = 0
    level = 0
    for m in _TAG_RE.finditer(body):
        tag = m.group(1).strip()
        kw = tag.split()[0] if tag else ""
        if kw in ("if", "for"):
            level += 1
        elif kw in ("endif", "endfor"):
            level -= 1
        elif level == 0 and kw == "elif":
            branches.append((cur_cond, body[seg_start : m.start()]))
            cur_cond = tag[len("elif") :].strip()
            seg_start = m.end()
        elif level == 0 and kw == "else":
            branches.append((cur_cond, body[seg_start : m.start()]))
            cur_cond = None
            seg_start = m.end()
    branches.append((cur_cond, body[seg_start:]))
    return branches


def _render_for(tag: str, body: str, ctx: dict[str, Any], depth: int) -> str:
    m = _FOR_RE.match(tag)
    if not m:
        raise PromptRenderError(f"malformed for: {tag!r}")
    var_name, coll_expr = m.group(1), m.group(2)
    collection = _eval_expr(coll_expr, ctx)
    if collection is None:
        return ""
    if isinstance(collection, dict):
        items = list(collection.values())
    elif isinstance(collection, (list, tuple)):
        items = list(collection)
    elif isinstance(collection, str):
        items = list(collection)
    else:
        raise PromptRenderError(f"cannot iterate over {coll_expr!r}")
    if len(items) > MAX_LOOP_ITERATIONS:
        raise PromptRenderError(f"loop exceeds {MAX_LOOP_ITERATIONS} iterations")
    out = []
    n = len(items)
    for idx, item in enumerate(items):
        loop_ctx = dict(ctx)
        loop_ctx[var_name] = item
        loop_ctx["loop"] = {
            "index": idx,
            "index1": idx + 1,
            "first": idx == 0,
            "last": idx == n - 1,
            "length": n,
        }
        out.append(_render_blocks(body, loop_ctx, depth + 1))
    return "".join(out)


def _render_exprs(text: str, ctx: dict[str, Any]) -> str:
    """Replace {{ expr }} expressions. Undeclared bare names pass through."""

    def repl(m: re.Match[str]) -> str:
        expr = m.group(1).strip()
        if expr.startswith(">"):
            return m.group(0)  # an include slipped through — leave for the include pass
        # Inline typed-variable declaration: {{ name::type }} / {{ name::[a,b] }} /
        # {{ name::select::[a,b] }}. The ::suffix declares the variable's UI type for
        # extraction; at render it's consumed — only the name resolves. (Stripped only
        # for a bare name+suffix, never inside a function call or comparison.)
        expr = _strip_type_suffix(expr)
        # A bare identifier/dot-path that isn't in context → leave the literal
        # braces (authors may write {{x}} as literal text). A function call or a
        # comparison/known-var always resolves.
        is_func = bool(_FUNC_RE.match(expr))
        val = _eval_expr(expr, ctx)
        if val is None and not is_func and _lookup(expr, ctx) is None and not _is_known(expr, ctx):
            return m.group(0)
        return "" if val is None else _stringify(val)

    return _EXPR_RE.sub(repl, text)


def _is_known(expr: str, ctx: dict[str, Any]) -> bool:
    root = expr.split(".")[0].strip()
    return root in ctx


def _stringify(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


# ── includes (snippet transclusion) ─────────────────────────────────────────


def _resolve_includes(
    text: str, resolver: SnippetResolver | None, depth: int, seen: tuple[str, ...]
) -> str:
    if "{{>" not in text:
        return text
    if depth > MAX_INCLUDE_DEPTH:
        raise PromptRenderError(f"snippet include depth exceeds {MAX_INCLUDE_DEPTH}")

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in seen:
            raise PromptRenderError(f"circular snippet include: {name}")
        snippet = resolver(name) if resolver else None
        if snippet is None:
            return f"[missing snippet: {name}]"
        return _resolve_includes(snippet.content, resolver, depth + 1, seen + (name,))

    return _INCLUDE_RE.sub(repl, text)


# ── whitespace control ───────────────────────────────────────────────────────
# Jinja-style trim markers: a leading '-' (just inside the opener) eats the
# whitespace immediately BEFORE the tag; a trailing '-' (just inside the closer)
# eats the whitespace immediately AFTER it. Applies to {{- -}}, {%- -%}, {#- -#}.
# We strip the markers and the adjacent run of whitespace in one pre-pass, then
# hand clean {{ }}/{% %}/{# #} delimiters to the normal passes. Templates that
# use no markers are returned untouched (the regex simply doesn't match).
_WS_OPEN_RE = re.compile(r"[ \t\r\n]+(\{[{%#])-")  # whitespace before an opener whose marker is '-'
_WS_CLOSE_RE = re.compile(r"-([}%#]\})[ \t\r\n]+")  # whitespace after a closer whose marker is '-'
_WS_OPEN_BARE_RE = re.compile(
    r"(\{[{%#])-"
)  # leftover opener marker at string start (no preceding ws)
_WS_CLOSE_BARE_RE = re.compile(
    r"-([}%#]\})"
)  # leftover closer marker at string end (no following ws)


def _apply_whitespace_control(text: str) -> str:
    if "-" not in text:
        return text
    # Order matters: trim adjacent whitespace first (markers still present as the
    # anchor), then drop any markers that had no adjacent whitespace to trim.
    text = _WS_OPEN_RE.sub(r"\1", text)
    text = _WS_CLOSE_RE.sub(r"\1", text)
    text = _WS_OPEN_BARE_RE.sub(r"\1", text)
    text = _WS_CLOSE_BARE_RE.sub(r"\1", text)
    return text


# ── variable value map (declared variables → coerced values) ────────────────


def _build_value_ctx(variables: list[PromptVariable], values: dict[str, Any]) -> dict[str, Any]:
    ctx: dict[str, Any] = dict(values)  # ambient values (e.g. {{bot_name}}) pass through
    for var in variables:
        if var.name in values and values[var.name] is not None:
            ctx[var.name] = _coerce(var, values[var.name])
        elif var.default is not None:
            ctx[var.name] = _coerce(var, var.default)
        elif var.required:
            raise PromptRenderError(f"missing required variable: {var.name}")
        else:
            ctx.setdefault(var.name, "")
    return ctx


# ── public API ───────────────────────────────────────────────────────────────


def render(
    content: str,
    variables: list[PromptVariable],
    values: dict[str, Any] | None = None,
    *,
    resolver: SnippetResolver | None = None,
) -> str:
    """Render raw ``content`` through the full pipeline.

    1. resolve {{> snippet}} includes (via ``resolver``), 2. strip comments,
    3. evaluate if/for blocks + {{ expr }} expressions against the typed variable
    values + ambient ``values``.
    """
    ctx = _build_value_ctx(variables, dict(values or {}))
    text = _resolve_includes(content, resolver, 0, ())
    text = _apply_whitespace_control(text)
    text = _COMMENT_RE.sub("", text)
    return _render_blocks(text, ctx)


def render_template(
    template: PromptTemplate,
    values: dict[str, Any] | None = None,
    *,
    resolver: SnippetResolver | None = None,
) -> str:
    """Render a ``PromptTemplate`` with the supplied variable values."""
    return render(template.content, template.variables, values, resolver=resolver)


def render_snippet(
    snippet: PromptSnippet,
    values: dict[str, Any] | None = None,
    *,
    resolver: SnippetResolver | None = None,
) -> str:
    """Render a ``PromptSnippet`` standalone (for preview)."""
    return render(snippet.content, snippet.variables, values, resolver=resolver)


def included_snippet_names(content: str) -> list[str]:
    """The snippet names directly included by ``content`` (one level, in order)."""
    seen: list[str] = []
    for m in _INCLUDE_RE.finditer(content):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def extract_inline_variables(content: str) -> list[PromptVariable]:
    """Auto-detect inline typed-variable declarations — ``{{ name::type }}`` /
    ``{{ name::select::[a, b] }}`` / ``{{ name::[a, b] }}`` — from raw content,
    in first-appearance order, deduped by name (first declaration wins). Lets the
    authoring UI surface a variable + its type straight from the template text,
    OpenForge/PromptForge-style. Bare ``{{ name }}`` with no ``::`` is NOT returned
    (those are declared explicitly via the prompt's ``variables`` list)."""
    out: list[PromptVariable] = []
    seen: set[str] = set()
    for m in _EXPR_RE.finditer(content):
        expr = m.group(1).strip()
        decl = _TYPE_DECL_RE.match(expr)
        if not decl:
            continue
        name = decl.group(1)
        if "." in name or name in seen:
            continue  # dotted paths aren't user-fillable variables
        seen.add(name)
        vtype, options = parse_type_decl(decl.group(2))
        out.append(PromptVariable(name=name, type=vtype, options=options))
    return out


def merged_variables(
    template: PromptTemplate | PromptSnippet, resolver: SnippetResolver | None = None
) -> list[PromptVariable]:
    """A template's own variables ∪ the variables of every snippet it transitively
    includes, deduped by name (the host's declaration wins). This is the set the
    fill-in UI renders and binding-time validation checks."""
    out: list[PromptVariable] = []
    seen_names: set[str] = set()

    def add(vars_: list[PromptVariable]) -> None:
        for v in vars_:
            if v.name not in seen_names:
                seen_names.add(v.name)
                out.append(v)

    add(template.variables)

    def walk(content: str, depth: int, seen: tuple[str, ...]) -> None:
        if depth > MAX_INCLUDE_DEPTH or not resolver:
            return
        for name in included_snippet_names(content):
            if name in seen:
                continue
            snip = resolver(name)
            if snip is None:
                continue
            add(snip.variables)
            walk(snip.content, depth + 1, seen + (name,))

    walk(template.content, 0, ())
    return out
