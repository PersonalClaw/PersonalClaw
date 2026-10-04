"""An app built for a newer core is refused by an older one, saying what it needs.

Every core built between two releases reads the same version, so a channel app's update that
relied on a contract added a few commits earlier (the answers an approval prompt offers) passed
``minPersonalClawVersion`` and installed on a core without that contract. Every approval on the
channel then arrived as a notice with nothing to press, a workflow step timed out waiting on one,
the update dialog said the update "does not change anything it gets", and no log said anything.

An app now names the core features it relies on (``requiresCoreFeatures``), and the one
compatibility check refuses an app that needs one this core does not offer, at every door: the
review a consent dialog shows, install, update, switching it on, and the Store card's verdict. So
does an app whose ``minPersonalClawVersion`` is above this core, which the review now refuses too.
Both directions are asserted: an app needing what this core offers goes through every door.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw
from personalclaw.apps import app_manager, backend_runtime, catalog, core_features, manager
from personalclaw.apps.core_version import (
    CORE_COMPAT_INCOMPATIBLE,
    CORE_COMPAT_OK,
    CORE_COMPAT_UNKNOWN_HOST,
    check_core_compatibility,
)
from personalclaw.apps.manifest import AppManifest
from personalclaw.sdk.features import APPROVAL_ANSWERS

HOST = "1.4.2"  # the pretend running core for every path test below
#: A well-formed name this core has never heard of: a feature of a newer core.
NEWER = "a-feature-of-a-newer-core"


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    """An isolated home and a pinned core version, so nothing depends on the core under test."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(personalclaw, "__version__", HOST)
    return tmp_path


def _source(
    tmp_path: Path,
    *,
    name: str = "needs-app",
    version: str = "1.0.0",
    features: list | None = None,
    floor: str | None = None,
    subdir: str = "src",
) -> Path:
    src = tmp_path / subdir / name
    src.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "name": name,
        "version": version,
        "displayName": "Needs App",
        "description": "A fixture app that relies on core features.",
    }
    if features is not None:
        manifest["requiresCoreFeatures"] = features
    if floor is not None:
        manifest["minPersonalClawVersion"] = floor
    (src / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return src


# ── the verdict ──────────────────────────────────────────────────────────────────────────────


class TestTheVerdict:
    def test_a_feature_this_core_does_not_offer_refuses_naming_it(self):
        v = check_core_compatibility("", [NEWER], host=HOST)
        assert v.state == CORE_COMPAT_INCOMPATIBLE
        assert v.admits is False
        assert v.missing == (NEWER,)
        assert v.reason == (
            f"needs a newer PersonalClaw: this core ({HOST}) does not have the core feature "
            f"'{NEWER}' it relies on. Upgrade the core — run `personalclaw update` — then try "
            "again."
        )

    def test_a_feature_this_core_offers_admits(self):
        v = check_core_compatibility("", [APPROVAL_ANSWERS], host=HOST)
        assert (v.state, v.admits, v.missing, v.reason) == (CORE_COMPAT_OK, True, (), "")

    def test_two_cores_of_one_version_are_told_apart(self, monkeypatch):
        """The whole point: a core built before the contract reads the same version as one built
        after it, and the version floor admits both. The feature does not."""
        assert check_core_compatibility(HOST, [APPROVAL_ANSWERS], host=HOST).admits is True
        monkeypatch.setattr(core_features, "CORE_FEATURES", frozenset())
        before = check_core_compatibility(HOST, [APPROVAL_ANSWERS], host=HOST)
        assert before.admits is False
        assert before.missing == (APPROVAL_ANSWERS,)

    def test_a_missing_feature_is_named_where_the_version_cannot_be_read(self):
        """A feature needs no reading of the core's version, so a core whose version cannot be
        read still names the feature it lacks, rather than only that it cannot read itself."""
        assert check_core_compatibility("99.0.0", host="unknown").state == (
            CORE_COMPAT_UNKNOWN_HOST
        )
        v = check_core_compatibility("99.0.0", [NEWER], host="unknown")
        assert v.state == CORE_COMPAT_INCOMPATIBLE
        assert NEWER in v.reason

    def test_a_floor_and_a_feature_both_unmet_are_both_named(self):
        v = check_core_compatibility("99.0.0", [NEWER, "another-newer-feature"], host=HOST)
        assert v.reason == (
            f"requires PersonalClaw 99.0.0 or newer, but this core is {HOST} and does not have the "
            f"core features '{NEWER}' and 'another-newer-feature' it relies on. Upgrade the core — "
            "run `personalclaw update` — then try again."
        )

    def test_a_floor_alone_keeps_its_sentence(self):
        assert check_core_compatibility("99.0.0", host=HOST).reason == (
            f"requires PersonalClaw 99.0.0 or newer, but this core is {HOST}. Upgrade the core — "
            "run `personalclaw update` — then try again."
        )

    def test_a_feature_named_twice_is_missing_once(self):
        assert check_core_compatibility("", [NEWER, NEWER], host=HOST).missing == (NEWER,)

    def test_the_card_carries_what_is_missing(self):
        d = check_core_compatibility("", [NEWER], host=HOST).to_dict()
        assert d["state"] == CORE_COMPAT_INCOMPATIBLE and d["missing"] == [NEWER]
        assert check_core_compatibility("", host=HOST).to_dict()["missing"] == []


# ── the manifest field ───────────────────────────────────────────────────────────────────────


class TestTheManifestField:
    def test_it_round_trips_and_feeds_the_verdict(self):
        m = AppManifest.from_dict(
            {"name": "x", "version": "1.0.0", "requiresCoreFeatures": [APPROVAL_ANSWERS, NEWER]}
        )
        assert m.requiresCoreFeatures == [APPROVAL_ANSWERS, NEWER]
        assert m.to_dict()["requiresCoreFeatures"] == [APPROVAL_ANSWERS, NEWER]
        assert m.core_compatibility().missing == (NEWER,)

    def test_declaring_none_is_absent_on_the_wire(self):
        m = AppManifest.from_dict({"name": "x", "version": "1.0.0"})
        assert m.requiresCoreFeatures == [] and "requiresCoreFeatures" not in m.to_dict()

    def test_a_lone_name_is_a_list_of_one(self):
        m = AppManifest.from_dict({"name": "x", "version": "1.0.0", "requiresCoreFeatures": NEWER})
        assert m.requiresCoreFeatures == [NEWER]

    @pytest.mark.parametrize("bad", ["Approval Answers", "approval_answers", "", 7, {"a": 1}])
    def test_an_entry_that_is_not_a_name_is_an_install_error(self, bad):
        m = AppManifest.from_dict(
            {
                "name": "x",
                "version": "1.0.0",
                "displayName": "X",
                "description": "d.",
                "requiresCoreFeatures": [APPROVAL_ANSWERS, bad],
            }
        )
        assert any(
            "requiresCoreFeatures entries must be core feature names" in e for e in m.validate()
        )

    def test_a_duplicate_is_an_install_error(self):
        m = AppManifest.from_dict(
            {
                "name": "x",
                "version": "1.0.0",
                "displayName": "X",
                "description": "d.",
                "requiresCoreFeatures": [APPROVAL_ANSWERS, APPROVAL_ANSWERS],
            }
        )
        assert "requiresCoreFeatures has duplicate entries: ['approval-answers']" in m.validate()

    def test_a_newer_cores_feature_is_not_a_manifest_error(self):
        """It is a refusal of THIS core, which says why; the manifest itself is fine."""
        m = AppManifest.from_dict(
            {
                "name": "x",
                "version": "1.0.0",
                "displayName": "X",
                "description": "d.",
                "requiresCoreFeatures": [NEWER],
            }
        )
        assert m.validate() == []


# ── every door ───────────────────────────────────────────────────────────────────────────────


class TestTheReview:
    """``preview`` is what the install and update dialogs show before anything changes."""

    def test_the_review_refuses_naming_the_feature(self, tmp_path, caplog):
        src = _source(tmp_path, features=[NEWER])
        with caplog.at_level(logging.WARNING, logger="personalclaw.apps.app_manager"):
            res = app_manager.preview(src)
        assert res.ok is False and res.consent in ("", None) and not res.needs_consent
        assert res.error.startswith("install refused: 'needs-app' needs a newer PersonalClaw")
        assert NEWER in res.error and "personalclaw update" in res.error
        assert any(NEWER in r.getMessage() for r in caplog.records), "nothing in the log"

    def test_the_review_refuses_a_floor_above_this_core(self, tmp_path):
        res = app_manager.preview(_source(tmp_path, floor="99.0.0"))
        assert res.ok is False
        assert "99.0.0" in res.error and HOST in res.error

    def test_the_review_of_an_update_refuses_naming_the_feature(self, tmp_path):
        assert app_manager.install(_source(tmp_path), confirm=True).ok is True
        newer = _source(tmp_path, version="2.0.0", features=[NEWER], subdir="v2")
        res = app_manager.preview(newer, name="needs-app")
        assert res.ok is False and not res.needs_consent
        assert res.error.startswith("update refused: 'needs-app' needs a newer PersonalClaw")

    def test_the_review_of_an_app_needing_what_this_core_offers_waits_for_consent(self, tmp_path):
        res = app_manager.preview(_source(tmp_path, features=[APPROVAL_ANSWERS]))
        assert res.error == "" and res.needs_consent is True and res.consent


class TestInstallAndUpdate:
    def test_install_refuses_and_installs_nothing(self, tmp_path, monkeypatch):
        audited: list[tuple[str, str, str]] = []
        monkeypatch.setattr(
            app_manager,
            "_audit",
            lambda op, outcome, name, **kw: audited.append((op, outcome, kw.get("error", ""))),
        )
        res = app_manager.install(_source(tmp_path, features=[NEWER]), confirm=True)
        assert res.ok is False and NEWER in res.error
        assert manager._read_installed("needs-app") is None
        assert not app_manager.app_dir("needs-app").exists()
        assert any(op == "install" and NEWER in err for op, _o, err in audited)

    def test_update_refuses_and_keeps_the_installed_version(self, tmp_path, monkeypatch):
        assert app_manager.install(_source(tmp_path), confirm=True).ok is True
        audited: list[tuple[str, str, str]] = []
        monkeypatch.setattr(
            app_manager,
            "_audit",
            lambda op, outcome, name, **kw: audited.append((op, name, kw.get("error", ""))),
        )
        newer = _source(tmp_path, version="2.0.0", features=[NEWER], subdir="v2")
        res = app_manager.update(newer, "needs-app", confirm=True)
        assert res.ok is False and res.name == "needs-app"
        assert res.error.startswith("update refused: 'needs-app' needs a newer PersonalClaw")
        meta = manager._read_installed("needs-app")
        assert meta is not None and meta.version == "1.0.0"
        assert ("update", "needs-app", res.error) in audited

    def test_an_app_needing_what_this_core_offers_installs_and_updates(self, tmp_path):
        assert (
            app_manager.install(_source(tmp_path, features=[APPROVAL_ANSWERS]), confirm=True).ok
            is True
        )
        newer = _source(tmp_path, version="2.0.0", features=[APPROVAL_ANSWERS], subdir="v2")
        assert app_manager.update(newer, "needs-app", confirm=True).ok is True
        meta = manager._read_installed("needs-app")
        assert meta is not None and meta.version == "2.0.0"


class TestSwitchingOn:
    def test_enable_refuses_an_installed_app_this_core_cannot_host(self, tmp_path, monkeypatch):
        """The installed copy needs a feature the running core lacks: a home restored onto an
        older core. Nothing of the app is started."""
        src = _source(tmp_path, features=[APPROVAL_ANSWERS])
        assert app_manager.install(src, confirm=True).ok is True
        assert app_manager.disable("needs-app") is True
        monkeypatch.setattr(core_features, "CORE_FEATURES", frozenset())
        assert app_manager.enable("needs-app") is False
        meta = manager._read_installed("needs-app")
        assert meta is not None and meta.enabled is False


class TestTheStoreCard:
    def test_the_card_says_what_this_core_lacks(self, tmp_path):
        root = tmp_path / "store"
        _source(tmp_path, name="needs-newer", features=[NEWER], subdir="store")
        _source(tmp_path, name="needs-offered", features=[APPROVAL_ANSWERS], subdir="store")
        catalog.add_local_source(str(root))
        cards = {e.name: e.coreCompatibility for e in catalog._scan_local_sources()}
        assert cards["needs-newer"]["state"] == CORE_COMPAT_INCOMPATIBLE
        assert cards["needs-newer"]["missing"] == [NEWER]
        assert NEWER in cards["needs-newer"]["reason"]
        assert cards["needs-offered"]["state"] == CORE_COMPAT_OK


# ── over HTTP: the consent dialog's review, and the update it commits ───────────────────────


@asynccontextmanager
async def _client(home: Path):
    from personalclaw import inbox as _inbox
    from personalclaw.dashboard.handlers.apps import register_app_routes
    from personalclaw.providers import entity_routes as _er

    with (
        patch.object(catalog, "config_dir", return_value=home),
        patch.object(_er, "config_dir", return_value=home),
        patch.object(_inbox, "config_dir", return_value=home),
    ):
        backend_runtime._supervisor = backend_runtime.BackendSupervisor()
        app = web.Application()
        register_app_routes(app)
        async with TestClient(TestServer(app)) as client:
            try:
                yield client
            finally:
                backend_runtime.get_backend_supervisor().stop_all()


@pytest.mark.asyncio
async def test_the_dialogs_review_says_which_feature_this_core_lacks(tmp_path):
    async with _client(tmp_path) as client:
        r = await client.post(
            "/api/apps/preview", json={"source": str(_source(tmp_path, features=[NEWER]))}
        )
        assert r.status == 400, await r.text()
        error = (await r.json())["error"]
        assert error["code"] == "app_preview_failed"
        assert error["message"].startswith(
            "install refused: 'needs-app' needs a newer PersonalClaw"
        )
        assert NEWER in error["message"]


@pytest.mark.asyncio
async def test_an_update_this_core_cannot_host_is_refused_and_changes_nothing(tmp_path):
    async with _client(tmp_path) as client:
        v1 = str(_source(tmp_path))
        review = await (await client.post("/api/apps/preview", json={"source": v1})).json()
        r = await client.post("/api/apps", json={"source": v1, "consent": review["consent"]})
        assert r.status == 201, await r.text()

        v2 = str(_source(tmp_path, version="2.0.0", features=[NEWER], subdir="v2"))
        r = await client.post("/api/apps/preview", json={"source": v2, "name": "needs-app"})
        assert r.status == 400
        assert NEWER in (await r.json())["error"]["message"]
        r = await client.post("/api/apps/needs-app/update", json={"source": v2})
        assert r.status == 400, await r.text()
        body = await r.json()
        assert body["ok"] is False and not body.get("needs_consent")
        assert body["error"].startswith("update refused: 'needs-app' needs a newer PersonalClaw")
        got = await (await client.get("/api/apps/needs-app")).json()
        assert got["installed"]["version"] == "1.0.0"
