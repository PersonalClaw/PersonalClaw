"""A file's comments follow the file into a home restored at another path.

A comment on a file in Files (a passage selected in its preview) is anchored to its document by
the file's absolute path, in ``doc_id`` and ``doc_path``. That path is true only in the home, at
the place, that wrote it. A snapshot restored into a home at another path (another machine,
another account's name, a Linux ``/home``) carried the comments with the old home's paths, so the
same file there showed its comments greyed out as another document's, with no highlight, and
Submit to AI handed the model a path that does not exist.

Asserted against the real snapshot and restore the command line runs, replacing and merging, and
an export archive imported: a comment on a file in the old home's workspace, or anywhere else in
the old home, names the same file in this home; a comment on an artifact (its slug), and one on a
file outside the old home, are left as they are; a merge leaves this home's own comments as they
were.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personalclaw import doc_comments, snapshot
from personalclaw.config import loader as config_loader


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    """No gateway of these homes is running: a replace restore asks."""
    monkeypatch.setattr(snapshot, "_running_gateway", lambda: None)


def _use_home(monkeypatch, home: Path) -> Path:
    """Make *home* the active PersonalClaw home, as ``PERSONALCLAW_HOME`` names one."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return Path(config_loader.config_dir())


def _a_file(folder: str | Path, rel: str) -> str:
    path = Path(folder) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Plan\n\nThe beds by the south fence get tomatoes.\n", encoding="utf-8")
    return str(path)


def _comment_on(path: str, words: str) -> str:
    return doc_comments.add(
        doc_id=path,
        doc_path=path,
        doc_label=os.path.basename(path),
        quote="tomatoes",
        comment=words,
        line=3,
        column=33,
    ).id


def _anchors() -> dict[str, tuple[str, str]]:
    """Each comment's words, with the document it is anchored to, as the deck reads them."""
    return {c["comment"]: (c["doc_id"], c["doc_path"]) for c in doc_comments.list_comments()}


def _old_home(tmp_path: Path, monkeypatch, first_home: str, own_workspace: bool) -> dict:
    """A home with a comment on a file in its workspace, on a file elsewhere in the home, on an
    artifact and on a file outside the home; returns what each file is, by the comment's words."""
    home = _use_home(monkeypatch, tmp_path / "alice" / first_home)
    if own_workspace:
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "alice" / "notes"))
    workspace = config_loader.default_workspace_dir()
    outside = _a_file(tmp_path / "repos" / "garden", "README.md")
    in_workspace = _a_file(workspace, "beds/plan.md")
    in_home = _a_file(home / "uploads", "seed-list.md")
    _comment_on(in_workspace, "Check the spacing.")
    _comment_on(in_home, "Order by March.")
    _comment_on(outside, "Keep as it is.")
    doc_comments.add(doc_id="garden-plan", doc_label="Garden plan", comment="Nice chart.")
    return {"workspace": workspace, "home": str(home), "outside": outside}


@pytest.mark.parametrize("mode", ["replace", "merge"])
@pytest.mark.parametrize(
    "first_home, own_workspace",
    [
        pytest.param(".personalclaw", False, id="first-home-at-the-default-place"),
        pytest.param("pc-home", False, id="first-home-elsewhere"),
        pytest.param("pc-home", True, id="first-home-with-a-workspace-of-its-own"),
    ],
)
def test_a_snapshot_restored_at_another_path_anchors_each_files_comments_to_this_homes_file(
    tmp_path, monkeypatch, mode, first_home, own_workspace
) -> None:
    old = _old_home(tmp_path, monkeypatch, first_home, own_workspace)
    snaps = tmp_path / "snapshots"
    assert snapshot.snapshot_main([str(snaps)]) == 0
    archive = next(snaps.glob("personalclaw-snapshot-*.tar.gz"))

    monkeypatch.delenv("PERSONALCLAW_WORKSPACE", raising=False)
    home = _use_home(monkeypatch, tmp_path / "bob-mini" / "pc")
    if mode == "merge":
        mine = _a_file(config_loader.default_workspace_dir(), "todo.md")
        _comment_on(mine, "Bob's own note.")
    assert snapshot.restore_main([str(archive), "--mode", mode]) == 0

    here = config_loader.default_workspace_dir()
    assert here != old["workspace"] and here.startswith(os.path.realpath(home))
    anchors = _anchors()
    moved = str(Path(here) / "beds" / "plan.md")
    assert anchors["Check the spacing."] == (moved, moved), "the workspace file's, in this home"
    in_home = str(home / "uploads" / "seed-list.md")
    assert anchors["Order by March."] == (in_home, in_home), "a file elsewhere in the home's"
    assert anchors["Keep as it is."] == (old["outside"], old["outside"]), "outside: as it was"
    assert anchors["Nice chart."] == ("garden-plan", ""), "an artifact's slug: as it was"
    if mode == "merge":
        mine = str(Path(here) / "todo.md")
        assert anchors["Bob's own note."] == (mine, mine), "this home's own: as they were"


def test_an_export_archive_imported_at_another_path_anchors_the_comments_here(
    tmp_path, monkeypatch
) -> None:
    from personalclaw.portability import create_export_zip

    old = _old_home(tmp_path, monkeypatch, "pc-home", False)
    exported, _ = create_export_zip()
    archive = tmp_path / "export.zip"
    archive.write_bytes(exported)

    home = _use_home(monkeypatch, tmp_path / "bob-mini" / "pc")
    assert snapshot.restore_main([str(archive), "--mode", "merge"]) == 0

    here = config_loader.default_workspace_dir()
    anchors = _anchors()
    moved = str(Path(here) / "beds" / "plan.md")
    assert anchors["Check the spacing."] == (moved, moved)
    assert anchors["Keep as it is."] == (old["outside"], old["outside"])
    assert anchors["Nice chart."] == ("garden-plan", "")
    assert all(not a[0].startswith(old["home"]) for a in anchors.values()), anchors
    assert home.is_dir()


# ── only the archive's own rows, and never by a folder that holds this home ──────────────────


def _unpacked(tmp_path: Path, came_from: str, brought: list[dict]) -> Path:
    """An unpacked archive naming its home *came_from*, carrying the comments *brought*."""
    import json

    snap = tmp_path / "unpacked"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "MANIFEST.json").write_text(json.dumps({"personalclaw_dir": came_from}), "utf-8")
    (snap / "doc_comments.json").write_text(json.dumps({"comments": brought}), "utf-8")
    return snap


def _row(cid: str, path: str) -> dict:
    return {"id": cid, "doc_id": path, "doc_path": path, "comment": f"note {cid}", "ts": 1.0}


def _seed(rows: list[dict]) -> None:
    import json

    doc_comments.store_path().write_text(json.dumps({"comments": rows}), "utf-8")


def test_an_archive_that_names_the_root_as_its_home_moves_no_comment(tmp_path, monkeypatch) -> None:
    home = _use_home(monkeypatch, tmp_path / "bob" / "pc")
    mine = str(Path(config_loader.default_workspace_dir()) / "todo.md")
    theirs = str(tmp_path / "alice" / "pc-home" / "workspace" / "plan.md")
    _seed([_row("c-mine", mine), _row("c-theirs", theirs)])
    snap = _unpacked(tmp_path, "/", [_row("c-theirs", theirs)])
    assert doc_comments.take_in_restored(snap) == 0
    assert _anchors() == {"note c-mine": (mine, mine), "note c-theirs": (theirs, theirs)}
    assert home.is_dir()


def test_an_archive_folder_that_holds_this_home_moves_nothing(tmp_path, monkeypatch) -> None:
    _use_home(monkeypatch, tmp_path / "bob" / "pc")
    mine = str(Path(config_loader.default_workspace_dir()) / "todo.md")
    above = str(tmp_path / "bob" / "notes.md")
    _seed([_row("c-mine", mine), _row("c-above", above)])
    snap = _unpacked(tmp_path, str(tmp_path / "bob"), [_row("c-above", above)])
    assert doc_comments.take_in_restored(snap) == 0
    assert _anchors() == {"note c-mine": (mine, mine), "note c-above": (above, above)}


def test_only_the_comments_the_archive_brought_are_moved(tmp_path, monkeypatch) -> None:
    # Two homes on one machine: this one's own comment on a file of the other home stays as it is.
    _use_home(monkeypatch, tmp_path / "bob" / "pc")
    there = str(tmp_path / "alice" / "pc-home" / "workspace" / "plan.md")
    _seed([_row("c-brought", there), _row("c-mine-on-alices", there)])
    snap = _unpacked(tmp_path, str(tmp_path / "alice" / "pc-home"), [_row("c-brought", there)])
    assert doc_comments.take_in_restored(snap) == 1
    moved = str(Path(config_loader.default_workspace_dir()) / "plan.md")
    assert _anchors() == {"note c-brought": (moved, moved), "note c-mine-on-alices": (there, there)}
