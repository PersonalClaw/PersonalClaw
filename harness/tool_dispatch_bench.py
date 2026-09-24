"""Concurrent tool-dispatch benchmark + HC-6's before/after gate (HARNESS-CRAFT HC-6).

HC-6 is a concurrency change, and a concurrency change with no before-number cannot be
shown to have helped — so it inherits HC-1's measure-first discipline rather than inventing
a second one. The three decisions HC-1 made structurally are made the same way here:

* **The benchmark reads the SHIPPED log line; it does not keep its own stopwatch.**
  Durations come from parsing :data:`~personalclaw.agents.native.dispatch_plan.
  TIMING_LOG_PREFIX`'s ``tool batch`` row, so the number in this report is the number
  production emits, and the log-line contract gets a real reader — change its fields and
  this benchmark reds.
* **The verdict needs UNANIMITY, and a near-boundary result is `unresolved`.** Every
  concurrent sample must beat every serial sample by more than the band. A mean-vs-mean
  comparison on a machine that also runs test suites is luck, not a measurement, and this
  gate decides whether a concurrency change is claimed to have worked.
* **The baseline arm is production code, not a simulation of it.** It is the same runtime
  with ``max_tool_concurrency=1``, which makes every wave one call wide — i.e. exactly the
  pre-HC-6 dispatch. A separate "serial mode" written for the benchmark would be free to
  differ from the real serial path, and the whole comparison would rest on that difference.

The turn being measured, :data:`MULTI_LOOKUP_TURN`, is a representative multi-lookup turn:
eight independent read-only lookups — three greps, two globs, a repo map and two file
reads — no two of which touch the same path, which is the shape the atom is about. It is
deliberately all-reads: a write in the turn would serialize against the readers of its path
and the benchmark would then be measuring the reservation rule instead of the concurrency.

Usage::

    python -m harness dispatch-bench                  # synthesize a repo, both arms
    python -m harness dispatch-bench --trials 7 --json
    python -m harness dispatch-bench --repo /path/to/repo --contended

Never runs against the real PersonalClaw home: the synthesized repo and everything the
turn touches live under a temp dir that is removed on the way out.
"""

from __future__ import annotations

import asyncio
import logging
import re
import statistics
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence, cast

from personalclaw.agents.native import dispatch_plan
from personalclaw.agents.native import runtime as native_runtime
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TOOL_CALL, AgentEvent

#: Trials per arm. Seven because the gate is unanimity over the samples and a three-sample
#: arm on a loaded machine can be unanimous by luck.
DEFAULT_TRIALS = 7

#: Files in the synthesized repo. Large enough that a grep and a repo_map are real work
#: (the whole point — concurrency only shows up when the calls cost something), small
#: enough that a 2-arm × 7-trial run stays under a minute.
DEFAULT_FILES = 1200

#: Half-width of the band around "no change", as a fraction of the serial baseline's MEDIAN.
#: Inside it the run reports `unresolved`: 15% of a wall-clock measurement on a shared
#: machine is not a statistical claim, it is the honest resolution of the instrument.
#: Narrowing it to extract a "yes, faster" is the move HC-1's band exists to forbid.
#:
#: Of the serial arm's MEDIAN and not its mean, for the reason HC-1's own gate gives for
#: not deciding on a mean: a fan-out arm is right-skewed, so one cold-cache or
#: contention-hit sample moves the mean by more than the effect being measured. Keyed to
#: the mean, a single 10s outlier in a ~1.3s arm inflated the band to 419ms and reported
#: `unresolved` on a run whose arms did not overlap at all — noise in the baseline's TAIL
#: deciding a question about its FLOOR. The median is a scale estimate; the mean, here,
#: is not.
GATE_BAND_FRACTION = 0.15

VERDICT_IMPROVED = "improved"
VERDICT_NO_IMPROVEMENT = "no_improvement"
VERDICT_UNRESOLVED = "unresolved"

#: Every verdict this module can return, so a caller asserts the vocabulary instead of
#: string-matching, and `unresolved` is a first-class outcome rather than an error path.
VERDICTS = frozenset({VERDICT_IMPROVED, VERDICT_NO_IMPROVEMENT, VERDICT_UNRESOLVED})

#: The representative multi-lookup turn: eight independent read-only lookups.
MULTI_LOOKUP_TURN: tuple[tuple[str, str], ...] = (
    ("grep", '{"query": "VALUE = 41"}'),
    ("grep", '{"query": "def f900"}'),
    ("grep", '{"query": "synthetic module 12"}'),
    ("glob", '{"pattern": "**/mod00*.py"}'),
    ("glob", '{"pattern": "pkg001/*.py"}'),
    ("repo_map", '{"max_files": 300}'),
    ("read_file", '{"path": "pkg0000/mod0000.py"}'),
    ("read_file", '{"path": "pkg0001/mod0001.py"}'),
)


class BenchmarkError(RuntimeError):
    """The benchmark could not run (no git, an unusable repo, no timing row emitted)."""


# ── the log-line contract (the benchmark's input) ──

#: Parser for the runtime's dispatch timing line. Anchored on the module's own prefix
#: constant rather than a copied string, so a renamed prefix is a red test here and not a
#: silently-empty report.
_ROW_RE = re.compile(
    re.escape(dispatch_plan.TIMING_LOG_PREFIX) + r"\s+mode=(?P<mode>\S+)\s+calls=(?P<calls>\d+)"
    r"\s+waves=(?P<waves>\d+)\s+widest=(?P<widest>\d+)\s+ms=(?P<ms>-?\d+)"
)


@dataclass(frozen=True)
class DispatchRow:
    """One parsed tool-batch timing line."""

    mode: str
    calls: int
    waves: int
    widest: int
    ms: int


def parse_timing_line(line: str) -> DispatchRow | None:
    """Parse one dispatch timing line, or None if the line is not one.

    Strict about the field set, for HC-1's reason: a tolerant parser would let the
    instrumentation lose ``mode`` or ``widest`` and still produce a plausible report — and
    those two fields are the only thing distinguishing the baseline arm from the after arm.
    """
    m = _ROW_RE.search(line or "")
    if m is None:
        return None
    return DispatchRow(
        mode=m.group("mode"),
        calls=int(m.group("calls")),
        waves=int(m.group("waves")),
        widest=int(m.group("widest")),
        ms=int(m.group("ms")),
    )


class _RowCollector(logging.Handler):
    """Collects parsed dispatch rows off the native runtime's logger."""

    def __init__(self, rows: list[DispatchRow]) -> None:
        super().__init__(level=logging.INFO)
        self._rows = rows

    def emit(self, record: logging.LogRecord) -> None:
        row = parse_timing_line(record.getMessage())
        if row is not None:
            self._rows.append(row)


@contextmanager
def collect_timing_rows() -> Iterator[list[DispatchRow]]:
    """Capture the dispatch timing rows emitted inside the block.

    Forces the runtime's own logger to INFO for the duration — a benchmark that silently
    collected nothing because the root logger sat at WARNING would report an empty run as
    a clean one.
    """
    rows: list[DispatchRow] = []
    log = logging.getLogger(native_runtime.__name__)
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
    """HC-6's before/after decision plus the reasoning that produced it."""

    verdict: str
    notes: list[str] = field(default_factory=list)

    @property
    def conclusive(self) -> bool:
        return self.verdict != VERDICT_UNRESOLVED

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "notes": list(self.notes)}


def evaluate_gate(serial_ms: Sequence[int], concurrent_ms: Sequence[int]) -> GateVerdict:
    """Is the improvement REAL on this benchmark, rather than assumed?

    Unanimity, for HC-1's reason: a fan-out on a shared machine is right-skewed, so there is
    no trustworthy central estimate to compare against a threshold — but agreement between
    the arms IS trustworthy.

    * every concurrent sample below ``min(serial) - band`` → ``improved``. Upper-tail noise
      cannot change this: even the SLOWEST concurrent turn beat the FASTEST serial one.
    * every concurrent sample above ``max(serial) + band`` → ``no_improvement``, for the
      mirror reason (and it is a real result, not an error — a concurrency change that does
      not pay is a finding).
    * anything else OVERLAPS → ``unresolved``, naming where.

    The band is a fraction of the serial baseline rather than an absolute, because the same
    absolute millisecond gap means something different for a 200ms turn and a 20s one.
    """
    notes: list[str] = []
    if not serial_ms or not concurrent_ms:
        notes.append(
            "one arm produced no timing row — nothing was measured, which is not the same "
            "as nothing being faster"
        )
        return GateVerdict(VERDICT_UNRESOLVED, notes)
    s_lo, s_hi = min(serial_ms), max(serial_ms)
    c_lo, c_hi = min(concurrent_ms), max(concurrent_ms)
    band = statistics.median(serial_ms) * GATE_BAND_FRACTION
    if c_hi < s_lo - band:
        notes.append(
            f"every concurrent turn beat every serial turn — the SLOWEST concurrent was "
            f"{c_hi}ms against a FASTEST serial of {s_lo}ms, {s_lo - c_hi}ms of daylight "
            f"against a {band:.0f}ms band, so no amount of tail noise can move the verdict: "
            f"the improvement is measured, not assumed "
            f"({statistics.fmean(serial_ms) / statistics.fmean(concurrent_ms):.2f}x on the means)"
        )
        return GateVerdict(VERDICT_IMPROVED, notes)
    if c_lo > s_hi + band:
        notes.append(
            f"every concurrent turn was SLOWER than every serial turn — the fastest "
            f"concurrent was {c_lo}ms against a slowest serial of {s_hi}ms. Concurrency is "
            "costing more than it saves on this benchmark; that is a finding, not a failure"
        )
        return GateVerdict(VERDICT_NO_IMPROVEMENT, notes)
    notes.append(
        f"the arms overlap (serial {s_lo}ms…{s_hi}ms against concurrent {c_lo}ms…{c_hi}ms, "
        f"band {band:.0f}ms), so the observations do not agree on an answer — unresolved "
        "rather than rounded to whichever side the means happen to land on; re-measure on an "
        "idle machine, or with more trials"
    )
    return GateVerdict(VERDICT_UNRESOLVED, notes)


# ── the measurement ──


@dataclass
class DispatchBaseline:
    """Both arms of a recorded before/after: the durations the shipped log line reported."""

    repo: str
    calls: int
    trials: int
    serial_rows: list[DispatchRow] = field(default_factory=list)
    concurrent_rows: list[DispatchRow] = field(default_factory=list)
    contended: bool = False

    @property
    def serial_ms(self) -> list[int]:
        return [r.ms for r in self.serial_rows]

    @property
    def concurrent_ms(self) -> list[int]:
        return [r.ms for r in self.concurrent_rows]

    @property
    def speedup(self) -> float:
        """Mean serial ÷ mean concurrent. Reported BESIDE the samples, never instead of
        them — a ratio of two means hides which arm's spread produced it."""
        s, c = self.serial_ms, self.concurrent_ms
        if not s or not c:
            return 0.0
        cm = statistics.fmean(c)
        return statistics.fmean(s) / cm if cm else 0.0

    def gate(self) -> GateVerdict:
        verdict = evaluate_gate(self.serial_ms, self.concurrent_ms)
        widths = {r.widest for r in self.concurrent_rows}
        if widths and widths <= {1}:
            verdict.notes.append(
                "the CONCURRENT arm's every wave was one call wide — the partition found no "
                "two disjoint calls, so this run compared serial against serial and any "
                "difference is noise"
            )
        if self.contended:
            verdict.notes.append(
                "measured on a machine under concurrent load — both arms are pessimistic, "
                "and a serial arm pays that load per call while a concurrent arm pays it "
                "per wave, so `improved` is the direction load flatters; re-check on an "
                "idle machine before quoting the ratio"
            )
        return verdict

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "calls": self.calls,
            "trials": self.trials,
            "serial_ms": self.serial_ms,
            "concurrent_ms": self.concurrent_ms,
            "serial_mean_ms": round(statistics.fmean(self.serial_ms), 1) if self.serial_ms else 0.0,
            "concurrent_mean_ms": (
                round(statistics.fmean(self.concurrent_ms), 1) if self.concurrent_ms else 0.0
            ),
            "waves": sorted({r.waves for r in self.concurrent_rows}),
            "widest": sorted({r.widest for r in self.concurrent_rows}),
            "speedup": round(self.speedup, 2),
            "contended": self.contended,
            "gate": self.gate().to_dict(),
        }


class _ScriptedModel:
    """A ModelProvider that asks for :data:`MULTI_LOOKUP_TURN` once, then stops.

    A real provider cannot be in this measurement: its latency and variance would dwarf the
    dispatch section, and the arms would differ by network weather rather than by dispatch.
    """

    supports_tools = True
    _model = "scripted"

    def __init__(self, turn: Sequence[tuple[str, str]]) -> None:
        self._turn = tuple(turn)
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.calls += 1
        if self.calls == 1:
            for i, (name, args) in enumerate(self._turn):
                yield AgentEvent(
                    kind=EVENT_TOOL_CALL, tool_call_id=f"c{i}", title=name, tool_input=args
                )
        yield AgentEvent(kind=EVENT_COMPLETE)


async def _one_turn(repo: Path, *, max_concurrency: int) -> None:
    """Drive one real turn of the real runtime over ``repo``. Timing is the log line's."""
    rt = native_runtime.NativeAgentRuntime(
        definition=AgentRuntimeDefinition(
            name="dispatch-bench", provider="native", model="scripted"
        ),
        # cast: the scripted stand-in implements the one method the loop calls
        # (`complete`) plus the two attributes it reads, which is the whole of the
        # contract this measurement exercises.
        model_provider=cast(Any, _ScriptedModel(MULTI_LOOKUP_TURN)),
        tool_providers=[NativeBuiltinToolProvider(cwd=repo)],
        cwd=repo,
        max_tool_concurrency=max_concurrency,
    )
    await rt.start()
    async for _ev in rt.stream("look these up"):
        pass


async def measure(repo: str | Path, *, trials: int, contended: bool = False) -> DispatchBaseline:
    """Run both arms over ``repo``, baseline (``max_tool_concurrency=1``) first.

    Baseline first, deliberately: it is the number the change has to beat, and measuring it
    second on a machine whose load is drifting would let the drift flatter the arm measured
    in the quieter window.
    """
    repo_path = Path(repo).resolve()
    if trials <= 0:
        raise BenchmarkError(f"trials must be positive, got {trials}")
    # One un-recorded warm turn: the first grep of a fresh checkout pays a cold page cache,
    # and whichever arm ran first would otherwise carry it alone.
    await _one_turn(repo_path, max_concurrency=1)
    with collect_timing_rows() as serial:
        for _ in range(trials):
            await _one_turn(repo_path, max_concurrency=1)
    serial_rows = list(serial)
    with collect_timing_rows() as concurrent:
        for _ in range(trials):
            await _one_turn(repo_path, max_concurrency=dispatch_plan.MAX_CONCURRENT_CALLS)
    concurrent_rows = list(concurrent)
    if not serial_rows or not concurrent_rows:
        raise BenchmarkError(
            "the runtime emitted no dispatch timing row — the instrumentation is gone or its "
            f"prefix changed away from {dispatch_plan.TIMING_LOG_PREFIX!r}"
        )
    return DispatchBaseline(
        repo=str(repo_path),
        calls=len(MULTI_LOOKUP_TURN),
        trials=trials,
        serial_rows=serial_rows,
        concurrent_rows=concurrent_rows,
        contended=contended,
    )


def run_benchmark(
    *,
    repo: str | Path | None = None,
    files: int = DEFAULT_FILES,
    trials: int = DEFAULT_TRIALS,
    contended: bool = False,
) -> DispatchBaseline:
    """Measure both arms, synthesizing a benchmark repo under a temp dir when none is given.

    Reuses HC-1's :func:`~harness.worktree_bench.synthesize_repo` rather than growing a
    second synthetic-repo builder: two of them would drift, and a benchmark whose input
    differs run to run cannot be re-run to check a number.
    """
    from harness.worktree_bench import BenchmarkError as WtError
    from harness.worktree_bench import synthesize_repo

    if repo is not None:
        return asyncio.run(measure(repo, trials=trials, contended=contended))
    with tempfile.TemporaryDirectory(prefix="pclaw-dispatch-bench-") as tmp:
        try:
            synthesized = synthesize_repo(Path(tmp) / "repo", files=files)
        except WtError as exc:  # a missing git / an unusable temp dir
            raise BenchmarkError(str(exc)) from exc
        return asyncio.run(measure(synthesized, trials=trials, contended=contended))
