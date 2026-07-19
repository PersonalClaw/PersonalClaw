"""Image-gen provider registry — resolves the active image-generation backend.

Which model serves ``image_gen`` is the active selection in
``active_models.json`` (Settings -> Models, ``"provider:model"``); per-use-case
behavior lives in ``use_case_settings/image_gen.json``. Mirrors
``stt/registry.py``: the core OpenAI-Images adapter is registered one-per
OpenAI-family config provider; bespoke platforms (FAL) register from their own
removable bundle.
"""

import logging
from typing import Any

from personalclaw.image_gen.provider import ImageGenProvider

logger = logging.getLogger(__name__)

_providers: dict[str, ImageGenProvider] = {}
# Whether the auto-registered providers (OpenAI-family remotes + env stub) have
# been built. Separate from ``_providers`` being non-empty, because a bundled
# provider (FAL via its app manifest → ModelTypeHandler) registers BEFORE first
# resolution — so "registry has entries" no longer implies "auto-providers ran".
_auto_registered = False


def register_provider(provider: ImageGenProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def get_provider(name: str) -> ImageGenProvider | None:
    return _providers.get(name)


def list_providers() -> list[ImageGenProvider]:
    return list(_providers.values())


def _ensure_registered() -> None:
    global _auto_registered
    if not _auto_registered:
        _auto_registered = True
        _register_remote_providers()
        _register_stub_provider()
    # The scanner sweep is NOT under the latch: an app module (e.g. Bedrock) may
    # register its scanner AFTER the first resolution ran (boot-order race), so a
    # once-and-done latch would cache an empty result forever. Re-scan every call —
    # it's cheap and dedupes by provider.name (matches the video_gen registry).
    _ensure_scanned()


_scanner_names: set[str] = set()
"""Provider names contributed by scanners (vs the OpenAI-family loop or bundles).
Tracked so stale entries are removed when a config entry is deleted."""


def _ensure_scanned() -> None:
    """Reconcile scanner-contributed image adapters against the current config.

    A scanner provider is AUTHORITATIVE for its (provider, capability) and
    OVERWRITES any same-named OpenAI-family adapter. It also REMOVES stale
    scanner-contributed providers whose config entry no longer exists (the user
    deleted the provider or uninstalled the app) so they don't ghost the UI.
    """
    from personalclaw.providers.media_scanners import scan
    fresh = scan("image_gen")
    fresh_names = {getattr(p, "name", "") for p in fresh} - {""}
    for prov in fresh:
        nm = getattr(prov, "name", "")
        if nm:
            register_provider(prov)
            _scanner_names.add(nm)
    # Remove stale scanner-contributed providers whose config entry is gone
    stale = _scanner_names - fresh_names
    for nm in stale:
        _providers.pop(nm, None)
    _scanner_names.difference_update(stale)


def _register_remote_providers() -> None:
    """Register one OpenAI-Images adapter per OpenAI-family config provider.

    Keyed by the provider's config name so a ``<name>:gpt-image-1`` active
    selection resolves to the same account that backs that provider's chat —
    exactly the STT registry pattern.
    """
    from personalclaw.image_gen.openai_provider import OpenAIImageProvider
    from personalclaw.providers.use_cases import openai_family_providers

    for p in openai_family_providers():
        if p["name"] in _providers:
            continue
        register_provider(OpenAIImageProvider(
            provider_name=p["name"], provider_type=p.get("type", ""),
            endpoint=p["endpoint"], api_key=p["api_key"],
        ))


def _register_stub_provider() -> None:
    """Register a deterministic, offline stub when ``PCLAW_IMAGE_GEN_STUB=1``.

    A validation/dev affordance ONLY — it returns a tiny solid-color PNG with no
    network call, so the full generate/edit/artifact/render path can be exercised
    repeatedly without spending a paid provider. Never registered in normal runs
    (the env var gates it), so it can't shadow a real provider in production.
    """
    import os

    if os.environ.get("PCLAW_IMAGE_GEN_STUB") != "1":
        return
    from personalclaw.image_gen.stub_provider import StubImageProvider

    if "stub" not in _providers:
        register_provider(StubImageProvider())


def refresh_providers() -> None:
    """Drop the auto-registered remote/stub providers so they re-read on next use.

    Bespoke-platform providers (FAL, ...) are contributed by their bundled app
    manifest through ``ModelTypeHandler`` (enable → register, disable →
    unregister) and are NOT rebuilt by ``_ensure_registered``; clearing them here
    would orphan an enabled bundle. So this preserves any provider whose name the
    auto-registration doesn't own — only the remote OpenAI-family + stub entries
    are transient and safe to drop on a config change.
    """
    global _auto_registered
    from personalclaw.providers.use_cases import openai_family_providers

    transient = {p["name"] for p in openai_family_providers()} | {"stub"}
    for name in list(_providers):
        if name in transient:
            _providers.pop(name, None)
    # Re-arm so the next resolution rebuilds the remote/stub set from current
    # config; manifest-contributed bundles (FAL) are untouched above and stay.
    _auto_registered = False


def active_image_gen() -> tuple[ImageGenProvider, str] | None:
    """Resolve the active image-gen provider + model id from ``active_models.json``.

    Returns ``(provider, model_id)`` or None when no model is selected or its
    provider is unknown. The ref format is ``"provider_name:model_id"``.
    """
    from personalclaw.providers.use_cases import active_model_refs, split_ref

    refs = active_model_refs("image_gen")
    if not refs:
        return None
    parsed = split_ref(refs[0])
    if not parsed:
        return None
    provider_name, model_id = parsed
    _ensure_registered()
    prov = _providers.get(provider_name)
    if prov is None:
        return None
    return (prov, model_id)


def get_active_provider() -> ImageGenProvider | None:
    """The active image-gen provider (without its model id)."""
    resolved = active_image_gen()
    return resolved[0] if resolved else None


async def list_all_providers_info() -> list[dict[str, Any]]:
    """Info for all registered providers, including live availability + active flag."""
    _ensure_registered()
    resolved = active_image_gen()
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
    """List models for a specific provider (shape mirrors the STT models API)."""
    _ensure_registered()
    prov = _providers.get(provider_name)
    if not prov:
        return []
    models = await prov.list_models()
    return [
        {
            "name": m.name,
            "description": m.description,
            "sizes": m.sizes,
            "supports_edit": m.supports_edit,
            "downloaded": m.downloaded,
            "active": m.active,
        }
        for m in models
    ]
