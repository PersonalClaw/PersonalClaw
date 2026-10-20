"""Install consent says when a program an app starts reads the settings of the folder it works in.

An agent CLI reads its configuration from two kinds of place: the owner's own (its sign-in, its
settings folder and the auto-approve rules there) and the folder it is pointed at. A
repository's own settings for the CLI can add rules that let it act without asking and name
commands it runs, and those come from whoever wrote the repository, not from the owner. So
``launches[].inherits`` has a word for them, ``folder-settings``, the install review and an
update's review carry it like the other three, and an update that starts reading a folder's
settings asks again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, catalog, manager
from personalclaw.apps.manifest import LAUNCH_INHERITS, AppManifest
from personalclaw.providers import loader

OWN = ["sign-in", "settings", "auto-approve-rules"]
PROGRAM = {
    "program": "example-cli",
    "why": "It does the work of each chat; the app talks to it over ACP.",
    "inherits": OWN,
}


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
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    catalog._git_scan_cache.clear()
    catalog._registry_cache.clear()
    yield tmp_path
    catalog._git_scan_cache.clear()
    catalog._registry_cache.clear()


def _manifest(**extra) -> dict:
    return {
        "name": "example-agent",
        "version": "1.0.0",
        "displayName": "Example Agent",
        "description": "runs an agent CLI",
        **extra,
    }


def _bundle(root: Path, manifest: dict) -> Path:
    d = root / manifest["name"]
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def test_a_folders_own_settings_are_a_word_consent_has():
    assert "folder-settings" in LAUNCH_INHERITS
    raw = _manifest(launches=[{**PROGRAM, "inherits": [*OWN, "folder-settings"]}])
    m = AppManifest.from_dict(raw)
    assert m.validate() == []
    assert m.to_dict()["launches"] == raw["launches"]


def test_the_review_carries_it(tmp_path):
    res = app_manager.preview(
        _bundle(tmp_path / "a", _manifest(launches=[{**PROGRAM, "inherits": ["folder-settings"]}]))
    )
    assert res.disclosure["launches"][0]["inherits"] == ["folder-settings"]


def test_an_update_that_starts_reading_a_folders_settings_asks_first(tmp_path):
    v1 = _bundle(tmp_path / "v1", _manifest(launches=[PROGRAM]))
    assert app_manager.install(v1, confirm=True).ok
    v2 = _bundle(
        tmp_path / "v2",
        _manifest(version="1.1.0", launches=[{**PROGRAM, "inherits": [*OWN, "folder-settings"]}]),
    )

    res = app_manager.update(v2)

    assert res.needs_consent and not res.ok, res.error
    assert res.previous["launches"][0]["inherits"] == OWN
    assert res.disclosure["launches"][0]["inherits"] == [*OWN, "folder-settings"]
