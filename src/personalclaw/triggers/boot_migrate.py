"""Run the cron migration at boot, then arm the clock (§7 step 2 — S98).

**🔴 THE GAP, measured before writing.** `store.migrate_from_crons()` exists, is idempotent, and is
called by **nothing outside tests**. So on a real machine `triggers.json` is EMPTY: every cron still
lives only in `crons.json`, and the unified store the whole substrate reads is blank. Two
consequences that block the rest of the cutover:

1. **Re-pointing `/api/triggers`' schedule backend at the store would show the user ZERO
schedules** —
   every cron would vanish from the Automations page while still firing from the legacy service. The
   API re-point (§6) is unbuildable until the store actually holds the crons.
2. **The tick has nothing to fire.** S96 armed the clock and S97 made `overlap` enforce, but
both act
   on rows that were never imported.

So the migration runs at boot, and then the newly-imported rows are ARMED (S96) — an imported cron
with an empty `next_fire_at` is inert, which is the exact defect S96 measured.

**Boot-safe by construction.** The migration upserts by id (idempotent, preserves rows authored
directly in `triggers.json`), leaves `crons.json` untouched on disk (§6's "old file read-only one
release", which `verify-migration` needs to diff), and never raises into boot: a broken or missing
legacy file reports a reason and leaves the store as it was. A gateway that failed to start because
a cron file had a typo would be a far worse outcome than one that starts and reports the problem.

**It does NOT retire the legacy service.** Both still read their own store this release, which is
deliberate: the migration is additive, so a bad import can be corrected by fixing `crons.json` and
restarting rather than by restoring a deleted file. `verify-migration` (S91) is the check that says
whether the import is trustworthy, and it is run here so the answer is in the log at the moment it
matters.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)


def migrate_and_arm(base_dir: Path | str | None = None, *, now: float = 0.0) -> dict[str, Any]:
    """Import `crons.json` into the trigger store, then arm every unarmed clock trigger.

    Returns a report a caller can log or surface. Never raises: every failure path reports a reason
    and leaves the store unchanged, because this runs during gateway boot.

    The order matters. Importing without arming leaves the rows inert (S96's finding: an imported
    cron has no `next_fire_at`, and `due_ids` only surfaces rows that have one), and arming before
    importing has nothing to arm.
    """
    from personalclaw.triggers.store import TriggerStore

    # Resolved through THIS module's `config_dir` so there is exactly one place to redirect the
    # boot migration's home — which is what `tests/conftest.py::_isolate_trigger_store` patches.
    root = base_dir if base_dir is not None else config_dir()
    try:
        store = TriggerStore(base_dir=root)
    except Exception:  # noqa: BLE001 - boot must survive an unusable home
        logger.warning("trigger store unavailable; skipping cron migration", exc_info=True)
        return {"ok": False, "reason": "store unavailable", "converted": 0, "armed": []}

    try:
        report = store.migrate_from_crons()
    except Exception:  # noqa: BLE001 - a bad legacy file must not stop the gateway
        logger.warning("cron migration failed; leaving the trigger store as-is", exc_info=True)
        return {"ok": False, "reason": "migration raised", "converted": 0, "armed": []}

    armed = arm_unarmed(store, now=now)

    out: dict[str, Any] = {
        "ok": bool(report.get("lossless", False)) and not report.get("reason"),
        "converted": int(report.get("converted", 0) or 0),
        "written": int(report.get("written", 0) or 0),
        "refused": int(report.get("refused", 0) or 0),
        "lossless": bool(report.get("lossless", False)),
        "reason": str(report.get("reason", "") or ""),
        "armed": armed,
    }
    _log_report(out)
    return out


def arm_unarmed(store: Any, *, now: float = 0.0) -> list[str]:
    """Arm every enabled clock trigger that has no `next_fire_at`. Returns the ids armed.

    Uses S96's `arm.needs_arming` so exactly the inert population is touched: a row that already
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
    """S91's `verify-migration` diff, as data, so boot can log whether the import is trustworthy.

    Run here because "was my migration faithful" is a question with a shelf life: answered
    in the boot
    log it is actionable, and answered only by a command the user must think to run it is usually
    never asked. `lossless: true` is deliberately NOT the bar — S91 measured that two of the owner's
    real automations migrate lossless AND disabled, so `ok` here means "needs no attention".
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
