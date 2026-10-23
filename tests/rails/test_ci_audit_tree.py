"""The source-tree checks split from
``tests/test_ci_audit.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

import ci_audit_shared
import pytest
import yaml
from rails_repo import REPO

from tools.ci import audit, supply_chain

_no_summary = ci_audit_shared._no_summary


def test_each_graph_names_the_extras_its_artifact_installs() -> None:
    """The extras are read from the files that install them, so a new extra in the image or
    the desktop bundle cannot leave the audit on a smaller graph."""
    dockerfile = (REPO / "deploy/docker/Dockerfile.backend").read_text(encoding="utf-8")
    export = re.search(
        r"uv export --locked --no-emit-project --emit-index-url(.*?)-o ", dockerfile, re.S
    )
    assert export, "the gateway image's locked export is gone"
    assert (
        tuple(re.findall(r"--extra (\S+)", export.group(1))) == audit.PYTHON_GRAPHS["gateway-image"]
    )
    release = (REPO / ".github/workflows/release-artifacts.yml").read_text(encoding="utf-8")
    syncs = {
        tuple(re.findall(r"--extra (\S+)", line))
        for line in release.splitlines()
        if "uv sync --locked --no-editable" in line
    }
    assert syncs == {audit.PYTHON_GRAPHS["desktop-bundle"]}, syncs
    assert audit.PYTHON_GRAPHS["wheel"] == ()


def test_every_lock_under_deploy_docker_is_an_audited_graph() -> None:
    """An image installs its npm tools from a lock under deploy/docker/ (tools/ci/supply_chain.py
    holds that), so each such lock is a graph that ships, and the audit reads every one."""
    locks = {
        path.relative_to(REPO).as_posix()
        for path in (REPO / "deploy/docker").rglob("package-lock.json")
        if "node_modules" not in path.parts
    }
    tree = supply_chain.read_tree(REPO)
    locks |= {
        rel.rpartition("/")[0] + "/package-lock.json" for rel in supply_chain.image_manifests(tree)
    }
    assert locks, "no image installs an npm lock any more: this test holds nothing"
    audited = {lock for _graph, lock in audit.npm_graphs(REPO)}
    assert "package-lock.json" in audited
    assert locks <= audited, sorted(locks - audited)


def _job(workflow: str, job: str) -> dict:
    doc = yaml.safe_load((REPO / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    return doc["jobs"][job]


@pytest.mark.parametrize("workflow", ["ci.yml", "breadth.yml"])
def test_the_audit_job_runs_the_command_and_nothing_masks_it(workflow: str) -> None:
    job = _job(workflow, "audit")
    assert "continue-on-error" not in job, workflow
    runs = [str(s.get("run", "")) for s in job["steps"]]
    command = [r for r in runs if "-m tools.ci audit run" in r]
    assert len(command) == 1, runs
    assert "|| true" not in command[0] and "|| :" not in command[0]
    assert not any(s.get("continue-on-error") for s in job["steps"])
    assert not any("pip_audit ||" in r or "npm audit ||" in r for r in runs)


def test_the_pull_request_audit_waits_on_tree_check_like_every_gated_job() -> None:
    job = _job("ci.yml", "audit")
    assert job["needs"] == ["tree-check"] or job["needs"] == "tree-check"
    assert job["if"] == "${{ !cancelled() && needs.tree-check.outputs.tested != 'true' }}"
