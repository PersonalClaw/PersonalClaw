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

from aiohttp import web


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
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    provider = str(body.get("provider", ""))
    model = str(body.get("model", ""))
    job, error = _registry(request).start(provider, model)
    if error is not None:
        return web.json_response({"error": error}, status=400)
    return web.json_response(job.to_dict(), status=202)


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
        return web.json_response({"error": str(exc)[:200]}, status=500)
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


async def api_hf_token_status(request: web.Request) -> web.Response:
    """GET /api/models/hf-token/status — per-source HuggingFace token status (LMMV §5).

    Returns ``{sources: [{source, present, valid, username?, masked}], active_source,
    username}`` — the three-source cascade (credential store → env → HF CLI file), each
    whoami-validated. Token VALUES never leave the server: a source carries a masked
    preview only (Success Criterion 4)."""
    from personalclaw.local_models import hf_token

    return web.json_response(await hf_token.hf_token_status())


async def api_hf_token_set(request: web.Request) -> web.Response:
    """PUT /api/models/hf-token — set (body ``{token}``) or clear (``{token: ""}``) the
    credential-store HuggingFace token (source 1). SEL-audited; the value is never
    echoed back — the response re-reads the (masked) cascade status."""
    from personalclaw.local_models import hf_token

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict) or "token" not in body:
        return web.json_response({"error": "body must include a 'token' key"}, status=400)
    token = str(body.get("token") or "").strip()
    if token:
        hf_token.set_hf_token(token)
    else:
        hf_token.clear_hf_token()
    return web.json_response(await hf_token.hf_token_status())


async def api_local_model_health(request: web.Request) -> web.Response:
    """GET /api/models/local/{provider}/health — cheap per-provider health (LMMV §6).

    NEVER 500s: an exception in the provider's availability check becomes
    ``{ok: false, message}`` (built on the ABC ``availability_detail()``). Returns
    ``{provider, ok, message, latency_ms}``."""
    from personalclaw.local_models.selftest import provider_health

    return web.json_response(await provider_health(request.match_info["provider"]))


async def api_local_model_selftest(request: web.Request) -> web.Response:
    """POST /api/models/local/{provider}/selftest — real per-capability inference (§6).

    Body ``{model?}``. Runs a tiny REAL inference for each capability the provider
    serves (transcribe a bundled fixture, embed a sentence, …), ``single_flight``-
    serialized and per-capability timeout-bounded, returning typed reasons — so a
    pyannote-4-style runtime-contract break fails on the API, not on file presence
    (Success Criterion 5). User-click only."""
    from personalclaw.local_models.selftest import provider_selftest

    model: str | None = None
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("model"):
            model = str(body["model"])
    except Exception:
        model = None
    return web.json_response(await provider_selftest(request.match_info["provider"], model))


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
        return web.json_response({"models": [], "error": str(exc)[:200]})
    from personalclaw.local_models.registry import capabilities_for

    caps = capabilities_for(provider_name)
    return web.json_response(
        {"models": [to_local_model(m, capabilities=caps).to_dict() for m in raw]}
    )


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
    # HuggingFace token cascade status + set/clear (LMMV §5). Literal paths, registered
    # before the {provider} routes so they never fall into a param match.
    app.router.add_get("/api/models/hf-token/status", api_hf_token_status)
    app.router.add_put("/api/models/hf-token", api_hf_token_set)
    # Generic per-provider local-model management (replaces the per-kind routes).
    # Literal-suffix routes (health/selftest/search) precede the {model} catch-all so a
    # model literally named "health" can't shadow them; method also disambiguates.
    app.router.add_get("/api/models/local/{provider}/health", api_local_model_health)
    app.router.add_post("/api/models/local/{provider}/selftest", api_local_model_selftest)
    app.router.add_get("/api/models/local/{provider}/search", api_local_model_search)
    app.router.add_delete("/api/models/local/{provider}/{model}", api_local_model_delete)
