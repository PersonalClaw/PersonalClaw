"""App dependency ledger (A3) — reference-counted shared-dependency tracking.

Covers: install records installedBy per dep; classify distinguishes removable
(only this app) / shared (another app needs it) / userInstalled (no app owns
it); record_uninstall removes the app + returns classification; and the
end-to-end safety property — uninstalling one of two apps that share an MCP dep
keeps the dep (shared), not removable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, dependency_ledger, manager
from personalclaw.apps.dependency_ledger import DepDisposition
from personalclaw.apps.manifest import AppManifest


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


def _manifest(name: str, *, mcp=None, skills=None, agents=None) -> AppManifest:
    return AppManifest.from_dict(
        {
            "name": name,
            "version": "1.0.0",
            "displayName": name,
            "description": "x",
            "dependencies": {
                "marketplace": {
                    "mcp": mcp or [],
                    "skills": skills or [],
                    "agents": agents or [],
                }
            },
        }
    )


def _src(tmp_path: Path, name: str, *, subdir: str, mcp=None) -> Path:
    d = tmp_path / subdir / name
    d.mkdir(parents=True)
    mani = {"name": name, "version": "1.0.0", "displayName": name, "description": "x"}
    if mcp:
        mani["dependencies"] = {"marketplace": {"mcp": mcp}}
    (d / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    return d


class TestLedgerUnit:
    def test_record_install_adds_owner(self):
        dependency_ledger.record_install(_manifest("app-a", mcp=["shared-mcp"]))
        assert dependency_ledger.installed_by("mcp:shared-mcp") == ["app-a"]

    def test_dep_keys_singularize_kind(self):
        m = _manifest("a", mcp=["m1"], skills=["s1"], agents=["g1"])
        assert set(dependency_ledger.manifest_dep_keys(m)) == {"mcp:m1", "skill:s1", "agent:g1"}

    def test_object_form_dep_id(self):
        m = _manifest("a", mcp=[{"id": "obj-mcp", "managedBy": "app"}])
        assert dependency_ledger.manifest_dep_keys(m) == ["mcp:obj-mcp"]

    def test_classify_removable_when_sole_owner(self):
        m = _manifest("solo", mcp=["only-mine"])
        dependency_ledger.record_install(m)
        cls = {c.key: c.disposition for c in dependency_ledger.classify_uninstall(m)}
        assert cls["mcp:only-mine"] is DepDisposition.REMOVABLE

    def test_classify_shared_when_another_owner(self):
        dependency_ledger.record_install(_manifest("app-a", mcp=["common"]))
        m_b = _manifest("app-b", mcp=["common"])
        dependency_ledger.record_install(m_b)
        cls = {c.key: c for c in dependency_ledger.classify_uninstall(m_b)}
        assert cls["mcp:common"].disposition is DepDisposition.SHARED
        assert cls["mcp:common"].remaining == ["app-a"]

    def test_classify_user_installed_when_no_record(self):
        # The app declares a dep the ledger never recorded (user added it directly).
        m = _manifest("late", mcp=["user-added"])
        cls = {c.key: c.disposition for c in dependency_ledger.classify_uninstall(m)}
        assert cls["mcp:user-added"] is DepDisposition.USER_INSTALLED

    def test_record_uninstall_removes_owner(self):
        m = _manifest("app-a", mcp=["d1"])
        dependency_ledger.record_install(m)
        dependency_ledger.record_uninstall(m)
        assert dependency_ledger.installed_by("mcp:d1") == []


class TestLedgerThroughLifecycle:
    def test_install_records_via_app_manager(self, tmp_path):
        app_manager.install(_src(tmp_path, "app-a", subdir="s1", mcp=["shared"]))
        assert "app-a" in dependency_ledger.installed_by("mcp:shared")

    def test_two_apps_share_dep_force_uninstall_keeps_shared(self, tmp_path):
        app_manager.install(_src(tmp_path, "app-a", subdir="s1", mcp=["shared"]))
        app_manager.install(_src(tmp_path, "app-b", subdir="s2", mcp=["shared"]))
        # Preview app-a's uninstall: the shared dep should classify SHARED.
        preview = {c.key: c.disposition for c in app_manager.preview_uninstall("app-a")}
        assert preview["mcp:shared"] is DepDisposition.SHARED
        # Force-uninstall app-a (real removal) → app-b still owns the dep. (A plain
        # uninstall just deactivates, keeping files+deps, so the ledger only
        # updates on the destructive force path.)
        app_manager.force_uninstall("app-a")
        assert dependency_ledger.installed_by("mcp:shared") == ["app-b"]
        # Now app-b's uninstall sees it as REMOVABLE (sole remaining owner).
        preview_b = {c.key: c.disposition for c in app_manager.preview_uninstall("app-b")}
        assert preview_b["mcp:shared"] is DepDisposition.REMOVABLE

    def test_preview_unknown_app_empty(self, tmp_path):
        assert app_manager.preview_uninstall("ghost") == []
