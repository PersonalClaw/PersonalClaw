"""``.github/workflows/merge-verdict.yml``: the merge verdict's poster keeps its trust shape.

It runs only as a ``workflow_run`` listener of Merge and of the runs that judge a head again
(CodeQL, Contract and each gate's) and a listener of completed check runs,
so GitHub runs main's copy, and, beside a head's Merge run (the shape ``beside``), also on a pull
request's Merge run's start, to start the head's contract with it (``decide --start``, which posts
nothing), and on a dispatch on main alone, which a contract run's ``wake`` job sends for its head
(``decide --head``). It names nothing of the candidate but in its
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
poster on main for the head only after checking the head against its own branch. Its condition
is the queue shape's (0.4.0 A1): a queue commit's Merge run is taken as a pull request's is, on its
completion and its start. The workflow grants nothing past ``contents: read``: each write is a
job's own. In every job, a runner identity step is the tree's one script, alone; no step's script
pastes an expression (``${{``), so nothing an event names becomes shell; and its actions are main's
checkout and the App's token alone. Every step of every job is its fixture as a whole: its keys,
condition, environment, inputs and closed list of lines, nothing past them (no ``shell``, no
``BASH_ENV``); and neither the workflow nor a job sets ``env``, ``defaults`` or a ``container``,
which would reach every step under it. Each job runs on a GitHub-hosted ``ubuntu-24.04`` runner.
contract.yml's wake is held the same way. A poster may also hold the in-place rerun's two jobs
(#5809 piece 3), both or neither: ``rerun``, after ``post`` and only for a run post names, holds
``actions: write`` with ``contents: read``, deploys nowhere, reads no secret, and runs exactly three
steps: the runner identity, main's checkout and the controller's ``rerun`` on its own token, within
a bound inside the merge queue's check limit; ``rerun-fallback``, for that run whenever ``rerun``
did not succeed, holds post's reads and post's one token step and decides the queue commit with its
rerun ended.
Each rule fails on a planted edit.
"""

from __future__ import annotations

import json
import re
from collections import Counter
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
#: The poster's triggers, ``beside``: a head's contract starts once the run has started a job on a
#: runner (``decide --start``, on the run's ``in_progress``), beside the run, and the contract
#: run's own ``wake`` job dispatches the poster on main for the head (``decide --head``) when it
#: reports. (The ``after`` shape, which started it at the run's completion, is gone: it took no
#: queue commit's start.)
_BESIDE = "beside"
_MERGE_PATH = f".github/workflows/{workflows.MERGE}"
#: The beside shape's runs that take a runner: the sweep, a completed dispatch off main, and a
#: dispatch on main alone (a contract run's wake) always, the completions read only as completions,
#: and, once the flip
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
#: The queue shape (0.4.0 A1): the beside shape's triggers, and its condition with a queue
#: commit's Merge run (``merge_group``) taken as a pull request's is, on its completion and on its
#: start. A poster with the beside shape's triggers is held to it: the beside condition alone is
#: refused.
_QUEUE = "queue"
_QUEUE_RUN = "|| github.event.workflow_run.event == 'merge_group'"
_STARTS = "&& github.event.workflow_run.event == 'pull_request'"
_QUEUE_LINES = tuple(
    part
    for line in _BESIDE_LINES
    for part in (
        (line, _QUEUE_RUN)
        if line == "&& (github.event.workflow_run.event == 'pull_request'"
        else (
            ("&& (github.event.workflow_run.event == 'pull_request'", f"{_QUEUE_RUN})")
            if line == _STARTS
            else (line,)
        )
    )
)
_CONTRACT_QUEUE_LINES = (
    f"({_QUEUE_LINES[0]}",
    *_QUEUE_LINES[1:-1],
    f"{_QUEUE_LINES[-1]})",
    "&& (needs.post.outputs.dispatch != '' || needs.post.outputs.cleanup != '')",
)
_WAKES_QUEUE = " ".join(_QUEUE_LINES)
_CONTRACT_IF_QUEUE = " ".join(_CONTRACT_QUEUE_LINES)
_WAKES_BY_SHAPE = {_BESIDE: _WAKES_BESIDE, _QUEUE: _WAKES_QUEUE}
#: The clause that would take a run of a ``pull_request_target`` workflow: no workflow runs on that
#: event (#3908), so no condition names it.
_TARGET_RUN = "|| github.event.workflow_run.event == 'pull_request_target'"


#: What the beside shape's concurrency group keys a dispatch on: the head it names, empty for any
#: other event (a context the concurrency row models, unlike ``inputs``).
_HEAD_KEY = "${{ github.event.inputs.head }}"
#: And what it keys a run's start apart from its completion on: GitHub sends a run's
#: ``in_progress`` again as its jobs start, and a completion that shared their group would queue
#: behind them.
_ACTION_KEY = "${{ github.event.action }}"
_CONTRACT_IF_BY_SHAPE = {
    _BESIDE: _CONTRACT_IF_BESIDE,
    _QUEUE: _CONTRACT_IF_QUEUE,
}
#: The in-place rerun (#5809 piece 3): a queue commit whose red is flake-class, with time left in
#: main's merge queue's check limit, reruns its failed jobs. A poster has both its jobs or neither.
#: ``rerun`` runs after ``post`` only when post names a run, under post's own condition, and holds
#: the job token's ``actions: write``, the one grant besides ``contract``'s. It deploys nowhere,
#: reads no secret, runs no action but main's checkout and runs one step: the controller's
#: ``rerun``, on the job's own token, which reruns the run's failed jobs, waits for that attempt
#: and wakes the poster for its queue commit. ``rerun-fallback`` judges the queue commit again with
#: its rerun ended (``decide --rerun-ended``) whenever ``rerun`` did not succeed (refused, past its
#: deadline, timed out or cancelled), with post's reads and post's one token step, so a rerun's
#: in-progress verdict never stands for good.
_RERUN_JOBS = frozenset({"rerun", "rerun-fallback"})
_RERUN_OUTPUT = "${{ steps.decide.outputs.rerun }}"
_RERUN_NAMED = "&& needs.post.outputs.rerun != ''"
_RERUN_LINES = (f"({_QUEUE_LINES[0]}", *_QUEUE_LINES[1:-1], f"{_QUEUE_LINES[-1]})", _RERUN_NAMED)
_RERUN_IF = " ".join(_RERUN_LINES)
_FALLBACK_LINES = (
    "always()",
    f"&& ({_QUEUE_LINES[0]}",
    *_QUEUE_LINES[1:-1],
    f"{_QUEUE_LINES[-1]})",
    _RERUN_NAMED,
    "&& needs.rerun.result != 'success'",
)
_FALLBACK_IF = " ".join(_FALLBACK_LINES)
#: Main's merge queue's check limit (its rule's ``check_response_timeout_minutes``): a required
#: check with no conclusion by then fails, and the queue drops the entry.
_QUEUE_CHECK_MINUTES = 120
#: The rerun job's bound, and the fallback's after it: both inside that limit, so a rerun that
#: overruns ends red through the fallback, never through the queue's timeout.
_RERUN_MINUTES = 100
_FALLBACK_MINUTES = 10
_RERUN_GRANTS = {"contents": "read", "actions": "write"}
_POST_GRANTS = {"contents": "read", "actions": "read", "security-events": "read"}
_RERUN_RUN = "merge-controller rerun "
#: The rerun step's whole environment: the job's own token, post's run, and the App whose note
#: it reads.
_RERUN_ENV_ = {
    "GH_TOKEN": "${{ github.token }}",
    "RERUN": "${{ needs.post.outputs.rerun }}",
    "APP_ID": "${{ vars.CONTROLLER_APP_ID }}",
}
_RERUN_ENDED = '--rerun-ended "$RERUN"'
#: The closed lists of lines each step that holds a write runs (CORE F3), as test_ci_contract holds
#: ``results``' (``_TIER_LINES``): each line stripped, blank and comment lines aside, in order.
#: A line added, swapped or reordered is a script of its own on the step's token.
_SCRIPT = "set -euo pipefail"
_RERUN_SCRIPT = (
    _SCRIPT,
    'python3 -m tools.ci merge-controller rerun --repo "$GITHUB_REPOSITORY" \\',
    '--run-id "$RERUN" --controller-app-id "$APP_ID"',
)
_POST_SCRIPT = (
    _SCRIPT,
    'python3 -m tools.ci merge-controller post --repo "$GITHUB_REPOSITORY" \\',
    '--body "$RUNNER_TEMP/merge-verdict.json"',
)
_FALLBACK_DECIDE_SCRIPT = (
    _SCRIPT,
    'python3 -m tools.ci merge-controller decide --repo "$GITHUB_REPOSITORY" \\',
    '--rerun-ended "$RERUN" --controller-app-id "$APP_ID" \\',
    '--controller-revision "$GITHUB_SHA" --out "$RUNNER_TEMP/merge-verdict.json"',
)
_DECIDE_SCRIPT = (
    _SCRIPT,
    'if [ -n "$RUN_ID" ] && [ "$ACTION" = in_progress ]; then',
    'which=(--start "$RUN_ID")',
    'elif [ -n "$RUN_ID" ]; then',
    'which=(--run-id "$RUN_ID")',
    'elif [ -n "$WAKE_HEAD" ]; then',
    'if [[ ! "$WAKE_HEAD" =~ ^[0-9a-f]{40}$ ]]; then',
    'echo "::notice title=no head::the dispatch names no full commit id"',
    'echo "post=false" >> "$GITHUB_OUTPUT"',
    "exit 0",
    "fi",
    'which=(--head "$WAKE_HEAD")',
    'elif [ -n "$CHECK_RUN_ID" ]; then',
    'which=(--check-run-id "$CHECK_RUN_ID")',
    "else",
    "which=(--waiting)",
    "fi",
    'python3 -m tools.ci merge-controller decide --repo "$GITHUB_REPOSITORY" "${which[@]}" \\',
    '--controller-app-id "$APP_ID" \\',
    '--controller-revision "$GITHUB_SHA" --out "$RUNNER_TEMP/merge-verdict.json"',
)
#: The contract job's step on the App's token: delete the branches post names, then point the
#: head's contract branch at main's tip.
_TREE_SCRIPT = (
    _SCRIPT,
    'if [ -n "$CLEANUP" ]; then',
    "gone=()",
    'for branch in $CLEANUP; do gone+=(--branch "$branch"); done',
    'python3 -m tools.ci merge-controller cleanup --repo "$GITHUB_REPOSITORY" "${gone[@]}"',
    "fi",
    'if [ -n "$HEAD_SHA" ]; then',
    'python3 -m tools.ci merge-controller contract-branch --repo "$GITHUB_REPOSITORY" \\',
    '--head "$HEAD_SHA" --base "$BASE"',
    "fi",
)
#: Its dispatch, on the job's own actions: write.
_DISPATCH_SCRIPT = (
    _SCRIPT,
    'python3 -m tools.ci merge-controller dispatch --repo "$GITHUB_REPOSITORY"'
    ' --branch "$BRANCH"',
)


def _script_lines(step: dict) -> tuple[str, ...]:
    """A step's script as its closed list reads it: each line stripped, blank and comment lines
    aside."""
    lines = (line.strip() for line in str(step.get("run", "")).splitlines())
    return tuple(line for line in lines if line and not line.startswith("#"))


#: Every option the fallback's decision passes: its run, and what post's passes beside its event.
_FALLBACK_OPTIONS = {
    "--repo",
    "--rerun-ended",
    "--controller-app-id",
    "--controller-revision",
    "--out",
}
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
#: The Runner identity step every job opens with: its script, as the tree spells it.
_IDENTITY_RUN = 'echo "runner image ${ImageOS:?not a GitHub-hosted runner image}'
#: The runner identity step's whole script, as every job of the tree runs it.
_IDENTITY_SCRIPT = (
    _IDENTITY_RUN + ' ${ImageVersion:?no image version} on ${RUNNER_OS} ${RUNNER_ARCH}"'
)
_OWN_TOKEN = "${{ github.token }}"
_APP_ID = "${{ vars.CONTROLLER_APP_ID }}"
_NAMED_RERUN = "${{ needs.post.outputs.rerun }}"


#: Each step of the poster as a whole (CORE F4): its keys and each value but its name, a ``uses``
#: by its action (its pin is the action pins gate's) and a ``run`` by its closed lines. A key or an
#: entry past its own is code of its own on the step's token: ``shell`` runs a command before the
#: script, a ``BASH_ENV`` entry in ``env`` is expanded (command substitution included) before it,
#: and an action's ``env`` reaches node (``NODE_OPTIONS``) before the action.
_IDENTITY_ = {"name": "Runner identity", "run": (_IDENTITY_SCRIPT,)}
_CHECKOUT_ = {"uses": "actions/checkout@", "with": {"persist-credentials": False}}
_DECIDE_ = {
    "id": "decide",
    "env": {
        "GH_TOKEN": _OWN_TOKEN,
        "RUN_ID": "${{ github.event.workflow_run.id }}",
        "CHECK_RUN_ID": "${{ github.event.check_run.id }}",
        "ACTION": _ACTION_KEY,
        "WAKE_HEAD": "${{ inputs.head }}",
        "APP_ID": _APP_ID,
    },
    "run": _DECIDE_SCRIPT,
}
_TOKEN_ = {"id": "controller", "if": _POSTING, "uses": _APP_ACTION, "with": _TOKEN_ASKS}
_POSTS_ = {"if": _POSTING, "env": {"GH_TOKEN": _APP_TOKEN}, "run": _POST_SCRIPT}
_CONTRACT_TOKEN_ = {"id": "controller", "uses": _APP_ACTION, "with": _CONTRACT_ASKS}
_TREE_ = {
    "id": "tree",
    "env": {
        "GH_TOKEN": _APP_TOKEN,
        "HEAD_SHA": "${{ needs.post.outputs.dispatch }}",
        "BASE": "${{ needs.post.outputs.base }}",
        "CLEANUP": "${{ needs.post.outputs.cleanup }}",
    },
    "run": _TREE_SCRIPT,
}
_DISPATCH_ = {
    "if": "steps.tree.outputs.branch != ''",
    "env": {"GH_TOKEN": _OWN_TOKEN, "BRANCH": "${{ steps.tree.outputs.branch }}"},
    "run": _DISPATCH_SCRIPT,
}
_RERUNS_ = {"env": _RERUN_ENV_, "run": _RERUN_SCRIPT}
_FALLBACK_DECIDE_ = {
    "id": "decide",
    "env": {"GH_TOKEN": _OWN_TOKEN, "RERUN": _NAMED_RERUN, "APP_ID": _APP_ID},
    "run": _FALLBACK_DECIDE_SCRIPT,
}
_OPENING = (("runner identity", _IDENTITY_), ("checkout", _CHECKOUT_))
_POSTING_STEPS = (("token step", _TOKEN_), ("posting step", _POSTS_))
#: Each job's own keys, beside ``name`` and ``steps``, and its steps, each its fixture, in order.
#: No job sets ``env``, ``defaults``, ``container`` or ``services``, and neither does the
#: workflow (``_WORKFLOW_KEYS``): each reaches every step under it.
_JOBS = {
    "post": (
        {"if", "runs-on", "timeout-minutes", "environment", "permissions", "outputs"},
        (*_OPENING, ("decision", _DECIDE_), *_POSTING_STEPS),
    ),
    "contract": (
        {"needs", "if", "runs-on", "timeout-minutes", "environment", "permissions"},
        (
            *_OPENING,
            ("token step", _CONTRACT_TOKEN_),
            ("branch step on the App's token", _TREE_),
            ("dispatch", _DISPATCH_),
        ),
    ),
    "rerun": (
        {"needs", "if", "runs-on", "timeout-minutes", "permissions"},
        (*_OPENING, ("rerun step", _RERUNS_)),
    ),
    "rerun-fallback": (
        {"needs", "if", "runs-on", "timeout-minutes", "environment", "permissions"},
        (*_OPENING, ("decision", _FALLBACK_DECIDE_), *_POSTING_STEPS),
    ),
}
_WORKFLOW_KEYS = {"name", "on", "concurrency", "permissions", "jobs"}
#: The runner every job of the poster and contract.yml's wake runs on: GitHub-hosted, as the file
#: names it (CORE N1 on #6312). A self-hosted runner's own hooks run beside each step, the token
#: steps among them, and a label list or a runner group picks a runner the file does not name.
_RUNNER = "ubuntu-24.04"


def _keys(data: dict) -> set[str]:
    """A workflow's own keys, ``on`` as the file spells it (YAML reads a bare ``on`` as true)."""
    return {"on" if key is True else str(key) for key in data}


def _closed(step: object, want: dict[str, Any]) -> bool:
    """*step* is *want* as a whole: the same keys, a name aside, and each one's value."""
    if not isinstance(step, dict) or not set(want) <= set(step) <= {*want, "name"}:
        return False
    if not isinstance(step.get("name", ""), str):
        return False
    for key, value in want.items():
        held = step[key]
        if key == "uses":
            same = isinstance(held, str) and held.startswith(value)
        elif key == "run":
            same = isinstance(held, str) and _script_lines(step) == value
        else:
            same = held == value
        if not same:
            return False
    return True


def _closed_job_problems(
    where: str, job: dict, keys: set[str], steps: tuple[tuple[str, dict[str, Any]], ...]
) -> list[str]:
    """*job* runs on ``_RUNNER``, sets no key past *keys* and runs exactly *steps*, each its fixture
    as a whole."""
    found: list[str] = []
    if job.get("runs-on") != _RUNNER:
        found.append(
            f"{where} runs on {job.get('runs-on')!r}, not a GitHub-hosted {_RUNNER} runner"
        )
    extra = sorted(map(str, set(job) - keys - {"name", "steps"}))
    if extra:
        found.append(
            f"{where} sets {extra}: a job's `env`, `defaults` or `container` reaches each of its"
            " steps"
        )
    held = job.get("steps")
    if not isinstance(held, list) or len(held) != len(steps):
        found.append(f"{where} runs other than exactly its steps: {', '.join(n for n, _ in steps)}")
        return found
    for step, (label, want) in zip(held, steps):
        if not _closed(step, want):
            found.append(
                f"{where}'s {label} is not its closed step as a whole: no key, condition,"
                " environment entry, input or line past its own"
            )
    return found


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


def _contract_problems(job: dict, shape: str = _QUEUE) -> list[str]:
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


def _bounded(job: dict, minutes: int) -> bool:
    bound = job.get("timeout-minutes")
    return isinstance(bound, int) and not isinstance(bound, bool) and 0 < bound <= minutes


def _posting_problems(where: str, steps: list[dict]) -> list[str]:
    """The App's token, as the post job holds it: minted in one step with ``checks: write`` alone,
    only with a verdict to post, and reaching only the step that posts it."""
    found: list[str] = []
    tokens = [s for s in steps if str(s.get("uses", "")).startswith(_APP_ACTION)]
    if len(tokens) != 1 or tokens[0].get("with") != _TOKEN_ASKS:
        found.append(
            f"in {where}, the controller's token is asked for as {[t.get('with') for t in tokens]}"
        )
    elif tokens[0].get("if") != _POSTING:
        found.append(f"in {where}, the controller's token is minted with no verdict to post")
    if [s for s in steps if "secrets." in str(s)] != tokens:
        found.append(f"in {where}, a step other than the token step reads a secret")
    holders = [s for s in steps if _APP_TOKEN in str(s)]
    if len(holders) != 1 or holders[0].get("if") != _POSTING:
        found.append(f"in {where}, the App's token reaches a step other than the one that posts")
    elif "merge-controller post" not in str(holders[0].get("run")):
        found.append(f"in {where}, the step holding the App's token does not post the verdict")
    elif _script_lines(holders[0]) != _POST_SCRIPT:
        found.append(f"in {where}, the step holding the App's token runs more than the post")
    return found


def _rerun_problems(job: dict) -> list[str]:
    found: list[str] = []
    if job.get("needs") != "post" or job.get("if") != _RERUN_IF:
        found.append(
            "the rerun job runs other than after post, on post's events, for a run it names"
        )
    if "environment" in job:
        found.append(f"the rerun job deploys to {job.get('environment')!r}: it holds no secret")
    if job.get("permissions") != _RERUN_GRANTS:
        found.append(f"the rerun job holds {job.get('permissions')!r}")
    if not _bounded(job, _RERUN_MINUTES):
        found.append(
            f"the rerun job's bound is {job.get('timeout-minutes')!r}, not at most"
            f" {_RERUN_MINUTES} minutes, inside the queue's {_QUEUE_CHECK_MINUTES}-minute limit"
        )
    steps = _steps(job)
    found += _checkout_problems("the rerun job", steps)
    if any("secrets." in str(s) for s in steps):
        found.append("a step of the rerun job reads a secret")
    if any(not str(s["uses"]).startswith("actions/checkout@") for s in steps if "uses" in s):
        found.append("the rerun job runs an action other than main's checkout")
    runs = [s for s in steps if "run" in s and not _is_identity(s)]
    if (
        len(runs) != 1
        or _RERUN_RUN not in str(runs[0].get("run"))
        or _script_lines(runs[0]) != _RERUN_SCRIPT
        or set(runs[0]) != {"name", "env", "run"}
        or runs[0].get("env") != _RERUN_ENV_
    ):
        found.append(
            "the rerun job runs other than the controller's rerun alone, of the run post names,"
            " on the job's own token"
        )
    # Exactly the three steps (CORE F1): what else it ran would run on the job's actions: write.
    if (
        len(steps) != 3
        or not _is_identity(steps[0])
        or not _is_checkout(steps[1])
        or steps[2] not in runs
    ):
        found.append(
            "the rerun job's steps are not exactly the runner identity, main's checkout and the"
            " controller's rerun"
        )
    return found


def _is_identity(step: dict) -> bool:
    """The runner identity step, exactly as the tree spells it: its name and its one script."""
    return set(step) == {"name", "run"} and step == {
        "name": "Runner identity",
        "run": _IDENTITY_SCRIPT,
    }


def _is_checkout(step: dict) -> bool:
    return (
        set(step) <= {"name", "uses", "with"}
        and str(step.get("uses", "")).startswith("actions/checkout@")
        and step.get("with") == {"persist-credentials": False}
    )


def _step_problems(name: str, job: dict) -> list[str]:
    """What every job of the poster holds to (CORE F1, F2, N1): a step that names itself the runner
    identity is the tree's one script, alone; no step's script pastes an expression (``${{``),
    so nothing the event names (a pull request's title, a branch) ever becomes shell; and its
    actions are main's checkout and the App's token alone, the rerun job's main's checkout alone."""
    found: list[str] = []
    for step in _steps(job):
        script = str(step.get("run", ""))
        if (step.get("name") == "Runner identity" or script.startswith(_IDENTITY_RUN)) and (
            not _is_identity(step)
        ):
            found.append(f"the {name} job's runner identity step is not the tree's one script")
        if "${{" in script:
            found.append(f"a step of the {name} job pastes an expression into its script")
        uses = str(step.get("uses", ""))
        if uses and not uses.startswith(("actions/checkout@", _APP_ACTION)):
            found.append(
                f"the {name} job runs {uses.split('@')[0]}, not main's checkout or the App's token"
            )
    if sum(_is_identity(s) for s in _steps(job)) > 1:
        found.append(f"the {name} job has more than one runner identity step")
    return found


def _fallback_problems(job: dict) -> list[str]:
    found: list[str] = []
    if job.get("needs") != ["post", "rerun"] or job.get("if") != _FALLBACK_IF:
        found.append(
            "the rerun fallback runs other than after post and rerun, whenever rerun did not"
            " succeed"
        )
    if job.get("environment") != "ci-controller":
        found.append(f"the rerun fallback deploys to {job.get('environment')!r}")
    if job.get("permissions") != _POST_GRANTS:
        found.append(f"the rerun fallback holds {job.get('permissions')!r}")
    if not _bounded(job, _FALLBACK_MINUTES):
        found.append(f"the rerun fallback's bound is {job.get('timeout-minutes')!r}")
    steps = _steps(job)
    found += _checkout_problems("the rerun fallback", steps)
    found += _posting_problems("the rerun fallback", steps)
    decide = next((s for s in steps if s.get("id") == "decide"), None)
    run = str((decide or {}).get("run", ""))
    env = (decide or {}).get("env") or {}
    if (
        "merge-controller decide" not in run
        or _script_lines(decide or {}) != _FALLBACK_DECIDE_SCRIPT
        or set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", run)) != _FALLBACK_OPTIONS
        or _RERUN_ENDED not in run
        or env.get("GH_TOKEN") != "${{ github.token }}"
        or env.get("RERUN") != "${{ needs.post.outputs.rerun }}"
    ):
        found.append(
            "the rerun fallback decides other than the queue commit of the run post names, with"
            " its rerun ended, on the job's own token"
        )
    return found


def _problems(text: str) -> list[str]:
    found: list[str] = []
    if "workflow_run.head" in _IF_BLOCK.sub("", text):
        found.append("it names the candidate's head, branch or repository")
    data = yaml.safe_load(text)
    on = data.get("on", data.get(True))
    if _shape(on) != _BESIDE:
        found.append(
            f"runs on {on!r}, not a completed and a started Merge run with its contract's wake"
        )
    if data.get("permissions") != {"contents": "read"}:
        found.append(
            f"the workflow holds {data.get('permissions')!r}: each grant past `contents: read` is"
            " a job's own"
        )
    extra = sorted(_keys(data) - _WORKFLOW_KEYS)
    if extra:
        found.append(
            f"the workflow sets {extra}: past its name, triggers, concurrency, grant and jobs, a"
            " key such as `env` or `defaults` reaches every step"
        )
    jobs = data.get("jobs") or {}
    if set(jobs) not in ({"post", "contract"}, {"post", "contract", *_RERUN_JOBS}):
        return [*found, f"has jobs {sorted(jobs)}"]
    for name, each in jobs.items():
        found += _step_problems(name, each)
        found += _closed_job_problems(f"the {name} job", each, *_JOBS[name])
    # The beside shape's triggers carry the queue shape's condition (0.4.0 A1): main's merge queue
    # is judged through this poster, so a condition that takes no queue commit's run is not this
    # file's, and neither is the after shape's.
    shape = _QUEUE
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
    if job.get("permissions") != _POST_GRANTS:
        found.append(f"the job holds {job.get('permissions')!r}")
    steps = _steps(job)
    found += _checkout_problems("the post job", steps)
    found += _posting_problems("the post job", steps)
    if _RERUN_JOBS <= set(jobs):
        found += _rerun_problems(jobs["rerun"]) + _fallback_problems(jobs["rerun-fallback"])
        if (job.get("outputs") or {}).get("rerun") != _RERUN_OUTPUT:
            found.append(
                "the post job names no run to rerun to the rerun jobs (its `rerun` output)"
            )
    decide = next((s for s in steps if s.get("id") == "decide"), None)
    if decide is None or "merge-controller decide" not in str(decide.get("run")):
        found.append("no step decides the verdict")
    elif (decide.get("env") or {}).get("GH_TOKEN") != "${{ github.token }}":
        found.append("the decision reads with a token other than the job's own")
    elif shape in (_BESIDE, _QUEUE):
        found += _beside_decide_problems(decide)
        if _script_lines(decide) != _DECIDE_SCRIPT:
            found.append("the decision runs other than its closed list of lines")
    if shape in (_BESIDE, _QUEUE):
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


#: The runs the poster wakes on: Merge, the two that judge a head again, and each gate's
#: (tools/ci/workflows.py's GATES), whose completion judges its head again. The dependency review
#: and the sign-off check are the poster's own reads (#3908), so no run of either wakes it.
_WAKES = [
    workflows.NAMES[workflows.MERGE],
    "CodeQL",
    "Contract",
    *(gate.name for gate in workflows.GATES),
]


def _shape(on: object) -> str | None:
    """``beside`` when the poster's triggers are the beside shape's, else None."""
    both = {"check_run": {"types": ["completed"]}, "schedule": [{"cron": "*/10 * * * *"}]}
    if not isinstance(on, dict) or set(on) != {"workflow_run", "workflow_dispatch", *both}:
        return None
    dispatch = on["workflow_dispatch"]
    inputs = dispatch.get("inputs") if isinstance(dispatch, dict) else None
    head = inputs.get("head") if isinstance(inputs, dict) else None
    if (
        all(on[k] == v for k, v in both.items())
        and on["workflow_run"] == {"workflows": _WAKES, "types": ["in_progress", "completed"]}
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
#: CORE F4's plants: a forged verdict on the App's token, and the env block of each step that
#: holds it.
_FORGE = "gh api repos/$GITHUB_REPOSITORY/check-runs -f name=merge-verdict -f conclusion=success"
_POSTED_ENV = f"        env:\n          GH_TOKEN: {_APP_TOKEN}\n"
_NODE = '          NODE_OPTIONS: "--import=data:text/javascript,console.log(1)"\n'


#: The poster's wake, which workflows' runs it judges, as the tree spells it, in either shape (its
#: types are ``[completed]``, or ``[in_progress, completed]`` beside) and its list in either
#: spelling (``[...]``, or one name a line): a plant anchored here reads whichever the tree has.
_WAKE = "<the poster's workflow_run wake>"
_WAKE_LIST = "<the poster's workflows list>"
_WAKE_LINES = re.compile(
    r"    (workflows:(?: \[[^\]\n]*\]|(?:\n      - [^\n]+)+))\n    types: \[[^\]\n]*\]\n"
)


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
            _WAKE_LIST,
            'workflows: ["Merge", "Dependency review", "CodeQL", "Contract", "DCO"]',
            "runs on",
        ),
        (
            _WAKE_LIST,
            'workflows: ["Merge", "Dependency review", "CodeQL", "Contract", "DCO", "Action pins",'
            ' "Release image tooling"]',
            "runs on",
        ),
        (
            _WAKE_LIST,
            'workflows: ["Merge", "Dependency review", "CodeQL", "Contract", "DCO", "Nix",'
            ' "Action pins"]',
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
        # CORE F3 on the tree as it is: the posting step, on the App's token, and the decision
        # each run their closed list of lines alone.
        (
            '            --body "$RUNNER_TEMP/merge-verdict.json"\n',
            '            --body "$RUNNER_TEMP/merge-verdict.json"\n'
            '          gh api "repos/$GITHUB_REPOSITORY/check-runs" -f name=merge-verdict'
            " -f conclusion=success\n",
            "in the post job, the step holding the App's token runs more than the post",
        ),
        (
            "          set -euo pipefail\n          # A Merge run's completion",
            '          set -euo pipefail\n          eval "$TITLE"\n'
            "          # A Merge run's completion",
            "the decision runs other than its closed list of lines",
        ),
        # CORE F2 on the tree as it is: the pull request's title pasted into post's decision.
        (
            "          python3 -m tools.ci merge-controller decide",
            '          echo "${{ github.event.workflow_run.display_title }}"\n'
            "          python3 -m tools.ci merge-controller decide",
            "a step of the post job pastes an expression into its script",
        ),
        (
            "      - uses: actions/checkout@",
            "      - uses: someone/action@0123456789012345678901234567890123456789\n"
            "      - uses: actions/checkout@",
            "the post job runs someone/action",
        ),
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
        # CORE F4: code beside a closed script, through a step's other keys or from above the
        # step. Each closed step is its fixture as a whole, and neither the workflow nor a job
        # sets `env` or `defaults`.
        pytest.param(
            _POSTED_ENV,
            _POSTED_ENV + f"          BASH_ENV: '$({_FORGE})'\n",
            "the post job's posting step is not its closed step as a whole",
            id="P14 BASH_ENV on post's posting step",
        ),
        pytest.param(
            _POSTED_ENV,
            f"        shell: bash -c '{_FORGE}; bash {{0}}'\n" + _POSTED_ENV,
            "the post job's posting step is not its closed step as a whole",
            id="P15 a shell on post's posting step",
        ),
        pytest.param(
            _DECIDE_TOKEN,
            _DECIDE_TOKEN
            + "          BASH_ENV: '$(echo forged > $RUNNER_TEMP/merge-verdict.json)'\n",
            "the post job's decision is not its closed step as a whole",
            id="P18 BASH_ENV on post's decision",
        ),
        pytest.param(
            "\njobs:\n",
            f"\nenv:\n  BASH_ENV: '$({_FORGE})'\n\njobs:\n",
            "the workflow sets ['env']",
            id="P19 a workflow-level env",
        ),
        pytest.param(
            "\njobs:\n",
            f"\ndefaults:\n  run:\n    shell: bash -c '{_FORGE}; bash {{0}}'\n\njobs:\n",
            "the workflow sets ['defaults']",
            id="P20 a workflow-level shell",
        ),
        pytest.param(
            "\n  post:\n",
            f"\n  post:\n    env:\n      BASH_ENV: '$({_FORGE})'\n",
            "the post job sets ['env']",
            id="P21 a job-level env on post",
        ),
        pytest.param(
            '              --head "$HEAD_SHA" --base "$BASE"\n',
            '              --head "$HEAD_SHA" --base "$BASE"\n'
            '          gh api "repos/$GITHUB_REPOSITORY/git/refs/heads/main" -X PATCH -f sha=0\n',
            "the contract job's branch step on the App's token is not its closed step as a whole",
            id="P22 a line on contract's App-token step",
        ),
        pytest.param(
            '--branch "$BRANCH"\n',
            '--branch "$BRANCH"\n          gh workflow run release.yml --ref main\n',
            "the contract job's dispatch is not its closed step as a whole",
            id="a line on contract's dispatch",
        ),
        pytest.param(
            "          permission-checks: write\n",
            "          permission-checks: write\n        env:\n" + _NODE,
            "the post job's token step is not its closed step as a whole",
            id="node options on post's token step",
        ),
        pytest.param(
            _POST_CHECKOUT,
            _POST_CHECKOUT + "        env:\n" + _NODE,
            "the post job's checkout is not its closed step as a whole",
            id="node options on post's checkout",
        ),
        pytest.param(
            "    timeout-minutes: 10\n    environment: ci-controller\n",
            "    timeout-minutes: 10\n    container: someone/image:1\n"
            "    environment: ci-controller\n",
            "the post job sets ['container']",
            id="a container under post",
        ),
        # CORE N1 on #6312: each job runs on a GitHub-hosted runner, as the file names it.
        pytest.param(
            "    runs-on: ubuntu-24.04\n",
            "    runs-on: [self-hosted]\n",
            "the post job runs on ['self-hosted']",
            id="post on a self-hosted runner",
        ),
        pytest.param(
            "    runs-on: ubuntu-24.04\n    timeout-minutes: 10\n    environment: ci-controller\n"
            "    permissions:\n      contents: read   #",
            "    runs-on: [ubuntu-24.04, self-hosted]\n    timeout-minutes: 10\n"
            "    environment: ci-controller\n    permissions:\n      contents: read   #",
            "the contract job runs on ['ubuntu-24.04', 'self-hosted']",
            id="contract on a self-hosted runner by a label list",
        ),
    ],
)
def test_a_planted_edit_of_the_poster_fails(old: str, new: str, says: str) -> None:
    """Each plant in the first place its anchor stands: the post job's, which comes first, where
    the rerun fallback spells the same steps after it."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    old, new = _anchored(text, old, new)
    assert text.count(old) >= 1, f"the plant's anchor is gone: {old!r}"
    found = _problems(text.replace(old, new, 1))
    assert any(says in p for p in found), found


def _spelled(names: list[str], *, block: bool) -> str:
    if block:
        return "workflows:" + "".join(f"\n      - {name}" for name in names)
    return "workflows: [" + ", ".join(f'"{name}"' for name in names) + "]"


@pytest.mark.parametrize("block", [False, True], ids=["on one line", "one name a line"])
def test_the_wake_list_in_either_spelling_keeps_the_trust_shape(block: bool) -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    old, new = _anchored(text, _WAKE_LIST, _spelled(_WAKES, block=block))
    assert text.count(old) == 1, f"the wake's list is not once: {old!r}"
    assert _problems(text.replace(old, new)) == []


def test_a_wake_list_that_wakes_on_dco_again_fails() -> None:
    # dco.yml is the head's copy now (#3908): its run judges nothing, so it wakes nothing.
    text = _WORKFLOW.read_text(encoding="utf-8")
    old, new = _anchored(text, _WAKE_LIST, _spelled([*_WAKES, "DCO"], block=False))
    assert any("runs on" in p for p in _problems(text.replace(old, new)))


#: Each event the poster can be started by, and when its jobs take a runner: ``head``, a pull
#: request's wake, only with MERGE_VERDICT_LIVE 'true'; ``always``; ``never``. Each is a
#: completion (``completed``), a check run's (``check``), the schedule, a run's start
#: (``in_progress``) or a dispatch of the poster (``dispatch``).
_FORK = {"head_repository": {"full_name": "someone/project", "fork": True}, "pull_requests": []}
_EVENTS: tuple[tuple[str, str, dict[str, Any], str, str], ...] = (
    ("a pull request's Merge run", "workflow_run", {"event": "pull_request"}, "head", "completed"),
    # No workflow runs on pull_request_target (#3908): a run that claims it takes no runner.
    (
        "a pull_request_target run",
        "workflow_run",
        {"event": "pull_request_target"},
        "never",
        "completed",
    ),
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
        "a pull_request_target run starting",
        "workflow_run",
        {"event": "pull_request_target", "path": ".github/workflows/dependency-review.yml"},
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
        "a queue commit's Merge run",
        "workflow_run",
        {"event": "merge_group", "head_branch": "gh-readonly-queue/main/pr-7-ab"},
        "queue",
        "completed",
    ),
    (
        "a queue commit's Merge run starting",
        "workflow_run",
        {
            "event": "merge_group",
            "path": _MERGE_PATH,
            "head_branch": "gh-readonly-queue/main/pr-7-ab",
        },
        "queue",
        "in_progress",
    ),
    (
        "another workflow named Merge starting on a queue commit",
        "workflow_run",
        {"event": "merge_group", "path": ".github/workflows/merge-copy.yml"},
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
    _BESIDE: frozenset({"completed", "check", "schedule", "in_progress", "dispatch"}),
    _QUEUE: frozenset({"completed", "check", "schedule", "in_progress", "dispatch"}),
}
#: MERGE_VERDICT_LIVE as the repository may hold it: before the flip, after it, and unset.
_LIVE = ("false", "true", None)


def _wanted(kind: str, live: str | None, shape: str = _QUEUE) -> bool:
    """Whether the shape's condition takes a runner for an event of this kind: a queue commit's
    run (``queue``) is taken as a pull request's head's is, and only in the queue shape."""
    queued = kind == "queue" and shape == _QUEUE
    return kind == "always" or ((kind == "head" or queued) and live == "true")


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


def _wake_problems(where: str, expr: str, shape: str = _QUEUE) -> list[str]:
    """Each event that starts the shape's runs, as the condition answers it, with
    MERGE_VERDICT_LIVE each way, against whether it can judge a head then."""
    return [
        f"{where} {'takes no runner for' if _wanted(e[3], live, shape) else 'takes a runner for'}"
        f" {e[0]} with MERGE_VERDICT_LIVE {live!r}"
        for e in _EVENTS
        if e[4] in _DELIVERS[shape]
        for live in _LIVE
        if _answers(expr, e, live) is not _wanted(e[3], live, shape)
    ]


_SHAPED_EVENTS = [
    (shape, e) for shape in (_BESIDE, _QUEUE) for e in _EVENTS if e[4] in _DELIVERS[shape]
]


@pytest.mark.parametrize("live", _LIVE, ids=lambda v: f"live={v}")
@pytest.mark.parametrize(
    "shape, event", _SHAPED_EVENTS, ids=[f"{shape}: {e[0]}" for shape, e in _SHAPED_EVENTS]
)
def test_the_wake_condition_takes_a_runner_only_for_what_can_judge_a_head(
    shape: str, event: tuple[str, str, dict[str, Any], str, str], live: str | None
) -> None:
    assert _answers(_WAKES_BY_SHAPE[shape], event, live) is _wanted(event[3], live, shape), event[0]


def test_the_conditions_read_only_the_fields_they_name() -> None:
    """What either condition reads of an event: its name, its action, its ref, the run's event,
    branch and path, the check's App and name, and the variable. Nothing a fork's author names but
    the branch, which only a dispatch's and CodeQL's clauses read."""
    for expr in (_WAKES_BESIDE, _WAKES_QUEUE):
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
        for expr in (_WAKES_BESIDE, _WAKES_QUEUE):
            assert _answers(expr, fork, live) is _answers(expr, plain, live)


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
            "      || github.event.workflow_run.event == 'merge_group'\n",
            "      || github.event.workflow_run.event == 'merge_group'\n"
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


def test_a_condition_that_takes_a_pull_request_target_run_again_fails() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    line = (
        "      && github.event.action == 'completed'\n"
        "      && (github.event.workflow_run.event == 'pull_request'\n"
    )
    assert text.count(line) == len(
        _IF_BLOCK.findall(text)
    ), f"the anchor is not in every condition: {line!r}"
    planted = text.replace(line, line + f"      {_TARGET_RUN}\n")
    found = _problems(planted)
    assert any("takes a runner for a pull_request_target run" in p for p in found), found
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


def test_the_after_shape_s_triggers_fail() -> None:
    """The shape that started a head's contract at its run's completion, with no start and no wake:
    it takes no queue commit's start, so it is not this file's."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    dispatch = (
        "  workflow_dispatch:\n    inputs:\n      head:\n"
        "        description: the head whose contract run reported (a full commit id)\n"
        "        required: true\n"
    )
    assert text.count(dispatch) == 1 and text.count("    types: [in_progress, completed]\n") == 1
    after = text.replace(dispatch, "", 1).replace(
        "    types: [in_progress, completed]\n", "    types: [completed]\n", 1
    )
    found = _problems(after)
    assert any("runs on" in p for p in found), found


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
_WAKE_DISPATCH = (
    'gh workflow run merge-verdict.yml --repo "$GITHUB_REPOSITORY" --ref main -f head="$HEAD_SHA"'
)
#: The wake's one step, its closed lines (CORE F4), on the job's actions: write.
_WAKE_SCRIPT = (
    _SCRIPT,
    f"if ! printf '%s' \"$HEAD_SHA\" | {_WAKE_CHECKS[0]}; then",
    "echo \"::error title=no head::'$HEAD_SHA' is not a full commit id\"",
    "exit 1",
    "fi",
    _WAKE_CHECKS[1],
    _WAKE_DISPATCH,
)
_WAKE_KEYS = {"needs", "if", "runs-on", "timeout-minutes", "permissions"}
_WAKE_STEPS = (
    ("runner identity", _IDENTITY_),
    ("dispatch step", {"env": _WAKE_ENV, "run": _WAKE_SCRIPT}),
)
#: contract.yml's own keys, and its one workflow-wide environment entry, which uv reads: a
#: workflow's `env` or `defaults` would reach the wake's step too.
_CONTRACT_WORKFLOW_KEYS = {"name", "run-name", "on", "permissions", "env", "concurrency", "jobs"}
_CONTRACT_ENV = {"UV_PYTHON_PREFERENCE": "only-managed"}


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
    return found + _closed_job_problems("contract.yml's wake", job, _WAKE_KEYS, _WAKE_STEPS)


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
    extra = sorted(_keys(data) - _CONTRACT_WORKFLOW_KEYS)
    if extra:
        found.append(f"contract.yml sets {extra}: a workflow's `defaults` reaches every step")
    if data.get("env") != _CONTRACT_ENV:
        found.append(
            f"contract.yml's workflow `env` is {data.get('env')!r}, not {_CONTRACT_ENV!r}: each"
            " entry reaches every step, the wake's on actions: write among them"
        )
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
        # CORE F4's class on the wake, which holds actions: write: its one step as a whole, and no
        # env or defaults from above it.
        (
            "\njobs:\n",
            "\ndefaults:\n  run:\n    shell: sh\n\njobs:\n",
            "contract.yml sets ['defaults']",
        ),
        (
            "  UV_PYTHON_PREFERENCE: only-managed\n",
            "  UV_PYTHON_PREFERENCE: only-managed\n  BASH_ENV: '$(gh run cancel 1)'\n",
            "contract.yml's workflow `env` is",
        ),
        (
            "\n  wake:\n",
            "\n  wake:\n    env:\n      BASH_ENV: '$(gh run cancel 1)'\n",
            "contract.yml's wake sets ['env']",
        ),
        (
            ' -f head="$HEAD_SHA"\n',
            ' -f head="$HEAD_SHA"\n          curl -X POST -H "Authorization: token $GH_TOKEN"'
            ' "https://api.github.com/repos/$GITHUB_REPOSITORY/actions/workflows/x.yml/'
            'dispatches"\n',
            "contract.yml's wake's dispatch step is not its closed step as a whole",
        ),
        (
            "      - name: judge the head again\n",
            "      - name: judge the head again\n"
            "        shell: bash -c 'gh run cancel 1; bash {0}'\n",
            "contract.yml's wake's dispatch step is not its closed step as a whole",
        ),
        (
            "    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n",
            "    runs-on: [self-hosted, linux]\n    timeout-minutes: 5\n",
            "contract.yml's wake runs on ['self-hosted', 'linux']",
        ),
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
    "        run: |\n" + "".join(f"          {line}\n" for line in _WAKE_SCRIPT)
)


def _beside_poster() -> str:
    """The tree's poster, which has the beside shape's triggers (and the queue shape's
    condition)."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert _shape(data.get("on", data.get(True))) == _BESIDE, "the poster's triggers are not beside"
    return text


def _beside_contract() -> str:
    text = _CONTRACT.read_text(encoding="utf-8")
    return text if "\n  wake:\n" in text else text.rstrip("\n") + "\n" + _WAKE_JOB


_CHECKOUT_STEP = (
    "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    "        with:\n          persist-credentials: false\n"
)
_IDENTITY_STEP = (
    "      - name: Runner identity\n"
    f"        run: {_IDENTITY_RUN} ${{ImageVersion:?no image version}} on ${{RUNNER_OS}}"
    ' ${RUNNER_ARCH}"\n'
)
_RERUN_STEP = "      - name: rerun the failed jobs in place, then wake the poster\n"
_POST_STEPS = (
    "      - name: the controller's token\n        id: controller\n"
    f"        if: {_POSTING}\n"
    "        uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510"
    " # v3.3.0\n"
    "        with:\n          app-id: ${{ vars.CONTROLLER_APP_ID }}\n"
    "          private-key: ${{ secrets.CONTROLLER_APP_KEY }}\n"
    "          permission-checks: write\n"
    f"      - name: post merge-verdict\n        if: {_POSTING}\n"
    f"        env:\n          GH_TOKEN: {_APP_TOKEN}\n"
    "        run: |\n          set -euo pipefail\n"
    '          python3 -m tools.ci merge-controller post --repo "$GITHUB_REPOSITORY" \\\n'
    '            --body "$RUNNER_TEMP/merge-verdict.json"\n'
)
#: The in-place rerun's two jobs, as the change that adds them to the tree spells them (#5809
#: piece 3): the rows below hold the tree to their shape before and after it does.
_RERUN_JOB = (
    "\n  rerun:\n    needs: post\n"
    + _folded(_RERUN_LINES)
    + f"    runs-on: ubuntu-24.04\n    timeout-minutes: {_RERUN_MINUTES}\n"
    "    permissions:\n      contents: read\n      actions: write\n"
    "    steps:\n"
    + _IDENTITY_STEP
    + _CHECKOUT_STEP
    + _RERUN_STEP
    + "        env:\n          GH_TOKEN: ${{ github.token }}\n"
    "          RERUN: ${{ needs.post.outputs.rerun }}\n"
    "          APP_ID: ${{ vars.CONTROLLER_APP_ID }}\n"
    "        run: |\n          set -euo pipefail\n"
    '          python3 -m tools.ci merge-controller rerun --repo "$GITHUB_REPOSITORY" \\\n'
    '            --run-id "$RERUN" --controller-app-id "$APP_ID"\n'
    "\n  rerun-fallback:\n    needs: [post, rerun]\n"
    + _folded(_FALLBACK_LINES)
    + f"    runs-on: ubuntu-24.04\n    timeout-minutes: {_FALLBACK_MINUTES}\n"
    "    environment: ci-controller\n"
    "    permissions:\n      contents: read\n      actions: read\n      security-events: read\n"
    "    steps:\n"
    + _IDENTITY_STEP
    + _CHECKOUT_STEP
    + "      - name: judge the queue commit with its rerun ended\n        id: decide\n"
    "        env:\n          GH_TOKEN: ${{ github.token }}\n"
    "          RERUN: ${{ needs.post.outputs.rerun }}\n"
    "          APP_ID: ${{ vars.CONTROLLER_APP_ID }}\n"
    "        run: |\n          set -euo pipefail\n"
    '          python3 -m tools.ci merge-controller decide --repo "$GITHUB_REPOSITORY" \\\n'
    f'            {_RERUN_ENDED} --controller-app-id "$APP_ID" \\\n'
    '            --controller-revision "$GITHUB_SHA" --out "$RUNNER_TEMP/merge-verdict.json"\n'
    + _POST_STEPS
)


def _rerun_poster() -> str:
    """The tree's poster with the in-place rerun's jobs: the tree's own once it has them, else
    the tree's with them added and post naming its run to them."""
    text = _beside_poster()
    if "\n  rerun:\n" in text:
        return text
    named = "      cleanup: ${{ steps.decide.outputs.cleanup }}\n"
    assert text.count(named) == 1, "the post job's outputs moved"
    text = text.replace(named, f"{named}      rerun: {_RERUN_OUTPUT}\n", 1)
    return text.rstrip("\n") + "\n" + _RERUN_JOB


def test_the_in_place_rerun_keeps_its_trust_shape() -> None:
    assert _problems(_rerun_poster()) == []


def test_the_rerun_holds_actions_write_alone_and_ends_inside_the_queue_s_limit() -> None:
    """Of every job, only ``contract`` and ``rerun`` hold ``actions: write``, nothing
    workflow-wide does, and the rerun and its fallback both end inside the queue's check limit."""
    data = yaml.safe_load(_rerun_poster())
    writers = sorted(
        name
        for name, job in data["jobs"].items()
        if (job.get("permissions") or {}).get("actions") == "write"
    )
    assert writers == ["contract", "rerun"]
    assert data["permissions"] == {"contents": "read"}
    bounds = [data["jobs"][j]["timeout-minutes"] for j in ("rerun", "rerun-fallback")]
    assert bounds[0] <= _RERUN_MINUTES and bounds[1] <= _FALLBACK_MINUTES
    assert _RERUN_MINUTES + _FALLBACK_MINUTES < _QUEUE_CHECK_MINUTES


_FALLBACK_GRANTS = (
    "    environment: ci-controller\n"
    "    permissions:\n      contents: read\n      actions: read\n      security-events: read\n"
)
_RERUN_ENV = (
    _RERUN_STEP + "        env:\n          GH_TOKEN: ${{ github.token }}\n"
    "          RERUN: ${{ needs.post.outputs.rerun }}\n"
)


@pytest.mark.parametrize(
    "old, new, says",
    [
        # The rerun job: after post, on post's events, for a run post names.
        ("  rerun:\n    needs: post\n", "  rerun:\n", "the rerun job runs other than after post"),
        (
            f"      {_RERUN_NAMED}\n    runs-on: ubuntu-24.04\n    timeout-minutes: {_RERUN_MINUTES}",  # noqa: E501
            f"    runs-on: ubuntu-24.04\n    timeout-minutes: {_RERUN_MINUTES}",
            "the rerun job runs other than after post",
        ),
        # Its bound: inside the queue's check limit, and there at all.
        (
            f"    timeout-minutes: {_RERUN_MINUTES}\n",
            f"    timeout-minutes: {_QUEUE_CHECK_MINUTES + 10}\n",
            "the rerun job's bound",
        ),
        (f"    timeout-minutes: {_RERUN_MINUTES}\n", "", "the rerun job's bound"),
        # Its grant: actions: write and contents: read, nothing more, and no deployment.
        (
            "    permissions:\n      contents: read\n      actions: write\n",
            "    permissions:\n      contents: read\n      actions: write\n      checks: write\n",
            "the rerun job holds",
        ),
        (
            "permissions:\n  contents: read\n\njobs:",
            "permissions:\n  contents: read\n  actions: write\n\njobs:",
            "the workflow holds",
        ),
        (
            f"    timeout-minutes: {_RERUN_MINUTES}\n",
            f"    timeout-minutes: {_RERUN_MINUTES}\n    environment: ci-controller\n",
            "the rerun job deploys to",
        ),
        # Its one step: the controller's rerun, of post's run, on the job's own token.
        (
            '            --run-id "$RERUN" --controller-app-id "$APP_ID"\n',
            '            --run-id "$RERUN" --controller-app-id "$APP_ID"\n'
            "      - name: more\n        run: echo ${{ secrets.CONTROLLER_APP_KEY }}\n",
            "a step of the rerun job reads a secret",
        ),
        (
            _CHECKOUT_STEP + _RERUN_STEP,
            _CHECKOUT_STEP + "      - uses: actions/github-script@v7\n" + _RERUN_STEP,
            "the rerun job runs an action other than main's checkout",
        ),
        (
            _CHECKOUT_STEP + _RERUN_STEP,
            _CHECKOUT_STEP.replace("persist-credentials: false", "fetch-depth: 1") + _RERUN_STEP,
            "the rerun job's checkout keeps its credentials",
        ),
        (
            _RERUN_ENV,
            _RERUN_ENV.replace("${{ github.token }}", _APP_TOKEN),
            "the rerun job runs other than the controller's rerun alone",
        ),
        (
            _RERUN_ENV,
            _RERUN_ENV.replace(
                "${{ needs.post.outputs.rerun }}", "${{ github.event.workflow_run.id }}"
            ),
            "the rerun job runs other than the controller's rerun alone",
        ),
        ("merge-controller rerun --repo", "merge-controller dispatch --repo", "rerun alone"),
        # CORE F1: a second step that opens like the identity, or the identity's own script
        # extended, would run on the job's actions: write.
        pytest.param(
            _RERUN_STEP,
            "      - name: Runner identity again\n"
            f'        run: {_IDENTITY_RUN}" ; gh run cancel "$RERUN"\n'
            "        env:\n          GH_TOKEN: ${{ github.token }}\n"
            "          RERUN: ${{ needs.post.outputs.rerun }}\n" + _RERUN_STEP,
            "the rerun job's steps are not exactly",
            id="a second identity step",
        ),
        pytest.param(
            _IDENTITY_STEP + _CHECKOUT_STEP + _RERUN_STEP,
            _IDENTITY_STEP.rstrip("\n")
            + ' ; gh api -X POST "repos/$GITHUB_REPOSITORY/actions/runs/1/approve"\n'
            "        env:\n          GH_TOKEN: ${{ github.token }}\n"
            + _CHECKOUT_STEP
            + _RERUN_STEP,
            "the rerun job's runner identity step is not the tree's one script",
            id="the identity step extended",
        ),
        # CORE F3: the rerun step's own script, a line added or swapped for another command.
        (
            '            --run-id "$RERUN" --controller-app-id "$APP_ID"\n',
            '            --run-id "$RERUN" --controller-app-id "$APP_ID"\n'
            '          gh run cancel "$RERUN"\n',
            "the rerun job runs other than the controller's rerun alone",
        ),
        (
            '          python3 -m tools.ci merge-controller rerun --repo "$GITHUB_REPOSITORY" \\\n'
            '            --run-id "$RERUN" --controller-app-id "$APP_ID"\n',
            '          gh api -X POST "repos/$GITHUB_REPOSITORY/actions/runs/$RERUN/approve"'
            " # merge-controller rerun \n",
            "the rerun job runs other than the controller's rerun alone",
        ),
        # CORE F2: an expression pasted into a script, the event's own words in the shell.
        (
            '--run-id "$RERUN"',
            '--run-id "${{ github.event.workflow_run.id }}"',
            "a step of the rerun job pastes an expression into its script",
        ),
        (
            "          set -euo pipefail\n          python3 -m tools.ci merge-controller rerun",
            "          set -euo pipefail\n"
            '          echo "${{ github.event.workflow_run.display_title }}"\n'
            "          python3 -m tools.ci merge-controller rerun",
            "a step of the rerun job pastes an expression into its script",
        ),
        # The fallback: whenever rerun did not succeed, a timeout or a cancel included.
        ("    needs: [post, rerun]\n", "    needs: rerun\n", "the rerun fallback runs other than"),
        ("      always()\n", "      failure()\n", "the rerun fallback runs other than"),
        (
            "      && needs.rerun.result != 'success'\n",
            "",
            "the rerun fallback runs other than",
        ),
        # A poster run that names no run to rerun starts no fallback: its rerun was skipped, which
        # is not success either, and an App-token job must not start for it.
        (
            f"      {_RERUN_NAMED}\n      && needs.rerun.result != 'success'\n",
            "      && needs.rerun.result != 'success'\n",
            "the rerun fallback runs other than",
        ),
        (_FALLBACK_GRANTS, _FALLBACK_GRANTS.split("\n", 1)[1], "the rerun fallback deploys to"),
        (
            _FALLBACK_GRANTS,
            _FALLBACK_GRANTS.replace("actions: read", "actions: write"),
            "the rerun fallback holds",
        ),
        (
            f"    timeout-minutes: {_FALLBACK_MINUTES}\n    environment",
            f"    timeout-minutes: {_RERUN_MINUTES}\n    environment",
            "the rerun fallback's bound",
        ),
        # Its decision: the queue commit of post's run, with its rerun ended, and nothing else.
        (_RERUN_ENDED, "--waiting", "the rerun fallback decides other than"),
        (_RERUN_ENDED, _RERUN_ENDED + ' --head "$RERUN"', "the rerun fallback decides other than"),
        # CORE F3: the fallback's posting step, on the App's token, a line added.
        (
            '            --body "$RUNNER_TEMP/merge-verdict.json"\n',
            '            --body "$RUNNER_TEMP/merge-verdict.json"\n          echo forged\n',
            "in the rerun fallback, the step holding the App's token runs more than the post",
        ),
        # CORE N1: its actions are main's checkout and the App's token alone.
        pytest.param(
            _POST_STEPS,
            "      - uses: someone/action@0123456789012345678901234567890123456789\n" + _POST_STEPS,
            "the rerun-fallback job runs someone/action, not main's checkout or the App's token",
            id="a third-party action in the fallback",
        ),
        # Its token: post's one step, minted only with a verdict to post.
        (
            f"        id: controller\n        if: {_POSTING}\n",
            "        id: controller\n",
            "in the rerun fallback, the controller's token is minted with no verdict to post",
        ),
        # Post names the run to them, and a poster has both jobs or neither.
        (f"      rerun: {_RERUN_OUTPUT}\n", "", "names no run to rerun"),
        ("\n  rerun-fallback:\n", "\n  rerun-fallback-later:\n", "has jobs"),
        # CORE F4 on the rerun's jobs: the fallback's posting step and decision, the rerun step, and
        # each job's own env or defaults.
        pytest.param(
            _POSTED_ENV,
            _POSTED_ENV + f"          BASH_ENV: '$({_FORGE})'\n",
            "the rerun-fallback job's posting step is not its closed step as a whole",
            id="P16 BASH_ENV on the fallback's posting step",
        ),
        pytest.param(
            _RERUN_ENV,
            _RERUN_ENV + "          BASH_ENV: '$(gh run cancel 1)'\n",
            "the rerun job's rerun step is not its closed step as a whole",
            id="P17 BASH_ENV on the rerun step",
        ),
        pytest.param(
            "        id: decide\n        env:\n",
            "        id: decide\n"
            "        shell: bash -c 'gh run cancel 1; bash {0}'\n        env:\n",
            "the rerun-fallback job's decision is not its closed step as a whole",
            id="a shell on the fallback's decision",
        ),
        pytest.param(
            "  rerun:\n    needs: post\n",
            "  rerun:\n    env:\n      BASH_ENV: '$(gh run cancel 1)'\n    needs: post\n",
            "the rerun job sets ['env']",
            id="a job-level env on rerun",
        ),
        pytest.param(
            "    needs: [post, rerun]\n",
            "    needs: [post, rerun]\n    defaults:\n      run:\n"
            f"        shell: bash -c '{_FORGE}; bash {{0}}'\n",
            "the rerun-fallback job sets ['defaults']",
            id="job-level defaults on the fallback",
        ),
        # CORE N1 on #6312: the rerun and its fallback each run on a GitHub-hosted runner.
        pytest.param(
            f"    runs-on: ubuntu-24.04\n    timeout-minutes: {_RERUN_MINUTES}\n",
            f"    runs-on: self-hosted\n    timeout-minutes: {_RERUN_MINUTES}\n",
            "the rerun job runs on 'self-hosted'",
            id="the rerun on a self-hosted runner",
        ),
        pytest.param(
            "    runs-on: ubuntu-24.04\n",
            "    runs-on: {group: ci-runners}\n",
            "the rerun-fallback job runs on {'group': 'ci-runners'}",
            id="the fallback on a runner group",
        ),
    ],
)
def test_a_planted_edit_of_the_in_place_rerun_fails(old: str, new: str, says: str) -> None:
    """Each plant in the last place its anchor stands: the rerun's jobs come last in the file,
    and the fallback after the rerun job."""
    text = _rerun_poster()
    assert old in text, f"the plant's anchor is gone: {old!r}"
    before, _, after = text.rpartition(old)
    found = _problems(before + new + after)
    assert any(says in p for p in found), found


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
        (_QUEUE_LINES, _WAKES_QUEUE),
        (_CONTRACT_QUEUE_LINES, _CONTRACT_IF_QUEUE),
        (_RERUN_LINES, _RERUN_IF),
        (_FALLBACK_LINES, _FALLBACK_IF),
    ):
        assert yaml.safe_load("job:\n" + _folded(lines))["job"]["if"] == expr
    poster = yaml.safe_load(_beside_poster())["jobs"]
    assert (poster["post"]["if"], poster["contract"]["if"]) in (
        (_WAKES_BESIDE, _CONTRACT_IF_BESIDE),
        (_WAKES_QUEUE, _CONTRACT_IF_QUEUE),
    )


def test_the_queue_shape_takes_a_queue_commit_s_run_as_a_heads() -> None:
    """The queue shape's condition is the beside shape's with a queue commit's Merge run taken
    on its completion and its start, and nothing else: the rows a poster in it is held to."""
    assert Counter(_QUEUE_LINES) - Counter(_BESIDE_LINES) == Counter(
        [_QUEUE_RUN, "&& (github.event.workflow_run.event == 'pull_request'", f"{_QUEUE_RUN})"]
    )
    assert Counter(_BESIDE_LINES) - Counter(_QUEUE_LINES) == Counter([_STARTS])
    beside = _beside_poster()
    posts, contracts, *_ = _IF_BLOCK.findall(beside)
    queued = beside.replace(posts, _folded(_QUEUE_LINES), 1)
    queued = queued.replace(contracts, _folded(_CONTRACT_QUEUE_LINES), 1)
    assert _problems(queued) == []
    # A queue shape whose contract job's condition is the beside shape's, in either tree.
    half = queued.replace(_folded(_CONTRACT_QUEUE_LINES), _folded(_CONTRACT_BESIDE_LINES), 1)
    assert any("contract" in p for p in _problems(half)), _problems(half)
    # The beside shape whole, which takes no queue commit's run: refused.
    beside_only = queued.replace(_folded(_QUEUE_LINES), _folded(_BESIDE_LINES), 1)
    beside_only = beside_only.replace(
        _folded(_CONTRACT_QUEUE_LINES), _folded(_CONTRACT_BESIDE_LINES), 1
    )
    found = _problems(beside_only)
    assert any("condition is not exactly" in p for p in found), found
    assert any("contract" in p for p in found), found


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
            "takes a runner for CodeQL on a pull request starting",
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
