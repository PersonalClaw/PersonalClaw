"""The source-tree checks split from
``tests/test_ghcr_anonymous_pull.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import yaml
from rails_repo import REPO

_REPO = REPO


_WORKFLOW = _REPO / ".github" / "workflows" / "ghcr-anonymous-pull.yml"


def test_the_workflow_declares_no_credentials_and_fetches_the_tags() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    job = workflow["jobs"]["anonymous-pull"]
    identity, checkout, probe = job["steps"]

    # Both levels: the job level is the one that binds, because a job may widen the default.
    assert workflow["permissions"] == {}
    assert job["permissions"] == {}
    # An `env:` block is how a token gets back into scope; the tool would then exit 2.
    assert "env" not in workflow
    assert "env" not in job
    assert not [step for step in job["steps"] if "env" in step]
    assert "secrets." not in text

    # The runner-image line every job logs first (tests/rails/test_ci_contract.py) reads no secret.
    assert identity["name"] == "Runner identity" and "secrets." not in identity["run"]
    assert checkout["uses"].startswith("actions/checkout@")
    # Without the tags the expectation resolves empty and the tag half would pass vacuously.
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    assert probe["run"].strip() == "python3 tools/ghcr_anonymous_pull.py"
    # `on:` is the YAML 1.1 boolean `True` once parsed, hence the two-key lookup.
    assert "schedule" in (workflow.get("on") or workflow[True])
