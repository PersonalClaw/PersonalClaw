"""``.github/workflows/flaky-tests.yml``'s trust shape: one issue per flaky test (#3948).

It runs daily and on dispatch, on main only, one run at a time. Its one job holds
``actions: read`` and ``issues: write`` beside the checkout's ``contents: read``, and its token
reaches only the fetch and the step that writes the issues. It fetches a day more than the 14 days
an issue closes after, and keeps no cache. It carries its own window from its previous run
(``--carry flaky-tests``), and its one upload is that window, ``flaky-tests-window``, stored, once
the fetch that built it succeeded. Each rule fails on a planted edit of the real workflow.
"""

from __future__ import annotations

import json
import re
import shlex

import pytest
import yaml
from rails_repo import REPO

from tools.ci import fetch, flaky_issues

_WORKFLOWS = REPO / ".github" / "workflows"
_FLAKY = "flaky-tests.yml"
_TOKEN = re.compile(r"\$\{\{[^}]*\b(?:github\.token|secrets\.)")
_FETCH = "fetch the last 14 days' histories"
_ISSUES = "open, edit, reopen and close the flaky tests' issues"
_ON_MAIN = "${{ github.ref == 'refs/heads/main' }}"
_WINDOW = '--window "$RUNNER_TEMP/window"'
#: The carried window's artifact, named here rather than read from the head's tools/ci.
_CARRY = "flaky-tests-window"
#: The carried window is uploaded only once the fetch that built it succeeded.
_AFTER_FETCH = "${{ !cancelled() && steps.fetch.outcome == 'success' }}"
#: Days a carried window is kept: enough for a run GitHub's cron skipped or ran late to find it.
_CARRY_DAYS = 3
#: The fetch's options, spelled in full. Its parser takes any unique prefix of one (``--ca``), a
#: value joined by ``=``, and the last of a repeated one, so the rail reads the fetch's words and
#: refuses any other spelling, and any option given twice, rather than look for a substring.
_FETCH_OPTIONS = frozenset({"--repo", "--out", "--days", "--reserve", "--no-carry", "--carry"})

# The plants' anchors in the real workflow.
_CARRIED_DAYS = '/window" --days 15 --carry flaky-tests'
_FETCH_ID = "        id: fetch\n"
_CARRY_STEP = (
    "      # The window, for tomorrow's run: only once the fetch that built it succeeded,"
    " the window\n"
    "      # archive alone (the next run refuses an artifact holding anything else),"
    " stored as built.\n"
    "      - name: carry the window to the next run\n"
    f"        if: {_AFTER_FETCH}\n"
    "        uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2\n"
    "        with:\n"
    f"          name: {_CARRY}\n"
    f"          path: ${{{{ runner.temp }}}}/window/{fetch.WINDOW}\n"
    "          if-no-files-found: error\n"
    "          compression-level: 0\n"
    f"          retention-days: {_CARRY_DAYS}\n"
)


def _texts(old: str = "", new: str = "") -> dict[str, str]:
    texts = {p.name: p.read_text(encoding="utf-8") for p in sorted(_WORKFLOWS.glob("*.yml"))}
    if old:
        assert texts[_FLAKY].count(old) == 1, f"the plant's anchor is not once: {old!r}"
        texts[_FLAKY] = texts[_FLAKY].replace(old, new)
    return texts


def _crons(text: str) -> set[str]:
    return set(re.findall(r'^\s*-\s*cron:\s*"([^"]+)"', text, re.M))


def _fetch_options(fetching: str) -> tuple[dict[str, str | None], list[str]]:
    """The fetch's options, each with the word after it, and each word that spells one wrongly."""
    try:
        words = shlex.split(fetching)
    except ValueError:
        return {}, [f"{_FLAKY}'s fetch is not a command line it can read"]
    options: dict[str, str | None] = {}
    bad: list[str] = []
    for at, word in enumerate(words):
        if not word.startswith("--"):
            continue
        if word not in _FETCH_OPTIONS:
            bad.append(f"{_FLAKY}'s fetch passes {word!r}, not one of its options spelled in full")
        elif word in options:
            bad.append(f"{_FLAKY}'s fetch passes {word} twice")
        else:
            options[word] = words[at + 1] if at + 1 < len(words) else None
    return options, bad


def _carry_faults(
    steps: list[dict], options: dict[str, str | None], uploads: list[dict]
) -> list[str]:
    """The carry's rules: its own window in, and that window alone out."""
    bad: list[str] = []
    ids = [step.get("id") for step in steps if step.get("name") == _FETCH]
    carried = options.get("--carry") == "flaky-tests" and "--no-carry" not in options
    if not carried or ids != ["fetch"]:
        bad.append(f"{_FLAKY} does not carry its own window from its previous run")
    given = (uploads[0].get("with") or {}) if len(uploads) == 1 else {}
    if len(uploads) != 1 or given.get("name") != _CARRY:
        bad.append(f"{_FLAKY} does not upload its carried window, {_CARRY}, and that alone")
    elif given.get("compression-level") != 0 or given.get("if-no-files-found") != "error":
        bad.append(f"{_FLAKY} does not carry the window once, stored")
    elif given.get("path") != f"${{{{ runner.temp }}}}/window/{fetch.WINDOW}":
        # The next run takes a carried artifact only when it holds the window archive alone.
        bad.append(f"{_FLAKY} carries more than {fetch.WINDOW}")
    elif uploads[0].get("if") != _AFTER_FETCH:
        bad.append(f"{_FLAKY} carries a window the fetch did not finish")
    elif given.get("retention-days") != _CARRY_DAYS:
        bad.append(f"{_FLAKY} keeps its carried window {given.get('retention-days')!r} days")
    return bad


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
    if any(re.match(r"actions/cache(?:/save|/restore)?@", str(s.get("uses", ""))) for s in steps):
        bad.append(f"{_FLAKY} keeps a cache")
    by_name = {step.get("name"): str(step.get("run", "")) for step in steps}
    options, spelled = _fetch_options(by_name.get(_FETCH, ""))
    bad += spelled
    days = options.get("--days") or ""
    if not days.isdigit() or int(days) <= flaky_issues.CLOSE_DAYS:
        bad.append(f"{_FLAKY} does not fetch more than {flaky_issues.CLOSE_DAYS} days")
    uploads = [s for s in steps if re.match(r"actions/upload-artifact@", str(s.get("uses", "")))]
    bad += _carry_faults(steps, options, uploads)
    if options.get("--out") != "$RUNNER_TEMP/window" or _WINDOW not in by_name.get(_ISSUES, ""):
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
        "concurrency:\n  group: flaky-tests\n  cancel-in-progress: false\n",
        "",
        "can run twice at once or cancel a run",
        id="no-concurrency-group",
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
        '--window "$RUNNER_TEMP/window"',
        '--window "$RUNNER_TEMP/other"',
        "not written from the window it fetched",
        id="another-window",
    ),
    pytest.param(
        "      - name: open, edit, reopen",
        "      - uses: actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830\n"
        "        with:\n          path: x\n          key: x\n"
        "      - name: open, edit, reopen",
        "keeps a cache",
        id="a-cache",
    ),
    pytest.param(
        "      - name: open, edit, reopen",
        "      - uses: actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830\n"
        "        with:\n          path: x\n          key: x\n"
        "      - name: open, edit, reopen",
        "keeps a cache",
        id="a-cache-restored",
    ),
    pytest.param(
        "      - name: open, edit, reopen",
        "      - uses: actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830\n"
        "        with:\n          path: x\n          key: x\n"
        "      - name: open, edit, reopen",
        "keeps a cache",
        id="a-cache-saved",
    ),
    pytest.param(
        '/window" --days 15',
        '/window" --days 15 --days 3',
        "passes --days twice",
        id="a-later-window-short-of-the-close",
    ),
    pytest.param(
        '/window" --days 15',
        '/window" --days=15',
        "'--days=15', not one of its options spelled in full",
        id="the-days-joined-by-equals",
    ),
    pytest.param(
        _CARRIED_DAYS,
        '/window" --days 14 --carry flaky-tests',
        "does not fetch more than 14 days",
        id="a-window-short-of-the-close",
    ),
    pytest.param(
        _CARRIED_DAYS,
        '/window" --days 15 --no-carry',
        "does not carry its own window",
        id="a-window-fetched-afresh-every-day",
    ),
    pytest.param(
        _CARRIED_DAYS,
        f"{_CARRIED_DAYS} --no-carry",
        "does not carry its own window",
        id="a-carry-and-a-fresh-fetch",
    ),
    pytest.param(
        _CARRIED_DAYS,
        '/window" --days 15 --carry flake-report',
        "does not carry its own window",
        id="the-weekly-reports-window",
    ),
    pytest.param(
        _CARRIED_DAYS,
        f"{_CARRIED_DAYS} --carry flake-report",
        "passes --carry twice",
        id="the-weekly-reports-window-carried-last",
    ),
    pytest.param(
        _CARRIED_DAYS,
        f"{_CARRIED_DAYS} --ca flake-report",
        "'--ca', not one of its options spelled in full",
        id="the-weekly-reports-window-by-a-prefix",
    ),
    pytest.param(
        _CARRIED_DAYS,
        f"{_CARRIED_DAYS} --carry=flake-report",
        "'--carry=flake-report', not one of its options spelled in full",
        id="the-weekly-reports-window-joined-by-equals",
    ),
    pytest.param(
        _CARRIED_DAYS,
        '/window" --days 15 --carry flaky-tests-x',
        "does not carry its own window",
        id="a-carry-named-past-its-own",
    ),
    pytest.param(
        _CARRY_STEP,
        "",
        "does not upload its carried window, flaky-tests-window, and that alone",
        id="the-carry-step-removed",
    ),
    pytest.param(
        _FETCH_ID,
        "",
        "does not carry its own window",
        id="a-fetch-the-carry-cannot-name",
    ),
    pytest.param(
        f"          path: ${{{{ runner.temp }}}}/window/{fetch.WINDOW}\n",
        "          path: ${{ runner.temp }}/window/\n",
        f"carries more than {fetch.WINDOW}",
        id="the-status-carried-beside-the-window",
    ),
    pytest.param(
        f"        if: {_AFTER_FETCH}\n",
        "        if: ${{ !cancelled() }}\n",
        "carries a window the fetch did not finish",
        id="a-window-carried-after-a-failed-fetch",
    ),
    pytest.param(
        "          compression-level: 0\n",
        "          compression-level: 6\n",
        "does not carry the window once, stored",
        id="the-window-compressed-again",
    ),
    pytest.param(
        "          if-no-files-found: error\n",
        "          if-no-files-found: warn\n",
        "does not carry the window once, stored",
        id="a-carry-of-nothing",
    ),
    pytest.param(
        f"          retention-days: {_CARRY_DAYS}\n",
        "          retention-days: 90\n",
        "keeps its carried window 90 days",
        id="a-carry-kept-past-its-use",
    ),
    pytest.param(
        f"          name: {_CARRY}\n",
        "          name: flake-report-window\n",
        "does not upload its carried window, flaky-tests-window, and that alone",
        id="the-weekly-reports-name",
    ),
    pytest.param(
        "      - name: open, edit, reopen",
        "      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9\n"
        "        with:\n          name: window\n          path: x\n"
        "      - name: open, edit, reopen",
        "does not upload its carried window, flaky-tests-window, and that alone",
        id="a-second-upload",
    ),
]


@pytest.mark.parametrize(("old", "new", "said"), _PLANTS)
def test_each_rule_fails_on_its_plant(old: str, new: str, said: str) -> None:
    found = violations(_texts(old, new))
    assert any(said in line for line in found), found
