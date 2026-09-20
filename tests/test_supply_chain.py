"""CRE-5 rails for lock refresh and release image attestations."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = REPO_ROOT / "Makefile"
README = REPO_ROOT / "README.md"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


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
