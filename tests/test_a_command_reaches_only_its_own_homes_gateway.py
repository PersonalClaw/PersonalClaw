"""A command run for a home reaches that home's gateway, and sends the home's secret nowhere else.

``personalclaw token`` for a scratch home whose gateway listens on another port asked port 10000:
the command took its port from ``--port``, ``PERSONALCLAW_PORT``, the port in ``dashboard.url``,
else 10000, and a gateway started with ``--port`` writes none of those. Whatever listened on 10000
was handed the home's local secret, the credential that signs the owner in: on a machine with two
homes, the other home's gateway. ``logout``, ``auth revoke`` and ``auth rotate-key`` did the same,
``chat``, ``run`` and ``spawn`` minted their token there, ``cron trigger`` sent the internal
credential wherever ``dashboard.url`` pointed, and ``doctor`` and the dev tools trusted the port the
home's record named without asking who answered on it.

Each now asks ``home_gateway.reach``: the port the home's gateway recorded, or the one ``--port`` or
``PERSONALCLAW_PORT`` names; then, before any credential is sent, ``/api/healthz``, whose
``home_id`` says which home the gateway answering there serves.

The scenarios use gateways on loopback ports of this test's own: this home's, serving the real
token and logout routes, and another home's, standing where the command used to look: "the default
port" is a stand-in port the old default is pointed at, never 10000 itself. Each also measures what
a proxy from the environment is sent, since urllib hands it even a loopback request, headers and
all. What the gateway starts (its core tools, a scheduled script) calls it back with the same
secret, through the same transport, and is held to the same.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import secrets
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fakes import GatewayStandIn, gateway_stand_in, home_fingerprint, released_port

import personalclaw
from personalclaw import cli, cli_server
from personalclaw.config import loader as config_loader
from personalclaw.dashboard import origin, token_auth
from personalclaw.dashboard.handlers.core import api_logout, api_token_local

SRC = Path(personalclaw.__file__).resolve().parent
HARNESS = SRC.parents[1] / "harness"

THIS_HOMES_SECRET = "this-homes-local-secret-for-the-test"
OTHER_HOMES_SECRET = "another-homes-local-secret-for-the-test"

#: The variables that would point a request somewhere else: a port named, a proxy, a proxy bypass.
_AIMING = (
    "PERSONALCLAW_PORT",
    "PERSONALCLAW_SESSION_KEY",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unset_env) -> Path:
    """This home: named by ``PERSONALCLAW_HOME``, with the shipped empty ``dashboard.url``, the
    local secret its gateway writes as it starts, and nothing in the environment that points a
    request anywhere. The old default port is pointed at a port nothing listens on, so no
    command, fixed or not, can reach for the real one."""
    unset_env(*_AIMING)
    _point_the_default_at(monkeypatch, released_port())
    home = tmp_path / "this-home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    (home / "config.json").write_text(json.dumps({"dashboard": {"url": ""}}), encoding="utf-8")
    (home / ".local_secret").write_text(THIS_HOMES_SECRET, encoding="utf-8")
    token_auth.use_ephemeral_secret(secrets.token_bytes(32))
    token_auth.revoke_all_sessions()
    yield home
    token_auth.revoke_all_sessions()
    token_auth.use_persistent_secret()


@pytest.fixture
def other_home(tmp_path: Path) -> Path:
    other = tmp_path / "another-home"
    other.mkdir()
    return other


def _point_the_default_at(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    """Point the port the commands used to fall back to at *port*, a stand-in for 10000: the
    module constants every client read it from. A command that reaches only its home's gateway
    reads none of them, so for it this changes nothing."""
    monkeypatch.setattr(cli_server, "_DEFAULT_PORT", port, raising=False)
    monkeypatch.setattr(config_loader, "_DEFAULT_PORT", port)
    monkeypatch.setattr(origin, "_DEFAULT_PORT", port)
    monkeypatch.setattr(cli, "DASHBOARD_PORT", port, raising=False)


@pytest.fixture
def this_homes_gateway(home: Path) -> Iterator[GatewayStandIn]:
    """This home's gateway, on a port of its own, with the gateway's own token and logout routes,
    and the runtime record it writes once it listens."""
    with gateway_stand_in(
        home,
        local_secret=THIS_HOMES_SECRET,
        routes={("GET", "/api/token/local"): api_token_local, ("POST", "/api/logout"): api_logout},
    ) as gateway:
        gateway.record()
        yield gateway


@pytest.fixture
def other_homes_gateway(other_home: Path) -> Iterator[GatewayStandIn]:
    """Another home's gateway: it says it serves that home, and its own token and logout routes
    check that home's secret."""
    with gateway_stand_in(
        other_home,
        local_secret=OTHER_HOMES_SECRET,
        routes={("GET", "/api/token/local"): api_token_local, ("POST", "/api/logout"): api_logout},
    ) as gateway:
        yield gateway


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch, other_home: Path) -> Iterator[GatewayStandIn]:
    """A proxy named in the environment, as a corporate or VPN setup names one. It records every
    request it is sent, and answers it with a refusal."""
    import urllib.request

    with gateway_stand_in(other_home, routes={}) as listener:
        for name in ("HTTP_PROXY", "http_proxy"):
            monkeypatch.setenv(name, listener.url)
        # urllib builds its default opener once, from the environment of the moment, as a command
        # does when it first sends a request: a test that follows another must not inherit one.
        monkeypatch.setattr(urllib.request, "_opener", None)
        yield listener


def _token(port: int | None = None) -> int:
    """``personalclaw token`` as typed (``--port`` when *port* is given): its exit status."""
    try:
        cli_server._token(argparse.Namespace(ttl="20h", port=port))
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _signed_in_by(link: str) -> bool:
    """Whether the link ``token`` printed carries a sign-in this process's token store minted."""
    token = link.rsplit("?token=", 1)[-1].strip()
    return bool(token) and token_auth.validate_token(token, use_session_exp=True)[0]


# ── personalclaw token ──────────────────────────────────────────────────────────────────────────


def test_token_for_a_home_on_another_port_gets_that_homes_gateway(
    home, this_homes_gateway, monkeypatch, capsys
):
    """🔴 On integration: nothing listens where the default points, so ``token`` said it could not
    reach a gateway, while the home's own gateway was running on the port it recorded."""
    code = _token()

    out, err = capsys.readouterr()
    assert code == 0, err
    assert out.startswith(f"http://localhost:{this_homes_gateway.port}?token="), out
    assert _signed_in_by(out.splitlines()[0]), "the link must sign in to this home's gateway"
    (mint,) = this_homes_gateway.asked("/api/token/local")
    assert mint.headers["x-local-secret"] == THIS_HOMES_SECRET


def test_with_another_homes_gateway_on_the_default_port_the_secret_is_not_sent_there(
    home, this_homes_gateway, other_homes_gateway, monkeypatch, capsys
):
    """🔴 On integration: the other home's gateway, standing on the default port, was sent this
    home's secret (and refused it); the link never came."""
    _point_the_default_at(monkeypatch, other_homes_gateway.port)

    code = _token()

    out, err = capsys.readouterr()
    assert code == 0, err
    assert out.startswith(f"http://localhost:{this_homes_gateway.port}?token="), out
    assert other_homes_gateway.requests == [], "nothing at all goes to another home's gateway"


def test_with_no_gateway_of_this_home_it_refuses_and_sends_nothing_anywhere(
    home, other_homes_gateway, monkeypatch, capsys
):
    """🔴 On integration: with this home's gateway stopped, its secret went to the other home's
    gateway on the default port."""
    _point_the_default_at(monkeypatch, other_homes_gateway.port)

    code = _token()

    out, err = capsys.readouterr()
    assert (code, out) == (1, "")
    assert f"No gateway is running for this home ({home.resolve()})" in err, err
    assert "Start it with: personalclaw gateway" in err, err
    assert other_homes_gateway.requests == []


def test_the_default_home_with_its_gateway_on_the_default_port_still_works(
    home, this_homes_gateway, monkeypatch, capsys
):
    """The control: a gateway on the default port that records that port, as the install's own
    does, is reached, and signs this command in."""
    _point_the_default_at(monkeypatch, this_homes_gateway.port)

    code = _token()

    out, err = capsys.readouterr()
    assert code == 0, err
    assert out.startswith(f"http://localhost:{this_homes_gateway.port}?token="), out
    assert _signed_in_by(out.splitlines()[0])


@pytest.mark.parametrize("named_by", ["--port", "PERSONALCLAW_PORT"])
def test_a_port_named_explicitly_reaches_the_gateway_there(
    home, this_homes_gateway, monkeypatch, capsys, named_by
):
    """The override still works: a home whose record is gone (or never written) is reached at the
    port named, once the gateway there shows it is this home's."""
    (home / "gateway.runtime.json").unlink()
    port: int | None = this_homes_gateway.port
    if named_by == "PERSONALCLAW_PORT":
        monkeypatch.setenv("PERSONALCLAW_PORT", str(port))
        port = None

    code = _token(port)

    out, err = capsys.readouterr()
    assert code == 0, err
    assert _signed_in_by(out.splitlines()[0])


@pytest.mark.parametrize("named_by", ["--port", "PERSONALCLAW_PORT"])
def test_a_port_named_explicitly_that_is_another_homes_is_refused_and_sent_no_secret(
    home, this_homes_gateway, other_homes_gateway, monkeypatch, capsys, named_by
):
    """🔴 On integration: the port typed was taken on trust, and the other home's gateway was
    sent this home's secret. It is asked whose it is first, and only that."""
    port: int | None = other_homes_gateway.port
    if named_by == "PERSONALCLAW_PORT":
        monkeypatch.setenv("PERSONALCLAW_PORT", str(port))
        port = None

    code = _token(port)

    out, err = capsys.readouterr()
    assert (code, out) == (1, "")
    assert f"Port {other_homes_gateway.port}, which {named_by} names, is answered by another" in err
    assert f"This home's gateway listens on port {this_homes_gateway.port}." in err, err
    assert [s.path for s in other_homes_gateway.requests] == ["/api/healthz"]
    assert other_homes_gateway.credentials_sent() == []
    assert this_homes_gateway.asked("/api/token/local") == []


def test_the_port_typed_wins_over_the_one_the_environment_names(
    home, this_homes_gateway, other_homes_gateway, monkeypatch, capsys
):
    """``--port`` is the more explicit of the two names, as it always was."""
    monkeypatch.setenv("PERSONALCLAW_PORT", str(other_homes_gateway.port))

    code = _token(this_homes_gateway.port)

    out, err = capsys.readouterr()
    assert code == 0, err
    assert out.startswith(f"http://localhost:{this_homes_gateway.port}?token="), out
    assert other_homes_gateway.requests == []


@pytest.mark.parametrize(
    ("port", "environment", "said"),
    [
        (None, "not-a-port", "PERSONALCLAW_PORT='not-a-port' is not a port"),
        (0, "", "0 is not a port (1 to 65535)"),
        (70000, "", "70000 is not a port (1 to 65535)"),
    ],
)
def test_a_port_that_is_not_one_is_refused_before_anything_is_asked(
    home, this_homes_gateway, monkeypatch, capsys, port, environment, said
):
    """A name that is no port refuses: it is not read as "no port named", which would send the
    request somewhere the user did not say."""
    if environment:
        monkeypatch.setenv("PERSONALCLAW_PORT", environment)

    assert _token(port) == 1

    assert said in capsys.readouterr().err
    assert this_homes_gateway.requests == []


def test_a_program_that_is_not_a_gateway_is_said_to_be_one(home, monkeypatch, capsys):
    """A port held by something that does not answer as a gateway (here, a 404 for its health) is
    named for what it is, and is sent nothing more."""
    with gateway_stand_in(home, routes={("GET", "/api/healthz"): (404, {})}) as stranger:
        monkeypatch.setenv("PERSONALCLAW_PORT", str(stranger.port))
        code = _token()
    _out, err = capsys.readouterr()
    assert code == 1
    assert "a program that is not a PersonalClaw gateway" in err, err
    assert stranger.credentials_sent() == []


def test_a_proxy_in_the_environment_is_never_sent_the_secret(
    home, this_homes_gateway, proxy, monkeypatch, capsys
):
    """🔴 On integration: with ``HTTP_PROXY`` set, urllib sent the loopback token request to the
    proxy, ``X-Local-Secret`` and all."""
    monkeypatch.setenv("PERSONALCLAW_PORT", str(this_homes_gateway.port))

    code = _token()

    out, err = capsys.readouterr()
    assert code == 0, err
    assert _signed_in_by(out.splitlines()[0])
    assert proxy.requests == [], "a proxy from the environment is never a gateway's transport"


def test_a_gateway_of_this_home_the_record_names_is_still_asked_whose_it_is(
    home, other_homes_gateway, monkeypatch, capsys
):
    """A gateway that crashed leaves its record, and the system may give its pid to another
    program and its port to another home's gateway: the record is a place to ask, not proof."""
    other_homes_gateway.record(home)

    code = _token()

    _out, err = capsys.readouterr()
    assert code == 1
    assert "another PersonalClaw home's gateway" in err, err
    assert "This home's gateway no longer runs there. Start it with:" in err, err
    assert other_homes_gateway.credentials_sent() == []


# ── every command that talks to a gateway ───────────────────────────────────────────────────────

#: What this home's gateway answers each command, beyond the token and logout routes.
_ROUTES: dict[tuple[str, str], Any] = {
    ("GET", "/api/status"): (200, {"uptime": "1m", "sessions": 0}),
    ("POST", "/api/auth/rotate-key"): (200, {"ok": True, "signed_out": 0}),
    ("GET", "/api/spawn"): (200, {"agents": []}),
    ("POST", "/api/triggers/schedule:clock:nightly/run"): (200, {"ok": True, "name": "Nightly"}),
    ("GET", "/api/doctor/remediation"): (200, {"score": 90.0, "deficits": []}),
    ("GET", "/api/model-providers"): (200, {"providers": []}),
}


def _exits(run: Callable[[], Any]) -> int:
    try:
        result = run()
    except SystemExit as exc:
        # A SystemExit carrying a sentence exits 1, as Python does with it.
        return exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
    return result if isinstance(result, int) else 0


def _status() -> int:
    return _exits(lambda: cli_server._status(argparse.Namespace(port=None)))


def _logout() -> int:
    return _exits(lambda: cli_server._logout(None))


def _revoke() -> int:
    from personalclaw.auth import cli as auth_cli

    return _exits(lambda: auth_cli.auth_cmd(argparse.Namespace(auth_command="revoke", all=True)))


def _rotate() -> int:
    from personalclaw.auth import cli as auth_cli

    return _exits(lambda: auth_cli.auth_cmd(argparse.Namespace(auth_command="rotate-key")))


def _chat() -> int:
    from personalclaw import cli_chat

    return cli_chat._chat_main(argparse.Namespace(message="hi", port=None, model=None))


def _run() -> int:
    from personalclaw import cli_run

    args = argparse.Namespace(
        prompt="hi",
        format="plain",
        agent="",
        model="",
        session="",
        cwd="",
        allow=False,
        timeout=5.0,
        port=None,
    )
    return cli_run._run_one(args)


def _spawn() -> int:
    from personalclaw import cli_commands

    return _exits(lambda: cli_commands._spawn(cli.build_parser().parse_args(["spawn", "list"])))


def _cron_trigger() -> int:
    from personalclaw import schedule_trigger

    ok, _message = schedule_trigger.trigger_schedule_job("clock:nightly")
    return 0 if ok else 1


def _doctor() -> int:
    from personalclaw.cli_doctor import _read_running_gateway

    return 0 if _read_running_gateway().remediation is not None else 1


def _dev_tool() -> int:
    from harness import named_home

    return _exits(lambda: named_home.scratch_gateway() and 0)


#: Every command that talks to a running gateway, and what it sends the gateway when it reaches it.
COMMANDS: dict[str, tuple[Callable[[], int], tuple[str, str]]] = {
    "token": (_token, ("GET", "/api/token/local")),
    "logout": (_logout, ("POST", "/api/logout")),
    "status": (_status, ("GET", "/api/status")),
    "auth revoke --all": (_revoke, ("POST", "/api/logout")),
    "auth rotate-key": (_rotate, ("POST", "/api/auth/rotate-key")),
    "chat": (_chat, ("GET", "/api/token/local")),
    "run": (_run, ("GET", "/api/token/local")),
    "spawn list": (_spawn, ("GET", "/api/spawn")),
    "cron trigger": (_cron_trigger, ("POST", "/api/triggers/schedule:clock:nightly/run")),
    "doctor": (_doctor, ("GET", "/api/model-providers")),
    "a dev tool": (_dev_tool, ("GET", "/api/token/local")),
}


@pytest.fixture
def no_transient_gateway(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """``run`` starts a gateway of its own when none of its home runs: recorded, not started."""
    from personalclaw import cli_run

    started: list[int] = []

    def start() -> Any:
        started.append(1)
        raise cli_run.RunError("no gateway is started in this test")

    monkeypatch.setattr(cli_run, "start_transient_gateway", start)
    return started


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_no_command_sends_a_credential_to_another_homes_gateway_where_it_used_to_look(
    home, other_homes_gateway, monkeypatch, no_transient_gateway, command
):
    """🔴 On integration ``token``, ``logout``, both ``auth`` commands, ``chat``, ``run``,
    ``spawn`` and ``cron trigger`` each sent this home's secret (or a token minted with it) to the
    other home's gateway, standing on the default port and named by a copied ``dashboard.url``.
    With no gateway of this home running, nothing is sent anywhere: a command refuses, or does
    what it can without one (``auth`` changes the home itself, ``run`` starts its own gateway,
    ``doctor`` measures here, ``status`` says so)."""
    _point_the_default_at(monkeypatch, other_homes_gateway.port)
    (home / "config.json").write_text(
        json.dumps({"dashboard": {"url": f"http://127.0.0.1:{other_homes_gateway.port}"}}),
        encoding="utf-8",
    )
    run, _reaches = COMMANDS[command]

    run()

    assert other_homes_gateway.requests == [], [
        (s.method, s.path, s.credentials()) for s in other_homes_gateway.requests
    ]


def test_status_says_no_gateway_of_this_home_runs_rather_than_reporting_another_homes(
    home, other_homes_gateway, monkeypatch, capsys
):
    """🔴 On integration: ``status`` read the other home's gateway on the default port as this
    home's, running."""
    _point_the_default_at(monkeypatch, other_homes_gateway.port)

    assert _status() == 0
    out = capsys.readouterr().out
    assert f"No gateway is running for this home ({home.resolve()})" in out, out
    assert "PersonalClaw gateway is running" not in out, out


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_no_command_trusts_a_record_whose_port_another_homes_gateway_now_holds(
    home, other_homes_gateway, monkeypatch, no_transient_gateway, command
):
    """🔴 On integration ``doctor``, ``cron trigger`` and the dev tools read the home's record and
    sent the credential to the port it named, whoever answered there."""
    other_homes_gateway.record(home)
    run, _reaches = COMMANDS[command]

    run()

    assert other_homes_gateway.credentials_sent() == []
    assert {(s.method, s.path) for s in other_homes_gateway.requests} <= {("GET", "/api/healthz")}


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_command_reaches_its_homes_gateway_on_its_own_port_and_never_a_proxy(
    home, monkeypatch, proxy, no_transient_gateway, command
):
    """🔴 On integration ``token``, ``logout``, ``status``, both ``auth`` commands, ``chat``,
    ``run``, ``spawn`` and ``cron trigger`` asked the default port and never reached the home's
    gateway on the port it recorded; and what they sent went through the proxy."""
    run, (method, path) = COMMANDS[command]
    with gateway_stand_in(
        home,
        local_secret=THIS_HOMES_SECRET,
        routes={
            ("GET", "/api/token/local"): api_token_local,
            ("POST", "/api/logout"): api_logout,
            **_ROUTES,
        },
    ) as gateway:
        gateway.record()
        run()

    assert [s for s in gateway.requests if (s.method, s.path.split("?", 1)[0]) == (method, path)]
    assert proxy.requests == [], "a proxy from the environment is never a gateway's transport"
    for seen in gateway.requests:
        if "x-local-secret" in seen.headers or "x-internal-secret" in seen.headers:
            assert THIS_HOMES_SECRET in seen.headers.values()


# ── a gateway's own children ────────────────────────────────────────────────────────────────────
#
# What a gateway starts calls it back with the same secret, in ``X-Internal-Secret``: the core
# tools an agent CLI runs (``mcp_core``, and ``mcp_shared``'s read of the session's tool policy)
# and a scheduled script's launcher. They call the port the gateway exported to them, and each call
# went through urllib's default opener, which hands even a loopback request to a proxy the
# environment names. A child has one on purpose: the sandbox passes the proxy variables on, for a
# script's own downloads.


def _a_child_of(gateway: GatewayStandIn, monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment a gateway hands what it starts: the port it listens on, and the session
    the child works for."""
    monkeypatch.setenv("PERSONALCLAW_PORT", str(gateway.port))
    monkeypatch.setenv("PERSONALCLAW_SESSION_KEY", "cron:a-child")


def _redirect_to(location: str) -> Callable[[Any], Any]:
    """A route that answers with a redirect to *location*."""

    async def redirect(_request: Any) -> Any:
        from aiohttp import web

        return web.Response(status=302, headers={"Location": location})

    return redirect


def _core_post(_monkeypatch: pytest.MonkeyPatch) -> Any:
    from personalclaw import mcp_core

    return mcp_core._post("/api/send-message", {"text": "hello"})


def _core_get(_monkeypatch: pytest.MonkeyPatch) -> Any:
    from personalclaw import mcp_core

    return mcp_core._get("/api/sessions")


def _core_delete(_monkeypatch: pytest.MonkeyPatch) -> Any:
    from personalclaw import mcp_core

    return mcp_core._delete("/api/learn/a-lesson")


def _tool_policy(monkeypatch: pytest.MonkeyPatch) -> Any:
    from personalclaw import mcp_shared

    # The server keeps the policy it read for its lifetime, and a failure for a while: this is
    # its first read.
    for name, value in (
        ("_excluded_tools", None),
        ("_last_failure_time", 0.0),
        ("_last_startup_race_time", 0.0),
        ("_failure_count", 0),
    ):
        monkeypatch.setattr(mcp_shared, name, value)
    return mcp_shared._resolve_excluded_tools()


#: Every way a core tool calls its gateway back, the request it sends, and the answer it is given.
CHILD_CALLS: dict[str, tuple[Callable[[pytest.MonkeyPatch], Any], tuple[str, str], Any]] = {
    "a core tool's POST": (_core_post, ("POST", "/api/send-message"), {"ok": True}),
    "a core tool's GET": (_core_get, ("GET", "/api/sessions"), {"sessions": []}),
    "a core tool's DELETE": (_core_delete, ("DELETE", "/api/learn/a-lesson"), {"ok": True}),
    "the tool policy read": (
        _tool_policy,
        ("GET", "/api/session-tool-policy"),
        {"exclude": ["a-tool-this-session-may-not-use"]},
    ),
}


@pytest.mark.parametrize("call", sorted(CHILD_CALLS))
def test_a_core_tool_calls_its_gateway_back_and_never_through_a_proxy(
    home, proxy, monkeypatch, call
):
    """🔴 On integration each of these sent its call to the proxy the environment names, the
    internal credential in its headers, and its gateway was never asked. The proxy's answer was
    taken as the gateway's, the session's tool policy included."""
    run, (method, path), answer = CHILD_CALLS[call]
    with gateway_stand_in(home, routes={(method, path): (200, answer)}) as gateway:
        _a_child_of(gateway, monkeypatch)
        got = run(monkeypatch)

    assert proxy.requests == [], [(s.method, s.path, s.credentials()) for s in proxy.requests]
    assert [
        s.headers.get("x-internal-secret") for s in gateway.asked(path) if s.method == method
    ] == [THIS_HOMES_SECRET], [(s.method, s.path) for s in gateway.requests]
    expected = set(answer["exclude"]) if path == "/api/session-tool-policy" else answer
    assert got == expected


def _a_scheduled_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: GatewayStandIn
) -> dict[str, Any]:
    """Run, as a scheduled run starts one, through the sandbox its launcher runs in, a script that
    calls its gateway back once (``ctx.notify``): what the run reports."""
    import personalclaw.schedule_script as schedule_script

    scripts = tmp_path / "crons"
    scripts.mkdir()
    script = scripts / "notify.py"
    script.write_text(
        "def run(ctx):\n"
        "    r = ctx.notify('hello')\n"
        "    return 'ok=%s status=%s' % (r.get('ok'), r.get('status', 200))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(schedule_script, "_crons_dir", lambda: scripts)
    monkeypatch.setattr(schedule_script, "validate_file_path", lambda p: p)
    _a_child_of(gateway, monkeypatch)
    return schedule_script.run_script_sandboxed(f"{script}:run", "a-job", "", timeout=90)


def test_a_scheduled_scripts_call_back_reaches_its_gateway_and_never_a_proxy(
    home, proxy, monkeypatch, tmp_path
):
    """🔴 On integration a scheduled script's ``ctx.notify`` went to the proxy the sandbox passed
    on to its launcher, the internal credential in its headers, and its gateway was never asked."""
    routes = {("POST", "/api/send-message"): (200, {"ok": True})}
    with gateway_stand_in(home, routes=routes) as gateway:
        result = _a_scheduled_script(tmp_path, monkeypatch, gateway)

    assert proxy.requests == [], [(s.method, s.path, s.credentials()) for s in proxy.requests]
    assert result == {"status": "ok", "message": "ok=True status=200"}, result
    sent = gateway.asked("/api/send-message")
    assert [s.headers.get("x-internal-secret") for s in sent] == [THIS_HOMES_SECRET]


def test_a_scheduled_scripts_call_back_does_not_follow_a_redirect(
    home, other_home, monkeypatch, tmp_path
):
    """🔴 On integration the launcher followed a redirect its gateway answered with, and carried the
    internal credential on to wherever it pointed. The script is told the gateway's answer."""
    with (
        gateway_stand_in(other_home, routes={}) as elsewhere,
        gateway_stand_in(
            home,
            routes={
                ("POST", "/api/send-message"): _redirect_to(f"{elsewhere.url}/api/send-message")
            },
        ) as gateway,
    ):
        result = _a_scheduled_script(tmp_path, monkeypatch, gateway)

    assert elsewhere.requests == [], [
        (s.method, s.path, s.credentials()) for s in elsewhere.requests
    ]
    assert result == {"status": "ok", "message": "ok=False status=302"}, result
    assert len(gateway.asked("/api/send-message")) == 1


def test_a_core_tools_call_does_not_follow_a_redirect(home, other_home, monkeypatch):
    """🔴 On integration a core tool's read followed a redirect, and carried the internal
    credential on to wherever it pointed. A redirect is the gateway's answer, not a place to go."""
    from personalclaw import mcp_core

    with (
        gateway_stand_in(other_home, routes={}) as elsewhere,
        gateway_stand_in(
            home, routes={("GET", "/api/sessions"): _redirect_to(f"{elsewhere.url}/api/sessions")}
        ) as gateway,
    ):
        _a_child_of(gateway, monkeypatch)
        got = mcp_core._get("/api/sessions")

    assert len(gateway.asked("/api/sessions")) == 1
    assert elsewhere.requests == [], [
        (s.method, s.path, s.credentials()) for s in elsewhere.requests
    ]
    assert str(got.get("error", "")).startswith("HTTP 302"), got


# ── the rail ────────────────────────────────────────────────────────────────────────────────────

#: Every module that names the local secret's file, and why none of them sends the secret anywhere
#: but to a gateway that has shown it is this home's.
SECRET_FILE_NAMED_BY = {
    "home_gateway.py": "the one client: it reads the secret for a gateway that showed it is "
    "this home's, and sends it there alone",
    "dashboard/server.py": "the gateway writes it, each time it starts",
    "mcp_core.py": "a child of the gateway (an agent CLI's tool server), addressed through "
    "gateway_base by the gateway that declared its home and port to it, and sending through "
    "home_gateway's loopback transport",
    "inbound/auth.py": "compares an inbound token with it, so that none can equal it",
    "security.py": "names it among PersonalClaw's own secrets, which no agent may read",
    "packs/deny.py": "keeps it out of an exported pack",
    "durability/inventory.py": "lists it as a credential, which a backup and an export leave out",
    "durability/state_history.py": "keeps it out of the home's state history",
    "turn_checkpoints.py": "keeps it out of a turn's checkpoint",
    "workflows/project_export.py": "keeps it out of a project export",
}


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            if isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def _trees(root: Path) -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(root.rglob("*.py")):
        yield path.relative_to(root).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _names_the_secret_file(tree: ast.AST) -> bool:
    docs = _docstrings(tree)
    return any(
        isinstance(node, ast.Constant) and node.value == ".local_secret" and id(node) not in docs
        for node in ast.walk(tree)
    )


def _makes_a_home_gateway(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (
            getattr(node.func, "id", "") == "HomeGateway"
            or getattr(node.func, "attr", "") == "HomeGateway"
        )
        for node in ast.walk(tree)
    )


def test_only_the_one_client_reads_the_secret_to_send_it():
    """A module that reads ``.local_secret`` can send it anywhere; the client that sends it is
    ``home_gateway``, and every other module that names the file says why it sends it nowhere."""
    found = {rel for rel, tree in _trees(SRC) if _names_the_secret_file(tree)}
    assert found == set(SECRET_FILE_NAMED_BY), (
        "a module names the local secret's file without being one the rail knows: send the secret "
        "through home_gateway.reach(), or say here why the module never sends it: "
        f"{sorted(found ^ set(SECRET_FILE_NAMED_BY))}"
    )
    tools = [rel for rel, tree in _trees(HARNESS) if _names_the_secret_file(tree)]
    assert tools == [], f"a dev tool reads the secret itself: {tools}"


def test_only_reach_makes_a_home_gateway():
    """A ``HomeGateway`` is the proof a gateway showed it is this home's: made anywhere but
    ``reach``, it would be a port taken on trust with the secret's door open."""
    makers = [rel for rel, tree in _trees(SRC) if _makes_a_home_gateway(tree)]
    assert makers == ["home_gateway.py"], makers
    tree = ast.parse((SRC / "home_gateway.py").read_text(encoding="utf-8"))
    functions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and _makes_a_home_gateway(node)
    ]
    assert functions == ["reach"], functions
    assert [rel for rel, tree in _trees(HARNESS) if _makes_a_home_gateway(tree)] == []


def test_the_rail_sees_a_planted_reader_and_a_planted_maker(tmp_path):
    """Calibration both ways: each check finds what it is for, and not a docstring."""
    planted = ast.parse(
        '"""Mentions .local_secret in prose."""\n'
        "def leak(home):\n"
        "    return (home / '.local_secret').read_text()\n"
        "def trusted(port):\n"
        "    return home_gateway.HomeGateway(home=None, port=port, pid=0)\n"
    )
    assert _names_the_secret_file(planted)
    assert _makes_a_home_gateway(planted)
    prose = ast.parse('"""Only the docstring says .local_secret."""\nX = 1\n')
    assert not _names_the_secret_file(prose)
    assert len(SECRET_FILE_NAMED_BY) >= 8, "the census lost the modules it knows"


#: The headers that carry this home's secret to its gateway.
_SECRET_HEADERS = {"X-Local-Secret", "X-Internal-Secret"}


def _puts_the_secret_in_a_header(tree: ast.AST) -> bool:
    """Whether *tree* builds a request header that carries the home's secret: a header table with
    one of :data:`_SECRET_HEADERS` as a key, or a call that names the header (``secret_header=``).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and any(
            isinstance(key, ast.Constant) and key.value in _SECRET_HEADERS for key in node.keys
        ):
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in _SECRET_HEADERS
        ):
            return True
        if isinstance(node, ast.keyword) and node.arg == "secret_header":
            return True
    return False


def _opens_by_itself(tree: ast.AST) -> list[int]:
    """The lines where *tree* sends a request through urllib's own opener (``urlopen``, which
    hands it to a proxy the environment names and follows a redirect), or builds an opener."""
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None))
        in {"urlopen", "build_opener"}
    )


def test_every_request_that_carries_the_secret_goes_through_the_one_loopback_transport():
    """A module that puts the home's secret in a request header sends the request through
    ``home_gateway.open_loopback``: no proxy from the environment, no redirect followed. The
    senders are found in the tree, so a new one is held to this as it is written."""
    trees = {rel: tree for rel, tree in _trees(SRC)}
    trees.update({f"harness/{rel}": tree for rel, tree in _trees(HARNESS)})
    senders = {rel for rel, tree in trees.items() if _puts_the_secret_in_a_header(tree)}
    known = {
        "home_gateway.py",
        "cli_server.py",
        "auth/cli.py",
        "schedule_trigger.py",
        "mcp_core.py",
        "mcp_shared.py",
    }
    assert known <= senders, f"the census no longer sees {sorted(known - senders)}"
    own = {
        rel: lines
        for rel in sorted(senders - {"home_gateway.py"})
        if (lines := _opens_by_itself(trees[rel]))
    }
    assert own == {}, (
        "these send the home's secret through an opener of their own: send it through "
        f"home_gateway.open_loopback(), or through home_gateway.reach(): {own}"
    )


def test_a_scheduled_scripts_launcher_sends_nothing_through_urllibs_own_opener():
    """The launcher runs where PersonalClaw is not importable, so it builds the transport itself:
    the same one, and its call back goes through nothing else."""
    from personalclaw import schedule_script

    launcher = ast.parse(schedule_script._LAUNCHER_SRC)
    assert _puts_the_secret_in_a_header(launcher), "the census no longer sees its call back"
    opens = [
        node
        for node in ast.walk(launcher)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None))
        in {"urlopen", "build_opener"}
    ]
    assert [getattr(node.func, "attr", None) for node in opens] == ["build_opener"], [
        ast.unparse(node) for node in opens
    ]
    assert ast.unparse(opens[0]) == (
        "urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())"
    )


def test_the_transport_rail_sees_a_planted_sender():
    """Calibration: a request carrying the secret through ``urlopen`` is seen, in each of the
    three ways a header is named, and one carrying none is not a sender."""
    forms = {
        "a header table": "req = Request(url, headers={'X-Internal-Secret': secret})",
        "a header set by name": "headers['X-Local-Secret'] = secret",
        "a header named to a call": "gateway.post(path, body, secret_header=header)",
    }
    for form, line in forms.items():
        planted = ast.parse(f"{line}\nurllib.request.urlopen(req)\n")
        assert _puts_the_secret_in_a_header(planted), form
        assert _opens_by_itself(planted) == [2], form
    assert not _puts_the_secret_in_a_header(
        ast.parse("urlopen(Request(url, headers={'Accept': 'application/json'}))\n")
    )
    assert _opens_by_itself(ast.parse("opener = build_opener(ProxyHandler({}))\n")) == [1]


def test_the_healthz_route_and_the_client_compute_one_fingerprint(home):
    """The gateway's ``/api/healthz`` and the command compare one value: both ask
    ``home_gateway.home_id``, and the recipe its docstring gives reproduces it."""
    from personalclaw import home_gateway

    assert home_gateway.home_id() == home_fingerprint(home) == home_gateway.home_id(home)
    linked = home.parent / "a-link-to-this-home"
    linked.symlink_to(home)
    assert home_gateway.home_id(linked) == home_fingerprint(home), "a link is the folder it is"


def test_the_identity_read_waits_for_a_busy_gateway_and_not_for_a_closed_port(monkeypatch):
    """A refused connection is a definite answer and is not asked again; a gateway that took the
    connection and has not answered yet is asked again, because misreading a busy gateway as gone
    makes ``run`` start a second one on the same home."""
    import socket

    from personalclaw import home_gateway

    monkeypatch.setattr(home_gateway, "ANSWER_TIMEOUT_SECS", 0.2)
    closed = released_port()
    started = time.monotonic()
    assert home_gateway._healthz(closed) == home_gateway._NOTHING
    assert time.monotonic() - started < 1.0

    with socket.socket() as busy:  # takes connections, never answers
        busy.bind(("127.0.0.1", 0))
        busy.listen(8)
        started = time.monotonic()
        said = home_gateway._healthz(busy.getsockname()[1], attempts=3)
        waited = time.monotonic() - started
    assert said == home_gateway._SILENT
    assert waited >= 0.55, f"three reads of 0.2 s were not all made: {waited:.2f} s"


def test_a_busy_gateway_that_answers_a_later_read_is_reached(home, monkeypatch):
    """A gateway too busy to answer the first two reads, and answering the third, is this home's
    gateway, not an absent one: measured, a live gateway took more than 2 s to answer while busy,
    and reading it as gone made ``run`` start a second gateway on the same home."""
    import socket
    import threading

    from personalclaw import home_gateway

    monkeypatch.setattr(home_gateway, "ANSWER_TIMEOUT_SECS", 0.3)
    body = json.dumps({"status": "ok", "pid": os.getpid(), "home_id": home_fingerprint(home)})
    answer = (
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n{body}"
    ).encode()
    held: list[socket.socket] = []
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    port = server.getsockname()[1]

    def serve() -> None:
        for attempt in range(3):
            conn, _ = server.accept()
            if attempt < 2:
                held.append(conn)  # took the connection, and says nothing yet
                continue
            conn.recv(4096)
            conn.sendall(answer)
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        reached = home_gateway.reach(port)
    finally:
        thread.join(5)
        server.close()
        for conn in held:
            conn.close()
    assert (reached.port, len(held)) == (port, 2)


@pytest.mark.asyncio
async def test_the_gateways_own_healthz_route_shows_it_is_this_homes(home):
    """The real ``/api/healthz`` answers with the fingerprint the client computes: driven against
    the gateway's own route, not the stand-in's copy of it."""
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from personalclaw import home_gateway
    from personalclaw.dashboard.handlers_system import api_healthz

    app = web.Application()
    app.router.add_get("/api/healthz", api_healthz)
    async with TestServer(app, host="127.0.0.1") as server:
        port = server.port
        reached = await asyncio.to_thread(home_gateway.reach, port)
    assert (reached.port, reached.home, reached.pid) == (port, home.resolve(), os.getpid())


#: The modules a command that talks to its gateway runs in.
CLIENTS = (
    "cli.py",
    "cli_server.py",
    "cli_run.py",
    "cli_chat.py",
    "cli_commands.py",
    "cli_doctor.py",
    "auth/cli.py",
    "schedule_trigger.py",
    "home_gateway.py",
)

#: What answers where a gateway BINDS, or where a CHILD of one connects: never where a command
#: connects, which is home_gateway.reach()'s answer.
_NOT_WHERE_A_COMMAND_CONNECTS = {
    "_DEFAULT_PORT",
    "DASHBOARD_PORT",
    "resolve_api_base",
    "resolve_port",
    "live_port",
    "_api_base",
    "_post",
    "_get",
    "_delete",
}


def test_no_command_asks_the_old_resolvers_for_a_port():
    """``cli_server.resolve_client_port`` is gone, and no client module reads the port a gateway
    binds, or the address a child of the gateway is handed, as where a command connects."""
    assert not hasattr(cli_server, "resolve_client_port")
    for rel in CLIENTS:
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = {getattr(node, "id", None), getattr(node, "attr", None)}
            if isinstance(node, ast.ImportFrom):
                names |= {alias.name for alias in node.names}
            hit = names & _NOT_WHERE_A_COMMAND_CONNECTS
            assert not hit, (
                f"{rel}:{getattr(node, 'lineno', '?')} reads {sorted(hit)}: a command connects "
                "where home_gateway.reach() says"
            )


def test_every_command_asks_home_gateway(home, monkeypatch, no_transient_gateway):
    """The one function every command asks: made to refuse, each command asks it once and stops
    there (a command that found its gateway another way would go on)."""
    from personalclaw import home_gateway

    asked: list[int | None] = []

    def refuse(port: int | None = None) -> Any:
        asked.append(port)
        raise home_gateway.NotThisHomesGateway("refused by the test")

    monkeypatch.setattr(home_gateway, "reach", refuse)
    (home / "gateway.runtime.json").write_text(
        json.dumps({"port": released_port(), "pid": os.getpid()}), encoding="utf-8"
    )
    for name in sorted(COMMANDS):
        before = len(asked)
        assert COMMANDS[name][0]() != 0, f"{name} went on past a refusal"
        assert len(asked) == before + 1, f"{name} did not ask home_gateway.reach()"
