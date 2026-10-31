"""An app's accounts (#3842): list and add them, read and write each one's settings and its
capabilities' own, and remove one.

``/api/apps/{name}/accounts`` lists the app's accounts, the first (``default``, the settings at
``/api/apps/{name}/config``) then each further one, and adds one. Under
``/api/apps/{name}/accounts/{account}``, ``config`` is that account's own settings and
``capabilities/{capability}/config`` a capability's for that account, under the same rules as the
first account's routes: credentials masked on the way out, a write only over the copy the form was
built from (``revision``, ``If-Match``). ``DELETE`` removes a further account with exactly its
secrets; the first account is never removed, only saved empty.

A further account is stored and read here, and nothing runs on it yet: the registry builds each
capability from the first account (a provider per capability and account comes with #3843's
switches). So a write to the first account is applied at once, as its other routes apply it, and
a write to a further one is only saved.

Only an app that declares account ``settings`` has accounts. An app reaches only its own: the
gateway refuses one naming another app before these run (``apps/permissions.ROUTE_AUTHZ``).
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from personalclaw.request_validation import json_object_body
from personalclaw.sel import sel_log_async
from personalclaw.stale_write import refusal_outcome, revision_of, stale_write_refusal

_LABEL_MAX = 120


def _sel_log(*args: Any, **kwargs: Any) -> None:
    from personalclaw.dashboard.handlers.apps import _sel_log as log

    log(*args, **kwargs)


def _named(name: str, account: str | None, capability: str | None = None) -> str:
    """What a row of these routes names: the app first, as every app row does, then the account
    (``default`` for the first) and the capability, ``key=value`` as the log's other rows do."""
    from personalclaw.providers.accounts import FIRST

    named = f"{name} account={account or FIRST}"
    return f"{named} capability={capability}" if capability else named


def _app(request: web.Request) -> tuple[str, Any] | web.Response:
    """The app and its manifest, or the response that says why it has no accounts.

    These routes name every file by what the gateway knows (the installed manifest's own name,
    an account record that exists, a declared capability id), never by what the request typed."""
    from personalclaw.apps.app_manager import _manifest_of

    asked = request.match_info["name"]
    manifest = _manifest_of(asked)
    if manifest is None or manifest.name != asked:
        return web.json_response({"error": f"app {asked!r} not installed"}, status=404)
    name = manifest.name
    if not manifest.settings:
        who = manifest.displayName or name
        return web.json_response(
            {
                "error": f"{who} keeps one account, its settings at /api/apps/{name}/config: "
                "only an app that declares account settings has more"
            },
            status=409,
        )
    return name, manifest


def _account(request: web.Request, name: str) -> str | None | web.Response:
    """The account the path names: ``None`` for the first, the id of a further account the app
    has (as its record is named, not as the request typed it), or the 404 for one it does not."""
    from personalclaw.providers.accounts import FIRST, further_accounts

    asked = request.match_info["account"]
    if asked == FIRST:
        return None
    account = next((a for a in further_accounts(name) if a == asked), None)
    if account is None:
        return web.json_response({"error": f"{name!r} has no account {asked!r}"}, status=404)
    return account


def _shown(account: str | None) -> str:
    from personalclaw.providers.accounts import FIRST

    return FIRST if account is None else account


async def api_app_accounts_list(request: web.Request) -> web.Response:
    """The app's accounts, the first then each further one, by id and label."""
    from personalclaw.providers.settings import ProviderSettings

    target = _app(request)
    if isinstance(target, web.Response):
        return target
    name, _ = target
    return web.json_response({"name": name, "accounts": ProviderSettings.list_accounts(name)})


async def api_app_account_add(request: web.Request) -> web.Response:
    """Add a further account, with no settings yet.

    The body may name its label: ``{"label": "..."}``."""
    from personalclaw.providers.settings import ProviderSettings

    target = _app(request)
    if isinstance(target, web.Response):
        return target
    name, _ = target
    body = await json_object_body(request)
    label = body.get("label", "")
    if not isinstance(label, str) or len(label.strip()) > _LABEL_MAX:
        return web.json_response(
            {"error": f"label must be a string of at most {_LABEL_MAX} characters"}, status=400
        )
    account = ProviderSettings.add_account(name, label.strip())
    await sel_log_async(_sel_log, "apps.account_add", "ok", _named(name, account), request)
    return web.json_response({"id": account, "label": label.strip()}, status=201)


async def api_app_account_remove(request: web.Request) -> web.Response:
    """Remove a further account and exactly its secrets, its capabilities' with them."""
    from personalclaw.providers.settings import ProviderSettings

    target = _app(request)
    if isinstance(target, web.Response):
        return target
    name, _ = target
    account = _account(request, name)
    if isinstance(account, web.Response):
        return account
    if account is None:
        return web.json_response(
            {"error": "the first account is never removed: save its settings empty to clear it"},
            status=409,
        )
    removed = ProviderSettings.remove_account(name, account)
    await sel_log_async(
        _sel_log,
        "apps.account_remove",
        "ok",
        f"{_named(name, account)} secrets_removed={removed}",
        request,
    )
    return web.json_response({"ok": True, "id": account, "secretsRemoved": removed})


def _account_view(name: str, account: str | None, schema: dict[str, Any]) -> dict[str, Any]:
    from personalclaw.apps.secret_fields import mask_secrets
    from personalclaw.providers.settings import load_stored_account

    masked, secret_set = mask_secrets(load_stored_account(name, account), schema)
    return {
        "name": name,
        "account": _shown(account),
        "config": masked,
        "schema": schema,
        "_secret_set": secret_set,
        "revision": revision_of(masked),
    }


async def api_app_account_config_get(request: web.Request) -> web.Response:
    """One account's own settings, its credentials masked, with the revision a write names."""
    target = _app(request)
    if isinstance(target, web.Response):
        return target
    name, manifest = target
    account = _account(request, name)
    if isinstance(account, web.Response):
        return account
    return web.json_response(_account_view(name, account, manifest.settings))


async def api_app_account_config_put(request: web.Request) -> web.Response:
    """Save one account's own settings.

    The first account's are applied at once, to every capability that is on."""
    from personalclaw.apps.app_config import AppConfigError, write_account_config
    from personalclaw.apps.secret_fields import mask_secrets, preserve_unchanged_secrets
    from personalclaw.providers.routes import apply_saved_settings
    from personalclaw.providers.settings import load_stored_account

    target = _app(request)
    if isinstance(target, web.Response):
        return target
    name, manifest = target
    account = _account(request, name)
    if isinstance(account, web.Response):
        return account
    schema = manifest.settings
    # A save is the whole form: no body is refused, never read as an empty form that clears
    # every setting.
    values = await json_object_body(request, empty_ok=False)
    stored = load_stored_account(name, account)
    # Written only over the copy the form was built from; no `await` between this check and the
    # write below.
    stale = stale_write_refusal(
        request,
        mask_secrets(stored, schema)[0],
        what=f"the settings of the account {_shown(account)!r} of {name!r}",
    )
    if stale is not None:
        await sel_log_async(
            _sel_log, "apps.config", refusal_outcome(stale), _named(name, account), request
        )
        return stale
    values = preserve_unchanged_secrets(values, stored, schema)
    try:
        write_account_config(name, _shown(account), values, schema)
    except AppConfigError as exc:
        await sel_log_async(
            _sel_log, "apps.config", "error", _named(name, account), request, error=str(exc)
        )
        return web.json_response({"error": str(exc)}, status=400)
    await sel_log_async(_sel_log, "apps.config", "ok", _named(name, account), request)
    if account is None:
        await apply_saved_settings(name)
    return web.json_response({"ok": True, **_account_view(name, account, schema)})


def _capability(
    request: web.Request, name: str, manifest: Any
) -> tuple[str, dict[str, Any]] | web.Response:
    from personalclaw.apps.account_settings import capability_schemas

    wanted = request.match_info["capability"]
    schemas = capability_schemas(manifest)
    capability = next((cid for cid in schemas if cid == wanted), None)
    if capability is None:
        return web.json_response({"error": f"{name!r} has no capability {wanted!r}"}, status=404)
    return capability, schemas[capability]


def _capability_view(
    name: str, account: str | None, capability: str, schema: dict[str, Any]
) -> dict[str, Any]:
    from personalclaw.apps.secret_fields import mask_secrets
    from personalclaw.providers.settings import load_stored_capability

    masked, secret_set = mask_secrets(load_stored_capability(name, capability, account), schema)
    return {
        "name": name,
        "account": _shown(account),
        "capability": capability,
        "config": masked,
        "schema": schema,
        "_secret_set": secret_set,
        "revision": revision_of(masked),
    }


async def api_app_account_capability_config_get(request: web.Request) -> web.Response:
    """One capability's own settings for one account, its credentials masked."""
    target = _app(request)
    if isinstance(target, web.Response):
        return target
    name, manifest = target
    account = _account(request, name)
    if isinstance(account, web.Response):
        return account
    found = _capability(request, name, manifest)
    if isinstance(found, web.Response):
        return found
    capability, schema = found
    return web.json_response(_capability_view(name, account, capability, schema))


async def api_app_account_capability_config_put(request: web.Request) -> web.Response:
    """Save one capability's own settings for one account.

    The first account's are applied to that capability at once."""
    from personalclaw.apps.app_config import AppConfigError, write_capability_config
    from personalclaw.apps.secret_fields import mask_secrets, preserve_unchanged_secrets
    from personalclaw.providers.routes import apply_saved_settings
    from personalclaw.providers.settings import load_stored_capability

    target = _app(request)
    if isinstance(target, web.Response):
        return target
    name, manifest = target
    account = _account(request, name)
    if isinstance(account, web.Response):
        return account
    found = _capability(request, name, manifest)
    if isinstance(found, web.Response):
        return found
    capability, schema = found
    # A save is the whole form: no body is refused, never read as an empty form that clears
    # every setting.
    values = await json_object_body(request, empty_ok=False)
    stored = load_stored_capability(name, capability, account)
    # Written only over the copy the form was built from; no `await` between this check and the
    # write below.
    stale = stale_write_refusal(
        request,
        mask_secrets(stored, schema)[0],
        what=f"the settings of the capability {capability!r} in the account "
        f"{_shown(account)!r} of {name!r}",
    )
    if stale is not None:
        await sel_log_async(
            _sel_log,
            "apps.config",
            refusal_outcome(stale),
            _named(name, account, capability),
            request,
        )
        return stale
    values = preserve_unchanged_secrets(values, stored, schema)
    try:
        write_capability_config(name, capability, values, schema, account)
    except AppConfigError as exc:
        await sel_log_async(
            _sel_log,
            "apps.config",
            "error",
            _named(name, account, capability),
            request,
            error=str(exc),
        )
        return web.json_response({"error": str(exc)}, status=400)
    await sel_log_async(_sel_log, "apps.config", "ok", _named(name, account, capability), request)
    if account is None:
        await apply_saved_settings(name, capability)
    return web.json_response({"ok": True, **_capability_view(name, account, capability, schema)})


def register_routes(app: web.Application) -> None:
    # Literal paths: the route census and the published reference read them from the source.
    app.router.add_get("/api/apps/{name}/accounts", api_app_accounts_list)
    app.router.add_post("/api/apps/{name}/accounts", api_app_account_add)
    app.router.add_delete("/api/apps/{name}/accounts/{account}", api_app_account_remove)
    app.router.add_get("/api/apps/{name}/accounts/{account}/config", api_app_account_config_get)
    app.router.add_put("/api/apps/{name}/accounts/{account}/config", api_app_account_config_put)
    app.router.add_get(
        "/api/apps/{name}/accounts/{account}/capabilities/{capability}/config",
        api_app_account_capability_config_get,
    )
    app.router.add_put(
        "/api/apps/{name}/accounts/{account}/capabilities/{capability}/config",
        api_app_account_capability_config_put,
    )
