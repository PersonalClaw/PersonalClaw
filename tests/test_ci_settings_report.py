"""The weekly read-only settings report reads each setting and fails on every difference.

``tools/ci/settings_report.py`` runs against a fake GitHub API holding one repository whose
settings match its expected entry and its rulesets file; each case plants one change in the
live answers and checks that the report names it, and that the unchanged answers pass. A
setting the API does not answer fails as not read, never as a pass.
"""

from __future__ import annotations

import base64
import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tools.ci import settings_report as sr

REPO = "owner/project"
APP_ID = 815
ACTIONS_APP = 15368


class FakeAPI:
    """Answers GETs from a table, by path; every other path is GitHub's plain 404."""

    def __init__(self, answers: dict[str, tuple[int, object]]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def __call__(self, path: str) -> tuple[int, object]:
        self.calls.append(path)
        return self.answers.get(path, (404, {"message": "Not Found"}))


def _page(path: str) -> str:
    joiner = "&" if "?" in path else "?"
    return f"{path}{joiner}per_page=100&page=1"


MAIN_PROTECT: dict[str, Any] = {
    "name": "main-protect",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
    "rules": [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [{"context": "merge-verdict", "integration_id": APP_ID}],
            },
        },
    ],
    "bypass_actors": [],
}

EXPECTED: dict[str, Any] = {
    "merge methods": {
        "allow_merge_commit": False,
        "allow_rebase_merge": False,
        "allow_squash_merge": True,
    },
    "classic protection": None,
    "security": {
        "dependabot_security_updates": "enabled",
        "secret_scanning": "enabled",
        "secret_scanning_push_protection": "enabled",
    },
    "dependabot alerts": True,
    "dependency graph": True,
    "codeql default setup": {
        "languages": ["actions", "python"],
        "query_suite": "default",
        "state": "configured",
    },
    "actions": {"allowed_actions": "all", "enabled": True, "sha_pinning_required": True},
    "workflow token": {
        "can_approve_pull_request_reviews": False,
        "default_workflow_permissions": "read",
    },
    "fork approval": "first_time_contributors",
    "environments": {
        "release": {
            "deployment": ["tag:v*"],
            "prevent_self_review": False,
            "reviewers": ["User:maintainer"],
            "wait_timer": 0,
        }
    },
    "allowed bypass": [],
}


def _answers(ruleset: dict[str, Any] | None = None) -> dict[str, tuple[int, object]]:
    live = dict(copy.deepcopy(ruleset or MAIN_PROTECT), id=7, source="owner/project")
    live.pop("bypass_actors", None)  # not shown to a read-only caller
    content = base64.b64encode(json.dumps(MAIN_PROTECT).encode()).decode()
    r = f"/repos/{REPO}"
    return {
        f"{r}/contents/{sr.RULESETS_DIR}": (200, [{"name": "main-protect.json"}]),
        f"{r}/contents/{sr.RULESETS_DIR}/main-protect.json": (200, {"content": content}),
        _page(f"{r}/rulesets?includes_parents=false"): (200, [{"id": 7, "name": "main-protect"}]),
        f"{r}/rulesets/7": (200, live),
        _page(f"{r}/rulesets/rule-suites?time_period=week&rule_suite_result=bypass"): (200, []),
        r: (
            200,
            {
                "default_branch": "main",
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
                "allow_squash_merge": True,
                "security_and_analysis": {
                    "secret_scanning": {"status": "enabled"},
                    "secret_scanning_push_protection": {"status": "enabled"},
                    "dependabot_security_updates": {"status": "enabled"},
                },
            },
        ),
        f"{r}/branches/main/protection": (404, {"message": "Branch not protected"}),
        f"{r}/actions/permissions": (
            200,
            {"enabled": True, "allowed_actions": "all", "sha_pinning_required": True},
        ),
        f"{r}/actions/permissions/workflow": (
            200,
            {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
        ),
        f"{r}/actions/permissions/fork-pr-contributor-approval": (
            200,
            {"approval_policy": "first_time_contributors"},
        ),
        f"{r}/code-scanning/default-setup": (
            200,
            {"state": "configured", "query_suite": "default", "languages": ["python", "actions"]},
        ),
        f"{r}/vulnerability-alerts": (204, None),
        f"{r}/dependency-graph/sbom": (200, {"sbom": {"packages": [{"name": "npm:left-pad"}]}}),
        _page(f"{r}/environments"): (
            200,
            {
                "environments": [
                    {
                        "name": "release",
                        "protection_rules": [
                            {
                                "type": "required_reviewers",
                                "prevent_self_review": False,
                                "reviewers": [
                                    {"type": "User", "reviewer": {"login": "maintainer"}}
                                ],
                            },
                            # GitHub lists a custom deployment policy as a rule too.
                            {"type": "branch_policy"},
                        ],
                        "deployment_branch_policy": {
                            "protected_branches": False,
                            "custom_branch_policies": True,
                        },
                    }
                ]
            },
        ),
        _page(f"{r}/environments/release/deployment-branch-policies"): (
            200,
            {"branch_policies": [{"name": "v*", "type": "tag"}]},
        ),
    }


def _run(
    plant: Callable[[dict[str, tuple[int, object]]], None] | None = None,
    *,
    expected: dict[str, Any] | None = None,
    admin: bool = True,
) -> list[str]:
    answers = _answers()
    if plant:
        plant(answers)
    api = FakeAPI(answers)
    drift = sr.report({REPO: expected or EXPECTED}, api, api if admin else None)
    return [d.line() for d in drift]


def _set(
    path: str, *keys: str | int, value: object
) -> Callable[[dict[str, tuple[int, object]]], None]:
    """Set ``keys`` inside the answer at *path* (``/repos/owner/project`` + *path*)."""

    def plant(answers: dict[str, tuple[int, object]]) -> None:
        at = f"/repos/{REPO}{path}"
        status, body = answers[at]
        body = copy.deepcopy(body)
        node: Any = body
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
        answers[at] = (status, body)

    return plant


def test_the_unchanged_settings_pass() -> None:
    assert _run() == []


def test_a_secret_scanning_turned_off_fails() -> None:
    lines = _run(_set("", "security_and_analysis", "secret_scanning", value={"status": "disabled"}))
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} security:")
    assert lines[0].endswith('secret_scanning expected "enabled", live "disabled"')


def test_a_push_protection_turned_off_fails() -> None:
    plant = _set(
        "",
        "security_and_analysis",
        "secret_scanning_push_protection",
        value={"status": "disabled"},
    )
    lines = _run(plant)
    assert (
        len(lines) == 1
        and 'secret_scanning_push_protection expected "enabled", live "disabled"' in lines[0]
    )


def test_codeql_default_setup_not_configured_fails() -> None:
    lines = _run(_set("/code-scanning/default-setup", "state", value="not-configured"))
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} codeql default setup:")


def test_sha_pinning_turned_off_fails() -> None:
    lines = _run(_set("/actions/permissions", "sha_pinning_required", value=False))
    assert len(lines) == 1 and lines[0].endswith("sha_pinning_required expected true, live false")


def test_a_widened_workflow_token_fails() -> None:
    lines = _run(
        _set("/actions/permissions/workflow", "default_workflow_permissions", value="write")
    )
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} workflow token:")


def test_actions_allowed_to_approve_pull_requests_fails() -> None:
    plant = _set("/actions/permissions/workflow", "can_approve_pull_request_reviews", value=True)
    assert len(_run(plant)) == 1


def test_a_changed_fork_approval_policy_fails() -> None:
    plant = _set(
        "/actions/permissions/fork-pr-contributor-approval",
        "approval_policy",
        value="first_time_contributors_new_to_github",
    )
    lines = _run(plant)
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} fork approval:")


def test_a_merge_method_turned_back_on_fails() -> None:
    lines = _run(_set("", "allow_rebase_merge", value=True))
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} merge methods:")


def test_an_environment_open_to_every_ref_fails() -> None:
    plant = _set(
        "/environments?per_page=100&page=1",
        "environments",
        0,
        "deployment_branch_policy",
        value=None,
    )
    lines = _run(plant)
    assert len(lines) == 1 and lines[0].endswith(
        'release.deployment expected ["tag:v*"], live "any ref"'
    )


def test_an_environment_that_lost_its_reviewer_fails() -> None:
    plant = _set(
        "/environments?per_page=100&page=1", "environments", 0, "protection_rules", value=[]
    )
    lines = _run(plant)
    assert len(lines) == 1 and 'release.reviewers expected ["User:maintainer"], live []' in lines[0]


def test_an_environment_rule_of_another_kind_fails() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        at = _page(f"/repos/{REPO}/environments")
        body = copy.deepcopy(answers[at][1])
        assert isinstance(body, dict)
        body["environments"][0]["protection_rules"].append({"type": "custom_protection"})
        answers[at] = (200, body)

    lines = _run(plant)
    assert len(lines) == 1
    assert 'release.other_rules expected null, live ["custom_protection"]' in lines[0]


def test_an_added_environment_fails() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        at = _page(f"/repos/{REPO}/environments")
        body = copy.deepcopy(answers[at][1])
        assert isinstance(body, dict)
        body["environments"].append({"name": "smoke", "protection_rules": []})
        answers[at] = (200, body)

    lines = _run(plant)
    assert len(lines) == 1 and "environments: smoke expected null, live {" in lines[0]


def test_classic_protection_put_back_fails() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[f"/repos/{REPO}/branches/main/protection"] = (
            200,
            {"enforce_admins": {"enabled": True}, "allow_force_pushes": {"enabled": False}},
        )

    lines = _run(plant)
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} classic protection: expected null")


def test_dependabot_alerts_turned_off_fails() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[f"/repos/{REPO}/vulnerability-alerts"] = (404, {"message": "Not Found"})

    lines = _run(plant)
    assert lines == [f"{REPO} dependabot alerts: expected true, live false"]


def test_a_dependency_graph_turned_off_fails() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[f"/repos/{REPO}/dependency-graph/sbom"] = (404, {"message": "Not Found"})

    assert _run(plant) == [f"{REPO} dependency graph: expected true, live false"]


def test_a_dependency_graph_the_api_cannot_answer_fails_as_not_read() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[f"/repos/{REPO}/dependency-graph/sbom"] = (500, {"message": "Server Error"})

    lines = _run(plant)
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} dependency graph: not read")


def test_the_dependency_graph_is_read_with_no_read_token() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[f"/repos/{REPO}/dependency-graph/sbom"] = (404, {"message": "Not Found"})

    lines = _run(plant, admin=False)
    assert f"{REPO} dependency graph: expected true, live false" in lines


def test_a_removed_required_check_fails() -> None:
    live = copy.deepcopy(MAIN_PROTECT)
    live["rules"] = [r for r in live["rules"] if r["type"] != "required_status_checks"]

    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers.update({k: v for k, v in _answers(live).items() if k.endswith("/rulesets/7")})

    lines = _run(plant)
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} ruleset main-protect:")


def test_a_required_check_bound_to_another_integration_fails() -> None:
    live = copy.deepcopy(MAIN_PROTECT)
    rule = next(r for r in live["rules"] if r["type"] == "required_status_checks")
    rule["parameters"]["required_status_checks"][0]["integration_id"] = ACTIONS_APP

    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers.update({k: v for k, v in _answers(live).items() if k.endswith("/rulesets/7")})

    lines = _run(plant)
    assert len(lines) == 1 and f'"integration_id":{ACTIONS_APP}' in lines[0]


def test_an_added_bypass_actor_fails_when_the_answer_shows_the_list() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        at = f"/repos/{REPO}/rulesets/7"
        body = copy.deepcopy(answers[at][1])
        assert isinstance(body, dict)
        body["bypass_actors"] = [
            {"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"}
        ]
        answers[at] = (200, body)

    lines = _run(plant)
    assert len(lines) == 1 and '"actor_type":"RepositoryRole"' in lines[0]


def test_the_same_list_shown_and_empty_passes() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        at = f"/repos/{REPO}/rulesets/7"
        body = copy.deepcopy(answers[at][1])
        assert isinstance(body, dict)
        body["bypass_actors"] = []
        answers[at] = (200, body)

    assert _run(plant) == []


def test_a_ruleset_no_file_names_fails() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[_page(f"/repos/{REPO}/rulesets?includes_parents=false")] = (
            200,
            [{"id": 7}, {"id": 8}],
        )
        answers[f"/repos/{REPO}/rulesets/8"] = (200, dict(MAIN_PROTECT, name="extra", id=8))

    lines = _run(plant)
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} ruleset extra: expected null")


def test_a_deleted_ruleset_fails() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[_page(f"/repos/{REPO}/rulesets?includes_parents=false")] = (200, [])

    lines = _run(plant)
    assert len(lines) == 1 and lines[0].endswith("live null")


def test_a_bypass_used_this_week_fails() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[
            _page(f"/repos/{REPO}/rulesets/rule-suites?time_period=week&rule_suite_result=bypass")
        ] = (
            200,
            [
                {
                    "actor_name": "someone",
                    "ref": "refs/heads/main",
                    "result": "bypass",
                    "pushed_at": "x",
                }
            ],
        )

    lines = _run(plant)
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} bypass use:") and "someone" in lines[0]


def test_an_allowed_bypass_passes_and_no_other() -> None:
    suites = [
        {"actor_name": "controller[bot]", "ref": "refs/tags/v1.2.3", "result": "bypass"},
        {"actor_name": "controller[bot]", "ref": "refs/heads/main", "result": "bypass"},
    ]

    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[
            _page(f"/repos/{REPO}/rulesets/rule-suites?time_period=week&rule_suite_result=bypass")
        ] = (
            200,
            suites,
        )

    expected = dict(
        EXPECTED,
        **{"allowed bypass": [{"actor_name": "controller[bot]", "ref_prefix": "refs/tags/v"}]},
    )
    lines = _run(plant, expected=expected)
    assert len(lines) == 1 and "refs/heads/main" in lines[0]


def test_an_answer_the_api_refuses_fails_as_not_read() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[f"/repos/{REPO}/actions/permissions"] = (
            403,
            {"message": "Resource not accessible"},
        )

    lines = _run(plant)
    # The refused setting, and the Dependabot answer that a refused credential cannot vouch for.
    assert any(x.startswith(f"{REPO} actions: not read (GET ") and "403" in x for x in lines)
    assert all("not read" in x for x in lines)


def test_a_404_from_dependabot_is_not_read_when_the_credential_shows_no_administration() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[f"/repos/{REPO}/actions/permissions"] = (
            403,
            {"message": "Resource not accessible"},
        )
        answers[f"/repos/{REPO}/vulnerability-alerts"] = (404, {"message": "Not Found"})

    lines = _run(plant)
    assert any(x.startswith(f"{REPO} dependabot alerts: not read") for x in lines)


def test_a_protection_404_that_is_not_an_unprotected_branch_fails_as_not_read() -> None:
    def plant(answers: dict[str, tuple[int, object]]) -> None:
        answers[f"/repos/{REPO}/branches/main/protection"] = (404, {"message": "Not Found"})

    lines = _run(plant)
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} classic protection: not read")


def test_with_no_read_token_every_administration_setting_fails_as_not_read() -> None:
    lines = _run(admin=False)
    not_read = sorted(x.split(":")[0] for x in lines)
    want = sorted(
        f"{REPO} {s}"
        for s in sr.SETTINGS
        if s not in ("environments", "dependency graph", "allowed bypass")
    ) + [f"{REPO} bypass use"]
    assert not_read == sorted(want)
    assert all("not read (no SETTINGS_READ_TOKEN" in x for x in lines)


def test_an_expected_file_missing_a_setting_is_refused(tmp_path: Path) -> None:
    entry = {k: v for k, v in EXPECTED.items() if k != "fork approval"}
    path = tmp_path / "expected.json"
    path.write_text(json.dumps({"repositories": {REPO: entry}}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"missing \['fork approval'\]"):
        sr.load_expected(path)


def test_an_expected_file_naming_an_unknown_setting_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "expected.json"
    path.write_text(json.dumps({"repositories": {REPO: dict(EXPECTED, extra=1)}}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"unknown \['extra'\]"):
        sr.load_expected(path)


class FakeGh:
    """``gh`` for the issue: the open issues by page, and every command it was given."""

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.commands.append(args)
        if args[0] == "api":
            return json.dumps(self.issues)
        if args[:2] == ("issue", "create"):
            return f"https://github.com/{REPO}/issues/41\n"
        return ""


BOT_USER = {"login": "github-actions[bot]", "type": "Bot"}


def test_drift_opens_the_issue_once() -> None:
    gh = FakeGh([{"number": 3, "title": sr.ISSUE_TITLE, "user": {"login": "a-person"}}])
    assert sr.publish(REPO, "body", True, gh) == 41
    assert [c[:2] for c in gh.commands] == [
        (
            "api",
            f"repos/{REPO}/issues?state=open&creator=github-actions%5Bbot%5D&per_page=100&page=1",
        ),
        ("issue", "create"),
    ]


def test_drift_edits_the_open_issue_the_workflow_opened() -> None:
    gh = FakeGh([{"number": 9, "title": sr.ISSUE_TITLE, "user": BOT_USER}])
    assert sr.publish(REPO, "body", True, gh) == 9
    assert gh.commands[-1][:3] == ("issue", "edit", "9")


def test_a_clean_run_closes_the_open_issue_and_touches_no_other() -> None:
    gh = FakeGh(
        [
            {"number": 9, "title": sr.ISSUE_TITLE, "user": BOT_USER},
            {"number": 10, "title": sr.ISSUE_TITLE, "user": {"login": "a-person"}},
            {"number": 11, "title": "something else", "user": BOT_USER},
        ]
    )
    assert sr.publish(REPO, "No settings drift.", False, gh) is None
    assert [c[:3] for c in gh.commands[1:]] == [("issue", "close", "9")]


def test_main_exits_1_on_drift_and_0_when_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "expected.json"
    path.write_text(json.dumps({"repositories": {REPO: EXPECTED}}), encoding="utf-8")
    answers = _answers()
    monkeypatch.setattr(sr, "github_fetch", lambda token: FakeAPI(answers))
    monkeypatch.setenv("SETTINGS_READ_TOKEN", "a-read-token")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert sr.main(["--expected", str(path)]) == 0
    assert "No settings drift" in capsys.readouterr().out
    _set("/actions/permissions", "sha_pinning_required", value=False)(answers)
    assert sr.main(["--expected", str(path)]) == 1
    assert "::error title=settings drift::" in capsys.readouterr().out
    monkeypatch.delenv("SETTINGS_READ_TOKEN")
    assert sr.main(["--expected", str(path)]) == 1


def test_main_exits_2_on_an_unreadable_expected_file(tmp_path: Path) -> None:
    path = tmp_path / "expected.json"
    path.write_text("{}", encoding="utf-8")
    assert sr.main(["--expected", str(path)]) == 2


class FakeQuery:
    """Answers the merge-methods query with one fixed answer, and counts what it was asked."""

    def __init__(self, status: int, body: object) -> None:
        self.answer = (status, body)
        self.asked: list[dict[str, object]] = []

    def __call__(self, text: str, variables: Any) -> tuple[int, object]:
        assert "mergeCommitAllowed" in text
        self.asked.append(dict(variables))
        return self.answer


def _hide_merge_methods(answers: dict[str, tuple[int, object]]) -> None:
    """A read-only credential's repository answer: no allow_* fields."""
    status, body = answers[f"/repos/{REPO}"]
    assert isinstance(body, dict)
    answers[f"/repos/{REPO}"] = (
        status,
        {k: v for k, v in body.items() if not k.startswith("allow_")},
    )


def _with_queries(*queries: FakeQuery) -> list[str]:
    answers = _answers()
    _hide_merge_methods(answers)
    api = FakeAPI(answers)
    return [d.line() for d in sr.report({REPO: EXPECTED}, api, api, list(queries))]


MERGE = {"mergeCommitAllowed": False, "squashMergeAllowed": True, "rebaseMergeAllowed": False}


def test_merge_methods_a_read_token_cannot_see_are_read_through_graphql() -> None:
    query = FakeQuery(200, {"data": {"repository": MERGE}})
    assert _with_queries(query) == []
    assert query.asked == [{"owner": "owner", "name": "project"}]


def test_a_merge_method_back_on_is_drift_through_graphql_too() -> None:
    query = FakeQuery(200, {"data": {"repository": dict(MERGE, rebaseMergeAllowed=True)}})
    lines = _with_queries(query)
    assert lines == [f"{REPO} merge methods: allow_rebase_merge expected false, live true"]


def test_the_second_credential_is_asked_when_the_first_is_refused() -> None:
    refused = FakeQuery(200, {"errors": [{"message": "Resource not accessible"}]})
    shown = FakeQuery(200, {"data": {"repository": MERGE}})
    assert _with_queries(refused, shown) == []
    assert len(refused.asked) == len(shown.asked) == 1


def test_merge_methods_no_credential_shows_fail_as_not_read() -> None:
    refused = FakeQuery(200, {"errors": [{"message": "Resource not accessible"}]})
    lines = _with_queries(refused)
    assert len(lines) == 1 and lines[0].startswith(f"{REPO} merge methods: not read")
    assert "Resource not accessible" in lines[0]
    lines = _with_queries()
    assert len(lines) == 1 and "no GraphQL credential" in lines[0]


def test_a_graphql_answer_missing_a_method_fails_as_not_read() -> None:
    query = FakeQuery(200, {"data": {"repository": {"mergeCommitAllowed": False}}})
    lines = _with_queries(query)
    assert len(lines) == 1 and "not read" in lines[0]
