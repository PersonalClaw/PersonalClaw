"""One-click 'create a profile from my current voice' migration (MULTIMODAL-IO §6).

Voice today is a flat ``provider:voice`` selection in ``active_models.json`` plus the
behavioral ``tts`` use-case settings (speed, persona). This promotes that flat
selection into a first-class :class:`~personalclaw.voice.profiles.VoiceProfile` of the
``design`` kind and binds it as the ``default`` surface, so the user's current voice
becomes an editable profile without changing what they hear.

Two rules are deliberate:

* **Captured from the FLAT selection, never the resolved chain.** The source is
  :func:`~personalclaw.tts.registry.active_tts` (the ``active_models.json`` ref) plus the
  ``tts`` behavioral settings — NOT :func:`~personalclaw.tts.registry.active_voice_params`,
  which would walk any existing profile binding and migrate an already-migrated profile
  back onto itself. A design kind carries no reference clip, so this never fabricates
  consent-bearing provenance.
* **Never automatic.** There is no caller on a startup/first-run path; the only trigger
  is the explicit ``POST /api/voice/migrate`` a user's click makes. The zero-profile
  fallback (§1/§6) already reproduces today's output, so nothing here runs on its own.
"""

from __future__ import annotations

from typing import Any

from personalclaw.voice import bindings as vb
from personalclaw.voice import profiles as vp

#: The name a migrated profile gets when the caller does not supply one.
DEFAULT_MIGRATED_NAME = "My current voice"


def active_voice_fields() -> dict[str, Any] | None:
    """The design-profile create fields for the active flat TTS selection, or None.

    None means there is nothing to migrate: no TTS voice is selected, or its provider is
    not registered. The persona (``speech_voice``, used by remote providers) is folded
    into ``design_params`` so a migrated remote voice reproduces the same persona; Piper
    ignores it, exactly as the flat path does.
    """
    from personalclaw.providers.use_cases import load_use_case_settings
    from personalclaw.tts.registry import active_tts

    resolved = active_tts()
    if resolved is None:
        return None
    provider, voice_id = resolved

    settings = load_use_case_settings("tts")
    try:
        speed = float(settings.get("speed", 1.0))
    except (TypeError, ValueError):
        speed = 1.0
    speech_voice = str(settings.get("speech_voice", "") or "")

    fields: dict[str, Any] = {
        "kind": "design",
        "provider": str(getattr(provider, "name", "") or ""),
        "model": str(voice_id or ""),
        "speed": speed,
    }
    if speech_voice:
        fields["design_params"] = {"speech_voice": speech_voice}
    return fields


def migrate_active_to_default_profile(*, name: str = "") -> vp.VoiceProfile:
    """Create a design profile from the active flat voice and bind it ``default``.

    Raises :class:`~personalclaw.voice.profiles.VoiceProfileError` (409
    ``no_active_voice``) when there is no active TTS selection to migrate — the UI shows
    that as "select a voice in Settings → Models first" rather than creating an empty
    profile that renders nothing.
    """
    fields = active_voice_fields()
    if fields is None:
        raise vp.VoiceProfileError(
            "no active TTS voice to migrate — bind one in Settings → Models first",
            409,
            "no_active_voice",
        )
    fields["name"] = str(name or "").strip() or DEFAULT_MIGRATED_NAME
    profile = vp.create_profile(**fields)
    vb.set_binding(vb.DEFAULT_KEY, profile.id)
    return profile
