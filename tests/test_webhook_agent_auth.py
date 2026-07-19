"""POST /api/hooks/agent token verification (_verify_hook_token).

Regression: ``_verify_hook_token`` called ``AppConfig.load()`` without importing
``AppConfig`` — a NameError since the handler split, so EVERY external webhook
call to /api/hooks/agent crashed (500) instead of running the agent turn. The
route is exempt from dashboard auth middleware (internal_paths), so this token
check is its ONLY auth gate — it must actually execute.
"""

from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers import hooks as hooks_mod


def _req(headers: dict[str, str]):
    req = MagicMock()
    req.headers = headers
    return req


@pytest.fixture
def _cfg(monkeypatch, tmp_path):
    """Point config at an isolated home with a known webhook token."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    cfg.hooks["webhook_token"] = "sekrit-token"
    cfg.save()
    return cfg


def test_valid_bearer_token_accepted(_cfg):
    assert hooks_mod._verify_hook_token(_req({"Authorization": "Bearer sekrit-token"})) is True


def test_valid_header_token_accepted(_cfg):
    assert hooks_mod._verify_hook_token(_req({"x-personalclaw-token": "sekrit-token"})) is True


def test_wrong_token_rejected(_cfg):
    assert hooks_mod._verify_hook_token(_req({"Authorization": "Bearer nope"})) is False


def test_missing_token_rejected(_cfg):
    assert hooks_mod._verify_hook_token(_req({})) is False


def test_unconfigured_token_rejects_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    assert hooks_mod._verify_hook_token(_req({"Authorization": "Bearer anything"})) is False
