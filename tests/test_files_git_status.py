"""Tests for the Files git-status endpoint (Files P2)."""

from __future__ import annotations

import asyncio
import json
import subprocess

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import files as F


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """A real git repo under a dashboard root, with a tracked + modified file
    and an untracked file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    import os

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def run(*a):
        return subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=env)

    run("init", "-q")
    (repo / "tracked.txt").write_text("v1\n")
    run("add", "tracked.txt")
    run("commit", "-qm", "init")
    (repo / "tracked.txt").write_text("v2\n")  # modified
    (repo / "untracked.txt").write_text("new\n")  # untracked

    # Make the repo a dashboard root so the path validator + containment pass.
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("Repo", str(repo))])
    monkeypatch.setattr(
        F, "_validate_dashboard_path", lambda raw: str(repo) if raw == str(repo) else None
    )
    return repo


def _call(path: str):
    from urllib.parse import quote

    req = make_mocked_request("GET", f"/api/file-git-status?path={quote(path)}")
    resp = asyncio.run(F.api_file_git_status(req))
    return resp.status, json.loads(resp.body.decode())


def test_git_status_reports_branch_and_changes(git_repo, monkeypatch):
    monkeypatch.setattr(
        F, "_sel", lambda: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    )
    status, body = _call(str(git_repo))
    assert status == 200
    assert body["repoRoot"] == str(git_repo)
    assert body["branch"]  # some branch name (main/master)
    # tracked.txt modified, untracked.txt untracked
    codes = {k.split("/")[-1]: v for k, v in body["statuses"].items()}
    assert "M" in codes.get("tracked.txt", "")
    assert codes.get("untracked.txt") == "??"


def test_unborn_branch_reports_real_name_not_HEAD(tmp_path, monkeypatch):
    # A freshly `git init`'d repo with NO commits (the greenfield case): the branch
    # must report its real name (main/master) via symbolic-ref, not the literal
    # "HEAD" that rev-parse --abbrev-ref prints on an unborn branch.
    repo = tmp_path / "fresh"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    (repo / "new.py").write_text("x = 1\n")  # untracked, never committed
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("Fresh", str(repo))])
    monkeypatch.setattr(
        F, "_validate_dashboard_path", lambda raw: str(repo) if raw == str(repo) else None
    )
    monkeypatch.setattr(
        F, "_sel", lambda: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    )
    status, body = _call(str(repo))
    assert status == 200
    assert body["branch"] and body["branch"] != "HEAD"  # real name, not "HEAD"
    assert body["statuses"][str(repo / "new.py")] == "??"


def test_non_repo_returns_empty(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("Plain", str(plain))])
    monkeypatch.setattr(
        F, "_validate_dashboard_path", lambda raw: str(plain) if raw == str(plain) else None
    )
    monkeypatch.setattr(
        F, "_sel", lambda: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    )
    status, body = _call(str(plain))
    assert status == 200
    assert body == {"repoRoot": "", "branch": "", "statuses": {}}


def test_invalid_path_rejected(monkeypatch):
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw: None)
    status, body = _call("/etc")
    assert status == 400


def test_repo_outside_roots_rejected(git_repo, monkeypatch):
    # Path validates, but the repo root is NOT within the allowed roots.
    monkeypatch.setattr(F, "_path_within_roots", lambda p: False)
    monkeypatch.setattr(
        F, "_sel", lambda: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    )
    status, body = _call(str(git_repo))
    assert status == 200
    assert body["repoRoot"] == ""


def test_git_helper_reaps_child_on_timeout(tmp_path, monkeypatch):
    # The shared _git helper kills a git command that exceeds its timeout — it must
    # also REAP the killed child (await proc.wait) so a slow/large repo + the
    # Changes-panel poll loop don't pile up zombie git processes. We stand in a
    # `sleep` for `git` (via PATH) so the command reliably overruns a tiny timeout.

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_stub = fake_bin / "git"
    git_stub.write_text("#!/bin/sh\nsleep 5\n")
    git_stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))  # _git calls bare "git" → our stub

    async def run():
        # asyncio.create_subprocess_exec resolves "git" against PATH; capture the proc
        # the helper spawned so we can assert it was reaped (returncode set).
        spawned = {}
        real = asyncio.create_subprocess_exec

        async def spy(*a, **k):
            p = await real(*a, **k)
            spawned["p"] = p
            return p

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
        out = await F._git(["status"], str(tmp_path), timeout=0.2)
        assert out == ""  # timed out → empty
        p = spawned["p"]
        assert p.returncode is not None  # reaped, not left a zombie

    asyncio.run(run())


def test_git_log_returns_commits(git_repo, monkeypatch):
    monkeypatch.setattr(
        F, "_sel", lambda: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    )
    from urllib.parse import quote

    req = make_mocked_request("GET", f"/api/file-git-log?path={quote(str(git_repo))}&limit=10")
    resp = asyncio.run(F.api_file_git_log(req))
    assert resp.status == 200
    body = json.loads(resp.body.decode())
    assert body["repoRoot"] == str(git_repo)
    assert len(body["commits"]) >= 1
    c = body["commits"][0]
    assert c["hash"] and c["subject"] == "init" and c["relative"] and c["author"]


def test_git_log_non_repo_empty(tmp_path, monkeypatch):
    plain = tmp_path / "plain2"
    plain.mkdir()
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("Plain2", str(plain))])
    monkeypatch.setattr(
        F, "_validate_dashboard_path", lambda raw: str(plain) if raw == str(plain) else None
    )
    monkeypatch.setattr(
        F, "_sel", lambda: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    )
    from urllib.parse import quote

    req = make_mocked_request("GET", f"/api/file-git-log?path={quote(str(plain))}")
    resp = asyncio.run(F.api_file_git_log(req))
    assert json.loads(resp.body.decode()) == {"repoRoot": "", "commits": []}


def test_git_commit_returns_diff(git_repo, monkeypatch):
    monkeypatch.setattr(
        F, "_sel", lambda: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    )
    import subprocess

    # get the init commit hash
    h = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    from urllib.parse import quote

    req = make_mocked_request("GET", f"/api/file-git-commit?path={quote(str(git_repo))}&hash={h}")
    resp = asyncio.run(F.api_file_git_commit(req))
    assert resp.status == 200
    body = json.loads(resp.body.decode())
    assert body["hash"] == h and body["subject"] == "init"
    assert "tracked.txt" in body["diff"] and "+v1" in body["diff"]
    # A small commit is NOT truncated — the flag is present + False so the cockpit
    # knows the diff is complete (a large commit sets it True; no-silent-caps).
    assert body["truncated"] is False
    assert body["found"] is True


def test_git_commit_unknown_hash_reports_not_found(git_repo, monkeypatch):
    # A valid-hex but nonexistent hash (stale ref after a force-push/rebase, or from
    # a different repo) must report found=False — NOT a misleading empty "diff" that
    # the cockpit would show as a legit "empty checkpoint".
    monkeypatch.setattr(
        F, "_sel", lambda: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    )
    from urllib.parse import quote

    req = make_mocked_request(
        "GET", f"/api/file-git-commit?path={quote(str(git_repo))}&hash=deadbeef"
    )
    resp = asyncio.run(F.api_file_git_commit(req))
    assert resp.status == 200
    body = json.loads(resp.body.decode())
    assert body["found"] is False and body["diff"] == "" and body["subject"] == ""


def test_git_original_signals_truncation_for_large_committed_file(tmp_path, monkeypatch):
    # A committed file larger than the 512KB read cap must come back with
    # truncated=True so the diff view can say the original side was cut (else a large
    # file's diff reads as if the tail was deleted — no-silent-caps).
    import os

    repo = tmp_path / "big"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def run(*a):
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=env)

    run("init", "-q")
    big = repo / "big.txt"
    big.write_text("x\n" * 400_000)  # ~800KB > 512KB cap
    run("add", "big.txt")
    run("commit", "-qm", "big")
    fp = str(big)
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw: fp if raw == fp else None)
    monkeypatch.setattr(F, "_path_within_roots", lambda p: True)
    monkeypatch.setattr(
        F, "_sel", lambda: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    )
    from urllib.parse import quote

    req = make_mocked_request("GET", f"/api/file-git-original?path={quote(fp)}")
    resp = asyncio.run(F.api_file_git_original(req))
    body = json.loads(resp.body.decode())
    assert (
        body["exists"] is True and body["truncated"] is True and len(body["content"]) == 512 * 1024
    )


def test_git_commit_rejects_non_hex_hash(git_repo, monkeypatch):
    monkeypatch.setattr(
        F, "_sel", lambda: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    )
    from urllib.parse import quote

    # an injection-y / non-hex value must be rejected before reaching git
    req = make_mocked_request(
        "GET", f"/api/file-git-commit?path={quote(str(git_repo))}&hash={quote('HEAD; rm -rf /')}"
    )
    resp = asyncio.run(F.api_file_git_commit(req))
    assert resp.status == 400
