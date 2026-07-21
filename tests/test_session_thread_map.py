"""Tests for SessionManager thread tracking (set_thread / get_thread).

Thread tracking is backed by SessionMap (persisted to disk) via
set_channel_link / get_channel_link. The single-field accessors set_thread /
get_thread (and set_channel / get_channel) read-modify-write the combined link,
preserving the other field.
"""

from unittest.mock import AsyncMock

import pytest

from personalclaw.config import AppConfig
from personalclaw.session import SessionManager


def _mock_provider_factory():
    def factory(session_key=None, agent=None, channel_id=None, **kwargs):
        provider = AsyncMock()
        provider.start = AsyncMock()
        provider.shutdown = AsyncMock()
        provider.context_usage_pct = lambda: 0.0
        return provider

    return factory


@pytest.fixture()
def config():
    return AppConfig()


class TestSetThread:
    """set_thread stores thread_ts via SessionMap."""

    @pytest.mark.asyncio
    async def test_stores_thread_for_existing_session(self, config) -> None:
        session_manager = SessionManager(config, provider_factory=_mock_provider_factory())
        await session_manager.get_or_create("cron:j1")
        await session_manager.set_thread("cron:j1", "1711957800.001234")
        assert session_manager.get_thread("cron:j1") == "1711957800.001234"
        await session_manager.close_all()

    @pytest.mark.asyncio
    async def test_set_thread_without_session_creates_entry(self, config) -> None:
        """set_channel_link creates an entry even without an active session."""
        session_manager = SessionManager(config, provider_factory=_mock_provider_factory())
        await session_manager.set_thread("cron:ghost", "1711957800.999")
        assert session_manager.get_thread("cron:ghost") == "1711957800.999"
        await session_manager.close_all()

    @pytest.mark.asyncio
    async def test_get_thread_returns_none_when_unset(self, config) -> None:
        session_manager = SessionManager(config, provider_factory=_mock_provider_factory())
        await session_manager.get_or_create("cron:j2")
        assert session_manager.get_thread("cron:j2") is None
        await session_manager.close_all()


class TestThreadCleanup:
    """Thread map entries persist across reset/remove (backed by SessionMap)."""

    @pytest.mark.asyncio
    async def test_reset_preserves_thread(self, config) -> None:
        """reset() kills the session but preserves the SessionMap entry."""
        session_manager = SessionManager(config, provider_factory=_mock_provider_factory())
        await session_manager.get_or_create("cron:j1")
        await session_manager.set_thread("cron:j1", "1711957800.001234")
        await session_manager.reset("cron:j1")
        # Thread link persists in SessionMap after reset
        assert session_manager.get_thread("cron:j1") == "1711957800.001234"
        await session_manager.close_all()

    @pytest.mark.asyncio
    async def test_remove_preserves_thread(self, config) -> None:
        """remove() preserves the SessionMap entry for future resume."""
        session_manager = SessionManager(config, provider_factory=_mock_provider_factory())
        await session_manager.get_or_create("cron:j1")
        await session_manager.set_thread("cron:j1", "1711957800.001234")
        await session_manager.remove("cron:j1")
        assert session_manager.get_thread("cron:j1") == "1711957800.001234"
        await session_manager.close_all()

    @pytest.mark.asyncio
    async def test_destroy_clears_thread(self, config) -> None:
        """destroy() deletes the SessionMap entry permanently."""
        session_manager = SessionManager(config, provider_factory=_mock_provider_factory())
        await session_manager.get_or_create("cron:j1")
        await session_manager.set_thread("cron:j1", "1711957800.001234")
        await session_manager.destroy("cron:j1")
        assert session_manager.get_thread("cron:j1") is None
        await session_manager.close_all()

    @pytest.mark.asyncio
    async def test_close_all_preserves_threads(self, config) -> None:
        """close_all() saves session mappings — thread links persist."""
        session_manager = SessionManager(config, provider_factory=_mock_provider_factory())
        await session_manager.get_or_create("cron:j1")
        await session_manager.set_thread("cron:j1", "1711957800.001234")
        await session_manager.close_all()
        # Thread link persists in SessionMap after close_all
        assert session_manager.get_thread("cron:j1") == "1711957800.001234"


class TestChannelLink:
    """Direct set_channel_link / get_channel_link / get_session_for_thread tests."""

    @pytest.mark.asyncio
    async def test_set_and_get_channel_link(self, config) -> None:
        sm = SessionManager(config, provider_factory=_mock_provider_factory())
        sm.set_channel_link("dashboard:chat-1", "1711957800.001", "C123")
        ts, ch = sm.get_channel_link("dashboard:chat-1")
        assert ts == "1711957800.001"
        assert ch == "C123"
        await sm.close_all()

    @pytest.mark.asyncio
    async def test_get_session_for_thread(self, config) -> None:
        sm = SessionManager(config, provider_factory=_mock_provider_factory())
        sm.set_channel_link("dashboard:chat-1", "1711957800.001", "C123")
        assert sm.get_session_for_thread("1711957800.001") == "dashboard:chat-1"
        assert sm.get_session_for_thread("nonexistent") is None
        await sm.close_all()


class TestSessionMapLoad:
    """Tests for SessionMap on-disk load: only dict entries with a ``sid`` are kept."""

    @pytest.mark.asyncio
    async def test_load_skips_non_dict_entries(self, config, tmp_path, monkeypatch) -> None:
        """Plain-string and other non-dict values are dropped as corrupt."""
        import json

        monkeypatch.setattr("personalclaw.session_map.config_dir", lambda: tmp_path)
        map_path = tmp_path / "session_map.json"
        map_path.write_text(json.dumps({"str-entry": "sid-abc", "good": {"sid": "s1"}}))
        sm = SessionManager(config, provider_factory=_mock_provider_factory())
        # The string entry is not a valid dict mapping → no link resolves for it.
        ts, ch = sm.get_channel_link("str-entry")
        assert ts is None
        assert ch is None
        await sm.close_all()

    @pytest.mark.asyncio
    async def test_load_skips_corrupt_entries(self, config, tmp_path, monkeypatch) -> None:
        import json

        monkeypatch.setattr("personalclaw.session_map.config_dir", lambda: tmp_path)
        map_path = tmp_path / "session_map.json"
        map_path.write_text(json.dumps({"good": {"sid": "s1"}, "bad": [1, 2, 3]}))
        sm = SessionManager(config, provider_factory=_mock_provider_factory())
        # Good entry loaded, bad entry skipped
        ts, ch = sm.get_channel_link("good")
        assert ts is None  # no channel link set
        ts2, ch2 = sm.get_channel_link("bad")
        assert ts2 is None
        await sm.close_all()


class TestSetChannelLinkUpdate:
    """Tests for set_channel_link updating an existing entry (lines 572-576)."""

    @pytest.mark.asyncio
    async def test_update_existing_link(self, config, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("personalclaw.session_map.config_dir", lambda: tmp_path)
        sm = SessionManager(config, provider_factory=_mock_provider_factory())
        sm.set_channel_link("k1", "ts-old", "C-old")
        sm.set_channel_link("k1", "ts-new", "C-new")
        ts, ch = sm.get_channel_link("k1")
        assert ts == "ts-new"
        assert ch == "C-new"
        # Old thread should be removed from reverse index
        assert sm.get_session_for_thread("ts-old") is None
        assert sm.get_session_for_thread("ts-new") == "k1"
        await sm.close_all()

    @pytest.mark.asyncio
    async def test_idempotent_set_same_link(self, config, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("personalclaw.session_map.config_dir", lambda: tmp_path)
        sm = SessionManager(config, provider_factory=_mock_provider_factory())
        sm.set_channel_link("k1", "ts-1", "C-1")
        sm.set_channel_link("k1", "ts-1", "C-1")  # same values — no-op
        ts, ch = sm.get_channel_link("k1")
        assert ts == "ts-1"
        assert ch == "C-1"
        await sm.close_all()


class TestChannelAccessors:
    """set_channel/get_channel single-field accessors over the combined channel link."""

    @pytest.mark.asyncio
    async def test_set_channel_updates_existing_link(self, config, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("personalclaw.session_map.config_dir", lambda: tmp_path)
        sm = SessionManager(config, provider_factory=_mock_provider_factory())
        sm.set_channel_link("k1", "ts-1", "C-old")
        await sm.set_channel("k1", "C-new")
        assert sm.get_channel("k1") == "C-new"
        # Thread should be preserved
        assert sm.get_thread("k1") == "ts-1"
        await sm.close_all()

    @pytest.mark.asyncio
    async def test_set_channel_stores_without_thread(self, config, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("personalclaw.session_map.config_dir", lambda: tmp_path)
        sm = SessionManager(config, provider_factory=_mock_provider_factory())
        await sm.set_channel("k1", "C-1")
        # set_channel now stores unconditionally (mirrors set_thread behavior)
        assert sm.get_channel("k1") == "C-1"
        await sm.close_all()

    @pytest.mark.asyncio
    async def test_get_channel_returns_none_for_unknown(
        self, config, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr("personalclaw.session_map.config_dir", lambda: tmp_path)
        sm = SessionManager(config, provider_factory=_mock_provider_factory())
        assert sm.get_channel("nonexistent") is None
        await sm.close_all()
