"""Tests for the worktree fan-out benchmark + §1.1 measure-first gate (HARNESS-CRAFT HC-1).

The gate decides whether a whole atom (`HC-2`) gets built, so the tests that matter are the ones
that keep it from being talked into a verdict: the near-boundary band, the too-small-repo case, and
the fact that the benchmark reads the SHIPPED log line rather than its own stopwatch.

Nothing here asserts a wall-clock threshold. The end-to-end case runs a real (tiny) fan-out and
asserts SHAPE — that durations were captured at all, from real rows, and that the verdict on a
small repo refuses to generalize.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import pytest

from harness import worktree_bench as wb
from personalclaw.loop import worktree as wt

pytestmark = pytest.mark.skipif(not wt.git_available(), reason="git not installed")


@pytest.fixture(autouse=True)
def _clear_size_cache():
    wt._FILE_COUNT_CACHE.clear()
    yield
    wt._FILE_COUNT_CACHE.clear()


def _row(ms: int, outcome: str = wt.OUTCOME_CREATED) -> wb.TimingRow:
    return wb.TimingRow(outcome=outcome, task="t-x", ms=ms, files=20_000, size_class="large")


class TestLogLineContract:
    """The benchmark's only input is the production log line. That coupling is deliberate."""

    def test_parses_the_line_the_module_actually_emits(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path / "pclaw")
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
            cwd=str(repo),
            check=True,
        )
        with wb.collect_timing_rows() as rows:
            assert wt.add_worktree(str(repo), "t-live") is not None
        assert len(rows) == 1
        assert rows[0].outcome == wt.OUTCOME_CREATED
        assert rows[0].files == 1
        assert rows[0].size_class == "tiny"

    def test_a_line_missing_the_size_class_does_not_parse(self):
        """Strict on purpose: a tolerant parser would let the size tag disappear and still print a
        plausible report, which is the defect the tag exists to prevent."""
        good = f"{wt.TIMING_LOG_PREFIX} outcome=created task=t-a ms=42 files=9 size_class=tiny"
        assert wb.parse_timing_line(good) is not None
        assert (
            wb.parse_timing_line(f"{wt.TIMING_LOG_PREFIX} outcome=created task=t-a ms=42") is None
        )
        assert wb.parse_timing_line("some other log line entirely") is None
        assert wb.parse_timing_line("") is None

    def test_collector_captures_even_when_the_logger_was_silenced(self):
        """A benchmark that quietly collected zero rows would report a clean fan-out of nothing."""
        log = logging.getLogger(wt.__name__)
        prior = log.level
        log.setLevel(logging.CRITICAL)
        try:
            with wb.collect_timing_rows() as rows:
                wt.logger.info(
                    "%s outcome=created task=t-q ms=7 files=3 size_class=tiny",
                    wt.TIMING_LOG_PREFIX,
                )
        finally:
            log.setLevel(prior)
        assert len(rows) == 1 and rows[0].ms == 7
        assert log.level == prior, "the collector must restore the level it raised"


class TestGate:
    def test_a_unanimously_slow_arm_proceeds_to_hc2(self):
        v = wb.evaluate_gate([4238, 6144, 9000, 12030], repo_files=20_000, width=4)
        assert v.verdict == wb.VERDICT_PROCEED
        assert v.conclusive
        assert any("proceed to HC-2" in n for n in v.notes)

    def test_a_unanimously_fast_arm_skips_and_rescopes(self):
        v = wb.evaluate_gate([276, 303, 250, 288], repo_files=20_000, width=4)
        assert v.verdict == wb.VERDICT_SKIP_AND_RESCOPE
        assert any("SKIPPED" in n for n in v.notes)

    @pytest.mark.parametrize("ms", [1600, 1900, 2000, 2100, 2400])
    def test_the_boundary_band_refuses_to_pick_a_side(self, ms):
        """A wall clock cannot separate 1.9s from 2.1s, and this decision builds or drops an atom.

        Both directions are inside the band, so neither the convenient nor the inconvenient
        reading can be extracted from a near-boundary number.
        """
        v = wb.evaluate_gate([ms] * 4, repo_files=20_000, width=4)
        assert v.verdict == wb.VERDICT_UNRESOLVED
        assert not v.conclusive

    def test_samples_that_straddle_the_gate_get_no_verdict(self):
        """One fast worktree and one slow one is not an answer, whatever the mean says.

        This is the rule that makes the gate honest under load: a single unlucky sample can move
        a four-sample mean across the threshold, so a mean-versus-threshold test would have been
        deciding an atom's fate on which sample got descheduled.
        """
        v = wb.evaluate_gate([900, 5000, 1200, 1100], repo_files=20_000, width=4)
        assert v.verdict == wb.VERDICT_UNRESOLVED
        assert any("straddle" in n for n in v.notes)

    def test_a_noisy_but_unanimous_arm_still_answers(self):
        """HC-1's own measured shape: samples 4.2s–12.0s, a spread larger than the 6.5s mean.

        An earlier version of this gate refused here on the spread alone. That was wrong — every
        observation was more than twice the gate, and a same-window 40-file control arm came in at
        276ms, so the noise was all in the tail and the floor was never in question. Throwing away
        a unanimous result is as dishonest as inventing one.
        """
        v = wb.evaluate_gate([4238, 4948, 6144, 12030], repo_files=10_000, width=4)
        assert v.verdict == wb.VERDICT_PROCEED
        assert any("CHEAPEST was 4238ms" in n for n in v.notes)

    def test_a_repo_under_the_benchmark_size_cannot_close_the_gate(self):
        """A fast number on a small repo is a real number about a different question."""
        v = wb.evaluate_gate([40, 45], repo_files=3_000, width=4)
        assert v.verdict == wb.VERDICT_UNRESOLVED
        assert any("tracked files" in n for n in v.notes)

    def test_a_narrow_fanout_cannot_close_the_gate(self):
        v = wb.evaluate_gate([40, 45], repo_files=20_000, width=2)
        assert v.verdict == wb.VERDICT_UNRESOLVED
        assert any("narrower" in n for n in v.notes)

    def test_no_samples_is_not_a_fast_result(self):
        """An empty arm means nothing was measured — which must never read as "nothing is slow"."""
        v = wb.evaluate_gate([], repo_files=20_000, width=4)
        assert v.verdict == wb.VERDICT_UNRESOLVED
        assert any("nothing was measured" in n for n in v.notes)

    def test_every_verdict_is_in_the_declared_vocabulary(self):
        for arm in ([], [500], [2000] * 4, [9_999] * 4, [100, 9_999]):
            v = wb.evaluate_gate(arm, repo_files=20_000, width=4)
            assert v.verdict in wb.VERDICTS


class TestBaseline:
    def test_reused_and_failed_rows_are_excluded_from_the_per_worktree_mean(self):
        base = wb.FanOutBaseline(
            repo="/r",
            repo_files=20_000,
            size_class="large",
            width=4,
            rows=[
                _row(4000),
                _row(4000),
                _row(1, outcome=wt.OUTCOME_REUSED),
                _row(30_000, outcome=wt.OUTCOME_FAILED),
            ],
        )
        assert base.created_ms == [4000, 4000]
        assert base.mean_ms == 4000.0
        assert base.max_ms == 4000
        assert base.total_ms == 8000
        assert base.spread_ms == 0
        # The near-zero reuse row would have made the arm straddle the gate (1ms…4000ms) and
        # returned `unresolved`; the timeout row would have argued for `proceed` on a FAILURE.
        # Excluding both has to happen before the verdict, not after.
        assert base.gate().verdict == wb.VERDICT_PROCEED

    def test_missing_samples_are_named_in_the_verdict(self):
        base = wb.FanOutBaseline(
            repo="/r", repo_files=20_000, size_class="large", width=4, rows=[_row(4000)]
        )
        assert any("only 1 of 4" in n for n in base.gate().notes)

    def test_a_timed_out_worktree_is_reported_as_a_LOST_worker_not_a_slow_one(self):
        """The measured shape of the real benchmark run: 3 creations + 1 timeout.

        A report that folded the failure into "3 samples" would hide the single most important
        thing the measurement found — that a width-4 fan-out on a 10K-file repo does not merely
        get slow, it drops a task's worktree entirely at the 30s git timeout.
        """
        base = wb.FanOutBaseline(
            repo="/r",
            repo_files=20_000,
            size_class="large",
            width=4,
            rows=[_row(4000)] * 3 + [_row(30_004, outcome=wt.OUTCOME_FAILED)],
        )
        assert base.outcomes == {wt.OUTCOME_CREATED: 3, wt.OUTCOME_FAILED: 1}
        assert base.failed_ms == [30_004]
        notes = base.gate().notes
        assert any("FAILED" in n and "lost a worker" in n for n in notes)
        assert base.to_dict()["failed_ms"] == [30_004]
        assert base.to_dict()["outcomes"][wt.OUTCOME_FAILED] == 1

    def test_contention_is_recorded_on_the_verdict_not_hidden(self):
        base = wb.FanOutBaseline(
            repo="/r",
            repo_files=20_000,
            size_class="large",
            width=4,
            rows=[_row(4000)] * 4,
            contended=True,
        )
        notes = base.gate().notes
        assert any("PESSIMISTIC" in n for n in notes)

    def test_to_dict_carries_every_reported_number_and_the_verdict(self):
        """Every field the report PRINTS is asserted here, `median_ms` included.

        Found by mutation: replacing `median_ms` with a constant `0.0` reded nothing, because the
        median was printed to a human and never checked by a test. A reported number with no
        assertion behind it is a number nobody is defending.
        """
        base = wb.FanOutBaseline(
            repo="/r",
            repo_files=20_000,
            size_class="large",
            width=4,
            rows=[_row(3000), _row(4000), _row(5000), _row(12_000)],
        )
        d = base.to_dict()
        assert d["created_ms"] == [3000, 4000, 5000, 12_000]
        assert d["mean_ms"] == 6000.0
        assert d["median_ms"] == 4500.0
        assert d["max_ms"] == 12_000
        assert d["spread_ms"] == 9000
        assert d["total_ms"] == 24_000
        assert d["repo_files"] == 20_000
        assert d["size_class"] == "large"
        assert d["width"] == 4
        assert d["outcomes"] == {wt.OUTCOME_CREATED: 4}
        assert d["failed_ms"] == []
        assert d["contended"] is False
        assert d["gate"]["verdict"] == wb.VERDICT_PROCEED


class TestEndToEnd:
    """A benchmark nobody runs rots, so the whole path runs in CI at a tiny size."""

    def test_a_small_fanout_measures_real_rows_and_refuses_to_generalize(self):
        base = wb.run_benchmark(files=40, width=2)
        assert base.repo_files == 40
        assert base.size_class == "tiny"
        assert len(base.created_ms) == 2
        # Shape, not threshold: a real checkout takes a positive, finite amount of time.
        assert all(ms >= 0 for ms in base.created_ms)
        assert base.total_ms == sum(base.created_ms)
        verdict = base.gate()
        assert verdict.verdict == wb.VERDICT_UNRESOLVED
        assert any("tracked files" in n for n in verdict.notes)

    def test_the_synthetic_repo_is_deterministic(self, tmp_path):
        a = wb.synthesize_repo(tmp_path / "a", files=30)
        b = wb.synthesize_repo(tmp_path / "b", files=30)

        def _tree(repo):
            out = subprocess.run(
                ["git", "rev-parse", "HEAD^{tree}"],
                cwd=str(repo),
                capture_output=True,
                check=True,
            )
            return out.stdout.decode().strip()

        assert _tree(a) == _tree(b), "a benchmark whose input drifts cannot be re-run"
        assert wt.repo_file_count(str(a)) == 30

    def test_it_refuses_the_real_personalclaw_home(self, tmp_path):
        """Worktrees land under `config_dir()`, so an ambient home would litter the user's real
        directory — and the benchmark's own cleanup would then delete inside it."""
        with pytest.raises(wb.BenchmarkError, match="refusing to run"):
            wb.measure_fanout(tmp_path, Path.home() / ".personalclaw", width=1)

    def test_a_non_repo_target_is_an_honest_error(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(wb.BenchmarkError, match="not a git repo"):
            wb.measure_fanout(plain, tmp_path / "home", width=1)

    def test_the_temp_home_is_restored_after_a_run(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "outer"))
        wb.run_benchmark(files=10, width=1)
        assert os.environ["PERSONALCLAW_HOME"] == str(tmp_path / "outer")
