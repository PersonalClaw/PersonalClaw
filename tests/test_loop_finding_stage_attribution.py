"""Findings attribute to tasks: the ingest canonicalizes stage labels (issue 642).

A finding's `stage` is unconstrained model output — one 12-cycle loop produced three
shapes ('1 — Write bell_times.py', '2 — Verify & QA', 'Stage 2/2 — Verify & QA'),
none equal to a plan phase's stage id or title, so exact-match attribution surfaced
ZERO findings on the cockpit of a blocked loop asking to be steered. The plan is the
authority: record_cycle_findings now resolves the label against it at the ONE write
into the ledger, stamping the canonical stage (raw preserved as stage_label).
"""

from __future__ import annotations

from personalclaw.loop import files as F
from personalclaw.loop import store as S

PLAN = [
    {"stage": "implementation", "title": "Write bell_times.py"},
    {"stage": "verification", "title": "Verify & QA"},
    {"stage": "", "title": "Stageless phase"},
]


class TestCanonicalStage:
    def test_all_three_observed_shapes_resolve(self):
        assert F._canonical_stage("1 — Write bell_times.py", PLAN) == "implementation"
        assert F._canonical_stage("2 — Verify & QA", PLAN) == "verification"
        assert F._canonical_stage("Stage 2/2 — Verify & QA", PLAN) == "verification"

    def test_exact_id_and_title_still_match_first(self):
        assert F._canonical_stage("implementation", PLAN) == "implementation"
        assert F._canonical_stage("Verify & QA", PLAN) == "verification"

    def test_stageless_phase_keys_by_title(self):
        assert F._canonical_stage("3 — Stageless phase", PLAN) == "Stageless phase"

    def test_unresolvable_label_returns_none(self):
        assert F._canonical_stage("7 — Something the plan never had", PLAN) is None
        assert F._canonical_stage("", PLAN) is None


class TestIngestStamping:
    def test_ingest_stamps_canonical_stage_and_preserves_raw(self, tmp_path, monkeypatch):
        monkeypatch.setattr(F, "safe_loop_dir", lambda _lid: tmp_path)
        monkeypatch.setattr(F, "loop_dir", lambda _lid: tmp_path)
        fdir = tmp_path / "findings"
        fdir.mkdir()
        (fdir / "cycle_1.json").write_text(
            '{"cycle": 1, "stage": "1 \\u2014 Write bell_times.py", "summary": "did the thing"}'
        )

        class _Loop:
            plan = PLAN

        monkeypatch.setattr(S, "get", lambda _lid: _Loop())
        assert F.record_cycle_findings("loop-x") == 1

        findings = F.get_findings("loop-x")
        assert len(findings) == 1
        assert findings[0]["stage"] == "implementation"
        assert findings[0]["stage_label"] == "1 — Write bell_times.py"

    def test_ingest_leaves_unresolvable_and_canonical_labels_alone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(F, "safe_loop_dir", lambda _lid: tmp_path)
        monkeypatch.setattr(F, "loop_dir", lambda _lid: tmp_path)
        fdir = tmp_path / "findings"
        fdir.mkdir()
        (fdir / "cycle_1.json").write_text('{"cycle": 1, "stage": "verification", "summary": "s"}')
        (fdir / "cycle_2.json").write_text(
            '{"cycle": 2, "stage": "9 — never planned", "summary": "s"}'
        )

        class _Loop:
            plan = PLAN

        monkeypatch.setattr(S, "get", lambda _lid: _Loop())
        F.record_cycle_findings("loop-y")
        by_cycle = {f["cycle"]: f for f in F.get_findings("loop-y")}
        assert by_cycle[1]["stage"] == "verification"
        assert "stage_label" not in by_cycle[1]
        assert by_cycle[2]["stage"] == "9 — never planned"  # untouched, FE fallback's job
