"""Tests for the concurrent-dispatch benchmark + HC-6's before/after gate.

The atom's gate is that the improvement is REAL on the benchmark rather than assumed, so the
tests that matter are the ones that keep the gate from being talked into `improved`: the
overlap band, an outlier in the baseline's tail, a concurrent arm that never actually
overlapped anything, and the fact that the benchmark reads the SHIPPED log line rather than
its own stopwatch.

Nothing here asserts a wall-clock threshold. The end-to-end case runs a real (tiny) turn and
asserts SHAPE — that rows were captured at all, from real production log lines.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from harness import tool_dispatch_bench as tdb
from personalclaw.agents.native import dispatch_plan


def _row(ms: int, *, mode: str = dispatch_plan.MODE_CONCURRENT, widest: int = 8) -> tdb.DispatchRow:
    return tdb.DispatchRow(mode=mode, calls=8, waves=1, widest=widest, ms=ms)


class TestLogLineContract:
    """The benchmark's only input is the production log line. That coupling is deliberate."""

    def test_parses_the_line_the_runtime_actually_emits(self):
        line = f"{dispatch_plan.TIMING_LOG_PREFIX} mode=concurrent calls=8 waves=2 widest=6 ms=812"
        row = tdb.parse_timing_line(line)
        assert row == tdb.DispatchRow(mode="concurrent", calls=8, waves=2, widest=6, ms=812)

    def test_parses_it_with_a_log_prefix_in_front(self):
        row = tdb.parse_timing_line(
            f"INFO personalclaw: {dispatch_plan.TIMING_LOG_PREFIX} mode=serial calls=2 "
            "waves=2 widest=1 ms=7"
        )
        assert row is not None and row.mode == "serial" and row.widest == 1

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "something else entirely",
            # Missing a field does not parse AT ALL rather than parsing with a default: mode
            # and widest are the only things telling the baseline arm from the after arm, so
            # a tolerant parser would silently compare an arm against itself.
            f"{dispatch_plan.TIMING_LOG_PREFIX} mode=serial calls=2 waves=2 ms=7",
            f"{dispatch_plan.TIMING_LOG_PREFIX} calls=2 waves=2 widest=1 ms=7",
        ],
    )
    def test_a_line_missing_a_field_does_not_parse(self, line):
        assert tdb.parse_timing_line(line) is None

    def test_the_collector_captures_rows_off_the_runtime_logger(self):
        log = logging.getLogger("personalclaw.agents.native.runtime")
        with tdb.collect_timing_rows() as rows:
            log.info(
                "%s mode=%s calls=%d waves=%d widest=%d ms=%d",
                dispatch_plan.TIMING_LOG_PREFIX,
                "concurrent",
                4,
                1,
                4,
                33,
            )
            log.info("an unrelated line")
        assert [r.ms for r in rows] == [33]

    def test_the_collector_forces_the_logger_open_and_restores_it(self):
        log = logging.getLogger("personalclaw.agents.native.runtime")
        log.setLevel(logging.CRITICAL)
        try:
            with tdb.collect_timing_rows() as rows:
                log.info(
                    "%s mode=serial calls=1 waves=1 widest=1 ms=1", dispatch_plan.TIMING_LOG_PREFIX
                )
            assert len(rows) == 1, "a benchmark that collected nothing must not look clean"
            assert log.level == logging.CRITICAL
        finally:
            log.setLevel(logging.NOTSET)


class TestGate:
    def test_unanimously_faster_is_improved(self):
        v = tdb.evaluate_gate([1400, 1600, 2000], [900, 910, 950])
        assert v.verdict == tdb.VERDICT_IMPROVED
        assert v.conclusive

    def test_unanimously_slower_is_a_finding_not_an_error(self):
        v = tdb.evaluate_gate([900, 910, 950], [1400, 1600, 2000])
        assert v.verdict == tdb.VERDICT_NO_IMPROVEMENT
        assert v.conclusive

    def test_overlapping_arms_are_unresolved(self):
        v = tdb.evaluate_gate([1000, 1100, 1200], [950, 1050, 1150])
        assert v.verdict == tdb.VERDICT_UNRESOLVED
        assert "overlap" in " ".join(v.notes)

    def test_a_near_boundary_win_inside_the_band_is_unresolved(self):
        """Faster on every sample, but by less than the instrument can see."""
        v = tdb.evaluate_gate([1000, 1000, 1000], [900, 900, 900])
        assert v.verdict == tdb.VERDICT_UNRESOLVED

    def test_an_outlier_in_the_baselines_tail_cannot_widen_the_band_away_from_a_verdict(self):
        """The band is a fraction of the baseline's MEDIAN, not its mean. Keyed to the mean, a
        single contention-hit serial sample inflated the band past the whole effect and
        reported `unresolved` on arms that did not overlap — noise in the baseline's tail
        deciding a question about its floor."""
        v = tdb.evaluate_gate([1206, 1313, 1356, 1424, 2901, 10168, 1197], [614, 632, 610, 791])
        assert v.verdict == tdb.VERDICT_IMPROVED

    def test_an_empty_arm_is_unresolved_not_a_win(self):
        assert tdb.evaluate_gate([], [900]).verdict == tdb.VERDICT_UNRESOLVED
        assert tdb.evaluate_gate([900], []).verdict == tdb.VERDICT_UNRESOLVED

    def test_every_verdict_is_in_the_declared_vocabulary(self):
        for verdict in (
            tdb.evaluate_gate([1400], [900]),
            tdb.evaluate_gate([900], [1400]),
            tdb.evaluate_gate([1000], [1000]),
            tdb.evaluate_gate([], []),
        ):
            assert verdict.verdict in tdb.VERDICTS


class TestBaselineReport:
    def _baseline(self, **kw) -> tdb.DispatchBaseline:
        defaults: dict = dict(
            repo="/tmp/x",
            calls=8,
            trials=3,
            serial_rows=[_row(1400, mode=dispatch_plan.MODE_SERIAL, widest=1) for _ in range(3)],
            concurrent_rows=[_row(900) for _ in range(3)],
        )
        defaults.update(kw)
        return tdb.DispatchBaseline(**defaults)

    def test_reports_the_speedup_beside_the_samples(self):
        b = self._baseline()
        d = b.to_dict()
        assert d["serial_ms"] == [1400, 1400, 1400]
        assert d["concurrent_ms"] == [900, 900, 900]
        assert d["speedup"] == pytest.approx(1.56, abs=0.01)
        assert d["gate"]["verdict"] == tdb.VERDICT_IMPROVED

    def test_a_concurrent_arm_that_never_overlapped_anything_is_called_out(self):
        """The failure this exists for: a run that compared serial against serial and
        reported a speedup made of noise."""
        b = self._baseline(concurrent_rows=[_row(900, widest=1) for _ in range(3)])
        notes = " ".join(b.gate().notes)
        assert "one call wide" in notes

    def test_contention_is_recorded_as_the_direction_that_flatters_the_result(self):
        b = self._baseline(contended=True)
        assert "pessimistic" in " ".join(b.gate().notes)

    def test_no_samples_means_no_speedup_claim(self):
        b = self._baseline(serial_rows=[], concurrent_rows=[])
        assert b.speedup == 0.0
        assert b.gate().verdict == tdb.VERDICT_UNRESOLVED


class TestEndToEnd:
    """One real measurement, asserting SHAPE. The gate's verdict is deliberately not asserted:
    the suite runs on shared CI hardware, and a test that required `improved` would be a
    performance assertion masquerading as a correctness one."""

    def test_measures_both_arms_off_real_log_lines(self, tmp_path: Path):
        (tmp_path / "pkg0000").mkdir()
        (tmp_path / "pkg0000" / "mod0000.py").write_text("VALUE = 0\n", encoding="utf-8")
        (tmp_path / "pkg0001").mkdir()
        (tmp_path / "pkg0001" / "mod0001.py").write_text("VALUE = 1\n", encoding="utf-8")
        baseline = asyncio.run(tdb.measure(tmp_path, trials=2, contended=True))
        assert len(baseline.serial_ms) == 2
        assert len(baseline.concurrent_ms) == 2
        assert baseline.calls == len(tdb.MULTI_LOOKUP_TURN)
        # The arms differ in the way the PLAN says they should, which is the thing the
        # benchmark's two arms are actually selecting between.
        assert {r.mode for r in baseline.serial_rows} == {dispatch_plan.MODE_SERIAL}
        assert {r.widest for r in baseline.serial_rows} == {1}
        assert {r.mode for r in baseline.concurrent_rows} == {dispatch_plan.MODE_CONCURRENT}
        assert max(r.widest for r in baseline.concurrent_rows) == len(tdb.MULTI_LOOKUP_TURN)
        assert baseline.gate().verdict in tdb.VERDICTS

    def test_a_nonpositive_trial_count_is_refused(self, tmp_path: Path):
        with pytest.raises(tdb.BenchmarkError):
            asyncio.run(tdb.measure(tmp_path, trials=0))
