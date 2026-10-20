"""A call an agent CLI ran without asking the host, judged, logged and audited once for every host.

An agent CLI decides for itself which of its calls ask the host first
(:mod:`personalclaw.acp.permission_authority`), so the host learns of a call the CLI's own settings
let run only when its result lands, and there is nothing left to block. Three hosts run agent CLIs:
a chat (``dashboard.ungated_calls``), a room member and a background turn (both through
``llm_helpers.stream_and_collect``). Each says the call ran without asking where its owner reads
it, the chat on the call's own card and a room on its transcript, and each judges it, logs it and
audits it here, so its row reads the same wherever it ran:

* ``ungated`` when nothing excuses it, ``ungated_declared`` for a tool the CLI is known never to ask
  about whose residual was accepted (:attr:`~personalclaw.acp.permission_authority.NotGateable.
  accepted`);
* the risk the call carried, the CLI, why it was never asked, the posture the host judged it by (a
  chat's task mode, a room member's tier) and whether the host stopped the turn for it.

A host stops the turn (:func:`stop_turn`) when such a call may have changed something under a
posture that allows no change, so the model cannot chain more of them behind a gate that was never
consulted. Which posture allows no change is the host's to say (:class:`HostAnswer`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from personalclaw.acp import permission_authority
from personalclaw.audit_subject import log_title
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

#: Why the host was never asked about a call nothing excuses, on its audit row.
NEVER_ASKED = "no session/request_permission for this tool_call"


@dataclass(frozen=True)
class UngatedCall:
    """One call an agent CLI ran without asking the host, as every host describes it."""

    #: The CLI, as its runtime id names it after ``acp:`` (``claude-code``).
    acp_cli: str
    #: The call's title, as the CLI sent it. The audit row stores it masked.
    title: str
    tool_kind: str
    tool_input: str
    #: The call's effective risk (``task_modes.resolve_effective_risk``).
    risk: str
    #: The CLI never asks about this tool, and that residual was accepted.
    excused: bool
    #: Why the host was never asked, for the audit row.
    reason: str
    #: The CLI's name, as every sentence names it ("Claude Code").
    who: str

    @property
    def shown(self) -> str:
        """The title as a sentence may show it: URLs and credentials masked."""
        title, _ = redact_exfiltration_urls(self.title or "?")
        title, _ = redact_credentials(title)
        return title

    @property
    def reported_read(self) -> bool:
        """Whether the call is known to have only read: its tool declares it (one of PersonalClaw's
        own), its command only reads, or the CLI reported a read kind. The kind decides only
        whether a turn stops, never whether anything runs (``task_modes.REPORTED_READ_KINDS``)."""
        from personalclaw.task_modes import REPORTED_READ_KINDS

        return self.risk == "safe" or (self.tool_kind or "").lower() in REPORTED_READ_KINDS

    @property
    def may_have_changed(self) -> bool:
        """Whether a posture that allows no change stops the turn for this call."""
        return not self.excused and not self.reported_read

    def why(self) -> str:
        """Why it ran without asking, as a clause: the CLI's own settings allowed it, or the CLI
        never asks about this tool (``permission_authority.ungated_call_why``)."""
        return permission_authority.ungated_call_why(self.who, excused=self.excused)


@dataclass(frozen=True)
class HostAnswer:
    """A host's answer about one :class:`UngatedCall`: whether its turn stops, and the posture it
    judged that by, which the call's audit row records (``{"task_mode": "plan"}``)."""

    stop: bool = False
    posture: Mapping[str, str] = field(default_factory=dict)


def judge(
    acp_cli: str, *, title: str, tool_kind: str = "", tool_input: str = "", declared: str = ""
) -> UngatedCall:
    """The call *acp_cli* ran without asking, judged: excused or not, and its effective risk.

    ``declared`` is what the call's tool declares, which only PersonalClaw's own tools carry
    (``acp.mcp_servers.core_tool_declaration``). Only an ACCEPTED residual excuses a call: a
    measured one nobody blessed stays as loud as an undeclared hole.
    """
    from personalclaw.providers.image_input import agent_label
    from personalclaw.task_modes import resolve_effective_risk

    entry = permission_authority.not_gateable_entry(acp_cli, title)
    excused = entry is not None and entry.accepted
    return UngatedCall(
        acp_cli=acp_cli,
        title=title,
        tool_kind=tool_kind,
        tool_input=tool_input,
        risk=resolve_effective_risk(declared, title, tool_kind, tool_input),
        excused=excused,
        reason=entry.reason if excused and entry is not None else NEVER_ASKED,
        who=agent_label(f"acp:{acp_cli}"),
    )


def record(
    call: UngatedCall,
    *,
    session_key: str,
    agent: str,
    source: str,
    request_id: str,
    answer: HostAnswer,
    where: str,
) -> None:
    """The one log line and the one audit row for *call*. Never raises.

    The log line names the CLI, the tool and *where* it ran (the session), never the call's
    arguments, and writes the title masked, on one line and bounded (``audit_subject.log_title``):
    an accepted residual at INFO, every other call at WARNING. The audit row keeps what the call
    ran (``tool_input``), masked by the log itself.
    """
    logger.log(
        logging.INFO if call.excused else logging.WARNING,
        "%s (acp:%s) ran %r without asking the host (session %s)%s",
        call.who,
        call.acp_cli,
        log_title(call.title or "?"),
        where,
        " — turn stopped" if answer.stop else "",
    )
    try:
        sel().log_tool_invocation(
            session_key=session_key,
            agent=agent,
            source=source,
            tool_name=call.title,
            tool_kind=call.tool_kind,
            outcome="ungated_declared" if call.excused else "ungated",
            request_id=request_id,
            tool_input=call.tool_input,
            metadata={
                "risk": call.risk,
                "provider": call.acp_cli,
                **dict(answer.posture),
                "reason": call.reason,
                **({"aborted_turn": True} if answer.stop else {}),
            },
        )
    except Exception:  # noqa: BLE001 - the call already ran; losing its row must not end the turn
        logger.warning("SEL audit failed for ungated ACP tool call", exc_info=True)


async def stop_turn(provider: object, why: str) -> None:
    """Cancel the agent CLI's in-flight turn: the ONE seam that actually stops it.

    *provider* is the pooled provider (an ``AcpAgentProvider``), not the inner ``AcpClient``. Those
    two spell cancellation differently: the provider implements the project-wide
    ``AgentProvider.cancel(*, wait_ack_timeout)`` seam that ``SessionManager.cancel_current`` (a
    user-pressed Stop) drives, while ``cancel_session`` exists ONLY on the inner ``AcpClient``. Both
    abort sites used to reach for ``cancel_session`` on the provider, so ``getattr`` returned
    ``None``, the call was skipped, and nothing logged the miss: the host announced the abort and
    wrote a row saying ``aborted_turn: true`` while the CLI ran every remaining call and finished
    the turn normally (measured on a live ``acp:claude-code`` session).

    A provider exposing no cancel at all is logged rather than passed over, because a
    silently-skipped stop is exactly the failure this function replaces.
    """
    cancel = getattr(provider, "cancel", None)
    if not callable(cancel):
        logger.warning(
            "ACP abort (%s) could not cancel the turn: provider %s exposes no cancel() seam",
            why,
            type(provider).__name__,
        )
        return
    try:
        await cancel(wait_ack_timeout=0.0)
    except Exception:  # noqa: BLE001 - a failed cancel is logged; the turn's own end still comes
        logger.warning("ACP cancel after %s failed", why, exc_info=True)
