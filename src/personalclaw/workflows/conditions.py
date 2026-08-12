"""The ONE boolean-condition dialect the engine evaluates.

Three places in the engine ask "is this true?": a `gate` of kind `expression`, a `loop`
in `until` mode, and a node's `success_when`. Before this module they answered it two
different ways — and one of them was silently wrong.

**The defect this module exists to fix.** The expression gate resolved its `expr` with
`bindings.resolve` (which INTERPOLATES a template into a string) and then asked
`_truthy` about the result. So the shipped `{{inputs.fix}} == true` became the STRING
`"false == true"` when `fix` was false — and a non-empty string is truthy, so the gate
passed. Two shipped templates carried a gate that could never reject. A comparison is
not a string, and the fix is to parse it rather than to interpolate it.

**The grammar, closed and deliberately small** (precedence low → high)::

    ||        top-level disjunction
    &&        conjunction
    !         unary negation
    ( … )     grouping
    ==  !=    comparison against a literal or another reference
    <leaf>    bare truthiness of a resolved reference

A leaf operand is one of: a `{{binding}}`, a bare `dotted.path` (resolved through the
SAME `bindings.resolve_expr` every prompt uses, pipes included), or a literal —
`true`/`false`/`null`/`none` (case-insensitive), a number, or a quoted string.

**Word forms (`and`/`or`/`not`) are deliberately NOT accepted.** Two spellings of one
operator is a dialect nobody can memorise, and the failure is legible: `a and b` parses
as one bare path, `resolve_expr` cannot walk it, and the caller reports the expression
verbatim. Silently accepting both would instead invite each template to pick one.

**Unresolvable references RAISE `BindingError`** rather than evaluating to false. A
condition that cannot read what it tests has not answered the question, and the callers
each turn that into their own typed failure — a gate cannot pass, a loop stops, a node
fails. Treating "I could not tell" as "no" is how a control ships looking present.
"""

from __future__ import annotations

from typing import Any

from personalclaw.workflows.bindings import BindingContext, BindingError, resolve_expr

#: Literal spellings that are values rather than references, mapped exhaustively. A bare
#: word outside this table is a REFERENCE — so an unquoted `pass` raises rather than
#: comparing equal to the string "pass", which is the mistake worth failing loudly on.
_LITERALS: dict[str, Any] = {
    "true": True,
    "false": False,
    "null": None,
    "none": None,
}

#: The comparison operators, longest-first so `!=` is never read as `!` + `=`.
_COMPARISONS = ("==", "!=")


def truthy(value: Any) -> bool:
    """The engine's ONE truthiness rule.

    A string is falsy when it spells a falsy value, because half the values the engine
    reads come back from a model as text and `bool("false")` is True.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "null", "none")
    if isinstance(value, (list, dict)):
        return bool(value)
    return bool(value)


def evaluate(expr: str, ctx: BindingContext) -> bool:
    """Evaluate one boolean condition. Raises `BindingError` on an unresolvable leaf."""
    text = (expr or "").strip()
    if not text:
        raise BindingError("empty condition", expr or "")
    return _or(text, ctx, expr or "")


# ── the recursive descent ────────────────────────────────────────────────────


def _or(text: str, ctx: BindingContext, whole: str) -> bool:
    parts = _split_top(text, "||")
    if len(parts) > 1:
        # NOT short-circuited: every leaf is resolved, so a typo in the right-hand side of
        # a condition whose left side happens to be true still raises instead of hiding
        # until the day the left side flips.
        return any([_and(p, ctx, whole) for p in parts])
    return _and(text, ctx, whole)


def _and(text: str, ctx: BindingContext, whole: str) -> bool:
    parts = _split_top(text, "&&")
    if len(parts) > 1:
        return all([_unary(p, ctx, whole) for p in parts])
    return _unary(text, ctx, whole)


def _unary(text: str, ctx: BindingContext, whole: str) -> bool:
    inner = text.strip()
    if inner.startswith("!") and not inner.startswith("!="):
        return not _unary(inner[1:], ctx, whole)
    return _leaf(inner, ctx, whole)


def _leaf(text: str, ctx: BindingContext, whole: str) -> bool:
    inner = text.strip()
    if not inner:
        raise BindingError("empty condition operand", whole)
    if inner.startswith("(") and _matching_paren(inner) == len(inner) - 1:
        return _or(inner[1:-1], ctx, whole)

    op, left_raw, right_raw = _split_comparison(inner)
    if op is None:
        return truthy(_operand(inner, ctx, whole))
    left = _operand(left_raw, ctx, whole)
    right = _operand(right_raw, ctx, whole)
    if op == "==":
        return _equal(left, right)
    if op == "!=":
        return not _equal(left, right)
    # Exhaustive over `_COMPARISONS`; a new operator must add its branch here rather than
    # falling through to a default that would silently read as false.
    raise BindingError(f"unsupported comparison {op!r}", whole)


def _equal(left: Any, right: Any) -> bool:
    """Equality with ONE coercion: a bool against its own spelling.

    `{{inputs.flag}} == true` resolves the left side to a real `True`, but a model-filled
    field arrives as the string `"true"` often enough that comparing the two as different
    types would make the same template pass or fail depending on who wrote the value.
    Everything else compares as-is — a string is not silently a number.
    """
    if isinstance(left, bool) and isinstance(right, str):
        return left is _LITERALS.get(right.strip().lower(), object())
    if isinstance(right, bool) and isinstance(left, str):
        return right is _LITERALS.get(left.strip().lower(), object())
    return bool(left == right)


def _operand(text: str, ctx: BindingContext, whole: str) -> Any:
    """One side of a comparison, or a bare truthiness leaf."""
    raw = text.strip()
    if not raw:
        raise BindingError("empty condition operand", whole)

    if raw.startswith("{{") and raw.endswith("}}"):
        return resolve_expr(raw[2:-2].strip(), ctx)

    if (raw[0] == raw[-1] == '"' or raw[0] == raw[-1] == "'") and len(raw) >= 2:
        return raw[1:-1]

    lowered = raw.lower()
    if lowered in _LITERALS:
        return _LITERALS[lowered]

    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass

    return resolve_expr(raw, ctx)


# ── tokenizing helpers ───────────────────────────────────────────────────────


def _split_top(text: str, token: str) -> list[str]:
    """Split on *token* at the top level only, respecting quotes and parentheses."""
    parts: list[str] = []
    depth = 0
    quote = ""
    cur = ""
    i = 0
    n = len(text)
    width = len(token)
    while i < n:
        ch = text[i]
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
        elif ch == ")":
            depth -= 1
        if depth == 0 and text[i : i + width] == token:
            parts.append(cur)
            cur = ""
            i += width
            continue
        cur += ch
        i += 1
    parts.append(cur)
    return parts if len(parts) > 1 else [text]


def _split_comparison(text: str) -> tuple[str | None, str, str]:
    """The top-level comparison, or `(None, "", "")` when there is none."""
    depth = 0
    quote = ""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0:
            for op in _COMPARISONS:
                if text[i : i + len(op)] == op:
                    return op, text[:i], text[i + len(op) :]
        i += 1
    return None, "", ""


def _matching_paren(text: str) -> int:
    """Index of the `)` closing the `(` at position 0, or -1."""
    depth = 0
    quote = ""
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1
