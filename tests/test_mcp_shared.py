"""Tests for mcp_shared: _read_message framing detection and respond output."""

import io
import json
from unittest.mock import patch

import personalclaw.mcp_shared as mcp_shared
from personalclaw.mcp_shared import _read_message, respond


def _make_stdin(data: bytes):
    """Create a fake stdin with a binary .buffer attribute."""
    buf = io.BytesIO(data)
    fake = type("FakeStdin", (), {"buffer": buf})()
    return fake


def _content_length_frame(obj: dict) -> bytes:
    body = json.dumps(obj).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8") + body


class TestReadMessageContentLength:
    def setup_method(self):
        mcp_shared._use_content_length = False

    def test_reads_content_length_message(self):
        msg = {"jsonrpc": "2.0", "method": "initialize", "id": 1}
        stdin = _make_stdin(_content_length_frame(msg))
        result = _read_message(stdin)
        assert result == msg
        assert mcp_shared._use_content_length is True

    def test_reads_multibyte_utf8(self):
        msg = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "tëst_émoji_🎉"},
        }
        stdin = _make_stdin(_content_length_frame(msg))
        result = _read_message(stdin)
        assert result == msg

    def test_reads_two_sequential_messages(self):
        """Two Content-Length messages from the same stream are read correctly."""
        msg1 = {"jsonrpc": "2.0", "method": "initialize", "id": 1}
        msg2 = {"jsonrpc": "2.0", "method": "tools/list", "id": 2}
        stdin = _make_stdin(_content_length_frame(msg1) + _content_length_frame(msg2))
        assert _read_message(stdin) == msg1
        assert _read_message(stdin) == msg2

    def test_malformed_length_continues(self):
        """Malformed Content-Length skips to next message, flag stays False."""
        bad = b"Content-Length: abc\r\n\r\n"
        good_msg = {"jsonrpc": "2.0", "id": 2}
        data = bad + json.dumps(good_msg).encode("utf-8") + b"\n"
        stdin = _make_stdin(data)
        result = _read_message(stdin)
        assert result == good_msg
        assert mcp_shared._use_content_length is False

    def test_invalid_json_in_content_length_frame_continues(self):
        """Invalid JSON body with correct Content-Length skips to next message."""
        bad = b"Content-Length: 5\r\n\r\n{bad}"
        good_msg = {"jsonrpc": "2.0", "id": 3}
        good = json.dumps(good_msg).encode("utf-8") + b"\n"
        stdin = _make_stdin(bad + good)
        result = _read_message(stdin)
        assert result == good_msg

    def test_true_truncation_continues(self):
        """Content-Length larger than available body consumes remaining bytes, skips to next."""
        # Claim 100 bytes but only provide 5 — read(100) returns short, json.loads fails
        bad = b"Content-Length: 100\r\n\r\n{bad}"
        good_msg = {"jsonrpc": "2.0", "id": 4}
        good = json.dumps(good_msg).encode("utf-8") + b"\n"
        stdin = _make_stdin(bad + good)
        # The truncated read consumes into the next message's bytes, so we get None (EOF)
        result = _read_message(stdin)
        assert result is None


class TestReadMessageBareJson:
    def setup_method(self):
        mcp_shared._use_content_length = False

    def test_reads_bare_json(self):
        msg = {"jsonrpc": "2.0", "method": "initialize", "id": 1}
        stdin = _make_stdin(json.dumps(msg).encode("utf-8") + b"\n")
        result = _read_message(stdin)
        assert result == msg
        assert mcp_shared._use_content_length is False

    def test_skips_invalid_json(self):
        good_msg = {"jsonrpc": "2.0", "id": 1}
        data = b"not json\n" + json.dumps(good_msg).encode("utf-8") + b"\n"
        stdin = _make_stdin(data)
        result = _read_message(stdin)
        assert result == good_msg

    def test_eof_returns_none(self):
        stdin = _make_stdin(b"")
        assert _read_message(stdin) is None

    def test_skips_blank_lines(self):
        msg = {"jsonrpc": "2.0", "id": 1}
        data = b"\n\n" + json.dumps(msg).encode("utf-8") + b"\n"
        stdin = _make_stdin(data)
        assert _read_message(stdin) == msg


class TestRespondFraming:
    def setup_method(self):
        mcp_shared._use_content_length = False

    def test_respond_bare_json(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            respond(1, {"ok": True})
        output = out.getvalue()
        assert output.endswith("\n")
        assert "Content-Length" not in output
        parsed = json.loads(output.strip())
        assert parsed["id"] == 1
        assert parsed["result"] == {"ok": True}

    def test_respond_content_length(self):
        mcp_shared._use_content_length = True
        out = io.BytesIO()
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.buffer = out
            respond(1, {"ok": True})
        output = out.getvalue()
        assert output.startswith(b"Content-Length:")
        header, body = output.split(b"\r\n\r\n", 1)
        length = int(header.split(b":")[1].strip())
        assert length == len(body)
        parsed = json.loads(body.decode("utf-8"))
        assert parsed["id"] == 1

    def test_respond_none_id_is_noop(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            respond(None, {"ok": True})
        assert out.getvalue() == ""
