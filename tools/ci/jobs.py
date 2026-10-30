"""Run the catalogue's jobs on this machine: ``python -m tools.ci run-job`` and ``run``.

``run-job <id>`` runs one job of ``tools/ci/catalogue.toml``, or one shard of it, the way CI's job
runs it: its toolchain's installs, then each of its steps, token for token, through
:func:`tools.ci.run.run_job`. Every step gets the job's :class:`~tools.ci.run.Scratch` and an
environment built from nothing (:func:`tools.ci.run.job_env`): what a GitHub runner gives every
step (:data:`RUNNER_ENV`, ``CI=true``), the workflow's env, then the job's, then the step's, never
the caller's beyond its ``PATH``. A value only the runner knows is filled in: ``{runner_temp}`` is
the job's own temporary folder (``Scratch.tmp``; on GitHub, ``$RUNNER_TEMP``), ``{shard}`` the
shard asked for, ``{base}`` the ref a diff is taken against, ``{run_number}`` the run's number on
GitHub and :data:`LOCAL_RUN_NUMBER` here.

The job has its catalogue cap less a minute, across every step; a step still running when that
runs out is stopped with its whole group, and a pytest step says first which of its processes
were still alive, since a hung xdist worker is the usual cause. Every report the job declares
must then exist and be newer than the job's start, so a pytest that died before writing one is
red. A step that fails stops the job, except the first whose one fault is failed tests its own
report names (the diagnostic rerun, below, found them): the job is red, and its other steps run on,
inside the same time, so what they leave is there for a later job (a browser leg's later homes
leave the blobs ``e2e`` and ``ui-quality`` merge).

Each job (each shard of a sharded one) leaves one record (:func:`verdict.job_record`): its key,
result, the head it ran on, the partner revisions it read, the run and its attempt (on GitHub,
``$GITHUB_RUN_ID`` and ``$GITHUB_RUN_ATTEMPT``, either refused before any step when it cannot be
read; locally, no run and attempt 1), the diagnostic rerun (below), which quarantined tests
failed, what it ran on (:func:`runner_of`: the catalogue's label, the image and the machine) and
its toolchain's identities, as a key reads them, each step's argv, exit and seconds, and how many
of its planned steps never started (job-record/4: one its time ran out before). A record
is left only when the checkout was
clean, at the same head, before and after the job, and each partner checkout the job reads was
at the same head before and after it: otherwise the head or partner revision it would name is
not what it ran.

**The diagnostic rerun.** When a pytest step exits 1 (tests ran and some failed) and nothing else
went wrong, :func:`diagnose` reads the failed tests from the step's own JUnit report, each as the
one test file of the step's own its case names, and reruns them once, alone (``-n 0`` when the
step ran workers), in a new process, with the step's per-test ``--timeout`` and inside the job's
remaining time. A rail (``tests/rails/``) is never rerun, and the rerun deselects every
``security``-marked test. Each failed test is classified ``flake`` (it passed alone) or
``repeated failure`` in the record's ``rerun`` field, as a ``rerun_result`` property in the job's
report (which the telemetry collector reads) and as a ``::warning`` line. The files the step left
beside its report are put back after the rerun. The job stays red whatever the rerun shows: only
a quarantine entry waives a known failure.

``run <tier>`` runs every job of a tier in the catalogue's order, each after the jobs its
``after`` names, every shard of each, and judges each job's records with :func:`verdict.decide`
against the head and the partner revisions that job reads. A tier with no job is input it cannot
run. A job for another platform family, in a container, or needing a toolchain or partner
checkout this machine lacks, cannot run here: it is named, and the run is PARTIAL, never green.
So, off GitHub, is an ``electron`` job on a host whose kernel withholds the user namespaces
Chromium's sandbox needs (:mod:`tools.ci.sandbox_host`), named with the commands that lift the
rule. A run short only of jobs this host cannot run is incomplete: its summary says
``### Verdict: incomplete`` with each one's cause, and its last line ``incomplete: N jobs could
not run here``. Any other job that is not green, a red one or one that left no record, keeps the
verdict red. After an interrupted job nothing more runs. ``--changed`` runs the jobs
:func:`tools.ci.plan.plan` plans for the diff from the base.

Exit codes are :mod:`tools.ci.run`'s: 0 every planned job ran here and passed; 1 a job ran and
failed, or broke its isolation; 2 a job could not run here, was interrupted, or left no record,
or the input could not be read (an unknown job, tier or option, an option given twice or not
spelled in full, a tier with no job).

``--working-tree`` (either verb) runs the same steps on the checkout as it stands, uncommitted
changes included, and writes no record, on a clean tree or a dirty one: the steps ran on no
commit. Its exit is the steps' result, 2 only for a job that cannot run here or an interrupted
run, and ``run`` judges nothing. Only in this mode does a job of another platform family run
here, when every program its toolchain needs is on ``PATH``; the summary names it, with the
platform CI runs it on. The mode is refused on GitHub, where the checkout is the commit under
test.

**Reuse.** ``run <tier>`` reads the local verdict store (:mod:`tools.ci.verdict_store`) before
any job runs: a job of a reusable class with a recorded clean pass of its key (this tree, and
every other input as this run would run the job) is not run, and the run's verdict counts it by
its receipt, ``reuse-receipt.json``; each other job runs, and a clean pass of a reusable class is
recorded. ``--no-reuse`` reads no recorded pass and still records. A working-tree run and
``run-job`` never read or write the store.

**Admission.** Off GitHub, ``run <tier>`` starts each job only once the machine has the memory
it needs, its workers times its last peak here (:mod:`tools.ci.admission`); a job still waiting
after ``--admission-wait`` is not run (partial). Admission decides when a job starts, never what
it runs.

A local result never stands in for GitHub's: a local record names no run, so a run's verdict
(``verdict --records --run-id``) reads it red, and no workflow reads the store. A local
``--workers N`` replaces the catalogue's worker count, is refused on GitHub, is written in
the record, and is in the key.

Standard library only, like every ``tools/ci`` module.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import platform as host_platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import (
    admission,
    capabilities,
    catalogue,
    hung,
    plan,
    run,
    sandbox_host,
    shards,
    verdict,
    verdict_store,
)
from .catalogue import Catalogue, Job, Step

#: What a GitHub-hosted runner gives every step that a step run here gets too: ``CI``, which the
#: e2e job's Playwright config reads for its JSON report, retries and server. ``GITHUB_ACTIONS``
#: is not here: it says the run is on GitHub (:attr:`Host.on_github`). A rails test holds this to
#: its declared list.
RUNNER_ENV: dict[str, str] = {"CI": "true"}

#: The runner's variables a step gets on GitHub, as GitHub gives them to every step: the event and
#: the full ref, which say whether the run is a push to main or a pull request's, the runs whose
#: rails legs record what each rail read (tests/rails_reads.py). Off GitHub none is passed, so a
#: local run records nothing, and ``GITHUB_ACTIONS``, which says the run is on GitHub, never is.
RUNNER_PASSED = ("GITHUB_EVENT_NAME", "GITHUB_REF")

#: The env the tier workflows give every job, at their top level. A test holds it to theirs.
WORKFLOW_ENV: dict[str, str] = {"UV_PYTHON_PREFERENCE": "only-managed"}

#: Each toolchain word whatever runs a job installs, as it installs it.
INSTALL_ARGV: dict[str, tuple[str, ...]] = {
    "uv-sync": ("uv", "sync", "--locked", "--extra", "dev"),
    "npm-ci": ("npm", "ci"),
    "chromium": ("npx", "playwright", "install", "chromium"),
    "electron": ("sh", "tools/ci/install_electron.sh"),
}

#: The marker run-job gives a command that runs Electron's setuid sandbox helper
#: (``tools/ci/sandbox_helper.sh``) on a GitHub-hosted Linux runner, so the helper gives it root's
#: 4755 there and nowhere else: each step's environment is built from nothing, so the
#: ``GITHUB_ACTIONS`` and ``RUNNER_ENVIRONMENT`` the helper reads under a workflow step never reach
#: it under run-job (merge.yml run 40053059413's web-build). run-job reads the runner from its own
#: process environment (:func:`github_hosted_linux`), and gives the marker to the ``electron``
#: install and to a step that runs the helper itself, and to no other command. No catalogue entry
#: may set it (``catalogue.RUN_JOB_SETS``).
SANDBOX_HELPER_ENV = "PCLAW_SANDBOX_HELPER_HOST"
SANDBOX_HELPER_HOSTED = "github-hosted"
#: A step that runs the helper itself, as desktop-smoke does on the packed app.
SANDBOX_HELPER_STEP: tuple[str, ...] = ("sh", "tools/ci/sandbox_helper.sh")

#: The job's own Python, which run-job gives every step of a job that sets up a venv
#: (:func:`_cache_env`): that venv's interpreter, wherever uv puts it in this run mode. The
#: harness's smoke scripts (``harness/exemplars/resolve_py.sh``), its profiles
#: (``harness/profiles.py``) and the browser runs' gateway (``web/playwright.config.ts``) each take
#: it over a checkout's ``.venv``. A local run's venv is in the work folder, so a new checkout has
#: no ``.venv``: gate proof 40143285105's exemplar smokes found no Python there, and its gateway
#: command was not found. A checkout that has one has the contributor's own, which is not the
#: job's. No catalogue entry may set it (``catalogue.RUN_JOB_SETS``).
JOB_PYTHON_ENV = "PERSONALCLAW_PY"

#: The `chromium` install's bounds and commands beside :data:`INSTALL_ARGV`'s (#5265,
#: :func:`install_chromium`): Playwright's build without apt, a check that it starts, and its
#: system libraries from apt only when that check fails. A hung apt mirror then holds the job for a
#: bound that names it, never its whole limit.
CHROMIUM_INSTALL_S = 300
CHROMIUM_START_CHECK: tuple[str, ...] = (
    "node",
    "-e",
    "require('playwright').chromium.launch().then((browser) => browser.close())",
)
CHROMIUM_START_CHECK_S = 120
CHROMIUM_LIBRARIES: tuple[str, ...] = ("npx", "playwright", "install-deps", "chromium")
CHROMIUM_LIBRARIES_S = 300

#: Each toolchain word that is a program this machine must already have.
PROGRAMS: dict[str, str] = {
    "uv": "uv",
    "node": "node",
    "tmux": "tmux",
    "shellcheck": "shellcheck",
    "dash": "dash",
    "docker": "docker",
}

#: The partner checkouts, by external input: where CI checks each out, under the root.
PARTNERS: dict[str, str] = {"apps-partner": ".ci-apps"}

#: The job's time is its catalogue cap less this, so it is stopped before CI would stop it.
MARGIN_S = 60

#: The folder, under the repository root, each run's scratch folders, caches and records go in.
WORK = ".local/run-job"

_SHARD = re.compile(r"([1-9][0-9]*)/([1-9][0-9]*)")


class Unreadable(Exception):
    """The input cannot be run: an unknown job, a bad shard, a refused option."""


@dataclass(frozen=True)
class Host:
    """What the machine running the jobs has, given so a test can stand in for it."""

    root: Path
    family: str  # the runner family whose jobs run here: "ubuntu-" or "macos-"
    path: str  # the PATH every step gets
    on_github: bool = False
    lister: Callable[[int], list[str]] | None = None
    #: The memory a tier run admits each job by (tools/ci/admission.py): this machine's off
    #: GitHub, where a runner holds one job; ``None`` starts every job at once.
    memory: admission.Memory | None = field(default=None, compare=False)
    #: Why this host cannot run an Electron job's sandbox, given the root, or ``None`` when it
    #: can (tools/ci/sandbox_host.py): read off GitHub only. ``None`` here reads nothing.
    sandbox: Callable[[Path], str | None] | None = field(default=None, compare=False)
    #: The folder every local run on this account shares uv's cache and managed Pythons in
    #: (:func:`shared_cache_root`), or ``None`` for each checkout's own, as on CI.
    shared_cache: Path | None = field(default=None, compare=False)

    @classmethod
    def this(cls, root: Path) -> Host:
        family = {"darwin": "macos-", "linux": "ubuntu-"}.get(sys.platform, "")
        on_github = bool(os.environ.get("GITHUB_ACTIONS"))
        return cls(
            root=root,
            family=family,
            path=os.environ.get("PATH", os.defpath),
            on_github=on_github,
            memory=None if on_github else admission.Memory.this(),
            sandbox=None if on_github else sandbox_host.problem,
            shared_cache=None if on_github or os.environ.get("CI") else shared_cache_root(),
        )


@dataclass(frozen=True)
class Options:
    base: str | None = None
    workers: int | None = None
    deadline_s: float | None = None  # overrides the cap, for tests
    grace: float = run.GRACE
    #: Run on the checkout as it stands, uncommitted changes included: the same steps, no
    #: record, and the steps' result. A local mode only; on GitHub the checkout is a commit.
    working_tree: bool = False
    #: Read the verdict store and skip a job it holds a clean pass of (``--no-reuse`` turns it
    #: off: every job runs, and each clean pass is still recorded).
    reuse: bool = True
    #: How long a job of a local tier run may wait for the memory it needs before the run gives
    #: it up (``--admission-wait``; tools/ci/admission.py).
    admission_wait: float = admission.WAIT_S
    #: Keep the folder a run inside the checkout made in the system's temporary folder for its
    #: jobs' scratch (``--keep-scratch``; :func:`scratch_base`). Removed when the run ends
    #: otherwise.
    keep_scratch: bool = False


@dataclass
class Outcome:
    """How one job, or one shard of it, ended here."""

    key: str
    code: int
    reasons: list[str] = field(default_factory=list)
    ran: bool = True
    record: Path | None = None
    interrupted: bool = False  # the runner got SIGINT or SIGTERM while a step ran
    #: In a working-tree run only: the platform the job runs on in CI, when this machine is of
    #: another family and the job ran here anyway. Never set on a run that records.
    off_platform: str | None = None
    #: Not run: the verdict store held a clean pass of this job on this tree.
    reused: bool = False
    #: The tests its JUnit reports say were skipped, as ``classname::name``: neither an expected
    #: failure nor a quarantined one. Read only from a job that passed.
    skipped: list[str] = field(default_factory=list)
    #: The peak memory of the largest process of any of its steps, in MB, of the steps that
    #: ended on their own (``run.Result.peak_mb``).
    peak_mb: int | None = None
    #: Not run: this host cannot run the job (:func:`cannot_run_here`), and its reasons say why.
    not_here: bool = False


# ── what a job needs, and whether it can run here ────────────────────────────────────────────


def key_of(job: Job, shard: int | None) -> str:
    return f"{job.id} ({shard}/{job.shards})" if shard is not None else job.id


def off_its_platform(job: Job, host: Host) -> bool:
    """Whether this machine is of another runner family than the one ``job`` runs on in CI."""
    return not host.family or not job.platform.startswith(host.family)


def cannot_run_here(job: Job, host: Host, *, working_tree: bool = False) -> list[str]:
    """Why ``job`` cannot run on this machine; empty when it can.

    A job of another platform family cannot run here, except in a working-tree run
    (``working_tree``), which writes no record, so its result can never stand as evidence.
    A container, a toolchain program missing from PATH and a missing partner checkout are
    reasons in either mode, and off GitHub so is a host whose kernel withholds the user
    namespaces an ``electron`` job's sandbox needs (``host.sandbox``). On GitHub that is never
    read: the runner gives the sandbox helper root's 4755 (``tools/ci/sandbox_helper.sh``)."""
    why: list[str] = []
    if off_its_platform(job, host) and not working_tree:
        why.append(f"it runs on {job.platform}, and this machine runs {host.family or 'neither'}*")
    if job.container is not None:
        why.append(f"it runs in the container {job.container}")
    for tool in job.toolchain:
        program = PROGRAMS.get(tool) or INSTALL_ARGV.get(tool, ("",))[0]
        if program and shutil.which(program, path=host.path) is None:
            why.append(f"its toolchain needs {program}, which is not on PATH")
    for name in job.external_inputs:
        where = PARTNERS.get(name)
        if where and not (host.root / where / ".git").exists():
            why.append(f"it reads {name}, and there is no checkout at {where}")
    if "electron" in job.toolchain and not host.on_github and host.sandbox is not None:
        withheld = host.sandbox(host.root)
        if withheld:
            why.append(withheld)
    return why


def _git(host: Host, *args: str, cwd: Path | None = None) -> str:
    """The runner's own git read: no system or global file, so no helper of the caller's runs."""
    env = {
        "PATH": host.path,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    done = subprocess.run(
        ["git", *args],
        cwd=cwd or host.root,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if done.returncode != 0:
        raise Unreadable(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def _state(host: Host) -> tuple[str, bool]:
    """The checkout's head, and whether its tree is clean (nothing changed or untracked)."""
    head = _git(host, "rev-parse", "HEAD").strip()
    return head, not _git(host, "status", "--porcelain", "--untracked-files=normal").strip()


def external_revisions(job_or_jobs: Sequence[Job], host: Host) -> dict[str, str]:
    """Each partner checkout's head the jobs read: only the partners their external inputs
    name."""
    out: dict[str, str] = {}
    for job in job_or_jobs:
        for name in job.external_inputs:
            where = PARTNERS.get(name)
            if where and (host.root / where / ".git").exists():
                out[name] = _git(host, "rev-parse", "HEAD", cwd=host.root / where).strip()
    return out


def _python(job: Job, host: Host) -> str:
    """The uv-managed interpreter of ``job``'s Python, as ``{python}`` stands for it: what
    ``uv python find`` answers here. A job with no Python of its own, or one uv cannot find,
    cannot run here."""
    if not job.python or job.python == "image":
        raise Unreadable(f"{job.id} names no Python, so {{python}} has no value")
    done = subprocess.run(
        ["uv", "python", "find", job.python],
        cwd=host.root,
        capture_output=True,
        text=True,
        env={"PATH": host.path, "HOME": os.environ.get("HOME", "")},
        stdin=subprocess.DEVNULL,
    )
    found = done.stdout.strip()
    if done.returncode != 0 or not found or "\n" in found:
        raise Unreadable(f"uv found no Python {job.python} here: {done.stderr.strip()[:200]}")
    return found


#: A commit id as an event names it: a SHA-1 or a SHA-256 object id, in lowercase hex.
_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _base(host: Host, options: Options) -> str:
    """``--base``; on GitHub, a push to main's commit it replaced (the event's ``before``), as the
    push's own diff; else the merge-base with ``origin/main``, which is a pull request's base on
    its merge commit, and on a push to main the pushed commit itself."""
    if options.base:
        return options.base
    replaced = pushed_before(host)
    if replaced is not None:
        return replaced
    return _git(host, "merge-base", "HEAD", "origin/main").strip()


def pushed_before(host: Host) -> str | None:
    """On GitHub, the commit a push to ``refs/heads/main`` replaced, from its own event; else, or
    for a push that created the branch (an all-zero ``before``), none."""
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not host.on_github or not event_path:
        return None
    if (os.environ.get("GITHUB_EVENT_NAME"), os.environ.get("GITHUB_REF")) != (
        "push",
        "refs/heads/main",
    ):
        return None
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    before = event.get("before") if isinstance(event, dict) else None
    if not isinstance(before, str) or not _OBJECT_ID.fullmatch(before) or not before.strip("0"):
        return None
    return before


# ── one job ──────────────────────────────────────────────────────────────────────────────────


def _render(value: str, values: Mapping[str, str]) -> str:
    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise Unreadable(f"no value for the placeholder {{{name}}}")
        return values[name]

    return re.sub(r"\{([^{}]*)\}", fill, value)


#: ``{run_number}`` here, where no numbered run runs: a local run of a job that names it draws
#: the same values every time (the randomized-order leg's seeds ``01`` and ``02``). A
#: ``GITHUB_RUN_NUMBER`` the caller set names another run, so it is not read.
LOCAL_RUN_NUMBER = "0"


def _run_number(host: Host) -> str:
    """``{run_number}``: on GitHub the run's own number, ``GITHUB_RUN_NUMBER``, which every
    attempt of the run keeps; :data:`LOCAL_RUN_NUMBER` here."""
    if not host.on_github:
        return LOCAL_RUN_NUMBER
    number = os.environ.get("GITHUB_RUN_NUMBER")
    if number is None or not re.fullmatch(r"[0-9]+", number):
        raise Unreadable(
            f"GITHUB_RUN_NUMBER is {number!r}, not a run's number, so {{run_number}} has no value"
        )
    return number


def _workers(job: Job, options: Options) -> str | None:
    if options.workers is None:
        return None if job.workers is None else str(job.workers)
    return str(options.workers)


#: What a catalogue job's ``workers = "auto"`` is off GitHub, in ``run``, ``make check`` and
#: ``make gate``: two, the owner's machine rule after a local run took 40 GB on 2026-10-22. On
#: GitHub ``auto`` is the runner's CPU count, so CI's speed and its measured targets stay as
#: they are. A local ``--workers N`` still sets any count, and admission still holds it.
LOCAL_AUTO_WORKERS = 2


def _local_workers(job: Job, options: Options, host: Host) -> int | None:
    """The worker count a run here sets in place of the catalogue's: ``--workers``, or
    :data:`LOCAL_AUTO_WORKERS` for ``auto`` off GitHub; ``None`` when the catalogue's stands."""
    if options.workers is not None:
        return options.workers
    if job.workers == "auto" and not host.on_github:
        return LOCAL_AUTO_WORKERS
    return None


def _worker_count(job: Job, options: Options, host: Host) -> str | None:
    """The worker count the job's steps get: the local ``--workers`` or the catalogue's, with
    ``auto`` read as the runner's CPU count on GitHub and :data:`LOCAL_AUTO_WORKERS` here."""
    workers = _workers(job, options)
    if workers != "auto":
        return workers
    return str(os.cpu_count() or 1) if host.on_github else str(LOCAL_AUTO_WORKERS)


def _step_argv(
    step: Step, job: Job, values: Mapping[str, str], options: Options, host: Host
) -> list[str]:
    """The step's argv as it runs here: its placeholders filled, and in each worker count it
    passes pytest, in each spelling :func:`catalogue.pytest_workers` reads (``-n N``, ``-nN``,
    ``--numprocesses N``, ``--numprocesses=N``), a local ``--workers``, or off GitHub
    :data:`LOCAL_AUTO_WORKERS` for the catalogue's ``auto``."""
    argv = [_render(a, values) for a in step.argv]
    workers = _local_workers(job, options, host)
    if workers is None or "pytest" not in argv:
        return argv
    count, local = str(job.workers), str(workers)
    for i in range(argv.index("pytest") + 1, len(argv)):
        if argv[i] in ("-n", "--numprocesses") and argv[i + 1 : i + 2] == [count]:
            argv[i + 1] = local
        elif argv[i] == f"--numprocesses={count}":
            argv[i] = f"--numprocesses={local}"
        elif argv[i] == f"-n{count}":
            argv[i] = f"-n{local}"
    # The record says the local count, so no step may run another.
    if any(n != local for n in catalogue.pytest_workers(Step(tuple(argv)))):
        raise Unreadable(
            f"{job.id}: {local} workers cannot set every pytest -n of {shlex.join(argv)}"
        )
    return argv


#: What names the folder local runs share uv's cache and managed Pythons in, in place of the
#: account's own cache folder (:func:`shared_cache_root`): a machine that keeps everything a run
#: writes under one folder sets it, and the suite pins it to a folder of its own.
SHARED_CACHE_ENV = "PCLAW_RUN_JOB_CACHE"
#: The most the shared uv cache may hold, in GB, before a local run evicts from it
#: (:func:`bound_shared_cache`), and what changes it.
CACHE_BOUND_GB = 6.0
CACHE_BOUND_ENV = "PCLAW_RUN_JOB_CACHE_GB"
#: How long an eviction waits for uv's lock on the cache, in seconds, before it leaves the cache
#: as it is for this run: another checkout's run holds it while its uv runs.
EVICT_LOCK_S = 10
#: The shared caches already bounded, and checked for their file system, by this process.
_READIED: set[Path] = set()


def shared_cache_root(environ: Mapping[str, str] = os.environ) -> Path | None:
    """Where local runs share uv's cache and managed Pythons: :data:`SHARED_CACHE_ENV` when it
    names a folder, else the account's own cache folder, resolved from the home its entry in the
    user database names (``run.real_home``), never ``$HOME``, which a caller can point anywhere:
    ``Library/Caches/personalclaw/run-job`` on macOS, ``$XDG_CACHE_HOME/personalclaw/run-job``
    (else ``.cache/...``) elsewhere. ``None`` when the account's home can't be told, and each
    checkout keeps its own."""
    given = environ.get(SHARED_CACHE_ENV)
    if given:
        return Path(given)
    try:
        home = run.real_home()
    except RuntimeError:
        return None
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "personalclaw" / "run-job"
    xdg = environ.get("XDG_CACHE_HOME", "")
    return (Path(xdg) if os.path.isabs(xdg) else home / ".cache") / "personalclaw" / "run-job"


def _blocks(folder: Path) -> int:
    """The bytes the files under *folder* take on disk."""
    total = 0
    for parent, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.lstat(os.path.join(parent, name)).st_blocks * 512
            except OSError:
                pass
    return total


def _packages_oldest_first(uv_cache: Path) -> list[str]:
    """Each package the cache holds wheels of, the one written to longest ago first: by the
    newest entry under its folder in each index (``wheels-v*/pypi/<name>``,
    ``wheels-v*/index/<index>/<name>``)."""
    newest: dict[str, float] = {}
    for wheels in uv_cache.glob("wheels-v*"):
        folders = [*wheels.glob("pypi/*"), *wheels.glob("index/*/*")]
        for folder in folders:
            try:
                times = [entry.lstat().st_mtime for entry in folder.iterdir()]
            except OSError:
                continue
            newest[folder.name] = max([newest.get(folder.name, 0.0), *times])
    return sorted(newest, key=lambda name: (newest[name], name))


def bound_shared_cache(
    root: Path,
    *,
    bound_gb: float | None = None,
    uv: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    log=None,
) -> None:
    """Keep the shared uv cache under *root* within its bound (:data:`CACHE_BOUND_GB`, or
    :data:`CACHE_BOUND_ENV`): over it, ``uv cache prune`` first, and if it is still over, ``uv
    cache clean`` of the packages written to longest ago, a few at a time, oldest first, until it
    holds under four fifths of the bound. Both through uv, whose lock keeps a run in another
    checkout from reading an entry while it goes; each waits :data:`EVICT_LOCK_S` for that lock
    and, refused it, leaves the cache as it is for this run and says so. A checkout's venv keeps
    working once its packages go: its files are its own clones or links of what the cache held.
    Managed Pythons are not evicted."""
    out = log or sys.stderr
    cache = root / "uv"
    if not cache.is_dir():
        return
    if bound_gb is None:
        try:
            bound_gb = float(os.environ.get(CACHE_BOUND_ENV) or CACHE_BOUND_GB)
        except ValueError:
            bound_gb = CACHE_BOUND_GB
    bound = int(bound_gb * 1e9)
    size = _blocks(cache)
    if size <= bound:
        return
    env = {**os.environ, "UV_CACHE_DIR": str(cache), "UV_LOCK_TIMEOUT": str(EVICT_LOCK_S)}

    def through_uv(*words: str) -> bool:
        try:
            done = uv(["uv", "cache", *words], env=env, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"run: the shared uv cache was left as it is: {exc}", file=out)
            return False
        if done.returncode != 0:
            said = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or ["no reason"]
            print(f"run: the shared uv cache was left as it is: {said[0]}", file=out)
            return False
        return True

    print(
        f"run: the shared uv cache at {cache} holds {size / 1e9:.1f} GB, past its"
        f" {bound_gb:g} GB: pruning it",
        file=out,
    )
    if not through_uv("prune"):
        return
    size = _blocks(cache)
    oldest = _packages_oldest_first(cache)
    while size > bound * 0.8 and oldest:
        batch, oldest = oldest[:8], oldest[8:]
        if not through_uv("clean", *batch):
            return
        size = _blocks(cache)
    print(f"run: the shared uv cache holds {size / 1e9:.1f} GB", file=out)


def _device(path: Path) -> int | None:
    """The file system *path* is on, or the nearest folder above it that exists is on."""
    for each in (path, *path.parents):
        try:
            return each.stat().st_dev
        except OSError:
            continue
    return None


def _ready_shared_cache(host: Host) -> None:
    """Once a process: bound the host's shared cache (:func:`bound_shared_cache`), and say when it
    is on another file system than the checkout, where uv copies a venv's files in place of
    linking them, so each checkout's venv costs its whole size again."""
    shared = host.shared_cache
    if shared is None or shared in _READIED:
        return
    _READIED.add(shared)
    if _device(shared) != _device(host.root):
        print(
            f"run: the shared uv cache {shared} is on another file system than {host.root}: uv"
            f" copies each venv's files from it rather than linking them ({SHARED_CACHE_ENV}"
            " names another folder)",
            file=sys.stderr,
        )
    bound_shared_cache(shared)


def _command_uv(job: Job, host: Host, scratch: run.Scratch) -> dict[str, str]:
    """What a command step of *job* runs uv with where the host shares its cache: a cache of the
    job's own in its scratch folder, and no sync (``UV_NO_SYNC``), so the command runs the venv
    the job's ``uv sync`` install left and nothing it does reaches the shared cache. Only that
    install writes it, from what the lock's hashes allow. Nothing where each checkout keeps its
    own cache, or for a job with no ``uv sync`` install, whose commands install what they use."""
    if host.shared_cache is None or "uv-sync" not in job.toolchain:
        return {}
    return {"UV_CACHE_DIR": str(scratch.root / "uv-cache"), "UV_NO_SYNC": "1"}


def _cache_env(job: Job, host: Host) -> dict[str, str]:
    """Where a step's caches, managed Pythons and project venv go.

    Here, under the work folder, kept between runs as CI restores its caches, never the caller's
    own; and ``uv venv`` replaces a venv an earlier local run left in the checkout, as a new
    checkout on CI has none. Where the host shares a cache (:attr:`Host.shared_cache`), uv's
    cache for a ``uv sync`` job and the managed Pythons are that folder's, so every checkout on
    the account installs from one cache, and its venv, still its own under the work folder, is
    linked from it: on APFS each file is a clone of the cache's, so a checkout's venv costs about
    a tenth of its size. The venv stays the checkout's own: ``uv sync`` installs the project
    itself, editable, from that checkout's ``src``. The commands after the install run with a
    cache of their own (:func:`_command_uv`). ``npm ci`` still reinstalls the checkout's own
    ``node_modules``, as it does on CI.

    On GitHub, in the setup action's own folders, so the cache it restored is what the job reads
    and the cache it saves holds what the job wrote: setup-uv's cache and Python folders
    (``UV_CACHE_DIR`` and ``UV_PYTHON_INSTALL_DIR``, which it sets; a ``UV_PYTHON`` it set up
    that is not the job's own Python is refused), and the npm cache setup-node
    restores, ``.npm`` in the runner's own home (a step's ``HOME`` is its scratch home); and the
    project venv where uv puts it, the checkout's ``.venv``, as ci.yml's jobs have it: a new
    checkout has none, and ``harness/exemplars/resolve_py.sh`` finds the dev dependencies' Python
    there (run 40053059413's exemplar smokes found none in the work folder's). setup-uv
    fails its post step on a cache folder nothing wrote to, which every uv job of a run made with
    the work folder's did (merge.yml run 40053059413). A folder the runner names none for is the
    work folder's, as here, and so are Playwright's browsers, which no setup action caches.

    In both, the venv's own interpreter is the job's Python, given as ``PERSONALCLAW_PY``
    (:data:`JOB_PYTHON_ENV`), so nothing a step runs looks for it in a checkout's ``.venv``."""
    cache = host.root / WORK / "cache"
    shared = host.shared_cache
    runner = os.environ if host.on_github else {}
    env: dict[str, str] = {}
    if "uv" in job.toolchain or "uv-sync" in job.toolchain:
        uv_cache = shared / "uv" if shared and "uv-sync" in job.toolchain else cache / "uv"
        env["UV_CACHE_DIR"] = runner.get("UV_CACHE_DIR") or str(uv_cache)
        env["UV_PYTHON_INSTALL_DIR"] = runner.get("UV_PYTHON_INSTALL_DIR") or str(
            (shared or cache) / "uv-python"
        )
        if not host.on_github:
            env["UV_PROJECT_ENVIRONMENT"] = str(cache / f"venv-{job.python or 'default'}")
            env["UV_VENV_CLEAR"] = "1"
        venv = Path(env.get("UV_PROJECT_ENVIRONMENT") or host.root / ".venv")
        env[JOB_PYTHON_ENV] = str(venv / "bin" / "python")
        if job.python and job.python != "image":
            # The job's own Python is the contract: a runner that set up another is refused,
            # never followed, so a record cannot name one Python and have run another.
            given = runner.get("UV_PYTHON")
            if given and given != job.python:
                raise Unreadable(
                    f"{job.id} names Python {job.python}, and the runner's setup-uv set up"
                    f" {given} (UV_PYTHON): the step would not run the job's own Python"
                )
            env["UV_PYTHON"] = job.python
    if "npm-ci" in job.toolchain:
        home = runner.get("HOME")
        env["npm_config_cache"] = str(Path(home) / ".npm") if home else str(cache / "npm")
    if "chromium" in job.toolchain:
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "ms-playwright")
    return env


def _base_env(job: Job, host: Host, options: Options) -> dict[str, str]:
    """What every step of *job* gets below its job's own env and its own: the runner's (and, on
    GitHub, :data:`RUNNER_PASSED`), the workflow's, the caches (:func:`_cache_env`), and the
    worker count when the job names a variable for it (``workers_env``)."""
    env = {**RUNNER_ENV, **WORKFLOW_ENV, **_cache_env(job, host)}
    if host.on_github:
        env.update({name: os.environ[name] for name in RUNNER_PASSED if name in os.environ})
    count = _worker_count(job, options, host)
    if job.workers_env and count is not None:
        env[job.workers_env] = count
    return env


def _offline_plan(job: Job, host: Host, runner_temp: Path) -> None:
    """The shard plan a local run uses: every collectable file in equal weights, split the way
    ``shards plan`` splits them, for each leg the job's steps name. Never on GitHub."""
    files = shards.universe(host.root)
    if not files:
        raise Unreadable(f"no test file is collectable under {host.root}")
    legs = {
        step.argv[step.argv.index("--leg") + 1] for step in job.command if "--leg" in step.argv[:-1]
    }
    plan = {
        leg: {
            "n": job.shards,
            "buckets": shards.plan(files, {}, job.shards),
            "durations": "offline: a local run, so every file weighs the same",
        }
        for leg in sorted(legs)
    }
    out = runner_temp / "shard-plan" / "plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"version": shards.VERSION, "legs": plan}, sort_keys=True))


def _list_group(pgid: int) -> list[str]:
    done = subprocess.run(
        ["pgrep", "-l", "-g", str(pgid)], capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    return done.stdout.splitlines()


def _hang_report(host: Host) -> Callable[[int], None]:
    """What a pytest step out of time says before its group is stopped: each process of the group
    still alive, then where each thread of each waits (:func:`hung.group`), read from outside it,
    since a process blocked in native code holding the GIL can say nothing of itself."""

    def report(pgid: int) -> None:
        lister = host.lister or _list_group
        try:
            alive = lister(pgid)
        except Exception as exc:  # noqa: BLE001 - the stop goes ahead whatever the lister does
            alive = [f"(the group could not be listed: {type(exc).__name__}: {exc})"]
        print(
            f"possible xdist hang: the job ran out of time with {len(alive)} process(es) of"
            f" group {pgid} alive:",
            file=sys.stderr,
        )
        for line in alive:
            print(f"  {line}", file=sys.stderr)
        pids = [int(words[0]) for words in map(str.split, alive) if words and words[0].isdigit()]
        if pids:
            print(f"where each thread of each waits:\n{hung.group(pids)}", file=sys.stderr)

    return report


def _overdue(argv: Sequence[str], cwd: Path) -> Callable[[int], None] | None:
    """For a pytest step that writes a JUnit report, the watch that ends a worker past its test's
    own deadline from outside it (:class:`hung.Overdue`), reading where the hard deadline's dump
    for that report goes; None for any other step."""
    junit = _junit_arg(argv)
    if "pytest" not in argv or junit is None:
        return None
    return hung.Overdue.beside(cwd / junit)


def skipped_tests(path: Path) -> list[str]:
    """The tests a JUnit report says were skipped: each case pytest recorded as skipped, but for
    an expected failure (``xfail``) and a quarantined case, which :func:`quarantined_failures`
    reads."""
    found = []
    for case in ET.parse(path).getroot().iter("testcase"):
        skipped = case.find("skipped")
        if skipped is None or skipped.get("type") == "pytest.xfail":
            continue
        if "quarantine_issue" in {p.get("name") for p in case.iter("property")}:
            continue
        found.append(f"{case.get('classname', '')}::{case.get('name', '')}")
    return found


def quarantined_failures(path: Path) -> list[str]:
    """The quarantined tests a JUnit report says failed: a case carrying the quarantine's
    property that pytest recorded as an expected failure."""
    found = []
    for case in ET.parse(path).getroot().iter("testcase"):
        names = {p.get("name") for p in case.iter("property")}
        if "quarantine_issue" in names and case.find("skipped") is not None:
            found.append(f"{case.get('classname', '')}::{case.get('name', '')}")
    return found


# ── the diagnostic rerun ─────────────────────────────────────────────────────────────────────

#: The JUnit property the rerun writes on each failed case it classified, the one the telemetry
#: collector reads: :data:`verdict.FLAKE` or :data:`verdict.REPEATED_FAILURE`.
RERUN_PROPERTY = "rerun_result"

#: Where the rails live. A failure there is red at once and never rerun.
RAILS = "tests/rails/"

#: pytest's exit status when tests ran and some failed: the one ending with a test to diagnose.
TESTS_FAILED = 1

#: pytest's exit statuses for a run that read its tests: all passed, some failed, none selected.
_READ_ITS_TESTS = frozenset({0, 1, 5})

#: The xdist options that start workers. A step that names one ran in workers, so its rerun
#: names ``-n 0``; a step that names none ran in one process, with a pytest that may have no
#: xdist (the client package's has none) and would refuse ``-n``.
_WORKER_OPTIONS = frozenset({"-n", "--numprocesses", "--dist", "--tx"})

#: Why a failed case whose classname names no one test file of the step's own is not rerun.
NOT_ONE_FILE = "names no one test file of the step's own, so it is not rerun"

Key = tuple[str, str]
"""A JUnit case's classname and name, which is how pytest names one test in its report."""


def _junit_arg(argv: Sequence[str]) -> str | None:
    """The report a pytest step writes, from ``--junitxml=PATH`` or ``--junitxml PATH``."""
    for i, arg in enumerate(argv):
        for flag in ("--junitxml", "--junit-xml"):
            if arg.startswith(flag + "="):
                return arg.partition("=")[2]
            if arg == flag and i + 1 < len(argv):
                return argv[i + 1]
    return None


def _key(case: ET.Element) -> Key:
    return case.get("classname") or "", case.get("name") or ""


def _after_pytest(argv: Sequence[str]) -> list[str]:
    return list(argv[list(argv).index("pytest") + 1 :])


def _test_paths(argv: Sequence[str], cwd: Path) -> list[Path]:
    """What a pytest step collects: each argument after ``pytest`` that is not an option and
    names a file or folder here (a node id by its file), resolved; the step's folder when it
    names none, as pytest then collects from there. An option's value that names a path adds
    only a place to look, so it can make a case name two files, never another one."""
    found = []
    for arg in _after_pytest(argv):
        if arg.startswith("-"):
            continue
        path = cwd / arg.split("::")[0]
        if path.exists():
            found.append(path.resolve())
    return found or [cwd.resolve()]


def _node_id(case: ET.Element, cwd: Path, paths: Sequence[Path]) -> str | None:
    """A failed case's pytest node id, relative to *cwd*, or None when the case names no one
    test file of the step's own.

    pytest writes a case's classname from its node id, which is relative to pytest's rootdir:
    the test file's path, dotted, then its classes. That rootdir is a folder at or above what
    the step collects (*paths*): the repository's, or a package's with a pytest config of its
    own, as ``packages/personalclaw-client-py`` has. So the classname is looked up from every
    folder at or above each path, and a ``.py`` file counts only when it is one of *paths* or
    under one. Exactly one file and class path must match: with none, or two, the case could
    be another test than the one that failed."""
    parts = [p for p in (case.get("classname") or "").split(".") if p]
    name = case.get("name") or ""
    if not parts or not name:
        return None
    bases: set[Path] = set()
    for path in paths:
        folder = path if path.is_dir() else path.parent
        bases.update((folder, *folder.parents))
    found: set[tuple[Path, tuple[str, ...]]] = set()
    for base in bases:
        for k in range(1, len(parts) + 1):
            file = base.joinpath(*parts[: k - 1], parts[k - 1] + ".py")
            if any(file.is_relative_to(path) for path in paths) and file.is_file():
                found.add((file, tuple(parts[k:])))
    if len(found) != 1:
        return None
    [(file, classes)] = found
    return "::".join([Path(os.path.relpath(file, cwd.resolve())).as_posix(), *classes, name])


def _case_result(case: ET.Element) -> str:
    if case.find("failure") is not None or case.find("error") is not None:
        return "failed"
    return "skipped" if case.find("skipped") is not None else "passed"


def _results(report: Path) -> dict[Key, str]:
    """Each test of a report, by its key, and its result: failed when any of its cases failed
    (a failed call and an errored teardown are two cases of one test), passed when every one
    passed, else skipped."""
    found: dict[Key, set[str]] = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        found.setdefault(_key(case), set()).add(_case_result(case))
    return {
        key: "failed" if "failed" in got else "passed" if got == {"passed"} else "skipped"
        for key, got in found.items()
    }


def _rerun_args(argv: Sequence[str]) -> list[str]:
    """What the rerun keeps of the step's own pytest options: its per-test time budget
    (``--timeout``) and the plugins it loads or blocks (``-p``), so the rerun has the first
    attempt's budget and plugins. Its file selection, workers and report are its own."""
    rest = _after_pytest(argv)
    kept: list[str] = []
    for i, arg in enumerate(rest):
        if arg.startswith("--timeout="):
            kept.append(arg)
        elif arg in ("--timeout", "-p") and i + 1 < len(rest):
            kept += rest[i : i + 2]
    return kept


def _worker_args(argv: Sequence[str]) -> list[str]:
    """``-n 0`` for a step that ran xdist workers, so its rerun runs alone; none for a step that
    ran in one process already (:data:`_WORKER_OPTIONS`)."""
    for arg in _after_pytest(argv):
        if arg.partition("=")[0] in _WORKER_OPTIONS or re.fullmatch(r"-n\S+", arg):
            return ["-n", "0"]
    return []


def _mark_report(report: Path, words: Mapping[Key, str]) -> None:
    """Write each classified test's ``rerun_result`` property on its cases in the job's own
    report."""
    tree = ET.parse(report)
    for case in tree.getroot().iter("testcase"):
        word = words.get(_key(case))
        if word is None:
            continue
        props = case.find("properties")
        if props is None:
            props = ET.Element("properties")
            case.insert(0, props)
        ET.SubElement(props, "property", {"name": RERUN_PROPERTY, "value": word})
    tree.write(report, encoding="utf-8", xml_declaration=True)


def _keep(folder: Path, into: Path) -> dict[str, tuple[int, int]]:
    """Copy each file *folder* holds (not its subfolders) into *into*, before the rerun can write
    over it, and return each one's size and modification time."""
    into.mkdir(mode=0o700, parents=True)
    kept: dict[str, tuple[int, int]] = {}
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                stat = entry.stat(follow_symlinks=False)
                shutil.copy2(entry.path, into / entry.name)
                kept[entry.name] = (stat.st_size, stat.st_mtime_ns)
    return kept


def _put_back(
    folder: Path, kept_in: Path, kept: Mapping[str, tuple[int, int]], moved_to: Path
) -> list[str]:
    """Leave *folder* as the step left it: each file the rerun made or changed there moves to
    *moved_to*, and each of the step's files is put back from *kept_in*. Returns the names of
    the files the rerun wrote."""
    folder.mkdir(parents=True, exist_ok=True)
    with os.scandir(folder) as entries:
        now = [
            (e.name, e.stat(follow_symlinks=False))
            for e in entries
            if e.is_file(follow_symlinks=False)
        ]
    wrote = []
    for name, stat in now:
        if kept.get(name) != (stat.st_size, stat.st_mtime_ns):
            moved_to.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.move(folder / name, moved_to / name)
            wrote.append(name)
    for name in kept:
        if not (folder / name).exists():
            shutil.copy2(kept_in / name, folder / name)
    shutil.rmtree(kept_in)
    return sorted(wrote)


def _entry(test: str, word: str | None, why: str) -> dict[str, object]:
    return {"test": test, "rerun_result": word, "why": why}


def _classify(got: str | None, clean_run: bool, code: int | None) -> tuple[str | None, str]:
    """A rerun test's word and why, from its result in the rerun's own report (None when that
    report holds no case of it, or could not be read)."""
    if got == "passed":
        return verdict.FLAKE, "passed rerun alone in a new process"
    if got == "failed":
        return verdict.REPEATED_FAILURE, "failed again, alone"
    if got == "skipped":
        return None, "skipped on the rerun, so not classified"
    if clean_run:
        return None, "security-marked: the rerun deselects it, so it is never rerun"
    return None, f"the rerun reported nothing for it (exit {code})"


def diagnose(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    host: Host,
    scratch: run.Scratch,
    grace: float,
    deadline: float,
    started: float,
) -> tuple[dict[str, object] | None, list[str], bool]:
    """Rerun a failed pytest step's failed tests once, alone, in a fresh process, to classify
    each. Returns the record's ``rerun`` (None when there is no failed test to diagnose), the
    reasons it adds, and whether the runner was interrupted during the rerun. It never changes
    the job's result: the first result is the result.

    The failed tests come from the step's own JUnit report, read as XML, never from its terminal
    text, each as the one test file of the step's own its case names (:func:`_node_id`). A rail
    (under ``tests/rails/``) is never rerun, and the rerun deselects every ``security``-marked
    test (``-m "not security"``), so neither runs twice. The rerun is a new process group, alone
    (``-n 0`` when the step ran workers), with the step's own ``--timeout`` and ``-p`` options,
    inside what is left of the job's time. It reads its own report only when every case there is
    one the step's report failed, named alike. The files the step left beside its report (a
    session report, as the real-home guard's) are put back after it, and what the rerun wrote
    there moves to the job's scratch folder."""
    junit = _junit_arg(argv)
    if junit is None:
        return None, [], False
    report = Path(junit) if Path(junit).is_absolute() else cwd / junit
    try:
        if not report.is_file() or report.stat().st_mtime < started:
            return None, [], False
        failed_cases = [
            c for c in ET.parse(report).getroot().iter("testcase") if _case_result(c) == "failed"
        ]
    except (ET.ParseError, OSError):
        return None, [], False
    if not failed_cases:
        return None, [], False
    paths = _test_paths(argv, cwd)
    rails = (host.root / RAILS).resolve()
    order: list[str] = []
    entries: dict[str, dict[str, object]] = {}
    keys: dict[str, Key] = {}  # each test the rerun runs, and its cases' key in the step's report
    for case in failed_cases:
        test = _node_id(case, cwd, paths)
        if test is None:
            label = "::".join(_key(case))
            if label not in entries:
                order.append(label)
                entries[label] = _entry(label, None, NOT_ONE_FILE)
            continue
        if test in entries or test in keys:
            continue  # a failed call and an errored teardown: one test, two cases
        order.append(test)
        if (cwd / test.split("::")[0]).resolve().is_relative_to(rails):
            entries[test] = _entry(test, None, "a rail: red at once and never rerun")
        else:
            keys[test] = _key(case)
    rerun_argv: list[str] | None = None
    code: int | None = None
    reasons: list[str] = []
    interrupted = False
    work = scratch.tmp / f"rerun-{uuid.uuid4().hex}"
    kept: dict[str, tuple[int, int]] | None = None
    left = 0.0
    if keys:
        try:
            kept = _keep(report.parent, work / "kept")
        except OSError as exc:
            reasons.append(f"rerun: the step's report folder could not be kept aside: {exc}")
        left = deadline - time.monotonic()
    if keys and (kept is None or left <= 0):
        why = (
            "the step's report folder could not be kept aside, so it is not rerun"
            if kept is None
            else "the job's time ran out before the rerun"
        )
        shutil.rmtree(work, ignore_errors=True)
        for test in keys:
            entries[test] = _entry(test, None, why)
    elif kept is not None:
        out = work / "junit.xml"
        rerun_argv = [
            *argv[: list(argv).index("pytest") + 1],
            *_worker_args(argv),
            "-p",
            "no:cacheprovider",
            *_rerun_args(argv),
            "-m",
            "not security",
            f"--junitxml={out}",
            *keys,
        ]
        print(f"rerun, alone, for diagnosis only: {shlex.join(rerun_argv)}", file=sys.stderr)
        result = run.run_job(
            rerun_argv,
            cwd=cwd,
            scratch=scratch,
            timeout=left,
            path=host.path,
            env=env,
            grace=grace,
            before_stop=_hang_report(host),
            watch=_overdue(rerun_argv, cwd),
        )
        code, interrupted = result.returncode, result.interrupted
        other = [v for v in result.violations if not v.startswith("the job exited")]
        reasons += [f"rerun: {v}" for v in other]
        try:
            wrote = _put_back(report.parent, work / "kept", kept, work / "outputs")
        except OSError as exc:
            reasons.append(f"rerun: the step's report folder could not be put back: {exc}")
        else:
            if wrote:
                print(
                    f"rerun: it wrote {', '.join(wrote)}; kept in {work / 'outputs'}",
                    file=sys.stderr,
                )
        seen: dict[Key, str] | None = None
        if not out.is_file():
            reasons.append("rerun: it wrote no report")
        else:
            try:
                seen = _results(out)
            except (ET.ParseError, OSError) as exc:
                reasons.append(f"rerun: its report is unreadable: {exc}")
        foreign = sorted(set(seen or {}) - set(keys.values()))
        if foreign:
            reasons.append(
                f"rerun: its report names {'::'.join(foreign[0])}, which the step's report did"
                " not fail, so it classifies nothing"
            )
            seen = None
        clean_run = seen is not None and code in _READ_ITS_TESTS and not other and not interrupted
        for test, key in keys.items():
            word, why = _classify((seen or {}).get(key), clean_run, code)
            entries[test] = _entry(test, word, why)
    listed = [entries[t] for t in order]
    words = {keys[t]: str(entries[t]["rerun_result"]) for t in keys if entries[t]["rerun_result"]}
    if words:
        try:
            _mark_report(report, words)
        except (ET.ParseError, OSError) as exc:
            reasons.append(f"rerun: the job's report could not be marked: {exc}")
    for entry in listed:
        title = entry["rerun_result"] or "never rerun"
        print(f"::warning title={title}::{entry['test']} ({entry['why']})", flush=True)
    return {"argv": rerun_argv, "exit": code, "tests": listed}, reasons, interrupted


# ── the web runners ────────────────────────────────────────────────────────────────────────

#: The web-unit job's Vitest step, token for token, and the folder Vitest runs in.
VITEST_STEP = ("npm", "run", "test:web")
VITEST_ROOT = "web"

#: The JUnit report Vitest writes under CI (``web/vitest.config.ts``), from the repository root.
#: Its cases' classname is the test file, from :data:`VITEST_ROOT`; their name, the test's full
#: name (its suites and title joined by `` > ``), which is what ``--testNamePattern`` matches.
VITEST_JUNIT = "reports/junit-web-unit.xml"

#: The folder Playwright's ``testDir`` names, under :data:`VITEST_ROOT`: its JSON report names
#: each spec's file from there.
PLAYWRIGHT_TEST_DIR = "e2e"

#: The Playwright step's start, and the variable naming its JSON report.
PLAYWRIGHT_STEP = ("npx", "playwright", "test")
PLAYWRIGHT_REPORT_ENV = "PLAYWRIGHT_JSON_OUTPUT_NAME"

#: The statuses Playwright gives a run of a test that failed: an assertion, or its timeout.
_PLAYWRIGHT_FAILED = frozenset({"failed", "timedOut"})

_JS_SPECIAL = re.compile(r"[.*+?^${}()|\[\]\\/]")


def _js_literal(text: str) -> str:
    """*text* as a JavaScript regular expression matching it literally (and Python's too)."""
    return _JS_SPECIAL.sub(lambda m: "\\" + m.group(0), text)


def diagnose_vitest(
    *,
    root: Path,
    env: Mapping[str, str],
    host: Host,
    scratch: run.Scratch,
    grace: float,
    deadline: float,
    started: float,
) -> tuple[dict[str, object] | None, list[str], bool]:
    """Rerun a failed Vitest step's failed tests once, alone, in a new process, to classify each,
    as :func:`diagnose` does for pytest; the job stays red whatever the rerun shows.

    The failed tests come from the step's JUnit report (:data:`VITEST_JUNIT`), read as XML; a
    report that is missing, older than the job or unreadable gives no rerun. A case whose
    classname is no test file under :data:`VITEST_ROOT` is not rerun. The rerun is ``npx vitest
    run`` in a new process group on one worker (``--maxWorkers=1``, and ``VITEST_MAX_WORKERS=1``),
    of the failed tests' files only, filtered to their full names, each whole
    (``--testNamePattern=^(?:…)$``), so a test that passed in the step, its title's namesake in
    another suite included, is not run again. It has the config's own per-test time budget
    (nothing sets a time or a retry), inside what is left of the job's time. Its report goes to
    the job's scratch folder, and is read only when every case it ran is one the step's report
    failed, named alike: Vitest reports a test of a named file that the filter leaves out as
    skipped. The files beside the step's report are put back after it."""
    report = root / VITEST_JUNIT
    cwd = root / VITEST_ROOT
    try:
        if not report.is_file() or report.stat().st_mtime < started:
            return None, [], False
        failed_cases = [
            c for c in ET.parse(report).getroot().iter("testcase") if _case_result(c) == "failed"
        ]
    except (ET.ParseError, OSError):
        return None, [], False
    if not failed_cases:
        return None, [], False
    order: list[str] = []
    entries: dict[str, dict[str, object]] = {}
    keys: dict[str, Key] = {}
    for case in failed_cases:
        file, name = _key(case)
        test = f"{VITEST_ROOT}/{file}::{name}"
        if test in entries or test in keys:
            continue
        order.append(test)
        path = (cwd / file).resolve() if file else cwd
        if not file or not name or not path.is_file() or not path.is_relative_to(cwd.resolve()):
            entries[test] = _entry(test, None, NOT_ONE_FILE)
        else:
            keys[test] = (file, name)
    rerun_argv: list[str] | None = None
    code: int | None = None
    reasons: list[str] = []
    interrupted = False
    work = scratch.tmp / f"rerun-{uuid.uuid4().hex}"
    kept: dict[str, tuple[int, int]] | None = None
    left = 0.0
    if keys:
        try:
            kept = _keep(report.parent, work / "kept")
        except OSError as exc:
            reasons.append(f"rerun: the step's report folder could not be kept aside: {exc}")
        left = deadline - time.monotonic()
    if keys and (kept is None or left <= 0):
        why = (
            "the step's report folder could not be kept aside, so it is not rerun"
            if kept is None
            else "the job's time ran out before the rerun"
        )
        shutil.rmtree(work, ignore_errors=True)
        for test in keys:
            entries[test] = _entry(test, None, why)
    elif kept is not None:
        out = work / "junit.xml"
        files = sorted({file for file, _ in keys.values()})
        names = sorted({_js_literal(name) for _, name in keys.values()})
        rerun_argv = [
            "npx",
            "vitest",
            "run",
            "--maxWorkers=1",
            "--reporter=junit",
            f"--outputFile={out}",
            f"--testNamePattern=^(?:{'|'.join(names)})$",
            *files,
        ]
        print(f"rerun, alone, for diagnosis only: {shlex.join(rerun_argv)}", file=sys.stderr)
        result = run.run_job(
            rerun_argv,
            cwd=cwd,
            scratch=scratch,
            timeout=left,
            path=host.path,
            env={**env, "VITEST_MAX_WORKERS": "1"},
            grace=grace,
        )
        code, interrupted = result.returncode, result.interrupted
        other = [v for v in result.violations if not v.startswith("the job exited")]
        reasons += [f"rerun: {v}" for v in other]
        try:
            _put_back(report.parent, work / "kept", kept, work / "outputs")
        except OSError as exc:
            reasons.append(f"rerun: the step's report folder could not be put back: {exc}")
        seen: dict[Key, str] | None = None
        if not out.is_file():
            reasons.append("rerun: it wrote no report")
        else:
            try:
                seen = _results(out)
            except (ET.ParseError, OSError) as exc:
                reasons.append(f"rerun: its report is unreadable: {exc}")
        failed = set(keys.values())
        foreign = sorted(
            k for k, got in (seen or {}).items() if got != "skipped" and k not in failed
        )
        if foreign:
            reasons.append(
                f"rerun: its report names {'::'.join(foreign[0])}, which the step's report did"
                " not fail, so it classifies nothing"
            )
            seen = None
        clean = seen is not None and code in (0, 1) and not other and not interrupted
        for test, key in keys.items():
            got = (seen or {}).get(key) if clean else None
            if got == "passed":
                word, why = verdict.FLAKE, "passed rerun alone in a new process"
            elif got == "failed":
                word, why = verdict.REPEATED_FAILURE, "failed again, alone"
            elif got == "skipped":
                word, why = None, "skipped on the rerun, so not classified"
            else:
                word, why = None, f"the rerun reported nothing for it (exit {code})"
            entries[test] = _entry(test, word, why)
    return _finish(report, order, entries, keys, reasons, rerun_argv, code, interrupted)


def _finish(
    report: Path | None,
    order: Sequence[str],
    entries: Mapping[str, dict[str, object]],
    keys: Mapping[str, Key],
    reasons: list[str],
    argv: list[str] | None,
    code: int | None,
    interrupted: bool,
) -> tuple[dict[str, object], list[str], bool]:
    """Mark the step's report (when it has one) and say each classification as a warning."""
    listed = [entries[t] for t in order]
    words = {keys[t]: str(entries[t]["rerun_result"]) for t in keys if entries[t]["rerun_result"]}
    if words and report is not None:
        try:
            _mark_report(report, words)
        except (ET.ParseError, OSError) as exc:
            reasons.append(f"rerun: the job's report could not be marked: {exc}")
    for entry in listed:
        title = entry["rerun_result"] or "never rerun"
        print(f"::warning title={title}::{entry['test']} ({entry['why']})", flush=True)
    return {"argv": argv, "exit": code, "tests": listed}, reasons, interrupted


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} is not a string")
    return value


def _items(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{what} is not a list")
    return value


def _object(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{what} is not an object")
    return value


def _playwright_tests(
    suite: object, titles: tuple[str, ...]
) -> list[tuple[str, str, str, str, list[str], int | None]]:
    """Each test of a JSON report's suite, nested ones included: its file, its titles below the
    file's own suite, its status, its project, each of its runs' status, and its spec's line (None
    when the report gives none). A suite, spec, test or run of another shape than Playwright
    writes raises :class:`ValueError`."""
    found: list[tuple[str, str, str, str, list[str], int | None]] = []
    suite = _object(suite, "a suite")
    for spec in _items(suite.get("specs", []), "a suite's specs"):
        spec = _object(spec, "a spec")
        path = " > ".join((*titles, _text(spec.get("title"), "a spec's title")))
        file = _text(spec.get("file"), "a spec's file")
        line = spec.get("line")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            raise ValueError("a spec's line is not a whole number from 1")
        for test in _items(spec.get("tests"), "a spec's tests"):
            test = _object(test, "a test")
            runs = [
                _text(_object(r, "a test's run").get("status"), "a run's status")
                for r in _items(test.get("results"), "a test's results")
            ]
            status = _text(test.get("status"), "a test's status")
            project = _text(test.get("projectName"), "a test's projectName")
            found.append((file, path, status, project, runs, line))
    for child in _items(suite.get("suites", []), "a suite's suites"):
        title = _text(_object(child, "a suite").get("title"), "a suite's title")
        found += _playwright_tests(child, (*titles, title))
    return found


def _playwright_report(
    report: Path, name: str, started: float
) -> tuple[list[tuple[str, str, str, str, list[str], int | None]] | None, str]:
    """The tests of a Playwright JSON report written since *started*, or None and why not."""
    try:
        if not report.is_file():
            return None, f"Playwright wrote no {name}, so nothing is classified"
        if report.stat().st_mtime < started:
            return None, (
                f"Playwright wrote no {name} in this run (the one there is older than the job),"
                " so nothing is classified"
            )
        data = _object(json.loads(report.read_text(encoding="utf-8")), "the report")
        return [
            t
            for s in _items(data.get("suites"), "the report's suites")
            for t in _playwright_tests(s, ())
        ], ""
    except (OSError, ValueError, RecursionError) as exc:
        return None, f"its {name} is unreadable: {exc}"


#: The projects whose tests an invocation filtered to them runs on their own: the render smoke,
#: and the shared specs (``chromium``, which brings ``setup``, as the step does). A test of any
#: other project runs only in its chain or its home (web/playwright.config.ts), and is not rerun
#: alone.
PLAYWRIGHT_ALONE_PROJECTS = frozenset({"smoke", "chromium"})

#: The project the alone rerun runs as a dependency: its tests are not judged.
PLAYWRIGHT_SETUP = "setup"


def diagnose_playwright(
    argv: Sequence[str],
    *,
    root: Path,
    cwd: Path,
    env: Mapping[str, str],
    host: Host,
    scratch: run.Scratch,
    grace: float,
    deadline: float,
    started: float,
) -> tuple[dict[str, object] | None, list[str], bool]:
    """Classify a red Playwright step's failed tests, rerunning each that Playwright's own retry
    passed once more, alone; the job stays red whatever the rerun shows.

    Playwright's one retry (``retries: 1`` under CI) runs in a new worker, beside the others, so
    a pass there is not a pass alone. Each ``flaky`` test of :data:`PLAYWRIGHT_ALONE_PROJECTS`, at
    most :data:`verdict.MAX_FLAKES` of them (a step with more reruns none: the run could not pass
    them all), is rerun in one invocation, so one gateway starts: ``npx playwright test`` with
    :data:`verdict.E2E_ALONE` (one worker, no retry), each test's project and its
    ``<file>:<line>``, the step's env, and its reports, traces and outputs in the job's scratch
    folder, inside what is left of the job's time. The step's own reports beside it are put back
    after it. Each is ``flake`` when it passed there and ``repeated failure`` when it failed
    there. An ``unexpected`` test whose retry failed or timed out too is a repeated failure
    without a rerun. Any other is not classified.

    The rerun's own report is read only when every test it ran is one rerun, or a test of
    :data:`PLAYWRIGHT_SETUP`, which it runs as their dependency, and its exit is 0 or 1. The
    record's ``rerun`` names the alone rerun's own argv and exit, or no argv when none ran.

    The step's report is this run's only when it was written since the job started (*started*):
    one an earlier run left is never read as this one's. A report that is missing, older than
    the job, or of another shape than Playwright writes classifies nothing, and says so."""
    name = env.get(PLAYWRIGHT_REPORT_ENV)
    if not name:
        return None, ["rerun: Playwright wrote no JSON report, so nothing is classified"], False
    tests, why_not = _playwright_report(cwd / name, name, started)
    if tests is None:
        return None, [f"rerun: {why_not}"], False
    order: list[str] = []
    entries: dict[str, dict[str, object]] = {}
    alone: dict[tuple[str, str, str], tuple[str, int]] = {}  # (file, title, project): id, line
    for file, title, status, project, runs, line in tests:
        if status not in ("flaky", "unexpected"):
            continue
        rel = Path(os.path.relpath(cwd / PLAYWRIGHT_TEST_DIR / file, root)).as_posix()
        test_id = f"{rel}::{title} [{project}]"
        if test_id in entries:
            continue
        order.append(test_id)
        if status == "unexpected":
            if len(runs) < 2:
                word, why = None, "failed with no retry run, so not classified"
            elif runs[-1] in _PLAYWRIGHT_FAILED:
                word, why = verdict.REPEATED_FAILURE, "failed again on Playwright's one retry"
            else:
                word, why = None, f"its retry ended {runs[-1]}, so not classified"
            entries[test_id] = _entry(test_id, word, why)
        elif project not in PLAYWRIGHT_ALONE_PROJECTS:
            entries[test_id] = _entry(
                test_id,
                None,
                f"its project, {project}, runs only in its chain or its home, so it is not"
                " rerun alone",
            )
        elif line is None:
            entries[test_id] = _entry(
                test_id, None, "its report gives no line, so it is not rerun alone"
            )
        else:
            alone[(file, title, project)] = (test_id, line)
            entries[test_id] = _entry(test_id, None, "not rerun alone")
    if not order:
        return None, [], False
    reasons: list[str] = []
    if len(alone) > verdict.MAX_FLAKES:
        for test_id, _ in alone.values():
            entries[test_id] = _entry(
                test_id,
                None,
                f"one of {len(alone)} flaky tests, more than the {verdict.MAX_FLAKES} a run passes"
                " as flakes, so none is rerun alone",
            )
        alone = {}
    rerun_argv: list[str] | None = None
    code: int | None = None
    interrupted = False
    work = scratch.tmp / f"rerun-{uuid.uuid4().hex}"
    kept: dict[str, tuple[int, int]] | None = None
    left = 0.0
    if alone:
        try:
            kept = _keep(cwd, work / "kept")
        except OSError as exc:
            reasons.append(f"rerun: the step's report folder could not be kept aside: {exc}")
        left = deadline - time.monotonic()
    if alone and (kept is None or left <= 0):
        why = (
            "the step's report folder could not be kept aside, so it is not rerun"
            if kept is None
            else "the job's time ran out before the rerun"
        )
        shutil.rmtree(work, ignore_errors=True)
        for test_id, _ in alone.values():
            entries[test_id] = _entry(test_id, None, why)
    elif kept is not None:
        out = work / "report.json"
        projects = sorted({project for _, _, project in alone})
        places = sorted(
            {f"{PLAYWRIGHT_TEST_DIR}/{f}:{line}" for (f, _, _), (_, line) in alone.items()}
        )
        rerun_argv = [
            *PLAYWRIGHT_STEP,
            *verdict.E2E_ALONE,
            *(f"--project={project}" for project in projects),
            f"--output={work / 'test-results'}",
            *places,
        ]
        print(f"rerun, alone, for diagnosis only: {shlex.join(rerun_argv)}", file=sys.stderr)
        result = run.run_job(
            rerun_argv,
            cwd=cwd,
            scratch=scratch,
            timeout=left,
            path=host.path,
            env={
                **env,
                PLAYWRIGHT_REPORT_ENV: str(out),
                "PLAYWRIGHT_BLOB_OUTPUT_DIR": str(work / "blob-report"),
                "PLAYWRIGHT_HTML_OUTPUT_DIR": str(work / "playwright-report"),
            },
            grace=grace,
        )
        code, interrupted = result.returncode, result.interrupted
        other = [v for v in result.violations if not v.startswith("the job exited")]
        reasons += [f"rerun: {v}" for v in other]
        try:
            _put_back(cwd, work / "kept", kept, work / "outputs")
        except OSError as exc:
            reasons.append(f"rerun: the step's report folder could not be put back: {exc}")
        ran, why_not = _playwright_report(out, "rerun report", started)
        if ran is None:
            reasons.append(f"rerun: {why_not}")
        seen: dict[tuple[str, str, str], str] | None = None
        if ran is not None:
            seen = {(f, t, p): status for f, t, status, p, _, _ in ran if p != PLAYWRIGHT_SETUP}
            foreign = sorted(k for k, got in seen.items() if got != "skipped" and k not in alone)
            if foreign:
                reasons.append(
                    f"rerun: its report names {foreign[0][0]}::{foreign[0][1]}"
                    f" [{foreign[0][2]}], which was not rerun, so it classifies nothing"
                )
                seen = None
        clean = seen is not None and code in (0, 1) and not other and not interrupted
        for key, (test_id, _) in alone.items():
            got = (seen or {}).get(key) if clean else None
            if got == "expected":
                word, why = verdict.FLAKE, "passed rerun alone in a new process"
            elif got == "unexpected":
                word, why = verdict.REPEATED_FAILURE, "failed again, alone"
            elif got is not None:
                word, why = None, f"it was {got} on the rerun, so not classified"
            else:
                word, why = None, f"the rerun reported nothing for it (exit {code})"
            entries[test_id] = _entry(test_id, word, why)
    return _finish(None, order, entries, {}, reasons, rerun_argv, code, interrupted)


def _owned_env(job: Job) -> None:
    """Refuse a job whose env, a step's env or ``workers_env`` names a variable the runner owns
    (:func:`tools.ci.run.runner_owns`): the loader refuses one, so only a job built elsewhere
    can, and it is refused before anything runs."""
    names = {*job.env, *(name for step in job.command for name in step.env)}
    if job.workers_env:
        names.add(job.workers_env)
    owned = sorted(name for name in names if run.runner_owns(name))
    if owned:
        raise Unreadable(f"{job.id}: its env sets {', '.join(owned)}, which the runner owns")


def run_attempt(host: Host) -> int:
    """The run attempt a record names: on GitHub, ``$GITHUB_RUN_ATTEMPT``, which a rerun of the
    run raises, so a reader of attempt 1 can refuse a record a later attempt wrote; locally, 1.
    On GitHub a value that is not a whole number from 1 to 999999 in ASCII digits
    (:data:`verdict.ATTEMPT_TEXT`), or none, is refused before any step runs, since a record must
    never name an attempt it cannot vouch for."""
    if not host.on_github:
        return 1
    text = os.environ.get("GITHUB_RUN_ATTEMPT")
    if text is None or not verdict.ATTEMPT_TEXT.fullmatch(text):
        raise Unreadable(
            f"GITHUB_RUN_ATTEMPT must be a whole number from 1 to 999999 on GitHub, got {text!r}"
        )
    return int(text)


#: The folder under a run's folder a ``lint_feedback`` job's remediation is left in, which the
#: stage job uploads as ``lint-feedback`` for ``pr-feedback.yml`` to post (#3915).
FEEDBACK = "feedback"


def pull_request_number(host: Host) -> str | None:
    """The pull request this run is for, from its own event: a ``pull_request`` event's number
    on GitHub, else none. A dispatched or pushed run has none, and so leaves no remediation."""
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not host.on_github or not event_path:
        return None
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pull = event.get("pull_request") if isinstance(event, dict) else None
    number = pull.get("number") if isinstance(pull, dict) else None
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        return None
    return str(number)


def write_feedback(folder: Path, argv: Sequence[str], kept: Path, number: str) -> None:
    """Leave a failed step's kept output as the remediation ``pr-feedback.yml`` posts:
    ``violations.md``, the step's command and its output, and ``pr.txt``, the pull request's
    number. The output is data the relay fences; nothing here
    reads it as anything else."""
    words = list(argv[2:] if list(argv[:2]) == ["uv", "run"] else argv)
    text = kept.read_bytes().decode("utf-8", errors="replace").rstrip()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "violations.md").write_text(
        f"**{' '.join(words)}**\n```\n{text}\n```\n", encoding="utf-8"
    )
    (folder / "pr.txt").write_text(number, encoding="utf-8")


def run_id_of(host: Host) -> int | None:
    """The run a record names: on GitHub, ``$GITHUB_RUN_ID``, so the run's verdict reads a record
    of another run red; locally, none, so no run's verdict ever reads a local record green. On
    GitHub a value that is not a whole number from 1 in ASCII digits (:data:`verdict.RUN_ID_TEXT`),
    or none, is refused before any step runs."""
    if not host.on_github:
        return None
    text = os.environ.get("GITHUB_RUN_ID")
    if text is None or not verdict.RUN_ID_TEXT.fullmatch(text):
        raise Unreadable(f"GITHUB_RUN_ID must be a whole number from 1 on GitHub, got {text!r}")
    return int(text)


def _only_failed(result: run.Result) -> bool:
    """Whether *result* failed by its own exit or its own time alone: no signal, and no credential
    tripwire or real home, which are never a check's answer."""
    return (
        result.code == run.RED
        and not result.interrupted
        and all(
            v.startswith(("the job exited ", "the job timed out after ")) for v in result.violations
        )
    )


def install_chromium(
    step: Callable[[Sequence[str], float], run.Result],
) -> tuple[int, list[str], bool]:
    """The `chromium` toolchain word's install, in three steps (#5265). *step* runs one command
    within the seconds it is given, or what is left of the job when that is less.

    1. ``npx playwright install chromium``, the build the lock pins, within
       :data:`CHROMIUM_INSTALL_S`, and no apt.
    2. :data:`CHROMIUM_START_CHECK` within :data:`CHROMIUM_START_CHECK_S`: Chromium starts. Its
       failing is an answer, not the job's red.
    3. Only when it does not: :data:`CHROMIUM_LIBRARIES` within :data:`CHROMIUM_LIBRARIES_S`,
       named as the apt mirror when it does not finish, then the check again, which must pass.

    The e2e legs and browse-live check alike: the proof finds the headless shell Playwright
    installed under the run's ``PLAYWRIGHT_BROWSERS_PATH`` (tests/browse_chrome.py), the same
    binary ``chromium.launch()`` starts, so the libraries it needs are the same. Returns the run's
    code, the reasons it is not OK and whether the runner was interrupted."""
    installed = step(INSTALL_ARGV["chromium"], CHROMIUM_INSTALL_S)
    if installed.code != run.OK:
        why = [f"Chromium did not install: {v}" for v in installed.violations]
        return installed.code, why, installed.interrupted
    checked = step(CHROMIUM_START_CHECK, CHROMIUM_START_CHECK_S)
    if checked.code == run.OK:
        return run.OK, [], False
    if not _only_failed(checked):
        why = [f"Chromium's start check: {v}" for v in checked.violations]
        return checked.code, why, checked.interrupted
    print(
        "::warning::Chromium did not start on this runner image, so its system libraries"
        " install from apt",
        flush=True,
    )
    libraries = step(CHROMIUM_LIBRARIES, CHROMIUM_LIBRARIES_S)
    if libraries.code != run.OK:
        why = [f"Chromium's system libraries: {v}" for v in libraries.violations]
        if f"the job timed out after {CHROMIUM_LIBRARIES_S:g}s" in libraries.violations:
            named = (
                f"Chromium's system libraries did not install within {CHROMIUM_LIBRARIES_S:g}s:"
                " the apt mirror is slow or down"
            )
            print(f"::error::{named}", flush=True)
            why.insert(0, named)
        return libraries.code, why, libraries.interrupted
    again = step(CHROMIUM_START_CHECK, CHROMIUM_START_CHECK_S)
    if again.code != run.OK:
        why = [f"Chromium does not start with its system libraries: {v}" for v in again.violations]
        return again.code, why, again.interrupted
    return run.OK, [], False


#: Each run folder inside a checkout, and the folder of the system's temporary folder its jobs'
#: scratch folders go in instead (:func:`scratch_base`), made once a run.
_SCRATCH_BASES: dict[Path, Path] = {}


def scratch_base(host: Host, work: Path) -> Path:
    """Where the run in ``work`` makes its jobs' scratch folders: ``work/jobs``, unless ``work`` is
    inside the checkout, as a local run's default ``.local/run-job/<run>`` is. Then a folder the
    run makes in the system's temporary folder, which only its owner can open.

    A job's home and temporary folder inside the checkout put every tree one of its tests plants
    in its temporary folder inside the checkout too, where git answers for the checkout, whose
    ``.gitignore`` names ``.local/``: a rail that asks git what a planted tree holds heard nothing
    and passed what it should have failed (#3920, a licence rail's planted CLA file). On GitHub
    the run folder is the runner's temporary folder, outside the checkout, as it stays."""
    work = Path(work).resolve()
    if not work.is_relative_to(Path(host.root).resolve()):
        return work / "jobs"
    if work not in _SCRATCH_BASES:
        _SCRATCH_BASES[work] = Path(tempfile.mkdtemp(prefix=f"personalclaw-{work.name}-"))
    return _SCRATCH_BASES[work] / "jobs"


def release_scratch(work: Path, *, keep: bool) -> Path | None:
    """The end of the run in ``work``: the folder :func:`scratch_base` made for its jobs' scratch,
    their homes and temporary folders with it, is removed unless ``keep``, so they do not pile up
    in the system's temporary folder from run to run. Returns its path, for the receipt to name,
    or None when the run made none (its scratch is ``work/jobs``, or no job ran)."""
    base = _SCRATCH_BASES.pop(Path(work).resolve(), None)
    if base is not None and not keep:
        _remove_tree(base)
    return base


def _remove_tree(path: Path) -> None:
    """Remove ``path`` and everything under it: a file or folder a job left read-only (a module
    cache's) is made writable first. What still cannot be removed is said, not raised: the run's
    result stands."""

    def writable(function, name, _exc) -> None:  # type: ignore[no-untyped-def]
        for each in (os.path.dirname(name), name):
            try:
                os.chmod(each, 0o700)
            except OSError:
                pass
        try:
            function(name)
        except OSError:
            pass

    shutil.rmtree(path, onexc=writable)
    if path.exists():
        print(f"run: the jobs' scratch folder {path} could not all be removed", file=sys.stderr)


def run_one(
    cat: Catalogue,
    job: Job,
    shard: int | None,
    host: Host,
    options: Options,
    work: Path,
) -> Outcome:
    """Run ``job`` (shard ``shard`` of it) here, and leave its record in ``work/records`` (none
    with ``--working-tree``)."""
    key = key_of(job, shard)
    _owned_env(job)
    why = cannot_run_here(job, host, working_tree=options.working_tree)
    if why:
        said = [f"cannot run here: {w}" for w in why]
        return Outcome(key, run.PARTIAL, said, ran=False, not_here=True)
    if options.workers is not None and host.on_github:
        raise Unreadable("--workers is a local override; on GitHub the catalogue's count runs")
    if options.working_tree and host.on_github:
        raise Unreadable(
            "--working-tree is a local mode: on GitHub the checkout is the commit under test"
        )
    run_id = run_id_of(host)
    attempt = run_attempt(host)
    head, clean = _state(host)
    revisions = external_revisions([job], host)
    slug = re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-")
    scratch = run.Scratch.create(scratch_base(host, work) / slug)
    runner_temp = Path(os.environ["RUNNER_TEMP"]) if host.on_github else scratch.tmp
    values = {"runner_temp": str(runner_temp)}
    if shard is not None:
        values["shard"] = str(shard)
    if any("base" in step.placeholders() for step in job.command):
        values["base"] = _base(host, options)
    if any("python" in step.placeholders() for step in job.command):
        values["python"] = _python(job, host)
    # In a step or in the job's own env, which each step runs with: where the loader accepts it.
    if "run_number" in job.placeholders():
        values["run_number"] = _run_number(host)
    plan: str | None = None
    if job.shards > 1:
        if host.on_github:
            plan = "ci"
        else:
            _offline_plan(job, host, runner_temp)
            plan = "offline"
    base_env = _base_env(job, host, options)
    if "uv-sync" in job.toolchain:
        _ready_shared_cache(host)

    planned: list[tuple[list[str], str, dict[str, str]]] = [
        (list(INSTALL_ARGV[tool]), "", _helper_env(INSTALL_ARGV[tool], host))
        for tool in job.toolchain
        if tool in INSTALL_ARGV
    ]
    # The first command step's number: the installs before it leave no remediation.
    first_command = len(planned) + 1
    for step in job.command:
        env = {name: _render(value, values) for name, value in {**job.env, **step.env}.items()}
        argv = _step_argv(step, job, values, options, host)
        planned.append(
            (
                argv,
                step.workdir,
                {**env, **_helper_env(argv, host), **_command_uv(job, host, scratch)},
            )
        )

    total = (
        options.deadline_s if options.deadline_s is not None else job.timeout_min * 60 - MARGIN_S
    )
    started = time.time()
    deadline = time.monotonic() + total
    reasons: list[str] = []
    steps: list[tuple[list[str], int, float]] = []
    code = run.OK
    interrupted = False
    rerun: dict[str, object] | None = None
    peaks: list[int] = []
    # The planned steps begun: the record says how many never were (job-record/4), and the flake
    # rule passes a failing job only when every one began.
    begun = 0

    def bounded(
        n: int, ran: list[tuple[list[str], int, float]], words: Sequence[str], bound: float
    ) -> run.Result:
        """One command of step *n*, an install with bounds of its own, noted in *ran*: *bound*
        seconds, or what is left of the job when that is less, which a timeout's reason then
        names."""
        left = deadline - time.monotonic()
        if left <= 0:
            return run.Result(
                run.RED, 0, (f"the job's {total:g}s ran out before {shlex.join(words)}",)
            )
        print(f"[{key}] step {n}: {' '.join(words)}", file=sys.stderr, flush=True)
        began = time.monotonic()
        result = run.run_job(
            words,
            cwd=host.root,
            scratch=scratch,
            timeout=min(bound, left),
            path=host.path,
            env=base_env,
            grace=options.grace,
            budget=bound if bound <= left else total,
        )
        ran.append((list(words), result.returncode, time.monotonic() - began))
        if result.peak_mb is not None:
            peaks.append(result.peak_mb)
        return result

    for n, (argv, workdir, env) in enumerate(planned, 1):
        left = deadline - time.monotonic()
        if left <= 0:
            reasons.append(f"the job's {total:g}s ran out before step {n} ({' '.join(argv)})")
            code = run.RED
            break
        begun += 1
        if n < first_command and tuple(argv) == INSTALL_ARGV["chromium"]:
            ran: list[tuple[list[str], int, float]] = []
            code, more, interrupted = install_chromium(functools.partial(bounded, n, ran))
            # A record of success holds no step that exited non-zero (verdict.job_record). The
            # only one a green install leaves is the start check that sent it to apt: an answer,
            # not a failure, which the libraries' step after it records. A red one keeps all.
            steps.extend(s for s in ran if code != run.OK or s[1] == 0)
            reasons.extend(f"step {n}: {m}" for m in more)
            if code != run.OK:
                break
            continue
        print(f"[{key}] step {n}: {' '.join(argv)}", file=sys.stderr, flush=True)
        began = time.monotonic()
        kept = scratch.root / f"step-{n}.out" if job.lint_feedback and n >= first_command else None
        result = run.run_job(
            argv,
            cwd=host.root / workdir,
            scratch=scratch,
            timeout=left,
            path=host.path,
            env={**base_env, **env},
            grace=options.grace,
            before_stop=_hang_report(host) if "pytest" in argv else None,
            # The reason names the job's budget, not what was left of it as this step started.
            budget=total,
            output=kept,
            watch=_overdue(argv, host.root / workdir),
        )
        steps.append((argv, result.returncode, time.monotonic() - began))
        if result.peak_mb is not None:
            peaks.append(result.peak_mb)
        if result.code != run.OK:
            reasons.extend(f"step {n}: {v}" for v in result.violations)
            # A lint step that exited non-zero on its own hands its violations on: a timeout, a
            # signal or the tripwire is no remediation.
            number = pull_request_number(host)
            exited = result.violations == (f"the job exited {result.returncode}",)
            if kept is not None and kept.is_file() and exited and number is not None:
                write_feedback(work / FEEDBACK, argv, kept, number)
            code = result.code
            interrupted = result.interrupted
            # Only the job's first red step is diagnosed: the record holds one rerun, and a job
            # with two red steps is never a flake (verdict._pass_flakes), so a later one stops it.
            if rerun is not None:
                break
            # A pytest step whose tests ran and some failed (exit 1), and nothing else went wrong:
            # a timeout, a signal or the credential tripwire each adds a violation of its own, so
            # the exit is the only one. Its failed tests are rerun once, alone, to classify them.
            # The job stays red.
            if "pytest" in argv and result.violations == (f"the job exited {TESTS_FAILED}",):
                rerun, more, stopped = diagnose(
                    argv,
                    cwd=host.root / workdir,
                    env={**base_env, **env},
                    host=host,
                    scratch=scratch,
                    grace=options.grace,
                    deadline=deadline,
                    started=started,
                )
                reasons.extend(f"step {n}: {m}" for m in more)
                interrupted = interrupted or stopped
            # The same for the web runners: Vitest's failed tests are rerun once, alone, and so
            # are the Playwright tests its own retry passed. The job stays red.
            elif result.violations == (f"the job exited {TESTS_FAILED}",):
                if tuple(argv) == VITEST_STEP and not workdir:
                    rerun, more, stopped = diagnose_vitest(
                        root=host.root,
                        env={**base_env, **env},
                        host=host,
                        scratch=scratch,
                        grace=options.grace,
                        deadline=deadline,
                        started=started,
                    )
                    reasons.extend(f"step {n}: {m}" for m in more)
                    interrupted = interrupted or stopped
                elif tuple(argv[:3]) == PLAYWRIGHT_STEP:
                    rerun, more, stopped = diagnose_playwright(
                        argv,
                        root=host.root,
                        cwd=host.root / workdir,
                        env={**base_env, **env},
                        host=host,
                        scratch=scratch,
                        grace=options.grace,
                        deadline=deadline,
                        started=started,
                    )
                    reasons.extend(f"step {n}: {m}" for m in more)
                    interrupted = interrupted or stopped
            # A step whose one fault is failed tests its own report names (its diagnosis found
            # them) leaves the job red, and the job's other steps run on, inside the same time:
            # what they leave is there for the jobs that read it, as a browser leg's later homes
            # leave the blobs `e2e` and `ui-quality` merge. Any other fault stops the job.
            if rerun is not None and not interrupted:
                print(
                    f"[{key}] step {n}: its failed tests make the job red; its other steps run on",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            break
    quarantined: list[str] = []
    skipped: list[str] = []
    if code == run.OK:
        for report in job.junit:
            path = host.root / _render(report, values)
            if not path.is_file() or path.stat().st_mtime < started:
                reasons.append(f"wrote no junit at {path.relative_to(host.root)} in this run")
                code = run.RED
                continue
            try:
                quarantined += quarantined_failures(path)
                skipped += skipped_tests(path)
            except (ET.ParseError, OSError) as exc:
                reasons.append(f"its junit {report} is unreadable: {exc}")
                code = run.RED

    outcome = Outcome(
        key,
        code,
        reasons,
        interrupted=interrupted,
        skipped=skipped,
        peak_mb=max(peaks) if peaks else None,
    )
    if options.working_tree and off_its_platform(job, host):
        outcome.off_platform = job.platform
        outcome.reasons.append(
            f"off its platform: it runs on {job.platform} in CI, and ran here on"
            f" {host.family or 'another'}* instead; a local result is never CI's evidence"
        )
    if options.working_tree:
        # The steps ran on whatever the tree holds, so their result names no commit: it is
        # reported, and never written as a record a later run could take for evidence.
        outcome.reasons.append(
            "working tree: no record, since the steps ran on the checkout as it stands"
        )
        return outcome
    after_head, after_clean = _state(host)
    unrecorded = None
    if not (clean and after_clean and head == after_head):
        unrecorded = (
            "no record: the checkout was not clean at one head before and after the job, so the"
            " head a record would name is not the tree it ran"
        )
    elif external_revisions([job], host) != revisions:
        unrecorded = (
            "no record: a partner checkout the job reads was not at one head before and after"
            " the job, so the partner revision a record would name is not the one it read"
        )
    if unrecorded:
        outcome.reasons.append(unrecorded)
        if outcome.code == run.OK:
            outcome.code = run.PARTIAL
        return outcome
    word = {run.OK: verdict.SUCCESS, run.RED: verdict.FAILURE}.get(code, verdict.CANCELLED)
    # The toolchain as a key reads it. One that cannot be read leaves the record naming none,
    # which no push replays.
    toolchain: dict[str, str] | None
    try:
        toolchain = _toolchain(job, host, _as_written(job, options, host), {})
    except (capabilities.Refused, Unreadable):
        toolchain = None
    text = verdict.job_record(
        key=key,
        result=word,
        identity=verdict.Identity(head, revisions),
        steps=steps,
        attempt=attempt,
        run_id=run_id,
        rerun=rerun,
        quarantined=quarantined,
        plan=plan,
        workers=_local_workers(job, options, host),
        runner=runner_of(job, host),
        toolchain=toolchain,
        unstarted_steps=len(planned) - begun,
    )
    out = work / "records" / f"{slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    outcome.record = out
    return outcome


# ── reuse: the verdict store ─────────────────────────────────────────────────────────────────

#: The receipt a run writes in its folder: the run's ``dirty`` field, each planned job's reuse
#: decision and why, and, for each job that was not reused, whether its pass was recorded and why
#: not. When the store could not be used at all it also says why (``off``), and every job ran.
RECEIPT = "reuse-receipt.json"

#: The locks every key names, by their path from the root, when the checkout has them.
LOCKS = ("uv.lock", "package-lock.json")

#: Each placeholder as a key holds it: unfilled. ``{runner_temp}`` is this run's own scratch
#: folder, which would change every key; ``{shard}`` takes each of its values in every run of the
#: job, and the shard count is keyed; ``{base}`` names a commit, which the key holds as
#: ``declared["base"]``; ``{run_number}`` is another value every run, and a job whose steps name
#: it is never reused (``catalogue.validate``).
_AS_WRITTEN = {name: f"{{{name}}}" for name in catalogue.PLACEHOLDERS}

Fingerprint = Callable[..., Mapping[str, str]]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(program: str, host: Host, seen: dict[str, str]) -> str:
    """*program* as a step finds it, a bare name on the job's ``PATH`` and an absolute path as it
    is: its resolved path and the sha256 of its bytes, or ``absent``. Nothing is started, so no
    ``--version`` is read. *seen* holds each file already read in this run."""
    found = program if os.path.isabs(program) else shutil.which(program, path=host.path)
    if not found or not os.path.isfile(found):
        return capabilities.ABSENT
    if found not in seen:
        seen[found] = capabilities.identity(found)
    return seen[found]


def _as_written(job: Job, options: Options, host: Host) -> list[tuple[str, Sequence[str]]]:
    """Each install and step of *job* with the folder it runs in, its placeholders unfilled
    (:data:`_AS_WRITTEN`), as a key and a record's toolchain read them."""
    steps: list[tuple[str, Sequence[str]]] = [
        ("", INSTALL_ARGV[tool]) for tool in job.toolchain if tool in INSTALL_ARGV
    ]
    steps += [
        (step.workdir, _step_argv(step, job, _AS_WRITTEN, options, host)) for step in job.command
    ]
    return steps


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def github_hosted_linux(host: Host) -> bool:
    """Whether run-job runs on a GitHub-hosted Linux runner, as its own process environment says:
    on GitHub, ``RUNNER_ENVIRONMENT`` is ``github-hosted`` (a self-hosted runner says
    ``self-hosted``) and the image names itself (``ImageOS``), on Linux. Nothing a step sets
    reaches this process, and a caller here is not on GitHub."""
    return (
        host.on_github
        and _is_linux()
        and os.environ.get("RUNNER_ENVIRONMENT") == SANDBOX_HELPER_HOSTED
        and bool(os.environ.get("ImageOS"))
    )


def _helper_env(argv: Sequence[str], host: Host) -> dict[str, str]:
    """:data:`SANDBOX_HELPER_ENV` for a command that runs the sandbox helper, on a GitHub-hosted
    Linux runner; nothing for any other command or host."""
    runs_it = tuple(argv[:2]) in (INSTALL_ARGV["electron"], SANDBOX_HELPER_STEP)
    if runs_it and github_hosted_linux(host):
        return {SANDBOX_HELPER_ENV: SANDBOX_HELPER_HOSTED}
    return {}


def runner_of(job: Job, host: Host) -> dict[str, str]:
    """What a record says *job* ran on (``verdict.RUNNER_KEYS``): its catalogue runner label; the
    image, which is the container's for a job in one, ``<ImageOS>/<ImageVersion>`` on a
    GitHub-hosted runner (empty when either is unset, as on a self-hosted one) and empty for a
    bare host here; and this machine's OS and architecture. A push replays a pass only on the
    image it runs now (``tools/ci/main_replay.py``), and an empty one never."""
    image = ""
    if job.container is not None:
        image = job.container
    elif host.on_github and os.environ.get("ImageOS") and os.environ.get("ImageVersion"):
        image = f"{os.environ['ImageOS']}/{os.environ['ImageVersion']}"
    return {
        "label": job.platform,
        "image": image,
        "os": sys.platform,
        "arch": host_platform.machine(),
    }


def _toolchain(
    job: Job, host: Host, steps: Sequence[tuple[str, Sequence[str]]], seen: dict[str, str]
) -> dict[str, str]:
    """The Python the job asks for; each program its toolchain names; and each program an install
    or a step starts (``program:<name>``) by a bare name or an absolute path, which is no file of
    the tree. A program named by a path in the checkout is the tree's, which the key holds."""
    out = {"python": job.python or ""}
    for tool in job.toolchain:
        program = PROGRAMS.get(tool) or INSTALL_ARGV.get(tool, ("",))[0]
        if program:
            out[tool] = _identity(program, host, seen)
    for _, argv in steps:
        program = argv[0]
        if os.path.isabs(program) or "/" not in program:
            out[f"program:{program}"] = _identity(program, host, seen)
    return out


def _base_commit(host: Host, options: Options) -> str:
    """The commit a step's ``{base}`` diffs from in this run (``--base``, or the merge-base with
    ``origin/main``), as its full object id."""
    base = _base(host, options)
    if not base.startswith("-"):
        try:
            return _git(host, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").strip()
        except Unreadable:
            pass
    raise Unreadable(f"the base {base!r} names no commit, so a job that diffs from it is not keyed")


def _key_inputs(
    cat: Catalogue,
    job: Job,
    host: Host,
    tree: str,
    host_capabilities: Mapping[str, str],
    options: Options,
    seen: dict[str, str] | None = None,
) -> verdict_store.KeyInputs:
    """Everything *job*'s result on this host depends on, as this run runs it, as the store keys it.

    The entry is the job as the catalogue was read (its ``repr``: every field, its env and
    steps included), and the catalogue's version the digest of every job's. The argv is each
    install, and each step as this run runs it, a local ``--workers`` in its pytest worker count,
    with the folder it runs in; its placeholders stay unfilled (:data:`_AS_WRITTEN`). A sharded
    job's selection is its shard count: the offline plan splits the tree's own test files, which
    the tree already keys. The declared inputs are the job's env, the worker count its steps get,
    and the commit ``{base}`` names when a step diffs from it. Locks, the toolchain and each
    step's own program, the platform, the container and the host's capabilities complete it."""
    seen = {} if seen is None else seen
    steps = _as_written(job, options, host)
    locks = {
        rel: _sha256((host.root / rel).read_bytes()) for rel in LOCKS if (host.root / rel).is_file()
    }
    declared = {f"env:{name}": value for name, value in job.env.items()}
    workers = _worker_count(job, options, host)
    if workers is not None:
        declared["workers"] = workers
    if any("base" in step.placeholders() for step in job.command):
        declared["base"] = _base_commit(host, options)
    return verdict_store.KeyInputs(
        job_id=job.id,
        entry_text=repr(job),
        catalogue_version=_sha256(repr((cat.jobs, cat.merge_jobs)).encode()),
        argv=tuple((workdir or ".", *argv) for workdir, argv in steps),
        selection_digest=_sha256(f"{job.id}:shards={job.shards}".encode()),
        tree_oid=tree,
        external_revisions=external_revisions([job], host),
        lock_digests=locks,
        toolchain=_toolchain(job, host, steps, seen),
        os=sys.platform,
        arch=host_platform.machine(),
        runner=job.container or "",
        declared=declared,
        capabilities=dict(host_capabilities),
    )


def _capabilities(
    job: Job,
    host: Host,
    options: Options,
    fingerprint: Fingerprint,
    memo: dict[frozenset[tuple[str, str]], dict[str, str]],
) -> dict[str, str]:
    """The host's capabilities for *job*, each read with the environment one of its steps gets and
    a home that is none of the user's (a job's own home is a new scratch folder with nothing in
    it). One every step reads alike is keyed by its name; one its steps read differently, once per
    step, as ``step <n>: <name>``. *memo* holds each environment already read in this run."""
    home = host.root / WORK / "no-home"
    common = {**_base_env(job, host, options), **job.env}
    readings = []
    for environ in [{**common, **step.env} for step in job.command] or [common]:
        seen = frozenset(environ.items())
        if seen not in memo:
            memo[seen] = dict(fingerprint(host.root, path=host.path, environ=environ, home=home))
        readings.append(memo[seen])
    out: dict[str, str] = {}
    for name in sorted(set().union(*readings)):
        values = [reading.get(name) for reading in readings]
        if all(value == values[0] for value in values) and values[0] is not None:
            out[name] = values[0]
        else:
            for n, value in enumerate(values, 1):
                out[f"step {n}: {name}"] = value if value is not None else "(not read)"
    return out


@dataclass
class _Reuse:
    """A run's use of the store: the jobs it plans, its start, the receipt decided before any job
    ran, and what each job that was not reused recorded. ``off`` says why the store is not used
    at all."""

    jobs: Sequence[Job] = ()
    start: verdict_store.Start | None = None
    store: verdict_store.Store | None = None
    inputs: dict[str, verdict_store.KeyInputs] = field(default_factory=dict)
    receipt: verdict_store.Receipt | None = None
    recorded: dict[str, str] = field(default_factory=dict)
    off: str | None = None

    def reused(self, job_id: str) -> verdict_store.Decision | None:
        for decision in self.receipt.jobs if self.receipt else ():
            if decision.id == job_id and decision.reused:
                return decision
        return None

    def line(self, job_id: str) -> str | None:
        """The receipt's line for *job_id* (:meth:`verdict_store.Receipt.lines`), as the run prints
        it; None when reuse is off."""
        for decision, text in zip(self.receipt.jobs, self.receipt.lines()) if self.receipt else ():
            if decision.id == job_id:
                return text
        return None


def _begin_reuse(
    cat: Catalogue,
    jobs: Sequence[Job],
    host: Host,
    options: Options,
    fingerprint: Fingerprint,
    now: float,
) -> _Reuse:
    """Read the checkout once, as the run begins, and decide each planned job from it.

    Whatever keeps a key from being built turns reuse off for the run, with the reason: a checkout
    or a file that cannot be read, a probe the fingerprint cannot name, a ``{base}`` that names no
    commit. Every job then runs, and nothing is recorded. With ``--no-reuse`` no recorded pass is
    read, and each clean pass is still recorded."""
    try:
        start = verdict_store.begin(host.root)
    except OSError as exc:
        return _Reuse(jobs, off=f"the checkout cannot be read: {exc}")
    try:
        if start.tree is None:
            raise verdict_store.Refused("the checkout's tree cannot be read")
        store = verdict_store.Store.for_repo(host.root)
        memo: dict[frozenset[tuple[str, str]], dict[str, str]] = {}
        seen: dict[str, str] = {}
        inputs = {}
        for job in jobs:
            found = _capabilities(job, host, options, fingerprint, memo)
            inputs[job.id] = _key_inputs(cat, job, host, start.tree, found, options, seen)
        receipt = verdict_store.decide(
            jobs, inputs, store, now=now, start=start, replay=options.reuse
        )
    except (verdict_store.Refused, capabilities.Refused, Unreadable, OSError) as exc:
        return _Reuse(jobs, start, off=str(exc))
    return _Reuse(jobs, start, store, inputs, receipt)


def _record_pass(reuse: _Reuse, job: Job, outcomes: Sequence[Outcome], now: float) -> None:
    """Record *job*'s pass in the store when its class is one the store reuses and every shard of
    it ran here, passed and left its record; and say in the receipt what was recorded, or why
    nothing was. *outcomes* are the job's own, one per shard."""
    reuse.recorded[job.id] = _recorded(reuse, job, outcomes, now)


def _recorded(reuse: _Reuse, job: Job, outcomes: Sequence[Outcome], now: float) -> str:
    if reuse.store is None or reuse.start is None or reuse.receipt is None:
        return f"not recorded: reuse is off: {reuse.off}"
    never = verdict_store.never_reason(job)
    if never is not None:
        return f"not recorded: {never}"
    if reuse.start.dirty:
        return "not recorded: the checkout was dirty when the run began"
    if len(outcomes) != job.shards or not all(o.ran for o in outcomes):
        return "not recorded: " + (
            "it did not run here" if job.shards == 1 else "not every shard of it ran here"
        )
    if any(o.code == run.RED for o in outcomes):
        return "not recorded: it is red"
    if any(o.code != run.OK or o.record is None for o in outcomes):
        return "not recorded: it left no record of a pass"
    records = [json.loads(o.record.read_text(encoding="utf-8")) for o in outcomes if o.record]
    heads = {r["head_sha"] for r in records}
    result = verdict_store.Result(
        verdict=verdict.SUCCESS if all(r["result"] == verdict.SUCCESS for r in records) else "",
        sha=heads.pop() if len(heads) == 1 else "",
        seconds=sum(step["seconds"] for r in records for step in r["steps"]),
        skipped=tuple(sorted({test for o in outcomes for test in o.skipped})),
        junit=job.junit[0] if job.junit else "",
        rerun=any(r["rerun"] is not None for r in records),
        quarantined=tuple(t for r in records for t in r["quarantined"]),
    )
    try:
        done = reuse.store.record(reuse.start, reuse.inputs[job.id], result, now=now)
    except OSError as exc:
        return f"not recorded: the store cannot be written: {exc}"
    return "recorded" if done.recorded else f"not recorded: {done.reason}"


def _write_receipt(work: Path, reuse: _Reuse, scratch: Path | None, *, kept: bool) -> None:
    work.mkdir(parents=True, exist_ok=True)
    if reuse.receipt is None:
        why = reuse.off or "no receipt"
        data: dict[str, object] = {
            "off": why,
            # A checkout that could not be read is not known to be clean.
            "dirty": reuse.start.dirty if reuse.start is not None else True,
            "jobs": [
                {
                    "id": job.id,
                    "decision": "run",
                    "reuse": str(verdict_store.reuse_class(job)),
                    "key": None,
                    "inputs": None,
                    "reason": f"reuse is off: {why}",
                    "source": None,
                    "ttl_age_seconds": None,
                }
                for job in reuse.jobs
            ],
        }
    else:
        data = reuse.receipt.to_json()
    data["recorded"] = dict(sorted(reuse.recorded.items()))
    # Where the jobs' homes and temporary folders were, outside the checkout, and whether they
    # are still there (``--keep-scratch``); null for a run whose scratch is the run's folder.
    data["scratch"] = {"path": str(scratch) if scratch else None, "kept": bool(scratch) and kept}
    (work / RECEIPT).write_text(json.dumps(data, sort_keys=True, indent=1) + "\n")


def _summary(judged: verdict.Verdict, outcomes: Sequence[Outcome]) -> str:
    """The run's verdict as Markdown: :func:`verdict.summary`'s, except that a run short only of
    jobs this host could not run is incomplete (:func:`could_not_run`), naming each one's cause,
    and a green run with a reused job says it is green on reuse and names each one. A reused job
    did not run on this candidate: it stands on a recorded clean pass of the same tree and
    inputs."""
    reused = [o.key for o in outcomes if o.reused]
    short = [] if judged.green else could_not_run(outcomes, judged)
    if short:
        return _incomplete(short, judged)
    if not judged.green or not reused:
        return verdict.summary(judged)
    listed = verdict.tests_summary(judged.tests)
    named = ", ".join(f"`{key}`" for key in reused)
    stands = f"a recorded clean pass of the same tree and inputs: {named}"
    if all(o.reused for o in outcomes):
        said = f"No planned job ran on this candidate: each stands on {stands}."
    else:
        said = (
            "Every planned job that ran passed on this candidate, and each other stands on"
            f" {stands}."
        )
    return f"### Verdict: green on reuse\n\n{said}\n{listed}"


# ── a tier ───────────────────────────────────────────────────────────────────────────────────


def ordered(jobs: Sequence[Job]) -> list[Job]:
    """``jobs`` in catalogue order, each after the jobs its ``after`` names among them."""
    ids = {j.id for j in jobs}
    done: list[Job] = []
    placed: set[str] = set()
    pending = list(jobs)
    while pending:
        ready = [j for j in pending if all(a in placed or a not in ids for a in j.after)]
        if not ready:
            raise Unreadable(f"the jobs' after makes a cycle: {', '.join(j.id for j in pending)}")
        job = ready[0]
        done.append(job)
        placed.add(job.id)
        pending.remove(job)
    return done


def _changed(host: Host, base: str, untracked: bool = False) -> list[str]:
    """Every path the diff from ``base`` touches: both sides of a move, since the job whose
    inputs lost the file changed too, and each name as it is, never quoted. A working-tree run
    adds the untracked files git does not ignore, since its steps run on them too."""
    out = _git(host, "diff", "--name-only", "--no-renames", "-z", base)
    paths = {p for p in out.split("\0") if p}
    if untracked:
        listed = _git(host, "ls-files", "--others", "--exclude-standard", "-z")
        paths |= {p for p in listed.split("\0") if p}
    return sorted(paths)


def run_tier(
    cat: Catalogue,
    tier: str,
    host: Host,
    options: Options,
    work: Path,
    *,
    changed: bool = False,
    overrides: Sequence[catalogue.Override] = (),
    fail_fast: bool = False,
    fingerprint: Fingerprint = capabilities.fingerprint,
    pre_push: bool = False,
) -> tuple[int, list[Outcome], verdict.Verdict]:
    """Run every job of ``tier`` here, and judge each job's records against the head and the
    partner revisions it reads. ``changed``: only the jobs the diff from the base plans, with
    the area map's ``overrides``.

    Outside a working-tree run the verdict store is read first (:func:`_begin_reuse`): a job it
    holds a clean pass of on this tree is not run, and counts by its receipt
    (:func:`verdict_store.judge_reused`), since it leaves no job record. Each job of a reusable
    class that passed is recorded. The receipt goes to ``work/`` :data:`RECEIPT`.
    ``fingerprint`` reads the host's capabilities, for a test to stand in for. ``pre_push``: only
    the jobs whose entries leave them to the pre-push hook (``prepush``), as it runs them.

    With ``host.memory`` each job starts only once the machine has the memory it needs
    (tools/ci/admission.py): a job that never gets it is not run (partial). Each job that ran
    records its peak for the next run to reckon by."""
    jobs = list(cat.tier(tier))
    if not jobs:
        raise Unreadable(f"the {tier} tier has no job in the catalogue, so a run proves nothing")
    for job in jobs:
        _owned_env(job)
    if changed:
        paths = _changed(host, _base(host, options), options.working_tree)
        chosen = plan.plan(cat, tier, paths, overrides=overrides, root=host.root)
        for reason in chosen.full_reasons:
            print(f"plan: the whole tier: {reason}", file=sys.stderr)
        jobs = [j for j in jobs if j.id in chosen.jobs]
    if pre_push:
        left = sorted(j.id for j in jobs if not j.prepush)
        if left:
            print(f"pre-push: left to CI (prepush = false): {', '.join(left)}", file=sys.stderr)
        jobs = [j for j in jobs if j.prepush]
    # The change tier's jobs first, static's lint among them: the catalogue lists the suite
    # first for merge.yml's caps, and a run here still gives a pull request's quick signal first.
    jobs = ordered(sorted(jobs, key=lambda j: "change" not in j.tiers))
    now = time.time()
    reuse = (
        None if options.working_tree else _begin_reuse(cat, jobs, host, options, fingerprint, now)
    )
    if reuse is not None and reuse.off is not None:
        print(
            f"reuse is off: {reuse.off}; every job runs, and nothing is recorded", file=sys.stderr
        )
    head, _ = _state(host)
    # What each job's records must name: the head, and the partner revisions that job reads.
    expected = {job.id: verdict.Identity(head, external_revisions([job], host)) for job in jobs}
    outcomes: list[Outcome] = []
    planned: dict[str, list[str]] = {}
    memory = host.memory
    peaks = admission.Peaks(_peaks_path(host) if memory is not None else None)
    try:
        for job in jobs:
            for shard in range(1, job.shards + 1) if job.shards > 1 else [None]:
                planned.setdefault(job.id, []).append(key_of(job, shard))
                if fail_fast and any(o.code == run.RED for o in outcomes):
                    outcomes.append(
                        Outcome(
                            key_of(job, shard), run.PARTIAL, ["not run: --fail-fast"], ran=False
                        )
                    )
                    continue
                if any(o.interrupted for o in outcomes):
                    outcomes.append(
                        Outcome(
                            key_of(job, shard), run.PARTIAL, ["not run: interrupted"], ran=False
                        )
                    )
                    continue
                # Each outcome says its job's receipt line first: reused, or why it ran.
                line = reuse.line(job.id) if reuse is not None else None
                if reuse is not None and reuse.reused(job.id) is not None and line is not None:
                    outcomes.append(Outcome(key_of(job, shard), run.OK, [line], reused=True))
                    continue
                if memory is not None and not cannot_run_here(
                    job, host, working_tree=options.working_tree
                ):
                    need, how = admission.need_mb(
                        workers=max(1, int(_worker_count(job, options, host) or 1)),
                        peak_mb=peaks.get(job.id),
                        declared_gb=job.mem_gb,
                    )
                    refused = admission.admit(
                        key_of(job, shard), need, how, memory, wait_s=options.admission_wait
                    )
                    if refused is not None:
                        outcomes.append(
                            Outcome(key_of(job, shard), run.PARTIAL, [refused], ran=False)
                        )
                        continue
                try:
                    outcome = run_one(cat, job, shard, host, options, work)
                finally:
                    if memory is not None:
                        admission.release(memory)
                if outcome.ran and outcome.peak_mb is not None:
                    peaks.record(job.id, outcome.peak_mb)
                if line is not None and outcome.ran:
                    outcome.reasons.insert(0, line)
                outcomes.append(outcome)
            if reuse is not None and reuse.reused(job.id) is None:
                own = [o for o in outcomes if o.key in planned[job.id]]
                _record_pass(reuse, job, own, time.time())
    finally:
        scratch = release_scratch(work, keep=options.keep_scratch)
    if scratch is not None and options.keep_scratch:
        print(f"run: the jobs' scratch folders are kept in {scratch}", file=sys.stderr)
    if reuse is not None:
        _write_receipt(work, reuse, scratch, kept=options.keep_scratch)
    if options.working_tree:
        # No job left a record, so nothing is judged: the run says what its steps did.
        unjudged = verdict.Reason("", WORKING_TREE, "no record was written, so none is judged")
        return _tier_code(outcomes), outcomes, verdict.Verdict(green=False, reasons=(unjudged,))
    records = [o.record.read_text(encoding="utf-8") for o in outcomes if o.record is not None]
    results = verdict.from_job_records(records)
    reasons: list[verdict.Reason] = []
    tests: list[verdict.FailedTest] = []
    counted: set[str] = set()
    if reuse is not None and reuse.receipt is not None and reuse.start is not None:
        try:
            counted, stale = verdict_store.judge_reused(
                reuse.receipt, jobs, tree=str(reuse.start.tree)
            )
        except verdict_store.Refused as exc:
            reasons.append(verdict.Reason("", "reuse refused", str(exc)))
        else:
            reasons.extend(stale)
    for job_id, keys in planned.items():
        if job_id in counted:
            continue  # reused: judged by its receipt, above
        mine = [r for r in results if r.key in keys]
        each = verdict.decide(keys, mine, expected[job_id])
        reasons.extend(each.reasons)
        tests.extend(each.tests)
    judged = verdict.Verdict(green=not reasons, reasons=tuple(reasons), tests=tuple(tests))
    return _tier_code(outcomes, unproven=not judged.green), outcomes, judged


def _peaks_path(host: Host) -> Path | None:
    """Where this clone keeps its jobs' peak memory: beside the verdict store, in the git common
    folder every worktree shares; ``None`` outside a clone."""
    try:
        return verdict_store.Store.for_repo(host.root).root.parent / admission.PEAKS_FILE
    except verdict_store.Refused:
        return None


#: The kind of the one reason a working-tree run's verdict gives: a note that nothing was
#: judged, not a fault of any job.
WORKING_TREE = "working tree"


def could_not_run(outcomes: Sequence[Outcome], judged: verdict.Verdict) -> list[Outcome]:
    """The jobs this host could not run, when they are the whole of why the run is not green, each
    with the cause :func:`cannot_run_here` named; empty otherwise. A job that is not green for any
    other reason (red, not run for ``--fail-fast``, an interruption or memory, or run with no
    record), or a reason the plan or reuse gave, leaves the run's own verdict, red over this."""
    here = [o for o in outcomes if o.not_here]
    if not here or not all(o.reasons for o in here):
        return []
    if any(o.code != run.OK for o in outcomes if not o.not_here):
        return []
    keys = {o.key for o in here}
    faults = [r for r in judged.reasons if r.kind != WORKING_TREE]
    if any(r.key not in keys or r.kind != "missing" for r in faults):
        return []
    return here


def _jobs(n: int) -> str:
    return f"{n} job" if n == 1 else f"{n} jobs"


def _incomplete(short: Sequence[Outcome], judged: verdict.Verdict) -> str:
    """The summary of a run short only of the jobs in *short*, which this host could not run."""
    prefix = "cannot run here: "
    lines = [
        "### Verdict: incomplete",
        "",
        f"{_jobs(len(short))} could not run here, so this run is no verdict on"
        f" {'it' if len(short) == 1 else 'them'}, and never green. Every other planned job"
        " passed.",
        "",
    ]
    for o in short:
        causes = [r.removeprefix(prefix) for r in o.reasons if r.startswith(prefix)]
        lines.append(f"- `{o.key}`: {'; '.join(causes)}")
    return "\n".join(lines) + "\n" + verdict.tests_summary(judged.tests)


def _last_line(code: int, outcomes: Sequence[Outcome], judged: verdict.Verdict) -> str:
    """The run's last line: ``incomplete: N jobs could not run here`` for a partial run short only
    of jobs this host could not run, else its exit's word."""
    short = [] if code != run.PARTIAL else could_not_run(outcomes, judged)
    if short:
        return f"incomplete: {_jobs(len(short))} could not run here"
    return {run.OK: "ok", run.RED: "red", run.PARTIAL: "partial"}[code]


def _tier_code(outcomes: Sequence[Outcome], *, unproven: bool = False) -> int:
    """A tier's exit, in either mode: red over partial, so a red job is never reported as one
    that could not run; partial for a job that could not run here or was interrupted; and when
    every job passed, red if its records are ``unproven`` (the verdict is not green), else ok."""
    if any(o.code == run.RED for o in outcomes):
        return run.RED
    if any(o.code != run.OK for o in outcomes):
        return run.PARTIAL
    return run.RED if unproven else run.OK


# ── the command line ─────────────────────────────────────────────────────────────────────────


class _Parser(argparse.ArgumentParser):
    """A parser that refuses with exit 2, and takes each option spelled in full only: no prefix
    stands in for an option, so a later option can never change what a short spelling meant
    (:func:`_parser` builds it with ``allow_abbrev=False``)."""

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{self.prog}: {message}", file=sys.stderr)
        raise SystemExit(run.PARTIAL)


class _OnceFlag(argparse.Action):
    """A flag. Given twice, it is refused rather than taken once, as every option of the two
    verbs is (the valued ones by :class:`verdict._Once`)."""

    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: object) -> None:
        kwargs.pop("nargs", None)
        kwargs.pop("default", None)
        super().__init__(
            option_strings, dest, nargs=0, default=False, **kwargs  # type: ignore[arg-type]
        )

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, False):
            raise argparse.ArgumentError(self, "given twice")
        setattr(namespace, self.dest, True)


def _parser(prog: str, description: str) -> argparse.ArgumentParser:
    """The options run and run-job share. Each is given once and spelled in full: one given
    twice is refused, not last-wins, as the verdict's own command line refuses it, and no prefix
    stands in for one."""
    once = verdict._Once
    parser = _Parser(prog=f"python -m tools.ci {prog}", description=description, allow_abbrev=False)
    parser.add_argument("--catalogue", type=Path, action=once, help="the catalogue to read")
    parser.add_argument("--base", action=once, help="the ref a diff is taken against")
    parser.add_argument(
        "--workers", type=int, action=once, help="a local worker count in place of the catalogue's"
    )
    parser.add_argument("--out", type=Path, action=once, help="the run's folder (scratch, records)")
    parser.add_argument(
        "--working-tree",
        action=_OnceFlag,
        help="run on the checkout as it stands, uncommitted changes included: no record, and"
        " the exit is the steps' result",
    )
    parser.add_argument(
        "--keep-scratch",
        action=_OnceFlag,
        help="keep the jobs' homes and temporary folders, which a run whose folder is in the"
        " checkout makes in the system's temporary folder and removes when it ends",
    )
    return parser


def _catalogue_path(args: argparse.Namespace) -> Path:
    return Path(args.catalogue or catalogue.REPO / catalogue.CATALOGUE)


def _setup(args: argparse.Namespace) -> tuple[Catalogue, Host, Options, Path]:
    root = catalogue.REPO
    path = _catalogue_path(args)
    cat = catalogue.load(catalogue._text(path, str(path)), str(path))
    host = Host.this(root)
    if args.workers is not None and args.workers < 1:
        raise Unreadable("--workers is a whole number from 1")
    if args.workers is not None and host.on_github:
        raise Unreadable("--workers is a local override; on GitHub the catalogue's count runs")
    try:
        # Each step asks too (run.run_job); asked here first, so no step is announced in vain.
        run.real_home()
    except RuntimeError as exc:
        raise Unreadable(str(exc)) from None
    work = args.out or root / WORK / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    if args.working_tree and host.on_github:
        raise Unreadable(
            "--working-tree is a local mode: on GitHub the checkout is the commit under test"
        )
    options = Options(
        base=args.base,
        workers=args.workers,
        working_tree=args.working_tree,
        reuse=not getattr(args, "no_reuse", False),
        admission_wait=getattr(args, "admission_wait", None) or admission.WAIT_S,
        keep_scratch=bool(args.keep_scratch),
    )
    return cat, host, options, work


def _say(outcomes: Sequence[Outcome]) -> None:
    for o in outcomes:
        word = "reused" if o.reused else {run.OK: "passed", run.RED: "red"}.get(o.code, "partial")
        print(f"{o.key}: {word}" + (f" (record {o.record})" if o.record else ""))
        for reason in o.reasons:
            print(f"  {reason}")
    missing = [o.key for o in outcomes if not o.ran]
    if missing:
        print(f"not proven here: {', '.join(missing)}")
    moved = [f"{o.key} ({o.off_platform} in CI)" for o in outcomes if o.off_platform]
    if moved:
        print(
            f"ran off their platform family: {', '.join(moved)}. A local result is never CI's"
            " evidence: CI runs each job on its own platform."
        )


def run_job_main(argv: list[str]) -> int:
    """``python -m tools.ci run-job <id> [--shard i/N]``."""
    parser = _parser(
        "run-job", "Run one catalogue job here, and leave its record (none with --working-tree)."
    )
    parser.add_argument("job")
    parser.add_argument("--shard", action=verdict._Once, help="i/N, for a sharded job")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # A refused option is input it cannot run; -h is too, since it judged nothing.
        return exc.code if isinstance(exc.code, int) and exc.code else run.PARTIAL
    try:
        cat, host, options, work = _setup(args)
        try:
            job = cat.job(args.job)
        except KeyError as exc:
            raise Unreadable(str(exc.args[0])) from None
        shard: int | None = None
        if job.shards > 1:
            match = _SHARD.fullmatch(args.shard or "")
            if not match or int(match.group(2)) != job.shards or int(match.group(1)) > job.shards:
                raise Unreadable(
                    f"{job.id} runs in {job.shards} shards: give --shard i/{job.shards}"
                )
            shard = int(match.group(1))
        elif args.shard is not None:
            raise Unreadable(f"{job.id} is not sharded, so it takes no --shard")
        try:
            outcome = run_one(cat, job, shard, host, options, work)
        finally:
            scratch = release_scratch(work, keep=options.keep_scratch)
        if scratch is not None and options.keep_scratch:
            print(f"run-job: the job's scratch folder is kept in {scratch}", file=sys.stderr)
    except (Unreadable, catalogue.CatalogueError, ValueError, OSError) as exc:
        problems = exc.problems if isinstance(exc, catalogue.CatalogueError) else [str(exc)]
        for problem in problems:
            print(f"run-job: {problem}", file=sys.stderr)
        return run.PARTIAL
    _say([outcome])
    return outcome.code


def run_main(argv: list[str]) -> int:
    """``python -m tools.ci run <tier> [--changed] [--fail-fast] [--no-reuse] [--pre-push]
    [--admission-wait SECONDS]``."""
    parser = _parser(
        "run", "Run every job of a tier here, and judge their records (none with --working-tree)."
    )
    parser.add_argument("tier", help=f"one of {', '.join(catalogue.TIERS)}")
    parser.add_argument("--changed", action=_OnceFlag, help="only the jobs the diff touches")
    parser.add_argument("--fail-fast", action=_OnceFlag, help="stop after the first red job")
    parser.add_argument(
        "--no-reuse",
        action=_OnceFlag,
        help="run every job, reusing no recorded pass; each clean pass is still recorded",
    )
    parser.add_argument(
        "--pre-push",
        action=_OnceFlag,
        help="only the jobs the pre-push hook runs: those whose entries do not say prepush = false",
    )
    parser.add_argument(
        "--admission-wait",
        type=float,
        action=verdict._Once,
        help=f"how long a job may wait for memory before it is not run, in seconds (default "
        f"{admission.WAIT_S:g})",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # A refused option is input it cannot run; -h is too, since it judged nothing.
        return exc.code if isinstance(exc.code, int) and exc.code else run.PARTIAL
    try:
        if args.tier not in catalogue.TIERS:
            raise Unreadable(f"{args.tier!r} is not a tier: one of {', '.join(catalogue.TIERS)}")
        if args.admission_wait is not None and not args.admission_wait > 0:
            raise Unreadable("--admission-wait is a number of seconds above 0")
        cat, host, options, work = _setup(args)
        overrides = plan.read_beside(_catalogue_path(args), cat) if args.changed else ()
        code, outcomes, judged = run_tier(
            cat,
            args.tier,
            host,
            options,
            work,
            changed=args.changed,
            overrides=overrides,
            fail_fast=args.fail_fast,
            pre_push=args.pre_push,
        )
    except (Unreadable, catalogue.CatalogueError, ValueError, OSError) as exc:
        problems = exc.problems if isinstance(exc, catalogue.CatalogueError) else [str(exc)]
        for problem in problems:
            print(f"run: {problem}", file=sys.stderr)
        return run.PARTIAL
    _say(outcomes)
    if options.working_tree:
        print("working tree: no record was written, so this run is no evidence for a commit")
    else:
        print(_summary(judged, outcomes), end="")
    print(_last_line(code, outcomes, judged))
    return code
