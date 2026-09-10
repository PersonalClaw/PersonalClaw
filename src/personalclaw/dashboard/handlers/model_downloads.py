"""HTTP API for async bundled-model downloads — /api/models/downloads/*.

One async path for every native bundled model (embedding / STT / TTS). A POST
starts a background job and returns ``202`` with the job; the per-job SSE stream
carries ``progress``/``done``/``error``/``cancelled`` frames; a GET lists live +
recently-finished jobs so a reloaded client re-attaches; a DELETE cancels.

This replaces the three synchronous, kind-specific download routes
(``POST /api/memory/download-model``, ``POST /api/stt/.../download``,
``POST /api/tts/models/{model}/download``) — the request no longer blocks for the
whole multi-minute fetch.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw.providers.failure_copy import relayed_failure_copy

logger = logging.getLogger(__name__)


def _registry(request: web.Request):
    return request.app["state"].model_downloads()


async def api_model_downloads_list(request: web.Request) -> web.Response:
    """GET /api/models/downloads — live + recently-finished download jobs."""
    reg = _registry(request)
    return web.json_response({"downloads": [j.to_dict() for j in reg.list()]})


async def api_model_download_start(request: web.Request) -> web.Response:
    """POST /api/models/downloads — start a download. Body: {provider, model}.

    Returns ``202`` with the job. Re-requesting an in-flight ``(provider, model)``
    returns the same job; an already-downloaded model returns a ``done`` job.

    This handler downloads exactly the model it was asked for. The memory step-down (offer
    the largest variant that FITS instead of one that cannot load) belongs in the browse
    payload — ``GET /api/models/available`` carries ``fit_step_down`` per row — because a
    substitution here would return a job for a model the user never requested, and its SSE
    stream and progress would be keyed to that other model.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    provider = str(body.get("provider", ""))
    model = str(body.get("model", ""))

    # Free space BEFORE the fetch: a download that cannot land should be refused in the
    # request, not after the user waits for gigabytes. The refusal names both numbers.
    precheck = await _download_precheck(_registry(request), provider, model)
    if precheck is not None and not precheck.ok:
        return web.json_response({"error": precheck.reason}, status=400)

    job, error = _registry(request).start(provider, model)
    if error is not None:
        return web.json_response({"error": error}, status=400)
    payload = job.to_dict()
    if precheck is not None and precheck.warning:
        # An unmeasurable filesystem is not a reason to block a good download — but the
        # user is told the download was never verified to fit rather than assuming it was.
        payload["warning"] = precheck.warning
    return web.json_response(payload, status=202)


async def _download_precheck(reg, provider_name: str, model: str):
    """The pre-download free-space decision, or None when there is nothing to check.

    Returns a :class:`personalclaw.local_models.fit.DiskPrecheck`. None (skip entirely) when
    no bytes are about to land: an unknown provider (``start`` reports that itself), a model
    the catalog already has on disk, or a re-request for an in-flight job — every one of
    those would otherwise refuse a request that downloads nothing.
    """
    from personalclaw.local_models import fit
    from personalclaw.local_models.registry import catalog_for, get_provider

    provider = get_provider(provider_name)
    if provider is None:
        return None
    live = ("queued", "running")
    for job in reg.list():
        if job.provider == provider_name and job.model == model and job.state in live:
            return None

    need_mb = 0.0
    try:
        for lm in await catalog_for(provider):
            if lm.name == model:
                if lm.downloaded:
                    return None  # already on disk; nothing to land
                need_mb = float(lm.size_mb or 0)
                break
    except Exception:  # noqa: BLE001 — a catalog failure must not block a download
        logger.debug("catalog lookup failed for %s/%s", provider_name, model, exc_info=True)

    return fit.disk_precheck(need_mb, _provider_cache_dir(provider))


async def api_model_download_stream(request: web.Request) -> web.StreamResponse:
    """GET /api/models/downloads/{id}/stream — per-job progress SSE.

    Replays the job's current state as a ``snapshot`` on connect (so a late or
    re-attaching client is immediately current), then streams
    ``progress``/``done``/``error``/``cancelled`` frames until the job finishes
    or the client disconnects.
    """
    from personalclaw.dashboard.model_downloads import registry_key
    from personalclaw.dashboard.sse import stream_response

    reg = _registry(request)
    job_id = request.match_info["id"]
    job = reg.get(job_id)
    if job is None:
        return web.json_response({"error": "Not found"}, status=404)

    key = registry_key(job_id)
    hub = reg.sse.hub(key)
    return await stream_response(
        request,
        hub,
        on_connect=[("snapshot", job.to_dict())],
        registry_evict=(reg.sse, key),
    )


async def api_model_download_cancel(request: web.Request) -> web.Response:
    """DELETE /api/models/downloads/{id} — cancel and detach a download job."""
    if _registry(request).cancel(request.match_info["id"]):
        return web.json_response({"ok": True})
    return web.json_response({"error": "Not found"}, status=404)


async def api_local_model_delete(request: web.Request) -> web.Response:
    """DELETE /api/models/local/{provider}/{model} — delete a downloaded local model.

    Generic across every local-model provider (faster-whisper, piper,
    sentence-transformers, the diarization backends, ollama, …): resolves the named
    provider from the local-model registry.

    Freeing the disk is the SHARED layout sweep (:func:`layouts.delete_all_layouts`),
    not each provider's own ``delete_model`` — that is Success Criterion 2. A model
    fetched twice by different paths (a provider ``save()`` and later an HF snapshot)
    leaves two copies on disk; a ``delete_model`` that only knows its own ``save()``
    layout frees one and the disk never fully frees. When the provider exposes a
    cache root (``cache_dir()``), the greedy sweep removes EVERY layout that root
    holds for the model — so a twice-fetched model actually frees. ``delete_model``
    still runs afterward for provider-specific teardown (an index entry, a manifest,
    an ollama registry blob) and as the authoritative path for a provider whose
    weights don't live under a ``cache_dir()`` (``cache_dir()`` is None)."""
    from personalclaw.local_models import layouts
    from personalclaw.local_models.registry import get_provider

    provider_name = request.match_info["provider"]
    model = request.match_info["model"]
    provider = get_provider(provider_name)
    if provider is None:
        return web.json_response({"error": f"Unknown provider {provider_name!r}"}, status=404)

    swept: list = []
    cache_root = _provider_cache_dir(provider)
    if cache_root is not None:
        swept = layouts.delete_all_layouts(cache_root, model)
    try:
        ok = await provider.delete_model(model)
    except Exception as exc:  # noqa: BLE001 — surface a delete failure honestly
        logger.warning("model delete failed for provider %r", provider_name, exc_info=True)
        return web.json_response({"error": relayed_failure_copy(exc)}, status=500)
    # The delete succeeded if either teardown path removed something: the shared
    # sweep freed a layout, or the provider's own teardown reported success.
    if ok or swept:
        return web.json_response({"ok": True, "swept": len(swept)})
    return web.json_response({"error": "model not found or delete failed"}, status=404)


def _provider_cache_dir(provider) -> str | None:
    """The provider's cache root for the layout sweep, or None (best-effort).

    A provider MAY expose ``cache_dir()`` (the dir whose growth tracks a download,
    typed ``str | None`` on the local-model provider ABC). When it returns a path,
    that is the root the shared layout sweep works over; a None / raising provider
    degrades to provider-only teardown."""
    getter = getattr(provider, "cache_dir", None)
    if not callable(getter):
        return None
    try:
        got = getter()
    except Exception:  # noqa: BLE001 — a provider cache_dir must never break delete
        return None
    return str(got) if got else None


def _provider_cache_roots() -> list:
    """Every registered local provider's cache root, deduped in registration order.

    The set the cleanup affordance scans: a partial-download leftover can live under
    ANY provider's cache root, so "Reclaim N GB" enumerates them all. Two providers
    that share a root (both under the shared HF hub cache) contribute it once."""
    from personalclaw.local_models.registry import list_providers

    roots: list = []
    seen: set[str] = set()
    for provider in list_providers():
        root = _provider_cache_dir(provider)
        if root is None:
            continue
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)
    return roots


async def api_model_download_cleanup_candidates(request: web.Request) -> web.Response:
    """GET /api/models/downloads/cleanup-candidates — partial-download leftovers.

    Enumerates ``*.part``/``*.tmp``/``*.incomplete`` files across every local
    provider's cache root (:func:`layouts.cleanup_candidates`) — the files a
    cancelled or crashed fetch leaves behind, which are otherwise invisible because
    nothing lists them. Powers the "Reclaim N GB" affordance."""
    from personalclaw.local_models import layouts

    candidates: list[dict] = []
    for root in _provider_cache_roots():
        candidates.extend(layouts.cleanup_candidates(root))
    candidates.sort(key=lambda c: c["bytes"], reverse=True)
    total = sum(c["bytes"] for c in candidates)
    return web.json_response({"candidates": candidates, "total_bytes": total})


async def api_model_download_cleanup(request: web.Request) -> web.Response:
    """POST /api/models/downloads/cleanup — delete the partial-download leftovers.

    Body ``{confirm: true}`` (guarded — a missing/false ``confirm`` returns 400,
    because this unlinks files). Re-enumerates candidates across every provider's
    cache root and unlinks them best-effort per file, returning what actually went."""
    import os

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not (isinstance(body, dict) and body.get("confirm") is True):
        return web.json_response({"error": "confirm:true required"}, status=400)

    from personalclaw.local_models import layouts

    removed = 0
    freed = 0
    for root in _provider_cache_roots():
        for cand in layouts.cleanup_candidates(root):
            try:
                os.unlink(cand["path"])
                removed += 1
                freed += int(cand["bytes"])
            except OSError:
                continue  # best-effort per file; a race/permission skip is fine
    return web.json_response({"removed": removed, "freed_bytes": freed})


async def api_sidecar_install_start(request: web.Request) -> web.Response:
    """POST /api/models/sidecar/{provider}/install — start the resumable install.

    Returns ``202`` with the canonical job record. Safe to call again: an in-flight job is
    returned as-is and a finished install re-runs steps that existence-check themselves
    into ``skipped``, so a killed install resumes rather than starting over (§3.2).
    """
    provider = request.match_info["provider"]
    job, error = _registry(request).start_install(provider)
    if error is not None:
        return web.json_response({"error": error}, status=400)
    assert job is not None  # start_install returns one of the two
    return web.json_response(job.to_dict(), status=202)


async def api_sidecar_install_status(request: web.Request) -> web.Response:
    """GET /api/models/sidecar/{provider}/install/status — the rich install poll shape.

    ``{provider, installed, managed, install_dir, job: {state, steps, log_tail, error,
    remediation, weights_progress}}``. ``remediation`` is deliberately separate from
    ``error``: the error says what broke, the remediation says what the user should DO,
    which is the difference between a dead end and a next step.
    """
    provider = request.match_info["provider"]
    registry = _registry(request)
    install = registry.install(provider)
    if install is None:
        return web.json_response(
            {"error": f"{provider!r} declares no sidecar provider"}, status=404
        )
    status = install.status()
    job = registry.install_job(provider)
    status["job"] = {
        "state": job.state if job is not None else "idle",
        "progress": job.progress if job is not None else 0.0,
        "steps": status.pop("steps"),
        "log_tail": status.pop("log_tail"),
        "error": status.pop("error"),
        "reason": status.pop("reason"),
        "remediation": status.pop("remediation"),
        "weights_progress": _weights_progress(registry, provider),
    }
    return web.json_response(status)


def _weights_progress(registry, provider: str) -> float:
    """Progress of a live WEIGHTS job for *provider* (0.0 when none is running).

    The install surface reports weights progress without owning it: the weights fetch is
    the ordinary download job, read here from the same canonical record the download UI
    reads (§4.1). One writer, two readers — never a second progress source.
    """
    for job in registry.list():
        if job.provider == provider and job.kind == "weights" and job.state == "running":
            return job.progress
    return 0.0


async def api_sidecar_install_delete(request: web.Request) -> web.Response:
    """DELETE /api/models/sidecar/{provider}/install — remove a CORE-created venv.

    ``409`` while a job runs (deleting the tree under a live pip is how you get a
    half-installed venv that every later step believes), and ``400`` for a venv core did
    not create — a user-managed environment is never deleted, because core cannot know
    what else depends on it.
    """
    provider = request.match_info["provider"]
    registry = _registry(request)
    install = registry.install(provider)
    if install is None:
        return web.json_response(
            {"error": f"{provider!r} declares no sidecar provider"}, status=404
        )
    job = registry.install_job(provider)
    if job is not None and job.state in ("queued", "running"):
        return web.json_response(
            {"error": "install in progress", "reason": "install_running"}, status=409
        )
    if not install.managed:
        return web.json_response(
            {
                "error": "this venv was not created by PersonalClaw and is never deleted",
                "reason": "unmanaged_venv",
            },
            status=400,
        )
    from personalclaw.local_models.sidecar import unregister_runner

    unregister_runner(provider)  # stop the child before its interpreter disappears
    return web.json_response({"ok": install.delete()})


async def api_models_loaded(request: web.Request) -> web.Response:
    """GET /api/models/loaded — every resident model + the memory-pressure snapshot.

    Answers "what is occupying my RAM right now", including the reclaimable case: a model
    still resident after its binding moved elsewhere reports ``is_active: false``.
    """
    from personalclaw.local_models.residency import residency_snapshot

    return web.json_response(await residency_snapshot())


async def api_models_unload(request: web.Request) -> web.Response:
    """POST /api/models/unload {provider} — free what a provider holds. Idempotent.

    The reply carries a FRESH pressure snapshot, so the surface can show that the unload
    actually freed memory instead of asserting it (Success Criterion 8).
    """
    from personalclaw.local_models.residency import unload_provider

    try:
        body = await request.json()
    except Exception:
        body = {}
    provider = str((body or {}).get("provider", "")) if isinstance(body, dict) else ""
    if not provider:
        return web.json_response({"error": "Missing 'provider'"}, status=400)
    result = await unload_provider(provider)
    return web.json_response(result, status=200 if result.get("ok") else 404)


async def api_local_model_search(request: web.Request) -> web.Response:
    """GET /api/models/local/{provider}/search?q= — search a searchable provider's
    remote catalog (ollama's library). Empty for fixed-catalog providers."""
    from personalclaw.local_models.registry import get_provider, to_local_model

    provider_name = request.match_info["provider"]
    query = request.query.get("q", "").strip()
    provider = get_provider(provider_name)
    if provider is None:
        return web.json_response({"error": f"Unknown provider {provider_name!r}"}, status=404)
    try:
        raw = await provider.search_models(query)
    except Exception as exc:  # noqa: BLE001 — search is fail-soft
        logger.warning("model search failed for provider %r", provider_name, exc_info=True)
        return web.json_response({"models": [], "error": relayed_failure_copy(exc)})
    from personalclaw.local_models.registry import capabilities_for

    caps = capabilities_for(provider_name)
    return web.json_response(
        {"models": [to_local_model(m, capabilities=caps).to_dict() for m in raw]}
    )


def _mask(text: str) -> str:
    """Redact any secret-shaped run out of a message before it leaves the server.

    The health/selftest bodies echo provider messages and exception strings, which could in
    principle carry a token; the SEL redactor is the ONE definition of "safe to surface", so
    a masked message can never leak an HF token through an error string (Success Criterion 4).
    """
    try:
        from personalclaw.security import redact

        return redact(text or "")
    except Exception:  # noqa: BLE001 — redaction must never itself break a health/selftest reply
        return text or ""


def _sel_caller(request: web.Request) -> str:
    """A caller identity for the token set/clear SEL event (the session key, else a default)."""
    return request.headers.get("X-Session-Key") or "dashboard:hf-token"


# ── HF token cascade (LMMV §5) — status + set/clear, values never leave unmasked ──────


async def api_hf_token_status(request: web.Request) -> web.Response:
    """GET /api/models/hf-token/status — per-source ``{present, valid, username, masked, active}``.

    The three cascade sources (credential store → env → ``huggingface-cli`` file), each with a
    live-but-cached whoami verdict. The token VALUE never leaves the server — only the
    :func:`mask_token` preview (Success Criterion 4)."""
    from dataclasses import asdict

    from personalclaw.local_models import hf_token

    sources = await hf_token.token_status()
    return web.json_response({"sources": [asdict(s) for s in sources]})


async def api_hf_token_set(request: web.Request) -> web.Response:
    """PUT /api/models/hf-token — write the token to SOURCE 1 (the credential store).

    Body ``{token}``. The value goes to the credential store (never ``config.json``); the set
    is SEL-audited by name, never by value. Returns the refreshed per-source status."""
    from dataclasses import asdict

    from personalclaw.local_models import hf_token

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    token = str(body.get("token", "")) if isinstance(body, dict) else ""
    if not token.strip():
        return web.json_response({"error": "token is required"}, status=400)
    try:
        hf_token.set_token(token, caller=_sel_caller(request))
    except ValueError as exc:
        # Authored ValueError words survive; an unexpected class becomes generic copy — never
        # raw exception text on the wire (the shared failure-copy rail).
        return web.json_response({"error": relayed_failure_copy(exc)}, status=400)
    sources = await hf_token.token_status()
    return web.json_response({"sources": [asdict(s) for s in sources]})


async def api_hf_token_clear(request: web.Request) -> web.Response:
    """DELETE /api/models/hf-token — clear the managed token (SOURCE 1). SEL-audited by name."""
    from dataclasses import asdict

    from personalclaw.local_models import hf_token

    existed = hf_token.clear_token(caller=_sel_caller(request))
    sources = await hf_token.token_status()
    return web.json_response({"cleared": existed, "sources": [asdict(s) for s in sources]})


# ── Per-provider health + real-inference selftest (LMMV §6) ───────────────────────────


async def api_local_model_health(request: web.Request) -> web.Response:
    """GET /api/models/local/{provider}/health — NEVER 500s (LMMV §6).

    Uses the ABC ``availability_detail()`` (which itself never raises), so the reply is a typed
    body — ``{provider, ok, message, latency_ms}`` — even when the provider is unavailable. The
    message is masked, so a token can never ride out in it."""
    import time

    from personalclaw.local_models.registry import get_provider

    provider_name = request.match_info["provider"]
    provider = get_provider(provider_name)
    if provider is None:
        return web.json_response(
            {
                "provider": provider_name,
                "ok": False,
                "message": "unknown provider",
                "latency_ms": 0,
            },
            status=404,
        )
    t0 = time.monotonic()
    try:
        detail = getattr(provider, "availability_detail", None)
        if callable(detail):
            ok, message = await detail()
        else:
            # A duck-typed local provider (registered by capability, not subclass) may not carry
            # the ABC method — fall back to the bare availability bool so health still answers.
            ok = bool(await provider.is_available())
            message = "ready" if ok else "not available on this machine"
    except Exception as exc:  # noqa: BLE001 — defense in depth; availability_detail never raises
        # This branch only fires if a provider's availability_detail ITSELF raises (an internal
        # crash) — authored copy, not raw exception text on the wire (the failure-copy rail).
        ok, message = False, relayed_failure_copy(exc)
    return web.json_response(
        {
            "provider": provider_name,
            "ok": bool(ok),
            "message": _mask(str(message))[:300],
            "latency_ms": round((time.monotonic() - t0) * 1000),
        }
    )


def _selftest_timeout_s() -> float:
    """The per-capability selftest timeout from ``local_models.selftest_timeout_s`` (→ default)."""
    try:
        from personalclaw.config.loader import AppConfig

        return float(AppConfig.load().local_models.selftest_timeout_s)
    except Exception:  # noqa: BLE001 — config unreadable → a sane default, never a crash
        return 90.0


def _selftest_fixture_wav(path: str) -> None:
    """A deterministic ~0.5 s 16 kHz mono sine WAV — the bundled selftest fixture.

    Generated with the stdlib rather than committed as a binary (same choice as the doctor
    clone probe's ``_write_reference_clip``): the inference providers validate a real, decodable
    clip on disk, and generating one keeps the wheel free of audio blobs."""
    import math
    import struct
    import wave

    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = bytearray()
        for i in range(8000):
            frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 220 * i / 16000)))
        w.writeframes(bytes(frames))


def _ms(t0: float) -> int:
    import time

    return round((time.monotonic() - t0) * 1000)


async def _timed(coro, timeout: float) -> tuple[str, object, int]:
    """Await ``coro`` under a hard timeout. ``(outcome, value_or_exc, duration_ms)`` where
    outcome ∈ ``ok`` / ``timeout`` / ``error`` — never raises, so one capability's failure is
    isolated to its own row."""
    import asyncio
    import time

    t0 = time.monotonic()
    try:
        value = await asyncio.wait_for(coro, timeout=timeout)
        return ("ok", value, _ms(t0))
    except asyncio.TimeoutError:
        return ("timeout", None, _ms(t0))
    except Exception as exc:  # noqa: BLE001 — a broken runtime contract fails HERE, typed
        return ("error", exc, _ms(t0))


def _error_result(exc: object, ms: int) -> dict:
    """A failed-capability row carrying a TYPED reason. A provider that set ``typed_reason``
    (a sidecar crash) surfaces it verbatim; otherwise the exception class becomes the reason,
    so a pyannote-4-style ``AttributeError`` reads as ``selftest_error:AttributeError`` — a
    contract break failing on the API surface, not on file presence (Success Criterion 5)."""
    typed = getattr(exc, "typed_reason", "")
    reason = str(typed) or f"selftest_error:{type(exc).__name__}"
    # A selftest is an explicit user-clicked DIAGNOSTIC — the exact inference error IS the
    # thing the user asked to see (SC5: a contract break must fail visibly, not read as file
    # presence), so the masked exception text is surfaced deliberately here, unlike the
    # connectivity-toast surfaces the failure-copy rail guards. `_mask` keeps a token out of it.
    detail = _mask(str(exc))[:200] or type(exc).__name__
    return {"ok": False, "duration_ms": ms, "detail": detail, "reason": reason}


def _timeout_result(ms: int, what: str) -> dict:
    return {"ok": False, "duration_ms": ms, "detail": f"{what} timed out", "reason": "timeout"}


async def _selftest_stt(provider, model: str, timeout: float) -> dict:
    import os
    import tempfile

    fd, wav = tempfile.mkstemp(suffix=".wav", prefix="pc-selftest-")
    os.close(fd)
    try:
        _selftest_fixture_wav(wav)
        outcome, value, ms = await _timed(provider.transcribe(wav, model=model), timeout)
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass
    if outcome == "timeout":
        return _timeout_result(ms, "transcription")
    if outcome == "error":
        return _error_result(value, ms)
    ok = value is not None
    return {
        "ok": ok,
        "duration_ms": ms,
        "detail": "transcribed the fixture" if ok else "transcribe returned nothing",
        "reason": "" if ok else "stt_returned_nothing",
    }


async def _selftest_tts(provider, model: str, timeout: float) -> dict:
    import os
    import tempfile

    # Hand the provider a caller-owned path and clean it up in `finally` regardless of outcome
    # — a timeout cancels the coroutine mid-write, so an ok-only unlink (or output_path="")
    # orphans whatever the provider already wrote. Matches _selftest_stt/_selftest_diarization.
    fd, out = tempfile.mkstemp(suffix=".wav", prefix="pc-selftest-tts-")
    os.close(fd)
    outcome, value, ms = "error", None, 0
    try:
        outcome, value, ms = await _timed(
            provider.synthesize("Selftest.", voice=model, output_path=out), timeout
        )
    finally:
        # Unlink the path we handed over AND any different path the provider chose to return.
        for path in {out, value if isinstance(value, str) else ""}:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    if outcome == "timeout":
        return _timeout_result(ms, "synthesis")
    if outcome == "error":
        return _error_result(value, ms)
    ok = bool(value)
    return {
        "ok": ok,
        "duration_ms": ms,
        "detail": "synthesis returned audio" if ok else "synthesize returned nothing",
        "reason": "" if ok else "tts_returned_nothing",
    }


async def _selftest_embedding(provider, model: str, timeout: float) -> dict:
    outcome, value, ms = await _timed(
        provider.embed("The quick brown fox jumps over the lazy dog.", model=model), timeout
    )
    if outcome == "timeout":
        return _timeout_result(ms, "embedding")
    if outcome == "error":
        return _error_result(value, ms)
    dims = len(value) if isinstance(value, (list, tuple)) else 0
    ok = dims > 0
    return {
        "ok": ok,
        "duration_ms": ms,
        "detail": f"{dims} dims" if ok else "embed returned no vector",
        "reason": "" if ok else "embedding_returned_nothing",
    }


async def _selftest_diarization(provider, model: str, timeout: float) -> dict:
    import os
    import tempfile

    fd, wav = tempfile.mkstemp(suffix=".wav", prefix="pc-selftest-")
    os.close(fd)
    try:
        _selftest_fixture_wav(wav)
        outcome, value, ms = await _timed(provider.diarize(wav, model=model), timeout)
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass
    if outcome == "timeout":
        return _timeout_result(ms, "diarization")
    if outcome == "error":
        # THE Success-Criterion-5 case: a pyannote-4 itertracks/DiarizeOutput break raises here
        # and is reported as a typed reason, instead of the model passing because its file exists.
        return _error_result(value, ms)
    ok = value is not None
    turns = len(value) if isinstance(value, (list, tuple)) else 0
    return {
        "ok": ok,
        "duration_ms": ms,
        "detail": f"ran the pipeline ({turns} turn(s))" if ok else "diarize returned nothing",
        "reason": "" if ok else "diarization_returned_nothing",
    }


#: capability → (inference method name on the provider object, runner). A capability is tested
#: only when the provider OBJECT implements the method — so the answer is always for THIS
#: provider, never whatever happens to be bound (the provider-blind bug the
#: /api/model-providers selftest has). A capability that routes through a model-provider binding
#: (ollama chat) has no direct method here and is reported as not-directly-testable.
_SELFTEST_RUNNERS: dict[str, tuple[str, object]] = {
    "stt": ("transcribe", _selftest_stt),
    "tts": ("synthesize", _selftest_tts),
    "embedding": ("embed", _selftest_embedding),
    "diarization": ("diarize", _selftest_diarization),
}


async def _dispatch_selftest(provider, caps: list, model: str, timeout: float) -> dict[str, dict]:
    """Run a real inference for each capability the provider OBJECT can serve directly.

    Pure (no lock, no HTTP) so the dispatch + typed-reason logic is unit-testable on a fake
    provider. A capability whose method the object does not implement is skipped."""
    out: dict[str, dict] = {}
    for cap in caps:
        spec = _SELFTEST_RUNNERS.get(cap)
        if spec is None:
            continue
        method_name, runner = spec
        if not callable(getattr(provider, method_name, None)):
            continue
        out[cap] = await runner(provider, model, timeout)  # type: ignore[operator]
    return out


async def api_local_model_selftest(request: web.Request) -> web.Response:
    """POST /api/models/local/{provider}/selftest — a real per-capability inference (LMMV §6).

    Body ``{model?}``. Runs a tiny REAL inference for each capability the named provider serves
    directly (stt→transcribe, tts→synthesize, embedding→embed, diarization→diarize) using a
    generated fixture, so a broken runtime contract fails HERE on the API surface with a TYPED
    reason (Success Criterion 5), not merely on file presence.

    User-click only — it can page a model into RAM, so it is never fired by a background job —
    and serialized behind a ``single_flight`` lock so two clicks don't run two inferences at
    once. Each capability is hard-timeout-bounded by ``local_models.selftest_timeout_s``.

    A provider whose capabilities route through a model-provider binding rather than a local
    inference method (ollama chat) exposes no directly-testable method here; that is reported
    honestly rather than false-greened, and such a provider is tested via
    ``POST /api/model-providers/{name}/selftest``."""
    from personalclaw.concurrency import single_flight
    from personalclaw.local_models.registry import capabilities_for, get_provider

    provider_name = request.match_info["provider"]
    provider = get_provider(provider_name)
    if provider is None:
        return web.json_response({"error": f"Unknown provider {provider_name!r}"}, status=404)

    try:
        body = await request.json()
    except Exception:
        body = {}
    model = str(body.get("model", "")) if isinstance(body, dict) else ""
    caps = capabilities_for(provider_name)
    timeout = _selftest_timeout_s()

    with single_flight(f"local-model-selftest:{provider_name}") as acquired:
        if not acquired:
            return web.json_response(
                {
                    "error": "a selftest for this provider is already running",
                    "reason": "selftest_running",
                },
                status=409,
            )
        capabilities = await _dispatch_selftest(provider, caps, model, timeout)

    if not capabilities:
        return web.json_response(
            {
                "provider": provider_name,
                "capabilities": {},
                "detail": (
                    "no directly-testable capability — this provider serves its capabilities "
                    "through a model-provider binding; test it via "
                    "POST /api/model-providers/{name}/selftest"
                ),
            }
        )
    return web.json_response({"provider": provider_name, "capabilities": capabilities})


def register_model_download_routes(app: web.Application) -> None:
    """Register /api/models/downloads/* routes."""
    app.router.add_get("/api/models/downloads", api_model_downloads_list)
    app.router.add_post("/api/models/downloads", api_model_download_start)
    # Literal-path routes BEFORE the {id}-param routes (aiohttp matches in
    # registration order) so `cleanup-candidates`/`cleanup` never fall into `{id}`.
    app.router.add_get(
        "/api/models/downloads/cleanup-candidates", api_model_download_cleanup_candidates
    )
    app.router.add_post("/api/models/downloads/cleanup", api_model_download_cleanup)
    app.router.add_get("/api/models/downloads/{id}/stream", api_model_download_stream)
    app.router.add_delete("/api/models/downloads/{id}", api_model_download_cancel)
    # Sidecar isolation (LMMV §3.2): the resumable install job for a provider that
    # declares `execution: sidecar`. Same job registry + SSE hub as a weights download.
    app.router.add_post("/api/models/sidecar/{provider}/install", api_sidecar_install_start)
    app.router.add_get("/api/models/sidecar/{provider}/install/status", api_sidecar_install_status)
    app.router.add_delete("/api/models/sidecar/{provider}/install", api_sidecar_install_delete)
    # Residency / memory pressure (LMMV §7).
    app.router.add_get("/api/models/loaded", api_models_loaded)
    app.router.add_post("/api/models/unload", api_models_unload)
    # HF token cascade (LMMV §5): read status + set/clear source 1 (the credential store).
    # Literal `hf-token` prefix — never shadowed by the `local/{provider}` routes below.
    app.router.add_get("/api/models/hf-token/status", api_hf_token_status)
    app.router.add_put("/api/models/hf-token", api_hf_token_set)
    app.router.add_delete("/api/models/hf-token", api_hf_token_clear)
    # Generic per-provider local-model management (replaces the per-kind routes). The literal
    # `health`/`selftest` segments (LMMV §6) are registered BEFORE the `{model}` delete so the
    # literal path wins over the param one.
    app.router.add_get("/api/models/local/{provider}/search", api_local_model_search)
    app.router.add_get("/api/models/local/{provider}/health", api_local_model_health)
    app.router.add_post("/api/models/local/{provider}/selftest", api_local_model_selftest)
    app.router.add_delete("/api/models/local/{provider}/{model}", api_local_model_delete)
