"""``python -m tools.ci telemetry``: the one writer of CI telemetry (#3948).

Every record of CI history, per run and per test, is written here, in one versioned schema, and
nowhere else. ``collect`` runs in ``.github/workflows/ci-telemetry.yml`` after a CI or Breadth
run completes, from main's own code, and writes ``history-<run>-<attempt>.jsonl``:

- the first line is the run's: the schema and its version, then the facts only the Actions API
  knows (queue delay, setup, time to the first failed job, verdict time, runner-minutes,
  cancelled work, artifact volume, schedule lag) and one entry per job;
- then one line per job record the attempt uploaded (version 4), by key;
- every other line is one test case of that attempt's junit reports: its id, its first result,
  its seconds, the report it came from. On a tested push, the pull request run that tested the
  pushed tree adds its own (below).

Every line is within ``MAX_LINE_BYTES``, its line break included, the bound each reader (the
weekly report and its fetch, the shard planner, the parity check) holds a history line to and
refuses a whole history past: a line that would pass it fails the collection and writes nothing.

**Which runs count.** A push to ``main``, of ``merge.yml`` (``workflows.MAIN_PUSH``), and a
scheduled or dispatched Breadth run on ``main``, all from this repository. That is decided from
the run itself, before any artifact is listed or downloaded, so a pull request's run is never
collected. Its artifacts are read only as a tested push's suite, below.

**Where the reports are.** An artifact named ``junit-*`` is one report, named as the artifact.
merge.yml uploads each entry's reports folder as ``reports-<slug>-<attempt>``: each
``junit-*.xml`` directly in its ``reports/`` (or at its root, when ``reports/`` was the upload's
one folder) is one report, named as the file without ``.xml``, so ``junit-test-shard-1`` either
way. Nothing else in such an archive is read. Every upload of a junit report in a collected
workflow is named with one of :data:`REPORT_ARTIFACT_PREFIXES`, which a rail holds.

**A tested push's suite** (version 5). A push to main whose tree a green pull request run
already tested runs only the always-run jobs, so its own reports hold no suite. The collector
then also reads that run's first attempt, the one ``tree-check`` names: the same rules
(``tools/ci/tree_tested.py``'s ``decide``, with the pushed commit's parent as the commit it
replaced) find exactly one pull request into main merged as the pushed commit, whose head has
the pushed tree and holds that parent, and whose runs on that head all passed on their first
attempt. The run must also be a ``merge.yml`` pull_request run of this repository on that head:
a run on an older head, a fork's, or an unmerged pull request's is never read. That run ran the
pushed tree's own code, as the push would have. Its reports add test lines carrying its own run
id, attempt and head, for each report the push did not upload itself, and the run line's
``tested_by`` names it (null when no such run was found, or its reports could not be read, which
the job's log names). Version 5 adds ``tested_by`` and changes nothing else.

**A first result is never replaced.** Each attempt is collected on its own, from that attempt's
own facts (``attempts/<n>``: its start, its end, its conclusion), its own jobs, and only the
reports uploaded between its start and its end. So attempt 1's file keeps the first result even
when it is collected after a rerun has run, and a rerun's file sits beside it, never over it.
Two reports with one name in one attempt are read once, the lower artifact id. Jobs and
artifacts are listed page by page against the API's ``total_count``; a listing that comes up
short fails the collection.

**Where each test ran.** The suite records, on each process's first test case, its platform
(OS, machine and Python) and its number of xdist workers (``tests/run_facts.py``). Every test
line of that report carries them, so a reader can keep one platform's and one worker count's
seconds apart. On Linux and macOS it also records the host's CPU model, and the run line's
``cpus`` maps each report to it (null for a report that recorded none), so a reader can weigh a
report's seconds by how fast its host model is.

**The CPU model** (version 3). Version 3 adds the run line's ``cpus`` and changes nothing else,
so every reader takes a version 1 or 2 history (:data:`READS`) as one whose reports recorded no
CPU model.

**Each job's record** (version 4). Each test-stage entry uploads its job's record, the one
``run-job`` writes (``tools/ci/verdict.py``), as ``ci-record-<key slug>-<run>-<attempt>``. Each
record the attempt uploaded is one ``record`` line after the run line, by key: the job's result,
each failed test with the diagnostic rerun's word for it (null when it was not classified), and
each quarantined failure. That is the one place every runner's classification lands, Playwright's
included, which no junit report carries. A line of its own holds each record within the line bound
however many jobs failed: one run line holding them all would pass it in a run with several
thousand failed tests, and lose the run, its test lines too, to every reader. A record archive
holds one ``.json`` file within the record's bound, read in memory with
``verdict.from_job_records``, in the artifact its own key, this run and this attempt name; anything
else fails the collection, as a report that cannot be read does, since a history with fewer records
would read as reruns that found less. So a job whose record passes that bound (several thousand
failed tests: ``run-job`` does not yet hold its record within it, and ``verdict --records`` refuses
that record too) leaves its run with no history. Version 4 adds the record lines and changes
nothing else: the shard planner and the parity check read test lines alone, and every reader takes
an older history as one that carries no records.

**Artifacts are data.** Their content is never executed or extracted to disk: an archive larger
than ``MAX_ARCHIVE_BYTES`` is never downloaded, a report is read in memory up to
``MAX_JUNIT_BYTES``, a report that is not UTF-8 is refused, and so is one that declares a
DOCTYPE anywhere. A report that cannot be read fails the collection and writes nothing, since a
history with fewer tests would read as a run that ran fewer.

**The diagnostic rerun.** ``run-job`` reruns a failed test once, alone, and writes its
classification on the case as a ``rerun_result`` property: ``flake`` or ``repeated failure``. A
test line carries it as ``rerun_result``, null for a case that was not rerun. Any other value,
two on one case, or one on a case whose first result was not a failure, fails the collection.
The first result is never replaced by it.

**The environment marker** (version 2). The suite names a red the environment caused, rather than
the code under test, with the exact words ``ENVIRONMENT, NOT POLICY`` in the failure: the browse
proof's page that stayed detached. A test line's ``environment_failure`` is true when its case
failed or errored with those words in the failure's message or text, and false for every other
case. Version 2 adds that field and changes nothing else, so every reader takes a version-1
history (:data:`READS`) as one whose lines carry no such field.

Quarantine facts on a test line (``quarantined``) and cache hits (``cache_hits``) no API reports,
so until they are written they are null, never guessed. Standard library only, like every file in
``tools/ci``.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import tree_tested, verdict, workflows

SCHEMA = "personalclaw-ci-telemetry"
VERSION = 5
#: The versions a reader takes: this one; version 4, whose run line lacks ``tested_by``; version
#: 3, which lacks ``records`` too; version 2, which lacks ``cpus`` too; and version 1, whose test
#: lines also lack ``environment_failure``.
READS = frozenset({1, 2, 3, 4, VERSION})

#: The run line's fields, after ``kind``, ``schema`` and ``version``.
RUN_FIELDS = (
    "run_id",
    "attempt",
    "workflow",
    "event",
    "branch",
    "sha",
    "conclusion",
    "created_at",
    "started_at",
    "updated_at",
    "verdict_seconds",
    "queue_delay_seconds",
    "first_failure_seconds",
    "runner_minutes",
    "cancelled_minutes",
    "artifact_bytes",
    "schedule_lag_seconds",
    "cache_hits",
    "jobs",
    "cpus",
    "tested_by",
)

#: A record line's fields, exactly: one line per job record the attempt uploaded (version 4).
RECORD_FIELDS = ("kind", "key", "result", "rerun", "quarantined")

#: A test line's fields, exactly.
TEST_FIELDS = (
    "kind",
    "run_id",
    "attempt",
    "workflow",
    "sha",
    "report",
    "runner",
    "test",
    "seconds",
    "first_result",
    "rerun_result",
    "environment_failure",
    "quarantined",
    "platform",
    "workers",
    "worker",
    "position",
)

#: The junit properties ``tests/run_facts.py`` records on each process's first test case.
PLATFORM_PROPERTY = "ci_platform"
WORKERS_PROPERTY = "ci_workers"
CPU_PROPERTY = "ci_cpu"

#: Where the workflows live, repository-relative: the folder the Actions API's ``path`` names.
WORKFLOWS = ".github/workflows"
#: The workflows whose runs are collected, by the ``path`` the API reports for a run: a push to
#: main's, merge.yml's, and breadth's.
MERGE_PATH = f"{WORKFLOWS}/{workflows.MERGE}"
BREADTH_PATH = f"{WORKFLOWS}/breadth.yml"
#: The collector's own workflow: the run that uploads each history, which a reader checks.
COLLECTOR_PATH = f"{WORKFLOWS}/ci-telemetry.yml"

#: The workflows whose runs are collected, and the events that count for each: each workflow a
#: push to main runs in (:data:`workflows.MAIN_PUSH`), on its push, and breadth's nights.
COUNTED = {
    **{name: frozenset({"push"}) for name in workflows.MAIN_PUSH},
    "breadth.yml": frozenset({"schedule", "workflow_dispatch"}),
}

#: An artifact that is one junit report, named as the report.
REPORT_PREFIX = "junit-"
#: merge.yml's archive of one entry's reports folder, ``reports-<slug>-<attempt>``.
REPORTS_PREFIX = "reports-"
#: Every name an artifact the collector reads reports from starts with.
REPORT_ARTIFACT_PREFIXES = (REPORT_PREFIX, REPORTS_PREFIX)
#: The artifact ``ci-telemetry.yml`` uploads each attempt's history as; the shard planner reads it.
HISTORY_ARTIFACT = "telemetry-history-{run}-{attempt}"
#: The artifact its ``slots`` job uploads, by the collector run that wrote it.
SLOTS_ARTIFACT = "telemetry-slots-{run}"
#: The events a collector run of each kind is triggered by: a history follows a completed run,
#: the slots run on the collector's own schedule or on dispatch.
HISTORY_EVENTS = frozenset({"workflow_run"})
SLOTS_EVENTS = frozenset({"schedule", "workflow_dispatch"})
#: A collector archive larger than this is never downloaded: one main run's history zips to
#: about 3 MB.
MAX_HISTORY_ARCHIVE_BYTES = 32 * 1024 * 1024
#: The weekly report's window, one stored archive of collector archives, at most: this many
#: members, and these bytes in all (90 days are about 860 archives of about 3 MB).
MAX_WINDOW_MEMBERS = 2000
MAX_WINDOW_BYTES = 6 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
#: A ``reports-*`` archive larger than this is never downloaded. It holds the entry's other output
#: too (an e2e shard's Playwright report and traces measured 7.4 MB on a green run), and each
#: report in it is still read within :data:`MAX_JUNIT_BYTES`.
MAX_REPORTS_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_JUNIT_BYTES = 8 * 1024 * 1024
#: The most one history line holds, its line break included. Every reader refuses a history with
#: a longer line whole, so the collector writes none: a test line is a few hundred bytes, a run
#: line with its jobs a few tens of kilobytes, and a record line about its record's size.
MAX_LINE_BYTES = 1024 * 1024
#: Pages of 100 read for one listing at most: a Breadth attempt uploads about 63 artifacts.
MAX_PAGES = 10
#: How long one ``gh`` call may take, so a hung call cannot hold the job to its limit.
GH_TIMEOUT_SECONDS = 120


class CollectError(Exception):
    """The attempt's history cannot be written whole."""


# ── gh ────────────────────────────────────────────────────────────────────────────────────────


def _gh_text(*args: str) -> str:
    done = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        errors="replace",
        check=True,
        timeout=GH_TIMEOUT_SECONDS,
    )
    return done.stdout


def _gh_binary(*args: str) -> bytes:
    return subprocess.run(
        ["gh", *args], capture_output=True, check=True, timeout=GH_TIMEOUT_SECONDS
    ).stdout


def _json(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CollectError(f"{what}: not JSON ({exc})") from None


def _listed(text: Callable[..., str], path: str, key: str, what: str) -> list[Any]:
    """Every item of a paged listing, checked against its ``total_count``."""
    items: list[Any] = []
    total = 0
    for page in range(1, MAX_PAGES + 1):
        doc = _json(text("api", f"{path}?per_page=100&page={page}"), what)
        if not isinstance(doc, dict) or not isinstance(doc.get("total_count"), int):
            raise CollectError(f"{what}: the listing has no total_count")
        total = int(doc["total_count"])
        batch = doc.get(key) or []
        if not isinstance(batch, list):
            raise CollectError(f"{what}: the listing's {key} is not a list")
        items += batch
        if len(items) >= total or not batch:
            break
    if len(items) < total:
        raise CollectError(f"{what}: {len(items)} of {total} listed")
    return items


# ── whose upload is the record ────────────────────────────────────────────────────────────────
#
# Any run can upload an artifact of any name, a fork's pull request included. A reader of the
# record (the shard planner, the weekly report's fetch) takes an artifact only when the artifact
# says a run on main of this repository's own code made it, and that run is the collector's.


def own_run(run: object, repo: str, path: str, events: frozenset[str]) -> bool:
    """Whether ``run``, a run as the Actions API describes it, is a run of the workflow at
    ``path``, triggered by one of ``events``, on main, in ``repo``: main's own code."""
    if not isinstance(run, Mapping):
        return False
    head = run.get("head_repository")
    return (
        run.get("path") == path
        and run.get("event") in events
        and run.get("head_branch") == "main"
        and isinstance(head, Mapping)
        and head.get("full_name") == repo
    )


def collector_run(run: object, repo: str, events: frozenset[str] = HISTORY_EVENTS) -> bool:
    """Whether ``run`` is the collector's own: a ``ci-telemetry.yml`` run of one of ``events``,
    on main, in ``repo``."""
    return own_run(run, repo, COLLECTOR_PATH, events)


def member_refusal(info: zipfile.ZipInfo) -> str | None:
    """Why an archive's member is never read, or None. A reader opens members in memory and
    never writes one to disk, so a path cannot escape; a member is still refused when its name is
    absolute, holds a parent path or a folder, or it is a link or a folder itself, since the
    collector writes none of those."""
    name = info.filename
    mode = (info.external_attr >> 16) & 0o170000
    if name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
        return f"{name!r} is an absolute path"
    if ".." in name.replace("\\", "/").split("/"):
        return f"{name!r} holds a parent path"
    if "/" in name or "\\" in name or info.is_dir():
        return f"{name!r} is under a folder, or is one"
    if mode == 0o120000:
        return f"{name!r} is a link"
    return None


def upload_refusal(artifact: object) -> str | None:
    """Why an artifact is not one a run on main of this repository made, or None: it is
    unexpired and, as it names the run that uploaded it, that run was on main, not a fork's. That
    run is still to be checked with :func:`collector_run`, by its ``workflow_run.id``."""
    if not isinstance(artifact, Mapping):
        return "is not an artifact"
    if artifact.get("expired"):
        return "has expired"
    made_by = artifact.get("workflow_run")
    if not isinstance(made_by, Mapping) or not isinstance(made_by.get("id"), int):
        return "names no run that uploaded it"
    if made_by.get("head_branch") != "main":
        return f"was uploaded on {made_by.get('head_branch')!r}, not main"
    if made_by.get("head_repository_id") != made_by.get("repository_id"):
        return "was uploaded by a fork's run"
    return None


# ── which runs count ──────────────────────────────────────────────────────────────────────────


def collect_condition() -> str:
    """The ``if:`` of ``ci-telemetry.yml``'s collect job: :func:`counts`'s rule, written from
    :data:`COUNTED`, so the workflow and the code cannot drift apart (the rail compares them)."""
    run = "github.event.workflow_run"
    per_workflow = " || ".join(
        f"({run}.path == '{WORKFLOWS}/{workflow}' && ("
        + " || ".join(f"{run}.event == '{event}'" for event in sorted(events))
        + "))"
        for workflow, events in sorted(COUNTED.items())
    )
    return (
        f"github.event_name == 'workflow_run' && {run}.head_branch == 'main'"
        f" && {run}.head_repository.full_name == github.repository && ({per_workflow})"
    )


def counts(run: Mapping[str, Any], repo: str) -> str | None:
    """Why ``run`` is not collected, or None when it is."""
    workflow = str(run.get("path", "")).rsplit("/", 1)[-1]
    if workflow not in COUNTED:
        return f"{workflow or 'an unnamed workflow'} is not collected"
    head = run.get("head_repository") or {}
    if not isinstance(head, Mapping) or head.get("full_name") != repo:
        return "its head is another repository's"
    if run.get("head_branch") != "main":
        return f"it ran on {run.get('head_branch')!r}, not main"
    if run.get("event") not in COUNTED[workflow]:
        return f"a {run.get('event')} run of {workflow} is not collected"
    return None


# ── times ─────────────────────────────────────────────────────────────────────────────────────


def _at(value: object) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _seconds(start: object, end: object) -> int | None:
    a, b = _at(start), _at(end)
    return None if a is None or b is None else max(0, round((b - a).total_seconds()))


REPO = Path(__file__).resolve().parents[2]


def _crons(path: str) -> list[str]:
    """The ``cron:`` lines of the workflow at ``path`` (repository-relative) in this checkout,
    which is main's."""
    try:
        text = (REPO / path).read_text(encoding="utf-8")
    except OSError:
        return []
    return re.findall(r"""^\s*-?\s*cron:\s*["']([^"']+)["']""", text, re.M)


def _scheduled_workflows() -> list[str]:
    """Every workflow in this checkout with a ``cron:`` line."""
    return sorted(
        p.name for p in (REPO / WORKFLOWS).glob("*.yml") if _crons(f"{WORKFLOWS}/{p.name}")
    )


def _field(spec: str, value: int, low: int) -> bool:
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
        if part == "*":
            start, end = low, 10**6
        elif "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
        else:
            start = end = int(part)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def _cron_matches(cron: str, at: datetime.datetime) -> bool:
    minute, hour, dom, month, dow = cron.split()[:5]
    weekday = (at.weekday() + 1) % 7  # cron's Sunday is 0
    on_day = _field(dom, at.day, 1)
    on_weekday = _field(dow, weekday, 0) or (dow != "*" and weekday == 0 and _field(dow, 7, 0))
    # cron's rule: when both the day of the month and the day of the week are restricted, a
    # time matches on either one; when one is `*`, only the other decides.
    day = (on_day or on_weekday) if dom != "*" and dow != "*" else (on_day and on_weekday)
    return (
        _field(minute, at.minute, 0)
        and _field(hour, at.hour, 0)
        and _field(month, at.month, 1)
        and day
    )


def schedule_lag(crons: Sequence[str], created: datetime.datetime) -> int | None:
    """Seconds from the newest scheduled time at or before ``created`` to ``created``, over the
    eight days before it (the longest gap a weekly cron leaves)."""
    at = created.replace(second=0, microsecond=0)
    for _ in range(8 * 24 * 60):
        if any(_cron_matches(cron, at) for cron in crons):
            return round((created - at).total_seconds())
        at -= datetime.timedelta(minutes=1)
    return None


# ── junit ─────────────────────────────────────────────────────────────────────────────────────


def _result(case: ET.Element) -> str:
    if case.find("failure") is not None:
        return "failed"
    if case.find("error") is not None:
        return "error"
    skipped = case.find("skipped")
    if skipped is not None:
        return "xfailed" if skipped.get("type") == "pytest.xfail" else "skipped"
    return "passed"


#: The junit property ``run-job``'s diagnostic rerun writes on each failed case it classified,
#: and its two words, ``tools/ci/verdict.py``'s ``FLAKE`` and ``REPEATED_FAILURE``.
RERUN_PROPERTY = "rerun_result"
FLAKE = verdict.FLAKE
RERUN_RESULTS = verdict.RERUN_RESULTS

#: The words a failure carries when the environment, not the code under test, caused it.
ENVIRONMENT_MARKER = "ENVIRONMENT, NOT POLICY"

Case = tuple[str, float, str, str | None, str | None, bool]
"""One test case: its id, its seconds, its first result, the runner host it ran on, the
diagnostic rerun's classification of it (None when it was not rerun), and whether its failure
carried :data:`ENVIRONMENT_MARKER`."""


def _environment_failure(case: ET.Element) -> bool:
    """Whether a failure or an error of the case carries the marker, in its message or text."""
    for element in (*case.findall("failure"), *case.findall("error")):
        text = (element.get("message") or "") + "".join(element.itertext())
        if ENVIRONMENT_MARKER in text:
            return True
    return False


def _rerun_result(name: str, test: str, first: str, case: ET.Element) -> str | None:
    """The case's ``rerun_result`` property, refused unless it is one property, one of the two
    words, on a case whose first result was a failure: a report that says otherwise is not this
    rerun's."""
    words = [p.get("value") for p in case.iter("property") if p.get("name") == RERUN_PROPERTY]
    if not words:
        return None
    if len(words) > 1:
        raise CollectError(f"{name}: {test} has more than one rerun_result: {words}")
    [word] = words
    if word not in RERUN_RESULTS:
        raise CollectError(f"{name}: {test} has a rerun_result of {word!r}")
    if first not in ("failed", "error"):
        raise CollectError(f"{name}: {test} has a rerun_result, and its first result is {first}")
    return word


def junit_members(name: str, archive: bytes) -> list[str]:
    """The reports a ``reports-*`` archive holds: each ``junit-*.xml`` file directly in its
    ``reports/``, or at its root. Nothing else in it is read."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            infos = zipped.infolist()
    except zipfile.BadZipFile:
        raise CollectError(f"{name}: not a zip archive") from None
    found = []
    for info in infos:
        folder, _, base = info.filename.rpartition("/")
        mode = (info.external_attr >> 16) & 0o170000
        if folder not in ("", "reports") or info.is_dir() or mode == 0o120000:
            continue
        if base.startswith(REPORT_PREFIX) and base.endswith(".xml"):
            found.append(info.filename)
    return sorted(found)


def report_cases(name: str, archive: bytes) -> tuple[list[Case], str | None, int | None]:
    """Each test case of one junit archive, and the platform and worker count the suite recorded
    in it (``tests/run_facts.py``), or None for a report that records none."""
    cases, platform, workers, _ = report_facts(name, archive)
    return cases, platform, workers


def report_facts(
    name: str, archive: bytes, member: str | None = None
) -> tuple[list[Case], str | None, int | None, str | None]:
    """:func:`report_cases`, and the CPU model the report's processes recorded, or None. With
    ``member``, that one file of the archive is the report, and nothing else in it is read."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            members = [
                m
                for m in zipped.infolist()
                if not m.is_dir()
                and m.filename.endswith(".xml")
                and (member is None or m.filename == member)
            ]
            if not members:
                raise CollectError(f"{name}: the archive holds no junit report")
            cases: list[Case] = []
            facts: set[tuple[str | None, str | None]] = set()
            models: set[str] = set()
            budget = MAX_JUNIT_BYTES
            for info in members:
                with zipped.open(info) as fh:
                    data = fh.read(budget + 1)
                budget -= len(data)
                if budget < 0:
                    raise CollectError(f"{name}: its reports pass {MAX_JUNIT_BYTES} bytes")
                try:
                    data.decode("utf-8")
                except UnicodeDecodeError:
                    raise CollectError(f"{name}: a report is not UTF-8") from None
                if b"<!DOCTYPE" in data.upper():
                    raise CollectError(f"{name}: a report declares a DOCTYPE")
                root = ET.parse(io.BytesIO(data)).getroot()
                for suite in root.iter("testsuite"):
                    host = suite.get("hostname")
                    for case in suite.iter("testcase"):
                        test = f"{case.get('classname') or ''}::{case.get('name') or ''}"
                        found = {p.get("name"): p.get("value") for p in case.iter("property")}
                        first = _result(case)
                        rerun = _rerun_result(name, test, first, case)
                        environment = first in ("failed", "error") and _environment_failure(case)
                        seconds = float(case.get("time") or 0.0)
                        cases.append((test, seconds, first, host, rerun, environment))
                        if PLATFORM_PROPERTY in found or WORKERS_PROPERTY in found:
                            facts.add((found.get(PLATFORM_PROPERTY), found.get(WORKERS_PROPERTY)))
                        if found.get(CPU_PROPERTY):
                            models.add(str(found[CPU_PROPERTY]))
    except zipfile.BadZipFile:
        raise CollectError(f"{name}: not a zip archive") from None
    except (ET.ParseError, ValueError) as exc:
        raise CollectError(f"{name}: not a junit report ({exc})") from None
    if len(facts) > 1:
        raise CollectError(f"{name}: its processes recorded more than one platform: {facts}")
    if len(models) > 1:
        raise CollectError(f"{name}: its processes recorded more than one CPU model: {models}")
    platform, workers = next(iter(facts), (None, None))
    if workers is not None and not workers.isdigit():
        raise CollectError(f"{name}: a worker count of {workers!r}")
    count = int(workers) if workers is not None else None
    return cases, platform, count, next(iter(models), None)


# ── each job's record ─────────────────────────────────────────────────────────────────────────

#: The name every job record's artifact starts with (``verdict.record_artifact``).
RECORD_PREFIX = verdict.RECORD_ARTIFACT_PREFIX
#: A record archive larger than this is never downloaded: the one record it holds is within
#: ``verdict.RECORD_BYTES``.
MAX_RECORD_ARCHIVE_BYTES = 2 * 1024 * 1024


def record_bytes(
    name: str, archive: bytes, run_id: int, attempt: int
) -> tuple[bytes, verdict.JobResult]:
    """The one record the job record artifact ``name`` of this run's ``attempt`` holds, as its
    bytes and as read. The archive holds one ``.json`` file, read in memory within
    ``verdict.RECORD_BYTES`` and with ``verdict.from_job_records``, the record of this run and
    this attempt, in the artifact its own key names. Anything else raises :class:`CollectError`.
    A push's replay reads a run's records with it too (``tools/ci/main_replay.py``)."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            members = zipped.infolist()
            if not members:
                raise CollectError(f"{name}: the archive holds no record")
            if len(members) != 1:
                raise CollectError(f"{name}: the archive holds {len(members)} files, not one")
            [member] = members
            why = member_refusal(member)
            if why is not None:
                raise CollectError(f"{name}: {why}")
            if not member.filename.endswith(".json"):
                raise CollectError(f"{name}: {member.filename!r} is not a record file")
            with zipped.open(member) as fh:
                data = fh.read(verdict.RECORD_BYTES + 1)
    except (zipfile.BadZipFile, OSError, EOFError) as exc:
        raise CollectError(f"{name}: not a record archive ({exc})") from None
    if len(data) > verdict.RECORD_BYTES:
        raise CollectError(f"{name}: larger than any job record")
    try:
        [result] = verdict.from_job_records([data.decode("utf-8")])
    except UnicodeDecodeError:
        raise CollectError(f"{name}: the record is not UTF-8") from None
    except verdict.Refused as exc:
        raise CollectError(f"{name}: {exc}") from None
    expected = verdict.record_artifact(result.key, run_id=run_id, attempt=result.attempt)
    if name != expected:
        raise CollectError(
            f"{name} holds the record of {result.key!r} on attempt {result.attempt}, which this"
            f" run uploads as {expected}"
        )
    if result.attempt != attempt:
        raise CollectError(f"{name}: written by attempt {result.attempt}, not attempt {attempt}")
    if result.run_id != run_id:
        raise CollectError(f"{name}: the record names run {result.run_id}, not run {run_id}")
    return data, result


def record_line(name: str, archive: bytes, run_id: int, attempt: int) -> dict[str, Any]:
    """The history's line for the job record artifact ``name`` of this run's ``attempt``
    (:func:`record_bytes`): the job's key and result, each failed test with the diagnostic
    rerun's word for it (None when it was not classified), and each quarantined failure."""
    _, result = record_bytes(name, archive, run_id, attempt)
    failed = [t for t in result.tests if t.standing != verdict.QUARANTINED]
    return {
        "kind": "record",
        "key": result.key,
        "result": result.result,
        "rerun": [
            {"test": t.test, "rerun_result": t.standing if t.standing in RERUN_RESULTS else None}
            for t in failed
        ],
        "quarantined": [t.test for t in result.tests if t.standing == verdict.QUARANTINED],
    }


# ── the collection ────────────────────────────────────────────────────────────────────────────


def _job_minutes(job: Mapping[str, Any]) -> float:
    return (_seconds(job.get("started_at"), job.get("completed_at")) or 0) / 60


def _job_entry(job: Mapping[str, Any]) -> dict[str, Any]:
    setup = next(
        (
            s
            for s in job.get("steps") or []
            if isinstance(s, Mapping) and s.get("name") == "Set up job"
        ),
        None,
    )
    return {
        "name": job.get("name"),
        "conclusion": job.get("conclusion"),
        "runner": list(job.get("labels") or []),
        "queue_seconds": _seconds(job.get("created_at"), job.get("started_at")),
        "setup_seconds": (
            _seconds(setup.get("started_at"), setup.get("completed_at")) if setup else None
        ),
        "minutes": round(_job_minutes(job), 2),
    }


def _uploads(
    text: Callable[..., str], base: str, this: Mapping[str, Any], attempt: int, run_id: int
) -> list[Mapping[str, Any]]:
    """The artifacts the attempt ``this`` of the run at ``base`` uploaded: only those made between
    its start and its end, since one made before it started is an earlier attempt's and one made
    after it ended a later attempt's."""
    since, until = _at(this.get("run_started_at")), _at(this.get("updated_at"))
    if since is None or until is None:
        raise CollectError(f"attempt {attempt} of run {run_id} has no start or no end")
    artifacts = []
    for a in _listed(text, f"{base}/artifacts", "artifacts", "the artifacts"):
        made = _at(a.get("created_at"))
        if a.get("expired") or made is None:
            continue
        if since <= made <= until:
            artifacts.append(a)
    return artifacts


def _download(
    binary: Callable[..., bytes], repo: str, artifact: Mapping[str, Any], limit: int
) -> bytes:
    name = str(artifact.get("name", ""))
    if int(artifact.get("size_in_bytes") or 0) > limit:
        raise CollectError(f"{name}: the archive passes {limit} bytes")
    data = binary("api", f"repos/{repo}/actions/artifacts/{artifact['id']}/zip")
    if len(data) > limit:
        raise CollectError(f"{name}: the archive passes {limit} bytes")
    return data


def _test_lines(
    binary: Callable[..., bytes],
    repo: str,
    artifacts: Sequence[Mapping[str, Any]],
    *,
    run_id: int,
    attempt: int,
    workflow: str,
    sha: object,
    skip: frozenset[str] = frozenset(),
) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    """One line per test case of every report the artifacts hold, and each report's CPU model.
    Two reports with one name are read once, from the lower artifact id, and a report named in
    ``skip`` is not read."""
    found: dict[str, tuple[bytes, str | None, str]] = {}
    for artifact in sorted(artifacts, key=lambda a: int(a["id"])):
        name = str(artifact.get("name", ""))
        if name.startswith(REPORT_PREFIX):
            if name not in found and name not in skip:
                found[name] = (_download(binary, repo, artifact, MAX_ARCHIVE_BYTES), None, name)
        elif name.startswith(REPORTS_PREFIX):
            data = _download(binary, repo, artifact, MAX_REPORTS_ARCHIVE_BYTES)
            for file in junit_members(name, data):
                report = file.rpartition("/")[2][: -len(".xml")]
                if report not in found and report not in skip:
                    found[report] = (data, file, f"{name}'s {file}")
    tests: list[dict[str, Any]] = []
    cpus: dict[str, str | None] = {}
    for report, (archive, member, where) in sorted(found.items()):
        cases, platform, workers, cpus[report] = report_facts(where, archive, member)
        for test, seconds, result, host, rerun, environment in cases:
            tests.append(
                {
                    "kind": "test",
                    "run_id": run_id,
                    "attempt": attempt,
                    "workflow": workflow,
                    "sha": sha,
                    "report": report,
                    "runner": host,
                    "test": test,
                    "seconds": seconds,
                    "first_result": result,
                    "rerun_result": rerun,
                    "environment_failure": environment,
                    "quarantined": None,
                    "platform": platform,
                    "workers": workers,
                    "worker": None,
                    "position": None,
                }
            )
    return tests, cpus


def _api(text: Callable[..., str]) -> tree_tested.Get:
    """``tree_tested``'s reader, through ``gh``: an answer it cannot read is ``Unanswered``."""

    def get(path: str) -> Any:
        try:
            return json.loads(text("api", path.lstrip("/")))
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            raise tree_tested.Unanswered(f"GET {path}: {exc}") from None

    return get


def _tested_run(
    text: Callable[..., str], repo: str, run: Mapping[str, Any]
) -> tuple[Mapping[str, Any], tree_tested.Verdict] | str:
    """The pull request run that tested a push's tree, with ``tree_tested``'s answer for the
    push, or why there is none. The push's commit replaced its parent: main takes one squashed
    commit a push, and a head that holds the parent holds anything older too."""
    get = _api(text)
    sha = str(run.get("head_sha") or "")
    try:
        commit = get(f"/repos/{repo}/commits/{sha}")
        parents = commit.get("parents") if isinstance(commit, Mapping) else None
        first = parents[0] if isinstance(parents, list) and parents else None
        parent = first.get("sha") if isinstance(first, Mapping) else None
        if not isinstance(parent, str):
            return f"commit {sha[:12]} names no parent"
    except tree_tested.Unanswered as exc:
        return str(exc)
    answer = tree_tested.decide(
        get,
        event="push",
        ref=tree_tested.MAIN,
        repo=repo,
        sha=sha,
        before=parent,
        workflow=workflows.MERGE,
    )
    if not answer.tested or answer.run is None:
        return answer.reason
    try:
        other = get(f"/repos/{repo}/actions/runs/{answer.run}")
    except tree_tested.Unanswered as exc:
        return str(exc)
    if not isinstance(other, Mapping) or not isinstance(other.get("id"), int):
        return f"run {answer.run} is not a run"
    if other.get("path") != MERGE_PATH or other.get("event") != "pull_request":
        return f"run {answer.run} is not a {workflows.MERGE} pull_request run"
    if (other.get("head_repository") or {}).get("full_name") != repo:
        return f"run {answer.run}'s head is another repository's"
    if other.get("head_sha") != answer.head:
        return f"run {answer.run} is not on #{answer.pull_request}'s head"
    return other, answer


def collect(
    repo: str,
    run_id: int,
    attempt: int,
    *,
    text: Callable[..., str],
    binary: Callable[..., bytes],
) -> list[dict[str, Any]] | str:
    """The attempt's history lines, or why the run is not collected."""
    base = f"repos/{repo}/actions/runs/{run_id}"
    run = _json(text("api", base), "the run")
    why = counts(run, repo)
    if why is not None:
        return why
    workflow = str(run["path"]).rsplit("/", 1)[-1]
    # The attempt's own facts, never the run's: the run object describes its newest attempt, so
    # read for attempt 1 after a rerun it would give the rerun's end and conclusion.
    this = _json(text("api", f"{base}/attempts/{attempt}"), "the attempt")
    started, ended = this.get("run_started_at"), this.get("updated_at")
    jobs = [
        j
        for j in _listed(text, f"{base}/attempts/{attempt}/jobs", "jobs", "the jobs")
        if j.get("run_attempt", attempt) == attempt
    ]
    artifacts = _uploads(text, base, this, attempt, run_id)
    uploaded: dict[str, Mapping[str, Any]] = {}
    for artifact in sorted(artifacts, key=lambda a: int(a["id"])):
        name = str(artifact.get("name", ""))
        if name.startswith(RECORD_PREFIX) and name not in uploaded:
            uploaded[name] = artifact
    records: list[dict[str, Any]] = []
    for name, artifact in sorted(uploaded.items()):
        data = _download(binary, repo, artifact, MAX_RECORD_ARCHIVE_BYTES)
        records.append(record_line(name, data, run_id, attempt))
    tests, cpus = _test_lines(
        binary,
        repo,
        artifacts,
        run_id=run_id,
        attempt=attempt,
        workflow=workflow,
        sha=run.get("head_sha"),
    )
    tested_by = None
    if workflow == workflows.MERGE and run.get("event") == "push":
        found = _tested_run(text, repo, run)
        if isinstance(found, str):
            print(f"no pull request run's reports are read for this push: {found}")
        else:
            other, answer = found
            other_id = int(other["id"])
            other_base = f"repos/{repo}/actions/runs/{other_id}"
            try:
                first = _json(text("api", f"{other_base}/attempts/1"), "its first attempt")
                more, more_cpus = _test_lines(
                    binary,
                    repo,
                    _uploads(text, other_base, first, 1, other_id),
                    run_id=other_id,
                    attempt=1,
                    workflow=workflow,
                    sha=answer.head,
                    skip=frozenset(cpus),
                )
            except CollectError as err:
                print(
                    "::warning title=the tested pull request run's reports are not read::"
                    f"run {other_id}: {err}"
                )
            else:
                tests += more
                cpus.update(more_cpus)
                tested_by = {
                    "run_id": other_id,
                    "attempt": 1,
                    "pull_request": answer.pull_request,
                    "head_sha": answer.head,
                    "tree": answer.tree,
                }
    starts = [t for j in jobs if (t := _at(j.get("started_at"))) is not None]
    failed_ends = [
        _seconds(started, j.get("completed_at")) for j in jobs if j.get("conclusion") == "failure"
    ]
    lag = None
    if run.get("event") == "schedule" and _at(run.get("created_at")) is not None:
        created = _at(run.get("created_at"))
        assert created is not None
        lag = schedule_lag(_crons(f"{WORKFLOWS}/{workflow}"), created)
    head = {
        "kind": "run",
        "schema": SCHEMA,
        "version": VERSION,
        "run_id": run_id,
        "attempt": attempt,
        "workflow": workflow,
        "event": run.get("event"),
        "branch": run.get("head_branch"),
        "sha": run.get("head_sha"),
        "conclusion": this.get("conclusion"),
        "created_at": run.get("created_at"),
        "started_at": started,
        "updated_at": ended,
        "verdict_seconds": _seconds(started, ended),
        "queue_delay_seconds": (_seconds(started, min(starts).isoformat()) if starts else None),
        "first_failure_seconds": min((s for s in failed_ends if s is not None), default=None),
        "runner_minutes": round(sum(_job_minutes(j) for j in jobs), 2),
        "cancelled_minutes": round(
            sum(_job_minutes(j) for j in jobs if j.get("conclusion") == "cancelled"), 2
        ),
        "artifact_bytes": sum(int(a.get("size_in_bytes") or 0) for a in artifacts),
        "schedule_lag_seconds": lag,
        "cache_hits": None,
        "jobs": [_job_entry(j) for j in jobs],
        "cpus": cpus,
        "tested_by": tested_by,
    }
    return [head, *sorted(records, key=lambda r: str(r["key"])), *tests]


def history_text(lines: Sequence[Mapping[str, Any]]) -> str:
    """The history file's text, one JSON line each, every line within :data:`MAX_LINE_BYTES` with
    its line break. A line past it, which every reader would refuse the whole history for, raises
    :class:`CollectError`."""
    out: list[str] = []
    for n, line in enumerate(lines, 1):
        text = json.dumps(line, sort_keys=True) + "\n"
        size = len(text.encode("utf-8"))
        if size > MAX_LINE_BYTES:
            named = str(line.get("key") or line.get("test") or "")[:80]
            raise CollectError(
                f"history line {n}, the {line.get('kind')} line {named!r}, is {size} bytes: it"
                f" passes {MAX_LINE_BYTES} bytes, which no reader takes"
            )
        out.append(text)
    return "".join(out)


# ── scheduled slots: late, and missed ─────────────────────────────────────────────────────────
#
# A scheduled run that never starts fires no ``workflow_run``, so a run line alone can say how
# late a night was and never that it did not happen. ``slots`` runs on its own schedule and
# writes one line per slot each scheduled workflow was due in the window: the run that answered
# it, or that none did. Scheduled runs here start hours late (one workflow's last ten nights:
# 239-469 minutes), so a slot is ``missed`` only once the workflow's next slot has passed with
# no run for this one; until then it is ``pending``. A slot before the workflow existed was
# never due, and a disabled workflow's slots say ``disabled`` rather than missed.

SLOT_STATUSES = ("ran", "pending", "missed", "disabled")


def _iso(at: datetime.datetime) -> str:
    return at.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slots_between(
    crons: Sequence[str], start: datetime.datetime, end: datetime.datetime
) -> list[datetime.datetime]:
    at = start.replace(second=0, microsecond=0)
    if at < start:
        at += datetime.timedelta(minutes=1)
    found = []
    while at <= end:
        if any(_cron_matches(cron, at) for cron in crons):
            found.append(at)
        at += datetime.timedelta(minutes=1)
    return found


def _next_slot(crons: Sequence[str], after: datetime.datetime) -> datetime.datetime | None:
    at = after + datetime.timedelta(minutes=1)
    for _ in range(8 * 24 * 60):
        if any(_cron_matches(cron, at) for cron in crons):
            return at
        at += datetime.timedelta(minutes=1)
    return None


def _answering_slot(crons: Sequence[str], created: datetime.datetime) -> datetime.datetime | None:
    lag = schedule_lag(crons, created)
    if lag is None:
        return None
    return (created - datetime.timedelta(seconds=lag)).replace(second=0, microsecond=0)


def slots(
    repo: str, now: datetime.datetime, days: int, *, text: Callable[..., str]
) -> list[dict[str, Any]]:
    """One line per scheduled slot of every scheduled workflow due in the ``days`` before
    ``now``."""
    start = now - datetime.timedelta(days=days)
    lines: list[dict[str, Any]] = []
    for name in _scheduled_workflows():
        crons = _crons(f"{WORKFLOWS}/{name}")
        meta = _json(text("api", f"repos/{repo}/actions/workflows/{name}"), f"{name}")
        created = _at(meta.get("created_at"))
        disabled = meta.get("state") != "active"
        listed = _json(
            text("api", f"repos/{repo}/actions/workflows/{name}/runs?event=schedule&per_page=100"),
            f"{name}'s scheduled runs",
        )
        answered: dict[datetime.datetime, tuple[int, int]] = {}
        for run in listed.get("workflow_runs") or []:
            made = _at(run.get("created_at"))
            if made is None:
                continue
            slot = _answering_slot(crons, made)
            if slot is not None and slot not in answered:
                answered[slot] = (int(run["id"]), round((made - slot).total_seconds()))
        for due in _slots_between(crons, max(start, created) if created else start, now):
            line: dict[str, Any] = {
                "kind": "slot",
                "workflow": name,
                "due_at": _iso(due),
                "run_id": None,
                "lag_seconds": None,
                "overdue_seconds": None,
            }
            if due in answered:
                line["status"] = "ran"
                line["run_id"], line["lag_seconds"] = answered[due]
            else:
                following = _next_slot(crons, due)
                line["overdue_seconds"] = round((now - due).total_seconds())
                if disabled:
                    line["status"] = "disabled"
                elif following is not None and following <= now:
                    line["status"] = "missed"
                else:
                    line["status"] = "pending"
            lines.append(line)
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ci telemetry")
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("collect", help="write one completed run attempt's history")
    go.add_argument("--repo", required=True)
    go.add_argument("--run-id", type=int, required=True)
    go.add_argument("--attempt", type=int, required=True)
    go.add_argument("--out", type=Path, required=True, help="the folder to write the file in")
    due = sub.add_parser("slots", help="write every scheduled slot due in the window")
    due.add_argument("--repo", required=True)
    due.add_argument("--now", default=None, help="an ISO time, for a replay; default: now")
    due.add_argument("--days", type=int, default=8)
    due.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command == "slots":
        now = _at(args.now) if args.now else datetime.datetime.now(datetime.UTC)
        assert now is not None
        try:
            found = slots(args.repo, now, args.days, text=_gh_text)
        except (CollectError, subprocess.SubprocessError, OSError) as err:
            print(f"::error title=no slots written::{err}")
            return 1
        head = {"kind": "slots", "schema": SCHEMA, "version": VERSION, "at": _iso(now)}
        args.out.mkdir(parents=True, exist_ok=True)
        path = args.out / f"slots-{now.strftime('%Y-%m-%dT%H-%M')}.jsonl"
        body = "".join(json.dumps(x, sort_keys=True) + "\n" for x in [head, *found])
        path.write_text(body, "utf-8")
        for line in found:
            if line["status"] == "missed":
                print(f"::warning title=missed scheduled run::{line['workflow']} {line['due_at']}")
        print(f"wrote {path}: {len(found)} slot(s)")
        return 0
    try:
        lines = collect(args.repo, args.run_id, args.attempt, text=_gh_text, binary=_gh_binary)
        if isinstance(lines, str):
            print(f"run {args.run_id} is not collected: {lines}")
            return 0
        body = history_text(lines)
    except (CollectError, subprocess.SubprocessError, OSError) as err:
        print(f"::error title=no history written::{err}")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"history-{args.run_id}-{args.attempt}.jsonl"
    path.write_text(body, "utf-8")
    kinds = [line["kind"] for line in lines]
    print(
        f"wrote {path}: {kinds.count('test')} test case(s), {kinds.count('record')} job record(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
