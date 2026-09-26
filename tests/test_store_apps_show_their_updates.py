"""An app installed from the Store shows its update, and its Update starts from where it came from.

🔴 THE DEFECT (measured on ``origin/main``). ``catalog.updates_available`` read only the
configured LOCAL sources' manifests, matched by app name. A Store card installs from a pointer —
``url#app`` from a multi-app repository (the first-party PersonalClawApps shape), a registry
listing's repo, or a single-app repository's URL — and ``installed.json`` records that pointer as
the app's ``source``. Nothing read what that pointer offers now, so an app installed from the Store
never showed an update, and its Update dialog opened with an empty source.

What the pointer offers now is read from the Store's OWN discovery caches (the registry indexes,
the multi-app scans, the single-app versions those scans read), which the catalog read refreshes
under its budget and failure backoff. The ``/api/apps`` read path stays network-free.

Every repository here is a real bare git repository driven over ``file://``: the identical git
code path the Store uses, with no network.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.apps import catalog, manager
from personalclaw.providers import loader


def _caches() -> tuple[dict, ...]:
    """The Store's process-global discovery caches, which every test starts and ends without."""
    return (
        catalog._git_scan_cache,
        catalog._registry_cache,
        catalog._registry_failures,
        catalog._git_root_versions,
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    from personalclaw import inbox as _inbox
    from personalclaw.providers import entity_routes as _er

    for module in (cfg, manager, catalog, _er, _inbox):
        monkeypatch.setattr(module, "config_dir", lambda: tmp_path)
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", native)
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "no-first-party"))
    # No shipped network source, and the process-global discovery caches start empty.
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    for cache in _caches():
        cache.clear()
    yield tmp_path
    for cache in _caches():
        cache.clear()


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "user.name=Fixture",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _manifest(name: str, version: str) -> str:
    return json.dumps(
        {"name": name, "version": version, "displayName": name.title(), "description": name}
    )


class Repo:
    """A bare git repository behind ``file://``, with a work tree to publish from."""

    def __init__(self, root: Path) -> None:
        self.work = root / "work"
        self.work.mkdir(parents=True)
        _git("init", "--initial-branch=main", ".", cwd=self.work)
        (self.work / "README.md").write_text("fixture\n", encoding="utf-8")
        _git("add", "-A", cwd=self.work)
        _git("commit", "-m", "init", cwd=self.work)
        bare = root / "apps.git"
        _git("clone", "--bare", str(self.work), str(bare), cwd=root)
        _git("remote", "add", "origin", str(bare), cwd=self.work)
        self.url = f"file://{bare}"

    def write(self, rel: str, text: str) -> None:
        path = self.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def publish(self, message: str = "publish") -> None:
        _git("add", "-A", cwd=self.work)
        _git("commit", "-m", message, cwd=self.work)
        _git("push", "origin", "HEAD:main", cwd=self.work)


def _installed(root: Path, name: str, version: str, *, source: str, origin: str) -> None:
    """An installed app as a Store install records it: its version and the pointer it came from."""
    d = root / "apps" / name
    d.mkdir(parents=True)
    (d / "installed.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "enabled": True,
                "origin": origin,
                "source": source,
            }
        ),
        encoding="utf-8",
    )
    (d / "app.json").write_text(_manifest(name, version), encoding="utf-8")


def _updates() -> dict[str, dict]:
    return {u["name"]: u for u in catalog.updates_available()}


def test_an_app_installed_from_a_multi_app_repository_shows_its_update(tmp_path) -> None:
    repo = Repo(tmp_path / "first-party")
    repo.write("alpha-app/app.json", _manifest("alpha-app", "0.1.0"))
    repo.write("beta-app/app.json", _manifest("beta-app", "1.0.0"))
    repo.publish()
    catalog.add_git_source(repo.url)
    pointer = f"{repo.url}#alpha-app"
    _installed(tmp_path, "alpha-app", "0.1.0", source=pointer, origin="external")
    _installed(tmp_path, "beta-app", "1.0.0", source=f"{repo.url}#beta-app", origin="external")

    repo.write("alpha-app/app.json", _manifest("alpha-app", "0.2.0"))
    repo.write("beta-app/app.json", _manifest("beta-app", "1.1.0"))
    repo.publish("new versions")

    catalog.available_catalog()  # the Store's read: what the Apps page asks for when it opens
    updates = _updates()
    assert set(updates) == {"alpha-app", "beta-app"}, updates
    assert updates["alpha-app"]["latestVersion"] == "0.2.0"
    assert (
        updates["alpha-app"]["latestSource"] == pointer
    ), "the Update must start where it came from"
    assert updates["beta-app"]["latestVersion"] == "1.1.0"


def test_one_repository_spelled_with_and_without_git_is_one_source(tmp_path) -> None:
    # GitHub serves a repository at both spellings, so an app installed from one is offered
    # its update by a Store source configured with the other. (What a scan of it found is
    # seeded: this repository is not fetched.)
    catalog.add_git_source("https://github.com/example/solo-app.git")
    catalog._git_root_versions["https://github.com/example/solo-app.git"] = "2.0.0"
    _installed(
        tmp_path,
        "solo-app",
        "1.0.0",
        source="https://github.com/example/solo-app/",
        origin="external",
    )
    assert _updates().get("solo-app", {}).get("latestVersion") == "2.0.0"


def test_an_app_a_registry_listed_shows_its_update(tmp_path) -> None:
    registry = Repo(tmp_path / "registry")
    registry.write("gamma-app/app.json", _manifest("gamma-app", "1.4.0"))
    registry.write(
        "app-registry.json",
        json.dumps(
            {"apps": [{"name": "gamma-app", "subdirectory": "gamma-app", "version": "1.4.0"}]}
        ),
    )
    registry.publish()
    catalog.add_git_source(registry.url)
    pointer = f"{registry.url}#gamma-app"
    _installed(tmp_path, "gamma-app", "1.2.0", source=pointer, origin="external")

    catalog.available_catalog()
    updates = _updates()
    assert set(updates) == {"gamma-app"}, updates
    assert updates["gamma-app"]["latestVersion"] == "1.4.0"
    assert updates["gamma-app"]["latestSource"] == pointer


def test_an_app_installed_from_a_single_app_repository_shows_its_update(tmp_path) -> None:
    repo = Repo(tmp_path / "solo")
    repo.write("app.json", _manifest("solo-app", "0.1.0"))
    repo.publish()
    catalog.add_git_source(repo.url)
    _installed(tmp_path, "solo-app", "0.1.0", source=repo.url, origin="external")

    repo.write("app.json", _manifest("solo-app", "0.3.0"))
    repo.publish("0.3.0")
    catalog.available_catalog()
    assert _updates().get("solo-app", {}).get("latestVersion") == "0.3.0"


def test_the_apps_list_never_touches_the_network_and_is_as_fresh_as_the_last_store_read(
    tmp_path, monkeypatch
) -> None:
    repo = Repo(tmp_path / "first-party")
    repo.write("alpha-app/app.json", _manifest("alpha-app", "0.2.0"))
    repo.publish()
    catalog.add_git_source(repo.url)
    _installed(tmp_path, "alpha-app", "0.1.0", source=f"{repo.url}#alpha-app", origin="external")

    catalog.available_catalog()
    repo.write("alpha-app/app.json", _manifest("alpha-app", "0.3.0"))
    repo.publish("0.3.0")

    def no_network(*_a, **_k):
        raise AssertionError("the apps list ran a subprocess")

    monkeypatch.setattr(subprocess, "run", no_network)
    # The read path answers from what the Store's last read found: 0.2.0, not 0.3.0.
    assert _updates().get("alpha-app", {}).get("latestVersion") == "0.2.0"


def test_an_app_whose_store_source_offers_nothing_newer_is_absent(tmp_path) -> None:
    repo = Repo(tmp_path / "first-party")
    repo.write("alpha-app/app.json", _manifest("alpha-app", "0.2.0"))
    repo.publish()
    catalog.add_git_source(repo.url)
    _installed(tmp_path, "alpha-app", "0.2.0", source=f"{repo.url}#alpha-app", origin="external")
    # Another repository's app of the same name does not count: only where it came from does.
    other = Repo(tmp_path / "other")
    other.write("alpha-app/app.json", _manifest("alpha-app", "9.0.0"))
    other.publish()
    catalog.add_git_source(other.url)

    catalog.available_catalog()
    assert _updates() == {}


def test_a_source_the_owner_removed_is_no_longer_asked(tmp_path) -> None:
    repo = Repo(tmp_path / "first-party")
    repo.write("alpha-app/app.json", _manifest("alpha-app", "0.2.0"))
    repo.publish()
    catalog.add_git_source(repo.url)
    _installed(tmp_path, "alpha-app", "0.1.0", source=f"{repo.url}#alpha-app", origin="external")
    catalog.available_catalog()
    assert set(_updates()) == {"alpha-app"}

    catalog.remove_git_source(repo.url)
    assert _updates() == {}, "an update was still offered from a source the owner removed"


def test_a_single_app_repository_that_becomes_a_multi_app_one_stops_offering_its_old_version(
    tmp_path,
) -> None:
    repo = Repo(tmp_path / "solo")
    repo.write("app.json", _manifest("solo-app", "0.3.0"))
    repo.publish()
    catalog.add_git_source(repo.url)
    _installed(tmp_path, "solo-app", "0.1.0", source=repo.url, origin="external")
    catalog.available_catalog()
    assert set(_updates()) == {"solo-app"}

    _git("rm", "-q", "app.json", cwd=repo.work)
    repo.write("solo-app/app.json", _manifest("solo-app", "0.3.0"))
    repo.publish("split into a multi-app repository")
    catalog._git_scan_cache.clear()  # the scan's own 5-minute window, passed
    catalog.available_catalog()
    assert _updates() == {}, "the repository's root no longer holds an app"


def _list_rows() -> dict[str, dict]:
    from personalclaw.dashboard.handlers import apps as apps_handlers

    req = make_mocked_request("GET", "/api/apps", app=web.Application())
    resp = asyncio.run(apps_handlers.api_apps_list(req))
    return {row["name"]: row for row in json.loads(resp.text)["apps"]}


def test_the_update_dialog_starts_from_where_a_store_app_came_from(tmp_path) -> None:
    repo = Repo(tmp_path / "first-party")
    repo.write("alpha-app/app.json", _manifest("alpha-app", "0.2.0"))
    repo.write("beta-app/app.json", _manifest("beta-app", "1.0.0"))
    repo.publish()
    catalog.add_git_source(repo.url)
    local = tmp_path / "my-apps" / "delta-app"
    local.mkdir(parents=True)
    _installed(tmp_path, "alpha-app", "0.1.0", source=f"{repo.url}#alpha-app", origin="external")
    _installed(tmp_path, "beta-app", "1.0.0", source=f"{repo.url}#beta-app", origin="external")
    _installed(tmp_path, "delta-app", "1.0.0", source=str(local), origin="local")
    _installed(tmp_path, "gone-app", "1.0.0", source=str(tmp_path / "deleted"), origin="local")
    _installed(tmp_path, "shipped-app", "1.0.0", source="builtin", origin="builtin")

    catalog.available_catalog()
    rows = _list_rows()
    assert rows["alpha-app"]["updateAvailable"] is True
    assert rows["alpha-app"]["latestVersion"] == "0.2.0"
    assert rows["alpha-app"]["latestSource"] == f"{repo.url}#alpha-app"
    # No newer version, and the Update still starts from where it came from.
    assert rows["beta-app"]["updateAvailable"] is False
    assert rows["beta-app"]["updateSource"] == f"{repo.url}#beta-app"
    assert rows["delta-app"]["updateSource"] == str(local)
    assert rows["gone-app"]["updateSource"] == "", "a folder that is gone is nowhere to update from"
    assert rows["shipped-app"]["updateSource"] == "", "a shipped app updates with PersonalClaw"
