"""``python -m tools.ci queue-readback`` (0.4.0 A1, P5): main's merge queue settings, read back.

Each case is a planted answer of the API: main as P5 leaves it, main as it is before P5, and one
condition broken at a time. Every gate gives its line, a read that fails is a ``FAIL`` and never
a pass, and the two proofs wait for a settled queue run.
"""

from __future__ import annotations

import copy
import datetime
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from tools.ci import queue_readback as rb

REPO = "o/r"
APP = 5444147
NOW = datetime.datetime(2026, 11, 2, 12, 0, tzinfo=datetime.UTC)
QUEUE_SHA = "a" * 40
BUILT_ON = "b" * 40
RULESET = 25732182


def _queue_rule(**over: object) -> dict:
    params: dict[str, object] = {
        "grouping_strategy": "ALLGREEN",
        "merge_method": "SQUASH",
        **rb.QUEUE_SIZES,
    }
    params.update(over)
    return {"type": "merge_queue", "ruleset_id": RULESET, "parameters": params}


def _checks_rule(strict: bool = False, pin: object = APP, context: str = "merge-verdict") -> dict:
    return {
        "type": "required_status_checks",
        "ruleset_id": RULESET,
        "parameters": {
            "strict_required_status_checks_policy": strict,
            "do_not_enforce_on_create": False,
            "required_status_checks": [{"context": context, "integration_id": pin}],
        },
    }


def _queue_run(**over: object) -> dict:
    run: dict[str, object] = {
        "id": 900,
        "event": "merge_group",
        "path": rb.MERGE_PATH,
        "head_branch": f"gh-readonly-queue/main/pr-5900-{BUILT_ON}",
        "head_sha": QUEUE_SHA,
        "status": "completed",
        "conclusion": "success",
        "updated_at": "2026-11-02T11:30:00Z",
    }
    run.update(over)
    return run


def _p5() -> dict[str, object]:
    """Main as P5 leaves it, after the first queue run: every gate holds."""
    return {
        f"repos/{REPO}/rules/branches/main?per_page=100": [
            {"type": "deletion", "ruleset_id": RULESET},
            {"type": "pull_request", "ruleset_id": RULESET, "parameters": {}},
            _checks_rule(),
            _queue_rule(),
        ],
        f"repos/{REPO}/rulesets/{RULESET}": {
            "id": RULESET,
            "name": "main-protect",
            "bypass_actors": [],
        },
        f"apps/{rb.CONTROLLER_SLUG}": {"id": APP, "slug": rb.CONTROLLER_SLUG},
        f"repos/{REPO}/environments/ci-controller/variables/CONTROLLER_APP_ID": {
            "name": "CONTROLLER_APP_ID",
            "value": str(APP),
        },
        f"repos/{REPO}/actions/variables/MERGE_QUEUE_LIVE": {
            "name": "MERGE_QUEUE_LIVE",
            "value": "true",
        },
        f"repos/{REPO}/actions/workflows/merge.yml/runs"
        "?event=merge_group&status=completed&per_page=100": {"workflow_runs": [_queue_run()]},
        f"repos/{REPO}/commits/{QUEUE_SHA}/check-runs?check_name=merge-verdict&per_page=100": {
            "check_runs": [
                {
                    "name": "merge-verdict",
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"id": APP},
                }
            ]
        },
        f"repos/{REPO}/actions/runs?head_sha={QUEUE_SHA}&per_page=100": {
            "workflow_runs": [
                {"id": 901, "path": "dynamic/github-code-scanning/codeql", "event": "dynamic"},
                {"id": 900, "path": rb.MERGE_PATH, "event": "merge_group"},
            ]
        },
    }


class Api:
    """The planted answers by path; an exception planted for a path is its read failing."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, path: str) -> Any:
        self.asked.append(path)
        if path not in self.answers:
            raise rb.Unread(f"GET {path}: Not Found", 404)
        answer = self.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


def _read(answers: dict[str, object]) -> dict[str, rb.Line]:
    lines = rb.readback(Api(answers), REPO, now=NOW)
    return {line.name: line for line in lines}


GATES = [
    "a merge queue on main",
    "ALLGREEN",
    "SQUASH",
    "the queue's sizes",
    "no bypass actor",
    "merge-verdict required on queue commits",
    "merge-verdict pinned to the controller App",
    "strict off",
    "MERGE_QUEUE_LIVE",
]


# ── main as P5 leaves it ──────────────────────────────────────────────────────────────────────


def test_main_as_p5_leaves_it_passes_every_gate_and_both_proofs() -> None:
    lines = rb.readback(Api(_p5()), REPO, now=NOW)
    assert [line.name for line in lines] == [*GATES, rb.POSTER, rb.CODEQL]
    assert [line.state for line in lines] == ["PASS"] * 10 + ["INFO"]
    by = {line.name: line for line in lines}
    assert "carries merge-verdict from personalclaw-ci-controller: success" in by[rb.POSTER].detail
    assert by[rb.CODEQL].detail.startswith("yes: default setup's run 901")
    assert rb.status(lines) == 0


def test_main_before_p5_fails_the_gates_p5_changes_and_waits_for_a_queue_run() -> None:
    answers = _p5()
    rules = f"repos/{REPO}/rules/branches/main?per_page=100"
    answers[rules] = [
        _checks_rule(strict=True) if r["type"] == "required_status_checks" else r
        for r in answers[rules]  # type: ignore[union-attr]
        if r["type"] != "merge_queue"
    ]
    del answers[f"repos/{REPO}/actions/variables/MERGE_QUEUE_LIVE"]
    answers[f"repos/{REPO}/actions/workflows/merge.yml/runs"
            "?event=merge_group&status=completed&per_page=100"] = {"workflow_runs": []}  # fmt: skip
    lines = rb.readback(Api(answers), REPO, now=NOW)
    states = {line.name: line.state for line in lines}
    assert [n for n, s in states.items() if s == "PASS"] == [
        "no bypass actor",
        "merge-verdict pinned to the controller App",
    ]
    assert states[rb.POSTER] == "WAIT" and states[rb.CODEQL] == "WAIT"
    by = {line.name: line for line in lines}
    assert (
        by["strict off"].detail
        == "strict_required_status_checks_policy reads [True], not all false"
    )
    assert by["MERGE_QUEUE_LIVE"].detail == "MERGE_QUEUE_LIVE is not set"
    assert rb.status(lines) == 1


# ── one condition broken at a time ────────────────────────────────────────────────────────────


def _rules(answers: dict[str, object]) -> list[dict]:
    return answers[f"repos/{REPO}/rules/branches/main?per_page=100"]  # type: ignore[return-value]


def _replace(kind: str, rule: dict) -> Callable[[dict], None]:
    def plant(answers: dict) -> None:
        rules = _rules(answers)
        rules[:] = [rule if r["type"] == kind else r for r in rules]

    return plant


def _set(path: str, value: object) -> Callable[[dict], None]:
    def plant(answers: dict) -> None:
        answers[path] = value

    return plant


def _two_queues(answers: dict) -> None:
    _rules(answers).append(_queue_rule())


def _another_ruleset_with_a_bypass(answers: dict) -> None:
    _rules(answers).append({"type": "non_fast_forward", "ruleset_id": 7})
    answers[f"repos/{REPO}/rulesets/7"] = {
        "id": 7,
        "name": "org-wide",
        "bypass_actors": [{"actor_id": 1, "actor_type": "OrganizationAdmin"}],
    }


def _two_check_rules_one_strict(answers: dict) -> None:
    _rules(answers).append(_checks_rule(strict=True, context="other"))


#: The gates one broken condition fails together: every gate that reads the queue's parameters
#: reads the one broken queue, and a check that is not required has no pin to read.
TOGETHER = {
    "a merge queue on main": {
        "a merge queue on main",
        "ALLGREEN",
        "SQUASH",
        "the queue's sizes",
        "merge-verdict required on queue commits",
    },
    "merge-verdict required on queue commits": {
        "merge-verdict required on queue commits",
        "merge-verdict pinned to the controller App",
    },
}
RULESET_PATH = f"repos/{REPO}/rulesets/{RULESET}"
VARIABLE_PATH = f"repos/{REPO}/actions/variables/MERGE_QUEUE_LIVE"
ENV_PATH = f"repos/{REPO}/environments/ci-controller/variables/CONTROLLER_APP_ID"


@pytest.mark.parametrize(
    ("plant", "gate", "says"),
    [
        pytest.param(_two_queues, "a merge queue on main", "2 merge_queue rules", id="two-queues"),
        pytest.param(
            _replace("merge_queue", _queue_rule(grouping_strategy="HEADGREEN")),
            "ALLGREEN",
            "grouping_strategy is 'HEADGREEN'",
            id="headgreen",
        ),
        pytest.param(
            _replace("merge_queue", _queue_rule(merge_method="MERGE")),
            "SQUASH",
            "merge_method is 'MERGE'",
            id="a-merge-commit",
        ),
        pytest.param(
            _replace("merge_queue", _queue_rule(max_entries_to_build=5)),
            "the queue's sizes",
            "max_entries_to_build is 5, not 3",
            id="five-built-at-once",
        ),
        pytest.param(
            _set(
                RULESET_PATH,
                {"id": RULESET, "name": "main-protect", "bypass_actors": [{"actor_id": 9}]},
            ),
            "no bypass actor",
            "ruleset 25732182 (main-protect) has 1 bypass actor(s), of type unnamed",
            id="a-bypass-actor",
        ),
        pytest.param(
            _set(RULESET_PATH, {"id": RULESET, "name": "main-protect"}),
            "no bypass actor",
            "not read: ruleset 25732182 (main-protect) answered without its bypass_actors",
            id="bypass-actors-hidden-from-the-caller",
        ),
        pytest.param(
            _another_ruleset_with_a_bypass,
            "no bypass actor",
            "ruleset 7 (org-wide) has 1 bypass actor(s), of type OrganizationAdmin",
            id="another-ruleset-s-bypass",
        ),
        pytest.param(
            _replace("required_status_checks", _checks_rule(context="results")),
            "merge-verdict required on queue commits",
            "merge-verdict is not a required check of main",
            id="merge-verdict-not-required",
        ),
        pytest.param(
            _replace("required_status_checks", _checks_rule(pin=None)),
            "merge-verdict pinned to the controller App",
            "pinned to None, not personalclaw-ci-controller's 5444147",
            id="any-app-may-post-it",
        ),
        pytest.param(
            _replace("required_status_checks", _checks_rule(pin=15368)),
            "merge-verdict pinned to the controller App",
            "pinned to 15368",
            id="pinned-to-another-app",
        ),
        pytest.param(
            _set(ENV_PATH, {"value": "123"}),
            "merge-verdict pinned to the controller App",
            "ci-controller's CONTROLLER_APP_ID is '123', not 5444147",
            id="the-poster-mints-another-app-s-token",
        ),
        pytest.param(
            _replace("required_status_checks", _checks_rule(strict=True)),
            "strict off",
            "reads [True], not all false",
            id="strict-on",
        ),
        pytest.param(
            _two_check_rules_one_strict,
            "strict off",
            "reads [False, True], not all false",
            id="a-second-rule-strict",
        ),
        pytest.param(
            _set(VARIABLE_PATH, {"value": "false"}),
            "MERGE_QUEUE_LIVE",
            "MERGE_QUEUE_LIVE is 'false', not 'true'",
            id="the-variable-false",
        ),
        pytest.param(
            _set(VARIABLE_PATH, rb.Unread("GET …: Forbidden", 403)),
            "MERGE_QUEUE_LIVE",
            "not read: GET …: Forbidden",
            id="the-variable-unreadable",
        ),
    ],
)
def test_each_condition_broken_alone_fails_its_own_gate_only(
    plant: Callable[[dict], None], gate: str, says: str
) -> None:
    answers = _p5()
    plant(answers)
    lines = rb.readback(Api(answers), REPO, now=NOW)
    failed = {line.name: line.detail for line in lines if line.state == "FAIL"}
    assert set(failed) == TOGETHER.get(gate, {gate})
    assert says in failed[gate]
    assert rb.status(lines) == 1


def test_a_bypass_fail_names_how_many_and_their_types_never_the_actors() -> None:
    """The output goes into a public pull request, and GitHub shows a ruleset's bypass list only
    to a caller who may edit it: a FAIL line names the count and the actor types alone."""
    answers = _p5()
    answers[RULESET_PATH] = {
        "id": RULESET,
        "name": "main-protect",
        "bypass_actors": [
            {"actor_id": 4242, "actor_type": "Integration", "bypass_mode": "always"},
            {"actor_id": 5151, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"},
            {"actor_id": 6161, "actor_type": "Integration", "bypass_mode": "always"},
        ],
    }
    lines = rb.readback(Api(answers), REPO, now=NOW)
    (line,) = [line for line in lines if line.name == "no bypass actor"]
    assert line.state == "FAIL"
    assert line.detail == (
        "ruleset 25732182 (main-protect) has 3 bypass actor(s), of type Integration, RepositoryRole"
    )
    text = "\n".join(line.text() for line in lines)
    assert "actor_id" not in text and "bypass_mode" not in text
    assert not any(ident in text for ident in ("4242", "5151", "6161"))


def test_rules_that_cannot_be_read_fail_every_gate_never_pass() -> None:
    answers = _p5()
    answers[f"repos/{REPO}/rules/branches/main?per_page=100"] = rb.Unread("GET …: HTTP 502", 502)
    lines = rb.readback(Api(answers), REPO, now=NOW)
    gates = [line for line in lines if line.name in GATES]
    assert [line.state for line in gates] == ["FAIL"] * len(GATES)
    assert all(line.detail == "not read: GET …: HTTP 502" for line in gates)


def test_rules_that_are_not_a_list_are_unread() -> None:
    answers = _p5()
    answers[f"repos/{REPO}/rules/branches/main?per_page=100"] = {"message": "Not Found"}
    lines = _read(answers)
    assert lines["ALLGREEN"].state == "FAIL"
    assert lines["ALLGREEN"].detail == "not read: main's rules is not a list"


# ── the two proofs: they wait for a settled queue run ─────────────────────────────────────────

RUNS_PATH = (
    f"repos/{REPO}/actions/workflows/merge.yml/runs"
    "?event=merge_group&status=completed&per_page=100"
)
CHECKS_PATH = f"repos/{REPO}/commits/{QUEUE_SHA}/check-runs?check_name=merge-verdict&per_page=100"


@pytest.mark.parametrize(
    ("runs", "says"),
    [
        pytest.param([], "no merge_group run of merge.yml has completed", id="none-yet"),
        pytest.param(
            [_queue_run(updated_at="2026-11-02T11:50:00Z")],
            "1 queue run(s) completed, none 15 minutes ago yet",
            id="one-too-new",
        ),
        pytest.param(
            [_queue_run(path=rb.MERGE_PATH.replace("merge.yml", "pr.yml"))],
            "no merge_group run of merge.yml has completed",
            id="another-workflow-s-run",
        ),
        pytest.param(
            [_queue_run(head_branch="main")],
            "no merge_group run of merge.yml has completed",
            id="off-the-queue-s-branch",
        ),
    ],
)
def test_the_proofs_wait_until_a_queue_run_has_settled(runs: list, says: str) -> None:
    answers = _p5()
    answers[RUNS_PATH] = {"workflow_runs": runs}
    lines = rb.readback(Api(answers), REPO, now=NOW)
    by = {line.name: line for line in lines}
    assert by[rb.POSTER].state == "WAIT" and says in by[rb.POSTER].detail
    assert by[rb.CODEQL].state == "WAIT"
    assert rb.status(lines) == 3


@pytest.mark.parametrize(
    "checks",
    [
        pytest.param([], id="no-verdict"),
        pytest.param(
            [{"name": "merge-verdict", "status": "completed", "app": {"id": 15368}}],
            id="another-app-s-verdict",
        ),
        pytest.param(
            [{"name": "merge-verdict", "status": "in_progress", "app": {"id": APP}}],
            id="a-verdict-never-completed",
        ),
    ],
)
def test_a_settled_queue_commit_without_the_controller_s_verdict_fails(checks: list) -> None:
    answers = _p5()
    answers[CHECKS_PATH] = {"check_runs": checks}
    lines = rb.readback(Api(answers), REPO, now=NOW)
    by = {line.name: line for line in lines}
    assert by[rb.POSTER].state == "FAIL"
    assert by[rb.POSTER].detail == (
        f"no completed merge-verdict from personalclaw-ci-controller on queue commit(s) "
        f"{QUEUE_SHA[:12]}"
    )
    assert rb.status(lines) == 1


def test_codeql_absent_on_queue_commits_is_a_fact_that_fails_no_gate() -> None:
    answers = _p5()
    answers[f"repos/{REPO}/actions/runs?head_sha={QUEUE_SHA}&per_page=100"] = {
        "workflow_runs": [{"id": 900, "path": rb.MERGE_PATH}]
    }
    lines = rb.readback(Api(answers), REPO, now=NOW)
    by = {line.name: line for line in lines}
    assert by[rb.CODEQL].state == "INFO"
    assert (
        by[rb.CODEQL].detail == f"no: default setup ran on none of queue commit(s) {QUEUE_SHA[:12]}"
    )
    assert rb.status(lines) == 0


def test_queue_runs_that_cannot_be_read_fail_the_poster() -> None:
    answers = _p5()
    answers[RUNS_PATH] = rb.Unread("GET …: HTTP 500", 500)
    by = _read(answers)
    assert by[rb.POSTER].state == "FAIL" and by[rb.POSTER].detail == "not read: GET …: HTTP 500"
    assert by[rb.CODEQL].state == "INFO" and by[rb.CODEQL].detail.startswith("not read")


# ── it only reads ─────────────────────────────────────────────────────────────────────────────


def test_every_read_is_a_plain_gh_api_get(monkeypatch) -> None:
    called: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        called.append(argv)
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rb.readback(rb.gh_get, REPO, now=NOW)
    assert called and all(argv[:2] == ["gh", "api"] and len(argv) == 3 for argv in called)
    assert not any(a.startswith("-") for argv in called for a in argv[2:])


def test_a_gh_error_names_its_http_status(monkeypatch) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.CalledProcessError(1, argv, "", "gh: Not Found (HTTP 404)\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(rb.Unread) as raised:
        rb.gh_get("repos/o/r/actions/variables/MERGE_QUEUE_LIVE")
    assert raised.value.status == 404
    assert str(raised.value) == (
        "GET repos/o/r/actions/variables/MERGE_QUEUE_LIVE: gh: Not Found (HTTP 404)"
    )


# ── the command ───────────────────────────────────────────────────────────────────────────────


def test_the_command_prints_a_line_a_condition_and_exits_by_the_gates(monkeypatch, capsys) -> None:
    api = Api(_p5())
    monkeypatch.setattr(rb, "gh_get", api)
    monkeypatch.setattr(rb, "_now", lambda: NOW)
    assert rb.main(["--repo", REPO]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split(" ", 1)[0] for line in out[:-1]] == ["PASS"] * 10 + ["INFO"]
    assert out[0] == "PASS a merge queue on main: main's rules hold one merge_queue rule"
    assert out[-1] == "queue-readback: 10 pass, 0 fail, 0 wait, 1 info"


def test_the_command_is_registered_and_loads_only_when_called(monkeypatch) -> None:
    from tools.ci import __main__ as commands

    monkeypatch.setattr(rb, "main", lambda argv: 7 if argv == ["--repo", REPO] else 0)
    assert commands.COMMANDS["queue-readback"](["--repo", REPO]) == 7
