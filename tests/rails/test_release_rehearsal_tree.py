"""The release rehearsal builds every release artifact, publishes nothing, and cannot drift (#3942).

``.github/workflows/release-rehearsal.yml`` runs weekly on main and calls
``release-artifacts.yml``, the build half of ``release.yml`` as a reusable workflow. This holds:

- **nothing is published.** Neither file names an environment, asks for more than reading the
  tree (no ``id-token``, ``packages`` or ``contents: write``), passes or reads a secret, logs in to
  a registry, pushes an image, or runs a publishing step (PyPI, a GitHub Release, a tag, an
  attestation). Every file goes up only as a workflow artifact kept at most 7 days.
- **it runs on main only.** It triggers on its schedule and on a dispatch, nothing else, and its
  first job refuses any ref but main and any repository but this one; the build waits on it.
- **it cannot drift.** Until ``release.yml`` calls ``release-artifacts.yml`` (#3911), each job
  copied from it is held to ``release.yml``'s own, step for step: the same names, actions, inputs
  and commands, with only the tag it reads (``$RELEASE_REF`` for ``$GITHUB_REF_NAME``) and the
  artifacts' retention added. The image job is the one built differently, to an OCI layout with
  no push; its matrix, its builder and the build's inputs are held to the release's.
- **its manifest job runs.** The job's commands, from its write on, run as the runner runs them in
  a folder shaped like the release's, and name each image by the repository release.yml pushes it
  to. The first rehearsal on the runner found the write refusing the images' bare names.

Each rule is planted to fail.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import source_index
import yaml
from rails_repo import REPO

WORKFLOWS = REPO / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
ARTIFACTS = WORKFLOWS / "release-artifacts.yml"
REHEARSAL = WORKFLOWS / "release-rehearsal.yml"

#: The jobs copied from release.yml, held to it step for step.
COPIED = ("build", "gateway-dashboard", "desktop-mac", "desktop-linux")
#: The one permission either workflow may hold.
READ_ONLY = {"contents": "read"}
#: Actions that publish, log in or attest.
PUBLISHING_ACTIONS = (
    "pypa/gh-action-pypi-publish",
    "softprops/action-gh-release",
    "ncipollo/release-action",
    "docker/login-action",
    "actions/attest-build-provenance",
    "actions/attest-sbom",
    "actions/attest",
)
#: Commands that publish, push or tag.
PUBLISHING_COMMANDS = re.compile(
    r"\b(gh\s+release|gh\s+api\s+-X\s*(POST|PUT|PATCH)|twine\s+upload|uv\s+publish|npm\s+publish|"
    r"docker\s+(image\s+)?push|imagetools\s+create|git\s+push|git\s+tag|oras\s+push|"
    r"crane\s+(push|copy)|skopeo\s+copy)\b"
)
MAX_RETENTION_DAYS = 7


def _load(text: str) -> dict[str, Any]:
    doc = yaml.safe_load(text)
    # YAML 1.1 reads the `on:` key as True.
    if True in doc:
        doc["on"] = doc.pop(True)
    return doc


def _steps(job: dict) -> list[dict]:
    return list(job.get("steps") or [])


# ── nothing is published ───────────────────────────────────────────────────────────────────────


def publish_problems(name: str, text: str) -> list[str]:
    """Everything in the workflow *text* that could publish, or reach what publishing needs."""
    doc = _load(text)
    out: list[str] = []
    if "secrets." in text or "secrets: inherit" in text:
        out.append(f"{name}: reads or passes a secret")
    perms = doc.get("permissions")
    if perms != READ_ONLY:
        out.append(f"{name}: the workflow's permissions are {perms!r}, not contents: read alone")
    for job_id, job in (doc.get("jobs") or {}).items():
        where = f"{name}: job {job_id}"
        if "environment" in job:
            out.append(f"{where}: names environment {job['environment']!r}")
        if "permissions" in job and job["permissions"] != READ_ONLY:
            out.append(f"{where}: asks for permissions {job['permissions']!r}")
        if job.get("secrets"):
            out.append(f"{where}: passes secrets")
        for step in _steps(job):
            uses = str(step.get("uses") or "")
            label = f"{where}, step {step.get('name') or uses or '?'}"
            if any(uses.startswith(f"{a}@") for a in PUBLISHING_ACTIONS):
                out.append(f"{label}: uses {uses.split('@')[0]}, which publishes or logs in")
            run = str(step.get("run") or "")
            found = PUBLISHING_COMMANDS.search(run)
            if found:
                out.append(f"{label}: runs {found.group(0)!r}, which publishes")
            inputs = step.get("with") or {}
            if uses.startswith("docker/build-push-action@"):
                if inputs.get("push") not in (False, "false"):
                    out.append(f"{label}: builds with push {inputs.get('push')!r}, not false")
                if "registry" in str(inputs.get("outputs", "")) or "push=true" in str(
                    inputs.get("outputs", "")
                ):
                    out.append(f"{label}: its outputs reach a registry")
            if uses.startswith("actions/upload-artifact@"):
                days = inputs.get("retention-days")
                if not isinstance(days, int) or days > MAX_RETENTION_DAYS:
                    out.append(
                        f"{label}: keeps its artifact {days!r} days,"
                        f" not at most {MAX_RETENTION_DAYS}"
                    )
    return out


# ── main only ──────────────────────────────────────────────────────────────────────────────────


def trigger_problems(text: str) -> list[str]:
    doc = _load(text)
    out: list[str] = []
    triggers = set(doc.get("on") or {})
    if triggers != {"schedule", "workflow_dispatch"}:
        out.append(
            f"the rehearsal triggers on {sorted(triggers)}, not its schedule and a dispatch alone"
        )
    jobs = doc.get("jobs") or {}
    gate = jobs.get("version") or {}
    condition = " ".join(str(gate.get("if") or "").split())
    for needed in (
        "github.ref == 'refs/heads/main'",
        "github.repository == 'PersonalClaw/PersonalClaw'",
    ):
        if needed not in condition:
            out.append(f"the rehearsal's first job runs without {needed}")
    for job_id, job in jobs.items():
        if job_id != "version" and "version" not in (job.get("needs") or []):
            out.append(f"job {job_id} does not wait on the main-only check")
    calls = [j for j in jobs.values() if j.get("uses")]
    if [j.get("uses") for j in calls] != ["./.github/workflows/release-artifacts.yml"]:
        out.append("the rehearsal does not call release-artifacts.yml, once")
    return out


# ── no drift from release.yml ──────────────────────────────────────────────────────────────────


def _normalised(job: dict) -> dict:
    """A copied job as release.yml would hold it: its tag variable, and no artifact retention."""
    job = copy.deepcopy(job)
    for step in _steps(job):
        for key in ("run",):
            if isinstance(step.get(key), str):
                step[key] = step[key].replace("$RELEASE_REF", "$GITHUB_REF_NAME")
        for value in (step.get("env") or {}).values():
            if isinstance(value, str) and "RELEASE_REF" in value:
                raise AssertionError("RELEASE_REF in a step's env")
        if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
            (step.get("with") or {}).pop("retention-days", None)
    return job


def drift_problems(release_text: str, artifacts_text: str) -> list[str]:
    release, artifacts = _load(release_text), _load(artifacts_text)
    if any(
        j.get("uses") == "./.github/workflows/release-artifacts.yml"
        for j in (release.get("jobs") or {}).values()
    ):
        return []  # release.yml builds through it: there is one copy, and nothing to drift.
    out: list[str] = []
    rel_jobs, art_jobs = release.get("jobs") or {}, artifacts.get("jobs") or {}
    for job_id in COPIED:
        if job_id not in art_jobs:
            out.append(f"release-artifacts.yml has no job {job_id}")
            continue
        mine, theirs = _normalised(art_jobs[job_id]), rel_jobs.get(job_id) or {}
        for key in ("runs-on", "timeout-minutes", "needs", "strategy", "outputs"):
            if mine.get(key) != theirs.get(key):
                out.append(
                    f"{job_id}: {key} is {mine.get(key)!r}, release.yml's is {theirs.get(key)!r}"
                )
        a, b = _steps(mine), _steps(theirs)
        for i in range(max(len(a), len(b))):
            x, y = (a[i] if i < len(a) else None), (b[i] if i < len(b) else None)
            if x != y:
                name = (x or y or {}).get("name") or (x or y or {}).get("uses")
                out.append(f"{job_id}: step {i + 1} ({name}) is not release.yml's")
    images, rel_images = art_jobs.get("images") or {}, rel_jobs.get("images") or {}
    if images.get("strategy") != rel_images.get("strategy"):
        out.append("images: the matrix is not release.yml's")
    for action in ("docker/setup-qemu-action@", "docker/setup-buildx-action@"):
        mine = [s for s in _steps(images) if str(s.get("uses", "")).startswith(action)]
        theirs = [s for s in _steps(rel_images) if str(s.get("uses", "")).startswith(action)]
        if mine != theirs:
            out.append(f"images: {action.rstrip('@')} is not release.yml's")
    builds = [
        s for s in _steps(images) if str(s.get("uses", "")).startswith("docker/build-push-action@")
    ]
    rel_builds = [
        s
        for s in _steps(rel_images)
        if str(s.get("uses", "")).startswith("docker/build-push-action@")
    ]
    drop = ("push", "tags", "outputs")
    if [s.get("uses") for s in builds] != [s.get("uses") for s in rel_builds] or [
        {k: v for k, v in (s.get("with") or {}).items() if k not in drop} for s in builds
    ] != [{k: v for k, v in (s.get("with") or {}).items() if k not in drop} for s in rel_builds]:
        out.append("images: the build's action or inputs are not release.yml's")
    return out


def _texts() -> tuple[str, str, str]:
    files = set(source_index.workflow_files())
    for path in (RELEASE, ARTIFACTS, REHEARSAL):
        assert path in files, f"{path.name} is not under .github/workflows"
    return source_index.text(RELEASE), source_index.text(ARTIFACTS), source_index.text(REHEARSAL)


def test_neither_the_rehearsal_nor_the_artifacts_workflow_can_publish() -> None:
    _, artifacts, rehearsal = _texts()
    assert publish_problems("release-artifacts.yml", artifacts) == []
    assert publish_problems("release-rehearsal.yml", rehearsal) == []
    jobs = _load(artifacts)["jobs"]
    assert {"build", "images", "desktop-mac", "desktop-linux", "manifest"} <= set(
        jobs
    ), "every artifact is built (vacuity floor)"


def test_the_rehearsal_runs_on_main_only_and_calls_the_artifacts_workflow() -> None:
    _, _, rehearsal = _texts()
    assert trigger_problems(rehearsal) == []


def test_the_copied_jobs_are_release_ymls_step_for_step() -> None:
    release, artifacts, _ = _texts()
    assert drift_problems(release, artifacts) == []


# ── its manifest job runs ──────────────────────────────────────────────────────────────────────

#: Where the manifest job's own commands start. The steps before it fetch and stage the run's
#: artifacts, which the test stands in for with a folder shaped like the release's.
FIRST_MANIFEST_STEP = "Write the digest-bound manifest"
#: The repository release.yml pushes each image to, with no tag.
_RELEASE_REPOSITORY = re.compile(r"^\s+IMAGE: (\S+)\$\{\{ matrix\.name \}\}$", re.M)
_IMAGE_ARG = re.compile(r"--image\s+([^=\s]+)=(\S+)")
_STEP_OUTPUT = re.compile(r"\$\{\{ steps\.([a-z_-]+)\.outputs\.([a-z_-]+) \}\}")


def _oci_index(name: str) -> bytes:
    """A two-platform index, the shape tools/ci/oci_layout.py reads out of an image's layout."""
    manifests = [
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": "sha256:" + hashlib.sha256(f"{name} {arch}".encode()).hexdigest(),
            "size": 1,
            "platform": {"os": "linux", "architecture": arch},
        }
        for arch in ("amd64", "arm64")
    ]
    index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json"}
    return json.dumps({**index, "manifests": manifests}).encode()


def manifest_run_problems(release_text: str, artifacts_text: str, work: Path) -> list[str]:
    """Run the manifest job's commands, from its write on, in ``work``.

    Each step runs as the runner runs a ``run:`` step (``bash -e``), on a staged folder and an
    index for each image it names, with an earlier step's outputs passed on as the workflow passes
    them. Every step names the images by the repositories release.yml pushes them to.
    """
    jobs = _load(artifacts_text)["jobs"]
    steps = _steps(jobs["manifest"])
    names = [s.get("name") for s in steps]
    if FIRST_MANIFEST_STEP not in names:
        return [f"manifest: no step named {FIRST_MANIFEST_STEP!r}"]
    runs = [s for s in steps[names.index(FIRST_MANIFEST_STEP) :] if "run" in s]
    repository = _RELEASE_REPOSITORY.search(release_text)
    if not repository:
        return ["release.yml: no image repository to hold the manifest's names to"]
    include = jobs["images"]["strategy"]["matrix"]["include"]
    pushed = sorted(repository.group(1) + m["name"] for m in include)
    out = []
    for step in runs:
        named = sorted({n for n, _ in _IMAGE_ARG.findall(step["run"])})
        if named and named != pushed:
            out.append(f"manifest: {step['name']!r} names the images {named}, not {pushed}")
    # A staged folder (with a wheel, which the planted control changes) and each index named.
    (work / "staged").mkdir()
    (work / "staged" / "personalclaw-0.0.0-py3-none-any.whl").write_bytes(b"wheel bytes")
    (work / "staged" / "personalclaw-0.0.0.tar.gz").write_bytes(b"sdist bytes")
    for step in runs:
        for name, index in _IMAGE_ARG.findall(step["run"]):
            (work / index).parent.mkdir(parents=True, exist_ok=True)
            (work / index).write_bytes(_oci_index(name))
    (work / "tools").symlink_to(REPO / "tools", target_is_directory=True)
    github_output = work / "github-output"
    outputs: dict[str, dict[str, str]] = {}
    for step in runs:
        env = {
            # `python3` is this run's interpreter; the tool imports only the standard library.
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(work),
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_OUTPUT": str(github_output),
        }
        for key, value in (step.get("env") or {}).items():
            used = _STEP_OUTPUT.fullmatch(str(value))
            if not used or used.group(2) not in outputs.get(used.group(1), {}):
                return out + [f"manifest: {step['name']!r} sets {key} to {value!r}, not passed on"]
            env[key] = outputs[used.group(1)][used.group(2)]
        if "${{" in step["run"]:
            return out + [f"manifest: {step['name']!r} runs an expression this run cannot fill"]
        github_output.write_text("")
        script = work / "step.sh"
        script.write_text(step["run"])
        result = subprocess.run(
            ["bash", "-e", str(script)],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            said = " ".join((result.stdout + result.stderr).strip().splitlines()[-3:])
            return out + [f"manifest: {step['name']!r} exited {result.returncode}: {said}"]
        if step.get("id"):
            lines = github_output.read_text().splitlines()
            outputs[step["id"]] = dict(line.split("=", 1) for line in lines if "=" in line)
    return out


def test_the_manifest_jobs_commands_run_on_a_release_shaped_folder(tmp_path: Path) -> None:
    release, artifacts, _ = _texts()
    assert manifest_run_problems(release, artifacts, tmp_path) == []


# ── each rule can fail ──────────────────────────────────────────────────────────────────────────

_MINIMAL = """
name: x
on:
  workflow_call:
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/upload-artifact@abc
        with:
          name: dist
          path: dist/
          retention-days: 3
"""


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (
            (
                "    runs-on: ubuntu-24.04\n",
                "    runs-on: ubuntu-24.04\n    environment: release\n",
            ),
            "names environment 'release'",
        ),
        (
            ("permissions:\n  contents: read\n", "permissions:\n  contents: write\n"),
            "not contents: read alone",
        ),
        (
            (
                "    runs-on: ubuntu-24.04\n",
                "    runs-on: ubuntu-24.04\n    permissions:\n      id-token: write\n",
            ),
            "asks for permissions",
        ),
        (
            (
                "    runs-on: ubuntu-24.04\n",
                "    runs-on: ubuntu-24.04\n    permissions:\n      packages: write\n",
            ),
            "asks for permissions",
        ),
        (
            ("    steps:\n", "    steps:\n      - uses: pypa/gh-action-pypi-publish@abc\n"),
            "which publishes or logs in",
        ),
        (
            ("    steps:\n", "    steps:\n      - uses: docker/login-action@abc\n"),
            "which publishes or logs in",
        ),
        (
            ("    steps:\n", "    steps:\n      - run: gh release create v1 dist/*\n"),
            "'gh release'",
        ),
        (("    steps:\n", "    steps:\n      - run: docker push ghcr.io/x/y:1\n"), "'docker push'"),
        (("    steps:\n", "    steps:\n      - run: git push origin v1\n"), "'git push'"),
        (
            (
                "    steps:\n",
                "    steps:\n      - uses: docker/build-push-action@abc\n"
                "        with:\n          push: true\n",
            ),
            "push True, not false",
        ),
        (
            (
                "    steps:\n",
                "    steps:\n      - env:\n          T: ${{ secrets.PYPI }}\n        run: echo\n",
            ),
            "reads or passes a secret",
        ),
        (
            ("          retention-days: 3\n", "          retention-days: 90\n"),
            "keeps its artifact 90 days",
        ),
        (("          retention-days: 3\n", ""), "keeps its artifact None days"),
    ],
)
def test_each_publishing_path_is_caught(change: tuple[str, str], said: str) -> None:
    assert publish_problems("planted", _MINIMAL) == []
    planted = _MINIMAL.replace(*change, 1)
    assert planted != _MINIMAL
    assert any(said in p for p in publish_problems("planted", planted)), publish_problems(
        "planted", planted
    )


def test_a_rehearsal_on_another_trigger_or_ref_is_caught() -> None:
    _, _, rehearsal = _texts()
    for planted, said in [
        (
            rehearsal.replace("  workflow_dispatch:\n", "  workflow_dispatch:\n  push:\n", 1),
            "not its schedule and a dispatch alone",
        ),
        (
            rehearsal.replace("github.ref == 'refs/heads/main' && ", "", 1),
            "runs without github.ref == 'refs/heads/main'",
        ),
        (
            rehearsal.replace("    needs: [version]\n", "", 1),
            "does not wait on the main-only check",
        ),
    ]:
        assert planted != rehearsal
        assert any(said in p for p in trigger_problems(planted)), trigger_problems(planted)


def test_a_build_step_changed_on_one_side_is_drift() -> None:
    release, artifacts, _ = _texts()
    planted = artifacts.replace("npm run typecheck:web", "npm run typecheck:web --if-present", 1)
    assert planted != artifacts
    assert any(
        p.startswith("build: step") for p in drift_problems(release, planted)
    ), drift_problems(release, planted)
    planted = artifacts.replace("platforms: linux/amd64,linux/arm64", "platforms: linux/amd64", 1)
    assert "images: the build's action or inputs are not release.yml's" in drift_problems(
        release, planted
    )
    called = release.replace(
        "  build:\n", "  build:\n    uses: ./.github/workflows/release-artifacts.yml\n", 1
    )
    assert drift_problems(called, planted) == [], "once release.yml calls it, there is one copy"


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (
            # The first rehearsal's defect: the images by their bare names, in every step.
            (
                "--image ghcr.io/personalclaw/personalclaw-gateway=",
                "--image personalclaw-gateway=",
                -1,
            ),
            "not an image repository",
        ),
        (
            (
                "--image ghcr.io/personalclaw/personalclaw-web="
                "indexes/personalclaw-web.index.json\n",
                "",
                1,
            ),
            "names the images",
        ),
        (('--ref "$GITHUB_REF"', '--ref "$GITHUB_REF_NAME"', 1), "exited 2"),
        (("printf 'planted' >> \"$first\"", "true", 1), "the verify checks nothing"),
    ],
)
def test_a_manifest_command_that_would_fail_on_the_runner_is_caught(
    tmp_path: Path, change: tuple[str, str, int], said: str
) -> None:
    release, artifacts, _ = _texts()
    planted = artifacts.replace(*change)
    assert planted != artifacts
    problems = manifest_run_problems(release, planted, tmp_path)
    assert any(said in p for p in problems), problems
