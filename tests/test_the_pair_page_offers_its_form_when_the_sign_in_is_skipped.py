"""The /pair page offers its form where the sign-in is skipped, and goes home only when signed in.

The page decides for itself whether this browser is already signed in: one read of
``GET /api/auth/session``, sent with the cookie and the pair the page holds
(``handlers/devices.py``). The two modes that skip the sign-in, ``PERSONALCLAW_AUTH_MODE=none`` and
``PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1``, answer that read 200 to anyone. So the page goes home only
on the read's ``signed_in``, which says whether the request named a session, and only a sign-in
with its pair names one. Otherwise no phone, companion, desktop window or browse connector could
pair on such a gateway. These drive the real gateway (``start_dashboard``) in each mode, and run
the page's own decision in Node.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from browser_sign_in import browser_headers, browser_token, pair_of
from signed_in_gateway import AUTH_SHORTCUTS

from personalclaw.dashboard import session_store, token_auth
from personalclaw.dashboard.handlers import auth as auth_h
from personalclaw.dashboard.handlers import devices as devices_h
from personalclaw.dashboard.session_store import ISSUER_LOGIN
from personalclaw.dashboard.sign_in_pair import PAIR_HEADER

PORT = 10000
WRONG_PAIR = "W" * 43


@dataclass
class _Gateway:
    base: str
    port: int
    #: A browser's bound sign-in, minted after the start, so with the gateway's own secret.
    token: str


@contextlib.asynccontextmanager
async def _gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: dict[str, str]
) -> AsyncIterator[_Gateway]:
    """The real gateway on a free loopback port, with *mode*'s environment."""
    home = tmp_path / "home"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)
    for name, value in mode.items():
        monkeypatch.setenv(name, value)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)
    runner, _state = await server_mod.start_dashboard(sessions=MagicMock(count=0), port=0)
    try:
        port = runner.addresses[0][1]
        yield _Gateway(f"http://127.0.0.1:{port}", port, browser_token("owner", 600))
    finally:
        await runner.cleanup()


SKIPPING = {
    "auth mode none": {"PERSONALCLAW_AUTH_MODE": "none"},
    "bypassed local network": {"PERSONALCLAW_BYPASS_LOCAL_NETWORKS": "1"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(SKIPPING), ids=list(SKIPPING))
async def test_a_gateway_that_skips_the_sign_in_says_no_session_was_named(
    tmp_path, monkeypatch, mode
) -> None:
    async with _gateway(tmp_path, monkeypatch, SKIPPING[mode]) as gw:
        async with aiohttp.ClientSession() as http:
            read = await http.get(f"{gw.base}/api/auth/session")
            assert read.status == 200, await read.text()
            assert (await read.json())["signed_in"] is False
            page = await http.get(f"{gw.base}/pair", allow_redirects=False)
            assert page.status == 200, "the form is served"
            assert "id='f'" in await page.text()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(SKIPPING), ids=list(SKIPPING))
async def test_where_the_sign_in_is_skipped_only_a_sign_in_with_its_pair_is_signed_in(
    tmp_path, monkeypatch, mode
) -> None:
    async with _gateway(tmp_path, monkeypatch, SKIPPING[mode]) as gw:
        cookie = f"pc_token_{gw.port}"
        unbound = token_auth.mint_session("owner", 600, issuer=ISSUER_LOGIN).token
        asked = {
            "a bound cookie alone": {"Cookie": f"{cookie}={gw.token}"},
            "a wrong pair": {"Cookie": f"{cookie}={gw.token}", PAIR_HEADER: WRONG_PAIR},
            "an unbound cookie": {"Cookie": f"{cookie}={unbound}", PAIR_HEADER: pair_of(unbound)},
            "the sign-in with its pair": browser_headers(gw.token, port=gw.port),
        }
        told = {}
        async with aiohttp.ClientSession() as http:
            for name, headers in asked.items():
                read = await http.get(f"{gw.base}/api/auth/session", headers=headers)
                assert read.status == 200, (name, await read.text())
                told[name] = (await read.json())["signed_in"]
        assert told == {
            "a bound cookie alone": False,
            "a wrong pair": False,
            "an unbound cookie": False,
            "the sign-in with its pair": True,
        }


#: The page's own decision, run with a stand-in for the page and its one read.
_RUN_THE_PAGE = """
const {head, cases} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
(async () => {
  const out = [];
  for (const c of cases) {
    let went = null, sent = null;
    const window = {
      localStorage: { getItem: () => c.pair },
      location: { assign: (to) => { went = to; } },
    };
    const document = { getElementById: () => ({ getAttribute: () => '/home' }) };
    const fetch = (url, opts) => {
      sent = opts.headers;
      return Promise.resolve({ status: c.status, json: () => Promise.resolve(c.body) });
    };
    new Function('window', 'document', 'fetch', head)(window, document, fetch);
    await new Promise((resolve) => setTimeout(resolve, 0));
    out.push({ went, sent });
  }
  console.log(JSON.stringify(out));
})();
"""


def test_the_page_goes_home_only_on_the_reads_signed_in() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH, so the page's script cannot run here")
    head = devices_h._PAIR_SCRIPT.split("var params", 1)[0]
    pair = "P" * 43
    cases = {
        "signed in": (200, {"signed_in": True}, pair),
        "a skip mode's 200": (200, {"signed_in": False}, pair),
        "a 200 that names nothing": (200, {}, pair),
        "a refusal": (403, {"error": {"code": "session_required"}}, pair),
        "no pair kept": (200, {"signed_in": False}, ""),
    }
    stdin = {
        "head": head,
        "cases": [{"status": s, "body": b, "pair": p} for s, b, p in cases.values()],
    }
    proc = subprocess.run(
        [node, "-e", _RUN_THE_PAGE],
        input=json.dumps(stdin),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    ran = dict(zip(cases, json.loads(proc.stdout), strict=True))
    assert {name: r["went"] for name, r in ran.items()} == {
        "signed in": "/home",
        "a skip mode's 200": None,
        "a 200 that names nothing": None,
        "a refusal": None,
        "no pair kept": None,
    }
    assert ran["signed in"]["sent"] == {PAIR_HEADER: pair}, "the read is sent with the page's pair"
    assert ran["no pair kept"]["sent"] == {}


@pytest.fixture
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import personalclaw.config.loader as loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    for name in AUTH_SHORTCUTS:
        monkeypatch.delenv(name, raising=False)
    token_auth.use_ephemeral_secret(b"the-pair-page-and-the-skip-modes")
    token_auth.revoke_all_sessions()
    yield tmp_path
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


@pytest.mark.asyncio
async def test_a_signed_in_browser_is_told_so_and_its_cookie_alone_is_not(_isolated) -> None:
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=PORT)])
    app.router.add_get("/api/auth/session", auth_h.api_auth_session)
    token = browser_token("owner", 600)
    async with TestClient(TestServer(app)) as client:
        signed = await client.get("/api/auth/session", headers=browser_headers(token, port=PORT))
        assert signed.status == 200
        assert (await signed.json())["signed_in"] is True
        alone = await client.get(
            "/api/auth/session", headers={"Cookie": f"pc_token_{PORT}={token}"}
        )
        assert alone.status == 403, "the cookie alone is refused before the read"
