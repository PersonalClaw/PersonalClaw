"""Every session key the source mints names a kind ``session_keys`` classifies, read from its row.

Whether anybody watches a piece of work is decided by the kind of session key it runs under
(``personalclaw.session_keys``): the safety profile, the approval posture of a run nobody can be
asked in, the spend caps, the self-stop check and the autonomy ladder all read that answer. A key
whose kind has no row there matches nothing, and nothing reads as a chat someone is watching. So
a new kind of key minted without a row is new unattended work judged as watched, and a prefix
spelled at a mint site instead of read from its row is a kind that can drift from its own
classification.

This census reads the source tree for the places a string becomes a session key and fails on:

* a key minted under a prefix no row names, and
* a row's prefix spelled where a key is minted, instead of read from the row
  (``session_keys.TILE.key(…)``, ``session_keys.TILE.prefix``).

It follows a key built from a name to what the name holds: a variable of the function it is in,
a constant of its module, or a constant imported from another. A record of who did something (an
audit row, a usage row) labels the row and is judged by nothing, so its key is not a mint.

The readers that tell one kind of key from another for a route, a record, a prompt, an origin or
the idle sweep (:data:`READERS`) are held to the rows too: one that spells a kind's prefix where it
reads a key (``key.startswith("cron:")``, a tuple of prefixes it loops over) is a second answer to
which kind a key is, and it drifts: the reader that names a run's runtime to its model told a
workflow step's that it ran in a messaging channel while the audit log recorded a workflow's, and
a prefix outlived the kind it named.
"""

from __future__ import annotations

import pytest
from session_key_census_shared import (
    SRC,
    Census,
    _function_named,
    hand_typed_reads,
    reads_the_table,
    violations,
)

from personalclaw import session_keys
from personalclaw.guardrails.policy import is_unattended_session

PACKAGE = SRC / "personalclaw"


# ── the rail ──────────────────────────────────────────────────────────────────────────────


# ── the census refuses what it says it refuses ─────────────────────────────────────────────

_SNIPPET_UNCLASSIFIED = """
def start(x):
    session_key = f"newkind:{x}"
    return session_key
"""

_SNIPPET_SPELLED = """
TILE_PREFIX = "tile:"

def refresh(sessions, ref):
    return sessions.get_or_create(f"{TILE_PREFIX}{ref}")
"""

_SNIPPET_FROM_THE_ROW = """
from personalclaw import session_keys

def refresh(sessions, ref):
    key = session_keys.TILE.key(ref)
    return sessions.get_or_create(key)
"""

_SNIPPET_A_RECORD = """
def audit(sel, x):
    sel.log_tool_invocation(session_key=f"newkind:{x}", tool_name="t")
"""


def test_a_kind_nothing_classifies_is_refused():
    found = violations(Census({"personalclaw.example": _SNIPPET_UNCLASSIFIED}))
    assert found == [
        "personalclaw.example:3 mints session keys under 'newkind:', a kind session_keys has no "
        "row for: add one that says whether anybody watches that work"
    ]


def test_a_rows_prefix_spelled_at_a_mint_site_is_refused_through_its_constant():
    found = violations(Census({"personalclaw.example": _SNIPPET_SPELLED}))
    assert found == [
        "personalclaw.example:5 spells 'tile:' where it mints a session key: mint it through "
        "session_keys.TILE, so the kind cannot drift from its classification"
    ]


def test_a_key_minted_from_its_row_and_a_record_of_who_did_something_pass():
    census = Census(
        {"personalclaw.example": _SNIPPET_FROM_THE_ROW, "personalclaw.audit": _SNIPPET_A_RECORD}
    )
    assert violations(census) == []
    assert [m.literal for m in census.mints() if m.where.startswith("personalclaw.example")] == [
        None
    ]


# ── the readers that tell one kind of key from another ──────────────────────────────────────


_SNIPPET_A_READER_THAT_SPELLS = """
_ORIGINS = (("loop-", "loop"), ("legacy-", "legacy"))

def origin_of(name):
    for prefix, origin in _ORIGINS:
        if name.startswith(prefix):
            return origin
    if name.startswith(("cron:", "subagent:")):
        return name[len("cron:"):]
    return "manual"
"""

_SNIPPET_A_READER_OF_THE_ROWS = """
from personalclaw import session_keys

_ORIGINS = ((session_keys.LOOP, "loop"),)

def origin_of(name):
    kind = session_keys.kind_of(name)
    for row, origin in _ORIGINS:
        if row is kind and name.startswith(row.prefix):
            return origin
    return "manual"
"""


def test_a_reader_that_spells_a_prefix_is_refused_through_its_loop_its_tuple_and_its_cut():
    census = Census({"personalclaw.example": _SNIPPET_A_READER_THAT_SPELLS})
    module = census.modules["personalclaw.example"]
    reader = _function_named(module, "origin_of")
    assert reader is not None

    assert sorted(hand_typed_reads(census, module, reader)) == [
        "6: 'loop-'",
        "8: 'cron:'",
        "8: 'subagent:'",
        "9: 'cron:'",
    ]
    assert reads_the_table(census, module, reader) is False


def test_a_reader_of_the_rows_passes():
    census = Census({"personalclaw.example": _SNIPPET_A_READER_OF_THE_ROWS})
    module = census.modules["personalclaw.example"]
    reader = _function_named(module, "origin_of")
    assert reader is not None

    assert hand_typed_reads(census, module, reader) == []
    assert reads_the_table(census, module, reader) is True


# ── the table itself ──────────────────────────────────────────────────────────────────────


def test_each_kind_has_one_prefix_ending_in_its_separator_and_says_who_runs():
    prefixes = [kind.prefix for kind in session_keys.KINDS]
    assert len(prefixes) == len(set(prefixes))
    for kind in session_keys.KINDS:
        assert kind.prefix[-1] in ":-_", kind
        assert kind.who and kind.who[0].isupper(), kind
    rows = {v for v in vars(session_keys).values() if isinstance(v, session_keys.SessionKind)}
    assert rows == set(session_keys.KINDS), "a row defined but not in KINDS is never matched"


def test_a_kind_inside_another_is_classified_as_that_one_is():
    """A longer prefix wins the match (``kind_of``), so one nested in another must not flip the
    answer for part of its parent's keys by accident."""
    for inner in session_keys.KINDS:
        for outer in session_keys.KINDS:
            if inner is not outer and inner.prefix.startswith(outer.prefix):
                assert (inner.unattended, inner.stateless) == (outer.unattended, outer.stateless)


@pytest.mark.parametrize("kind", session_keys.KINDS, ids=lambda kind: kind.prefix)
def test_the_policy_reads_each_kind_as_its_row_says(kind):
    key = kind.key("x")
    assert session_keys.kind_of(key) is kind
    assert is_unattended_session(key) is kind.unattended
    assert session_keys.is_stateless(key) is kind.stateless
