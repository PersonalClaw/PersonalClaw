"""``.github/workflows/release-tag.yml``'s trust shape: the one way a ``v*`` tag is made (#4584).

It runs only on a dispatch, its one job only on main, in the ``ci-controller`` environment, with
``contents: read``, ``actions: read``, ``issues: read``, ``checks: read`` and
``pull-requests: read``. The dispatch inputs reach the steps only as environment variables, never
as text in a command. ``python -m tools.ci release-tag`` checks the commit and the tag, and reads
the commit's push run on main, the merge controller's verdict on it, its breadth and the issues
that own breadth's advisory reds with the job's own token (#3911), before the release
controller's App token is made; that token is asked for ``contents: write`` alone and reaches
only the step that creates the tag. Each rule fails on a planted edit of the real file.
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
#: The job's own token, which reads the commit's push run on main and its verdict, nothing else.
_RUNS_TOKEN = "${{ github.token }}"
_GATE = '--repository "$GITHUB_REPOSITORY"'
#: What the tag job holds: the job's own token reads the commit's push run on main, the merge
#: controller's verdict on it (its check run and its pull request), its breadth and the issues that
#: own breadth's advisory reds.
_PERMISSIONS = {
    "contents": "read",
    "actions": "read",
    "issues": "read",
    "checks": "read",
    "pull-requests": "read",
}


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
    if job.get("permissions") != _PERMISSIONS:
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
        # The release gate reads this repository's runs with the job's token, in both steps.
        if (step.get("env") or {}).get("RUNS_TOKEN") != _RUNS_TOKEN or _GATE not in run:
            found.append(f"step {at!r} does not read the commit's push run on main")
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
            "    permissions:\n      contents: read\n      #",
            "    permissions:\n      contents: write\n      #",
            "the job holds",
        ),
        (
            'release-tag --tag "$TAG" --commit "$COMMIT" --repository "$GITHUB_REPOSITORY" "${more',
            "release-tag --tag ${{ inputs.tag }} --commit "
            '"$COMMIT" --repository "$GITHUB_REPOSITORY" "${more',
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
        ("--create ", "", "creates nothing"),
        ("        default: false\n", "        default: true\n", "defaults to false"),
        (
            '--create "${more[@]}"',
            "--create",
            "'create the tag' does not pass the rehearsal on",
        ),
        (
            "      actions: read\n",
            "      actions: write\n",
            "the job holds",
        ),
        ("      issues: read\n", "      issues: write\n", "the job holds"),
        ("      checks: read\n", "      checks: write\n", "the job holds"),
        ("      pull-requests: read\n", "", "the job holds"),
        (
            '--commit "$COMMIT" --repository "$GITHUB_REPOSITORY" "${more[@]}"',
            '--commit "$COMMIT" "${more[@]}"',
            "'check the commit and the tag' does not read the commit's push run",
        ),
        (
            '--repository "$GITHUB_REPOSITORY" --create',
            "--create",
            "'create the tag' does not read the commit's push run",
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


def _holding(permissions: dict[str, str]) -> list[str]:
    """The real file's problems with the tag job holding *permissions*, whichever set it holds."""
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    data["jobs"]["tag"]["permissions"] = dict(permissions)
    return _problems(yaml.safe_dump(data))


def test_the_five_pass() -> None:
    assert _holding(_PERMISSIONS) == []


@pytest.mark.parametrize(
    "permissions",
    [
        {"contents": "read", "actions": "read", "issues": "read"},
        {"contents": "read", "actions": "read", "issues": "read", "checks": "read"},
        {"contents": "read", "actions": "read", "issues": "read", "pull-requests": "read"},
        {
            "contents": "read",
            "actions": "read",
            "issues": "read",
            "checks": "write",
            "pull-requests": "read",
        },
    ],
    ids=["the-three-before-the-verdict", "checks-alone", "pull-requests-alone", "checks-write"],
)
def test_any_other_set_fails(permissions: dict[str, str]) -> None:
    assert any("the job holds" in p for p in _holding(permissions))


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


# ── breadth on a rehearsal, and the owner's record (#3911, #4712) ─────────────────────────────
#
# A rehearsal tag's push starts breadth.yml on the commit, so nothing dispatches it and no
# workflow needs `actions: write` for it. Breadth starts on that push and on no other: a branch
# push would run it on every candidate. The owner's record of a red run to pass over, when it
# exists, holds its shape.

_BREADTH = REPO / ".github" / "workflows" / "breadth.yml"
_WORKFLOWS = REPO / ".github" / "workflows"
_REHEARSAL_TAGS = ["rehearsal-v*"]
#: How a workflow could dispatch breadth itself: the API, the CLI, or the script action's client.
_DISPATCHES = ("breadth.yml/dispatches", "workflow run breadth", "createWorkflowDispatch")


def _breadth_problems(text: str) -> list[str]:
    on = yaml.safe_load(text).get("on", yaml.safe_load(text).get(True))
    if not isinstance(on, dict) or set(on) != {"schedule", "workflow_dispatch", "push"}:
        return [f"breadth runs on {sorted(on) if isinstance(on, dict) else on!r}"]
    push = on["push"]
    if not isinstance(push, dict) or push != {"tags": _REHEARSAL_TAGS}:
        return [f"breadth's push is {push!r}, not a rehearsal tag's alone"]
    return []


def _dispatch_problems(name: str, text: str) -> list[str]:
    return [f"{name} dispatches breadth ({d})" for d in _DISPATCHES if d in text]


def test_breadth_starts_on_a_rehearsal_tags_push_and_nothing_dispatches_it() -> None:
    assert _breadth_problems(_BREADTH.read_text(encoding="utf-8")) == []
    found = [
        problem
        for path in sorted(_WORKFLOWS.glob("*.y*ml"))
        for problem in _dispatch_problems(path.name, path.read_text(encoding="utf-8"))
    ]
    assert found == []


@pytest.mark.parametrize(
    "old, new, says",
    [
        (
            '    tags: ["rehearsal-v*"]\n',
            '    tags: ["rehearsal-v*"]\n    branches: [main]\n',
            "push",
        ),
        ('    tags: ["rehearsal-v*"]\n', '    tags: ["*"]\n', "push"),
        ('  push:\n    tags: ["rehearsal-v*"]\n', "", "runs on"),
        ('  push:\n    tags: ["rehearsal-v*"]\n', "  push:\n", "push"),
    ],
)
def test_a_planted_breadth_trigger_fails(old: str, new: str, says: str) -> None:
    text = _BREADTH.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"the plant's anchor is not once: {old!r}"
    assert any(says in p for p in _breadth_problems(text.replace(old, new)))


def test_a_planted_dispatch_of_breadth_fails() -> None:
    """The plant: the tagging workflow dispatching breadth itself, which would need a write."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    planted = text + (
        "      - name: start breadth\n"
        "        env:\n          GH_TOKEN: ${{ github.token }}\n"
        '        run: gh api -X POST "repos/$GITHUB_REPOSITORY/actions/workflows/'
        'breadth.yml/dispatches" -f ref=main\n'
    )
    assert _dispatch_problems("release-tag.yml", planted)
    assert _dispatch_problems("x.yml", "run: gh workflow run breadth.yml --ref v1")


def test_the_owners_record_when_it_exists_holds_its_shape() -> None:
    """Only the owner creates or edits it, by hand; while it does not exist nothing is excused."""
    from tools.ci import release_tag

    path = REPO / release_tag.BREADTH_EXCEPTIONS
    if path.exists():
        assert release_tag.exception_problems(json.loads(path.read_text(encoding="utf-8"))) == []
    assert release_tag.exception_problems({"about": "x", "exceptions": [{"run_id": 1}]})
