"""Platform event registry (APE-2) — typed core facts, delivered only to apps that declared them.

APE-1 shipped the DECLARATION half: ``permissions.eventSubscriptions``, a list of exact
platform-event names an app asks to receive, disclosed at install consent. It deliberately
added no accessor and no delivery, on the reasoning that an enforcement point with no call
site enforces nothing. This module is the runtime that honours the declaration.

**Three registered events, each registered AT its emit site** (no second bus beside the
existing ones — the emit points are the places the fact already becomes true):

* ``session.created``    — ``dashboard/state.get_or_create_session``, where a new session
  row is inserted into ``_sessions``.
* ``knowledge.ingested`` — ``knowledge/pipeline/runner.ingest_item``, at the same terminal
  point the SSE ``ingest_complete`` fires from, so the app-facing fact and the UI-facing
  one can never disagree.
* ``task.completed``     — ``tasks/native.NativeTaskProvider.update_task``, on the same
  edge-triggered completion boundary (``pool.should_fire_completion``) the ``TaskComplete``
  user hook fires on. One edge, two observers.

**Two axes, kept apart (the contract APE-1 pinned).** ``eventSubscriptions`` is NOT
``permissions.events``. ``events`` is the gateway's WebSocket event-type allowlist, gated by
``can_use_event`` and enforced in ``state.broadcast_ws``; a platform subscription must not
grant the WS type of the same name, or the registry would inherit a second, wider path to
the same data (``test_event_subscriptions_do_not_widen_the_ws_event_allowlist``). Delivery
here therefore does **not** touch the WS fan-out at all: it goes through the broker-owned
per-app inbox (below), gated by its own accessor
``PermissionChecker.can_receive_platform_event``.

**Transport: the existing broker inbox, not a new one.** A delivered event is appended to
the app's broker-owned queue under ``config_dir()/app_messages/<app>.json`` — the same
queue ``apps/messaging.py`` owns and the app already drains, read-once, through
``GET /api/apps/message`` (scoped to the reader's own verified app identity). Reusing it
buys the depth cap, the atomic write, the read-once semantics and a live consumer for free.
Platform events are distinguishable from app-to-app messages by their sender:
:data:`PLATFORM_SENDER` is ``"@platform"``, which ``manager._validate_app_name`` rejects, so
no installed app can ever forge a message that reads as one. The two GRANTS stay separate:
``appMessaging`` governs app→app, ``eventSubscriptions`` governs platform→app, and holding
one grants nothing about the other.

**Deny by default, exact match.** ``emit`` fans out only to apps that are installed,
ENABLED, and name the event exactly. There is no prefix and no ``*`` wildcard (mirroring
``desktop``, unlike ``api``/``events``), so a subscription to ``task.completed`` never
matches ``task.completed.extra`` or ``task.*`` — a typo denies rather than widens. An app
that declares nothing receives nothing.

**Payloads carry identifiers, never prose.** Each event's payload keys are pinned in the
registry and an undeclared key is refused. That is a security rule, not tidiness: if an
event carried a task title or an item's text, a subscribed app with no matching
``permissions.api`` grant would receive content it cannot otherwise read, and the
subscription would silently widen ``can_use_api``. Keeping payloads to ids/statuses means a
subscription grants TIMING, not CONTENT — the app still has to fetch details through its own
granted API scope. ``session.created`` carries the session's name because the name IS its
address in this codebase; that is exactly what install consent discloses ("Receive platform
events: session.created"), and string values are length-capped. An app that feeds a payload
value to a model should fence it itself (``personalclaw.sdk.security``); nothing here is
free text the platform generated on an app's behalf, so there is no fence to apply on its
behalf either.

**Audit posture (deliberate).** Ordinary fan-out writes NO security event, in either
direction. An app never *requests* a platform event — dispatch is host-initiated, so a
non-delivery is not an access attempt, and one SEL row per (installed app × emitted event)
would flood the HMAC chain and drown the real rows. What IS audited is bounded and
anomalous: an emit naming an UNREGISTERED event (``outcome="rejected"`` — a code defect that
would otherwise vanish) and a delivery that FAILED for an app that had earned it
(``outcome="error"`` — a subscribed app silently missing an event it declared).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

#: The reserved sender identity every delivered platform event carries in an app's inbox.
#: ``@`` is not legal in an app name (``manager._validate_app_name``), so no installed app
#: can send a brokered message that reads as a platform event.
PLATFORM_SENDER = "@platform"

#: Cap on any single string value in a payload, so an event can never hand an app (or the
#: inbox file) an unbounded blob through a user-authored name.
MAX_VALUE_CHARS = 200

SESSION_CREATED = "session.created"
KNOWLEDGE_INGESTED = "knowledge.ingested"
TASK_COMPLETED = "task.completed"


@dataclass(frozen=True)
class PlatformEvent:
    """One registered platform event: its name, what it means, and the EXACT payload keys
    it may carry. ``payload_keys`` is a closed set — :func:`emit` refuses an undeclared
    key rather than passing it through, which is what keeps "identifiers, never prose"
    true over time instead of by convention."""

    name: str
    summary: str
    payload_keys: tuple[str, ...]


#: The closed registry. A name absent from here cannot be emitted and cannot be subscribed
#: to usefully — both halves are checked against this table.
PLATFORM_EVENTS: dict[str, PlatformEvent] = {
    SESSION_CREATED: PlatformEvent(
        name=SESSION_CREATED,
        summary="A new chat session was created. Carries the session's name (its address).",
        payload_keys=("session",),
    ),
    KNOWLEDGE_INGESTED: PlatformEvent(
        name=KNOWLEDGE_INGESTED,
        summary=(
            "A knowledge item was ingested and is in the store. Carries the item id and the "
            "terminal status — `done`, or `partial` when optional steps were skipped. A run "
            "that FAILED ingests nothing and is not announced under this name at all."
        ),
        payload_keys=("item_id", "status"),
    ),
    TASK_COMPLETED: PlatformEvent(
        name=TASK_COMPLETED,
        summary=(
            "A task crossed into a completed state. Carries the task id and its status — "
            "no title, so the event grants timing rather than task content."
        ),
        payload_keys=("task_id", "status"),
    ),
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sel_platform_event(*, outcome: str, event: str, app: str = "", error: str = "") -> None:
    """Emit one platform-event security row. Never raises (audit must not break delivery).

    Reserved for the two bounded, anomalous cases named in the module docstring — NOT for
    ordinary fan-out or ordinary non-delivery."""
    try:
        from personalclaw.sel import SecurityEvent, sel

        sel().log(
            SecurityEvent(
                event_id=uuid4().hex[:16],
                timestamp=_iso_now(),
                event_type="app_platform_event",
                caller_identity=f"app:{app}" if app else "platform",
                agent="personalclaw",
                source="apps",
                operation="platform_event_deliver",
                outcome=outcome,
                resources=f"event={event}",
                error=error,
            )
        )
    except Exception:  # audit must never break the delivery decision
        logger.debug("app_events SEL emit failed for %s", event, exc_info=True)


def _coerce_payload(spec: PlatformEvent, payload: dict[str, Any]) -> dict[str, Any]:
    """Project ``payload`` onto the event's declared keys, capping string values.

    Undeclared keys are DROPPED rather than forwarded: the registry's key set is the
    contract that keeps an event from becoming a content channel, so an emit site that
    grew a field must add it here (and to the event's disclosed summary) first."""
    out: dict[str, Any] = {}
    for key in spec.payload_keys:
        value = payload.get(key, "")
        if isinstance(value, str):
            value = value[:MAX_VALUE_CHARS]
        elif not isinstance(value, (int, float, bool)):
            value = str(value)[:MAX_VALUE_CHARS]
        out[key] = value
    extra = sorted(set(payload) - set(spec.payload_keys))
    if extra:
        logger.debug("app_events: dropped undeclared payload key(s) %s from %s", extra, spec.name)
    return out


def subscribers(event: str) -> list[str]:
    """The installed, ENABLED apps that declared ``event`` — deny by default.

    Mirrors ``app_crons._desired_app_crons``: walk the installed apps, skip anything not
    enabled, and consult the app's own :class:`~personalclaw.apps.permissions.
    PermissionChecker`. A disabled app is not a subscriber (its declaration goes dormant
    with it), and an app whose manifest can't be resolved is not one either."""
    from personalclaw.apps.manager import _read_installed, apps_dir
    from personalclaw.apps.permissions import checker_for

    try:
        root = apps_dir()
    except Exception:
        logger.debug("app_events: apps dir unavailable", exc_info=True)
        return []
    if not root.is_dir():
        return []
    out: list[str] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            meta = _read_installed(entry.name)
        except Exception:
            logger.debug("app_events: install record unreadable for %s", entry.name, exc_info=True)
            continue
        if meta is None or not meta.enabled:
            continue
        checker = checker_for(meta.name)
        if checker is None or not checker.can_receive_platform_event(event):
            continue
        out.append(meta.name)
    return out


def _deliver(app_name: str, event: str, body: dict[str, Any]) -> None:
    """Append one platform event to ``app_name``'s broker-owned inbox.

    Deliberately reuses ``messaging``'s queue writer rather than opening a second queue:
    the depth cap, the atomic write and the read-once drain route are already there, and a
    parallel store would be a second inbox an app has to learn to poll."""
    from personalclaw.apps.messaging import AppMessage, _append_to_queue

    _append_to_queue(
        app_name,
        AppMessage(
            id=uuid4().hex[:16],
            sender=PLATFORM_SENDER,
            target=app_name,
            type=event,
            payload=json.dumps(body, sort_keys=True),
            ts=_iso_now(),
        ),
    )


def emit(event: str, payload: dict[str, Any] | None = None) -> list[str]:
    """Fan one platform event out to every app that DECLARED it. Returns the app names
    delivered to (empty when nobody subscribed).

    Total by construction — an emit site is an OBSERVER boundary: a broken app manifest or
    an unwritable inbox must never fail the session creation, ingest or task edit that
    produced the fact. Every failure is swallowed (and, for a subscribed app, audited)."""
    spec = PLATFORM_EVENTS.get(event)
    if spec is None:
        # An unregistered name is a code defect at the emit site, not a user path: it would
        # otherwise be delivered to nobody forever with no trace. Refuse loudly + audit.
        logger.warning("app_events: refusing to emit unregistered platform event %r", event)
        _sel_platform_event(outcome="rejected", event=event, error="unregistered platform event")
        return []
    try:
        body = _coerce_payload(spec, payload or {})
        targets = subscribers(event)
    except Exception:
        logger.debug("app_events: subscriber resolution failed for %s", event, exc_info=True)
        return []
    delivered: list[str] = []
    for app_name in targets:
        try:
            _deliver(app_name, event, body)
        except Exception:
            # This app EARNED the event and did not get it — bounded, rare, and worth a row.
            logger.debug("app_events: delivery failed for %s → %s", event, app_name, exc_info=True)
            _sel_platform_event(
                outcome="error", event=event, app=app_name, error="inbox write failed"
            )
            continue
        delivered.append(app_name)
    if delivered:
        logger.debug("app_events: delivered %s to %s", event, delivered)
    return delivered
