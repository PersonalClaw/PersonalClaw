"""Trigger sources — app-contributed origins of `event`-kind trigger fires (AUTO-A4).

A *trigger source* observes something core knows nothing about (a remote workspace, a device, a
service) and emits typed events onto the ONE event bus under a namespaced source
(``app:<name>:<event>``). `kind: event` triggers then match them with the existing
``{source, pattern}`` spec — no new trigger kind, no second matcher, no per-vendor glue in core.

:mod:`personalclaw.trigger_sources.base` is the contract an app implements (re-exported for apps
via ``personalclaw.sdk.trigger_source``); :mod:`personalclaw.trigger_sources.registry` is the flat
name→provider map plus the fenced, namespaced ingestion path.
"""

from personalclaw.trigger_sources.base import SourceEvent, TriggerSourceProvider
from personalclaw.trigger_sources.registry import (
    NAMESPACE_PREFIX,
    declared_events,
    emit,
    get_source,
    list_sources,
    namespace,
    namespaced_events,
    register_source,
    undeclared_events,
    unregister_source,
)

__all__ = [
    "NAMESPACE_PREFIX",
    "SourceEvent",
    "TriggerSourceProvider",
    "declared_events",
    "emit",
    "get_source",
    "list_sources",
    "namespace",
    "namespaced_events",
    "register_source",
    "undeclared_events",
    "unregister_source",
]
