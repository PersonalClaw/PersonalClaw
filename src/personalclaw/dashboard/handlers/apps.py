"""App Platform REST API (A4).

The lifecycle layer (A1–A3) exposed over HTTP, plus the backend reverse-proxy:

    GET    /api/apps                      — installed apps + state
    GET    /api/apps/{name}               — manifest + status + config + backend
    POST   /api/apps                      — install from source (path | git URL)
    POST   /api/apps/{name}/enable        — enable (run onEnable, register)
    POST   /api/apps/{name}/disable       — disable
    POST   /api/apps/{name}/update        — atomic update from source
    DELETE /api/apps/{name}               — uninstall (consults dependency ledger)
    GET    /api/apps/{name}/uninstall-preview — classify shared deps (A3)
    GET    /api/apps/{name}/config        — read config + the configSchema
    PUT    /api/apps/{name}/config        — validate + persist config
    *      /apps/{name}/api/{tail:.*}      — reverse-proxy to the app's backend

Lifecycle routes are SEL-audited inside the manager. Install/update run the
shared scanner gate: a ``dangerous`` verdict is refused (non-overridable); a
``warning`` returns ``needs_consent`` unless the request passes ``confirm:true``
(the install UI's explicit owner consent).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.security import (
    is_sensitive_path,
    redact_credentials,
    redact_exfiltration_urls,
)

logger = logging.getLogger(__name__)


def _redact(text: str) -> str:
    """Redact exfil URLs + credentials from LLM-derived agent output before it
    crosses back to an app (same two-pass discipline as messaging._redact)."""
    text, _ = redact_exfiltration_urls(text or "")
    text, _ = redact_credentials(text)
    return text

# Hop-by-hop headers that must not be forwarded across the proxy boundary.
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
})
_PROXY_TIMEOUT = 30  # seconds for an app-backend round trip


def register_app_routes(app: web.Application) -> None:
    """Register the App Platform REST + proxy routes on an aiohttp app.

    Specific sub-paths are registered before the catch-all ``/api/apps/{name}``
    GET/DELETE so routing isn't shadowed."""
    app.router.add_get("/api/apps", api_apps_list)
    app.router.add_post("/api/apps", api_app_install)
    # Store catalog + git-source management — registered BEFORE the catch-all
    # /api/apps/{name} so "catalog"/"sources" aren't parsed as an app name.
    app.router.add_get("/api/apps/catalog", api_app_catalog)
    app.router.add_get("/api/apps/sources", api_app_sources_list)
    app.router.add_post("/api/apps/sources", api_app_sources_add)
    app.router.add_delete("/api/apps/sources", api_app_sources_remove)
    app.router.add_get("/api/apps/local-sources", api_app_local_sources_list)
    app.router.add_post("/api/apps/local-sources", api_app_local_sources_add)
    app.router.add_delete("/api/apps/local-sources", api_app_local_sources_remove)
    app.router.add_post("/api/apps/{name}/enable", api_app_enable)
    app.router.add_post("/api/apps/{name}/disable", api_app_disable)
    app.router.add_post("/api/apps/{name}/update", api_app_update)
    app.router.add_get("/api/apps/{name}/uninstall-preview", api_app_uninstall_preview)
    app.router.add_get("/api/apps/{name}/config", api_app_config_get)
    app.router.add_put("/api/apps/{name}/config", api_app_config_put)
    app.router.add_post("/api/apps/{name}/agent-run", api_app_agent_run)
    app.router.add_get("/api/apps/{name}/agent-run/{run_id}", api_app_agent_run_status)
    app.router.add_post("/api/apps/{name}/token", api_app_token)
    app.router.add_get("/api/apps/{name}", api_app_get)
    app.router.add_delete("/api/apps/{name}", api_app_uninstall)
    app.router.add_get("/apps/{name}/ui/{tail:.*}", api_app_ui_asset)
    app.router.add_route("*", "/apps/{name}/api/{tail:.*}", api_app_proxy)


def _sel_log(op: str, outcome: str, resources: str, request: web.Request, error: str = "") -> None:
    try:
        from personalclaw.sel import sel as _s
        _s().log_api_access(
            caller=request.get("user", "dashboard"), operation=op, outcome=outcome,
            source="apps", resources=resources, error=error,
        )
    except Exception:
        pass


def _reconcile_app_crons(request: web.Request) -> None:
    """Re-run app-cron reconciliation after a lifecycle transition (install /
    enable / disable / uninstall / update).

    App-declared manifest crons are otherwise reconciled only once at gateway
    startup, while MCP servers reconcile on every transition — so without this a
    disabled/uninstalled app's cron kept firing agent jobs (and a freshly-enabled
    app's cron didn't register) until the next restart. Reconciliation is
    idempotent + declarative (diffs desired app:* jobs against registered ones),
    so calling it on each transition simply converges the scheduler. Best-effort:
    a missing scheduler (``--no-crons``) or any error is swallowed, never blocking
    the lifecycle response."""
    try:
        state = request.app.get("state")
        crons = getattr(state, "crons", None) if state is not None else None
        if crons is None:
            return
        from personalclaw.apps.app_crons import reconcile_app_crons
        reconcile_app_crons(crons)
    except Exception:
        logger.debug("app cron reconcile after lifecycle transition failed", exc_info=True)


def _app_status(name: str) -> dict[str, Any]:
    """Runtime status for an app: enabled + backend running/port."""
    from personalclaw.apps.backend_runtime import get_backend_supervisor

    rb = get_backend_supervisor().get(name)
    return {
        "backendRunning": rb is not None,
        "backendPort": rb.port if rb else None,
    }


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def api_apps_list(request: web.Request) -> web.Response:
    """GET /api/apps — installed apps with manifest summary + runtime state."""
    from personalclaw.apps.catalog import resolve_hero_url
    from personalclaw.apps.manager import app_dir, list_apps

    out: list[dict[str, Any]] = []
    for app in list_apps():
        manifest = app.get("manifest", {})
        name = app.get("name", "")
        # A provider app's settings live in Settings > Providers; a non-provider
        # app's settings (setup.configSchema) are aggregated in Settings > Apps.
        # Surface both signals so each surface can filter without an N+1 fetch.
        is_provider = bool(manifest.get("provider"))
        # `hasConfig` = does the app have ANY settings surface. Mirror
        # `_effective_config_schema`: an explicit setup.configSchema OR a provider
        # app's provider.settingsSchema. Reading only setup.configSchema wrongly
        # reported hasConfig=false for provider apps whose config lives under
        # provider.settingsSchema (e.g. native-vector-memory/tasks/skills/
        # notifications) — so the Apps UI hid their Configure action (bug #29).
        config_schema = (manifest.get("setup", {}) or {}).get("configSchema") or {}
        provider_schema = (manifest.get("provider", {}) or {}).get("settingsSchema") or {}
        has_config = bool(config_schema.get("properties") or provider_schema.get("properties"))
        # Contributed UI pages (route/label/icon) so the shell can register each as
        # a nav target under the Apps section — not just a single per-app page.
        ui_pages = [
            {
                "route": p.get("route", ""),
                "label": p.get("label", ""),
                "icon": p.get("icon", ""),
            }
            for p in (manifest.get("ui", {}) or {}).get("pages", [])
            if p.get("route")
        ]
        out.append({
            "name": name,
            "displayName": manifest.get("displayName") or name,
            "version": app.get("version", ""),
            "description": manifest.get("description", ""),
            "enabled": app.get("enabled", False),
            "origin": app.get("origin", ""),
            # A native app is locked on — the FE hides uninstall/disable and
            # shows a "native, always-on" notice, offering Configure/Update only.
            "native": bool(manifest.get("native", False)),
            # Concrete provenance (path / git URL / "builtin" / "registry:name") so
            # the Store/Library can group installed apps under their source divider.
            "source": app.get("source", ""),
            "icon": manifest.get("icon", ""),
            # Optional hero/banner image → resolved to a data: URI from the
            # installed app dir (empty when the manifest declares none / unreadable).
            "heroUrl": resolve_hero_url(app_dir(name), str(manifest.get("heroImage", ""))),
            "hasBackend": bool(manifest.get("backend", {}).get("entryPoint")),
            "hasUI": bool(manifest.get("ui", {}).get("pages")),
            "uiPages": ui_pages,
            "isProvider": is_provider,
            "providerType": (manifest.get("provider") or {}).get("type", "") if is_provider else "",
            "hasConfig": has_config,
            "permissions": manifest.get("permissions", {}),
            "tags": [str(t) for t in manifest.get("tags", []) if t],
            "installedAt": app.get("installedAt", ""),
            "updatedAt": app.get("updatedAt", ""),
            **_app_status(name),
        })

    # UT6: the bundled PROVIDER extensions (native tool/knowledge/memory/… providers
    # + the mcp/openai adapters) register via the extension loader, not the app
    # manager's installed-apps dir, so list_apps() above never sees them — yet they
    # ARE app-platform-registered providers the user expects in the Library. Append
    # them here (deduped by name) so Settings>Providers and Store>Library show the
    # SAME provider universe. They're platform-managed (always-on, not user-
    # uninstallable) → flagged platform:true so the Library renders them as such.
    try:
        from personalclaw.providers.registry import get_provider_registry

        have = {a["name"] for a in out}
        # The always-on platform tool provider (filesystem+shell) isn't a registered
        # extension (per-session in the runtime), so synthesize it here too — same
        # entry Settings>Providers shows — so both surfaces list the identical
        # provider universe.
        if "personalclaw-filesystem" not in have:
            out.append({
                "name": "personalclaw-filesystem",
                "displayName": "Filesystem & Shell Tools",
                "version": "1.0.0",
                "description": "Always-on platform tools — read/write/edit/list/glob/grep/repo_map, "
                               "bash, full-result retrieval. Required by the agent.",
                "enabled": True, "origin": "bundled", "icon": "FolderCog", "heroUrl": "",
                "hasBackend": False, "hasUI": False, "uiPages": [],
                "isProvider": True, "providerType": "tool", "hasConfig": False,
                "permissions": {}, "tags": [], "installedAt": "", "updatedAt": "",
                # Always-on, non-uninstallable ⇒ a NATIVE app (the one tier flag).
                # No separate `platform` flag — the app category is `native`; whether
                # it has settings is `hasConfig` (False here → UI shows "manage elsewhere").
                "native": True, "status": "running",
            })
            have.add("personalclaw-filesystem")
        for ext in get_provider_registry().list_extensions():
            if ext.name in have:
                continue
            out.append({
                "name": ext.name,
                "displayName": ext.manifest.displayName or ext.name,
                "version": ext.manifest.version,
                "description": ext.manifest.description,
                "enabled": ext.enabled,
                "origin": "bundled",
                "icon": ext.manifest.icon,
                # Extension providers installed on disk may ship a hero image; resolve
                # from their app dir (no-op → "" when absent or not disk-installed).
                "heroUrl": resolve_hero_url(app_dir(ext.name), ext.manifest.heroImage),
                "hasBackend": False,
                "hasUI": False,
                "uiPages": [],
                "isProvider": True,
                "providerType": ext.provider_config.type,
                "hasConfig": bool((ext.provider_config.settingsSchema or {}).get("properties")),
                "permissions": {},
                "tags": [],
                "installedAt": "",
                "updatedAt": "",
                # Always-on, non-uninstallable ⇒ NATIVE (the single tier flag). Config
                # affordance is driven by `hasConfig` above, not a separate flag.
                "native": True,
                "status": "running" if ext.enabled else "stopped",
            })
    except Exception:
        logger.debug("apps list: bundled provider extensions append skipped", exc_info=True)

    return web.json_response({"apps": out})


async def api_app_catalog(request: web.Request) -> web.Response:
    """GET /api/apps/catalog — available-to-install apps (Store): bundled-but-not-
    installed manifests + the configured git source URLs."""
    from personalclaw.apps import catalog

    result = await asyncio.to_thread(catalog.available_catalog)
    return web.json_response(result)


async def api_app_sources_list(request: web.Request) -> web.Response:
    """GET /api/apps/sources — the configured git source URLs (defaults + user)."""
    from personalclaw.apps import catalog

    return web.json_response({"sources": catalog.list_git_sources()})


async def api_app_sources_add(request: web.Request) -> web.Response:
    """POST /api/apps/sources — add a user git source URL ``{url}``."""
    from personalclaw.apps import catalog

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    url = str(body.get("url", "")).strip()
    if not url:
        return web.json_response({"error": "url is required"}, status=400)
    try:
        catalog.add_git_source(url)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    _sel_log("apps.source_add", "ok", url, request)
    return web.json_response({"ok": True, "sources": catalog.list_git_sources()})


async def api_app_sources_remove(request: web.Request) -> web.Response:
    """DELETE /api/apps/sources?url=… — remove a user git source URL."""
    from personalclaw.apps import catalog

    url = request.query.get("url", "").strip()
    if not url:
        return web.json_response({"error": "url is required"}, status=400)
    catalog.remove_git_source(url)
    _sel_log("apps.source_remove", "ok", url, request)
    return web.json_response({"ok": True, "sources": catalog.list_git_sources()})


async def api_app_local_sources_list(request: web.Request) -> web.Response:
    """GET /api/apps/local-sources — the configured local app-source directories."""
    from personalclaw.apps import catalog

    return web.json_response({"sources": catalog.list_local_sources()})


async def api_app_local_sources_add(request: web.Request) -> web.Response:
    """POST /api/apps/local-sources — add a local app-source dir ``{path}`` (a
    directory of app subdirs; its apps then surface in the Store catalog)."""
    from personalclaw.apps import catalog

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    path = str(body.get("path", "")).strip()
    if not path:
        return web.json_response({"error": "path is required"}, status=400)
    try:
        catalog.add_local_source(path)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    _sel_log("apps.local_source_add", "ok", path, request)
    return web.json_response({"ok": True, "sources": catalog.list_local_sources()})


async def api_app_local_sources_remove(request: web.Request) -> web.Response:
    """DELETE /api/apps/local-sources?path=… — remove a local app-source dir."""
    from personalclaw.apps import catalog

    path = request.query.get("path", "").strip()
    if not path:
        return web.json_response({"error": "path is required"}, status=400)
    catalog.remove_local_source(path)
    _sel_log("apps.local_source_remove", "ok", path, request)
    return web.json_response({"ok": True, "sources": catalog.list_local_sources()})


async def api_app_get(request: web.Request) -> web.Response:
    """GET /api/apps/{name} — full manifest + status + saved config."""
    from personalclaw.apps.app_config import read_config
    from personalclaw.apps.manager import _read_installed
    from personalclaw.apps.app_manager import _manifest_of

    name = request.match_info["name"]
    meta = _read_installed(name)
    if meta is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    manifest = _manifest_of(name)
    return web.json_response({
        "name": name,
        "installed": meta.to_dict(),
        "manifest": manifest.to_dict() if manifest else None,
        "config": read_config(name),
        # Effective schema (setup.configSchema OR a provider app's provider.
        # settingsSchema) — same source the dedicated /config endpoint uses, so a
        # provider app's detail view shows its real config surface, not empty (the
        # #29 class: reading only setup.configSchema hides provider settings).
        "configSchema": _effective_config_schema(manifest) if manifest else {},
        **_app_status(name),
    })


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def api_app_install(request: web.Request) -> web.Response:
    """POST /api/apps — install from ``{source, confirm?}``.

    ``source`` is a local directory path or a git URL. ``confirm:true`` consents
    to a ``warning`` scan verdict; a ``dangerous`` verdict is always refused."""
    from personalclaw.apps import app_manager, source as app_source

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    src = str(body.get("source", "")).strip()
    if not src:
        return web.json_response({"error": "source is required"}, status=400)
    confirm = bool(body.get("confirm", False))

    try:
        resolved = await asyncio.to_thread(app_source.resolve, src)
    except app_source.SourceError as exc:
        _sel_log("apps.install", "error", src, request, error=str(exc))
        return web.json_response({"error": str(exc)}, status=400)

    def _do_install():
        try:
            return app_manager.install(
                resolved.path, origin=resolved.origin, confirm=confirm,
                caller=request.get("user", "dashboard"),
                source_ref=src,
            )
        finally:
            if resolved.cleanup:
                app_source._rmtree(resolved.cleanup_path)

    result = await asyncio.to_thread(_do_install)

    # 201 installed · 409 scan-warning needs consent · 200 client-install directive
    # (a VALID app that installs on the user's machine, not a bad request — the body
    # carries the copy-paste one-liner) · 400 a genuine bad/failed request.
    if result.ok:
        status = 201
    elif result.needs_consent:
        status = 409
    elif result.needs_client_install:
        status = 200
    else:
        status = 400
    _sel_log("apps.install", "ok" if result.ok else "refused", result.name or src, request,
             error=result.error)
    if result.ok:
        _reconcile_app_crons(request)  # register a freshly-installed app's crons now
    return web.json_response(result.to_dict(), status=status)


async def api_app_update(request: web.Request) -> web.Response:
    """POST /api/apps/{name}/update — atomic update from ``{source, confirm?}``."""
    from personalclaw.apps import app_manager, source as app_source

    name = request.match_info["name"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    src = str(body.get("source", "")).strip()
    if not src:
        return web.json_response({"error": "source is required"}, status=400)
    confirm = bool(body.get("confirm", False))

    try:
        resolved = await asyncio.to_thread(app_source.resolve, src)
    except app_source.SourceError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    def _do_update():
        try:
            return app_manager.update(
                resolved.path, name, origin=resolved.origin, confirm=confirm,
                caller=request.get("user", "dashboard"),
            )
        finally:
            if resolved.cleanup:
                app_source._rmtree(resolved.cleanup_path)

    result = await asyncio.to_thread(_do_update)

    status = 200 if result.ok else (409 if result.needs_consent else 400)
    _sel_log("apps.update", "ok" if result.ok else "error", name, request, error=result.error)
    if result.ok:
        _reconcile_app_crons(request)  # a manifest edit may add/remove/retime crons
    return web.json_response(result.to_dict(), status=status)


async def api_app_enable(request: web.Request) -> web.Response:
    from personalclaw.apps import app_manager

    name = request.match_info["name"]
    ok = await asyncio.to_thread(
        app_manager.enable, name, caller=request.get("user", "dashboard"),
    )
    _sel_log("apps.enable", "ok" if ok else "error", name, request)
    if not ok:
        return web.json_response({"error": f"enable failed for {name!r}"}, status=400)
    _reconcile_app_crons(request)  # register the app's manifest crons now, not at next restart
    return web.json_response({"ok": True, "name": name, "enabled": True})


async def api_app_disable(request: web.Request) -> web.Response:
    from personalclaw.apps import app_manager

    name = request.match_info["name"]
    ok = await asyncio.to_thread(
        app_manager.disable, name, caller=request.get("user", "dashboard"),
    )
    _sel_log("apps.disable", "ok" if ok else "error", name, request)
    if not ok:
        return web.json_response({"error": f"disable failed for {name!r}"}, status=400)
    _reconcile_app_crons(request)  # prune the now-disabled app's crons immediately
    return web.json_response({"ok": True, "name": name, "enabled": False})


async def api_app_uninstall(request: web.Request) -> web.Response:
    """DELETE /api/apps/{name} — uninstall = DEACTIVATE (keep files). Pass
    ``?force=1`` for the destructive force-uninstall that removes files from disk.
    """
    from personalclaw.apps import app_manager

    name = request.match_info["name"]
    force = request.query.get("force") in ("1", "true", "yes")
    caller = request.get("user", "dashboard")
    if force:
        ok = await asyncio.to_thread(
            app_manager.force_uninstall, name, caller=caller,
        )
        op = "apps.force_uninstall"
    else:
        ok = await asyncio.to_thread(
            app_manager.uninstall, name, caller=caller,
        )
        op = "apps.uninstall"
    _sel_log(op, "ok" if ok else "error", name, request)
    if not ok:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    _reconcile_app_crons(request)  # prune the uninstalled app's crons immediately
    return web.json_response({"ok": True, "name": name, "forced": force})


async def api_app_uninstall_preview(request: web.Request) -> web.Response:
    """GET /api/apps/{name}/uninstall-preview — classify shared deps (A3)."""
    from personalclaw.apps import app_manager

    name = request.match_info["name"]
    classifications = app_manager.preview_uninstall(name)
    return web.json_response({
        "name": name,
        "dependencies": [c.to_dict() for c in classifications],
    })


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _effective_config_schema(manifest) -> dict[str, Any]:
    """The schema that drives an app's config UI: an explicit ``setup.configSchema``
    if declared, else a provider app's ``provider.settingsSchema`` (where a
    pluggable provider declares its user-configurable settings). Lets a
    provider-only app expose its settings without duplicating the schema."""
    schema = manifest.setup.configSchema
    if schema:
        return schema
    if manifest.provider and manifest.provider.settingsSchema:
        return manifest.provider.settingsSchema
    return {}


# A field whose schema marks ``x-meta.sensitive: true`` (api keys, tokens, …) is
# WRITE-ONLY over the API: its stored value is never returned to the client on GET
# (only this masked sentinel + a "set" flag), and a PUT that carries the sentinel
# back is treated as "keep the stored secret". This keeps a configured key from
# leaving the backend in cleartext on every config-panel open (#43), while still
# letting the user save unrelated field edits without re-typing the key.
_SECRET_MASK = "••••••••"  # ••••••••


def _sensitive_field_names(schema: dict[str, Any]) -> set[str]:
    """Property names flagged ``x-meta.sensitive: true`` in a config schema."""
    props = (schema or {}).get("properties") or {}
    out: set[str] = set()
    for key, spec in props.items():
        if isinstance(spec, dict) and (spec.get("x-meta") or {}).get("sensitive"):
            out.add(key)
    return out


def _mask_secret_config(config: dict[str, Any], schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return (masked_config, set_field_names): sensitive fields with a stored
    non-empty value are replaced by the mask sentinel; the second element lists
    which sensitive fields are currently set (so the UI can show "saved")."""
    sensitive = _sensitive_field_names(schema)
    masked = dict(config or {})
    were_set: list[str] = []
    for key in sensitive:
        if str(masked.get(key, "") or ""):
            masked[key] = _SECRET_MASK
            were_set.append(key)
    return masked, were_set


async def api_app_config_get(request: web.Request) -> web.Response:
    from personalclaw.apps.app_config import read_config
    from personalclaw.apps.app_manager import _manifest_of

    name = request.match_info["name"]
    manifest = _manifest_of(name)
    if manifest is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    schema = _effective_config_schema(manifest)
    # Write-only sensitive fields: mask the stored secret, never send it in the clear
    # (#43). ``_secret_set`` tells the UI which sensitive fields are already set.
    masked, secret_set = _mask_secret_config(read_config(name), schema)
    return web.json_response({
        "name": name,
        "config": masked,
        "schema": schema,
        "_secret_set": secret_set,
    })


async def api_app_config_put(request: web.Request) -> web.Response:
    from personalclaw.apps.app_config import AppConfigError, read_config, write_config
    from personalclaw.apps.app_manager import _manifest_of

    name = request.match_info["name"]
    manifest = _manifest_of(name)
    if manifest is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    try:
        values = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    schema = _effective_config_schema(manifest)
    # A sensitive field carrying the mask sentinel (or empty when it was already set)
    # means "keep the stored secret" — don't overwrite it with the placeholder (#43).
    if isinstance(values, dict):
        existing = read_config(name)
        for key in _sensitive_field_names(schema):
            if key not in values:
                continue
            incoming = str(values.get(key, "") or "")
            had_value = bool(str(existing.get(key, "") or ""))
            if incoming == _SECRET_MASK or (incoming == "" and had_value):
                # Preserve the existing secret rather than clobbering it.
                if had_value:
                    values[key] = existing[key]
                else:
                    values.pop(key, None)
    try:
        saved = write_config(name, values, schema)
    except AppConfigError as exc:
        _sel_log("apps.config", "error", name, request, error=str(exc))
        return web.json_response({"error": str(exc)}, status=400)
    _sel_log("apps.config", "ok", name, request)
    # Never echo the freshly-saved secret back either — mask on the response too.
    masked, secret_set = _mask_secret_config(saved, schema)
    return web.json_response({"ok": True, "name": name, "config": masked, "_secret_set": secret_set})


# ---------------------------------------------------------------------------
# Background agent tasks: an app runs a headless agent + polls its result.
# This is the NON-iframe agentic path — for apps that act on agent output
# rather than show a human a chat window. Gated by permissions.agent.
# ---------------------------------------------------------------------------

def _app_agent_allowed(name: str) -> bool:
    """Whether the installed app declares the `agent` permission."""
    from personalclaw.apps.permissions import checker_for

    checker = checker_for(name)
    return checker is not None and checker.can_use_agent()


async def api_app_agent_run(request: web.Request) -> web.Response:
    """POST /api/apps/{name}/agent-run — start a background agent task.

    Body: ``{task, agent?, max_turns?}``. Runs a headless subagent (auto-approve,
    silent) on behalf of the app and returns its ``{id}``; the app polls
    ``/agent-run/{id}`` for the result. Requires the app's ``agent`` permission."""
    name = request.match_info["name"]
    if not _app_agent_allowed(name):
        _sel_log("apps.agent_run", "denied", name, request, error="agent permission not granted")
        return web.json_response(
            {"error": f"app {name!r} does not declare the 'agent' permission"}, status=403)

    state = request.app["state"]
    if not getattr(state, "subagents", None):
        return web.json_response({"error": "subagents not available"}, status=503)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    task = str((body or {}).get("task", "")).strip()
    if not task:
        return web.json_response({"error": "task is required"}, status=400)
    agent = str((body or {}).get("agent", "")) or ""
    try:
        max_turns = int((body or {}).get("max_turns", 0) or 0)
    except (TypeError, ValueError):
        max_turns = 0

    # App-run agents are headless: auto-approve tools + silent (no chat surfacing),
    # tagged by the app so the run is attributable.
    info = state.subagents.spawn(
        task, parent_session_key=f"app:{name}", agent=agent,
        max_turns=max_turns, approval_mode="auto", silent=True,
    )
    if not info:
        return web.json_response(
            {"error": f"capacity reached ({state.subagents.max_concurrent})"}, status=429)
    if info.done and info.error:
        _sel_log("apps.agent_run", "error", name, request, error=info.error)
        return web.json_response({"error": info.error}, status=400)
    _sel_log("apps.agent_run", "ok", name, request, error="")
    return web.json_response({"id": info.id, "task": task, "status": "running"}, status=202)


async def api_app_agent_run_status(request: web.Request) -> web.StreamResponse:
    """GET /api/apps/{name}/agent-run/{run_id} — poll a background agent task.

    Returns ``{id, done, result?, error?, turns?, elapsed?}``. Requires the app's
    ``agent`` permission."""
    name = request.match_info["name"]
    run_id = request.match_info["run_id"]
    if not _app_agent_allowed(name):
        return web.json_response(
            {"error": f"app {name!r} does not declare the 'agent' permission"}, status=403)

    state = request.app["state"]
    if not getattr(state, "subagents", None):
        return web.json_response({"error": "subagents not available"}, status=503)
    info = state.subagents.get(run_id)
    if not info:
        return web.json_response({"error": "not found"}, status=404)
    data: dict[str, Any] = {"id": info.id, "task": info.task, "done": info.done,
                            "turns": info.turns, "elapsed": round(time.time() - info.started)}
    if info.done:
        result = info.result
        if getattr(info, "result_path", "") and not is_sensitive_path(info.result_path):
            try:
                result = await asyncio.to_thread(
                    Path(info.result_path).read_text, encoding="utf-8", errors="replace")
            except OSError:
                pass
        data["result"] = _redact(result or "")
        data["error"] = _redact(info.error) if info.error else ""
    return web.json_response(data)


# ---------------------------------------------------------------------------
# Per-app identity token (untrusted-app sandbox, P1)
# ---------------------------------------------------------------------------

# App tokens are short-lived — the SDK mints one on mount and re-mints on expiry.
# Bounded so a leaked app token has a small blast radius.
_APP_TOKEN_TTL_SECS = 3600


async def api_app_token(request: web.Request) -> web.Response:
    """POST /api/apps/{name}/token — mint an app-scoped identity token.

    An installed app's SDK calls this on mount; the returned token carries an
    ``app`` claim so every subsequent app request (fetch ``Authorization: Bearer`` +
    the ``/api/ws?app_token=`` handshake) is attributable to THIS app. The token
    auth middleware sets ``request["app"]`` from the claim, and the app-permission
    middleware + WS event filter gate on it. Bound to the current owner user (an app
    never exceeds the owner's own reach) and short-lived.

    Only the OWNER (a non-app request) may mint an app token — an app can't mint a
    token for a different app to escalate."""
    from personalclaw.apps.manager import _read_installed
    from personalclaw.dashboard.token_auth import generate_token

    name = request.match_info["name"]
    # Reject minting from within an app context (no privilege escalation across apps).
    if request.get("app", ""):
        return web.json_response({"error": "apps may not mint tokens"}, status=403)

    meta = _read_installed(name)
    if meta is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    if not meta.enabled:
        return web.json_response({"error": f"app {name!r} is disabled"}, status=403)

    user_id = request.get("user", "dashboard")
    token = generate_token(user_id, ttl_seconds=_APP_TOKEN_TTL_SECS, app=name)
    return web.json_response({"token": token, "expires_in": _APP_TOKEN_TTL_SECS})


# ---------------------------------------------------------------------------
# Reverse proxy: /apps/{name}/api/{tail} → the app's backend subprocess
# ---------------------------------------------------------------------------

async def api_app_proxy(request: web.Request) -> web.StreamResponse:
    """Reverse-proxy a request to an app's backend subprocess.

    Matches ``/apps/{name}/api/{tail:.*}`` for any method. 404 if the app isn't
    installed, 502 if its backend isn't running, 403 if the app is disabled.

    The owner's session credential (cookie / bearer) is STRIPPED before forwarding —
    an app backend must never receive the owner's token (it could replay it against
    the full gateway API). Instead we forward a fresh app-scoped token so the backend
    has an identity bounded to its own declared permissions."""
    import aiohttp

    from personalclaw.apps.backend_runtime import get_backend_supervisor
    from personalclaw.apps.manager import _read_installed
    from personalclaw.dashboard.token_auth import generate_token

    name = request.match_info["name"]
    tail = request.match_info.get("tail", "")

    meta = _read_installed(name)
    if meta is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    if not meta.enabled:
        return web.json_response({"error": f"app {name!r} is disabled"}, status=403)

    rb = get_backend_supervisor().get(name)
    if rb is None:
        return web.json_response({"error": f"app {name!r} backend not running"}, status=502)

    target = f"{rb.base_url}/{tail}"
    # Strip the owner credential (cookie + Authorization) and any inbound app-identity
    # headers, then attach a fresh app-scoped token so the backend is bounded to its
    # own permissions rather than borrowing the owner's session.
    _STRIP = _HOP_BY_HOP | {"cookie", "authorization", "x-personalclaw-app"}
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    user_id = request.get("user", "dashboard")
    fwd_headers["Authorization"] = f"Bearer {generate_token(user_id, ttl_seconds=_APP_TOKEN_TTL_SECS, app=name)}"
    fwd_headers["X-PersonalClaw-App"] = name
    body = await request.read()
    timeout = aiohttp.ClientTimeout(total=_PROXY_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                request.method, target, headers=fwd_headers,
                params=request.rel_url.query, data=body or None,
                allow_redirects=False,
            ) as upstream:
                resp = web.StreamResponse(status=upstream.status)
                for k, v in upstream.headers.items():
                    if k.lower() not in _HOP_BY_HOP:
                        resp.headers[k] = v
                await resp.prepare(request)
                async for chunk in upstream.content.iter_chunked(8192):
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
    except aiohttp.ClientError as exc:
        return web.json_response({"error": f"app backend error: {exc}"}, status=502)
    except Exception as exc:  # noqa: BLE001
        logger.warning("app %s proxy failed: %s", name, exc)
        return web.json_response({"error": "app backend proxy failed"}, status=502)


# ---------------------------------------------------------------------------
# UI asset serving: /apps/{name}/ui/{tail} → files under the app's ui/ dir
# ---------------------------------------------------------------------------

_UI_CONTENT_TYPES = {
    ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
    ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
    ".woff2": "font/woff2", ".map": "application/json",
}


async def api_app_ui_asset(request: web.Request) -> web.StreamResponse:
    """Serve an installed app's contributed UI bundle file (the ESM the frontend
    code-splits in to mount the app's page). Confined to the app's ``ui/`` dir
    with a path-traversal guard; only enabled apps serve UI."""
    from personalclaw.apps.manager import _read_installed, app_dir

    name = request.match_info["name"]
    tail = request.match_info.get("tail", "")
    meta = _read_installed(name)
    if meta is None:
        return web.json_response({"error": f"app {name!r} not installed"}, status=404)
    if not meta.enabled:
        return web.json_response({"error": f"app {name!r} is disabled"}, status=403)

    ui_root = (app_dir(name) / "ui").resolve()
    target = (ui_root / tail).resolve()
    if not str(target).startswith(str(ui_root)) or not target.is_file():
        return web.json_response({"error": "not found"}, status=404)

    ctype = _UI_CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
    return web.FileResponse(target, headers={"Content-Type": ctype})
