"""Abstract base for trigger-source providers (AUTOMATION-SUBSTRATE AUTO-A4).

A **trigger source** is an app-contributed origin of events that `kind: event` triggers match.
The app owns the outside world — a remote workspace's message stream, a device's sensor, a
service's webhooks — and turns what it observes into typed events on the ONE event bus
(:func:`personalclaw.event_triggers.emit_event`). Core learns no vendor's schema: the app
supplies an event NAME and a payload, and the existing `{source, pattern}` matcher decides
which triggers fire.

**Why a provider seam rather than a new trigger kind.** The plan's round-2 amendment is explicit:
"no new trigger kind, no second matcher, no bespoke per-vendor glue". `SOURCE_APP` and the
`event` kind already exist; what was missing was a PRODUCER for them. So this seam adds the
producer and nothing else — every gate an `event` fire already walks (the injection screen, the
frozen capability fence, the denylist, incident mode, the debounce + rate cap) governs an
app-sourced fire unchanged, because it re-enters through the same `emit_event` seam a memory
write does.

**The namespace is core's, not the app's.** An app declares its event names; core prefixes them
into ``app:<app-name>:<event>`` at ingestion (:func:`personalclaw.trigger_sources.namespace`).
Two consequences, both deliberate:

* One app cannot forge another's events. The prefix is derived from the REGISTERED name, never
  read from the emit call, so a hostile app naming its event ``app:other-app:thing`` still emits
  under its own namespace.
* A trigger bound to ``app:calendar:meeting_soon`` keeps matching across app updates, because
  the identity is (app name, event name) rather than anything the app can rewrite.

**Fencing happens at INGESTION, at origin.** :func:`personalclaw.trigger_sources.emit` fences the
payload text with rich provenance (``source_type=app:<name>``, ``source_id=<event>``,
``transformation_path=app-source:emit``) before it ever reaches the bus, following the
``web_watch`` precedent (S127). Downstream fencing is idempotent via ``security.is_fenced``, so
the origin's richer provenance survives rather than being re-wrapped with a coarser one.

An app implements :class:`TriggerSourceProvider`, imports it from
``personalclaw.sdk.trigger_source`` (never from this module directly), and registers through the
``trigger_source`` provider type (``providers/registry.py::TriggerSourceTypeHandler``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceEvent:
    """One event an app's source observed — pure data, no behaviour.

    ``event`` is the app's own event NAME (``message_posted``, ``meeting_soon``), unnamespaced:
    core prefixes it. It must be non-empty and is matched literally by a trigger's
    ``event_glob``, so an app that renames an event retires the triggers bound to the old name —
    which is why :attr:`TriggerSourceProvider.events` exists as a declaration a user can browse
    before authoring.

    ``key`` identifies WHICH thing happened (a message id, a calendar entry id). It rides the
    fire record and the fence's ``source_id``, so a reader can tell two fires of the same event
    apart. Empty is tolerated (not every event has an id) rather than rejected, because refusing
    would make a source drop an event over bookkeeping.

    ``text`` is the untrusted prose — the message body, the summary. It is FENCED at ingestion and
    screened before any token is spent. Everything an app wants matched must be here or in
    ``meta``; core reads no other field.

    ``meta`` carries source-specific fields for the pattern matchers to read (the same contract
    the inbox bridge uses for ``sender``/``address``). Coerced to strings at ingestion so a
    nested object cannot smuggle unfenced prose past the screen.
    """

    event: str
    key: str = ""
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class TriggerSourceProvider(ABC):
    """One app-contributed origin of trigger events.

    The provider is a PUSH source: it watches whatever it owns and calls the ``emit`` callable
    handed to it by :meth:`start`. Core never polls it — a poll loop would need core to know the
    source's cadence, cost and rate limits, which is exactly the vendor knowledge the provider
    boundary exists to keep out of core.

    Lifecycle mirrors every other provider type: enabling the app calls :meth:`start`, disabling
    it calls :meth:`stop`. A source that raises from either is deregistered anyway — a provider
    that cannot be stopped cleanly must not be able to hold its registration hostage.
    """

    #: Stable identifier, matched to the app name; the registry keys on it and the event
    #: namespace is derived from it.
    name: str = ""
    #: Human label for the Store / the trigger-create form.
    display_name: str = ""

    @property
    @abstractmethod
    def events(self) -> tuple[str, ...]:
        """The event names this source can emit — the browsable vocabulary.

        DECLARED rather than discovered, so a user authoring a trigger picks from what the source
        actually produces instead of typing a name that will never fire. An event emitted but not
        declared here is still delivered (refusing it would drop real work over a stale manifest)
        and is reported by :func:`personalclaw.trigger_sources.undeclared_events` — the same
        "make the gap queryable rather than fake the behaviour" choice ``triggers/events.py``
        makes for dormant lifecycle events.
        """

    @abstractmethod
    async def start(self, emit: Callable[[SourceEvent], None]) -> None:
        """Begin observing, calling *emit* for each event. Must not block indefinitely.

        *emit* is best-effort and never raises: it fences, namespaces and hands the event to the
        bus. A provider that awaits inside a long-lived watch loop should own its own task —
        ``start`` is called on the enable path, and blocking there would stall the app enable.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Stop observing and release anything ``start`` acquired. Idempotent.

        Called on app disable. Triggers bound to this source are PARKED (not disabled) by the
        type handler once this returns — see ``providers/registry.py::TriggerSourceTypeHandler``.
        """
