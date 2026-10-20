"""An MCP server's start is never cut off part-way: one still installing what it runs when its
connection stops waiting is left to finish, and once it has, it is looked at again.

🔴 THE DEFECT (measured before this change, with npm itself against a registry served on loopback).
A package runner (``npx``) installs what it runs the first time it runs it. The probe gave a server
15 seconds to answer and then stopped it with everything it started, so an install that took longer
was cut off part-way: a part-written file and npm's lock left in its cache and, for a package named
by its exact version, the package recorded there as installed when it never finished. Every later
start of it then exited at once ("Could not read package.json"), however long it was given, until
its cache folder was deleted by hand. Each start in the meantime counted as a failure, so the server
was soon stopped too. And once a probe did connect, the card went on carrying the failed probe's
sentence beside "ok".

Now a program that has not said a word when its connection stops waiting is left to finish (for up
to ``mcp_stdio.FINISH_SECS``) and its card says it is still starting; a start of the same program
meanwhile waits for it; once it ends, the server is probed again; and a probe that connects says
nothing of the one before. A program that has spoken is a running server and is stopped as before.

Every server here is a script this test wrote under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from test_an_mcp_server_card_says_how_it_stopped import (
    _agent_lists,
    _Fixture,
    _row,
    _save,
    _settled,
    _tools_page,
)

from personalclaw import mcp_client, mcp_discovery, mcp_stdio

# What a first start imports, imported before any test runs: on a busy machine the first import of
# the SDK can take longer than the short deadlines below, and a start that runs out of time before
# its program is even started is not what these tests are about.
_FIRST_START_IMPORTS = (
    "mcp.client.session",
    "mcp.shared.message",
    "personalclaw.mcp_elicitation",
    "personalclaw.sandbox",
)
for _module in _FIRST_START_IMPORTS:
    importlib.import_module(_module)

#: The parts every script below shares: replying to the MCP handshake and listing one tool.
_SERVE = """
import json, sys

def _send(message):
    sys.stdout.write(json.dumps(message) + "\\n")
    sys.stdout.flush()

def serve(*, list_tools=True):
    for line in sys.stdin:
        message = json.loads(line)
        method, msg_id = message.get("method"), message.get("id")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1.0.0"},
            }})
        elif method == "tools/list" and list_tools:
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [{
                "name": "hello", "description": "say hello",
                "inputSchema": {"type": "object", "properties": {}},
            }]}})
        elif msg_id is not None and method != "tools/list":
            _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": method}})
"""


def _write_script(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n" + _SERVE + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)
    return path


class _Installer:
    """A server that installs what it runs on its first start, as a package runner does: a half-done
    marker while it installs, the finished one when it is done, and from then on it starts at once
    and serves one tool. The install lasts until the test calls :meth:`finish_install` (or a minute,
    so a broken test cannot hang on it). It records each launch, and a launch that found another
    still installing."""

    def __init__(self, root: Path, name: str) -> None:
        self.launches_file = root / f"{name}.launches"
        self.partial = root / f"{name}.partial"
        self.installed = root / f"{name}.installed"
        self.overlap = root / f"{name}.overlap"
        self.release = root / f"{name}.release"
        self.script = _write_script(
            root / f"{name}_server.py",
            f"""
            import os, time

            with open({str(self.launches_file)!r}, "a", encoding="utf-8") as fh:
                fh.write("launched\\n")
            if not os.path.exists({str(self.installed)!r}):
                if os.path.exists({str(self.partial)!r}):
                    open({str(self.overlap)!r}, "w").write("two installs at once")
                open({str(self.partial)!r}, "w").write("half done")
                began = time.monotonic()
                while not os.path.exists({str(self.release)!r}):
                    if time.monotonic() - began > 60:
                        sys.exit(9)
                    time.sleep(0.05)
                open({str(self.installed)!r}, "w").write("done")
                os.unlink({str(self.partial)!r})
            serve()
            """,
        )

    def finish_install(self) -> None:
        self.release.touch()

    @property
    def launches(self) -> int:
        if not self.launches_file.exists():
            return 0
        return len(self.launches_file.read_text(encoding="utf-8").splitlines())

    def spec(self) -> dict[str, Any]:
        return {"command": str(self.script), "args": ["--fixture"]}


def _first_probe_waits(monkeypatch, secs: float) -> None:
    """The first probe waits *secs*; every later one the probe's own default, so a start that has
    finished installing answers whatever the load on the machine running the test."""
    deadlines = iter([secs])
    monkeypatch.setattr(mcp_discovery, "_get_probe_timeout", lambda: next(deadlines, 15))


class _Silent:
    """A server that never says a word and never ends, and writes down its pid."""

    def __init__(self, root: Path, name: str) -> None:
        self.pids = root / f"{name}.pids"
        self.script = _write_script(
            root / f"{name}_server.py",
            f"""
            import os, time

            with open({str(self.pids)!r}, "a", encoding="utf-8") as fh:
                fh.write(f"{{os.getpid()}}\\n")
            time.sleep(120)
            """,
        )

    def pid_list(self) -> list[int]:
        if not self.pids.exists():
            return []
        return [int(p) for p in self.pids.read_text(encoding="utf-8").split()]

    def spec(self) -> dict[str, Any]:
        return {"command": str(self.script), "args": ["--fixture"]}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _all_finished(timeout: float = 30.0) -> None:
    """Every program left to finish has ended, and every look it led to has landed."""
    deadline = time.monotonic() + timeout
    while mcp_stdio._finishing or mcp_discovery._RECHECKS:
        assert time.monotonic() < deadline, "what was left to finish never ended"
        await asyncio.sleep(0.05)


async def _gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while _alive(pid):
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


@pytest.mark.asyncio
async def test_a_first_start_still_installing_is_left_to_finish_and_then_looked_at(
    tmp_path, monkeypatch
) -> None:
    server = _Installer(tmp_path, "notes")
    _first_probe_waits(monkeypatch, 3)
    async with _tools_page(monkeypatch) as http:
        await _save(http, "notes", server.spec())
        await _settled(http)

        row = await _row(http, "notes")
        assert row["status"] == "probing", row
        assert "has not answered after 3 seconds and is still starting" in row["error"], row
        assert "installs what it runs" in row["error"] and "10 minutes" in row["error"], row
        assert server.partial.exists() and server.launches == 1
        assert mcp_discovery._probe_cache["notes"].failures == 0, "a start still going counted"

        server.finish_install()
        await _all_finished()

        assert server.installed.exists(), "the install was cut off part-way"
        assert not server.partial.exists(), "the install left a half-done part behind"
        assert server.launches == 2, "one start to install, one look once it had"
        looked = mcp_discovery._probe_cache["notes"]
        assert (looked.status, looked.error, looked.detail) == ("ok", "", ""), looked
        assert [t["name"] for t in looked.tools] == ["hello"]
        assert (await _row(http, "notes"))["status"] == mcp_discovery.UNSERVED  # no agent here


@pytest.mark.asyncio
async def test_a_start_meanwhile_waits_for_the_one_still_installing(tmp_path, monkeypatch) -> None:
    server = _Installer(tmp_path, "notes")
    _first_probe_waits(monkeypatch, 3)
    monkeypatch.setattr(mcp_client, "_CONNECT_TIMEOUT_SECS", 30.0)
    async with _tools_page(monkeypatch) as http:
        await _save(http, "notes", server.spec())
        await _settled(http)
        assert server.launches == 1 and server.partial.exists()

        # An agent's turn while the install runs: it waits for it, then starts what is installed.
        turn = asyncio.ensure_future(_agent_lists("notes", "chat-1"))
        await asyncio.sleep(1.0)
        assert not turn.done() and server.launches == 1, "it started beside the one installing"
        server.finish_install()
        listed = await turn
        assert [tool.name for tool in listed] == ["hello"]
        assert not server.overlap.exists(), "a second start ran beside the one still installing"
        await _all_finished()
        assert server.installed.exists() and not server.partial.exists()


@pytest.mark.asyncio
async def test_a_start_that_waited_out_its_own_deadline_says_so(tmp_path, monkeypatch) -> None:
    server = _Installer(tmp_path, "notes")
    _first_probe_waits(monkeypatch, 3)
    monkeypatch.setattr(mcp_client, "_CONNECT_TIMEOUT_SECS", 1.0)
    async with _tools_page(monkeypatch) as http:
        await _save(http, "notes", server.spec())
        await _settled(http)

        assert await _agent_lists("notes", "chat-1") == []
        conn = mcp_client.get_mcp_client_registry().get("notes", "chat-1")
        assert conn is not None
        assert "still finishing an earlier start" in conn.error, conn.error
        assert server.launches == 1, "the waiting start started a second program"
        assert mcp_discovery._probe_cache["notes"].failures == 0
        server.finish_install()
        await _all_finished()
        assert server.installed.exists() and not server.partial.exists()


@pytest.mark.asyncio
async def test_a_probe_that_connects_says_nothing_of_the_one_that_failed(
    tmp_path, monkeypatch
) -> None:
    """🔴 Red before: the server came out of `list_servers` carrying its last probe's sentence and
    error output, and a probe that connected replaced only its status and tools."""
    server = _Fixture(tmp_path, "builds", "exit")
    async with _tools_page(monkeypatch) as http:
        await _save(http, "builds", server.spec())
        await _settled(http)
        assert "exited with code 3" in (await _row(http, "builds"))["error"]

        server.fixed.touch()
        recheck = mcp_discovery.recheck(
            ["builds"], hold=http.app["state"]._background_tasks, forget=False
        )
        assert recheck is not None
        await recheck

        looked = mcp_discovery._probe_cache["builds"]
        assert (looked.status, looked.error, looked.detail) == ("ok", "", ""), looked


@pytest.mark.asyncio
async def test_a_server_that_spoke_and_then_stalled_is_stopped_as_before(
    tmp_path, monkeypatch
) -> None:
    """Not every server still running at its deadline is installing: one that answered its
    handshake is running, and one that then never lists its tools did not answer."""
    pids = tmp_path / "stalls.pids"
    script = _write_script(
        tmp_path / "stalls_server.py",
        f"""
        import os

        with open({str(pids)!r}, "a", encoding="utf-8") as fh:
            fh.write(f"{{os.getpid()}}\\n")
        serve(list_tools=False)
        import time
        time.sleep(120)
        """,
    )
    monkeypatch.setattr(mcp_discovery, "_get_probe_timeout", lambda: 5)
    async with _tools_page(monkeypatch) as http:
        await _save(http, "stalls", {"command": str(script), "args": ["--fixture"]})
        await _settled(http)
        row = await _row(http, "stalls")
        assert row["status"] == "error", row
        assert "did not answer within 5 seconds, so PersonalClaw stopped it" in row["error"], row
        [pid] = [int(p) for p in pids.read_text(encoding="utf-8").split()]
        assert await _gone(pid), "a server that stalled was left running"


@pytest.mark.asyncio
async def test_a_program_left_to_finish_is_stopped_once_its_time_is_up(
    tmp_path, monkeypatch
) -> None:
    server = _Silent(tmp_path, "mute")
    monkeypatch.setattr(mcp_discovery, "_get_probe_timeout", lambda: 1)
    monkeypatch.setattr(mcp_stdio, "FINISH_SECS", 1.0)
    async with _tools_page(monkeypatch) as http:
        await _save(http, "mute", server.spec())
        await _settled(http)
        [first] = server.pid_list()
        assert _alive(first), "it was stopped as the probe gave up, not left to finish"

        await _all_finished()

        assert await _gone(first), "it outlived the time it was given to finish"
        # It was looked at again once stopped; a server left once that has not answered since is
        # not installing, and is stopped and said not to answer.
        pids = server.pid_list()
        assert len(pids) == 2 and await _gone(pids[1]), pids
        row = await _row(http, "mute")
        assert row["status"] == "error", row
        assert "did not answer within 1 second, so PersonalClaw stopped it" in row["error"], row

        # Its owner's Retry gives a start its time again: one that is installing by then is not cut
        # off for an earlier one that never answered.
        retried = await http.post("/api/mcp/probe/mute")
        assert retried.status == 200, await retried.text()
        assert (await _row(http, "mute"))["status"] == "probing"
        third = server.pid_list()[-1]
        assert len(server.pid_list()) == 3 and _alive(third), server.pid_list()
        await _all_finished()
        assert all([await _gone(pid) for pid in server.pid_list()]), "something outlived its time"


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["the gateway stops", "the server goes"])
async def test_a_program_left_to_finish_does_not_outlive(how, tmp_path, monkeypatch) -> None:
    server = _Silent(tmp_path, "mute")
    monkeypatch.setattr(mcp_discovery, "_get_probe_timeout", lambda: 1)
    async with _tools_page(monkeypatch) as http:
        await _save(http, "mute", server.spec())
        await _settled(http)
        [pid] = server.pid_list()
        assert _alive(pid)

        if how == "the gateway stops":
            await mcp_client.get_mcp_client_registry().shutdown_all()
        else:
            mcp_client.close_servers(lambda name: name == "mute")

        assert await _gone(pid), f"it went on running after {how}"
        await _all_finished()
        assert server.pid_list() == [pid], "it was started again after it was stopped"
