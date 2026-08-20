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
    # Sidecar isolation (LMMV §3.2): the resumable install job for a provider that
    # declares `execution: sidecar`. Same job registry + SSE hub as a weights download.
    app.router.add_post("/api/models/sidecar/{provider}/install", api_sidecar_install_start)
    app.router.add_get("/api/models/sidecar/{provider}/install/status", api_sidecar_install_status)
    app.router.add_delete("/api/models/sidecar/{provider}/install", api_sidecar_install_delete)
    # Residency / memory pressure (LMMV §7).
    app.router.add_get("/api/models/loaded", api_models_loaded)
    app.router.add_post("/api/models/unload", api_models_unload)
    # Generic per-provider local-model management (replaces the per-kind routes).
    app.router.add_get("/api/models/local/{provider}/search", api_local_model_search)
    app.router.add_delete("/api/models/local/{provider}/{model}", api_local_model_delete)
