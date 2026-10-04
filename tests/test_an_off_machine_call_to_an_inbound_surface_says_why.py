"""A program on another machine calling an inbound surface is told why it was refused, and what
would reach it.

The dashboard refuses a state-changing request from another address that carries no browser
origin, and it does so before any inbound surface reads the request. An MCP client (or any other
program) sends no origin, so from another machine every call to ``/mcp``, ``/v1/…``, ``/a2a/…`` and
``/capture/…`` was answered "The request origin is not allowed on this instance." — a sentence about
browsers that named neither the cause nor a way through. The refusal is the same (403, the same
code); the sentence now says the surface takes requests only from programs on PersonalClaw's own
machine, where this one came from, and the two ways to reach it. A request from a web page keeps
the origin sentence, and so does every other route.

The webhook's two doors (``/api/triggers/<id>/fire`` and ``/api/hooks/agent``) are answered the
same way, and every such refusal is a row of the inbound audit and of the Security log, as a
refusal the surface makes itself is: refused before the surface read it, it left no row at all.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.origin import origin_refusal
from personalclaw.http_errors import HTTP_ERROR_CODES

ORIGIN_SENTENCE = HTTP_ERROR_CODES["auth_origin_not_allowed"]


def _request(path: str, *, peer: str = "192.0.2.10", headers: dict | None = None):
    transport = MagicMock()
    transport.get_extra_info.side_effect = lambda name, default=None: (
        (peer, 52100) if name == "peername" else default
    )
    return make_mocked_request("POST", path, headers=headers or {}, transport=transport)


def _error(response) -> dict:
    assert response.status == 403
    return json.loads(response.body)["error"]


@pytest.mark.parametrize(
    ("path", "label"),
    [
        ("/mcp", "MCP surface"),
        ("/v1/chat/completions", "OpenAI-compatible API"),
        ("/a2a/tasks", "A2A gateway"),
        ("/capture/v1/messages", "Capture proxy"),
        ("/api/triggers/store:webhook:build-finished/fire", "webhook"),
        ("/api/hooks/agent", "webhook"),
    ],
)
def test_a_program_on_another_machine_is_told_the_surface_answers_only_this_one(path, label):
    error = _error(origin_refusal(_request(path)))
    assert error["code"] == "auth_origin_not_allowed"
    message = error["message"]
    assert f"PersonalClaw's {label} takes requests only from programs on the machine" in message
    assert "this one came from 192.0.2.10" in message
    # What would reach it, in the two ways that do.
    assert "connect to 127.0.0.1" in message and "over SSH" in message
    assert message != ORIGIN_SENTENCE


@pytest.mark.parametrize(
    ("path", "peer", "headers"),
    [
        # Not an inbound surface: the browser-origin refusal it always was.
        ("/api/config/personalclaw", "192.0.2.10", {}),
        ("/mcpx", "192.0.2.10", {}),
        # A web page, from another machine or from this one: an origin WAS refused.
        ("/mcp", "192.0.2.10", {"Origin": "https://elsewhere.example"}),
        ("/mcp", "127.0.0.1", {"Origin": "https://elsewhere.example"}),
        ("/v1/chat/completions", "192.0.2.10", {"Referer": "https://elsewhere.example/page"}),
    ],
)
def test_every_other_refusal_keeps_the_origin_sentence(path, peer, headers):
    error = _error(origin_refusal(_request(path, peer=peer, headers=headers)))
    assert error == {"code": "auth_origin_not_allowed", "message": ORIGIN_SENTENCE}


@pytest.mark.parametrize(
    ("path", "surface"),
    [
        ("/mcp", "mcp"),
        ("/api/triggers/store:webhook:build-finished/fire", "webhook"),
        ("/api/hooks/agent", "webhook"),
    ],
)
def test_the_refusal_is_a_row_of_the_inbound_audit_and_of_the_security_log(
    path, surface, tmp_path, monkeypatch
):
    """🔴 Red before: refused before any surface read it, the call left no row in either, so the
    refusal an operator looks for in Settings → External Access and the Security log was silent."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))

    _error(origin_refusal(_request(path)))

    (row,) = _rows(tmp_path / "inbound_audit.jsonl")
    assert (row["surface"], row["route"], row["status"]) == (surface, f"POST {path}", 403)
    assert row["refused_reason"]
    denied = [
        e
        for e in _rows(tmp_path / "security_events.jsonl")
        if e.get("outcome") == "denied" and e.get("caller_identity") == f"inbound:{surface}"
    ]
    assert len(denied) == 1, denied


def test_a_refusal_of_any_other_request_writes_no_inbound_row(tmp_path, monkeypatch):
    """Control: a web page's refused origin, and a route that is no inbound surface, are not calls
    to a surface, so neither is a row of its audit."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))

    _error(origin_refusal(_request("/mcp", headers={"Origin": "https://elsewhere.example"})))
    _error(origin_refusal(_request("/api/config/personalclaw")))

    assert _rows(tmp_path / "inbound_audit.jsonl") == []


def _rows(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_the_dashboard_answers_its_refusals_with_origin_refusal():
    """The middleware is a closure inside `start_dashboard`, so this pins, at the source, that the
    refusal it sends is the one tested above."""
    from personalclaw.dashboard import server

    src = inspect.getsource(server)
    at = src.find("async def csrf_middleware")
    assert at != -1
    assert "return origin_refusal(request)" in src[at : at + 1400]
