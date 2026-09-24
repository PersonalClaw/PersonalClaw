"""Full-matrix as-a-user validation sweep (MULTIMODAL-IO MI-5 capstone).

MI-5's done-when: "full-matrix as-a-user validation passes across profile CRUD × lock ×
both engines × per-surface bindings × duplex behaviors × screen share on vision and
non-vision models." This walks that matrix through the SAME HTTP handlers the dashboard
calls (profile/binding/consent/lock/migrate routes), plus the resolver→capability-gate→
engine path for both engines and the pure duplex/screen-context decisions — all with
zero model spend and an isolated home.

Browser-level E2E of the profile-manager UI is a separate Playwright concern; this is the
API-boundary as-a-user drive that proves every backend behavior the UI depends on.
"""

from __future__ import annotations

import wave

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard import screen_context as sc
from personalclaw.dashboard.handlers import voice_profiles as vph
from personalclaw.tts.registry import (
    CloningUnsupportedError,
    active_voice_params,
    route_synthesis,
)
from personalclaw.voice import bindings as vb
from personalclaw.voice import duplex
from personalclaw.voice import profiles as vp


class _FakeState:
    def __init__(self):
        self.events: list[tuple[str, object]] = []

    def broadcast_ws(self, msg_type, data):
        self.events.append((msg_type, data))


class _FakeSel:
    def log_api_access(self, **kwargs):
        pass


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
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: _FakeSel())
    # flat selection for /resolve + migration; behavioral settings kept trivial.
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


def _app(state) -> web.Application:
    app = web.Application()
    app["state"] = state
    r = app.router
    r.add_get("/api/voice/profiles", vph.api_voice_profiles_list)
    r.add_post("/api/voice/profiles", vph.api_voice_profile_create)
    r.add_get("/api/voice/bindings", vph.api_voice_bindings_get)
    r.add_put("/api/voice/bindings", vph.api_voice_bindings_put)
    r.add_delete("/api/voice/bindings", vph.api_voice_bindings_delete)
    r.add_post("/api/voice/migrate", vph.api_voice_migrate)
    r.add_get("/api/voice/resolve", vph.api_voice_resolve)
    r.add_get("/api/voice/profiles/{id}", vph.api_voice_profile_get)
    r.add_put("/api/voice/profiles/{id}", vph.api_voice_profile_update)
    r.add_delete("/api/voice/profiles/{id}", vph.api_voice_profile_delete)
    r.add_get("/api/voice/profiles/{id}/audio", vph.api_voice_profile_audio)
    r.add_post("/api/voice/profiles/{id}/lock", vph.api_voice_profile_lock)
    r.add_post("/api/voice/profiles/{id}/unlock", vph.api_voice_profile_unlock)
    r.add_post("/api/voice/profiles/{id}/consent", vph.api_voice_profile_consent_record)
    r.add_post("/api/voice/profiles/{id}/consent/verify", vph.api_voice_profile_consent_verify)
    r.add_delete("/api/voice/profiles/{id}/consent", vph.api_voice_profile_consent_revoke)
    return app


# ── profile CRUD × per-surface bindings × migration, all over HTTP ───────────


@pytest.mark.asyncio
async def test_crud_bindings_and_migration_over_http(home):
    state = _FakeState()
    async with TestClient(TestServer(_app(state))) as client:
        # create a design + a clone profile
        design = await (
            await client.post("/api/voice/profiles", json={"name": "Design", "kind": "design"})
        ).json()
        clone = await (
            await client.post(
                "/api/voice/profiles",
                json={"name": "Clone", "kind": "clone", "provider": "voice-clone-tts"},
            )
        ).json()
        assert {design["kind"], clone["kind"]} == {"design", "clone"}

        # list shows both; update patches a mutable field
        listed = await (await client.get("/api/voice/profiles")).json()
        assert len(listed["profiles"]) == 2
        assert (
            await client.put(f"/api/voice/profiles/{design['id']}", json={"speed": 1.3})
        ).status == 200

        # per-surface bindings across all three namespaces + default
        for surface, pid in (
            ("default", design["id"]),
            ("channel:webui", clone["id"]),
            ("agent:research", design["id"]),
        ):
            assert (
                await client.put(
                    "/api/voice/bindings", json={"surface": surface, "profile_id": pid}
                )
            ).status == 200

        # the resolver reports which level wins per surface
        webui = await (await client.get("/api/voice/resolve?surface=channel:webui")).json()
        assert webui["level"] == vb.LEVEL_BINDING and webui["profile_id"] == clone["id"]
        other = await (await client.get("/api/voice/resolve?surface=channel:slack")).json()
        assert other["level"] == vb.LEVEL_DEFAULT and other["profile_id"] == design["id"]

        # §6 one-click migration → a fresh design profile becomes default
        migrated = await (await client.post("/api/voice/migrate", json={"name": "Current"})).json()
        assert migrated["kind"] == "design"
        binds = (await (await client.get("/api/voice/bindings")).json())["bindings"]
        assert binds["default"] == migrated["id"]

        # deleting a bound profile removes it AND forgets its bindings (no dangling ref)
        assert (await client.delete(f"/api/voice/profiles/{clone['id']}")).status == 200
        binds2 = (await (await client.get("/api/voice/bindings")).json())["bindings"]
        assert clone["id"] not in binds2.values()


# ── lock-from-history × consent gate, over HTTP ──────────────────────────────


@pytest.mark.asyncio
async def test_lock_and_consent_gate_over_http(home):
    async with TestClient(TestServer(_app(_FakeState()))) as client:
        clone = await (
            await client.post(
                "/api/voice/profiles",
                json={"name": "C", "kind": "clone", "provider": "voice-clone-tts"},
            )
        ).json()
        pid = clone["id"]

        # a prior synthesis left a generation in history; lock pins it
        vp.append_history(pid, _wav(home / "gen.wav"), seed=9)
        locked = await (
            await client.post(f"/api/voice/profiles/{pid}/lock", json={"history_index": 0})
        ).json()
        assert locked["locked"] is True and locked["seed"] == 9
        assert (await client.post(f"/api/voice/profiles/{pid}/unlock")).status == 200

        # consent: seed the recording (as a completed upload would), record text, verify
        vp.attach_consent_audio(pid, _wav(home / "consent.wav"))
        vp.attach_ref_audio(pid, _wav(home / "ref.wav"))
        assert (
            await client.post(
                f"/api/voice/profiles/{pid}/consent", json={"consent_text": "I consent."}
            )
        ).status == 200
        verify = await (await client.post(f"/api/voice/profiles/{pid}/consent/verify")).json()
        assert verify["verified_own_voice"] is True

        # verified → the reference clip is releasable; revoke → it is refused (the gate's teeth)
        assert (
            await client.get(f"/api/voice/profiles/{pid}/audio?artifact=ref_audio")
        ).status == 200
        assert (await client.delete(f"/api/voice/profiles/{pid}/consent")).status == 200
        blocked = await client.get(f"/api/voice/profiles/{pid}/audio?artifact=ref_audio")
        assert blocked.status == 403
        assert (await blocked.json())["error"]["code"] == "consent_required"


# ── both engines: resolver → capability gate → engine ────────────────────────


@pytest.mark.asyncio
async def test_both_engines_through_the_resolver(home, monkeypatch):
    piper, clone_engine = _StubTts("piper"), _CloningTts("voice-clone-tts")
    monkeypatch.setattr(
        "personalclaw.tts.registry._providers", {"piper": piper, "voice-clone-tts": clone_engine}
    )
    monkeypatch.setattr("personalclaw.tts.registry._ensure_registered", lambda: None)

    # a design profile bound default resolves to piper and synthesizes fine (no ref clip)
    design = vp.create_profile(name="D", kind="design", provider="piper")
    vb.set_binding("default", design.id)
    params = active_voice_params(surface="channel:webui")
    assert params is not None  # a bound profile always resolves
    assert await route_synthesis(params, "hello") is not None
    assert piper.calls and piper.calls[-1]["text"] == "hello"

    # a clone profile (reference clip) bound to piper is REFUSED with a typed 409
    clone = vp.create_profile(name="C", kind="clone", provider="piper")
    vp.attach_ref_audio(clone.id, _wav(home / "ref.wav"))
    vb.set_binding("channel:slack", clone.id)
    clone_params = active_voice_params(surface="channel:slack")
    assert clone_params is not None
    assert clone_params["ref_audio"]  # the clip resolved
    with pytest.raises(CloningUnsupportedError) as exc:
        await route_synthesis(clone_params, "nope")
    assert exc.value.status == 409

    # the SAME clone profile bound to the cloning engine synthesizes through the reference
    vp.update_profile(clone.id, provider="voice-clone-tts")
    ok_params = active_voice_params(surface="channel:slack")
    assert ok_params is not None
    assert await route_synthesis(ok_params, "cloned") is not None
    assert clone_engine.calls[-1]["ref_audio"] == ok_params["ref_audio"]


# ── duplex behaviors (pure, as-a-user speech turns) ──────────────────────────


def test_duplex_behaviors():
    assert duplex.is_confirmation("okay do it, go ahead") is True
    assert duplex.is_confirmation("go ahead and think about the whole plan first") is False
    assert duplex.is_exit("never mind, cancel that") is True
    # the assistant's own 3+ word run coming back is filtered as echo
    assert (
        duplex.is_echo("the quick brown fox jumps", "well, the quick brown fox jumps high") is True
    )
    assert duplex.is_echo("yes", "the quick brown fox") is False
    # pre-TTS cleaning strips what should not be spoken aloud (URLs/paths → words)
    assert "http" not in duplex.clean_for_speech("see https://example.com/docs now").lower()


# ── screen share on vision vs non-vision models ──────────────────────────────


def test_screen_share_vision_and_non_vision(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.llm.catalog.infer_capabilities",
        lambda label: ["image_modality"] if "vision" in label else [],
    )
    # vision model → the frame is delivered NATIVELY as an image part
    assert sc.model_reads_images("ollama-vision:7b") is True
    assert sc.resolve_delivery("ollama-vision:7b") == (sc.DELIVERY_NATIVE, "")
    # non-vision model → never native (described if a vision use-case resolves, else none)
    assert sc.model_reads_images("plain-text-model") is False
    mode, reason = sc.resolve_delivery("plain-text-model")
    assert mode in (sc.DELIVERY_DESCRIBED, sc.DELIVERY_NONE)
    # 'auto' is conservatively non-vision — the toggle can only under-promise
    assert sc.resolve_delivery("auto")[0] != sc.DELIVERY_NATIVE
