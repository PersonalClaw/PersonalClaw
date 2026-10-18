"""The catalogue of CI's jobs: ``tools/ci/catalogue.toml``, read into typed jobs and checked.

One entry per validation job says what the job runs, on what, for which tiers, what it reads
from outside the tree, and whether its result may stand for a later run of the same inputs.
Nothing runs from the catalogue yet: the workflows still run their own ``run:`` lines, and the
CI contract table's row "every catalogue command is the workflow's" holds the two to each other:
each entry's steps are its ci.yml jobs' lines, token for token and in order, and each line of
those jobs that validates something is one entry's step.

A command is a list of steps, each an argv run from the repository root (or from ``workdir``
under it), with its own ``env`` set on top of its job's. A value only the runner knows is spelled
with a placeholder from :data:`PLACEHOLDERS`; a ``$`` in an argument is refused, so no step reads
the environment behind the catalogue's back.

Reuse is one of ``never``, ``tree`` (the result stands for any run of the same tree and
external inputs) and ``ttl:<hours>``. :func:`parse_reuse` is the one parser of it. A security
or live job is never reused, by TTL or by tree, and a job that reads one of
:data:`LIVE_INPUTS` is live. The merge tier's security evidence is the suite's own run of its
security-marked modules on each platform, :data:`MERGE_SECURITY_JOBS`.

This module is imported by ``python -m tools.ci``, which ci.yml's shard planner runs on the
runner image's own ``python3`` before anything is installed, so it imports the standard library
only.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The catalogue, relative to the repository root.
CATALOGUE = "tools/ci/catalogue.toml"

#: The repository this module sits in.
REPO = Path(__file__).resolve().parents[2]

#: The tiers, in the order a change meets them. There is no batch tier.
TIERS: tuple[str, ...] = ("change", "merge", "breadth", "weekly", "release")

#: What each tier proves, and what it does not.
TIER_TEXT: dict[str, tuple[str, str]] = {
    "change": (
        "the check to run before a commit: the fast jobs, on this machine",
        "anything the merge tier adds: the full suite on both platforms, the browser specs, "
        "the dependency audit",
    ),
    "merge": (
        "what a pull request into main must pass: the tree lints, every rail holds, the suite "
        "(its security-marked modules included) passes on Linux and macOS, the dashboard "
        "builds and its tests pass, the wheel installs and serves, the browser specs pass when "
        "the frontend changed, and the dependency audit is clean",
        "the nightly breadth (other platforms, Pythons and coverage), the adversarial security "
        "corpus, and anything an outside feed publishes after the run",
    ),
    "breadth": (
        "the checks too slow or too wide for every pull request, run on main",
        "that a given pull request passed them before it merged",
    ),
    "weekly": ("the long checks, run once a week", "anything about a commit between two runs"),
    "release": ("what a release is cut from", "anything about an untagged commit"),
}

#: A job's security posture. ``security``: its result is security evidence (it runs
#: security-marked or fail-closed checks), so it is never reused, rerun or selected out.
SECURITY: tuple[str, ...] = ("ordinary", "security")

#: What a job installs before its steps run.
TOOLCHAINS = frozenset(
    {"uv", "uv-sync", "node", "npm-ci", "chromium", "tmux", "shellcheck", "dash"}
)

#: The caches a job restores.
CACHES = frozenset({"uv", "npm"})

#: What a job reads that the tree does not pin.
EXTERNAL_INPUTS = frozenset(
    {
        "apps-partner",  # the apps repository's checkout
        "advisory-feed",  # the vulnerability advisories published at the time of the run
        "package-index",  # an install the lock does not pin
        "browser-download",  # the browser build the install step fetches
        "shard-history",  # the telemetry the shard plan is drawn from
    }
)

#: The external inputs that are about the time of the run, not the tree: a job that reads one
#: is ``live``, so its evidence is never reused.
LIVE_INPUTS = frozenset({"advisory-feed"})

#: What a toolchain installs that a job's ``needs_*`` flag says it needs: the two agree.
NEEDS: dict[str, str] = {"needs_node": "node", "needs_browser": "chromium"}

#: The words a pathspec's ``:(...)`` magic may use.
PATHSPEC_MAGIC = frozenset({"exclude", "glob", "top", "literal", "icase"})

#: Each placeholder a step may use, with the workflow spellings of the value it stands for.
PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "runner_temp": ("$RUNNER_TEMP",),
    "shard": ("${{ matrix.shard }}",),
    "base": ("${{ steps.base.outputs.ref }}", "$BASE_SHA", "$BEFORE"),
}

#: The fields every entry declares.
REQUIRED: tuple[str, ...] = (
    "tiers",
    "inputs",
    "external_inputs",
    "toolchain",
    "cache",
    "security",
    "timeout_min",
)

_KEYS = frozenset(
    {
        "id",
        *REQUIRED,
        "platform",
        "python",
        "live",
        "reuse",
        "command",
        "script",
        "container",
        "env",
        "workers",
        "mem_gb",
        "junit",
        "shards",
        "needs_node",
        "needs_browser",
        "full_history",
        "after",
        "install_exemptions",
    }
)
_STEP_KEYS = frozenset({"argv", "workdir", "env"})
_EXEMPTION_KEYS = frozenset({"install", "reason"})
_ID = re.compile(r"[a-z][a-z0-9-]*")
_TTL = re.compile(r"ttl:([1-9][0-9]*)")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_PYTHON = re.compile(r"image|[0-9]+\.[0-9]+(\.[0-9]+)?")
_VARIABLE = re.compile(r"[A-Z_][A-Z0-9_]*")
#: Git's short pathspec magic: a colon, the signs for top (``/``) and exclude (``!``, ``^``),
#: and an optional colon that ends them.
_SHORT_MAGIC = re.compile(r"\A:[/!^]*:?")

#: The job that holds every rail. A merge tier without it is refused, so no rail is skipped.
MERGE_NEEDS_JOB = "rails"

#: The merge tier's security evidence: the suite's own run of its security-marked modules, on
#: each platform, each job with the runner family it runs on. A merge tier without either, with
#: either not security posture, or with either on the other platform, is refused, so neither run
#: is reused or lost and no other security job stands in for it.
MERGE_SECURITY_JOBS: dict[str, str] = {"python-suite": "ubuntu-", "python-suite-macos": "macos-"}


class CatalogueError(Exception):
    """The catalogue could not be read; :attr:`problems` names each job and field at fault."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


class Env(Mapping[str, str]):
    """A step's or a job's environment, read-only and hashable like the rest of a :class:`Job`."""

    __slots__ = ("_items",)

    def __init__(self, items: Mapping[str, str] | None = None) -> None:
        self._items: dict[str, str] = dict(items or {})

    def __getitem__(self, name: str) -> str:
        return self._items[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(frozenset(self._items.items()))

    def __repr__(self) -> str:
        return f"Env({self._items!r})"


@dataclass(frozen=True)
class Reuse:
    """When a job's result may stand for a later run of the same inputs."""

    kind: str  # never | tree | ttl
    hours: int | None = None

    def __str__(self) -> str:
        return f"ttl:{self.hours}" if self.kind == "ttl" else self.kind


NEVER = Reuse("never")


def parse_reuse(value: object) -> Reuse:
    """Read a reuse class: absent is ``never``; else ``never``, ``tree`` or ``ttl:<hours>``."""
    if value is None or value == "never":
        return NEVER
    if value == "tree":
        return Reuse("tree")
    match = _TTL.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(
            f"reuse is {value!r}: it is never, tree or ttl:<hours>, hours a positive whole number"
        )
    return Reuse("ttl", int(match.group(1)))


@dataclass(frozen=True)
class Step:
    """One argv, run from the repository root or from ``workdir`` under it, with ``env`` set on
    top of its job's."""

    argv: tuple[str, ...]
    workdir: str = ""
    env: Env = field(default_factory=Env)

    def placeholders(self) -> set[str]:
        return {
            name
            for value in (*self.argv, *self.env.values())
            for name in _PLACEHOLDER.findall(value)
        }

    def render(self) -> str:
        env = "".join(f"{name}={shlex.quote(value)} " for name, value in self.env.items())
        return f"{self.workdir or '.'}$ {env}{shlex.join(self.argv)}"


@dataclass(frozen=True)
class Exemption:
    """An install that may resolve outside the lock, and why."""

    install: str
    reason: str


@dataclass(frozen=True)
class Job:
    id: str
    tiers: tuple[str, ...]
    inputs: tuple[str, ...]
    external_inputs: tuple[str, ...]
    toolchain: tuple[str, ...]
    cache: tuple[str, ...]
    security: str
    timeout_min: int
    platform: str = "ubuntu-24.04"
    python: str | None = None
    live: bool = False
    reuse: Reuse = NEVER
    command: tuple[Step, ...] = ()
    script: str | None = None
    container: str | None = None
    env: Env = field(default_factory=Env)
    workers: int | str | None = None
    mem_gb: float | None = None
    junit: tuple[str, ...] = ()
    shards: int = 1
    needs_node: bool = False
    needs_browser: bool = False
    full_history: bool = False
    after: tuple[str, ...] = ()
    install_exemptions: tuple[Exemption, ...] = ()


@dataclass(frozen=True)
class Catalogue:
    jobs: tuple[Job, ...]

    def job(self, job_id: str) -> Job:
        for job in self.jobs:
            if job.id == job_id:
                return job
        raise KeyError(f"{job_id!r} is no job of the catalogue")

    def tier(self, name: str) -> tuple[Job, ...]:
        return tuple(j for j in self.jobs if name in j.tiers)


# ── reading ──────────────────────────────────────────────────────────────────────────────────


class _Fields:
    """One entry's raw table, read field by field; each fault is a problem naming the field."""

    def __init__(self, raw: Mapping[str, Any], label: str, problems: list[str]) -> None:
        self.raw = raw
        self.label = label
        self.problems = problems

    def fault(self, name: str, what: str) -> None:
        self.problems.append(f"{self.label}: {name} {what}")

    def strings(
        self, name: str, *, allowed: frozenset[str] | tuple[str, ...] | None = None
    ) -> tuple[str, ...]:
        value = self.raw.get(name, [])
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            self.fault(name, "is a list of non-empty strings")
            return ()
        if allowed is not None:
            for v in value:
                if v not in allowed:
                    self.fault(
                        name, f"names {v!r}, which is not one of {', '.join(sorted(allowed))}"
                    )
        if len(set(value)) != len(value):
            self.fault(name, "names a value twice")
        return tuple(value)

    def string(self, name: str, default: str | None = None) -> str | None:
        value = self.raw.get(name, default)
        if value is not None and (not isinstance(value, str) or not value):
            self.fault(name, "is a non-empty string")
            return default
        return value

    def flag(self, name: str) -> bool:
        value = self.raw.get(name, False)
        if not isinstance(value, bool):
            self.fault(name, "is true or false")
            return False
        return value

    def count(self, name: str, default: int | None) -> int | None:
        value = self.raw.get(name, default)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            self.fault(name, "is a positive whole number")
            return default
        return value


def _pathspec_fault(spec: str) -> str | None:
    """What is wrong with a pathspec, its magic read as git reads it, long form or short."""
    if spec.startswith(":("):
        end = spec.find(")")
        if end < 0:
            return "opens its magic with :( and never closes it"
        words = {w.strip() for w in spec[2:end].split(",")}
        unknown = sorted(words - PATHSPEC_MAGIC)
        if unknown:
            return f"uses unknown magic {', '.join(unknown)}"
        spec = spec[end + 1 :]
    else:
        spec = _SHORT_MAGIC.sub("", spec, count=1)
    if spec.startswith("/") or ".." in Path(spec).parts:
        return "leaves the repository"
    return None


def _value_faults(value: str) -> Iterator[str]:
    """What a value a step or job is given reads around the catalogue: ``$``, or a placeholder
    the catalogue does not have."""
    if "$" in value:
        yield f"is {value!r}, which reads the environment: use a placeholder"
    for name in _PLACEHOLDER.findall(value):
        if name not in PLACEHOLDERS:
            yield f"uses unknown placeholder {{{name}}}: one of " + ", ".join(
                f"{{{p}}}" for p in PLACEHOLDERS
            )


def _env(fields: _Fields, where: str, raw: object) -> Env:
    """An env table: variable names, each a string value that reads nothing around the catalogue."""
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in raw.items()
    ):
        fields.fault(where, "is a table of string values")
        return Env()
    for name, value in raw.items():
        if not _VARIABLE.fullmatch(name):
            fields.fault(where, f"name {name!r} is not a variable name ([A-Z_][A-Z0-9_]*)")
        for fault in _value_faults(value):
            fields.fault(f"{where} {name}", fault)
    return Env(raw)


def _steps(fields: _Fields) -> tuple[Step, ...]:
    raw = fields.raw.get("command")
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        fields.fault("command", "is a non-empty list of steps")
        return ()
    steps: list[Step] = []
    for n, step in enumerate(raw, 1):
        where = f"command step {n}"
        if not isinstance(step, dict):
            fields.fault(where, "is a table with argv and an optional workdir")
            continue
        for key in sorted(set(step) - _STEP_KEYS):
            fields.fault(where, f"has unknown key {key!r}")
        argv = step.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            fields.fault(where, "argv is a non-empty list of strings")
            continue
        if not argv[0]:
            fields.fault(where, "argv[0] is the program to run, so it is not empty")
        for arg in argv:
            if "$" in arg:
                fields.fault(where, f"argument {arg!r} reads the environment: use a placeholder")
            for name in _PLACEHOLDER.findall(arg):
                if name not in PLACEHOLDERS:
                    fields.fault(
                        where,
                        f"uses unknown placeholder {{{name}}}: one of "
                        + ", ".join(f"{{{p}}}" for p in PLACEHOLDERS),
                    )
        workdir = step.get("workdir", "")
        if (
            not isinstance(workdir, str)
            or workdir.startswith("/")
            or ".." in Path(workdir).parts
            or "$" in workdir
            or _PLACEHOLDER.search(workdir)
        ):
            fields.fault(where, "workdir is a path inside the repository")
            workdir = ""
        env = _env(fields, f"{where} env", step.get("env", {}))
        steps.append(Step(tuple(argv), workdir, env))
    return tuple(steps)


def _job(raw: object, n: int, problems: list[str]) -> Job | None:
    if not isinstance(raw, dict):
        problems.append(f"job {n}: is not a table")
        return None
    job_id = raw.get("id")
    if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
        problems.append(f"job {n}: id is lower-case letters, digits and dashes, starting a letter")
        job_id = None
    label = job_id or f"job {n}"
    before = len(problems)
    fields = _Fields(raw, label, problems)
    for key in sorted(set(raw) - _KEYS):
        problems.append(f"{label}: unknown key {key!r}")
    for name in REQUIRED:
        if name not in raw:
            problems.append(f"{label}: missing {name}")

    tiers = fields.strings("tiers", allowed=TIERS)
    if "tiers" in raw and not tiers:
        fields.fault("tiers", "names at least one tier")
    inputs = fields.strings("inputs")
    if "inputs" in raw and not inputs:
        fields.fault("inputs", "names at least one pathspec")
    for spec in inputs:
        fault = _pathspec_fault(spec)
        if fault:
            fields.fault("inputs", f"pathspec {spec!r} {fault}")
    external = fields.strings("external_inputs", allowed=EXTERNAL_INPUTS)
    toolchain = fields.strings("toolchain", allowed=TOOLCHAINS)
    cache = fields.strings("cache", allowed=CACHES)
    security = raw.get("security")
    if "security" in raw and security not in SECURITY:
        fields.fault("security", f"is one of {', '.join(SECURITY)}")
    timeout = fields.count("timeout_min", None) if "timeout_min" in raw else None
    platform = fields.string("platform", "ubuntu-24.04") or "ubuntu-24.04"
    python = fields.string("python")
    if python is not None and not _PYTHON.fullmatch(python):
        fields.fault("python", "is a version such as 3.12.15, or image for the runner's own")
    try:
        reuse = parse_reuse(raw.get("reuse"))
    except ValueError as exc:
        problems.append(f"{label}: {exc}")
        reuse = NEVER
    env = _env(fields, "env", raw.get("env", {}))
    workers = raw.get("workers")
    if workers is not None and workers != "auto" and fields.count("workers", None) is None:
        workers = None
    mem_gb = raw.get("mem_gb")
    if mem_gb is not None and (
        isinstance(mem_gb, bool) or not isinstance(mem_gb, (int, float)) or mem_gb <= 0
    ):
        fields.fault("mem_gb", "is a positive number")
        mem_gb = None
    exemptions: list[Exemption] = []
    raw_exemptions = raw.get("install_exemptions", [])
    if not isinstance(raw_exemptions, list):
        fields.fault("install_exemptions", "is a list of tables")
        raw_exemptions = []
    for item in raw_exemptions:
        if (
            not isinstance(item, dict)
            or set(item) != _EXEMPTION_KEYS
            or not all(isinstance(item[k], str) and item[k].strip() for k in _EXEMPTION_KEYS)
        ):
            fields.fault("install_exemptions", "entries each name an install and a reason")
            continue
        exemptions.append(Exemption(item["install"], item["reason"]))

    job = Job(
        id=job_id or "",
        tiers=tiers,
        inputs=inputs,
        external_inputs=external,
        toolchain=toolchain,
        cache=cache,
        security=security if isinstance(security, str) else "",
        timeout_min=timeout or 0,
        platform=platform,
        python=python,
        live=fields.flag("live"),
        reuse=reuse,
        command=_steps(fields),
        script=fields.string("script"),
        container=fields.string("container"),
        env=env,
        workers=workers,
        mem_gb=float(mem_gb) if mem_gb is not None else None,
        junit=fields.strings("junit"),
        shards=fields.count("shards", 1) or 1,
        needs_node=fields.flag("needs_node"),
        needs_browser=fields.flag("needs_browser"),
        full_history=fields.flag("full_history"),
        after=fields.strings("after"),
        install_exemptions=tuple(exemptions),
    )
    return job if len(problems) == before and job_id else None


def load(text: str, source: str = CATALOGUE) -> Catalogue:
    """Read a catalogue's text, or raise :class:`CatalogueError` naming every fault."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CatalogueError([f"{source} is not TOML: {exc}"]) from None
    problems: list[str] = [f"{source}: unknown table {key!r}" for key in data if key != "job"]
    raw_jobs = data.get("job")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise CatalogueError([*problems, f"{source} has no [[job]] entries"])
    ids = [raw.get("id") for raw in raw_jobs if isinstance(raw, dict)]
    for job_id in sorted({i for i in ids if isinstance(i, str) and ids.count(i) > 1}):
        problems.append(f"{job_id}: the id is used by {ids.count(job_id)} entries")
    jobs = [job for n, raw in enumerate(raw_jobs, 1) if (job := _job(raw, n, problems))]
    problems += validate(Catalogue(tuple(jobs)), complete=not problems)
    if problems:
        raise CatalogueError(list(dict.fromkeys(problems)))
    return Catalogue(tuple(jobs))


def validate(catalogue: Catalogue, complete: bool = True) -> list[str]:
    """The rules across fields and jobs. ``complete``: every entry was read, so the merge tier's
    contents are judged too (a catalogue with a broken entry is not also told it lacks it)."""
    problems: list[str] = []
    ids = [j.id for j in catalogue.jobs]
    for job_id in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{job_id}: the id is used by {ids.count(job_id)} entries")
    for job in catalogue.jobs:
        if job.reuse != NEVER and (job.security == "security" or job.live):
            what = "a security job's" if job.security == "security" else "a live job's"
            problems.append(f"{job.id}: reuse is {job.reuse}, but {what} evidence is never reused")
        for feed in sorted(LIVE_INPUTS & set(job.external_inputs)):
            if not job.live:
                problems.append(
                    f"{job.id}: reads {feed}, which changes with the time of the run and not with"
                    " the tree, so the job is live (live = true) and its evidence is never reused"
                )
        for flag, tool in NEEDS.items():
            needs = getattr(job, flag)
            if needs != (tool in job.toolchain):
                problems.append(
                    f"{job.id}: {flag} is {str(needs).lower()}, and its toolchain"
                    f" {'installs' if tool in job.toolchain else 'does not install'} {tool}"
                )
        has_script = job.script is not None or job.container is not None
        if bool(job.command) == has_script:
            problems.append(f"{job.id}: has either a command or a script and container, not both")
        elif has_script and (job.script is None or job.container is None):
            problems.append(f"{job.id}: a script runs in a container, so it names both")
        uses_shard = any("shard" in step.placeholders() for step in job.command)
        if uses_shard != (job.shards > 1):
            problems.append(
                f"{job.id}: shards is {job.shards}, and a sharded job's steps name {{shard}}"
                " (an unsharded job's do not)"
            )
        for other in job.after:
            if other == job.id or other not in ids:
                problems.append(f"{job.id}: after names {other!r}, which is not another job")
    if complete:
        merge = {j.id: j for j in catalogue.tier("merge")}
        if MERGE_NEEDS_JOB not in merge:
            problems.append(
                f"the merge tier lacks the {MERGE_NEEDS_JOB} job, so a rail can be skipped"
            )
        for job_id, family in MERGE_SECURITY_JOBS.items():
            suite = merge.get(job_id)
            if suite is None:
                problems.append(
                    f"the merge tier lacks {job_id}, so its security evidence can be skipped"
                )
                continue
            if suite.security != "security":
                problems.append(
                    f"{job_id}: security is {suite.security}, but its run of the security-marked"
                    " modules is the merge tier's security evidence"
                )
            if not suite.platform.startswith(family):
                problems.append(
                    f"{job_id}: platform is {suite.platform}, but the merge tier's security"
                    f" evidence is this suite's run on a {family}* runner"
                )
    return problems


def _text(path: Path, source: str) -> str:
    """The catalogue file's text; one that is not UTF-8 is refused by name."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise CatalogueError([f"{source} is not UTF-8"]) from None


def read(root: Path = REPO) -> Catalogue:
    """The catalogue at ``root``."""
    path = root / CATALOGUE
    source = str(path.relative_to(root))
    return load(_text(path, source), source)


# ── python -m tools.ci list | explain ───────────────────────────────────────────────────────


def _parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"python -m tools.ci {prog}", description=description)
    parser.add_argument(
        "--catalogue", type=Path, default=REPO / CATALOGUE, help="the catalogue to read"
    )
    return parser


def _read_for(path: Path) -> Catalogue | None:
    try:
        return load(_text(path, str(path)), str(path))
    except (OSError, CatalogueError) as exc:
        problems = exc.problems if isinstance(exc, CatalogueError) else [str(exc)]
        for problem in problems:
            print(problem, file=sys.stderr)
        return None


def list_main(argv: list[str]) -> int:
    """``python -m tools.ci list``: every job, its tiers, platform, cap and reuse."""
    args = _parser("list", "List the catalogue's jobs.").parse_args(argv)
    catalogue = _read_for(args.catalogue)
    if catalogue is None:
        return 1
    width = max(len(job.id) for job in catalogue.jobs)
    for job in catalogue.jobs:
        print(
            f"{job.id:<{width}}  {','.join(job.tiers):<14}  {job.platform:<12}"
            f"  {job.timeout_min:>3} min  reuse {job.reuse}"
        )
    return 0


def explain_main(argv: list[str]) -> int:
    """``python -m tools.ci explain <tier>``: what a tier proves, and the jobs that prove it."""
    parser = _parser("explain", "Say what a tier proves, and list its jobs' commands.")
    parser.add_argument("tier", help=f"one of {', '.join(TIERS)}")
    args = parser.parse_args(argv)
    if args.tier not in TIERS:
        print(f"{args.tier!r} is not a tier: one of {', '.join(TIERS)}", file=sys.stderr)
        return 2
    catalogue = _read_for(args.catalogue)
    if catalogue is None:
        return 1
    proves, does_not = TIER_TEXT[args.tier]
    jobs = catalogue.tier(args.tier)
    print(f"{args.tier}: proves {proves}.")
    print(f"It does not prove {does_not}.")
    print(f"{len(jobs)} job(s).")
    for job in jobs:
        python = f", python {job.python}" if job.python else ""
        print(
            f"\n{job.id} ({job.platform}{python}, {job.timeout_min} min cap, reuse {job.reuse},"
            f" security {job.security}{', live' if job.live else ''})"
        )
        for step in job.command:
            print(f"  {step.render()}")
        if job.script is not None:
            print(f"  script {job.script} in {job.container}")
    return 0
