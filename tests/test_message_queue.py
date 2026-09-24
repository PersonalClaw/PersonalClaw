"""Tests for the channel message queue on SessionManager."""

import asyncio
from unittest.mock import MagicMock

import pytest

from personalclaw.session import SessionManager, _Session

# ── Unit tests for _Session queue fields ──


class TestSessionQueue:
    def _make_session(self) -> _Session:
        provider = MagicMock()
        provider.is_alive.return_value = True
        return _Session(provider=provider)

    def test_queue_starts_empty(self):
        sess = self._make_session()
        assert len(sess.queue) == 0
        assert sess.cancelled == set()

    def test_cancelled_independent_per_session(self):
        s1 = self._make_session()
        s2 = self._make_session()
        s1.cancelled.add("ts1")
        assert "ts1" not in s2.cancelled


# ── Unit tests for SessionManager queue methods ──


class TestSessionManagerQueue:
    @staticmethod
    def _make_mgr() -> tuple[SessionManager, _Session]:
        mgr = SessionManager.__new__(SessionManager)
        mgr._sessions = {}
        mgr._lock = asyncio.Lock()
        provider = MagicMock()
        provider.is_alive.return_value = True
        sess = _Session(provider=provider)
        mgr._sessions["thread1"] = sess
        return mgr, sess

    def test_enqueue_returns_false_when_unlocked(self):
        mgr, sess = self._make_mgr()
        assert mgr.enqueue("thread1", "ts1", "hello") is False
        assert len(sess.queue) == 0

    def test_enqueue_force_bypasses_lock_check(self):
        mgr, sess = self._make_mgr()
        assert mgr.enqueue("thread1", "ts1", "hello", force=True) is True
        assert len(sess.queue) == 1

    @pytest.mark.asyncio
    async def test_enqueue_returns_true_when_locked(self):
        mgr, sess = self._make_mgr()
        await sess.semaphore.acquire()
        assert mgr.enqueue("thread1", "ts1", "hello", channel="C1") is True
        assert len(sess.queue) == 1
        assert sess.queue[0] == ("ts1", "hello", {"channel": "C1"})
        sess.semaphore.release()

    def test_enqueue_unknown_session(self):
        mgr, _ = self._make_mgr()
        assert mgr.enqueue("unknown", "ts1", "hi") is False

    def test_dequeue_empty(self):
        mgr, _ = self._make_mgr()
        assert mgr.dequeue("thread1") is None

    def test_dequeue_unknown_session(self):
        mgr, _ = self._make_mgr()
        assert mgr.dequeue("unknown") is None

    @pytest.mark.asyncio
    async def test_dequeue_fifo(self):
        mgr, sess = self._make_mgr()
        await sess.semaphore.acquire()
        mgr.enqueue("thread1", "ts1", "first")
        mgr.enqueue("thread1", "ts2", "second")
        sess.semaphore.release()
        result = mgr.dequeue("thread1")
        assert result is not None
        assert result[0] == "ts1"
        assert result[1] == "first"

    @pytest.mark.asyncio
    async def test_dequeue_skips_cancelled(self):
        mgr, sess = self._make_mgr()
        await sess.semaphore.acquire()
        mgr.enqueue("thread1", "ts1", "first")
        mgr.enqueue("thread1", "ts2", "second")
        sess.semaphore.release()
        sess.cancelled.add("ts1")
        result = mgr.dequeue("thread1")
        assert result is not None
        assert result[0] == "ts2"
        assert "ts1" not in sess.cancelled  # cleaned up

    def test_cancel_queued_removes_from_queue(self):
        mgr, sess = self._make_mgr()
        sess.queue.append(("ts1", "hello", {}))
        sess.queue.append(("ts2", "world", {}))
        assert mgr.cancel_queued("thread1", "ts1") is True
        assert len(sess.queue) == 1
        assert sess.queue[0][0] == "ts2"

    @pytest.mark.asyncio
    async def test_cancel_queued_adds_to_cancelled_if_not_in_queue(self):
        mgr, sess = self._make_mgr()
        await sess.semaphore.acquire()  # simulate in-flight processing
        assert mgr.cancel_queued("thread1", "ts_inflight") is False
        assert "ts_inflight" in sess.cancelled
        sess.semaphore.release()

    def test_cancel_queued_skips_cancelled_when_not_inflight(self):
        mgr, sess = self._make_mgr()
        assert mgr.cancel_queued("thread1", "ts_stale") is False
        assert "ts_stale" not in sess.cancelled

    def test_cancel_queued_unknown_session(self):
        mgr, _ = self._make_mgr()
        assert mgr.cancel_queued("unknown", "ts1") is False

    def test_is_cancelled_consumes_flag(self):
        mgr, sess = self._make_mgr()
        sess.cancelled.add("ts1")
        assert mgr.is_cancelled("thread1", "ts1") is True
        assert mgr.is_cancelled("thread1", "ts1") is False  # consumed

    def test_is_cancelled_unknown_session(self):
        mgr, _ = self._make_mgr()
        assert mgr.is_cancelled("unknown", "ts1") is False

    def test_clear_queue(self):
        mgr, sess = self._make_mgr()
        sess.queue.append(("ts1", "hello", {}))
        sess.cancelled.add("ts2")
        mgr.clear_queue("thread1")
        assert len(sess.queue) == 0
        assert sess.cancelled == set()


# ── Integration test: message_deleted event handling ──


class TestMessageDeletedEvent:
    @pytest.mark.asyncio
    async def test_message_deleted_removes_from_queue(self):
        """message_deleted subtype should cancel a queued message."""
        mgr, sess = TestSessionManagerQueue._make_mgr()
        await sess.semaphore.acquire()
        mgr.enqueue("thread1", "ts_queued", "will be deleted", channel="C1")
        assert len(sess.queue) == 1
        was_queued = mgr.cancel_queued("thread1", "ts_queued")
        assert was_queued is True
        assert len(sess.queue) == 0
        sess.semaphore.release()
