"""``.github/workflows/flaky-tests.yml``'s trust shape: one issue per flaky test (#3948).

It runs daily and on dispatch, on main only, one run at a time. Its one job holds
``actions: read`` and ``issues: write`` beside the checkout's ``contents: read``, and its token
reaches only the fetch and the step that writes the issues. It fetches a day more than the 14 days
an issue closes after, and uploads nothing. Each rule fails on a planted edit of the real
workflow.
"""

from __future__ import annotations

import json
import re

import pytest
import yaml
from rails_repo import REPO

from tools.ci import flaky_issues

_WORKFLOWS = REPO / ".github" / "workflows"
_FLAKY = "flaky-tests.yml"
_TOKEN = re.compile(r"\$\{\{[^}]*\b(?:github\.token|secrets\.)")
_FETCH = "fetch the last 14 days' histories"
_ISSUES = "open, edit, reopen and close the flaky tests' issues"
_ON_MAIN = "${{ github.ref == 'refs/heads/main' }}"
_WINDOW = '--window "$RUNNER_TEMP/window"'


def _texts(old: str = "", new: str = "") -> dict[str, str]:
    texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(_WORKFLOWS.glob("*.yml"))}
    if old:
        assert texts[_FLAKY].count(old) == 1, f"the plant's anchor is not once: {old!r}"
        texts[_FLAKY] = texts[_FLAKY].replace(old, new)
    return texts


def _crons(text: str) -> set[str]:
    return set(re.findall(r'^\s*-\s*cron:\s*"([^"]+)"', text, re.M))


def violations(texts: dict[str, str]) -> list[str]:
    workflow = yaml.safe_load(texts[_FLAKY])
    bad: list[str] = []
    on = workflow.get("on", workflow.get(True))
    if on != {"schedule": [{"cron": "23 11 * * *"}], "workflow_dispatch": None}:
        bad.append(f"{_FLAKY} runs on {on}, not daily at 11:23 UTC and on dispatch")
    taken = {c for name, text in texts.items() if name != _FLAKY for c in _crons(text)}
    if _crons(texts[_FLAKY]) & taken:
        bad.append(f"{_FLAKY}'s slot is another workflow's")
    if workflow.get("concurrency") != {"group": "flaky-tests", "cancel-in-progress": False}:
        bad.append(f"{_FLAKY} can run twice at once or cancel a run")
    if workflow.get("permissions") != {"contents": "read"}:
        bad.append(f"{_FLAKY} grants {workflow.get('permissions')!r} at the top")
    jobs = workflow.get("jobs") or {}
    if set(jobs) != {"flaky-tests"}:
        bad.append(f"{_FLAKY} has jobs {sorted(jobs)}, not flaky-tests alone")
    job = jobs.get("flaky-tests") or {}
    if job.get("if") != _ON_MAIN:
        bad.append(f"{_FLAKY}:flaky-tests runs off main")
    wanted = {"contents": "read", "actions": "read", "issues": "write"}
    if job.get("permissions") != wanted:
        bad.append(f"{_FLAKY}:flaky-tests holds {job.get('permissions')!r}, not {wanted}")
    steps = job.get("steps") or []
    for step in steps:
        if _TOKEN.search(json.dumps(step)) and step.get("name") not in {_FETCH, _ISSUES}:
            bad.append(
                f"{_FLAKY}:flaky-tests:{step.get('name') or step.get('uses')} holds the token"
            )
    if any(re.match(r"actions/(cache|upload-artifact)@", str(s.get("uses", ""))) for s in steps):
        bad.append(f"{_FLAKY} uploads or caches something")
    by_name = {step.get("name"): str(step.get("run", "")) for step in steps}
    days = re.search(r"--days (\d+) --no-carry\b", by_name.get(_FETCH, ""))
    if not days or int(days.group(1)) <= flaky_issues.CLOSE_DAYS:
        bad.append(f"{_FLAKY} does not fetch more than {flaky_issues.CLOSE_DAYS} days afresh")
    if '--out "$RUNNER_TEMP/window"' not in by_name.get(_FETCH, "") or _WINDOW not in by_name.get(
        _ISSUES, ""
    ):
        bad.append(f"{_FLAKY}'s issues are not written from the window it fetched")
    return bad


def test_the_flaky_tests_workflow_holds_its_trust_shape() -> None:
    assert violations(_texts()) == []


_PLANTS = [
    pytest.param(
        "  workflow_dispatch:\n",
        "  workflow_dispatch:\n  pull_request:\n",
        "not daily at 11:23 UTC and on dispatch",
        id="a-pull-request-trigger",
    ),
    pytest.param(
        '    - cron: "23 11 * * *"',
        '    - cron: "47 10 * * *"',
        "slot is another workflow's",
        id="the-collectors-slot",
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
        "has jobs ['flaky-tests', 'more']",
        id="a-second-job",
    ),
    pytest.param(
        "    if: ${{ github.ref == 'refs/heads/main' }}\n",
        "",
        "runs off main",
        id="off-main",
    ),
    pytest.param(
        "      issues: write   #",
        "      issues: write\n      pull-requests: write   #",
        "holds {'contents': 'read', 'actions': 'read', 'issues': 'write', 'pull-requests'",
        id="a-wider-token",
    ),
    pytest.param(
        "      - uses: actions/checkout@",
        "      - name: more\n        env:\n          GH_TOKEN: ${{ github.token }}\n"
        "        run: echo\n      - uses: actions/checkout@",
        "flaky-tests:more holds the token",
        id="the-token-in-another-step",
    ),
    pytest.param(
        '/window" --days 15 --no-carry',
        '/window" --days 14 --no-carry',
        "does not fetch more than 14 days afresh",
        id="a-window-short-of-the-close",
    ),
    pytest.param(
        '/window" --days 15 --no-carry',
        '/window" --days 15',
        "does not fetch more than 14 days afresh",
        id="a-carried-window",
    ),
    pytest.param(
        '--window "$RUNNER_TEMP/window"',
        '--window "$RUNNER_TEMP/other"',
        "not written from the window it fetched",
        id="another-window",
    ),
    pytest.param(
        "      - name: open, edit, reopen",
        "      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9\n"
        "        with:\n          name: window\n          path: x\n"
        "      - name: open, edit, reopen",
        "uploads or caches something",
        id="an-upload",
    ),
]


@pytest.mark.parametrize(("old", "new", "said"), _PLANTS)
def test_each_rule_fails_on_its_plant(old: str, new: str, said: str) -> None:
    found = violations(_texts(old, new))
    assert any(said in line for line in found), found
