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
    """Suppression gating a real consumer (FS-6).

    Feedback-Signal's original gated consumer (`workflows.surfacing.eligible_workflows`)
    was deleted in WORKFLOWS-V2 Phase 1, leaving `suppressed_producers()` inert — read
    only by the Settings display and the retire-proposal path, withholding nothing. FS-6
    re-adds a LIVE consumer: turn-time **skill surfacing** (`SkillsLoader.get_surfaced_
    skills` → `skills.surfacing.surface_skills`), the one turn-time surfacing gate that
    actually runs (the workflow suggestion path is itself inert). A skill whose judgments
    persistently draw 👎 — identity `("skill_synthesis", <key>)` — falls below
    `retire_threshold` with `n >= min_n`, enters the suppressed set, and is withheld from
    surfacing until the user edits it (which clears it).
    """

    def test_suppression_set_is_the_contract_a_consumer_reads(self):
        """The shape a gate consumes: a set of (kind, id) pairs, membership-checked."""
        _drive_below_threshold(producer_kind="skill_synthesis", producer_id="auto/wrong-skill")
        suppressed = fb.suppressed_producers()
        assert ("skill_synthesis", "auto/wrong-skill") in suppressed
        assert all(isinstance(p, tuple) and len(p) == 2 for p in suppressed)
        # Clearing restores eligibility — the round-trip a consumer depends on.
        fb.clear_producer("skill_synthesis", "auto/wrong-skill")
        assert ("skill_synthesis", "auto/wrong-skill") not in fb.suppressed_producers()

    def test_workflow_surfacing_stays_in_the_producer_vocabulary(self):
        """`PRODUCER_KINDS` is append-only: records on disk reference this label, so it
        must not be removed just because its consumer is between implementations."""
        assert "workflow_surfacing" in fb.PRODUCER_KINDS

    def test_live_skill_gate_withholds_a_suppressed_producer(self, isolated):
        """End-to-end through the LIVE gate: a suppressed skill stops surfacing while a
        healthy one still does. Drives feedback below threshold for one skill, then runs
        the real `get_surfaced_skills` path and asserts it is withheld."""
        from personalclaw.skills.loader import SkillsLoader

        # A skills library isolated to the test home; two skills that both match on
        # the keyword "deploy service".
        skills_root = isolated / "skills"
        for key in ("wrong-skill", "good-skill"):
            d = skills_root / key
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text(f"---\nname: {key}\ntriggers: deploy service\n---\nbody\n")
        loader = SkillsLoader(skills_path=skills_root, install_builtins=False)

        # Healthy state: both surface.
        assert set(loader.get_surfaced_skills("deploy service")) == {"wrong-skill", "good-skill"}

        # Drive one skill's producer below the retire threshold (n>=min_n, acc<threshold).
        _drive_below_threshold(producer_kind="skill_synthesis", producer_id="wrong-skill")
        assert ("skill_synthesis", "wrong-skill") in fb.suppressed_producers()

        # The live gate now WITHHOLDS the suppressed producer; the healthy one stays.
        surfaced = loader.get_surfaced_skills("deploy service")
        assert "wrong-skill" not in surfaced
        assert "good-skill" in surfaced

        # Clearing (the user edited the skill) restores surfacing — the round trip.
        fb.clear_producer("skill_synthesis", "wrong-skill")
        assert set(loader.get_surfaced_skills("deploy service")) == {"wrong-skill", "good-skill"}

    def test_live_skill_gate_fails_open_when_suppression_raises(self, isolated, monkeypatch):
        """A suppression-lookup fault must never empty the turn's skills: the gate
        surfaces normally (fail-open)."""
        from personalclaw.skills.loader import SkillsLoader

        skills_root = isolated / "skills"
        d = skills_root / "a-skill"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("---\nname: a-skill\ntriggers: deploy service\n---\nbody\n")
        loader = SkillsLoader(skills_path=skills_root, install_builtins=False)

        def _boom(*a, **k):
            raise RuntimeError("suppression store exploded")

        monkeypatch.setattr(fb, "suppressed_producers", _boom)
        # Even driven-suppressed, a raising lookup degrades to suppress-nothing.
        assert loader.get_surfaced_skills("deploy service") == ["a-skill"]
