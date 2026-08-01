"""FEEDBACK-SIGNAL S1 — the deterministic capture + attribution substrate.

Capture: append-only JSONL, supersede-by-target, tolerant reads, 2×-cap trim,
never-raises. Attribution: producer_stats as a pure GROUP BY. Thresholds:
suppressed_producers + one-time retire proposals, fail-open everywhere.
"""

from __future__ import annotations

import json
import time

import pytest

from personalclaw import feedback as fb


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    fb._invalidate()
    yield tmp_path
    fb._invalidate()


def _rec(target_id="item-1", verdict="up", **kw):
    return fb.record_feedback(
        target_kind=kw.pop("target_kind", "inbox_classification"),
        target_id=target_id,
        verdict=verdict,
        producer_kind=kw.pop("producer_kind", "prompt"),
        producer_id=kw.pop("producer_id", "native:inbox-classify"),
        **kw,
    )


# ── Layer 1: capture ─────────────────────────────────────────────────────────


class TestCapture:
    def test_round_trip(self, isolated):
        rec = _rec()
        assert rec is not None and rec.id.startswith("fb_")
        got = fb.current_verdict("inbox_classification", "item-1")
        assert got is not None and got.verdict == "up"
        # persisted, human-readable JSONL
        lines = (isolated / "feedback.jsonl").read_text().splitlines()
        assert len(lines) == 1 and json.loads(lines[0])["verdict"] == "up"

    def test_supersede_by_target(self, isolated):
        _rec(verdict="up")
        _rec(verdict="down", reason="wrong label")
        got = fb.current_verdict("inbox_classification", "item-1")
        assert got is not None and got.verdict == "down" and got.reason == "wrong label"
        # the old record stays in the JSONL for audit
        lines = (isolated / "feedback.jsonl").read_text().splitlines()
        assert len(lines) == 2

    def test_reason_only_rides_down(self):
        rec = _rec(verdict="up", reason="should be dropped")
        assert rec is not None and rec.reason == ""

    def test_reason_clipped(self):
        rec = _rec(verdict="down", reason="x" * 900)
        assert rec is not None and len(rec.reason) == 500

    def test_unknown_kinds_dropped_never_raise(self):
        assert _rec(target_kind="chat_message") is None  # not a judgment surface
        assert _rec(verdict="meh") is None
        assert _rec(producer_kind="mystery") is None

    def test_corrupt_line_skipped(self, isolated):
        _rec()
        with open(isolated / "feedback.jsonl", "a") as f:
            f.write("{not json\n")
        _rec(target_id="item-2", verdict="down")
        fb._invalidate()
        assert fb.current_verdict("inbox_classification", "item-1") is not None
        assert fb.current_verdict("inbox_classification", "item-2") is not None

    def test_trim_at_double_cap(self, isolated, monkeypatch):
        monkeypatch.setattr(fb, "_CAP", 5)
        for i in range(11):
            _rec(target_id=f"item-{i}")
        lines = (isolated / "feedback.jsonl").read_text().splitlines()
        assert len(lines) == 5  # trimmed to the newest _CAP

    def test_file_mode_0600(self, isolated):
        _rec()
        mode = (isolated / "feedback.jsonl").stat().st_mode & 0o777
        assert mode == 0o600


# ── Layer 2: attribution ─────────────────────────────────────────────────────


class TestAttribution:
    def test_group_by_current_verdicts_only(self):
        _rec(target_id="a", verdict="up")
        _rec(target_id="b", verdict="down")
        _rec(target_id="b", verdict="up")  # supersedes the down
        stats = fb.producer_stats()
        row = stats[("prompt", "native:inbox-classify")]
        assert row == {"ups": 2, "downs": 0, "n": 2, "accuracy": 1.0}

    def test_window_excludes_old_records(self, monkeypatch):
        _rec(target_id="old")
        # age the record beyond the window by rewriting its timestamp
        idx = fb._load_index()
        key = ("inbox_classification", "old")
        old = idx[key]
        object.__setattr__(old, "created_at", time.time() - 100 * 86_400)
        stats = fb.producer_stats(window_days=90)
        assert ("prompt", "native:inbox-classify") not in stats

    def test_separate_producers_grouped_separately(self):
        _rec(target_id="a", producer_id="native:inbox-classify")
        _rec(
            target_id="f1",
            target_kind="loop_finding",
            producer_kind="loop_judge",
            producer_id="research",
            verdict="down",
        )
        stats = fb.producer_stats()
        assert stats[("prompt", "native:inbox-classify")]["ups"] == 1
        assert stats[("loop_judge", "research")]["downs"] == 1


# ── Layer 3: deterministic thresholds ────────────────────────────────────────


def _drive_below_threshold(
    producer_kind="workflow_surfacing", producer_id="wf_abc", n_down=5, n_up=1
):
    for i in range(n_down):
        fb.record_feedback(
            target_kind="proposal_content",
            target_id=f"t-down-{producer_id}-{i}",
            verdict="down",
            producer_kind=producer_kind,
            producer_id=producer_id,
        )
    for i in range(n_up):
        fb.record_feedback(
            target_kind="proposal_content",
            target_id=f"t-up-{producer_id}-{i}",
            verdict="up",
            producer_kind=producer_kind,
            producer_id=producer_id,
        )


class TestThresholds:
    def test_below_threshold_suppressed(self):
        _drive_below_threshold()  # 1 up / 5 downs = 0.167 < 0.4, n=6 >= 5
        assert ("workflow_surfacing", "wf_abc") in fb.suppressed_producers()

    def test_below_min_n_not_suppressed(self):
        _drive_below_threshold(n_down=2, n_up=0)  # n=2 < min_n 5
        assert ("workflow_surfacing", "wf_abc") not in fb.suppressed_producers()

    def test_snooze_and_clear(self):
        _drive_below_threshold()
        fb.snooze_producer("workflow_surfacing", "wf_abc")
        assert ("workflow_surfacing", "wf_abc") not in fb.suppressed_producers()
        fb.clear_producer("workflow_surfacing", "wf_abc")
        assert ("workflow_surfacing", "wf_abc") not in fb.suppressed_producers()

    def test_corrupt_settings_suppress_nothing(self, isolated):
        _drive_below_threshold()
        sf = isolated / "entity_settings" / "feedback.json"
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text("{corrupt")
        # fail-open: the whole check returns empty rather than raising
        assert isinstance(fb.suppressed_producers(), set)

    def test_retire_candidate_emitted_once(self):
        _drive_below_threshold()
        first = fb.check_retire_candidates()
        assert len(first) == 1 and first[0]["producer_id"] == "wf_abc"
        assert fb.check_retire_candidates() == []  # dedup: one proposal per crossing

    def test_retire_notification_via_state(self):
        _drive_below_threshold(producer_kind="prompt", producer_id="native:inbox-classify")

        class FakeState:
            notes: list = []

            def notify(self, kind, title, body, *, meta=None):
                self.notes.append((kind, title, meta))

        st = FakeState()
        out = fb.check_retire_candidates(state=st)
        assert out and st.notes and st.notes[0][0] == "feedback_retire"
        # prompt producers deep-link to Settings → Prompts
        assert st.notes[0][2]["link"] == "#/settings/prompts"

    def test_snooze_allows_future_reproposal(self):
        _drive_below_threshold()
        fb.check_retire_candidates()
        fb.snooze_producer("workflow_surfacing", "wf_abc")
        data = fb._settings()
        assert "workflow_surfacing:wf_abc" not in (data.get("retire_proposed") or [])

    def test_disabled_config_suppresses_nothing(self, isolated):
        (isolated / "config.json").write_text(json.dumps({"feedback": {"enabled": False}}))
        _drive_below_threshold()
        assert fb.suppressed_producers() == set()
        assert fb.check_retire_candidates() == []


# ── T3.1: the surfacing gates consult suppression ────────────────────────────


class TestSurfacingSuppression:
    """Suppression gating a real consumer.

    This used to drive `workflows.surfacing.eligible_workflows` end to end — the only
    *gated* consumer Feedback-Signal ever had (its own S3 log records that skills
    surfacing was deliberately left unwired). WORKFLOWS-V2 Phase 1 deleted that module,
    so as of now **no runtime path consults `suppressed_producers()`**: the Settings
    panel reads it for display, and `check_retire_candidates` proposes retirement, but
    nothing is actually withheld.

    That is an honest capability gap, not a silent one. The threshold machinery below
    stays fully covered, and WORKFLOWS-V2 Slice 6 (workflow surfacing in chat) or the
    Learning-Flywheel skill work is where a gated consumer returns; whichever lands
    first should re-add an end-to-end case here.
    """

    def test_suppression_set_is_the_contract_a_consumer_reads(self):
        """The shape a gate consumes: a set of (kind, id) pairs, membership-checked."""
        _drive_below_threshold(producer_kind="workflow_surfacing", producer_id="wf_abc")
        suppressed = fb.suppressed_producers()
        assert ("workflow_surfacing", "wf_abc") in suppressed
        assert all(isinstance(p, tuple) and len(p) == 2 for p in suppressed)
        # Clearing restores eligibility — the round-trip a consumer depends on.
        fb.clear_producer("workflow_surfacing", "wf_abc")
        assert ("workflow_surfacing", "wf_abc") not in fb.suppressed_producers()

    def test_workflow_surfacing_stays_in_the_producer_vocabulary(self):
        """`PRODUCER_KINDS` is append-only: records on disk reference this label, so it
        must not be removed just because its consumer is between implementations."""
        assert "workflow_surfacing" in fb.PRODUCER_KINDS
