"""The source-tree checks split from
``tests/test_state_history_background_surface.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index

import personalclaw

SRC = Path(personalclaw.__file__).resolve().parent


#: The producer that already existed. It is the census's vacuity floor: if the walker stops
#: matching calls (a rename, an alias, a bad AST assumption) this row disappears too, so a census
#: that has quietly stopped seeing anything fails here instead of reading clean.
KNOWN_SCHEDULED_PRODUCER = "durability/state_history.py"


#: The producer this change adds.
EXPECTED_BACKGROUND_PRODUCER = "gateway.py"


def _writing_surface_calls() -> dict[str, set[str]]:
    """Map ``<path relative to src/personalclaw>`` -> surface constants passed to a CALL of
    ``writing_surface(...)``.

    Deliberately an AST walk over calls, not a text scan for the constant's name. A file that only
    MENTIONS `SURFACE_BACKGROUND` — in a comment, a docstring, or a read like
    `history_debounce._dominant_surface`'s membership test — is not a producer, and a census that
    counted those would report this change as already done before a single line was written.
    """
    found: dict[str, set[str]] = {}
    for path, tree in source_index.package_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if called != "writing_surface":
                continue
            arg = node.args[0]
            surface = arg.attr if isinstance(arg, ast.Attribute) else getattr(arg, "id", "")
            found.setdefault(str(path.relative_to(SRC)), set()).add(surface)
    return found


def test_the_census_still_sees_the_KNOWN_producer():
    """🔴 The vacuity floor, asserted on its own so a broken walker cannot read as a clean tree.

    Every claim below is "the census found / did not find X". That is only evidence if the census
    can still find something it is known to contain.
    """
    calls = _writing_surface_calls()
    assert KNOWN_SCHEDULED_PRODUCER in calls, (
        "the census found no `writing_surface(...)` call in "
        f"{KNOWN_SCHEDULED_PRODUCER} — the walker is broken, not the tree"
    )
    assert "SURFACE_SCHEDULED" in calls[KNOWN_SCHEDULED_PRODUCER]


def test_SURFACE_BACKGROUND_has_a_producer_outside_state_history():
    """🔴 The gap this change closes. A declared surface with no writer is an inert control: the
    panel's filter offers a choice the data can never satisfy."""
    calls = _writing_surface_calls()
    producers = {
        path
        for path, surfaces in calls.items()
        if "SURFACE_BACKGROUND" in surfaces and path != KNOWN_SCHEDULED_PRODUCER
    }
    assert producers, (
        "SURFACE_BACKGROUND has no producer outside state_history.py — it is still a declared "
        "constant nothing writes"
    )
    assert EXPECTED_BACKGROUND_PRODUCER in producers, (
        f"expected the unattended trigger dispatch in {EXPECTED_BACKGROUND_PRODUCER} to be the "
        f"producer; found {sorted(producers)}"
    )


def test_the_producer_is_APPLIED_to_the_unattended_dispatch():
    """A call that exists is not a call that runs.

    The producer is a decorator, so deleting the one line that APPLIES it would leave the
    `writing_surface(SURFACE_BACKGROUND)` call sitting in the tree, still passing the census above,
    while every unattended fire went back to the interactive surface. So the census names the
    decorated function too — the same "assert the call SITE, not the mechanism" reason the census
    exists at all.
    """
    tree = source_index.tree(SRC / "gateway.py")
    dispatches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_fire_store_trigger"
    ]
    assert len(dispatches) == 1, (
        "expected exactly one `_fire_store_trigger`; the dispatch seam has moved or forked, and "
        "this census no longer measures the shared path"
    )
    decorators = {
        d.attr if isinstance(d, ast.Attribute) else getattr(d, "id", "")
        for d in dispatches[0].decorator_list
    }
    assert "_background_write_surface" in decorators, (
        "`_fire_store_trigger` is no longer wrapped in the background writing surface — the "
        f"producer exists but nothing applies it (decorators: {sorted(decorators)})"
    )


def test_a_MENTION_of_the_constant_does_not_count_as_a_producer():
    """The census must be scoped to the call, not to any file that names the constant.

    `history_debounce` names `SURFACE_BACKGROUND` (its `_dominant_surface` prefers it when a
    coalesced burst contains one) and `gateway`'s wrap carries it in prose. Neither of those is a
    writer; a text scan would have scored both.
    """
    debounce = SRC / "durability" / "history_debounce.py"
    text = source_index.text(debounce)
    assert (
        "SURFACE_BACKGROUND" in text
    ), "this discriminator is vacuous unless the file really does mention the constant"
    calls = _writing_surface_calls()
    assert "SURFACE_BACKGROUND" not in calls.get(
        "durability/history_debounce.py", set()
    ), "a READ of the constant was scored as a producer"

    gateway_text = source_index.text(SRC / "gateway.py")
    prose = [
        line
        for line in gateway_text.splitlines()
        if "SURFACE_BACKGROUND" in line and line.lstrip().startswith("#")
    ]
    for line in prose:
        assert "writing_surface(" not in line, f"a commented-out call would score: {line!r}"
