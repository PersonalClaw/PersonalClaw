"""An Attended code loop merges a task's work into its owner's branch only once they approve it,
and every commit a loop makes carries the owner's own configured git identity.

Measured on an Attended Code loop working in parallel: it committed three task commits and two
merge commits straight into its owner's ``main``, with no approval card and no notice, and the
merges were authored "PersonalClaw <code@personalclaw.local>" — an identity of the product's own,
which would have gone out with their next push. Afterwards the working tree was clean, so nothing
on the page was left to review.

Now an Attended loop's finished work waits, committed on its own branch as git is configured to
commit there, and the loop pauses with a review of it (each task's branch, its commits and its
diff); the scheduler merges it at the commit its owner approved. An Unattended loop merges as each
task finishes, also under the owner's identity. With no identity configured the loop asks for one
instead of inventing one. Every merge is recorded for the loop's page.

Driven against real git repositories and worktrees.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds, store, worktree
from personalclaw.loop.loop import Loop, LoopStatus

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

OWNER = ("Ada Example", "ada@example.com")
TASK = "t-0a1b2c3d"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path / "home")
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path / "home")
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path / "home")
    # git as the owner's machine has it, with no machine-wide settings and no global identity of
    # the test runner's: the repository's own settings are all there is.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty.gitconfig"))
    monkeypatch.setenv("HOME", str(tmp_path / "owner"))
    # …and no identity in the environment either (the suite sets one for every test), so a
    # commit made here carries the name it was made with.
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    kinds.ensure_loaded()
    return tmp_path


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path, *, identity: bool = True) -> Path:
    """The owner's codebase: one commit on ``main``, and (when *identity*) their name and email
    configured in it the way they would set them."""
    repo = tmp_path / "feedsmith"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "digest.py").write_text("TITLE = 'Q&A'\n")
    _git(repo, "add", "digest.py")
    _git(
        repo,
        "-c",
        "user.name=Ada Example",
        "-c",
        "user.email=ada@example.com",
        "commit",
        "-qm",
        "first",
    )
    if identity:
        _git(repo, "config", "user.name", OWNER[0])
        _git(repo, "config", "user.email", OWNER[1])
    return repo


def _loop(repo: Path, *, attended: bool) -> Loop:
    loop = store.create(
        Loop(
            id="",
            name="Digest titles",
            kind="code",
            task="stop escaping digest titles twice",
            attended=attended,
            workspace_dir=str(repo),
            plan=[{"stage": "implementation", "title": "Fix"}],
            kind_config={"entry_stage": "implementation", "queued_task_ids": [TASK]},
        )
    )
    store.update_status(loop.id, LoopStatus.RUNNING)
    return store.get(loop.id)


def _finished_task_worktree(repo: Path) -> Path:
    """A task worker's checkout after it finished: its edit is there, not yet committed."""
    path = Path(worktree.add_worktree(str(repo), TASK))
    (path / "digest.py").write_text("TITLE = 'Q&A'  # escaped once, by the template\n")
    return path


class _Ctx:
    def __init__(self):
        self.state = SimpleNamespace(_sessions={})
        self.svc = SimpleNamespace(get_by_session=lambda _k: None, list_all=lambda: [])
        self.events: list[str] = []

    def publish(self, _loop_id, event, _data=None):
        self.events.append(event)


@pytest.fixture
def code(monkeypatch):
    """The code kind, with its task store answering that the task is done, and none of another
    test's approvals."""
    strat = kinds.get("code")
    monkeypatch.setattr(strat, "_merge_approvals", {})

    async def _done(_tid):
        return SimpleNamespace(id=TASK, title="Escape titles once", status="done")

    monkeypatch.setattr(strat, "_get_task", _done)
    return strat


def _schedule(strat, loop: Loop, ctx: _Ctx) -> bool:
    """One pass of the code kind's scheduler, as the watchdog asks it every poll."""
    return asyncio.get_event_loop().run_until_complete(strat.schedule(store.get(loop.id), ctx))


def _authors(repo: Path, rev_range: str) -> set[str]:
    return set(_git(repo, "log", "--format=%an <%ae>|%cn <%ce>", rev_range).splitlines())


# ── Attended: nothing lands on the owner's branch until they approve it ──────────────────


def test_an_attended_loops_finished_work_waits_for_its_owner(tmp_path, code):
    repo = _repo(tmp_path)
    loop = _loop(repo, attended=True)
    _finished_task_worktree(repo)
    before = _git(repo, "rev-parse", "main")
    ctx = _Ctx()

    paused = _schedule(code, loop, ctx)

    assert paused is True and store.get(loop.id).status == "needs_input"
    assert _git(repo, "rev-parse", "main") == before, "the loop put work on main unasked"
    asked = loop_files.pending_question(loop.id)
    assert asked and asked["merge"]["into"] == "main", asked
    (waiting,) = asked["merge"]["tasks"]
    assert (waiting["task_id"], waiting["title"]) == (TASK, "Escape titles once")
    assert waiting["tip"] == worktree.branch_tip(str(repo), TASK)
    assert "ready to merge into main" in asked["question"]
    # Its work was committed on its own branch, under the owner's own name, so the review shows
    # exactly what would land.
    assert _authors(repo, f"main..{worktree.branch_name(TASK)}") == {
        "Ada Example <ada@example.com>|Ada Example <ada@example.com>"
    }
    review = worktree.merge_review(str(repo), [TASK])
    assert "escaped once, by the template" in review["tasks"][0]["diff"]
    assert ctx.events == ["needs_input"]
    assert loop_files.get_merges(loop.id) == []


def test_the_owners_approval_merges_the_work_under_their_name(tmp_path, code):
    repo = _repo(tmp_path)
    loop = _loop(repo, attended=True)
    _finished_task_worktree(repo)
    ctx = _Ctx()
    _schedule(code, loop, ctx)
    tip = worktree.branch_tip(str(repo), TASK)

    # What the review page's Merge does: the approval at the commit it showed, then Resume.
    code.approve_merge(loop.id, {TASK: tip})
    loop_files.clear_question(loop.id)
    store.update_status(loop.id, LoopStatus.RUNNING)
    _schedule(code, loop, ctx)

    assert _git(repo, "merge-base", "--is-ancestor", tip, "main") == ""
    assert "escaped once" in (repo / "digest.py").read_text()
    assert not worktree.branch_exists(str(repo), TASK)
    assert all(
        a == "Ada Example <ada@example.com>|Ada Example <ada@example.com>"
        for a in _authors(repo, "main~1..main")
    ), _authors(repo, "main~1..main")
    (merged,) = loop_files.get_merges(loop.id)
    assert (merged["task_id"], merged["into"], merged["by"]) == (TASK, "main", "you")
    assert merged["head"] == _git(repo, "rev-parse", "main")
    assert merged["commits"] and merged["commits"][0].endswith(f"task {TASK}: work")


def test_work_that_moved_after_the_review_is_not_merged_on_its_approval(tmp_path, code):
    repo = _repo(tmp_path)
    loop = _loop(repo, attended=True)
    path = _finished_task_worktree(repo)
    ctx = _Ctx()
    _schedule(code, loop, ctx)
    reviewed = worktree.branch_tip(str(repo), TASK)
    # The branch moves after the owner looked at it.
    (path / "extra.py").write_text("x = 1\n")
    _git(path, "add", "extra.py")
    _git(
        path,
        "-c",
        "user.name=Ada Example",
        "-c",
        "user.email=ada@example.com",
        "commit",
        "-qm",
        "more",
    )
    before = _git(repo, "rev-parse", "main")

    code.approve_merge(loop.id, {TASK: reviewed})
    loop_files.clear_question(loop.id)
    store.update_status(loop.id, LoopStatus.RUNNING)
    paused = _schedule(code, loop, ctx)

    assert paused is True and _git(repo, "rev-parse", "main") == before
    assert loop_files.pending_question(loop.id)["merge"]["tasks"][0]["tip"] != reviewed


def test_an_attended_loop_with_finished_work_waiting_starts_nothing_on_top_of_it(
    tmp_path, code, monkeypatch
):
    from personalclaw.loop import tasks_link

    repo = _repo(tmp_path)
    loop = _loop(repo, attended=True)
    _finished_task_worktree(repo)
    started: list[str] = []

    async def _ready(_loop, _stage):
        return [SimpleNamespace(id="t-11111111", title="Next", action_plan=[], description="")]

    async def _spawn(*_a, **_k):
        started.append("t-11111111")

    monkeypatch.setattr(tasks_link, "ready_queued_tasks", _ready)
    monkeypatch.setattr("personalclaw.loop.manager.spawn_task_worker", _spawn)

    _schedule(code, loop, _Ctx())

    assert started == [], "a task was cut from a branch that lacks the work waiting on it"


# ── Unattended: merged as each task finishes, under the owner's name ─────────────────────


def test_an_unattended_loop_merges_by_itself_under_the_owners_name(tmp_path, code):
    repo = _repo(tmp_path)
    loop = _loop(repo, attended=False)
    _finished_task_worktree(repo)

    paused = _schedule(code, loop, _Ctx())

    assert paused is False and store.get(loop.id).status == "running"
    assert "escaped once" in (repo / "digest.py").read_text()
    assert all(
        a == "Ada Example <ada@example.com>|Ada Example <ada@example.com>"
        for a in _authors(repo, "main~1..main")
    ), _authors(repo, "main~1..main")
    assert "personalclaw" not in _git(repo, "log", "--format=%an %ae %cn %ce").lower()
    (merged,) = loop_files.get_merges(loop.id)
    assert merged["by"] == "the loop"


# ── no identity: asked for, never invented ───────────────────────────────────────────────


@pytest.mark.parametrize("attended", [True, False])
def test_with_no_identity_configured_the_loop_asks_for_one(tmp_path, code, attended):
    repo = _repo(tmp_path, identity=False)
    loop = _loop(repo, attended=attended)
    _finished_task_worktree(repo)
    before = _git(repo, "rev-parse", "main")

    paused = _schedule(code, loop, _Ctx())

    assert paused is True and store.get(loop.id).status == "needs_input"
    assert _git(repo, "rev-parse", "main") == before
    assert worktree.branch_tip(str(repo), TASK) == before, "a commit was made with no identity"
    question = loop_files.pending_question(loop.id)["question"]
    assert "git config user.name" in question and str(repo) in question


def _owner_named_in_personalclaw(tmp_path: Path) -> None:
    """The owner's name as Settings → Account keeps it: a name, with no email to commit as."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps({"dashboard": {"user_name": "Ada Example"}}))


@pytest.mark.parametrize("attended", [True, False])
def test_a_code_loop_whose_repository_has_no_identity_does_not_start(
    tmp_path, attended, monkeypatch
):
    """Measured: in a repository with no ``user.name``, a loop's worker made every commit as
    ``git -c user.name=… -c user.email=…`` of an identity it made up. A loop whose repository has
    no identity to commit as stops before any worker runs, and says how to set one; the name
    PersonalClaw knows the owner by is not one (it has no email), so it is not used."""
    from personalclaw.dashboard.handlers import loop_routes

    repo = _repo(tmp_path, identity=False)
    _owner_named_in_personalclaw(tmp_path)
    loop = store.create(
        Loop(
            id="",
            name="Readme",
            kind="code",
            task="tidy the README",
            attended=attended,
            workspace_dir=str(repo),
            plan=[{"stage": "implementation", "title": "Tidy"}],
            kind_config={"entry_stage": "implementation", "project_kind": "brownfield"},
        )
    )
    reason = kinds.get("code").launch_blocker(loop)
    assert reason and "git config user.name" in reason and str(repo) in reason, reason

    started: list[str] = []

    async def _start(_state, _svc, cid):
        started.append(cid)

    monkeypatch.setattr(loop_routes.manager, "start", _start)
    app = web.Application()
    app["state"] = SimpleNamespace()
    resp = asyncio.get_event_loop().run_until_complete(
        loop_routes.api_loop_action(_req(app, "PATCH", "/l", cid=loop.id, body={"action": "start"}))
    )
    assert resp.status == 422 and b"git config user.name" in resp.body, resp.body
    assert started == []

    # The control: once git has a name and email there, the loop starts.
    _git(repo, "config", "user.name", OWNER[0])
    _git(repo, "config", "user.email", OWNER[1])
    assert kinds.get("code").launch_blocker(store.get(loop.id)) is None


def test_a_running_loop_whose_repository_has_no_identity_stops_before_its_worker_commits(
    tmp_path, code
):
    """A stage worker commits in the workspace itself, with no worktree and no merge of the
    scheduler's in between, so the scheduler asks before that worker's next cycle: the loop
    pauses for its owner instead of leaving the worker to commit as someone."""
    repo = _repo(tmp_path, identity=False)
    loop = store.create(
        Loop(
            id="",
            name="Readme",
            kind="code",
            task="tidy the README",
            attended=True,
            workspace_dir=str(repo),
            plan=[{"stage": "implementation", "title": "Tidy"}],
            kind_config={"entry_stage": "implementation"},
        )
    )
    store.update_status(loop.id, LoopStatus.RUNNING)
    before = _git(repo, "rev-parse", "main")

    paused = asyncio.get_event_loop().run_until_complete(code.schedule(store.get(loop.id), _Ctx()))

    assert paused is True and store.get(loop.id).status == "needs_input"
    assert "git config user.name" in loop_files.pending_question(loop.id)["question"]
    assert _git(repo, "rev-parse", "main") == before

    # The control: with an identity there, the scheduler lets the stage worker carry on.
    _git(repo, "config", "user.name", OWNER[0])
    _git(repo, "config", "user.email", OWNER[1])
    loop_files.clear_question(loop.id)
    store.update_status(loop.id, LoopStatus.RUNNING)
    assert (
        asyncio.get_event_loop().run_until_complete(code.schedule(store.get(loop.id), _Ctx()))
        is False
    )


@pytest.mark.parametrize("attended", [True, False])
def test_work_a_worker_committed_under_another_name_is_not_merged(tmp_path, code, attended):
    """Whatever a worker does, nothing a loop merges into its owner's branch carries a name
    other than theirs: a task whose branch holds a commit made as someone else is not merged,
    or put up for merging, and the loop says which commits and whose name they carry."""
    repo = _repo(tmp_path)
    loop = _loop(repo, attended=attended)
    path = _finished_task_worktree(repo)
    _git(path, "add", "digest.py")
    _git(
        path,
        "-c",
        "user.name=Loop Worker",
        "-c",
        "user.email=worker@example.invalid",
        "commit",
        "-qm",
        "escape titles once",
    )
    before = _git(repo, "rev-parse", "main")

    paused = _schedule(code, loop, _Ctx())

    assert paused is True and store.get(loop.id).status == "needs_input"
    assert _git(repo, "rev-parse", "main") == before
    asked = loop_files.pending_question(loop.id)
    assert "merge" not in asked, "work under another name was put up for merging"
    assert "Loop Worker <worker@example.invalid>" in asked["question"]
    assert "Ada Example <ada@example.com>" in asked["question"]
    assert loop_files.get_merges(loop.id) == []


def test_the_first_commit_of_an_empty_repository_is_made_as_its_owner(tmp_path):
    repo = tmp_path / "fresh"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", OWNER[0])
    _git(repo, "config", "user.email", OWNER[1])
    (repo / "README.md").write_text("# fresh\n")

    assert worktree.ensure_base_commit(str(repo)) is True
    assert _authors(repo, "main") == {"Ada Example <ada@example.com>|Ada Example <ada@example.com>"}


def test_an_empty_repository_with_no_identity_gets_no_invented_commit(tmp_path):
    repo = tmp_path / "fresh"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# fresh\n")

    assert worktree.ensure_base_commit(str(repo)) is False
    assert worktree.commit_identity(str(repo)) is None


# ── the review and the approval, through their routes ────────────────────────────────────


def _req(app, method: str, path: str, *, cid: str, body=None):
    req = make_mocked_request(method, path, match_info={"id": cid}, app=app)
    req["user"] = "owner"
    if body is not None:

        async def _json():
            return body

        req.json = _json  # type: ignore[assignment]
    return req


def test_the_review_route_shows_the_work_and_the_merge_route_approves_it(
    tmp_path, code, monkeypatch
):
    from personalclaw.dashboard.handlers import loop_routes

    repo = _repo(tmp_path)
    loop = _loop(repo, attended=True)
    _finished_task_worktree(repo)
    _schedule(code, loop, _Ctx())
    started: list[str] = []

    async def _resume(_state, _svc, cid):
        started.append(cid)
        store.update_status(cid, LoopStatus.RUNNING)

    monkeypatch.setattr(loop_routes.manager, "start", _resume)
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: object())
    app = web.Application()
    app["state"] = SimpleNamespace()
    run = asyncio.get_event_loop().run_until_complete

    review = run(loop_routes.api_loop_merge_review(_req(app, "GET", "/m", cid=loop.id)))
    shown = json.loads(review.body)
    (task,) = shown["tasks"]
    assert (task["task_id"], task["title"], shown["into"]) == (TASK, "Escape titles once", "main")
    assert "escaped once" in task["diff"] and task["commits"]

    unconfirmed = run(
        loop_routes.api_loop_merge(
            _req(app, "POST", "/m", cid=loop.id, body={"tips": {TASK: task["tip"]}})
        )
    )
    assert unconfirmed.status == 400 and b"confirmation_required" in unconfirmed.body
    stale = run(
        loop_routes.api_loop_merge(
            _req(app, "POST", "/m", cid=loop.id, body={"tips": {TASK: "0" * 40}, "confirm": True})
        )
    )
    assert stale.status == 409 and b"loop_merge_moved" in stale.body
    assert started == [] and code._approved_tip(loop.id, TASK) == ""

    ok = run(
        loop_routes.api_loop_merge(
            _req(
                app, "POST", "/m", cid=loop.id, body={"tips": {TASK: task["tip"]}, "confirm": True}
            )
        )
    )
    assert ok.status == 200, ok.body
    assert started == [loop.id]
    assert code._approved_tip(loop.id, TASK) == task["tip"]
    assert loop_files.pending_question(loop.id) is None

    again = run(loop_routes.api_loop_merge_review(_req(app, "GET", "/m", cid=loop.id)))
    assert again.status == 409 and b"loop_merge_not_waiting" in again.body


def test_merging_a_loops_work_is_the_owners_alone():
    from personalclaw.apps.permissions import ROUTE_AUTHZ, OwnerOnly

    assert isinstance(ROUTE_AUTHZ["POST /api/loops/{id}/merge"], OwnerOnly)


def test_no_loop_commit_names_an_identity_of_the_products_own():
    """Every commit a loop or a workflow run makes in the owner's repository is made as git is
    configured there, and nothing a loop's workers are told or given names one either: no
    ``user.name=`` or ``user.email=`` in the code that commits, briefs or tools a loop."""
    src = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
    scanned = 0
    for area in ("loop", "workflows", "planning", "agents", "dashboard"):
        for path in sorted((src / area).rglob("*")):
            if path.suffix not in (".py", ".md", ".json", ".txt", ".yaml", ".toml"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            scanned += 1
            assert "user.name=" not in text and "user.email=" not in text, path
    assert scanned > 100, scanned  # the scan read the tree


def test_a_code_loops_workers_are_told_to_commit_as_git_is_configured(tmp_path):
    """The stage worker's brief says how it commits: as git is configured, with no name or email
    of its own, and to stop and say so when git has none."""
    loop = _loop(_repo(tmp_path), attended=True)

    brief = kinds.get("code").build_brief(loop)

    assert "commit as git is configured here" in brief
    assert "Never set a name or email for a commit yourself" in brief and "stop and say so" in brief


def test_resume_after_setting_an_identity_does_not_stop_on_the_settled_question(tmp_path, code):
    """The scheduler asks its questions again while they hold, so a Resume carries on once the
    identity is set, instead of stopping on the question it already answered. A worker's own
    question of an Attended loop still holds until it is answered."""
    from personalclaw.loop import watchdog

    repo = _repo(tmp_path, identity=False)
    loop = _loop(repo, attended=True)
    _finished_task_worktree(repo)
    _schedule(code, loop, _Ctx())
    assert loop_files.pending_question(loop.id)["asked_by"] == "scheduler"
    _git(repo, "config", "user.name", OWNER[0])
    _git(repo, "config", "user.email", OWNER[1])

    wd = watchdog.LoopWatchdog(SimpleNamespace(_sessions={}), SimpleNamespace())
    resumed = store.update_status(loop.id, LoopStatus.RUNNING)  # the owner's Resume
    since = float(resumed.started_at or 0.0)
    assert wd._handle_question(loop.id, attended=True, since=since) is False
    assert loop_files.pending_question(loop.id) is None
    assert _schedule(code, loop, _Ctx()) is True  # on to the merge review, under her name
    assert loop_files.pending_question(loop.id)["merge"]["tasks"][0]["task_id"] == TASK

    loop_files.write_question(loop.id, "Which feed format should the digest keep?")
    assert wd._handle_question(loop.id, attended=True, since=since) is True


def test_an_approval_file_in_the_loops_folder_merges_nothing(tmp_path, code):
    """The loop's folder is its workers' to write (its brief, findings and questions live there),
    so nothing written there stands for its owner's approval: only the approval route's does."""
    import json as _json

    repo = _repo(tmp_path)
    loop = _loop(repo, attended=True)
    _finished_task_worktree(repo)
    _schedule(code, loop, _Ctx())
    tip = worktree.branch_tip(str(repo), TASK)
    folder = loop_files.loop_dir(loop.id)
    (folder / "merge_approved.json").write_text(_json.dumps({TASK: tip}))
    before = _git(repo, "rev-parse", "main")

    loop_files.clear_question(loop.id)
    store.update_status(loop.id, LoopStatus.RUNNING)
    paused = _schedule(code, loop, _Ctx())

    assert paused is True and _git(repo, "rev-parse", "main") == before


def test_a_finished_task_that_changed_nothing_is_not_put_to_its_owner(tmp_path, code):
    repo = _repo(tmp_path)
    loop = _loop(repo, attended=True)
    worktree.add_worktree(str(repo), TASK)  # the task finished without an edit

    paused = _schedule(code, loop, _Ctx())

    assert paused is False and loop_files.pending_question(loop.id) is None
    assert not worktree.branch_exists(str(repo), TASK)
    assert TASK not in store.get(loop.id).kind_config.get("queued_task_ids", [])
