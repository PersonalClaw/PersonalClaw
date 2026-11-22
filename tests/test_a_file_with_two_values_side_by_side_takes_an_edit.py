"""A file holding two hidden values side by side still takes an edit away from them.

An agent's file tools show a file with credential-shaped values masked, and map an edit made
against that view back onto the stored text (``security.masked_edit``,
``security.keep_masked_lines``). The mapping walks the unchanged text between the markers. Two
values written side by side leave nothing between their markers, and that empty text was read as
"the end of the file": the first marker swallowed the rest, the walk failed, and every edit of the
file was refused, however far from a hidden value, with a sentence saying the stored copy "no longer
lines up" though nothing had changed. ``tests/test_redaction_cost.py``, whose corpus holds two
access keys written back to back, could not be edited at all.

Now a run of markers with nothing between them stands for one stored span. An edit outside it
keeps every stored byte; one that cuts into it, or would set its values down again, is refused,
as before; and a file whose view still cannot be mapped says so plainly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw import security
from personalclaw.security import MaskConflict

pytestmark = pytest.mark.security

KEYS = "AKIAQQQQQQQQQQQQQQQQAKIAQQQQQQQQQQQQQQQQ"
STORED = f'KEYS = "{KEYS}"\nname = "old"\n'
CORPUS = Path(__file__).with_name("test_redaction_cost.py")


def test_the_fixture_shows_two_markers_side_by_side():
    shown = security.redact_for_display(STORED)

    assert KEYS not in shown
    assert "][" in shown.splitlines()[0], shown


def test_an_edit_away_from_two_values_side_by_side_is_made_and_keeps_them():
    """🔴 Red before: refused, "no longer lines up with the redacted version you edited"."""
    out, count = security.masked_edit(STORED, 'name = "old"', 'name = "new"')

    assert count == 1
    assert out == f'KEYS = "{KEYS}"\nname = "new"\n'


def test_a_whole_file_write_away_from_them_keeps_their_stored_bytes():
    """🔴 Red before: refused the same way."""
    shown = security.redact_for_display(STORED)

    out = security.keep_masked_lines(shown.replace('"old"', '"new"'), STORED)

    assert out == f'KEYS = "{KEYS}"\nname = "new"\n'


def test_the_file_that_found_it_takes_an_edit_to_its_first_line():
    """🔴 Red before: every edit of it was refused."""
    stored = CORPUS.read_text(encoding="utf-8")
    first, rest = stored.split("\n", 1)

    out, count = security.masked_edit(stored, first, first + "  # an edit")

    assert count == 1
    assert out == first + "  # an edit\n" + rest


def test_an_edit_that_cuts_between_two_values_side_by_side_is_refused():
    shown = security.redact_for_display(STORED)
    markers = security.mask_markers(shown)
    cut = markers[0] + markers[1][:3]

    with pytest.raises(MaskConflict) as refused:
        security.masked_edit(STORED, cut, "x")

    assert str(refused.value) == security.MARKER_CUT


def test_an_edit_that_would_set_their_values_down_again_is_refused():
    """Which value each marker of the run stands for cannot be told, so a line holding the run is
    kept only by leaving it alone, as a moved marker is refused anywhere."""
    shown_line = security.redact_for_display(STORED).splitlines()[0]

    with pytest.raises(MaskConflict) as refused:
        security.masked_edit(STORED, shown_line, shown_line + "  # both keys")

    assert str(refused.value) == security.HIDDEN_VALUE_KEPT


def test_a_file_whose_view_cannot_be_mapped_says_so_plainly(monkeypatch):
    """🔴 Red before: it said the stored copy no longer lined up, as if it had changed."""
    monkeypatch.setattr(security, "_mask_segments", lambda _shown, _stored: None)

    for attempt in (
        lambda: security.masked_edit(STORED, 'name = "old"', 'name = "new"'),
        lambda: security.keep_masked_lines('name = "new"\n', STORED),
    ):
        with pytest.raises(MaskConflict) as refused:
            attempt()
        assert str(refused.value) == security.MASK_UNWALKABLE
        assert "no longer lines up" not in str(refused.value)
