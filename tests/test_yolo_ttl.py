"""YOLO time-limited override: TTL expiry is honored everywhere (no bypass).

The dashboard YOLO already auto-expires after a TTL ceiling; these guard that
(a) is_yolo_active() flips false once the TTL passes, (b) yolo_remaining_secs()
reports the countdown, and (c) config-driven YOLO is permanent.
"""

from __future__ import annotations

import time

from chat_test_helpers import _make_state


def test_yolo_active_then_expires(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    base = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: base)
    state.enable_yolo()
    assert state.is_yolo_active() is True
    assert state.yolo_remaining_secs() == state._YOLO_TTL

    # Just before the ceiling — still active.
    monkeypatch.setattr(time, "monotonic", lambda: base + state._YOLO_TTL - 1)
    assert state.is_yolo_active() is True
    assert 0 < state.yolo_remaining_secs() <= state._YOLO_TTL

    # Past the ceiling — auto-expires on read (check-on-use).
    monkeypatch.setattr(time, "monotonic", lambda: base + state._YOLO_TTL + 1)
    assert state.is_yolo_active() is False
    assert state.yolo_remaining_secs() is None


def test_config_yolo_is_permanent(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    base = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: base)
    state.enable_yolo(from_config=True)
    # Far past any TTL — config YOLO never expires, and has no countdown.
    monkeypatch.setattr(time, "monotonic", lambda: base + state._YOLO_TTL * 100)
    assert state.is_yolo_active() is True
    assert state.yolo_remaining_secs() is None


def test_disable_clears_remaining(tmp_path, monkeypatch):
    state = _make_state(tmp_path)
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    state.enable_yolo()
    assert state.yolo_remaining_secs() is not None
    state.disable_yolo()
    assert state.is_yolo_active() is False
    assert state.yolo_remaining_secs() is None
