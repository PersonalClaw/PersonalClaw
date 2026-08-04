"""Dynamic tool-group activation (CONTEXT-ECONOMY §5).

The tool surface grows with every installed app and MCP server, and every
enabled tool's schema rides every model turn. Groups partition that surface so
**inactive groups cost (almost) zero context**: their schemas leave the tool
block and each is replaced by ONE catalog stub line.

Three properties make this safe rather than a hidden capability cliff — the
fail-open triad (mirroring :mod:`agents.native.tool_retrieval`'s stance that "a
hidden tool is a capability regression, not a safety risk"):

1. **Selection ≠ dispatch.** Deactivating a group removes SCHEMAS only. The
   runtime's ``_tool_index`` dispatch map is never filtered, so every tool stays
   callable by name — a model that ignores groups entirely still works.
2. **Stubs, not silence.** Each inactive group renders as one line naming its
   tool count and a few names, so the capability is visible at ~15 tokens
   instead of ~7 schemas.
3. **Search reaches across groups.** ``tool_search`` ranks the FULL catalog and
   annotates a hit in an inactive group with the activation step, so any tool is
   one search (or one direct call) away.

Group activation is therefore **context economy, not a security boundary**.
Structural tool DENIAL stays where it already lives: user disable
(:mod:`tool_providers.tool_prefs`) and the unattended interactive-strip, both of
which run BEFORE grouping in the runtime's assembly chain.

**Groups are derived, not hand-maintained** — one group per registered tool
provider, which is the partition the registry already keeps. The reserved
``core`` group (:data:`CORE_GROUP`) is ``always_on``: it holds the platform
providers plus every :data:`~tool_providers.tool_prefs.CORE_LOCKED` tool name
(wherever it lives) plus the runtime's synthetic meta-tools, so the primitives an
agent can't recover from losing are never deactivatable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# The reserved always-on group: platform primitives + core-locked tools + the
# runtime's synthetic meta-tools. Can never be deactivated.
CORE_GROUP = "core"

# Tool providers whose whole surface belongs to the core group: the in-process
# ``personalclaw-core`` module (skills/notify/wait/loop control) and the
# cwd-coupled platform bundle (filesystem + shell + tool_result_get).
_CORE_PROVIDERS: frozenset[str] = frozenset(
    {
        "personalclaw-core",
        "personalclaw-filesystem",
    }
)


@dataclass(frozen=True)
class ToolGroup:
    """One activatable slice of the tool surface (provider-grain).

    ``instructions`` is returned to the model when the group is newly activated —
    usage guidance arrives exactly when the tools do. ``capability`` is an
    optional gate name: a group whose capability doesn't resolve is not
    offerable (per-capability gating, §5.5 — the field ships now, the probe
    lands with it).
    """

    name: str
    display: str
    instructions: str = ""
    always_on: bool = False
    capability: str = ""
    # Tool names in this group, in assembly order (stable ⇒ stable serialization).
    tools: tuple[str, ...] = field(default_factory=tuple)


# Per-group usage guidance, returned on activation. Keyed by derived group name;
# an unknown group (a new MCP server / app provider) simply has none, which is
# fine — its stub line and tool descriptions carry the meaning.
_GROUP_INSTRUCTIONS: dict[str, str] = {
    "schedule": (
        "Scheduling is now active: create/list/cancel reminders and cron-style "
        "recurring runs. Prefer one recurring schedule over many one-offs."
    ),
    "artifacts": (
        "Artifacts are now active: save durable, versioned outputs (documents, "
        "widgets, code) the user can revisit — not scratch notes."
    ),
    "workflows": (
        "Workflows/SOPs are now active: look for an existing SOP before "
        "improvising a multi-step procedure, and record a reusable one when you "
        "finish something worth repeating."
    ),
    "memory": (
        "Memory is now active: recall before assuming, and record durable "
        "lessons (not transient task state)."
    ),
    "subagents": (
        "Subagents are now active: spawn one for independent, parallelizable "
        "work; keep the synthesis in this session."
    ),
    "knowledge": (
        "The knowledge library is now active: search it before searching the "
        "web, and file durable references you'd want again."
    ),
    "tasks": (
        "Tasks are now active: the Project → task-list → task hierarchy. Read "
        "what's ready before creating more."
    ),
    "inbox": "The inbox is now active: post items that need the user's attention.",
    "ui-docs": (
        "The design-system docs are now active: search the kit for an existing "
        "primitive before writing new UI."
    ),
    "web": "Web tools are now active: search and fetch live pages.",
}

# Display labels for the derived groups we know by name. Unknown groups fall back
# to a title-cased form of the derived name.
_GROUP_DISPLAY: dict[str, str] = {
    CORE_GROUP: "Core",
    "schedule": "Schedule",
    "artifacts": "Artifacts",
    "workflows": "Workflows",
    "memory": "Memory",
    "subagents": "Subagents",
    "knowledge": "Knowledge",
    "tasks": "Tasks",
    "inbox": "Inbox",
    "ui-docs": "Design System",
    "web": "Web",
    "app-routes": "App Routes",
}


def group_name_for_provider(provider: str) -> str:
    """Derive a group name from a tool-provider instance name.

    The registry's provider names are already the partition; this only shortens
    them into stable, model-facing labels::

        personalclaw-core            → core       (a core provider)
        personalclaw-filesystem      → core       (a core provider)
        personalclaw-knowledge-tools → knowledge
        mcp-tools:github             → mcp:github
        openai-tools:work            → openai:work
        some-app-provider            → some-app-provider

    An empty/unknown provider lands in ``other`` so nothing is silently dropped.
    """
    name = (provider or "").strip()
    if not name:
        return "other"
    if name in _CORE_PROVIDERS:
        return CORE_GROUP
    if name.startswith("personalclaw-"):
        name = name[len("personalclaw-") :]
    # "<kind>-tools:<instance>" → "<kind>:<instance>"; "<kind>-tools" → "<kind>".
    name = name.replace("-tools:", ":")
    if name.endswith("-tools"):
        name = name[: -len("-tools")]
    return name or "other"


def group_of_tool(tool: Any, *, provider: str = "") -> str:
    """The group a tool definition belongs to.

    ``provider`` overrides the def's own tag with the key the caller already
    resolved (the runtime passes the same key its disable gate used, so a
    provider that forgot to stamp its tools still groups correctly instead of
    collapsing into ``other``).

    A :data:`~tool_providers.tool_prefs.CORE_LOCKED` tool is ALWAYS in ``core``
    regardless of which provider surfaces it — those are the names platform
    features invoke directly and an agent can't recover from losing, so they
    must never be deactivatable.
    """
    from personalclaw.tool_providers.tool_prefs import is_locked

    name = getattr(tool, "name", "") or ""
    if is_locked(name):
        return CORE_GROUP
    return group_name_for_provider(provider or getattr(tool, "provider", "") or "")


def partition(defs: list[Any], *, provider_of: dict[str, str] | None = None) -> list[ToolGroup]:
    """Partition tool defs into groups, ``core`` first then first-appearance order.

    Order is derived from the assembly order of ``defs``, so the same providers
    in the same order always yield the same groups with the same tool sequences —
    which is what makes an identical active set serialize to identical bytes.
    ``provider_of`` optionally supplies each tool's resolved provider key.
    """
    provider_of = provider_of or {}
    buckets: dict[str, list[str]] = {}
    for d in defs:
        name = getattr(d, "name", "") or ""
        buckets.setdefault(group_of_tool(d, provider=provider_of.get(name, "")), []).append(name)
    out: list[ToolGroup] = []
    names = list(buckets)
    # core first (it's the always-on anchor), then first-appearance order.
    names.sort(key=lambda n: (n != CORE_GROUP,))
    for name in names:
        out.append(
            ToolGroup(
                name=name,
                display=_GROUP_DISPLAY.get(name, name.replace("-", " ").replace(":", ": ").title()),
                instructions=_GROUP_INSTRUCTIONS.get(name, ""),
                always_on=(name == CORE_GROUP),
                capability=_GROUP_CAPABILITY.get(name, ""),
                tools=tuple(buckets[name]),
            )
        )
    return out


# ── per-capability gating (§5.5) ────────────────────────────────────────────
# A group may declare the capability its tools NEED to do anything. When that
# capability doesn't resolve, the group is not OFFERABLE: it is neither active nor
# stub-listed, so the model is never shown tools that cannot work.
#
# Two probe kinds, both cheap and side-effect-free (this runs at assembly time and
# again on refresh_toolset()):
#   * "model:<use_case>" — a model-shaped capability, via the no-instantiate
#     `provider_bridge.can_resolve_use_case` probe (the same one behind the
#     onboarding "needs a model" nudge, so the two never disagree).
#   * "tool_provider:<name>" / "search_provider:<name>" — registry presence.
#
# Deliberately sparse: only groups whose tools are genuinely inert without a
# binding. A group with no entry is always offerable. `memory` is NOT gated — its
# lesson store works without an embedder (recall degrades, it doesn't break).
_GROUP_CAPABILITY: dict[str, str] = {
    # Subagent spawns inference through a ModelProvider; with no model resolvable
    # every spawn fails at the first turn, so the tools are worse than useless.
    "subagents": "model:orchestration",
}


def capability_available(capability: str) -> bool:
    """Whether a group's declared ``capability`` resolves right now.

    Fail-OPEN on every uncertainty (unknown probe kind, probe error, empty
    capability): an unavailable group merely costs context, but a wrongly-hidden
    group is the capability regression this whole module promises not to cause.
    """
    spec = (capability or "").strip()
    if not spec:
        return True
    kind, _, value = spec.partition(":")
    try:
        if kind == "model":
            from personalclaw.providers.provider_bridge import can_resolve_use_case

            return bool(can_resolve_use_case(value or "chat"))
        if kind in ("tool_provider", "search_provider"):
            if kind == "tool_provider":
                from personalclaw.tool_providers.registry import get_provider

                return get_provider(value) is not None
            from personalclaw.search_providers.registry import get_provider as get_search

            return get_search(value) is not None
    except Exception:
        logger.debug("groups: capability probe %r failed — treating as available", spec)
        return True
    logger.debug("groups: unknown capability probe kind %r — treating as available", kind)
    return True


def offerable(group: ToolGroup) -> bool:
    """Whether ``group`` may be activated or stub-listed at all (§5.5).

    An always-on group is always offerable — ``core`` holds the primitives an
    agent can't recover from losing, so no probe may remove it.
    """
    if group.always_on:
        return True
    return capability_available(group.capability)


# ── per-surface defaults (§5.4) ─────────────────────────────────────────────
# Which groups start active for a given SURFACE — keyed by the session's model
# axis, the classifier already threaded through provider resolution
# (MODEL-USE-CASES-V2): "chat"/"code_tools"/"reasoning" for human-watched chat,
# "background" for the lite background session, "loops" for loop workers,
# "orchestration" for subagent spawns.
#
# A surface with NO entry (chat and friends) keeps EVERY group active — today's
# behavior exactly, so enabling the feature is a no-op for interactive chat.
# ``["*"]`` is an explicit spelling of "all groups".
DEFAULT_GROUP_DEFAULTS: dict[str, list[str]] = {
    # The lite background session (titles, tags, digests, consolidation) barely
    # touches tools; it does read/write memory.
    "background": [CORE_GROUP, "memory"],
    # Subagent spawns: focused, short-lived work under a parent's supervision.
    "orchestration": [CORE_GROUP, "memory"],
    # Loop workers: procedure-driven autonomous runs that fan work out.
    "loops": [CORE_GROUP, "workflows", "subagents"],
}

ALL_GROUPS = "*"


def groups_enabled() -> bool:
    """Whether group activation is on (``tools.groups_enabled``).

    Fail-open: any config problem reads as DISABLED, i.e. every group active —
    the same direction the whole module fails.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().tools.groups_enabled)
    except Exception:
        logger.debug("groups: config unreadable — treating activation as off", exc_info=True)
        return False


def resolve_default_groups(surface: str) -> set[str] | None:
    """The groups that start active for ``surface``, or ``None`` for "all active".

    ``None`` is the fail-open answer and the answer for every surface without a
    configured default (notably interactive chat) — the runtime then skips group
    filtering entirely, so the tool block is byte-identical to having no groups.
    """
    if not groups_enabled():
        return None
    defaults: dict[str, list[str]] = dict(DEFAULT_GROUP_DEFAULTS)
    try:
        from personalclaw.config.loader import AppConfig

        configured = AppConfig.load().tools.group_defaults or {}
        for key, value in configured.items():
            if isinstance(key, str) and isinstance(value, list):
                defaults[key] = [str(v) for v in value if isinstance(v, str)]
    except Exception:
        logger.debug("groups: group_defaults unreadable — using built-in defaults", exc_info=True)
    wanted = defaults.get((surface or "").strip() or "chat")
    if not wanted or ALL_GROUPS in wanted:
        return None
    # core is always on, so it's implied even if a config entry forgets it.
    return {CORE_GROUP, *wanted}
