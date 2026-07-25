"""Per-tool invocation counter — the §6 power-ups 'touched' surface.

Mirrors ``test_skill_usage.py``: the sidecar JSON store is best-effort and
advisory (it only decides which untouched capability the dashboard proposes),
so it degrades to empty on corruption/missing and never raises on a bad write.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from personalclaw.legibility.tool_usage import ToolUsage, ToolUsageStore

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> ToolUsageStore:
    return ToolUsageStore(path=tmp_path / "tool_usage.json")


def test_unrecorded_tool_is_zero(store: ToolUsageStore):
    assert store.get("knowledge_add") == ToolUsage(count=0, last_used_at="")


def test_record_use_increments_and_stamps(store: ToolUsageStore):
    assert store.record_use("knowledge_add", now=NOW) == 1
    assert store.record_use("knowledge_add", now=NOW) == 2
    u = store.get("knowledge_add")
    assert u.count == 2
    assert u.last_used_at == NOW.isoformat(timespec="seconds")


def test_empty_name_is_noop(store: ToolUsageStore):
    assert store.record_use("", now=NOW) == 0
    assert store.all_usage() == {}


def test_used_names_only_nonzero(store: ToolUsageStore):
    store.record_use("a", now=NOW)
    store.record_use("b", now=NOW)
    assert store.used_names() == {"a", "b"}


def test_all_usage_roundtrip(store: ToolUsageStore):
    store.record_use("x", now=NOW)
    store.record_use("y", now=NOW)
    store.record_use("y", now=NOW)
    allu = store.all_usage()
    assert allu["x"].count == 1
    assert allu["y"].count == 2


def test_counts_persist_across_instances(tmp_path: Path):
    p = tmp_path / "tool_usage.json"
    ToolUsageStore(path=p).record_use("knowledge_add", now=NOW)
    assert ToolUsageStore(path=p).get("knowledge_add").count == 1


def test_corrupt_file_degrades_to_empty(tmp_path: Path):
    p = tmp_path / "tool_usage.json"
    p.write_text("{ not valid json", encoding="utf-8")
    store = ToolUsageStore(path=p)
    assert store.all_usage() == {}
    assert store.used_names() == set()
    # a record still works — it overwrites the garbage
    assert store.record_use("a", now=NOW) == 1


def test_missing_file_is_empty(tmp_path: Path):
    store = ToolUsageStore(path=tmp_path / "does-not-exist.json")
    assert store.get("anything") == ToolUsage()
    assert store.all_usage() == {}
    assert store.used_names() == set()


def test_defaults_to_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """With no explicit path, the store lives under config_dir()/tool_usage.json."""
    monkeypatch.setattr("personalclaw.legibility.tool_usage.config_dir", lambda: tmp_path)
    store = ToolUsageStore()
    store.record_use("t", now=NOW)
    assert (tmp_path / "tool_usage.json").exists()
