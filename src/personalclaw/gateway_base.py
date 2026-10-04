"""The ONE owner of "where is THIS instance's gateway".

Every child of a gateway — the ``personalclaw-core`` MCP server, a sandboxed cron
script, an ACP CLI's MCP subprocess — has to know the API base to reach the gateway
that spawned it. Before this module each of them worked that out for itself from
``dashboard.url`` (via ``parse_dashboard_url``) or from ``config.loader.DASHBOARD_PORT``,
and **both** fall back to the fixed ``_DEFAULT_PORT`` of ``10000``.

Why that is not a hang but a cross-instance leak (#2539). ``dashboard.url`` is ``""``
in a default config, and neither ``--port N`` nor ``--port auto`` writes it. So a
gateway bound to port N spawned children addressed to ``10000`` — and *something is
listening there*: in a multi-gateway setup that is a DIFFERENT instance, with its own
home, config and state. Measured end to end on two isolated homes: instance B (home
``…/homeB``, bound ``127.0.0.1:10771``) fired a ``run-script`` action whose
``ctx.notify()`` was **persisted into instance A's** ``notifications.jsonl`` (home
``…/homeA``, bound ``127.0.0.1:10772``) while B's own notification store stayed empty.
The child reported ``{'ok': True}``. Nothing warned. A reading tool in that position is
a cross-instance information leak; a writing tool corrupts another instance's state, and
the request carries this home's ``.local_secret`` to the stranger on the way.

The shape of the fix, and why it is a shape and not a patch:

* **One source of truth.** The only authoritative answer is the socket the gateway
  ACTUALLY bound — known to the gateway and to nothing else. :func:`publish` is called
  once, after bind, with that port; every reader goes through :func:`resolve_port`.
  Two independently-derived answers that can disagree IS the defect, so no second
  resolution path is added: ``parse_dashboard_url`` keeps its (unchanged) job of
  deciding what the server should BIND, and stops being consulted about where a child
  should CONNECT.
* **Fail closed, and fail fast.** When the base cannot be resolved this REFUSES, loudly,
  naming every source it consulted. It never substitutes ``10000``. Failing open on a
  security-critical operation is the pitfall to avoid, and a system should fail fast
  rather than hang indefinitely on a timeout — the old default violated both at once,
  silently delivering the request to whatever stranger occupied the port while the
  observable symptom was a hang.

Resolution order — three projections of ONE fact, never a guess:

1. ``PERSONALCLAW_PORT`` — the gateway's own export of its bound port into its process
   environment, inherited by every child it spawns (and the documented dev override).
2. the per-home runtime record written by :func:`publish`, when the pid it names is
   still alive. This covers a child whose environment was rebuilt from an allowlist and
   a helper the gateway did not spawn itself. Being INSIDE the home, it can never name
   another instance's gateway; being pid-checked, a crashed instance's record is not
   trusted.
3. an EXPLICIT port in ``dashboard.url`` — the operator's own declaration.

then refuse. ``_DEFAULT_PORT`` is deliberately unreachable from here.

A command run for a home (``personalclaw token`` and the rest) is no child: nothing it inherits
names its gateway, so it asks :mod:`personalclaw.home_gateway`, which reads the same record (or
the port the command was given) and, before any credential is sent, asks the gateway answering
there which home it serves.

One gateway serves a home (:func:`claim_home`). Before a gateway does anything else it takes its
home's claim, a lock on ``gateway.lock`` in the home, and it holds it until it exits. A start on a
home whose claim another process holds ends there, before it seeds, binds, publishes or writes the
local secret, and names the gateway that serves the home. The record says where a gateway listens;
the claim says whether one serves the home at all, so a record whose process is gone, or whose
lock nobody holds, stops no start.
"""

from __future__ import annotations

import fcntl
import io
import json
import logging
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: The environment variable carrying the bound port to a child. Written by
#: :func:`publish`, read first by :func:`resolve_port`, and allowlisted for sandboxed
#: children in ``sandbox.CHILD_ENV_BASE_NAMES``.
PORT_ENV = "PERSONALCLAW_PORT"

#: Per-home record of the socket the gateway actually bound. Lives under the home, so it
#: cannot name a different instance's gateway however stale it gets.
RUNTIME_FILE = "gateway.runtime.json"

#: The lock a gateway holds on its home for as long as it runs (:func:`claim_home`). An empty
#: file, never deleted: removing it would let a second gateway lock a new file of that name while
#: the first still holds the old one.
LOCK_FILE = "gateway.lock"

#: The exit status of a start refused because another gateway already serves its home. A status of
#: its own, so what started it (the desktop, a service manager, ``personalclaw run``, a script) can
#: tell it from a start that failed (1) and from a command line that could not be read (2).
HOME_SERVED_EXIT = 3


class GatewayBaseUnresolved(RuntimeError):
    """This instance's gateway address could not be resolved.

    Raised instead of guessing a port. The message names every source that was
    consulted, because the operator action differs per source and a bare
    "could not resolve" would send them reading code.
    """


def _runtime_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / RUNTIME_FILE


def pid_is_alive(pid: int) -> bool:
    """Whether *pid* still exists. A record from a crashed gateway must not be trusted.

    PUBLIC because it is the project's one owner-liveness predicate, and a second copy is how two
    surfaces start disagreeing about whether a process is gone. `triggers.claims.orphaned_ids`
    (WF2AUT-16's boot pass) asks exactly this question about the process that granted a claim.

    A non-positive pid is "we cannot tell", and it answers False — NOT alive — because the two
    callers want opposite fallbacks and each states its own: the runtime record refuses to trust a
    pid-less row, and the claim pass never terminalizes on an unknown owner (it checks for an owner
    before asking this). Neither reads a bare False as "provably dead".
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but is owned by someone else — it exists, which is the question asked.
        return True
    except OSError:
        return False
    return True


def publish(port: int, *, pid: int | None = None) -> None:
    """Record *port* as the socket this gateway bound. Call once, right after bind.

    Writes BOTH projections: the process environment (inherited by children) and the
    per-home record (readable by a child whose environment was rebuilt). Both carry the
    same value from the same call, so they cannot drift.

    A non-positive *port* is a hard error rather than a silent no-op. A gateway that
    cannot say which socket it bound cannot tell its children either, and the old
    ``if self._dashboard_port:`` guard turned that into a refusal deferred to the first
    tool call — i.e. exactly the silent misdirection this module exists to end.
    """
    if not isinstance(port, int) or isinstance(port, bool) or port <= 0:
        raise ValueError(
            f"gateway_base.publish() needs the bound port, got {port!r}. A gateway that "
            "cannot name its own socket cannot address its children."
        )
    os.environ[PORT_ENV] = str(port)
    record = {"port": port, "pid": int(pid if pid is not None else os.getpid())}
    try:
        from personalclaw.atomic_write import atomic_json_write

        atomic_json_write(_runtime_path(), record)
    except OSError:
        # The environment projection already landed, which covers every child the gateway
        # spawns itself. Losing the file narrows the fallback; it does not misdirect.
        logger.warning(
            "could not write %s; children outside this process tree will "
            "have to resolve the port from the environment",
            RUNTIME_FILE,
            exc_info=True,
        )


def unpublish() -> None:
    """Drop the runtime record on shutdown. Never raises."""
    try:
        _runtime_path().unlink()
    except Exception:  # noqa: BLE001 - shutdown must not fail on a bookkeeping unlink
        logger.debug("could not remove %s", RUNTIME_FILE, exc_info=True)


class LiveGateway(NamedTuple):
    """A live gateway of this home, as its runtime record names it."""

    port: int
    pid: int


def live_gateway() -> LiveGateway | None:
    """The port and pid recorded by a LIVE gateway of this home, or ``None``.

    Deliberately does NOT consult the environment: this answers "is a gateway of this
    home up, and on what socket", and an operator's exported ``PERSONALCLAW_PORT`` is a
    hint about where to talk, not evidence that anything is listening.

    ``personalclaw stop`` reads the pid from here, because the record is the one account of
    this home's gateway that needs nothing installed: finding it by asking ``lsof`` who
    listens on a port failed on every host without ``lsof``, the published image included.
    A live pid is still only a pid, and a crash leaves the record behind for the system to
    hand that pid to another program, so a caller that signals it confirms first what runs
    under it (``process_facts.command_line``).
    """
    try:
        raw = _runtime_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        record = json.loads(raw)
        port = int(record["port"])
        pid = int(record.get("pid", 0))
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        logger.debug("ignoring malformed %s", RUNTIME_FILE)
        return None
    if port <= 0 or not pid_is_alive(pid):
        return None
    return LiveGateway(port, pid)


def live_port() -> int | None:
    """The bound port recorded by a LIVE gateway of this home, or ``None`` (see
    :func:`live_gateway`)."""
    gateway = live_gateway()
    return gateway.port if gateway else None


class HomeAlreadyServed(RuntimeError):
    """Another process holds this home's claim, so no second gateway may start on it.

    ``str()`` is what the refused start prints: the gateway that serves the home, by its pid and
    address once it has shown it serves this home, else that it is still starting, and how to use
    or stop it instead. *pid* and *port* are ``0`` when they are not known.
    """

    def __init__(self, home: Path, pid: int = 0, port: int = 0) -> None:
        self.home = home
        self.pid = pid
        self.port = port
        which = (
            f"pid {pid}, http://127.0.0.1:{port}"
            if pid > 0 and port > 0
            else "it is still starting"
        )
        super().__init__(
            f"PersonalClaw did not start: another gateway already serves {home} ({which}). "
            f"Use that one, or stop it first: PERSONALCLAW_HOME={shlex.quote(str(home))} "
            "personalclaw stop"
        )


class HomeNotClaimed(RuntimeError):
    """This home's claim could not be taken for a reason other than another process holding it.

    Fails closed: the lock is the only thing that keeps a second gateway off the home, so a home
    whose lock cannot be taken is not served. ``str()`` names the lock and why.
    """


@dataclass(frozen=True)
class HomeClaim:
    """This process's claim on *home*, the lock at *lock*, held from :func:`claim_home` until the
    process ends."""

    home: Path
    lock: Path


#: The claims this process holds, by home, each with the open lock file that IS the claim. Never
#: closed: the operating system lets go of a lock when its process ends, however it ends.
_held: dict[Path, tuple[HomeClaim, io.FileIO]] = {}


def claim_home() -> HomeClaim:
    """Claim this home for the gateway this process runs, or raise.

    The claim is an exclusive, non-blocking ``flock`` on ``<home>/gateway.lock``, opened through
    ``durability.home_paths.open_lock`` (the one way a lock in the home is opened: made ``0600``
    and empty, never through a link). Its descriptor stays open in this module until the process
    ends and is never handed to a child (``O_CLOEXEC``). So the operating system lets go of the
    claim on any exit, a crash included, no child the gateway started keeps it, and a restart's new
    image (``restart_request.start``, an ``os.execve``) loses it at the exec and claims again as it
    starts. Asked again for a home this process holds, it returns that claim.

    Raises :class:`HomeAlreadyServed` when another process holds the claim, naming the gateway that
    serves the home (:func:`_serving`), and :class:`HomeNotClaimed` when the home cannot be made or
    its lock cannot be opened or taken for any other reason.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.durability.home_paths import LinkInTheWay, open_lock

    try:
        home = config_dir()
    except OSError as exc:
        raise HomeNotClaimed(
            f"PersonalClaw did not start: its home cannot be made ({exc})."
        ) from exc
    held = _held.get(home)
    if held is not None:
        return held[0]
    lock = home / LOCK_FILE
    try:
        handle = open_lock(lock)
    except (LinkInTheWay, OSError) as exc:
        raise HomeNotClaimed(_not_claimed(home, lock, exc)) from exc
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        pid, port = _serving()
        raise HomeAlreadyServed(home, pid, port) from None
    except OSError as exc:
        handle.close()
        raise HomeNotClaimed(_not_claimed(home, lock, exc)) from exc
    claim = HomeClaim(home=home, lock=lock)
    _held[home] = (claim, handle)
    return claim


def _not_claimed(home: Path, lock: Path, exc: BaseException) -> str:
    """What a start says when *lock* cannot be opened or taken, *exc* being why."""
    from personalclaw.durability.home_paths import LinkInTheWay

    if isinstance(exc, LinkInTheWay):
        why = exc.why
    elif isinstance(exc, OSError) and exc.strerror:
        why = exc.strerror
    else:
        why = str(exc)
    return (
        f"PersonalClaw did not start: {lock} could not be locked ({why}). That lock is what keeps "
        f"a second gateway off {home}, so the home is not served without it."
    )


def _serving() -> tuple[int, int]:
    """The pid and port of the gateway that holds this home's claim, once it has shown it serves
    this home: the port its runtime record names, where the gateway answering is asked whose it is
    (``home_gateway.reach``), which carries no credential. ``(0, 0)`` while the gateway has not
    recorded where it listens, or when what answers there is not this home's gateway."""
    recorded = live_gateway()
    if recorded is None:
        return 0, 0
    from personalclaw import home_gateway  # here, not above: home_gateway imports this module

    try:
        gateway = home_gateway.reach(recorded.port)
    except home_gateway.GatewayError:
        return 0, 0
    return gateway.pid or recorded.pid, gateway.port


def _configured_port() -> int | None:
    """The EXPLICIT port in ``dashboard.url``, or ``None``.

    Not ``parse_dashboard_url``: that substitutes ``_DEFAULT_PORT`` for a URL without a
    port, which is the guess being removed. A URL that names no port declares no port.
    """
    try:
        from personalclaw.config.loader import AppConfig

        url = str(AppConfig.load().dashboard.url or "").strip()
    except Exception:  # noqa: BLE001 - an unreadable config declares nothing
        logger.debug("dashboard.url unreadable", exc_info=True)
        return None
    if not url:
        return None
    if "://" not in url:
        url = f"http://{url}"
    try:
        port = urlparse(url).port
    except ValueError:
        logger.warning("dashboard.url %r has a malformed port; it declares nothing", url)
        return None
    return port if port and port > 0 else None


def resolve_port() -> int:
    """The port of THIS instance's gateway. Raises :class:`GatewayBaseUnresolved`.

    Never returns a default. See the module docstring for the order and the reasons.
    """
    raw_env = os.environ.get(PORT_ENV, "").strip()
    if raw_env:
        try:
            port = int(raw_env)
        except ValueError:
            port = 0
        if port > 0:
            return port
        logger.warning("%s=%r is not a usable port; ignoring it", PORT_ENV, raw_env)

    recorded = live_port()
    if recorded:
        return recorded

    configured = _configured_port()
    if configured:
        return configured

    raise GatewayBaseUnresolved(
        "cannot resolve this instance's gateway address: "
        f"{PORT_ENV} is unset, no live gateway record at "
        f"{_runtime_path()}, and dashboard.url declares no port. "
        "Refusing to assume the default port — on a multi-instance host that would "
        "send this request to a DIFFERENT instance's gateway (issue #2539). "
        f"Fix: start the gateway (it publishes its bound port), or set {PORT_ENV}, "
        "or give dashboard.url an explicit port."
    )


def resolve_api_base() -> str:
    """The gateway API base a child of this instance must call.

    Raises :class:`GatewayBaseUnresolved` rather than returning a base that may point
    at a stranger.
    """
    return f"http://localhost:{resolve_port()}"
