"""Only a commit on main whose definitions are main's may become a ``v*`` tag (#4584).

``tools/ci/release_tag.py`` runs on a planted repository with a bare ``origin``: ``main`` holds a
release workflow, a later commit that changes only a README, and a later one that changes the
workflow; a branch holds a commit that is not on ``main``. The planted ``scripts/release_tags.py``
stands in for the release's parser, so the check is shown to read the checkout's own.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tools.ci import release_tag, trust

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


def _green_runs(monkeypatch: pytest.MonkeyPatch, commit: str) -> None:
    """The commit's push run on main is green, read with the job's token."""
    green = FakeGet(ci_yml=[_run("ci.yml", sha=commit)])
    monkeypatch.setattr(release_tag, "github_get", lambda token: green)
    monkeypatch.setenv("RUNS_TOKEN", "the-jobs-token")


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
    # Its own name is the one that must not exist; the release tag's does not stop it.
    _git(repo, "tag", "v1.2.3", str(planted["first"]))
    release_tag.check(repo, "v1.2.3", readme, rehearsal=True)
    _git(repo, "tag", "rehearsal-v1.2.4", str(planted["first"]))
    with pytest.raises(trust.Refused, match="rehearsal-v1.2.4 exists already"):
        release_tag.check(repo, "v1.2.4", readme, rehearsal=True)


def test_a_rehearsal_creates_the_rehearsal_name_and_never_the_release_tag(
    planted: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    post = FakePost((201, {"sha": TAG_OBJECT}), (201, {"ref": "refs/tags/rehearsal-v1.2.3"}))
    monkeypatch.setattr(release_tag, "github_post", lambda token: post)
    monkeypatch.setenv("GH_TOKEN", "an-app-token")
    _green_runs(monkeypatch, str(planted["readme"]))
    argv = ["--tag", "v1.2.3", "--commit", str(planted["readme"]), "--repo", str(planted["repo"])]
    assert (
        release_tag.main([*argv, "--repository", "owner/project", "--create", "--rehearsal"]) == 0
    )
    assert [body.get("tag") or body.get("ref") for _, body in post.calls] == [
        "rehearsal-v1.2.3",
        "refs/tags/rehearsal-v1.2.3",
    ]
    assert release_tag.tag_name("v1.2.3") == "v1.2.3"


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
