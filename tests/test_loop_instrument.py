"""Tests for the P4 'prove-the-instrument' gates: the calibrated returns-band, the
adversarial-skeptic adjudication, and the canary judge probe."""

from __future__ import annotations

import pytest

from personalclaw.loop import instrument
from personalclaw.loop.granularity import (
    calibrated_band,
    returns_exhausted,
    returns_exhausted_calibrated,
)
from personalclaw.loop.judge import CycleVerdict, adjudicate

# ── calibrated band ────────────────────────────────────────────────────────


def test_band_flat_trail_equals_floor():
    # A calm (zero-variance) signal must behave exactly like the fixed dial.
    assert calibrated_band([2.0, 2.0, 2.0, 2.0], 1.0) == 1.0


def test_band_noisy_trail_raised_above_floor():
    # A jittery signal raises the bar (2σ > floor), so the loop needs a deeper dip to stop.
    band = calibrated_band([0.0, 5.0, 0.0, 5.0], 1.0)
    assert band > 1.0


def test_band_short_trail_returns_floor():
    assert calibrated_band([3.0], 2.0) == 2.0
    assert calibrated_band([], 2.0) == 2.0


def test_band_never_below_floor():
    # Low but nonzero variance must not drop the bar under the dial threshold.
    assert calibrated_band([2.0, 2.1, 2.0, 2.1], 2.0) == 2.0


# ── calibrated returns-exhaustion ──────────────────────────────────────────


def test_calibrated_falls_back_to_fixed_below_min_reps():
    # Below _MIN_REPS the calibrated variant must match the plain fixed-threshold one.
    for trail in ([], [0.5], [0.5, 0.5], [4.0, 0.1, 0.1]):
        assert returns_exhausted_calibrated(trail, "quick") == returns_exhausted(trail, "quick")


def test_calibrated_forever_never_exhausts():
    assert returns_exhausted_calibrated([0.0] * 10, "forever") is False


def test_calibrated_exhausts_on_calm_low_trail():
    # Enough reps, all clearly below the balanced threshold (2.0), low variance → exhausted.
    trail = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert returns_exhausted_calibrated(trail, "balanced") is True


def test_calibrated_noise_defers_where_fixed_would_stop():
    # A noisy trail widens the patience window, so a short run of low cycles amid the jitter
    # must NOT exhaust (it's likely a variance dip, not genuine exhaustion) — even though the
    # plain fixed check (window=2, [1.5,1.5] both < 2.0) WOULD stop. This is the guard's point.
    trail = [0.0, 5.0, 0.0, 5.0, 1.5, 1.5]
    assert returns_exhausted(trail, "balanced") is True  # fixed would stop here
    assert returns_exhausted_calibrated(trail, "balanced") is False  # calibrated stays patient


def test_calibrated_exhausts_after_extended_low_run_when_noisy():
    # Once the noisy trail shows a LONG ENOUGH run of sub-threshold cycles, it does exhaust —
    # patience is finite, not infinite.
    trail = [0.0, 5.0, 0.0, 5.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert returns_exhausted_calibrated(trail, "balanced") is True


# ── adjudicate (adversarial skeptic merge) ─────────────────────────────────


def test_adjudicate_done_needs_two_yeses():
    primary = CycleVerdict(done=True, done_reason="met", marginal_value=1.0, quality_score=4.0)
    # skeptic disagrees → completion overturned
    overturned = adjudicate(primary, CycleVerdict(done=False, done_reason="not really"))
    assert overturned.done is False
    assert "overturned" in overturned.done_reason
    assert overturned.adversarial is True
    # skeptic agrees → completion stands
    assert adjudicate(primary, CycleVerdict(done=True)).done is True


def test_adjudicate_regressed_survives_either():
    assert adjudicate(CycleVerdict(regressed=False), CycleVerdict(regressed=True)).regressed is True
    assert adjudicate(CycleVerdict(regressed=True), CycleVerdict(regressed=False)).regressed is True


def test_adjudicate_none_skeptic_passes_primary_unchanged():
    # When the skeptic can't run we never manufacture a refutation.
    primary = CycleVerdict(done=True, marginal_value=2.0, quality_score=3.0)
    result = adjudicate(primary, None)
    assert result.done is True
    assert result.adversarial is False  # no cross-check happened


def test_adjudicate_carries_primary_scores():
    primary = CycleVerdict(done=True, marginal_value=3.5, quality_score=4.2, band_used=1.7)
    merged = adjudicate(primary, CycleVerdict(done=True))
    assert merged.marginal_value == 3.5 and merged.quality_score == 4.2 and merged.band_used == 1.7


# ── canary probe ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_canary_trustworthy_when_judge_separates():
    async def good(goal, dod, finding, prior):
        strong = "Implemented" in (finding.get("summary") or "")
        return CycleVerdict(quality_score=4.5 if strong else 0.5)

    assert await instrument.probe_judge(good) is True


@pytest.mark.asyncio
async def test_canary_blind_when_judge_collapses():
    async def blind(goal, dod, finding, prior):
        return CycleVerdict(quality_score=3.0)  # identical for strong + null → no separation

    assert await instrument.probe_judge(blind) is False


@pytest.mark.asyncio
async def test_canary_defers_when_probe_unrunnable():
    async def dead(goal, dod, finding, prior):
        return None

    assert await instrument.probe_judge(dead) is None  # never a false-blind


@pytest.mark.asyncio
async def test_canary_defers_on_exception():
    async def boom(goal, dod, finding, prior):
        raise RuntimeError("model down")

    assert await instrument.probe_judge(boom) is None


# ── observability fields ───────────────────────────────────────────────────


def test_verdict_to_dict_lean_by_default():
    d = CycleVerdict(marginal_value=1.0, quality_score=2.0).to_dict()
    assert "adversarial" not in d and "band_used" not in d


def test_verdict_to_dict_includes_set_observability():
    d = CycleVerdict(done=True, adversarial=True, band_used=1.777).to_dict()
    assert d["adversarial"] is True and d["band_used"] == 1.78  # rounded to 2dp


# ── reproduce_confirm anchor resolution (V5 fix: fall back to the kind's deliverable) ──


@pytest.mark.asyncio
async def test_reproduce_uses_kind_deliverable_when_cfg_empty(monkeypatch, tmp_path):
    """An open-ended loop with empty cfg.deliverables must still reproduce against the
    kind's canonical deliverable (REPORT.md) — the common case the gate previously missed."""
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop

    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    (tmp_path / "REPORT.md").write_text("# Report\nreal deliverable content", encoding="utf-8")
    import json as _json

    loop = store.create(
        Loop(
            id="",
            name="g",
            kind="goal",
            task="t",
            success_criteria="c",
            workspace_dir=str(tmp_path),
            kind_config={"goal_type": "open_ended", "deliverables": []},
        )
    )
    # A finding is a cycle_NNN.json file in the loop's findings/ dir (worker-written on disk).
    fdir = store.safe_loop_dir(loop.id) / "findings"
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "cycle_001.json").write_text(
        _json.dumps({"cycle": 1, "summary": "wrote REPORT.md"}), encoding="utf-8"
    )
    captured = {}

    async def spy(goal, sc, finding, prior, **kw):
        captured.update(kw)  # capture the deliverables the reproduce threaded in
        return CycleVerdict(done=True, quality_score=4.0)

    monkeypatch.setattr("personalclaw.loop.judge.assess_cycle", spy)
    result = await instrument.reproduce_confirm(loop)
    assert result is True  # fresh pass agreed
    assert captured.get("deliverables") == ["REPORT.md"], f"kind deliverable not used: {captured}"


@pytest.mark.asyncio
async def test_reproduce_none_when_no_anchor_and_no_kind_deliverable(monkeypatch, tmp_path):
    """A goal whose kind has no canonical deliverable (verifiable → '') and no cfg anchor
    returns None (nothing independent to reproduce) — the gate stays fail-safe."""
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop

    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    loop = store.create(
        Loop(
            id="",
            name="g",
            kind="goal",
            task="t",
            kind_config={"goal_type": "verifiable", "deliverables": []},
        )
    )
    assert await instrument.reproduce_confirm(loop) is None
