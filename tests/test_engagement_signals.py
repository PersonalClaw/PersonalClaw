"""Tests for EngagementStore (P11 engagement-weighted ranking, store + kernel).

Pure/read-time decay, additive accumulation, dismiss-floors, warm-up neutral, persistence
round-trip. Injects an explicit tmp path (no config_dir monkeypatch needed — the store
takes a path) so it never pollutes ~/.personalclaw."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from personalclaw.engagement_signals import (
    _NEUTRAL,
    _WEIGHT_FLOOR,
    DEFAULT_HALF_LIFE_DAYS,
    EngagementStore,
    rank_by_engagement,
)
from personalclaw.preference_facets import decay

_DAY = 86400.0


def _store(tmp_path):
    return EngagementStore(path=tmp_path / "engagement.json")


def test_shared_decay_kernel_halves_at_half_life():
    # The extracted kernel both facets + engagement share: value halves after one half-life.
    assert decay(1.0, DEFAULT_HALF_LIFE_DAYS, DEFAULT_HALF_LIFE_DAYS) == 0.5
    assert decay(1.0, 0.0, DEFAULT_HALF_LIFE_DAYS) == 1.0
    assert decay(1.0, 5.0, 0.0) == 1.0  # non-positive half-life → no decay (guard)
    assert (
        decay(0.8, DEFAULT_HALF_LIFE_DAYS * 2, DEFAULT_HALF_LIFE_DAYS) == 0.2
    )  # two half-lives → /4


def test_warmup_neutral_until_threshold(tmp_path):
    s = _store(tmp_path)
    # one signal → still warming up → neutral (cold topic ranks on pure recency)
    s.record("chan:A", "favorite", now=0.0)
    assert s.weight_for("chan:A", now=0.0) == _NEUTRAL
    # unknown topic → neutral
    assert s.weight_for("never:seen", now=0.0) == _NEUTRAL


def test_favorite_boosts_after_warmup(tmp_path):
    s = _store(tmp_path)
    s.record("chan:A", "favorite", now=0.0)
    s.record("chan:A", "open", now=0.0)  # 2 signals → warmed up
    w = s.weight_for("chan:A", now=0.0)
    assert w > _NEUTRAL  # engaged topic is boosted above neutral


def test_dismiss_floors_never_buries(tmp_path):
    s = _store(tmp_path)
    for i in range(6):  # pile on dismisses
        s.record("chan:spam", "dismiss", now=0.0)
    w = s.weight_for("chan:spam", now=0.0)
    assert w >= _WEIGHT_FLOOR  # floored — still surfaces, just lower
    assert w < _NEUTRAL  # but demoted below neutral


def test_boost_decays_back_toward_neutral(tmp_path):
    s = _store(tmp_path)
    s.record("chan:A", "favorite", now=0.0)
    s.record("chan:A", "favorite", now=0.0)  # warmed + boosted
    fresh = s.weight_for("chan:A", now=0.0)
    aged = s.weight_for("chan:A", now=DEFAULT_HALF_LIFE_DAYS * _DAY)  # one half-life later
    assert _NEUTRAL < aged < fresh  # deviation halved toward neutral, not gone


def test_persistence_round_trip(tmp_path):
    s = _store(tmp_path)
    s.record("chan:A", "favorite", now=0.0)
    s.record("chan:A", "reply", now=0.0)
    s.save()
    s2 = _store(tmp_path)
    s2.load()
    assert s2.weight_for("chan:A", now=0.0) == s.weight_for("chan:A", now=0.0)


def test_prune_drops_decayed_to_neutral(tmp_path):
    s = _store(tmp_path)
    s.record("chan:A", "favorite", now=0.0)
    s.record("chan:A", "open", now=0.0)
    # far in the future the boost has fully decayed to ~neutral → prunable
    pruned = s.prune(now=DEFAULT_HALF_LIFE_DAYS * 20 * _DAY)
    assert pruned == 1
    assert s.weight_for("chan:A", now=0.0) == _NEUTRAL  # gone → neutral again


# ── rank_by_engagement: the shared blend both consumers use ───────────────────


def test_rank_engaged_channel_above_equally_recent(tmp_path):
    s = _store(tmp_path)
    for _ in range(3):  # warm + boost chan:hot
        s.record("hot", "favorite", now=0.0)
    # two items with IDENTICAL recency but different topics
    items = [{"id": "cold", "r": 100.0, "t": "cold"}, {"id": "hot", "r": 100.0, "t": "hot"}]
    ranked = rank_by_engagement(
        items, recency_key=lambda i: i["r"], topic_key=lambda i: i["t"], store=s, now=0.0
    )
    assert ranked[0]["id"] == "hot"  # engaged topic wins the tie-break


def test_rank_cold_start_is_pure_recency(tmp_path):
    s = _store(tmp_path)  # empty store → all weights neutral
    items = [{"id": "old", "r": 10.0, "t": "a"}, {"id": "new", "r": 99.0, "t": "b"}]
    ranked = rank_by_engagement(
        items, recency_key=lambda i: i["r"], topic_key=lambda i: i["t"], store=s, now=0.0
    )
    assert [i["id"] for i in ranked] == ["new", "old"]  # degrades to recency order


def test_rank_dismissed_topic_demoted_but_present(tmp_path):
    s = _store(tmp_path)
    for _ in range(4):
        s.record("spam", "dismiss", now=0.0)  # floored, demoted
    items = [{"id": "spam", "r": 100.0, "t": "spam"}, {"id": "ok", "r": 60.0, "t": "ok"}]
    ranked = rank_by_engagement(
        items, recency_key=lambda i: i["r"], topic_key=lambda i: i["t"], store=s, now=0.0
    )
    ids = [i["id"] for i in ranked]
    assert "spam" in ids  # never buried (floored, still ranked)
    # 100 × floor(~0.6) = ~60 vs 60 × 1.0 = 60 → spam demoted to a tie/below despite newer


# ── P11 inbox HANDLER wiring (the consumer that layers on the store) ─────────────
# These exercise the handler helpers directly (no HTTP): the gated re-rank + topic-key
# derivation + signal capture. The flag is read via AppConfig.load() (DashboardState has
# no .config), so we patch _inbox_config to toggle it without touching config.json.


def _item(id, channel, sender, created_at, classification="needs_reply"):
    return SimpleNamespace(
        id=id,
        channel=channel,
        sender_id=sender,
        classification=classification,
        created_at=created_at,
    )


def _fake_state(tmp_path):
    from personalclaw.engagement_signals import EngagementStore

    st = SimpleNamespace()
    st._engagement_store = EngagementStore(path=tmp_path / "engagement.json")
    return st


def test_topic_keys_are_channel_sender_classification():
    from personalclaw.dashboard.handlers_inbox import _topic_keys

    keys = _topic_keys(_item("i1", "C1", "U1", 5.0, "needs_reply"))
    assert keys == ["ch:C1", "snd:U1", "cls:needs_reply"]
    # blank fields are dropped (no empty topic keys)
    assert _topic_keys(_item("i2", "", "", 1.0, "")) == []


def test_rank_items_off_is_pure_recency(tmp_path):
    from personalclaw.dashboard.handlers_inbox import _rank_items

    st = _fake_state(tmp_path)
    items = [_item("old", "C1", "U1", 10.0), _item("new", "C2", "U2", 99.0)]
    with patch(
        "personalclaw.dashboard.handlers_inbox._inbox_config",
        return_value=SimpleNamespace(
            engagement_ranking_enabled=False, engagement_half_life_days=0.0
        ),
    ):
        ranked = _rank_items(st, items)
    assert [i.id for i in ranked] == ["new", "old"]  # unchanged baseline


def test_rank_items_on_boosts_engaged_channel(tmp_path):
    from personalclaw.dashboard.handlers_inbox import _rank_items, _record_signal

    st = _fake_state(tmp_path)
    on = SimpleNamespace(engagement_ranking_enabled=True, engagement_half_life_days=0.0)
    with patch("personalclaw.dashboard.handlers_inbox._inbox_config", return_value=on):
        # favorite the OLDER item's channel a few times → its topic outweighs recency
        hot = _item("hot", "C-hot", "U1", 50.0)
        for _ in range(3):
            _record_signal(st, hot, "favorite")
        items = [_item("cold", "C-cold", "U2", 60.0), hot]  # cold is newer
        ranked = _rank_items(st, items)
    assert ranked[0].id == "hot"  # engagement lifts the older-but-engaged item to the top


def test_record_signal_noop_when_disabled(tmp_path):
    from personalclaw.dashboard.handlers_inbox import _record_signal

    st = _fake_state(tmp_path)
    off = SimpleNamespace(engagement_ranking_enabled=False, engagement_half_life_days=0.0)
    with patch("personalclaw.dashboard.handlers_inbox._inbox_config", return_value=off):
        _record_signal(st, _item("i", "C1", "U1", 1.0), "favorite")
    # nothing accrued (we don't collect state the user hasn't opted into)
    assert st._engagement_store.weight_for("ch:C1", now=0.0) == _NEUTRAL
