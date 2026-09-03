"""Entity-settings routes (notifications + inbox) — the PUT must persist only
KNOWN keys, not blindly merge arbitrary body keys into the store.

Regression for bug #22: a blind ``current.update(body)`` let any key (e.g. a
typo'd or garbage field) persist and then leak back through every GET's
``{**DEFAULTS, **loaded}`` merge, polluting the config. The defaults dict is the
authoritative allowlist.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.providers import entity_routes as er


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch, tmp_path):
    # Point the entity-settings dir at a tmp path so the live store is untouched.
    monkeypatch.setattr(er, "_entity_settings_path", lambda entity: tmp_path / f"{entity}.json")


def _req(body):
    r = MagicMock()
    r.json = AsyncMock(return_value=body)
    return r


async def _json(resp):
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_notifications_put_persists_known_keys():
    # NB: "warn" (the old fixture value) is now correctly a 400 — it was
    # out-of-domain all along and ranked as info in the delivery gate.
    resp = await er.handle_notifications_settings_put(
        _req({"mute_all": True, "min_severity": "warning"})
    )
    data = await _json(resp)
    assert data["ok"] is True
    assert data["settings"]["mute_all"] is True
    assert data["settings"]["min_severity"] == "warning"


@pytest.mark.asyncio
async def test_notifications_put_drops_unknown_keys():
    """The core of bug #22 — an unknown key must NOT persist."""
    resp = await er.handle_notifications_settings_put(
        _req({"mute_all": True, "totally_bogus_key_xyz": "junk", "sound_enabled": None})
    )
    settings = (await _json(resp))["settings"]
    assert settings["mute_all"] is True  # known key applied
    assert "totally_bogus_key_xyz" not in settings  # garbage rejected
    assert "sound_enabled" not in settings  # not-in-schema rejected
    # And it's not on disk either (GET would otherwise leak it back).
    get_resp = await er.handle_notifications_settings_get(_req({}))
    got = (await _json(get_resp))["settings"]
    assert "totally_bogus_key_xyz" not in got
    assert set(got) == set(er.NOTIFICATIONS_DEFAULTS)


@pytest.mark.asyncio
async def test_notifications_migrates_legacy_master_mute_key(tmp_path):
    """A store written before the mute_all rename must read back as mute_all
    (and self-heal to the new key on the next PUT)."""
    store = er._entity_settings_path("notifications")
    store.write_text(json.dumps({"master_mute": True, "min_severity": "warn"}))

    get_resp = await er.handle_notifications_settings_get(_req({}))
    got = (await _json(get_resp))["settings"]
    assert got["mute_all"] is True
    assert "master_mute" not in got

    await er.handle_notifications_settings_put(_req({"min_severity": "error"}))
    healed = json.loads(store.read_text())
    assert healed["mute_all"] is True
    assert "master_mute" not in healed


@pytest.mark.asyncio
async def test_inbox_put_drops_unknown_keys():
    """The inbox settings handler has the same guard."""
    resp = await er.handle_inbox_settings_put(_req({"retention_days": 30, "nope_not_a_setting": 1}))
    settings = (await _json(resp))["settings"]
    assert settings["retention_days"] == 30
    assert "nope_not_a_setting" not in settings


@pytest.mark.asyncio
async def test_put_rejects_non_object_body():
    resp = await er.handle_notifications_settings_put(_req(["not", "an", "object"]))
    assert resp.status == 400


@pytest.mark.asyncio
async def test_inbox_put_rejects_mistyped_values():
    """A known key with a wrong-TYPE value must 400, not persist.

    Regression: `retention_days: true` persisted and int(True) == 1 made maintenance
    delete everything older than a day. (The `alert_keywords: "urgent"` case that used to
    live here — a string whose CHARACTERS became keywords — is gone with the field itself;
    the equivalent guard on the rules PUT is
    test_rules_put_rejects_malformed_shapes.)"""
    for body in (
        {"retention_days": True},
        {"retention_days": "90"},
        {"auto_cleanup_enabled": 1},
    ):
        resp = await er.handle_inbox_settings_put(_req(body))
        assert resp.status == 400, f"accepted mistyped {body}"
    # Store untouched → GET returns pristine defaults.
    got = (await _json(await er.handle_inbox_settings_get(_req({}))))["settings"]
    assert got == er.INBOX_DEFAULTS


@pytest.mark.asyncio
async def test_inbox_put_rejects_out_of_range_retention():
    """retention_days outside the UI's [1, 3650] clamp must 400 — the
    consumer's max(1, …) would turn 0/-5 into a 1-day mass-cleanup window."""
    for days in (0, -5, 4000):
        resp = await er.handle_inbox_settings_put(_req({"retention_days": days}))
        assert resp.status == 400, f"accepted retention_days={days}"
    resp = await er.handle_inbox_settings_put(_req({"retention_days": 30}))
    assert (await _json(resp))["settings"]["retention_days"] == 30


@pytest.mark.asyncio
async def test_notifications_put_rejects_mistyped_values():
    """Same type guard on the notifications handler."""
    for body in ({"mute_all": "yes"}, {"quiet_hours_start": 22}):
        resp = await er.handle_notifications_settings_put(_req(body))
        assert resp.status == 400, f"accepted mistyped {body}"


@pytest.mark.asyncio
async def test_notifications_put_rejects_out_of_domain_values():
    """Well-typed but out-of-DOMAIN values must 400, not persist — they
    silently broke the delivery gate: an unknown min_severity ranked as
    info (threshold gone) and an unparseable quiet-hours time made
    _in_quiet_window() always False (quiet hours enabled but dead)."""
    for body in (
        {"min_severity": "banana"},
        {"min_severity": ""},
        {"quiet_hours_start": ""},
        {"quiet_hours_start": "25:00"},
        {"quiet_hours_end": "8pm"},
        {"quiet_hours_end": "12:75"},
    ):
        resp = await er.handle_notifications_settings_put(_req(body))
        assert resp.status == 400, f"accepted out-of-domain {body}"
    # Store untouched → GET returns pristine defaults.
    got = (await _json(await er.handle_notifications_settings_get(_req({}))))["settings"]
    assert got == er.NOTIFICATIONS_DEFAULTS
    # And the valid shapes still round-trip.
    resp = await er.handle_notifications_settings_put(
        _req({"min_severity": "error", "quiet_hours_start": "23:15"})
    )
    settings = (await _json(resp))["settings"]
    assert settings["min_severity"] == "error"
    assert settings["quiet_hours_start"] == "23:15"


@pytest.mark.asyncio
async def test_notifications_drops_retired_default_channel(tmp_path):
    """default_channel was retired (no provider declares type=notification, no
    delivery consumer) — a legacy store carrying it must not leak it back."""
    store = er._entity_settings_path("notifications")
    store.write_text(json.dumps({"default_channel": "browser", "mute_all": True}))
    got = (await _json(await er.handle_notifications_settings_get(_req({}))))["settings"]
    assert "default_channel" not in got
    assert got["mute_all"] is True


class TestNotificationAllowed:
    """The notify() delivery gate — mute_all / min_severity / quiet hours."""

    def _write(self, **settings):
        er._save_entity_settings("notifications", settings)

    def test_defaults_allow_everything(self):
        for kind in (
            "info",
            "cron",
            "heartbeat",
            "warning",
            "inbox_alert",
            "error",
            "unknown-kind",
        ):
            assert er.notification_allowed(kind) is True

    def test_mute_all_blocks_everything(self):
        self._write(mute_all=True)
        assert er.notification_allowed("error") is False
        assert er.notification_allowed("info") is False

    def test_min_severity_warning_filters_info_kinds(self):
        self._write(min_severity="warning")
        assert er.notification_allowed("cron") is False  # info-ranked
        assert er.notification_allowed("heartbeat") is False  # info-ranked
        assert er.notification_allowed("warning") is True
        assert er.notification_allowed("inbox_alert") is True  # user-configured alert = warning
        assert er.notification_allowed("error") is True

    def test_min_severity_error_only(self):
        self._write(min_severity="error")
        assert er.notification_allowed("warning") is False
        assert er.notification_allowed("error") is True

    def test_quiet_hours_suppress_non_critical(self):
        from datetime import datetime

        self._write(quiet_hours_enabled=True, quiet_hours_start="22:00", quiet_hours_end="08:00")
        inside = datetime(2026, 1, 1, 23, 30)  # wraps midnight
        inside2 = datetime(2026, 1, 1, 7, 59)
        outside = datetime(2026, 1, 1, 12, 0)
        assert er.notification_allowed("info", now=inside) is False
        assert er.notification_allowed("warning", now=inside2) is False
        assert er.notification_allowed("error", now=inside) is True  # critical rides through
        assert er.notification_allowed("info", now=outside) is True

    def test_quiet_hours_non_wrapping_window(self):
        from datetime import datetime

        self._write(quiet_hours_enabled=True, quiet_hours_start="09:00", quiet_hours_end="17:00")
        assert er.notification_allowed("info", now=datetime(2026, 1, 1, 12, 0)) is False
        assert er.notification_allowed("info", now=datetime(2026, 1, 1, 8, 59)) is True

    def test_garbage_quiet_window_never_matches(self):
        from datetime import datetime

        self._write(quiet_hours_enabled=True, quiet_hours_start="bogus", quiet_hours_end="08:00")
        assert er.notification_allowed("info", now=datetime(2026, 1, 1, 3, 0)) is True


@pytest.mark.asyncio
async def test_state_notify_respects_gate(monkeypatch, tmp_path):
    """DashboardState.notify() must consult the gate: a muted store drops the
    note (no log append, no broadcast, no persist)."""
    from personalclaw.dashboard import state as st

    er._save_entity_settings("notifications", {"mute_all": True})
    ds = object.__new__(st.DashboardState)  # skip heavyweight __init__
    ds._notification_log = []
    # `notify()` reads the desktop registry to decide the `native` target (DC-5). Supplied
    # explicitly rather than left off: the decision fails OPEN, so an absent attribute would
    # make this test pass through the except branch and stop exercising the live path.
    from personalclaw.dashboard.desktop_registry import DesktopRegistry

    ds.desktop = DesktopRegistry()
    broadcasts = []
    persisted = []
    monkeypatch.setattr(st.DashboardState, "_broadcast", lambda self, note: broadcasts.append(note))
    monkeypatch.setattr(st, "_persist_notification", lambda note: persisted.append(note))

    ds.notify("info", "Muted", "should not deliver")
    assert ds._notification_log == [] and broadcasts == [] and persisted == []

    er._save_entity_settings("notifications", {"mute_all": False})
    ds.notify("info", "Live", "delivers")
    assert len(ds._notification_log) == 1 and len(broadcasts) == 1 and len(persisted) == 1


# ── Notification rules matrix (INBOX-NOTIFICATIONS-UNIFICATION T1.3) ──
#
# The guards here exist because a rules file that fails to parse degrades to
# registry defaults — which means a REJECTED-at-write value that got persisted
# anyway becomes silent policy failure: the user sets `never` on a noisy kind,
# sees it accepted, and keeps getting notified. So the PUT must 400 rather than
# store anything the read path would later ignore.


@pytest.fixture()
def _isolate_rules(monkeypatch, tmp_path):
    from personalclaw import notification_rules as nr

    (tmp_path / "entity_settings").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(nr, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_rules_get_returns_a_row_per_registered_kind(_isolate_rules):
    from personalclaw import notification_kinds as nk

    resp = await er.handle_notification_rules_get(MagicMock())
    data = await _json(resp)
    assert {r["key"] for r in data["rules"]} == {k.key for k in nk.all_kinds()}
    assert data["digest"]["schedule"]


@pytest.mark.asyncio
async def test_rules_put_persists_and_takes_effect(_isolate_rules):
    from personalclaw import notification_rules as nr

    resp = await er.handle_notification_rules_put(
        _req({"rules": {"heartbeat/status": {"mode": "badge"}}})
    )
    data = await _json(resp)
    assert data["ok"] is True
    # The effective read path — not just the response echo — must reflect it.
    assert nr.resolve_rule("heartbeat", "status").mode == "badge"


@pytest.mark.asyncio
async def test_rules_put_merges_rather_than_replacing(_isolate_rules):
    from personalclaw import notification_rules as nr

    await er.handle_notification_rules_put(_req({"rules": {"heartbeat/status": {"mode": "badge"}}}))
    await er.handle_notification_rules_put(_req({"rules": {"hook/fired": {"mode": "never"}}}))
    assert nr.resolve_rule("heartbeat", "status").mode == "badge", "second PUT dropped the first"
    assert nr.resolve_rule("hook", "fired").mode == "never"


@pytest.mark.asyncio
async def test_rules_put_rejects_unknown_kind(_isolate_rules):
    resp = await er.handle_notification_rules_put(_req({"rules": {"nope/nada": {"mode": "never"}}}))
    assert resp.status == 400
    assert "unknown notification kind" in (await _json(resp))["error"]


@pytest.mark.asyncio
async def test_rules_put_rejects_unknown_mode(_isolate_rules):
    """A typo'd mode would read back as the default — accept it and policy lies."""
    resp = await er.handle_notification_rules_put(_req({"rules": {"hook/fired": {"mode": "nevr"}}}))
    assert resp.status == 400


@pytest.mark.asyncio
async def test_rules_put_rejects_unknown_target(_isolate_rules):
    resp = await er.handle_notification_rules_put(
        _req({"rules": {"hook/fired": {"targets": ["hologram"]}}})
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_rules_put_rejects_verify_true_on_non_verifiable_kind(_isolate_rules):
    """INU-6: verify:true on a kind that carries no checkable claim is a 400, not a silent
    no-op the user would see 'saved' and never fire."""
    from personalclaw import notification_rules as nr

    resp = await er.handle_notification_rules_put(
        _req({"rules": {"heartbeat/status": {"verify": True}}})
    )
    assert resp.status == 400
    assert "not verifiable" in (await _json(resp))["error"]
    # And it must not have persisted.
    assert nr.resolve_rule("heartbeat", "status").verify is False


@pytest.mark.asyncio
async def test_rules_put_accepts_verify_on_a_verifiable_kind(_isolate_rules):
    from personalclaw import notification_rules as nr

    resp = await er.handle_notification_rules_put(
        _req({"rules": {"skills/proposal": {"verify": True}}})
    )
    assert resp.status == 200
    assert nr.resolve_rule("skills", "proposal").verify is True


@pytest.mark.asyncio
async def test_rules_put_accepts_a_known_sound(_isolate_rules):
    """MC-6: a voice from the closed set persists and takes effect on the read path."""
    from personalclaw import notification_rules as nr

    resp = await er.handle_notification_rules_put(
        _req({"rules": {"approval/requested": {"sound": "coin_blip"}}})
    )
    assert resp.status == 200
    assert nr.resolve_rule("approval", "requested").sound == "coin_blip"


@pytest.mark.asyncio
async def test_rules_put_rejects_an_unknown_sound(_isolate_rules):
    """A voice outside soundCues would 'save' and then hand the client an unplayable name."""
    from personalclaw import notification_rules as nr

    resp = await er.handle_notification_rules_put(
        _req({"rules": {"approval/requested": {"sound": "ka-ching"}}})
    )
    assert resp.status == 400
    assert "sound" in (await _json(resp))["error"]["message"]
    assert nr.resolve_rule("approval", "requested").sound is None


@pytest.mark.asyncio
async def test_rules_put_null_sound_clears_it(_isolate_rules):
    from personalclaw import notification_rules as nr

    await er.handle_notification_rules_put(
        _req({"rules": {"approval/requested": {"sound": "error"}}})
    )
    await er.handle_notification_rules_put(_req({"rules": {"approval/requested": {"sound": None}}}))
    assert nr.resolve_rule("approval", "requested").sound is None


@pytest.mark.asyncio
async def test_rules_put_merges_fields_within_a_key(_isolate_rules):
    """Two independent controls (mode, then sound) each save their OWN partial PUT — the second
    must not clobber the first, or the sound picker would silently reset the delivery mode."""
    from personalclaw import notification_rules as nr

    await er.handle_notification_rules_put(
        _req({"rules": {"approval/requested": {"mode": "badge"}}})
    )
    await er.handle_notification_rules_put(
        _req({"rules": {"approval/requested": {"sound": "coin_blip"}}})
    )
    rule = nr.resolve_rule("approval", "requested")
    assert rule.mode == "badge", "the sound PUT dropped the mode set a moment earlier"
    assert rule.sound == "coin_blip"


@pytest.mark.asyncio
async def test_rules_put_rejects_non_bool_verify(_isolate_rules):
    resp = await er.handle_notification_rules_put(
        _req({"rules": {"skills/proposal": {"verify": "yes"}}})
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_rules_put_allows_verify_false_on_any_kind(_isolate_rules):
    """verify:false is always a no-op opt-out — never rejected, even for a non-verifiable
    kind (the FE may send the whole row back)."""
    resp = await er.handle_notification_rules_put(
        _req({"rules": {"heartbeat/status": {"verify": False}}})
    )
    assert resp.status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"rules": ["hook/fired"]},
        {"rules": {"hook/fired": "never"}},
        {"rules": {"hook/fired": {"targets": "dashboard"}}},
        {"rules": {"hook/fired": {"conditions": []}}},
        {"rules": {"hook/fired": {"conditions": {"keywords": "deploy"}}}},
        {"rules": {"hook/fired": {"conditions": {"keywords": [1, 2]}}}},
        {"rules": {"hook/fired": {"conditions": {"name_mention": "yes"}}}},
        {"digest": []},
        {"digest": {"schedule": "not a cron"}},
        {"digest": {"schedule": 8}},
    ],
)
async def test_rules_put_rejects_malformed_shapes(_isolate_rules, bad):
    resp = await er.handle_notification_rules_put(_req(bad))
    assert resp.status == 400, f"should have rejected {bad!r}"


@pytest.mark.asyncio
async def test_rules_put_rejects_non_object_body(_isolate_rules):
    resp = await er.handle_notification_rules_put(_req(["not", "an", "object"]))
    assert resp.status == 400


@pytest.mark.asyncio
async def test_rules_put_persists_a_valid_digest_schedule(_isolate_rules):
    from personalclaw import notification_rules as nr

    resp = await er.handle_notification_rules_put(_req({"digest": {"schedule": "0 7 * * 1-5"}}))
    assert resp.status == 200
    assert nr.digest_settings()["schedule"] == "0 7 * * 1-5"


@pytest.mark.asyncio
async def test_rules_put_stores_conditions_that_escalate(_isolate_rules):
    from personalclaw import notification_rules as nr

    await er.handle_notification_rules_put(
        _req(
            {
                "rules": {
                    "hook/fired": {
                        "mode": "badge",
                        "conditions": {"keywords": ["deploy"], "name_mention": True},
                    }
                }
            }
        )
    )
    rule = nr.resolve_rule("hook", "fired")
    assert rule.mode == "badge"
    assert rule.conditions.matches("please deploy now") == "keyword: deploy"
    assert rule.escalated().mode == "immediate"


@pytest.mark.asyncio
async def test_inbox_put_no_longer_accepts_the_retired_alert_fields():
    """The alert fields moved to notification rules (plan 42 S3).

    They are absent from INBOX_DEFAULTS, which is the authoritative allowlist, so a PUT
    naming them must NOT persist them — otherwise a client written against the old API
    would keep writing values into a store nothing reads, and the user would think their
    alerts were configured.
    """
    resp = await er.handle_inbox_settings_put(
        _req({"alert_keywords": ["urgent"], "alert_on_name_mention": True, "retention_days": 30})
    )
    settings = (await _json(resp))["settings"]
    assert "alert_keywords" not in settings
    assert "alert_on_name_mention" not in settings
    assert settings["retention_days"] == 30, "the known key still applies"


@pytest.mark.asyncio
async def test_legacy_alert_fields_are_readable_for_the_backfill(tmp_path, monkeypatch):
    """The backfill needs the RAW values even though load_inbox_settings() drops them."""
    import json

    monkeypatch.setattr(er, "_entity_settings_path", lambda entity: tmp_path / f"{entity}.json")
    (tmp_path / "inbox.json").write_text(
        json.dumps({"alert_keywords": ["deploy"], "alert_on_name_mention": True}), encoding="utf-8"
    )
    assert er.legacy_inbox_alert_fields() == {
        "alert_keywords": ["deploy"],
        "alert_on_name_mention": True,
    }
    # …and the public read path no longer surfaces them.
    assert "alert_keywords" not in er.load_inbox_settings()


@pytest.mark.asyncio
async def test_legacy_alert_read_is_empty_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(er, "_entity_settings_path", lambda entity: tmp_path / f"{entity}.json")
    assert er.legacy_inbox_alert_fields() == {}
