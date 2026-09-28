"""The LMM-V2 through-clone selftest + the SDK sidecar facade.

The behaviours the change pins:

1. the doctor selftest gains a real-synthesis TTS probe, and when the resolved provider
   supports cloning the probe conditions on a reference clip that EXISTS on disk — the
   clone path (reference validation → engine) is what runs, not the plain-voice path;
2. a sidecar death during the probe surfaces its TYPED reason (``sidecar_crashed:<why>``)
   in the capability detail, never a generic failure;
3. no bound voice means no TTS row — an untestable capability is absent, not failed;
4. ``personalclaw.sdk.sidecar`` re-exports the LMMV §3 machinery unchanged, so a
   provider app can drive a runner without importing core internals.
"""

from __future__ import annotations

import os
import tempfile
import wave

import pytest

from personalclaw.dashboard.handlers.doctor import _tts_clone_probe, _write_reference_clip

pytestmark = pytest.mark.asyncio


async def _timed(coro, timeout: float = 15.0):
    return await coro


@pytest.fixture(autouse=True)
def system_temp(tmp_path, monkeypatch):
    """The system temp folder, as the probe and the provider see it: this test's own."""
    folder = tmp_path / "system-temp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


#: The provider the bound voice is: the one whose selftest runs the TTS probe.
VOICE = "omni-voice"


class _FakeProvider:
    name = VOICE
    supports_cloning = True

    def __init__(self) -> None:
        self.seen: dict = {}

    async def synthesize(self, text, *, ref_audio="", ref_text="", output_path="", **opts):
        """As the real providers behave: it writes where it is told, or makes a temporary file
        of its own when told nowhere, and hands the path back for the caller to remove."""
        self.seen = {"text": text, "ref_audio": ref_audio, "ref_text": ref_text, **opts}
        if not output_path:
            fd, output_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
        with open(output_path, "wb") as fh:
            fh.write(b"RIFF")
        return output_path


def _params(provider) -> dict:
    return {
        "provider": provider,
        "voice": "omnivoice-zeroshot",
        "speed": 1.0,
        "speech_voice": "",
        "enabled": True,
        "auto_speak": False,
    }


def test_the_reference_clip_is_a_real_decodable_wav(tmp_path):
    path = str(tmp_path / "ref.wav")
    _write_reference_clip(path)
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        assert w.getnframes() == 8000


async def test_a_cloning_provider_is_probed_through_a_real_clip(monkeypatch):
    provider = _FakeProvider()
    seen_exists: dict = {}

    async def fake_route(params, text, *, output_path=""):
        # Capture whether the injected clip exists AT SYNTHESIS TIME — the whole point
        # of generating it is that the provider's path validation passes.
        seen_exists["ref"] = params.get("ref_audio", "")
        seen_exists["existed"] = os.path.isfile(params.get("ref_audio", ""))
        return await params["provider"].synthesize(
            text,
            ref_audio=params.get("ref_audio", ""),
            ref_text=params.get("ref_text", ""),
            output_path=output_path,
        )

    import personalclaw.tts.registry as reg

    monkeypatch.setattr(reg, "active_voice_params", lambda **kw: _params(provider))
    monkeypatch.setattr(reg, "route_synthesis", fake_route)

    result = await _tts_clone_probe(_timed, VOICE)
    assert result == {"ok": True, "detail": "clone synthesis returned audio", "cloning": True}
    assert seen_exists["existed"] is True
    # The generated clip is cleaned up after the probe.
    assert not os.path.exists(seen_exists["ref"])


async def test_a_locked_profile_clip_is_never_overridden(monkeypatch):
    provider = _FakeProvider()

    async def fake_route(params, text, *, output_path=""):
        return await params["provider"].synthesize(
            text, ref_audio=params.get("ref_audio", ""), output_path=output_path
        )

    import personalclaw.tts.registry as reg

    monkeypatch.setattr(
        reg,
        "active_voice_params",
        lambda **kw: {**_params(provider), "ref_audio": "/locked/clip.wav"},
    )
    monkeypatch.setattr(reg, "route_synthesis", fake_route)

    await _tts_clone_probe(_timed, VOICE)
    assert provider.seen["ref_audio"] == "/locked/clip.wav"


async def test_a_sidecar_death_surfaces_its_typed_reason(monkeypatch):
    provider = _FakeProvider()

    class _Crash(RuntimeError):
        typed_reason = "sidecar_crashed:signal_11"

    async def fake_route(params, text, *, output_path=""):
        raise _Crash("child died")

    import personalclaw.tts.registry as reg

    monkeypatch.setattr(reg, "active_voice_params", lambda **kw: _params(provider))
    monkeypatch.setattr(reg, "route_synthesis", fake_route)

    result = await _tts_clone_probe(_timed, VOICE)
    assert result is not None
    assert result["ok"] is False
    assert result["detail"] == "sidecar_crashed:signal_11"


async def test_no_bound_voice_means_no_tts_row(monkeypatch):
    import personalclaw.tts.registry as reg

    monkeypatch.setattr(reg, "active_voice_params", lambda **kw: None)
    assert await _tts_clone_probe(_timed, VOICE) is None


async def test_a_non_cloning_provider_is_probed_without_a_clip(monkeypatch):
    provider = _FakeProvider()
    provider.supports_cloning = False

    async def fake_route(params, text, *, output_path=""):
        assert not params.get("ref_audio")  # plain-voice path: nothing injected
        return await params["provider"].synthesize(text, output_path=output_path)

    import personalclaw.tts.registry as reg

    monkeypatch.setattr(reg, "active_voice_params", lambda **kw: _params(provider))
    monkeypatch.setattr(reg, "route_synthesis", fake_route)

    result = await _tts_clone_probe(_timed, VOICE)
    assert result == {"ok": True, "detail": "synthesis returned audio", "cloning": False}


# On `main` the probe asked for its clip with no path (`output_path=""`), so the provider made a
# temporary file, and the probe only looked at whether a path came back: every selftest with a
# voice bound left that clip in the system temp folder. It now hands the provider a path of its
# own, as the Models page's selftest does, and removes it and any other path the provider returns.


@pytest.mark.parametrize("cloning", [False, True], ids=["plain", "clone"])
async def test_the_probe_leaves_no_clip_behind(monkeypatch, system_temp, cloning):
    provider = _FakeProvider()
    provider.supports_cloning = cloning

    async def fake_route(params, text, *, output_path=""):
        return await params["provider"].synthesize(
            text, ref_audio=params.get("ref_audio", ""), output_path=output_path
        )

    import personalclaw.tts.registry as reg

    monkeypatch.setattr(reg, "active_voice_params", lambda **kw: _params(provider))
    monkeypatch.setattr(reg, "route_synthesis", fake_route)

    result = await _tts_clone_probe(_timed, VOICE)
    assert result is not None and result["ok"] is True, result
    assert list(system_temp.iterdir()) == []


async def test_a_clip_the_provider_put_somewhere_else_is_removed_too(monkeypatch, system_temp):
    provider = _FakeProvider()
    provider.supports_cloning = False

    async def fake_route(params, text, *, output_path=""):
        return await params["provider"].synthesize(text)  # its own file, not the probe's path

    import personalclaw.tts.registry as reg

    monkeypatch.setattr(reg, "active_voice_params", lambda **kw: _params(provider))
    monkeypatch.setattr(reg, "route_synthesis", fake_route)

    result = await _tts_clone_probe(_timed, VOICE)
    assert result is not None and result["ok"] is True, result
    assert list(system_temp.iterdir()) == []


def test_the_sdk_facade_is_the_lmmv_machinery_unchanged():
    from personalclaw.local_models import sidecar as core
    from personalclaw.sdk.sidecar import (
        SidecarCrashed,
        SidecarRunner,
        SidecarWorkerError,
        get_runner,
        register_runner,
        sidecar_venv_dir,
        unregister_runner,
    )

    assert SidecarRunner is core.SidecarRunner
    assert SidecarCrashed is core.SidecarCrashed
    assert SidecarWorkerError is core.SidecarWorkerError
    assert get_runner is core.get_runner
    assert register_runner is core.register_runner
    assert sidecar_venv_dir is core.sidecar_venv_dir
    assert unregister_runner is core.unregister_runner
