"""Worktree fan-out hydration benchmark + the §1.1 measure-first gate (HARNESS-CRAFT HC-1).

HARNESS-CRAFT §1 is a **measured-bottleneck plan**: its sparse/pooled/reused hydration work
(§1.2, atom `HC-2`) is only allowed to be built if a fan-out actually pays for full working-tree
hydration. §1.1 states the gate in one sentence — *"if measurement shows <2s per worktree on the
benchmark, Session 1's remaining items are SKIPPED and the plan is re-scoped"* — and this module is
that sentence, executable.

Three decisions here are structural rather than incidental:

* **The benchmark reads the SHIPPED log line; it does not keep its own stopwatch.** Durations come
  from parsing `personalclaw.loop.worktree`'s timing line, so the number in this report is the
  number production emits. A parallel timer in the harness would be a second implementation of the
  measurement, free to disagree with the one that ships — and the disagreement would surface as a
  mystery months later. It also gives the log-line contract a real reader: change its fields and
  this benchmark reds.
* **The verdict needs UNANIMITY across the samples, and a near-boundary result is `unresolved`
  rather than rounded to the convenient side.** Wall-clock on a loaded machine cannot separate
  1.9s from 2.1s, and the gate decides whether a whole atom gets built.
  `fanout_measure.INCONCLUSIVE_BAND_POINTS` set the precedent in this harness: a band the
  measurement cannot see through returns "unresolved" rather than a verdict. Narrowing the band to
  extract a decision is the move that precedent exists to forbid.
* **A repo under :data:`BENCHMARK_MIN_FILES` cannot deliver the gate verdict at all.** §1.1 names
  the benchmark case (a 10K-file repo, fan-out of 4); a fast result on a small repo says nothing
  about it, so the gate reports `unresolved` with the reason rather than an unearned `skip`.

The synthetic repo exists because the measurement has to be reproducible by someone who does not
have a 10K-file checkout handy: :func:`synthesize_repo` builds one deterministically (content is a
function of the index, so two runs produce byte-identical blobs and comparable timings).

**Telling hydration apart from the machine.** Two checks, because a number that measures ambient
load rather than working-tree hydration answers the wrong question and looks identical:

1. The mechanized one: the verdict requires every sample to agree (see :func:`evaluate_gate`), so
   a run whose observations straddle the gate returns `unresolved` instead of leaning on a mean
   that a single unlucky sample can move.
2. The manual one, worth running whenever the answer matters: re-run with ``--files 40`` in the
   same window. A 40-file worktree has essentially nothing to hydrate, so its per-worktree number
   is the floor — process spawn, git startup, repo metadata, ambient load. Subtract that floor
   before believing the benchmark arm is about hydration at all.

Usage::

    python -m harness worktree-bench                    # synthesize 10K files, fan out 4
    python -m harness worktree-bench --repo /path/to/repo --width 4
    python -m harness worktree-bench --contended --json  # record the load caveat + emit the dict

Never runs against the real PersonalClaw home: worktrees land under an explicit temp home and the
run refuses to proceed if that home resolves to the default one.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import statistics
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

from personalclaw.loop import worktree

#: The §1.1 threshold, verbatim: "<2s per worktree on the benchmark = skip and re-scope".
#: Not a tunable. Lowering it to make an optimization look justified is the objection that
#: deferred this plan in the first place ("worktree optimization without a real bottleneck").
GATE_MS_PER_WORKTREE = 2000.0

#: Half-width of the band around the gate in which no verdict is offered, as a fraction of the
#: gate. 20% (±400ms) is not a statistical claim; it is the honest resolution of a wall-clock
#: measurement on a machine that also runs test suites and builds. Inside it, the run reports
#: `unresolved` and says what would settle it (an idle machine, more trials).
GATE_UNRESOLVED_FRACTION = 0.20

#: The benchmark repo size §1.1 names. A smaller repo produces a real number that is simply not
#: an answer to the gate's question, so the verdict says so instead of generalizing.
BENCHMARK_MIN_FILES = 10_000

#: The fan-out width §1.1 names. Today's creation path is sequential (one `add_worktree` per READY
#: task inside the scheduler loop), so the benchmark is sequential too — measuring a pooled
#: creation that does not exist yet would be measuring `HC-2` instead of justifying it.
DEFAULT_WIDTH = 4

VERDICT_PROCEED = "proceed"
VERDICT_SKIP_AND_RESCOPE = "skip_and_rescope"
VERDICT_UNRESOLVED = "unresolved"

#: Every verdict this module can return. Exported so a caller asserts the vocabulary instead of
#: string-matching, and so `unresolved` is a first-class outcome rather than an error path.
VERDICTS = frozenset({VERDICT_PROCEED, VERDICT_SKIP_AND_RESCOPE, VERDICT_UNRESOLVED})

#: Files per directory in the synthetic repo. Real checkouts are not one flat directory, and a
#: flat one measures a different filesystem behavior (one huge dirent scan) than the tree git
#: actually hydrates.
_SYNTH_FILES_PER_DIR = 100


class BenchmarkError(RuntimeError):
    """The benchmark could not run (no git, an unusable repo, the real home as a target)."""


# ── the log-line contract (the benchmark's input) ──

#: Parser for `personalclaw.loop.worktree`'s timing line. Anchored on the module's own prefix
#: constant rather than a copied string, so a renamed prefix is a red test here and not a
#: silently-empty report.
_ROW_RE = re.compile(
    re.escape(worktree.TIMING_LOG_PREFIX) + r"\s+outcome=(?P<outcome>\S+)\s+task=(?P<task>\S+)"
    r"\s+ms=(?P<ms>-?\d+)\s+files=(?P<files>-?\d+)\s+size_class=(?P<size_class>\S+)"
)


@dataclass(frozen=True)
class TimingRow:
    """One parsed worktree timing line."""

    outcome: str
    task: str
    ms: int
    files: int
    size_class: str


def parse_timing_line(line: str) -> TimingRow | None:
    """Parse one timing line, or None if the line is not one.

    Strict about the field set: a line missing `size_class` or `files` does not parse at all
    rather than parsing with a default. A tolerant parser here would let the instrumentation lose
    its size tag and still produce a plausible-looking report — the exact defect the tag exists
    to prevent (two repos' numbers averaged together).
    """
    m = _ROW_RE.search(line or "")
    if m is None:
        return None
    return TimingRow(
        outcome=m.group("outcome"),
        task=m.group("task"),
        ms=int(m.group("ms")),
        files=int(m.group("files")),
        size_class=m.group("size_class"),
    )


class _RowCollector(logging.Handler):
    """Collects parsed timing rows off the worktree logger."""

    def __init__(self, rows: list[TimingRow]) -> None:
        super().__init__(level=logging.INFO)
        self._rows = rows

    def emit(self, record: logging.LogRecord) -> None:
        row = parse_timing_line(record.getMessage())
        if row is not None:
            self._rows.append(row)


@contextmanager
def collect_timing_rows() -> Iterator[list[TimingRow]]:
    """Capture the worktree timing rows emitted inside the block.

    Attaches to the module's own logger and forces it to INFO for the duration — a benchmark that
    silently collected nothing because the root logger was at WARNING would report a fan-out of
    zero worktrees as a clean run.
    """
    rows: list[TimingRow] = []
    log = logging.getLogger(worktree.__name__)
    handler = _RowCollector(rows)
    prior_level, prior_disabled = log.level, log.disabled
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.disabled = False
    try:
        yield rows
    finally:
        log.removeHandler(handler)
        log.setLevel(prior_level)
        log.disabled = prior_disabled


# ── the gate ──


@dataclass
class GateVerdict:
    """The §1.1 measure-first decision plus the reasoning that produced it."""

    verdict: str
    notes: list[str] = field(default_factory=list)

    @property
    def conclusive(self) -> bool:
        return self.verdict != VERDICT_UNRESOLVED

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "notes": list(self.notes)}


def evaluate_gate(samples_ms: Sequence[int], *, repo_files: int, width: int) -> GateVerdict:
    """Apply §1.1's gate to the per-worktree observations, requiring UNANIMITY.

    The gate takes the samples, not their mean, and that is the central decision in this module.
    A four-sample fan-out on a shared machine is right-skewed — the first worktree pays a cold
    page cache and any concurrent load lands on whichever sample is unlucky — so there is no
    trustworthy central estimate to compare against a threshold. What IS trustworthy is agreement:

    * every sample above `gate + band` → `proceed`. Upper-tail noise cannot change this, because
      even the CHEAPEST worktree observed was over the line.
    * every sample below `gate - band` → `skip_and_rescope`, for the mirror reason.
    * anything else STRADDLES the decision → `unresolved`, naming where it straddled.

    Measured, and the reason the rule is this and not a mean-vs-threshold test: HC-1's own
    benchmark run produced samples from 4.2s to 12.0s. A mean (6.5s) with a spread larger than
    itself looks like pure noise, and a spread-based rule refused to answer — yet every one of
    those four observations was more than twice the gate, and a same-window 40-file control arm
    came in at 276ms with a 53ms spread. The noise was entirely in the tail; the floor was never
    in question. Deciding on the mean would have been luck, and refusing on the spread would have
    thrown away a unanimous result.

    A measurement taken on a repo too small to be the benchmark, or at a narrower fan-out than
    §1.1 names, is `unresolved` regardless of what it says.
    """
    notes: list[str] = []
    band = GATE_MS_PER_WORKTREE * GATE_UNRESOLVED_FRACTION
    lower, upper = GATE_MS_PER_WORKTREE - band, GATE_MS_PER_WORKTREE + band
    if not samples_ms:
        notes.append(
            "no worktree reported a creation duration — nothing was measured, which is not the "
            "same as nothing being slow"
        )
        return GateVerdict(VERDICT_UNRESOLVED, notes)
    if repo_files < BENCHMARK_MIN_FILES:
        notes.append(
            f"repo has {repo_files} tracked files, under the {BENCHMARK_MIN_FILES}-file benchmark "
            f"case §1.1 names — these are real numbers about a different repo, so they cannot "
            "open or close the gate"
        )
        return GateVerdict(VERDICT_UNRESOLVED, notes)
    if width < DEFAULT_WIDTH:
        notes.append(
            f"fan-out width {width} is narrower than the benchmark's {DEFAULT_WIDTH}; a narrower "
            "fan-out pays less contention than the case the gate is about"
        )
        return GateVerdict(VERDICT_UNRESOLVED, notes)
    lo, hi = min(samples_ms), max(samples_ms)
    if lo > upper:
        notes.append(
            f"all {len(samples_ms)} worktrees cost more than the {GATE_MS_PER_WORKTREE:.0f}ms "
            f"gate — the CHEAPEST was {lo}ms, {lo - GATE_MS_PER_WORKTREE:.0f}ms over, so no amount "
            "of upper-tail noise can move the verdict: hydration is a measured bottleneck and "
            "§1.2 (sparse + pooled + reuse) is justified — proceed to HC-2"
        )
        return GateVerdict(VERDICT_PROCEED, notes)
    if hi < lower:
        notes.append(
            f"all {len(samples_ms)} worktrees cost less than the {GATE_MS_PER_WORKTREE:.0f}ms "
            f"gate — the most expensive was {hi}ms — so there is no measured bottleneck: §1's "
            "remaining items are SKIPPED and re-scoped (§1.1); the instrumentation ships regardless"
        )
        return GateVerdict(VERDICT_SKIP_AND_RESCOPE, notes)
    notes.append(
        f"the samples straddle the gate ({lo}ms…{hi}ms against {GATE_MS_PER_WORKTREE:.0f}ms "
        f"±{band:.0f}ms), so the observations do not agree on an answer — unresolved rather than "
        "rounded to whichever side the mean happens to land on; re-measure on an idle machine and "
        "cross-check with a small-repo control arm in the same window"
    )
    return GateVerdict(VERDICT_UNRESOLVED, notes)


# ── the measurement ──


@dataclass
class FanOutBaseline:
    """A recorded fan-out-of-N baseline: the durations the shipped log line reported."""

    repo: str
    repo_files: int
    size_class: str
    width: int
    rows: list[TimingRow] = field(default_factory=list)
    contended: bool = False

    @property
    def created_ms(self) -> list[int]:
        """Durations of the rows that actually hydrated a working tree."""
        return [r.ms for r in self.rows if r.outcome == worktree.OUTCOME_CREATED]

    @property
    def mean_ms(self) -> float:
        vals = self.created_ms
        return statistics.fmean(vals) if vals else 0.0

    @property
    def median_ms(self) -> float:
        vals = self.created_ms
        return statistics.median(vals) if vals else 0.0

    @property
    def max_ms(self) -> int:
        vals = self.created_ms
        return max(vals) if vals else 0

    @property
    def total_ms(self) -> int:
        """Wall-clock a sequential fan-out of this width costs — the number a user waits."""
        return sum(self.created_ms)

    @property
    def spread_ms(self) -> int:
        """max − min across the creation samples: the arm's own variance.

        Reported beside the mean rather than behind it, following ``fanout_measure``'s rule that a
        delta smaller than the variance it sits in is unresolved. On a busy machine this is the
        number that decides whether the mean is a measurement or an average of noise.
        """
        vals = self.created_ms
        return max(vals) - min(vals) if vals else 0

    @property
    def outcomes(self) -> dict[str, int]:
        """Row count per outcome — how the fan-out actually ENDED, not just how fast.

        Reported because the first real run of this benchmark found the answer here and not in the
        mean: a width-4 fan-out on a 10K-file repo produced three creations and one **failure**,
        the 30s ``_TIMEOUT`` in ``loop/worktree.py`` firing on the last one. A report that showed
        only the mean would have presented that as "3 samples" and buried the most important fact
        the measurement produced.
        """
        tally: dict[str, int] = {}
        for row in self.rows:
            tally[row.outcome] = tally.get(row.outcome, 0) + 1
        return tally

    @property
    def failed_ms(self) -> list[int]:
        """Durations of rows that ended in failure — a timeout shows up here at ~``_TIMEOUT``."""
        return [r.ms for r in self.rows if r.outcome == worktree.OUTCOME_FAILED]

    def gate(self) -> GateVerdict:
        verdict = evaluate_gate(self.created_ms, repo_files=self.repo_files, width=self.width)
        if self.failed_ms:
            verdict.notes.append(
                f"{len(self.failed_ms)} of {self.width} worktrees FAILED after "
                f"{'/'.join(str(m) for m in self.failed_ms)}ms — at or near the "
                f"{worktree._TIMEOUT}s git timeout, i.e. the fan-out did not just get slow, it "
                "lost a worker; a failed `add_worktree` degrades that task to no worktree at all"
            )
        if len(self.created_ms) < self.width:
            verdict.notes.append(
                f"only {len(self.created_ms)} of {self.width} worktrees reported a creation row "
                f"(outcomes: {self.outcomes}), so the mean covers fewer samples than the width"
            )
        if self.contended:
            verdict.notes.append(
                "measured on a machine under concurrent load — timings are PESSIMISTIC, so a "
                "`proceed` may be load and a `skip_and_rescope` is the safer direction to trust"
            )
        return verdict

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "repo_files": self.repo_files,
            "size_class": self.size_class,
            "width": self.width,
            "outcomes": self.outcomes,
            "created_ms": self.created_ms,
            "failed_ms": self.failed_ms,
            "mean_ms": round(self.mean_ms, 1),
            "median_ms": round(self.median_ms, 1),
            "max_ms": self.max_ms,
            "spread_ms": self.spread_ms,
            "total_ms": self.total_ms,
            "contended": self.contended,
            "gate": self.gate().to_dict(),
        }


def _run(cwd: Path, *argv: str) -> None:
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[:400]
        raise BenchmarkError(f"{' '.join(argv)} failed in {cwd}: {detail}")


def synthesize_repo(root: str | Path, files: int = BENCHMARK_MIN_FILES) -> Path:
    """Build a deterministic git repo of ``files`` tracked files under ``root``.

    Deterministic content (a function of the file's index) so two synthesis runs produce identical
    blobs: a benchmark whose input differs run to run cannot be re-run to check a number. Files
    are spread :data:`_SYNTH_FILES_PER_DIR` per directory because a real checkout is a tree, and a
    single flat directory would measure one huge dirent scan instead of the tree git hydrates.
    """
    if files <= 0:
        raise BenchmarkError(f"a benchmark repo needs at least one file, got {files}")
    if not worktree.git_available():
        raise BenchmarkError("git is not on PATH; the worktree benchmark cannot run")
    repo = Path(root)
    repo.mkdir(parents=True, exist_ok=True)
    _run(repo, "git", "init", "-q")
    for i in range(files):
        d = repo / f"pkg{i // _SYNTH_FILES_PER_DIR:04d}"
        d.mkdir(exist_ok=True)
        # ~200 bytes each: big enough that hydration is real file I/O, small enough that 10K
        # files stay a ~2MB checkout rather than something the disk cache cannot hold.
        (d / f"mod{i % _SYNTH_FILES_PER_DIR:04d}.py").write_text(
            f'"""synthetic module {i}."""\n\nVALUE = {i}\n\n\ndef f{i}(x):\n'
            f"    return x + {i}  # {'y' * 120}\n",
            encoding="utf-8",
        )
    _run(repo, "git", "add", "-A")
    _run(
        repo,
        "git",
        "-c",
        "user.name=bench",
        "-c",
        "user.email=bench@personalclaw.local",
        "commit",
        "-qm",
        f"synthetic benchmark repo ({files} files)",
    )
    return repo


@contextmanager
def _temp_home(home: str | Path) -> Iterator[Path]:
    """Point ``PERSONALCLAW_HOME`` at ``home`` for the block, refusing the real home.

    Worktrees are created under ``config_dir()``, so a benchmark that ran with the ambient home
    would litter the user's real PersonalClaw directory with synthetic worktrees and branches —
    and its teardown would then be deleting things inside it.
    """
    target = Path(home).expanduser().resolve()
    default = (Path.home() / ".personalclaw").resolve()
    if target == default:
        raise BenchmarkError(
            f"refusing to run the benchmark against the real home {default}; pass a temp dir"
        )
    target.mkdir(parents=True, exist_ok=True)
    prior = os.environ.get("PERSONALCLAW_HOME")
    os.environ["PERSONALCLAW_HOME"] = str(target)
    try:
        yield target
    finally:
        if prior is None:
            os.environ.pop("PERSONALCLAW_HOME", None)
        else:
            os.environ["PERSONALCLAW_HOME"] = prior


def measure_fanout(
    repo: str | Path,
    home: str | Path,
    *,
    width: int = DEFAULT_WIDTH,
    contended: bool = False,
) -> FanOutBaseline:
    """Create ``width`` worktrees off ``repo`` sequentially and report what the log line said.

    Cleans up its own worktrees. The repo itself is left alone — the caller owns it, and a
    benchmark that deleted a repo it was pointed at would be one `--repo` typo away from deleting
    a user's checkout.
    """
    repo_path = Path(repo).resolve()
    if width <= 0:
        raise BenchmarkError(f"fan-out width must be positive, got {width}")
    if not worktree.git_available():
        raise BenchmarkError("git is not on PATH; the worktree benchmark cannot run")
    with _temp_home(home):
        if not worktree.is_git_repo(str(repo_path)):
            raise BenchmarkError(f"{repo_path} is not a git repo")
        if not worktree.ensure_base_commit(str(repo_path)):
            raise BenchmarkError(f"{repo_path} has no commit to branch from")
        # Prime the size-class cache OUTSIDE the measured calls, exactly as production does after
        # its first creation — otherwise the first row of every benchmark carries a `git ls-files`
        # the later rows do not, and the mean would be reporting the instrumentation.
        files = worktree.repo_file_count(str(repo_path))
        try:
            with collect_timing_rows() as rows:
                for i in range(width):
                    worktree.add_worktree(str(repo_path), f"t-bench{i:03d}")
            return FanOutBaseline(
                repo=str(repo_path),
                repo_files=files,
                size_class=worktree.size_class(files),
                width=width,
                rows=list(rows),
                contended=contended,
            )
        finally:
            worktree.cleanup_all(str(repo_path))


def run_benchmark(
    *,
    repo: str | Path | None = None,
    files: int = BENCHMARK_MIN_FILES,
    width: int = DEFAULT_WIDTH,
    contended: bool = False,
) -> FanOutBaseline:
    """Measure a fan-out, synthesizing a benchmark repo under a temp dir when none is given.

    Everything the benchmark creates — the synthetic repo and the PClaw home holding the
    worktrees — lives in one temp directory that is removed on the way out, so nothing it measures
    can end up in the committed tree.
    """
    if repo is not None:
        with tempfile.TemporaryDirectory(prefix="pclaw-wt-bench-home-") as home:
            return measure_fanout(repo, home, width=width, contended=contended)
    with tempfile.TemporaryDirectory(prefix="pclaw-wt-bench-") as tmp:
        synthesized = synthesize_repo(Path(tmp) / "repo", files=files)
        try:
            return measure_fanout(synthesized, Path(tmp) / "home", width=width, contended=contended)
        finally:
            shutil.rmtree(synthesized, ignore_errors=True)
