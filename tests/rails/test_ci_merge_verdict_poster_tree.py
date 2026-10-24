"""``.github/workflows/merge-verdict.yml``: the merge verdict's poster keeps its trust shape.

It runs only as a ``workflow_run`` listener of Merge and Dependency review and a listener of
completed check runs, so GitHub runs main's copy. It names nothing
of the candidate (no ``workflow_run.head*`` anywhere), checks out only the default branch and
keeps no credential. ``post`` reads with its own token (``contents`` and ``actions``, read) and
mints the controller App's token, with ``checks: write`` alone, only when there is a verdict to
post, and only the step that posts holds it. ``trusted`` runs after it only when it names a head
to judge or a branch to delete, mints the App's token with ``contents: write`` alone for the step
that makes the trusted run's commit, and dispatches with its own token, which holds
``actions: write`` and nothing else that writes. Each rule fails on a planted edit.
"""

from __future__ import annotations

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
_TRUSTED_ASKS = {**_APP_KEYS, "permission-contents": "write"}
_TRUSTED_IF = "needs.post.outputs.dispatch != '' || needs.post.outputs.cleanup != ''"
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


def _trusted_problems(job: dict) -> list[str]:
    found: list[str] = []
    if job.get("needs") != "post" or job.get("if") != _TRUSTED_IF:
        found.append("the trusted job runs other than after post, on a head or a branch it names")
    if job.get("environment") != "ci-controller":
        found.append(f"the trusted job deploys to {job.get('environment')!r}")
    if job.get("permissions") != {"contents": "read", "actions": "write"}:
        found.append(f"the trusted job holds {job.get('permissions')!r}")
    steps = _steps(job)
    found += _checkout_problems("the trusted job", steps)
    tokens = [s for s in steps if str(s.get("uses", "")).startswith(_APP_ACTION)]
    if len(tokens) != 1 or tokens[0].get("with") != _TRUSTED_ASKS:
        found.append(
            f"the trusted job's App token is asked for as {[t.get('with') for t in tokens]}"
        )
    if [s for s in steps if "secrets." in str(s)] != tokens:
        found.append("a step of the trusted job other than its token step reads a secret")
    holders = [s for s in steps if _APP_TOKEN in str(s)]
    if len(holders) != 1 or "merge-controller trusted" not in str(holders[0].get("run")):
        found.append("the App's token reaches a step other than the one that makes the commit")
    sends = [s for s in steps if "merge-controller dispatch" in str(s.get("run"))]
    if len(sends) != 1 or (sends[0].get("env") or {}).get("GH_TOKEN") != "${{ github.token }}":
        found.append("the dispatch is not one step on the job's own token")
    return found


def _problems(text: str) -> list[str]:
    found: list[str] = []
    if "workflow_run.head" in text:
        found.append("it names the candidate's head, branch or repository")
    data = yaml.safe_load(text)
    on = data.get("on", data.get(True))
    want = {
        "workflow_run": {
            "workflows": [workflows.NAMES[workflows.MERGE], "Dependency review"],
            "types": ["completed"],
        },
        "check_run": {"types": ["completed"]},
    }
    if on != want:
        found.append(f"runs on {on!r}, not only a completed Merge run")
    jobs = data.get("jobs") or {}
    if set(jobs) != {"post", "trusted"}:
        return [*found, f"has jobs {sorted(jobs)}"]
    found += _trusted_problems(jobs["trusted"])
    job = jobs["post"]
    if "if" in job:
        found.append("the job can be skipped, so a run could post nothing")
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
_TRUSTED_CHECKOUT = (
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


@pytest.mark.parametrize(
    "old, new, says",
    [
        (
            '    workflows: ["Merge", "Dependency review"]\n    types: [completed]\n',
            '    workflows: ["Merge", "Dependency review"]\n    types: [completed]\n'
            "  pull_request:\n",
            "runs on",
        ),
        (
            "  check_run:\n    types: [completed]\n",
            "  check_run:\n    types: [completed, created]\n",
            "runs on",
        ),
        ('workflows: ["Merge", "Dependency review"]', 'workflows: ["Merge", "PR"]', "runs on"),
        ('workflows: ["Merge", "Dependency review"]', 'workflows: ["Merge"]', "runs on"),
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
        ("    needs: post\n", "", "the trusted job runs other than after post"),
        (
            "      actions: write   #",
            "      actions: write\n      contents: write   #",
            "the trusted job holds",
        ),
        (
            _TRUSTED_CHECKOUT,
            _TRUSTED_CHECKOUT.replace("persist-credentials: false", "fetch-depth: 1"),
            "the trusted job's checkout keeps its credentials",
        ),
        (
            "          permission-contents: write\n",
            "          permission-contents: write\n          permission-workflows: write\n",
            "the trusted job's App token is asked for",
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


def test_a_job_condition_that_could_skip_the_post_fails() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8").replace(
        "  post:\n", "  post:\n    if: github.event.workflow_run.conclusion == 'success'\n", 1
    )
    assert any("can be skipped" in p for p in _problems(text))
