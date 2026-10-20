"""A tool call to the gateway that runs out of time says which call and after how long.

The agent's own tools reach the gateway over HTTP (``mcp_core._get`` / ``_post`` / ``_delete``).
When one ran out of time, the error was ``str()`` of the socket's timeout, "timed out", so the
agent read "Error: timed out" and nothing else, and nothing was logged on either side: four
``memory_recall`` calls in a row failed that way and the gateway log held no line for any of them.

A timed-out call now answers a sentence naming the call and its budget, logged once as a WARNING
(the path without its query: what was asked stays out of the log), and ``memory_recall`` tells the
agent what happened and what still holds.
"""

from __future__ import annotations

import logging
import urllib.error

import pytest

from personalclaw import mcp_core, mcp_memory
from personalclaw.tool_providers.base import ToolFailure


@pytest.fixture
def unanswered(monkeypatch):
    """A gateway that never answers in time: every request raises the socket's timeout."""
    raised: list[BaseException] = [TimeoutError("timed out")]

    def _urlopen(req, timeout=None):  # noqa: ARG001 - the signature the gateway's transport takes
        raise raised[0]

    monkeypatch.setattr(mcp_core, "_api_base", lambda: "http://127.0.0.1:9")
    monkeypatch.setattr(mcp_core, "_internal_headers", lambda extra=None: dict(extra or {}))
    monkeypatch.setattr(mcp_core.home_gateway, "open_loopback", _urlopen)
    return raised


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize(
    "timeout",
    [TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))],
    ids=["while-reading", "while-connecting"],
)
@pytest.mark.parametrize(
    "call, method, budget",
    [
        (lambda: mcp_core._get("/api/memory/recall?q=kitchen+quotes"), "GET", "10"),
        (lambda: mcp_core._post("/api/lessons?scope=global", {"rule": "x"}), "POST", "30"),
        (lambda: mcp_core._delete("/api/lessons?x=kitchen", {"rule": "x"}), "DELETE", "10"),
    ],
    ids=["get", "post", "delete"],
)
def test_a_gateway_call_that_runs_out_of_time_names_the_call_and_its_budget(
    unanswered, caplog, timeout, call, method, budget
):
    """🔴 Red before: the answer was ``{"error": "timed out"}`` and nothing was logged."""
    unanswered[0] = timeout
    caplog.set_level(logging.WARNING)

    answer = call()

    path = "/api/memory/recall" if method == "GET" else "/api/lessons"
    assert answer["error"] == f"the gateway did not answer {method} {path} within {budget} s"
    assert answer["timed_out"] is True
    logged = _warnings(caplog)
    assert len(logged) == 1, logged
    assert f"{method} {path}" in logged[0] and f"{budget} s" in logged[0]
    assert "kitchen" not in logged[0] and "kitchen" not in answer["error"], "never what was asked"


def test_memory_recall_that_runs_out_of_time_tells_the_agent_what_happened(unanswered):
    """🔴 Red before: "Error: timed out"."""
    out = mcp_memory._call_tool_inner("memory_recall", {"query": "kitchen must-haves"})

    assert isinstance(out, ToolFailure)
    assert str(out) == (
        "Error: memory search did not answer within 10 s, so nothing was recalled this time. "
        "The memories are intact; ask again in a moment."
    )


def test_memory_recall_passes_on_the_routes_own_sentence_when_the_route_ran_out_of_time(
    monkeypatch,
):
    """When the recall itself ran out of its budget, the route says where it was; the agent is
    handed that sentence as the route wrote it."""
    sentence = (
        "Memory search did not finish within 8 s (it was still searching past conversations), "
        "so nothing was recalled this time. The memories are intact; ask again in a moment."
    )
    monkeypatch.setattr(
        mcp_memory,
        "_get",
        lambda path: {
            "error": sentence,
            "error_detail": {"code": "memory_recall_timeout", "message": sentence},
        },
    )
    out = mcp_memory._call_tool_inner("memory_recall", {"query": "kitchen"})

    assert isinstance(out, ToolFailure)
    assert str(out) == f"Error: {sentence}"
