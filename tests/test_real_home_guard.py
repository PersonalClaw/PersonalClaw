"""The real-home rail must be able to FAIL (CRE-8).

Every assertion here drives ``real_home_guard`` against a throwaway root under
``tmp_path``. Nothing in this file writes to the developer's real
``~/.personalclaw`` — proving a leak detector works by leaking is not a proof, it
is the defect the detector exists to prevent.
"""

import os
import time
from pathlib import Path

import real_home_guard
from real_home_guard import HomeChange, format_report, scan_changes


def _fake_home(tmp_path: Path) -> Path:
    """A stand-in 'real home' with some pre-existing content the rail must ignore."""
    root = tmp_path / "fake_home" / ".personalclaw"
    (root / "sessions").mkdir(parents=True)
    (root / "security_events.jsonl").write_text('{"pre":"existing"}\n')
    (root / "sessions" / "s1.json").write_text("{}\n")
    return root


def _armed(root: Path) -> int:
    """Mimic pytest_sessionstart: take the timestamp, then let mtimes advance."""
    since = time.time_ns()
    # Filesystem mtimes must be strictly newer than the arm point for the writes
    # below; a short sleep keeps this honest on coarse-grained filesystems.
    time.sleep(0.01)
    return since


def test_quiet_run_reports_nothing(tmp_path: Path) -> None:
    root = _fake_home(tmp_path)
    since = _armed(root)
    assert scan_changes(root, since) == []
    assert "unchanged by this run" in format_report(root, [])


def test_appending_to_an_existing_file_is_caught(tmp_path: Path) -> None:
    """The shape this atom was written for: a growing append-only log."""
    root = _fake_home(tmp_path)
    since = _armed(root)

    with (root / "security_events.jsonl").open("a") as fh:
        fh.write('{"leaked":"row"}\n')

    changes = scan_changes(root, since)
    assert [c.path for c in changes] == ["security_events.jsonl"]
    assert changes[0].kind == "modified"
    report = format_report(root, changes)
    assert "real-home rail FAILED" in report
    assert "security_events.jsonl" in report


def test_new_file_and_its_parent_are_named(tmp_path: Path) -> None:
    root = _fake_home(tmp_path)
    since = _armed(root)

    (root / "tasks").mkdir()
    (root / "tasks" / "t-deadbeef.json").write_text("{}\n")

    caught = {c.path: c.kind for c in scan_changes(root, since)}
    assert caught["tasks/t-deadbeef.json"] in {"created", "modified"}
    assert caught["tasks"] == "dir-entries-changed"


def test_in_place_rewrite_of_identical_bytes_is_caught(tmp_path: Path) -> None:
    """Same size, same content, new mtime — the case a size-only diff misses.

    Measured for real: the suite rewrote ``skills/*/SKILL.md`` byte-identically.
    """
    root = _fake_home(tmp_path)
    since = _armed(root)

    (root / "sessions" / "s1.json").write_text("{}\n")

    assert [c.path for c in scan_changes(root, since)] == ["sessions/s1.json"]


def test_deletion_is_caught_via_the_surviving_parent(tmp_path: Path) -> None:
    root = _fake_home(tmp_path)
    since = _armed(root)

    (root / "sessions" / "s1.json").unlink()

    assert [c.path for c in scan_changes(root, since)] == ["sessions"]


def test_absent_home_reports_nothing_and_says_so(tmp_path: Path) -> None:
    """A fresh CI container has no ~/.personalclaw: nothing to compare, no invented
    failure — and the report says which case it is in."""
    missing = tmp_path / "never_created" / ".personalclaw"
    assert scan_changes(missing, time.time_ns()) == []
    report = format_report(missing, [])
    assert "does not exist" in report
    assert "nothing to report" in report


def test_scan_does_not_create_or_touch_the_root(tmp_path: Path) -> None:
    """The detector stats; it never writes. Its own walk must not move any mtime."""
    root = _fake_home(tmp_path)
    before = {
        p: p.stat().st_mtime_ns for p in [root, root / "sessions", root / "sessions" / "s1.json"]
    }
    scan_changes(root, time.time_ns())
    assert {p: p.stat().st_mtime_ns for p in before} == before
    missing = tmp_path / "absent"
    scan_changes(missing, time.time_ns())
    assert not missing.exists(), "scan_changes must not create the root it inspects"


def test_symlink_out_of_the_tree_is_not_followed(tmp_path: Path) -> None:
    """A symlinked subdir must not drag an unrelated tree into the report."""
    root = _fake_home(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "link")
    since = _armed(root)
    (outside / "unrelated.txt").write_text("x\n")

    paths = [c.path for c in scan_changes(root, since)]
    assert "link/unrelated.txt" not in paths


def test_residue_allowance_is_empty_and_exact(tmp_path: Path) -> None:
    """The allowance must stay a NAMED list, never a prefix/glob baseline.

    If a future atom populates it, this test forces the entry to be an exact path
    and keeps a glob from silently covering a whole subtree.
    """
    assert real_home_guard.ALLOWED_RESIDUE == frozenset()
    assert not any(ch in entry for entry in real_home_guard.ALLOWED_RESIDUE for ch in "*?[")

    root = _fake_home(tmp_path)
    since = _armed(root)
    (root / "sessions" / "s1.json").write_text("{}\n")
    assert scan_changes(root, since), "an unlisted path must still be caught"


def test_change_renders_with_path_kind_and_size() -> None:
    rendered = str(HomeChange(path="security_events.jsonl", kind="modified", size=27318621))
    assert "security_events.jsonl" in rendered
    assert "modified" in rendered
    assert "27318621" in rendered
