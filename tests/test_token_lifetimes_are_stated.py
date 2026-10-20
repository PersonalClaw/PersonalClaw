"""Every token the owner is handed says what it is and how long it lasts — and the docs agree.

``personalclaw token`` printed a bare URL; the ``--json-ready`` help and
``docs/reference/cli.md`` said its token "grants access for up to 20 hours" while the code minted
it for 30 days; the ``generate_token`` docstring said the session was "capped at 20 hours" while
the cap was a year; and ``/api/token/local`` handed a year to any caller that did not name a
lifetime. The decision recorded with this change: a browser sign-in lasts ``auth.session_ttl``
(30 days by default) and the startup link and harness token ARE browser sign-ins, so they follow
that setting; a token a caller mints without naming a lifetime lasts 20 hours, the documented
``personalclaw token`` default; and nothing lasts longer than 90 days — asking for
longer is refused with a sentence saying so. That the CLI help and the CLI reference state those
lifetimes is held by the rail ``tests/rails/test_token_lifetimes_are_stated_tree.py``, once per CI
run in the rails job.
"""

from __future__ import annotations

import argparse
import json
import time
from types import SimpleNamespace

import pytest
from fakes import gateway_stand_in

from personalclaw import cli_server
from personalclaw.dashboard import token_auth

_SYNTHETIC_TOKEN = "synthetic.placeholder.not-a-real-token"


def _run_token(monkeypatch, tmp_path, capsys, reply: dict) -> tuple[str, str]:
    """`personalclaw token` against this home's gateway, on a port of its own, answering *reply*:
    what it printed, with that port written as ``PORT``."""
    (tmp_path / ".local_secret").write_text("synthetic-local-secret\n")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    with gateway_stand_in(tmp_path, routes={("GET", "/api/token/local"): (200, reply)}) as gateway:
        gateway.record()
        cli_server._token(argparse.Namespace(ttl="20h", port=None))
    captured = capsys.readouterr()
    return captured.out.replace(f"localhost:{gateway.port}?", "localhost:PORT?"), captured.err


def test_personalclaw_token_says_what_it_is_and_how_long_it_lasts(monkeypatch, tmp_path, capsys):
    now = time.time()
    out, err = _run_token(
        monkeypatch,
        tmp_path,
        capsys,
        {
            "token": _SYNTHETIC_TOKEN,
            "expires_in": 20 * 3600,
            "expires_at": now + 20 * 3600,
            "open_within": 20 * 3600,
        },
    )
    # stdout stays exactly the URL, so `open "$(personalclaw token)"` and `| head -1` keep working.
    assert out == f"http://localhost:PORT?token={_SYNTHETIC_TOKEN}\n"
    assert "sign-in link" in err
    assert "Authorization: Bearer" in err, "a script needs to know it can send it as a header"
    assert "20 hours" in err
    assert "Settings → Devices" in err, "where to see it and sign it out"


def test_a_long_token_says_its_link_must_still_be_opened_within_a_day(
    monkeypatch, tmp_path, capsys
):
    now = time.time()
    _out, err = _run_token(
        monkeypatch,
        tmp_path,
        capsys,
        {
            "token": _SYNTHETIC_TOKEN,
            "expires_in": 30 * 86400,
            "expires_at": now + 30 * 86400,
            "open_within": 86400,
        },
    )
    assert "within 24 hours" in err, err
    assert "30 days" in err, err


def test_the_harness_ready_line_says_what_its_token_is_and_how_long_it_lasts(tmp_path):
    from personalclaw import gateway

    minted = token_auth.mint_session("local-startup", 30 * 86400, issuer=token_auth.ISSUER_READY)
    line = gateway.ready_line(port=4318, home=tmp_path, minted=minted)
    assert line.startswith("PERSONALCLAW_READY:")
    payload = json.loads(line.split(":", 1)[1])
    assert payload["token"] == minted.token and payload["port"] == 4318
    assert payload["token_expires_in"] == 30 * 86400
    assert payload["token_expires_at"] == pytest.approx(minted.expires_at)


def test_the_startup_link_and_harness_token_last_as_long_as_a_browser_sign_in(tmp_path):
    """They are how a browser signs in, so they follow `auth.session_ttl` like every other door."""
    from personalclaw import gateway

    for configured, expected in (("7d", 7 * 86400), ("12h", 12 * 3600), ("", 30 * 86400)):
        auth_cfg = SimpleNamespace(session_ttl=configured)
        for issuer in (token_auth.ISSUER_STARTUP, token_auth.ISSUER_READY):
            minted = gateway.mint_startup_token(issuer, auth_cfg)
            payload = json.loads(token_auth._b64url_decode(minted.token.split(".")[0]))
            assert payload["session_exp"] - payload["iat"] == pytest.approx(expected, abs=5), (
                configured,
                issuer,
            )
