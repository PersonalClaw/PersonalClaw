"""``.github/workflows/merge-verdict.yml``: the merge verdict's poster keeps its trust shape.

It runs only as a ``workflow_run`` listener of Merge, so GitHub runs main's copy. It names nothing
of the candidate (no ``workflow_run.head*`` anywhere), checks out only the default branch and
keeps no credential. Its one job reads with its own token (``contents`` and ``actions``, read)
and mints the controller App's token, with ``checks: write`` alone, only when there is a verdict
to post, and only the step that posts holds it. Each rule fails on a planted edit.
"""

from __future__ import annotations

import pytest
import yaml
from rails_repo import REPO

from tools.ci import workflows

_WORKFLOW = REPO / ".github" / "workflows" / "merge-verdict.yml"
_APP_ACTION = "actions/create-github-app-token@"
_POSTING = "steps.decide.outputs.post == 'true'"
_TOKEN_ASKS = {
    "app-id": "${{ vars.CONTROLLER_APP_ID }}",
    "private-key": "${{ secrets.CONTROLLER_APP_KEY }}",
    "permission-checks": "write",
}


def _problems(text: str) -> list[str]:
    found: list[str] = []
    if "workflow_run.head" in text:
        found.append("it names the candidate's head, branch or repository")
    data = yaml.safe_load(text)
    on = data.get("on", data.get(True))
    want = {
        "workflow_run": {"workflows": [workflows.NAMES[workflows.MERGE]], "types": ["completed"]}
    }
    if on != want:
        found.append(f"runs on {on!r}, not only a completed Merge run")
    jobs = data.get("jobs") or {}
    if set(jobs) != {"post"}:
        return [*found, f"has jobs {sorted(jobs)}"]
    job = jobs["post"]
    if "if" in job:
        found.append("the job can be skipped, so a run could post nothing")
    if job.get("environment") != "ci-controller":
        found.append(f"the job deploys to {job.get('environment')!r}")
    if job.get("permissions") != {"contents": "read", "actions": "read"}:
        found.append(f"the job holds {job.get('permissions')!r}")
    steps = [s for s in job.get("steps") or [] if isinstance(s, dict)]
    for step in steps:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            given = step.get("with") or {}
            if given.get("persist-credentials") is not False:
                found.append("the checkout keeps its credentials")
            if {"ref", "repository"} & set(given):
                found.append("the checkout names a ref or a repository: only main's is read")
    tokens = [s for s in steps if str(s.get("uses", "")).startswith(_APP_ACTION)]
    if len(tokens) != 1 or tokens[0].get("with") != _TOKEN_ASKS:
        found.append(f"the controller's token is asked for as {[t.get('with') for t in tokens]}")
    elif tokens[0].get("if") != _POSTING:
        found.append("the controller's token is minted with no verdict to post")
    if [s for s in steps if "secrets." in str(s)] != tokens:
        found.append("a step other than the token step reads a secret")
    holders = [s for s in steps if "steps.controller.outputs.token" in str(s)]
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


@pytest.mark.parametrize(
    "old, new, says",
    [
        ("    types: [completed]\n", "    types: [completed]\n  pull_request:\n", "runs on"),
        ('workflows: ["Merge"]', 'workflows: ["Merge", "PR"]', "runs on"),
        (
            "          RUN_ID: ${{ github.event.workflow_run.id }}\n",
            "          RUN_ID: ${{ github.event.workflow_run.id }}\n"
            "          HEAD: ${{ github.event.workflow_run.head_sha }}\n",
            "names the candidate's head",
        ),
        ("    environment: ci-controller\n", "    environment: release\n", "deploys to"),
        ("      actions: read", "      actions: read\n      checks: write", "the job holds"),
        ("          persist-credentials: false\n", "          fetch-depth: 1\n", "credentials"),
        (
            "          persist-credentials: false\n",
            "          persist-credentials: false\n          ref: refs/pull/1/head\n",
            "names a ref",
        ),
        (
            "          permission-checks: write\n",
            "          permission-checks: write\n          permission-contents: write\n",
            "token is asked for",
        ),
        (
            "        if: steps.decide.outputs.post == 'true'\n        uses:",
            "        uses:",
            "minted",
        ),
        (
            "          RUN_ID: ${{ github.event.workflow_run.id }}\n",
            "          RUN_ID: ${{ github.event.workflow_run.id }}\n"
            "          KEY: ${{ secrets.CONTROLLER_APP_KEY }}\n",
            "other than the token step reads a secret",
        ),
        (
            "          GH_TOKEN: ${{ github.token }}\n",
            "          GH_TOKEN: ${{ steps.controller.outputs.token }}\n",
            "reaches a step other than",
        ),
        ("merge-controller post", "merge-controller decide", "does not post"),
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
