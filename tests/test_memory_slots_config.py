"""The Slots block budget: `memory.slot_size_cap`'s four-point wiring + its clamp (MGAV-9).

MGAV-8 shipped slots with the block ceiling as a code constant. MGAV-9's `done_when` requires
"slot caps" reachable from the settings tab through the `_EDITABLE_CONFIG` PATCH allowlist, so
the number became a config field — which means it now has to survive the round trip AND stay
inside a range config alone cannot leave. Both properties are asserted here because either one
alone is a hole: a field that round-trips but is unbounded is an unbounded always-injected
block, and a clamp nobody reads is decoration.
"""

from __future__ import annotations

import json

import pytest

from personalclaw import memory_slots
from personalclaw.config.loader import AppConfig


@pytest.fixture()
def _cfg(tmp_path, monkeypatch):
    """An isolated config dir — never the real home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader._config_cache", None, raising=False)
    return tmp_path


def _write(cfg_dir, memory: dict) -> None:
    (cfg_dir / "config.json").write_text(json.dumps({"memory": memory}), encoding="utf-8")


def test_default_matches_the_module_block_ceiling():
    """The dataclass default IS `SLOTS_BLOCK_MAX_CHARS`.

    They are two literals in two files (loader must not import the memory subsystem at import
    time), so nothing but this test stops them drifting — and a drift would mean the default
    config silently changed what every session's Slots block costs.
    """
    assert AppConfig().memory.slot_size_cap == memory_slots.SLOTS_BLOCK_MAX_CHARS


def test_slot_size_cap_survives_the_round_trip(_cfg):
    """load() maps it through, and to_dict() carries it back out.

    The `load()` half is the bug class the MemoryConfig comment in loader.py records: an
    explicit field-by-field mapping that omits a field drops it silently, so the saved value
    reads back as the default and the setting looks broken with no error anywhere.
    """
    _write(_cfg, {"slot_size_cap": 900})
    cfg = AppConfig.load()
    assert cfg.memory.slot_size_cap == 900
    assert cfg.to_dict()["memory"]["slot_size_cap"] == 900


def test_an_unreadable_value_falls_back_instead_of_raising(_cfg):
    """A hand-edited garbage value must not stop a session from starting."""
    _write(_cfg, {"slot_size_cap": "lots"})
    # load() coerces; a non-numeric string is a ValueError there, so the CLAMP is what has to
    # tolerate it — that is the layer the renderer actually calls.
    assert memory_slots.resolve_block_limit("lots") == memory_slots.SLOTS_BLOCK_MAX_CHARS
    assert memory_slots.resolve_block_limit(None) == memory_slots.SLOTS_BLOCK_MAX_CHARS


def test_config_cannot_widen_the_block_past_the_hard_ceiling():
    """The whole point of the clamp: editing config.json cannot buy unbounded context.

    Mirrors `memory_service.HARD_CAP_RECORDS` — a limit enforced at the consumer, not at the
    write boundary, so a value that reached the field by ANY route (a hand edit, a CLI, a test
    building MemoryConfig directly) still cannot exceed it.
    """
    assert memory_slots.resolve_block_limit(1_000_000) == memory_slots.SLOTS_BLOCK_HARD_MAX_CHARS
    assert memory_slots.resolve_block_limit(1) == memory_slots.SLOTS_BLOCK_MIN_CHARS


def test_the_editable_allowlist_bounds_match_the_clamp():
    """The PATCH allowlist and the consumer agree on the range.

    Two different numbers would mean the UI offers a value the renderer silently rejects (or
    refuses one it would have honoured) — the split-brain that makes a knob untrustworthy.
    """
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    spec = _EDITABLE_CONFIG["memory.slot_size_cap"]
    assert spec["type"] == "int"
    assert spec["min"] == memory_slots.SLOTS_BLOCK_MIN_CHARS
    assert spec["max"] == memory_slots.SLOTS_BLOCK_HARD_MAX_CHARS


class _FakeSlotStore:
    """The narrow slice `memory_slots` needs — no sqlite, no FAISS (its Protocol's whole point)."""

    def __init__(self):
        self.rows: dict[str, object] = {}

    def get_semantic(self, key):
        return {"value": self.rows[key]} if key in self.rows else None

    def set_semantic(self, key, value, confidence, source):
        self.rows[key] = value
        return None


def _stuffed_store() -> _FakeSlotStore:
    """A store holding enough slot text that a 250-char budget MUST truncate."""
    store = _FakeSlotStore()
    memory_slots.append(store, "pending_items", "x" * 300, source="user_explicit")
    memory_slots.append(store, "persona", "y" * 300, source="user_explicit")
    return store


def test_the_limit_argument_bounds_the_rendered_block():
    """The primitive honours its own `limit`. Necessary, and NOT sufficient — see below."""
    store = _stuffed_store()
    generous = memory_slots.render_slots_block(store, limit=1400)
    tight = memory_slots.render_slots_block(store, limit=memory_slots.resolve_block_limit(250))
    assert len(generous) > len(tight)
    assert len(tight) <= 250
    assert "[slots truncated]" in tight


def test_the_session_builder_actually_READS_the_configured_budget(_cfg, monkeypatch):
    """The CALL SITE, not the primitive — the difference that decides whether the knob is real.

    Measured: with only the test above, deleting `limit=` from `ContextBuilder._slots_block`
    left the whole suite GREEN. That is the repo's recurring shape — a field wired through all
    four config points, exercised by a test that calls the primitive directly, and inert in the
    one place a user's setting was supposed to reach. So this drives the consumer itself and
    compares against the SAME store rendered at the default, which is what makes it fail when
    the argument goes missing rather than when the primitive changes.
    """
    from typing import Any, cast

    from personalclaw.context import ContextBuilder

    store = _stuffed_store()
    # `_slots_block` never touches `self` (it is a namespaced helper); calling it unbound keeps
    # the test off ContextBuilder's constructor, which would pull in skills + a real home.
    render = cast(Any, ContextBuilder._slots_block)

    _write(_cfg, {"slot_size_cap": 250})
    tight = render(None, store)
    _write(_cfg, {"slot_size_cap": 1400})
    generous = render(None, store)

    assert tight, "the builder must still render a block, just a smaller one"
    assert len(tight) <= 250, "the configured budget did not reach render_slots_block"
    assert len(generous) > len(tight), "changing the config changed nothing — the knob is inert"
