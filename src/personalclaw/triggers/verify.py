"""`automation verify-migration` — the row-for-row diff (§7 step 2 — S91).

§7 step 2: "**Trigger store unification**: `triggers.json` + row-for-row cron migration (old
file read-only one release; `personalclaw automation verify-migration` diff command)." §8 names
the risk it mitigates: "Migration trust (crons are the most-loved automations) → Row-for-row
migration + read-only legacy file + verify-migration diff command".

S87 shipped the store and the migration; my own docstring there promised this command by name
and it did not exist. It is the plan's named prerequisite for the cutover, so it lands before
the cutover, not after.

**🔴 WHAT DRIVING IT AGAINST THE OWNER'S REAL STORE FOUND.** Four jobs migrate `lossless:
true` — and two of them come out **disabled**. `j-every` (a 5-minute interval) and `j-seq` (an
`agent_sequence`) were `enabled=True` in `crons.json` and land `enabled=False`.

That is NOT a bug. `migrate.convert_job` does it deliberately, and its comment is right: "A row
with notes loads DISABLED even if it was enabled, so nothing fires on a schedule the migration
could not fully interpret. The user re-enables after reading the note — the opposite default
would run a half-understood automation unattended."

But `lossless: true` alongside two silently-paused automations is a report that is technically
accurate and practically misleading. A user reading "lossless" concludes nothing needs doing;
their 5-minute automation has stopped. **That gap is precisely what this command exists to
close** — and it is why the plan put a diff command in the same breath as the migration rather
than trusting the migration's own summary.

So this module reports THREE things a `lossless` flag cannot say:

* **`paused` — rows that were live and are not.** Named individually with the note that caused
  it, because "2 need review" sends a user hunting while `j-every: legacy every has no trigger
  clock kind…` tells them what to do.
* **`missing` — rows in `crons.json` with no counterpart at all.** The one true data loss, and
  the thing a row-for-row diff exists to make impossible to miss.
* **`field_drift` — per-row field comparison.** `skip_dates` dropped fires on a holiday and
  nobody knows why; that is the quietly-losable class §1.3 keeps warning about.

Pure functions over both stores. Nothing here writes: a verify that mutated would be a
migration, and the whole point is to be safe to run before deciding.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Legacy fields whose loss changes WHEN or WHETHER a job fires. Compared per row and reported
#: individually, because these are the ones §1.3 calls quietly-losable: "a dropped `skip_dates`
#: fires on a holiday and nobody knows why".
TIMING_FIELDS: tuple[str, ...] = (
    "skip_dates",
    "timezone",
    "strict_schedule",
    "delete_after_run",
)


@dataclass
class RowDiff:
    """One legacy job compared against its migrated trigger."""

    job_id: str
    #: True when a trigger with this id exists in the new store at all.
    present: bool = False
    #: Was live in `crons.json` and is not live now — the silent-pause class.
    paused: bool = False
    #: Why it paused, verbatim from the migration's own note.
    note: str = ""
    #: Legacy fields whose value did not survive into the trigger's spec/gates.
    field_drift: list[str] = field(default_factory=list)
    #: Parse errors on the migrated row, if any.
    errors: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Whether this row needs no attention at all."""
        return self.present and not self.paused and not self.field_drift and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "present": self.present,
            "paused": self.paused,
            "note": self.note,
            "field_drift": list(self.field_drift),
            "errors": list(self.errors),
            "clean": self.clean,
        }


@dataclass
class VerifyReport:
    """The whole diff. `ok` is the ONE flag a caller should branch on."""

    rows: list[RowDiff] = field(default_factory=list)
    #: Set when either store could not be read. Distinct from "no differences": an unreadable
    #: legacy file is not a clean migration, and reporting it as one is how a user skips a check
    #: that never ran.
    unreadable: str = ""

    @property
    def missing(self) -> list[str]:
        return [r.job_id for r in self.rows if not r.present]

    @property
    def paused(self) -> list[str]:
        return [r.job_id for r in self.rows if r.paused]

    @property
    def drifted(self) -> list[str]:
        return [r.job_id for r in self.rows if r.field_drift]

    @property
    def broken(self) -> list[str]:
        return [r.job_id for r in self.rows if r.errors]

    @property
    def ok(self) -> bool:
        """Whether the migration needs no human attention.

        A PAUSED row counts as not-ok. That is the deliberate difference from `migrate_crons`'
        `lossless`, which is true for a paused row because nothing was *lost* — the data is all
        there, the automation just is not running. A user asking "did my migration work" means
        "are my automations still running", and answering the narrower question would be a
        technically-true report that gets someone's 5-minute job silently stopped.
        """
        return not self.unreadable and all(r.clean for r in self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "total": len(self.rows),
            "missing": self.missing,
            "paused": self.paused,
            "drifted": self.drifted,
            "broken": self.broken,
            "unreadable": self.unreadable,
            "rows": [r.to_dict() for r in self.rows],
        }


def _read_json(path: Path) -> tuple[Any, str]:
    """`(payload, error)` — never raises. An unreadable file is reported, not treated as empty."""
    if not path.exists():
        return None, f"{path.name} does not exist"
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except (OSError, ValueError) as exc:
        return None, f"{path.name} is unreadable: {exc}"


def _legacy_jobs(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("jobs") if isinstance(payload, dict) else payload
    return [r for r in (rows or []) if isinstance(r, dict)]


def _spec_and_gates(trigger: Any) -> dict[str, Any]:
    """Everything a migrated trigger carries that a legacy timing field could have landed in.

    Flattened across `spec` and `gates` because the migration puts `skip_dates` in one and
    `timezone` in the other depending on kind, and a drift check that only looked in `spec`
    would report a false loss.
    """
    merged: dict[str, Any] = {}
    for source in (getattr(trigger, "spec", None), getattr(trigger, "gates", None)):
        if isinstance(source, dict):
            merged.update(source)
    return merged


def _field_drift(job: dict[str, Any], trigger: Any) -> list[str]:
    """Legacy timing fields whose value did not survive.

    Compares PRESENCE, not equality: the migration legitimately renames (`strict_schedule` →
    `strict`) and re-types (an epoch `at_ts` → an `at`), so demanding equal values would report
    drift on every correctly-converted row. What matters is that a field the user set is still
    represented somewhere.
    """
    carried = _spec_and_gates(trigger)
    drift: list[str] = []
    for name in TIMING_FIELDS:
        value = job.get(name)
        if value in (None, "", [], False):
            continue  # not set legacy-side, so nothing to lose
        alias = "strict" if name == "strict_schedule" else name
        if name not in carried and alias not in carried:
            drift.append(name)
    return drift


def verify(
    *, crons_path: Path | str, store: Any, notes_by_id: dict[str, str] | None = None
) -> VerifyReport:
    """Diff `crons.json` against a `TriggerStore`, row for row.

    `notes_by_id` supplies the migration's own note per job, so a paused row can explain ITSELF
    rather than making the user re-run the migration to find out why. `report_notes()` builds
    it.

    Order follows the legacy file, so a user can read the report next to their own `crons.json`
    top to bottom — the same reason `migrate_crons` preserves order.
    """
    payload, error = _read_json(Path(crons_path))
    if error:
        return VerifyReport(unreadable=error)

    notes = notes_by_id or {}
    loaded = {row.trigger.id: row for row in store.load()}
    rows: list[RowDiff] = []

    for job in _legacy_jobs(payload):
        job_id = str(job.get("id") or "")
        if not job_id:
            # A row with no id cannot be matched; `migrate_crons` refuses it for the same reason
            # ("a generated id would be un-recognizable against the user's file").
            rows.append(RowDiff(job_id="<no id>", present=False, note="legacy row has no id"))
            continue
        found = loaded.get(job_id)
        if found is None:
            rows.append(RowDiff(job_id=job_id, present=False))
            continue
        was_enabled = bool(job.get("enabled", False))
        rows.append(
            RowDiff(
                job_id=job_id,
                present=True,
                paused=was_enabled and not found.trigger.enabled,
                note=notes.get(job_id, ""),
                field_drift=_field_drift(job, found.trigger),
                errors=[i.message for i in getattr(found, "errors", [])],
            )
        )

    return VerifyReport(rows=rows)


def report_notes(crons_path: Path | str) -> dict[str, str]:
    """`{job_id: note}` from a dry-run migration, so a paused row can say why.

    Runs `migrate_crons` on the legacy payload WITHOUT writing anything — it is a pure function
    over dicts, which is exactly what makes a verify command safe to run before deciding.
    """
    payload, error = _read_json(Path(crons_path))
    if error or not isinstance(payload, dict):
        return {}
    from personalclaw.triggers.migrate import migrate_crons

    notes: dict[str, str] = {}
    report = migrate_crons(payload)
    for converted in getattr(report, "converted", None) or []:
        trigger = getattr(converted, "trigger", None) or {}
        job_id = str(trigger.get("id") or "") if isinstance(trigger, dict) else ""
        row_notes = list(getattr(converted, "notes", None) or [])
        if job_id and row_notes:
            notes[job_id] = row_notes[0]
    return notes


def verify_home(base_dir: Path | str | None = None) -> VerifyReport:
    """Verify the migration in one home directory. The CLI's entry point.

    Builds the store itself rather than taking one, so the command is a single call with no
    setup — a verify that required the caller to build two stores correctly would be one more
    place to get the paths wrong.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.triggers.store import TriggerStore

    root = Path(base_dir) if base_dir else config_dir()
    crons = root / "crons.json"
    return verify(
        crons_path=crons,
        store=TriggerStore(base_dir=root),
        notes_by_id=report_notes(crons),
    )


def render(report: VerifyReport) -> str:
    """The human-readable diff. What `personalclaw automation verify-migration` prints.

    Leads with the ACTION, not the summary. A user runs this to answer "is anything broken, and
    what do I do" — so a paused automation and its note come before the counts, which they can
    already see in the migration's own output.
    """
    if report.unreadable:
        return f"✗ cannot verify: {report.unreadable}"

    lines: list[str] = []
    if report.missing:
        lines.append(
            f"✗ MISSING from triggers.json ({len(report.missing)}): {', '.join(report.missing)}"
        )
        lines.append(
            "   These jobs did not migrate. crons.json is still intact — re-run the migration."
        )
    if report.paused:
        lines.append(
            f"⚠ PAUSED by the migration ({len(report.paused)}) — these were running and are not:"
        )
        for row in report.rows:
            if row.paused:
                why = row.note or "the migration could not fully interpret this row"
                lines.append(f"   · {row.job_id}: {why}")
        lines.append("   Read the note, then re-enable each one you still want.")
    if report.drifted:
        lines.append(f"⚠ FIELDS not carried ({len(report.drifted)}):")
        for row in report.rows:
            if row.field_drift:
                lines.append(f"   · {row.job_id}: {', '.join(row.field_drift)}")
    if report.broken:
        lines.append(
            f"✗ UNPARSEABLE after migration ({len(report.broken)}): {', '.join(report.broken)}"
        )

    if not lines:
        return f"✓ {len(report.rows)} job(s) migrated cleanly — all still enabled, no fields lost."

    lines.append("")
    lines.append(
        f"{len(report.rows)} legacy job(s): "
        f"{len([r for r in report.rows if r.clean])} clean, "
        f"{len(report.paused)} paused, {len(report.missing)} missing, "
        f"{len(report.drifted)} drifted."
    )
    lines.append("crons.json is READ-ONLY for one release and was not modified by this check.")
    return "\n".join(lines)
