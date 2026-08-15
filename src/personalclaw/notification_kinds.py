"""The notification kind registry (INBOX-NOTIFICATIONS-UNIFICATION C1).

Every notification this system delivers is a ``(source, kind)`` pair, registered here
once. Before this module, ``kind`` was a bare string invented at each of 25 call sites —
so nothing could enumerate what the system is *able* to tell you, and the rules UI had
nothing to draw a row for. A registry turns "what notifications exist?" from a grep into
a function call.

**Source vs kind.** ``source`` is the emitter domain (who is speaking: ``cron``,
``loop``, ``inbox``, ``system``…); ``kind`` is what kind of thing is being said
(``needs_input``, ``proposal``, ``failed``…). They are separate because the rules layer
wants both axes: "never notify me about anything from ``heartbeat``" and "always
interrupt me for a ``needs_input``, whoever raised it" are both natural rules, and a
single flat string can express neither.

**Registration is frozen at import.** Registering the same ``(source, kind)`` twice
raises — a duplicate means two emitters disagree about what they're emitting, which is a
bug worth failing the import over rather than resolving by last-write-wins.

**Resolution is fail-OPEN.** An unregistered pair resolves to a synthetic
``(system, generic)`` kind and logs a warning instead of raising. This mirrors the
existing delivery gate's philosophy (`providers/entity_routes.notification_allowed`
delivers when its own settings file is unreadable): a notification the system could not
classify is still a notification the user should see. Losing a message because a plugin
forgot to register is worse than showing one with a generic label.

**Severity means the same thing it already did.** 1=info, 2=warning, 3=error, matching
`_KIND_SEVERITY`/`_MIN_SEVERITY_RANK` in `providers/entity_routes.py` — 3 is the rank
that bypasses quiet hours. This module does not re-implement the global gate; it supplies
the severity the gate reads.

**Every default_mode is ``immediate``, deliberately.** ``badge`` is the interesting new
capability — persist without interrupting — and heartbeats, loop progress and
signal-retirement notices are all obvious candidates for it. But this plan replaces the
delivery path outright with no gate to hide behind, so its safety property is that a user
with no rules file sees *exactly* what they see today, and today every emitter that passes
the global gate produces a toast. Shipping opinionated `badge` defaults would silently
stop delivering three kinds of notification as a side effect of a refactor — the user
would experience it as "notifications stopped working," with no setting they knowingly
changed. So the registry ships behavior-preserving defaults and `badge` becomes something
the user opts into per row in the rules matrix. `tests/test_notification_kinds.py` pins
this as an invariant, not a coincidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

#: Delivery modes a rule may select (C2). ``badge`` persists without a toast — the
#: "I want it in the list but don't interrupt me" mode the old global gate had no way to
#: express (it could only deliver or drop entirely).
Mode = Literal["never", "badge", "immediate", "digest"]

MODES: tuple[str, ...] = ("never", "badge", "immediate", "digest")

#: Severity ranks, identical to the existing delivery gate's vocabulary.
SEV_INFO = 1
SEV_WARNING = 2
SEV_ERROR = 3

#: The fallback pair for an unregistered kind (fail-open).
GENERIC_SOURCE = "system"
GENERIC_KIND = "generic"


@dataclass(frozen=True)
class NotificationKind:
    """One registered kind of notification.

    ``default_mode``/``default_severity`` are the behavior when the user has no rule for
    this pair — which is the common case, and the case the "no rules file behaves exactly
    as before" regression test pins.
    """

    source: str
    kind: str
    label: str
    default_mode: Mode = "immediate"
    default_severity: int = SEV_INFO
    #: True when this kind carries a durable inbox item rather than only a transient
    #: delivery. Set for the attention kinds folded in from Session 2 onward.
    attention: bool = False
    #: True when this kind's payload asserts a checkable claim, so a rule MAY opt into a
    #: second-opinion verification pass (INU-6) before delivery. Parallel to ``attention``:
    #: ``attention`` is "does this persist a row", ``verifiable`` is "may a rule ask a model
    #: whether the row's claim holds". Setting this alone changes nothing — verification runs
    #: only when a rule sets ``verify:true``, which the rules PUT rejects for a
    #: non-verifiable kind.
    verifiable: bool = False

    @property
    def key(self) -> str:
        """The ``<source>/<kind>`` string used as the rules-store key."""
        return f"{self.source}/{self.kind}"


_REGISTRY: dict[tuple[str, str], NotificationKind] = {}


def register(k: NotificationKind) -> None:
    """Register a kind. Raises ``ValueError`` on a duplicate ``(source, kind)``."""
    ident = (k.source, k.kind)
    if ident in _REGISTRY:
        raise ValueError(f"duplicate notification kind registration: {k.key}")
    if k.default_mode not in MODES:
        raise ValueError(f"{k.key}: unknown mode {k.default_mode!r}")
    if k.default_severity not in (SEV_INFO, SEV_WARNING, SEV_ERROR):
        raise ValueError(f"{k.key}: severity must be 1, 2 or 3 (got {k.default_severity})")
    _REGISTRY[ident] = k


def all_kinds() -> list[NotificationKind]:
    """Every registered kind, ordered by source then kind (stable for the rules UI)."""
    return sorted(_REGISTRY.values(), key=lambda k: (k.source, k.kind))


def resolve_kind(source: str, kind: str) -> NotificationKind:
    """The registered kind, or a synthetic generic one (fail-open + warn)."""
    found = _REGISTRY.get((source, kind))
    if found is not None:
        return found
    logger.warning(
        "unregistered notification kind %s/%s — delivering as %s/%s",
        source,
        kind,
        GENERIC_SOURCE,
        GENERIC_KIND,
    )
    return NotificationKind(
        source=GENERIC_SOURCE,
        kind=GENERIC_KIND,
        label=f"{source}/{kind}" if source or kind else "Notification",
        default_mode="immediate",
        default_severity=SEV_INFO,
    )


def kind_for_legacy_pair(source: str, kind: str) -> str:
    """The flat wire string for a registered ``(source, kind)``.

    Emitters that know their typed pair (the attention kinds, which never had a legacy flat
    string) still have to hand ``notify()`` a wire value, since the flat string is what the
    persisted log and the SPA's display map key on. This is the one place that mapping
    lives, so a new attention kind cannot invent a second convention.

    Prefers an existing legacy string when one maps to this pair — so ``inbox/alert`` keeps
    emitting ``inbox_alert`` and its persisted history stays one kind — and otherwise falls
    back to the bare ``kind``, which is what a brand-new attention kind wants.
    """
    for flat, ident in _WIRE_TO_PAIR.items():
        if ident == (source, kind):
            return flat
    return kind


def kind_for_legacy(kind: str) -> NotificationKind:
    """Resolve a bare pre-registry ``kind`` string to a registered kind.

    The persisted notification log and the SSE wire both carry a flat ``kind`` string,
    and 25 call sites passed one. Rather than rewrite history or break the frontend's
    display map, the flat string stays the wire format and this function maps it back to
    its registration. Unknown → generic, fail-open.
    """
    flat = (kind or "").strip().lower()
    ident = _WIRE_TO_PAIR.get(flat)
    if ident is None:
        return resolve_kind(GENERIC_SOURCE, flat or GENERIC_KIND)
    return resolve_kind(*ident)


# ── Registrations ───────────────────────────────────────────────────────────
# Built from an AST inventory of every `.notify(...)` call site in src/ (T1.1); the
# inventory table is in the plan's execution log. The flat `kind` string each site
# passes today is preserved as the wire value via _LEGACY_FLAT below, so the persisted
# log and the frontend's display map keep working unchanged.

_KINDS: tuple[NotificationKind, ...] = (
    # cron / schedule — 5 sites in gateway.py. Both rank INFO because gateway.py:1299
    # emits a job FAILURE through the same flat "cron" kind, which the old severity map
    # left unlisted (⇒ info). Ranking failures as warning here would start delivering them
    # to a user who had raised min_severity to warning — a change they never asked for. The
    # rules matrix is where they can now make that choice themselves.
    NotificationKind("cron", "result", "Scheduled job result", "immediate", SEV_INFO),
    NotificationKind("cron", "failed", "Scheduled job failed", "immediate", SEV_INFO),
    # heartbeat — 5 sites in gateway.py
    NotificationKind("heartbeat", "status", "Heartbeat", "immediate", SEV_INFO),
    # loop watchdog — dynamic kind via _NOTIFY_EVENTS (8 events → 4 flat kinds)
    NotificationKind("loop", "complete", "Loop complete", "immediate", SEV_INFO),
    NotificationKind("loop", "failed", "Loop failed", "immediate", SEV_ERROR),
    NotificationKind("loop", "stalled", "Loop stalled or blocked", "immediate", SEV_WARNING),
    NotificationKind(
        "loop", "needs_input", "Loop needs your input", "immediate", SEV_WARNING, attention=True
    ),
    NotificationKind("loop", "progress", "Loop progress", "immediate", SEV_INFO),
    # inbox — the user-configured keyword/name alert (inbox.py:301)
    NotificationKind("inbox", "alert", "Inbox alert", "immediate", SEV_WARNING),
    # agent / subagent / hooks
    NotificationKind("agent", "message", "Agent message", "immediate", SEV_INFO),
    NotificationKind("agent", "subagent", "Subagent update", "immediate", SEV_INFO),
    NotificationKind("hook", "fired", "Trigger fired", "immediate", SEV_INFO),
    # system-level warnings and drift
    NotificationKind("system", "warning", "System warning", "immediate", SEV_WARNING),
    NotificationKind("system", "error", "System error", "immediate", SEV_ERROR),
    NotificationKind("system", "info", "Notice", "immediate", SEV_INFO),
    NotificationKind("system", "success", "Success", "immediate", SEV_INFO),
    # INFO, not warning: the flat "app.route.drift" string was unlisted in the old severity
    # map (⇒ info). Promoting it would start delivering it under a raised min_severity.
    NotificationKind("system", "route_drift", "App route drift", "immediate", SEV_INFO),
    NotificationKind("system", "session", "Session notice", "immediate", SEV_INFO),
    # learning / feedback
    NotificationKind("learning", "retire", "Retired a learned signal", "immediate", SEV_INFO),
    # ── Attention kinds (S2+) ────────────────────────────────────────────
    # These carry a durable inbox item, so `attention=True`. They have NO legacy flat
    # string — nothing emitted them before `emit_attention_item` existed — which is why
    # they may carry their honest severity rather than inheriting a historical rank
    # (see test_new_attention_pairs_are_unreachable_from_legacy_strings).
    NotificationKind(
        "skills",
        "proposal",
        "Skill proposal",
        "immediate",
        SEV_INFO,
        attention=True,
        verifiable=True,
    ),
    NotificationKind(
        "system",
        "agent_request",
        "Agent request",
        "immediate",
        SEV_WARNING,
        attention=True,
        verifiable=True,
    ),
    NotificationKind("system", "digest", "Daily digest", "immediate", SEV_INFO, attention=True),
    # apps — an installed app's source offers a newer version (APE-7). Attention-bearing
    # (a durable inbox row deep-links to the app), emitted once per (name, latest_version)
    # via emit_attention_item on the existing /api/apps read path — no polling loop.
    NotificationKind(
        "apps", "update", "App update available", "immediate", SEV_INFO, attention=True
    ),
    # The synthetic fallback, registered so the rules UI can show a row for it.
    NotificationKind(GENERIC_SOURCE, GENERIC_KIND, "Uncategorized", "immediate", SEV_INFO),
)

#: Flat legacy `kind` string → registered `(source, kind)`.
#:
#: Two entries have NO backend emitter and are here deliberately: the frontend's display
#: map (`web/src/pages/notifications/notificationMeta.ts`) has rows for `schedule` and
#: `loop`, which no `.notify()` call ever passes — pre-existing drift found by the T1.1
#: inventory. They map to their nearest real registration so a notification persisted by
#: an older build still resolves.
_LEGACY_FLAT: dict[str, tuple[str, str]] = {
    "cron": ("cron", "result"),
    "schedule": ("cron", "result"),
    "heartbeat": ("heartbeat", "status"),
    "loop": ("loop", "progress"),
    "inbox_alert": ("inbox", "alert"),
    "agent": ("agent", "message"),
    "subagent": ("agent", "subagent"),
    "hook": ("hook", "fired"),
    "warning": ("system", "warning"),
    "error": ("system", "error"),
    "info": ("system", "info"),
    "success": ("system", "success"),
    "app.route.drift": ("system", "route_drift"),
    "session": ("system", "session"),
    "feedback_retire": ("learning", "retire"),
    GENERIC_KIND: (GENERIC_SOURCE, GENERIC_KIND),
}

#: Wire strings introduced BY the attention kinds (S2+), kept separate from the legacy map
#: above because the two answer different questions.
#:
#: `_LEGACY_FLAT` is a historical record: "what did an emitter already in the tree pass?"
#: Its entries carry a severity obligation — re-ranking one changes min-severity filtering
#: for a user who never touched a setting, which is why a test walks it against the old
#: `_KIND_SEVERITY` map. These kinds have no such history (nothing emitted them before
#: `emit_attention_item` existed), so they are free to carry their honest severity.
#:
#: They still need a wire string: it is what `notify()` resolves a rule from and what the
#: SPA's display map keys on. Without one they resolve to system/generic and lose their own
#: rule — a user's "always interrupt me for needs_input" would silently do nothing.
_ATTENTION_FLAT: dict[str, tuple[str, str]] = {
    "needs_input": ("loop", "needs_input"),
    "proposal": ("skills", "proposal"),
    "agent_request": ("system", "agent_request"),
    "digest": ("system", "digest"),
    "app_update": ("apps", "update"),
}

#: Every wire string this build understands, for resolution. Legacy entries win a collision:
#: an existing persisted kind must never be re-pointed by a newly added attention kind.
_WIRE_TO_PAIR: dict[str, tuple[str, str]] = {**_ATTENTION_FLAT, **_LEGACY_FLAT}

for _k in _KINDS:
    register(_k)


# ── Wire constants for emitters (T1.2) ──────────────────────────────────
# The flat string remains the WIRE format — it is what the persisted notification log
# stores, what the SSE payload carries, and what the SPA's display map keys on. So a
# "typed constant" here is that flat string, named and greppable, rather than a new
# vocabulary that would need translating at the boundary. The point is that a call site
# can no longer invent a kind by typo: `notify("warnign", …)` used to deliver a generic
# notification forever, silently.

CRON = "cron"
HEARTBEAT = "heartbeat"
INBOX_ALERT = "inbox_alert"
AGENT = "agent"
SUBAGENT = "subagent"
HOOK = "hook"
WARNING = "warning"
ERROR = "error"
INFO = "info"
SUCCESS = "success"
APP_ROUTE_DRIFT = "app.route.drift"
SESSION = "session"
FEEDBACK_RETIRE = "feedback_retire"
GENERIC = GENERIC_KIND

#: Every constant above, for the import-time consistency check and the drift test.
WIRE_CONSTANTS: tuple[str, ...] = (
    CRON,
    HEARTBEAT,
    INBOX_ALERT,
    AGENT,
    SUBAGENT,
    HOOK,
    WARNING,
    ERROR,
    INFO,
    SUCCESS,
    APP_ROUTE_DRIFT,
    SESSION,
    FEEDBACK_RETIRE,
    GENERIC,
)

# A constant that no longer maps to a registration is a silent downgrade to the generic
# fallback at every site that imports it — so fail the import instead. Not an `assert`:
# `python -O` strips those, and this is a correctness invariant, not a debug aid.
_unmapped = [c for c in WIRE_CONSTANTS if c not in _WIRE_TO_PAIR]
if _unmapped:  # pragma: no cover - import-time guard
    raise RuntimeError(f"notification wire constants missing a registration: {_unmapped}")
