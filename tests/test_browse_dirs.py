"""Tests for /api/browse-dirs endpoint."""

import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import api_browse_dirs, api_create_dir


def _make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/browse-dirs", api_browse_dirs)
    app.router.add_post("/api/create-dir", api_create_dir)
    return app


@pytest.fixture()
def mock_sel():
    with patch("personalclaw.dashboard.handlers.sel") as m:
        m.return_value = MagicMock()
        yield m.return_value


class TestBrowseDirs:
    @pytest.mark.asyncio
    async def test_default_path_is_the_workspace_root(self, tmp_path, mock_sel, monkeypatch):
        """The picker opens where work lives — the workspace root — not ``$HOME``.

        Measured on the container image: ``$HOME`` is ``/home/personalclaw``, outside the only
        volume, so a project folder made with "New folder here" at the picker's starting point was
        gone after ``docker rm`` + ``docker run``. The image sets the workspace to
        ``/data/workspace``; opening there puts the new folder on the volume. Same answer the
        Terminal gives for its cwd (#544).
        """
        workspace = tmp_path / "workspace"
        (workspace / "garden-planner").mkdir(parents=True)
        home = tmp_path / "home"
        (home / "elsewhere").mkdir(parents=True)
        monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
        monkeypatch.setenv("HOME", str(home))
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/browse-dirs")
            data = await resp.json()
        assert resp.status == 200
        assert data["path"] == os.path.realpath(workspace)
        assert {d["name"] for d in data["dirs"]} == {"garden-planner"}

    @pytest.mark.asyncio
    async def test_default_path_falls_back_to_home_without_a_usable_workspace(
        self, tmp_path, mock_sel, monkeypatch
    ):
        """No safe workspace root (``default_workspace_dir()`` is ``""``) → the home, as before."""
        (tmp_path / "projects").mkdir()
        monkeypatch.setattr("personalclaw.config.loader.default_workspace_dir", lambda: "")
        with patch("os.path.expanduser", side_effect=lambda p: p.replace("~", str(tmp_path))):
            async with TestClient(TestServer(_make_app())) as client:
                resp = await client.get("/api/browse-dirs")
                data = await resp.json()
                assert data["path"] == str(tmp_path)
                names = {d["name"] for d in data["dirs"]}
                assert "projects" in names

    @pytest.mark.asyncio
    async def test_lists_subdirectories(self, tmp_path, mock_sel):
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        (tmp_path / "file.txt").write_text("x")
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/browse-dirs?path={tmp_path}")
            data = await resp.json()
            names = [d["name"] for d in data["dirs"]]
            assert "alpha" in names
            assert "beta" in names
            assert "file.txt" not in names  # files excluded

    @pytest.mark.asyncio
    async def test_sorted_alphabetically(self, tmp_path, mock_sel):
        (tmp_path / "zebra").mkdir()
        (tmp_path / "apple").mkdir()
        (tmp_path / "mango").mkdir()
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/browse-dirs?path={tmp_path}")
            names = [d["name"] for d in (await resp.json())["dirs"]]
            assert names == ["apple", "mango", "zebra"]

    @pytest.mark.asyncio
    async def test_skips_hidden_and_excluded(self, tmp_path, mock_sel):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "src").mkdir()
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/browse-dirs?path={tmp_path}")
            names = {d["name"] for d in (await resp.json())["dirs"]}
            assert names == {"src"}

    @pytest.mark.asyncio
    async def test_flags_git_repos(self, tmp_path, mock_sel):
        # A child dir that is a git repo gets is_repo=True; a plain one False, so
        # the brownfield workspace picker can mark which folders are codebases.
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (tmp_path / "plain").mkdir()
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/browse-dirs?path={tmp_path}")
            by_name = {d["name"]: d for d in (await resp.json())["dirs"]}
            assert by_name["myrepo"]["is_repo"] is True
            assert by_name["plain"]["is_repo"] is False

    @pytest.mark.asyncio
    async def test_returns_parent(self, tmp_path, mock_sel):
        child = tmp_path / "child"
        child.mkdir()
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/browse-dirs?path={child}")
            data = await resp.json()
            assert data["parent"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_invalid_path_returns_404(self, mock_sel):
        # A path that isn't a browsable directory (missing/typo) is 404 Not Found —
        # matches api_file_list's contract; only an unreadable (permission) path 400s.
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/browse-dirs?path=/nonexistent_xyz_123")
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_missing_path_says_no_such_directory(self, tmp_path, mock_sel):
        # A path-bar user who mistypes/pastes a stale path gets a clear "No such
        # directory", not a confusing blanket "Not a directory".
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/browse-dirs?path={tmp_path}/does_not_exist")
            assert resp.status == 404
            assert "no such" in (await resp.json())["error"].lower()

    @pytest.mark.asyncio
    async def test_file_path_says_file_not_directory(self, tmp_path, mock_sel):
        f = tmp_path / "a_file.txt"
        f.write_text("x")
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/browse-dirs?path={f}")
            assert resp.status == 404
            assert "file" in (await resp.json())["error"].lower()

    @pytest.mark.asyncio
    async def test_permission_error_returns_empty_dirs(self, tmp_path, mock_sel):
        restricted = tmp_path / "restricted"
        restricted.mkdir()
        restricted.chmod(0o000)
        try:
            async with TestClient(TestServer(_make_app())) as client:
                resp = await client.get(f"/api/browse-dirs?path={restricted}")
                data = await resp.json()
                assert data["dirs"] == []
        finally:
            restricted.chmod(0o755)


class TestCreateDir:
    @pytest.mark.asyncio
    async def test_creates_dir(self, tmp_path, mock_sel):
        target = tmp_path / "new-proj"
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post("/api/create-dir", json={"path": str(target)})
            assert resp.status == 200
            assert target.is_dir()

    @pytest.mark.asyncio
    async def test_existing_dir_409(self, tmp_path, mock_sel):
        (tmp_path / "exists").mkdir()
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post("/api/create-dir", json={"path": str(tmp_path / "exists")})
            assert resp.status == 409

    @pytest.mark.asyncio
    async def test_missing_parent_400_no_chain_created(self, tmp_path, mock_sel):
        # create-dir makes ONE leaf inside an existing parent — it must NOT silently
        # materialize a whole chain (the old makedirs bug: "foo/bar/baz" built a nested
        # tree + buried the workspace at the deepest level).
        target = tmp_path / "foo" / "bar" / "baz"
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post("/api/create-dir", json={"path": str(target)})
            assert resp.status == 400
            assert "parent" in (await resp.json())["error"].lower()
        assert not (tmp_path / "foo").exists()  # nothing created

    @pytest.mark.asyncio
    async def test_system_root_denied_403(self, mock_sel):
        # browse-dirs refuses to navigate system roots; create-dir must refuse to
        # create under them too (consistency + a dir there would be unreachable in
        # the picker, and is_sensitive_path doesn't cover /etc & friends).
        async with TestClient(TestServer(_make_app())) as client:
            # The subtree list is the Code engine's validation list, so creating
            # under any OS-managed tree (not just /etc) is refused — so a stray folder
            # can't be materialized in a location a workspace bind would then reject.
            for p in ("/etc/pclaw-x", "/usr/pclaw-x", "/System/pclaw-x", "/Library/pclaw-x"):
                resp = await client.post("/api/create-dir", json={"path": p})
                assert resp.status == 403, p
                assert not os.path.exists(p), p


class TestBrowseRefusalIsLegible:
    """A refusal the workspace picker shows must say WHICH folder and WHY.

    Measured in a real browser against a gateway running as root: the picker's first browse (the
    gateway's home) answered ``403 {"error": "Access denied"}`` — no path, no reason — and the
    picker rendered exactly that, above an empty listing it had never read. The only record of
    what was refused was a ``security_events.jsonl`` row the user cannot see.
    """

    @pytest.mark.asyncio
    async def test_a_refusal_names_the_path_and_the_reason(self, mock_sel):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/browse-dirs?path=/etc")
            body = await resp.json()
        assert resp.status == 403
        err = body["error"]
        refused = os.path.realpath("/etc")  # macOS answers for /private/etc, and says so
        assert err["code"] == "path_protected"
        assert err["reason"] == "system_root"
        assert err["path"] == refused
        assert refused in err["message"]
        assert "protected system location" in err["message"]

    @pytest.mark.asyncio
    async def test_a_refused_default_location_names_the_home_it_tried(self, mock_sel, monkeypatch):
        # The failure the picker hit: no `path` at all, so the client cannot know what was refused
        # unless the answer says. A home inside a system tree is refused for any account. The
        # workspace root is taken out of the way so the default falls through to that home.
        monkeypatch.setenv("HOME", "/usr/share")
        monkeypatch.setattr("personalclaw.config.loader.default_workspace_dir", lambda: "")
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/browse-dirs")
            err = (await resp.json())["error"]
        home = os.path.realpath("/usr/share")
        assert resp.status == 403
        assert err["path"] == home
        assert home in err["message"]

    @pytest.mark.asyncio
    async def test_a_credentials_folder_is_refused_for_its_own_reason(
        self, tmp_path, mock_sel, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".ssh").mkdir()
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/browse-dirs?path={tmp_path}/.ssh")
            err = (await resp.json())["error"]
        assert resp.status == 403
        assert err["reason"] == "sensitive_path"
        assert "credentials" in err["message"]
        # The audit record is unchanged: same operation, same reason string as before.
        mock_sel.log_api_access.assert_called_with(
            caller="dashboard",
            operation="browse_dirs",
            outcome="denied",
            resources=os.path.realpath(tmp_path / ".ssh"),
            error="sensitive path",
        )


class TestCreateDirNeverAtFilesystemRoot:
    """``create-dir`` refuses a parent ``browse-dirs`` would refuse — today, that means the root.

    The handler's own comment assumed "the picker always creates inside a dir it just navigated
    to". It did not: with its first browse refused, the picker built ``'' + '/' + 'q4-launch'``
    and this route answered 200 for ``/q4-launch`` — a new root-owned folder at the top of the
    disk, then bound as the project's workspace. The assumption is now a rule the server enforces.
    """

    @pytest.mark.asyncio
    async def test_never_creates_a_folder_directly_under_the_filesystem_root(
        self, mock_sel, monkeypatch
    ):
        # A spy, not a real mkdir: a gateway (or a CI runner) that runs as root would otherwise
        # really create the folder the test is about.
        made: list[str] = []
        monkeypatch.setattr(os, "mkdir", lambda p, *a, **k: made.append(p))
        target = f"/pclaw-root-probe-{uuid.uuid4().hex[:8]}"
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post("/api/create-dir", json={"path": target})
            body = await resp.json()
        assert resp.status == 403
        assert made == []
        err = body["error"]
        assert err["code"] == "path_protected"
        assert err["path"] == "/"
        assert "/" in err["message"]
        mock_sel.log_api_access.assert_called_with(
            caller="dashboard",
            operation="create_dir",
            outcome="denied",
            resources=target,
            error="unbrowsable parent (system root)",
        )

    @pytest.mark.asyncio
    async def test_still_creates_inside_a_folder_the_picker_can_open(self, tmp_path, mock_sel):
        # Positive control: the rule is "only where you can browse", not "never".
        async with TestClient(TestServer(_make_app())) as client:
            browsed = await client.get(f"/api/browse-dirs?path={tmp_path}")
            assert browsed.status == 200
            resp = await client.post("/api/create-dir", json={"path": f"{tmp_path}/q4-launch"})
        assert resp.status == 200
        assert (tmp_path / "q4-launch").is_dir()
