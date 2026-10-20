"""Tests for the one usage store.

The exemption tests matter as much as the recording ones: lessons are deliberately
untracked, and a store that quietly accepted them would produce a number that looks
like evidence and means "how much did the user talk".
"""

import json

import pytest

from personalclaw.learning.usage import (
    KIND_EVENTS,
    TRACKED_KINDS,
    UsageRecord,
    UsageStore,
    promotion_ready,
)


@pytest.fixture
def store(tmp_path):
    s = UsageStore(tmp_path)
    yield s
    s.close()


# ── per-entity semantics ──


def test_lessons_are_exempt_by_design():
    """Their "surfaced" count degenerates to session count — a number that measures
    how much the user talks, not whether the lesson is useful."""
    assert "lesson" not in TRACKED_KINDS


def test_recording_a_lesson_is_a_no_op_not_an_error(store):
    """A policy that crashes callers gets worked around."""
    assert store.record(kind="lesson", entity="l1", event="surfaced") is False
    assert store.get("lesson", "l1") is None


def test_an_event_a_kind_does_not_define_is_refused(store):
    assert store.record(kind="skill", entity="s1", event="run") is False
    assert store.record(kind="skill", entity="s1", event="loaded") is True


def test_each_kind_defines_its_own_events():
    assert "loaded" in KIND_EVENTS["skill"]
    assert "run_failure" in KIND_EVENTS["template"]
    assert "run" not in KIND_EVENTS["skill"]


def test_an_empty_entity_is_refused(store):
    assert store.record(kind="skill", entity="", event="loaded") is False


# ── flush cadence and damping ──


def test_events_are_buffered_until_flushed(store):
    store.record(kind="skill", entity="s1", event="loaded")
    assert store.get("skill", "s1") is None
    assert store.flush() == 1
    assert store.get("skill", "s1") is not None


def test_a_retrieval_burst_collapses_and_is_damped(store):
    """Ten retrievals in one turn is one act of attention. Un-damped, the inflated
    heat distorts every comparison against it — and heat drives eviction."""
    for _ in range(11):
        store.record(kind="skill", entity="s1", event="loaded", context="repo-a")
    assert store.flush() == 1  # one row, not eleven
    record = store.get("skill", "s1")
    assert 1 < record.used < 11  # damped, not discarded


def test_immediate_writes_a_one_off_event(store):
    """A run outcome has no burst to collapse, and losing it to an un-flushed
    buffer would lose real information."""
    store.record(kind="template", entity="t1", event="run_success", immediate=True)
    assert store.get("template", "t1").successes == 1


def test_flushing_nothing_is_zero(store):
    assert store.flush() == 0


def test_flushing_twice_does_not_double_count(store):
    store.record(kind="skill", entity="s1", event="loaded")
    store.flush()
    before = store.get("skill", "s1").used
    store.flush()
    assert store.get("skill", "s1").used == before


# ── outcome semantics ──


def test_success_rate_distinguishes_never_ran_from_always_failed(store):
    """0.0 means "ran and always failed"; None means "never ran". Collapsing them
    makes an unused template look broken and archives it for the wrong reason."""
    store.record(kind="template", entity="never", event="surfaced", immediate=True)
    assert store.get("template", "never").success_rate is None

    store.record(kind="template", entity="broken", event="run_failure", immediate=True)
    assert store.get("template", "broken").success_rate == 0.0


def test_success_rate_is_the_ratio(store):
    for _ in range(3):
        store.record(kind="template", entity="t1", event="run_success", immediate=True)
    store.record(kind="template", entity="t1", event="run_failure", immediate=True)
    assert store.get("template", "t1").success_rate == pytest.approx(0.75)


def test_surfaced_and_used_are_separate_counters(store):
    store.record(kind="skill", entity="s1", event="surfaced")
    store.record(kind="skill", entity="s1", event="loaded")
    store.flush()
    record = store.get("skill", "s1")
    assert record.surfaced >= 1 and record.used >= 1


def test_timestamps_track_the_right_event(store):
    store.record(kind="skill", entity="s1", event="surfaced", immediate=True)
    surfaced_only = store.get("skill", "s1")
    assert surfaced_only.last_surfaced_at and not surfaced_only.last_used_at

    store.record(kind="skill", entity="s1", event="loaded", immediate=True)
    assert store.get("skill", "s1").last_used_at


# ── context diversity ──


def test_contexts_accumulate_and_deduplicate(store):
    store.record(kind="template", entity="t1", event="run", context="repo-a")
    store.record(kind="template", entity="t1", event="run", context="repo-b")
    store.record(kind="template", entity="t1", event="run", context="repo-a")
    store.flush()
    record = store.get("template", "t1")
    assert set(record.contexts) == {"repo-a", "repo-b"}
    assert record.context_diversity == 2


def test_contexts_survive_across_flushes(store):
    store.record(kind="template", entity="t1", event="run", context="repo-a")
    store.flush()
    store.record(kind="template", entity="t1", event="run", context="repo-b")
    store.flush()
    assert store.get("template", "t1").context_diversity == 2


# ── curator flags ──


def test_flags_can_be_set_on_a_never_used_entity(store):
    """A user-authored, never-surfaced item has no usage row — without this it would
    inherit the agent default and become eligible for aging."""
    store.set_flags("skill", "mine", source_type="user", pinned=True)
    record = store.get("skill", "mine")
    assert record.source_type == "user" and record.pinned is True


def test_flags_do_not_reset_counters(store):
    store.record(kind="skill", entity="s1", event="loaded", immediate=True)
    used = store.get("skill", "s1").used
    store.set_flags("skill", "s1", pinned=True)
    assert store.get("skill", "s1").used == used


def test_the_default_source_type_is_agent(store):
    store.record(kind="skill", entity="s1", event="loaded", immediate=True)
    assert store.get("skill", "s1").source_type == "agent"


# ── listing and the active-days clock ──


def test_listing_is_scoped_by_kind(store):
    store.record(kind="skill", entity="s1", event="loaded")
    store.record(kind="template", entity="t1", event="run")
    store.flush()
    assert [r.entity for r in store.list_kind("skill")] == ["s1"]
    assert [r.entity for r in store.list_kind("template")] == ["t1"]


def test_a_flush_marks_the_day_active(store):
    store.record(kind="skill", entity="s1", event="loaded")
    store.flush()
    assert len(store.active_days()) == 1


def test_marking_active_is_idempotent_per_day(store):
    store.mark_active("2026-07-01")
    store.mark_active("2026-07-01")
    assert store.active_days() == ["2026-07-01"]


# ── legacy sidecar import ──


def test_the_legacy_sidecar_imports(store, tmp_path):
    sidecar = tmp_path / ".usage.json"
    sidecar.write_text(
        json.dumps({"auto/foo": {"count": 7, "last_used_at": "2026-06-01T00:00:00Z"}})
    )
    assert store.import_skill_sidecar(sidecar) == 1
    assert store.get("skill", "auto/foo").used == 7


def test_importing_twice_cannot_double_a_counter(store, tmp_path):
    """An idempotent backfill, not a migration file — so running it twice is safe."""
    sidecar = tmp_path / ".usage.json"
    sidecar.write_text(json.dumps({"auto/foo": {"count": 7}}))
    store.import_skill_sidecar(sidecar)
    store.import_skill_sidecar(sidecar)
    assert store.get("skill", "auto/foo").used == 7


def test_the_sidecar_is_left_on_disk(store, tmp_path):
    """Deleting the old source before the new one is verified in real use trades a
    recoverable state for an unrecoverable one."""
    sidecar = tmp_path / ".usage.json"
    sidecar.write_text(json.dumps({"auto/foo": {"count": 1}}))
    store.import_skill_sidecar(sidecar)
    assert sidecar.is_file()


def test_a_corrupt_sidecar_imports_nothing_rather_than_raising(store, tmp_path):
    bad = tmp_path / ".usage.json"
    bad.write_text("{not json")
    assert store.import_skill_sidecar(bad) == 0
    assert store.import_skill_sidecar(tmp_path / "missing.json") == 0


def test_a_sidecar_holding_the_wrong_shape_is_ignored(store, tmp_path):
    weird = tmp_path / ".usage.json"
    weird.write_text(json.dumps(["not", "a", "dict"]))
    assert store.import_skill_sidecar(weird) == 0


# ── multi-gate promotion ──


def _record(**kw) -> UsageRecord:
    return UsageRecord(kind="template", entity="x", **kw)


def test_promotion_needs_enough_uses():
    ok, why = promotion_ready(_record(used=1, contexts=["a", "b"]), active_days_idle=1)
    assert not ok and "use" in why


def test_promotion_needs_context_diversity():
    """Usage alone measures one busy afternoon; diversity is what distinguishes
    "genuinely general" from "used repeatedly in one place"."""
    ok, why = promotion_ready(_record(used=9, contexts=["a"]), active_days_idle=1)
    assert not ok and "context" in why


def test_promotion_needs_recency():
    ok, why = promotion_ready(_record(used=9, contexts=["a", "b"]), active_days_idle=400)
    assert not ok and "idle" in why


def test_a_failing_template_is_not_promoted():
    ok, why = promotion_ready(
        _record(used=9, contexts=["a", "b"], successes=1, failures=8), active_days_idle=1
    )
    assert not ok and "success" in why


def test_all_gates_met_yields_a_suggestion():
    ok, why = promotion_ready(_record(used=5, contexts=["a", "b"], successes=5), active_days_idle=2)
    assert ok and "multi-gate" in why


def test_a_never_run_entity_is_not_blocked_by_its_missing_success_rate():
    """`None` must not be compared as if it were 0.0 — that would make every
    never-run-but-loaded skill unpromotable."""
    ok, _ = promotion_ready(_record(used=5, contexts=["a", "b"]), active_days_idle=1)
    assert ok


# ── storage ──


def test_the_store_shares_learning_db_with_staging(tmp_path):
    store = UsageStore(tmp_path)
    try:
        assert store.path.name == "learning.db"
    finally:
        store.close()


def test_records_survive_a_reopen(tmp_path):
    first = UsageStore(tmp_path)
    first.record(kind="skill", entity="s1", event="loaded", immediate=True)
    first.close()
    second = UsageStore(tmp_path)
    try:
        assert second.get("skill", "s1").used == 1
    finally:
        second.close()
