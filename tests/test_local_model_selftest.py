"""Per-provider health + real-inference selftest (LMMV-4 §6, Success Criterion 5).

The health endpoint must NEVER 500; the selftest must run a REAL inference and fail a
broken runtime contract (a pyannote-4-style API break) with a typed reason rather than
passing on file presence.
"""

from __future__ import annotations

import pytest

from personalclaw.local_models import registry
from personalclaw.local_models.provider import LocalModel, LocalModelProvider
from personalclaw.local_models.selftest import provider_health, provider_selftest


@pytest.fixture(autouse=True)
def _clean_registry():
    """Isolate the process-global local-model registry per test."""
    saved = dict(registry._providers)
    saved_caps = dict(registry._capabilities)
    registry._providers.clear()
    registry._capabilities.clear()
    yield
    registry._providers.clear()
    registry._providers.update(saved)
    registry._capabilities.clear()
    registry._capabilities.update(saved_caps)


class _FakeSttProvider(LocalModelProvider):
    """A local STT provider whose transcribe/availability are controllable per test."""

    def __init__(
        self, *, available=True, avail_raises=False, transcribe_result="hi", transcribe_raises=False
    ):
        self._available = available
        self._avail_raises = avail_raises
        self._transcribe_result = transcribe_result
        self._transcribe_raises = transcribe_raises

    @property
    def name(self) -> str:
        return "fake_stt"

    @property
    def display_name(self) -> str:
        return "Fake STT"

    async def is_available(self) -> bool:
        if self._avail_raises:
            raise RuntimeError("torch import blew up: libcusomething not found")
        return self._available

    async def list_models(self):
        return [LocalModel(name="tiny", capabilities=["stt"], downloaded=True)]

    async def download_model(self, model_name: str) -> bool:
        return True

    async def delete_model(self, model_name: str) -> bool:
        return True

    async def transcribe(self, audio_path: str, model: str = "", language: str = ""):
        if self._transcribe_raises:
            # The pyannote-4 class: weights present, but the inference API broke.
            raise TypeError("DiarizeOutput has no attribute itertracks")
        return self._transcribe_result


# ── health never 500s (Success Criterion for the health endpoint) ──────────────


@pytest.mark.asyncio
async def test_health_ok_for_available_provider():
    registry.register_provider(_FakeSttProvider(available=True), ["stt"], name="fw")
    h = await provider_health("fw")
    assert h["ok"] is True
    assert h["provider"] == "fw"
    assert "latency_ms" in h


@pytest.mark.asyncio
async def test_health_never_raises_when_availability_explodes():
    """A provider whose is_available() raises must yield ok=False, NOT propagate."""
    registry.register_provider(_FakeSttProvider(avail_raises=True), ["stt"], name="fw")
    h = await provider_health("fw")
    assert h["ok"] is False
    assert h["message"]  # carries the (redacted) reason


@pytest.mark.asyncio
async def test_health_unknown_provider_is_soft_not_500():
    h = await provider_health("nonexistent")
    assert h["ok"] is False
    assert "unknown provider" in h["message"]


@pytest.mark.asyncio
async def test_availability_detail_default_wraps_is_available():
    """The ABC default availability_detail wraps is_available (additive, no override)."""
    p = _FakeSttProvider(available=False)
    ok, msg = await p.availability_detail()
    assert ok is False and isinstance(msg, str)


# ── selftest runs a REAL inference and types failures (Success Criterion 5) ────


@pytest.mark.asyncio
async def test_selftest_passes_on_working_stt():
    registry.register_provider(_FakeSttProvider(transcribe_result="hello"), ["stt"], name="fw")
    r = await provider_selftest("fw")
    assert r["ok"] is True
    assert r["capabilities"]["stt"]["ok"] is True
    assert r["capabilities"]["stt"]["reason"] == ""


@pytest.mark.asyncio
async def test_selftest_broken_runtime_contract_fails_typed():
    """A transcribe that RAISES (API break) fails with reason=runtime_contract — the
    pyannote-4 class: it must NOT pass just because the model files exist."""
    registry.register_provider(_FakeSttProvider(transcribe_raises=True), ["stt"], name="fw")
    r = await provider_selftest("fw")
    assert r["ok"] is False
    assert r["capabilities"]["stt"]["ok"] is False
    assert r["capabilities"]["stt"]["reason"] == "runtime_contract"


@pytest.mark.asyncio
async def test_selftest_none_result_is_runtime_contract():
    """transcribe() returning None (not raising) is still a contract failure."""
    registry.register_provider(_FakeSttProvider(transcribe_result=None), ["stt"], name="fw")
    r = await provider_selftest("fw")
    assert r["capabilities"]["stt"]["reason"] == "runtime_contract"


@pytest.mark.asyncio
async def test_selftest_unknown_provider_soft():
    r = await provider_selftest("nope")
    assert r["ok"] is False
    assert "unknown provider" in r["detail"]


@pytest.mark.asyncio
async def test_selftest_no_testable_capability():
    """A provider whose declared caps aren't selftestable returns ok with no caps."""

    class _Odd(_FakeSttProvider):
        @property
        def name(self) -> str:
            return "odd"

    registry.register_provider(_Odd(), ["image_gen"], name="odd")
    r = await provider_selftest("odd")
    assert r["ok"] is True
    assert r["capabilities"] == {}


@pytest.mark.asyncio
async def test_selftest_timeout_typed(monkeypatch):
    """A capability that exceeds the per-capability timeout is reason=timeout."""
    import asyncio as _asyncio

    class _Slow(_FakeSttProvider):
        async def transcribe(self, audio_path: str, model: str = "", language: str = ""):
            await _asyncio.sleep(5)
            return "late"

    registry.register_provider(_Slow(), ["stt"], name="slow")
    monkeypatch.setattr("personalclaw.local_models.selftest._timeout_s", lambda: 0.05)
    r = await provider_selftest("slow")
    assert r["capabilities"]["stt"]["reason"] == "timeout"
