"""Tests for the Files git-status endpoint (Files P2).

The ``git_original`` block near the bottom pins #432: one endpoint had re-implemented
``_git``'s subprocess call, and the copy forgot each thing the original knew — a
timed-out ``git show`` was neither killed nor reaped (2 live processes leaked per
timeout), an unexecutable git escaped as HTTP 500 instead of degrading, and a directory
came back as a git TREE LISTING with ``exists:true``. The fix deleted the copy, so the
tests here are about the endpoint's behaviour AND about there being exactly one invoker.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest
from aiohttp.test_utils import make_mocked_request
from test_spawn_ceiling_audit import _SPAWN_CALLEES, _callee, _normalize

from personalclaw.dashboard.handlers import files as F

# The stub git outlives the bound by a wide margin so a loaded host cannot flip the
# comparison: the fixed path returns at its (injected) deadline, the broken one waits
# out the grandchild.
_GRANDCHILD_SECS = 8
_BOUND_SECS = 4.0


def _mock() -> MagicMock:
    return MagicMock()


def _req(route: str, path: str):
    return make_mocked_request("GET", f"/api/{route}?path={quote(path)}")


def _repo_with_commit(tmp_path):
    """A repo whose HEAD holds ``tracked.txt`` and a ``sub/`` directory with two files."""
    repo = tmp_path / "orig-repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "tracked.txt").write_text("v1\n")
    (repo / "sub" / "a.txt").write_text("a\n")
    (repo / "sub" / "b.txt").write_text("b\n")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)
    return repo


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


def _slow_git(tmp_path, monkeypatch, name="bin"):
    """Put a ``git`` on PATH that never answers AND forks a grandchild.

    The fork is the point: git plumbing really does fork (an fsmonitor hook, an LFS
    filter driver, a remote helper), the grandchild inherits the stdout pipe, and
    ``Process.wait()`` resolves on pipe disconnect rather than on reaping. A pid-only
    kill therefore waits out the grandchild's whole runtime under the timeout's name.
    """
    fake_bin = tmp_path / name
    fake_bin.mkdir()
    git_stub = fake_bin / "git"
    # /bin/sleep by ABSOLUTE path: PATH below holds only this dir, so a bare `sleep`
    # is not found and the stub would exit 0 instantly. That is exactly how the
    # pre-fix version of the reap test below passed while inducing no timeout at all.
    git_stub.write_text(f"#!/bin/sh\n/bin/sleep {_GRANDCHILD_SECS} & wait\n")
    git_stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))  # _git calls bare "git" → our stub
    return fake_bin


def _group_is_empty(pgid: int, *, deadline: float = 2.0) -> bool:
    """True once no process remains in *pgid*. Polls — SIGKILL delivery is not instant."""
    import os
    import time

    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:  # pragma: no cover - someone else owns it now
            return True
        time.sleep(0.05)
    return False


def _spy_spawn(monkeypatch) -> dict:
    """Record every process ``asyncio.create_subprocess_exec`` hands the code under test."""
    seen: dict = {"procs": []}
    real = asyncio.create_subprocess_exec

    async def spy(*a, **k):
        p = await real(*a, **k)
        seen["procs"].append(p)
        seen["kwargs"] = k
        return p

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    return seen


def test_git_helper_reaps_child_on_timeout(tmp_path, monkeypatch):
    """A blown deadline leaves NOTHING running and returns AT the deadline.

    Both halves matter and each was measured on the pre-fix code. Reaping the direct
    child alone left the grandchild holding the inherited pipe, which made the reap
    itself unbounded: one git-status request with a 1.00s deadline took **135.48s**.
    Fast-because-we-stopped-waiting is the other failure — that is the process leak —
    so the group must be empty too.
    """
    _slow_git(tmp_path, monkeypatch)

    async def run():
        seen = _spy_spawn(monkeypatch)
        started = time.monotonic()
        res = await F._git(["status"], str(tmp_path), timeout=0.2)
        elapsed = time.monotonic() - started

        assert res.ok is False and res.out == ""  # timed out → no answer
        # VACUITY FLOOR. The stub must actually have OVERRUN the deadline. Without this
        # the test passes on a stub that exits instantly — which is what the earlier
        # version of it did (its `sleep` was not on the narrowed PATH), so it asserted
        # reaping about a child that had already exited on its own.
        assert elapsed >= 0.2, (
            f"the stub git returned in {elapsed:.2f}s, inside the 0.2s deadline — no "
            "timeout was induced, so this test proves nothing"
        )
        p = seen["procs"][0]
        assert p.returncode is not None, "the killed child was never reaped (zombie)"
        assert seen["kwargs"].get("start_new_session") is True, (
            "_git no longer asks for its own session, so kill_timed_out falls back to a "
            "single-pid signal and a forking git's grandchild survives holding the pipe"
        )
        assert elapsed < _BOUND_SECS, (
            f"the 0.2s deadline took {elapsed:.2f}s to return — the post-kill reap waited "
            f"for the grandchild's inherited pipe instead of the child's exit"
        )
        assert _group_is_empty(p.pid), (
            f"process group {p.pid} still has members after the timeout — the grandchild "
            "outlived the kill (one leaked pair per timed-out poll)"
        )

    asyncio.run(run())


def test_git_original_kills_and_reaps_a_timed_out_read(tmp_path, monkeypatch):
    """#432's leak, at the endpoint: the copy killed nothing and reaped nothing.

    MEASURED on the pre-fix handler, three requests against a `git` that never answers:
    3 live ``git`` children and 3 live grandchildren afterwards — two orphans per
    timeout, accumulating for as long as the diff view is opened. The deadline is
    INJECTED (``_GIT_SHOW_TIMEOUT``), never slept out.
    """
    repo = _repo_with_commit(tmp_path)
    fp = str(repo / "tracked.txt")
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw: fp if raw == fp else None)
    monkeypatch.setattr(F, "_path_within_roots", lambda p: True)
    monkeypatch.setattr(F, "_sel", lambda: _mock())
    monkeypatch.setattr(F, "_GIT_SHOW_TIMEOUT", 0.3)
    _slow_git(tmp_path, monkeypatch)

    async def run():
        seen = _spy_spawn(monkeypatch)
        started = time.monotonic()
        resp = await F.api_file_git_original(_req("file-git-original", fp))
        elapsed = time.monotonic() - started

        assert resp.status == 200
        assert json.loads(resp.body.decode()) == {"content": "", "exists": False}
        assert seen["procs"], "the endpoint never spawned git — the test proves nothing"
        assert elapsed >= 0.3, (
            f"the stub git returned in {elapsed:.2f}s, inside the injected 0.3s deadline "
            "— no timeout was induced, so this test proves nothing"
        )
        p = seen["procs"][0]
        assert p.returncode is not None, "the timed-out git was never reaped"
        assert elapsed < _BOUND_SECS, f"the 0.3s deadline took {elapsed:.2f}s to return"
        assert _group_is_empty(p.pid), (
            f"process group {p.pid} survived the timeout — this is the #432 leak: an "
            "orphaned git (plus its grandchild) per timed-out diff-view read"
        )

    asyncio.run(run())


def test_git_original_degrades_when_git_is_not_executable(tmp_path, monkeypatch):
    """An unexecutable git is a REFUSAL, not a crash.

    MEASURED on the pre-fix handler: ``PermissionError: [Errno 13] Permission denied:
    'git'`` escaped the handler and the request answered **HTTP 500 "Server got itself
    in trouble"** — while the sibling endpoint, which already routed through ``_git``,
    answered 200 with an empty payload. PATH holds ONLY the unexecutable git: POSIX
    ``execvp`` remembers EACCES and keeps searching, so a real git further along PATH
    would mask the failure entirely.
    """
    repo = _repo_with_commit(tmp_path)
    fp = str(repo / "tracked.txt")
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw: fp if raw == fp else None)
    monkeypatch.setattr(F, "_path_within_roots", lambda p: True)
    monkeypatch.setattr(F, "_sel", lambda: _mock())

    bad_bin = tmp_path / "badbin"
    bad_bin.mkdir()
    stub = bad_bin / "git"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o000)
    monkeypatch.setenv("PATH", str(bad_bin))

    resp = asyncio.run(F.api_file_git_original(_req("file-git-original", fp)))
    assert resp.status == 200
    assert json.loads(resp.body.decode()) == {"content": "", "exists": False}


def test_git_original_refuses_a_directory(tmp_path, monkeypatch):
    """A directory has no committed CONTENT — and must not be served a tree listing.

    MEASURED on the pre-fix handler (``git show HEAD:<dir>`` exits 0 and prints the
    tree): ``{"content": "tree HEAD:sub\\n\\na.txt\\nb.txt\\n", "exists": true}`` — the
    diff view rendered a directory index as the original side of a file.
    """
    repo = _repo_with_commit(tmp_path)
    for target in (str(repo / "sub"), str(repo)):
        monkeypatch.setattr(
            F, "_validate_dashboard_path", lambda raw, t=target: t if raw == t else None
        )
        monkeypatch.setattr(F, "_path_within_roots", lambda p: True)
        monkeypatch.setattr(F, "_sel", lambda: _mock())
        resp = asyncio.run(F.api_file_git_original(_req("file-git-original", target)))
        assert resp.status == 200
        body = json.loads(resp.body.decode())
        assert body == {"content": "", "exists": False}, f"{target} answered {body}"


def test_git_original_reports_a_file_absent_from_head(tmp_path, monkeypatch):
    """A newly added file has no committed side — the diff is against empty.

    This is the endpoint's documented contract and the reason the read's EXIT STATUS
    has to be carried back to the handler: routing through ``_git`` and reading only
    its stdout would answer ``{"content": "", "exists": true}`` here, which the diff
    view renders as "the file used to be empty" rather than "the file is new".
    """
    repo = _repo_with_commit(tmp_path)
    (repo / "brand-new.txt").write_text("fresh\n")
    fp = str(repo / "brand-new.txt")
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw: fp if raw == fp else None)
    monkeypatch.setattr(F, "_path_within_roots", lambda p: True)
    monkeypatch.setattr(F, "_sel", lambda: _mock())
    resp = asyncio.run(F.api_file_git_original(_req("file-git-original", fp)))
    assert json.loads(resp.body.decode()) == {"content": "", "exists": False}


def test_git_original_still_serves_a_committed_file(tmp_path, monkeypatch):
    """The control for the three above: the happy path is unchanged."""
    repo = _repo_with_commit(tmp_path)
    fp = str(repo / "sub" / "a.txt")
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw: fp if raw == fp else None)
    monkeypatch.setattr(F, "_path_within_roots", lambda p: True)
    monkeypatch.setattr(F, "_sel", lambda: _mock())
    resp = asyncio.run(F.api_file_git_original(_req("file-git-original", fp)))
    assert json.loads(resp.body.decode()) == {
        "content": "a\n",
        "exists": True,
        "truncated": False,
    }


# ── the rail: one git invoker in this module, derived from the AST ──


def _spawns_git(node) -> bool:
    """True iff *node* is a process spawn whose argv[0] is the literal ``"git"``.

    The spawn vocabulary and the callee matcher are imported from
    ``test_spawn_ceiling_audit`` rather than re-listed, so a new spawn shape has to be
    taught to the tree-wide census and this rail at once — one place, not two.
    """
    import ast

    if not isinstance(node, ast.Call) or not node.args:
        return False
    if _normalize(_callee(node)) not in _SPAWN_CALLEES:
        return False
    first = node.args[0]
    if isinstance(first, ast.List) and first.elts:
        first = first.elts[0]
    return isinstance(first, ast.Constant) and first.value == "git"


def _git_spawning_functions() -> set[str]:
    """Names of the functions in files.py that spawn ``git``. Derived from the AST."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(F.__file__).read_text())
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}

    def enclosing(node):
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur.name
            cur = parents.get(cur)
        return "<module>"

    return {enclosing(n) for n in ast.walk(tree) if _spawns_git(n)}


def test_files_has_exactly_one_git_invoker():
    """Every ``git`` spawn in files.py must live in ``_git``. Derived, not hand-listed.

    #432 was one function re-implementing ``_git``: the copy leaked a process per
    timeout, 500'd on an unexecutable git, and served a tree listing as a file's
    content. Deleting the copy fixes those three; this keeps a fourth from being
    written. The callee vocabulary is the spawn-census one (``test_spawn_ceiling_audit``
    owns the tree-wide version), so a new spawn shape is caught in both places.
    """
    spawners = _git_spawning_functions()

    assert spawners == {"_git"}, (
        f"git is spawned outside _git in files.py: {sorted(spawners - {'_git'})}. Route it "
        "through _git — that is where the kill+reap of a blown deadline, the degrade when "
        "git cannot be executed, and the redaction of git's output all live. A second "
        "invoker re-earns #432 one forgotten branch at a time."
    )


def test_the_git_invoker_matcher_is_not_vacuous():
    """VACUITY: the rail must SEE a hand-rolled git spawn, and must ignore a non-git one.

    Both directions. A matcher that matched nothing would let the rail above pass by
    inspecting an empty set, which is the failure mode a census rail is most prone to.
    """
    import ast

    sneaky = ast.parse(
        "import asyncio\n"
        "async def api_sneaky():\n"
        "    proc = await asyncio.create_subprocess_exec('git', 'show', 'HEAD:x')\n"
    )
    assert [
        n for n in ast.walk(sneaky) if _spawns_git(n)
    ], "the matcher would not notice a hand-rolled git spawn — the rail above is vacuous"
    other = ast.parse(
        "import asyncio\n"
        "async def f():\n"
        "    proc = await asyncio.create_subprocess_exec('rg', '--json')\n"
    )
    assert not [n for n in ast.walk(other) if _spawns_git(n)], "rg is not a git invocation"
    # And the rail must find the real one, so `== {"_git"}` is not `== set()` passing.
    assert "_git" in _git_spawning_functions()


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
