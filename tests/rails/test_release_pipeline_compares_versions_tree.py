"""The source-tree checks split from
``tests/test_release_pipeline_compares_versions.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import release_pipeline_compares_versions_shared
import yaml
from release_pipeline_compares_versions_shared import REPO, SCRIPT, _classified_version, _pointer

release_version = release_pipeline_compares_versions_shared.release_version


RELEASE_YML = REPO / ".github" / "workflows" / "release.yml"
#: Where the release builds and smokes: release.yml calls it as its `artifacts` job.
ARTIFACTS_YML = REPO / ".github" / "workflows" / "release-artifacts.yml"


#: The workflow's two spellings of the version `build` classified.
_VERSION_EXPRESSIONS = ("${{ needs.build.outputs.version }}", "${{ steps.ver.outputs.version }}")


def _jobs(path: Path = ARTIFACTS_YML) -> dict:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow["jobs"]


def _step(job: dict, name: str) -> dict:
    steps = [step for step in job["steps"] if step.get("name") == name]
    assert len(steps) == 1, f"expected one step named {name!r} in the job"
    return steps[0]


def _expand(value: str, version: str) -> str:
    for expression in _VERSION_EXPRESSIONS:
        value = value.replace(expression, version)
    assert "${{" not in value, f"an expression this dry run does not model: {value!r}"
    return value


def _stand_in(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


_FAKE_DOCKER = """
    import os, subprocess, sys

    args = sys.argv[1:]
    if args[0] in ("load", "image"):
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


#: skopeo, which copies one arch out of the layout to a docker archive the step then loads.
_FAKE_SKOPEO = """
    import sys
    from pathlib import Path

    args = sys.argv[1:]
    if args[:1] == ["--version"]:
        sys.exit(0)
    assert args[0] == "copy" and args[-1].startswith("docker-archive:"), args
    Path(args[-1].removeprefix("docker-archive:").split(":", 1)[0]).write_bytes(b"")
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
    _stand_in(bin_dir, "skopeo", _FAKE_SKOPEO)

    env = dict(os.environ, RUNNER_TEMP=str(tmp_path))
    for key, value in step["env"].items():
        value = str(value).replace("${{ matrix.name }}", leg["name"])
        value = value.replace("${{ runner.temp }}", str(tmp_path))
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


def test_the_build_job_requires_the_wheel_to_be_the_tagged_release() -> None:
    script = _step(_jobs()["build"], "Verify the wheel contract (SPA packaged + Node-free serve)")[
        "run"
    ]
    assert "scripts/verify_wheel.py" in script
    assert '--release-version "${{ steps.ver.outputs.version }}"' in script


def _locked_version(name: str) -> str:
    import tomllib

    lock = tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))
    versions = [p["version"] for p in lock["package"] if p["name"] == name]
    assert len(versions) == 1, f"uv.lock holds {name} {versions}"
    return versions[0]


def _notes(tmp_path: Path, ref: str, changelog: str):
    """Run the build job's notes step for *ref* over *changelog*, with uv standing in.

    uv hands the step an interpreter with `packaging` and nothing else, PersonalClaw not
    installed, so the script reads the version comparison from the checkout's `src/`. The
    stand-in gives it exactly that: this Python without its site-packages (`-S`), `packaging`
    alone on its path, and a checkout whose `src/` is this repository's. A third-party import
    added to the comparison would fail here, not in the release.
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
    # The step names packaging exactly, at the release uv.lock holds, so a newer packaging on
    # PyPI cannot change how a release's notes are found.
    pinned = f"packaging=={_locked_version('packaging')}"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _stand_in(
        bin_dir,
        "uv",
        f"""
        import os, subprocess, sys

        args = sys.argv[1:]
        assert args[:4] == ["run", "--no-project", "--with", {pinned!r}], args
        assert args[4] == "python", args
        env = dict(os.environ, PYTHONPATH={str(only_packaging)!r})
        sys.exit(subprocess.run([sys.executable, "-S", *args[5:]], env=env).returncode)
        """,
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    # What the runner sets for a tag push, so a test run inside CI does not read its own run's.
    env.update(
        GITHUB_SERVER_URL="https://github.com",
        GITHUB_REPOSITORY="PersonalClaw/PersonalClaw",
        RELEASE_REF=ref,
    )
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


#: GitHub refuses a release body longer than this ("body is too long (maximum is 125000
#: characters)"), and the job that creates the release runs after PyPI and the images published.
GITHUB_RELEASE_BODY_LIMIT = 125_000


def test_a_section_that_fits_is_the_notes_unchanged(tmp_path) -> None:
    changelog = _CHANGELOG.format(heading="0.3.0-rc.1")
    section = changelog.split("— 2026-10-01\n", 1)[1].split("\n## [0.2.0]", 1)[0].strip()
    assert _notes(tmp_path, "v0.3.0-rc.1", changelog) == section


#: The sections the oversized section's notes take in turn, and how many entries each holds.
_OPTIONAL = {"Added": 900, "Removed": 2, "Fixed": 600, "Security": 300}


def _oversized() -> tuple[str, dict[str, list[str]]]:
    """A CHANGELOG whose ``0.3.0-rc.1`` section is far over the budget, and the entries of each
    section its notes cut."""
    filler = "in a headline of ordinary length, " * 5
    entries = {
        name: [f"- **{name} {n:04d}, {filler}said once.**" for n in range(count)]
        for name, count in _OPTIONAL.items()
    }
    section = [
        "## [0.3.0-rc.1] — 2026-10-01",
        "",
        "What the candidate is about, in a paragraph",
        "that wraps onto a second line.",
        "",
        "### Highlights",
        "",
        "- **The first highlight.**",
        "- **The second highlight.**",
        "",
        "### ⚠️ Breaking changes — read before upgrading",
        "",
        "- **A break to read first.**",
    ]
    for name, lines in entries.items():
        section += ["", f"### {name}", "", *lines]
    head = "# Changelog\n\n## [Unreleased]\n\n"
    return head + "\n".join(section) + "\n\n## [0.2.0] — 2026-09-23\n", entries


def test_an_oversized_section_is_cut_on_entries_and_links_the_whole_section(
    tmp_path, release_version
) -> None:
    budget = release_version.NOTES_BUDGET
    changelog, entries = _oversized()
    assert len(changelog) > 2 * budget, "not oversized, so the cut would go untested"
    notes = _notes(tmp_path, "v0.3.0-rc.1", changelog)
    lines = notes.split("\n")

    assert len(notes) <= budget
    assert notes.startswith("What the candidate is about, in a paragraph\nthat wraps onto")
    for whole in ("- **The first highlight.**", "- **The second highlight.**"):
        assert whole in lines, f"the Highlights lost {whole!r}"
    assert "- **A break to read first.**" in lines, "the breaking changes were cut"
    shown = {name: [line for line in lines if line.startswith(f"- **{name} ")] for name in entries}
    for name, lines_of in shown.items():
        assert (
            lines_of == entries[name][: len(lines_of)]
        ), f"{name} is not cut on an entry, in order"
    assert shown["Removed"] == entries["Removed"], "a short section is shown whole"
    open_counts = [len(shown[name]) for name in ("Added", "Fixed", "Security")]
    assert 0 < min(open_counts) and max(open_counts) - min(open_counts) <= 1, open_counts
    assert all(c < len(entries[n]) for c, n in zip(open_counts, ("Added", "Fixed", "Security")))
    total = sum(map(len, entries.values())) + 3
    count = sum(map(len, shown.values())) + 3
    url = "https://github.com/PersonalClaw/PersonalClaw/blob/v0.3.0-rc.1/CHANGELOG.md"
    pointer = _pointer(count, total, f"{url}#030-rc1--2026-10-01")
    assert lines[-1] == pointer
    # Filled to the budget: the entry whose turn was next is the one that did not fit, measured
    # as the fill measures, with the pointer at its longest.
    turn = min(
        ("Added", "Fixed", "Security"), key=lambda n: (len(shown[n]), list(entries).index(n))
    )
    following = entries[turn][len(shown[turn])]
    longest = len(notes) - len(pointer) + len(_pointer(total, total, f"{url}#030-rc1--2026-10-01"))
    assert longest + len(following) + 1 > budget


def test_the_release_being_cut_has_notes_a_github_release_accepts(
    tmp_path, release_version
) -> None:
    """The newest release of the real CHANGELOG, the version pyproject names, as the build job
    resolves it: within GitHub's limit, opening with its introduction and, when cut, ending with
    the link to its whole section at its tag."""
    import tomllib

    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    notes = _notes(tmp_path, f"v{version}", changelog)
    assert notes != f"Release {version}.", f"the CHANGELOG has no section for {version}"
    assert len(notes) <= GITHUB_RELEASE_BODY_LIMIT, f"{len(notes):,} characters"
    heading = next(line for line in changelog.split("\n") if line.startswith(f"## [{version}]"))
    section = changelog.split(heading + "\n", 1)[1].split("\n## [", 1)[0].strip()
    intro = section.split("\n### ", 1)[0].split("\n- ", 1)[0].strip()
    assert intro and notes.startswith(intro), "the notes must open with the introduction"
    if notes != section:
        url = f"https://github.com/PersonalClaw/PersonalClaw/blob/v{version}/CHANGELOG.md"
        anchor = release_version.heading_anchor(heading)
        assert notes.split("\n")[-1].endswith(f"[CHANGELOG.md]({url}#{anchor}).")


def test_the_release_job_publishes_the_notes_the_build_resolved() -> None:
    """The job that can create the release fetches nothing: it takes `build`'s file."""
    build = _jobs()["build"]
    uploads = [s for s in build["steps"] if s.get("with", {}).get("name") == "release-notes"]
    assert uploads and uploads[0]["with"]["path"] == "release-notes.md"
    notes = _jobs(RELEASE_YML)["notes"]
    downloads = [s for s in notes["steps"] if s.get("with", {}).get("name") == "release-notes"]
    assert downloads, "the notes job must download the notes the build job resolved"
    create = _step(notes, "Create GitHub Release")["run"]
    assert "--notes-file release-notes.md" in create
    assert not any("CHANGELOG.md" in str(s.get("run", "")) for s in notes["steps"])
