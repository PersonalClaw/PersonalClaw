"""In-process artifact change notifications (PRODUCT-EXPERIENCE-PARITY §6.1 choice 2).

The gateway is the only writer of the artifact store, so mirroring artifacts into another
subsystem needs an OBSERVER, not a poller: a watched directory is polled because an editor
outside the process edits it, while an artifact only ever changes because a handler, an
MCP tool or a chat tool called :class:`~personalclaw.artifacts.native.NativeArtifactProvider`.
A signature-diff loop over ``<home>/artifacts`` would re-stat the whole library forever to
learn something the writing call already knew.

**The vocabulary is deliberately two words** — ``upsert`` and ``delete``. Artifacts do not
own the knowledge library's ``created``/``modified``/``deleted`` change vocabulary
(``knowledge_providers.base``): translating "this artifact now has this body" into
create-vs-modify requires knowing whether the MIRROR already exists, which is knowledge-side
state. Emitting three words from here would mean this module deciding a question it cannot
see the answer to, and a wrong guess would either duplicate a mirror row or drop an edit.

**A listener never breaks a write.** :func:`emit` swallows and logs every listener
exception: an artifact save is the user's work and a downstream indexing fault must not turn
a successful save into a 500. That is the fail-open direction, chosen because the write has
already happened by the time a listener runs — refusing after the fact is not available.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

#: An artifact was created, edited or reverted — its body may now differ.
UPSERT = "upsert"
#: An artifact was removed from the store.
DELETE = "delete"

#: The closed set, so a consumer can reject an unknown change instead of defaulting.
ARTIFACT_CHANGES = frozenset({UPSERT, DELETE})

#: ``(change, slug) -> None`` callbacks. A list rather than a set so registration order is
#: the notification order, and identity-deduped on subscribe so a re-entrant wiring path
#: (the gateway restarting its own subsystems) cannot double-notify.
_listeners: list[Callable[[str, str], None]] = []


def subscribe(listener: Callable[[str, str], None]) -> None:
    """Register a change listener (idempotent for the same callable)."""
    if listener not in _listeners:
        _listeners.append(listener)


def unsubscribe(listener: Callable[[str, str], None]) -> None:
    """Remove a listener; unknown callables are ignored (tests tear down freely)."""
    if listener in _listeners:
        _listeners.remove(listener)


def emit(change: str, slug: str) -> None:
    """Notify every listener that *slug* changed. Never raises to the writer."""
    if change not in ARTIFACT_CHANGES:
        # A typo'd change would otherwise reach a listener that branches on the two known
        # values and silently take its else-path. Refuse loudly here instead.
        raise ValueError(f"unknown artifact change {change!r}")
    if not slug:
        return
    for listener in list(_listeners):
        try:
            listener(change, slug)
        except Exception:  # noqa: BLE001 — an indexing fault must not fail the save
            logger.warning("artifact change listener failed for %s", slug, exc_info=True)
