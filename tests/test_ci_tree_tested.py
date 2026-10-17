"""``tools/ci/tree_tested.py``: a push to ``main`` skips only what a green pull-request run did.

Every case is a planted set of API answers. The check must answer yes for exactly one shape, a
merged head with the pushed tree, containing the previous ``main``, whose first ``pull_request``
run passed on its first attempt, and no for every other shape and for any error.
"""

from __future__ import annotations

import copy
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.ci import tree_tested as tt

REPO = "example/project"
SHA = "a" * 40
BEFORE = "b" * 40
HEAD = "c" * 40
TREE = "d" * 40
WORKFLOW = "ci.yml"
RUNS = f"/repos/{REPO}/actions/workflows/{WORKFLOW}/runs"
EVENTS = f"/repos/{REPO}/issues/7/events"


def _answers() -> dict[str, Any]:
    """The one shape that is tested: everything below agrees."""
    return {
        f"/repos/{REPO}/commits/{SHA}": {"commit": {"tree": {"sha": TREE}}},
        f"/repos/{REPO}/commits/{HEAD}": {"commit": {"tree": {"sha": TREE}}},
        f"/repos/{REPO}/commits/{SHA}/pulls": [
            {
                "number": 7,
                "merge_commit_sha": SHA,
                "created_at": "2026-10-16T09:00:00Z",
                "merged_at": "2026-10-17T00:00:00Z",
                "base": {"ref": "main"},
                "head": {"sha": HEAD},
            }
        ],
        f"/repos/{REPO}/compare/{BEFORE}...{HEAD}": {"behind_by": 0, "ahead_by": 1},
        RUNS: {
            "total_count": 2,
            "workflow_runs": [
                {
                    "id": 900,
                    "run_number": 50,
                    "head_sha": HEAD,
                    "event": "pull_request",
                    "created_at": "2026-10-16T09:01:00Z",
                },
                {
                    "id": 901,
                    "run_number": 51,
                    "head_sha": HEAD,
                    "event": "pull_request",
                    "created_at": "2026-10-16T10:00:00Z",
                },
            ],
        },
        EVENTS: [{"event": "labeled"}, {"event": "review_requested"}],
        f"/repos/{REPO}/actions/runs/900/attempts/1": {
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://example.com/runs/900",
        },
        f"/repos/{REPO}/actions/runs/901/attempts/1": {
            "status": "completed",
            "conclusion": "success",
        },
    }


def _getter(answers: dict[str, Any]):
    def get(path: str) -> Any:
        key = path.split("?", 1)[0]
        if key not in answers:
            raise OSError(f"no planted answer for {key}")
        value = answers[key]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    return get


def _decide(answers: dict[str, Any], **overrides: str) -> tt.Verdict:
    kwargs = {
        "event": "push",
        "ref": "refs/heads/main",
        "repo": REPO,
        "sha": SHA,
        "before": BEFORE,
        "workflow": WORKFLOW,
    }
    kwargs.update(overrides)
    return tt.decide(_getter(answers), **kwargs)


def test_an_identical_tree_whose_first_attempt_was_green_is_tested() -> None:
    verdict = _decide(_answers())
    assert verdict.tested, verdict.reason
    assert (verdict.pull_request, verdict.head, verdict.tree, verdict.run) == (7, HEAD, TREE, 900)
    text = tt.summary(verdict, WORKFLOW)
    for named in ("#7", HEAD, TREE, "900"):
        assert named in text


def _set(path: str, value: Any):
    def plant(answers: dict[str, Any]) -> None:
        answers[path] = value

    return plant


def _edit(path: str, edit):
    def plant(answers: dict[str, Any]) -> None:
        edit(answers[path])

    return plant


def _no_runs(answers: dict[str, Any]) -> None:
    answers[RUNS] = {"total_count": 0, "workflow_runs": []}


#: Every shape that must be a no, each one edit away from the tested shape.
NOT_TESTED = {
    "a tree that differs from the head's (a merge behind its base)": _set(
        f"/repos/{REPO}/commits/{HEAD}", {"commit": {"tree": {"sha": "e" * 40}}}
    ),
    "a head behind the previous main, whose tree still matches": _set(
        f"/repos/{REPO}/compare/{BEFORE}...{HEAD}", {"behind_by": 2, "ahead_by": 1}
    ),
    "no pull request": _set(f"/repos/{REPO}/commits/{SHA}/pulls", []),
    "a pull request merged as another commit": _edit(
        f"/repos/{REPO}/commits/{SHA}/pulls", lambda prs: prs[0].update(merge_commit_sha="f" * 40)
    ),
    "a pull request into another branch": _edit(
        f"/repos/{REPO}/commits/{SHA}/pulls", lambda prs: prs[0].update(base={"ref": "release"})
    ),
    "a red first attempt with a green rerun": _set(
        f"/repos/{REPO}/actions/runs/900/attempts/1",
        {"status": "completed", "conclusion": "failure"},
    ),
    "a cancelled first attempt": _set(
        f"/repos/{REPO}/actions/runs/900/attempts/1",
        {"status": "completed", "conclusion": "cancelled"},
    ),
    "a first attempt still running": _set(
        f"/repos/{REPO}/actions/runs/900/attempts/1", {"status": "in_progress", "conclusion": None}
    ),
    "no pull_request run of the head": _no_runs,
    "more runs than one page holds": _edit(RUNS, lambda page: page.update(total_count=500)),
    "a first run made before the pull request was opened (another pull request's)": _edit(
        RUNS, lambda page: page["workflow_runs"][0].update(created_at="2026-10-15T08:00:00Z")
    ),
    "a pull request whose base was changed to main": _set(
        EVENTS, [{"event": "labeled"}, {"event": "base_ref_changed"}]
    ),
    "a base changed automatically": _set(EVENTS, [{"event": "automatic_base_change_succeeded"}]),
    "more events than one page holds": _set(EVENTS, [{"event": "labeled"}] * 100),
    "events that are not a list": _set(EVENTS, {"message": "Not Found"}),
    "a later run on the head red on its first attempt (another pull request's)": _set(
        f"/repos/{REPO}/actions/runs/901/attempts/1",
        {"status": "completed", "conclusion": "failure"},
    ),
    "an API error": _set(f"/repos/{REPO}/compare/{BEFORE}...{HEAD}", OSError("HTTP 502")),
    "an answer of the wrong shape": _set(f"/repos/{REPO}/commits/{SHA}/pulls", {"message": "x"}),
}


@pytest.mark.parametrize("case", NOT_TESTED, ids=list(NOT_TESTED))
def test_every_other_shape_is_not_tested(case: str) -> None:
    answers = _answers()
    NOT_TESTED[case](answers)
    verdict = _decide(answers)
    assert not verdict.tested, case
    assert verdict.reason


def test_the_first_run_counts_whichever_order_the_api_lists_them_in() -> None:
    """The second run went green; the first did not. Listing the second first changes nothing."""
    answers = _answers()
    answers[RUNS]["workflow_runs"].reverse()
    answers[f"/repos/{REPO}/actions/runs/900/attempts/1"]["conclusion"] = "failure"
    assert not _decide(answers).tested


@pytest.mark.parametrize(
    "overrides",
    [
        {"event": "pull_request"},
        {"ref": "refs/heads/feature-x"},
        {"before": tt.ZERO},
        {"before": ""},
        {"sha": "main"},
    ],
    ids=["a pull request", "another branch", "a first push", "no before", "not a commit id"],
)
def test_anything_but_a_push_to_main_with_its_previous_commit_answers_no_at_once(
    overrides: dict[str, str],
) -> None:
    def refuse(path: str) -> Any:
        raise AssertionError(f"asked the API for {path}")

    verdict = tt.decide(
        refuse,
        **{
            "event": "push",
            "ref": "refs/heads/main",
            "repo": REPO,
            "sha": SHA,
            "before": BEFORE,
            "workflow": WORKFLOW,
            **overrides,
        },
    )
    assert not verdict.tested


def test_it_runs_as_one_standard_library_file(tmp_path: Path) -> None:
    """The workflow runs the previous main's copy of the file alone, on the image's Python."""
    copied = tmp_path / "tree_tested.py"
    shutil.copy(Path(tt.__file__), copied)
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    done = subprocess.run(
        [sys.executable, "-I", str(copied), "--workflow", WORKFLOW],
        cwd=tmp_path,
        env={
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF": "refs/pull/7/merge",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert output.read_text(encoding="utf-8") == "tested=false\n"
    assert "no green pull-request run tested this tree" in summary.read_text(encoding="utf-8")


def test_it_imports_only_the_standard_library() -> None:
    spec = importlib.util.spec_from_file_location("_tree_tested_alone", tt.__file__)
    assert spec is not None and spec.loader is not None
    imported = {
        line.split()[1].split(".")[0]
        for line in Path(tt.__file__).read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from ")) and not line.startswith("from __future__")
    }
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
