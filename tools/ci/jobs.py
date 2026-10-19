"""Run the catalogue's jobs on this machine: ``python -m tools.ci run-job`` and ``run``.

``run-job <id>`` runs one job of ``tools/ci/catalogue.toml``, or one shard of it, the way CI's job
runs it: its toolchain's installs, then each of its steps, token for token, through
:func:`tools.ci.run.run_job`. Every step gets the job's :class:`~tools.ci.run.Scratch` and an
environment built from nothing (:func:`tools.ci.run.job_env`): what a GitHub runner gives every
step (:data:`RUNNER_ENV`, ``CI=true``), the workflow's env, then the job's, then the step's, never
the caller's beyond its ``PATH``. A value only the runner knows is filled in: ``{runner_temp}`` is
the job's own temporary folder (``Scratch.tmp``; on GitHub, ``$RUNNER_TEMP``), ``{shard}`` the
shard asked for, ``{base}`` the ref a diff is taken against.

The job has its catalogue cap less a minute, across every step; a step still running when that
runs out is stopped with its whole group, and a pytest step says first which of its processes
were still alive, since a hung xdist worker is the usual cause. Every report the job declares
must then exist and be newer than the job's start, so a pytest that died before writing one is
red.

Each job (each shard of a sharded one) leaves one record (:func:`verdict.job_record`): its key,
result, the head it ran on, the partner revisions it read, whether it needed a rerun (never,
here), which quarantined tests failed, and each step's argv, exit and seconds. A record is left
only when the checkout was clean, at the same head, before and after the job, and each partner
checkout the job reads was at the same head before and after it: otherwise the head or partner
revision it would name is not what it ran.

``run <tier>`` runs every job of a tier in the catalogue's order, each after the jobs its
``after`` names, every shard of each, and judges each job's records with :func:`verdict.decide`
against the head and the partner revisions that job reads. A tier with no job is input it cannot
run. A job for another platform family, in a container, or needing a toolchain or partner
checkout this machine lacks, cannot run here: it is named, and the run is PARTIAL, never green.
After an interrupted job nothing more runs. ``--changed`` runs the jobs
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

A local result never stands in for GitHub's: nothing reads these records yet, and reuse of a
recorded pass is not wired. A local ``--workers N`` replaces the catalogue's worker count, is
refused on GitHub, and is written in the record.

Standard library only, like every ``tools/ci`` module.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import catalogue, plan, run, shards, verdict
from .catalogue import Catalogue, Job, Step

#: What a GitHub-hosted runner gives every step that a step run here gets too: ``CI``, which the
#: e2e job's Playwright config reads for its JSON report, retries and server. ``GITHUB_ACTIONS``
#: is not here: it says the run is on GitHub (:attr:`Host.on_github`). A rails test holds this to
#: its declared list.
RUNNER_ENV: dict[str, str] = {"CI": "true"}

#: The env ci.yml gives every job, at the workflow's top level. A test holds it to ci.yml's.
WORKFLOW_ENV: dict[str, str] = {"UV_PYTHON_PREFERENCE": "only-managed"}

#: Each toolchain word whatever runs a job installs, as ci.yml installs it.
INSTALL_ARGV: dict[str, tuple[str, ...]] = {
    "uv-sync": ("uv", "sync", "--locked", "--extra", "dev"),
    "npm-ci": ("npm", "ci"),
    "chromium": ("npx", "playwright", "install", "--with-deps", "chromium"),
}

#: Each toolchain word that is a program this machine must already have.
PROGRAMS: dict[str, str] = {
    "uv": "uv",
    "node": "node",
    "tmux": "tmux",
    "shellcheck": "shellcheck",
    "dash": "dash",
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

    @classmethod
    def this(cls, root: Path) -> Host:
        family = {"darwin": "macos-", "linux": "ubuntu-"}.get(sys.platform, "")
        return cls(
            root=root,
            family=family,
            path=os.environ.get("PATH", os.defpath),
            on_github=bool(os.environ.get("GITHUB_ACTIONS")),
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
    reasons in either mode."""
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


def _base(host: Host, options: Options) -> str:
    if options.base:
        return options.base
    return _git(host, "merge-base", "HEAD", "origin/main").strip()


# ── one job ──────────────────────────────────────────────────────────────────────────────────


def _render(value: str, values: Mapping[str, str]) -> str:
    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise Unreadable(f"no value for the placeholder {{{name}}}")
        return values[name]

    return re.sub(r"\{([^{}]*)\}", fill, value)


def _workers(job: Job, options: Options) -> str | None:
    if options.workers is None:
        return None if job.workers is None else str(job.workers)
    return str(options.workers)


def _step_argv(step: Step, job: Job, values: Mapping[str, str], options: Options) -> list[str]:
    """The step's argv as it runs here: its placeholders filled, and a local ``--workers`` in
    each worker count it passes pytest, in each spelling :func:`catalogue.pytest_workers` reads
    (``-n N``, ``-nN``, ``--numprocesses N``, ``--numprocesses=N``)."""
    argv = [_render(a, values) for a in step.argv]
    if options.workers is None or "pytest" not in argv:
        return argv
    count, local = str(job.workers), str(options.workers)
    for i in range(argv.index("pytest") + 1, len(argv)):
        if argv[i] in ("-n", "--numprocesses") and argv[i + 1 : i + 2] == [count]:
            argv[i + 1] = local
        elif argv[i] == f"--numprocesses={count}":
            argv[i] = f"--numprocesses={local}"
        elif argv[i] == f"-n{count}":
            argv[i] = f"-n{local}"
    # The record says the local count, so no step may run another.
    if any(n != local for n in catalogue.pytest_workers(Step(tuple(argv)))):
        raise Unreadable(f"{job.id}: --workers cannot set every pytest -n of {shlex.join(argv)}")
    return argv


def _local_env(job: Job, host: Host) -> dict[str, str]:
    """What a step run here gets that CI's new runner makes needless: the job's caches, managed
    Pythons and project venv under the work folder, kept between runs as CI restores its caches,
    never the caller's own; and ``uv venv`` replacing a venv an earlier local run left in the
    checkout, as a new checkout on CI has none. ``npm ci`` still reinstalls the checkout's own
    ``node_modules``, as it does on CI."""
    cache = host.root / WORK / "cache"
    env: dict[str, str] = {}
    if "uv" in job.toolchain or "uv-sync" in job.toolchain:
        env["UV_CACHE_DIR"] = str(cache / "uv")
        env["UV_PYTHON_INSTALL_DIR"] = str(cache / "uv-python")
        env["UV_PROJECT_ENVIRONMENT"] = str(cache / f"venv-{job.python or 'default'}")
        env["UV_VENV_CLEAR"] = "1"
        if job.python and job.python != "image":
            env["UV_PYTHON"] = job.python
    if "npm-ci" in job.toolchain:
        env["npm_config_cache"] = str(cache / "npm")
    if "chromium" in job.toolchain:
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "ms-playwright")
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

    return report


def quarantined_failures(path: Path) -> list[str]:
    """The quarantined tests a JUnit report says failed: a case carrying the quarantine's
    property that pytest recorded as an expected failure."""
    found = []
    for case in ET.parse(path).getroot().iter("testcase"):
        names = {p.get("name") for p in case.iter("property")}
        if "quarantine_issue" in names and case.find("skipped") is not None:
            found.append(f"{case.get('classname', '')}::{case.get('name', '')}")
    return found


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
        return Outcome(key, run.PARTIAL, [f"cannot run here: {w}" for w in why], ran=False)
    if options.workers is not None and host.on_github:
        raise Unreadable("--workers is a local override; on GitHub the catalogue's count runs")
    if options.working_tree and host.on_github:
        raise Unreadable(
            "--working-tree is a local mode: on GitHub the checkout is the commit under test"
        )
    head, clean = _state(host)
    revisions = external_revisions([job], host)
    slug = re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-")
    scratch = run.Scratch.create(work / "jobs" / slug)
    runner_temp = Path(os.environ["RUNNER_TEMP"]) if host.on_github else scratch.tmp
    values = {"runner_temp": str(runner_temp)}
    if shard is not None:
        values["shard"] = str(shard)
    if any("base" in step.placeholders() for step in job.command):
        values["base"] = _base(host, options)
    plan: str | None = None
    if job.shards > 1:
        if host.on_github:
            plan = "ci"
        else:
            _offline_plan(job, host, runner_temp)
            plan = "offline"
    workers = _workers(job, options)
    base_env = {**RUNNER_ENV, **WORKFLOW_ENV, **_local_env(job, host)}
    if job.workers_env and workers is not None:
        count = str(os.cpu_count() or 1) if workers == "auto" else workers
        base_env[job.workers_env] = count

    planned: list[tuple[list[str], str, dict[str, str]]] = [
        (list(INSTALL_ARGV[tool]), "", {}) for tool in job.toolchain if tool in INSTALL_ARGV
    ]
    for step in job.command:
        env = {name: _render(value, values) for name, value in {**job.env, **step.env}.items()}
        planned.append((_step_argv(step, job, values, options), step.workdir, env))

    total = (
        options.deadline_s if options.deadline_s is not None else job.timeout_min * 60 - MARGIN_S
    )
    started = time.time()
    deadline = time.monotonic() + total
    reasons: list[str] = []
    steps: list[tuple[list[str], int, float]] = []
    code = run.OK
    interrupted = False
    for n, (argv, workdir, env) in enumerate(planned, 1):
        left = deadline - time.monotonic()
        if left <= 0:
            reasons.append(f"the job's {total:g}s ran out before step {n} ({' '.join(argv)})")
            code = run.RED
            break
        print(f"[{key}] step {n}: {' '.join(argv)}", file=sys.stderr, flush=True)
        began = time.monotonic()
        result = run.run_job(
            argv,
            cwd=host.root / workdir,
            scratch=scratch,
            timeout=left,
            path=host.path,
            env={**base_env, **env},
            grace=options.grace,
            before_stop=_hang_report(host) if "pytest" in argv else None,
        )
        steps.append((argv, result.returncode, time.monotonic() - began))
        if result.code != run.OK:
            reasons.extend(f"step {n}: {v}" for v in result.violations)
            code = result.code
            interrupted = result.interrupted
            break
    quarantined: list[str] = []
    if code == run.OK:
        for report in job.junit:
            path = host.root / _render(report, values)
            if not path.is_file() or path.stat().st_mtime < started:
                reasons.append(f"wrote no junit at {path.relative_to(host.root)} in this run")
                code = run.RED
                continue
            try:
                quarantined += quarantined_failures(path)
            except (ET.ParseError, OSError) as exc:
                reasons.append(f"its junit {report} is unreadable: {exc}")
                code = run.RED

    outcome = Outcome(key, code, reasons, interrupted=interrupted)
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
    text = verdict.job_record(
        key=key,
        result=word,
        identity=verdict.Identity(head, revisions),
        steps=steps,
        rerun=False,
        quarantined=quarantined,
        plan=plan,
        workers=options.workers,
    )
    out = work / "records" / f"{slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    outcome.record = out
    return outcome


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
) -> tuple[int, list[Outcome], verdict.Verdict]:
    """Run every job of ``tier`` here, and judge each job's records against the head and the
    partner revisions it reads. ``changed``: only the jobs the diff from the base plans, with
    the area map's ``overrides``."""
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
    jobs = ordered(jobs)
    head, _ = _state(host)
    # What each job's records must name: the head, and the partner revisions that job reads.
    expected = {job.id: verdict.Identity(head, external_revisions([job], host)) for job in jobs}
    outcomes: list[Outcome] = []
    planned: dict[str, list[str]] = {}
    for job in jobs:
        for shard in range(1, job.shards + 1) if job.shards > 1 else [None]:
            planned.setdefault(job.id, []).append(key_of(job, shard))
            if fail_fast and any(o.code == run.RED for o in outcomes):
                outcomes.append(
                    Outcome(key_of(job, shard), run.PARTIAL, ["not run: --fail-fast"], ran=False)
                )
                continue
            if any(o.interrupted for o in outcomes):
                outcomes.append(
                    Outcome(key_of(job, shard), run.PARTIAL, ["not run: interrupted"], ran=False)
                )
                continue
            outcomes.append(run_one(cat, job, shard, host, options, work))
    if options.working_tree:
        # No job left a record, so nothing is judged: the run says what its steps did.
        unjudged = verdict.Reason("", "working tree", "no record was written, so none is judged")
        return _tier_code(outcomes), outcomes, verdict.Verdict(green=False, reasons=(unjudged,))
    records = [o.record.read_text(encoding="utf-8") for o in outcomes if o.record is not None]
    results = verdict.from_job_records(records)
    reasons: list[verdict.Reason] = []
    for job_id, keys in planned.items():
        mine = [r for r in results if r.key in keys]
        reasons.extend(verdict.decide(keys, mine, expected[job_id]).reasons)
    judged = verdict.Verdict(green=not reasons, reasons=tuple(reasons))
    return _tier_code(outcomes, unproven=not judged.green), outcomes, judged


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
    options = Options(base=args.base, workers=args.workers, working_tree=args.working_tree)
    return cat, host, options, work


def _say(outcomes: Sequence[Outcome]) -> None:
    for o in outcomes:
        word = {run.OK: "passed", run.RED: "red"}.get(o.code, "partial")
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
        outcome = run_one(cat, job, shard, host, options, work)
    except (Unreadable, catalogue.CatalogueError, ValueError, OSError) as exc:
        problems = exc.problems if isinstance(exc, catalogue.CatalogueError) else [str(exc)]
        for problem in problems:
            print(f"run-job: {problem}", file=sys.stderr)
        return run.PARTIAL
    _say([outcome])
    return outcome.code


def run_main(argv: list[str]) -> int:
    """``python -m tools.ci run <tier> [--changed] [--fail-fast]``."""
    parser = _parser(
        "run", "Run every job of a tier here, and judge their records (none with --working-tree)."
    )
    parser.add_argument("tier", help=f"one of {', '.join(catalogue.TIERS)}")
    parser.add_argument("--changed", action=_OnceFlag, help="only the jobs the diff touches")
    parser.add_argument("--fail-fast", action=_OnceFlag, help="stop after the first red job")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # A refused option is input it cannot run; -h is too, since it judged nothing.
        return exc.code if isinstance(exc.code, int) and exc.code else run.PARTIAL
    try:
        if args.tier not in catalogue.TIERS:
            raise Unreadable(f"{args.tier!r} is not a tier: one of {', '.join(catalogue.TIERS)}")
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
        print(verdict.summary(judged), end="")
    print({run.OK: "ok", run.RED: "red", run.PARTIAL: "partial"}[code])
    return code
