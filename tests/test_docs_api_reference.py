"""Drift guard for the PUBLISHED API reference (``docs/reference/api-*.md``).

``docs/reference/api-overview.md`` used to carry a hand-maintained route table under a
header stating that it *"lists every route with a one-liner"*. Measured through the
production route census, it listed 248 of 689 distinct paths — 441 absent. The gap was
recoverable; the CLAIM was not, because a reader cannot tell a documented surface from an
absent one, and 415-odd missing rows is what a page always drifts to once a human is the
only thing keeping it current.

So the route list is now generated (``scripts/generate_api_route_reference.py``) and the
overview is orientation that defers to it. This suite is the rail that keeps it that way,
in both directions:

* the generated file byte-matches a fresh render and covers the WHOLE census, so it
  cannot go stale or partial;
* the overview cannot regrow a route catalogue, and cannot reassert completeness.

The second half matters as much as the first. A generated reference beside a page that
has quietly started re-listing routes is the original defect with an extra file — the
ceiling below is what makes reintroducing it red rather than merely regrettable.

**Cost note.** Every leg here needs the route census, an AST walk of the whole package
(~5s). It is computed ONCE for the module and shared; do not add a leg that calls
``_routes_from_ast`` or ``render_published_route_reference`` again. A sibling gate that
re-rendered a census per assertion took 18 minutes under load and stopped fitting in a
shard.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from personalclaw.manifest_reference import (
    PUBLISHED_ROUTE_REFERENCE,
    _render_published_routes,
    _routes_from_ast,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
OVERVIEW = REPO_ROOT / "docs" / "reference" / "api-overview.md"
ROUTES_DOC = REPO_ROOT / PUBLISHED_ROUTE_REFERENCE

#: How many distinct route paths the ORIENTATION page may name before it is a catalogue
#: again. The page legitimately names some: its "behaviours that are easy to get wrong"
#: section is about specific routes, and that section is the reason to keep the page.
#: Measured at 19 when written (inflated by prefix matching — naming
#: ``/api/tools/invoke`` also matches ``/api/tools``), so this leaves room for that
#: section to roughly double. It sits two orders of magnitude below what the deleted
#: table matched, which is the distance that makes the difference legible: a ceiling set
#: just above today's count would red on honest editing, and one set near 248 would let
#: the catalogue grow straight back.
_OVERVIEW_PATH_CEILING = 40

#: Self-completeness claims. The SECONDARY leg — a phrase fence is evadable by rewording,
#: which is why the ceiling above is the load-bearing one. This catches the specific
#: sentence shapes that made the old page dishonest, so the exact wording cannot return.
_COMPLETENESS_CLAIMS = (
    "lists every route",
    "lists all routes",
    "every route with a one-liner",
    "complete list of routes",
    "full list of routes",
    "documents every route",
)

_REGENERATE = (
    "Regenerate it with the path PINNED to this checkout (the repo .venv is an editable\n"
    "install of the MAIN checkout, so a bare run renders that tree's routes):\n"
    '    PYTHONPATH="$(git rev-parse --show-toplevel)/src" \\\n'
    "        python scripts/generate_api_route_reference.py\n"
    "The script refuses outright if the census and the target are different checkouts.\n"
    "Changing a route docstring ALSO needs `python -m personalclaw.manifest_reference`\n"
    "for the wheel's copy — the two files render the same census and are guarded apart."
)


@lru_cache(maxsize=1)
def _census() -> tuple[list[dict[str, Any]], str]:
    """The route census and its published rendering — one AST walk for the whole module."""
    routes = _routes_from_ast()
    return routes, _render_published_routes(routes)


def _assert_one_checkout() -> None:
    """The census and the file under test must come from the same tree.

    Otherwise this suite compares a render of one checkout's routes against another
    checkout's committed file, and its verdict means nothing in either direction.
    """
    import personalclaw

    imported = Path(personalclaw.__file__).resolve().parents[2]
    assert imported == REPO_ROOT, (
        "the imported personalclaw and the docs tree are different checkouts, so this\n"
        f"comparison is unattributable:\n  imported: {imported}\n  docs tree: {REPO_ROOT}\n"
        'Run pytest with PYTHONPATH="<this checkout>/src".'
    )


def test_published_route_reference_matches_a_fresh_render():
    """The committed route reference is exactly what the generator produces."""
    _assert_one_checkout()
    _, expected = _census()
    assert ROUTES_DOC.is_file(), f"{PUBLISHED_ROUTE_REFERENCE} is missing.\n{_REGENERATE}"
    assert ROUTES_DOC.read_text(encoding="utf-8") == expected, (
        f"{PUBLISHED_ROUTE_REFERENCE} differs from a fresh render — the published route\n"
        f"reference is stale, or was hand-edited.\n{_REGENERATE}"
    )


def test_published_route_reference_covers_the_whole_census():
    """Every registered route has a row, and the stated count is the measured one.

    Byte-equality above already implies this, but only transitively — it says the file
    equals the renderer, not that the renderer is complete. Asserted directly so the
    contract the page claims in its own first paragraph is the contract under test: if a
    future rendering ever dropped or truncated a section, this reds with the missing
    routes named, while the byte-compare would go quietly green on the new output.
    """
    routes, rendered = _census()
    missing = [
        f"{r['method']} {r['path']}"
        for r in routes
        if f"| `{r['method']}` | `{r['path']}` |" not in rendered
    ]
    assert not missing, (
        f"{len(missing)} of {len(routes)} registered routes have no row in "
        f"{PUBLISHED_ROUTE_REFERENCE}: {missing[:10]}"
    )
    stated = re.search(r"\*\*(\d+) registrations\*\* over \*\*(\d+) distinct paths\*\*", rendered)
    assert stated, "the route reference no longer states its own census size"
    assert int(stated.group(1)) == len(routes)
    assert int(stated.group(2)) == len({r["path"] for r in routes})


def test_route_family_index_accounts_for_every_registration():
    """The family counts sum to the census — no route falls outside its own index."""
    routes, rendered = _census()
    counts = [
        int(m.group(1))
        for m in re.finditer(r"^\| `/[^`]*` \| (\d+) \| \d+ \|$", rendered, re.MULTILINE)
    ]
    assert counts, "the route reference no longer renders a family index"
    assert sum(counts) == len(routes), (
        f"family index sums to {sum(counts)} but the census has {len(routes)} "
        "registrations — a route is being grouped into no family, or into two"
    )


def test_generated_route_reference_warns_against_hand_editing():
    """The file says it is generated, before the rail has to say it.

    A reader who edits a generated file and then has to decode a byte-compare failure
    was failed by the file, not by the guard.
    """
    head = ROUTES_DOC.read_text(encoding="utf-8")[:600]
    assert "GENERATED FILE" in head
    assert "generate_api_route_reference.py" in head


def test_api_overview_does_not_duplicate_the_route_catalogue():
    """The orientation page names a handful of routes, not the surface.

    This is the leg that makes the original defect unreintroducible. A hand-kept route
    table is only honest on the day it is written; the way it stops existing is that
    growing one back reds here.
    """
    routes, _ = _census()
    text = OVERVIEW.read_text(encoding="utf-8")
    named = sorted({r["path"] for r in routes if r["path"] in text})
    assert len(named) <= _OVERVIEW_PATH_CEILING, (
        f"api-overview.md now names {len(named)} distinct route paths (ceiling "
        f"{_OVERVIEW_PATH_CEILING}) — it is turning back into a route catalogue.\n"
        f"Route rows belong in {PUBLISHED_ROUTE_REFERENCE}, which is generated and "
        "therefore cannot go stale. Keep the overview to conventions and the behaviours "
        "that are not derivable from a route's docstring."
    )


def test_api_overview_defers_to_the_generated_reference():
    """The overview points at the route list and claims no completeness of its own."""
    text = OVERVIEW.read_text(encoding="utf-8")
    assert "api-routes.md" in text, (
        "api-overview.md no longer links the generated route reference — a reader who "
        "needs the route list has nowhere to go from here"
    )
    lowered = text.lower()
    asserted = [claim for claim in _COMPLETENESS_CLAIMS if claim in lowered]
    assert not asserted, (
        f"api-overview.md asserts completeness it does not have: {asserted}. The "
        f"complete list is generated into {PUBLISHED_ROUTE_REFERENCE}; this page must "
        "defer to it rather than claim to be it."
    )
