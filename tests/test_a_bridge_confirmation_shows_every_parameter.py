"""A control-bridge confirmation carries every parameter it runs with, and no cut splits a mask.

A confirmation is answered in one press on its card, so the card has to show what the press runs.
The waiting list used to carry only the Inbox row's sentence, whose parameters were cut at 300
characters with no mark (``inbound/bridge.py``'s ``_confirmation_body``). Now the list carries the
parameters themselves:
- masked;
- in the producer's sorted key order;
- up to 4,000 characters;
- with whether they were cut and how long they were.

A cut never ends inside a ``[REDACTED: …]`` marker. A half marker reads as a value, and no restore
recognises it. ``textfmt.clip_words`` keeps the same rule, so every server-built line that is cut
keeps its masks whole.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from personalclaw import textfmt, waiting
from personalclaw.inbound import bridge

pytestmark = pytest.mark.security

MASK = "[REDACTED: credential]"


@pytest.fixture(autouse=True)
def _no_pending():
    bridge._pending.clear()
    yield
    bridge._pending.clear()


def _mint(params: dict) -> dict:
    bridge._mint_confirmation(SimpleNamespace(name="tasks.create"), params, asked_by="bridge:c1")
    [row] = bridge.pending_confirmations()
    return row


# ── the cut ───────────────────────────────────────────────────────────────────────────────────────


def test_a_cut_inside_a_marker_moves_to_the_markers_start():
    text = "key " + MASK + " tail"
    inside = len("key ") + 5
    assert textfmt.cut_outside_masks(text, inside) == len("key ")
    assert textfmt.cut_outside_masks(text, len("key ")) == len("key ")
    after = len("key " + MASK)
    assert textfmt.cut_outside_masks(text, after) == after
    assert textfmt.cut_outside_masks(text, len(text)) == len(text)


def test_clip_words_never_ends_inside_a_marker():
    # The cut falls inside the marker, and the marker's own space is the last word break before it.
    line = "Send the token " + MASK + " to the deploy step"
    for limit in range(len("Send the token ") + 2, len("Send the token " + MASK) + 1):
        out = textfmt.clip_words(line, limit)
        assert len(out) <= limit, (limit, out)
        # The trailing ":" of a split marker is stripped before the "…", so look for its bracket.
        opened = out.rfind("[REDACTED")
        assert opened == -1 or "]" in out[opened:], (limit, out)
    assert textfmt.clip_words(line, len("Send the token " + MASK) + 2).startswith(
        "Send the token " + MASK
    )


def test_clip_words_still_cuts_a_line_with_no_marker_at_a_word():
    assert textfmt.clip_words("Draft the Friday status email", 20) == "Draft the Friday…"


# ── the confirmation ──────────────────────────────────────────────────────────────────────────────


def test_the_parameters_are_carried_whole_masked_and_in_sorted_order():
    row = _mint(
        {
            "title": "Pay the rent",
            "due": "2026-11-15",
            "note": "API_KEY=fake-anthropic-test-" + "x" * 40,
        }
    )
    params = row["params"]
    assert params.startswith('{"due": "2026-11-15", "note": ')
    assert "fake-anthropic-test" not in params
    assert "[REDACTED:" in params
    assert row["params_cut"] is False
    assert row["params_chars"] == len(params)


def test_long_parameters_are_cut_at_4000_between_masks_and_say_so():
    filler = "a" * 3990
    row = _mint({"a": filler, "b": "API_KEY=fake-anthropic-test-" + "y" * 40, "c": "z" * 500})
    params = row["params"]
    assert row["params_cut"] is True
    assert len(params) <= 4000
    assert row["params_chars"] > 4000
    opened = params.rfind("[REDACTED")
    assert opened == -1 or "]" in params[opened:], "the cut split a marker"


def test_no_parameters_carry_an_empty_string():
    row = _mint({})
    assert row["params"] == ""
    assert row["params_cut"] is False
    assert row["params_chars"] == 0


def test_the_waiting_brief_carries_the_parameters():
    row = _mint({"title": "Pay the rent", "due": "2026-11-15"})
    [item] = waiting._bridge_confirmations([row], now=row["asked_at"])
    assert item["brief"]["params"] == json.dumps(
        {"due": "2026-11-15", "title": "Pay the rent"}, sort_keys=True, default=str
    )
    assert item["brief"]["params_cut"] is False
    assert item["brief"]["params_chars"] == len(item["brief"]["params"])
