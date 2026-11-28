"""A cut of masked text reads its markers in one pass, and so does a card's marker strip.

``textfmt.cut_outside_masks`` keeps every ``[REDACTED: …]`` marker whole when a line, a preview or a
card's parameters are cut. It cuts where the markers ``security._MASK_RE`` finds would have it cut,
and it reads them in time linear in the text: a text of marker openings that never close, which a
pattern search tried from each opening reads to its end again and again, is cut as fast as any
other text its length. A learning card takes its producer's own markers (``[stumble: …]``) out the
same way: an opening's search ends at the line's end or the next opening.
"""

from __future__ import annotations

import random
import time

import pytest

from personalclaw import security, textfmt
from personalclaw.learning import cards

pytestmark = pytest.mark.security


def _by_pattern(text: str, end: int) -> int:
    """The cut as the markers' pattern places it: the start of the marker *end* falls inside."""
    for m in security._MASK_RE.finditer(text):
        if m.start() >= end:
            break
        if end < m.end():
            return m.start()
    return end


def test_it_cuts_where_the_markers_pattern_would_at_every_end():
    """Openings, closes and line breaks in every order, nested openings and a close on the next
    line among them, cut at each of their positions."""
    pieces = ["[REDACTED:", "[REDACTED: credential]", "]", "\n", "a", " ", "[", "REDACTED:"]
    rng = random.Random(6187)
    for _ in range(400):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 14)))
        for end in range(len(text) + 2):
            assert textfmt.cut_outside_masks(text, end) == _by_pattern(text, end), (text, end)


def test_an_end_inside_a_markers_opening_moves_to_its_start():
    text = "key [REDACTED: credential] tail"
    for end in range(len("key ") + 1, len("key [REDACTED: credential]")):
        assert textfmt.cut_outside_masks(text, end) == len("key ")


@pytest.mark.parametrize(
    "text",
    [
        "[REDACTED:" * 50_000,  # openings that never close
        "[REDACTED:" * 50_000 + "\n]",  # their line ends before its close
        "[REDACTED:\n" * 50_000 + "]",  # each on a line of its own
    ],
    ids=["never-closed", "closed-past-its-line", "a-line-each"],
)
def test_openings_that_never_close_cost_what_their_length_costs(text):
    started = time.process_time()
    assert textfmt.cut_outside_masks(text, len(text) // 2) == len(text) // 2
    assert time.process_time() - started < 0.05


def test_a_cards_markers_are_taken_out_and_read_once():
    assert "stumble" not in cards._plain("[stumble: deploy] fix the build")
    assert cards._plain("[skill-ladder rung 2] keep it") == " keep it"
    text = "[stumble" * 50_000  # openings that never close
    started = time.process_time()
    assert cards._MARKER_RE.sub("", text) == text
    assert time.process_time() - started < 0.05
