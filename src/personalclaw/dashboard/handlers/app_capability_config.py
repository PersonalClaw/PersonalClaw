"""``GET``/``PUT /api/apps/{name}/capabilities/{capability}/config``: one capability's own settings
(#3842).

An app's account settings, the ones every capability shares, are ``/api/apps/{name}/config``; these
routes read and write what one capability alone reads, under the same rules: credentials masked
on the way out, a write only over the copy the form was built from (``revision``, ``If-Match``),
and the saved values applied at once, to that capability alone. Only an app that declares account
``settings`` keeps a capability's settings apart; one that does not keeps one settings file, and
these routes say where it is.

An app reaches only its own: the gateway refuses one naming another app before these run
(``apps/permissions.ROUTE_AUTHZ``).
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from personalclaw.sel import sel_log_async
from personalclaw.stale_write import refusal_outcome, revision_of, stale_write_refusal


def _sel_log(*args: Any, **kwargs: Any) -> None:
    from personalclaw.dashboard.handlers.apps import _sel_log as log

    log(*args, **kwargs)


def _target(request: web.Request) -> tuple[str, str, dict[str, Any]] | web.Response:
    """The app, the capability and its own schema, or the response that says why not."""
    from personalclaw.apps.account_settings import capability_schemas
    from personalclaw.apps.app_manager import _manifest_of

    asked, wanted = request.match_info["name"], request.match_info["capability"]
    manifest = _manifest_of(asked)
    if manifest is None or manifest.name != asked:
        return web.json_response({"error": f"app {asked!r} not installed"}, status=404)
    # The installed manifest's own name and declared id from here on, never the request's: every
    # file these read or write is named by what the app declares, not by what a request typed.
    name = manifest.name
    schemas = capability_schemas(manifest)
    capability = next((cid for cid in schemas if cid == wanted), None)
    if capability is None:
        return web.json_response({"error": f"{name!r} has no capability {wanted!r}"}, status=404)
    who = manifest.displayName or name
    if not manifest.settings:
        return web.json_response(
            {
                "error": f"{who} keeps one settings file, shared by all its capabilities: it is "
                f"at /api/apps/{name}/config"
            },
            status=409,
        )
    provider = dict(zip(manifest.capability_ids(), manifest.all_providers()))[capability]
    if provider.multiInstance:
        return web.json_response(
            {
                "error": f"{who} keeps this capability's settings on each instance. Add, edit, "
                "test or remove its instances in Settings → Providers."
            },
            status=409,
        )
    return name, capability, schemas[capability]


async def api_app_capability_config_get(request: web.Request) -> web.Response:
    """One capability's own settings, its credentials masked.

    With the revision a write names, as the account's settings carry theirs."""
    from personalclaw.apps.secret_fields import mask_secrets
    from personalclaw.providers.settings import load_stored_capability

    target = _target(request)
    if isinstance(target, web.Response):
        return target
    name, capability, schema = target
    masked, secret_set = mask_secrets(load_stored_capability(name, capability), schema)
    return web.json_response(
        {
            "name": name,
            "capability": capability,
            "config": masked,
            "schema": schema,
            "_secret_set": secret_set,
            "revision": revision_of(masked),
        }
    )


async def api_app_capability_config_put(request: web.Request) -> web.Response:
    """Save one capability's own settings, and apply them to that capability alone."""
    from personalclaw.apps.app_config import AppConfigError, write_capability_config
    from personalclaw.apps.secret_fields import mask_secrets, preserve_unchanged_secrets
    from personalclaw.providers.routes import apply_saved_settings
    from personalclaw.providers.settings import load_stored_capability

    target = _target(request)
    if isinstance(target, web.Response):
        return target
    name, capability, schema = target
    try:
        values = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    stored = load_stored_capability(name, capability)
    # Written only over the copy the form was built from, as the account's settings are; no
    # `await` between this check and the write below.
    stale = stale_write_refusal(
        request,
        mask_secrets(stored, schema)[0],
        what=f"the settings of the capability {capability!r} of {name!r}",
    )
    if stale is not None:
        await sel_log_async(_sel_log, "apps.config", refusal_outcome(stale), name, request)
        return stale
    if isinstance(values, dict):
        values = preserve_unchanged_secrets(values, stored, schema)
    try:
        write_capability_config(name, capability, values, schema)
    except AppConfigError as exc:  # invalid, another owner's key, or a value no store can hold
        await sel_log_async(_sel_log, "apps.config", "error", name, request, error=str(exc))
        return web.json_response({"error": str(exc)}, status=400)
    await sel_log_async(_sel_log, "apps.config", "ok", name, request)
    await apply_saved_settings(name, capability)
    masked, secret_set = mask_secrets(load_stored_capability(name, capability), schema)
    return web.json_response(
        {
            "ok": True,
            "name": name,
            "capability": capability,
            "config": masked,
            "_secret_set": secret_set,
            "revision": revision_of(masked),
        }
    )
