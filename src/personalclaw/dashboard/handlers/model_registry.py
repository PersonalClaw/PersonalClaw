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
import functools
import json
import logging
from typing import Any

from aiohttp import web

from personalclaw.llm.catalog import FAILURE_DETAIL_CHARS
from personalclaw.providers.failure_copy import relayed_failure_copy
from personalclaw.providers.use_cases import (
    USE_CASES,
    VALID_USE_CASES,
    load_active_models,
    save_active_models,
)

logger = logging.getLogger(__name__)


def _sel_log(
    op: str, outcome: str, resources: str, request: "web.Request", error: str = ""
) -> None:
    """Record a model-binding mutation in the security event log (#45 — every
    state-changing provider op is auditable, mirroring the app-lifecycle handlers).
    Best-effort: never let an audit failure break the request."""
    try:
        from personalclaw.sel import sel as _s

        _s().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=op,
            outcome=outcome,
            source="models",
            resources=resources,
            error=error,
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
    """config.json ``providers[]`` with options RESOLVED: discovery authenticates with the
    stored key, not with the ``{{secret:…}}`` reference the document carries."""
    from personalclaw.config.loader import config_path
    from personalclaw.config.secret_refs import resolve_provider_records

    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return resolve_provider_records(data.get("providers", []))
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
        # The credential it names in the store is part of how it authenticates: without it,
        # discovery for an entry keyed that way asked the endpoint with no key at all.
        credential=p.get("credential") or None,
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
                    out.append(
                        {
                            "id": m.name,
                            "name": m.name,
                            "capabilities": ["image_gen"],
                            "description": m.description,
                            "downloaded": m.downloaded,
                            "provider": prov.name,
                            "provider_type": "image_gen",
                            "supports_edit": m.supports_edit,
                        }
                    )
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
                    out.append(
                        {
                            "id": m.name,
                            "name": m.name,
                            "capabilities": ["video_gen"],
                            "description": m.description,
                            "provider": prov.name,
                            "provider_type": "video_gen",
                        }
                    )
            except Exception:  # noqa: BLE001
                logger.debug("video_gen provider %r list_models failed", prov.name, exc_info=True)
        return out
    except Exception:
        logger.debug("video_gen discovery failed", exc_info=True)
        return []


_BYTES_PER_MB = 1024 * 1024


def _fit_probe() -> tuple[Any, int | None, bool]:
    """``(host, budget_bytes, hide_unrunnable)`` — the host facts, gathered ONCE.

    Every fit answer on this surface comes from :mod:`personalclaw.local_models.fit`, so the
    chip on a row and the download panel's own arithmetic cannot disagree. Runs on a worker
    thread (see the call site): the first probe may shell out to ``nvidia-smi`` /
    ``system_profiler`` and reading config touches the disk — neither belongs on the loop.
    """
    from personalclaw.local_models import fit as _fit

    host = _fit.host_capacity()
    budget = _fit.usable_memory_bytes(host, reserve_gb=_fit.configured_reserve_gb())
    return host, budget, _fit.hide_unrunnable_default()


def _step_down_name(
    rows: list[dict[str, Any]], family: str, verdict: str, budget_bytes: int | None
) -> str | None:
    """The variant a row that cannot load should step DOWN to, or None.

    Only a ``red`` row has anywhere to step down to, and the target is the largest sibling
    that still fits per :func:`fit.largest_that_fits` — offering a variant that loads instead
    of one that OOMs. None when the row fits, the host is unmeasured, or no sibling fits:
    with nothing that fits there is nothing honest to offer. The sibling's OWN size decides,
    not the family quote, because the step-down target is a concrete download.
    """
    if verdict != "red":
        return None
    from personalclaw.local_models import fit as _fit

    siblings = [r for r in rows if _fit.family_key(str(r.get("name", ""))) == family]
    target = _fit.largest_that_fits([float(r.get("size_mb") or 0) for r in siblings], budget_bytes)
    if target is None:
        return None
    for r in siblings:
        if float(r.get("size_mb") or 0) == target:
            return str(r.get("name", "")) or None
    return None


async def _hf_token_ready() -> bool | None:
    """Whether a gated download can proceed without pre-warning for a token (LMMV §5).

    Delegates to the HF-token cascade's server-side pre-warn policy. Best-effort: returns
    ``None`` when the cascade can't answer, so the caller leaves ``token_ready`` off the row
    and does not pre-warn on a transient failure (never a false nag)."""
    try:
        from personalclaw.local_models import hf_token

        return await hf_token.gated_prewarn_ok()
    except Exception:  # noqa: BLE001 — a pre-warn probe must never break the models list
        logger.debug("hf token pre-warn check failed", exc_info=True)
        return None


async def _listed(catalog: Any) -> list[Any]:
    """``catalog.list_models()``, raising the failure a fail-soft listing swallowed.

    A catalog that lists through core's fail-soft discovery answers ``[]`` for a refused key
    or an unreachable server, which a row would render as "lists no models". Run as its own
    task (``asyncio.gather``), so each listing captures only its own failures.
    """
    from personalclaw.llm.catalog import capture_discovery_failures

    with capture_discovery_failures() as swallowed:
        models = await catalog.list_models()
    refused = next((exc for exc in swallowed if exc.rejected_credential), None)
    if refused is not None:
        raise refused  # every model it lists would fail its first turn with the same refusal
    if swallowed and not models:
        raise swallowed[-1]
    return list(models)


async def api_models_available(request: web.Request) -> web.Response:
    """GET /api/models/available — discover models from all configured providers.

    Returns {providers: [{name, type, models: [{id, name, capabilities, ...}]}], fit: {...}}.
    Includes both config-based providers (Ollama, OpenAI, etc.) and bundled
    providers (sentence-transformers, faster-whisper, piper, image-gen).

    LOCAL rows carry a hardware-fit verdict (``fit`` / ``fit_reason`` / ``fit_need_mb`` /
    ``quoted_size_mb`` / ``fit_step_down``) and the response carries the one memory budget
    they were judged against. Config-provider and image/video-gen rows carry NO fit fields:
    they have no local weights, and an absent field is how the UI knows to draw no chip.

    Every configured instance's row carries its MEASURED ``connection``
    (``providers/connection.py``). An instance whose last check failed is not asked for its
    models again until a check passes — its row carries the check's sentence as ``error`` —
    which is what stopped a rejected key being re-sent to its vendor on every load. A row
    that could not be listed says so in ``error``; ``models: []`` alone means "lists none".
    """
    from personalclaw.llm.registry import canonical_provider_type
    from personalclaw.providers.connection import (
        FAILED,
        Connection,
        get_connection_board,
        settings_fingerprint,
    )

    providers_cfg = _get_providers_from_config()
    result: list[dict[str, Any]] = []
    board = get_connection_board()
    connections: dict[str, Connection] = {}
    for p in providers_cfg:
        pname = str(p.get("name", ""))
        connections[pname] = board.read(
            pname,
            settings_fingerprint(canonical_provider_type(p.get("type", "")), p.get("options")),
            functools.partial(_catalog_for_config_provider, p),
        )

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
        connection = connections[pname].to_wire()
        catalog = _catalog_for_config_provider(p)
        if catalog is None:
            result.append({"name": pname, "type": ptype, "models": [], "connection": connection})
            continue
        if connections[pname].state == FAILED:
            result.append(
                {
                    "name": pname,
                    "type": ptype,
                    "models": [],
                    "error": connections[pname].detail,
                    "connection": connection,
                }
            )
            continue
        tasks.append((pname, ptype, _listed(catalog)))

    if tasks:
        results = await asyncio.gather(*(t[2] for t in tasks), return_exceptions=True)
        for (pname, ptype, _), models_or_exc in zip(tasks, results):
            connection = connections[pname].to_wire()
            if isinstance(models_or_exc, BaseException):
                result.append(
                    {
                        "name": pname,
                        "type": ptype,
                        "models": [],
                        "error": relayed_failure_copy(models_or_exc)[:FAILURE_DETAIL_CHARS],
                        "connection": connection,
                    }
                )
            else:
                models = []
                for mi in models_or_exc:
                    d = mi.to_dict()
                    d["provider"] = pname
                    d["provider_type"] = ptype
                    models.append(d)
                result.append(
                    {"name": pname, "type": ptype, "models": models, "connection": connection}
                )

    # Local downloadable providers — ONE uniform source: every provider that registered
    # into the local-model registry (faster-whisper, piper, sentence-transformers, the
    # diarization backends, ollama, …). Each card lists the provider's full catalog
    # (downloaded AND downloadable) with per-model capabilities, so the same surface
    # drives binding, download, and runtime. No per-kind branching, no hardcoded names.
    from personalclaw.local_models import fit as _fit
    from personalclaw.local_models.registry import list_catalog as _local_catalog
    from personalclaw.local_models.registry import registered as _local_registered

    # ONE host probe for the whole response — not one per model. The budget every row is
    # judged against is the same number the response reports, so a chip and the panel's
    # header can never quote different capacities.
    host, budget_bytes, hide_unrunnable = await asyncio.to_thread(_fit_probe)

    # Gated pre-warn (LMMV §4.3/§5): a gated model row carries a server-side ``token_ready``
    # computed from the HF-token cascade, so the UI can warn BEFORE the user clicks Download
    # when no valid token is present — instead of letting the download fail. Computed at most
    # once per response (only when a gated row is actually present) and whoami-cached, so a
    # frequent list render never hammers HuggingFace. Best-effort: if the cascade can't answer,
    # ``token_ready`` stays absent and the UI simply doesn't pre-warn (never a false nag).
    token_ready: bool | None = None

    # Key each card by the REGISTRY key (the app name) — matches the Providers UI's ext
    # name AND the ``provider:model`` binding refs — not the provider's internal .name.
    for pkey, prov in _local_registered():
        # A config-backed instance (an Ollama entry) whose last check failed is not asked
        # again — the check's sentence is the row's error. Any other listing failure is the
        # row's error too: "No downloadable models listed" is not what an unreachable
        # server's card should say.
        instance = connections.get(pkey)
        listing_error = ""
        if instance is not None and instance.state == FAILED:
            rows: list[dict[str, Any]] = []
            listing_error = instance.detail
        else:
            try:
                rows = [lm.to_dict() for lm in await _local_catalog(prov)]
            except Exception as exc:  # noqa: BLE001 — one provider's failure is its row's error
                logger.debug("local catalog failed for %s", pkey, exc_info=True)
                rows, listing_error = [], relayed_failure_copy(exc)[:FAILURE_DETAIL_CHARS]
        # A family QUOTES its median variant, never its smallest: quoting the smallest
        # promises a fit the user will not get from the variant they actually pick. A
        # colonless name is a family of one, so its quote is its own size and nothing
        # changes for it.
        sizes_by_family: dict[str, list[float]] = {}
        for d in rows:
            sizes_by_family.setdefault(_fit.family_key(str(d.get("name", ""))), []).append(
                float(d.get("size_mb") or 0)
            )
        models = []
        for d in rows:
            family = _fit.family_key(str(d.get("name", "")))
            quoted = _fit.median_variant_size_mb(sizes_by_family.get(family, []))
            # The VERDICT is judged against the weights this row actually pulls — its own
            # size. Judging every variant by the family quote would paint the family's
            # largest variant with the median's verdict, i.e. promise a fit that OOMs. A row
            # that publishes NO size (a family entry in a searchable catalog) falls back to
            # the family quote, which is the median and never the smallest for exactly the
            # reason above; with neither, ``fit_verdict`` answers "unknown".
            own_size_mb = float(d.get("size_mb") or 0)
            assessment = _fit.fit_verdict(
                size_mb=own_size_mb or quoted,
                context_tokens=int(d.get("context_tokens") or 0),
                budget_bytes=budget_bytes,
            )
            d["provider"] = pkey
            d["provider_type"] = pkey
            d["quoted_size_mb"] = round(quoted, 1)
            d["fit"] = assessment.verdict
            d["fit_reason"] = assessment.reason
            d["fit_need_mb"] = round(assessment.need_bytes / _BYTES_PER_MB, 1)
            d["fit_step_down"] = _step_down_name(rows, family, assessment.verdict, budget_bytes)
            if d.get("gated"):
                if token_ready is None:
                    token_ready = await _hf_token_ready()
                d["token_ready"] = token_ready
            models.append(d)
        row: dict[str, Any] = {
            "name": pkey,
            "displayName": getattr(prov, "display_name", pkey),
            "type": pkey,
            "local": True,  # a locally-downloadable provider → gets a download-management card
            "searchable": bool(getattr(prov, "searchable", False)),
            "models": models,
        }
        if listing_error:
            row["error"] = listing_error
        if instance is not None:
            row["connection"] = instance.to_wire()
        result.append(row)

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

    return web.json_response(
        {
            "providers": result,
            # ``budget_mb`` is null — never 0 — on a host whose memory could not be
            # measured: "unknown" and "nothing fits" are different answers and only one of
            # them should hide models from the user.
            "fit": {
                "budget_mb": (
                    None if budget_bytes is None else round(budget_bytes / _BYTES_PER_MB)
                ),
                "total_ram_mb": round(host.total_ram_bytes / _BYTES_PER_MB),
                "unified_memory": bool(host.unified_memory),
                "gpu_model": host.gpu_model,
                "measured": bool(host.memory_measured),
                "hide_unrunnable": bool(hide_unrunnable),
            },
        }
    )


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
    """PUT /api/models/active/{use_case} — set the active model CHAIN for a use-case.

    Body: {models: ["provider_name:model_id", ...]} — an ordered fallback chain
    for EVERY use case (MODEL-USE-CASES-V2): position 0 is the default, later
    entries are fallbacks resolution walks when an earlier provider's breaker is
    open or its build fails. Order is preserved verbatim.
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

    # An OMITTED key is not an empty chain. `body.get("models", [])` treated a body
    # that never mentioned `models` — e.g. a caller guessing `{"providers": [...]}` —
    # as "clear this binding", unset the use-case, and still answered ok:true. The
    # caller saw success while its binding was wiped. Clearing must be explicit, so
    # `{"models": []}` still clears and anything else is a 400 naming the key.
    if "models" not in body:
        _sel_log(
            "models.active_set",
            "error",
            use_case,
            request,
            error=f"body has no 'models' key (got: {sorted(body)})",
        )
        return web.json_response(
            {
                "error": {
                    "code": "models_required",
                    "message": (
                        "Body must include a 'models' key holding an ordered list of "
                        '"provider:model_id" refs. To clear this use-case\'s binding, '
                        'send {"models": []} explicitly.'
                    ),
                    "received_keys": sorted(str(k) for k in body),
                }
            },
            status=400,
        )

    models = body.get("models", [])
    if not isinstance(models, list):
        return web.json_response({"error": "models must be a list"}, status=400)

    if len(models) > 20:
        return web.json_response(
            {"error": "a fallback chain may have at most 20 entries"},
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
        if (
            known is not None
        ):  # None = config unreadable → skip validation (don't block on I/O error)
            for m in models:
                parsed = split_ref(str(m))
                if parsed and parsed[0] not in known:
                    _sel_log(
                        "models.active_set",
                        "error",
                        f"{use_case}:{m}",
                        request,
                        error=f"unknown provider {parsed[0]!r}",
                    )
                    return web.json_response(
                        {
                            "error": f"Unknown provider {parsed[0]!r} in model ref {m!r}. "
                            f"Install/configure it first (Providers), or pick a known provider. "
                            f"Known: {sorted(known)}"
                        },
                        status=400,
                    )
    except Exception:
        logger.debug("active-model provider validation skipped", exc_info=True)

    active = load_active_models()
    active[use_case] = [str(m) for m in models]
    save_active_models(active)

    # Audit the binding change (#45): repointing a use-case to a different model is
    # a security-relevant state change — record who set what.
    _sel_log(
        "models.active_set",
        "ok",
        f"{use_case}={','.join(active[use_case]) or '(cleared)'}",
        request,
    )
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
            result.append(
                {
                    "name": model_id if not provider_name else model_ref,
                    "model_name": model_id,
                    "model_id": model_id,
                    "provider": provider_name,
                    "description": model_id,
                }
            )
        return web.json_response(result)

    # Fallback: no active selection — discover chat-capable models from every
    # configured provider via its registered ModelCatalog (generic, no per-type
    # branching). Each provider's list runs concurrently; a provider with no
    # catalog contributes nothing.
    from personalclaw.llm.capabilities import Capability
    from personalclaw.llm.registry import get_default_registry

    registry = get_default_registry()
    live = {e.name: e for e in registry.list_entries()}

    def _cannot_serve(pname: str) -> bool:
        # The registry's readiness answer — the same one onboarding and the resolver read — so
        # this list never offers a model the next turn would refuse (a provider whose model is
        # not downloaded yet). A row with no live entry is left to its catalog, as before.
        entry = live.get(pname)
        return entry is not None and registry.not_ready(entry, implicit=False) is not None

    config_rows = _get_providers_from_config()
    config_names = {str(p.get("name", "")) for p in config_rows}
    providers_cfg = [p for p in config_rows if not _cannot_serve(p.get("name", ""))]
    all_models: list[dict[str, Any]] = []

    def _add(pname: str, mid: str) -> None:
        all_models.append(
            {
                "name": f"{pname}/{mid}" if pname else mid,
                "model_name": mid,
                "model_id": mid,
                "provider": pname,
                "description": mid,
            }
        )

    from personalclaw.llm.registry import canonical_provider_type
    from personalclaw.providers.connection import (
        FAILED,
        get_connection_board,
        settings_fingerprint,
    )

    board = get_connection_board()
    tasks = []  # (pname, has_pinned_model, pinned_model, coro)
    for p in providers_cfg:
        pname = p.get("name", "")
        # An instance whose last check failed (unreachable, or its key rejected) offers
        # nothing: a model it would list is a model whose first turn fails.
        connection = board.read(
            pname,
            settings_fingerprint(canonical_provider_type(p.get("type", "")), p.get("options")),
            functools.partial(_catalog_for_config_provider, p),
        )
        if connection.state == FAILED:
            continue
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
            if isinstance(models_or_exc, BaseException) or not models_or_exc:
                # Discovery failed / empty — fall back to the pinned model id.
                if pinned:
                    _add(pname, pinned)
                continue
            for mi in models_or_exc:
                if "chat" in (mi.capabilities or []):
                    _add(pname, mi.id)

    # Entries an APP registers itself rather than a config.json row — the bundled floor model —
    # contribute their pinned model when they can serve. They are what a fresh install actually
    # chats with, and the list above only walks config.json, so before this the one model that
    # answered was the one model no picker offered ("Nothing came back"). A floor counts even
    # when a stale config.json row shares its name: the app's entry is the live one (config rows
    # are never floors), and the row above contributed nothing for it.
    listed = {m["provider"] for m in all_models}
    for entry in live.values():
        if entry.name in listed or entry.type == "acp_agent" or not entry.model:
            continue
        if entry.name in config_names and not getattr(entry, "floor", False):
            continue
        caps = entry.declared_capabilities
        if not caps:
            try:
                caps = registry.capability_of(entry.type).capabilities
            except Exception:
                caps = frozenset()
        if Capability.CHAT not in caps or _cannot_serve(entry.name):
            continue
        _add(entry.name, entry.model)

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
