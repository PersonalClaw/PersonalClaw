"""Tests for session restore on startup and dashboard config API."""

import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.chat import restore_recent_sessions
from personalclaw.dashboard.state import DashboardState
from personalclaw.history import ConversationLog

# ── Helpers ──


def _make_state(tmp_path, **kwargs):
    """Create a DashboardState with mocked services and real ConversationLog."""
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.remove = AsyncMock()
    return DashboardState(
        sessions=sessions,
        crons=MagicMock(list_jobs=MagicMock(return_value=[]), status=MagicMock(return_value={})),
        lessons=MagicMock(load_all=MagicMock(return_value=[])),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
        **kwargs,
    )


def _write_session(
    tmp_path: Path, key: str, messages: list[dict], meta: dict | None = None
) -> None:
    """Write a JSONL session file directly for test setup."""
    path = tmp_path / f"{key}.jsonl"
    lines = []
    meta_line = {"_type": "metadata", "created_at": "2026-03-23T10:00:00", "last_consolidated": 0}
    if meta:
        meta_line.update(meta)
    lines.append(json.dumps(meta_line))
    for m in messages:
        lines.append(json.dumps(m))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_config_app(tmp_path):
    """Minimal aiohttp app with dashboard config endpoint."""
    from personalclaw.dashboard.handlers import api_dashboard_config

    state = _make_state(tmp_path)
    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/dashboard/config", api_dashboard_config)
    app.router.add_put("/api/dashboard/config", api_dashboard_config)
    return app


# ── restore_recent_sessions tests ──


class TestRestoreRecentSessions:
    def test_returns_zero_when_no_conversation_log(self, tmp_path):
        """Returns 0 when state has no conversation_log."""
        state = _make_state(tmp_path)
        state.conversation_log = None
        assert restore_recent_sessions(state) == 0

    def test_restores_recent_dashboard_session(self, tmp_path, monkeypatch):
        """Restores a dashboard session modified within the time window."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_chat1",
            [
                {"role": "user", "content": "hello", "ts": "2026-03-23T10:00:00"},
                {"role": "assistant", "content": "hi there", "ts": "2026-03-23T10:00:01"},
            ],
            meta={
                "title": "Test Chat",
                "agent": "personalclaw",
                "workspace_dir": "myws",
                "mode": "orchestrator",
            },
        )
        # Touch the file to make it recent
        path = tmp_path / "dashboard_chat1.jsonl"
        path.touch()

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60)
        assert restored == 1
        assert "chat1" in state._sessions
        session = state._sessions["chat1"]
        assert session.title == "Test Chat"
        assert session.agent == "personalclaw"
        assert session.workspace_dir == "myws"
        assert session.mode == "orchestrator"
        assert len(session.messages) == 2
        assert session.messages[0]["content"] == "hello"
        assert session.messages[1]["content"] == "hi there"
        assert session._dirty is False
        assert session._resumed_count == 2

    def test_restores_mode_empty_by_default(self, tmp_path, monkeypatch):
        """Sessions without mode in metadata default to empty string."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_nomode",
            [{"role": "user", "content": "hi", "ts": "2026-03-23T10:00:00"}],
            meta={"title": "No Mode"},
        )
        (tmp_path / "dashboard_nomode.jsonl").touch()
        state = _make_state(tmp_path)
        restore_recent_sessions(state, window_minutes=60)
        assert state._sessions["nomode"].mode == ""

    def test_trust_flags_not_restored(self, tmp_path, monkeypatch):
        """Trust flags in metadata are NOT restored — security boundary."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_trusted",
            [{"role": "user", "content": "hi", "ts": "2026-03-23T10:00:00"}],
            meta={"title": "Trusted", "trust": True, "trust_reads": True},
        )
        (tmp_path / "dashboard_trusted.jsonl").touch()
        state = _make_state(tmp_path)
        restore_recent_sessions(state, window_minutes=60)
        assert state._sessions["trusted"]._trust is False
        assert state._sessions["trusted"]._trust_reads is False

    def test_skips_old_sessions(self, tmp_path, monkeypatch):
        """Sessions older than the window are not restored."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_old",
            [{"role": "user", "content": "old msg", "ts": "2026-03-20T10:00:00"}],
        )
        # Set mtime to 2 hours ago
        path = tmp_path / "dashboard_old.jsonl"
        old_time = time.time() - 7200
        os.utime(path, (old_time, old_time))

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=30)
        assert restored == 0
        assert "old" not in state._sessions

    def test_skips_non_dashboard_sessions(self, tmp_path, monkeypatch):
        """Sessions not prefixed with 'dashboard' are skipped."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "slack_thread123",
            [{"role": "user", "content": "slack msg", "ts": "2026-03-23T10:00:00"}],
        )
        path = tmp_path / "slack_thread123.jsonl"
        path.touch()

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60)
        assert restored == 0

    def test_skips_already_existing_sessions(self, tmp_path, monkeypatch):
        """Does not overwrite sessions that already exist in state."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_existing",
            [{"role": "user", "content": "msg", "ts": "2026-03-23T10:00:00"}],
        )
        path = tmp_path / "dashboard_existing.jsonl"
        path.touch()

        state = _make_state(tmp_path)
        # Pre-create the session
        session = state.get_or_create_session("existing")
        session.append("user", "already here")
        session.drain()

        restored = restore_recent_sessions(state, window_minutes=60)
        assert restored == 0
        assert len(state._sessions["existing"].messages) == 1
        assert state._sessions["existing"].messages[0]["content"] == "already here"

    def test_limits_to_500_messages(self, tmp_path, monkeypatch):
        """Only the last 500 messages are loaded from a session."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        messages = [
            {"role": "user", "content": f"msg {i}", "ts": f"2026-03-23T10:{i:04d}"}
            for i in range(600)
        ]
        _write_session(tmp_path, "dashboard_big", messages)
        path = tmp_path / "dashboard_big.jsonl"
        path.touch()

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60)
        assert restored == 1
        session = state._sessions["big"]
        assert len(session.messages) == 500
        assert session.messages[0]["content"] == "msg 100"
        assert session._disk_older_count == 100  # 600 total - 500 loaded = 100 older on disk

    def test_restores_multiple_sessions(self, tmp_path, monkeypatch):
        """Multiple recent dashboard sessions are all restored."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        for name in ["dashboard_a", "dashboard_b", "dashboard_c"]:
            _write_session(
                tmp_path,
                name,
                [{"role": "user", "content": f"from {name}", "ts": "2026-03-23T10:00:00"}],
            )
            (tmp_path / f"{name}.jsonl").touch()

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60)
        assert restored == 3
        assert "a" in state._sessions
        assert "b" in state._sessions
        assert "c" in state._sessions

    def test_dashboard_underscore_key_derives_correct_session_name(self, tmp_path, monkeypatch):
        """Underscore-format key (dashboard_mychat) derives session name 'mychat'."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_mychat",
            [{"role": "user", "content": "hi", "ts": "2026-03-23T10:00:00"}],
        )
        (tmp_path / "dashboard_mychat.jsonl").touch()

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60)
        assert restored == 1
        assert "mychat" in state._sessions
        assert state._sessions["mychat"].messages[0]["content"] == "hi"

    def test_dashboard_colon_key_derives_correct_session_name(self, tmp_path, monkeypatch):
        """Colon-format key (dashboard:mychat) derives session name 'mychat'.

        list_sessions() returns keys from filenames, but the colon-stripping
        branch in restore_recent_sessions handles keys like 'dashboard:xyz'.
        We mock list_sessions() to return a colon-format key to exercise that path.
        """
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_mychat",
            [{"role": "user", "content": "colon test", "ts": "2026-03-23T10:00:00"}],
        )
        (tmp_path / "dashboard_mychat.jsonl").touch()

        state = _make_state(tmp_path)
        # Patch list_sessions to return the colon-format key
        original_list = state.conversation_log.list_sessions

        def patched_list():
            sessions = original_list()
            for s in sessions:
                if s["key"] == "dashboard_mychat":
                    s["key"] = "dashboard:mychat"
            return sessions

        monkeypatch.setattr(state.conversation_log, "list_sessions", patched_list)

        restored = restore_recent_sessions(state, window_minutes=60)
        assert restored == 1
        assert "mychat" in state._sessions
        assert state._sessions["mychat"].messages[0]["content"] == "colon test"

    def test_redacts_credentials_in_restored_messages(self, tmp_path, monkeypatch):
        """LLM-sourced content is redacted before being added to dashboard sessions."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_redact",
            [
                {"role": "user", "content": "show me the key", "ts": "2026-03-23T10:00:00"},
                {
                    "role": "assistant",
                    "content": "Here: AKIAIOSFODNN7EXAMPLE",
                    "ts": "2026-03-23T10:00:01",
                },
            ],
        )
        (tmp_path / "dashboard_redact.jsonl").touch()

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60)
        assert restored == 1
        session = state._sessions["redact"]
        # User content should be preserved as-is
        assert session.messages[0]["content"] == "show me the key"
        # Assistant content should have the AWS key redacted
        assert "AKIAIOSFODNN7EXAMPLE" not in session.messages[1]["content"]
        assert "[REDACTED" in session.messages[1]["content"]

    def test_zero_window_restores_all_sessions(self, tmp_path, monkeypatch):
        """window_minutes=0 means infinite — restores sessions regardless of age."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_ancient",
            [{"role": "user", "content": "old msg", "ts": "2025-01-01T10:00:00"}],
        )
        # Set mtime to 30 days ago
        path = tmp_path / "dashboard_ancient.jsonl"
        old_time = time.time() - (30 * 24 * 3600)
        os.utime(path, (old_time, old_time))

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=0)
        assert restored == 1
        assert "ancient" in state._sessions

    def test_negative_window_restores_all_sessions(self, tmp_path, monkeypatch):
        """Negative window_minutes is treated the same as 0 (restore all)."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_neg",
            [{"role": "user", "content": "msg", "ts": "2025-01-01T10:00:00"}],
        )
        path = tmp_path / "dashboard_neg.jsonl"
        old_time = time.time() - (30 * 24 * 3600)
        os.utime(path, (old_time, old_time))

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=-5)
        assert restored == 1
        assert "neg" in state._sessions

    def test_removeprefix_preserves_interior_dashboard(self, tmp_path, monkeypatch):
        """Session name 'my_dashboard_session' is not mangled by prefix stripping."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_my_dashboard_session",
            [{"role": "user", "content": "hi", "ts": "2026-03-23T10:00:00"}],
        )
        (tmp_path / "dashboard_my_dashboard_session.jsonl").touch()

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60)
        assert restored == 1
        # The old .replace() would produce "my_session"; removeprefix gives "my_dashboard_session"
        assert "my_dashboard_session" in state._sessions


# ── api_dashboard_config tests ──


class TestDashboardConfigAPI:
    @pytest.mark.asyncio
    async def test_get_defaults(self, tmp_path, monkeypatch):
        """GET returns default config values."""
        monkeypatch.setattr(
            "personalclaw.config.loader.config_path", lambda: tmp_path / "nonexistent.json"
        )
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/api/dashboard/config")
                assert resp.status == 200
                data = await resp.json()
                assert data["restore_sessions"] is False
                assert data["restore_window_minutes"] == 30

    @pytest.mark.asyncio
    async def test_put_updates_config(self, tmp_path, monkeypatch):
        """PUT updates restore settings and persists to disk."""
        cfg_file = tmp_path / "config.json"
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                resp = await client.put(
                    "/api/dashboard/config",
                    json={"restore_sessions": True, "restore_window_minutes": 120},
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["ok"] is True

                # Verify it was persisted
                assert cfg_file.exists()
                saved = json.loads(cfg_file.read_text())
                assert saved["dashboard"]["restore_sessions"] is True
                assert saved["dashboard"]["restore_window_minutes"] == 120

    @pytest.mark.asyncio
    async def test_put_clamps_window_minutes(self, tmp_path, monkeypatch):
        """PUT clamps restore_window_minutes to [0, 1440] range."""
        cfg_file = tmp_path / "config.json"
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                # Too high
                resp = await client.put(
                    "/api/dashboard/config",
                    json={"restore_window_minutes": 9999},
                )
                assert resp.status == 200
                saved = json.loads(cfg_file.read_text())
                assert saved["dashboard"]["restore_window_minutes"] == 1440

                # Negative clamps to 0
                resp = await client.put(
                    "/api/dashboard/config",
                    json={"restore_window_minutes": -5},
                )
                assert resp.status == 200
                saved = json.loads(cfg_file.read_text())
                assert saved["dashboard"]["restore_window_minutes"] == 0

    @pytest.mark.asyncio
    async def test_put_accepts_zero_window(self, tmp_path, monkeypatch):
        """PUT accepts restore_window_minutes=0 (infinite restore)."""
        cfg_file = tmp_path / "config.json"
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                resp = await client.put(
                    "/api/dashboard/config",
                    json={"restore_window_minutes": 0},
                )
                assert resp.status == 200
                saved = json.loads(cfg_file.read_text())
                assert saved["dashboard"]["restore_window_minutes"] == 0

    @pytest.mark.asyncio
    async def test_put_invalid_json(self, tmp_path, monkeypatch):
        """PUT with invalid JSON returns 400."""
        monkeypatch.setattr(
            "personalclaw.config.loader.config_path", lambda: tmp_path / "config.json"
        )
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                resp = await client.put(
                    "/api/dashboard/config",
                    data="not json",
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 400

    @pytest.mark.asyncio
    async def test_put_invalid_window_type(self, tmp_path, monkeypatch):
        """PUT with non-integer restore_window_minutes returns 400."""
        monkeypatch.setattr(
            "personalclaw.config.loader.config_path", lambda: tmp_path / "config.json"
        )
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                resp = await client.put(
                    "/api/dashboard/config",
                    json={"restore_window_minutes": "not a number"},
                )
                assert resp.status == 400
                data = await resp.json()
                assert "integer" in data["error"]

    @pytest.mark.asyncio
    async def test_put_invalid_restore_sessions_type(self, tmp_path, monkeypatch):
        """PUT with non-boolean restore_sessions returns 400."""
        monkeypatch.setattr(
            "personalclaw.config.loader.config_path", lambda: tmp_path / "config.json"
        )
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                resp = await client.put(
                    "/api/dashboard/config",
                    json={"restore_sessions": "false"},
                )
                assert resp.status == 400
                data = await resp.json()
                assert "boolean" in data["error"]

    @pytest.mark.asyncio
    async def test_get_after_put_reflects_changes(self, tmp_path, monkeypatch):
        """GET after PUT returns the updated values."""
        cfg_file = tmp_path / "config.json"
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        with patch("personalclaw.sel.sel") as mock_sel:
            mock_sel.return_value = MagicMock()
            app = _make_config_app(tmp_path)
            async with TestClient(TestServer(app)) as client:
                await client.put(
                    "/api/dashboard/config",
                    json={"restore_sessions": True, "restore_window_minutes": 60},
                )
                resp = await client.get("/api/dashboard/config")
                data = await resp.json()
                assert data["restore_sessions"] is True
                assert data["restore_window_minutes"] == 60


# ── Config loader roundtrip tests ──


class TestConfigRestoreFields:
    def test_defaults_have_restore_fields(self):
        """Default config has restore_sessions=False and restore_window_minutes=30."""
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig()
        assert cfg.dashboard.restore_sessions is False
        assert cfg.dashboard.restore_window_minutes == 30

    def test_load_restore_fields_from_file(self, tmp_path, monkeypatch):
        """Config loader reads dashboard restore fields from JSON."""
        from personalclaw.config.loader import AppConfig

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "dashboard": {
                        "url": "http://localhost:9120",
                        "restore_sessions": True,
                        "restore_window_minutes": 120,
                    }
                }
            )
        )
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)

        cfg = AppConfig.load()
        assert cfg.dashboard.restore_sessions is True
        assert cfg.dashboard.restore_window_minutes == 120
        assert cfg.dashboard.url == "http://localhost:9120"

    def test_to_dict_includes_restore_fields(self):
        """to_dict() serializes restore fields under dashboard key."""
        from personalclaw.config.loader import AppConfig, DashboardConfig

        cfg = AppConfig(dashboard=DashboardConfig(restore_sessions=True, restore_window_minutes=60))
        d = cfg.to_dict()
        assert d["dashboard"]["restore_sessions"] is True
        assert d["dashboard"]["restore_window_minutes"] == 60

    def test_save_and_reload_roundtrip(self, tmp_path, monkeypatch):
        """save() then load() preserves restore fields."""
        from personalclaw.config.loader import AppConfig, DashboardConfig

        cfg_file = tmp_path / ".personalclaw" / "config.json"
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)

        cfg = AppConfig(
            dashboard=DashboardConfig(restore_sessions=True, restore_window_minutes=720)
        )
        cfg.save()

        loaded = AppConfig.load()
        assert loaded.dashboard.restore_sessions is True
        assert loaded.dashboard.restore_window_minutes == 720

    def test_missing_restore_fields_use_defaults(self, tmp_path, monkeypatch):
        """Config without dashboard restore fields falls back to defaults."""
        from personalclaw.config.loader import AppConfig

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"dashboard": {"url": "http://localhost:9120"}}))
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg_file)

        cfg = AppConfig.load()
        assert cfg.dashboard.restore_sessions is False
        assert cfg.dashboard.restore_window_minutes == 30

    def test_restores_foldered_session_regardless_of_age(self, tmp_path, monkeypatch):
        """Sessions with folder_id are restored even when older than the window."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_foldered",
            [{"role": "user", "content": "in folder", "ts": "2026-03-20T10:00:00"}],
            meta={"folder_id": "f1"},
        )
        path = tmp_path / "dashboard_foldered.jsonl"
        old_time = time.time() - 7200
        os.utime(path, (old_time, old_time))

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=30)
        assert restored == 1
        assert "foldered" in state._sessions
        assert state._sessions["foldered"].folder_id == "f1"

    def test_closed_foldered_session_not_restored(self, tmp_path, monkeypatch):
        """Closed sessions are NOT restored even with folder_id — explicit close always wins."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_closedfolder",
            [{"role": "user", "content": "closed but foldered", "ts": "2026-03-23T10:00:00"}],
            meta={"closed": True, "folder_id": "f2"},
        )
        path = tmp_path / "dashboard_closedfolder.jsonl"
        old_time = time.time() - 7200
        os.utime(path, (old_time, old_time))

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60)
        assert restored == 0
        assert "closedfolder" not in state._sessions

    def test_skips_closed_session_without_folder(self, tmp_path, monkeypatch):
        """Closed sessions without folder_id are not restored."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_closednofolder",
            [{"role": "user", "content": "closed no folder", "ts": "2026-03-23T10:00:00"}],
            meta={"closed": True},
        )
        (tmp_path / "dashboard_closednofolder.jsonl").touch()

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60)
        assert restored == 0

    def test_folders_only_skips_non_foldered(self, tmp_path, monkeypatch):
        """folders_only=True skips sessions without folder_id."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_nofolder",
            [{"role": "user", "content": "no folder", "ts": "2026-03-23T10:00:00"}],
        )
        (tmp_path / "dashboard_nofolder.jsonl").touch()

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60, folders_only=True)
        assert restored == 0

    def test_folders_only_restores_foldered(self, tmp_path, monkeypatch):
        """folders_only=True restores sessions with folder_id."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_withfolder",
            [{"role": "user", "content": "has folder", "ts": "2026-03-23T10:00:00"}],
            meta={"folder_id": "f3"},
        )
        (tmp_path / "dashboard_withfolder.jsonl").touch()

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60, folders_only=True)
        assert restored == 1
        assert state._sessions["withfolder"].folder_id == "f3"

    def test_folder_id_persisted_in_flush(self, tmp_path, monkeypatch):
        """folder_id is written to JSONL metadata when session is saved."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("testsession")
        session.folder_id = "f-abc"
        session.append("user", "hello")
        session.drain()

        from personalclaw.dashboard.chat import _save_session_to_history

        _save_session_to_history(state, session)

        path = tmp_path / "dashboard_testsession.jsonl"
        assert path.exists()
        meta = json.loads(path.read_text().split("\n")[0])
        assert meta["folder_id"] == "f-abc"

    def test_restores_pinned_session_regardless_of_age(self, tmp_path, monkeypatch):
        """Pinned sessions are restored even when older than the window."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_pinnedold",
            [{"role": "user", "content": "pinned", "ts": "2026-03-20T10:00:00"}],
            meta={"pinned": True},
        )
        path = tmp_path / "dashboard_pinnedold.jsonl"
        old_time = time.time() - 7200
        os.utime(path, (old_time, old_time))

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=30)
        assert restored == 1
        assert state._sessions["pinnedold"].pinned is True

    def test_folders_only_restores_pinned(self, tmp_path, monkeypatch):
        """folders_only=True also restores pinned sessions."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_pinnedonly",
            [{"role": "user", "content": "pinned", "ts": "2026-03-23T10:00:00"}],
            meta={"pinned": True},
        )
        (tmp_path / "dashboard_pinnedonly.jsonl").touch()

        state = _make_state(tmp_path)
        restored = restore_recent_sessions(state, window_minutes=60, folders_only=True)
        assert restored == 1
        assert state._sessions["pinnedonly"].pinned is True

    def test_pinned_persisted_in_save(self, tmp_path, monkeypatch):
        """pinned is written to JSONL metadata when session is saved."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        state = _make_state(tmp_path)
        session = state.get_or_create_session("pinsession")
        session.pinned = True
        session.append("user", "hello")
        session.drain()

        from personalclaw.dashboard.chat import _save_session_to_history

        _save_session_to_history(state, session)

        path = tmp_path / "dashboard_pinsession.jsonl"
        assert path.exists()
        meta = json.loads(path.read_text().split("\n")[0])
        assert meta["pinned"] is True


# ── _rehydrate_session_from_history tests ──


class TestRehydrateSessionFromHistory:
    """Integration tests for the per-session rehydrate path used by cron→origin
    injection. Unlike restore_recent_sessions (bulk startup), this helper
    rehydrates a single session on demand and must preserve metadata (memory_mode,
    title, agent, messages) so the revived session is not a phantom empty tab."""

    def test_returns_none_when_session_not_on_disk(self, tmp_path, monkeypatch):
        """Returns None for a session name that has no persisted session file.

        The messaging handler relies on this to distinguish "session truly gone
        → fall through to Slack DM" from "session on disk but unloaded → revive"."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.chat import _rehydrate_session_from_history

        state = _make_state(tmp_path)
        assert _rehydrate_session_from_history(state, "missing-session") is None
        assert "missing-session" not in state._sessions

    def test_returns_existing_session_without_reloading(self, tmp_path, monkeypatch):
        """Hot-path: when session is already in memory, return it as-is."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        from personalclaw.dashboard.chat import _rehydrate_session_from_history

        state = _make_state(tmp_path)
        existing = state.get_or_create_session("hot-session")
        existing.title = "Original Title"
        result = _rehydrate_session_from_history(state, "hot-session")
        assert result is existing
        # No double-registration
        assert state._sessions["hot-session"] is existing
        assert existing.title == "Original Title"

    def test_rehydrates_session_with_metadata_and_messages(self, tmp_path, monkeypatch):
        """Rehydrate restores title, agent, model, memory_mode and message history
        from the persisted JSONL — not just an empty shell."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_originchat",
            [
                {"role": "user", "content": "first", "ts": "2026-03-23T10:00:00"},
                {"role": "assistant", "content": "reply", "ts": "2026-03-23T10:00:01"},
            ],
            meta={"title": "Cron Owner Tab", "agent": "general", "model": "claude-opus-4.7"},
        )
        from personalclaw.dashboard.chat import _rehydrate_session_from_history

        state = _make_state(tmp_path)
        # The persisted model is accepted verbatim only when compatible with the
        # active provider; force the compat gate True so we exercise restore wiring.
        with patch(
            "personalclaw.dashboard.chat_persistence._model_matches_provider",
            return_value=True,
        ):
            session = _rehydrate_session_from_history(state, "originchat")
        assert session is not None
        assert session.title == "Cron Owner Tab"
        assert session.agent == "general"
        assert session.model == "claude-opus-4.7"
        # Message history restored — not a phantom empty tab.
        assert len(session.messages) == 2
        assert session.messages[0]["content"] == "first"
        assert session.messages[1]["content"] == "reply"
        # Registered in _sessions so subsequent send_message calls hit the hot path.
        assert state._sessions["originchat"] is session

    def test_rehydrates_incognito_memory_mode(self, tmp_path, monkeypatch):
        """Rehydrated session preserves non-persistent memory_mode from metadata.

        Regression guard for the phantom-session bug: naive get_or_create_session
        would default to memory_mode='persistent', so an incognito cron message
        would leak content to disk."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_incog",
            [{"role": "user", "content": "secret", "ts": "2026-03-23T10:00:00"}],
            meta={"memory_mode": "off", "title": "Private Tab"},
        )
        from personalclaw.dashboard.chat import _rehydrate_session_from_history

        state = _make_state(tmp_path)
        session = _rehydrate_session_from_history(state, "incog")
        assert session is not None
        assert session.memory_mode == "off"
        # Restricted keys marker is set so consolidation respects the mode.
        assert "dashboard:incog" in state._restricted_keys

    def test_rehydrates_folder_and_pin_metadata(self, tmp_path, monkeypatch):
        """Folder, pin, and color metadata are preserved across rehydrate."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_foldered",
            [{"role": "user", "content": "x", "ts": "2026-03-23T10:00:00"}],
            meta={
                "title": "Foldered",
                "folder_id": "work",
                "pinned": True,
                "color_index": 3,
            },
        )
        from personalclaw.dashboard.chat import _rehydrate_session_from_history

        state = _make_state(tmp_path)
        session = _rehydrate_session_from_history(state, "foldered")
        assert session is not None
        assert session.folder_id == "work"
        assert session.pinned is True
        assert session.color_index == 3

    def test_skips_closed_session(self, tmp_path, monkeypatch):
        """Explicitly closed sessions are NOT rehydrated — cron messages fall
        through to Slack DM instead of resurrecting a tab the user closed."""
        monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_closed",
            [{"role": "user", "content": "bye", "ts": "2026-03-23T10:00:00"}],
            meta={"closed": True, "title": "Done"},
        )
        from personalclaw.dashboard.chat import _rehydrate_session_from_history

        state = _make_state(tmp_path)
        assert _rehydrate_session_from_history(state, "closed") is None
        assert "closed" not in state._sessions

    def test_returns_none_when_no_conversation_log(self, tmp_path):
        """Without a conversation_log, rehydrate is a no-op returning None."""
        from personalclaw.dashboard.chat import _rehydrate_session_from_history

        state = _make_state(tmp_path)
        state.conversation_log = None
        assert _rehydrate_session_from_history(state, "anything") is None
