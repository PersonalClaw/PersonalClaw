"""P3d — async local-model downloads (registry + HTTP handlers).

Drives the :class:`ModelDownloadRegistry` and the /api/models/downloads/*
handlers with the provider resolution/catalog functions monkeypatched, so no real
HuggingFace download happens. Downloads are PROVIDER-scoped (provider name + model).
Asserts: job lifecycle (running→done/error), (provider, model) dedupe,
already-downloaded short-circuit, validation, cancel, re-attach listing, and that
progress frames reach an SSE subscriber.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard import model_downloads as M
from personalclaw.dashboard.handlers import model_downloads as H

# ── catalog/provider stubs (no network) ──


@pytest.fixture(autouse=True)
def _stub_providers(monkeypatch):
    """Stub provider resolution, catalog membership, size, on-disk state, and the fetch.

    The fetch is an awaitable we can pace, so a test can observe the `running`
    state before completion. ``_provider`` resolves any name to a truthy sentinel
    (a real registered provider isn't needed for the job-runner mechanics).
    """
    monkeypatch.setattr(M, "_provider", lambda name: object())
    monkeypatch.setattr(
        M, "_model_exists", lambda provider, model: model in {"good", "slow", "boom", "already"}
    )
    monkeypatch.setattr(M, "_expected_size_bytes", lambda provider, model: 4 * 1024 * 1024)
    monkeypatch.setattr(M, "_is_downloaded", lambda provider, model: model == "already")
    monkeypatch.setattr(M, "_dir_size", lambda path: 0)

    async def _fetch(provider, model):
        if model == "boom":
            raise RuntimeError("download exploded")
        if model == "slow":
            await asyncio.sleep(0.05)

    monkeypatch.setattr(M, "_run_fetch", _fetch)


async def _settle():
    """Yield control so scheduled job tasks can run to completion."""
    for _ in range(20):
        await asyncio.sleep(0.02)


# ── registry ──


@pytest.mark.asyncio
async def test_start_runs_to_done():
    reg = M.ModelDownloadRegistry()
    job, err = reg.start("sentence-transformers", "good")
    assert err is None and job is not None
    assert job.status == "running"
    assert job.size_bytes == 4 * 1024 * 1024
    await _settle()
    assert reg.get(job.id).status == "done"


@pytest.mark.asyncio
async def test_start_records_error():
    reg = M.ModelDownloadRegistry()
    job, err = reg.start("faster-whisper", "boom")
    assert err is None
    await _settle()
    done = reg.get(job.id)
    assert done.status == "error"
    assert "exploded" in done.error


@pytest.mark.asyncio
async def test_unknown_provider_and_unknown_model(monkeypatch):
    reg = M.ModelDownloadRegistry()
    # Unknown provider → _provider() returns None for this name.
    monkeypatch.setattr(M, "_provider", lambda name: None if name == "bogus" else object())
    job, err = reg.start("bogus", "good")
    assert job is None and "Unknown provider" in err
    job, err = reg.start("sentence-transformers", "nonesuch")
    assert job is None and "Unknown model" in err
    job, err = reg.start("sentence-transformers", "")
    assert job is None and "Missing" in err


@pytest.mark.asyncio
async def test_dedupe_in_flight_returns_same_job():
    reg = M.ModelDownloadRegistry()
    j1, _ = reg.start("piper-tts", "slow")
    j2, _ = reg.start("piper-tts", "slow")
    assert j1.id == j2.id  # same (provider, model) while running → one job
    await _settle()


@pytest.mark.asyncio
async def test_already_downloaded_short_circuits():
    reg = M.ModelDownloadRegistry()
    job, err = reg.start("sentence-transformers", "already")
    assert err is None
    assert job.status == "done"
    assert job.bytes == job.size_bytes  # reported complete immediately


@pytest.mark.asyncio
async def test_cancel_detaches_job():
    reg = M.ModelDownloadRegistry()
    job, _ = reg.start("piper-tts", "slow")
    assert reg.cancel(job.id) is True
    assert reg.get(job.id) is None  # dropped from registry
    assert reg.cancel(job.id) is False  # unknown now
    await _settle()


@pytest.mark.asyncio
async def test_progress_frames_reach_subscriber():
    reg = M.ModelDownloadRegistry()
    job, _ = reg.start("faster-whisper", "slow")
    hub = reg.sse.hub(M.registry_key(job.id))
    q = hub.subscribe()
    await _settle()
    seen = []
    while not q.empty():
        seen.append(q.get_nowait().name)
    assert "done" in seen  # terminal frame published to the live hub


# ── HTTP handlers ──


def _req(method, path, reg, *, body=None, match_info=None):
    app = web.Application()

    class _State:
        def model_downloads(self):
            return reg

    app["state"] = _State()
    req = make_mocked_request(method, path, match_info=match_info or {}, app=app)
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


def _body(resp):
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_handler_start_returns_202_and_lists():
    reg = M.ModelDownloadRegistry()
    resp = await H.api_model_download_start(
        _req(
            "POST",
            "/api/models/downloads",
            reg,
            body={"provider": "sentence-transformers", "model": "good"},
        )
    )
    assert resp.status == 202
    jid = _body(resp)["id"]
    await _settle()

    lst = await H.api_model_downloads_list(_req("GET", "/api/models/downloads", reg))
    ids = [d["id"] for d in _body(lst)["downloads"]]
    assert jid in ids


@pytest.mark.asyncio
async def test_handler_start_rejects_bad_body():
    reg = M.ModelDownloadRegistry()
    resp = await H.api_model_download_start(
        _req(
            "POST",
            "/api/models/downloads",
            reg,
            body={"provider": "sentence-transformers", "model": "nope"},
        )
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_handler_cancel_unknown_404():
    reg = M.ModelDownloadRegistry()
    resp = await H.api_model_download_cancel(
        _req("DELETE", "/api/models/downloads/dl-99", reg, match_info={"id": "dl-99"})
    )
    assert resp.status == 404


@pytest.mark.asyncio
async def test_handler_stream_missing_job_404():
    reg = M.ModelDownloadRegistry()
    resp = await H.api_model_download_stream(
        _req("GET", "/api/models/downloads/dl-99/stream", reg, match_info={"id": "dl-99"})
    )
    assert resp.status == 404
