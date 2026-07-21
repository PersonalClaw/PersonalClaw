"""Model-provider and agent-provider management API handlers.

Routes:
    GET    /api/model-providers              — list configured model-provider entries
    POST   /api/model-providers              — create a model provider
    PUT    /api/model-providers/{name}       — update a model provider
    DELETE /api/model-providers/{name}       — delete a model provider
    POST   /api/model-providers/{name}/test  — test a provider's connectivity
    GET    /api/model-providers/{name}/models, /search; POST .../pull, .../models/delete
    GET    /api/agent-providers              — list agent runtimes (native + acp:<cli>)
    GET    /api/agent-providers/{id}/agents  — discovered agents for a runtime
"""

import asyncio
import contextlib
import logging
from typing import Any

from aiohttp import web

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

# Readiness-probe cache for ``/api/agent-providers``. A runtime warmed in the
# connection pool is answered instantly from the pool; runtimes NOT in the pool
# (e.g. codex, whose engine is absent → a 45s npx-fetch probe that always fails)
# would otherwise be re-probed on every call. Cache their probe result with a
# short TTL so the endpoint stays fast after the first hit. Process-local; keyed
# by runtime id → (monotonic_ts, status_dict).
_READINESS_TTL_SECS = 300.0
_readiness_cache: dict[str, tuple[float, dict[str, Any]]] = {}


# ── /api/model-providers ────────────────────────────────────────────────────────────


async def api_providers_list(request: web.Request) -> web.Response:
    """GET /api/model-providers — list configured model-provider entries.

    Returns ``{providers: [{name, type, model, capabilities, credential_status}]}``.
    ``credential_status`` is ``"ok"``, ``"missing"``, or ``"unconfigured"``
    (when no credential is declared).  No secret values are included.
    """
    from personalclaw.llm.registry import get_default_registry

    registry = get_default_registry()
    entries = registry.list_entries()

    result: list[dict[str, Any]] = []
    for entry in entries:
        # Agent-runtime entries (acp_agent / acp:<cli>) are NOT model providers —
        # they have no model catalog and no endpoint, so they belong only under
        # "Agent Providers" (/api/agent-providers). Listing them here surfaced a
        # bogus card per ACP runtime whose Test said "no endpoint configured" and
        # Models said "no model found". Exclude them from the model-provider list.
        if entry.type == "acp_agent":
            continue
        # Resolve credential status without exposing the secret
        if not entry.credential:
            cred_status = "ok"
        else:
            try:
                from personalclaw.config.loader import config_dir
                from personalclaw.llm.credentials import CredentialStore

                store = CredentialStore(config_dir() / "credentials.json")
                cred = store.resolve(entry.credential)
                cred_status = "ok" if cred.secret else "missing"
            except Exception:
                cred_status = "missing"

        # Get static capability descriptor for this type
        try:
            cap = registry.capability_of(entry.type)
            capabilities = sorted(c.value for c in cap.capabilities)
        except Exception:
            capabilities = sorted(c.value for c in entry.declared_capabilities)

        result.append(
            {
                "name": entry.name,
                "type": entry.type,
                "model": entry.model,
                "capabilities": capabilities,
                "credential_status": cred_status,
            }
        )

    return web.json_response({"providers": result})


async def api_provider_types(request: web.Request) -> web.Response:
    """GET /api/model-provider-types — installable model-provider types.

    Drives the "Add instance" dropdown. The list is EXACTLY the model-provider
    apps currently installed (each contributes a provider ``type`` via its
    manifest) — no hardcoded type list in the frontend. A type not backed by an
    installed app never appears, so a user can only add an instance of a provider
    whose app they've installed. Each entry carries the app's display label, the
    declared capabilities, whether it's multi-instance, and its ``settingsSchema``
    (JSON Schema + x-meta) so the form renders the right fields (api_key / region /
    endpoint enum / …) without the frontend knowing the provider.
    """
    from personalclaw.providers.registry import get_provider_registry

    reg = get_provider_registry()
    seen: set[str] = set()
    types: list[dict[str, Any]] = []
    for ext in reg.list_by_type("model"):
        cfg = ext.provider_config
        # The CONCRETE type the app registers into the LLM registry (what
        # api_provider_create expects) is ``providerType`` — NOT ``cfg.type``,
        # which is the entity CLASS "model". Fall back to the app-name stem only
        # if the manifest doesn't declare it.
        ptype = cfg.providerType or ext.manifest.name.replace("-models", "")
        if not ptype or ptype in seen or ptype == "acp_agent":
            continue
        seen.add(ptype)
        manifest = ext.manifest
        types.append(
            {
                "type": ptype,
                "label": manifest.displayName or manifest.name,
                "app": manifest.name,
                "capabilities": list(cfg.capabilities),
                "multiInstance": bool(cfg.multiInstance),
                "settingsSchema": cfg.settingsSchema or {},
            }
        )
    types.sort(key=lambda t: t["label"].lower())
    return web.json_response({"types": types})


# ── /api/agent-providers ──────────────────────────────────────────────────────


async def api_agent_providers_list(request: web.Request) -> web.Response:
    """GET /api/agent-providers — the single list of agent runtimes + readiness.

    This is the one source of truth for the "Agent Providers" UI section: the
    AgentProvider *runtime* axis, spanning the in-process ``native`` runtime
    and every ``acp:<cli>`` runtime registered by a removable bundle
    (claude-code / codex / future). Each runtime is probed so the UI
    can show a readiness chip and offer the Sign-in terminal when a runtime
    reports ``needs_login``.

    Returns ``{agent_providers: [{name, provider_id, type, extension, ready,
    state, detail, login_command}]}`` where ``extension`` (when present) is the
    bundle name the row's enable/config card is keyed by, so the frontend can
    merge readiness onto the extension card instead of rendering two sections.
    """
    from personalclaw.agents.registry import get_agent_provider_class
    from personalclaw.llm.registry import get_default_registry

    # ?refresh=1 forces a fresh readiness probe — used right after a sign-in (and by
    # the manual "Check availability" action) so a newly-authed CLI is re-detected
    # instead of being answered from the 5-minute cache.
    force_refresh = request.query.get("refresh") in ("1", "true", "yes")

    registry = get_default_registry()
    entries = registry.list_entries()

    result: list[dict[str, Any]] = []

    # ── native: the always-available in-process runtime ──────────────────
    # It is not a model-registry ProviderEntry (it's resolved per-session by
    # the provider bridge from an agent's definition), so synthesize its row
    # explicitly. It needs no external CLI and no sign-in — always ready.
    result.append(
        {
            "name": "native",
            "provider_id": "native",
            "type": "native",
            "extension": "native-agents",
            "ready": True,
            "state": "ready",
            "detail": "In-process agent runtime (no external CLI).",
            "login_command": None,
        }
    )

    # ── acp:<cli> runtimes registered by bundles ─────────────────────────
    # Only entries that resolve to an AgentProvider runtime belong here. The
    # ACP family registers under runtime-id prefix "acp"; entry type is
    # "acp_agent".
    runtime_entries = [
        entry
        for entry in entries
        if get_agent_provider_class("acp" if entry.type == "acp_agent" else entry.type) is not None
    ]

    # A runtime with a live warmed pool connection is provably ready — answer
    # from the pool WITHOUT a fresh handshake. Without this, every call to this
    # endpoint re-spawns a probe per ACP runtime (~22s total; codex alone does a
    # 45s npx fetch before failing), which is what made the chat picker's
    # discovered section appear ~22s late even though the snapshot was pre-warmed.
    from personalclaw.acp.connection_pool import get_acp_pool

    _pool = get_acp_pool()

    async def _probe(entry: Any) -> dict[str, Any]:
        family = "acp" if entry.type == "acp_agent" else entry.type
        cls = get_agent_provider_class(family)
        options = dict(entry.options or {})
        import time as _time

        def _row(status_d: dict[str, Any]) -> dict[str, Any]:
            return {
                "name": entry.name,
                "provider_id": entry.name,
                "type": entry.type,
                "extension": options.get("extension"),
                **status_d,
            }

        # 1. Pool fast-path: a live warmed connection IS ready (no probe).
        if _pool is not None and _pool.is_warmed(entry.name):
            return _row(
                {
                    "ready": True,
                    "state": "ready",
                    "detail": "warmed (pooled live connection)",
                    "login_command": None,
                }
            )
        # 2. Cached probe result (covers not-pooled runtimes like codex without
        #    re-paying their slow handshake on every call). Skipped on ?refresh=1 so
        #    a just-signed-in CLI is re-probed instead of returning the stale state.
        if not force_refresh:
            hit = _readiness_cache.get(entry.name)
            if hit and (_time.monotonic() - hit[0]) < _READINESS_TTL_SECS:
                return _row(hit[1])
        # 3. Live probe (first hit / cache miss).
        if cls is None:
            return _row(
                {
                    "ready": False,
                    "state": "error",
                    "detail": f"no agent provider registered for family {family!r}",
                    "login_command": None,
                }
            )
        try:
            status = await cls.probe_readiness(options)
            status_d = {
                "ready": status.ready,
                "state": status.state,
                "detail": status.detail,
                "login_command": status.login_command,
            }
        except Exception as exc:  # noqa: BLE001 - never fail the listing
            logger.debug("agent-provider probe failed for %s: %s", entry.name, exc)
            status_d = {
                "ready": False,
                "state": "error",
                "detail": f"probe failed: {exc}",
                "login_command": None,
            }
        _readiness_cache[entry.name] = (_time.monotonic(), status_d)
        # entry.name is already the canonical runtime id ("acp:<cli>") — _row uses
        # it directly rather than re-deriving from the command basename (which
        # would mislabel an adapter like claude-agent-acp).
        return _row(status_d)

    # Probe in parallel — each probe can block up to its handshake timeout, so
    # serial probing would make the Settings list wait on their sum.
    if runtime_entries:
        import asyncio as _asyncio

        result.extend(await _asyncio.gather(*(_probe(e) for e in runtime_entries)))

    return web.json_response({"agent_providers": result})


async def warm_readiness_cache() -> int:
    """Populate ``_readiness_cache`` for every ACP runtime at startup.

    The agent-providers readiness probe is slow for runtimes NOT in the pool
    (codex does a 45s npx fetch before failing). Running it once in the background
    at launch means the first ``/api/agent-providers`` call — which the chat
    picker's discovered section depends on — is fast instead of blocking on the
    slowest probe. Pool-warmed runtimes are answered from the pool and skipped
    here. Best-effort; never raises. Returns the number of runtimes probed."""
    import asyncio as _asyncio
    import time as _time

    from personalclaw.acp.connection_pool import get_acp_pool
    from personalclaw.agents.registry import get_agent_provider_class
    from personalclaw.llm.registry import get_default_registry

    pool = get_acp_pool()
    entries = [e for e in get_default_registry().list_entries() if e.type == "acp_agent"]
    targets = [e for e in entries if not (pool is not None and pool.is_warmed(e.name))]
    if not targets:
        return 0

    async def _probe_one(entry: Any) -> None:
        cls = get_agent_provider_class("acp")
        if cls is None:
            return
        try:
            status = await cls.probe_readiness(dict(entry.options or {}))
            status_d = {
                "ready": status.ready,
                "state": status.state,
                "detail": status.detail,
                "login_command": status.login_command,
            }
        except Exception as exc:  # noqa: BLE001
            status_d = {
                "ready": False,
                "state": "error",
                "detail": f"probe failed: {exc}",
                "login_command": None,
            }
        _readiness_cache[entry.name] = (_time.monotonic(), status_d)
        # A ready runtime's FIRST discovery is a cold session/new (~15-20s). If we
        # only warm readiness, the chat picker's discovered section is still empty
        # on first open until that slow fetch lands ("No agents available" right
        # after an agent app becomes ready). Warm discovery here too so a booted /
        # freshly-enabled ACP runtime is immediately pickable. Best-effort.
        if status_d.get("ready"):
            try:
                await _compute_discovery(entry.name, entry)
            except Exception:  # noqa: BLE001 - warming never breaks boot
                logger.debug("discovery pre-warm failed for %s", entry.name, exc_info=True)

    await _asyncio.gather(*(_probe_one(e) for e in targets), return_exceptions=True)
    return len(targets)


# ── /api/agent-providers/{id}/agents ──────────────────────────────────────────

# In-process discovery cache: discovery opens a live session (spawn + initialize
# + session/new, ~15-20s), so cache the normalized result per runtime id with a
# short TTL. A "Refresh" affordance (?refresh=1) bypasses the cache. The cache is
# intentionally module-level + process-local: it reflects a live external CLI's
# account state and must not survive a gateway restart.
_DISCOVERY_TTL_SECS = 600.0
_discovery_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _runtime_label_for(entry: Any) -> str:
    """A friendly display label for a runtime (e.g. "Claude Code", "Codex").

    Title-cases the ``acp:<cli>`` suffix so discovered agent names read well
    (``claude-code`` → ``Claude Code``). Vendor display polish lives at this
    presentation layer; the backend stays neutral."""
    name = str(entry.name or "")
    cli = name.split(":", 1)[-1] if ":" in name else name
    return " ".join(w.capitalize() for w in cli.replace("_", "-").split("-") if w) or cli


async def api_agent_provider_agents(request: web.Request) -> web.Response:
    """GET /api/agent-providers/{id}/agents — list a runtime's discoverable agents.

    Opens one live session on the ``acp:<cli>`` runtime and returns its normalized
    agent catalog (default-dialect personas / claude effort-agents) for the chat picker.
    Cached per runtime id with a TTL; ``?refresh=1`` forces a fresh probe.

    Returns ``{agents: [{id, name, runtime, description, provider_agent,
    reasoning_effort, models}], permission_modes: [...], cached: bool}`` where
    ``permission_modes`` are the runtime's NATIVE permission modes (raw capability
    for the trust-ladder grey-out). ``native`` and unknown ids return ``[]``.
    """
    runtime_id = request.match_info.get("id", "")
    refresh = request.rel_url.query.get("refresh") in ("1", "true", "yes")

    # native has no discovered agents (its agents are PersonalClaw's own defs).
    if runtime_id == "native":
        return web.json_response({"agents": [], "permission_modes": [], "cached": False})

    # Serve from cache unless a refresh was requested.
    if not refresh:
        cached = _cached_discovery(runtime_id)
        if cached is not None:
            return web.json_response({**cached, "cached": True})

    from personalclaw.llm.registry import get_default_registry

    registry = get_default_registry()
    try:
        entry = registry.get_entry(runtime_id)
    except Exception:
        return web.json_response({"error": f"unknown runtime {runtime_id!r}"}, status=404)
    if entry.type != "acp_agent":
        return web.json_response({"error": f"{runtime_id!r} is not an ACP runtime"}, status=400)

    payload = await _compute_discovery(runtime_id, entry)
    if payload is None:
        return web.json_response({"error": "ACP runtime class unavailable"}, status=500)
    return web.json_response({**payload, "cached": False})


def _cached_discovery(runtime_id: str) -> dict[str, Any] | None:
    """Return the cached discovery payload for *runtime_id* if still fresh."""
    import time as _time

    hit = _discovery_cache.get(runtime_id)
    if hit and (_time.monotonic() - hit[0]) < _DISCOVERY_TTL_SECS:
        return dict(hit[1][0]) if hit[1] else {}
    return None


async def _compute_discovery(runtime_id: str, entry: Any) -> dict[str, Any] | None:
    """Run discovery for one ACP runtime entry, write the cache, return payload.

    Returns ``None`` only when the ACP runtime class can't be resolved. Discovery
    failures yield an empty agent list (never raises) so callers never break.
    """
    import time as _time

    from personalclaw.agents.registry import get_agent_provider_class

    cls = get_agent_provider_class("acp")
    if cls is None:
        return None

    options = dict(entry.options or {})
    options["runtime_id"] = runtime_id
    options["runtime_label"] = _runtime_label_for(entry)

    # Fast path: a warmed pool connection holds a live ``session/new`` snapshot —
    # map it directly (no spawn). Falls back to a throwaway probe when no pool
    # connection exists (no-pool deploys / a not-yet-warmed runtime).
    agents = None
    try:
        from personalclaw.acp.connection_pool import get_acp_pool

        pool = get_acp_pool()
        snap = pool.snapshot(runtime_id) if pool is not None else None
        if snap is not None:
            agents = cls.agents_from_snapshot(options, snap)
            logger.debug("discovery: served %s from pool snapshot", runtime_id)
    except Exception:
        logger.debug("discovery: pool snapshot path failed for %s", runtime_id, exc_info=True)
        agents = None

    if agents is None:
        try:
            agents = await cls.discover_agents(options)
        except Exception as exc:  # noqa: BLE001 - discovery never breaks the caller
            logger.debug("discover_agents failed for %s: %s", runtime_id, exc)
            agents = []

    permission_modes = await _runtime_permission_modes(options, cls)
    payload: dict[str, Any] = {
        "agents": [
            {
                "id": a.id,
                "name": a.name,
                "runtime": a.runtime,
                "description": a.description,
                "provider_agent": a.provider_agent,
                "reasoning_effort": a.reasoning_effort,
                "models": list(a.models),
                "supported_efforts": list(a.supported_efforts),
            }
            for a in agents
        ],
        "permission_modes": permission_modes,
    }
    _discovery_cache[runtime_id] = (_time.monotonic(), [payload])
    return payload


async def _runtime_permission_modes(options: dict, cls: Any) -> list[str]:
    """The runtime's native permission modes (capability for the trust grey-out).

    Reuses the same lightweight read discovery does — but discovery already
    captured it via the dialect. To avoid a second spawn we infer from the
    dialect's static shape: Zed adapters expose the 5-mode axis; the default
    dialect exposes none. This is a static per-dialect fact, so no extra
    session is opened."""
    from personalclaw.acp.dialect import ZedAdapterDialect, get_dialect

    dialect = get_dialect(options.get("dialect"))
    if isinstance(dialect, ZedAdapterDialect):
        # The adapter validates against the live model's modes, but the canonical
        # set is stable; the trust-ladder grey-out only needs "does this runtime
        # have a separate mode axis at all + which rungs". Report the full set.
        return ["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"]
    return []


# ── /api/model-providers/{name}/models ─────────────────────────────────────────────


async def api_provider_models(request: web.Request) -> web.Response:
    """GET /api/model-providers/{name}/models — list available models for a provider entry.

    Generic across provider types: resolves the entry's registered ModelCatalog and
    returns ``catalog.list_models()``. A provider with no catalog registered (its app
    not loaded, or a provider that exposes no discovery) returns an empty list — NOT
    an error — so a keyless/non-HTTP provider (e.g. Bedrock) can never 500 here (the
    old code assumed every non-ollama provider served an OpenAI ``/v1/models`` and
    fell back to ``localhost:11434``)."""
    from personalclaw.llm.registry import ProviderResolutionError, get_default_registry

    name = request.match_info.get("name", "")
    registry = get_default_registry()
    try:
        entry = registry.get_entry(name)
    except ProviderResolutionError:
        return web.json_response({"error": f"No provider entry named '{name}'"}, status=404)

    catalog = registry.build_catalog(entry)
    if catalog is None:
        return web.json_response({"models": []})
    try:
        models = await catalog.list_models()
    except Exception as exc:  # noqa: BLE001 — discovery failure is not a server error
        return web.json_response({"models": [], "error": str(exc)[:200]})
    # ``name`` is the field this endpoint historically returned (the model id); keep
    # it alongside ``id`` for FE compatibility. to_dict() flattens extra fields
    # (owned_by / parameter_size / size_human / …) onto the top level.
    out = []
    for m in models:
        d = m.to_dict()
        d.setdefault("name", d.get("id", ""))
        out.append(d)
    return web.json_response({"models": out})


# ── /api/model-providers/{name}/search ──────────────────────────────────────────────


async def api_provider_model_search(request: web.Request) -> web.Response:
    """GET /api/model-providers/{name}/search?q=<query> — search a provider's
    installable model catalog.

    Generic across provider types via the ModelManager axis: a provider whose
    catalog implements ``search_catalog`` (ollama) returns results; any other
    provider (a hosted API with no installable catalog) returns an empty list.
    """
    from personalclaw.llm.catalog import ModelManager
    from personalclaw.llm.registry import ProviderResolutionError, get_default_registry

    name = request.match_info.get("name", "")
    registry = get_default_registry()
    try:
        entry = registry.get_entry(name)
    except ProviderResolutionError:
        return web.json_response({"error": f"No provider entry named '{name}'"}, status=404)

    catalog = registry.build_catalog(entry)
    if not isinstance(catalog, ModelManager):
        return web.json_response({"results": []})

    q = request.rel_url.query.get("q", "").strip()
    if not q:
        return web.json_response({"error": "q parameter required"}, status=400)

    try:
        models = await catalog.search_catalog(q)
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"results": [], "error": str(exc)[:200]})
    # Preserve the historical result shape ({name, description, pulls, tags}).
    results = [
        {"name": m.name or m.id, "description": m.description, "pulls": 0, "tags": []}
        for m in models
    ]
    return web.json_response({"results": results})


# ── /api/model-providers/{name}/show ─────────────────────────────────────────────────


async def api_provider_model_show(request: web.Request) -> web.Response:
    """GET /api/model-providers/{name}/show?model=<m> — rich model metadata.

    Generic across provider types via the ModelManager axis. A provider whose
    catalog implements ``show_model`` (ollama) returns ``{model, family,
    parameter_size, quantization, format, context_length, capabilities,
    license_short}`` (empty fields omitted); any other provider returns 400
    "not supported". Lets a user inspect a model before binding it in
    Settings → Models."""
    from personalclaw.llm.catalog import ModelManager
    from personalclaw.llm.registry import ProviderResolutionError, get_default_registry

    name = request.match_info.get("name", "")
    registry = get_default_registry()
    try:
        entry = registry.get_entry(name)
    except ProviderResolutionError:
        return web.json_response({"error": f"No provider entry named '{name}'"}, status=404)

    catalog = registry.build_catalog(entry)
    if not isinstance(catalog, ModelManager):
        return web.json_response(
            {"error": "Model detail not supported by this provider"}, status=400
        )

    model = request.rel_url.query.get("model", "").strip()
    if not model:
        return web.json_response({"error": "model parameter required"}, status=400)

    try:
        info = await catalog.show_model(model)
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"error": str(exc)[:200]}, status=500)

    # to_dict flattens the manager's extra fields (family / parameter_size /
    # context_length / …) onto the top level; keep the historical ``model`` key
    # and drop empty values.
    out = info.to_dict()
    out["model"] = model
    out.pop("id", None)
    out.pop("name", None)
    return web.json_response({k: v for k, v in out.items() if v})


# ── /api/model-providers/{name}/pull ────────────────────────────────────────────────


async def api_provider_model_pull(request: web.Request) -> web.StreamResponse:
    """POST /api/model-providers/{name}/pull — pull (download) a model.

    Body: {model: "<model_name>"}. Generic across provider types via the
    ModelManager axis: providers whose catalog implements ``pull_model`` (ollama)
    stream progress; others return 400.

    Streams newline-delimited JSON progress frames. Each: {status, completed?,
    total?, digest?}; a terminal failure frame is {error: "..."}.
    """
    from personalclaw.llm.catalog import ModelManager
    from personalclaw.llm.registry import ProviderResolutionError, get_default_registry

    name = request.match_info.get("name", "")
    registry = get_default_registry()
    try:
        entry = registry.get_entry(name)
    except ProviderResolutionError:
        return web.json_response({"error": f"No provider entry named '{name}'"}, status=404)

    catalog = registry.build_catalog(entry)
    if not isinstance(catalog, ModelManager):
        return web.json_response(
            {"error": "Model download not supported by this provider"}, status=400
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    model = str(body.get("model", "")).strip()
    if not model:
        return web.json_response({"error": "model is required"}, status=400)

    # Stream the manager's PullProgress frames as NDJSON. Use web.StreamResponse +
    # prepare()/write() — the same pattern every other streaming endpoint uses
    # (api_chat, api_file_watch). The previous web.Response(body=<async gen>)
    # form does NOT stream incrementally under aiohttp; served directly by the
    # gateway (desktop) it failed to deliver progress, while the nginx-proxied
    # podman path masked it. One streaming primitive, both surfaces.
    #
    # Cancellable: when the client aborts the fetch (the user clicks Stop),
    # ``resp.write`` raises a connection-reset OR the task is cancelled. Closing
    # the pull_model generator (breaking the loop) closes its upstream connection,
    # which is what actually stops the provider-side download.
    import json as _json

    from aiohttp.client_exceptions import ClientConnectionResetError

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)
    cancelled = False
    pull = catalog.pull_model(model)
    try:
        async for frame in pull:
            # Stop the moment the client goes away — closing the generator (via the
            # finally aclose below) cancels the provider-side download.
            if request.transport is None or request.transport.is_closing():
                cancelled = True
                break
            try:
                await resp.write((_json.dumps(frame.to_dict()) + "\n").encode())
            except (ConnectionResetError, ClientConnectionResetError):
                cancelled = True
                break
    except (asyncio.CancelledError, ConnectionResetError, ClientConnectionResetError):
        cancelled = True
    except Exception as exc:
        # Best-effort error frame; the response is already prepared so we write
        # an error line rather than changing the status code.
        try:
            await resp.write((_json.dumps({"error": str(exc)[:200]}) + "\n").encode())
        except Exception:
            logger.debug("pull: failed to write error frame", exc_info=True)
    finally:
        # Close the async generator so its upstream connection is released (this is
        # what stops the provider-side download on client disconnect).
        with contextlib.suppress(Exception):
            aclose = getattr(pull, "aclose", None)
            if aclose is not None:
                await aclose()
    if cancelled:
        logger.info("Model pull of %r cancelled by client disconnect", model)
    with contextlib.suppress(Exception):
        await resp.write_eof()
    return resp


# ── /api/model-providers/{name}/models/delete ───────────────────────────────────────


async def api_provider_model_delete(request: web.Request) -> web.Response:
    """POST /api/model-providers/{name}/models/delete — delete a local model.

    Body: {model: "<model_name:tag>"}. Generic across provider types via the
    ModelManager axis: providers whose catalog implements ``delete_model`` (ollama)
    delete it; others return 400.
    """
    from personalclaw.llm.catalog import ModelManager
    from personalclaw.llm.registry import ProviderResolutionError, get_default_registry

    name = request.match_info.get("name", "")
    registry = get_default_registry()
    try:
        entry = registry.get_entry(name)
    except ProviderResolutionError:
        return web.json_response({"error": f"No provider entry named '{name}'"}, status=404)

    catalog = registry.build_catalog(entry)
    if not isinstance(catalog, ModelManager):
        return web.json_response(
            {"error": "Model deletion not supported by this provider"}, status=400
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    model = str(body.get("model", "")).strip()
    if not model:
        return web.json_response({"error": "model is required"}, status=400)

    try:
        await catalog.delete_model(model)
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"error": str(exc)[:200]}, status=500)
    return web.json_response({"ok": True, "model": model})


# ── /api/credentials ──────────────────────────────────────────────────────────


async def api_provider_create(request: web.Request) -> web.Response:
    """POST /api/model-providers — add a new model provider to config."""
    import json as _json

    from personalclaw.config.loader import config_path
    from personalclaw.dashboard.handlers.agents import _get_config_lock

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    name = body.get("name", "").strip()
    ptype = body.get("type", "").strip()
    model = body.get("model", "")
    options = body.get("options", {})

    if not name or not ptype:
        return web.json_response({"error": "name and type are required"}, status=400)

    # A provider type is valid iff SOME installed model app registered it — either
    # an inference factory (register_type) or a discovery catalog (register_catalog).
    # No hardcoded allow-list: the set of addable types is exactly what's installed
    # (the model-provider-as-app model). Every model-provider type comes from an app
    # (ollama included); only ``acp_agent`` — an agent runtime, not a model provider —
    # is core-native.
    from personalclaw.llm.registry import canonical_provider_type
    from personalclaw.llm.registry import get_default_registry as _gdr

    _reg = _gdr()
    _canon = canonical_provider_type(ptype)
    _known = _canon in _reg._capabilities or _reg.catalog_of(_canon) is not None  # noqa: SLF001
    if not _known:
        _addable = sorted(set(_reg._capabilities) | set(_reg._catalog_factories))  # noqa: SLF001
        return web.json_response(
            {
                "error": f"Unknown provider type {ptype!r}. Install its app first. "
                f"Currently registered: {_addable}"
            },
            status=400,
        )

    async with _get_config_lock():
        path = config_path()
        try:
            data = _json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            data = {}

        providers = data.setdefault("providers", [])
        if any(p.get("name") == name for p in providers):
            return web.json_response({"error": f"Provider '{name}' already exists"}, status=409)

        entry: dict = {"name": name, "type": ptype, "model": model}
        if options:
            entry["options"] = options
        providers.append(entry)

        atomic_write(path, _json.dumps(data, indent=2) + "\n", fsync=True)

    from personalclaw.llm.registry import (
        ProviderEntry,
        canonical_provider_type,
        get_default_registry,
    )

    registry = get_default_registry()
    try:
        # Single source of truth for the branded-alias → base-type collapse (shared
        # with the config sync + discovery handlers). Phase B replaces the branded
        # aliases with dedicated apps, shrinking this to the two protocol types.
        registry_type = canonical_provider_type(ptype)
        cap = registry.capability_of(registry_type)
        entry_options = dict(options or {})
        if ptype != registry_type:
            entry_options["_original_type"] = ptype
        new_entry = ProviderEntry(
            name=name,
            type=registry_type,
            model=model,
            options=entry_options,
            credential=None,
            declared_capabilities=cap.capabilities,
        )
        registry.register_entry(new_entry)
    except Exception:
        pass

    _refresh_media_registries()
    return web.json_response({"ok": True, "name": name})


def _refresh_media_registries() -> None:
    """Drop the typed STT/TTS/image-gen registries so a config change re-reads.

    Remote STT/TTS/image adapters are built from config.json providers at first
    resolution; clearing the registries makes a newly added/removed/edited
    OpenAI-family endpoint selectable as the active voice/image model without a
    gateway restart.
    """
    from personalclaw.image_gen.registry import refresh_providers as _img_refresh
    from personalclaw.stt.registry import refresh_providers as _stt_refresh
    from personalclaw.tts.registry import refresh_providers as _tts_refresh
    from personalclaw.video_gen.registry import refresh_providers as _vid_refresh

    _stt_refresh()
    _tts_refresh()
    _img_refresh()
    _vid_refresh()
    # Re-surface config-based downloadable providers (ollama) as local-model providers
    # so a newly added/edited endpoint gets its download card without a restart.
    try:
        from personalclaw.local_models.registry import register_config_model_managers

        register_config_model_managers()
    except Exception:
        pass


async def api_provider_update(request: web.Request) -> web.Response:
    """PUT /api/model-providers/{name} — update a provider's model, endpoint, or options."""
    import dataclasses as _dataclasses
    import json as _json

    from personalclaw.config.loader import config_path
    from personalclaw.dashboard.handlers.agents import _get_config_lock

    name = request.match_info["name"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    async with _get_config_lock():
        path = config_path()
        try:
            data = _json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            data = {}

        providers = data.get("providers", [])
        target = None
        for p in providers:
            if p.get("name") == name:
                target = p
                break
        if not target:
            return web.json_response({"error": "not found"}, status=404)

        if "model" in body:
            target["model"] = body["model"]
        if "options" in body:
            target.setdefault("options", {}).update(body["options"])
        if "type" in body:
            target["type"] = body["type"]

        atomic_write(path, _json.dumps(data, indent=2) + "\n", fsync=True)

    from personalclaw.llm.registry import get_default_registry

    registry = get_default_registry()
    try:
        existing = registry.get_entry(name)
        # ProviderEntry is a frozen dataclass — build a replacement and re-register
        # (register_entry is idempotent-by-name, so drop the old one first).
        updated = _dataclasses.replace(
            existing,
            model=target.get("model", existing.model),
            options=target.get("options", existing.options),
        )
        registry.unregister_entry(name)
        registry.register_entry(updated)
    except Exception:
        pass

    _refresh_media_registries()
    return web.json_response({"ok": True, "name": name})


async def api_provider_delete(request: web.Request) -> web.Response:
    """DELETE /api/model-providers/{name} — remove a provider from config."""
    import json as _json

    from personalclaw.config.loader import config_path
    from personalclaw.dashboard.handlers.agents import _get_config_lock

    name = request.match_info["name"]

    async with _get_config_lock():
        path = config_path()
        try:
            data = _json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            data = {}

        providers = data.get("providers", [])
        before = len(providers)
        data["providers"] = [p for p in providers if p.get("name") != name]
        if len(data["providers"]) == before:
            return web.json_response({"error": "not found"}, status=404)

        atomic_write(path, _json.dumps(data, indent=2) + "\n", fsync=True)

    from personalclaw.llm.registry import get_default_registry

    registry = get_default_registry()
    try:
        registry.unregister_entry(name)
    except Exception:
        pass

    # Drop this provider's active-model selections so it stops surfacing as a
    # ghost in the Settings count, the app-wide model dropdowns, and routing.
    # Reads prune defensively too, but this self-heals the file at removal time.
    _drop_provider_active_models(name)
    _refresh_media_registries()

    return web.json_response({"ok": True})


def _drop_provider_active_models(provider_name: str) -> None:
    """Remove every active-model ref (``"<provider>:<model>"``) for a provider."""
    from personalclaw.providers.use_cases import load_active_models, save_active_models

    active = load_active_models()
    changed = False
    for use_case, refs in list(active.items()):
        if not isinstance(refs, list):
            continue
        kept = [r for r in refs if str(r).split(":", 1)[0] != provider_name]
        if len(kept) != len(refs):
            active[use_case] = kept
            changed = True
    if changed:
        save_active_models(active)


async def api_provider_test(request: web.Request) -> web.Response:
    """POST /api/model-providers/{name}/test — test provider connectivity.

    Generic across provider types: builds the entry's ModelCatalog and calls
    ``test_connection()``. Works for a provider still only in config.json (a
    just-created entry not yet in the live registry) by synthesizing a transient
    ProviderEntry from its stored type/options and building the catalog from that.
    A provider type with no catalog registered (its app not loaded) reports a
    benign "no discovery" status rather than erroring."""
    import json as _json

    from personalclaw.config.loader import config_path
    from personalclaw.llm.registry import ProviderEntry, get_default_registry

    name = request.match_info["name"]

    # Try the live registry first; fall back to the config file for an entry that
    # exists on disk but isn't registered yet.
    registry = get_default_registry()
    entry = next((e for e in registry.list_entries() if e.name == name), None)
    if entry is None:
        try:
            data = (
                _json.loads(config_path().read_text(encoding="utf-8"))
                if config_path().exists()
                else {}
            )
            p = next((p for p in data.get("providers", []) if p.get("name") == name), None)
            if not p:
                return web.json_response({"error": "not found"}, status=404)
            options = p.get("options") or {}
            # ``_original_type`` preserves the branded config type; the registry type
            # (openai/anthropic/…) is what a catalog is keyed on.
            ptype = options.get("_original_type") or p.get("type", "")
            entry = ProviderEntry(name=name, type=ptype, model=p.get("model", ""), options=options)
        except Exception:
            return web.json_response({"error": "not found"}, status=404)

    catalog = registry.build_catalog(entry)
    if catalog is None:
        # No discovery/connectivity probe for this provider (e.g. its app isn't
        # loaded, or it authenticates purely via environment/SDK chain).
        return web.json_response(
            {
                "ok": True,
                "status": "no_probe",
                "message": "No connectivity probe available for this provider type",
            }
        )

    result = await catalog.test_connection()
    if result.ok:
        msg = result.detail or (
            f"Connected — {result.model_count} model(s) available"
            if result.model_count is not None
            else "Connected"
        )
        return web.json_response({"ok": True, "status": "connected", "message": msg})
    return web.json_response(
        {
            "ok": False,
            "status": "error",
            "message": result.detail or "Connection test failed",
        }
    )
