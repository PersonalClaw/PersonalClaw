"""Apply a reviewed conflict's chosen version to the live store (§4.2 item 2).

:mod:`durability.conflicts` detects a both-sides-edited divergence and HOLDS the local row;
:mod:`durability.conflict_merge` may draft a proposed merge. Neither writes anything — the
soul rule is propose, don't write. This module is the ONE place a human decision becomes a
write, which is why the choice is explicit (three named versions, no "auto") and why the
caller must confirm it.

**Failure semantics: recoverable, not transactional** — the same choice the export/import
half made and stated. There is no cross-store transaction to be had here, so the ordering is
picked so that every failure leaves a state a repeat of the same request fixes:

1. The store write goes first, as a WHOLE-ENTRY rewrite through
   :func:`durability.writeback.apply_rows` — the same path the sync cycle itself uses. Every
   local row is read, the one reviewed row is substituted by entity id, and the full set is
   written back atomically (temp file + rename). A single-row ``apply_rows`` would truncate a
   ``jsonl_append`` stream to one event, so the substitution is not an optimisation to skip. A
   store of records (``StateEntry.records``) has the one record substituted in its document,
   under the file's lock (``reconcile.take_in``).
2. The queue record flips to ``resolved`` only AFTER that write returns.

So a failed write leaves the record ``needs-review`` and the store untouched (the caller sees
a typed error, never a success); a write that lands but whose queue update fails leaves the
record ``needs-review`` too, and re-resolving writes the identical bytes — idempotent, so the
recoverable direction is "ask again", never "half-applied and reported done".

**What the next sync cycle does with a resolution.** Nothing is pushed from here; the cycle
converges on its own schedule, and it does so differently per choice:

* ``keep_local`` — the three shas are unchanged, so :func:`conflicts.detect_conflicts` (a pure
  function that never consults the queue) detects the same divergence next cycle and HOLDS the
  id again, so the local row keeps winning. ``queue.record`` dedups on the record id, so the
  resolved record is not resurrected as a new needs-review row. The decision sticks.
* ``take_remote`` — local becomes the remote sha, as two homes compare a row
  (``conflicts.compared``), so the two sides are converged and the divergence stops being
  detected at all.
* ``accept_proposal`` — local becomes a THIRD sha the peer has never seen, so once the peer's
  export is pulled the divergence is genuinely new (different local sha → different record id)
  and a fresh review item appears until the peer has the merged row. That is honest rather
  than convenient: a proposal the peer hasn't seen is not agreement, and pretending otherwise
  is how a merge silently loses the other machine's edit.

**The other machine's version comes in the way a sync brings it in** (``reconcile.take_in``).
Taking it, or the merge drafted from it, is the other machine's edit to a row this home has, so it
is taken in as the sync takes an edit only the other machine made (``StateEntry.edit_arrives``):
what a person makes of the row is the other machine's, and this home's own part stays. An
automation or a hook stays switched on or off as it is here, with what happened to it here, and
its grant keeps only what the edited version still runs as it ran here, so what runs differently
asks the owner first; a workflow keeps what its steps were allowed here only on the steps the edit
left as they were. It used to come in as a row this home did not have — an automation switched
off with no grant, one the owner had running here stopped by the review. A row this home no longer
has comes in by the store's arrival rule (``StateEntry.arrives``), as a pull brings a new one.
Keeping this machine's version writes nothing: the row the record holds is a copy from when the
divergence was found, and writing it back would undo every change this machine has made to the
row since.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability import inventory as inv
from personalclaw.durability import reconcile, writeback

logger = logging.getLogger(__name__)

#: Keep this machine's version. Writes nothing new — but is still a decision, because it is
#: what stops the review item from being an open question.
CHOICE_KEEP_LOCAL = "keep_local"
#: Take the peer's version, overwriting the local row.
CHOICE_TAKE_REMOTE = "take_remote"
#: Take the drafted merge. Refused when there is no proposal (a draft can fail).
CHOICE_ACCEPT_PROPOSAL = "accept_proposal"

CHOICES = (CHOICE_KEEP_LOCAL, CHOICE_TAKE_REMOTE, CHOICE_ACCEPT_PROPOSAL)


@dataclass
class ResolveOutcome:
    """The result of one resolve attempt. ``code`` is empty on success and a stable,
    machine-readable refusal reason otherwise — the review surface renders the reason, so a
    refusal must never arrive as a bare False."""

    ok: bool
    code: str = ""
    message: str = ""
    choice: str = ""
    record_id: str = ""
    written: int = 0
    removed: int = 0
    #: The record as it now stands (resolved on success, untouched on a refusal).
    record: dict | None = None
    #: What the version written is like here, for a store that takes another machine's in by a
    #: rule (``StateEntry.edit_arrival``, or ``arrival`` for a row this home no longer had): the
    #: sentence the review shows once it is written.
    note: str = ""


def _refuse(code: str, message: str, *, choice: str = "", record_id: str = "") -> ResolveOutcome:
    return ResolveOutcome(ok=False, code=code, message=message, choice=choice, record_id=record_id)


def chosen_row(rec: conflicts_mod.ConflictRecord, choice: str) -> dict | None:
    """The row ``choice`` names on ``rec``, or ``None`` when that version does not exist.

    Split out so the review surface and the writer agree on what a choice MEANS: a choice
    whose version is absent (an undrafted proposal) has to refuse, not fall back to another
    version — silently applying the local row when the user asked for the proposal is exactly
    the "partial apply that looks like a success" this atom must not ship.
    """
    if choice == CHOICE_KEEP_LOCAL:
        return rec.local_row or None
    if choice == CHOICE_TAKE_REMOTE:
        return rec.remote_row or None
    if choice == CHOICE_ACCEPT_PROPOSAL:
        return rec.proposal or None
    return None


def resolve_conflict(home: Path, record_id: str, choice: str, *, now: str = "") -> ResolveOutcome:
    """Apply ``choice``'s version of conflict ``record_id`` to the live store under ``home``.

    Refusal codes, all typed so the surface can say WHY rather than "failed":

    ==================  ==========================================================
    ``unknown_choice``  not one of :data:`CHOICES`
    ``not_found``       no such record in this home's queue
    ``already_resolved``the record was reviewed already (never re-applied silently)
    ``unknown_entry``   the record names an inventory entry that no longer exists
    ``unsupported_kind``the entry is not a row-merge kind (sqlite/tree have their own path)
    ``no_version``      the chosen version is absent — typically an undrafted proposal
    ``not_a_record``    the version is not one record of a store of records (a conflict
                        recorded over the whole file); only keeping this machine's resolves it
    ``machine_local``   the store, or this file of it, is one machine's own and never takes
                        another machine's (``StateEntry.machine_local``); a conflict recorded
                        before it was declared so closes by keeping this machine's
    ``write_failed``    the store write raised; the record stays needs-review
    ==================  ==========================================================
    """
    if choice not in CHOICES:
        return _refuse(
            "unknown_choice",
            f"choice must be one of {', '.join(CHOICES)}",
            choice=choice,
            record_id=record_id,
        )
    queue = conflicts_mod.ConflictQueue(home)
    rec = queue.get(record_id)
    if rec is None:
        return _refuse("not_found", "no conflict with that id is queued", choice=choice)
    if rec.status != conflicts_mod.STATUS_NEEDS_REVIEW:
        return _refuse(
            "already_resolved",
            f"this conflict was already resolved ({rec.resolution or rec.status})",
            choice=choice,
            record_id=record_id,
        )
    entry = inv.by_id(rec.entry_id)
    if entry is None:
        return _refuse(
            "unknown_entry",
            f"the store this conflict belongs to ({rec.entry_id}) is no longer declared",
            choice=choice,
            record_id=record_id,
        )
    if not reconcile.handles_kind(entry.kind):
        return _refuse(
            "unsupported_kind",
            f"{rec.entry_id} is a {entry.kind} store — row-level resolution does not apply",
            choice=choice,
            record_id=record_id,
        )
    if choice != CHOICE_KEEP_LOCAL and (
        entry.machine_local or inv.stays_here(entry, rec.entity_id)
    ):
        return _refuse(
            "machine_local",
            (
                f"{entry.path} is this machine's own and never takes another machine's version; "
                "keep this machine's to close the conflict"
            ),
            choice=choice,
            record_id=record_id,
        )
    row = chosen_row(rec, choice)
    if row is None:
        return _refuse(
            "no_version",
            (
                "there is no drafted merge to accept"
                if choice == CHOICE_ACCEPT_PROPOSAL
                else f"the {choice} version is not recorded on this conflict"
            ),
            choice=choice,
            record_id=record_id,
        )

    applied = writeback.ApplyResult()
    edited = False
    if choice != CHOICE_KEEP_LOCAL:
        if entry.records is not None and not all(
            reconcile.is_record(entry, r) for r in (rec.remote_row, row)
        ):
            return _refuse(
                "not_a_record",
                (
                    f"this conflict was recorded over the whole of {entry.path}, which is now "
                    "compared one entry at a time; keep this machine's version to close it, and "
                    "the next sync brings in, one at a time, what only the other machine has"
                ),
                choice=choice,
                record_id=record_id,
            )
        dest = Path(home) / entry.path
        try:
            applied, edited = reconcile.take_in(entry, dest, rec.entity_id, row)
        except Exception as exc:  # noqa: BLE001 — a failed write must leave the review open
            logger.warning("conflict resolve: write failed for %s", record_id, exc_info=True)
            return _refuse(
                "write_failed",
                f"nothing was applied: {exc}",
                choice=choice,
                record_id=record_id,
            )

    rec.status = conflicts_mod.STATUS_RESOLVED
    rec.resolution = choice
    rec.resolved_at = now
    if not queue.update(rec):
        # The store already holds the chosen row and re-resolving writes the same bytes, so
        # this is reported as a refusal the caller can simply repeat — never as a success
        # whose record still says needs-review.
        return _refuse(
            "write_failed",
            "the chosen version was written but the review record could not be updated; "
            "resolve it again",
            choice=choice,
            record_id=record_id,
        )
    logger.info("conflict resolve: %s/%s resolved as %s", rec.entry_id, rec.entity_id, choice)
    return ResolveOutcome(
        ok=True,
        choice=choice,
        record_id=record_id,
        written=applied.written,
        removed=applied.removed,
        record=rec.to_dict(),
        note=(
            "" if choice == CHOICE_KEEP_LOCAL else (entry.edit_arrival if edited else entry.arrival)
        ),
    )


def taking_it_here(home: Path, rec: conflicts_mod.ConflictRecord) -> str:
    """What taking the other machine's version of *rec*, or the merge drafted from it, makes of it
    here, in the words the review shows before anything is written: ``StateEntry.edit_arrival``
    when this home has the row (the review takes the version in as an edit), ``arrival`` when it
    no longer does; ``""`` when neither can be taken, or the store takes it as written."""
    entry = inv.by_id(rec.entry_id)
    if entry is None or entry.machine_local or inv.stays_here(entry, rec.entity_id):
        return ""
    if not reconcile.handles_kind(entry.kind):
        return ""
    try:
        has = reconcile.holds(entry, Path(home) / entry.path, rec.entity_id)
    except (OSError, ValueError):
        return ""
    return entry.edit_arrival if has else entry.arrival
