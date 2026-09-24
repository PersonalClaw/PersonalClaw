"""Desktop capability seam (DESKTOP-CAPABILITIES DC-2 — C2/C3).

Covers the gateway half of the bridge: the registry's fail-closed behavior, the
loopback + credential rails on the three shell-side writes, what a plain browser tab
reads, and the app-manifest ``desktop`` permission.

The Electron half's state machine is unit-tested in ``desktop/test/capabilities.test.js``
(``node --test``); the vocabulary rail below is what keeps the two sides from drifting.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import app_manager, manager
from personalclaw.apps.manifest import Permissions
from personalclaw.apps.permissions import PermissionChecker
from personalclaw.dashboard.desktop_registry import (
    CAPABILITIES,
    GRANT_STATES,
    DesktopRegistry,
    normalize_manifest,
)
from personalclaw.dashboard.handlers import desktop as desktop_handlers

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_SECRET = "s3cret-local"


# ── The two-sided vocabulary rail ──────────────────────────────────────


def test_capability_vocabulary_matches_the_electron_side():
    """The shell and the gateway must agree on the capability names and states.

    A capability added to only one side would register (or read) as an unknown name
    and silently vanish, which is exactly the half-wired shape this rail exists to
    prevent. Parsed from source rather than executed — node is not a test dependency.
    """
    src = (REPO_ROOT / "desktop" / "capabilities.js").read_text(encoding="utf-8")

    def _array(name: str) -> list[str]:
        m = re.search(rf"const {name} = \[(.*?)\];", src, re.S)
        assert m, f"{name} not found in desktop/capabilities.js"
        return re.findall(r'"([^"]+)"', m.group(1))

    assert sorted(_array("CAPABILITIES")) == sorted(CAPABILITIES)
    assert sorted(_array("GRANT_STATES")) == sorted(GRANT_STATES)


def test_every_capability_has_an_electron_spec():
    """Each name in the vocabulary must have a SPEC entry, or probe() returns
    ``unknown capability`` for a capability the gateway happily stores."""
    src = (REPO_ROOT / "desktop" / "capabilities.js").read_text(encoding="utf-8")
    specs = re.search(r"const SPECS = \{(.*?)\n\};", src, re.S)
    assert specs
    declared = set(re.findall(r"^  (\w+): \{", specs.group(1), re.M))
    assert declared == set(CAPABILITIES)


# ── Registry: fail closed ──────────────────────────────────────────────


def test_registry_starts_disconnected_with_no_capabilities():
    reg = DesktopRegistry()
    snap = reg.snapshot()
    assert snap["connected"] is False
    assert snap["capabilities"] == {}
    assert snap["shell"] is None
    # Not "the six names with a placeholder state" — absence is the honest answer.
    assert reg.capability("audio_capture") is None


def test_registry_never_exposes_the_token():
    reg = DesktopRegistry()
    token = reg.register(shell={"version": "0.1.0", "platform": "darwin"}, capabilities={})
    assert token
    assert token not in json.dumps(reg.snapshot())
    assert not any("token" in k for k in reg.snapshot())


def test_registry_verifies_and_rotates_the_token():
    reg = DesktopRegistry()
    first = reg.register(shell={}, capabilities={})
    assert reg.verify(first) is True
    assert reg.verify("") is False
    assert reg.verify(first + "x") is False
    second = reg.register(shell={}, capabilities={})
    # A shell restart invalidates the old token, so a stale process cannot keep
    # writing capability state for a shell that is gone.
    assert first != second
    assert reg.verify(first) is False
    assert reg.verify(second) is True


def test_update_and_unregister_require_the_token():
    reg = DesktopRegistry()
    token = reg.register(
        shell={},
        capabilities={"tray": {"available": True, "granted": "granted"}},
    )
    assert reg.update(token="wrong", capabilities={}) is False
    assert reg.snapshot()["capabilities"]["tray"]["granted"] == "granted"  # unchanged
    assert reg.update(token=token, capabilities={}) is True
    assert reg.snapshot()["capabilities"] == {}
    assert reg.unregister("wrong") is False
    assert reg.snapshot()["connected"] is True
    assert reg.unregister(token) is True
    assert reg.snapshot()["connected"] is False


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "granted",
        {"granted": "yes-please"},
        {"available": True, "granted": "sudo"},
        {"available": "truthy", "granted": "granted", "requestable": True},
    ],
)
def test_normalize_fails_closed_on_garbage(raw):
    """An unparseable capability entry must never inherit a permissive default."""
    out = normalize_manifest({"audio_capture": raw})["audio_capture"]
    if out["granted"] != "granted":
        assert out["available"] is False
        assert out["requestable"] is False


def test_normalize_drops_unknown_capability_names():
    out = normalize_manifest(
        {
            "audio_capture": {"available": True, "granted": "granted"},
            "read_keychain": {"available": True, "granted": "granted"},
        }
    )
    assert set(out) == {"audio_capture"}


def test_normalize_forces_requestable_false_when_unavailable():
    out = normalize_manifest(
        {"screen_capture": {"available": False, "granted": "denied", "requestable": True}}
    )["screen_capture"]
    assert out == {
        "available": False,
        "granted": "unavailable",
        "requestable": False,
        "reason": "",
    }


# ── HTTP: the shell-side rails ─────────────────────────────────────────


@asynccontextmanager
async def _client(tmp_path, monkeypatch, *, local_secret=LOCAL_SECRET):
    """A client over the five desktop routes.

    ``X-Test-Remote`` rewrites ``request.remote`` so the loopback rail can be driven
    from a test (aiohttp's test server is always loopback), and ``X-Test-App`` stands
    in for a verified app-scoped token exactly as ``token_auth`` would stamp it.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))  # SEL binds here

    class _State:
        pass

    state = _State()
    state.desktop = DesktopRegistry()

    @web.middleware
    async def stamp(request, handler):
        forced = request.headers.get("X-Test-Remote", "")
        if forced:
            # aiohttp's test server is always loopback, so the only way to drive the
            # non-loopback rail is to clone the request with a different peer.
            request = request.clone(remote=forced)
        ident = request.headers.get("X-Test-App", "")
        if ident:
            request["app"] = ident
        return await handler(request)

    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        app = web.Application(middlewares=[stamp])
        app["state"] = state
        if local_secret is not None:
            app["local_secret"] = local_secret
        app.router.add_post("/api/desktop/register", desktop_handlers.api_desktop_register)
        app.router.add_post("/api/desktop/unregister", desktop_handlers.api_desktop_unregister)
        app.router.add_get("/api/desktop/state", desktop_handlers.api_desktop_state)
        app.router.add_post("/api/desktop/state", desktop_handlers.api_desktop_state_push)
        app.router.add_get(
            "/api/desktop/capabilities/{cap}", desktop_handlers.api_desktop_capability
        )
        async with TestClient(TestServer(app)) as client:
            client.desktop = state.desktop
            yield client


_MANIFEST = {
    "audio_capture": {
        "available": True,
        "granted": "not-determined",
        "requestable": True,
        "reason": "",
    },
    "screen_capture": {
        "available": True,
        "granted": "denied",
        "requestable": False,
        "reason": "Grant Screen Recording in System Settings.",
    },
    "tray": {"available": True, "granted": "granted", "requestable": False, "reason": ""},
}


def _sel_rows(tmp_path: Path) -> list[dict]:
    p = tmp_path / "security_events.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _ops(tmp_path: Path, outcome: str = "denied") -> list[str]:
    return [r["operation"] for r in _sel_rows(tmp_path) if r.get("outcome") == outcome]


async def _register(client) -> str:
    res = await client.post(
        "/api/desktop/register",
        json={"shell": {"version": "0.1.0", "platform": "darwin"}, "capabilities": _MANIFEST},
        headers={"X-Local-Secret": LOCAL_SECRET},
    )
    assert res.status == 200, await res.text()
    body = await res.json()
    return body["shell_token"]


@pytest.mark.asyncio
async def test_register_mints_a_token_and_stores_the_manifest(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        token = await _register(client)
        assert token and len(token) >= 32
        snap = client.desktop.snapshot()
        assert snap["connected"] is True
        assert snap["shell"] == {"version": "0.1.0", "platform": "darwin"}
        assert snap["capabilities"]["screen_capture"]["requestable"] is False
        # The audit names the capabilities, never the token.
        rows = [r for r in _sel_rows(tmp_path) if r["operation"] == "desktop.register"]
        assert rows and rows[-1]["outcome"] == "success"
        assert token not in json.dumps(rows)


@pytest.mark.asyncio
async def test_register_without_the_local_secret_is_403_and_sel_logged(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        res = await client.post("/api/desktop/register", json={"capabilities": _MANIFEST})
        assert res.status == 403
        body = await res.json()
        assert body == {"error": "invalid secret"}
        assert client.desktop.snapshot()["connected"] is False  # fail closed
        assert "desktop.register" in _ops(tmp_path)
        assert any(r["resources"] == "invalid-secret" for r in _sel_rows(tmp_path))


@pytest.mark.asyncio
async def test_register_with_a_wrong_local_secret_leaks_nothing(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        res = await client.post(
            "/api/desktop/register",
            json={"capabilities": _MANIFEST},
            headers={"X-Local-Secret": "guess"},
        )
        assert res.status == 403
        text = await res.text()
        assert LOCAL_SECRET not in text and "guess" not in text
        assert LOCAL_SECRET not in json.dumps(_sel_rows(tmp_path))


@pytest.mark.asyncio
async def test_register_is_503_when_no_secret_was_minted(tmp_path, monkeypatch):
    """No local secret → refuse, rather than fall back to loopback-only (which would
    let any local process register a capability manifest)."""
    async with _client(tmp_path, monkeypatch, local_secret=None) as client:
        res = await client.post("/api/desktop/register", json={"capabilities": _MANIFEST})
        assert res.status == 503


@pytest.mark.asyncio
async def test_non_loopback_register_is_refused_before_the_credential(tmp_path, monkeypatch):
    """A remote caller must not even reach the secret comparison — no token oracle."""
    async with _client(tmp_path, monkeypatch) as client:
        res = await client.post(
            "/api/desktop/register",
            json={"capabilities": _MANIFEST},
            headers={"X-Local-Secret": LOCAL_SECRET, "X-Test-Remote": "10.1.2.3"},
        )
        assert res.status == 403
        assert await res.json() == {"error": "loopback only"}
        assert client.desktop.snapshot()["connected"] is False
        rows = [r for r in _sel_rows(tmp_path) if r["operation"] == "desktop.register"]
        assert rows and rows[-1]["resources"] == "non-loopback"


@pytest.mark.asyncio
async def test_state_push_requires_the_shell_token(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        token = await _register(client)
        bad = await client.post(
            "/api/desktop/state",
            json={"capabilities": {}},
            headers={"X-Shell-Token": "not-the-token"},
        )
        assert bad.status == 403
        assert await bad.json() == {"error": "invalid shell token"}
        assert client.desktop.snapshot()["capabilities"]  # unchanged
        assert "desktop.state.push" in _ops(tmp_path)

        ok = await client.post(
            "/api/desktop/state",
            json={
                "capabilities": {
                    "audio_capture": {"available": True, "granted": "granted", "reason": ""}
                }
            },
            headers={"X-Shell-Token": token},
        )
        assert ok.status == 200
        assert client.desktop.snapshot()["capabilities"]["audio_capture"]["granted"] == "granted"
        assert "tray" not in client.desktop.snapshot()["capabilities"]  # replaced, not merged


@pytest.mark.asyncio
async def test_state_push_from_a_non_loopback_caller_is_refused(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        token = await _register(client)
        res = await client.post(
            "/api/desktop/state",
            json={"capabilities": {}},
            headers={"X-Shell-Token": token, "X-Test-Remote": "192.168.1.9"},
        )
        assert res.status == 403
        assert await res.json() == {"error": "loopback only"}
        assert client.desktop.snapshot()["capabilities"]  # unchanged


@pytest.mark.asyncio
async def test_unregister_disconnects_and_needs_the_token(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        token = await _register(client)
        bad = await client.post("/api/desktop/unregister", json={})
        assert bad.status == 403
        assert client.desktop.snapshot()["connected"] is True
        ok = await client.post("/api/desktop/unregister", json={}, headers={"X-Shell-Token": token})
        assert ok.status == 200
        state = await (await client.get("/api/desktop/state")).json()
        assert state == {
            "connected": False,
            "shell": None,
            "capabilities": {},
            "registered_at": "",
            "last_seen": "",
        }


# ── HTTP: what a browser tab reads ─────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_tab_reads_not_connected(tmp_path, monkeypatch):
    """No shell → ``connected: false`` and an EMPTY capability map. The panel renders
    'not connected' from the absence; nothing advertises a capability that cannot be
    delivered."""
    async with _client(tmp_path, monkeypatch) as client:
        body = await (await client.get("/api/desktop/state")).json()
        assert body["connected"] is False
        assert body["capabilities"] == {}


@pytest.mark.asyncio
async def test_state_reflects_the_registered_manifest(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        await _register(client)
        body = await (await client.get("/api/desktop/state")).json()
        assert body["connected"] is True
        assert set(body["capabilities"]) == set(_MANIFEST)
        assert body["capabilities"]["audio_capture"]["requestable"] is True
        assert body["registered_at"]


@pytest.mark.asyncio
async def test_capability_route_404s_for_an_unknown_name(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        await _register(client)
        res = await client.get("/api/desktop/capabilities/read_keychain")
        assert res.status == 404


@pytest.mark.asyncio
async def test_capability_route_404s_when_the_shell_is_absent(tmp_path, monkeypatch):
    """Fail closed: never a synthesized 'not granted yet' an app could retry against."""
    async with _client(tmp_path, monkeypatch) as client:
        res = await client.get("/api/desktop/capabilities/audio_capture")
        assert res.status == 404
        assert (await res.json())["error"] == "desktop shell not connected"


# ── C3: the app-manifest ``desktop`` permission ────────────────────────


def _install(tmp_path: Path, name: str, *, desktop: list[str] | None = None):
    d = tmp_path / "src" / name
    d.mkdir(parents=True)
    mani = {"name": name, "version": "1.0.0", "displayName": name, "description": "x"}
    if desktop is not None:
        mani["permissions"] = {"desktop": desktop}
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    res = app_manager.install(d)
    assert res.ok, res.error


def test_checker_is_exact_match_and_deny_by_default():
    assert PermissionChecker("a", Permissions()).can_use_desktop("audio_capture") is False
    held = PermissionChecker("a", Permissions(desktop=["audio_capture"]))
    assert held.can_use_desktop("audio_capture") is True
    assert held.can_use_desktop("screen_capture") is False
    assert held.can_use_desktop("") is False
    # No wildcard: "everything native this host can do" is not a clickable grant.
    assert PermissionChecker("a", Permissions(desktop=["*"])).can_use_desktop("tray") is False
    assert (
        PermissionChecker("a", Permissions(desktop=["audio_*"])).can_use_desktop("audio_capture")
        is False
    )


def test_permission_round_trips_through_the_manifest():
    p = Permissions.from_dict({"desktop": ["audio_capture", "native_notifications"]})
    assert p.desktop == ["audio_capture", "native_notifications"]
    assert p.to_dict()["desktop"] == ["audio_capture", "native_notifications"]
    # Absent when undeclared, so an app that wants nothing native shows nothing.
    assert "desktop" not in Permissions().to_dict()


@pytest.mark.asyncio
async def test_app_with_the_grant_reads_its_capability(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "voice-app", desktop=["audio_capture"])
        await _register(client)
        res = await client.get(
            "/api/desktop/capabilities/audio_capture", headers={"X-Test-App": "voice-app"}
        )
        assert res.status == 200
        body = await res.json()
        assert body["capability"] == "audio_capture"
        assert body["granted"] == "not-determined"


@pytest.mark.asyncio
async def test_app_without_the_grant_gets_403_and_a_sel_row(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "quiet-app")
        await _register(client)
        res = await client.get(
            "/api/desktop/capabilities/audio_capture", headers={"X-Test-App": "quiet-app"}
        )
        assert res.status == 403
        assert "desktop.capability_denied" in _ops(tmp_path)
        row = [r for r in _sel_rows(tmp_path) if r["operation"] == "desktop.capability_denied"][-1]
        assert row["caller_identity"] == "app:quiet-app"
        assert row["resources"] == "audio_capture"


@pytest.mark.asyncio
async def test_app_declaring_one_capability_cannot_read_another(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "voice-app", desktop=["audio_capture"])
        await _register(client)
        res = await client.get(
            "/api/desktop/capabilities/screen_capture", headers={"X-Test-App": "voice-app"}
        )
        assert res.status == 403


@pytest.mark.asyncio
async def test_app_with_no_desktop_grant_cannot_read_the_whole_state(tmp_path, monkeypatch):
    async with _client(tmp_path, monkeypatch) as client:
        _install(tmp_path, "quiet-app")
        _install(tmp_path, "voice-app", desktop=["audio_capture"])
        await _register(client)
        denied = await client.get("/api/desktop/state", headers={"X-Test-App": "quiet-app"})
        assert denied.status == 403
        allowed = await client.get("/api/desktop/state", headers={"X-Test-App": "voice-app"})
        assert allowed.status == 200
        # The owner/dashboard identity is unaffected by the app gate.
        owner = await client.get("/api/desktop/state")
        assert owner.status == 200


@pytest.mark.asyncio
async def test_an_unknown_app_identity_is_denied(tmp_path, monkeypatch):
    """``checker_for`` returning None (uninstalled/unresolvable app) must deny."""
    async with _client(tmp_path, monkeypatch) as client:
        await _register(client)
        res = await client.get("/api/desktop/state", headers={"X-Test-App": "ghost-app"})
        assert res.status == 403
