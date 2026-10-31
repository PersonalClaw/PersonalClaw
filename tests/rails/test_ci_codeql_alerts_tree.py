"""``.github/workflows/codeql-alerts.yml``'s trust shape: one issue per new CodeQL alert on main.

It listens to main's CodeQL runs alone, and runs hourly and on dispatch besides, on main only,
one run at a time. Its one job holds ``security-events: read`` and ``issues: write`` beside the
checkout's ``contents: read``, names no environment and no secret, checks out main's own tree
without keeping its credentials, and its token reaches only the step that writes the issues. Each
rule fails on a planted edit of the real workflow.
"""

from __future__ import annotations

import json
import re

import pytest
import yaml
from rails_repo import REPO

_WORKFLOWS = REPO / ".github" / "workflows"
_ALERTS = "codeql-alerts.yml"
_JOB = "codeql-alerts"
_TOKEN = re.compile(r"\$\{\{[^}]*\b(?:github\.token|secrets\.)")
_ISSUES = "open, reopen and close the CodeQL alerts' issues"
_RUN = 'python3 -m tools.ci codeql-issues --repo "$GITHUB_REPOSITORY"'
_ON = {
    "workflow_run": {"workflows": ["CodeQL"], "types": ["completed"], "branches": ["main"]},
    "schedule": [{"cron": "11 * * * *"}],
    "workflow_dispatch": None,
}
_IF = (
    "github.ref == 'refs/heads/main'"
    " && (github.event_name != 'workflow_run'"
    " || github.event.workflow_run.path == 'dynamic/github-code-scanning/codeql')"
)


def _texts(old: str = "", new: str = "") -> dict[str, str]:
    texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(_WORKFLOWS.glob("*.yml"))}
    if old:
        assert texts[_ALERTS].count(old) == 1, f"the plant's anchor is not once: {old!r}"
        texts[_ALERTS] = texts[_ALERTS].replace(old, new)
    return texts


def _crons(text: str) -> set[str]:
    return set(re.findall(r'^\s*-\s*cron:\s*"([^"]+)"', text, re.M))


def violations(texts: dict[str, str]) -> list[str]:
    workflow = yaml.safe_load(texts[_ALERTS])
    bad: list[str] = []
    on = workflow.get("on", workflow.get(True))
    if on != _ON:
        bad.append(f"{_ALERTS} runs on {on}, not main's CodeQL runs, hourly and on dispatch")
    taken = {c for name, text in texts.items() if name != _ALERTS for c in _crons(text)}
    if _crons(texts[_ALERTS]) & taken:
        bad.append(f"{_ALERTS}'s slot is another workflow's")
    if workflow.get("concurrency") != {"group": "codeql-alerts", "cancel-in-progress": False}:
        bad.append(f"{_ALERTS} can run twice at once or cancel a run")
    if workflow.get("permissions") != {"contents": "read"}:
        bad.append(f"{_ALERTS} grants {workflow.get('permissions')!r} at the top")
    jobs = workflow.get("jobs") or {}
    if set(jobs) != {_JOB}:
        bad.append(f"{_ALERTS} has jobs {sorted(jobs)}, not {_JOB} alone")
    job = jobs.get(_JOB) or {}
    if " ".join(str(job.get("if", "")).split()) != _IF:
        bad.append(f"{_ALERTS}:{_JOB} runs off main or on another workflow's run")
    wanted = {"contents": "read", "security-events": "read", "issues": "write"}
    if job.get("permissions") != wanted:
        bad.append(f"{_ALERTS}:{_JOB} holds {job.get('permissions')!r}, not {wanted}")
    if "environment" in job or "secrets." in texts[_ALERTS]:
        bad.append(f"{_ALERTS}:{_JOB} names an environment or a secret")
    steps = job.get("steps") or []
    for step in steps:
        if _TOKEN.search(json.dumps(step)) and step.get("name") != _ISSUES:
            bad.append(f"{_ALERTS}:{_JOB}:{step.get('name') or step.get('uses')} holds the token")
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
    if [s.get("with") for s in checkouts] != [{"persist-credentials": False}]:
        bad.append(f"{_ALERTS}:{_JOB} checks out other than main's tree, credentials dropped")
    if any(re.match(r"actions/(cache|upload-artifact)@", str(s.get("uses", ""))) for s in steps):
        bad.append(f"{_ALERTS} uploads or caches something")
    by_name = {step.get("name"): str(step.get("run", "")).strip() for step in steps}
    if by_name.get(_ISSUES) != _RUN:
        bad.append(f"{_ALERTS}'s issues step runs {by_name.get(_ISSUES)!r}, not {_RUN!r}")
    return bad


def test_the_codeql_alerts_workflow_holds_its_trust_shape() -> None:
    assert violations(_texts()) == []


_PLANTS = [
    pytest.param(
        "  workflow_dispatch:\n",
        "  workflow_dispatch:\n  pull_request:\n",
        "not main's CodeQL runs, hourly and on dispatch",
        id="a-pull-request-trigger",
    ),
    pytest.param(
        "    branches: [main]\n",
        "",
        "not main's CodeQL runs, hourly and on dispatch",
        id="a-listener-off-main",
    ),
    pytest.param(
        '    workflows: ["CodeQL"]',
        '    workflows: ["CodeQL", "Merge"]',
        "not main's CodeQL runs, hourly and on dispatch",
        id="another-workflows-runs",
    ),
    pytest.param(
        '    - cron: "11 * * * *"',
        '    - cron: "23 11 * * *"',
        "slot is another workflow's",
        id="the-flaky-tests-slot",
    ),
    pytest.param(
        "  cancel-in-progress: false\n",
        "  cancel-in-progress: true\n",
        "can run twice at once or cancel a run",
        id="a-cancelled-run",
    ),
    pytest.param(
        "permissions:\n  contents: read\n\njobs:",
        "permissions:\n  contents: read\n  issues: write\n\njobs:",
        "grants {'contents': 'read', 'issues': 'write'} at the top",
        id="a-write-at-the-top",
    ),
    pytest.param(
        "jobs:\n",
        "jobs:\n  more:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo\n",
        "has jobs ['codeql-alerts', 'more']",
        id="a-second-job",
    ),
    pytest.param(
        "      github.ref == 'refs/heads/main'\n      && ",
        "      ",
        "runs off main or on another workflow's run",
        id="off-main",
    ),
    pytest.param(
        "      || github.event.workflow_run.path == 'dynamic/github-code-scanning/codeql')",
        "      || true)",
        "runs off main or on another workflow's run",
        id="any-codeql-named-run",
    ),
    pytest.param(
        "      issues: write           #",
        "      issues: write\n      pull-requests: write   #",
        "holds {'contents': 'read', 'security-events': 'read', 'issues': 'write', 'pull-requests'",
        id="a-wider-token",
    ),
    pytest.param(
        "      security-events: read   #",
        "      security-events: write   #",
        "holds {'contents': 'read', 'security-events': 'write'",
        id="a-security-events-write",
    ),
    pytest.param(
        "    runs-on: ubuntu-24.04\n",
        "    runs-on: ubuntu-24.04\n    environment: ci-controller\n",
        "names an environment or a secret",
        id="an-environment",
    ),
    pytest.param(
        "          GH_TOKEN: ${{ github.token }}",
        "          GH_TOKEN: ${{ secrets.ADMIN }}",
        "names an environment or a secret",
        id="a-secret",
    ),
    pytest.param(
        "      - uses: actions/checkout@",
        "      - name: more\n        env:\n          GH_TOKEN: ${{ github.token }}\n"
        "        run: echo\n      - uses: actions/checkout@",
        f"{_JOB}:more holds the token",
        id="the-token-in-another-step",
    ),
    pytest.param(
        "          persist-credentials: false\n",
        "          persist-credentials: false\n"
        "          ref: ${{ github.event.workflow_run.head_sha }}\n",
        "checks out other than main's tree, credentials dropped",
        id="the-runs-head",
    ),
    pytest.param(
        "          persist-credentials: false\n",
        "          persist-credentials: true\n",
        "checks out other than main's tree, credentials dropped",
        id="kept-credentials",
    ),
    pytest.param(
        "      - name: open, reopen",
        "      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9\n"
        "        with:\n          name: alerts\n          path: x\n"
        "      - name: open, reopen",
        "uploads or caches something",
        id="an-upload",
    ),
    pytest.param(
        'codeql-issues --repo "$GITHUB_REPOSITORY"',
        'codeql-issues --repo "$GITHUB_REPOSITORY" && gh issue list',
        "issues step runs",
        id="more-in-the-issues-step",
    ),
]


@pytest.mark.parametrize(("old", "new", "said"), _PLANTS)
def test_each_rule_fails_on_its_plant(old: str, new: str, said: str) -> None:
    found = violations(_texts(old, new))
    assert any(said in line for line in found), found
