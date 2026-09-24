"""BA-1 — the sentinel action vocabulary + parser (browse/sentinels.py).

Covers done_when part 3: ref-based sentinels ``CLICK <ref>`` / ``TYPE <ref>(value)`` parse
correctly, the full §2 verb set is recognized, and every action round-trips (an action the
agent emits parses back to the same ref+value).
"""

from __future__ import annotations

import pytest

from personalclaw.browse.sentinels import (
    ClickAction,
    DoneAction,
    GoBackAction,
    NavigateAction,
    NotesAction,
    ScrollAction,
    SubmitAction,
    TypeAction,
    WaitAction,
    parse_sentinel,
)


def test_click_ref_parses():
    a = parse_sentinel("CLICK a1b2c3d4")
    assert a == ClickAction(ref="a1b2c3d4")


def test_type_ref_value_parses():
    a = parse_sentinel("TYPE a1b2c3d4(hello world)")
    assert a == TypeAction(ref="a1b2c3d4", value="hello world")


def test_type_value_may_contain_parens():
    # The LAST ')' closes the group, so a value with inner parens survives.
    a = parse_sentinel("TYPE deadbeef(foo (bar) baz)")
    assert a == TypeAction(ref="deadbeef", value="foo (bar) baz")


def test_type_empty_value():
    a = parse_sentinel("TYPE deadbeef()")
    assert a == TypeAction(ref="deadbeef", value="")


def test_bare_word_sentinels():
    assert parse_sentinel("SUBMIT") == SubmitAction()
    assert parse_sentinel("GO_BACK") == GoBackAction()
    assert parse_sentinel("DONE") == DoneAction()


def test_navigate_scroll_wait_notes():
    assert parse_sentinel("NAVIGATE https://example.com/x") == NavigateAction(
        url="https://example.com/x"
    )
    assert parse_sentinel("SCROLL down") == ScrollAction(direction="down")
    assert parse_sentinel("SCROLL up") == ScrollAction(direction="up")
    assert parse_sentinel("WAIT 3") == WaitAction(seconds=3)
    assert parse_sentinel("NOTES the price is $40") == NotesAction(text="the price is $40")


def test_wait_clamped_to_band():
    assert parse_sentinel("WAIT 99") == WaitAction(seconds=10)
    assert parse_sentinel("WAIT 0") == WaitAction(seconds=1)


def test_case_insensitive_and_whitespace_tolerant():
    assert parse_sentinel("  click A1B2C3D4  ") == ClickAction(ref="a1b2c3d4")
    assert parse_sentinel("submit") == SubmitAction()


def test_unknown_and_blank_lines_ignored():
    assert parse_sentinel("") is None
    assert parse_sentinel("   ") is None
    assert parse_sentinel("just some prose the model wrote") is None
    assert parse_sentinel("CLICKX abc") is None
    assert parse_sentinel("CLICK") is None  # missing ref


def test_click_rejects_non_ref_token():
    # A ref is hex; a positional integer or a word is not a ref and must not parse as CLICK.
    assert parse_sentinel("CLICK 7") is None
    assert parse_sentinel("CLICK the-button") is None


@pytest.mark.parametrize(
    "action",
    [
        NavigateAction(url="https://example.com/path?q=1"),
        ClickAction(ref="a1b2c3d4"),
        TypeAction(ref="deadbeef", value="user@example.com"),
        TypeAction(ref="deadbeef", value="pass (with parens) 123"),
        TypeAction(ref="deadbeef", value=""),
        SubmitAction(),
        ScrollAction(direction="down"),
        WaitAction(seconds=5),
        GoBackAction(),
        DoneAction(),
        NotesAction(text="a freeform note with symbols: $, %, →"),
    ],
)
def test_round_trip(action):
    """Every emitted action parses back to itself — the property the loop relies on."""
    assert parse_sentinel(action.render()) == action
