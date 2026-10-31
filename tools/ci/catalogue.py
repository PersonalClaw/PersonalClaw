"""The catalogue of CI's jobs: ``tools/ci/catalogue.toml``, read into typed jobs and checked.

One entry per validation job says what the job runs, on what, for which tiers, what it reads
from outside the tree, and whether its result may stand for a later run of the same inputs.
``pr.yml`` and ``merge.yml`` run each change and merge job through ``run-job``. ``breadth.yml``
still runs its own ``run:`` lines, and the CI contract table's row "every catalogue command is
the workflow's" holds the two to each other: each breadth entry's steps are its breadth.yml
jobs' lines, token for token and in order, and each line of those jobs that validates something
is one entry's step.

A command is a list of steps, each an argv run from the repository root (or from ``workdir``
under it), with its own ``env`` set on top of its job's. A value only the runner knows is spelled
with a placeholder from :data:`PLACEHOLDERS`; a ``$`` in an argument is refused, so no step reads
the environment behind the catalogue's back. No env names a variable the runner owns
(:func:`tools.ci.run.runner_owns`), so an entry the loader reads is never refused as it runs.

Reuse is one of ``never``, ``tree`` (the result stands for any run of the same tree and
external inputs) and ``ttl:<hours>``. :func:`parse_reuse` is the one parser of it. A security
or live job is never reused, by TTL or by tree, and a job that reads one of
:data:`LIVE_INPUTS` is live. Nor is a job that names ``{run_number}``, another value every run,
in a step or in its own env (:meth:`Job.placeholders`, where run-job fills it). The merge tier's
security evidence, :data:`MERGE_SECURITY_JOBS`, is the suite's own run of its security-marked
modules on each platform and ``security``, the one run of the security corpus.

A pull request's catalogue is read with main's as its reference (:func:`against`): a security or
live job main has stays, with its posture and its tiers, until main's own copy marks it
``retiring = "#<issue>"``, so a removal takes two merges and the second only drops the entry.
A ``nightly`` mark, which lets a live job out of a plan its inputs do not reach, likewise counts
only once main's copy carries it (:func:`trusted_marks`).

This module is imported by ``python -m tools.ci``, which merge.yml's shard planner runs on the
runner image's own ``python3`` before anything is installed, so it imports the standard library
only.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import run

#: The catalogue, relative to the repository root.
CATALOGUE = "tools/ci/catalogue.toml"

#: The area map's explicit overrides, beside the catalogue whose jobs they name.
AREAS = "tools/ci/areas.toml"

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
        "(its security-marked modules included) passes on Linux and macOS, the security corpus "
        "passes on Linux, the dashboard builds and its tests pass, the wheel installs and serves, "
        "the browser specs pass when the frontend changed, and the dependency audit is clean",
        "the nightly breadth (other platforms, Pythons and coverage, and the security corpus on "
        "macOS), and anything an outside feed publishes after the run",
    ),
    "breadth": (
        "the checks too slow or too wide for every pull request, run on main",
        "that a given pull request passed them before it merged",
    ),
    "weekly": ("the long checks, run once a week", "anything about a commit between two runs"),
    "release": ("what a release is cut from", "anything about an untagged commit"),
}

#: A job's security posture. ``security``: its result is security evidence (it runs
#: security-marked or fail-closed checks), so it is never selected out, the job's result is never
#: rerun or reused, and a security-marked test is never rerun; an ordinary failed test inside it
#: gets one diagnostic rerun (``tools/ci/jobs.py``'s ``diagnose``). On a pull request's run the
#: verdict's flake rule reads that same attempt's own rerun, and may pass ordinary flakes only:
#: a security-marked failure is never rerun, so it stays red, and a job is passed only when every
#: security-marked test in it passed on attempt 1. No result is reused across heads.
SECURITY: tuple[str, ...] = ("ordinary", "security")

#: What a job installs before its steps run.
TOOLCHAINS = frozenset(
    {
        "uv",
        "uv-sync",
        "node",
        "npm-ci",
        "chromium",
        "electron",
        "tmux",
        "shellcheck",
        "dash",
        # ffmpeg and ffprobe, from the runner's package manager (the setup action): the tests that
        # build and read a video skip without them (#3918).
        "ffmpeg",
        "docker",
        # The user namespaces a GitHub-hosted Linux runner withholds, lifted for the job's own run
        # (tools/ci/allow_userns.sh); a job that names it cannot run anywhere else (#3918).
        "userns",
    }
)

#: The caches a job restores.
CACHES = frozenset({"uv", "npm"})

#: What a job reads that the tree does not pin, by kind:
#:
#: - ``revision``: a partner repository's checkout, whose commit is part of the result's identity,
#:   so a result for one partner commit never stands for another.
#: - ``plan-only``: it decides which shard runs a file, never what is tested.
#: - ``unpinned``: what it fetches can change while the tree does not, so a job that reads one is
#:   never reused by tree (a TTL still bounds how long its result stands).
#: - ``live``: it is about the time of the run, so a job that reads one is live and never reused.
EXTERNAL_INPUT_KINDS: dict[str, str] = {
    "apps-partner": "revision",  # the apps repository's checkout
    "advisory-feed": "live",  # the vulnerability advisories published at the time of the run
    "package-index": "unpinned",  # an install the lock does not pin
    "browser-download": "unpinned",  # the browser build the install step fetches
    "shard-history": "plan-only",  # the telemetry the shard plan is drawn from
}
EXTERNAL_INPUTS = frozenset(EXTERNAL_INPUT_KINDS)

#: The external inputs that are about the time of the run, not the tree: a job that reads one
#: is ``live``, so its evidence is never reused.
LIVE_INPUTS = frozenset(k for k, kind in EXTERNAL_INPUT_KINDS.items() if kind == "live")

#: The external inputs a tree does not pin: a job that reads one is never reused by tree.
UNPINNED_INPUTS = frozenset(k for k, kind in EXTERNAL_INPUT_KINDS.items() if kind == "unpinned")

#: The partner checkouts: a job that reads one names its revision in its record, and the verdict
#: holds the record to exactly the ones its job reads.
REVISION_INPUTS = frozenset(k for k, kind in EXTERNAL_INPUT_KINDS.items() if kind == "revision")

#: The installs that are a job's toolchain, never one of its steps, each by the start of its
#: argv and the toolchain word that names it. Whatever runs a job installs its toolchain first.
INSTALLS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("uv", "sync"), "uv-sync"),
    (("npm", "ci"), "npm-ci"),
    (("npx", "playwright", "install"), "chromium"),
    # Electron's binary, which `npm ci` does not download (tools/ci/install_electron.sh, which also
    # gives Chromium's setuid sandbox helper its bit on a Linux runner).
    (("sh", "tools/ci/install_electron.sh"), "electron"),
    # The user namespaces a GitHub-hosted Linux runner withholds (tools/ci/allow_userns.sh): a
    # step that ran it would change a kernel setting wherever the job runs, a machine of your own
    # included, so it is only ever an install, which run-job runs on such a runner alone.
    (("sh", "tools/ci/allow_userns.sh"), "userns"),
)

#: What a toolchain installs that a job's ``needs_*`` flag says it needs: the two agree.
NEEDS: dict[str, str] = {"needs_node": "node", "needs_browser": "chromium"}

#: The words a pathspec's ``:(...)`` magic may use.
PATHSPEC_MAGIC = frozenset({"exclude", "glob", "top", "literal", "icase"})

#: Each placeholder a step may use, with the workflow spellings of the value it stands for.
PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "runner_temp": ("$RUNNER_TEMP",),
    "shard": ("${{ matrix.shard }}",),
    "base": ("${{ steps.base.outputs.ref }}", "$BASE_SHA", "$BEFORE"),
    # The uv-managed interpreter of the job's own Python, as a step that must not install the
    # project first runs it: breadth's shard list and evidence steps, before deciding to run.
    "python": ('"$(uv python find "$PY")"',),
    # The run's number, which every attempt of the run keeps: the randomized-order leg's seeds
    # are drawn from it. It is another value every run, so a job that names it is never reused.
    "run_number": ("${{ github.run_number }}",),
}

#: The job-level variables a placeholder's workflow spelling reads, and nothing else: ``PY``
#: names the Python ``{python}`` finds, which is the entry's ``python``.
PLACEHOLDER_ENV: frozenset[str] = frozenset({"PY"})

#: The smallest cap a job may have: a local run stops a job a minute before its cap, so CI's
#: own timeout never decides it, and a one-minute cap would leave it no time at all.
MIN_TIMEOUT_MIN = 2

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
        "workers_env",
        "mem_gb",
        "junit",
        "shards",
        "needs_node",
        "needs_browser",
        "after",
        "install_exemptions",
        "retiring",
        "nightly",
        "lint_feedback",
        "prepush",
    }
)
_STEP_KEYS = frozenset({"argv", "workdir", "env"})
_EXEMPTION_KEYS = frozenset({"install", "reason"})
_ID = re.compile(r"[a-z][a-z0-9-]*")
_TTL = re.compile(r"ttl:([1-9][0-9]*)")
#: ``retiring``: the issue that retires the job, ``#<number>``.
_RETIRING = re.compile(r"#[1-9][0-9]*")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_PYTHON = re.compile(r"image|[0-9]+\.[0-9]+(\.[0-9]+)?")
_VARIABLE = re.compile(r"[A-Z_][A-Z0-9_]*")
#: Git's short pathspec magic: a colon, the signs for top (``/``) and exclude (``!``, ``^``),
#: and an optional colon that ends them.
SHORT_MAGIC = re.compile(r"\A:[/!^]*:?")

#: The catalogue's tables besides its jobs, and what each may hold.
_TIER_TABLES = frozenset({"merge"})
_TIER_KEYS = frozenset({"jobs", "every_push"})
#: What an ``[[override]]`` of the areas file may hold.
_OVERRIDE_KEYS = frozenset({"paths", "jobs", "whole_tier", "reason"})

#: The job that holds every rail. A merge tier without it is refused, so no rail is skipped.
MERGE_NEEDS_JOB = "rails"

#: The job that runs ``rails check``, which holds every rail to exactly one leg of a rails job
#: that runs in legs (``rails_census.leg_violations``). A merge tier whose rails run in legs
#: without it is refused, and every plan holds it, so no rail can sit in no leg unseen.
RAILS_CHECKS_JOB = "rails-checks"

#: The merge tier's security evidence: the suite's own run of its security-marked modules, on
#: each platform, and ``security``, the one run of the security corpus (``tests/security`` and the
#: supply-chain scanner's tests), which no suite shard collects. Each job with the runner family it
#: runs on. A merge tier without one, with one not security posture, or with one on another
#: platform, is refused, so no run is reused or lost and no other security job stands in for it.
MERGE_SECURITY_JOBS: dict[str, str] = {
    "python-suite": "ubuntu-",
    "python-suite-macos": "macos-",
    "security": "ubuntu-",
}

#: The jobs a push to main runs whatever the tree check answers (the owner's rule, 2026-10-19):
#: the security corpus, whose evidence is never reused, and apps-contract, whose partner may have
#: moved since the pull request's run. ``[tier.merge] every_push`` must list each one the merge
#: tier runs, so a push whose tree a pull request's run tested still runs them.
EVERY_PUSH_JOBS: tuple[str, ...] = ("security", "apps-contract")


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
    #: The scripts a job runs in its container, in order: one or more repository paths.
    script: tuple[str, ...] | None = None
    container: str | None = None
    env: Env = field(default_factory=Env)
    workers: int | str | None = None
    workers_env: str | None = None
    mem_gb: float | None = None
    junit: tuple[str, ...] = ()
    shards: int = 1
    needs_node: bool = False
    needs_browser: bool = False
    after: tuple[str, ...] = ()
    install_exemptions: tuple[Exemption, ...] = ()
    #: The issue that retires the job (``#<number>``). The job is still the catalogue's and
    #: still runs; once main's catalogue marks it, a pull request may drop it (:func:`against`).
    retiring: str | None = None
    #: A live job the plan selects by its inputs, like any other: the breadth job this names runs
    #: the same steps every night, whatever changed. A live job without it is in every plan. Read
    #: against main's catalogue, a mark main's copy of the job lacks counts for nothing
    #: (:func:`trusted_marks`), so the merge that adds one still plans the job.
    nightly: str | None = None
    #: A failed step's output is the pull request's lint remediation: run-job leaves it for the
    #: stage job to upload as ``lint-feedback``, which ``pr-feedback.yml`` posts. One job, of the
    #: change tier, which ``pr.yml`` runs on every pull request.
    lint_feedback: bool = False
    #: The pre-push hook runs it, as a job of the change tier (``run change --pre-push``,
    #: ``scripts/run_prepush.sh``). ``false`` leaves it to CI, for a job too slow for a push:
    #: the contract holds each change-tier job that runs a test suite to ``false``.
    prepush: bool = True

    def placeholders(self) -> set[str]:
        """Every placeholder the job names: in a step's argv or env, or in its own env, which
        each step runs with."""
        return {n for step in self.command for n in step.placeholders()} | {
            n for value in self.env.values() for n in _PLACEHOLDER.findall(value)
        }


@dataclass(frozen=True)
class Override:
    """An ``[[override]]`` of ``tools/ci/areas.toml``: jobs a path schedules beyond what the
    jobs' declared inputs say, and why. ``whole_tier``: the path plans the whole tier. An
    override only adds: it never names a path known, so it never leaves a job out."""

    paths: tuple[str, ...]
    jobs: tuple[str, ...]
    whole_tier: bool
    reason: str


@dataclass(frozen=True)
class Catalogue:
    jobs: tuple[Job, ...]
    #: ``[tier.merge] jobs``: the jobs the merge tier must run, held against each job's tiers.
    merge_jobs: tuple[str, ...] = ()
    #: ``[tier.merge] every_push``: the merge-tier jobs a push to main runs whatever the tree
    #: check answers (:data:`EVERY_PUSH_JOBS` among them); None when the catalogue names none.
    every_push: tuple[str, ...] | None = None

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

    def flag(self, name: str, default: bool = False) -> bool:
        value = self.raw.get(name, default)
        if not isinstance(value, bool):
            self.fault(name, "is true or false")
            return default
        return value

    def count(self, name: str, default: int | None) -> int | None:
        value = self.raw.get(name, default)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            self.fault(name, "is a positive whole number")
            return default
        return value


def pathspec_fault(spec: str) -> str | None:
    """What is wrong with a pathspec, its magic read as git reads it, long form or short. Its
    path is in git's one normal form, since git reads ``.`` as the tree but ``:(top).`` as
    nothing; and a wildcard is one ``tools/ci/plan.py`` reads as git does."""
    words: set[str] = set()
    if spec.startswith(":("):
        end = spec.find(")")
        if end < 0:
            return "opens its magic with :( and never closes it"
        words = {w.strip() for w in spec[2:end].split(",")}
        unknown = sorted(words - PATHSPEC_MAGIC)
        if unknown:
            return f"uses unknown magic {', '.join(unknown)}"
        if {"literal", "glob"} <= words:
            return "uses literal and glob together, which git refuses"
        spec = spec[end + 1 :]
        exclude = "exclude" in words
    else:
        short = SHORT_MAGIC.match(spec)
        exclude = short is not None and bool({"!", "^"} & set(short.group()))
        spec = SHORT_MAGIC.sub("", spec, count=1)
    if exclude and not spec.strip("*?/"):
        # git 2.54 excludes every path by `src/ :!` or `src/ :(exclude,glob)**`, and 2.55 none.
        return (
            "excludes every path, which git versions read otherwise: an exclude names the"
            " paths it skips"
        )
    parts = spec.split("/")
    if spec.startswith("/") or ".." in parts:
        return "leaves the repository"
    if spec and (any(part in ("", ".") for part in parts[:-1]) or parts[-1] == "."):
        return "is not in git's normal form (an empty or . part): name the path itself"
    return None if "literal" in words else _wildcard_fault(spec)


def _wildcard_fault(spec: str) -> str | None:
    """A wildcard git reads otherwise than the planner: an escape, or a class it cannot read."""
    if "\\" in spec:
        return "has a backslash, which git reads as an escape: use :(literal)"
    i = spec.find("[")
    while i >= 0:
        j = i + 1 + (spec[i + 1 : i + 2] in ("!", "^"))
        j += spec[j : j + 1] == "]"
        end = spec.find("]", j)
        if end < 0:
            return "opens a [ it never closes: use :(literal)"
        if "/" in spec[i:end]:
            return "has a / in a class, which never matches a folder's /"
        if "[:" in spec[i + 1 : end]:
            return "uses a [:name:] character class: list the characters"
        i = spec.find("[", end + 1)
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


#: What ``run-job`` alone gives a step: from its own reading of the runner, the marker that says
#: it runs on a GitHub-hosted Linux runner, given only to the commands that run Electron's setuid
#: sandbox helper (``tools/ci/jobs.py`` ``SANDBOX_HELPER_ENV``); and the job's own Python, its
#: venv's interpreter (``JOB_PYTHON_ENV``). No entry may set either.
RUN_JOB_SETS = frozenset({"PCLAW_SANDBOX_HELPER_HOST", "PERSONALCLAW_PY"})


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
        elif run.runner_owns(name):
            fields.fault(where, f"name {name!r} is the runner's: a job may not set it")
        elif name in RUN_JOB_SETS:
            fields.fault(where, f"name {name!r} is run-job's: a job may not set it")
        for fault in _value_faults(value):
            fields.fault(f"{where} {name}", fault)
    return Env(raw)


def _scripts(fields: _Fields) -> tuple[str, ...] | None:
    """``script``: one repository path, or a non-empty list of them, each run in turn in the
    job's container as ``sh <path>``. A path is inside the repository, in its one normal form
    (no empty or ``.`` part, no ``./`` in front), with no whitespace, so one script has one
    spelling and a repeat cannot hide behind a second."""
    raw = fields.raw.get("script")
    if raw is None:
        return None
    paths = [raw] if isinstance(raw, str) else raw
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        fields.fault("script", "is a path, or a non-empty list of paths")
        return None
    for path in paths:
        parts = path.split("/")
        if re.search(r"\s", path):
            fields.fault("script", f"names {path!r}, which holds whitespace")
        elif (
            path.startswith(("/", "~")) or ".." in parts or "$" in path or _PLACEHOLDER.search(path)
        ):
            fields.fault("script", f"names {path!r}, which is not a path inside the repository")
        elif any(part in ("", ".") for part in parts):
            fields.fault(
                "script",
                f"names {path!r}, which is not in the repository's normal form (an empty or ."
                " part): name the path itself",
            )
    if len(set(paths)) != len(paths):
        fields.fault("script", "names a path twice")
    return tuple(paths)


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
        for prefix, tool in INSTALLS:
            if tuple(argv[: len(prefix)]) == prefix:
                fields.fault(
                    where,
                    f"is an install ({shlex.join(argv)}): it is toolchain {tool!r}, never a step",
                )
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
        fault = pathspec_fault(spec)
        if fault:
            fields.fault("inputs", f"pathspec {spec!r} {fault}")
    external = fields.strings("external_inputs", allowed=EXTERNAL_INPUTS)
    toolchain = fields.strings("toolchain", allowed=TOOLCHAINS)
    cache = fields.strings("cache", allowed=CACHES)
    security = raw.get("security")
    if "security" in raw and security not in SECURITY:
        fields.fault("security", f"is one of {', '.join(SECURITY)}")
    timeout = fields.count("timeout_min", None) if "timeout_min" in raw else None
    if timeout is not None and timeout < MIN_TIMEOUT_MIN:
        fields.fault(
            "timeout_min",
            f"is {MIN_TIMEOUT_MIN} or more: a local run stops a job a minute before its cap",
        )
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
    # 0 is pytest's `-n0`: one process, no workers, as a collection that must not split asks.
    if workers == 0 and not isinstance(workers, bool):
        pass
    elif workers is not None and workers != "auto" and fields.count("workers", None) is None:
        workers = None
    workers_env = fields.string("workers_env")
    if workers_env is not None and not _VARIABLE.fullmatch(workers_env):
        fields.fault("workers_env", "is an environment variable's name")
        workers_env = None
    elif workers_env is not None and run.runner_owns(workers_env):
        fields.fault("workers_env", f"{workers_env!r} is the runner's: a job may not set it")
        workers_env = None
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
    retiring = fields.string("retiring")
    if retiring is not None and not _RETIRING.fullmatch(retiring):
        fields.fault("retiring", "names the issue that retires the job, as #<number>")
        retiring = None

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
        script=_scripts(fields),
        container=fields.string("container"),
        env=env,
        workers=workers,
        workers_env=workers_env,
        mem_gb=float(mem_gb) if mem_gb is not None else None,
        junit=fields.strings("junit"),
        shards=fields.count("shards", 1) or 1,
        needs_node=fields.flag("needs_node"),
        needs_browser=fields.flag("needs_browser"),
        lint_feedback=fields.flag("lint_feedback"),
        prepush=fields.flag("prepush", True),
        after=fields.strings("after"),
        install_exemptions=tuple(exemptions),
        retiring=retiring,
        nightly=fields.string("nightly"),
    )
    return job if len(problems) == before and job_id else None


def load(text: str, source: str = CATALOGUE, reference: Catalogue | None = None) -> Catalogue:
    """Read a catalogue's text, or raise :class:`CatalogueError` naming every fault.

    ``reference``: main's catalogue, when this one is a pull request's. Its security and live
    jobs are then held as :func:`against` says, so a pull request's catalogue that drops one is
    refused here, by the loader, wherever main's copy reads it."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CatalogueError([f"{source} is not TOML: {exc}"]) from None
    problems: list[str] = [
        f"{source}: unknown table {key!r}" for key in data if key not in ("job", "tier")
    ]
    merge_jobs, every_push = _tier_merge(data.get("tier"), source, problems)
    raw_jobs = data.get("job")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise CatalogueError([*problems, f"{source} has no [[job]] entries"])
    ids = [raw.get("id") for raw in raw_jobs if isinstance(raw, dict)]
    for job_id in sorted({i for i in ids if isinstance(i, str) and ids.count(i) > 1}):
        problems.append(f"{job_id}: the id is used by {ids.count(job_id)} entries")
    jobs = [job for n, raw in enumerate(raw_jobs, 1) if (job := _job(raw, n, problems))]
    cat = Catalogue(tuple(jobs), merge_jobs, every_push)
    complete = not problems
    problems += validate(cat, complete=complete)
    if reference is not None and complete:
        # Only a catalogue whose every entry was read: a broken entry is not also a dropped one.
        problems += against(cat, reference)
    if problems:
        raise CatalogueError(list(dict.fromkeys(problems)))
    return cat if reference is None else trusted_marks(cat, reference)


def load_areas(text: str, cat: Catalogue, source: str = AREAS) -> tuple[Override, ...]:
    """Read the area map's overrides against ``cat``, or raise :class:`CatalogueError` naming
    every fault: each ``[[override]]`` names pathspecs, the catalogue jobs it adds or
    ``whole_tier``, and a reason, and nothing else is in the file."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CatalogueError([f"{source} is not TOML: {exc}"]) from None
    problems = [f"{source}: unknown table {key!r}" for key in data if key != "override"]
    overrides = _overrides(data.get("override", []), {j.id for j in cat.jobs}, source, problems)
    if problems:
        raise CatalogueError(list(dict.fromkeys(problems)))
    return overrides


def read_areas(root: Path, cat: Catalogue) -> tuple[Override, ...]:
    """The overrides at ``root``, read against ``cat``."""
    path = root / AREAS
    source = str(path.relative_to(root))
    return load_areas(_text(path, source), cat, source)


def _overrides(
    raw: object, ids: set[str], source: str, problems: list[str]
) -> tuple[Override, ...]:
    if not isinstance(raw, list):
        problems.append(f"{source}: override is a list of [[override]] tables")
        return ()
    out: list[Override] = []
    for n, item in enumerate(raw, 1):
        label = f"{source}: override {n}"
        if not isinstance(item, dict):
            problems.append(f"{label}: is not a table")
            continue
        before = len(problems)
        fields = _Fields(item, label, problems)
        for key in sorted(set(item) - _OVERRIDE_KEYS):
            problems.append(f"{label}: unknown key {key!r}")
        paths = fields.strings("paths")
        if not paths:
            fields.fault("paths", "names at least one pathspec")
        for spec in paths:
            fault = pathspec_fault(spec)
            if fault:
                fields.fault("paths", f"pathspec {spec!r} {fault}")
        jobs = fields.strings("jobs")
        for job_id in jobs:
            if job_id not in ids:
                fields.fault("jobs", f"names {job_id}, which is no job of the catalogue")
        whole = fields.flag("whole_tier")
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            fields.fault("reason", "is a non-empty string")
            reason = ""
        if not jobs and not whole:
            problems.append(f"{label}: adds no job and is not whole_tier")
        if len(problems) == before:
            out.append(Override(paths, jobs, whole, reason))
    return tuple(out)


def _tier_merge(
    raw: object, source: str, problems: list[str]
) -> tuple[tuple[str, ...], tuple[str, ...] | None]:
    """``[tier.merge] jobs``, the merge tier's job list, and ``every_push``, the ones a push to
    main runs whatever the tree check answers; a fault is a problem naming it."""
    if raw is None:
        return (), None
    if not isinstance(raw, dict):
        problems.append(f"{source}: tier is a table of tiers")
        return (), None
    for key in sorted(set(raw) - _TIER_TABLES):
        problems.append(f"{source}: unknown table 'tier.{key}'")
    merge = raw.get("merge")
    if not isinstance(merge, dict):
        problems.append(f"{source}: tier.merge is a table holding jobs")
        return (), None
    for key in sorted(set(merge) - _TIER_KEYS):
        problems.append(f"{source}: tier.merge has unknown key {key!r}")
    every: tuple[str, ...] | None = None
    if "every_push" in merge:
        listed = merge["every_push"]
        if not isinstance(listed, list) or not all(isinstance(j, str) and j for j in listed):
            problems.append(f"{source}: tier.merge every_push is a list of job ids")
        else:
            if len(set(listed)) != len(listed):
                problems.append(f"{source}: tier.merge every_push names a job twice")
            every = tuple(listed)
    jobs = merge.get("jobs")
    if not isinstance(jobs, list) or not jobs or not all(isinstance(j, str) and j for j in jobs):
        problems.append(f"{source}: tier.merge jobs is a non-empty list of job ids")
        return (), every
    if len(set(jobs)) != len(jobs):
        problems.append(f"{source}: tier.merge jobs names a job twice")
    return tuple(jobs), every


#: pytest's own spellings of "collect, run nothing". No other word counts.
COLLECT_ONLY_FLAGS = frozenset({"--collect-only", "--co"})
#: pytest options whose next word is their value, never a flag of its own.
_PYTEST_VALUED = frozenset(
    {
        "-k",
        "-m",
        "-p",
        "-o",
        "-c",
        "-W",
        "-n",
        "--numprocesses",
        "--rootdir",
        "--junitxml",
        "--junit-xml",
        "--deselect",
        "--ignore",
        "--ignore-glob",
        "--basetemp",
        "--confcutdir",
        "--override-ini",
        "--shard-list",
        "--timeout",
        "--dist",
        "--maxfail",
    }
)


def _is_python(word: str) -> bool:
    return bool(re.fullmatch(r"(?:\S*/)?python(?:\d+(?:\.\d+)*)?", word))


def pytest_args(words: Sequence[str]) -> list[str] | None:
    """The words pytest itself is given, when *words* is one pytest command: ``pytest``,
    ``python -m pytest`` or ``uv run [flags] [python -m] pytest``, after any ``NAME=value``
    assignments. None for any other command, so a ``pytest`` that is another program's argument
    (``echo pytest --co``, ``sh -c "pytest --co"``) is no pytest command."""
    words = list(words)
    while words and re.fullmatch(r"[A-Za-z_]\w*=\S*", words[0]):
        words = words[1:]
    if words[:2] == ["uv", "run"]:
        words = words[2:]
        while words and words[0].startswith("-"):
            words = words[1 + (words[0] in ("--with", "--python", "--project", "--directory")) :]
    if len(words) >= 3 and _is_python(words[0]) and words[1] == "-m":
        words = words[2:]
    if not words or words[0] != "pytest":
        return None
    return words[1:]


def collect_only(words: Sequence[str]) -> bool:
    """Whether *words* is a pytest command that only collects: ``--collect-only`` or ``--co``
    as a word of the pytest command itself. It runs no test, so it is never security evidence.
    A flag inside a quoted string or another tool's arguments is not one of its words."""
    args = pytest_args(words)
    if args is None:
        return False
    for i, word in enumerate(args):
        if word in COLLECT_ONLY_FLAGS and (i == 0 or args[i - 1] not in _PYTEST_VALUED):
            return True
    return False


def pytest_workers(step: Step) -> list[str]:
    """Each worker count a pytest step passes (``-n N``, ``-nN``, ``--numprocesses[=]N``)."""
    if "pytest" not in step.argv:
        return []
    args = list(step.argv[step.argv.index("pytest") + 1 :])
    found = []
    for i, arg in enumerate(args):
        if arg in ("-n", "--numprocesses"):
            found.append(args[i + 1] if i + 1 < len(args) else "")
        elif arg.startswith("--numprocesses="):
            found.append(arg.partition("=")[2])
        elif arg.startswith("-n") and len(arg) > 2 and not arg.startswith("--"):
            found.append(arg[2:])
    return found


def validate(catalogue: Catalogue, complete: bool = True) -> list[str]:
    """The rules across fields and jobs. ``complete``: every entry was read, so the merge tier's
    contents are judged too (a catalogue with a broken entry is not also told it lacks it)."""
    problems: list[str] = []
    ids = [j.id for j in catalogue.jobs]
    for job_id in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{job_id}: the id is used by {ids.count(job_id)} entries")
    feedback = [j.id for j in catalogue.jobs if j.lint_feedback]
    if len(feedback) > 1:
        problems.append(
            f"lint_feedback is set on {', '.join(feedback)}: one job's failed step is a pull"
            " request's lint remediation, uploaded under one name"
        )
    for job in catalogue.jobs:
        if job.lint_feedback and "change" not in job.tiers:
            problems.append(
                f"{job.id}: lint_feedback is set, but the job is not of the change tier, which"
                " pr.yml runs on every pull request"
            )
    for job in catalogue.jobs:
        if job.reuse != NEVER and (job.security == "security" or job.live):
            what = "a security job's" if job.security == "security" else "a live job's"
            problems.append(f"{job.id}: reuse is {job.reuse}, but {what} evidence is never reused")
        if job.reuse != NEVER and "run_number" in job.placeholders():
            problems.append(
                f"{job.id}: reuse is {job.reuse}, but its steps name {{run_number}}, another"
                " value every run, so its evidence is never reused"
            )
        if job.reuse.kind == "tree":
            for unpinned in sorted(UNPINNED_INPUTS & set(job.external_inputs)):
                problems.append(
                    f"{job.id}: reuse is tree, but it reads {unpinned}, which the tree does not"
                    " pin, so a result for one fetch could stand for another"
                )
        _workers_faults(job, problems)
        tests = [step for step in job.command if pytest_args(step.argv) is not None]
        if job.security == "security" and tests and all(collect_only(s.argv) for s in tests):
            problems.append(
                f"{job.id}: security posture, and every pytest step only collects"
                " (--collect-only): it runs no test, so it is no security evidence"
            )
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
        if job.nightly is not None:
            problems += _nightly_faults(catalogue, job)
    if complete:
        merge = {j.id: j for j in catalogue.tier("merge")}
        if not catalogue.merge_jobs:
            problems.append(
                "the catalogue has no [tier.merge] jobs list, so the merge tier's jobs are"
                " held by no list"
            )
        else:
            for job_id in catalogue.merge_jobs:
                if job_id not in merge:
                    problems.append(
                        f"{job_id}: [tier.merge] lists it, and it is no job whose tiers claim merge"
                    )
            for job_id in merge:
                if job_id not in catalogue.merge_jobs:
                    problems.append(
                        f"{job_id}: its tiers claim merge, and [tier.merge] does not list it"
                    )
        if MERGE_NEEDS_JOB not in merge:
            problems.append(
                f"the merge tier lacks the {MERGE_NEEDS_JOB} job, so a rail can be skipped"
            )
        elif merge[MERGE_NEEDS_JOB].shards > 1 and RAILS_CHECKS_JOB not in merge:
            problems.append(
                f"the merge tier runs the {MERGE_NEEDS_JOB} job in legs and lacks"
                f" {RAILS_CHECKS_JOB}, so a rail can be in no leg unseen"
            )
        if catalogue.every_push is not None:
            for job_id in catalogue.every_push:
                if job_id not in merge:
                    problems.append(
                        f"every_push lists {job_id}, which is no job the merge tier runs"
                    )
            for job_id in EVERY_PUSH_JOBS:
                if job_id in merge and job_id not in catalogue.every_push:
                    problems.append(
                        f"every_push leaves out {job_id}, so a push whose tree a pull request's"
                        " run tested would skip it"
                    )
        for job_id, family in MERGE_SECURITY_JOBS.items():
            evidence = merge.get(job_id)
            if evidence is None:
                problems.append(
                    f"the merge tier lacks {job_id}, so its security evidence can be skipped"
                )
                continue
            if evidence.security != "security":
                problems.append(
                    f"{job_id}: security is {evidence.security}, but its run is the merge tier's"
                    " security evidence"
                )
            if not evidence.platform.startswith(family):
                problems.append(
                    f"{job_id}: platform is {evidence.platform}, but the merge tier's security"
                    f" evidence is its run on a {family}* runner"
                )
    return problems


def _workers_faults(job: Job, problems: list[str]) -> None:
    """``workers`` is the job's worker count: each pytest step's ``-n`` is it, and
    ``workers_env`` names the variable through which its other tools read it."""
    passed = [n for step in job.command for n in pytest_workers(step)]
    if job.workers is None:
        if passed:
            problems.append(f"{job.id}: a step passes pytest -n, so workers is required")
        if job.workers_env is not None:
            problems.append(f"{job.id}: workers_env is set, so workers is required")
        return
    for value in passed:
        if value != str(job.workers):
            problems.append(
                f"{job.id}: a step passes pytest -n {value}, and workers is {job.workers}"
            )
    if not passed and job.workers_env is None:
        problems.append(
            f"{job.id}: workers is {job.workers}, and no step passes pytest -n nor does"
            " workers_env name a variable it reaches"
        )


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


# ── a pull request's catalogue, read against main's ────────────────────────────────────────


def _kind(job: Job) -> str:
    return "security" if job.security == "security" else "live"


def _nightly_faults(catalogue: Catalogue, job: Job) -> list[str]:
    """What is wrong with ``job``'s ``nightly`` mark: it lets a live job out of a plan its
    inputs do not reach, so it holds only while the job it names runs the same steps every night,
    and only where nothing else holds the job in every plan."""
    if not job.live:
        return [
            f"{job.id}: nightly is set, and the job is not live: a job that is not live is"
            " planned by its inputs already"
        ]
    if job.security == "security":
        return [
            f"{job.id}: nightly is set, and the job is security posture, which every plan holds"
        ]
    other = next((j for j in catalogue.jobs if j.id == job.nightly and j is not job), None)
    if other is None:
        return [f"{job.id}: nightly names {job.nightly!r}, which is not another job"]
    problems: list[str] = []
    if "breadth" not in other.tiers:
        problems.append(f"{job.id}: nightly names {other.id}, which the breadth tier does not run")
    if not other.live:
        problems.append(
            f"{job.id}: nightly names {other.id}, which is not live, so a reused result could"
            " stand for its nightly run"
        )
    if other.command != job.command:
        problems.append(f"{job.id}: nightly names {other.id}, whose steps are not this job's")
    return problems


def trusted_marks(candidate: Catalogue, trusted: Catalogue) -> Catalogue:
    """``candidate`` with each ``nightly`` mark main's catalogue (``trusted``) does not give the
    same job read as absent. A mark lets a live job out of a plan, so like ``retiring`` it counts
    only once main carries it: the pull request that adds one still plans the job."""
    theirs = {job.id: job.nightly for job in trusted.jobs}
    jobs = tuple(
        (
            replace(job, nightly=None)
            if job.nightly is not None and theirs.get(job.id) != job.nightly
            else job
        )
        for job in candidate.jobs
    )
    return replace(candidate, jobs=jobs)


def against(candidate: Catalogue, trusted: Catalogue) -> list[str]:
    """What main's catalogue (``trusted``) refuses in a pull request's (``candidate``).

    A security or live job of main's stays, with its posture and every tier main gives it: a
    candidate cannot drop the evidence its own verdict is judged by. Removal takes two merges.
    First the job is marked ``retiring = "#<issue>"`` (it stays and still runs); once main's
    catalogue carries the mark, a pull request may drop that entry, and only drop it. A mark the
    candidate adds itself is not main's, so it allows nothing. While main runs a security job in
    the breadth tier, the candidate keeps one there too, whatever main marks. :func:`load` applies
    this when it is given main's catalogue as its ``reference``."""
    problems: list[str] = []
    have = {job.id: job for job in candidate.jobs}
    for job in trusted.jobs:
        if job.security != "security" and not job.live:
            continue
        mine = have.get(job.id)
        if mine is None:
            if job.retiring is None:
                problems.append(
                    f"{job.id}: main's catalogue has this {_kind(job)} job, and this catalogue"
                    f' drops it: mark it retiring = "#<issue>" in a merge of its own first'
                )
            continue
        if job.security == "security" and mine.security != "security":
            problems.append(
                f"{job.id}: security is {mine.security}, and main's catalogue has it security"
            )
        if job.live and not mine.live:
            problems.append(f"{job.id}: live is false, and main's catalogue has it live")
        for tier in job.tiers:
            if tier not in mine.tiers:
                problems.append(
                    f"{job.id}: it leaves the {tier} tier, where main's catalogue runs this"
                    f" {_kind(job)} job"
                )
    if _breadth_security(trusted) and not _breadth_security(candidate):
        problems.append(
            "no security job runs in the breadth tier, and main's catalogue runs one there"
        )
    return problems


def _breadth_security(cat: Catalogue) -> bool:
    return any(job.security == "security" for job in cat.tier("breadth"))


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
        for script in job.script or ():
            print(f"  script {script} in {job.container}")
    return 0
