"""The gateway as it runs records where it listens, binds that, and builds its origins and a
pairing link from the same record.

``test_a_phone_is_paired_to_an_address_it_can_reach.py`` holds the rules on the helpers. This starts
the dashboard itself (``start_dashboard``), every middleware in front of the routes, with its bind
decided on loopback, so nothing in a test listens beyond this machine, and a fake network address
for that bind: the listener holds the recorded bind, the origin set holds that bind's network
address, and a pairing asked on loopback names it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import aiohttp
import pytest
from signed_in_gateway import AUTH_SHORTCUTS

from personalclaw.dashboard import origin

LAN = "192.0.2.5"


def _home(tmp_path, monkeypatch):
    """A home of the test's own, and a gateway whose one bind decision is recorded and answers
    loopback whatever it is asked, so nothing listens beyond this machine."""
    home, user_home = tmp_path / "home", tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)
    asked: list[tuple[bool, bool | None]] = []

    def decide(local_only, no_auth=None):
        asked.append((local_only, no_auth))
        return "127.0.0.1"

    monkeypatch.setattr(server_mod, "gateway_bind_host", decide)
    monkeypatch.setattr(origin, "lan_address", lambda bind: LAN if bind == "127.0.0.1" else "")
    return home, server_mod, asked


@pytest.mark.asyncio
async def test_the_gateway_binds_the_address_it_records_and_a_pairing_names_its_network_address(
    tmp_path, monkeypatch
):
    home, server_mod, asked = _home(tmp_path, monkeypatch)
    runner, _state = await server_mod.start_dashboard(
        sessions=MagicMock(count=0), port=0, local_only=False
    )
    try:
        assert asked == [(False, False)], "decided once, with sign-in on"
        app = runner.app
        host, port = runner.addresses[0][:2]
        assert host == app["bind_host"] == "127.0.0.1", "the listener binds the recorded bind"
        assert f"http://{LAN}:{app['port']}" in app["allowed_origins"]

        secret = (home / ".local_secret").read_text(encoding="utf-8").strip()
        base = f"http://127.0.0.1:{port}"
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as http:
            minted = await http.get(f"{base}/api/token/local", headers={"X-Local-Secret": secret})
            assert minted.status == 200, await minted.text()
            owner = {
                "Authorization": f"Bearer {(await minted.json())['token']}",
                "X-Local-Secret": secret,
            }
            started = await http.post(f"{base}/api/devices/pair/start", json={}, headers=owner)
            assert started.status == 200, await started.text()
            link = (await started.json())["pairing_url"]
        assert link.startswith(f"http://{LAN}:{app['port']}/pair?code="), link
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_a_gateway_with_sign_in_off_decides_its_bind_as_one_that_stays_on_loopback(
    tmp_path, monkeypatch
):
    """The bind decision is told sign-in is off (``no_auth``), the invariant that keeps such a
    gateway on loopback whatever it was asked (``origin.gateway_bind_host``)."""
    _home_dir, server_mod, asked = _home(tmp_path, monkeypatch)
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    runner, _state = await server_mod.start_dashboard(
        sessions=MagicMock(count=0), port=0, local_only=False
    )
    try:
        assert asked == [(False, True)], "decided once, told sign-in is off"
        assert runner.addresses[0][0] == runner.app["bind_host"] == "127.0.0.1"
    finally:
        await runner.cleanup()
