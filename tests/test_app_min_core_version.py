"""``minPersonalClawVersion`` is enforced, not just declared (#1778).

Before this, the manifest field validated and round-tripped but nothing read it: an
app declaring a floor above the running core installed cleanly and then failed at
runtime inside the app backend, where the error read as an app bug rather than a
version mismatch.

Covered here: the four-state verdict itself (``ok`` / ``invalid`` /
``unknown_host_version`` / ``incompatible``) and every path that puts an app into
effect — install, update, enable (the core-DOWNGRADE case, where the app is already
on disk), the gateway boot backend launcher, and the Store's install-consent card.

Two directions matter equally, so both are asserted throughout: only ``ok`` admits — a floor
above the core refuses, and so does a floor or a core version that cannot be read — and an
absent or satisfied floor still installs. ``TestVacuityFloor`` is the explicit guard that a
gate which simply refused everything would fail this file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import personalclaw
from personalclaw.apps import app_manager, app_runtime, catalog, manager
from personalclaw.apps.core_version import (
    CORE_COMPAT_INCOMPATIBLE,
    CORE_COMPAT_INVALID,
    CORE_COMPAT_OK,
    CORE_COMPAT_UNKNOWN_HOST,
    check_core_compatibility,
    host_core_version,
)
from personalclaw.apps.manifest import AppManifest

HOST = "1.4.2"  # the pretend running core for every path test below


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    """Isolated config dir + a pinned host core version, so no test depends on the
    version of the core it happens to be running against."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    # Patched on the ROOT package, not on `host_core_version`, so the real lazy
    # `from personalclaw import __version__` read is exercised.
    monkeypatch.setattr(personalclaw, "__version__", HOST)
    return tmp_path


def _make_app_source(
    tmp_path: Path,
    *,
    name: str = "demo-app",
    floor: str | None = None,
    manifest_extra: dict | None = None,
    subdir: str = "src",
) -> Path:
    src = tmp_path / subdir / name
    src.mkdir(parents=True, exist_ok=True)
    mani: dict = {
        "name": name,
        "version": "1.0.0",
        "displayName": "Demo App",
        "description": "A demo fixture app",
    }
    if floor is not None:
        mani["minPersonalClawVersion"] = floor
    if manifest_extra:
        mani.update(manifest_extra)
    (src / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    return src


# ---------------------------------------------------------------------------
# The verdict itself — one owner, four states
# ---------------------------------------------------------------------------


class TestFourStateVerdict:
    def test_absent_floor_is_ok_and_admits(self):
        v = check_core_compatibility("", host=HOST)
        assert v.state == CORE_COMPAT_OK
        assert v.admits is True
        assert v.reason == ""

    def test_older_floor_is_ok(self):
        assert check_core_compatibility("1.0.0", host=HOST).state == CORE_COMPAT_OK

    def test_equal_floor_is_ok(self):
        assert check_core_compatibility(HOST, host=HOST).state == CORE_COMPAT_OK

    def test_newer_floor_is_incompatible_and_does_not_admit(self):
        v = check_core_compatibility("99.0.0", host=HOST)
        assert v.state == CORE_COMPAT_INCOMPATIBLE
        assert v.admits is False
        assert "99.0.0" in v.reason and HOST in v.reason

    @pytest.mark.parametrize("floor", ["latest", "one.two.three", "~1.2.0", ">=1.2.0", "1.2.x"])
    def test_malformed_floor_is_invalid_and_refuses(self, floor):
        """Fails CLOSED: a floor that is not a version is a gate with no answer, and the
        reason names both the bad value and the host."""
        v = check_core_compatibility(floor, host=HOST)
        assert v.state == CORE_COMPAT_INVALID
        assert v.admits is False
        assert floor in v.reason and HOST in v.reason

    @pytest.mark.parametrize("host", ["unknown", "", "main"])
    def test_unmeasurable_host_refuses(self, host):
        """A core whose own version cannot be read cannot tell whether it is new enough."""
        v = check_core_compatibility("99.0.0", host=host)
        assert v.state == CORE_COMPAT_UNKNOWN_HOST
        assert v.admits is False
        assert "99.0.0" in v.reason and repr(host) in v.reason

    def test_a_source_checkouts_dev_version_is_compared(self):
        """A dev build is a version, older than its release: it is held to the floor."""
        assert check_core_compatibility("99.0.0", host="0.2.0.dev3+g9a1c").state == (
            CORE_COMPAT_INCOMPATIBLE
        )
        assert check_core_compatibility("0.1.0", host="0.2.0.dev3+g9a1c").state == CORE_COMPAT_OK

    def test_versions_compare_numerically_not_lexically(self):
        """The classic trap: ``"10.0.0" < "9.0.0"`` as strings."""
        assert check_core_compatibility("9.0.0", host="10.0.0").state == CORE_COMPAT_OK
        assert check_core_compatibility("0.10.0", host="0.9.0").state == CORE_COMPAT_INCOMPATIBLE
        assert check_core_compatibility("1.0.10", host="1.0.9").state == CORE_COMPAT_INCOMPATIBLE

    def test_a_prerelease_sorts_below_its_release(self):
        """``1.4.2-rc1`` is older than ``1.4.2``: a candidate floor is met by the release, and a
        release floor is not met by its candidate."""
        assert check_core_compatibility("1.4.2-rc1", host=HOST).state == CORE_COMPAT_OK
        assert check_core_compatibility("1.4.2", host="1.4.2-rc1").state == (
            CORE_COMPAT_INCOMPATIBLE
        )
        assert check_core_compatibility("v1.4.2", host=HOST).state == CORE_COMPAT_OK

    def test_an_unreadable_floor_is_not_read_as_zero(self):
        """A floor read as ``0`` would be met by every core, so junk refuses even on the newest
        core instead of passing as the lowest version."""
        assert check_core_compatibility("garbage", host="999.0.0").admits is False
        assert check_core_compatibility("0.0.0", host=HOST).state == CORE_COMPAT_OK

    def test_host_core_version_reads_the_running_core(self):
        assert host_core_version() == HOST

    def test_manifest_method_delegates_to_the_one_owner(self):
        m = AppManifest(name="x", version="1.0.0", minPersonalClawVersion="99.0.0")
        assert m.core_compatibility().state == CORE_COMPAT_INCOMPATIBLE
        assert m.core_compatibility(host="99.0.1").state == CORE_COMPAT_OK

    def test_to_dict_carries_all_four_facts(self):
        d = check_core_compatibility("99.0.0", host=HOST).to_dict()
        assert d["state"] == CORE_COMPAT_INCOMPATIBLE
        assert d["required"] == "99.0.0"
        assert d["host"] == HOST
        assert "99.0.0" in d["reason"]


# ---------------------------------------------------------------------------
# Entry path: install
# ---------------------------------------------------------------------------


class TestInstallPath:
    def test_newer_floor_refused_naming_both_versions(self, tmp_path):
        src = _make_app_source(tmp_path, name="needs-future", floor="99.0.0")
        res = app_manager.install(src, confirm=True)
        assert res.ok is False
        assert "99.0.0" in res.error and HOST in res.error
        assert "personalclaw update" in res.error  # states what to do next
        assert manager._read_installed("needs-future") is None
        assert not app_manager.app_dir("needs-future").exists()

    def test_older_floor_installs(self, tmp_path):
        src = _make_app_source(tmp_path, name="needs-past", floor="0.0.1")
        assert app_manager.install(src, confirm=True).ok is True

    def test_equal_floor_installs(self, tmp_path):
        src = _make_app_source(tmp_path, name="needs-exact", floor=HOST)
        assert app_manager.install(src, confirm=True).ok is True

    def test_absent_declaration_installs(self, tmp_path):
        src = _make_app_source(tmp_path, name="declares-nothing")
        assert app_manager.install(src, confirm=True).ok is True

    def test_malformed_declaration_is_refused_naming_it(self, tmp_path, caplog):
        src = _make_app_source(tmp_path, name="floor-typo", floor="latest")
        with caplog.at_level(logging.WARNING, logger="personalclaw.apps.app_manager"):
            res = app_manager.install(src, confirm=True)
        assert res.ok is False
        assert "'latest', which is not a version" in res.error and HOST in res.error
        assert any("latest" in r.getMessage() for r in caplog.records)
        assert manager._read_installed("floor-typo") is None

    def test_unmeasurable_host_is_refused_naming_it(self, tmp_path, caplog, monkeypatch):
        monkeypatch.setattr(personalclaw, "__version__", "unknown")
        src = _make_app_source(tmp_path, name="dev-tree", floor="0.1.0")
        with caplog.at_level(logging.WARNING, logger="personalclaw.apps.app_manager"):
            res = app_manager.install(src, confirm=True)
        assert res.ok is False
        assert "0.1.0 or newer" in res.error and "'unknown'" in res.error
        assert any("0.1.0" in r.getMessage() for r in caplog.records)

    def test_refusal_is_audited(self, tmp_path, monkeypatch):
        seen: list[tuple] = []
        monkeypatch.setattr(
            app_manager,
            "_audit",
            lambda op, outcome, name, **kw: seen.append((op, outcome, kw.get("error", ""))),
        )
        src = _make_app_source(tmp_path, name="needs-future", floor="99.0.0")
        app_manager.install(src, confirm=True)
        assert any(op == "install" and "99.0.0" in err for op, _o, err in seen)


# ---------------------------------------------------------------------------
# Entry path: update
# ---------------------------------------------------------------------------


class TestUpdatePath:
    def _install_v1(self, tmp_path, name="rolling-app"):
        src = _make_app_source(tmp_path, name=name, floor="1.0.0")
        assert app_manager.install(src, confirm=True).ok is True
        return name

    def test_update_raising_the_floor_above_the_core_is_refused(self, tmp_path):
        name = self._install_v1(tmp_path)
        newer = _make_app_source(
            tmp_path,
            name=name,
            floor="99.0.0",
            manifest_extra={"version": "2.0.0"},
            subdir="v2",
        )
        res = app_manager.update(newer, name, confirm=True)
        assert res.ok is False
        assert "99.0.0" in res.error and HOST in res.error
        assert "personalclaw update" in res.error
        # The old app is untouched — a refused update never swaps.
        meta = manager._read_installed(name)
        assert meta is not None and meta.version == "1.0.0"

    def test_update_within_the_floor_still_lands(self, tmp_path):
        name = self._install_v1(tmp_path)
        newer = _make_app_source(
            tmp_path,
            name=name,
            floor=HOST,
            manifest_extra={"version": "2.0.0"},
            subdir="v2",
        )
        res = app_manager.update(newer, name, confirm=True)
        assert res.ok is True, res.error
        meta = manager._read_installed(name)
        assert meta is not None and meta.version == "2.0.0"


# ---------------------------------------------------------------------------
# Entry path: enable (the core-DOWNGRADE case — the app is already on disk)
# ---------------------------------------------------------------------------


class TestEnablePath:
    def test_enable_refused_after_a_core_downgrade(self, tmp_path, monkeypatch):
        src = _make_app_source(tmp_path, name="was-fine", floor="1.0.0")
        assert app_manager.install(src, confirm=True).ok is True
        assert app_manager.disable("was-fine") is True

        monkeypatch.setattr(personalclaw, "__version__", "0.9.0")  # core downgraded
        assert app_manager.enable("was-fine") is False
        meta = manager._read_installed("was-fine")
        assert meta is not None and meta.enabled is False

    def test_enable_refusal_precedes_the_onenable_hook(self, tmp_path, monkeypatch):
        """No third-party hook runs for an app this core cannot host."""
        src = _make_app_source(
            tmp_path,
            name="hooked-app",
            floor="1.0.0",
            manifest_extra={"setup": {"onEnable": "echo enabled"}},
        )
        assert app_manager.install(src, confirm=True).ok is True
        assert app_manager.disable("hooked-app") is True

        ran: list[str] = []
        monkeypatch.setattr(
            app_manager, "_run_hook", lambda cmd, **kw: ran.append(str(cmd)) or None
        )
        monkeypatch.setattr(personalclaw, "__version__", "0.9.0")
        assert app_manager.enable("hooked-app") is False
        assert ran == []

    def test_enable_still_works_when_the_floor_is_met(self, tmp_path):
        src = _make_app_source(tmp_path, name="still-fine", floor="1.0.0")
        assert app_manager.install(src, confirm=True).ok is True
        assert app_manager.disable("still-fine") is True
        assert app_manager.enable("still-fine") is True
        meta = manager._read_installed("still-fine")
        assert meta is not None and meta.enabled is True


# ---------------------------------------------------------------------------
# Entry path: gateway boot-load of an already-installed, already-enabled app
# ---------------------------------------------------------------------------


class _FakeSupervisor:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.held: set[str] = set()

    def reap_orphans(self, name, entry):  # noqa: ANN001, ARG002
        return None

    def hold(self, name):  # noqa: ANN001
        self.held.add(name)

    def unhold(self, name):  # noqa: ANN001
        self.held.discard(name)

    def start(self, manifest):  # noqa: ANN001
        if manifest.name in self.held:  # what the real supervisor does: nothing held starts
            return None
        self.started.append(manifest.name)
        return object()


def _install_with_backend(tmp_path, name, floor):
    """Write an installed+enabled app tree directly — this is the boot-time state of an
    app installed before the gate existed, or one the core was downgraded under."""
    dest = app_manager.app_dir(name)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "server.py").write_text("", encoding="utf-8")
    (dest / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": name,
                "description": "d",
                "minPersonalClawVersion": floor,
                "backend": {"entryPoint": "server.py"},
            }
        ),
        encoding="utf-8",
    )
    manager._write_installed(
        name, manager.InstalledApp(name=name, version="1.0.0", displayName=name, enabled=True)
    )


class TestBootLoadPath:
    @pytest.fixture(autouse=True)
    def _fake_supervisor(self, monkeypatch):
        import personalclaw.apps.backend_runtime as backend_runtime

        sup = _FakeSupervisor()
        monkeypatch.setattr(backend_runtime, "get_backend_supervisor", lambda: sup)
        monkeypatch.delenv("PERSONALCLAW_SKIP_APP_BACKENDS", raising=False)
        return sup

    def test_incompatible_app_backend_is_not_started(self, tmp_path, _fake_supervisor, caplog):
        _install_with_backend(tmp_path, "stale-app", "99.0.0")
        with caplog.at_level(logging.WARNING, logger="personalclaw.apps.app_runtime"):
            started = app_runtime.start_installed()
        assert started == []
        assert _fake_supervisor.started == []
        # Held, so the watchdog's pass 30 s later does not start it either.
        assert _fake_supervisor.start(app_manager._manifest_of("stale-app")) is None
        assert any("99.0.0" in r.getMessage() for r in caplog.records)

    def test_compatible_app_backend_still_starts(self, tmp_path, _fake_supervisor):
        _install_with_backend(tmp_path, "fresh-app", "1.0.0")
        assert app_runtime.start_installed() == ["fresh-app"]
        assert _fake_supervisor.started == ["fresh-app"]


# ---------------------------------------------------------------------------
# Read surface: the Store install-consent card
# ---------------------------------------------------------------------------


class TestStoreConsentSurface:
    def _local_source(self, tmp_path, entries):
        root = tmp_path / "store"
        root.mkdir(parents=True, exist_ok=True)
        for name, floor in entries:
            _make_app_source(root.parent, name=name, floor=floor, subdir="store")
        catalog.add_local_source(str(root))
        return {e.name: e for e in catalog._scan_local_sources()}

    def test_card_carries_the_verdict_for_every_state(self, tmp_path):
        by_name = self._local_source(
            tmp_path,
            [("card-future", "99.0.0"), ("card-ok", "1.0.0"), ("card-typo", "latest")],
        )
        assert by_name["card-future"].coreCompatibility["state"] == CORE_COMPAT_INCOMPATIBLE
        assert by_name["card-future"].coreCompatibility["required"] == "99.0.0"
        assert by_name["card-future"].coreCompatibility["host"] == HOST
        assert "99.0.0" in by_name["card-future"].coreCompatibility["reason"]
        assert by_name["card-ok"].coreCompatibility["state"] == CORE_COMPAT_OK
        assert by_name["card-typo"].coreCompatibility["state"] == CORE_COMPAT_INVALID

    def test_verdict_reaches_the_api_payload(self, tmp_path):
        self._local_source(tmp_path, [("card-future", "99.0.0")])
        local_apps = catalog.available_catalog()["localApps"]
        assert local_apps, "expected the local source to surface a card"
        assert all("coreCompatibility" in d for d in local_apps)


# ---------------------------------------------------------------------------
# Vacuity floor — a gate that refused everything would fail HERE
# ---------------------------------------------------------------------------


class TestVacuityFloor:
    """The gate has to let the overwhelmingly common cases through. If any of these
    start refusing, the gate has become a wall and the file is no longer vacuous-safe."""

    @pytest.mark.parametrize(
        "floor",
        [
            None,  # declared nothing at all
            "0.0.1",  # far below the host
            "1.0.0",  # below the host
            HOST,  # exactly the host
            "1.2",  # read as 1.2.0, below the host
            "",  # explicitly empty
        ],
    )
    def test_these_all_install_and_enable(self, tmp_path, floor):
        name = "vacuity-app"
        src = _make_app_source(tmp_path, name=name, floor=floor)
        res = app_manager.install(src, confirm=True)
        assert res.ok is True, f"floor={floor!r} was refused: {res.error}"
        assert app_manager.disable(name) is True
        assert app_manager.enable(name) is True

    def test_only_ok_admits(self):
        states = {
            check_core_compatibility(f, host=h).state: check_core_compatibility(f, host=h).admits
            for f, h in [
                ("", HOST),
                ("1.0.0", HOST),
                ("latest", HOST),
                ("99.0.0", "unknown"),
                ("99.0.0", HOST),
            ]
        }
        assert states == {
            CORE_COMPAT_OK: True,
            CORE_COMPAT_INVALID: False,
            CORE_COMPAT_UNKNOWN_HOST: False,
            CORE_COMPAT_INCOMPATIBLE: False,
        }
