"""voice_profiles entity store, resolver, consent provenance + upload target (MI-1).

The four rails this suite exists to hold, each proven by an outcome rather than a
flag read:

* **``verified_own_voice`` is recomputed, never believed** — a hand-edited
  ``"verified_own_voice": true`` in the persisted JSON must still read unverified,
  and the gated surface must still refuse.
* **Ids are symlink-contained** — a traversal id and a planted symlink escaping the
  profiles root are both refused, not followed.
* **Revoking consent blocks use** — the gated artifact read returns 200 while consent
  verifies and 403 ``consent_required`` after revocation.
* **Resumable upload is byte-exact** — a partial-then-resumed upload lands the same
  bytes as a single upload, and an abandoned partial is never served as complete.

Plus the bounded-history bound (asserted with oversized input, not trusted from a
comment), the four-level precedence chain, and the zero-profile regression: with an
empty store the resolver returns exactly the pre-profile dict.
"""

from __future__ import annotations

import json
import os
import wave

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import uploads as up
from personalclaw.dashboard.handlers import voice_profiles as vph
from personalclaw.uploads.store import UploadStore
from personalclaw.voice import bindings as vb
from personalclaw.voice import profiles as vp

# ── fixtures ────────────────────────────────────────────────────────────────


class _FakeState:
    """Collects broadcast_ws calls so the typed-event contract is assertable."""

    def __init__(self):
        self.events: list[tuple[str, object]] = []

    def broadcast_ws(self, msg_type: str, data: object) -> None:
        self.events.append((msg_type, data))


class _FakeSel:
    def __init__(self):
        self.calls: list[dict] = []

    def log_api_access(self, **kwargs):
        self.calls.append(kwargs)

    def log_tool_invocation(self, **kwargs):  # pragma: no cover - unused here
        self.calls.append(kwargs)


class _FakeProvider:
    name = "piper"

    def synthesize(self, *a, **k):  # pragma: no cover - never called here
        return b""


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home for the profile + binding stores (never the real one)."""
    root = tmp_path / "home"
    root.mkdir()
    # Both modules bind ``config_dir`` at import time, so patch the bound names —
    # patching config.loader alone would leave these pointing at the real home.
    monkeypatch.setattr("personalclaw.voice.profiles.config_dir", lambda: root)
    monkeypatch.setattr("personalclaw.voice.bindings.config_dir", lambda: root)
    return root


@pytest.fixture
def sel_recorder(monkeypatch):
    recorder = _FakeSel()
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: recorder)
    return recorder


def _wav(path, seconds=1.2, rate=16000):
    """A real WAV of ``seconds`` silence — long enough to satisfy the consent floor."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def _app(home, tmp_path, monkeypatch, state=None) -> web.Application:
    app = web.Application(client_max_size=8 * 1024 * 1024)
    app["state"] = state if state is not None else _FakeState()
    app["upload_store"] = UploadStore(tmp_path / ".parts")
    app.router.add_get("/api/voice/profiles", vph.api_voice_profiles_list)
    app.router.add_post("/api/voice/profiles", vph.api_voice_profile_create)
    app.router.add_get("/api/voice/bindings", vph.api_voice_bindings_get)
    app.router.add_put("/api/voice/bindings", vph.api_voice_bindings_put)
    app.router.add_delete("/api/voice/bindings", vph.api_voice_bindings_delete)
    app.router.add_get("/api/voice/resolve", vph.api_voice_resolve)
    app.router.add_get("/api/voice/profiles/{id}", vph.api_voice_profile_get)
    app.router.add_put("/api/voice/profiles/{id}", vph.api_voice_profile_update)
    app.router.add_delete("/api/voice/profiles/{id}", vph.api_voice_profile_delete)
    app.router.add_get("/api/voice/profiles/{id}/audio", vph.api_voice_profile_audio)
    app.router.add_post("/api/voice/profiles/{id}/lock", vph.api_voice_profile_lock)
    app.router.add_post("/api/voice/profiles/{id}/unlock", vph.api_voice_profile_unlock)
    app.router.add_post("/api/voice/profiles/{id}/consent", vph.api_voice_profile_consent_record)
    app.router.add_post(
        "/api/voice/profiles/{id}/consent/verify", vph.api_voice_profile_consent_verify
    )
    app.router.add_delete("/api/voice/profiles/{id}/consent", vph.api_voice_profile_consent_revoke)
    app.router.add_post("/api/uploads/init", up.api_uploads_init)
    app.router.add_put("/api/uploads/{id}/part", up.api_uploads_part)
    app.router.add_get("/api/uploads/{id}", up.api_uploads_status)
    app.router.add_post("/api/uploads/{id}/complete", up.api_uploads_complete)
    return app


# ── CRUD + typed WS events ──────────────────────────────────────────────────


class TestCrud:
    def test_create_read_update_delete(self, home):
        profile = vp.create_profile(name="My Voice", kind="clone", provider="piper-tts")
        assert profile.id.startswith("vp-")
        assert vp.get_profile(profile.id).name == "My Voice"
        vp.update_profile(profile.id, name="Renamed", seed=42, speed=1.25)
        again = vp.get_profile(profile.id)
        assert (again.name, again.seed, again.speed) == ("Renamed", 42, 1.25)
        assert [p.id for p in vp.list_profiles()] == [profile.id]
        assert vp.delete_profile(profile.id) is True
        assert vp.get_profile(profile.id) is None
        assert vp.list_profiles() == []

    def test_kind_is_validated_and_immutable(self, home):
        with pytest.raises(vp.VoiceProfileError):
            vp.create_profile(name="x", kind="chimera")
        profile = vp.create_profile(name="x", kind="design")
        with pytest.raises(vp.VoiceProfileError) as exc:
            vp.update_profile(profile.id, kind="clone")
        assert exc.value.reason == "kind_immutable"

    @pytest.mark.asyncio
    async def test_routes_emit_typed_ws_events(self, home, tmp_path, monkeypatch):
        state = _FakeState()
        async with TestClient(TestServer(_app(home, tmp_path, monkeypatch, state))) as client:
            r = await client.post("/api/voice/profiles", json={"name": "Mine", "kind": "design"})
            assert r.status == 201
            pid = (await r.json())["id"]
            assert (await (await client.get("/api/voice/profiles")).json())["profiles"][0][
                "id"
            ] == pid
            assert (await client.put(f"/api/voice/profiles/{pid}", json={"name": "Yours"})).status
            assert (await client.delete(f"/api/voice/profiles/{pid}")).status == 200
        kinds = [name for name, _ in state.events]
        assert kinds == ["voice_profile_created", "voice_profile_updated", "voice_profile_deleted"]

    @pytest.mark.asyncio
    async def test_unknown_profile_is_404_not_500(self, home, tmp_path, monkeypatch):
        async with TestClient(TestServer(_app(home, tmp_path, monkeypatch))) as client:
            r = await client.get("/api/voice/profiles/vp-deadbeef")
            assert r.status == 404
            assert (await r.json())["reason"] == "not_found"


# ── rail 1: verified_own_voice is recomputed, never believed ────────────────


class TestVerifiedOwnVoiceIsRecomputed:
    def test_hand_edited_flag_does_not_verify(self, home):
        profile = vp.create_profile(name="Mine", kind="clone")
        path = vp.profile_path(profile.id)
        raw = json.loads(path.read_text())
        # The forgery: flip the flag (and invent the provenance strings) by hand.
        raw["verified_own_voice"] = True
        raw["consent_text"] = "I consent"
        raw["consent_audio"] = "consent.wav"
        path.write_text(json.dumps(raw))

        # No artifact on disk → the recompute overrules the file.
        assert vp.get_profile(profile.id).verified_own_voice is False
        assert vp.recompute_verified(vp.get_profile(profile.id)) is False
        assert vp.profile_payload(vp.get_profile(profile.id))["verified_own_voice"] is False

    def test_too_short_recording_does_not_verify(self, home):
        profile = vp.create_profile(name="Mine", kind="clone")
        _wav(vp.artifact_path(profile.id, "consent.wav"), seconds=0.2)
        vp.record_consent(profile.id, consent_text="I consent")
        # The audio exists but is under the one-second floor.
        assert vp.get_profile(profile.id).verified_own_voice is False

    def test_real_recording_plus_text_verifies(self, home):
        profile = vp.create_profile(name="Mine", kind="clone")
        _wav(vp.artifact_path(profile.id, "consent.wav"))
        vp.record_consent(profile.id, consent_text="I consent to cloning my voice")
        assert vp.get_profile(profile.id).verified_own_voice is True
        # Text alone is not enough either: drop the artifact and it un-verifies.
        vp.artifact_path(profile.id, "consent.wav").unlink()
        assert vp.get_profile(profile.id).verified_own_voice is False

    @pytest.mark.asyncio
    async def test_verify_endpoint_recomputes_and_audits(
        self, home, tmp_path, monkeypatch, sel_recorder
    ):
        profile = vp.create_profile(name="Mine", kind="clone")
        path = vp.profile_path(profile.id)
        raw = json.loads(path.read_text())
        raw["verified_own_voice"] = True
        path.write_text(json.dumps(raw))
        async with TestClient(TestServer(_app(home, tmp_path, monkeypatch))) as client:
            r = await client.post(f"/api/voice/profiles/{profile.id}/consent/verify")
            assert r.status == 200
            assert (await r.json())["verified_own_voice"] is False
        ops = [c["operation"] for c in sel_recorder.calls]
        assert ops == ["voice_profile.consent.verify"]
        assert sel_recorder.calls[0]["outcome"] == "denied"


# ── rail 2: symlink-contained ids ──────────────────────────────────────────


class TestContainment:
    @pytest.mark.parametrize(
        "bad",
        [
            "../evil",
            "../../etc/passwd",
            "/etc/passwd",
            "vp-a/../../b",
            "sub/dir",
            "..",
            "",
            "vp-" + "x" * 200,
            "vp\x00null",
        ],
    )
    def test_traversal_ids_are_refused(self, home, bad):
        with pytest.raises(vp.VoiceProfileError) as exc:
            vp.profile_path(bad)
        assert exc.value.reason == "invalid_profile_id"
        with pytest.raises(vp.VoiceProfileError):
            vp.get_profile(bad)

    def test_planted_record_symlink_is_not_followed(self, home, tmp_path):
        outside = tmp_path / "outside.json"
        outside.write_text(json.dumps({"id": "vp-outside", "name": "escaped"}))
        root = vp.profiles_root()
        root.mkdir(parents=True, exist_ok=True)
        os.symlink(outside, root / "vp-escape.json")

        with pytest.raises(vp.VoiceProfileError) as exc:
            vp.get_profile("vp-escape")
        assert exc.value.reason == "path_escape"
        # …and the escaping record never shows up in a listing either.
        assert [p.id for p in vp.list_profiles()] == []

    def test_planted_dir_symlink_is_not_written_through(self, home, tmp_path):
        outside = tmp_path / "outside-dir"
        outside.mkdir()
        root = vp.profiles_root()
        root.mkdir(parents=True, exist_ok=True)
        os.symlink(outside, root / "vp-escape")

        with pytest.raises(vp.VoiceProfileError) as exc:
            vp.artifact_path("vp-escape", "ref_audio.wav")
        assert exc.value.reason == "path_escape"
        assert list(outside.iterdir()) == []

    def test_artifact_relative_path_cannot_escape(self, home):
        profile = vp.create_profile(name="x", kind="clone")
        for bad in ("../../etc/passwd", "/etc/passwd", "sub/../../out.wav", ""):
            with pytest.raises(vp.VoiceProfileError):
                vp.artifact_path(profile.id, bad)

    def test_delete_does_not_recurse_through_a_symlink(self, home, tmp_path):
        keep = tmp_path / "keep"
        keep.mkdir()
        (keep / "precious.txt").write_text("do not delete me")
        profile = vp.create_profile(name="x", kind="design")
        pdir = vp.profile_dir(profile.id)
        # Swap the profile's artifact dir for a symlink pointing outside.
        pdir.rmdir()
        os.symlink(keep, pdir)
        vp.delete_profile(profile.id)
        assert (keep / "precious.txt").is_file()


# ── rail 3: revoked consent blocks use ─────────────────────────────────────


class TestConsentRevocationBlocks:
    @pytest.mark.asyncio
    async def test_revoke_blocks_the_gated_artifact_read(
        self, home, tmp_path, monkeypatch, sel_recorder
    ):
        profile = vp.create_profile(name="Mine", kind="clone")
        _wav(vp.artifact_path(profile.id, "ref_audio.wav"))
        vp.update_profile(profile.id)
        stored = vp.get_profile(profile.id)
        stored.ref_audio = "ref_audio.wav"
        vp._write(stored)
        _wav(vp.artifact_path(profile.id, "consent.wav"))

        async with TestClient(TestServer(_app(home, tmp_path, monkeypatch))) as client:
            # Unverified clone: the bytes do not leave the store.
            r = await client.get(f"/api/voice/profiles/{profile.id}/audio")
            assert r.status == 403
            assert (await r.json())["reason"] == "consent_required"

            r = await client.post(
                f"/api/voice/profiles/{profile.id}/consent",
                json={"consent_text": "I consent to cloning my voice"},
            )
            assert (await r.json())["verified_own_voice"] is True
            assert (await client.get(f"/api/voice/profiles/{profile.id}/audio")).status == 200

            # Revoke → blocked again, and the recording is gone from disk.
            r = await client.delete(f"/api/voice/profiles/{profile.id}/consent")
            assert (await r.json())["verified_own_voice"] is False
            r = await client.get(f"/api/voice/profiles/{profile.id}/audio")
            assert r.status == 403
            assert (await r.json())["reason"] == "consent_required"

        assert not vp.artifact_path(profile.id, "consent.wav").exists()
        ops = [c["operation"] for c in sel_recorder.calls]
        assert ops == ["voice_profile.consent.record", "voice_profile.consent.revoke"]

    def test_consent_recording_is_never_served(self, home):
        profile = vp.create_profile(name="Mine", kind="clone")
        _wav(vp.artifact_path(profile.id, "consent.wav"))
        vp.record_consent(profile.id, consent_text="I consent")
        with pytest.raises(vp.VoiceProfileError) as exc:
            vp.assert_artifact_release_allowed(vp.get_profile(profile.id), "consent")
        assert exc.value.reason == "artifact_not_readable"

    def test_sel_payload_carries_no_consent_text_or_audio(self, home, sel_recorder, monkeypatch):
        # The audit trail is ids + verdicts; the consent statement is not logged.
        secret = "my spoken consent statement"
        profile = vp.create_profile(name="Mine", kind="clone")
        _wav(vp.artifact_path(profile.id, "consent.wav"))
        vp.record_consent(profile.id, consent_text=secret)
        import asyncio

        from aiohttp.test_utils import make_mocked_request

        request = make_mocked_request("POST", f"/api/voice/profiles/{profile.id}/consent/verify")
        request.match_info["id"] = profile.id
        asyncio.run(vph.api_voice_profile_consent_verify(request))
        blob = json.dumps(sel_recorder.calls)
        assert secret not in blob
        assert ".wav" not in blob
        assert profile.id in blob

    def test_unverified_clone_binding_warns(self, home):
        profile = vp.create_profile(name="Mine", kind="clone")
        assert vb.binding_warning(profile, "channel:slack") == "unverified_clone_consent"
        # Local synthesis is not an ethics checkpoint: no surface, no warning.
        assert vb.binding_warning(profile, "") == ""
        design = vp.create_profile(name="Designed", kind="design")
        assert vb.binding_warning(design, "channel:slack") == ""


# ── rail 4: resumable ref-audio upload ─────────────────────────────────────


class TestResumableRefAudioUpload:
    @pytest.mark.asyncio
    async def test_partial_then_resume_matches_a_single_upload(
        self, home, tmp_path, monkeypatch, sel_recorder
    ):
        monkeypatch.setattr("personalclaw.uploads.store.PART_SIZE", 4096)
        payload = os.urandom(4096 * 5 + 17)
        one = vp.create_profile(name="One", kind="clone")
        two = vp.create_profile(name="Two", kind="clone")

        async def _init(client, pid):
            r = await client.post(
                "/api/uploads/init",
                json={
                    "filename": "ref.wav",
                    "size": len(payload),
                    "mime": "audio/wav",
                    "target": "voice_profile",
                    "profile_id": pid,
                    "kind": "ref_audio",
                },
            )
            assert r.status == 200, await r.text()
            return await r.json()

        async def _part(client, uid, index, part_size):
            chunk = payload[index * part_size : (index + 1) * part_size]
            r = await client.put(
                f"/api/uploads/{uid}/part?index={index}",
                data=chunk,
                headers={"Content-Type": "application/octet-stream"},
            )
            assert r.status == 200

        async with TestClient(TestServer(_app(home, tmp_path, monkeypatch))) as client:
            # A: single pass, every part in order.
            info = await _init(client, one.id)
            for i in range(info["totalParts"]):
                await _part(client, info["uploadId"], i, info["partSize"])
            assert (await client.post(f"/api/uploads/{info['uploadId']}/complete")).status == 200

            # B: stall after two parts, "resume" (re-send one part, then the rest).
            info = await _init(client, two.id)
            uid, part_size, total = info["uploadId"], info["partSize"], info["totalParts"]
            for i in range(2):
                await _part(client, uid, i, part_size)
            status = await (await client.get(f"/api/uploads/{uid}")).json()
            assert status["complete"] is False and sorted(status["received"]) == [0, 1]
            # Resume: an idempotent re-send of part 1 must not corrupt the stream.
            await _part(client, uid, 1, part_size)
            for i in range(2, total):
                await _part(client, uid, i, part_size)
            assert (await client.post(f"/api/uploads/{uid}/complete")).status == 200

        first = vp.artifact_path(one.id, vp.get_profile(one.id).ref_audio).read_bytes()
        second = vp.artifact_path(two.id, vp.get_profile(two.id).ref_audio).read_bytes()
        assert first == payload
        assert second == payload  # byte-equality: resume produced the same file

    @pytest.mark.asyncio
    async def test_abandoned_partial_is_never_served_as_complete(self, home, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.uploads.store.PART_SIZE", 4096)
        payload = os.urandom(4096 * 3)
        profile = vp.create_profile(name="Mine", kind="clone")
        async with TestClient(TestServer(_app(home, tmp_path, monkeypatch))) as client:
            r = await client.post(
                "/api/uploads/init",
                json={
                    "filename": "ref.wav",
                    "size": len(payload),
                    "mime": "audio/wav",
                    "target": "voice_profile",
                    "profile_id": profile.id,
                },
            )
            info = await r.json()
            await client.put(
                f"/api/uploads/{info['uploadId']}/part?index=0",
                data=payload[:4096],
                headers={"Content-Type": "application/octet-stream"},
            )
            # Never completed: the profile has no reference clip…
            assert vp.get_profile(profile.id).ref_audio == ""
            assert vp.profile_payload(vp.get_profile(profile.id))["artifacts"]["ref_audio"] is False
            # …and completing a half-uploaded session is refused outright.
            r = await client.post(f"/api/uploads/{info['uploadId']}/complete")
            assert r.status >= 400
            # …so the gated read has nothing to hand back.
            r = await client.get(f"/api/voice/profiles/{profile.id}/audio")
            assert r.status in (403, 404)

    @pytest.mark.asyncio
    async def test_upload_target_validates_profile_and_kind(self, home, tmp_path, monkeypatch):
        profile = vp.create_profile(name="Mine", kind="clone")
        async with TestClient(TestServer(_app(home, tmp_path, monkeypatch))) as client:

            async def _init(**over):
                body = {
                    "filename": "ref.wav",
                    "size": 1024,
                    "mime": "audio/wav",
                    "target": "voice_profile",
                    "profile_id": profile.id,
                }
                body.update(over)
                return await client.post("/api/uploads/init", json=body)

            assert (await _init(profile_id="../escape")).status == 400
            assert (await _init(profile_id="vp-nonexistent")).status == 404
            assert (await _init(kind="something-else")).status == 400
            # Non-audio is refused: a voice profile is not a general file drop.
            assert (await _init(filename="notes.txt", mime="text/plain")).status == 415
            assert (await _init()).status == 200

    @pytest.mark.asyncio
    async def test_consent_upload_slot_verifies_after_text(self, home, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.uploads.store.PART_SIZE", 4096)
        profile = vp.create_profile(name="Mine", kind="clone")
        clip = _wav(tmp_path / "consent-source.wav")
        payload = clip.read_bytes()
        async with TestClient(TestServer(_app(home, tmp_path, monkeypatch))) as client:
            await client.post(
                f"/api/voice/profiles/{profile.id}/consent",
                json={"consent_text": "I consent to cloning my voice"},
            )
            # Text first, audio second — verification is the recompute over both.
            assert vp.get_profile(profile.id).verified_own_voice is False
            r = await client.post(
                "/api/uploads/init",
                json={
                    "filename": "consent.wav",
                    "size": len(payload),
                    "mime": "audio/wav",
                    "target": "voice_profile",
                    "profile_id": profile.id,
                    "kind": "consent",
                },
            )
            info = await r.json()
            for i in range(info["totalParts"]):
                await client.put(
                    f"/api/uploads/{info['uploadId']}/part?index={i}",
                    data=payload[i * info["partSize"] : (i + 1) * info["partSize"]],
                    headers={"Content-Type": "application/octet-stream"},
                )
            assert (await client.post(f"/api/uploads/{info['uploadId']}/complete")).status == 200
        assert vp.get_profile(profile.id).verified_own_voice is True


# ── lock from a bounded history ────────────────────────────────────────────


class TestHistoryAndLock:
    def test_history_is_bounded_with_oversized_input(self, home, tmp_path):
        profile = vp.create_profile(name="Mine", kind="design")
        clip = _wav(tmp_path / "gen.wav", seconds=0.05)
        for i in range(vp.HISTORY_MAX * 2 + 5):
            vp.append_history(profile.id, clip, seed=i)
        stored = vp.get_profile(profile.id)
        assert len(stored.history) == vp.HISTORY_MAX
        # The FILES are pruned too, not just the records.
        files = list(vp.artifact_path(profile.id, "history").iterdir())
        assert len(files) == vp.HISTORY_MAX
        # And it is the OLDEST that went: the newest seed is still there.
        assert stored.history[-1]["seed"] == vp.HISTORY_MAX * 2 + 4
        assert stored.history[0]["seed"] == vp.HISTORY_MAX + 5

    def test_lock_pins_seed_and_writes_locked_wav(self, home, tmp_path):
        profile = vp.create_profile(name="Mine", kind="design")
        clip = _wav(tmp_path / "gen.wav", seconds=0.05)
        for seed in (11, 22, 33):
            vp.append_history(profile.id, clip, seed=seed)
        locked = vp.lock_profile(profile.id, 1)
        assert locked.locked is True and locked.seed == 22
        assert vp.artifact_path(profile.id, "locked.wav").is_file()
        unlocked = vp.unlock_profile(profile.id)
        assert unlocked.locked is False and unlocked.seed == 0
        assert not vp.artifact_path(profile.id, "locked.wav").exists()

    def test_lock_rejects_a_bad_index(self, home, tmp_path):
        profile = vp.create_profile(name="Mine", kind="design")
        with pytest.raises(vp.VoiceProfileError) as exc:
            vp.lock_profile(profile.id, 0)
        assert exc.value.reason == "empty_history"
        vp.append_history(profile.id, _wav(tmp_path / "gen.wav", seconds=0.05), seed=1)
        with pytest.raises(vp.VoiceProfileError) as exc:
            vp.lock_profile(profile.id, 7)
        assert exc.value.reason == "history_index_out_of_range"

    def test_locked_clip_becomes_the_reference(self, home, tmp_path, monkeypatch):
        profile = vp.create_profile(name="Mine", kind="clone", provider="piper-tts")
        vp.append_history(profile.id, _wav(tmp_path / "gen.wav", seconds=0.05), seed=99)
        vp.lock_profile(profile.id, 0)
        vb.set_binding("default", profile.id)
        params = _resolve(monkeypatch)
        assert params["locked"] is True and params["seed"] == 99
        assert params["ref_audio"].endswith("locked.wav")


# ── the four-level precedence chain + zero-profile regression ──────────────


def _resolve(monkeypatch, surface="", profile_id="", settings=None):
    from personalclaw.tts import registry as tr

    monkeypatch.setattr(tr, "active_tts", lambda: (_FakeProvider(), "en_US-flat.onnx"))
    monkeypatch.setattr(tr, "_provider_by_app_name", lambda name: _FakeProvider() if name else None)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_use_case_settings",
        lambda use_case: settings
        or {"speed": 1.0, "speech_voice": "nova", "enabled": True, "auto_speak": False},
    )
    return tr.active_voice_params(surface=surface, profile_id=profile_id)


class TestPrecedenceChain:
    def test_zero_profile_output_is_todays_flat_output(self, home, monkeypatch):
        params = _resolve(monkeypatch)
        assert set(params) == {
            "provider",
            "voice",
            "speed",
            "speech_voice",
            "enabled",
            "auto_speak",
        }
        assert params["voice"] == "en_US-flat.onnx"
        assert params["speed"] == 1.0
        assert params["speech_voice"] == "nova"
        assert params["enabled"] is True and params["auto_speak"] is False

    def test_deleting_every_profile_restores_the_flat_output(self, home, monkeypatch):
        def _comparable(params):
            # The provider is a live object; compare by name plus every value key.
            return {k: v for k, v in params.items() if k != "provider"} | {
                "provider_name": params["provider"].name
            }

        flat = _comparable(_resolve(monkeypatch))
        profile = vp.create_profile(name="Mine", kind="design", provider="piper-tts", model="x")
        vb.set_binding("default", profile.id)
        assert _resolve(monkeypatch)["profile_id"] == profile.id
        vp.delete_profile(profile.id)
        vb.forget_profile(profile.id)
        assert _comparable(_resolve(monkeypatch)) == flat

    def test_binding_beats_default_and_explicit_beats_binding(self, home, monkeypatch):
        fallback = vp.create_profile(name="Default", kind="design", provider="piper-tts")
        bound = vp.create_profile(name="Bound", kind="design", provider="piper-tts")
        explicit = vp.create_profile(name="Explicit", kind="design", provider="piper-tts")
        vb.set_binding("default", fallback.id)
        vb.set_binding("channel:slack", bound.id)

        # level 3 — default
        got = _resolve(monkeypatch, surface="channel:webui")
        assert (got["profile_id"], got["profile_level"]) == (fallback.id, vb.LEVEL_DEFAULT)
        # level 2 — the surface binding
        got = _resolve(monkeypatch, surface="channel:slack")
        assert (got["profile_id"], got["profile_level"]) == (bound.id, vb.LEVEL_BINDING)
        # level 1 — explicit wins over both
        got = _resolve(monkeypatch, surface="channel:slack", profile_id=explicit.id)
        assert (got["profile_id"], got["profile_level"]) == (explicit.id, vb.LEVEL_EXPLICIT)
        # level 4 — nothing bound and no default
        vb.clear_binding("default")
        vb.clear_binding("channel:slack")
        assert "profile_id" not in _resolve(monkeypatch, surface="agent:research")

    def test_resolver_returns_the_superset_dict(self, home, monkeypatch, tmp_path):
        profile = vp.create_profile(
            name="Mine",
            kind="clone",
            provider="piper-tts",
            model="engine-voice",
            ref_text="a line of reference speech",
            instruct="warm and slow",
            design_params={"accent": "us"},
            seed=7,
            speed=1.5,
        )
        _wav(vp.artifact_path(profile.id, "ref_audio.wav"))
        stored = vp.get_profile(profile.id)
        stored.ref_audio = "ref_audio.wav"
        vp._write(stored)
        vb.set_binding("agent:research-agent", profile.id)
        params = _resolve(monkeypatch, surface="agent:research-agent")
        assert params["voice"] == "engine-voice"
        assert params["speed"] == 1.5
        assert params["ref_text"] == "a line of reference speech"
        assert params["instruct"] == "warm and slow"
        assert params["design_params"] == {"accent": "us"}
        assert params["seed"] == 7
        assert params["ref_audio"].endswith("ref_audio.wav")
        # The pre-profile keys are still all there — a caller that ignores the new
        # keys keeps working.
        for key in ("provider", "voice", "speed", "speech_voice", "enabled", "auto_speak"):
            assert key in params

    def test_explicit_unknown_profile_raises(self, home, monkeypatch):
        with pytest.raises(vp.VoiceProfileError) as exc:
            _resolve(monkeypatch, profile_id="vp-nope")
        assert exc.value.reason == "not_found"

    def test_stale_binding_degrades_to_the_next_level(self, home, monkeypatch):
        vb.save_bindings({"channel:slack": "vp-gone", "default": "vp-alsogone"})
        params = _resolve(monkeypatch, surface="channel:slack")
        assert "profile_id" not in params  # falls through to built-in, no error


class TestBindingStore:
    def test_surface_keys_are_validated(self, home):
        profile = vp.create_profile(name="x", kind="design")
        for bad in ("slack", "channel:", "channel:has space", "weird:thing", ""):
            with pytest.raises(vp.VoiceProfileError) as exc:
                vb.set_binding(bad, profile.id)
            assert exc.value.reason == "invalid_surface"
        assert vb.set_binding("client:some-client", profile.id)["client:some-client"] == profile.id

    def test_binding_requires_an_existing_profile(self, home):
        with pytest.raises(vp.VoiceProfileError) as exc:
            vb.set_binding("channel:webui", "vp-missing")
        assert exc.value.reason == "not_found"

    def test_malformed_entries_are_dropped_on_read(self, home):
        vb.bindings_path().write_text(
            json.dumps({"channel:webui": "../escape", "nonsense": "vp-a", "default": "vp-a"})
        )
        assert vb.load_bindings() == {"default": "vp-a"}

    def test_deleting_a_profile_forgets_its_bindings(self, home):
        profile = vp.create_profile(name="x", kind="design")
        vb.set_binding("channel:webui", profile.id)
        vb.set_binding("default", profile.id)
        vp.delete_profile(profile.id)
        assert vb.forget_profile(profile.id) == {}
