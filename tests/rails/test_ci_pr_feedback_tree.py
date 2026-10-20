"""The source-tree checks split from
``tests/test_ci_pr_feedback.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import yaml
from ci_pr_feedback_shared import REPO

WORKFLOW = REPO / ".github" / "workflows" / "pr-feedback.yml"


def _steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["lint-help"]["steps"]
    assert isinstance(steps, list)
    return steps


def test_the_workflow_runs_the_relay_from_the_default_branch() -> None:
    steps = _steps()
    checkout = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkout) == 1
    with_ = checkout[0].get("with")
    assert isinstance(with_, dict)
    assert "ref" not in with_ and "repository" not in with_, "the default branch, nothing else"
    assert with_.get("persist-credentials") is False
    assert "scripts/ci_pr_feedback.mjs" in str(with_.get("sparse-checkout"))
    scripts = [s for s in steps if str(s.get("uses", "")).startswith("actions/github-script@")]
    assert len(scripts) == 1
    script = str(scripts[0]["with"]["script"])  # type: ignore[index]
    assert "scripts/ci_pr_feedback.mjs" in script and "relay(" in script
    assert "readFileSync" not in script, "the logic lives in the tested module, not inline"


def test_the_artifact_lands_outside_the_checkout() -> None:
    download = [s for s in _steps() if "download-artifact" in str(s.get("uses", ""))]
    assert len(download) == 1
    assert str(download[0]["with"]["path"]).startswith("${{ runner.temp }}/")  # type: ignore[index]
