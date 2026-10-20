"""Headless one-shot CLI turn — ``personalclaw run``.

The CLI face of the inbound-access story the rest of that plan builds over HTTP: a
non-interactive caller runs ONE agent turn and consumes structured output. It is a
*client*, not a second engine — the turn runs inside the gateway through the same
``POST /api/chat`` + ``/api/ws`` pair the dashboard drives, so there is exactly one
turn path and one stream contract.

``personalclaw chat`` (``cli_chat``) is the other client of the same chat, and the attended
one: its turns run in an ordinary chat, so a call that asks for approval asks you, wherever
your approvals reach you. ``run`` is the headless one, for a script or CI with nobody there
to ask, which is why it is a command of its own rather than a flag on ``chat``. Both reach
this home's gateway through ``home_gateway.reach``, then the token and the turn socket below;
the two are told apart in ``docs/reference/cli.md``.

Safety posture (fail-CLOSED, and the reason this module exists at all):

* The session key is ``inbound:cli:<...>``, which ``guardrails.policy`` classifies as
  unattended, so the run resolves through the ``HEADLESS`` SafetyProfile by
  construction rather than by anything this module remembers to pass.
* Read-only default is enforced by the session's TASK MODE (``ask``), not by
  ``SafetyProfile.tool_grants``. ``tool_grants`` is enforced (``guardrails.policy.
  tool_grant_denial``, at the MCP tool handler, the spawn approval loop and the sandbox
  gateway), but it is enforced against the posture the SEAM owns — a workflow leaf's
  compiled capability, a spawn's capability class — and NOT against
  ``HEADLESS.tool_grants``. Reading the session profile here would make ``--allow``
  unusable: this run resolves HEADLESS, whose tier is ``read``, so the explicit write
  grant below would be vetoed by the very posture the caller asked to widen.
  ``task_mode_denies`` is deny-by-default, runs BEFORE the approval gate, and is
  documented as un-bypassable by Trust/YOLO — it is the read-only posture that holds for
  a whole CLI turn.
* ``--allow`` is the explicit write grant, printed to stderr at start so a script is
  self-documenting about the posture it asked for. It is two writes, because a call that
  changes something passes two gates: the task mode (``agent``) admits it, and Trust on the
  run's own chat approves it (:func:`grant_writes`). A headless turn has nobody to ask, so
  without the second a call that asks for approval is declined, and ``--allow`` ran nothing
  it promised. Trust is a grant, so the operator ceiling bounds it: under ``approval: ask`` the
  run is refused, saying why, rather than started with a grant that does not hold. It is the
  run's, for the run's turn: the gateway ends it when that turn ends or is stopped
  (``dashboard.headless_run``), so a helper's report the run did not wait for, or the next run
  of a named session, gets none of it.
* Without ``--allow`` the run's chat is not trusted: a tool that declares it only reads runs,
  since a read asks nobody, and a call that would ask is declined.
* The turn works in the gateway, not in this process, so a run that stops waiting for it (its
  ``--timeout`` passes, a stop signal reaches it, its connection to the gateway closes) stops
  the turn there before it exits, as Stop does in the dashboard, and says what ended the run
  and what the gateway did (:func:`_consume`). The gateway ends the run's Trust before it asks
  the turn to stop, so no call is approved on it while the turn winds down.
* An agent CLI is held the same way: an unattended turn tells it its asking mode, so every call
  it asks about meets the task mode before anything could approve it, and the unattended
  fail-fast declines, with its reason, what nothing approves. A call its own settings let it run
  without asking is outside that, as in any chat on it: the run reports it, and stops the turn
  when it may have changed something (:func:`grant_notice` says so).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator, Callable, Iterator
from typing import TYPE_CHECKING, Any

from personalclaw import home_gateway, session_keys

if TYPE_CHECKING:
    import aiohttp

#: Session-key prefix for a headless CLI turn. ``session_keys.INBOUND`` classifies the
#: ``inbound:`` kind as unattended, so this prefix is what makes the run HEADLESS.
CLI_SESSION_PREFIX = session_keys.INBOUND.key("cli:")

#: SpendMeter run scope for CLI turns: their budgets ride the meter under this key.
#: The meter's parameter is ``run_key``, not ``scope_key``.
CLI_RUN_KEY = "cli"

VALID_FORMATS = ("plain", "json", "streaming-json")

#: How long to wait for a transient gateway to print its ``PERSONALCLAW_READY:`` line.
_BOOT_TIMEOUT_SECS = 90.0

#: Default ceiling on one headless turn. Overridable with ``--timeout``.
_DEFAULT_TURN_TIMEOUT_SECS = 600.0

#: TTL for the token minted for one CLI invocation, in the token endpoint's own grammar. Short on
#: purpose: a headless run is seconds-to-minutes, and a token that outlives its run is a live
#: credential for nothing. (It counts against the TOKEN limit only, so however many runs there
#: are, none of them can sign the operator's browser or phone out.) It was the bare
#: number ``3600``, which is not a duration the endpoint reads, so every run was quietly minted
#: the endpoint's default lifetime instead — and the endpoint now refuses it. A run that may last
#: longer gets a token that lasts as long as it may (:func:`_token_ttl`).
_TOKEN_TTL = "1h"

#: How long a run follows a turn it stopped until the gateway says it has ended (``chat`` too).
_STOP_WAIT_SECS = 30.0

#: How long a run waits for the gateway to answer its stop. The gateway answers once the turn's
#: runtime has acknowledged the stop, which it waits up to a minute for (its soft-stop budget)
#: before it ends the runtime instead.
_STOP_ANSWER_SECS = 90.0

#: How a run that ended its turn itself says it ended, as its JSON report's ``outcome``, beside the
#: four a ``chat_done`` says (``complete``, ``stopped``, ``error``, ``interrupted``): its
#: ``--timeout`` passed, a stop signal reached it, or its connection to the gateway closed.
TIMED_OUT = "timed_out"
CANCELLED = "cancelled"
CONNECTION_LOST = "connection_lost"

#: The signals that end a run before its turn has ended, and how the run says each: Ctrl-C, a
#: stop sent to the command (a CI job cancelled, a ``timeout`` wrapper's), its terminal closing.
_STOP_SIGNALS: dict[int, str] = {
    signal.SIGINT: "it was interrupted (Ctrl-C)",
    signal.SIGTERM: "it was told to stop (SIGTERM)",
    **({signal.SIGHUP: "its terminal closed (SIGHUP)"} if hasattr(signal, "SIGHUP") else {}),
}


class RunError(Exception):
    """A headless run could not be set up or completed. Message is user-facing."""


class TurnTimedOut(RunError):
    """The time a run gave its turn passed before the turn ended."""


class TurnConnectionLost(RunError):
    """The socket a turn streams on closed before the turn ended."""


class GatewayGone(RunError):
    """Nothing answered on the gateway's port: it has gone away."""


# ── Gateway discovery / bootstrap ────────────────────────────────────────────────


def mint_local_token(gateway: home_gateway.HomeGateway, *, ttl: str = _TOKEN_TTL) -> str:
    """A sign-in token from this home's running gateway, for one command's requests.

    Same handshake as ``personalclaw token`` (``HomeGateway.sign_in``): the home's local
    secret, presented to the loopback-only ``/api/token/local`` of a gateway that has shown it
    serves this home (``home_gateway.reach``), and to no other. ``ttl`` is its lifetime in the
    endpoint's grammar: no longer than the command needs it. Raises :class:`RunError`.
    """
    try:
        reply = gateway.sign_in(ttl)
    except home_gateway.GatewayError as exc:
        raise RunError(str(exc)) from exc
    token = str(reply.get("token") or "")
    if not token:
        raise RunError(f"this home's gateway on port {gateway.port} returned an empty token.")
    return token


def _token_ttl(timeout: float) -> str:
    """How long the token of a run given *timeout* seconds lasts: :data:`_TOKEN_TTL`, or as long
    as the run may last when that is longer, so the stop it sends once its timeout passes still
    signs in. Never longer than the longest lifetime the gateway signs a token for."""
    from personalclaw.auth.lifetimes import MAX_LIFETIME_SECS, lifetime_seconds

    lasts = timeout + _STOP_ANSWER_SECS + _STOP_WAIT_SECS
    if lasts <= (lifetime_seconds(_TOKEN_TTL) or 0):
        return _TOKEN_TTL
    return f"{min(math.ceil(lasts / 60), MAX_LIFETIME_SECS // 60)}m"


def start_transient_gateway() -> tuple[int, str, subprocess.Popen]:
    """Boot a gateway for the lifetime of one ``run`` and return ``(port, token, proc)``.

    ``--json-ready`` is the handshake: the gateway prints ONE
    ``PERSONALCLAW_READY:{port,token,pid,home}`` line once bound, so there is no polling
    race and no need to guess the ephemeral port. ``--port auto`` keeps a transient boot
    off the operator's configured port, so it can never collide with (or be mistaken
    for) their real gateway.

    The child inherits this process's environment — deliberately, so an isolated
    ``PERSONALCLAW_HOME`` stays isolated. It is NOT detached: ``run`` owns it and kills
    it by pid in ``_shutdown_transient``, so a headless invocation cannot leave a
    gateway running behind the operator's back. It is this install's own CLI
    (``self_update.cli_argv``).
    """
    from personalclaw.env import gateway_env
    from personalclaw.self_update import cli_argv

    cmd = [*cli_argv(), "gateway", "--port", "auto", "--no-open", "--json-ready"]

    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=gateway_env(),
    )
    deadline = time.monotonic() + _BOOT_TIMEOUT_SECS
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                err = ""
                if proc.stderr is not None:
                    err = (proc.stderr.read() or "")[-2000:]
                raise RunError(
                    f"transient gateway exited with code {proc.returncode} before it was "
                    f"ready.\n{err}"
                )
            continue
        if line.startswith("PERSONALCLAW_READY:"):
            try:
                payload = json.loads(line[len("PERSONALCLAW_READY:") :])
                return int(payload["port"]), str(payload["token"]), proc
            except (ValueError, KeyError, TypeError) as exc:
                raise RunError(f"unparseable readiness line from the gateway: {line!r}") from exc
    _shutdown_transient(proc)
    raise RunError(
        f"transient gateway did not report ready within {_BOOT_TIMEOUT_SECS:.0f}s. "
        f"Start one yourself with `personalclaw gateway` and re-run."
    )


def _shutdown_transient(proc: subprocess.Popen | None) -> None:
    """Terminate a gateway ``run`` started, by pid. Never raises.

    By pid and only by pid: a pattern kill would take out the operator's real gateway
    (and any sibling process whose argv happens to match).
    """
    if proc is None or proc.poll() is not None:
        return
    with contextlib.suppress(Exception):
        proc.terminate()
    with contextlib.suppress(Exception):
        proc.wait(timeout=15)
    if proc.poll() is None:  # pragma: no cover — only a wedged child reaches here
        with contextlib.suppress(Exception):
            proc.kill()
    for stream in (proc.stdout, proc.stderr):
        with contextlib.suppress(Exception):
            if stream is not None:
                stream.close()


# ── Session identity + posture ───────────────────────────────────────────────────


def session_key_for(name: str) -> str:
    """The ``inbound:cli:`` session key for this invocation.

    A ``--session`` name gives a stable, reusable key, so the gateway rehydrates that
    session's history and the run continues a conversation. No name mints a random one,
    so the default is a fresh one-shot with no prior context. Either way the prefix is
    ``inbound:cli:``, so the HEADLESS classification does not depend on which branch
    ran — a named session is persistent, never *attended*.
    """
    cleaned = "".join(c for c in (name or "").strip() if c.isalnum() or c in "-_.")
    return f"{CLI_SESSION_PREFIX}{cleaned or secrets.token_hex(4)}"


def task_mode_for(allow: bool) -> str:
    """``agent`` when the caller passed ``--allow``, else ``ask`` (read-only).

    ``ask`` routes every tool call through ``task_modes.task_mode_denies``, which allows
    a call ONLY when ``classify_invocation`` positively answers READ_ONLY —
    ``UNCLASSIFIED`` (an opaque shell command, an unlabelled external MCP tool) is
    denied. That is the fail-closed direction: a tool this codebase cannot see is
    refused, not waved through.
    """
    return "agent" if allow else "ask"


def agent_cli_of(agent: str) -> str:
    """The name of the agent CLI ``agent`` runs on, or ``""`` for PersonalClaw's own agent.

    Read from this home's configuration, the one the gateway resolves the run's agent from. A
    binding this process cannot read names no CLI: the posture line then says what holds for
    PersonalClaw's own agent, and the gateway still runs the turn on what the agent is bound to.
    """
    try:
        from personalclaw.config import AppConfig
        from personalclaw.config.loader import resolve_agent_bindings
        from personalclaw.providers.image_input import agent_label

        kind = getattr(resolve_agent_bindings(AppConfig.load(), agent or None), "provider", "")
    except Exception:  # noqa: BLE001 — an unreadable binding names no CLI
        return ""
    kind = str(kind or "")
    return agent_label(kind) if kind.startswith("acp") else ""


def grant_notice(session_key: str, task_mode: str, *, agent_cli: str = "") -> str:
    """The stderr posture line. Printed for BOTH modes, not only for ``--allow``.

    The write grant is printed so scripts are self-documenting.
    Printing only the grant would make the read-only default the silent case — the one
    a reader cannot distinguish from "no posture was applied at all". Announcing both
    means the absence of this line is itself a signal.

    *agent_cli* names the agent CLI the run's agent runs on (:func:`agent_cli_of`), whose
    read-only holds for the calls it asks about: a call its own settings let it run without
    asking is reported, and stops the turn when it may have changed something.
    """
    if task_mode == "agent":
        return (
            f"personalclaw run: WRITE GRANT active (--allow) — session {session_key} runs "
            f"with full tool access under the headless safety profile, and trusts its own "
            f"calls, since nobody is there to approve them."
        )
    if agent_cli:
        return (
            f"personalclaw run: read-only — session {session_key} denies every non-read-only "
            f"call {agent_cli} asks about; a call its own settings let it run without asking "
            f"is reported, and stops the turn if it may have changed something. Pass --allow "
            f"to grant writes."
        )
    return (
        f"personalclaw run: read-only — session {session_key} denies every non-read-only "
        f"tool. Pass --allow to grant writes."
    )


def grant_writes(port: int, token: str, session_key: str) -> None:
    """``--allow``'s approval half: Trust on the run's own chat, so its calls are approved.

    Trust for ONE chat answers only that chat's approvals (``/api/chat/mode``), and the
    operator ceiling bounds it; a refusal raises :class:`RunError` saying why, before the
    turn is posted.
    """
    try:
        _api(port, token, "/api/chat/mode", {"mode": "trust", "session": session_key})
    except RunError as exc:
        raise RunError(f"--allow could not grant this run's writes: {exc}") from exc


# ── HTTP helpers (loopback, token-authenticated) ─────────────────────────────────


def owner_headers(token: str) -> dict[str, str]:
    """The owner token as the ``Authorization: Bearer`` header every CLI request carries.

    Never the URL: a ``?token=`` rides into the process list, shell history, a proxy's access
    log and the repr of every client error that names the URL. ``?token=`` is the browser's
    entry link, which the gateway exchanges for a session cookie; a CLI has no cookie jar to
    keep one in, and the header is the stateless carrier for exactly that credential.
    """
    return {"Authorization": f"Bearer {token}"}


def _api(port: int, token: str, path: str, body: dict | None = None) -> dict:
    """One loopback API call. Returns the decoded JSON object."""
    data = json.dumps(body or {}).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json", **owner_headers(token)},
    )
    try:
        with home_gateway.open_loopback(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = exc.read().decode()[:500]
        raise RunError(f"{path} failed: HTTP {exc.code} {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RunError(f"{path} failed: {exc}") from exc
    try:
        out = json.loads(raw)
    except ValueError as exc:
        raise RunError(f"{path} returned non-JSON: {raw[:200]!r}") from exc
    return out if isinstance(out, dict) else {"result": out}


# ── The turn ─────────────────────────────────────────────────────────────────────


class _Collector:
    """Accumulates the WS frames of one turn into the ``json`` document."""

    def __init__(self, session_key: str, fmt: str) -> None:
        self.session_key = session_key
        self.fmt = fmt
        self.text_parts: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.done = False
        # How the turn ended, as its final `chat_done` says: "complete", "stopped", "error" or
        # "interrupted" (`chat_runner.terminal_outcome_for_turn`). The error rows alone cannot say
        # it: a retry notice is an error row, and the retry after it can still finish the turn.
        self.outcome = ""

    def feed(self, envelope: dict) -> None:
        """Consume one ``{"type", "data"}`` envelope. Ignores other sessions' frames."""
        kind = str(envelope.get("type", ""))
        data = envelope.get("data")
        if not isinstance(data, dict) or data.get("session") != self.session_key:
            return
        if self.fmt == "streaming-json":
            # NDJSON of the SAME envelopes the dashboard consumes — one stream contract.
            # Emitted only for the three frame kinds below so the contract is a promise
            # about a named set, not "whatever the gateway happened to broadcast".
            if kind in ("chat_chunk", "tool_call", "chat_done"):
                sys.stdout.write(json.dumps(envelope, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        if kind == "chat_chunk":
            self.text_parts.append(str(data.get("content", "")))
        elif kind == "tool_call":
            self.tool_calls.append(
                {"name": str(data.get("tool", "")), "ok": True, "kind": str(data.get("kind", ""))}
            )
        elif kind == "tool_result":
            # A denied tool is reported as a tool_result carrying the deny reason, and a call
            # that failed (an agent CLI's call refused at the gate among them) as one saying
            # `ok: false`; mark the matching call not-ok so `tool_calls[].ok` is a measured
            # field rather than a constant True (which would make the json doc's `ok` decorative).
            out = str(data.get("output", ""))
            failed = data.get("ok") is False
            if self.tool_calls and (failed or "mode —" in out or "denied" in out.lower()):
                self.tool_calls[-1]["ok"] = False
        elif kind == "chat_message" and str(data.get("role", "")) == "error":
            self.errors.append(str(data.get("content", "")))
        elif kind == "chat_done":
            self.outcome = str(data.get("outcome", ""))
            self.done = True

    def result_text(self) -> str:
        return "".join(self.text_parts).strip()


@contextlib.asynccontextmanager
async def _turn_socket(
    port: int, token: str, session_key: str, prompt: str
) -> AsyncIterator[tuple[aiohttp.ClientSession, aiohttp.ClientWebSocketResponse]]:
    """Post *prompt* as the next turn of chat *session_key*, and yield the socket it streams on
    with the HTTP session that posted it (which can still ask the gateway for more, as a stop).

    The WS is opened BEFORE the POST: ``POST /api/chat?ws=1`` returns as soon as the
    turn task is created, so a reader attached afterwards races the first chunk. No
    ``Origin`` header is sent — ``dashboard.origin.check_origin`` trusts a loopback peer
    that sends none, and sending a wrong one is a 403. A gateway that cannot be reached,
    or refuses the socket, is a :class:`RunError` that says so, as every other request's is.
    """
    import aiohttp

    base = f"http://127.0.0.1:{port}"
    try:
        async with aiohttp.ClientSession(headers=owner_headers(token)) as http:
            async with http.ws_connect(base + "/api/ws") as ws:
                resp = await http.post(
                    base + "/api/chat?ws=1",
                    json={"message": prompt, "session": session_key},
                )
                async with resp:
                    if resp.status != 200:
                        raise RunError(
                            f"POST /api/chat failed: HTTP {resp.status} {await resp.text()}"
                        )
                yield http, ws
    except aiohttp.ClientError as exc:
        raise RunError(
            f"the turn's connection to the gateway on port {port} failed: {exc}"
        ) from exc


async def _read_turn(
    ws: aiohttp.ClientWebSocketResponse, collector: _Collector, timeout: float | None
) -> None:
    """Feed the turn's frames to *collector* until its ``chat_done``. ``timeout`` ``None`` waits
    for as long as the gateway runs the turn: it is the gateway that says when a turn ends.

    Raises :class:`TurnTimedOut` once *timeout* passes and :class:`TurnConnectionLost` when the
    socket closes first. Either way the turn may still be running in the gateway."""
    import aiohttp

    late = f"the turn did not finish within {timeout:.0f}s" if timeout is not None else ""
    deadline = None if timeout is None else time.monotonic() + timeout
    while not collector.done:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise TurnTimedOut(late)
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
        except TimeoutError as exc:
            raise TurnTimedOut(late) from exc
        if msg.type is aiohttp.WSMsgType.TEXT:
            with contextlib.suppress(ValueError):
                envelope = json.loads(msg.data)
                if isinstance(envelope, dict):
                    collector.feed(envelope)
        elif msg.type in (
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.ERROR,
        ):
            raise TurnConnectionLost("gateway closed the websocket before the turn finished")


async def _ask_to_stop(
    http: aiohttp.ClientSession,
    port: int,
    session_key: str,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Ask the gateway to stop the turn of chat *session_key*, as Stop does in the dashboard, and
    return its answer: ``stopped``, whether this stopped a turn, and ``trust``, whether the chat's
    Trust still stands. *headers* sign the request in when the session's own no longer do.

    Raises :class:`GatewayGone` when nothing answers on the gateway's port, and
    :class:`RunError` when the gateway does not stop the turn or does not answer in time.
    """
    import aiohttp

    quoted = urllib.parse.quote(session_key, safe="")
    try:
        async with http.post(
            f"http://127.0.0.1:{port}/api/chat/sessions/{quoted}/stop",
            json={},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=_STOP_ANSWER_SECS),
        ) as resp:
            if resp.status != 200:
                detail = (await resp.text())[:300]
                raise RunError(f"the gateway did not stop the turn: HTTP {resp.status} {detail}")
            answer = await resp.json(content_type=None)
    except TimeoutError as exc:
        raise RunError(
            f"the gateway did not answer the stop within {_STOP_ANSWER_SECS:.0f}s"
        ) from exc
    except (aiohttp.ClientConnectionError, OSError) as exc:
        raise GatewayGone(f"nothing answers on port {port} ({exc})") from exc
    except (aiohttp.ClientError, ValueError) as exc:
        raise RunError(f"the gateway's answer to the stop could not be read: {exc}") from exc
    return answer if isinstance(answer, dict) else {}


class _Ending:
    """How a run ended its turn itself, when it did: why, and what the gateway did about it.

    The first stop signal ends the run: it cancels the wait for the turn's frames (:attr:`reading`)
    and the run stops the turn, sends that stop whatever comes next, and follows the stopped turn
    to its end. A later signal gives up on all of it (:attr:`forced`): the command was asked twice.
    A turn that ended in the gateway before the run stopped it stays the gateway's to say.
    """

    def __init__(self) -> None:
        #: :data:`TIMED_OUT`, :data:`CANCELLED` or :data:`CONNECTION_LOST` once the run is ending
        #: the turn itself; "" while the turn runs, and for one that ended in the gateway.
        self.why = ""
        #: Whether the run is ending the turn (or a stop signal asked it to), and the first stop
        #: signal the command got, 0 for none.
        self.stopping = False
        self.signum = 0
        #: The gateway's answer to the stop (:func:`_ask_to_stop`); None when it gave none.
        self.answer: dict[str, Any] | None = None
        #: Why the gateway could not be told, and whether nothing answered on its port at all.
        self.failed = ""
        self.gone = False
        #: Whether the turn's ``chat_done`` arrived after the stop.
        self.ended = False
        #: Whether a later signal gave up waiting.
        self.forced = False
        self.reading: asyncio.Future[None] | None = None
        self.main: asyncio.Future[None] | None = None

    def on_signal(self, signum: int) -> None:
        if self.stopping:
            self.signum = self.signum or signum
            self.forced = True
            if self.main is not None:
                self.main.cancel()
            return
        self.stopping, self.signum = True, signum
        if self.reading is not None:
            self.reading.cancel()

    def stop(self, why: str) -> None:
        """The run ends the turn itself, for *why*: a later stop signal gives up waiting."""
        self.why, self.stopping = why, True


@contextlib.contextmanager
def _taking_stop_signals(handler: Callable[[int], None]) -> Iterator[None]:
    """Hand each stop signal to *handler* while the run's loop runs, and give it back as it was.

    A signal the command was started ignoring (``nohup``, a job started in the background) stays
    ignored. Where the loop cannot take a signal (off the main thread) none is taken."""
    loop = asyncio.get_running_loop()
    taken: list[tuple[int, Any]] = []
    for signum in _STOP_SIGNALS:
        before = signal.getsignal(signum)
        if before == signal.SIG_IGN:
            continue
        try:
            loop.add_signal_handler(signum, handler, signum)
        except (NotImplementedError, RuntimeError, ValueError):
            continue
        taken.append((signum, before))
    try:
        yield
    finally:
        for signum, before in taken:
            loop.remove_signal_handler(signum)
            if before is not None:
                signal.signal(signum, before)


async def _consume(
    port: int, token: str, collector: _Collector, prompt: str, timeout: float
) -> _Ending | None:
    """Post the turn and consume its frames until ``chat_done``, within *timeout* seconds.

    The turn works in the gateway, so a run that stops waiting for it first (its timeout passes,
    a stop signal reaches it, its connection closes) stops it there before it returns, and returns
    how it ended (:class:`_Ending`). None when the turn ended in the gateway on its own terms.
    """
    ending = _Ending()
    with _taking_stop_signals(ending.on_signal):
        ending.main = asyncio.ensure_future(
            _run_the_turn(port, token, collector, prompt, timeout, ending)
        )
        try:
            await ending.main
        except asyncio.CancelledError:
            if not ending.forced:
                raise
    return ending if ending.why else None


async def _run_the_turn(
    port: int, token: str, collector: _Collector, prompt: str, timeout: float, ending: _Ending
) -> None:
    """Post the turn and read it until its ``chat_done``; stop it in the gateway when the run
    stops waiting for it first (:func:`_stop`)."""
    import aiohttp

    async with _turn_socket(port, token, collector.session_key, prompt) as (http, ws):
        if ending.stopping:  # a stop signal came while the turn was being posted
            ending.stop(CANCELLED)
        else:
            ending.reading = asyncio.ensure_future(_read_turn(ws, collector, timeout))
            try:
                await ending.reading
                return
            except asyncio.CancelledError:
                here = asyncio.current_task()
                if not ending.stopping or (here is not None and here.cancelling()):
                    raise
                ending.stop(CANCELLED)
            except TurnTimedOut:
                ending.stop(TIMED_OUT)
            except (TurnConnectionLost, aiohttp.ClientError):
                ending.stop(CONNECTION_LOST)
            finally:
                ending.reading = None
        await _stop(http, port, collector, ws, ending)


async def _stop(
    http: aiohttp.ClientSession,
    port: int,
    collector: _Collector,
    ws: aiohttp.ClientWebSocketResponse,
    ending: _Ending,
) -> None:
    """Stop the turn the run has stopped waiting for, and follow it to its end while its socket is
    open. The gateway ends the Trust the run gave its chat before it asks the turn to stop."""
    import aiohttp

    try:
        ending.answer = await _ask_to_stop(http, port, collector.session_key)
    except GatewayGone as exc:
        ending.failed, ending.gone = str(exc), True
        return
    except RunError as exc:
        ending.failed = str(exc)
        return
    if ending.why == CONNECTION_LOST or ws.closed:
        return
    with contextlib.suppress(RunError, aiohttp.ClientError):
        await _read_turn(ws, collector, _STOP_WAIT_SECS)
    ending.ended = collector.done


def _token_total(session_key: str) -> int:
    """Total tokens this session billed, from the usage ledger. 0 when unreadable.

    No WS frame carries token counts, so the count comes from the ledger the gateway
    writes under ``config_dir()/usage/turns.jsonl`` — readable here precisely because
    ``run`` talks only to the gateway of its own home (``home_gateway.reach``). Best-effort:
    a missing ledger reports 0 rather than failing a turn that already succeeded.

    🔴 The ledger keys rows by the DASHBOARD-WRAPPED provider key
    (``dashboard:inbound:cli:<id>``), not by the session name. Querying the bare key
    matched nothing and reported a confident ``"tokens": 0`` on a turn that had really
    billed 22,979 — a decorative field, not a measured one. ``dashboard_history_key`` is
    the write site's own rule, so the query cannot drift from it. It is imported from
    ``constants``, NOT from ``dashboard.chat_utils``: reaching up into the HTTP surface for
    a naming rule is the inversion ``core-must-not-import-the-http-surface`` exists to
    catch, and the gate caught it here.
    """
    try:
        from personalclaw import usage_ledger
        from personalclaw.constants import dashboard_history_key

        agg = usage_ledger.totals(session_key=dashboard_history_key(session_key))
        return int(agg.get("input_tokens", 0)) + int(agg.get("output_tokens", 0))
    except Exception:  # noqa: BLE001 — telemetry must never fail a completed turn
        return 0


def _run_one(args) -> int:
    """Execute one headless turn. Returns the process exit code."""
    prompt = (getattr(args, "prompt", "") or "").strip()
    if not prompt:
        # -p is `required=True` at the parser, so argparse already refuses an omitted
        # flag. This catches `-p ""` and `-p "   "`, which argparse accepts: a bench in
        # this repo shipped with a defaulted "" that the callee refused, so every test
        # passed and the command a human types could never do anything.
        print(
            "personalclaw run: -p/--prompt must be a non-empty prompt.",
            file=sys.stderr,
        )
        return 2

    fmt = getattr(args, "format", "plain") or "plain"
    if fmt not in VALID_FORMATS:  # pragma: no cover — argparse `choices` fences this
        print(f"personalclaw run: unknown --format {fmt!r}", file=sys.stderr)
        return 2

    session_key = session_key_for(getattr(args, "session", "") or "")
    task_mode = task_mode_for(bool(getattr(args, "allow", False)))
    agent_cli = agent_cli_of(getattr(args, "agent", "") or "") if task_mode == "ask" else ""
    print(grant_notice(session_key, task_mode, agent_cli=agent_cli), file=sys.stderr, flush=True)

    timeout = float(getattr(args, "timeout", 0) or _DEFAULT_TURN_TIMEOUT_SECS)
    gateway: home_gateway.HomeGateway | None = None
    transient: subprocess.Popen | None = None
    started = time.monotonic()
    try:
        try:
            gateway = home_gateway.reach(getattr(args, "port", None))
        except home_gateway.NoGatewayRunning:
            print(
                "personalclaw run: no gateway of this home is running — starting a transient one.",
                file=sys.stderr,
                flush=True,
            )
            port, token, transient = start_transient_gateway()
        except home_gateway.GatewayError as exc:
            raise RunError(str(exc)) from exc
        else:
            port, token = gateway.port, mint_local_token(gateway, ttl=_token_ttl(timeout))

        _api(
            port,
            token,
            "/api/chat/sessions",
            {
                "name": session_key,
                "agent": getattr(args, "agent", "") or "",
                "model": getattr(args, "model", "") or "",
            },
        )
        # The read-only rail, set BEFORE the turn is posted so it is in force for the
        # first tool call rather than applied after one has already run.
        #
        # 🔴 It must go through `/api/chat/task-mode`, NOT the session-create body's
        # `mode` key. Those are different fields: create's `mode` writes
        # `_ChatSession.mode`, while the tool gate reads `_ChatSession._task_mode`, and
        # `apply_task_mode` is the ONE write path because the mode is TWO writes (the
        # session's posture AND the runtime's, via `set_task_mode`). Creating
        # the session with `{"mode": "ask"}` left `_task_mode` at its `"agent"` default,
        # and a headless run then wrote a file to disk while announcing "read-only" on
        # stderr — a read-only promise that denied nothing.
        _api(port, token, "/api/chat/task-mode", {"mode": task_mode, "session": session_key})
        cwd = getattr(args, "cwd", "") or ""
        if cwd:
            _api(
                port,
                token,
                f"/api/chat/sessions/{session_key}/workspace-dir",
                {"workspace_dir": str(os.path.abspath(os.path.expanduser(cwd)))},
            )
        # The write grant last, just before the turn it is for, so a setup that fails leaves it
        # standing nowhere.
        if task_mode == "agent":
            grant_writes(port, token, session_key)

        collector = _Collector(session_key, fmt)
        ending = asyncio.run(_consume(port, token, collector, prompt, timeout))
    except RunError as exc:
        print(f"personalclaw run: {exc}", file=sys.stderr)
        return 1
    finally:
        _shutdown_transient(transient)

    duration_ms = int((time.monotonic() - started) * 1000)
    outcome = ending.why if ending is not None else collector.outcome
    ok = outcome == "complete"
    if fmt == "json":
        print(
            json.dumps(
                {
                    "result": collector.result_text(),
                    "session": session_key,
                    "outcome": outcome,
                    "turns": 1,
                    "tool_calls": [
                        {"name": t["name"], "ok": t["ok"]} for t in collector.tool_calls
                    ],
                    "tokens": _token_total(session_key),
                    "duration_ms": duration_ms,
                },
                indent=2,
            )
        )
    elif fmt == "plain":
        text = collector.result_text()
        if text:
            print(text)
    # streaming-json already wrote its NDJSON as frames arrived.

    if ending is not None:
        said = _how_the_run_ended(
            ending,
            timeout=timeout,
            allow=task_mode == "agent",
            gateway=gateway,
            transient=transient is not None,
        )
        for line in said:
            print(f"personalclaw run: {line}", file=sys.stderr)
        # A stop signal's exit is the one the shell reads for that signal (`_run` ends with it).
        return 128 + ending.signum if ending.signum else 1
    if collector.outcome == "stopped":
        print("personalclaw run: the turn was stopped before it finished", file=sys.stderr)
    elif collector.outcome == "interrupted":
        # Its error row says which: the gateway restarted, or shut down, before the reply finished.
        for err in collector.errors or ["the gateway stopped before the turn finished"]:
            print(f"personalclaw run: {err}", file=sys.stderr)
    elif not ok:
        for err in collector.errors or ["the gateway reported no reason"]:
            print(f"personalclaw run: turn failed: {err}", file=sys.stderr)
    return 0 if ok else 1


def _how_the_run_ended(
    ending: _Ending,
    *,
    timeout: float,
    allow: bool,
    gateway: home_gateway.HomeGateway | None,
    transient: bool,
) -> list[str]:
    """What a run that ended its turn itself says: what ended it, then what the gateway did with
    the stop, and what became of the run's write grant when it has one."""
    if ending.why == TIMED_OUT:
        why = f"the turn did not finish within {timeout:.0f}s."
    elif ending.why == CANCELLED:
        why = f"{_STOP_SIGNALS.get(ending.signum, 'it was stopped')} before the turn finished."
    else:
        why = "the connection to the gateway closed before the turn finished."
    answer = ending.answer
    if answer is not None:
        if answer.get("stopped") is True:
            done = "stopped the turn in the gateway."
            if not ending.ended:
                done = (
                    "stopped the turn in the gateway, which is still ending it: the chat in the "
                    "dashboard shows when it has."
                )
        elif ending.ended:
            done = "the turn has ended in the gateway."
        else:
            done = (
                "the gateway is already ending the turn: the chat in the dashboard shows when it "
                "has."
            )
        if not allow:
            return [why, done]
        trust = (
            "this run's write grant has ended."
            if answer.get("trust") is False
            else "this run's write grant still stands in the gateway."
        )
        return [why, done, trust]
    if ending.forced:
        done = "stopped waiting for the gateway at a second signal."
    elif ending.gone:
        done = (
            f"the gateway could not be told to stop the turn: "
            f"{gateway.gone() if gateway is not None else ending.failed}"
        )
    else:
        done = f"the gateway did not stop the turn: {ending.failed}"
    if transient:
        after = ["the gateway this run started is shut down with it, and the turn with it."]
    elif allow:
        after = ["if the turn still runs there, this run's write grant ends when the turn does."]
    else:
        after = []
    return [why, done, *after]


def _run(args) -> None:
    """``personalclaw run`` entry point — dispatched from ``cli.main``."""
    code = _run_one(args)
    signum = code - 128
    if signum in _STOP_SIGNALS:
        # A stop signal ended the run, which stopped its turn first. It now ends as that signal
        # ends a program, so the shell or the job that sent it reads the exit it always read.
        sys.stdout.flush()
        sys.stderr.flush()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    raise SystemExit(code)
