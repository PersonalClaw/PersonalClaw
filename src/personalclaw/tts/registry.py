"""TTS provider registry — resolves the active text-to-speech backend.

Which voice serves ``tts`` is the active selection in ``active_models.json``
(Settings → Models, ``"provider:voice"``); provider-agnostic behavior (enabled,
auto-speak, speaking speed) lives in ``use_case_settings/tts.json``. Backends are
pluggable apps (the local piper backend is the ``piper-tts`` app) + the remote
OpenAI-family adapters; this registry is provider-agnostic.
"""

import logging
from typing import Any, Mapping

from personalclaw.safety_flags import strict_bool
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
    prov = provider_named(provider_name)
    if prov is None:
        return None
    return (prov, voice_id)


def get_active_provider() -> TtsProvider | None:
    """The active TTS provider (without its voice id)."""
    resolved = active_tts()
    return resolved[0] if resolved else None


def provider_named(name: str) -> TtsProvider | None:
    """The TTS provider a ``provider:voice`` ref or a voice profile names, by its app or registry
    name (``piper-tts`` and ``piper`` are the one Piper engine), or None."""
    if not name:
        return None
    _ensure_registered()
    key = "piper" if name in _PIPER_NAMES else name
    return _providers.get(key)


def profile_engine(provider_name: str) -> TtsProvider | None:
    """The engine a voice profile naming *provider_name* speaks with, or None.

    The named engine when it is registered, else the one bound in Models — the order
    :func:`active_voice_params` resolves in, because it asks this. The same answer backs the
    create-time check (:func:`clone_refusal`), so the form cannot accept a voice the resolver
    would then hand to a different engine.
    """
    named = provider_named(provider_name)
    if named is not None:
        return named
    bound = active_tts()
    return bound[0] if bound else None


def _engine_label(engine: object) -> str:
    return str(getattr(engine, "display_name", "") or getattr(engine, "name", "") or "")


def clone_refusal(provider_name: str) -> str:
    """Why a CLONE voice naming *provider_name* could never speak, or ``""`` when it can.

    Asked when the voice is created or its engine changes, rather than first at synthesis,
    which refuses the same request (:func:`guard_synthesis_capability`) only after the voice
    was saved as if it worked.
    """
    engine = profile_engine(provider_name)
    if engine is None:
        return (
            "Cloning needs a text-to-speech engine that can clone a voice from a reference "
            "clip, and none is set up. Add one from Apps, then choose it as this voice's engine."
        )
    if getattr(engine, "supports_cloning", False):
        return ""
    return (
        f"{_engine_label(engine)} can't clone a voice. Cloning needs a text-to-speech engine "
        "that can clone from a reference clip: choose one as this voice's engine, or add one "
        "from Apps."
    )


def engine_catalog() -> dict[str, Any]:
    """The engines a voice profile can name, whether each can clone, and the bound one."""
    _ensure_registered()
    bound = active_tts()
    return {
        "engines": [
            {
                "name": str(getattr(p, "name", "") or ""),
                "display_name": _engine_label(p),
                "clones": bool(getattr(p, "supports_cloning", False)),
            }
            for p in list_providers()
        ],
        "bound_engine": str(getattr(bound[0], "name", "") or "") if bound else "",
    }


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
    from personalclaw.providers.use_cases import load_use_case_settings, use_case_enabled
    from personalclaw.voice.bindings import resolve_profile_id
    from personalclaw.voice.profiles import artifact_path, get_profile

    pid, level = resolve_profile_id(surface=surface, explicit=profile_id)
    profile = get_profile(pid) if pid else None

    resolved = active_tts()
    if profile is None:
        if resolved is None:
            return None
        provider, voice_id = resolved
    else:
        # The profile's own engine when it is registered, else the flat selection's; with
        # neither there is nothing to render with.
        engine = profile_engine(profile.provider)
        if engine is None:
            return None
        provider = engine
        voice_id = profile.model or (resolved[1] if resolved else "")

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
        "enabled": use_case_enabled("tts", settings),
        "auto_speak": strict_bool(
            settings.get("auto_speak"), field="tts settings auto_speak", default=False
        ),
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
# A voice PROFILE can carry a reference clip (clone kind) or a text/param
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


class TtsNotReady(Exception):
    """The bound provider says it cannot speak with the bound voice now (HTTP 503).

    Raised before any audio is asked for, so the answer names the reason (a voice that is not
    downloaded, a missing runtime or key) instead of a synthesis that ran and produced nothing.
    Carries its status and the sentence to show. A route that answers it sends the registered
    ``tts_not_ready`` code as a literal, so the error-code registry check can read it.
    """

    def __init__(self, provider: object, voice: str):
        self.status = 503
        name = str(getattr(provider, "name", "") or "")
        shown = str(getattr(provider, "display_name", "") or name or "The provider")
        self.message = (
            f"{shown} says it cannot speak with {voice or 'the chosen voice'} right now. "
            "Check the text-to-speech model in Settings → Models."
        )
        super().__init__(self.message)


async def can_speak(provider: object, voice: str) -> bool:
    """Whether *provider* says it can produce audio for *voice* now (``can_synthesize``).

    Asked by every synthesis a user hears, before it starts. An adapter that does not subclass
    :class:`TtsProvider` may leave the method out; it then makes no claim, and its synthesis
    answers for itself.
    """
    check = getattr(provider, "can_synthesize", None)
    return True if check is None else bool(await check(voice))


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
    synthesizing in the wrong voice), refuses a voice the provider cannot speak with now
    (:class:`TtsNotReady`), then dispatches to ``provider.synthesize`` with the
    conditioning set MI-1 threaded into the ABC signature. A backend ignores any knob it
    does not use via ``**opts``, so piper/OpenAI are unchanged.
    """
    from personalclaw.guardrails.media_call import MediaCall, metered_media_call
    from personalclaw.providers.engines import binding_name

    provider: TtsProvider = params["provider"]
    guard_synthesis_capability(provider, params)
    voice = str(params.get("voice", "") or "")
    if not await can_speak(provider, voice):
        raise TtsNotReady(provider, voice)
    # Metered by the characters it speaks: held to the dollar caps first when it is unattended
    # work, and counted in Usage either way.
    return await metered_media_call(
        MediaCall(
            provider=binding_name(provider),
            model=voice,
            unit="character",
            quantity=len(text),
        ),
        lambda: provider.synthesize(
            text,
            voice=voice,
            output_path=output_path,
            speed=float(params.get("speed", 1.0) or 1.0),
            speech_voice=str(params.get("speech_voice", "") or ""),
            ref_audio=str(params.get("ref_audio", "") or ""),
            ref_text=str(params.get("ref_text", "") or ""),
            seed=int(params.get("seed", 0) or 0),
            instruct=str(params.get("instruct", "") or ""),
            design_params=dict(params.get("design_params") or {}),
        ),
    )
