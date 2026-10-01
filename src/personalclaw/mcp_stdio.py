"""Starting an MCP server PersonalClaw runs as a program: the session's two streams over the
program's stdin and stdout, and how the program ended.

The SDK's own stdio client starts the program and keeps it to itself. A server that exited before it
answered read only as "Connection closed", its exit code was lost, and what it wrote to its error
output went into the gateway's own output, where nobody looking at the server would find it.
This starts it PersonalClaw's way: through the resource ceiling
(`sandbox.create_subprocess_limited`), in a process group of its own so that stopping it stops
whatever it started too, with the SDK session given the same two streams the SDK's client gives
it. It keeps the tail of the program's error output and knows how the program ended
(:class:`StdioRun`), which is what says why a start failed (`mcp_status`).

The probe and an agent's connection both start a server through here (`mcp_client.McpServerConn`),
so a server that probes "ok" is one an agent can start, and both say the same thing when it cannot.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from personalclaw.mcp_status import STDERR_TAIL_BYTES

#: How long a server has to end on its own once its input is closed, before it is stopped.
_END_GRACE_SECS = 2.0
#: How long the rest of its error output may take to arrive once it has ended.
_DRAIN_SECS = 1.0
#: How much of the server's output is read at a time. A message is one line, of any length.
_READ_CHUNK = 64 * 1024


@dataclass
class StdioRun:
    """One start of a stdio server: how it ended, and the tail of what it wrote to its error
    output. Filled in as it runs; final once its streams are closed."""

    #: Its exit code once it ended (negative: the signal that ended it), else ``None``.
    returncode: int | None = None
    #: It did not end on its own once its input was closed, so PersonalClaw stopped it.
    stopped: bool = False
    _stderr: bytearray = field(default_factory=bytearray)

    def _keep(self, chunk: bytes) -> None:
        self._stderr += chunk
        if len(self._stderr) > STDERR_TAIL_BYTES:
            del self._stderr[: len(self._stderr) - STDERR_TAIL_BYTES]

    @property
    def stderr(self) -> str:
        """The tail of its error output (:data:`~personalclaw.mcp_status.STDERR_TAIL_BYTES`)."""
        return self._stderr.decode("utf-8", "replace")

    @property
    def exited(self) -> bool:
        """Whether it ended on its own: PersonalClaw did not stop it."""
        return self.returncode is not None and not self.stopped


class CommandNotFound(Exception):
    """Nothing was started: the server's command is not there (`mcp_status.command_not_found`)."""

    def __init__(self, command: str) -> None:
        super().__init__(f"command not found: {command}")
        self.command = command


def resolve_command(command: str, *, env: Mapping[str, str], cwd: str | None) -> str | None:
    """The program *command* names, as the server is started: on the ``PATH`` of its environment,
    or, for a relative path, under the folder it starts in. ``None`` when there is none."""
    if os.sep in command and not os.path.isabs(command) and cwd:
        command = os.path.join(cwd, command)
    return shutil.which(command, path=env.get("PATH"))


@contextlib.asynccontextmanager
async def stdio_streams(
    server: str, spec: Mapping[str, Any], *, run: StdioRun
) -> AsyncIterator[tuple[Any, Any]]:
    """Start server *server* from *spec* (its ``command``, ``args``, resolved ``env`` and ``cwd``)
    and yield ``(read, write)``, the streams an SDK ``ClientSession`` takes.

    It is started in the one environment a server is started in (`mcp_discovery.stdio_spawn_env`),
    its command found on that environment's ``PATH`` (:class:`CommandNotFound`, before anything is
    started, when it is not), through the resource ceiling.

    *read* carries each line the server writes to stdout as a ``SessionMessage`` (or the error that
    parsing it raised, as the SDK's own client does), and ends when the server closes stdout. What
    is sent on *write* is written to its stdin, one JSON message per line. On the way out the
    server's input is closed, which asks it to exit; one that has not ended after
    :data:`_END_GRACE_SECS` is stopped with its whole process group
    (`cancellation.terminate_and_reap`). Leaving always waits for that, bounded, so no server
    outlives the connection that started it.
    """
    import anyio
    from mcp import types
    from mcp.shared.message import SessionMessage

    from personalclaw.cancellation import kill_timed_out, terminate_and_reap
    from personalclaw.mcp_discovery import stdio_spawn_env
    from personalclaw.sandbox import PROFILE_TOOL, create_subprocess_limited

    command = str(spec.get("command") or "")
    if not command:
        raise ValueError("server spec has neither 'url' nor 'command'")
    env = stdio_spawn_env(spec.get("env") or {}, server=server)
    # ``cwd`` lets an app-shipped server (registered by the app-platform MCP bridge with
    # cwd=app_dir) resolve relative command/args; absent, it starts in the gateway's.
    cwd = str(spec.get("cwd") or "") or None
    resolved = resolve_command(command, env=env, cwd=cwd)
    if resolved is None:
        raise CommandNotFound(command)
    proc = await create_subprocess_limited(
        resolved,
        *(str(a) for a in spec.get("args") or []),
        profile=PROFILE_TOOL,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
        # Its own process group, so stopping it stops what it started (a package manager's build).
        start_new_session=True,
    )
    read_send, read_recv = anyio.create_memory_object_stream[Any](0)
    write_send, write_recv = anyio.create_memory_object_stream[Any](0)
    stderr_done = anyio.Event()

    async def from_server() -> None:
        assert proc.stdout is not None
        pending = b""
        async with read_send:
            while chunk := await proc.stdout.read(_READ_CHUNK):
                *lines, pending = (pending + chunk).split(b"\n")
                for line in lines:
                    if not line.strip():
                        continue
                    item: Any
                    # A line that is not a message is handed on as its error: the session decides
                    # what it means, as with the SDK's own client.
                    try:
                        item = SessionMessage(types.JSONRPCMessage.model_validate_json(line))
                    except Exception as exc:  # noqa: BLE001
                        item = exc
                    try:
                        await read_send.send(item)
                    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                        return

    async def to_server() -> None:
        assert proc.stdin is not None
        async with write_recv:
            async for message in write_recv:
                data = message.message.model_dump_json(by_alias=True, exclude_none=True)
                try:
                    proc.stdin.write(data.encode("utf-8") + b"\n")
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    # It is gone: what it wrote before it went is still read, and how it
                    # ended says why.
                    return

    async def error_output() -> None:
        assert proc.stderr is not None
        try:
            while chunk := await proc.stderr.read(4096):
                run._keep(chunk)
        finally:
            stderr_done.set()

    async with anyio.create_task_group() as pumps:
        pumps.start_soon(from_server)
        pumps.start_soon(to_server)
        pumps.start_soon(error_output)
        try:
            yield read_recv, write_send
        finally:
            # Shielded: the connection being cancelled is the usual way here, and the server must
            # still be put away. Every wait below is bounded.
            with anyio.CancelScope(shield=True):
                # Closing its input asks it to exit (the MCP stdio shutdown); one that has not
                # ended within the grace is stopped with its whole group.
                if proc.stdin is not None and not proc.stdin.is_closing():
                    with contextlib.suppress(Exception):
                        proc.stdin.close()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_END_GRACE_SECS)
                except TimeoutError:
                    if proc.returncode is None:
                        run.stopped = True
                        await terminate_and_reap(proc)
                    else:
                        # It ended, and something it started still holds its output open:
                        # `wait()` waits for that too. Bounded, so the connection is not held
                        # for as long as that runs.
                        await kill_timed_out(proc)
                run.returncode = proc.returncode
                with anyio.move_on_after(_DRAIN_SECS):
                    await stderr_done.wait()
            pumps.cancel_scope.cancel()
            write_send.close()
            read_recv.close()
