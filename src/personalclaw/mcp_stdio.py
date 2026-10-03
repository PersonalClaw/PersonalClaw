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

**A start is never cut off part-way.** A package runner (``npx``, ``uvx``) installs what it runs the
first time it runs it, and that can take longer than a connection waits for an answer. Stopped in
the middle, the install is left half-written: a part-downloaded file, its lock still held, and on
some runners a package recorded as installed that never finished installing, so every later start
of it fails until the folder is deleted by hand. So a program that has not said a word, and is
still running when its connection stops waiting for it, is left to finish on its own, for up to
:data:`FINISH_SECS`. A start of the same program meanwhile waits for that one rather than starting a
second beside it, and when it ends its server is looked at again (`mcp_discovery.look_again`). A
program that has spoken is a running server and is stopped as before, and so is the next silent
start of a server left to finish once already that has not answered since, until its owner presses
Retry or changes it: a server slow to start every time is not installing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from personalclaw.mcp_status import STDERR_TAIL_BYTES

logger = logging.getLogger(__name__)

#: How long a server has to end on its own once its input is closed, before it is stopped.
_END_GRACE_SECS = 2.0
#: How long the rest of its error output may take to arrive once it has ended.
_DRAIN_SECS = 1.0
#: How much of the server's output is read at a time. A message is one line, of any length.
_READ_CHUNK = 64 * 1024
#: How long a program its connection stopped waiting for may go on starting on its own before it is
#: stopped: the time a first start has to install what it runs.
FINISH_SECS = 600.0


@dataclass
class StdioRun:
    """One start of a stdio server: how it ended, and the tail of what it wrote to its error
    output. Filled in as it runs; final once its streams are closed."""

    #: Its exit code once it ended (negative: the signal that ended it), else ``None``.
    returncode: int | None = None
    #: It did not end on its own once its input was closed, so PersonalClaw stopped it.
    stopped: bool = False
    #: It wrote to its output: it got as far as speaking, so it is a running server.
    spoke: bool = False
    #: Its connection stopped waiting for its answer (:meth:`stop_waiting`).
    abandoned: bool = False
    #: It was still starting when its connection stopped waiting, so it was left to finish.
    left_to_finish: bool = False
    #: It ran nothing: it was waiting for an earlier start of the same program to finish.
    waiting: bool = False
    _stderr: bytearray = field(default_factory=bytearray)

    def stop_waiting(self) -> None:
        """Its connection gives up on an answer, so a program still starting is left to finish."""
        self.abandoned = True

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


@dataclass
class _Finishing:
    """A program left to finish starting: the server it was started for, and the task waiting on
    it (:func:`_finish`)."""

    server: str
    task: "asyncio.Task[None]"


#: The programs left to finish starting, by what each runs (:func:`_program`).
_finishing: dict[tuple[str, ...], _Finishing] = {}
#: The servers whose start was left to finish once and that have not answered since: the next start
#: that does not answer is stopped. Its owner's Retry, or a change to it, gives it its time again
#: (:func:`forget_left`).
_left_unanswered: set[str] = set()


def forget_left(server: str) -> None:
    """Give *server*'s next start its time to finish again: its owner pressed Retry, or it was
    changed (`mcp_discovery.forget_probe`)."""
    _left_unanswered.discard(server)


def _program(command: str, args: Iterable[str], cwd: str | None) -> tuple[str, ...]:
    """What a start runs: its program, the folder it starts in, and its arguments."""
    return (command, cwd or "", *args)


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
    started, when it is not), through the resource ceiling. While an earlier start of the same
    program is still finishing (see the module's docstring), this one waits for it before it starts
    anything; its own deadline can end that wait, and nothing here ends the earlier start.

    *read* carries each line the server writes to stdout as a ``SessionMessage`` (or the error that
    parsing it raised, as the SDK's own client does), and ends when the server closes stdout. What
    is sent on *write* is written to its stdin, one JSON message per line. On the way out the
    server's input is closed, which asks it to exit; one that has not ended after
    :data:`_END_GRACE_SECS` is stopped with its whole process group
    (`cancellation.terminate_and_reap`), and leaving waits for that, bounded. The one exception is
    a program still starting whose connection stopped waiting for it
    (:meth:`StdioRun.stop_waiting`): it is left to finish (:attr:`StdioRun.left_to_finish`), and
    nothing it runs outlives :data:`FINISH_SECS`, the gateway, or its server being switched off,
    removed or unloaded.
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
    args = [str(a) for a in spec.get("args") or []]
    program = _program(resolved, args, cwd)
    earlier = _finishing.get(program)
    if earlier is not None and earlier.task.get_loop() is not asyncio.get_running_loop():
        # Left by a loop that has gone (an earlier gateway in this process): nothing waits on it.
        del _finishing[program]
        earlier = None
    if earlier is not None:
        # A second start beside it would install into the same place at the same time.
        # `asyncio.wait`, not `await`: this start's deadline cancels the wait, not the earlier one.
        run.waiting = True
        await asyncio.wait({earlier.task})
        run.waiting = False
    proc = await create_subprocess_limited(
        resolved,
        *args,
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
    # It closed its output: it will never answer, so it is not left to finish.
    stdout_ended = anyio.Event()

    async def from_server() -> None:
        assert proc.stdout is not None
        pending = b""
        async with read_send:
            while chunk := await proc.stdout.read(_READ_CHUNK):
                *lines, pending = (pending + chunk).split(b"\n")
                for line in lines:
                    if not line.strip():
                        continue
                    if not run.spoke:
                        run.spoke = True
                        _left_unanswered.discard(server)
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
            stdout_ended.set()

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

    try:
        async with anyio.create_task_group() as pumps:
            pumps.start_soon(from_server)
            pumps.start_soon(to_server)
            pumps.start_soon(error_output)
            try:
                yield read_recv, write_send
            finally:
                # Shielded: the connection being cancelled is the usual way here, and the server
                # must still be put away. Every wait below is bounded.
                with anyio.CancelScope(shield=True):
                    # Closing its input asks it to exit (the MCP stdio shutdown).
                    if proc.stdin is not None and not proc.stdin.is_closing():
                        with contextlib.suppress(Exception):
                            proc.stdin.close()
                    if proc.returncode is None and _may_finish(run, server, stdout_ended.is_set()):
                        # Still starting, and its connection gave up on it: left to finish at
                        # once, with no grace to run out, so an install that ends a moment from
                        # now is looked at again like one that ends in minutes.
                        run.left_to_finish = True
                    else:
                        # One that has not ended within the grace is stopped with its whole group.
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=_END_GRACE_SECS)
                        except TimeoutError:
                            if proc.returncode is None:
                                run.stopped = True
                                await terminate_and_reap(proc)
                            else:
                                # It ended, and something it started still holds its output
                                # open: `wait()` waits for that too. Bounded, so the connection
                                # is not held for as long as that runs.
                                await kill_timed_out(proc)
                        run.returncode = proc.returncode
                        with anyio.move_on_after(_DRAIN_SECS):
                            await stderr_done.wait()
                pumps.cancel_scope.cancel()
                write_send.close()
                read_recv.close()
    finally:
        # Once its pumps are gone, so that what it writes is read by one reader at a time.
        if run.left_to_finish:
            _leave_to_finish(server, program, proc)


def _may_finish(run: StdioRun, server: str, stdout_ended: bool) -> bool:
    """Whether a program still running as its connection closes is left to finish: the connection
    stopped waiting for an answer the program had not begun to give (it never wrote to its output,
    which is still open), and its server was not left to finish already without answering since.
    """
    return run.abandoned and not run.spoke and not stdout_ended and server not in _left_unanswered


def _leave_to_finish(server: str, program: tuple[str, ...], proc: Any) -> None:
    _left_unanswered.add(server)
    task = asyncio.get_running_loop().create_task(
        _finish(server, program, proc), name=f"mcp-finish-start:{server}"
    )
    _finishing[program] = _Finishing(server, task)
    logger.info(
        "MCP server %r had not answered when its connection stopped waiting, and is still "
        "starting: it is left to finish, for up to %gs",
        server,
        FINISH_SECS,
    )


async def _finish(server: str, program: tuple[str, ...], proc: Any) -> None:
    """Wait for a program left to finish (:func:`stdio_streams`) to end on its own, reading what it
    writes so a full pipe never blocks it, and stop it with what it started once :data:`FINISH_SECS`
    have passed. Its server is then looked at again. Cancelled (:func:`stop_finishing`), it is
    stopped and nothing is looked at: the gateway is stopping, or the server is going."""
    from personalclaw.cancellation import terminate_and_reap

    readers = [
        asyncio.ensure_future(_discard(stream))
        for stream in (proc.stdout, proc.stderr)
        if stream is not None
    ]
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout=FINISH_SECS)
            logger.info(
                "MCP server %r finished starting on its own (exit %s)", server, proc.returncode
            )
        except TimeoutError:
            logger.warning(
                "MCP server %r was still starting %gs after its connection stopped waiting for it, "
                "so it was stopped",
                server,
                FINISH_SECS,
            )
            await terminate_and_reap(proc)
    except asyncio.CancelledError:
        await asyncio.shield(terminate_and_reap(proc))
        raise
    finally:
        for reader in readers:
            reader.cancel()
        held = _finishing.get(program)
        if held is not None and held.task is asyncio.current_task():
            del _finishing[program]
    try:
        from personalclaw.mcp_discovery import look_again

        look_again(server)
    except Exception:  # noqa: BLE001 - the program has ended either way; a later look sees it
        logger.debug("MCP server %r: could not look at it again", server, exc_info=True)


async def _discard(stream: Any) -> None:
    with contextlib.suppress(Exception):
        while await stream.read(_READ_CHUNK):
            pass


async def stop_finishing(match: Callable[[str], bool]) -> None:
    """Stop every program left to finish whose server *match* names, with what it started: the
    gateway is stopping, or the server was switched off, removed or unloaded. Bounded."""
    from personalclaw.cancellation import cancel_and_wait

    tasks = [held.task for held in list(_finishing.values()) if match(held.server)]
    if tasks:
        await cancel_and_wait(tasks, what="MCP programs left to finish starting")


def stop_finishing_soon(match: Callable[[str], bool]) -> None:
    """:func:`stop_finishing` without waiting for it, from any thread: each program is stopped on
    the loop that waits on it, a moment later."""
    for held in list(_finishing.values()):
        if match(held.server) and not held.task.done():
            held.task.get_loop().call_soon_threadsafe(held.task.cancel)
