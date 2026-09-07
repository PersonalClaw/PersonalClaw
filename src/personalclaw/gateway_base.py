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
  naming every source it consulted. It never substitutes ``10000``. Per ARCC SAX-04
  Outcome 5 ("Fail Closed vs Fail Open Design Decisions"), *"failing open for
  security-critical operations"* is a named pitfall and a system should *"fail fast
  rather than hanging indefinitely on timeout"* — the old default violated both at once,
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
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: The environment variable carrying the bound port to a child. Written by
#: :func:`publish`, read first by :func:`resolve_port`, and allowlisted for sandboxed
#: children in ``sandbox.CHILD_ENV_BASE_NAMES``.
PORT_ENV = "PERSONALCLAW_PORT"

#: Per-home record of the socket the gateway actually bound. Lives under the home, so it
#: cannot name a different instance's gateway however stale it gets.
RUNTIME_FILE = "gateway.runtime.json"


class GatewayBaseUnresolved(RuntimeError):
    """This instance's gateway address could not be resolved.

    Raised instead of guessing a port. The message names every source that was
    consulted, because the operator action differs per source and a bare
    "could not resolve" would send them reading code.
    """


def _runtime_path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / RUNTIME_FILE


def _pid_is_alive(pid: int) -> bool:
    """Whether *pid* still exists. A record from a crashed gateway must not be trusted."""
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
        path = _runtime_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        os.replace(tmp, path)
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


def live_port() -> int | None:
    """The bound port recorded by a LIVE gateway of this home, or ``None``.

    Deliberately does NOT consult the environment: this answers "is a gateway of this
    home up, and on what socket", and an operator's exported ``PERSONALCLAW_PORT`` is a
    hint about where to talk, not evidence that anything is listening.
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
    if port <= 0 or not _pid_is_alive(pid):
        return None
    return port


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
