"""A linked artifact that cannot follow its file says so, and so does a save that missed the file.

A file-backed artifact is a live link to a file: it shows the file's text, and saving it writes the
file. Every read and save re-checks the link against the folders an artifact may follow. When the
check refuses it (a home restored elsewhere, the workspace earlier releases made under the user's
home), or the file is gone, the artifact showed its last saved copy without a word, and a save
wrote only that copy while answering as if the file had changed.

Now a read says why it could not follow the file (``source_unfollowed``: ``outside``, ``missing``,
``unreadable``), and a save or revert that did not write the file says why
(``source_unwritten``). Neither is ever written to the artifact's meta. The agent's view of the
artifact says the same, so it does not edit a file the artifact no longer follows.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from personalclaw.artifacts import handlers
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.config import loader as config_loader

PLAN = "# Plan\n\nThe beds by the south fence get tomatoes.\n"
EDITED = "# Plan\n\nThe beds by the south fence get peppers this year.\n"
SAVED = "# Plan\n\nThe beds by the south fence get beans.\n"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "bob" / "pc"
    home.mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return Path(config_loader.config_dir())


def _a_file(folder: Path, rel: str, text: str = PLAN) -> Path:
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _linked(home: Path) -> tuple[NativeArtifactProvider, str, Path]:
    """An artifact linked to a file in this home's workspace: the provider, its slug, the file."""
    plan = _a_file(Path(config_loader.default_workspace_dir()), "beds/plan.md")
    provider = NativeArtifactProvider()
    art = provider.create(name="Plan", content=None, kind="markdown", source_path=str(plan))
    return provider, art.slug, plan


def _relink(provider: NativeArtifactProvider, slug: str, path: Path) -> None:
    """Point *slug*'s link at *path*, as a home restored elsewhere has it."""
    art = provider._read_meta(slug)
    assert art is not None
    art.source_path = str(path)
    provider._write_meta(art)


def _meta(home: Path, slug: str) -> dict:
    return json.loads((home / "artifacts" / slug / "meta.json").read_text("utf-8"))


def test_a_link_outside_the_folders_an_artifact_follows_says_so(home, tmp_path) -> None:
    provider, slug, _plan = _linked(home)
    elsewhere = _a_file(tmp_path / "alice" / "pc-home" / "workspace", "beds/plan.md", EDITED)
    _relink(provider, slug, elsewhere)

    art = provider.get(slug)
    assert art is not None
    assert art.content == PLAN, "the read shows the artifact's last saved copy"
    assert art.source_unfollowed == "outside"
    shown = handlers._serialize(art, include_content=True)
    assert shown["source_unfollowed"] == "outside"
    assert shown["source_path"] == str(elsewhere), "the answer names the file it could not follow"


def test_a_link_to_a_file_that_is_gone_says_so(home) -> None:
    provider, slug, plan = _linked(home)
    plan.unlink()

    art = provider.get(slug)
    assert art is not None and art.content == PLAN
    assert art.source_unfollowed == "missing"


def test_a_followed_link_says_nothing(home) -> None:
    provider, slug, plan = _linked(home)
    plan.write_text(EDITED, encoding="utf-8")

    art = provider.get(slug)
    assert art is not None and art.content == EDITED
    assert art.source_unfollowed == ""
    assert "source_unwritten" not in handlers._serialize(art, include_content=True)


def test_a_save_that_cannot_reach_the_file_says_so_and_keeps_the_copy(home, tmp_path) -> None:
    provider, slug, _plan = _linked(home)
    elsewhere = _a_file(tmp_path / "alice" / "pc-home" / "workspace", "beds/plan.md")
    _relink(provider, slug, elsewhere)

    answer = provider.update(slug, content=SAVED, snapshot=True)
    assert answer is not None
    assert answer.source_unwritten == "outside"
    assert handlers._serialize(answer, include_content=True)["source_unwritten"] == "outside"
    assert answer.content == SAVED, "the save is kept as the artifact's own copy"
    assert elsewhere.read_text("utf-8") == PLAN, "a file outside is never written"
    meta = _meta(home, slug)
    assert "source_unwritten" not in meta and "source_unfollowed" not in meta


def test_a_save_to_a_file_it_cannot_write_says_so(home, monkeypatch) -> None:
    """The file system refuses the write to the file (a full disk, a file another program locked):
    the save is kept here, and says the file did not change. Only that one write is refused, as a
    folder's own mode cannot be: a write under the home makes its folder the owner's own again."""
    from personalclaw.artifacts import native

    provider, slug, plan = _linked(home)
    real = native.atomic_write

    def refuse_the_file(path, content, **kw):
        if Path(path).resolve() == plan.resolve():
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, content, **kw)

    monkeypatch.setattr(native, "atomic_write", refuse_the_file)
    answer = provider.update(slug, content=SAVED, snapshot=True)
    assert answer is not None and answer.source_unwritten == "unwritable"
    assert answer.content == PLAN, "the read follows the file, which the save did not change"
    assert plan.read_text("utf-8") == PLAN


def test_a_revert_that_cannot_reach_the_file_says_so(home, tmp_path) -> None:
    provider, slug, plan = _linked(home)
    assert provider.update(slug, content=SAVED, snapshot=True) is not None
    plan.unlink()

    answer = provider.revert(slug, 1)
    assert answer is not None and answer.source_unwritten == "missing"
    assert answer.source_unfollowed == "missing"
    assert answer.content == PLAN, "the revert is kept as the artifact's own copy"
    assert not plan.exists(), "a revert never makes the file"


def test_a_save_that_reaches_the_file_says_nothing(home) -> None:
    provider, slug, plan = _linked(home)

    answer = provider.update(slug, content=SAVED, snapshot=True)
    assert answer is not None and answer.source_unwritten == ""
    assert plan.read_text("utf-8") == SAVED
    assert "source_unwritten" not in handlers._serialize(answer, include_content=True)


def test_a_list_row_carries_no_link_state(home, tmp_path) -> None:
    """A list row comes off the meta, where nothing is computed: absent beats a made-up ""."""
    provider, slug, _plan = _linked(home)
    _relink(provider, slug, _a_file(tmp_path / "elsewhere", "plan.md"))
    art = provider.get(slug)
    assert art is not None
    row = handlers._serialize(art)
    assert "source_unfollowed" not in row and "source_unwritten" not in row


def test_the_agent_is_told_the_artifact_no_longer_follows_its_file(
    home, tmp_path, monkeypatch
) -> None:
    from personalclaw import investigate as inv
    from personalclaw.artifacts import registry

    provider, slug, _plan = _linked(home)
    _relink(provider, slug, _a_file(tmp_path / "alice" / "pc-home" / "workspace", "beds/plan.md"))
    monkeypatch.setitem(registry._providers, "native", provider)

    ctx = asyncio.run(inv._resolve_artifact(slug, object()))
    assert ctx is not None
    assert "Its file can't be followed now (it is outside the folders an artifact can follow)" in (
        ctx.snapshot
    )
