"""Split the suite into whole-file shards by recorded per-file durations.

A shard's job runs pytest over its own list of test files, so each worker collects only that
shard's files, never the whole suite. Every shard of a leg reads one plan, made once per run
(``plan``), so a file runs in exactly one shard of the leg: no file is dropped or run twice
(``check``). Modules stay whole, so a module- or class-scoped fixture is set up in one shard.

The plan is longest-processing-time greedy over per-file seconds: descending duration, ties
broken by path, each file into the lightest bucket. A file with no record weighs the median of
the recorded files. The same files and durations always give the same buckets.

**The durations source is #3948's telemetry record.** :class:`TelemetryRecord` reads each file's
median first-attempt seconds over the leg's own reports in the newest three main runs whose
history ``tools/ci/telemetry.py`` wrote, the one writer of CI telemetry, and nothing else. Until
the first collected main run there is no record, and the plan uses equal weights and says so. A
file whose runs differ by half its median or more (:data:`WIDE`) weighs its 75th percentile over
them instead (:func:`typical`): a file that swings is planned nearer its slow runs, since the
slowest shard is what the run waits for (#3923).

**Seconds are weighed by the host they ran on.** GitHub's Linux hosts come in CPU models whose
speeds differ by about 1.5x, and every file of a shard runs slower together on a slower one. So
each report's seconds are divided by its host model's factor before the median is taken: the
median, over the input runs' reports on that model, of what the report ran against what its
files' plain medians planned. Factors come from the input runs only, never from the run being
planned, and a report whose model was not recorded weighs as it ran (a factor of 1.0).
Durations only balance the shards: ``shards check`` is what proves every file runs once.

Standard library only, like every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import os
import statistics
import subprocess
import sys
import tomllib
import zipfile
import zlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import telemetry, workflows

VERSION = 1

#: Directories pytest's default ``norecursedirs`` skips, besides any starting with a dot.
_SKIPPED_DIRS = frozenset({"__pycache__", "node_modules", "build", "dist", "venv", "CVS"})

#: The rails' folder, run by the ``rails`` job and never by a shard (``rails_census.RAILS``).
RAILS = "tests/rails"

#: The test files a job runs by name, and so no shard collects (#3929): each runs once per run,
#: in the job that owns it. ``file: "catalogue job"``, the job's id in ``tools/ci/catalogue.toml``,
#: which ``ci.yml``'s jobs and the tier workflows' both run. A rail
#: (``tests/rails/test_each_tree_check_runs_in_one_place.py``) holds that each owner's command
#: still runs its file by name, so leaving a file out here never leaves it running nowhere.
JOB_OWNED: Mapping[str, str] = {
    "tests/test_website_installer.py": "static",
    "tests/test_npm_advisory_floors.py": "rails-checks",
    "tests/security/test_app_data_reads_never_follow_links.py": "security",
    "tests/security/test_app_installs_what_was_scanned.py": "security",
    "tests/security/test_app_signature.py": "security",
    "tests/security/test_app_staging_links.py": "security",
    "tests/security/test_mode_independence.py": "security",
    "tests/security/test_scanner_adversarial.py": "security",
    "tests/security/test_scanner_judges_what_the_app_runs.py": "security",
    "tests/security/test_scanner_reachability.py": "security",
    "tests/security/test_scanner_recall.py": "security",
    "tests/security/test_scanner_runtime_use.py": "security",
    "tests/test_supply_chain_gates.py": "security",
    "tests/test_supply_chain_scanner.py": "security",
}


# ── the universe ──────────────────────────────────────────────────────────────────────────────


def _pytest_options(root: Path) -> Mapping[str, object]:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    options = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    return options if isinstance(options, Mapping) else {}


def universe(root: Path, owned: Mapping[str, str] | None = None) -> list[str]:
    """Every file pytest collects under ``testpaths`` by ``python_files`` (its defaults,
    ``test_*.py`` and ``*_test.py``, when the config names none), as sorted posix paths
    relative to ``root``. A future ``foo_test.py`` cannot fall outside every shard.

    Two things are excluded, so ``check`` proves no shard of any leg lists one:

    - ``tests/rails``, the rails, which the ``rails`` job runs once per run
      (``tools/ci/rails_census.py``);
    - the files in ``owned`` (:data:`JOB_OWNED` unless given), each of which its catalogue job
      runs by name.

    The security corpus is among them: the ``security`` job runs it on every run, and the merge
    tier requires that job (``catalogue.MERGE_SECURITY_JOBS``).
    """
    skipped = JOB_OWNED if owned is None else owned
    options = _pytest_options(root)
    named_paths = options.get("testpaths", ["."])
    named_files = options.get("python_files", ["test_*.py", "*_test.py"])
    testpaths = [str(p) for p in named_paths] if isinstance(named_paths, list) else ["."]
    patterns = [str(p) for p in named_files] if isinstance(named_files, list) else []
    found: set[str] = set()
    for testpath in testpaths:
        base = root / testpath
        for current, dirs, names in os.walk(base):
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith(".")
                and d not in _SKIPPED_DIRS
                and (Path(current) / d).relative_to(root).as_posix() != RAILS
            )
            for name in names:
                if name.endswith(".py") and any(fnmatch.fnmatch(name, p) for p in patterns):
                    rel = (Path(current) / name).relative_to(root).as_posix()
                    if rel not in skipped:
                        found.add(rel)
    return sorted(found)


# ── the plan ──────────────────────────────────────────────────────────────────────────────────


def weights(files: Sequence[str], durations: Mapping[str, float]) -> dict[str, float]:
    """Each file's weight: its recorded seconds, or the median of the recorded files' seconds.
    A record for a file that is gone is ignored. With no record at all, every file weighs 1."""
    recorded = {f: float(durations[f]) for f in files if f in durations}
    fallback = statistics.median(recorded.values()) if recorded else 1.0
    return {f: recorded.get(f, fallback) for f in files}


def plan(files: Sequence[str], durations: Mapping[str, float], n: int) -> list[list[str]]:
    """``n`` buckets of whole files, longest-processing-time greedy: heaviest file first (ties
    by path), each into the lightest bucket (ties by index). Each bucket is sorted."""
    if n < 1:
        raise ValueError(f"a plan needs at least one shard, not {n}")
    weight = weights(sorted(set(files)), durations)
    buckets: list[list[str]] = [[] for _ in range(n)]
    loads = [0.0] * n
    for path in sorted(weight, key=lambda f: (-weight[f], f)):
        lightest = min(range(n), key=lambda i: (loads[i], i))
        buckets[lightest].append(path)
        loads[lightest] += weight[path]
    return [sorted(bucket) for bucket in buckets]


def check(files: Sequence[str], buckets: Sequence[Sequence[str]]) -> list[str]:
    """What is wrong with ``buckets`` as a plan of ``files``: a collectable file in no shard or
    in two, and a listed file pytest would not collect. Empty when the plan is whole."""
    seen: dict[str, int] = {}
    for bucket in buckets:
        for path in bucket:
            seen[path] = seen.get(path, 0) + 1
    problems = [f"{f} is in no shard" for f in sorted(set(files) - set(seen))]
    problems += [f"{f} is in {count} shards" for f, count in sorted(seen.items()) if count > 1]
    problems += [f"{f} is listed but not collected" for f in sorted(set(seen) - set(files))]
    return problems


# ── the durations source ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Leg:
    """A suite leg the plan covers: its name, the workflow whose runs hold its junit reports
    (``main-push`` for main's push runs in either shape, :data:`workflows.MAIN_PUSH` in turn),
    the prefix those reports' artifacts are named by, and its number of shards."""

    name: str
    workflow: str
    report_prefix: str
    shards: int

    @property
    def workflows(self) -> tuple[str, ...]:
        """The workflow files whose runs are read, in turn (:func:`workflows.named`)."""
        return workflows.named(self.workflow)

    @classmethod
    def parse(cls, text: str) -> Leg:
        """``name:workflow:report-prefix:shards``, as ``plan --leg`` takes it."""
        parts = text.split(":")
        if len(parts) != 4 or not parts[3].isdigit() or int(parts[3]) < 1 or not all(parts):
            raise ValueError(f"not name:workflow:report-prefix:shards: {text!r}")
        return cls(parts[0], parts[1], parts[2], int(parts[3]))


class DurationsSource(Protocol):
    """Where the per-file seconds come from: :class:`TelemetryRecord`."""

    def durations(self, leg: Leg, files: Sequence[str]) -> tuple[dict[str, float], str]:
        """Per-file seconds for ``leg``, and one line saying where they came from."""
        ...


Text = Callable[..., str]
Binary = Callable[..., bytes]


#: The collector's history artifact, by run and attempt (``tools/ci/telemetry.py`` names it).
HISTORY_ARTIFACT = telemetry.HISTORY_ARTIFACT
#: An archive larger than this is never downloaded (``telemetry.MAX_HISTORY_ARCHIVE_BYTES``).
MAX_HISTORY_ARCHIVE_BYTES = telemetry.MAX_HISTORY_ARCHIVE_BYTES
#: A history larger than this, unzipped, is not read on: one main run's is about 49 MB.
MAX_HISTORY_BYTES = 256 * 1024 * 1024
#: One history line longer than this is refused, its line break included: the collector's one
#: bound (``telemetry.MAX_LINE_BYTES``), which it writes no line past.
MAX_HISTORY_LINE_BYTES = telemetry.MAX_LINE_BYTES


def _file_of(dotted: str, modules: Mapping[str, str]) -> str | None:
    """The test file whose dotted module path is the longest prefix of ``dotted``."""
    parts = dotted.split(".")
    for end in range(len(parts), 0, -1):
        path = modules.get(".".join(parts[:end]))
        if path is not None:
            return path
    return None


#: What a history's reports say they ran on: ``(platform, workers)``, as the suite recorded them.
Identity = tuple[str | None, int | None]

#: One run's seconds: by identity, by report, by file.
RunSeconds = dict[Identity, dict[str, dict[str, float]]]


@dataclass(frozen=True)
class History:
    """One run's first-attempt seconds of a leg's reports, and each report's host CPU model
    (None where the report recorded none)."""

    seconds: RunSeconds
    cpus: Mapping[str, str | None]


def history_seconds(archive: bytes, leg: Leg, files: Sequence[str], *, run: int) -> History:
    """Per-file first-attempt seconds of ``leg``'s reports in one history archive, by the
    platform and worker count the reports recorded, so seconds measured on one never weigh
    another, and by report, with the CPU model the run line names for each. Read line by line,
    each line within :data:`MAX_HISTORY_LINE_BYTES` and the whole within
    :data:`MAX_HISTORY_BYTES`. A history not in the one schema or in a version the reader does
    not know, or whose run line describes another run than ``run``, raises ``ValueError``."""
    modules = {f[: -len(".py")].replace("/", "."): f for f in files if f.endswith(".py")}
    grouped: RunSeconds = {}
    cpus: dict[str, str | None] = {}
    budget = MAX_HISTORY_BYTES
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        members = [m for m in zipped.infolist() if m.filename.endswith(".jsonl")]
        if not members:
            raise ValueError("the archive holds no history")
        for member in members:
            with zipped.open(member) as handle:
                number = -1
                while True:
                    raw = handle.readline(min(budget, MAX_HISTORY_LINE_BYTES) + 1)
                    if not raw:
                        break
                    number += 1
                    if len(raw) > MAX_HISTORY_LINE_BYTES:
                        raise ValueError(f"a history line passes {MAX_HISTORY_LINE_BYTES} bytes")
                    budget -= len(raw)
                    if budget < 0:
                        raise ValueError(f"the history passes {MAX_HISTORY_BYTES} bytes")
                    line = json.loads(raw)
                    if number == 0:
                        if (
                            line.get("kind") != "run"
                            or line.get("schema") != telemetry.SCHEMA
                            or line.get("version") not in telemetry.READS
                        ):
                            raise ValueError(f"{member.filename} is not in the one schema")
                        if line.get("run_id") != run:
                            raise ValueError(
                                f"the history describes run {line.get('run_id')}, not run {run}"
                            )
                        named = line.get("cpus") or {}
                        if not isinstance(named, Mapping):
                            raise ValueError("the run line's cpus is not a mapping")
                        cpus = {
                            str(k): v if isinstance(v, str) and v else None
                            for k, v in named.items()
                        }
                        continue
                    if line.get("kind") != "test" or line.get("attempt") != 1:
                        continue
                    report = str(line.get("report", ""))
                    if not report.startswith(leg.report_prefix):
                        continue
                    classname, _, name = str(line.get("test", "")).partition("::")
                    path = _file_of(classname or name, modules)
                    if path is None:
                        continue
                    workers = line.get("workers")
                    identity: Identity = (
                        line.get("platform") if isinstance(line.get("platform"), str) else None,
                        workers if isinstance(workers, int) else None,
                    )
                    seconds = grouped.setdefault(identity, {}).setdefault(report, {})
                    seconds[path] = seconds.get(path, 0.0) + float(line.get("seconds") or 0.0)
    return History(grouped, {report: cpus.get(report) for r in grouped.values() for report in r})


def _per_file(reports: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for seconds in reports.values():
        for path, value in seconds.items():
            merged[path] = merged.get(path, 0.0) + value
    return merged


def host_factors(
    read: Sequence[tuple[Mapping[str, Mapping[str, float]], Mapping[str, str | None]]],
) -> dict[str, float]:
    """Each recorded CPU model's factor over ``read``, the input runs' ``(reports, cpus)``: the
    median, over the reports that ran on it, of the report's seconds against what its files'
    plain medians over ``read`` planned. A model with no report, or reports whose files planned
    nothing, has no factor, and a report with no model gives none."""
    plain = _medians([_per_file(reports) for reports, _ in read])
    ratios: dict[str, list[float]] = {}
    for reports, cpus in read:
        for report, seconds in reports.items():
            model = cpus.get(report)
            planned = sum(plain[p] for p in seconds)
            if model and planned > 0:
                ratios.setdefault(model, []).append(sum(seconds.values()) / planned)
    return {model: statistics.median(r) for model, r in sorted(ratios.items())}


def _medians(runs: Sequence[Mapping[str, float]]) -> dict[str, float]:
    return {
        path: statistics.median(s[path] for s in runs if path in s)
        for path in sorted({p for s in runs for p in s})
    }


#: A file whose runs' range is at least this share of its median is a wide one, and weighs its
#: 75th percentile (:func:`typical`). Over run 40112658197's three input runs,
#: test_raw_durable_write_reds_the_gate.py ranged 87% of its median and
#: test_container_runtime_census.py 51%, and each ran 24-32 s past its median in that run.
WIDE = 0.5


def typical(seconds: Sequence[float]) -> float:
    """What a file weighs, from its seconds in each run it was recorded in: the median, or, for
    a wide file (its range at least :data:`WIDE` of its median), the 75th percentile, read
    inclusive of the runs' own values."""
    middle = statistics.median(seconds)
    if len(seconds) < 2 or max(seconds) - min(seconds) < WIDE * middle:
        return middle
    return statistics.quantiles(seconds, n=4, method="inclusive")[2]


def _typicals(runs: Sequence[Mapping[str, float]]) -> tuple[dict[str, float], int]:
    """Each file's :func:`typical` weight over ``runs``, and how many weigh their percentile."""
    out: dict[str, float] = {}
    wide = 0
    for path in sorted({p for s in runs for p in s}):
        seconds = [s[path] for s in runs if path in s]
        out[path] = typical(seconds)
        wide += out[path] != statistics.median(seconds)
    return out, wide


def _describe(identity: Identity) -> str:
    platform, workers = identity
    return f"{platform or 'an unrecorded platform'}, {workers or 'an unrecorded number of'} workers"


class TelemetryRecord:
    """#3948's record: each file's median first-attempt seconds over the leg's own reports in
    the newest ``RUNS`` completed ``main`` runs whose history the collector wrote.

    It reads each run's attempt-1 history, so a rerun's seconds never stand in for the first
    run's, and only the leg's own reports. The leg's platform and worker count are those its
    newest history recorded, and an older run counts only with the same ones: when a leg moves
    to another Python or another ``-n``, its plan reads the new figures from the first run that
    recorded them. Only history the collector's own run uploaded (``ci-telemetry.yml``, on
    ``workflow_run``, on main, in this repository) and whose run line names that CI run is read:
    any run, a fork's pull request included, can upload an artifact of that name. A run
    with no such history, an archive over :data:`MAX_HISTORY_ARCHIVE_BYTES`, or a history the
    reader cannot read is skipped, and an unreadable one is named in the job's log. Each history
    is downloaded once, whatever number of legs read it. Nothing is written or kept."""

    RUNS = 3
    COUNTED = frozenset({"success", "failure"})

    def __init__(
        self, repo: str, *, text: Text, binary: Binary, runs: int = 30, planning: int = 0
    ) -> None:
        self.repo, self.text, self.binary, self.limit = repo, text, binary, runs
        #: The run this plan is for, whose history (a rerun's first attempt) is never read.
        self.planning = planning
        self._archives: dict[int, bytes | None] = {}

    def _archive(self, run: int) -> bytes | None:
        if run not in self._archives:
            self._archives[run] = self._download(run)
        return self._archives[run]

    def _is_collector(self, uploader: object) -> bool:
        """Whether the run that uploaded an artifact is the collector's own: a
        ``ci-telemetry.yml`` run triggered by ``workflow_run``, on main, in this repository.

        An artifact names the run that uploaded it, and the collector uploads each CI run's
        history from a run of its own, so that run, never the CI run, is what is checked. Any
        run can upload an artifact of any name; only the collector's is the record."""
        if not isinstance(uploader, int):
            return False
        run = json.loads(self.text("api", f"repos/{self.repo}/actions/runs/{uploader}"))
        return telemetry.collector_run(run, self.repo)

    def _download(self, run: int) -> bytes | None:
        name = HISTORY_ARTIFACT.format(run=run, attempt=1)
        listed = json.loads(
            self.text("api", f"repos/{self.repo}/actions/artifacts?name={name}&per_page=100")
        )
        found = []
        for artifact in listed.get("artifacts") or []:
            if telemetry.upload_refusal(artifact) or not self._is_collector(
                artifact["workflow_run"]["id"]
            ):
                continue
            found.append((int(artifact["id"]), int(artifact.get("size_in_bytes") or 0)))
        if not found:
            return None
        artifact_id, artifact_bytes = min(found)
        if artifact_bytes > MAX_HISTORY_ARCHIVE_BYTES:
            print(f"::warning title=telemetry history skipped::run {run}: over the size limit")
            return None
        archive = self.binary("api", f"repos/{self.repo}/actions/artifacts/{artifact_id}/zip")
        if len(archive) > MAX_HISTORY_ARCHIVE_BYTES:
            print(f"::warning title=telemetry history skipped::run {run}: over the size limit")
            return None
        return archive

    def _run_seconds(self, run: int, leg: Leg, files: Sequence[str]) -> History | None:
        archive = self._archive(run)
        if archive is None:
            return None
        try:
            grouped = history_seconds(archive, leg, files, run=run)
        except (
            zipfile.BadZipFile,
            zlib.error,
            ValueError,
            TypeError,
            AttributeError,
            EOFError,
        ) as exc:
            print(f"::warning title=telemetry history unreadable::run {run}: {exc}")
            return None
        return grouped if grouped.seconds else None

    def _listed(self, workflow: str) -> list[str]:
        return self.text(
            "api",
            f"repos/{self.repo}/actions/workflows/{workflow}/runs"
            f"?branch=main&status=completed&per_page={self.limit}",
            "--jq",
            '.workflow_runs[] | "\\(.id) \\(.conclusion) \\(.head_repository.full_name)"',
        ).splitlines()

    def durations(self, leg: Leg, files: Sequence[str]) -> tuple[dict[str, float], str]:
        """The leg's runs are read workflow by workflow (:attr:`Leg.workflows`), each one's
        newest first, until :data:`RUNS` hold its history: merge.yml's push runs before ci.yml's
        for ``main-push``, so the plan reads whichever shape has run."""
        identity: Identity | None = None
        read: list[tuple[int, dict[str, dict[str, float]], Mapping[str, str | None]]] = []
        lines = (line for workflow in leg.workflows for line in self._listed(workflow))
        for line in lines:
            ident, conclusion, owner = (line.split() + ["", "", ""])[:3]
            if not ident.isdigit() or conclusion not in self.COUNTED or owner != self.repo:
                continue
            if int(ident) == self.planning:
                continue
            history = self._run_seconds(int(ident), leg, files)
            if history is None:
                continue
            grouped = history.seconds
            if identity is None:
                identity = max(grouped, key=lambda key: (len(_per_file(grouped[key])), str(key)))
            if identity in grouped:
                read.append((int(ident), grouped[identity], history.cpus))
            if len(read) == self.RUNS:
                break
        if not read or identity is None:
            return {}, (
                f"no telemetry history of {' or '.join(leg.workflows)} on main holds"
                f" {leg.report_prefix}* yet, so every file weighs the same"
            )
        factors = host_factors([(reports, cpus) for _, reports, cpus in read])
        normalized = []
        unrecorded = False
        for _, reports, cpus in read:
            seconds: dict[str, float] = {}
            for report, by_file in reports.items():
                model = cpus.get(report)
                unrecorded = unrecorded or not model
                factor = factors.get(model, 1.0) if model else 1.0
                for path, value in by_file.items():
                    seconds[path] = seconds.get(path, 0.0) + value / factor
            normalized.append(seconds)
        merged, wide = _typicals(normalized)
        runs = ", ".join(str(run) for run, _, _ in read)
        hosts = ", ".join(f"{m} {f:.2f}" for m, f in factors.items()) or "none recorded"
        if unrecorded:
            hosts += "; an unrecorded CPU model weighs as it ran"
        return merged, (
            f"the median of telemetry runs {runs}' {leg.report_prefix}* first attempts"
            f" on {_describe(identity)}, by host factor ({hosts}); {wide} wide"
            f" file{'' if wide == 1 else 's'} by {'its' if wide == 1 else 'their'} 75th percentile"
        )


#: How long one ``gh`` call may take. A hung call would otherwise hold the plan job, and every
#: shard waiting on it, until the job's own time limit.
GH_TIMEOUT_SECONDS = 120


def _gh_text(*args: str, timeout: float = GH_TIMEOUT_SECONDS) -> str:
    done = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
        timeout=timeout,
    )
    return done.stdout


def _gh_binary(*args: str, timeout: float = GH_TIMEOUT_SECONDS) -> bytes:
    return subprocess.run(["gh", *args], capture_output=True, check=True, timeout=timeout).stdout


def _durations_or_equal(
    source: DurationsSource, leg: Leg, files: Sequence[str]
) -> tuple[dict[str, float], str]:
    """The leg's durations, or none, with the reason, when they cannot be read for any reason.

    Durations only balance the shards; ``shards check`` is what proves every file runs exactly
    once. So a failed fetch (an API error, a rate limit, a timeout, a corrupt report or archive,
    a reply of the wrong shape) never stops the plan: every file weighs the same, the plan is
    still whole, and the reason is recorded in it and printed as a warning in the job's log.
    """
    try:
        return source.durations(leg, files)
    except Exception as exc:  # noqa: BLE001 - any failed fetch falls back; the plan is checked
        reason = f"unreadable ({type(exc).__name__}: {exc}), so every file weighs the same"
        print(f"::warning title=shard durations unreadable::{leg.name}: {reason}")
        return {}, reason


# ── the command line ──────────────────────────────────────────────────────────────────────────


def _output(line: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _summary(text: str) -> None:
    print(text)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def _spread(buckets: Iterable[Sequence[str]], weight: Mapping[str, float]) -> float:
    loads = [sum(weight[f] for f in b) for b in buckets]
    return max(loads) / min(loads) if loads and min(loads) > 0 else float("inf")


#: The artifact a run's plan is uploaded as, which a later run of the same workflow keeps.
PLAN_ARTIFACT = "shard-plan"


def latest_main_plan(
    repo: str, workflow: str, current_run: int, *, text: Text, binary: Binary, runs: int = 20
) -> tuple[bytes, str]:
    """The plan the newest earlier completed ``main`` run of ``workflow`` uploaded, and which
    run that was. A run's conclusion does not matter: a red run's plan is still whole."""
    listed = text(
        "api",
        f"repos/{repo}/actions/workflows/{workflow}/runs"
        f"?branch=main&status=completed&per_page={runs}",
        "--jq",
        ".workflow_runs[].id",
    )
    for run in (int(r) for r in listed.split() if int(r) != current_run):
        found = [
            line.partition(" ")[0]
            for line in text(
                "api",
                "--paginate",
                f"repos/{repo}/actions/runs/{run}/artifacts?per_page=100",
                "--jq",
                '.artifacts[] | select(.expired | not) | "\\(.id) \\(.name)"',
            ).splitlines()
            if line.partition(" ")[2] == PLAN_ARTIFACT
        ]
        if not found:
            continue
        archive = binary("api", f"repos/{repo}/actions/artifacts/{min(found, key=int)}/zip")
        with zipfile.ZipFile(io.BytesIO(archive)) as plan_zip:
            return plan_zip.read("plan.json"), f"run {run}"
    raise ValueError(f"no earlier main run of {workflow} kept a {PLAN_ARTIFACT}")


def _kept_legs(args: argparse.Namespace) -> tuple[Mapping[str, object], str]:
    """The legs of the earlier plan to keep, or none, with why. Any failure to read it only
    means the plan is made afresh: a new plan is whole too, it just carries nothing tonight."""
    if not args.keep_from_main:
        return {}, ""
    try:
        data, run = latest_main_plan(
            args.repo, args.keep_from_main, args.run_id, text=_gh_text, binary=_gh_binary
        )
        legs = json.loads(data.decode("utf-8"))
        if not isinstance(legs, dict) or legs.get("version") != VERSION:
            raise ValueError(f"{run}'s plan is not a version {VERSION} shard plan")
        named = legs.get("legs")
        if not isinstance(named, dict):
            raise ValueError(f"{run}'s plan names no legs")
        return named, f"{run}'s plan"
    except Exception as exc:  # noqa: BLE001 - an unreadable earlier plan only means a new one
        print(f"::warning title=no earlier shard plan kept::{type(exc).__name__}: {exc}")
        return {}, ""


def _kept_buckets(
    kept: Mapping[str, object], leg: Leg, files: Sequence[str]
) -> list[list[str]] | None:
    """The earlier plan's buckets for ``leg``, when they are a whole plan of today's files in
    the same number of shards: the same files, each in exactly one non-empty shard."""
    earlier = kept.get(leg.name)
    if not isinstance(earlier, dict) or earlier.get("n") != leg.shards:
        return None
    buckets = earlier.get("buckets")
    if (
        not isinstance(buckets, list)
        or len(buckets) != leg.shards
        or not all(
            isinstance(b, list) and b and all(isinstance(f, str) for f in b) for b in buckets
        )
    ):
        return None
    return [list(b) for b in buckets] if not check(files, buckets) else None


def _plan(args: argparse.Namespace) -> int:
    root = Path(args.root)
    files = universe(root)
    if not files:
        print(f"no test file is collectable under {root}", file=sys.stderr)
        return 1
    record: DurationsSource = TelemetryRecord(
        args.repo, text=_gh_text, binary=_gh_binary, planning=args.run_id
    )
    # A breadth shard records its list's digest as an input, so a result is carried only onto
    # the files it measured. A plan made afresh from each run's durations moves files between
    # shards every night (measured: two green main runs hours apart planned 885-949 of each
    # shard's files differently), so no shard would ever carry. So the newest earlier main
    # run's plan is kept for every leg it still plans whole: the same test files, the same N.
    kept, kept_from = _kept_legs(args)
    legs: dict[str, object] = {}
    lines = []
    for leg in (Leg.parse(text) for text in args.leg):
        reused = _kept_buckets(kept, leg, files)
        durations: dict[str, float]
        if reused is not None:
            durations, said = {}, f"kept {kept_from}: the same {len(files)} files"
            buckets = reused
        else:
            durations, said = _durations_or_equal(record, leg, files)
            if kept_from:
                said = f"{kept_from} does not plan today's files; {said}"
            buckets = plan(files, durations, leg.shards)
        problems = check(files, buckets)
        if problems or any(not b for b in buckets):
            print(f"{leg.name}: the plan is not whole: {problems or 'an empty shard'}")
            return 1
        legs[leg.name] = {"n": leg.shards, "buckets": buckets, "durations": said}
        if reused is not None:
            lines.append(f"- `{leg.name}`: {leg.shards} shards; {said}")
            continue
        spread = _spread(buckets, weights(files, durations))
        lines.append(f"- `{leg.name}`: {leg.shards} shards, planned spread {spread:.3f}x; {said}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"version": VERSION, "legs": legs}, sort_keys=True, indent=1))
    _summary(f"### shard plan\n\n{len(files)} test files.\n\n" + "\n".join(lines))
    return 0


def _load_plan(path: str) -> Mapping[str, object]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError(f"not a version {VERSION} shard plan")
    legs = data.get("legs")
    if not isinstance(legs, dict):
        raise ValueError("the plan names no legs")
    return legs


def _list(args: argparse.Namespace) -> int:
    """Write shard i of N's files, one per line, for ``pytest @<file>``. Writes nothing and
    fails when it cannot: pytest given no paths would run every test in ``testpaths``."""
    try:
        legs = _load_plan(args.plan)
        index, _, total = str(args.shard).partition("/")
        if not (index.isdigit() and total.isdigit()):
            raise ValueError(f"--shard is i/N, not {args.shard!r}")
        i, n = int(index), int(total)
        leg = legs.get(args.leg)
        if not isinstance(leg, dict):
            raise ValueError(f"the plan has no leg {args.leg!r}")
        buckets = leg.get("buckets")
        if not isinstance(buckets, list) or leg.get("n") != n or len(buckets) != n:
            raise ValueError(f"{args.leg} is planned in {leg.get('n')} shards, not {n}")
        if not 1 <= i <= n:
            raise ValueError(f"shard {i} is not one of 1 to {n}")
        files = buckets[i - 1]
        if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
            raise ValueError(f"{args.leg} shard {i}/{n} lists no file")
    except (OSError, ValueError) as exc:
        print(f"shards list: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{f}\n" for f in files)
    out.write_text(text, encoding="utf-8")
    # The list's digest, as a step output: a breadth shard records it as one of its inputs, so
    # a result carried on identical inputs was measured on these very files.
    _output(f"digest={hashlib.sha256(text.encode('utf-8')).hexdigest()}")
    print(f"{args.leg} shard {i}/{n}: {len(files)} test files")
    return 0


def _check(args: argparse.Namespace) -> int:
    try:
        legs = _load_plan(args.plan)
    except (OSError, ValueError) as exc:
        print(f"shards check: {exc}", file=sys.stderr)
        return 1
    files = universe(Path(args.root))
    bad = False
    for name, leg in sorted(legs.items()):
        buckets = leg.get("buckets") if isinstance(leg, dict) else None
        problems = check(files, buckets if isinstance(buckets, list) else [])
        for problem in problems:
            print(f"{name}: {problem}")
        bad = bad or bool(problems)
    return 1 if bad else 0


def _universe(args: argparse.Namespace) -> int:
    """Write every file the plan covers, one per line, for a collection of the planned suite
    (``pytest --shard-list <file>``). Writes nothing and fails on a tree with none: pytest given
    an empty list would collect every test, the rails and the job-owned files included."""
    files = universe(Path(args.root))
    if not files:
        print(f"shards universe: no test file is collectable under {args.root}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{f}\n" for f in files), encoding="utf-8")
    print(f"the planned suite: {len(files)} test files")
    return 0


#: A shard's collected test ids, one per line, as ``tests/shard_list.py``'s ``--shard-ids``
#: writes them and the union job finds them.
SHARD_IDS = "shard-ids-{leg}-{shard}.txt"


def _id_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def union_problems(
    expected: Sequence[str], shard_ids: Mapping[int, Sequence[str] | None]
) -> list[str]:
    """What is wrong with the shards' collected ids as a run of ``expected``: an id the planned
    suite holds twice, a shard that wrote no ids, an id collected twice (by one shard or by
    two), an expected id no shard collected, and a collected id the planned suite does not
    have. Empty when every id ran exactly once."""
    planned: dict[str, int] = {}
    for test in expected:
        planned[test] = planned.get(test, 0) + 1
    problems = [
        f"{t} is in the planned suite {n} times" for t, n in sorted(planned.items()) if n > 1
    ]
    problems += [
        f"shard {shard} wrote no id file, so the ids it would have run are not known to have run"
        for shard, ids in sorted(shard_ids.items())
        if ids is None
    ]
    count: dict[str, int] = {}
    for ids in shard_ids.values():
        for test in ids or ():
            count[test] = count.get(test, 0) + 1
    want = set(planned)
    problems += [f"{t} was collected {n} times" for t, n in sorted(count.items()) if n > 1]
    problems += [
        f"{t} is in the planned suite and no shard collected it" for t in sorted(want - set(count))
    ]
    problems += [
        f"{t} was collected by a shard and is not in the planned suite"
        for t in sorted(set(count) - want)
    ]
    return problems


#: A leg's planned suite, collected on that leg's own runner and Python: the ids its shards
#: together must equal.
PLANNED_IDS = "planned-ids-{leg}.txt"


def _leg_and_shards(text: str) -> tuple[str, int]:
    """``name:N``, as ``union --leg`` takes it."""
    name, _, count = text.partition(":")
    if not name or not count.isdigit() or int(count) < 1:
        raise ValueError(f"--leg is name:shards, not {text!r}")
    return name, int(count)


def _leg_union(folder: Path, leg: str, n: int) -> list[str]:
    """What is wrong with one leg: its planned collection missing or empty, or its shards' ids
    not that collection's, each once."""
    planned = PLANNED_IDS.format(leg=leg)
    try:
        expected = _id_lines(folder / planned)
    except OSError:
        return ["the planned suite was not collected on this leg's runner, so nothing is known"]
    except UnicodeDecodeError:
        return [f"{planned} is not UTF-8 text, so nothing is known"]
    if not expected:
        return ["the planned suite's collection names no test, so it failed"]
    found: dict[int, Sequence[str] | None] = {}
    unreadable: list[str] = []
    for shard in range(1, n + 1):
        name = SHARD_IDS.format(leg=leg, shard=shard)
        try:
            found[shard] = _id_lines(folder / name)
        except OSError:
            found[shard] = None
        except UnicodeDecodeError:
            # Read as naming nothing: every id the shard would have run is then one no shard ran.
            found[shard] = []
            unreadable.append(
                f"{name} is not UTF-8 text, so the ids shard {shard} ran are not known"
            )
    problems = unreadable + union_problems(expected, found)
    if not problems:
        print(f"{leg}: {n} shards collected the planned suite's {len(expected)} ids, each once")
    return problems


def _union(args: argparse.Namespace) -> int:
    """Check every leg named, then fail if any is short, so one red names every leg that is."""
    try:
        legs = [_leg_and_shards(text) for text in args.leg]
    except ValueError as exc:
        print(f"shards union: {exc}", file=sys.stderr)
        return 1
    bad = False
    for leg, n in legs:
        for problem in _leg_union(Path(args.dir), leg, n):
            print(f"{leg}: {problem}")
            bad = True
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci shards", description=__doc__.splitlines()[0] if __doc__ else ""
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="plan every leg's shards once, from recorded durations")
    p.add_argument("--repo", required=True, help="owner/name, whose main runs hold the durations")
    p.add_argument("--root", default=".", help="the repository root")
    p.add_argument("--leg", action="append", required=True, help="name:workflow:prefix:shards")
    p.add_argument(
        "--keep-from-main",
        default=None,
        metavar="WORKFLOW",
        help="keep the plan the newest earlier main run of WORKFLOW uploaded, for each leg "
        "it still plans whole",
    )
    p.add_argument("--run-id", type=int, default=0, help="this run, never kept from or weighed by")
    p.add_argument("--out", required=True)
    p = sub.add_parser("list", help="write one shard's files, for pytest @<file>")
    p.add_argument("--plan", required=True)
    p.add_argument("--leg", required=True)
    p.add_argument("--shard", required=True, help="i/N")
    p.add_argument("--out", required=True)
    p = sub.add_parser("check", help="fail when a collectable file is in no shard or in two")
    p.add_argument("--plan", required=True)
    p.add_argument("--root", default=".")
    p = sub.add_parser("universe", help="write every file the plan covers, for its collection")
    p.add_argument("--root", default=".")
    p.add_argument("--out", required=True)
    p = sub.add_parser(
        "union", help="fail unless a leg's shards collected the planned suite's ids, each once"
    )
    p.add_argument(
        "--dir", required=True, help="the folder holding each leg's planned and shard id files"
    )
    p.add_argument("--leg", action="append", required=True, help="name:shards, once per leg")
    args = parser.parse_args(argv)
    commands = {"plan": _plan, "list": _list, "check": _check, "universe": _universe}
    return {**commands, "union": _union}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
