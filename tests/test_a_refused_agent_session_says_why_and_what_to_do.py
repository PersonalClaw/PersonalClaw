"""A runtime's Test that fails says why: the agent's own words, how its program ended, what to do.

Driven through the real readiness probe (a runtime card's Test) against ``scripted_acp_agent.py``
``session-refused``: an agent that answers ``session/new`` with a JSON-RPC error carrying a message
and data, says why on stderr, and exits with code 64. Red before the fix: the card read
"handshake failed: session/new returned no sessionId (result=None)" followed by the adapter's own
progress lines — the error's message and data were dropped, and nothing said that the program
exited, with what code, or what to do about it.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from scripted_acp_agent import DYING_STDERR, REFUSAL_DATA, REFUSAL_MESSAGE, REFUSAL_STDERR

from personalclaw.acp.client import AcpClient
from personalclaw.acp.dialect import CodexDialect
from personalclaw.acp.errors import AcpProcessDied
from personalclaw.llm.acp_agent import AcpAgentProvider

AGENT = Path(__file__).with_name("scripted_acp_agent.py")


@pytest.mark.asyncio
async def test_a_refused_session_says_the_agents_words_its_exit_and_what_to_do(tmp_path, caplog):
    options = {
        "command": [sys.executable, str(AGENT), "session-refused", str(tmp_path / "wire.jsonl")],
        "dialect": "codex",
        # The adapter hands the turn to an engine program; the bundle declares which.
        "requires_executable": {"label": "example-engine", "path": sys.executable},
    }
    with caplog.at_level(logging.WARNING):
        status = await AcpAgentProvider.probe_readiness(options)

    assert status.state == "error"
    detail = status.detail
    assert "returned no sessionId" not in detail and "result=None" not in detail
    # The error's own message and data.
    assert REFUSAL_MESSAGE in detail and REFUSAL_DATA in detail, detail
    # How the program ended, and what it said last.
    assert f"{Path(sys.executable).name} exited with code 64" in detail, detail
    assert REFUSAL_STDERR in detail, detail
    # What to do, naming the program the bundle says does the work.
    assert "example-engine" in detail and "version" in detail, detail
    # The gateway log says the same.
    assert detail in caplog.text


def test_a_json_rpc_error_keeps_its_message_and_data_and_masks_what_it_repeats():
    """Every ACP call's error is said in the agent's words: its message and its data. An agent
    can repeat what a program it ran printed, so the words are masked like any child's output."""
    from personalclaw.acp.errors import AcpRequestError, rpc_error_words

    error = {
        "code": -32603,
        "message": "Internal error",
        "data": {"details": "token=ghp_" + "a" * 36 + " was refused", "empty": ""},
    }
    words = rpc_error_words(error)
    assert words.startswith("Internal error — details: token=")
    assert "a" * 36 not in words, "a credential the agent repeated reached the sentence"
    assert "empty" not in words
    refused = AcpRequestError("session/new", error)
    assert str(refused) == f"session/new was refused: {words}"
    assert refused.code == -32603
    assert rpc_error_words({"code": 1}) == "no message (code 1)"


def test_an_exit_is_said_with_what_its_code_means():
    from personalclaw.acp.errors import exit_words

    assert exit_words("example-cli", 64, "") == (
        "example-cli exited with code 64 (a usage error: it was handed an argument or option it "
        "does not accept)"
    )
    assert exit_words("example-cli", 2, "bad flag") == (
        "example-cli exited with code 2; its last output: bad flag"
    )
    assert exit_words("example-cli", -9) == "example-cli was ended by signal 9"
    assert exit_words("example-cli", 1, "x" * 2000).endswith("…" + "x" * 600)


@pytest.mark.asyncio
async def test_an_agent_that_exits_mid_turn_is_said_to_with_its_exit_and_last_words(
    tmp_path, monkeypatch
):
    """The turn's error — the one the chat logs — says the process ended, with what code and
    what it printed last. Red before the fix: the closed connection read "ACP prompt timed out"."""
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    client = AcpClient(
        command=[sys.executable, str(AGENT), "dies-mid-turn", str(tmp_path / "wire.jsonl")],
        work_dir=tmp_path / "work",
        dialect=CodexDialect(),
    )
    try:
        with pytest.raises(AcpProcessDied) as died:
            async for _event in client.stream_events("review the last commit"):
                pass
    finally:
        await client.shutdown()
    said = str(died.value)
    assert said.startswith("the agent's process ended before it finished the turn"), said
    assert f"{Path(sys.executable).name} exited with code 3" in said, said
    assert DYING_STDERR in said, said
