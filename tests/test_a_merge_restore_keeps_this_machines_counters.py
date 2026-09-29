"""A merge restore, and an import, keep this machine's own counters, and never add another's.

🔴 A merge restore took in every day of spend this home had none for (``_merge_json_map`` over
``spend.json``), and the generic pass copied the archive's file into a home without one: another
machine's dollars counted against this machine's budget caps. The tool and context-savings
counters were merged the same way, and another machine's scheduler marks
(``durability_state.json``), copied into a home without its own, read as backups this machine had
just taken, so its first snapshot waited out their interval. An import did the same.

Each of these is one machine's account of itself, and not ``merged_in`` now: a merge restore and an
import leave this machine's as it is, or absent, and the merge plan says so. A replace restore
still brings the whole home back.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from personalclaw.durability import inventory as inv

#: Each counter, as this machine holds it and as another machine's archive does.
COUNTERS: dict[str, tuple[dict, dict]] = {
    "spend": ({"2026-09-28": 1.5}, {"2026-09-01": 40.0, "2026-09-28": 99.0}),
    "tool_usage": ({"read_file": {"count": 3}}, {"shell": {"count": 90}}),
    "tokenjuice_savings": (
        {"schema": 1, "rows": {"2026-09|m|log": {"count": 3}}},
        {"schema": 1, "rows": {"2026-08|m|log": {"count": 70}}},
    ),
    "durability_state": ({"last_snapshot": 1000.0}, {"last_snapshot": 9_999_999_999.0}),
    "task_due_notices": ({"notified": {"task-1": "2026-09-28"}}, {"notified": {"t-9": "x"}}),
}


def test_every_counter_is_left_out_of_a_merge():
    left_out = {e.id for e in inv.INVENTORY if not e.merged_in and e.kind == inv.KIND_JSON_FILE}
    assert left_out == set(COUNTERS)


def _archive_and_home(tmp_path: Path, *, here: bool) -> tuple[Path, Path]:
    snap, home = tmp_path / "snap", tmp_path / "home"
    snap.mkdir()
    home.mkdir()
    (snap / "config.json").write_text("{}", encoding="utf-8")
    (home / "config.json").write_text("{}", encoding="utf-8")
    for entry_id, (mine, theirs) in COUNTERS.items():
        path = inv.by_id(entry_id).path
        (snap / path).write_text(json.dumps(theirs), encoding="utf-8")
        if here:
            (home / path).write_text(json.dumps(mine), encoding="utf-8")
    return snap, home


@pytest.mark.parametrize("here", [True, False], ids=["this machine has its own", "it has none"])
def test_a_merge_restore_leaves_this_machines_counters_as_they_are(tmp_path, here):
    from personalclaw.snapshot import _do_merge

    snap, home = _archive_and_home(tmp_path, here=here)
    before = {e: (home / inv.by_id(e).path).read_bytes() if here else None for e in COUNTERS}
    _do_merge(snap, home, None)
    for entry_id in COUNTERS:
        path = home / inv.by_id(entry_id).path
        if here:
            assert path.read_bytes() == before[entry_id], f"{entry_id} took the archive's in"
        else:
            assert not path.exists(), f"{entry_id} was copied in from the archive"


def test_the_merge_plan_says_it_leaves_them_out(tmp_path):
    from personalclaw.snapshot import merge_plan

    snap, home = _archive_and_home(tmp_path, here=True)
    rows = {r["path"]: r for r in merge_plan(snap, home, None)}
    for entry_id in COUNTERS:
        row = rows[inv.by_id(entry_id).path]
        assert row["action"] == "skip", entry_id
        assert row["detail"] == "this machine's own: the archive's is left out"


def test_an_import_leaves_them_as_they_are(tmp_path, monkeypatch):
    from personalclaw.portability import apply_import_zip

    snap, home = _archive_and_home(tmp_path, here=True)
    before = {e: (home / inv.by_id(e).path).read_bytes() for e in COUNTERS}
    archive = tmp_path / "export.zip"
    root = "personalclaw-export-20260901T000000Z"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{root}/MANIFEST.json", json.dumps({"version": 2, "contents": {}}))
        for entry_id in COUNTERS:
            path = inv.by_id(entry_id).path
            zf.writestr(f"{root}/{path}", (snap / path).read_bytes())
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.portability.config_dir", lambda: home)
    apply_import_zip(archive, mode="merge")
    for entry_id in COUNTERS:
        assert (home / inv.by_id(entry_id).path).read_bytes() == before[entry_id], entry_id


def test_another_machines_spend_never_counts_against_the_caps_here(tmp_path, monkeypatch):
    """What the counter is for: today's spend, which the day's budget cap is checked against.
    Another machine spent today; this one has spent nothing yet."""
    from personalclaw.guardrails.budgets import SpendMeter, _today_key
    from personalclaw.snapshot import _do_merge

    snap, home = tmp_path / "snap", tmp_path / "home"
    snap.mkdir()
    home.mkdir()
    (snap / "config.json").write_text("{}", encoding="utf-8")
    (home / "config.json").write_text("{}", encoding="utf-8")
    (snap / "spend.json").write_text(
        json.dumps({_today_key(): {"tokens": 90_000, "dollars": 99.0, "unpriced": 0}})
    )
    (home / "spend.json").write_text(json.dumps({"2026-01-01": {"tokens": 1, "dollars": 0.01}}))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    _do_merge(snap, home, None)
    assert SpendMeter(config_dir=home).day_totals().dollars == 0.0


def test_a_legacy_automation_file_still_comes_in(tmp_path):
    """The control: `autonudge.json` is a legacy automation store, not a counter. Each loop the
    home lacks still comes in, and the boot's legacy import brings it in switched off."""
    from personalclaw.snapshot import _do_merge

    snap, home = _archive_and_home(tmp_path, here=True)
    (snap / "autonudge.json").write_text(json.dumps({"version": 1, "loops": {"snap": {}}}))
    (home / "autonudge.json").write_text(json.dumps({"version": 1, "loops": {"live": {}}}))
    _do_merge(snap, home, None)
    assert set(json.loads((home / "autonudge.json").read_text())["loops"]) == {"live", "snap"}
