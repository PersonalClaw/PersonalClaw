"""Version single-sourcing consistency (plan 34 T1.2, contract C3).

pyproject.toml is the single source of truth for the package version. This test
asserts the three surfaces a release exposes agree:

  1. pyproject.toml  ``[project].version``
  2. ``personalclaw.__version__``  (importlib.metadata when installed, literal
     fallback on a source tree)
  3. the latest release heading in ``CHANGELOG.md``  (``## [X.Y.Z] — DATE``,
     skipping the ``[Unreleased]`` section)

If any of the three drift, this test goes red — that is the guardrail that keeps
`pip show`, `personalclaw --version`, and the in-app "what's new" panel honest.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import personalclaw

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"

# ``## [0.1.0] — 2026-07-19`` — first non-Unreleased release heading wins.
# Accept an em dash, en dash, or a plain hyphen as the date separator, and allow
# no date (in-progress release headings).
_RELEASE_HEADING = re.compile(
    r"^##\s*\[(?P<version>\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?)\]", re.MULTILINE
)


def _pyproject_version() -> str:
    with _PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return str(data["project"]["version"])


def _changelog_latest_version() -> str:
    text = _CHANGELOG.read_text(encoding="utf-8")
    for match in _RELEASE_HEADING.finditer(text):
        version = match.group("version")
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
    assert personalclaw.__version__ == _pyproject_version(), (
        f"personalclaw.__version__={personalclaw.__version__!r} disagrees with "
        f"pyproject [project].version={_pyproject_version()!r}"
    )


def test_module_fallback_tracks_pyproject() -> None:
    """The source-tree fallback literal must equal the pyproject version.

    This is what a raw ``python -m personalclaw`` from an uninstalled checkout
    reports; it must not drift from the packaged version.
    """
    assert personalclaw._FALLBACK_VERSION == _pyproject_version(), (
        f"_FALLBACK_VERSION={personalclaw._FALLBACK_VERSION!r} disagrees with "
        f"pyproject [project].version={_pyproject_version()!r} — bump both together"
    )


def test_changelog_latest_matches_pyproject() -> None:
    """The newest dated CHANGELOG heading matches the pyproject version.

    A release is only cut once the changelog names it, so at release time the
    latest release heading must equal the pyproject version.
    """
    assert _changelog_latest_version() == _pyproject_version(), (
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
    assert _client_version() == _pyproject_version(), (
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
    assert _acp_client_version() == _pyproject_version(), (
        f"acp/client.py CLIENT_VERSION={_acp_client_version()!r} disagrees with core "
        f"version={_pyproject_version()!r} — it is sent in the ACP initialize handshake"
    )


def _readme_declared_version() -> str:
    text = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"PersonalClaw is at \*\*v(\d+\.\d+\.\d+)\*\*", text)
    assert m, "the pre-1.0 banner's 'PersonalClaw is at **vX.Y.Z**' sentence is missing"
    return m.group(1)


def test_readme_banner_version_tracks_core() -> None:
    """The README's pre-1.0 banner states a version; it must be the current one.

    Unenforced, it drifted three releases (it still said v0.1.0 at v0.1.3) — and it
    sits in the one paragraph warning users their data may break, where being wrong
    about which release they are reading about is worst.
    """
    assert _readme_declared_version() == _pyproject_version(), (
        f"README pre-1.0 banner says v{_readme_declared_version()} but core is "
        f"v{_pyproject_version()} — update the banner in the release-prep commit"
    )
