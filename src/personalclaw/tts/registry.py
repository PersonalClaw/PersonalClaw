"""TTS provider registry — resolves the active text-to-speech backend.

Which voice serves ``tts`` is the active selection in ``active_models.json``
(Settings → Models, ``"provider:voice"``); provider-agnostic behavior (enabled,
auto-speak, speaking speed) lives in ``use_case_settings/tts.json``. Backends are
pluggable apps (the local piper backend is the ``piper-tts`` app) + the remote
OpenAI-family adapters; this registry is provider-agnostic.
"""

import logging

from personalclaw.tts.provider import TtsProvider

logger = logging.getLogger(__name__)

_providers: dict[str, TtsProvider] = {}
# Names of the REMOTE adapters we build from config.json (OpenAI-family). Only these are
# dropped on ``refresh_providers`` — the app-registered bundled backend (piper-tts),
# registered once by the app loader on enable, must survive a config change.
_remote_names: set[str] = set()

# active_models.json refs may name the bundled provider as "piper-tts" (manifest
# name) or "piper" (registry key); both map to the one in-process backend.
_PIPER_NAMES = ("piper-tts", "piper")


def register_provider(provider: TtsProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)
    _remote_names.discard(name)


def get_provider(name: str) -> TtsProvider | None:
    return _providers.get(name)


def list_providers() -> list[TtsProvider]:
    return list(_providers.values())


def _ensure_registered() -> None:
    # The local piper backend ships as the ``piper-tts`` APP now (registered by the
    # loader via the ModelTypeHandler ``tts``-capability seam). Here we only ensure the
    # remote OpenAI-family TTS adapters are registered (core-generic). With no local app
    # installed and no remote provider configured, TTS gracefully has no provider.
    _register_remote_providers()


def _register_remote_providers() -> None:
    """Register one remote TTS adapter per OpenAI-family config provider.

    Keyed by the provider's config name so an ``<name>:tts-1`` active selection
    resolves to the same account that backs that provider's chat.
    """
    from personalclaw.providers.use_cases import openai_family_providers
    from personalclaw.tts.openai_provider import OpenAITtsProvider

    for p in openai_family_providers():
        if p["name"] in _providers:
            continue
        register_provider(
            OpenAITtsProvider(
                provider_name=p["name"],
                provider_type=p.get("type", ""),
                endpoint=p["endpoint"],
                api_key=p["api_key"],
            )
        )
        _remote_names.add(p["name"])

    # App-contributed TTS adapters (e.g. Gemini) for config entries the app owns.
    # A scanner provider is AUTHORITATIVE for its (provider, capability): the app
    # ships it precisely because the generic OpenAI-family adapter can't serve this
    # provider's TTS (Gemini's OpenAI-compat endpoint has no audio.speech — TTS goes
    # through generateContent). So it OVERWRITES any same-named family adapter
    # registered above, rather than being skipped when the name already exists.
    from personalclaw.providers.media_scanners import scan

    for prov in scan("tts"):
        nm = getattr(prov, "name", "")
        if nm:
            register_provider(prov)
            _remote_names.add(nm)


def refresh_providers() -> None:
    """Drop only the REMOTE adapters so the next resolution re-reads config providers.

    Called when config.json providers change (added/removed in Settings) so a
    newly-configured remote TTS endpoint becomes selectable without a restart. The
    app-registered bundled backend (piper-tts) is registered once by the app loader on
    enable and MUST survive — clearing it here silently unregistered TTS until the next
    gateway restart (the regression this guards against)."""
    for name in list(_remote_names):
        _providers.pop(name, None)
    _remote_names.clear()


def active_tts() -> tuple[TtsProvider, str] | None:
    """Resolve the active TTS provider + voice id from ``active_models.json``.

    Returns ``(provider, voice_id)`` or None if no TTS voice is selected or its
    provider is unknown. The model ref format is ``"provider_name:voice_id"``.
    """
    from personalclaw.providers.use_cases import active_model_refs, split_ref

    refs = active_model_refs("tts")
    if not refs:
        return None
    parsed = split_ref(refs[0])
    if not parsed:
        return None
    provider_name, voice_id = parsed
    _ensure_registered()
    key = "piper" if provider_name in _PIPER_NAMES else provider_name
    prov = _providers.get(key)
    if prov is None:
        return None
    return (prov, voice_id)


def get_active_provider() -> TtsProvider | None:
    """The active TTS provider (without its voice id)."""
    resolved = active_tts()
    return resolved[0] if resolved else None


def active_voice_params() -> dict | None:
    """Resolve provider-neutral synthesis params from the unified store + settings.

    Returns ``{"provider": TtsProvider, "voice": str, "speed": float,
    "speech_voice": str, "enabled": bool, "auto_speak": bool}`` for the active
    TTS selection, or None when no voice is selected. ``speed`` maps the
    behavioral ``speed`` setting (default 1.0); ``speech_voice`` is the persona
    used by remote providers (alloy / nova / …), ignored by Piper. Each provider
    turns ``voice`` into whatever it needs (Piper a local ``.onnx``, OpenAI a
    hosted model id), so callers stay provider-agnostic.
    """
    from personalclaw.providers.use_cases import load_use_case_settings

    resolved = active_tts()
    if resolved is None:
        return None
    provider, voice_id = resolved
    settings = load_use_case_settings("tts")
    try:
        speed = float(settings.get("speed", 1.0))
    except (TypeError, ValueError):
        speed = 1.0
    return {
        "provider": provider,
        "voice": voice_id,
        "speed": speed,
        "speech_voice": str(settings.get("speech_voice", "") or ""),
        "enabled": bool(settings.get("enabled", False)),
        "auto_speak": bool(settings.get("auto_speak", False)),
    }
