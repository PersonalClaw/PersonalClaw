"""The tier a run plans (tools/ci/plan.py ``tier_for``, ``python -m tools.ci plan-tier``).

A pull request's run plans the entry tier only once the merge queue gates main: the repository's
``MERGE_QUEUE_LIVE`` is ``true`` and main's rules hold a merge_queue rule. Every other answer,
an unreadable rule included, is the merge tier, and the queue's own runs and pushes always plan
it. Each case is one planted answer; the rules are read through the merge controller's reader.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tools.ci import catalogue, merge_controller, merge_verdict
from tools.ci import plan as planner

_REPO = "PersonalClaw/PersonalClaw"
_RULES = f"/repos/{_REPO}/rules/branches/main?per_page=100"


def _rules(*types: str) -> list[dict[str, Any]]:
    return [{"type": t, "ruleset_id": 1} for t in types]


def _reader(answer: object) -> Callable[[str], Any]:
    """A reader of main's rules that answers ``answer``, or raises it when it is an exception."""

    def get(path: str) -> Any:
        assert path.startswith(f"/repos/{_REPO}/rules/branches/main"), path
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return get


def _live(answer: object) -> Callable[[], bool]:
    return lambda: merge_controller.queue_live(_reader(answer), _REPO)


def _never() -> bool:
    raise AssertionError("main's rules were read for a run that cannot plan the entry tier")


def test_the_rules_read_is_the_controllers() -> None:
    seen: list[str] = []

    def get(path: str) -> Any:
        seen.append(path)
        return _rules("merge_queue")

    assert merge_controller.queue_live(get, _REPO)
    assert seen == [_RULES]


def test_a_pull_request_plans_the_entry_tier_once_the_queue_gates_main() -> None:
    live = _live(_rules("pull_request", "merge_queue", "required_status_checks"))
    assert planner.tier_for("pull_request", "true", live) == catalogue.ENTRY_TIER == "entry"


def test_the_variable_without_the_rule_plans_the_merge_tier() -> None:
    """The pull request would still merge straight to main, with no queue run of the merge
    tier."""
    live = _live(_rules("pull_request", "required_status_checks"))
    assert planner.tier_for("pull_request", "true", live) == "merge"


@pytest.mark.parametrize("queue_var", ["false", "", "True", "1", "yes"])
def test_the_rule_without_the_variable_plans_the_merge_tier(queue_var: str) -> None:
    assert planner.tier_for("pull_request", queue_var, _never) == "merge"


@pytest.mark.parametrize(
    "answer",
    [
        OSError("no answer"),
        ValueError("not JSON"),
        {"message": "Not Found"},
        None,
        [{"type": 3}, "merge_queue"],
    ],
    ids=["no-answer", "not-json", "an-error-body", "null", "no-rule-shape"],
)
def test_rules_it_cannot_read_plan_the_merge_tier(answer: object) -> None:
    assert planner.tier_for("pull_request", "true", _live(answer)) == "merge"


@pytest.mark.parametrize(
    "event", ["merge_group", "push", "schedule", "workflow_dispatch", "pull_request_target"]
)
def test_every_other_run_plans_the_merge_tier_whatever_the_queue(event: str) -> None:
    assert planner.tier_for(event, "true", _never) == "merge"


# ── python -m tools.ci plan-tier ────────────────────────────────────────────────────────────


def _main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answer: object, *argv: str
) -> tuple[int, str, str]:
    tokens: list[str] = []

    def github_get(api: str, token: str) -> Callable[[str], Any]:
        assert api == merge_controller.API
        tokens.append(token)
        return _reader(answer)

    monkeypatch.setattr(merge_verdict, "github_get", github_get)
    monkeypatch.setenv("GH_TOKEN", "t0ken")
    code = planner.tier_main(list(argv))
    out, err = capsys.readouterr()
    assert tokens in ([], ["t0ken"])
    return code, out, err


def test_the_command_prints_the_entry_tier_once_the_queue_gates_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ("--event", "pull_request", "--queue-var", "true", "--repo", _REPO)
    assert _main(monkeypatch, capsys, _rules("merge_queue"), *argv) == (0, "entry\n", "")


@pytest.mark.parametrize(
    ("event", "queue_var", "answer"),
    [
        ("pull_request", "true", _rules("required_status_checks")),
        ("pull_request", "false", _rules("merge_queue")),
        ("pull_request", "true", OSError("no answer")),
        ("merge_group", "true", _rules("merge_queue")),
        ("push", "true", _rules("merge_queue")),
    ],
    ids=["no-rule", "no-variable", "unread", "queue-run", "push"],
)
def test_the_command_prints_the_merge_tier_otherwise(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    event: str,
    queue_var: str,
    answer: object,
) -> None:
    argv = ("--event", event, "--queue-var", queue_var, "--repo", _REPO)
    assert _main(monkeypatch, capsys, answer, *argv) == (0, "merge\n", "")


@pytest.mark.parametrize(
    "argv",
    [
        ("--event", "pull_request", "--queue-var", "true"),
        ("--event", "pull_request", "--queue-var", "true", "--repo", "not a repo"),
        ("--queue-var", "true", "--repo", _REPO),
    ],
    ids=["no-repo", "bad-repo", "no-event"],
)
def test_the_command_refuses_arguments_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
) -> None:
    code, out, _ = _main(monkeypatch, capsys, _rules("merge_queue"), *argv)
    assert (code, out) == (2, "")


def test_plan_tier_is_a_command() -> None:
    from tools.ci import __main__ as cli

    assert cli.COMMANDS["plan-tier"] is planner.tier_main
