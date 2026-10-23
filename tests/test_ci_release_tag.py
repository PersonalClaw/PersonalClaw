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

PARSER = (
    "import re\n"
    "def parse_release_ref(ref):\n"
    "    if not re.fullmatch(r'v\\d+\\.\\d+\\.\\d+(-[0-9A-Za-z.]+)?', ref):\n"
    "        raise ValueError(f'{ref!r} is not a release tag (planted parser)')\n"
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


def test_main_refuses_to_create_without_the_controllers_token(
    planted: dict[str, object], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    argv = ["--tag", "v1.2.3", "--commit", str(planted["readme"]), "--repo", str(planted["repo"])]
    assert release_tag.main(argv) == 0
    assert release_tag.main([*argv, "--create", "owner/project"]) == 1
    assert "no GH_TOKEN" in capsys.readouterr().out
    assert (
        release_tag.main(
            ["--tag", "v1.2", "--commit", str(planted["readme"]), "--repo", str(planted["repo"])]
        )
        == 1
    )


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
    argv = ["--tag", "v1.2.3", "--commit", str(planted["readme"]), "--repo", str(planted["repo"])]
    assert release_tag.main([*argv, "--create", "owner/project", "--rehearsal"]) == 0
    assert [body.get("tag") or body.get("ref") for _, body in post.calls] == [
        "rehearsal-v1.2.3",
        "refs/tags/rehearsal-v1.2.3",
    ]
    assert release_tag.tag_name("v1.2.3") == "v1.2.3"
