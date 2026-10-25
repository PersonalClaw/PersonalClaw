"""``python -m tools.ci rails select --paths ...``: the rails a change can break (#3920).

Each rails leg of a push to main or a pull request's run records what each rail read
(``tests/rails_reads.py``) and writes it beside its JUnit report as ``rails-reads-<leg>.json``.
merge.yml uploads each entry's reports as the artifact ``reports-<slug>-<attempt>`` (the rails
leg's slug is ``rails-<leg>-<legs>``), the record at ``reports/rails-reads-<leg>.json`` in it.
This command reads the newest record of a main commit and names every rail whose recorded reads
touch a changed path, one per line, so a lane runs those rails and its own tests rather than all
of ``tests/rails``.

**Only main's records.** With no ``--map``, it walks main's first-parent commits of the last
day, newest first, and reads the first that has a record. Since the flip a tested push runs no
rails, so a commit's record is most often its pull request's. For each commit:

1. a push run of main on that commit (:data:`WORKFLOWS`) that holds a record, the newest first.
   Each leg's member must be recorded on the run's own commit;
2. else the pull request merged into main as that commit (its ``merge_commit_sha``), from this
   repository, and its newest :data:`PR_WORKFLOW` run made in the last day that holds a record.
   The run must be that pull request's: of its branch, made after it was opened and before it
   merged. The run's own head must have the commit's tree, never the pull request's head now,
   which may have moved since the run. Each leg's member must be recorded on a commit with the
   commit's tree: the merge the run tested, which was the commit's tree only when the head held
   the base it merged onto. The record then counts as the main commit's, whose tree a clone
   holds.

Of a run's artifacts it takes only those named for a leg (each leg's newest attempt), uploaded
by that run, unexpired and within :data:`MAX_ARTIFACT_BYTES`. Of each artifact it takes only the
one member named for its leg, within :data:`MAX_MEMBER_BYTES`. An artifact of that name from any
other run is never read. ``--map`` reads files instead, a leg's record or several.

**What selects a rail.** For each changed path:

- a rail that read the path: opened it, stat'd it (a check that it exists counts, whether or not
  it was there), asked the source index for it, or walked a source index root that holds it;
- when the path was added or removed since the record's commit (it is not in that commit's tree,
  or it is not in this checkout), every rail that listed its folder or walked a root holding it:
  a rail sees a new path only by listing its folder or asking for it by name, and the second is
  the read above. A path in a folder the commit did not hold counts against the nearest folder
  it did hold. When the commit is not in this clone, every path counts as added or removed, and
  its folder is the nearest one some rail reads;
- a path the harness reads selects every rail. The harness is the run's own setup, which every
  rail runs under: conftest, the plugin and guard modules it loads from ``tests/``, the files they
  read, ``pyproject.toml`` and ``uv.lock``. A package module the harness imports (conftest's
  registry fences import some two hundred) is no part of it, and selects only the rails that read
  it. An import-time break in such a module fails the change's own tests, which load the same
  harness, so nothing slips.

**What is always selected:**

- ``tests/rails/test_ci_contract.py`` (:data:`ALWAYS`);
- a rail that started a child process: nothing a child reads is recorded, so it may read the
  whole tree;
- a rail the record does not hold: a new one, one in a leg with no record, or one whose run
  failed (``unrecorded``, since it read only part of what it reads);
- every rail when no commit of the last day has a record, or the record is older than
  :data:`MAX_AGE`.

When it cannot tell, it selects more, never less. Standard library only, like every file in
``tools/ci``.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import posixpath
import re
import subprocess
import sys
import urllib.parse
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import rails_census, workflows

#: The record's schema (``tests/rails_reads.py``'s ``SCHEMA``).
SCHEMA = 1

#: The rails every selection holds, whatever changed.
ALWAYS = ("tests/rails/test_ci_contract.py",)

#: How old a record may be before it names nothing and every rail runs.
MAX_AGE = datetime.timedelta(days=1)

#: The workflows whose push runs on main run the rails legs, in the order they are read: the
#: registry's own (``tools/ci/workflows.py``), so the flip strands no reader.
WORKFLOWS = workflows.MAIN_PUSH

#: The workflow whose pull request runs are read for a main commit no push run recorded: the
#: merge tier's, which runs the rails legs on the tree the merge lands.
PR_WORKFLOW = "merge.yml"

#: The most of main's commits one walk reads, newest first.
MAX_COMMITS = 100

#: The rails legs (``rails_census.LEGS``), each with its artifact, less its attempt, and the
#: member it holds: at ``reports/`` from the artifact's root, or at the root when the upload's
#: one folder is ``reports/`` itself.
LEGS = ("1", "2", "3")
ARTIFACT = "reports-rails-{leg}-" + str(len(LEGS)) + "-"
MEMBERS = ("reports/rails-reads-{leg}.json", "rails-reads-{leg}.json")

#: The most a record's artifact may weigh as listed, and its member once read.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024

#: How long one ``gh`` call may take.
GH_TIMEOUT_S = 120

_KINDS = ("files", "folders", "walks", "spawns")
_SHA = re.compile(r"[0-9a-f]{40}")


class RecordError(ValueError):
    """A record that cannot be read as one."""


@dataclass(frozen=True)
class Reads:
    """What one rail (or the run's setup) read."""

    files: frozenset[str] = frozenset()
    folders: frozenset[str] = frozenset()
    #: Source index walks, each a folder and the suffix it keeps ("" keeps every file).
    walks: tuple[tuple[str, str], ...] = ()
    spawns: tuple[str, ...] = ()

    def holds(self, path: str) -> bool:
        """Whether this read *path*: opened, stat'd, asked of the index, or walked."""
        return path in self.files or any(
            path.startswith(folder + "/") and path.endswith(suffix) for folder, suffix in self.walks
        )

    def lists(self, folder: str) -> bool:
        """Whether this listed *folder*, or walked a root that holds it."""
        return folder in self.folders or any(
            folder == root or folder.startswith(root + "/") for root, _suffix in self.walks
        )


@dataclass
class Record:
    """One commit's reads: every leg's slice of it, merged."""

    commit: str
    recorded_at: datetime.datetime
    legs: tuple[str, ...]
    harness: Reads
    rails: dict[str, Reads]
    unrecorded: dict[str, str]
    source: str = ""


def _strings(value: object, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RecordError(f"{what} is not a list of strings")
    return value


def _reads(value: object, what: str) -> Reads:
    if not isinstance(value, Mapping) or set(value) != set(_KINDS):
        raise RecordError(f"{what} does not hold exactly {', '.join(_KINDS)}")
    walks = value["walks"]
    if not isinstance(walks, list) or not all(
        isinstance(w, list) and len(w) == 2 and all(isinstance(p, str) for p in w) for w in walks
    ):
        raise RecordError(f"{what}'s walks are not pairs of a folder and a suffix")
    if any(not folder for folder, _suffix in walks):
        raise RecordError(f"{what} walks the whole repository, which no source index root is")
    return Reads(
        files=frozenset(_strings(value["files"], f"{what}'s files")),
        folders=frozenset(_strings(value["folders"], f"{what}'s folders")),
        walks=tuple(sorted((folder, suffix) for folder, suffix in walks)),
        spawns=tuple(_strings(value["spawns"], f"{what}'s spawns")),
    )


def _when(value: object) -> datetime.datetime:
    if not isinstance(value, str):
        raise RecordError("recorded_at is not a time")
    try:
        when = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecordError(f"recorded_at {value!r} is not a time") from exc
    if when.tzinfo is None:
        raise RecordError(f"recorded_at {value!r} names no zone")
    return when


def load(data: object, source: str = "") -> Record:
    """One leg's record, or a merged one, checked for its shape. A record that says the
    recording broke is refused: what it holds may be short."""
    if not isinstance(data, Mapping):
        raise RecordError(f"{source or 'the record'} is not a JSON object")
    if data.get("schema") != SCHEMA:
        raise RecordError(
            f"{source or 'the record'} is schema {data.get('schema')!r}, not {SCHEMA}"
        )
    if data.get("error"):
        raise RecordError(f"{source or 'the record'} says its recording broke: {data['error']}")
    commit = data.get("commit")
    if not isinstance(commit, str) or not commit:
        raise RecordError(f"{source or 'the record'} names no commit")
    rails = data.get("rails")
    unrecorded = data.get("unrecorded")
    if not isinstance(rails, Mapping) or not isinstance(unrecorded, Mapping):
        raise RecordError(f"{source or 'the record'} holds no rails or no unrecorded rails")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in unrecorded.items()):
        raise RecordError(f"{source or 'the record'}'s unrecorded rails are not named with reasons")
    return Record(
        commit=commit,
        recorded_at=_when(data.get("recorded_at")),
        legs=tuple(_strings(data.get("legs"), "legs")),
        harness=_reads(data.get("harness"), "the harness"),
        rails={str(rail): _reads(reads, rail) for rail, reads in rails.items()},
        unrecorded=dict(unrecorded),
        source=source,
    )


def _union(reads: Iterable[Reads]) -> Reads:
    every = list(reads)
    return Reads(
        files=frozenset().union(*(r.files for r in every)),
        folders=frozenset().union(*(r.folders for r in every)),
        walks=tuple(sorted({w for r in every for w in r.walks})),
        spawns=tuple(sorted({s for r in every for s in r.spawns})),
    )


def merge(records: Sequence[Record]) -> Record:
    """The legs' records of one commit as one. Records of two commits are refused, since a
    record says which commit it was recorded on. The oldest leg's time is the record's."""
    if not records:
        raise RecordError("no record to merge")
    commits = sorted({r.commit for r in records})
    if len(commits) > 1:
        raise RecordError(f"the records are of {len(commits)} commits ({', '.join(commits)})")
    rails: dict[str, list[Reads]] = {}
    for record in records:
        for rail, reads in record.rails.items():
            rails.setdefault(rail, []).append(reads)
    unrecorded = {k: v for r in records for k, v in r.unrecorded.items()}
    return Record(
        commit=commits[0],
        recorded_at=min(r.recorded_at for r in records),
        legs=tuple(sorted({leg for r in records for leg in r.legs})),
        harness=_union(r.harness for r in records),
        rails={rail: _union(each) for rail, each in rails.items() if rail not in unrecorded},
        unrecorded=unrecorded,
        source=", ".join(r.source for r in records if r.source),
    )


# ── selecting ───────────────────────────────────────────────────────────────────────────────


def _ancestors(path: str) -> list[str]:
    """*path*'s folders, nearest first, ending with the repository's root (``""``)."""
    found = []
    while path:
        path = posixpath.dirname(path)
        found.append(path)
    return found


def select(
    paths: Iterable[str],
    rails: Iterable[str],
    record: Record | None,
    *,
    now: datetime.datetime,
    tree: frozenset[str] | None,
    exists: Callable[[str], bool],
    why_none: str = "there is no record",
) -> dict[str, str]:
    """Each rail of *rails* a change of *paths* selects, with the first reason found.

    *tree* holds the files of the record's commit, or is ``None`` when that commit is not at
    hand; *exists* says whether a path is in this checkout."""
    every = sorted(set(rails))
    chosen: dict[str, str] = {}

    def pick(rail: str, why: str) -> None:
        chosen.setdefault(rail, why)

    for rail in ALWAYS:
        if rail in every:
            pick(rail, "always selected")
    if record is None:
        for rail in every:
            pick(rail, why_none)
        return chosen
    if now - record.recorded_at > MAX_AGE:
        recorded = f"{record.recorded_at:%Y-%m-%d %H:%M} UTC"
        for rail in every:
            pick(rail, f"the record is older than a day (recorded {recorded})")
        return chosen
    for rail in every:
        if rail in record.unrecorded:
            pick(rail, f"unrecorded: {record.unrecorded[rail]}")
        elif rail not in record.rails:
            pick(rail, "the record does not hold it")
        elif record.rails[rail].spawns:
            pick(rail, f"starts {record.rails[rail].spawns[0]}: what a child reads is not recorded")
    held = {rail: record.rails[rail] for rail in every if rail in record.rails}
    folders = None if tree is None else {a for f in tree for a in _ancestors(f)}
    for path in paths:
        if record.harness.holds(path):
            for rail in every:
                pick(rail, f"the run's own setup reads {path}, and every rail runs under it")
            continue
        for rail, reads in held.items():
            if reads.holds(path):
                pick(rail, f"reads {path}")
        if tree is not None and path in tree and exists(path):
            continue  # changed in place: no folder's listing moved
        folder = _folder(path, folders, [record.harness, *held.values()])
        if folder is None:
            continue
        shown = folder or "the repository's root"
        if record.harness.lists(folder):
            for rail in every:
                pick(rail, f"the run's own setup lists {shown}, where {path} was added or removed")
            continue
        for rail, reads in held.items():
            if reads.lists(folder):
                pick(rail, f"lists {shown}, where {path} was added or removed")
    return chosen


def _folder(path: str, folders: set[str] | None, readers: Sequence[Reads]) -> str | None:
    """The folder whose listing a path added or removed changes: its own, or the nearest one
    the record's commit held. Without that commit, the nearest one any reader lists."""
    for folder in _ancestors(path):
        if folders is not None:
            if folder in folders or not folder:
                return folder
        elif any(reads.lists(folder) for reads in readers):
            return folder
    return None


# ── fetching main's record ──────────────────────────────────────────────────────────────────

Gh = Callable[[str], bytes]


def _gh(root: Path) -> Gh:
    def call(path: str) -> bytes:
        done = subprocess.run(
            ["gh", "api", path],
            cwd=root,
            capture_output=True,
            timeout=GH_TIMEOUT_S,
            check=False,
        )
        if done.returncode != 0:
            raise RecordError(
                f"gh api {path} exited {done.returncode}: {done.stderr.decode()[:300]}"
            )
        return done.stdout

    return call


def _json(gh: Gh, path: str) -> Any:
    try:
        return json.loads(gh(path))
    except json.JSONDecodeError as exc:
        raise RecordError(f"gh api {path} answered no JSON") from exc


def _object(gh: Gh, path: str) -> Mapping[str, Any]:
    found = _json(gh, path)
    if not isinstance(found, Mapping):
        raise RecordError(f"gh api {path} answered no JSON object")
    return found


def _foreign(run: Mapping[str, Any]) -> str | None:
    head = (run.get("head_repository") or {}).get("full_name")
    if not head or head != (run.get("repository") or {}).get("full_name"):
        return "its head is another repository's"
    return None


def _run_refusal(run: Mapping[str, Any], workflow: str) -> str | None:
    if run.get("event") != "push" or run.get("head_branch") != "main":
        return "not a push to main"
    if not str(run.get("path", "")).endswith(f"/{workflow}"):
        return f"not a run of {workflow}"
    return _foreign(run)


def _pr_run_refusal(run: Mapping[str, Any], pull: Mapping[str, Any]) -> str | None:
    """Why *run* is not *pull*'s own run of :data:`PR_WORKFLOW`: of its branch, made after it was
    opened and before it merged."""
    if run.get("event") != "pull_request":
        return "not a pull request's run"
    if not str(run.get("path", "")).endswith(f"/{PR_WORKFLOW}"):
        return f"not a run of {PR_WORKFLOW}"
    foreign = _foreign(run)
    if foreign:
        return foreign
    made = str(run.get("created_at", ""))
    if run.get("head_branch") != (pull.get("head") or {}).get("ref") or not (
        str(pull.get("created_at", "")) <= made <= str(pull.get("merged_at", ""))
    ):
        return f"not pull request #{pull.get('number')}'s"
    return None


def _tree_of(gh: Gh, sha: str, trees: dict[str, str]) -> str:
    """Commit *sha*'s tree, as the API reads it."""
    if sha not in trees:
        if not _SHA.fullmatch(sha):
            raise RecordError(f"{sha!r} is not a commit id")
        tree = (_object(gh, f"repos/{{owner}}/{{repo}}/git/commits/{sha}").get("tree") or {}).get(
            "sha"
        )
        if not isinstance(tree, str) or not _SHA.fullmatch(tree):
            raise RecordError(f"commit {sha[:12]} names no tree")
        trees[sha] = tree
    return trees[sha]


def _slice(
    gh: Gh,
    run: Mapping[str, Any],
    artifact: Mapping[str, Any],
    leg: str,
    tested: tuple[str, str, dict[str, str]] | None = None,
) -> Record:
    """One leg's record from one of the run's artifacts, held to its run, name and sizes. A push
    run's is recorded on the run's own commit; a pull request run's (*tested*: the main commit,
    its tree, and the trees read so far) on a commit with that tree."""
    name = str(artifact.get("name"))
    if (artifact.get("workflow_run") or {}).get("id") != run["id"]:
        raise RecordError(f"{name} was not uploaded by run {run['id']}")
    if artifact.get("expired"):
        raise RecordError(f"{name} has expired")
    size = artifact.get("size_in_bytes")
    if not isinstance(size, int) or size > MAX_ARTIFACT_BYTES:
        raise RecordError(f"{name} is listed at {size} bytes, past {MAX_ARTIFACT_BYTES}")
    data = gh(f"repos/{{owner}}/{{repo}}/actions/artifacts/{artifact['id']}/zip")
    if len(data) > MAX_ARTIFACT_BYTES:
        raise RecordError(f"{name} arrived at {len(data)} bytes, past {MAX_ARTIFACT_BYTES}")
    members = [m.format(leg=leg) for m in MEMBERS]
    member = members[0]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            held = set(archive.namelist())
            member = next((m for m in members if m in held), member)
            info = archive.getinfo(member)
            if info.file_size > MAX_MEMBER_BYTES:
                raise RecordError(f"{name}'s {member} is {info.file_size} bytes")
            text = archive.read(info)
    except (KeyError, zipfile.BadZipFile) as exc:
        raise RecordError(f"{name} holds no {member}") from exc
    try:
        record = load(json.loads(text), f"{name} of run {run['id']}")
    except json.JSONDecodeError as exc:
        raise RecordError(f"{name}'s {member} is not JSON") from exc
    if tested is None:
        if record.commit != run.get("head_sha"):
            raise RecordError(
                f"{name} was recorded on {record.commit}, not the run's {run.get('head_sha')}"
            )
    else:
        commit, tree, trees = tested
        if _tree_of(gh, record.commit, trees) != tree:
            raise RecordError(
                f"{name} was recorded on {record.commit[:12]}, whose tree is not {commit[:12]}'s"
            )
    if record.legs != (leg,):
        raise RecordError(f"{name} records legs {list(record.legs)}, not {leg}")
    return record


def _newest(artifacts: Iterable[Mapping[str, Any]], prefix: str) -> Mapping[str, Any] | None:
    """The artifact named *prefix* and an attempt, of the highest attempt: a rerun's leg replaces
    the one before it."""
    found: list[tuple[int, Mapping[str, Any]]] = []
    for artifact in artifacts:
        name = str(artifact.get("name", ""))
        attempt = name[len(prefix) :]
        if name.startswith(prefix) and attempt.isdigit() and attempt.isascii():
            found.append((int(attempt), artifact))
    return max(found, key=lambda pair: pair[0])[1] if found else None


def _legs(
    gh: Gh,
    run: Mapping[str, Any],
    notes: list[str],
    tested: tuple[str, str, dict[str, str]] | None = None,
) -> list[Record]:
    """Each leg's record the run holds, with what was refused in *notes*."""
    artifacts = _object(
        gh, f"repos/{{owner}}/{{repo}}/actions/runs/{run['id']}/artifacts?per_page=100"
    )
    slices = []
    for leg in LEGS:
        artifact = _newest(artifacts.get("artifacts", []), ARTIFACT.format(leg=leg))
        if artifact is None:
            continue
        try:
            slices.append(_slice(gh, run, artifact, leg, tested))
        except RecordError as exc:
            notes.append(f"run {run['id']}: {exc}")
    return slices


def _main_commits(gh: Gh, since: str) -> list[tuple[str, str]]:
    """Main's first-parent commits made since *since*, newest first, each with its tree."""
    listed = _json(
        gh, f"repos/{{owner}}/{{repo}}/commits?sha=main&since={since}&per_page={MAX_COMMITS}"
    )
    if not isinstance(listed, list):
        raise RecordError("main's commits are not a list")
    by_sha = {str(c.get("sha")): c for c in listed if isinstance(c, Mapping)}
    found: list[tuple[str, str]] = []
    item = listed[0] if listed else None
    while isinstance(item, Mapping) and len(found) < len(listed):
        sha = item.get("sha")
        tree = ((item.get("commit") or {}).get("tree") or {}).get("sha")
        if not all(isinstance(v, str) and _SHA.fullmatch(v) for v in (sha, tree)):
            raise RecordError("one of main's commits names no commit id or no tree")
        found.append((str(sha), str(tree)))
        parents = item.get("parents")
        first = parents[0] if isinstance(parents, list) and parents else None
        item = by_sha.get(str(first.get("sha"))) if isinstance(first, Mapping) else None
    return found


def _merged(
    gh: Gh, commit: str, tree: str, since: str, trees: dict[str, str], notes: list[str]
) -> Record | None:
    """The record of the pull request merged into main as *commit*: its newest run of
    :data:`PR_WORKFLOW` whose own head has *commit*'s tree and whose legs were recorded on a
    commit with that tree, read as *commit*'s."""
    pulls = _json(gh, f"repos/{{owner}}/{{repo}}/commits/{commit}/pulls")
    merged = [
        p
        for p in (pulls if isinstance(pulls, list) else [])
        if isinstance(p, Mapping)
        and p.get("merge_commit_sha") == commit
        and p.get("merged_at")
        and (p.get("base") or {}).get("ref") == "main"
    ]
    if len(merged) != 1:
        notes.append(f"commit {commit[:12]}: {len(merged)} pull requests were merged as it, not 1")
        return None
    (pull,) = merged
    number = pull.get("number")
    head = pull.get("head") or {}
    if (head.get("repo") or {}).get("full_name") != (
        (pull.get("base") or {}).get("repo") or {}
    ).get("full_name"):
        notes.append(f"pull request #{number}: its head is another repository's")
        return None
    branch = urllib.parse.quote(str(head.get("ref", "")), safe="")
    listed = _object(
        gh,
        f"repos/{{owner}}/{{repo}}/actions/workflows/{PR_WORKFLOW}/runs"
        f"?event=pull_request&branch={branch}&per_page=50&created=%3E%3D{since}",
    )
    runs = sorted(
        listed.get("workflow_runs", []), key=lambda r: str(r.get("created_at")), reverse=True
    )
    for run in runs:
        refusal = _pr_run_refusal(run, pull)
        if refusal is None:
            # The run's own head, never the pull request's now: it may have moved since the run.
            head_sha = str(run.get("head_sha"))
            try:
                if _tree_of(gh, head_sha, trees) != tree:
                    refusal = f"its head {head_sha[:12]} does not have {commit[:12]}'s tree"
            except RecordError as exc:
                refusal = str(exc)
        if refusal:
            notes.append(f"run {run.get('id')}: {refusal}")
            continue
        slices = _legs(gh, run, notes, (commit, tree, trees))
        if slices:
            for one in slices:
                one.commit = commit
            record = merge(slices)
            record.source = (
                f"run {run['id']} of {PR_WORKFLOW}, pull request #{number}, "
                f"legs {', '.join(record.legs)}"
            )
            return record
    return None


def fetch(gh: Gh, now: datetime.datetime) -> tuple[Record | None, list[str]]:
    """The newest record of main's first-parent commits made within :data:`MAX_AGE`, with what
    was skipped and why: a commit's push run's, else its pull request's. ``None`` when no
    commit of that day has one."""
    notes: list[str] = []
    since = (now - MAX_AGE).strftime("%Y-%m-%dT%H:%M:%SZ")
    pushes: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for workflow in WORKFLOWS:
        listed = _object(
            gh,
            f"repos/{{owner}}/{{repo}}/actions/workflows/{workflow}/runs"
            f"?branch=main&event=push&per_page=50&created=%3E%3D{since}",
        )
        runs = sorted(
            listed.get("workflow_runs", []), key=lambda r: str(r.get("created_at")), reverse=True
        )
        for run in runs:
            refusal = _run_refusal(run, workflow)
            if refusal:
                notes.append(f"run {run.get('id')}: {refusal}")
                continue
            pushes.setdefault(str(run.get("head_sha")), []).append((workflow, run))
    trees: dict[str, str] = {}
    for commit, tree in _main_commits(gh, since):
        trees[commit] = tree
        for workflow, run in pushes.get(commit, []):
            slices = _legs(gh, run, notes)
            if slices:
                record = merge(slices)
                record.source = f"run {run['id']} of {workflow}, legs {', '.join(record.legs)}"
                return record, notes
        found = _merged(gh, commit, tree, since, trees, notes)
        if found is not None:
            return found, notes
    return None, notes


# ── the command ─────────────────────────────────────────────────────────────────────────────


def _tree(root: Path, commit: str) -> frozenset[str] | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", "-z", "--name-only", commit],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return frozenset(p for p in done.stdout.decode("utf-8", "replace").split("\0") if p)


def _changed(root: Path, given: Iterable[str]) -> list[str]:
    paths = []
    for path in given:
        candidate = Path(path)
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(root.resolve())
            except ValueError as exc:
                raise RecordError(f"{path} is outside {root}") from exc
        text = posixpath.normpath(candidate.as_posix())
        if text == "." or text.startswith("../"):
            raise RecordError(f"{path} names no file in {root}")
        paths.append(text)
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.ci rails select")
    parser.add_argument("--paths", nargs="+", required=True, help="the changed paths")
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        help="a record file to read instead of main's (a leg's, or merged); repeatable",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--why", action="store_true", help="each rail with why it was selected")
    args = parser.parse_args(argv)
    root = Path(args.root)
    now = datetime.datetime.now(datetime.timezone.utc)
    rails = [
        rel
        for rel in (p.relative_to(root).as_posix() for p in rails_census.test_modules(root))
        if rel.startswith(rails_census.RAILS + "/")
    ]
    try:
        paths = _changed(root, args.paths)
    except RecordError as exc:
        print(f"rails select: {exc}", file=sys.stderr)
        return 2
    record: Record | None = None
    why_none = "there is no record"
    try:
        if args.map:
            record = merge(
                [load(json.loads(Path(m).read_text(encoding="utf-8")), m) for m in args.map]
            )
        else:
            record, notes = fetch(_gh(root), now)
            for note in notes:
                print(f"rails select: skipped {note}", file=sys.stderr)
            if record is None:
                why_none = "no commit of main in the last day has a record"
    except (OSError, RecordError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        why_none = f"the record cannot be read: {exc}"
        record = None
    tree = _tree(root, record.commit) if record is not None else None
    chosen = select(
        paths,
        rails,
        record,
        now=now,
        tree=tree,
        exists=lambda p: (root / p).exists() or (root / p).is_symlink(),
        why_none=why_none,
    )
    for rail in sorted(chosen):
        print(f"{rail}\t{chosen[rail]}" if args.why else rail)
    if record is None:
        said = why_none
    else:
        said = (
            f"the record of {record.commit[:12]}, recorded {record.recorded_at:%Y-%m-%d %H:%M} UTC"
        )
        if record.source:
            said += f" ({record.source})"
        if tree is None:
            said += "; that commit is not in this clone, so every path counts as added or removed"
    print(f"rails select: {len(chosen)} of {len(rails)} rails, from {said}", file=sys.stderr)
    return 0
