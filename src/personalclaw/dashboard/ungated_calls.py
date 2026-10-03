"""A call an agent CLI ran without asking the host: said on its own row, audited and logged.

An ACP CLI decides for itself which of its tools ask the host first
(`acp.permission_authority`), so the chat runner learns of such a call only when its result
lands. :func:`report_ungated_call` is what the runner does then.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from personalclaw.acp import permission_authority as acp_permission_authority
from personalclaw.audit_subject import log_title
from personalclaw.dashboard.chat_utils import strip_status_sentinel
from personalclaw.dashboard.state import resolve_effective_risk
from personalclaw.providers.image_input import agent_label
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel
from personalclaw.task_modes import REPORTED_READ_KINDS

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

logger = logging.getLogger(__name__)


def report_ungated_call(
    state: DashboardState,
    session: _ChatSession,
    *,
    agent: str,
    session_key: str,
    acp_cli: str,
    title: str,
    tool_kind: str,
    tool_input: str,
    request_id: str,
    declared: str = "",
) -> str:
    """Surface an ACP tool call the host was never asked about; return an abort reason.

    §2.2's honest half (`G27`). An ACP CLI chooses which of its tools request
    permission, so the host's gate is opt-in *by the CLI*. When a tool result lands
    for a call that never reached the gate, the tool already ran — there is nothing
    left to block. What is still available, and what the finding actually asked for,
    is a positive mechanism:

    * an **accepted** entry in the per-provider not-gateable registry means this hole
      is written down AND blessed — surfaced, not silent, but not treated as a new
      incident;
    * everything else is the dangerous case, and that includes a residual which is
      measured but *not* accepted (``entry.accepted`` False). It is audited as
      ``ungated`` and — when it is a mutation under a read-only posture — aborts the
      turn, so the model cannot chain further ungated mutations behind a gate that was
      never consulted. Writing a hole down is never a way to silence it.

    Either way the call's OWN row says it ran without asking her, and why
    (`acp.permission_authority.ungated_call_note`): live, on the card the page already
    shows (a ``tool_call`` update frame carrying ``ungated``), and in the transcript, on
    the row a reload rebuilds that card from (``meta.ungated``). So it never reads like a
    call she approved, and a reload shows what the turn showed — no row of its own. One log
    line names the runtime and the tool, never the call's arguments.

    ``agent`` names the chat's agent on the audit row. ``declared`` is the declaration the call
    carried, which only a call to PersonalClaw's own ``personalclaw-core`` tools has
    (``acp.mcp_servers.core_tool_declaration``).

    Returns the abort reason, or ``""`` to continue the turn.
    """
    entry = acp_permission_authority.not_gateable_entry(acp_cli, title)
    # Declared is not excused: only an ACCEPTED residual may quiet the signal.
    excused = entry is not None and entry.accepted
    risk = resolve_effective_risk(declared, title, tool_kind, tool_input)
    # The call already ran, so the only question left is whether it CHANGED something under
    # a read-only posture. The evidence is what the tool declares (one of our own), a
    # read-only shell command, or a call the CLI reported with a read kind. The kind decides
    # only whether the turn stops, never whether anything runs (`REPORTED_READ_KINDS`).
    reported_read = risk == "safe" or (tool_kind or "").lower() in REPORTED_READ_KINDS
    task_mode = getattr(session, "_task_mode", "agent")
    _title, _ = redact_exfiltration_urls(title or "?")
    _title, _ = redact_credentials(_title)
    who = agent_label(f"acp:{acp_cli}")
    abort = ""
    if not excused and not reported_read and task_mode in ("ask", "plan"):
        abort = (
            f"{_title} ran without a host approval request under {task_mode} mode "
            f"({who} never asked) — turn stopped"
        )
    note = acp_permission_authority.ungated_call_note(
        who, excused=excused, stopped_in=task_mode if abort else ""
    )
    for m in reversed(session.messages):
        if m.get("role") == "tool" and m.get("meta", {}).get("tool_call_id") == request_id:
            _meta = m.setdefault("meta", {})
            _meta["ungated"] = note
            _meta["ungated_declared"] = excused
            state.broadcast_ws(
                "tool_call",
                {
                    "session": session.key,
                    "tool": strip_status_sentinel(m.get("content", "")),
                    "tool_call_id": request_id,
                    "ungated": note,
                    "update": True,
                },
            )
            break
    # An accepted residual is the one case the host may be quiet about (INFO); every other is loud.
    logger.log(
        logging.INFO if excused else logging.WARNING,
        "%s (acp:%s) ran %r without asking the host (session %s)%s",
        who,
        acp_cli,
        log_title(title or "?"),
        session.key,
        " — turn stopped" if abort else "",
    )
    state.broadcast_ws(
        "activity_event",
        {
            "session": session.key,
            "kind": "permission",
            "text": (
                f"Not gated by host: {_title} — documented {who} limitation"
                if excused
                else f"Ran without host approval: {_title} ({who} never asked)"
            ),
        },
    )
    try:
        sel().log_tool_invocation(
            session_key=session_key,
            agent=agent,
            source="dashboard",
            tool_name=title,
            tool_kind=tool_kind,
            outcome="ungated_declared" if excused else "ungated",
            request_id=request_id,
            tool_input=tool_input,
            metadata={
                "risk": risk,
                "provider": acp_cli,
                "task_mode": task_mode,
                "reason": (
                    entry.reason
                    if excused and entry is not None
                    else "no session/request_permission for this tool_call"
                ),
                **({"aborted_turn": True} if abort else {}),
            },
        )
    except Exception:
        logger.warning("SEL audit failed for ungated ACP tool call", exc_info=True)
    if abort:
        state.broadcast_ws(
            "activity_event",
            {"session": session.key, "kind": "permission", "text": abort},
        )
    return abort
