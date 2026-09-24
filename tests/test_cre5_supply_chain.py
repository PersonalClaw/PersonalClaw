"""CRE-5 rails for lock refresh and release image attestations."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = REPO_ROOT / "Makefile"
README = REPO_ROOT / "README.md"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
FULL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "full.yml"


def _make_recipe(target: str) -> list[str]:
    recipe: list[str] = []
    in_target = False
    for raw in MAKEFILE.read_text(encoding="utf-8").splitlines():
        if raw.startswith("\t"):
            if in_target:
                recipe.append(raw.lstrip("\t"))
            continue
        if raw.startswith("#") or not raw.strip():
            continue
        head, separator, _tail = raw.partition(":")
        if separator and not head.startswith("."):
            in_target = head.strip() == target
    return recipe


def _workflow() -> dict[str, object]:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _full_workflow() -> dict[str, object]:
    workflow = yaml.safe_load(FULL_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _step(job: dict[str, object], *, name: str) -> dict[str, object]:
    steps = job.get("steps")
    assert isinstance(steps, list)
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    raise AssertionError(f"missing workflow step {name!r}")


def test_make_lock_runs_the_real_uv_lock_command_and_is_listed_in_help() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "UV      ?= uv" in makefile
    assert _make_recipe("lock") == ["$(UV) lock"]
    assert "## lock: refresh uv.lock" in makefile


def test_image_build_publishes_sbom_and_provenance_attestations() -> None:
    workflow = _workflow()
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    images = jobs.get("images")
    assert isinstance(images, dict)

    build = _step(images, name="Build and push image with supply-chain attestations")
    assert build.get("uses") == "docker/build-push-action@v7"
    inputs = build.get("with")
    assert isinstance(inputs, dict)
    assert inputs.get("sbom") is True
    assert inputs.get("provenance") == "mode=max"


def test_both_image_architectures_export_spdx_json_to_the_release() -> None:
    workflow = _workflow()
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    images = jobs.get("images")
    notes = jobs.get("notes")
    assert isinstance(images, dict)
    assert isinstance(notes, dict)

    export = _step(images, name="Export image SBOM attestations (SPDX-JSON)")
    script = export.get("run")
    assert isinstance(script, str)
    for anchor in (
        "docker buildx imagetools inspect",
        "{{ json .SBOM }}",
        '"linux/amd64", "linux/arm64"',
        '"SPDX"',
        '"SPDXRef-DOCUMENT"',
    ):
        assert anchor in script

    upload = _step(images, name="Upload image SBOM artifacts")
    assert upload.get("uses") == "actions/upload-artifact@v7"
    upload_inputs = upload.get("with")
    assert isinstance(upload_inputs, dict)
    assert upload_inputs.get("name") == "image-sbom-${{ matrix.name }}"
    assert upload_inputs.get("path") == "image-sboms/*.spdx.json"
    assert upload_inputs.get("if-no-files-found") == "error"

    steps = notes.get("steps")
    assert isinstance(steps, list)
    downloads = [
        step.get("with")
        for step in steps
        if isinstance(step, dict) and step.get("uses") == "actions/download-artifact@v8"
    ]
    assert any(
        isinstance(inputs, dict)
        and inputs.get("pattern") == "image-sbom-*"
        and inputs.get("merge-multiple") is True
        for inputs in downloads
    )
    release = _step(notes, name="Create GitHub Release")
    release_script = release.get("run")
    assert isinstance(release_script, str)
    assert "image-sboms/*" in release_script


def test_readme_supply_chain_claim_matches_the_release_outputs() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "syft\nSPDX-JSON SBOMs" in readme
    assert "both architectures of each image" in readme
    assert "build-provenance attestations** for the wheel and images" in readme


def test_readme_coverage_badge_reads_the_branch_the_workflow_publishes() -> None:
    workflow = _full_workflow()
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    coverage = jobs.get("coverage")
    assert isinstance(coverage, dict)

    publish = _step(coverage, name="Publish coverage badge")
    environment = publish.get("env")
    script = publish.get("run")
    assert isinstance(environment, dict)
    assert isinstance(script, str)
    branch = environment.get("BADGE_BRANCH")
    assert isinstance(branch, str)
    assert "refs/heads/${BADGE_BRANCH}" in script

    readme = README.read_text(encoding="utf-8")
    badge = re.search(
        r"https://img\.shields\.io/endpoint\?url=https://raw\.githubusercontent\.com/"
        r"PersonalClaw/PersonalClaw/([^/\s)]+)/coverage-badge\.json",
        readme,
    )
    assert badge is not None
    assert badge.group(1) == branch


def test_a_non_cancelling_workflow_bounds_every_job() -> None:
    """A serialized lane needs per-job caps, because the group will not evict a hung job.

    `cancel-in-progress: false` is deliberate on full.yml (#2946) so a merge cannot kill
    the previous commit's verification. The cost is that a wedged job no longer wastes only
    its own slot — it HOLDS the lane, and every later push queues behind it until GitHub's
    360-minute default fires. MEASURED 2026-09-22 (#3318): `coverage` hung past 55 minutes
    with 29 of its 30 sibling jobs already green, and the three pushes behind it reached
    `jobs: []`. So the bound belongs on the job. Workflows whose group cancels, or that have
    no group at all, are exempt: there a hung job is evicted or serializes nothing.
    """
    unbounded: list[str] = []
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(workflow, dict)
        concurrency = workflow.get("concurrency")
        if not isinstance(concurrency, dict) or concurrency.get("cancel-in-progress"):
            continue
        jobs = workflow.get("jobs")
        assert isinstance(jobs, dict)
        for name, job in jobs.items():
            if isinstance(job, dict) and job.get("uses"):
                continue  # a reusable-workflow call carries its timeout in the callee
            assert isinstance(job, dict)
            if not isinstance(job.get("timeout-minutes"), int):
                unbounded.append(f"{path.name}:{name}")

    assert not unbounded, (
        "these jobs run in a non-cancelling concurrency group with no timeout-minutes, so any "
        f"one of them can starve the lane for GitHub's 360-minute default: {unbounded}"
    )
