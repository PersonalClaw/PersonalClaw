"""The source-tree checks split from
``tests/test_apps_sdk_contract.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import pytest
import yaml
from apps_sdk_contract_shared import REPO

from scripts import ci_touches_sdk


@pytest.mark.parametrize(
    "path",
    [
        "src/personalclaw/sdk/channel.py",
        "src/personalclaw/sdk/signatures.json",
        "scripts/apps_sdk_contract.py",
        "scripts/sdk_signature_snapshot.py",
        ".github/workflows/ci.yml",
    ],
)
def test_a_path_that_can_change_the_sdk_runs_the_job(path):
    assert ci_touches_sdk.touches_sdk([path])


def _ci() -> dict:
    return yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_the_workflow_wires_the_verdict_to_the_job():
    jobs = _ci()["jobs"]
    assert jobs["changes"]["outputs"]["sdk"] == "${{ steps.sdk.outputs.sdk }}"
    step = next(s for s in jobs["changes"]["steps"] if s.get("id") == "sdk")
    assert (
        "scripts/ci_touches_sdk.py" in step["run"] and 'verdict="sdk=true"' in step["run"]
    ), "the verdict must fall back to RUNNING the job when the classifier gives no answer"
    contract = jobs["apps-contract"]
    assert contract["needs"] == ["changes"]
    assert "needs.changes.outputs.sdk == 'true'" in contract["if"]
    runs = " ".join(str(s.get("run", "")) for s in contract["steps"])
    assert "scripts/apps_sdk_contract.py" in runs
    assert "--base-ref" in runs and "--install-deps" in runs
    repos = [s.get("with", {}).get("repository") for s in contract["steps"]]
    assert "PersonalClaw/PersonalClawApps" in repos
