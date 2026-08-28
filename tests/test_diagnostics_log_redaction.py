import asyncio
import collections
import json
import logging
import sys
import types
from unittest.mock import MagicMock

if sys.platform == "win32":
    if "fcntl" not in sys.modules:
        _m = types.ModuleType("fcntl")
        _m.flock = lambda *args, **kwargs: None
        _m.LOCK_EX = 2
        _m.LOCK_NB = 4
        _m.LOCK_UN = 8
        _m.LOCK_SH = 1
        sys.modules["fcntl"] = _m
    if "termios" not in sys.modules:
        _t = types.ModuleType("termios")
        _t.tcgetattr = lambda *args: [0, 0, 0, 0, 0, 0, [0] * 32]
        _t.tcsetattr = lambda *args: None
        _t.TCSADRAIN = 1
        sys.modules["termios"] = _t
    if "pty" not in sys.modules:
        _p = types.ModuleType("pty")
        _p.openpty = lambda: (1, 2)
        _p.fork = lambda: (0, 1)
        sys.modules["pty"] = _p

from personalclaw.dashboard.handlers.updates import (
    _QueueLogHandler,
    _redact_log_text,
    _RingLogHandler,
)
from personalclaw.dashboard.state import DashboardState


class TestDiagnosticsLogRedaction:
    def test_redact_log_text_helper(self):
        """_redact_log_text redacts raw API keys, tokens, and git credentials.

        🔴 This claimed "git credentials" while planting a GitHub TOKEN as the
        password, and `ghp_…` is a shape `_CREDENTIAL_PATTERNS` already matched — so
        it passed because of the token's shape, not because URL userinfo was
        handled at all. An ordinary password in the same position went straight
        through (#406). The second case below is the one that fails on the old
        patterns, and it is why both are here.
        """
        token_part = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        raw = f"app registry: git fetch errored for https://user:{token_part}@github.com/repo.git"
        redacted = _redact_log_text(raw)
        assert token_part not in redacted
        assert "[REDACTED" in redacted

        # An ARBITRARY password — no recognisable provider shape, so only a
        # positional rule catches it.
        plain = "app registry: git fetch errored for https://alice:hunter2@github.com/repo.git"
        redacted_plain = _redact_log_text(plain)
        assert "hunter2" not in redacted_plain, "an ordinary password in a URL still leaks"
        # …and the host survives, or the log stops being diagnosable.
        assert "github.com/repo.git" in redacted_plain

    def test_redact_log_text_exfiltration_url(self):
        """_redact_log_text redacts suspicious URLs with long query parameters."""
        long_query = "a" * 250
        raw = f"Network request to https://analytics-tracker.org/event?payload={long_query}"
        redacted = _redact_log_text(raw)
        assert long_query not in redacted
        assert "[REDACTED: suspicious URL" in redacted

    def test_queue_log_handler_redacts_credentials(self):
        """_QueueLogHandler.emit redacts credentials before placing in queue."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        handler = _QueueLogHandler(queue)
        handler.setFormatter(logging.Formatter("%(message)s"))

        anthropic_key = "sk-ant-api03-abcdef1234567890abcdef1234567890"
        aws_key = "AKIAIOSFODNN7EXAMPLE"
        record = logging.LogRecord(
            name="personalclaw.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=10,
            msg="Connected using key %s and secret %s",
            args=(anthropic_key, aws_key),
            exc_info=None,
        )
        handler.emit(record)

        assert not queue.empty()
        payload = json.loads(queue.get_nowait())
        assert payload["level"] == "ERROR"
        assert anthropic_key not in payload["msg"]
        assert aws_key not in payload["msg"]
        assert "[REDACTED" in payload["msg"]

    def test_ring_log_handler_redacts_credentials_in_buffer(self):
        """_RingLogHandler.emit redacts credentials stored in the ring buffer."""
        ring: collections.deque[str] = collections.deque(maxlen=100)
        handler = _RingLogHandler(ring)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

        pat_token = "ghp_secretTokenVal1234567890abcdef"
        secret_url = f"https://user:{pat_token}@example.com/repo.git"
        record = logging.LogRecord(
            name="personalclaw.catalog",
            level=logging.WARNING,
            pathname=__file__,
            lineno=20,
            msg="app registry: git fetch errored for %s",
            args=(secret_url,),
            exc_info=None,
        )
        handler.emit(record)

        assert len(ring) == 1
        payload = json.loads(ring[0])
        assert payload["level"] == "WARNING"
        assert pat_token not in payload["msg"]
        assert "[REDACTED" in payload["msg"]

    def test_ring_log_handler_ws_broadcast_redacted(self):
        """_RingLogHandler broadcasts redacted logs to WebSocket subscribers."""
        ring: collections.deque[str] = collections.deque(maxlen=100)
        handler = _RingLogHandler(ring)
        handler.setFormatter(logging.Formatter("%(message)s"))

        state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
        ws_mock = MagicMock()
        state._ws_log_subscribers.add(ws_mock)
        handler.set_state(state)

        secret = "sk-proj-123456789012345678901234567890"
        record = logging.LogRecord(
            name="personalclaw.llm",
            level=logging.INFO,
            pathname=__file__,
            lineno=25,
            msg="Initialized model with key %s",
            args=(secret,),
            exc_info=None,
        )
        handler.emit(record)

        assert len(ring) == 1
        ring_payload = json.loads(ring[0])
        assert secret not in ring_payload["msg"]
        assert "[REDACTED" in ring_payload["msg"]

    def test_clean_log_record_preserved(self):
        """Non-sensitive log records are preserved accurately."""
        ring: collections.deque[str] = collections.deque(maxlen=100)
        handler = _RingLogHandler(ring)
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="personalclaw.server",
            level=logging.INFO,
            pathname=__file__,
            lineno=30,
            msg="Server started on port %d with %s workers",
            args=(8080, "4"),
            exc_info=None,
        )
        handler.emit(record)

        assert len(ring) == 1
        payload = json.loads(ring[0])
        assert payload["level"] == "INFO"
        assert payload["msg"] == "Server started on port 8080 with 4 workers"
