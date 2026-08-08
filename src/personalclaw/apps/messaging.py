"""App-to-app messaging broker (APE-9).

The ONE mediated path for one installed app to send another a typed message. Apps
never open sockets to each other; every message goes through the gateway broker
(``POST /api/apps/message``), which:

* establishes the SENDER's identity from its verified app-scoped token
  (``request["app"]``, set by token-auth), NEVER a body field — un-spoofable;
* checks the sender's ``appMessaging`` grant for the TARGET. Deny by default: an
  app that did not declare ``appMessaging: [target]`` may message NO app, and an
  undeclared pair is refused ``403`` **and** written to the SEL audit chain
  (``event_type="app_messaging", outcome="denied"``);
* caps the payload size (oversize → rejected, nothing queued);
* fences the payload as untrusted (``security.fence_untrusted``) so the receiving
  app reads the sender's content as DATA, not instructions — a message from app A
  cannot inject instructions into app B's agent;
* delivers to a broker-owned per-target queue the target drains via
  ``GET /api/apps/message`` (gated on the reader's own verified app identity).

The queue is broker-owned (under ``config_dir()/app_messages/``), not the app's
own writable data dir — a sender can only reach a target's inbox THROUGH this
broker, and a target can only read ITS OWN inbox through the gateway route. There
is no filesystem or socket seam between the two apps.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personalclaw.apps.manager import _validate_app_name
from personalclaw.apps.permissions import checker_for
from personalclaw.atomic_write import atomic_write
from personalclaw.security import fence_untrusted

logger = logging.getLogger(__name__)

_QUEUE_DIRNAME = "app_messages"
# A single message payload is capped so a sender can't hand a target (or the audit
# log) an unbounded blob. 64 KiB is generous for a typed control/notification
# message while staying well under any accidental file-dump.
MAX_PAYLOAD_BYTES = 64 * 1024
# Bound a target's inbox depth so a chatty (or hostile) sender can't grow the queue
# without limit; oldest are dropped once the cap is hit (newest-N retained).
_MAX_QUEUE = 100


class AppMessageError(Exception):
    """A brokered send was refused. ``status`` is the HTTP status the handler returns."""

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class AppMessage:
    """One brokered message. ``payload`` is ALWAYS stored fenced (untrusted)."""

    id: str
    sender: str  # verified sender app identity (never body-supplied)
    target: str
    type: str  # the message-type discriminator (a "typed message")
    payload: str  # the sender's content, wrapped by fence_untrusted
    ts: str  # ISO 8601 UTC

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "from": self.sender,
            "type": self.type,
            "payload": self.payload,
            "ts": self.ts,
        }


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _queue_path(app_name: str) -> Path:
    """Broker-owned inbox file for ``app_name`` under ``config_dir()``.

    Validated as a kebab-case app name before it can name a file (the same guard
    ``app_data_dir`` uses) so a caller cannot escape the queue dir with a crafted
    target name."""
    from personalclaw.config.loader import config_dir

    return config_dir() / _QUEUE_DIRNAME / f"{_validate_app_name(app_name)}.json"


def read_queue(target: str) -> list[dict[str, str]]:
    """The target's queued messages (empty list if none / unreadable)."""
    path = _queue_path(target)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def drain_queue(target: str) -> list[dict[str, str]]:
    """Return AND clear the target's inbox (read-once queue semantics)."""
    msgs = read_queue(target)
    if not msgs:
        return []
    path = _queue_path(target)
    try:
        path.unlink()
    except OSError:
        logger.debug("app_messaging: failed to clear inbox for %s", target, exc_info=True)
    return msgs


def _append_to_queue(target: str, msg: AppMessage) -> None:
    path = _queue_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    queue = read_queue(target)
    queue.append(msg.to_dict())
    # Retain the newest N so a target's inbox can't grow without bound.
    if len(queue) > _MAX_QUEUE:
        queue = queue[-_MAX_QUEUE:]
    atomic_write(path, json.dumps(queue, indent=2))


def _sel_message(
    *, operation: str, outcome: str, sender: str, target: str, error: str = ""
) -> None:
    """Emit one app-messaging security event. Never raises (audit must not break the
    decision). Mirrors the ``channel_trust`` precedent — free-form audit strings on the
    HMAC-chained SEL, not a closed enum."""
    try:
        from personalclaw.sel import SecurityEvent, sel

        sel().log(
            SecurityEvent(
                event_id=uuid4().hex[:16],
                timestamp=_iso_now(),
                event_type="app_messaging",
                caller_identity=f"app:{sender}" if sender else "app:?",
                agent="personalclaw",
                source="apps",
                operation=operation,
                outcome=outcome,
                resources=f"target={target}",
                error=error,
            )
        )
    except Exception:  # audit must never break the messaging decision
        logger.debug("app_messaging SEL emit failed for %s", operation, exc_info=True)


def send_message(*, sender: str, target: str, msg_type: str, payload: str) -> AppMessage:
    """Broker a typed message from ``sender`` (a verified app identity) to ``target``.

    ``sender`` MUST be the caller's verified app identity — the handler passes
    ``request["app"]``, never a body field, so the sender cannot be spoofed. Raises
    :class:`AppMessageError` (fail CLOSED) on an undeclared pair (403 + SEL denial),
    an oversize payload (413), or a non-string payload (400)."""
    checker = checker_for(sender)
    if checker is None or not checker.can_use_app_messaging(target):
        _sel_message(
            operation="app_message",
            outcome="denied",
            sender=sender,
            target=target,
            error="appMessaging grant not declared for target",
        )
        raise AppMessageError(f"app {sender!r} is not permitted to message {target!r}", status=403)

    if not isinstance(payload, str):
        raise AppMessageError("payload must be a string", status=400)
    payload_bytes = len(payload.encode("utf-8"))
    if payload_bytes > MAX_PAYLOAD_BYTES:
        _sel_message(
            operation="app_message",
            outcome="rejected",
            sender=sender,
            target=target,
            error=f"payload too large ({payload_bytes} bytes)",
        )
        raise AppMessageError(
            f"payload too large ({payload_bytes} bytes, max {MAX_PAYLOAD_BYTES})",
            status=413,
        )

    # Fence the sender's content so the receiving app treats it as untrusted DATA,
    # not instructions — a message from app A can carry a prompt injection.
    fenced = fence_untrusted(
        payload,
        source=f"app_message:{sender}",
        source_type="app_message",
        source_id=sender,
    )
    msg = AppMessage(
        id=uuid4().hex[:16],
        sender=sender,
        target=target,
        type=msg_type,
        payload=fenced,
        ts=_iso_now(),
    )
    _append_to_queue(target, msg)
    _sel_message(operation="app_message", outcome="success", sender=sender, target=target)
    return msg
