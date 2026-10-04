"""The scheduled snapshot service.

Durability today is manual and single-shot: you run `personalclaw snapshot` when you
remember to. This project has already lost a memory directory once, and
"when you remember to" is exactly the property that failed.

So the schedule is boring and automatic:

* a **nightly full snapshot** (the existing tar path) with tiered retention, so a
  year of history costs ~30 files instead of 365. The snapshot is THE backup: it holds
  every store whole, a folder with every file at its path, and it is what a restore reads;
* an **hourly incremental shard export** of only what changed: a copy of the records
  to review and diff (``durability.shards``), which is not a backup — nothing restores
  from it, and it holds no folder of files;
* a **monthly restore drill** — because a backup nobody has restored is a hope, not a
  backup. The drill unpacks the newest snapshot into a temp directory, checks it holds
  something, runs `PRAGMA integrity_check` on every SQLite copy, and reports PASS/FAIL.
  It never touches live state.

Everything here is defensive on purpose. A snapshot service that can crash a gateway,
block a request, or double-run concurrently is worse than no service, so every job is
budgeted, single-flighted across processes, and swallows its own failures into an
audited report. Swallowed is not silent: every run that happened is recorded with what it
did (:func:`record_job`), a failed one in plain words beside the last one that worked, and
the owner is told once when a job starts failing and once when it works again.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.durability.retention import Snapshot

logger = logging.getLogger(__name__)

# Cadences, in seconds. The nightly/monthly jobs check elapsed time rather than
# wall-clock hours so a machine that sleeps through 03:00 still gets its snapshot on
# the next wake instead of silently skipping the night.
HOURLY_SECS = 60 * 60
NIGHTLY_SECS = 24 * 60 * 60
DRILL_SECS = 30 * 24 * 60 * 60

# How often the loop wakes to see whether anything is due. Short enough to be
# responsive after a sleep, long enough to cost nothing.
TICK_SECS = 5 * 60

_STATE_FILE = "durability_state.json"

# job name -> the `durability_state.json` key whose cadence `_due()` measures. One map, so
# a job that can be run by hand and a job the tick schedules can never stamp different keys
# and drift apart. `drill` is deliberately absent: it carries a verdict, not just a stamp,
# so `job_stamp_fields` builds its whole block.
_STAMP_KEYS = {
    "export": "last_export",
    "history": "last_history",
    "snapshot": "last_snapshot",
}


@dataclass
class JobResult:
    """One job run — what happened, honestly, including the skips."""

    job: str
    ok: bool = True
    skipped: str = ""
    detail: str = ""
    duration_secs: float = 0.0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "job": self.job,
            "ok": self.ok,
            "skipped": self.skipped,
            "detail": self.detail,
            "duration_secs": round(self.duration_secs, 3),
            **({"extra": self.extra} if self.extra else {}),
        }


def active_home() -> Path:
    """The home this process operates on: :func:`~personalclaw.config.loader.config_dir`.

    Public because the dashboard's conflict-review routes (DAS-10) must read the queue from
    the SAME home the sync cycle writes it to; resolving it a second way in the handler is
    how a review surface ends up reading an empty queue in an isolated dev home."""
    from personalclaw.config.loader import config_dir

    return config_dir()


def _state_path() -> Path:
    return active_home() / _STATE_FILE


def load_state() -> dict:
    """Last-run timestamps. A missing or corrupt file reads as "never run".

    Deliberately not fatal: the worst case of losing this file is one extra snapshot,
    while refusing to run because a bookkeeping file is unreadable would defeat the
    entire point.
    """
    import json

    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(state, dict):
        return {}
    # The backfill for a pass recorded before passes had a record of their own: when the last
    # drill passed, it IS the newest verified snapshot. Idempotent, and the next save writes it,
    # so retention holds that snapshot from the first run after an update, not the next drill.
    if "last_verified_archive" not in state and state.get("last_drill_ok") is True:
        state.update(_verified_fields(state.get("last_drill_archive"), state))
    # The backfill for a run recorded before each run kept an outcome of its own: the schedule
    # stamp was written only by a run that worked, so it IS the last run known to have worked.
    # Idempotent, and the next run writes what it did.
    for job in ("export", "snapshot"):
        stamp = state.get(_STAMP_KEYS[job])
        if stamp and f"last_{job}_run" not in state:
            state.update(
                {f"last_{job}_run": stamp, f"last_{job}_ok": True, f"last_{job}_success": stamp}
            )
    return state


def save_state(state: dict) -> None:
    import json

    try:
        from personalclaw.atomic_write import atomic_write

        atomic_write(_state_path(), json.dumps(state, indent=2) + "\n")
    except Exception:  # noqa: BLE001
        logger.debug("durability: could not persist service state", exc_info=True)


def job_stamp_fields(job: str, result: JobResult, *, at: float, previous: dict) -> dict:
    """The `durability_state.json` fields ONE finished job contributes — or `{}`.

    🔴 THE STAMP RULE LIVES HERE, ONCE, because there are two callers and they disagreed:
    the tick (which owns its own batch `save_state`) and the on-demand
    `POST /api/durability/run`. The endpoint stamped only the drill, so a hand-run
    `export`/`snapshot` did the work and recorded nothing — "Last run of each job" stayed
    stale, and `_due()` reads the same stamp, so the scheduler redid the job on the next
    tick (#361). One rule table, so a third caller cannot invent a fourth behaviour.

    A job keeps two records, and reading one as the other is how a failed snapshot read as the
    success before it: the SCHEDULE stamp `_due()` measures, and what a run DID
    (:func:`_outcome_fields` — when, whether it worked, why not, and for how long), for the three
    jobs the Backups page shows a run of: the export, the snapshot and sync. The outcome is read
    against *previous*, because a failure extends a streak.

    The rules are NOT uniform and the difference is deliberate:

    * `export`/`snapshot` stamp the schedule only on a real success — a failed one must stay due
      and be retried on the next tick, which is the whole point of an hourly cadence. Their
      outcome is written on every run that happened, a failure included, so "Last run" is the
      run that happened and the last one that worked stays beside it.
    * an export that ran and left a file out keeps its schedule all the same: another try in five
      minutes cannot carry a file that is still unreadable, and the store it is in stays changed
      (`shards.Changes.except_for`), so the next hourly export reads it again.
    * `drill` stamps on ANY non-skip, failure included: a failing drill must not retry
      every tick and bury the user in notifications. The warning is already delivered,
      so the VERDICT is stamped with it (§6's "validate status") and the archive browser
      can show *whether* the last drill passed, not just when it ran.
    * a drill that PASSES is also recorded as the newest verified snapshot, on its own, so a later
      failing drill cannot erase it: retention holds that snapshot until a newer one passes.
    * `snapshot` keeps its report too, which names what the run removed and kept and why, so a
      nightly pass is as visible on the Backups page as one run from Run now.
    * `sync` stamps its schedule on EVERY attempt, a skip and a failure included, so an erroring
      or unconfigured sync is held to the staleness window instead of reaching for the remote
      every tick. A skip records its reason and leaves the last outcome as it was.

    A skip of any other job returns `{}` — "another run already holds the lock" is not a run, and
    stamping it would let a collision satisfy the schedule.
    """
    if job == "sync":
        fields: dict = {"last_sync": at, "last_sync_skipped": result.skipped}
        if result.skipped:
            return fields
        fields.update(_outcome_fields(job, result, at=at, previous=previous))
        if result.ok:
            # Why the run could not remove the copies a newer one replaced: they stay, and the
            # Backups card says so rather than that the store keeps only the newest.
            fields["sync_removal_failed"] = str(
                (result.extra or {}).get("removal_failed", "") or ""
            )
        return fields
    if result.skipped:
        return {}
    if job == "drill":
        extra = result.extra or {}
        fields = {
            "last_drill": at,
            "last_drill_ok": bool(result.ok),
            "last_drill_detail": result.detail,
            "last_drill_archive": str(extra.get("snapshot", "") or ""),
            "last_drill_databases": int(extra.get("databases_checked", 0) or 0),
        }
        if result.ok and fields["last_drill_archive"]:
            fields.update(_verified_fields(fields["last_drill_archive"], fields))
        return fields
    key = _STAMP_KEYS.get(job, "")
    if not key:
        return {}
    if job == "history":
        return {key: at} if result.ok else {}
    fields = {}
    if result.ok or (result.extra or {}).get("failure") == "left_out":
        fields[key] = at
    if result.ok and job == "snapshot":
        fields["last_snapshot_detail"] = result.detail
    fields.update(_outcome_fields(job, result, at=at, previous=previous))
    return fields


def _outcome_fields(job: str, result: JobResult, *, at: float, previous: dict) -> dict:
    """What one run of *job* did: when, whether it worked, and, when it did not, the failure's code,
    the system's or the transport's own words, the folder it was writing to, and how long the
    streak has lasted (read against *previous*). Written only by a run that happened."""
    fields: dict = {f"last_{job}_run": at, f"last_{job}_ok": bool(result.ok)}
    if result.ok:
        fields.update(
            {
                f"last_{job}_success": at,
                f"{job}_failure": "",
                f"{job}_failure_reason": "",
                f"{job}_failure_folder": "",
                f"{job}_failing_since": 0.0,
                f"{job}_failures": 0,
            }
        )
        return fields
    extra = result.extra or {}
    streak = previous.get(f"last_{job}_ok") is False
    fields.update(
        {
            # The code and the system's own words, not a finished sentence: `PROBLEMS` stays the
            # one source of the wording, including for a record written before it changed.
            f"{job}_failure": str(extra.get("failure", "") or "error"),
            f"{job}_failure_reason": str(extra.get("reason", "") or result.detail),
            f"{job}_failure_folder": str(extra.get("folder", "") or ""),
            f"{job}_failing_since": (
                float(previous.get(f"{job}_failing_since", 0) or 0) if streak else at
            ),
            f"{job}_failures": (int(previous.get(f"{job}_failures", 0) or 0) if streak else 0) + 1,
        }
    )
    return fields


def _verified_fields(archive: object, drill: dict) -> dict:
    """The newest-verified record, from the `last_drill_*` fields of the drill that passed."""
    return {
        "last_verified_archive": str(archive or ""),
        "last_verified_at": float(drill.get("last_drill", 0) or 0),
        "last_verified_detail": str(drill.get("last_drill_detail", "") or ""),
        "last_verified_databases": int(drill.get("last_drill_databases", 0) or 0),
    }


def record_job(state: dict, job: str, result: JobResult, *, at: float, notifier=None) -> dict:
    """Record one finished run of *job* in *state*, and raise the note it is news for.

    🔴 THE ONE PATH EVERY JOB'S OUTCOME TAKES, the tick's and Run now's alike, the four jobs the
    owner is told about (the export, the snapshot, the drill and sync) included: the stamp rule
    (:func:`job_stamp_fields`) and the note rule (:func:`_note`) are each defined once, so the
    schedule and the button cannot record or announce one run two ways. The note reads the run
    before this one, so it is raised before the record is folded in. Returns the fields recorded.
    """
    fields = job_stamp_fields(job, result, at=at, previous=state)
    _notify(job, state, fields, notifier)
    state.update(fields)
    return fields


def persist_job_result(
    job: str, result: JobResult, *, at: float | None = None, notifier=None
) -> None:
    """Record one job outcome for callers that do not own a `save_state` of their own."""
    state = load_state()
    at = at if at is not None else time.time()
    if record_job(state, job, result, at=at, notifier=notifier):
        save_state(state)


def last_drill() -> dict:
    """The last drill's verdict, for the archive browser. ``ran`` is False when none has.

    A drill that has never run reports ``ran: False`` rather than a fabricated pass —
    "not yet verified" is the honest state of a fresh install, and rendering it as
    green would be the worst possible lie for a backup surface to tell.
    """
    state = load_state()
    at = float(state.get("last_drill", 0) or 0)
    if not at:
        return {"ran": False, "ok": None, "at": 0.0, "detail": "", "archive": ""}
    return {
        "ran": True,
        # `last_drill_ok` absent means the stamp predates outcome recording: report
        # None (unknown), never True.
        "ok": state.get("last_drill_ok") if "last_drill_ok" in state else None,
        "at": at,
        "detail": str(state.get("last_drill_detail", "") or ""),
        "archive": str(state.get("last_drill_archive", "") or ""),
        "databases_checked": int(state.get("last_drill_databases", 0) or 0),
    }


def last_verified() -> dict:
    """The newest snapshot a restore drill PASSED on, which retention holds; ``archive`` is
    ``""`` when no drill has passed.

    Apart from :func:`last_drill` because a failing drill replaces that one: after a pass on one
    snapshot and a failure on the next, the first is the only snapshot known to restore.
    """
    state = load_state()
    return {
        "archive": str(state.get("last_verified_archive", "") or ""),
        "at": float(state.get("last_verified_at", 0) or 0),
        "detail": str(state.get("last_verified_detail", "") or ""),
        "databases_checked": int(state.get("last_verified_databases", 0) or 0),
    }


def _due(state: dict, key: str, interval: float, *, now: float | None = None) -> bool:
    stamp = float(state.get(key, 0) or 0)
    return (now or time.time()) - stamp >= interval


def _audit(
    event: str, resources: str, *, outcome: str = "allowed", metadata: dict | None = None
) -> None:
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="durability:service",
            operation=event,
            outcome=outcome,
            resources=resources[:400],
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001
        logger.debug("durability: audit write failed", exc_info=True)


# ── the jobs ───────────────────────────────────────────────────────────────────

#: What a full disk raises, and what a refused write raises.
_DISK_FULL = frozenset({errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)})
_REFUSED = frozenset({errno.EACCES, errno.EPERM, errno.EROFS})


def _failure(exc: BaseException, folder: Path | None) -> dict:
    """What *exc*, raised by a job writing into *folder*, means: the code :data:`PROBLEMS` has
    words for, the system's own words, and the folder the sentence names.

    A full disk is ``disk_full``, wherever it filled. A refusal on the job's own folder, or on one
    it had to make on the way there, is ``unwritable``. Anything else is ``error``, quoted as it
    was raised: a refused read of some file in the home is not a folder the owner should change.
    """
    where = str(folder or "")
    if isinstance(exc, OSError) and folder is not None:
        words = str(exc.strerror or exc)
        reason = words[:1].lower() + words[1:]
        if exc.errno in _DISK_FULL:
            return {"failure": "disk_full", "reason": reason, "folder": _filled(exc, folder)}
        if exc.errno in _REFUSED and _on_the_way(exc.filename, folder):
            return {"failure": "unwritable", "reason": reason, "folder": where}
    return {"failure": "error", "reason": str(exc) or type(exc).__name__, "folder": where}


def _on_the_way(path: object, folder: Path) -> bool:
    """Whether *path* is *folder*, a path inside it, or a folder above it that a write there
    needs."""
    if not isinstance(path, (str, bytes)) or not path:
        return False
    named = Path(os.path.realpath(os.fsdecode(path)))
    target = Path(os.path.realpath(folder))
    return named == target or named.is_relative_to(target) or target.is_relative_to(named)


def _filled(exc: OSError, folder: Path) -> str:
    """The folder on the disk that filled: the job's own, unless the system named a path elsewhere —
    a snapshot copies the home into the temporary folder before it writes the archive."""
    for name in (exc.filename2, exc.filename):
        if isinstance(name, (str, bytes)) and name and not _on_the_way(name, folder):
            path = Path(os.path.realpath(os.fsdecode(name)))
            temporary = Path(os.path.realpath(tempfile.gettempdir()))
            return str(temporary if path.is_relative_to(temporary) else path.parent)
    return str(folder)


def _failed(job: str, name: str, failure: dict, started: float) -> JobResult:
    """A failed run of *job*: its answer is the sentence the Backups page keeps (Run now shows it),
    and its record keeps the code and the words the sentence is made from."""
    sentence, _remedy = job_problem(job, failure["failure"], failure["reason"], failure["folder"])
    return JobResult(
        name, ok=False, detail=sentence, duration_secs=time.monotonic() - started, extra=failure
    )


def run_incremental_export() -> JobResult:
    """Hourly: export only the shards whose content changed.

    A copy of the records to review and diff, and not a backup: a restore reads the nightly
    snapshot (``durability.shards``). It re-exports changed stores only, so a quiet hour costs
    a fingerprint comparison. What changed is recorded as exported only once the export of it
    is written, so a run that fails is tried again in full rather than reading "nothing changed".
    """
    from personalclaw.concurrency import single_flight

    started = time.monotonic()
    out_dir: Path | None = None
    with single_flight("durability:export") as acquired:
        if not acquired:
            return JobResult("incremental_export", skipped="another export is already running")
        try:
            from personalclaw.durability.shards import (
                default_shard_dir,
                dirty_entries,
                export_shards,
                left_out_sentence,
                mark_exported,
            )

            home = active_home()
            out_dir = default_shard_dir(home)
            state_path = out_dir / "export_state.json"
            # Only the entries whose content moved — that's what makes this hourly
            # rather than nightly. A missing state file reports everything dirty,
            # which is the safe direction.
            changes = dirty_entries(home, state_path)
            # The empty case must return EARLY: `export_shards(entries=[])` reads the
            # empty list as falsy and exports everything, turning the cheap hourly
            # job into a full re-export.
            if not changes.entries:
                mark_exported(state_path, changes)
                return JobResult(
                    "incremental_export",
                    detail="nothing changed",
                    duration_secs=time.monotonic() - started,
                    extra={"entries_exported": 0, "manifest_shards": 0},
                )
            result = export_shards(home, out_dir, entries=changes.entries)
            # A store whose file the export could not carry stays changed, so every export reads
            # it again until it can, rather than one export of another store reading as whole.
            mark_exported(state_path, changes.except_for(result.left_out_entries))
        except Exception as exc:  # noqa: BLE001 — a failed backup must not kill the loop
            logger.warning("durability: incremental export failed", exc_info=True)
            _audit("durability_export", f"failed: {exc}", outcome="denied")
            return _failed("export", "incremental_export", _failure(exc, out_dir), started)
    # Report the WORK DONE (entries re-exported), not the manifest size: the manifest
    # carries every shard including the untouched ones it merges forward, so quoting
    # its length would make an idle hour look like a full backup.
    exported = int(getattr(result, "entries", 0) or 0)
    manifest_shards = len(getattr(result, "shards", ()) or ())
    left_out = dict(getattr(result, "left_out", {}) or {})
    _audit("durability_export", f"entries={exported} manifest_shards={manifest_shards}")
    extra = {"entries_exported": exported, "manifest_shards": manifest_shards, "left_out": left_out}
    if left_out:
        # A file the export could not carry is said, never dropped in silence: an export that
        # lacks it is not the copy it reads as.
        failure = {"failure": "left_out", "reason": left_out_sentence(left_out), "folder": ""}
        failed = _failed("export", "incremental_export", failure, started)
        failed.extra.update(extra)
        return failed
    return JobResult(
        "incremental_export",
        detail=f"{exported} store(s) re-exported",
        duration_secs=time.monotonic() - started,
        extra=extra,
    )


def run_history_commit() -> JobResult:
    """Hourly git commit of the memory tree — §3's deferred piece, owned by §5.

    Independent of the debouncer: the debouncer only fires when something wrote
    through `atomic_write`, and the memory markdown tree is edited by paths that
    do not all funnel there. An hour is therefore the guaranteed ceiling on how
    much memory history can be missing, which is the direct mitigation for the
    2026-07-02 loss.
    """
    from personalclaw.concurrency import single_flight
    from personalclaw.durability import state_history

    start = time.perf_counter()
    if not _cfg().time_travel:
        return JobResult("history_commit", skipped="time travel is off")
    if not state_history.git_available():
        from personalclaw.net.git import git_problem

        # No git on PATH, or one older than PersonalClaw's git runs: the reason says which.
        return JobResult("history_commit", skipped=git_problem() or "git is not available")
    with single_flight("durability:history") as acquired:
        if not acquired:
            return JobResult("history_commit", skipped="another history commit is running")
        results = state_history.commit_memory_roots(home=active_home())
    changed = [r for r in results if r.get("changed")]
    failed = [r for r in results if not r.get("ok")]
    _audit(
        "durability.history_commit", "state-history", outcome="allowed" if not failed else "error"
    )
    return JobResult(
        "history_commit",
        ok=not failed,
        detail=(
            f"{len(changed)} root(s) committed"
            if not failed
            else f"{len(failed)} root(s) failed: {failed[0].get('error', '')}"
        ),
        duration_secs=time.perf_counter() - start,
        extra={"roots": results},
    )


def run_nightly_snapshot(
    *, daily: int | None = None, weekly: int | None = None, monthly: int | None = None
) -> JobResult:
    """Nightly: a full tar snapshot, then tiered retention.

    Reuses the existing `snapshot_main` path rather than reimplementing archiving —
    one snapshot format, one restore path, one thing to keep correct.

    The tier overrides are ``None``-sentinelled, not ``0``-sentinelled, because ``0``
    is a MEANINGFUL budget: the panel, the PATCH allowlist and
    ``GET /api/durability/archive``'s keep-vs-prune preview all promise "0 disables a
    tier", and ``plan_retention``'s ``max(0, …)`` implements exactly that. An ``or``
    chain here read 0 as "unset" and substituted the built-in default, so the preview
    listed a zeroed tier's snapshots as ``would_prune`` and the nightly run then kept
    them — the two surfaces disagreed at precisely the value the copy calls out.
    """
    import argparse

    from personalclaw.concurrency import single_flight
    from personalclaw.durability import retention

    started = time.monotonic()
    out_dir = ""
    with single_flight("durability:snapshot") as acquired:
        if not acquired:
            return JobResult("nightly_snapshot", skipped="another snapshot is already running")
        try:
            from personalclaw.snapshot import _default_snapshot_dir, snapshot_main

            out_dir = _default_snapshot_dir()
            before = {s.name for s in retention.list_snapshots(Path(out_dir))}
            # keep is very high here because tiered retention below owns pruning;
            # letting snapshot_main prune would fight the tier plan.
            code = snapshot_main(
                parsed=argparse.Namespace(output_dir=out_dir, keep=10_000, list_snapshots=False)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("durability: nightly snapshot failed", exc_info=True)
            _audit("durability_snapshot", f"failed: {exc}", outcome="denied")
            failure = _failure(exc, Path(out_dir) if out_dir else None)
            return _failed("snapshot", "nightly_snapshot", failure, started)
        if code != 0:
            _audit("durability_snapshot", f"exit={code}", outcome="denied")
            failure = {"failure": "error", "reason": f"snapshot exited {code}", "folder": out_dir}
            return _failed("snapshot", "nightly_snapshot", failure, started)
        taken = next(
            (s for s in retention.list_snapshots(Path(out_dir)) if s.name not in before), None
        )
        cfg = _cfg()
        # `_cfg()` already falls back to `DurabilityConfig()` when config is unreadable,
        # and `load()` clamps each tier to a concrete int, so the config value IS the
        # effective budget — including 0. No second default layer here.
        plan = retention.apply_retention(
            Path(out_dir),
            verified=last_verified()["archive"],
            daily=cfg.keep_daily if daily is None else daily,
            weekly=cfg.keep_weekly if weekly is None else weekly,
            monthly=cfg.keep_monthly if monthly is None else monthly,
        )
    plan["taken"] = taken.name if taken else ""
    detail = _snapshot_report(taken, plan)
    # The run record: the sentence, and every name it removed with why, which a long list would
    # cut off the end of the sentence's 400 characters.
    _audit(
        "durability_snapshot",
        detail,
        metadata={k: plan[k] for k in ("taken", "held", "reasons", "bytes_freed", "tiers")},
    )
    return JobResult(
        "nightly_snapshot",
        detail=detail,
        duration_secs=time.monotonic() - started,
        extra=plan,
    )


#: The names a report spells out for one reason before it counts the rest.
_NAMED = 3


def _names(names: list[str]) -> str:
    """``A``, ``A and B``, ``A, B and C``, ``A, B, C and 4 more``."""
    if len(names) > _NAMED:
        return ", ".join(names[:_NAMED]) + f" and {len(names) - _NAMED} more"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _snapshot_report(taken: Snapshot | None, plan: dict) -> str:
    """What one snapshot run did, in the words Run now shows and the Backups page keeps.

    It names the archive it made, each snapshot retention removed with why, and the verified one
    it held (:data:`retention.HELD`). "kept 1, pruned 1" read as nothing at all to a person
    whose only verified snapshot it had just deleted.
    """
    from personalclaw.durability import retention
    from personalclaw.durability.footprint import human_bytes

    parts = [
        f"Created {taken.name} ({human_bytes(taken.size)})." if taken else "Created a snapshot."
    ]
    pruned: list[str] = plan["pruned"]
    by_reason: dict[str, list[str]] = {}
    for name in pruned:
        by_reason.setdefault(plan["reasons"][name], []).append(name)
    clauses = [f"{_names(names)}, {reason}" for reason, names in by_reason.items()]
    if not pruned:
        parts.append("No snapshot was removed.")
    elif len(pruned) == 1:
        parts.append(f"Removed {clauses[0]}.")
    else:
        parts.append(f"Removed {len(pruned)} snapshots: {'; '.join(clauses)}.")
    if plan["held"]:
        parts.append(f"Kept {plan['held']} as well: {retention.HELD}.")
    return " ".join(parts)


def run_restore_drill() -> JobResult:
    """Monthly: prove the newest snapshot can actually be restored.

    Restores into a temp directory and checks three independent things — the shard
    manifest validates, every SQLite copy passes `integrity_check`, and the archive
    actually contained something. A drill NEVER touches live state; it only ever
    reads the archive and writes to its own temp dir. Its verdict is announced where every
    job's outcome is (:func:`record_job`).
    """
    import shutil
    import tarfile
    import tempfile

    from personalclaw.concurrency import single_flight
    from personalclaw.durability import retention
    from personalclaw.sqlite_compat import sqlite3

    started = time.monotonic()
    with single_flight("durability:drill") as acquired:
        if not acquired:
            return JobResult("restore_drill", skipped="another drill is already running")
        try:
            from personalclaw.snapshot import _default_snapshot_dir

            snapshots = retention.list_snapshots(Path(_default_snapshot_dir()))
        except Exception as exc:  # noqa: BLE001
            return JobResult("restore_drill", ok=False, detail=str(exc))
        if not snapshots:
            return JobResult("restore_drill", skipped="no snapshot to drill yet")

        newest = snapshots[0]
        scratch = Path(tempfile.mkdtemp(prefix="pc-drill-"))
        problems: list[str] = []
        checked_dbs = 0
        try:
            try:
                with tarfile.open(newest.path, "r:gz") as tar:
                    # `data` filter: a drill must never be a path-traversal vector.
                    tar.extractall(scratch, filter="data")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"archive did not extract: {exc}")

            files = [p for p in scratch.rglob("*") if p.is_file()]
            if not files:
                problems.append("archive extracted to nothing")

            # Each database the archive holds, and only those: a file SQLite reads as one, and a
            # file where the manifest declares one. Read by its `.db` name, a user's own
            # `notes.db` in the workspace failed every drill of the snapshot that carried it.
            from personalclaw.durability.sqlite_files import databases_in

            for db_path in databases_in(scratch):
                checked_dbs += 1
                try:
                    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                    try:
                        row = conn.execute("PRAGMA integrity_check").fetchone()
                    finally:
                        conn.close()
                    if not row or str(row[0]).lower() != "ok":
                        problems.append(f"{db_path.name}: integrity_check said {row and row[0]}")
                except sqlite3.Error as exc:
                    problems.append(f"{db_path.name}: {exc}")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    ok = not problems
    detail = (
        f"{newest.name}: {checked_dbs} database(s) verified"
        if ok
        else f"{newest.name}: " + "; ".join(problems[:4])
    )
    _audit("durability_drill", detail, outcome="allowed" if ok else "denied")
    return JobResult(
        "restore_drill",
        ok=ok,
        detail=detail,
        duration_secs=time.monotonic() - started,
        extra={"snapshot": newest.name, "databases_checked": checked_dbs, "problems": problems},
    )


#: The Backups page's address, where every note about a backup job sends the reader.
BACKUPS_URL = "#/settings/durability"

#: Each way a job fails, in the words the Backups page, the Doctor and the failure note all use, and
#: what to do about it. One table, so no two of them can describe one failure two ways. ``{reason}``
#: is the transport's or the system's own words, or the refusal's sentence, where the step has one,
#: and ``{folder}`` the folder the job was writing to.
PROBLEMS: dict[str, dict[str, tuple[str, str]]] = {
    "sync": {
        "passphrase": (
            "Nothing was synced: shards are encrypted for this transport, and no sync passphrase "
            "was saved on this machine.",
            "Save a sync passphrase under Settings → Backups → Sync, and use the same one on every "
            "machine that syncs with this one.",
        ),
        "salt": (
            "Nothing was synced: the shared store's encryption salt could not be read or written.",
            "Check that this machine can write to the transport's storage; the next sync tries "
            "again.",
        ),
        "pull": (
            "Nothing was synced: reading the shared store failed ({reason}).",
            "Check the transport's settings under Settings → Providers and that its storage is "
            "reachable; the next sync tries again.",
        ),
        "push": (
            "This machine's changes were not sent: writing to the shared store failed ({reason}).",
            "Check the transport's settings under Settings → Providers and that its storage is "
            "writable; the next sync tries again.",
        ),
        "refused": (
            "The sync ran, and {reason}.",
            "Nothing was written for those paths. Check what the other machine is sending.",
        ),
        "error": (
            "The sync stopped with an error ({reason}).",
            "The gateway log has the details; the next sync tries again.",
        ),
    },
    "export": {
        "unwritable": (
            "The export was not written: PersonalClaw may not write to {folder} ({reason}).",
            "Make that folder writable for the account PersonalClaw runs as.",
        ),
        "disk_full": (
            "The export was not written: the disk that holds {folder} is full.",
            "Free some space on that disk.",
        ),
        "left_out": (
            "The export ran, but {reason}.",
            "Mend or remove those files and the next export is whole; a snapshot holds them as "
            "they are.",
        ),
        "error": (
            "The export stopped with an error ({reason}).",
            "The gateway log has the details.",
        ),
    },
    "snapshot": {
        "unwritable": (
            "The snapshot was not taken: PersonalClaw may not write to {folder} ({reason}).",
            "Make that folder writable for the account PersonalClaw runs as, or choose another "
            "with personalclaw config set snapshot_dir <folder>.",
        ),
        "disk_full": (
            "The snapshot was not taken: the disk that holds {folder} is full.",
            "Free some space on that disk.",
        ),
        "error": (
            "The snapshot was not taken: it stopped with an error ({reason}).",
            "The gateway log has the details.",
        ),
    },
}

#: A system's or a transport's error is quoted, not pasted: a cap keeps one sentence one sentence.
_REASON_CHARS = 240


def job_problem(job: str, code: str, reason: str = "", folder: str = "") -> tuple[str, str]:
    """The sentence and the remedy for one way *job* failed (:data:`PROBLEMS`).

    The one place a system's or a transport's error enters words a person reads, so it is masked
    here: an error that quotes a storage address can carry the login in it, and the Backups page,
    the Doctor and the note must not show it.
    """
    table = PROBLEMS[job]
    sentence, remedy = table.get(code, table["error"])
    return (
        sentence.format(
            reason=_quoted(reason) or "no reason given", folder=_quoted(folder) or "its folder"
        ),
        remedy,
    )


def _quoted(text: str) -> str:
    """*text* masked, on one line, and capped at :data:`_REASON_CHARS`."""
    from personalclaw.security import redact_or_withhold

    words = " ".join(redact_or_withhold(str(text or "")).split())
    return words if len(words) <= _REASON_CHARS else words[: _REASON_CHARS - 1] + "…"


def _snapshots_now() -> dict:
    """The snapshot folder, and the newest snapshot in it: what a restore can bring back now.

    Read from the folder, not from the last run's record: a snapshot taken from the command line
    is one too, and a snapshot removed by hand is not.
    """
    from personalclaw.durability import retention
    from personalclaw.snapshot import _default_snapshot_dir

    folder = _default_snapshot_dir()
    snapshots = retention.list_snapshots(Path(folder))
    newest = (
        {"name": snapshots[0].name, "taken_at": snapshots[0].taken_at.timestamp()}
        if snapshots
        else None
    )
    return {"folder": folder, "newest": newest}


def restorable(snapshot: dict) -> str:
    """What a restore can still bring back, from a status's ``snapshot`` entry (or
    :func:`_snapshots_now`): in the words the failure note, the Doctor and the CLI use."""
    newest = snapshot.get("newest") or {}
    if newest.get("name"):
        return f"The newest snapshot a restore can bring back is {newest['name']}."
    folder = snapshot.get("folder") or "the snapshot folder"
    return f"There is no snapshot in {folder} to restore from."


def run_sync_job() -> JobResult:
    """Run one sync cycle against the configured transport, if sync is enabled (§4).

    Guarded and fail-quiet: sync stays idle unless ``durability.sync_enabled`` is on AND
    a ``sync_transport`` is both named and registered (an installed, enabled transport).
    Any of those absent is a ``skipped`` result, not an error — a not-yet-configured sync
    is a normal state, not a failure. The cycle itself never raises (its report carries
    the error), and it runs under ``single_flight`` so it never overlaps an export.
    """
    from personalclaw.concurrency import single_flight

    started = time.monotonic()
    cfg = _cfg()
    if not getattr(cfg, "sync_enabled", False):
        return JobResult("sync", skipped="sync disabled")
    transport_name = getattr(cfg, "sync_transport", "") or ""
    if not transport_name:
        return JobResult("sync", skipped="no sync transport configured")

    from personalclaw.sync_transports.registry import get_transport

    transport = get_transport(transport_name)
    if transport is None:
        return JobResult("sync", skipped=f"transport {transport_name!r} not installed/enabled")

    with single_flight("durability:sync") as acquired:
        if not acquired:
            return JobResult("sync", skipped="another sync/export is already running")
        try:
            from datetime import datetime, timezone

            from personalclaw.durability.shards import machine_id
            from personalclaw.durability.sync_cycle import run_sync_cycle

            home = active_home()
            report = run_sync_cycle(
                transport,
                home,
                self_id=machine_id(home),
                now=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                encrypt=str(getattr(cfg, "sync_encrypt", "auto") or "auto"),
            )
            if report.conflicts:
                _draft_conflict_proposals(home)
        except Exception as exc:  # noqa: BLE001 — a failed sync must not kill the loop
            logger.warning("durability: sync cycle raised", exc_info=True)
            _audit("durability_sync", f"failed: {exc}", outcome="denied")
            return JobResult(
                "sync",
                ok=False,
                detail=str(exc),
                duration_secs=time.monotonic() - started,
                extra={"failure": "error", "reason": str(exc)},
            )
    # A path another machine named outside what a sync may write is refused, and so is a link this
    # home holds in the way of what a sync writes; the run says so as a failure: it is the one thing
    # in a sync report its owner has to look at.
    ok = report.ok and not report.refused
    _audit("durability_sync", report.detail, outcome="allowed" if ok else "denied")
    extra: dict = {
        "rows_added": report.rows_added,
        "rows_updated": report.rows_updated,
        "rows_removed": report.rows_removed,
        "seq_published": report.seq_published,
        "refused": dict(report.refused),
        "removal_failed": report.removal_failed,
    }
    if not ok:
        from personalclaw.durability.shards import refused_sentence

        failure = report.failure or "refused"
        extra.update(
            failure=failure,
            reason=(
                refused_sentence(report.refused)
                if failure == "refused"
                else report.error.split(": ", 1)[-1]
            ),
        )
    return JobResult(
        "sync",
        ok=ok,
        detail=report.detail,
        duration_secs=time.monotonic() - started,
        extra=extra,
    )


@dataclass(frozen=True)
class _Notes:
    """The notes one job raises: the title of a failed run's, and the title and body of a run that
    worked. ``every_run`` says each verdict is news, not only a failure that starts and one that
    ends."""

    failed: str
    working: str
    #: The body of a note about a run that worked; ``""`` takes the run's own report.
    working_body: str = ""
    every_run: bool = False


#: The four jobs the owner is told about, and the titles they are told in.
NOTES: dict[str, _Notes] = {
    "export": _Notes("Export failed", "Export is working again", "The last export went through."),
    "snapshot": _Notes("Snapshot failed", "Snapshots are working again"),
    "drill": _Notes("Backup restore drill FAILED", "Backup restore drill passed", every_run=True),
    "sync": _Notes("Sync failed", "Sync is working again", "The last sync went through."),
}


def _note(job: str, previous: dict, fields: dict) -> tuple[str, str, str] | None:
    """The note one finished run of *job* raises, as ``(kind, title, body)``, or ``None``.

    A failure is a ``warning``: a backup that is not being taken is what a minimum-severity or
    quiet-hours filter must not hide. A run that fails the way the last one did says nothing new,
    so it raises nothing — the Backups page and the Doctor keep saying it for as long as it lasts —
    while a NEW reason is news, and so is the first run that works again. Every drill verdict is
    news: the drill runs once a month, and it is the proof a snapshot restores.
    """
    notes = NOTES.get(job)
    ok_key = f"last_{job}_ok"
    if notes is None or ok_key not in fields:
        return None
    was_failing = previous.get(ok_key) is False
    if fields[ok_key] is False:
        code = fields.get(f"{job}_failure", "")
        if not notes.every_run and was_failing and previous.get(f"{job}_failure") == code:
            return None
        return "warning", notes.failed, _failure_body(job, fields)
    if notes.every_run or was_failing:
        report = str(fields.get(f"last_{job}_detail", "") or "")
        return "info", notes.working, notes.working_body or report
    return None


def _failure_body(job: str, fields: dict) -> str:
    """What a failure note says: the sentence and the remedy, and for a snapshot what a restore can
    still bring back. The drill says its own report."""
    if job == "drill":
        return str(fields.get("last_drill_detail", "") or "")
    sentence, remedy = job_problem(
        job,
        fields[f"{job}_failure"],
        fields[f"{job}_failure_reason"],
        fields[f"{job}_failure_folder"],
    )
    body = f"{sentence} {remedy}"
    return f"{body} {restorable(_snapshots_now())}" if job == "snapshot" else body


def _notify(job: str, previous: dict, fields: dict, notifier=None) -> None:
    """Deliver :func:`_note`'s note for one run, through the dashboard's notification gate.

    Delivery goes through `DashboardState.notify`, so the entity-settings gate stays the one gate;
    with no dashboard bound (CLI use) the run is still recorded and audited — it just has nobody to
    tell. Every note links to the Backups page.
    """
    if notifier is None:
        return
    try:
        note = _note(job, previous, fields)
        if note is not None:
            kind, title, body = note
            notifier(kind, title, body, meta={"statusUrl": BACKUPS_URL})
    except Exception:  # noqa: BLE001
        logger.debug("durability: %s notification skipped", job, exc_info=True)


def _draft_conflict_proposals(home: Path) -> None:
    """Run the background propose-only merge pass over the fresh conflicts (DAS-7, §4.2).

    Best-effort and fail-open in BOTH directions: the pass itself never raises (a missing
    model leaves each record needs-review with no proposal), and this wrapper swallows even a
    loop/import failure — a conflict is already durably recorded and the local version is
    already authoritative, so a failed draft costs a suggestion, never a conflict.

    The durability jobs run on the service's executor thread, which owns no event loop, so
    ``asyncio.run`` is the correct bridge to the async model call here.
    """
    try:
        from datetime import datetime, timezone

        from personalclaw.durability.conflict_merge import draft_proposals

        now = datetime.now(timezone.utc).isoformat()
        report = asyncio.run(draft_proposals(home, now=now))
        logger.info("durability: conflict merge pass — %s", report.detail)
    except Exception:  # noqa: BLE001 — a draft is a suggestion; never fail the sync for it
        logger.warning("durability: conflict merge pass failed", exc_info=True)


# ── the loop ───────────────────────────────────────────────────────────────────


def _tick_graph_maintenance() -> None:
    """One graph-maintenance tick (KL-14). Never raises; logs only when something ran.

    Kept as a named module function rather than a lambda so the tick's own test can call
    exactly what the loop calls — a lambda would force the test to re-implement the body and
    then it would be asserting its own copy.
    """
    from personalclaw.knowledge import maintenance

    try:
        result = maintenance.run_maintenance()
    except Exception:  # noqa: BLE001 — the guard belongs at the SEAM, not only at the caller
        # `run_maintenance` documents "never raises" and guards itself, so reaching here means
        # the host's own guard was bypassed (a patched internal, an import-time failure). The
        # loop below also wraps this call, but a helper that any caller can invoke should not
        # depend on where it is invoked FROM to be safe — a test calling it directly found
        # exactly that gap.
        logger.warning("graph maintenance raised past its own guard", exc_info=True)
        return
    if not result.ran:
        logger.debug("graph maintenance not due: %s", result.reason)
        return
    if result.errors:
        logger.warning(
            "graph maintenance ran with %d failing pass(es): %s", len(result.errors), result.errors
        )
    if result.total:
        logger.info("graph maintenance processed %d unit(s): %s", result.total, result.per_pass)


def _tick_evals_watchdog(*, notifier=None) -> None:
    """One eval-substrate maintenance tick (EVALUATION-SUBSTRATE §3.1 + §3.2).

    This is the "cron today" the plan asks for: the ablation runner is periodic and the
    model-upgrade watchdog is "an mtime check on the maintenance tick today" (a
    ``Trigger{kind:clock}`` / ``kind:file`` watcher after AUTOMATION-SUBSTRATE). A named
    module function, like ``_tick_graph_maintenance``, so its test calls exactly what the
    loop calls.

    Gated on ``evals.enabled`` — the substrate's OWN switch, off by default — and NOT on
    ``durability.auto_backup``: turning off scheduled backups must not silently disable a
    subsystem it does not name. The ablation half is additionally gated by an empty ablation
    registry (which is what ships), so enabling the substrate cannot by itself start spending
    model calls on a component the user never registered.

    Never raises: a maintenance tick that can break the backup loop is worse than a tick that
    skipped one cadence.
    """
    try:
        if not _cfg_evals_enabled():
            return
    except Exception:  # noqa: BLE001 — an unreadable config is not a reason to raise here
        logger.debug("evals tick: config unreadable", exc_info=True)
        return

    try:
        from personalclaw.evals import model_watchdog

        result = model_watchdog.check(notifier=notifier)
        if result.changed:
            logger.info(
                "model rebind detected (%s → %s): queued %d re-benchmark(s)",
                result.previous_model_fp or "—",
                result.model_fp or "—",
                len(result.queued),
            )
    except Exception:  # noqa: BLE001
        logger.warning("model-upgrade watchdog tick failed", exc_info=True)

    try:
        from personalclaw.evals import ablation

        summary = ablation.run_cadence()
        if summary.get("ran"):
            logger.info(
                "ablation measured %s: %s (delta %s)",
                summary.get("component_id"),
                summary.get("verdict"),
                summary.get("delta"),
            )
        else:
            logger.debug("ablation not run: %s", summary.get("reason"))
    except Exception:  # noqa: BLE001
        logger.warning("ablation cadence tick failed", exc_info=True)


def _cfg_evals_enabled() -> bool:
    from personalclaw.config.loader import AppConfig

    return bool(AppConfig.load().evals.enabled)


async def _prune_expired_runs() -> int:
    """Apply ``workflows.retention_per_def`` to every def that has runs (RET-3).

    This is the run-retention pruner's only caller. Before RET-3 it had NONE: `prune_runs` was
    shipped, tested and documented as "the path that fires without anyone watching", and nothing
    watched because nothing called it — `workflows.retention_per_def` was likewise declared,
    clamped, loaded and PATCH-writable with zero readers. A knob wired to nothing and a pruner
    called by nothing are the same bug seen from two ends, and this function is the seam.

    ASYNC because `prune_runs` is: retention is the second deletion path workspace teardown has
    to cover, so pruning a run stops its services before sweeping its directory.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.workflows import store as wf_store
    from personalclaw.workflows.watchdog import prune_runs

    keep = int(AppConfig.load().workflows.retention_per_def)
    pruned = 0
    for name in wf_store.def_names():
        pruned += await prune_runs(name, keep=keep)
    return pruned


async def _tick_footprint_maintenance() -> None:
    """One footprint-maintenance tick (RET-3): prune expired runs, reclaim the bytes their
    deletion freed, and record a footprint sample.

    **The three steps are one job because the first two are useless apart.** Deleting rows from a
    SQLite store does not shrink the file — the pages are marked free and reused later — so a
    pruner without a reclaim frees nothing a user can see on disk, and a reclaim without a pruner
    has no free pages to return. The sample is recorded LAST, so the series measures the
    steady-state footprint rather than the pre-compaction peak.

    Rides the durability loop for the reason `_tick_graph_maintenance` does — one dispatch path
    rather than two that drift — and sits OUTSIDE the ``durability.auto_backup`` gate for the
    reason that one does: a user who turns off scheduled backups must not silently also stop
    reclaiming disk. They mitigate unrelated failures.

    Never raises: a reclaim pass that can break the backup loop is worse than one that skips a
    cadence. The prune half runs every tick (it is a bounded query and a delete); the reclaim half
    is gated on ``footprint.RECLAIM_SECS`` because VACUUM rewrites every store.
    """
    from personalclaw.concurrency import single_flight
    from personalclaw.durability import footprint

    home = active_home()
    try:
        pruned = await _prune_expired_runs()
        if pruned:
            logger.info("run retention pruned %d expired run(s)", pruned)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a def that will not prune must not cost the reclaim
        logger.warning("run retention pruning failed", exc_info=True)

    if not footprint.reclaim_due(home):
        return
    try:
        # Two VACUUMs racing on one store is the one way this job can lose data, and the CLI's
        # `--reclaim` shares the key so a user running it by hand cannot collide with the tick.
        with single_flight("footprint-reclaim") as acquired:
            if not acquired:
                logger.debug("footprint reclaim already running elsewhere — skipping")
                return
            result = await asyncio.get_running_loop().run_in_executor(
                None, lambda: footprint.reclaim(home)
            )
        footprint.stamp_reclaim(home)
        if result.freed_bytes > 0:
            logger.info(
                "footprint reclaim freed %s across %d store(s)",
                footprint.human_bytes(result.freed_bytes),
                result.stores,
            )
        elif result.growth_bytes > 0:
            logger.info(
                "footprint reclaim completed across %d store(s); no disk space freed "
                "(measured footprint grew %s during compaction)",
                result.stores,
                footprint.human_bytes(result.growth_bytes),
            )
        for store_id, reason in result.skipped.items():
            logger.debug("footprint reclaim skipped %s: %s", store_id, reason)
        footprint.record(home)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — reclaim must never break the backup tick
        logger.warning("footprint reclaim failed", exc_info=True)


def run_due_jobs(*, now: float | None = None, force: str = "", notifier=None) -> list[JobResult]:
    """Run whatever is due. Returns one result per job attempted.

    Elapsed-time scheduling rather than wall-clock: a laptop asleep at 03:00 gets its
    snapshot when it wakes instead of skipping the night entirely.
    """
    state = load_state()
    stamp = now or time.time()
    results: list[JobResult] = []

    def run(job: str, runner) -> None:
        result = runner()
        results.append(result)
        record_job(state, job, result, at=stamp, notifier=notifier)

    # Every branch records through `record_job` rather than assigning its own key: the stamp
    # rule (including "export only on success" vs "drill even on failure") and the note rule are
    # defined once there, so the tick and `POST /api/durability/run` cannot diverge (#361).
    if force == "export" or _due(state, "last_export", HOURLY_SECS, now=stamp):
        run("export", run_incremental_export)

    # Time-travel's hourly memory commit. Its own cadence key so an export
    # failure never starves it and vice versa — they mitigate different losses.
    if force == "history" or _due(state, "last_history", HOURLY_SECS, now=stamp):
        run("history", run_history_commit)

    if force == "snapshot" or _due(state, "last_snapshot", NIGHTLY_SECS, now=stamp):
        run("snapshot", run_nightly_snapshot)

    drills_on = _cfg().restore_drills
    if force == "drill" or (drills_on and _due(state, "last_drill", DRILL_SECS, now=stamp)):
        run("drill", run_restore_drill)

    # Sync: the staleness window is the schedule — pull+push no more often than
    # sync_stale_after_secs. `run_sync_job` is self-guarding (disabled/unconfigured →
    # skipped), so it's cheap to reach here every stale window and let it decide.
    cfg = _cfg()
    if getattr(cfg, "sync_enabled", False):
        stale = float(getattr(cfg, "sync_stale_after_secs", 900) or 900)
        if force == "sync" or _due(state, "last_sync", stale, now=stamp):
            run("sync", run_sync_job)

    if results:
        save_state(state)
    return results


def _resolved_encryption(cfg) -> bool:
    """Whether the CONFIGURED transport's shards will actually be encrypted (§4.4).

    Resolves the tri-state against the transport's own default so the status surface can
    answer "are my bytes readable in that bucket?" instead of echoing "auto". With no
    transport chosen there is nothing to resolve, and nothing is being sent — False.
    """
    name = str(getattr(cfg, "sync_transport", "") or "")
    if not name:
        return False
    from personalclaw.durability.crypto import encryption_enabled_for

    return encryption_enabled_for(name, str(getattr(cfg, "sync_encrypt", "auto") or "auto"))


def _schedule(state: dict, key: str, interval: float, now: float) -> dict:
    """When the schedule stamp *key* was last written, and whether the job is due again."""
    last = float(state.get(key, 0) or 0)
    return {
        "last_run": last,
        "due_in_secs": max(0.0, interval - (now - last)) if last else 0.0,
        "due": _due(state, key, interval, now=now),
    }


def backup_status(state: dict | None = None, *, now: float | None = None) -> dict:
    """Whether automatic backups are on, and the export's and the snapshot's last runs.

    The half of :func:`status` the Doctor's backups check reads, and nothing of sync's: it asks no
    transport and no credential store. For each job `due` runs on the schedule stamp and
    `last_run` is the last run that HAPPENED, so a failed snapshot is never read as the success
    before it, and a skip (another run held the lock) never reads as a run.
    """
    state = load_state() if state is None else state
    now = time.time() if now is None else now
    return {
        "enabled": enabled(),
        "export": {
            **_schedule(state, "last_export", HOURLY_SECS, now),
            **_outcome(state, "export"),
        },
        "snapshot": {
            **_schedule(state, "last_snapshot", NIGHTLY_SECS, now),
            **_outcome(state, "snapshot"),
            # What the last snapshot that worked removed and kept: a nightly pass has no Run now
            # answer, so this line is the only place a person sees what it deleted.
            "detail": str(state.get("last_snapshot_detail", "") or ""),
            # The snapshot folder and the newest snapshot in it, which a restore can bring back
            # whatever the last run did.
            **_snapshots_now(),
        },
    }


def status() -> dict:
    """Last-run times + what's due, for the settings surface and diagnostics."""
    state = load_state()
    now = time.time()
    cfg = _cfg()
    stale = float(getattr(cfg, "sync_stale_after_secs", 900) or 900)
    from personalclaw.durability.crypto import PASSPHRASE_CREDENTIAL, passphrase_stored
    from personalclaw.durability.published import KEEP_PREVIOUS_SECS
    from personalclaw.sync_transports.registry import get_transport

    chosen = get_transport(str(getattr(cfg, "sync_transport", "") or ""))

    return {
        **backup_status(state, now=now),
        "drill": _schedule(state, "last_drill", DRILL_SECS, now),
        "sync": {
            # The schedule stamp is written on a skip too; the run is the last one that happened.
            **_schedule(state, "last_sync", stale, now),
            **_outcome(state, "sync"),
            "skipped": str(state.get("last_sync_skipped", "") or ""),
            "removal_failed": str(state.get("sync_removal_failed", "") or ""),
            "enabled": bool(getattr(cfg, "sync_enabled", False)),
            "transport": getattr(cfg, "sync_transport", "") or "",
            # The RESOLVED encryption verdict, not the raw tri-state: "auto" tells a user
            # nothing about whether their bytes are encrypted, which is the only question
            # this status field exists to answer (the toggle "states that tradeoff
            # explicitly"). Names/booleans only — never the passphrase or a key.
            "encrypt": str(getattr(cfg, "sync_encrypt", "auto") or "auto"),
            "encrypted": _resolved_encryption(cfg),
            "passphrase_credential": PASSPHRASE_CREDENTIAL,
            "passphrase_stored": passphrase_stored(),
            # Which of this machine's copies the store keeps (`durability.published`): `None`
            # while the chosen transport isn't installed and enabled, so nothing is known of it.
            "removes_old_copies": None if chosen is None else bool(chosen.removes_old_copies),
            "keeps_previous_secs": KEEP_PREVIOUS_SECS,
        },
    }


def _outcome(state: dict, job: str) -> dict:
    """What the last run of *job* did, from :func:`_outcome_fields`' record.

    ``ok`` is ``None`` until a run has happened: "never ran" is neither a pass nor a failure.
    ``problem`` carries the plain sentence and its remedy (:data:`PROBLEMS`) and how long it has
    lasted, so a job that keeps failing reads as one that keeps failing; ``last_success`` is the
    last run that worked, which stays beside a failure.
    """
    ran = float(state.get(f"last_{job}_run", 0) or 0)
    failed = bool(ran) and state.get(f"last_{job}_ok") is False
    problem = None
    if failed:
        code = str(state.get(f"{job}_failure", "") or "error")
        message, remedy = job_problem(
            job,
            code,
            str(state.get(f"{job}_failure_reason", "") or ""),
            str(state.get(f"{job}_failure_folder", "") or ""),
        )
        problem = {
            "code": code,
            "message": message,
            "remedy": remedy,
            "since": float(state.get(f"{job}_failing_since", 0) or ran),
            "failures": int(state.get(f"{job}_failures", 0) or 1),
        }
    return {
        "last_run": ran,
        "ok": (not failed) if ran else None,
        "last_success": float(state.get(f"last_{job}_success", 0) or 0),
        "problem": problem,
    }


def _cfg():
    """The durability config section, or defaults when config is unreadable."""
    from personalclaw.config.loader import DurabilityConfig

    try:
        from personalclaw.config.loader import AppConfig

        return AppConfig.load().durability
    except Exception:  # noqa: BLE001 — defaults keep backups running
        logger.debug("durability: config unreadable — using defaults", exc_info=True)
        return DurabilityConfig()


def enabled() -> bool:
    """Whether the scheduled service should run (``durability.auto_backup``).

    Fail-SAFE to ON: losing scheduled backups because a config file was unreadable
    is the failure this whole plan exists to prevent.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().durability.auto_backup)
    except Exception:  # noqa: BLE001
        logger.debug("durability: config unreadable — leaving auto-backup on", exc_info=True)
        return True


class DurabilityService:
    """Boot-started background loop that runs the due jobs.

    Started from the dashboard startup path alongside the other retention loops. All
    work happens on an executor thread: snapshots are tar + sqlite I/O, and blocking
    the event loop for that would stall every request.
    """

    def __init__(self, *, tick_secs: float = TICK_SECS, notifier=None) -> None:
        self._tick_secs = tick_secs
        # `DashboardState.notify`-shaped callable, or None for headless runs.
        self._notifier = notifier
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        # Time-travel's debouncer rides the atomic-write seam, not this loop, so it
        # is installed here rather than inside `_loop`: it must be listening from
        # the first write of the process, and its gate is `time_travel`, not
        # `auto_backup` (they are different promises).
        self._install_history()
        self._install_export_follower()
        logger.info("Durability service started (tick=%ds)", int(self._tick_secs))

    def _install_history(self) -> None:
        try:
            if not _cfg().time_travel:
                return
            from personalclaw.durability.history_debounce import install

            install(home=active_home())
        except Exception:  # noqa: BLE001 — history must never block boot
            logger.warning("durability: could not install time-travel history", exc_info=True)

    def _install_export_follower(self) -> None:
        """The hourly export's other half: a store that can carry a credential is re-exported as
        soon as it is written (`export_follow`). Gated like the hourly export, by ``auto_backup``.
        """
        try:
            if not enabled():
                return
            from personalclaw.durability.export_follow import install

            install(home=active_home())
        except Exception:  # noqa: BLE001 — following writes must never block boot
            logger.warning("durability: could not follow configuration writes", exc_info=True)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        try:
            from personalclaw.durability.history_debounce import uninstall

            uninstall()
        except Exception:  # noqa: BLE001
            logger.debug("durability: history uninstall failed", exc_info=True)
        try:
            from personalclaw.durability.export_follow import uninstall as stop_following

            stop_following()
        except Exception:  # noqa: BLE001
            logger.debug("durability: export follower uninstall failed", exc_info=True)

    async def _loop(self) -> None:
        from personalclaw import shutdown_event

        first = True
        while not shutdown_event.is_set():
            if not first:
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=self._tick_secs)
                    return  # shutdown signalled
                except asyncio.TimeoutError:
                    pass
            first = False
            # Graph maintenance rides THIS tick — the alternative is a second periodic
            # loop, and `gateway.py`'s own rule is one dispatch path "rather than two that
            # drift". But it is deliberately OUTSIDE the `enabled()` gate below: that gate is
            # `durability.auto_backup`, and a user who turns off scheduled backups must not
            # silently also lose knowledge-graph maintenance. They mitigate unrelated
            # failures, and coupling them would make one setting quietly disable a subsystem
            # it does not name. `test_maintenance_runs_with_auto_backup_off` is the proof.
            try:
                await asyncio.get_running_loop().run_in_executor(None, _tick_graph_maintenance)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — maintenance must never break the backup tick
                logger.warning("graph maintenance tick failed", exc_info=True)
            # The eval substrate's cadences (ablation runner + model-upgrade watchdog).
            # Also outside the `enabled()` gate below, for the same reason: `evals.enabled` is
            # their switch, and `durability.auto_backup` must not quietly be a second one.
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, lambda: _tick_evals_watchdog(notifier=self._notifier)
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — evals must never break the backup tick
                logger.warning("evals maintenance tick failed", exc_info=True)
            # Run retention + footprint reclaim. Awaited rather than pushed to an executor
            # like its two neighbours, because the run-retention pruner is genuinely async — it
            # tears a run's workspace down before sweeping its directory — and it pushes its own
            # blocking half (the VACUUM) to an executor from inside. Also outside the `enabled()`
            # gate below: `durability.auto_backup` must not quietly stop reclaiming disk.
            try:
                await _tick_footprint_maintenance()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — reclaim must never break the backup tick
                logger.warning("footprint maintenance tick failed", exc_info=True)
            if not enabled():
                continue
            try:
                results = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: run_due_jobs(notifier=self._notifier)
                )
                for result in results:
                    if result.skipped:
                        logger.debug("durability %s skipped: %s", result.job, result.skipped)
                    elif result.ok:
                        logger.info("durability %s: %s", result.job, result.detail)
                    else:
                        logger.warning("durability %s FAILED: %s", result.job, result.detail)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("durability tick failed", exc_info=True)
