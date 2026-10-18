"""The source-tree checks split from
``tests/test_app_catalog_provenance.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app_catalog_provenance_shared
import source_index
from rails_repo import REPO

_isolate = app_catalog_provenance_shared._isolate


def _py_census(token: str) -> set[tuple[str, str]]:
    """Census ``token`` across the shipped package → {(module path, enclosing def)}.

    Enclosing function rather than line number, for the reason
    ``_scanner_gate_call_sites`` gives: a decision can move between functions while the line
    count stays put, and the function is what a reader reaches."""
    import personalclaw

    pkg_root = Path(personalclaw.__file__).resolve().parent
    files = list(source_index.package_files())
    # Vacuity floor #1: a broken glob reads as "no sites" and would make every assertion
    # below trivially true.
    assert len(files) > 100, f"census walked only {len(files)} files under {pkg_root}"

    sites: set[tuple[str, str]] = set()
    for path in files:
        text = source_index.text(path)
        if token not in text:
            continue
        owners = [
            (n.lineno, n.end_lineno or n.lineno, n.name)
            for n in ast.walk(source_index.tree(path))
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for lineno, line in enumerate(text.splitlines(), start=1):
            if token not in line:
                continue
            enclosing = [name for start, end, name in owners if start <= lineno <= end]
            sites.add(
                (path.relative_to(pkg_root).as_posix(), enclosing[-1] if enclosing else "<module>")
            )
    return sites


def _py_raw_count(token: str) -> int:
    """How many times *token* occurs in the package's bytes, counted WITHOUT the ast walk.

    Vacuity floor #2, and deliberately a different mechanism from ``_py_census``: if that
    walker (or its parse) ever silently produced nothing, this count would still be non-zero
    and the paired assertion would fail rather than agree."""
    import personalclaw

    pkg_root = Path(personalclaw.__file__).resolve().parent
    return sum(
        source_index.text(p).count(token)
        for p in source_index.package_files()
        if p.is_relative_to(pkg_root)
    )


def test_one_owner_resolves_a_catalog_name_collision():
    """No second site decides which copy of an app name the Store shows.

    Two tokens, because a new resolver would reach for one or both: excluding the Library
    (``_installed_names()``) and ranking by source kind (``precedence_rank(``). Every one of
    the five scanners that used to hold a private ``seen`` set called the first.

    If you are here because this went red: the fix is not to widen the expected set. It is to
    route the new decision through ``resolve_catalog_entries``, or to justify a second owner
    and widen this deliberately.
    """
    assert _py_census("_installed_names()") == {
        ("apps/catalog.py", "resolve_catalog_entries"),
        ("apps/catalog.py", "_installed_names"),
    }
    assert _py_census("precedence_rank(") == {
        ("apps/catalog.py", "precedence_rank"),
        ("apps/catalog.py", "resolve_catalog_entries"),
    }

    # Vacuity, both directions. A token that is not in the package must census EMPTY…
    assert _py_census("_installed_names_for_every_source()") == set()
    # …and the tokens above must really be present, counted by a mechanism that does not
    # share the census's parse. An absolute lower bound: the def + its one caller, and the
    # rank helper's def + its two comparisons.
    assert _py_raw_count("_installed_names()") >= 2
    assert _py_raw_count("precedence_rank(") >= 3


def _web_census(token: str) -> set[str]:
    """Census ``token`` across the SPA's non-test sources → {relative path}.

    Comment-only lines are skipped, matching ``web/src/design/tokenLint.test.ts``: prose that
    quotes the old code is not a second implementation of it."""
    web_src = REPO / "web" / "src"
    files = sorted(
        p
        for p in source_index.web_files()
        if p.suffix.startswith(".ts") and not p.name.endswith((".test.ts", ".test.tsx", ".d.ts"))
    )
    # Vacuity floor #1: a wrong root or suffix reads as "no sites".
    assert len(files) > 100, f"census walked only {len(files)} files under {web_src}"
    hits: set[str] = set()
    for path in files:
        for line in source_index.text(path).splitlines():
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            if token in stripped:
                hits.add(path.relative_to(web_src).as_posix())
    return hits


def _web_raw_count(token: str) -> int:
    """Raw occurrence count over the SPA's bytes — vacuity floor #2 for the web census, on a
    different mechanism (no line splitting, no comment filter)."""
    return sum(
        source_index.text(p).count(token)
        for p in source_index.web_files()
        if p.suffix.startswith(".ts")
    )


def test_one_owner_labels_app_provenance():
    """No surface derives a provenance label on its own.

    ``'platform'`` and ``'first-party'`` are the two words in this vocabulary that can only
    mean provenance, so they are the census tokens. ``lib/api.ts`` is expected for
    ``'first-party'`` and only there: it declares the WIRE type, it does not render a label.

    Issue 2514 is what happens without this rail — the Tools page grew its own two-way
    labelling and the Store card silently disagreed with it.

    🔴 `pages/tools/ToolsPage.tsx` IS in the expected set below, and that is a deliberate,
    temporary widening rather than an oversight. This branch originally replaced that page's
    `providerLocked ? 'platform' : 'built-in'` binary too — the line #2514 reported. **PR #2632
    fixes it better**: it plumbs the bundle's recorded trust TIER through `InstalledApp` from the
    install gate — the only place that sees the signature, so a verified maintainer signature can
    raise `community` to `official` — and shares one string with the install dialog through
    `lib/trustTier`. Provenance ("where did these bytes come from") and tier ("how vouched-for is
    it") are two different facts, so ceding the badge to the better answer costs nothing;
    keeping both would have put two owners on one pill, which is the defect this rail exists for.

    So this branch reverted its Tools-page half. #2632 has since MERGED, and measuring what it
    actually left behind matters more than what was expected of it: `lib/trustTier` owns the TIER
    map (`builtin: 'built-in'`), but **`'platform'` stayed inline at `ToolsPage.tsx:390`** — it is
    the *locked* case, which is not a tier, so it did not fit that map.

    `pages/tools/ToolsPage.tsx` therefore stays in the expected set, and not as a placeholder:
    the word `'platform'` genuinely has two homes now, `lib/provenance.ts` here and that inline
    label there, meaning the same thing on two surfaces. That is a real if mild duplication,
    recorded rather than papered over — it is a residual of the split, not something this branch
    should fix by editing code another change just landed. If the census grows a THIRD entry, the
    rail is doing its job and the duplication has stopped being mild."""
    assert _web_census("'platform'") == {"lib/provenance.ts", "pages/tools/ToolsPage.tsx"}
    assert _web_census("'first-party'") == {"lib/provenance.ts", "lib/api.ts"}

    # Vacuity, both directions.
    assert _web_census("'platform-provenance-that-does-not-exist'") == set()
    assert _web_raw_count("'platform'") >= 1
    assert _web_raw_count("'first-party'") >= 2


def test_one_owner_merges_the_app_catalog():
    """No surface re-concatenates the catalog's four app lists.

    Three did, in three different orders, which is how one payload produced three answers to
    "which copy of this app am I looking at?". The property accessors are the census tokens —
    the wire TYPE still names the fields, but nothing reads them outside the merge."""
    for token in (".bundled", ".localApps", ".remoteApps", ".gitApps"):
        assert _web_census(f"catalog?{token}") == {"lib/appCatalog.ts"}, token
    # A consumer spelling its parameter something else still has to go through the merge, so
    # the bare accessors must not appear anywhere but there either.
    assert _web_census(".remoteApps") == {"lib/appCatalog.ts"}
    assert _web_census(".gitApps") == {"lib/appCatalog.ts"}

    # Vacuity, both directions.
    assert _web_census("catalog?.notAListThatExists") == set()
    assert _web_raw_count(".remoteApps") >= 1
    assert _web_raw_count("catalog?.gitApps") >= 1
