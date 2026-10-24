"""Only a commit on main whose definitions are main's may become a ``v*`` tag (#4584).

``tools/ci/release_tag.py`` runs on a planted repository with a bare ``origin``: ``main`` holds a
release workflow, a later commit that changes only a README, and a later one that changes the
workflow; a branch holds a commit that is not on ``main``. The planted ``scripts/release_tags.py``
stands in for the release's parser, so the check is shown to read the checkout's own.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import urllib.parse
import zipfile
from pathlib import Path

import pytest

from tools.ci import breadth_evidence, merge_verdict, release_tag, trust

#: Shaped as the real parser is: postponed annotations and a frozen dataclass, which only load
#: when the module is registered while it runs.
PARSER = (
    "from __future__ import annotations\n"
    "import re\n"
    "from dataclasses import dataclass\n"
    "@dataclass(frozen=True)\n"
    "class ReleaseRef:\n"
    "    ref: str\n"
    "def parse_release_ref(ref: str) -> ReleaseRef:\n"
    "    if not re.fullmatch(r'v\\d+\\.\\d+\\.\\d+(-[0-9A-Za-z.]+)?', ref):\n"
    "        raise ValueError(f'{ref!r} is not a release tag (planted parser)')\n"
    "    return ReleaseRef(ref)\n"
)
#: A definition in the planted tree: a workflow, under the first of trust's definition roots.
RELEASE = trust.DEFINITION_ROOTS[0] + "release.yml"


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Planted",
        GIT_AUTHOR_EMAIL="planted@example.invalid",
        GIT_COMMITTER_NAME="Planted",
        GIT_COMMITTER_EMAIL="planted@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    )
    return done.stdout.strip()


def _commit(repo: Path, path: str, text: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def planted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    # The module's own git calls read the same isolated configuration.
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "-q", "--bare", "-b", "main")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", str(origin))
    _commit(repo, "scripts/release_tags.py", PARSER, "the parser")
    first = _commit(repo, RELEASE, "on: push\njobs: {}\n", "the release workflow")
    readme = _commit(repo, "README.md", "hello\n", "a README")
    _git(repo, "checkout", "-q", "-b", "side", first)
    side = _commit(repo, "side.txt", "off main\n", "a side commit")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    return {"repo": repo, "origin": origin, "first": first, "readme": readme, "side": side}


def _refusal(planted: dict[str, object], tag: str, commit: object) -> str:
    with pytest.raises(trust.Refused) as refused:
        release_tag.check(planted["repo"], tag, str(commit))  # type: ignore[arg-type]
    return str(refused.value)


def test_mains_tip_may_be_tagged(planted: dict[str, object]) -> None:
    release_tag.check(planted["repo"], "v1.2.3", str(planted["readme"]))  # type: ignore[arg-type]


def test_an_older_main_commit_with_mains_definitions_may_be_tagged(
    planted: dict[str, object],
) -> None:
    release_tag.check(planted["repo"], "v1.2.3", str(planted["first"]))  # type: ignore[arg-type]


def test_a_tag_the_release_parser_refuses_is_refused(planted: dict[str, object]) -> None:
    assert "planted parser" in _refusal(planted, "1.2.3", planted["readme"])
    assert "planted parser" in _refusal(planted, "v1.2", planted["readme"])


def test_a_commit_off_main_is_refused(planted: dict[str, object]) -> None:
    assert "is not on origin/main" in _refusal(planted, "v1.2.3", planted["side"])


def test_a_short_or_named_commit_is_refused(planted: dict[str, object]) -> None:
    assert "not a full commit id" in _refusal(planted, "v1.2.3", str(planted["readme"])[:12])
    assert "not a full commit id" in _refusal(planted, "v1.2.3", "main")


def test_an_older_commit_whose_release_workflow_main_has_changed_is_refused(
    planted: dict[str, object],
) -> None:
    repo = planted["repo"]
    assert isinstance(repo, Path)
    _commit(repo, RELEASE, "on: push\njobs: {x: {}}\n", "a new release")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    reason = _refusal(planted, "v1.2.3", planted["readme"])
    assert f"{RELEASE} (modified)" in reason


def test_a_tag_that_exists_here_is_refused(planted: dict[str, object]) -> None:
    repo = planted["repo"]
    assert isinstance(repo, Path)
    _git(repo, "tag", "v1.2.3", str(planted["first"]))
    assert "exists already" in _refusal(planted, "v1.2.3", planted["readme"])


def test_a_tag_that_exists_only_on_the_remote_is_refused(planted: dict[str, object]) -> None:
    repo = planted["repo"]
    assert isinstance(repo, Path)
    _git(repo, "tag", "v1.2.4", str(planted["first"]))
    _git(repo, "push", "-q", "origin", "v1.2.4")
    _git(repo, "tag", "-d", "v1.2.4")
    assert "exists already" in _refusal(planted, "v1.2.4", planted["readme"])


def test_an_unreachable_remote_is_refused_not_passed(planted: dict[str, object]) -> None:
    repo = planted["repo"]
    assert isinstance(repo, Path)
    _git(repo, "remote", "set-url", "origin", str(repo.parent / "no-such-origin.git"))
    assert "cannot tell whether v1.2.3 exists" in _refusal(planted, "v1.2.3", planted["readme"])


class FakePost:
    def __init__(self, *answers: tuple[int, object]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, path: str, body: dict[str, object]) -> tuple[int, object]:
        self.calls.append((path, body))
        return self.answers.pop(0)


COMMIT = "a" * 40
TAG_OBJECT = "b" * 40


def test_create_makes_the_annotated_tag_and_then_its_ref() -> None:
    post = FakePost((201, {"sha": TAG_OBJECT}), (201, {"ref": "refs/tags/v1.2.3"}))
    assert release_tag.create("owner/project", "v1.2.3", COMMIT, post) == TAG_OBJECT
    assert post.calls == [
        (
            "/repos/owner/project/git/tags",
            {"tag": "v1.2.3", "message": "PersonalClaw v1.2.3", "object": COMMIT, "type": "commit"},
        ),
        ("/repos/owner/project/git/refs", {"ref": "refs/tags/v1.2.3", "sha": TAG_OBJECT}),
    ]


def test_a_refused_tag_object_creates_no_ref() -> None:
    post = FakePost((403, {"message": "Resource not accessible by integration"}))
    with pytest.raises(trust.Refused, match="tag object was not created"):
        release_tag.create("owner/project", "v1.2.3", COMMIT, post)
    assert len(post.calls) == 1


def test_a_refused_ref_is_reported() -> None:
    post = FakePost((201, {"sha": TAG_OBJECT}), (422, {"message": "Repository rule violations"}))
    with pytest.raises(trust.Refused, match="refs/tags/v1.2.3 was not created"):
        release_tag.create("owner/project", "v1.2.3", COMMIT, post)


def _green_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """The controller's verdict on the commit's tree is green (its own tests are below)."""
    monkeypatch.setattr(
        release_tag, "controller_green", lambda repository, commit, get, *, repo: "PR #1"
    )


def _green_breadth(monkeypatch: pytest.MonkeyPatch) -> None:
    """The commit's breadth is green (its own tests are below)."""
    monkeypatch.setattr(
        release_tag,
        "breadth_green",
        lambda repository, commit, get, download, *, repo: "https://breadth/run/1",
    )


def _green_runs(monkeypatch: pytest.MonkeyPatch, commit: str) -> None:
    """The commit's push run on main is green, read with the job's token."""
    green = FakeGet(ci_yml=[_run("ci.yml", sha=commit)])
    monkeypatch.setattr(release_tag, "github_get", lambda token: green)
    monkeypatch.setenv("RUNS_TOKEN", "the-jobs-token")
    _green_breadth(monkeypatch)
    _green_verdict(monkeypatch)


def test_main_refuses_to_create_without_the_controllers_token(
    planted: dict[str, object], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    _green_runs(monkeypatch, str(planted["readme"]))
    argv = ["--tag", "v1.2.3", "--commit", str(planted["readme"]), "--repo", str(planted["repo"])]
    argv += ["--repository", "owner/project"]
    assert release_tag.main(argv) == 0
    assert release_tag.main([*argv, "--create"]) == 1
    assert "no GH_TOKEN" in capsys.readouterr().out
    planted_tag = [
        "--tag",
        "v1.2",
        "--commit",
        str(planted["readme"]),
        "--repo",
        str(planted["repo"]),
    ]
    assert release_tag.main([*planted_tag, "--repository", "owner/project"]) == 1


def test_a_rehearsal_is_checked_like_the_release_and_named_apart(
    planted: dict[str, object],
) -> None:
    repo = planted["repo"]
    assert isinstance(repo, Path)
    readme = str(planted["readme"])
    release_tag.check(repo, "v1.2.3", readme, rehearsal=True)
    # The tag is still the release's: the parser and main hold it the same way.
    with pytest.raises(trust.Refused, match="planted parser"):
        release_tag.check(repo, "1.2.3", readme, rehearsal=True)
    with pytest.raises(trust.Refused, match="is not on origin/main"):
        release_tag.check(repo, "v1.2.3", str(planted["side"]), rehearsal=True)
    # Its own name is the one that must not exist; the release tag's does not stop it, and
    # neither does another commit's rehearsal of the same tag.
    _git(repo, "tag", "v1.2.3", str(planted["first"]))
    release_tag.check(repo, "v1.2.3", readme, rehearsal=True)
    _git(repo, "tag", f"rehearsal-v1.2.4-{str(planted['first'])[:7]}", str(planted["first"]))
    release_tag.check(repo, "v1.2.4", readme, rehearsal=True)
    _git(repo, "tag", f"rehearsal-v1.2.4-{readme[:7]}", readme)
    with pytest.raises(trust.Refused, match=f"rehearsal-v1.2.4-{readme[:7]} exists already"):
        release_tag.check(repo, "v1.2.4", readme, rehearsal=True)


def test_a_rehearsal_creates_the_rehearsal_name_and_never_the_release_tag(
    planted: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    readme = str(planted["readme"])
    name = f"rehearsal-v1.2.3-{readme[:7]}"
    post = FakePost((201, {"sha": TAG_OBJECT}), (201, {"ref": f"refs/tags/{name}"}))
    monkeypatch.setattr(release_tag, "github_post", lambda token: post)
    monkeypatch.setenv("GH_TOKEN", "an-app-token")
    _green_runs(monkeypatch, readme)
    # A rehearsal is what starts breadth on its commit, so it reads none.
    monkeypatch.setattr(release_tag, "breadth_green", _never)
    argv = ["--tag", "v1.2.3", "--commit", readme, "--repo", str(planted["repo"])]
    assert (
        release_tag.main([*argv, "--repository", "owner/project", "--create", "--rehearsal"]) == 0
    )
    assert [body.get("tag") or body.get("ref") for _, body in post.calls] == [
        name,
        f"refs/tags/{name}",
    ]
    assert release_tag.tag_name("v1.2.3", commit=readme) == "v1.2.3"
    assert release_tag.tag_name("v1.2.3", rehearsal=True, commit=readme) == name


def _never(*args: object, **kwargs: object) -> str:
    raise AssertionError("a rehearsal reads no breadth")


# ── the commit's own push run on main must be green (#3911) ───────────────────────────────────

REPOSITORY = "owner/project"


def _run(
    workflow: str,
    *,
    sha: str = COMMIT,
    status: str = "completed",
    conclusion: str | None = "success",
    number: int = 7,
    event: str = "push",
    branch: str = "main",
    repository: str = REPOSITORY,
) -> dict[str, object]:
    return {
        "id": 1000 + number,
        "run_number": number,
        "path": release_tag.WORKFLOW_DIR + workflow,
        "head_sha": sha,
        "event": event,
        "head_branch": branch,
        "head_repository": {"full_name": repository},
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{1000 + number}",
    }


class FakeGet:
    """The API's runs of each workflow, by its file name; every path asked is kept."""

    def __init__(self, **runs: list[dict[str, object]]) -> None:
        self.runs = {name.replace("_", "."): listed for name, listed in runs.items()}
        self.paths: list[str] = []

    def __call__(self, path: str) -> object:
        self.paths.append(path)
        workflow = path.split("/actions/workflows/", 1)[1].split("/", 1)[0]
        return {"workflow_runs": self.runs.get(workflow, [])}


def _gate_refusal(get: FakeGet) -> str:
    with pytest.raises(trust.Refused) as refused:
        release_tag.main_push_green(REPOSITORY, COMMIT, get)
    return str(refused.value)


def test_a_commit_whose_own_main_push_run_is_green_may_be_tagged() -> None:
    get = FakeGet(ci_yml=[_run("ci.yml")])
    assert release_tag.main_push_green(REPOSITORY, COMMIT, get).endswith("/runs/1007")
    # merge.yml is asked first, then ci.yml, each for this commit's push runs on main.
    assert [p.split("/actions/workflows/")[1].split("/")[0] for p in get.paths] == [
        "merge.yml",
        "ci.yml",
    ]
    for path in get.paths:
        assert path.startswith(f"/repos/{REPOSITORY}/actions/workflows/")
        assert f"head_sha={COMMIT}" in path and "event=push" in path and "branch=main" in path


def test_the_newer_shape_decides_and_its_red_is_not_passed_over() -> None:
    """Once #3915's flip lands, merge.yml's push run is the commit's: a red there is never passed
    over for ci.yml's green."""
    green = FakeGet(merge_yml=[_run("merge.yml")], ci_yml=[_run("ci.yml", conclusion="failure")])
    assert release_tag.main_push_green(REPOSITORY, COMMIT, green)
    red = FakeGet(merge_yml=[_run("merge.yml", conclusion="failure")], ci_yml=[_run("ci.yml")])
    assert "merge.yml" in _gate_refusal(red) and "concluded failure" in _gate_refusal(red)


@pytest.mark.parametrize(
    ("run", "says"),
    [
        # A shard a newer push replaced, or one nothing proved, reds the run's gate.
        (_run("ci.yml", conclusion="failure"), "concluded failure, not success"),
        (_run("ci.yml", conclusion="cancelled"), "concluded cancelled, not success"),
        (_run("ci.yml", conclusion="timed_out"), "concluded timed_out, not success"),
        (_run("ci.yml", status="in_progress", conclusion=None), "is in_progress"),
        (_run("ci.yml", status="queued", conclusion=None), "is queued"),
    ],
)
def test_a_run_that_is_not_green_is_refused_naming_it(run: dict[str, object], says: str) -> None:
    reason = _gate_refusal(FakeGet(ci_yml=[run]))
    assert says in reason and str(run["html_url"]) in reason, reason


def test_the_newest_run_of_the_commit_is_the_one_read() -> None:
    get = FakeGet(ci_yml=[_run("ci.yml", number=7), _run("ci.yml", number=9, conclusion="failure")])
    assert "/runs/1009" in _gate_refusal(get)
    get = FakeGet(ci_yml=[_run("ci.yml", number=9), _run("ci.yml", number=7, conclusion="failure")])
    assert release_tag.main_push_green(REPOSITORY, COMMIT, get).endswith("/runs/1009")


@pytest.mark.parametrize(
    "other",
    [
        _run("ci.yml", sha="c" * 40),
        _run("ci.yml", event="pull_request"),
        _run("ci.yml", event="workflow_dispatch"),
        _run("ci.yml", branch="side"),
        _run("ci.yml", repository="someone/fork"),
        _run("pr.yml"),
    ],
)
def test_a_run_that_is_not_this_commits_push_to_main_proves_nothing(
    other: dict[str, object],
) -> None:
    assert "has no push run on main" in _gate_refusal(FakeGet(ci_yml=[other]))


def test_a_commit_with_no_push_run_on_main_is_refused() -> None:
    reason = _gate_refusal(FakeGet())
    assert "has no push run on main in merge.yml or ci.yml" in reason


def test_an_answer_the_gate_cannot_read_is_refused_not_passed() -> None:
    def broken(path: str) -> object:
        return {"message": "Bad credentials"}

    assert "cannot read" in _gate_refusal(broken)  # type: ignore[arg-type]

    def failing(path: str) -> object:
        raise OSError("connection reset")

    assert "cannot read" in _gate_refusal(failing)  # type: ignore[arg-type]


def test_main_reads_the_gate_before_it_creates_and_a_red_run_creates_nothing(
    planted: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    commit = str(planted["readme"])
    post = FakePost((201, {"sha": TAG_OBJECT}), (201, {"ref": "refs/tags/v1.2.3"}))
    monkeypatch.setattr(release_tag, "github_post", lambda token: post)
    _green_breadth(monkeypatch)
    _green_verdict(monkeypatch)
    red = FakeGet(ci_yml=[_run("ci.yml", sha=commit, conclusion="failure")])
    tokens: list[str] = []
    monkeypatch.setattr(release_tag, "github_get", lambda token: tokens.append(token) or red)
    monkeypatch.setenv("GH_TOKEN", "an-app-token")
    monkeypatch.setenv("RUNS_TOKEN", "the-jobs-token")
    argv = ["--tag", "v1.2.3", "--commit", commit, "--repo", str(planted["repo"])]
    assert release_tag.main([*argv, "--repository", REPOSITORY, "--create"]) == 1
    assert "concluded failure" in capsys.readouterr().out
    assert post.calls == [] and tokens == ["the-jobs-token"]
    green = FakeGet(ci_yml=[_run("ci.yml", sha=commit)])
    monkeypatch.setattr(release_tag, "github_get", lambda token: green)
    assert release_tag.main([*argv, "--repository", REPOSITORY, "--create"]) == 0
    out = capsys.readouterr().out
    assert (
        "its push run on main is green: https://github.com/owner/project/actions/runs/1007" in out
    )
    assert [path for path, _ in post.calls] == [
        f"/repos/{REPOSITORY}/git/tags",
        f"/repos/{REPOSITORY}/git/refs",
    ]


def test_main_refuses_without_the_token_that_reads_the_runs(
    planted: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("RUNS_TOKEN", raising=False)
    argv = ["--tag", "v1.2.3", "--commit", str(planted["readme"]), "--repo", str(planted["repo"])]
    assert release_tag.main([*argv, "--repository", REPOSITORY]) == 1
    assert "no RUNS_TOKEN" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        release_tag.main(argv)  # the repository is required: there is no tag without the gate


# ── no tag while a release runs (#3911) ───────────────────────────────────────────────────────


def _release(status: str, *, tag: str = "v1.2.2", number: int = 41) -> dict[str, object]:
    """A release.yml run of *tag* in *status*: a tag's push, as the API lists it."""
    return _run("release.yml", status=status, conclusion=None, number=number, branch=tag)


@pytest.mark.parametrize("status", release_tag.UNFINISHED)
def test_a_release_that_has_not_finished_refuses_a_new_tag_naming_it(status: str) -> None:
    get = FakeGet(release_yml=[_release(status)])
    with pytest.raises(trust.Refused) as refused:
        release_tag.no_release_running(REPOSITORY, get)
    said = str(refused.value)
    assert "v1.2.2" in said and status in said and said.count("/actions/runs/1041") == 1, said


def test_finished_releases_and_other_workflows_runs_refuse_nothing() -> None:
    finished = {**_release("completed"), "conclusion": "success"}
    elsewhere = _run("ci.yml", status="in_progress", conclusion=None)
    get = FakeGet(release_yml=[finished, elsewhere], ci_yml=[elsewhere])
    release_tag.no_release_running(REPOSITORY, get)
    asked = [p for p in get.paths if f"/actions/workflows/{release_tag.RELEASE_WORKFLOW}/" in p]
    assert sorted(p.split("status=")[1].split("&")[0] for p in asked) == sorted(
        release_tag.UNFINISHED
    ), "every state a run has before it finishes is asked about"
    assert all(p.startswith(f"/repos/{REPOSITORY}/") for p in get.paths)


def test_releases_the_gate_cannot_read_refuse_the_tag_rather_than_pass_it() -> None:
    def failing(path: str) -> object:
        raise OSError("connection reset")

    with pytest.raises(trust.Refused, match="cannot read release.yml"):
        release_tag.no_release_running(REPOSITORY, failing)
    with pytest.raises(trust.Refused, match="cannot read release.yml"):
        release_tag.no_release_running(REPOSITORY, lambda path: {"message": "Bad credentials"})


def test_main_creates_no_tag_while_a_release_runs(
    planted: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The plant: a release in progress, and a commit that may otherwise be tagged."""
    commit = str(planted["readme"])
    post = FakePost((201, {"sha": TAG_OBJECT}), (201, {"ref": "refs/tags/v1.2.3"}))
    monkeypatch.setattr(release_tag, "github_post", lambda token: post)
    running = FakeGet(ci_yml=[_run("ci.yml", sha=commit)], release_yml=[_release("in_progress")])
    monkeypatch.setattr(release_tag, "github_get", lambda token: running)
    _green_breadth(monkeypatch)
    _green_verdict(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "an-app-token")
    monkeypatch.setenv("RUNS_TOKEN", "the-jobs-token")
    argv = ["--tag", "v1.2.3", "--commit", commit, "--repo", str(planted["repo"])]
    assert release_tag.main([*argv, "--repository", REPOSITORY, "--create"]) == 1
    out = capsys.readouterr().out
    assert "release.yml's run for v1.2.2" in out and "in_progress" in out, out
    assert post.calls == [], "no tag is made while a release runs"
    assert release_tag.main([*argv, "--repository", REPOSITORY, "--create", "--rehearsal"]) == 1
    assert post.calls == [], "a rehearsal is held to it as the release is"


# ── the commit's breadth must be green (#3911, #4712) ────────────────────────────────────────
#
# A rehearsal's tag push starts breadth.yml on the commit, off main, where nothing is reused. The
# real tag's gate reads breadth's runs of the commit: the newest must be a rehearsal tag's push,
# completed green, with a digest-verified record of every attempt, every leg run and green; an
# earlier red run of the commit refuses unless the owner's record names it. It never waits.

BREADTH_TAG = "rehearsal-v1.2.3-aaaaaaa"
#: A gating shard: a red one refuses whatever owns it.
GATING = "ubuntu-24.04-arm-3.13/3"


def _record(run_id: int, tree: str, *, attempt: int = 1, **legs: dict[str, object]) -> dict:
    entries: dict[str, object] = {
        key: breadth_evidence.executed({"tree": tree}, breadth_evidence.GREEN, "now", attempt)
        for key in sorted(breadth_evidence.expected_keys())
    }
    entries.update(legs)
    return {"version": 1, "run_id": run_id, "attempt": attempt, "tree": tree, "legs": entries}


def _red(key: str, attempt: int = 1, **marks: object) -> dict[str, object]:
    """Shard *key*'s entry, red on *attempt*."""
    return {"inputs": {}, "result": breadth_evidence.RED, "attempt": attempt, **marks}


class BreadthApi:
    """breadth.yml's runs of the commit, each run's artifacts, and each artifact's zip."""

    def __init__(self, planted: dict[str, object]) -> None:
        repo = planted["repo"]
        assert isinstance(repo, Path)
        self.commit = str(planted["readme"])
        self.tree = _git(repo, "rev-parse", f"{self.commit}^{{tree}}")
        self.runs: list[dict[str, object]] = []
        self.artifacts: dict[int, list[dict[str, object]]] = {}
        self.zips: dict[int, bytes] = {}
        self.issues: list[dict[str, object]] = []
        self.fail = ""
        self.paths: list[str] = []

    def issue(
        self,
        leg: str,
        number: int,
        *,
        login: str = "github-actions[bot]",
        kind: str = "Bot",
        association: str = "NONE",
        title: str = "",
    ) -> "BreadthApi":
        """An open issue, as breadth's report files one for a red leg."""
        self.issues.append(
            {
                "number": number,
                "title": title or breadth_evidence.issue_title(leg),
                "state": "open",
                "user": {"login": login, "type": kind},
                "author_association": association,
                "html_url": f"https://github.com/{REPOSITORY}/issues/{number}",
            }
        )
        return self

    def run(
        self,
        run_id: int,
        *records: dict,
        status: str = "completed",
        conclusion: str | None = "success",
        event: str = "push",
        branch: str = BREADTH_TAG,
        attempt: int = 1,
        bytes_of: bytes | None = None,
        **overrides: object,
    ) -> "BreadthApi":
        self.runs.append(
            {
                "id": run_id,
                "run_number": run_id,
                "run_attempt": attempt,
                "path": release_tag.WORKFLOW_DIR + "breadth.yml",
                "head_sha": self.commit,
                "event": event,
                "head_branch": branch,
                "head_repository": {"full_name": REPOSITORY},
                "status": status,
                "conclusion": conclusion,
                "html_url": f"https://github.com/{REPOSITORY}/actions/runs/{run_id}",
                **overrides,
            }
        )
        listed = self.artifacts.setdefault(run_id, [])
        for record in records:
            data = breadth_evidence.canonical(record)
            artifact = run_id * 100 + len(listed)
            listed.append(
                {
                    "id": artifact,
                    "name": breadth_evidence.artifact_name(breadth_evidence.digest(data)),
                    "expired": False,
                }
            )
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("record.json", bytes_of if bytes_of is not None else data)
            self.zips[artifact] = buffer.getvalue()
        listed.append(
            {"id": run_id * 100 + 99, "name": "breadth-entry-coverage__1", "expired": False}
        )
        return self

    def __call__(self, path: str) -> object:
        self.paths.append(path)
        if self.fail and self.fail in path:
            raise OSError("HTTP Error 502: Bad Gateway")
        if path == (
            f"/repos/{REPOSITORY}/actions/workflows/breadth.yml/runs"
            f"?head_sha={self.commit}&per_page=100"
        ):
            return {"total_count": len(self.runs), "workflow_runs": self.runs}
        for run_id, listed in self.artifacts.items():
            if path == f"/repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100&page=1":
                return {"total_count": len(listed), "artifacts": listed}
        if path.startswith("/search/issues?"):
            query = urllib.parse.parse_qs(path.split("?", 1)[1])["q"][0]
            assert query.startswith(f"repo:{REPOSITORY} is:issue is:open in:title "), query
            phrase = query.split("in:title ", 1)[1].strip('"')
            # The search matches words, not the exact title: the gate reads the title again.
            found = [i for i in self.issues if phrase.split(":")[0] in str(i["title"])]
            return {"total_count": len(found), "incomplete_results": False, "items": found}
        raise OSError(f"HTTP Error 404: Not Found ({path})")

    def download(self, path: str) -> bytes:
        self.paths.append(path)
        if self.fail and self.fail in path:
            raise OSError("HTTP Error 502: Bad Gateway")
        for artifact, data in self.zips.items():
            if path == f"/repos/{REPOSITORY}/actions/artifacts/{artifact}/zip":
                return data
        raise OSError(f"HTTP Error 404: Not Found ({path})")


def _breadth(planted: dict[str, object], api: BreadthApi) -> str:
    repo = planted["repo"]
    assert isinstance(repo, Path)
    return release_tag.breadth_green(REPOSITORY, api.commit, api, api.download, repo=repo)


def _breadth_refusal(planted: dict[str, object], api: BreadthApi) -> str:
    with pytest.raises(trust.Refused) as refused:
        _breadth(planted, api)
    return str(refused.value)


def _excuse(planted: dict[str, object], *entries: dict[str, object]) -> None:
    """The owner's record, planted in the checkout the gate reads (main's)."""
    repo = planted["repo"]
    assert isinstance(repo, Path)
    path = repo / release_tag.BREADTH_EXCEPTIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"about": "planted", "exceptions": list(entries)}), encoding="utf-8")


def test_a_commit_whose_rehearsal_breadth_is_green_may_be_tagged(
    planted: dict[str, object],
) -> None:
    api = BreadthApi(planted)
    api.run(70, _record(70, api.tree))
    assert _breadth(planted, api).endswith("/actions/runs/70")
    assert any(p.endswith("/zip") for p in api.paths), "the record is read, never a summary"


def test_a_commit_with_no_breadth_run_off_main_is_refused_saying_how_to_start_one(
    planted: dict[str, object],
) -> None:
    api = BreadthApi(planted)
    said = _breadth_refusal(planted, api)
    assert "no breadth run" in said and "rehearsal" in said, said
    for overrides in (
        {"head_branch": "main"},  # main's nightly reuses: a release never rests on it
        {"head_sha": "c" * 40},
        {"head_repository": {"full_name": "someone/fork"}},
        {"path": release_tag.WORKFLOW_DIR + "ci.yml"},
    ):
        other = BreadthApi(planted)
        other.run(70, _record(70, other.tree), **overrides)
        assert "no breadth run" in _breadth_refusal(planted, other), overrides


@pytest.mark.parametrize("status", ["in_progress", "queued", "waiting"])
def test_the_gate_never_waits_on_a_run_that_has_not_finished(
    planted: dict[str, object], status: str
) -> None:
    api = BreadthApi(planted)
    api.run(70, status=status, conclusion=None)
    said = _breadth_refusal(planted, api)
    assert status in said and "/actions/runs/70" in said, said
    earlier = BreadthApi(planted)
    earlier.run(69, status=status, conclusion=None).run(70, _record(70, earlier.tree))
    assert "/actions/runs/69" in _breadth_refusal(planted, earlier)


def test_the_newest_run_must_be_a_rehearsal_tags_push(planted: dict[str, object]) -> None:
    for event, branch in (
        ("schedule", BREADTH_TAG),
        ("push", "release/v1.2"),
        ("workflow_dispatch", "side"),
    ):
        api = BreadthApi(planted)
        api.run(70, _record(70, api.tree), event=event, branch=branch)
        said = _breadth_refusal(planted, api)
        assert "not a rehearsal tag's push" in said, said


@pytest.mark.parametrize(
    "conclusion", ["cancelled", "timed_out", "skipped", "action_required", "startup_failure"]
)
def test_a_newest_run_that_measured_nothing_is_refused(
    planted: dict[str, object], conclusion: str
) -> None:
    api = BreadthApi(planted)
    api.run(70, _record(70, api.tree), conclusion=conclusion)
    assert conclusion in _breadth_refusal(planted, api)


def test_a_failed_run_is_judged_by_its_records(planted: dict[str, object]) -> None:
    """A red leg's job fails its run, so a run whose only reds are advisory concludes failure:
    its records say which legs were red, and a gating red refuses."""
    advisory = BreadthApi(planted)
    advisory.run(
        70, _record(70, advisory.tree, **{"coverage/5": _red("coverage/5")}), conclusion="failure"
    )
    advisory.issue("coverage", 4769)
    assert "#4769" in _breadth(planted, advisory)
    gating = BreadthApi(planted)
    key = "ubuntu-24.04-3.13/2"
    gating.run(70, _record(70, gating.tree, **{key: _red(key)}), conclusion="failure")
    assert key in _breadth_refusal(planted, gating)


def test_a_record_with_a_red_or_carried_leg_does_not_satisfy_a_release(
    planted: dict[str, object],
) -> None:
    api = BreadthApi(planted)
    key = sorted(breadth_evidence.expected_keys())[0]
    red = {"inputs": {}, "result": breadth_evidence.RED, "attempt": 1}
    api.run(70, _record(70, api.tree, **{key: red}))
    assert "does not satisfy a release" in _breadth_refusal(planted, api)
    carried = BreadthApi(planted)
    reused = {"inputs": {}, "result": "success", "reused_from": {"run_id": 1, "digest": "d"}}
    carried.run(70, _record(70, carried.tree, **{key: reused}))
    assert "does not satisfy a release" in _breadth_refusal(planted, carried)


def test_a_rerun_missing_an_earlier_attempts_record_is_refused(
    planted: dict[str, object],
) -> None:
    """Attempt 1 measured something nobody can read: a green attempt 2 does not stand in."""
    api = BreadthApi(planted)
    api.run(70, _record(70, api.tree, attempt=2), attempt=2)
    assert "does not satisfy a release" in _breadth_refusal(planted, api)
    both = BreadthApi(planted)
    both.run(70, _record(70, both.tree), _record(70, both.tree, attempt=2), attempt=2)
    assert _breadth(planted, both).endswith("/runs/70")


def test_a_record_that_does_not_hash_to_its_name_or_names_another_tree_is_refused(
    planted: dict[str, object],
) -> None:
    api = BreadthApi(planted)
    record = _record(70, api.tree)
    api.run(70, record, bytes_of=breadth_evidence.canonical({**record, "attempt": 1, "x": 1}))
    assert "does not hash" in _breadth_refusal(planted, api)
    other = BreadthApi(planted)
    other.run(70, _record(70, "d" * 40))
    said = _breadth_refusal(planted, other)
    assert "d" * 40 in said and other.tree in said, said
    elsewhere = BreadthApi(planted)
    elsewhere.run(70, _record(71, elsewhere.tree))
    assert "run 71" in _breadth_refusal(planted, elsewhere)
    none = BreadthApi(planted)
    none.run(70)
    assert "holds no breadth record" in _breadth_refusal(planted, none)


def test_an_earlier_red_run_of_the_commit_refuses_unless_the_owner_recorded_why(
    planted: dict[str, object],
) -> None:
    def api() -> BreadthApi:
        made = BreadthApi(planted)
        made.run(69, _record(69, made.tree, **{GATING: _red(GATING)}), event="workflow_dispatch")
        return made.run(70, _record(70, made.tree))

    said = _breadth_refusal(planted, api())
    assert "/actions/runs/69" in said and release_tag.BREADTH_EXCEPTIONS in said, said
    commit = str(planted["readme"])
    # The owner's record must name that run and that commit.
    _excuse(planted, {"run_id": 68, "commit": commit, "why": "another run"})
    assert "/actions/runs/69" in _breadth_refusal(planted, api())
    _excuse(planted, {"run_id": 69, "commit": "c" * 40, "why": "another commit"})
    assert "/actions/runs/69" in _breadth_refusal(planted, api())
    _excuse(planted, {"run_id": 69, "commit": commit, "why": "the runner image broke the leg"})
    assert _breadth(planted, api()).endswith("/actions/runs/70")


def test_an_owners_record_cannot_excuse_the_newest_run(planted: dict[str, object]) -> None:
    commit = str(planted["readme"])
    _excuse(planted, {"run_id": 70, "commit": commit, "why": "no"})
    api = BreadthApi(planted)
    api.run(70, _record(70, api.tree, **{GATING: _red(GATING)}))
    assert GATING in _breadth_refusal(planted, api)


@pytest.mark.parametrize(
    "data, says",
    [
        ([], "not an object"),
        ({"exceptions": []}, "about"),
        ({"about": "x", "exceptions": [], "extra": 1}, "extra"),
        ({"about": "x", "exceptions": {}}, "not a list"),
        ({"about": "x", "exceptions": [{"run_id": 1, "commit": "a" * 40}]}, "why"),
        (
            {"about": "x", "exceptions": [{"run_id": True, "commit": "a" * 40, "why": "x"}]},
            "run_id",
        ),
        ({"about": "x", "exceptions": [{"run_id": 0, "commit": "a" * 40, "why": "x"}]}, "run_id"),
        ({"about": "x", "exceptions": [{"run_id": 1, "commit": "a" * 39, "why": "x"}]}, "commit"),
        ({"about": "x", "exceptions": [{"run_id": 1, "commit": "a" * 40, "why": " "}]}, "why"),
        (
            {
                "about": "x",
                "exceptions": [
                    {"run_id": 1, "commit": "a" * 40, "why": "x"},
                    {"run_id": 1, "commit": "a" * 40, "why": "y"},
                ],
            },
            "twice",
        ),
    ],
)
def test_the_owners_record_holds_its_shape(data: object, says: str) -> None:
    problems = release_tag.exception_problems(data)
    assert any(says in p for p in problems), problems
    assert (
        release_tag.exception_problems(
            {"about": "x", "exceptions": [{"run_id": 1, "commit": "a" * 40, "why": "x"}]}
        )
        == []
    )


def test_a_malformed_owners_record_refuses_rather_than_excuses(
    planted: dict[str, object],
) -> None:
    repo = planted["repo"]
    assert isinstance(repo, Path)
    path = repo / release_tag.BREADTH_EXCEPTIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    api = BreadthApi(planted)
    api.run(69, _record(69, api.tree, **{GATING: _red(GATING)})).run(70, _record(70, api.tree))
    assert release_tag.BREADTH_EXCEPTIONS in _breadth_refusal(planted, api)


@pytest.mark.parametrize("part", ["/workflows/breadth.yml/runs", "/artifacts?", "/zip"])
def test_an_answer_the_breadth_gate_cannot_read_is_refused_not_passed(
    planted: dict[str, object], part: str
) -> None:
    api = BreadthApi(planted)
    api.run(70, _record(70, api.tree))
    api.fail = part
    assert "cannot read" in _breadth_refusal(planted, api)


def test_main_reads_breadth_before_it_creates_a_release_tag_and_a_rehearsal_reads_none(
    planted: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The plant: a commit whose push run is green and which no breadth run has measured."""
    commit = str(planted["readme"])
    post = FakePost(
        (201, {"sha": TAG_OBJECT}),
        (201, {"ref": "refs/tags/x"}),
        (201, {"sha": TAG_OBJECT}),
        (201, {"ref": "refs/tags/v1.2.3"}),
    )
    monkeypatch.setattr(release_tag, "github_post", lambda token: post)
    runs = FakeGet(ci_yml=[_run("ci.yml", sha=commit)])
    breadth = BreadthApi(planted)

    def answers(path: str) -> object:
        return breadth(path) if "breadth.yml" in path or "/artifacts" in path else runs(path)

    monkeypatch.setattr(release_tag, "github_get", lambda token: answers)
    monkeypatch.setattr(release_tag, "github_download", lambda token: breadth.download)
    _green_verdict(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "an-app-token")
    monkeypatch.setenv("RUNS_TOKEN", "the-jobs-token")
    argv = ["--tag", "v1.2.3", "--commit", commit, "--repo", str(planted["repo"])]
    assert release_tag.main([*argv, "--repository", REPOSITORY, "--create"]) == 1
    assert "no breadth run" in capsys.readouterr().out
    assert post.calls == []
    assert release_tag.main([*argv, "--repository", REPOSITORY, "--create", "--rehearsal"]) == 0
    assert post.calls[0][1]["tag"] == f"rehearsal-v1.2.3-{commit[:7]}"
    breadth.run(70, _record(70, breadth.tree), branch=f"rehearsal-v1.2.3-{commit[:7]}")
    assert release_tag.main([*argv, "--repository", REPOSITORY, "--create"]) == 0
    out = capsys.readouterr().out
    assert "its breadth is green: https://github.com/owner/project/actions/runs/70" in out, out
    assert post.calls[2][1]["tag"] == "v1.2.3"


# ── advisory legs are owned, not gating; a dispatch re-measures an excused red (#3911) ────────
#
# The gating legs (each OS, Python and architecture, and random-order) must be green. The
# advisory ones (the next-Ubuntu canary and what reads it, and coverage) measure: they must have
# run, and each red one must have an open issue named for it, filed by breadth's report or by a
# maintainer, which the gate lists. A dispatch on a rehearsal tag may decide, as a re-measure,
# only when an earlier run of the commit was red and the owner's record names that run.


def test_the_advisory_legs_are_breadths_own() -> None:
    assert release_tag.ADVISORY_LEGS <= set(breadth_evidence.SHARDS)
    assert {"canary", "coverage"} <= release_tag.ADVISORY_LEGS
    for gating in ("ubuntu-24.04-3.13", "macos-26-3.12", "ubuntu-24.04-arm-3.13", "random-order"):
        assert gating not in release_tag.ADVISORY_LEGS


def test_an_advisory_red_with_its_open_issue_passes_and_is_listed(
    planted: dict[str, object],
) -> None:
    api = BreadthApi(planted)
    api.run(70, _record(70, api.tree, **{"canary/1": _red("canary/1"), "coverage/5": _red("c")}))
    api.issue("canary", 4768).issue("coverage", 4769)
    said = _breadth(planted, api)
    assert "/actions/runs/70" in said and "#4768" in said and "#4769" in said, said


def test_an_advisory_red_with_no_open_issue_refuses_naming_the_title(
    planted: dict[str, object],
) -> None:
    api = BreadthApi(planted)
    api.run(70, _record(70, api.tree, **{"canary/1": _red("canary/1")}))
    api.issue("coverage", 4769)
    said = _breadth_refusal(planted, api)
    assert "breadth: canary is red" in said, said


def test_an_issue_an_outsider_opened_owns_nothing(planted: dict[str, object]) -> None:
    """On a public repository anyone may open an issue under that title."""
    api = BreadthApi(planted)
    api.run(70, _record(70, api.tree, **{"canary/1": _red("canary/1")}))
    api.issue("canary", 9001, login="someone", kind="User", association="NONE")
    api.issue("canary", 9002, title="breadth: canary is red again")
    assert "breadth: canary is red" in _breadth_refusal(planted, api)
    owned = BreadthApi(planted)
    owned.run(70, _record(70, owned.tree, **{"canary/1": _red("canary/1")}))
    owned.issue("canary", 9003, login="keyurgolani", kind="User", association="OWNER")
    assert "#9003" in _breadth(planted, owned)


def test_an_advisory_leg_that_did_not_run_refuses(planted: dict[str, object]) -> None:
    api = BreadthApi(planted)
    api.run(70, _record(70, api.tree, **{"canary/2": _red("canary/2", missing=True)}))
    api.issue("canary", 4768)
    assert "did not run" in _breadth_refusal(planted, api)


def test_a_gating_red_refuses_whatever_issue_it_has(planted: dict[str, object]) -> None:
    for key in ("ubuntu-24.04-arm-3.13/3", "random-order/4", "macos-26-3.12/2", "security/1"):
        api = BreadthApi(planted)
        api.run(70, _record(70, api.tree, **{key: _red(key)}))
        api.issue(breadth_evidence.leg_of(key), 4771)
        said = _breadth_refusal(planted, api)
        assert key in said, said


def test_an_earlier_attempts_red_counts_as_its_leg_does(planted: dict[str, object]) -> None:
    advisory = BreadthApi(planted)
    first = _record(70, advisory.tree, **{"coverage/2": _red("coverage/2")})
    advisory.run(70, first, _record(70, advisory.tree, attempt=2), attempt=2)
    advisory.issue("coverage", 4769)
    assert _breadth(planted, advisory).startswith(
        "https://github.com/owner/project/actions/runs/70"
    )
    gating = BreadthApi(planted)
    first = _record(70, gating.tree, **{"ubuntu-24.04-3.13/1": _red("ubuntu-24.04-3.13/1")})
    gating.run(70, first, _record(70, gating.tree, attempt=2), attempt=2)
    assert "ubuntu-24.04-3.13/1" in _breadth_refusal(planted, gating)


def test_an_issue_search_the_gate_cannot_read_refuses(planted: dict[str, object]) -> None:
    api = BreadthApi(planted)
    api.run(70, _record(70, api.tree, **{"canary/1": _red("canary/1")}))
    api.issue("canary", 4768)
    api.fail = "/search/issues"
    assert "cannot read" in _breadth_refusal(planted, api)


def test_a_dispatch_on_a_rehearsal_tag_re_measures_only_an_excused_red(
    planted: dict[str, object],
) -> None:
    commit = str(planted["readme"])

    def api(*, earlier: bool = True) -> BreadthApi:
        made = BreadthApi(planted)
        legs = {GATING: _red(GATING)} if earlier else {}
        made.run(69, _record(69, made.tree, **legs))
        return made.run(70, _record(70, made.tree), event="workflow_dispatch")

    # Not excused: the red refuses, as any earlier red does.
    assert "/actions/runs/69" in _breadth_refusal(planted, api())
    # Excused by the owner's record: the dispatch decides.
    _excuse(planted, {"run_id": 69, "commit": commit, "why": "the arm runner was lost"})
    assert _breadth(planted, api()).endswith("/actions/runs/70")
    # With no red before it there is nothing to re-measure, so a dispatch decides nothing.
    said = _breadth_refusal(planted, api(earlier=False))
    assert "re-measure" in said, said
    alone = BreadthApi(planted)
    alone.run(70, _record(70, alone.tree), event="workflow_dispatch")
    assert "re-measure" in _breadth_refusal(planted, alone)


# ── the merge controller's green verdict on the tagged tree (#3911) ──────────────────────────
#
# A squash lands a pull request's head tree on main as a new commit, and the controller judged
# that head, not the commit. So the gate finds the one pull request that landed the commit, reads
# the verdict on its head through merge_verdict.verify, and holds the receipt's tree to the
# commit's own tree, read from the checkout.

HEAD = "f" * 40
APP = 5444147
PR = 4321


def _pull(**overrides: object) -> dict[str, object]:
    pull: dict[str, object] = {
        "number": PR,
        "state": "closed",
        "merged_at": "2026-10-24T12:00:00Z",
        "merge_commit_sha": None,  # filled in with the commit
        "head": {"sha": HEAD},
        "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
    }
    pull.update(overrides)
    return pull


def _verdict_run(
    run_id: int,
    receipt: dict[str, object],
    *,
    app: int = APP,
    conclusion: str | None = "success",
    status: str = "completed",
) -> dict[str, object]:
    return {
        "id": run_id,
        "name": merge_verdict.CHECK_NAME,
        "head_sha": HEAD,
        "status": status,
        "conclusion": conclusion,
        "app": {"id": app, "slug": "personalclaw-ci-controller"},
        "output": {"title": "green", "summary": "", "text": merge_verdict.receipt_block(receipt)},
    }


class VerdictApi:
    """The answers merge_verdict reads, by path; a path it does not plant is a 404."""

    def __init__(
        self,
        planted: dict[str, object],
        *,
        pulls: list[dict[str, object]] | None = None,
        runs: list[dict[str, object]] | None = None,
        head_tree: str | None = None,
        required: list[dict[str, object]] | None = None,
        fail: str = "",
    ) -> None:
        repo = planted["repo"]
        assert isinstance(repo, Path)
        self.commit = str(planted["readme"])
        self.tree = _git(repo, "rev-parse", f"{self.commit}^{{tree}}")
        self.receipt: dict[str, object] = {
            "controller_revision": str(planted["first"]),
            "head_sha": HEAD,
            "tree_oid": self.tree,
            "external_revisions": {},
            "toolchain": {"python": "3.13.16", "uv": "0.13.1"},
            "evidence_digests": {"ci-record-lint": "sha256:" + "b" * 64},
        }
        listed = runs if runs is not None else [_verdict_run(90, self.receipt)]
        checks = [{"context": "merge-verdict", "integration_id": APP}]
        self.answers: dict[str, object] = {
            f"/repos/{REPOSITORY}/commits/{self.commit}/pulls?per_page=100": (
                pulls if pulls is not None else [_pull(merge_commit_sha=self.commit)]
            ),
            f"/repos/{REPOSITORY}/rules/branches/main?per_page=100": [
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "required_status_checks": required if required is not None else checks
                    },
                }
            ],
            f"/repos/{REPOSITORY}/commits/{HEAD}": {
                "commit": {"tree": {"sha": head_tree or self.tree}}
            },
            f"/repos/{REPOSITORY}/commits/{HEAD}/check-runs?check_name=merge-verdict&filter=all"
            "&per_page=100&page=1": {"total_count": len(listed), "check_runs": listed},
        }
        self.fail = fail
        self.paths: list[str] = []

    def __call__(self, path: str) -> object:
        self.paths.append(path)
        if self.fail and self.fail in path:
            raise OSError("HTTP Error 502: Bad Gateway")
        if path not in self.answers:
            raise OSError(f"HTTP Error 404: Not Found ({path})")
        return self.answers[path]


def _verdict(planted: dict[str, object], api: VerdictApi) -> str:
    repo = planted["repo"]
    assert isinstance(repo, Path)
    return release_tag.controller_green(REPOSITORY, api.commit, api, repo=repo)


def _verdict_refusal(planted: dict[str, object], api: VerdictApi) -> str:
    with pytest.raises(trust.Refused) as refused:
        _verdict(planted, api)
    return str(refused.value)


def test_a_commit_whose_pull_requests_head_the_controller_judged_green_may_be_tagged(
    planted: dict[str, object],
) -> None:
    api = VerdictApi(planted)
    said = _verdict(planted, api)
    assert f"#{PR}" in said and HEAD in said and "check run 90" in said, said
    assert api.paths[0] == f"/repos/{REPOSITORY}/commits/{api.commit}/pulls?per_page=100"


def test_a_commit_no_pull_request_landed_is_refused(planted: dict[str, object]) -> None:
    commit = str(planted["readme"])
    for pulls in (
        [],
        # Open, or merged as another commit, or into another branch or repository: not this one.
        [_pull(merged_at=None, merge_commit_sha=commit)],
        [_pull(merge_commit_sha="c" * 40)],
        [_pull(merge_commit_sha=commit, base={"ref": "side", "repo": {"full_name": REPOSITORY}})],
        [_pull(merge_commit_sha=commit, base={"ref": "main", "repo": {"full_name": "a/fork"}})],
    ):
        said = _verdict_refusal(planted, VerdictApi(planted, pulls=pulls))
        assert "no pull request landed" in said and commit in said, said


def test_a_commit_two_pull_requests_claim_is_refused(planted: dict[str, object]) -> None:
    commit = str(planted["readme"])
    two = [_pull(merge_commit_sha=commit), _pull(number=PR + 1, merge_commit_sha=commit)]
    assert "more than one" in _verdict_refusal(planted, VerdictApi(planted, pulls=two))


def test_a_red_verdict_behind_an_older_green_is_refused(planted: dict[str, object]) -> None:
    receipt = VerdictApi(planted).receipt
    runs = [_verdict_run(91, receipt, conclusion="failure"), _verdict_run(90, receipt)]
    said = _verdict_refusal(planted, VerdictApi(planted, runs=runs))
    assert "91" in said and "failure" in said and f"#{PR}" in said, said
    pending = [
        _verdict_run(90, receipt),
        _verdict_run(92, receipt, status="in_progress", conclusion=None),
    ]
    assert "in_progress" in _verdict_refusal(planted, VerdictApi(planted, runs=pending))


def test_a_green_from_another_poster_is_refused(planted: dict[str, object]) -> None:
    receipt = VerdictApi(planted).receipt
    said = _verdict_refusal(
        planted, VerdictApi(planted, runs=[_verdict_run(90, receipt, app=15368)])
    )
    assert "no merge-verdict check run by App" in said, said


def test_a_green_on_another_tree_than_the_commits_is_refused_naming_both(
    planted: dict[str, object],
) -> None:
    """The head's tree, as the receipt and the API agree on it, is not what main holds: the pull
    request landed with a tree the controller never judged."""
    other = "d" * 40
    receipt = {**VerdictApi(planted).receipt, "tree_oid": other}
    api = VerdictApi(planted, runs=[_verdict_run(90, receipt)], head_tree=other)
    said = _verdict_refusal(planted, api)
    assert other in said and api.tree in said, said


def test_no_verdict_counts_until_mains_rule_requires_it(planted: dict[str, object]) -> None:
    """main-protect names the App that is believed; until it requires merge-verdict, none is."""
    said = _verdict_refusal(planted, VerdictApi(planted, required=[]))
    assert "no required merge-verdict check" in said, said


@pytest.mark.parametrize("part", ["/pulls", "/rules/", f"/commits/{HEAD}", "/check-runs"])
def test_an_answer_the_verdict_gate_cannot_read_is_refused_not_passed(
    planted: dict[str, object], part: str
) -> None:
    assert "cannot read" in _verdict_refusal(planted, VerdictApi(planted, fail=part))


def test_main_reads_the_verdict_before_it_creates_and_a_red_one_creates_nothing(
    planted: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The plant: a commit whose push run is green, and whose verdict is red."""
    commit = str(planted["readme"])
    post = FakePost((201, {"sha": TAG_OBJECT}), (201, {"ref": "refs/tags/v1.2.3"}))
    monkeypatch.setattr(release_tag, "github_post", lambda token: post)
    _green_breadth(monkeypatch)
    runs = FakeGet(ci_yml=[_run("ci.yml", sha=commit)])
    red = VerdictApi(
        planted, runs=[_verdict_run(90, VerdictApi(planted).receipt, conclusion="failure")]
    )

    def answers(api: VerdictApi) -> object:
        return lambda path: runs(path) if "/actions/workflows/" in path else api(path)

    monkeypatch.setattr(release_tag, "github_get", lambda token: answers(red))
    monkeypatch.setenv("GH_TOKEN", "an-app-token")
    monkeypatch.setenv("RUNS_TOKEN", "the-jobs-token")
    argv = ["--tag", "v1.2.3", "--commit", commit, "--repo", str(planted["repo"])]
    for more in (["--create"], ["--create", "--rehearsal"]):
        assert release_tag.main([*argv, "--repository", REPOSITORY, *more]) == 1
        assert "failure" in capsys.readouterr().out
    assert post.calls == []
    monkeypatch.setattr(release_tag, "github_get", lambda token: answers(VerdictApi(planted)))
    assert release_tag.main([*argv, "--repository", REPOSITORY, "--create"]) == 0
    out = capsys.readouterr().out
    assert f"the controller's verdict on its tree is green: #{PR}" in out, out
    assert len(post.calls) == 2
