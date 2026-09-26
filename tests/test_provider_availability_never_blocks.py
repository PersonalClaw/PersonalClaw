"""Settings → Providers never runs an app's availability hook inside the gateway.

Measured before the fix, in the container: ``GET /api/providers`` called every provider
app's ``availability()`` synchronously on the event loop, per request. Sentence Transformers'
hook imported torch (171.8 s cold), kiro-cli's ran ``npm root -g`` (6.8 s) — so the list and
a concurrent ``GET /api/healthz`` both exceeded 120 s and the container's health check
recorded 161 s.

These drive the REAL route and the REAL availability child process against an app planted in
the test's own home, whose hook is slow, prints to stdout (the protocol channel), and writes
down the PID of the process that ran it. Nothing about the probe is mocked: the answer the
card shows has to have come out of the child.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.native_contract import namespaced_module_name
from personalclaw.config import loader as config_loader
from personalclaw.providers import routes as provider_routes
from personalclaw.providers.registry import ProviderRegistry

APP = "slow-probe-app"


def _board(**kwargs):
    # Imported here, not at module top, so the two listing tests below still RUN (and fail on
    # their assertions) against a tree that predates the board.
    from personalclaw.providers.availability import AvailabilityBoard

    return AvailabilityBoard(**kwargs)


async def _stop(board) -> None:
    if board is not None:
        await board.shutdown()


_PROVIDER = """
import json, os, time
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def availability():
    state = json.loads((_HERE / "behaviour.json").read_text())
    (_HERE / "ran-in.pid").write_text(str(os.getpid()))
    print("a hook that prints must not corrupt the probe protocol")
    time.sleep(state["sleep"])
    needs = state.get("needs")
    if needs:
        import importlib.util

        found = importlib.util.find_spec(needs) is not None
        return found, "" if found else f"{needs} is not installed"
    return state["ok"], state["reason"]


def create_provider(config=None):
    return None
"""


def _plant_app(home: Path, *, sleep: float, ok: bool, reason: str) -> Path:
    root = home / "apps" / APP
    root.mkdir(parents=True, exist_ok=True)
    (root / "installed.json").write_text(
        json.dumps({"name": APP, "version": "0.1.0", "enabled": True, "origin": "local"})
    )
    (root / "app.json").write_text(
        json.dumps(
            {
                "name": APP,
                "version": "0.1.0",
                "displayName": "Slow Probe",
                "description": "An app whose availability check is slow.",
                "provider": {"type": "tool", "implementation": "provider:create_provider"},
            }
        )
    )
    (root / "provider.py").write_text(_PROVIDER)
    _behave(root, sleep=sleep, ok=ok, reason=reason)
    return root


def _behave(root: Path, *, sleep: float, ok: bool, reason: str, needs: str = "") -> None:
    (root / "behaviour.json").write_text(
        json.dumps({"sleep": sleep, "ok": ok, "reason": reason, "needs": needs})
    )


@pytest.fixture
def planted(monkeypatch):
    """The app installed in this test's home, a private registry holding it, a fresh board."""
    # Resolved through the loader MODULE at call time: a `from ... import config_dir` at
    # module top binds the function before conftest's home redirect exists, and this fixture
    # then plants its app in the developer's real home.
    home = config_loader.config_dir()
    assert home != Path.home() / ".personalclaw", "refusing to plant a test app in the real home"
    root = _plant_app(home, sleep=2.0, ok=False, reason="needs the frobnicator")
    registry = ProviderRegistry()
    registry.register(AppManifest.from_json_file(root / "app.json"))
    monkeypatch.setattr(provider_routes, "get_provider_registry", lambda: registry)
    try:
        board = _board()
    except ImportError:
        board = None
    monkeypatch.setattr(provider_routes, "get_availability_board", lambda: board, raising=False)
    yield root, board
    sys.modules.pop(namespaced_module_name(APP, "provider"), None)


def _app() -> web.Application:
    app = web.Application()
    provider_routes.register_routes(app)

    async def healthz(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app.router.add_get("/api/healthz", healthz)
    return app


async def _card(client: TestClient) -> dict:
    resp = await client.get("/api/providers")
    assert resp.status == 200
    body = await resp.json()
    return next(p for p in body["providers"] if p["name"] == APP)


async def _settled(client: TestClient, *, within: float = 45.0) -> dict:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        card = await _card(client)
        if card["availability"]["state"] != "checking":
            return card
        await asyncio.sleep(0.25)
    raise AssertionError(f"availability never settled within {within:.0f} s")


@pytest.mark.asyncio
async def test_listing_providers_runs_no_app_code_in_the_gateway(planted):
    """The list answers at once, reading ``checking`` — and never imports the app's module."""
    root, board = planted
    async with TestClient(TestServer(_app())) as client:
        started = time.monotonic()
        card = await _card(client)
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"/api/providers took {elapsed:.2f}s — it waited on the hook"
        assert card["availability"]["state"] == "checking"
        assert (
            namespaced_module_name(APP, "provider") not in sys.modules
        ), "the gateway imported the app's provider module just to list it"
        await _stop(board)


@pytest.mark.asyncio
async def test_healthz_stays_fast_while_providers_loads_cold(planted):
    """A cold Providers load and a health check at the same time: health stays under 1 s."""
    _root, board = planted
    async with TestClient(TestServer(_app())) as client:

        async def timed_healthz() -> float:
            started = time.monotonic()
            resp = await client.get("/api/healthz")
            assert resp.status == 200
            return time.monotonic() - started

        listing = asyncio.ensure_future(client.get("/api/providers"))
        health = [await timed_healthz() for _ in range(5)]
        await listing
        assert max(health) < 1.0, f"/api/healthz took {max(health):.2f}s during a Providers load"
        await _stop(board)


@pytest.mark.asyncio
async def test_the_answer_comes_from_the_child_and_carries_the_apps_reason(planted):
    root, board = planted
    async with TestClient(TestServer(_app())) as client:
        await _card(client)
        card = await _settled(client)
    assert card["availability"]["state"] == "unavailable"
    assert card["availability"]["reason"] == "needs the frobnicator"
    assert card["availability"]["checkedAt"] is not None
    ran_in = int((root / "ran-in.pid").read_text())
    assert ran_in != os.getpid(), "the hook ran inside the gateway process"
    await _stop(board)


@pytest.mark.asyncio
async def test_a_hook_that_never_returns_is_unknown_at_the_deadline_and_its_child_is_killed(
    planted, monkeypatch
):
    root, _unused = planted
    _behave(root, sleep=600, ok=True, reason="")
    # The deadline has to leave the child time to START and reach the hook — a cold
    # `python -m personalclaw` took 3-4 s on a loaded host — or the kill proves nothing.
    board = _board(deadline_secs=15.0)
    monkeypatch.setattr(provider_routes, "get_availability_board", lambda: board)
    async with TestClient(TestServer(_app())) as client:
        await _card(client)
        card = await _settled(client, within=90.0)
    assert card["availability"]["state"] == "unknown"
    assert "did not finish within 15 s" in card["availability"]["reason"]
    pid_file = root / "ran-in.pid"
    assert pid_file.is_file(), "the child never reached the hook, so its kill proves nothing"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)  # the child that was stuck in the hook is gone


@pytest.mark.asyncio
async def test_a_package_the_installer_put_in_the_home_is_visible_to_the_check(planted):
    """An app's packages live in ``<home>/app-python`` (#3605), on the gateway's import path
    once ``register_extension_providers`` activates it. The child must activate it too, or a
    hook asking "is my package installed?" answers for a process that cannot see it."""
    from personalclaw.apps import app_python

    root, board = planted
    package = app_python.site_dirs()[0] / "pclaw_probe_only_pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    _behave(root, sleep=0, ok=False, reason="", needs="pclaw_probe_only_pkg")
    async with TestClient(TestServer(_app())) as client:
        await _card(client)
        card = await _settled(client)
    assert card["availability"]["state"] == "available", card["availability"]
    await _stop(board)


@pytest.mark.asyncio
async def test_check_again_measures_again(planted):
    root, board = planted
    _behave(root, sleep=0, ok=False, reason="not yet")
    async with TestClient(TestServer(_app())) as client:
        await _card(client)
        assert (await _settled(client))["availability"]["state"] == "unavailable"

        _behave(root, sleep=0, ok=True, reason="")
        resp = await client.post(f"/api/providers/{APP}/availability")
        assert resp.status == 202
        assert (await resp.json())["availability"]["state"] == "checking"
        card = await _settled(client)
    assert card["availability"]["state"] == "available"
    await _stop(board)


@pytest.mark.asyncio
async def test_check_again_on_an_unknown_provider_is_404(planted):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/providers/no-such-app/availability")
        assert resp.status == 404
        assert (await resp.json())["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_a_stale_answer_keeps_being_served_while_it_is_measured_again(planted, monkeypatch):
    root, _unused = planted
    _behave(root, sleep=0, ok=False, reason="first answer")
    board = _board(ttl_secs=0.0)
    monkeypatch.setattr(provider_routes, "get_availability_board", lambda: board)
    async with TestClient(TestServer(_app())) as client:
        await _card(client)
        first = await _settled(client)
        assert first["availability"]["reason"] == "first answer"
        _behave(root, sleep=3, ok=False, reason="second answer")
        # Stale (ttl 0): this read schedules a re-measure, and still answers with the old one.
        again = await _card(client)
        assert again["availability"]["state"] == "unavailable"
        assert again["availability"]["reason"] == "first answer"
    await _stop(board)


def test_a_malformed_protocol_line_is_dropped_not_trusted():
    from personalclaw.providers.availability import parse_answer

    assert parse_answer(b"not json") is None
    assert parse_answer(b"[]") is None
    assert parse_answer(json.dumps({"name": APP, "state": "sure"})) is None
    assert parse_answer(json.dumps({"name": APP, "state": "checking"})) is None
    parsed = parse_answer(json.dumps({"name": APP, "implementation": "p:f", "state": "available"}))
    assert parsed is not None and parsed[2].state == "available"
