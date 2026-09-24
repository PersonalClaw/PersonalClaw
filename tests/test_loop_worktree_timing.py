"""Tests for worktree creation-cost instrumentation (HARNESS-CRAFT §1.1, HC-1).

**A test that only asserts "a timing line was emitted" is worthless**, because the defect this
instrumentation exists to prevent is a line whose NUMBER is wrong — a constant, a zero, a duration
that silently measures the `git ls-files` probe instead of the checkout. Every duration assertion
here is therefore two-sided:

* a LOWER bound, from a deliberate sleep injected into the git call, so a hard-coded `ms=0` reds;
* an UPPER bound, from a wall clock the test keeps around the same call, so a hard-coded large
  constant reds too.

Both bounds survive a loaded machine, because contention can only push the real duration up
(clearing the floor) and it pushes the observed wall clock up with the logged value (preserving the
ceiling). There is no fixed-threshold assertion anywhere in this file.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time

import pytest

from personalclaw.loop import worktree as wt

pytestmark = pytest.mark.skipif(not wt.git_available(), reason="git not installed")


@pytest.fixture(autouse=True)
def _wt_root(tmp_path, monkeypatch):
    """Root worktrees under a temp config dir, and clear the process-global size cache.

    The cache is keyed by workspace abspath and lives for the process, so a test that left an
    entry behind would hand the next test a file count from a repo that no longer exists — the
    stale-process-global-state hazard, not a hypothetical one.
    """
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path / "pclaw")
    wt._FILE_COUNT_CACHE.clear()
    yield tmp_path
    wt._FILE_COUNT_CACHE.clear()


def _init_repo(path: str, files: int = 1) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    for i in range(files):
        with open(os.path.join(path, f"f{i}.txt"), "w") as fh:
            fh.write(f"{i}\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        cwd=path,
        check=True,
    )


def _repo(tmp_path, name: str = "repo", files: int = 1) -> str:
    d = tmp_path / name
    d.mkdir()
    _init_repo(str(d), files=files)
    return str(d)


def _rows(caplog) -> list[re.Match]:
    """Every timing line in the captured log, parsed into its fields."""
    pattern = re.compile(
        re.escape(wt.TIMING_LOG_PREFIX)
        + r" outcome=(?P<outcome>\S+) task=(?P<task>\S+) ms=(?P<ms>-?\d+) "
        r"files=(?P<files>-?\d+) size_class=(?P<size_class>\S+)$"
    )
    out = []
    for rec in caplog.records:
        m = pattern.search(rec.getMessage())
        if m is not None:
            out.append(m)
    return out


class TestSizeClass:
    """The class is a decade bucket, and its boundaries are the contract."""

    @pytest.mark.parametrize(
        "count,expected",
        [
            (0, "tiny"),
            (99, "tiny"),
            (100, "small"),
            (999, "small"),
            (1_000, "medium"),
            (9_999, "medium"),
            # §1.1's benchmark case is a 10K-file repo: it must land ON a class floor, not
            # straddle a boundary, or two benchmark runs could report different classes.
            (10_000, "large"),
            (99_999, "large"),
            (100_000, "huge"),
            (5_000_000, "huge"),
        ],
    )
    def test_bucket_boundaries(self, count, expected):
        assert wt.size_class(count) == expected

    def test_unknown_is_not_a_small_repo(self):
        """An unmeasurable repo must not read as a tiny one — that would silently make every
        no-git workspace look like the cheapest class in the log."""
        assert wt.size_class(wt.FILE_COUNT_UNKNOWN) == wt.SIZE_CLASS_UNKNOWN
        assert wt.size_class(wt.FILE_COUNT_UNKNOWN) != wt.size_class(0)


class TestFileCountCache:
    def test_counts_tracked_files(self, tmp_path):
        repo = _repo(tmp_path, files=7)
        assert wt.repo_file_count(repo) == 7
        assert wt.repo_size_class(repo) == "tiny"

    def test_second_call_does_not_shell_out(self, tmp_path, monkeypatch):
        """The cache is the whole reason this is safe to call per creation.

        Uncached, `git ls-files` walks the index of the very repo being timed — on the 10K-file
        benchmark case that is a real share of the number being reported, i.e. the
        instrumentation becoming the thing it measures.
        """
        repo = _repo(tmp_path, files=3)
        assert wt.repo_file_count(repo) == 3

        calls: list[tuple[str, ...]] = []
        real = wt._git

        def _spy(workspace, *args, **kw):
            calls.append(args)
            return real(workspace, *args, **kw)

        monkeypatch.setattr(wt, "_git", _spy)
        for _ in range(5):
            assert wt.repo_file_count(repo) == 3
        assert not any(a[:1] == ("ls-files",) for a in calls), calls

    def test_a_non_repo_reports_unknown_and_caches_the_failure(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert wt.repo_file_count(str(plain)) == wt.FILE_COUNT_UNKNOWN
        assert wt.repo_size_class(str(plain)) == wt.SIZE_CLASS_UNKNOWN
        assert wt._FILE_COUNT_CACHE[os.path.abspath(str(plain))] == wt.FILE_COUNT_UNKNOWN


class TestTimingLine:
    def test_creation_logs_one_parseable_row_with_the_repo_size(self, tmp_path, caplog):
        repo = _repo(tmp_path, files=5)
        with caplog.at_level(logging.INFO, logger=wt.__name__):
            assert wt.add_worktree(repo, "t-one") is not None
        rows = _rows(caplog)
        assert len(rows) == 1, [r.getMessage() for r in caplog.records]
        row = rows[0]
        assert row["outcome"] == wt.OUTCOME_CREATED
        assert row["task"] == "t-one"
        # The size tag is not decoration: it is what says which numbers may be compared with
        # which. Assert the REAL count, so dropping the tag or hard-coding it reds.
        assert int(row["files"]) == 5
        assert row["size_class"] == wt.size_class(5) == "tiny"

    def test_the_logged_duration_is_a_real_measurement(self, tmp_path, caplog):
        """Two-sided: a slow `git worktree add` must show up, and cannot be exceeded.

        The floor comes from a sleep injected into the git call (so `ms=0` or any constant below
        it reds); the ceiling comes from a wall clock around the same call (so a constant large
        value reds). Contention moves both bounds in the same direction, so neither is flaky.
        """
        repo = _repo(tmp_path, files=2)
        sleep_secs = 0.30
        real = wt._git

        def _slow(workspace, *args, **kw):
            if args[:1] == ("worktree",):
                time.sleep(sleep_secs)
            return real(workspace, *args, **kw)

        with caplog.at_level(logging.INFO, logger=wt.__name__):
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(wt, "_git", _slow)
                started = time.perf_counter()
                assert wt.add_worktree(repo, "t-slow") is not None
                observed_ms = (time.perf_counter() - started) * 1000

        rows = _rows(caplog)
        assert len(rows) == 1
        logged_ms = int(rows[0]["ms"])
        assert (
            logged_ms >= sleep_secs * 1000 * 0.9
        ), f"logged {logged_ms}ms ignores the injected sleep"
        # The logged window is a SUBSET of the observed one, plus rounding and the log call
        # itself. A generous slack keeps this robust under load while still killing any
        # hard-coded constant, which would have to be under the real duration to pass the floor.
        assert (
            logged_ms <= observed_ms + 250
        ), f"logged {logged_ms}ms exceeds observed {observed_ms}ms"

    def test_the_reused_path_is_tagged_and_not_counted_as_hydration(self, tmp_path, caplog):
        """`add_worktree` is idempotent; its early return must not read as a fast checkout.

        Untagged, a resume would drop near-zero rows into the same population as real creations
        and drag any mean under the §1.1 gate — a measurement that argues itself out of a job.
        """
        repo = _repo(tmp_path, files=2)
        with caplog.at_level(logging.INFO, logger=wt.__name__):
            first = wt.add_worktree(repo, "t-again")
            second = wt.add_worktree(repo, "t-again")
        assert first == second
        rows = _rows(caplog)
        assert [r["outcome"] for r in rows] == [wt.OUTCOME_CREATED, wt.OUTCOME_REUSED]
        assert int(rows[1]["ms"]) <= int(rows[0]["ms"])

    def test_a_failed_creation_still_reports_its_duration(self, tmp_path, caplog):
        """The timeout case is the one §1 worries about ("inside the 30s _TIMEOUT budget").

        A failure that logged nothing would make the most expensive outcome the only invisible
        one.
        """
        repo = _repo(tmp_path, files=2)
        real = wt._git

        def _fail(workspace, *args, **kw):
            if args[:1] == ("worktree",):
                return 1, "fatal: nope"
            return real(workspace, *args, **kw)

        with caplog.at_level(logging.INFO, logger=wt.__name__):
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(wt, "_git", _fail)
                assert wt.add_worktree(repo, "t-broken") is None
        rows = _rows(caplog)
        assert len(rows) == 1
        assert rows[0]["outcome"] == wt.OUTCOME_FAILED
        assert int(rows[0]["ms"]) >= 0

    def test_an_unsafe_task_id_logs_no_timing_row(self, tmp_path, caplog):
        """The refusal happens before any work, so there is no duration to report — and a row
        with a rejected id in it would pollute the population with a non-measurement."""
        repo = _repo(tmp_path, files=1)
        with caplog.at_level(logging.INFO, logger=wt.__name__):
            assert wt.add_worktree(repo, "../escape") is None
        assert _rows(caplog) == []

    def test_the_size_probe_runs_after_the_clock_stops(self, tmp_path, caplog):
        """A cache miss must not be charged to the checkout.

        Asserted as an ORDERING, not as a duration: the size probe must be called only after the
        `git worktree add` has returned, at which point the measured window is already closed.
        An earlier version of this test made `ls-files` sleep 600ms and asserted the logged
        duration stayed under that — which FAILED on a loaded machine when the real checkout took
        801ms, i.e. it was a wall-clock-flaky test measuring the wrong thing. Ordering is the
        actual invariant and it cannot be flaked by load.

        Instrumented the wrong way round, the first worktree of every process — the one a fan-out
        benchmark reads first — would report its own probe as hydration.
        """
        repo = _repo(tmp_path, files=2)
        events: list[str] = []
        real_git, real_count = wt._git, wt.repo_file_count

        def _tracked_git(workspace, *args, **kw):
            out = real_git(workspace, *args, **kw)
            if args[:1] == ("worktree",):
                events.append("git-worktree-add-returned")
            return out

        def _tracked_count(workspace):
            events.append("size-probe")
            return real_count(workspace)

        with caplog.at_level(logging.INFO, logger=wt.__name__):
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(wt, "_git", _tracked_git)
                mp.setattr(wt, "repo_file_count", _tracked_count)
                assert wt.add_worktree(repo, "t-probe") is not None

        assert events == ["git-worktree-add-returned", "size-probe"], events
        rows = _rows(caplog)
        assert len(rows) == 1
        assert int(rows[0]["files"]) == 2, "the probe must still run — just not on the clock"
