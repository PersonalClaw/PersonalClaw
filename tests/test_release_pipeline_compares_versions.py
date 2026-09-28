"""The release workflow compares versions as versions: a dry run of its own commands.

The first release candidate would have failed its release. The images job's smoke required the
text ``personalclaw 0.3.0-rc.1`` from a binary that prints ``personalclaw 0.3.0rc1`` (the build
normalizes a candidate's spelling), ``verify_wheel`` compared the wheel's ``0.3.0rc1`` with
``pyproject.toml``'s ``0.3.0-rc.1`` as text, and the notes lookup read a heading spelled
differently from the tag as no heading at all. Every one of those now goes through
``scripts/release_version.py``, which asks ``personalclaw.self_update`` — the updater's parse.

Nothing here triggers a release. The steps' own ``run:`` scripts are read out of
``release.yml`` and executed with ``docker`` and ``uv`` replaced by stand-ins on ``PATH``, and
the version they are handed comes from ``scripts/release_tags.py`` classifying a tag, the way
the ``build`` job hands it on. What runs is the workflow's shell, the workflow's version and the
real comparison script; only the image and the package index are not real.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from personalclaw.self_update import same_version

REPO = Path(__file__).resolve().parents[1]
RELEASE_YML = REPO / ".github" / "workflows" / "release.yml"
SCRIPT = REPO / "scripts" / "release_version.py"

#: The workflow's two spellings of the version `build` classified.
_VERSION_EXPRESSIONS = ("${{ needs.build.outputs.version }}", "${{ steps.ver.outputs.version }}")


def _jobs() -> dict:
    workflow = yaml.safe_load(RELEASE_YML.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow["jobs"]


def _step(job: dict, name: str) -> dict:
    steps = [step for step in job["steps"] if step.get("name") == name]
    assert len(steps) == 1, f"expected one step named {name!r} in the job"
    return steps[0]


def _classified_version(ref: str) -> str:
    """The version the `build` job hands on for *ref*: `release_tags.py --github-output`."""
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "release_tags.py"),
            "--ref",
            ref,
            "--github-output",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    versions = [
        line.split("=", 1)[1] for line in proc.stdout.splitlines() if line.startswith("version=")
    ]
    assert len(versions) == 1, proc.stdout
    return versions[0]


def _expand(value: str, version: str) -> str:
    for expression in _VERSION_EXPRESSIONS:
        value = value.replace(expression, version)
    assert "${{" not in value, f"an expression this dry run does not model: {value!r}"
    return value


def _stand_in(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


# ── the images job: the per-arch smoke ────────────────────────────────────────────────


_FAKE_DOCKER = """
    import os, subprocess, sys

    args = sys.argv[1:]
    if args[0] == "pull":
        sys.exit(0)
    assert args[0] == "run", args
    arch = args[args.index("--platform") + 1].split("/", 1)[1]
    command = args[args.index(os.environ["IMAGE"]) + 1:]
    if command[:2] == ["sh", "-c"]:
        # What the image's smoke command prints on this arch.
        print(os.environ.get(f"BANNER_{arch}", os.environ["BANNER"]))
        sys.exit(0)
    if command[:2] == ["python", "-"]:
        # The image's Python running the script piped in: here, this venv's, which has the
        # package installed exactly as the image does.
        sys.exit(subprocess.run([sys.executable, *command[1:]], stdin=sys.stdin).returncode)
    sys.exit(f"this dry run does not model: docker {' '.join(args)}")
"""


def _smoke(tmp_path: Path, leg_name: str, ref: str, banner: str, **per_arch: str):
    """Run the images job's smoke step for one matrix leg, as released for *ref*."""
    images = _jobs()["images"]
    legs = {leg["name"]: leg for leg in images["strategy"]["matrix"]["include"]}
    leg = legs[leg_name]
    step = _step(images, "Smoke both arches (release-blocking)")
    version = _classified_version(ref)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stand_in(bin_dir, "docker", _FAKE_DOCKER)

    env = dict(os.environ)
    for key, value in step["env"].items():
        value = str(value).replace("${{ matrix.name }}", leg["name"])
        for field in ("smoke", "expect", "expect_version"):
            value = value.replace(f"${{{{ matrix.{field} }}}}", str(leg.get(field, "")))
        env[key] = _expand(value, version)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["BANNER"] = banner
    env.update({f"BANNER_{arch}": text for arch, text in per_arch.items()})
    return subprocess.run(
        ["bash", "-e", "-c", step["run"]], cwd=REPO, env=env, capture_output=True, text=True
    )


def test_a_release_candidate_image_passes_its_smoke(tmp_path) -> None:
    """The defect: `v0.3.0-rc.1`'s image prints `personalclaw 0.3.0rc1`. One release."""
    result = _smoke(tmp_path, "gateway", "v0.3.0-rc.1", "personalclaw 0.3.0rc1")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_stable_image_passes_its_smoke(tmp_path) -> None:
    result = _smoke(tmp_path, "gateway", "v0.3.0", "personalclaw 0.3.0")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "banner, per_arch",
    [
        ("personalclaw 0.2.0", {}),  # a stale builder layer: the PREVIOUS release, on both
        ("personalclaw 0.3.0rc1", {"arm64": "personalclaw 0.2.0"}),  # stale on one arch only
        ("personalclaw 0.3.0", {}),  # the release, where the candidate was tagged
        ("", {}),  # a version command that printed nothing
    ],
)
def test_an_image_that_is_not_the_tagged_release_fails_its_smoke(
    tmp_path, banner: str, per_arch: dict[str, str]
) -> None:
    result = _smoke(tmp_path, "gateway", "v0.3.0-rc.1", banner, **per_arch)
    assert result.returncode != 0, result.stdout
    assert "did not report version 0.3.0-rc.1" in result.stdout


def test_the_web_image_keeps_its_banner_check(tmp_path) -> None:
    ok = _smoke(tmp_path, "web", "v0.3.0-rc.1", "nginx version: nginx/1.27.3")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    bad = _smoke(tmp_path, "web", "v0.3.0-rc.1", "sh: nginx: not found")
    assert bad.returncode != 0 and "did not report 'nginx version:'" in bad.stdout


# ── the build job: the wheel is the tagged release ──────────────────────────────────


def test_the_build_job_requires_the_wheel_to_be_the_tagged_release() -> None:
    script = _step(_jobs()["build"], "Verify the wheel contract (SPA packaged + Node-free serve)")[
        "run"
    ]
    assert "scripts/verify_wheel.py" in script
    assert '--release-version "${{ steps.ver.outputs.version }}"' in script


@pytest.fixture
def verify_wheel():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_verify_wheel", REPO / "scripts" / "verify_wheel.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _project(tmp_path: Path, version: str) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "personalclaw"\nversion = "{version}"\n', encoding="utf-8"
    )
    return root


def test_a_candidates_wheel_is_its_pinned_version_and_its_tag(verify_wheel, tmp_path) -> None:
    """`pyproject.toml` pins `0.3.0-rc.1`; the wheel's metadata says `0.3.0rc1`. One release."""
    root = _project(tmp_path, "0.3.0-rc.1")
    verify_wheel._assert_versions(
        Path(sys.executable),
        {"personalclaw-0.3.0rc1-py3-none-any.whl": "0.3.0rc1"},
        root=root,
        release_version=_classified_version("v0.3.0-rc.1"),
    )


@pytest.mark.parametrize(
    "pinned, metadata, tag, says",
    [
        ("0.3.0-rc.1", "0.3.0rc2", "", "is not pyproject.toml's version"),
        (
            "0.2.0",
            "0.2.0",
            "v0.3.0-rc.1",
            "not the version being released",
        ),  # tagged before the bump
    ],
)
def test_a_wheel_that_is_not_the_release_fails_before_anything_publishes(
    verify_wheel, tmp_path, capsys, pinned: str, metadata: str, tag: str, says: str
) -> None:
    root = _project(tmp_path, pinned)
    with pytest.raises(SystemExit):
        verify_wheel._assert_versions(
            Path(sys.executable),
            {"the.whl": metadata},
            root=root,
            release_version=_classified_version(tag) if tag else "",
        )
    assert says in capsys.readouterr().err


def test_an_artifact_that_cannot_answer_fails_instead_of_borrowing_the_tree(
    verify_wheel, tmp_path, capsys
) -> None:
    """A gate asking a BUILT artifact which version it is must get that artifact's answer.

    `--installed` refuses the script's fallback to the checkout's `src/`, so an interpreter that
    cannot import the package exits 2, "could not run" — neither a match nor a mismatch — and
    `verify_wheel` fails on that instead of reporting a version mismatch. The stand-in below is
    such an interpreter: this one with its site-packages switched off (`-S`).
    """
    proc = subprocess.run(
        [sys.executable, "-I", "-S", str(SCRIPT), "--installed", "same", "0.2.0", "0.2.0"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert "cannot load the updater's version parse" in proc.stderr

    bare = tmp_path / "bare-python"
    bare.write_text(f'#!/bin/sh\nexec "{sys.executable}" -S "$@"\n', encoding="utf-8")
    bare.chmod(0o755)
    with pytest.raises(SystemExit):
        verify_wheel._same_version(bare, "0.2.0", "0.2.0")
    assert "did not run in the installed wheel" in capsys.readouterr().err


def test_a_package_older_than_the_parse_cannot_answer_either(
    verify_wheel, tmp_path, capsys
) -> None:
    """A stale build — the previous release's code under this release's metadata — imports fine
    and has no `same_version`. That is a comparison that did not run, not a version mismatch.
    """
    older = tmp_path / "older" / "personalclaw"
    older.mkdir(parents=True)
    (older / "__init__.py").write_text("", encoding="utf-8")
    (older / "self_update.py").write_text(
        "def normalize_version(v):\n    return v\n", encoding="utf-8"
    )
    stale = tmp_path / "stale-python"
    stale.write_text(
        f'#!/bin/sh\nPYTHONPATH="{older.parent}" exec "{sys.executable}" -S "$@"\n',
        encoding="utf-8",
    )
    stale.chmod(0o755)

    proc = subprocess.run(
        [str(stale), str(SCRIPT), "--installed", "same", "0.2.0", "0.2.0"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert "has no attribute 'same_version'" in proc.stderr

    # `verify_wheel` passes `-I`, which would drop that PYTHONPATH, so the stand-in for the
    # stale wheel's interpreter ignores its arguments' flags and keeps the stale package.
    keeps = tmp_path / "stale-wheel-python"
    keeps.write_text(
        f'#!/bin/sh\n[ "$1" = "-I" ] && shift\n'
        f'PYTHONPATH="{older.parent}" exec "{sys.executable}" -S "$@"\n',
        encoding="utf-8",
    )
    keeps.chmod(0o755)
    with pytest.raises(SystemExit):
        verify_wheel._same_version(keeps, "0.3.0rc1", "0.3.0-rc.1")
    err = capsys.readouterr().err
    assert "did not run in the installed wheel" in err
    assert "is not pyproject.toml's version" not in err


# ── the notes: the CHANGELOG section of this version, found by version ─────────────


def _notes(tmp_path: Path, ref: str, changelog: str):
    """Run the build job's notes step for *ref* over *changelog*, with uv standing in.

    uv hands the step an interpreter with `packaging` and nothing else, PersonalClaw not
    installed, so the script reads the updater's parse from the checkout's `src/`. The stand-in
    gives it exactly that: this Python without its site-packages (`-S`), `packaging` alone on
    its path, and a checkout whose `src/` is this repository's. A third-party import added to
    the updater would fail here, not in the release.
    """
    import packaging

    step = _step(_jobs()["build"], "Resolve this release's CHANGELOG section")
    workdir = tmp_path / "checkout"
    (workdir / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, workdir / "scripts" / SCRIPT.name)
    (workdir / "src").symlink_to(REPO / "src", target_is_directory=True)
    (workdir / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    only_packaging = tmp_path / "only-packaging"
    only_packaging.mkdir()
    (only_packaging / "packaging").symlink_to(
        Path(packaging.__file__).parent, target_is_directory=True
    )
    # Not vacuous: the product's own dependencies are not importable in that interpreter.
    bare = subprocess.run(
        [sys.executable, "-S", "-c", "import aiohttp"],
        env=dict(os.environ, PYTHONPATH=str(only_packaging)),
        capture_output=True,
    )
    assert bare.returncode != 0, "the stand-in's interpreter can import the product's dependencies"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stand_in(
        bin_dir,
        "uv",
        f"""
        import os, subprocess, sys

        args = sys.argv[1:]
        assert args[:4] == ["run", "--no-project", "--with", "packaging"], args
        assert args[4] == "python", args
        env = dict(os.environ, PYTHONPATH={str(only_packaging)!r})
        sys.exit(subprocess.run([sys.executable, "-S", *args[5:]], env=env).returncode)
        """,
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    for key, value in step["env"].items():
        env[key] = _expand(str(value), _classified_version(ref))
    result = subprocess.run(
        ["bash", "-e", "-c", step["run"]], cwd=workdir, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (workdir / "release-notes.md").read_text(encoding="utf-8").strip()


_CHANGELOG = """# Changelog

## [Unreleased]

### Fixed

- **Not in this release.**

## [{heading}] — 2026-10-01

The candidate.

### Fixed

- **In this release.**

## [0.2.0] — 2026-09-23

The one before.
"""


@pytest.mark.parametrize("heading", ["0.3.0-rc.1", "0.3.0rc1"])
def test_the_notes_are_this_versions_section_however_its_heading_is_spelled(
    tmp_path, heading: str
) -> None:
    notes = _notes(tmp_path, "v0.3.0-rc.1", _CHANGELOG.format(heading=heading))
    assert notes.startswith("The candidate.")
    assert "In this release." in notes
    assert "Not in this release." not in notes and "The one before." not in notes


def test_a_version_with_no_section_gets_the_bare_notes(tmp_path) -> None:
    notes = _notes(tmp_path, "v0.3.0-rc.2", _CHANGELOG.format(heading="0.3.0-rc.1"))
    assert notes == "Release 0.3.0-rc.2."


def test_the_release_job_publishes_the_notes_the_build_resolved() -> None:
    """The job that can create the release fetches nothing: it takes `build`'s file."""
    build = _jobs()["build"]
    uploads = [s for s in build["steps"] if s.get("with", {}).get("name") == "release-notes"]
    assert uploads and uploads[0]["with"]["path"] == "release-notes.md"
    notes = _jobs()["notes"]
    downloads = [s for s in notes["steps"] if s.get("with", {}).get("name") == "release-notes"]
    assert downloads, "the notes job must download the notes the build job resolved"
    create = _step(notes, "Create GitHub Release")["run"]
    assert "--notes-file release-notes.md" in create
    assert not any("CHANGELOG.md" in str(s.get("run", "")) for s in notes["steps"])


def test_the_tag_and_the_package_name_one_release() -> None:
    """The classifier's version for a candidate tag, and the spelling its package reports."""
    assert _classified_version("v0.3.0-rc.1") == "0.3.0-rc.1"
    assert same_version(_classified_version("v0.3.0-rc.1"), "0.3.0rc1")
    assert not same_version(_classified_version("v0.3.0-rc.1"), "0.3.0")
