"""Untrusted-app sandbox P3 — the remaining capability enforcements:

* ``can_use_cron``  → app-declared manifest crons are registered only when the app
  holds the permission; reconcile prunes them when it doesn't (or the app is gone).
* ``can_use_storage`` → the backend launcher hands DATA_DIR only when held.
* ``can_use_mcp_tool`` → checker gates the tool-invoke path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import manager
from personalclaw.apps.manifest import Permissions
from personalclaw.apps.permissions import PermissionChecker


def _install_app(tmp_path: Path, name: str, *, permissions: dict, crons: list[dict] | None = None,
                 enabled: bool = True) -> None:
    """Materialize an installed app on disk (app.json + installed.json)."""
    appdir = tmp_path / "apps" / name
    appdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name, "version": "1.0.0", "displayName": name, "description": "x",
        "permissions": permissions,
    }
    if crons is not None:
        manifest["crons"] = crons
    (appdir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (appdir / "installed.json").write_text(json.dumps({
        "name": name, "version": "1.0.0", "enabled": enabled,
    }), encoding="utf-8")


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Point config_dir at a tmp tree so apps + crons live in isolation."""
    from personalclaw.config import loader
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


# ── can_use_cron: reconcile registers only permitted app crons ──

class TestAppCronReconcile:
    def _svc(self, tmp_path):
        from personalclaw.schedule import ScheduleService
        return ScheduleService(base_dir=tmp_path)

    def test_registers_crons_only_with_permission(self, app_env):
        from personalclaw.apps.app_crons import reconcile_app_crons
        _install_app(app_env, "with-cron", permissions={"cron": True},
                     crons=[{"name": "daily", "every": 3600, "agent": "x", "message": "go"}])
        _install_app(app_env, "no-cron", permissions={},  # cron NOT declared
                     crons=[{"name": "daily", "every": 3600, "agent": "x", "message": "go"}])
        svc = self._svc(app_env)
        reconcile_app_crons(svc)
        jobs = {j.name: j for j in svc.list_jobs(include_disabled=True)}
        assert "app:with-cron:daily" in jobs
        assert "app:no-cron:daily" not in jobs  # gated out
        # Headless app crons are always silent — no owner conversation to deliver
        # to; otherwise every run logs a Slack-DM channel-delivery failure.
        assert jobs["app:with-cron:daily"].silent is True

    def test_prunes_when_permission_revoked(self, app_env):
        from personalclaw.apps.app_crons import reconcile_app_crons
        _install_app(app_env, "app1", permissions={"cron": True},
                     crons=[{"name": "j", "every": 3600, "agent": "a", "message": "m"}])
        svc = self._svc(app_env)
        reconcile_app_crons(svc)
        assert any(j.name == "app:app1:j" for j in svc.list_jobs(include_disabled=True))
        # Revoke the permission + reconcile again → the app job is pruned.
        _install_app(app_env, "app1", permissions={},
                     crons=[{"name": "j", "every": 3600, "agent": "a", "message": "m"}])
        reconcile_app_crons(svc)
        assert not any(j.name == "app:app1:j" for j in svc.list_jobs(include_disabled=True))

    def test_prunes_when_app_disabled(self, app_env):
        from personalclaw.apps.app_crons import reconcile_app_crons
        _install_app(app_env, "app2", permissions={"cron": True},
                     crons=[{"name": "j", "every": 3600, "agent": "a", "message": "m"}])
        svc = self._svc(app_env)
        reconcile_app_crons(svc)
        assert any(j.name == "app:app2:j" for j in svc.list_jobs(include_disabled=True))
        _install_app(app_env, "app2", permissions={"cron": True}, enabled=False,
                     crons=[{"name": "j", "every": 3600, "agent": "a", "message": "m"}])
        reconcile_app_crons(svc)
        assert not any(j.name == "app:app2:j" for j in svc.list_jobs(include_disabled=True))

    def test_reconcile_converges_silent_on_existing_job(self, app_env):
        """A pre-fix app job persisted with silent=False must be corrected on the
        next reconcile (silent is manifest-driven, not a user toggle) — else it
        keeps trying to Slack-DM the app pseudo-id on every run."""
        from personalclaw.apps.app_crons import reconcile_app_crons
        _install_app(app_env, "loud", permissions={"cron": True},
                     crons=[{"name": "j", "every": 3600, "agent": "a", "message": "m"}])
        svc = self._svc(app_env)
        # Simulate a legacy job: registered loud (the pre-fix behavior).
        svc.add_job("app:loud:j", every_secs=3600, created_by="app:loud", silent=False)
        assert next(j for j in svc.list_jobs(include_disabled=True)
                    if j.name == "app:loud:j").silent is False
        reconcile_app_crons(svc)
        job = next(j for j in svc.list_jobs(include_disabled=True) if j.name == "app:loud:j")
        assert job.silent is True  # converged

    def test_reconcile_is_idempotent(self, app_env):
        from personalclaw.apps.app_crons import reconcile_app_crons
        _install_app(app_env, "app3", permissions={"cron": True},
                     crons=[{"name": "j", "cron_expr": "0 9 * * *", "agent": "a", "message": "m"}])
        svc = self._svc(app_env)
        reconcile_app_crons(svc)
        reconcile_app_crons(svc)  # second run must not duplicate
        matching = [j for j in svc.list_jobs(include_disabled=True) if j.name == "app:app3:j"]
        assert len(matching) == 1

    def test_lifecycle_handler_reconciles_on_transition(self, app_env, monkeypatch):
        """The reconcile is otherwise only run at gateway startup; the app lifecycle
        HANDLERS must re-run it so a disabled/uninstalled app's cron stops firing
        (and an enabled one starts) without a restart. Exercises the handler's
        ``_reconcile_app_crons`` seam directly against a live ScheduleService."""
        from personalclaw.dashboard.handlers.apps import _reconcile_app_crons

        _install_app(app_env, "lc-app", permissions={"cron": True},
                     crons=[{"name": "beat", "every": 1800, "agent": "a", "message": "m"}])
        svc = self._svc(app_env)

        # A request-like stub exposing request.app["state"].crons (what the handler reads).
        class _State:
            crons = svc

        class _AppMap:
            def get(self, key, default=None):
                return _State() if key == "state" else default

        class _Req:
            app = _AppMap()

        req = _Req()
        _reconcile_app_crons(req)  # simulate post-install/enable
        assert any(j.name == "app:lc-app:beat" for j in svc.list_jobs(include_disabled=True))

        # Disable the app on disk, then the handler reconcile must prune its cron.
        _install_app(app_env, "lc-app", permissions={"cron": True}, enabled=False,
                     crons=[{"name": "beat", "every": 1800, "agent": "a", "message": "m"}])
        _reconcile_app_crons(req)
        assert not any(j.name == "app:lc-app:beat" for j in svc.list_jobs(include_disabled=True))

    def test_reconcile_helper_noop_without_scheduler(self, app_env):
        """``--no-crons`` (no scheduler on state) must not raise from the handler seam."""
        from personalclaw.dashboard.handlers.apps import _reconcile_app_crons

        class _AppMap:
            def get(self, key, default=None):
                return default  # no "state"

        class _Req:
            app = _AppMap()

        _reconcile_app_crons(_Req())  # must be a silent no-op


# ── can_use_storage: DATA_DIR handed only when held ──

class TestStorageGate:
    def test_data_dir_only_when_permitted(self, app_env, monkeypatch):
        import subprocess
        from personalclaw.apps.backend_runtime import BackendSupervisor
        from personalclaw.apps.manifest import AppManifest, BackendConfig

        _install_app(app_env, "store-yes", permissions={"storage": True})
        _install_app(app_env, "store-no", permissions={})

        captured: dict = {}

        class _FakeProc:
            pid = 4321

        def _fake_popen(cmd, **kw):
            captured["env"] = kw.get("env", {})
            return _FakeProc()

        monkeypatch.setattr(subprocess, "Popen", _fake_popen)
        # A backend that will "launch" (entryPoint set); the launcher builds env.
        sup = BackendSupervisor()

        def _manifest(name):
            return AppManifest(name=name, version="1.0.0",
                               backend=BackendConfig(entryPoint="server.py", type="python"))

        for nm, expect_dir in (("store-yes", True), ("store-no", False)):
            (app_env / "apps" / nm / "server.py").write_text("# stub", encoding="utf-8")
            captured.clear()
            sup.start(_manifest(nm))
            env = captured.get("env", {})
            assert ("PERSONALCLAW_APP_DATA_DIR" in env) is expect_dir, nm


# ── can_use_mcp_tool: checker logic ──

class TestMcpToolChecker:
    def test_declared_tool_allowed_undeclared_denied(self):
        c = PermissionChecker(app_name="x", permissions=Permissions(mcpTools=["read_file", "grep*"]))
        assert c.can_use_mcp_tool("read_file")
        assert c.can_use_mcp_tool("grep")            # wildcard prefix
        assert c.can_use_mcp_tool("grep_dir")
        assert not c.can_use_mcp_tool("bash")
        assert not c.can_use_mcp_tool("write_file")

    def test_empty_mcptools_denies_all(self):
        c = PermissionChecker(app_name="x", permissions=Permissions(mcpTools=[]))
        assert not c.can_use_mcp_tool("read_file")
