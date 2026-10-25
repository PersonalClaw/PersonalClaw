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


@pytest.mark.parametrize("workflow", ["breadth.yml"])
def test_the_audit_job_runs_the_command_and_nothing_masks_it(workflow: str) -> None:
    job = _job(workflow, "audit")
    assert "continue-on-error" not in job, workflow
    runs = [str(s.get("run", "")) for s in job["steps"]]
    command = [r for r in runs if "-m tools.ci audit run" in r]
    assert len(command) == 1, runs
    assert "|| true" not in command[0] and "|| :" not in command[0]
    assert not any(s.get("continue-on-error") for s in job["steps"])
    assert not any("pip_audit ||" in r or "npm audit ||" in r for r in runs)


def test_the_catalogues_audit_runs_the_command_once() -> None:
    """The tier workflows run the catalogue's audit through run-job, whose steps are argvs (no
    shell to mask an exit) and whose stage job the CI contract table holds to its two lines, so
    a red audit fails its entry."""
    from tools.ci import catalogue

    job = catalogue.read(REPO).job("audit")
    want = ("-m", "tools.ci", "audit", "run")
    commands = [
        s.argv
        for s in job.command
        if any(s.argv[i : i + len(want)] == want for i in range(len(s.argv)))
    ]
    assert len(commands) == 1, [s.argv for s in job.command]
