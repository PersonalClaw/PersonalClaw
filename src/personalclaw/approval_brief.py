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
``summary`` line (what the call can touch, and its risk). A channel renders its prompt
from these alone (:func:`approval_brief_for`), so every channel shows what the
dashboard's card shows and none has a masking pass of its own to get wrong. Three channels
used to show the tool's name and nothing else, and people approved a call they could not
see.

── This module DECIDES nothing ──────────────────────────────────────────────────
It is descriptive, exactly like its frontend twin. The approval gate, trust-reads
and the task-mode gate live in :mod:`personalclaw.task_modes` +
:mod:`personalclaw.gateway` and are unchanged. The one classification here —
whether a call only reads — is CONSUMED from ``task_modes``, never re-derived: it
is the call's effective risk, which is ``safe`` only when the tool DECLARES it only
reads or the command it runs is a read-only one. In particular this module never
inspects a command string: deciding whether a command is read-only is security
logic and it already has an owner (:func:`~personalclaw.task_modes.is_read_only_bash`,
reached only via :func:`~personalclaw.task_modes.resolve_effective_risk`).

── One vocabulary, two languages ────────────────────────────────────────────────
``web/src/pages/chat/approvalMeta.ts`` is the same derivation for the
dashboard's chips and the out-of-context toast. Three surfaces must not invent three
words for one claim, so the facet labels and the hint lists below are the
TypeScript's verbatim, and ``tests/test_approval_brief.py`` parses that file and
asserts every label and hint list still agrees. A drift becomes a red test, not a
third vocabulary.

The hint lists DESCRIBE a change; they never establish a read. ``writes``, ``shell``
and ``network`` name what kind of thing a call that is not a read can touch, from
words in the tool's name, and a name is only ever evidence of what a tool might do.
``readOnly`` comes from the declaration alone, and a call it holds for claims no
``writes``.

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
from collections.abc import Mapping
from typing import Any

from personalclaw.security import redact_field
from personalclaw.task_modes import resolve_effective_risk, tool_input_to_str

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
}

# ── Tool-name description ────────────────────────────────────────────────────────
# What kind of change a call that is not a read can make, from words in its name. The
# TypeScript mirrors each list verbatim; `tests/test_approval_brief.py` pins them.

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
    "web_",
    "http",
    "fetch",
    "browse",
    "download",
    "upload",
    "crawl",
    "scrape",
    "url",
)

#: Removes something. A delete is a write to the world, so these describe ``writes`` too.
DESTRUCTIVE_HINTS: tuple[str, ...] = ("delete", "remove", "destroy", "drop_", "purge", "forget")

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
    "set_",
    "put_",
    "install",
    "deploy",
    "subagent",
    "schedule",
    "notify",
    "post_",
    "send",
    "commit",
    "push",
    "generate",
    "remember",
)

#: Does a risk level positively establish that the call is a read?
#:
#: Consumed, not invented: :func:`~personalclaw.task_modes.resolve_effective_risk`
#: reaches ``'safe'`` only through a read-only shell command or a tool that DECLARES it
#: only reads — so EFFECTIVE-safe is already derived FROM read-only-ness.
#: ``'caution'``/``'destructive'`` say a call has side effects but not WHICH facet, so
#: they establish nothing here. A level this build has never heard of is no evidence, not
#: a read.
RISK_ESTABLISHES_READ_ONLY: dict[str, bool] = {
    "safe": True,
    "caution": False,
    "destructive": False,
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
    "readOnly": {
        "label": "Reads only",
        "detail": "Established as a read: no change was established.",
    },
}

#: Render order — broadest consequence first, the read claim last. Kept as data so the
#: order is deliberate and reviewable, and pinned against the TypeScript's
#: ``BLAST_RADIUS_FACET_ORDER`` by test.
BLAST_RADIUS_FACET_ORDER: tuple[str, ...] = ("writes", "shell", "network", "readOnly")


def _normalize_tool_name(tool: str) -> str:
    """Lowercase + strip any ``<prefix>/`` so the verb match sees the bare name.

    Mirrors ``approvalMeta.ts``' ``normalizeToolName``, so an ``mcp/<server>/<tool>``
    name is described by its tool. Lowercasing also lets ACP display titles ("Terminal")
    match.
    """
    lowered = (tool or "").lower().strip()
    return lowered.rsplit("/", 1)[-1] if "/" in lowered else lowered


def _has_any(name: str, hints: tuple[str, ...]) -> bool:
    return any(h in name for h in hints)


def _risk_establishes_read_only(risk: str | None) -> bool:
    if not risk:
        return False
    return RISK_ESTABLISHES_READ_ONLY.get(str(risk).lower(), False)


def derive_blast_radius(
    tool: str,
    *,
    risk: str | None = None,
    read_only: bool | None = None,
) -> dict[str, bool] | None:
    """Derive C2's four facets for one pending call, or ``None`` if none was established.

    ``tool`` is the tool identity as it already travels the approval path (``event.title``
    — the same value ``chat_runner`` broadcasts as the ``approval`` event's ``tool``).
    ``risk`` is the EFFECTIVE per-invocation risk. ``read_only`` is the approval's
    ``is_read_only`` (``task_modes.reads_only``): ``True``/``False`` establish/rule out the read
    claim, ``None`` (a row that carries none) says nothing either way. It is declared for parity
    with the frontend's ``deriveBlastRadius`` (C2's third input) and NO caller here supplies it —
    :func:`compose_approval_brief` explains why passing it would be redundant.

    Total and pure — no I/O, no clock, no throws. Field-for-field identical to
    ``approvalMeta.ts``' ``deriveBlastRadius``.
    """
    name = _normalize_tool_name(tool)

    shell = _has_any(name, SHELL_HINTS)
    network = _has_any(name, NETWORK_HINTS)

    # What kind of change the call can make, from words in its name — a description, never a
    # read: no word establishes that a call changes nothing.
    writes = _has_any(name, DESTRUCTIVE_HINTS) or _has_any(name, WRITE_HINTS)
    # `reads` needs positive evidence: the call's read verdict or an EFFECTIVE-safe risk (the
    # tool declares it only reads, or its command screened read-only). An explicit `False`
    # rules it out whatever the risk says, and so does an established write — a tool labelled
    # read-only whose name says it writes is shown as the write it may be.
    reads = not writes and (
        read_only is True or (read_only is not False and _risk_establishes_read_only(risk))
    )

    # Nothing established → say nothing. See the honesty contract in the header.
    if not writes and not network and not shell and not reads:
        return None
    return {"writes": writes, "network": network, "shell": shell, "readOnly": reads}


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


#: The one facet that is NOT a consequence: ``readOnly`` claims what a call does not do, so
#: it must never be framed as something the call "can" do. Named as the EXCEPTION rather than
#: listing the consequences, so a facet added later is framed as a consequence automatically
#: instead of silently reading as a reassurance.
_READ_CLAIM_FACET = "readOnly"


def summary_line(radius: dict[str, bool] | None, risk: str) -> str:
    """The one line a channel prints under the call: what it can touch, and its risk.

    ``"Can: writes files, runs a command · Risk: Caution"``; ``"Reads only · Risk: Safe"`` when
    the only established facet is the read claim; ``"Risk: Caution"`` when no facet was
    established; ``""`` when neither is known. The facet half follows the honesty contract
    (:func:`established_facets`): an absent blast radius adds no words, never "nothing
    established". The risk half is the dashboard card's chip, which shows whatever facets say.
    """
    parts: list[str] = []
    facets = established_facets(radius)
    if facets:
        words = ", ".join(f["label"].lower() for f in facets)
        consequence = any(f["key"] != _READ_CLAIM_FACET for f in facets)
        parts.append(f"Can: {words}" if consequence else f"{words[:1].upper()}{words[1:]}")
    label = RISK_LABELS.get(str(risk or "").lower())
    if label:
        parts.append(f"Risk: {label}")
    return " · ".join(parts)


def _brief(
    tool: str, *, shown_tool: str, shown_input: str, shown_purpose: str, risk: str
) -> dict[str, Any]:
    """The brief's one shape. ``tool`` is the identity the blast radius is derived from; the
    ``shown_*`` strings are what a channel prints, already masked by the caller."""
    radius = derive_blast_radius(tool, risk=risk)
    brief: dict[str, Any] = {
        "tool": shown_tool,
        "input": shown_input,
        "purpose": shown_purpose,
        "risk": risk,
    }
    if radius is not None:
        brief["blastRadius"] = radius
        brief["blastRadiusLine"] = blast_radius_line(radius)
    brief["summary"] = summary_line(radius, risk)
    return brief


def compose_approval_brief(event: Any) -> dict[str, Any] | None:
    """Compose the brief for one approval event, or ``None`` when it has no identity.

    Reads only fields the event already carries, and takes its single classification
    from ``task_modes`` rather than re-deriving it: ``risk`` is
    :func:`~personalclaw.task_modes.resolve_effective_risk`, so the channel sees the same
    EFFECTIVE risk the dashboard shows (the event itself carries only the tool's DECLARED
    ``risk_level``, which over-states a read-only ``bash``).

    The event is the RAW one (the gateway's, or a channel's own turn's), so the tool, its
    arguments and its purpose are masked here, once, exactly as the dashboard's pending
    approval masks them (``DashboardApprovalState._approval_entry``: :func:`tool_input_to_str`, then
    :func:`~personalclaw.security.redact_field`). A native-loop call's arguments arrive as a
    dict, and are JSON-encoded before the mask reads them, which is what the card shows too.

    **Why the read verdict is not passed separately.** OU-8 measured the
    ``read_only`` pass-through as redundant, and it is: ``resolve_effective_risk``
    already routes a readable command through ``is_read_only_bash`` and only ever reports
    ``'safe'`` on positive read evidence — a tool that declares nothing floors at
    ``'caution'``, never ``'safe'``, so nothing arrives on the phone claiming "reads only"
    without a declaration or a screened command behind it.

    """
    tool = str(getattr(event, "title", "") or "")
    if not tool:
        # No tool identity → nothing honest to say about what it can touch.
        return None

    tool_kind = str(getattr(event, "tool_kind", "") or "")
    tool_input = getattr(event, "tool_input", "")
    risk = str(
        resolve_effective_risk(getattr(event, "risk_level", ""), tool, tool_kind, tool_input)
    )
    return _brief(
        tool,
        shown_tool=redact_field(tool),
        shown_input=redact_field(tool_input_to_str(tool_input)),
        shown_purpose=redact_field(str(getattr(event, "tool_purpose", "") or "")),
        risk=risk,
    )


def entry_approval_brief(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """The brief for a pending approval the dashboard registered (its ``_approval_entry``).

    The entry's strings are the ones the dashboard's card shows, already masked, so they are
    carried as they are: masking them again would print something the card does not. Its
    ``risk`` is the chat's effective risk; a background approval's entry has none, and gets it
    the way :func:`compose_approval_brief` does."""
    tool = str(entry.get("tool") or "")
    if not tool:
        return None
    shown_input = str(entry.get("tool_input") or "")
    risk = str(entry.get("risk") or "") or str(resolve_effective_risk("", tool, "", shown_input))
    return _brief(
        tool,
        shown_tool=tool,
        shown_input=shown_input,
        shown_purpose=str(entry.get("tool_purpose") or ""),
        risk=risk,
    )


def approval_brief_for(event: Any) -> dict[str, Any] | None:
    """What a channel's approval prompt shows, for *event*: THE read a channel makes.

    The brief core stamped on the event (``tool_meta``) when core asked the channel; composed
    from the event itself when the channel's own turn raised the approval. Either way every
    string in it is masked: ``tool``, ``input`` (the arguments, as the dashboard's card shows
    them), ``purpose`` and ``summary`` (what the call can touch, and its risk). A channel prints
    those and masks nothing of its own. ``None`` when the event names no tool.

    A stamped brief that lacks one of those four strings is not used: the prompt it made would
    show less than the call, so the brief is composed from the event instead.
    """
    meta = getattr(event, "tool_meta", None)
    brief = meta.get(APPROVAL_BRIEF_META_KEY) if isinstance(meta, dict) else None
    if isinstance(brief, dict) and all(isinstance(brief.get(k), str) for k in _SHOWN):
        return brief
    return compose_approval_brief(event)


#: What a prompt shows, all of it in every brief :func:`_brief` makes.
_SHOWN = ("tool", "input", "purpose", "summary")


def attach_approval_brief(event: Any) -> dict[str, Any] | None:
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
    brief = compose_approval_brief(event)
    if brief is None:
        return None
    meta = getattr(event, "tool_meta", None)
    if not isinstance(meta, dict):
        logger.debug("approval brief not attached: %s has no dict tool_meta", type(event).__name__)
        return None
    meta[APPROVAL_BRIEF_META_KEY] = brief
    return brief
