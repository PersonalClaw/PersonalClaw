"""Neutral agent-event type — the provider-agnostic event the chat runner reads.

Defines the backend-neutral :class:`AgentEvent` that every backend (ACP via
``acp/adapter.py``, the native loop, the HTTP model providers) emits and the
chat runner consumes. ``LLMEvent`` aliases it.

The field set is a superset of ``acp.types.AcpEvent`` (same names + defaults).
The event-kind constants live here as the canonical home; ``acp.types`` imports
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Event-kind constants (canonical home) ──
EVENT_TEXT_CHUNK = "text_chunk"
EVENT_THINKING_CHUNK = "thinking_chunk"
EVENT_TOOL_CALL = "tool_call"
# Resolved input (and refined title/purpose) for a tool whose initial TOOL_CALL
# frame was empty — agents stream the real args in a later update. Refines the
# existing card in place; does NOT re-fire hooks/SEL/mirror like a fresh call.
EVENT_TOOL_CALL_UPDATE = "tool_call_update"
EVENT_TOOL_RESULT = "tool_result"
EVENT_PERMISSION_REQUEST = "permission_request"
#: The turn's terminal event. On a CANCELLED stop that the runtime made itself (its loop breaker
#: gave up on the turn), ``text`` is the sentence saying why, for the surface to show; it is
#: empty for every other ending.
EVENT_COMPLETE = "complete"
EVENT_COMPACTION_STATUS = "compaction_status"
#: The ``text`` of a COMPACTION_STATUS a loop sends when it compacted its own history on its own,
#: between two steps of a turn; ``title`` says how much, in ``/compact``'s words. Not
#: ``completed``, which is a ``/compact`` command's result: the chat runner lets that replace what
#: streamed before it, and mid-turn what streamed before it is the answer.
COMPACTION_AUTOMATIC = "automatic"
EVENT_CLEAR_STATUS = "clear_status"
EVENT_AGENT_SWITCHED = "agent_switched"
# The turn's model failed before it said anything and the next model in its chain answers
# instead. ``text`` is the sentence that says so ("Ran on X instead of Y: …"), and it arrives
# before anything that model streams. Only the native loop emits it, and only for a caller
# that asked it to fail over (``NativeAgentRuntime.announce_failover``), since a caller that
# drops this event would be showing another model's reply as the chosen one's.
EVENT_MODEL_SUBSTITUTION = "model_substitution"

#: The ``stop_reason`` of a reply that stopped because it reached the model's OUTPUT cap — the
#: Anthropic/Bedrock spelling, which a provider that owns its own decoding also emits.
STOP_MAX_TOKENS = "max_tokens"

#: Every spelling of that same stop across providers: OpenAI's ``finish_reason`` is ``length``.
#: One set, so the tool-argument reader (a call cut mid-JSON) and the chat surface (a reply cut
#: mid-sentence) cannot disagree about which stops were cuts.
LENGTH_STOP_REASONS: frozenset[str] = frozenset({STOP_MAX_TOKENS, "length"})


def is_length_stop(stop_reason: object) -> bool:
    """Whether ``stop_reason`` says the reply was cut at the output cap, in any provider's words."""
    return str(stop_reason or "").strip().lower() in LENGTH_STOP_REASONS


#: The ``stop_reason`` of a turn the agent refused to go on with — the Agent Client Protocol's
#: word and the Anthropic API's alike.
STOP_REFUSAL = "refusal"


def is_refusal_stop(stop_reason: object) -> bool:
    """Whether ``stop_reason`` says the agent refused to continue the turn."""
    return str(stop_reason or "").strip().lower() == STOP_REFUSAL


#: The ``tool_meta`` key a TOOL_RESULT carries when the call would have asked first and the
#: session's approval policy answered for it (``set_approval_policy("auto")``) — so no approval
#: ever reached the chat runner's gate, which is the one place that knows WHOSE switch set the
#: policy: the app's grant, your Trust, or YOLO. Absent for a call that asks nobody.
TOOL_META_APPROVAL_WAIVED = "approval_waived"

#: The ``tool_meta`` key a TOOL_RESULT carries when the call needed an approval and the run was
#: unattended, so the runtime declined it without asking anyone. The runtime cannot reach
#: the Inbox; whoever consumes its stream — the chat runner, the subagent manager — records the
#: denial there (``auto_denials.py``) so the morning can see what did not run.
TOOL_META_AUTO_DENIED = "auto_denied"

#: The ``tool_meta`` key a TOOL_RESULT carries when the runtime's OWN gate refused the call before
#: anyone could be asked, naming the gate: ``deny_list``, ``task_mode``, ``tool_grants`` (the host's
#: grants for this run, ``NativeAgentRuntime.set_tool_grants``), ``hook``, ``loop_breaker``,
#: ``unknown_tool``, ``withdrawn`` (the tool's app was removed, or the tool switched off, after the
#: turn's catalog was built), or ``dry_run`` (observe mode, which runs nothing that writes).
TOOL_META_REFUSED_BY = "refused_by"

#: The ``tool_meta`` key a TOOL_RESULT carries when the call was answered without running, for a
#: reason that is not a gate's refusal: ``stopped`` (a stop reached it first),
#: ``unreadable_arguments`` (nothing to run it with), ``missing_arguments`` (it lacks an argument
#: its tool's declared input schema requires, so it was answered before anyone was asked to approve
#: it), ``failed_predecessor`` (an earlier call on the same resource failed, so its state is
#: unknown).
TOOL_META_NOT_RUN = "not_run"


# ── The one audit row of a call nobody was asked about ──────────────────────────────────────
#
# A tool call is audited ONCE, when it is decided, never at its card. The card (EVENT_TOOL_CALL)
# arrives before any gate runs: the native loop yields it and only then checks the deny-list, the
# task mode and the approval, so a row written there — `auto_approved`, even `invoked` — claimed a
# decision nobody had made, and a refused call read as approved. A call that was ASKED is audited
# by the host where the answer lands (approved, rejected, expired, cancelled, or the grant that
# answered it). One that was not asked is audited at its result, from what the runtime stamped:


def unasked_outcome(meta: dict[str, Any]) -> str:
    """The outcome of the one audit row of a call nobody was asked about, from its result's meta.

    ``denied`` when the runtime refused it (its own gate, or an unattended run with nobody to
    ask); ``cancelled`` when a stop reached it before it ran; ``failed`` when it could not be run
    at all; ``auto_approved`` when the session's approval policy answered its ask; ``invoked`` for
    a tool that asks nobody by its own definition — it ran, and no approval decided anything.
    """
    not_run = str(meta.get(TOOL_META_NOT_RUN) or "")
    if not_run == "stopped":
        return "cancelled"
    if not_run:
        return "failed"
    if meta.get(TOOL_META_AUTO_DENIED) or meta.get(TOOL_META_REFUSED_BY):
        return "denied"
    if meta.get(TOOL_META_APPROVAL_WAIVED):
        return "auto_approved"
    return "invoked"


def unasked_reason(meta: dict[str, Any]) -> str:
    """Why :func:`unasked_outcome` says what it says: the refusing gate, or what decided."""
    not_run = str(meta.get(TOOL_META_NOT_RUN) or "")
    if not_run:
        return not_run
    if meta.get(TOOL_META_AUTO_DENIED):
        return "unattended_no_one_to_ask"
    refused = str(meta.get(TOOL_META_REFUSED_BY) or "")
    if refused:
        return refused
    if meta.get(TOOL_META_APPROVAL_WAIVED):
        return "session_policy"
    return "no_approval_needed"


@dataclass
class AgentEvent:
    """A neutral event from any agent/model backend's turn stream.

    Every ``acp.types.AcpEvent`` field is here under the same name and default,
    so the chat runner consumes either without change. ``risk_level``, ``builds``,
    ``proposes``, ``tells_owner``, ``served_model_ref`` and ``audit_ids`` have no ACP twin, since
    an ACP agent reports none of them.
    ``tool_input``/``tool_output`` are typed ``Any`` (the native loop may pass
    structured values; ACP passes str).
    """

    kind: str  # one of the EVENT_* constants above
    text: str = ""
    tool_call_id: str = ""
    title: str = ""
    tool_kind: str = ""
    tool_purpose: str = ""
    # Declared risk of the tool behind a TOOL_CALL / PERMISSION_REQUEST — the
    # tool's static ToolDefinition.risk_level ('safe'|'caution'|'destructive'),
    # or '' when the backend declared none (an ACP CLI's own tools). 'safe' is the
    # read-only declaration and '' is not one. The approval gate resolves the
    # per-invocation EFFECTIVE risk from this (a read-only bash call downgrades to
    # safe); it's also surfaced as a user-facing indicator.
    risk_level: str = ""
    #: Context-window usage the provider actually measured, or ``None`` when it
    #: measured none. A defaulted 0.0 made "unsupplied" and "a genuinely empty
    #: context" the same value, so every consumer printed a fabricated 0%.
    context_usage_pct: float | None = None
    stop_reason: str = ""
    request_id: str | int = ""
    options: Any = field(default_factory=list)
    tool_input: Any = ""
    #: The structured form of :attr:`tool_input`, for backends that carry BOTH a
    #: display string and the object behind it (ACP-AGENT-PARITY §2.5 gap 7). The
    #: native runtime needs nothing here — it already puts its dict straight into
    #: ``tool_input`` — but an ACP frame's ``tool_input`` is a redacted, pretty-printed
    #: string the card renders verbatim, so flattening the object into that field
    #: would have changed every ACP card's preview to buy the schema-driven fields.
    #: ``None`` means "no object was supplied", never "the object was empty".
    tool_input_obj: dict[str, Any] | None = None
    #: A file edit the BACKEND declared, as ``{"path", "before", "after"}``
    #: (ACP-AGENT-PARITY §2.5 gap 7). The native runtime leaves this empty and keeps
    #: driving chips off its own write-tool name set, because it can reconstruct
    #: ``after`` from the call arguments; a backend that states both sides outright
    #: fills this instead of asking the host to infer anything.
    file_change: dict[str, str] | None = None
    tool_output: Any = ""
    # usage / cost — read by chat_runner's EVENT_COMPLETE token block
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0
    duration_ms: int = 0
    # Turn telemetry on the terminal EVENT_COMPLETE — provider-neutral aggregate
    # tallies the chat runner renders as the collapsed "Turn complete" stats line
    # (events seen this prompt, tool calls made). Both the native loop and the ACP
    # client populate these; live-only, never persisted.
    event_count: int = 0
    tool_call_count: int = 0
    # Typed tool I/O metadata for the rendering framework (tool-io-rendering) +
    # projection (tool-output-projection): on a TOOL_RESULT, carries
    # content_type / raw_ref / truncated / original_length; on a TOOL_CALL, may
    # carry the input schema + render hint. Empty for backends that don't supply
    # it (ACP) → the UI renders exactly as before. Mirror in acp.types.AcpEvent.
    tool_meta: dict[str, Any] = field(default_factory=dict)
    #: The ``"<entry>:<model>"`` ref of the model that answered, on the native loop's terminal
    #: EVENT_COMPLETE: the fallback when the turn fell back down its chain, so the usage this
    #: event carries is priced by the model that ran. ``""`` from a backend that does not say.
    served_model_ref: str = ""
    #: The tool behind a PERMISSION_REQUEST declares it builds a Build-mode deliverable
    #: (``ToolDefinition.builds``), so the dashboard's task-mode gate admits it in Build mode
    #: exactly as the native runtime's did. False from a backend that declares nothing.
    builds: bool = False
    #: ...and whether its only effect is a proposal the owner reviews
    #: (``ToolDefinition.proposes``), which a research-class run's ``read`` grant admits.
    proposes: bool = False
    #: On a terminal EVENT_COMPLETE, the ``audit_id`` of each guarded model call whose usage this
    #: event carries, as ``model_calls.jsonl`` records it: the guard that made the call stamps it
    #: (``guardrails.model_call``), and the native loop carries those of every inference of its
    #: turn. A usage row keeps them, so the model-call log's census leaves out a call the row
    #: already counts. Empty from a backend no guard wraps (the interactive chat, an ACP CLI).
    audit_ids: tuple[str, ...] = ()
    #: With ``builds`` and ``proposes``, what the tool behind a TOOL_CALL / PERMISSION_REQUEST
    #: declares: whether THIS call does nothing but tell the owner something
    #: (``ToolDefinition.tells_owner``, ``tool_providers.base.only_tells_the_owner``), which an
    #: automation's own agent may do though its grant is ``read``.
    tells_owner: bool = False
    #: With ``builds`` and ``proposes``, what the tool's server labels it in its MCP annotations
    #: (``ToolDefinition.annotations``), which the approval prompt shows as the server's word.
    #: Empty from a backend that says none. Last, so no field moves.
    annotations: dict[str, Any] = field(default_factory=dict)
