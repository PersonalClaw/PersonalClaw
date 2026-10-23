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
_REHEARSE = 'if [ "$REHEARSAL" = true ]; then more+=(--rehearsal); fi'


def _problems(text: str) -> list[str]:
    data = yaml.safe_load(text)
    found: list[str] = []
    on = data.get("on", data.get(True))
    if not isinstance(on, dict) or set(on) != {"workflow_dispatch"}:
        found.append(f"runs on {on!r}, not a dispatch alone")
    inputs = (on.get("workflow_dispatch") or {}).get("inputs") if isinstance(on, dict) else None
    rehearsal = (inputs or {}).get("rehearsal") or {}
    if rehearsal.get("type") != "boolean" or rehearsal.get("default") is not False:
        found.append(f"the rehearsal input is {rehearsal!r}, not a boolean that defaults to false")
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
        step = steps[names.index(at)]
        run = str(step.get("run", ""))
        if "python3 -m tools.ci release-tag" not in run:
            found.append(f"step {at!r} runs no release-tag check")
        # A rehearsal must check and create the same name: both steps pass --rehearsal alike.
        rehearses = (
            (step.get("env") or {}).get("REHEARSAL") == "${{ inputs.rehearsal }}"
            and _REHEARSE in run
            and '"${more[@]}"' in run
        )
        if not rehearses:
            found.append(f"step {at!r} does not pass the rehearsal on")
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
            'release-tag --tag "$TAG" --commit "$COMMIT" "${more[@]}"\n',
            'release-tag --tag ${{ inputs.tag }} --commit "$COMMIT" "${more[@]}"\n',
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
        ("        default: false\n", "        default: true\n", "defaults to false"),
        (
            '--create "$GITHUB_REPOSITORY" "${more[@]}"',
            '--create "$GITHUB_REPOSITORY"',
            "'create the tag' does not pass the rehearsal on",
        ),
        (
            "        type: boolean\n",
            "        type: string\n",
            "not a boolean",
        ),
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


def test_the_check_loads_the_real_release_parser() -> None:
    """The tag check reads scripts/release_tags.py from the checkout, as the workflow's job does:
    it loads, takes a release tag, and refuses what the release refuses, naming why."""
    from tools.ci import release_tag, trust

    release_tag._parse_tag(REPO, "v0.2.6")
    release_tag._parse_tag(REPO, "v0.3.0-rc.1")
    with pytest.raises(trust.Refused, match="the leading 'v' is required"):
        release_tag._parse_tag(REPO, "0.2.6")
    with pytest.raises(trust.Refused, match="build metadata"):
        release_tag._parse_tag(REPO, "v0.2.6+local")
