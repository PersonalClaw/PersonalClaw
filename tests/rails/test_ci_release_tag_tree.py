"""``.github/workflows/release-tag.yml``'s trust shape: the one way a ``v*`` tag is made (#4584).

It runs only on a dispatch, its one job only on main, in the ``ci-controller`` environment, with
``contents: read``. The dispatch inputs reach the steps only as environment variables, never as
text in a command. ``python -m tools.ci release-tag`` checks the commit and the tag before the
release controller's App token is made; that token is asked for ``contents: write`` alone and
reaches only the step that creates the tag. Each rule fails on a planted edit of the real file.
"""

from __future__ import annotations

import json

import pytest
import yaml
from rails_repo import REPO

_WORKFLOW = REPO / ".github" / "workflows" / "release-tag.yml"
_CHECK, _TOKEN, _CREATE = (
    "check the commit and the tag",
    "the release controller's token",
    "create the tag",
)
_APP_ACTION = "actions/create-github-app-token@"


def _problems(text: str) -> list[str]:
    data = yaml.safe_load(text)
    found: list[str] = []
    on = data.get("on", data.get(True))
    if not isinstance(on, dict) or set(on) != {"workflow_dispatch"}:
        found.append(f"runs on {on!r}, not a dispatch alone")
    jobs = data.get("jobs") or {}
    if set(jobs) != {"tag"}:
        return [*found, f"has jobs {sorted(jobs)}, not the one tag job"]
    job = jobs["tag"]
    if job.get("if") != "github.ref == 'refs/heads/main'":
        found.append(f"the job runs on {job.get('if')!r}, not main alone")
    if job.get("environment") != "ci-controller":
        found.append(f"the job deploys to {job.get('environment')!r}")
    if job.get("permissions") != {"contents": "read"}:
        found.append(f"the job holds {job.get('permissions')!r}")
    steps = job.get("steps") or []
    names = [s.get("name") for s in steps]
    for step in steps:
        if "${{ inputs." in str(step.get("run", "")) or "github.event.inputs" in str(
            step.get("run", "")
        ):
            found.append(f"step {step.get('name')!r} puts an input into its command")
        if "steps.controller.outputs.token" in json.dumps(step) and step.get("name") != _CREATE:
            found.append(f"step {step.get('name')!r} is handed the controller's token")
        if "secrets." in json.dumps(step) and step.get("name") != _TOKEN:
            found.append(f"step {step.get('name')!r} references a secret")
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            if (step.get("with") or {}).get("persist-credentials") is not False:
                found.append("the checkout keeps its credentials")
            if (step.get("with") or {}).get("ref"):
                found.append("the checkout names a ref: it must be main's own")
    if not all(n in names for n in (_CHECK, _TOKEN, _CREATE)):
        return [*found, f"steps {names!r} lack the check, the token or the create"]
    if not names.index(_CHECK) < names.index(_TOKEN) < names.index(_CREATE):
        found.append("the token is made before the commit and the tag are checked")
    token = steps[names.index(_TOKEN)]
    asks = {k: v for k, v in (token.get("with") or {}).items() if k.startswith("permission-")}
    if not str(token.get("uses", "")).startswith(_APP_ACTION) or asks != {
        "permission-contents": "write"
    }:
        found.append(f"the token step is {token.get('uses')!r} asking {asks!r}")
    for at in (_CHECK, _CREATE):
        run = str(steps[names.index(at)].get("run", ""))
        if "python3 -m tools.ci release-tag" not in run:
            found.append(f"step {at!r} runs no release-tag check")
    if "--create" not in str(steps[names.index(_CREATE)].get("run", "")):
        found.append("the create step creates nothing")
    return found


def test_the_tagging_workflow_checks_first_and_hands_the_token_to_one_step() -> None:
    assert _problems(_WORKFLOW.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "old, new, says",
    [
        ("  workflow_dispatch:\n", "  workflow_dispatch:\n  push:\n", "not a dispatch alone"),
        ("    if: github.ref == 'refs/heads/main'\n", "", "not main alone"),
        ("    environment: ci-controller\n", "", "deploys to None"),
        (
            "    permissions:\n      contents: read\n    steps:",
            "    permissions:\n      contents: write\n    steps:",
            "the job holds",
        ),
        (
            'run: python3 -m tools.ci release-tag --tag "$TAG" --commit "$COMMIT"\n',
            'run: python3 -m tools.ci release-tag --tag ${{ inputs.tag }} --commit "$COMMIT"\n',
            "puts an input into its command",
        ),
        ("          permission-contents: write\n", "", "asking {}"),
        (
            "          permission-contents: write\n",
            "          permission-contents: write\n          permission-workflows: write\n",
            "asking",
        ),
        (
            "          persist-credentials: false\n",
            "          persist-credentials: false\n          ref: ${{ inputs.commit }}\n",
            "names a ref",
        ),
        (
            "          persist-credentials: false\n",
            "          persist-credentials: true\n",
            "credentials",
        ),
        (
            "      - name: check the commit and the tag\n        env:\n",
            "      - name: check the commit and the tag\n        env:\n"
            "          GH_TOKEN: ${{ steps.controller.outputs.token }}\n",
            "is handed the controller's token",
        ),
        ('--create "$GITHUB_REPOSITORY"', "", "creates nothing"),
    ],
)
def test_a_planted_edit_fails(old: str, new: str, says: str) -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"the plant's anchor is not once: {old!r}"
    found = _problems(text.replace(old, new))
    assert any(says in p for p in found), found


def test_a_token_made_before_the_check_fails() -> None:
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["tag"]["steps"]
    names = [s.get("name") for s in steps]
    check = steps.pop(names.index(_CHECK))
    steps.append(check)
    assert "the token is made before the commit and the tag are checked" in _problems(
        yaml.safe_dump(data)
    )
