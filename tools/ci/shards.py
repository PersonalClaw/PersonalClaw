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
the first collected main run there is no record, and the plan uses equal weights and says so.
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

from . import telemetry

VERSION = 1

#: Directories pytest's default ``norecursedirs`` skips, besides any starting with a dot.
_SKIPPED_DIRS = frozenset({"__pycache__", "node_modules", "build", "dist", "venv", "CVS"})

#: The rails' folder, run by the ``rails`` job and never by a shard (``rails_census.RAILS``).
RAILS = "tests/rails"


# ── the universe ──────────────────────────────────────────────────────────────────────────────


def _pytest_options(root: Path) -> Mapping[str, object]:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    options = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    return options if isinstance(options, Mapping) else {}


def universe(root: Path) -> list[str]:
    """Every file pytest collects under ``testpaths`` by ``python_files`` (its defaults,
    ``test_*.py`` and ``*_test.py``, when the config names none), as sorted posix paths
    relative to ``root``. A future ``foo_test.py`` cannot fall outside every shard.

    One folder is excluded: ``tests/rails``, the rails, which the ``rails`` job runs once per
    run (``tools/ci/rails_census.py``), so ``check`` proves no shard of any leg lists one. The
    security paths stay in the shards until a merge-tier security job runs them, or they would
    run nowhere.
    """
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
                    found.add((Path(current) / name).relative_to(root).as_posix())
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
    """A suite leg the plan covers: its name, the workflow whose runs hold its junit reports,
    the prefix those reports' artifacts are named by, and its number of shards."""

    name: str
    workflow: str
    report_prefix: str
    shards: int

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
#: An archive larger than this is never downloaded: one main run's history zips to about 3 MB.
MAX_HISTORY_ARCHIVE_BYTES = 32 * 1024 * 1024
#: A history larger than this, unzipped, is not read on: one main run's is about 49 MB.
MAX_HISTORY_BYTES = 256 * 1024 * 1024
#: One history line longer than this is refused: a test line is a few hundred bytes, and a run
#: line with its jobs a few tens of kilobytes.
MAX_HISTORY_LINE_BYTES = 1024 * 1024


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


def history_seconds(
    archive: bytes, leg: Leg, files: Sequence[str], *, run: int
) -> dict[Identity, dict[str, float]]:
    """Per-file first-attempt seconds of ``leg``'s reports in one history archive, by the
    platform and worker count the reports recorded, so seconds measured on one never weigh
    another. Read line by line, each line within :data:`MAX_HISTORY_LINE_BYTES` and the whole
    within :data:`MAX_HISTORY_BYTES`. A history not in the one schema, or whose run line
    describes another run than ``run``, raises ``ValueError``."""
    modules = {f[: -len(".py")].replace("/", "."): f for f in files if f.endswith(".py")}
    grouped: dict[Identity, dict[str, float]] = {}
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
                            or line.get("version") != telemetry.VERSION
                        ):
                            raise ValueError(f"{member.filename} is not in the one schema")
                        if line.get("run_id") != run:
                            raise ValueError(
                                f"the history describes run {line.get('run_id')}, not run {run}"
                            )
                        continue
                    if line.get("kind") != "test" or line.get("attempt") != 1:
                        continue
                    if not str(line.get("report", "")).startswith(leg.report_prefix):
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
                    seconds = grouped.setdefault(identity, {})
                    seconds[path] = seconds.get(path, 0.0) + float(line.get("seconds") or 0.0)
    return grouped


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

    def __init__(self, repo: str, *, text: Text, binary: Binary, runs: int = 30) -> None:
        self.repo, self.text, self.binary, self.limit = repo, text, binary, runs
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
        head = run.get("head_repository") or {}
        return (
            run.get("path") == telemetry.COLLECTOR_PATH
            and run.get("event") == "workflow_run"
            and run.get("head_branch") == "main"
            and isinstance(head, Mapping)
            and head.get("full_name") == self.repo
        )

    def _download(self, run: int) -> bytes | None:
        name = HISTORY_ARTIFACT.format(run=run, attempt=1)
        listed = json.loads(
            self.text("api", f"repos/{self.repo}/actions/artifacts?name={name}&per_page=100")
        )
        found = []
        for artifact in listed.get("artifacts") or []:
            made_by = artifact.get("workflow_run") or {}
            if (
                artifact.get("expired")
                or made_by.get("head_branch") != "main"
                or made_by.get("head_repository_id") != made_by.get("repository_id")
                or not self._is_collector(made_by.get("id"))
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

    def _run_seconds(
        self, run: int, leg: Leg, files: Sequence[str]
    ) -> dict[Identity, dict[str, float]] | None:
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
        return grouped or None

    def durations(self, leg: Leg, files: Sequence[str]) -> tuple[dict[str, float], str]:
        listed = self.text(
            "api",
            f"repos/{self.repo}/actions/workflows/{leg.workflow}/runs"
            f"?branch=main&status=completed&per_page={self.limit}",
            "--jq",
            '.workflow_runs[] | "\\(.id) \\(.conclusion) \\(.head_repository.full_name)"',
        )
        identity: Identity | None = None
        read: list[tuple[int, dict[str, float]]] = []
        for line in listed.splitlines():
            ident, conclusion, owner = (line.split() + ["", "", ""])[:3]
            if not ident.isdigit() or conclusion not in self.COUNTED or owner != self.repo:
                continue
            grouped = self._run_seconds(int(ident), leg, files)
            if grouped is None:
                continue
            if identity is None:
                identity = max(grouped, key=lambda key: (len(grouped[key]), str(key)))
            if identity in grouped:
                read.append((int(ident), grouped[identity]))
            if len(read) == self.RUNS:
                break
        if not read or identity is None:
            return {}, (
                f"no telemetry history of {leg.workflow} on main holds {leg.report_prefix}* yet,"
                " so every file weighs the same"
            )
        merged = {
            path: statistics.median(s[path] for _, s in read if path in s)
            for path in sorted({p for _, s in read for p in s})
        }
        runs = ", ".join(str(run) for run, _ in read)
        return merged, (
            f"the median of telemetry runs {runs}' {leg.report_prefix}* first attempts"
            f" on {_describe(identity)}"
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
    record: DurationsSource = TelemetryRecord(args.repo, text=_gh_text, binary=_gh_binary)
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
    p.add_argument("--run-id", type=int, default=0, help="this run, never kept from")
    p.add_argument("--out", required=True)
    p = sub.add_parser("list", help="write one shard's files, for pytest @<file>")
    p.add_argument("--plan", required=True)
    p.add_argument("--leg", required=True)
    p.add_argument("--shard", required=True, help="i/N")
    p.add_argument("--out", required=True)
    p = sub.add_parser("check", help="fail when a collectable file is in no shard or in two")
    p.add_argument("--plan", required=True)
    p.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    return {"plan": _plan, "list": _list, "check": _check}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
