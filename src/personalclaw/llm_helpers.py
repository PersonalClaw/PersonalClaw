"""Shared LLM interaction helpers — stream collection, JSON parsing, history saving.

Eliminates duplicate code across gateway, handler, dashboard, subagent,
and history modules.
"""

import asyncio
import contextlib
import contextvars
import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterator
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar

from personalclaw import memory_writes
from personalclaw.hooks import fire_tool_hooks, get_global_hook_store
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    LLMEvent,
    ModelProvider,
    ModelSubstitution,
)
from personalclaw.llm.events import (
    EVENT_MODEL_SUBSTITUTION,
    EVENT_SPENT,
    refusal_audit,
    unasked_outcome,
    unasked_reason,
)
from personalclaw.sel import sel as _sel
from personalclaw.turn_streams import closing_stream

_PROMPT_BUSY_RETRIES = 2
_PROMPT_BUSY_DELAY = 1.5  # seconds between retries

_ChainResult = TypeVar("_ChainResult")


class PromptBusyExhaustedError(Exception):
    """Provider was shut down after prompt-busy retries were exhausted."""


if TYPE_CHECKING:
    from personalclaw.acp.ungated import HostAnswer, UngatedCall
    from personalclaw.approval_grants import ToolDecision
    from personalclaw.guardrails.local_queue import Attended
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import HookManager
    from personalclaw.usage_ledger import Attribution

logger = logging.getLogger(__name__)


# ── Tool Approval Policies ──


class ToolApprovalPolicy(Enum):
    """How to handle tool permission requests during streaming."""

    AUTO_APPROVE = "auto_approve"
    REJECT_ALL = "reject_all"
    HOOK_BASED = "hook_based"


# Callback type for custom tool approval logic
OnPermissionCallback = Callable[[LLMEvent], Awaitable[bool]]


# ── Stream and Collect ──


async def stream_and_collect(
    provider: ModelProvider,
    message: str,
    *,
    approval_policy: ToolApprovalPolicy = ToolApprovalPolicy.REJECT_ALL,
    hooks: "HookManager | None" = None,
    on_chunk: Callable[[str], None] | None = None,
    on_tool_approval: "Callable[[LLMEvent], Awaitable[bool | ToolDecision]] | None" = None,
    on_complete: Callable[[LLMEvent], None] | None = None,
    on_substitution: Callable[[str], None] | None = None,
    session_key: str = "",
    agent: str = "",
    on_ungated: "Callable[[UngatedCall], HostAnswer] | None" = None,
    on_refused: "Callable[[LLMEvent, str], None] | None" = None,
) -> str:
    """Stream a message through an LLM provider and collect the full response.

    This is the core pattern used by cron, heartbeat, subagent, room and one-shot calls.

    Args:
        provider: The LLM provider to stream through.
        message: The prompt to send.
        approval_policy: How to handle tool permission requests. Under every one, a call the
            deny-list refuses is refused first (``_resolve_permission``). The default refuses
            every request, each audited with its reason: a call that asks a model for a text
            answer (``one_shot_completion``, and every chore through it) lets it use no tool, and
            nobody is there to be asked. A turn that may use tools passes its own policy.
        hooks: The HookManager the deny-list is read from (the gateway's own when ``None``), and
            whose auto-approve patterns answer a call under HOOK_BASED.
        on_chunk: Optional callback invoked with each text chunk (for progress).
        on_tool_approval: Optional async callback for interactive approval.
        on_complete: Optional callback invoked with the terminal ``EVENT_COMPLETE``
            event (which carries the turn's token counts + cost) just before the
            text is returned — the seam the cost/token ledger's non-``run_chat``
            write-sites use (COST-AND-TOKEN-OBSERVABILITY C2). A turn that ends in an
            error hands it its ``EVENT_SPENT`` instead, what the calls it made before
            the error cost, and the error is raised after. Default ``None``
            leaves the streamed text byte-identical for every other caller. Never
            raises into the turn: a callback fault is swallowed.
        on_substitution: For a caller that shows it, the sentence a turn says when its
            model failed before replying and the next model of its chain answers ("Ran on
            Y instead of X: …"), before anything that model streams. Passing it is what
            lets the turn fall back at all (``NativeAgentRuntime.announce_failover``): a
            caller with nowhere to show the sentence keeps the failure, since another
            model's reply would read as the chosen one's.
        session_key: The session the turn runs in, and ``agent`` the agent it runs as: what
            every audit row of the turn's calls names as who made them, and whose blocking hooks
            each call meets (``pre_tool_hooks``). ``""`` names none: the default agent's hooks.
        on_ungated: For a turn on an agent CLI, told of each call the CLI ran without asking
            (``acp.ungated``): the caller says where it shows it, and answers whether the turn
            stops for it and the posture it judged that by (``acp.ungated.HostAnswer``). Every such
            call is audited as ``ungated`` whether or not a caller is told.
        on_refused: Told of each asked call the deny-list or a blocking hook refused, with the
            refusal's reason, for a caller that shows its turn's refusals. Each is audited whether
            or not one is told.

    Returns:
        The complete response text.
    """
    from personalclaw.acp.errors import AcpError

    # An agent CLI decides which of its calls ask the host first (`acp.permission_authority`), so
    # a result for a call it never asked about is one it ran without asking anyone.
    runtime_id = str(getattr(provider, "provider_id", "") or "")
    acp_cli = runtime_id.removeprefix("acp:") if runtime_id.startswith("acp:") else ""
    for attempt in range(_PROMPT_BUSY_RETRIES + 1):
        result_text = ""
        # The calls that were ASKED about: each is audited where it is answered
        # (`_resolve_permission`), every other call once, at its result, from the card it was
        # made with (a result carries no arguments).
        asked: set[str] = set()
        called: dict[str, LLMEvent] = {}
        if on_substitution is not None:
            let_fail_over(provider)
        try:
            async with closing_stream(provider.stream(message)) as events:
                async for event in events:
                    if event.kind == EVENT_TEXT_CHUNK:
                        result_text += event.text
                        if on_chunk:
                            on_chunk(event.text)
                    elif event.kind == EVENT_MODEL_SUBSTITUTION:
                        if on_substitution is not None:
                            on_substitution(event.text)
                    elif event.kind == EVENT_PERMISSION_REQUEST:
                        asked.add(str(event.tool_call_id or ""))
                        approved = await _resolve_permission(
                            provider,
                            event,
                            approval_policy,
                            hooks,
                            on_tool_approval,
                            session_key=session_key,
                            agent=agent,
                            on_refused=on_refused,
                        )
                        if not approved:
                            continue
                    elif event.kind == EVENT_TOOL_CALL:
                        # The card of a call being made, before any gate has run (the native loop
                        # checks its deny-list, task mode and approval only after yielding it), so
                        # it is not audited here: this row said `auto_approved` for every call, a
                        # refused one included. PreToolUse hooks fire, informational only.
                        called[str(event.tool_call_id or "")] = event
                        await fire_tool_hooks(
                            get_global_hook_store(),
                            event.title,
                            event.tool_input,
                        )
                    elif (
                        event.kind == EVENT_TOOL_RESULT
                        and str(event.tool_call_id or "") not in asked
                    ):
                        card = called.pop(str(event.tool_call_id or ""), None)
                        if acp_cli:
                            await _report_ungated(
                                provider,
                                acp_cli,
                                card or event,
                                session_key=session_key,
                                agent=agent,
                                on_ungated=on_ungated,
                            )
                            continue
                        # A call nobody was asked about: its one audit row, from what the runtime
                        # stamped on its result (`llm.events.unasked_outcome`).
                        meta = event.tool_meta or {}
                        decided_by = unasked_reason(meta)
                        _sel().log_tool_invocation(
                            session_key=session_key,
                            agent=agent,
                            source="llm_helpers",
                            tool_name=event.title,
                            tool_kind=event.tool_kind,
                            outcome=unasked_outcome(meta),
                            request_id=str(event.tool_call_id or ""),
                            tool_input=card.tool_input if card is not None else None,
                            metadata={
                                "reason": decided_by,
                                "decided_by": decided_by,
                                **refusal_audit(meta),
                            },
                        )
                    elif event.kind in (EVENT_COMPLETE, EVENT_SPENT):
                        if on_complete is not None:
                            try:
                                on_complete(event)
                            except Exception:  # noqa: BLE001 — telemetry must never break a turn
                                logger.debug("stream_and_collect on_complete failed", exc_info=True)
                        if event.kind == EVENT_COMPLETE:
                            break
            return result_text
        except AcpError as exc:
            if "already in progress" not in str(exc) or attempt >= _PROMPT_BUSY_RETRIES:
                if "already in progress" in str(exc):
                    # Provider is permanently stuck — kill it so the next
                    # get_or_create cold-starts a fresh process.
                    logger.warning(
                        "Prompt busy after %d retries, shutting down provider", _PROMPT_BUSY_RETRIES
                    )
                    try:
                        await provider.shutdown()
                    except Exception:
                        logger.debug("Provider shutdown after busy retries failed", exc_info=True)
                    raise PromptBusyExhaustedError(str(exc)) from exc
                raise
            logger.warning(
                "Prompt busy (attempt %d/%d), cancelling and retrying: %s",
                attempt + 1,
                _PROMPT_BUSY_RETRIES,
                exc,
            )
            try:
                await provider.cancel()
            except Exception:
                logger.debug("Cancel before retry failed", exc_info=True)
            await asyncio.sleep(_PROMPT_BUSY_DELAY * (2**attempt))
    return ""  # unreachable, satisfies type checker


async def _report_ungated(
    provider: ModelProvider,
    acp_cli: str,
    card: LLMEvent,
    *,
    session_key: str,
    agent: str,
    on_ungated: "Callable[[UngatedCall], HostAnswer] | None",
) -> None:
    """A call *acp_cli* ran without asking the host, from the card it was made with: judged, told
    to the caller (*on_ungated*), logged and audited (``acp.ungated``), and the turn stopped when
    the caller says so. A caller whose answer fails stops the turn: what it would have held the
    call to cannot be told, and the call already ran."""
    from personalclaw.acp import ungated
    from personalclaw.task_modes import tool_input_to_str

    call = ungated.judge(
        acp_cli,
        title=str(card.title or ""),
        tool_kind=str(card.tool_kind or ""),
        tool_input=tool_input_to_str(card.tool_input)[:2000],
        declared=str(getattr(card, "risk_level", "") or ""),
    )
    answer = ungated.HostAnswer()
    if on_ungated is not None:
        try:
            answer = on_ungated(call)
        except Exception:  # noqa: BLE001 - fail closed: the turn stops
            logger.warning("the host could not judge an ungated call; stopping", exc_info=True)
            answer = ungated.HostAnswer(stop=True)
    ungated.record(
        call,
        session_key=session_key,
        agent=agent,
        # Named by the session it ran in, as the turn's asked calls are; a turn in none is ours.
        source="" if session_key else "llm_helpers",
        request_id=str(card.tool_call_id or ""),
        answer=answer,
        where=session_key or "background",
    )
    if answer.stop:
        await ungated.stop_turn(provider, "ungated tool call")


def json_object_problem(text: str) -> str:
    """What is wrong with *text* read as a JSON object (``parse_llm_json``), ``""`` when it holds
    one."""
    return "" if parse_llm_json(text) is not None else "no JSON object"


def let_fail_over(provider: object) -> None:
    """Let *provider*'s next turn go to the next model of its chain when its model fails before
    it replies, for a caller that says the substitute (it handles ``EVENT_MODEL_SUBSTITUTION``).

    The rule a one-shot call's chain walk follows (:func:`run_over_use_case_chain`): a provider
    error, a timeout and an open breaker hand the turn on
    (``NativeAgentRuntime.announce_failover``). A provider without the seam (an ACP runtime) is
    left as it is.
    """
    announce = getattr(provider, "announce_failover", None)
    if callable(announce):
        announce()


async def _resolve_permission(
    provider: ModelProvider,
    event: LLMEvent,
    policy: ToolApprovalPolicy,
    hooks: "HookManager | None",
    on_tool_approval: "Callable[[LLMEvent], Awaitable[bool | ToolDecision]] | None" = None,
    session_key: str = "",
    agent: str = "",
    on_refused: "Callable[[LLMEvent, str], None] | None" = None,
) -> bool:
    """Resolve a tool permission request. Returns True if approved.

    The refusals come first, in the order every gate asks them. The deny-list, whatever *policy*
    says: a call the hook chain refuses, read on the command that would run as well as on its
    title (``screen_tool_call``, with *hooks*, or the gateway's own when none is handed over).
    Then a policy that lets nothing run (``reject_all``), and a call past the run's bounds with
    nobody to ask. Then the operator's blocking hooks, bound to the agent *agent* the turn runs as
    (``pre_tool_hooks``): a hook that blocks the call or fails to run refuses it, before anything
    approves it or anyone is asked. *on_refused* is told of a refusal by the deny-list or a hook,
    with its reason.

    Then who approves it. Under ``hook_based``, an operator's pattern (*hooks*); a callback, when
    one is given; and with nobody to ask, the run's own policy. ``auto_approve`` approves.
    ``hook_based`` approves only what a hook decides, so a call no pattern approved is refused, as
    an unattended chat refuses what nothing approves, unless it asks nobody anywhere (what its tool
    declares: ``approval_grants.declared_answer``).

    Each decision is audited once, here, saying who decided it (``decided_by``). Both ways this
    approves without asking — a hook's auto-approve verdict and the run's own policy — are grants,
    so the operator ceiling bounds them (`approval_grants`, rule 2): under ``approval: ask`` a
    call with nobody to ask is declined, whatever policy the caller passed.
    """
    from personalclaw import approval_grants, pre_tool_hooks
    from personalclaw.acp.permission_authority import screen_tool_call
    from personalclaw.hooks import TOOL_AUTO_APPROVE, TOOL_DENY
    from personalclaw.sel import sel

    def _log(outcome: str, **extra):
        sel().log_tool_invocation(
            session_key=session_key,
            agent=agent,
            tool_name=event.title,
            tool_kind=event.tool_kind,
            outcome=outcome,
            request_id=event.request_id,
            tool_input=event.tool_input,
            **extra,
        )

    title = str(event.title or "")[:80]
    caller = session_key or "background"
    # The deny-list is a floor (the shell denylist's, for a CLI's request): nobody is asked about
    # what cannot run, and no policy approves it.
    tool_result = screen_tool_call(
        hooks,
        str(event.title or ""),
        event.tool_input,
        tool_kind=str(getattr(event, "tool_kind", "") or ""),
        declared=getattr(event, "risk_level", "") or "",
    )
    if tool_result.action == TOOL_DENY:
        await provider.reject_tool(event.request_id)
        control = tool_result.audit()
        _log(
            "refused" if control else "denied",
            error=tool_result.reason,
            metadata={"decided_by": control.get("control", "hook_deny"), **control},
        )
        if on_refused is not None:
            on_refused(event, tool_result.reason or "a hook refused it")
        return False

    # A command reaching a host off the allowed hosts is answered by a person or not at all
    # (`run_bounds`): no hook pattern and no policy approves it.
    from personalclaw.run_bounds import off_list

    reaches_off_list = off_list(event, session_key)
    if policy == ToolApprovalPolicy.REJECT_ALL:
        await provider.reject_tool(event.request_id)
        _log(
            "rejected", metadata={"reason": "reject_all_policy", "decided_by": "reject_all_policy"}
        )
        return False

    if reaches_off_list and on_tool_approval is None:
        await provider.reject_tool(event.request_id)
        _log("denied", metadata={"reason": "run_bounds", "decided_by": "run_bounds"})
        return False

    # The operator's blocking hooks, at the one step every path asks them: before any pattern,
    # callback or policy can approve the call. A call its own runtime asks about met them there.
    hooks_said = await pre_tool_hooks.on_request(event, agent=agent or None)
    if hooks_said.refused:
        await provider.reject_tool(event.request_id)
        _log(**hooks_said.audit_row())
        if on_refused is not None:
            on_refused(event, hooks_said.note)
        return False

    if policy == ToolApprovalPolicy.HOOK_BASED and hooks is not None:
        if (
            not reaches_off_list
            and tool_result.action == TOOL_AUTO_APPROVE
            and approval_grants.stands(
                approval_grants.HOOK_PATTERN,
                caller=caller,
                subject=title,
                level=approval_grants.LEVEL_HOOK,
            )
        ):
            await provider.approve_tool(event.request_id)
            _log(
                "auto_approved",
                metadata={
                    "reason": "hook_auto_approve",
                    "decided_by": approval_grants.HOOK_PATTERN,
                },
            )
            return True

    # Interactive approval if callback provided
    if on_tool_approval:
        approved = await on_tool_approval(event)
        # A callback that says who decided (the gateway relay's and a room's `ToolDecision`) is
        # recorded as saying so. A plain bool does not, so the row does not guess who: a room's
        # gate answers one for a call the member's own tier refuses, which nobody was asked about.
        by = str(getattr(approved, "decided_by", "") or "")
        decided = {"decided_by": by} if by else {}
        if not approved:
            await provider.reject_tool(event.request_id)
            _log("rejected", metadata={"reason": "interactive_rejected", **decided})
            return False
        await provider.approve_tool(event.request_id)
        # Settled without asking anyone (a grant, or a read, which asks nobody) is recorded as
        # that, never as a person's Allow.
        if getattr(approved, "outcome", "") == "auto_approved":
            _log("auto_approved", metadata={"reason": by, **decided})
        else:
            _log("approved", metadata={"reason": "interactive", **decided})
        return True

    # Nobody to ask, and a hook decides: what no pattern approved is refused, as an unattended
    # chat refuses what nothing approves, unless its tool declares that it asks nobody anywhere
    # (it only reads, or what it starts asks the owner itself). Not a grant either way.
    if policy == ToolApprovalPolicy.HOOK_BASED:
        unasked = approval_grants.declared_answer(event)
        if unasked:
            await provider.approve_tool(event.request_id)
            _log("auto_approved", metadata={"reason": unasked, "decided_by": unasked})
            return True
        await provider.reject_tool(event.request_id)
        _log(
            "denied",
            metadata={"reason": "unattended_fail_fast", "decided_by": "unattended_no_one_to_ask"},
        )
        return False

    # Nobody to ask: the run's own policy approves. A grant like any other, so an `ask` ceiling
    # declines the call instead.
    if not approval_grants.stands(
        approval_grants.SESSION_POLICY, caller=caller, subject=f"policy={policy.value},{title}"
    ):
        await provider.reject_tool(event.request_id)
        _log(
            "rejected",
            metadata={"reason": "refused_by_ceiling", "decided_by": approval_grants.NOBODY},
        )
        return False
    await provider.approve_tool(event.request_id)
    _log(
        "auto_approved",
        metadata={"reason": policy.value, "decided_by": approval_grants.SESSION_POLICY},
    )
    return True


# ── Reading a JSON answer ──
#
# ONE reading of a model's JSON answer. The one-shot call's own check (``output_type``), every
# caller's check of the answer's shape (``validate=``, ``expecting``) and every parser of the same
# answer read it here, so an answer the call accepted is one its caller can read. Each used to keep
# a reading of its own, and they disagreed: the call took an answer in a markdown fence as usable,
# the triage's proposal check read the same text with ``json.loads`` and called it "not JSON", and
# the chain moved on from every model that had answered.

#: The body of each markdown code fence (```json … ``` or ``` … ```).
_FENCED = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)

#: Where a JSON value of each kind can start, for the kinds a reader asks for.
_OPENERS: dict[type, re.Pattern[str]] = {dict: re.compile(r"\{"), list: re.compile(r"\[")}
_ANY_OPENER = re.compile(r"[{\[]")

#: The most places in one answer a JSON value is tried from. An answer asked for JSON that has not
#: begun one after this many braces is prose, and the bound keeps an answer of thousands of unclosed
#: openers from costing the event loop a decode at every one of them.
_MOST_STARTS = 256

#: What :func:`_document` returns for text that is not one JSON document.
_NOT_JSON = object()


def _document(text: str) -> Any:
    """*text* decoded as one JSON document, or :data:`_NOT_JSON`."""
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return _NOT_JSON


def _first_value(text: str, kind: type) -> Any:
    """The first object or array of *kind* that starts anywhere in *text*, or ``None``.

    The decoder itself decides where each candidate ends, so a brace inside a string, a nested
    object and prose after the value are all read as JSON reads them.
    """
    opener = _OPENERS.get(kind, _ANY_OPENER)
    decoder = json.JSONDecoder()
    found = opener.search(text)
    for _ in range(_MOST_STARTS):
        if found is None:
            return None
        try:
            value, _end = decoder.raw_decode(text, found.start())
        except (ValueError, RecursionError):
            value = None
        if value is not None and isinstance(value, kind):
            return value
        found = opener.search(text, found.start() + 1)
    return None


def _parse_llm(text: object, kind: type) -> Any:
    """The JSON value of *kind* a model's answer holds, or ``None``: the one reading.

    *kind* is ``dict``, ``list`` or ``object`` (any value). Models wrap the JSON they were asked
    for in a markdown fence and put a sentence before or after it, and that is what this reads
    through: the whole answer when it is one JSON document of the kind, else a fenced block that
    is one, else the first object or array of the kind that starts inside a fenced block, then
    anywhere in the answer. So the answer a model fenced is preferred to an object it quoted before
    it. A bare word, a near-miss and a value of another kind are no value. An answer that was
    already read (an object an injected completion returns) is itself.
    """
    if isinstance(text, (dict, list)):
        return text if isinstance(text, kind) else None
    if not isinstance(text, str):
        return None
    body = text.strip()
    if not body:
        return None
    fenced = [block.strip() for block in _FENCED.findall(body)]
    for region in (body, *fenced):
        value = _document(region)
        if value is not _NOT_JSON and isinstance(value, kind):
            return value
    for region in (*fenced, body):
        value = _first_value(region, kind)
        if value is not None:
            return value
    logger.debug("No JSON %s in the model's answer: %.200s", kind.__name__, body)
    return None


def parse_llm_json(text: object) -> dict | None:
    """The JSON object a model's answer holds (:func:`_parse_llm`), or ``None``."""
    return _parse_llm(text, dict)


def parse_llm_json_list(text: object) -> list | None:
    """The JSON array a model's answer holds (:func:`_parse_llm`), or ``None``."""
    return _parse_llm(text, list)


def parse_llm_json_value(text: object) -> Any:
    """Any JSON value a model's answer holds (:func:`_parse_llm`), or ``None``: for a reader that
    takes an object or an array, and tells a bare string or number apart from text."""
    return _parse_llm(text, object)


# ── Conversation History Helpers ──


def save_conversation_turn(
    log: "ConversationLog",
    key: str,
    user_text: str,
    assistant_text: str,
    source_thread: str | None = None,
    source_user: str | None = None,
    source_channel: str | None = None,
) -> None:
    """Save a user+assistant conversation turn to the history log, with its provenance, and to
    the chat the dashboard has open for the conversation, if it has one.

    A channel that runs a conversation itself records its turns here: the thread, who sent the
    message (*source_user*) and the channel itself (*source_channel*, its provider key). Memory
    takes a line as the owner's words only when its sender is the owner that channel keeps
    (``turn_source.sent_by_owner``), so a turn saved without its channel is nobody's. The
    dashboard's chat for that conversation rewrites the whole file from what it holds, so it is
    given the turn too (``DashboardState.take_channel_turn``), with the same provenance: left out,
    the chat showed the conversation as it was when it was opened, and its next save wrote that
    over every turn since.
    """
    log.append(
        key,
        "user",
        user_text,
        source_thread=source_thread,
        source_user=source_user,
        source_channel=source_channel,
    )
    if assistant_text:
        log.append(
            key,
            "assistant",
            assistant_text,
            source_thread=source_thread,
            source_user=source_user,
            source_channel=source_channel,
        )
    from personalclaw.inbox_providers.native_source import get_dashboard_state
    from personalclaw.turn_source import arrived_on

    state = get_dashboard_state()
    if state is None:
        return
    try:
        state.take_channel_turn(
            log,
            key,
            user_text,
            assistant_text,
            arrived_on(source_thread, source_user, source_channel),
        )
    except Exception:  # noqa: BLE001 - the turn is written; the channel's reply must not fail
        logger.warning("could not give the open chat for %s its turn", key, exc_info=True)


def _enforces_json_schema_natively(model_ref: str) -> bool:
    """Does the provider behind ``model_ref`` enforce a supplied JSON Schema NATIVELY?

    The gate on forwarding ``output_type`` as a build kwarg (AUTONOMY-GUARDRAILS §2.4,
    AG-9). It asks a narrow question about ONE entry and answers ``False`` to every
    doubt, because a false positive here does not degrade to a weaker constraint — it
    corrupts the request. Build kwargs land in the provider's ``extra_options``, and both
    wire clients copy that bag onto the request body (``llm/openai.py`` assigns
    unconditionally, ``llm/anthropic.py`` ``setdefault``s). A provider that does not POP
    ``output_type`` therefore hands a Python ``type`` to the JSON encoder and dies with an
    opaque ``TypeError`` from the HTTP client — a call that works today would start
    failing. So only a provider that ADVERTISED the capability is sent the key; the ollama
    app pops it (``resolve_output_format``) and normalizes it into ollama's own ``format``.

    ``JSON_SCHEMA`` ONLY. ``JSON_MODE`` is deliberately excluded, and the comparison below
    names the grade rather than spelling it ``!= NONE`` so the code states that decision.
    A ``JSON_MODE`` provider speaks OpenAI-wire ``response_format={"type":
    "json_object"}`` — a DIFFERENT request field that no adapter derives from
    ``output_type``, so the key would ride through unconsumed (the corruption above)
    instead of being translated. It also cannot express ``output_type=list`` at all
    (``json_object`` mode requires an object), so for half of this function's real inputs
    the weaker grade is not weaker, it is wrong. Reaching ``JSON_MODE`` is genuine work —
    its own ``response_format`` build kwarg plus an adapter that consumes it — not a
    loosened comparison here.

    Contrast ``workflows/grounding.py``, which asks ``!= NONE``: that answers a different
    question — "does schema-constrained emission exist ANYWHERE in this install" — for
    which ``JSON_MODE`` legitimately counts.

    The comparison is ``==`` rather than ``is`` because :class:`StructuredOutput` is a
    ``str`` enum: that accepts both the enum member and its own ``"json_schema"`` spelling
    (an app declaring the grade as a bare string still gets its capability honoured) and
    nothing else — no other grade or value compares equal, so the acceptance set widens
    without admitting a false positive.

    Everything unverifiable degrades to ``False``: an unqualified ref (a bare model id,
    including the ``"gpt-oss:20b"`` shape whose colon is not a provider prefix — the
    ``get_entry`` lookup rejects it exactly as the bridge does), the legacy
    ``"Provider/model"`` slash spelling, an unregistered provider type, an unbootstrapped
    registry, or ``""`` (the unbound-axis plain path, where the implicit fallback has not
    picked a provider yet). In every one of those cases core sends no constraint and the
    universal parse-with-targeted-retry net in ``_run`` owns the contract — byte-for-byte
    today's behaviour.
    """
    try:
        from personalclaw.llm.capabilities import StructuredOutput
        from personalclaw.llm.registry import get_default_registry
        from personalclaw.providers.use_cases import split_ref

        parsed = split_ref(model_ref)
        if not parsed:
            return False
        registry = get_default_registry()
        # get_entry raises for a name that is not a registered entry, which is exactly
        # how the bridge distinguishes a "Provider:model" ref from a bare id whose model
        # name happens to contain a colon. Same rule, same outcome.
        cap = registry.capability_of(registry.get_entry(parsed[0]).type)
        grade = getattr(cap, "structured_output", StructuredOutput.NONE)
        return grade == StructuredOutput.JSON_SCHEMA
    except Exception:  # noqa: BLE001 — an unverifiable capability is NOT a capability
        logger.debug(
            "one_shot_completion: no native schema enforcement resolvable for %r", model_ref
        )
        return False


# ── Call-failure chain advance ───────────────────────────────────────────────
# ONE walk, shared by every NON-INTERACTIVE consumer of the use-case chain. It lives
# here because ``one_shot_completion`` was the first consumer, not because it is the
# only one: the other direct consumers of a use case's model on the non-interactive
# axes (the knowledge-pipeline nodes, the loop stage-gate judge) need
# exactly this walk, and a second hand-rolled copy of it would be a divergence defect
# — two answers to "should we try the next model" drifting apart.
#
# The INTERACTIVE chat/code_tools stream is deliberately NOT a consumer of this walk: its
# provider is a NativeAgentRuntime holding per-turn tool/transcript state that cannot be
# rebuilt mid-stream. It advances at call-start via the seam's resolution-time chain walk
# (the breaker-OPEN skip in ``provider_bridge.resolve_provider_for_use_case``), and at call
# time by swapping only the runtime's INNER model (``agents/native/failover.py``): once, per
# later model, while nothing of the turn has been shown, and said on the turn.


class ChainExhausted(RuntimeError):
    """Every model of a use case's chain failed for one call (:func:`run_over_use_case_chain`).

    A ``RuntimeError``, so every caller that already catches one keeps working, with the
    message it always had. ``failures`` is what each entry did, in order — ``(ref,
    exception)`` — so a caller can say what happened ("each timed out") instead of reading one
    last error as the whole story.
    """

    def __init__(self, message: str, failures: list[tuple[str, BaseException]]) -> None:
        super().__init__(message)
        self.failures = list(failures)


def why_no_model_answered(exc: BaseException) -> str:
    """Why a call got no answer, as a person reads it: what each model of an exhausted chain did
    ("here:tiny failed before it replied (it answered with nothing), and so did …"), else the
    failure's own sentence with its kind."""
    failures = getattr(exc, "failures", None)
    if isinstance(exc, ChainExhausted) and failures:
        from personalclaw.guardrails.failure import failed_before_replying
        from personalclaw.providers.provider_bridge import substitution_reason

        clauses = [(ref, substitution_reason(err)[0]) for ref, err in failures]
        return f"{failures[0][0]} " + failed_before_replying(clauses).removeprefix("it ")
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}"[:300] if text else type(exc).__name__


def is_timeout_failure(exc: BaseException) -> bool:
    """Whether *exc* is a model that did not answer in time, rather than one that failed or
    was not there: a timeout of the call, the guard's, a first-token one, or the HTTP
    client's — directly or as the cause of a wrapper."""
    import httpx

    from personalclaw.guardrails.failure import FailureMode

    seen: BaseException | None = exc
    for _ in range(5):
        if seen is None:
            return False
        if isinstance(seen, (TimeoutError, httpx.TimeoutException)):
            return True
        if getattr(seen, "mode", None) == FailureMode.TIMEOUT:
            return True
        seen = seen.__cause__
    return False


def use_case_chain(use_case: str) -> list[str]:
    """The ordered resolution chain for ``use_case``, or ``[]`` when unreadable.

    A tolerant read of :func:`personalclaw.providers.use_cases.resolution_chain` that
    NEVER raises: a missing or corrupt ``active_models.json`` must leave the caller on
    its plain single-resolution path, not fail the completion.

    Callers gate on ``len(chain) > 1`` themselves rather than handing a one-entry chain
    to :func:`run_over_use_case_chain`, and that is deliberate: "a one-entry chain takes
    today's plain path" is a rule about WHICH resolution call is made. The walk passes
    ``model_override=<ref>``; a plain resolve passes none, which additionally admits the
    implicit-capability fallback. Collapsing the two would silently change single-binding
    behaviour.

    The composer/session override is deliberately NOT plumbed through here. Every
    consumer of this walk is NON-INTERACTIVE (one-shot completions, the knowledge
    pipeline nodes, the loop stage-gate judge), and a session override is a property
    of an interactive composer turn — there is no session to override on these axes.
    Forwarding it would also widen ``resolution_chain``'s call shape for no caller.
    """
    try:
        from personalclaw.providers.use_cases import resolution_chain

        return list(resolution_chain(use_case))
    except Exception:  # noqa: BLE001 — an unreadable chain degrades to the plain path
        logger.debug("use_case_chain: chain unreadable for %r", use_case, exc_info=True)
        return []


async def run_over_use_case_chain(
    use_case: str,
    chain: list[str],
    run: Callable[[Any], Awaitable[_ChainResult]],
    *,
    entry_kwargs: Callable[[str], Awaitable[dict]] | None = None,
    label: str = "chain",
) -> _ChainResult:
    """Run ``run`` against ``chain`` entry 0, advancing to N+1 on a call failure.

    The call-failure half of the fallback chain (MODEL-USE-CASES-V2 T2.4), complementing
    the seam's resolution-time breaker skip: a ``CircuitOpenError``/provider failure from
    entry N rebuilds from entry N+1 — once per remaining entry, bounded by chain length —
    so a declared fallback actually serves a call that a live provider dropped mid-flight
    rather than only one that was already known-down at resolution time.

    ``run`` receives one resolved provider and owns its whole lifecycle (start / stream /
    shutdown); it must RAISE on failure, because a swallowed error is indistinguishable
    from a good answer and would pin the walk to entry 0 forever. An answer that is empty text
    is a failure too (:class:`~personalclaw.guardrails.failure.EmptyCompletion`): a model that
    spent its output budget thinking and said nothing has not answered, and the next model may.

    ``entry_kwargs`` derives the per-ENTRY build kwargs (budget/constraint) for the ref
    about to run — per entry, not once, because the walk can advance from a capable model
    to an incapable one mid-call.

    An answer that missed its requested shape (``OutputContractError``) advances as well: the
    model answered, but not usably, and a measured miss is a model too small for the shape far
    more often than a prompt no model could follow.

    An exhausted chain raises ONE :class:`ChainExhausted` (a ``RuntimeError``) naming the
    axis, the chain length and the last error — one clear error, not N stack traces — and
    carrying what each entry did. When the last entry answered in the wrong shape, its
    ``OutputContractError`` is raised instead, so a caller can still read the text it returned.

    An entry that serves after the head failed serves IN ITS PLACE, and the provider carries
    that (``provider_bridge.stamp_substitution``), so the call the guard records says "ran on
    <entry> instead of <head>". The fallback is the user's own configuration and it still
    serves; what changed is that a step and Introspect no longer name the entry that answered
    as if it were the one asked for.

    Every entry resolves metered (``provider_bridge.resolve_metered_model``): each consumer of
    this walk is automation, whichever axis it walks, so each call counts against the daily cap.

    Each attempt names the entry after it (``local_queue.next_entry``): a call somebody is waiting
    for gives a busy local model a short wait only when there is a next model to answer instead.
    """
    from personalclaw.guardrails.failure import EmptyCompletion, OutputContractError
    from personalclaw.guardrails.local_queue import next_entry
    from personalclaw.providers.provider_bridge import (
        resolve_metered_model,
        stamp_substitution,
        substitution_reason,
    )

    last_exc: Exception | None = None
    failures: list[tuple[str, BaseException]] = []
    head_failure: tuple[str, str] | None = None  # (why, fix) of the head that did not serve
    for i, ref in enumerate(chain):
        try:
            kw = await entry_kwargs(ref) if entry_kwargs is not None else {}
            provider = resolve_metered_model(use_case, model_override=ref, **kw)
        except Exception as exc:  # noqa: BLE001 — an unbuildable entry advances
            last_exc = exc
            failures.append((ref, exc))
            if i == 0:
                head_failure = substitution_reason(exc)
            continue
        if i > 0 and head_failure is not None:
            why, fix = head_failure
            stamp_substitution(
                provider,
                ModelSubstitution(
                    requested=chain[0],
                    served=str(getattr(provider, "served_ref", "") or ref),
                    why=why,
                    fix=fix,
                ),
            )
        try:
            # What this attempt moves on to if it does not serve: a call somebody is waiting for
            # gives a busy local model a short wait only when there is somewhere to go.
            with next_entry(chain[i + 1] if i + 1 < len(chain) else ""):
                result = await run(provider)
            if isinstance(result, str) and not result.strip():
                raise EmptyCompletion(ref)
            return result
        except Exception as exc:  # noqa: BLE001 — a failed call advances
            last_exc = exc
            failures.append((ref, exc))
            if i == 0:
                head_failure = substitution_reason(exc)
            if i + 1 < len(chain):
                logger.warning(
                    "%s advance: %s entry %d (%s) failed (%s) — trying next",
                    label,
                    use_case,
                    i,
                    ref,
                    type(exc).__name__,
                )
    if isinstance(last_exc, OutputContractError):
        raise last_exc
    raise ChainExhausted(
        f"every model in the {use_case!r} fallback chain failed "
        f"({len(chain)} entr{'y' if len(chain) == 1 else 'ies'}); "
        f"last error: {last_exc}",
        failures,
    ) from last_exc


#: What the one-shot calls of a block expect their answer to be (:func:`expecting`).
_EXPECTED_SHAPE: contextvars.ContextVar[Callable[[str], str] | None] = contextvars.ContextVar(
    "personalclaw_one_shot_expected_shape", default=None
)


@contextlib.contextmanager
def expecting(check: Callable[[str], str]) -> Iterator[None]:
    """The one-shot calls in this block need answers *check* accepts: ``validate=`` for a call
    made through a completion function the caller was handed (a classifier's ``ask``, a triage
    run's ``completion``), whose signature it does not own. *check* gets the answer's text and
    returns ``""`` when it can be used, else what is wrong with it."""
    token = _EXPECTED_SHAPE.set(check)
    try:
        yield
    finally:
        _EXPECTED_SHAPE.reset(token)


async def one_shot_completion(
    prompt: str,
    *,
    use_case: str = "background",
    output_type: type | None = None,
    model: str = "",
    temperature: float | None = None,
    usage: "Attribution | None" = None,
    attempt_timeout: float | None = None,
    attended: "Attended | None" = None,
    validate: Callable[[str], str] | None = None,
    max_output_tokens: int | None = None,
) -> str:
    """Send a single prompt to the system's configured LLM and return the response.

    The answer is text alone: the model is offered no tools, it is never an agent CLI (a whole
    agent with tools of its own, which the resolution below never builds), and a model that asks
    for a tool anyway has every call it asks about refused.

    Resolves the provider through the same use-case bridge the chat path uses —
    which reads the active model selection from ``active_models.json`` (Settings →
    Models) — then builds a temporary instance, streams the response, and returns
    the collected text. Every resolution path (the pin, the chain and the plain resolve) goes
    through the one seam (``provider_bridge.resolve_metered_model``), which wraps the model in
    the model-call guard (circuit breaker + hard timeout + attempt audit + the spend budgets)
    and never builds an agent CLI: a call asks a model for a text answer. With no model chosen
    for the use case the bridge's refusal is raised, and nothing is built.

    ``use_case`` names a chat sub-category axis (MODEL-USE-CASES-V2):
    ``"background"`` IS a real axis now (titles/tags/suggestions/digests/
    consolidation route through it, falling back to the active ``chat`` chain when
    unbound), as are ``"reasoning"``, ``"loops"``, and ``"orchestration"``. The
    remaining informal label ``"ingestion"`` collapses to ``"background"``; anything
    unrecognized collapses to ``"reasoning"``. ``chat``/``code_tools`` are never
    used here: they are the axes a person's own turns run on, and a one-shot call
    is not one.

    On a chain with fallbacks, a ``CircuitOpenError``/provider failure from entry N
    advances to entry N+1 for this call (bounded by chain length) — the
    call-failure walk that complements the seam's resolution-time breaker skip. An answer
    with no text is such a failure on every path: it raises
    :class:`~personalclaw.guardrails.failure.EmptyCompletion`, so a chain moves on and a
    caller never has to tell an empty string from a failure.

    ``output_type`` (AUTONOMY-GUARDRAILS §2.4) opts into typed structured output:
    pass ``dict`` or ``list`` to require the response parse as that JSON shape.
    ``validate`` checks the shape the caller needs inside it: it is given the answer's text and
    returns ``""`` when the caller can use it, else what is wrong with it ("no 'proposals'
    array"). An answer that misses either is the model failing this call, so the chain asks its
    next model; the last model (or the only one) is asked ONCE more with a targeted correction
    note naming what was wrong (the dominant real-world cause is the schema not being visible),
    and if that misses too an :class:`~personalclaw.guardrails.failure.OutputContractError` is
    raised, carrying the last answer's text for a caller that salvages it. A caller therefore
    never checks the shape after the fact to decide whether another model should have been
    asked. A caller that calls through a completion function it was handed binds the same check
    around the call instead (:func:`expecting`). The response is always returned as text, and a
    caller reads its JSON with :func:`parse_llm_json` / :func:`parse_llm_json_list` /
    :func:`parse_llm_json_value`: the reading this call's ``output_type`` check uses, so the answer
    the call accepted is the one its caller reads, and a ``validate`` check reads it the same way.

    ``output_type`` ALSO rides the bridge as a build kwarg (AG-9) — but only to an entry
    whose provider advertised ``StructuredOutput.JSON_SCHEMA``, so a natively capable
    provider CONSTRAINS generation instead of merely being asked nicely in prose. The
    ollama app is the first consumer: it pops the key and normalizes ``dict``/``list``
    into ollama's own ``format=<schema>``. The gate is per ENTRY, not per call, because a
    chain can advance from a capable model to an incapable one mid-call; a provider that
    never advertised the capability is sent NOTHING, because build kwargs reach the
    request body and an unconsumed key there is a corrupt request, not a soft no-op.
    ``JSON_MODE`` is deliberately excluded — see :func:`_enforces_json_schema_natively`.
    The native constraint LAYERS with the parse-and-retry above rather than replacing it:
    constrained decoding can still return a valid-but-empty document, so dropping the
    parse would turn a caught failure into a silent one.

    ``model`` PINS resolution to one concrete model (a ``"Provider:model_id"`` ref,
    or a bare id), bypassing the use case's active-selection CHAIN — a pin is not a
    chain. This is the seam cross-model judge isolation needs (WF2LOO-11): the engine
    resolves a different-FAMILY judge model up front, validates it against the
    producing stage's model, and pins it here so the judge provably runs on the model
    it was checked against. The default ``""`` keeps today's use-case-only resolution
    byte-for-byte.

    ``temperature`` pins the SAMPLING temperature for this one call (HARNESS-CRAFT
    §2.1): it rides the bridge as a build kwarg into the provider's ``extra_options``,
    where both protocol clients already forward call params into the request. This is
    what makes best-of-N sampling genuinely varied rather than N identical calls. Two
    honest caveats: an Anthropic model in extended-thinking mode forbids a custom
    temperature and drops it (``llm/anthropic.py``), and a provider that ignores the
    parameter simply returns its default — a temperature is a request, not a promise.
    ``None`` (the default) sends nothing, keeping every existing call site unchanged.

    The per-call OUTPUT BUDGET (LMMV §2.2) is derived, never hardcoded: every resolution
    path (pin / chain-advance / plain) asks
    :func:`personalclaw.local_models.budgets.output_budget` for the model it is about to
    run and rides the answer as a ``max_tokens`` build kwarg. A local model whose card
    declares ``context_tokens``/``output_tokens`` therefore gets ITS window instead of the
    hosted-model table's 200k default and the adapters' hardcoded 4096; a model the
    catalog does not know falls back to that table and, only then, to 4096 — the same
    number as before, now named once in ``local_models/budgets.py``. Because the bridge
    merges build kwargs with ``setdefault``, an operator's configured ``max_tokens`` still
    wins. No compaction logic is involved: this makes the number available, it does not
    decide what to drop. ``max_output_tokens`` is a ceiling on that budget for this one call:
    each model is given the smaller of the two, so a caller that bounds what its answer may
    run to (a chore, ``chores.run_chore``) bounds it on every model the chain walks.

    Every model call it makes writes one usage-ledger row, through the seam every turn's row
    takes (``stream_and_collect(on_complete=…)``), priced by the model the resolved provider was
    built for. ``usage`` names whose spend it is (:class:`~personalclaw.usage_ledger.Attribution`):
    the source, and the session and agent it was made for. A caller that names none is recorded
    as unattended background spend (:data:`~personalclaw.usage_ledger.UNATTENDED`), which every
    call here is: an interactive turn never comes through this function. A call used to write no
    row unless its caller asked, and each of those calls was spend Settings → Usage could not show.

    ``attempt_timeout`` bounds each MODEL's attempt, not the call: a model that has not answered
    within it counts as failed and the chain moves to its next entry, exactly as it does for one
    that errored. A caller that wrapped the whole call in one timeout instead cut the chain off
    while its second model was still answering, so a slow first model was the end of the call
    however the chain went on — which is how a busy local model left library items reading
    "model unavailable" while two more models were bound behind it. ``None`` leaves each attempt
    to the model-call guard's own limit. Either limit includes the time a call waits for its turn
    on a local model (``guardrails.local_queue``).

    ``attended`` says somebody is waiting for this call — a chat tool's step, a page waiting on
    its answer — and what the wait is (:class:`~personalclaw.guardrails.local_queue.Attended`). On
    a model that runs on this machine it is given the model before any background call, it waits
    for a busy one only briefly when its chain has another model to try, and while it waits the
    page can say why. ``None`` is background work: it waits its turn behind them.
    """
    from personalclaw.guardrails.local_queue import attending, next_entry

    # The shape this call needs is its own (the argument, else the block's), and no call it
    # makes on its way inherits it.
    check = validate or _EXPECTED_SHAPE.get()
    shape_token = _EXPECTED_SHAPE.set(None)
    try:
        # Bound here, inside the coroutine, so a sync bridge that runs it on a thread of its
        # own still carries it to the guard. What it moves on to is its own chain's to say, so
        # nothing a caller's walk bound reaches it.
        with attending(attended), next_entry(""):
            return await _one_shot_completion(
                prompt,
                use_case=use_case,
                output_type=output_type,
                model=model,
                temperature=temperature,
                usage=usage,
                attempt_timeout=attempt_timeout,
                validate=check,
                max_output_tokens=max_output_tokens,
            )
    finally:
        _EXPECTED_SHAPE.reset(shape_token)


async def _one_shot_completion(
    prompt: str,
    *,
    use_case: str,
    output_type: type | None,
    model: str,
    temperature: float | None,
    usage: "Attribution | None",
    attempt_timeout: float | None,
    validate: Callable[[str], str] | None,
    max_output_tokens: int | None,
) -> str:
    """:func:`one_shot_completion`, with whoever is waiting for it already bound."""
    from personalclaw.providers.provider_bridge import resolve_metered_model
    from personalclaw.providers.use_cases import VALID_USE_CASES
    from personalclaw.usage_ledger import UNATTENDED, recorder

    who = usage or UNATTENDED

    # Honor a caller that already named a real model-axis use case; the remaining
    # informal label ("ingestion") collapses to the background axis; anything
    # unrecognized collapses to reasoning (→ chat fallback either way).
    if use_case in VALID_USE_CASES and use_case not in ("chat", "code_tools"):
        resolved_uc = use_case
    elif use_case == "ingestion":
        resolved_uc = "background"
    else:
        resolved_uc = "reasoning"

    from personalclaw.guardrails.failure import EmptyCompletion, OutputContractError

    # A pinned sampling temperature rides EVERY resolution path as a build kwarg
    # (pin / chain-advance / plain), so a fallback entry samples at the
    # temperature the caller asked for rather than silently reverting to the provider
    # default.
    _bridge_kw: dict = {} if temperature is None else {"temperature": float(temperature)}

    async def _entry_kw(model_ref: str) -> dict:
        """The build kwargs for the ONE entry ``model_ref`` names: budget + constraint.

        ``max_tokens`` is the per-model output budget DERIVED for ``model_ref``. The
        number comes from the local-model catalog's ``context_tokens`` /
        ``output_tokens`` (LMMV §2.2) via ``local_models.budgets``, which falls back to
        the shared hosted-model window table and, only when that too is silent, to the
        4096 the adapters used to hardcode. Riding it as a build kwarg means it reaches
        the provider through the same seam ``temperature`` uses — and because the bridge
        merges build kwargs with ``setdefault``, an operator's explicitly configured
        ``max_tokens`` still wins over the derived one.

        Fail-soft: an unresolvable budget must not be the thing that stops a completion,
        so the call site degrades to "pass nothing" (the adapter default) rather than
        raising.

        ``output_type`` rides here too (AG-9), and ONLY when this entry's provider
        advertised ``StructuredOutput.JSON_SCHEMA`` — see
        :func:`_enforces_json_schema_natively` for why an unadvertised provider must not
        receive the key. PER ENTRY is the whole point of deciding here rather than once
        up front: every resolution path (pin / chain advance / plain) funnels
        through this function WITH the ref it is about to run, and the chain walk can advance
        from a capable entry to an incapable one mid-call. A decision made once would send
        the constraint to a fallback provider that cannot honour it — precisely the
        corrupt-request failure the capability gate exists to prevent.

        The constraint is a REQUEST, never a promise, so it does not replace anything: the
        parse-with-targeted-retry net in ``_run`` still checks every response. Constrained
        decoding is measured in this repo to return valid-but-empty documents, so dropping
        the parse because "the provider guarantees it" would convert a caught failure into
        a silent one. Both layers stay.

        ``output_type=None`` (the overwhelmingly common case) short-circuits before the
        capability lookup, so the kwargs are byte-for-byte what they were and no registry
        work happens on that path.
        """
        kw = dict(_bridge_kw)
        if output_type is not None and _enforces_json_schema_natively(model_ref):
            kw["output_type"] = output_type
        try:
            from personalclaw.local_models.budgets import output_budget

            kw["max_tokens"] = await output_budget(model_ref)
        except Exception:  # noqa: BLE001 — a budget miss degrades, it never blocks
            logger.debug("one_shot_completion: budget derivation failed for %r", model_ref)
        if max_output_tokens is not None:
            # The caller's ceiling: the model's own budget when that is smaller, else the ceiling,
            # and the ceiling alone when no budget could be derived.
            kw["max_tokens"] = min(
                int(kw.get("max_tokens") or max_output_tokens), max_output_tokens
            )
        return kw

    expected = (
        getattr(output_type, "__name__", str(output_type))
        if output_type is not None
        else "the shape asked for"
    )

    def _miss(text: str) -> str:
        """What makes *text* unusable to the caller, ``""`` when nothing does."""
        if output_type is not None and _parse_llm(text, output_type) is None:
            return f"it did not parse as {expected}"
        if validate is None:
            return ""
        try:
            return str(validate(text) or "")
        except Exception as exc:  # noqa: BLE001 — an answer its reader fails on is unusable
            return f"it could not be read ({type(exc).__name__})"

    async def _ask(provider, text: str, on_complete: Callable[[LLMEvent], None]) -> str:
        """*text*'s answer from *provider*, in text alone. A one-shot call offers its model no
        tools: it answers from what its prompt carries, and that prompt can quote text nobody
        vetted. So a model that asks for a tool anyway has every call it asks about refused, with
        nobody asked; and no agent CLI, which brings tools of its own, is ever the model here."""
        return await stream_and_collect(
            provider, text, approval_policy=ToolApprovalPolicy.REJECT_ALL, on_complete=on_complete
        )

    async def _run(provider) -> str:
        from personalclaw.guardrails.local_queue import moving_on_to

        try:
            await provider.start()
            on_complete = recorder(provider, who)
            served = str(getattr(provider, "served_ref", "") or "")
            text = await _ask(provider, prompt, on_complete)
            # No text is no answer, on every path: the chain moves on, and a caller with one
            # model sees a failure rather than an empty string it would have to tell apart.
            if not text.strip():
                raise EmptyCompletion(served)
            miss = _miss(text)
            if not miss:
                return text
            # The wrong shape. With another model to ask, that model is asked rather than this
            # one again: a model that missed once is the likelier one to miss twice, and on a
            # slow local model a second try costs as long as the first.
            if moving_on_to():
                raise OutputContractError(expected, text, why=miss)
            from personalclaw.guardrails.failure import FailureMode, correction_note

            note = correction_note(FailureMode.SCHEMA_VIOLATION)
            retry_prompt = f"{prompt}\n\n{note} What was wrong: {miss}."
            retry_text = await _ask(provider, retry_prompt, on_complete)
            if not retry_text.strip():
                raise EmptyCompletion(served)
            miss = _miss(retry_text)
            if not miss:
                return retry_text
            raise OutputContractError(expected, retry_text, why=miss)
        finally:
            try:
                await provider.shutdown()
            except Exception:
                pass

    async def _attempt(provider) -> str:
        """One model's attempt, bounded by ``attempt_timeout`` when there is one. Its
        ``TimeoutError`` is a failed call like any other, so the chain walk moves on."""
        if attempt_timeout is None:
            return await _run(provider)
        return await asyncio.wait_for(_run(provider), float(attempt_timeout))

    # Work that derives from an Incognito or Temporary chat hands nothing to a model but the one
    # the chat's turn runs on (`memory_writes.model_may_read`): a call that names no model runs
    # on that one, stamped as serving in the bound model's place; one before the turn named its
    # model, pinned to another, or in the work of a chat on an agent CLI (which no call runs on),
    # is refused by the guard every model built here passes.
    own = memory_writes.own_model()
    stays_on_own = (
        own is not None and not model and bool(own) and not memory_writes.is_agent_cli(own)
    )
    if stays_on_own:
        model = str(own)

    # A pinned model bypasses the active-selection chain entirely — a pin is not a
    # chain. The caller has already decided WHICH model must run (a
    # cross-model judge validated against the worker's family), so walking the
    # use-case fallback chain would defeat the pin: a fallback entry could be the
    # very family the isolation control excluded. Resolve the one model and run it.
    if model:
        pinned = resolve_metered_model(
            resolved_uc, model_override=model, **(await _entry_kw(model))
        )
        if stays_on_own:
            _stamp_own_model(pinned, resolved_uc, model)
        return await _attempt(pinned)

    # Call-failure chain advance: with a multi-entry chain declared, a failure from entry N
    # (a provider error, an open breaker, a busy local model, an empty answer or one in the
    # wrong shape) advances to entry N+1 for THIS call — once per remaining entry, bounded by
    # chain length. A one-entry/empty chain takes the plain resolution path below. The walk
    # itself is :func:`run_over_use_case_chain`, shared with the other non-interactive
    # consumers of the chain so there is exactly one answer to "advance?".
    _chain = use_case_chain(resolved_uc)
    if len(_chain) > 1:
        return await run_over_use_case_chain(
            resolved_uc,
            _chain,
            _attempt,
            entry_kwargs=_entry_kw,
            label="one_shot chain",
        )

    # A one-entry or empty chain: the bridge resolves the axis itself, by the one rule every call
    # automation makes is resolved by (``provider_bridge.resolve_metered_model``) — the model the
    # use case is bound to, else a configured model that names one of its own, and never an agent
    # CLI, which is a whole agent with tools of its own and no model to ask for a text answer. Its
    # refusal is the answer, typed and saying which use case wants which model and where it is
    # chosen. This call used to build "the first registered provider" when the bridge refused, and
    # on a home whose only runtime is an agent CLI that was the CLI: every chore started it, and
    # its requests to run commands were approved with nobody asked.
    #
    # The budget is derived from the model the axis will actually run (``_chain`` holds it when
    # there is one) rather than from nothing — an unbound axis falls back to the window table.
    _plain_ref = _chain[0] if _chain else ""
    provider = resolve_metered_model(resolved_uc, **(await _entry_kw(_plain_ref)))
    return await _attempt(provider)


def _stamp_own_model(provider: object, use_case: str, own: str) -> None:
    """Say on every call *provider* makes that it serves in *use_case*'s bound model's place, on
    the chat's own model *own*, because the work is an Incognito or Temporary chat's — in the
    words every substitution uses ("ran on <own> instead of <bound>: this chat is Incognito, …").
    Nothing to say when *use_case* is bound to *own* itself, or to nothing."""
    from personalclaw.providers.provider_bridge import stamp_substitution

    chain = use_case_chain(use_case)
    requested = chain[0] if chain else ""
    if requested and requested != own:
        stamp_substitution(
            provider,
            ModelSubstitution(
                requested=requested, served=own, why=memory_writes.own_model_reason()
            ),
        )


def failed_endpoint(exc: BaseException) -> str:
    """``" at <host:port>"`` for a transport error that knows its request, else ``""``.

    Only the host and port — never the path or query, which is where a provider that takes
    its key in the URL would carry it. httpx attaches the request to the errors it raises
    while sending; its ``request`` property raises instead of returning ``None`` when it was
    never attached, so the absence is caught rather than tested.
    """
    try:
        url = exc.request.url  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        return ""
    host = str(getattr(url, "host", "") or "")
    if not host:
        return ""
    if ":" in host:
        host = f"[{host}]"
    port = getattr(url, "port", None)
    return f" at {host}:{port}" if port else f" at {host}"


def is_model_call_failure(exc: BaseException) -> bool:
    """Whether *exc* is a model call failing, rather than a fault in the code around it.

    A failure the guard classified (a :class:`GuardError`), a provider's refusal or a broken
    transport to it (``httpx.HTTPError``, a timeout, a dropped connection), or a provider that
    cannot be resolved — directly or as the cause of a wrapper. These are the conditions a log
    reports as one line with their sentence: the traceback of a model that did not answer shows
    only the HTTP client's frames. Anything else is a defect, and keeps its traceback.
    """
    import httpx

    from personalclaw.guardrails.failure import GuardError
    from personalclaw.llm.registry import ProviderResolutionError
    from personalclaw.providers.provider_bridge import ProviderResolutionError as UseCaseUnresolved

    # Both refusals are "cannot be resolved": the registry's (an entry or type it cannot build)
    # and the use-case resolver's, which carries the coded envelope. Only the first was named, so
    # a home with no model bound logged the resolver's refusal as a defect, traceback and all.
    kinds = (
        GuardError,
        ProviderResolutionError,
        UseCaseUnresolved,
        httpx.HTTPError,
        TimeoutError,
        ConnectionError,
    )
    seen: BaseException | None = exc
    for _ in range(5):
        if seen is None:
            return False
        if isinstance(seen, kinds):
            return True
        seen = seen.__cause__
    return False


def _describe_unexplained_failure(exc: object) -> str:
    """The sentence for a failure whose ``str()`` is empty — never an empty string.

    ``httpx.ReadError``, every httpx timeout, ``asyncio.TimeoutError`` and a bare
    ``ConnectionResetError`` all stringify to ``""``, and a turn error is SHOWN as its text:
    an empty string rendered as an error bar with nothing in it, over the WebSocket and on
    disk alike. So the class — and, for httpx, the endpoint — has to say what the message
    did not. A wrapper raised ``from`` a transport error is described by that cause, since the
    cause is what actually failed.
    """
    if exc is None:
        return (
            "The turn failed without reporting an error. Try again; if it keeps failing, "
            "check the gateway log."
        )
    transport = _transport_failure_sentence(exc)
    if transport is not None:
        return transport
    return (
        f"The turn failed with {type(exc).__name__}, and the error carried no message. "
        "Try again; if it keeps failing, check the gateway log."
    )


def _transport_failure_sentence(exc: object) -> str | None:
    """The sentence for a model call whose connection failed or timed out, or ``None``.

    Decided by the failure's TYPE, or its cause's, never by its words: a timeout is a timeout
    whether its message is empty (every httpx timeout) or says so ("Request timed out." from a
    provider SDK that wraps the httpx one), and read by its words a timeout that said so was
    "an error PersonalClaw doesn't recognize".
    """
    import httpx

    seen: BaseException | None = exc if isinstance(exc, BaseException) else None
    for _ in range(5):
        if seen is None:
            break
        where = failed_endpoint(seen)
        if isinstance(seen, httpx.ConnectTimeout):
            return (
                f"Timed out connecting to the model provider{where}. Check that it is "
                "running and reachable, then try again."
            )
        if isinstance(seen, (httpx.TimeoutException, TimeoutError)):
            return (
                f"The model provider{where} did not answer in time, so the request timed "
                "out. Wait a moment and try again, or pick a different model."
            )
        if isinstance(seen, (httpx.ConnectError, ConnectionRefusedError)):
            return (
                f"Couldn't connect to the model provider{where}. Check that it is running "
                "and reachable, then try again."
            )
        if isinstance(
            seen, (httpx.NetworkError, httpx.RemoteProtocolError, ConnectionError, EOFError)
        ):
            return (
                f"The connection to the model provider{where} was lost before its reply was "
                "complete. Check that it is still running and reachable, then try again."
            )
        seen = seen.__cause__
    return None


#: The most of a failure's own words the sentence for an unrecognized failure carries.
_OWN_WORDS_CAP = 500


def humanize_provider_error(exc: object, *, room_member: str = "") -> str:
    """Turn a raw LLM-provider exception into a short, actionable user-facing line.

    Providers (Anthropic/OpenAI/…-compatible) surface failures as verbose SDK
    exceptions whose ``str()`` is a JSON-ish blob (e.g. ``Error code: 400 - {'type':
    'error', 'error': {'message': 'Your credit balance is too low…'}}``). Shown raw
    in the chat error bubble that's noise, not guidance. Map the common, recognizable
    classes — billing/credits, auth, rate-limit, model-not-found, overload — to a
    concise hint. Pure string heuristics (provider SDKs don't share a typed error
    taxonomy), matched on the lowercased message.

    **An unrecognized failure is never shown bare, and never hidden.** Its own words are an
    SDK's, and they name no next step: relayed as the whole message, one terse SDK line or a
    JSON dump was all a user had to go on. So it is said as a failure PersonalClaw does not
    recognize, with the one step that holds for any failure, and its own words (trimmed) come
    after that as the detail. A model app that knows the fix raises its own sentence instead
    (the SDK's ``ProviderResolutionError``, below).

    Never returns an empty string: an exception with no message is described from its
    class instead (:func:`_describe_unexplained_failure`).

    These classes are answered BEFORE the matcher, because the matcher would get them wrong:

    * A failure carrying a coded envelope (an ``agent_error`` whose code is registered in
      ``errors.ERROR_CODES``) already knows what failed, why, and the fix — the model resolver's
      refusal on a home with no model bound is one — so it is said as that envelope's
      :meth:`~personalclaw.errors.AgentError.sentence`. Read as unrecognized, it told a person
      with no model bound to try again.
    * ``PromptExceedsWindow`` is already the user-facing sentence (model, limit, fix). Its
      figures are this turn's own — "1,429 tokens" contains ``429``, which the substring map
      below reads as a rate limit — so it passes through verbatim.
    * ``MemoryError`` from an in-process model is numpy's allocator text ("Unable to allocate
      26.0 GiB for an array with shape (9, 27862, 27862)") — true, and nothing a user can act on.
      Answered before the empty-message rule too, since a bare ``MemoryError()`` is the same
      failure with the same fix.
    * ``ToolSchemaRejected`` — a provider refusing one of the request's tool definitions — is
      already the sentence (which tool, whose bug, and a workaround that is true in a chat and a
      room alike). The raw dump it replaces contains ``400`` and ``permission``-shaped words
      the substring map below would misread.
    * ``NoModelAnswered`` names every model a turn fell back to and why each failed, which no
      one provider's error can; and a model app's ``ProviderResolutionError`` (the SDK's) is its
      own sentence for why a model cannot serve this account, naming the fix only it knows
      (Bedrock's model access, a data-retention policy). The map would replace either with a
      generic line.
    * ``FirstTokenTimeout`` names the instance's own timeout setting and where it is, which the
      generic "did not answer in time" for an untyped timeout cannot; ``ModelCallTimeout``, an
      automated call that ran past the spend guard's ceiling, names the model, the limit and the
      use case whose model to change.
    * ``AnswerCutOff``: a stream that ended before its provider said the answer was finished
      raised nothing in the client library, and its own words name the adapter and the event that
      never came, which are the log's, so the chat says the answer was cut off.
    * A connection that failed or timed out, known by its type or its cause's
      (:func:`_transport_failure_sentence`): a provider SDK's "Request timed out." has words the
      matcher knows nothing in, and read by them it was a failure PersonalClaw doesn't recognize.
      An agent CLI's own timeout (``AcpTimeoutError``) is said as one too.

    A status code in the map matches only as a number of its own: ``401`` inside an account id
    or an ARN is not an HTTP 401, and read as one it named the API key for a permission error.
    A permission error is its own class: the provider knew the credentials and would not let
    them use the model, so the fix is access to that model, not a new key.

    **``room_member`` makes the remedies true on a room.** A sentence here is product copy on
    whatever surface shows it, and some of them name a chat-only fix: the composer's model
    selector, "start a new chat", "your message", "this chat's models". A room member's model is
    its AGENT BINDING's, chosen on the Agents page, and what outgrows a model there is the room's
    conversation — so given the member's name those say that instead. Every other sentence is
    surface-neutral and is the same words either way; with no member, every word is exactly the
    chat's.
    """
    known = _known_failure_sentence(exc, room_member=room_member)
    if known is not None:
        return known
    return (
        "The turn failed with an error PersonalClaw doesn't recognize. Try again; if it keeps "
        f"failing, check the gateway log. Details: {_own_words(exc)}"
    )


def _known_failure_sentence(exc: object, *, room_member: str = "") -> str | None:
    """:func:`humanize_provider_error`'s sentence for a failure it recognizes, or ``None`` for
    one that carries a message it recognizes nothing in."""
    from personalclaw.acp.errors import AcpTimeoutError
    from personalclaw.errors import ERROR_CODES, AgentError
    from personalclaw.guardrails.failure import (
        AnswerCutOff,
        CircuitOpenError,
        FirstTokenTimeout,
        LocalModelBusy,
        ModelCallTimeout,
        NoModelAnswered,
        PromptExceedsWindow,
        budget_refusal,
        request_exceeds_window_sentence,
    )
    from personalclaw.llm.registry import ProviderResolutionError
    from personalclaw.tool_providers.portable_schema import ToolSchemaRejected

    envelope = getattr(exc, "agent_error", None)
    if isinstance(envelope, AgentError) and envelope.code in ERROR_CODES:
        return envelope.sentence()
    if isinstance(exc, AcpTimeoutError):
        # An agent CLI did not answer within its turn's time limit — the one ending of its turn
        # that is a timeout (a stop, a refusal and a lost connection each say so themselves).
        # The agent is told to stop, and its late answer is dropped, never shown in another turn.
        return (
            "The agent did not finish within the turn's time limit, so the turn was stopped. "
            "Try again; if it keeps happening, check the gateway log."
        )
    refusal = budget_refusal(exc)
    if refusal is not None:
        # A spend ceiling's refusal says which ceiling, what was spent, and how it is lifted.
        return refusal.sentence()
    if isinstance(exc, ToolSchemaRejected):
        return exc.sentence()
    if isinstance(
        exc,
        (
            NoModelAnswered,
            FirstTokenTimeout,
            ModelCallTimeout,
            CircuitOpenError,
            LocalModelBusy,
            AnswerCutOff,
        ),
    ):
        return exc.sentence(room_member=room_member)
    if isinstance(exc, ProviderResolutionError) and str(exc).strip():
        return str(exc).strip()
    if isinstance(exc, PromptExceedsWindow):
        if room_member:
            return request_exceeds_window_sentence(
                model=exc.model,
                room_tokens=exc.room_tokens,
                request_tokens=exc.request_tokens,
                request_chars=exc.request_chars,
                room_member=room_member,
            )
        return str(exc)
    if isinstance(exc, MemoryError):
        if room_member:
            return (
                "This machine ran out of memory while the model was reading this room's "
                "conversation, so no reply was produced. Start a new room, or give the "
                f"{room_member} agent a model that does not run on this machine on the Agents page."
            )
        return (
            "This machine ran out of memory while the model was reading this conversation, so "
            "no reply was produced. Shorten the message or start a new chat — or bind a model "
            "that does not run on this machine in Settings → Models."
        )
    raw = str(exc or "").strip()
    if not raw:
        return _describe_unexplained_failure(exc)
    transport = _transport_failure_sentence(exc)
    if transport is not None:
        return transport
    low = raw.lower()
    # (needle, friendly) — order matters; first match wins. The two surface-bound remedies are
    # resolved first, so the table below stays one row per failure class.
    credits_fix = (
        f"give the {room_member} agent a different model on the Agents page."
        if room_member
        else "pick a different model for this chat (the model selector is in the composer)."
    )
    model_id = (
        f"The model the {room_member} agent names isn't valid for this provider. Pick a listed "
        "model for it on the Agents page."
        if room_member
        else "The selected model id isn't valid for this provider. Pick a listed model in "
        "the composer's model selector."
    )
    _MAP = [
        (
            ("credit balance is too low", "insufficient_quota", "insufficient credit", "billing"),
            f"This model's provider account is out of credits/quota. Top it up, or {credits_fix}",
        ),
        (
            (
                "rate limit",
                "rate_limit",
                "429",
                "too many requests",
                "overloaded",
                "overloaded_error",
            ),
            "The model provider is rate-limiting or overloaded right now. Wait a moment and "
            "retry, or switch to a different model.",
        ),
        (
            (
                "permission",
                "forbidden",
                "403",
                "access denied",
                "accessdenied",
                "not authorized to",
                "don't have access",
                "do not have access",
            ),
            "The model provider refused this request: the credentials it was sent aren't allowed "
            "to use this model. Give them access to it with the provider, or pick a different "
            "model.",
        ),
        (
            (
                "authentication",
                "invalid api key",
                "invalid x-api-key",
                "401",
                "unauthorized",
                "invalid_api_key",
            ),
            "The model provider rejected the API key (auth failed). Check the key in "
            "Settings → Providers, or pick a different model.",
        ),
        (
            (
                "model not found",
                "does not exist",
                "not_found_error",
                "unknown model",
                "invalid model",
            ),
            model_id,
        ),
    ]
    for needles, friendly in _MAP:
        if any(_mentions(low, n) for n in needles):
            return friendly
    return None


def _own_words(exc: object) -> str:
    """A failure's own message, trimmed to :data:`_OWN_WORDS_CAP` characters."""
    raw = str(exc or "").strip()
    return raw if len(raw) <= _OWN_WORDS_CAP else raw[:_OWN_WORDS_CAP] + "…"


def _mentions(text: str, needle: str) -> bool:
    """Whether ``text`` says ``needle``; a status code only as a number of its own.

    Not inside a longer number (an account id, an ARN, a request id) and not as part of a figure
    ("1,429 tokens", "401.5").
    """
    if needle.isdigit():
        return re.search(rf"(?<![\d.,]){needle}(?!\d|[.,]\d)", text) is not None
    return needle in text


#: The longest failure clause :func:`failure_clause` gives, in characters.
_CLAUSE_CAP = 160


def failure_clause(exc: BaseException) -> str:
    """A model's failure as a clause for "it failed before it replied (…)".

    The first sentence of what the chat shows for that failure (:func:`humanize_provider_error`),
    without its closing stop, its first letter lowered when it starts a sentence ("The model
    provider…" → "the model provider…", while "HTTP 500" stays as it is), and cut at a word past
    :data:`_CLAUSE_CAP` characters. One reading of a failure, so a fallback's line and the
    error the same failure shows cannot describe it differently. For a failure that reading does
    not recognize, the clause is the failure's own words — the detail the chat shows it with —
    since "doesn't recognize" says nothing about what failed in a line that names each model.
    A model that was busy, answered nothing or answered in the wrong shape raised no error worth
    quoting, and reads as what it did ("it answered with nothing"); a chain none of whose models
    answered reads as what each did, whoever's chain it was.
    """
    from personalclaw.guardrails.failure import (
        AnswerCutOff,
        EmptyCompletion,
        LocalModelBusy,
        NoModelAnswered,
        OutputContractError,
    )

    if isinstance(exc, (EmptyCompletion, LocalModelBusy, OutputContractError, AnswerCutOff)):
        return exc.reason()
    if isinstance(exc, NoModelAnswered):
        return f"no model of its chain answered: {exc.tried()}"
    if isinstance(exc, ChainExhausted) and exc.failures:
        # The reading the native loop gives a chain it walked: what each model did, each failure
        # in its one clause.
        walked = NoModelAnswered([(ref, failure_clause(err)) for ref, err in exc.failures])
        return f"no model of its chain answered: {walked.tried()}"
    text = (_known_failure_sentence(exc) or _own_words(exc)).strip()
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].rstrip(".!?")
    if len(first) > _CLAUSE_CAP:
        first = first[:_CLAUSE_CAP].rsplit(" ", 1)[0] + "…"
    if len(first) > 1 and first[0].isupper() and first[1].islower():
        first = first[0].lower() + first[1:]
    return first
