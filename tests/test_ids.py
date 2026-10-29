"""Every id is minted in one place, ``personalclaw.ids``, in one shape (shared contracts §2.1).

An id is its prefix, an underscore and 26 lowercase Crockford base32 characters: 48 bits of
milliseconds, then 80 random bits that count up within one millisecond, so ids sort by creation.
A row read before it had an id gets a stable one from its time and a seed, the same each read.
``tests/rails/test_ids_have_one_minter.py`` holds the rest of the package to this one minter.
"""

from __future__ import annotations

import re

import pytest
from table_rows import row

from personalclaw import ids

SHAPE = re.compile(r"^msg_[0-9a-hjkmnp-tv-z]{26}$")
CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"


def _time_chars(ms: int) -> str:
    """The first ten characters of an id minted at *ms*: the 128 bits sit in 26 characters of five
    bits, so the top ten characters are the 48 bits of time under two zero bits."""
    return "".join(CROCKFORD[(ms >> (5 * (9 - i))) & 31] for i in range(10))


def test_an_id_has_its_prefix_and_26_crockford_characters() -> None:
    minted = ids.new_id("msg")
    assert SHAPE.fullmatch(minted), minted
    assert ids.is_id("msg", minted)
    assert not ids.is_id("p", minted), "an id is its own prefix's, never another's"


def test_ids_sort_by_creation_within_one_millisecond(monkeypatch) -> None:
    monkeypatch.setattr(ids, "_now_ms", lambda: 1_793_000_000_000)
    minted = [ids.new_id("e") for _ in range(1000)]
    assert len(set(minted)) == 1000
    assert minted == sorted(minted)
    assert {m[2:12] for m in minted} == {minted[0][2:12]}, "one millisecond, one time part"


def test_a_later_millisecond_sorts_after_an_earlier_one(monkeypatch) -> None:
    clock = iter([1_793_000_000_000, 1_793_000_000_001])
    monkeypatch.setattr(ids, "_now_ms", lambda: next(clock))
    first, second = ids.new_id("run"), ids.new_id("run")
    assert first < second


def test_a_stable_id_is_the_same_for_the_same_seed_and_instant() -> None:
    at = "2026-10-04T15:17:09.123000+00:00"
    once = ids.stable_id("msg", at, "chat-1:row-7")
    assert once == ids.stable_id("msg", at, "chat-1:row-7")
    assert ids.is_id("msg", once)
    assert once != ids.stable_id("msg", at, "chat-1:row-8"), "another seed, another id"
    assert once[4:14] == _time_chars(1_791_127_029_123), "its time part is the instant's"
    assert once < ids.stable_id("msg", "2026-10-04T15:17:09.124000+00:00", "chat-1:row-7")


def test_a_stable_id_refuses_a_time_with_no_zone() -> None:
    with pytest.raises(ValueError):
        ids.stable_id("msg", "2026-10-04T15:17:09", "seed")


def test_is_id_refuses_every_other_shape(subtests) -> None:
    good = ids.new_id("msg")
    body = good[len("msg_") :]
    shapes = [
        (f"p_{body}", "another prefix"),
        (f"msg_{body.upper()}", "upper case"),
        (good[:-1], "25 characters"),
        (good + "0", "27 characters"),
        (f"msg_{body[:-1]}i", "an i"),
        (f"msg_{body[:-1]}l", "an l"),
        (f"msg_{body[:-1]}o", "an o"),
        (f"msg_{body[:-1]}u", "a u"),
        (f"msg-{body}", "a hyphen for the underscore"),
        (body, "no prefix"),
    ]
    assert len(shapes) == 10, "a refused shape was dropped from the table"
    for value, why in shapes:
        with row(subtests, value=value, why=why):
            assert not ids.is_id("msg", value)


def test_an_unknown_prefix_is_refused(subtests) -> None:
    for call in (
        lambda: ids.new_id("chat"),
        lambda: ids.stable_id("chat", "2026-10-04T15:17:09+00:00", "seed"),
        lambda: ids.is_id("chat", "chat_00000000000000000000000000"),
        lambda: ids.new_id(""),
    ):
        with row(subtests, call=call), pytest.raises(ValueError):
            call()


def test_the_prefixes_are_the_shared_tables() -> None:
    """The literal table, so a prefix added or dropped is a change of this test too. The
    committed contracts document holds the table this reads; its own test compares the two."""
    assert ids.PREFIXES == frozenset(
        "msg p e cg rec inc note run dlg sia mv mvp boot blank ev "
        "ka wq job rule acc fill off srq addr bind snd shr wt rv fnd tw pv demo ma".split()
    )
