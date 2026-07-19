"""App Platform REST API + backend reverse-proxy (A4).

HTTP-level coverage over the /api/apps routes: install from a local path,
list/get, enable/disable, config get/put (validated against configSchema),
uninstall-preview, dangerous-install refused, and the reverse-proxy round-trip
to a real app backend subprocess.
"""

from __future__ import annotations

import json
import sys
import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import backend_runtime, manager
from personalclaw.dashboard.handlers.apps import register_app_routes


@asynccontextmanager
async def _client(tmp_path):
    with patch("personalclaw.config.loader.config_dir", return_value=tmp_path), \
         patch.object(manager, "config_dir", return_value=tmp_path):
        # Fresh supervisor per test so backend processes don't leak between tests.
        backend_runtime._supervisor = backend_runtime.BackendSupervisor()
        app = web.Application()
        register_app_routes(app)
        async with TestClient(TestServer(app)) as client:
            try:
                yield client
            finally:
                backend_runtime.get_backend_supervisor().stop_all()


def _app_src(tmp_path: Path, name: str, *, version="1.0.0", subdir="src",
             setup=None, backend=None, files=None, platform=None) -> str:
    d = tmp_path / subdir / name
    d.mkdir(parents=True)
    mani = {"name": name, "version": version, "displayName": name.title(),
            "description": f"{name} fixture"}
    if setup:
        mani["setup"] = setup
    if backend:
        mani["backend"] = backend
    if platform:
        mani["platform"] = platform
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    for rel, content in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return str(d)


@pytest.mark.asyncio
async def test_install_list_get(tmp_path):
    async with _client(tmp_path) as client:
        src = _app_src(tmp_path, "notes")
        r = await client.post("/api/apps", json={"source": src})
        assert r.status == 201, await r.text()
        body = await r.json()
        assert body["ok"] and body["name"] == "notes"

        r = await client.get("/api/apps")
        apps = (await r.json())["apps"]
        assert any(a["name"] == "notes" and a["enabled"] for a in apps)

        r = await client.get("/api/apps/notes")
        got = await r.json()
        assert got["manifest"]["name"] == "notes"
        assert got["installed"]["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_list_hasconfig_from_provider_settings_schema(tmp_path):
    """GET /api/apps `hasConfig` must be true for a PROVIDER app whose settings live
    under provider.settingsSchema (not setup.configSchema) — regression for bug #29,
    where such apps (native-vector-memory/tasks/skills/notifications) reported
    hasConfig=false so the Apps UI hid their Configure action."""
    async with _client(tmp_path) as client:
        # A provider app with settings under provider.settingsSchema, NO setup.configSchema.
        d = tmp_path / "src" / "cfgprov"
        d.mkdir(parents=True)
        (d / "app.json").write_text(json.dumps({
            "name": "cfgprov", "version": "1.0.0", "displayName": "Cfg Prov",
            "description": "provider-schema fixture",
            "provider": {
                "type": "tool",
                "implementation": "provider:create_provider",
                "settingsSchema": {"type": "object", "properties": {"threshold": {"type": "number"}}},
            },
        }), encoding="utf-8")
        (d / "provider.py").write_text("def create_provider(config=None):\n    return None\n", encoding="utf-8")
        r = await client.post("/api/apps", json={"source": str(d), "confirm": True})
        assert r.status == 201, await r.text()

        apps = (await (await client.get("/api/apps")).json())["apps"]
        row = next(a for a in apps if a["name"] == "cfgprov")
        assert row["hasConfig"] is True, "provider.settingsSchema must make hasConfig true"
        assert row["isProvider"] is True


@pytest.mark.asyncio
async def test_list_hasconfig_false_without_any_schema(tmp_path):
    """A plain app with neither setup.configSchema nor provider.settingsSchema → hasConfig false."""
    async with _client(tmp_path) as client:
        src = _app_src(tmp_path, "noconf")
        await client.post("/api/apps", json={"source": src})
        apps = (await (await client.get("/api/apps")).json())["apps"]
        row = next(a for a in apps if a["name"] == "noconf")
        assert row["hasConfig"] is False


@pytest.mark.asyncio
async def test_client_install_returns_200_with_one_liner(tmp_path):
    """P21 platform gate: an installMode=client app is NOT server-installable — the
    handler must return 200 (a valid client-install DIRECTIVE, not a 400 bad-request)
    with needs_client_install + the copy-paste one-liner, and must NOT commit it."""
    async with _client(tmp_path) as client:
        src = _app_src(tmp_path, "clientapp", platform={
            "os": ["macos", "linux"], "installMode": "client",
            "clientInstall": {"shell": "curl -fsSL https://example.invalid/i.sh | sh",
                              "postInstall": "open ~/Applications/X.app"},
        })
        r = await client.post("/api/apps", json={"source": src})
        assert r.status == 200, await r.text()  # directive, not a 400 bad-request
        body = await r.json()
        assert body["ok"] is False
        assert body["needs_client_install"] is True
        assert body["client_install"]["shell"] == "curl -fsSL https://example.invalid/i.sh | sh"
        # NOT committed to the live tree
        apps = (await (await client.get("/api/apps")).json())["apps"]
        assert not any(a["name"] == "clientapp" for a in apps)


@pytest.mark.asyncio
async def test_install_missing_source_400(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post("/api/apps", json={})
        assert r.status == 400
        r = await client.post("/api/apps", json={"source": "/no/such/dir"})
        assert r.status == 400


@pytest.mark.asyncio
async def test_dangerous_install_refused(tmp_path):
    async with _client(tmp_path) as client:
        src = _app_src(tmp_path, "evil",
                       files={"scripts/x.sh": "rm -rf / --no-preserve-root\n"})
        r = await client.post("/api/apps", json={"source": src, "confirm": True})
        assert r.status == 400
        body = await r.json()
        assert not body["ok"] and body["scan"]["verdict"] == "dangerous"


@pytest.mark.asyncio
async def test_enable_disable(tmp_path):
    async with _client(tmp_path) as client:
        src = _app_src(tmp_path, "notes")
        await client.post("/api/apps", json={"source": src})
        assert (await client.post("/api/apps/notes/disable")).status == 200
        assert not (await (await client.get("/api/apps/notes")).json())["installed"]["enabled"]
        assert (await client.post("/api/apps/notes/enable")).status == 200
        assert (await (await client.get("/api/apps/notes")).json())["installed"]["enabled"]


@pytest.mark.asyncio
async def test_config_get_put_validated(tmp_path):
    schema = {"type": "object", "properties": {
        "apiKey": {"type": "string"},
        "maxItems": {"type": "integer"},
    }, "required": ["apiKey"]}
    async with _client(tmp_path) as client:
        src = _app_src(tmp_path, "notes", setup={"configSchema": schema})
        await client.post("/api/apps", json={"source": src})

        # empty config initially; schema returned
        r = await client.get("/api/apps/notes/config")
        body = await r.json()
        assert body["config"] == {} and body["schema"]["required"] == ["apiKey"]

        # invalid: wrong type + missing required
        r = await client.put("/api/apps/notes/config", json={"maxItems": "lots"})
        assert r.status == 400

        # valid
        r = await client.put("/api/apps/notes/config",
                             json={"apiKey": "sk-1", "maxItems": 10})
        assert r.status == 200
        assert (await client.get("/api/apps/notes/config")).status == 200
        saved = (await (await client.get("/api/apps/notes/config")).json())["config"]
        assert saved == {"apiKey": "sk-1", "maxItems": 10}

        # unknown key rejected
        r = await client.put("/api/apps/notes/config", json={"apiKey": "x", "bogus": 1})
        assert r.status == 400


@pytest.mark.asyncio
async def test_sensitive_config_field_is_write_only(tmp_path):
    """A field marked x-meta.sensitive is WRITE-ONLY over the API (#43): GET masks
    the stored secret (never returns it in the clear) + flags it in _secret_set; a
    PUT carrying the mask sentinel (or empty) keeps the stored secret rather than
    clobbering it; a real new value overwrites it."""
    schema = {"type": "object", "properties": {
        "api_key": {"type": "string", "x-meta": {"label": "API Key", "sensitive": True}},
        "endpoint": {"type": "string"},
    }}
    async with _client(tmp_path) as client:
        src = _app_src(tmp_path, "sec", setup={"configSchema": schema})
        await client.post("/api/apps", json={"source": src})

        # set a real secret + a normal field
        r = await client.put("/api/apps/sec/config",
                             json={"api_key": "sk-REALSECRET-123", "endpoint": "https://x"})
        assert r.status == 200
        put_body = await r.json()
        # the PUT response must NOT echo the raw secret back
        assert put_body["config"]["api_key"] != "sk-REALSECRET-123"
        assert "api_key" in put_body["_secret_set"]

        # GET masks the secret (raw value never leaves the backend) but keeps endpoint
        body = await (await client.get("/api/apps/sec/config")).json()
        assert body["config"]["api_key"] != "sk-REALSECRET-123"
        assert body["config"]["api_key"]  # a non-empty mask sentinel
        assert body["config"]["endpoint"] == "https://x"
        assert body["_secret_set"] == ["api_key"]
        mask = body["config"]["api_key"]

        # PUT the mask sentinel back (with a changed endpoint) → secret PRESERVED
        r = await client.put("/api/apps/sec/config",
                             json={"api_key": mask, "endpoint": "https://y"})
        assert r.status == 200

        # confirm on-disk stored secret is still the real one (via the manager's raw read)
        from personalclaw.apps.app_config import read_config
        raw = read_config("sec")
        assert raw["api_key"] == "sk-REALSECRET-123"  # NOT overwritten by the sentinel
        assert raw["endpoint"] == "https://y"          # normal field updated

        # a genuinely new secret value DOES overwrite
        r = await client.put("/api/apps/sec/config", json={"api_key": "sk-NEW-456", "endpoint": "https://y"})
        assert r.status == 200
        assert read_config("sec")["api_key"] == "sk-NEW-456"


@pytest.mark.asyncio
async def test_config_route_rejects_traversal_name_cleanly(tmp_path):
    """A path-escaping {name} on the config route must 404 cleanly (the manifest
    check treats an invalid name as not-installed), NOT surface app_dir's guard
    ValueError as a 500 (#44)."""
    async with _client(tmp_path) as client:
        for bad in ["..%2F..%2Fetc", "..%2F..%2F..%2Fevil"]:
            r = await client.get(f"/api/apps/{bad}/config")
            assert r.status == 404, f"{bad} → {r.status} (want clean 404, not 500)"
            r2 = await client.put(f"/api/apps/{bad}/config", json={"x": 1})
            assert r2.status == 404, f"PUT {bad} → {r2.status}"


@pytest.mark.asyncio
async def test_config_falls_back_to_provider_settings_schema(tmp_path):
    # A provider app declares its settings under provider.settingsSchema (not
    # setup.configSchema); the config UI/API must surface + validate against it.
    import json as _json
    async with _client(tmp_path) as client:
        d = tmp_path / "src" / "wiki"
        d.mkdir(parents=True)
        (d / "app.json").write_text(_json.dumps({
            "name": "wiki", "version": "1.0.0", "displayName": "Wiki", "description": "x",
            "provider": {
                "type": "search", "implementation": "provider:create_provider",
                "settingsSchema": {"type": "object", "properties": {
                    "lang": {"type": "string"}, "timeout_secs": {"type": "integer"}}},
            },
        }), encoding="utf-8")
        (d / "provider.py").write_text(
            "def create_provider(config=None):\n    return object()\n", encoding="utf-8")
        await client.post("/api/apps", json={"source": str(d)})

        # schema surfaced from provider.settingsSchema (NOT empty)
        body = await (await client.get("/api/apps/wiki/config")).json()
        assert set(body["schema"].get("properties", {})) == {"lang", "timeout_secs"}

        # validated against it: valid saves, wrong type rejected
        assert (await client.put("/api/apps/wiki/config", json={"lang": "en", "timeout_secs": 20})).status == 200
        assert (await client.put("/api/apps/wiki/config", json={"timeout_secs": "slow"})).status == 400


@pytest.mark.asyncio
async def test_uninstall_deactivates_force_removes(tmp_path):
    async with _client(tmp_path) as client:
        src = _app_src(tmp_path, "notes")
        await client.post("/api/apps", json={"source": src})
        r = await client.get("/api/apps/notes/uninstall-preview")
        assert r.status == 200 and "dependencies" in (await r.json())
        # Plain DELETE = deactivate: still installed (present), but disabled.
        assert (await client.delete("/api/apps/notes")).status == 200
        got = await client.get("/api/apps/notes")
        assert got.status == 200
        assert (await got.json())["installed"]["enabled"] is False
        # force=1 = real removal → gone (404 afterwards).
        assert (await client.delete("/api/apps/notes?force=1")).status == 200
        assert (await client.get("/api/apps/notes")).status == 404


@pytest.mark.asyncio
async def test_proxy_404_when_not_installed(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.get("/apps/ghost/api/ping")
        assert r.status == 404


@pytest.mark.asyncio
async def test_ui_asset_served_and_traversal_guarded(tmp_path):
    async with _client(tmp_path) as client:
        src = _app_src(tmp_path, "widget",
                       files={"ui/index.js": "export function mount(){return null}\n"})
        assert (await client.post("/api/apps", json={"source": src})).status == 201
        r = await client.get("/apps/widget/ui/index.js")
        assert r.status == 200
        assert "mount" in await r.text()
        assert r.headers["Content-Type"].startswith("text/javascript")
        # path traversal is rejected
        assert (await client.get("/apps/widget/ui/../app.json")).status == 404
        # disabled app serves no UI
        await client.post("/api/apps/widget/disable")
        assert (await client.get("/apps/widget/ui/index.js")).status == 403


@pytest.mark.asyncio
async def test_backend_proxy_round_trip(tmp_path):
    # A real Python backend: an http.server that echoes the path on /health and /ping.
    backend_py = textwrap.dedent("""
        import json, os
        from http.server import BaseHTTPRequestHandler, HTTPServer
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"path": self.path}).encode())
            def log_message(self, *a): pass
        HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
    """)
    async with _client(tmp_path) as client:
        src = _app_src(
            tmp_path, "svc",
            backend={"entryPoint": "backend/server.py", "type": "python", "healthCheck": "/health"},
            files={"backend/server.py": backend_py},
        )
        r = await client.post("/api/apps", json={"source": src})
        assert r.status == 201, await r.text()

        # Backend was launched on install; poll until the proxy gets through.
        import asyncio
        got = None
        for _ in range(50):
            resp = await client.get("/apps/svc/api/ping")
            if resp.status == 200:
                got = await resp.json()
                break
            await asyncio.sleep(0.1)
        assert got is not None, "backend never became reachable through the proxy"
        assert got["path"] == "/ping"

        # Disabling the app stops the backend → proxy 403 (disabled) or 502.
        await client.post("/api/apps/svc/disable")
        resp = await client.get("/apps/svc/api/ping")
        assert resp.status in (403, 502)


@pytest.mark.asyncio
async def test_startup_relaunches_enabled_backends(tmp_path, monkeypatch):
    # Regression: enabled apps' backend subprocesses don't survive a gateway
    # restart; start_enabled_app_backends() relaunches them at startup so the
    # reverse-proxy is live without a manual re-enable.
    # This test exercises the startup launcher itself, so the global test guard
    # (PERSONALCLAW_SKIP_APP_BACKENDS, set in conftest) must be lifted — safe
    # here because _client() isolates config_dir to tmp_path.
    monkeypatch.delenv("PERSONALCLAW_SKIP_APP_BACKENDS", raising=False)
    backend_py = textwrap.dedent("""
        import json, os
        from http.server import BaseHTTPRequestHandler, HTTPServer
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode())
            def log_message(self, *a): pass
        HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
    """)
    import asyncio

    from personalclaw.apps import app_manager
    async with _client(tmp_path) as client:
        src = _app_src(tmp_path, "svc2",
                       backend={"entryPoint": "backend/server.py", "type": "python"},
                       files={"backend/server.py": backend_py})
        assert (await client.post("/api/apps", json={"source": src})).status == 201
        # Simulate a gateway restart: drop the supervisor (kills tracked procs).
        backend_runtime.get_backend_supervisor().stop_all()
        backend_runtime._supervisor = backend_runtime.BackendSupervisor()
        assert backend_runtime.get_backend_supervisor().get("svc2") is None
        # Startup relaunch brings the enabled app's backend back.
        started = app_manager.start_enabled_app_backends()
        assert "svc2" in started
        got = None
        for _ in range(50):
            resp = await client.get("/apps/svc2/api/ping")
            if resp.status == 200:
                got = await resp.json(); break
            await asyncio.sleep(0.1)
        assert got == {"ok": True}
