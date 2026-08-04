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


def _install_app(
    tmp_path: Path,
    name: str,
    *,
    permissions: dict,
    crons: list[dict] | None = None,
    enabled: bool = True,
) -> None:
    """Materialize an installed app on disk (app.json + installed.json)."""
    appdir = tmp_path / "apps" / name
    appdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": name,
        "description": "x",
        "permissions": permissions,
    }
    if crons is not None:
        manifest["crons"] = crons
    (appdir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (appdir / "installed.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "enabled": enabled,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Point config_dir at a tmp tree so apps + crons live in isolation."""
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


# ── can_use_cron: reconcile registers only permitted app crons ──


class TestAppCronReconcile:
    """App-declared crons reconcile into the unified TRIGGER STORE (S108).

    These tests used to drive a `ScheduleService`, and passed the whole time app crons DID NOT FIRE:
    the reconciler wrote `crons.json`, which the clock engine never reads, so a declared cron stayed
    inert until the next boot imported it. Asserting against the store is what makes them mean
    "the cron will run" rather than "a row was written somewhere".
    """

    def _store(self, tmp_path):
        from personalclaw.triggers.store import TriggerStore

        return TriggerStore(base_dir=tmp_path)

    def _ids(self, store):
        return {row.trigger.id for row in store.load()}

    def test_registers_crons_only_with_permission(self, app_env):
        from personalclaw.apps.app_crons import reconcile_app_crons

        _install_app(
            app_env,
            "with-cron",
            permissions={"cron": True},
            crons=[{"name": "daily", "every": 3600, "agent": "x", "message": "go"}],
        )
        _install_app(
            app_env,
            "no-cron",
            permissions={},  # cron NOT declared
            crons=[{"name": "daily", "every": 3600, "agent": "x", "message": "go"}],
        )
        store = self._store(app_env)
        reconcile_app_crons(store)
        ids = self._ids(store)
        assert "app:with-cron:daily" in ids
        assert "app:no-cron:daily" not in ids  # gated out
        row = store.get("app:with-cron:daily")
        # Headless app crons are always silent — no owner conversation to deliver to; otherwise
        # every run logs a channel-delivery failure. `delivery: none` is the store's spelling.
        assert row.trigger.delivery == "none"
        # 🔴 And it must be ARMED, which is the whole difference between a registered cron and a
        # running one: `service.due_ids` only surfaces rows that carry a `next_fire_at`.
        assert row.trigger.next_fire_at
        assert row.ok, row.errors

    def test_the_registered_action_matches_what_the_migration_produces(self, app_env):
        """An app cron written here and one imported from `crons.json` must be the SAME row, or the
        two paths would produce triggers that fire differently."""
        from personalclaw.apps.app_crons import reconcile_app_crons

        _install_app(
            app_env,
            "shape",
            permissions={"cron": True},
            crons=[{"name": "j", "every": 3600, "agent": "helper", "message": "do it"}],
        )
        store = self._store(app_env)
        reconcile_app_crons(store)
        inline = (store.get("app:shape:j").trigger.workflow or {}).get("inline") or {}
        assert inline.get("provider") == "invoke-agent"
        config = inline.get("config") or {}
        # `task_template`, NOT `message` — the key `invoke-agent` actually reads.
        assert config.get("task_template") == "do it"
        assert config.get("agent") == "helper"
        assert config.get("approval_mode") == "auto"  # unattended: cannot wedge on a human

    def test_prunes_when_permission_revoked(self, app_env):
        from personalclaw.apps.app_crons import reconcile_app_crons

        _install_app(
            app_env,
            "app1",
            permissions={"cron": True},
            crons=[{"name": "j", "every": 3600, "agent": "a", "message": "m"}],
        )
        store = self._store(app_env)
        reconcile_app_crons(store)
        assert "app:app1:j" in self._ids(store)
        # Revoke the permission + reconcile again → the app trigger is pruned.
        _install_app(
            app_env,
            "app1",
            permissions={},
            crons=[{"name": "j", "every": 3600, "agent": "a", "message": "m"}],
        )
        reconcile_app_crons(store)
        assert "app:app1:j" not in self._ids(store)

    def test_prunes_when_app_disabled(self, app_env):
        from personalclaw.apps.app_crons import reconcile_app_crons

        _install_app(
            app_env,
            "app2",
            permissions={"cron": True},
            crons=[{"name": "j", "every": 3600, "agent": "a", "message": "m"}],
        )
        store = self._store(app_env)
        reconcile_app_crons(store)
        assert "app:app2:j" in self._ids(store)
        _install_app(
            app_env,
            "app2",
            permissions={"cron": True},
            enabled=False,
            crons=[{"name": "j", "every": 3600, "agent": "a", "message": "m"}],
        )
        reconcile_app_crons(store)
        assert "app:app2:j" not in self._ids(store)

    def test_a_users_own_trigger_is_never_pruned(self, app_env):
        """The diff is scoped to the `app:` prefix. A reconcile that swept anything else would
        delete the user's automations whenever an app was disabled."""
        from personalclaw.apps.app_crons import reconcile_app_crons
        from personalclaw.triggers.models import Trigger

        store = self._store(app_env)
        store.upsert(
            Trigger(
                id="clock:mine",
                name="mine",
                kind="clock",
                spec={"kind": "interval", "interval_secs": 600},
            )
        )
        reconcile_app_crons(store)  # no apps installed at all → desired set is empty
        assert "clock:mine" in self._ids(store)

    def test_reconcile_converges_silent_on_existing_trigger(self, app_env):
        """A row persisted with a channel must be corrected on the next reconcile (silent is
        manifest-driven, not a user toggle) — else it keeps trying to DM the app pseudo-id."""
        from personalclaw.apps.app_crons import reconcile_app_crons
        from personalclaw.triggers.models import Trigger

        _install_app(
            app_env,
            "loud",
            permissions={"cron": True},
            crons=[{"name": "j", "every": 3600, "agent": "a", "message": "m"}],
        )
        store = self._store(app_env)
        # Simulate a legacy row: registered loud (the pre-fix behavior).
        store.upsert(
            Trigger(
                id="app:loud:j",
                name="app:loud:j",
                kind="clock",
                created_by="app:loud",
                spec={"kind": "interval", "interval_secs": 3600},
                delivery="channel:C123",
            )
        )
        reconcile_app_crons(store)
        assert store.get("app:loud:j").trigger.delivery == "none"  # converged

    def test_reconcile_is_idempotent(self, app_env):
        from personalclaw.apps.app_crons import reconcile_app_crons

        _install_app(
            app_env,
            "app3",
            permissions={"cron": True},
            crons=[{"name": "j", "cron_expr": "0 9 * * *", "agent": "a", "message": "m"}],
        )
        store = self._store(app_env)
        reconcile_app_crons(store)
        reconcile_app_crons(store)  # second run must not duplicate
        matching = [row for row in store.load() if row.trigger.id == "app:app3:j"]
        assert len(matching) == 1

    def test_an_unreadable_store_is_survived(self, app_env):
        """Reconciliation runs at boot and on every app lifecycle transition — it must never be able
        to block either one."""
        from unittest.mock import MagicMock

        from personalclaw.apps.app_crons import reconcile_app_crons

        broken = MagicMock()
        broken.load.side_effect = OSError("triggers.json is gibberish")
        reconcile_app_crons(broken)  # must not raise
        broken.upsert.assert_not_called()

    def test_lifecycle_handler_reconciles_on_transition(self, app_env, monkeypatch):
        """The reconcile is otherwise only run at gateway startup; the app lifecycle HANDLERS must
        re-run it so a disabled/uninstalled app's cron stops firing (and an enabled one starts)
        without a restart. Exercises the handler's ``_reconcile_app_crons`` seam."""
        from personalclaw.dashboard.handlers.apps import _reconcile_app_crons

        _install_app(
            app_env,
            "lc-app",
            permissions={"cron": True},
            crons=[{"name": "beat", "every": 1800, "agent": "a", "message": "m"}],
        )
        store = self._store(app_env)

        class _State:
            no_crons = False

        class _AppMap:
            def get(self, key, default=None):
                return _State() if key == "state" else default

        class _Req:
            app = _AppMap()

        req = _Req()
        _reconcile_app_crons(req)  # simulate post-install/enable
        assert "app:lc-app:beat" in self._ids(store)

        # Disable the app on disk, then the handler reconcile must prune its cron.
        _install_app(
            app_env,
            "lc-app",
            permissions={"cron": True},
            enabled=False,
            crons=[{"name": "beat", "every": 1800, "agent": "a", "message": "m"}],
        )
        _reconcile_app_crons(req)
        assert "app:lc-app:beat" not in self._ids(store)

    def test_reconcile_helper_noop_with_no_crons(self, app_env):
        """``--no-crons`` must not register anything from the handler seam.

        The guard moved from "is there a scheduler on state" to `state.no_crons` (S108): the store
        is a FILE, not a service, so the old presence check stopped answering the question — it
        would have reconciled happily in a `--no-crons` gateway.
        """
        from personalclaw.dashboard.handlers.apps import _reconcile_app_crons

        _install_app(
            app_env,
            "nc-app",
            permissions={"cron": True},
            crons=[{"name": "j", "every": 3600, "agent": "a", "message": "m"}],
        )

        class _State:
            no_crons = True

        class _AppMap:
            def get(self, key, default=None):
                return _State() if key == "state" else default

        class _Req:
            app = _AppMap()

        _reconcile_app_crons(_Req())
        assert "app:nc-app:j" not in self._ids(self._store(app_env))

    def test_reconcile_helper_survives_a_stateless_request(self, app_env):
        """A request with no "state" at all must not raise from the handler seam."""
        from personalclaw.dashboard.handlers.apps import _reconcile_app_crons

        class _AppMap:
            def get(self, key, default=None):
                return default  # no "state"

        class _Req:
            app = _AppMap()

        _reconcile_app_crons(_Req())  # must not raise


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
            return AppManifest(
                name=name,
                version="1.0.0",
                backend=BackendConfig(entryPoint="server.py", type="python"),
            )

        for nm, expect_dir in (("store-yes", True), ("store-no", False)):
            (app_env / "apps" / nm / "server.py").write_text("# stub", encoding="utf-8")
            captured.clear()
            sup.start(_manifest(nm))
            env = captured.get("env", {})
            assert ("PERSONALCLAW_APP_DATA_DIR" in env) is expect_dir, nm


# ── can_use_mcp_tool: checker logic ──


class TestMcpToolChecker:
    def test_declared_tool_allowed_undeclared_denied(self):
        c = PermissionChecker(
            app_name="x", permissions=Permissions(mcpTools=["read_file", "grep*"])
        )
        assert c.can_use_mcp_tool("read_file")
        assert c.can_use_mcp_tool("grep")  # wildcard prefix
        assert c.can_use_mcp_tool("grep_dir")
        assert not c.can_use_mcp_tool("bash")
        assert not c.can_use_mcp_tool("write_file")

    def test_empty_mcptools_denies_all(self):
        c = PermissionChecker(app_name="x", permissions=Permissions(mcpTools=[]))
        assert not c.can_use_mcp_tool("read_file")
