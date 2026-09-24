"""STT provider registry — resolves the active speech-to-text backend.

Which model serves ``stt`` is the active selection in ``active_models.json``
(Settings → Models, ``"provider:model"``); provider-agnostic behavior (enabled,
language, streaming) lives in ``use_case_settings/stt.json``. faster-whisper is
the sole bundled backend.
"""

import logging

from personalclaw.stt.provider import SttProvider

logger = logging.getLogger(__name__)

_providers: dict[str, SttProvider] = {}
# Names of the REMOTE adapters we build from config.json (OpenAI-family). Only these are
# dropped on ``refresh_providers`` — app-registered bundled providers (faster-whisper),
# which the app loader registers once on enable, must survive a config change.
_remote_names: set[str] = set()

# active_models.json refs may name the bundled provider as "faster-whisper"
# (manifest name) or "faster_whisper" (registry key); both map to the one
# in-process backend.
_FASTER_WHISPER_NAMES = ("faster-whisper", "faster_whisper")


def register_provider(provider: SttProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)
    _remote_names.discard(name)


def get_provider(name: str) -> SttProvider | None:
    return _providers.get(name)


def list_providers() -> list[SttProvider]:
    return list(_providers.values())


def _ensure_registered() -> None:
    # The local faster-whisper backend ships as the ``faster-whisper`` APP now (the
    # app loader registers it via the ModelTypeHandler ``stt``-capability seam). Here
    # we only ensure the remote OpenAI-family STT adapters are registered (core-generic,
    # one per configured provider). With no local app installed and no remote provider
    # configured, STT gracefully has no provider.
    _register_remote_providers()


def _register_remote_providers() -> None:
    """Register one remote STT adapter per OpenAI-family config provider.

    Keyed by the provider's config name so an ``<name>:whisper-1`` active
    selection resolves to the same account that backs that provider's chat.
    """
    from personalclaw.providers.use_cases import openai_family_providers
    from personalclaw.stt.openai_provider import OpenAISttProvider

    for p in openai_family_providers():
        if p["name"] in _providers:
            continue
        register_provider(
            OpenAISttProvider(
                provider_name=p["name"],
                provider_type=p.get("type", ""),
                endpoint=p["endpoint"],
                api_key=p["api_key"],
            )
        )
        _remote_names.add(p["name"])

    # App-contributed STT adapters (e.g. Bedrock, Gemini) for config entries the
    # app owns — the app registered a scanner on import. A scanner provider is
    # AUTHORITATIVE for its (provider, capability) and OVERWRITES any same-named
    # OpenAI-family adapter registered above: the app ships it because the generic
    # adapter can't serve this provider's STT (e.g. Gemini transcribes via
    # generateContent, not the OpenAI audio.transcriptions API), and ``google`` is
    # in the OpenAI family, so without the overwrite the family adapter would shadow
    # the real one. Tracked as remote so refresh_providers() re-reads on a change.
    from personalclaw.providers.media_scanners import scan

    for prov in scan("stt"):
        nm = getattr(prov, "name", "")
        if nm:
            register_provider(prov)
            _remote_names.add(nm)


def refresh_providers() -> None:
    """Drop only the REMOTE adapters so the next resolution re-reads config providers.

    Called when config.json providers change (added/removed in Settings) so a
    newly-configured remote STT endpoint becomes selectable without a restart. The
    app-registered bundled backend (faster-whisper) is registered once by the app
    loader on enable and MUST survive — clearing it here silently unregistered STT
    until the next gateway restart (the regression this guards against)."""
    for name in list(_remote_names):
        _providers.pop(name, None)
    _remote_names.clear()


def active_stt() -> tuple[SttProvider, str] | None:
    """Resolve the active STT provider + model id from ``active_models.json``.

    Returns ``(provider, model_id)`` or None if no STT model is selected or its
    provider is unknown. The model ref format is ``"provider_name:model_id"``.
    """
    from personalclaw.providers.use_cases import active_model_refs, split_ref

    refs = active_model_refs("stt")
    if not refs:
        return None
    parsed = split_ref(refs[0])
    if not parsed:
        return None
    provider_name, model_id = parsed
    _ensure_registered()
    key = "faster_whisper" if provider_name in _FASTER_WHISPER_NAMES else provider_name
    prov = _providers.get(key)
    if prov is None:
        return None
    return (prov, model_id)


def get_active_provider() -> SttProvider | None:
    """The active STT provider (without its model id)."""
    resolved = active_stt()
    return resolved[0] if resolved else None
