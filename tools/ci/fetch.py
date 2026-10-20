"""``python -m tools.ci fetch``: the weekly report's window, fetched from the record (#3948).

The record is what the collector (``ci-telemetry.yml``) uploads: one history archive per CI or
Breadth attempt on main, and a slots archive each day. This builds the window the report reads:
one stored archive, :data:`WINDOW`, holding the window's history archives, the newest slots
archive and :data:`MANIFEST`, beside :data:`STATUS`, which says how it was built.

**Only the collector's own uploads.** Any run can upload an artifact of any name, a fork's pull
request included. The fetch lists the collector's runs on main made in the last :data:`DAYS` days
and keeps those that are the collector's own and succeeded (``telemetry.collector_run``); of each
run's artifacts it takes only the one that run uploaded, on main, in this repository, unexpired
and named as the collector names it (``telemetry.upload_refusal``), within
``telemetry.MAX_HISTORY_ARCHIVE_BYTES`` as listed and as it arrives: the checks the shard planner
makes. Every run and artifact it skips, and why, is in the status's refusals.

**Every history shown from the run it came from.** A history archive is kept only when it holds
one member, ``history-<run>-<attempt>.jsonl``, named after the archive, whose run line is that run
and attempt in the one schema and a counted run (a push to main of ``ci.yml``, or a scheduled or
dispatched Breadth run on main), and that run is one its own workflow's listing shows, in this
repository, with that attempt. A history whose run cannot be shown is refused, fetched or carried.

**Archives are data.** Nothing is unpacked to disk and nothing in an archive is run. A downloaded
archive is checked in memory and copied whole into the window, which is written member by member.
A member that is absolute, holds a parent path or a folder, or is a link is refused
(``telemetry.member_refusal``), and so is one past its size; a window past
``telemetry.MAX_WINDOW_MEMBERS`` members or ``telemetry.MAX_WINDOW_BYTES``, or holding one name
twice, is refused whole, and the window being built stops at both caps and never takes a name it
holds.

**The carried window.** 90 days are about 860 histories, and fetching each costs two calls of the
job token's, which GitHub allows 1,000 an hour for the whole repository. So the weekly workflow
carries the window it built to its next run as one artifact (:data:`CARRY`). The next run reads
the uploading run through the API, by the id the artifact names, and takes the window only when
that run is this workflow's own scheduled or dispatched run on main; anything else is ignored, and
the window refills from the record within the budget, the status saying why. Every carried
history is then checked again as a fetched one is: its collector run, which :data:`MANIFEST`
names, must still be listed as the collector's own, and its own run must be shown. One that fails,
or whose manifest entry is not in its shape, is dropped and, when its collector run is listed,
fetched again. Only the collector runs the carried window does not hold are fetched: about 70 a
week.

**A budget.** Before each history the fetch reads how many calls the token has left
(``rate_limit``, which costs none) and stops at :data:`RESERVE`, so the other workflows keep
theirs; a count it cannot read is a spent budget. Newest first, so what it leaves is the oldest;
the report says how much of the 90 days the window holds, and the next run fetches the rest.

Standard library only, like every file in ``tools/ci``.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from . import report, telemetry

#: The artifact the weekly workflow carries its window in, and the workflow itself.
CARRY = "flake-report-window"
FLAKE_REPORT_PATH = f"{telemetry.WORKFLOWS}/flake-report.yml"
CARRY_EVENTS = frozenset({"schedule", "workflow_dispatch"})
#: The window: one stored archive of collector archives, and the file naming where each came from.
WINDOW = "window.zip"
MANIFEST = "fetched.json"
#: Beside the window: how it was built, for the report to say.
STATUS = "fetch-status.json"
#: Days fetched: the report's 90, and one more, since a history's run began before its upload.
DAYS = report.WINDOW_DAYS + 1
#: Calls the fetch leaves the token, for every other workflow of the repository.
RESERVE = 200
#: Pages of 100 runs listed at most: about 950 collector runs, and 860 CI runs, in 90 days.
RUN_PAGES = 30
#: A history is fetched in two calls, its run's artifact listing and its download.
CALLS_PER_HISTORY = 2
#: The names a collector archive goes by.
HISTORY_ARCHIVE = re.compile(r"telemetry-history-(\d+)-(\d+)")
SLOTS_ARCHIVE = re.compile(r"telemetry-slots-\d+")
ARCHIVE = re.compile(r"telemetry-(?:history-\d+-\d+|slots-\d+)\.zip")
#: The manifest at most.
MAX_MANIFEST_BYTES = 1024 * 1024
#: How long one ``gh`` call may take.
GH_TIMEOUT_SECONDS = 300
#: The workflows a history may come from: their paths and their counted events.
SOURCES = {
    name: (f"{telemetry.WORKFLOWS}/{name}", events) for name, events in telemetry.COUNTED.items()
}

Text = Callable[..., str]
Download = Callable[[str, Path, int], None]


class FetchError(Exception):
    """The window cannot be fetched whole enough to report on."""


@dataclass
class Fetched:
    carried: int = 0
    dropped: int = 0
    fetched: int = 0
    refused: int = 0
    left: int = 0
    carry: str = "none: no window was carried"
    slots: str | None = None
    refusals: list[str] = field(default_factory=list)


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


def _gh_download(path: str, dest: Path, limit: int) -> None:
    """``gh api <path>`` into ``dest``, stopped at ``limit`` bytes."""
    with dest.open("wb") as out:
        process = subprocess.Popen(["gh", "api", path], stdout=subprocess.PIPE)
        assert process.stdout is not None
        written = 0
        try:
            while chunk := process.stdout.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    process.kill()
                    raise FetchError(f"{path} passes {limit} bytes")
                out.write(chunk)
        finally:
            process.stdout.close()
            code = process.wait(timeout=GH_TIMEOUT_SECONDS)
    if code != 0:
        raise FetchError(f"gh api {path} exited {code}")


def _json(text: str, what: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise FetchError(f"{what}: not JSON ({exc})") from None


def _when(value: object) -> datetime.datetime | None:
    """A time as the API and the manifest write one, with its zone, or None."""
    if not isinstance(value, str):
        return None
    try:
        at = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo is not None else None


def _remaining(text: Text) -> int | None:
    """The calls the token has left this hour, or None when GitHub does not say."""
    try:
        doc = _json(text("api", "rate_limit"), "the rate limit")
        value = doc["resources"]["core"]["remaining"]
    except (FetchError, KeyError, TypeError):
        return None
    return value if isinstance(value, int) else None


def _runs(text: Text, base: str, what: str) -> list[dict[str, Any]]:
    """Every run of a listing, every page against the listing's own count."""
    runs: list[Any] = []
    total = 0
    for page in range(1, RUN_PAGES + 1):
        doc = _json(text("api", f"{base}&per_page=100&page={page}"), what)
        if not isinstance(doc, dict) or not isinstance(doc.get("total_count"), int):
            raise FetchError(f"{what}: the listing has no total_count")
        total = doc["total_count"]
        batch = doc.get("workflow_runs")
        if not isinstance(batch, list):
            raise FetchError(f"{what}: the listing's runs are not a list")
        runs += batch
        if len(runs) >= total or not batch:
            break
    if len(runs) < total:
        raise FetchError(f"{what}: {len(runs)} of {total} listed")
    return [r for r in runs if isinstance(r, dict) and isinstance(r.get("id"), int)]


def _created(since: datetime.datetime) -> str:
    return urllib.parse.quote(f">={since:%Y-%m-%d}")


def collector_runs(
    text: Text, repo: str, since: datetime.datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    """The collector's own successful runs on main created since ``since``'s day, newest first,
    and why each other run the listing holds is not read. The listing is asked for that day on,
    and each run is checked against it here too."""
    base = (
        f"repos/{repo}/actions/workflows/ci-telemetry.yml/runs"
        f"?branch=main&status=success&created={_created(since)}"
    )
    events = telemetry.HISTORY_EVENTS | telemetry.SLOTS_EVENTS
    own: list[dict[str, Any]] = []
    refusals: list[str] = []
    for r in _runs(text, base, "the collector's runs"):
        made = _when(r.get("created_at"))
        head = r.get("head_repository")
        where = head.get("full_name") if isinstance(head, Mapping) else None
        if not telemetry.collector_run(r, repo, events):
            refusals.append(
                f"collector run {r['id']} ({r.get('path')}, {r.get('event')}, "
                f"{r.get('head_branch')}, {where}) is not the collector's own run on main"
            )
        elif r.get("conclusion") != "success":
            refusals.append(f"collector run {r['id']} concluded {r.get('conclusion')!r}")
        elif made is None or made.date() < since.date():
            refusals.append(
                f"collector run {r['id']} was not made in the window ({r.get('created_at')!r})"
            )
        else:
            own.append(r)
    own.sort(key=lambda r: (str(r["created_at"]), r["id"]), reverse=True)
    return own, refusals


def source_runs(text: Text, repo: str, since: datetime.datetime) -> dict[int, int]:
    """Each counted run a history may come from, by id, with its newest attempt: each counted
    workflow's own runs on main, of its counted events, in this repository."""
    shown: dict[int, int] = {}
    for name, (path, events) in SOURCES.items():
        base = f"repos/{repo}/actions/workflows/{name}/runs?branch=main&created={_created(since)}"
        for run in _runs(text, base, f"{name}'s runs"):
            attempt = run.get("run_attempt")
            if telemetry.own_run(run, repo, path, events) and isinstance(attempt, int):
                shown[run["id"]] = attempt
    return shown


def _upload_refusal(
    artifact: object, run: Mapping[str, Any], name: re.Pattern[str], limit: int
) -> str | None:
    """Why an artifact ``run``'s listing holds is not the archive it uploaded, or None."""
    if why := telemetry.upload_refusal(artifact):
        return why
    assert isinstance(artifact, Mapping)
    uploader = artifact["workflow_run"]["id"]
    if uploader != run["id"]:
        return f"was uploaded by run {uploader}, not by this run"
    if not name.fullmatch(str(artifact.get("name") or "")):
        return "is not named as the collector names its archives"
    size = artifact.get("size_in_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= limit:
        return f"is listed at {size!r} bytes, not 1 to {limit}"
    return None


def _uploaded(
    text: Text,
    repo: str,
    run: Mapping[str, Any],
    name: re.Pattern[str],
    limit: int,
    refusals: list[str],
) -> dict[str, Any] | None:
    """The one archive ``run`` uploaded under a name ``name`` matches, within ``limit`` as it is
    listed, or None; why each other artifact it lists is skipped goes to ``refusals``."""
    listed = _json(
        text("api", f"repos/{repo}/actions/runs/{run['id']}/artifacts?per_page=100"),
        f"run {run['id']}'s artifacts",
    )
    artifacts = listed.get("artifacts") if isinstance(listed, dict) else None
    if not isinstance(artifacts, list) or not artifacts:
        refusals.append(f"collector run {run['id']} lists no artifact")
        return None
    for artifact in artifacts:
        why = _upload_refusal(artifact, run, name, limit)
        if why is None:
            return dict(artifact)
        ident, called = (
            (artifact.get("id"), artifact.get("name"))
            if isinstance(artifact, Mapping)
            else (None, None)
        )
        refusals.append(f"collector run {run['id']}: artifact {ident} {str(called)!r} {why}")
    return None


def history_refusal(archive: IO[bytes], name: str, shown: Mapping[int, int]) -> str | None:
    """Why a history archive named ``name`` (``telemetry-history-<run>-<attempt>.zip``) is not
    kept, or None: read in memory, its one member's run line against the run it names."""
    match = HISTORY_ARCHIVE.fullmatch(name.removesuffix(".zip"))
    if not match:
        return f"{name} is not named as a history"
    run, attempt = int(match.group(1)), int(match.group(2))
    try:
        with zipfile.ZipFile(archive) as zipped:
            infos = zipped.infolist()
            member = f"history-{run}-{attempt}.jsonl"
            if [i.filename for i in infos] != [member]:
                return f"{name} holds {[i.filename for i in infos][:3]}, not {member} alone"
            if why := telemetry.member_refusal(infos[0]):
                return f"{name}: {why}"
            if infos[0].file_size > report.MAX_FILE_BYTES:
                return f"{name}: {member} passes {report.MAX_FILE_BYTES} bytes"
            with zipped.open(infos[0]) as handle:
                head = json.loads(handle.readline(report.MAX_LINE_BYTES + 1))
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        return f"{name} cannot be read: {exc}"
    if (
        not isinstance(head, dict)
        or head.get("kind") != "run"
        or head.get("schema") != telemetry.SCHEMA
        or head.get("version") not in telemetry.READS
    ):
        return f"{name}: its first line is not a {telemetry.SCHEMA} run line"
    if head.get("run_id") != run or head.get("attempt") != attempt:
        return f"{name} describes run {head.get('run_id')} attempt {head.get('attempt')}"
    workflow, event, branch = head.get("workflow"), head.get("event"), head.get("branch")
    if event not in telemetry.COUNTED.get(str(workflow), ()) or branch != "main":
        return f"{name}: run {run} ({workflow}, {event}, {branch}) is not a counted run"
    if run not in shown or attempt > shown[run]:
        return f"{name}: run {run} attempt {attempt} is not a run its workflow's listing shows"
    return None


def _slots_refusal(archive: IO[bytes], name: str) -> str | None:
    try:
        with zipfile.ZipFile(archive) as zipped:
            infos = zipped.infolist()
            if len(infos) != 1 or not report.SLOTS_NAME.fullmatch(infos[0].filename):
                return f"{name} does not hold one slots file"
            if why := telemetry.member_refusal(infos[0]):
                return f"{name}: {why}"
    except (zipfile.BadZipFile, OSError) as exc:
        return f"{name} cannot be read: {exc}"
    return None


class Window:
    """The window being built: one stored archive, written member by member, within its caps,
    each name once (a member is opened by its name)."""

    def __init__(self, path: Path) -> None:
        self.zipped = zipfile.ZipFile(path, "w", zipfile.ZIP_STORED)
        self.bytes = 0
        self.names: set[str] = set()

    def room(self, name: str, size: int) -> str | None:
        """Why one more member ``name`` of ``size`` does not fit, or None; the manifest keeps a
        place, and its bytes."""
        if name in self.names or name == MANIFEST:
            return "the window holds it already"
        if len(self.names) + 2 > telemetry.MAX_WINDOW_MEMBERS:
            return f"the window holds {telemetry.MAX_WINDOW_MEMBERS} members"
        if self.bytes + size + MAX_MANIFEST_BYTES > telemetry.MAX_WINDOW_BYTES:
            return f"the window passes {telemetry.MAX_WINDOW_BYTES} bytes"
        return None

    def add(self, name: str, source: IO[bytes], size: int) -> None:
        with self.zipped.open(zipfile.ZipInfo(name), "w") as dest:
            while chunk := source.read(1024 * 1024):
                dest.write(chunk)
        self.bytes += size
        self.names.add(name)

    def close(self, manifest: Mapping[str, Any] | None = None) -> None:
        if manifest is not None:
            self.zipped.writestr(MANIFEST, json.dumps(manifest, indent=1, sort_keys=True))
        self.zipped.close()


def _carried_artifact(
    text: Text, repo: str, download: Download, scratch: Path, got: Fetched
) -> Path | None:
    """The artifact this workflow's own last run carried, downloaded, or None (and why, in
    ``got.carry``). The uploading run is read through the API by the id the artifact names, and
    only this workflow's own scheduled or dispatched run on main is trusted, never a name."""
    listed = _json(
        text("api", f"repos/{repo}/actions/artifacts?name={CARRY}&per_page=100"),
        "the carried window",
    )
    candidates = [
        a
        for a in (listed.get("artifacts") or [] if isinstance(listed, dict) else [])
        if isinstance(a, Mapping) and a.get("name") == CARRY and telemetry.upload_refusal(a) is None
    ]
    if not candidates:
        return None
    newest = max(candidates, key=lambda a: (str(a.get("created_at") or ""), int(a["id"])))
    uploader = newest["workflow_run"]["id"]
    run = _json(text("api", f"repos/{repo}/actions/runs/{uploader}"), "its run")
    if not telemetry.own_run(run, repo, FLAKE_REPORT_PATH, CARRY_EVENTS):
        got.carry = (
            f"refused: artifact {newest['id']} was uploaded by run {uploader}, which is not this "
            "workflow's own scheduled or dispatched run on main"
        )
        return None
    path = scratch / "carried.zip"
    try:
        limit = telemetry.MAX_WINDOW_BYTES + MAX_MANIFEST_BYTES
        download(f"repos/{repo}/actions/artifacts/{newest['id']}/zip", path, limit)
    except FetchError as err:
        got.carry = f"refused: {err}"
        return None
    got.carry = f"kept: the window weekly run {uploader} carried"
    return path


@contextlib.contextmanager
def _carried_window(path: Path) -> Generator[zipfile.ZipFile, None, None]:
    """The carried window, opened in place: upload-artifact's archive holds the one window
    archive, which holds the collector archives. Refused whole (ValueError) when it is not shaped
    so or passes either cap."""
    with contextlib.ExitStack() as stack:
        outer = stack.enter_context(zipfile.ZipFile(path))
        infos = outer.infolist()
        if [i.filename for i in infos] != [WINDOW] or telemetry.member_refusal(infos[0]):
            names = [i.filename for i in infos][:3]
            raise ValueError(f"the artifact holds {names}, not {WINDOW} alone")
        window = stack.enter_context(zipfile.ZipFile(stack.enter_context(outer.open(infos[0]))))
        members = window.infolist()
        if len(members) > telemetry.MAX_WINDOW_MEMBERS:
            raise ValueError(
                f"it holds {len(members)} members, past {telemetry.MAX_WINDOW_MEMBERS} members"
            )
        total = sum(i.file_size for i in members)
        if total > telemetry.MAX_WINDOW_BYTES:
            raise ValueError(f"it holds {total} bytes, past {telemetry.MAX_WINDOW_BYTES}")
        seen: set[str] = set()
        for name in (i.filename for i in members):
            if name in seen:
                raise ValueError(f"it holds {name!r} twice")
            seen.add(name)
        yield window


def _keep_carried(
    old: zipfile.ZipFile,
    listed: Mapping[str, Mapping[str, Any]],
    shown: Mapping[int, int],
    since: datetime.datetime,
    window: Window,
    manifest: dict[str, dict[str, str]],
    got: Fetched,
) -> None:
    """Copy into the window each carried history that passes every check a fetched one does."""
    limit = telemetry.MAX_HISTORY_ARCHIVE_BYTES
    infos = {i.filename: i for i in old.infolist()}
    for member in infos.values():
        if refused := telemetry.member_refusal(member):
            got.refusals.append(f"carried: {refused}")
        elif member.filename != MANIFEST and not ARCHIVE.fullmatch(member.filename):
            got.refusals.append(f"carried: {member.filename!r} is not a collector archive")
    entries: Any = {}
    manifest_info = infos.get(MANIFEST)
    if manifest_info is not None and manifest_info.file_size <= MAX_MANIFEST_BYTES:
        entries = json.loads(old.read(manifest_info))
    if not isinstance(entries, dict):
        raise ValueError("its manifest is not a table")
    for run_id, entry in sorted(entries.items()):
        archive = str(entry.get("archive")) if isinstance(entry, dict) else ""
        info = infos.get(archive)
        why: str | None
        made = _when(entry.get("created_at")) if isinstance(entry, dict) else None
        if not str(run_id).isdigit() or info is None or not ARCHIVE.fullmatch(archive):
            why = f"manifest entry {run_id!r} names no archive the window holds"
        elif refusal := telemetry.member_refusal(info):
            why = refusal
        elif info.file_size > limit:
            why = f"{archive} passes {limit} bytes"
        elif made is None:
            why = f"{archive}: its manifest entry has no time: {entry.get('created_at')!r}"
        elif made < since:
            why = f"{archive} left the window"
        elif run_id not in listed:
            why = f"{archive}: collector run {run_id} is no longer the collector's own, listed"
        elif room := window.room(archive, info.file_size):
            why = f"{archive}: {room}"
        else:
            with old.open(info) as handle:
                data = handle.read(limit + 1)
            why = history_refusal(io.BytesIO(data), archive, shown)
            if why is None:
                window.add(archive, io.BytesIO(data), len(data))
                manifest[run_id] = {"archive": archive, "created_at": str(entry["created_at"])}
                got.carried += 1
                continue
        got.dropped += 1
        got.refusals.append(f"carried: {why}")


def _kept(
    repo: str,
    artifact: Mapping[str, Any],
    download: Download,
    scratch: Path,
    window: Window,
    got: Fetched,
    limit: int,
    refusal: Callable[[IO[bytes], str], str | None],
) -> str | None:
    """Download one archive, check it in memory, and copy it into the window; its name there,
    or None when it was refused."""
    name = f"{artifact['name']}.zip"
    if why := window.room(name, 0):
        got.refusals.append(f"{name}: {why}")
        return None
    partial = scratch / "download.zip"
    try:
        download(f"repos/{repo}/actions/artifacts/{artifact['id']}/zip", partial, limit)
        size = partial.stat().st_size
        if room := window.room(name, size):
            why = f"{name}: {room}"
        else:
            with partial.open("rb") as handle:
                why = refusal(handle, name)
        if why is None:
            with partial.open("rb") as handle:
                window.add(name, handle, size)
            return name
        got.refusals.append(why)
    except FetchError as err:
        got.refusals.append(f"{name}: {err}")
    finally:
        partial.unlink(missing_ok=True)
    return None


def fetch(
    repo: str,
    out: Path,
    *,
    days: int = DAYS,
    reserve: int = RESERVE,
    carry: bool = True,
    now: datetime.datetime | None = None,
    text: Text = _gh_text,
    download: Download = _gh_download,
    archive_limit: int | None = None,
) -> Fetched:
    out.mkdir(parents=True, exist_ok=True)
    limit = archive_limit or telemetry.MAX_HISTORY_ARCHIVE_BYTES
    now = now or datetime.datetime.now(datetime.UTC)
    since = now - datetime.timedelta(days=days)
    got = Fetched()
    runs, got.refusals = collector_runs(text, repo, since)
    shown = source_runs(text, repo, since - datetime.timedelta(days=2))
    listed = {str(r["id"]): r for r in runs if r.get("event") in telemetry.HISTORY_EVENTS}
    manifest: dict[str, dict[str, str]] = {}
    part = out / f"{WINDOW}.part"
    window = Window(part)
    with tempfile.TemporaryDirectory() as scratch:
        carried = _carried_artifact(text, repo, download, Path(scratch), got) if carry else None
        if carried is not None:
            try:
                with _carried_window(carried) as old:
                    _keep_carried(old, listed, shown, since, window, manifest, got)
            except (ValueError, zipfile.BadZipFile, OSError, EOFError) as err:
                got.carry = f"refused: the carried window cannot be read: {err}"
                got.carried = got.dropped = 0
                window.close()
                window, manifest = Window(part), {}

        def slots_refusal(handle: IO[bytes], name: str) -> str | None:
            return _slots_refusal(handle, name)

        def history(handle: IO[bytes], name: str) -> str | None:
            return history_refusal(handle, name, shown)

        for run in runs:
            if run.get("event") in telemetry.SLOTS_EVENTS:
                artifact = _uploaded(text, repo, run, SLOTS_ARCHIVE, limit, got.refusals)
                if artifact is not None:
                    name = _kept(
                        repo, artifact, download, Path(scratch), window, got, limit, slots_refusal
                    )
                    if name is not None:
                        got.slots = name
                        break
        left = _remaining(text)
        for run in runs:
            if run.get("event") not in telemetry.HISTORY_EVENTS or str(run["id"]) in manifest:
                continue
            # A count the fetch cannot read is a spent budget: the rest is left for the next run.
            if left is None or left - CALLS_PER_HISTORY < reserve:
                got.left += 1
                continue
            artifact = _uploaded(text, repo, run, HISTORY_ARCHIVE, limit, got.refusals)
            name = (
                None
                if artifact is None
                else _kept(repo, artifact, download, Path(scratch), window, got, limit, history)
            )
            if name is None:
                got.refused += 1
            else:
                manifest[str(run["id"])] = {"archive": name, "created_at": str(run["created_at"])}
                got.fetched += 1
            left = _remaining(text)
        if left is None and got.left:
            got.refusals.insert(
                0,
                "the rate limit does not say how many calls the token has left, so the "
                "histories not yet fetched are left for the next run",
            )
    window.close(manifest)
    part.replace(out / WINDOW)
    status = {
        "carry": got.carry,
        "carried": got.carried,
        "dropped": got.dropped,
        "fetched": got.fetched,
        "refused": got.refused,
        "left": got.left,
        "reserve": reserve,
        "slots": got.slots,
        "refusals": got.refusals,
    }
    (out / STATUS).write_text(json.dumps(status, indent=1, sort_keys=True), encoding="utf-8")
    return got


def _summary(text: str) -> None:
    print(text)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ci fetch")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", type=Path, required=True, help="the window's folder")
    parser.add_argument("--days", type=int, default=DAYS)
    parser.add_argument("--reserve", type=int, default=RESERVE)
    parser.add_argument("--no-carry", action="store_true", help="fetch the whole window afresh")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        got = fetch(
            args.repo, args.out, days=args.days, reserve=args.reserve, carry=not args.no_carry
        )
    except (FetchError, subprocess.SubprocessError, OSError) as err:
        print(f"::error title=the window was not fetched::{err}")
        return 2
    _summary(
        f"The window: {got.carried} histories carried ({got.carry}), {got.dropped} dropped on "
        f"recheck, {got.fetched} fetched, {got.refused} refused, {got.left} left for the next "
        f"run, and the newest slots ({got.slots or 'none'})."
    )
    for why in got.refusals:
        print(f"::warning title=refused::{why}")
    if got.left:
        print(
            f"::warning title=the window is short of {got.left} histories::the fetch keeps "
            f"{args.reserve} of the token's calls for the other workflows; the next run fetches "
            "the rest"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
