"""A change two task workers made lands in the workspace once, and no task is started without the
work it builds on.

Measured on an Attended Code loop over a README: "Draft the section" committed its change on its
task branch and was marked done while its worker's turn was still running, so it was not merged
yet; "Apply the section", which depends on it, was started in that gap, in a worktree cut from a
workspace without the draft. Its worker found the draft on the other branch and cherry-picked it.
The scheduler then fast-forwarded the draft and merged the copy, so the workspace's history
carried the same change twice, joined by a merge commit that changed nothing.

Now a task waits until the work of each prerequisite marked done is merged, and a task whose
branch brings nothing the workspace lacks merges nothing: no commit and no merge commit, its
worktree and branch removed, and the task done. Real repositories and worktrees throughout.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from types import SimpleNamespace

import pytest

from personalclaw.loop import kinds, store, tasks_link, worktree
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.loop.manager import session_key, task_session_key

SECTION = "## Storage\n\nPostgres is set with FEEDSMITH_DB; see migrations/.\n"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_home(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    kinds.ensure_loaded()
    yield tmp_path
    kinds.get("code")._stood_down.clear()


def _git(cwd, home, *args):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    return subprocess.run(
        ["git", "-c", "credential.helper=", "-c", "user.name=t", "-c", "user.email=t@example.com",
         *args],
        cwd=cwd, check=True, capture_output=True, env=env, text=True,
    ).stdout  # fmt: skip


@pytest.fixture()
def repo(tmp_path):
    ws = tmp_path / "feedsmith"
    ws.mkdir()
    (ws / "README.md").write_text("# feedsmith\n\n## Storage\n\nSQLite.\n")
    _git(ws, tmp_path, "init", "-q", "-b", "main")
    # The identity its owner configured: every commit the loop makes here carries it.
    _git(ws, tmp_path, "config", "user.name", "t")
    _git(ws, tmp_path, "config", "user.email", "t@example.com")
    _git(ws, tmp_path, "add", "README.md")
    _git(ws, tmp_path, "commit", "-q", "-m", "init")
    return ws


def _history(ws) -> list[str]:
    """The workspace's commits, newest first, a merge commit marked as one."""
    out = _git(ws, ws.parent, "log", "--format=%p|%s")
    rows = [ln.split("|", 1) for ln in out.splitlines()]
    return [("merge: " if " " in parents else "") + subject for parents, subject in rows]


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _worker_writes(ws, tid, text=SECTION, message="docs: document Postgres storage"):
    wt = worktree.worktree_path(str(ws), tid)
    _write(os.path.join(wt, "README.md"), "# feedsmith\n\n" + text)
    _git(wt, ws.parent, "commit", "-q", "-am", message)
    return wt


# ── a change already on the workspace merges nothing ──────────────────────────


def test_two_workers_that_made_the_same_change_leave_one_commit(repo):
    for tid in ("t-draft", "t-apply"):
        assert worktree.add_worktree(str(repo), tid)
        _worker_writes(repo, tid)

    first = worktree.merge_worktree(str(repo), "t-draft")
    second = worktree.merge_worktree(str(repo), "t-apply")

    assert _history(repo) == ["docs: document Postgres storage", "init"]
    assert first.ok and not first.already
    assert second.ok and second.already, "the second copy of the change was merged"
    assert not worktree.branch_exists(str(repo), "t-apply")
    assert not os.path.isdir(worktree.worktree_path(str(repo), "t-apply"))
    assert "FEEDSMITH_DB" in (repo / "README.md").read_text()


def test_a_cherry_picked_copy_of_merged_work_merges_nothing(repo):
    assert worktree.add_worktree(str(repo), "t-draft")
    _worker_writes(repo, "t-draft")
    assert worktree.add_worktree(str(repo), "t-apply")
    draft = _git(repo, repo.parent, "rev-parse", worktree.branch_name("t-draft")).strip()
    _git(worktree.worktree_path(str(repo), "t-apply"), repo.parent, "cherry-pick", draft)

    assert worktree.merge_worktree(str(repo), "t-draft").ok
    merged = worktree.merge_worktree(str(repo), "t-apply")

    assert _history(repo) == ["docs: document Postgres storage", "init"]
    assert merged.ok and merged.already


def test_the_same_change_made_in_two_steps_merges_nothing_either(repo):
    assert worktree.add_worktree(str(repo), "t-draft")
    _worker_writes(repo, "t-draft")
    assert worktree.add_worktree(str(repo), "t-apply")
    half = "## Storage\n\nPostgres is set with FEEDSMITH_DB.\n"
    _worker_writes(repo, "t-apply", half, "docs: name the variable")
    _worker_writes(repo, "t-apply", SECTION, "docs: link migrations")

    assert worktree.merge_worktree(str(repo), "t-draft").ok
    merged = worktree.merge_worktree(str(repo), "t-apply")

    assert _history(repo) == ["docs: document Postgres storage", "init"]
    assert merged.ok and merged.already


def test_a_branch_with_something_new_still_merges(repo):
    for tid in ("t-draft", "t-more"):
        assert worktree.add_worktree(str(repo), tid)
    _worker_writes(repo, "t-draft")
    _write(os.path.join(worktree.worktree_path(str(repo), "t-more"), "NOTES.md"), "Postgres\n")

    assert worktree.merge_worktree(str(repo), "t-draft").ok
    more = worktree.merge_worktree(str(repo), "t-more")

    assert more.ok and not more.already
    assert (repo / "NOTES.md").is_file()
    assert _history(repo)[0].startswith("merge: ")


def test_kept_work_already_on_the_workspace_is_not_kept(repo):
    """At a run's end, a task branch whose change the workspace has is nothing to keep."""
    for tid in ("t-draft", "t-apply"):
        assert worktree.add_worktree(str(repo), tid)
        _worker_writes(repo, tid)
    assert worktree.merge_worktree(str(repo), "t-draft").ok

    (apply,) = worktree.task_work(str(repo), ["t-apply"])
    assert apply.commits == 0 and not apply.unmerged
    assert worktree.sweep_finished(str(repo), ["t-apply"]) == []


# ── a task is not started without the work it builds on ───────────────────────


class _Svc:
    def __init__(self):
        self._loops: dict[str, SimpleNamespace] = {}
        self._n = 0

    async def add(self, *, session_name, **_kw):
        self._n += 1
        row = SimpleNamespace(id=f"N{self._n}", session_name=session_name, active=True)
        self._loops[row.id] = row
        return row

    def get_by_session(self, session_name):
        return next((r for r in self._loops.values() if r.session_name == session_name), None)

    def list_all(self):
        return list(self._loops.values())

    async def update(self, row_id, **kw):
        for k, v in kw.items():
            setattr(self._loops[row_id], k, v)

    async def remove(self, row_id):
        self._loops.pop(row_id, None)


class _State:
    def __init__(self):
        self._sessions: dict = {}

    def get_or_create_session(self, *, name, **_kw):
        return self._sessions.setdefault(
            name,
            SimpleNamespace(
                key=name, running=False, messages=[], acp_provider="", acp_provider_agent="",
                reasoning_effort="", acp_mode="", _extra_tool_roots=None,
            ),
        )  # fmt: skip

    def push_sessions_update(self):
        pass


class _Ctx:
    def __init__(self, state, svc):
        self.state, self.svc, self.events = state, svc, []

    def publish(self, loop_id, event, data=None):
        self.events.append((event, data))

    async def complete(self, loop_id, reason=""):
        pass


def _readme_loop(repo) -> Loop:
    loop = store.create(
        Loop(
            id="",
            name="readme storage",
            kind="code",
            task="document the Postgres storage in the README",
            # An Unattended loop's: its scheduler merges each task's work as the task finishes.
            attended=False,
            autopilot=True,
            workspace_dir=str(repo),
            plan=[
                {
                    "stage": "implementation",
                    "title": "Implementation",
                    "tasks": [
                        {"title": "Draft the Storage section"},
                        {"title": "Apply the section to README.md", "depends_on": [0]},
                    ],
                }
            ],
            phase_status={"implementation": "active"},
        )
    )
    tasks_link.provision(loop.id)
    _run(tasks_link.seed_phase_tasks(loop.id))
    return store.update_status(loop.id, LoopStatus.RUNNING)


def test_a_task_waits_for_the_work_it_builds_on_to_be_merged(repo):
    from personalclaw.tasks import registry

    loop = _readme_loop(repo)
    state, svc = _State(), _Svc()
    state.get_or_create_session(name=session_key(loop.id))
    _run(svc.add(session_name=session_key(loop.id)))
    code = kinds.get("code")

    def _schedule():
        _run(code.schedule(store.get(loop.id), _Ctx(state, svc)))

    def _worktree(tid):
        return worktree.worktree_path(str(repo), tid, store.get(loop.id).tasks_project_id)

    _schedule()
    list_id = tasks_link.phase_list_id(loop, "implementation")
    tasks, _ = _run(registry.collect_tasks(task_list_id=list_id))
    by_title = {t.title: t.id for t in tasks}
    draft, apply = by_title["Draft the Storage section"], by_title["Apply the section to README.md"]
    assert set(store.get(loop.id).kind_config["queued_task_ids"]) == {draft, apply}
    assert svc.get_by_session(task_session_key(loop.id, draft)) is not None
    assert svc.get_by_session(task_session_key(loop.id, apply)) is None

    # The draft's worker makes its change and marks its task done; its turn is not over yet.
    _write(os.path.join(_worktree(draft), "README.md"), "# feedsmith\n\n" + SECTION)
    state.get_or_create_session(name=task_session_key(loop.id, draft)).running = True
    _run(registry.update_task(draft, provider_name="native", status="done"))
    _schedule()

    assert (
        svc.get_by_session(task_session_key(loop.id, apply)) is None
    ), "the dependent task was started without the work it builds on"
    assert not os.path.isdir(_worktree(apply))

    state.get_or_create_session(name=task_session_key(loop.id, draft)).running = False
    _schedule()  # the draft is merged
    _schedule()  # and the task that builds on it starts, on a workspace that has it

    assert "FEEDSMITH_DB" in (repo / "README.md").read_text()
    assert svc.get_by_session(task_session_key(loop.id, apply)) is not None
    with open(os.path.join(_worktree(apply), "README.md"), encoding="utf-8") as fh:
        assert "FEEDSMITH_DB" in fh.read(), "its worktree was cut without the draft"
