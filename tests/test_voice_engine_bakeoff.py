"""Tests for the voice-clone engine bake-off (MULTIMODAL-IO §2.2, atom MI-6).

These lock the harness's *honesty* contract as much as its verdict: raw values are
literature-or-unknown (never invented), scores are bounded judgments, an unknown metric
is excluded rather than guessed, and the measurement path skips with a clear reason
instead of fabricating a latency.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.evals import voice_engine_bakeoff as vb
from personalclaw.local_models import provider as lm_provider


def test_report_is_deterministic_and_omnivoice_wins() -> None:
    a = vb.run_bakeoff()
    b = vb.run_bakeoff()
    assert a.scores == b.scores  # pure + deterministic
    assert a.winner == "omnivoice"
    assert a.scores["omnivoice"] > a.scores["cosyvoice"]
    # the call is not lopsided — CosyVoice is a real contender, not a straw man
    assert a.scores["cosyvoice"] > 0.5
    assert "OmniVoice" in a.verdict
    assert a.rejection_notes and "CosyVoice" in a.rejection_notes


def test_footprint_is_measured_not_guessed() -> None:
    """The cell the spike EXCLUDED as unpublished now carries the integration-run
    measurement — a real number with ``measured`` provenance, never a fabricated one,
    and its inclusion did not flip the verdict."""
    report = vb.run_bakeoff()
    excluded = {c.criterion.key for c in report.cells if not c.scored}
    assert "footprint" not in excluded
    omni = next(c for c in report.candidates if c.key == "omnivoice")
    assert omni.metrics["footprint"].provenance == vb.MEASURED
    assert "3.27 GB" in omni.metrics["footprint"].raw
    assert omni.metrics["footprint"].score is not None
    assert report.winner == "omnivoice"


def test_scores_bounded_and_renormalized_over_kept_criteria() -> None:
    report = vb.run_bakeoff()
    for key, score in report.scores.items():
        assert 0.0 <= score <= 1.0, f"{key} score out of range: {score}"
    # aggregate is a weighted mean over KEPT criteria only, renormalized to their weights
    kept = [c.criterion for c in report.cells if c.scored]
    total = sum(c.weight for c in kept)
    omni = next(c for c in report.candidates if c.key == "omnivoice")
    acc = 0.0
    for crit in kept:
        cell_score = omni.metrics[crit.key].score
        assert cell_score is not None  # kept criteria are known for every candidate
        acc += crit.weight * cell_score
    assert report.scores["omnivoice"] == pytest.approx(acc / total, abs=1e-4)


def test_every_known_metric_declares_provenance_and_literature_cites_a_url() -> None:
    report = vb.run_bakeoff()
    for cand in report.candidates:
        for key, metric in cand.metrics.items():
            assert metric.provenance in {vb.LITERATURE, vb.MEASURED, vb.JUDGMENT, vb.UNKNOWN}
            if metric.provenance == vb.LITERATURE:
                assert metric.citation is not None, f"{cand.key}.{key} literature w/o citation"
                assert metric.citation.url.startswith("http")
            if metric.provenance == vb.UNKNOWN:
                assert metric.score is None
            # a measured cell must say WHERE it was measured — provenance is not a vibe
            if metric.provenance == vb.MEASURED:
                assert "integration host" in metric.raw


def test_license_rule_matches_local_models_provider() -> None:
    # both engines are Apache-2.0 → commercial-safe, and our sniff agrees with the
    # provider's "omnivoice rule" it mirrors, for the same inputs
    for cand in vb.run_bakeoff().candidates:
        assert cand.license_spdx == "Apache-2.0"
        assert cand.non_commercial is False
    for lic in ("Apache-2.0", "MIT", "CC-BY-NC-4.0", "cc-by-nc-sa"):
        assert vb.is_non_commercial(lic) == lm_provider._is_non_commercial(lic)
    assert vb.is_non_commercial("CC-BY-NC-4.0") is True


def test_measure_skips_when_engine_key_unknown() -> None:
    r = vb.measure_fixture_rtf("nope", "/tmp/does-not-matter")
    assert r.skipped and "unknown engine" in r.reason and r.rtf is None


def test_measure_skips_when_engine_package_not_installed(tmp_path) -> None:
    # the real engine keys map to packages absent on the build host
    r = vb.measure_fixture_rtf("omnivoice", tmp_path)
    assert r.skipped and "not installed" in r.reason and r.rtf is None


def test_measure_skips_when_no_fixtures(tmp_path, monkeypatch) -> None:
    # point the engine at an importable stdlib module so we reach the fixtures check
    monkeypatch.setitem(vb._ENGINE_IMPORT, "omnivoice", "json")
    r = vb.measure_fixture_rtf("omnivoice", tmp_path)  # empty dir → no *.wav
    assert r.skipped and "no reference-audio fixtures" in r.reason


def test_measure_skips_ready_when_weights_unconfirmed(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(vb._ENGINE_IMPORT, "omnivoice", "json")
    (tmp_path / "ref.wav").write_bytes(b"RIFF....WAVE")  # a fixture exists
    r = vb.measure_fixture_rtf("omnivoice", tmp_path, weights_present=False)
    assert r.skipped and "weights not confirmed" in r.reason and r.fixtures == 1
    assert r.rtf is None  # never fabricated even when engine + fixtures are present


def test_cli_json_exits_zero_and_is_wellformed(capsys) -> None:
    rc = vb.main(["--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["winner"] == "omnivoice"
    assert set(payload["scores"]) == {"omnivoice", "cosyvoice"}
    assert payload["rejection_notes"]
    assert not any(e["key"] == "footprint" for e in payload["excluded_criteria"])
    assert payload["deferred"], "the remaining fixture-latency deferral must be recorded"


def test_cli_measure_skip_prints_reason(capsys) -> None:
    rc = vb.main(["--measure", "cosyvoice", "--fixtures", "/tmp/none-here"])
    assert rc == 0
    assert "skipped" in capsys.readouterr().out.lower()


def test_format_report_names_both_engines_and_the_verdict() -> None:
    text = vb.format_report(vb.run_bakeoff())
    assert "OmniVoice" in text and "CosyVoice" in text
    assert "VERDICT" in text and "REJECTION NOTES" in text and "DEFERRED" in text
