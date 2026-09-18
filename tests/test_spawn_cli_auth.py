"""``personalclaw spawn`` must authenticate like every sibling CLI command (#2947).

Before the fix, ``_spawn`` sent every request with no credential in any accepted
location. On the default auth-on gateway that meant:

  - ``spawn list``'s GET to ``/api/spawn`` got back ``403``, and because
    ``urllib.error.HTTPError`` subclasses ``URLError``, it landed on the
    "gateway not running" arm — misreporting a running, healthy gateway as down.
  - ``spawn run``'s POST also got ``403``, surfaced via its ``HTTPError`` arm as the
    unhelpful bare "Error: Forbidden", with no flag to authenticate at all.

The fix routes ``spawn`` through the same trio ``personalclaw run`` already uses:
``probe_gateway`` (hits the auth-bypassed ``/api/healthz`` for liveness),
``mint_local_token`` (exchanges the shared ``.local_secret`` for a token at
``/api/token/local`` — the same handshake ``token``/``status``/``logout`` use), and
``_authed`` (rides the token as ``?token=``, the only location ``token_auth`` honours).

These tests build a minimal fake gateway that actually enforces token auth on
``/api/spawn`` (403 without ``?token=``, 200 with it matching what ``/api/token/local``
minted) — the run-list test fails against the pre-fix ``_spawn`` because it never
attaches a token, and passes after because the fix does.
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from personalclaw import cli_commands

_SECRET = "test-local-secret"
_TOKEN = "minted-test-token"


class _Resp:
    """A minimal stand-in for ``http.client.HTTPResponse`` as a context manager."""

    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()
        self.status = 200

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _forbidden(url: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url, 403, "Forbidden", None, io.BytesIO(b'{"error": "Forbidden"}')
    )


def _fake_gateway(secret_path, agents_payload):
    """A tiny ``urlopen`` replacement that enforces the real auth contract:

    ``/api/healthz`` always answers (the bypass every liveness probe relies on),
    ``/api/token/local`` requires the correct ``X-Local-Secret`` header, and
    ``/api/spawn*`` requires ``?token=`` to equal what that mint returned.
    """

    def _urlopen(req, timeout=5):
        url = req if isinstance(req, str) else req.full_url
        parts = urlsplit(url)
        query = parse_qs(parts.query)

        if parts.path == "/api/healthz":
            return _Resp({})

        if parts.path == "/api/token/local":
            # ``Request.add_header`` stores keys via ``.capitalize()`` ("X-local-secret"),
            # and ``Request.get_header`` does NOT re-capitalize its argument — so a
            # case-insensitive scan is the only way to read it back reliably here,
            # same as a real (case-insensitive) HTTP server would.
            header = (
                ""
                if isinstance(req, str)
                else next((v for k, v in req.headers.items() if k.lower() == "x-local-secret"), "")
            )
            if header != secret_path.read_text(encoding="utf-8").strip():
                raise _forbidden(url)
            return _Resp({"token": _TOKEN})

        if parts.path.startswith("/api/spawn"):
            if query.get("token", [""])[0] != _TOKEN:
                raise _forbidden(url)
            return _Resp(agents_payload)

        raise AssertionError(f"unexpected URL in fake gateway: {url}")

    return _urlopen


def _spawn_args(*, action: str, port: int, task: str = "", fire_and_forget: bool = True):
    return argparse.Namespace(
        spawn_action=action,
        port=port,
        task=task,
        fire_and_forget=fire_and_forget,
    )


@pytest.fixture
def _local_secret(tmp_path, monkeypatch):
    """Point every ``config_dir()`` caller (``cli_run`` and ``cli_commands``) at an
    isolated home carrying a known ``.local_secret`` — the file ``mint_local_token``
    reads to authenticate, so no real ``PERSONALCLAW_HOME`` is ever touched."""

    secret_path = tmp_path / ".local_secret"
    secret_path.write_text(_SECRET, encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return secret_path


def test_spawn_list_authenticates_against_a_real_auth_gateway(_local_secret, monkeypatch, capsys):
    """The end-to-end regression: a `spawn list` must reach a token-gated gateway.

    Fails before the fix (no token ever sent -> 403 -> "gateway not running", even
    though the fake gateway is answering `/api/healthz` fine). Passes after: the
    minted token rides `?token=` and the fake gateway's `/api/spawn` accepts it.
    """
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _fake_gateway(
            _local_secret,
            {
                "agents": [
                    {"id": "agent-42", "task": "check open PRs", "done": True},
                ]
            },
        ),
    )

    cli_commands._spawn(_spawn_args(action="list", port=10884))

    out = capsys.readouterr().out
    assert "gateway not running" not in out, (
        "a healthy, auth-on gateway was misreported as not running — the request "
        "never carried a token"
    )
    assert "agent-42" in out, f"the authenticated request never reached the gateway: {out!r}"


def test_spawn_run_sends_the_minted_token_on_the_query_string(_local_secret, monkeypatch, capsys):
    """`spawn run --async` must attach `?token=` to its POST, not send it bare."""
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _fake_gateway(_local_secret, {"id": "agent-7", "task": "probe"}),
    )

    cli_commands._spawn(_spawn_args(action="run", port=10884, task="probe"))

    out = capsys.readouterr().out
    assert "Forbidden" not in out
    assert "Spawned subagent agent-7" in out


def test_spawn_reports_not_running_only_when_the_gateway_is_actually_absent(monkeypatch, capsys):
    """VACUITY: a real liveness failure must still read as "gateway not running".

    Without this, a fix that always finds a way to avoid the phrase (e.g. by
    swallowing every error) would pass the tests above for the wrong reason.
    """
    monkeypatch.setattr(cli_commands, "probe_gateway", lambda *a, **k: False)

    with pytest.raises(SystemExit):
        cli_commands._spawn(_spawn_args(action="list", port=10884))

    assert "gateway not running" in capsys.readouterr().out


def test_spawn_mint_failure_is_reported_as_itself_not_as_not_running(monkeypatch, capsys):
    """A live gateway this process cannot authenticate against (e.g. a different
    ``PERSONALCLAW_HOME``) is a DIFFERENT fact than "not running" and must read as
    one — mirroring how `status` distinguishes a 401/403 from an unreachable port."""
    monkeypatch.setattr(cli_commands, "probe_gateway", lambda *a, **k: True)

    def _boom(port, timeout=5.0):
        raise cli_commands.RunError("cannot read secret — different PERSONALCLAW_HOME")

    monkeypatch.setattr(cli_commands, "mint_local_token", _boom)

    with pytest.raises(SystemExit):
        cli_commands._spawn(_spawn_args(action="list", port=10884))

    out = capsys.readouterr().out
    assert "gateway not running" not in out
    assert "different PERSONALCLAW_HOME" in out
