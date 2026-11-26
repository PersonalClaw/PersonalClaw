"""Media-capability config scanners — an app-owned extension point.

The typed media registries (image_gen, video_gen, stt, tts, embedding) build a
per-capability ADAPTER for each configured model provider so one config.json
entry serves every use-case that provider supports. Core knows the OpenAI-family
built-in; any OTHER provider (Bedrock, …) contributes its adapters here WITHOUT
core needing to know the provider — the app registers a scanner on import.

A scanner for capability ``cap`` is a callable that takes the list of
config.json provider entries (``[{name, type, options}, …]``) and returns the
provider adapters it wants registered for that capability. It should return only
adapters for entries whose ``type`` it owns, and is called every time a registry
(re)builds — so it must be cheap + idempotent (return fresh adapter objects;
the registry dedupes by ``provider.name``).

This keeps core provider-agnostic: Bedrock's image/video/stt/embedding logic
lives entirely in the bedrock-models app, which calls ``register_scanner`` at
import for each capability it serves.

A scanner an app's code registered acts only while the app capability it belongs to runs
(#3843, ``capability_switches.registration_acts``): one a capability's build registered is that
capability's, and one its modules registered as they loaded belongs to the app's capabilities
that declare its media kind. A switch changes :func:`generation`, so a registry that keeps what
it built rebuilds from what acts now. A scanner core registers acts always.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from personalclaw import app_code

logger = logging.getLogger(__name__)

# capability → list of scanner callables. A scanner: (entries) -> [provider, …]
_scanners: dict[str, list[Callable[[list[dict[str, Any]]], list[Any]]]] = {}
#: ``(media kind, scanner)`` for each scanner an app's code registered → ``(app, capability)``
#: (``app_code.owned``).
_owners: dict[tuple[str, Callable[[list[dict[str, Any]]], list[Any]]], tuple[str, str]] = {}

#: How many times the registered scanners changed: an app's import registered one, or the app's
#: unload took one back (an update does both). A registry that keeps the adapters it was handed
#: reads it to know its adapters may now be another version's code (:func:`generation`).
_generation = 0


def register_scanner(capability: str, scanner: Callable[[list[dict[str, Any]]], list[Any]]) -> None:
    """Register a config scanner for a media ``capability``.

    Idempotent per (capability, scanner identity): re-registering the same
    function object is a no-op. A scanner an app registered is taken back when the app is
    unloaded, so its next version's import registers its own instead of a second one.
    """
    global _generation
    lst = _scanners.setdefault(capability, [])
    if scanner not in lst:
        lst.append(scanner)
        _generation += 1
        held = app_code.owned()
        if held is not None:
            _owners[(capability, scanner)] = held

        def _forget() -> None:
            global _generation
            if scanner in lst:
                lst.remove(scanner)
                _owners.pop((capability, scanner), None)
                _generation += 1

        app_code.keep(_forget)


def generation() -> int:
    """A number that changes whenever a scanner is registered or taken back, or an app's
    capability is built or torn down (which may change which scanners act)."""
    from personalclaw.providers.registry import state_changes

    return _generation + state_changes()


def _acts(scanner: Callable[[list[dict[str, Any]]], list[Any]], kind: str) -> bool:
    """Whether *scanner* builds *kind* adapters now: core's always; an app's while its
    capability runs. A state that cannot be read acts on nothing, as unreadable switches do."""
    held = _owners.get((kind, scanner))
    if held is None:
        return True
    try:
        from personalclaw.apps.capability_switches import registration_acts

        return registration_acts(held[0], held[1], kind)
    except Exception:  # noqa: BLE001 — the app's scanner sits out; the others still run
        logger.warning("media scanner of %s: its capability's state unreadable", held[0])
        return False


def scan(capability: str) -> list[Any]:
    """Run every registered scanner for ``capability`` against the current
    config.json provider entries; return the flattened adapter list.

    Import-guarded + best-effort: one scanner raising never blocks the others
    or the registry's own built-in discovery.
    """
    scanners = _scanners.get(capability)
    if not scanners:
        return []
    entries = _config_provider_entries()
    out: list[Any] = []
    for fn in scanners:
        if not _acts(fn, capability):
            continue
        try:
            out.extend(fn(entries) or [])
        except Exception:  # noqa: BLE001 — a bad scanner can't break discovery
            logger.debug("media scanner for %r failed", capability, exc_info=True)
    return out


def _config_provider_entries() -> list[dict[str, Any]]:
    """The ``providers[]`` array from config.json (``[{name, type, options}]``), options
    RESOLVED — a scanner builds adapters that authenticate with the stored key, and a
    ``{{secret:…}}`` reference in its place would reach the vendor as the credential."""
    import json

    from personalclaw.config.loader import config_path
    from personalclaw.config.secret_refs import resolve_provider_records

    path = config_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return resolve_provider_records(data.get("providers") if isinstance(data, dict) else None)
