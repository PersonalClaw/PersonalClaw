"""A linked artifact follows its file into a home restored at another path.

A file-backed artifact is a live link to a file: it shows the file's text, and saving it writes
the file. The link is the file's absolute path (``source_path``), true only in the home, at the
place, that wrote it. A snapshot restored into a home at another path (another machine, another
account's name, a Linux ``/home``) kept the old home's path, so every read and save of the link
was refused as outside this home's places: the artifact showed its last saved copy, edits to the
file never appeared, and a save wrote only that copy.

Asserted against the real snapshot and restore the command line runs, replacing and merging, and
an export archive imported: an artifact linked to a file in the old home's workspace follows the
same file here, shows its text, shows an edit made to it here, and a save writes it; one linked
to a file outside the old home is left as it is.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from personalclaw import snapshot
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.config import loader as config_loader

PLAN = "# Plan\n\nThe beds by the south fence get tomatoes.\n"
EDITED = "# Plan\n\nThe beds by the south fence get peppers this year.\n"
SAVED = "# Plan\n\nThe beds by the south fence get beans.\n"


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    """No gateway of these homes is running: a replace restore asks."""
    monkeypatch.setattr(snapshot, "_running_gateway", lambda: None)


def _use_home(monkeypatch, home: Path) -> Path:
    """Make *home* the active PersonalClaw home, as ``PERSONALCLAW_HOME`` names one."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return Path(config_loader.config_dir())


def _a_file(folder: str | Path, rel: str, text: str = PLAN) -> str:
    path = Path(folder) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def _old_home(tmp_path: Path, monkeypatch, first_home: str, own_workspace: bool) -> tuple[str, str]:
    """A home with an artifact linked to a file in its workspace and one linked to a file outside
    the home; returns their slugs."""
    _use_home(monkeypatch, tmp_path / "alice" / first_home)
    if own_workspace:
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "alice" / "notes"))
    plan = _a_file(config_loader.default_workspace_dir(), "beds/plan.md")
    provider = NativeArtifactProvider()
    linked = provider.create(name="Plan", content=None, kind="markdown", source_path=plan)
    assert linked.source_path == plan and provider.get(linked.slug).content == PLAN
    outside = str(tmp_path / "repos" / "garden" / "README.md")
    kept = provider.create(name="Readme", content="# Readme\n", kind="markdown")
    kept.source_path = outside
    provider._write_meta(kept)
    return linked.slug, kept.slug


def _follows_here(slug: str) -> None:
    """The artifact *slug* is linked to this home's copy of the plan, and reads and writes it."""
    here = Path(config_loader.default_workspace_dir()) / "beds" / "plan.md"
    provider = NativeArtifactProvider()
    art = provider.get(slug)
    assert art.source_path == str(here), "the link names the same file in this home"
    assert art.content == PLAN, "it shows the file's text"
    here.write_text(EDITED, encoding="utf-8")
    assert provider.get(slug).content == EDITED, "an edit made to the file here shows"
    provider.update(slug, content=SAVED, snapshot=False, actor="user")
    assert here.read_text(encoding="utf-8") == SAVED, "a save writes the file here"


@pytest.mark.parametrize("mode", ["replace", "merge"])
@pytest.mark.parametrize(
    "first_home, own_workspace",
    [
        pytest.param(".personalclaw", False, id="first-home-at-the-default-place"),
        pytest.param("pc-home", False, id="first-home-elsewhere"),
        pytest.param("pc-home", True, id="first-home-with-a-workspace-of-its-own"),
    ],
)
def test_a_snapshot_restored_at_another_path_has_each_linked_artifact_follow_this_homes_file(
    tmp_path, monkeypatch, mode, first_home, own_workspace
) -> None:
    linked, kept = _old_home(tmp_path, monkeypatch, first_home, own_workspace)
    outside = NativeArtifactProvider().get(kept).source_path
    snaps = tmp_path / "snapshots"
    assert snapshot.snapshot_main([str(snaps)]) == 0
    archive = next(snaps.glob("personalclaw-snapshot-*.tar.gz"))

    monkeypatch.delenv("PERSONALCLAW_WORKSPACE", raising=False)
    home = _use_home(monkeypatch, tmp_path / "bob-mini" / "pc")
    assert snapshot.restore_main([str(archive), "--mode", mode]) == 0
    # The file as this home has it: restored with the home, or copied here with a workspace that
    # was a folder of its own, which a snapshot of the home does not carry.
    _a_file(config_loader.default_workspace_dir(), "beds/plan.md")

    assert config_loader.default_workspace_dir().startswith(os.path.realpath(home))
    _follows_here(linked)
    assert NativeArtifactProvider().get(kept).source_path == outside, "outside: as it was"


def test_an_export_archive_imported_at_another_path_has_its_linked_artifacts_follow(
    tmp_path, monkeypatch
) -> None:
    from personalclaw.portability import create_export_zip

    linked, _kept = _old_home(tmp_path, monkeypatch, "pc-home", False)
    exported, _ = create_export_zip()
    archive = tmp_path / "export.zip"
    archive.write_bytes(exported)

    _use_home(monkeypatch, tmp_path / "bob-mini" / "pc")
    assert snapshot.restore_main([str(archive), "--mode", "merge"]) == 0
    _a_file(config_loader.default_workspace_dir(), "beds/plan.md")

    _follows_here(linked)


def test_only_the_artifacts_the_archive_brought_follow_and_never_by_the_root(
    tmp_path, monkeypatch
) -> None:
    import json

    from personalclaw.artifacts import native

    _use_home(monkeypatch, tmp_path / "bob" / "pc")
    there = _a_file(tmp_path / "alice" / "pc-home" / "workspace", "beds/plan.md")
    provider = NativeArtifactProvider()
    brought = provider.create(name="Brought", content="# x\n", kind="markdown")
    mine = provider.create(name="Mine", content="# y\n", kind="markdown")
    for art in (brought, mine):
        art.source_path = there
        provider._write_meta(art)
    snap = tmp_path / "unpacked"
    (snap / "artifacts" / brought.slug).mkdir(parents=True)
    (snap / "artifacts" / brought.slug / "meta.json").write_text("{}", "utf-8")

    (snap / "MANIFEST.json").write_text(json.dumps({"personalclaw_dir": "/"}), "utf-8")
    assert native.take_in_restored(snap) == 0, "the root names no home"
    (snap / "MANIFEST.json").write_text(
        json.dumps({"personalclaw_dir": str(tmp_path / "bob")}), "utf-8"
    )
    assert native.take_in_restored(snap) == 0, "a folder holding this home names no old home"

    (snap / "MANIFEST.json").write_text(
        json.dumps({"personalclaw_dir": str(tmp_path / "alice" / "pc-home")}), "utf-8"
    )
    assert native.take_in_restored(snap) == 1
    here = str(Path(config_loader.default_workspace_dir()) / "beds" / "plan.md")
    assert NativeArtifactProvider().get(brought.slug).source_path == here
    assert (
        NativeArtifactProvider().get(mine.slug).source_path == there
    ), "this home's own: as it was"
