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
# Every ``route`` is a real SPA destination (App.tsx ROUTABLE) that the SPA does not
# redirect away from — a tip must LAND where its label promised; Memory has no page
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
        # ``loops/history`` — NOT the bare ``loops`` route, which LoopsSection
        # unconditionally redirects to the ``loop`` composer (it survives only as the
        # transient plan-review address). A "see what exists" tip that lands on a
        # blank "What do you want to accomplish?" form answers a question the user
        # didn't ask. The label says "loops", not "goal loops", because the list is
        # every non-code kind (goal, general, design) — the destination is wider than
        # the lesson, and the label must not promise the narrower thing.
        try_it=_try("loops/history", "Open Loops"),
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


#: Every tip id the catalog defines — the complete set of ids that mean anything.
#:
#: DERIVED from :data:`CATALOG` rather than hand-listed, so it cannot drift from it: a tip
#: added or renamed above joins this set by construction. A hand-maintained second copy of
#: these ten strings is exactly the kind of pair that goes stale.
TIP_IDS: frozenset[str] = frozenset(tip.id for tip in CATALOG)


class UnknownTipError(ValueError):
    """A dismissal named an id the catalog does not define.

    Such an id is *inert*: :func:`select_visible` only ever compares against catalog ids, so
    persisting one can never hide anything. It is junk in a settings file that
    :func:`load_dismissed` re-reads on every Discover request, and nothing ever removes it.
    """


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
    """Whether the USER has any automation — clock, file watch, event, the lot.

    🔴 Read the unified store (S111). This asked `state.crons`, which describes only the legacy
    `crons.json` — a file nothing has written since S108. Measured: a home with a store trigger read
    as NOT engaged with automation, so the legibility surface told a user with live automations that
    they had none.

    🔴 Exclude `created_by="system"` rows. The gateway registers the notification-digest
    trigger at every boot, so counting every row made this true before the user's first
    interaction — the automation tip was unreachable on 100% of installs. Agent-created
    triggers still count: an agent writes one inside a user conversation, which is the
    user automating. A data-event trigger is a row in the same store, so it counts the same way.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.triggers.store import TriggerStore

    try:
        rows = TriggerStore(base_dir=config_dir()).load()
        return any(lt.trigger.created_by != "system" for lt in rows)
    except Exception:  # noqa: BLE001 - a legibility probe must never raise
        return False


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
    return any(not p.is_builtin_project() for p in HierarchyStore().list_projects())


def _engaged_inbox(_state: Any) -> bool:
    """Whether the user has ACTED on the Inbox, not whether the system filled it.

    The Inbox exists to receive system-generated items (proposals, digests,
    needs-input), so "the store is non-empty" is a near-tautology on any instance
    that has run for a while — it hid the tip the moment the system produced its
    first item. Engagement is a user gesture: an item moved off PENDING (seen /
    dismissed / handled / sent), or favorited. FILTERED is excluded — the
    verification pass writes it with no user involved.
    """
    from personalclaw.inbox import InboxStore, ItemStatus

    store = InboxStore()
    store.load()
    system_states = (ItemStatus.PENDING.value, ItemStatus.FILTERED.value)
    return any(
        i.status not in system_states or getattr(i, "favorited", False)
        for i in store.items.values()
    )


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
    # Auto-consolidation machine-writes rows constantly, so active counts said
    # "engaged" on a home whose Memory panel was never opened. The tip's verb is
    # "review and curate": count rows the human explicitly wrote or tombstoned
    # (source='user_explicit' — the memory editor's documented stamp).
    return stats.get("user_curated", 0) > 0


def _engaged_skills(_state: Any) -> bool:
    """Whether a skill exists beyond the bundled baseline — the tip's verb is "teach".

    The usage store this read counted PASSIVE turn-time injection: the surfacer
    auto-picks bundled skills for ordinary messages, so one plain chat message hid
    "Teach it a reusable skill" on a home where the user taught nothing. Mirror
    `_engaged_apps` instead: engaged means a skill the install didn't ship —
    authored, learned, or imported.
    """
    from personalclaw.skills.loader import _BUILTIN_SKILLS_DIR, SkillsLoader

    try:
        bundled = {p.name for p in _BUILTIN_SKILLS_DIR.iterdir() if p.is_dir()}
    except OSError:
        bundled = set()
    return any(s["key"] not in bundled for s in SkillsLoader().list_skills())


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
    """The set of dismissed tip ids (empty on any read error).

    Narrowed to :data:`TIP_IDS`, because that is what a *tip id* is. The file is on the
    user's disk and was written by older builds that accepted anything, so anything else in
    it is junk the reader must not carry — :func:`dismiss` prunes it on the next write.
    """
    from personalclaw.providers.entity_routes import _load_entity_settings

    # Fail-OPEN on a discarded read (`or {}`): a store we cannot read means nothing is
    # dismissed, so the tips come back. Re-showing a tip is the cheapest failure on this
    # surface — there is nothing here to destroy.
    raw = _load_entity_settings(_ENTITY) or {}
    ids = raw.get(_DISMISSED_FIELD, [])
    if not isinstance(ids, list):
        return set()
    return {str(x) for x in ids} & TIP_IDS


def dismiss(tip_id: str) -> set[str]:
    """Persist *tip_id* as dismissed; returns the full dismissed set.

    Refuses an id the catalog does not define (:class:`UnknownTipError`) — default-deny
    against :data:`TIP_IDS` rather than a shape check, because the valid set is *closed and
    known*, so nothing outside it can ever be legitimate. An accepted id needs no length or
    character bound as a consequence: the longest one the catalog defines is ten characters.

    Also prunes: an id no longer in the catalog is dropped from the stored list on the way
    through. That clears junk written by a build that accepted anything, and drops the
    dismissal of a tip that has since been retired — which is meaningless either way, since
    :func:`select_visible` can only act on ids the catalog still defines.
    """
    from personalclaw.providers.entity_routes import (
        _load_entity_settings,
        _save_entity_settings,
    )

    if tip_id not in TIP_IDS:
        raise UnknownTipError(tip_id)

    # Fail-OPEN on a discarded read (`or {}`): the user clicked the X, so the dismissal has to
    # persist, and an unreadable store has no dismissals left to preserve. The write replaces
    # the unusable file with a valid one, which is a repair rather than a loss.
    current = _load_entity_settings(_ENTITY) or {}
    existing = current.get(_DISMISSED_FIELD, [])
    stored = {str(x) for x in existing} if isinstance(existing, list) else set()
    ids = (stored | {tip_id}) & TIP_IDS
    if pruned := stored - ids:
        logger.info("discover: dropping %d dismissed id(s) the catalog no longer has", len(pruned))
    current[_DISMISSED_FIELD] = sorted(ids)
    _save_entity_settings(_ENTITY, current)
    return ids


def clear_dismissed() -> int:
    """Drop every dismissal; returns how many stored ids were removed.

    The counterpart :func:`dismiss` never had (#452). Dismiss is one click on an X with no
    confirm, and until this existed the only onboarding surface the product has could be
    permanently removed by a reflex — "persisted forever" was literal, with no API, no list
    and no reset behind it.

    Clear-ALL, deliberately, and no per-id restore: the user cannot see *which* ids are
    stored (that list is out of scope on #452), so a per-id control would ask them to pick
    from a set they were never shown. Clearing the field is the whole operation.

    Counts what was STORED, not what the catalog defines, so junk an older build persisted
    is reported as removed too — it really was removed. The write drops the field's contents
    wholesale rather than filtering, so it cannot leave residue behind for a later read to
    narrow away.
    """
    from personalclaw.providers.entity_routes import (
        _load_entity_settings,
        _save_entity_settings,
    )

    # Fail-OPEN on a discarded read (`or {}`): an unreadable store reports nothing stored and
    # therefore writes nothing at all (the early return below), so this cannot overwrite a file
    # it could not read.
    current = _load_entity_settings(_ENTITY) or {}
    existing = current.get(_DISMISSED_FIELD, [])
    removed = len(existing) if isinstance(existing, list) else 0
    if not removed:
        # Nothing stored: do not write. A no-op PUT would still rewrite the settings file,
        # and this route is reachable from a button whose own gate can be stale by a click.
        return 0
    current[_DISMISSED_FIELD] = []
    _save_entity_settings(_ENTITY, current)
    logger.info("discover: cleared %d dismissal(s) at the user's request", removed)
    return removed


# ── the payload ──────────────────────────────────────────────────────────────


def _auto_hidden(tip: DiscoverTip, engaged: dict[str, bool]) -> bool:
    """Whether *tip* drops on its own because the user has already used its area.

    The second of the two independent hide reasons, factored out so :func:`select_visible`
    and :func:`count_restorable` share ONE predicate. They must agree exactly — a restore
    count that disagreed with what the feed then admits is the inert-button bug in a
    different place — and agreeing by construction beats agreeing by inspection.
    """
    return bool(tip.engaged_key and engaged.get(tip.engaged_key))


def select_visible(*, dismissed: set[str], engaged: dict[str, bool]) -> list[DiscoverTip]:
    """The catalog minus dismissed tips and minus areas already engaged.

    Order follows :data:`CATALOG` (curated), so the dashboard spotlight and the hub
    present the same stable sequence.
    """
    return [tip for tip in CATALOG if tip.id not in dismissed and not _auto_hidden(tip, engaged)]


def count_restorable(*, dismissed: set[str], engaged: dict[str, bool]) -> int:
    """How many tips clearing the dismissals would actually bring back.

    The dismissed tips that are not ALSO auto-hidden — i.e. exactly the ones
    :func:`select_visible` would admit once the dismissal is gone. See
    :func:`compute_discover` for why the restore control gates on this and not on the size
    of the dismissed set.
    """
    return sum(1 for tip in CATALOG if tip.id in dismissed and not _auto_hidden(tip, engaged))


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

    ``dismissed_count`` is here because ``visible_count: 0`` has two causes the hub's
    empty state was congratulating identically (#452): a user who USED every area, and a
    user who HID the tips. Both are reachable — most tips auto-hide on engagement, and
    several predicates read the filesystem, so a merely seeded home can start at zero —
    and the page could not tell them apart from ``visible_count``/``total`` alone.
    Counted off :func:`load_dismissed`, which is already narrowed to :data:`TIP_IDS`, so
    it is the number of REAL tips the user hid, never junk an older build persisted.

    ``restorable_count`` is the count the restore control must gate on, and it is NOT
    ``dismissed_count`` (#452). The two filters in :func:`select_visible` are independent:
    clearing a dismissal only brings a tip back if its area is *also* still unengaged. So a
    user who dismissed a tip and later used that area has ``dismissed_count: 1`` with
    nothing to restore — gating the button on that count ships a control that writes the
    settings file and changes nothing the user can see. This counts the dismissed tips that
    :func:`select_visible` would actually admit once the dismissal is gone, so
    ``restorable_count: 0`` means "restoring is genuinely a no-op here" and the control
    stays hidden.
    """
    from personalclaw.config.loader import AppConfig

    dismissed = load_dismissed()
    if not AppConfig.load().legibility.discover_tips:
        return {
            "enabled": False,
            "areas": [],
            "visible_count": 0,
            "total": len(CATALOG),
            "dismissed_count": len(dismissed),
            # Zero without reading engagement: with the kill switch off the payload carries
            # no tips at all, so clearing dismissals restores nothing *here* either. Saying
            # 0 keeps the field's contract exact ("how many tips restoring would reveal")
            # and avoids the filesystem reads in compute_engaged on a disabled surface.
            "restorable_count": 0,
        }

    engaged = compute_engaged(state)
    visible = select_visible(dismissed=dismissed, engaged=engaged)
    return {
        "enabled": True,
        "areas": _group_by_area(visible),
        "visible_count": len(visible),
        "total": len(CATALOG),
        "dismissed_count": len(dismissed),
        "restorable_count": count_restorable(dismissed=dismissed, engaged=engaged),
    }
