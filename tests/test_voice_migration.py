"""One-click 'profile from my current voice' migration (MULTIMODAL-IO §6, MI-5).

The migration promotes the flat ``active_models.json`` TTS selection into a design-kind
profile and binds it ``default``. The rails proven here:

* it captures the FLAT selection (``active_tts`` + ``tts`` settings), so the persona and
  speed the user hears today survive into the profile;
* it is refused with a typed 409 when there is no active voice, rather than creating an
  empty profile that renders nothing;
* it happens ONLY through the explicit ``POST /api/voice/migrate`` — the module exposes
  no startup/first-run caller — and the mutation is SEL-audited.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import voice_profiles as vph
from personalclaw.voice import bindings as vb
from personalclaw.voice import migration as vm
from personalclaw.voice import profiles as vp


class _FakeState:
    def __init__(self):
        self.events: list[tuple[str, object]] = []

    def broadcast_ws(self, msg_type: str, data: object) -> None:
        self.events.append((msg_type, data))


class _FakeSel:
    def __init__(self):
        self.calls: list[dict] = []

    def log_api_access(self, **kwargs):
        self.calls.append(kwargs)


class _FakeProvider:
    def __init__(self, name="piper"):
        self.name = name


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr("personalclaw.voice.profiles.config_dir", lambda: root)
    monkeypatch.setattr("personalclaw.voice.bindings.config_dir", lambda: root)
    return root


@pytest.fixture
def sel_recorder(monkeypatch):
    recorder = _FakeSel()
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: recorder)
    return recorder


def _set_active(monkeypatch, provider="piper", voice="en_US-amy", *, speed=1.0, speech_voice=""):
    """Point the flat TTS resolution at a fixed selection, no models.json/registry."""
    monkeypatch.setattr(
        "personalclaw.tts.registry.active_tts", lambda: (_FakeProvider(provider), voice)
    )
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_use_case_settings",
        lambda use_case: {"speed": speed, "speech_voice": speech_voice},
    )


def _clear_active(monkeypatch):
    monkeypatch.setattr("personalclaw.tts.registry.active_tts", lambda: None)


def _app(home, state) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/voice/migrate", vph.api_voice_migrate)
    app.router.add_get("/api/voice/profiles", vph.api_voice_profiles_list)
    app.router.add_get("/api/voice/bindings", vph.api_voice_bindings_get)
    return app


# ── field capture ───────────────────────────────────────────────────────────


def test_active_voice_fields_captures_the_flat_selection(home, monkeypatch):
    _set_active(monkeypatch, provider="piper", voice="en_US-amy", speed=1.25, speech_voice="nova")
    fields = vm.active_voice_fields()
    assert fields == {
        "kind": "design",
        "provider": "piper",
        "model": "en_US-amy",
        "speed": 1.25,
        "design_params": {"speech_voice": "nova"},
    }


def test_active_voice_fields_omits_persona_when_absent(home, monkeypatch):
    _set_active(monkeypatch, speech_voice="")
    fields = vm.active_voice_fields()
    assert "design_params" not in fields  # piper ignores persona; nothing to carry


def test_active_voice_fields_is_none_without_a_selection(home, monkeypatch):
    _clear_active(monkeypatch)
    assert vm.active_voice_fields() is None


# ── orchestration ─────────────────────────────────────────────────────────


def test_migrate_creates_a_design_profile_and_binds_default(home, monkeypatch):
    _set_active(monkeypatch, provider="piper", voice="en_US-amy", speed=1.1)
    profile = vm.migrate_active_to_default_profile(name="Keyur")

    assert profile.kind == "design"
    assert profile.name == "Keyur"
    assert (profile.provider, profile.model, profile.speed) == ("piper", "en_US-amy", 1.1)
    # bound as the default surface, and the record actually persisted
    assert vb.load_bindings().get(vb.DEFAULT_KEY) == profile.id
    assert vp.get_profile(profile.id) is not None
    # a design migration never fabricates consent-bearing provenance
    assert profile.ref_audio == "" and profile.verified_own_voice is False


def test_migrate_defaults_the_name_when_blank(home, monkeypatch):
    _set_active(monkeypatch)
    profile = vm.migrate_active_to_default_profile(name="   ")
    assert profile.name == vm.DEFAULT_MIGRATED_NAME


def test_migrate_without_active_voice_raises_409(home, monkeypatch):
    _clear_active(monkeypatch)
    with pytest.raises(vp.VoiceProfileError) as exc:
        vm.migrate_active_to_default_profile()
    assert exc.value.status == 409
    assert exc.value.reason == "no_active_voice"
    assert vp.list_profiles() == []  # nothing half-created


def test_module_exposes_no_automatic_caller():
    """§6 'never automatic' — the only public entry points are the explicit two."""
    public = {n for n in dir(vm) if not n.startswith("_")}
    # imported names (vb/vp) are allowed; the migration's OWN callables are exactly these.
    own = {n for n in public if getattr(getattr(vm, n), "__module__", "") == vm.__name__}
    assert own == {"active_voice_fields", "migrate_active_to_default_profile"}


# ── HTTP surface ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_migrate_is_201_and_sets_default(home, sel_recorder, monkeypatch):
    _set_active(monkeypatch, provider="piper", voice="en_US-amy")
    state = _FakeState()
    async with TestClient(TestServer(_app(home, state))) as client:
        r = await client.post("/api/voice/migrate", json={"name": "Mine"})
        assert r.status == 201
        body = await r.json()
        assert body["kind"] == "design" and body["name"] == "Mine"
        pid = body["id"]
        bindings = (await (await client.get("/api/voice/bindings")).json())["bindings"]
        assert bindings.get("default") == pid

    assert state.events[0][0] == "voice_profile_created"
    assert state.events[0][1].get("migrated") is True
    audited = [c for c in sel_recorder.calls if c.get("operation") == "voice_profile.migrate"]
    assert audited and audited[0]["outcome"] == "success"


@pytest.mark.asyncio
async def test_post_migrate_tolerates_an_empty_body(home, sel_recorder, monkeypatch):
    _set_active(monkeypatch)
    async with TestClient(TestServer(_app(home, _FakeState()))) as client:
        r = await client.post("/api/voice/migrate")  # no JSON body at all
        assert r.status == 201
        assert (await r.json())["name"] == vm.DEFAULT_MIGRATED_NAME


@pytest.mark.asyncio
async def test_post_migrate_is_409_without_active_voice(home, sel_recorder, monkeypatch):
    _clear_active(monkeypatch)
    async with TestClient(TestServer(_app(home, _FakeState()))) as client:
        r = await client.post("/api/voice/migrate", json={})
        assert r.status == 409
        assert (await r.json())["error"]["code"] == "no_active_voice"
    denied = [c for c in sel_recorder.calls if c.get("operation") == "voice_profile.migrate"]
    assert denied and denied[0]["outcome"] == "denied"
