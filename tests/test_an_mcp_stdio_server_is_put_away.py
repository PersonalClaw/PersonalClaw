"""An MCP server PersonalClaw starts as a program is put away when its connection closes, whatever
it does, and PersonalClaw knows how it ended (`mcp_stdio`).

The probe and an agent's connection both start a stdio server through ``mcp_stdio.stdio_streams``.
On the way out the server's input is closed, which asks it to exit. A server that does is left to
exit; one that does not is stopped, with everything it started (its own process group), in bounded
time. Either way the run says how it ended (its exit code, and whether PersonalClaw had to stop it)
and keeps the tail of what it wrote to its error output, which is what its card says.

Every server here is a script this test wrote under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from personalclaw import mcp_status
from personalclaw.mcp_stdio import CommandNotFound, StdioRun, stdio_streams


def _script(root: Path, name: str, body: str) -> Path:
    path = root / f"{name}.py"
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)
    return path


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_a_server_that_exits_when_its_input_closes_is_left_to(tmp_path) -> None:
    script = _script(
        tmp_path,
        "polite",
        """
        import sys
        sys.stdin.read()
        sys.stderr.write("bye\\n")
        """,
    )
    run = StdioRun()
    async with stdio_streams("polite", {"command": str(script)}, run=run):
        pass
    assert run.returncode == 0 and not run.stopped and run.exited
    assert run.stderr.strip() == "bye"


@pytest.mark.asyncio
async def test_a_server_that_will_not_exit_is_stopped_with_what_it_started(tmp_path) -> None:
    pids = tmp_path / "pids"
    script = _script(
        tmp_path,
        "stubborn",
        f"""
        import os, signal, subprocess, sys, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        with open({str(pids)!r}, "w") as fh:
            fh.write(f"{{os.getpid()}} {{child.pid}}")
        time.sleep(120)
        """,
    )
    run = StdioRun()
    began = time.monotonic()
    async with stdio_streams("stubborn", {"command": str(script)}, run=run):
        deadline = time.monotonic() + 15
        while not pids.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
    took = time.monotonic() - began
    server_pid, child_pid = (int(p) for p in pids.read_text().split())
    assert run.stopped and not run.exited and run.returncode is not None
    assert not _alive(server_pid) and not _alive(child_pid), "something it started outlived it"
    assert took < 20, f"putting it away took {took:.1f}s"


@pytest.mark.asyncio
async def test_only_the_tail_of_its_error_output_is_kept(tmp_path) -> None:
    script = _script(
        tmp_path,
        "chatty",
        f"""
        import sys
        for i in range({mcp_status.STDERR_TAIL_BYTES}):
            sys.stderr.write(f"noise {{i}}\\n")
        sys.stderr.write("the last line\\n")
        sys.exit(2)
        """,
    )
    run = StdioRun()
    async with stdio_streams("chatty", {"command": str(script)}, run=run):
        pass
    assert run.returncode == 2 and run.exited
    assert len(run.stderr.encode()) <= mcp_status.STDERR_TAIL_BYTES
    assert run.stderr.rstrip().endswith("the last line")


@pytest.mark.asyncio
async def test_a_command_that_is_not_there_starts_nothing(tmp_path) -> None:
    run = StdioRun()
    with pytest.raises(CommandNotFound) as raised:
        async with stdio_streams("gone", {"command": "/nonexistent/pc-fixture-mcp"}, run=run):
            pass
    assert raised.value.command == "/nonexistent/pc-fixture-mcp"
    assert run.returncode is None


def test_the_cause_line_is_what_went_wrong_not_where() -> None:
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "/home/user/.cache/build/core.py", line 184, in run_commands\n'
        "    self.run_command(cmd)\n"
        "ModuleNotFoundError: No module named 'native_ext'\n"
    )
    assert mcp_status.cause_line(traceback) == "ModuleNotFoundError: No module named 'native_ext'"
    node = (
        "node:internal/modules/cjs/loader:1228\n"
        "Error: Cannot find module 'left-pad'\n"
        "    at Module._resolveFilename (node:internal/modules/cjs/loader:1225:15)\n"
        "Node.js v20.11.0\n"
    )
    assert mcp_status.cause_line(node) == "Error: Cannot find module 'left-pad'"
    tool = "  × Failed to build `native-ext==7.2`\n  ╰─▶ the build backend returned an error\n"
    assert mcp_status.cause_line(tool) == "Failed to build `native-ext==7.2`"
    assert mcp_status.cause_line("listening on stdio\n") == "listening on stdio"
    assert mcp_status.cause_line("") == ""


def test_an_exit_with_nothing_to_say_says_so() -> None:
    said = mcp_status.exited("notes", 1, "")
    assert (
        said.headline
        == "notes exited with code 1 before it answered, and wrote nothing to say why."
    )
    assert mcp_status.exited("notes", -9, "").headline.startswith("notes was ended by signal 9")
