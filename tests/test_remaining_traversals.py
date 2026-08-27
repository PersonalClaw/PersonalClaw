"""The three traversal instances `record_ids` did not reach (#655, #430, #739).

The unvalidated-id-to-path class was closed by :mod:`personalclaw.record_ids` for the stores that
name a record ``<dir>/<id>``. These three are the same class reached differently, so the primitive
alone did not cover them:

* **#655** — ``POST /api/reveal`` was the ONE files endpoint that skipped
  ``_validate_dashboard_path``, so ``/etc/hosts`` and another instance's home both answered 200. It
  gains nothing from the blocked-basename work either, because that lives *inside* the function it
  bypassed — and it is the endpoint that hands a path to ``open``/``xdg-open``, i.e. to the host's
  default handler for whatever it is.
* **#430** — the git read endpoints resolved a repository by looking for ``.git``, accepted it as a
  FILE (git's standard gitfile pointer), and then validated **the pointer's location** rather than
  the **resolved gitdir**. The containment check ran on every request and measured the wrong path.
  The dashboard will even write the pointer for you: ``.git`` is not a blocked basename.
* **#739** — skill install validated every *file* path for ``..`` and left the *directory name* they
  are all written into unchecked. A safe relative path under an unsafe root is an unsafe path.

**And a fourth, found here rather than reported:** the QUARANTINE directory is named from
``detail.name`` too, and it escaped *before* ``scan_dir`` ran — so the supply-chain gate could not
refuse a write it had not yet been asked about.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from personalclaw.dashboard.handlers import files as F
from personalclaw.record_ids import UnsafeRecordId


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    """One dashboard root, plus an out-of-root directory holding a secret."""
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".env").write_text("SECRET=1")
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("Workspace", str(root))])
    return root, outside


# ── #430: the gitfile pointer ─────────────────────────────────────────────────


class TestGitdirContainment:
    def test_a_pointer_at_an_out_of_root_repo_is_refused(self, roots):
        """🔴 The escape. A one-line `.git` file inside an allowed root redirected all four git
        read endpoints at an arbitrary repository anywhere on disk."""
        root, outside = roots
        (outside / ".git").mkdir()
        probe = root / "probe"
        probe.mkdir()
        (probe / ".git").write_text(f"gitdir: {outside / '.git'}\n")
        assert F._git_repo_root(str(probe)) is None

    def test_a_RELATIVE_pointer_out_of_root_is_refused(self, roots):
        """git resolves a relative gitdir against the marker's directory, so the escape does not
        need an absolute path — and a check that only rejected absolute ones would miss this."""
        root, outside = roots
        (outside / ".git").mkdir()
        probe = root / "probe"
        probe.mkdir()
        rel = os.path.relpath(outside / ".git", probe)
        (probe / ".git").write_text(f"gitdir: {rel}\n")
        assert F._git_repo_root(str(probe)) is None

    def test_an_ordinary_in_root_repository_still_resolves(self, roots):
        """Vacuity floor. A guard that refused everything would pass both tests above while
        removing git status from the file explorer entirely."""
        root, _ = roots
        repo = root / "repo"
        (repo / ".git").mkdir(parents=True)
        assert F._git_repo_root(str(repo)) == str(repo)

    def test_a_worktree_of_an_IN_root_repo_still_resolves(self, roots):
        """The legitimate gitfile case. `git worktree` is why `.git` may be a file at all, so
        refusing every pointer would have been the easy fix and the wrong one."""
        root, _ = roots
        repo = root / "repo"
        (repo / ".git" / "worktrees" / "wt").mkdir(parents=True)
        wt = root / "wt"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {repo / '.git' / 'worktrees' / 'wt'}\n")
        assert F._git_repo_root(str(wt)) == str(wt)

    def test_a_symlinked_root_does_not_defeat_the_check(self, roots, monkeypatch):
        """🔴 The mistake the first version of this fix made, pinned so it cannot come back.

        The gitdir is compared after `realpath`, and on macOS `/var` is a symlink to `/private/var`
        (a user's workspace may be symlinked anywhere too). Comparing a resolved candidate against
        an UNRESOLVED root reported "outside" for a path plainly inside — refusing every ordinary
        repository, not just the escaping one.
        """
        root, _ = roots
        real = root / "real"
        (real / ".git" / "worktrees" / "wt").mkdir(parents=True)
        link = root / "link-to-real"
        link.symlink_to(real)
        wt = root / "wt"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {link / '.git' / 'worktrees' / 'wt'}\n")
        assert F._git_repo_root(str(wt)) == str(wt)

    @pytest.mark.parametrize("body", ["", "not a gitdir line\n", "gitdir:\n", "gitdir:   \n"])
    def test_a_garbage_pointer_is_not_a_repo(self, roots, body):
        root, _ = roots
        probe = root / "probe"
        probe.mkdir()
        (probe / ".git").write_text(body)
        assert F._git_repo_root(str(probe)) is None


# ── #739 + the quarantine: skill install directory names ──────────────────────


class TestSkillInstallDirectoryName:
    FILES = [{"path": "SKILL.md", "contents": "---\nname: ok\ndescription: d\n---\n\nbody\n"}]

    @pytest.mark.parametrize("name", ["../../evil", "/tmp/evil", "..", "a/b", "a\\b", ""], ids=repr)
    def test_an_escaping_skill_name_is_refused(self, name, tmp_path):
        from personalclaw.skills.marketplace import install_skill_files

        base = tmp_path / "skills"
        base.mkdir()
        with pytest.raises(UnsafeRecordId):
            install_skill_files(self.FILES, name, base)

    def test_an_ordinary_skill_name_still_installs(self, tmp_path):
        """Vacuity floor: every file path was already validated, so a guard that refused all names
        would pass the tests above and break installing anything."""
        from personalclaw.skills.marketplace import install_skill_files

        base = tmp_path / "skills"
        base.mkdir()
        written = install_skill_files(self.FILES, "ok-skill", base)
        assert written == base / "ok-skill" / "SKILL.md"
        assert written.is_file()

    def test_nothing_is_created_outside_the_base(self, tmp_path):
        """The property, not just the exception: a refusal must also not have `mkdir`'d on its way
        to raising."""
        from personalclaw.skills.marketplace import install_skill_files

        base = tmp_path / "skills"
        base.mkdir()
        before = sorted(p.name for p in tmp_path.iterdir())
        with pytest.raises(UnsafeRecordId):
            install_skill_files(self.FILES, "../evil", base)
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_the_quarantine_directory_name_is_guarded_too(self):
        """The fourth instance, found rather than reported.

        `install_scanned` stages the fetched payload into a temp dir named from `detail.name`, which
        the marketplace supplies — and it escaped BEFORE `scan_dir` ran, so the supply-chain gate
        could not refuse a write it had not been asked about yet. Asserted at the expression rather
        than by driving a fake marketplace, because the expression is the whole finding.
        """
        import inspect

        from personalclaw.skills import marketplace as M

        src = inspect.getsource(M.install_scanned)
        assert (
            "staged_root / (detail.name" not in src
        ), "the quarantine dir is being named by a raw join again — the fetched `name` reaches it"
        assert "record_path(" in src

        staged_root = Path(tempfile.mkdtemp())
        with pytest.raises(UnsafeRecordId):
            from personalclaw.record_ids import record_path

            record_path(staged_root, "../../evil", suffix="", kind="skill name")


# ── #655: /api/reveal ─────────────────────────────────────────────────────────


class TestRevealRootAllowlist:
    """Asserted at the ROUTE, because the finding is that this route skipped the guard — the guard
    itself was always correct."""

    def _request(self, body: dict):
        from unittest.mock import AsyncMock, MagicMock

        request = MagicMock()
        request.json = AsyncMock(return_value=body)
        request.get = lambda *a, **k: "dashboard"
        return request

    @pytest.mark.asyncio
    async def test_a_path_outside_every_root_is_refused(self, roots):
        import json as _json

        _root, outside = roots
        resp = await F.api_reveal_path(self._request({"path": str(outside / ".env")}))
        assert resp.status == 400
        # The STRUCTURED envelope, unlike this module's six flat siblings emitting the
        # same sentence: `test_wire_error_envelope_census` ratchets the flat population
        # down, so a new refusal joins the shape the project converges on.
        error = _json.loads(resp.body.decode())["error"]
        assert error["code"] == "invalid_path"
        assert "forbidden" in error["message"]

    @pytest.mark.asyncio
    async def test_a_system_path_is_refused(self, roots):
        """`/etc/hosts` carries no `..` and is not in the sensitive-path list, which is exactly why
        the two checks that WERE present did not catch it."""
        resp = await F.api_reveal_path(self._request({"path": "/etc/hosts"}))
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_the_guard_it_skipped_is_the_one_it_now_calls(self, roots, monkeypatch):
        """Pins the wiring rather than the outcome: an equivalent hand-rolled check here would pass
        the two tests above and drift from `_validate_dashboard_path` the first time that changes.
        """
        seen: list[str] = []
        real = F._validate_dashboard_path

        def _spy(raw: str):
            seen.append(raw)
            return real(raw)

        monkeypatch.setattr(F, "_validate_dashboard_path", _spy)
        await F.api_reveal_path(self._request({"path": "/etc/hosts"}))
        assert seen == ["/etc/hosts"]

    @pytest.mark.asyncio
    async def test_an_in_root_path_is_not_refused_by_the_new_guard(self, roots, monkeypatch):
        """Vacuity floor: the only caller is the explorer's Reveal button, passing a path the
        explorer enumerated. A guard that refused those would make the button permanently broken.
        """
        root, _ = roots
        target = root / "notes.md"
        target.write_text("hello")
        # Stop short of actually launching a file manager.
        monkeypatch.setattr(F.sys, "platform", "linux", raising=False)
        monkeypatch.setattr(F.shutil, "which", lambda _n: None)
        resp = await F.api_reveal_path(self._request({"path": str(target)}))
        assert resp.status == 200
