"""``.github/workflows/dependency-review.yml``'s trust shape (#4585).

It runs on one event alone: ``pull_request_target``, so GitHub takes it from main, or, once it
moves (#3908), ``pull_request``, where it is the head's copy and feedback only, since the merge
verdict reads the dependency graph itself. It has no checkout at all, so no candidate file is ever
on disk; it holds
``contents: read`` and nothing else, and references no secret; and its one step is the pinned
dependency-review action, which reads the base and head from the event and compares them over the
API. Each rule fails on a planted edit of the real workflow.
"""

from __future__ import annotations

import json
import re

import pytest
import yaml
from rails_repo import REPO

_WORKFLOW = REPO / ".github" / "workflows" / "dependency-review.yml"
_ACTION = "actions/dependency-review-action@"
#: The one event it runs on: ``pull_request_target``, main's copy, until it moves to
#: ``pull_request`` (#3908), where the head's copy runs as feedback and the merge verdict reads
#: the dependency graph itself. The rest holds the same either way.
_TRIGGERS = ({"pull_request_target"}, {"pull_request"})
#: The trigger's line, whichever of the two the tree has: the plants below edit it as it is.
_TRIGGER = re.compile(r"^  (pull_request_target|pull_request):\n", re.M)


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


def test_it_runs_on_either_event_alone_and_on_both_fails() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    [line] = _TRIGGER.findall(text)
    other = "pull_request" if line == "pull_request_target" else "pull_request_target"
    assert _problems(_TRIGGER.sub(f"  {other}:\n", text)) == []
    both = _TRIGGER.sub(f"  {other}:\n    types: [opened]\n  {line}:\n", text)
    assert any("alone" in p for p in _problems(both)), _problems(both)
