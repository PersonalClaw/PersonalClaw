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

import inspect
import logging
from collections.abc import Callable, Mapping
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
            # The host's posture first, so what core judged (the risk, the CLI, why it was never
            # asked, the stop) is never overwritten by a posture key of the same name.
            metadata={
                **dict(answer.posture),
                "risk": call.risk,
                "provider": call.acp_cli,
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


async def report(
    provider: object,
    card: object,
    *,
    session_key: str,
    agent: str = "",
    on_ungated: "Callable[[UngatedCall], HostAnswer] | None" = None,
    source: str = "",
) -> HostAnswer | None:
    """The call *card* made, which *provider*'s agent CLI ran without asking the host: judged,
    told to the caller (*on_ungated*), logged and audited (:func:`record`), and the turn stopped
    when the caller says so (:func:`stop_turn`). ``None`` when *provider* is no agent CLI, whose
    calls this judges nothing of.

    A host calls it at a call's result when the call made no permission request: the card is what
    the call was made with, since a result carries no arguments. A caller whose answer fails, or is
    no :class:`HostAnswer`, stops the turn: what it would have held the call to cannot be told, and
    the call already ran.
    *source* names the host on the audit row, ``""`` for one the session names.
    """
    from personalclaw.task_modes import tool_input_to_str

    runtime_id = str(getattr(provider, "provider_id", "") or "")
    if not runtime_id.startswith("acp:"):
        return None
    call = judge(
        runtime_id.removeprefix("acp:"),
        title=str(getattr(card, "title", "") or ""),
        tool_kind=str(getattr(card, "tool_kind", "") or ""),
        tool_input=tool_input_to_str(getattr(card, "tool_input", None))[:2000],
        declared=str(getattr(card, "risk_level", "") or ""),
    )
    answer = HostAnswer()
    if on_ungated is not None:
        try:
            given: object = on_ungated(call)
        except Exception:  # noqa: BLE001 - fail closed: the turn stops
            logger.warning("the host could not judge an ungated call; stopping", exc_info=True)
            given = HostAnswer(stop=True)
        if not isinstance(given, HostAnswer):
            # No answer it can be held to (an async function's coroutine, ``None``): fail closed.
            logger.warning(
                "the host gave no answer for an ungated call (%s); stopping", type(given).__name__
            )
            if inspect.iscoroutine(given):
                given.close()
            given = HostAnswer(stop=True)
        answer = given
    record(
        call,
        session_key=session_key,
        agent=agent,
        source=source,
        request_id=str(getattr(card, "tool_call_id", "") or ""),
        answer=answer,
        where=session_key or "background",
    )
    if answer.stop:
        await stop_turn(provider, "ungated tool call")
    return answer


async def report_for_app(
    provider: object,
    card: object,
    *,
    session_key: str,
    on_ungated: "Callable[[UngatedCall], HostAnswer] | None" = None,
) -> HostAnswer | None:
    """:func:`report` as an app makes it (``personalclaw.sdk.channel.report_ungated_call``), held
    to the session *provider* is the runtime of (``session.held_under``).

    The row names the app whose code asked for that session as its source, and the agent the
    session runs, never what an argument says, so an app cannot write a row as core's or as
    another app's. *session_key* must be that session's: a key the runtime is not held under
    (another chat's), a runtime that is no session's, or one an app did not ask for (core's own),
    is refused with :class:`PermissionError`, and nothing is recorded or stopped. So is a call made
    by another app's code than the one that asked for the session, and one no app's code made (a
    task started on it runs on core's frames alone, so whose it is cannot be told): an app awaits
    it.
    """
    from personalclaw import app_code
    from personalclaw.session import held_under

    held = held_under(provider)
    caller = app_code.owner()
    refusal = ""
    if held is None:
        refusal = "the runtime is no session's"
    elif held[0] != session_key:
        refusal = f"the runtime is not held under session {session_key!r}"
    elif not held[1]:
        refusal = "the session is PersonalClaw's own, not an app's"
    elif caller is None:
        refusal = "no app's code made this call"
    elif caller != held[1]:
        refusal = f"the session belongs to app {held[1]!r}"
    if refusal:
        logger.error(
            "report_ungated_call refused: %s (called by %s)",
            refusal,
            f"app {caller}" if caller else "no app",
        )
        raise PermissionError(f"report_ungated_call: {refusal}")
    assert held is not None
    key, app, agent = held
    return await report(
        provider, card, session_key=key, agent=agent, on_ungated=on_ungated, source=app
    )
