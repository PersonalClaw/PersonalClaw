"""``walk_window`` must report BOTH of its caps, and only when it really truncated.

🔴 Issue 2553. ``walk_window`` enforces two caps and reported one. ``MAX_ELEMENTS`` set
``walk.truncated = True``; ``MAX_DEPTH`` silently dropped the whole subtree and left the flag
at its ``False`` default, so ``op_snapshot`` forwarded ``"truncated": False`` and a deep window
reached the model **labelled complete**.

That contradicted the function's own two stated contracts — its docstring ("a truncated answer
*that says so* is more useful than a killed process") and ``MAX_ELEMENTS``' comment ("a window
exposing more is truncated *and says so*"). Absent and complete are different facts, and this is
the read a model uses to decide what is on screen: asked whether a window holds a password
field, it saw the walked elements and ``truncated: false``, and could only conclude no.
``MAX_DEPTH`` of 25 is generous for a native window and not generous for a Chromium or Electron
one, which is exactly where such a field lives.

**Why there is a test per cap rather than one truncation test.** A single assertion on
``truncated`` passed on the unfixed code, because the element cap set the flag correctly and
masked the depth cap entirely. One test covering "truncation is reported" is precisely the shape
that let this survive, so each cap is pinned separately and neither can regress alone.

The two floors matter as much as the two caps. Fixing this by setting the flag unconditionally on
the depth branch would make every deep-but-complete tree claim truncation — a flag that is always
true carries no information and teaches its reader to ignore it, which is the same defect facing
the other way. So a complete tree and a leaf sitting exactly at the limit both assert ``False``.

The walk is driven over a synthetic element tree by faking the FFI helpers, because the suite's
existing computer-use tests mock ``walk_window`` itself and therefore never exercise its
internals — which is the other reason this went unnoticed.
"""

from __future__ import annotations

import pytest

from personalclaw.computer_use import macos_ffi
from personalclaw.computer_use.types import MAX_DEPTH, MAX_ELEMENTS


class _Node:
    """One synthetic AX element. ``children`` is what the real ``AXChildren`` would return."""

    def __init__(self, name: str, children: list["_Node"] | None = None) -> None:
        self.name = name
        self.children = children or []


def _chain(length: int) -> _Node:
    """A single path ``length`` nodes long — deep, but only ``length`` elements in total.

    Depth and breadth are varied independently on purpose: a tree that tripped BOTH caps could
    not tell us which one set the flag, which is the ambiguity that hid this bug.
    """
    node = _Node("leaf")
    for i in range(length - 1):
        node = _Node(f"n{i}", [node])
    return node


def _fan(width: int) -> _Node:
    """A root with *width* leaf children — wide and shallow, so only the element cap can fire."""
    return _Node("root", [_Node(f"c{i}") for i in range(width)])


@pytest.fixture
def walk(monkeypatch):
    """Drive the real ``walk_window`` over a synthetic tree.

    Only the FFI edges are faked. The loop under test — both cap checks, the stack discipline and
    the flag — is the shipped code.
    """

    def _run(root: _Node):
        monkeypatch.setattr(macos_ffi, "_load", lambda: object())
        monkeypatch.setattr(macos_ffi, "is_process_trusted", lambda: True)
        monkeypatch.setattr(macos_ffi, "resolve_app_pid", lambda app: 4242)
        monkeypatch.setattr(macos_ffi, "_front_window", lambda frameworks, application: root)
        # `_attribute(frameworks, handle, "AXChildren")` then `_array_items(...)` is how the walk
        # descends; returning the node itself and unwrapping in `_array_items` keeps the two
        # calls honest rather than collapsing them into one fake.
        monkeypatch.setattr(macos_ffi, "_attribute", lambda frameworks, element, name: element)
        monkeypatch.setattr(
            macos_ffi, "_array_items", lambda frameworks, ref: list(getattr(ref, "children", []))
        )
        monkeypatch.setattr(
            macos_ffi, "_text_attribute", lambda frameworks, element, name: element.name
        )
        monkeypatch.setattr(
            macos_ffi, "_bool_attribute", lambda frameworks, element, name, default: default
        )
        monkeypatch.setattr(macos_ffi, "_frame", lambda frameworks, element: (0.0, 0.0, 1.0, 1.0))
        monkeypatch.setattr(macos_ffi, "_action_names", lambda frameworks, element: ())

        class _App:
            def AXUIElementCreateApplication(self, pid):  # noqa: N802 - mirrors the AX symbol
                return object()

        class _FW:
            ax = _App()

        monkeypatch.setattr(macos_ffi, "_load", lambda: _FW())
        return macos_ffi.walk_window("Finder")

    return _run


def test_the_harness_walks_the_synthetic_tree_at_all(walk):
    """Floor on the fixture, not the fix: an inert harness would make every case below pass."""
    result = walk(_fan(3))
    assert len(result.elements) == 4, "root plus three children should be walked"
    assert [e.title for e in result.elements][0] == "root"


def test_a_depth_truncated_tree_reports_truncated(walk):
    """THE defect. A chain deeper than MAX_DEPTH drops a subtree, so it must say so.

    Deliberately far below MAX_ELEMENTS, so a pass here cannot be the element cap doing the
    work — on the unfixed code this returned `truncated is False`.
    """
    result = walk(_chain(MAX_DEPTH + 5))
    assert (
        len(result.elements) < MAX_ELEMENTS
    ), "precondition: this tree must not trip the element cap, or it proves the wrong cap"
    assert result.truncated is True, (
        "a subtree was dropped at MAX_DEPTH and the walk reported the tree as complete — the "
        "model would read `truncated: false` for a window it was only shown part of (#2553)"
    )


def test_an_element_truncated_tree_still_reports_truncated(walk):
    """The cap that already worked. Pinned separately so it cannot regress alone."""
    result = walk(_fan(MAX_ELEMENTS + 10))
    assert len(result.elements) == MAX_ELEMENTS
    assert result.truncated is True


def test_a_complete_tree_reports_not_truncated(walk):
    """Vacuity floor. A flag that is always True is as uninformative as one always False."""
    result = walk(_fan(3))
    assert result.truncated is False


def test_a_leaf_at_the_depth_limit_does_not_cry_wolf(walk):
    """The guard on the fix itself: exactly-at-the-limit with nothing below is COMPLETE.

    ``MAX_DEPTH + 1`` nodes, not ``MAX_DEPTH``. The count has to put a node at depth *exactly*
    ``MAX_DEPTH`` with no children, because that is the only input that reaches the ``elif`` at
    all — and getting it wrong is how this test first shipped useless.

    **Found by mutation, not by review.** Written first with ``_chain(MAX_DEPTH)``, it passed and
    looked right: a chain of ``MAX_DEPTH`` nodes spans depths ``0..MAX_DEPTH-1``, so
    ``depth < MAX_DEPTH`` holds for every node, the ``elif`` never executes, and replacing it with
    a bare ``else`` changed nothing this test could see. The mutant that sets the flag
    unconditionally SURVIVED. The old assertion on ``len(result.elements)`` was true and proved
    nothing about the depth actually reached, which is why the precondition below asserts the
    depth instead of a count that merely correlates with it.

    Setting the flag unconditionally would report truncation on every deep-but-complete tree,
    teaching the reader to ignore it — the same defect as #2553 facing the other way.
    """
    depth_of_chain = MAX_DEPTH + 1
    result = walk(_chain(depth_of_chain))
    assert len(result.elements) == depth_of_chain, (
        f"precondition: all {depth_of_chain} nodes must be walked, so the deepest sits at depth "
        f"MAX_DEPTH ({MAX_DEPTH}) and is the one node that reaches the depth branch"
    )
    assert result.truncated is False, (
        "nothing was dropped — the node at MAX_DEPTH had no children — so reporting truncation "
        "here would make the flag fire on complete trees and cost it its meaning"
    )
