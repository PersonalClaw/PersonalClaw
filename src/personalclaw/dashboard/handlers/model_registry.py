"""Unified model discovery and active-model assignment API.

Endpoints:
    GET    /api/models/available           — discover models from all configured providers
    GET    /api/models/active              — active models per use-case
    PUT    /api/models/active/{use_case}   — set active model(s) for a use-case
    GET    /api/models/chat                — active chat models (for dropdown use)
    POST   /api/models/test                — Test one model for one use case (one small real call)

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

from personalclaw.http_errors import json_error
from personalclaw.llm.catalog import FAILURE_DETAIL_CHARS
from personalclaw.providers.failure_copy import relayed_failure_copy
from personalclaw.providers.use_cases import (
    USE_CASES,
    VALID_USE_CASES,
    load_active_models,
    names_model,
    parent_capability,
    save_active_models,
    split_ref,
)
from personalclaw.stale_write import refusal_outcome, revision_of, stale_write_refusal

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


def _names_no_model(entry: object) -> str:
    """The sentence ``PUT /api/models/active`` refuses a chain entry that names no model with."""
    if not isinstance(entry, str):
        return (
            'Each model in the chain is a "provider:model" string that names a model, and one '
            f"is {type(entry).__name__}."
        )
    parsed = split_ref(entry)
    if parsed is None or not parsed[0].strip():
        return (
            'One of the models in the chain is empty. Name it as "provider:model", or choose '
            "one in Settings → Models."
        )
    provider = parsed[0]
    return (
        f"“{entry}” names the provider {provider} and no model. Name one as "
        f"“{provider}:<model id>”, or choose one of its models in Settings → Models."
    )


#: The longest a binding waits on its provider's model list before it is stored unchecked.
_BIND_CHECK_TIMEOUT_SECS = 8.0

#: Each job a model is listed for, in the words a sentence about it uses.
_JOB_WORDS = {
    "chat": "chat",
    "image_modality": "reading images",
    "audio_modality": "understanding audio",
    "video_modality": "understanding video",
    "embedding": "embedding",
    "stt": "speech-to-text",
    "tts": "text-to-speech",
    "diarization": "telling speakers apart",
    "image_gen": "making images",
    "audio_gen": "making audio",
    "video_gen": "making video",
}


async def _listed_jobs(refs: list[str]) -> dict[tuple[str, str], list[str]]:
    """What each ``provider:model`` ref's own provider lists the model for, where it says so.

    A ref is in the answer, keyed ``(provider, model)``, only when its provider is a configured
    instance whose catalog lists the model. One whose provider could not be asked (no catalog,
    its last connection check failed, the listing failed or outran
    :data:`_BIND_CHECK_TIMEOUT_SECS`), or that does not list the model, says nothing and is left
    out: a server slow to list what it serves, or a model pulled a moment ago, is no reason to
    refuse it. Each provider is listed once, all of them at the same time.
    """
    from personalclaw.llm.registry import canonical_provider_type
    from personalclaw.providers.connection import (
        FAILED,
        get_connection_board,
        settings_fingerprint,
    )

    wanted: dict[str, set[str]] = {}
    for ref in refs:
        parsed = split_ref(str(ref))
        if parsed:
            wanted.setdefault(parsed[0], set()).add(parsed[1])
    if not wanted:
        return {}
    board = get_connection_board()
    catalogs: dict[str, Any] = {}
    for p in _get_providers_from_config():
        name = str(p.get("name", ""))
        if name not in wanted or name in catalogs:
            continue
        fingerprint = settings_fingerprint(
            canonical_provider_type(p.get("type", "")), p.get("options")
        )
        last = board.peek(name, fingerprint)
        if last is not None and last.state == FAILED:
            continue  # its listing would fail too, and re-send a key its vendor refused
        catalog = _catalog_for_config_provider(p)
        if catalog is not None:
            catalogs[name] = catalog

    async def _rows(catalog: Any) -> list[Any]:
        return list(await asyncio.wait_for(catalog.list_models(), _BIND_CHECK_TIMEOUT_SECS))

    names = list(catalogs)
    listings = await asyncio.gather(*(_rows(catalogs[n]) for n in names), return_exceptions=True)
    out: dict[tuple[str, str], list[str]] = {}
    for name, rows in zip(names, listings):
        if isinstance(rows, BaseException):
            logger.debug("models of %s could not be listed to check a binding", name, exc_info=rows)
            continue
        for row in rows:
            if row.id in wanted[name]:
                jobs = out.setdefault((name, row.id), [])
                jobs.extend(c for c in row.capabilities or [] if c not in jobs)
    return out


def _cannot_serve(use_case: str, provider: str, model: str, jobs: list[str]) -> str:
    """Why the model its provider lists for ``jobs`` cannot be bound to ``use_case``, or ``""``."""
    need = parent_capability(use_case)
    if need in jobs:
        return ""
    wanted = _JOB_WORDS.get(need, need)
    named = [_JOB_WORDS[j] for j in jobs if j in _JOB_WORDS]
    choose = "Choose one of the models Settings → Models offers for it."
    if not named:
        return (
            f"{provider} lists “{model}” for nothing PersonalClaw can use it for, so it can't be "
            f"used for {wanted}. {choose}"
        )
    listed = named[0] if len(named) == 1 else f"{', '.join(named[:-1])} and {named[-1]}"
    return f"{provider} lists “{model}” for {listed}, not for {wanted}. {choose}"


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


def _media_model(kind: str, provider: str, m: Any) -> dict[str, Any]:
    """One image- or video-generation model as a Settings → Models row lists it. The id is BARE:
    the FE prepends ``provider:`` to build the binding ref (matching stt/tts/chat)."""
    model: dict[str, Any] = {
        "id": m.name,
        "name": m.name,
        "capabilities": [kind],
        "description": m.description,
        "provider": provider,
        "provider_type": kind,
    }
    if kind == "image_gen":
        model["downloaded"] = m.downloaded
        model["supports_edit"] = m.supports_edit
    return model


async def _media_rows(kind: str) -> list[dict[str, Any]]:
    """Settings → Models' rows for the ``image_gen`` or ``video_gen`` providers: one per provider,
    keyed by its name, so each shows under its own card.

    The image_gen providers (the OpenAI-Images adapter built per OpenAI-family config provider,
    and the apps' own adapters) own model catalogs the chat/embedding discovery doesn't see, so
    they are listed here for the 'Image · Generation' row, and the video providers for 'Video ·
    Generation'. A provider that cannot generate right now keeps its row when it says why
    (``unavailable_reason``), with that sentence as the row's ``error``: every unavailable
    provider used to be left out, so an instance whose key was missing vanished from the row with
    nothing saying why. One with nothing to say is still left out, as an adapter that makes no
    images at all is. A listing that fails is its row's error too, not a row that lists none.
    """
    try:
        if kind == "image_gen":
            from personalclaw.image_gen import registry as ig

            ig._ensure_registered()
            providers: list[Any] = list(ig.list_providers())
        else:
            from personalclaw.video_gen import registry as vg

            providers = list(vg.list_providers())
    except Exception:  # noqa: BLE001 — a registry that can't load lists no media providers
        logger.debug("%s discovery failed", kind, exc_info=True)
        return []
    rows: list[dict[str, Any]] = []
    for prov in providers:
        row: dict[str, Any] = {"name": prov.name, "type": kind, "models": []}
        try:
            if not await prov.is_available():
                reason = " ".join(str(await prov.unavailable_reason() or "").split())
                if reason:
                    rows.append({**row, "error": reason[:FAILURE_DETAIL_CHARS]})
                continue
            row["models"] = [_media_model(kind, prov.name, m) for m in await prov.list_models()]
        except Exception as exc:  # noqa: BLE001 — one provider's failure is its row's error
            logger.debug("%s provider %r could not be listed", kind, prov.name, exc_info=True)
            row["error"] = relayed_failure_copy(exc)[:FAILURE_DETAIL_CHARS]
        if row["models"] or "error" in row:
            rows.append(row)
    return rows


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
    they have no local weights, and an absent field is how the UI knows to draw no chip. Nor
    does a row of a configured model server's card whose model runs off this machine (one an
    Ollama answers from its cloud, or any model of a server on another machine): it carries
    ``runs_here: false`` instead, which the UI says.

    Every configured instance's row carries its MEASURED ``connection``
    (``providers/connection.py``). An instance whose last check failed is not asked for its
    models again until a check passes — its row carries the check's sentence as ``error`` —
    which is what stopped a rejected key being re-sent to its vendor on every load. A row
    that could not be listed says so in ``error``; ``models: []`` alone means "lists none".
    One that could not be listed while its last check said Connected is measured again, in
    the background, so its connection stops reading Connected on the next load.

    A model whose Test cannot work for one of its use cases carries ``untestable`` — ``{use
    case: the sentence saying why}`` — and every other row's Test is ``POST /api/models/test``.
    """
    from personalclaw.llm.registry import canonical_provider_type
    from personalclaw.providers.connection import (
        CONNECTED,
        FAILED,
        Connection,
        get_connection_board,
        settings_fingerprint,
    )

    providers_cfg = _get_providers_from_config()
    result: list[dict[str, Any]] = []
    board = get_connection_board()
    connections: dict[str, Connection] = {}
    measure_again: dict[str, Any] = {}  # name → a call that re-measures it in the background
    for p in providers_cfg:
        pname = str(p.get("name", ""))
        fingerprint = settings_fingerprint(
            canonical_provider_type(p.get("type", "")), p.get("options")
        )
        catalog_factory = functools.partial(_catalog_for_config_provider, p)
        connections[pname] = board.read(pname, fingerprint, catalog_factory)
        measure_again[pname] = functools.partial(
            board.remeasure, pname, fingerprint, catalog_factory
        )

    def _listing_failed(pname: str) -> None:
        """Its models could not be listed, while its last test said Connected: that answer no
        longer describes it, so it is measured again, and Settings → Providers stops showing an
        instance as connected that did not answer here."""
        if pname in connections and connections[pname].state == CONNECTED:
            measure_again[pname]()

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
                _listing_failed(pname)
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
    from personalclaw.llm.registry import served_on_this_machine
    from personalclaw.local_models import fit as _fit
    from personalclaw.local_models.registry import adapts_an_entry
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
                _listing_failed(pkey)
        # A configured model server's card (an Ollama instance) lists models that run where that
        # server answers each one: its own on its machine, and one it passes on to a hosted
        # service elsewhere (``llm.registry.served_on_this_machine``). Only a model that runs
        # here is judged against this machine's memory, or counts as a variant of a family here;
        # any other says it runs off this machine and carries no fit at all, because the
        # question does not apply to it. A bundled runtime's models run in the gateway.
        per_model = adapts_an_entry(prov)
        runs_here = [
            not per_model or served_on_this_machine(pkey, str(d.get("name", ""))) for d in rows
        ]
        here = [d for d, ok in zip(rows, runs_here) if ok]
        # A family QUOTES its median variant, never its smallest: quoting the smallest
        # promises a fit the user will not get from the variant they actually pick. A
        # colonless name is a family of one, so its quote is its own size and nothing
        # changes for it.
        sizes_by_family: dict[str, list[float]] = {}
        for d in here:
            sizes_by_family.setdefault(_fit.family_key(str(d.get("name", ""))), []).append(
                float(d.get("size_mb") or 0)
            )
        models = []
        for d, ok in zip(rows, runs_here):
            d["provider"] = pkey
            d["provider_type"] = pkey
            if not ok:
                d["runs_here"] = False
            else:
                family = _fit.family_key(str(d.get("name", "")))
                quoted = _fit.median_variant_size_mb(sizes_by_family.get(family, []))
                # The VERDICT is judged against the weights this row actually pulls — its own
                # size. Judging every variant by the family quote would paint the family's
                # largest variant with the median's verdict, i.e. promise a fit that OOMs. A
                # row that publishes NO size (a family entry in a searchable catalog) falls back
                # to the family quote, which is the median and never the smallest for exactly
                # the reason above; with neither, ``fit_verdict`` answers "unknown".
                own_size_mb = float(d.get("size_mb") or 0)
                assessment = _fit.fit_verdict(
                    size_mb=own_size_mb or quoted,
                    context_tokens=int(d.get("context_tokens") or 0),
                    budget_bytes=budget_bytes,
                )
                d["quoted_size_mb"] = round(quoted, 1)
                d["fit"] = assessment.verdict
                d["fit_reason"] = assessment.reason
                d["fit_need_mb"] = round(assessment.need_bytes / _BYTES_PER_MB, 1)
                d["fit_step_down"] = _step_down_name(here, family, assessment.verdict, budget_bytes)
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

    # Image- and video-generation providers, one row each: their models, or why they have none.
    result.extend(await _media_rows("image_gen"))
    result.extend(await _media_rows("video_gen"))

    # Every row offers its Test, for each use case it is listed under, unless that Test cannot
    # work: then it says why, instead (``providers.model_test``).
    from personalclaw.providers.model_test import mark_untestable

    mark_untestable(result)

    return web.json_response(
        {
            "providers": result,
            # ``budget_mb`` is null — never 0 — on a host whose memory could not be
            # "unknown" and "nothing fits" are different answers and only one of
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

    Returns ``{use_cases: {chat: [model_ids...], embedding: [model_id], ...}, revisions: {...}}``.

    Each use case's chain is ONE document — the PUT below replaces all of it — so each carries
    the revision that write must name (`personalclaw/stale_write.py`), keyed by use case and
    taken from the very chain beside it.
    """
    active = load_active_models()
    normalized: dict[str, list[str]] = {}
    for uc in USE_CASES:
        normalized[uc] = active.get(uc, [])
    return web.json_response(
        {
            "use_cases": normalized,
            "revisions": {uc: revision_of(chain) for uc, chain in normalized.items()},
        }
    )


async def api_models_active_set(request: web.Request) -> web.Response:
    """PUT /api/models/active/{use_case} — set the active model CHAIN for a use-case.

    Body: {models: ["provider_name:model_id", ...]} — an ordered fallback chain
    for EVERY use case (MODEL-USE-CASES-V2): position 0 is the default, later
    entries are fallbacks resolution walks when an earlier provider's breaker is
    open or its build fails. Order is preserved verbatim.

    The chain is replaced whole, so the request names the revision it was built from in
    ``If-Match`` — ``revisions[use_case]`` from the GET — and a chain that changed since is
    refused with ``409 stale_write`` (`personalclaw/stale_write.py`).

    Every entry names a model (``use_cases.names_model``): one that names none (``""``,
    ``"Bedrock:"``) is refused with ``400 model_ref_names_no_model``. A model the request adds
    whose own provider lists it for other jobs — an embedding model bound to Chat, a reranker to
    anything — is refused with ``400 model_cannot_serve_use_case``; one its provider does not
    describe (it could not be asked, or does not list it) is bound as asked
    (:func:`_listed_jobs`). Binding Embedding to a model starts the re-index of what that model
    has not embedded, in the background.
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

    # Every entry names a model. One that names only a provider (`"Bedrock:"`), or nothing, chose
    # none, and was stored: a media call on it was then answered by a model of the adapter's own
    # choosing. Settings → Models offers no such entry, so this is an API caller's mistake.
    unnamed = [m for m in models if not names_model(m)]
    if unnamed:
        _sel_log(
            "models.active_set",
            "error",
            f"{use_case}:{unnamed[0]!r}",
            request,
            error="a chain entry names no model",
        )
        return json_error(
            "model_ref_names_no_model",
            message=_names_no_model(unnamed[0]),
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
        from personalclaw.providers.use_cases import _known_provider_names

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

    # A model this request ADDS is checked against what its own provider lists it for. One the
    # chain already holds is not: a binding stored before this check existed can still be moved
    # and removed.
    held = set(load_active_models().get(use_case, []))
    added = [str(m) for m in models if str(m) not in held]
    listed = await _listed_jobs(added)
    for ref in added:
        provider, model = split_ref(ref) or ("", ref)
        jobs = listed.get((provider, model))
        refusal = _cannot_serve(use_case, provider, model, jobs) if jobs is not None else ""
        if refusal:
            _sel_log(
                "models.active_set",
                "error",
                f"{use_case}:{ref}",
                request,
                error=f"its provider lists it for {', '.join(jobs or []) or 'no job'}",
            )
            return json_error("model_cannot_serve_use_case", message=refusal, status=400)

    active = load_active_models()
    # 🔴 A CHAIN IS WRITTEN ONLY OVER THE COPY IT WAS BUILT FROM. The Models panel builds the
    # chain it sends from the one it read — a toggle appends to it, a reorder swaps two of its
    # entries — so a tab opened before another tab (or onboarding, or a provider's removal)
    # changed this use case replaced that change with its own copy, without a word. Checked here,
    # with no `await` before the save, so nothing can land between the comparison and the write.
    stale = stale_write_refusal(
        request, active.get(use_case, []), what=f"the {use_case} model chain"
    )
    if stale is not None:
        _sel_log("models.active_set", refusal_outcome(stale), use_case, request)
        return stale
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
    if use_case == "embedding" and active[use_case]:
        # Embed what the model bound now has not, in the background, by the one path every change
        # of the model takes: a memory written while no model was bound joins semantic search now,
        # whichever caller bound it, and a model not ready yet is re-indexed once it is. Settings →
        # Models also starts the re-index after its save; the registry runs one job at a time, so
        # both reach the same job.
        from personalclaw.dashboard.handlers.embedding_reindex import (
            schedule_reindex_for_binding,
        )

        schedule_reindex_for_binding(request.app)
    # The new revision, so a panel that stays open saves its next edit over this one.
    return web.json_response(
        {
            "ok": True,
            "use_case": use_case,
            "models": active[use_case],
            "revision": revision_of(active[use_case]),
        }
    )


async def api_models_chat(request: web.Request) -> web.Response:
    """GET /api/models/chat — chat models for dropdowns (the one model list).

    Returns active chat models from Settings → Models when configured — less any its own provider
    lists for something other than chat — else falls back to discovering all chat-capable models
    from every provider.

    Each entry carries BOTH ``model_name`` and ``model_id`` (the same bare id)
    plus ``name``/``provider``/``description`` — a superset shape so every
    consumer (composer model pill reads model_name; agent/chat pickers read
    name/model_id) works off one endpoint.
    """
    active = load_active_models()
    chat_active = active.get("chat", [])

    # An entry that names no model is none to offer: a picker binding it would write it back.
    chat_active = [ref for ref in chat_active if names_model(ref)]
    # Nor is one its own provider lists for something other than chat, bound before the PUT
    # checked: a turn on it fails. One its provider does not describe is offered as bound.
    jobs_of = await _listed_jobs(chat_active)
    chat_active = [
        ref for ref in chat_active if "chat" in jobs_of.get(split_ref(ref) or ("", ref), ["chat"])
    ]
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
    from personalclaw.llm.registry import get_default_registry, own_model

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
        if not str(mid or "").strip():
            return  # a listing row with no id is no model to offer
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
        # The instance's own model (its model, else its Default Model): the one it serves when
        # nothing names one, so the model offered when discovery cannot list any.
        pinned = own_model(p.get("model"), p.get("options"))
        if catalog is None:
            # No discovery available — surface the instance's own model if it names one.
            if pinned:
                _add(pname, pinned)
            continue
        tasks.append((pname, pinned, catalog.list_models()))

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
        if entry.name in listed or entry.type == "acp_agent" or not entry.own_model:
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
        _add(entry.name, entry.own_model)

    return web.json_response(all_models)


async def api_model_test(request: web.Request) -> web.Response:
    """POST /api/models/test — Test one model for one use case with one small real call.

    Body ``{use_case, model}``, ``model`` a ``"provider:model"`` ref. Answers ``{use_case, model,
    ok, detail, reason, duration_ms}``; a Test that failed is a 200 whose ``detail`` says why,
    because the Test ran and that is its answer (``providers.model_test``). Refused before any
    call is made: a use case Settings → Models doesn't list, or a ref that names no model (400
    ``invalid_request``); a use case or provider with no Test (409 ``model_untestable``, its
    message saying why); and a second Test while one of the same provider's runs (409
    ``model_test_running``). User-click only: it spends the provider's tokens or this machine's
    compute, so nothing runs it on a schedule.
    """
    from personalclaw.providers.model_test import (
        ModelTestRunning,
        ModelUntestable,
        run_model_test,
    )
    from personalclaw.request_validation import json_object_body

    body = await json_object_body(request, empty_ok=False)
    use_case = body.get("use_case")
    if not isinstance(use_case, str) or use_case not in VALID_USE_CASES:
        return json_error(
            "invalid_request",
            message=f"Name the use case to test the model for, one of: {', '.join(USE_CASES)}.",
            status=400,
        )
    ref = body.get("model")
    parsed = split_ref(ref) if isinstance(ref, str) else None
    if parsed is None or not parsed[0].strip() or not parsed[1].strip():
        return json_error(
            "invalid_request",
            message='Name the model to test as "provider:model", as Settings → Models lists it.',
            status=400,
        )
    provider_name, model = parsed
    try:
        result = await run_model_test(use_case, provider_name, model)
    except ModelUntestable as refusal:
        return json_error("model_untestable", message=str(refusal), status=409)
    except ModelTestRunning:
        return json_error(
            "model_test_running",
            message=(
                f"A Test of one of {provider_name}'s models is already running. Try again when "
                "it has finished."
            ),
            status=409,
        )
    return web.json_response({"use_case": use_case, "model": ref, **result.to_dict()})


def register_model_registry_routes(app: web.Application) -> None:
    """Register model registry routes.

    Local-model download/delete/search is served generically by the local-model
    routes (``/api/models/downloads`` + ``/api/models/local/{provider}/…``); no
    per-kind catalog/delete routes live here anymore."""
    app.router.add_get("/api/models/available", api_models_available)
    app.router.add_get("/api/models/active", api_models_active)
    app.router.add_put("/api/models/active/{use_case}", api_models_active_set)
    app.router.add_get("/api/models/chat", api_models_chat)
    app.router.add_post("/api/models/test", api_model_test)
