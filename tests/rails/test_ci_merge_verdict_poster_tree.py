"""``.github/workflows/merge-verdict.yml``: the merge verdict's poster keeps its trust shape.

It runs only as a ``workflow_run`` listener of Merge and Dependency review and a listener of
completed check runs, so GitHub runs main's copy. It names nothing of the candidate but in its
jobs' one condition, which reads the triggering run's event and branch only to spare the queue
the runs that can judge no pull request's head (each case below a planted event), checks out
only the default branch and keeps no credential. ``post`` reads with its own token
(``contents`` and ``actions``, read) and mints the controller App's token, with ``checks: write``
alone, only when there is a verdict to post, and only the step that posts holds it. ``contract``
runs after it only when it names a head whose contract runs or a branch to delete, mints the App's
token with ``contents: write`` alone for the step that points the head's contract branch at
main's tip, and dispatches with its own token, which holds ``actions: write`` and nothing else
that writes. ``contract.yml``, which that dispatch runs, holds no secret and no write token, keeps
no credential, runs only on the head's own contract branch and names the head only through a
step's environment (#4584 step 4). Each rule fails on a planted edit.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml
from rails_repo import REPO

from tools.ci import workflows

_WORKFLOW = REPO / ".github" / "workflows" / "merge-verdict.yml"
_APP_ACTION = "actions/create-github-app-token@"
_POSTING = "steps.decide.outputs.post == 'true'"
_APP_KEYS = {
    "app-id": "${{ vars.CONTROLLER_APP_ID }}",
    "private-key": "${{ secrets.CONTROLLER_APP_KEY }}",
}
_TOKEN_ASKS = {**_APP_KEYS, "permission-checks": "write"}
_CONTRACT_ASKS = {**_APP_KEYS, "permission-contents": "write"}
#: The runs that can judge a pull request's head, and so take a runner: the sweep; a completed
#: dispatch off main (a contract run, a dispatch proof), whatever MERGE_VERDICT_LIVE says; and, once
#: the flip sets it 'true', code scanning's CodeQL check and a completed run of a pull request
#: (its own, its dependency review, or CodeQL's on refs/pull/*). decide checks each again.
_WAKES = (
    "github.event_name == 'schedule'"
    " || (github.event_name == 'workflow_run'"
    " && github.event.workflow_run.event == 'workflow_dispatch'"
    " && github.event.workflow_run.head_branch != 'main')"
    " || (vars.MERGE_VERDICT_LIVE == 'true'"
    " && ((github.event_name == 'check_run'"
    " && github.event.check_run.app.id == 57789 && github.event.check_run.name == 'CodeQL')"
    " || (github.event_name == 'workflow_run'"
    " && (github.event.workflow_run.event == 'pull_request'"
    " || github.event.workflow_run.event == 'pull_request_target'"
    " || (github.event.workflow_run.event == 'dynamic'"
    " && startsWith(github.event.workflow_run.head_branch, 'refs/pull/'))))))"
)
_CONTRACT_IF = (
    f"({_WAKES}) && (needs.post.outputs.dispatch != '' || needs.post.outputs.cleanup != '')"
)
#: Each job's condition as the file spells it: a folded block, read out before the candidate check.
_IF_BLOCK = re.compile(r"^    if: >-\n(?:      .*\n)+", re.M)
_APP_TOKEN = "${{ steps.controller.outputs.token }}"


def _steps(job: dict) -> list[dict]:
    return [s for s in job.get("steps") or [] if isinstance(s, dict)]


def _checkout_problems(where: str, steps: list[dict]) -> list[str]:
    found = []
    for step in steps:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            given = step.get("with") or {}
            if given.get("persist-credentials") is not False:
                found.append(f"{where}'s checkout keeps its credentials")
            if {"ref", "repository"} & set(given):
                found.append(f"{where}'s checkout names a ref or a repository: only main's")
    return found


def _contract_problems(job: dict) -> list[str]:
    found: list[str] = []
    if job.get("needs") != "post" or job.get("if") != _CONTRACT_IF:
        found.append("the contract job runs other than after post, on a head or a branch it names")
    if job.get("environment") != "ci-controller":
        found.append(f"the contract job deploys to {job.get('environment')!r}")
    if job.get("permissions") != {"contents": "read", "actions": "write"}:
        found.append(f"the contract job holds {job.get('permissions')!r}")
    steps = _steps(job)
    found += _checkout_problems("the contract job", steps)
    tokens = [s for s in steps if str(s.get("uses", "")).startswith(_APP_ACTION)]
    if len(tokens) != 1 or tokens[0].get("with") != _CONTRACT_ASKS:
        found.append(
            f"the contract job's App token is asked for as {[t.get('with') for t in tokens]}"
        )
    if [s for s in steps if "secrets." in str(s)] != tokens:
        found.append("a step of the contract job other than its token step reads a secret")
    holders = [s for s in steps if _APP_TOKEN in str(s)]
    if len(holders) != 1 or "merge-controller contract-branch" not in str(holders[0].get("run")):
        found.append("the App's token reaches a step other than the one that points the branch")
    sends = [s for s in steps if "merge-controller dispatch" in str(s.get("run"))]
    if len(sends) != 1 or (sends[0].get("env") or {}).get("GH_TOKEN") != "${{ github.token }}":
        found.append("the dispatch is not one step on the job's own token")
    return found


def _problems(text: str) -> list[str]:
    found: list[str] = []
    if "workflow_run.head" in _IF_BLOCK.sub("", text):
        found.append("it names the candidate's head, branch or repository")
    data = yaml.safe_load(text)
    on = data.get("on", data.get(True))
    want = {
        "workflow_run": {
            "workflows": [
                workflows.NAMES[workflows.MERGE],
                "Dependency review",
                "CodeQL",
                "Contract",
            ],
            "types": ["completed"],
        },
        "check_run": {"types": ["completed"]},
        "schedule": [{"cron": "*/10 * * * *"}],
    }
    if on != want:
        found.append(f"runs on {on!r}, not only a completed Merge run")
    jobs = data.get("jobs") or {}
    if set(jobs) != {"post", "contract"}:
        return [*found, f"has jobs {sorted(jobs)}"]
    found += _contract_problems(jobs["contract"])
    job = jobs["post"]
    if job.get("if") != _WAKES:
        found.append(
            "the post job's condition is not exactly: the runs that can judge a pull request's"
            f" head ({job.get('if')!r})"
        )
    found += _wake_problems("the post job", str(job.get("if")))
    if job.get("environment") != "ci-controller":
        found.append(f"the job deploys to {job.get('environment')!r}")
    if job.get("permissions") != {
        "contents": "read",
        "actions": "read",
        "security-events": "read",
    }:
        found.append(f"the job holds {job.get('permissions')!r}")
    steps = _steps(job)
    found += _checkout_problems("the post job", steps)
    tokens = [s for s in steps if str(s.get("uses", "")).startswith(_APP_ACTION)]
    if len(tokens) != 1 or tokens[0].get("with") != _TOKEN_ASKS:
        found.append(f"the controller's token is asked for as {[t.get('with') for t in tokens]}")
    elif tokens[0].get("if") != _POSTING:
        found.append("the controller's token is minted with no verdict to post")
    if [s for s in steps if "secrets." in str(s)] != tokens:
        found.append("a step other than the token step reads a secret")
    holders = [s for s in steps if _APP_TOKEN in str(s)]
    if len(holders) != 1 or holders[0].get("if") != _POSTING:
        found.append("the App's token reaches a step other than the one that posts")
    elif "merge-controller post" not in str(holders[0].get("run")):
        found.append("the step holding the App's token does not post the verdict")
    decide = next((s for s in steps if s.get("id") == "decide"), None)
    if decide is None or "merge-controller decide" not in str(decide.get("run")):
        found.append("no step decides the verdict")
    elif (decide.get("env") or {}).get("GH_TOKEN") != "${{ github.token }}":
        found.append("the decision reads with a token other than the job's own")
    return found


def test_the_poster_keeps_its_trust_shape() -> None:
    assert _problems(_WORKFLOW.read_text(encoding="utf-8")) == []


_POST_CHECKOUT = (
    "      # No `ref:`: under `workflow_run` GitHub checks out the default branch.\n"
    "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    "        with:\n"
    "          persist-credentials: false\n"
)
_CONTRACT_CHECKOUT = (
    "      # Main's tools/ci again, from the default branch, with no credential kept.\n"
    "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    "        with:\n"
    "          persist-credentials: false\n"
)
_DECIDE_TOKEN = (
    "          GH_TOKEN: ${{ github.token }}\n"
    "          RUN_ID: ${{ github.event.workflow_run.id }}\n"
)
_DISPATCH_TOKEN = (
    "          GH_TOKEN: ${{ github.token }}\n          BRANCH: ${{ steps.tree.outputs.branch }}\n"
)


#: The poster's wake: which workflows' completions it judges.
_WAKE = (
    '    workflows: ["Merge", "Dependency review", "CodeQL", "Contract"]\n    types: [completed]\n'
)


@pytest.mark.parametrize(
    "old, new, says",
    [
        (
            _WAKE,
            _WAKE + "  pull_request:\n",
            "runs on",
        ),
        (
            _WAKE,
            _WAKE + "  workflow_dispatch: {}\n",
            "runs on",
        ),
        (
            _WAKE,
            _WAKE + "  push:\n",
            "runs on",
        ),
        (
            "  check_run:\n    types: [completed]\n",
            "  check_run:\n    types: [completed, created]\n",
            "runs on",
        ),
        (
            'workflows: ["Merge", "Dependency review", "CodeQL", "Contract"]',
            'workflows: ["Merge", "PR"]',
            "runs on",
        ),
        (
            'workflows: ["Merge", "Dependency review", "CodeQL", "Contract"]',
            'workflows: ["Merge", "Dependency review", "CodeQL"]',
            "runs on",
        ),
        (
            "          RUN_ID: ${{ github.event.workflow_run.id }}\n",
            "          RUN_ID: ${{ github.event.workflow_run.id }}\n"
            "          HEAD: ${{ github.event.workflow_run.head_sha }}\n",
            "names the candidate's head",
        ),
        (
            "    timeout-minutes: 10\n    environment: ci-controller\n    permissions:\n"
            "      contents: read          # main's tip",
            "    timeout-minutes: 10\n    environment: release\n    permissions:\n"
            "      contents: read          # main's tip",
            "the job deploys to",
        ),
        (
            "      actions: read           #",
            "      checks: write\n      actions: read           #",
            "the job holds",
        ),
        ("      security-events: read   #", "      security-events: write   #", "the job holds"),
        (
            _POST_CHECKOUT,
            _POST_CHECKOUT.replace("persist-credentials: false", "fetch-depth: 1"),
            "the post job's checkout keeps its credentials",
        ),
        (_POST_CHECKOUT, _POST_CHECKOUT + "          ref: refs/pull/1/head\n", "names a ref"),
        (
            "          permission-checks: write\n",
            "          permission-checks: write\n          permission-contents: write\n",
            "the controller's token is asked for",
        ),
        (
            "        if: steps.decide.outputs.post == 'true'\n        uses:",
            "        uses:",
            "minted",
        ),
        (
            _DECIDE_TOKEN,
            _DECIDE_TOKEN + "          KEY: ${{ secrets.CONTROLLER_APP_KEY }}\n",
            "a step other than the token step reads a secret",
        ),
        (
            _DECIDE_TOKEN,
            _DECIDE_TOKEN.replace("${{ github.token }}", "${{ steps.controller.outputs.token }}"),
            "reaches a step other than the one that posts",
        ),
        ("merge-controller post", "merge-controller decide", "does not post"),
        ("    needs: post\n", "", "the contract job runs other than after post"),
        (
            "      actions: write   #",
            "      actions: write\n      contents: write   #",
            "the contract job holds",
        ),
        (
            _CONTRACT_CHECKOUT,
            _CONTRACT_CHECKOUT.replace("persist-credentials: false", "fetch-depth: 1"),
            "the contract job's checkout keeps its credentials",
        ),
        (
            "          permission-contents: write\n",
            "          permission-contents: write\n          permission-workflows: write\n",
            "the contract job's App token is asked for",
        ),
        (
            _DISPATCH_TOKEN,
            _DISPATCH_TOKEN.replace("${{ github.token }}", "${{ steps.controller.outputs.token }}"),
            "the dispatch is not one step on the job's own token",
        ),
        (
            "          CLEANUP: ${{ needs.post.outputs.cleanup }}\n",
            "          CLEANUP: ${{ needs.post.outputs.cleanup }}\n"
            "          KEY: ${{ secrets.CONTROLLER_APP_KEY }}\n",
            "other than its token step reads a secret",
        ),
    ],
)
def test_a_planted_edit_of_the_poster_fails(old: str, new: str, says: str) -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"the plant's anchor is not once: {old!r}"
    assert any(says in p for p in _problems(text.replace(old, new))), _problems(
        text.replace(old, new)
    )


#: Each event the poster can be started by, and when its jobs take a runner: ``head``, a pull
#: request's wake, only with MERGE_VERDICT_LIVE 'true'; ``always``; ``never``.
_EVENTS: tuple[tuple[str, str, dict[str, Any], str], ...] = (
    ("a pull request's Merge run", "workflow_run", {"event": "pull_request"}, "head"),
    ("a Dependency review run", "workflow_run", {"event": "pull_request_target"}, "head"),
    (
        "CodeQL on a pull request",
        "workflow_run",
        {"event": "dynamic", "head_branch": "refs/pull/7/head"},
        "head",
    ),  # noqa: E501
    (
        "code scanning's CodeQL check",
        "check_run",
        {"app": {"id": 57789}, "name": "CodeQL"},
        "head",
    ),  # noqa: E501
    (
        "a contract run",
        "workflow_run",
        {"event": "workflow_dispatch", "head_branch": "contract-run/ab"},
        "always",
    ),  # noqa: E501
    ("the sweep", "schedule", {}, "always"),
    ("CodeQL on main", "workflow_run", {"event": "dynamic", "head_branch": "main"}, "never"),
    (
        "a dispatch on main",
        "workflow_run",
        {"event": "workflow_dispatch", "head_branch": "main"},
        "never",
    ),  # noqa: E501
    ("the push stage on main", "workflow_run", {"event": "push", "head_branch": "main"}, "never"),
    (
        "a schedule of the run's",
        "workflow_run",
        {"event": "schedule", "head_branch": "main"},
        "never",
    ),  # noqa: E501
    ("another App's check", "check_run", {"app": {"id": 15368}, "name": "CodeQL"}, "never"),
    (
        "code scanning's other check",
        "check_run",
        {"app": {"id": 57789}, "name": "Analyze"},
        "never",
    ),  # noqa: E501
)
#: MERGE_VERDICT_LIVE as the repository may hold it: before the flip, after it, and unset.
_LIVE = ("false", "true", None)


def _wanted(kind: str, live: str | None) -> bool:
    return kind == "always" or (kind == "head" and live == "true")


def _tokens(expr: str) -> list[str]:
    found = re.findall(r"\s*('[^']*'|&&|\|\||==|!=|[()!,]|[A-Za-z_][\w.-]*|\d+)", expr)
    assert "".join(found).replace(" ", "") == expr.replace(" ", ""), f"unread: {expr!r}"
    return found


def _evaluate(
    expr: str, event_name: str, payload: dict[str, Any], live: str | None = "true"
) -> Any:
    """GitHub's expression syntax as far as a job condition here uses it: ``==``/``!=`` (strings
    compared without case), ``&&``, ``||``, ``!``, parentheses, ``startsWith``, and
    ``github.event_name``, ``github.event.*`` and ``vars.*`` paths, a missing one being null."""
    key = "check_run" if event_name == "check_run" else "workflow_run"
    context: dict[str, Any] = {"event_name": event_name, "event": {key: payload}}
    variables = {} if live is None else {"MERGE_VERDICT_LIVE": live}
    tokens = _tokens(expr)

    def value(name: str) -> Any:
        if name in ("true", "false", "null"):
            return {"true": True, "false": False, "null": None}[name]
        here: Any = {"github": context, "vars": variables}
        for part in name.split("."):
            here = here.get(part) if isinstance(here, dict) else None
        return here

    def same(a: Any, b: Any) -> bool:
        if isinstance(a, str) and isinstance(b, str):
            return a.casefold() == b.casefold()
        return a == b

    def primary(i: int) -> tuple[Any, int]:
        tok = tokens[i]
        if tok == "(":
            got, i = either(i + 1)
            assert tokens[i] == ")"
            return got, i + 1
        if tok == "!":
            got, i = primary(i + 1)
            return not got, i
        if tok.startswith("'"):
            return tok[1:-1], i + 1
        if tok.isdigit():
            return int(tok), i + 1
        if tok == "startsWith":
            assert tokens[i + 1] == "("
            a, i = either(i + 2)
            assert tokens[i] == ","
            b, i = either(i + 1)
            assert tokens[i] == ")"
            return str(a or "").casefold().startswith(str(b or "").casefold()), i + 1
        return value(tok), i + 1

    def compare(i: int) -> tuple[Any, int]:
        left, i = primary(i)
        while i < len(tokens) and tokens[i] in ("==", "!="):
            op = tokens[i]
            right, i = primary(i + 1)
            left = same(left, right) if op == "==" else not same(left, right)
        return left, i

    def both(i: int) -> tuple[Any, int]:
        left, i = compare(i)
        while i < len(tokens) and tokens[i] == "&&":
            right, i = compare(i + 1)
            left = bool(left) and bool(right)
        return left, i

    def either(i: int) -> tuple[Any, int]:
        left, i = both(i)
        while i < len(tokens) and tokens[i] == "||":
            right, i = both(i + 1)
            left = bool(left) or bool(right)
        return left, i

    got, end = either(0)
    assert end == len(tokens), f"unread past {tokens[end:]}"
    return bool(got)


def _wake_problems(where: str, expr: str) -> list[str]:
    """Each event as the condition answers it, with MERGE_VERDICT_LIVE each way, against whether
    it can judge a head then."""
    return [
        f"{where} {'takes no runner for' if _wanted(kind, live) else 'takes a runner for'} {name}"
        f" with MERGE_VERDICT_LIVE {live!r}"
        for name, event_name, payload, kind in _EVENTS
        for live in _LIVE
        if _evaluate(expr, event_name, payload, live) is not _wanted(kind, live)
    ]


@pytest.mark.parametrize("live", _LIVE, ids=lambda v: f"live={v}")
@pytest.mark.parametrize("name, event_name, payload, kind", _EVENTS, ids=[e[0] for e in _EVENTS])
def test_the_wake_condition_takes_a_runner_only_for_what_can_judge_a_head(
    name: str, event_name: str, payload: dict[str, Any], kind: str, live: str | None
) -> None:
    assert _evaluate(_WAKES, event_name, payload, live) is _wanted(kind, live), name


def test_the_evaluator_reads_the_condition_it_judges() -> None:
    """A control for the evaluator itself: GitHub compares strings without case, and a missing
    property is null, which no string equals."""
    assert _evaluate("github.event_name == 'SCHEDULE'", "schedule", {})
    assert _evaluate("github.event.workflow_run.head_branch != 'main'", "workflow_run", {})
    assert not _evaluate("startsWith(github.event.workflow_run.head_branch, 'refs/')", "x", {})


@pytest.mark.parametrize(
    "old, new, says",
    [
        (
            "      || (github.event.workflow_run.event == 'dynamic'\n"
            "      && startsWith(github.event.workflow_run.head_branch, 'refs/pull/'))))))\n",
            "      || github.event.workflow_run.event == 'dynamic'))))\n",
            "takes a runner for CodeQL on main with MERGE_VERDICT_LIVE 'true'",
        ),
        (
            "      || github.event.workflow_run.event == 'pull_request_target'\n",
            "      || github.event.workflow_run.event == 'pull_request_target'\n"
            "      || github.event.workflow_run.event == 'push'\n",
            "takes a runner for the push stage on main",
        ),
        (
            "      && github.event.workflow_run.head_branch != 'main')\n",
            "      && github.event.workflow_run.head_branch != '')\n",
            "takes a runner for a dispatch on main",
        ),
        (
            "      && (github.event.workflow_run.event == 'pull_request'\n",
            "      && (github.event.workflow_run.event == 'pull_requests'\n",
            "takes no runner for a pull request's Merge run with MERGE_VERDICT_LIVE 'true'",
        ),
        (
            "      && github.event.check_run.app.id == 57789 && github.event.check_run.name == 'CodeQL')\n",  # noqa: E501
            "      && github.event.check_run.name == 'CodeQL')\n",
            "takes a runner for another App's check",
        ),
        # A pull request's wake that runs before the flip: the gate left out.
        (
            "      || (vars.MERGE_VERDICT_LIVE == 'true'\n"
            "      && ((github.event_name == 'check_run'\n",
            "      || (((github.event_name == 'check_run'\n",
            "takes a runner for a pull request's Merge run with MERGE_VERDICT_LIVE 'false'",
        ),
        # The dispatch path made to wait on the variable: a dispatch proof stops working.
        (
            "      || (github.event_name == 'workflow_run'\n"
            "      && github.event.workflow_run.event == 'workflow_dispatch'\n",
            "      || (vars.MERGE_VERDICT_LIVE == 'true' && github.event_name == 'workflow_run'\n"
            "      && github.event.workflow_run.event == 'workflow_dispatch'\n",
            "takes no runner for a contract run with MERGE_VERDICT_LIVE 'false'",
        ),
    ],
)
def test_a_planted_wake_condition_fails(old: str, new: str, says: str) -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert text.count(old) >= 1, f"the plant's anchor is gone: {old!r}"
    planted = text.replace(old, new, 1)
    found = _problems(planted)
    assert any(says in p for p in found), found
    assert any("condition is not exactly" in p for p in found), found


def test_a_step_that_names_the_triggering_runs_branch_fails() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8").replace(
        "          RUN_ID: ${{ github.event.workflow_run.id }}\n",
        "          RUN_ID: ${{ github.event.workflow_run.id }}\n"
        "          BRANCH: ${{ github.event.workflow_run.head_branch }}\n",
        1,
    )
    assert any("names the candidate's head" in p for p in _problems(text))


def test_a_schedule_more_often_than_ten_minutes_fails() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8").replace('"*/10 * * * *"', '"* * * * *"', 1)
    assert any("runs on" in p for p in _problems(text))


# ── contract.yml: main's contract over a head's definitions (#4584 step 4) ─────────────────────

_CONTRACT = REPO / ".github" / "workflows" / "contract.yml"
_CONTRACT_BRANCH_IF = "github.ref == format('refs/heads/contract-run/{0}', inputs.head)"
#: The name each contract run carries, which merge_controller.contract_run matches to the head.
_CONTRACT_RUN_NAME = "Contract ${{ inputs.head }}"


def _contract_workflow_problems(text: str) -> list[str]:
    found: list[str] = []
    data = yaml.safe_load(text)
    on = data.get("on", data.get(True))
    if not isinstance(on, dict) or set(on) != {"workflow_dispatch"}:
        found.append(f"contract.yml runs on {on!r}, not a dispatch alone")
    elif set((on["workflow_dispatch"] or {}).get("inputs") or {}) != {"head"}:
        found.append("contract.yml's dispatch takes other inputs than the head")
    # What it holds (contents: read, no secret, no environment, no cache save) is the contract's
    # row CONTRACT_RUN_HOLDS_NOTHING; this is its shape, which the poster reads.
    if data.get("run-name") != _CONTRACT_RUN_NAME:
        found.append("contract.yml's runs are not named for the head they judge")
    jobs = data.get("jobs") or {}
    if set(jobs) != {"contract"}:
        return [*found, f"contract.yml has jobs {sorted(jobs)}"]
    job = jobs["contract"]
    if job.get("if") != _CONTRACT_BRANCH_IF:
        found.append("contract.yml runs other than on the head's own contract branch")
    steps = _steps(job)
    found += _checkout_problems("contract.yml's job", steps)
    if any("inputs." in str(s.get("run", "")) for s in steps):
        found.append("contract.yml names the head in a step's script, not through its environment")
    if not any("python -m tools.ci contract" in str(s.get("run", "")) for s in steps):
        found.append("contract.yml does not run main's contract")
    return found


def test_the_contract_workflow_keeps_its_shape() -> None:
    assert _contract_workflow_problems(_CONTRACT.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "old, new, says",
    [
        ("  workflow_dispatch:\n", "  push:\n  workflow_dispatch:\n", "not a dispatch alone"),
        ("run-name: Contract ${{ inputs.head }}\n", "", "not named for the head"),
        (f"    if: {_CONTRACT_BRANCH_IF}\n", "", "other than on the head's own contract branch"),
        (
            'git worktree add --detach "$RUNNER_TEMP/head" "$HEAD_SHA"',
            'git worktree add --detach "$RUNNER_TEMP/head" "${{ inputs.head }}"',
            "in a step's script",
        ),
        (
            "          persist-credentials: false\n",
            "          fetch-depth: 1\n",
            "keeps its credentials",
        ),
        ("python -m tools.ci contract", "python -m tools.ci list", "does not run main's contract"),
    ],
)
def test_a_planted_edit_of_the_contract_workflow_fails(old: str, new: str, says: str) -> None:
    text = _CONTRACT.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"the plant's anchor is not once: {old!r}"
    found = _contract_workflow_problems(text.replace(old, new))
    assert any(says in p for p in found), found
