"""Convergence for a SYSTEM-OWNED singleton trigger, keyed on the job rather than the id.

Five reconcilers register one row apiece with a deterministic id (`system:notification-digest`,
`system:source-digest`, `system:usage-recap`, and the remediation / identity-report rows). The
deterministic id is a deliberate choice and `digest_provider` records why: a generated slug would
make every restart add another copy instead of recognising its own.

The gap is the other direction, and it is an UPGRADE-path gap invisible on a fresh install. The
legacy `crons.json` already holds these jobs under RANDOM ids, and the boot import upserts by id —
correctly, so a hand-authored row survives a re-import. So the migrated copy and the reconciled
copy coexist: two identical rows, both enabled, same cron, same next-fire instant, indistinguishable
in the UI, and the job runs twice a day (issue 396). Neither side is wrong alone; nothing reconciled
across them.

`converge_system_singleton` is that missing side. It identifies the job by what it IS — the inline
action provider it runs — rather than by the id it happens to carry, and retires every system-owned
row that is this job under a different id.

Two properties are load-bearing:

* **`created_by == "system"` is the discriminator.** A user may legitimately schedule the same
  action for themselves; that row is theirs and must survive. `created_by` is a closed vocabulary
  (`user`/`agent`/`system`) recording WHAT wrote the row, which makes it exactly the right question.
* **A retired row's `enabled` flag is reported back, not discarded.** If the only copy was the
  migrated one and the user had switched the digest OFF, creating a fresh canonical row at its
  default would silently switch it back on. The caller seeds from the returned flag instead.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

logger = logging.getLogger(__name__)


def inline_provider(trigger: "Trigger") -> str:
    """The action provider a trigger runs inline, or "" for anything else.

    Read defensively: `workflow` is persisted JSON, so a hand-edited or legacy row can hold any
    shape. An unreadable workflow simply is not this job.
    """
    workflow = getattr(trigger, "workflow", None)
    if not isinstance(workflow, dict):
        return ""
    inline = workflow.get("inline")
    if not isinstance(inline, dict):
        return ""
    provider = inline.get("provider")
    return provider if isinstance(provider, str) else ""


def converge_display_name(store: "TriggerStore", trigger: "Trigger", *, name: str) -> None:
    """Rename a system row that still carries its machine id as its name. Idempotent.

    Three reconcilers used to create their row with `name=<id>`, so a fresh home's Triggers page
    opened on `system:notification-digest`, `system:usage-recap` and `system:source-digest` — raw
    ids where every other row has words. New rows are created with the human name; this brings an
    EXISTING row along, keyed on inspecting it: only a name still equal to the id is ours to
    change, so a row the user renamed keeps their name. Best-effort, like every step of a boot
    reconcile — a failed write leaves the old name for the next boot.
    """
    if trigger.name != trigger.id or trigger.name == name:
        return
    try:
        trigger.name = name
        store.upsert(trigger)
    except Exception:
        logger.debug("could not rename system trigger %s", trigger.id, exc_info=True)


def converge_system_singleton(
    store: "TriggerStore", *, canonical_id: str, provider: str
) -> bool | None:
    """Retire every system-owned row that is this job under a foreign id.

    Returns the `enabled` flag the caller should seed a NEWLY-created canonical row with, or None
    when there is nothing to adopt (no duplicate found, or the canonical row already exists and
    carries its own state). A caller that derives `enabled` from config can ignore the result.

    Conservative on the flag: with several duplicates, the row stays disabled if ANY of them was —
    an off switch the user flipped outweighs a default.

    Best-effort by construction. This runs inside a boot reconcile whose whole contract is never to
    block the scheduler, so a store that cannot be read or a delete that fails leaves the duplicate
    in place for the next boot rather than raising.
    """
    try:
        rows = store.list_triggers(include_broken=True)
    except Exception:
        logger.debug(
            "system singleton %s: could not read the trigger store", canonical_id, exc_info=True
        )
        return None

    canonical_present = any(t.id == canonical_id for t in rows)
    duplicates = [
        t
        for t in rows
        if t.id != canonical_id
        # The row must be OURS. A user's own schedule for the same action is their row, not a
        # duplicate of ours, and deleting it would be the worse bug.
        and getattr(t, "created_by", "") == "system" and inline_provider(t) == provider
    ]
    if not duplicates:
        return None

    adopted = all(bool(getattr(t, "enabled", False)) for t in duplicates)
    for dup in duplicates:
        try:
            store.delete(dup.id)
            logger.info(
                "retired a duplicate %s trigger (%s) — the canonical row is %s",
                provider,
                dup.id,
                canonical_id,
            )
        except Exception:
            logger.warning("could not retire duplicate trigger %s", dup.id, exc_info=True)

    return None if canonical_present else adopted
