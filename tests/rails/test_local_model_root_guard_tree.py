"""The source-tree checks split from
``tests/test_local_model_root_guard.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import real_model_root_guard

from personalclaw.local_models import layouts


def _cache_root_functions() -> set[str]:
    """Public ``layouts`` functions whose FIRST parameter is ``cache_root``, read from SOURCE.

    Derived by parsing the file, never by introspecting the imported module: the autouse
    fixture has already replaced every guarded attribute with ``_guarded(cache_root, ...)``,
    so ``inspect.signature`` reports ``cache_root`` first for anything *already* wrapped and
    a signature-based derivation would confirm itself. Reading the source asks the question
    the rail actually cares about — what entry points EXIST — independently of what the rail
    did to them.
    """
    tree = ast.parse(Path(layouts.__file__).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):  # module-private helpers take a path, not a root
            continue
        args = node.args.args
        if args and args[0].arg == "cache_root":
            found.add(node.name)
    return found


def test_the_guarded_set_covers_every_cache_root_entry_point():
    """The rail's population is DERIVED, so a new entry point cannot escape it.

    ``test_every_guarded_entry_point_is_wrapped`` below iterates the *listed* set, which
    proves every listed name is wrapped but says nothing about a name nobody listed — a
    one-sided inventory. A cache-root function added to ``layouts.py`` and left out of
    ``GUARDED_FUNCTIONS`` is exactly the hole the bound-model-deletion rail must not have,
    and it is the hole a listed-set check reports as clean. So compare the list against the
    module's real surface in both directions.
    """
    derived = _cache_root_functions()
    # Vacuity: a failed/empty parse would make the equality below trivially true, including
    # against a GUARDED_FUNCTIONS someone had emptied. Assert the scan really found the
    # surface, and specifically found the sweep the incident ran.
    assert len(derived) >= 7, f"the cache-root scan found only {sorted(derived)}"
    assert "delete_all_layouts" in derived, derived

    listed = set(real_model_root_guard.GUARDED_FUNCTIONS)
    unguarded = derived - listed
    assert not unguarded, (
        f"layouts.{sorted(unguarded)} take a cache_root but are NOT in GUARDED_FUNCTIONS — "
        f"the model-root rail has a hole it reports as clean. Add them there so the autouse "
        f"fixture wraps them."
    )
    stale = listed - derived
    assert not stale, (
        f"GUARDED_FUNCTIONS lists {sorted(stale)}, which is no longer a public cache-root "
        f"entry point in layouts.py — a rail entry matching nothing looks clean."
    )
