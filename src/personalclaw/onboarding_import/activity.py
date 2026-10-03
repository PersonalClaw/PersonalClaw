"""What the onboarding step leaves running: the reading pass, and the import.

Both are one thread each and one at a time, because both are long only for the person the step
exists for — someone with a months-long history in another tool (measured: 12,005 conversation
files, 5.3 GB, took 41 s to read and 246 s to import 10,000 of them).

**The reading pass** (:class:`ReadingPass`) follows a scan that LOOKED: it reads each conversation
file the scan only looked into, in full and one at a time (:func:`~.engine.read_unread`), so the
next scan's counts are final. It holds one file's conversation at a time and writes nothing.

**The import** (:class:`ImportJob`) re-scans the tools its press names (and those turned on in
Settings: :func:`~personalclaw.outside_home.readable`), writes the picked items one by one — every
kind before conversations, so a stop part-way has brought the setup over — and keeps a running count
a person can watch. A stop (asked for, or the gateway shutting down) takes effect between two items:
each write is whole or absent (a transcript lands in one atomic write that names where it came
from), so what is left behind is every written item and nothing half-written. A gateway restart
ends the thread the same way; nothing about the job survives it but what it wrote, and the next
scan shows that as already here.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from personalclaw.onboarding_import.model import (
    ImportItem,
    ImportReport,
    ScanResult,
    WriteOutcome,
    WriteResult,
)

logger = logging.getLogger(__name__)

#: How long a shutdown waits for a thread to finish the item it is on.
_JOIN_SECONDS = 5.0


class ReadingPass:
    """The background reading of what a looking scan left unread. One at a time.

    It gives way to a scan: two threads parsing at once share one interpreter, and a scan answered
    beside the pass took 7.9 s instead of 0.8 s (measured over 12,005 files). So a scan pauses it
    (:meth:`paused`), and the pass waits, between two files or every few thousand lines of one
    (:func:`~.sources.common.giving_way`), until the scan has been answered.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        #: Clear while a scan is being answered; the pass waits for it between two files.
        self._go = threading.Event()
        self._go.set()
        self._pausers = 0
        #: Conversation files the scan that started this pass had already read in full.
        self._already = 0
        #: Files this pass set out to read, and has read so far.
        self._total = 0
        self._done = 0

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, results: list[ScanResult]) -> bool:
        """Read ``results``' unread files in full, in a thread — unless a pass is running or
        nothing is unread. Returns whether one was started."""
        from personalclaw.onboarding_import.engine import unread

        todo = unread(results)
        with self._lock:
            if todo == 0 or (self._thread is not None and self._thread.is_alive()):
                return False
            self._stop = threading.Event()
            self._already = sum(r.conversation_files for r in results) - todo
            self._total = todo
            self._done = 0
            stop = self._stop
            self._thread = threading.Thread(
                target=self._run,
                args=(results, stop),
                name="onboarding-import-reading",
                daemon=True,
            )
            self._thread.start()
            return True

    def _tick(self) -> None:
        with self._lock:
            self._done += 1

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Hold the pass between two files for as long as this block runs."""
        with self._lock:
            self._pausers += 1
            self._go.clear()
        try:
            yield
        finally:
            with self._lock:
                self._pausers -= 1
                if self._pausers == 0:
                    self._go.set()

    def _wait_out_a_scan(self, stop: threading.Event) -> None:
        """The reader's chance to step aside: wait while a scan has the pass paused."""
        while not self._go.wait(0.1):
            if stop.is_set():
                return

    def _run(self, results: list[ScanResult], stop: threading.Event) -> None:
        from personalclaw.onboarding_import.engine import read_unread
        from personalclaw.onboarding_import.sources.common import giving_way

        try:
            with giving_way(lambda: self._wait_out_a_scan(stop)):
                read_unread(results, stop=stop.is_set, on_read=self._tick)
        except Exception:  # noqa: BLE001 — a failed pass leaves the look's answer standing
            logger.warning("onboarding import: reading pass failed", exc_info=True)

    def stop(self, *, wait: bool = False) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
        if wait and thread is not None:
            thread.join(_JOIN_SECONDS)

    def to_dict(self) -> dict[str, Any]:
        """``{"running", "read", "of"}``: the conversation files read in full, of all the scan
        found."""
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive()
            return {
                "running": alive,
                "read": self._already + self._done,
                "of": self._already + self._total,
            }


class ImportJob:
    """One import, running or finished: what it is doing, how far it is, and how it ended.

    ``status`` is ``running``, then ``done`` (every picked item was written or reported),
    ``stopped`` (a stop ended it before the last item: the report names the rest as
    ``not_reached``) or ``failed`` (a write raised; ``error`` is its sentence, screened).
    """

    def __init__(
        self,
        job_id: str,
        fingerprints: list[str],
        accepted: Mapping[str, str],
        asked: Collection[str] = (),
    ) -> None:
        self.id = job_id
        self._fingerprints = fingerprints
        self._accepted = dict(accepted)
        #: The tools' setups the import's press names: its re-scan reads those, and the ones
        #: the owner turned on, and no other.
        self._asked = frozenset(asked)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.status = "running"
        #: ``scanning`` (the re-scan that is the pick's allowlist) → ``importing`` → ``finished``.
        self.phase = "scanning"
        self.total = len(fingerprints)
        self.done = 0
        self.counts = {outcome.value: 0 for outcome in WriteOutcome}
        #: The title of the item being written, as the scan listed it.
        self.current = ""
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.error = ""
        self.report: ImportReport | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def stopping(self) -> bool:
        return self._stop.is_set() and self.running

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"onboarding-{self.id}", daemon=True)
        self._thread.start()

    def stop(self, *, wait: bool = False) -> None:
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(_JOIN_SECONDS)

    def _stop_before(self, item: ImportItem) -> bool:
        """Asked with each item before it is written: whether to stop there — and, when not, the
        item this job is now on."""
        if self._stop.is_set():
            return True
        with self._lock:
            self.current = item.title or item.key
        return False

    def _landed(self, _item: ImportItem, result: WriteResult) -> None:
        with self._lock:
            self.done += 1
            self.counts[result.outcome.value] += 1

    def _run(self) -> None:
        from personalclaw.onboarding_import import run_import, scan_all
        from personalclaw.onboarding_import.floors import screened_failure

        try:
            results = scan_all(look=True, asked=self._asked)
            found = {item.fingerprint for result in results for item in result.items}
            with self._lock:
                self.total = sum(1 for fp in dict.fromkeys(self._fingerprints) if fp in found)
                self.phase = "importing"
            report = run_import(
                results,
                fingerprints=self._fingerprints,
                accepted=self._accepted,
                on_result=self._landed,
                stop_before=self._stop_before,
            )
        except Exception as exc:  # noqa: BLE001 — a write fault is reported, never swallowed
            logger.warning("onboarding import: write failed", exc_info=True)
            with self._lock:
                self.status = "failed"
                self.error = (
                    f"The import stopped after a write failed: {screened_failure(exc)}. Anything "
                    "already imported is kept, so importing again brings over only what is "
                    "still missing."
                )
                self.phase = "finished"
                self.current = ""
                self.finished_at = time.time()
            return
        with self._lock:
            self.report = report
            self.status = "stopped" if report.not_reached else "done"
            self.phase = "finished"
            self.current = ""
            self.finished_at = time.time()

    def to_dict(self, *, report: bool = True) -> dict[str, Any]:
        """The job as the step shows it. ``report`` includes the finished report, which for a
        months-long history runs to megabytes: the progress stream leaves it out."""
        with self._lock:
            payload: dict[str, Any] = {
                "id": self.id,
                "status": self.status,
                "phase": self.phase,
                "stopping": self._stop.is_set() and self.status == "running",
                "total": self.total,
                "done": self.done,
                "counts": dict(self.counts),
                "current": self.current,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
            }
            if report:
                payload["report"] = self.report.to_dict() if self.report is not None else None
            return payload


class ImportActivity:
    """The step's background work in one gateway: the reading pass and the one import job."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reading = ReadingPass()
        self._job: ImportJob | None = None
        self._ids = itertools.count(1)

    @property
    def job(self) -> ImportJob | None:
        with self._lock:
            return self._job

    def read_behind(self, results: list[ScanResult]) -> None:
        """After a scan that looked: read what it left unread — unless an import is running,
        which reads what it imports and would only compete with the pass."""
        job = self.job
        if job is not None and job.running:
            return
        self.reading.start(results)

    def start_import(
        self,
        fingerprints: list[str],
        accepted: Mapping[str, str],
        asked: Collection[str] = (),
    ) -> tuple[ImportJob, bool]:
        """``(job, started)``: a new import, or the one already running (``started`` False).
        ``asked`` is the tools' setups its press names."""
        with self._lock:
            if self._job is not None and self._job.running:
                return self._job, False
            self.reading.stop()
            job = ImportJob(f"import-{next(self._ids)}", fingerprints, accepted, asked)
            self._job = job
        job.start()
        return job, True

    def status(self) -> dict[str, Any]:
        job = self.job
        return {
            "reading": self.reading.to_dict(),
            "job": job.to_dict(report=False) if job is not None else None,
        }

    def shutdown(self) -> None:
        """Stop both threads between two items, and give each a moment to get there."""
        self.reading.stop(wait=True)
        job = self.job
        if job is not None:
            job.stop(wait=True)
