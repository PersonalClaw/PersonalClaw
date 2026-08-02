"""Tests for the hardened curator.

The guards are the point. An automated janitor's failure modes are not an automated
author's: it fails by deleting quietly and at scale, so the tests that matter most
are the ones proving it refuses, spares, and can be undone.
"""

from datetime import datetime, timedelta, timezone

import pytest

from personalclaw.learning import curator as C
from personalclaw.learning.curator import Candidate, MutationLog

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)
#: A user who worked every day for the last 120 — so active days == wall-clock days
#: and the decay curve is exercised rather than the vacation guard.
ACTIVE = [(NOW - timedelta(days=i)).date().isoformat() for i in range(121)]


def cand(entity: str, days_idle: float, **kw) -> Candidate:
    return Candidate(
        kind=kw.pop("kind", "skill"),
        entity=entity,
        last_used_at=(NOW - timedelta(days=days_idle)).isoformat(),
        created_at=(NOW - timedelta(days=days_idle + 10)).isoformat(),
        **kw,
    )


@pytest.fixture
def log(tmp_path):
    journal = MutationLog(tmp_path)
    yield journal
    journal.close()


# ── the aging ladder ──


def test_a_fresh_entity_is_left_alone(log):
    report = C.run_aging([cand("fresh", 1)], active_dates=ACTIVE, now=NOW, log=log)
    assert report.changed == 0


def test_an_aging_entity_goes_stale(log):
    report = C.run_aging([cand("aging", 45)], active_dates=ACTIVE, now=NOW, log=log)
    assert report.to_stale == ["aging"]


def test_a_cold_entity_is_archived_not_deleted(log):
    """Archive is the maximum destructive action."""
    report = C.run_aging([cand("cold", 110, kind="failure")], active_dates=ACTIVE, now=NOW, log=log)
    assert report.to_archived == ["cold"]


def test_the_ladder_and_the_curve_cannot_disagree():
    """`target_state` is a pure mapping from the kernel's verdict.

    The code this replaces had a day-threshold ladder and no curve at all, so
    "stale" and "decayed" were unrelated notions.
    """
    from personalclaw.learning.decay import evaluate

    healthy = evaluate(kind="skill", active_days_since_use=1)
    assert C.target_state(healthy, C.STATE_ACTIVE) == C.STATE_ACTIVE
    cold = evaluate(kind="failure", active_days_since_use=300)
    assert C.target_state(cold, C.STATE_ACTIVE) == C.STATE_ARCHIVED


def test_a_review_verdict_does_not_change_state():
    from personalclaw.learning.decay import evaluate

    review = evaluate(kind="skill", active_days_since_use=200, stability=0.8)
    assert review.review
    assert C.target_state(review, C.STATE_STALE) == C.STATE_STALE


# ── provenance scoping ──


def test_user_authored_entities_are_skipped(log):
    """The curator may age agent-created entities ONLY. Deleting what the user wrote
    is not curation, it is data loss with a tidy justification."""
    report = C.run_aging(
        [cand("mine", 900, kind="failure", source_type="user")],
        active_dates=ACTIVE,
        now=NOW,
        log=log,
    )
    assert report.skipped_user == ["mine"]
    assert report.to_archived == [] and report.to_stale == []


def test_pinned_entities_bypass_aging_entirely(log):
    report = C.run_aging(
        [cand("pinned", 900, kind="failure", pinned=True)], active_dates=ACTIVE, now=NOW, log=log
    )
    assert report.skipped_pinned == ["pinned"]
    assert report.changed == 0


# ── over-deletion refusal ──


def test_a_pass_cutting_most_of_the_library_is_refused():
    """The realistic cause of such a pass is a bug in the pass — a mis-parsed
    timestamp, an empty usage table read as "nothing is used"."""
    many = [cand(f"c{i}", 300, kind="failure") for i in range(10)]
    report = C.run_aging(many, active_dates=ACTIVE, now=NOW, dry_run=True)
    assert report.refused
    assert "refusing" in report.refused
    assert report.to_archived == []


def test_a_small_set_is_not_refused():
    """Cutting 1 of 2 is not a red flag; cutting 30 of 40 is."""
    few = [cand("a", 300, kind="failure"), cand("b", 300, kind="failure")]
    report = C.run_aging(few, active_dates=ACTIVE, now=NOW, dry_run=True)
    assert not report.refused
    assert set(report.to_archived) == {"a", "b"}


def test_refusal_counts_only_eligible_entities():
    """Pinned and user-authored entities are not candidates, so they must not pad
    the denominator into permitting a mass cut.

    Ten cold + twenty pinned: 10 of 10 eligible is refused. Counting the pinned rows
    would make it 10 of 30 — a third, under the fraction — and the mass cut would
    proceed.
    """
    entities = [cand(f"c{i}", 300, kind="failure") for i in range(10)]
    entities += [cand(f"p{i}", 300, kind="failure", pinned=True) for i in range(20)]
    report = C.run_aging(entities, active_dates=ACTIVE, now=NOW, dry_run=True, batch_size=40)
    assert report.refused
    assert "of 10" in report.refused  # the denominator excluded the pinned rows


def test_a_set_below_the_refusal_floor_is_allowed_to_be_cut_entirely():
    """The floor exists because a fraction of a tiny set means nothing — refusing
    "2 of 2" would make the curator unable to work on a small library at all."""
    few = [cand(f"c{i}", 300, kind="failure") for i in range(C.MIN_SET_FOR_REFUSAL - 1)]
    report = C.run_aging(few, active_dates=ACTIVE, now=NOW, dry_run=True, batch_size=40)
    assert not report.refused
    assert len(report.to_archived) == len(few)


def test_refusal_reports_what_it_would_have_done():
    many = [cand(f"c{i}", 300, kind="failure") for i in range(10)]
    report = C.run_aging(many, active_dates=ACTIVE, now=NOW, dry_run=True)
    assert "of" in report.refused
    assert "REFUSED" in report.summary()


# ── bounded batches ──


def test_the_batch_is_bounded(log):
    """An unbounded tick is a latency spike attached to whatever cadence hosts it,
    and janitorial work is never urgent enough to justify one."""
    lots = [cand(f"e{i}", 1) for i in range(50)]
    report = C.run_aging(lots, active_dates=ACTIVE, now=NOW, dry_run=True, batch_size=8)
    assert report.scanned == 8


def test_oldest_audited_is_examined_first():
    """So a bounded batch covers the whole library over successive ticks instead of
    re-examining the same head every time."""
    entities = [
        Candidate(kind="skill", entity="recent", audited_at="2026-07-30"),
        Candidate(kind="skill", entity="ancient", audited_at="2026-01-01"),
        Candidate(kind="skill", entity="never", audited_at=""),
    ]
    report = C.run_aging(entities, active_dates=ACTIVE, now=NOW, dry_run=True, batch_size=1)
    assert report.scanned == 1
    # "never audited" sorts first — it is the one with no evidence at all.
    assert report.skipped_pinned == [] and report.skipped_user == []


def test_the_batch_size_floor_is_one():
    report = C.run_aging([cand("a", 1)], active_dates=ACTIVE, now=NOW, dry_run=True, batch_size=0)
    assert report.scanned == 1


# ── mode scoping ──


def test_a_sweep_can_be_scoped_to_one_kind():
    mixed = [cand("s1", 45), cand("t1", 45, kind="template")]
    report = C.run_aging(mixed, active_dates=ACTIVE, now=NOW, dry_run=True, mode="template")
    assert report.scanned == 1
    assert report.to_stale == ["t1"]


def test_an_unscoped_sweep_covers_every_kind():
    mixed = [cand("s1", 45), cand("t1", 45, kind="template")]
    report = C.run_aging(mixed, active_dates=ACTIVE, now=NOW, dry_run=True)
    assert report.scanned == 2


# ── the undo journal ──


def test_every_mutation_is_journaled_with_before_and_after(log):
    """Undo is what makes an automated janitor acceptable. Without it, every
    heuristic has to be right first time on data the user cannot get back."""
    C.run_aging([cand("aging", 45)], active_dates=ACTIVE, now=NOW, log=log)
    pending = log.pending_undo()
    assert len(pending) == 1
    _, mutation = pending[0]
    assert mutation.operation == "age"
    assert mutation.before["state"] == C.STATE_ACTIVE
    assert mutation.after["state"] == C.STATE_STALE


def test_an_archival_journals_its_evidence(log):
    """Measured regression: every archival journaled `strength: None`.

    `DecayVerdict.__bool__` is False for a pruned entity, so `if verdict` asked "is
    this healthy?" when the question was "did I get a verdict?" — losing the evidence
    for exactly the mutations most likely to need undoing.
    """
    C.run_aging([cand("cold", 200, kind="failure")], active_dates=ACTIVE, now=NOW, log=log)
    _, mutation = log.pending_undo()[0]
    assert mutation.after["strength"] is not None
    assert mutation.after["strength"] < 0.2
    assert mutation.after["reason"] == "low_strength_low_importance"


def test_a_dry_run_journals_nothing(log):
    C.run_aging([cand("aging", 45)], active_dates=ACTIVE, now=NOW, dry_run=True, log=log)
    assert log.pending_undo() == []


def test_a_mutation_can_be_marked_undone_once(log):
    C.run_aging([cand("aging", 45)], active_dates=ACTIVE, now=NOW, log=log)
    mutation_id = log.pending_undo()[0][0]
    assert log.mark_undone(mutation_id) is True
    assert log.mark_undone(mutation_id) is False  # idempotent
    assert log.pending_undo() == []


def test_the_changelog_keeps_undone_entries(log):
    """Append-only dated changelog semantics: an undo is history, not an erasure."""
    C.run_aging([cand("aging", 45)], active_dates=ACTIVE, now=NOW, log=log)
    log.mark_undone(log.pending_undo()[0][0])
    changelog = log.changelog()
    assert len(changelog) == 1
    assert changelog[0]["undone_at"]


def test_the_journal_survives_a_reopen(tmp_path):
    first = MutationLog(tmp_path)
    C.run_aging([cand("aging", 45)], active_dates=ACTIVE, now=NOW, log=first)
    first.close()
    second = MutationLog(tmp_path)
    try:
        assert len(second.changelog()) == 1
    finally:
        second.close()


# ── the vacation guard ──


def test_nothing_ages_across_days_the_user_was_absent():
    """Wall-clock decay punishes a single user for taking a holiday: come back after
    three weeks and the library has gone stale with no decision made about it."""
    report = C.run_aging(
        [cand("holiday", 200, kind="failure")], active_dates=[], now=NOW, dry_run=True
    )
    assert report.to_archived == [] and report.to_stale == []


# ── review proposals ──


def test_decayed_but_stable_is_reported_for_review(log):
    report = C.run_aging(
        [cand("confident", 200, stability=0.85)], active_dates=ACTIVE, now=NOW, log=log
    )
    assert report.review_proposals == ["confident"]
    assert report.to_archived == []  # NOT silently archived


def test_review_findings_are_filed_through_the_shared_queue(tmp_path, monkeypatch):
    """Routed through the queue rather than acted on: "confident about something
    nobody uses" is a user decision, not a curator one."""
    from personalclaw.learning import proposals as P

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_audit", lambda op, prop, outcome: None)

    report = C.CuratorReport(review_proposals=["auto/thing"])
    assert C.file_review_proposals(report) == 1
    pending = P.list_pending(kind=P.Kind.RETIREMENT.value)
    assert len(pending) == 1
    assert pending[0].target == "auto/thing"


def test_a_dry_run_files_no_proposals():
    report = C.CuratorReport(review_proposals=["auto/thing"])
    assert C.file_review_proposals(report, dry_run=True) == 0


def test_no_findings_files_nothing():
    assert C.file_review_proposals(C.CuratorReport()) == 0


# ── the optimizer battery ──


def test_a_large_entity_is_flagged_for_compression():
    detections = C.detect([cand("big", 1)], sizes={"big": 1200})
    assert len(detections) == 1
    assert detections[0].detector == "compress_summary"


def test_saving_estimates_make_findings_comparable():
    """That is the point of the battery: a compression finding and an archival
    finding become orderable by what they actually buy."""
    detections = C.detect([cand("big", 1)], sizes={"big": 1000})
    assert detections[0].estimated_token_saving > 0


def test_a_small_entity_is_not_flagged():
    assert C.detect([cand("small", 1)], sizes={"small": 100}) == []


def test_detection_changes_nothing():
    """Pure: returns findings, mutates no state."""
    candidates = [cand("big", 1)]
    C.detect(candidates, sizes={"big": 5000})
    assert candidates[0].state == C.STATE_ACTIVE


# ── the report ──


def test_the_summary_names_what_happened(log):
    report = C.run_aging(
        [cand("aging", 45), cand("cold", 300, kind="failure")],
        active_dates=ACTIVE,
        now=NOW,
        log=log,
    )
    summary = report.summary()
    assert "scanned 2" in summary
    assert "stale" in summary and "archived" in summary


def test_a_dry_run_says_so():
    report = C.run_aging([cand("aging", 45)], active_dates=ACTIVE, now=NOW, dry_run=True)
    assert "dry run" in report.summary()


def test_an_empty_pass_is_valid():
    report = C.run_aging([], active_dates=ACTIVE, now=NOW, dry_run=True)
    assert report.scanned == 0 and not report.refused
