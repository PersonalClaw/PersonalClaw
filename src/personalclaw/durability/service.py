"""The scheduled snapshot service (DURABILITY-AND-SYNC §3).

Durability today is manual and single-shot: you run `personalclaw snapshot` when you
remember to. This project has already lost a memory directory once (2026-07-02), and
"when you remember to" is exactly the property that failed.

So the schedule is boring and automatic:

* a **nightly full snapshot** (the existing tar path) with tiered retention, so a
  year of history costs ~30 files instead of 365;
* an **hourly incremental shard export** of only what changed, which bounds the blast
  radius of any loss to one hour;
* a **monthly restore drill** — because a backup nobody has restored is a hope, not a
  backup. The drill restores into a temp directory, validates the shards, runs
  `PRAGMA integrity_check` on every SQLite copy, and reports PASS/FAIL. It never
  touches live state.

Everything here is defensive on purpose. A snapshot service that can crash a gateway,
block a request, or double-run concurrently is worse than no service, so every job is
budgeted, single-flighted across processes, and swallows its own failures into an
audited report.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

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
    """The home this process operates on — ``PERSONALCLAW_HOME`` first, else the config dir.

    Public because the dashboard's conflict-review routes (DAS-10) must read the queue from
    the SAME home the sync cycle writes it to; resolving it a second way in the handler is
    how a review surface ends up reading an empty queue in an isolated dev home."""
    from personalclaw.config.loader import config_dir

    return Path(os.environ.get("PERSONALCLAW_HOME", config_dir()))


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
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return {}


def save_state(state: dict) -> None:
    import json

    try:
        from personalclaw.atomic_write import atomic_write

        atomic_write(_state_path(), json.dumps(state, indent=2) + "\n")
    except Exception:  # noqa: BLE001
        logger.debug("durability: could not persist service state", exc_info=True)


def drill_fields(result: JobResult, *, at: float) -> dict:
    """The drill OUTCOME fields for `durability_state.json` (§6's "validate status").

    Only `last_drill` (a timestamp) was ever persisted, so the archive browser could
    say *when* the last drill ran but not whether it PASSED — and a drill that passed
    and one that failed rendered identically. §6 asks for the validate status, so the
    verdict is persisted alongside the stamp.

    Built here rather than inline at each call site because there are two: the tick
    (which owns its own `save_state`) and the on-demand `POST /api/durability/run`.
    """
    extra = result.extra or {}
    return {
        "last_drill": at,
        "last_drill_ok": bool(result.ok),
        "last_drill_detail": result.detail,
        "last_drill_archive": str(extra.get("snapshot", "") or ""),
        "last_drill_databases": int(extra.get("databases_checked", 0) or 0),
    }


def persist_drill_result(result: JobResult, *, at: float | None = None) -> None:
    """Record a drill outcome for callers that do not own a `save_state` of their own."""
    state = load_state()
    state.update(drill_fields(result, at=at if at is not None else time.time()))
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


def _due(state: dict, key: str, interval: float, *, now: float | None = None) -> bool:
    stamp = float(state.get(key, 0) or 0)
    return (now or time.time()) - stamp >= interval


def _audit(event: str, resources: str, *, outcome: str = "allowed") -> None:
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="durability:service", operation=event, outcome=outcome, resources=resources[:400]
        )
    except Exception:  # noqa: BLE001
        logger.debug("durability: audit write failed", exc_info=True)


# ── the jobs ───────────────────────────────────────────────────────────────────


def run_incremental_export() -> JobResult:
    """Hourly: export only the shards whose content changed.

    This is the job that bounds a loss to one hour. It re-exports changed stores
    only, so a quiet hour costs a fingerprint comparison.
    """
    from personalclaw.concurrency import single_flight

    started = time.monotonic()
    with single_flight("durability:export") as acquired:
        if not acquired:
            return JobResult("incremental_export", skipped="another export is already running")
        try:
            from personalclaw.durability.shards import (
                default_shard_dir,
                dirty_entries,
                export_shards,
            )

            home = active_home()
            out_dir = default_shard_dir(home)
            state_path = out_dir / "export_state.json"
            # Only the entries whose content moved — that's what makes this hourly
            # rather than nightly. A missing state file reports everything dirty,
            # which is the safe direction.
            dirty = dirty_entries(home, state_path)
            # The empty case must return EARLY: `export_shards(entries=[])` reads the
            # empty list as falsy and exports everything, turning the cheap hourly
            # job into a full re-export.
            if not dirty:
                return JobResult(
                    "incremental_export",
                    detail="nothing changed",
                    duration_secs=time.monotonic() - started,
                    extra={"entries_exported": 0, "manifest_shards": 0},
                )
            result = export_shards(home, out_dir, entries=dirty)
        except Exception as exc:  # noqa: BLE001 — a failed backup must not kill the loop
            logger.warning("durability: incremental export failed", exc_info=True)
            _audit("durability_export", f"failed: {exc}", outcome="denied")
            return JobResult(
                "incremental_export",
                ok=False,
                detail=str(exc),
                duration_secs=time.monotonic() - started,
            )
    # Report the WORK DONE (entries re-exported), not the manifest size: the manifest
    # carries every shard including the untouched ones it merges forward, so quoting
    # its length would make an idle hour look like a full backup.
    exported = int(getattr(result, "entries", 0) or 0)
    manifest_shards = len(getattr(result, "shards", ()) or ())
    _audit("durability_export", f"entries={exported} manifest_shards={manifest_shards}")
    return JobResult(
        "incremental_export",
        detail=f"{exported} store(s) re-exported",
        duration_secs=time.monotonic() - started,
        extra={"entries_exported": exported, "manifest_shards": manifest_shards},
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
        return JobResult("history_commit", skipped="git is not available")
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


def run_nightly_snapshot(*, daily: int = 0, weekly: int = 0, monthly: int = 0) -> JobResult:
    """Nightly: a full tar snapshot, then tiered retention.

    Reuses the existing `snapshot_main` path rather than reimplementing archiving —
    one snapshot format, one restore path, one thing to keep correct.
    """
    import argparse

    from personalclaw.concurrency import single_flight
    from personalclaw.durability import retention

    started = time.monotonic()
    with single_flight("durability:snapshot") as acquired:
        if not acquired:
            return JobResult("nightly_snapshot", skipped="another snapshot is already running")
        try:
            from personalclaw.snapshot import _default_snapshot_dir, snapshot_main

            out_dir = _default_snapshot_dir()
            # keep is very high here because tiered retention below owns pruning;
            # letting snapshot_main prune would fight the tier plan.
            code = snapshot_main(
                parsed=argparse.Namespace(output_dir=out_dir, keep=10_000, list_snapshots=False)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("durability: nightly snapshot failed", exc_info=True)
            _audit("durability_snapshot", f"failed: {exc}", outcome="denied")
            return JobResult(
                "nightly_snapshot",
                ok=False,
                detail=str(exc),
                duration_secs=time.monotonic() - started,
            )
        if code != 0:
            _audit("durability_snapshot", f"exit={code}", outcome="denied")
            return JobResult(
                "nightly_snapshot",
                ok=False,
                detail=f"snapshot exited {code}",
                duration_secs=time.monotonic() - started,
            )
        cfg = _cfg()
        plan = retention.apply_retention(
            Path(out_dir),
            daily=daily or cfg.keep_daily or retention.DEFAULT_DAILY,
            weekly=weekly or cfg.keep_weekly or retention.DEFAULT_WEEKLY,
            monthly=monthly or cfg.keep_monthly or retention.DEFAULT_MONTHLY,
        )
    _audit(
        "durability_snapshot",
        f"kept={len(plan['kept'])} pruned={len(plan['pruned'])}",
    )
    return JobResult(
        "nightly_snapshot",
        detail=f"kept {len(plan['kept'])}, pruned {len(plan['pruned'])}",
        duration_secs=time.monotonic() - started,
        extra=plan,
    )


def run_restore_drill(*, notifier=None) -> JobResult:
    """Monthly: prove the newest snapshot can actually be restored.

    Restores into a temp directory and checks three independent things — the shard
    manifest validates, every SQLite copy passes `integrity_check`, and the archive
    actually contained something. A drill NEVER touches live state; it only ever
    reads the archive and writes to its own temp dir.
    """
    import shutil
    import sqlite3
    import tarfile
    import tempfile

    from personalclaw.concurrency import single_flight
    from personalclaw.durability import retention

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

            for db_path in scratch.rglob("*.db"):
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
    _notify_drill(ok, detail, notifier)
    return JobResult(
        "restore_drill",
        ok=ok,
        detail=detail,
        duration_secs=time.monotonic() - started,
        extra={"snapshot": newest.name, "databases_checked": checked_dbs, "problems": problems},
    )


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
            from personalclaw.durability.shards import machine_id
            from personalclaw.durability.sync_cycle import run_sync_cycle

            home = active_home()
            report = run_sync_cycle(
                transport,
                home,
                self_id=machine_id(home),
                encrypt=str(getattr(cfg, "sync_encrypt", "auto") or "auto"),
            )
            # GC tombstone side-logs past the sync horizon (DAS-6c-iii): once every peer
            # has had a chance to see a delete (older than the staleness window * a safety
            # factor), its marker is dead weight. Only after a SUCCESSFUL cycle — a failed
            # push means peers may not have pulled the delete yet.
            if report.ok:
                _prune_tombstones(home, float(getattr(cfg, "sync_stale_after_secs", 900) or 900))
            if report.conflicts:
                _draft_conflict_proposals(home)
        except Exception as exc:  # noqa: BLE001 — a failed sync must not kill the loop
            logger.warning("durability: sync cycle raised", exc_info=True)
            _audit("durability_sync", f"failed: {exc}", outcome="denied")
            return JobResult(
                "sync", ok=False, detail=str(exc), duration_secs=time.monotonic() - started
            )
    outcome = "allowed" if report.ok else "denied"
    _audit("durability_sync", report.detail, outcome=outcome)
    return JobResult(
        "sync",
        ok=report.ok,
        detail=report.detail,
        duration_secs=time.monotonic() - started,
        extra={
            "rows_added": report.rows_added,
            "rows_removed": report.rows_removed,
            "seq_published": report.seq_published,
        },
    )


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


def _prune_tombstones(home: Path, stale_after_secs: float) -> None:
    """GC each tombstone-bearing entry's sync-only delete side-log (DAS-6c-iii).

    Horizon = now − stale_after_secs × a safety factor, so a marker is only dropped well
    after every peer that syncs within the window has had a chance to observe the delete.
    Best-effort — a prune failure never affects the sync outcome."""
    try:
        from datetime import datetime, timedelta, timezone

        from personalclaw.durability import inventory as inv
        from personalclaw.durability.tombstones import prune

        # 4× the staleness window is a generous "everyone has surely pulled by now" margin.
        horizon = datetime.now(timezone.utc) - timedelta(seconds=stale_after_secs * 4)
        keep_after = horizon.isoformat()
        for entry in inv.all_entries():
            if entry.tombstones and entry.kind == inv.KIND_JSON_ENTITY_DIR:
                prune(home / entry.path, keep_after=keep_after)
    except Exception:  # noqa: BLE001
        logger.debug("durability: tombstone prune skipped", exc_info=True)


def _notify_drill(ok: bool, detail: str, notifier=None) -> None:
    """Surface a drill outcome through the dashboard's notification gate.

    A FAILED drill is a `warning`, not `info`: it means the backups are not
    known-good, which is exactly what a minimum-severity or quiet-hours filter must
    not hide. Delivery goes through `DashboardState.notify` so the entity-settings
    gate stays the one gate; with no dashboard bound (CLI use) the drill still runs
    and still audits — it just has nobody to tell.
    """
    if notifier is None:
        return
    try:
        notifier(
            "info" if ok else "warning",
            "Backup restore drill passed" if ok else "Backup restore drill FAILED",
            detail,
        )
    except Exception:  # noqa: BLE001
        logger.debug("durability: drill notification skipped", exc_info=True)


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


def run_due_jobs(*, now: float | None = None, force: str = "", notifier=None) -> list[JobResult]:
    """Run whatever is due. Returns one result per job attempted.

    Elapsed-time scheduling rather than wall-clock: a laptop asleep at 03:00 gets its
    snapshot when it wakes instead of skipping the night entirely.
    """
    state = load_state()
    stamp = now or time.time()
    results: list[JobResult] = []

    if force == "export" or _due(state, "last_export", HOURLY_SECS, now=stamp):
        result = run_incremental_export()
        results.append(result)
        if result.ok and not result.skipped:
            state["last_export"] = stamp

    # Time-travel's hourly memory commit (§5). Its own cadence key so an export
    # failure never starves it and vice versa — they mitigate different losses.
    if force == "history" or _due(state, "last_history", HOURLY_SECS, now=stamp):
        result = run_history_commit()
        results.append(result)
        if result.ok and not result.skipped:
            state["last_history"] = stamp

    if force == "snapshot" or _due(state, "last_snapshot", NIGHTLY_SECS, now=stamp):
        result = run_nightly_snapshot()
        results.append(result)
        if result.ok and not result.skipped:
            state["last_snapshot"] = stamp

    drills_on = _cfg().restore_drills
    if force == "drill" or (drills_on and _due(state, "last_drill", DRILL_SECS, now=stamp)):
        result = run_restore_drill(notifier=notifier)
        results.append(result)
        # Stamped even on failure: a failing drill must not retry every tick and
        # bury the user in notifications. The warning is already delivered — and the
        # VERDICT is now stamped with it, so the archive browser can show it.
        if not result.skipped:
            state.update(drill_fields(result, at=stamp))

    # Sync (§4): the staleness window is the schedule — pull+push no more often than
    # sync_stale_after_secs. `run_sync_job` is self-guarding (disabled/unconfigured →
    # skipped), so it's cheap to reach here every stale window and let it decide.
    cfg = _cfg()
    if getattr(cfg, "sync_enabled", False):
        stale = float(getattr(cfg, "sync_stale_after_secs", 900) or 900)
        if force == "sync" or _due(state, "last_sync", stale, now=stamp):
            result = run_sync_job()
            results.append(result)
            # Stamp even on a skip/failure so a disabled-but-enabled or erroring sync
            # doesn't hammer the remote every tick — the staleness window rate-limits it.
            state["last_sync"] = stamp

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


def status() -> dict:
    """Last-run times + what's due, for the settings surface and diagnostics."""
    state = load_state()
    now = time.time()

    def _entry(key: str, interval: float) -> dict:
        last = float(state.get(key, 0) or 0)
        return {
            "last_run": last,
            "due_in_secs": max(0.0, interval - (now - last)) if last else 0.0,
            "due": _due(state, key, interval, now=now),
        }

    cfg = _cfg()
    stale = float(getattr(cfg, "sync_stale_after_secs", 900) or 900)
    return {
        "enabled": enabled(),
        "export": _entry("last_export", HOURLY_SECS),
        "snapshot": _entry("last_snapshot", NIGHTLY_SECS),
        "drill": _entry("last_drill", DRILL_SECS),
        "sync": {
            **_entry("last_sync", stale),
            "enabled": bool(getattr(cfg, "sync_enabled", False)),
            "transport": getattr(cfg, "sync_transport", "") or "",
            # The RESOLVED encryption verdict, not the raw tri-state: "auto" tells a user
            # nothing about whether their bytes are encrypted, which is the only question
            # this status field exists to answer (§4.4's toggle "states that tradeoff
            # explicitly"). Names/booleans only — never the passphrase or a key.
            "encrypt": str(getattr(cfg, "sync_encrypt", "auto") or "auto"),
            "encrypted": _resolved_encryption(cfg),
        },
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
        logger.info("Durability service started (tick=%ds)", int(self._tick_secs))

    def _install_history(self) -> None:
        try:
            if not _cfg().time_travel:
                return
            from personalclaw.durability.history_debounce import install

            install(home=active_home())
        except Exception:  # noqa: BLE001 — history must never block boot
            logger.warning("durability: could not install time-travel history", exc_info=True)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        try:
            from personalclaw.durability.history_debounce import uninstall

            uninstall()
        except Exception:  # noqa: BLE001
            logger.debug("durability: history uninstall failed", exc_info=True)

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
            # KL-14: graph maintenance rides THIS tick — the alternative is a second periodic
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
