"""``.github/workflows/dependency-review.yml``'s trust shape (#4585).

It runs on ``pull_request`` alone (#3908), so it is the head's copy and feedback only: the merge
verdict reads the dependency graph itself, and never this workflow's run. It has no checkout at
all, so no candidate file is ever on disk; it holds
``contents: read`` and nothing else, and references no secret; and its one step is the pinned
dependency-review action, which reads the base and head from the event and compares them over the
API. Each rule fails on a planted edit of the real workflow.
"""

from __future__ import annotations

import json

import pytest
import yaml
from rails_repo import REPO

_WORKFLOW = REPO / ".github" / "workflows" / "dependency-review.yml"
_ACTION = "actions/dependency-review-action@"
#: The one event it runs on: no workflow runs on ``pull_request_target`` (#3908).
_TRIGGERS = ({"pull_request"},)


def _problems(text: str) -> list[str]:
    data = yaml.safe_load(text)
    found: list[str] = []
    on = data.get("on", data.get(True))
    if not isinstance(on, dict) or set(on) not in _TRIGGERS:
        found.append(f"runs on {on!r}, not one of {sorted(t for (t,) in _TRIGGERS)} alone")
    if data.get("permissions") != {"contents": "read"}:
        found.append(f"the workflow grants {data.get('permissions')!r}")
    jobs = data.get("jobs") or {}
    for name, job in jobs.items():
        if job.get("permissions") != {"contents": "read"}:
            found.append(f"{name} holds {job.get('permissions')!r}")
        if "secrets." in json.dumps(job):
            found.append(f"{name} references a secret")
        uses = [str(s.get("uses", "")) for s in job.get("steps") or []]
        if any(u.startswith("actions/checkout@") for u in uses):
            found.append(f"{name} checks out code")
        if any(u and not u.startswith(_ACTION) for u in uses):
            found.append(f"{name} runs another action: {uses!r}")
        if not any(u.startswith(_ACTION) for u in uses):
            found.append(f"{name} runs no dependency review")
    if not jobs:
        found.append("no job")
    return found


def test_the_review_runs_from_main_reads_only_and_checks_nothing_out() -> None:
    assert _problems(_WORKFLOW.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "old, new, says",
    [
        (
            "    types: [opened, synchronize, reopened]\n",
            "    types: [opened, synchronize, reopened]\n  push:\n",
            "alone",
        ),
        (
            "    permissions:\n      contents: read\n",
            "    permissions:\n      contents: read\n      pull-requests: write\n",
            "holds",
        ),
        (
            "      - name: Review the dependency changes\n",
            "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
            "      - name: Review the dependency changes\n",
            "checks out code",
        ),
        (
            "          comment-summary-in-pr: never\n",
            "          comment-summary-in-pr: never\n"
            "          repo-token: ${{ secrets.SOME_TOKEN }}\n",
            "references a secret",
        ),
        ("actions/dependency-review-action@", "someone/dependency-review-action@", "no dependency"),
    ],
)
def test_a_planted_edit_fails(old: str, new: str, says: str) -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"the plant's anchor is not once: {old!r}"
    problems = _problems(text.replace(old, new))
    assert any(says in p for p in problems), problems


@pytest.mark.parametrize(
    "new",
    ["  pull_request_target:\n", "  pull_request_target:\n    types: [opened]\n  pull_request:\n"],
    ids=["back-on-pull_request_target", "on-both"],
)
def test_it_runs_on_pull_request_alone(new: str) -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert text.count("  pull_request:\n") == 1
    found = _problems(text.replace("  pull_request:\n", new))
    assert any("alone" in p for p in found), found
