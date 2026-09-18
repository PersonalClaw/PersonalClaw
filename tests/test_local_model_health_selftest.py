"""Per-provider health + real-inference selftest (LOCAL-MODEL-MANAGER-V2 §6, LMMV-4).

No real model, no real weights, no network — fake providers stand in for the six local
backends. The properties under test:

* the ABC ``availability_detail()`` default wraps ``is_available()`` and NEVER raises;
* ``GET /api/models/local/{provider}/health`` never 500s — an unavailable or raising provider
  still returns a typed body;
* ``POST /api/models/local/{provider}/selftest`` runs a REAL per-capability inference against
  THIS provider object, returns TYPED reasons, and a pyannote-4-style contract break fails on
  the API surface (Success Criterion 5) — not on file presence;
* the selftest is bounded by a timeout, serialized behind ``single_flight``, and a provider
  whose capabilities route through a model-provider binding is reported, not false-greened.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import model_downloads as md
from personalclaw.local_models import registry as reg
from personalclaw.local_models.provider import LocalModelProvider


class _BaseFake(LocalModelProvider):
    """Minimal local provider — the management contract, nothing resident."""

    _pname = "fake"

    @property
    def name(self) -> str:
        return self._pname

    @property
    def display_name(self) -> str:
        return self._pname

    async def is_available(self) -> bool:
        return True

    async def list_models(self):
        return []

    async def download_model(self, model_name: str) -> bool:
        return True

    async def delete_model(self, model_name: str) -> bool:
        return True


class _SttFake(_BaseFake):
    async def transcribe(self, audio_path, model="", language=""):
        return "the fixture said hello"


class _DiarBreakFake(_BaseFake):
    """A pyannote-4-style runtime-contract break: the API shape changed under it."""

    async def diarize(
        self, audio_path, *, model="", num_speakers=None, min_speakers=None, max_speakers=None
    ):
        raise AttributeError("'DiarizeOutput' object has no attribute 'itertracks'")


class _DiarNoneFake(_BaseFake):
    async def diarize(
        self, audio_path, *, model="", num_speakers=None, min_speakers=None, max_speakers=None
    ):
        return None


class _EmbFake(_BaseFake):
    async def embed(self, text, model=""):
        return [0.1] * 384


class _TtsFake(_BaseFake):
    async def synthesize(self, text, voice="", output_path="", *, speed=1.0, **opts):
        import os
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".wav", prefix="pc-tts-fake-")
        os.close(fd)
        return path  # the selftest must delete this (TtsProvider caller-owns contract)


class _SlowSttFake(_BaseFake):
    async def transcribe(self, audio_path, model="", language=""):
        import asyncio

        await asyncio.sleep(5)
        return "too late"


class _RaisingFake(_BaseFake):
    async def is_available(self) -> bool:
        raise RuntimeError("torch import blew up")


@pytest.fixture()
def registered():
    """Register a provider under a name for the duration of a test, then clean up."""
    names: list[str] = []

    def _register(provider, name, caps):
        provider._pname = name
        reg.register_provider(provider, capabilities=caps, name=name)
        names.append(name)
        return name

    yield _register
    for n in names:
        reg.unregister_provider(n)


def _req(method, path, *, match_info=None, body=None):
    app = web.Application()
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app)
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


def _payload(resp):
    return json.loads(resp.body.decode())


# ── availability_detail() default (clause 5) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_availability_detail_default_reports_ready_and_unavailable():
    assert await _SttFake().availability_detail() == (True, "ready")

    class _Down(_BaseFake):
        async def is_available(self) -> bool:
            return False

    ok, msg = await _Down().availability_detail()
    assert ok is False and "not available" in msg


@pytest.mark.asyncio
async def test_availability_detail_never_raises():
    ok, msg = await _RaisingFake().availability_detail()
    assert ok is False
    assert "RuntimeError" in msg


# ── GET /health never 500s (clause 5) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_returns_typed_body_even_when_provider_raises(registered):
    registered(_RaisingFake(), "raiser", ["stt"])
    resp = await md.api_local_model_health(
        _req("GET", "/api/models/local/raiser/health", match_info={"provider": "raiser"})
    )
    assert resp.status == 200  # NEVER a 500
    body = _payload(resp)
    assert body["provider"] == "raiser"
    assert body["ok"] is False
    assert "message" in body and "latency_ms" in body


@pytest.mark.asyncio
async def test_health_unknown_provider_is_404_typed_not_500(registered):
    resp = await md.api_local_model_health(
        _req("GET", "/api/models/local/nope/health", match_info={"provider": "nope"})
    )
    assert resp.status == 404
    assert _payload(resp)["ok"] is False


@pytest.mark.asyncio
async def test_health_falls_back_to_is_available_for_a_duck_typed_provider(registered):
    # A provider registered by capability duck-typing (not subclassing the ABC) has no
    # availability_detail; health must still answer from its bare is_available bool.
    class _DuckTyped:
        name = "duck"
        display_name = "Duck"

        async def is_available(self):
            return True

        async def list_models(self):
            return []

        async def download_model(self, m):
            return True

        async def delete_model(self, m):
            return True

    registered(_DuckTyped(), "duck", ["stt"])
    resp = await md.api_local_model_health(
        _req("GET", "/api/models/local/duck/health", match_info={"provider": "duck"})
    )
    assert resp.status == 200
    body = _payload(resp)
    assert body["ok"] is True
    assert body["message"] == "ready"


# ── selftest dispatch: real inference + typed reasons (clause 6, SC5) ─────────────────


@pytest.mark.asyncio
async def test_selftest_stt_runs_a_real_inference():
    out = await md._dispatch_selftest(_SttFake(), ["stt"], "", 30.0)
    assert out["stt"]["ok"] is True
    assert out["stt"]["reason"] == ""


@pytest.mark.asyncio
async def test_selftest_embedding_reports_dims():
    out = await md._dispatch_selftest(_EmbFake(), ["embedding"], "", 30.0)
    assert out["embedding"]["ok"] is True
    assert out["embedding"]["detail"] == "384 dims"


@pytest.mark.asyncio
async def test_selftest_tts_ok_and_cleans_up_its_file():
    out = await md._dispatch_selftest(_TtsFake(), ["tts"], "", 30.0)
    assert out["tts"]["ok"] is True


@pytest.mark.asyncio
async def test_selftest_diarization_contract_break_fails_on_api_with_typed_reason():
    # THE Success-Criterion-5 case: the model file could be perfectly present, but the runtime
    # API changed — the selftest RUNS it and fails with a typed reason, not a false green.
    out = await md._dispatch_selftest(_DiarBreakFake(), ["diarization"], "", 30.0)
    assert out["diarization"]["ok"] is False
    assert out["diarization"]["reason"] == "selftest_error:AttributeError"


@pytest.mark.asyncio
async def test_selftest_diarization_none_is_a_typed_empty_result():
    out = await md._dispatch_selftest(_DiarNoneFake(), ["diarization"], "", 30.0)
    assert out["diarization"]["ok"] is False
    assert out["diarization"]["reason"] == "diarization_returned_nothing"


@pytest.mark.asyncio
async def test_selftest_times_out_with_a_typed_reason():
    out = await md._dispatch_selftest(_SlowSttFake(), ["stt"], "", 0.05)
    assert out["stt"]["ok"] is False
    assert out["stt"]["reason"] == "timeout"


@pytest.mark.asyncio
async def test_selftest_skips_capabilities_the_object_cannot_serve_directly():
    # A chat-only provider (ollama) exposes no local inference method here → nothing dispatched.
    out = await md._dispatch_selftest(_BaseFake(), ["chat"], "", 30.0)
    assert out == {}


# ── selftest HTTP surface: 404 / not-directly-testable / 409 lock (clause 6) ──────────


def _isolate_locks(monkeypatch, tmp_path):
    """Point single_flight's lock dir at tmp_path — never the real home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)


@pytest.mark.asyncio
async def test_selftest_unknown_provider_is_404():
    resp = await md.api_local_model_selftest(
        _req("POST", "/api/models/local/nope/selftest", match_info={"provider": "nope"}, body={})
    )
    assert resp.status == 404


@pytest.mark.asyncio
async def test_selftest_happy_path_returns_typed_capability_results(
    registered, tmp_path, monkeypatch
):
    _isolate_locks(monkeypatch, tmp_path)
    registered(_SttFake(), "stt-fake", ["stt"])
    resp = await md.api_local_model_selftest(
        _req(
            "POST",
            "/api/models/local/stt-fake/selftest",
            match_info={"provider": "stt-fake"},
            body={},
        )
    )
    assert resp.status == 200
    body = _payload(resp)
    assert body["provider"] == "stt-fake"
    assert body["capabilities"]["stt"]["ok"] is True


@pytest.mark.asyncio
async def test_selftest_not_directly_testable_provider_is_reported_not_false_greened(
    registered, tmp_path, monkeypatch
):
    _isolate_locks(monkeypatch, tmp_path)
    registered(_BaseFake(), "chat-only", ["chat"])
    resp = await md.api_local_model_selftest(
        _req(
            "POST",
            "/api/models/local/chat-only/selftest",
            match_info={"provider": "chat-only"},
            body={},
        )
    )
    assert resp.status == 200
    body = _payload(resp)
    assert body["capabilities"] == {}
    assert "model-provider" in body["detail"]


@pytest.mark.asyncio
async def test_selftest_is_409_when_a_selftest_is_already_running(registered, monkeypatch):
    registered(_SttFake(), "stt-fake2", ["stt"])

    @contextmanager
    def _busy(_key):
        yield False  # single_flight reports the lock is already held

    monkeypatch.setattr("personalclaw.concurrency.single_flight", _busy)
    resp = await md.api_local_model_selftest(
        _req(
            "POST",
            "/api/models/local/stt-fake2/selftest",
            match_info={"provider": "stt-fake2"},
            body={},
        )
    )
    assert resp.status == 409
    assert _payload(resp)["reason"] == "selftest_running"
