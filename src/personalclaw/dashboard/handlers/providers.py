"""Model-provider and agent-provider management API handlers.

Routes:
    GET    /api/model-providers              — list configured model-provider entries
    POST   /api/model-providers              — create a model provider
    PUT    /api/model-providers/{name}       — update a model provider
    DELETE /api/model-providers/{name}       — delete a model provider
    POST   /api/model-providers/{name}/test  — test a provider's connectivity
    GET    /api/model-providers/{name}/models, /search; POST .../pull, .../models/delete
    GET    /api/agent-providers              — list agent runtimes (native + acp:<cli>)
    POST   /api/agent-providers/{id}/test    — start one runtime's CLI once (the user's Test)
    GET    /api/agent-providers/{id}/agents  — the agents a runtime offered at its last Test
    GET    /api/agent-runners                — runner catalog rows + the health last checked
    POST   /api/agent-runners/{id}/check     — run one runner's CLI for its version

Only the two POSTs start another agent's CLI, and only because the user pressed Test or
Check: every GET here answers from what is on disk and from what the user's last Test or
Check found (``agents/runtime_tests.py``, ``agents/runners.py``).
"""

import asyncio
import contextlib
import functools
import logging
from typing import Any

from aiohttp import web

from personalclaw.config import secret_refs
from personalclaw.config.loader import ConfigWriteError
from personalclaw.config.transactions import mutate_config_async
from personalclaw.dashboard.handlers._shared import (
    RefusedInConfigTransaction,
    config_write_refusal,
)
from personalclaw.providers.failure_copy import relayed_failure_copy
from personalclaw.request_validation import RequestValidationError, require_string

logger = logging.getLogger(__name__)

# ── /api/model-providers ────────────────────────────────────────────────────────────


def _model_type_apps() -> dict[str, Any]:
    """Installed model-provider apps by the provider TYPE each registers.

    The concrete type an app registers into the LLM registry is its manifest's
    ``providerType`` — NOT ``provider.type``, which is the entity CLASS "model" — with the
    app-name stem as the fallback for a manifest that declares none. The one type→app
    mapping, shared by the Add-instance type list and each instance's settings schema.
    """
    from personalclaw.providers.registry import get_provider_registry, model_provider_type

    apps: dict[str, Any] = {}
    for ext in get_provider_registry().list_by_type("model"):
        ptype = model_provider_type(ext)
        if ptype and ptype != "acp_agent":
            apps.setdefault(ptype, ext)
    return apps


def _declared_type(entry: Any) -> str:
    """The type an instance was created as (a branded alias survives in ``_original_type``)."""
    return str((entry.options or {}).get("_original_type") or entry.type)


def _sensitive_settings(schema: dict[str, Any], names: Any) -> set[str]:
    """Settings that hold a secret: declared ``x-meta.sensitive``, or credential-named."""
    from personalclaw.apps.secret_fields import is_credential_field_name, sensitive_field_names

    return sensitive_field_names(schema) | {n for n in names if is_credential_field_name(n)}


def _wire_settings(options: dict[str, Any] | None, schema: dict[str, Any]) -> tuple[dict, list]:
    """An instance's settings for the wire: secrets masked, core bookkeeping dropped.

    Returns ``(settings, secret_set)`` — ``secret_set`` names the secret settings that hold
    a value, so the edit form can say "a key is saved" without being handed it. ``options``
    is the STORED form where there is one, so a ``{{secret:…}}`` reference is masked whatever
    its field is called — a hand-typed one in a field no schema calls secret included.
    """
    from personalclaw.apps.secret_fields import SECRET_MASK

    settings = {k: v for k, v in (options or {}).items() if not str(k).startswith("_")}
    secret_set: list[str] = []
    held = {k for k, v in settings.items() if secret_refs.ref_key(v) is not None}
    for key in _sensitive_settings(schema, settings) | held:
        if key in settings and str(settings[key] or ""):
            settings[key] = SECRET_MASK
            secret_set.append(key)
    return settings, sorted(secret_set)


def _key_in_store(entry: Any) -> bool:
    """Whether the instance authenticates with a credential from Settings → Secrets.

    That is the instance's declared ``credential`` — a reference to a stored credential the
    instance does NOT own, so removing the instance leaves it for whatever else uses it (the
    keys the instance itself saved are ``stored_secrets``, and they go with it).
    """
    if not entry.credential:
        return False
    try:
        from personalclaw.config.loader import config_dir
        from personalclaw.llm.credentials import CredentialStore

        # The HOME, not a file: `CredentialStore` reads `<home>/.env` (and the keychain).
        # Passing a file path made it read nothing and raise `KeyError` from `resolve` for
        # every name — swallowed below, so every correctly configured provider reported its
        # credential as absent (#2217).
        return bool(CredentialStore(config_dir()).resolve(entry.credential).secret)
    except Exception:
        return False


async def api_providers_list(request: web.Request) -> web.Response:
    """GET /api/model-providers — every configured model-provider instance.

    Returns ``{providers: [{name, type, declared_type, model, capabilities, connection,
    options, secret_set, stored_secrets, key_in_store}]}``:

    * ``connection`` is MEASURED (``providers/connection.py``) — ``checking`` until the
      instance's first background test lands. It replaced a badge derived from whether a
      credential was present, which read "Configured" for an instance with no key at all.
    * ``options`` are the instance's settings with every secret masked; ``secret_set``
      names the secret settings that hold a value.
    * ``stored_secrets`` names the option fields whose value this instance keeps in the
      credential store — by NAME, so the delete dialog can say the key goes with it.
    * ``key_in_store`` is True when the instance authenticates with a Settings → Secrets
      credential it references — one removing the instance leaves in place.
    """
    import json as _json

    from personalclaw.config.loader import config_path
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.providers.connection import entry_fingerprint, get_connection_board

    registry = get_default_registry()
    try:
        document = _json.loads(config_path().read_text(encoding="utf-8"))
        records = document.get("providers") if isinstance(document, dict) else None
    except (OSError, ValueError):
        records = None
    stored_options = {
        str(p.get("name")): p.get("options") or {}
        for p in (records if isinstance(records, list) else [])
        if isinstance(p, dict)
    }
    board = get_connection_board()
    type_apps = _model_type_apps()

    result: list[dict[str, Any]] = []
    for entry in registry.list_entries():
        # Agent-runtime entries (acp_agent / acp:<cli>) are NOT model providers —
        # they have no model catalog and no endpoint, so they belong only under
        # "Agent Providers" (/api/agent-providers). Listing them here surfaced a
        # bogus card per ACP runtime whose Test said "no endpoint configured" and
        # Models said "no model found". Exclude them from the model-provider list.
        if entry.type == "acp_agent":
            continue
        # A FLOOR entry is not a connection anyone configured — the app that ships it registers
        # it in memory once its model is on disk, and the Providers panel already shows it, with
        # its download card, under Native (bundled). Listed here it rendered a second time as a
        # "Remote (multi-instance)" instance whose Edit and Delete could only answer 404: there
        # is no config.json row behind it. `floor` is the registering app's declaration, so this
        # names no app.
        if getattr(entry, "floor", False):
            continue
        declared = _declared_type(entry)
        app = type_apps.get(declared) or type_apps.get(entry.type)
        schema = (app.provider_config.settingsSchema or {}) if app is not None else {}
        # The document's stored form when the entry has one: the registry's copy holds the
        # VALUES, and a reference shown resolved is a credential handed out.
        stored = stored_options.get(entry.name)
        settings, secret_set = _wire_settings(
            stored if isinstance(stored, dict) else entry.options, schema
        )

        # Get static capability descriptor for this type
        try:
            cap = registry.capability_of(entry.type)
            capabilities = sorted(c.value for c in cap.capabilities)
        except Exception:
            capabilities = sorted(c.value for c in entry.declared_capabilities)

        connection = board.read(
            entry.name, entry_fingerprint(entry), functools.partial(registry.build_catalog, entry)
        )
        result.append(
            {
                "name": entry.name,
                "type": entry.type,
                "declared_type": declared,
                "model": entry.model,
                "capabilities": capabilities,
                "connection": connection.to_wire(),
                "options": settings,
                "secret_set": secret_set,
                "stored_secrets": secret_refs.owned_field_names(
                    stored_options.get(entry.name), secret_refs.provider_owner(entry.name)
                ),
                "key_in_store": _key_in_store(entry),
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
    types: list[dict[str, Any]] = []
    for ptype, ext in _model_type_apps().items():
        cfg = ext.provider_config
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


def _runtime_entries() -> list[Any]:
    """Every registry entry that is an agent RUNTIME (an ``acp:<cli>`` a bundle registered)."""
    from personalclaw.agents.registry import get_agent_provider_class
    from personalclaw.llm.registry import get_default_registry

    return [
        entry
        for entry in get_default_registry().list_entries()
        if get_agent_provider_class("acp" if entry.type == "acp_agent" else entry.type) is not None
    ]


def _runtime_row(entry: Any, readiness: dict[str, Any]) -> dict[str, Any]:
    from personalclaw.agents import runtime_tests

    # entry.name is already the canonical runtime id ("acp:<cli>") — used directly rather than
    # re-derived from the command basename (which would mislabel an adapter like
    # claude-agent-acp).
    return {
        "name": entry.name,
        "provider_id": entry.name,
        "type": entry.type,
        "extension": dict(entry.options or {}).get("extension"),
        **readiness,
        # Its ACP adapter is not installed, so it runs through npx, and enabling its app again
        # installs it: the card offers Retry, and says why when the last install failed.
        "adapter_install": runtime_tests.adapter_install(entry),
    }


async def api_agent_providers_list(request: web.Request) -> web.Response:
    """GET /api/agent-providers — the single list of agent runtimes + readiness.

    This is the one source of truth for the "Agent Providers" UI section: the
    AgentProvider *runtime* axis, spanning the in-process ``native`` runtime
    and every ``acp:<cli>`` runtime registered by a removable bundle
    (claude-code / codex / future). Each row carries the runtime's readiness so
    the UI can show a readiness chip, the Test, and the Sign-in terminal when a
    runtime's last Test reported ``needs_login``.

    Returns ``{agent_providers: [{name, provider_id, type, extension, ready,
    state, detail, login_command, tested_at, adapter_install}]}`` where
    ``adapter_install`` is ``{error, at}`` when the runtime's ACP adapter is not installed and
    enabling its app again installs it (``error``/``at`` say why the last install failed, or are
    null when none is on record; nothing tries until the app is enabled again), and
    ``extension`` (when present) is the
    bundle name the row's enable/config card is keyed by, so the frontend can
    merge readiness onto the extension card instead of rendering two sections.

    **A read starts nothing.** An ``acp:<cli>`` row answers from whether its CLI is
    installed and from the user's last Test (``agents/runtime_tests.py``): ``untested``
    until the user presses Test, then that Test's answer and when it ran (``tested_at``).
    """
    from personalclaw.agents import runtime_tests

    # ── native: the always-available in-process runtime ──────────────────
    # It is not a model-registry ProviderEntry (it's resolved per-session by
    # the provider bridge from an agent's definition), so synthesize its row
    # explicitly. It needs no external CLI and no sign-in — always ready.
    result: list[dict[str, Any]] = [
        {
            "name": "native",
            "provider_id": "native",
            "type": "native",
            "extension": "native-agents",
            "ready": True,
            "state": "ready",
            "detail": "In-process agent runtime (no external CLI).",
            "login_command": None,
            "tested_at": None,
            "adapter_install": None,
        }
    ]
    # ── acp:<cli> runtimes registered by bundles ─────────────────────────
    result.extend(_runtime_row(e, runtime_tests.readiness(e)) for e in _runtime_entries())
    return web.json_response({"agent_providers": result})


async def api_agent_provider_test(request: web.Request) -> web.Response:
    """POST /api/agent-providers/{id}/test — start one agent CLI once, because you asked.

    The one route that starts another agent's CLI, and only the one it names: ACP
    ``initialize`` and one empty session, then the CLI is stopped. What that found —
    ready, needs sign-in, too slow, or its error, and the agents the runtime offered in
    that session — is recorded (``agents/runtime_tests.py``), and the runtime's card and the
    chat's agent picker read from it until the next Test. A CLI that is not installed is not
    started; its row says so. A second press while a Test runs waits for that Test.

    Returns ``{agent_provider: <row>}``, the row ``GET /api/agent-providers`` lists.
    """
    from personalclaw.agents import runtime_tests
    from personalclaw.http_errors import json_error

    runtime_id = request.match_info.get("id", "")
    entry = next((e for e in _runtime_entries() if e.name == runtime_id), None)
    if entry is None:
        return json_error(
            "not_found",
            message=(
                f"No agent CLI is set up as {runtime_id!r} here, so there is nothing to test."
            ),
            status=404,
        )
    readiness = await runtime_tests.run_test(entry)
    return web.json_response({"agent_provider": _runtime_row(entry, readiness)})


# ── /api/agent-providers/{id}/agents ──────────────────────────────────────────


async def api_agent_provider_agents(request: web.Request) -> web.Response:
    """GET /api/agent-providers/{id}/agents — the agents a runtime offered at its last Test.

    Starts nothing. The list is the one the user's last Test of the runtime recorded, for
    the command it runs now (``agents/runtime_tests.py``); a runtime nobody has tested lists
    none, with ``tested_at: null``. The chat picker reads it (default-dialect personas /
    claude effort-agents).

    Returns ``{agents: [{id, name, runtime, description, provider_agent,
    reasoning_effort, models, supported_efforts}], permission_modes: [...], tested_at}``
    where ``permission_modes`` are the runtime's NATIVE permission modes (raw capability for
    the trust-ladder grey-out). ``native`` returns ``[]``.

    A last Test that did not list the agents — it failed, or its answer could not be read — is
    answered as that failure (``agent_discovery_failed``, 502, the message says why), never as
    ``agents: []``, which is the answer "this runtime offers none".
    """
    from personalclaw.agents import runtime_tests
    from personalclaw.http_errors import json_error
    from personalclaw.llm.registry import get_default_registry

    runtime_id = request.match_info.get("id", "")

    # native has no discovered agents (its agents are PersonalClaw's own defs).
    if runtime_id == "native":
        return web.json_response({"agents": [], "permission_modes": [], "tested_at": None})

    try:
        entry = get_default_registry().get_entry(runtime_id)
    except Exception:
        return web.json_response({"error": f"unknown runtime {runtime_id!r}"}, status=404)
    if entry.type != "acp_agent":
        return web.json_response({"error": f"{runtime_id!r} is not an ACP runtime"}, status=400)

    last = runtime_tests.last_test(entry)
    failure = runtime_tests.discovery_failure(entry)
    if failure is not None:
        # The agents are unknown, and "[]" would say the runtime offers none.
        return json_error(
            "agent_discovery_failed",
            message=(
                f"Couldn't list the agents {runtime_tests.runtime_label(entry)} offers: its last "
                f"Test {failure}."
            ),
            status=502,
        )
    return web.json_response(
        {
            "agents": runtime_tests.tested_agents(entry) or [],
            "permission_modes": _runtime_permission_modes(dict(entry.options or {})),
            "tested_at": last.get("tested_at") if last else None,
        }
    )


def declared_efforts(runtime_id: str) -> list[str] | None:
    """The reasoning-effort values *runtime_id* DECLARED, or ``None`` when unknown.

    Starts nothing: it reads the agents the runtime offered at the user's last Test
    (``agents/runtime_tests.py``), which is the list the composer's pill was drawn from, so
    the set checked on the write path is exactly the set the user saw.

    **An empty list and ``None`` are different facts and must not be collapsed.**
    ``[]`` is a *declaration* — the backend was asked and reported no effort axis (codex:
    ``supported_efforts: []``), so pinning an effort is refusable. ``None`` is an
    *absence of information* — the runtime has not been tested, or its Test found no agent
    — and a caller must fall back to a format check rather than refuse a bind it cannot
    judge. ``supported_efforts`` is computed once per runtime and attached identically to
    every agent (``AcpAgentProvider.agents_from_snapshot``), so this is a runtime-level
    question and needs no per-agent disambiguation.
    """
    from personalclaw.agents import runtime_tests
    from personalclaw.llm.registry import get_default_registry

    try:
        entry = get_default_registry().get_entry(runtime_id)
    except Exception:
        return None
    agents = runtime_tests.tested_agents(entry)
    if not agents:
        return None  # never tested, or a Test that listed no agent: unknown, not "none"
    first = agents[0]
    if "supported_efforts" not in first:
        return None  # a record shape without the field — unknown, not empty
    # The rows are the backend's VERBATIM option dicts (``{"value", "label", …}``), the
    # same shape the composer's pill renders and `record_capabilities` reads `value` from.
    # Stringifying a row instead of reading `value` would compare an effort against
    # "{'value': 'low', …}" and refuse every legitimate bind.
    out: list[str] = []
    for row in first.get("supported_efforts") or []:
        value = str(row.get("value") or "") if isinstance(row, dict) else str(row or "")
        if value:
            out.append(value)
    return out


def _runtime_permission_modes(options: dict) -> list[str]:
    """The runtime's native permission modes (capability for the trust grey-out).

    A static per-dialect fact, so nothing is started to learn it: Zed adapters expose the
    5-mode axis; the default dialect exposes none."""
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
        logger.warning("model discovery failed for provider %r", name, exc_info=True)
        return web.json_response({"models": [], "error": relayed_failure_copy(exc)})
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
        logger.warning("catalog search failed for provider %r", name, exc_info=True)
        return web.json_response({"results": [], "error": relayed_failure_copy(exc)})
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
        logger.warning("model detail failed for provider %r", name, exc_info=True)
        return web.json_response({"error": relayed_failure_copy(exc)}, status=500)

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
        logger.warning("model pull stream failed", exc_info=True)
        try:
            await resp.write((_json.dumps({"error": relayed_failure_copy(exc)}) + "\n").encode())
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
        logger.warning("model delete failed for provider %r", name, exc_info=True)
        return web.json_response({"error": relayed_failure_copy(exc)}, status=500)
    return web.json_response({"ok": True, "model": model})


# ── /api/credentials ──────────────────────────────────────────────────────────


async def api_provider_create(request: web.Request) -> web.Response:
    """POST /api/model-providers — add a new model provider to config."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    # Shared validator, this module's envelope — see `lexicon/handlers.py`. This module answers
    # flat in thirty-one places (including this door's own four) and uses `json_error` in none,
    # so a nested refusal for `name` alone would be the odd one out inside one endpoint.
    try:
        name = require_string(body, "name")
        ptype = require_string(body, "type")
    except RequestValidationError as exc:
        return web.json_response({"error": exc.message}, status=exc.status)
    model = body.get("model", "")
    options = body.get("options", {})
    if not isinstance(options, dict):
        return web.json_response({"error": "options must be a JSON object"}, status=400)
    problem = _endpoint_refusal(options)
    if problem:
        return web.json_response({"error": problem}, status=400)

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

    # `None` is the client's explicit "leave this unset" (see api_provider_update's clear
    # semantics) — on a first create there is nothing yet to clear, so a `None` here means only
    # "store nothing for it", never a literal JSON `null` on disk.
    options = {k: v for k, v in options.items() if v is not None} if options else {}
    stored_secrets = False

    def _create(data: dict) -> dict:
        nonlocal stored_secrets
        providers = data.get("providers")
        if not isinstance(providers, list):
            providers = data["providers"] = []
        if any(isinstance(p, dict) and p.get("name") == name for p in providers):
            raise RefusedInConfigTransaction(
                web.json_response({"error": f"Provider '{name}' already exists"}, status=409)
            )
        # The key the user typed goes to the credential store; the document gets a
        # `{{secret:…}}` reference to it. Writing `options` verbatim is how the key landed in
        # config.json in plaintext — world-readable, and in every snapshot and export.
        try:
            stored = secret_refs.store_provider_options(name, ptype, options)
        except ValueError as exc:
            raise RefusedInConfigTransaction(
                web.json_response({"error": relayed_failure_copy(exc)}, status=400)
            ) from exc
        stored_secrets = True
        entry: dict = {"name": name, "type": ptype, "model": model}
        if stored:
            entry["options"] = stored
        providers.append(entry)
        return entry

    # In the config transaction: the duplicate check and the append are one step no other
    # writer can come between, and an unreadable config.json is refused rather than read as
    # `{}` — which wrote the new provider over every other provider and setting.
    try:
        entry = await mutate_config_async(_create)
    except RefusedInConfigTransaction as refused:
        return refused.answer()
    except Exception as exc:
        # The transaction failed, so nothing references the secret it stored: do not leave it
        # behind. `Exception`, not `BaseException`: a cancelled request does not stop the
        # write already running on its thread, and purging then would orphan the reference.
        if stored_secrets:
            secret_refs.purge([secret_refs.provider_owner(name).prefix])
        if isinstance(exc, ConfigWriteError):
            return config_write_refusal(exc)
        raise

    # The entry is built from the record as STORED — a secret field a `{{secret:…}}` reference —
    # resolved against this provider's own credentials, exactly as the boot sync builds it.
    from personalclaw.llm.registry import register_config_record

    register_config_record(entry)

    from personalclaw.providers.connection import get_connection_board

    get_connection_board().forget(name)
    _refresh_media_registries()
    return web.json_response({"ok": True, "name": name})


def _endpoint_refusal(options: dict[str, Any]) -> str | None:
    """Why the endpoint in ``options`` cannot be saved, or ``None``.

    Refused at write time: a malformed endpoint used to be saved and only fail later, as
    ``not%20a%20url/api/tags`` — the HTTP client's percent-encoded echo of it. So is an address
    under ``base_url``, the other name some clients give it: no provider reads that option, and
    one saved there was sent nowhere while the instance went to its default address.
    """
    from personalclaw.llm.registry import ENDPOINT_OPTION
    from personalclaw.providers.failure_copy import endpoint_problem

    if options.get("base_url") is not None:
        return (
            f"An instance's address is its {ENDPOINT_OPTION!r} option; 'base_url' is not read. "
            f"Save the address as {ENDPOINT_OPTION!r}."
        )
    value = options.get(ENDPOINT_OPTION)
    if isinstance(value, str) and value.strip():
        return endpoint_problem(value)
    return None


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

    name = request.match_info["name"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    incoming = body.get("options", {})
    if not isinstance(incoming, dict):
        return web.json_response({"error": "options must be a JSON object"}, status=400)
    problem = _endpoint_refusal(incoming)
    if problem:
        return web.json_response({"error": problem}, status=400)

    from personalclaw.apps.secret_fields import SECRET_MASK

    def _update(data: dict) -> tuple[dict, dict | None]:
        providers = data.get("providers")
        target = None
        for p in providers if isinstance(providers, list) else []:
            if isinstance(p, dict) and p.get("name") == name:
                target = p
                break
        if not target:
            raise RefusedInConfigTransaction(web.json_response({"error": "not found"}, status=404))

        if "model" in body:
            target["model"] = body["model"]
        # Before `options`: which fields are secret is the TYPE's app's declaration.
        if "type" in body:
            target["type"] = body["type"]
        if "options" in body:
            # A plain `dict.update()` can only ADD or OVERWRITE a key that is PRESENT in
            # the incoming options — it has no way to express "remove this key", so an
            # emptied credential field the client simply omits (as a caller reasonably
            # would for "nothing to say about it") could never clear a previously stored
            # value; the old key survived every PATCH that didn't re-send it (#3554). A
            # `None` value is the client's explicit "clear this field", distinct from the
            # key being absent from the payload at all (leave whatever is stored alone) —
            # so absence still means "unchanged" and only an explicit `null` deletes.
            #
            # The merge runs on the STORED options, and the result is stored back through the
            # credential store: a rotated key replaces the stored one, a cleared key is deleted
            # from the store, an untouched one stays the reference it is — no value is read out
            # to be merged. The list route hands secrets out MASKED, so the mask arriving back
            # is the other spelling of "unchanged" — storing it would replace a working key with
            # eight dots. A reference to a credential another owner holds is refused by the
            # store, with what to do instead.
            previous = target.get("options") or {}
            merged = dict(previous)
            for key, value in incoming.items():
                if value is None:
                    merged.pop(key, None)
                elif value != SECRET_MASK:
                    merged[key] = value
            try:
                target["options"] = secret_refs.store_provider_options(
                    name, str(target.get("type") or ""), merged, previous
                )
            except ValueError as exc:
                raise RefusedInConfigTransaction(
                    web.json_response({"error": relayed_failure_copy(exc)}, status=400)
                ) from exc

        # Resolved BEFORE the write: options already on disk that name another owner's
        # credential refuse the whole edit, rather than a saved change the registry then
        # cannot load.
        try:
            logical_options = (
                secret_refs.resolve(target["options"], owner=secret_refs.provider_owner(name))
                if "options" in target
                else None
            )
        except secret_refs.ForeignSecretReference as exc:
            raise RefusedInConfigTransaction(
                web.json_response({"error": relayed_failure_copy(exc)}, status=400)
            ) from exc
        return target, logical_options

    # In the config transaction: the edit is made to the record as it is at the write, and an
    # unreadable config.json is refused rather than read as `{}`, which answered 404 for a
    # provider the file holds.
    try:
        target, logical_options = await mutate_config_async(_update)
    except RefusedInConfigTransaction as refused:
        return refused.answer()
    except ConfigWriteError as exc:
        return config_write_refusal(exc)

    from personalclaw.llm.registry import get_default_registry
    from personalclaw.providers.connection import get_connection_board

    registry = get_default_registry()
    try:
        existing = registry.get_entry(name)
        # ProviderEntry is a frozen dataclass — build a replacement and re-register
        # (register_entry is idempotent-by-name, so drop the old one first). The entry
        # carries the LOGICAL options: a factory reads `api_key` from it, not a reference.
        # Core's own bookkeeping keys (``_original_type``) live only on the entry, never in
        # config.json, so they are carried over rather than dropped with the old entry.
        bookkeeping = {k: v for k, v in (existing.options or {}).items() if str(k).startswith("_")}
        updated = _dataclasses.replace(
            existing,
            model=target.get("model", existing.model),
            options=(
                existing.options if logical_options is None else {**bookkeeping, **logical_options}
            ),
        )
        registry.unregister_entry(name)
        registry.register_entry(updated)
    except Exception:
        pass

    # What was measured belongs to the settings that were just replaced.
    get_connection_board().forget(name)
    _refresh_media_registries()
    return web.json_response({"ok": True, "name": name})


async def api_provider_delete(request: web.Request) -> web.Response:
    """DELETE /api/model-providers/{name} — remove a provider from config.

    Removing the provider whose model Embedding is bound to binds the next model in its chain, so
    what the removed one embedded is another model's now: the removal takes the one path every
    change of the embedding model takes (``embedding_reindex.reindex_for_binding``).
    """
    from personalclaw.embedding_providers.registry import BoundEmbedding

    name = request.match_info["name"]
    embedding_before = BoundEmbedding.ref()

    def _delete(data: dict) -> None:
        providers = data.get("providers")
        providers = providers if isinstance(providers, list) else []
        kept = [p for p in providers if not (isinstance(p, dict) and p.get("name") == name)]
        if len(kept) == len(providers):
            raise RefusedInConfigTransaction(web.json_response({"error": "not found"}, status=404))
        data["providers"] = kept

    def _purge() -> None:
        # The key this instance owned goes with it — once nothing on disk refers to it, and
        # before a create of the same name (waiting for the lock) stores a new one. A reference
        # it held to a Secrets-panel credential is not owned, so that credential stays for
        # whatever else uses it.
        secret_refs.purge([secret_refs.provider_owner(name).prefix])

    # In the config transaction: an unreadable config.json is refused rather than read as `{}`
    # and answered 404 for a provider the file holds.
    try:
        await mutate_config_async(_delete, on_written=_purge)
    except RefusedInConfigTransaction as refused:
        return refused.answer()
    except ConfigWriteError as exc:
        return config_write_refusal(exc)

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
    from personalclaw.providers.connection import get_connection_board

    get_connection_board().forget(name)
    _refresh_media_registries()
    if BoundEmbedding.ref() != embedding_before:
        from personalclaw.dashboard.handlers.embedding_reindex import (
            schedule_reindex_for_binding,
        )

        schedule_reindex_for_binding(request.app)

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
            try:
                options = secret_refs.resolve(
                    p.get("options") or {}, owner=secret_refs.provider_owner(name)
                )
            except secret_refs.ForeignSecretReference as exc:
                return web.json_response({"error": relayed_failure_copy(exc)}, status=400)
            # ``_original_type`` preserves the branded config type; the registry type
            # (openai/anthropic/…) is what a catalog is keyed on.
            ptype = options.get("_original_type") or p.get("type", "")
            entry = ProviderEntry(
                name=name,
                type=ptype,
                model=p.get("model", ""),
                options=options,
                credential=p.get("credential") or None,
            )
        except Exception:
            return web.json_response({"error": "not found"}, status=404)

    from personalclaw.providers.connection import (
        CONNECTED,
        UNTESTABLE,
        entry_fingerprint,
        get_connection_board,
        measure,
    )

    # The one inline connection test: the same measurement the background checks run, and its
    # answer is RECORDED, so the card, the Models picker and the next list all agree with it.
    # No catalog registered = no connectivity probe for this type (its app isn't loaded, or it
    # authenticates purely via the environment/SDK chain) — reported as such, never as a pass.
    connection = await measure(registry.build_catalog(entry))
    get_connection_board().record(name, entry_fingerprint(entry), connection)
    if connection.state == UNTESTABLE:
        status = "no_probe"
    else:
        status = "connected" if connection.state == CONNECTED else "error"
    return web.json_response(
        {
            "ok": connection.state in (CONNECTED, UNTESTABLE),
            "status": status,
            "message": connection.detail,
            "connection": connection.to_wire(),
        }
    )


# ── /api/agent-runners ────────────────────────────────────────────────────────


async def api_agent_runners_list(request: web.Request) -> web.Response:
    """GET /api/agent-runners — the runner catalog, with the health each one was last checked at.

    One row per cataloged runner: the definition, the health its last Check MEASURED
    (``ok``/``version``/``latency_ms``/``error``/``checked_at``), the capability matrix a Test's
    handshake recorded, the adapter-provenance verdict the unattended-spawn gate reads, and
    ``set_up`` — whether an installed agent app or a provider entry of the owner's registers
    its runtime here.

    Starts nothing, so the Settings surface paints at once and never fabricates a value for
    a runner nobody checked: ``health: null`` means "never checked", not "fine". A runner's
    Check (``POST /api/agent-runners/{id}/check``) is the one thing that runs its CLI.
    """
    from personalclaw.agents import runners as runner_catalog

    loop = asyncio.get_running_loop()
    # A read of small files and of the adapter's install records, off the loop all the same.
    rows = await loop.run_in_executor(None, runner_catalog.runner_rows)
    return web.json_response({"runners": [row.to_dict() for row in rows]})


async def api_agent_runner_check(request: web.Request) -> web.Response:
    """POST /api/agent-runners/{id}/check — run one runner's CLI for its version, because you asked.

    Runs ``<bin> <version_args>`` (``--version`` for every shipped row) and nothing else, for
    the ONE runner it names, and only one that is set up here. A CLI nothing set up — no
    installed agent app, no provider entry of the owner's — is refused with
    ``runner_not_set_up`` and never run, not even for its version. A definition of yours that
    waits for your Allow here (``runner_grants``) is refused with ``runner_waiting``.

    Returns ``{runner: <row>}``, the row ``GET /api/agent-runners`` lists, with this Check's
    measured health.
    """
    from personalclaw.agents import runners as runner_catalog
    from personalclaw.http_errors import json_error

    runner_id = request.match_info.get("id", "")
    defn = runner_catalog.catalog().get(runner_id)
    if defn is None:
        return json_error(
            "not_found", message=f"No runner {runner_id!r} is in the catalog.", status=404
        )
    if not runner_catalog.is_set_up(defn):
        return json_error(
            "runner_not_set_up",
            message=(
                f"Nothing sets {defn.display_name} up here — no installed agent app or provider "
                f"entry registers {defn.runtime_id} — so PersonalClaw does not run its CLI."
            ),
            status=409,
        )
    from personalclaw.agents import runner_grants

    if not runner_grants.allowed(defn):
        return json_error("runner_waiting", message=runner_grants.WAITING_REASON, status=409)
    loop = asyncio.get_running_loop()
    # One subprocess with a bounded budget; off the loop so a slow CLI stalls nothing else.
    await loop.run_in_executor(None, runner_catalog.probe_runner, defn)
    row = await loop.run_in_executor(None, runner_catalog.runner_row, defn)
    return web.json_response({"runner": row.to_dict()})


async def api_agent_runner_allow(request: web.Request) -> web.Response:
    """POST /api/agent-runners/{id}/allow — the owner's yes to a runner definition that waits.

    A definition under ``runners/`` waits when nothing the owner allowed covers what it runs now
    (``runner_grants``): a device sync brought it, or another machine's edit to it, an agent wrote
    it, or it changed since. The body names ``revision``, the row's ``allow_revision``, so a yes is
    never given to a definition that changed after the page read it (``409 stale_write``). Without
    ``"confirm": true`` the answer is ``400 confirmation_required`` with the sentence saying what
    it runs. Owner-only: no app declaration reaches it (``apps/permissions``). Both answers are
    written to the security audit. Returns ``{runner: <row>}``; it runs nothing.
    """
    from personalclaw.agents import runner_grants
    from personalclaw.agents import runners as runner_catalog
    from personalclaw.http_errors import consent_required, json_error
    from personalclaw.safety_flags import confirm_granted
    from personalclaw.sel import sel

    runner_id = request.match_info.get("id", "")
    defn = runner_catalog.catalog().get(runner_id)
    if defn is None:
        return json_error(
            "not_found", message=f"No runner {runner_id!r} is in the catalog.", status=404
        )
    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_request", message="The body must be a JSON object.", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_request", message="The body must be a JSON object.", status=400)
    caller = request.get("user", "dashboard")
    resource = f"runners.{defn.id}"
    if not runner_grants.allowed(defn):
        if body.get("revision") != runner_grants.revision(defn):
            return json_error(
                "stale_write",
                message=(
                    f"What {defn.display_name} runs changed after this page read it, so nothing "
                    "was allowed. Look at it again, then allow it."
                ),
                status=409,
            )
        if not confirm_granted(body):
            sel().log_api_access(
                caller=caller,
                operation="runner.grant",
                outcome="denied",
                source="dashboard",
                resources=f"{resource}: allowing without confirm",
            )
            return consent_required(
                resource, runner_grants.consent(defn), title=runner_grants.CONSENT_TITLE
            )
        runner_grants.give(defn)
        sel().log_api_access(
            caller=caller,
            operation="runner.grant",
            outcome="success",
            source="dashboard",
            resources=resource,
        )
    loop = asyncio.get_running_loop()
    row = await loop.run_in_executor(None, runner_catalog.runner_row, defn)
    return web.json_response({"runner": row.to_dict()})
