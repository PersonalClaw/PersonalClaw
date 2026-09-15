"""Doctor's auth line must reflect the real token posture, not the bind (#2860).

`personalclaw doctor` printed **`auth: loopback trusted (no token required)`** for
any local-bound gateway. But a default `personalclaw gateway` on a loopback bind
runs `AuthMode.local_token`, which STILL requires a token on loopback — a tokenless
loopback request gets `403 {"error": "Token required"}` from the `token_auth`
middleware. The diagnostic told the user they could reach the API without a token
when they could not.

A token is genuinely NOT required on loopback only in the three cases the middleware
short-circuits on: `AuthMode.NONE` (`PERSONALCLAW_AUTH_MODE=none`), the blanket
`PERSONALCLAW_DEV_NO_AUTH=1` skip, or the opt-in local-network bypass
(`PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1`). `loopback_requires_token` is the predicate
that mirrors that logic; doctor consults it instead of inferring from the bind alone.
"""

from __future__ import annotations

import urllib.error
from unittest.mock import patch

import pytest

from personalclaw.dashboard.origin import loopback_requires_token

_TOKEN_REQUIRED = "🔒 token required"
_NO_TOKEN = "loopback trusted (no token required)"


# ── the predicate: mirrors the token_auth middleware short-circuits ──────────


def _clear_auth_env(monkeypatch) -> None:
    monkeypatch.delenv("PERSONALCLAW_AUTH_MODE", raising=False)
    monkeypatch.delenv("PERSONALCLAW_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)


def test_default_local_token_gateway_requires_a_token_on_loopback(monkeypatch):
    # This is the bug: a plain gateway (no bypass env) is `local_token`, and the
    # middleware returns 403 to a tokenless loopback request — so a token IS required.
    _clear_auth_env(monkeypatch)
    assert loopback_requires_token() is True


def test_auth_mode_none_needs_no_token(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    assert loopback_requires_token() is False


def test_dev_no_auth_flag_needs_no_token(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("PERSONALCLAW_DEV_NO_AUTH", "1")
    assert loopback_requires_token() is False


def test_bypass_local_networks_needs_no_token(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", "1")
    assert loopback_requires_token() is False


# ── the doctor line: default loopback vs genuine bypass ──────────────────────


def _doctor_output(capsys) -> str:
    """Run ``_doctor()`` with its probes stubbed and a forced local bind."""
    from personalclaw.cli_doctor import _doctor

    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
        patch(
            "subprocess.run",
            return_value=type(
                "R",
                (),
                {
                    "returncode": 0,
                    "stdout": "Python 3.13.14",
                    "stderr": "",
                    "check_returncode": lambda self: None,
                },
            )(),
        ),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
    ):
        try:
            _doctor()
        except SystemExit:
            pass
    return capsys.readouterr().out


def test_doctor_default_loopback_says_token_required(monkeypatch, capsys):
    _clear_auth_env(monkeypatch)
    out = _doctor_output(capsys)
    assert _TOKEN_REQUIRED in out, out
    assert _NO_TOKEN not in out, (
        "a default local_token gateway returns 403 to a tokenless loopback request; "
        f"doctor must not claim {_NO_TOKEN!r}:\n{out}"
    )


@pytest.mark.parametrize(
    "env",
    [
        {"PERSONALCLAW_AUTH_MODE": "none"},
        {"PERSONALCLAW_DEV_NO_AUTH": "1"},
        {"PERSONALCLAW_BYPASS_LOCAL_NETWORKS": "1"},
    ],
)
def test_doctor_genuine_bypass_says_no_token_required(env, monkeypatch, capsys):
    _clear_auth_env(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    out = _doctor_output(capsys)
    assert _NO_TOKEN in out, out
    assert _TOKEN_REQUIRED not in out, out
