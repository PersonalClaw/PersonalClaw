"""Tests for board_options — powers inline Waiting-lane buttons on the board."""

from personalclaw.textfmt import board_options


def test_parse_simple_options():
    assert board_options("Pick one.\n[OPTIONS: A | B | C]") == ["A", "B", "C"]


def test_no_options_returns_empty():
    assert board_options("Just prose with no marker.") == []
    assert board_options("") == []


def test_single_option():
    assert board_options("[OPTIONS: Go]") == ["Go"]


def test_whitespace_is_stripped():
    assert board_options("[OPTIONS:   Apply now  |   Hold off   ]") == ["Apply now", "Hold off"]


def test_multiple_markers_uses_last():
    # An assistant message might quote an earlier OPTIONS block then ask a new question.
    txt = "Earlier I said [OPTIONS: old1 | old2]. Now choose:\n[OPTIONS: new1 | new2 | new3]"
    assert board_options(txt) == ["new1", "new2", "new3"]


def test_empty_parts_filtered():
    assert board_options("[OPTIONS: A ||| B]") == ["A", "B"]


def test_multiline_content_in_message():
    txt = """Some explanation.
Line two.
More text.

[OPTIONS: Ship it | Park it | Show diff]"""
    assert board_options(txt) == ["Ship it", "Park it", "Show diff"]
