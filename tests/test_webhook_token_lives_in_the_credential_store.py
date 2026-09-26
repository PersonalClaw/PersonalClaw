"""The webhook token lives in the credential store; ``config.json`` holds a reference.

``hooks.webhook_token`` — the bearer token ``POST /api/hooks/agent`` checks — was the second
secret #3607 disclosed and left inline (``docs/security/limitations.md`` §6): in ``config.json``
as typed, so in every snapshot, every export and the local time-travel history. Its writers are
``AppConfig.save()`` (the path every API handler that saves the config takes) and the CLI's
``config set`` / ``--file`` / ``unset``; every one of them now stores the value and writes the
reference, the reader resolves it, and the boot move converts a token an earlier release wrote.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import zipfile
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import credential_names, get_credential
from personalclaw.config.secret_refs import migrate_plaintext_secrets, ref_key
from personalclaw.dashboard.handlers import hooks as hooks_mod

TOKEN = "whk_fixtureWebhookToken_4f3e2d1c0b9a8877"
OLD_TOKEN = "whk_fixtureBackupToken_1234567890abcdef"


def _key() -> str:
    """The credential-store key the webhook token is owned under."""
    from personalclaw.config.secret_refs import config_owner

    return config_owner("hooks").key("webhook_token")


@pytest.fixture
def home(monkeypatch):
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    return config_loader.config_dir()


def _config(home) -> dict:
    return json.loads((home / "config.json").read_text(encoding="utf-8"))


def _authenticates(token: str) -> bool:
    req = MagicMock()
    req.headers = {"Authorization": f"Bearer {token}"}
    return hooks_mod._verify_hook_token(req)


def _cli(**kwargs) -> None:
    from personalclaw.cli_config import _config_cmd

    args = {"config_action": "set", "key": None, "value": None, "file": None, "reveal": False}
    _config_cmd(argparse.Namespace(**{**args, **kwargs}))


def _assert_only_a_reference(home) -> None:
    text = (home / "config.json").read_text(encoding="utf-8")
    assert TOKEN not in text
    assert ref_key(_config(home)["hooks"]["webhook_token"]) == _key()
    assert get_credential(_key()) == TOKEN
    assert _authenticates(TOKEN) and not _authenticates("nope")


# ── the API writer: AppConfig.save() ────────────────────────────────────────


def test_a_token_saved_through_appconfig_never_lands_in_config_json(home):
    cfg = config_loader.AppConfig.load()
    cfg.hooks["webhook_token"] = TOKEN
    cfg.save()
    _assert_only_a_reference(home)


def test_an_api_request_that_saves_the_config_moves_a_plaintext_token_out(home):
    """A token an earlier release (or a hand edit) left in the file does not survive the next
    API write: `POST /api/agents` saves the config, and the save stores the token."""
    from personalclaw.dashboard.handlers import agents as agents_mod

    (home / "config.json").write_text(json.dumps({"hooks": {"webhook_token": TOKEN}}))
    req = make_mocked_request("POST", "/api/agents")

    async def _json():
        return {"name": "fixture-agent"}

    req.json = _json
    assert asyncio.run(agents_mod.api_personalclaw_agents_create(req)).status == 200
    assert "fixture-agent" in _config(home)["agents"]
    _assert_only_a_reference(home)


# ── the CLI writers ─────────────────────────────────────────────────────────


def test_config_set_stores_the_token_and_the_audit_row_carries_only_the_key(home, capsys):
    _cli(key="hooks.webhook_token", value=TOKEN)
    _assert_only_a_reference(home)
    out = capsys.readouterr().out
    assert TOKEN not in out and "credential store" in out
    # The security log travels in every export; the row names the key, never the value.
    events = (home / "security_events.jsonl").read_text(encoding="utf-8")
    assert "hooks.webhook_token" in events
    assert TOKEN not in events


def test_config_set_of_the_whole_hooks_section_stores_the_token(home):
    _cli(key="hooks", value=json.dumps({"webhook_token": TOKEN, "auto_approve_sources": ["ci"]}))
    _assert_only_a_reference(home)
    assert _config(home)["hooks"]["auto_approve_sources"] == ["ci"]
    assert TOKEN not in (home / "security_events.jsonl").read_text(encoding="utf-8")


def test_config_set_file_stores_the_token(home, tmp_path):
    (home / "config.json").write_text(json.dumps({"hooks": {}}))
    handed_back = tmp_path / "edited.json"
    handed_back.write_text(json.dumps({"hooks": {"webhook_token": TOKEN}}))
    _cli(file=str(handed_back))
    _assert_only_a_reference(home)


def test_config_unset_removes_the_stored_token(home):
    _cli(key="hooks.webhook_token", value=TOKEN)
    assert _key() in credential_names()
    _cli(config_action="unset", key="hooks.webhook_token")
    assert "webhook_token" not in _config(home)["hooks"]
    assert _key() not in credential_names()
    assert not _authenticates(TOKEN)


# ── the one-time move at boot ───────────────────────────────────────────────


def test_the_boot_move_converts_a_plaintext_token_and_the_hook_still_authenticates(home):
    (home / "config.json").write_text(json.dumps({"hooks": {"webhook_token": TOKEN}}))
    (home / "config.json.bak").write_text(json.dumps({"hooks": {"webhook_token": OLD_TOKEN}}))

    moved = migrate_plaintext_secrets()
    assert "config.json" in moved and "config.json.bak" in moved
    _assert_only_a_reference(home)
    # The backup is pointed at the live key and its (older) value is not stored.
    bak = json.loads((home / "config.json.bak").read_text(encoding="utf-8"))
    assert ref_key(bak["hooks"]["webhook_token"]) == _key()
    assert OLD_TOKEN not in (home / ".env").read_text(encoding="utf-8")
    assert migrate_plaintext_secrets() == []


# ── snapshots and exports ───────────────────────────────────────────────────


def test_an_export_carries_no_webhook_token(home):
    from personalclaw.portability import create_export_zip

    _cli(key="hooks.webhook_token", value=TOKEN)
    data, _manifest = create_export_zip()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = {i.filename: zf.read(i) for i in zf.infolist() if not i.is_dir()}
    assert [n for n, body in members.items() if TOKEN.encode() in body] == []
    assert any(n.endswith("config.json") for n in members)
