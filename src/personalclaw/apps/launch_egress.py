"""Where a program an app's code starts reaches on the network: audited, and held to the app's
declarations and the owner's allowed hosts.

An app's provider runs inside the gateway, so a program its code starts (``npx``, ``git``,
``curl``…) reaches the network as the owner, whatever helper started it. Core reads each launch's
command line the way it reads the agent's shell (:func:`personalclaw.command_effects.argv_effects`)
at the one moment every launch passes through: Python's own audit event for it
(``subprocess.Popen``, which ``subprocess.run`` and ``asyncio``'s subprocesses use, and
``os.system``, ``os.posix_spawn``, ``os.exec*`` and ``os.spawn*``). A launch from code that is
not an app's (``app_code.owner``) is core's, and is not read here.

* Each host the command line names gets an egress row in the audit log (``egress_launch``, the
  app as the caller and the program beside the host), allowed or refused; so does a launch that
  reaches a host it does not name.
* A named host is allowed when the app's manifest declares it for that program
  (``launches[].hosts``, which install consent shows; a declared npm package's registry for an
  npm program) or when it is on the owner's Allowed hosts, and never when it is on Denied hosts.
  Any other is refused before the program starts: the launch raises :class:`LaunchRefused`, whose
  sentence names the host and how to allow it.

A host the command line does not name (a git remote by name, a registry from the program's own
configuration) is audited and not refused: nothing in the command says where it goes, and the
app's code is what its install consent covered. What a program reaches on its own, past its
command line, is outside any reading of it.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

#: The audit events a process launch raises, and where each carries its command line.
_LAUNCH_EVENTS = frozenset(
    {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.spawn"}
)

_installed = False
_install_lock = threading.Lock()
#: Set while this module reads a launch, so what it does then (a config read) is not read too.
_reading = threading.local()


class LaunchRefused(PermissionError):
    """A program an app's code started would reach a host neither its manifest declares nor the
    owner allowed. The message is the sentence the owner and the app's card read."""


def install() -> None:
    """Read every launch from here on. Idempotent; an audit hook cannot be taken back, so it is
    installed once, when the first app's code is loaded (``apps.native_contract``)."""
    global _installed
    with _install_lock:
        if _installed:
            return
        sys.addaudithook(_on_audit)
        _installed = True


def _words(items: Any) -> list[str] | None:
    if isinstance(items, (str, bytes, os.PathLike)):
        items = [items]
    try:
        return [os.fsdecode(item) for item in items]
    except TypeError:
        return None


def _command_line(event: str, args: tuple[Any, ...]) -> tuple[str, list[str]] | None:
    """``("argv", words)`` or ``("shell", [text])`` for a launch event, ``None`` for none."""
    if event == "subprocess.Popen" and len(args) >= 2:
        words = _words(args[1])
        return ("argv", words) if words else None
    if event == "os.system" and args:
        return ("shell", [os.fsdecode(args[0])]) if args[0] else None
    if event in ("os.posix_spawn", "os.exec") and len(args) >= 2:
        words = _words(args[1])
        return ("argv", words) if words else None
    if event == "os.spawn" and len(args) >= 3:
        words = _words(args[2])
        return ("argv", words) if words else None
    return None


def _on_audit(event: str, args: tuple[Any, ...]) -> None:
    if event not in _LAUNCH_EVENTS or getattr(_reading, "busy", False):
        return
    from personalclaw import app_code

    app = app_code.owner()
    if app is None:
        return
    found = _command_line(event, args)
    if found is None:
        return
    _reading.busy = True
    try:
        refusal = check(app, *found)
    except Exception:  # noqa: BLE001 - a reading that breaks must not break the app's launch
        logger.warning("launch egress: could not read a launch by %s", app, exc_info=True)
        return
    finally:
        _reading.busy = False
    if refusal:
        raise LaunchRefused(refusal)


def check(app: str, form: str, words: Sequence[str]) -> str:
    """Audit what a launch by *app* reaches, and say why it is refused (``""``: it may start).

    *form* is ``"argv"`` (a program and its arguments) or ``"shell"`` (one shell command line)."""
    from personalclaw import run_bounds
    from personalclaw.apps.declared import declared_hosts
    from personalclaw.command_effects import argv_effects, command_effects
    from personalclaw.net.guard import ALLOWED_HOSTS, allow_host_step
    from personalclaw.net.policy import LISTED, egress_policy_for

    effects = argv_effects(words) if form == "argv" else command_effects(words[0])
    if not effects.network:
        return ""
    program = os.path.basename(words[0]) if form == "argv" else "sh"
    refused = run_bounds.unlisted(
        effects.hosts, egress_policy_for(LISTED), declared=declared_hosts(app, program)
    )
    caller = f"app:{app}"
    run_bounds.audit(
        caller,
        sorted(set(effects.hosts) - set(refused)),
        outcome="allowed",
        what=program,
        unnamed=effects.host_unread,
    )
    if not refused:
        return ""
    which = "it" if len(refused) == 1 else "them"
    sentence = (
        f"PersonalClaw's network settings stopped the {app} app from starting {program}: it "
        f"reaches {', '.join(refused)}, which its install review did not name and which is not "
        f"on {ALLOWED_HOSTS}. To allow {which}, {allow_host_step(refused[0])}"
        + (" (and the others)" if len(refused) > 1 else "")
        + "."
    )
    run_bounds.audit(caller, refused, outcome="denied", what=program, reason=sentence)
    return sentence


__all__ = ["LaunchRefused", "check", "install"]
