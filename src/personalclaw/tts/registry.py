"""TTS provider registry — resolves the active text-to-speech backend.

Which voice serves ``tts`` is the active selection in ``active_models.json``
(Settings → Models, ``"provider:voice"``); provider-agnostic behavior (enabled,
auto-speak, speaking speed) lives in ``use_case_settings/tts.json``. Backends are
pluggable apps (the local piper backend is the ``piper-tts`` app) + the remote
OpenAI-family adapters; this registry is provider-agnostic.
"""

import logging
from typing import Any, Mapping

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


def _provider_by_app_name(name: str) -> TtsProvider | None:
    """A TTS provider by the app/registry name a voice profile records."""
    if not name:
        return None
    _ensure_registered()
    key = "piper" if name in _PIPER_NAMES else name
    return _providers.get(key)


def active_voice_params(*, surface: str = "", profile_id: str = "") -> dict | None:
    """Resolve provider-neutral synthesis params from the unified store + settings.

    Returns ``{"provider": TtsProvider, "voice": str, "speed": float,
    "speech_voice": str, "enabled": bool, "auto_speak": bool}`` for the active
    TTS selection, or None when no voice is selected. ``speed`` maps the
    behavioral ``speed`` setting (default 1.0); ``speech_voice`` is the persona
    used by remote providers (alloy / nova / …), ignored by Piper. Each provider
    turns ``voice`` into whatever it needs (Piper a local ``.onnx``, OpenAI a
    hosted model id), so callers stay provider-agnostic.

    Profile-aware (MULTIMODAL-IO §3.2): ``surface`` (``channel:webui``,
    ``agent:<slug>``, …) and an ``profile_id`` override walk the four-level chain
    (explicit > binding > default > built-in). When a profile wins, its provider /
    model / speed shadow the flat selection and the dict grows a SUPERSET of keys —
    ``profile_id``, ``profile_level``, ``ref_audio`` (absolute, the locked clip when
    the profile is locked), ``ref_text``, ``seed``, ``instruct``, ``design_params``,
    ``locked``. When nothing resolves (the common case, and every case before a user
    creates a profile) the returned dict is EXACTLY the pre-profile six keys, so an
    empty store reproduces today's flat output rather than merely approximating it.

    The conditioning keys are carried, not consumed: threading ``ref_audio``/``seed``
    into ``TtsProvider.synthesize`` needs the capability flags MI-2 adds, and handing
    a reference clip to a non-cloning engine would be the silent wrong-voice
    synthesis the plan forbids.
    """
    from personalclaw.providers.use_cases import load_use_case_settings
    from personalclaw.voice.bindings import resolve_profile_id
    from personalclaw.voice.profiles import artifact_path, get_profile

    pid, level = resolve_profile_id(surface=surface, explicit=profile_id)
    profile = get_profile(pid) if pid else None

    resolved = active_tts()
    if resolved is None:
        # No flat selection: a profile can still stand alone, but only if the engine
        # it names is actually registered (otherwise there is nothing to render with).
        prov = _provider_by_app_name(profile.provider) if profile is not None else None
        if profile is None or prov is None:
            return None
        provider, voice_id = prov, profile.model
    else:
        provider, voice_id = resolved
        if profile is not None:
            prov = _provider_by_app_name(profile.provider)
            if prov is not None:
                provider = prov
            if profile.model:
                voice_id = profile.model

    settings = load_use_case_settings("tts")
    try:
        speed = float(settings.get("speed", 1.0))
    except (TypeError, ValueError):
        speed = 1.0
    params = {
        "provider": provider,
        "voice": voice_id,
        "speed": speed,
        "speech_voice": str(settings.get("speech_voice", "") or ""),
        "enabled": bool(settings.get("enabled", False)),
        "auto_speak": bool(settings.get("auto_speak", False)),
    }
    if profile is None:
        return params

    ref = ""
    rel = "locked.wav" if profile.locked else profile.ref_audio
    if rel:
        try:
            candidate = artifact_path(profile.id, rel)
            ref = str(candidate) if candidate.is_file() else ""
        except Exception:
            ref = ""
    params["speed"] = profile.speed or speed
    params.update(
        {
            "profile_id": profile.id,
            "profile_level": level,
            "ref_audio": ref,
            "ref_text": profile.ref_text,
            "seed": profile.seed,
            "instruct": profile.instruct,
            "design_params": dict(profile.design_params),
            "locked": profile.locked,
        }
    )
    return params


# ── Cloning-capable synthesis: the capability gate synth surfaces route through ──
#
# MI-2a. A voice PROFILE can carry a reference clip (clone kind) or a text/param
# description (design kind); `active_voice_params` resolves those into its dict but
# deliberately does not consume them — handing a reference clip to a non-cloning engine
# would be the silent wrong-voice synthesis the plan forbids. This section is where that
# refusal lives: a synth surface calls `route_synthesis`, which enforces the provider's
# declared capability BEFORE any audio is produced.


class CloningUnsupportedError(Exception):
    """A clone-kind synth request routed to a provider that cannot clone (HTTP 409).

    Mirrors :class:`~personalclaw.voice.profiles.VoiceProfileError`: the exception carries
    the status the route should answer with — 409, because the requested voice CONFLICTS
    with the bound engine's capabilities — and a stable ``reason`` an HTTP client branches
    on. Its string form is ``cloning_unsupported:<provider>``.
    """

    def __init__(self, provider: str):
        self.provider = provider
        self.reason = "cloning_unsupported"
        self.status = 409
        self.message = f"cloning_unsupported:{provider}"
        super().__init__(self.message)


def is_clone_request(params: Mapping[str, Any]) -> bool:
    """Whether resolved synth *params* ask for voice CLONING — i.e. carry a reference clip.

    Clone kind is signalled by a non-empty ``ref_audio`` (the locked/reference clip the
    profile resolves to). Voice-DESIGN (``design_params``/``instruct`` with no reference)
    is a separate kind, not a clone request, and is not gated here.
    """
    return bool(params.get("ref_audio"))


def guard_synthesis_capability(provider: TtsProvider, params: Mapping[str, Any]) -> None:
    """Fail-closed capability gate for a synth request about to be dispatched.

    Raises :class:`CloningUnsupportedError` (HTTP 409) when a clone-kind request
    (:func:`is_clone_request`) is routed to a provider that does not declare
    ``supports_cloning``. Fail-closed twice over: the flag itself defaults False AND the
    lookup defaults False, so a provider that says nothing is treated as unable to clone
    rather than assumed capable. A non-clone request is always allowed through.
    """
    if is_clone_request(params) and not getattr(provider, "supports_cloning", False):
        raise CloningUnsupportedError(getattr(provider, "name", "") or "")


async def route_synthesis(
    params: Mapping[str, Any], text: str, *, output_path: str = ""
) -> str | None:
    """Route a resolved synth request to its provider, enforcing capability first.

    The single chokepoint a synth surface hands the dict :func:`active_voice_params`
    returns: it applies :func:`guard_synthesis_capability` (so a clone-kind request to a
    non-cloning engine raises :class:`CloningUnsupportedError` — HTTP 409 — rather than
    synthesizing in the wrong voice), then dispatches to ``provider.synthesize`` with the
    conditioning set MI-1 threaded into the ABC signature. A backend ignores any knob it
    does not use via ``**opts``, so piper/OpenAI are unchanged.
    """
    provider: TtsProvider = params["provider"]
    guard_synthesis_capability(provider, params)
    return await provider.synthesize(
        text,
        voice=str(params.get("voice", "") or ""),
        output_path=output_path,
        speed=float(params.get("speed", 1.0) or 1.0),
        speech_voice=str(params.get("speech_voice", "") or ""),
        ref_audio=str(params.get("ref_audio", "") or ""),
        ref_text=str(params.get("ref_text", "") or ""),
        seed=int(params.get("seed", 0) or 0),
        instruct=str(params.get("instruct", "") or ""),
        design_params=dict(params.get("design_params") or {}),
    )
