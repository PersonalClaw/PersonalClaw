"""App Platform lifecycle core (A1) — install / enable / disable / uninstall.

Drives apps.app_manager against a real fixture app on disk (isolated config dir),
covering: clean install + onInstall hook runs + installed.json written; the
scanner gate (dangerous → refused non-overridable, warning → needs_consent →
confirm installs); enable/disable flips state + runs hooks; uninstall runs
onUninstall + removes the dir; and that a hook timeout/failure aborts cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager
from personalclaw.supply_chain import Verdict


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    """Point config_dir at a tmp dir so apps install into an isolated tree."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    # apps.manager imported config_dir by reference — patch there too.
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


def _make_app_source(
    tmp_path: Path,
    *,
    name: str = "demo-app",
    manifest_extra: dict | None = None,
    files: dict[str, str] | None = None,
) -> Path:
    src = tmp_path / "src" / name
    src.mkdir(parents=True)
    mani = {
        "name": name,
        "version": "1.0.0",
        "displayName": "Demo App",
        "description": "A demo fixture app",
    }
    if manifest_extra:
        mani.update(manifest_extra)
    (src / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    for rel, content in (files or {}).items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return src


class TestNativeLock:
    """A native (Tier-1) app is locked on: disable / uninstall / force-uninstall
    all refuse, leaving it installed + enabled. Only its config is editable."""

    def _install_native(self, tmp_path):
        src = _make_app_source(tmp_path, name="native-demo", manifest_extra={"native": True})
        res = app_manager.install(src, confirm=True)
        assert res.ok
        return "native-demo"

    def test_native_disable_refused(self, tmp_path):
        name = self._install_native(tmp_path)
        assert app_manager.disable(name) is False
        meta = manager._read_installed(name)
        assert meta is not None and meta.enabled is True

    def test_native_uninstall_refused(self, tmp_path):
        name = self._install_native(tmp_path)
        assert app_manager.uninstall(name) is False
        assert manager._read_installed(name) is not None

    def test_native_force_uninstall_refused(self, tmp_path):
        name = self._install_native(tmp_path)
        assert app_manager.force_uninstall(name) is False
        assert (manager.app_dir(name) / "app.json").is_file()  # files intact

    def test_non_native_still_uninstallable(self, tmp_path):
        # Control: a normal (Tier-2) app disables/uninstalls as before.
        src = _make_app_source(tmp_path, name="normal-demo")
        assert app_manager.install(src, confirm=True).ok
        assert app_manager.uninstall("normal-demo") is True


class TestInstall:
    def test_clean_install_succeeds(self, tmp_path):
        src = _make_app_source(tmp_path)
        res = app_manager.install(src)
        assert res.ok and res.name == "demo-app"
        assert (manager.app_dir("demo-app") / "app.json").is_file()
        meta = manager._read_installed("demo-app")
        assert meta is not None and meta.enabled and meta.version == "1.0.0"

    def test_install_runs_oninstall_hook(self, tmp_path):
        # onInstall writes a marker file into the app dir.
        src = _make_app_source(
            tmp_path,
            manifest_extra={"setup": {"onInstall": "echo hi > installed_marker.txt"}},
        )
        res = app_manager.install(src)
        assert res.ok
        assert (manager.app_dir("demo-app") / "installed_marker.txt").is_file()

    def test_oninstall_hook_can_write_data_dir(self, tmp_path):
        # Regression: data/ must exist before the hook runs (apps seed state there).
        src = _make_app_source(
            tmp_path,
            manifest_extra={"setup": {"onInstall": "echo seeded > data/seed.txt"}},
        )
        res = app_manager.install(src)
        assert res.ok, res.error
        assert (manager.app_dir("demo-app") / "data" / "seed.txt").is_file()

    def test_dangerous_content_refused_non_overridable(self, tmp_path):
        src = _make_app_source(
            tmp_path,
            files={"scripts/evil.sh": "rm -rf / --no-preserve-root\n"},
        )
        # Even with confirm=True, a dangerous verdict is terminal.
        res = app_manager.install(src, confirm=True)
        assert not res.ok and res.scan.verdict is Verdict.DANGEROUS
        assert not manager.app_dir("demo-app").exists()  # never landed live

    def test_warning_needs_consent_then_installs(self, tmp_path):
        src = _make_app_source(
            tmp_path,
            files={"scripts/fetch.sh": "curl https://api.example.com/data\n"},
        )
        # Community/local tier: a plain curl is a warning → needs consent.
        res1 = app_manager.install(src, origin="local")
        assert not res1.ok and res1.needs_consent and res1.scan.verdict is Verdict.WARNING
        assert not manager.app_dir("demo-app").exists()
        # With confirm, it installs.
        res2 = app_manager.install(src, origin="local", confirm=True)
        assert res2.ok and manager.app_dir("demo-app").is_file() is False  # dir, not file
        assert manager.app_dir("demo-app").is_dir()

    def test_oninstall_failure_rolls_back(self, tmp_path):
        src = _make_app_source(
            tmp_path,
            manifest_extra={"setup": {"onInstall": "exit 7"}},
        )
        res = app_manager.install(src)
        assert not res.ok and "onInstall" in res.error
        assert not manager.app_dir("demo-app").exists()  # rolled back

    def test_double_install_refused(self, tmp_path):
        src = _make_app_source(tmp_path)
        assert app_manager.install(src).ok
        res2 = app_manager.install(src)
        assert not res2.ok and "already installed" in res2.error

    def test_invalid_manifest_refused(self, tmp_path):
        src = tmp_path / "src" / "bad"
        src.mkdir(parents=True)
        (src / "app.json").write_text('{"name": "Bad Name", "version": "nope"}', encoding="utf-8")
        res = app_manager.install(src)
        assert not res.ok and "manifest" in res.error.lower()


class TestEnableDisable:
    def test_disable_then_enable(self, tmp_path):
        src = _make_app_source(tmp_path)
        app_manager.install(src)
        assert app_manager.disable("demo-app")
        assert manager._read_installed("demo-app").enabled is False
        assert app_manager.enable("demo-app")
        assert manager._read_installed("demo-app").enabled is True

    def test_enable_unknown_app_is_false(self, tmp_path):
        assert app_manager.enable("ghost") is False


class TestUninstall:
    def test_uninstall_deactivates_keeps_files(self, tmp_path):
        # Uninstall = deactivate: files stay on disk, enabled flips to false.
        src = _make_app_source(tmp_path)
        app_manager.install(src)
        assert manager.app_dir("demo-app").is_dir()
        assert app_manager.uninstall("demo-app")
        assert manager.app_dir("demo-app").exists()  # files kept
        meta = manager._read_installed("demo-app")
        assert meta is not None and meta.enabled is False  # turned off

    def test_force_uninstall_removes_dir(self, tmp_path):
        src = _make_app_source(tmp_path)
        app_manager.install(src)
        assert manager.app_dir("demo-app").is_dir()
        assert app_manager.force_uninstall("demo-app")
        assert not manager.app_dir("demo-app").exists()  # files gone

    def test_force_uninstall_runs_onuninstall_hook(self, tmp_path):
        # onUninstall writes a marker into a sibling dir that survives removal.
        out = tmp_path / "out"
        out.mkdir()
        src = _make_app_source(
            tmp_path,
            manifest_extra={"setup": {"onUninstall": f"touch {out}/uninstalled.txt"}},
        )
        app_manager.install(src)
        app_manager.force_uninstall("demo-app")
        assert (out / "uninstalled.txt").is_file()

    def test_uninstall_unknown_is_false(self, tmp_path):
        assert app_manager.uninstall("ghost") is False
        assert app_manager.force_uninstall("ghost") is False


# ── path-traversal guard on app_dir (defense-in-depth, #44) ──────────────────


def test_app_dir_rejects_path_traversal():
    """app_dir is the single chokepoint for every app-scoped path (config, backend
    entry, lifecycle hooks, uninstall rmtree). A traversal/escape name must be
    rejected there so no caller can escape the apps/ sandbox — worst case an
    rmtree(app_dir('../..')) outside it."""
    for bad in ["..", ".", "../evil", "/etc/passwd", "a/b", "foo/../bar", "\x00x", ""]:
        with pytest.raises(ValueError):
            manager.app_dir(bad)


def test_app_dir_allows_real_and_special_names():
    """Real app ids AND legitimate special on-disk dirs (e.g. .quarantine from the
    skill supply-chain) must pass — the guard blocks escape, not enumeration."""
    for ok in ["brave-search", ".quarantine", "app_v2", "a"]:
        # resolves under apps/ without raising; must not escape the apps root
        p = manager.app_dir(ok).resolve()
        assert manager.apps_dir().resolve() in p.parents or p.parent == manager.apps_dir().resolve()


def test_config_path_helpers_are_traversal_safe():
    """The config read/write helpers (app_config + ProviderSettings) go through
    app_dir, so a traversal name can't reach the filesystem via them either."""
    from personalclaw.apps.app_config import read_config
    from personalclaw.providers.settings import ProviderSettings

    with pytest.raises(ValueError):
        read_config("../../etc/passwd")
    with pytest.raises(ValueError):
        ProviderSettings.config_path("../../evil")


class TestPlatformGate:
    """P21 Gap B: an app that must install on the user's local machine (installMode=
    client) or that doesn't support this server's OS short-circuits to a client-install
    result WITHOUT committing to the live tree; a normal server app is unaffected."""

    def test_client_install_mode_does_not_commit(self, tmp_path):
        src = _make_app_source(
            tmp_path,
            name="sampleapp",
            manifest_extra={
                "platform": {
                    "installMode": "client",
                    "clientInstall": {
                        "shell": "brew install --cask sampleapp",
                        "postInstall": "open ~/Applications/SampleApp.app",
                    },
                },
            },
        )
        res = app_manager.install(src, confirm=True)
        assert res.ok is False and res.needs_client_install is True
        assert res.client_install == {
            "shell": "brew install --cask sampleapp",
            "postInstall": "open ~/Applications/SampleApp.app",
        }
        # Nothing committed to the live tree.
        assert not (manager.app_dir("sampleapp")).exists()

    def test_unsupported_os_short_circuits(self, tmp_path):
        # An app that only supports an OS this machine isn't → client-install result.
        import sys

        other = "windows" if sys.platform != "win32" else "linux"
        src = _make_app_source(
            tmp_path,
            name="winonly",
            manifest_extra={
                "platform": {"os": [other]},
            },
        )
        res = app_manager.install(src, confirm=True)
        assert res.ok is False and res.needs_client_install is True
        assert not (manager.app_dir("winonly")).exists()

    def test_supported_server_app_installs_normally(self, tmp_path):
        # Default platform (macos/linux, server) → installs on darwin/linux CI, no gate.
        import sys

        if sys.platform not in ("darwin", "linux"):
            pytest.skip("default platform gate only clears on darwin/linux")
        src = _make_app_source(
            tmp_path,
            name="server-ok",
            manifest_extra={
                "platform": {"os": ["macos", "linux"], "installMode": "server"},
            },
        )
        res = app_manager.install(src, confirm=True)
        assert res.ok is True and res.needs_client_install is False
        assert (manager.app_dir("server-ok") / "app.json").is_file()

    def test_to_dict_carries_client_install_fields(self):
        r = app_manager.InstallResult(
            ok=False, name="x", needs_client_install=True, client_install={"shell": "s"}
        )
        d = r.to_dict()
        assert d["needs_client_install"] is True and d["client_install"] == {"shell": "s"}
