"""A code session's owner commits its workspace's uncommitted changes from the session (#3821).

``POST /api/loops/{id}/commit {message}`` commits what the code session's workspace holds and has
not committed, with the message its owner wrote. The workspace is the session's own, read from the
loop on the server, never from anything the request names. Only you commit: an agent's tool and an
app are refused. A loop still at work is refused too, since its supervisor commits each stage and
merges each task into that tree. The message is required and capped. It commits and pushes nothing,
as git is configured to commit there, and, as every git the gateway runs in a repository an agent
can write, it runs no program the repository names, a hook included. Its audit row names the loop
and the commit.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import loop_routes as H
from personalclaw.loop import store
from personalclaw.loop.loop import Loop, LoopStatus

#: Who may commit, and into which tree, is an authorization boundary.
pytestmark = pytest.mark.security


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    yield tmp_path


def _git(ws, home, *args):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    return subprocess.run(
        ["git", "-c", "credential.helper=", *args],
        cwd=ws, check=True, capture_output=True, env=env, text=True,
    )  # fmt: skip


def _repo(home, name):
    ws = home / name
    ws.mkdir()
    (ws / "feed.py").write_text("def refetch():\n    pass\n")
    _git(ws, home, "init", "-q")
    _git(ws, home, "config", "user.name", "t")
    _git(ws, home, "config", "user.email", "t@example.com")
    _git(ws, home, "add", "feed.py")
    _git(ws, home, "commit", "-q", "-m", "init")
    # What the session left uncommitted.
    (ws / "feed.py").write_text("def refetch():\n    return retry()\n")
    (ws / "retry.py").write_text("def retry():\n    return True\n")
    return ws


def _session(ws, status=LoopStatus.COMPLETE):
    loop = store.create(
        Loop(id="", name="feed", kind="code", task="fix the refetch", workspace_dir=str(ws))
    )
    store.update_status(loop.id, status)
    return store.get(loop.id)


def _head(ws, home):
    return _git(ws, home, "log", "-1", "--format=%H %s").stdout.strip()


def _dirty(ws, home):
    return _git(ws, home, "status", "--porcelain").stdout.strip()


class _Audit:
    def __init__(self):
        self.rows: list[dict] = []

    def log_api_access(self, **kw):
        self.rows.append(kw)


@pytest.fixture()
def audit(monkeypatch):
    a = _Audit()
    monkeypatch.setattr("personalclaw.sel.sel", lambda: a)
    return a


def _commit(loop_id, body, *, who="you"):
    app = web.Application()
    app["state"] = None
    headers = {"X-Internal-Secret": "s", "X-Session-Key": "chat-1"} if who == "agent" else {}
    req = make_mocked_request(
        "POST", f"/api/loops/{loop_id}/commit", match_info={"id": loop_id}, app=app, headers=headers
    )
    if who == "you":
        req["user"] = "owner"
    if who == "app":
        req["app"] = "some-app"

    async def _json():
        return body

    req.json = _json
    resp = _run(H.api_loop_commit(req))
    return resp.status, json.loads(resp.body)


def test_you_commit_the_sessions_changes_with_your_message(tmp_path, audit):
    ws = _repo(tmp_path, "feed")
    loop = _session(ws)

    status, body = _commit(loop.id, {"message": "Retry the refetch"})

    assert status == 200, body
    sha, subject = _head(ws, tmp_path).split(" ", 1)
    assert body["sha"] == sha
    assert subject == "Retry the refetch"
    assert _dirty(ws, tmp_path) == "", "every change, the new file included, is committed"
    assert any(
        r.get("operation") == "loop.commit"
        and loop.id in r.get("resources", "")
        and sha in r["resources"]
        for r in audit.rows
    )


@pytest.mark.parametrize("who", ["agent", "app"])
def test_an_agent_or_an_app_is_refused(tmp_path, audit, who):
    ws = _repo(tmp_path, "feed")
    loop = _session(ws)
    before = _head(ws, tmp_path)

    status, body = _commit(loop.id, {"message": "Retry the refetch"}, who=who)

    assert status == 403
    assert body["error"]["code"] == "commit_owner_only"
    assert _head(ws, tmp_path) == before
    assert _dirty(ws, tmp_path) != ""


@pytest.mark.parametrize("status", [LoopStatus.RUNNING, LoopStatus.NEEDS_INPUT, LoopStatus.PAUSED])
def test_a_loop_still_at_work_is_refused(tmp_path, audit, status):
    ws = _repo(tmp_path, "feed")
    loop = _session(ws, status=status)
    before = _head(ws, tmp_path)

    code, body = _commit(loop.id, {"message": "Retry the refetch"})

    assert code == 409
    assert body["error"]["code"] == "loop_still_at_work"
    assert _head(ws, tmp_path) == before


def test_a_path_the_request_names_is_not_where_it_commits(tmp_path, audit):
    ws = _repo(tmp_path, "feed")
    other = _repo(tmp_path, "elsewhere")
    loop = _session(ws)
    other_before = _head(other, tmp_path)

    status, _ = _commit(
        loop.id, {"message": "Retry the refetch", "path": str(other), "workspace_dir": str(other)}
    )

    assert status == 200
    assert _head(ws, tmp_path).endswith("Retry the refetch")
    assert _head(other, tmp_path) == other_before, "the other repository is untouched"
    assert _dirty(other, tmp_path) != ""


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("", "commit_message_required"),
        ("   \n ", "commit_message_required"),
        ("x" * 5001, "commit_message_too_long"),
    ],
    ids=["empty", "blank", "too-long"],
)
def test_a_missing_or_overlong_message_is_a_400(tmp_path, audit, message, code):
    ws = _repo(tmp_path, "feed")
    loop = _session(ws)
    before = _head(ws, tmp_path)

    status, body = _commit(loop.id, {"message": message})

    assert status == 400
    assert body["error"]["code"] == code
    assert _head(ws, tmp_path) == before


def test_a_clean_tree_has_nothing_to_commit(tmp_path, audit):
    ws = _repo(tmp_path, "feed")
    _git(ws, tmp_path, "add", "-A")
    _git(ws, tmp_path, "commit", "-q", "-m", "done")
    loop = _session(ws)

    status, body = _commit(loop.id, {"message": "Retry the refetch"})

    assert status == 409
    assert body["error"]["code"] == "nothing_to_commit"


def test_the_repositorys_hooks_do_not_run_as_the_gateway(tmp_path, audit):
    ws = _repo(tmp_path, "feed")
    ran = tmp_path / "hook-ran"
    hook = ws / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {ran}\n")
    hook.chmod(0o755)
    loop = _session(ws)

    status, _ = _commit(loop.id, {"message": "Retry the refetch"})

    assert status == 200
    assert not ran.exists(), "an agent can write .git/hooks; the gateway's git runs none"
