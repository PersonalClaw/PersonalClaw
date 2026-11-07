"""The gateway says it has the desktop window's door, and the desktop reads the same name.

A desktop app asks ``GET /api/token/local?for=window`` only of a gateway that says it has that
door. Asked of one that doesn't, it would write that gateway's unbound ready token into its window
cookie. So this build says so (``sign_in_pair.WINDOW_DOOR``), whatever the auth mode:
- on the ready line;
- in the health read, only beside its proof, so a caller trusts it no sooner than the proof.

The desktop side reads the same name. Parsed from source, as ``test_desktop_home_gateway_parity``
does: ``homeGateway.js`` declares it, the file that asks the door reads it beyond the declaration,
and every other desktop file that uses it takes it from ``homeGateway.js``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from personalclaw import gateway, home_gateway
from personalclaw.dashboard import token_auth
from personalclaw.dashboard.handlers_system import api_healthz
from personalclaw.dashboard.session_store import ISSUER_READY
from personalclaw.dashboard.sign_in_pair import WINDOW_DOOR

SECRET = "the-local-secret-of-this-start"
DESKTOP = Path(__file__).resolve().parent.parent / "desktop"
_HOME_GATEWAY = DESKTOP / "homeGateway.js"


def test_the_ready_line_says_the_build_has_the_window_door(tmp_path) -> None:
    token_auth.use_ephemeral_secret(b"the-window-door-flag")
    try:
        minted = token_auth.mint_session("local-startup", 600, issuer=ISSUER_READY)
        line = gateway.ready_line(port=4318, home=tmp_path, minted=minted)
    finally:
        token_auth.use_persistent_secret()
    payload = json.loads(line.split(":", 1)[1])
    assert payload[WINDOW_DOOR] is True


@pytest.mark.asyncio
async def test_the_health_read_says_so_only_beside_its_proof() -> None:
    app = web.Application()
    app["local_secret"] = SECRET
    app.router.add_get("/api/healthz", api_healthz)
    async with TestServer(app, host="127.0.0.1") as server:
        async with ClientSession() as http:
            nonce = home_gateway.new_nonce()
            proven = await (
                await http.get(f"{server.make_url('/api/healthz')}?nonce={nonce}")
            ).json()
            bare = await (await http.get(str(server.make_url("/api/healthz")))).json()
    assert proven["proof"] and proven[WINDOW_DOOR] is True
    assert "proof" not in bare and WINDOW_DOOR not in bare, "the flag rides without a proof"


_DECLARED = re.compile(r'^const WINDOW_DOOR_FIELD = "([^"]*)";$', re.MULTILINE)
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


def _code(text: str) -> str:
    """*text* without its comments, so a name or a query in prose counts for nothing."""
    return _COMMENTS.sub("", text)


def _desktop_files() -> list:
    return sorted(p for p in DESKTOP.iterdir() if p.suffix == ".js" and p.is_file())


def test_home_gateway_declares_the_gateways_name_for_the_flag() -> None:
    declared = _DECLARED.findall(_HOME_GATEWAY.read_text(encoding="utf-8"))
    assert declared == [
        WINDOW_DOOR
    ], f'desktop/homeGateway.js must declare const WINDOW_DOOR_FIELD = "{WINDOW_DOOR}"; once'


def test_the_file_that_asks_the_door_reads_the_flag_first() -> None:
    asking = [p for p in _desktop_files() if "for=window" in _code(p.read_text(encoding="utf-8"))]
    assert asking, "no desktop file asks the window's door, so the rail checks nothing"
    for path in asking:
        code = _DECLARED.sub("", _code(path.read_text(encoding="utf-8")))
        uses = [line for line in code.splitlines() if "WINDOW_DOOR_FIELD" in line]
        assert any(
            line.strip() != "WINDOW_DOOR_FIELD," for line in uses
        ), f"desktop/{path.name} asks the window's door without reading the gateway's flag"


def test_every_other_desktop_file_takes_the_name_from_home_gateway() -> None:
    for path in _desktop_files():
        if path == _HOME_GATEWAY:
            continue
        code = _code(path.read_text(encoding="utf-8"))
        if "WINDOW_DOOR_FIELD" not in code:
            continue
        assert not _DECLARED.search(code), f"desktop/{path.name} declares a second copy"
        assert re.search(
            r'\{[^}]*\bWINDOW_DOOR_FIELD\b[^}]*\}\s*=\s*require\("\./homeGateway"\)', code
        ), f"desktop/{path.name} uses WINDOW_DOOR_FIELD without taking it from homeGateway.js"
