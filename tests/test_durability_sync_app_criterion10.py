"""Criterion 10, end to end: a THIRD-PARTY `type: "sync"` app registers, configures through
the standard provider routes, and syncs — with zero core changes (DURABILITY-AND-SYNC §4.3).

`test_sync_transport_contract.py` already pins the pieces at unit level (the type is in
`PROVIDER_TYPES`, `SyncTypeHandler` registers and deregisters an instance). What it cannot
show is the criterion's actual claim, which is about an app the core has never heard of:

* the transport arrives as an `app.json` + a Python file INSIDE the app's own directory —
  nothing in `src/personalclaw` names it, so "zero core changes" is a property of the
  fixture rather than an assertion about it;
* its settings are read and written through `/api/providers/{name}/schema` + `/config`, the
  same routes every other provider type uses — no durability-specific config endpoint;
* and then it actually SYNCS: `service.run_sync_job()` resolves it by name and a real cycle
  pushes objects into the store it owns.

The transport here is deliberately dumb (it copies bytes into a directory), because the point
being verified is the seam, not the transport.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

APP_NAME = "acme-box-sync"

#: The app's whole implementation. Nothing in core imports or names it — the file is written
#: into a temp home by the fixture, exactly as an installed third-party app would be.
_PROVIDER_PY = '''
"""A third-party sync transport. Imports core ONLY through the SDK."""

from pathlib import Path

from personalclaw.sdk.sync import (
    ConnectionResult,
    PushResult,
    RemoteRef,
    SyncObject,
    SyncTransportProvider,
)


class BoxTransport(SyncTransportProvider):
    name = "acme-box-sync"
    display_name = "Acme Box"

    def __init__(self, config):
        self._root = Path(str(config.get("folder", "")))

    def push(self, objects):
        written = 0
        for obj in objects:
            dest = self._root / obj.key
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_bytes(obj.data)
                written += 1
        return PushResult(pushed=written, skipped=len(objects) - written)

    def list_remote(self, prefix=""):
        if not self._root.is_dir():
            return []
        out = []
        for p in sorted(self._root.rglob("*")):
            if not p.is_file():
                continue
            key = str(p.relative_to(self._root))
            if prefix and not key.startswith(prefix):
                continue
            st = p.stat()
            out.append(RemoteRef(key=key, size=st.st_size, fingerprint=str(st.st_mtime)))
        return out

    def pull(self, refs):
        return [
            SyncObject(key=r.key, data=(self._root / r.key).read_bytes())
            for r in refs
            if (self._root / r.key).is_file()
        ]

    def cas_registry(self, expected_sha, data):
        target = self._root / "registry.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return True

    def test(self):
        return ConnectionResult(ok=self._root.is_dir(), detail=str(self._root))


def create(config):
    return BoxTransport(config)
'''

_MANIFEST = {
    "name": APP_NAME,
    "version": "1.0.0",
    "displayName": "Acme Box Sync",
    "description": "Syncs PersonalClaw state into an Acme Box folder.",
    "provider": {
        "type": "sync",
        "implementation": "provider:create",
        "settingsSchema": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "default": "",
                    "x-meta": {"label": "Box folder", "help": "Where shards are written."},
                }
            },
        },
    },
}


@pytest.fixture
def installed_app(tmp_path, monkeypatch):
    """A third-party sync app installed into an isolated home, plus its remote folder."""
    home = tmp_path / "home"
    app = home / "apps" / APP_NAME
    app.mkdir(parents=True)
    (app / "app.json").write_text(json.dumps(_MANIFEST), encoding="utf-8")
    (app / "provider.py").write_text(_PROVIDER_PY, encoding="utf-8")
    remote = tmp_path / "box"
    remote.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    # `apps_dir()` resolves `config_dir()` from this module's globals at CALL time, so this one
    # patch also relocates `app_dir` — which is what `ProviderSettings` and the provider
    # loader both use to find an installed app.
    monkeypatch.setattr("personalclaw.apps.manager.config_dir", lambda: home)
    return home, app, remote


@pytest.fixture
def registered(installed_app):
    """The app registered + enabled through the real provider registry, then cleaned up."""
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.providers.registry import get_provider_registry

    home, app, remote = installed_app
    registry = get_provider_registry()
    manifest = AppManifest.from_json_file(app / "app.json")
    registry.register(manifest, enabled=False)
    try:
        yield registry, home, app, remote
    finally:
        registry.deregister(APP_NAME)


def test_the_manifest_type_needs_no_core_change(registered):
    """The type is already in the closed set, so a third-party manifest is admissible as
    shipped. If this ever fails, criterion 10 needs a core change and is not met."""
    from personalclaw.apps.manifest import PROVIDER_TYPES

    assert "sync" in PROVIDER_TYPES


def test_enabling_registers_the_transport_under_its_own_name(registered):
    from personalclaw.sync_transports import get_transport

    registry, home, _app, remote = registered
    # Configure the folder through the provider settings store the routes write to, so the
    # factory receives it exactly as a user-configured app would.
    from personalclaw.providers.settings import ProviderSettings

    ProviderSettings.save(APP_NAME, {"folder": str(remote)})
    assert registry.enable(APP_NAME) is True
    transport = get_transport(APP_NAME)
    assert transport is not None
    assert transport.name == APP_NAME
    assert transport.test().ok is True
    # And disabling it takes it back out — the one-source-of-truth lifecycle.
    registry.disable(APP_NAME)
    assert get_transport(APP_NAME) is None


def _providers_app() -> web.Application:
    from personalclaw.providers.routes import register_routes

    @web.middleware
    async def identity(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    app = web.Application(middlewares=[identity])
    register_routes(app)
    return app


@pytest.mark.asyncio
async def test_it_configures_through_the_standard_provider_routes(registered):
    """The criterion's "configures via the standard provider settings routes" clause, driven.

    No durability-specific config endpoint is involved: the list, the schema and the config
    PATCH are the generic `/api/providers` ones.
    """
    registry, home, _app, remote = registered
    async with TestClient(TestServer(_providers_app())) as client:
        listed = await client.get("/api/providers?type=sync")
        assert listed.status == 200
        names = [p["name"] for p in (await listed.json())["providers"]]
        assert APP_NAME in names, "a third-party sync app is invisible to the providers list"

        schema = await client.get(f"/api/providers/{APP_NAME}/schema")
        assert schema.status == 200
        props = (await schema.json())["schema"]["properties"]
        assert "folder" in props, "the app's own settings schema did not survive the route"

        patched = await client.patch(
            f"/api/providers/{APP_NAME}/config", json={"folder": str(remote)}
        )
        assert patched.status == 200

        read_back = await client.get(f"/api/providers/{APP_NAME}/config")
        assert (await read_back.json())["config"]["folder"] == str(remote)


def test_it_actually_syncs_through_the_configured_transport(registered, monkeypatch):
    """The last clause: `run_sync_job` resolves the app by name and a real cycle pushes.

    The vacuity floor is the remote folder — an empty one would mean the job "succeeded"
    without a transport ever being called, which is exactly how a skip reads as a pass.
    """
    from personalclaw.config.loader import DurabilityConfig
    from personalclaw.durability import service
    from personalclaw.providers.settings import ProviderSettings

    registry, home, _app, remote = registered
    ProviderSettings.save(APP_NAME, {"folder": str(remote)})
    assert registry.enable(APP_NAME) is True

    # Something worth syncing, in a row-merge entry.
    (home / "tasks").mkdir(parents=True, exist_ok=True)
    (home / "tasks" / "t1.json").write_text(json.dumps({"id": "t1", "title": "sync me"}))

    monkeypatch.setattr(
        service,
        "_cfg",
        lambda: DurabilityConfig(sync_enabled=True, sync_transport=APP_NAME, sync_encrypt="off"),
    )
    result = service.run_sync_job()
    assert not result.skipped, f"the sync job skipped instead of running: {result.skipped}"
    assert result.ok, f"the cycle failed: {result.detail}"
    pushed = [p for p in remote.rglob("*") if p.is_file()]
    assert pushed, "the cycle reported success but the transport received nothing"
    # The shard payload really is this machine's state, not an empty envelope.
    assert any("t1" in p.read_text(errors="ignore") for p in pushed)
    # And the credentials rail holds on a transport core has never seen.
    for p in pushed:
        body = p.read_text(errors="ignore")
        for secret in (".local_secret", "sel_hmac.key", "telemetry_salt", "ANTHROPIC_API_KEY"):
            assert secret not in body


def test_the_app_names_nothing_in_core(registered):
    """ "Zero core changes", checked rather than asserted: no file under `src/personalclaw`
    mentions this app. A hand-wired special case in the sync registry would show up here."""
    import subprocess

    src = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
    hit = subprocess.run(
        ["grep", "-rl", APP_NAME, str(src)], capture_output=True, text=True
    ).stdout.strip()
    assert hit == "", f"core mentions the third-party app: {hit}"
