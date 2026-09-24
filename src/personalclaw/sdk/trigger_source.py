"""SDK: the trigger-source contract — ``TriggerSourceProvider`` + its event type.

A trigger-source app imports these from ``personalclaw.sdk.trigger_source`` (never from the core
module directly) to contribute an ORIGIN of automation events: it observes something core knows
nothing about and emits typed :class:`SourceEvent`\\ s, which core namespaces
(``app:<name>:<event>``), fences at ingestion, and matches against ``kind: event`` triggers. The app
registers through the ``trigger_source`` provider type
(``providers/registry.py::TriggerSourceTypeHandler``).

The app declares the event NAMES it can emit (``TriggerSourceProvider.events``) so a user authoring
a trigger picks from what the source actually produces. It never chooses its own namespace: the
prefix is derived from the registered app name, so one app cannot emit into another's.
"""

from personalclaw.trigger_sources.base import (  # noqa: F401
    SourceEvent,
    TriggerSourceProvider,
)

__all__ = [
    "TriggerSourceProvider",
    "SourceEvent",
]
