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
cross-job Executions view. Writes take an fcntl advisory lock (opened by
``durability.home_paths.open_lock``); reads are lock-free — a partial final line from
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
from typing import Any, Callable, Iterator

from personalclaw import bounded_log
from personalclaw.atomic_write import atomic_write
from personalclaw.durability import home_paths

logger = logging.getLogger(__name__)

# Caps (module constants — there is no schedule-history config block).
_SUMMARY_CAP = 200
_TRACE_CAP = 50_000  # 50 KB of the full last result
_MAX_RECORDS_PER_JOB = 100
_MAX_INDEX_RECORDS = 2_000

#: How many SUPPRESSED rows one job may keep, out of `_MAX_RECORDS_PER_JOB`.
#:
#: A quarter, deliberately: enough that "why did my automation not run last night" stays answerable
#: across a long quiet window, while leaving three quarters of it for runs that DID work — the
#: rows a user opens the history for. Before this split, a suppression storm evicted every real
#: run within its own duration.
_MAX_SUPPRESSED_PER_JOB = _MAX_RECORDS_PER_JOB // 4

#: How many rows one job may hold in the SHARED cross-job index when it must be trimmed.
#:
#: Set to the per-job file cap: a job cannot usefully contribute more index rows than its own
#: history retains, and matching the two means the index never evicts a row whose full record
#: still exists. Without this bound, one noisy trigger owned all 2000 index rows and every other
#: automation vanished from the dashboard's cross-schedule view.
_MAX_INDEX_PER_JOB = _MAX_RECORDS_PER_JOB

#: The statuses a row is settled FROM (`ScheduleRunStore.settle_sync`): the run only started work
#: that ends later (`launched`), or queued it behind a run already in flight (`queued`). Such a run
#: has not ended, so nothing reads it as a success or a failure yet (`autopause.ending_decision`).
#: A row that already says how it went is never rewritten.
UNSETTLED_STATUSES: frozenset[str] = frozenset({"launched", "queued"})

#: What a row's typed exit (`ScheduleRun.trigger`) is for a run by hand: a settle keeps it, so the
#: hourly cap and the failure count go on passing over that run (`triggers.run_record`).
_BY_HAND = "manual"

#: Endings that arrived before the row they settle, by `(history dir, job id, work id)`. A fire's
#: row is written after its action returns, while the agent it started goes on by itself, so an
#: agent refused at once can end first; the append then writes the row as it ended. Bounded: an
#: ending whose row never comes (a recorder that failed) is dropped, oldest first.
_EARLY_ENDINGS: dict[tuple[str, str, str], dict[str, Any]] = {}
_EARLY_ENDINGS_CAP = 64

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
    # The trigger's name when it ran. The run feed names a run by its trigger as it is called now,
    # and by this once the trigger has left the list: a one-shot retires after its run, and a
    # deleted trigger keeps its history.
    job_name: str = ""
    # "manual" for a run by hand. A fire's row carries its typed exit (`autopause.ExitType`, what
    # the failure count reads) or, for a fire that did not run its action, its outcome; a
    # `launched` or `queued` row takes the exit its work ended with when it settles.
    trigger: str = "scheduled"
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_ms: int = 0
    # "success" | "failure" | "timeout": a verified synchronous outcome. "timeout" is also a run
    #   the reaper closed past its deadline (`triggers/reaper.reap_one`).
    # "interrupted": a stop or a restart cut the run off before it finished, or cut off the work a
    #   `launched` run started (`triggers/reaper.RESTART_INTERRUPTED_STATUS`). Not retried on its
    #   own: it waits on the Triggers page's review for the user to run it again or dismiss it.
    # "ran_late": the review's Run now — a run standing in for a slot that did not run.
    # "launched": the run only STARTED background work (a fire-and-forget spawn —
    #   run-prompt / run-workflow / invoke-agent); the spawned turn's real outcome
    #   is recorded by ITS own run, not this one. Honest "started ≠ succeeded"
    #   status (T7) — a green "ran" must not imply the work succeeded.
    # "skipped_noop": the action ran and had nothing to do (`status_for_result`).
    # "degraded": the action did its work at its no-model floor and its summary says what it went
    #   without — a digest with no synthesis (`status_for_result`). Not a plain success.
    # "declined": the work a launched run started asked its owner to start and they declined it
    #   (`ScheduleRunStore.settle_sync`). Their own decision, not a failure.
    # "refused": the agent a launched run started had calls refused by its own limits
    #   (`triggers.settle`): not a success, and not a failure either. Also a run refused before
    #   its action ran, because something the action needs is gone: its app, a secret it uses, an
    #   action at all (`triggers.run_record.record_refusal`), its `error` saying which.
    # "stopped": someone stopped the work a launched or queued run started before it finished:
    #   its owner stopped the agent, or cancelled the workflow run (`triggers.settle`). Their own
    #   decision, not a failure.
    status: str = "success"
    summary: str = ""
    trace: str = ""
    error: str = ""
    # The work a `launched` or `queued` run started (`ActionResult.work_id`), which ends later:
    # `subagent:<id>` for an agent, `workflow:<id>` for a workflow run. When it ends, this row says
    # how it went instead (`ScheduleRunStore.settle_sync`).
    work_id: str = ""

    def to_dict(self, *, include_trace: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "job_name": self.job_name,
            "trigger": self.trigger,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "summary": self.summary,
            "error": self.error,
            "work_id": self.work_id,
        }
        if include_trace:
            d["trace"] = self.trace
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScheduleRun":
        return cls(
            run_id=str(d.get("run_id", "")) or uuid.uuid4().hex[:12],
            job_id=str(d.get("job_id", "")),
            job_name=str(d.get("job_name", "")),
            trigger=str(d.get("trigger", "scheduled")),
            started_at=float(d.get("started_at", 0.0) or 0.0),
            finished_at=float(d.get("finished_at", 0.0) or 0.0),
            duration_ms=int(d.get("duration_ms", 0) or 0),
            status=str(d.get("status", "success")),
            summary=str(d.get("summary", "")),
            trace=str(d.get("trace", "")),
            error=str(d.get("error", "")),
            work_id=str(d.get("work_id", "")),
        )


#: The `ActionResult.outcome` refinements a finished run records as its own status, rather than
#: as a plain `success`. `launched` and `queued` started work that records its own outcome (T7,
#: WV-14); `skip` ran and had nothing to do, recorded as the inert `skipped_noop`, which folds out
#: of the default history and runs views — a minutely automation with nothing to do would
#: otherwise bury the runs that did something.
_RESULT_STATUS: dict[str, str] = {
    "launched": "launched",
    "queued": "queued",
    "skip": "skipped_noop",
    # The action stopped for a person — browse at a sign-in page. Not `success`: nothing it was
    # asked to do happened yet, and the trigger's question is open (`triggers.parks`).
    "needs_input": "waiting",
    # The action did its work at its no-model floor: the digest arrived without its synthesis. Not
    # `success`, which would say it delivered all it is for; its summary says what it went without.
    "degraded": "degraded",
}

#: Every status `status_for_result` can return: the closed vocabulary `triggers/history.py`'s
#: `SCHEDULE_STATUS_TO_OUTCOME` must translate (`test_triggers_status_vocabulary` reads this).
RESULT_STATUSES: frozenset[str] = frozenset({"success", "failure", *_RESULT_STATUS.values()})


def status_for_result(result: Any) -> str:
    """The `ScheduleRun.status` a finished action records: `failure`, a refinement, or `success`.

    The one answer every run's record takes (`triggers.run_record.record_run`), a fire's and a
    hand run's alike, so the two cannot record different statuses for the same result. A fire used
    to record every successful result as `success`, so one that only launched a workflow read as
    one whose work had succeeded.
    """
    if result is None:
        return "success"
    if not bool(getattr(result, "success", True)):
        return "failure"
    return _RESULT_STATUS.get(str(getattr(result, "outcome", "") or ""), "success")


def summary_for_result(result: Any) -> str:
    """The history row's line for an action that did not fail: the sentence the action wrote for
    a person (`ActionResult.summary`), else what it printed.

    ONE answer for every run's record, as `status_for_result` is. A browse run prints its whole
    account as JSON, and its row showed that JSON: the row's TRACE is what an action printed, and
    its summary is what a person reads.
    """
    if result is None:
        return ""
    said = str(getattr(result, "summary", "") or "")
    return said or str(getattr(result, "stdout", "") or "")


#: How much of a failed command's error output its failure keeps: the end, where a command says
#: what went wrong. The history row keeps the whole output as its trace.
_FAILURE_OUTPUT_TAIL = 400


def failure_for_result(result: Any) -> str:
    """Why an action that did not succeed failed, for a person: the reason it gave, else the end
    of what it wrote to its error output and its exit code, else its exit code alone.

    ONE answer for the fire's history row, the trigger's last error and the note that tells the
    owner, as :func:`summary_for_result` is for a success. A command that exits non-zero reports
    no reason of its own (`BashActionProvider`), so "the action reported failure" was all any of
    them said, while the command had written why.
    """
    said = str(getattr(result, "error", "") or "").strip() if result is not None else ""
    if said:
        return said
    code = getattr(result, "exit_code", 0) if result is not None else 0
    written = str(getattr(result, "stderr", "") or "").strip() if result is not None else ""
    if written:
        tail = written[-_FAILURE_OUTPUT_TAIL:]
        if len(written) > _FAILURE_OUTPUT_TAIL and "\n" in tail:
            tail = tail.split("\n", 1)[1]
        return f"exited with code {code}: {tail}" if code else tail
    if code:
        return f"exited with code {code}"
    return "the action reported failure"


def late_summary(late: str, summary: str) -> str:
    """The history row's line for a run that ran late: why, as its own sentence, then what it did.

    ONE answer for both kinds of late run, as :func:`summary_for_result` is: a scheduled fire the
    clock reached well after its slot (`missed.late_outcome`) and the review's Run now standing in
    for a slot that did not run (`missed.resolve_missed`). Such a run records `ran_late` when it
    succeeded; this is what its row then says, and what any other row of it that did not fail says
    first.
    """
    return f"{late[:1].upper()}{late[1:]}." + (f" {summary}" if summary else "")


def _redact_stored(text: str | None) -> str:
    """Credential-redact a field on its way INTO the run ledger (criterion 11 — S138).

    Never raises: a redaction failure must not lose the run record. The unredacted text is dropped
    rather than stored in that case — losing a summary is recoverable, and writing a credential to
    disk is not.

    Reuses `security.redact_credentials`, the same matcher the read path and the SEL already use,
    so a pattern added there covers this too. Composed with `redact_exfiltration_urls` because
    a resolved token most often escapes inside a URL a command printed. Both, and the withholding,
    are `security.redact_or_withhold`'s.
    """
    if not text:
        return ""
    from personalclaw.security import redact_or_withhold

    return redact_or_withhold(str(text))


def _settle_row(row: dict[str, Any], ending: dict[str, Any], *, with_trace: bool) -> None:
    """Write an ending (`ScheduleRunStore.settle_sync`) onto a stored row, in place: its status,
    what it said, when it finished, and the exit it ended with, which a run by hand's row does not
    take (:func:`_exit_after`). The index's rows carry no trace, and are given none."""
    started = float(row.get("started_at") or 0.0)
    finished = max(started, float(ending["finished_at"]))
    row.update(
        status=ending["status"],
        summary=ending["summary"],
        error=ending["error"],
        finished_at=finished,
        duration_ms=int((finished - started) * 1000),
        trigger=_exit_after(str(row.get("trigger") or ""), ending),
    )
    if with_trace:
        row["trace"] = ending["trace"]


def _exit_after(tag: str, ending: dict[str, Any]) -> str:
    """A settled row's typed exit: the one its work ended with, and ``manual`` for a run by hand,
    which stays a hand run whatever its work did."""
    if tag == _BY_HAND or not ending["exit"]:
        return tag
    return str(ending["exit"])


def _newest_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*rows* newest first by ``started_at``, whatever order the file holds them in."""
    return bounded_log.in_time_order(rows, at="started_at")[::-1]


class ScheduleRunStore:
    """JSONL-per-job store of :class:`ScheduleRun` records, owned by the service.

    The read API (``list_for_job`` / ``list_all`` / ``get_run``) returns
    ``(rows, total)`` to mirror ``TaskProvider.list_tasks``. All public methods
    are async (``asyncio.to_thread`` wraps the sync, locked JSONL I/O).
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = Path(base_dir)
        self._dir = self._base / _HISTORY_DIRNAME
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
        """Cross-process advisory lock, opened by the one opener of a lock
        (``home_paths.open_lock``): never emptied, and refused where the home holds a link at it."""
        self._dir.mkdir(parents=True, exist_ok=True)
        with home_paths.open_lock(self._dir / _LOCK_NAME) as fd:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)

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

    def append_sync(self, run: ScheduleRun) -> None:
        # 🔴 REDACT BEFORE WRITE (criterion 11). The criterion is explicit that
        # `{{secret:KEY}}` "never appears resolved in triggers.json, journals, LEDGER, or
        # `automation_history` output". The API's `_redact_run` cleans the response, but
        # nothing cleaned the WRITE — a bash action that echoed a resolved credential put it in
        # plaintext into `cron-history/<job>.jsonl` AND `_index.jsonl`, both 0600 but both on disk,
        # both carried by `personalclaw snapshot`, and both readable by anything that reads
        # the home. Redacting only on read is a read-path control over a storage-path leak.
        #
        # At the single write point, deliberately: `append_sync` is the one funnel every run record
        # passes through, so a future caller cannot forget it — the per-call-site alternative is how
        # the screen and the fence gaps happened.
        run.summary = _redact_stored(run.summary)[:_SUMMARY_CAP]
        run.trace = _redact_stored(run.trace)[:_TRACE_CAP]
        run.error = _redact_stored(run.error)
        run.job_name = _redact_stored(run.job_name)
        job_path = self._job_path(run.job_id)
        with self._lock():
            self._dir.mkdir(parents=True, exist_ok=True)
            # The work this row names ended before the row was written: it is written as it ended.
            early = (
                _EARLY_ENDINGS.pop((str(self._dir), run.job_id, run.work_id), None)
                if run.work_id and run.status in UNSETTLED_STATUSES
                else None
            )
            if early is not None:
                run.status = early["status"]
                run.summary, run.trace, run.error = early["summary"], early["trace"], early["error"]
                run.finished_at = max(run.started_at, early["finished_at"])
                run.duration_ms = int((run.finished_at - run.started_at) * 1000)
                run.trigger = _exit_after(run.trigger, early)
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

    def rewrite_sync(self, change: Callable[[dict[str, Any]], dict[str, Any]]) -> bool:
        """Pass every row of the history through *change*, under the lock, and write back each
        file a row of which it changed; whether any did. For a pass that takes something out of
        what was recorded (``workflows.input_secrets.redact_home``): no row is added, dropped or
        moved."""
        if not self._dir.is_dir():
            return False
        changed = False
        with self._lock():
            for path in sorted(self._dir.glob("*.jsonl")):
                rows = self._read_jsonl(path)
                rewritten = [change(row) for row in rows]
                if rewritten != rows:
                    self._write_jsonl(path, rewritten)
                    changed = True
        return changed

    async def append(self, run: ScheduleRun) -> None:
        import asyncio

        await asyncio.to_thread(self.append_sync, run)

    def settle_sync(
        self,
        job_id: str,
        work_id: str,
        *,
        status: str,
        summary: str = "",
        trace: str = "",
        error: str = "",
        finished_at: float = 0.0,
        exit_type: str = "",
    ) -> bool:
        """Say how the work a `launched` or `queued` run started went, on that run's row.

        A fire whose action starts an agent or a workflow run records its run the moment the work
        starts, as `launched` (started is not succeeded), or `queued` behind a run in flight,
        naming the work in `work_id`. When the work ends, this writes how on the same row — its
        `status`, what it said or why it failed, when it finished — in the per-job file and the
        cross-job index both, redacted as `append_sync` redacts. *exit_type* is the exit the work
        ended with, which becomes the row's typed exit (`ScheduleRun.trigger`) unless the run was
        one by hand: the fire's own exit said only that the work started, and the failure count
        reads this one. Only a row that has not settled changes: an ending that arrives twice
        changes nothing the second time.

        Returns whether the ending was taken: written now, or held until its row is written (the
        work ended before its fire recorded the run, see `_EARLY_ENDINGS`). False when there is
        nothing to settle.
        """
        if not job_id or not work_id:
            return False
        import time

        ending = {
            "status": status,
            "summary": _redact_stored(summary)[:_SUMMARY_CAP],
            "trace": _redact_stored(trace or summary)[:_TRACE_CAP],
            "error": _redact_stored(error),
            "finished_at": finished_at or time.time(),
            "exit": exit_type,
        }
        job_path = self._job_path(job_id)
        with self._lock():
            rows = self._read_jsonl(job_path)
            named = [row for row in rows if row.get("work_id") == work_id]
            if not named:
                key = (str(self._dir), job_id, work_id)
                _EARLY_ENDINGS[key] = ending
                while len(_EARLY_ENDINGS) > _EARLY_ENDINGS_CAP:
                    _EARLY_ENDINGS.pop(next(iter(_EARLY_ENDINGS)))
                return True
            row = named[-1]
            if row.get("status") not in UNSETTLED_STATUSES:
                return False
            run_id = row.get("run_id")
            _settle_row(row, ending, with_trace=True)
            self._write_jsonl(job_path, rows)
            index = self._read_jsonl(self._index)
            for entry in index:
                if entry.get("job_id") == job_id and entry.get("run_id") == run_id:
                    _settle_row(entry, ending, with_trace=False)
            self._write_jsonl(self._index, index)
        return True

    def unsettled_row(self, job_id: str, work_id: str) -> dict[str, Any] | None:
        """The row of *job_id* that has not settled about *work_id* yet, or None.

        What a run cut off before its work ended needs to know of that run: whose it was (its
        ``trigger`` tag says whether it was a run by hand), when it started, and its id.
        """
        if not job_id or not work_id:
            return None
        rows = self._read_jsonl(self._job_path(job_id))
        named = [row for row in rows if row.get("work_id") == work_id]
        if not named or named[-1].get("status") not in UNSETTLED_STATUSES:
            return None
        return named[-1]

    # ── Read (TaskProvider-shaped: returns (rows, total)) ─────────────

    def has_runs(self, job_id: str) -> bool:
        """Whether any run of ``job_id`` is recorded. Sync and cheap: one ``stat``.

        A run's record outlives its trigger — a one-shot retires after its run, and the chat's
        delete keeps the history — so a new trigger must not take an id that still has runs, or
        it would show them as its own (`triggers.tools._unique_id`).
        """
        try:
            return self._job_path(job_id).exists()
        except ValueError:
            return False

    def _list_for_job_sync(
        self, job_id: str, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], int]:
        rows = _newest_first(self._read_jsonl(self._job_path(job_id)))
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
            # 🔴 A SUPPRESSION IS NOT A FIRE. Since suppressed fires began persisting their
            # typed row here (§7 crit 8's "zero silent drops"), this window would otherwise count
            # them — measured, 5 quiet-hours skips read as 5 fires, so a trigger held by its own
            # quiet window would consume the hourly cap it never used and then be refused for
            # "running away". The cap exists to bound work the machine DID.
            #
            # Keyed on `INERT_OUTCOMES`, the same set `history.is_inert` uses, rather than a local
            # list of `skipped_*` strings: one definition of "this did nothing" for both surfaces.
            if str(row.get("status") or "") in INERT_OUTCOMES:
                continue
            # Nor is a fire refused before its action ran, because something it needs is gone
            # (`run_record.record_refusal`, tagged `refused`): it did no work, and counted it would
            # hold the automation's first runs once what it needs is back.
            if str(row.get("trigger") or "") == "refused":
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
        rows = _newest_first(rows)
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

        Newest by each run's own `started_at`, never by its place in the file (`bounded_log`): a
        merge restore brings an archive's older runs in, and a tail that kept the last lines kept
        those and let the home's newest go. The file is written in time order: the two classes are
        partitioned to decide what survives, then re-merged by time, because `count_since` walks
        the file and a file grouped by class would read out of order.
        """
        path = self._job_path(job_id)
        rows = self._read_jsonl(path)
        if len(rows) <= _MAX_RECORDS_PER_JOB:
            return
        rows = bounded_log.in_time_order(rows, at="started_at")
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
        # In time order first (`bounded_log`), so each job's own tail is its newest runs.
        rows = bounded_log.in_time_order(rows, at="started_at")
        per_job: dict[str, list[int]] = {}
        for i, r in enumerate(rows):
            per_job.setdefault(str(r.get("job_id") or ""), []).append(i)
        keep: set[int] = set()
        for idxs in per_job.values():
            keep.update(idxs[-_MAX_INDEX_PER_JOB:])
        # Then the global cap, on the fair-shared set, kept in time order.
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

    def merge_in(self, src_dir: Path, *, left: list[str] | None = None) -> tuple[int, int]:
        """Bring another home's run history in — a snapshot's `cron-history/` — and return
        ``(shards, runs brought in)``.

        A shard this store lacks is copied whole; one it holds gets the runs it lacks, matched on
        ``run_id``, written in time order by ``started_at`` (``bounded_log.merge_jsonl``): the
        archive's runs are older, and the page reads the newest first. Under the lock every append
        takes, so a run the gateway records meanwhile is not written over. Retention stays the
        boot's (:meth:`rotate_all`).

        Each goes where ``durability.home_paths.landing`` says, in the home this store is in:
        where the home holds a link on the way — the folder, its lock or the shard — nothing of
        the archive's is written there, and the link is named on *left*.
        """
        refused = left if left is not None else []
        folder = home_paths.landing(self._base, _HISTORY_DIRNAME, refused)
        lock = home_paths.landing(self._base, f"{_HISTORY_DIRNAME}/{_LOCK_NAME}", refused)
        if folder is None or lock is None:
            return 0, 0
        shards = imported = 0
        with self._lock():
            for src in sorted(Path(src_dir).glob("*.jsonl")):
                dst = home_paths.landing(self._base, f"{_HISTORY_DIRNAME}/{src.name}", refused)
                if dst is None:
                    continue
                if dst.is_file():
                    imported += bounded_log.merge_jsonl(src, dst, key="run_id", at="started_at")
                else:
                    home_paths.put_file(src, dst)
                shards += 1
        return shards, imported

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
