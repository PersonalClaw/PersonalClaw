"""A home with no recorded workflow version keeps no table of earlier digests.

At start, each recorded version's earlier digest is noted beside the history, once
(``versions.remember_digests``), so an Allow given before carries over. 🔴 A start with no recorded
version wrote the table anyway, empty: a JSON file under the workflows store, which a snapshot
counts as a row of the automation area, so an archive of a home with no workflow read
"automation: 1 rows" where it read "automation: N files". Now a start with nothing to note writes
nothing, and a later start notes the versions it finds then.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw import snapshot
from personalclaw.workflows import store, versions


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    versions._renamed_cache["at"] = None
    return tmp_path


def test_a_start_with_no_version_to_note_writes_no_table(home: Path) -> None:
    (store.workflows_dir() / "runs").mkdir(parents=True)  # a store, as a first start leaves it
    assert store.workflows_dir().resolve().is_relative_to(home.resolve()), "a scratch home"
    versions.remember_digests_at_start()
    assert not versions._renamed_path().exists()
    assert sorted(p.name for p in store.workflows_dir().rglob("*.json")) == []
    assert versions.current_form("earlier") == "earlier", "nothing noted, nothing renamed"


def test_the_archive_of_such_a_home_counts_no_automation_row(home: Path) -> None:
    """What the archive browser reads ("automation: N rows" when any, else N files), on the home
    as a snapshot stages it."""
    (store.workflows_dir() / "runs").mkdir(parents=True)
    (store.workflows_dir() / "runs" / "note.txt").write_text("x", encoding="utf-8")
    versions.remember_digests_at_start()
    counts = snapshot._domain_counts(store.workflows_dir().parent)
    automation = counts.get("automation") or {}
    assert automation.get("files", 0) >= 1, counts
    assert automation.get("rows", 0) == 0, counts
