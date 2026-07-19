"""Tests for /api/file-read resolve=1 path resolution."""

import os
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import api_file_read


def _make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/file-read", api_file_read)
    return app


@pytest.fixture()
def mock_sel():
    with patch("personalclaw.sel.sel") as m:
        instance = MagicMock()
        m.return_value = instance
        yield instance


@pytest.fixture()
def home_patch(tmp_path):
    real_realpath = os.path.realpath

    def fake_expanduser(p):
        return p.replace("~", str(tmp_path))

    from personalclaw.config.loader import workspace_root

    roots = [("Test", str(tmp_path)), ("Workspace", os.path.realpath(str(workspace_root())))]
    with patch("os.path.expanduser", side_effect=fake_expanduser), patch(
        "os.path.realpath", side_effect=real_realpath
    ), patch("pathlib.Path.home", return_value=tmp_path), patch(
        "personalclaw.dashboard.handlers.files._dashboard_roots",
        return_value=roots,
    ):
        yield tmp_path


class TestFileReadResolve:
    @pytest.mark.asyncio
    async def test_resolve_joins_project_dir(self, tmp_path, mock_sel, home_patch):
        # A file present only under PROJECT_DIR (not workspace) still resolves —
        # the resolver tries workspace first, then PROJECT_DIR, picking whichever
        # base actually contains the file.
        proj = tmp_path / "project"
        proj.mkdir()
        f = proj / "src" / "app.py"
        f.parent.mkdir(parents=True)
        f.write_text("print('hello')")

        # Empty workspace (tried first, file absent there) → falls through to
        # PROJECT_DIR where the file lives.
        ws = tmp_path / "ws"
        ws.mkdir()
        with patch.dict(
            os.environ,
            {"PERSONALCLAW_PROJECT_DIR": str(proj), "PERSONALCLAW_WORKSPACE": str(ws)},
        ):
            async with TestClient(TestServer(_make_app())) as client:
                resp = await client.get("/api/file-read?path=src/app.py&resolve=1")
                assert resp.status == 200
                text = await resp.text()
                assert "hello" in text

    @pytest.mark.asyncio
    async def test_resolve_ignored_for_absolute_paths(self, tmp_path, mock_sel, home_patch):
        f = tmp_path / "abs.py"
        f.write_text("absolute")

        with patch.dict(os.environ, {"PERSONALCLAW_PROJECT_DIR": "/wrong/dir"}):
            async with TestClient(TestServer(_make_app())) as client:
                resp = await client.get(f"/api/file-read?path={f}&resolve=1")
                assert resp.status == 200
                text = await resp.text()
                assert "absolute" in text

    @pytest.mark.asyncio
    async def test_resolve_falls_back_to_workspace(self, tmp_path, mock_sel, home_patch):
        # With no PERSONALCLAW_PROJECT_DIR, a relative path must resolve against
        # the workspace root (where chat sessions + the native agent operate),
        # not be denied — otherwise clicking a file in chat 404s.
        from personalclaw.config.loader import workspace_root

        ws = workspace_root()
        os.makedirs(os.path.join(ws, "sub"), exist_ok=True)
        with open(os.path.join(ws, "sub", "note.txt"), "w") as fh:
            fh.write("ws-content")

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PERSONALCLAW_PROJECT_DIR", None)
            async with TestClient(TestServer(_make_app())) as client:
                resp = await client.get("/api/file-read?path=sub/note.txt&resolve=1")
                assert resp.status == 200
                assert "ws-content" in await resp.text()

    @pytest.mark.asyncio
    async def test_resolve_prefers_workspace_over_project_dir(self, tmp_path, mock_sel, home_patch):
        # cli.py auto-sets PERSONALCLAW_PROJECT_DIR to HOME (e.g. /data), but
        # session/agent files live in the workspace (/data/workspace). A relative
        # path present in the workspace must resolve there even when PROJECT_DIR is
        # set and does NOT contain the file.
        from personalclaw.config.loader import workspace_root

        ws = workspace_root()
        os.makedirs(ws, exist_ok=True)
        with open(os.path.join(ws, "doc.md"), "w") as fh:
            fh.write("ws-doc")
        # PROJECT_DIR points elsewhere and lacks doc.md.
        proj = tmp_path / "elsewhere"
        proj.mkdir()
        with patch.dict(os.environ, {"PERSONALCLAW_PROJECT_DIR": str(proj)}):
            async with TestClient(TestServer(_make_app())) as client:
                resp = await client.get("/api/file-read?path=doc.md&resolve=1")
                assert resp.status == 200
                assert "ws-doc" in await resp.text()

    @pytest.mark.asyncio
    async def test_audit_logs_resolved_path(self, tmp_path, mock_sel, home_patch):
        proj = tmp_path / "project"
        proj.mkdir()
        f = proj / "test.txt"
        f.write_text("content")

        with patch.dict(os.environ, {"PERSONALCLAW_PROJECT_DIR": str(proj)}):
            async with TestClient(TestServer(_make_app())) as client:
                resp = await client.get("/api/file-read?path=test.txt&resolve=1")
                assert resp.status == 200
                # Verify the resolved path (not "test.txt") was logged
                calls = [str(c) for c in mock_sel.log_tool_invocation.call_args_list]
                assert any(str(proj) in c for c in calls)

    @pytest.mark.asyncio
    async def test_resolve_rejects_traversal(self, tmp_path, mock_sel, home_patch):
        proj = tmp_path / "project"
        proj.mkdir()
        with patch.dict(os.environ, {"PERSONALCLAW_PROJECT_DIR": str(proj)}):
            async with TestClient(TestServer(_make_app())) as client:
                resp = await client.get("/api/file-read?path=../../etc/passwd&resolve=1")
                assert resp.status == 400

    @pytest.mark.asyncio
    async def test_head_returns_200_no_body(self, tmp_path, mock_sel, home_patch):
        f = tmp_path / "exists.txt"
        f.write_text("content")

        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.head(f"/api/file-read?path={f}")
            assert resp.status == 200
            body = await resp.read()
            assert body == b""

    @pytest.mark.asyncio
    async def test_head_returns_404_for_missing_file(self, tmp_path, mock_sel, home_patch):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.head(f"/api/file-read?path={tmp_path}/nope.txt")
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_head_logs_success(self, tmp_path, mock_sel, home_patch):
        f = tmp_path / "logged.txt"
        f.write_text("data")

        async with TestClient(TestServer(_make_app())) as client:
            await client.head(f"/api/file-read?path={f}")
            mock_sel.log_tool_invocation.assert_called_with(
                session_key="dashboard", tool_name="file_read", outcome="success", resources=str(f)
            )
