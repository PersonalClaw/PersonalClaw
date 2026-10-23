"""``.github/workflows/image-pins.yml`` and the image pins it reads (#4585).

The weekly check runs only on its schedule and a dispatch, from one job that holds
``contents: read`` and ``issues: write``, keeps no checkout credential, and writes the refresh
issue only on main. Every image a workflow or local action pins by digest carries the comment the
check needs (its platform's manifest and the index it came from), so no pin falls out of the
weekly check unseen. Each rule fails on a planted edit.
"""

from __future__ import annotations

import pytest
import yaml
from rails_repo import REPO

from tools.ci import image_pins

_WORKFLOW = REPO / ".github" / "workflows" / "image-pins.yml"
_STEP = "check the pinned images, and on main write the refresh issue"
_ON_MAIN = "${{ github.ref == 'refs/heads/main' && github.repository || '' }}"


def _problems(text: str) -> list[str]:
    data = yaml.safe_load(text)
    found: list[str] = []
    on = data.get("on", data.get(True))
    if not isinstance(on, dict) or set(on) != {"schedule", "workflow_dispatch"}:
        found.append(f"runs on {on!r}, not only its schedule and a dispatch")
    jobs = data.get("jobs") or {}
    if set(jobs) != {"check"}:
        return [*found, f"has jobs {sorted(jobs)}"]
    job = jobs["check"]
    if job.get("permissions") != {"contents": "read", "issues": "write"}:
        found.append(f"the job holds {job.get('permissions')!r}")
    for step in job.get("steps") or []:
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            if (step.get("with") or {}).get("persist-credentials") is not False:
                found.append("the checkout keeps its credentials")
        if step.get("name") == _STEP:
            if (step.get("env") or {}).get("ISSUE_REPO") != _ON_MAIN:
                found.append("the refresh issue is written off main")
            if "python3 -m tools.ci image-pins" not in str(step.get("run")):
                found.append("the step runs no image check")
    if not any(s.get("name") == _STEP for s in job.get("steps") or []):
        found.append("no check step")
    return found


def test_the_workflow_reads_only_and_writes_the_issue_on_main() -> None:
    assert _problems(_WORKFLOW.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "old, new, says",
    [
        ("  workflow_dispatch:\n", "  workflow_dispatch:\n  pull_request:\n", "runs on"),
        ("      issues: write", "      issues: write\n      contents: write", "the job holds"),
        (_ON_MAIN, "${{ github.repository }}", "written off main"),
        ("          persist-credentials: false\n", "          fetch-depth: 1\n", "credentials"),
    ],
)
def test_a_planted_edit_of_the_workflow_fails(old: str, new: str, says: str) -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"the plant's anchor is not once: {old!r}"
    assert any(says in p for p in _problems(text.replace(old, new)))


def test_every_pinned_image_names_its_platform_and_index() -> None:
    files = [
        (str(p.relative_to(REPO)), p.read_text(encoding="utf-8"))
        for p in image_pins.workflow_files(REPO)
    ]
    found, problems = image_pins.pins(files)
    assert problems == []
    assert len(found) >= 1, "no pinned workflow image found: the reader has lost them"


def test_a_pin_whose_comment_lost_its_index_fails() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    planted = text + (
        "  more:\n    runs-on: ubuntu-24.04\n    # python, the linux/amd64 manifest\n"
        "    container: python:3.12@sha256:" + "0" * 64 + "\n"
    )
    _, problems = image_pins.pins([("image-pins.yml", planted)])
    assert problems and "names no platform manifest and index" in problems[0]
