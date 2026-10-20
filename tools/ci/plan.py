"""One plan per run from the diff: ``python -m tools.ci plan <tier>`` (#3945).

:func:`plan` turns a tier and the paths a change touched into the jobs to run. The area map is
generated from the catalogue's declared inputs (``tools/ci/catalogue.toml``), with only its
explicit overrides kept beside it (``tools/ci/areas.toml``):

- a path a job's declared ``inputs`` reach schedules that job, and so does a path one of its
  steps runs (a script or folder a step names, and a sibling script that script imports);
- an override adds jobs for its paths, with its reason, or plans the whole tier.

A path is known only when an input other than the whole tree (:func:`whole_tree`) names it:
an include selects it, or an exclude leaves it out by name. Neither a step nor an override
makes a path known, so an override only ever adds and never takes a job out of a plan.

Some jobs are planned whatever the diff: the rails (``catalogue.MERGE_NEEDS_JOB``) and their
own check (``catalogue.RAILS_CHECKS_JOB``, which holds every rail to one half), every
``security`` job and every ``live`` one. Each job ``after`` names comes with the job that
names it.

What the planner cannot read plans the whole tier: an empty diff (it proves nothing about what
to leave out), a path no input but the whole tree names, a path that is not a plain path inside
the repository, a list that cannot be read, and any error inside the planner. A forgotten path
therefore runs everything, never nothing.

Every plan reports the rails floor: the median, over the passing CI runs in a folder of
telemetry history (``tools/ci/telemetry.py``), or, with ``--repo``, over main's newest collected
CI runs (:func:`rails_floor_from_ci`), of each run's ``rails`` minutes (the slower of its two
halves, ``rails (1)`` and ``rails (2)``, or the one job a run before the halves had; a run
missing a half, or naming one twice, measures nothing); "not measured" when there is none,
never a guessed number. The floor is a report: nothing it reads changes the jobs.

``--trusted-areas`` names main's areas file. Its overrides, read against main's own catalogue
beside it, are added to the tree's: the overrides in force are the union of main's and the pull
request's, so a pull request that drops or narrows one of main's (the workflows', ``tools/ci``'s,
``deploy/docker``'s whole tier, or a job a script adds) still plans what main's says. A job-adding
override of main's that names a job the tree's catalogue lacks plans the whole tier for its paths,
since the job it promised can no longer be named. Either file unreadable plans nothing, exit 2,
so the caller runs everything. Main's catalogue is also the reference the tree's is loaded
against (``catalogue.against``): a pull request's catalogue that drops a security or live job of
main's, unless main marks it retiring, is refused, and plans nothing either.

``--outputs`` prints the two lines ``ci.yml``'s ``changes`` job publishes, ``full=true|false``
and ``jobs=<JSON list>``, on stdout, and the plan on stderr.

``--matrix`` prints the plan as a job matrix (:func:`matrix`): one entry per planned job and
shard, keyed as ``run-job`` keys its record, each with the stage it runs in after the planned
jobs whose output it uses. With ``--outputs`` it adds ``matrix=<JSON>`` (the ``include`` list a
``strategy.matrix`` reads), ``keys=<JSON list>`` (every entry's key, the plan a results stage
judges the uploaded records against), and a ``matrix-<job>=<JSON>`` line for each workflow job
that runs part of it (:func:`stage_jobs`): ``build``, ``test`` and ``test-<k>``.

Standard library only, like every ``tools/ci`` module. Pathspecs are read as git reads them;
a test holds :func:`reaches` to ``git ls-files`` over the forms the catalogue accepts, and the
loader refuses a form git reads otherwise (``catalogue.pathspec_fault``).
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import functools
import io
import json
import math
import posixpath
import re
import statistics
import sys
import time
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from . import catalogue, rails_census, shards, verdict
from .catalogue import Catalogue, Job, Override

#: What :func:`rails_floor` says when no passing rails run is recorded.
NOT_MEASURED: dict[str, Any] = {"minutes": None, "runs": 0}

#: The workflow and job whose runs the rails floor is measured from.
FLOOR_WORKFLOW, FLOOR_JOB = "ci.yml", catalogue.MERGE_NEEDS_JOB

#: The rails job's legs as GitHub names them, one per half (``rails_census.HALVES``).
FLOOR_LEGS: tuple[str, ...] = tuple(
    f"{FLOOR_JOB} ({n})" for n in range(1, len(rails_census.HALVES) + 1)
)

_WILD = frozenset("*?[")


@dataclass(frozen=True)
class Plan:
    """The jobs a run plans, in catalogue order, each with why; ``full`` and its reasons when
    the whole tier runs; and the rails floor."""

    tier: str
    full: bool
    jobs: tuple[str, ...]
    reasons: Mapping[str, list[str]]
    full_reasons: tuple[str, ...]
    rails_floor: Mapping[str, Any] = field(default_factory=lambda: dict(NOT_MEASURED))

    def as_json(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "full": self.full,
            "jobs": list(self.jobs),
            "reasons": {k: list(v) for k, v in self.reasons.items()},
            "full_reasons": list(self.full_reasons),
            "rails_floor": dict(self.rails_floor),
        }


# ── pathspecs, as git reads them ─────────────────────────────────────────────────────────────


@functools.lru_cache(maxsize=None)
def _wild_regex(pattern: str, pathname: bool) -> re.Pattern[str]:
    """git's wildmatch as a regex. Under ``:(glob)`` (``pathname``) ``*``, ``?`` and a class stay
    within a folder, ``**/`` is any folders and a trailing ``/**`` everything inside; without
    it every run of ``*`` and each ``?`` and class cross folders too."""
    out, i, n = [], 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            j = i
            while j < n and pattern[j] == "*":
                j += 1
            bounded = (i == 0 or pattern[i - 1] == "/") and (j == n or pattern[j] == "/")
            if not pathname:
                out.append(".*")
            elif j - i >= 2 and bounded and j == n:
                out.append(".*")
            elif j - i >= 2 and bounded:
                out.append("(?:.*/)?")
                j += 1
            else:
                out.append("[^/]*")
            i = j
        elif c == "?":
            out.append("[^/]" if pathname else ".")
            i += 1
        elif c == "[":
            j = i + 1
            negate = j < n and pattern[j] in "!^"
            j += negate
            first = j
            j += j < n and pattern[j] == "]"
            j = pattern.index("]", j)
            body = "".join("\\" + ch if ch in "\\[]^" else ch for ch in pattern[first:j])
            out.append(
                "[" + ("^" if negate else "") + body + ("/" if negate and pathname else "") + "]"
            )
            i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out), re.DOTALL)


def _magic(spec: str) -> tuple[frozenset[str], str]:
    """A pathspec's magic words, long form or short, and the path it names."""
    if spec.startswith(":("):
        end = spec.index(")")
        return frozenset(w.strip() for w in spec[2:end].split(",")), spec[end + 1 :]
    if spec.startswith(":"):
        match = catalogue.SHORT_MAGIC.match(spec)
        signs = match.group(0) if match else ":"
        exclude = "!" in signs or "^" in signs
        return frozenset({"exclude"} if exclude else ()), spec[len(signs) :]
    return frozenset(), spec


def _one(spec: str, path: str) -> tuple[bool, bool]:
    """Whether ``spec`` is an exclude, and whether it matches ``path``. git compares a spec as a
    literal path first (the path itself, or one inside it; a trailing ``/`` names only a
    folder), then as a wildcard unless it is ``:(literal)``."""
    words, body = _magic(spec)
    if "icase" in words:
        body, path = body.lower(), path.lower()
    exclude = "exclude" in words
    if not body or path == body:
        return exclude, True
    if path.startswith(body) and (body.endswith("/") or path[len(body)] == "/"):
        return exclude, True
    if "literal" in words or not (_WILD & set(body)):
        return exclude, False
    return exclude, _wild_regex(body, "glob" in words).fullmatch(path) is not None


def reaches(specs: Sequence[str], path: str) -> bool:
    """Whether the pathspecs ``specs`` select ``path``: one of its includes matches (every
    path, when it has only excludes) and none of its excludes does."""
    included, any_include = False, False
    for spec in specs:
        exclude, hit = _one(spec, path)
        if exclude and hit:
            return False
        if not exclude:
            any_include = True
            included = included or hit
    return included or not any_include


# ── the area map: what each job's inputs name and its steps run ─────────────────────────────

#: Paths no input of a real catalogue names together: a pathspec that matches every one of them
#: matches the whole tree, however it is spelled.
_PROBES = ("a", "Z.Z", ".x/-", "0/1/2/3.q", "\u2603", "~ \u2603/[!]/\u2603.\u2603")


@functools.lru_cache(maxsize=None)
def _every_path(spec: str) -> bool:
    return all(_one(spec, p)[1] for p in _PROBES)


def whole_tree(spec: str) -> bool:
    """Whether ``spec`` is an include of every path (``:(glob)**``, ``:/``, ``*``, ``?*``,
    ``:(glob)**/*``). A job may declare one, and runs on any change, but it names no path."""
    return not _one(spec, _PROBES[0])[0] and _every_path(spec)


def known(specs: Sequence[str], path: str) -> bool:
    """Whether ``specs`` name ``path`` itself: an include other than the whole tree selects it,
    or an exclude leaves it out by name, a deliberate statement that the job skips it. A
    pathspec that matches every path, include or exclude, names none."""
    return any(_one(spec, path)[1] for spec in specs if not _every_path(spec))


def _step_paths(job: Job, root: Path | None) -> set[str]:
    """The repository paths a job's steps name, and the sibling scripts a named script
    imports when ``root`` is given."""
    found: set[str] = set()
    for step in job.command:
        for arg in step.argv[1:]:
            if arg.startswith("-") or "=" in arg or "{" in arg or _WILD & set(arg):
                continue
            if "/" not in arg and "." not in arg:
                continue
            rel = posixpath.normpath(posixpath.join(step.workdir or ".", arg))
            if rel.startswith("..") or rel.startswith("/") or rel == ".":
                continue
            found.add(rel)
    if root is not None:
        for rel in sorted(p for p in found if p.endswith(".py")):
            found |= _imported_siblings(root, rel)
    return found


def _imported_siblings(root: Path, rel: str) -> set[str]:
    try:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    names = {
        a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    names |= {
        n.module.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module and n.level == 0
    }
    folder = posixpath.dirname(rel)
    return {
        posixpath.join(folder, f"{name}.py")
        for name in names
        if (root / folder / f"{name}.py").is_file()
    }


def _step_reaches(paths: set[str], path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in paths)


# ── the plan ─────────────────────────────────────────────────────────────────────────────────


def _readable(path: object) -> bool:
    return (
        isinstance(path, str)
        and bool(path)
        and not any(c in path for c in "\n\r\0")
        and not path.startswith("/")
        and ".." not in path.split("/")
        and path == posixpath.normpath(path)
    )


def _whole(
    cat: Catalogue,
    tier: str,
    why: list[str],
    floor: Mapping[str, Any],
    reasons: Mapping[str, list[str]] | None = None,
) -> Plan:
    """The whole tier, each job with what had scheduled it so far and the whole tier."""
    jobs = tuple(j.id for j in cat.tier(tier))
    found = reasons or {}
    return Plan(
        tier,
        True,
        jobs,
        {j: [*found.get(j, []), "the whole tier"] for j in jobs},
        tuple(why),
        floor,
    )


def plan(
    cat: Catalogue,
    tier: str,
    changed: Iterable[object] | None,
    *,
    overrides: Sequence[Override],
    full: bool = False,
    history: Path | None = None,
    root: Path | None = None,
    floor: Mapping[str, Any] | None = None,
) -> Plan:
    """The plan for ``tier`` over the ``changed`` paths, with the area map's ``overrides``
    (:func:`catalogue.read_areas`). A tier with no job in the catalogue raises ValueError,
    since its plan would run nothing; anything else the planner cannot read plans the whole
    tier."""
    if tier not in catalogue.TIERS:
        raise ValueError(f"{tier!r} is not a tier: one of {', '.join(catalogue.TIERS)}")
    if not cat.tier(tier):
        raise ValueError(f"the {tier} tier has no job in the catalogue, so its plan runs nothing")
    if floor is not None:
        floor = dict(floor)
    else:
        try:
            floor = rails_floor(history)
        except Exception:  # noqa: BLE001 - a history it cannot read measures nothing
            floor = dict(NOT_MEASURED)
    try:
        if full:
            return _whole(cat, tier, ["the run asked for the whole tier (--full)"], floor)
        return _plan(cat, tier, changed, overrides, floor, root)
    except Exception as exc:  # noqa: BLE001 - a broken planner must plan everything
        return _whole(cat, tier, [f"the planner failed ({type(exc).__name__}: {exc})"], floor)


def _plan(
    cat: Catalogue,
    tier: str,
    changed: Iterable[object] | None,
    overrides: Sequence[Override],
    floor: Mapping[str, Any],
    root: Path | None,
) -> Plan:
    if changed is None:
        return _whole(cat, tier, ["the diff could not be read"], floor)
    listed = list(changed)
    unreadable = [p for p in listed if not _readable(p)]
    if unreadable:
        why = [f"{p!r} cannot be read as a path in the repository" for p in unreadable]
        return _whole(cat, tier, why, floor)
    if not listed:
        return _whole(
            cat, tier, ["the diff is empty, which proves nothing about what to skip"], floor
        )
    paths = [str(p) for p in listed]
    in_tier = {j.id for j in cat.tier(tier)}
    reasons: dict[str, list[str]] = {}
    named: set[str] = set()

    def add(job_id: str, why: str) -> None:
        reasons.setdefault(job_id, []).append(why)

    for job in cat.jobs:
        steps = _step_paths(job, root)
        for p in paths:
            if known(job.inputs, p):
                named.add(p)
            if reaches(job.inputs, p):
                add(job.id, f"{p}: a declared input")
            elif _step_reaches(steps, p):
                add(job.id, f"{p}: a step runs it")
    whole: list[str] = []
    for override in overrides:
        for p in (p for p in paths if reaches(override.paths, p)):
            if override.whole_tier:
                whole.append(f"{p}: {override.reason}")
            for job_id in override.jobs:
                add(job_id, f"{p}: {override.reason}")
    whole += [f"{p} is named by no input but the whole tree" for p in paths if p not in named]
    if whole:
        return _whole(cat, tier, whole, floor, reasons)
    for job in cat.tier(tier):
        if job.id == catalogue.MERGE_NEEDS_JOB:
            add(job.id, "the rails always run")
        if job.id == catalogue.RAILS_CHECKS_JOB:
            add(job.id, "the rails' own check always runs")
        if job.security == "security":
            add(job.id, "security posture: never selected out")
        if job.live:
            add(job.id, "live: never selected out")
    grew = True
    while grew:
        grew = False
        for job in cat.tier(tier):
            for other in job.after if job.id in reasons else ():
                if other in in_tier and other not in reasons:
                    add(other, f"after: {job.id} uses its output")
                    grew = True
    order = tuple(j.id for j in cat.tier(tier) if j.id in reasons)
    return Plan(tier, False, order, {j: reasons[j] for j in order}, (), floor)


# ── the plan as a job matrix ─────────────────────────────────────────────────────────────────


#: The toolchain words that are a program the setup action (``.github/actions/setup``) installs
#: from the runner's package manager when the image lacks it: ``run-job`` installs ``uv-sync``,
#: ``npm-ci`` and ``chromium`` itself, the action sets up ``uv`` and ``node`` from the entry's
#: ``python`` and ``node``, and ``run-job`` finds every other program on PATH or cannot run.
SETUP_PROGRAMS: tuple[str, ...] = ("tmux", "shellcheck", "dash")

#: The workflow job that runs the stage-1 entries a later entry waits for, and the one that runs
#: every other stage-1 entry beside it. Stage ``k`` from 2 is run by ``test-<k>``.
BUILD_JOB, TEST_JOB = "build", "test"


def matrix_key(job: Job, shard: int | None) -> str:
    """The key of one matrix entry: ``run-job``'s record key for that job and shard, so a
    workflow's results stage can name exactly which planned record is missing."""
    return f"{job.id} ({shard}/{job.shards})" if shard is not None else job.id


def matrix(cat: Catalogue, planned: Plan) -> dict[str, Any]:
    """``planned`` as a job matrix: one entry per planned job and shard, in plan order, each with
    what its runner needs (``runs-on``, ``timeout``, ``python``, ``node``, ``browser``, and the
    ``container`` a script job runs in, or empty) and the ``shard`` ``run-job`` takes (``i/N``,
    or empty for a job that takes no ``--shard``). ``after`` names the planned jobs whose output
    the entry's job uses, and ``stage`` is 1 for an entry that waits for none, else one more than
    the latest stage it waits for: a ``strategy.matrix`` starts every entry at once, so a
    workflow runs one matrix per stage, each after the one before. ``keys`` lists every entry's
    key: the plan a results stage judges the run's records against. A planned job the catalogue
    does not hold, or an ``after`` that makes a cycle, raises ValueError naming it, so the
    command plans nothing and its caller runs everything.

    What the workflow's test stage needs besides: ``slug``, the key as the record artifact's
    name spells it (``verdict.record_slug``); ``history``, whether the checkout needs the whole
    history (a step names ``{base}``, which ``run-job`` reads as the merge-base with
    ``origin/main``); ``programs``, the toolchain's :data:`SETUP_PROGRAMS`; and
    ``external``, the job's external inputs (the shard plan, a partner checkout)."""
    by_id = {j.id: j for j in cat.jobs}
    gone = [j for j in planned.jobs if j not in by_id]
    if gone:
        raise ValueError(f"the plan names {', '.join(gone)}, which the catalogue does not hold")
    waits = {j: [a for a in by_id[j].after if a in planned.jobs] for j in planned.jobs}
    stages: dict[str, int] = {}

    def stage(job_id: str, path: tuple[str, ...] = ()) -> int:
        if job_id in path:
            raise ValueError(f"the jobs' after makes a cycle: {' -> '.join((*path, job_id))}")
        if job_id not in stages:
            stages[job_id] = 1 + max((stage(a, (*path, job_id)) for a in waits[job_id]), default=0)
        return stages[job_id]

    include: list[dict[str, Any]] = []
    for job_id in planned.jobs:
        job = by_id[job_id]
        for shard in range(1, job.shards + 1) if job.shards > 1 else (None,):
            include.append(
                {
                    "id": job.id,
                    "key": matrix_key(job, shard),
                    "shard": f"{shard}/{job.shards}" if shard is not None else "",
                    "runs-on": job.platform,
                    "timeout": job.timeout_min,
                    "python": job.python or "",
                    "node": job.needs_node,
                    "browser": job.needs_browser,
                    "container": job.container or "",
                    "after": list(waits[job_id]),
                    "stage": stage(job_id),
                    "slug": verdict.record_slug(matrix_key(job, shard)),
                    "history": any("base" in step.placeholders() for step in job.command),
                    "programs": [t for t in job.toolchain if t in SETUP_PROGRAMS],
                    "external": list(job.external_inputs),
                }
            )
    return {"include": include, "keys": [e["key"] for e in include]}


def stage_jobs(grid: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """A matrix's entries by the workflow job that runs them, each list in plan order, and only
    the jobs with an entry: :data:`BUILD_JOB`, the stage-1 entries a later entry waits for;
    :data:`TEST_JOB`, every other stage-1 entry, which waits for nothing and starts beside
    ``build``; and ``test-<k>`` for stage ``k`` from 2, after the stage before it (``test-2``
    after ``build``). So a long stage-1 entry no other entry needs never holds back the stages
    that wait on the build."""
    entries = list(grid["include"])
    waited = {a for e in entries for a in e["after"]}
    out: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        if e["stage"] == 1:
            name = BUILD_JOB if e["id"] in waited else TEST_JOB
        else:
            name = f"{TEST_JOB}-{e['stage']}"
        out.setdefault(name, []).append(e)
    # By stage, and build before test within stage 1.
    return dict(sorted(out.items(), key=lambda item: (item[1][0]["stage"], item[0] != BUILD_JOB)))


# ── the rails floor ──────────────────────────────────────────────────────────────────────────


def _is_floor_job(name: object) -> bool:
    """The rails job, or one of its halves: GitHub names a matrix leg ``rails (1)``."""
    if not isinstance(name, str):
        return False
    if name == FLOOR_JOB:
        return True
    head, _, rest = name.partition(" (")
    return head == FLOOR_JOB and rest.endswith(")") and rest[:-1].isdigit()


def _run_minutes(jobs: list[Any]) -> float | None:
    """One run's rails minutes: the slower of its rails jobs, which is when the rails ended.
    Its rails jobs are the one ``rails`` job of a run before the halves, or exactly the halves
    (:data:`FLOOR_LEGS`), each once: a run missing a half, naming one twice or both kinds says
    nothing, and so does one with any rails job that is not a passing measurement."""
    legs = [job for job in jobs if isinstance(job, dict) and _is_floor_job(job.get("name"))]
    names = sorted(str(job["name"]) for job in legs)
    if names != [FLOOR_JOB] and names != sorted(FLOOR_LEGS):
        return None
    measured = [_measured(job) for job in legs]
    if any(m is None for m in measured):
        return None
    return max(m for m in measured if m is not None)


def _measured(job: object) -> float | None:
    """A passing rails job's minutes, when they are a measurement: a finite positive number."""
    if not isinstance(job, dict) or not _is_floor_job(job.get("name")):
        return None
    minutes = job.get("minutes")
    if job.get("conclusion") != "success" or isinstance(minutes, bool):
        return None
    if not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes <= 0:
        return None
    return float(minutes)


#: How many recorded CI runs the floor is the median of, and how long reading them may take.
FLOOR_RUNS, FLOOR_SECONDS = 3, 60.0


def _rails_minutes(head: object) -> float | None:
    """One history file's run line's rails minutes, when they are a measurement: the slower
    half, or the one job a run before the halves had (:func:`_run_minutes`)."""
    if not isinstance(head, dict) or head.get("kind") != "run":
        return None
    if head.get("workflow") != FLOOR_WORKFLOW or not isinstance(head.get("jobs"), list):
        return None
    return _run_minutes(head["jobs"])


def _floor(minutes: Sequence[float]) -> dict[str, Any]:
    if not minutes:
        return dict(NOT_MEASURED)
    return {"minutes": round(statistics.median(minutes), 2), "runs": len(minutes)}


def rails_floor(folder: Path | None) -> dict[str, Any]:
    """The median minutes of the passing ``rails`` job over the CI runs whose history files
    are in ``folder``, and how many runs that is; :data:`NOT_MEASURED` without one. A head it
    cannot measure from is skipped, never guessed at."""
    minutes: list[float] = []
    for path in sorted(folder.rglob("history-*.jsonl")) if folder and folder.is_dir() else ():
        try:
            head = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        except (OSError, IndexError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(head, dict) or head.get("kind") != "run":
            continue
        if head.get("workflow") != FLOOR_WORKFLOW or not isinstance(head.get("jobs"), list):
            continue
        run = _run_minutes(head["jobs"])
        if run is not None:
            minutes.append(run)
    return _floor(minutes)


def _archive_heads(archive: bytes, run: int) -> list[object]:
    """Each history file's run line in one archive, read within the history's line bound, and
    only a line that describes ``run``."""
    heads: list[object] = []
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for member in zipped.infolist():
            if not member.filename.endswith(".jsonl"):
                continue
            with zipped.open(member) as handle:
                raw = handle.readline(shards.MAX_HISTORY_LINE_BYTES + 1)
            if len(raw) > shards.MAX_HISTORY_LINE_BYTES:
                continue
            head = json.loads(raw.decode("utf-8"))
            if isinstance(head, dict) and head.get("run_id") == run:
                heads.append(head)
    return heads


T = TypeVar("T")


def _within(
    call: Callable[..., T], clock: Callable[[], float], deadline: float
) -> Callable[..., T]:
    """``call`` (a gh call taking ``timeout``) given only the time left before ``deadline``,
    and never more than one gh call may take; past the deadline it is not made at all."""

    def bounded(*args: str) -> T:
        left = deadline - clock()
        if left <= 0:
            raise TimeoutError(f"reading the rails floor took its {FLOOR_SECONDS:g} seconds")
        return call(*args, timeout=min(float(shards.GH_TIMEOUT_SECONDS), left))

    return bounded


def rails_floor_from_ci(
    repo: str,
    *,
    text: shards.Text,
    binary: shards.Binary,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """The rails floor from main's recorded CI runs: the median over the newest
    :data:`FLOOR_RUNS` completed main runs of ``ci.yml`` whose history the collector itself
    uploaded (``shards.TelemetryRecord`` checks who uploaded each archive), counting a red run
    whose rails jobs passed and never a cancelled one.

    ``text`` and ``binary`` are gh calls that take a ``timeout``. Each is given only what is
    left of :data:`FLOOR_SECONDS`, and none is made once they are spent, so the whole read ends
    within them. Any failure, running out of time included, gives what was read so far, never a
    guess, with a warning on stderr."""
    deadline = clock() + FLOOR_SECONDS
    text, binary = _within(text, clock, deadline), _within(binary, clock, deadline)
    minutes: list[float] = []
    try:
        record = shards.TelemetryRecord(repo, text=text, binary=binary)
        listed = text(
            "api",
            f"repos/{repo}/actions/workflows/{FLOOR_WORKFLOW}/runs"
            "?branch=main&status=completed&per_page=30",
            "--jq",
            '.workflow_runs[] | "\\(.id) \\(.conclusion) \\(.head_repository.full_name)"',
        )
        for line in listed.splitlines():
            if len(minutes) == FLOOR_RUNS:
                break
            ident, conclusion, owner = (line.split() + ["", "", ""])[:3]
            if not ident.isdigit() or conclusion not in record.COUNTED or owner != repo:
                continue
            archive = record._archive(int(ident))
            if archive is None:
                continue
            try:
                heads = _archive_heads(archive, int(ident))
            except (zipfile.BadZipFile, ValueError, UnicodeDecodeError, OSError, EOFError):
                continue
            found = [m for m in (_rails_minutes(h) for h in heads) if m is not None]
            if found:
                minutes.append(found[0])
    except Exception as exc:  # noqa: BLE001 - the floor is a report, never a gate
        print(f"::warning title=rails floor not read::{type(exc).__name__}: {exc}", file=sys.stderr)
    return _floor(minutes)


def _ci_floor(repo: str) -> dict[str, Any]:
    """:func:`rails_floor_from_ci` over gh, with anything it prints sent to stderr: stdout
    carries the plan alone, or under ``--outputs`` the two output lines alone."""
    with contextlib.redirect_stdout(sys.stderr):
        return rails_floor_from_ci(repo, text=shards._gh_text, binary=shards._gh_binary)


# ── the command ──────────────────────────────────────────────────────────────────────────────


def _changed(args: argparse.Namespace) -> list[str] | None:
    try:
        raw = Path(args.paths).read_bytes() if args.paths else sys.stdin.buffer.read()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    parts = text.split("\0") if args.z else text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def read_beside(path: Path, cat: Catalogue, areas: Path | None = None) -> tuple[Override, ...]:
    """The overrides in ``areas``, or in the areas file beside the catalogue at ``path``."""
    where = areas or path.with_name(Path(catalogue.AREAS).name)
    return catalogue.load_areas(catalogue._text(where, str(where)), cat, str(where))


def trusted_catalogue(areas: Path) -> Catalogue:
    """Main's catalogue, beside its trusted areas file. Raises when it cannot be read."""
    path = areas.with_name(Path(catalogue.CATALOGUE).name)
    return catalogue.load(catalogue._text(path, str(path)), str(path))


def trusted_overrides(
    areas: Path, cat: Catalogue, trusted: Catalogue | None = None
) -> tuple[Override, ...]:
    """The overrides of a trusted areas file (main's, beside main's catalogue), read against that
    catalogue, never the tree under test's, as they apply to ``cat``, the tree's. Added to the
    tree's own they make the union: a path is whole-tier when either copy says so, and gets every
    job either copy adds. A job main's override adds that ``cat`` does not have makes its paths
    whole-tier instead, so dropping a job cannot drop the run it promised. Each reason says it is
    main's. ``trusted``: main's catalogue already read (:func:`trusted_catalogue`). Raises what
    :func:`read_beside` raises when either file cannot be read, and the command then plans
    nothing, so its caller runs everything."""
    path = areas.with_name(Path(catalogue.CATALOGUE).name)
    if trusted is None:
        trusted = trusted_catalogue(areas)
    ids = {j.id for j in cat.jobs}
    out: list[Override] = []
    for o in read_beside(path, trusted, areas):
        reason = f"{o.reason} (main's {catalogue.AREAS})"
        gone = [j for j in o.jobs if j not in ids]
        if gone:
            why = f"{reason}; it adds {', '.join(gone)}, which this catalogue does not have"
            out.append(Override(o.paths, (), True, why))
            continue
        out.append(Override(o.paths, o.jobs, o.whole_tier, reason))
    return tuple(out)


def main(argv: list[str]) -> int:
    """``python -m tools.ci plan <tier>``: the plan as JSON. Exit 0 with a plan, 2 when the
    tier, the catalogue or its overrides, or the ``--trusted-areas`` file or the catalogue
    beside it, cannot be read, or that catalogue refuses this one (nothing is printed, so a
    caller plans everything)."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci plan", description=__doc__.splitlines()[0]
    )
    parser.add_argument("tier")
    parser.add_argument("--catalogue", type=Path, default=catalogue.REPO / catalogue.CATALOGUE)
    parser.add_argument(
        "--areas", type=Path, default=None, help="the overrides (areas.toml beside the catalogue)"
    )
    parser.add_argument(
        "--trusted-areas",
        type=Path,
        default=None,
        help="main's areas file: its overrides are added to the tree's own (the union)",
    )
    parser.add_argument("--paths", default=None, help="the changed paths, one per line (stdin)")
    parser.add_argument("-z", action="store_true", help="the paths are NUL-separated")
    parser.add_argument("--full", action="store_true", help="plan the whole tier")
    parser.add_argument("--history", type=Path, default=None, help="a folder of telemetry")
    parser.add_argument("--root", type=Path, default=catalogue.REPO, help="the repository root")
    parser.add_argument(
        "--repo", default=None, help="owner/name: read the rails floor from main's CI history"
    )
    parser.add_argument(
        "--outputs",
        action="store_true",
        help="print GitHub output lines (full=<bool>, jobs=<JSON>) and the plan on stderr",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="print the plan as a job matrix; with --outputs, add matrix= and keys= lines",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) and exc.code else 2
    try:
        trusted = None
        if args.trusted_areas is not None:
            trusted = trusted_catalogue(args.trusted_areas)
        source = str(args.catalogue)
        cat = catalogue.load(catalogue._text(args.catalogue, source), source, trusted)
        overrides = read_beside(args.catalogue, cat, args.areas)
        if args.trusted_areas is not None:
            overrides += trusted_overrides(args.trusted_areas, cat, trusted)
        result = plan(
            cat,
            args.tier,
            _changed(args),
            overrides=overrides,
            full=args.full,
            history=args.history,
            root=args.root,
            floor=_ci_floor(args.repo) if args.repo else None,
        )
        text = json.dumps(result.as_json(), sort_keys=True, indent=1, allow_nan=False)
        grid = matrix(cat, result) if args.matrix else None
    except (OSError, ValueError, catalogue.CatalogueError) as exc:
        problems = exc.problems if isinstance(exc, catalogue.CatalogueError) else [str(exc)]
        for problem in problems:
            print(f"plan: {problem}", file=sys.stderr)
        return 2
    if args.outputs:
        print(text, file=sys.stderr)
        print(f"full={'true' if result.full else 'false'}")
        print("jobs=" + json.dumps(list(result.jobs), separators=(",", ":")))
        if grid is not None:
            # `strategy.matrix` gets `include` alone: any other key would become a dimension.
            include = {"include": grid["include"]}
            print("matrix=" + json.dumps(include, separators=(",", ":"), sort_keys=True))
            print("keys=" + json.dumps(grid["keys"], separators=(",", ":")))
            # One more line per workflow job that runs part of it, each its own `include`.
            for name, entries in stage_jobs(grid).items():
                part = json.dumps({"include": entries}, separators=(",", ":"), sort_keys=True)
                print(f"matrix-{name}={part}")
        return 0
    if grid is not None:
        print(json.dumps(grid, sort_keys=True, indent=1))
        return 0
    print(text)
    return 0
