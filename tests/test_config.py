"""Tests for config loader."""

import json

from personalclaw.config.loader import AppConfig, config_dir


class TestAppConfig:
    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.agent.approval_mode == "auto"
        assert cfg.session.timeout_secs == 3600

    def test_load_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "empty"))
        cfg = AppConfig.load()
        assert cfg.agent.approval_mode == "auto"

    def test_load_from_file(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / ".personalclaw" / "config.json"
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text(
            json.dumps(
                {
                    # "streaming" is a retired field — load() must silently drop it
                    "agent": {"approval_mode": "interactive", "streaming": False},
                    "session": {"timeout_secs": 600},
                    "hooks": {"auto_approve_tools": ["ReadFile"]},
                }
            )
        )
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)

        cfg = AppConfig.load()
        assert cfg.agent.approval_mode == "interactive"
        assert not hasattr(cfg.agent, "streaming")  # retired, silently dropped
        assert cfg.session.timeout_secs == 600
        assert cfg.hooks == {"auto_approve_tools": ["ReadFile"]}

    def test_load_invalid_json(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("not json")
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)

        cfg = AppConfig.load()
        assert cfg.agent.approval_mode == "auto"  # falls back to defaults


class TestObserveSizing:
    """observe_max_messages/observe_ttl_hours are generic ChannelHistory sizing —
    top-level keys only (the one-time legacy slack.observe_* fallback was removed
    once live configs were verified migrated). Per-channel activation + all other
    channel-app config is the app's own store — see
    apps/slack-channel/tests/test_settings.py."""

    def test_defaults(self):
        cfg = AppConfig()
        assert cfg.observe_max_messages == 200
        assert cfg.observe_ttl_hours == 168.0

    def test_top_level_keys_win(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / ".personalclaw" / "config.json"
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text(json.dumps({"observe_max_messages": 55, "observe_ttl_hours": 24.0}))
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)
        cfg = AppConfig.load()
        assert cfg.observe_max_messages == 55
        assert cfg.observe_ttl_hours == 24.0

    def test_legacy_slack_observe_keys_are_ignored(self, tmp_path, monkeypatch):
        # The one-time slack.observe_* fallback is gone: a leftover block no longer
        # feeds core sizing — the top-level defaults win (the block itself stays
        # opaque app-owned data; see test_save_preserves_legacy_slack_block).
        cfg_file = tmp_path / ".personalclaw" / "config.json"
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text(
            json.dumps({"slack": {"observe_max_messages": 77, "observe_ttl_hours": 12.0}})
        )
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)
        cfg = AppConfig.load()
        assert cfg.observe_max_messages == 200
        assert cfg.observe_ttl_hours == 168.0

    def test_to_dict_emits_top_level_and_no_slack_block(self):
        d = AppConfig().to_dict()
        assert d["observe_max_messages"] == 200
        assert d["observe_ttl_hours"] == 168.0
        assert "slack" not in d  # Slack config lives in the app's own store

    def test_save_preserves_legacy_slack_block(self, tmp_path, monkeypatch):
        """Core save() must not drop an unmigrated legacy 'slack' block (the app's
        migrate_from_core() owns removing it)."""
        cfg_file = tmp_path / ".personalclaw" / "config.json"
        cfg_file.parent.mkdir(parents=True)
        legacy = {"slack": {"tracking_channels": [{"channel_id": "C1"}], "command": "pc"}}
        cfg_file.write_text(json.dumps(legacy))
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)
        cfg = AppConfig.load()
        cfg.save()
        after = json.loads(cfg_file.read_text())
        assert after["slack"] == legacy["slack"]

    def test_save_serializes_observe_top_level(self, tmp_path, monkeypatch):
        """save() emits observe_* top-level (their only home — the legacy slack.*
        fallback + self-heal were removed once live configs were verified migrated)."""
        cfg_file = tmp_path / ".personalclaw" / "config.json"
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text(json.dumps({"observe_max_messages": 77, "observe_ttl_hours": 12.0}))
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)
        cfg = AppConfig.load()
        cfg.save()
        after = json.loads(cfg_file.read_text())
        assert after["observe_max_messages"] == 77
        assert after["observe_ttl_hours"] == 12.0
        assert "slack" not in after  # nothing invents a slack block

    def test_load_does_not_warn_on_legacy_slack_key(self, tmp_path, monkeypatch, caplog):
        """The frequently-called loader must NOT log-flood 'unrecognized top-level keys:
        slack' for a mid-migration config that still carries a residual slack block."""
        import logging

        cfg_file = tmp_path / ".personalclaw" / "config.json"
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text(json.dumps({"slack": {"observe_max_messages": 55}}))
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)
        with caplog.at_level(logging.WARNING, logger="personalclaw.config.loader"):
            AppConfig.load()
        assert not any("unrecognized top-level keys" in r.message for r in caplog.records)


class TestConfigDir:
    def test_config_dir_is_home_based(self):
        d = config_dir()
        assert d.name == ".personalclaw"
