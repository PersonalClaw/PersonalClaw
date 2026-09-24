"""A legitimate warning repeated a thousand times is not a louder warning — it is a quieter log.

Both findings here were measured off the Chairman's 2026-09-23 `gateway.log`: 1144 of ~2000 lines
in one browsing session were the SAME warning, `Config: unrecognized top-level keys: auto_update`.
Two independent defects produced that number, and fixing either alone leaves the other:

1. **`auto_update` was half-known.** `AppConfig.load_with_migration_state` read the retired key as
   a legacy-backfill input while `config.validation`, one call earlier, had never heard of it — so
   every load both migrated the home correctly AND reported its config as containing an
   unrecognized key. The key is now CONSUMED by the validator's retired-key step, so it cannot be
   reported and the next `save()` rewrites the file without it.
2. **the config was re-validated per request.** `AppConfig.load()` is a pure read called from ~300
   sites, and it re-ran jsonschema over the whole sixty-section schema and re-logged every finding
   each time. That is what turned one warning into 1144, and it is why eight `FileNotFoundError`
   tracebacks in the same window were easy to miss.

Every arm here writes into `tmp_path` with `PERSONALCLAW_HOME` redirected; nothing touches a real
home.
"""

from __future__ import annotations

import json
import logging

import pytest

from personalclaw.config import validation
from personalclaw.config.loader import AppConfig

_UNKNOWN_KEY_MARKER = "unrecognized top-level keys"


@pytest.fixture
def home(monkeypatch, tmp_path):
    """An isolated config home. `_STRIP_MEMO` is cleared so an arm cannot ride a neighbour's hit."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    validation._STRIP_MEMO.clear()
    return tmp_path


def _write(home, payload: dict) -> None:
    (home / "config.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _unknown_key_warnings(records) -> list[str]:
    return [r.getMessage() for r in records if _UNKNOWN_KEY_MARKER in r.getMessage()]


class TestTheLegacyAutoUpdateKeyIsConsumed:
    def test_a_legacy_home_does_not_warn(self, home, caplog):
        """🔴 THE REGRESSION, in the exact shape the Chairman's home has it."""
        _write(home, {"auto_update": True})
        with caplog.at_level(logging.WARNING):
            AppConfig.load()
        assert _unknown_key_warnings(caplog.records) == []

    def test_a_legacy_home_still_gets_its_intent(self, home):
        """Consumed, not merely silenced: `auto_update=true` still means unattended-staged."""
        _write(home, {"auto_update": True})
        cfg = AppConfig.load()
        assert cfg.updates.auto == "staged"
        assert cfg.updates.channel == "stable"

    def test_auto_update_false_maps_to_off(self, home):
        _write(home, {"auto_update": False})
        assert AppConfig.load().updates.auto == "off"

    def test_an_explicit_updates_field_wins_over_the_legacy_flag(self, home):
        """The RUM-1 rule. A user who set `updates.auto` must not have it overwritten by a
        stale flag their old install left behind."""
        _write(home, {"auto_update": True, "updates": {"auto": "off"}})
        assert AppConfig.load().updates.auto == "off"

    def test_the_legacy_track_main_flag_is_consumed_too(self, home, caplog):
        """`dashboard.update_dev_mode` is the sibling retired flag; it moved with `auto_update`
        so the vocabulary is not split across two files."""
        _write(home, {"dashboard": {"update_dev_mode": True}})
        with caplog.at_level(logging.WARNING):
            cfg = AppConfig.load()
        assert cfg.updates.channel == "nightly"
        assert "update_dev_mode" not in json.dumps(
            [r.getMessage() for r in caplog.records]
        ), "the retired flag must not be reported"

    def test_an_explicit_channel_wins_over_the_legacy_flag(self, home):
        _write(home, {"dashboard": {"update_dev_mode": True}, "updates": {"channel": "beta"}})
        assert AppConfig.load().updates.channel == "beta"

    def test_the_keys_are_removed_from_the_parsed_dict(self):
        """The mechanism, directly: a caller downstream of validation must not still see them."""
        data = {"auto_update": True, "dashboard": {"update_dev_mode": True}}
        validation._validate_config_data(data)
        assert "auto_update" not in data
        assert "update_dev_mode" not in data["dashboard"]
        assert data["updates"] == {"auto": "staged", "channel": "nightly"}

    def test_a_genuinely_unknown_key_is_still_reported(self, home, caplog):
        """The control. Without it every arm above could be passing because the unknown-key
        check itself had been disarmed."""
        _write(home, {"definitely_not_a_section": {"x": 1}})
        with caplog.at_level(logging.WARNING):
            AppConfig.load()
        assert _unknown_key_warnings(caplog.records), "the unknown-key check is disarmed"

    def test_the_retired_inbound_section_is_dropped(self, home, caplog):
        """`inbound` was MCP-READONLY-INBOUND's single-surface section, replaced OUTRIGHT by
        `external_access` with no back-read. Found by the fixture arm below once `auto_update`
        stopped warning — the same flood, one key along."""
        _write(home, {"inbound": {"mcp": {"enabled": False}, "public_url": ""}})
        with caplog.at_level(logging.WARNING):
            AppConfig.load()
        assert _unknown_key_warnings(caplog.records) == []

    def test_the_shipped_six_month_home_fixture_loads_clean(self, monkeypatch, tmp_path, caplog):
        """The real thing, not a hand-built dict: the shipped `six-month-home` fixture carries
        `auto_update: true`, `dashboard.update_dev_mode: false` AND a retired `inbound` block —
        an actual aged home, and the arm that found the third key."""
        import shutil

        from personalclaw import tests_fixtures

        src = tests_fixtures.__path__[0] + "/six-month-home/config.json"
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        validation._STRIP_MEMO.clear()
        shutil.copy(src, tmp_path / "config.json")
        raw = json.loads((tmp_path / "config.json").read_text())
        assert raw.get("auto_update") is True, "fixture no longer carries the legacy key"
        with caplog.at_level(logging.WARNING):
            cfg = AppConfig.load()
        assert _unknown_key_warnings(caplog.records) == []
        assert cfg.updates.auto == "staged"


class TestValidationRunsOncePerContent:
    def test_the_warning_is_not_repeated_across_loads(self, home, caplog):
        """🔴 THE FLOOD. 1144 identical lines came from ~1144 loads of one unchanged file."""
        _write(home, {"definitely_not_a_section": {"x": 1}})
        with caplog.at_level(logging.WARNING):
            for _ in range(25):
                AppConfig.load()
        assert len(_unknown_key_warnings(caplog.records)) == 1

    def test_jsonschema_is_not_re_run_per_load(self, home, monkeypatch):
        """The perf half, measured rather than assumed: the expensive pass must run once."""
        calls: list[int] = []
        real = validation.jsonschema.validate

        def counting(*a, **k):
            calls.append(1)
            return real(*a, **k)

        monkeypatch.setattr(validation.jsonschema, "validate", counting)
        _write(home, {"agent": {"log_level": "INFO"}})
        for _ in range(25):
            AppConfig.load()
        assert len(calls) == 1, f"jsonschema ran {len(calls)} times for one unchanged config"

    def test_a_write_re_reports(self, home, caplog):
        """Invalidation is the write itself — there is nothing to remember to call."""
        _write(home, {"definitely_not_a_section": {"x": 1}})
        with caplog.at_level(logging.WARNING):
            AppConfig.load()
            _write(home, {"also_not_a_section": {"x": 1}})
            AppConfig.load()
        messages = _unknown_key_warnings(caplog.records)
        assert len(messages) == 2
        assert "definitely_not_a_section" in messages[0]
        assert "also_not_a_section" in messages[1]

    def test_a_cache_hit_strips_the_invalid_value_just_like_a_miss(self, home):
        """The correctness half. Skipping jsonschema must not skip the STRIP, or a second load
        would read an invalid value where the first read the default."""
        _write(home, {"agent": {"log_level": "not-a-level"}})
        first = AppConfig.load().agent.log_level
        second = AppConfig.load().agent.log_level
        assert first == second
        assert first != "not-a-level"

    def test_the_memo_holds_one_entry(self, home):
        """Bounded by construction: `config.json` has one current content, so a memo that grew
        would only be holding stale views of a file that no longer looks like that."""
        for i in range(5):
            _write(home, {"observe_max_messages": 10 + i})
            AppConfig.load()
        assert len(validation._STRIP_MEMO) == 1

    def test_the_memo_stores_paths_and_never_values(self, home):
        """A cache on the config path must not become a place config secrets accumulate."""
        _write(home, {"agent": {"log_level": "not-a-level"}})
        AppConfig.load()
        stored = list(validation._STRIP_MEMO.values())
        assert stored == [("agent.log_level",)]
        assert "not-a-level" not in json.dumps(stored)

    def test_the_uncached_entry_point_still_reports_every_time(self):
        """Direct callers (and the tests that drive them) keep the old behaviour exactly —
        the cache is the loader's, not the validator's."""
        seen = []
        logger = logging.getLogger("personalclaw.config.validation")

        class _Collect(logging.Handler):
            def emit(self, record):
                seen.append(record.getMessage())

        handler = _Collect()
        logger.addHandler(handler)
        try:
            for _ in range(3):
                validation._validate_config_data({"definitely_not_a_section": {"x": 1}})
        finally:
            logger.removeHandler(handler)
        assert len([m for m in seen if _UNKNOWN_KEY_MARKER in m]) == 3
