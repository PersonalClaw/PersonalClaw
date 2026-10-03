"""A stored switch reads as the word it spells, never by truthiness.

``bool("false")`` is True, and a setting written as text stays text on disk: a hand edit, or an
older request that stored a body's value as it was sent. Read back by truthiness, the owner's
"off" was on: ``security.egress.allow_private: "false"`` in ``config.json`` loaded as ``True`` (the
egress guard then reached private addresses), a provider instance saved with
``"enabled": "false"`` was on, and a script hook switched off in quotes ran.

A stored setting cannot be refused once it is written, so it is read as the word, with the
switch's safe value for anything that spells neither (``safety_flags.strict_bool``). A switch whose
missing key means "on" but whose unreadable value must mean "off", because what it guards runs
code (an app, a script hook, an MCP server), says both (``strict_bool(..., absent=True)``).
``tests/test_request_boolean_census.py`` holds the loaders and every ``config.json`` boolean to it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from personalclaw.safety_flags import strict_bool

TEXT_OFF = ["false", "False", " no ", "off", "0"]
UNREADABLE = ["maybe", "", [], {}, "enabled"]


def _write_config(doc: dict[str, Any]) -> None:
    from personalclaw.config import validation
    from personalclaw.config.loader import config_dir, config_path

    config_dir()
    validation._STRIP_MEMO.clear()
    config_path().write_text(json.dumps(doc), encoding="utf-8")


# ── strict_bool's two defaults ───────────────────────────────────────────────


def test_a_switch_on_unless_switched_off_reads_absent_and_unreadable_apart():
    assert strict_bool(None, field="f", default=False, absent=True) is True
    assert strict_bool("maybe", field="f", default=False, absent=True) is False
    assert strict_bool("false", field="f", default=False, absent=True) is False
    assert strict_bool("true", field="f", default=False, absent=True) is True
    assert strict_bool(None, field="f", default=False) is False


# ── config.json ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("sent", TEXT_OFF, ids=repr)
def test_private_addresses_stay_blocked_when_the_switch_is_off_in_quotes(sent):
    """Three keys deep, where the validation pass used to leave the text for the loader's
    ``bool()``: the egress guard then reached private and LAN addresses."""
    from personalclaw.config.loader import AppConfig

    _write_config({"security": {"egress": {"allow_private": sent}}})
    assert AppConfig.load().security.egress.allow_private is False


def test_a_quoted_true_does_not_open_private_addresses():
    from personalclaw.config.loader import AppConfig

    _write_config({"security": {"egress": {"allow_private": "true"}}})
    assert AppConfig.load().security.egress.allow_private is False
    _write_config({"security": {"egress": {"allow_private": True}}})
    assert AppConfig.load().security.egress.allow_private is True


@pytest.mark.parametrize("sent", TEXT_OFF, ids=repr)
def test_a_switch_that_is_on_by_default_stays_off_when_written_off_in_quotes(sent):
    """Two keys deep the text was stripped to the default, so a default-on switch the owner turned
    off read as on."""
    from personalclaw.config.loader import AppConfig

    _write_config({"updates": {"check_enabled": sent}})
    assert AppConfig.load().updates.check_enabled is False


def test_the_warning_says_what_was_read(caplog):
    from personalclaw.config.loader import AppConfig

    _write_config({"updates": {"check_enabled": "false"}})
    with caplog.at_level("WARNING"):
        AppConfig.load()
    assert any(
        "updates.check_enabled" in r.getMessage() and "read as false" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.parametrize("sent", ["false", "no", "0"], ids=repr)
def test_a_retired_update_flag_written_off_in_quotes_turns_nothing_on(sent):
    from personalclaw.config.loader import AppConfig

    _write_config({"auto_update": sent, "dashboard": {"update_dev_mode": sent}})
    updates = AppConfig.load().updates
    assert updates.auto == "off"
    assert updates.channel != "nightly"


def test_a_retired_update_flag_that_was_on_still_carries_over():
    from personalclaw.config.loader import AppConfig

    _write_config({"auto_update": True})
    assert AppConfig.load().updates.auto == "staged"


@pytest.mark.parametrize("sent, mode", [("false", "off"), ("no", "off"), (True, "mirror")])
def test_the_retired_vault_switch_reads_as_the_word(sent, mode):
    from personalclaw.config.loader import AppConfig

    _write_config({"memory": {"vault_enabled": sent}})
    assert AppConfig.load().memory.vault_mode == mode


@pytest.mark.parametrize("sent", TEXT_OFF, ids=repr)
def test_the_terminal_opt_out_in_quotes_turns_the_terminal_off(sent, monkeypatch):
    from personalclaw.dashboard.handlers import terminal

    monkeypatch.setattr(terminal, "_enabled_cache", [True, 0.0])
    _write_config({"dashboard": {"terminal": {"enabled": sent}}})
    assert terminal._is_enabled(None) is False  # type: ignore[arg-type]


def test_the_terminal_is_on_unless_switched_off(monkeypatch):
    from personalclaw.dashboard.handlers import terminal

    monkeypatch.setattr(terminal, "_enabled_cache", [True, 0.0])
    _write_config({"dashboard": {}})
    assert terminal._is_enabled(None) is True  # type: ignore[arg-type]


# ── records ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("sent", TEXT_OFF, ids=repr)
def test_an_app_switched_off_in_quotes_stays_off(sent):
    from personalclaw.apps.manager import InstalledApp

    assert InstalledApp.from_dict({"name": "notes-app", "enabled": sent}).enabled is False


@pytest.mark.parametrize("sent", UNREADABLE, ids=repr)
def test_an_app_whose_switch_spells_neither_stays_off(sent):
    """An app runs code: a switch that cannot be read keeps it off."""
    from personalclaw.apps.manager import InstalledApp

    assert InstalledApp.from_dict({"name": "notes-app", "enabled": sent}).enabled is False


def test_an_app_is_on_unless_switched_off():
    from personalclaw.apps.manager import InstalledApp

    assert InstalledApp.from_dict({"name": "notes-app"}).enabled is True
    assert InstalledApp.from_dict({"name": "notes-app", "enabled": True}).enabled is True


@pytest.mark.parametrize("sent", TEXT_OFF, ids=repr)
def test_a_provider_instance_switched_off_in_quotes_stays_off(sent):
    from personalclaw.providers.instances import ExtensionInstance

    record = {"id": "work", "extension_name": "example-models", "enabled": sent}
    assert ExtensionInstance.from_dict(record).enabled is False


@pytest.mark.parametrize("sent", TEXT_OFF, ids=repr)
def test_a_saved_source_query_switched_off_in_quotes_stays_off(sent):
    from personalclaw.knowledge.source_queries import SavedSourceQuery

    record = {"id": "q1", "name": "Releases", "query": "intitle:release", "enabled": sent}
    assert SavedSourceQuery.from_dict(record).enabled is False


@pytest.mark.parametrize("sent", [*TEXT_OFF, *UNREADABLE], ids=repr)
def test_a_script_hook_switched_off_or_unreadable_does_not_run(sent):
    from personalclaw.hooks import ScriptHook

    assert ScriptHook.from_dict({"id": "h1", "name": "Format", "enabled": sent}).enabled is False


def test_a_script_hook_is_on_unless_switched_off():
    from personalclaw.hooks import ScriptHook

    assert ScriptHook.from_dict({"id": "h1", "name": "Format"}).enabled is True


@pytest.mark.parametrize(
    "spec, off",
    [
        ({}, False),
        ({"disabled": False}, False),
        ({"disabled": "false"}, False),
        ({"disabled": "no"}, False),
        ({"disabled": True}, True),
        ({"disabled": "true"}, True),
        ({"disabled": "maybe"}, True),
    ],
)
def test_an_mcp_server_switch_reads_as_the_word(spec, off):
    from personalclaw.mcp_status import switched_off

    assert switched_off(spec, "notes") is off


def test_an_mcp_server_off_in_quotes_is_off_on_its_card_and_stays_off_when_saved():
    from personalclaw.providers.mcp_instances import _config_to_spec, _spec_to_instance

    spec = {"command": "/nonexistent/pc-fixture-mcp", "disabled": "true"}
    assert _spec_to_instance("notes", spec).enabled is False
    saved = _config_to_spec({"command": "/nonexistent/pc-fixture-mcp"}, spec)
    assert saved.get("disabled") is True


@pytest.mark.parametrize("use_case", ["stt", "tts"])
@pytest.mark.parametrize("sent", TEXT_OFF, ids=repr)
def test_speech_switched_off_in_quotes_stays_off(use_case, sent):
    from personalclaw.providers.use_cases import use_case_enabled

    assert use_case_enabled(use_case, {"enabled": sent}) is False


def test_speech_switches_keep_their_defaults():
    from personalclaw.providers.use_cases import use_case_enabled

    assert use_case_enabled("stt", {}) is True
    assert use_case_enabled("tts", {}) is False


@pytest.mark.parametrize("sent", TEXT_OFF, ids=repr)
def test_a_notification_rule_condition_off_in_quotes_is_off(sent):
    from personalclaw.notification_rules import _coerce_conditions

    assert _coerce_conditions({"name_mention": sent}).name_mention is False


@pytest.mark.parametrize("sent", TEXT_OFF, ids=repr)
def test_a_project_name_lock_off_in_quotes_is_off(sent):
    from personalclaw.tasks.models import Project

    assert (
        Project.from_dict({"id": "p1", "name": "Garden", "name_locked": sent}).name_locked is False
    )


@pytest.mark.parametrize("sent", TEXT_OFF, ids=repr)
def test_a_reminder_opt_out_in_quotes_is_an_opt_out(sent):
    from personalclaw.tasks.models import Task

    assert (
        Task.from_dict({"id": "t-1", "title": "Water", "due_reminder": sent}).due_reminder is False
    )


def test_an_unreadable_reminder_opt_out_keeps_reminding():
    from personalclaw.tasks.models import Task

    assert Task.from_dict({"id": "t-1", "title": "Water", "due_reminder": "maybe"}).due_reminder


@pytest.mark.parametrize("sent", TEXT_OFF, ids=repr)
def test_a_room_and_an_intent_and_a_voice_lock_read_the_word(sent):
    from personalclaw.knowledge.intents import Intent
    from personalclaw.rooms.store import Room
    from personalclaw.voice.profiles import VoiceProfile

    room = Room.from_dict({"id": "design-review", "archived": sent, "paused": sent})
    assert room.archived is False and room.paused is False
    intent = Intent.from_dict({"goal": "Collect recipes", "enabled": sent, "propose_skill": sent})
    assert intent.enabled is False and intent.propose_skill is False
    assert VoiceProfile.from_dict({"id": "v1", "name": "Calm", "locked": sent}).locked is False
