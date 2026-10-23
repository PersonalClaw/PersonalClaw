"""The weekly settings report's wiring and its expected file (#3907, #4585, #4584).

``.github/workflows/settings-report.yml`` runs only on its schedule and on a dispatch, from one
job in the ``settings-report`` environment, whose read-only credential reaches only the step
that reads the settings; it writes the drift issue only on main. ``.github/settings-expected.json``
names every setting the reader reads for core, the apps and the website, and holds the
``settings-report`` environment to deployments from main alone, so no branch's copy of the
workflow can reach that credential. Each rule fails on a planted edit of the real files.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
import yaml
from rails_repo import REPO

from tools.ci import settings_report

_WORKFLOW = REPO / ".github" / "workflows" / "settings-report.yml"
_EXPECTED = REPO / settings_report.EXPECTED
_REPOSITORIES = {
    "PersonalClaw/PersonalClaw",
    "PersonalClaw/PersonalClawApps",
    "PersonalClaw/personalclaw.dev",
}
_READ_STEP = "read the settings and compare, and on main write the drift issue"
#: The one bypass the report lets pass: the release controller's App creating a release tag or
#: its rehearsal (#4584), the release-tag-creation ruleset's one bypass actor, in core alone.
_CONTROLLER_BYPASS = [
    {"actor_name": "personalclaw-ci-controller[bot]", "ref_prefix": "refs/tags/v"},
    {"actor_name": "personalclaw-ci-controller[bot]", "ref_prefix": "refs/tags/rehearsal-v"},
]
#: The environments that hold a credential for a job only main runs: the report's read token
#: and the release controller's App key (#4584).
_MAIN_ONLY = ("settings-report", "ci-controller")
#: The environments only a release run may deploy to (#4584): a tag's own release.yml.
_TAG_ONLY = ("release", "release-client", "website-follow")
_ON_MAIN = "${{ github.ref == 'refs/heads/main' && github.repository || '' }}"


def _workflow_problems(text: str) -> list[str]:
    data = yaml.safe_load(text)
    found: list[str] = []
    on = data.get("on", data.get(True))
    if not isinstance(on, dict) or set(on) != {"schedule", "workflow_dispatch"}:
        found.append(f"runs on {on!r}, not only its schedule and a dispatch")
    jobs = data.get("jobs") or {}
    if set(jobs) != {"report"}:
        found.append(f"has jobs {sorted(jobs)}, not the one report job")
    job = jobs.get("report") or {}
    if job.get("environment") != "settings-report":
        found.append(f"report deploys to {job.get('environment')!r}, not settings-report")
    if job.get("permissions") != {"contents": "read", "issues": "write"}:
        found.append(f"report holds {job.get('permissions')!r}")
    for step in job.get("steps") or []:
        named = step.get("name") or step.get("uses", "")
        holds = json.dumps(step)
        if "secrets." in holds and named != _READ_STEP:
            found.append(f"step {named!r} references a secret")
        if (
            step.get("uses", "").startswith("actions/checkout@")
            and (step.get("with") or {}).get("persist-credentials") is not False
        ):
            found.append("the checkout keeps its credentials")
        if named == _READ_STEP:
            env = step.get("env") or {}
            if env.get("ISSUE_REPO") != _ON_MAIN:
                found.append(f"the drift issue's repository is {env.get('ISSUE_REPO')!r}")
            if env.get("SETTINGS_READ_TOKEN") != "${{ secrets.SETTINGS_READ_TOKEN }}":
                found.append("the read step does not take SETTINGS_READ_TOKEN")
            if "python3 -m tools.ci settings-report" not in str(step.get("run")):
                found.append("the read step runs no settings-report")
    if not any((s.get("name") == _READ_STEP) for s in job.get("steps") or []):
        found.append("no read step")
    return found


def _expected_problems(data: dict[str, Any]) -> list[str]:
    found: list[str] = []
    repos = data.get("repositories") or {}
    if set(repos) != _REPOSITORIES:
        found.append(f"names {sorted(repos)}, not core, the apps and the website")
    for repo, entry in repos.items():
        missing = set(settings_report.SETTINGS) - set(entry)
        if missing:
            found.append(f"{repo} misses {sorted(missing)}")
        allowed = _CONTROLLER_BYPASS if repo == "PersonalClaw/PersonalClaw" else []
        if entry.get("allowed bypass") != allowed:
            found.append(f"{repo} allows a bypass: {entry.get('allowed bypass')!r}")
    core = repos.get("PersonalClaw/PersonalClaw") or {}
    for name in _TAG_ONLY:
        deploys = ((core.get("environments") or {}).get(name) or {}).get("deployment")
        if deploys != ["tag:v*"]:
            found.append(f"core's {name} environment deploys from {deploys!r}, not v* tags alone")
    if core.get("fork approval") != "first_time_contributors":
        found.append(f"core's fork approval is {core.get('fork approval')!r}")
    for name in _MAIN_ONLY:
        env = (core.get("environments") or {}).get(name)
        if not isinstance(env, dict) or env.get("deployment") != ["branch:main"]:
            found.append(
                f"core's {name} environment does not deploy from main alone: "
                f"{None if not isinstance(env, dict) else env.get('deployment')!r}"
            )
    return found


def test_the_workflow_reads_from_main_alone_and_writes_only_the_issue() -> None:
    assert _workflow_problems(_WORKFLOW.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "old, new, says",
    [
        ("  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request:\n", "runs on"),
        ("    environment: settings-report\n", "", "deploys to None"),
        ("      issues: write", "      issues: write\n      contents: write", "report holds"),
        (_ON_MAIN, "${{ github.repository }}", "the drift issue's repository"),
        ("          persist-credentials: false\n", "          fetch-depth: 1\n", "credentials"),
        (
            "      - name: Runner identity\n",
            "      - name: Runner identity\n        env:\n"
            "          T: ${{ secrets.SETTINGS_READ_TOKEN }}\n",
            "references a secret",
        ),
    ],
)
def test_a_planted_edit_of_the_workflow_fails(old: str, new: str, says: str) -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"the plant's anchor is not once: {old!r}"
    problems = _workflow_problems(text.replace(old, new))
    assert any(says in p for p in problems), problems


def test_the_expected_file_names_every_setting_and_keeps_the_credential_on_main() -> None:
    settings_report.load_expected(_EXPECTED)
    assert _expected_problems(json.loads(_EXPECTED.read_text(encoding="utf-8"))) == []


@pytest.mark.parametrize(
    "plant, says",
    [
        (lambda d: d["repositories"].pop("PersonalClaw/personalclaw.dev"), "names"),
        (
            lambda d: d["repositories"]["PersonalClaw/PersonalClaw"]["environments"][
                "settings-report"
            ].update(deployment="any ref"),
            "does not deploy from main alone",
        ),
        (
            lambda d: d["repositories"]["PersonalClaw/PersonalClawApps"].update(
                {"allowed bypass": [{"actor_name": "x", "ref_prefix": "refs/heads/"}]}
            ),
            "allows a bypass",
        ),
        (
            lambda d: d["repositories"]["PersonalClaw/PersonalClaw"]["allowed bypass"].append(
                {"actor_name": "personalclaw-ci-controller[bot]", "ref_prefix": "refs/heads/"}
            ),
            "PersonalClaw/PersonalClaw allows a bypass",
        ),
        (
            lambda d: d["repositories"]["PersonalClaw/PersonalClaw"]["allowed bypass"][0].update(
                actor_name="keyurgolani"
            ),
            "PersonalClaw/PersonalClaw allows a bypass",
        ),
        (
            lambda d: d["repositories"]["PersonalClaw/PersonalClaw"]["allowed bypass"][0].update(
                ref_prefix="refs/tags/"
            ),
            "PersonalClaw/PersonalClaw allows a bypass",
        ),
        (
            lambda d: d["repositories"]["PersonalClaw/PersonalClawApps"].pop("security"),
            "misses ['security']",
        ),
        (
            lambda d: d["repositories"]["PersonalClaw/PersonalClaw"]["environments"][
                "ci-controller"
            ].update(deployment=["branch:main", "tag:v*"]),
            "core's ci-controller environment does not deploy from main alone",
        ),
        (
            lambda d: d["repositories"]["PersonalClaw/PersonalClaw"]["environments"][
                "release"
            ].update(deployment="any ref"),
            "core's release environment deploys from 'any ref'",
        ),
        (
            lambda d: d["repositories"]["PersonalClaw/PersonalClaw"]["environments"][
                "website-follow"
            ].update(deployment=["tag:v*", "branch:main"]),
            "core's website-follow environment deploys from",
        ),
        (
            lambda d: d["repositories"]["PersonalClaw/PersonalClaw"].update(
                {"fork approval": "first_time_contributors_new_to_github"}
            ),
            "core's fork approval",
        ),
    ],
)
def test_a_planted_edit_of_the_expected_file_fails(plant: Any, says: str) -> None:
    data = copy.deepcopy(json.loads(_EXPECTED.read_text(encoding="utf-8")))
    plant(data)
    problems = _expected_problems(data)
    assert any(says in p for p in problems), problems
