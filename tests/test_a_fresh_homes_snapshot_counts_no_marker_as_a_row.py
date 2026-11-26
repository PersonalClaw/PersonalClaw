"""A home's snapshot counts the workflow versions' digest table as a file, never as a row.

🔴 The defect. A home's first start wrote the workflow versions' digest table once
(`workflows.versions.remember_digests`), an empty one on a fresh home, so that an Allow recorded
before digests were of the declared definition still matches its version. It sits in the
workflows store's folder, and a snapshot counted every JSON file there as one of the automation
domain's rows (`snapshot._domain_counts`). So a fresh home's archive in Settings › Durability read
"automation: 1 rows", automation data the owner never had, where it had read "# files".

Now a start that finds no recorded version writes no table at all, so a fresh home holds none.
Where a start does write one, for the versions it found, the inventory names it among the files
the gateway keeps for a store itself (`StateEntry.bookkeeping_within`), and the row count leaves
it out: the table is one of the domain's files and none of its rows. A version the owner saved
still counts.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from personalclaw.durability import archive as arch
from personalclaw.durability import inventory as inv
from personalclaw.workflows import store, versions


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: h)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: h)
    return h


def _spec() -> dict[str, Any]:
    return {
        "name": "sample",
        "version": 1,
        "root": {"kind": "transform", "id": "only", "config": {"expr": "done"}},
    }


def _automation(tmp_path) -> dict[str, int]:
    """The automation domain's counts in a snapshot of the home, as the archive browser reads."""
    from personalclaw.snapshot import snapshot_main

    out = tmp_path / "out"
    code = snapshot_main(
        parsed=argparse.Namespace(output_dir=str(out), keep=5, list_snapshots=False)
    )
    assert code == 0
    (tar,) = sorted(out.glob("personalclaw-snapshot-*.tar.gz"))
    manifest = arch.read_manifest(tar)
    assert manifest is not None
    return manifest["domains"].get("automation", {"files": 0, "rows": 0})


def _workflows() -> inv.StateEntry:
    return next(e for e in inv.INVENTORY if e.id == "workflows")


def test_a_fresh_homes_start_writes_no_digest_table_and_counts_no_row(home, tmp_path):
    """🔴 Before: rows 1, and the archive said the fresh home held automation data."""
    assert versions.remember_digests() == 0
    assert not (home / "workflows" / "versions" / "digests-renamed.json").exists()
    counts = _automation(tmp_path)
    assert counts["rows"] == 0, counts


def test_a_digest_table_a_start_writes_is_a_file_and_no_row(home, tmp_path):
    """A version recorded before the start that notes it: its table is a file of the domain's,
    and the version alone is a row."""
    assert versions.record_version("sample", _spec(), saved_by=versions.OWNER) == 1
    assert versions.remember_digests() == 1
    assert (home / "workflows" / "versions" / "digests-renamed.json").is_file()
    counts = _automation(tmp_path)
    assert counts["rows"] == 1, counts
    assert counts["files"] >= 2, counts


def test_a_version_the_owner_saved_is_still_a_row(home, tmp_path):
    """The control: the table is left out, never the owner's own versions."""
    versions.remember_digests()
    assert versions.record_version("sample", _spec(), saved_by=versions.OWNER) == 1
    counts = _automation(tmp_path)
    assert counts["rows"] == 1, counts


def test_the_inventory_names_the_table_the_versions_store_writes(home):
    """The declared name and the file the store writes are one: a rename of either reds here."""
    written = versions._renamed_path().relative_to(store.workflows_dir()).as_posix()
    assert inv.is_bookkeeping(_workflows(), written), written
    assert not inv.is_bookkeeping(_workflows(), "versions/sample/v001.json")
    assert not inv.is_bookkeeping(_workflows(), "defs/sample/workflow.json")
