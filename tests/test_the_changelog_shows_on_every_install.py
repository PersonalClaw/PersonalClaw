"""Settings › Updates shows the changelog of the release an install runs, on every install.

Measured before this was written: ``GET /api/changelog`` read ``CHANGELOG.md`` only from the
folder ``PERSONALCLAW_PROJECT_DIR`` names. The CLI sets that only for a source checkout, nothing
sets it in the image, and the desktop app sets it to its bundle's resources, which hold no
changelog. No distribution carried the file either. So every install but a checkout answered
``{"content": ""}``, and its Changelog card said "No changelog available.", offline or not.

Now every distribution carries the file inside the package (``setup.py`` ``BuildWithWeb``,
``MANIFEST.in``, the image's builder stage, the desktop bundle's data), and the route reads a
source checkout's working copy, so its unreleased lines show, else the copy the package carries
(``updates._changelog_text``). ``tests/test_verify_wheel_contract.py`` builds the wheel and the
sdist and refuses either without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw
from personalclaw import self_update
from personalclaw.dashboard.handlers import updates

pytestmark = pytest.mark.asyncio

RELEASED = "# Changelog\n\n## [Unreleased]\n\n## [0.4.0] — 2026-12-01\n\n- **A fix.**\n"


async def _shown() -> str:
    """What Settings › Updates' Changelog card is given."""
    app = web.Application()
    app.router.add_get("/api/changelog", updates.api_changelog)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/changelog")
        assert resp.status == 200, await resp.text()
        return (await resp.json())["content"]


@pytest.fixture
def installed(tmp_path, monkeypatch) -> Path:
    """An install from no checkout (a wheel, the image, the desktop bundle), whose package
    carries the changelog of the release it was built from."""
    package = tmp_path / "site-packages" / "personalclaw"
    package.mkdir(parents=True)
    (package / "CHANGELOG.md").write_text(RELEASED, encoding="utf-8")
    monkeypatch.setattr(self_update, "source_checkout", lambda: "")
    # raising=False: an install whose route reads no packaged copy is what the red run measures.
    monkeypatch.setattr(
        updates, "_packaged_changelog", lambda: package / "CHANGELOG.md", raising=False
    )
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)
    return package


async def test_an_install_from_no_checkout_shows_the_changelog_its_package_carries(installed):
    """🔴 Red before: with no checkout and no ``PERSONALCLAW_PROJECT_DIR`` (a wheel, the image),
    the route answered ``""``."""
    assert await _shown() == RELEASED


async def test_the_desktop_app_shows_the_changelog_its_bundle_carries(
    installed, tmp_path, monkeypatch
):
    """🔴 Red before: the desktop app names its bundle's resources as the project folder, which
    holds no changelog, and the route answered ``""``."""
    resources = tmp_path / "Resources"
    (resources / "backend-dist").mkdir(parents=True)
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(resources))

    assert await _shown() == RELEASED


async def test_a_checkout_shows_its_own_working_copy(installed, tmp_path, monkeypatch):
    """A source checkout keeps showing the file as it is written, unreleased lines and all, not a
    packaged copy, as the CLI runs it (it names the checkout as the project folder)."""
    checkout = tmp_path / "PersonalClaw"
    checkout.mkdir()
    working = RELEASED.replace("## [Unreleased]\n", "## [Unreleased]\n\n- **Not yet released.**\n")
    (checkout / "CHANGELOG.md").write_text(working, encoding="utf-8")
    monkeypatch.setattr(self_update, "source_checkout", lambda: str(checkout))
    monkeypatch.setenv("PERSONALCLAW_PROJECT_DIR", str(checkout))

    assert await _shown() == working


async def test_an_install_with_no_changelog_shows_none(installed):
    """A package that lost its copy answers what the card shows as its empty state."""
    (installed / "CHANGELOG.md").unlink()

    assert await _shown() == ""


async def test_a_package_its_loader_cannot_read_from_shows_none(installed, monkeypatch):
    """A loader with no resource reader (a frozen bundle's, say) raises from the lookup itself:
    the card shows its empty state, as for any copy that can't be read, never an error."""

    def _no_reader():
        raise TypeError("the loader has no resource reader")

    monkeypatch.setattr(updates, "_packaged_changelog", _no_reader)

    assert await _shown() == ""


async def test_the_packaged_copy_is_the_one_inside_the_package():
    """The copy the route falls back to is the package's own ``CHANGELOG.md``, the member the
    wheel carries (``personalclaw/CHANGELOG.md``) and the desktop bundle puts beside the package's
    modules."""
    packaged = Path(str(updates._packaged_changelog())).resolve()
    assert packaged == Path(personalclaw.__file__).resolve().parent / "CHANGELOG.md"
