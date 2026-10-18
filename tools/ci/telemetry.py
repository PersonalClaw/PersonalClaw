"""``python -m tools.ci telemetry``: the one writer of CI telemetry (#3948).

Every record of CI history, per run and per test, is written here, in one versioned schema, and
nowhere else. ``collect`` runs in ``.github/workflows/ci-telemetry.yml`` after a CI or Breadth
run completes, from main's own code, and writes ``history-<run>-<attempt>.jsonl``:

- the first line is the run's: the schema and its version, then the facts only the Actions API
  knows (queue delay, setup, time to the first failed job, verdict time, runner-minutes,
  cancelled work, artifact volume, schedule lag) and one entry per job;
- every other line is one test case of that attempt's junit reports: its id, its first result,
  its seconds, the report it came from.

**Which runs count.** A push to ``main`` of ``ci.yml``, and a scheduled or dispatched Breadth
run on ``main``, both from this repository. That is decided from the run itself, before any
artifact is listed or downloaded, so a pull request's artifacts are never read at all.

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
seconds apart.

**Artifacts are data.** Their content is never executed or extracted to disk: an archive larger
than ``MAX_ARCHIVE_BYTES`` is never downloaded, a report is read in memory up to
``MAX_JUNIT_BYTES``, a report that is not UTF-8 is refused, and so is one that declares a
DOCTYPE anywhere. A report that cannot be read fails the collection and writes nothing, since a
history with fewer tests would read as a run that ran fewer.

Rerun and quarantine facts (``rerun_result``, ``quarantined``) are #3947's ``run-job``'s, and
cache hits (``cache_hits``) are #3909's; no API reports them, so until those land they are null,
never guessed. Standard library only, like every file in ``tools/ci``.
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

SCHEMA = "personalclaw-ci-telemetry"
VERSION = 1

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
)

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
    "quarantined",
    "platform",
    "workers",
    "worker",
    "position",
)

#: The junit properties ``tests/run_facts.py`` records on each process's first test case.
PLATFORM_PROPERTY = "ci_platform"
WORKERS_PROPERTY = "ci_workers"

#: Where the workflows live, repository-relative: the folder the Actions API's ``path`` names.
WORKFLOWS = ".github/workflows"
#: The two workflows whose runs are collected, by the ``path`` the API reports for a run.
CI_PATH = f"{WORKFLOWS}/ci.yml"
BREADTH_PATH = f"{WORKFLOWS}/breadth.yml"

#: The workflows whose runs are collected, and the events that count for each.
COUNTED = {
    "ci.yml": frozenset({"push"}),
    "breadth.yml": frozenset({"schedule", "workflow_dispatch"}),
}

REPORT_PREFIX = "junit-"
#: The artifact ``ci-telemetry.yml`` uploads each attempt's history as; the shard planner reads it.
HISTORY_ARTIFACT = "telemetry-history-{run}-{attempt}"
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_JUNIT_BYTES = 8 * 1024 * 1024
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


Case = tuple[str, float, str, str | None]
"""One test case: its id, its seconds, its first result, and the runner host it ran on."""


def report_cases(name: str, archive: bytes) -> tuple[list[Case], str | None, int | None]:
    """Each test case of one junit archive, and the platform and worker count the suite recorded
    in it (``tests/run_facts.py``), or None for a report that records none."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            members = [
                m for m in zipped.infolist() if not m.is_dir() and m.filename.endswith(".xml")
            ]
            if not members:
                raise CollectError(f"{name}: the archive holds no junit report")
            cases: list[Case] = []
            facts: set[tuple[str | None, str | None]] = set()
            budget = MAX_JUNIT_BYTES
            for member in members:
                with zipped.open(member) as fh:
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
                        cases.append((test, float(case.get("time") or 0.0), _result(case), host))
                        found = {p.get("name"): p.get("value") for p in case.iter("property")}
                        if PLATFORM_PROPERTY in found or WORKERS_PROPERTY in found:
                            facts.add((found.get(PLATFORM_PROPERTY), found.get(WORKERS_PROPERTY)))
    except zipfile.BadZipFile:
        raise CollectError(f"{name}: not a zip archive") from None
    except (ET.ParseError, ValueError) as exc:
        raise CollectError(f"{name}: not a junit report ({exc})") from None
    if len(facts) > 1:
        raise CollectError(f"{name}: its processes recorded more than one platform: {facts}")
    platform, workers = next(iter(facts), (None, None))
    if workers is not None and not workers.isdigit():
        raise CollectError(f"{name}: a worker count of {workers!r}")
    return cases, platform, int(workers) if workers is not None else None


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
    # Only this attempt's uploads: an artifact made before the attempt started is an earlier
    # attempt's, and one made after it ended is a later attempt's.
    since, until = _at(started), _at(ended)
    if since is None or until is None:
        raise CollectError(f"attempt {attempt} of run {run_id} has no start or no end")
    artifacts = []
    for a in _listed(text, f"{base}/artifacts", "artifacts", "the artifacts"):
        made = _at(a.get("created_at"))
        if a.get("expired") or made is None:
            continue
        if since <= made <= until:
            artifacts.append(a)
    reports: dict[str, Mapping[str, Any]] = {}
    for artifact in sorted(artifacts, key=lambda a: int(a["id"])):
        name = str(artifact.get("name", ""))
        if name.startswith(REPORT_PREFIX) and name not in reports:
            reports[name] = artifact
    tests: list[dict[str, Any]] = []
    for name, artifact in sorted(reports.items()):
        if int(artifact.get("size_in_bytes") or 0) > MAX_ARCHIVE_BYTES:
            raise CollectError(f"{name}: the archive passes {MAX_ARCHIVE_BYTES} bytes")
        data = binary("api", f"repos/{repo}/actions/artifacts/{artifact['id']}/zip")
        if len(data) > MAX_ARCHIVE_BYTES:
            raise CollectError(f"{name}: the archive passes {MAX_ARCHIVE_BYTES} bytes")
        cases, platform, workers = report_cases(name, data)
        for test, seconds, result, host in cases:
            tests.append(
                {
                    "kind": "test",
                    "run_id": run_id,
                    "attempt": attempt,
                    "workflow": workflow,
                    "sha": run.get("head_sha"),
                    "report": name,
                    "runner": host,
                    "test": test,
                    "seconds": seconds,
                    "first_result": result,
                    "rerun_result": None,
                    "quarantined": None,
                    "platform": platform,
                    "workers": workers,
                    "worker": None,
                    "position": None,
                }
            )
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
    }
    return [head, *tests]


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
    except (CollectError, subprocess.SubprocessError, OSError) as err:
        print(f"::error title=no history written::{err}")
        return 1
    if isinstance(lines, str):
        print(f"run {args.run_id} is not collected: {lines}")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"history-{args.run_id}-{args.attempt}.jsonl"
    path.write_text("".join(json.dumps(line, sort_keys=True) + "\n" for line in lines), "utf-8")
    print(f"wrote {path}: {len(lines) - 1} test case(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
