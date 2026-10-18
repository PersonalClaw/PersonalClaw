"""Every setup-uv step that keeps uv's cache between runs prunes it before saving.

setup-uv saves uv's cache (``UV_CACHE_DIR``) at the end of a job and restores it at the start of
the next one. Pruned for CI (``uv cache prune --ci``, which setup-uv runs when ``prune-cache`` is
true), the cache keeps only the wheels uv had to build from source. Unpruned, it keeps every wheel
the job downloaded, and every later run restores all of them before anything else: a macOS test
job spent 14-20 s restoring a 261 MB cache so that ``uv sync`` could skip downloads it otherwise
makes in about 8 s.

setup-uv's own default changed under us: ``prune-cache`` defaulted to true up to v7 and defaults
to false from v10. So a version bump alone turned pruning off and changed the cache key, and the
next run saved the full cache. The rule does not lean on the default: every setup-uv step either
turns caching off (``enable-cache: false``) or sets ``prune-cache: true`` itself. ``enable-cache``
left out means ``auto``, which caches on GitHub's runners for every event but a release, a tag
push, ``pull_request_target`` and ``workflow_run``, so it is held to the rule too.

The planted workflows below show the rule fails on a caching step with no ``prune-cache``, with
``prune-cache: false`` as a boolean and as a string, and with ``enable-cache`` left out or
``auto``, and passes on a pruned step and on one that caches nothing.
"""

from __future__ import annotations

import copy

import pytest
import yaml
from rails_repo import REPO

ROOT = REPO
WORKFLOWS = ROOT / ".github" / "workflows"


def _flag(value: object) -> str:
    """A ``with:`` input as setup-uv reads it: YAML's true is the string "true"."""
    return str(value).strip().lower()


def setup_uv_steps(workflow: dict) -> list[tuple[str, dict]]:
    """``(job id, step)`` for every setup-uv step in one parsed workflow."""
    found = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        for step in (job or {}).get("steps") or []:
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
                found.append((job_id, step))
    return found


def unpruned_caches(name: str, workflow: dict) -> list[str]:
    """One problem line for each setup-uv step that would save uv's cache unpruned."""
    problems = []
    for job_id, step in setup_uv_steps(workflow):
        given = step.get("with") or {}
        enabled = _flag(given.get("enable-cache", "auto"))
        if enabled == "false":
            continue
        if _flag(given.get("prune-cache", "")) != "true":
            where = f"{name} {job_id} {step.get('name') or step['uses'].split('@')[0]!r}"
            problems.append(
                f"{where}: keeps uv's cache (enable-cache: {enabled}) without "
                "prune-cache: true, so it saves every downloaded wheel and every "
                "run restores them"
            )
    return problems


def _workflows() -> dict[str, dict]:
    paths = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
    return {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in paths}


def test_the_rule_reads_every_setup_uv_step_the_workflows_have():
    """Vacuity floor: ci.yml and breadth.yml install uv in most of their jobs, and the release,
    Nix and headless-smoke workflows each install it too."""
    found = {name: len(setup_uv_steps(wf)) for name, wf in _workflows().items()}
    assert found.get("ci.yml", 0) >= 10 and found.get("breadth.yml", 0) >= 5, found
    assert {"release.yml", "nix.yml", "headless-run-smoke.yml"} <= {
        n for n, c in found.items() if c
    }


def test_every_setup_uv_step_that_caches_prunes_first():
    problems = [line for name, wf in _workflows().items() for line in unpruned_caches(name, wf)]
    assert not problems, "\n".join(problems)


# -- planted workflows ------------------------------------------------------------------------

SETUP_UV = "astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884"
PLANTED = {
    "jobs": {
        "build": {
            "steps": [
                {
                    "name": "Install uv",
                    "uses": SETUP_UV,
                    "with": {"version": "0.13.1", "enable-cache": True, "prune-cache": True},
                }
            ]
        }
    }
}


def _planted(**with_inputs: object) -> dict:
    workflow = copy.deepcopy(PLANTED)
    given = workflow["jobs"]["build"]["steps"][0]["with"]
    for key, value in with_inputs.items():
        if value is None:
            given.pop(key.replace("_", "-"), None)
        else:
            given[key.replace("_", "-")] = value
    return workflow


def test_the_planted_baseline_passes():
    assert unpruned_caches("planted.yml", _planted()) == []


@pytest.mark.parametrize(
    ("inputs", "shown"),
    [
        ({"prune_cache": None}, "enable-cache: true"),
        ({"prune_cache": False}, "enable-cache: true"),
        ({"prune_cache": "false"}, "enable-cache: true"),
        ({"enable_cache": None, "prune_cache": None}, "enable-cache: auto"),
        ({"enable_cache": "auto", "prune_cache": None}, "enable-cache: auto"),
    ],
    ids=[
        "no prune-cache",
        "prune-cache false",
        "prune-cache 'false'",
        "enable-cache left out",
        "enable-cache auto",
    ],
)
def test_a_planted_step_that_saves_the_cache_unpruned_fails(inputs, shown):
    problems = unpruned_caches("planted.yml", _planted(**inputs))
    assert len(problems) == 1 and problems[0].startswith("planted.yml build 'Install uv'"), problems
    assert shown in problems[0] and "prune-cache: true" in problems[0]


def test_a_planted_step_with_no_inputs_at_all_fails():
    """No ``with:`` block is ``enable-cache: auto`` with setup-uv's own ``prune-cache``."""
    workflow = {"jobs": {"build": {"steps": [{"uses": SETUP_UV}]}}}
    problems = unpruned_caches("planted.yml", workflow)
    assert len(problems) == 1 and "enable-cache: auto" in problems[0], problems


@pytest.mark.parametrize(
    "inputs",
    [
        {"enable_cache": False, "prune_cache": None},
        {"enable_cache": "false", "prune_cache": None},
        {"prune_cache": "true"},
    ],
    ids=["caching off", "caching off as a string", "prune-cache as a string"],
)
def test_a_planted_step_that_caches_nothing_or_prunes_passes(inputs):
    assert unpruned_caches("planted.yml", _planted(**inputs)) == []


def test_a_step_of_another_action_is_not_read():
    workflow = {
        "jobs": {"build": {"steps": [{"uses": "actions/setup-node@x", "with": {"cache": "npm"}}]}}
    }
    assert setup_uv_steps(workflow) == [] and unpruned_caches("planted.yml", workflow) == []
