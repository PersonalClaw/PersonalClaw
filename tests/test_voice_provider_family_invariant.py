"""Success Criterion 10 — the voice surface adds no provider family (MULTIMODAL-IO §2.3, MI-5).

The whole voice-profile/binding/migration/cloning surface is a set of ENTITIES and an
HTTP surface, not a new app-provider type and not a new hook action. This is the
capstone's byte-identical assertion: snapshot the three provider-family collections,
drive the full voice matrix across BOTH engines, snapshot again, and require every
collection to be byte-for-byte unchanged.

  * ``ALLOWED_HOOK_PROVIDERS`` — the hook-action allowlist (no ``voice`` hook action crept in);
  * ``PROVIDER_TYPES`` — the manifest validator's declarable-type allowlist
    (no ``voice_profile`` type);
  * the runtime type-handler set — what the provider registry actually serves.

A future voice change that registers a handler, mints a provider type, or adds a hook
action at import or exercise time reds this — the "no hook-action creep" rail with teeth.
"""

from __future__ import annotations

import wave

import pytest


def _snapshot() -> dict[str, tuple[str, ...]]:
    """The three provider-family collections as sorted tuples (order-independent equality)."""
    from personalclaw.apps.manifest import PROVIDER_TYPES
    from personalclaw.providers.registry import get_provider_registry
    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    return {
        "hook_providers": tuple(sorted(ALLOWED_HOOK_PROVIDERS)),
        "provider_types": tuple(sorted(PROVIDER_TYPES)),
        "type_handlers": tuple(sorted(get_provider_registry()._type_handlers)),
    }


class _StubTts:
    supports_cloning = False

    def __init__(self, name="piper"):
        self._name = name
        self.calls: list[dict] = []

    @property
    def name(self):
        return self._name

    @property
    def display_name(self):
        return self._name

    async def is_available(self):
        return True

    async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
        self.calls.append({"text": text, "voice": voice, **opts})
        return output_path or "/tmp/out.wav"


class _CloningTts(_StubTts):
    supports_cloning = True


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr("personalclaw.voice.profiles.config_dir", lambda: root)
    monkeypatch.setattr("personalclaw.voice.bindings.config_dir", lambda: root)
    monkeypatch.setattr(
        "personalclaw.tts.registry.active_tts", lambda: (_StubTts("piper"), "en_US-amy")
    )
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_use_case_settings",
        lambda use_case: {"speed": 1.0, "speech_voice": ""},
    )
    return root


def _wav(path, seconds=1.2, rate=16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as h:
        h.setnchannels(1)
        h.setsampwidth(2)
        h.setframerate(rate)
        h.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


async def _exercise_the_whole_voice_matrix(home):
    """Every voice mutation the capstone spans — profile CRUD × lock × consent × both
    engines × per-surface bindings × migration — with zero model spend."""
    from personalclaw.tts.registry import route_synthesis
    from personalclaw.voice import bindings as vb
    from personalclaw.voice import migration as vm
    from personalclaw.voice import profiles as vp

    # design + clone CRUD
    design = vp.create_profile(name="Designed", kind="design", provider="piper", speed=1.1)
    clone = vp.create_profile(name="Cloned", kind="clone", provider="voice-clone-tts")
    vp.update_profile(design.id, seed=7)

    # lock-from-history
    vp.append_history(clone.id, _wav(home / "gen.wav"), seed=3)
    vp.lock_profile(clone.id, 0)

    # consent record → verify → revoke
    vp.attach_consent_audio(clone.id, _wav(home / "consent.wav"))
    vp.record_consent(clone.id, consent_text="I consent to cloning my own voice.")
    assert vp.recompute_verified(vp.get_profile(clone.id)) is True
    vp.revoke_consent(clone.id)

    # per-surface bindings + migration (§6)
    vb.set_binding("channel:webui", design.id)
    vm.migrate_active_to_default_profile(name="From current")

    # both engines through the capability gate
    await route_synthesis({"provider": _StubTts("piper"), "voice": "en_US-amy"}, "hi")
    await route_synthesis(
        {"provider": _CloningTts("clone"), "voice": "x", "ref_audio": str(home / "consent.wav")},
        "hello",
    )

    vp.delete_profile(design.id)


@pytest.mark.asyncio
async def test_provider_family_is_byte_identical_before_and_after(home):
    before = _snapshot()
    assert before["type_handlers"], "registry has no handlers — snapshot is vacuous"

    await _exercise_the_whole_voice_matrix(home)

    after = _snapshot()
    assert after == before, (
        "the voice surface changed a provider-family collection — a voice profile is an "
        f"entity, not a new provider type/handler/hook action. before={before} after={after}"
    )


def test_voice_namespace_is_absent_from_every_provider_family():
    """The intent behind the invariant: no voice token is a provider type or hook action."""
    snap = _snapshot()
    voice_tokens = {"voice", "voice_profile", "voice_profiles", "voice-profile", "voice-migrate"}
    for collection, members in snap.items():
        leaked = voice_tokens & set(members)
        assert not leaked, f"{collection} leaked voice tokens {leaked}"
