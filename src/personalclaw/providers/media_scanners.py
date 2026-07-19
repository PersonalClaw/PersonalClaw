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
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# capability → list of scanner callables. A scanner: (entries) -> [provider, …]
_scanners: dict[str, list[Callable[[list[dict[str, Any]]], list[Any]]]] = {}


def register_scanner(
    capability: str, scanner: Callable[[list[dict[str, Any]]], list[Any]]
) -> None:
    """Register a config scanner for a media ``capability``.

    Idempotent per (capability, scanner identity): re-registering the same
    function object is a no-op, so an app module re-imported in tests doesn't
    stack duplicate scanners.
    """
    lst = _scanners.setdefault(capability, [])
    if scanner not in lst:
        lst.append(scanner)


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
        try:
            out.extend(fn(entries) or [])
        except Exception:  # noqa: BLE001 — a bad scanner can't break discovery
            logger.debug("media scanner for %r failed", capability, exc_info=True)
    return out


def _config_provider_entries() -> list[dict[str, Any]]:
    """The ``providers[]`` array from config.json (``[{name, type, options}]``)."""
    import json

    from personalclaw.config.loader import config_path

    path = config_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    providers = data.get("providers") if isinstance(data, dict) else None
    return [p for p in providers if isinstance(p, dict)] if isinstance(providers, list) else []
