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
    """merge.yml's shard plan and every orchestration job of the tier workflows run
    ``python -m tools.ci`` on the runner image's own python3, before anything is installed, and
    that imports these modules."""
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


def _workflow(name: str) -> dict:
    import yaml

    path = Path(catalogue.REPO) / ".github/workflows" / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["pr.yml", "merge.yml"])
def test_run_jobs_workflow_env_is_the_tier_workflows(name: str) -> None:
    """A step run here gets the env each tier workflow gives every job, no more and no less."""
    from tools.ci import jobs

    env = _workflow(name).get("env") or {}
    assert jobs.WORKFLOW_ENV == {str(k): str(v) for k, v in env.items()}


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


def test_each_install_word_the_catalogue_names_is_one_run_job_runs() -> None:
    """run-job runs each toolchain install itself, in the tier workflows and here alike, so its
    INSTALL_ARGV is the one definition, and each install word the catalogue knows is one."""
    from tools.ci import jobs

    assert {tool for _, tool in catalogue.INSTALLS} == set(jobs.INSTALL_ARGV)


#: The jobs whose suite runs ``--strict-state`` (tests/state_leak_guard.py, #3949). None yet:
#: the merge tier's Ubuntu suite joins once 5 consecutive merge runs show ``0 strict`` in each of
#: its shards' logs, and the macOS suite once 7 days of its merge runs do.
STRICT_STATE_JOBS: frozenset[str] = frozenset()


def _strict_state_jobs(cat: catalogue.Catalogue) -> set[str]:
    return {
        job.id
        for job in cat.jobs
        for step in job.command
        if "--strict-state" in (catalogue.pytest_args(step.argv) or [])
    }


def test_strict_state_runs_in_the_jobs_named_for_it() -> None:
    assert _strict_state_jobs(catalogue.read()) == STRICT_STATE_JOBS


_UBUNTU_STEP_END = '"--junitxml=reports/junit-test-shard-{shard}.xml"] }'


def test_a_suite_that_gains_strict_state_is_seen() -> None:
    text = (catalogue.REPO / catalogue.CATALOGUE).read_text(encoding="utf-8")
    assert text.count(_UBUNTU_STEP_END) == 1, _UBUNTU_STEP_END
    planted = text.replace(
        _UBUNTU_STEP_END, _UBUNTU_STEP_END.replace("] }", ', "--strict-state"] }')
    )
    assert _strict_state_jobs(catalogue.load(planted)) == {"python-suite"}
