"""Tests for ACP types."""

from personalclaw.acp.types import AcpPromptStats, JsonRpcMessage, JsonRpcRequest


class TestJsonRpcRequest:
    def test_to_dict(self):
        req = JsonRpcRequest(method="initialize", params={"key": "val"}, id=1)
        d = req.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["id"] == 1
        assert d["method"] == "initialize"
        assert d["params"] == {"key": "val"}


class TestJsonRpcMessage:
    def test_is_response_for_matching(self):
        msg = JsonRpcMessage(id=42, result={"ok": True})
        assert msg.is_response_for(42)

    def test_is_response_for_non_matching(self):
        msg = JsonRpcMessage(id=42, result={"ok": True})
        assert not msg.is_response_for(99)

    def test_is_method_matching(self):
        msg = JsonRpcMessage(method="session/update")
        assert msg.is_method("session/update")

    def test_is_method_non_matching(self):
        msg = JsonRpcMessage(method="session/update")
        assert not msg.is_method("session/prompt")

    def test_is_method_none(self):
        msg = JsonRpcMessage()
        assert not msg.is_method("anything")


class TestAcpPromptStats:
    def test_defaults(self):
        stats = AcpPromptStats()
        assert stats.event_count == 0
        assert stats.text_chunks == 0
        assert stats.tool_calls == []
        assert stats.context_pct == 0.0
