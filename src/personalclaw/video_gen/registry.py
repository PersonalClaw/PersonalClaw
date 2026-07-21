"""Video-gen provider registry — resolves the active video-generation backend.

Which model serves ``video_gen`` is the active selection in
``active_models.json`` (Settings -> Models, ``"provider:model"``). Mirrors
``image_gen/registry.py``: bespoke platforms (FAL) register from their own
removable bundle via the ModelTypeHandler.
"""

import logging
from typing import Any

from personalclaw.video_gen.provider import VideoGenProvider

logger = logging.getLogger(__name__)

_providers: dict[str, VideoGenProvider] = {}


def register_provider(provider: VideoGenProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)
    _scanner_names.discard(name)


def refresh_providers() -> None:
    """Drop scanner-contributed providers so the next resolution re-reads config.

    Called when config.json providers change (added/removed in Settings) so a
    deleted video provider disappears without a restart. App-manifest bundles
    (FAL) are NOT touched — they manage their own lifecycle.
    """
    for nm in list(_scanner_names):
        _providers.pop(nm, None)
    _scanner_names.clear()


def get_provider(name: str) -> VideoGenProvider | None:
    _ensure_scanned()
    return _providers.get(name)


_scanner_names: set[str] = set()
"""Provider names that were contributed by scanners (vs app-manifest/FAL bundles).
Tracked so we can REMOVE stale ones when a config entry is deleted."""


def _ensure_scanned() -> None:
    """Reconcile scanner-contributed video adapters against the current config.

    Adds providers for config entries that have a registered scanner, and REMOVES
    any scanner-contributed provider whose config entry no longer exists (the user
    deleted the provider instance or uninstalled the app). App-manifest bundles
    (e.g. FAL, registered via ModelTypeHandler) are NOT touched — they manage their
    own lifecycle via enable/disable.
    """
    from personalclaw.providers.media_scanners import scan

    fresh = scan("video_gen")
    fresh_names = {getattr(p, "name", "") for p in fresh} - {""}
    # Add/update providers from current config entries
    for prov in fresh:
        nm = getattr(prov, "name", "")
        if nm:
            _providers[nm] = prov
            _scanner_names.add(nm)
    # Remove stale: scanner-contributed providers whose config entry is gone
    stale = _scanner_names - fresh_names
    for nm in stale:
        _providers.pop(nm, None)
    _scanner_names.difference_update(stale)


def list_providers() -> list[VideoGenProvider]:
    _ensure_scanned()
    return list(_providers.values())


def active_video_gen() -> tuple[VideoGenProvider, str] | None:
    """Resolve the active video-gen provider + model id from ``active_models.json``.

    Returns ``(provider, model_id)`` or None when no model is selected or its
    provider is unknown. The ref format is ``"provider_name:model_id"``.
    """
    from personalclaw.providers.use_cases import active_model_refs, split_ref

    refs = active_model_refs("video_gen")
    if not refs:
        return None
    parsed = split_ref(refs[0])
    if not parsed:
        return None
    provider_name, model_id = parsed
    _ensure_scanned()
    prov = _providers.get(provider_name)
    if prov is None:
        return None
    return (prov, model_id)


def get_active_provider() -> VideoGenProvider | None:
    """The active video-gen provider (without its model id)."""
    resolved = active_video_gen()
    return resolved[0] if resolved else None


async def list_all_providers_info() -> list[dict[str, Any]]:
    """Info for all registered providers, including live availability + active flag."""
    resolved = active_video_gen()
    active_name = resolved[0].name if resolved else ""

    result = []
    for prov in _providers.values():
        available = await prov.is_available()
        info = prov.info()
        info["available"] = available
        info["active"] = prov.name == active_name
        result.append(info)
    return result


async def list_models_for_provider(provider_name: str) -> list[dict[str, Any]]:
    """List models for a specific provider."""
    prov = _providers.get(provider_name)
    if not prov:
        return []
    models = await prov.list_models()
    return [
        {
            "name": m.name,
            "description": m.description,
            "aspect_ratios": m.aspect_ratios,
            "max_duration_s": m.max_duration_s,
            "downloaded": m.downloaded,
            "active": m.active,
        }
        for m in models
    ]
