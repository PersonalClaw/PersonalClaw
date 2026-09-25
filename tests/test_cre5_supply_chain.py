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


# ---------------------------------------------------------------------------
# A container command may never be run through a LOGIN shell (#3562)
# ---------------------------------------------------------------------------
#
# 🔴 THE MEASURED DEFECT. `release.yml`'s release-blocking per-arch smoke ran
# `sh -lc "$SMOKE"` from 2026-08-10 (#1004) onwards, and the last Release before the fix
# was v0.1.3 on 2026-07-31 — so the step had NEVER executed and v0.2.0 would have been its
# first run. `-l` makes the shell read `/etc/profile`, and `/etc/profile` does not EXTEND
# `PATH`, it OVERWRITES it, discarding the image's own `ENV PATH`. Measured in containers
# against the published 0.1.3 gateway image:
#
#     sh -lc 'personalclaw --version'  ->  127  "sh: 1: personalclaw: not found"
#     sh -c  'personalclaw --version'  ->  0    "personalclaw 0.1.3"
#
# `images` pushes `:X.Y.Z`, `:X.Y` and `:latest` BEFORE it smokes what it pushed, so this
# would have moved three GHCR tags and then failed the job — published tags with no
# GitHub Release to explain them.
#
# WHY THE RULE IS ABOUT THE LOGIN SHELL AND NOT ABOUT THE USER. The issue attributed the
# `web` leg's survival to `/etc/profile` taking its root branch. Measured, that is only
# half true and the other half is worse: Debian's profile does branch on `id -u` (the
# gateway drops to uid 10001), but web's **Alpine** profile overwrites `PATH` in ONE
# unconditional line with no user branch at all — it survives purely because that line
# happens to contain `/usr/sbin`, where `nginx` lives, and it survives as a non-root user
# too. Both bases clobber `PATH`; only one clobbers it into something that still works. So
# the rule cannot be "do not do this as a non-root user" — it is "do not do this at all",
# and the `web` leg is one binary relocation away from the same 127.

#: Shell invocations that re-derive `PATH` from `/etc/profile`: a short-flag CLUSTER
#: containing `l` (`-lc`, `-cl`, `-l -c`) or an explicit `--login`. The optional path and
#: shell-name prefixes cover `/bin/sh`, `bash`, `dash`, `ash`, `zsh`, `ksh`.
_LOGIN_SHELL_RE = re.compile(
    r"\b(?:[\w./-]*/)?(?:ba|da|a|z|k)?sh\s+(?:-[A-Za-z]*l[A-Za-z]*|--login)\b"
)

#: Runtimes whose `run`/`exec` hands a command to a fresh container, where the image's
#: `ENV PATH` is the only thing that makes its binaries resolvable.
_CONTAINER_INVOCATIONS = ("docker run", "docker exec", "podman run", "nerdctl run", "finch run")


def _logical_lines(script: str) -> list[str]:
    """Shell comments stripped, backslash continuations joined.

    Both halves are load-bearing. **Comments must go first**: a rail that scans raw source
    counts the prose explaining the fix as the defect, and the commit that fixes this one
    necessarily documents `sh -lc` in order to say why it is wrong. Only whole-line comments
    are removed — a `#` mid-command can live inside a quoted string, and guessing wrong there
    would silently drop real code. **Continuations must be joined second**: the defect spanned
    two physical lines (`docker run ... \\` then `sh -lc "$SMOKE"`), so a per-line scan sees a
    container invocation with no shell and a shell with no container, and finds nothing.
    """
    uncommented = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    joined: list[str] = []
    pending = ""
    for line in uncommented:
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        joined.append(pending + stripped)
        pending = ""
    if pending:
        joined.append(pending)
    return joined


def _container_commands() -> list[tuple[str, str, str, str]]:
    """Every (workflow, job, step, logical line) that hands a command to a container."""
    found: list[tuple[str, str, str, str]] = []
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(workflow, dict)
        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                script = step.get("run")
                if not isinstance(script, str):
                    continue
                label = str(step.get("name") or f"step #{index}")
                for line in _logical_lines(script):
                    if any(token in line for token in _CONTAINER_INVOCATIONS):
                        found.append((path.name, str(job_name), label, line.strip()))
    return found


def test_no_workflow_runs_a_container_command_through_a_login_shell() -> None:
    """The rail for #3562, over every workflow rather than only the step that had the bug.

    A login shell sources `/etc/profile`, which OVERWRITES `PATH` rather than extending it,
    so the image's `ENV PATH` is discarded and its own binaries stop resolving. There is no
    case where that is what a container smoke wanted: `-l` looks like "pick up the image's
    environment" and does the exact opposite.
    """
    offenders = [
        f"{workflow}:{job}:{step}: {line}"
        for workflow, job, step, line in _container_commands()
        if _LOGIN_SHELL_RE.search(line)
    ]
    assert not offenders, (
        "these steps run a command inside a container through a LOGIN shell, which sources "
        "/etc/profile and OVERWRITES PATH — discarding the image's own ENV PATH, so its "
        f"binaries are not found (#3562, measured exit 127): {offenders}"
    )


def test_the_login_shell_scan_can_see_the_release_smoke_it_exists_to_guard() -> None:
    """The vacuity floor. A zero from a scan that found no container commands is not a pass.

    This is the control that makes the assertion above falsifiable: it pins that the scan
    reaches the exact step whose regression is the issue, THROUGH the line-continuation join
    (the invocation and the shell sit on different physical lines), and that the shell it
    finds there is the non-login form.
    """
    commands = _container_commands()
    assert commands, "the scan found no container commands at all, so its zero means nothing"

    smoke = [
        line
        for workflow, job, step, line in commands
        if workflow == "release.yml" and job == "images" and "--entrypoint ''" in line
    ]
    assert len(smoke) == 1, f"expected exactly one release smoke invocation, got {smoke}"
    assert 'sh -c "$SMOKE"' in smoke[0], (
        "the release smoke must hand its command to a NON-login shell; measured, `sh -lc` "
        f"exits 127 in the gateway image and `sh -c` prints the version: {smoke[0]}"
    )


def test_the_login_shell_detector_fires_and_ignores_prose() -> None:
    """Three controls on the detector itself, because a regex that cannot match reads clean.

    The middle one is the trap this rail was written to survive: the commit that fixes a
    login-shell bug has to WRITE `sh -lc` down in order to explain it, so a detector that
    counts comments reds its own fix and the next author reaches for rewording instead.
    """
    invocation = 'docker run --rm --entrypoint \'\' "$IMAGE" \\\n  sh -lc "$SMOKE"'
    assert [
        line for line in _logical_lines(invocation) if _LOGIN_SHELL_RE.search(line)
    ], "the detector does not fire on the exact pre-fix invocation, so its zero is vacuous"

    documented = 'docker run --rm "$IMAGE" sh -c "$SMOKE"\n# it used to say sh -lc, which broke'
    assert not [
        line for line in _logical_lines(documented) if _LOGIN_SHELL_RE.search(line)
    ], "a whole-line comment explaining the fix must not read as the defect"

    for variant in ("sh -l -c cmd", "bash -lc cmd", "sh -cl cmd", "/bin/bash --login -c cmd"):
        assert _LOGIN_SHELL_RE.search(f'docker run "$IMAGE" {variant}'), variant
    for benign in ("sh -c cmd", "git stash -l", "npm publish -l", "sh -eu -c cmd"):
        assert not _LOGIN_SHELL_RE.search(f'docker run "$IMAGE" {benign}'), benign


def test_every_smoked_image_declares_the_output_its_smoke_must_produce() -> None:
    """An exit status is a weak claim about a version command, so the OUTPUT is asserted.

    Two holes this closes. A leg with no `expect` makes the workflow's `case` pattern `*""*`,
    which matches every possible output — green having asserted nothing, which is how the
    original step read for 45 days. And a `expect` carrying a hardcoded version literal would
    rot into asserting a PAST release; the gateway's has to be derived from the version this
    run is publishing, so a stale build layer serving the previous release cannot pass.
    """
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    images = jobs.get("images")
    assert isinstance(images, dict)
    legs = images["strategy"]["matrix"]["include"]
    assert isinstance(legs, list) and legs

    for leg in legs:
        assert isinstance(leg, dict)
        expected = leg.get("expect")
        assert isinstance(expected, str) and expected.strip(), (
            f"image leg {leg.get('name')!r} smokes {leg.get('smoke')!r} with no `expect`, so "
            "the workflow asserts only an exit status and an empty output would pass"
        )

    gateway = next(leg for leg in legs if leg.get("name") == "gateway")
    assert gateway["expect"] == "personalclaw ${{ needs.build.outputs.version }}", (
        "the gateway smoke must require the version being RELEASED, not any version: a "
        "cached builder layer would otherwise ship a stale venv under a `:latest` that lies"
    )

    smoke_step = _step(images, name="Smoke both arches (release-blocking)")
    environment = smoke_step.get("env")
    assert isinstance(environment, dict)
    assert environment.get("EXPECT") == "${{ matrix.expect }}"
    script = smoke_step.get("run")
    assert isinstance(script, str)
    assert (
        'if [ -z "$EXPECT" ]; then' in script
    ), "the step must refuse an empty expectation; `case $output in *''*)` matches anything"
    assert '*"$EXPECT"*)' in script, "the captured output must be tested against the expectation"
    assert "2>&1" in script, (
        "stderr must be captured: `nginx -v` writes its banner there, so a stdout-only "
        "capture reads EMPTY and reds the web leg on a working image"
    )


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
