"""Version single-sourcing consistency (contract C3).

pyproject.toml ``[project].version`` is the single source of truth for the package
version. This test asserts every surface a release exposes agrees with it:

  1. ``personalclaw.__version__``  (importlib.metadata when installed, literal
     fallback on a source tree)
  2. ``personalclaw._FALLBACK_VERSION``  (what a bare, uninstalled checkout reports)
  3. the latest release heading in ``CHANGELOG.md``  (``## [X.Y.Z] — DATE``,
     skipping the ``[Unreleased]`` section)
  4. ``packages/personalclaw-client-py/pyproject.toml`` ``[project].version``
     (the client releases in lockstep with core)
  5. ``src/personalclaw/acp/client.py`` ``CLIENT_VERSION`` (sent in the ACP
     initialize handshake)
  6. the ``README.md`` pre-1.0 banner's ``PersonalClaw is at **vX.Y.Z**`` sentence
  7. ``desktop/package.json`` ``version`` (electron-builder derives the released
     dmg/AppImage/deb filenames from it)

If any of them drift, this test goes red — that is the guardrail that keeps
`pip show`, `personalclaw --version`, the in-app "what's new" panel, and the names
of the desktop artifacts a release publishes honest.

They are compared as VERSIONS, with the one comparison the product uses
(``personalclaw.versions``), never as text. A release candidate is written ``0.3.0-rc.1``
everywhere a release cut writes it — the tag's spelling — and the installed package reports
it as ``0.3.0rc1``, because the build normalizes it: one release, which a text comparison
read as two, so every check here failed the first candidate.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

import personalclaw
from personalclaw.versions import parse_version, same_version

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"

# ``## [0.1.0] — 2026-07-19`` — the first heading whose bracketed text is a VERSION wins, so
# ``[Unreleased]`` is skipped and a candidate's ``## [0.3.0-rc.1]`` counts. Whatever follows the
# bracket (a date, or none on an in-progress release) is not read.
_RELEASE_HEADING = re.compile(r"^##\s*\[(?P<version>[^\]\n]+)\]", re.MULTILINE)

#: The README's pre-1.0 banner, ``PersonalClaw is at **v0.2.0**`` — or ``**v0.3.0-rc.1**`` on a
#: candidate: whatever the cut wrote between ``**v`` and ``**``, which has to parse as a version.
_README_BANNER = re.compile(r"PersonalClaw is at \*\*v(?P<version>[^*\s]+)\*\*")


def _pyproject_version() -> str:
    with _PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return str(data["project"]["version"])


def _changelog_latest_version(text: str | None = None) -> str:
    text = _CHANGELOG.read_text(encoding="utf-8") if text is None else text
    for match in _RELEASE_HEADING.finditer(text):
        version = match.group("version").strip()
        if parse_version(version) is not None:
            return version
    raise AssertionError(
        "no release heading (## [X.Y.Z]) found in CHANGELOG.md — "
        "add a dated release section below [Unreleased]"
    )


def test_pyproject_and_module_version_agree() -> None:
    """``personalclaw.__version__`` matches pyproject's ``[project].version``.

    Under an editable/wheel install importlib.metadata returns the pyproject
    version; on a bare source tree the literal fallback must also track it, so
    the two must always agree.
    """
    assert same_version(personalclaw.__version__, _pyproject_version()), (
        f"personalclaw.__version__={personalclaw.__version__!r} disagrees with "
        f"pyproject [project].version={_pyproject_version()!r}"
    )


def test_module_fallback_tracks_pyproject() -> None:
    """The source-tree fallback literal must equal the pyproject version.

    This is what a raw ``python -m personalclaw`` from an uninstalled checkout
    reports; it must not drift from the packaged version.
    """
    assert same_version(personalclaw._FALLBACK_VERSION, _pyproject_version()), (
        f"_FALLBACK_VERSION={personalclaw._FALLBACK_VERSION!r} disagrees with "
        f"pyproject [project].version={_pyproject_version()!r} — bump both together"
    )


def test_changelog_latest_matches_pyproject() -> None:
    """The newest dated CHANGELOG heading matches the pyproject version.

    A release is only cut once the changelog names it, so at release time the
    latest release heading must equal the pyproject version.
    """
    assert same_version(_changelog_latest_version(), _pyproject_version()), (
        f"CHANGELOG latest release={_changelog_latest_version()!r} disagrees with "
        f"pyproject [project].version={_pyproject_version()!r} — add/rename the "
        "release heading to match before cutting the release"
    )


def _client_version() -> str:
    with (_REPO_ROOT / "packages" / "personalclaw-client-py" / "pyproject.toml").open("rb") as fh:
        data = tomllib.load(fh)
    return str(data["project"]["version"])


def test_client_version_locksteps_core() -> None:
    """``personalclaw-client`` releases in LOCKSTEP with core (owner policy,
    2026-07-22): the client's version always equals core's, bumped every release
    whether or not the client changed.

    This keeps the two PyPI packages' versioning legible (client X.Y.Z pairs
    with core X.Y.Z) and makes the release pipeline idempotent — every tag
    carries a fresh, publishable client version, so the pypi-client job can
    never fail on PyPI's no-re-upload rule for an unchanged package.
    """
    assert same_version(_client_version(), _pyproject_version()), (
        f"personalclaw-client version={_client_version()!r} disagrees with core "
        f"version={_pyproject_version()!r} — bump packages/personalclaw-client-py/"
        "pyproject.toml in the same release-prep commit (lockstep policy)"
    )


def _acp_client_version() -> str:
    text = (_REPO_ROOT / "src" / "personalclaw" / "acp" / "client.py").read_text(encoding="utf-8")
    m = re.search(r'^CLIENT_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "CLIENT_VERSION literal not found in acp/client.py"
    return m.group(1)


def test_acp_client_version_tracks_core() -> None:
    """The version PersonalClaw announces to an ACP agent must be its real one.

    This was found hardcoded at ``0.1.2`` during the 0.1.3 release — the handshake
    told every ACP CLI the wrong version, which is the kind of thing that surfaces
    as an unreproducible compatibility report months later.
    """
    assert same_version(_acp_client_version(), _pyproject_version()), (
        f"acp/client.py CLIENT_VERSION={_acp_client_version()!r} disagrees with core "
        f"version={_pyproject_version()!r} — it is sent in the ACP initialize handshake"
    )


def _readme_declared_version(text: str | None = None) -> str:
    text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8") if text is None else text
    m = _README_BANNER.search(text)
    assert m, "the pre-1.0 banner's 'PersonalClaw is at **vX.Y.Z**' sentence is missing"
    version = m.group("version")
    assert parse_version(version) is not None, f"the README banner's v{version} is not a version"
    return version


def test_readme_banner_version_tracks_core() -> None:
    """The README's pre-1.0 banner states a version; it must be the current one.

    Unenforced, it drifted three releases (it still said v0.1.0 at v0.1.3) — and it
    sits in the one paragraph warning users their data may break, where being wrong
    about which release they are reading about is worst.
    """
    assert same_version(_readme_declared_version(), _pyproject_version()), (
        f"README pre-1.0 banner says v{_readme_declared_version()} but core is "
        f"v{_pyproject_version()} — update the banner in the release-prep commit"
    )


def _desktop_shell_version() -> str:
    text = (_REPO_ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
    return str(json.loads(text)["version"])


def test_desktop_shell_version_tracks_core() -> None:
    """``desktop/package.json`` names the desktop artifacts a release publishes.

    ``desktop/package.json`` declares no ``build.artifactName``, so electron-builder
    falls back to ``${productName}-${version}-${arch}.${ext}`` and this field — not
    pyproject, not the tag — decides the filename. ``.github/workflows/release.yml``
    then uploads ``desktop/dist/*.dmg`` verbatim, so a stale value here does not fail
    the build: it publishes a correctly built dmg whose name states the wrong release,
    and only at tag time, when it is most expensive to fix.

    It drifted three patch versions unenforced (``0.1.0`` while core was ``0.1.3``)
    because no check in the gate read this file for a version at all.
    """
    assert same_version(_desktop_shell_version(), _pyproject_version()), (
        f"desktop/package.json version={_desktop_shell_version()!r} disagrees with core "
        f"version={_pyproject_version()!r} — electron-builder derives the released "
        f"artifact filenames from this field (PersonalClaw-{_desktop_shell_version()}"
        f"-<arch>.dmg), so leaving it stale ships a dmg misnamed for the release; bump "
        "it in the release-prep commit"
    )


def test_a_release_candidate_cut_passes_every_check() -> None:
    """The first release candidate, cut the way the release runbook says: the tag's spelling
    everywhere the cut writes a version, while the INSTALLED package reports the spelling the
    build normalizes it to. Every check here read those as two releases.

    Drives the same extractions and the same comparison the checks above make, over a cut's
    texts, since this repository's own files will only look like this on the day of a cut.
    """
    cut = "0.3.0-rc.1"
    installed = "0.3.0rc1"  # what setuptools writes into the metadata for that pin
    changelog = f"## [Unreleased]\n\n### Fixed\n\n## [{cut}] — 2026-10-01\n\nThe candidate.\n"
    readme = f"> PersonalClaw is at **v{cut}**, pre-1.0 — data may break between releases."

    assert _changelog_latest_version(changelog) == cut
    assert _readme_declared_version(readme) == cut
    for surface in (
        installed,
        _changelog_latest_version(changelog),
        _readme_declared_version(readme),
    ):
        assert same_version(surface, cut), f"{surface} read as a different release from {cut}"

    # Not vacuous: a different candidate, or its release, is a different version.
    assert not same_version(installed, "0.3.0-rc.2")
    assert not same_version(installed, "0.3.0")
    stale = readme.replace(cut, "0.2.0")
    assert not same_version(_readme_declared_version(stale), cut)


def test_the_banner_must_name_a_version() -> None:
    """A banner the release cut left garbled fails loudly rather than comparing as nothing."""
    with pytest.raises(AssertionError, match="not a version"):
        _readme_declared_version("PersonalClaw is at **vnext**")
