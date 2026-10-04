"""Artifact change notifications: what follows an artifact's write hears it, whoever wrote it.

Two things follow every write to the artifact store, and both live in the gateway: Knowledge's
copy of the artifact, which makes its text searchable (``knowledge.artifact_ingest``), and the
hint that tells every open page to read it again (``DashboardState.announce_artifact_change``).
The gateway subscribes both here when it starts. The store has more writers than the gateway,
though: an agent CLI's artifact tools run in the tool server the CLI starts (``personalclaw
mcp-core``), a process of its own that writes the store there, and any other PersonalClaw process
of the same home may write it too. In such a process nothing is subscribed, so a change emitted
there used to reach nothing: an agent CLI's artifact was never found in Knowledge, and no open
page showed it until something in the gateway wrote it again.

So :func:`emit`, the seam every write method of the store calls once its write has landed, tells
each change to the gateway's observers exactly once, by one of two ways, and the gateway's own
record of itself says which (``gateway_base.live_gateway``: the pid it published):

* in the gateway, the listeners subscribed here are told, and nothing is sent anywhere;
* in any other process, the gateway of its home is told which artifact changed
  (:func:`_tell_the_gateway`): it reads the artifact from its own store and emits, there, what
  the store holds now (``artifacts.handlers.api_artifact_changed``). The call names the chat the
  work is for, so the gateway tells its observers as that chat's work: an Incognito chat's artifact
  is shown on the open pages and kept out of Knowledge, as it is when the gateway's own agent
  writes it. With no live gateway of the home there is no one to tell: no page is open, and
  Knowledge's copy of that artifact is brought up to date by its next write.

The gateway never tells itself, so it hears its own writes once; a process that is not the gateway
has no observers of its own, so the gateway hears its writes once too. What is told is the
artifact's slug and nothing else, so telling is not a door for text: the content scan reads an
artifact's text once, at the door that writes it (``knowledge.artifact_ingest.text_refusal``), in
whichever process that is.

**An observer, not a poller.** A watched directory is polled because an editor outside the process
edits it, while an artifact only ever changes because something called
:class:`~personalclaw.artifacts.native.NativeArtifactProvider`, and the writing call already knows
which artifact it changed. A signature-diff loop over ``<home>/artifacts`` would re-stat the whole
library forever to learn that, and could not tell a change from a write that changed nothing.

**The vocabulary is deliberately two words** — ``upsert`` and ``delete``. Artifacts do not
own the knowledge library's ``created``/``modified``/``deleted`` change vocabulary
(``knowledge_providers.base``): translating "this artifact now has this body" into
create-vs-modify requires knowing whether the MIRROR already exists, which is knowledge-side
state. Emitting three words from here would mean this module deciding a question it cannot
see the answer to, and a wrong guess would either duplicate a mirror row or drop an edit.

**A listener never breaks a write**, and neither does telling the gateway. :func:`emit` swallows
and logs every listener exception, and a gateway that cannot be told is logged naming the artifact:
an artifact save is the user's work and a downstream indexing fault must not turn a successful save
into a 500. That is the fail-open direction, chosen because the write has already happened by the
time a listener runs — refusing after the fact is not available.
"""

from __future__ import annotations

import logging
import os
from typing import Callable
from urllib.parse import quote

logger = logging.getLogger(__name__)

#: An artifact was created, edited or reverted — its body may now differ.
UPSERT = "upsert"
#: An artifact was removed from the store.
DELETE = "delete"

#: The closed set, so a consumer can reject an unknown change instead of defaulting.
ARTIFACT_CHANGES = frozenset({UPSERT, DELETE})

#: How long a process that wrote an artifact waits for its gateway to take the change. The gateway
#: reads one artifact and answers, so it is as long as a tool waits for a read
#: (``mcp_core.GATEWAY_READ_TIMEOUT_SECS``), not a write: the write has already landed.
GATEWAY_TOLD_WITHIN_SECS = 10.0

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
    """Tell every listener here that *slug* changed, and this home's gateway when it is another
    process (:func:`_tell_the_gateway`). Never raises to the writer."""
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
    _tell_the_gateway(slug)


def _tell_the_gateway(slug: str) -> None:
    """Name *slug* to the gateway of this home when the gateway is another process: the one way
    a write made outside the gateway reaches what follows it there. Never raises.

    The gateway reads the artifact from its store and tells its observers what the store holds,
    so only the slug is sent, never the change or the text. Sent with the internal credential and
    the chat the work is for (``mcp_core._internal_headers``)."""
    try:
        from personalclaw import gateway_base

        gateway = gateway_base.live_gateway()
        if gateway is None or gateway.pid == os.getpid():
            return
        from personalclaw import mcp_core

        answer = mcp_core._post(
            f"/api/artifacts/{quote(slug, safe='')}/changed", timeout=GATEWAY_TOLD_WITHIN_SECS
        )
        error = answer.get("error")
    except Exception as exc:  # noqa: BLE001 — the write has landed; telling must not undo it
        error = str(exc) or type(exc).__name__
    if error:
        logger.warning(
            "artifact %s was written, and the gateway could not be told, so neither Knowledge "
            "nor the open pages heard of it: %s",
            slug,
            error,
        )
