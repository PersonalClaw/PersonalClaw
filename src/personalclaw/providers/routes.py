"""HTTP API routes for the extension system.

Provides endpoints for:
- Listing extensions with status and type filtering
- Reading/writing per-extension config
- Fetching settings schemas for dynamic UI rendering
- Re-checking whether an extension can run on this machine

A provider has no on/off of its own. It is its app's, so the Settings → Providers switch is the
app's enable and disable (``POST /api/apps/{name}/enable|disable``): the one load and unload
every lifecycle step goes through, on routes only the owner may call.

A provider is its app's to reach, too. An app-scoped request reaches only the provider registered
under its own name: the gateway refuses one naming another before these handlers run
(``apps/permissions.ROUTE_AUTHZ``), and the list answers an app with its own providers only.
"""

import logging
from typing import Any

from aiohttp import web

from personalclaw.apps.secret_fields import mask_secrets, preserve_unchanged_secrets
from personalclaw.config.secret_refs import ForeignSecretReference
from personalclaw.http_errors import json_error
from personalclaw.providers.availability import AVAILABLE, Availability, get_availability_board
from personalclaw.providers.registry import get_provider_registry
from personalclaw.providers.settings import ProviderSettings, load_stored

logger = logging.getLogger(__name__)


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/providers", handle_list_extensions)
    app.router.add_get("/api/providers/{name}", handle_get_extension)
    app.router.add_get("/api/providers/{name}/schema", handle_get_schema)
    app.router.add_get("/api/providers/{name}/config", handle_get_config)
    app.router.add_patch("/api/providers/{name}/config", handle_patch_config)
    app.router.add_post("/api/providers/{name}/availability", handle_recheck_availability)


async def handle_list_extensions(request: web.Request) -> web.Response:
    registry = get_provider_registry()
    type_filter = request.query.get("type")
    # An app lists its own providers and no other: the rest are other apps' and the platform's, and
    # a card says what is installed, whether it runs and why not.
    request_app = request.get("app", "")

    extensions = registry.list_extensions()
    if request_app:
        extensions = [e for e in extensions if e.name == request_app]
    if type_filter:
        extensions = [e for e in extensions if e.provider_config.type == type_filter]

    # A bundle may declare itself unusable on this machine (e.g. its binary isn't
    # installed). The answer comes from the availability board, which measures it in a
    # child process: this route runs no app code, so no hook can stall the gateway.
    board = get_availability_board()
    result: list[dict[str, Any]] = []
    for ext in extensions:
        availability = board.read(ext.name, ext.provider_config.implementation)
        result.append(
            {
                "name": ext.name,
                "displayName": ext.manifest.displayName,
                "description": ext.manifest.description,
                "version": ext.manifest.version,
                "author": ext.manifest.author,
                "enabled": ext.enabled,
                "error": ext.error,
                "availability": availability.to_wire(),
                # A "managed" provider is a user-lifecycle app (first/third-party: install/
                # uninstall is its on/off); a native app is locked-on (no
                # toggle — mandatory). Lets Settings>Providers show the right control:
                # install/uninstall state vs an always-on native badge.
                "managed": not bool(ext.manifest.native),
                "provider": {
                    "type": ext.provider_config.type,
                    "entity": ext.provider_config.entity,
                    "capabilities": ext.provider_config.capabilities,
                    "multiInstance": ext.provider_config.multiInstance,
                    # True only when the provider has APP-level settings, so the UI can
                    # hide the "Configure" expander for a schema-less provider (it would
                    # open to an empty form) and for a multi-instance one, whose schema
                    # describes its instances — those are edited on the instance cards.
                    "hasConfigSchema": not ext.provider_config.multiInstance
                    and bool((ext.provider_config.settingsSchema or {}).get("properties")),
                },
                "tags": ext.manifest.tags,
            }
        )

    # UT6: surface the always-on PLATFORM tool provider (filesystem + shell) so
    # Settings>Providers shows the WHOLE tool universe — it's not a registered
    # extension (it's built per-session in the runtime, being cwd-coupled), so it
    # would otherwise be the one tool provider missing from this list while
    # appearing on the Tools page. Synthesized here as an always-on, non-managed,
    # non-removable card (mirrors how the Tools page marks it 'platform/required'). It is no app's,
    # so an app's list leaves it out with the rest.
    if not request_app and (not type_filter or type_filter == "tool"):
        result.append(
            {
                "name": "personalclaw-filesystem",
                "displayName": "Filesystem & Shell Tools",
                "description": "The always-on platform tools — read/write/edit/list/glob/grep/repo_map, "  # noqa: E501
                "bash, and full-result retrieval. Required by the agent; can't be disabled.",
                "version": "1.0.0",
                "author": "PersonalClaw",
                "enabled": True,
                "error": "",
                "availability": Availability(AVAILABLE).to_wire(),
                "managed": False,
                "platform": True,  # non-removable, non-disableable platform provider
                "provider": {
                    "type": "tool",
                    "entity": "tool",
                    "capabilities": ["filesystem", "shell"],
                    "multiInstance": False,
                    "hasConfigSchema": False,
                },
                "tags": ["tool", "bundled", "platform"],
            }
        )

    return web.json_response({"providers": result})


async def handle_get_extension(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    return web.json_response(
        {
            "name": ext.name,
            "displayName": ext.manifest.displayName,
            "description": ext.manifest.description,
            "version": ext.manifest.version,
            "author": ext.manifest.author,
            "enabled": ext.enabled,
            "error": ext.error,
            "provider": ext.provider_config.to_dict(),
            "manifest": ext.manifest.to_dict(),
        }
    )


async def handle_get_schema(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)

    return web.json_response(
        {
            "name": name,
            "schema": ext.provider_config.settingsSchema,
        }
    )


def _configured_per_instance(ext: Any) -> web.Response | None:
    """409 for app-level config on a provider whose settings live on its INSTANCES.

    A multi-instance provider's ``settingsSchema`` describes one instance; no factory reads
    an app-level copy of it (``ModelTypeHandler``/``ToolTypeHandler`` build from the
    instances). Saving one answered 200 and changed nothing — the Ollama half of this was
    measured: the endpoint chat used stayed at localhost after a "successful" save.
    """
    if not ext.provider_config.multiInstance:
        return None
    who = ext.manifest.displayName or ext.name
    return web.json_response(
        {
            "error": f"{who} keeps its settings on each instance, not on the app. Add, edit, "
            "test or remove its instances in Settings → Providers."
        },
        status=409,
    )


async def handle_get_config(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)
    refusal = _configured_per_instance(ext)
    if refusal is not None:
        return refusal

    # Sensitive fields are WRITE-ONLY: masked out, never handed back. This route returned
    # them verbatim while ``/api/apps/{name}/config`` — reading the SAME file and honouring
    # the SAME ``x-meta.sensitive`` flag — masked them. Since the Providers page calls THIS
    # one, a configured app's credentials (e.g. slack-channel's Bot + App tokens) were
    # shipped to the browser on every panel open, held in the form's state, and revealable
    # on screen through its show/hide toggle. One policy now, in ``apps.secret_fields``.
    #
    # The STORED form, not the values: a reference is masked whatever its field is called, so
    # nothing here ever reads a credential — including one the file names that belongs to
    # another owner, which ``load`` would refuse.
    config, secret_set = mask_secrets(load_stored(name), ext.provider_config.settingsSchema)
    return web.json_response({"name": name, "config": config, "_secret_set": secret_set})


async def handle_patch_config(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    registry = get_provider_registry()
    ext = registry.get(name)
    if not ext:
        return web.json_response({"error": f"Extension {name!r} not found"}, status=404)
    refusal = _configured_per_instance(ext)
    if refusal is not None:
        return refusal

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)

    schema = ext.provider_config.settingsSchema
    # The other half of masking on GET: the form PATCHes back whatever GET gave it, so a
    # sensitive field arriving as the mask (or empty over a stored value) means "keep it".
    # Without this, masking the GET would erase a working token the first time the operator
    # saved an unrelated field on the same form.
    body = preserve_unchanged_secrets(body, load_stored(name), schema)
    errors = ProviderSettings.validate(body, schema)
    if errors:
        return web.json_response({"error": "Validation failed", "details": errors}, status=422)

    try:
        ProviderSettings.update(name, body)
    except ForeignSecretReference as exc:  # names another owner's key: says what to do instead
        return web.json_response({"error": str(exc)}, status=400)
    except ValueError as exc:  # a value no credential can hold (a NUL character)
        return web.json_response({"error": "Validation failed", "details": [str(exc)]}, status=422)

    await apply_saved_settings(name)
    # Mask on the way out too: echoing the freshly-saved token back would undo the GET fix
    # for the one response most likely to be read from a log or a devtools panel. What was
    # saved is read back as stored, so a credential-named field the schema did not declare
    # is masked as a reference, not echoed as the value typed.
    masked, secret_set = mask_secrets(load_stored(name), schema)
    return web.json_response({"name": name, "config": masked, "_secret_set": secret_set})


async def apply_saved_settings(name: str) -> None:
    """Make an app's just-saved settings take effect now, without a gateway restart.

    A provider instance is built from its settings at enable-time and cached in the typed
    registry, so a saved change (a new API key, a new bot token) reached nothing until a
    restart. This rebuilds an enabled app's providers from the saved settings — for a channel,
    the registry change stops the old instance's receiver and starts the rebuilt one's
    (``channel_transports.reconcile_inbound``), which has run by the time this returns — and
    drops the typed media registries' transient adapters so the next resolution rebuilds from
    current config.

    ONE definition for every write of an app's settings: ``PATCH /api/providers/{name}/config``
    did this, and ``PUT /api/apps/{name}/config`` — the Apps page's Configure → Save, writing the
    same file — did none of it, so a token saved there read "No bot token configured" until
    restart. A multi-instance provider's instances are its settings too, one record each
    (``instance_routes``).

    A provider whose enable FAILED is retried too: its settings are what failed it (one naming a
    credential another owner holds, say), and a fix that waits for a restart reads as a fix that
    did not work. Nothing else is switched on here. A provider switched off is its app disabled,
    and a disabled app is not retried: its change is saved, and loads when the app is switched on.
    Neither is an app this core cannot host, which startup lists off with that reason and leaves
    enabled (``app_runtime._refuse``): the core refused it, not its settings, and enabling it is
    ``app_manager.enable``'s decision, which asks the same question first.
    """
    from personalclaw.apps.permissions import app_lifecycle_denial
    from personalclaw.channel_transports import settled

    registry = get_provider_registry()
    ext = registry.get(name)
    if ext is not None and ext.enabled:
        registry.rebuild(name)
    elif (
        ext is not None
        and ext.error
        and not app_lifecycle_denial(name)
        and ext.manifest.core_compatibility().admits
    ):
        registry.enable(name)
    # A rebuilt tool provider offers what its new settings make it offer (a different endpoint
    # answers a different tool list), so its names are read again before this answers.
    await admit_tool_names()
    # For a channel, either one changed the transport registry: its receivers are reconciled
    # before this returns, so the response the caller renders already reflects them.
    await settled()
    try:
        from personalclaw.dashboard.handlers.providers import _refresh_media_registries

        _refresh_media_registries()
    except Exception:  # noqa: BLE001 — refresh is best-effort, never block a save
        logger.debug("media registry refresh after a settings save failed", exc_info=True)


async def apply_changed_credentials() -> None:
    """Make a credential just written to, or removed from, the shared store reach the channels.

    A channel app may read a plain credential name besides its own settings — an earlier
    release's setup stored ``TELEGRAM_BOT_TOKEN`` in the store, and a container passes one in
    the environment — and its receiver keeps the value it started with. Core cannot know which
    names an app reads, so it asks the channels. A reconciliation first starts a channel the
    change configured and stops one it left with nothing (``offline``); then every enabled
    channel that reports an error — a receiver still running on the old value says so — is
    rebuilt from its current settings, which replaces its receiver. A channel the change did
    not touch stays ``ready`` and keeps running undisturbed.
    """
    from personalclaw.channel_transports import channel_health, reconcile_inbound

    await reconcile_inbound()
    unsettled: list[str] = []
    for ext in get_provider_registry().list_by_type("channel"):
        transport = ext.provider_instance
        if not ext.enabled or transport is None:
            continue
        if (await channel_health(transport)).get("state") == "error":
            unsettled.append(ext.name)
    for name in dict.fromkeys(unsettled):
        await apply_saved_settings(name)


async def admit_tool_names() -> None:
    """Read the tool names of every tool provider registered since the last read, so a refusal
    (``tool_providers.registry``: a name has one provider) is on the app's status when the change
    that registered it answers. Waits a few seconds at most: a provider whose list takes longer (a
    remote tool server, the MCP servers) is checked when its list arrives."""
    from personalclaw.tool_providers.registry import admit

    await admit(wait=_ADMISSION_WAIT_SECS)


#: How long a registering change waits for the new providers' tool lists before it answers.
_ADMISSION_WAIT_SECS = 5.0


async def handle_recheck_availability(request: web.Request) -> web.Response:
    """POST /api/providers/{name}/availability — measure again whether it can run here.

    Answers 202 at once: the check runs in the availability child process, and the card
    reads ``checking`` from the list route until the new answer lands.
    """
    name = request.match_info["name"]
    ext = get_provider_registry().get(name)
    if not ext:
        return json_error(
            "not_found", message="No provider is registered under that name.", status=404
        )
    board = get_availability_board()
    board.recheck(name)
    return web.json_response(
        {
            "name": name,
            "availability": board.read(name, ext.provider_config.implementation).to_wire(),
        },
        status=202,
    )
