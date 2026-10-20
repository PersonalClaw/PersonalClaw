"""Install consent and the Store card say what an app needs that PersonalClaw doesn't install.

Local Image Generation needs a ComfyUI server PersonalClaw cannot install. On main a manifest
had nowhere to say so: the consent dialog and the Store card could only describe what the app
gets, and the first the owner heard of ComfyUI was the error after installing it. The manifest
now declares ``requires: [{name, why, how}]``, and the one disclosure projection carries it
(and the engine packages Install engine will put in a sidecar app's own environment) to the
install review, the Store card and an update's review.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, catalog, manager
from personalclaw.apps.disclosure import describe
from personalclaw.apps.manifest import AppManifest
from personalclaw.providers import loader

COMFYUI = {
    "name": "ComfyUI",
    "why": "It generates the images; this app sends it the prompt.",
    "how": "Install ComfyUI, start it on this machine, and put its address in Configure.",
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
        "name": "image-maker",
        "version": "1.0.0",
        "displayName": "Image Maker",
        "description": "makes images",
        **extra,
    }


def _bundle(root: Path, manifest: dict) -> Path:
    d = root / manifest["name"]
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def test_a_prerequisite_round_trips_through_the_manifest():
    raw = _manifest(requires=[COMFYUI])
    m = AppManifest.from_dict(raw)

    assert m.validate() == []
    assert m.to_dict()["requires"] == [COMFYUI]
    assert AppManifest.from_dict(m.to_dict()).to_dict() == m.to_dict()
    assert "requires" not in AppManifest.from_dict(_manifest()).to_dict()
    assert "requires" not in m.extra


@pytest.mark.parametrize(
    ("requires", "says"),
    [
        ([{"name": "ComfyUI", "how": "install it"}], "missing 'why'"),
        ([{"name": "ComfyUI", "why": "it draws"}], "missing 'how'"),
        (["ComfyUI"], "missing 'why'"),
        ("ComfyUI", "missing 'how'"),
        ([COMFYUI, {**COMFYUI, "name": "comfyui"}], "more than once"),
        ([{**COMFYUI, "why": "x" * 301}], "at most 300"),
        ([{**COMFYUI, "name": f"thing {i}"} for i in range(11)], "at most 10"),
    ],
)
def test_a_prerequisite_the_consent_cannot_show_is_an_install_error(requires, says):
    errors = AppManifest.from_dict(_manifest(requires=requires)).validate()
    assert any(says in e for e in errors), errors


def test_the_install_review_names_the_prerequisite_and_the_engine(tmp_path):
    """The review the consent dialog shows is the server's reading of the staged bytes."""
    src = _bundle(
        tmp_path / "src",
        _manifest(
            requires=[COMFYUI],
            provider={
                "type": "model",
                "implementation": "provider:create_provider",
                "execution": "sidecar",
            },
            dependencies={"sidecarDependencies": ["omnivoice>=0.2", "vocos"]},
        ),
    )

    review = app_manager.preview(src)

    assert review.needs_consent, review.error
    assert review.disclosure["requires"] == [COMFYUI]
    assert review.disclosure["sidecarDependencies"] == ["omnivoice>=0.2", "vocos"]
    assert "the 2 engine packages it installs when you choose Install engine" in (
        review.disclosure["runsAsYou"]
    )
    assert review.disclosure["pythonDependencies"] == [], "the engine is not a gateway package"


def test_the_store_card_says_what_the_app_needs(tmp_path):
    root = tmp_path / "local-src"
    _bundle(root, _manifest(requires=[COMFYUI]))
    catalog.add_local_source(str(root))

    [card] = [c for c in catalog._scan_local_sources() if c.name == "image-maker"]

    assert card.requires == [COMFYUI]
    assert card.consentKnown
    assert describe(AppManifest.from_dict(_manifest(requires=[COMFYUI])))["requires"] == [COMFYUI]


def test_an_update_that_adds_a_prerequisite_asks_first(tmp_path):
    """A new "needs ComfyUI" is something to agree to, like a new grant: on main it went in
    ``extra``, the review said nothing changed, and the update committed on a bare request."""
    v1 = _bundle(tmp_path / "v1", _manifest())
    assert app_manager.install(v1, confirm=True).ok
    v2 = _bundle(tmp_path / "v2", _manifest(version="1.1.0", requires=[COMFYUI]))

    res = app_manager.update(v2)

    assert res.needs_consent and not res.ok, res.error
    assert res.disclosure["requires"] == [COMFYUI]
    assert res.previous["requires"] == []
    assert manager._read_installed("image-maker").version == "1.0.0"
