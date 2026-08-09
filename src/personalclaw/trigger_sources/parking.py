"""Parking the triggers bound to an app-contributed source (AUTO-A4, decision 9 semantics).

The plan's requirement: "enable/disable of the app registers/unregisters the source, and triggers
bound to a vanished source park with a typed reason, never silently die."

**Reuses `triggers/autopause.py` rather than inventing a second mechanism.** The decision comes
from `autopause.evaluate(exit_type=TRANSPORT_UNAVAILABLE)` — so the state, the health rollup, the
user-facing reason and the cooldown all come from the one table the rest of the substrate reads.
`transport_unavailable` is the right classification and not merely the closest: the source that
produced these events is a service this trigger calls, it is now unreachable, and the condition
resolves when the app comes back. A park is reversible and self-healing, which is exactly the
semantics of "the user disabled the app for an afternoon".

Deliberately NOT `disabled`: `enabled=False` is a USER decision, and `TriggerState`'s own docstring
names the failure — "showing both as 'paused' would make the user look for a switch they never
flipped". A user who disables a calendar app and later re-enables it must not have to hunt for
every trigger it fed and toggle each one back on.

**Which triggers bind to which app** is decided by :func:`bound_app` on the trigger's `event_glob`,
and the rule is narrow on purpose — see that function.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.event_triggers import EventTrigger, EventTriggerStore

logger = logging.getLogger(__name__)

#: Glob metacharacters. A segment containing any of these is not a literal app name, so a glob like
#: ``app:*:meeting_soon`` names no single app and binds to none (see :func:`bound_app`).
_GLOB_CHARS = frozenset("*?[]")


def bound_app(event_glob: str) -> str:
    """The single app an ``AppEvent`` glob is bound to, or "" when it spans more than one.

    A glob binds to app ``X`` only when its app segment is the LITERAL name ``X`` — i.e.
    ``app:calendar:*`` binds to ``calendar``, and ``app:calendar:meeting_soon`` does too.

    Narrow on purpose, and the exclusions are the interesting half:

    * An **empty** glob is the catch-all across every app's events. Parking it because ONE app was
      disabled would stop a trigger that still has live sources — a park that stops working
      automations is worse than no park at all.
    * A **wildcarded app segment** (``app:*:meeting_soon``) is the same case: it spans apps, so
      disabling one leaves it legitimately firing on the others.
    * A glob with **no ``app:`` prefix** is not an app-source glob at all.

    So the rule errs toward NOT parking, and that direction is chosen deliberately: failing to park
    a cross-app trigger leaves it firing correctly from its remaining sources, while over-parking
    would silently stop automations whose source is still present.
    """
    from personalclaw.trigger_sources.registry import NAMESPACE_PREFIX

    glob = (event_glob or "").strip()
    prefix = f"{NAMESPACE_PREFIX}:"
    if not glob.startswith(prefix):
        return ""
    rest = glob[len(prefix) :]
    app, sep, _event = rest.partition(":")
    if not sep or not app or _GLOB_CHARS & set(app):
        return ""
    return app


def bound_triggers(triggers: list["EventTrigger"], app: str) -> list["EventTrigger"]:
    """Every ``AppEvent`` trigger bound to *app*, in store order."""
    from personalclaw.event_triggers import APP_EVENT

    return [t for t in triggers if t.pattern == APP_EVENT and bound_app(t.event_glob) == app]


def park_for_app(store: "EventTriggerStore", app: str, *, now: float = 0.0) -> list[str]:
    """Park every trigger bound to *app*. Returns the parked trigger ids.

    Called when the app's ``trigger_source`` provider deregisters (app disabled or uninstalled).
    Idempotent: a trigger already parked is left as-is rather than having its ``retry_after``
    pushed forward, so repeatedly disabling an already-disabled app cannot extend a cooldown.

    The decision — state, health, reason, cooldown — comes from ``autopause.evaluate``, so this
    function decides only WHICH triggers, never WHAT parking means.
    """
    from personalclaw.triggers.autopause import ExitType, evaluate
    from personalclaw.triggers.models import TriggerState

    decision = evaluate(
        exit_type=ExitType.TRANSPORT_UNAVAILABLE.value, consecutive_failures=0, now=now
    )
    parked: list[str] = []
    items = store.load()
    for trigger in bound_triggers(items, app):
        if trigger.state == TriggerState.PARKED.value:
            continue
        trigger.state = decision.state
        # The app name rides the reason because `PARK_REASONS` phrases the CLASS of outage ("the
        # service this trigger calls was unreachable") and the user's next question is WHICH one.
        trigger.park_reason = f"{decision.reason}: the {app!r} app that supplies its events is "
        trigger.park_reason += "disabled or uninstalled"
        trigger.park_retry_after = decision.retry_after
        parked.append(trigger.id)
    if parked:
        store.save(items)
        logger.info(
            "parked %d event trigger(s) bound to the disabled app %r: %s",
            len(parked),
            app,
            ", ".join(parked),
        )
    return parked


def unpark_for_app(store: "EventTriggerStore", app: str) -> list[str]:
    """Un-park every trigger bound to *app*. Returns the revived trigger ids.

    Called when the app's source registers (app enabled). The app being back IS the proof the
    outage ended, so this does not wait for the cooldown: ``autopause.PARK_COOLDOWN_SECS`` exists
    to space RETRIES against a service that may still be down, and there is nothing to probe here.

    Only a PARKED trigger is revived. An autopaused or quarantined one is left alone — those states
    were reached for reasons this app's absence had nothing to do with, and reviving a quarantined
    trigger by re-enabling an unrelated app would defeat the one state that must never auto-retry.
    """
    from personalclaw.triggers.models import TriggerState

    revived: list[str] = []
    items = store.load()
    for trigger in bound_triggers(items, app):
        if trigger.state != TriggerState.PARKED.value:
            continue
        trigger.state = TriggerState.ACTIVE.value
        trigger.park_reason = ""
        trigger.park_retry_after = 0.0
        revived.append(trigger.id)
    if revived:
        store.save(items)
        logger.info(
            "un-parked %d event trigger(s) bound to the re-enabled app %r: %s",
            len(revived),
            app,
            ", ".join(revived),
        )
    return revived


def _default_store() -> Any:
    """The live event-trigger store for the active home.

    Resolved lazily at each call rather than held, matching `dashboard/handlers/triggers.py`'s
    `_event_store`: the home can change between calls in tests and in a seeded dev run, and a
    cached path would write the wrong file.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.event_triggers import EventTriggerStore

    return EventTriggerStore(config_dir() / "event_triggers.json")
