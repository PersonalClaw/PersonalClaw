"""A loop a spend ceiling refused: how it waits for its owner, said once for its workers and its
planner.

A refused call is not retried by itself (``AutoNudgeService.notify_turn_complete`` switches the
refused session's nudge loop off), so the loop waits, and its owner hears it ONCE, wherever she
looks: one Inbox item and its one notification (``inbox.emit_attention_item``, deduped while it is
open), the loop's own page, and the refusal's own sentence (which ceiling, what was spent against
it, what the call needed, and where it is lifted) everywhere it is shown. A running loop waits as
``needs_input`` (``LoopWatchdog.hold_for_spend_cap``); a loop still planning keeps its status and
its walkthrough says it is paused (``plan_walkthrough``), with Resume.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: How the notification and the Inbox item for a loop a spend ceiling refused are titled.
TITLE = "Loop paused — a spend cap refused its next call"

#: What the pause means for the loop, beside the refusal's own sentence.
WHY = "The loop does not try the refused call again by itself. Resume it once the cap has room."

#: Why a planning walkthrough is paused (``PlanSession.paused["by"]``).
PAUSED_BY = "spend_cap"

#: The ref a planning pause's Inbox item carries beside the loop's, so resolving it closes no other
#: row of the loop.
_PLANNING_REF = "spend_cap"


def planning_pause(refusal: Any) -> dict[str, str]:
    """What a walkthrough paused by *refusal* records (``PlanSession.paused``): why, and the
    Settings page the ceiling is changed on, which the walkthrough links."""
    return {"by": PAUSED_BY, "settings": str(getattr(refusal, "settings_page", "") or "")}


def raise_planning_item(state: Any, loop: Any, refusal: Any) -> str:
    """The one Inbox item, and its one notification, for a loop whose planner a spend ceiling
    refused. Deduped while it is open, so a second refusal of the same pause says nothing new.
    Returns the item id, or ``""``. Never raises."""
    if state is None:
        return ""
    try:
        from personalclaw.inbox import ItemKind, emit_attention_item
        from personalclaw.security import redact_for_display

        name = redact_for_display(getattr(loop, "name", "") or loop.id)
        return emit_attention_item(
            state,
            source="loop",
            kind="needs_input",
            item_kind=ItemKind.NEEDS_INPUT.value,
            title=TITLE,
            body=f"{name}\n{refusal.sentence()}",
            refs={
                "loop": loop.id,
                "loop_kind": getattr(loop, "kind", ""),
                _PLANNING_REF: "planning",
            },
            dedup_key=f"loop:{loop.id}:spend_cap:planning",
        )
    except Exception:
        logger.debug("loop %s: could not raise the spend-cap item", loop.id, exc_info=True)
        return ""


def resolve_planning_item(state: Any, loop_id: str) -> int:
    """Close the planning pause's Inbox item of *loop_id*: its planner runs again, or it has
    stopped planning. ``state`` may be ``None`` (no gateway: the Inbox on disk). Returns how many
    were closed. Never raises."""
    if not loop_id:
        return 0
    try:
        from personalclaw.inbox import resolve_attention_items

        return resolve_attention_items(state, {"loop": loop_id, _PLANNING_REF: "planning"})
    except Exception:
        logger.debug("loop %s: could not close the spend-cap item", loop_id, exc_info=True)
        return 0
