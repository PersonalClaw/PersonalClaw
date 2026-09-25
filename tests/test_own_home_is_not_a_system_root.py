"""A root gateway's own home is a HOME, not a system tree, and it is still guarded like one.

``security._SYSTEM_SUBTREES`` lists ``/root`` (and macOS's ``/private/var/root``) beside ``/etc``
and ``/usr``. For an ordinary gateway that is right: the superuser's home is someone else's home,
and nothing else guards it. ``is_sensitive_path`` resolves its credential entries against the
RUNNING account's home, so for a gateway running as ``alice`` ``/root/.ssh`` is not a credential
path at all unless ``/root`` is refused whole.

For a gateway running AS root (a VPS, an LXC container, ``pip install`` into a plain Python image)
the same entry refused the account its own home. Measured in a real browser on a fresh container:
the workspace picker's default location, ``~``, answered 403, and a separate picker defect then
created the user's project folder at the filesystem root. The entry protected nothing there that
the home-keyed guards did not already cover, because ``/root/.ssh`` IS ``Path.home()/.ssh`` —
refused by ``is_sensitive_path`` (and every file route funnelling through it), under the same
home the OS sandbox lays its credential masks over.

So the superuser's home is exempt from the system-tree rule exactly when it IS the running
account's home, keyed on the same ``Path.home()`` those guards resolve against: the tree that is
let in is, by construction, the tree whose credentials they cover. Everything else stays refused.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import security
from personalclaw.dashboard.handlers import api_browse_dirs, api_create_dir
from personalclaw.dashboard.handlers.files import _is_system_root
from personalclaw.loop.validation import workspace_dir_errors, workspace_write_target_errors
from personalclaw.security import is_sensitive_path, is_system_path

SUPERUSER_HOMES = ("/root", "/private/var/root")


@pytest.mark.parametrize("home", SUPERUSER_HOMES)
class TestTheSuperuserHomeWhenItIsYourOwn:
    def test_its_folders_are_ordinary_workspace_locations(self, monkeypatch, home):
        monkeypatch.setenv("HOME", home)
        # The strict check (create-dir, workspace binds, terminal cwd, pack scan) …
        assert not is_system_path(f"{home}/code/project")
        assert not is_system_path(home)
        # … and the subtree-only one (browse-dirs, file search, the file browser's roots).
        assert not _is_system_root(f"{home}/code/project")
        assert not _is_system_root(home)

    def test_a_project_or_loop_can_bind_a_folder_in_it(self, monkeypatch, home):
        monkeypatch.setenv("HOME", home)
        assert workspace_write_target_errors(f"{home}/q4-launch") == []
        assert workspace_dir_errors(f"{home}/q4-launch", require_exists=False) == []

    def test_its_credentials_are_still_refused(self, monkeypatch, home):
        monkeypatch.setenv("HOME", home)
        for entry in (".ssh", ".ssh/id_ed25519", ".aws/credentials", ".gnupg", ".netrc"):
            assert is_sensitive_path(f"{home}/{entry}"), entry
            assert workspace_dir_errors(f"{home}/{entry}", require_exists=False), entry

    def test_the_bare_home_is_still_not_a_project_write_target(self, monkeypatch, home):
        # Exactly the rule `/home/alice` gets: no CLAUDE.md / AGENTS.md dropped into $HOME itself.
        monkeypatch.setenv("HOME", home)
        assert workspace_write_target_errors(home) == [
            "Workspace directory cannot be your home directory itself."
        ]


class TestEveryOtherSystemTreeIsStillRefused:
    @pytest.mark.parametrize("home", [*SUPERUSER_HOMES, "/home/alice"])
    @pytest.mark.parametrize(
        "path", ["/", "/etc", "/etc/ssh", "/usr/bin", "/System/Library", "/private/etc/passwd"]
    )
    def test_system_trees(self, monkeypatch, home, path):
        monkeypatch.setenv("HOME", home)
        assert is_system_path(path)

    def test_the_superuser_home_stays_refused_when_it_is_someone_elses(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/alice")
        # Why the entry exists at all: the credential guard is keyed on THIS account's home, so for
        # alice's gateway root's keys are not a credential path …
        assert not is_sensitive_path("/root/.ssh/id_ed25519")
        # … and the whole-tree refusal is the only thing standing in front of them.
        for path in ("/root", "/root/.ssh", "/root/code/project"):
            assert is_system_path(path), path
            assert _is_system_root(path), path

    def test_one_platforms_superuser_home_does_not_unlock_the_others(self, monkeypatch):
        monkeypatch.setenv("HOME", "/root")
        assert is_system_path("/private/var/root/project")
        monkeypatch.setenv("HOME", "/private/var/root")
        assert is_system_path("/root/project")

    def test_a_home_inside_a_system_tree_unlocks_nothing(self, monkeypatch):
        # The carve-out is the superuser's home, not "wherever $HOME points": a service account
        # whose home sits under /usr does not make /usr a workspace.
        monkeypatch.setenv("HOME", "/usr/local/svc")
        assert is_system_path("/usr/local/svc/project")
        assert _is_system_root("/usr/local/svc/project")

    def test_a_home_that_cannot_be_determined_exempts_nothing(self, monkeypatch):
        def no_home(cls):
            raise RuntimeError("Could not determine home directory.")

        monkeypatch.setattr(Path, "home", classmethod(no_home))
        assert is_system_path("/root/project")
        assert is_system_path("/private/var/root/project")


# ── end to end, through the two routes the workspace picker drives ─────────────────────────────


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


@pytest.fixture()
def superuser_home(tmp_path, monkeypatch):
    """A stand-in superuser home under ``tmp_path``, listed exactly as ``/root`` is.

    A test cannot become root, and one that happens to RUN as root must not write into the real
    ``/root``; so the list gains one entry that behaves like it.
    """
    home = os.path.realpath(tmp_path / "root")
    os.mkdir(home)
    monkeypatch.setattr(security, "_SYSTEM_SUBTREES", (*security._SYSTEM_SUBTREES, home))
    monkeypatch.setattr(security, "_SUPERUSER_HOMES", security._SUPERUSER_HOMES | {home})
    return home


class TestThePickerOnARootGateway:
    @pytest.mark.asyncio
    async def test_the_default_location_opens(self, superuser_home, monkeypatch, mock_sel):
        # The default location is the workspace root, which with nothing configured is
        # `<home>/workplace/personalclaw-workspace` — inside the superuser's own home, so it must
        # open here exactly as the home itself does.
        monkeypatch.setenv("HOME", superuser_home)
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/browse-dirs")
            body = await resp.json()
        assert resp.status == 200
        assert body["path"] == os.path.join(superuser_home, "workplace", "personalclaw-workspace")

    @pytest.mark.asyncio
    async def test_a_new_project_folder_lands_in_the_home(
        self, superuser_home, monkeypatch, mock_sel
    ):
        monkeypatch.setenv("HOME", superuser_home)
        target = f"{superuser_home}/q4-launch"
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.post("/api/create-dir", json={"path": target})
        assert resp.status == 200
        assert os.path.isdir(target)

    @pytest.mark.asyncio
    async def test_its_credentials_stay_closed(self, superuser_home, monkeypatch, mock_sel):
        monkeypatch.setenv("HOME", superuser_home)
        os.mkdir(f"{superuser_home}/.ssh")
        async with TestClient(TestServer(_make_app())) as client:
            browsed = await client.get(f"/api/browse-dirs?path={superuser_home}/.ssh")
            created = await client.post(
                "/api/create-dir", json={"path": f"{superuser_home}/.ssh/keys"}
            )
            browse_err = (await browsed.json())["error"]
        assert browsed.status == 403
        assert browse_err["reason"] == "sensitive_path"
        assert created.status == 403
        assert not os.path.exists(f"{superuser_home}/.ssh/keys")

    @pytest.mark.asyncio
    async def test_for_any_other_account_it_stays_closed(
        self, superuser_home, tmp_path, monkeypatch, mock_sel
    ):
        (tmp_path / "alice").mkdir()
        monkeypatch.setenv("HOME", str(tmp_path / "alice"))
        async with TestClient(TestServer(_make_app())) as client:
            browsed = await client.get(f"/api/browse-dirs?path={superuser_home}")
            created = await client.post(
                "/api/create-dir", json={"path": f"{superuser_home}/q4-launch"}
            )
            browse_err = (await browsed.json())["error"]
        assert browsed.status == 403
        assert browse_err["reason"] == "system_root"
        assert created.status == 403
        assert not os.path.exists(f"{superuser_home}/q4-launch")
