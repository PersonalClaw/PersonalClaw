"""``.github/workflows/merge-verdict.yml``: the merge verdict's poster keeps its trust shape.

It runs only as a ``workflow_run`` listener of Merge and of the runs that judge a head again
(Dependency review, CodeQL, Contract and DCO) and a listener of completed check runs, so GitHub
runs main's copy, and, beside a head's Merge run (the shape ``beside``), also on a pull request's
Merge run's start, to start the head's contract with it (``decide --start``, which posts nothing),
and on a dispatch on main alone, which a contract run's ``wake`` job sends for its head (``decide
--head``). It names nothing of the candidate but in its
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
step's environment (#4584 step 4); its ``wake`` job, in the shape ``beside``, holds
``actions: write`` alone, runs no action and reads nothing of the contract job, and dispatches the
poster on main for the head only after checking the head against its own branch. Each rule
fails on a planted edit, in each shape the tree may hold.
"""

from __future__ import annotations

import json
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
#: The poster's two shapes. ``after``: a head's contract starts when its Merge run completes.
#: ``beside``: it starts once the run has started a job on a runner (``decide --start``, on the
#: run's ``in_progress``), beside the run, and the contract run's own ``wake`` job dispatches the
#: poster on main for the head (``decide --head``) when it reports.
_AFTER, _BESIDE = "after", "beside"
_MERGE_PATH = f".github/workflows/{workflows.MERGE}"
#: The beside shape's runs that take a runner: the after shape's, with a dispatch on main alone
#: (a contract run's wake) always, the completions read only as completions, and, once the flip
#: sets MERGE_VERDICT_LIVE, a pull request's own Merge run's start (merge.yml's, by its path).
#: As the file spells it: one folded line each, which YAML joins with a space.
_BESIDE_LINES = (
    "github.event_name == 'schedule'",
    "|| (github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main')",
    "|| (github.event_name == 'workflow_run'",
    "&& github.event.action == 'completed'",
    "&& github.event.workflow_run.event == 'workflow_dispatch'",
    "&& github.event.workflow_run.head_branch != 'main')",
    "|| (vars.MERGE_VERDICT_LIVE == 'true'",
    "&& ((github.event_name == 'check_run'",
    "&& github.event.check_run.app.id == 57789 && github.event.check_run.name == 'CodeQL')",
    "|| (github.event_name == 'workflow_run'",
    "&& github.event.action == 'completed'",
    "&& (github.event.workflow_run.event == 'pull_request'",
    "|| github.event.workflow_run.event == 'pull_request_target'",
    "|| (github.event.workflow_run.event == 'dynamic'",
    "&& startsWith(github.event.workflow_run.head_branch, 'refs/pull/'))))",
    "|| (github.event_name == 'workflow_run'",
    "&& github.event.action == 'in_progress'",
    "&& github.event.workflow_run.event == 'pull_request'",
    f"&& github.event.workflow_run.path == '{_MERGE_PATH}')))",
)
_CONTRACT_BESIDE_LINES = (
    f"({_BESIDE_LINES[0]}",
    *_BESIDE_LINES[1:-1],
    f"{_BESIDE_LINES[-1]})",
    "&& (needs.post.outputs.dispatch != '' || needs.post.outputs.cleanup != '')",
)
_WAKES_BESIDE = " ".join(_BESIDE_LINES)
_CONTRACT_IF_BESIDE = " ".join(_CONTRACT_BESIDE_LINES)
_WAKES_BY_SHAPE = {_AFTER: _WAKES, _BESIDE: _WAKES_BESIDE}
#: What the beside shape's concurrency group keys a dispatch on: the head it names, empty for any
#: other event (a context the concurrency row models, unlike ``inputs``).
_HEAD_KEY = "${{ github.event.inputs.head }}"
#: And what it keys a run's start apart from its completion on: GitHub sends a run's
#: ``in_progress`` again as its jobs start, and a completion that shared their group would queue
#: behind them.
_ACTION_KEY = "${{ github.event.action }}"
_CONTRACT_IF_BY_SHAPE = {_AFTER: _CONTRACT_IF, _BESIDE: _CONTRACT_IF_BESIDE}
#: The beside shape's decision: a started run is decided by ``--start`` alone, before anything
#: else, and a wake by ``--head``.
_START_BRANCH = re.compile(
    r'if \[ -n "\$RUN_ID" \] && \[ "\$ACTION" = in_progress \]; then\n(?:\s*#[^\n]*\n)*'
    r'\s*which=\(--start "\$RUN_ID"\)\n'
)
#: A dispatch's head, checked whole before the controller reads it, so a value that starts with
#: ``-`` never reaches its options; grep would pass a value with one good line among others.
_HEAD_CHECK = '[[ ! "$WAKE_HEAD" =~ ^[0-9a-f]{40}$ ]]'
#: What a head that fails it does: posts nothing and ends the step.
_HEAD_REFUSED = ('echo "post=false" >> "$GITHUB_OUTPUT"', "exit 0")
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


def _contract_problems(job: dict, shape: str = _AFTER) -> list[str]:
    found: list[str] = []
    if job.get("needs") != "post" or job.get("if") != _CONTRACT_IF_BY_SHAPE[shape]:
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
    shape = _shape(on)
    if shape is None:
        found.append(
            f"runs on {on!r}, not only a completed Merge run, nor that and a started one with its"
            " contract's wake"
        )
        shape = _AFTER
    jobs = data.get("jobs") or {}
    if set(jobs) != {"post", "contract"}:
        return [*found, f"has jobs {sorted(jobs)}"]
    found += _contract_problems(jobs["contract"], shape)
    job = jobs["post"]
    if job.get("if") != _WAKES_BY_SHAPE[shape]:
        found.append(
            "the post job's condition is not exactly: the runs that can judge a pull request's"
            f" head ({job.get('if')!r})"
        )
    found += _wake_problems("the post job", str(job.get("if")), shape)
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
    elif shape == _BESIDE:
        found += _beside_decide_problems(decide)
    if shape == _BESIDE:
        group = data.get("concurrency") or {}
        if _HEAD_KEY not in str(group.get("group")) or group.get("cancel-in-progress") is not False:
            found.append(
                "a wake's runs share one concurrency group, or one cancels another: the group"
                f" must key on {_HEAD_KEY} and never cancel"
            )
        if _ACTION_KEY not in str(group.get("group")):
            found.append(
                "a run's start and its completion share one concurrency group: the group must"
                f" key on {_ACTION_KEY}"
            )
    return found


def _shape(on: object) -> str | None:
    """Which of the poster's two shapes its triggers are, or None for neither."""
    names = [workflows.NAMES[workflows.MERGE], "Dependency review", "CodeQL", "Contract", "DCO"]
    both = {"check_run": {"types": ["completed"]}, "schedule": [{"cron": "*/10 * * * *"}]}
    if on == {"workflow_run": {"workflows": names, "types": ["completed"]}, **both}:
        return _AFTER
    if not isinstance(on, dict) or set(on) != {"workflow_run", "workflow_dispatch", *both}:
        return None
    dispatch = on["workflow_dispatch"]
    inputs = dispatch.get("inputs") if isinstance(dispatch, dict) else None
    head = inputs.get("head") if isinstance(inputs, dict) else None
    if (
        all(on[k] == v for k, v in both.items())
        and on["workflow_run"] == {"workflows": names, "types": ["in_progress", "completed"]}
        and set(dispatch) == {"inputs"}
        and set(inputs) == {"head"}
        and isinstance(head, dict)
        and head.get("required") is True
        and set(head) <= {"description", "required"}
    ):
        return _BESIDE
    return None


def _beside_decide_problems(step: dict) -> list[str]:
    found: list[str] = []
    env = step.get("env") or {}
    if env.get("ACTION") != "${{ github.event.action }}" or env.get("WAKE_HEAD") != (
        "${{ inputs.head }}"
    ):
        found.append("the decision does not read the run's action and the wake's head")
    run = str(step.get("run", ""))
    if not _START_BRANCH.search(run):
        found.append("a started run is not decided by --start alone, before anything else")
    if run.count("--start") != 1:
        found.append("--start is passed other than for a started run")
    if 'which=(--head "$WAKE_HEAD")' not in run:
        found.append("a contract run's wake is not decided by --head")
    lines = [s for s in (line.strip() for line in run.splitlines()) if s and s[0] != "#"]
    named = [s for s in lines if "WAKE_HEAD" in s]
    wake, decided = 'elif [ -n "$WAKE_HEAD" ]; then', 'which=(--head "$WAKE_HEAD")'
    at = lines.index(wake) if wake in lines else len(lines)
    # After the check, past its notice: what a head that fails it does, then the decision.
    then = [s for s in lines[at + 2 :] if not s.startswith('echo "::notice ')][:4]
    if named != [wake, f"if {_HEAD_CHECK}; then", decided] or then != [
        *_HEAD_REFUSED,
        "fi",
        decided,
    ]:
        found.append(
            "a dispatch's head reaches the controller unchecked, or one that fails the check"
            " does not end the step with nothing posted"
        )
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


#: The poster's wake, which workflows' runs it judges, as the tree spells it, in either shape (its
#: types are ``[completed]``, or ``[in_progress, completed]`` beside): a plant anchored here reads
#: a tree that lists the DCO run as well as one that does not.
_WAKE = "<the poster's workflow_run wake>"
_WAKE_LIST = "<the poster's workflows list>"
_WAKE_LINES = re.compile(r"    (workflows: \[[^\]\n]*\])\n    types: \[[^\]\n]*\]\n")


def _anchored(text: str, old: str, new: str) -> tuple[str, str]:
    """*old* and *new* with the wake's placeholders read from *text*."""
    found = _WAKE_LINES.search(text)
    assert found, "the poster lists no workflow_run wake"
    for placeholder, spelled in ((_WAKE, found.group(0)), (_WAKE_LIST, found.group(1))):
        old, new = old.replace(placeholder, spelled), new.replace(placeholder, spelled)
    return old, new


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
            _WAKE + "  repository_dispatch: {}\n",
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
            _WAKE_LIST,
            'workflows: ["Merge", "PR"]',
            "runs on",
        ),
        (
            _WAKE_LIST,
            'workflows: ["Merge", "Dependency review", "CodeQL"]',
            "runs on",
        ),
        (
            _WAKE_LIST,
            'workflows: ["Merge", "Dependency review", "CodeQL", "Contract", "DCO", "PR"]',
            "runs on",
        ),
        (
            _WAKE_LIST,
            'workflows: ["Merge", "Dependency review", "CodeQL", "Contract"]',
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
    old, new = _anchored(text, old, new)
    assert text.count(old) == 1, f"the plant's anchor is not once: {old!r}"
    assert any(says in p for p in _problems(text.replace(old, new))), _problems(
        text.replace(old, new)
    )


#: Each event the poster can be started by, and when its jobs take a runner: ``head``, a pull
#: request's wake, only with MERGE_VERDICT_LIVE 'true'; ``always``; ``never``. Each is a
#: completion (``completed``), a check run's (``check``), the schedule, a run's start
#: (``in_progress``) or a dispatch of the poster (``dispatch``): the after shape is started by the
#: first three alone.
_FORK = {"head_repository": {"full_name": "someone/project", "fork": True}, "pull_requests": []}
_EVENTS: tuple[tuple[str, str, dict[str, Any], str, str], ...] = (
    ("a pull request's Merge run", "workflow_run", {"event": "pull_request"}, "head", "completed"),
    (
        "a Dependency review run",
        "workflow_run",
        {"event": "pull_request_target"},
        "head",
        "completed",
    ),
    ("a DCO run", "workflow_run", {"event": "pull_request_target"}, "head", "completed"),
    (
        "CodeQL on a pull request",
        "workflow_run",
        {"event": "dynamic", "head_branch": "refs/pull/7/head"},
        "head",
        "completed",
    ),
    (
        "code scanning's CodeQL check",
        "check_run",
        {"app": {"id": 57789}, "name": "CodeQL"},
        "head",
        "check",
    ),
    (
        "a contract run",
        "workflow_run",
        {"event": "workflow_dispatch", "head_branch": "contract-run/ab"},
        "always",
        "completed",
    ),
    ("the sweep", "schedule", {}, "always", "schedule"),
    (
        "CodeQL on main",
        "workflow_run",
        {"event": "dynamic", "head_branch": "main"},
        "never",
        "completed",
    ),
    (
        "a dispatch on main",
        "workflow_run",
        {"event": "workflow_dispatch", "head_branch": "main"},
        "never",
        "completed",
    ),
    (
        "the push stage on main",
        "workflow_run",
        {"event": "push", "head_branch": "main"},
        "never",
        "completed",
    ),
    (
        "a schedule of the run's",
        "workflow_run",
        {"event": "schedule", "head_branch": "main"},
        "never",
        "completed",
    ),
    (
        "another App's check",
        "check_run",
        {"app": {"id": 15368}, "name": "CodeQL"},
        "never",
        "check",
    ),
    (
        "code scanning's other check",
        "check_run",
        {"app": {"id": 57789}, "name": "Analyze"},
        "never",
        "check",
    ),
    (
        "a pull request's Merge run starting",
        "workflow_run",
        {"event": "pull_request", "path": _MERGE_PATH},
        "head",
        "in_progress",
    ),
    (
        "a fork's Merge run starting, its branch named main",
        "workflow_run",
        {"event": "pull_request", "path": _MERGE_PATH, "head_branch": "main", **_FORK},
        "head",
        "in_progress",
    ),
    (
        "a Dependency review run starting",
        "workflow_run",
        {"event": "pull_request_target", "path": ".github/workflows/dependency-review.yml"},
        "never",
        "in_progress",
    ),
    (
        "a DCO run starting",
        "workflow_run",
        {"event": "pull_request_target", "path": ".github/workflows/dco.yml"},
        "never",
        "in_progress",
    ),
    (
        "CodeQL on a pull request starting",
        "workflow_run",
        {
            "event": "dynamic",
            "head_branch": "refs/pull/7/head",
            "path": "dynamic/github-code-scanning/codeql",
        },
        "never",
        "in_progress",
    ),
    (
        "a contract run starting",
        "workflow_run",
        {
            "event": "workflow_dispatch",
            "head_branch": "contract-run/ab",
            "path": ".github/workflows/contract.yml",
        },
        "never",
        "in_progress",
    ),
    (
        "another workflow named Merge starting",
        "workflow_run",
        {"event": "pull_request", "path": ".github/workflows/merge-copy.yml"},
        "never",
        "in_progress",
    ),
    (
        "the push stage on main starting",
        "workflow_run",
        {"event": "push", "path": _MERGE_PATH, "head_branch": "main"},
        "never",
        "in_progress",
    ),
    (
        "a contract run's wake, a dispatch of the poster on main",
        "workflow_dispatch",
        {"ref": "refs/heads/main"},
        "always",
        "dispatch",
    ),
    (
        "a dispatch of the poster off main",
        "workflow_dispatch",
        {"ref": "refs/heads/contract-run/ab"},
        "never",
        "dispatch",
    ),
)
#: What starts each shape's runs.
_DELIVERS = {
    _AFTER: frozenset({"completed", "check", "schedule"}),
    _BESIDE: frozenset({"completed", "check", "schedule", "in_progress", "dispatch"}),
}
#: MERGE_VERDICT_LIVE as the repository may hold it: before the flip, after it, and unset.
_LIVE = ("false", "true", None)


def _wanted(kind: str, live: str | None) -> bool:
    return kind == "always" or (kind == "head" and live == "true")


def _tokens(expr: str) -> list[str]:
    found = re.findall(r"\s*('[^']*'|&&|\|\||==|!=|[()!,]|[A-Za-z_][\w.-]*|\d+)", expr)
    assert "".join(found).replace(" ", "") == expr.replace(" ", ""), f"unread: {expr!r}"
    return found


def _evaluate(
    expr: str,
    event_name: str,
    payload: dict[str, Any],
    live: str | None = "true",
    action: str = "completed",
) -> Any:
    """GitHub's expression syntax as far as a job condition here uses it: ``==``/``!=`` (strings
    compared without case), ``&&``, ``||``, ``!``, parentheses, ``startsWith``, and
    ``github.event_name``, ``github.event.*``, ``github.ref`` and ``vars.*`` paths, a missing one
    being null. A dispatch's payload is its ``ref``; a run's or a check's carries its ``action``."""
    if event_name == "workflow_dispatch":
        context: dict[str, Any] = {"event_name": event_name, "ref": payload.get("ref"), "event": {}}
    else:
        key = "check_run" if event_name == "check_run" else "workflow_run"
        event = {key: payload, **({"action": action} if event_name != "schedule" else {})}
        context = {"event_name": event_name, "ref": "refs/heads/main", "event": event}
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


def _answers(expr: str, event: tuple[str, str, dict[str, Any], str, str], live: str | None) -> Any:
    _, event_name, payload, _, starts = event
    action = {"completed": "completed", "check": "completed", "in_progress": "in_progress"}
    return _evaluate(expr, event_name, payload, live, action.get(starts, ""))


def _wake_problems(where: str, expr: str, shape: str = _AFTER) -> list[str]:
    """Each event that starts the shape's runs, as the condition answers it, with
    MERGE_VERDICT_LIVE each way, against whether it can judge a head then."""
    return [
        f"{where} {'takes no runner for' if _wanted(e[3], live) else 'takes a runner for'} {e[0]}"
        f" with MERGE_VERDICT_LIVE {live!r}"
        for e in _EVENTS
        if e[4] in _DELIVERS[shape]
        for live in _LIVE
        if _answers(expr, e, live) is not _wanted(e[3], live)
    ]


_SHAPED_EVENTS = [
    (shape, e) for shape in (_AFTER, _BESIDE) for e in _EVENTS if e[4] in _DELIVERS[shape]
]


@pytest.mark.parametrize("live", _LIVE, ids=lambda v: f"live={v}")
@pytest.mark.parametrize(
    "shape, event", _SHAPED_EVENTS, ids=[f"{shape}: {e[0]}" for shape, e in _SHAPED_EVENTS]
)
def test_the_wake_condition_takes_a_runner_only_for_what_can_judge_a_head(
    shape: str, event: tuple[str, str, dict[str, Any], str, str], live: str | None
) -> None:
    assert _answers(_WAKES_BY_SHAPE[shape], event, live) is _wanted(event[3], live), event[0]


def test_the_conditions_read_only_the_fields_they_name() -> None:
    """What either condition reads of an event: its name, its action, its ref, the run's event,
    branch and path, the check's App and name, and the variable. Nothing a fork's author names but
    the branch, which only a dispatch's and CodeQL's clauses read."""
    for expr in (_WAKES, _WAKES_BESIDE):
        read = {t for t in _tokens(expr) if t.startswith(("github.", "vars."))}
        assert read <= {
            "github.event_name",
            "github.event.action",
            "github.ref",
            "github.event.workflow_run.event",
            "github.event.workflow_run.head_branch",
            "github.event.workflow_run.path",
            "github.event.check_run.app.id",
            "github.event.check_run.name",
            "vars.MERGE_VERDICT_LIVE",
        }, read
    fork = next(e for e in _EVENTS if e[0].startswith("a fork's"))
    plain = next(e for e in _EVENTS if e[0] == "a pull request's Merge run starting")
    for live in _LIVE:
        assert _answers(_WAKES_BESIDE, fork, live) is _answers(_WAKES_BESIDE, plain, live)


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
            "      && startsWith(github.event.workflow_run.head_branch, 'refs/pull/'))",
            "      || github.event.workflow_run.event == 'dynamic'",
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
            "      && github.event.workflow_run.event == 'workflow_dispatch'\n",
            "      && vars.MERGE_VERDICT_LIVE == 'true'\n"
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


#: The beside shape's ``wake`` job of contract.yml: after the contract, whatever it concluded, on
#: the head's own branch, dispatch the poster on main for the head.
_WAKE_IF = f"always() && {_CONTRACT_BRANCH_IF}"
_WAKE_ENV = {"GH_TOKEN": "${{ github.token }}", "HEAD_SHA": "${{ inputs.head }}"}
_WAKE_CHECKS = (
    "grep -Eq '^[0-9a-f]{40}$'",
    'test "$GITHUB_REF" = "refs/heads/contract-run/$HEAD_SHA"',
)
#: The Runner identity step every job opens with: its script, as the tree spells it.
_IDENTITY_RUN = 'echo "runner image ${ImageOS:?not a GitHub-hosted runner image}'
_WAKE_DISPATCH = (
    'gh workflow run merge-verdict.yml --repo "$GITHUB_REPOSITORY" --ref main -f head="$HEAD_SHA"'
)


def _wake_job_problems(job: dict) -> list[str]:
    found: list[str] = []
    if job.get("needs") != "contract" or job.get("if") != _WAKE_IF:
        found.append("contract.yml's wake runs other than after the contract, on the head's branch")
    if job.get("permissions") != {"actions": "write"}:
        found.append(
            f"contract.yml's wake holds {job.get('permissions')!r}, not actions: write alone"
        )
    held = json.dumps(job)
    if "environment" in job or "secrets." in held or "needs." in held:
        found.append(
            "contract.yml's wake reads a secret, an environment or the contract job's work"
        )
    steps = _steps(job)
    identity = [st for st in steps if st.get("name") == "Runner identity"]
    rest = [st for st in steps if st not in identity]
    if (
        len(identity) != 1
        or not str(identity[0].get("run", "")).startswith(_IDENTITY_RUN)
        or len(rest) != 1
        or any("uses" in st for st in steps)
    ):
        return [
            *found,
            "contract.yml's wake runs an action, or more than its Runner identity and its one step",
        ]
    step = rest[0]
    if step.get("env") != _WAKE_ENV:
        found.append("contract.yml's wake holds a token or names the head other than its own")
    run = str(step.get("run", ""))
    if "inputs." in run:
        found.append("contract.yml names the head in a step's script, not through its environment")
    at = run.find(_WAKE_DISPATCH)
    if at < 0 or run.count("gh ") != 1:
        found.append("contract.yml's wake does other than dispatch the poster on main for its head")
    elif not all(0 <= run.find(check) < at for check in _WAKE_CHECKS):
        found.append("contract.yml's wake dispatches before it checks the head against its branch")
    return found


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
    if set(jobs) not in ({"contract"}, {"contract", "wake"}):
        return [*found, f"contract.yml has jobs {sorted(jobs)}"]
    if "wake" in jobs:
        found += _wake_job_problems(jobs["wake"])
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


# ── The beside shape: the head's contract starts beside its Merge run ──────────────────────────


def _folded(lines: tuple[str, ...]) -> str:
    """A job condition as the file spells one: a folded block of *lines*."""
    return "    if: >-\n" + "".join(f"      {line}\n" for line in lines)


#: Today's poster (the after shape) made beside, beyond its two conditions.
_TO_BESIDE = (
    (
        '    workflows: ["Merge", "Dependency review", "CodeQL", "Contract"]\n'
        "    types: [completed]\n",
        '    workflows: ["Merge", "Dependency review", "CodeQL", "Contract"]\n'
        "    types: [in_progress, completed]\n",
    ),
    (
        '    - cron: "*/10 * * * *"\n',
        '    - cron: "*/10 * * * *"\n  workflow_dispatch:\n    inputs:\n      head:\n'
        "        description: the head whose contract run reported (a full commit id)\n"
        "        required: true\n",
    ),
    (
        "-check-${{ github.event.check_run.id }}\n",
        "-check-${{ github.event.check_run.id }}-on-${{ github.event.action }}"
        "-head-${{ github.event.inputs.head }}\n",
    ),
    (
        "          CHECK_RUN_ID: ${{ github.event.check_run.id }}\n",
        "          CHECK_RUN_ID: ${{ github.event.check_run.id }}\n"
        "          ACTION: ${{ github.event.action }}\n          WAKE_HEAD: ${{ inputs.head }}\n",
    ),
    (
        '          if [ -n "$RUN_ID" ]; then\n            which=(--run-id "$RUN_ID")\n',
        '          if [ -n "$RUN_ID" ] && [ "$ACTION" = in_progress ]; then\n'
        '            which=(--start "$RUN_ID")\n'
        '          elif [ -n "$RUN_ID" ]; then\n            which=(--run-id "$RUN_ID")\n'
        '          elif [ -n "$WAKE_HEAD" ]; then\n'
        f"            if {_HEAD_CHECK}; then\n"
        '              echo "::notice title=no head::the dispatch names no full commit id"\n'
        f"              {_HEAD_REFUSED[0]}\n              {_HEAD_REFUSED[1]}\n            fi\n"
        '            which=(--head "$WAKE_HEAD")\n',
    ),
)
_WAKE_JOB = (
    "\n  wake:\n    needs: contract\n"
    f"    if: {_WAKE_IF}\n"
    "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n"
    "    permissions:\n      actions: write\n"
    "    steps:\n      - name: Runner identity\n"
    f"        run: {_IDENTITY_RUN} ${{ImageVersion:?no image version}} on ${{RUNNER_OS}}"
    ' ${RUNNER_ARCH}"\n'
    "      - name: judge the head again\n        env:\n"
    "          GH_TOKEN: ${{ github.token }}\n          HEAD_SHA: ${{ inputs.head }}\n"
    "        run: |\n          set -euo pipefail\n"
    f"          printf '%s' \"$HEAD_SHA\" | {_WAKE_CHECKS[0]}\n"
    f"          {_WAKE_CHECKS[1]}\n"
    f"          {_WAKE_DISPATCH}\n"
)


def _beside_poster() -> str:
    """The tree's poster in the beside shape: itself once it is, else today's with the edits
    that make it so, so these controls read both trees."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if _shape(data.get("on", data.get(True))) == _BESIDE:
        return text
    # The post job's condition, then the contract job's: unpacking refuses any other number.
    posts, contracts = _IF_BLOCK.findall(text)
    text = text.replace(posts, _folded(_BESIDE_LINES), 1)
    text = text.replace(contracts, _folded(_CONTRACT_BESIDE_LINES), 1)
    for old, new in _TO_BESIDE:
        assert text.count(old) == 1, f"the edit's anchor is not once: {old!r}"
        text = text.replace(old, new)
    return text


def _beside_contract() -> str:
    text = _CONTRACT.read_text(encoding="utf-8")
    return text if "\n  wake:\n" in text else text.rstrip("\n") + "\n" + _WAKE_JOB


def test_the_beside_shape_keeps_its_trust_shape() -> None:
    assert _problems(_beside_poster()) == []
    assert _contract_workflow_problems(_beside_contract()) == []


_EXPR = re.compile(r"\$\{\{\s*([\w.]+)\s*\}\}")


def _group_for(group: str, event_name: str, event: dict[str, Any]) -> str:
    """The concurrency group as GitHub renders it for an event: each ``${{ github.<path> }}`` is
    that path of the event's context, a missing one empty."""
    context = {"github": {"event_name": event_name, "event": event}}

    def value(match: re.Match[str]) -> str:
        here: Any = context
        for part in match.group(1).split("."):
            here = here.get(part) if isinstance(here, dict) else None
        return "" if here is None else str(here)

    return _EXPR.sub(value, group)


def test_a_runs_start_and_its_completion_never_share_a_group() -> None:
    group = str(yaml.safe_load(_beside_poster())["concurrency"]["group"])

    def run(run_id: int, action: str, key: str = group) -> str:
        payload = {"workflow_run": {"id": run_id, "run_attempt": 1}, "action": action}
        return _group_for(key, "workflow_run", payload)

    def wake(head: str) -> str:
        return _group_for(group, "workflow_dispatch", {"inputs": {"head": head}})

    # A run's starts share one group, and its completion has its own.
    assert run(7, "in_progress") == run(7, "in_progress") != run(7, "completed")
    # The control: it is the action that keys them apart.
    alike = group.replace(f"-on-{_ACTION_KEY}", "")
    assert alike != group and run(7, "in_progress", alike) == run(7, "completed", alike)
    # Another run's start is its own, and a wake keys on its head, apart from the schedule's.
    assert run(8, "in_progress") != run(7, "in_progress")
    assert wake("a" * 40) != wake("b" * 40) != _group_for(group, "schedule", {})
    assert wake("a" * 40) == wake("a" * 40)


def test_the_folded_conditions_read_as_the_expressions() -> None:
    for lines, expr in (
        (_BESIDE_LINES, _WAKES_BESIDE),
        (_CONTRACT_BESIDE_LINES, _CONTRACT_IF_BESIDE),
    ):
        assert yaml.safe_load("job:\n" + _folded(lines))["job"]["if"] == expr
    poster = yaml.safe_load(_beside_poster())["jobs"]
    assert (poster["post"]["if"], poster["contract"]["if"]) == (_WAKES_BESIDE, _CONTRACT_IF_BESIDE)


@pytest.mark.parametrize(
    "old, new, says",
    [
        # A dispatch of the poster off main: a branch's copy of the poster runs.
        (
            "      || (github.event_name == 'workflow_dispatch'"
            " && github.ref == 'refs/heads/main')\n",
            "      || github.event_name == 'workflow_dispatch'\n",
            "takes a runner for a dispatch of the poster off main",
        ),
        # Any workflow named Merge starting, not merge.yml by its path.
        (
            "\n      && github.event.workflow_run.path == '.github/workflows/merge.yml')))\n",
            ")))\n",
            "takes a runner for another workflow named Merge starting",
        ),
        # A completion's clause that reads a start too.
        (
            "      || (github.event_name == 'workflow_run'\n"
            "      && github.event.action == 'completed'\n"
            "      && (github.event.workflow_run.event == 'pull_request'\n",
            "      || (github.event_name == 'workflow_run'\n"
            "      && (github.event.workflow_run.event == 'pull_request'\n",
            "takes a runner for a Dependency review run starting",
        ),
        # A start that waits on nothing: the variable left out of its clause.
        (
            "      || (github.event_name == 'workflow_run'\n"
            "      && github.event.action == 'in_progress'\n",
            "      || (github.event_name == 'workflow_run' && true\n"
            "      && github.event.action == 'in_progress'\n",
            "condition is not exactly",
        ),
        (
            '            which=(--run-id "$RUN_ID")\n',
            '            which=(--start "$RUN_ID")\n',
            "--start is passed",
        ),
        (
            '          if [ -n "$RUN_ID" ] && [ "$ACTION" = in_progress ]; then\n',
            '          if [ -n "$RUN_ID" ] && [ "$ACTION" = completed ]; then\n',
            "not decided by --start alone",
        ),
        (
            '            which=(--head "$WAKE_HEAD")\n',
            "            which=(--waiting)\n",
            "not decided by --head",
        ),
        (
            "          WAKE_HEAD: ${{ inputs.head }}\n",
            "          WAKE_HEAD: ${{ github.event.workflow_run.head_sha }}\n",
            "names the candidate's head",
        ),
        (
            "    types: [in_progress, completed]\n",
            "    types: [requested, in_progress, completed]\n",
            "runs on",
        ),
        (
            "-head-${{ github.event.inputs.head }}\n",
            "\n",
            "a wake's runs share one concurrency group",
        ),
        # A start and its run's completion in one group: the completion queues behind the starts.
        (
            "-on-${{ github.event.action }}-head-",
            "-head-",
            "a run's start and its completion share one concurrency group",
        ),
        (
            "  cancel-in-progress: false\n",
            "  cancel-in-progress: true\n",
            "a wake's runs share one concurrency group",
        ),
        (
            "        required: true\n",
            "        required: true\n      other:\n        required: false\n",
            "runs on",
        ),
        (
            "  workflow_dispatch:\n    inputs:\n      head:\n",
            "  workflow_dispatch:\n    inputs:\n      ref:\n",
            "runs on",
        ),
        (
            "  workflow_dispatch:\n    inputs:\n      head:\n",
            "  workflow_dispatch: {}\n  removed:\n    inputs:\n      head:\n",
            "runs on",
        ),
        # The head handed to the controller unchecked.
        (f"            if {_HEAD_CHECK}; then\n", "            if false; then\n", "unchecked"),
        # A check grep makes, line by line: `--run-id` above a good line passes it.
        (
            f"            if {_HEAD_CHECK}; then\n",
            "            if ! printf '%s' \"$WAKE_HEAD\" | grep -Eq '^[0-9a-f]{40}$'; then\n",
            "unchecked",
        ),
        # A head that fails the check goes on to the controller.
        (f"              {_HEAD_REFUSED[1]}\n", "              true\n", "unchecked"),
        # Or posts on.
        (f"              {_HEAD_REFUSED[0]}\n", "", "unchecked"),
        # The head read once more after it, where nothing checked it.
        (
            '            which=(--head "$WAKE_HEAD")\n',
            '            which=(--head "$WAKE_HEAD" --out "$WAKE_HEAD")\n',
            "unchecked",
        ),
    ],
)
def test_a_planted_edit_of_the_beside_poster_fails(old: str, new: str, says: str) -> None:
    text = _beside_poster()
    assert text.count(old) >= 1, f"the plant's anchor is gone: {old!r}"
    found = _problems(text.replace(old, new, 1))
    assert any(says in p for p in found), found


@pytest.mark.parametrize(
    "old, new, says",
    [
        (
            "      actions: write\n",
            "      actions: write\n      contents: write\n",
            "not actions: write alone",
        ),
        ("    permissions:\n      actions: write\n", "", "not actions: write alone"),
        (
            "      - name: judge the head again\n",
            "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
            "      - name: judge the head again\n",
            "runs an action",
        ),
        (
            "          GH_TOKEN: ${{ github.token }}\n          HEAD_SHA: ${{ inputs.head }}\n",
            "          GH_TOKEN: ${{ github.token }}\n          HEAD_SHA: ${{ inputs.head }}\n"
            "          OUT: ${{ needs.contract.outputs.x }}\n",
            "the contract job's work",
        ),
        (
            "          GH_TOKEN: ${{ github.token }}\n          HEAD_SHA",
            "          GH_TOKEN: ${{ secrets.CONTROLLER_APP_KEY }}\n          HEAD_SHA",
            "reads a secret",
        ),
        (" --ref main ", ' --ref "$GITHUB_REF" ', "does other than dispatch the poster on main"),
        (' -f head="$HEAD_SHA"', ' -f head="${{ inputs.head }}"', "in a step's script"),
        (f"    if: {_WAKE_IF}\n", "    if: always()\n", "other than after the contract"),
        ("    needs: contract\n", "", "other than after the contract"),
        (
            "\n  wake:\n",
            "\n  extra:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo\n\n  wake:\n",
            "contract.yml has jobs",
        ),
    ],
)
def test_a_planted_edit_of_the_wake_fails(old: str, new: str, says: str) -> None:
    text = _beside_contract()
    assert text.count(old) >= 1, f"the plant's anchor is gone: {old!r}"
    found = _contract_workflow_problems(text.replace(old, new, 1))
    assert any(says in p for p in found), found


def test_a_wake_that_dispatches_before_its_check_fails() -> None:
    text = _beside_contract()
    check = next(line for line in text.splitlines() if _WAKE_CHECKS[1] in line)
    dispatch = next(line for line in text.splitlines() if _WAKE_DISPATCH in line)
    planted = text.replace(check + "\n", "", 1).replace(dispatch, f"{dispatch}\n{check}", 1)
    found = _contract_workflow_problems(planted)
    assert any("dispatches before it checks" in p for p in found), found
