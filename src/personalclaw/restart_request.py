"""A restart is a stop that starts again: the gateway's own shutdown, then a fresh process image.

The dashboard's Restart, an applied update and a staged auto-update used to re-exec from where they
were asked for, after a clean-up of their own: chat history saved, sessions closed, app processes
stopped — and nothing else. Every other step a normal stop runs was skipped, so what those steps
hold was lost on every restart. Time travel's debouncer held the commits of the last few seconds of
edits and never wrote them, and the durability loop, the search indexer and the sign-in success
tally were never stopped or flushed.

So nothing re-execs on its own any more. A restart is requested here — the image to start and the
environment to start it with — and the gateway is asked to stop exactly the way every stop runs
(:data:`personalclaw.shutdown_event`). When its shutdown is done, the gateway starts the requested
image in place of exiting (``gateway.GatewayOrchestrator._finish``). One shutdown path, with one
difference at its very end.

A stop asked for while a restart is pending — Ctrl-C, or a service manager's SIGTERM — wins
(:func:`request_stop`): a stop that came back as a new process is not a stop.

The image is the command this gateway was started with, as the install it runs from accepts it
(:func:`relaunch_argv`), for every install kind. It is checked before the gateway is asked to
stop: a restart whose program cannot be run is refused (:class:`RestartUnavailable`) while the
gateway still serves and can say so. One that passes the check and still cannot be started finds
a gateway that has already stopped, so it says why on stderr and exits non-zero (:func:`start`).
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import NoReturn

logger = logging.getLogger(__name__)

#: The exit status of a gateway that stopped to restart and could not start its new image: not 0,
#: so the desktop shell, a service manager or a script that started it knows it did not come back.
RESTART_FAILED = 1


@dataclass(frozen=True)
class RestartRequest:
    """The image a restart starts: ``os.execve(executable, argv, env)``."""

    executable: str
    argv: tuple[str, ...]
    env: dict[str, str]


class RestartUnavailable(Exception):
    """A restart refused before the gateway stopped: the program it would start cannot be run.

    The message says what happened and what the owner can do; the gateway keeps running."""


_pending: RestartRequest | None = None

#: Why the gateway is stopping (:func:`stopping_for`): to start again, or to stay down.
RESTARTING = "restart"
SHUTTING_DOWN = "shutdown"


def relaunch_argv() -> tuple[str, ...]:
    """The command a restart starts this gateway with again, for every install kind.

    This install's own CLI (``self_update.cli_argv``) followed by the arguments this gateway was
    started with, less the options that seeded its home: ``cli.main`` takes those out once they
    have run, since a restart serves the home as it is now. In the desktop app that is the frozen
    bundle's executable and the shell's own
    ``gateway --port auto --json-ready --no-open``, the command line the bundle accepted once
    already; anywhere else it is this interpreter's ``-m personalclaw`` with the same arguments.
    Never ``sys.argv[0]``: a console script's path can be relative, and a build clean may have
    removed it.
    """
    from personalclaw.self_update import cli_argv

    return (*cli_argv(), *sys.argv[1:])


def _cannot_start(program: str) -> str:
    """Why a restart is refused when *program* cannot be run, and what the owner can do instead."""
    from personalclaw.self_update import detect_install_kind

    then = (
        "Quit PersonalClaw and open it again to restart it."
        if detect_install_kind() == "desktop"
        else "Stop PersonalClaw and start it again to restart it."
    )
    return (
        f"PersonalClaw can't restart: {program or 'the program it runs from'} is missing or "
        f"can't be run, so it keeps running as it is. {then}"
    )


def request_restart(*, auth_mode: str = "") -> RestartRequest:
    """Ask the running gateway to stop the way it always stops, then start a fresh gateway.

    The fresh one is :func:`relaunch_argv`. Its program is checked first: when it is gone or cannot
    be run, the gateway would stop and never come back, so this raises
    :class:`RestartUnavailable` instead and the gateway keeps serving. *auth_mode* is the RUNNING
    gateway's resolved auth mode, pinned into the new one's environment (#46): the launcher's
    environment may not have survived (the shell that exported ``=none`` exits and the process is
    reparented), and a restart must never change whether sign-in is required.

    Every other variable is the environment this gateway was launched with (``env.launch_env``),
    never ``os.environ`` as it now stands: the new image is started in place of this one, so what
    a running gateway changed in its own environment would otherwise become the next one's launch
    environment, and every child it builds would inherit it. Started from the launch, each
    restart starts from the same environment, and the new gateway sets up the rest itself as it
    starts (its `.env`'s credentials, the libraries' settings, the port it binds).
    """
    global _pending
    from personalclaw import shutdown_event
    from personalclaw.env import launch_env

    argv = relaunch_argv()
    program = argv[0]
    if not (program and os.path.isfile(program) and os.access(program, os.X_OK)):
        raise RestartUnavailable(_cannot_start(program))
    env = launch_env()
    if auth_mode:
        env["PERSONALCLAW_AUTH_MODE"] = str(auth_mode)
    _pending = RestartRequest(program, argv, env)
    shutdown_event.set()
    return _pending


def request_stop() -> None:
    """Ask the running gateway to stop — and not to restart, even if a restart was pending."""
    global _pending
    from personalclaw import shutdown_event

    _pending = None
    shutdown_event.set()


def pending() -> RestartRequest | None:
    """The restart the gateway was asked for, or ``None`` when it was asked to stop."""
    return _pending


def stopping_for() -> str:
    """Why the stopping gateway stops: :data:`RESTARTING` while a restart is pending, else
    :data:`SHUTTING_DOWN`."""
    return RESTARTING if _pending is not None else SHUTTING_DOWN


def start(request: RestartRequest) -> NoReturn:
    """Replace this process with the requested image. Call only once the shutdown is done.

    When the image cannot be started, this gateway has already stopped everything it served and
    cannot carry on. It says why on stderr, which is what the desktop shell, a service manager's
    log or the terminal that started it shows, and exits with :data:`RESTART_FAILED`: a status of 0
    would read as a stop someone asked for.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execve(request.executable, list(request.argv), request.env)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        logger.error("The restart could not start %s: %s", request.executable, reason)
        print(
            f"PersonalClaw stopped to restart and could not start again ({request.executable}: "
            f"{reason}). Start it again.",
            file=sys.stderr,
            flush=True,
        )
        os._exit(RESTART_FAILED)
