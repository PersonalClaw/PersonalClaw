"""The release rehearsal builds every release artifact and publishes nothing (#3942).

``.github/workflows/release-rehearsal.yml`` runs weekly on main and calls
``release-artifacts.yml``, the build half of ``release.yml`` as a reusable workflow. This holds:

- **nothing is published.** Neither file names an environment, asks for more than reading the
  tree (no ``id-token``, ``packages`` or ``contents: write``), passes or reads a secret, logs in to
  a registry, pushes an image, or runs a publishing step (PyPI, a GitHub Release, a tag, an
  attestation). An image is copied only to a file on the runner (``skopeo copy`` to ``oci:`` or
  ``docker-archive:``). Every file goes up only as a workflow artifact: kept the days its caller
  names, and the rehearsal names at most 7.
- **it runs on main only.** It triggers on its schedule and on a dispatch, nothing else, and its
  first job refuses any ref but main and any repository but this one; the build waits on it.
- **the release builds through it** (#3911). ``release.yml`` calls ``release-artifacts.yml`` once,
  as the tag, and builds nothing itself, so there is one copy of every build and smoke. Every
  other job of the release waits on it, so nothing publishes before every artifact is built,
  smoked and in the manifest. The images are published from the layouts it built, with every
  digest kept (``skopeo copy --all --preserve-digests``), never built again.
- **its manifest job runs.** The job's commands, from its write on, run as the runner runs them in
  a folder shaped like the release's, and name each image by the repository release.yml pushes it
  to. The first rehearsal on the runner found the write refusing the images' bare names.

Each rule is planted to fail.
"""

from __future__ import annotations

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

#: The jobs release-artifacts.yml builds and smokes with, and release.yml no longer holds. Its
#: `images` job is release.yml's too, which pushes and builds nothing (release_problems).
BUILDS = ("build", "gateway-dashboard", "desktop-mac", "desktop-linux", "manifest")
CALL = "./.github/workflows/release-artifacts.yml"
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
    r"crane\s+(push|copy))\b"
)
#: Where ``skopeo copy`` may write in either file: a file on the runner, never a registry.
LOCAL_DESTINATIONS = ("oci:", "docker-archive:")
_SKOPEO_COPY = re.compile(r"\bskopeo\s+copy\b([^\n]*)")
MAX_RETENTION_DAYS = 7
#: How release-artifacts.yml keeps each upload: the days its caller names.
RETENTION_INPUT = "${{ inputs.retention-days }}"


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
            for copied in _SKOPEO_COPY.finditer(run.replace("\\\n", " ")):
                destination = copied.group(1).split()[-1].strip("'\"") if copied.group(1) else ""
                if not destination.startswith(LOCAL_DESTINATIONS):
                    out.append(f"{label}: skopeo copies to {destination!r}, not a file here")
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
                if days == RETENTION_INPUT and "workflow_call" in (doc.get("on") or {}):
                    continue  # the caller's days, which trigger_problems holds for the rehearsal
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
    if [j.get("uses") for j in calls] != [CALL]:
        out.append("the rehearsal does not call release-artifacts.yml, once")
    for call in calls:
        days = (call.get("with") or {}).get("retention-days")
        if not isinstance(days, int) or days > MAX_RETENTION_DAYS:
            out.append(f"the rehearsal keeps its artifacts {days!r} days, not at most 7")
    return out


# ── the release builds through it ──────────────────────────────────────────────────────────────


def _waits_on(jobs: dict, job_id: str, target: str, seen: frozenset = frozenset()) -> bool:
    needs = jobs.get(job_id, {}).get("needs") or []
    needs = [needs] if isinstance(needs, str) else list(needs)
    return target in needs or any(
        _waits_on(jobs, n, target, seen | {job_id}) for n in needs if n not in seen
    )


def release_problems(release_text: str) -> list[str]:
    """release.yml builds through release-artifacts.yml, and publishes only after all of it."""
    jobs = _load(release_text).get("jobs") or {}
    out: list[str] = []
    calls = [job_id for job_id, job in jobs.items() if job.get("uses") == CALL]
    if calls != ["artifacts"]:
        return [f"release.yml calls release-artifacts.yml as {calls}, not once as job artifacts"]
    call = jobs["artifacts"]
    if (call.get("with") or {}).get("ref") != "${{ github.ref_name }}":
        out.append("release.yml builds its artifacts as a ref other than its own tag")
    if call.get("permissions") != READ_ONLY:
        out.append(f"the artifacts job holds {call.get('permissions')!r}, not contents: read")
    for job_id in BUILDS:
        if job_id in jobs:
            out.append(
                f"release.yml builds {job_id} itself, a second copy of release-artifacts.yml's"
            )
    for job_id in jobs:
        if job_id != "artifacts" and not _waits_on(jobs, job_id, "artifacts"):
            out.append(f"{job_id} does not wait on every artifact being built and smoked")
    images = _steps(jobs.get("images") or {})
    if any(str(s.get("uses", "")).startswith("docker/build-push-action@") for s in images):
        out.append("images builds again, so what it pushes is not what was smoked")
    pushes = [
        m.group(1)
        for s in images
        for m in _SKOPEO_COPY.finditer(str(s.get("run") or "").replace("\\\n", " "))
    ]
    if not pushes or not all(
        "--all" in p.split() and "--preserve-digests" in p.split() and "oci-archive:" in p
        for p in pushes
    ):
        out.append("images does not push the layout with every digest kept")
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


def test_the_release_builds_through_it_and_publishes_only_after_it() -> None:
    release, _, _ = _texts()
    assert release_problems(release) == []


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
                "    steps:\n"
                "      - run: skopeo copy --all oci-archive:x.tar docker://ghcr.io/x/y:1\n",
            ),
            "skopeo copies to 'docker://ghcr.io/x/y:1'",
        ),
        (
            ("    steps:\n", '    steps:\n      - run: skopeo copy oci-archive:x.tar "$DEST"\n'),
            "skopeo copies to '$DEST'",
        ),
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
        (
            rehearsal.replace("      retention-days: 3\n", "      retention-days: 30\n", 1),
            "keeps its artifacts 30 days",
        ),
    ]:
        assert planted != rehearsal
        assert any(said in p for p in trigger_problems(planted)), trigger_problems(planted)


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


def test_a_release_that_builds_or_publishes_on_its_own_is_caught() -> None:
    release, _, _ = _texts()
    assert release_problems(release) == []
    for planted, said in [
        (
            release.replace("    uses: ./.github/workflows/release-artifacts.yml\n", "", 1),
            "not once as job artifacts",
        ),
        (
            release.replace("      ref: ${{ github.ref_name }}\n", "      ref: main\n", 1),
            "a ref other than its own tag",
        ),
        (
            release.replace("\n  pypi:\n", "\n  build:\n    runs-on: x\n\n  pypi:\n", 1),
            "builds build itself",
        ),
        (release.replace("    needs: [artifacts]\n", "", 1), "does not wait on every artifact"),
        (
            release.replace("--preserve-digests ", "", 1),
            "does not push the layout with every digest kept",
        ),
        (
            release.replace(
                "      - name: Push the layout",
                "      - uses: docker/build-push-action@abc\n      - name: Push the layout",
                1,
            ),
            "images builds again",
        ),
    ]:
        assert planted != release, said
        assert any(said in p for p in release_problems(planted)), (said, release_problems(planted))
