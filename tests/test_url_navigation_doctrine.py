"""Guardrail tests for the URL-navigation doctrine (url-navigation-unification.md).

ONE canonical routing model: all navigation goes through the hash router
(`navigate`/`setQuery` from `useHashRoute.ts`). Pages must never write the URL
directly, and must take the full `RouteProps` contract (not `Partial`, which
silently degrades URL state to local state if the App shell contract ever drifts).

These pin the structural invariants so a future page can't silently reintroduce a
raw `location.hash`/`history.*` bypass or a `Partial<RouteProps>` guard — the
project's FE-source-guard idiom (see test_transport_doctrine.py), not an ESLint
dependency (web builds with tsc, no eslint toolchain).
"""

from __future__ import annotations

import re
from pathlib import Path

_PAGES = Path("web/src/pages")
_ROUTER = Path("web/src/app/useHashRoute.ts")

# Raw URL/history mutations that bypass the router. The router file itself is the
# ONLY place allowed to touch these (it owns the push/replace mechanics).
_BYPASS_RE = re.compile(
    r"location\.hash\s*=|history\.(pushState|replaceState)\b|location\.replace\s*\(",
)


def _uncommented(line: str) -> str:
    """The code portion of a single line: everything before a real `//` line
    comment (not inside a string / URL). Line-by-line — never spans newlines, so
    it can't glue fragments into a false positive the way a global strip can. A
    line that is wholly a comment returns ''."""
    stripped = line.lstrip()
    if stripped.startswith(("//", "*", "/*")):
        return ""
    out, i, n, quote = [], 0, len(line), ""
    while i < n:
        c = line[i]
        if quote:
            out.append(c)
            if c == quote and line[i - 1] != "\\":
                quote = ""
        elif c in "\"'`":
            quote = c
            out.append(c)
        elif c == "/" and i + 1 < n and line[i + 1] == "/" and (i == 0 or line[i - 1] != ":"):
            break  # real line comment (`:` guard keeps https:// URLs intact)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def test_no_raw_history_or_location_bypass_in_pages():
    """No page writes the URL directly — all nav routes through navigate/setQuery."""
    offenders: list[str] = []
    for f in _PAGES.rglob("*.tsx"):
        for lineno, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = _BYPASS_RE.search(_uncommented(raw))
            if m:
                offenders.append(f"{f}:{lineno}  {m.group(0).strip()}")
    assert not offenders, (
        "Raw URL/history mutation in a page — route through navigate()/setQuery() "
        "(the hash router) instead:\n  " + "\n  ".join(offenders)
    )


def test_no_partial_routeprops_in_pages():
    """Pages take the FULL RouteProps contract (or a precise Pick<>), never
    Partial<RouteProps> — a Partial silently degrades URL state to local state
    (with `query ?? {}` / `setQuery ?? (()=>{})` guards) if App.tsx's contract
    ever breaks. App.tsx always spreads the full bundle, so Partial is a lie."""
    offenders = [
        str(f)
        for f in _PAGES.rglob("*.tsx")
        if "Partial<RouteProps>" in f.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "Partial<RouteProps> found — use full RouteProps or a precise "
        "Pick<RouteProps, ...> so URL state can't silently degrade to local:\n  "
        + "\n  ".join(offenders)
    )


def test_no_startediting_seed_prop_in_pages():
    """Detail panels take a controlled `editing` + `onEditingChange` pair (owned by
    the URL via `useEditFlag` → `?edit=1`), never a one-shot `startEditing` seed
    into a local `useState`. The seed made edit mode invisible to the URL: clicking
    Edit didn't push, Back couldn't leave edit, and refresh dropped it. Banning the
    prop keeps every view↔edit toggle on the single ?edit=1 contract (S4)."""
    offenders: list[str] = []
    for f in _PAGES.rglob("*.tsx"):
        for lineno, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "startEditing" in _uncommented(raw):
                offenders.append(f"{f}:{lineno}")
    assert not offenders, (
        "`startEditing` prop found — detail panels must be controlled via "
        "`editing`/`onEditingChange` (useEditFlag, ?edit=1), not a local-state seed:\n  "
        + "\n  ".join(offenders)
    )


def test_replace_keys_use_replace_semantics():
    """Canonical model §3: in-place view refinements — search/tab/view-mode/filter/
    sort — are `replace` (they must NOT stack Back-undoable history). A
    `useQueryParam(query, setQuery, '<key>', …)` binding for one of these keys must
    pass `{ replace: true }`; otherwise it defaults to PUSH and every toggle spams a
    history entry (so Back rewinds filter changes instead of leaving the page). The
    push keys (open/edit/panel/entity/intent/dir) are intentionally absent here."""
    replace_keys = ("q", "tab", "view", "filter", "sort", "scope", "list", "include", "src", "tag")
    # useQueryParam(<q>, <sq>, 'KEY'  … ) — capture through end of line to see opts.
    call_re = re.compile(r"useQueryParam\([^,]+,[^,]+,\s*'(" + "|".join(replace_keys) + r")'")
    offenders: list[str] = []
    for f in _PAGES.rglob("*.tsx"):
        for lineno, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = _uncommented(raw)
            if call_re.search(code) and "replace" not in code:
                offenders.append(f"{f}:{lineno}  {code.strip()[:90]}")
    assert not offenders, (
        "A replace-class query key (search/tab/view/filter/sort/…) is bound without "
        "{ replace: true } — it will PUSH per toggle and spam history (canonical §3 "
        "says these are replace):\n  " + "\n  ".join(offenders)
    )


def test_router_still_owns_history_mechanics():
    """Sanity: the one allowed place (the router) still performs the hash/history
    writes — so the guard above is banning *bypasses*, not the mechanism itself."""
    src = _ROUTER.read_text(encoding="utf-8")
    assert (
        "location.hash" in src and "replaceState" in src
    ), "useHashRoute.ts should own the location.hash/replaceState mechanics"
