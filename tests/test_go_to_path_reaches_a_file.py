"""The explorer's listing says a file is a file, and "Go to path" learns what a path is from the
folder's entries.

Files › Explorer › Go to path, given a file's own path, said "That path does not exist." for a file
that opened from the tree: the box sent every path to `GET /api/file-list` as a folder, and the
listing answered `404 {"error": "not a directory"}` for a file and for nothing-at-all alike, which
the page could only read one way. The box now asks the folder's entries (`GET /api/file-complete`,
its autocomplete read) whether the name is a file or a folder, and the listing answers
`not_a_directory` for a file and `not_found` for a path with nothing there.

Every confinement stays exactly as strict: a path outside the roots, a blocked file inside one, and
a relative path the server has no base for are all still the one `400` that says nothing about what
is there.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import api_file_complete, api_file_list
from personalclaw.dashboard.session_store import KEY_FILE


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/file-list", api_file_list)
    app.router.add_get("/api/file-complete", api_file_complete)
    return app


@pytest.fixture(autouse=True)
def _sel():
    with patch("personalclaw.sel.sel") as m:
        m.return_value = MagicMock()
        yield


@pytest.fixture
def root(tmp_path):
    """``tmp_path/root`` as the dashboard's one browsable root, with a file two folders down."""
    base = tmp_path / "root"
    (base / "memory" / "instructions").mkdir(parents=True)
    (base / "memory" / "instructions" / "NOTES.md").write_text("# notes\n")
    real = os.path.realpath(base)
    with patch(
        "personalclaw.dashboard.handlers.files._dashboard_roots",
        return_value=[("Workspace", real)],
    ):
        yield base


async def _get(path: str, route: str = "/api/file-list") -> tuple[int, dict]:
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get(route, params={"path": path})
        return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_the_folders_entries_say_a_file_is_a_file(root) -> None:
    """What the box reads to tell a file from a folder: a 200 for an admitted folder, with the
    exact name among its entries."""
    target = root / "memory" / "instructions" / "NOTES.md"
    status, body = await _get(str(target), route="/api/file-complete")
    assert status == 200
    (entry,) = [s for s in body["suggestions"] if s["name"] == "NOTES.md"]
    assert entry == {"name": "NOTES.md", "path": os.path.realpath(target), "is_dir": False}


@pytest.mark.asyncio
async def test_the_folders_entries_name_nothing_outside_the_roots(root, tmp_path) -> None:
    (tmp_path / "elsewhere.md").write_text("x")
    status, body = await _get(str(tmp_path / "elsewhere.md"), route="/api/file-complete")
    assert (status, body) == (200, {"suggestions": []})


@pytest.mark.asyncio
async def test_a_files_path_is_named_as_a_file(root) -> None:
    status, body = await _get(str(root / "memory" / "instructions" / "NOTES.md"))
    assert status == 404
    assert body["error"]["code"] == "not_a_directory"


@pytest.mark.asyncio
async def test_a_path_with_nothing_there_is_not_found(root) -> None:
    status, body = await _get(str(root / "memory" / "absent.md"))
    assert status == 404
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_a_folder_still_lists(root) -> None:
    status, body = await _get(str(root / "memory" / "instructions"))
    assert status == 200
    assert [e["name"] for e in body["entries"]] == ["NOTES.md"]


@pytest.mark.asyncio
async def test_a_file_outside_the_roots_is_refused_without_saying_it_is_a_file(root, tmp_path):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x")
    status, body = await _get(str(outside))
    assert status == 400
    assert "not_a_directory" not in str(body)


@pytest.mark.asyncio
async def test_a_blocked_file_inside_a_root_is_refused_without_saying_it_is_a_file(root) -> None:
    """The session signing key's basename is refused wherever it sits, root or not."""
    key = root / KEY_FILE
    key.write_bytes(b"\x00" * 32)
    status, body = await _get(str(key))
    assert status == 400
    assert "not_a_directory" not in str(body)


@pytest.mark.asyncio
async def test_a_relative_path_is_still_refused(root) -> None:
    """The server has no base to resolve it against; the explorer resolves one against the folder
    it shows before it asks."""
    status, _ = await _get("memory/instructions/NOTES.md")
    assert status == 400
