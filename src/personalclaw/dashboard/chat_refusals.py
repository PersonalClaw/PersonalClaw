"""How a chat's approval gate refuses a call on a turn nobody is watching.

Two refusals end the same way, so they are written once: the call is refused at once rather
than parked on a person who is not there, the transcript says why, the audit log has its row,
and the Inbox (where a person looks in the morning) has a note.

* **The unattended fail-fast** (``chat_runner``'s last gate before a turn waits on a human).
  Everything past that point publishes the approval to every surface (the card, the approvals
  list, the Inbox) and then blocks for up to two hours. On an unattended turn there is no human,
  so that is not a gate: it is a two-hour stall that ends in a rejection anyway. It is denied
  NOW, with the reason, and the turn continues: the CLI sees a normal denial and can adapt or
  stop, which is the "never wedge waiting for a human" semantic the native runtime already has.
  It is deliberately placed LAST, after Trust/YOLO and after every deny path. An unattended loop
  sets ``_trust``, so its tools were already auto-approved and never reach it; what does is a
  request nothing could resolve. So it can only ever turn a two-hour park into an immediate
  denial, never a denial into an approval.
* **A call past its run's bounds** (:mod:`personalclaw.run_bounds`): a shell command reaching a
  host off the allowed hosts, or writing outside the folders the run works in. Asked before any
  grant, since an unattended loop's grant would otherwise answer it.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from personalclaw import auto_denials, run_bounds, security
from personalclaw.audit_subject import log_title
from personalclaw.dashboard import turn_endings
from personalclaw.dashboard.step_notes import note_refusal
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.task_modes import tool_input_to_str

logger = logging.getLogger(__name__)


async def refuse_unattended(
    state: Any,
    session: Any,
    session_key: str,
    event: Any,
    refuse: Callable[..., Awaitable[None]],
    *,
    agent: str,
    said: str,
    reason: str,
    decided_by: str,
    why: str,
    kind: str,
    risk: str = "",
) -> None:
    """Refuse *event*'s call on an unattended turn (*refuse* is the runner's own refusal, which
    tells the agent *why*, a ``security.DENY_KIND_*`` *kind* of reason, never her decline), saying
    on the call's card that it did not run and *said* why (``step_notes.note_refusal``), with its
    audit row and its Inbox note."""
    await refuse(event, why=why, kind=kind)
    title, _ = redact_exfiltration_urls(event.title)
    title, _ = redact_credentials(title)
    note_refusal(session, event, said)
    logger.warning(
        "unattended: refused %r on %s (%s)", log_title(event.title), session.key, decided_by
    )
    sel().log_tool_invocation(
        session_key=session_key,
        agent=agent,
        source="dashboard",
        tool_name=event.title,
        tool_kind=event.tool_kind,
        outcome="denied",
        request_id=event.request_id,
        tool_input=event.tool_input,
        metadata={
            # The answers the agent offered for the call (`turn_endings.offered`), as on every
            # other decision row of the approval gate.
            **turn_endings.offered(event.options),
            "reason": reason,
            **({"risk": risk} if risk else {}),
            "decided_by": decided_by,
        },
    )
    shown = ""
    if event.tool_input:
        shown, _ = redact_exfiltration_urls(tool_input_to_str(event.tool_input))
        shown, _ = redact_credentials(shown)
    auto_denials.note_unattended(state, session_key=session.key, tool=title, tool_input=shown)


async def refuse_past_bounds(
    state: Any,
    session: Any,
    session_key: str,
    event: Any,
    refuse: Callable[..., Awaitable[None]],
    *,
    agent: str,
) -> bool:
    """Refuse an unattended turn's call that reaches past its run's bounds; whether it did.

    The run's folders are its workspace (the folder its file tools resolve against), the folders
    it was given, and its own temporary folder."""
    from personalclaw.config.loader import workspace_root

    workspace = (
        str(Path(session.workspace_dir).resolve())
        if getattr(session, "workspace_dir", "")
        else str(workspace_root())
    )
    reach, within, scratch = run_bounds.asked_call_reach(
        getattr(event, "risk_level", "") or "",
        event.title,
        event.tool_kind,
        event.tool_input,
        session_key=session.key,
        workspace=workspace,
        extra_roots=list(getattr(session, "_extra_tool_roots", []) or []),
        unattended=True,
    )
    if not reach:
        return False
    why = run_bounds.refusal(reach, within, scratch=scratch)
    await refuse_unattended(
        state,
        session,
        session_key,
        event,
        refuse,
        agent=agent,
        said=run_bounds.past_bounds(reach, within),
        reason="run_bounds",
        decided_by="run_bounds",
        why=why,
        kind=security.DENY_KIND_POLICY,
    )
    return True


__all__ = ["refuse_past_bounds", "refuse_unattended"]
