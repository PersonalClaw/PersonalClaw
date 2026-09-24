"""Active-job tracker + mid-turn origin/debounce tests (PLATFORM-RESILIENCE §6).

The tracker is bookkeeping over turn lifecycle, and the cancel-and-replace decision
hinges on two pure predicates — origin classification (which turns are interactive
and thus cancellable) and the debounce guard (a burst produces ONE cancel). Both are
pinned here.
"""

from __future__ import annotations

import pytest

from personalclaw.resilience.active_jobs import (
    ActiveJobTracker,
    classify_origin,
    is_cancellable_origin,
)

# ── origin classification (the eligibility guard's input) ────────────────────


@pytest.mark.parametrize(
    "key,expected",
    [
        ("dashboard:abc123", "webui"),
        ("dashboard", "webui"),
        ("my-chat", "webui"),  # bare interactive session
        ("loop-42", "loop"),
        ("loop-plan-7", "loop"),
        ("cron:daily-digest", "cron"),
        ("cron:job:agent", "cron"),
        ("subagent:xyz", "subagent"),
        ("_bg", "heartbeat"),
    ],
)
def test_classify_origin(key, expected):
    assert classify_origin(key) == expected


def test_only_interactive_origins_are_cancellable():
    assert is_cancellable_origin("webui") is True
    assert is_cancellable_origin("channel:slack") is True
    for unattended in ("loop", "cron", "subagent", "heartbeat", "other"):
        assert is_cancellable_origin(unattended) is False, unattended


# ── tracker lifecycle ────────────────────────────────────────────────────────


def test_register_clear_roundtrip():
    t = ActiveJobTracker()
    assert t.get("s1") is None
    job = t.register("s1", now=100.0)
    assert job.origin == "webui" and job.started_at == 100.0
    assert t.get("s1") is job
    t.clear("s1")
    assert t.get("s1") is None
    t.clear("s1")  # idempotent


def test_origin_override_for_channel():
    t = ActiveJobTracker()
    job = t.register("some-key", origin="channel:slack", now=1.0)
    assert job.origin == "channel:slack" and job.interactive is True


def test_active_and_interactive_count():
    t = ActiveJobTracker()
    t.register("dashboard:a", now=1.0)
    t.register("loop-1", now=2.0)
    t.register("cron:x", now=3.0)
    assert len(t.active()) == 3
    assert t.interactive_count() == 1  # only the dashboard turn is interactive


# ── debounce guard (§6.3.5) ──────────────────────────────────────────────────


def test_debounce_blocks_within_window_then_allows_after():
    t = ActiveJobTracker()
    # No prior cancel → not within debounce.
    assert t.within_debounce("s1", 2.0, now=100.0) is False
    t.mark_cancel("s1", now=100.0)
    # 1s later, inside the 2s window → coalesce.
    assert t.within_debounce("s1", 2.0, now=101.0) is True
    # 2.5s later, past the window → allowed again.
    assert t.within_debounce("s1", 2.0, now=102.5) is False


def test_debounce_is_per_session():
    t = ActiveJobTracker()
    t.mark_cancel("s1", now=100.0)
    # A different session is unaffected by s1's cancel.
    assert t.within_debounce("s2", 2.0, now=100.5) is False


def test_zero_interval_never_debounces():
    t = ActiveJobTracker()
    t.mark_cancel("s1", now=100.0)
    assert t.within_debounce("s1", 0.0, now=100.0) is False


# ── config policy resolution ─────────────────────────────────────────────────


def test_mid_turn_policy_defaults_to_queue(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load().resilience
    assert cfg.mid_turn_policy == "queue"  # safe default: never cancel unless opted in
    assert cfg.cancel_replace_min_interval_secs == 2.0


def test_mid_turn_policy_invalid_falls_back_to_queue(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"resilience": {"mid_turn_policy": "bogus"}}), encoding="utf-8"
    )
    from personalclaw.config.loader import AppConfig

    assert AppConfig.load().resilience.mid_turn_policy == "queue"


def test_mid_turn_policy_cancel_and_replace_honored(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"resilience": {"mid_turn_policy": "cancel_and_replace"}}), encoding="utf-8"
    )
    from personalclaw.config.loader import AppConfig

    assert AppConfig.load().resilience.mid_turn_policy == "cancel_and_replace"
