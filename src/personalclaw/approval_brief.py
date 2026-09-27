"""OU-9 — the approval brief carried over the core↔channel seam (Contract C2,
`the ONBOARDING-UX plan (internal, not in this repo)`).

A channel (Slack, …) prompts the owner to approve a tool call through
:meth:`~personalclaw.channel_delivery.ChannelDelivery.request_approval`. Until this
module, the payload it received said WHAT tool wants to run but nothing about what
running it could TOUCH — the blast radius existed only in the dashboard, derived
frontend-side. This module composes the same brief backend-side and stamps it onto
the approval event as **additive meta**, so a phone notification can say the one
line that matters.

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
``web/src/pages/chat/approvalMeta.ts`` (OU-7/OU-8) is the same derivation for the
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
from typing import Any

from personalclaw.task_modes import resolve_effective_risk

logger = logging.getLogger(__name__)

#: The key the brief occupies in ``event.tool_meta``. A channel implementation reads
#: ``event.tool_meta.get(APPROVAL_BRIEF_META_KEY)`` and renders what it can; a channel
#: that ignores it behaves exactly as before. See
#: :meth:`personalclaw.channel_delivery.ChannelDelivery.request_approval`.
APPROVAL_BRIEF_META_KEY = "approval_brief"

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
    read_only_command: bool | None = None,
) -> dict[str, bool] | None:
    """Derive C2's four facets for one pending call, or ``None`` if none was established.

    ``tool`` is the tool identity as it already travels the approval path (``event.title``
    — the same value ``chat_runner`` broadcasts as the ``approval`` event's ``tool``).
    ``risk`` is the EFFECTIVE per-invocation risk. ``read_only_command`` is the command
    screening verdict: ``True``/``False`` positively establish/rule out the read claim,
    ``None`` says nothing either way. It is declared for parity with the frontend's
    ``deriveBlastRadius`` (C2's third input) and, as there, NO caller supplies it —
    :func:`compose_approval_brief` explains why passing it would be worse than redundant.

    Total and pure — no I/O, no clock, no throws. Field-for-field identical to
    ``approvalMeta.ts``' ``deriveBlastRadius``.
    """
    name = _normalize_tool_name(tool)

    shell = _has_any(name, SHELL_HINTS)
    network = _has_any(name, NETWORK_HINTS)

    # What kind of change the call can make, from words in its name — a description, never a
    # read: no word establishes that a call changes nothing.
    writes = _has_any(name, DESTRUCTIVE_HINTS) or _has_any(name, WRITE_HINTS)
    # `read_only` needs positive evidence: the screening verdict (it inspected the actual
    # command) or an EFFECTIVE-safe risk (the tool declares it only reads). An explicit `False`
    # from the screen rules it out whatever the risk says, and so does an established write —
    # a tool labelled read-only whose name says it writes is shown as the write it may be.
    read_only = not writes and (
        read_only_command is True
        or (read_only_command is not False and _risk_establishes_read_only(risk))
    )

    # Nothing established → say nothing. See the honesty contract in the header.
    if not writes and not network and not shell and not read_only:
        return None
    return {"writes": writes, "network": network, "shell": shell, "readOnly": read_only}


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


def compose_approval_brief(event: Any) -> dict[str, Any] | None:
    """Compose the brief for one approval event, or ``None`` when it has no identity.

    Reads only fields the event already carries, and takes its single classification
    from ``task_modes`` rather than re-deriving it: ``risk`` is
    :func:`~personalclaw.task_modes.resolve_effective_risk`, so the channel sees the same
    EFFECTIVE risk the dashboard shows (the event itself carries only the tool's DECLARED
    ``risk_level``, which over-states a read-only ``bash``).

    **Why the screening verdict is not passed separately.** OU-8 measured the
    ``read_only_command`` pass-through as redundant, and it is: ``resolve_effective_risk``
    already routes a readable command through ``is_read_only_bash`` and only ever reports
    ``'safe'`` on positive read evidence — a tool that declares nothing floors at
    ``'caution'``, never ``'safe'``, so nothing arrives on the phone claiming "reads only"
    without a declaration or a screened command behind it.

    ``purpose`` is deliberately NOT duplicated into the brief: it already reaches the
    channel as ``event.tool_purpose``, and copying it would mean redacting the same string
    twice on one payload.
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

    radius = derive_blast_radius(tool, risk=risk)

    brief: dict[str, Any] = {"tool": tool, "risk": risk}
    if radius is not None:
        brief["blastRadius"] = radius
        brief["blastRadiusLine"] = blast_radius_line(radius)
    return brief


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
