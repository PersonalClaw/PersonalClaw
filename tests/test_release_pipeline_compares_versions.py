"""The release workflow compares versions as versions: a dry run of its own commands.

The first release candidate would have failed its release. The images job's smoke required the
text ``personalclaw 0.3.0-rc.1`` from a binary that prints ``personalclaw 0.3.0rc1`` (the build
normalizes a candidate's spelling), ``verify_wheel`` compared the wheel's ``0.3.0rc1`` with
``pyproject.toml``'s ``0.3.0-rc.1`` as text, and the notes lookup read a heading spelled
differently from the tag as no heading at all. Every one of those now goes through
``scripts/release_version.py``, which asks ``personalclaw.versions`` — the one comparison the
updater and every other version check use.

Nothing here triggers a release. The steps' own ``run:`` scripts are read out of
``release.yml`` and executed with ``docker`` and ``uv`` replaced by stand-ins on ``PATH``, and
the version they are handed comes from ``scripts/release_tags.py`` classifying a tag, the way
the ``build`` job hands it on. What runs is the workflow's shell, the workflow's version and the
real comparison script; only the image and the package index are not real.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import release_pipeline_compares_versions_shared
from release_pipeline_compares_versions_shared import REPO, SCRIPT, _classified_version, _pointer

from personalclaw.versions import same_version

release_version = release_pipeline_compares_versions_shared.release_version


# ── the images job: the per-arch smoke ────────────────────────────────────────────────


# ── the build job: the wheel is the tagged release ──────────────────────────────────


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
    assert "cannot load PersonalClaw's version comparison" in proc.stderr

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
    and has no version comparison. That is a comparison that did not run, not a mismatch.
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
    assert "cannot load PersonalClaw's version comparison" in proc.stderr
    assert "versions" in proc.stderr

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


# ── the notes fit a GitHub Release ────────────────────────────────────────────────────────


def test_an_entry_is_never_split_and_what_is_kept_whole_must_fit(release_version) -> None:
    one, three = "- **" + "a" * 100 + "**", "- **" + "c" * 100 + "**"
    two = "- **" + "b" * 60 + "\n  " + "b" * 60 + "**"  # one entry, written on two lines
    body = "\n".join(["Intro.", "", "### Fixed", "", one, two, three])
    url = "https://example.test/changelog#100"
    expected = "\n".join(["Intro.", "", "### Fixed", "", one, "", _pointer(1, 3, url)])
    budget = len(expected) + len(two) // 2
    assert len(body) > budget
    assert release_version.release_notes(body, full_list=url, budget=budget) == expected

    kept_whole = "### Highlights\n\n- **" + "x" * 200 + "**\n\n### Fixed\n\n- **y**"
    with pytest.raises(ValueError, match="kept whole"):
        release_version.release_notes(kept_whole, full_list=url, budget=150)


def test_the_anchor_is_the_one_github_gives_a_release_heading(release_version) -> None:
    # As GitHub renders this CHANGELOG at v0.1.3: `#013--2026-07-30` and `#unreleased`.
    assert release_version.heading_anchor("## [0.1.3] — 2026-07-30") == "013--2026-07-30"
    assert release_version.heading_anchor("## [Unreleased]") == "unreleased"
    assert release_version.heading_anchor("## [0.3.0-rc.1] — 2026-10-01") == "030-rc1--2026-10-01"


def test_the_tag_and_the_package_name_one_release() -> None:
    """The classifier's version for a candidate tag, and the spelling its package reports."""
    assert _classified_version("v0.3.0-rc.1") == "0.3.0-rc.1"
    assert same_version(_classified_version("v0.3.0-rc.1"), "0.3.0rc1")
    assert not same_version(_classified_version("v0.3.0-rc.1"), "0.3.0")
