"""The source-tree checks split from
``tests/test_supply_chain.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from supply_chain_shared import _LOGIN_SHELL_RE, README, REPO_ROOT, _logical_lines

WORKFLOWS = REPO_ROOT / ".github" / "workflows"


RELEASE_WORKFLOW = WORKFLOWS / "release.yml"


BREADTH_WORKFLOW = WORKFLOWS / "breadth.yml"


def _action(step: object) -> str:
    """The action a step uses, without its ref: the ref is a commit SHA that moves with every
    release, and tests/rails/test_ci_contract.py holds it to one."""
    return str(step.get("uses", "") if isinstance(step, dict) else "").split("@", 1)[0]


def _workflow() -> dict[str, object]:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _jobs() -> dict[str, dict]:
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    return jobs


def _breadth_workflow() -> dict[str, object]:
    workflow = yaml.safe_load(BREADTH_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _step(job: dict[str, object], *, name: str) -> dict[str, object]:
    steps = job.get("steps")
    assert isinstance(steps, list)
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    raise AssertionError(f"missing workflow step {name!r}")


def test_image_build_publishes_sbom_and_provenance_attestations() -> None:
    workflow = _workflow()
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    images = jobs.get("images")
    assert isinstance(images, dict)

    build = _step(images, name="Build and push image with supply-chain attestations")
    assert _action(build) == "docker/build-push-action"
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
    assert _action(upload) == "actions/upload-artifact"
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
        if isinstance(step, dict) and _action(step) == "actions/download-artifact"
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


_WHEEL_SBOM_STEP = "SBOM of the wheel as installed (syft SPDX-JSON)"


_WHEEL_SBOM = "wheel-sbom/personalclaw-wheel.spdx.json"


#: `uv venv`, `uv export` and `uv pip install`, done to a real venv: an install writes the
#: distribution's metadata into the venv's site-packages, which is what syft and
#: `importlib.metadata` both read. Each call is logged, and the lock's flags are required.
_FAKE_UV = """
    import os, subprocess, sys
    from pathlib import Path

    args = sys.argv[1:]
    with open(os.environ["DRY_RUN_LOG"], "a", encoding="utf-8") as log:
        log.write("uv " + " ".join(args) + "\\n")


    def install(python, name, version):
        site = subprocess.run(
            [python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dist = Path(site) / f"{name.replace('-', '_')}-{version}.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text(
            f"Metadata-Version: 2.1\\nName: {name}\\nVersion: {version}\\n", encoding="utf-8"
        )


    if args[0] == "venv":
        made = subprocess.run([sys.executable, "-m", "venv", "--without-pip", args[-1]])
        sys.exit(made.returncode)
    if args[0] == "export":
        assert "--locked" in args and "--no-emit-project" in args, args
        out = Path(args[args.index("--output-file") + 1])
        out.write_text("aiohttp==3.14.3\\nPyYAML==6.0.3\\n", encoding="utf-8")
        sys.exit(0)
    if args[:2] == ["pip", "install"]:
        python = args[args.index("--python") + 1]
        assert "--no-deps" in args, args
        if "-r" in args:
            assert "--require-hashes" in args, args
            listed = Path(args[args.index("-r") + 1]).read_text(encoding="utf-8")
            for line in listed.splitlines():
                install(python, *line.split("=="))
        else:
            (wheel,) = [arg for arg in args if arg.endswith(".whl")]
            install(python, *Path(wheel).name.split("-")[:2])
        sys.exit(0)
    sys.exit(f"this dry run does not model: uv {' '.join(args)}")
"""


#: syft's python cataloger: the scanned root, then one package per installed distribution under
#: it, named by its purl. `SYFT_DROPS` names distributions a broken scan leaves out ("*" for
#: all, the v0.2.0 SBOM).
_FAKE_SYFT = """
    import json, os, re, sys
    from pathlib import Path

    args = sys.argv[1:]
    with open(os.environ["DRY_RUN_LOG"], "a", encoding="utf-8") as log:
        log.write("syft " + " ".join(args) + "\\n")
    assert args[0] == "scan" and args[1].startswith("dir:"), args
    root = Path(args[1].removeprefix("dir:"))
    (out,) = [arg.removeprefix("spdx-json=") for arg in args if arg.startswith("spdx-json=")]
    drops = os.environ.get("SYFT_DROPS", "").split(",")
    packages = [{"name": root.name, "SPDXID": "SPDXRef-Root", "primaryPackagePurpose": "FILE"}]
    for metadata in sorted(root.rglob("*.dist-info/METADATA")):
        fields = dict(
            line.split(": ", 1) for line in metadata.read_text(encoding="utf-8").splitlines()
        )
        if "*" in drops or fields["Name"] in drops:
            continue
        purl = f"pkg:pypi/{re.sub(r'[-_.]+', '-', fields['Name']).lower()}@{fields['Version']}"
        packages.append(
            {
                "name": fields["Name"],
                "versionInfo": fields["Version"],
                "externalRefs": [{"referenceType": "purl", "referenceLocator": purl}],
            }
        )
    document = {"spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT", "packages": packages}
    Path(out).write_text(json.dumps(document), encoding="utf-8")
"""


def _stand_in(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def _wheel_sbom(tmp_path: Path, drops: str = "") -> tuple[subprocess.CompletedProcess, Path]:
    """Run the build job's wheel-SBOM step in a checkout holding a built wheel and sdist."""
    step = _step(_jobs()["build"], name=_WHEEL_SBOM_STEP)
    checkout = tmp_path / "checkout"
    (checkout / "dist").mkdir(parents=True)
    for name in ("personalclaw-9.9.9-py3-none-any.whl", "personalclaw-9.9.9.tar.gz"):
        (checkout / "dist" / name).write_bytes(b"")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stand_in(bin_dir / "uv", _FAKE_UV)
    _stand_in(bin_dir / "syft", _FAKE_SYFT)
    (tmp_path / "runner-temp").mkdir()
    expressions = {
        "${{ steps.syft.outputs.cmd }}": str(bin_dir / "syft"),
        "${{ steps.ver.outputs.version }}": "9.9.9",
    }
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    env.update({key: expressions[value] for key, value in step["env"].items()})
    env.update(
        RUNNER_TEMP=str(tmp_path / "runner-temp"),
        DRY_RUN_LOG=str(tmp_path / "calls.log"),
        SYFT_DROPS=drops,
    )
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]], cwd=checkout, env=env, capture_output=True, text=True
    )
    return result, checkout


def test_the_wheels_sbom_lists_the_wheel_and_what_it_installs(tmp_path: Path) -> None:
    result, checkout = _wheel_sbom(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "the wheel and the 2 packages installed with it" in result.stdout

    document = json.loads((checkout / _WHEEL_SBOM).read_text(encoding="utf-8"))
    purls = {
        ref["referenceLocator"]
        for package in document["packages"]
        for ref in package.get("externalRefs", [])
    }
    assert purls == {
        "pkg:pypi/personalclaw@9.9.9",
        "pkg:pypi/aiohttp@3.14.3",
        "pkg:pypi/pyyaml@6.0.3",
    }
    # syft read the venv the wheel and its locked dependencies went into, and nothing else.
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines()
    installs = [call for call in calls if call.startswith("uv pip install")]
    assert len(installs) == 2, calls
    venv = installs[0].split("--python ", 1)[1].split()[0].removesuffix("/bin/python")
    assert all(f"--python {venv}/bin/python" in call for call in installs), calls
    (scan,) = [call for call in calls if call.startswith("syft scan")]
    assert scan.startswith(f"syft scan dir:{venv} "), scan
    # Written outside `dist/`: the `pypi` job uploads every `personalclaw-*` file in it.
    assert not _WHEEL_SBOM.startswith("dist/")
    assert sorted(p.name for p in (checkout / "dist").iterdir()) == [
        "personalclaw-9.9.9-py3-none-any.whl",
        "personalclaw-9.9.9.tar.gz",
    ]


@pytest.mark.parametrize(
    ("drops", "says"),
    [
        ("*", "1 package(s), so syft cataloged nothing"),  # v0.2.0's SBOM
        ("personalclaw", "does not list the wheel, pkg:pypi/personalclaw@9.9.9"),
        ("PyYAML", "does not list what the wheel installs: pyyaml@6.0.3"),
    ],
)
def test_a_wheel_sbom_that_misses_what_was_installed_fails_the_build(
    tmp_path: Path, drops: str, says: str
) -> None:
    result, _ = _wheel_sbom(tmp_path, drops)
    assert result.returncode != 0, result.stdout
    assert says in result.stderr, result.stderr


def test_the_release_attaches_the_wheels_sbom_and_pypi_never_sees_it() -> None:
    jobs = _jobs()
    build = jobs["build"]
    uploads = {
        step["with"]["name"]: step["with"]
        for step in build["steps"]
        if _action(step) == "actions/upload-artifact"
    }
    assert uploads["wheel-sbom"]["path"] == _WHEEL_SBOM
    assert uploads["wheel-sbom"].get("if-no-files-found") == "error"
    assert uploads["dist"]["path"] == "dist/", "the SBOM must stay out of the `dist` artifact"

    pypi_downloads = [
        step["with"]["name"]
        for step in jobs["pypi"]["steps"]
        if _action(step) == "actions/download-artifact"
    ]
    assert pypi_downloads == ["dist"], "PyPI is handed the distributions and nothing else"

    notes = jobs["notes"]
    downloads = [
        step["with"] for step in notes["steps"] if _action(step) == "actions/download-artifact"
    ]
    assert {"name": "wheel-sbom", "path": "wheel-sbom/"} in downloads
    create = _step(notes, name="Create GitHub Release")["run"]
    assert "wheel-sbom/*" in create.split("gh release create", 1)[1].split("--title", 1)[0]


def test_the_wheels_provenance_needs_only_the_build_it_attests() -> None:
    """`attest` attests `build`'s `dist`, so nothing else may stand between them.

    It used to need `notes` too. v0.2.0's desktop-linux failed, `notes` was skipped, `attest`
    with it, and the wheel and sdist PyPI had published have no build provenance on GitHub."""
    attest = _jobs()["attest"]
    needs = attest.get("needs")
    assert (needs if isinstance(needs, list) else [needs]) == ["build"], needs
    downloads = [
        step["with"] for step in attest["steps"] if _action(step) == "actions/download-artifact"
    ]
    assert downloads == [{"name": "dist", "path": "dist/"}], downloads


def test_both_desktop_bundles_are_frozen_by_one_pinned_pyinstaller() -> None:
    """PyInstaller is not in uv.lock, so each desktop job installed whatever PyInstaller was
    newest on the day of the tag. Both of its halves are pinned exactly, as its banner names
    them ("PyInstaller: 6.22.3, contrib hooks: 2026.8" on v0.2.0's run), and the macOS and
    Linux bundles are frozen by the same pair."""
    pins: dict[str, list[str]] = {}
    for name in ("desktop-mac", "desktop-linux"):
        installs = [
            line.strip()
            for step in _jobs()[name]["steps"]
            for line in str(step.get("run", "")).splitlines()
            if "pip install" in line and "pyinstaller" in line and not line.lstrip().startswith("#")
        ]
        assert len(installs) == 1, f"{name} installs PyInstaller {len(installs)} times: {installs}"
        pins[name] = [token for token in installs[0].split() if token.startswith("pyinstaller")]
    for name, tokens in pins.items():
        names = sorted(token.split("==")[0] for token in tokens)
        assert names == ["pyinstaller", "pyinstaller-hooks-contrib"], f"{name}: {tokens}"
        unpinned = [token for token in tokens if not re.fullmatch(r"[a-z-]+==\d[\w.]*", token)]
        assert not unpinned, f"{name} installs PyInstaller from a range: {unpinned}"
    assert pins["desktop-mac"] == pins["desktop-linux"], pins


def test_every_secret_the_release_reads_is_held_by_an_environment() -> None:
    """A repository secret reaches every workflow a branch of this repository runs, a pull
    request's included. An environment's reaches only a job that names the environment and
    passes its deployment rule, which for `website-follow` admits `v*` tags only. So no release
    job reads a secret outside an environment (`GITHUB_TOKEN` is the run's own token)."""
    jobs = _jobs()
    readers = {
        name: sorted(set(re.findall(r"secrets\.(\w+)", json.dumps(job))) - {"GITHUB_TOKEN"})
        for name, job in jobs.items()
    }
    readers = {name: secrets for name, secrets in readers.items() if secrets}
    assert readers == {"website-follow": ["WEBSITE_DISPATCH_TOKEN"]}, readers
    unheld = [name for name in readers if not jobs[name].get("environment")]
    assert not unheld, f"these jobs read a repository secret: {unheld}"
    assert jobs["website-follow"]["environment"] == "website-follow"


def _website_nudge(tmp_path: Path, token: str, gh_exit: int = 0) -> subprocess.CompletedProcess:
    """Run website-follow's step with *token* as its secret and a `gh` that exits *gh_exit*."""
    step = _step(_jobs()["website-follow"], name="Ask personalclaw.dev to re-check release parity")
    assert step["env"] == {"GH_TOKEN": "${{ secrets.WEBSITE_DISPATCH_TOKEN }}"}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    _stand_in(
        bin_dir / "gh",
        f"""
        import os, sys
        with open(os.environ["DRY_RUN_LOG"], "a", encoding="utf-8") as log:
            log.write("gh " + " ".join(sys.argv[1:]) + "\\n")
        sys.exit({gh_exit})
        """,
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GH_TOKEN": token,
        "DRY_RUN_LOG": str(tmp_path / "calls.log"),
    }
    return subprocess.run(
        ["bash", "-e", "-c", step["run"]], env=env, capture_output=True, text=True, cwd=tmp_path
    )


def test_the_website_nudge_without_its_token_skips_and_never_fails_the_release(
    tmp_path: Path,
) -> None:
    result = _website_nudge(tmp_path, token="")
    assert result.returncode == 0, result.stderr
    assert "No WEBSITE_DISPATCH_TOKEN configured" in result.stdout
    assert not (tmp_path / "calls.log").exists(), "nothing is dispatched without the token"
    assert _jobs()["website-follow"].get("continue-on-error") is True


def test_the_website_nudge_with_its_token_dispatches_the_websites_check(tmp_path: Path) -> None:
    result = _website_nudge(tmp_path, token="fake-website-dispatch-token")
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines()
    assert calls == ["gh workflow run release-follow.yml --repo PersonalClaw/personalclaw.dev"]
    failed = _website_nudge(tmp_path / "failing", token="fake-website-dispatch-token", gh_exit=1)
    assert failed.returncode == 0, failed.stderr
    assert "the website's daily watchdog will still catch it" in failed.stdout


def test_readme_supply_chain_claim_matches_the_release_outputs() -> None:
    """Each promise in the README's supply-chain paragraph, held to the job that keeps it.

    v0.2.0's README already promised an SBOM for the wheel. Checking only that the sentence
    existed kept this green while the SBOM was empty and the Release never attached it."""
    readme = README.read_text(encoding="utf-8")
    assert "syft\nSPDX-JSON SBOMs" in readme
    assert "both architectures of each image" in readme
    assert "build-provenance attestations** for the wheel and images" in readme

    jobs = _jobs()
    create = _step(jobs["notes"], name="Create GitHub Release")["run"]
    attached = create.split("gh release create", 1)[1].split("--title", 1)[0].split()
    assert {"dist/*", "wheel-sbom/*", "image-sboms/*"} <= set(attached), attached
    _step(jobs["build"], name=_WHEEL_SBOM_STEP)
    export = _step(jobs["images"], name="Export image SBOM attestations (SPDX-JSON)")["run"]
    assert '"linux/amd64", "linux/arm64"' in export
    attest = [
        step
        for step in jobs["attest"]["steps"]
        if "attest-build-provenance" in step.get("uses", "")
    ]
    assert attest and attest[0]["with"]["subject-path"] == "dist/*"
    push = _step(jobs["images"], name="Build and push image with supply-chain attestations")
    assert push["with"]["provenance"] == "mode=max"


def _jobs_that_run_the_suite() -> list[tuple[str, str, object]]:
    """``(workflow, job, permissions it holds)`` for every job with a step that runs pytest."""
    found: list[tuple[str, str, object]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(workflow, dict)
        default = workflow.get("permissions")
        jobs = workflow.get("jobs")
        assert isinstance(jobs, dict)
        for name, job in jobs.items():
            steps = job.get("steps") if isinstance(job, dict) else None
            if not isinstance(steps, list):
                continue
            if any(isinstance(s, dict) and "pytest" in str(s.get("run", "")) for s in steps):
                found.append((path.name, name, job.get("permissions", default)))
    return found


def test_no_job_that_runs_the_suite_holds_a_token_that_can_write() -> None:
    """The suite runs code nobody here wrote: every locked third-party package, in every test.
    So a job that runs it holds a read-only token. The coverage job held ``contents: write``
    only to push a coverage badge to a branch, and the badge is gone. A job with no
    ``permissions`` anywhere would get the repository's default, which is not known here."""
    jobs = _jobs_that_run_the_suite()
    assert len(jobs) >= 8, f"only {len(jobs)} jobs run pytest: the scan lost the workflows"
    can_write = [
        f"{workflow}:{job} -> {held}"
        for workflow, job, held in jobs
        if not isinstance(held, dict) or "write" in held.values()
    ]
    assert not can_write, can_write


def test_the_coverage_job_still_measures_and_keeps_only_its_report() -> None:
    """The suite is measured in quarters, each under ``--cov`` on its own runner, and the
    ``coverage`` job combines every quarter's data into the one report it keeps."""
    workflow = _breadth_workflow()
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    shard, coverage = jobs.get("coverage-shard"), jobs.get("coverage")
    assert isinstance(shard, dict) and isinstance(coverage, dict)
    matrix = shard.get("strategy", {}).get("matrix", {})
    shards = matrix.get("shard") if isinstance(matrix, dict) else None
    assert isinstance(shards, list) and len(shards) > 1, shards

    def runs(job: dict[str, object]) -> list[str]:
        steps = job.get("steps")
        assert isinstance(steps, list)
        return [str(step.get("run", "")) for step in steps if isinstance(step, dict)]

    measured = [run for run in runs(shard) if "--cov=personalclaw" in run]
    assert measured, "no shard of the suite is measured"
    for run in measured:
        assert (
            'tests --shard-list "$RUNNER_TEMP/shard.txt"' in run
        ), f"each shard must measure its own share of the suite, all {len(shards)} of them: {run}"
    listing = [run for run in runs(shard) if "tools.ci shards list" in run]
    want = f"--leg coverage-shard --shard ${{{{ matrix.shard }}}}/{len(shards)}"
    assert listing and want in " ".join(listing[0].split()), listing
    assert coverage.get("needs") == ["coverage-shard"]
    script = "tools/ci/coverage_combine.sh"
    assert f"sh {script}" in runs(coverage), "no step runs the script that combines the shards"
    combine = (Path(__file__).resolve().parents[2] / script).read_text(encoding="utf-8")
    assert "coverage combine" in combine and "coverage xml" in combine, combine
    assert f'"$found" -ne {len(shards)}' in combine, "the report waits for every shard's data"
    upload = _step(coverage, name="Upload coverage artifacts").get("with")
    assert isinstance(upload, dict)
    assert upload.get("path") == "coverage.xml"
    assert not any(
        "git push" in run for run in runs(shard) + runs(coverage)
    ), "the coverage jobs publish nothing"


#: Runtimes whose `run`/`exec` hands a command to a fresh container, where the image's
#: `ENV PATH` is the only thing that makes its binaries resolvable.
_CONTAINER_INVOCATIONS = ("docker run", "docker exec", "podman run", "nerdctl run", "finch run")


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

    images = [
        line
        for workflow, job, step, line in commands
        if workflow == "release.yml" and job == "images" and "--entrypoint ''" in line
    ]
    smoke = [line for line in images if '"$SMOKE"' in line]
    assert len(smoke) == 1, f"expected exactly one release smoke invocation, got {images}"
    assert 'sh -c "$SMOKE"' in smoke[0], (
        "the release smoke must hand its command to a NON-login shell; measured, `sh -lc` "
        f"exits 127 in the gateway image and `sh -c` prints the version: {smoke[0]}"
    )
    # The version check is the images job's other invocation, and it runs the image's Python
    # directly: no shell at all, so no profile to overwrite `PATH`.
    checks = [line for line in images if "scripts/release_version.py" in line]
    assert len(checks) == 1, f"expected exactly one version check in the image, got {images}"
    assert "python - --installed reports" in checks[0], checks[0]


def test_every_smoked_image_declares_the_output_its_smoke_must_produce() -> None:
    """An exit status is a weak claim about a version command, so the OUTPUT is asserted.

    Three holes this closes. A leg with no expectation makes the workflow's `case` pattern
    `*""*`, which matches every possible output — green having asserted nothing, which is how
    the original step read for 45 days. An expectation carrying a hardcoded version literal
    would rot into asserting a PAST release; the gateway's has to be derived from the version
    this run is publishing, so a stale build layer serving the previous release cannot pass.
    And that version is compared AS A VERSION, by the image's own updater: a text match failed
    the first release candidate, whose package prints `0.3.0rc1` for the tag `v0.3.0-rc.1`.
    """
    jobs = _workflow().get("jobs")
    assert isinstance(jobs, dict)
    images = jobs.get("images")
    assert isinstance(images, dict)
    legs = images["strategy"]["matrix"]["include"]
    assert isinstance(legs, list) and legs

    for leg in legs:
        assert isinstance(leg, dict)
        declared = [leg.get(key) for key in ("expect", "expect_version")]
        assert any(isinstance(value, str) and value.strip() for value in declared), (
            f"image leg {leg.get('name')!r} smokes {leg.get('smoke')!r} with no `expect` or "
            "`expect_version`, so the workflow asserts only an exit status and an empty output "
            "would pass"
        )

    gateway = next(leg for leg in legs if leg.get("name") == "gateway")
    assert gateway["expect_version"] == "${{ needs.build.outputs.version }}", (
        "the gateway smoke must require the version being RELEASED, not any version: a "
        "cached builder layer would otherwise ship a stale venv under a `:latest` that lies"
    )
    assert "expect" not in gateway, "the gateway's version is compared as a version, not as text"

    smoke_step = _step(images, name="Smoke both arches (release-blocking)")
    environment = smoke_step.get("env")
    assert isinstance(environment, dict)
    assert environment.get("EXPECT") == "${{ matrix.expect }}"
    assert environment.get("EXPECT_VERSION") == "${{ matrix.expect_version }}"
    script = smoke_step.get("run")
    assert isinstance(script, str)
    assert (
        'if [ -z "$EXPECT" ] && [ -z "$EXPECT_VERSION" ]; then' in script
    ), "the step must refuse a leg with no expectation; `case $output in *''*)` matches anything"
    assert '*"$EXPECT"*)' in script, "the captured output must be tested against the expectation"
    assert (
        '--installed reports --expect "$EXPECT_VERSION" --output "$output"' in script
        and "< scripts/release_version.py" in script
    ), "the version must be compared by the image's own updater, on the output the smoke captured"
    assert "2>&1" in script, (
        "stderr must be captured: `nginx -v` writes its banner there, so a stdout-only "
        "capture reads EMPTY and reds the web leg on a working image"
    )
