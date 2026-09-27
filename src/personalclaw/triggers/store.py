"""`triggers.json` — the one trigger store (§1 / §6 step 2 — S87).

§1: "One store: `~/.personalclaw/triggers.json` (fcntl + atomic write, absorbing crons.json /
hooks.json / event_triggers.json / autonudge config). Parsed with **never-throw structural
validation** (AUTO-R15): typed issue records + closest-match resolution rendered as WARNING chips —
an agent-authored near-miss must never become a silently-dead trigger."

**Why this is buildable now, when S83/S86 recorded the store as blocked.** Those sessions were right
that the store and the SERVICE are separate, and wrong to treat them as one unit: the service needs
the store, not the reverse. Everything the store itself depends on is shipped and was measured
before
this file existed —

* `Trigger.to_dict()` + `parse_trigger()` round-trip losslessly (verified field-by-field: zero
  fields
  fail to survive), so persistence needs no new serializer.
* `parse_trigger` already NEVER raises and already returns closest-match resolution (`'clok'` →
  `closest='clock'`), which is R15's whole requirement. A store that re-implemented validation would
  have a second opinion about what a valid trigger is.
* `migrate_crons()` already consumes a raw `crons.json` dict and reports `lossless`/`unaccounted`.
* `ScheduleService` already ships the exact fcntl-lock + atomic-write + mtime-`_sync` triad §1 asks
  for, and §6's "MCP-process gotcha" makes that mtime contract mandatory rather than incidental.

So this session is the store and nothing else. The service, the loop and the executor stay out —
those genuinely need the WakeupDispatcher, and S86's fire path is what they will call.

**Three properties this file exists to guarantee:**

**A broken row never disappears.** `load()` returns EVERY row it read, including the ones with
errors,
each carrying its issues. A store that dropped invalid rows would make an agent-authored typo look
like a trigger the user never created — R15's "silently-dead trigger" in its worst form, because the
user cannot fix what they cannot see. `enabled` is forced False on an error row instead (that is
`parse_trigger`'s own rule), so a broken trigger is VISIBLE and INERT rather than absent.

**A write never truncates the store.** Atomic tmp→rename under an exclusive lock, matching the
shipped
cron store. A partial write here is worse than a lost write: the next `load()` would report every
surviving trigger as malformed.

**A concurrent writer is not silently overwritten.** MCP tools mutate the store from a separate
process (§6's carried-over gotcha), so every mutation re-reads under the lock before writing —
otherwise a chat-created trigger vanishes when the dashboard saves a stale in-memory copy.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from personalclaw.triggers.models import Issue, Trigger, parse_trigger
from personalclaw.triggers.provider import TriggerStoreProvider

logger = logging.getLogger(__name__)

#: The store version. Bumped only for a shape change the reader must branch on — an added FIELD does
#: not need it, because `parse_trigger` tolerates unknown keys with a warning by design.
STORE_VERSION = 1

STORE_FILENAME = "triggers.json"
LOCK_FILENAME = ".triggers.lock"


@dataclass
class LoadedTrigger:
    """One row as read: the trigger plus whatever was wrong with it.

    A pair rather than issues stashed on `Trigger`: the entity is what gets WRITTEN, and
    persisting a
    parse complaint would make the next read report an issue about an issue. Issues belong to the
    read.
    """

    trigger: Trigger
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity != "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger.to_dict(),
            "issues": [
                {
                    "path": i.path,
                    "message": i.message,
                    "severity": i.severity,
                    "closest": i.closest,
                }
                for i in self.issues
            ],
            "ok": self.ok,
        }


class TriggerStore(TriggerStoreProvider):
    """`triggers.json`, with the shipped cron store's durability discipline.

    The NATIVE implementation of the trigger-store seam (TEAM-SHARED-ENTITIES §3 — TSE-4): it wraps
    `triggers.json` with all three preserved conventions (fcntl lock, mtime change-notify, atomic
    write). The base class is the abstraction a `trigger`-type provider app implements to serve rows
    from somewhere else; this class is what serves them from the local file, and it is the only
    implementation core ships.

    Deliberately NOT a subclass of or wrapper around `ScheduleService`: that class also owns
    the timer,
    the executor and the run store, and inheriting it would drag the whole legacy scheduler
    into a file
    whose job is persistence. The three durability mechanisms are copied because they are the
    *contract* §1 names, not because the code is reusable.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        from personalclaw.config.loader import config_dir

        self._dir = Path(base_dir) if base_dir else config_dir()
        self._path = self._dir / STORE_FILENAME
        self._lock_path = self._dir / LOCK_FILENAME
        self._last_mtime = 0.0

    # ── paths ──

    @property
    def base_dir(self) -> Path:
        """The directory this store lives in. Sidecars (claims, watch state) derive their root from
        it, so a store rooted at a temp dir never writes runtime state into the real home."""
        return self._dir

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.exists()

    # ── locking + durability ──

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Cross-process advisory lock, matching `ScheduleService._file_lock`.

        A separate lock FILE rather than locking `triggers.json` itself: the atomic write
        replaces the
        store by rename, which would invalidate a lock held on the old inode.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        handle = self._lock_path.open("w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()

    def _write(self, rows: list[dict[str, Any]]) -> None:
        """Atomic tmp→rename. A partial write is worse than a lost one."""
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": STORE_VERSION, "triggers": rows, "saved_at": time.time()}
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        try:
            self._last_mtime = self._path.stat().st_mtime
        except OSError:
            self._last_mtime = 0.0

    def changed_on_disk(self) -> bool:
        """Whether another process wrote the store since this instance last read it.

        §6's carried-over gotcha: "MCP tools mutate the store from a separate process; mtime `_sync`
        within the ≤30s poll remains the propagation contract". The service polls this; a mutation
        re-reads regardless.
        """
        try:
            return self._path.stat().st_mtime > self._last_mtime
        except OSError:
            return False

    # ── read ──

    def _read_rows(self) -> list[dict[str, Any]]:
        """Raw rows off disk. Returns [] for a missing or unreadable store.

        A CORRUPT store returns [] and logs rather than raising: the gateway must still boot with a
        damaged triggers file, because a boot failure takes every other subsystem with it. The
        file is
        left untouched so the user can inspect it — silently rewriting a corrupt store would destroy
        the evidence.
        """
        if not self._path.exists():
            self._last_mtime = 0.0
            return []
        try:
            self._last_mtime = self._path.stat().st_mtime
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("triggers.json is unreadable or malformed; treating as empty")
            return []
        rows = data.get("triggers") if isinstance(data, dict) else data
        return [r for r in (rows or []) if isinstance(r, dict)]

    def load(self) -> list[LoadedTrigger]:
        """Every row, INCLUDING the broken ones, each with its issues.

        The load-bearing decision. A store that dropped invalid rows would make an
        agent-authored typo
        indistinguishable from a trigger that was never created — R15's "silently-dead trigger",
        except the user cannot even see it to fix it. `parse_trigger` forces `enabled=False` on an
        error row, so a broken trigger is visible and inert rather than absent and mysterious.
        """
        out: list[LoadedTrigger] = []
        for row in self._read_rows():
            trigger, issues = parse_trigger(row)
            out.append(LoadedTrigger(trigger=trigger, issues=list(issues)))
        return out

    def list_triggers(self, *, kind: str = "", include_broken: bool = True) -> list[Trigger]:
        """The triggers, optionally filtered by kind.

        `include_broken` defaults True so a LISTING surface shows the user their broken row. A
        caller
        that is about to FIRE should pass False — and does not need to, since a broken row is
        `enabled=False` and the fire path never fires a disabled trigger.
        """
        rows = self.load()
        if not include_broken:
            rows = [r for r in rows if r.ok]
        triggers = [r.trigger for r in rows]
        if kind:
            triggers = [t for t in triggers if t.kind == kind]
        return triggers

    def get(self, trigger_id: str) -> LoadedTrigger | None:
        for row in self.load():
            if row.trigger.id == trigger_id:
                return row
        return None

    # ── write ──

    def save_all(self, triggers: list[Trigger]) -> int:
        """Replace the whole store. Returns the row count written.

        Used by the migration and by tests; a normal mutation goes through `upsert`/`delete`, which
        re-read first. A caller that assembled its list from a stale `load()` would drop a
        concurrent
        writer's row, which is exactly what `upsert` exists to prevent.
        """
        with self._file_lock():
            rows = [t.to_dict() for t in triggers]
            self._write(rows)
            return len(rows)

    def upsert(self, trigger: Trigger) -> Trigger:
        """Insert or replace one trigger, RE-READING under the lock first.

        The re-read is the whole point. MCP tools write this store from another process, so a
        mutation
        built on this instance's cached view would silently delete a trigger created in chat thirty
        seconds ago. Read-modify-write inside one lock is the only shape that cannot lose a row.

        🔴 A ROW A REGISTERED `trigger` PROVIDER SERVES IS WRITTEN BACK TO THAT PROVIDER, not here
        (TSE-5). Writing it here instead would put one trigger id in two stores, and since a local
        row WINS every later read, the team's row would silently fork into a local copy and the
        shared file would stop describing what actually runs. The check belongs at this one funnel
        rather than at the arm sites because the arm path is not the only writer: the gateway's
        fire-outcome recorder, its failure-dedup path and its autopause path each build their own
        `TriggerStore` and write a fired row back through it. `routing.route_upsert` returns None
        on every single-user install (one dict emptiness check, no I/O) and for every id no
        provider serves, which is why this
        costs nothing when nothing is installed.
        """
        from personalclaw.triggers import routing

        elsewhere = routing.route_upsert(trigger, native=self)
        if elsewhere is not None:
            return elsewhere
        with self._file_lock():
            rows = self._read_rows()
            updated = [r for r in rows if str(r.get("id") or "") != trigger.id]
            updated.append(trigger.to_dict())
            self._write(updated)
        return trigger

    def delete(self, trigger_id: str) -> bool:
        """Remove one trigger. Returns whether it was there.

        Also re-reads under the lock: deleting from a stale view would resurrect every row another
        process added since the last read.

        Routed to the serving provider for a provider-served id, exactly as `upsert` is: a retire
        (`delete_after_run`) that deleted a local row which never existed would leave the provider's
        copy live and holding an elapsed schedule — the fire storm again, by another route.
        """
        from personalclaw.triggers import routing

        elsewhere = routing.route_delete(trigger_id, native=self)
        if elsewhere is not None:
            return elsewhere
        with self._file_lock():
            rows = self._read_rows()
            kept = [r for r in rows if str(r.get("id") or "") != trigger_id]
            if len(kept) == len(rows):
                return False
            self._write(kept)
            return True

    def set_enabled(self, trigger_id: str, enabled: bool) -> Trigger | None:
        """Toggle one trigger, or None if it is not there.

        Refuses to ENABLE a row with parse errors: `parse_trigger` disabled it because the service
        cannot dispatch it, and flipping the flag would put a trigger the machine cannot run
        into the
        active set — pretending to work is worse than being visibly broken.
        """
        with self._file_lock():
            rows = self._read_rows()
            for index, row in enumerate(rows):
                if str(row.get("id") or "") != trigger_id:
                    continue
                trigger, issues = parse_trigger(row)
                if enabled and any(i.severity == "error" for i in issues):
                    logger.info("refusing to enable %s: it has parse errors", trigger_id)
                    return None
                trigger.enabled = enabled
                rows[index] = trigger.to_dict()
                self._write(rows)
                return trigger
        return None

    # ── the cron migration (§6 step 2) ──

    def migrate_from_crons(
        self, crons_path: Path | str | None = None, *, now: float = 0.0
    ) -> dict[str, Any]:
        """Import `crons.json` into this store, once per home. Returns `migrate_crons`' report plus
        what was written.

        🔴 ONCE, and never over a row the store already has. This used to run on every boot and
        take each job's config from the file — "`crons.json` is the source of truth for what the job
        IS" — which stopped being true when the store became the only writer (S101) and the service
        that wrote the file was deleted (S112). Measured on a scratch home before the change: an
        owner's rename and new action on an imported cron were put back from the file on the next
        restart, and a job added to the file later came in switched on, `created_by: user`, with
        `bash` granted by the next pass. So the file is read once and renamed
        `crons.json.imported-<date>` (kept: `verify-migration` diffs against that copy, and renaming
        it back gives an older build its store); a home holding such a copy has imported, so a
        `crons.json` found again is not read and the Doctor names it. A row already in the store is
        the store's, and is left exactly as it is — which is also why a second boot, or one after a
        crash midway, writes nothing twice.

        Every row it does write goes through `legacy_import.admit`: the file records no consent for
        what its jobs run, so none is carried, and a job that would run anything needing a grant
        arrives switched off to wait for the owner's review.
        """
        from personalclaw.triggers import legacy_import
        from personalclaw.triggers.migrate import migrate_crons

        source = Path(crons_path) if crons_path else (self._dir / "crons.json")
        if not source.exists():
            return {
                "converted": 0,
                "refused": 0,
                "lossless": True,
                "written": 0,
                "reason": "no crons.json",
            }
        if legacy_import.already_imported(source.parent, source.name):
            logger.warning(
                "%s is back after this home imported it; it is not read again (the Doctor says "
                "what to do with it)",
                source,
            )
            return {
                "converted": 0,
                "refused": 0,
                "lossless": True,
                "written": 0,
                "reason": "crons.json was already imported; this copy is not read",
            }
        try:
            store = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {
                "converted": 0,
                "refused": 0,
                "lossless": False,
                "written": 0,
                "reason": "crons.json is unreadable",
            }

        report = migrate_crons(store if isinstance(store, dict) else {})
        payload = report.to_dict()

        # `report.converted` — a list of `Converted`, each with a `.trigger` dict. Measured, not
        # guessed: a first pass read `report.converted_rows`, which does not exist, so `written` was
        # always 0 while `converted` said 1. The migration reported success and persisted nothing —
        # exactly the silent no-op this program keeps finding, in the one path whose whole job
        # is not losing the user's automations.
        written = 0
        waiting = 0
        refused_rows: list[dict[str, Any]] = []
        existing = {row.trigger.id for row in self.load()}
        for converted in getattr(report, "converted", None) or []:
            row = getattr(converted, "trigger", None)
            if not isinstance(row, dict):
                continue
            trigger, issues = parse_trigger(row)
            if trigger.id in existing:
                continue
            errors = [i for i in issues if i.severity == "error"]
            if errors:
                # Recorded, never dropped: a converted row the entity refuses is a contract mismatch
                # between two shipped modules (that is how S87 found `interval`), and the user needs
                # to see WHICH job did not make it rather than a count that silently disagrees.
                refused_rows.append({"id": trigger.id, "errors": [i.message for i in errors]})
                # 🔴 AND IT IS STILL WRITTEN, disabled (S110). A `crons.json` row with an empty or
                # unknown `schedule.kind` exists nowhere else once the file is retired, so dropping
                # it would make the user's job vanish from the list with no error anywhere.
                # `set_enabled` refuses to enable a row that fails validation (S87), so it cannot
                # become a live trigger by accident; the store's `ok=False` + `errors` are what the
                # UI renders, so the job is VISIBLE and says why it is broken.
                trigger.enabled = False
            if legacy_import.admit(trigger):
                waiting += 1
            self.upsert(trigger)
            existing.add(trigger.id)
            if not errors:
                written += 1
        retired = legacy_import.retire(source, now=now)
        legacy_import.audit_import(
            source.name, imported=written + len(refused_rows), waiting=waiting, retired=retired
        )
        payload["written"] = written
        payload["waiting_for_review"] = waiting
        payload["unparseable"] = refused_rows
        payload["retired_to"] = retired.name if retired is not None else ""
        return payload


#: Fields that belong to what has HAPPENED to a trigger in one home, not to what it IS — the
#: fields a snapshot merge drops from a row it brings in from another home (`snapshot.py`), so an
#: armed fire or a run count from elsewhere never arrives here as if it had happened here.
RUNTIME_FIELDS: tuple[str, ...] = (
    "next_fire_at",
    "last_run_id",
    "run_count",
    "last_success_at",
    "last_failure_at",
    "health_status",
    "last_error_summary",
    "state",
    # Whether an automation is switched on is a fact about what has happened TO a trigger — a
    # person turned it on or off — and another home's switch is not this one's (#461).
    "enabled",
)


#: 🔴 `health(store)` USED TO LIVE HERE, and it was the third place this store's warnings went to
#: die (issue 531). It summed `r.warnings` across every row — the one function in the package that
#: acknowledged them — and had **zero production callers**: measured across `src/`, the only two
#: were `tests/test_triggers_store.py`, testing the function on itself.
#:
#: Deleted rather than given a caller. The signal it counted now reaches two surfaces that a user
#: actually looks at, per row and by name: `warnings` rides the wire beside `broken`
#: (`dashboard/handlers/triggers.py::_issue_messages`), and `GET /api/triggers/doctor` folds every
#: loaded row's issues into its findings. A COUNT is strictly weaker than either — the function's
#: own docstring argued that naming the broken ids beats counting them, and the same argument
#: retires it. Inventing a caller to justify a producer is the defect this issue is about, one
#: level up.


def trigger_name(trigger_id: str) -> str:
    """What a stored trigger is called, for a sentence that names it — ``""`` when it is not there.

    Best-effort by design: the callers name a trigger in prose that is true without the name too
    ("A trigger asked to run …"), so an unreadable store costs the name and nothing else.
    """
    if not trigger_id:
        return ""
    try:
        row = TriggerStore().get(trigger_id)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("could not read the name of trigger %s", trigger_id, exc_info=True)
        return ""
    return str(row.trigger.name or "") if row is not None else ""
