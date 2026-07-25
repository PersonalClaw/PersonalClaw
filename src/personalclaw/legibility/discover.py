"""Discover — a curated tour of what PersonalClaw can do for you (Platform-Legibility §6).

The dashboard "Discover" section and the dedicated Discover hub read this. It answers
one question, from the *user's* side: *which parts of this system have I not tried yet?*
— then points at them.

Deliberately NOT tool-derived. The tool surface is an implementation detail the user
is never meant to drive by hand, so a "you haven't called `knowledge_add` yet" nudge is
noise. Instead this is a **hand-authored catalog** of the system's user-facing areas
(Chat, Goal loops, Tasks, Projects, Knowledge, Memory, Automation, Inbox, Skills, Apps),
each a one- or two-sentence lesson with a deep link into the page that owns it.

Two ways a tip leaves the feed, both hide-only:

* **Dismiss** — an explicit X. Persisted forever in ``entity_settings/legibility.json``
  (the notifications-settings pattern), so it never resurfaces.
* **Auto-hide when used** — once the user has actually engaged that area, the tip drops
  on its own. "Engaged" is a cheap read of state that already exists (a chat session on
  disk, a knowledge item, a scheduled job…), computed by :func:`compute_engaged`.

**Propose-don't-write (the soul guardrail, unchanged from §6):** every tip only *points*
(a deep link into an existing page) and *hides* (dismiss / auto-hide). Nothing here ever
enables or configures a feature on the user's behalf — the human acts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# entity_settings key + field for dismissals (the notifications.json pattern).
_ENTITY = "legibility"
_DISMISSED_FIELD = "dismissed_discover_tips"


@dataclass(frozen=True)
class DiscoverTip:
    """One hand-authored lesson pointing at a user-facing part of the system."""

    id: str  # stable slug, e.g. "chat"
    area: str  # the group it belongs to, e.g. "Talk to it"
    title: str
    lesson: str  # one or two sentences of plain guidance
    try_it: dict[str, Any] = field(default_factory=dict)  # {route, query, label}
    # Key into :func:`compute_engaged`; when that reads True the tip auto-hides.
    # "" means no cheap engagement signal exists — the tip is dismiss-only.
    engaged_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "area": self.area,
            "title": self.title,
            "lesson": self.lesson,
            "try_it": dict(self.try_it),
        }


def _try(route: str, label: str, query: dict[str, str] | None = None) -> dict[str, Any]:
    return {"route": route, "query": dict(query or {}), "label": label}


# ── The curated catalog ──────────────────────────────────────────────────────
# Ordered by area, then by the order a new user would naturally meet each part.
# Every ``route`` is a real SPA destination (App.tsx ROUTABLE); Memory has no page
# of its own, so its tip points at the Memory settings panel that owns it.
CATALOG: tuple[DiscoverTip, ...] = (
    DiscoverTip(
        id="chat",
        area="Talk to it",
        title="Start a conversation",
        lesson=(
            "Chat is the front door — ask a question, hand over a task, or think out "
            "loud, and PersonalClaw picks the tools and agents it needs on its own."
        ),
        try_it=_try("chat/new", "Open Chat"),
        engaged_key="chat",
    ),
    DiscoverTip(
        id="loops",
        area="Let it work",
        title="Hand off a goal to run on its own",
        lesson=(
            "A goal loop keeps working toward an outcome across many turns while you're "
            "away, checking in only when it needs you. Launch one from a project."
        ),
        try_it=_try("loops", "See goal loops"),
        engaged_key="loops",
    ),
    DiscoverTip(
        id="automation",
        area="Let it work",
        title="Automate on a schedule or an event",
        lesson=(
            "Triggers run a prompt on a clock or when something happens — a morning "
            "briefing, a nightly digest, a reaction to a new file. Set one and forget it."
        ),
        try_it=_try("triggers", "Set up a trigger"),
        engaged_key="automation",
    ),
    DiscoverTip(
        id="tasks",
        area="Stay organized",
        title="Track work as tasks",
        lesson=(
            "Tasks give long-running or multi-step work a home with state you can watch — "
            "and PersonalClaw can pick them up and drive them for you."
        ),
        try_it=_try("tasks", "Open Tasks"),
        engaged_key="tasks",
    ),
    DiscoverTip(
        id="projects",
        area="Stay organized",
        title="Group related work into a project",
        lesson=(
            "A project bundles a workspace, its context, and its loops so everything about "
            "one effort stays together — and agents inherit that context automatically."
        ),
        try_it=_try("projects", "Open Projects"),
        engaged_key="projects",
    ),
    DiscoverTip(
        id="inbox",
        area="Stay organized",
        title="Route messages into one inbox",
        lesson=(
            "The Inbox gathers what arrives from your connected sources into one triage "
            "feed, so PersonalClaw can act on it instead of it being scattered."
        ),
        try_it=_try("inbox", "Open Inbox"),
        engaged_key="inbox",
    ),
    DiscoverTip(
        id="knowledge",
        area="Give it context",
        title="Build a knowledge base it can draw on",
        lesson=(
            "Save documents, notes, and facts to the knowledge base and PersonalClaw "
            "retrieves the relevant pieces on its own the next time they matter."
        ),
        try_it=_try("knowledge", "Open Knowledge"),
        engaged_key="knowledge",
    ),
    DiscoverTip(
        id="memory",
        area="Give it context",
        title="See what it remembers about you",
        lesson=(
            "PersonalClaw remembers preferences and facts across conversations. Review "
            "and curate that memory so it keeps working from an accurate picture of you."
        ),
        try_it=_try("settings/memory", "Review Memory"),
        engaged_key="memory",
    ),
    DiscoverTip(
        id="skills",
        area="Extend it",
        title="Teach it a reusable skill",
        lesson=(
            "A skill is a saved way of doing something PersonalClaw can reach for by name "
            "later — codify a workflow once instead of re-explaining it every time."
        ),
        try_it=_try("skills", "Browse Skills"),
        engaged_key="skills",
    ),
    DiscoverTip(
        id="apps",
        area="Extend it",
        title="Install an app from the Store",
        lesson=(
            "Apps add whole capabilities — new providers, channels, and UI surfaces — from "
            "the Store, each asking only for the permissions it needs up front."
        ),
        try_it=_try("apps", "Open the Store"),
        engaged_key="apps",
    ),
)


# ── engagement signals (auto-hide "when used") ───────────────────────────────
# Each check is a cheap read of state that ALREADY exists — one dir listing, one
# JSON read, or one SQLite COUNT — never a provider/network call. Every check is
# wrapped so a failure reads as "not engaged" and never breaks the payload.


def _engaged_chat(state: Any) -> bool:
    cl = getattr(state, "conversation_log", None)
    return bool(cl and len(cl.list_sessions()) > 0)


def _engaged_loops(_state: Any) -> bool:
    from personalclaw.loop import store

    return len(store.list_all()) > 0


def _engaged_automation(state: Any) -> bool:
    from personalclaw.config.loader import config_dir
    from personalclaw.event_triggers import EventTriggerStore

    if EventTriggerStore(config_dir() / "event_triggers.json").load():
        return True
    crons = getattr(state, "crons", None)
    return bool(crons and crons.list_jobs(include_disabled=True))


def _engaged_tasks(_state: Any) -> bool:
    from personalclaw.config.loader import config_dir

    tasks_dir = config_dir() / "tasks"
    if not tasks_dir.exists():
        return False
    return any(p.is_file() and not p.name.startswith("_") for p in tasks_dir.glob("*.json"))


def _engaged_projects(_state: Any) -> bool:
    from personalclaw.tasks.hierarchy import HierarchyStore

    # A fresh instance always has the default project; "engaged" means the user
    # created one of their own beyond it.
    return any(not p.is_default_project() for p in HierarchyStore().list_projects())


def _engaged_inbox(_state: Any) -> bool:
    from personalclaw.inbox import InboxStore

    store = InboxStore()
    store.load()
    return bool(store.items)


def _engaged_knowledge(state: Any) -> bool:
    ks = getattr(state, "knowledge_store", None)
    return bool(ks and ks.get_stats().get("items", 0) > 0)


def _engaged_memory(state: Any) -> bool:
    # Read the ALREADY-initialized provider off the context builder — never trigger
    # the standalone-store creation / embed-fn autowire path (that has side effects).
    cb = getattr(state, "context_builder", None)
    mem = getattr(cb, "memory", None) if cb else None
    vs = getattr(mem, "vector_store", None) if mem else None
    if not vs:
        return False
    stats = vs.memory_stats()
    return (stats.get("semantic_active", 0) + stats.get("episodic_active", 0)) > 0


def _engaged_skills(_state: Any) -> bool:
    from personalclaw.skills.usage import SkillUsageStore

    return bool(SkillUsageStore().all_usage())


def _engaged_apps(_state: Any) -> bool:
    from personalclaw.apps.manager import list_apps

    return any(a.get("origin") != "builtin" for a in list_apps())


_ENGAGEMENT_CHECKS: dict[str, Callable[[Any], bool]] = {
    "chat": _engaged_chat,
    "loops": _engaged_loops,
    "automation": _engaged_automation,
    "tasks": _engaged_tasks,
    "projects": _engaged_projects,
    "inbox": _engaged_inbox,
    "knowledge": _engaged_knowledge,
    "memory": _engaged_memory,
    "skills": _engaged_skills,
    "apps": _engaged_apps,
}


def compute_engaged(state: Any = None) -> dict[str, bool]:
    """Which feature areas the user has already engaged (for auto-hide).

    Runs every cheap per-area check, each isolated so one failure can't blank the
    rest. A key reads ``True`` when that area shows real prior use.
    """
    engaged: dict[str, bool] = {}
    for key, check in _ENGAGEMENT_CHECKS.items():
        try:
            engaged[key] = bool(check(state))
        except Exception:  # noqa: BLE001 - advisory signal; a miss just keeps the tip
            logger.debug("discover engagement check %r failed", key, exc_info=True)
            engaged[key] = False
    return engaged


# ── dismissal persistence (entity_settings/legibility.json) ──────────────────


def load_dismissed() -> set[str]:
    """The set of dismissed tip ids (empty on any read error)."""
    from personalclaw.providers.entity_routes import _load_entity_settings

    raw = _load_entity_settings(_ENTITY)
    ids = raw.get(_DISMISSED_FIELD, [])
    return {str(x) for x in ids} if isinstance(ids, list) else set()


def dismiss(tip_id: str) -> set[str]:
    """Persist *tip_id* as dismissed; returns the full dismissed set."""
    from personalclaw.providers.entity_routes import (
        _load_entity_settings,
        _save_entity_settings,
    )

    current = _load_entity_settings(_ENTITY)
    existing = current.get(_DISMISSED_FIELD, [])
    ids = {str(x) for x in existing} if isinstance(existing, list) else set()
    ids.add(str(tip_id))
    current[_DISMISSED_FIELD] = sorted(ids)
    _save_entity_settings(_ENTITY, current)
    return ids


# ── the payload ──────────────────────────────────────────────────────────────


def select_visible(*, dismissed: set[str], engaged: dict[str, bool]) -> list[DiscoverTip]:
    """The catalog minus dismissed tips and minus areas already engaged.

    Order follows :data:`CATALOG` (curated), so the dashboard spotlight and the hub
    present the same stable sequence.
    """
    return [
        tip
        for tip in CATALOG
        if tip.id not in dismissed and not (tip.engaged_key and engaged.get(tip.engaged_key))
    ]


def _group_by_area(tips: list[DiscoverTip]) -> list[dict[str, Any]]:
    """Preserve catalog order while collapsing consecutive tips into area groups."""
    areas: list[dict[str, Any]] = []
    for tip in tips:
        if not areas or areas[-1]["area"] != tip.area:
            areas.append({"area": tip.area, "tips": []})
        areas[-1]["tips"].append(tip.to_dict())
    return areas


def compute_discover(state: Any = None) -> dict[str, Any]:
    """The payload for ``GET /api/legibility/discover``.

    Honors the ``legibility.discover_tips`` kill switch server-side (a disabled
    instance returns ``enabled: false`` with no tips), then returns the visible
    curated tips grouped by area for the hub, alongside counts the dashboard uses.
    """
    from personalclaw.config.loader import AppConfig

    if not AppConfig.load().legibility.discover_tips:
        return {"enabled": False, "areas": [], "visible_count": 0, "total": len(CATALOG)}

    dismissed = load_dismissed()
    engaged = compute_engaged(state)
    visible = select_visible(dismissed=dismissed, engaged=engaged)
    return {
        "enabled": True,
        "areas": _group_by_area(visible),
        "visible_count": len(visible),
        "total": len(CATALOG),
    }
