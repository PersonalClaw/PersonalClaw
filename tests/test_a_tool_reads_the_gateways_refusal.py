"""A tool sees the sentence the gateway refused it with, not only the status line.

``urlopen`` raises on every 4xx and 5xx, and ``mcp_core``'s helpers answered with ``str()`` of
that: "HTTP Error 403: Forbidden". The route's own sentence, which says why and what to do, never
reached the tool. Computer use rendered "HTTP Error 403: Forbidden" where its refusal composes
WHAT, WHY and FIX for the model, and the lesson tool could never say why a lesson was not saved.
Driven here through a real loopback server, so ``urlopen`` raises what it raises.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from personalclaw import mcp_core, memory_writes
from personalclaw.computer_use import tools as computer_tools
from personalclaw.http_errors import json_error

_REFUSAL = json_error(
    "computer_use_refused",
    message="Terminal is not allowed.",
    status=403,
    error_extra={
        "agent_code": "ERR_APP_NOT_ALLOWED",
        "what": "Terminal is not allowed.",
        "why": "It is not on the list of apps computer use may drive.",
        "fix": "Add it to that list in Settings.",
    },
)

#: path → (status, body bytes)
_ANSWERS: dict[str, tuple[int, bytes]] = {
    computer_tools.DISPATCH_PATH: (_REFUSAL.status, _REFUSAL.body),
    "/structured": (_REFUSAL.status, _REFUSAL.body),
    "/api/lessons": (403, json.dumps({"error": memory_writes.REFUSAL}).encode()),
    "/sentence": (400, json.dumps({"ok": False, "error": "unknown session"}).encode()),
    "/nameless": (409, json.dumps({"ok": False, "refused": "It is already running."}).encode()),
    "/not-json": (502, b"<html>Bad Gateway</html>"),
    "/long": (502, b"x" * 5000),
    "/empty": (500, b""),
    "/fine": (200, json.dumps({"ok": True}).encode()),
}


@pytest.fixture
def gateway(monkeypatch):
    class _Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            status, body = _ANSWERS[self.path]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_DELETE = _answer

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(mcp_core, "_api_base", lambda: f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(mcp_core, "_internal_secret", lambda: "internal-secret")
    monkeypatch.setattr(mcp_core, "_resolve_session_key", lambda: "")
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


_CALLS = {
    "post": lambda path: mcp_core._post(path, {"a": 1}),
    "get": mcp_core._get,
    "delete": lambda path: mcp_core._delete(path, {"a": 1}),
}


@pytest.mark.parametrize("call", sorted(_CALLS))
def test_a_structured_refusal_is_its_sentence_and_keeps_its_object(gateway, call):
    answer = _CALLS[call]("/structured")
    assert answer["error"] == "Terminal is not allowed."
    assert answer["error_detail"]["code"] == "computer_use_refused"
    assert answer["error_detail"]["fix"] == "Add it to that list in Settings."


@pytest.mark.parametrize("call", sorted(_CALLS))
def test_a_refusal_sentence_is_kept_as_the_route_wrote_it(gateway, call):
    assert _CALLS[call]("/sentence") == {"ok": False, "error": "unknown session"}


def test_an_answer_that_names_no_error_is_kept_whole_and_still_reads_failed(gateway):
    answer = mcp_core._post("/nameless")
    assert answer["refused"] == "It is already running."
    assert answer["error"].startswith("HTTP 409: ")


def test_a_body_that_is_not_json_is_kept_as_text_capped(gateway):
    assert mcp_core._get("/not-json") == {"error": "HTTP 502: <html>Bad Gateway</html>"}
    assert mcp_core._get("/long") == {"error": "HTTP 502: " + "x" * mcp_core._ERROR_TEXT_CAP}


def test_an_empty_body_still_names_the_status(gateway):
    assert mcp_core._get("/empty") == {"error": "HTTP 500: Internal Server Error"}


def test_an_answer_that_is_not_an_error_is_unchanged(gateway):
    assert mcp_core._get("/fine") == {"ok": True}


def test_computer_use_renders_the_refusal_the_dispatch_composed(gateway):
    text = computer_tools._call_tool_inner("computer_snapshot", {"app": "Terminal"})
    assert text.startswith("Terminal is not allowed.\nIt is not on the list"), text
    assert "Add it to that list in Settings." in text
    assert "(code: ERR_APP_NOT_ALLOWED)" in text
    assert "HTTP Error 403" not in text


def test_the_lesson_tool_says_why_the_gateway_refused_the_lesson(gateway):
    from personalclaw import mcp_memory
    from personalclaw.tool_providers.base import ToolFailure

    out = mcp_memory._call_tool_inner(
        "memory_remember", {"rule": "Answer in French.", "category": "preference"}
    )
    assert isinstance(out, ToolFailure), "a lesson that was not saved is a failure, not a reply"
    assert out == f"Error: {memory_writes.REFUSAL}", out
