"""``tools/ci/merge_verdict.py``: a posted merge verdict counts only when the merge controller's
App posted it green, as the newest of its runs, for the exact head and tree its receipt names.

Every case is a planted set of API answers (the branch rules, the head's commit and its
``merge-verdict`` check runs) and a planted git repository whose ``main`` holds the controller
revision. One shape is accepted; every other shape is refused, and an answer that cannot be read
is never accepted.
"""

from __future__ import annotations

import copy
import http.client
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tools.ci import merge_verdict as mv
from tools.ci import trust

pytestmark = pytest.mark.security

REPO = "example/project"
SHA = "c" * 40
TREE = "d" * 40
PARTNER = "e" * 40
APP = 4242
OTHER_APP = 777
RULES = f"/repos/{REPO}/rules/branches/main?per_page=100"
COMMIT = f"/repos/{REPO}/commits/{SHA}"

#: GitHub lists a commit's check runs newest first. The verdict leans on neither order, so every
#: case where the order could decide is planted both ways.
ORDERS = [
    pytest.param(True, id="newest first, as GitHub lists them"),
    pytest.param(False, id="oldest first"),
]


def _checks(page: int) -> str:
    return (
        f"/repos/{REPO}/commits/{SHA}/check-runs?check_name=merge-verdict&filter=all"
        f"&per_page=100&page={page}"
    )


# ── A planted repository: main holds the controller, a candidate branch does not ─────────────


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
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "the controller")
    _git(repo, "switch", "-q", "-c", "candidate")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "a candidate's own controller")
    _git(repo, "switch", "-q", "main")
    return repo


def _receipt(repo: Path, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "controller_revision": _git(repo, "rev-parse", "main"),
        "head_sha": SHA,
        "tree_oid": TREE,
        "external_revisions": {"PersonalClaw/PersonalClawApps": PARTNER},
        "toolchain": {"python": "3.13.16", "uv": "0.13.1"},
        "evidence_digests": {"lint": "sha256:" + "b" * 64},
    }
    data.update(overrides)
    return data


def _text(receipt: dict[str, object]) -> str:
    return "Every planned job passed.\n\n" + mv.receipt_block(receipt)


def _run(
    run_id: int,
    text: str | None,
    *,
    app: int = APP,
    slug: str = "merge-controller",
    head: str = SHA,
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": mv.CHECK_NAME,
        "head_sha": head,
        "status": status,
        "conclusion": conclusion,
        "app": {"id": app, "slug": slug},
        "output": {"title": "merge-verdict", "summary": "", "text": text},
    }


def _rule(*checks: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "required_status_checks",
        "parameters": {
            "strict_required_status_checks_policy": True,
            "required_status_checks": list(checks),
        },
    }


#: main's other rules, as a squash-only ruleset carries them beside its status checks.
OTHER_RULES: list[dict[str, Any]] = [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {
        "type": "pull_request",
        "parameters": {"allowed_merge_methods": ["squash"], "required_approving_review_count": 0},
    },
]


def _answers(repo: Path) -> dict[str, Any]:
    """The one accepted shape: the rule names the App, its newest run is green for this head."""
    return {
        RULES: [
            OTHER_RULES[0],
            _rule({"context": "lint"}, {"context": mv.CHECK_NAME, "integration_id": APP}),
            *OTHER_RULES[1:],
        ],
        COMMIT: {"sha": SHA, "commit": {"tree": {"sha": TREE}}},
        _checks(1): {"total_count": 1, "check_runs": [_run(10, _text(_receipt(repo)))]},
    }


def _getter(answers: dict[str, Any], reads: list[str] | None = None) -> Callable[[str], Any]:
    def get(path: str) -> Any:
        if reads is not None:
            reads.append(path)
        if path not in answers:
            raise OSError(f"HTTP 404 for {path}")
        value = answers[path]
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)

    return get


def _verify(repo: Path, answers: dict[str, Any], **kwargs: Any) -> mv.Result:
    return mv.verify(_getter(answers), repo=REPO, sha=SHA, git_dir=repo, main="main", **kwargs)


def _listed(runs: list[dict[str, Any]], newest_first: bool) -> list[dict[str, Any]]:
    return sorted(runs, key=lambda run: int(run["id"]), reverse=newest_first)


def _set_runs(answers: dict[str, Any], *runs: dict[str, Any], newest_first: bool = True) -> None:
    answers[_checks(1)] = {
        "total_count": len(runs),
        "check_runs": _listed(list(runs), newest_first),
    }


def _green(answers: dict[str, Any], run_id: int) -> dict[str, Any]:
    """A copy of the planted green run under another id."""
    run = copy.deepcopy(answers[_checks(1)]["check_runs"][0])
    run["id"] = run_id
    return run


# ── The positive controls ─────────────────────────────────────────────────────────────────────


def test_the_controllers_newest_green_for_this_head_and_tree_is_accepted(planted: Path) -> None:
    result = _verify(planted, _answers(planted))
    assert isinstance(result, mv.Accepted), result
    assert result.check_run_id == 10
    assert result.receipt.tree_oid == TREE
    assert result.receipt.head_sha == SHA


@pytest.mark.parametrize("newest_first", ORDERS)
def test_a_newer_green_over_an_older_red_is_accepted(planted: Path, newest_first: bool) -> None:
    answers = _answers(planted)
    text = _text(_receipt(planted))
    _set_runs(
        answers, _run(10, text, conclusion="failure"), _run(11, text), newest_first=newest_first
    )
    result = _verify(planted, answers)
    assert isinstance(result, mv.Accepted) and result.check_run_id == 11, result


@pytest.mark.parametrize("newest_first", ORDERS)
def test_every_page_is_read(planted: Path, newest_first: bool) -> None:
    """150 runs over two pages, the newest green and the rest red: it decides wherever it is."""
    answers = _answers(planted)
    text = _text(_receipt(planted))
    runs = [_run(n, text, conclusion="failure") for n in range(1, 150)] + [_run(150, text)]
    listed = _listed(runs, newest_first)
    answers[_checks(1)] = {"total_count": 150, "check_runs": listed[:100]}
    answers[_checks(2)] = {"total_count": 150, "check_runs": listed[100:]}
    result = _verify(planted, answers)
    assert isinstance(result, mv.Accepted) and result.check_run_id == 150, result


def test_an_expected_partner_that_matches_is_accepted(planted: Path) -> None:
    result = _verify(
        planted,
        _answers(planted),
        expected_external={"PersonalClaw/PersonalClawApps": PARTNER},
    )
    assert isinstance(result, mv.Accepted), result


# ── Who posted it ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("app", "slug"),
    [
        pytest.param(mv.GITHUB_ACTIONS_APP_ID, "github-actions", id="a job named merge-verdict"),
        pytest.param(OTHER_APP, "someone-elses-app", id="another App"),
    ],
)
def test_a_green_the_controller_did_not_post_is_refused(planted: Path, app: int, slug: str) -> None:
    answers = _answers(planted)
    _set_runs(answers, _run(10, _text(_receipt(planted)), app=app, slug=slug))
    result = _verify(planted, answers)
    assert isinstance(result, mv.Refused), result
    assert slug in result.reason


@pytest.mark.parametrize("newest_first", ORDERS)
def test_a_newer_run_by_another_app_does_not_hide_the_controllers(
    planted: Path, newest_first: bool
) -> None:
    """Only the controller speaks for the controller: a later Actions run changes nothing."""
    answers = _answers(planted)
    text = _text(_receipt(planted))
    _set_runs(
        answers,
        _run(10, text),
        _run(11, text, app=mv.GITHUB_ACTIONS_APP_ID, conclusion="failure"),
        newest_first=newest_first,
    )
    result = _verify(planted, answers)
    assert isinstance(result, mv.Accepted) and result.check_run_id == 10, result


def test_a_green_the_app_posted_under_another_name_is_not_counted(planted: Path) -> None:
    """The API's ``check_name`` filter is not taken on trust: each run's own name is read."""
    answers = _answers(planted)
    run = _run(10, _text(_receipt(planted)))
    run["name"] = "merge-verdict-preview"
    _set_runs(answers, run)
    result = _verify(planted, answers)
    assert isinstance(result, mv.Refused), result
    assert f"no {mv.CHECK_NAME} check run by App {APP}" in result.reason


@pytest.mark.parametrize(
    ("rules", "match"),
    [
        pytest.param([], "no required merge-verdict check", id="no rule on main"),
        pytest.param(
            [_rule({"context": "lint", "integration_id": APP})],
            "no required merge-verdict check",
            id="a rule for another check only",
        ),
        pytest.param(
            [_rule({"context": mv.CHECK_NAME, "integration_id": mv.GITHUB_ACTIONS_APP_ID})],
            "GitHub Actions",
            id="the rule names GitHub Actions",
        ),
        pytest.param(
            [_rule({"context": mv.CHECK_NAME})], "names no App", id="the rule names no App"
        ),
        pytest.param(
            [
                _rule({"context": mv.CHECK_NAME, "integration_id": APP}),
                _rule({"context": mv.CHECK_NAME, "integration_id": OTHER_APP}),
            ],
            "more than one App",
            id="two rules name two Apps",
        ),
        pytest.param(
            [_rule({"context": mv.CHECK_NAME, "integration_id": "4242"})],
            "names no App",
            id="an id that is not a number",
        ),
        pytest.param(
            [_rule({"context": mv.CHECK_NAME, "integration_id": True})],
            "names no App",
            id="an id that is true",
        ),
    ],
)
def test_the_app_comes_from_mains_rule_or_everything_is_refused(
    planted: Path, rules: list[Any], match: str
) -> None:
    answers = _answers(planted)
    answers[RULES] = rules
    result = _verify(planted, answers)
    assert isinstance(result, mv.Refused), result
    assert match in result.reason


# ── What it concluded ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [
        ("completed", "failure"),
        ("completed", "cancelled"),
        ("completed", "skipped"),
        ("completed", "neutral"),
        ("completed", "timed_out"),
        ("completed", "action_required"),
        ("completed", "stale"),
        ("completed", None),
        ("in_progress", None),
        ("queued", None),
        # A success the API reports on a run it has not completed is not one.
        ("in_progress", "success"),
        ("queued", "success"),
    ],
)
def test_anything_but_a_completed_success_is_refused(
    planted: Path, status: str, conclusion: str | None
) -> None:
    answers = _answers(planted)
    _set_runs(answers, _run(10, _text(_receipt(planted)), status=status, conclusion=conclusion))
    result = _verify(planted, answers)
    assert isinstance(result, mv.Refused), result
    assert "not completed/success" in result.reason


@pytest.mark.parametrize("newest_first", ORDERS)
@pytest.mark.parametrize(
    ("status", "conclusion"),
    [("in_progress", None), ("completed", "failure")],
    ids=["a newer pending run", "a newer red run"],
)
def test_an_older_green_under_a_newer_controller_run_is_refused(
    planted: Path, status: str, conclusion: str | None, newest_first: bool
) -> None:
    answers = _answers(planted)
    text = _text(_receipt(planted))
    _set_runs(
        answers,
        _run(10, text),
        _run(11, text, status=status, conclusion=conclusion),
        newest_first=newest_first,
    )
    result = _verify(planted, answers)
    assert isinstance(result, mv.Refused), result
    assert f"run on {SHA}, 11, is" in result.reason


# ── Whose head and tree ───────────────────────────────────────────────────────────────────────


def test_a_receipt_for_another_head_is_refused(planted: Path) -> None:
    answers = _answers(planted)
    _set_runs(answers, _run(10, _text(_receipt(planted, head_sha="f" * 40))))
    result = _verify(planted, answers)
    assert isinstance(result, mv.Refused) and "head" in result.reason, result


def test_a_receipt_for_another_tree_is_refused(planted: Path) -> None:
    answers = _answers(planted)
    _set_runs(answers, _run(10, _text(_receipt(planted, tree_oid="f" * 40))))
    result = _verify(planted, answers)
    assert isinstance(result, mv.Refused) and "tree" in result.reason, result


def test_a_check_run_on_another_head_is_not_counted(planted: Path) -> None:
    answers = _answers(planted)
    _set_runs(answers, _run(10, _text(_receipt(planted)), head="f" * 40))
    result = _verify(planted, answers)
    assert isinstance(result, mv.Refused), result


@pytest.mark.parametrize(
    "expected",
    [
        {"PersonalClaw/PersonalClawApps": "f" * 40},
        {"PersonalClaw/Other": PARTNER},
        {"PersonalClaw/PersonalClawApps": PARTNER, "PersonalClaw/Other": PARTNER},
    ],
    ids=["another partner head", "another partner", "a partner the receipt lacks"],
)
def test_a_partner_other_than_the_expected_one_is_refused(
    planted: Path, expected: dict[str, str]
) -> None:
    result = _verify(planted, _answers(planted), expected_external=expected)
    assert isinstance(result, mv.Refused) and "external" in result.reason, result


# ── The receipt itself ────────────────────────────────────────────────────────────────────────


def _receipt_text_cases(repo: Path) -> dict[str, str | None]:
    block = mv.receipt_block(_receipt(repo))
    good = json.dumps(_receipt(repo))
    no_toolchain = {k: v for k, v in _receipt(repo).items() if k != "toolchain"}
    return {
        "no output text": None,
        "no receipt": "Every planned job passed.",
        "two receipts": block + "\n" + block,
        "malformed JSON": f"```{mv.RECEIPT_FENCE}\n{{not json\n```\n",
        "not an object": f"```{mv.RECEIPT_FENCE}\n[1, 2]\n```\n",
        "an unclosed block": f"```{mv.RECEIPT_FENCE}\n{good}\n",
        "a missing field": mv.receipt_block(no_toolchain, check=False),
        # The same value twice: refused for the repeat itself, not for a tree that differs.
        "a key given twice": f"```{mv.RECEIPT_FENCE}\n"
        + good[:-1]
        + f', "tree_oid": "{TREE}"}}\n```\n',
    }


@pytest.mark.parametrize(
    ("case", "match"),
    [
        ("no output text", "has no output text"),
        ("no receipt", "holds no receipt"),
        ("two receipts", "holds 2 receipts"),
        ("malformed JSON", "is not JSON"),
        ("not an object", "is not a JSON object"),
        ("an unclosed block", "is never closed"),
        ("a missing field", "lacks toolchain"),
        ("a key given twice", "gives tree_oid twice"),
    ],
)
def test_a_receipt_that_cannot_be_read_is_refused(planted: Path, case: str, match: str) -> None:
    """Each shape is refused for its own reason, not by a neighbouring rule."""
    answers = _answers(planted)
    _set_runs(answers, _run(10, _receipt_text_cases(planted)[case]))
    result = _verify(planted, answers)
    assert isinstance(result, mv.Refused), result
    assert match in result.reason


def test_a_controller_revision_that_is_not_on_main_is_refused(planted: Path) -> None:
    own = _git(planted, "rev-parse", "candidate")
    answers = _answers(planted)
    _set_runs(answers, _run(10, _text(_receipt(planted, controller_revision=own))))
    result = _verify(planted, answers)
    assert isinstance(result, mv.Refused) and "not on main" in result.reason, result


def test_a_controller_revision_that_is_no_commit_is_refused(planted: Path) -> None:
    answers = _answers(planted)
    _set_runs(answers, _run(10, _text(_receipt(planted, controller_revision="a" * 40))))
    result = _verify(planted, answers)
    assert isinstance(result, mv.Refused) and "not a commit" in result.reason, result


def test_the_receipt_round_trips(planted: Path) -> None:
    receipt = _receipt(planted)
    output = mv.render_output(receipt, title="merge-verdict: green", summary="All jobs passed.")
    assert output["title"] == "merge-verdict: green"
    assert output["summary"] == "All jobs passed."
    assert mv.read_receipt({"output": output}) == trust.receipt_from(receipt)


def test_the_record_holds_the_checked_receipt_alone_as_canonical_json(planted: Path) -> None:
    """Sorted keys, no spaces, ASCII only, and only the receipt's fields: a key the caller adds
    is not written, so the record says nothing the verifier does not read."""
    data = _receipt(planted, toolchain={"python": "3.13.16", "locale": "fr_FR: français"})
    data["verdict"] = "red"
    text = mv.render_output(data, title="t", summary="s")["text"]
    fields = {name: data[name] for name in trust.RECEIPT_FIELDS}
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert text == f"```{mv.RECEIPT_FENCE}\n{canonical}\n```\n"
    assert text.isascii()


def test_an_incomplete_receipt_is_never_rendered(planted: Path) -> None:
    data = _receipt(planted)
    del data["evidence_digests"]
    with pytest.raises(trust.Refused, match="evidence_digests"):
        mv.render_output(data, title="t", summary="s")


def test_the_receipts_format_is_checked_without_git(planted: Path) -> None:
    """trust's format check, split out of check_receipt, reads no repository."""
    receipt = trust.receipt_from(_receipt(planted, controller_revision="a" * 40))
    assert receipt.controller_revision == "a" * 40


@pytest.mark.parametrize("field", ["controller_revision", "head_sha", "tree_oid"])
@pytest.mark.parametrize(
    "value",
    [pytest.param("main", id="a name"), pytest.param("a" * 40 + "\n", id="a trailing newline")],
)
def test_an_id_in_the_receipt_is_a_full_object_id_and_nothing_more(
    planted: Path, field: str, value: str
) -> None:
    with pytest.raises(trust.Refused, match=f"{field} must be a full object id"):
        trust.receipt_from(_receipt(planted, **{field: value}))


# ── What cannot be read is never accepted ─────────────────────────────────────────────────────


Plant = Callable[[dict[str, Any]], None]


def _rules_beside_the_good_one(*extra: object) -> Plant:
    def plant(answers: dict[str, Any]) -> None:
        answers[RULES] = [*answers[RULES], *extra]

    return plant


def _two_pages(first_count: int, second_count: int) -> Plant:
    """150 green runs, newest first, over two pages that give these counts. A count that grows
    means runs arrived after page one was read: listed newest first, they were never seen."""

    def plant(answers: dict[str, Any]) -> None:
        runs = [_green(answers, n) for n in range(150, 0, -1)]
        answers[_checks(1)] = {"total_count": first_count, "check_runs": runs[:100]}
        answers[_checks(2)] = {"total_count": second_count, "check_runs": runs[100:]}

    return plant


def _second_page_fails(answers: dict[str, Any]) -> None:
    runs = [_green(answers, n) for n in range(150, 50, -1)]
    answers[_checks(1)] = {"total_count": 150, "check_runs": runs}
    answers[_checks(2)] = OSError("HTTP 502")


def _cut_short(answers: dict[str, Any]) -> None:
    answers[_checks(1)]["total_count"] = 2
    answers[_checks(2)] = {"total_count": 2, "check_runs": []}


def _more_than_counted(answers: dict[str, Any]) -> None:
    answers[_checks(1)]["check_runs"] = [_green(answers, 11), _green(answers, 10)]


def _beside_the_green(stray: Callable[[dict[str, Any]], object]) -> Plant:
    """A second run, listed first, beside the controller's green: the count says two."""

    def plant(answers: dict[str, Any]) -> None:
        answers[_checks(1)] = {
            "total_count": 2,
            "check_runs": [stray(answers), _green(answers, 10)],
        }

    return plant


def _by_another_app(answers: dict[str, Any], **changes: object) -> dict[str, Any]:
    run = _green(answers, 11)
    run["app"] = {"id": OTHER_APP, "slug": "someone-elses-app"}
    run.update(changes)
    return run


def _no_id(answers: dict[str, Any]) -> dict[str, Any]:
    run = _by_another_app(answers)
    del run["id"]
    return run


@pytest.mark.parametrize(
    ("plant", "match"),
    [
        pytest.param(
            lambda a: a.__setitem__(_checks(1), OSError("HTTP 502")), "HTTP 502", id="checks fail"
        ),
        pytest.param(
            lambda a: a.__setitem__(RULES, OSError("HTTP 403")), "HTTP 403", id="rules fail"
        ),
        pytest.param(
            lambda a: a.__setitem__(COMMIT, OSError("HTTP 502")), "HTTP 502", id="commit fails"
        ),
        pytest.param(
            lambda a: a.__setitem__(COMMIT, {"sha": SHA}), "names no tree", id="commit has no tree"
        ),
        pytest.param(
            lambda a: a.__setitem__(COMMIT, {"sha": SHA, "commit": {"tree": {"sha": "main"}}}),
            "names no tree",
            id="a tree that is no object id",
        ),
        pytest.param(
            lambda a: a.__setitem__(COMMIT, {"sha": SHA, "commit": {"tree": {"sha": TREE + "\n"}}}),
            "names no tree",
            id="a tree id with a trailing newline",
        ),
        pytest.param(
            lambda a: a.__setitem__(RULES, {"rules": []}), "are not a list", id="rules not a list"
        ),
        pytest.param(lambda a: a.__setitem__(RULES, None), "are not a list", id="rules are null"),
        pytest.param(
            lambda a: a.__setitem__(RULES, [_rule({"context": "x"})] * 100),
            "fill a page",
            id="rules fill a page",
        ),
        pytest.param(
            _rules_beside_the_good_one("required_status_checks"),
            "is not an object",
            id="a rule that is not an object",
        ),
        pytest.param(
            _rules_beside_the_good_one({"type": "required_status_checks", "parameters": {}}),
            "lists no checks",
            id="a status-check rule with no list",
        ),
        pytest.param(
            lambda a: a.__setitem__(_checks(1), []), "are not an object", id="runs not an object"
        ),
        pytest.param(
            lambda a: a[_checks(1)].__setitem__("check_runs", {}),
            "no count or no list",
            id="runs not a list",
        ),
        pytest.param(
            lambda a: a[_checks(1)].pop("total_count"), "no count or no list", id="runs not counted"
        ),
        pytest.param(_second_page_fails, "HTTP 502", id="the second page fails"),
        pytest.param(
            _two_pages(150, 200), "count changed between pages", id="a count that grows by 50"
        ),
        pytest.param(
            _two_pages(150, 151), "count changed between pages", id="a count that grows by one"
        ),
        pytest.param(
            _cut_short, "2 check runs were counted but 1 came back", id="a list cut short"
        ),
        pytest.param(
            _more_than_counted, "more check runs came back than the count", id="more than counted"
        ),
        pytest.param(
            _beside_the_green(lambda a: "merge-verdict"),
            "not an object with an id",
            id="a run that is not an object",
        ),
        pytest.param(_beside_the_green(_no_id), "not an object with an id", id="a run with no id"),
        pytest.param(
            _beside_the_green(lambda a: _by_another_app(a, id=True)),
            "not an object with an id",
            id="a run whose id is true",
        ),
        pytest.param(
            lambda a: a.__setitem__(_checks(1), ValueError("not JSON")), "not JSON", id="not JSON"
        ),
        pytest.param(
            lambda a: a.__setitem__(_checks(1), http.client.IncompleteRead(b"{", 200)),
            "1 bytes read, 200 more expected",
            id="a body cut short",
        ),
        pytest.param(
            lambda a: a.__setitem__(RULES, http.client.BadStatusLine("")),
            f"GET {RULES}",
            id="a status line the client cannot read",
        ),
    ],
)
def test_an_answer_that_cannot_be_read_is_unreadable(
    planted: Path, plant: Plant, match: str
) -> None:
    """Each shape is unreadable for its own reason, not by a neighbouring rule or a crash."""
    answers = _answers(planted)
    plant(answers)
    result = _verify(planted, answers)
    assert isinstance(result, mv.Unreadable), result
    assert match in result.reason


# ── The command ───────────────────────────────────────────────────────────────────────────────


def _cli(
    planted: Path,
    answers: dict[str, Any],
    *extra: str,
    sha: str = SHA,
    repo: str = REPO,
    reads: list[str] | None = None,
) -> int:
    argv = [sha, "--repo", repo, "--git-dir", str(planted), "--main", "main", *extra]
    return mv.main(argv, get=_getter(answers, reads))


def test_the_command_exits_0_when_accepted_and_prints_the_receipt(
    planted: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _cli(planted, _answers(planted)) == 0
    out = capsys.readouterr().out
    assert json.loads(out.splitlines()[-1])["tree_oid"] == TREE


def test_the_command_exits_1_when_refused(planted: Path) -> None:
    answers = _answers(planted)
    answers[RULES] = []
    assert _cli(planted, answers) == 1


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(OSError("HTTP 502"), id="a failed read"),
        pytest.param(http.client.IncompleteRead(b"{", 200), id="a body cut short"),
    ],
)
def test_the_command_exits_2_when_unreadable(
    planted: Path, failure: Exception, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = _answers(planted)
    answers[_checks(1)] = failure
    assert _cli(planted, answers) == 2
    assert "could not read:" in capsys.readouterr().err


def test_the_command_reads_the_expected_partner(planted: Path) -> None:
    assert _cli(planted, _answers(planted), "--external", f"PersonalClaw/Other={PARTNER}") == 1
    assert (
        _cli(planted, _answers(planted), "--external", f"PersonalClaw/PersonalClawApps={PARTNER}")
        == 0
    )


@pytest.mark.parametrize(
    "bad",
    [
        "no-equals",
        "=" + PARTNER,
        "PersonalClaw/Other=main",
        "PersonalClaw/PersonalClawApps=" + PARTNER[:12],
        "PersonalClaw/PersonalClawApps=" + PARTNER + "\n",
    ],
)
def test_a_malformed_expected_partner_is_refused(
    planted: Path, bad: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _cli(planted, _answers(planted), "--external", bad) == 1
    assert "is not NAME=<full commit id>" in capsys.readouterr().err


def test_a_partner_expected_twice_is_refused(
    planted: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two values for one partner is a caller that does not know which it wants, even when the
    last one matches."""
    name = "PersonalClaw/PersonalClawApps"
    first, last = f"{name}={'f' * 40}", f"{name}={PARTNER}"
    assert _cli(planted, _answers(planted), "--external", first, "--external", last) == 1
    assert f"--external names {name} twice" in capsys.readouterr().err


@pytest.mark.parametrize(
    "sha",
    [
        pytest.param("main", id="a name"),
        pytest.param(SHA[:39], id="one short"),
        pytest.param(SHA.upper(), id="upper case"),
        pytest.param(SHA + "\n", id="a trailing newline"),
    ],
)
def test_a_sha_that_is_not_a_full_commit_id_is_refused_before_any_read(
    planted: Path, sha: str, capsys: pytest.CaptureFixture[str]
) -> None:
    reads: list[str] = []
    assert _cli(planted, _answers(planted), sha=sha, reads=reads) == 1
    assert "is not a full commit id" in capsys.readouterr().err
    assert reads == []


@pytest.mark.parametrize(
    "repo",
    [
        pytest.param("noslash", id="no slash"),
        pytest.param("example/project/rules", id="a path"),
        pytest.param("example/pro ject", id="a space"),
        pytest.param(REPO + "\n", id="a trailing newline"),
    ],
)
def test_a_repo_that_is_not_owner_name_is_refused_before_any_read(
    planted: Path, repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The repository goes into every API path, so nothing is read until it is OWNER/NAME."""
    reads: list[str] = []
    assert _cli(planted, _answers(planted), repo=repo, reads=reads) == 1
    assert "is not OWNER/NAME" in capsys.readouterr().err
    assert reads == []


class _Response:
    """What ``urllib.request.urlopen`` hands back, with a body that may be cut short."""

    def __init__(self, body: bytes, *, expected: int = 0) -> None:
        self.body, self.expected = body, expected

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        if self.expected > len(self.body):
            raise http.client.IncompleteRead(self.body, self.expected - len(self.body))
        return self.body


def test_the_api_is_read_with_the_token_only_when_one_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[Any] = []

    def urlopen(request: Any, timeout: float) -> _Response:
        sent.append(request)
        return _Response(b'{"total_count": 0}')

    monkeypatch.setattr(mv.urllib.request, "urlopen", urlopen)
    api = "https://api.example.invalid/"
    assert mv.github_get(api, "a-token")(f"/repos/{REPO}") == {"total_count": 0}
    assert mv.github_get(api, "")(f"/repos/{REPO}") == {"total_count": 0}
    with_token, without = sent
    assert with_token.full_url == f"https://api.example.invalid/repos/{REPO}"
    assert with_token.get_header("Authorization") == "Bearer a-token"
    assert without.get_header("Authorization") is None


def test_a_body_the_api_cuts_short_is_unreadable(
    planted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated response is an answer that could not be read, not a crash."""
    monkeypatch.setattr(
        mv.urllib.request, "urlopen", lambda request, timeout: _Response(b"[", expected=200)
    )
    get = mv.github_get("https://api.example.invalid", "")
    result = mv.verify(get, repo=REPO, sha=SHA, git_dir=planted, main="main")
    assert isinstance(result, mv.Unreadable), result
    assert "IncompleteRead" in result.reason


@pytest.mark.parametrize(
    "argv",
    [["shards", "--help"], ["verify-check-run", "--help"]],
    ids=["shards", "verify-check-run"],
)
def test_the_package_runs_on_the_standard_library_alone(argv: list[str]) -> None:
    """``python -S``: no site-packages, as the workflows run ``python3 -m tools.ci``."""
    done = subprocess.run(
        [sys.executable, "-S", "-m", "tools.ci", *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert done.returncode == 0, done.stderr


def test_the_verb_runs_the_verifier_on_the_standard_library_alone() -> None:
    """The verb reaches the verifier (and trust) with no site-packages: a short sha is refused
    before any API read, so nothing leaves the process."""
    done = subprocess.run(
        [sys.executable, "-S", "-m", "tools.ci", "verify-check-run", "abc123", "--repo", REPO],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.environ.get("PATH", ""), "GH_TOKEN": ""},
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert "is not a full commit id" in done.stderr


def test_the_other_commands_never_load_the_verifier() -> None:
    """The dispatcher, and every command it imports, loads neither the verifier nor trust: they
    load only for their own verb, so the commands every CI run starts depend on neither."""
    probe = (
        "import sys, tools.ci.__main__; "
        "print(sorted(n for n in sys.modules if n in ('tools.ci.trust', 'tools.ci.merge_verdict')))"
    )
    done = subprocess.run(
        [sys.executable, "-S", "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "[]"
