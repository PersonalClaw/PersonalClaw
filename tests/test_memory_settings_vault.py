"""Regression: the memory-settings endpoint must round-trip the vault settings.

The PUT handler writes a declared set of fields; the vault fields were initially
missing from it, so toggling the vault in the UI silently dropped the write (the
toggle looked on, config never changed). These tests pin both the PUT persistence
and the GET echo so that set can't regress. (The "allowlist" this docstring used to
claim only became one later: until then a field's ABSENCE was a silent no-op rather
than a 400, which is the same defect one layer up — see
`test_config_write_paths_are_one_validated_mutator.py`.)

MGAV-6 replaced the ``vault_enabled`` bool with the three-valued ``vault_mode``
(off|mirror|two_way). The legacy key is no longer writable and no longer echoed;
what remains of it is the ONE-WAY back-read in ``load()``, pinned below — an
existing install that turned the mirror on must come up mirroring, and must NOT be
silently upgraded to reading the user's files back.
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
    # The PUT audits to SEL now, and a SEL row is serialised to JSONL — a MagicMock
    # caller (what `MagicMock().get(...)` returns) is not JSON-serialisable, so the
    # fake request has to carry the same authenticated user a real one does.
    request.get = lambda key, default=None: "tester" if key == "user" else default

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
async def test_vault_mode_persists(_cfg):
    resp = await api_memory_settings(_put_request({"vault_mode": "two_way"}))
    assert resp.status == 200
    data = json.loads(_cfg.read_text(encoding="utf-8"))
    assert data["memory"]["vault_mode"] == "two_way"


@pytest.mark.asyncio
async def test_unknown_vault_mode_is_a_400_not_a_silent_off(_cfg):
    """A mistyped mode must be REFUSED, not coerced.

    Coercing `two-way` to `off` would look like the setting saved while quietly
    turning the vault off — the shape of failure a closed enum exists to prevent.
    """
    resp = await api_memory_settings(_put_request({"vault_mode": "two-way"}))
    assert resp.status == 400
    # The bad value must not be persisted. (Asserting the KEY is absent would be wrong:
    # `AppConfig.load()` stamps the whole file with its version meta, so every field is
    # already written out at its default.)
    data = json.loads(_cfg.read_text(encoding="utf-8"))
    assert data.get("memory", {}).get("vault_mode", "off") == "off"


@pytest.mark.asyncio
async def test_writing_the_mode_drops_the_retired_flag(_cfg):
    """config.json must not end up holding two answers about the vault."""
    _cfg.write_text(json.dumps({"memory": {"vault_enabled": True}}), encoding="utf-8")
    await api_memory_settings(_put_request({"vault_mode": "off"}))
    data = json.loads(_cfg.read_text(encoding="utf-8"))
    assert data["memory"]["vault_mode"] == "off"
    assert "vault_enabled" not in data["memory"]


def test_legacy_enabled_flag_back_reads_to_mirror(_cfg):
    """The upgrade path: `vault_enabled: true` comes up as `mirror`, never `two_way`."""
    from personalclaw.config.loader import AppConfig

    _cfg.write_text(json.dumps({"memory": {"vault_enabled": True}}), encoding="utf-8")
    assert AppConfig.load().memory.vault_mode == "mirror"
    _cfg.write_text(json.dumps({"memory": {"vault_enabled": False}}), encoding="utf-8")
    assert AppConfig.load().memory.vault_mode == "off"


def test_an_explicit_mode_beats_the_legacy_flag(_cfg):
    """Once the new key exists it is the only one that decides."""
    from personalclaw.config.loader import AppConfig

    _cfg.write_text(
        json.dumps({"memory": {"vault_enabled": True, "vault_mode": "off"}}), encoding="utf-8"
    )
    assert AppConfig.load().memory.vault_mode == "off"


def test_a_typo_in_the_mode_does_not_stop_an_existing_mirror(_cfg):
    """A hand-edited garbage mode falls back to the legacy read, not to `off`.

    A user already browsing a vault should not lose it to a typo; they should keep
    mirroring and see the value rejected next time they save from the UI.
    """
    from personalclaw.config.loader import AppConfig

    _cfg.write_text(
        json.dumps({"memory": {"vault_enabled": True, "vault_mode": "TWO WAY"}}), encoding="utf-8"
    )
    assert AppConfig.load().memory.vault_mode == "mirror"


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
    await api_memory_settings(_put_request({"vault_mode": "mirror"}))
    resp = await api_memory_settings(_get_request())
    data = json.loads(resp.body)
    assert data["vault_mode"] == "mirror"
    assert data["vault_path"] == "memory-vault"
    # The retired flag is gone from the payload — the FE reads one field, not two.
    assert "vault_enabled" not in data


@pytest.mark.asyncio
async def test_other_flags_untouched_when_setting_vault(_cfg):
    # Setting the vault mode must not clobber sibling memory config.
    await api_memory_settings(_put_request({"active_recall": False}))
    await api_memory_settings(_put_request({"vault_mode": "two_way"}))
    data = json.loads(_cfg.read_text(encoding="utf-8"))
    assert data["memory"]["active_recall"] is False
    assert data["memory"]["vault_mode"] == "two_way"
