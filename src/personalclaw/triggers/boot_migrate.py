"""Run the cron migration at boot, then arm the clock.

**🔴 THE GAP, measured before writing.** `store.migrate_from_crons()` exists, is idempotent, and is
called by **nothing outside tests**. So on a real machine `triggers.json` is EMPTY: every cron still
lives only in `crons.json`, and the unified store the whole substrate reads is blank. Two
consequences that block the rest of the cutover:

1. **Re-pointing `/api/triggers`' schedule backend at the store would show the user ZERO
schedules** —
   every cron would vanish from the Automations page while still firing from the legacy service. The
   API re-point is unbuildable until the store actually holds the crons.
2. **The tick has nothing to fire.** The clock is armed and `overlap` is enforced, but
both act
   on rows that were never imported.

So the migration runs at boot, and then the newly-imported rows are ARMED — an imported cron
with an empty `next_fire_at` is inert (measured).

**Boot-safe by construction.** The import writes only rows the store does not already have
(idempotent, preserves rows authored directly in `triggers.json`), runs once per home and renames
the legacy file `crons.json.imported-<date>` rather than deleting it (`verify-migration` diffs
against that copy), and never raises into boot: a broken or missing legacy file reports a reason
and leaves the store as it was. A gateway that failed to start because a cron file had a typo would
be a far worse outcome than one that starts and reports the problem.

**It grants nothing.** The legacy files record no consent for what their rows run, so every row an
import writes goes through `legacy_import.admit`, and a row that would run anything needing a grant
arrives switched off until the owner switches it on (`triggers/legacy_import.py`, which says why).
Nor does anything else here: this pass used to finish with a capability backfill that granted every
ungranted row whatever it ran, on every start, so an edit that re-pointed an action at `bash` was
allowed `bash` one restart later without anyone being asked. A restart grants nothing now; only the
owner does, on the Triggers page (`triggers/grants.py`).
`verify-migration` is the check that says whether the import is trustworthy, and it is run
here so the answer is in the log at the moment it matters.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from personalclaw import record_files
from personalclaw.config import loader as config_loader


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)


def migrate_and_arm(base_dir: Path | str | None = None, *, now: float = 0.0) -> dict[str, Any]:
    """Import `crons.json` into the trigger store, then arm every unarmed clock trigger.

    Returns a report a caller can log or surface. Never raises: every failure path reports a reason
    and leaves the store unchanged, because this runs during gateway boot.

    The order matters. Importing without arming leaves the rows inert (an imported
    cron has no `next_fire_at`, and `due_ids` only surfaces rows that have one), and arming before
    importing has nothing to arm.
    """
    from personalclaw.triggers.store import TriggerStore, unreadable

    # Resolved through THIS module's `config_dir` so there is exactly one place to redirect the
    # boot migration's home — which is what `tests/conftest.py::_isolate_trigger_store` patches.
    root = base_dir if base_dir is not None else config_dir()
    try:
        store = TriggerStore(base_dir=root)
    except Exception:  # noqa: BLE001 - boot must survive an unusable home
        logger.warning("trigger store unavailable; skipping cron migration", exc_info=True)
        return {"ok": False, "reason": "store unavailable", "converted": 0, "armed": []}
    if unreadable(store) is not None:
        # Every step below writes the store, which refuses while it cannot be read (the read has
        # said why, once, in the log). Nothing is imported or armed, and no legacy file is retired:
        # one renamed as imported while none of its rows landed would never be read again.
        return {"ok": False, "reason": "triggers.json cannot be read", "converted": 0, "armed": []}

    # Before the cron import, and independent of it: a home can hold a legacy `event_triggers.json`
    # with no `crons.json`, and a cron import that fails must not strand the user's event triggers.
    events_absorbed = absorb_event_triggers(store, now=now)
    # Here with the other two, not at the nudge service's start: the gateway raises the one review
    # item right after the dashboard comes up, from the rows waiting then, and the service starts
    # after that — an import there left its loops out of the item the owner is sent to.
    nudges_absorbed = _absorb_nudges(root, now=now)
    _drop_webhook_token_refs(store)
    _drop_retry(store)

    try:
        report = store.migrate_from_crons(now=now)
    except Exception:  # noqa: BLE001 - a bad legacy file must not stop the gateway
        logger.warning("cron migration failed; leaving the trigger store as-is", exc_info=True)
        return {
            "ok": False,
            "reason": "migration raised",
            "converted": 0,
            "armed": [],
            "events_absorbed": events_absorbed,
            "nudges_absorbed": nudges_absorbed,
        }

    armed = arm_unarmed(store, now=now)

    out: dict[str, Any] = {
        "ok": bool(report.get("lossless", False)) and not report.get("reason"),
        "converted": int(report.get("converted", 0) or 0),
        "written": int(report.get("written", 0) or 0),
        "refused": int(report.get("refused", 0) or 0),
        "lossless": bool(report.get("lossless", False)),
        "reason": str(report.get("reason", "") or ""),
        "armed": armed,
        "events_absorbed": events_absorbed,
        "nudges_absorbed": nudges_absorbed,
    }
    _log_report(out)
    return out


def _drop_webhook_token_refs(store: Any) -> None:
    """Take ``spec.token_ref`` out of every webhook automation. Never raises.

    A webhook automation's spec used to require it, and nothing read it: what admits a caller is a
    sender token made for the automation, which the client registry keeps as a hash
    (``inbound.webhook``). So a row keeps no credential it never used, whether it held a
    ``{{secret:…}}`` reference or the token itself.
    """
    try:
        from personalclaw.triggers.store import drop_spec_key

        dropped = drop_spec_key(store, "webhook", "token_ref")
    except Exception:  # noqa: BLE001 - a store that cannot be rewritten must not stop the gateway
        logger.warning("could not take token_ref out of the webhook automations", exc_info=True)
        return
    if dropped:
        logger.info("webhook automations: took the unread token_ref out of %s", ", ".join(dropped))


def _drop_retry(store: Any) -> None:
    """Take ``retry`` out of every automation. Never raises.

    Every row a release before this one wrote carried it, and nothing ever read it: a step's
    retries are its own. Left in, each such row would read on the Triggers page as carrying a
    field nothing knows."""
    try:
        from personalclaw.triggers.store import drop_field

        dropped = drop_field(store, "retry")
    except Exception:  # noqa: BLE001 - a store that cannot be rewritten must not stop the gateway
        logger.warning("could not take retry out of the automations", exc_info=True)
        return
    if dropped:
        logger.info("automations: took the unread retry out of %s", ", ".join(dropped))


def _absorb_nudges(root: Path | str, *, now: float = 0.0) -> int:
    """Import a legacy `autonudge.json` (`triggers.nudge.import_legacy`). Never raises."""
    try:
        from personalclaw.triggers.nudge import import_legacy

        return import_legacy(Path(root), now=now)
    except Exception:  # noqa: BLE001 - a bad legacy file must not stop the gateway
        logger.warning("auto-nudge import failed; leaving autonudge.json as-is", exc_info=True)
        return 0


#: The retired data-event store.
LEGACY_EVENT_FILE = "event_triggers.json"


def absorb_event_triggers(store: Any, *, now: float = 0.0) -> int:
    """Import a legacy `event_triggers.json` into the trigger store, once per home. Returns rows.

    🔴 WHY THIS EXISTS. That file was a second trigger store with its own engine, and the one place
    the Triggers page's Data-event form wrote to. Its engine ran an action and recorded nothing,
    and nothing else in the substrate — the tick, the run ledger, the doctor, the chat's
    `automation_*` tools — could see its rows. Event triggers live in `triggers.json` now, so a
    home that made them before keeps them: same id (namespaced `event:<id>`, the store's
    `kind:slug` form), pattern, the one matcher the pattern reads, action, budget, debounce, fire
    count and last-fired stamp, park state. `LEGACY_FIELD_MAP["EventTrigger"]` is the map.

    What it does NOT keep is anything the owner never allowed: the file records no consent, and it
    can reach a home by a snapshot restore or a copy. Each row goes through
    `legacy_import.admit` — written `created_by: import` with no capability block, and switched off
    to wait for review when it would run anything that needs a grant — and the file is then
    renamed `event_triggers.json.imported-<date>`. A home that has such a copy has imported, so a
    file found again is left in place, unread, for the Doctor to name. A row already in the store
    is left as it is, so an import interrupted midway finishes on the next boot and writes nothing
    twice. A row the entity refuses (a required matcher that was empty, so it could never have
    fired) is still written, and loads disabled, carrying its error: visible as broken on the page
    rather than silently absent (the `migrate_from_crons` rule).

    Never raises: an unreadable legacy file is left in place and logged, and boot continues.
    """
    import json

    from personalclaw.event_triggers import DEFAULT_DEBOUNCE_SECS, PATTERN_MATCHER, event_spec
    from personalclaw.triggers import legacy_import
    from personalclaw.triggers.models import Trigger, TriggerState
    from personalclaw.triggers.service import to_iso

    home = Path(store.base_dir)
    legacy = home / LEGACY_EVENT_FILE
    if not legacy.exists():
        return 0
    if legacy_import.already_imported(home, LEGACY_EVENT_FILE):
        logger.warning(
            "%s is back after this home imported it; it is not read again (the Doctor says what "
            "to do with it)",
            legacy,
        )
        return 0
    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("%s is unreadable; leaving it in place, nothing imported", legacy)
        return 0
    rows = (
        raw if isinstance(raw, list) else (raw.get("triggers", []) if isinstance(raw, dict) else [])
    )

    absorbed = 0
    waiting = 0
    try:
        existing = {row.trigger.id for row in store.load(strict=True)}
    except record_files.Unreadable:
        # Every row would be refused, and the file then retired as imported: left as it is.
        logger.warning("%s is left unread: the automations file cannot be read", legacy)
        return 0
    for item in rows:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            continue
        legacy_id = str(item["id"]).strip()
        trigger_id = f"event:{legacy_id}"
        if trigger_id in existing:
            continue
        try:
            pattern = str(item.get("pattern") or "")
            field = PATTERN_MATCHER.get(pattern)
            gates: dict[str, Any] = {}
            if int(item.get("max_fires", 0) or 0) > 0:
                gates["max_fires"] = int(item["max_fires"])
            # A row without the key gets the retired engine's own default, which is what it ran
            # with; one that stored 0 had its burst guard switched off, and keeps it off.
            debounce = float(item.get("debounce_secs", DEFAULT_DEBOUNCE_SECS) or 0.0)
            if debounce > 0:
                gates["debounce_secs"] = debounce
            state = str(item.get("state") or TriggerState.ACTIVE.value)
            trigger = Trigger(
                id=trigger_id,
                # The legacy row had no name; its id was what every surface displayed.
                name=legacy_id,
                kind="event",
                enabled=bool(item.get("enabled", True)),
                created_by=legacy_import.IMPORTED_BY,
                spec=event_spec(pattern, str(item.get(field) or "") if field else ""),
                gates=gates,
                workflow={
                    "inline": {
                        "provider": str(item.get("action_provider") or "notify"),
                        "config": dict(item.get("action_config") or {}),
                    }
                },
                run_count=int(item.get("fire_count", 0) or 0),
                last_fired_at=to_iso(float(item.get("last_fired_at", 0.0) or 0.0)),
                state=(
                    state if state in {s.value for s in TriggerState} else TriggerState.ACTIVE.value
                ),
                last_error_summary=str(item.get("park_reason") or ""),
                park_retry_after=float(item.get("park_retry_after", 0.0) or 0.0),
            )
            if legacy_import.admit(trigger):
                waiting += 1
            store.upsert(trigger)
            # Seen from here on: a file listing one id twice imports it once, the first time.
            existing.add(trigger_id)
            absorbed += 1
        except Exception:  # noqa: BLE001 - one malformed row must not strand the rest
            logger.warning("skipping a malformed legacy event trigger: %r", item, exc_info=True)
    retired = legacy_import.retire(legacy, now=now)
    legacy_import.audit_import(
        LEGACY_EVENT_FILE, imported=absorbed, waiting=waiting, retired=retired
    )
    logger.info(
        "imported %d legacy event trigger(s) from %s into triggers.json; %d wait for review",
        absorbed,
        LEGACY_EVENT_FILE,
        waiting,
    )
    return absorbed


def arm_unarmed(store: Any, *, now: float = 0.0) -> list[str]:
    """Arm every enabled clock trigger that has no `next_fire_at`. Returns the ids armed.

    Uses `arm.needs_arming` so exactly the inert population is touched: a row that already
    carries a next fire is left alone, because re-arming a live schedule mid-flight is how a fire
    gets skipped or doubled.
    """
    from personalclaw.triggers.arm import arm, needs_arming

    armed: list[str] = []
    for row in store.load():
        trigger = row.trigger
        if not getattr(row, "ok", True) or not needs_arming(trigger):
            continue
        when = arm(trigger, now=now)
        if not when:
            # Unarmable (invalid cron, elapsed one-shot). Skipped rather than armed to `now` —
            # firing on a guessed cadence is worse than not firing.
            continue
        trigger.next_fire_at = when
        store.upsert(trigger)
        armed.append(trigger.id)
    return armed


def verify_report(base_dir: Path | str | None = None) -> dict[str, Any]:
    """The `verify-migration` diff, as data, so boot can log whether the import is trustworthy.

    Run here because "was my migration faithful" is a question with a shelf life: answered
    in the boot
    log it is actionable, and answered only by a command the user must think to run it is usually
    never asked. `lossless: true` is deliberately NOT the bar — two of the owner's real
    automations were measured to migrate lossless AND disabled, so `ok` here means "needs no
    attention".
    """
    try:
        from personalclaw.triggers.verify import verify_home

        report = verify_home(base_dir)
        return report.to_dict()
    except Exception:  # noqa: BLE001 - a verifier that raised must not stop boot
        logger.debug("verify-migration could not run at boot", exc_info=True)
        return {}


def _log_report(report: dict[str, Any]) -> None:
    """One log line a user can act on, or silence when there was nothing to do.

    A migration that imported nothing (no `crons.json`, or every row already present) logs at DEBUG:
    an INFO line on every boot saying "converted 0" trains people to ignore the line that matters.
    """
    if report.get("reason") == "no crons.json":
        logger.debug("no crons.json to migrate")
        return
    converted = report.get("converted", 0)
    written = report.get("written", 0)
    armed = report.get("armed") or []
    if not converted and not armed:
        logger.debug("cron migration: nothing to do")
        return
    logger.info(
        "cron migration: %d converted, %d written, %d armed%s",
        converted,
        written,
        len(armed),
        (
            ""
            if report.get("lossless", True)
            else " (NOT lossless — run `personalclaw automation " "verify-migration`)"
        ),
    )
