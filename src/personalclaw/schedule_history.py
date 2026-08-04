"""Schedule run history — the ``ScheduleRun`` sub-entity + ``ScheduleRunStore``.

A ``ScheduleRun`` is a sub-entity of the Schedule entity: the persistent record
of one execution of a Schedule Job (status / timing / trigger / summary /
trace). It is the unit a future ``ScheduleProvider`` would ``list_runs`` /
``get_run`` — the read API on :class:`ScheduleRunStore` is deliberately shaped
like ``TaskProvider.list_tasks`` / ``get_task`` (returns ``(rows, total)``), so
the ABC can adopt it unchanged when the Schedule entity is put behind a provider
interface.

Persistence is JSONL-per-job (a first-class PersonalClaw idiom — cf. ``sel.py``,
``learn.py``, ``history.py``): ``<dir>/cron-history/{job_id}.jsonl`` holds full
records (with trace); ``_index.jsonl`` holds lightweight rows (no trace) for the
cross-job Executions view. Writes take an fcntl advisory lock (mirroring
``ScheduleService._file_lock``); reads are lock-free — a partial final line from
a concurrent append is silently skipped by the ``JSONDecodeError`` handler.
"""

from __future__ import annotations

import fcntl
import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

# Caps (module constants — there is no schedule-history config block).
_SUMMARY_CAP = 200
_TRACE_CAP = 50_000  # 50 KB of the full last result
_MAX_RECORDS_PER_JOB = 100
_MAX_INDEX_RECORDS = 2_000

_HISTORY_DIRNAME = "cron-history"
_INDEX_NAME = "_index.jsonl"
_LOCK_NAME = ".history.lock"


@dataclass
class ScheduleRun:
    """One execution of a Schedule Job (the run sub-entity).

    ``trace`` is the full (capped) last result; ``summary`` is a short prefix
    for list views. Index rows drop ``trace`` to keep cross-job queries cheap.
    """

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    job_id: str = ""
    trigger: str = "scheduled"  # "scheduled" | "manual"
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_ms: int = 0
    # "success" | "failure" | "timeout": a verified synchronous outcome.
    # "launched": the run only STARTED background work (a fire-and-forget spawn —
    #   run-prompt / run-workflow / invoke-agent); the spawned turn's real outcome
    #   is recorded by ITS own run, not this one. Honest "started ≠ succeeded"
    #   status (T7) — a green "ran" must not imply the work succeeded.
    status: str = "success"
    summary: str = ""
    trace: str = ""
    error: str = ""

    def to_dict(self, *, include_trace: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "trigger": self.trigger,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "summary": self.summary,
            "error": self.error,
        }
        if include_trace:
            d["trace"] = self.trace
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScheduleRun":
        return cls(
            run_id=str(d.get("run_id", "")) or uuid.uuid4().hex[:12],
            job_id=str(d.get("job_id", "")),
            trigger=str(d.get("trigger", "scheduled")),
            started_at=float(d.get("started_at", 0.0) or 0.0),
            finished_at=float(d.get("finished_at", 0.0) or 0.0),
            duration_ms=int(d.get("duration_ms", 0) or 0),
            status=str(d.get("status", "success")),
            summary=str(d.get("summary", "")),
            trace=str(d.get("trace", "")),
            error=str(d.get("error", "")),
        )


def _redact_stored(text: str | None) -> str:
    """Credential-redact a field on its way INTO the run ledger (criterion 11 — S138).

    Never raises: a redaction failure must not lose the run record. The unredacted text is dropped
    rather than stored in that case — losing a summary is recoverable, and writing a credential to
    disk is not.

    Reuses `security.redact_credentials`, the same matcher the read path and the SEL already use,
    so a pattern added there covers this too. Composed with `redact_exfiltration_urls` because
    a resolved token most often escapes inside a URL a command printed.
    """
    if not text:
        return ""
    try:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        cleaned, _urls = redact_exfiltration_urls(str(text))
        cleaned, _creds = redact_credentials(cleaned)
        return cleaned
    except Exception:  # noqa: BLE001 - see the docstring: drop rather than store raw
        return "[redaction failed; text withheld]"


class ScheduleRunStore:
    """JSONL-per-job store of :class:`ScheduleRun` records, owned by the service.

    The read API (``list_for_job`` / ``list_all`` / ``get_run``) returns
    ``(rows, total)`` to mirror ``TaskProvider.list_tasks``. All public methods
    are async (``asyncio.to_thread`` wraps the sync, locked JSONL I/O).
    """

    def __init__(self, base_dir: Path) -> None:
        self._dir = Path(base_dir) / _HISTORY_DIRNAME
        self._index = self._dir / _INDEX_NAME

    # ── Paths + lock ──────────────────────────────────────────────────

    def _job_path(self, job_id: str) -> Path:
        """Resolve ``{job_id}.jsonl`` under the history dir, guarding traversal.

        A malicious ``job_id`` (e.g. ``../../etc/x``) must never escape the
        history directory — assert the resolved parent is the history dir.
        """
        candidate = (self._dir / f"{job_id}.jsonl").resolve()
        if candidate.parent != self._dir.resolve():
            raise ValueError(f"unsafe job_id for history path: {job_id!r}")
        return candidate

    @contextmanager
    def _lock(self) -> Iterator[None]:
        """Cross-process advisory lock (mirrors ScheduleService._file_lock)."""
        self._dir.mkdir(parents=True, exist_ok=True)
        lock = self._dir / _LOCK_NAME
        fd = lock.open("w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        """Lock-free read; tolerates a partial trailing line (concurrent append)."""
        rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        # Partial final line from an in-flight append — skip it.
                        continue
        except FileNotFoundError:
            return []
        except OSError:
            logger.debug("Failed reading run history %s", path, exc_info=True)
            return []
        return rows

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
        content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        atomic_write(path, content, mode=0o600)

    # ── Write ─────────────────────────────────────────────────────────

    def _append_sync(self, run: ScheduleRun) -> None:
        # 🔴 REDACT BEFORE WRITE (criterion 11 — S138). The criterion is explicit that
        # `{{secret:KEY}}` "never appears resolved in triggers.json, journals, LEDGER, or
        # `automation_history` output". Measured: the API's `_redact_run` cleans the response, but
        # nothing cleaned the WRITE — a bash action that echoed a resolved credential put it in
        # plaintext into `cron-history/<job>.jsonl` AND `_index.jsonl`, both 0600 but both on disk,
        # both carried by `personalclaw snapshot` (S113), and both readable by anything that reads
        # the home. Redacting only on read is a read-path control over a storage-path leak.
        #
        # At the single write point, deliberately: `_append_sync` is the one funnel every run record
        # passes through, so a future caller cannot forget it — the per-call-site alternative is how
        # the screen and the fence gaps happened.
        run.summary = _redact_stored(run.summary)[:_SUMMARY_CAP]
        run.trace = _redact_stored(run.trace)[:_TRACE_CAP]
        run.error = _redact_stored(run.error)
        job_path = self._job_path(run.job_id)
        with self._lock():
            self._dir.mkdir(parents=True, exist_ok=True)
            # Full record (with trace) on the per-job file.
            with job_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(run.to_dict(include_trace=True), ensure_ascii=False) + "\n")
            try:
                job_path.chmod(0o600)
            except OSError:
                pass
            # Lightweight row (no trace) on the cross-job index.
            with self._index.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(run.to_dict(include_trace=False), ensure_ascii=False) + "\n")
            try:
                self._index.chmod(0o600)
            except OSError:
                pass
            self._rotate_job_locked(run.job_id)
            self._rotate_index_locked()

    async def append(self, run: ScheduleRun) -> None:
        import asyncio

        await asyncio.to_thread(self._append_sync, run)

    # ── Read (TaskProvider-shaped: returns (rows, total)) ─────────────

    def _list_for_job_sync(
        self, job_id: str, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._read_jsonl(self._job_path(job_id))
        rows.reverse()  # newest-first
        total = len(rows)
        page = [{k: v for k, v in r.items() if k != "trace"} for r in rows[offset : offset + limit]]
        return page, total

    async def list_for_job(
        self, job_id: str, offset: int = 0, limit: int = 10
    ) -> tuple[list[dict[str, Any]], int]:
        import asyncio

        return await asyncio.to_thread(self._list_for_job_sync, job_id, offset, limit)

    def _list_all_sync(
        self, offset: int, limit: int, job_id: str | None
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._read_jsonl(self._index)
        if job_id:
            rows = [r for r in rows if r.get("job_id") == job_id]
        rows.reverse()  # newest-first
        total = len(rows)
        return rows[offset : offset + limit], total

    async def list_all(
        self, offset: int = 0, limit: int = 20, job_id: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        import asyncio

        return await asyncio.to_thread(self._list_all_sync, offset, limit, job_id)

    def _get_run_sync(self, job_id: str, run_id: str) -> dict[str, Any] | None:
        for r in self._read_jsonl(self._job_path(job_id)):
            if r.get("run_id") == run_id:
                return r
        return None

    async def get_run(self, job_id: str, run_id: str) -> dict[str, Any] | None:
        import asyncio

        return await asyncio.to_thread(self._get_run_sync, job_id, run_id)

    # ── Rotation + delete ─────────────────────────────────────────────

    def _rotate_job_locked(self, job_id: str) -> None:
        path = self._job_path(job_id)
        rows = self._read_jsonl(path)
        if len(rows) > _MAX_RECORDS_PER_JOB:
            self._write_jsonl(path, rows[-_MAX_RECORDS_PER_JOB:])

    def _rotate_index_locked(self) -> None:
        rows = self._read_jsonl(self._index)
        if len(rows) > _MAX_INDEX_RECORDS:
            self._write_jsonl(self._index, rows[-_MAX_INDEX_RECORDS:])

    def _rotate_all_sync(self) -> None:
        if not self._dir.exists():
            return
        with self._lock():
            for path in self._dir.glob("*.jsonl"):
                if path.name == _INDEX_NAME:
                    continue
                rows = self._read_jsonl(path)
                if len(rows) > _MAX_RECORDS_PER_JOB:
                    self._write_jsonl(path, rows[-_MAX_RECORDS_PER_JOB:])
            self._rotate_index_locked()

    async def rotate_all(self) -> None:
        import asyncio

        await asyncio.to_thread(self._rotate_all_sync)

    def _delete_for_job_sync(self, job_id: str) -> None:
        with self._lock():
            path = self._job_path(job_id)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                logger.debug("Failed deleting run history %s", path, exc_info=True)
            # Drop the job's rows from the cross-job index.
            rows = [r for r in self._read_jsonl(self._index) if r.get("job_id") != job_id]
            self._write_jsonl(self._index, rows)

    async def delete_for_job(self, job_id: str) -> None:
        import asyncio

        await asyncio.to_thread(self._delete_for_job_sync, job_id)
