"""The source-tree check split from ``tests/test_ci_catalogue.py``.

It runs once per CI run, in the rails job.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tools.ci import catalogue, jobs, run


@pytest.mark.parametrize("module", [catalogue, jobs, run], ids=lambda m: m.__name__)
def test_it_imports_only_the_standard_library(module) -> None:
    """ci.yml's shard planner runs ``python -m tools.ci`` on the runner image's own python3,
    before anything is installed, and that imports these modules."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }
    imported.discard("__future__")
    assert imported, "the scan read no imports"
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)


def _ci() -> dict:
    import yaml

    path = Path(catalogue.REPO) / ".github/workflows/ci.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_run_jobs_workflow_env_is_ci_ymls() -> None:
    """A step run here gets the env ci.yml gives every job, no more and no less."""
    from tools.ci import jobs

    assert jobs.WORKFLOW_ENV == {str(k): str(v) for k, v in (_ci().get("env") or {}).items()}


#: The variables a GitHub-hosted runner gives every step that run-job gives a step too. CI is
#: the one a tool here reads to behave as it does on CI (the e2e job's Playwright config takes
#: its JSON report from it). GITHUB_ACTIONS is not one: it says the run is on GitHub, which
#: run-job reads (Host.on_github) and a local run is not; nor is any GITHUB_* or RUNNER_*
#: value, which names a run that does not exist here.
_RUNNER_PROVIDED = {"CI": "true"}


def test_the_runner_variables_run_job_sets_are_the_declared_ones() -> None:
    from tools.ci import jobs

    assert jobs.RUNNER_ENV == _RUNNER_PROVIDED
    assert not any(name.startswith(("GITHUB_", "RUNNER_")) for name in jobs.RUNNER_ENV)
    assert not set(jobs.RUNNER_ENV) & set(jobs.WORKFLOW_ENV)
    assert not any(run.runner_owns(name) for name in jobs.RUNNER_ENV)


def test_each_install_run_job_runs_is_one_ci_yml_runs() -> None:
    """The toolchain installs run-job runs are ci.yml's, word for word."""
    import shlex

    from tools.ci import jobs

    ran: set[tuple[str, ...]] = set()
    for job in (_ci().get("jobs") or {}).values():
        for step in job.get("steps") or []:
            for line in str(step.get("run", "")).replace("\\\n", " ").splitlines():
                try:
                    words = shlex.split(line, comments=True)
                except ValueError:  # a line no argv could be, such as an unmatched quote
                    continue
                if words:
                    ran.add(tuple(words))
    for tool, argv in jobs.INSTALL_ARGV.items():
        assert argv in ran, f"{tool}: {shlex.join(argv)} is no run: line of ci.yml"
    assert {tool for _, tool in catalogue.INSTALLS} == set(jobs.INSTALL_ARGV)
