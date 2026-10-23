"""Run one CI job from an empty environment, with a home, a temporary folder and a git of its own.

This is the isolation a local run of a CI job stands on (#3920); it holds no scheduler, catalogue,
plan or reuse receipt. A job gets a scratch folder of its own (:class:`Scratch`) and an
environment built from nothing (:func:`job_env`), never from the caller's, so it sees no home,
PersonalClaw home, temporary folder, token, ssh agent or git repository variable of whoever runs
it. Its ``HOME`` is the scratch home and nothing sets ``PERSONALCLAW_HOME``, as on a CI runner, so
its PersonalClaw home is the default one, ``~/.personalclaw`` under that scratch home; a job may
not declare ``PERSONALCLAW_HOME``, so none points at a home outside its scratch folder. Its git
reads no system file and no global file but the scratch one, and every credential
helper a file names is cleared, so no helper of the caller's or of any file runs. The helper git
does run first is a tripwire: it answers nothing, and each credential request git makes fails the
run naming what was asked (:func:`run_job`), even when a helper the job names on its own git
command line answers after it.

The tripwire guards the environment and the files, not the job's own git command line: a job that
sets git's command-line configuration of its own replaces it. Inside a pytest job the suite's
conftest does that; there the suite's git guard covers git instead.

The real home is the one this account's entry in the user database names, never ``$HOME``
(:func:`real_home`). A job that names it by path is caught by what it leaves: when the real home's
``.personalclaw`` was absent as the job started and is there as it ends, the run fails. The
runner only asks whether that folder exists: it never reads, lists or changes anything under the
real home, which may be a live PersonalClaw's.

Not guarded here: what a job does under a real home's ``.personalclaw`` that was already there,
one it made and removed again before it ended, anything else in the real home, the container or
worktree a job runs in, and scheduling. A local result never stands in for GitHub's, and no
workflow reads what this writes.

Standard library only, like every ``tools/ci`` module. The git variables mirror the suite's
neutral ones (``personalclaw.sdk.testing.neutral_git_env``), which ``tools/ci`` may not import;
a test compares the two.
"""

from __future__ import annotations

import json
import math
import os
import pwd
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import IO

#: The run's exit codes: the job passed; it failed, timed out or broke its isolation; it was
#: interrupted before it finished.
OK, RED, PARTIAL = 0, 1, 2

#: How long a stopped job's group has after SIGTERM before what is left of it gets SIGKILL.
GRACE = 5.0

#: The prefix of git's configuration variables, which could name a file or a helper of the
#: caller's. A job's declared env may set none of them, nor any variable the runner sets.
_GIT_CONFIG = "GIT_CONFIG"

#: The variables a job's declared env may not set although the runner does not set them either:
#: ``PERSONALCLAW_HOME`` would point the job at a home outside its scratch folder, where the
#: default one (``~/.personalclaw`` under the scratch ``HOME``) is inside it.
_REFUSED = frozenset({"PERSONALCLAW_HOME"})

#: The variables :func:`job_env` sets, besides git's configuration ones.
_RUNNER_SETS = frozenset({"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "GIT_TERMINAL_PROMPT"})

#: The folder a job must not make in the real home: PersonalClaw's own.
_PERSONALCLAW_DIR = ".personalclaw"

#: The most of a job's output :func:`run_job` keeps when it is given an ``output`` file: what the
#: lint remediation comment shows at most (``scripts/ci_pr_feedback.mjs``'s ``MAX_VIOLATIONS``).
OUTPUT_LIMIT = 60_000
#: What a kept output that was cut ends with.
OUTPUT_CUT = b"\n... cut here: the full output is in the job's log\n"
#: How long the copy of a job's output may run on once the job's group is gone: a process that
#: left the group could hold its end of the pipe open for ever.
_COPY_JOIN_S = 10.0
#: The runner's own output, where a job's output goes as it arrives, kept or not.
_LOG_FD = 1

#: The longest value the tripwire records of a request's field.
_FIELD = 200

#: How often the runner looks at the job, its group and the signals it noted while it waits.
_POLL = 0.05

#: The credential helper git runs before any the job names. It records only the action, protocol
#: and host of what git asked, reads the rest of the request (a store or erase carries the
#: password) and drops it, and answers nothing.
_TRIPWIRE = """\
import json, os, sys

fields = {}
for line in sys.stdin:
    key, sep, value = line.rstrip("\\n").partition("=")
    if sep and key in ("protocol", "host"):
        fields[key] = value[:%(field)d]
record = {
    "action": (sys.argv[1] if len(sys.argv) > 1 else "")[:%(field)d],
    "protocol": fields.get("protocol", ""),
    "host": fields.get("host", ""),
}
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tripwire.log")
fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
with os.fdopen(fd, "a") as out:
    out.write(json.dumps(record) + "\\n")
""" % {"field": _FIELD}


@dataclass(frozen=True)
class Scratch:
    """A job's own folder: its home (which holds its PersonalClaw home, the default
    ``~/.personalclaw``, once the job makes it) and temporary folder, the global git file git makes
    on its first write, and the tripwire with what it recorded."""

    root: Path

    @classmethod
    def create(cls, root: Path) -> Scratch:
        """Make *root*, which must not exist, so no earlier job's home or record is reused. Only
        its owner can open it."""
        root = Path(root)
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        scratch = cls(root)
        for folder in (scratch.home, scratch.tmp):
            folder.mkdir(mode=0o700)
        fd = os.open(scratch.tripwire, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(fd, "w") as out:
            out.write(_TRIPWIRE)
        return scratch

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def gitconfig(self) -> Path:
        return self.root / "gitconfig"

    @property
    def tripwire(self) -> Path:
        return self.root / "tripwire.py"

    @property
    def tripwire_log(self) -> Path:
        return self.root / "tripwire.log"

    def tripwire_helper(self) -> str:
        """git's ``credential.helper`` value for the tripwire. git runs a ``!`` helper through
        the shell, so both paths are quoted; ``-I`` keeps the interpreter off any Python
        variable or user folder."""
        return f"!{shlex.quote(sys.executable)} -I {shlex.quote(str(self.tripwire))}"


def runner_owns(name: str) -> bool:
    """Whether a job's declared env may not set *name*: a variable :func:`job_env` sets, any of
    git's configuration variables, or one refused though nothing sets it. The catalogue's loader
    refuses the same names, so an entry it loads never names one."""
    return name in _RUNNER_SETS or name in _REFUSED or name.startswith(_GIT_CONFIG)


def job_env(scratch: Scratch, *, path: str, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The whole environment of a job: built from nothing, never from ``os.environ``.

    *path* is the job's ``PATH``. *env* is what the job declares; it may not replace any variable
    the runner sets (the locale among them), set any of git's configuration variables, or set
    ``PERSONALCLAW_HOME``, so a job can't undo the scrub or leave its scratch home."""
    built = {
        "PATH": path,
        "HOME": str(scratch.home),
        "TMPDIR": str(scratch.tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(scratch.gitconfig),
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": scratch.tripwire_helper(),
        "GIT_TERMINAL_PROMPT": "0",
    }
    declared = dict(env or {})
    for key in declared:
        if key in built or runner_owns(key):
            raise ValueError(f"a job's declared env may not set {key}: the runner owns it")
    built.update(declared)
    return built


@dataclass(frozen=True)
class Result:
    """How a job's run ended: :data:`OK`, :data:`RED` or :data:`PARTIAL`, the job's own exit
    status (negative for a signal, as :mod:`subprocess` gives it), each reason it isn't OK, and
    whether the runner got SIGINT or SIGTERM meanwhile (so a caller stops, even when a credential
    git asked for made the run RED)."""

    code: int
    returncode: int
    violations: tuple[str, ...]
    interrupted: bool = False


class _Noted:
    """The runner's SIGINT and SIGTERM handler while a job runs. It only notes each signal and
    never raises, so a signal that lands while Popen starts the job, or after the job has ended,
    can't escape :func:`run_job` and leave the group behind; the runner looks at what it noted
    between its waits."""

    def __init__(self) -> None:
        self.signals: list[int] = []

    def __call__(self, signum: int, _frame: FrameType | None) -> None:
        self.signals.append(signum)


def _signal_group(pgid: int, signum: int) -> None:
    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _group_alive(pgid: int) -> bool:
    """Whether group *pgid* holds a process the runner can still signal. On macOS a group whose
    processes have all exited, but are not reaped yet, answers EPERM rather than ESRCH."""
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _wait(proc: subprocess.Popen[bytes], timeout: float, noted: _Noted) -> bool:
    """Wait until the job ends, *timeout* seconds pass or the runner is signalled. Whether the
    time ran out."""
    deadline = time.monotonic() + timeout
    while not noted.signals:
        left = deadline - time.monotonic()
        if left <= 0:
            return proc.poll() is None
        try:
            proc.wait(timeout=min(left, _POLL))
        except subprocess.TimeoutExpired:
            continue
        return False
    return False


def _stop(proc: subprocess.Popen[bytes], grace: float, noted: _Noted) -> None:
    """End the job's whole process group: SIGTERM to all of it, then SIGKILL once every process
    of the group has exited or *grace* has passed, whichever is first. A signal the runner gets
    meanwhile, beyond the one that began the stop, cuts the grace short."""
    began_with = min(len(noted.signals), 1)
    _signal_group(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and len(noted.signals) <= began_with:
        if proc.poll() is not None and not _group_alive(proc.pid):
            break
        time.sleep(_POLL)
    _signal_group(proc.pid, signal.SIGKILL)
    proc.wait()


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _copy(source: IO[bytes], kept: Path, limit: int) -> None:
    """Copy a job's output to the runner's log as it arrives, and its first *limit* bytes to
    *kept*, then :data:`OUTPUT_CUT` when there was more."""
    held, cut = 0, False
    with kept.open("wb") as out:
        while chunk := os.read(source.fileno(), 65536):
            _write_all(_LOG_FD, chunk)
            room = max(limit - held, 0)
            out.write(chunk[:room])
            held += min(room, len(chunk))
            cut = cut or len(chunk) > room
        if cut:
            out.write(OUTPUT_CUT)


def _tripwire_violations(scratch: Scratch) -> list[str]:
    try:
        lines = scratch.tripwire_log.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    found = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = {}
        if not isinstance(record, dict):
            record = {}
        asked = ", ".join(str(record.get(k, "?"))[:_FIELD] for k in ("action", "protocol", "host"))
        found.append(f"the tripwire: git asked for a credential ({asked})")
    return found


def real_home() -> Path:
    """The home this account's entry in the user database names. A job's ``HOME`` is a scratch
    folder, and the runner's own may be anything, so neither can say where the real home is.
    Raises :class:`RuntimeError` for an account with no entry, or whose entry names no absolute
    path (a relative one would be looked for under the runner's own folder), rather than guess."""
    try:
        named = pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:
        raise RuntimeError(
            f"user id {os.getuid()} has no entry in the user database, so the runner can't "
            "tell where the real home is, and runs no job"
        ) from None
    if not os.path.isabs(named):
        raise RuntimeError(
            f"the user database names {named!r} as user id {os.getuid()}'s home, which isn't "
            "an absolute path, so the runner can't tell where the real home is, and runs no job"
        )
    return Path(named)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def run_job(
    argv: Sequence[str],
    *,
    cwd: Path,
    scratch: Scratch,
    timeout: float,
    path: str,
    env: Mapping[str, str] | None = None,
    grace: float = GRACE,
    before_stop: Callable[[int], None] | None = None,
    budget: float | None = None,
    output: Path | None = None,
) -> Result:
    """Run *argv* in *cwd* with :func:`job_env`, in a process group of its own, and say how it
    ended.

    A credential git asked for, or a real home's ``.personalclaw`` that was absent as the job
    started and is there as it ended, makes the run :data:`RED`, whatever else happened.
    Otherwise a SIGINT or SIGTERM the runner gets from before the job starts until its group is
    gone makes the run :data:`PARTIAL`, never :data:`OK`. Failing both, a timeout or a non-zero
    exit makes it RED.

    A timeout or a signal stops the group (:func:`_stop`), and once the job's own process has
    ended, whatever is left of its group gets SIGKILL: whatever the ending, no process of the
    group is left behind.

    *before_stop*, when given, is called with the group's id once the job has run out of time,
    before the group is stopped, so a caller can say what was still running; what it raises is
    the caller's, and the group is stopped all the same.

    Only the main thread can take SIGINT and SIGTERM, so on any other thread this raises
    :class:`RuntimeError` and starts nothing, as it does when :func:`real_home` can't be told.
    *budget* is the seconds a timeout's reason names: the time the caller gave the job, when
    *timeout* is only what is left of it as this step starts (it defaults to *timeout*). So a
    job with a 2 s budget always reads "timed out after 2s", however late a step started.

    *timeout* and *budget* must be finite numbers of seconds above 0, and *grace* a finite number
    of seconds, 0 or more.

    *output*, when given, is a file for the job's output, its stdout and stderr together: the job
    writes to a pipe, which a thread copies to the runner's own output as it arrives, so the log
    reads as it would, and the first :data:`OUTPUT_LIMIT` bytes to *output*, which a cut one ends
    with :data:`OUTPUT_CUT`. The file is data for a reader to show, never to run."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("run_job runs a job only on the main thread, which takes its signals")
    if not _finite(timeout) or timeout <= 0:
        raise ValueError(f"a job's timeout must be a finite number of seconds above 0: {timeout!r}")
    if not _finite(grace) or grace < 0:
        raise ValueError(f"a job's grace must be a finite number of seconds, 0 or more: {grace!r}")
    named = timeout if budget is None else budget
    if not _finite(named) or named <= 0:
        raise ValueError(f"a job's budget must be a finite number of seconds above 0: {budget!r}")
    full_env = job_env(scratch, path=path, env=env)
    watched = real_home() / _PERSONALCLAW_DIR
    was_there = os.path.lexists(watched)
    noted = _Noted()
    handlers: dict[int, object] = {}
    timed_out = False
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.signal(signum, noted)
        piped = output is not None
        proc = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=full_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if piped else None,
            stderr=subprocess.STDOUT if piped else None,
            start_new_session=True,
        )
        copier = None
        if output is not None and proc.stdout is not None:
            copier = threading.Thread(
                target=_copy, args=(proc.stdout, output, OUTPUT_LIMIT), daemon=True
            )
            copier.start()
        try:
            timed_out = _wait(proc, timeout, noted)
            if timed_out and before_stop is not None and proc.poll() is None:
                try:
                    before_stop(proc.pid)
                finally:
                    _stop(proc, grace, noted)
            elif timed_out or noted.signals:
                _stop(proc, grace, noted)
        finally:
            _signal_group(proc.pid, signal.SIGKILL)
            proc.wait()
            if copier is not None:
                copier.join(_COPY_JOIN_S)
                if not copier.is_alive() and proc.stdout is not None:
                    proc.stdout.close()
    finally:
        for restored, handler in handlers.items():
            signal.signal(restored, handler)  # type: ignore[arg-type]
    violations: list[str] = []
    if timed_out:
        violations.append(f"the job timed out after {named:g}s")
    if noted.signals:
        violations.append(f"interrupted by {signal.Signals(noted.signals[0]).name}")
    elif not timed_out and proc.returncode != 0:
        violations.append(f"the job exited {proc.returncode}")
    tripped = _tripwire_violations(scratch)
    if not was_there and os.path.lexists(watched):
        tripped.append(f"the job made the real home's {watched}, which was absent as it started")
    violations.extend(tripped)
    if tripped:
        code = RED
    elif noted.signals:
        code = PARTIAL
    else:
        code = RED if violations else OK
    return Result(
        code=code,
        returncode=proc.returncode,
        violations=tuple(violations),
        interrupted=bool(noted.signals),
    )
