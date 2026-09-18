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
them to the panel. These tests are the invariants that keep them honest, in both directions:

1. no family offers a term no writer emits (a pill that can only ever return zero),
2. no family CAPTURES a word another family claims (a pill that lies about a row), and
3. the matcher really is any-of within a field, and still AND across fields.

**Issue #535, second pass.** The pills were still a view over 0.6% of the log: measured on a live
1,040-entry instance, ``ok`` alone was 1,021 rows (98.2%) and matched neither pill, so an operator
could narrow to what went wrong and never to what happened. The success vocabulary existed here —
as the tuple this file's coverage rail used — but was never OFFERED. It is now the ``ok`` family,
which is also why matching became token-bounded: measured over the tree's real vocabulary, a
SUBSTRING ``ok`` captures ``hook_blocked`` and ``hook_error``, and ``approved`` captures
``not_auto_approved``. A "Succeeded" pill returning a blocked hook is the same silent lie as a
"Failed" pill that misses one, so :func:`test_no_family_captures_another_familys_word` is the rail
for it — derived from the tree, not hand-listed.
"""

import re
from pathlib import Path

import pytest

from personalclaw.sel import (
    AUDIT_OUTCOME_FAMILIES,
    AUDIT_OUTCOME_SUCCESS,
    AUDIT_OUTCOME_TONES,
    _audit_matches,
    _outcome_token_match,
    audit_outcome_tone,
)

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
_LITERAL = re.compile(r'outcome="([a-z_]+)"')


def _family(key: str) -> dict:
    return next(f for f in AUDIT_OUTCOME_FAMILIES if f["key"] == key)


def _values(family: dict) -> tuple[str, ...]:
    return tuple(str(v) for v in family["values"])


def _claims(family: dict, word: str) -> bool:
    """Whether ``family`` matches ``word`` under the SERVER's matcher — not a local re-derivation.
    A test that re-implements the matcher proves only that the test agrees with itself."""
    return any(_outcome_token_match(v, word) for v in _values(family))


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
    for value in family["values"]:
        matching = [word for word in emitted if _outcome_token_match(str(value), word)]
        assert matching, f"{family['key']}: no writer emits anything matching {value!r}"


def test_the_families_cover_what_motivated_them() -> None:
    """The specific words the old two-substring pills missed."""
    denied, failed = _family("denied"), _family("failed")

    for word in ("denied", "rejected", "blocked", "refused"):
        assert _claims(denied, word), word
    for word in ("failure", "failed", "error"):
        assert _claims(failed, word), word
    # And the prefixed variants come along, which is why matching is on token boundaries and
    # not on the whole word.
    for word in ("denied_running", "denied_mismatch", "rejected_spawn", "refused_incident"):
        assert _claims(denied, word), word
    assert _claims(failed, "hook_error")


def test_the_success_family_can_select_the_bulk_of_the_log() -> None:
    """Issue #535's residue: the pills covered 0.6%.

    ``ok`` is 98.2% of a live log's rows and matched NEITHER of the two failure pills, so the
    filter row could narrow to what went wrong and never to what happened. The words were already
    classified — they were the tuple this file's coverage rail used — and simply were not offered.
    """
    ok = _family("ok")
    assert (
        AUDIT_OUTCOME_SUCCESS
    ), "the success vocabulary is derived from this family, not beside it"
    assert _values(ok) == AUDIT_OUTCOME_SUCCESS, "one list, or the two will drift"
    for word in ("ok", "success", "completed", "allowed", "approved", "granted", "auto_approved"):
        assert _claims(ok, word), word


@pytest.mark.parametrize("family", AUDIT_OUTCOME_FAMILIES, ids=lambda f: str(f["key"]))
def test_no_family_captures_another_familys_word(family: dict) -> None:
    """THE rail for the collision the success family would have introduced.

    Derived from the tree's real vocabulary rather than a hand-checked list, because the whole
    defect class here is a hand-maintained view over a vocabulary that moved. Measured: a
    SUBSTRING match puts ``hook_blocked`` (denied) and ``hook_error`` (failed) into the ``ok``
    family as well, so a "Succeeded" pill would have returned a blocked hook and a hook error —
    an audit surface asserting success for a record another pill calls a refusal.
    """
    emitted = _emitted_outcomes()
    others = [f for f in AUDIT_OUTCOME_FAMILIES if f["key"] != family["key"]]
    for word in emitted:
        if not _claims(family, word):
            continue
        clashing = [str(other["key"]) for other in others if _claims(other, word)]
        assert (
            not clashing
        ), f"{word!r} is claimed by {family['key']} AND {clashing} — one row, two verdicts"


def test_a_negation_is_not_captured_as_its_own_affirmative() -> None:
    """``not_auto_approved`` must not read as a success, and ``not_found`` must still read as a
    failure. The guard is a prefix rule, so it has to be checked in both directions or it just
    becomes a way to lose the ``not_*`` words that ARE classified."""
    assert audit_outcome_tone("not_auto_approved") == "neutral"
    assert not _claims(_family("ok"), "not_auto_approved")
    assert audit_outcome_tone("not_found") == "danger"
    assert _claims(_family("failed"), "not_found")


def test_every_family_declares_a_renderable_tone() -> None:
    """A tone the frontend does not map falls back to neutral, silently — the exact
    hand-maintained-list failure the tone moved here to end. ``sel`` checks this at import; this
    pins it as a rail so the check cannot be dropped."""
    for family in AUDIT_OUTCOME_FAMILIES:
        assert str(family["tone"]) in AUDIT_OUTCOME_TONES, family["key"]
    assert "neutral" in AUDIT_OUTCOME_TONES, "an unclassified word needs somewhere honest to land"


def test_the_tone_of_a_word_is_the_tone_of_the_family_that_claims_it() -> None:
    """The row colour and the pill are one decision. Measured over the whole emitted vocabulary,
    because the drift that motivated this was a single missing word (``not_found``)."""
    for word in _emitted_outcomes():
        owners = [f for f in AUDIT_OUTCOME_FAMILIES if _claims(f, word)]
        expected = str(owners[0]["tone"]) if owners else "neutral"
        assert audit_outcome_tone(word) == expected, word
    assert audit_outcome_tone("") == "neutral", "a blank outcome is not a verdict"


def test_the_families_are_disjoint() -> None:
    """A value in two families makes the pills lie about each other."""
    seen: dict[str, str] = {}
    for family in AUDIT_OUTCOME_FAMILIES:
        for value in family["values"]:
            assert value not in seen, f"{value!r} is in both {seen.get(value)} and {family['key']}"
            seen[value] = str(family["key"])


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
    unclassified = sorted(
        word for word in emitted if not any(_claims(f, word) for f in AUDIT_OUTCOME_FAMILIES)
    )
    # 34 — and every move into and out of this set is the point.
    #
    #   left:    `needs_confirm`, `needs_input`  -> classified into the new `needs_confirm` family
    #   arrived: `invoked`, `not_auto_approved`  -> they were never really classified
    #   arrived: `interrupted`                   -> WF2AUT-16 emitted it after this was written
    #
    # The first two arrivals are the substring bug, counted. Under the old `value in word` match,
    # `ok` claimed `invoked` and `approved` claimed `not_auto_approved` — so this ceiling read them
    # as accounted for while the pills would have called a negation a success. Token matching stops
    # claiming them, and they land here where an unclassified word belongs. A rail that counts a
    # false classification as coverage is measuring the wrong thing.
    #
    # `interrupted` (WF2AUT-16) is emitted by the boot sweep (`boot_orphan_terminalize`) and
    # describes the fate of the ORPHANED RUN, not the outcome of the sweep: the sweep did exactly
    # its job, nothing was denied to a caller, and no mechanism broke. Classifying it `failed`
    # would make the Failed pill accuse a working control every time the gateway restarts mid-run.
    # The subject run's own failure IS surfaced one layer over — the reaper's `ScheduleRun` carries
    # `status="timeout"`, which `SCHEDULE_STATUS_TO_OUTCOME` maps to `failed`.
    #
    # Everything here is unclassified on purpose. `halted_on_budget` (ES-6) is a gate that stopped
    # on its declared ceiling — the control WORKING: nothing was denied to a caller (so not
    # `denied`), nothing broke (so not `failed`), and the sweep is incomplete (so not a success).
    # It stays out for the same reason `expired` does: putting it in a family would make the audit
    # log assert a refusal or a fault that never happened.
    assert len(unclassified) <= 34, (
        "a new outcome word appeared — classify it into an AUDIT_OUTCOME_FAMILIES family, or "
        f"move this ceiling deliberately:\n{unclassified}"
    )


def test_a_family_is_one_any_of_query() -> None:
    """The comma form is what lets a family stay a single SERVER-side query, so the pill and
    the pagination cursor cannot disagree."""
    failed = ",".join(_values(_family("failed")))
    assert _audit_matches({"outcome": "error"}, {"outcome": failed}, "", "")
    assert _audit_matches({"outcome": "failure"}, {"outcome": failed}, "", "")
    assert _audit_matches({"outcome": "hook_error"}, {"outcome": failed}, "", "")
    assert not _audit_matches({"outcome": "success"}, {"outcome": failed}, "", "")
    # The single-value form must keep behaving exactly as before.
    assert _audit_matches({"outcome": "denied_running"}, {"outcome": "denied"}, "", "")
    assert not _audit_matches({"outcome": "error"}, {"outcome": "failed"}, "", "")

    ok = ",".join(_values(_family("ok")))
    assert _audit_matches({"outcome": "ok"}, {"outcome": ok}, "", "")
    assert _audit_matches({"outcome": "auto_approved_spawn"}, {"outcome": ok}, "", "")
    # The collision the success family would have shipped under a substring match.
    assert not _audit_matches({"outcome": "hook_error"}, {"outcome": ok}, "", "")
    assert not _audit_matches({"outcome": "hook_blocked"}, {"outcome": ok}, "", "")
    assert not _audit_matches({"outcome": "not_auto_approved"}, {"outcome": ok}, "", "")


def test_outcome_is_token_matched_but_free_text_fields_stay_substring() -> None:
    """The two fields are matched differently ON PURPOSE, and getting either backwards is a real
    defect: a substring over the closed outcome vocabulary mis-classifies (``ok`` in
    ``hook_error``), while token matching over free text would break the operation filter — you
    type ``terminal`` and expect ``DELETE /api/terminal/sessions/abc``."""
    row = {"outcome": "denied_running", "operation": "DELETE /api/terminal/sessions/abc"}
    assert _audit_matches(row, {"operation": "terminal"}, "", "")
    assert _audit_matches(row, {"operation": "erminal/sess"}, "", "")
    # ...and the outcome half does NOT do that: a partial word is not a member of the vocabulary.
    assert not _audit_matches(row, {"outcome": "enied"}, "", "")
    assert _audit_matches(row, {"outcome": "denied"}, "", "")


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
