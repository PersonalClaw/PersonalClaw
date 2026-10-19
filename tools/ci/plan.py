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

Some jobs are planned whatever the diff: the rails (``catalogue.MERGE_NEEDS_JOB``), every
``security`` job and every ``live`` one. Each job ``after`` names comes with the job that
names it.

What the planner cannot read plans the whole tier: an empty diff (it proves nothing about what
to leave out), a path no input but the whole tree names, a path that is not a plain path inside
the repository, a list that cannot be read, and any error inside the planner. A forgotten path
therefore runs everything, never nothing.

Every plan reports the rails floor: the median minutes of the ``rails`` job over the passing
CI runs in a folder of telemetry history (``tools/ci/telemetry.py``), or "not measured" when
there is none, never a guessed number.

Standard library only, like every ``tools/ci`` module. Pathspecs are read as git reads them;
a test holds :func:`reaches` to ``git ls-files`` over the forms the catalogue accepts, and the
loader refuses a form git reads otherwise (``catalogue.pathspec_fault``).
"""

from __future__ import annotations

import argparse
import ast
import functools
import json
import math
import posixpath
import re
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import catalogue
from .catalogue import Catalogue, Job, Override

#: What :func:`rails_floor` says when no passing rails run is recorded.
NOT_MEASURED: dict[str, Any] = {"minutes": None, "runs": 0}

#: The workflow and job whose runs the rails floor is measured from.
FLOOR_WORKFLOW, FLOOR_JOB = "ci.yml", catalogue.MERGE_NEEDS_JOB

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
) -> Plan:
    """The plan for ``tier`` over the ``changed`` paths, with the area map's ``overrides``
    (:func:`catalogue.read_areas`). A tier with no job in the catalogue raises ValueError,
    since its plan would run nothing; anything else the planner cannot read plans the whole
    tier."""
    if tier not in catalogue.TIERS:
        raise ValueError(f"{tier!r} is not a tier: one of {', '.join(catalogue.TIERS)}")
    if not cat.tier(tier):
        raise ValueError(f"the {tier} tier has no job in the catalogue, so its plan runs nothing")
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


# ── the rails floor ──────────────────────────────────────────────────────────────────────────


def _measured(job: object) -> float | None:
    """A passing rails job's minutes, when they are a measurement: a finite positive number."""
    if not isinstance(job, dict) or job.get("name") != FLOOR_JOB:
        return None
    minutes = job.get("minutes")
    if job.get("conclusion") != "success" or isinstance(minutes, bool):
        return None
    if not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes <= 0:
        return None
    return float(minutes)


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
        minutes += [m for m in map(_measured, head["jobs"]) if m is not None]
    if not minutes:
        return dict(NOT_MEASURED)
    return {"minutes": round(statistics.median(minutes), 2), "runs": len(minutes)}


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


def main(argv: list[str]) -> int:
    """``python -m tools.ci plan <tier>``: the plan as JSON. Exit 0 with a plan, 2 when the
    tier, the catalogue or its overrides cannot be read (nothing is printed, so a caller plans
    everything)."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci plan", description=__doc__.splitlines()[0]
    )
    parser.add_argument("tier")
    parser.add_argument("--catalogue", type=Path, default=catalogue.REPO / catalogue.CATALOGUE)
    parser.add_argument(
        "--areas", type=Path, default=None, help="the overrides (areas.toml beside the catalogue)"
    )
    parser.add_argument("--paths", default=None, help="the changed paths, one per line (stdin)")
    parser.add_argument("-z", action="store_true", help="the paths are NUL-separated")
    parser.add_argument("--full", action="store_true", help="plan the whole tier")
    parser.add_argument("--history", type=Path, default=None, help="a folder of telemetry")
    parser.add_argument("--root", type=Path, default=catalogue.REPO, help="the repository root")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) and exc.code else 2
    try:
        source = str(args.catalogue)
        cat = catalogue.load(catalogue._text(args.catalogue, source), source)
        result = plan(
            cat,
            args.tier,
            _changed(args),
            overrides=read_beside(args.catalogue, cat, args.areas),
            full=args.full,
            history=args.history,
            root=args.root,
        )
        text = json.dumps(result.as_json(), sort_keys=True, indent=1, allow_nan=False)
    except (OSError, ValueError, catalogue.CatalogueError) as exc:
        problems = exc.problems if isinstance(exc, catalogue.CatalogueError) else [str(exc)]
        for problem in problems:
            print(f"plan: {problem}", file=sys.stderr)
        return 2
    print(text)
    return 0
