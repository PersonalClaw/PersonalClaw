"""The desktop app's gateway asks every request for a sign-in, as every other install does.

The shell starts its gateway with the environment ``desktop/gatewayEnv.js::buildGatewayEnv``
builds, on a port the system picks (``--port auto``). That environment used to switch
authentication off for every request, so on a desktop install:

* an app's token was never read, and an app's call reached routes its manifest never declared;
* the gateway's internal credential, which an agent CLI's tool server holds, opened every route
  rather than the operations it is for;
* any process on the computer could answer an approval holding no credential at all.

The shell now signs its own windows in with the owner session the gateway's ready line hands it
for this start, as the cookie ``pc_token_<port>`` for the port that line names
(``desktop/localSignIn.js``), and sends the same session on its own requests as a Bearer header.

Driven as the desktop runs it: the environment is the one the shell's own builder produces,
executed, and the gateway is the real one (``start_dashboard``) on a real loopback socket, started
on port 0 exactly as ``--port auto`` starts it.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import aiohttp
import pytest
import pytest_asyncio

from personalclaw.dashboard.approval_state import chat_approval_id

REPO = Path(__file__).resolve().parent.parent
BUILDER = REPO / "desktop" / "gatewayEnv.js"
NODE = shutil.which("node")

#: The app this suite installs, and the one route its manifest declares.
APP = "notes-helper"
DECLARED = "/api/approvals"

#: Switches that decide how a gateway admits a request. Cleared from THIS process, so the only
#: environment the gateway sees is the one the shell's builder hands it.
_INHERITED_SWITCHES = (
    "PERSONALCLAW_AUTH_MODE",
    "PERSONALCLAW_BYPASS_LOCAL_NETWORKS",
    "PERSONALCLAW_BIND_HOST",
)

pytestmark = pytest.mark.skipif(NODE is None, reason="executing the shell's builder needs node")


def _desktop_env(inherited: dict[str, str]) -> dict[str, str]:
    """What the shell hands the gateway it spawns: its own builder, executed."""
    script = (
        "const {buildGatewayEnv} = require(process.argv[1]);"
        "const env = buildGatewayEnv({env: JSON.parse(process.argv[2]),"
        " loginPath: process.argv[3], projectDir: process.argv[4]});"
        "process.stdout.write(JSON.stringify(env));"
    )
    done = subprocess.run(
        [
            NODE or "node",
            "-e",
            script,
            str(BUILDER),
            json.dumps(inherited),
            "/usr/bin:/bin",
            str(REPO),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(done.stdout)


def _install(home: Path) -> None:
    """An installed app whose manifest declares exactly one API route."""
    appdir = home / "apps" / APP
    appdir.mkdir(parents=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Notes helper",
        "description": "Keeps notes.",
        "permissions": {"api": [DECLARED]},
    }
    (appdir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (appdir / "installed.json").write_text(
        json.dumps({"name": APP, "version": "1.0.0", "enabled": True}), encoding="utf-8"
    )


async def _start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited: dict[str, str]):
    """The real gateway, started with the environment the shell's builder makes of *inherited*."""
    home = tmp_path / "data"
    user_home = tmp_path / "user"
    user_home.mkdir()
    for name in _INHERITED_SWITCHES:
        monkeypatch.delenv(name, raising=False)
    env = _desktop_env(
        {
            "HOME": str(user_home),
            "PERSONALCLAW_HOME": str(home),
            "PATH": "/usr/bin:/bin",
            **inherited,
        }
    )
    for key, value in env.items():
        if key.startswith("PERSONALCLAW_"):
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("HOME", str(user_home))
    assert os.environ["PERSONALCLAW_HOME"] == str(home), "the gateway would not run in its own home"
    _install(home)
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    import personalclaw.dashboard.handlers.core as handlers_core_mod
    import personalclaw.dashboard.server as server_mod

    monkeypatch.setattr(server_mod, "_DIST_DIR", dist)
    monkeypatch.setattr(handlers_core_mod, "_DIST_DIR", dist)
    runner, state = await server_mod.start_dashboard(sessions=MagicMock(count=0), port=0)
    return runner, state, home


@pytest_asyncio.fixture
async def desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The desktop app's gateway: the shell's environment, an ephemeral port, its own home."""
    runner, state, home = await _start(tmp_path, monkeypatch, {})
    port = runner.addresses[0][1]
    try:
        yield _Gateway(port=port, state=state, home=home)
    finally:
        await runner.cleanup()


class _Gateway(SimpleNamespace):
    """The running gateway, and the credentials the desktop app's processes hold for it."""

    port: int
    state: object
    home: Path

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def ready_token(self) -> str:
        """The owner session the gateway's ready line hands the shell for this start."""
        from personalclaw.dashboard.session_store import ISSUER_READY
        from personalclaw.gateway import mint_startup_token

        return mint_startup_token(ISSUER_READY, None).token

    def window(self, token: str) -> dict[str, str]:
        """What a request from the shell's window carries: the session cookie the shell sets,
        named for the port the ready line names, and the page's own origin."""
        return {
            "Cookie": f"pc_token_{self.port}={token}",
            "Origin": f"http://localhost:{self.port}",
        }

    def internal_secret(self) -> str:
        """The internal credential, as an agent CLI's tool server reads it from the home."""
        return (self.home / ".local_secret").read_text(encoding="utf-8").strip()

    async def request(
        self, method: str, path: str, headers: dict[str, str] | None = None
    ) -> tuple[int, dict, str]:
        """One request with exactly *headers* — no cookie jar, so nothing rides along unasked."""
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as http:
            async with http.request(method, self.base + path, headers=headers or {}) as resp:
                text = await resp.text()
                return resp.status, dict(resp.headers), text


async def _held_approval(state, name: str = "chat-desktop") -> tuple[str, asyncio.Future]:
    """A chat waiting on your answer to one tool call, listed in ``GET /api/approvals``."""
    session = state.get_or_create_session(name)
    future = asyncio.get_running_loop().create_future()
    session._approval_futures["req-1"] = future
    await state.hold_session_approval(
        session,
        "req-1",
        tool="write_file",
        tool_input='{"path": "notes.txt"}',
        tool_purpose="",
        agent="",
        risk="",
        is_read_only=False,
        blast_radius=None,
        grant_agent="",
    )
    return chat_approval_id(session.key, "req-1"), future


def _code(text: str) -> str:
    try:
        body = json.loads(text)
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    return error.get("code", "") if isinstance(error, dict) else ""


# ── an approval is answered by you, never by a caller holding nothing ───────────────────────────


@pytest.mark.asyncio
async def test_a_caller_holding_no_credential_cannot_answer_an_approval(desktop) -> None:
    approval_id, future = await _held_approval(desktop.state)

    status, headers, text = await desktop.request("POST", f"/api/approvals/{approval_id}/approve")

    assert status == 403, (status, text)
    assert headers.get("X-Auth-Required") == "true", headers
    assert not future.done(), "an approval was answered by a request that carried no sign-in"
    assert approval_id in desktop.state._pending_approvals, "the approval left the list"


@pytest.mark.asyncio
async def test_a_caller_holding_no_credential_cannot_read_what_waits_on_you(desktop) -> None:
    await _held_approval(desktop.state)

    status, _headers, text = await desktop.request("GET", "/api/approvals")

    assert status == 403, (status, text)
    assert "write_file" not in text, "the pending call's brief reached a caller with no sign-in"


@pytest.mark.asyncio
async def test_the_window_signed_in_by_the_shell_answers_it(desktop) -> None:
    """The floor: the route answers, for the window the shell signed in with the ready token."""
    approval_id, future = await _held_approval(desktop.state)

    status, _headers, text = await desktop.request(
        "POST", f"/api/approvals/{approval_id}/approve", desktop.window(desktop.ready_token())
    )

    assert status == 200, (status, text)
    assert future.done() and future.result() == "approved", "the window's Allow missed the call"


@pytest.mark.asyncio
async def test_an_approval_needs_a_sign_in_even_when_the_shell_inherits_a_switch(
    tmp_path, monkeypatch
) -> None:
    """Started from a terminal whose environment turns sign-in off, the desktop app's gateway still
    asks for one: the shell's builder hands the child no switch it inherited."""
    runner, state, home = await _start(
        tmp_path,
        monkeypatch,
        {"PERSONALCLAW_AUTH_MODE": "none", "PERSONALCLAW_BYPASS_LOCAL_NETWORKS": "1"},
    )
    gw = _Gateway(port=runner.addresses[0][1], state=state, home=home)
    try:
        approval_id, future = await _held_approval(state)
        status, _headers, text = await gw.request("POST", f"/api/approvals/{approval_id}/approve")
        assert status == 403, (status, text)
        assert not future.done(), "an inherited switch let a caller with no sign-in answer"
    finally:
        await runner.cleanup()


# ── an app's token binds its calls to what its manifest declares ────────────────────────────────


@pytest.mark.asyncio
async def test_an_apps_call_outside_its_permissions_is_refused(desktop) -> None:
    from personalclaw.dashboard.token_auth import app_session_token, validate_token_with_app

    owner = desktop.ready_token()
    _valid, user, _reason, _app = validate_token_with_app(owner, use_session_exp=True)
    app_token = app_session_token(user, APP)[0]
    # What an app's page in the window sends: the window's session, plus its own app token.
    as_the_app = {**desktop.window(owner), "Authorization": f"Bearer {app_token}"}

    declared, _h, declared_text = await desktop.request("GET", DECLARED, as_the_app)
    undeclared, _h, undeclared_text = await desktop.request("GET", "/api/devices", as_the_app)
    as_you, _h, you_text = await desktop.request("GET", "/api/devices", desktop.window(owner))

    assert declared == 200, (declared, declared_text)
    assert as_you == 200, (as_you, you_text)
    assert undeclared == 403, (
        f"an app's call to a route its manifest never declared answered {undeclared}: "
        f"{undeclared_text[:200]}"
    )


# ── the internal credential opens its own operations and no other ───────────────────────────────


@pytest.mark.asyncio
async def test_the_internal_credential_is_refused_outside_its_operations(desktop) -> None:
    # What an agent CLI's tool server sends: the internal credential, and the chat it serves,
    # which is live in the gateway while its agent's turn runs.
    desktop.state.get_or_create_session("chat-desktop", memory_mode="persistent")
    tool = {
        "X-Internal-Secret": desktop.internal_secret(),
        "X-Session-Key": "dashboard:chat-desktop",
    }

    listed, _h, listed_text = await desktop.request("GET", "/api/lessons", tool)
    unlisted, _h, unlisted_text = await desktop.request("GET", "/api/devices", tool)
    wrong, _h, wrong_text = await desktop.request(
        "GET", "/api/lessons", {**tool, "X-Internal-Secret": "0" * 32}
    )

    assert listed == 200, (listed, listed_text)
    assert unlisted == 403 and _code(unlisted_text) == "internal_route_refused", (
        unlisted,
        unlisted_text[:200],
    )
    assert wrong == 403, (wrong, wrong_text[:200])


# ── the cookie the shell sets is the one the gateway reads ──────────────────────────────────────


@pytest.mark.asyncio
async def test_the_session_cookie_is_named_for_the_port_the_gateway_serves_on(desktop) -> None:
    """A gateway started on a port the system picks names its session cookie for that port, as
    every gateway does: the cookie a browser's sign-in link leaves is the cookie the shell sets."""
    token = desktop.ready_token()
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as http:
        async with http.get(f"{desktop.base}/api/approvals?token={token}") as resp:
            assert resp.status == 200, await resp.text()
            names = {c.key for c in resp.cookies.values() if c.value}

    assert names == {f"pc_token_{desktop.port}"}, names
