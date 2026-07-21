"""Regression: the memory-settings endpoint must round-trip the vault flags.

The PUT handler has an explicit field allowlist; ``vault_enabled`` / ``vault_path``
were initially missing from it, so toggling the vault in the UI silently dropped
the write (the toggle looked on, config never changed). These tests pin both the
PUT persistence and the GET echo so that allowlist can't regress.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.handlers.memory import api_memory_settings


@pytest.fixture
def _cfg(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)
    # config_dir() is consulted by AppConfig.load(); point it at the temp dir too.
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return cfg_file


def _put_request(body: dict):
    request = MagicMock()
    request.method = "PUT"
    request.app = {"state": MagicMock(consolidator=None)}

    async def _json():
        return body

    request.json = _json
    return request


def _get_request():
    request = MagicMock()
    request.method = "GET"
    request.app = {"state": MagicMock(consolidator=None)}
    return request


@pytest.mark.asyncio
async def test_vault_enabled_persists(_cfg):
    resp = await api_memory_settings(_put_request({"vault_enabled": True}))
    assert resp.status == 200
    data = json.loads(_cfg.read_text(encoding="utf-8"))
    assert data["memory"]["vault_enabled"] is True


@pytest.mark.asyncio
async def test_vault_path_persists_and_defaults(_cfg):
    await api_memory_settings(_put_request({"vault_path": "  my-vault  "}))
    data = json.loads(_cfg.read_text(encoding="utf-8"))
    assert data["memory"]["vault_path"] == "my-vault"  # trimmed
    # An empty path falls back to the default rather than persisting "".
    await api_memory_settings(_put_request({"vault_path": ""}))
    data = json.loads(_cfg.read_text(encoding="utf-8"))
    assert data["memory"]["vault_path"] == "memory-vault"


@pytest.mark.asyncio
async def test_get_echoes_vault_fields(_cfg):
    await api_memory_settings(_put_request({"vault_enabled": True}))
    resp = await api_memory_settings(_get_request())
    data = json.loads(resp.body)
    assert data["vault_enabled"] is True
    assert data["vault_path"] == "memory-vault"


@pytest.mark.asyncio
async def test_other_flags_untouched_when_setting_vault(_cfg):
    # Setting the vault flag must not clobber sibling memory config.
    await api_memory_settings(_put_request({"active_recall": False}))
    await api_memory_settings(_put_request({"vault_enabled": True}))
    data = json.loads(_cfg.read_text(encoding="utf-8"))
    assert data["memory"]["active_recall"] is False
    assert data["memory"]["vault_enabled"] is True
