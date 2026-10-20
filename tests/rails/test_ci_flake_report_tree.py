"""``.github/workflows/flake-report.yml``'s trust shape: the weekly CI health report (#3948).

The job that runs the repository's own tests (the quarantine's reruns) holds no write token; the
job that reads the record and writes the one weekly issue holds ``actions: read`` and
``issues: write`` beside the checkout's ``contents: read``, its token reaches only the steps
that call the API, and it writes the issue only on main. Each rule fails on a planted edit of the
real workflow.
"""

from __future__ import annotations

import json
import re

import pytest
import yaml
from rails_repo import REPO

from tools.ci import fetch

_WORKFLOWS = REPO / ".github" / "workflows"
_FLAKE = "flake-report.yml"
_TOKEN = re.compile(r"\$\{\{[^}]*\b(?:github\.token|secrets\.)")
_TOKEN_STEPS = {"fetch the window", "report, and on main write the weekly issue"}
#: The one repository the report may write the issue in: this one, and only on main. A dispatch
#: from any other branch passes no --issue-repo, so it writes nothing.
_ON_MAIN = "${{ github.ref == 'refs/heads/main' && github.repository || '' }}"
_ISSUE_ARG = '--issue-repo "$ISSUE_REPO"'
#: The carried window is uploaded only once the fetch that built it succeeded.
_AFTER_FETCH = "${{ !cancelled() && steps.fetch.outcome == 'success' }}"


def _texts(old: str = "", new: str = "") -> dict[str, str]:
    texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(_WORKFLOWS.glob("*.yml"))}
    if old:
        assert texts[_FLAKE].count(old) == 1, f"the plant's anchor is not once: {old!r}"
        texts[_FLAKE] = texts[_FLAKE].replace(old, new)
    return texts


def _crons(text: str) -> set[str]:
    return set(re.findall(r'^\s*-\s*cron:\s*"([^"]+)"', text, re.M))


def violations(texts: dict[str, str]) -> list[str]:
    workflow = yaml.safe_load(texts[_FLAKE])
    bad: list[str] = []
    on = workflow.get("on", workflow.get(True))
    if on != {"schedule": [{"cron": "13 8 * * 1"}], "workflow_dispatch": None}:
        bad.append(f"{_FLAKE} runs on {on}, not Mondays at 08:13 UTC and on dispatch")
    taken = {c for name, text in texts.items() if name != _FLAKE for c in _crons(text)}
    if _crons(texts[_FLAKE]) & taken:
        bad.append(f"{_FLAKE}'s slot is another workflow's")
    if workflow.get("concurrency") != {"group": "flake-report", "cancel-in-progress": False}:
        bad.append(f"{_FLAKE} can run twice at once or cancel a run")
    if workflow.get("permissions") != {"contents": "read"}:
        bad.append(f"{_FLAKE} grants {workflow.get('permissions')!r} at the top")
    jobs = workflow.get("jobs") or {}
    if set(jobs) != {"rerun", "report"}:
        bad.append(f"{_FLAKE} has jobs {sorted(jobs)}, not rerun and report")
    rerun, report = jobs.get("rerun") or {}, jobs.get("report") or {}
    if rerun.get("permissions") != {"contents": "read"}:
        bad.append(f"{_FLAKE}:rerun holds {rerun.get('permissions')!r}, not contents: read")
    if any(_TOKEN.search(json.dumps(step)) for step in rerun.get("steps") or []):
        bad.append(f"{_FLAKE}:rerun hands a step the job token")
    wanted = {"contents": "read", "actions": "read", "issues": "write"}
    if report.get("permissions") != wanted:
        bad.append(f"{_FLAKE}:report holds {report.get('permissions')!r}, not {wanted}")
    for step in report.get("steps") or []:
        if _TOKEN.search(json.dumps(step)) and step.get("name") not in _TOKEN_STEPS:
            bad.append(f"{_FLAKE}:report:{step.get('name') or step.get('uses')} holds the token")
    every = [step for job in jobs.values() for step in job.get("steps") or []]
    if any("actions/cache" in str(step.get("uses", "")) for step in every):
        bad.append(f"{_FLAKE} keeps a cache: the planner reads the record itself")
    carried = [s for s in every if (s.get("with") or {}).get("name") == fetch.CARRY]
    if len(carried) != 1 or (carried[0].get("with") or {}).get("compression-level") != 0:
        bad.append(f"{_FLAKE} does not carry the window once, stored, as {fetch.CARRY}")
    elif carried[0]["with"].get("path") != f"${{{{ runner.temp }}}}/window/{fetch.WINDOW}":
        # The next run takes a carried artifact only when it holds the window archive alone.
        bad.append(f"{_FLAKE} carries more than {fetch.WINDOW}")
    elif carried[0].get("if") != _AFTER_FETCH:
        bad.append(f"{_FLAKE} carries a window the fetch did not finish")
    # The issue is written by the one report step, in the repository ISSUE_REPO names, which is
    # empty off main.
    issuing = [s for s in every if "--issue-repo" in json.dumps(s)]
    run = str(issuing[0].get("run", "")) if len(issuing) == 1 else ""
    if (
        len(issuing) != 1
        or issuing[0] not in (report.get("steps") or [])
        or (issuing[0].get("env") or {}).get("ISSUE_REPO") != _ON_MAIN
        or run.count("--issue-repo") != run.count(_ISSUE_ARG)
    ):
        bad.append(f"{_FLAKE}'s report can write the issue off main")
    said = [s for s in report.get("steps") or [] if "tools.ci report" in str(s.get("run", ""))]
    if (
        len(said) != 1
        or f'--fetch-status "$RUNNER_TEMP/window/{fetch.STATUS}"' not in said[0]["run"]
    ):
        bad.append(f"{_FLAKE}'s report does not say how the window was built")
    return bad


def test_the_weekly_report_holds_its_trust_shape() -> None:
    assert violations(_texts()) == []


_PLANTS = [
    pytest.param(
        "  workflow_dispatch:\n",
        "  workflow_dispatch:\n  pull_request:\n",
        "not Mondays at 08:13 UTC and on dispatch",
        id="a-pull-request-trigger",
    ),
    pytest.param(
        "permissions:\n  contents: read\n\nenv:",
        "permissions:\n  contents: read\n  issues: write\n\nenv:",
        "grants {'contents': 'read', 'issues': 'write'} at the top",
        id="a-write-at-the-top",
    ),
    pytest.param(
        "jobs:\n",
        "jobs:\n  more:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo\n",
        "has jobs ['more', 'report', 'rerun']",
        id="a-third-job",
    ),
    pytest.param(
        "      - name: Install (locked)\n",
        "      - uses: actions/cache@v4\n        with:\n          path: x\n          key: x\n"
        "      - name: Install (locked)\n",
        "keeps a cache",
        id="a-cache",
    ),
    pytest.param(
        "ISSUE_REPO: ${{ github.ref == 'refs/heads/main' && github.repository || '' }}",
        "ISSUE_REPO: ${{ github.repository }}",
        "can write the issue off main",
        id="the-issue-written-off-main",
    ),
    pytest.param(
        'more+=(--issue-repo "$ISSUE_REPO")',
        'more+=(--issue-repo "$GITHUB_REPOSITORY")',
        "can write the issue off main",
        id="the-issue-repo-not-the-gated-one",
    ),
    pytest.param(
        "        if: ${{ !cancelled() && steps.fetch.outcome == 'success' }}\n",
        "        if: ${{ !cancelled() }}\n",
        "carries a window the fetch did not finish",
        id="a-window-carried-after-a-failed-fetch",
    ),
    pytest.param(
        '    - cron: "13 8 * * 1"  # Mondays, 08:13 UTC\n',
        '    - cron: "41 5 * * *"\n',
        "slot is another workflow's",
        id="another-workflows-slot",
    ),
    pytest.param(
        "  cancel-in-progress: false\n",
        "  cancel-in-progress: true\n",
        "can run twice at once or cancel a run",
        id="a-run-that-can-be-cancelled",
    ),
    pytest.param(
        "    timeout-minutes: 35\n    permissions:\n      contents: read\n",
        "    timeout-minutes: 35\n    permissions:\n      contents: read\n      issues: write\n",
        "rerun holds",
        id="a-write-on-the-rerun",
    ),
    pytest.param(
        "      - name: run each quarantined test ten times\n",
        "      - name: run each quarantined test ten times\n        env:\n"
        "          GH_TOKEN: ${{ github.token }}\n",
        "rerun hands a step the job token",
        id="the-token-on-the-rerun",
    ),
    pytest.param(
        "      actions: read   # the collector's",
        "      actions: write   # the collector's",
        "report holds",
        id="actions-write-on-the-report",
    ),
    pytest.param(
        "      - name: Upload the report's figures\n",
        "      - name: Upload the report's figures\n        env:\n"
        "          GH_TOKEN: ${{ github.token }}\n",
        "Upload the report's figures holds the token",
        id="the-token-on-an-upload",
    ),
    pytest.param(
        "          path: ${{ runner.temp }}/window/window.zip\n",
        "          path: ${{ runner.temp }}/window/\n",
        "carries more than window.zip",
        id="the-status-carried-beside-the-window",
    ),
    pytest.param(
        ' --fetch-status "$RUNNER_TEMP/window/fetch-status.json"',
        "",
        "does not say how the window was built",
        id="a-report-silent-on-the-fetch",
    ),
    pytest.param(
        "          compression-level: 0\n",
        "          compression-level: 6\n",
        "does not carry the window once, stored",
        id="the-window-compressed-again",
    ),
]


@pytest.mark.parametrize(("old", "new", "expect"), _PLANTS)
def test_the_weekly_reports_shape_fails_on_a_planted(old: str, new: str, expect: str) -> None:
    found = violations(_texts(old, new))
    assert any(expect in line for line in found), found
