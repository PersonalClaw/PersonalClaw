"""The approval brief carried over the core↔channel seam (Contract C2,
`the plan (internal, not in this repo)`).

A channel (Slack, …) prompts the owner to approve a tool call through
:meth:`~personalclaw.channel_delivery.ChannelDelivery.request_approval`. Until this
module, the payload it received said WHAT tool wants to run but nothing about what
running it could TOUCH — the blast radius existed only in the dashboard, derived
frontend-side. This module composes the same brief backend-side and stamps it onto
the approval event as **additive meta**, so a phone notification can say the one
line that matters.

── What will run, as the dashboard's card shows it ──────────────────────────────
The brief also carries the call itself: the tool, its arguments and the purpose the
runner gave, each MASKED with :func:`~personalclaw.security.redact_field` — the mask the
dashboard's pending-approval entry applies to the same three strings — plus the one
``summary`` line (what the call can touch, and its risk) and, for a call that reaches a host
off the allowed hosts, the ``reach`` line saying so (the card's line under its chips). A
channel renders its prompt
from these alone (:func:`approval_brief_for`), so every channel shows what the
dashboard's card shows and none has a masking pass of its own to get wrong. Three channels
used to show the tool's name and nothing else, and people approved a call they could not
see.

── This module DECIDES nothing ──────────────────────────────────────────────────
It is descriptive. The approval gate, trust-reads and the task-mode gate live in
:mod:`personalclaw.task_modes` + :mod:`personalclaw.gateway` and are unchanged. The
classification here is CONSUMED from ``task_modes``, never re-derived: one reading of the
call (:func:`~personalclaw.task_modes.read_call`) gives both its effective risk and, for a
shell call, what its command establishes it does (``command_effects``). The risk and the
facets beside it therefore come from ONE analysis and cannot disagree. This module never
inspects a command string itself: reading a command is security logic and it already has an
owner.

── One derivation, every surface ────────────────────────────────────────────────
The dashboard's card, the out-of-context toast, the phone queue and every channel show the
blast radius composed HERE: :func:`call_blast_radius` runs where an approval is registered,
on the call's raw arguments, and the result travels with the approval (``blast_radius``) to
every surface. ``web/src/pages/chat/approvalMeta.ts`` holds the same facet WORDS and render
order for the dashboard to print, and ``tests/test_approval_brief.py`` parses that file and
asserts they still agree. A drift becomes a red test, not a second vocabulary.

For a shell call the facets are the command's: "writes files" for a redirect into a file or a
program run in a form that writes or deletes, "uses the network", "reads only" when every
program reads, and "runs a command" for any part the screen could not vouch for. For any
other call the hint lists DESCRIBE a change; they never establish a read. ``writes``,
``shell`` and ``network`` name what kind of thing a call that is not a read can touch, from
words in the tool's name, and a name is only ever evidence of what a tool might do.
``readOnly`` comes from the declaration alone, and a call it holds for claims no ``writes``.

── Honesty contract (identical to the frontend's) ───────────────────────────────
Every boolean is a POSITIVE claim: ``False`` means "not established", never
"verified absent". So :func:`derive_blast_radius` returns ``None`` when NOTHING was
established, rather than an all-false object — rendered as a line, all-false reads
"no writes, no network, no shell, not read-only", a confident all-clear derived from
zero evidence, and it is worst on the surface least able to check (the phone).
``read_only`` is claimed only on positive evidence — a declaration or a screened
command — so this module can only ever UNDER-claim safety.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from personalclaw import chat_trust
from personalclaw.approval_grants import asked_for_line
from personalclaw.channel_delivery import ONE_CALL_ANSWERS, ApprovalAnswer, chat_answers
from personalclaw.command_effects import CommandEffects
from personalclaw.run_bounds import reach_note
from personalclaw.security import redact_field
from personalclaw.task_modes import read_call, resolve_effective_risk, tool_input_to_str

logger = logging.getLogger(__name__)

#: The key the brief occupies in ``event.tool_meta``. A channel reads it through
#: :func:`approval_brief_for` (``personalclaw.sdk.channel``), never by this literal. See
#: :meth:`personalclaw.channel_delivery.ChannelDelivery.request_approval`.
APPROVAL_BRIEF_META_KEY = "approval_brief"

#: The risk chip's words — ``RISK_META`` in ``web/src/pages/chat/ApprovalCard.tsx`` verbatim
#: (pinned by ``tests/test_channel_approvals_show_what_will_run.py``), so a channel's
#: "Risk: Caution" is the dashboard card's chip. A level outside this map is shown as no risk
#: at all rather than as a word the card never uses.
RISK_LABELS: dict[str, str] = {
    "safe": "Safe",
    "caution": "Caution",
    "destructive": "Destructive",
    "unchecked": "Not checked",
}

# ── Tool-name description ────────────────────────────────────────────────────────
# What kind of change a call that is not a read can make, from the WORDS of its name: a name is
# split on `_`, `-`, `/` and camelCase, and a hint matches a whole word, never a fragment of
# one, so `list_commits` (lists commits) is not a `commit` and `curling_scores` is not a `url`.

#: Runs a command / spawns a process. ``terminal``/``shell``/``zsh`` cover the display
#: names ACP agents send as the title. Deliberately NOT ``run``: the ``project_run_*``
#: tools drive a workflow run, not a shell.
SHELL_HINTS: tuple[str, ...] = (
    "bash",
    "shell",
    "terminal",
    "zsh",
    "exec",
    "spawn",
    "command",
)

#: Leaves the machine. ``web_fetch``/``web_search`` are the app-provided web tools; the
#: rest cover MCP tools named by convention.
NETWORK_HINTS: tuple[str, ...] = (
    "web",
    "http",
    "https",
    "fetch",
    "browse",
    "browser",
    "download",
    "upload",
    "crawl",
    "scrape",
    "url",
)

#: Removes something. A delete is a write to the world, so these describe ``writes`` too.
DESTRUCTIVE_HINTS: tuple[str, ...] = ("delete", "remove", "destroy", "drop", "purge", "forget")

#: Creates or changes something.
WRITE_HINTS: tuple[str, ...] = (
    "write",
    "edit",
    "create",
    "save",
    "update",
    "move",
    "rename",
    "append",
    "set",
    "put",
    "install",
    "deploy",
    "subagent",
    "schedule",
    "notify",
    "post",
    "send",
    "commit",
    "push",
    "generate",
    "remember",
)

#: Does a risk level positively establish that the call is a read?
#:
#: Consumed, not invented: :func:`~personalclaw.task_modes.read_call` reaches ``'safe'``
#: only through a read-only shell command or a tool that DECLARES it only reads — so
#: EFFECTIVE-safe is already derived FROM read-only-ness. The other levels say a call is not
#: established as a read but not WHICH facet, so they establish nothing here. A level this
#: build has never heard of is no evidence, not a read.
RISK_ESTABLISHES_READ_ONLY: dict[str, bool] = {
    "safe": True,
    "caution": False,
    "destructive": False,
    "unchecked": False,
}

#: The words for each facet — ``approvalMeta.ts``' ``FACET_COPY`` verbatim, so the chat
#: chip, the toast line and the channel brief say the same thing.
FACET_COPY: dict[str, dict[str, str]] = {
    "writes": {
        "label": "Writes files",
        "detail": "Can create or change files on this machine.",
    },
    "shell": {
        "label": "Runs a command",
        "detail": "Can execute a command on this machine.",
    },
    "network": {
        "label": "Uses the network",
        "detail": "Can reach the network from this machine.",
    },
    "saysReadOnly": {
        "label": "Server says it only reads",
        "detail": (
            "The server that offers this tool labels it read-only. PersonalClaw takes that label"
            " only from a server you trust on the Tools page, and only for its tools as they were"
            " when you trusted it: one it adds or changes asks until you review it there."
        ),
    },
    "readOnly": {
        "label": "Reads only",
        "detail": "Established as a read: no change was established.",
    },
}

#: Render order — broadest consequence first, the read claims last. Kept as data so the
#: order is deliberate and reviewable, and pinned against the TypeScript's
#: ``BLAST_RADIUS_FACET_ORDER`` by test.
BLAST_RADIUS_FACET_ORDER: tuple[str, ...] = (
    "writes",
    "shell",
    "network",
    "saysReadOnly",
    "readOnly",
)

#: One word of a name: an acronym before a capitalised word (`HTTP` in `HTTPRequest`), a word in
#: either case, or a run of digits.
_NAME_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def _name_words(tool: str) -> frozenset[str]:
    """The words of a tool's own name, lowercased: an ``mcp/<server>/<tool>`` name is read by its
    tool, and an ACP display title ("Terminal") the same way."""
    bare = (tool or "").strip().rsplit("/", 1)[-1]
    return frozenset(word.lower() for word in _NAME_WORD.findall(bare))


def _has_any(words: frozenset[str], hints: tuple[str, ...]) -> bool:
    return not words.isdisjoint(hints)


def _risk_establishes_read_only(risk: str | None) -> bool:
    if not risk:
        return False
    return RISK_ESTABLISHES_READ_ONLY.get(str(risk).lower(), False)


def derive_blast_radius(
    tool: str,
    *,
    risk: str | None = None,
    effects: CommandEffects | None = None,
    annotations: Mapping[str, Any] | None = None,
) -> dict[str, bool] | None:
    """Derive the facets of one pending call, or ``None`` if none was established.

    ``effects`` is what the call's shell command establishes it does (``read_call``'s): when it
    is given, the facets are the command's and the tool's name says nothing. Otherwise ``tool``
    is the tool identity as it already travels the approval path (``event.title``), ``risk`` the
    EFFECTIVE per-invocation risk, and ``annotations`` what the tool's server labels it
    (``ToolDefinition.annotations``):

    * a read-only label is the server's word. It is a read (``readOnly``) only when the risk says
      so, which it does only for a server the owner trusts; otherwise the facet is the claim
      itself (``saysReadOnly``). Either way it outweighs a guess from the name about writes;
    * a destructive label is a write, and an open-world label is the network;
    * otherwise the name's words describe what a change may touch.

    Total and pure — no I/O, no clock, no throws.
    """
    if effects is not None:
        return {
            "writes": effects.writes,
            "shell": effects.unread,
            "network": effects.network,
            "saysReadOnly": False,
            "readOnly": effects.reads_only,
        }

    words = _name_words(tool)
    hints = annotations if isinstance(annotations, Mapping) else {}
    says_read = hints.get("readOnlyHint") is True

    shell = _has_any(words, SHELL_HINTS)
    network = _has_any(words, NETWORK_HINTS) or hints.get("openWorldHint") is True

    # What kind of change the call can make — a description, never a read: no word establishes
    # that a call changes nothing.
    writes = not says_read and (
        hints.get("destructiveHint") is True
        or _has_any(words, DESTRUCTIVE_HINTS)
        or _has_any(words, WRITE_HINTS)
    )
    # `reads` needs positive evidence: an EFFECTIVE-safe risk (the tool declares it only reads).
    # An established write rules it out — a tool labelled read-only by nothing but its risk whose
    # name says it writes is shown as the write it may be.
    reads = not writes and _risk_establishes_read_only(risk)
    says = says_read and not reads

    # Nothing established → say nothing. See the honesty contract in the header.
    if not (writes or network or shell or says or reads):
        return None
    return {
        "writes": writes,
        "shell": shell,
        "network": network,
        "saysReadOnly": says,
        "readOnly": reads,
    }


def call_blast_radius(event: Any) -> dict[str, bool] | None:
    """What one permission event's call can touch, from the same reading that gives its risk.

    Run where an approval is registered, on the event's RAW arguments (a screen must read what
    will run, never a masked copy), and carried with the approval to every surface that shows it.
    Reads what the event carries: the tool's declared risk, its name and kind, the arguments, and
    the labels its server gave it.
    """
    tool = str(getattr(event, "title", "") or "")
    reading = read_call(
        getattr(event, "risk_level", ""),
        tool,
        str(getattr(event, "tool_kind", "") or ""),
        getattr(event, "tool_input", ""),
    )
    return derive_blast_radius(
        tool,
        risk=reading.risk,
        effects=reading.effects,
        annotations=getattr(event, "annotations", None),
    )


def established_facets(radius: dict[str, bool] | None) -> list[dict[str, str]]:
    """The facets a caller may legitimately SHOW, in render order.

    Only established (``True``) facets are returned, and ``None`` yields ``[]``: painting
    a ``False`` facet as a negative ("no network") would turn absence of evidence into a
    confident all-clear. A surface shows the positives or shows nothing.
    """
    if not radius:
        return []
    return [
        {"key": k, **FACET_COPY[k]}
        for k in BLAST_RADIUS_FACET_ORDER
        if radius.get(k) and k in FACET_COPY
    ]


def blast_radius_line(radius: dict[str, bool] | None) -> str:
    """The compact one-line form — the done_when's "blast-radius line".

    Empty string when nothing is established: the caller then says nothing about the
    blast radius, rather than "nothing established", which a reader hears as "nothing
    happens".
    """
    facets = established_facets(radius)
    return ", ".join(f["label"].lower() for f in facets)


#: The facets that are NOT consequences: each claims what a call does not do (or what its server
#: says it does not), so it must never be framed as something the call "can" do. Named as the
#: EXCEPTIONS rather than listing the consequences, so a facet added later is framed as a
#: consequence automatically instead of silently reading as a reassurance.
_READ_CLAIM_FACETS = frozenset({"saysReadOnly", "readOnly"})


def summary_line(radius: dict[str, bool] | None, risk: str) -> str:
    """The one line a channel prints under the call: what it can touch, and its risk.

    ``"Can: writes files, runs a command · Risk: Caution"``; ``"Reads only · Risk: Safe"`` and
    ``"Server says it only reads · Risk: Caution"`` for the read claims, each its own phrase;
    ``"Risk: Caution"`` when no facet was established; ``""`` when neither is known. The facet
    half follows the honesty contract (:func:`established_facets`): an absent blast radius adds
    no words, never "nothing established". The risk half is the dashboard card's chip, which
    shows whatever facets say.
    """
    parts: list[str] = []
    facets = established_facets(radius)
    consequences = [f["label"].lower() for f in facets if f["key"] not in _READ_CLAIM_FACETS]
    if consequences:
        parts.append(f"Can: {', '.join(consequences)}")
    parts.extend(f["label"] for f in facets if f["key"] in _READ_CLAIM_FACETS)
    label = RISK_LABELS.get(str(risk or "").lower())
    if label:
        parts.append(f"Risk: {label}")
    return " · ".join(parts)


def _brief(
    *,
    radius: dict[str, bool] | None,
    shown_tool: str,
    shown_input: str,
    shown_purpose: str,
    risk: str,
    answers: tuple[ApprovalAnswer, ...],
    reach: str = "",
    asked_for: str = "",
) -> dict[str, Any]:
    """The brief's one shape. ``radius`` is what the call can touch; the ``shown_*`` strings are
    what a channel prints, already masked by the caller. ``answers`` is what the prompt offers
    (``channel_delivery.ApprovalAnswer``), which its caller decided. ``reach`` is why it is asked
    though a grant answers any other call (``run_bounds.ask_note``): a line of its own, under the
    summary, as the dashboard's card shows it under its chips. ``asked_for`` names who asked for
    the work the call is made in when the owner did not (``approval_grants.asked_for_line``): a
    line of its own too."""
    brief: dict[str, Any] = {
        "tool": shown_tool,
        "input": shown_input,
        "purpose": shown_purpose,
        "risk": risk,
        "answers": [answer.as_dict() for answer in answers],
    }
    if radius is not None:
        brief["blastRadius"] = radius
        brief["blastRadiusLine"] = blast_radius_line(radius)
    brief["summary"] = summary_line(radius, risk)
    if reach:
        brief["reach"] = reach
    if asked_for:
        brief["asked_for"] = asked_for
    return brief


def compose_approval_brief(
    event: Any, *, chat: str = "", asked_by: Mapping[str, str] | None = None
) -> dict[str, Any] | None:
    """Compose the brief for one approval event, or ``None`` when it has no identity.

    *chat* is the conversation a channel's own turn raised it in, given when the prompt is shown
    in that conversation (:func:`approval_brief_for`); without it the prompt answers this call
    alone. *asked_by* is who asked for the work the call is made in when the owner did not, by
    default the work running now's (``memory_writes.asker``): the brief names them, and its
    prompt answers this call alone, since none of her standing grants would answer the next.

    Reads only fields the event already carries, and takes its classification from
    ``task_modes`` rather than re-deriving it: one :func:`~personalclaw.task_modes.read_call`
    gives the EFFECTIVE risk and the command's effects, so the channel sees the risk the
    dashboard shows, and facets that cannot disagree with it (the event itself carries only the
    tool's DECLARED ``risk_level``, which over-states a read-only ``bash``).

    The event is the RAW one (the gateway's, or a channel's own turn's), so the tool, its
    arguments and its purpose are masked here, once, exactly as the dashboard's pending
    approval masks them (``DashboardApprovalState._approval_entry``: :func:`tool_input_to_str`, then
    :func:`~personalclaw.security.redact_field`). A native-loop call's arguments arrive as a
    dict, and are JSON-encoded before the mask reads them, which is what the card shows too.

    Nothing arrives on the phone claiming "reads only" without a declaration or a screened
    command behind it: ``read_call`` reports ``'safe'`` only on positive read evidence.
    """
    tool = str(getattr(event, "title", "") or "")
    if not tool:
        # No tool identity → nothing honest to say about what it can touch.
        return None

    tool_kind = str(getattr(event, "tool_kind", "") or "")
    tool_input = getattr(event, "tool_input", "")
    reading = read_call(getattr(event, "risk_level", ""), tool, tool_kind, tool_input)
    reach = reach_note(getattr(event, "risk_level", ""), tool, tool_kind, tool_input, chat)
    if asked_by is None:
        from personalclaw import memory_writes

        asked_by = memory_writes.asker()
    asked_for = asked_for_line(asked_by)
    return _brief(
        radius=derive_blast_radius(
            tool,
            risk=reading.risk,
            effects=reading.effects,
            annotations=getattr(event, "annotations", None),
        ),
        shown_tool=redact_field(tool),
        shown_input=redact_field(tool_input_to_str(tool_input)),
        shown_purpose=redact_field(str(getattr(event, "tool_purpose", "") or "")),
        risk=reading.risk,
        # An event alone is a call with no chat behind it that core knows of: a background
        # origin's (the gateway's relay), or one a channel's own turn raised where the prompt is
        # not in its conversation. Its prompt answers this call and nothing beyond it. A channel's
        # own turn asked in its conversation is offered what that chat's card offers, while a chat
        # the owner sees can hold its Trust, unless someone else asked for the turn.
        answers=(
            chat_answers(risk=reading.risk, reach=reach)
            if chat and not asked_for and chat_trust.can_trust(chat)
            else ONE_CALL_ANSWERS
        ),
        reach=redact_field(reach),
        asked_for=redact_field(asked_for),
    )


def entry_approval_brief(
    entry: Mapping[str, Any], *, answers: tuple[ApprovalAnswer, ...] = ONE_CALL_ANSWERS
) -> dict[str, Any] | None:
    """The brief for a pending approval the dashboard registered (its ``_approval_entry``).

    The entry's strings are the ones the dashboard's card shows, already masked, so they are
    carried as they are: masking them again would print something the card does not. So is its
    ``blast_radius``, composed when the approval was registered from the call's raw arguments.
    Its ``risk`` is the chat's effective risk; a background approval's entry has none, and gets
    it the way :func:`compose_approval_brief` does. ``answers`` is what the registry offers for it
    (``DashboardApprovalState.channel_answers``)."""
    tool = str(entry.get("tool") or "")
    if not tool:
        return None
    shown_input = str(entry.get("tool_input") or "")
    risk = str(entry.get("risk") or "") or str(resolve_effective_risk("", tool, "", shown_input))
    radius = entry.get("blast_radius")
    return _brief(
        radius=radius if isinstance(radius, dict) else None,
        shown_tool=tool,
        shown_input=shown_input,
        shown_purpose=str(entry.get("tool_purpose") or ""),
        risk=risk,
        answers=answers,
        reach=str(entry.get("reach") or ""),
        asked_for=str(entry.get("asked_for") or ""),
    )


def approval_brief_for(event: Any, *, chat: str = "") -> dict[str, Any] | None:
    """What a channel's approval prompt shows, for *event*: THE read a channel makes.

    The brief core stamped on the event (``tool_meta``) when core asked the channel; composed
    from the event itself when the channel's own turn raised the approval. Either way every
    string in it is masked: ``tool``, ``input`` (the arguments, as the dashboard's card shows
    them), ``purpose`` and ``summary`` (what the call can touch, and its risk), ``reach``
    when the call reaches a host off the allowed hosts (a line of its own, under the summary), and
    ``asked_for`` when someone other than the owner asked for the turn the call is made in, naming
    them (a line of its own too). A channel prints those and masks nothing of its own, and offers
    the ``answers`` it carries (``ChannelDelivery.request_approval``): in a turn someone else asked
    for, the owner's answer for this call alone. ``None`` when the event names no tool.

    *chat* is for a channel that runs a conversation itself and asks about its own turn's call in
    that conversation: the session key the turn runs under. The prompt then offers what that
    chat's approval card offers for the call, Allow for this chat among them where the card would
    (``channel_delivery.chat_answers``), while a chat the owner sees can hold its Trust
    (``chat_trust.can_trust``); the channel hands the answer pressed to
    ``chat_trust.answer_in_chat``. Without it, a prompt for the channel's own turn answers the
    call alone. A brief core stamped is core's, and *chat* changes nothing in it.

    A stamped brief that lacks one of those four strings, or answers a prompt can offer, is not
    used: the prompt it made would show less than the call, or offer nothing to press, so the
    brief is composed from the event instead, which offers this call alone.
    """
    meta = getattr(event, "tool_meta", None)
    brief = meta.get(APPROVAL_BRIEF_META_KEY) if isinstance(meta, dict) else None
    if (
        isinstance(brief, dict)
        and all(isinstance(brief.get(k), str) for k in _SHOWN)
        and _offers_answers(brief.get("answers"))
    ):
        return brief
    return compose_approval_brief(event, chat=chat)


#: What a prompt shows, all of it in every brief :func:`_brief` makes.
_SHOWN = ("tool", "input", "purpose", "summary")


def _offers_answers(answers: object) -> bool:
    """Whether *answers* is a list a prompt can offer: at least one answer, each with every
    field of :class:`~personalclaw.channel_delivery.ApprovalAnswer` a string."""
    fields = tuple(ApprovalAnswer.__dataclass_fields__)
    return (
        isinstance(answers, list)
        and bool(answers)
        and all(
            isinstance(a, dict) and all(isinstance(a.get(f), str) for f in fields) for a in answers
        )
    )


def attach_approval_brief(
    event: Any, *, asked_by: Mapping[str, str] | None = None
) -> dict[str, Any] | None:
    """Stamp the brief onto ``event.tool_meta`` as additive meta; return it (or ``None``).

    ADDITIVE is the whole contract. The call's arguments do not change, no field is
    replaced, and every pre-existing ``tool_meta`` key survives — a channel that never
    heard of :data:`APPROVAL_BRIEF_META_KEY` behaves exactly as it did before. A
    permission-request event's ``tool_meta`` is empty in practice (the runtimes populate
    it on tool RESULTS), so this only ever adds.

    When the event cannot carry meta (``tool_meta`` absent or not a dict) nothing is
    stamped and ``None`` is returned: the channel prompts as before and the dashboard
    remains the rich surface either way.
    """
    brief = compose_approval_brief(event, asked_by=asked_by)
    if brief is None:
        return None
    meta = getattr(event, "tool_meta", None)
    if not isinstance(meta, dict):
        logger.debug("approval brief not attached: %s has no dict tool_meta", type(event).__name__)
        return None
    meta[APPROVAL_BRIEF_META_KEY] = brief
    return brief
