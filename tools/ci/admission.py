"""A local tier run starts a job only when this machine has the memory it needs (#3920).

``make check`` and ``make gate`` run a tier's jobs one at a time (``jobs.run_tier``), on a
machine that other work shares: another clone's run, a browser, a build. A job started into too
little memory swaps the whole machine, or is killed. So before each job the run reckons what it
needs and waits, reading the free memory every :data:`POLL_S` seconds, until the machine has it:

- **what it needs:** its workers times the peak memory of its largest process the last time it
  ran here (:class:`Peaks`, kept beside the verdict store). With no such run, the catalogue's
  ``mem_gb`` for the job; with neither, its workers times :data:`UNKNOWN_WORKER_MB`, since an
  unknown need is taken as a large one;
- **what is free:** ``MemAvailable`` on Linux; on macOS the free, inactive and speculative pages
  ``vm_stat`` counts. Less what the jobs just started hold: a job started a moment ago, by this
  run or another clone's, has not grown to its peak, so the reading does not show it yet. Each
  admitted job enters its need in a ledger the whole machine shares (:class:`Ledger`, in the
  user's cache folder, outside every clone), and admission counts each entry until its job ends,
  its run's process is gone, or it is :data:`RAMP_S` old. The check and the entry are made under
  one lock, so two runs never both take the same free memory.

A job that needs more than the machine holds in all never runs (``not run: it needs …``), and
nor does one still waiting after the run's bound (:data:`WAIT_S`, ``--admission-wait``). Each is
a partial run, exit 2, never a red one: the job did not run, so it neither passed nor failed. A
machine whose memory cannot be read admits every job, as a run did before admission: a reading
that is missing is no reason to hold work back, and the run says so once. A ledger that cannot
be used counts nothing, and the run says that once too.

Timing only: admission decides when a job starts, never which jobs run, how, or what their
results are, and it writes no job record. On GitHub every job starts at once; a runner holds one
job.
Standard library only, like every ``tools/ci`` module.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: How often a waiting job reads the free memory again, in seconds.
POLL_S = 5.0

#: How long a job may wait for memory, in seconds, before the run gives it up (not run).
WAIT_S = 30 * 60.0

#: What a worker of a job never measured here is taken to need, in MB.
UNKNOWN_WORKER_MB = 1024

#: Where the peaks are kept, in the clone's git common folder beside the verdict store.
PEAKS_FILE = "ci-job-memory.json"

#: How long an admitted job counts against the free memory as if it held its whole need, in
#: seconds, unless it ends first: about how long a job takes to grow to its peak.
RAMP_S = 120.0

#: The machine-wide ledger's file, in the user's cache folder (:func:`ledger_path`).
LEDGER_FILE = "admission-ledger.json"


def _vm_stat() -> int | None:
    done = subprocess.run(["vm_stat"], capture_output=True, text=True, check=False, timeout=10)
    if done.returncode != 0:
        return None
    size = re.search(r"page size of (\d+) bytes", done.stdout)
    counts = dict(re.findall(r"^Pages (free|inactive|speculative):\s+(\d+)\.", done.stdout, re.M))
    if not size or len(counts) < 3:
        return None
    return sum(int(n) for n in counts.values()) * int(size.group(1)) // (1024 * 1024)


def _meminfo(name: str) -> int | None:
    try:
        text = Path("/proc/meminfo").read_text(encoding="ascii")
    except OSError:
        return None
    found = re.search(rf"^{name}:\s+(\d+) kB$", text, re.M)
    return int(found.group(1)) // 1024 if found else None


def free_mb() -> int | None:
    """The memory this machine can give a new job now, in MB; ``None`` when it cannot be read."""
    try:
        if sys.platform == "darwin":
            return _vm_stat()
        return _meminfo("MemAvailable")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def total_mb() -> int | None:
    """All the memory this machine has, in MB; ``None`` when it cannot be read."""
    try:
        if sys.platform == "darwin":
            done = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=False
            )
            text = done.stdout.strip()
            return int(text) // (1024 * 1024) if done.returncode == 0 and text.isdigit() else None
        return _meminfo("MemTotal")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def ledger_path(
    environ: Mapping[str, str] = os.environ, home: Path | None = None, platform: str = sys.platform
) -> Path:
    """The machine-wide ledger: in the user's cache folder, which no clone holds."""
    home = home or Path.home()
    if platform == "darwin":
        base = home / "Library" / "Caches"
    else:
        cache = environ.get("XDG_CACHE_HOME", "")
        base = Path(cache) if os.path.isabs(cache) else home / ".cache"
    return base / "personalclaw-ci" / LEDGER_FILE


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # another user's process, and alive
    return True


def _entry(value: object) -> dict[str, Any] | None:
    """A ledger entry as written, or ``None`` for anything else."""
    if not isinstance(value, dict):
        return None
    pid, need, start = value.get("pid"), value.get("need_mb"), value.get("start")
    if not isinstance(value.get("id"), str) or not isinstance(value.get("job"), str):
        return None
    for number in (pid, need):
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            return None
    if isinstance(start, bool) or not isinstance(start, (int, float)):
        return None
    return value


@dataclass
class Ledger:
    """The jobs admitted on this machine by every run of every clone, so admission counts what a
    job just started will hold before the free reading shows it.

    One JSON list of entries (``id``, ``pid``, ``need_mb``, ``start``, ``job``), read and
    written whole under an exclusive lock on the ``.lock`` file beside it. An entry counts until
    the run that wrote it releases it (its job ended), the process that wrote it is gone, or it
    is :data:`RAMP_S` old; reading drops every other, and anything that is not an entry."""

    path: Path
    now: Callable[[], float] = time.time
    ramp_s: float = RAMP_S
    #: The entries this run entered and has not released.
    mine: list[str] = field(default_factory=list)

    def claim(self, job: str, need: int, free: int | None) -> tuple[bool, int]:
        """Whether *job* may start, and what the jobs just started hold, in MB. It may when
        *free* less that holds *need*, or when *free* is unknown; then its entry is made, under
        the same lock as the reading, so no other run counts that memory as free."""
        with self._locked() as entries:
            held = sum(int(e["need_mb"]) for e in entries)
            if free is not None and free - held < need:
                return False, held
            mine = uuid.uuid4().hex
            entries.append(
                {"id": mine, "pid": os.getpid(), "need_mb": need, "start": self.now(), "job": job}
            )
            self.mine.append(mine)
            return True, held

    def release(self) -> None:
        """The jobs this run entered have ended: their entries count no more."""
        if not self.mine:
            return
        with self._locked() as entries:
            entries[:] = [e for e in entries if e["id"] not in self.mine]
        self.mine.clear()

    @contextlib.contextmanager
    def _locked(self) -> Iterator[list[dict[str, Any]]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_name(self.path.name + ".lock"), "a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            entries = self._read()
            yield entries
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(entries, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    def _read(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except ValueError:
            return []  # a ledger this cannot read holds nothing; the next write replaces it
        now = self.now()
        kept = []
        for value in data if isinstance(data, list) else []:
            entry = _entry(value)
            if entry is None:
                continue
            if entry["id"] not in self.mine and not _alive(entry["pid"]):
                continue
            if now - entry["start"] >= self.ramp_s:
                continue
            kept.append(entry)
        return kept


@dataclass
class Memory:
    """The machine's memory as admission reads it, and its clock: the host's, or a test's."""

    free: Callable[[], int | None] = free_mb
    total: Callable[[], int | None] = total_mb
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    #: The jobs just started on this machine (:class:`Ledger`); ``None`` counts none.
    ledger: Ledger | None = None
    #: Whether the run has said once that this machine's memory cannot be read.
    said_unreadable: bool = field(default=False, repr=False)

    @classmethod
    def this(cls) -> Memory:
        """This machine's memory, with the ledger its runs share."""
        try:
            return cls(ledger=Ledger(ledger_path()))
        except (RuntimeError, KeyError):  # no home to find the cache folder in
            return cls()


def need_mb(*, workers: int, peak_mb: int | None, declared_gb: float | None) -> tuple[int, str]:
    """What a job needs to start, in MB, and how that was reckoned."""
    if peak_mb is not None:
        return workers * peak_mb, f"{workers} worker(s) × {peak_mb} MB, its peak here"
    if declared_gb is not None:
        return round(declared_gb * 1024), f"the catalogue's mem_gb = {declared_gb:g}"
    return (
        workers * UNKNOWN_WORKER_MB,
        f"{workers} worker(s) × {UNKNOWN_WORKER_MB} MB, never measured here",
    )


def admit(
    key: str,
    need: int,
    how: str,
    memory: Memory,
    *,
    wait_s: float = WAIT_S,
    poll_s: float = POLL_S,
    say: Callable[[str], None] = lambda line: print(line, file=sys.stderr, flush=True),
) -> str | None:
    """Wait until the machine has *need* MB free for job *key*, less what the jobs just started
    hold; ``None`` once it may start, its entry made in the ledger, or why it never will (it
    needs more than the machine holds, or it waited *wait_s*). Call :func:`release` when the
    job ends."""
    total = memory.total()
    if total is not None and need > total:
        return f"not run: it needs {need} MB ({how}), and this machine has {total} MB in all"
    began = memory.clock()
    waited = False
    while True:
        free = memory.free()
        if free is None and not memory.said_unreadable:
            say("admission: this machine's free memory cannot be read; every job starts")
            memory.said_unreadable = True
        admitted, held = _claim(memory, key, need, free, say)
        if admitted:
            if waited and free is not None:
                say(f"admission: [{key}] starts: {free - held} MB free of the {need} MB it needs")
            return None
        just_started = f" ({held} MB of it held by jobs just started)" if held else ""
        if memory.clock() - began >= wait_s:
            return (
                f"not run: it needs {need} MB ({how}), and {free} MB was free{just_started} "
                f"after {wait_s:g}s of waiting"
            )
        if not waited:
            say(
                f"admission: [{key}] waits: it needs {need} MB ({how}), {free} MB is free"
                + just_started
            )
            waited = True
        memory.sleep(poll_s)


def _claim(
    memory: Memory, key: str, need: int, free: int | None, say: Callable[[str], None]
) -> tuple[bool, int]:
    """:meth:`Ledger.claim` on the machine's ledger; with none, or one that cannot be used
    (said once, then no more used), the free reading alone decides."""
    if memory.ledger is not None:
        try:
            return memory.ledger.claim(key, need, free)
        except OSError as exc:
            say(
                f"admission: the machine's ledger {memory.ledger.path} cannot be used ({exc}); "
                "jobs other runs just started are not counted"
            )
            memory.ledger = None
    return free is None or free >= need, 0


def release(memory: Memory) -> None:
    """The job admitted last has ended: its ledger entry counts no more."""
    if memory.ledger is None:
        return
    try:
        memory.ledger.release()
    except OSError:
        pass  # its entry stops counting at RAMP_S


class Peaks:
    """The peak memory of each job's largest process, by job, the last time it ran here: read
    from a JSON object of job ids and MB, written whole after each job that ran."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.peaks: dict[str, int] = {}
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # a record this cannot read is no record: each job counts as never measured
        if isinstance(data, dict):
            self.peaks = {
                k: v
                for k, v in data.items()
                if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0
            }

    def get(self, job_id: str) -> int | None:
        return self.peaks.get(job_id)

    def record(self, job_id: str, peak_mb: int) -> None:
        if peak_mb <= 0 or self.path is None:
            return
        self.peaks[job_id] = peak_mb
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(self.peaks, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # a peak that cannot be kept leaves the next run to reckon without it
