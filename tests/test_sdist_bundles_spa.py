"""The sdist must graft the built SPA so a wheel built FROM it carries the SPA.

The release job's ``uv build`` builds the sdist first, then the wheel from that sdist, and
``make build`` rebuilds a wheel from the sdist and requires it to match the one built from the
tree. setup.py's ``BuildWithWeb`` copies ``web/dist`` into ``personalclaw/static/dist`` only
when ``web/dist/index.html`` exists in the build tree — so if the sdist omits ``web/dist``, the
wheel-from-sdist is SPA-less and ``scripts/verify_wheel.py`` fails (the gateway can't serve
``/``).

``MANIFEST.in``'s ``graft web/dist`` is what puts the SPA into the sdist. The first two tests
guard that graft statically (cheap — no build). The rest drive setuptools' real PEP 517
backend over a small package built from this repository's own ``setup.py`` and
``MANIFEST.in``, complementing the full build-install-serve check ``verify_wheel.py`` runs.

Regression: caught 2026-07-21 during the plan-34 release dry-run — the release
pipeline had never run (no tag pushed) and `python -m build` produced a SPA-less
wheel because there was no MANIFEST.in.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _REPO_ROOT / "MANIFEST.in"


def test_manifest_exists() -> None:
    assert _MANIFEST.is_file(), (
        "MANIFEST.in is missing — without it the sdist omits web/dist and the "
        "wheel built from the sdist (release job / `make build`) ships no SPA"
    )


def test_manifest_grafts_web_dist() -> None:
    """A ``graft web/dist`` line must be present (comments/whitespace tolerant)."""
    text = _MANIFEST.read_text(encoding="utf-8")
    grafts = {
        line.split(None, 1)[1].strip().replace("\\", "/")
        for line in text.splitlines()
        if re.match(r"^\s*graft\s+\S", line)
    }
    assert "web/dist" in grafts, (
        f"MANIFEST.in must `graft web/dist` so the sdist carries the built SPA; "
        f"found grafts={sorted(grafts)}"
    )


def _fixture_project(tmp_path: Path, *, dist_files: dict[str, str]) -> Path:
    """A one-package project built by THIS repository's ``setup.py`` and ``MANIFEST.in``."""
    project = tmp_path / "project"
    package = project / "src" / "personalclaw"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(_REPO_ROOT / "setup.py", project / "setup.py")
    shutil.copy2(_MANIFEST, project / "MANIFEST.in")
    (project / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (project / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        """
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "personalclaw"
version = "0.0.1"
description = "Packaging fixture"
readme = "README.md"
requires-python = ">=3.12"
license = "MIT"

[tool.setuptools.packages.find]
where = ["src"]
""".lstrip(),
        encoding="utf-8",
    )
    for relative, body in dist_files.items():
        target = project / "web" / "dist" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return project


def _backend(project: Path, call: str) -> str:
    """Run one setuptools ``build_meta`` hook in a fresh interpreter; return the artifact name.

    In-process through the PEP 517 hooks, like ``test_scan_rule_gloss``: no ``build`` CLI, no
    network, and the setuptools under test is the one ``uv.lock`` installs.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import warnings; warnings.filterwarnings('ignore')\n"
            f"from setuptools import build_meta\nprint(build_meta.{call})",
        ],
        cwd=project,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"{call} failed:\n{proc.stderr[-3000:]}"
    return proc.stdout.strip().splitlines()[-1]


_SPA = {"index.html": "<!doctype html><script src=/assets/app.js></script>\n"}
_ASSET = {"assets/app.js": "console.log('fixture')\n"}


def _members(wheel: Path) -> set[str]:
    with zipfile.ZipFile(wheel) as archive:
        return set(archive.namelist())


def test_a_wheel_with_no_dashboard_still_builds(tmp_path: Path) -> None:
    """Deliberately NOT refused. The apps repo's CI, the app-template CI and the release's
    desktop jobs install core with no SPA, and the gateway answers `/` with its "Build the
    dashboard" page for exactly that install; `make build` is what fails a distribution that
    lacks the dashboard, because it builds one first."""
    project = _fixture_project(tmp_path, dist_files={})
    out = tmp_path / "wheel"
    out.mkdir()
    wheel = out / _backend(project, f"build_wheel({str(out)!r})")
    assert not {m for m in _members(wheel) if m.startswith("personalclaw/static/dist/")}


def test_a_dist_directory_without_its_index_is_not_bundled(tmp_path: Path) -> None:
    """A ``web/dist`` holding assets but no ``index.html`` is a failed or half-written build.
    It used to be copied whole, shipping a dashboard that cannot load."""
    project = _fixture_project(tmp_path, dist_files=_ASSET)
    out = tmp_path / "wheel"
    out.mkdir()
    wheel = out / _backend(project, f"build_wheel({str(out)!r})")
    shipped = {m for m in _members(wheel) if m.startswith("personalclaw/static/dist/")}
    assert shipped == set(), f"a dist with no index.html was bundled: {sorted(shipped)}"


@pytest.mark.timeout(180)
def test_a_wheel_rebuilt_from_the_sdist_carries_the_whole_dashboard(tmp_path: Path) -> None:
    """The published sdist is enough to rebuild the user-facing wheel, dashboard included."""
    project = _fixture_project(tmp_path, dist_files={**_SPA, **_ASSET})
    sdist_out = tmp_path / "sdist"
    sdist_out.mkdir()
    sdist = sdist_out / _backend(project, f"build_sdist({str(sdist_out)!r})")

    extracted = tmp_path / "extracted"
    with tarfile.open(sdist, "r:gz") as archive:
        archive.extractall(extracted, filter="data")
    source = next(extracted.iterdir())
    wheel_out = tmp_path / "rebuilt"
    wheel_out.mkdir()
    wheel = wheel_out / _backend(source, f"build_wheel({str(wheel_out)!r})")

    assert {
        "personalclaw/static/dist/index.html",
        "personalclaw/static/dist/assets/app.js",
    } <= _members(wheel)
