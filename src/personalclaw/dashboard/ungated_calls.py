"""A call an agent CLI ran without asking the host: said on its own row, audited and logged.

An ACP CLI decides for itself which of its tools ask the host first
(`acp.permission_authority`), so the chat runner learns of such a call only when its result
lands. :func:`report_ungated_call` is what the runner does then. The call is judged, logged and
audited by ``acp.ungated``, as every host that runs an agent CLI does it; what is the chat's own is
where it says so (the call's card) and the posture it is judged under (the chat's task mode).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from personalclaw.acp import permission_authority as acp_permission_authority
from personalclaw.acp import ungated
from personalclaw.dashboard.chat_utils import strip_status_sentinel

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession


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
    line names the runtime and the tool, never the call's arguments (``acp.ungated.record``).

    ``agent`` names the chat's agent on the audit row. ``declared`` is the declaration the call
    carried, which only a call to PersonalClaw's own ``personalclaw-core`` tools has
    (``acp.mcp_servers.core_tool_declaration``).

    Returns the abort reason, or ``""`` to continue the turn.
    """
    call = ungated.judge(
        acp_cli, title=title, tool_kind=tool_kind, tool_input=tool_input, declared=declared
    )
    task_mode = getattr(session, "_task_mode", "agent")
    abort = ""
    if call.may_have_changed and task_mode in ("ask", "plan"):
        abort = (
            f"{call.shown} ran without a host approval request under {task_mode} mode "
            f"({call.who} never asked) — turn stopped"
        )
    note = acp_permission_authority.ungated_call_note(
        call.who, excused=call.excused, stopped_in=task_mode if abort else ""
    )
    for m in reversed(session.messages):
        if m.get("role") == "tool" and m.get("meta", {}).get("tool_call_id") == request_id:
            _meta = m.setdefault("meta", {})
            _meta["ungated"] = note
            _meta["ungated_declared"] = call.excused
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
    ungated.record(
        call,
        session_key=session_key,
        agent=agent,
        source="dashboard",
        request_id=request_id,
        answer=ungated.HostAnswer(stop=bool(abort), posture={"task_mode": task_mode}),
        where=session.key,
    )
    state.broadcast_ws(
        "activity_event",
        {
            "session": session.key,
            "kind": "permission",
            "text": (
                f"Not gated by host: {call.shown} — documented {call.who} limitation"
                if call.excused
                else f"Ran without host approval: {call.shown} ({call.who} never asked)"
            ),
        },
    )
    if abort:
        state.broadcast_ws(
            "activity_event",
            {"session": session.key, "kind": "permission", "text": abort},
        )
    return abort
