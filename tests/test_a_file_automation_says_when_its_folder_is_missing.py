"""A file automation whose folder is not on this machine says so, on its row and as it resumes.

🔴 The defect (Backlog: "A file automation restored at another path reads active and never fires,
with no error"). A file automation's patterns are stored as written, so an absolute one, as the
folder picker writes, names a folder on the machine that wrote it. Restore the home onto another
machine where that folder sits elsewhere, press Resume all, and the automation reads active. It
matches no file, so it never fires, and nothing said why: the watch reads "no files" as "no
changes".

Now a pattern whose fixed folder (the part before its first wildcard) is not there is said: on the
row in the Triggers list answer, the list's warning advisory (its badge reads "folder missing", from
the row's `folders_not_here`), and in Resume all's answer for each automation it switched back on.
A folder that is there with no match in it yet is not flagged: the file may still come.

Driven through the real Triggers list route, the real Resume all route and the real store, in a
home of the test's own. The row Resume all resumes is held the way a replace restore leaves it
(`restore_hold` set, switched off).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from test_a_replace_restore_pauses_what_runs_on_its_own import _listed, _page_on, _req

from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

SAID = "not on this machine"


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


def _watching(home: Path, tid: str, paths: list[str], *, held: bool = False) -> None:
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="file",
            spec={"paths": paths},
            workflow={"inline": {"provider": "notify", "config": {"title_template": "A new file"}}},
            enabled=not held,
            restore_hold="another_home" if held else "",
        )
    )


def _warned(home: Path, monkeypatch: pytest.MonkeyPatch, tid: str) -> list[str]:
    T = _page_on(monkeypatch, home)
    resp = asyncio.run(T.api_triggers(_req("GET", "/api/triggers?type=store")))
    (row,) = [r for r in json.loads(resp.body)["triggers"] if r["raw_id"] == tid]
    return [w for w in row["warnings"] if SAID in w]


def test_a_watch_whose_folder_is_not_here_says_so_on_its_row(home, tmp_path, monkeypatch):
    """🔴 Before: no warning, the row read as healthy, and it never fired."""
    gone = tmp_path / "Users" / "someone-else" / "notes"
    _watching(home, "file:notes", [f"{gone}/**/*.md"])
    (warning,) = _warned(home, monkeypatch, "file:notes")
    assert str(gone) in warning and "so it never fires" in warning, warning


def test_the_row_names_the_folders_its_badge_says_are_missing(home, tmp_path, monkeypatch):
    """The list's badge reads "folder missing" off them (`advisoryBadge`), not "check schedule"."""
    there, gone = tmp_path / "here", tmp_path / "gone"
    there.mkdir()
    _watching(home, "file:two", [f"{there}/*.txt", f"{gone}/*.txt"])
    _watching(home, "file:here", [f"{there}/*.md"])
    T = _page_on(monkeypatch, home)
    resp = asyncio.run(T.api_triggers(_req("GET", "/api/triggers?type=store")))
    rows = {r["raw_id"]: r for r in json.loads(resp.body)["triggers"]}
    assert rows["file:two"]["folders_not_here"] == [str(gone)]
    assert rows["file:here"]["folders_not_here"] == []


def test_a_folder_that_is_there_with_no_match_yet_is_not_flagged(home, tmp_path, monkeypatch):
    """The control: an empty watch whose folder exists is waiting for a file, not broken."""
    there = tmp_path / "notes"
    there.mkdir()
    _watching(home, "file:notes", [f"{there}/**/*.md", f"{there}/todo.txt"])
    assert _warned(home, monkeypatch, "file:notes") == []


def test_a_home_relative_pattern_is_read_in_this_home(home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _watching(home, "file:notes", ["~/inbox/*.pdf"])
    (warning,) = _warned(home, monkeypatch, "file:notes")
    assert str(tmp_path / "inbox") in warning, warning
    (tmp_path / "inbox").mkdir()
    assert _warned(home, monkeypatch, "file:notes") == []


def test_one_missing_folder_of_two_names_only_it(home, tmp_path, monkeypatch):
    there, gone = tmp_path / "here", tmp_path / "gone"
    there.mkdir()
    _watching(home, "file:two", [f"{there}/*.txt", f"{gone}/*.txt"])
    (warning,) = _warned(home, monkeypatch, "file:two")
    assert str(gone) in warning and str(there) not in warning, warning
    assert "so nothing there can fire it" in warning, warning


def test_two_patterns_in_one_missing_folder_name_it_once(home, tmp_path, monkeypatch):
    gone = tmp_path / "gone"
    _watching(home, "file:two", [f"{gone}/*.md", f"{gone}/*.txt"])
    (warning,) = _warned(home, monkeypatch, "file:two")
    assert warning.count(str(gone)) == 1 and "which is not" in warning, warning
    assert "so it never fires" in warning, warning


def test_another_kinds_stray_paths_key_is_not_a_watch(home, monkeypatch):
    """Only a file automation is polled for files: a schedule whose spec carries a `paths` key, as
    a hand edit can leave, watches nothing, and is not said to."""
    TriggerStore(base_dir=home).upsert(
        Trigger(
            id="clock:brief",
            name="Brief",
            kind="clock",
            spec={"kind": "cron", "expr": "0 9 * * *", "paths": ["/Users/someone-else/notes/*.md"]},
            workflow={"inline": {"provider": "notify", "config": {"title_template": "Brief"}}},
        )
    )
    row = _listed(_page_on(monkeypatch, home))["clock:brief"]
    assert [w for w in row["warnings"] if SAID in w] == []


def test_resume_all_says_it_as_it_switches_the_watch_back_on(home, tmp_path, monkeypatch):
    """🔴 Before: Resume all answered `{id, name}` and the watch read active."""
    there = tmp_path / "here"
    there.mkdir()
    _watching(home, "file:gone", [f"{tmp_path / 'old-home' / 'notes'}/*.md"], held=True)
    _watching(home, "file:here", [f"{there}/*.md"], held=True)
    T = _page_on(monkeypatch, home)
    resp = asyncio.run(
        T.api_triggers_resume_restored(_req("POST", "/api/triggers/restore-hold/resume"))
    )
    resumed = {r["id"]: r for r in json.loads(resp.body)["resumed"]}
    assert set(resumed) == {"file:gone", "file:here"}
    (warning,) = resumed["file:gone"]["warnings"]
    assert SAID in warning and str(tmp_path / "old-home" / "notes") in warning, warning
    assert resumed["file:here"]["warnings"] == []
