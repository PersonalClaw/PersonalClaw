"""A skills marketplace an app adds lives exactly as long as the app is installed and on.

A skills app adds its marketplace from its own code: the module the provider loader imports
registers it in the skills registry (``get_default_skills_registry().register(...)``), the way the
first-party marketplace app does. The registry kept no record of whose code added it, so nothing
took it back. After the app was disabled or uninstalled, Skills > Browse still listed the
marketplace, ``GET /api/skills/marketplaces`` still returned it, and a search scoped to it ran the
removed app's code, which started that app's search command as a child of the gateway, until the
gateway restarted.

Now the registry records how to take a marketplace back when an app's code adds one
(``personalclaw.app_code``), so every unload (a disable, each uninstall rung, the start of an
update) takes it back with the rest of the app's code. The probe below is a marketplace whose
every search and fetch first runs a command of its own, so "no process was started" is observed
rather than assumed.
"""

from __future__ import annotations

import json
import sys
import textwrap
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# Imported before any test patches `config_dir`: the probe imports the SDK, and a module first
# imported under a patch keeps the mock bound.
import personalclaw.sdk.skill  # noqa: F401
from personalclaw import app_code
from personalclaw.apps import manager
from personalclaw.apps.native_contract import load_bundle_module
from personalclaw.dashboard.handlers.apps import register_app_routes
from personalclaw.dashboard.handlers.skills import (
    api_skills_install,
    api_skills_marketplace_detail,
    api_skills_marketplaces,
    api_skills_search,
)
from personalclaw.providers import registry as registry_module
from personalclaw.skills import marketplace

APP = "catalogue-probe"
MARKET = "probe-catalogue"

_SETTINGS_PY = "VERSION = {version!r}\nCOMMAND = {command!r}\nMARKET = {market!r}\n"

# Written the way a marketplace app writes its provider: the marketplace is added when the module
# is imported, and the factory the manifest names returns nothing.
_PROVIDER_PY = '''
"""The probe's marketplace: every search and fetch runs its own command before it answers."""

import subprocess

from probe_settings import COMMAND, MARKET, VERSION

from personalclaw.sdk.skill import (
    SkillDetail,
    SkillEntry,
    SkillsMarketplace,
    get_default_skills_registry,
)

SKILL_MD = "---\\nname: probe-skill\\ndescription: A skill the probe offers.\\n---\\nUse it.\\n"


class ProbeCatalogue(SkillsMarketplace):
    @property
    def marketplace_type(self):
        return "probe"

    def search(self, query, limit=20):
        subprocess.run([*COMMAND, "search", query], check=True, timeout=60)
        entry = SkillEntry(id="probe-skill", name="probe-skill", description=VERSION, source=MARKET)
        return [entry]

    def fetch(self, skill_id):
        subprocess.run([*COMMAND, "fetch", skill_id], check=True, timeout=60)
        files = [{"path": "SKILL.md", "contents": SKILL_MD}]
        return SkillDetail(id=skill_id, name="probe-skill", files=files)


get_default_skills_registry().register(MARKET, ProbeCatalogue())


def create_provider(config=None):
    return None
'''

# The probe's command, outside the app's folder so that removing the app's files cannot be what
# stops it: every run is one line in `runs.txt`.
_COMMAND_PY = """
import sys

with open({runs!r}, "a", encoding="utf-8") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\\n")
"""


def _probe(home: Path, version: str) -> Path:
    """The probe's source directory at ``version``, for the Store to install."""
    command = home / "bin" / "probe_command.py"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_text(textwrap.dedent(_COMMAND_PY).format(runs=str(home / "runs.txt")))
    d = home / f"src-{version}" / APP
    d.mkdir(parents=True)
    (d / "probe_settings.py").write_text(
        _SETTINGS_PY.format(version=version, command=[sys.executable, str(command)], market=MARKET)
    )
    (d / "provider.py").write_text(textwrap.dedent(_PROVIDER_PY))
    manifest = {
        "name": APP,
        "version": f"{version.lstrip('v')}.0.0",
        "displayName": "Catalogue Probe",
        "description": "A skills marketplace whose every search runs a command of its own.",
        "provider": {
            "type": "skills",
            "implementation": "provider:create_provider",
            "capabilities": ["search", "install"],
        },
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


def _runs(home: Path) -> list[str]:
    """Every run of the probe's command so far, oldest first."""
    try:
        return (home / "runs.txt").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


@pytest.fixture
def home(tmp_path, monkeypatch) -> Iterator[Path]:
    """An isolated home, fresh app-code and provider ledgers, and a skills registry of its own
    that starts with the core catalogues a gateway starts with."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    for attr, empty in (("_roots", {}), ("_undo", {}), ("_parked", {}), ("_released", set())):
        monkeypatch.setattr(app_code, attr, empty)
    monkeypatch.setattr(app_code, "_loaded", ())
    isolated = marketplace.SkillsRegistry()
    isolated._marketplaces = dict(marketplace.get_default_skills_registry()._marketplaces)
    monkeypatch.setattr(marketplace, "_DEFAULT_REGISTRY", isolated)
    registry_module.reset_provider_registry()
    yield tmp_path
    registry = registry_module.get_provider_registry()
    for name in list(registry._extensions):
        registry.disable(name)
    registry_module.reset_provider_registry()
    for name, module in list(sys.modules.items()):  # the probe's own modules, in any version
        if str(getattr(module, "__file__", None) or "").startswith(str(tmp_path)):
            sys.modules.pop(name, None)


class _Gateway:
    """The routes a user's clicks send, on the Store and on Skills > Browse."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    async def call(self, method: str, path: str, body: Any = None) -> Any:
        resp = await self.client.request(method, path, json=body)
        assert resp.status < 300, f"{method} {path} → {resp.status}: {await resp.text()}"
        return await resp.json()

    async def answer(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        resp = await self.client.request(method, path, json=body)
        return resp.status, await resp.json()

    async def install(self, source: Path) -> None:
        review = await self.call("POST", "/api/apps/preview", {"source": str(source)})
        await self.call("POST", "/api/apps", {"source": str(source), "consent": review["consent"]})

    async def update(self, source: Path) -> None:
        review = await self.call("POST", "/api/apps/preview", {"source": str(source), "name": APP})
        await self.call(
            "POST", f"/api/apps/{APP}/update", {"source": str(source), "consent": review["consent"]}
        )

    async def marketplaces(self) -> list[str]:
        return [m["name"] for m in await self.call("GET", "/api/skills/marketplaces")]

    async def search(self, scope: str = "") -> tuple[int, Any]:
        path = "/api/skills/search?q=postmortem" + (f"&marketplace={scope}" if scope else "")
        return await self.answer("GET", path)


@asynccontextmanager
async def _gateway() -> AsyncIterator[_Gateway]:
    app = web.Application()
    register_app_routes(app)
    app.router.add_get("/api/skills/marketplaces", api_skills_marketplaces)
    app.router.add_get("/api/skills/search", api_skills_search)
    app.router.add_get("/api/skills/marketplace/detail", api_skills_marketplace_detail)
    app.router.add_post("/api/skills/install", api_skills_install)
    async with TestClient(TestServer(app)) as client:
        yield _Gateway(client)


async def _offered_and_searched(gw: _Gateway, home: Path, version: str = "v1") -> None:
    """The marketplace is listed, and a search and a detail of it each run the probe's command."""
    assert MARKET in await gw.marketplaces()
    status, body = await gw.search(MARKET)
    assert status == 200, body
    assert [(r["source"], r["description"]) for r in body["results"]] == [(MARKET, version)]
    detail = await gw.call(
        "GET", f"/api/skills/marketplace/detail?id=probe-skill&marketplace={MARKET}"
    )
    assert detail["name"] == "probe-skill"
    assert _runs(home)[-2:] == ["search postmortem", "fetch probe-skill"]


async def _not_offered_and_refused(gw: _Gateway, home: Path) -> None:
    """Gone from every surface, and each call that would reach its code refused, running nothing."""
    ran = _runs(home)
    assert MARKET not in await gw.marketplaces(), "the removed app's marketplace is still listed"

    status, body = await gw.search(MARKET)
    assert status == 404, f"a search of the removed app's marketplace answered {status}: {body}"
    assert body["error"]["code"] == "skills_marketplace_not_found"

    status, body = await gw.search()
    assert status == 200, body
    assert [r for r in body["results"] if r["source"] == MARKET] == []
    assert MARKET not in body["counts"]
    assert body["installable_sources"] == 0, "Browse would still offer a catalogue to install from"

    status, body = await gw.answer(
        "GET", f"/api/skills/marketplace/detail?id=probe-skill&marketplace={MARKET}"
    )
    assert status == 404, body
    status, body = await gw.answer(
        "POST", "/api/skills/install", {"id": "probe-skill", "marketplace": MARKET}
    )
    assert status == 404, body
    assert _runs(home) == ran, f"the removed app's command ran again: {_runs(home)[len(ran):]}"


# ── the marketplace follows the app's install state ───────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "removal",
    [
        ("POST", f"/api/apps/{APP}/disable"),
        ("DELETE", f"/api/apps/{APP}"),  # uninstall: switched off, files kept
        ("DELETE", f"/api/apps/{APP}?remove=1"),  # files removed, data kept
        ("DELETE", f"/api/apps/{APP}?force=1"),  # everything removed
    ],
    ids=["disable", "uninstall", "remove", "force-uninstall"],
)
async def test_a_removed_apps_marketplace_is_not_offered_and_a_search_of_it_is_refused(
    home, removal
):
    async with _gateway() as gw:
        await gw.install(_probe(home, "v1"))
        await _offered_and_searched(gw, home)
        assert _runs(home) == ["search postmortem", "fetch probe-skill"]

        await gw.call(*removal)
        await _not_offered_and_refused(gw, home)
        assert _runs(home) == ["search postmortem", "fetch probe-skill"]


@pytest.mark.asyncio
async def test_switching_the_app_on_again_brings_its_marketplace_back(home):
    async with _gateway() as gw:
        await gw.install(_probe(home, "v1"))
        await gw.call("POST", f"/api/apps/{APP}/disable")
        await _not_offered_and_refused(gw, home)

        await gw.call("POST", f"/api/apps/{APP}/enable")
        await _offered_and_searched(gw, home)
        assert _runs(home) == ["search postmortem", "fetch probe-skill"]

        await gw.call("POST", f"/api/apps/{APP}/disable")
        await _not_offered_and_refused(gw, home)


@pytest.mark.asyncio
async def test_after_an_update_only_the_new_versions_marketplace_answers(home):
    async with _gateway() as gw:
        await gw.install(_probe(home, "v1"))
        await _offered_and_searched(gw, home, "v1")

        await gw.update(_probe(home, "v2"))
        await _offered_and_searched(gw, home, "v2")

        await gw.call("DELETE", f"/api/apps/{APP}?force=1")
        await _not_offered_and_refused(gw, home)


# ── the rule underneath: whose marketplace is whose ────────────────────────────────────────────


def _load(home: Path) -> None:
    """Load the probe's module the way the provider loader does: its directory claimed first."""
    root = _probe(home, "v1")
    load_bundle_module(root, APP, "provider")


def test_a_marketplace_an_apps_module_adds_on_import_is_taken_back_when_it_is_released(home):
    registry = marketplace.get_default_skills_registry()
    _load(home)
    assert MARKET in registry.list()

    app_code.release(APP)
    assert MARKET not in registry.list()
    with pytest.raises(KeyError):
        registry.get(MARKET)


def test_releasing_an_app_leaves_the_core_catalogues_and_a_newer_one_of_the_same_name(home):
    registry = marketplace.get_default_skills_registry()
    core = {name: registry.get(name) for name in ("native", "installed")}
    _load(home)
    newer = registry.get(MARKET).__class__()  # registered by core, after the app's
    registry.register(MARKET, newer)

    app_code.release(APP)
    assert registry.get(MARKET) is newer, "taking the app's back removed one it did not add"
    assert {name: registry.get(name) for name in core} == core
