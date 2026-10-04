"""`triggers.json` — the one trigger store.

"One store: `~/.personalclaw/triggers.json` (fcntl + atomic write, absorbing crons.json /
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
  for, and the "MCP-process gotcha" makes that mtime contract mandatory rather than incidental.

So this session is the store and nothing else. The service, the loop and the executor stay out —
those genuinely need the WakeupDispatcher, and the fire path is what they will call.

**Three properties this file exists to guarantee:**

**A broken row never disappears.** `load()` returns EVERY row it read, including the ones with
errors,
each carrying its issues. A store that dropped invalid rows would make an agent-authored typo look
like a trigger the user never created — R15's "silently-dead trigger" in its worst form, because the
user cannot fix what they cannot see. `enabled` is forced False on an error row instead (that is
`parse_trigger`'s own rule), so a broken trigger is VISIBLE and INERT rather than absent.

**A write never truncates the store.** Atomic tmp→rename through the one JSON writer
(`atomic_write.atomic_json_write`) under an exclusive lock. A partial write here is worse than a
lost write: the next `load()` would report every surviving trigger as malformed.

**A concurrent writer is not silently overwritten.** MCP tools mutate the store from a separate
process (the carried-over gotcha), so every mutation re-reads under the lock before writing —
otherwise a chat-created trigger vanishes when the dashboard saves a stale in-memory copy.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from personalclaw import record_files
from personalclaw.atomic_write import atomic_json_write
from personalclaw.triggers.models import Issue, Trigger, parse_trigger
from personalclaw.triggers.provider import TriggerStoreProvider

logger = logging.getLogger(__name__)

#: The store version. Bumped only for a shape change the reader must branch on — an added FIELD does
#: not need it, because `parse_trigger` tolerates unknown keys with a warning by design.
STORE_VERSION = 1

STORE_FILENAME = "triggers.json"


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
        """The store's cross-process advisory lock: `record_files.locked`, a lock FILE beside
        `triggers.json` rather than a lock on it, since the atomic write replaces the store by
        rename, which would invalidate a lock held on the old inode.

        The one lock every writer of the file holds — this store's mutations, and a sync or a
        restore's merge bringing another home's automations in — so none of them writes a store
        built from a copy another has since changed.
        """
        with record_files.locked(self._path):
            yield

    def _write(self, rows: list[dict[str, Any]]) -> None:
        """Through the one JSON writer (``atomic_write.atomic_json_write``): a unique temp file
        renamed over the store, so a partial write never lands, which is worse than a lost one;
        0600 in a 0700 directory under the home, as that writer makes every file it writes there;
        and announced to its post-write subscribers, as every store's write is.

        It wrote a fixed ``triggers.json.tmp`` and renamed it itself, so the store kept the umask's
        mode (0644 on the usual umask) and no subscriber heard of the write.
        """
        payload = {"version": STORE_VERSION, "triggers": rows, "saved_at": time.time()}
        atomic_json_write(self._path, payload)
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
        rather than at the arm sites because the arm path is not the only writer: the run recorder
        (`run_record.record_run`), the failure-dedup path and the autopause it applies each build
        their own `TriggerStore` and write a fired row back through it. `routing.route_upsert`
        returns None on every single-user install (one dict emptiness check, no I/O) and for every
        id no provider serves, which is why this costs nothing when nothing is installed.
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
        # A webhook automation's sender tokens end with it: one made again under the same id (one
        # that never ran, a restore, an import) gets the same address, and a token made for this one
        # must not fire that one.
        if any(r.get("kind") == "webhook" for r in rows if str(r.get("id") or "") == trigger_id):
            from personalclaw.inbound import webhook

            webhook.end_senders_of(f"store:{trigger_id}", actor="system")
        return True

    def set_enabled(self, trigger_id: str, enabled: bool) -> Trigger | None:
        """Toggle one trigger, or None if it is not there.

        Refuses to ENABLE a row with parse errors: `parse_trigger` disabled it because the service
        cannot dispatch it, and flipping the flag would put a trigger the machine cannot run
        into the
        active set — pretending to work is worse than being visibly broken.

        Ends a restore's hold either way (`Trigger.restore_hold`): switched on it is resumed, and
        switched off by hand it is the owner's own pause, which Resume all leaves alone.
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
                trigger.restore_hold = ""
                rows[index] = trigger.to_dict()
                self._write(rows)
                return trigger
        return None

    # ── the cron migration ──

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
                # 🔴 AND IT IS STILL WRITTEN, disabled. A `crons.json` row with an empty or
                # unknown `schedule.kind` exists nowhere else once the file is retired, so dropping
                # it would make the user's job vanish from the list with no error anywhere.
                # `set_enabled` refuses to enable a row that fails validation, so it cannot
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


#: Fields that belong to one home rather than to what a trigger IS: what has HAPPENED to it there,
#: and what the owner decided about it there. A row brought in from another home arrives without
#: them (:func:`arrived_from_another_home`), and two homes never compare them (:func:`what_it_is`),
#: so an armed fire, a run count or a switch from elsewhere is neither taken in as if it had
#: happened here nor read as an edit to review.
#:
#: Every `Trigger` field is either here or part of what the trigger is, and
#: `test_trigger_runtime_fields.py` fails on a field that is neither, so a new stamp cannot ride a
#: merge unclassified.
RUNTIME_FIELDS: tuple[str, ...] = (
    "next_fire_at",
    "last_run_id",
    "run_count",
    "last_success_at",
    "last_failure_at",
    "last_waiting_at",
    # When it last fired HERE — what debounce spaces a fire from, so another home's fire would
    # hold back this home's first one.
    "last_fired_at",
    # Another home's park cooldown, from an outage this home never had.
    "park_retry_after",
    # Another home's alert dedupe: brought in, it would silence this home's first alert of a
    # failure only the other home had reported.
    "last_alert_hash",
    "last_alert_at",
    "health_status",
    "last_error_summary",
    "state",
    # Whether an automation is switched on is a fact about what has happened TO a trigger — a
    # person turned it on or off — and another home's switch is not this one's (#461).
    "enabled",
    # The owner's yes to what its action runs (`triggers.grants`). A yes is given where the owner is
    # shown what runs, so another home's is not this one's: an automation that arrives without one
    # is asked about here when it is switched on, as the Triggers page's switch asks of any row
    # that holds no grant.
    "capabilities",
    # A restore here switched it off until it is resumed here (`triggers.restore_hold`).
    "restore_hold",
)


def drop_spec_key(store: TriggerStore, kind: str, key: str) -> list[str]:
    """Take *key* out of the spec of every *kind* row in *store* that carries it, re-reading under
    the store's lock: the ids of the rows it came out of. For a key the kind no longer reads, so no
    row keeps it; a second call finds nothing. A boot pass's, so not a method of the store an app is
    handed."""
    with store._file_lock():
        rows = store._read_rows()
        dropped: list[str] = []
        for row in rows:
            spec = row.get("spec")
            if row.get("kind") == kind and isinstance(spec, dict) and key in spec:
                del spec[key]
                dropped.append(str(row.get("id") or ""))
        if dropped:
            store._write(rows)
    return dropped


def what_it_is(row: dict[str, Any]) -> dict[str, Any]:
    """A trigger row as a person made it: without :data:`RUNTIME_FIELDS`, and without the step
    keys that loosen whether its agent asks (``legacy_import.without_loosened_keys``), which like a
    grant are the owner's yes in one home.

    What two homes compare to tell whether either changed a trigger, so a fire, a switch or a yes
    in one home is never an edit the other must review; and all that a row from another home
    brings with it (:func:`arrived_from_another_home`).
    """
    from personalclaw.triggers.legacy_import import without_loosened_keys

    kept = {name: value for name, value in row.items() if name not in RUNTIME_FIELDS}
    if "workflow" in kept:
        kept["workflow"] = without_loosened_keys(kept["workflow"])
    return kept


def arrived_from_another_home(row: dict[str, Any]) -> dict[str, Any]:
    """A trigger row from another home, as it is brought into this one. The one rule for every way
    one arrives: a snapshot or archive merge (``snapshot._merge_triggers``), a device sync (the
    ``triggers`` inventory entry's ``arrives``, applied to each automation a peer's store holds),
    and a sync conflict resolved with the other machine's version or a drafted merge of an
    automation this home no longer has (``durability.conflict_resolve``; one it has takes the
    version in as an edit, :func:`edit_arrived_from_another_home`).

    What it is (:func:`what_it_is`) and nothing else: no armed fire, run count, park or alert
    dedupe from elsewhere, and no grant or loosened posture another home's owner gave; and switched
    off, so it does not fire until someone here switches it on, which asks first for what it runs
    (the boot sweep arms it then). Both halves are needed: dropping the fields discards what the
    OTHER home said, and ``enabled: False`` states what THIS home means. With the field merely
    absent, ``parse_trigger``'s default (on) would decide.
    """
    arrived = what_it_is(row)
    arrived["enabled"] = False
    return arrived


def edit_arrived_from_another_home(here: dict[str, Any], edited: dict[str, Any]) -> dict[str, Any]:
    """*edited* — a trigger row this home has (*here*) with the edit another home made to it taken
    in — as this home writes it: a device sync's rule for an automation only the other home changed
    since the two last agreed on it (the ``triggers`` inventory entry's ``edit_arrives``).

    *edited* holds what the automation is as the other home made it (:func:`what_it_is`), and this
    home's own part of it (:data:`RUNTIME_FIELDS`): its switch, what happened to it here, and its
    grant. What follows here is what an edit made here would do. The grant keeps only what the
    edited action still runs as it ran here (``grants.narrow``): a yes is given where the owner is
    shown what runs, so a changed command waits for the owner's yes here, and a renamed automation
    or a new cadence keeps it. And a new cadence re-arms the next fire, which was armed for the old
    one — by the rule every edit made here follows (``arm.next_fire_after_edit``).
    """
    from personalclaw.triggers import grants
    from personalclaw.triggers.arm import next_fire_after_edit

    before, _ = parse_trigger(here)
    after, _ = parse_trigger(edited)
    grants.narrow(after, before)
    out = dict(edited)
    if after.capabilities != before.capabilities:
        out["capabilities"] = after.capabilities
    rearmed = next_fire_after_edit(before, after)
    if rearmed is not None:
        out["next_fire_at"] = rearmed
    return out


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
    """What a trigger is called, for a sentence that names it — ``""`` when it is not there.

    A stored trigger, or a lifecycle hook, which an action it runs names as ``lifecycle:<id>``
    (``hooks.LIFECYCLE_TRIGGER_PREFIX``). Best-effort by design: the callers name a trigger in
    prose that is true without the name too ("A trigger asked to run …"), so an unreadable store
    costs the name and nothing else.
    """
    if not trigger_id:
        return ""
    from personalclaw.hooks import LIFECYCLE_TRIGGER_PREFIX, ScriptHookStore

    try:
        if trigger_id.startswith(LIFECYCLE_TRIGGER_PREFIX):
            hook = ScriptHookStore().get(trigger_id.removeprefix(LIFECYCLE_TRIGGER_PREFIX))
            return str(hook.name or "") if hook is not None else ""
        row = TriggerStore().get(trigger_id)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("could not read the name of trigger %s", trigger_id, exc_info=True)
        return ""
    return str(row.trigger.name or "") if row is not None else ""


#: How much of an instruction a run is titled with when its trigger has no name to lend it.
RUN_TITLE_CHARS = 80


def run_title(trigger_id: str, instruction: str) -> str:
    """What an agent a trigger started is called: the trigger's name, else its instruction.

    The agent's own task opens with the unattended-run framing, so a run named by its task read
    "[AUTONOMOUS RUN — no user is present to reply]" everywhere a person looked, and its completion
    arrived as "Subagent `<id>` completed". The instruction is the text before that framing — what
    the owner asked for ("Remind me to call the dentist") — cut to its first line.
    """
    name = trigger_name(trigger_id).strip()
    if name:
        return name
    first = next((line.strip() for line in (instruction or "").splitlines() if line.strip()), "")
    return first if len(first) <= RUN_TITLE_CHARS else first[: RUN_TITLE_CHARS - 1] + "…"
