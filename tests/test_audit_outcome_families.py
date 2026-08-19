"""The audit surface's outcome filters, checked against the vocabulary writers actually emit.

Settings → Audit log offers outcome filter pills. They used to be defined in the dashboard as
two literal substrings — ``denied`` and ``failed`` — against a log whose writers emit **62**
distinct outcome words. Measured across ``src/personalclaw``:

    denied 163 · rejected 24 · blocked 5 · refused 1        "Denied" matched 163 of 193
    failure 23 · error 21 · failed 4                        "Failed" matched 4 of 48

and confirmed on a live instance before the fix: a real ``DELETE /api/terminal/sessions/…``
recorded ``outcome=error`` was invisible to the Failed pill (``outcome=failed`` → 0 rows,
``outcome=error`` → 1 row). **On an audit surface a filter that silently omits matching records
is the worst available failure**: the operator reads the empty list as "nothing happened". The
handler already refuses an unknown filter KEY for exactly this reason; the VALUES had no such
guard.

So the families moved next to the log (``sel.AUDIT_OUTCOME_FAMILIES``) and the endpoint ships
them to the panel. These tests are the two invariants that keep them honest, in both directions:

1. no family offers a term no writer emits (a pill that can only ever return zero), and
2. the matcher really is any-of within a field, and still AND across fields.
"""

import re
from pathlib import Path

import pytest

from personalclaw.sel import (
    AUDIT_OUTCOME_FAMILIES,
    AUDIT_OUTCOME_SUCCESS,
    _audit_matches,
)

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
_LITERAL = re.compile(r'outcome="([a-z_]+)"')


def _emitted_outcomes() -> dict[str, int]:
    """Every ``outcome="…"`` literal in the tree, with how many writers use it."""
    counts: dict[str, int] = {}
    for path in _SRC.rglob("*.py"):
        for value in _LITERAL.findall(path.read_text(encoding="utf-8")):
            counts[value] = counts.get(value, 0) + 1
    return counts


def test_the_vocabulary_scan_resolves() -> None:
    """Vacuity floor. Every assertion below is 'for each emitted outcome …', so a scan that
    finds nothing passes everything — the failure mode this whole file exists to prevent."""
    emitted = _emitted_outcomes()
    assert len(emitted) >= 50, f"only {len(emitted)} outcome literals found; the scan broke"
    # The four that motivated the fix must be in it, or the regex has drifted.
    for value in ("denied", "rejected", "failure", "error"):
        assert emitted.get(value, 0) > 0, value


@pytest.mark.parametrize("family", AUDIT_OUTCOME_FAMILIES, ids=lambda f: str(f["key"]))
def test_no_family_offers_a_term_nobody_writes(family: dict) -> None:
    """A filter value that matches no emitted outcome can only ever return zero rows — the
    same silent-zero defect as a missing value, arriving from the other direction. (This test
    caught two invented values, ``not_permitted`` and ``timeout``, before they shipped.)"""
    emitted = _emitted_outcomes()
    for value in family["values"]:  # type: ignore[index]
        matching = [word for word in emitted if value in word]
        assert matching, f"{family['key']}: no writer emits anything containing {value!r}"


def test_the_families_cover_what_motivated_them() -> None:
    """The specific words the old two-substring pills missed."""
    denied = next(f for f in AUDIT_OUTCOME_FAMILIES if f["key"] == "denied")
    failed = next(f for f in AUDIT_OUTCOME_FAMILIES if f["key"] == "failed")

    def covered(family: dict, word: str) -> bool:
        return any(value in word for value in family["values"])  # type: ignore[index]

    for word in ("denied", "rejected", "blocked", "refused"):
        assert covered(denied, word), word
    for word in ("failure", "failed", "error"):
        assert covered(failed, word), word
    # And the prefixed variants come along, which is why substring matching is kept.
    for word in ("denied_running", "denied_mismatch", "rejected_spawn", "refused_incident"):
        assert covered(denied, word), word
    assert covered(failed, "hook_error")


def test_the_families_are_disjoint_and_exclude_success() -> None:
    """A word in two families makes the pills lie about each other; a success word in a failure
    family makes the audit log accuse a working operation."""
    seen: dict[str, str] = {}
    for family in AUDIT_OUTCOME_FAMILIES:
        for value in family["values"]:  # type: ignore[index]
            assert value not in seen, f"{value!r} is in both {seen.get(value)} and {family['key']}"
            seen[value] = str(family["key"])
    for good in AUDIT_OUTCOME_SUCCESS:
        for family in AUDIT_OUTCOME_FAMILIES:
            assert not any(v in good for v in family["values"]), (  # type: ignore[index]
                f"{good!r} would be filtered as {family['key']}"
            )


def test_the_unclassified_remainder_is_visible_not_silent() -> None:
    """A CEILING on the backlog, not a claim it is empty.

    Most of the 62 words are informational (``launched``, ``queued``, ``noop``, ``narrowed``)
    and belong in no filter. A handful are arguable — ``tampered``, ``too_large``, ``sigkill``,
    ``fanout_breaker_tripped`` — and classifying them is a judgement per word, not a sweep;
    getting it wrong on a security surface is worse than leaving a pill narrow. This records
    the size of that backlog so a NEW unclassified word is a decision someone makes, rather
    than a silent addition to a set nobody reads.
    """
    emitted = _emitted_outcomes()
    classified = {
        word
        for word in emitted
        if any(
            v in word for f in AUDIT_OUTCOME_FAMILIES for v in f["values"]  # type: ignore[index]
        )
        or any(good in word for good in AUDIT_OUTCOME_SUCCESS)
    }
    unclassified = sorted(set(emitted) - classified)
    assert len(unclassified) <= 32, (
        "a new outcome word appeared — classify it into a family, into "
        f"AUDIT_OUTCOME_SUCCESS, or raise this ceiling deliberately:\n{unclassified}"
    )


def test_a_family_is_one_any_of_query() -> None:
    """The comma form is what lets a family stay a single SERVER-side query, so the pill and
    the pagination cursor cannot disagree."""
    family = next(f for f in AUDIT_OUTCOME_FAMILIES if f["key"] == "failed")
    failed = ",".join(str(v) for v in family["values"])  # type: ignore[union-attr]
    assert _audit_matches({"outcome": "error"}, {"outcome": failed}, "", "")
    assert _audit_matches({"outcome": "failure"}, {"outcome": failed}, "", "")
    assert _audit_matches({"outcome": "hook_error"}, {"outcome": failed}, "", "")
    assert not _audit_matches({"outcome": "success"}, {"outcome": failed}, "", "")
    # The single-value form must keep behaving exactly as before.
    assert _audit_matches({"outcome": "denied_running"}, {"outcome": "denied"}, "", "")
    assert not _audit_matches({"outcome": "error"}, {"outcome": "failed"}, "", "")


def test_or_is_within_a_field_and_and_is_across_fields() -> None:
    """The regression this change could have introduced: turning the field AND into an OR would
    widen every audit query silently."""
    row = {"outcome": "error", "operation": "DELETE /api/terminal/sessions/abc"}
    assert _audit_matches(row, {"outcome": "failure,error", "operation": "DELETE"}, "", "")
    assert not _audit_matches(row, {"outcome": "failure,error", "operation": "POST"}, "", "")
    assert not _audit_matches(row, {"outcome": "denied,rejected", "operation": "DELETE"}, "", "")
    # An empty or comma-only needle must not become "match everything but claim a filter".
    assert _audit_matches(row, {"outcome": ""}, "", "")
    assert _audit_matches(row, {"outcome": " , "}, "", "")
