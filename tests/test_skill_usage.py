"""Skill-use counter — sidecar usage store (skill-use-counter, #25)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from personalclaw.skills.usage import SkillUsage, SkillUsageStore

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> SkillUsageStore:
    return SkillUsageStore(path=tmp_path / ".usage.json")


def test_unrecorded_skill_is_zero(store: SkillUsageStore):
    u = store.get("auto/foo")
    assert u == SkillUsage(count=0, last_used_at="")


def test_record_use_increments_and_stamps(store: SkillUsageStore):
    assert store.record_use("auto/foo", now=NOW) == 1
    assert store.record_use("auto/foo", now=NOW) == 2
    u = store.get("auto/foo")
    assert u.count == 2
    assert u.last_used_at == NOW.isoformat(timespec="seconds")


def test_record_uses_batch_dedups(store: SkillUsageStore):
    store.record_uses(["a", "b", "a"], now=NOW)  # 'a' twice in one turn → +1
    assert store.get("a").count == 1
    assert store.get("b").count == 1


def test_record_uses_empty_is_noop(store: SkillUsageStore):
    store.record_uses([], now=NOW)
    store.record_uses(["", None], now=NOW)  # type: ignore[list-item]
    assert store.all_usage() == {}


def test_all_usage_roundtrip(store: SkillUsageStore):
    store.record_use("x", now=NOW)
    store.record_use("y", now=NOW)
    store.record_use("y", now=NOW)
    allu = store.all_usage()
    assert allu["x"].count == 1
    assert allu["y"].count == 2


def test_counts_persist_across_instances(tmp_path: Path):
    p = tmp_path / ".usage.json"
    SkillUsageStore(path=p).record_use("auto/foo", now=NOW)
    # a fresh instance reads the same file
    assert SkillUsageStore(path=p).get("auto/foo").count == 1


def test_prune_drops_missing_skills(store: SkillUsageStore):
    store.record_uses(["keep", "drop"], now=NOW)
    store.prune(keep={"keep"})
    assert store.get("keep").count == 1
    assert store.get("drop").count == 0
    assert set(store.all_usage()) == {"keep"}


def test_corrupt_file_degrades_to_empty(tmp_path: Path):
    p = tmp_path / ".usage.json"
    p.write_text("{ this is not json", encoding="utf-8")
    store = SkillUsageStore(path=p)
    assert store.all_usage() == {}
    # and a record still works (overwrites the garbage)
    assert store.record_use("a", now=NOW) == 1


def test_missing_file_is_empty(tmp_path: Path):
    store = SkillUsageStore(path=tmp_path / "does-not-exist.json")
    assert store.get("anything") == SkillUsage()
    assert store.all_usage() == {}
