"""The flat trigger-source registry + the fenced ingestion path (AUTO-A4).

Mirrors :mod:`personalclaw.sync_transports.registry` and ``action_providers/registry.py``: the
``trigger_source`` provider-type handler (``providers/registry.py::TriggerSourceTypeHandler``)
registers an installed source here on enable and removes it on disable.

Unlike those registries this one also owns the **ingestion path** (:func:`emit`), because the
namespace and the fence are core's to apply, not the app's. An app hands over a
:class:`~personalclaw.trigger_sources.base.SourceEvent`; core derives the namespaced source
string from the REGISTERED name, fences the untrusted text with full provenance, and calls the
one bus emitter. Nothing an app passes can change which namespace it emits under.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.trigger_sources.base import SourceEvent, TriggerSourceProvider

logger = logging.getLogger(__name__)

#: The namespace prefix for every app-contributed event source. A trigger's ``spec.source`` is
#: ``app`` (``event_triggers.SOURCE_APP``) and its ``event_glob`` matches this namespaced name, so
#: the prefix is what keeps one app's events from matching another's glob by accident.
NAMESPACE_PREFIX = "app"

_sources: dict[str, "TriggerSourceProvider"] = {}

#: Event names an app emitted that its own ``events`` tuple does not declare, per source. Recorded
#: rather than refused: dropping a real event over a stale declaration would lose the user's work,
#: while an undeclared event that fires but appears in no browsable list is a gap someone must be
#: able to SEE. Queried by :func:`undeclared_events` (the doctor/test surface), which is the same
#: "make it queryable" treatment ``triggers/events.py`` gives dormant lifecycle events.
_undeclared: dict[str, set[str]] = {}


def namespace(source_name: str, event: str) -> str:
    """The namespaced bus event name for *event* from the source *source_name*.

    ``app:<source>:<event>`` — the plan's literal shape. Built from the registered name, never
    from anything the app supplies at emit time, so an app cannot emit into another's namespace.
    """
    return f"{NAMESPACE_PREFIX}:{source_name}:{event}"


def register_source(provider: "TriggerSourceProvider") -> None:
    _sources[provider.name] = provider


def unregister_source(name: str) -> None:
    _sources.pop(name, None)
    _undeclared.pop(name, None)


def get_source(name: str) -> "TriggerSourceProvider | None":
    return _sources.get(name)


def list_sources() -> list[str]:
    return list(_sources.keys())


def declared_events() -> dict[str, tuple[str, ...]]:
    """Every registered source's declared event names — the browsable vocabulary.

    What the trigger-create surface offers, so an author picks a name that can actually fire
    rather than typing one that never will. A provider whose ``events`` property raises is
    reported as declaring nothing rather than breaking the whole listing: one broken app must not
    make every other source unauthorable.
    """
    out: dict[str, tuple[str, ...]] = {}
    for name, provider in _sources.items():
        try:
            out[name] = tuple(str(e) for e in provider.events if str(e))
        except Exception:  # noqa: BLE001 - see the docstring
            logger.debug("trigger source %r could not list its events", name, exc_info=True)
            out[name] = ()
    return out


def namespaced_events() -> list[str]:
    """Every declared event as its full namespaced bus name, sorted.

    The concrete list a trigger's ``event_glob`` matches against, so a UI or a doctor check
    compares like with like instead of re-deriving the prefix at each call site.
    """
    return sorted(
        namespace(name, event) for name, events in declared_events().items() for event in events
    )


def undeclared_events(name: str = "") -> dict[str, list[str]]:
    """Events a source EMITTED but does not declare — the queryable gap.

    Empty is the healthy state. A non-empty entry means the app's ``events`` tuple is stale
    against what it actually produces, so an author browsing the vocabulary cannot find a live
    event. Reported rather than enforced: refusing the emit would drop the user's real work to
    punish the app's bookkeeping.
    """
    if name:
        return {name: sorted(_undeclared.get(name, set()))} if name in _undeclared else {}
    return {src: sorted(events) for src, events in _undeclared.items() if events}


def emit(source_name: str, event: "SourceEvent", *, now: float = 0.0) -> str:
    """Ingest one app-sourced event onto the bus. Returns the namespaced name, or "" on refusal.

    **The single ingestion point**, so the namespace, the fence and the provenance cannot be
    applied differently by two call sites — the failure mode that made the ``web_watch`` screen
    gap (S134) invisible for a release.

    Order, and why each step is where it is:

    1. **Refuse an unregistered source.** A source that is not registered is one whose app is
       disabled or uninstalled; accepting its events would let a stopped app keep firing
       automations, which is the opposite of what disable means.
    2. **Refuse an empty event name.** An unnamed event matches no glob a user can author, so it
       could only ever fire a catch-all — a silent widening of whatever that trigger meant.
    3. **Namespace from the REGISTERED name.** Never from the payload (see :func:`namespace`).
    4. **Fence the text AT ORIGIN**, with provenance naming the class (``app:<name>``), the
       instance (the event's key) and the transformation. The ``web_watch`` precedent (S127):
       fencing here means the downstream ``fence_payload`` leaves it alone (idempotent via
       ``security.is_fenced``) and the richer attributes survive, instead of a coarse re-wrap.
    5. **Hand to the ONE bus emitter.** `emit_event` is best-effort and never raises, so a broken
       trigger cannot break the app's own work.

    Never raises: a source calls this from its own watch loop, and an ingestion fault must not
    take down the app that observed the event.
    """
    try:
        if source_name not in _sources:
            logger.debug("dropping an event from unregistered trigger source %r", source_name)
            return ""
        event_name = str(getattr(event, "event", "") or "").strip()
        if not event_name:
            logger.warning(
                "trigger source %r emitted an event with no name; dropped (an unnamed event "
                "matches no authorable glob)",
                source_name,
            )
            return ""
        _note_undeclared(source_name, event_name)

        namespaced = namespace(source_name, event_name)
        key = str(getattr(event, "key", "") or "")
        raw_text = str(getattr(event, "text", "") or "")

        from personalclaw.security import fence_untrusted

        # Fenced for EVERY event, not only a suspicious one — an app's payload is untrusted text
        # by definition (the plan: "app-sourced payloads are untrusted text"). Fencing only the
        # flagged ones would mean the screen's misses arrive as instructions.
        fenced = fence_untrusted(
            raw_text,
            source=f"trigger:{namespaced}",
            source_type=f"{NAMESPACE_PREFIX}:{source_name}",
            source_id=key or event_name,
            transformation_path="app-source:emit",
        )

        from personalclaw.event_triggers import SOURCE_APP, emit_event

        emit_event(
            source=SOURCE_APP,
            event_type=namespaced,
            key=key,
            value=fenced,
            now=now or time.time(),
            meta=_provenance_meta(source_name, event_name, namespaced, event),
        )
        return namespaced
    except Exception:  # noqa: BLE001 - see the docstring
        logger.debug("app trigger-source ingestion failed for %r", source_name, exc_info=True)
        return ""


def _note_undeclared(source_name: str, event_name: str) -> None:
    """Record an emitted-but-undeclared event name. Best-effort, never raises."""
    try:
        provider = _sources.get(source_name)
        declared = {str(e) for e in (provider.events if provider else ())}
        if event_name not in declared:
            _undeclared.setdefault(source_name, set()).add(event_name)
    except Exception:  # noqa: BLE001 - bookkeeping must not break an ingest
        logger.debug("could not check %r's declared events", source_name, exc_info=True)


def _provenance_meta(
    source_name: str, event_name: str, namespaced: str, event: "SourceEvent"
) -> dict[str, Any]:
    """The event's `meta` dict: core's provenance keys plus the app's own fields.

    Core's four keys are written LAST so an app cannot overwrite them with a forged app name —
    provenance an app can rewrite is provenance nobody can rely on. The app's values are coerced
    to strings because the pattern matchers glob them, and because a nested object in `meta` is
    prose that would reach a provider without passing the fence (the payload's ``value`` is the
    only field fenced; `meta` is matched, not narrated).
    """
    meta: dict[str, Any] = {}
    raw = getattr(event, "meta", None)
    if isinstance(raw, dict):
        for key, value in raw.items():
            meta[str(key)] = value if isinstance(value, (str, int, float, bool)) else str(value)
    meta["app"] = source_name
    meta["app_event"] = event_name
    meta["source_event"] = namespaced
    meta["provenance"] = f"{NAMESPACE_PREFIX}:{source_name}"
    return meta
