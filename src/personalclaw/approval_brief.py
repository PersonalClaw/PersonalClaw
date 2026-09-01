"""OU-9 — the approval brief carried over the core↔channel seam (Contract C2,
``docs/roadmap/plans/ONBOARDING-UX.md``).

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
:mod:`personalclaw.gateway` and are unchanged. Every classification here is
CONSUMED from ``task_modes`` — the one invocation vocabulary — never re-derived. In
particular this module never inspects a command string: deciding whether a command
is read-only is security logic and it already has an owner
(:func:`~personalclaw.task_modes.is_read_only_bash`, reached only via
:func:`~personalclaw.task_modes.classify_invocation`). C2 says E4 if a gap tempts a
change; nothing here tempted one.

── One vocabulary, two languages ────────────────────────────────────────────────
``web/src/pages/chat/approvalMeta.ts`` (OU-7/OU-8) is the same derivation for the
dashboard's chips and the out-of-context toast. Three surfaces must not invent three
words for one claim, so:

* the name evidence is not copied at all — :data:`WRITE_HINTS`,
  :data:`DESTRUCTIVE_HINTS` and :data:`READ_VERB_HINTS` are DERIVED from
  ``task_modes``' own tuples, the same tuples the TypeScript mirrors. Adding a hint
  to ``task_modes`` flows into the brief automatically;
* the four facet labels are the TypeScript's ``FACET_COPY`` verbatim, and
  ``tests/test_approval_brief.py`` parses that file and asserts every label and
  hint list still agrees. A drift becomes a red test, not a third vocabulary.

── Honesty contract (identical to the frontend's) ───────────────────────────────
Every boolean is a POSITIVE claim: ``False`` means "not established", never
"verified absent". So :func:`derive_blast_radius` returns ``None`` when NOTHING was
established, rather than an all-false object — rendered as a line, all-false reads
"no writes, no network, no shell, not read-only", a confident all-clear derived from
zero evidence, and it is worst on the surface least able to check (the phone).
``read_only`` is claimed only on positive evidence and never survives an established
write, so this module can only ever UNDER-claim safety.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.task_modes import (
    _DESTRUCTIVE_NAME_HINTS,
    _MUTATING_NAME_HINTS,
    _READ_VERB_HINTS,
    resolve_effective_risk,
)

logger = logging.getLogger(__name__)

#: The key the brief occupies in ``event.tool_meta``. A channel implementation reads
#: ``event.tool_meta.get(APPROVAL_BRIEF_META_KEY)`` and renders what it can; a channel
#: that ignores it behaves exactly as before. See
#: :meth:`personalclaw.channel_delivery.ChannelDelivery.request_approval`.
APPROVAL_BRIEF_META_KEY = "approval_brief"

# ── Tool-name evidence ───────────────────────────────────────────────────────────
# Derived from `task_modes`' own tuples so the gate and the brief cannot disagree
# about what a name means. The three deltas below are OU-7's, mirrored here with its
# reasons; `tests/test_approval_brief.py` pins each against `approvalMeta.ts`.

#: Runs a command / spawns a process. ``_MUTATING_NAME_HINTS``' exec family, split out
#: because "can run anything" is its own facet. ``terminal``/``shell``/``zsh`` cover the
#: display names ACP agents send as the title. Deliberately NOT ``run``: the
#: ``project_run_*`` tools drive a workflow run, not a shell.
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
#: rest cover MCP tools named by convention. This facet has no ``task_modes``
#: counterpart — the gate never needed to ask "does this leave the host?" — so it is
#: OU-7's list, pinned against the TypeScript by test.
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

#: Destructive verbs, ``task_modes``' tuple itself. Checked FIRST and, like
#: ``infer_risk_from_name``, they win outright: a delete is a write to the world.
DESTRUCTIVE_HINTS: tuple[str, ...] = _DESTRUCTIVE_NAME_HINTS

#: Query/inspection verbs, ``task_modes``' tuple itself. Checked BEFORE the broad write
#: hints for the same reason the backend does it: ``schedule_list`` matches the write
#: fragment "schedule" but is plainly a read.
READ_VERB_HINTS: tuple[str, ...] = _READ_VERB_HINTS

#: Hints that belong to another facet (or to none) and so must not also mean "writes".
#: Every destructive verb is excluded by DERIVATION from :data:`DESTRUCTIVE_HINTS` rather
#: than by name: this used to hand-list ``delete``/``remove`` with the reason "already in
#: DESTRUCTIVE_HINTS", which was true of all six and spelled for two. When #2118 widened the
#: gate's tuple, the four it added (``destroy``, ``drop_``, ``purge``, ``forget``) flowed
#: into :data:`WRITE_HINTS` and drifted from the TypeScript mirror — a hand-listed subset of
#: a derived set is the same defect one layer up. ``exec``/``spawn`` moved to
#: :data:`SHELL_HINTS`; ``run`` is dropped outright per :data:`SHELL_HINTS`' note.
_NOT_A_WRITE_HINT = frozenset(DESTRUCTIVE_HINTS) | {"run", "exec", "spawn"}

#: Other writing verbs — ``_MUTATING_NAME_HINTS`` minus the hints re-homed above, plus
#: ``remember``. ``remember`` is the one deliberate divergence from ``task_modes``:
#: ``memory_remember`` durably persists a lesson, so it writes, but the gate's tuple
#: carries no ``remember`` token. Adding it THERE is a change to live risk inference and
#: is not this atom's to make (C2: E4), so the divergence lives here — and the
#: conservative ``read_only`` guard keeps the two consistent where it matters: an
#: established write never claims read-only, whatever the risk says.
WRITE_HINTS: tuple[str, ...] = tuple(
    h for h in _MUTATING_NAME_HINTS if h not in _NOT_A_WRITE_HINT
) + ("remember",)

#: Does a risk level positively establish that the call is a read?
#:
#: Consumed, not invented: :func:`~personalclaw.task_modes.resolve_effective_risk`
#: reaches ``'safe'`` only through a read-only bash invocation, a declared-SAFE native
#: tool, or a positive read-only ACP ``tool_kind`` — so EFFECTIVE-safe is already derived
#: FROM read-only-ness. ``'caution'``/``'destructive'`` say a call has side effects but
#: not WHICH facet, so they establish nothing here. A level this build has never heard of
#: is no evidence, not a read.
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

    Mirrors ``approvalMeta.ts``' ``normalizeToolName`` and, behind it,
    ``infer_risk_from_name``'s ``mcp/<server>/`` strip. Lowercasing also lets ACP
    display titles ("Terminal", "Read") match.
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

    # Name-verb precedence, mirroring `infer_risk_from_name`: destructive wins outright,
    # then a read verb short-circuits, then the broad write hints.
    writes = False
    read_verb = False
    if _has_any(name, DESTRUCTIVE_HINTS):
        writes = True
    elif _has_any(name, READ_VERB_HINTS):
        read_verb = True
    elif _has_any(name, WRITE_HINTS):
        writes = True

    # `read_only` needs positive evidence and never rides over an established write. The
    # screening verdict is the strongest signal (it inspected the actual command), then
    # EFFECTIVE-safe risk, then a read-verb name.
    read_only = False
    if read_only_command is True:
        read_only = True
    elif read_only_command is not False:
        read_only = _risk_establishes_read_only(risk) or read_verb
    if writes:
        read_only = False

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
    ``'safe'`` on positive read evidence — an unknown name floors at ``'caution'``, never
    ``'safe'``. Feeding :func:`~personalclaw.task_modes.classify_invocation` in as a second
    input is worse than redundant: its *name-fallback* branch answers ``READ_ONLY`` for
    ANY name carrying no mutating hint, so every unrecognized tool would arrive on the
    phone claiming "reads only" — a positive claim minted from no evidence, which is the
    one thing the honesty contract forbids. Deny-by-default holds for the GATE that
    consumes that verdict; it does not make the verdict evidence for a brief.

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
