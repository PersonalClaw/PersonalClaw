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

#: How many SUPPRESSED rows one job may keep, out of `_MAX_RECORDS_PER_JOB` (S173).
#:
#: A quarter, deliberately: enough that "why did my automation not run last night" stays answerable
#: across a long quiet window, while leaving three quarters of it for runs that DID work — the
#: rows a user opens the history for. Before this split, a suppression storm evicted every real
#: run within its own duration.
_MAX_SUPPRESSED_PER_JOB = _MAX_RECORDS_PER_JOB // 4

#: How many rows one job may hold in the SHARED cross-job index when it must be trimmed (S174).
#:
#: Set to the per-job file cap: a job cannot usefully contribute more index rows than its own
#: history retains, and matching the two means the index never evicts a row whose full record
#: still exists. Without this bound, one noisy trigger owned all 2000 index rows and every other
#: automation vanished from the dashboard's cross-schedule view.
_MAX_INDEX_PER_JOB = _MAX_RECORDS_PER_JOB

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

    def _count_since_sync(self, job_id: str, since: float, *, manual: bool) -> int:
        from personalclaw.triggers.models import INERT_OUTCOMES

        rows = self._read_jsonl(self._job_path(job_id))
        total = 0
        for row in rows:
            try:
                started = float(row.get("started_at") or 0.0)
            except (TypeError, ValueError):
                continue
            if started < since:
                continue
            # A MANUAL fire is excluded by default. §3.6 is explicit that "manual fires bypass the
            # hourly cap" — the cap exists to stop the machine running away on its own, and a person
            # clicking Run is not the machine running away. Counting their clicks toward the cap
            # would let a user lock themselves out of their own automation.
            if not manual and str(row.get("trigger") or "") == "manual":
                continue
            # 🔴 A SUPPRESSION IS NOT A FIRE (S171). Since suppressed fires began persisting their
            # typed row here (§7 crit 8's "zero silent drops"), this window would otherwise count
            # them — measured, 5 quiet-hours skips read as 5 fires, so a trigger held by its own
            # quiet window would consume the hourly cap it never used and then be refused for
            # "running away". The cap exists to bound work the machine DID.
            #
            # Keyed on `INERT_OUTCOMES`, the same set `history.is_inert` uses, rather than a local
            # list of `skipped_*` strings: one definition of "this did nothing" for both surfaces.
            if str(row.get("status") or "") in INERT_OUTCOMES:
                continue
            total += 1
        return total

    async def count_since(self, job_id: str, since: float, *, manual: bool = False) -> int:
        """How many runs this job recorded at or after `since` (a UTC epoch).

        🔴 THE WINDOWED QUERY three rate caps were waiting on (S152). `rate_cap`,
        `max_runs_per_hour` and `max_actions_per_hour` were all validated, carried, and enforced by
        NOTHING because this read did not exist — `list_for_job` is offset/limit only, so a caller
        could page rows but not ask "how many in the last hour". S150 named that gap explicitly;
        `missed.within_rate_window` has been the pure decision waiting for this number since S65.

        Counts rows rather than paging them: the answer is one integer, and `list_for_job(0, 1000)`
        would allocate a thousand dicts to compute it — on a path that runs on every fire.

        A row with an unparseable `started_at` is SKIPPED rather than counted. Counting it would let
        one malformed line push a trigger over its cap and suppress real work; skipping it can only
        under-count, and the cap's own purpose (stop a runaway) still holds because a runaway writes
        many well-formed rows.
        """
        import asyncio

        return await asyncio.to_thread(self._count_since_sync, job_id, since, manual=manual)

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
        """Trim a job's history, keeping WORK and suppressions on separate quotas (S173).

        🔴 WHY THE SPLIT. A single `[-_MAX_RECORDS_PER_JOB:]` tail is correct while every row is a
        run — but S171 began persisting suppressed fires (criterion 8's "zero silent drops"), and a
        minutely trigger held by quiet hours writes 1440 of them a day. `RunWeight`'s own docstring
        names that number. Measured against the flat tail: **1 real backup run plus 129 quiet-hours
        skips evicted the backup entirely**, and the 100-row window held ~100 MINUTES of history
        instead of ~100 runs.

        So the newest `_MAX_SUPPRESSED_PER_JOB` suppressions are kept, and the work quota is
        computed as the remainder — a job with no skips still keeps its full 100 runs, so nothing
        regresses for a trigger that never suppresses.

        Order is PRESERVED on write: the two classes are partitioned to decide what survives, then
        re-merged by their original position, because `list_for_job` reverses the file for
        newest-first and `count_since` walks it — both would misread a file grouped by class.
        """
        path = self._job_path(job_id)
        rows = self._read_jsonl(path)
        if len(rows) <= _MAX_RECORDS_PER_JOB:
            return
        from personalclaw.triggers.models import INERT_OUTCOMES

        sup_idx = [i for i, r in enumerate(rows) if str(r.get("status") or "") in INERT_OUTCOMES]
        work_idx = [i for i in range(len(rows)) if i not in set(sup_idx)]
        # A CEILING on suppressions, not a floor under them — and the work quota is whatever the
        # total leaves once suppressions are capped, so a job with FEW skips keeps a nearly-full
        # window of runs. My first draft capped work at `total - suppressed_cap` unconditionally,
        # which regressed a work-only job from 100 rows to 75 and broke `test_rotation_caps_per_job`
        # — the existing test correctly refused a change I had not justified.
        keep_suppressed = sup_idx[-_MAX_SUPPRESSED_PER_JOB:]
        keep_work = work_idx[-(_MAX_RECORDS_PER_JOB - len(keep_suppressed)) :]
        keep = sorted(set(keep_suppressed) | set(keep_work))
        self._write_jsonl(path, [rows[i] for i in keep])

    def _rotate_index_locked(self) -> None:
        """Trim the cross-job index, bounding how much of it ONE job may hold (S174).

        🔴 WHY A PER-JOB BOUND. The index is SHARED — it backs the dashboard's "recent runs across
        all schedules" — and a flat tail lets the loudest writer own all of it. Measured after S171
        began persisting suppressions: three well-behaved automations with one run each, plus 1.5
        days of one minutely trigger's quiet-hours skips, and the index held **2000 rows from that
        single trigger and nothing else**. Every other automation was evicted from the only
        cross-job view.

        S173 fixed the same shape per job; this is the cross-job half. There the classes competed
        (work vs suppressions), here the JOBS compete, so the bound is per `job_id`: no job may hold
        more than `_MAX_INDEX_PER_JOB` rows while others are being dropped.

        Applied only when trimming is needed, and only to jobs OVER their share — a store with a few
        busy jobs and room to spare keeps everything, so nothing regresses for an install that never
        hits the cap.
        """
        rows = self._read_jsonl(self._index)
        if len(rows) <= _MAX_INDEX_RECORDS:
            return
        # Newest-first per job, so each job's own tail is what survives its share.
        per_job: dict[str, list[int]] = {}
        for i, r in enumerate(rows):
            per_job.setdefault(str(r.get("job_id") or ""), []).append(i)
        keep: set[int] = set()
        for idxs in per_job.values():
            keep.update(idxs[-_MAX_INDEX_PER_JOB:])
        # Then the global cap, on the fair-shared set. Order preserved: `list_all` reverses the
        # file for newest-first, so a file regrouped by job would render out of order.
        self._write_jsonl(self._index, [rows[i] for i in sorted(keep)[-_MAX_INDEX_RECORDS:]])

    def _rotate_all_sync(self) -> None:
        """Rotate every job file + the index. Runs once at gateway boot.

        🔴 DELEGATES to `_rotate_job_locked` rather than repeating the trim (S175). This carried its
        own inlined `rows[-_MAX_RECORDS_PER_JOB:]` — the pre-S173 flat tail — so the BOOT path undid
        what the append path protects. Measured on the realistic case, an existing install's first
        boot on the new build: a 200-row legacy file (1 real run + 199 quiet-hours skips) came back
        as 100 rows with the real run **evicted**, while appending those same rows keeps it.

        A duplicated policy is how two paths start disagreeing, and here the second copy silently
        reverted the first at exactly the moment a user upgrades. One trim function now, called from
        both.
        """
        if not self._dir.exists():
            return
        with self._lock():
            for path in self._dir.glob("*.jsonl"):
                if path.name == _INDEX_NAME:
                    continue
                self._rotate_job_locked(path.stem)
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
