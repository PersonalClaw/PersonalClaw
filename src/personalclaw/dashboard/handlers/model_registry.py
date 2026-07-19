"""Unified model discovery and active-model assignment API.

Endpoints:
    GET    /api/models/available           — discover models from all configured providers
    GET    /api/models/active              — active models per use-case
    PUT    /api/models/active/{use_case}   — set active model(s) for a use-case
    GET    /api/models/chat                — active chat models (for dropdown use)

Local-model download / delete / search is served generically by the local-model routes
(``/api/models/downloads`` + ``/api/models/local/{provider}/…``), driven by the one
local-model registry — no per-kind catalog/delete/recommendation routes live here.
"""

import asyncio
import json
import logging
from typing import Any

from aiohttp import web

from personalclaw.providers.use_cases import (
    MULTI_ACTIVE_USE_CASES,
    USE_CASES,
    VALID_USE_CASES,
    load_active_models,
    save_active_models,
)

logger = logging.getLogger(__name__)


def _sel_log(op: str, outcome: str, resources: str, request: "web.Request", error: str = "") -> None:
    """Record a model-binding mutation in the security event log (#45 — every
    state-changing provider op is auditable, mirroring the app-lifecycle handlers).
    Best-effort: never let an audit failure break the request."""
    try:
        from personalclaw.sel import sel as _s
        _s().log_api_access(
            caller=request.get("user", "dashboard"), operation=op, outcome=outcome,
            source="models", resources=resources, error=error,
        )
    except Exception:
        pass


# NOTE: model-provider discovery (ollama /api/tags, OpenAI /v1/models, the
# Anthropic curated list, and Bedrock's boto3 control-plane query + fallback
# catalog) used to live here as a per-type switch. It now lives on each
# provider app's ModelCatalog (every model app's create_catalog — ollama's
# included, in apps/ollama-models), resolved generically via
# registry.build_catalog(). The bundled embedding/stt/tts + image-gen discovery
# below is NOT model-provider discovery and stays.


def _get_providers_from_config() -> list[dict[str, Any]]:
    from personalclaw.config.loader import config_path
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data.get("providers", [])
    except Exception:
        return []


def _catalog_for_config_provider(p: dict[str, Any]):
    """Build a ModelCatalog for a raw config.json provider dict, or None.

    Model discovery routes every provider through its registered catalog (the
    generic seam) instead of a per-type switch. The config type may be a branded
    OpenAI/Anthropic-compatible alias (together/groq/…); canonicalize it to the
    base registry type the catalog is keyed on. Returns None when no catalog is
    registered for the type (its app not loaded) — the caller treats that as "no
    models", never an error."""
    from personalclaw.llm.registry import (
        ProviderEntry,
        canonical_provider_type,
        get_default_registry,
    )

    ptype = canonical_provider_type(p.get("type", ""))
    entry = ProviderEntry(
        name=p.get("name", ""),
        type=ptype,
        model=p.get("model", ""),
        options=dict(p.get("options") or {}),
    )
    return get_default_registry().build_catalog(entry)


async def _discover_image_gen_models() -> list[dict[str, Any]]:
    """Discover image-generation models from the image_gen registry.

    The image_gen providers (the OpenAI-Images adapter built per OpenAI-family
    config provider + any bespoke bundle like FAL) own their own model catalogs
    that the chat/embedding discovery above doesn't see. Surface them here, tagged
    image_gen, so they appear in the Settings -> Models 'Image · Generation' row.
    Each model id is namespaced ``provider:model`` so the active-binding ref is
    exactly what the registry resolves.
    """
    try:
        from personalclaw.image_gen import registry as ig

        ig._ensure_registered()
        out: list[dict[str, Any]] = []
        for prov in ig.list_providers():
            try:
                if not await prov.is_available():
                    continue
                for m in await prov.list_models():
                    # Bare model id — the FE prepends ``provider:`` to build the
                    # binding ref (matching stt/tts/chat), so DON'T namespace here.
                    out.append({
                        "id": m.name,
                        "name": m.name,
                        "capabilities": ["image_gen"],
                        "description": m.description,
                        "downloaded": m.downloaded,
                        "provider": prov.name,
                        "provider_type": "image_gen",
                        "supports_edit": m.supports_edit,
                    })
            except Exception:  # noqa: BLE001 — one bad provider shouldn't drop the rest
                logger.debug("image_gen provider %r list_models failed", prov.name, exc_info=True)
        return out
    except Exception:
        logger.debug("image_gen discovery failed", exc_info=True)
        return []


async def _discover_video_gen_models() -> list[dict[str, Any]]:
    """Discover video-generation models from the video_gen registry."""
    try:
        from personalclaw.video_gen import registry as vg

        out: list[dict[str, Any]] = []
        for prov in vg.list_providers():
            try:
                if not await prov.is_available():
                    continue
                for m in await prov.list_models():
                    out.append({
                        "id": m.name,
                        "name": m.name,
                        "capabilities": ["video_gen"],
                        "description": m.description,
                        "provider": prov.name,
                        "provider_type": "video_gen",
                    })
            except Exception:  # noqa: BLE001
                logger.debug("video_gen provider %r list_models failed", prov.name, exc_info=True)
        return out
    except Exception:
        logger.debug("video_gen discovery failed", exc_info=True)
        return []


async def api_models_available(request: web.Request) -> web.Response:
    """GET /api/models/available — discover models from all configured providers.

    Returns {providers: [{name, type, models: [{id, name, capabilities, ...}]}]}.
    Includes both config-based providers (Ollama, OpenAI, etc.) and bundled
    providers (sentence-transformers, faster-whisper, piper, image-gen).
    """
    providers_cfg = _get_providers_from_config()
    result: list[dict[str, Any]] = []

    # Providers that ALSO surface through the local-model registry below (they own
    # local download/management — ollama) are rendered ONCE there, with a download card
    # + searchable catalog. Skip them in the discovery loop to avoid a duplicate card.
    from personalclaw.local_models.registry import get_provider as _local_get

    # Every config provider discovers through its registered ModelCatalog — no
    # per-type branching in core. A provider whose catalog isn't registered (its
    # app not loaded) or that returns nothing surfaces an empty list, never a 500.
    tasks = []  # (pname, ptype, coro)
    for p in providers_cfg:
        ptype = p.get("type", "")
        pname = p.get("name", "")
        if _local_get(pname) is not None:
            continue  # rendered by the local-model loop below (unified download card)
        catalog = _catalog_for_config_provider(p)
        if catalog is None:
            result.append({"name": pname, "type": ptype, "models": []})
            continue
        tasks.append((pname, ptype, catalog.list_models()))

    if tasks:
        results = await asyncio.gather(*(t[2] for t in tasks), return_exceptions=True)
        for (pname, ptype, _), models_or_exc in zip(tasks, results):
            if isinstance(models_or_exc, Exception):
                result.append({"name": pname, "type": ptype, "models": [], "error": str(models_or_exc)[:200]})
            else:
                models = []
                for mi in models_or_exc:
                    d = mi.to_dict()
                    d["provider"] = pname
                    d["provider_type"] = ptype
                    models.append(d)
                result.append({"name": pname, "type": ptype, "models": models})

    # Local downloadable providers — ONE uniform source: every provider that registered
    # into the local-model registry (faster-whisper, piper, sentence-transformers, the
    # diarization backends, ollama, …). Each card lists the provider's full catalog
    # (downloaded AND downloadable) with per-model capabilities, so the same surface
    # drives binding, download, and runtime. No per-kind branching, no hardcoded names.
    from personalclaw.local_models.registry import catalog_for as _local_catalog
    from personalclaw.local_models.registry import registered as _local_registered

    # Key each card by the REGISTRY key (the app name) — matches the Providers UI's ext
    # name AND the ``provider:model`` binding refs — not the provider's internal .name.
    for pkey, prov in _local_registered():
        models = []
        for lm in await _local_catalog(prov):
            d = lm.to_dict()
            d["provider"] = pkey
            d["provider_type"] = pkey
            models.append(d)
        result.append({
            "name": pkey,
            "displayName": getattr(prov, "display_name", pkey),
            "type": pkey,
            "local": True,  # a locally-downloadable provider → gets a download-management card
            "searchable": bool(getattr(prov, "searchable", False)),
            "models": models,
        })

    # Image-generation models from the image_gen registry (OpenAI-Images adapter +
    # bespoke bundles like FAL). Grouped per provider so each shows under its own
    # card. ``id`` is already ``provider:model`` (the binding ref).
    image_gen_models = await _discover_image_gen_models()
    if image_gen_models:
        by_provider: dict[str, list[dict[str, Any]]] = {}
        for m in image_gen_models:
            by_provider.setdefault(m["provider"], []).append(m)
        for pname, models in by_provider.items():
            result.append({"name": pname, "type": "image_gen", "models": models})

    # Video-generation models from the video_gen registry (FAL video, etc.).
    video_gen_models = await _discover_video_gen_models()
    if video_gen_models:
        by_provider_v: dict[str, list[dict[str, Any]]] = {}
        for m in video_gen_models:
            by_provider_v.setdefault(m["provider"], []).append(m)
        for pname, models in by_provider_v.items():
            result.append({"name": pname, "type": "video_gen", "models": models})

    return web.json_response({"providers": result})


async def api_models_active(request: web.Request) -> web.Response:
    """GET /api/models/active — active models per use-case.

    Returns {use_cases: {chat: [model_ids...], embedding: [model_id], ...}}.
    """
    active = load_active_models()
    normalized: dict[str, list[str]] = {}
    for uc in USE_CASES:
        normalized[uc] = active.get(uc, [])
    return web.json_response({"use_cases": normalized})


async def api_models_active_set(request: web.Request) -> web.Response:
    """PUT /api/models/active/{use_case} — set active model(s) for a use-case.

    Body: {models: ["provider_name:model_id", ...]} for multi-active use-cases.
    Body: {models: ["provider_name:model_id"]} for single-active use-cases.
    """
    use_case = request.match_info["use_case"]
    if use_case not in VALID_USE_CASES:
        return web.json_response(
            {"error": f"Invalid use case: {use_case!r}; valid: {list(USE_CASES)}"},
            status=400,
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    models = body.get("models", [])
    if not isinstance(models, list):
        return web.json_response({"error": "models must be a list"}, status=400)

    if use_case not in MULTI_ACTIVE_USE_CASES and len(models) > 1:
        return web.json_response(
            {"error": f"Use case {use_case!r} only supports one active model"},
            status=400,
        )

    # Reject a ref whose PROVIDER prefix names no known provider — fail-fast at
    # set-time rather than silently stranding the use-case on a dead binding (the
    # stale-pin bug class; use-time resolution already blocks with a clear error,
    # but binding it at all is a footgun). Conservative on purpose: we validate the
    # provider PREFIX against the authoritative name set (config.json providers +
    # bundled + media), NOT that the model id is in the discovered catalog — a real
    # provider that's installed but slow to enumerate models must NOT be rejected.
    # A bare id (no "provider:" prefix) is left alone (some use-cases store bare ids).
    try:
        from personalclaw.providers.use_cases import _known_provider_names, split_ref

        known = _known_provider_names()
        if known is not None:  # None = config unreadable → skip validation (don't block on I/O error)
            for m in models:
                parsed = split_ref(str(m))
                if parsed and parsed[0] not in known:
                    _sel_log("models.active_set", "error", f"{use_case}:{m}", request,
                             error=f"unknown provider {parsed[0]!r}")
                    return web.json_response(
                        {"error": f"Unknown provider {parsed[0]!r} in model ref {m!r}. "
                         f"Install/configure it first (Providers), or pick a known provider. "
                         f"Known: {sorted(known)}"},
                        status=400,
                    )
    except Exception:
        logger.debug("active-model provider validation skipped", exc_info=True)

    active = load_active_models()
    active[use_case] = [str(m) for m in models]
    save_active_models(active)

    # Audit the binding change (#45): repointing a use-case to a different model is
    # a security-relevant state change — record who set what.
    _sel_log("models.active_set", "ok", f"{use_case}={','.join(active[use_case]) or '(cleared)'}", request)
    return web.json_response({"ok": True, "use_case": use_case, "models": active[use_case]})


async def api_models_chat(request: web.Request) -> web.Response:
    """GET /api/models/chat — chat models for dropdowns (the one model list).

    Returns active chat models from Settings → Models when configured, else
    falls back to discovering all chat-capable models from every provider.

    Each entry carries BOTH ``model_name`` and ``model_id`` (the same bare id)
    plus ``name``/``provider``/``description`` — a superset shape so every
    consumer (composer model pill reads model_name; agent/chat pickers read
    name/model_id) works off one endpoint.
    """
    active = load_active_models()
    chat_active = active.get("chat", [])

    if chat_active:
        result = []
        for model_ref in chat_active:
            if ":" in model_ref:
                provider_name, model_id = model_ref.split(":", 1)
            else:
                provider_name, model_id = "", model_ref
            result.append({
                "name": model_id if not provider_name else model_ref,
                "model_name": model_id,
                "model_id": model_id,
                "provider": provider_name,
                "description": model_id,
            })
        return web.json_response(result)

    # Fallback: no active selection — discover chat-capable models from every
    # configured provider via its registered ModelCatalog (generic, no per-type
    # branching). Each provider's list runs concurrently; a provider with no
    # catalog contributes nothing.
    providers_cfg = _get_providers_from_config()
    all_models: list[dict[str, Any]] = []

    def _add(pname: str, mid: str) -> None:
        all_models.append({
            "name": f"{pname}/{mid}" if pname else mid,
            "model_name": mid,
            "model_id": mid,
            "provider": pname,
            "description": mid,
        })

    tasks = []  # (pname, has_pinned_model, pinned_model, coro)
    for p in providers_cfg:
        pname = p.get("name", "")
        catalog = _catalog_for_config_provider(p)
        if catalog is None:
            # No discovery available — surface a pinned model if the entry has one.
            if p.get("model"):
                _add(pname, p["model"])
            continue
        tasks.append((pname, p.get("model", ""), catalog.list_models()))

    if tasks:
        results = await asyncio.gather(*(t[2] for t in tasks), return_exceptions=True)
        for (pname, pinned, _), models_or_exc in zip(tasks, results):
            if isinstance(models_or_exc, Exception) or not models_or_exc:
                # Discovery failed / empty — fall back to the pinned model id.
                if pinned:
                    _add(pname, pinned)
                continue
            for mi in models_or_exc:
                if "chat" in (mi.capabilities or []):
                    _add(pname, mi.id)

    return web.json_response(all_models)


def register_model_registry_routes(app: web.Application) -> None:
    """Register model registry routes.

    Local-model download/delete/search is served generically by the local-model
    routes (``/api/models/downloads`` + ``/api/models/local/{provider}/…``); no
    per-kind catalog/delete routes live here anymore."""
    app.router.add_get("/api/models/available", api_models_available)
    app.router.add_get("/api/models/active", api_models_active)
    app.router.add_put("/api/models/active/{use_case}", api_models_active_set)
    app.router.add_get("/api/models/chat", api_models_chat)
