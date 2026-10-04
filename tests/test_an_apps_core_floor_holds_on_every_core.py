"""An app's core floor holds on every core: a release candidate, a dev build and a post release too.

On a release candidate the gate let every app in. It read the running core with a rule of its
own (``MAJOR.MINOR.PATCH`` followed by ``-``, ``+`` or nothing), and ``0.3.0rc1``, which is how a
candidate reports itself once installed, fits none of them. So the check was skipped and the app
admitted: an app that needs 9.9.9 installed on 0.3.0rc1, and every install on the beta channel
runs a candidate. A floor the rule could not read was skipped the same way.

The gate now reads versions with the one comparison every version check uses
(``personalclaw.versions``, the packaging standard): a candidate and a dev build are older than
their release, a post release is newer, and a version that cannot be read refuses the app,
naming both versions, instead of letting it in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import personalclaw
from personalclaw.apps import app_manager, catalog, manager
from personalclaw.apps.core_version import (
    CORE_COMPAT_INCOMPATIBLE,
    CORE_COMPAT_INVALID,
    CORE_COMPAT_OK,
    CORE_COMPAT_UNKNOWN_HOST,
    check_core_compatibility,
)
from personalclaw.providers import loader

CANDIDATE = "0.3.0rc1"  # what a release candidate reports once installed (its tag: v0.3.0-rc.1)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg

    for module in (cfg, manager, catalog):
        monkeypatch.setattr(module, "config_dir", lambda: tmp_path)
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", native)
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "no-first-party"))
    monkeypatch.setattr(personalclaw, "__version__", CANDIDATE)
    return tmp_path


def _source(tmp_path: Path, name: str, floor: str, *, subdir: str = "src") -> Path:
    src = tmp_path / subdir / name
    src.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": name.replace("-", " ").title(),
        "description": "A fixture app with a core floor.",
        "minPersonalClawVersion": floor,
    }
    (src / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return src


# ── the verdict ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host, floor, admits",
    [
        (CANDIDATE, "9.9.9", False),  # the case that was waved through
        (CANDIDATE, "0.3.0", False),  # a candidate is older than its release
        (CANDIDATE, "0.2.0", True),
        (CANDIDATE, CANDIDATE, True),
        (CANDIDATE, "0.3.0-rc.1", True),  # the tag's spelling of the same version
        ("0.3.0rc2", "0.3.0-rc.1", True),
        ("0.3.0b1", "0.3.0rc1", False),
        ("0.3.0.dev4", "0.3.0", False),  # a dev build is older than its release
        ("0.3.0.dev4", "0.3.0rc1", False),  # ...and than its candidates
        ("0.3.0.dev4", "0.2.9", True),
        ("0.2.2.dev47+g1a2b", "0.2.2", False),  # a source checkout's dev version
        ("0.2.2.dev47+g1a2b", "0.2.1", True),
        ("0.3.0.post1", "0.3.0", True),  # a post release is newer than its release
        ("0.3.0.post1", "0.3.1", False),
        ("0.3.0", "0.3.0rc1", True),
    ],
)
def test_the_floor_holds_on_candidates_dev_builds_and_post_releases(host, floor, admits):
    v = check_core_compatibility(floor, host=host)
    assert v.admits is admits, v.reason
    if admits:
        assert (v.state, v.reason) == (CORE_COMPAT_OK, "")
    else:
        assert v.state == CORE_COMPAT_INCOMPATIBLE
        assert v.reason == (
            f"requires PersonalClaw {floor} or newer, but this core is {host}. Upgrade the core "
            "— run `personalclaw update` — then try again."
        )


@pytest.mark.parametrize("floor", ["latest", "~1.2.0", ">=1.2.0", "1.2.x", "one.two.three"])
def test_a_floor_that_is_not_a_version_refuses_naming_both(floor):
    v = check_core_compatibility(floor, host="1.4.2")
    assert (v.state, v.admits) == (CORE_COMPAT_INVALID, False)
    assert v.reason == (
        f"declares minPersonalClawVersion {floor!r}, which is not a version, so this core "
        "(1.4.2) cannot tell whether it is new enough. The app's app.json must name a version "
        "there, such as 0.3.0."
    )


@pytest.mark.parametrize("host", ["unknown", "main", ""])
def test_a_core_whose_version_cannot_be_read_refuses_naming_both(host):
    v = check_core_compatibility("0.1.0", host=host)
    assert (v.state, v.admits) == (CORE_COMPAT_UNKNOWN_HOST, False)
    assert v.reason == (
        f"requires PersonalClaw 0.1.0 or newer, but this core reports its version as {host!r}, "
        "which is not a version, so it cannot tell whether it is new enough. Reinstall "
        "PersonalClaw, then try again."
    )


@pytest.mark.parametrize(
    "floor, state",
    [
        ("1", CORE_COMPAT_OK),
        ("1.2", CORE_COMPAT_OK),
        ("v1.2.0", CORE_COMPAT_OK),
        ("1.4.2-rc.1", CORE_COMPAT_OK),
        ("1.4.2.post1", CORE_COMPAT_INCOMPATIBLE),
        ("1.5", CORE_COMPAT_INCOMPATIBLE),
    ],
)
def test_any_spelling_the_comparison_reads_is_a_floor(floor, state):
    """A floor is read as the packaging standard reads it: ``1.2`` is ``1.2.0``."""
    assert check_core_compatibility(floor, host="1.4.2").state == state


#: Versions as they reach a check: release tags, the installed spellings of the same releases,
#: dev builds, a post release, and one of each kind of version a line ships.
_VERSIONS = (
    "0.2.0",
    "v0.2.0",
    "0.2.1.dev3",
    "0.2.2.dev47+g1a2b",
    "0.3.0.dev1",
    "0.3.0a1",
    "0.3.0b2",
    "v0.3.0-rc.1",
    "0.3.0rc1",
    "0.3.0-rc.2",
    "0.3.0",
    "0.3.0.post1",
    "1.0.0",
)


def test_the_gate_and_the_updater_agree_on_every_version():
    """The floor admits exactly the cores the updater would not call older than the floor."""
    from personalclaw.versions import is_newer

    disagree = [
        (host, floor)
        for host in _VERSIONS
        for floor in _VERSIONS
        if check_core_compatibility(floor, host=host).admits is is_newer(floor, host)
    ]
    assert disagree == []


# ── the doors ───────────────────────────────────────────────────────────────────────────────


def test_a_candidate_core_refuses_to_review_or_install_an_app_that_needs_its_release(tmp_path):
    src = _source(tmp_path, "needs-the-release", "0.3.0")
    review = app_manager.preview(src)
    assert review.ok is False and not review.needs_consent
    assert review.error == (
        "install refused: 'needs-the-release' requires PersonalClaw 0.3.0 or newer, but this "
        f"core is {CANDIDATE}. Upgrade the core — run `personalclaw update` — then try again."
    )
    res = app_manager.install(src, confirm=True)
    assert res.ok is False and CANDIDATE in res.error and "0.3.0 or newer" in res.error
    assert manager._read_installed("needs-the-release") is None


def test_a_candidate_core_installs_an_app_whose_floor_it_meets(tmp_path):
    res = app_manager.install(_source(tmp_path, "needs-an-older-core", "0.2.0"), confirm=True)
    assert res.ok is True, res.error
    assert manager._read_installed("needs-an-older-core") is not None


def test_a_floor_that_is_not_a_version_is_refused_at_install(tmp_path):
    res = app_manager.install(_source(tmp_path, "floor-typo", "latest"), confirm=True)
    assert res.ok is False
    assert "'latest', which is not a version" in res.error and CANDIDATE in res.error
    assert manager._read_installed("floor-typo") is None


def test_a_candidate_cores_store_card_carries_the_refusal(tmp_path):
    root = tmp_path / "store"
    _source(tmp_path, "needs-the-release", "0.3.0", subdir="store")
    _source(tmp_path, "needs-an-older-core", "0.2.0", subdir="store")
    catalog.add_local_source(str(root))
    cards = {e.name: e.coreCompatibility for e in catalog._scan_local_sources()}
    assert cards["needs-the-release"]["state"] == CORE_COMPAT_INCOMPATIBLE
    assert cards["needs-the-release"]["host"] == CANDIDATE
    assert cards["needs-an-older-core"]["state"] == CORE_COMPAT_OK
