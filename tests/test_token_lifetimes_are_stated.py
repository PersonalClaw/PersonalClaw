"""Every token the owner is handed says what it is and how long it lasts — and the docs agree.

Measured on main: ``personalclaw token`` printed a bare URL; the ``--json-ready`` help and
``docs/reference/cli.md`` said its token "grants access for up to 20 hours" while the code minted
it for 30 days; the ``generate_token`` docstring said the session was "capped at 20 hours" while
the cap was a year; and ``/api/token/local`` handed a year to any caller that did not name a
lifetime. The decision recorded with this change: a browser sign-in lasts ``auth.session_ttl``
(30 days by default, the owner's ruling) and the startup link and harness token ARE browser
sign-ins, so they follow that setting; a token a caller mints without naming a lifetime lasts 20
hours, the documented ``personalclaw token`` default; a year stays reachable only when asked for.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw import cli_server
from personalclaw.dashboard import token_auth

REPO = Path(__file__).resolve().parents[1]
_SYNTHETIC_TOKEN = "synthetic.placeholder.not-a-real-token"


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _run_token(monkeypatch, tmp_path, capsys, reply: dict) -> tuple[str, str]:
    (tmp_path / ".local_secret").write_text("synthetic-local-secret\n")
    monkeypatch.setattr(cli_server, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_PORT", "10000")
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=5: _Response(json.dumps(reply).encode())
    )
    cli_server._token(argparse.Namespace(ttl="20h", port=None))
    captured = capsys.readouterr()
    return captured.out, captured.err


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
    assert out == f"http://localhost:10000?token={_SYNTHETIC_TOKEN}\n"
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


def _subcommand_help(name: str) -> str:
    from personalclaw import cli

    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return " ".join(sub.choices[name].format_help().split())


def test_the_lifetimes_the_help_and_docs_state_are_the_codes():
    gateway_help = _subcommand_help("gateway")
    token_help = _subcommand_help("token")
    cli_doc = (REPO / "docs" / "reference" / "cli.md").read_text(encoding="utf-8")
    ready_row = next(line for line in cli_doc.splitlines() if line.startswith("| `--json-ready`"))

    assert "20 hours" not in ready_row, "the harness token lasts 30 days, not 20 hours"
    assert "30 days" in ready_row and "auth.session_ttl" in ready_row, ready_row
    assert "up to 20 hours" not in gateway_help, "the --json-ready help still says 20 hours"
    assert "30 days" in gateway_help and "auth.session_ttl" in gateway_help
    assert "20h" in token_help and "Settings → Devices" in token_help, token_help
    docstring = token_auth.generate_token.__doc__ or ""
    assert "capped at 20 hours" not in docstring and "5 minutes" not in docstring
    token_row = next(line for line in cli_doc.splitlines() if "`personalclaw token" in line)
    assert "20h" in token_row and "Settings → Devices" in token_row, token_row
    assert re.search(r"1 year|a year|8760h", token_row), "the ceiling a caller may ask for"
