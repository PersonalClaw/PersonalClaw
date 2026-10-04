"""POST /api/hooks/agent's webhook token check (``_hook_token_refusal``).

The route signs its caller in itself, with the owner's webhook token (``hooks.webhook_token``), so
the check is the one thing between the route and an agent turn: it must run, compare in constant
time, refuse while no usable token is set, and say why in the audit row's reason.

Regression kept from the handler split: the check called ``AppConfig.load()`` without importing
``AppConfig``, a NameError on every call.
"""

from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers import hooks as hooks_mod

#: A token as the owner sets one: long, and nothing else's.
TOKEN = "wh-" + "s3kr1tT0k3n-" * 4


def _req(headers: dict[str, str]):
    req = MagicMock()
    req.headers = headers
    return req


def _set(token: str) -> None:
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    cfg.hooks["webhook_token"] = token
    cfg.save()


@pytest.fixture
def _cfg(monkeypatch, tmp_path):
    """Point config at an isolated home with a known webhook token."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _set(TOKEN)


def test_valid_bearer_token_accepted(_cfg):
    assert hooks_mod._hook_token_refusal(_req({"Authorization": f"Bearer {TOKEN}"})) == ""


def test_valid_header_token_accepted(_cfg):
    assert hooks_mod._hook_token_refusal(_req({"x-personalclaw-token": TOKEN})) == ""


def test_wrong_token_rejected(_cfg):
    assert hooks_mod._hook_token_refusal(_req({"Authorization": "Bearer nope"}))


def test_a_header_that_is_not_ascii_is_refused_not_a_fault(_cfg):
    """`hmac.compare_digest` raises on a non-ASCII str, which made such a header a 500."""
    assert hooks_mod._hook_token_refusal(_req({"Authorization": "Bearer jalapeño"}))


def test_missing_token_rejected(_cfg):
    assert hooks_mod._hook_token_refusal(_req({})) == "no webhook token presented"


def test_unconfigured_token_rejects_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    refused = hooks_mod._hook_token_refusal(_req({"Authorization": "Bearer anything"}))
    assert "no webhook token is set" in refused


def test_a_token_shorter_than_32_bytes_rejects_everything_even_itself(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _set("short-token")
    refused = hooks_mod._hook_token_refusal(_req({"Authorization": "Bearer short-token"}))
    assert "shorter than 32 bytes" in refused


def test_the_internal_credential_cannot_be_the_webhook_token(monkeypatch, tmp_path):
    """The internal credential opens every internal operation; set as the webhook token, it would
    be handed to an outside system. Refused, even when presented."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    secret = "internal-" + "0123456789abcdef" * 3
    (tmp_path / ".local_secret").write_text(secret, encoding="utf-8")
    _set(secret)
    refused = hooks_mod._hook_token_refusal(_req({"Authorization": f"Bearer {secret}"}))
    assert "must not equal" in refused
