"""``python -m tools.ci report``: the weekly CI health report, read from the telemetry history.

It reads only what ``tools/ci/telemetry.py`` writes, in its one schema: the
``history-<run>-<attempt>.jsonl`` files and the ``slots-*.jsonl`` files. A file in another
schema or a version it does not read (``telemetry.READS``) fails the report (exit 2), since a
figure read from a record it does not understand would be a guess. It also reads the quarantine,
``tests/quarantine.toml``, for its status. The same table prints in a terminal and goes to the
job summary (``$GITHUB_STEP_SUMMARY``), and ``--json`` writes the figures for the weekly issue
and for the tools that read them (#3926 the p99 durations, #3946 the reds).

**At 90 days' scale.** 90 days are about 860 histories of about 49 MB each, so nothing is held
whole. The report reads each history's first line to find the runs and the window, then only the
runs inside the window, one run at a time and one line at a time (each within
``MAX_LINE_BYTES``, each file within ``MAX_FILE_BYTES``). Each run's outcomes are folded into
each test's figures before the next run is read, and a test's seconds are kept as each distinct
value with the number of runs that took it: junit writes seconds to the millisecond, so a test
takes few distinct values however many runs it has, and the percentiles are exact. A history
outside the window is read for its first line alone.

**The window.** The 90 days up to the newest run in the history. While less than 90 days of
history exist, the report states the exact window it read (its first and last run, and how many
days that is), and the 90-day checks (#3946's replay among them) stay off: ``window.mature`` is
false until the history spans 90 days.

**First results.** A test's result in a run is its record in that run's lowest attempt: a later
attempt, or a ``rerun_result`` of ``flake`` (``telemetry.FLAKE``) that ``run-job``'s diagnostic
rerun writes on the same line, adds a passing rerun to a first failure and never replaces it; a
``repeated failure`` adds none. Two records of one test in one attempt (a report read twice, a
retried upload) count once, the first failure if either failed. Skips and expected failures are
not runs of the test.

**Flake rate.** First failures that a rerun then passed, over the test's runs, rated only over
``MIN_RUNS`` runs or more.

**What the reruns found.** From each job's record line (``record``, version 4 on), of each run's
lowest attempt as for first results: every test the diagnostic rerun classified, with how many job
records found it a flake and how many a repeated failure, and the jobs that did. Every runner's
classification is there, Playwright's included, which no test line carries. A history of an older
version carries none, and a record line in one, or one not in its shape, fails the report. A
tested push's pull request run's record lines (``tested_record``, version 6) are checked as
record lines are and counted in no figure here: they are that run's, not the push's.

**Durations.** Each test's p50 and p99 seconds over the window, the 20 slowest by p50, and the
trend: the p50 of the last 7 days over the p50 of the 7 days before, less one.

**The quarantine.** Each entry of ``tests/quarantine.toml`` with its issue, its assignee and its
expiry, and the days left; an entry on or past its date has expired, as ``tests/quarantine.py``
reads it, and is flagged. A missing list is an empty one; an entry not in the list's shape fails
the report.

**Browse-live's environment quarantine.** The browse proof fails with ``ENVIRONMENT, NOT
POLICY`` when the browser, not the safety script, broke the run (``environment_failure`` on its
``junit-browse-live`` lines, version 2 on). Each head commit of main is one observation, so a
rerun cannot inflate the rate: of a head's runs and their attempts, the newest that was not
cancelled is the one that counts, and when it holds no browse-live line (or only version-1 lines,
which cannot say), the head is not observed and an older run never stands in for it. The newest
``BROWSE_LIVE_WINDOW`` observed heads are listed, and ``BROWSE_LIVE_THRESHOLD`` of them
quarantined is a recurrence: flagged in the issue, and an error annotation in the log. These are
the rules of the tracker workflow this row replaced, which also counted pull-request heads; the
history holds pushes to main only.

**Scheduled runs.** From the newest slots file: each scheduled workflow's slots that ran, with
their median lag, and the slots no run answered, as ``missed`` (its next slot passed with no run)
or ``pending`` (not yet). A missed night is its own line, never a late one.

Standard library only, like every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import datetime
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from array import array
from collections import Counter
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from . import telemetry, verdict

WINDOW_DAYS = 90
MIN_RUNS = 10
SLOWEST = 20
TREND_DAYS = 7
#: The results that are a run of the test; a skip or an expected failure is not.
RAN = frozenset({"passed", "failed", "error"})
FAILED = frozenset({"failed", "error"})
#: One history line longer than this is refused, its line break included: the collector's one
#: bound, which it writes no line past. The shard planner reads histories within the same limits.
MAX_LINE_BYTES = telemetry.MAX_LINE_BYTES
#: A history file larger than this is refused: one main run's is about 49 MB.
MAX_FILE_BYTES = 256 * 1024 * 1024
#: The quarantine, whose status the report shows, and its fields (``tests/quarantine.py``'s).
QUARANTINE = Path(__file__).resolve().parents[2] / "tests" / "quarantine.toml"
QUARANTINE_FIELDS = ("nodeid", "issue", "assignee", "expires", "reason")
#: The browse proof's report, whose lines the browse-live row reads.
BROWSE_LIVE_REPORT = "junit-browse-live"
#: An alert at 3 of the last 5 distinct heads: three independent revisions establish recurrence
#: beyond a one-off flake, and a rate also catches alternating failures a run of reds would miss.
BROWSE_LIVE_WINDOW = 5
BROWSE_LIVE_THRESHOLD = 3
#: An attempt with one of these conclusions observed nothing.
UNMEASURED = frozenset({None, "cancelled", "skipped"})
#: The names the collector gives its files, as they stand in a folder or inside an archive.
HISTORY_NAME = re.compile(r"history-\d+-\d+\.jsonl")
SLOTS_NAME = re.compile(r"slots-[A-Za-z0-9_.:-]+\.jsonl")
#: The quarantined tests' reruns, as ``python -m tools.ci quarantine-rerun`` writes them.
RERUN_SCHEMA = "personalclaw-quarantine-reruns"
RERUN_COUNTS = ("passed", "failed", "skipped", "not_run")
#: The weekly issue: the one open issue of this title the workflow's own account opened.
ISSUE_TITLE = "Weekly CI health"
#: The account the workflow token writes issues as. On a public repository anyone may open an
#: issue of that title, so only this account's is the report's.
BOT = "github-actions[bot]"
#: GitHub refuses an issue body past 65,536 characters; the issue keeps under this.
ISSUE_BODY_LIMIT = 65_000
#: Pages of 100 open issues read at most when looking for the weekly one.
ISSUE_PAGES = 10
#: How long one ``gh`` call may take.
GH_TIMEOUT_SECONDS = 120
#: The fetch's status (``python -m tools.ci fetch``): how the window was built, its counts.
FETCH_COUNTS = ("carried", "dropped", "fetched", "refused", "left", "reserve")
#: The refusals the issue lists at most; the fetch's log holds every one.
REFUSALS_SHOWN = 20
#: The tests the issue lists of what the reruns found, at most; report.json holds every one.
DIAGNOSED_SHOWN = 20
#: A job record line's fields, as the collector writes them.
RECORD_FIELDS = telemetry.RECORD_FIELDS
_RERUN_FIELDS = frozenset({"test", "rerun_result"})


class SchemaError(Exception):
    """A file in the history is not in the one schema this report reads."""


@dataclass(slots=True)
class _Outcome:
    """One test in one run: its first attempt's result, and whether a rerun passed."""

    attempt: int
    first: str
    seconds: float
    passed_on_rerun: bool = False


class Counted:
    """Seconds as each distinct value and the number of runs that took it, in two flat arrays
    in value order: 12 bytes a distinct value, where a list of every run's seconds would grow by
    one float object a run. Percentiles and medians from it are exact."""

    __slots__ = ("values", "counts", "total")

    def __init__(self) -> None:
        self.values = array("d")
        self.counts = array("I")
        self.total = 0

    def add(self, seconds: float) -> None:
        i = bisect.bisect_left(self.values, seconds)
        if i < len(self.values) and self.values[i] == seconds:
            self.counts[i] += 1
        else:
            self.values.insert(i, seconds)
            self.counts.insert(i, 1)
        self.total += 1

    def nth(self, index: int) -> float:
        """The ``index``-th smallest value, from 0."""
        for value, count in zip(self.values, self.counts, strict=True):
            if index < count:
                return value
            index -= count
        raise IndexError(index)

    def percentile(self, q: float) -> float:
        k = (self.total - 1) * q
        low = math.floor(k)
        high = min(low + 1, self.total - 1)
        a, b = self.nth(low), self.nth(high)
        return a + (b - a) * (k - low)

    def median(self) -> float:
        """``statistics.median`` of the values, computed as it computes it."""
        n = self.total
        if n % 2:
            return self.nth(n // 2)
        return (self.nth(n // 2 - 1) + self.nth(n // 2)) / 2


@dataclass(slots=True)
class Figures:
    """One test's figures over the window, folded in run by run: its seconds over the window,
    and over each of the two trend weeks it ran in."""

    runs: int = 0
    first_passes: int = 0
    first_failures: int = 0
    passed_on_rerun: int = 0
    seconds: Counted = field(default_factory=Counted)
    this_week: Counted | None = None
    last_week: Counted | None = None


@dataclass(slots=True)
class Diagnosed:
    """One test over the window, as the job records' reruns classified it."""

    flakes: int = 0
    repeated_failures: int = 0
    jobs: set[str] = field(default_factory=set)


@dataclass
class History:
    """What the report reads from the history: the window, each test's figures over it, and how
    many tests the newest run recorded; what the reruns found, from the job records, and how many
    of the window's runs carried any."""

    window: dict[str, Any]
    tests: dict[str, Figures]
    suite_size: int
    browse_live: list[dict[str, Any]] = field(default_factory=list)
    diagnosed: dict[str, Diagnosed] = field(default_factory=dict)
    runs_with_records: int = 0


@dataclass(frozen=True, slots=True)
class Source:
    """One telemetry file: a file in the folder, a member of an archive in it, or a member of an
    archive inside the window archive the fetch built. It is read in place, in memory, and never
    unpacked to disk."""

    path: Path
    members: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.members[-1] if self.members else self.path.name

    @contextlib.contextmanager
    def open(self) -> Generator[IO[bytes], None, None]:
        with contextlib.ExitStack() as stack:
            handle: IO[bytes] = stack.enter_context(self.path.open("rb"))
            for member in self.members:
                zipped = stack.enter_context(zipfile.ZipFile(handle))
                handle = stack.enter_context(zipped.open(member))
            yield handle


#: A collector archive as it stands inside the window archive.
COLLECTOR_ARCHIVE = re.compile(r"telemetry-(?:history-\d+-\d+|slots-\d+)\.zip")


def _readable(info: zipfile.ZipInfo, where: str) -> bool:
    """Whether a member may be read; a refused one is named in the log."""
    why = telemetry.member_refusal(info)
    if why:
        print(f"::warning title=archive member refused::{where}: {why}")
    return why is None


def _members(zipped: zipfile.ZipFile, where: str) -> list[zipfile.ZipInfo]:
    """An archive's members, within the window's caps: one past either fails the report. A
    member is opened by its name, and of two of one name zipfile opens the last, which may not be
    the one checked, so a name held twice is not read at all."""
    infos = zipped.infolist()
    if len(infos) > telemetry.MAX_WINDOW_MEMBERS:
        raise SchemaError(
            f"{where} holds {len(infos)} members, past {telemetry.MAX_WINDOW_MEMBERS}"
        )
    total = sum(i.file_size for i in infos)
    if total > telemetry.MAX_WINDOW_BYTES:
        raise SchemaError(f"{where} holds {total} bytes, past {telemetry.MAX_WINDOW_BYTES}")
    twice = {name for name, n in Counter(i.filename for i in infos).items() if n > 1}
    for name in sorted(twice):
        print(f"::warning title=archive member refused::{where}: {name!r} more than once")
    return [i for i in infos if i.filename not in twice and _readable(i, where)]


def sources(folder: Path, name: re.Pattern[str]) -> list[Source]:
    """The files under ``folder`` named as the collector names one kind of its files; the
    members so named at the top of every archive under it; and those inside every collector
    archive the window archive holds. A member that is absolute, holds a parent path or a
    folder, or is a link is refused, and so is a collector archive past its size; an archive past
    the window's caps, or not an archive, fails the report."""
    found = [Source(p) for p in folder.rglob("*.jsonl") if name.fullmatch(p.name)]
    for archive in sorted(folder.rglob("*.zip")):
        try:
            with zipfile.ZipFile(archive) as zipped:
                for info in _members(zipped, archive.name):
                    if name.fullmatch(info.filename):
                        found.append(Source(archive, (info.filename,)))
                    elif COLLECTOR_ARCHIVE.fullmatch(info.filename):
                        if info.file_size > telemetry.MAX_HISTORY_ARCHIVE_BYTES:
                            limit = telemetry.MAX_HISTORY_ARCHIVE_BYTES
                            print(
                                f"::warning title=archive member refused::{archive.name}: "
                                f"{info.filename} passes {limit} bytes"
                            )
                            continue
                        where = f"{archive.name}/{info.filename}"
                        with zipped.open(info) as handle, zipfile.ZipFile(handle) as inner:
                            found += [
                                Source(archive, (info.filename, i.filename))
                                for i in _members(inner, where)
                                if name.fullmatch(i.filename)
                            ]
        except (OSError, zipfile.BadZipFile, EOFError) as exc:
            raise SchemaError(f"{archive.name} is not an archive the fetch built: {exc}") from None
    return sorted(found, key=lambda s: (str(s.path), s.members))


@dataclass(frozen=True, slots=True)
class _File:
    path: Source
    run_id: int
    attempt: int
    created: datetime.datetime
    sha: str
    conclusion: str | None
    #: When the attempt was last updated (its created time when it has none): retries' order.
    updated: datetime.datetime
    #: The schema version its run line names: a record line is version 4's.
    version: int


def _at(value: object) -> datetime.datetime:
    if not isinstance(value, str) or not value:
        raise SchemaError(f"not a time: {value!r}")
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _records(path: Source) -> Generator[dict[str, Any], None, None]:
    """Each line of one telemetry file, parsed, one line at a time: a line past
    ``MAX_LINE_BYTES``, a file past ``MAX_FILE_BYTES`` (counted as it is unpacked, for an
    archive's member), or a line that is not a JSON object is refused. Blank lines are
    skipped."""
    budget = MAX_FILE_BYTES
    try:
        with path.open() as handle:
            while raw := handle.readline(MAX_LINE_BYTES + 1):
                if len(raw) > MAX_LINE_BYTES:
                    raise SchemaError(f"{path.name}: a line passes {MAX_LINE_BYTES} bytes")
                budget -= len(raw)
                if budget < 0:
                    raise SchemaError(f"{path.name}: the file passes {MAX_FILE_BYTES} bytes")
                if not raw.strip():
                    continue
                line = json.loads(raw)
                if not isinstance(line, dict):
                    raise SchemaError(f"{path.name}: a line is not a JSON object")
                yield line
    except (OSError, ValueError, zipfile.BadZipFile, EOFError) as exc:
        raise SchemaError(f"{path.name}: {exc}") from None


def _head(path: Source, kind: str) -> dict[str, Any]:
    """A file's first line, which must be its ``kind`` line in the one schema."""
    records = _records(path)
    try:
        head = next(records, None)
    finally:
        records.close()
    if head is None:
        raise SchemaError(f"{path.name} is empty")
    if head.get("kind") != kind or head.get("schema") != telemetry.SCHEMA:
        raise SchemaError(f"{path.name}: its first line is not a {telemetry.SCHEMA} {kind} line")
    if head.get("version") not in telemetry.READS:
        raise SchemaError(
            f"{path.name}: schema version {head.get('version')}, this report reads "
            f"{sorted(telemetry.READS)}"
        )
    return head


def _rerun_word(word: object) -> bool:
    return word is None or (isinstance(word, str) and word in telemetry.RERUN_RESULTS)


def job_records(lines: Sequence[dict[str, Any]], where: str) -> list[dict[str, Any]]:
    """One attempt's job record lines, each as the collector writes it. One in another shape
    fails the report: a figure read from it would be a guess."""
    keys: set[str] = set()
    for entry in lines:
        if set(entry) != set(RECORD_FIELDS):
            raise SchemaError(f"{where}: a job record line does not hold {RECORD_FIELDS}")
        key, rerun, quarantined = entry["key"], entry["rerun"], entry["quarantined"]
        if not isinstance(key, str) or not key or key in keys:
            raise SchemaError(f"{where}: a job record names no key, or a key twice")
        keys.add(key)
        result = entry["result"]
        if not isinstance(result, str) or result not in verdict.WORDS:
            raise SchemaError(f"{where}: {key}'s result {result!r} is no job's")
        if not isinstance(rerun, list) or not all(
            isinstance(t, dict)
            and set(t) == _RERUN_FIELDS
            and isinstance(t["test"], str)
            and t["test"]
            and _rerun_word(t["rerun_result"])
            for t in rerun
        ):
            raise SchemaError(f"{where}: {key}'s failed tests are not in their shape")
        # As a record does (``verdict``): one rerun names each test once.
        twice = sorted(test for test, n in Counter(t["test"] for t in rerun).items() if n > 1)
        if twice:
            raise SchemaError(f"{where}: {key}'s rerun names {twice[0]} twice")
        if not isinstance(quarantined, list) or not all(
            isinstance(t, str) and t for t in quarantined
        ):
            raise SchemaError(f"{where}: {key}'s quarantined failures are not test ids")
    return list(lines)


def tested_records(lines: Sequence[dict[str, Any]], where: str) -> list[dict[str, Any]]:
    """One attempt's tested run's record lines (version 6), each as the collector writes it: a
    record line's fields (:func:`job_records`) after the run and attempt it came from, attempt 1,
    one run for them all. One in another shape fails the report."""
    runs = set()
    for entry in lines:
        if set(entry) != set(telemetry.TESTED_RECORD_FIELDS):
            raise SchemaError(f"{where}: a tested record line does not hold its fields")
        run_id = entry["run_id"]
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id < 1:
            raise SchemaError(f"{where}: a tested record line names no run")
        if entry["attempt"] != 1:
            raise SchemaError(f"{where}: a tested record line is not of its run's first attempt")
        runs.add(run_id)
    if len(runs) > 1:
        raise SchemaError(f"{where}: tested record lines name more than one run")
    job_records(
        [
            {k: v for k, v in e.items() if k not in ("run_id", "attempt")} | {"kind": "record"}
            for e in lines
        ],
        where,
    )
    return list(lines)


def run_records(path: Source) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """One history's run line, its own job record lines (version 4 on) and its tested pull
    request run's (version 6), each checked as :func:`read` checks them. Its test lines are not
    read here."""
    head = _head(path, "run")
    version = int(head["version"])
    own: list[dict[str, Any]] = []
    tested: list[dict[str, Any]] = []
    records = _records(path)
    try:
        next(records)
        for record in records:
            if record.get("kind") == "record" and version >= 4:
                own.append(record)
            elif (
                record.get("kind") == telemetry.TESTED_RECORD
                and version >= telemetry.TESTED_RECORDS_SINCE
            ):
                tested.append(record)
    finally:
        records.close()
    return head, job_records(own, path.name), tested_records(tested, path.name)


def _outcomes(
    files: Sequence[_File],
) -> tuple[dict[str, _Outcome], dict[int, bool | None], list[dict[str, Any]]]:
    """Each test's outcome in one run, from that run's files, lowest attempt first; for each
    attempt with browse-live lines, whether one carried the environment marker (None when a line
    cannot say: a version-1 line has no such field); and the job record lines of the run's lowest
    attempt, as for first results (its first file's, in path order). Every file's are checked."""
    outcomes: dict[str, _Outcome] = {}
    browse: dict[int, bool | None] = {}
    first: list[dict[str, Any]] | None = None
    # Lowest attempt first, so each test's first result is that attempt's, whatever the file
    # order; files of one attempt in path order.
    for file in sorted(files, key=lambda f: f.attempt):
        records = _records(file.path)
        next(records)  # the run line, read and checked in the first pass
        jobs: list[dict[str, Any]] = []
        tested: list[dict[str, Any]] = []
        for record in records:
            if record.get("kind") == "record" and file.version >= 4:
                jobs.append(record)
                continue
            if (
                record.get("kind") == telemetry.TESTED_RECORD
                and file.version >= telemetry.TESTED_RECORDS_SINCE
            ):
                # A tested push's pull request run's records (version 6): checked, and not this
                # run's own, so no figure here reads them.
                tested.append(record)
                continue
            if record.get("kind") != "test":
                raise SchemaError(f"{file.path.name}: a line after the first is not a test line")
            test, result = str(record["test"]), str(record["first_result"])
            seconds = float(record.get("seconds") or 0.0)
            if record.get("report") == BROWSE_LIVE_REPORT:
                marked = record.get("environment_failure")
                known = browse.get(file.attempt, False)
                browse[file.attempt] = (
                    None if known is None or not isinstance(marked, bool) else known or marked
                )
            found = outcomes.get(test)
            if found is None:
                found = outcomes[test] = _Outcome(file.attempt, result, seconds)
            elif found.attempt == file.attempt:
                if result in FAILED and found.first not in FAILED:
                    found.first, found.seconds = result, seconds
                continue
            elif found.first in FAILED and result == "passed":
                found.passed_on_rerun = True
                continue
            if found.first in FAILED and record.get("rerun_result") == telemetry.FLAKE:
                found.passed_on_rerun = True
        checked = job_records(jobs, file.path.name)
        tested_records(tested, file.path.name)
        if first is None:
            first = checked
    return outcomes, browse, first or []


def _browse_live(
    files: Iterable[_File], measured: Mapping[tuple[int, int], bool | None]
) -> list[dict[str, Any]]:
    """The newest ``BROWSE_LIVE_WINDOW`` observed heads, newest first: per head, the newest of
    its runs' attempts that was not cancelled, when it measured browse-live."""
    by_sha: dict[str, list[_File]] = {}
    for file in files:
        if file.sha:
            by_sha.setdefault(file.sha, []).append(file)

    def order(file: _File) -> tuple[datetime.datetime, int, int]:
        return file.updated, file.run_id, file.attempt

    heads = sorted(by_sha.items(), key=lambda item: max(map(order, item[1])), reverse=True)
    observed: list[dict[str, Any]] = []
    for sha, retries in heads:
        if len(observed) >= BROWSE_LIVE_WINDOW:
            break
        counted = sorted((f for f in retries if f.conclusion not in UNMEASURED), key=order)
        if not counted:
            continue
        newest = counted[-1]
        quarantined = measured.get((newest.run_id, newest.attempt))
        if quarantined is None:
            continue
        observed.append(
            {
                "sha": sha,
                "run_id": newest.run_id,
                "attempt": newest.attempt,
                "quarantined": quarantined,
            }
        )
    return observed


def read(folder: Path) -> History:
    """The window and each test's figures over it, from the history files under ``folder``,
    read file by file: every file's first line, then the test lines of the runs in the window,
    one run at a time."""
    files = []
    for path in sources(folder, HISTORY_NAME):
        head = _head(path, "run")
        made = _at(head["created_at"])
        files.append(
            _File(
                path,
                int(head["run_id"]),
                int(head["attempt"]),
                made,
                str(head.get("sha") or ""),
                head.get("conclusion"),
                _at(head["updated_at"]) if head.get("updated_at") else made,
                int(head["version"]),
            )
        )
    if not files:
        window: dict[str, Any] = {"runs": 0, "days": 0, "first": None, "last": None}
        window["mature"] = False
        return History(window, {}, 0)
    created: dict[int, datetime.datetime] = {}
    by_run: dict[int, list[_File]] = {}
    for file in files:
        created[file.run_id] = min(created.get(file.run_id, file.created), file.created)
        by_run.setdefault(file.run_id, []).append(file)
    newest, oldest = max(created.values()), min(created.values())
    mature = (newest - oldest) >= datetime.timedelta(days=WINDOW_DAYS)
    start = newest - datetime.timedelta(days=WINDOW_DAYS)
    kept = {run_id: at for run_id, at in created.items() if at >= start}
    first = min(kept.values())
    window = {
        "runs": len(kept),
        "days": WINDOW_DAYS if mature else (newest - first).days,
        "first": first.isoformat(),
        "last": newest.isoformat(),
        "mature": mature,
    }
    latest = max(kept, key=lambda run_id: (kept[run_id], run_id))
    week = newest - datetime.timedelta(days=TREND_DAYS)
    before = newest - datetime.timedelta(days=2 * TREND_DAYS)
    tests: dict[str, Figures] = {}
    suite_size = 0
    measured: dict[tuple[int, int], bool | None] = {}
    diagnosed: dict[str, Diagnosed] = {}
    with_records = 0
    for run_id, at in sorted(kept.items()):
        outcomes, browse, lowest = _outcomes(by_run[run_id])
        with_records += bool(lowest)
        for entry in lowest:
            for t in entry["rerun"]:
                if t["rerun_result"] is None:
                    continue
                seen = diagnosed.setdefault(t["test"], Diagnosed())
                seen.flakes += t["rerun_result"] == telemetry.FLAKE
                seen.repeated_failures += t["rerun_result"] != telemetry.FLAKE
                seen.jobs.add(entry["key"])
        measured.update({(run_id, attempt): marked for attempt, marked in browse.items()})
        if run_id == latest:
            suite_size = len(outcomes)
        for test, outcome in outcomes.items():
            figures = tests.get(test)
            if figures is None:
                figures = tests[test] = Figures()
            if outcome.first not in RAN:
                continue
            figures.runs += 1
            figures.first_passes += outcome.first == "passed"
            if outcome.first in FAILED:
                figures.first_failures += 1
                figures.passed_on_rerun += outcome.passed_on_rerun
            figures.seconds.add(outcome.seconds)
            if at > week:
                figures.this_week = figures.this_week or Counted()
                figures.this_week.add(outcome.seconds)
            elif at > before:
                figures.last_week = figures.last_week or Counted()
                figures.last_week.add(outcome.seconds)
    in_window = [file for run_id in kept for file in by_run[run_id]]
    return History(
        window,
        tests,
        suite_size,
        _browse_live(in_window, measured),
        diagnosed,
        with_records,
    )


def read_slots(folder: Path) -> list[dict[str, Any]]:
    """The slot lines of the newest slots file, by the time it was written."""
    newest: tuple[datetime.datetime, Source] | None = None
    for path in sources(folder, SLOTS_NAME):
        at = _at(_head(path, "slots").get("at"))
        if newest is None or at > newest[0]:
            newest = (at, path)
    if newest is None:
        return []
    return [x for x in list(_records(newest[1]))[1:] if x.get("kind") == "slot"]


def read_fetch_status(path: Path) -> dict[str, Any]:
    """The fetch's status: whether the carried window was kept and why not, and how many
    histories were carried, dropped on the recheck, fetched, refused and left for the next run."""
    if path.stat().st_size > MAX_LINE_BYTES:
        raise SchemaError(f"{path}: passes {MAX_LINE_BYTES} bytes")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise SchemaError(f"{path}: not a fetch status")
    for key in FETCH_COUNTS:
        value = doc.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SchemaError(f"{path}: {key} is {value!r}, not a count")
    refusals = doc.get("refusals")
    if (
        not isinstance(doc.get("carry"), str)
        or not isinstance(doc.get("slots"), (str, type(None)))
        or not isinstance(refusals, list)
        or not all(isinstance(why, str) for why in refusals)
    ):
        raise SchemaError(f"{path}: not a fetch status")
    return {key: doc[key] for key in (*FETCH_COUNTS, "carry", "slots", "refusals")}


def figures(
    history: History,
    slots: Iterable[Mapping[str, Any]],
    quarantine: Iterable[Mapping[str, Any]],
    on: datetime.date | None = None,
    reruns: Mapping[str, Mapping[str, int]] | None = None,
    fetched: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for test, f in sorted(history.tests.items()):
        trend = None
        if f.this_week and f.last_week and f.last_week.median() > 0:
            trend = f.this_week.median() / f.last_week.median() - 1
        rows.append(
            {
                "test": test,
                "runs": f.runs,
                "first_passes": f.first_passes,
                "first_failures": f.first_failures,
                "passed_on_rerun": f.passed_on_rerun,
                "flake_rate": f.passed_on_rerun / f.runs if f.runs >= MIN_RUNS else None,
                "p50_seconds": f.seconds.percentile(0.5) if f.seconds.total else None,
                "p99_seconds": f.seconds.percentile(0.99) if f.seconds.total else None,
                "trend": trend,
            }
        )
    timed = [r for r in rows if r["p50_seconds"] is not None]
    slowest = sorted(timed, key=lambda r: (-r["p50_seconds"], r["test"]))[:SLOWEST]
    record = {
        "days": history.window["days"],
        "of": WINDOW_DAYS,
        "runs": history.window["runs"],
        "runs_with_records": history.runs_with_records,
        "left": fetched["left"] if fetched else 0,
        "fetch": dict(fetched) if fetched else None,
    }
    diagnosed: list[dict[str, Any]] = [
        {
            "test": test,
            "flakes": d.flakes,
            "repeated_failures": d.repeated_failures,
            "jobs": sorted(d.jobs),
        }
        for test, d in history.diagnosed.items()
    ]
    diagnosed.sort(key=lambda r: (-r["flakes"], -r["repeated_failures"], r["test"]))
    return {
        "window": history.window,
        "record": record,
        "tests": rows,
        "diagnosed": diagnosed,
        "slowest": slowest,
        "suite_size": history.suite_size,
        "quarantine": quarantine_status(quarantine, on or today(), reruns),
        "browse_live": browse_live(history.browse_live),
        "schedules": schedules(slots),
    }


def browse_live(observed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    quarantined = sum(bool(o["quarantined"]) for o in observed)
    return {
        "window": BROWSE_LIVE_WINDOW,
        "threshold": BROWSE_LIVE_THRESHOLD,
        "measured": len(observed),
        "quarantined": quarantined,
        "recurring": len(observed) == BROWSE_LIVE_WINDOW and quarantined >= BROWSE_LIVE_THRESHOLD,
        "observations": [dict(o) for o in observed],
    }


def today() -> datetime.date:
    """The day the quarantine is read, in UTC, as ``tests/quarantine.py`` reads it."""
    return datetime.datetime.now(datetime.UTC).date()


def read_quarantine(path: Path) -> list[dict[str, Any]]:
    """The quarantine's entries. A missing list is an empty one, as ``tests/quarantine.py``
    reads it. An entry not in the list's shape (its five fields exactly, an ``issue`` that is a
    number, an ``expires`` that is a date) fails the report: its status would be a guess. The
    list's other rules (the cap, the 30 days, a test that exists) are the suite's to hold."""
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SchemaError(f"{path.name}: {exc}") from None
    entries = data.get("entry", [])
    if set(data) - {"entry"} or not isinstance(entries, list):
        raise SchemaError(f"{path.name}: the quarantine is not a list of [[entry]] tables")
    for number, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or set(entry) != set(QUARANTINE_FIELDS):
            raise SchemaError(f"{path.name}: entry {number} does not hold {QUARANTINE_FIELDS}")
        issue, expires = entry["issue"], entry["expires"]
        if (
            not all(isinstance(entry[k], str) for k in ("nodeid", "assignee", "reason"))
            or not isinstance(issue, int)
            or isinstance(issue, bool)
            or not isinstance(expires, datetime.date)
            or isinstance(expires, datetime.datetime)
        ):
            raise SchemaError(f"{path.name}: entry {number} is not in the quarantine's shape")
    return entries


def quarantine_status(
    entries: Iterable[Mapping[str, Any]],
    on: datetime.date,
    reruns: Mapping[str, Mapping[str, int]] | None = None,
) -> list[dict[str, Any]]:
    """Each entry's issue, assignee and expiry, soonest first, and its reruns when they ran; on
    its date an entry has expired."""
    rows = [
        {
            "nodeid": e["nodeid"],
            "issue": e["issue"],
            "assignee": e["assignee"],
            "expires": e["expires"].isoformat(),
            "days_left": (e["expires"] - on).days,
            "expired": e["expires"] <= on,
            "reason": e["reason"],
            "reruns": dict(reruns[e["nodeid"]]) if reruns and e["nodeid"] in reruns else None,
        }
        for e in entries
    ]
    return sorted(rows, key=lambda r: (r["expires"], r["nodeid"]))


def read_reruns(path: Path) -> dict[str, dict[str, int]]:
    """Each quarantined test's reruns, as ``python -m tools.ci quarantine-rerun`` wrote them:
    how many of its rounds it passed, failed, skipped or did not run in, which add up to the
    rounds. A file in another shape fails the report."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SchemaError(f"{path.name}: {exc}") from None
    if not isinstance(doc, dict):
        raise SchemaError(f"{path.name} is not a {RERUN_SCHEMA} file")
    rounds, tests = doc.get("rounds"), doc.get("tests")
    if (
        doc.get("schema") != RERUN_SCHEMA
        or not isinstance(rounds, int)
        or isinstance(rounds, bool)
        or rounds < 1
        or not isinstance(tests, dict)
    ):
        raise SchemaError(f"{path.name} is not a {RERUN_SCHEMA} file")
    for nodeid, counts in tests.items():
        if (
            not isinstance(counts, dict)
            or set(counts) != set(RERUN_COUNTS)
            or not all(
                isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in counts.values()
            )
            or sum(counts.values()) != rounds
        ):
            raise SchemaError(f"{path.name}: {nodeid}'s reruns are not {rounds} rounds")
    return tests


def _reruns_cell(counts: Mapping[str, int] | None) -> str:
    if counts is None:
        return "no reruns"
    rounds = sum(counts.values())
    if counts["not_run"] == rounds:
        return "not run"
    cell = f"failed {counts['failed']} of {rounds}"
    if counts["not_run"]:
        cell += f", {counts['not_run']} not run"
    return cell


def _gh(*args: str) -> str:
    done = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
        timeout=GH_TIMEOUT_SECONDS,
    )
    return done.stdout


def _by_bot(user: object) -> bool:
    return isinstance(user, Mapping) and user.get("login") == BOT and user.get("type") == "Bot"


def publish(repo: str, body: str, *, run_url: str, gh: Callable[..., str] = _gh) -> int:
    """Edit the one open weekly issue this workflow's account opened, or open it once; return its
    number. Read from the issues listing, not from search, which lags behind a new issue, so a
    second report run soon after the first finds the issue the first opened. Two such issues (a
    race) edit the older and name the other."""
    tail = f"\n\nFrom {run_url}.\n"
    if len(body) + len(tail) > ISSUE_BODY_LIMIT:
        cut = (
            f"\n\n… cut here: GitHub keeps an issue under {ISSUE_BODY_LIMIT} characters. The "
            f"whole report is the run's summary and its report.json: {run_url}\n"
        )
        body, tail = body[: ISSUE_BODY_LIMIT - len(cut)], cut
    ours: list[int] = []
    for page in range(1, ISSUE_PAGES + 1):
        batch = json.loads(
            gh(
                "api",
                f"repos/{repo}/issues?state=open&creator=github-actions%5Bbot%5D"
                f"&per_page=100&page={page}",
            )
        )
        if not isinstance(batch, list):
            raise ValueError("the open issues are not a list")
        ours += [
            int(i["number"])
            for i in batch
            if i.get("title") == ISSUE_TITLE and _by_bot(i.get("user")) and "pull_request" not in i
        ]
        if len(batch) < 100:
            break
    else:
        raise ValueError(f"more than {ISSUE_PAGES * 100} open issues: the weekly one may be unseen")
    with tempfile.TemporaryDirectory() as scratch:
        text = Path(scratch) / "body.md"
        text.write_text(body + tail, encoding="utf-8")
        if not ours:
            url = gh(
                "issue", "create", "-R", repo, "--title", ISSUE_TITLE, "--body-file", str(text)
            )
            return int(url.strip().rsplit("/", 1)[-1])
        number, *others = sorted(ours)
        if others:
            print(
                "::warning title=more than one weekly CI health issue::edited "
                f"#{number}; close {', '.join(f'#{n}' for n in others)}"
            )
        gh("issue", "edit", str(number), "-R", repo, "--body-file", str(text))
        return number


def schedules(slots: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for slot in slots:
        name = str(slot.get("workflow"))
        row = by.setdefault(
            name,
            {"workflow": name, "ran": 0, "lags": [], "missed": [], "pending": [], "disabled": []},
        )
        status, due = slot.get("status"), slot.get("due_at")
        if status == "ran":
            row["ran"] += 1
            if slot.get("lag_seconds") is not None:
                row["lags"].append(int(slot["lag_seconds"]))
        elif status in ("missed", "pending", "disabled"):
            row[str(status)].append(due)
    out = []
    for name, row in sorted(by.items()):
        lags = row.pop("lags")
        row["median_lag_minutes"] = round(statistics.median(lags) / 60) if lags else None
        out.append(row)
    return out


def _histories(count: int) -> str:
    return f"{count} {'history' if count == 1 else 'histories'}"


def _record(record: Mapping[str, Any]) -> list[str]:
    """What the window holds of the 90 days, and how the fetch built it."""
    runs = f"{record['runs']} {'run' if record['runs'] == 1 else 'runs'}"
    lines = [f"The window holds {record['days']} of the {record['of']} days, {runs}."]
    fetch = record["fetch"]
    if fetch is None:
        return lines
    kind, _, why = fetch["carry"].partition(": ")
    if kind == "kept":
        lines.append(
            f"{_histories(fetch['carried'])} carried from the last weekly run, "
            f"{fetch['fetched']} fetched from the record."
        )
    elif kind == "refused":
        lines.append(
            f"**The carried window was refused**: {why}. The window was refilled from the record "
            f"within the budget: {_histories(fetch['fetched'])} fetched."
        )
    else:
        lines.append(
            f"No window was carried ({why or kind}): {_histories(fetch['fetched'])} fetched "
            "from the record."
        )
    if fetch["dropped"]:
        were = "history was" if fetch["dropped"] == 1 else "histories were"
        lines.append(f"{fetch['dropped']} carried {were} dropped when checked again.")
    if fetch["refused"]:
        lines.append(f"{_histories(fetch['refused'])} fetched were refused.")
    if fetch["left"]:
        lines.append(
            f"**{_histories(fetch['left'])} {'was' if fetch['left'] == 1 else 'were'} left for the "
            f"next run**, to keep {fetch['reserve']} of the token's calls for the other workflows."
        )
    if not fetch["slots"]:
        lines.append("No slots archive was fetched.")
    shown = fetch["refusals"][:REFUSALS_SHOWN]
    # Shown as code: a refusal can quote a name or a field an archive holds.
    lines += [f"- `{why.replace('`', chr(39))}`" for why in shown]
    if len(fetch["refusals"]) > len(shown):
        lines.append(f"- and {len(fetch['refusals']) - len(shown)} more, in the fetch's log")
    return lines


def markdown(data: Mapping[str, Any]) -> str:
    window = data["window"]
    lines = ["## Weekly CI health", ""]
    if not window["runs"]:
        lines.append("There is no history yet: no collected run of main.")
    elif window["mature"]:
        lines.append(
            f"The last {WINDOW_DAYS} days of history: {window['runs']} runs, from "
            f"{window['first']} to {window['last']}."
        )
    else:
        lines.append(
            f"{window['days']} days of history: {window['runs']} runs, from {window['first']} to "
            f"{window['last']}. The {WINDOW_DAYS}-day replay and maturity checks stay off until "
            f"{WINDOW_DAYS} days exist."
        )
    if window["runs"]:
        lines += ["", f"Suite size: {data['suite_size']} tests in the newest run."]
    lines += ["", "### The record", "", *_record(data["record"])]
    rated = [r for r in data["tests"] if r["flake_rate"]]
    unrated = [r for r in data["tests"] if r["flake_rate"] is None and r["runs"]]
    lines += ["", "### Flaky tests", ""]
    if rated:
        lines += [
            "| Test | Runs | First failures | Passed on rerun | Flake rate |",
            "|---|---|---|---|---|",
        ]
        for r in sorted(rated, key=lambda r: (-r["flake_rate"], r["test"])):
            lines.append(
                f"| `{r['test']}` | {r['runs']} | {r['first_failures']} | "
                f"{r['passed_on_rerun']} | {r['flake_rate']:.0%} |"
            )
    else:
        lines.append("No rated test failed first and then passed on a rerun.")
    if unrated:
        lines.append(
            f"\n{len(unrated)} test(s) have fewer than {MIN_RUNS} runs in the window and are "
            "not rated."
        )
    lines += ["", "### What the reruns found", ""]
    record = data["record"]
    if record["runs"]:
        lines += [
            "From each job's record, of each run's first attempt: every test the diagnostic "
            "rerun classified, and how many job records found it a flake or a repeated failure. "
            f"{record['runs_with_records']} of the window's {record['runs']} runs carried its "
            "jobs' records.",
            "",
        ]
    found = data["diagnosed"]
    if found:
        lines += ["| Test | Flakes | Repeated failures | Jobs |", "|---|---|---|---|"]
        for r in found[:DIAGNOSED_SHOWN]:
            jobs = ", ".join(f"`{job}`" for job in r["jobs"])
            lines.append(f"| `{r['test']}` | {r['flakes']} | {r['repeated_failures']} | {jobs} |")
        if len(found) > DIAGNOSED_SHOWN:
            lines.append(f"\n{len(found) - DIAGNOSED_SHOWN} more in report.json.")
    else:
        lines.append("No job record in the window classified a failed test.")
    lines += ["", "### Slowest tests", ""]
    if data["slowest"]:
        lines += ["| Test | p50 s | p99 s | Week over week |", "|---|---|---|---|"]
        for r in data["slowest"]:
            trend = "" if r["trend"] is None else f"{r['trend']:+.0%}"
            lines.append(
                f"| `{r['test']}` | {r['p50_seconds']:.1f} | {r['p99_seconds']:.1f} | {trend} |"
            )
    lines += ["", "### Quarantine", ""]
    if data["quarantine"]:
        lines += ["| Test | Issue | Assignee | Expires | Reruns |", "|---|---|---|---|---|"]
        for q in data["quarantine"]:
            expires = (
                f"**expired {q['expires']}**"
                if q["expired"]
                else f"{q['expires']}, {q['days_left']} days left"
            )
            lines.append(
                f"| `{q['nodeid']}` | #{q['issue']} | @{q['assignee']} | {expires} | "
                f"{_reruns_cell(q['reruns'])} |"
            )
    else:
        lines.append("The quarantine is empty.")
    browse = data["browse_live"]
    lines += ["", "### Browse-live's environment quarantine", ""]
    if browse["measured"] < browse["window"]:
        heads = "head" if browse["measured"] == 1 else "heads"
        lines.append(
            f"{browse['measured']} measured {heads} on main of the {browse['window']} a rate "
            "needs: no rate yet."
        )
    else:
        count = f"{browse['quarantined']} of the last {browse['window']}"
        lines.append(
            f"{f'**{count}**' if browse['recurring'] else count} measured heads on main carried "
            f"`{telemetry.ENVIRONMENT_MARKER}`; the alert is at {browse['threshold']} of "
            f"{browse['window']}."
        )
    for o in browse["observations"]:
        attempt = f" attempt {o['attempt']}" if o["attempt"] > 1 else ""
        verdict = "quarantined" if o["quarantined"] else "clear"
        lines.append(f"- `{o['sha'][:12]}` run {o['run_id']}{attempt}: {verdict}")
    lines += ["", "### Scheduled runs", ""]
    if not data["schedules"]:
        lines.append("No slots file was read.")
    for row in data["schedules"]:
        lag = (
            ""
            if row["median_lag_minutes"] is None
            else f", median {row['median_lag_minutes']} min late"
        )
        lines.append(f"- {row['workflow']}: {row['ran']} ran{lag}")
        lines += [f"  - **{row['workflow']} missed its {due} run**" for due in row["missed"]]
        lines += [
            f"  - {row['workflow']}'s {due} run has not started yet" for due in row["pending"]
        ]
        lines += [f"  - {row['workflow']} is disabled: no {due} run" for due in row["disabled"]]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ci report")
    parser.add_argument("--history", type=Path, required=True, help="a folder of telemetry files")
    parser.add_argument("--json", type=Path, default=None, help="write the figures here too")
    parser.add_argument(
        "--quarantine", type=Path, default=QUARANTINE, help="the quarantine list to show"
    )
    parser.add_argument("--reruns", type=Path, default=None, help="the quarantine's reruns")
    parser.add_argument("--issue-repo", default=None, help="write the weekly issue here")
    parser.add_argument("--run-url", default="", help="the run the issue names")
    parser.add_argument(
        "--fetch-status", type=Path, default=None, help="how the fetch built the window"
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if not args.history.is_dir():
        print(f"::error title=the report did not run::{args.history} is not a folder")
        return 2
    try:
        quarantine = read_quarantine(args.quarantine)
        reruns = read_reruns(args.reruns) if args.reruns else None
        fetched = read_fetch_status(args.fetch_status) if args.fetch_status else None
        data = figures(
            read(args.history),
            read_slots(args.history),
            quarantine,
            reruns=reruns,
            fetched=fetched,
        )
    except (SchemaError, KeyError, TypeError, ValueError, OSError) as err:
        print(f"::error title=the report did not run::{err}")
        return 2
    text = markdown(data)
    print(text, end="")
    browse = data["browse_live"]
    if browse["recurring"]:
        # In the log only, where the runner reads it: the summary and the issue carry the row.
        print(
            "::error title=browse-live environment quarantine is recurring::"
            f"{browse['quarantined']} of the last {browse['window']} measured heads on main "
            f"carried '{telemetry.ENVIRONMENT_MARKER}' (alert at {browse['threshold']} of "
            f"{browse['window']})"
        )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    if args.issue_repo:
        try:
            number = publish(args.issue_repo, text, run_url=args.run_url)
        except (subprocess.SubprocessError, OSError, ValueError, KeyError) as err:
            print(f"::error title=the weekly issue was not written::{err}")
            return 2
        print(f"the weekly issue: #{number}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
