"""§1.3's archive split: the runs inbox is for what the machine DID (S132).

§1.3 on `INERT_OUTCOMES`: *"These collapse to ledger rows and archive out of the default inbox
view —
the runs inbox is for what the machine DID."*

🔴 THE DEFECT. `INERT_OUTCOMES` was declared in `models.py` and read by **nothing** — the sixth
declared-but-unread table found in this stretch. Measured: `feed_response` returned every row
undifferentiated, so a minutely trigger held by quiet hours buries the one fire that mattered under
1439 `skipped_gate` rows, and the feed a user opens to answer *"what did my machine do"* answers
*"mostly nothing, 1440 times"*.

**A PARTITION, not a filter.** The suppressed rows are the answer to "why did my automation not
run",
so dropping them would replace one bad default with a worse one — §7 criterion 8 bans silent
drops, and
a row filtered out of the only surface that shows it is a silent drop with extra steps. `runs` still
carries every row; two id lists let a default view show work and fold the rest away.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers.history import feed_response, is_inert, outcome_counts, partition_inert
from personalclaw.triggers.models import INERT_OUTCOMES, FireRecord, Outcome


def _rec(rid: str, outcome: str) -> FireRecord:
    return FireRecord(id=rid, trigger_id="clock:x", outcome=outcome)


MIXED = [
    _rec("a", Outcome.RAN.value),
    _rec("b", Outcome.SKIPPED_GATE.value),
    _rec("c", Outcome.SKIPPED_OVERLAP.value),
    _rec("d", Outcome.FAILED.value),
    _rec("e", Outcome.REFUSED.value),
]


# ── the classification ──


@pytest.mark.parametrize("outcome", sorted(INERT_OUTCOMES))
def test_EVERY_declared_inert_outcome_is_classified(outcome):
    """🔴 The completeness half. A declared table is not a control until something reads it, so every
    entry is asserted rather than trusted — this is the check whose absence let the table drift."""
    assert is_inert(_rec("x", outcome)) is True


def test_RAN_is_not_inert():
    assert is_inert(_rec("x", Outcome.RAN.value)) is False
    assert is_inert(_rec("x", Outcome.RAN_LATE.value)) is False


def test_FAILED_is_not_inert():
    """A failure is the most important thing in the feed. Archiving it would hide exactly what the
    user opened the page to find."""
    assert is_inert(_rec("x", Outcome.FAILED.value)) is False


def test_REFUSED_is_not_inert():
    """🔴 A policy refusal is a DECISION the machine made — the kill switch, a capability fence, an
    unresolved secret. §1.3 groups it with neither the skips nor the successes, and it must stay
    visible: "your automation was refused" is not the same as "it was not due"."""
    assert is_inert(_rec("x", Outcome.REFUSED.value)) is False


def test_BLOCKED_INJECTION_is_not_inert():
    """An injection block is a security event. Folding it into the archive would bury the one row a
    user most needs to see."""
    assert is_inert(_rec("x", Outcome.BLOCKED_INJECTION.value)) is False


def test_DEFERRED_is_not_inert():
    """A deferred fire is work that WILL happen — parked, not skipped."""
    assert is_inert(_rec("x", Outcome.DEFERRED.value)) is False


# ── the partition ──


def test_the_partition_splits_work_from_suppression():
    did, suppressed = partition_inert(MIXED)
    assert [r.id for r in did] == ["a", "d", "e"]
    assert [r.id for r in suppressed] == ["b", "c"]


def test_NO_ROW_IS_LOST():
    """🔴 The property that makes this a partition rather than a filter. §7 criterion 8 bans silent
    drops, and a row dropped from the only surface that shows it is a silent drop with extra steps.
    """
    did, suppressed = partition_inert(MIXED)
    assert len(did) + len(suppressed) == len(MIXED)
    assert {r.id for r in did} | {r.id for r in suppressed} == {r.id for r in MIXED}


def test_the_ORDER_within_each_side_is_preserved():
    """The feed is chronological; a partition that reordered would make "the last thing that
    happened" wrong on both sides."""
    did, _ = partition_inert(MIXED + [_rec("f", Outcome.RAN.value)])
    assert [r.id for r in did] == ["a", "d", "e", "f"]


def test_an_EMPTY_feed_partitions_cleanly():
    assert partition_inert([]) == ([], [])


def test_an_ALL_SUPPRESSED_feed_reports_zero_work():
    """The exact case §1.3 describes: a minutely trigger held by quiet hours."""
    rows = [_rec(str(i), Outcome.SKIPPED_GATE.value) for i in range(20)]
    did, suppressed = partition_inert(rows)
    assert did == []
    assert len(suppressed) == 20


# ── the wire shape ──


def test_the_response_carries_BOTH_id_lists():
    payload = feed_response(MIXED)
    assert payload["did_ids"] == ["a", "d", "e"]
    assert payload["suppressed_ids"] == ["b", "c"]
    assert payload["suppressed"] == 2


def test_the_response_still_carries_EVERY_run():
    """A client that ignores the new keys must behave exactly as before — this is additive."""
    payload = feed_response(MIXED)
    assert len(payload["runs"]) == len(MIXED)
    assert [r["id"] for r in payload["runs"]] == ["a", "b", "c", "d", "e"]


def test_the_PRE_EXISTING_response_keys_are_unchanged():
    """`total`, `kinds` and `summaries` are consumed by the shipped UI. Adding a split must not move
    them."""
    payload = feed_response(MIXED)
    assert payload["total"] == 5
    assert payload["kinds"] == ["clock"]
    assert payload["summaries"] == 0


def test_an_explicit_TOTAL_still_wins():
    """Pagination: the caller's total is the store's, not the page's."""
    assert feed_response(MIXED, total=97)["total"] == 97


def test_outcome_counts_still_tallies_EVERY_outcome():
    """The tally is a different question from the split — it must still count suppressed rows, or a
    health rollup would under-report why a trigger is quiet."""
    counts = outcome_counts(MIXED)
    assert counts[Outcome.SKIPPED_GATE.value] == 1
    assert counts[Outcome.RAN.value] == 1
    assert sum(counts.values()) == len(MIXED)
