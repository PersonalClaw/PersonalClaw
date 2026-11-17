"""``tools/ci/landed.py``: each commit a push to ``main`` lands is the tree its pull request's merge
verdict verified (#3908, rule 4).

Every case is a planted git repository, whose ``main`` holds the controller revision and then the
landed commit, and a planted set of API answers: the commit's pull requests, main's rules, the
pull request's head and its ``merge-verdict`` check runs. One shape passes and is recorded; every
other shape is refused, and an answer that cannot be read is never a pass.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from tools.ci import landed
from tools.ci import merge_verdict as mv

pytestmark = pytest.mark.security

REPO = "example/project"
HEAD = "c" * 40
APP = 4242
OTHER_APP = 777
NUMBER = 7
RULES = f"/repos/{REPO}/rules/branches/main?per_page=100"


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
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
    )
    return proc.stdout.strip()


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    """main: the controller revision, then a landed squash commit that adds one file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "the controller")
    (repo / "change.txt").write_text("the pull request's change\n")
    _git(repo, "add", "change.txt")
    _git(repo, "commit", "-q", "-m", "the squash merge of #7")
    return repo


def _sha(repo: Path, rev: str) -> str:
    return _git(repo, "rev-parse", rev)


def _checks(page: int = 1, sha: str = HEAD) -> str:
    return (
        f"/repos/{REPO}/commits/{sha}/check-runs?check_name=merge-verdict&filter=all"
        f"&per_page=100&page={page}"
    )


def _queue_runs(sha: str) -> str:
    """The landed commit's own ``merge_group`` runs of merge.yml, as rule 4 asks for them."""
    return (
        f"/repos/{REPO}/actions/workflows/merge.yml/runs?head_sha={sha}&event=merge_group"
        "&per_page=100"
    )


def _run(repo: Path, tree: str, *, on: str = HEAD, **overrides: Any) -> dict[str, Any]:
    receipt = {
        "controller_revision": _sha(repo, "main~1"),
        "head_sha": on,
        "tree_oid": tree,
        "external_revisions": {},
        "toolchain": {"python": "3.13.16"},
        "evidence_digests": {"lint": "sha256:" + "b" * 64},
    }
    run: dict[str, Any] = {
        "id": 10,
        "name": mv.CHECK_NAME,
        "head_sha": on,
        "status": "completed",
        "conclusion": "success",
        "app": {"id": APP, "slug": "merge-controller"},
        "output": {"title": "Green", "summary": "", "text": mv.receipt_block(receipt)},
    }
    run.update(overrides)
    return run


def _answers(repo: Path, *, tree: str | None = None) -> dict[str, Any]:
    """The one shape that passes: the landed commit is #7's merge commit, and #7's head carries
    the controller's green verdict for the landed commit's own tree."""
    landed_sha = _sha(repo, "main")
    tree = tree or _sha(repo, "main^{tree}")
    return {
        f"/repos/{REPO}/commits/{landed_sha}/pulls?per_page=100": [
            {
                "number": NUMBER,
                "merge_commit_sha": landed_sha,
                "merged_at": "2026-10-26T10:00:00Z",
                "head": {"sha": HEAD},
            }
        ],
        RULES: [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": mv.CHECK_NAME, "integration_id": APP}]
                },
            }
        ],
        f"/repos/{REPO}/commits/{HEAD}": {"sha": HEAD, "commit": {"tree": {"sha": tree}}},
        _checks(): {"total_count": 1, "check_runs": [_run(repo, tree)]},
        # No queue run on the landed commit: the merge queue did not land it.
        _queue_runs(landed_sha): {"total_count": 0, "workflow_runs": []},
    }


def _getter(answers: dict[str, Any]) -> Callable[[str], Any]:
    def get(path: str) -> Any:
        if path not in answers:
            raise OSError(f"HTTP 404 for {path}")
        return copy.deepcopy(answers[path])

    return get


def _main(
    repo: Path, answers: dict[str, Any], tmp_path: Path, *, spec: str | None = None
) -> tuple[int, list[dict[str, Any]] | None]:
    record = tmp_path / "record.json"
    spec = spec or f"{_sha(repo, 'main~1')}..{_sha(repo, 'main')}"
    code = landed.main(
        ["--repo", REPO, "--range", spec, "--git-dir", str(repo), "--record", str(record)],
        get=_getter(answers),
    )
    return code, (json.loads(record.read_text()) if record.exists() else None)


def _pulls(repo: Path) -> str:
    return f"/repos/{REPO}/commits/{_sha(repo, 'main')}/pulls?per_page=100"


# ── The positive control ─────────────────────────────────────────────────────────────────────


def test_a_squash_that_lands_the_verified_tree_passes_and_is_recorded(
    planted: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, records = _main(planted, _answers(planted), tmp_path)
    assert code == 0, capsys.readouterr()
    assert records == [
        {
            "check_run_id": 10,
            "controller_revision": _sha(planted, "main~1"),
            "head_sha": HEAD,
            "main_sha": _sha(planted, "main"),
            "pull_request": NUMBER,
            "queue_run": None,
            "tree_oid": _sha(planted, "main^{tree}"),
            "verified_sha": HEAD,
        }
    ]
    assert "rule 4: no violation" in capsys.readouterr().err


# ── Rule 4 refuses ───────────────────────────────────────────────────────────────────────────


def test_a_commit_that_lands_another_tree_than_the_verified_one_fails(
    planted: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verified = "d" * 40
    code, records = _main(planted, _answers(planted, tree=verified), tmp_path)
    out = capsys.readouterr().out
    assert code == 1
    assert records == []
    assert f"{_sha(planted, 'main')} rule 4 lands tree {_sha(planted, 'main^{tree}')}" in out
    assert f"not {verified}, the tree #{NUMBER}'s verdict verified on its head {HEAD}" in out


@pytest.mark.parametrize(
    "pulls",
    [
        pytest.param([], id="no pull request at all, as a commit made in the web editor"),
        pytest.param(
            [{"number": 8, "merge_commit_sha": "e" * 40, "merged_at": "x", "head": {"sha": HEAD}}],
            id="a pull request whose merge commit is another",
        ),
        pytest.param(
            [{"number": 8, "merge_commit_sha": None, "merged_at": None, "head": {"sha": HEAD}}],
            id="an open pull request that holds the commit",
        ),
    ],
)
def test_a_commit_no_merged_pull_request_landed_fails(
    planted: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], pulls: list[Any]
) -> None:
    answers = _answers(planted)
    for pull in pulls:
        if pull["merge_commit_sha"] is None:
            pull["merge_commit_sha"] = _sha(planted, "main")
    answers[_pulls(planted)] = pulls
    code, records = _main(planted, answers, tmp_path)
    assert code == 1 and records == []
    assert "is the merge commit of no merged pull request" in capsys.readouterr().out


def test_a_commit_two_pull_requests_name_as_their_merge_commit_fails(
    planted: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = _answers(planted)
    second = dict(answers[_pulls(planted)][0], number=9)
    answers[_pulls(planted)].append(second)
    code, _records = _main(planted, answers, tmp_path)
    assert code == 1
    assert "more than one pull request (#7, #9)" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("overrides", "says"),
    [
        pytest.param({"conclusion": "failure"}, "completed/failure", id="a red verdict"),
        pytest.param(
            {"status": "in_progress", "conclusion": None}, "in_progress", id="a pending verdict"
        ),
        pytest.param(
            {"app": {"id": OTHER_APP, "slug": "someone"}},
            f"no {mv.CHECK_NAME} check run by App {APP}",
            id="a verdict another App posted",
        ),
    ],
)
def test_a_commit_whose_pull_requests_verdict_does_not_count_fails(
    planted: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, Any],
    says: str,
) -> None:
    answers = _answers(planted)
    answers[_checks()]["check_runs"] = [_run(planted, _sha(planted, "main^{tree}"), **overrides)]
    code, records = _main(planted, answers, tmp_path)
    out = capsys.readouterr().out
    assert code == 1 and records == []
    assert f"lands #{NUMBER}, whose verdict on its head {HEAD} does not count" in out
    assert says in out


def test_a_controller_revision_that_is_not_on_main_fails(
    planted: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(planted, "switch", "-q", "-c", "candidate", "main~1")
    _git(planted, "commit", "-q", "--allow-empty", "-m", "a candidate's own controller")
    elsewhere = _sha(planted, "candidate")
    _git(planted, "switch", "-q", "main")
    answers = _answers(planted)
    run = answers[_checks()]["check_runs"][0]
    receipt = mv.read_receipt(run)
    text = mv.receipt_block({**receipt.__dict__, "controller_revision": elsewhere})
    run["output"]["text"] = text
    code, _records = _main(planted, answers, tmp_path)
    assert code == 1
    assert f"{elsewhere} is not on {_sha(planted, 'main')}" in capsys.readouterr().out


def test_every_commit_of_the_range_is_held_and_each_one_named(
    planted: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A push of two commits: the first passes and is recorded, the second has no pull request."""
    first = _sha(planted, "main")
    answers = _answers(planted)
    _git(planted, "commit", "-q", "--allow-empty", "-m", "a commit made in the web editor")
    second = _sha(planted, "main")
    answers[f"/repos/{REPO}/commits/{second}/pulls?per_page=100"] = []
    code, records = _main(planted, answers, tmp_path, spec=f"{_sha(planted, 'main~2')}..{second}")
    out = capsys.readouterr().out
    assert code == 1
    assert [r["main_sha"] for r in records or []] == [first]
    assert f"{second} rule 4 is the merge commit of no merged pull request" in out
    assert first not in out


# ── A queue commit is held to its own verdict (0.4.0 A1; #3908's Done-when, item 5) ──────────
# Under main's merge queue the commit that lands is the queue commit itself: main as it was, plus
# the entry, built and tested before main reached it. Its tree is the head's only when the head
# was up to date, so its verdict is the one the controller posted on the queue commit, and its
# receipt must name that commit and its tree.

QUEUE_BRANCH_OF_7 = f"gh-readonly-queue/main/pr-{NUMBER}-{'e' * 40}"


def _queue_run(sha: str, **overrides: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "id": 501,
        "run_number": 9,
        "path": ".github/workflows/merge.yml",
        "event": "merge_group",
        "head_sha": sha,
        "head_branch": QUEUE_BRANCH_OF_7,
        "status": "completed",
        "conclusion": "success",
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
    }
    run.update(overrides)
    return run


def _queued(repo: Path) -> dict[str, Any]:
    """#7 entered the queue on a head behind main, so its head's verdict names the head's own tree
    (another than the landed one), and the queue commit carries the controller's green verdict
    for its own tree."""
    landed_sha, tree = _sha(repo, "main"), _sha(repo, "main^{tree}")
    answers = _answers(repo, tree="f" * 40)
    answers[_queue_runs(landed_sha)] = {"total_count": 1, "workflow_runs": [_queue_run(landed_sha)]}
    answers[f"/repos/{REPO}/commits/{landed_sha}"] = {
        "sha": landed_sha,
        "commit": {"tree": {"sha": tree}},
    }
    answers[_checks(sha=landed_sha)] = {
        "total_count": 1,
        "check_runs": [_run(repo, tree, on=landed_sha, id=11)],
    }
    return answers


def test_a_queue_landing_from_a_head_behind_main_is_held_to_its_queue_commits_verdict(
    planted: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, records = _main(planted, _queued(planted), tmp_path)
    assert code == 0, capsys.readouterr()
    landed_sha = _sha(planted, "main")
    assert records == [
        {
            "check_run_id": 11,
            "controller_revision": _sha(planted, "main~1"),
            "head_sha": HEAD,
            "main_sha": landed_sha,
            "pull_request": NUMBER,
            "queue_run": 501,
            "tree_oid": _sha(planted, "main^{tree}"),
            "verified_sha": landed_sha,
        }
    ]


def test_a_queue_landing_without_a_verdict_of_its_own_fails(
    planted: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The head's verdict never stands in for the queue commit's: here it even names the landed
    tree, and the landing still fails."""
    answers = _queued(planted)
    landed_sha = _sha(planted, "main")
    answers[_checks(sha=landed_sha)] = {"total_count": 0, "check_runs": []}
    answers[_checks()] = {
        "total_count": 1,
        "check_runs": [_run(planted, _sha(planted, "main^{tree}"))],
    }
    code, records = _main(planted, answers, tmp_path)
    out = capsys.readouterr().out
    assert code == 1 and records == []
    assert f"lands #{NUMBER} as a queue commit, whose verdict on itself does not count" in out


@pytest.mark.parametrize(
    ("receipt", "says"),
    [
        pytest.param(
            {"head_sha": HEAD}, f"the receipt names head {HEAD}", id="a receipt for the head"
        ),
        pytest.param(
            {"tree_oid": "a" * 40},
            "the receipt names tree " + "a" * 40,
            id="a receipt for another tree",
        ),
    ],
)
def test_a_queue_commits_verdict_counts_only_for_that_commit_and_its_tree(
    planted: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    receipt: dict[str, str],
    says: str,
) -> None:
    answers = _queued(planted)
    run = answers[_checks(sha=_sha(planted, "main"))]["check_runs"][0]
    run["output"]["text"] = mv.receipt_block({**mv.read_receipt(run).__dict__, **receipt})
    code, records = _main(planted, answers, tmp_path)
    out = capsys.readouterr().out
    assert code == 1 and records == []
    assert "as a queue commit, whose verdict on itself does not count" in out and says in out


@pytest.mark.parametrize(
    ("overrides", "says"),
    [
        pytest.param(
            {"head_branch": f"gh-readonly-queue/main/pr-8-{'e' * 40}"},
            f"names no queue entry of #{NUMBER}",
            id="a queue run of another pull request",
        ),
        pytest.param(
            {"head_branch": f"gh-readonly-queue/main/pr-{NUMBER}-{'e' * 40}-x"},
            f"names no queue entry of #{NUMBER}",
            id="a queue branch name with more after it",
        ),
        pytest.param(
            {"head_branch": "feature"},
            f"names no queue entry of #{NUMBER}",
            id="a run on no queue branch",
        ),
        pytest.param(
            {"conclusion": "failure"}, "concluded failure", id="a queue run that did not pass"
        ),
        pytest.param(
            {"status": "in_progress", "conclusion": None},
            "concluded in_progress",
            id="a queue run still going",
        ),
        pytest.param(
            {"repository": {"full_name": "someone/fork"}},
            "a merge_group run of another repository",
            id="a run in another repository",
        ),
        pytest.param(
            {"head_repository": {"full_name": "someone/fork"}},
            "a merge_group run of another repository",
            id="a run from a fork's head",
        ),
    ],
)
def test_a_queue_run_that_does_not_stand_for_the_entry_fails(
    planted: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, Any],
    says: str,
) -> None:
    answers = _queued(planted)
    landed_sha = _sha(planted, "main")
    answers[_queue_runs(landed_sha)]["workflow_runs"] = [_queue_run(landed_sha, **overrides)]
    code, records = _main(planted, answers, tmp_path)
    out = capsys.readouterr().out
    assert code == 1 and records == []
    assert says in out


@pytest.mark.parametrize(
    ("older", "newer", "code"),
    [
        pytest.param("success", "failure", 1, id="a newer run that failed after an older passed"),
        pytest.param("failure", "success", 0, id="a newer run that passed after an older failed"),
    ],
)
def test_the_newest_queue_run_on_the_commit_decides(
    planted: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    older: str,
    newer: str,
    code: int,
) -> None:
    """A queue commit re-queued runs merge.yml again: the newest run is the one that stands."""
    answers = _queued(planted)
    landed_sha = _sha(planted, "main")
    answers[_queue_runs(landed_sha)] = {
        "total_count": 2,
        "workflow_runs": [
            _queue_run(landed_sha, id=601, run_number=12, conclusion=newer),
            _queue_run(landed_sha, id=501, run_number=9, conclusion=older),
        ],
    }
    got, records = _main(planted, answers, tmp_path)
    out = capsys.readouterr().out
    assert got == code, out
    if code:
        assert records == [] and "newest merge.yml merge_group run 601 concluded failure" in out
    else:
        assert records and records[0]["queue_run"] == 601


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("repository", "example/project", id="a repository that is not an object"),
        pytest.param("head_repository", None, id="no head repository"),
        pytest.param("run_number", "9", id="a run number that is not a number"),
        pytest.param("id", True, id="an id that is a boolean"),
    ],
)
def test_a_queue_run_whose_fields_cannot_be_read_is_no_pass(
    planted: Path, tmp_path: Path, key: str, value: Any
) -> None:
    answers = _queued(planted)
    landed_sha = _sha(planted, "main")
    answers[_queue_runs(landed_sha)]["workflow_runs"] = [_queue_run(landed_sha, **{key: value})]
    code, records = _main(planted, answers, tmp_path)
    assert code == 2 and records is None


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"event": "push"}, id="a push to a branch named as a queue's"),
        pytest.param({"head_sha": "9" * 40}, id="a queue run of another commit"),
    ],
)
def test_what_is_not_the_landed_commits_own_queue_run_lands_it_by_its_head(
    planted: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, Any],
) -> None:
    """A run counts as the queue's only as a merge_group run of merge.yml on the landed commit
    itself; a queue-shaped branch name alone is never one. Without it the head's verdict is read,
    and here it names the head's own tree, not the landed one."""
    answers = _queued(planted)
    landed_sha = _sha(planted, "main")
    answers[_queue_runs(landed_sha)]["workflow_runs"] = [_queue_run(landed_sha, **overrides)]
    code, records = _main(planted, answers, tmp_path)
    out = capsys.readouterr().out
    assert code == 1 and records == []
    assert f"the tree #{NUMBER}'s verdict verified on its head {HEAD}" in out


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(None, id="the runs that cannot be read"),
        pytest.param({"total_count": 2, "workflow_runs": ["one"]}, id="runs past one page"),
        pytest.param({"total_count": 1}, id="a page with no runs list"),
        pytest.param(
            {"total_count": 1, "workflow_runs": [{"path": ".github/workflows/pr.yml"}]},
            id="a run of another workflow in merge.yml's list",
        ),
    ],
)
def test_queue_runs_that_cannot_be_read_are_no_pass(
    planted: Path, tmp_path: Path, page: dict[str, Any] | None
) -> None:
    answers = _queued(planted)
    landed_sha = _sha(planted, "main")
    if page is None:
        del answers[_queue_runs(landed_sha)]
    else:
        if page.get("workflow_runs") == ["one"]:
            page["workflow_runs"] = [_queue_run(landed_sha)]
        answers[_queue_runs(landed_sha)] = page
    code, records = _main(planted, answers, tmp_path)
    assert code == 2 and records is None


def test_main_s_rules_never_decide_which_verdict_a_landing_is_held_to(
    planted: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A landing is judged by what stands behind it, never by main's rules as they read when the
    check runs: with no queue run of its own, a commit is held to its head's verdict and the
    landed tree whether or not main queues its merges by then, so a rerun after the queue went
    live judges a landing from before it as it was judged when it landed."""
    answers = _answers(planted)
    answers[RULES].append({"type": "merge_queue", "parameters": {"merge_method": "SQUASH"}})
    code, records = _main(planted, answers, tmp_path)
    assert code == 0, capsys.readouterr()
    assert records and records[0]["queue_run"] is None and records[0]["verified_sha"] == HEAD


def test_the_queue_branch_is_the_one_main_replay_reads() -> None:
    from tools.ci import main_replay

    assert landed.QUEUE_BRANCH.pattern == main_replay.QUEUE_BRANCH.pattern


# ── What cannot be read never passes ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "drop",
    [
        pytest.param("pulls", id="the commit's pull requests"),
        pytest.param("rules", id="main's rules"),
        pytest.param("checks", id="the head's check runs"),
    ],
)
def test_an_answer_that_cannot_be_read_is_no_pass_and_writes_no_record(
    planted: Path, tmp_path: Path, drop: str
) -> None:
    answers = _answers(planted)
    del answers[{"pulls": _pulls(planted), "rules": RULES, "checks": _checks()}[drop]]
    code, records = _main(planted, answers, tmp_path)
    assert code == 2 and records is None


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param("main~1..main", id="names, not commit ids"),
        pytest.param(f"{'0' * 40}..{'1' * 40}", id="commits the clone does not hold"),
        pytest.param("same", id="an empty range"),
    ],
)
def test_a_range_that_brings_nothing_checkable_is_no_pass(
    planted: Path, tmp_path: Path, spec: str
) -> None:
    if spec == "same":
        spec = f"{_sha(planted, 'main')}..{_sha(planted, 'main')}"
    (tmp_path / "record.json").write_text("[]")
    code, records = _main(planted, _answers(planted), tmp_path, spec=spec)
    assert code == 2 and records is None, "a stale record is removed, never left to be read"


# ── verified-tree.yml's own shell, run against scratch pushes ────────────────────────────────
# Its shape is the CI contract row `verified-tree-holds-every-main-push-with-mains-previous-rule`.
# What is left here is behaviour: which copy of the rule judges a push.

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "verified-tree.yml"
_RULE = "tools/ci/landed.py"


def _judge_step() -> tuple[str, dict[str, Any]]:
    [job] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"].values()
    step = next(s for s in job["steps"] if "verify-landed" in str(s.get("run", "")))
    return str(step["run"]), dict(step.get("env") or {})


def _stand_in(label: str, code: int) -> dict[str, str | None]:
    """A tools/ci whose verify-landed says which copy ran and with what, then exits *code*."""
    main = (
        "import sys\n"
        f"print('ran:{label}')\n"
        "print('args:' + ' '.join(sys.argv[1:]))\n"
        "args = sys.argv[1:]\n"
        "open(args[args.index('--record') + 1], 'w').write('[]\\n')\n"
        f"sys.exit({code})\n"
    )
    return {"tools/ci/__init__.py": "", "tools/ci/__main__.py": main, _RULE: "# the rule\n"}


class _Pushes:
    """A scratch main whose commits carry stand-in rules, to run the job's own shell."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True)
        _git(root, "init", "-q", "-b", "main")

    def commit(self, files: dict[str, str | None]) -> str:
        for rel, text in files.items():
            path = self.root / rel
            if text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "--allow-empty", "-m", "c")
        return _sha(self.root, "HEAD")

    def push(
        self, before: str, after: str, tmp: Path, bin_dir: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        run, env = _judge_step()
        assert set(env) == {"BEFORE", "GH_TOKEN"}
        runner = tmp / "runner"
        runner.mkdir(exist_ok=True)
        py = tmp / "py"
        py.mkdir(exist_ok=True)
        (py / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        (py / "python3").chmod(0o755)
        path = f"{py}{os.pathsep}{os.environ['PATH']}"
        if bin_dir is not None:
            path = f"{bin_dir}{os.pathsep}{path}"
        scrubbed = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(
            ["bash", "-c", run],
            cwd=self.root,
            env={
                **scrubbed,
                "PATH": path,
                "HOME": str(tmp),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "BEFORE": before,
                "GH_TOKEN": "",
                "GITHUB_SHA": after,
                "GITHUB_REPOSITORY": REPO,
                "GITHUB_WORKSPACE": str(self.root),
                "RUNNER_TEMP": str(runner),
                "GITHUB_STEP_SUMMARY": str(tmp / "summary.md"),
            },
            capture_output=True,
            text=True,
            timeout=60,
        )


def test_a_push_is_judged_by_the_previous_commits_rule(tmp_path: Path) -> None:
    """The pushed commit edits the rule to pass itself. That is not what runs."""
    pushes = _Pushes(tmp_path / "main")
    before = pushes.commit(_stand_in("before", 0))
    after = pushes.commit(_stand_in("pushed", 0))
    done = pushes.push(before, after, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ran:before" in done.stdout and "pushed" not in done.stdout
    assert f"args:verify-landed --repo {REPO} --range {before}..{after}" in done.stdout


def test_the_previous_commits_verdict_is_the_jobs(tmp_path: Path) -> None:
    pushes = _Pushes(tmp_path / "main")
    before = pushes.commit(_stand_in("before", 1))
    after = pushes.commit(_stand_in("pushed", 0))
    assert pushes.push(before, after, tmp_path).returncode == 1


def test_the_push_that_adds_the_rule_is_judged_by_its_own_copy(tmp_path: Path) -> None:
    pushes = _Pushes(tmp_path / "main")
    before = pushes.commit({"README": "x"})
    after = pushes.commit(_stand_in("pushed", 0))
    done = pushes.push(before, after, tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ran:pushed" in done.stdout
    assert "never had the rule" in (tmp_path / "summary.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("before", ["0" * 40, "e" * 40], ids=["all-zeros", "not-in-the-checkout"])
def test_a_previous_commit_that_does_not_resolve_refuses_the_push(
    tmp_path: Path, before: str
) -> None:
    pushes = _Pushes(tmp_path / "main")
    pushes.commit({"README": "x"})
    after = pushes.commit(_stand_in("pushed", 0))
    done = pushes.push(before, after, tmp_path)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "ran:" not in done.stdout


def test_a_previous_commit_that_lost_the_rule_refuses_the_push(tmp_path: Path) -> None:
    pushes = _Pushes(tmp_path / "main")
    pushes.commit(_stand_in("old", 0))
    before = pushes.commit({_RULE: None})
    after = pushes.commit(_stand_in("pushed", 0))
    done = pushes.push(before, after, tmp_path)
    assert done.returncode == 2, done.stdout + done.stderr
    assert "ran:" not in done.stdout


def _a_git_whose_log_fails(bin_dir: Path) -> None:
    """A ``git`` that hands every command but ``log`` to the real one, and fails ``log`` the way
    a git that cannot read the history does."""
    real = shutil.which("git")
    assert real, "no git on PATH"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "log" ]; then echo "fatal: a stand-in failure" >&2; exit 128; fi\n'
        f'exec "{real}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)


@pytest.mark.parametrize("lost", [True, False], ids=["lost-the-rule", "never-had-it"])
def test_a_history_git_cannot_read_refuses_the_push(tmp_path: Path, lost: bool) -> None:
    """A git that cannot read the previous commit's history of the rule has not found it empty:
    the job stops with 2, and no copy runs."""
    pushes = _Pushes(tmp_path / "main")
    if lost:
        pushes.commit(_stand_in("old", 0))
        before = pushes.commit({_RULE: None})
    else:
        before = pushes.commit({"README": "x"})
    after = pushes.commit(_stand_in("pushed", 0))
    _a_git_whose_log_fails(tmp_path / "bin")
    done = pushes.push(before, after, tmp_path, bin_dir=tmp_path / "bin")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "ran:" not in done.stdout
    assert "history of the rule could not be read" in done.stdout + done.stderr
    summary = tmp_path / "summary.md"
    assert not summary.exists() or "never had" not in summary.read_text(encoding="utf-8")
