"""Gated-repo download pre-warn (LMMV-4 §4.3) — consumes cascade token status server-side.

A gated model with NO token in any cascade source is a guaranteed 401: the runner must
fail the job FAST with the typed reason ``gated_repo:no_token`` (and never auto-retry)
instead of attempting the doomed fetch. When a token IS present, the fetch proceeds
normally.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.dashboard import model_downloads as M


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(M, "_provider", lambda name: object())
    monkeypatch.setattr(M, "_model_exists", lambda provider, model: True)
    monkeypatch.setattr(M, "_expected_size_bytes", lambda provider, model: 1024)
    monkeypatch.setattr(M, "_is_downloaded", lambda provider, model: False)
    monkeypatch.setattr(M, "_dir_size", lambda path: 0)
    # The model under test is gated.
    monkeypatch.setattr(M, "_is_gated", lambda provider, model: True)

    fetched: list[str] = []

    async def _fetch(provider, model):
        fetched.append(model)

    monkeypatch.setattr(M, "_run_fetch", _fetch)
    return fetched


async def _settle():
    for _ in range(20):
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_gated_no_token_fails_fast_typed(monkeypatch, _stub):
    monkeypatch.setattr(M, "_hf_token_present", lambda: False)
    reg = M.ModelDownloadRegistry()
    job, err = reg.start("diarization-pyannote", "community-1")
    assert err is None and job is not None
    # No fetch attempted; job is error with the typed gated reason.
    assert job.state == "error"
    assert job.reason == "gated_repo:no_token"
    await _settle()
    assert _stub == []  # the doomed fetch was never run


@pytest.mark.asyncio
async def test_gated_with_token_proceeds(monkeypatch, _stub):
    monkeypatch.setattr(M, "_hf_token_present", lambda: True)
    reg = M.ModelDownloadRegistry()
    job, err = reg.start("diarization-pyannote", "community-1")
    assert err is None
    await _settle()
    assert reg.get(job.id).state == "done"
    assert _stub == ["community-1"]


@pytest.mark.asyncio
async def test_non_gated_ignores_token_state(monkeypatch, _stub):
    monkeypatch.setattr(M, "_is_gated", lambda provider, model: False)
    monkeypatch.setattr(M, "_hf_token_present", lambda: False)
    reg = M.ModelDownloadRegistry()
    job, err = reg.start("faster-whisper", "tiny")
    assert err is None
    await _settle()
    assert reg.get(job.id).state == "done"
