"""Saving the same file as an artifact again takes back the artifact that lost it.

A file-backed artifact is a live link to a file, kept as the file's absolute path. After the
upgrade from the releases whose workspace was ``~/workplace/personalclaw-workspace``, that
folder is no longer one an artifact may follow, so the link is refused and the artifact shows its
last saved copy. The owner moves the file into this workspace and saves it as an artifact from
Files: the save matched only the exact path, so it made a second artifact beside the first, and the
first never followed its file again.

Now a save of a file takes back the artifact whose link this home can no longer follow and that
names the same file at the same place under a workspace, as the release that made the link had it
(``NativeArtifactProvider.find_link_for``). A link outside any workspace is never taken.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.config import loader as config_loader
from personalclaw.stale_write import revision_of

PLAN = "# Plan\n\nThe beds by the south fence get tomatoes.\n"
SAVED = "# Plan\n\nThe beds by the south fence get beans.\n"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "bob" / "pc"
    home.mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return Path(config_loader.config_dir())


@pytest.fixture
def provider(home):
    from personalclaw.artifacts import registry

    prov = NativeArtifactProvider()
    with patch.object(registry, "get_provider", return_value=prov):
        yield prov


def _a_file(path: Path, text: str = PLAN) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _workspace() -> Path:
    return Path(config_loader.default_workspace_dir())


def _linked_to(provider: NativeArtifactProvider, path: Path, name: str = "Plan") -> str:
    """An artifact whose link names *path*, made as a release before this one made it."""
    made = _a_file(_workspace() / "made-here" / f"{name}.md")
    art = provider.create(name=name, content=None, kind="markdown", source_path=str(made))
    art.source_path = str(path)
    provider._write_meta(art)
    made.unlink()
    return art.slug


def _earlier_workspace(tmp_path: Path) -> Path:
    return tmp_path / "bob" / "workplace" / "personalclaw-workspace"


def _app() -> web.Application:
    from personalclaw.artifacts.handlers import register_artifact_routes

    app = web.Application()
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    register_artifact_routes(app)
    return app


async def _save_from_files(path: Path, content: str | None):
    """Files → Save as artifact, as ``api.saveFileAsArtifact`` sends it."""
    body = {"name": "Plan", "source": "manual", "source_path": str(path), "kind": "markdown"}
    if content is not None:
        body["content"] = content
    async with TestClient(TestServer(_app())) as c:
        resp = await c.post(
            "/api/artifacts",
            json=body,
            headers={"If-Match": f'"{revision_of(path.read_text("utf-8"))}"'},
        )
        return resp.status, await resp.json()


def test_a_save_of_the_moved_file_takes_back_the_artifact(provider, tmp_path) -> None:
    earlier = _a_file(_earlier_workspace(tmp_path) / "beds" / "plan.md")
    slug = _linked_to(provider, earlier)
    assert provider.get(slug).source_unfollowed == "outside"
    here = _a_file(_workspace() / "beds" / "plan.md")

    assert provider.find_link_for(str(here)).slug == slug
    status, body = asyncio.run(_save_from_files(here, SAVED))
    assert status == 200, body
    assert body["slug"] == slug, "the save took back the artifact instead of making a second"
    assert [a.slug for a in provider.list()] == [slug]
    art = provider.get(slug)
    assert art.source_path == str(here) and art.source_unfollowed == ""
    assert here.read_text("utf-8") == SAVED, "it follows the file again, so the save wrote it"
    assert earlier.read_text("utf-8") == PLAN, "the file it lost is never written"


def test_a_save_with_no_text_takes_it_back_and_shows_the_file(provider, tmp_path) -> None:
    slug = _linked_to(provider, _earlier_workspace(tmp_path) / "beds" / "plan.md")
    here = _a_file(_workspace() / "beds" / "plan.md", SAVED)

    status, body = asyncio.run(_save_from_files(here, None))
    assert status == 200, body
    assert body["slug"] == slug
    art = provider.get(slug)
    assert art.source_path == str(here) and art.content == SAVED


def test_a_link_outside_any_workspace_is_not_taken(provider, tmp_path) -> None:
    _linked_to(provider, _a_file(tmp_path / "elsewhere" / "beds" / "plan.md"))
    here = _a_file(_workspace() / "beds" / "plan.md")

    assert provider.find_link_for(str(here)) is None
    status, body = asyncio.run(_save_from_files(here, SAVED))
    assert status in (200, 201), body
    assert len(provider.list()) == 2, "an unrelated file's artifact is left as it was"


def test_the_newest_of_two_lost_links_is_taken(provider, tmp_path) -> None:
    older = _linked_to(provider, _earlier_workspace(tmp_path) / "beds" / "plan.md", "Older")
    newer = _linked_to(provider, _earlier_workspace(tmp_path) / "beds" / "plan.md", "Newer")
    art = provider._read_meta(older)
    art.updated_at = "2020-01-01T00:00:00+00:00"
    provider._write_meta(art)
    here = _a_file(_workspace() / "beds" / "plan.md")

    assert provider.find_link_for(str(here)).slug == newer


def test_a_link_that_still_follows_its_file_is_never_taken(provider, monkeypatch) -> None:
    """Even in a folder taken for a workspace: a link this home follows is another artifact's."""
    from personalclaw import memory_locality

    monkeypatch.setattr(memory_locality, "is_the_workspace", lambda folder: True)
    followed = _a_file(_workspace() / "projects" / "beds" / "plan.md")
    provider.create(name="Mine", content=None, kind="markdown", source_path=str(followed))
    here = _a_file(_workspace() / "beds" / "plan.md")

    assert provider.find_link_for(str(here)) is None
