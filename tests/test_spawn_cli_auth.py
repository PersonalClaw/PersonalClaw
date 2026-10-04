"""``personalclaw spawn`` must authenticate like every sibling CLI command (#2947).

Before the fix, ``_spawn`` sent every request with no credential in any accepted
location. On the default auth-on gateway that meant:

  - ``spawn list``'s GET to ``/api/spawn`` got back ``403``, and because
    ``urllib.error.HTTPError`` subclasses ``URLError``, it landed on the
    "gateway not running" arm — misreporting a running, healthy gateway as down.
  - ``spawn run``'s POST also got ``403``, surfaced via its ``HTTPError`` arm as the
    unhelpful bare "Error: Forbidden", with no flag to authenticate at all.

``spawn`` reaches its gateway the way every client command does: ``home_gateway.reach`` (this
home's gateway, at the port it recorded, once it has shown it serves this home), then
``mint_local_token`` (exchanges the home's ``.local_secret`` for a token at
``/api/token/local`` — the handshake ``token`` makes), and ``owner_headers`` (the token in
``Authorization: Bearer``, never in the URL).

These tests serve a gateway of this home on a loopback port of its own that actually enforces
token auth on ``/api/spawn`` (403 without the header, 200 with it matching what
``/api/token/local`` minted), and that refuses any ``/api/spawn`` URL carrying the token.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from aiohttp import web
from fakes import gateway_stand_in

from personalclaw import cli_commands

_SECRET = "test-local-secret"
_TOKEN = "minted-test-token"


def _auth_routes(home: Path, *, spawned: dict, polled: dict | None = None) -> dict:
    """The gateway's token route and its owner-only ``/api/spawn`` routes, enforcing the real
    auth contract: the mint takes the home's local secret in ``X-Local-Secret``, and every
    ``/api/spawn`` request takes what it minted as ``Authorization: Bearer``, never in its URL.
    """

    # What the gateway wrote as it started, and holds from then on.
    secret = (home / ".local_secret").read_text(encoding="utf-8").strip()

    async def token_local(request: web.Request) -> web.Response:
        if request.headers.get("X-Local-Secret") != secret:
            return web.json_response({"error": "Forbidden"}, status=403)
        return web.json_response({"token": _TOKEN})

    def _owner(request: web.Request) -> bool:
        assert _TOKEN not in request.path_qs, f"the owner token rode the URL: {request.path_qs}"
        return request.headers.get("Authorization") == f"Bearer {_TOKEN}"

    async def spawn(request: web.Request) -> web.Response:
        if not _owner(request):
            return web.json_response({"error": "Forbidden"}, status=403)
        return web.json_response(spawned)

    async def poll(request: web.Request) -> web.Response:
        if not _owner(request):
            return web.json_response({"error": "Forbidden"}, status=403)
        return web.json_response(polled or {})

    routes = {
        ("GET", "/api/token/local"): token_local,
        ("GET", "/api/spawn"): spawn,
        ("POST", "/api/spawn"): spawn,
    }
    if polled is not None:
        routes[("GET", f"/api/spawn/{spawned['id']}")] = poll
    return routes


def _spawn_args(*, action: str, task: str = "", fire_and_forget: bool = True):
    return argparse.Namespace(
        spawn_action=action,
        port=None,
        task=task,
        fire_and_forget=fire_and_forget,
    )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unset_env) -> Path:
    """This home, named by ``PERSONALCLAW_HOME``, with the ``.local_secret`` its gateway writes,
    so no real home is ever touched and no port in the environment is asked."""
    unset_env("PERSONALCLAW_PORT", "HTTP_PROXY", "http_proxy")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    (home / ".local_secret").write_text(_SECRET, encoding="utf-8")
    return home


def test_spawn_list_authenticates_against_a_real_auth_gateway(home, capsys):
    """The end-to-end regression: a `spawn list` must reach a token-gated gateway.

    Failed before #2947 (no token ever sent -> 403 -> "gateway not running", even though the
    gateway answered fine). The minted token rides the Bearer header and the gateway's
    `/api/spawn` accepts it.
    """
    agents = {"agents": [{"id": "agent-42", "task": "check open PRs", "done": True}]}
    with gateway_stand_in(home, routes=_auth_routes(home, spawned=agents)) as gateway:
        gateway.record()
        cli_commands._spawn(_spawn_args(action="list"))

    out = capsys.readouterr().out
    assert "agent-42" in out, f"the authenticated request never reached the gateway: {out!r}"


def test_spawn_run_sends_the_minted_token_in_the_header(home, capsys):
    """`spawn run --async` must authenticate its POST with the minted token, not send it bare."""
    spawned = {"id": "agent-7", "task": "probe"}
    with gateway_stand_in(home, routes=_auth_routes(home, spawned=spawned)) as gateway:
        gateway.record()
        cli_commands._spawn(_spawn_args(action="run", task="probe"))

    out = capsys.readouterr().out
    assert "Forbidden" not in out
    assert "Spawned subagent agent-7" in out


def test_spawn_run_polls_with_the_header_until_the_subagent_is_done(home, monkeypatch, capsys):
    """The blocking form's poll loop is the third request and must authenticate the same way:
    a poll the gateway refuses reads as "lost connection to gateway"."""
    spawned = {"id": "agent-9", "task": "probe"}
    done = {"done": True, "result": "the subagent's answer"}
    routes = _auth_routes(home, spawned=spawned, polled=done)
    monkeypatch.setattr(cli_commands._time, "sleep", lambda _s: None)
    with gateway_stand_in(home, routes=routes) as gateway:
        gateway.record()
        cli_commands._spawn(_spawn_args(action="run", task="probe", fire_and_forget=False))

    assert gateway.asked("/api/spawn/agent-9"), "the blocking form never polled"
    assert "the subagent's answer" in capsys.readouterr().out


def test_spawn_reports_not_running_only_when_the_gateway_is_actually_absent(home, capsys):
    """VACUITY: no gateway of this home running must still read as none running.

    Without this, a fix that always finds a way to avoid the phrase (e.g. by
    swallowing every error) would pass the tests above for the wrong reason.
    """
    with pytest.raises(SystemExit):
        cli_commands._spawn(_spawn_args(action="list"))

    assert "No gateway is running for this home" in capsys.readouterr().err


def test_spawn_mint_failure_is_reported_as_itself_not_as_not_running(home, capsys):
    """A gateway of this home that will not sign this command in (its secret is not the one in the
    home: a second gateway started on the home since) is a DIFFERENT fact than "not running" and
    must read as one — mirroring how `status` distinguishes a 401/403 from an unreachable port."""
    routes = _auth_routes(home, spawned={"agents": []})
    with gateway_stand_in(home, routes=routes) as gateway:
        gateway.record()
        (home / ".local_secret").write_text("a-secret-the-gateway-no-longer-holds")
        with pytest.raises(SystemExit):
            cli_commands._spawn(_spawn_args(action="list"))

    err = capsys.readouterr().err
    assert "No gateway is running" not in err
    assert "did not sign this command in" in err
