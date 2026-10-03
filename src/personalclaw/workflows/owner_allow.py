"""The owner's own Allow for what an agent cannot give itself, asked once.

Two things an agent may ask for put on a workflow step a posture key only the owner's consent may
put there (`automation_posture.POSTURE_SPECS`: approving its own tool calls, the write grant): a
subagent batch whose tasks may change things (`batch_start`), and a saved workflow whose steps
would do more than before (`definition_ask`). The agent cannot give that consent, so each asks the
owner once, through the approval registry itself (`DashboardState.request_approval`: the card in
the chat that asked, the Inbox, the phone, a channel), and nothing is written before her answer:

* **Only her answer.** The ask goes to the registry itself, so no standing grant (a chat's Trust,
  YOLO, an operator's source list) answers it; it is kept out of the answers a Trust or YOLO switch
  gives in bulk (``answered_alone``); and the registry takes an answer from the owner alone
  (`approval_answer`).
* **Nobody to ask, nothing done.** A session that acts on its own (a loop started Unattended) and a
  gateway with nowhere to ask are refused, saying why (:func:`nobody_to_ask`): a grant that
  approves calls on its own is not consent to what a step may do.
* **It ends with the work that asked.** A turn's Stop and a loop's ending cancel what it is still
  waiting for (:func:`end_asks`), saying why, and nothing is written.

Each ask waits in the gateway, not in the tool call that raised it: an approval waits as long as
the owner's approval window, longer than any tool call may run, and the agent goes on meanwhile.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: The registry id of a batch's ask, before the batch's definition name (`batch_start`).
BATCH_PREFIX = "batch:"
#: The registry id of a workflow save's ask, before the workflow's name (`definition_ask`).
SAVE_PREFIX = "workflow-save:"
#: Every ask of this kind, by its registry-id prefix.
PREFIXES: tuple[str, ...] = (BATCH_PREFIX, SAVE_PREFIX)


def nobody_to_ask(state: Any, chat: str) -> str:
    """Why nobody can be asked for the owner's Allow, as the rest of "…, which …"; "" when someone
    can. A session that acts on its own (`loop.posture`: an Unattended loop's) has nobody there to
    answer, and a gateway with no approval surface has nowhere to ask."""
    if state is None or not callable(getattr(state, "request_approval", None)):
        return "there is nowhere to ask for here"
    session = (getattr(state, "_sessions", None) or {}).get(chat)
    if session is not None and getattr(session, "_unattended", False) is True:
        return "this run cannot ask for: it acts on its own, with nobody there to answer"
    return ""


async def ask(
    state: Any, *, ask_id: str, source: str, tool: str, purpose: str, said: str, session: str
) -> Any:
    """Ask the owner once, as *ask_id*, and return how it ended (``approval_grants.ToolDecision``):
    her Allow or Deny, or no answer at all (nobody answered in time, or the work that asked
    stopped first), which the registry's bool cannot say. Never raises: an ask that could not be
    made is no Allow."""
    from personalclaw.approval_grants import NOBODY, YOU, ToolDecision
    from personalclaw.tool_providers.base import RiskLevel

    try:
        allowed = await state.request_approval(
            ask_id,
            source,
            tool,
            tool_input=said,
            tool_purpose=purpose,
            session=session,
            risk_level=RiskLevel.CAUTION.value,
            answered_alone=True,
        )
        ending = str(state.ended_as(ask_id) or "") or ("approved" if allowed else "rejected")
        return ToolDecision(
            bool(allowed), ending, YOU if ending in ("approved", "rejected") else NOBODY
        )
    except Exception:
        logger.warning("asking for the owner's Allow (%s) failed", ask_id, exc_info=True)
        return ToolDecision(False, "rejected", "approval_failed")


def audit(
    session_key: str, *, source: str, tool: str, decision: Any, metadata: dict[str, Any]
) -> None:
    """The ask's ending as one audit row, in the words the audit log's filters read
    (`audit_outcome_families`): nobody answering, or the work stopping first, is not a Deny, and a
    grant that let it start without asking (a batch that only reads, `batch_start`) is no person's
    answer."""
    from personalclaw.approval_grants import YOU
    from personalclaw.sel import sel

    if decision:
        outcome = "approved" if decision.decided_by == YOU else "auto_approved"
    elif decision.outcome in ("expired", "cancelled"):
        outcome = decision.outcome
    else:
        outcome = "rejected"
    try:
        sel().log_tool_invocation(
            session_key=session_key,
            source=source,
            tool_name=tool,
            outcome=outcome,
            metadata={**metadata, "decided_by": decision.decided_by},
        )
    except Exception:
        logger.debug("SEL audit failed for %s", metadata, exc_info=True)


def end_asks(state: Any, ended: Callable[[str, float], bool], *, reason: str) -> int:
    """End every ask still waiting for its owner's Allow whose ask *ended* says is over, given the
    session it was asked for and when (``started_work.end_started``: a loop's ending, a turn's
    Stop): it is cancelled saying *reason* ("its loop “…” was stopped"), on every surface, so it
    can no longer be allowed, and what it asked for is never written. Returns how many."""
    pending = getattr(state, "_pending_approvals", None) or {}
    asks = [
        approval_id
        for approval_id, entry in list(pending.items())
        if approval_id.startswith(PREFIXES)
        and ended(str(entry.get("session") or ""), float(entry.get("ts") or 0.0))
    ]
    if any(approval_id.startswith(BATCH_PREFIX) for approval_id in asks):
        # A batch keeps the record of its ask until it ends, which says why, first: the wait this
        # wakes then knows its owner ended it, and no gateway asks it again (`batch_start`).
        from personalclaw.workflows.batch_start import ended_unanswered

        for approval_id in asks:
            ended_unanswered(approval_id, reason)
    return sum(1 for approval_id in asks if state.cancel_approval(approval_id, reason=reason))
