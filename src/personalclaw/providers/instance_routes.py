"""HTTP API routes for multi-instance extension management and use-case settings.

Endpoints:
  GET    /api/providers/{name}/instances          — list instances
  POST   /api/providers/{name}/instances          — create instance
  GET    /api/providers/{name}/instances/{id}     — get instance
  PUT    /api/providers/{name}/instances/{id}     — update instance
  DELETE /api/providers/{name}/instances/{id}     — delete instance
  POST   /api/providers/{name}/instances/{id}/test — test instance connectivity

  GET    /api/models/use-cases/{use_case}/settings — get per-use-case settings
  PUT    /api/models/use-cases/{use_case}/settings — update per-use-case settings

Which model serves a use case is the active selection in ``active_models.json``
(``/api/models/active`` — see ``dashboard/handlers/model_registry.py``); these
``/settings`` routes carry only provider-agnostic behavior (e.g. auto-speak).

Every failure answers with the one wire error envelope
(``{"error": {"code", "message"}}`` via :func:`personalclaw.http_errors.json_error`);
only success keeps its ``{"ok": true, ...}`` shape. A "test connection" failure in
particular is a real 4xx/5xx so the frontend's shared error funnel (which fires on
``!response.ok``) surfaces guidance instead of a raw Python exception.

**A sensitive instance setting is write-only on every one of these routes.** An instance's
``to_dict()`` is the store's DISK serializer, and each of the four config-bearing routes
handed it straight to a response body — so an ``x-meta.sensitive`` field came back in
cleartext on create, on update, on read, and on every list. Measured against a live
gateway with ``openai-models`` (``multiInstance: true``, ``api_key`` sensitive), back when a
model app's instances could live here: all four returned the stored key verbatim, and the
list route returned EVERY configured instance's key in one body. ``openai-tools`` has
exactly that shape today.

**Who reaches these routes — measured, not assumed.** A cold Settings → Providers load calls
``GET .../instances`` once per enabled multi-instance provider; measured, that is
``mcp-tools`` and ``openai-tools``. An app-scoped request reaches only its own provider's
instances, and none of ``mcp-tools``' — those are every server the gateway launches — which the
gateway decides before these handlers run (``apps/permissions.py``). The masking policy is
:mod:`personalclaw.apps.secret_fields` — the same one the two single-config routes use, not
a second copy — reached here through :func:`~personalclaw.apps.secret_fields.mask_instance`.

**A model provider's instances are not here.** They are ``config.json`` ``providers[]``
entries (``/api/model-providers``) — what chat resolves, what discovery lists, and what
Settings → Providers edits. This generic store used to hold a second copy of a model
instance's settings too: reachable only through these routes, invisible to chat, and shown
on the Providers page with no Test, Edit or Remove. Every route below refuses a model app
with ``model_instances_elsewhere``.
"""

import logging
from typing import TYPE_CHECKING

from aiohttp import web

from personalclaw.apps.secret_fields import mask_instance, preserve_unchanged_secrets
from personalclaw.config.secret_refs import ForeignSecretReference
from personalclaw.http_errors import consent_required, json_error
from personalclaw.providers import mcp_instances as _mcp
from personalclaw.providers.failure_copy import connectivity_guidance
from personalclaw.safety_flags import confirm_granted
from personalclaw.security import MaskConflict, redact_for_display
from personalclaw.stale_write import revision_of, stale_write_refusal

if TYPE_CHECKING:
    from personalclaw.mcp_discovery import McpServerInfo

logger = logging.getLogger(__name__)


def _rebuild_agent_config_safe() -> None:
    """Best-effort agent-config rebuild after an instance mutation, so the change
    reaches PersonalClaw sessions without a restart. Never raises."""
    try:
        from personalclaw.agent import rebuild_agent_config

        rebuild_agent_config()
    except Exception:
        logger.warning("rebuild_agent_config failed after instance change", exc_info=True)


def _allow_as_saved(planned: "McpServerInfo") -> None:
    """The owner's yes to an MCP server the card just wrote, given to the server as ``mcp.json``
    holds it, and only when that is what they were asked about (`mcp_grants`)."""
    from personalclaw import mcp_grants

    stored = _mcp.saved(planned.name)
    if stored is not None and mcp_grants.definition(stored) == mcp_grants.definition(planned):
        mcp_grants.give(stored)


def _mcp_changed(
    request: web.Request, name: str, *, config: dict | None, enabled: bool | None
) -> None:
    """After the card wrote the MCP server *name*: one whose definition changed, or that was
    switched on, is probed again, so the Tools page's card says what it does now
    (`mcp_discovery.recheck`); one removed or switched off forgets its last probe."""
    from personalclaw.mcp_discovery import forget_probe, recheck

    if config is None and enabled is not True:
        forget_probe(name)
        return
    tasks = getattr(request.app.get("state"), "_background_tasks", None)
    recheck([name], hold=tasks if isinstance(tasks, set) else None)


async def _refresh_multi_instance_provider_safe(name: str) -> None:
    """Make a generic multiInstance TOOL provider's changed instance set take effect without a
    restart. mcp-tools has its own path (live mcp.json registry); this covers the other
    multiInstance tool apps (e.g. openai-tools), whose type handler builds one provider per
    enabled instance from the instances on disk.

    An instance is the provider's settings, one record each, so the change takes effect the way a
    settings save does (:func:`~personalclaw.providers.routes.apply_saved_settings`): a running
    app's providers are rebuilt, one whose start failed is tried again, and an app that is switched
    off stays off — the instance is saved, and loads when the app is switched on. The rebuilt
    providers' tool names are read before this returns, so an instance refused for a name another
    provider holds says so on the card the change answers to (``tool_providers.registry``). Then
    the agent config is rebuilt. Best-effort; never raises."""
    try:
        from personalclaw.providers.registry import get_provider_registry
        from personalclaw.providers.routes import apply_saved_settings

        ext = get_provider_registry().get(name)
        if not ext or ext.provider_config.type != "tool" or not ext.provider_config.multiInstance:
            return
        await apply_saved_settings(name)
        _rebuild_agent_config_safe()
    except Exception:
        logger.warning(
            "multi-instance provider refresh failed after instance change for %s",
            name,
            exc_info=True,
        )


def register_instance_routes(app: web.Application) -> None:
    """Register instance CRUD and per-use-case settings routes."""
    # Instance management
    app.router.add_get("/api/providers/{name}/instances", handle_list_instances)
    app.router.add_post("/api/providers/{name}/instances", handle_create_instance)
    app.router.add_get("/api/providers/{name}/instances/{id}", handle_get_instance)
    app.router.add_put("/api/providers/{name}/instances/{id}", handle_update_instance)
    app.router.add_delete("/api/providers/{name}/instances/{id}", handle_delete_instance)
    app.router.add_post("/api/providers/{name}/instances/{id}/test", handle_test_instance)

    # Per-use-case behavior settings (provider-agnostic). The active *model* for
    # a use case is set via /api/models/active (model_registry), not here.
    app.router.add_get("/api/models/use-cases/{use_case}/settings", handle_get_use_case_settings)
    app.router.add_put("/api/models/use-cases/{use_case}/settings", handle_set_use_case_settings)


def _revisioned(wire: dict) -> dict:
    """*wire* — an instance as :func:`mask_instance` hands it out — with the ``revision`` of its
    masked ``config`` beside it.

    The config is ONE document — the instance editor sends all of it back — so each read of an
    instance carries the revision its update must name (`personalclaw/stale_write.py`). It takes
    the MASKED record, never the instance, so every route still masks at the point it answers
    and a revision never encodes a secret its reader was not shown.
    """
    return {**wire, "revision": revision_of(wire.get("config") or {})}


def _model_instances_elsewhere(ext: object) -> web.Response | None:
    """400 for a MODEL app: its instances live in ``config.json`` ``providers[]``, not here."""
    if getattr(getattr(ext, "provider_config", None), "type", "") != "model":
        return None
    return json_error(
        "model_instances_elsewhere",
        message="A model provider's instances are added, edited, tested and removed in "
        "Settings → Providers → Model providers (/api/model-providers), not in this store.",
        status=400,
    )


# ── Instance Management ──────────────────────────────────────────────────────


async def handle_list_instances(request: web.Request) -> web.Response:
    """GET /api/providers/{name}/instances"""
    from personalclaw.providers.instances import list_instances
    from personalclaw.providers.registry import get_provider_registry

    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return json_error(
            "not_found", message="No provider is registered under that name.", status=404
        )
    if not ext.provider_config.multiInstance:
        return json_error(
            "bad_request",
            message="This provider does not support multiple instances.",
            status=400,
        )
    elsewhere = _model_instances_elsewhere(ext)
    if elsewhere is not None:
        return elsewhere

    # Every instance is serialized for the WIRE, not for disk: `mask_instance` withholds
    # each `x-meta.sensitive` field. This route is the worst of the four because it is the
    # only one that needs no operator action: N instances' secrets in ONE body, and a cold
    # Settings → Providers load calls it once per enabled multi-instance provider (measured:
    # `mcp-tools`, `openai-tools`).
    schema = ext.provider_config.settingsSchema

    # The mcp-tools card reads/writes the ONE store the native loop consumes
    # (~/.personalclaw/mcp.json), not the generic instance store.
    if name == _mcp.MCP_TOOLS_EXTENSION:
        return web.json_response(
            {"instances": [_revisioned(mask_instance(i, schema)) for i in _mcp.list_instances()]}
        )

    instances = list_instances(name)
    return web.json_response(
        {
            "instances": [_revisioned(mask_instance(inst, schema)) for inst in instances],
        }
    )


async def handle_create_instance(request: web.Request) -> web.Response:
    """POST /api/providers/{name}/instances"""
    from personalclaw.providers.instances import create_instance
    from personalclaw.providers.registry import get_provider_registry
    from personalclaw.providers.settings import ProviderSettings

    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return json_error(
            "not_found", message="No provider is registered under that name.", status=404
        )
    if not ext.provider_config.multiInstance:
        return json_error(
            "bad_request",
            message="This provider does not support multiple instances.",
            status=400,
        )
    elsewhere = _model_instances_elsewhere(ext)
    if elsewhere is not None:
        return elsewhere

    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)

    display_name = str(body.get("display_name", "")).strip()
    config = body.get("config", {})
    if not isinstance(config, dict):
        return json_error(
            "invalid_body", message="The 'config' field must be a JSON object.", status=400
        )

    schema = ext.provider_config.settingsSchema
    # Nothing is stored yet, so there is no secret to preserve — but a client that has been
    # handed a mask by any of the read routes must not be able to persist the SENTINEL as a
    # credential. Against an empty "existing" the shared helper drops such a field, which is
    # the only correct reading of ``SECRET_MASK`` arriving where no value exists. (Spelled by
    # NAME, not as the sentinel's literal value: test_provider_config_secrets.py's one-owner
    # rail is keyed on that value appearing in exactly one module, and it does not — and should
    # not — make an exception for comments.)
    config = preserve_unchanged_secrets(config, {}, schema)

    # Validate against schema
    errors = ProviderSettings.validate(config, schema)
    if errors:
        return json_error(
            "invalid_request",
            message="The instance configuration failed validation.",
            status=422,
            error_extra={"details": errors},
        )

    if name == _mcp.MCP_TOOLS_EXTENSION:
        from personalclaw import mcp_grants

        # The owner sees what the server runs before it first runs, and says yes: asked before
        # anything is written, like the Tools page's Add (`mcp_grants`).
        try:
            server = _mcp.planned(display_name, config, create=True)
        except ValueError as exc:
            return json_error("bad_request", message=str(exc), status=400)
        if not mcp_grants.allowed(server) and not confirm_granted(body):
            return consent_required(
                f"mcp.servers.{server.name}",
                mcp_grants.consent(server, saving=True),
                title=mcp_grants.title(server),
            )
        try:
            inst = _mcp.create_instance(display_name, config)
        except ValueError as exc:
            return json_error("bad_request", message=str(exc), status=400)
        _allow_as_saved(server)
        _rebuild_agent_config_safe()
        _mcp_changed(request, inst.id, config=config, enabled=True)
        return web.json_response({"instance": _revisioned(mask_instance(inst, schema))}, status=201)

    try:
        inst = create_instance(name, display_name=display_name, config=config)
    except ForeignSecretReference as exc:
        return _secret_owned_elsewhere(exc)
    except ValueError as exc:  # a value no credential can hold (a NUL character)
        return _unstorable_secret(exc)
    await _refresh_multi_instance_provider_safe(name)
    return web.json_response({"instance": _revisioned(mask_instance(inst, schema))}, status=201)


def _unstorable_secret(exc: ValueError) -> web.Response:
    return json_error(
        "invalid_request",
        message="The instance configuration failed validation.",
        status=422,
        error_extra={"details": [str(exc)]},
    )


def _secret_owned_elsewhere(exc: ForeignSecretReference) -> web.Response:
    """The instance names a credential another owner holds; the message says what to do."""
    return json_error("secret_owned_elsewhere", message=str(exc), status=400)


async def handle_get_instance(request: web.Request) -> web.Response:
    """GET /api/providers/{name}/instances/{id}"""
    from personalclaw.providers.instances import get_instance
    from personalclaw.providers.registry import get_provider_registry

    name = request.match_info["name"]
    instance_id = request.match_info["id"]

    # The registry lookup is what makes masking possible: which fields are sensitive is a
    # property of the provider's settingsSchema, so without the extension there is no way to
    # know what to withhold. This route was the only one of the six that never resolved the
    # provider, and it answered with the raw stored config as a result. Refusing an
    # unregistered provider — what list/create/update/test already do — is the fail-CLOSED
    # reading; treating "no schema" as "nothing is sensitive" would hand the secret back.
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return json_error(
            "not_found", message="No provider is registered under that name.", status=404
        )
    elsewhere = _model_instances_elsewhere(ext)
    if elsewhere is not None:
        return elsewhere
    schema = ext.provider_config.settingsSchema

    if name == _mcp.MCP_TOOLS_EXTENSION:
        inst = _mcp.get_instance(instance_id)
        if not inst:
            return json_error("not_found", message="No instance exists with that id.", status=404)
        return web.json_response({"instance": _revisioned(mask_instance(inst, schema))})
    inst = get_instance(name, instance_id)
    if not inst:
        return json_error("not_found", message="No instance exists with that id.", status=404)
    return web.json_response({"instance": _revisioned(mask_instance(inst, schema))})


async def handle_update_instance(request: web.Request) -> web.Response:
    """PUT /api/providers/{name}/instances/{id}"""
    from personalclaw.providers.instances import get_instance, update_instance
    from personalclaw.providers.registry import get_provider_registry
    from personalclaw.providers.settings import ProviderSettings

    name = request.match_info["name"]
    instance_id = request.match_info["id"]

    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return json_error(
            "not_found", message="No provider is registered under that name.", status=404
        )
    elsewhere = _model_instances_elsewhere(ext)
    if elsewhere is not None:
        return elsewhere

    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)

    schema = ext.provider_config.settingsSchema
    is_mcp = name == _mcp.MCP_TOOLS_EXTENSION

    # Validate config if provided
    config = body.get("config")
    if config is not None:
        if not isinstance(config, dict):
            return json_error(
                "invalid_body", message="The 'config' field must be a JSON object.", status=400
            )
        # The other half of masking the reads, and NOT optional: the instance editor seeds its
        # form from the list route's config and PUTs the whole dict back, so once that config
        # arrives masked, saving an unrelated field (a model id, an endpoint) would write the
        # mask sentinel over a working API key and silently break the provider. That would be
        # a worse bug than the leak. Runs BEFORE validation so the restored real value is what
        # gets validated — a sentinel has no reason to satisfy a pattern or a minLength.
        existing = _mcp.get_instance(instance_id) if is_mcp else get_instance(name, instance_id)
        if existing is None:
            return json_error("not_found", message="No instance exists with that id.", status=404)
        config = preserve_unchanged_secrets(config, existing.config, schema)
        errors = ProviderSettings.validate(config, schema)
        if errors:
            return json_error(
                "invalid_request",
                message="The instance configuration failed validation.",
                status=422,
                error_extra={"details": errors},
            )
        # 🔴 A CONFIG IS WRITTEN ONLY OVER THE COPY IT WAS BUILT FROM. The editor sends back the
        # whole config it seeded from its read, so a card opened before another tab saved this
        # instance replaced that save with its own copy. Compared in the masked form the reads
        # hand out (`_revisioned`), with no `await` from here to the write below.
        stale = stale_write_refusal(
            request,
            mask_instance(existing, schema)["config"],
            what=f"the settings of instance {instance_id!r}",
        )
        if stale is not None:
            return stale

    if is_mcp:
        from personalclaw import mcp_grants

        # An edit that changes what the server runs is asked about first, like the Tools page's
        # Edit (`mcp_grants`); switching it on or off is not.
        server = None
        try:
            if config is not None:
                try:
                    server = _mcp.planned(instance_id, config, create=False)
                except LookupError:
                    return json_error(
                        "not_found", message="No instance exists with that id.", status=404
                    )
                if not mcp_grants.allowed(server) and not confirm_granted(body):
                    return consent_required(
                        f"mcp.servers.{instance_id}",
                        mcp_grants.consent(server, saving=True),
                        title=mcp_grants.title(server),
                    )
            inst = _mcp.update_instance(instance_id, config=config, enabled=body.get("enabled"))
        except MaskConflict as exc:
            # A masked argument or address whose surroundings changed: nothing is saved.
            return json_error("mask_conflict", message=str(exc), status=409)
        if not inst:
            return json_error("not_found", message="No instance exists with that id.", status=404)
        if server is not None:
            _allow_as_saved(server)
        _rebuild_agent_config_safe()
        _mcp_changed(request, instance_id, config=config, enabled=body.get("enabled"))
        return web.json_response({"instance": _revisioned(mask_instance(inst, schema))})

    try:
        inst = update_instance(
            name,
            instance_id,
            display_name=body.get("display_name"),
            config=config,
            enabled=body.get("enabled"),
        )
    except ForeignSecretReference as exc:
        return _secret_owned_elsewhere(exc)
    except ValueError as exc:
        return _unstorable_secret(exc)
    if not inst:
        return json_error("not_found", message="No instance exists with that id.", status=404)
    await _refresh_multi_instance_provider_safe(name)
    return web.json_response({"instance": _revisioned(mask_instance(inst, schema))})


async def handle_delete_instance(request: web.Request) -> web.Response:
    """DELETE /api/providers/{name}/instances/{id}"""
    from personalclaw.providers.instances import delete_instance
    from personalclaw.providers.registry import get_provider_registry

    name = request.match_info["name"]
    instance_id = request.match_info["id"]
    elsewhere = _model_instances_elsewhere(get_provider_registry().get(name))
    if elsewhere is not None:
        return elsewhere
    if name == _mcp.MCP_TOOLS_EXTENSION:
        if not _mcp.delete_instance(instance_id):
            return json_error("not_found", message="No instance exists with that id.", status=404)
        _rebuild_agent_config_safe()
        _mcp_changed(request, instance_id, config=None, enabled=False)
        return web.json_response({"ok": True})
    deleted = delete_instance(name, instance_id)
    if not deleted:
        return json_error("not_found", message="No instance exists with that id.", status=404)
    await _refresh_multi_instance_provider_safe(name)
    return web.json_response({"ok": True})


def _probe_failure(exc: BaseException, *, context: str) -> web.Response:
    """Turn a connectivity-probe exception into the one wire error envelope.

    The raw exception is logged for the operator; it is NEVER placed in the user
    message (a truncated ``ConnectionRefusedError(...)`` is noise, not guidance).
    Failing to reach the endpoint is an upstream problem (502 ``provider_unreachable``);
    an unusable instance config is the caller's (400 ``provider_config_invalid``); any
    other error is an unexpected, retryable test failure (502 ``provider_test_failed``).
    """
    logger.warning("provider connectivity test failed (%s)", context, exc_info=True)
    # aiohttp wraps the OS-level cause on its connector errors; inspect it when present.
    if isinstance(exc, (ValueError, KeyError)):
        return json_error(
            "provider_config_invalid",
            message="This instance's configuration is incomplete or invalid, so it could "
            "not be tested. Check its settings and try again.",
            status=400,
        )
    guidance = connectivity_guidance(exc)
    if guidance is not None:
        return json_error("provider_unreachable", message=guidance, status=502)
    return json_error(
        "provider_test_failed",
        message="The connection test failed unexpectedly. Check the instance configuration "
        "and try again.",
        status=502,
    )


async def handle_test_instance(request: web.Request) -> web.Response:
    """POST /api/providers/{name}/instances/{id}/test — test connectivity."""
    from personalclaw.providers.instances import get_instance, resolved_config
    from personalclaw.providers.registry import get_provider_registry

    name = request.match_info["name"]
    instance_id = request.match_info["id"]

    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return json_error(
            "not_found", message="No provider is registered under that name.", status=404
        )
    elsewhere = _model_instances_elsewhere(ext)
    if elsewhere is not None:
        return elsewhere

    # mcp-tools instances live in ~/.personalclaw/mcp.json — probe the real
    # server (start → initialize → tools/list) for a true connectivity check. Pressing Test is the
    # owner asking, as Retry on the Tools page is: a server stopped after failing to start again
    # and again is started once more. What it found is what the Tools page's card says too.
    if name == _mcp.MCP_TOOLS_EXTENSION:
        from personalclaw.mcp_discovery import forget_probe, list_servers, probe_server

        target = next((s for s in list_servers() if s.name == instance_id), None)
        if target is None:
            return json_error("not_found", message="No instance exists with that id.", status=404)
        forget_probe(instance_id)
        try:
            probed = await probe_server(target)
        except Exception as exc:
            return _probe_failure(exc, context=f"mcp-tools/{instance_id}")
        if probed.status in ("ok", "ready", "connected"):
            return web.json_response(
                {"ok": True, "message": f"Connected — {len(probed.tools)} tool(s)"}
            )
        logger.warning(
            "mcp-tools instance %s probed not-ready: status=%s error=%s",
            instance_id,
            probed.status,
            probed.error,
        )
        return json_error(
            "provider_test_failed",
            message=redact_for_display(probed.error)
            or "The MCP server did not report ready. Check its "
            + ("URL and headers." if probed.is_remote else "command and configuration."),
            status=502,
        )

    inst = get_instance(name, instance_id)
    if not inst:
        return json_error("not_found", message="No instance exists with that id.", status=404)
    try:
        config = resolved_config(inst)
    except ForeignSecretReference as exc:
        return _secret_owned_elsewhere(exc)

    try:
        from personalclaw.providers.loader import load_factory

        factory = load_factory(ext)
        provider = factory(config)
        if hasattr(provider, "is_available"):
            available = await provider.is_available()
            if available:
                return web.json_response({"ok": True, "message": "Provider available"})
            return json_error(
                "provider_test_failed",
                message="The provider was created but reports it is not available. Check its "
                "configuration and that any backing service is running.",
                status=502,
            )
        return web.json_response({"ok": True, "message": "Provider created successfully"})
    except Exception as exc:
        return _probe_failure(exc, context=f"{name}/{instance_id}")


# ── Per-Use-Case Settings (provider-agnostic behavior) ───────────────────────


async def handle_get_use_case_settings(request: web.Request) -> web.Response:
    """GET /api/models/use-cases/{use_case}/settings

    The settings are ONE document — the PUT below replaces the file — so the read carries the
    ``revision`` that write must name (`personalclaw/stale_write.py`), taken from the very
    settings beside it.
    """
    from personalclaw.providers.use_cases import VALID_USE_CASES, load_use_case_settings

    use_case = request.match_info["use_case"]
    if use_case not in VALID_USE_CASES:
        return json_error("bad_request", message="That is not a recognized use case.", status=400)

    settings = load_use_case_settings(use_case)
    return web.json_response(
        {"use_case": use_case, "settings": settings, "revision": revision_of(settings)}
    )


async def handle_set_use_case_settings(request: web.Request) -> web.Response:
    """PUT /api/models/use-cases/{use_case}/settings

    Replaces the whole file with the body, over the revision named in ``If-Match``.
    """
    from personalclaw.providers.use_cases import (
        VALID_USE_CASES,
        load_use_case_settings,
        save_use_case_settings,
    )

    use_case = request.match_info["use_case"]
    if use_case not in VALID_USE_CASES:
        return json_error("bad_request", message="That is not a recognized use case.", status=400)

    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)

    if not isinstance(body, dict):
        return json_error(
            "invalid_body", message="The request body must be a JSON object.", status=400
        )

    # 🔴 THE FILE IS WRITTEN ONLY OVER THE COPY THE BODY WAS BUILT FROM. Settings → Voice sends
    # `{...settings, <the one it changed>}` from the copy it read, and this same file is written
    # by the routing levers (`routing.policy.set_mode`/`set_pin`) and by channel apps through the
    # SDK (`sdk.channel.save_use_case_settings`, the Slack voice modal) — so a page opened before
    # either wrote replaced their value with its own stale one. No `await` from here to the save.
    stale = stale_write_refusal(
        request, load_use_case_settings(use_case), what=f"the {use_case} settings"
    )
    if stale is not None:
        return stale
    save_use_case_settings(use_case, body)
    saved = load_use_case_settings(use_case)
    return web.json_response(
        {"ok": True, "use_case": use_case, "settings": saved, "revision": revision_of(saved)}
    )
