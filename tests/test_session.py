"""Tests for session manager."""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, patch

import pytest

from personalclaw.config import AppConfig
from personalclaw.session import BACKGROUND_KEY, SessionManager


@pytest.fixture
def cfg():
    c = AppConfig()
    c.session.timeout_secs = 2  # short for testing
    return c


def _mock_provider_factory():
    """Return a factory that creates mock ModelProviders."""

    def factory(session_key=None, agent=None, channel_id=None, **kwargs):
        m = AsyncMock()
        m.start = AsyncMock()
        m.shutdown = AsyncMock()
        m.context_usage_pct = lambda: 0.0
        return m

    return factory


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_creates_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, is_new, _resumed = await mgr.get_or_create("thread1")

        assert is_new is True
        assert mgr.count == 1
        provider.start.assert_awaited_once()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_reuses_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        p1, new1, _ = await mgr.get_or_create("thread1")
        mgr.release("thread1")
        p2, new2, _ = await mgr.get_or_create("thread1")
        mgr.release("thread1")

        assert p1 is p2
        assert new1 is True
        assert new2 is False
        assert mgr.count == 1
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_separate_sessions_per_key(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("t1")
        await mgr.get_or_create("t2")

        assert mgr.count == 2
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_remove_shuts_down_client(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("thread1")
        await mgr.remove("thread1")

        assert mgr.count == 0
        provider.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_all(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("t1")
        mgr.release("t1")
        await mgr.get_or_create("t1")  # same key
        mgr.release("t1")
        await mgr.close_all()

        assert mgr.count == 0

    @pytest.mark.asyncio
    async def test_reset_removes_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("thread1")
        await mgr.reset("thread1")

        assert mgr.count == 0
        provider.shutdown.assert_awaited_once()


class TestWarmPool:
    """Tests for warm session pool and background session."""

    @pytest.mark.asyncio
    async def test_start_pool_creates_background(self, cfg):
        """start_pool() creates background session."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()

        assert BACKGROUND_KEY in mgr._sessions
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_cold_start_for_new_session(self, cfg):
        """get_or_create cold-starts a new session."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()

        provider, is_new, _resumed = await mgr.get_or_create("dashboard:chat-1")
        assert is_new is True
        assert provider is not None
        provider.start.assert_awaited_once()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_background_session_reused(self, cfg):
        """BACKGROUND_KEY returns the same provider on repeated calls."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()

        p1, _, _ = await mgr.get_or_create(BACKGROUND_KEY)
        mgr.release(BACKGROUND_KEY)
        p2, _, _ = await mgr.get_or_create(BACKGROUND_KEY)
        mgr.release(BACKGROUND_KEY)

        assert p1 is p2
        p1.start.assert_awaited_once()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_background_session_not_expired(self, cfg):
        """Background session is never expired by idle cleanup."""
        import time

        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()

        mgr._sessions[BACKGROUND_KEY].last_used = time.monotonic() - 9999
        await mgr._expire_idle(1)

        assert BACKGROUND_KEY in mgr._sessions
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_channel_session_not_expired_by_idle(self, cfg):
        """Channel-agent sessions survive idle expiry (managed by channel lifecycle)."""
        import time

        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()

        key = "channel:abc123:agent1"
        mgr._sessions[key] = mgr._sessions[BACKGROUND_KEY].__class__.__new__(mgr._sessions[BACKGROUND_KEY].__class__)
        mgr._sessions[key].__dict__.update(mgr._sessions[BACKGROUND_KEY].__dict__)
        mgr._sessions[key].last_used = time.monotonic() - 9999

        await mgr._expire_idle(1)

        assert key in mgr._sessions
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_close_all_shuts_down_sessions(self, cfg):
        """close_all() shuts down all active sessions."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()
        await mgr.get_or_create("chat-1")

        await mgr.close_all()
        assert mgr.count == 0

    @pytest.mark.asyncio
    async def test_start_pool_idempotent(self, cfg):
        """Calling start_pool() twice is a no-op."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()

        await mgr.start_pool()  # should be no-op
        assert BACKGROUND_KEY in mgr._sessions
        await mgr.close_all()


class TestRecycleBackground:
    """Tests for background session context overflow recycling."""

    @pytest.mark.asyncio
    async def test_recycle_on_high_context(self, cfg):
        """Background session is recycled when context >= 70%."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()

        old_provider = mgr._sessions[BACKGROUND_KEY].provider
        # Simulate high context
        old_provider.context_usage_pct = lambda: 75.0

        await mgr.recycle_background()

        # Old provider should have been shut down
        old_provider.shutdown.assert_awaited_once()
        # New session should exist
        assert BACKGROUND_KEY in mgr._sessions
        new_provider = mgr._sessions[BACKGROUND_KEY].provider
        assert new_provider is not old_provider
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_recycle_blind_fallback(self, cfg):
        """Background session is recycled after 40 prompts with no metadata."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()

        old_provider = mgr._sessions[BACKGROUND_KEY].provider
        old_provider.context_usage_pct = lambda: 0.0  # no metadata
        mgr._sessions[BACKGROUND_KEY].prompt_count = 45

        await mgr.recycle_background()

        old_provider.shutdown.assert_awaited_once()
        assert BACKGROUND_KEY in mgr._sessions
        assert mgr._sessions[BACKGROUND_KEY].provider is not old_provider
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_no_recycle_when_low_context(self, cfg):
        """Background session is NOT recycled when context is low."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()

        old_provider = mgr._sessions[BACKGROUND_KEY].provider
        old_provider.context_usage_pct = lambda: 30.0

        await mgr.recycle_background()

        # Should NOT have been shut down
        old_provider.shutdown.assert_not_awaited()
        assert mgr._sessions[BACKGROUND_KEY].provider is old_provider
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_recycle_no_background_session(self, cfg):
        """recycle_background() is no-op when no background session exists."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        # Don't start pool — no background session
        await mgr.recycle_background()  # should not raise
        await mgr.close_all()


class TestCancelRaceCondition:
    """Tests for process leak prevention when CancelledError fires during get_or_create."""

    @pytest.mark.asyncio
    async def test_cancel_during_start_kills_provider(self, cfg):
        """CancelledError during provider.start() kills the process synchronously."""
        mock_provider = AsyncMock()
        mock_provider.start = AsyncMock(side_effect=asyncio.CancelledError)
        mock_provider._client = AsyncMock()
        mock_provider._client._pid = 99999

        def factory(session_key=None, agent=None, channel_id=None, **kwargs):
            return mock_provider

        mgr = SessionManager(cfg, provider_factory=factory)

        import personalclaw.session as _sess_mod

        with patch.object(_sess_mod, "_sync_kill_provider") as mock_kill:
            with pytest.raises(asyncio.CancelledError):
                await mgr.get_or_create("test-cancel")

            mock_kill.assert_called_once_with(mock_provider)

        # Session must NOT be registered
        assert mgr.count == 0
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_cancel_after_start_before_registration_kills_provider(self, cfg):
        """CancelledError after start() but before _sessions[key] kills the process."""
        mock_provider = AsyncMock()
        mock_provider.start = AsyncMock()  # succeeds
        mock_provider.context_usage_pct = lambda: 0.0
        mock_provider._client = AsyncMock()
        mock_provider._client._pid = 88888
        mock_provider.is_alive.return_value = True

        def factory(session_key=None, agent=None, channel_id=None, **kwargs):
            return mock_provider

        mgr = SessionManager(cfg, provider_factory=factory)
        original_lock = mgr._lock

        class CancelOnSecondLock:
            """First acquire (fast path) passes through; second (registration) cancels."""

            def __init__(self):
                self._calls = 0

            async def __aenter__(self):
                self._calls += 1
                if self._calls >= 2:
                    raise asyncio.CancelledError
                return await original_lock.__aenter__()

            async def __aexit__(self, *a):
                if self._calls < 2:
                    return await original_lock.__aexit__(*a)

        import personalclaw.session as _sess_mod

        with patch.object(_sess_mod, "_sync_kill_provider") as mock_kill:
            mgr._lock = CancelOnSecondLock()
            with pytest.raises(asyncio.CancelledError):
                await mgr.get_or_create("test-cancel-2")

            mock_kill.assert_called_once_with(mock_provider)

        mgr._lock = original_lock
        assert mgr.count == 0
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_normal_path_unaffected(self, cfg):
        """Normal get_or_create still works after the cancel-safety changes."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, is_new, _ = await mgr.get_or_create("normal-session")

        assert is_new is True
        assert mgr.count == 1
        provider.start.assert_awaited_once()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_model_forwarded_to_factory(self, cfg):
        """model param is forwarded to factory as model_override."""
        captured = {}

        def factory(session_key=None, agent=None, channel_id=None, **kwargs):
            captured.update(kwargs)
            m = AsyncMock()
            m.start = AsyncMock()
            m.context_usage_pct = lambda: 0.0
            m.is_alive.return_value = True
            return m

        mgr = SessionManager(cfg, provider_factory=factory)
        await mgr.get_or_create("test-model", model="claude-sonnet")
        assert captured["model_override"] == "claude-sonnet"
        await mgr.close_all()


class TestDeadProviderCleanup:
    """Tests for orphaned child process cleanup when a dead provider is detected."""

    @staticmethod
    def _make_provider(*, alive: bool = True):
        """Create a mock provider with sync is_alive."""
        from unittest.mock import MagicMock
        m = AsyncMock()
        m.start = AsyncMock()
        m.shutdown = AsyncMock()
        m.context_usage_pct = MagicMock(return_value=0.0)
        m.is_alive = MagicMock(return_value=alive)
        m.is_process_alive = MagicMock(return_value=alive)
        return m

    @pytest.mark.asyncio
    async def test_dead_provider_calls_shutdown(self, cfg):
        """When is_alive() returns False, shutdown() is called on the stale provider."""
        dead_provider = self._make_provider(alive=True)
        call_count = 0

        def factory(session_key=None, agent=None, channel_id=None, **kwargs):
            nonlocal call_count
            call_count += 1
            return dead_provider if call_count == 1 else self._make_provider()

        mgr = SessionManager(cfg, provider_factory=factory)
        await mgr.get_or_create("sess1")
        mgr.release("sess1")

        dead_provider.is_alive.return_value = False
        dead_provider.is_process_alive.return_value = False
        _, is_new, _ = await mgr.get_or_create("sess1")
        assert is_new is True
        dead_provider.shutdown.assert_awaited_once()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_dead_provider_shutdown_exception_does_not_propagate(self, cfg):
        """If shutdown() raises on a dead provider, get_or_create still succeeds."""
        dead_provider = self._make_provider(alive=True)
        dead_provider.shutdown = AsyncMock(side_effect=OSError("kill failed"))
        call_count = 0

        def factory(session_key=None, agent=None, channel_id=None, **kwargs):
            nonlocal call_count
            call_count += 1
            return dead_provider if call_count == 1 else self._make_provider()

        mgr = SessionManager(cfg, provider_factory=factory)
        await mgr.get_or_create("sess1")
        mgr.release("sess1")

        dead_provider.is_alive.return_value = False
        dead_provider.is_process_alive.return_value = False
        _, is_new, _ = await mgr.get_or_create("sess1")
        assert is_new is True
        assert mgr.count == 1
        dead_provider.shutdown.assert_awaited_once()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_dead_provider_removed_from_sessions(self, cfg):
        """Dead provider session is removed and replaced by a fresh one."""
        dead_provider = self._make_provider(alive=True)
        fresh_provider = self._make_provider()
        call_count = 0

        def factory(session_key=None, agent=None, channel_id=None, **kwargs):
            nonlocal call_count
            call_count += 1
            return dead_provider if call_count == 1 else fresh_provider

        mgr = SessionManager(cfg, provider_factory=factory)
        await mgr.get_or_create("sess1")
        mgr.release("sess1")

        dead_provider.is_alive.return_value = False
        dead_provider.is_process_alive.return_value = False
        provider, is_new, _ = await mgr.get_or_create("sess1")
        assert provider is fresh_provider
        assert is_new is True
        assert mgr.count == 1
        await mgr.close_all()


class TestIsProviderAlive:
    """Tests for is_provider_alive preferring is_process_alive over is_alive."""

    @pytest.mark.asyncio
    async def test_uses_is_process_alive_when_available(self, cfg):
        provider = TestDeadProviderCleanup._make_provider(alive=True)
        provider.is_process_alive.return_value = True
        mgr = SessionManager(cfg, provider_factory=lambda *a, **kw: provider)
        await mgr.get_or_create("sess1")
        mgr.release("sess1")
        result = await mgr.is_provider_alive("sess1")
        assert result is True
        provider.is_process_alive.assert_called()
        await mgr.close_all()


class TestApprovalPolicy:
    """Tests for approval policy get/set on sessions."""

    @pytest.mark.asyncio
    async def test_set_and_get_approval_policy(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("thread1")
        mgr.release("thread1")

        mgr.set_approval_policy("thread1", "auto")
        assert mgr.get_approval_policy("thread1") == "auto"

        mgr.set_approval_policy("thread1", "")
        assert mgr.get_approval_policy("thread1") == ""
        await mgr.close_all()

    def test_get_approval_policy_missing_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert mgr.get_approval_policy("nonexistent") == ""

    def test_set_approval_policy_missing_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr.set_approval_policy("nonexistent", "auto")  # should not raise

    @pytest.mark.asyncio
    async def test_approval_policy_propagated_on_create(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("thread1", approval_policy="auto")
        mgr.release("thread1")
        assert mgr.get_approval_policy("thread1") == "auto"
        await mgr.close_all()


class TestGetAgent:
    """Tests for get_agent() on SessionManager."""

    @pytest.mark.asyncio
    async def test_get_agent_returns_agent_name(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("thread1", agent="my-agent")
        mgr.release("thread1")
        assert mgr.get_agent("thread1") == "my-agent"
        await mgr.close_all()

    def test_get_agent_missing_session_returns_empty(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert mgr.get_agent("nonexistent") == ""

    @pytest.mark.asyncio
    async def test_get_agent_no_agent_returns_empty(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("thread1")
        mgr.release("thread1")
        assert mgr.get_agent("thread1") == ""
        await mgr.close_all()


class TestOrphanedDashboardSessions:
    """Tests for orphaned dashboard session detection in _expire_idle."""

    @pytest.mark.asyncio
    async def test_expire_idle_reaps_orphaned_dashboard_session(self, cfg):
        """Dashboard session whose session no longer exists is reaped immediately."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:tab1")
        mgr.release("dashboard:tab1")
        # Mark tab2 as the only active session — tab1 is orphaned
        mgr.set_active_dashboard_sessions({"dashboard:tab2"})
        await mgr._expire_idle(9999)  # high timeout so idle doesn't trigger

        assert "dashboard:tab1" not in mgr._sessions
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_expire_idle_skips_uninitialized_sessions(self, cfg):
        """When _active_dashboard_sessions is None, no orphan reaping occurs."""
        import time

        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:tab1")
        mgr.release("dashboard:tab1")
        # Don't call set_active_dashboard_sessions — stays None
        mgr._sessions["dashboard:tab1"].last_used = time.monotonic()
        await mgr._expire_idle(9999)

        assert "dashboard:tab1" in mgr._sessions
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_expire_idle_preserves_active_dashboard_session(self, cfg):
        """Dashboard session whose session still exists is NOT reaped."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:tab1")
        mgr.release("dashboard:tab1")
        mgr.set_active_dashboard_sessions({"dashboard:tab1"})
        await mgr._expire_idle(9999)

        assert "dashboard:tab1" in mgr._sessions
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_campaign_worker_not_orphan_reaped(self, cfg):
        """Headless campaign worker sessions are exempt from orphan reaping — a
        long cycle (e.g. spawning subagents) must not be killed mid-turn just
        because no UI tab is open for it. The watchdog ends them instead."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:loop-abc123")
        mgr.release("dashboard:loop-abc123")
        # Only a chat tab is active; the campaign worker is NOT in the set.
        mgr.set_active_dashboard_sessions({"dashboard:tab1"})
        await mgr._expire_idle(9999)  # high timeout → only orphan reaping fires

        assert "dashboard:loop-abc123" in mgr._sessions
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_campaign_worker_not_idle_reaped(self, cfg):
        """GoalLoop workers are also exempt from plain idle expiry (a cycle can
        idle between turns far longer than the chat idle window)."""
        import time

        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:loop-abc123")
        mgr.release("dashboard:loop-abc123")
        mgr._sessions["dashboard:loop-abc123"].last_used = time.monotonic() - 10_000
        await mgr._expire_idle(0)  # 0s timeout → everything else is idle

        assert "dashboard:loop-abc123" in mgr._sessions
        await mgr.close_all()


class TestSessionExpireCallback:
    """E11-P2: the on_session_expire seam fires for genuinely-idle sessions
    (so skills get a final extraction pass) but not for orphaned tab-closed
    dashboard sessions (the idle poll already covers those)."""

    @pytest.mark.asyncio
    async def test_callback_fires_on_idle_expire(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("key1")
        mgr.release("key1")
        seen = AsyncMock()
        mgr.set_session_expire_callback(seen)

        await mgr._expire_idle(0)  # 0s timeout → instantly idle

        seen.assert_awaited_once_with("key1")
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_callback_skipped_for_orphan(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:tab1")
        mgr.release("dashboard:tab1")
        mgr.set_active_dashboard_sessions({"dashboard:tab2"})  # tab1 orphaned
        seen = AsyncMock()
        mgr.set_session_expire_callback(seen)

        await mgr._expire_idle(9999)  # high timeout → only orphan reaping fires

        seen.assert_not_awaited()
        assert "dashboard:tab1" not in mgr._sessions
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_callback_failure_does_not_block_cleanup(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("key1")
        mgr.release("key1")
        mgr.set_session_expire_callback(AsyncMock(side_effect=RuntimeError("boom")))

        await mgr._expire_idle(0)  # must not raise

        assert not mgr.has_session("key1")  # reset still happened
        await mgr.close_all()


class TestStopTurn:
    """Tests for stop_turn(), _eager_respawn(), and cancel_current backcompat."""

    @pytest.mark.asyncio
    async def test_stop_turn_idle_no_session(self, cfg):
        """No session for key → returns 'idle'."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        result = await mgr.stop_turn("nonexistent")
        assert result == "idle"
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_stop_turn_soft_ack(self, cfg):
        """Provider returns 'acked' → stop_turn returns 'soft', on_soft called."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("key1")
        mgr.release("key1")

        provider.cancel = AsyncMock(return_value="acked")
        on_soft = AsyncMock()
        on_hard = AsyncMock()

        result = await mgr.stop_turn("key1", on_soft=on_soft, on_hard=on_hard)

        assert result == "soft"
        on_soft.assert_awaited_once()
        on_hard.assert_not_awaited()
        # Session should still exist (not reset)
        assert mgr.has_session("key1")
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_stop_turn_hard_on_timeout(self, cfg):
        """Provider returns 'timeout' → stop_turn returns 'hard', on_hard called."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("key1")
        mgr.release("key1")

        provider.cancel = AsyncMock(return_value="timeout")
        on_soft = AsyncMock()
        on_hard = AsyncMock()

        result = await mgr.stop_turn("key1", on_soft=on_soft, on_hard=on_hard)

        assert result == "hard"
        on_soft.assert_not_awaited()
        on_hard.assert_awaited_once()
        # Session should have been reset
        assert not mgr.has_session("key1")
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_stop_turn_hard_on_error(self, cfg):
        """Provider returns 'error' → stop_turn returns 'hard'."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("key1")
        mgr.release("key1")

        provider.cancel = AsyncMock(return_value="error")
        on_hard = AsyncMock()

        result = await mgr.stop_turn("key1", on_hard=on_hard)

        assert result == "hard"
        on_hard.assert_awaited_once()
        assert not mgr.has_session("key1")
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_stop_turn_force_skips_cancel(self, cfg):
        """force=True goes straight to reset without calling provider.cancel."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("key1")
        mgr.release("key1")

        provider.cancel = AsyncMock(return_value="acked")
        on_hard = AsyncMock()

        result = await mgr.stop_turn("key1", force=True, on_hard=on_hard)

        assert result == "hard"
        provider.cancel.assert_not_awaited()
        on_hard.assert_awaited_once()
        assert not mgr.has_session("key1")
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_stop_turn_clears_queue_first(self, cfg):
        """stop_turn clears the message queue regardless of outcome."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("key1")
        mgr.release("key1")

        # Populate queue
        mgr.enqueue("key1", "ts1", "msg1", force=True)
        mgr.enqueue("key1", "ts2", "msg2", force=True)

        provider.cancel = AsyncMock(return_value="acked")
        await mgr.stop_turn("key1")

        # Queue should be empty
        assert mgr.dequeue("key1") is None
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_stop_turn_idle_still_clears_queue(self, cfg):
        """Even when provider returns 'no_turn', queue is cleared."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("key1")
        mgr.release("key1")

        mgr.enqueue("key1", "ts1", "msg1", force=True)

        provider.cancel = AsyncMock(return_value="no_turn")
        result = await mgr.stop_turn("key1")

        assert result == "idle"
        assert mgr.dequeue("key1") is None
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_eager_respawn_called(self, cfg):
        """Hard path schedules _eager_respawn via asyncio.create_task."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("key1")
        mgr.release("key1")

        provider.cancel = AsyncMock(return_value="timeout")

        with patch.object(mgr, "_eager_respawn", new_callable=AsyncMock) as mock_respawn:
            await mgr.stop_turn("key1")
            # Allow the created task to run
            await asyncio.sleep(0)
            mock_respawn.assert_awaited_once_with("key1")

        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_eager_respawn_failure_logged(self, cfg, caplog):
        """_eager_respawn swallows exceptions and logs at debug."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())

        with patch.object(
            mgr, "get_or_create", new_callable=AsyncMock, side_effect=RuntimeError("boom")
        ):
            with caplog.at_level(logging.DEBUG, logger="personalclaw.session"):
                await mgr._eager_respawn("key1")

        assert "Eager respawn failed" in caplog.text
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_eager_respawn_releases_semaphore(self, cfg):
        """_eager_respawn must release the semaphore acquired by get_or_create,
        else the next user message deadlocks waiting on it."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        # Prime the session so get_or_create takes the fast path.
        provider, _, _ = await mgr.get_or_create("key1")
        mgr.release("key1")
        sess = mgr._sessions["key1"]
        # Sanity: semaphore is full (1 permit available) before respawn.
        assert sess.semaphore.locked() is False

        await mgr._eager_respawn("key1")

        # After respawn the semaphore MUST be released, otherwise the next
        # caller of get_or_create would hang on sess.semaphore.acquire().
        assert sess.semaphore.locked() is False
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_cancel_current_backcompat_default(self, cfg):
        """Existing cancel_current(key) call with no kwargs still works."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("key1")
        mgr.release("key1")

        provider.cancel = AsyncMock(return_value="no_turn")
        result = await mgr.cancel_current("key1")

        assert result == "no_turn"
        provider.cancel.assert_awaited_once_with(wait_ack_timeout=0.0)
        await mgr.close_all()


class TestCompactCallback:
    """Tests for the compact callback wiring on SessionManager.

    Covers set_compact_callback registration, pct threading through
    check_context_usage -> _trigger_compaction -> _compact_session, and
    callback fault isolation.
    """

    @pytest.mark.asyncio
    async def test_set_compact_callback_registers_handler(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        cb = AsyncMock()

        mgr.set_compact_callback(cb)

        assert mgr._on_compacted is cb
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_set_compact_callback_none_clears(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr.set_compact_callback(AsyncMock())

        mgr.set_compact_callback(None)

        assert mgr._on_compacted is None
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_set_compact_callback_warns_on_replace(self, cfg, caplog):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr.set_compact_callback(AsyncMock())

        with caplog.at_level(logging.WARNING, logger="personalclaw.session"):
            mgr.set_compact_callback(AsyncMock())

        assert any(
            "Compact callback already registered" in r.message for r in caplog.records
        )
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_compact_session_invokes_callback_with_key_and_pct(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:chat-1")
        cb = AsyncMock()
        mgr.set_compact_callback(cb)

        await mgr._compact_session("dashboard:chat-1", 92.0)

        cb.assert_awaited_once_with("dashboard:chat-1", 92.0)
        assert "dashboard:chat-1" not in mgr._sessions
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_compact_session_skips_callback_when_session_absent(self, cfg):
        """No session means no recycle happened, so the callback must not fire."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        cb = AsyncMock()
        mgr.set_compact_callback(cb)

        await mgr._compact_session("dashboard:missing", 91.0)

        cb.assert_not_awaited()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_compact_session_callback_exception_is_logged(self, cfg, caplog):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:chat-1")
        cb = AsyncMock(side_effect=RuntimeError("boom"))
        mgr.set_compact_callback(cb)

        with caplog.at_level(logging.ERROR, logger="personalclaw.session"):
            await mgr._compact_session("dashboard:chat-1", 95.0)

        cb.assert_awaited_once()
        assert any(
            "Compact callback failed" in r.message for r in caplog.records
        )
        # Session still recycled, compacting flag cleared
        assert "dashboard:chat-1" not in mgr._sessions
        assert "dashboard:chat-1" not in mgr._compacting
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_trigger_compaction_threads_pct_through(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:chat-2")
        captured: list[tuple[str, float]] = []

        async def cb(key, pct):
            captured.append((key, pct))

        mgr.set_compact_callback(cb)

        mgr._trigger_compaction("dashboard:chat-2", "context at 92%", 92.0)
        # _trigger_compaction schedules the work as a background task
        await asyncio.gather(*mgr._background_tasks, return_exceptions=True)

        assert captured == [("dashboard:chat-2", 92.0)]
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_check_context_usage_fires_callback_with_observed_pct(self, cfg):
        """High pct should flow from check_context_usage through to the callback."""
        cfg.session.autocompact_pct = 90.0
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("dashboard:chat-3")
        provider.context_usage_pct = lambda: 93.0
        captured: list[tuple[str, float]] = []

        async def cb(key, pct):
            captured.append((key, pct))

        mgr.set_compact_callback(cb)

        pct = mgr.check_context_usage("dashboard:chat-3", provider)
        await asyncio.gather(*mgr._background_tasks, return_exceptions=True)

        assert pct == 93.0
        assert captured == [("dashboard:chat-3", 93.0)]
        await mgr.close_all()


class TestRecordSuccessFailure:
    """Tests for record_success and record_failure circuit breaker."""

    @pytest.mark.asyncio
    async def test_get_provider_returns_provider(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        assert mgr.get_provider("k1") is provider
        await mgr.close_all()

    def test_get_provider_missing_returns_none(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert mgr.get_provider("nonexistent") is None

    @pytest.mark.asyncio
    async def test_pool_size_clamping(self, cfg, caplog):
        cfg.session.pool_size = 999
        with caplog.at_level(logging.WARNING, logger="personalclaw.session"):
            mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert any("exceeds max" in r.message for r in caplog.records)
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_record_success_resets_counter(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.release("k1")
        mgr._sessions["k1"].consecutive_failures = 3
        mgr.record_success("k1")
        assert mgr._sessions["k1"].consecutive_failures == 0
        await mgr.close_all()

    def test_record_success_missing_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr.record_success("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_record_failure_increments(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.release("k1")
        tripped = await mgr.record_failure("k1")
        assert tripped is False
        assert mgr._sessions["k1"].consecutive_failures == 1
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_record_failure_trips_circuit_breaker(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.release("k1")
        mgr._sessions["k1"].consecutive_failures = 4  # one below threshold
        tripped = await mgr.record_failure("k1")
        assert tripped is True
        assert not mgr.has_session("k1")
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_record_failure_missing_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        tripped = await mgr.record_failure("nonexistent")
        assert tripped is False


class TestMessageQueue:
    """Tests for enqueue, dequeue, cancel_queued, is_cancelled."""

    @pytest.mark.asyncio
    async def test_enqueue_when_busy(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        # semaphore is locked (acquired by get_or_create)
        queued = mgr.enqueue("k1", "ts1", "hello")
        assert queued is True
        mgr.release("k1")
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_enqueue_when_idle_returns_false(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.release("k1")
        queued = mgr.enqueue("k1", "ts1", "hello")
        assert queued is False
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_enqueue_force(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.release("k1")
        queued = mgr.enqueue("k1", "ts1", "hello", force=True)
        assert queued is True
        await mgr.close_all()

    def test_enqueue_missing_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert mgr.enqueue("nope", "ts1", "hi") is False

    @pytest.mark.asyncio
    async def test_dequeue_fifo(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.enqueue("k1", "ts1", "first")
        mgr.enqueue("k1", "ts2", "second")
        mgr.release("k1")
        result = mgr.dequeue("k1")
        assert result == ("ts1", "first", {})
        result = mgr.dequeue("k1")
        assert result == ("ts2", "second", {})
        assert mgr.dequeue("k1") is None
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_dequeue_skips_cancelled(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.enqueue("k1", "ts1", "first")
        mgr.enqueue("k1", "ts2", "second")
        mgr._sessions["k1"].cancelled.add("ts1")
        mgr.release("k1")
        result = mgr.dequeue("k1")
        assert result == ("ts2", "second", {})
        await mgr.close_all()

    def test_dequeue_missing_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert mgr.dequeue("nope") is None

    @pytest.mark.asyncio
    async def test_cancel_queued_removes_from_queue(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.enqueue("k1", "ts1", "msg1")
        mgr.enqueue("k1", "ts2", "msg2")
        mgr.release("k1")
        removed = mgr.cancel_queued("k1", "ts1")
        assert removed is True
        result = mgr.dequeue("k1")
        assert result[0] == "ts2"
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_cancel_queued_marks_inflight(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        # semaphore locked = something in-flight
        removed = mgr.cancel_queued("k1", "ts_inflight")
        assert removed is False
        assert "ts_inflight" in mgr._sessions["k1"].cancelled
        mgr.release("k1")
        await mgr.close_all()

    def test_cancel_queued_missing_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert mgr.cancel_queued("nope", "ts1") is False

    @pytest.mark.asyncio
    async def test_is_cancelled_consumes(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.release("k1")
        mgr._sessions["k1"].cancelled.add("ts1")
        assert mgr.is_cancelled("k1", "ts1") is True
        # Second call returns False (consumed)
        assert mgr.is_cancelled("k1", "ts1") is False
        await mgr.close_all()

    def test_is_cancelled_missing_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert mgr.is_cancelled("nope", "ts1") is False


class TestDrainProviders:
    """Tests for drain_all_providers and drain_warm_pool."""

    @pytest.mark.asyncio
    async def test_drain_all_providers(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.release("k1")
        await mgr.get_or_create("k2")
        mgr.release("k2")
        providers = await mgr.drain_all_providers()
        assert len(providers) == 2
        assert mgr.count == 0
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_drain_all_providers_empty(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        providers = await mgr.drain_all_providers()
        assert providers == []

    @pytest.mark.asyncio
    async def test_drain_warm_pool(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        # Manually put items in the warm pool
        mock_p = AsyncMock()
        mgr._warm_pool.put_nowait((mock_p, "agent1"))
        drained = await mgr.drain_warm_pool()
        assert len(drained) == 1
        assert drained[0] is mock_p
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_drain_warm_pool_empty(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        drained = await mgr.drain_warm_pool()
        assert drained == []


class TestRelease:
    """Tests for release() with subagent cleanup."""

    @pytest.mark.asyncio
    async def test_release_normal_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        # Semaphore should be locked after get_or_create
        assert mgr._sessions["k1"].semaphore.locked()
        mgr.release("k1")
        assert not mgr._sessions["k1"].semaphore.locked()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_release_subagent_with_cleanup(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("subagent:abc")
        provider.session_id = "sid-123"
        provider.cleanup_session = AsyncMock()
        mgr.release("subagent:abc", cleanup=True)
        # Allow the ensure_future to run
        await asyncio.sleep(0)
        provider.cleanup_session.assert_awaited_once_with("sid-123")
        await mgr.close_all()

    def test_release_missing_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr.release("nonexistent")  # should not raise


class TestResetWithPid:
    """Tests for reset() PID capture and force-kill logic."""

    @pytest.mark.asyncio
    async def test_reset_no_pid_just_shuts_down(self, cfg):
        """reset() with no PID attribute just calls shutdown."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        await mgr.reset("k1")
        provider.shutdown.assert_awaited_once()
        assert not mgr.has_session("k1")

    @pytest.mark.asyncio
    async def test_reset_with_acp_pid_dead_after_shutdown(self, cfg):
        """reset() with ACP PID that dies after shutdown — no force kill."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        # Simulate ACP client with PID
        mock_client = AsyncMock()
        mock_client._pid = 12345
        mock_client._child_pids = {}
        provider._client = mock_client

        with patch("os.kill", side_effect=ProcessLookupError):
            await mgr.reset("k1")

        provider.shutdown.assert_awaited_once()
        assert not mgr.has_session("k1")

    @pytest.mark.asyncio
    async def test_reset_with_pid_survives_shutdown_force_kills(self, cfg):
        """reset() force-kills when PID survives shutdown."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        mock_client = AsyncMock()
        mock_client._pid = 12345
        mock_client._child_pids = {}
        provider._client = mock_client

        with patch("os.kill", side_effect=[None, None]), \
             patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=12345), \
             patch("personalclaw.acp.client._get_child_pids", return_value=[]), \
             patch("personalclaw.acp.client._get_start_time", return_value=None):
            await mgr.reset("k1")
            mock_killpg.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_with_cc_provider_proc(self, cfg):
        """reset() picks up PID from ClaudeCode _proc attribute."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        # No _client, but has _proc (CC provider style)
        provider._client = None
        mock_proc = AsyncMock()
        mock_proc.pid = 99999
        mock_proc.returncode = None
        provider._proc = mock_proc

        with patch("os.kill", side_effect=ProcessLookupError), \
             patch("personalclaw.acp.client._get_child_pids", return_value=[]), \
             patch("personalclaw.acp.client._get_start_time", return_value=None):
            await mgr.reset("k1")

        provider.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reset_child_sweep(self, cfg):
        """reset() sweeps escaped children after root is dead."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        mock_client = AsyncMock()
        mock_client._pid = 12345
        mock_client._child_pids = {111: 1000, 222: 2000}
        provider._client = mock_client

        with patch("os.kill", side_effect=ProcessLookupError), \
             patch("personalclaw.acp.client._get_child_pids", return_value=[333]), \
             patch("personalclaw.acp.client._get_start_time", return_value=3000), \
             patch("personalclaw.acp.client._kill_escaped_children") as mock_sweep:
            await mgr.reset("k1")
            mock_sweep.assert_called_once()
            # Should include both original children and discovered ones
            call_arg = mock_sweep.call_args[0][0]
            assert 111 in call_arg
            assert 222 in call_arg
            assert 333 in call_arg

    @pytest.mark.asyncio
    async def test_reset_nonexistent_session(self, cfg):
        """reset() on missing key is a no-op."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.reset("nonexistent")  # should not raise


class TestReloadProviderFactory:
    """Tests for reload_provider_factory."""

    @pytest.mark.asyncio
    async def test_reload_clears_sessions_and_pool(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.release("k1")
        # Put something in warm pool
        mock_pool_p = AsyncMock()
        mgr._warm_pool.put_nowait((mock_pool_p, "agent"))

        with patch.object(AppConfig, "load", return_value=cfg), \
             patch.object(cfg, "create_provider_factory", return_value=_mock_provider_factory()):
            await mgr.reload_provider_factory()

        # Old sessions cleared
        assert not mgr.has_session("k1")
        # Pool provider shut down
        mock_pool_p.shutdown.assert_awaited_once()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_reload_shuts_down_stale_sessions(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")

        with patch.object(AppConfig, "load", return_value=cfg), \
             patch.object(cfg, "create_provider_factory", return_value=_mock_provider_factory()):
            await mgr.reload_provider_factory()

        provider.shutdown.assert_awaited_once()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_reload_shutdown_exception_swallowed(self, cfg):
        """Stale session shutdown failure doesn't crash reload."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        provider.shutdown = AsyncMock(side_effect=OSError("dead"))

        with patch.object(AppConfig, "load", return_value=cfg), \
             patch.object(cfg, "create_provider_factory", return_value=_mock_provider_factory()):
            await mgr.reload_provider_factory()  # should not raise

        await mgr.close_all()


class TestCheckContextUsage:
    """Tests for check_context_usage thresholds and prompt counting."""

    @pytest.mark.asyncio
    async def test_increments_prompt_count(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        assert mgr._sessions["k1"].prompt_count == 0
        mgr.check_context_usage("k1", provider)
        assert mgr._sessions["k1"].prompt_count == 1
        mgr.check_context_usage("k1", provider)
        assert mgr._sessions["k1"].prompt_count == 2
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_returns_pct(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        provider.context_usage_pct = lambda: 42.5
        result = mgr.check_context_usage("k1", provider)
        assert result == 42.5
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_warning_at_70_pct(self, cfg, caplog):
        cfg.session.autocompact_pct = 90.0
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        provider.context_usage_pct = lambda: 75.0
        with caplog.at_level(logging.WARNING, logger="personalclaw.session"):
            mgr.check_context_usage("k1", provider)
        assert any("75%" in r.message for r in caplog.records)
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_compaction_triggered_at_threshold(self, cfg):
        cfg.session.autocompact_pct = 90.0
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        provider.context_usage_pct = lambda: 92.0
        with patch.object(mgr, "_trigger_compaction") as mock_trigger:
            mgr.check_context_usage("k1", provider)
            mock_trigger.assert_called_once_with("k1", "context at 92%", 92.0)
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_no_compaction_below_threshold(self, cfg):
        cfg.session.autocompact_pct = 90.0
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        provider.context_usage_pct = lambda: 50.0
        with patch.object(mgr, "_trigger_compaction") as mock_trigger:
            mgr.check_context_usage("k1", provider)
            mock_trigger.assert_not_called()
        await mgr.close_all()

    def test_missing_session_still_returns_pct(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mock_p = AsyncMock()
        mock_p.context_usage_pct = lambda: 55.0
        result = mgr.check_context_usage("nonexistent", mock_p)
        assert result == 55.0


class TestDestroy:
    """Tests for destroy() — permanent session removal."""

    @pytest.mark.asyncio
    async def test_destroy_shuts_down_and_deletes_map(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        with patch.object(mgr._session_map, "delete") as mock_delete:
            await mgr.destroy("k1")
        provider.shutdown.assert_awaited_once()
        mock_delete.assert_called_once_with("k1")
        assert not mgr.has_session("k1")

    @pytest.mark.asyncio
    async def test_destroy_nonexistent_still_deletes_map(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        with patch.object(mgr._session_map, "delete") as mock_delete:
            await mgr.destroy("nonexistent")
        mock_delete.assert_called_once_with("nonexistent")

    @pytest.mark.asyncio
    async def test_destroy_shutdown_exception_still_deletes_map(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        provider.shutdown = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(mgr._session_map, "delete") as mock_delete:
            with pytest.raises(RuntimeError, match="boom"):
                await mgr.destroy("k1")
        # finally block still runs
        mock_delete.assert_called_once_with("k1")


class TestContextInfo:
    """Tests for context_info() and _resolve_agent_model()."""

    @pytest.mark.asyncio
    async def test_context_info_basic(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:session0")
        mgr.release("dashboard:session0")
        mgr._sessions["dashboard:session0"].prompt_count = 5

        info = mgr.context_info()
        assert len(info) == 1
        entry = info[0]
        assert entry["key"] == "dashboard:session0"
        assert entry["name"] == "Chat (session0)"
        assert entry["prompts"] == 5
        assert entry["context_pct"] == 0.0
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_context_info_background_key_name(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()
        info = mgr.context_info()
        bg_entry = next(e for e in info if e["key"] == BACKGROUND_KEY)
        assert "Background" in bg_entry["name"]
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_context_info_non_dashboard_key(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("channel:thread123")
        mgr.release("channel:thread123")
        info = mgr.context_info()
        entry = next(e for e in info if e["key"] == "channel:thread123")
        assert entry["name"] == "channel:thread123"
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_context_info_with_acp_provider(self, cfg):
        """AgentProvider path extracts model and agent via public accessors."""
        from unittest.mock import MagicMock

        from personalclaw.llm.acp_agent import AcpAgentProvider
        from personalclaw.session import _Session

        mock_provider = MagicMock(spec=AcpAgentProvider)
        mock_provider.context_usage_pct = MagicMock(return_value=45.0)
        mock_provider.shutdown = AsyncMock()
        # context_info reads the public AgentProvider accessors, not client internals.
        mock_provider.agent_model = "sonnet-4"
        mock_provider.agent_name = "personalclaw"
        mock_provider.session_id = ""

        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._sessions["k1"] = _Session(provider=mock_provider, prompt_count=3)

        info = mgr.context_info()
        entry = info[0]
        assert entry["model"] == "sonnet-4"
        assert entry["agent"] == "personalclaw"
        assert entry["context_pct"] == 45.0
        await mgr.close_all()

    def test_resolve_agent_model_cache_miss_returns_auto(self, cfg):
        # Clear cache if exists
        if hasattr(SessionManager, "_agent_model_cache"):
            SessionManager._agent_model_cache.clear()
        result = SessionManager._resolve_agent_model("nonexistent-agent-xyz")
        assert result == "auto"

    def test_resolve_agent_model_from_file(self, cfg, tmp_path):
        """Reads model from agent JSON file."""
        import json
        if hasattr(SessionManager, "_agent_model_cache"):
            SessionManager._agent_model_cache.clear()
        agent_file = tmp_path / "test-agent.json"
        agent_file.write_text(json.dumps({"name": "test-agent", "model": "opus-5"}))

        with patch("personalclaw.agent.AGENTS_DIR", tmp_path):
            result = SessionManager._resolve_agent_model("test-agent")
        assert result == "opus-5"


class TestWarmPoolInternals:
    """Tests for _fill_warm_pool, _claim_from_pool, _drain_and_claim."""

    @pytest.mark.asyncio
    async def test_fill_warm_pool_spawns_to_size(self, cfg):
        cfg.session.pool_size = 2
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_size = 2
        await mgr._fill_warm_pool()
        assert mgr._warm_pool.qsize() == 2
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_fill_warm_pool_no_factory(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._provider_factory = None
        await mgr._fill_warm_pool()  # should not raise
        assert mgr._warm_pool.qsize() == 0

    @pytest.mark.asyncio
    async def test_fill_warm_pool_zero_size(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_size = 0
        await mgr._fill_warm_pool()
        assert mgr._warm_pool.qsize() == 0

    @pytest.mark.asyncio
    async def test_fill_warm_pool_stops_on_failure(self, cfg):
        call_count = 0

        def failing_factory(session_key=None, agent=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise RuntimeError("spawn failed")
            m = AsyncMock()
            m.start = AsyncMock()
            return m

        mgr = SessionManager(cfg, provider_factory=failing_factory)
        mgr._pool_size = 3
        await mgr._fill_warm_pool()
        # Only 1 succeeded before failure stopped the loop
        assert mgr._warm_pool.qsize() == 1
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_claim_from_pool_matching_agent(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_agent = "personalclaw"
        mock_p = AsyncMock()
        mgr._warm_pool.put_nowait((mock_p, 100.0))
        result = mgr._claim_from_pool("personalclaw")
        assert result == (mock_p, 100.0)

    @pytest.mark.asyncio
    async def test_claim_from_pool_mismatched_agent(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_agent = "personalclaw"
        mock_p = AsyncMock()
        mgr._warm_pool.put_nowait((mock_p, 100.0))
        result = mgr._claim_from_pool("different-agent")
        assert result is None

    @pytest.mark.asyncio
    async def test_claim_from_pool_empty(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        result = mgr._claim_from_pool(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_drain_and_claim_healthy(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_agent = ""
        mock_p = AsyncMock()
        mock_p.is_process_alive = lambda: True
        mgr._warm_pool.put_nowait((mock_p, time.monotonic()))
        result = await mgr._drain_and_claim(None)
        assert result is mock_p
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_drain_and_claim_dead_provider_discarded(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_agent = ""
        dead_p = AsyncMock()
        dead_p.is_process_alive = lambda: False
        dead_p.exit_code = 1
        mgr._warm_pool.put_nowait((dead_p, time.monotonic()))
        result = await mgr._drain_and_claim(None)
        assert result is None
        dead_p.shutdown.assert_awaited_once()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_drain_and_claim_stale_ttl_discarded(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_agent = ""
        mgr._pool_ttl_secs = 60
        stale_p = AsyncMock()
        stale_p.is_process_alive = lambda: True

        # Spawned 120s ago — exceeds 60s TTL
        mgr._warm_pool.put_nowait((stale_p, time.monotonic() - 120))
        result = await mgr._drain_and_claim(None)
        assert result is None
        stale_p.shutdown.assert_awaited_once()
        await mgr.close_all()


class TestPoolHealthLoop:
    """Tests for _pool_health_loop periodic sweep."""

    @pytest.mark.asyncio
    async def test_health_loop_removes_dead_providers(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_size = 2
        mgr._pool_ttl_secs = 0  # no TTL

        dead_p = AsyncMock()
        dead_p.is_process_alive = lambda: False
        dead_p.exit_code = 1
        dead_p.client = AsyncMock()
        dead_p.client._pid = 111

        mgr._warm_pool.put_nowait((dead_p, time.monotonic()))

        # Run one iteration then cancel
        call_count = 0
        original_sleep = asyncio.sleep

        async def one_pass_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError
            await original_sleep(0)

        with patch("asyncio.sleep", side_effect=one_pass_sleep):
            with pytest.raises(asyncio.CancelledError):
                await mgr._pool_health_loop()

        assert mgr._warm_pool.qsize() == 0
        dead_p.shutdown.assert_awaited_once()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_health_loop_keeps_healthy_providers(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_size = 2
        mgr._pool_ttl_secs = 0

        healthy_p = AsyncMock()
        healthy_p.is_process_alive = lambda: True
        healthy_p.client = AsyncMock()
        healthy_p.client._pid = 222

        mgr._warm_pool.put_nowait((healthy_p, time.monotonic()))

        call_count = 0

        async def one_pass_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError
            # instant return for first sleep
            return

        with patch("asyncio.sleep", side_effect=one_pass_sleep):
            with pytest.raises(asyncio.CancelledError):
                await mgr._pool_health_loop()

        assert mgr._warm_pool.qsize() == 1
        healthy_p.shutdown.assert_not_awaited()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_health_loop_ttl_expiry(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_size = 2
        mgr._pool_ttl_secs = 60

        stale_p = AsyncMock()
        stale_p.is_process_alive = lambda: True
        stale_p.client = AsyncMock()
        stale_p.client._pid = 333

        mgr._warm_pool.put_nowait((stale_p, time.monotonic() - 120))

        call_count = 0

        async def one_pass_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError
            return

        with patch("asyncio.sleep", side_effect=one_pass_sleep):
            with pytest.raises(asyncio.CancelledError):
                await mgr._pool_health_loop()

        assert mgr._warm_pool.qsize() == 0
        stale_p.shutdown.assert_awaited_once()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_health_loop_empty_pool_skips(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_size = 2

        call_count = 0

        async def one_pass_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError
            return

        with patch("asyncio.sleep", side_effect=one_pass_sleep):
            with pytest.raises(asyncio.CancelledError):
                await mgr._pool_health_loop()
        # No crash, just skipped
        await mgr.close_all()


class TestCleanupLoop:
    """Tests for _cleanup_loop periodic maintenance."""

    @pytest.mark.asyncio
    async def test_cleanup_loop_calls_expire_idle(self, cfg):
        cfg.session.timeout_secs = 120
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())

        with patch.object(mgr, "_expire_idle", new_callable=AsyncMock) as mock_expire, \
             patch("personalclaw.session._cleanup_orphaned_mcp_servers", return_value=0), \
             patch("personalclaw.session._collect_active_pids", return_value=({}, True)), \
             patch("personalclaw.session._periodic_pid_sweep", return_value=([], [])), \
             patch("personalclaw.session._kill_confirmed_and_writeback", return_value=0), \
             patch("personalclaw.session.shutdown_event") as mock_event:
            # First wait_for returns TimeoutError (normal wakeup), second signals shutdown
            mock_event.is_set = lambda: mock_expire.await_count >= 1
            mock_event.wait = AsyncMock(side_effect=asyncio.TimeoutError)
            await mgr._cleanup_loop()

        mock_expire.assert_awaited_once_with(120)
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_cleanup_loop_disabled_idle_sweep(self, cfg):
        cfg.session.timeout_secs = 0
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())

        with patch.object(mgr, "_expire_idle", new_callable=AsyncMock) as mock_expire, \
             patch("personalclaw.session._cleanup_orphaned_mcp_servers", return_value=0), \
             patch("personalclaw.session._collect_active_pids", return_value=({}, True)), \
             patch("personalclaw.session._periodic_pid_sweep", return_value=([], [])), \
             patch("personalclaw.session._kill_confirmed_and_writeback", return_value=0), \
             patch("personalclaw.session.shutdown_event") as mock_event:
            call_count = [0]

            async def one_pass(*a, **kw):
                call_count[0] += 1
                raise asyncio.TimeoutError

            mock_event.is_set = lambda: call_count[0] >= 1
            mock_event.wait = AsyncMock(side_effect=one_pass)
            await mgr._cleanup_loop()

        # idle sweep disabled — _expire_idle should NOT be called
        mock_expire.assert_not_awaited()
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_cleanup_loop_clamps_low_timeout(self, cfg, caplog):
        cfg.session.timeout_secs = 30  # below 60 minimum
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())

        with patch.object(mgr, "_expire_idle", new_callable=AsyncMock) as mock_expire, \
             patch("personalclaw.session._cleanup_orphaned_mcp_servers", return_value=0), \
             patch("personalclaw.session._collect_active_pids", return_value=({}, True)), \
             patch("personalclaw.session._periodic_pid_sweep", return_value=([], [])), \
             patch("personalclaw.session._kill_confirmed_and_writeback", return_value=0), \
             patch("personalclaw.session.shutdown_event") as mock_event:
            mock_event.is_set = lambda: mock_expire.await_count >= 1
            mock_event.wait = AsyncMock(side_effect=asyncio.TimeoutError)
            with caplog.at_level(logging.WARNING, logger="personalclaw.session"):
                await mgr._cleanup_loop()

        # Should clamp to 60
        mock_expire.assert_awaited_once_with(60)
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_cleanup_loop_shutdown_signal(self, cfg):
        cfg.session.timeout_secs = 120
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())

        with patch("personalclaw.session.shutdown_event") as mock_event:
            mock_event.is_set = lambda: True
            mock_event.wait = AsyncMock(return_value=None)
            # Should return immediately since shutdown is set
            await mgr._cleanup_loop()
        await mgr.close_all()


class TestPoolPids:
    """Tests for _pool_pids non-destructive peek."""

    @pytest.mark.asyncio
    async def test_pool_pids_extracts_pids(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mock_p = AsyncMock()
        mock_p.client = AsyncMock()
        mock_p.client._pid = 42
        mgr._warm_pool.put_nowait((mock_p, time.monotonic()))
        pids = mgr._pool_pids()
        assert 42 in pids
        # Non-destructive — item still in pool
        assert mgr._warm_pool.qsize() == 1

    @pytest.mark.asyncio
    async def test_pool_pids_empty(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert mgr._pool_pids() == set()

    @pytest.mark.asyncio
    async def test_pool_pids_includes_sweep_pids(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_sweep_pids.add(999)
        pids = mgr._pool_pids()
        assert 999 in pids


class TestChannelLinkHelpers:
    """Tests for set/get channel_link, thread, channel helpers."""

    @pytest.mark.asyncio
    async def test_set_and_get_channel_link(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr.set_channel_link("k1", "ts123", "C001")
        assert mgr.get_channel_link("k1") == ("ts123", "C001")

    @pytest.mark.asyncio
    async def test_get_session_for_thread(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr.set_channel_link("k1", "ts123", "C001")
        assert mgr.get_session_for_thread("ts123") == "k1"
        assert mgr.get_session_for_thread("unknown") is None

    @pytest.mark.asyncio
    async def test_set_channel_compat(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr.set_channel_link("k1", "ts123", None)
        await mgr.set_channel("k1", "C002")
        assert mgr.get_channel("k1") == "C002"

    @pytest.mark.asyncio
    async def test_set_thread_compat(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr.set_channel_link("k1", "", "C001")
        await mgr.set_thread("k1", "ts456")
        assert mgr.get_thread("k1") == "ts456"

    def test_get_channel_no_link(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert mgr.get_channel("nonexistent") is None

    def test_get_thread_no_link(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert mgr.get_thread("nonexistent") is None

    def test_find_key_by_sid(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._session_map.set("k1", "sid-abc")
        assert mgr.find_key_by_sid("sid-abc") == "k1"
        assert mgr.find_key_by_sid("unknown") is None

    def test_delete_session_map_entry(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._session_map.set("k1", "sid-abc")
        mgr.delete_session_map_entry("k1")
        assert mgr.find_key_by_sid("sid-abc") is None


class TestGetPid:
    """Tests for get_pid."""

    @pytest.mark.asyncio
    async def test_get_pid_returns_pid(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        provider.client = AsyncMock()
        provider.client._pid = 12345
        assert mgr.get_pid("k1") == 12345
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_get_pid_no_client(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        # Remove client attr
        del provider.client
        assert mgr.get_pid("k1") is None
        await mgr.close_all()

    def test_get_pid_missing_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        assert mgr.get_pid("nonexistent") is None


class TestIsProviderAliveFallback:
    """Test is_provider_alive fallback to is_alive when no is_process_alive."""

    @pytest.mark.asyncio
    async def test_fallback_to_is_alive(self, cfg):
        from unittest.mock import MagicMock
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        # Remove is_process_alive so it falls back
        if hasattr(provider, "is_process_alive"):
            del provider.is_process_alive
        provider.is_alive = MagicMock(return_value=True)
        result = await mgr.is_provider_alive("k1")
        assert result is True
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_no_session_returns_none(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        result = await mgr.is_provider_alive("nonexistent")
        assert result is None


class TestSetActiveDashboardSessions:
    """Test set_active_dashboard_sessions."""

    def test_sets_sessions(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr.set_active_dashboard_sessions({"dashboard:tab1", "dashboard:tab2"})
        assert mgr._active_dashboard_sessions == {"dashboard:tab1", "dashboard:tab2"}


class TestStartPoolNonBlocking:
    """Tests for start_pool non-blocking path."""

    @pytest.mark.asyncio
    async def test_start_pool_non_blocking(self, cfg):
        cfg.session.pool_size = 1
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_size = 1
        await mgr.start_pool(blocking=False)
        # Let background tasks run
        await asyncio.sleep(0.1)
        assert BACKGROUND_KEY in mgr._sessions
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_start_pool_no_factory(self, cfg):
        mgr = SessionManager(cfg, provider_factory=None)
        await mgr.start_pool()  # should be no-op
        assert mgr.count == 0

    @pytest.mark.asyncio
    async def test_ensure_background_already_exists(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.start_pool()
        # Call again — should be no-op
        await mgr._ensure_background()
        assert mgr.count == 1  # still just the one bg session
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_ensure_background_factory_failure(self, cfg):
        def failing_factory(session_key=None, **kwargs):
            raise RuntimeError("spawn failed")

        mgr = SessionManager(cfg, provider_factory=failing_factory)
        await mgr._ensure_background()
        # Should not crash, just log warning
        assert BACKGROUND_KEY not in mgr._sessions


class TestScheduleReplenish:
    """Tests for _schedule_replenish fire-and-forget pool refill."""

    @pytest.mark.asyncio
    async def test_schedule_replenish_creates_task(self, cfg):
        cfg.session.pool_size = 2
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_size = 2
        mgr._schedule_replenish()
        await asyncio.sleep(0.1)  # let task run
        assert mgr._warm_pool.qsize() >= 1
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_schedule_replenish_noop_when_pool_disabled(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_size = 0
        mgr._schedule_replenish()  # should not create task
        assert len(mgr._background_tasks) == 0


class TestCompaction:
    """Tests for _trigger_compaction and _compact_session."""

    @pytest.mark.asyncio
    async def test_trigger_compaction_duplicate_is_noop(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("k1")
        mgr.release("k1")
        # First trigger starts compaction
        mgr._trigger_compaction("k1", "test", 92.0)
        assert "k1" in mgr._compacting
        # Second trigger on same key is a no-op (already in progress)
        mgr._trigger_compaction("k1", "test again", 95.0)
        await asyncio.sleep(0.1)
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_compact_session_calls_callback(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        callback_args = []
        mgr._on_compacted = AsyncMock(side_effect=lambda k, p: callback_args.append((k, p)))
        await mgr._compact_session("k1", 92.0)
        provider.shutdown.assert_awaited_once()
        assert callback_args == [("k1", 92.0)]
        assert not mgr.has_session("k1")

    @pytest.mark.asyncio
    async def test_compact_session_missing_key_is_safe(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._compacting.add("gone")
        await mgr._compact_session("gone", 90.0)
        assert "gone" not in mgr._compacting


class TestCloseAllPersistence:
    """Tests for close_all session_map persistence."""

    @pytest.mark.asyncio
    async def test_close_all_persists_acp_session_ids(self, cfg):
        from unittest.mock import MagicMock

        from personalclaw.llm.acp_agent import AcpAgentProvider
        from personalclaw.session import _Session

        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mock_provider = MagicMock(spec=AcpAgentProvider)
        mock_provider.shutdown = AsyncMock()
        mock_provider.context_usage_pct = MagicMock(return_value=0.0)
        mock_provider._work_dir = "/tmp/test"
        # close_all persists via the public session_id accessor.
        mock_provider.session_id = "sid-persist-test"

        mgr._sessions["dashboard:session0"] = _Session(provider=mock_provider)
        with patch.object(mgr._session_map, "set") as mock_set:
            await mgr.close_all()
        mock_set.assert_called_once_with(
            "dashboard:session0", "sid-persist-test", cwd="/tmp/test"
        )


class TestRemove:
    """Tests for remove() — shutdown but preserve session_map."""

    @pytest.mark.asyncio
    async def test_remove_shuts_down_preserves_map(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        with patch.object(mgr._session_map, "delete") as mock_delete:
            await mgr.remove("k1")
        provider.shutdown.assert_awaited_once()
        mock_delete.assert_not_called()  # remove preserves map
        assert not mgr.has_session("k1")

    @pytest.mark.asyncio
    async def test_remove_missing_key_is_noop(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.remove("nonexistent")  # should not raise


class TestSafeCleanup:
    """Tests for _safe_cleanup best-effort session file removal."""

    @pytest.mark.asyncio
    async def test_cleanup_calls_provider(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mock_p = AsyncMock()
        mock_p.cleanup_session = AsyncMock()
        await mgr._safe_cleanup(mock_p, "sid-123")
        mock_p.cleanup_session.assert_awaited_once_with("sid-123")

    @pytest.mark.asyncio
    async def test_cleanup_swallows_exception(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mock_p = AsyncMock()
        mock_p.cleanup_session = AsyncMock(side_effect=OSError("disk full"))
        await mgr._safe_cleanup(mock_p, "sid-456")  # should not raise


class TestSetCompactCallback:
    """Tests for set_compact_callback."""

    def test_sets_callback(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        cb = AsyncMock()
        mgr.set_compact_callback(cb)
        assert mgr._on_compacted is cb

    def test_warns_on_replace(self, cfg, caplog):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr.set_compact_callback(AsyncMock())
        with caplog.at_level(logging.WARNING, logger="personalclaw.session"):
            mgr.set_compact_callback(AsyncMock())
        assert any("already registered" in r.message for r in caplog.records)


class TestExpireIdleOrphans:
    """Tests for _expire_idle orphaned dashboard session detection."""

    @pytest.mark.asyncio
    async def test_orphaned_dashboard_session_expired(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:session5")
        mgr.release("dashboard:session5")
        # Set active sessions to NOT include session5
        mgr.set_active_dashboard_sessions({"dashboard:session0"})
        await mgr._expire_idle(timeout_secs=9999)  # not idle, but orphaned
        assert not mgr.has_session("dashboard:session5")

    @pytest.mark.asyncio
    async def test_active_session_not_expired(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:session0")
        mgr.release("dashboard:session0")
        mgr.set_active_dashboard_sessions({"dashboard:session0"})
        await mgr._expire_idle(timeout_secs=9999)
        assert mgr.has_session("dashboard:session0")
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_channel_session_never_expired(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("channel:C123")
        mgr.release("channel:C123")
        # Backdate to make it idle
        async with mgr._lock:
            mgr._sessions["channel:C123"].last_used = time.monotonic() - 9999
        await mgr._expire_idle(timeout_secs=1)
        assert mgr.has_session("channel:C123")
        await mgr.close_all()


class TestGetOrCreatePoolClaim:
    """Test get_or_create claiming from warm pool."""

    @pytest.mark.asyncio
    async def test_claims_from_pool_on_new_session(self, cfg):
        from personalclaw.llm.acp_agent import AcpAgentProvider

        cfg.session.pool_size = 1
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        mgr._pool_size = 1
        mgr._pool_agent = "personalclaw"

        # Pre-fill pool with a mock provider that looks like AcpAgentProvider.
        # The claim path drives the public AgentProvider surface
        # (set_session_key / set_model / resumed / session_id), not client internals.
        mock_pooled = AsyncMock(spec=AcpAgentProvider)
        mock_pooled.start = AsyncMock()
        mock_pooled.shutdown = AsyncMock()
        mock_pooled.context_usage_pct = lambda: 0.0
        mock_pooled.is_process_alive = lambda: True
        mock_pooled.agent_model = "claude-opus-4"
        mock_pooled.agent_name = "personalclaw"
        mock_pooled.session_id = ""
        mock_pooled.set_session_key = lambda *a, **kw: None
        mock_pooled.resumed = False

        mgr._warm_pool.put_nowait((mock_pooled, time.monotonic()))

        provider, is_new, _ = await mgr.get_or_create("dashboard:session1", agent="personalclaw")
        mgr.release("dashboard:session1")
        assert provider is mock_pooled
        assert is_new is True
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_cold_start_with_resume_sid(self, cfg):
        """get_or_create with a stored session_map entry attempts resume."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        # Mock session_map to return a resume SID
        with patch.object(mgr._session_map, "get", return_value="sid-resume-test"), \
             patch.object(mgr._session_map, "get_cwd", return_value=None), \
             patch.object(mgr._session_map, "get_provider", return_value="acp"):
            provider, is_new, _ = await mgr.get_or_create("dashboard:session2")
            mgr.release("dashboard:session2")
        assert is_new is True
        assert mgr.has_session("dashboard:session2")
        await mgr.close_all()


class TestGetOrCreateDeadProvider:
    """Test get_or_create when existing session has a dead provider."""

    @pytest.mark.asyncio
    async def test_dead_provider_gets_replaced(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        provider, _, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        # Mark provider as dead
        provider.is_alive = lambda: False
        provider.is_process_alive = lambda: False
        # Next get_or_create should detect dead provider and create new one
        new_provider, is_new, _ = await mgr.get_or_create("k1")
        mgr.release("k1")
        assert new_provider is not provider
        await mgr.close_all()


class TestSessionTimeout:
    @pytest.mark.asyncio
    async def test_session_expires_after_timeout(self, cfg):
        cfg.session.timeout_secs = 1
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        p, is_new, _ = await mgr.get_or_create("thread1")
        mgr.release("thread1")
        # Manually backdate last_used
        async with mgr._lock:
            mgr._sessions["thread1"].last_used = time.monotonic() - 10
        await mgr._expire_idle(timeout_secs=1)
        assert mgr.count == 0
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_active_session_not_expired(self, cfg):
        cfg.session.timeout_secs = 10
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        p, is_new, _ = await mgr.get_or_create("thread1")
        mgr.release("thread1")
        await mgr._expire_idle(timeout_secs=10)
        assert mgr.count == 1
        await mgr.close_all()


class TestConcurrentAccess:
    @pytest.mark.asyncio
    async def test_concurrent_get_or_create_same_key(self, cfg):
        """Second get_or_create on same key reuses existing session."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        p1, new1, _ = await mgr.get_or_create("shared")
        mgr.release("shared")
        p2, new2, _ = await mgr.get_or_create("shared")
        mgr.release("shared")
        assert p1 is p2
        assert new1 is True
        assert new2 is False
        assert mgr.count == 1
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_concurrent_different_keys(self, cfg):
        """Different keys should create independent sessions."""
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        results = await asyncio.gather(  # noqa: F841
            mgr.get_or_create("a"),
            mgr.get_or_create("b"),
            mgr.get_or_create("c"),
        )
        assert mgr.count == 3
        for key in ("a", "b", "c"):
            mgr.release(key)
        await mgr.close_all()


class TestCloseSession:
    @pytest.mark.asyncio
    async def test_close_removes_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        p, _, _ = await mgr.get_or_create("thread1")
        mgr.release("thread1")
        await mgr.destroy("thread1")
        assert not mgr.has_session("thread1")

    @pytest.mark.asyncio
    async def test_close_nonexistent_is_noop(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.destroy("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_close_calls_shutdown(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        p, _, _ = await mgr.get_or_create("thread1")
        mgr.release("thread1")
        await mgr.destroy("thread1")
        p.shutdown.assert_awaited_once()


class TestCloseAll:
    @pytest.mark.asyncio
    async def test_close_all_shuts_down_all(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        p1, _, _ = await mgr.get_or_create("a")
        p2, _, _ = await mgr.get_or_create("b")
        mgr.release("a")
        mgr.release("b")
        await mgr.close_all()
        p1.shutdown.assert_awaited_once()
        p2.shutdown.assert_awaited_once()
        assert mgr.count == 0


class TestSessionState:
    @pytest.mark.asyncio
    async def test_is_new_flag(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        _, is_new1, _ = await mgr.get_or_create("t1")
        mgr.release("t1")
        _, is_new2, _ = await mgr.get_or_create("t1")
        mgr.release("t1")
        assert is_new1 is True
        assert is_new2 is False
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_release_updates_last_used(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("t1")
        mgr.release("t1")
        async with mgr._lock:
            sess = mgr._sessions["t1"]
        # last_used should be recent (within last second)
        assert time.monotonic() - sess.last_used < 1.0
        await mgr.close_all()


class TestBackgroundSession:
    @pytest.mark.asyncio
    async def test_background_key_constant(self):
        assert BACKGROUND_KEY == "_bg"

    @pytest.mark.asyncio
    async def test_ensure_background_creates_session(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr._ensure_background()
        async with mgr._lock:
            assert BACKGROUND_KEY in mgr._sessions
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_ensure_background_idempotent(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr._ensure_background()
        await mgr._ensure_background()
        # Should still only have one background session
        assert mgr.count == 1
        await mgr.close_all()


class TestContextInfoBasic:
    @pytest.mark.asyncio
    async def test_returns_session_info(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr.get_or_create("dashboard:session0")
        mgr.release("dashboard:session0")
        info = mgr.context_info()
        assert len(info) >= 1
        session_info = [i for i in info if i["key"] == "dashboard:session0"]
        assert len(session_info) == 1
        assert session_info[0]["context_pct"] == 0.0
        assert "Chat" in session_info[0]["name"]
        await mgr.close_all()

    @pytest.mark.asyncio
    async def test_background_session_name(self, cfg):
        mgr = SessionManager(cfg, provider_factory=_mock_provider_factory())
        await mgr._ensure_background()
        info = mgr.context_info()
        bg_info = [i for i in info if i["key"] == BACKGROUND_KEY]
        assert len(bg_info) == 1
        assert "Background" in bg_info[0]["name"]
        await mgr.close_all()
