"""This home's running gateway, as a command run for the home reaches it, and nothing else.

``personalclaw token``, ``status``, ``logout``, ``auth revoke`` and ``auth rotate-key``, ``chat``,
``run``, ``spawn``, ``cron trigger`` and ``doctor`` each talk to a running gateway, and all but
``status`` hand it this home's local secret (``.local_secret``), the credential that signs the
owner in. They found the gateway by a port: the ``--port`` typed, ``PERSONALCLAW_PORT``, the port
in ``dashboard.url``, else 10000, and never asked who answered there. Nothing a gateway started
with ``--port`` or ``--port auto`` writes is any of those, so for a home whose gateway listened
anywhere else the command sent the secret to whatever listened on 10000: another home's gateway,
or any other program holding that port.

So a command asks here, once, and gets a :class:`HomeGateway` or a sentence that says why not:

* **Where.** The port the home's gateway recorded once it listened: ``gateway_base``'s runtime
  record, kept inside the home, whose pid must still be alive. ``--port`` and
  ``PERSONALCLAW_PORT`` may name a port explicitly instead. Nothing else is a source and no port
  is assumed, so with neither there is no gateway of this home to talk to, and
  :class:`NoGatewayRunning` says so with the command that starts one.
* **Whose.** Before anything that carries a credential is sent, the gateway at that port is asked
  which home it serves. ``GET /api/healthz`` answers without a sign-in and carries ``home_id``,
  a fingerprint of the gateway's resolved home (:func:`home_id`); this side computes the same
  for its own home and compares. Another home's gateway, a program that is not a gateway, or a
  port that does not answer is :class:`NotThisHomesGateway`, and nothing more is sent to it.
* **How.** Every request goes to ``127.0.0.1``, the address the identity was read at, never to a
  name that may resolve to another socket. None goes through a proxy from the environment: urllib
  hands even a loopback request to ``HTTP_PROXY``, its headers included. And none follows a
  redirect, which would carry the same headers on to wherever the redirect points
  (:func:`open_loopback`).

Only :func:`reach` makes a :class:`HomeGateway`, and only a :class:`HomeGateway` reads the
home's secret to send it: ``tests/test_a_command_reaches_only_its_own_homes_gateway.py`` holds
the tree to both, and drives every command against another home's gateway.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from personalclaw import gateway_base
from personalclaw.config import loader as config_loader

#: The one address a command reaches its gateway at. The identity read and every request after it
#: go to the same socket: ``localhost`` can resolve to ``::1``, where another program may listen.
LOOPBACK_HOST = "127.0.0.1"

#: How long one identity read waits for an answer, and how many reads are made.
#:
#: Both numbers were MEASURED, not chosen. At a 2 s single read, a gateway that was alive and
#: serving answered ``/api/healthz`` in more than 2 s while it was busy: three reads came back
#: empty, and the fourth answered in 0.67 s. Misreading a live gateway as absent is not cosmetic:
#: ``personalclaw run`` then starts a second gateway on the same home, whose fresh
#: ``.local_secret`` replaces the first one's, and the home's running gateway can no longer sign
#: anything in. So a timeout is read again; a refused connection, which is unambiguous, is not.
ANSWER_TIMEOUT_SECS = 10.0
ANSWER_ATTEMPTS = 3

#: The most of an identity answer that is read: a gateway's is about 150 bytes.
_ANSWER_LIMIT = 64 * 1024

#: What :func:`_healthz` says of a port where nothing listens, and of one that does not answer.
_NOTHING = "nothing listens"
_SILENT = "no answer"


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is the gateway's answer, never followed: following one would carry the request's
    headers, a secret among them, to wherever it points."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


#: Every request a command sends its gateway: no proxy from the environment, no redirects.
_LOOPBACK = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())


class GatewayError(Exception):
    """A request to this home's gateway was not sent, or was not answered. The message is the
    sentence a command prints: what happened, and what to do about it."""


class NoGatewayRunning(GatewayError):
    """No gateway of this home is running, so nothing was sent anywhere.

    The one refusal a command may act on by itself, for what it can do without a gateway: ``run``
    starts one for its turn, ``auth revoke`` and ``auth rotate-key`` change the home directly,
    ``doctor`` measures in its own process, ``status`` reports it.
    """


class NotThisHomesGateway(GatewayError):
    """The port named is not where this home's gateway answers, so no credential was sent there.

    A gateway of this home may still be running somewhere, so a command does nothing by itself on
    this one: changing the home behind a running gateway's back is what it must not do.
    """


def home_id(home: str | os.PathLike[str] | None = None) -> str:
    """A short, stable, non-reversible id for a home: the ``home_id`` a gateway's ``/api/healthz``
    answers with, and what a command compares it with.

    A fingerprint and not the path: ``/api/healthz`` answers without a sign-in and the gateway can
    bind ``0.0.0.0``, so a path there would hand every unauthenticated client on the network the
    owner's username and folder layout. A caller does not need the path, only to compare. *home*
    is the active home when none is named, and it is resolved first, so a home named through a
    link or by a relative path has the id of the folder it is.

    Reproduce it for the home you expect, then compare::

        python3 -c 'import hashlib,pathlib,os; \\
            p=pathlib.Path(os.environ["PERSONALCLAW_HOME"]).expanduser().resolve(); \\
            print(hashlib.sha256(str(p).encode()).hexdigest()[:16])'
    """
    path = Path(home) if home is not None else config_loader.resolve_config_dir()
    return hashlib.sha256(str(path.expanduser().resolve()).encode("utf-8")).hexdigest()[:16]


def open_loopback(request: urllib.request.Request, *, timeout: float) -> Any:
    """Send *request* to a gateway on this machine, and return the response urllib gives.

    Never through a proxy from the environment, and never on to where a redirect points (an error
    status, a redirect among them, raises ``HTTPError`` as ``urlopen`` does). Refuses a URL whose
    host is not this machine's loopback: this is the transport for a command's own gateway, and
    nothing else may borrow it.
    """
    host = urlsplit(request.full_url).hostname or ""
    if host not in {LOOPBACK_HOST, "localhost", "::1"}:
        raise ValueError(f"{request.full_url} is not a gateway on this machine")
    return _LOOPBACK.open(request, timeout=timeout)


def _decoded(raw: bytes) -> dict[str, Any]:
    """A JSON object from *raw*, or ``{}`` for anything else."""
    try:
        value = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _healthz(port: int, *, attempts: int = ANSWER_ATTEMPTS) -> dict[str, Any] | str:
    """What answers ``GET /api/healthz`` on loopback *port*: the JSON object it answered with
    (``{}`` for an answer that is not one), else :data:`_NOTHING` or :data:`_SILENT`.

    The read carries nothing: no secret, no token, no cookie.
    """
    request = urllib.request.Request(f"http://{LOOPBACK_HOST}:{port}/api/healthz")
    for attempt in range(max(1, attempts)):
        try:
            with open_loopback(request, timeout=ANSWER_TIMEOUT_SECS) as resp:
                return _decoded(resp.read(_ANSWER_LIMIT)) if resp.status == 200 else {}
        except urllib.error.HTTPError:
            return {}  # something answered, and not as a gateway's healthz does
        except urllib.error.URLError as exc:
            if not isinstance(exc.reason, TimeoutError):
                return _NOTHING  # refused, or no route to it: nothing listens there
        except TimeoutError:
            pass  # it took the connection and said nothing in time: ambiguous, so asked again
        except (http.client.HTTPException, OSError):
            return {}  # it took the connection and did not speak HTTP
        if attempt == attempts - 1:
            return _SILENT
    return _SILENT  # pragma: no cover - the loop always returns


def _start_command() -> str:
    """The command that starts this home's gateway: ``personalclaw restart`` when a service is
    installed for this home (its service manager runs the gateway), else ``personalclaw gateway``.
    """
    try:
        from personalclaw.service import controller

        installed = controller.this_homes_service() is not None
    except Exception:  # noqa: BLE001 - naming the command must not hide the refusal it ends
        installed = False
    return "personalclaw restart" if installed else "personalclaw gateway"


@dataclass(frozen=True)
class HomeGateway:
    """This home's running gateway, once it has shown it serves this home. Made by :func:`reach`.

    *pid* is the process that answered, as its ``/api/healthz`` said.
    """

    home: Path
    port: int
    pid: int

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.port}"

    def sign_in(self, ttl: str, *, timeout: float = 5.0) -> dict[str, Any]:
        """Trade this home's local secret for a sign-in (``GET /api/token/local``), lasting *ttl*
        in the endpoint's grammar (``30m``, ``20h``, ``7d``): the gateway's reply, its ``token``
        and how long it lasts. Raises :class:`GatewayError` when it does not sign one in."""
        status, answer = self._send(
            "GET",
            f"/api/token/local?ttl={quote(ttl, safe='')}",
            secret_header="X-Local-Secret",
            timeout=timeout,
        )
        if status != 200:
            raise GatewayError(
                f"This home's gateway on port {self.port} did not sign this command in: "
                f"{error_text(answer, status)}"
            )
        return answer

    def post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        secret_header: str,
        work: str = "",
        timeout: float = 5.0,
    ) -> tuple[int, dict[str, Any]]:
        """POST *body* to *path* with this home's local secret in *secret_header*:
        ``X-Local-Secret`` for a route that signs the owner in or out, ``X-Internal-Secret`` for
        one of the internal routes, which also needs the *work* the call does, sent as
        ``X-Session-Key``.

        Returns the status and the answer, an error status included. Raises :class:`GatewayError`
        when the gateway could not be asked at all.
        """
        return self._send(
            "POST", path, secret_header=secret_header, work=work, body=body, timeout=timeout
        )

    def answers(self) -> bool:
        """Whether this home's gateway still answers on its port (one quick read): for a command
        that failed part way, to say whether its gateway went away."""
        said = _healthz(self.port, attempts=1)
        return isinstance(said, dict) and said.get("home_id") == home_id(self.home)

    def gone(self) -> str:
        """What a command says once this gateway has stopped answering since it was reached."""
        return (
            f"No gateway is running for this home ({self.home}) any more: it no longer answers "
            f"on port {self.port}. Start it with: {_start_command()}"
        )

    def _secret(self) -> str:
        """The home's local secret, which its gateway writes each time it starts."""
        path = self.home / ".local_secret"
        try:
            secret = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise GatewayError(
                f"This home's gateway answers on port {self.port}, but its local secret cannot be "
                f"read ({path}: {exc.strerror or exc}), so this command cannot sign in to it."
            ) from exc
        if not secret:
            raise GatewayError(
                f"This home's gateway answers on port {self.port}, but its local secret ({path}) "
                f"is empty. The gateway writes it as it starts: restart it with: "
                f"{_start_command()}"
            )
        return secret

    def _send(
        self,
        method: str,
        path: str,
        *,
        secret_header: str,
        timeout: float,
        work: str = "",
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {secret_header: self._secret()}
        if work:
            headers["X-Session-Key"] = work
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with open_loopback(request, timeout=timeout) as resp:
                return int(resp.status), _decoded(resp.read())
        except urllib.error.HTTPError as exc:
            return int(exc.code), _decoded(exc.read())
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise GatewayError(
                f"This home's gateway on port {self.port} could not be asked ({reason})."
            ) from exc


def error_text(answer: dict[str, Any], status: int) -> str:
    """What a gateway's error answer says: the message of its envelope (``{"error": {"code",
    "message"}}`` or ``{"error": "…"}``), else its status."""
    error = answer.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or f"HTTP {status}")
    return str(error) if error else f"HTTP {status}"


def _named_port(port: int | None) -> tuple[int, str] | None:
    """The port named explicitly, and the words that say who named it: ``--port`` first, then
    ``PERSONALCLAW_PORT``. ``None`` when neither names one. Raises :class:`GatewayError` for a
    name that is not a port: a command asks nothing of a port it cannot read."""
    if port is not None:
        named, words = port, "which --port names"
    else:
        raw = os.environ.get(gateway_base.PORT_ENV, "").strip()
        if not raw:
            return None
        try:
            named = int(raw)
        except ValueError:
            raise GatewayError(
                f"{gateway_base.PORT_ENV}={raw!r} is not a port, so no gateway was asked. Unset "
                "it, or set it to the port this home's gateway listens on."
            ) from None
        words = f"which {gateway_base.PORT_ENV} names"
    if not 1 <= named <= 65535:
        raise GatewayError(f"{named} is not a port (1 to 65535), so no gateway was asked.")
    return named, words


def reach(port: int | None = None) -> HomeGateway:
    """This home's running gateway: at the port it recorded, or at *port* (``--port``) or
    ``PERSONALCLAW_PORT`` when one names a port, once that gateway has shown it serves this home.

    Raises :class:`NoGatewayRunning` when no gateway of this home is running, and
    :class:`NotThisHomesGateway` when the port named is not where this home's gateway answers.
    Before either, the only request sent is the identity read, which carries no credential.
    """
    home = config_loader.resolve_config_dir()
    named = _named_port(port)
    recorded = gateway_base.live_gateway()
    if named is None:
        if recorded is None:
            raise NoGatewayRunning(
                f"No gateway is running for this home ({home}). Start it with: {_start_command()}"
            )
        target, words = recorded.port, "where this home's gateway said it listens"
    else:
        target, words = named
    said = _healthz(target)
    if said == _NOTHING:
        if recorded is not None and named is not None and recorded.port != target:
            raise NotThisHomesGateway(
                f"Nothing listens on port {target}, {words}, so nothing was sent there. This "
                f"home's gateway ({home}) listens on port {recorded.port}."
            )
        raise NoGatewayRunning(
            f"No gateway is running for this home ({home}): nothing listens on port {target}, "
            f"{words}. Start it with: {_start_command()}"
        )
    if not isinstance(said, dict):  # it took the connection and never answered
        waited = int(ANSWER_TIMEOUT_SECS * ANSWER_ATTEMPTS)
        raise NotThisHomesGateway(
            f"Port {target}, {words}, did not answer within {waited} seconds, so this home's "
            "local secret was not sent there. If this home's gateway is starting, try again in a "
            "moment."
        )
    if said.get("home_id") == home_id(home):
        pid = said.get("pid")
        return HomeGateway(home=home, port=target, pid=pid if isinstance(pid, int) else 0)
    whose = (
        "another PersonalClaw home's gateway"
        if said.get("status") == "ok" and isinstance(said.get("home_id"), str)
        else "a program that is not a PersonalClaw gateway"
    )
    if recorded is None:
        then = f"No gateway is running for this home. Start it with: {_start_command()}"
    elif recorded.port != target:
        then = f"This home's gateway listens on port {recorded.port}."
    else:
        then = f"This home's gateway no longer runs there. Start it with: {_start_command()}"
    raise NotThisHomesGateway(
        f"Port {target}, {words}, is answered by {whose}, not by this home's ({home}), so this "
        f"home's local secret was not sent to it. {then}"
    )
