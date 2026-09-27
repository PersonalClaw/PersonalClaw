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
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import NoReturn


@dataclass(frozen=True)
class RestartRequest:
    """The image a restart starts: ``os.execve(executable, argv, env)``."""

    executable: str
    argv: tuple[str, ...]
    env: dict[str, str]


_pending: RestartRequest | None = None


def request_restart(*, auth_mode: str = "") -> RestartRequest:
    """Ask the running gateway to stop the way it always stops, then start a fresh gateway.

    The fresh one is started with ``-m personalclaw`` rather than ``sys.argv[0]`` (a build clean may
    have removed the original ``__main__`` path) and the same arguments. *auth_mode* is the RUNNING
    gateway's resolved auth mode, pinned into the new one's environment (#46): the launcher's
    environment may not have survived (the shell that exported ``=none`` exits and the process is
    reparented), and a restart must never change whether sign-in is required.
    """
    global _pending
    from personalclaw import shutdown_event

    env = dict(os.environ)
    if auth_mode:
        env["PERSONALCLAW_AUTH_MODE"] = str(auth_mode)
    exe = sys.executable
    _pending = RestartRequest(exe, (exe, "-m", "personalclaw", *sys.argv[1:]), env)
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


def start(request: RestartRequest) -> NoReturn:
    """Replace this process with the requested image. Call only once the shutdown is done."""
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(request.executable, list(request.argv), request.env)
