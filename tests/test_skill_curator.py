"""Skill-library curator — auto/ lifecycle aging (#27)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from personalclaw.skills.curator import STATE_STALE, CuratorReport, is_archived, restore, run_aging
from personalclaw.skills.loader import SkillsLoader

NOW = datetime(2026, 6, 15, tzinfo=timezone.utc)


@pytest.fixture
def loader(tmp_path, monkeypatch) -> SkillsLoader:
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: skills)
    return SkillsLoader(skills_path=skills, install_builtins=False)


def _write_auto(loader: SkillsLoader, slug: str, *, created_at: str, pinned=False, status=""):
    name = f"auto/{slug}"
    fm = [
        "---",
        f"name: {name}",
        "description: a test skill",
        "source: auto",
        f"created_at: {created_at}",
    ]
    if pinned:
        fm.append("pinned: true")
    if status:
        fm.append(f"status: {status}")
    fm.append("---")
    (loader._dir / name).mkdir(parents=True)
    (loader._dir / name / "SKILL.md").write_text("\n".join(fm) + "\n\n# body\n")
    return name


def _stub_usage(monkeypatch, mapping: dict[str, str]):
    """mapping: {name: last_used_at_iso}."""

    class _U:
        def __init__(self, ts):
            self.last_used_at = ts
            self.count = 1

    class _Store:
        def all_usage(self):
            return {k: _U(v) for k, v in mapping.items()}

    monkeypatch.setattr("personalclaw.skills.usage.SkillUsageStore", lambda: _Store())


# ── aging transitions ──


def test_fresh_skill_stays_active(loader, monkeypatch):
    _write_auto(loader, "fresh", created_at=(NOW - timedelta(days=5)).isoformat())
    _stub_usage(monkeypatch, {"auto/fresh": (NOW - timedelta(days=2)).isoformat()})
    report = run_aging(loader, now=NOW)
    assert report.scanned == 1
    assert report.changed == 0


def test_unused_30d_goes_stale(loader, monkeypatch):
    _write_auto(loader, "s", created_at=(NOW - timedelta(days=100)).isoformat())
    _stub_usage(monkeypatch, {"auto/s": (NOW - timedelta(days=40)).isoformat()})
    report = run_aging(loader, now=NOW)
    assert report.to_stale == ["auto/s"]
    assert loader.list_skills()[0]["status"] == STATE_STALE


def test_unused_90d_archived(loader, monkeypatch):
    _write_auto(loader, "a", created_at=(NOW - timedelta(days=200)).isoformat())
    _stub_usage(monkeypatch, {"auto/a": (NOW - timedelta(days=120)).isoformat()})
    report = run_aging(loader, now=NOW)
    assert report.to_archived == ["auto/a"]
    assert is_archived(loader._cached_frontmatter(loader._dir / "auto/a" / "SKILL.md"))


def test_never_used_ages_by_created_at(loader, monkeypatch):
    _write_auto(loader, "old", created_at=(NOW - timedelta(days=200)).isoformat())
    _stub_usage(monkeypatch, {})  # never used
    report = run_aging(loader, now=NOW)
    assert report.to_archived == ["auto/old"]


def test_reactivation_when_used_again(loader, monkeypatch):
    # currently archived, but used yesterday → back to active
    _write_auto(loader, "r", created_at=(NOW - timedelta(days=200)).isoformat(), status="archived")
    _stub_usage(monkeypatch, {"auto/r": (NOW - timedelta(days=1)).isoformat()})
    report = run_aging(loader, now=NOW)
    assert report.reactivated == ["auto/r"]
    assert loader.list_skills()[0]["status"] == "active"


def test_pinned_is_skipped(loader, monkeypatch):
    _write_auto(loader, "p", created_at=(NOW - timedelta(days=200)).isoformat(), pinned=True)
    _stub_usage(monkeypatch, {"auto/p": (NOW - timedelta(days=200)).isoformat()})
    report = run_aging(loader, now=NOW)
    assert report.skipped_pinned == ["auto/p"]
    assert report.changed == 0


def test_dry_run_does_not_write(loader, monkeypatch):
    _write_auto(loader, "d", created_at=(NOW - timedelta(days=200)).isoformat())
    _stub_usage(monkeypatch, {"auto/d": (NOW - timedelta(days=120)).isoformat()})
    report = run_aging(loader, now=NOW, dry_run=True)
    assert report.to_archived == ["auto/d"]
    assert loader.list_skills()[0]["status"] == "active"  # unchanged on disk


def test_idempotent(loader, monkeypatch):
    _write_auto(loader, "i", created_at=(NOW - timedelta(days=200)).isoformat())
    _stub_usage(monkeypatch, {"auto/i": (NOW - timedelta(days=120)).isoformat()})
    run_aging(loader, now=NOW)
    report2 = run_aging(loader, now=NOW)
    assert report2.changed == 0  # already archived → no further change


# ── invariants ──


def test_non_auto_skills_untouched(loader, monkeypatch):
    # a hand-authored skill (no auto/ prefix) — must never be aged
    (loader._dir / "manual").mkdir()
    (loader._dir / "manual" / "SKILL.md").write_text(
        "---\nname: manual\ndescription: hand-authored\n---\n# x\n"
    )
    _stub_usage(monkeypatch, {})
    report = run_aging(loader, now=NOW)
    assert report.scanned == 0  # manual/ not scanned


def test_restore_reactivates(loader):
    name = _write_auto(loader, "x", created_at=NOW.isoformat(), status="archived")
    assert restore(loader, name) is True
    assert loader.list_skills()[0]["status"] == "active"


def test_restore_refuses_non_auto(loader):
    assert restore(loader, "manual/x") is False


# ── report ──


def test_report_summary_no_changes():
    r = CuratorReport(scanned=3)
    assert "no changes" in r.summary()


def test_report_summary_dry_run():
    r = CuratorReport(scanned=5, to_archived=["a"], dry_run=True)
    assert "dry-run" in r.summary()
    assert "archived 1" in r.summary()
