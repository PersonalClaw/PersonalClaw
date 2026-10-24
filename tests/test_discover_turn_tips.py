"""Regression tests for the in-turn Discover tip cadence (#5098)."""

from __future__ import annotations

import pytest

from personalclaw.legibility import discover


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Point entity settings at a temp dir and enable Discover."""
    store: dict[str, dict] = {}

    monkeypatch.setattr(
        "personalclaw.providers.entity_routes._load_entity_settings",
        lambda entity: store.get(entity),
    )
    monkeypatch.setattr(
        "personalclaw.providers.entity_routes._save_entity_settings",
        lambda entity, data: store.__setitem__(entity, data),
    )
    monkeypatch.setattr(discover, "compute_engaged", lambda state=None: {})

    class _Legibility:
        discover_tips = True

    class _Config:
        legibility = _Legibility()

    monkeypatch.setattr(
        "personalclaw.config.loader.AppConfig.load",
        classmethod(lambda cls: _Config()),
    )
    return store


def test_turn_tip_shows_after_onboarding_turns():
    tip = discover.select_turn_tip(today="2026-09-22", turn_index=10)
    assert tip is not None
    assert tip.id in discover.TIP_IDS


def test_no_turn_tip_in_first_five_turns():
    assert discover.select_turn_tip(today="2026-09-22", turn_index=4) is None


def test_fourth_long_turn_in_a_day_shows_none():
    for _ in range(3):
        assert discover.select_turn_tip(today="2026-09-22", turn_index=10) is not None
        discover.record_turn_tip(today="2026-09-22")
    assert discover.select_turn_tip(today="2026-09-22", turn_index=10) is None


def test_dismissed_tip_never_returns_in_a_turn():
    tip = discover.select_turn_tip(today="2026-09-22", turn_index=10)
    assert tip is not None
    discover.dismiss(tip.id)
    again = discover.select_turn_tip(today="2026-09-22", turn_index=10)
    assert again is None or again.id != tip.id
