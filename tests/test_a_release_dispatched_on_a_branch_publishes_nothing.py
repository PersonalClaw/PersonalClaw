"""A release dispatched on a branch publishes nothing, and no workflow declares an input it ignores.

``release.yml`` keeps ``workflow_dispatch`` for the rehearsal of its tag-only jobs before a tag
exists: ``gh workflow run release.yml --ref main``. That is safe only because of how the jobs
are wired. Every job reads the ref it runs on (``$GITHUB_REF_NAME``); ``build``'s first step
classifies that ref with ``scripts/release_tags.py``, which refuses a branch; every job that can
publish needs ``build``; and the jobs that need nothing, and so do run on a branch, hold no write
permission, no environment and no secret, and tell electron-builder never to publish.

The dispatch used to declare a ``ref`` input that no step read. A dispatch that named a tag
there built the branch it was run on, and a rehearsal and a release looked the same in the form.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_RELEASE = _WORKFLOWS / "release.yml"


def _load(path: Path) -> dict:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), path
    return workflow


def _triggers(workflow: dict) -> dict:
    # YAML 1.1 reads a bare `on:` key as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    return triggers if isinstance(triggers, dict) else {}


def _dispatch_inputs(workflow: dict) -> dict:
    dispatch = _triggers(workflow).get("workflow_dispatch") or {}
    return dispatch.get("inputs") or {}


def _code(path: Path) -> str:
    """The workflow without its whole-line comments: what its steps and expressions do."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _needs(job: dict) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def _upstream(jobs: dict, name: str) -> set[str]:
    """Every job *name* waits on, directly or through another job."""
    seen: set[str] = set()
    pending = _needs(jobs[name])
    while pending:
        job = pending.pop()
        if job not in seen:
            seen.add(job)
            pending += _needs(jobs[job])
    return seen


def _can_publish(workflow: dict, job: dict) -> list[str]:
    """What lets *job* change something outside its run: a write grant, an environment, a secret."""
    held = job.get("permissions", workflow.get("permissions"))
    reasons = []
    if held == "write-all" or (isinstance(held, dict) and "write" in held.values()):
        reasons.append(f"permissions {held}")
    if job.get("environment"):
        reasons.append(f"environment {job['environment']}")
    if "secrets." in json.dumps(job):
        reasons.append("a secret")
    return reasons


def test_every_dispatch_input_a_workflow_declares_is_read() -> None:
    declared = {
        (path.name, name): path
        for path in sorted(_WORKFLOWS.glob("*.yml"))
        for name in _dispatch_inputs(_load(path))
    }
    # Vacuity floor: the scan reads inputs where there are some.
    assert ("clean-machine-walkthroughs.yml", "which") in declared, sorted(declared)
    unread = [
        f"{workflow}: inputs.{name}"
        for (workflow, name), path in declared.items()
        if not re.search(rf"\binputs\.{re.escape(name)}\b", _code(path))
    ]
    assert not unread, (
        "these dispatch inputs are declared and never read, so a run that sets one does not do "
        f"what the form says: {unread}"
    )


def test_the_release_can_still_be_dispatched_and_asks_for_nothing() -> None:
    triggers = _triggers(_load(_RELEASE))
    assert "workflow_dispatch" in triggers, "the no-publish rehearsal is a dispatch on a branch"
    assert _dispatch_inputs(_load(_RELEASE)) == {}, "every job builds the ref the run is on"
    assert triggers["push"] == {"tags": ["v*"]}


def test_build_refuses_a_branch_at_its_first_step(tmp_path: Path) -> None:
    """The step itself, run as a dispatch on `main` runs it: it fails, and hands on no version."""
    build = _load(_RELEASE)["jobs"]["build"]
    steps = [
        step
        for step in build["steps"]
        if not str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    first = steps[0]
    assert first.get("id") == "ver" and "scripts/release_tags.py" in first["run"], first
    assert '--ref "$GITHUB_REF_NAME"' in first["run"]
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    env = {**os.environ, "GITHUB_REF_NAME": "main", "GITHUB_OUTPUT": str(output)}
    result = subprocess.run(
        ["bash", "-e", "-c", first["run"]], cwd=_ROOT, env=env, capture_output=True, text=True
    )
    assert result.returncode != 0, result.stdout
    assert "version=" not in output.read_text(encoding="utf-8")
    # Not vacuous: the same step hands on the version for a release tag.
    tagged = subprocess.run(
        ["bash", "-e", "-c", first["run"]],
        cwd=_ROOT,
        env={**env, "GITHUB_REF_NAME": "v0.3.0"},
        capture_output=True,
        text=True,
    )
    assert tagged.returncode == 0, tagged.stderr
    assert "version=0.3.0" in output.read_text(encoding="utf-8").splitlines()


def test_every_job_that_can_publish_needs_build() -> None:
    workflow = _load(_RELEASE)
    jobs = workflow["jobs"]
    publishing = {name: _can_publish(workflow, job) for name, job in jobs.items()}
    publishing = {name: why for name, why in publishing.items() if why}
    # Vacuity floor: PyPI, the images, the Release, the attestation and the website nudge.
    assert {"pypi", "pypi-client", "images", "notes", "attest", "website-follow"} <= set(
        publishing
    ), publishing
    ungated = [name for name in publishing if "build" not in _upstream(jobs, name)]
    assert not ungated, (
        f"these jobs can publish and do not wait on `build`, so a dispatch on a branch runs "
        f"them: {ungated}"
    )


def test_what_a_dispatch_on_a_branch_runs_can_publish_nothing() -> None:
    workflow = _load(_RELEASE)
    jobs = workflow["jobs"]
    runs_on_a_branch = sorted(name for name, job in jobs.items() if not _needs(job))
    assert runs_on_a_branch == ["build", "desktop-linux", "desktop-mac", "gateway-dashboard"]
    holding = {name: _can_publish(workflow, jobs[name]) for name in runs_on_a_branch}
    assert not any(holding.values()), holding
    scripts = json.loads((_ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    for script in ("dist", "dist:linux"):
        assert "--publish never" in scripts["scripts"][script], script
