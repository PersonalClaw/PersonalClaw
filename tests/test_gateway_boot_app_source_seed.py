"""First-run seeding of the curated app registry, railed on the REAL boot path.

ET-4 ships ``https://github.com/PersonalClaw/registry.git`` as a *seeded* default git
source: the gateway writes it into ``apps/app-sources.json`` once, as an ordinary
REMOVABLE row, alongside a ``"seeded": ["registry"]`` marker whose job is to keep a
user's removal from being undone by the next start.

``tests/test_app_catalog.py`` rails the SEEDER — every assertion there calls
``catalog.seed_default_git_sources()`` itself. That leaves the WIRE unrailed: deleting
``app.on_startup.append(_app_sources_seed_startup)`` from ``dashboard/server.py`` keeps
that entire suite green while first-run seeding silently never happens (measured — see
the module docstring of that file for the seeder's own rails, and this file's history
for the falsification). A seeder nobody calls is an inert control, and the done-clause
is "seeds into app-sources.json on first run", not "a helper exists". So these rails
boot the real gateway and assert what the user's Store actually reads — over HTTP, from
the file — never by calling the seeder.

Isolation: ``PERSONALCLAW_HOME`` is the seam, because ``config_dir()`` reads the env var
on every call and so redirects BOTH bindings at once — ``config/loader.py``'s and the
one ``config/__init__.py`` binds at import. Patching the loader attribute instead misses
the import-bound copy. The redirect is asserted through both bindings before any boot
runs, or these rails would write the real ``~/.personalclaw``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REGISTRY_URL = "https://github.com/PersonalClaw/registry.git"


@pytest.fixture
def boot_home(tmp_path, monkeypatch):
    """An isolated ``PERSONALCLAW_HOME`` with NO sources file — a genuinely fresh home.

    The absence of ``apps/app-sources.json`` is the premise: "first run" is defined by
    the missing seed marker, not by the missing row, and these rails have to start from
    the state a new install is actually in.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")

    import personalclaw.config as config_pkg
    import personalclaw.config.loader as config_loader

    assert config_loader.config_dir().resolve() == tmp_path.resolve()
    assert config_pkg.config_dir().resolve() == tmp_path.resolve()
    assert not (tmp_path / "apps" / "app-sources.json").exists()
    return tmp_path


def _sources_file(home: Path) -> dict[str, list[str]]:
    return json.loads((home / "apps" / "app-sources.json").read_text(encoding="utf-8"))


async def _boot():
    """Boot the real gateway on an ephemeral port; return ``(runner, port)``."""
    from personalclaw.dashboard.server import start_dashboard

    runner, _state = await start_dashboard(sessions=MagicMock(count=0), port=0)
    return runner, runner.addresses[0][1]


async def _get_sources(port: int) -> list[str]:
    """The Store's own view of the configured git sources, over HTTP."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/api/apps/sources") as resp:
            assert resp.status == 200
            return list((await resp.json())["sources"])


async def _delete_source(port: int, url: str) -> list[str]:
    """Remove a source the way the Store's remove control does — the real DELETE."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.delete(
            f"http://127.0.0.1:{port}/api/apps/sources", params={"url": url}
        ) as resp:
            assert resp.status == 200
            return list((await resp.json())["sources"])


@pytest.mark.asyncio
async def test_first_run_boot_seeds_the_registry_as_a_removable_default(boot_home):
    """Booting a fresh home writes the registry row — and the Store can remove it."""
    runner, port = await _boot()
    try:
        # The wire fired: the file the Store reads now holds the row, plus the marker
        # that will keep a removal removed.
        raw = _sources_file(boot_home)
        assert raw["git"] == [REGISTRY_URL]
        assert raw["seeded"] == ["registry"]

        # …and the user sees it listed.
        assert REGISTRY_URL in await _get_sources(port)

        from personalclaw.apps import catalog

        # "Default" (labelled as shipped, not typed by the user) but NOT builtin — the
        # bundled tuple is folded into every read of list_git_sources(), so a builtin
        # cannot be removed. The registry has to be in the first set and not the second
        # or the Store would either mislabel it or hide the remove control.
        assert REGISTRY_URL in catalog.default_git_sources()
        assert REGISTRY_URL not in catalog.builtin_git_sources()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_a_removal_made_in_the_store_survives_the_next_boot(boot_home):
    """The failure this atom exists to prevent: a default that silently comes back."""
    runner, port = await _boot()
    try:
        assert REGISTRY_URL in await _get_sources(port)
        assert REGISTRY_URL not in await _delete_source(port, REGISTRY_URL)
    finally:
        await runner.cleanup()

    # The row is gone; the marker is what outlives it.
    raw = _sources_file(boot_home)
    assert raw["git"] == []
    assert raw["seeded"] == ["registry"]

    # A genuinely new boot over the same home — the moment a re-seed would happen.
    runner, port = await _boot()
    try:
        assert REGISTRY_URL not in await _get_sources(port)
        assert _sources_file(boot_home)["git"] == []
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_the_flag_off_boot_acquires_no_network_source(boot_home, monkeypatch):
    """Positive control for the two rails above: the boot leg can produce the OTHER
    outcome, so their green is an observation of the boot and not of a constant.

    Also the operator-facing promise — ``apps.registry_source_enabled`` off means a
    fresh home never acquires a shipped NETWORK source at all.
    """
    (boot_home / "config.json").write_text(
        json.dumps({"apps": {"registry_source_enabled": False}}), encoding="utf-8"
    )

    runner, port = await _boot()
    try:
        assert REGISTRY_URL not in await _get_sources(port)
        assert not (boot_home / "apps" / "app-sources.json").exists()
    finally:
        await runner.cleanup()
