"""The tool server an agent CLI starts answers in a home it can add nothing to, and leaves the
gateway's log to the gateway.

An agent CLI starts PersonalClaw's tool server (``personalclaw mcp-core``) from what the client
declares to it, and the server runs wherever the CLI runs: in the CLI's sandbox, which on Linux
lets nothing be added at the top of the home. The server's start opened the gateway's own rotating
log, ``gateway.log``, as every other command does, so in a home that had none yet it ended before
it answered a call, and in any other it was a second writer of the gateway's log, free to rotate
it. A home whose folder its owner may not write stands in for the sandbox here: nothing can be
added at its top, and what is already in it is still written. Each server is started as the client
declares it and spoken to over stdio, as the CLI does.

For the same reason a folder the server's tools write in has to be there before the CLI starts: the
skills folder, which only a gateway's start (seeding the shipped skills) or a first save had made,
is made when an agent CLI is launched. A draft the server cannot write says so: it said the draft
was empty or the session's draft limit was reached.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw import mcp_core
from personalclaw.acp.mcp_servers import core_mcp_servers
from personalclaw.acp.transport import AcpProcess
from personalclaw.skills import ephemeral

CHAT = "dashboard:chat-1"


def _free_port() -> int:
    """A loopback port nothing listens on: the gateway these servers name is not running."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("PERSONALCLAW_PORT", str(_free_port()))
    return home


def _ask(*requests: dict) -> tuple[list[dict], str]:
    """Start the tool server as an agent CLI starts it from the client's declaration, send it
    *requests*, close its input, and return its answers and what it wrote to stderr."""
    (server,) = core_mcp_servers(session_key=CHAT)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ["HOME"]}
    env.update({entry["name"]: entry["value"] for entry in server["env"]})
    frames = "".join(
        json.dumps({"jsonrpc": "2.0", "id": number, **request}) + "\n"
        for number, request in enumerate(requests, 1)
    )
    run = subprocess.run(
        [server["command"], *server["args"]],
        input=frames,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    answers = [json.loads(line) for line in run.stdout.splitlines() if line.strip()]
    return answers, run.stderr


INITIALIZE = {"method": "initialize", "params": {}}


def test_the_tool_server_answers_in_a_home_it_can_add_nothing_to(home: Path):
    """🔴 Red on integration: the server could not open a gateway.log the home did not have, and
    ended before it answered."""
    home.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        answers, stderr = _ask(INITIALIZE)
    finally:
        home.chmod(0o700)

    assert [answer.get("id") for answer in answers] == [1], stderr
    assert answers[0]["result"]["serverInfo"]["name"] == "personalclaw-core"
    assert not (home / "gateway.log").exists()


def test_the_tool_server_leaves_the_gateways_log_to_the_gateway(home: Path):
    """🔴 Red on integration: the server made the gateway's log, as a writer of its own."""
    answers, stderr = _ask(INITIALIZE, {"method": "tools/list", "params": {}})

    assert [answer.get("id") for answer in answers] == [1, 2], stderr
    assert answers[1]["result"]["tools"]
    assert not (home / "gateway.log").exists()


@pytest.mark.asyncio
async def test_an_agent_clis_start_makes_the_folder_its_skill_tools_write_in(
    home: Path, tmp_path: Path
):
    """🔴 Red on integration: nothing made the skills folder before the CLI started, so in the Linux
    sandbox its tool server could save no draft and no skill in a home that had none yet."""
    seen = tmp_path / "seen.txt"
    look = "import os, sys; open(sys.argv[2], 'w').write(str(os.path.isdir(sys.argv[1])))"
    work = tmp_path / "work"
    agent = AcpProcess(
        command=[sys.executable, "-c", look, str(home / "skills"), str(seen)],
        work_dir=work,
        sandbox_mode="off",
    )
    with patch("personalclaw.session._track_pid"), patch("personalclaw.session._track_session_pid"):
        await agent.spawn()
        await asyncio.wait_for(agent.process.wait(), timeout=60)
    agent.teardown()

    assert seen.read_text() == "True"
    assert stat.S_IMODE((home / "skills").stat().st_mode) == 0o700
    (home / "skills" / "kept.md").write_text("a skill already here")
    agent = AcpProcess(command=[sys.executable, "-c", "pass"], work_dir=work, sandbox_mode="off")
    with patch("personalclaw.session._track_pid"), patch("personalclaw.session._track_session_pid"):
        await agent.spawn()
        await asyncio.wait_for(agent.process.wait(), timeout=60)
    agent.teardown()
    assert (home / "skills" / "kept.md").read_text() == "a skill already here"


def test_a_draft_that_cannot_be_written_says_so(home: Path, monkeypatch: pytest.MonkeyPatch):
    """🔴 Red on integration: the failed write read as an empty draft or a full session."""

    def refused(path, *_args, **_kwargs):
        raise OSError(errno.EROFS, os.strerror(errno.EROFS), str(path))

    monkeypatch.setattr(ephemeral, "atomic_write", refused)
    token = mcp_core.set_current_session_key(CHAT)
    try:
        answer = mcp_core._call_tool(
            "skill_remember", {"title": "Release checklist", "body": "Read the changelog first."}
        )
    finally:
        mcp_core.reset_current_session_key(token)

    assert "could not be written" in answer and os.strerror(errno.EROFS) in answer, answer
    assert "limit" not in answer and "empty" not in answer, answer
    assert ephemeral.list_drafts(CHAT) == []


def test_a_draft_is_still_saved_and_the_limit_still_said(home: Path):
    """The control: an ordinary draft is saved, and a new one past the session's limit is refused
    in the words of the limit."""
    token = mcp_core.set_current_session_key(CHAT)
    try:
        saved = mcp_core._call_tool(
            "skill_remember", {"title": "Release checklist", "body": "Read the changelog first."}
        )
        for number in range(ephemeral._MAX_DRAFTS_PER_SESSION):
            ephemeral.remember(CHAT, f"Draft {number}", "A step.")
        refused = mcp_core._call_tool(
            "skill_remember", {"title": "One too many", "body": "Another step."}
        )
    finally:
        mcp_core.reset_current_session_key(token)

    assert saved.startswith("Saved a session skill draft: 'Release checklist'"), saved
    assert "draft limit was reached" in refused and "could not be written" not in refused, refused
