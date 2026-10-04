"""Long-lived in-process MCP client — external MCP servers in the native loop.

Unlike :func:`mcp_discovery.probe_server` (one-shot spawn → read → die), this
keeps a connection alive for the process and routes ``tools/list`` / ``tools/call``
over it. It is what lets PersonalClaw's *native* agent loop invoke tools from an
external MCP server configured in ``~/.personalclaw/mcp.json`` — independent of
the ACP CLI backends, which spawn their own MCP servers.

Built on the official ``mcp`` Python SDK, a core dependency. The SDK's clients are
async-context-manager based, so each server runs as a small **actor**: one background task
holds the transport + session context open and serves ``list_tools`` / ``call_tool`` requests
off a queue, with health/respawn and clean shutdown (drained by the gateway's reaper on exit).

A call is sent at most once, and never after its caller stopped waiting: one still queued when its
deadline passes, or when its turn stops, is dropped unsent and says it was not sent. One the server
was given and never answered, by the deadline or before its connection ended, says it may have gone
through, never that it failed, since calling it again may do it twice. That holds on every transport
and on a connection shared by many chats, because the actor is the one place a call is sent from. A
connection its session saw end (a stdio program ending, an SSE stream closing) answers the calls on
it then and is started again by its next call. The SDK's Streamable HTTP client reports no broken
response stream, so over it the deadline is what answers a call left out.

Transports: stdio (``command``/``args``/``env``, started by `mcp_stdio`, which knows how the program
ended), and a server at a ``url`` over Streamable HTTP or SSE, sent its ``headers`` on every
request — which one is the spec's ``type``, read by
:func:`personalclaw.mcp_discovery.mcp_transport` — and, for a server its owner signed in to, the
bearer token of that sign-in (:func:`personalclaw.mcp_oauth.connection_auth`, given to the same
transport client as its ``auth``). The SDK is imported at the first connection,
not at module load: it brings pydantic with it (~0.22 s), which a process that never connects to
a server — most CLI commands — has no reason to pay.

Every start is recorded where the Tools page reads it (`mcp_discovery.note_start`): what it found,
in the words of `mcp_status`, and how many starts in a row failed. A server that failed
``mcp_status.STOP_AFTER`` times in a row is not started again — by any connection, in any chat —
until its owner presses Retry or its definition changes.

A server at a URL is reached through the egress guard's judgement: its start and every tool call
over its connection ask the guard about its URL first (``net.policy.MCP_SERVER``), for the run the
call is made for, so the owner's Denied hosts and a run's egress tier hold for it, and a refused
call sends nothing and says why. A refusal is that call's answer, never a failed start.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from personalclaw import mcp_stdio
from personalclaw import trace_recorder as _trace
from personalclaw.cancellation import cancel_and_wait
from personalclaw.mcp_status import (
    StartFailure,
    _seconds,
    closed,
    command_not_found,
    did_not_answer,
    exited,
    still_starting,
    switched_off,
)
from personalclaw.mcp_stdio import CommandNotFound, StdioRun, stop_finishing, stop_finishing_soon

if TYPE_CHECKING:
    from personalclaw.tool_providers.base import RiskLevel

logger = logging.getLogger(__name__)

# Per-call ceiling so one wedged tool can't stall a chat turn indefinitely.
_CALL_TIMEOUT_SECS = 120.0
# Handshake ceiling — a server that never completes initialize is marked failed. The actor holds
# it, so a start's outcome is decided once, however many callers are waiting on it.
_CONNECT_TIMEOUT_SECS = 30.0
# How much longer than the deadline a caller waits for the actor's answer: putting a server that
# did not answer away is bounded too (`mcp_stdio`).
_ANSWER_SLACK_SECS = 10.0
# Reap a connection unused for this long (frees the subprocess; respawns on next
# use). Bounds resident MCP memory to actively-used (server, session) pairs.
_IDLE_TTL_SECS = 600.0
# How often the registry sweeps for idle connections.
_SWEEP_INTERVAL_SECS = 120.0
# How long closing one server's connection may take before it is abandoned (and logged), so a
# server that will not stop cannot hold up the app unload that is replacing it.
_CLOSE_TIMEOUT_SECS = 10.0
# The door a remote server's rows in the audit log name (`egress_fetch` from `mcp:mcp_server`).
_EGRESS_DOOR = "mcp"


@dataclass
class McpToolSpec:
    """One tool advertised by a connected server (provider-neutral)."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    #: The server's own ``annotations`` for the tool (``readOnlyHint``, ``destructiveHint``, …),
    #: as it sent them. A claim, not a fact: :func:`declared_risk` is what reads it.
    annotations: dict[str, Any] = field(default_factory=dict)


def declared_risk(server: str, tool: McpToolSpec) -> "RiskLevel":
    """What an external server's tool is taken to do, as a ``RiskLevel``. The approval gate's one
    read of the owner's trust, and every surface that shows a tool's risk reads it here too.

    The server's ``readOnlyHint`` counts only when the owner trusts that server's labels, and only
    for the tool exactly as the owner saw it then (`mcp_read_only_trust.believes`, which compares
    the digest of its definition): a server can call anything read-only, and believing it would let
    its tool run without a card and in Ask mode. So an untrusted server's tools are CAUTION — they
    ask — whatever they say, and so is a trusted server's tool that is new or changed since the
    owner trusted it; an explicit ``destructiveHint`` is believed from anyone, since it only adds a
    question.
    """
    from personalclaw.mcp_read_only_trust import believes
    from personalclaw.tool_providers.base import risk_from_annotations

    return risk_from_annotations(getattr(tool, "annotations", None), trusted=believes(server, tool))


# Strict numeric-literal guards for schema-driven arg coercion. A model (notably
# Claude-on-Bedrock via Converse) sometimes emits a numeric tool argument as a
# STRING ("128") even when the tool's inputSchema types the field as number/
# integer; a strict MCP server then rejects the call with -32602 "expected
# number, received string". We coerce such a value back to a real number — but
# ONLY against these strict patterns, NEVER bare int()/float(), because
# int("1_000")==1000 and float("inf")/float("nan")/float("1e9") all parse and
# would silently mangle a value the model never meant as that number.
_INT_LITERAL_RE = re.compile(r"^-?\d+$")
_NUM_LITERAL_RE = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")

#: The kinds a field's JSON TEXT is decoded into, once, when the schema wants exactly that kind:
#: the same models write an array, an object or a boolean as its JSON text ("[\"a.md\"]").
_JSON_KINDS: dict[str, type] = {"boolean": bool, "array": list, "object": dict}


def _schema_kind(prop_schema: object) -> str | None:
    """The kind ``prop_schema`` types a field as when it permits no string: ``integer``,
    ``number``, ``boolean``, ``array`` or ``object``. A ``type`` that is a list
    (e.g. ``["integer", "null"]``) has one; one that also allows ``"string"``
    (``["string", "integer"]``) has none. anyOf/oneOf/$ref branches are treated
    as "do not coerce" (unknown shape → leave the value alone)."""
    if not isinstance(prop_schema, dict):
        return None
    if any(k in prop_schema for k in ("anyOf", "oneOf", "allOf", "$ref")):
        return None
    t = prop_schema.get("type")
    types = {t} if isinstance(t, str) else set(t) if isinstance(t, list) else set()
    if "string" in types:
        return None
    return next((k for k in ("integer", "number", *_JSON_KINDS) if k in types), None)


def _decoded(text: str, kind: str) -> Any:
    """*text* decoded as JSON when it is the text of exactly a *kind* value, else None. Decoded
    once: an array encoded twice is still text after one decode, and is left for the server."""
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if type(value) is _JSON_KINDS[kind] else None


def _coerce_args_to_schema(
    arguments: dict[str, Any], input_schema: dict[str, Any] | None
) -> dict[str, Any]:
    """Turn a STRING arg back into the type the tool's inputSchema declares for it: a
    numeric-looking string into a number, and the JSON text of an array, an object or a
    boolean into that value. Only top-level properties are handled (nested values are a
    known, accepted limitation), and only a field that takes no string at all. Non-string
    values, string-typed fields, and strings that are not strictly a value of the declared
    kind are left untouched, so a genuinely bad value still reaches the server's own check
    (and :func:`argument_refusal` names it before anyone is asked)."""
    if not isinstance(input_schema, dict) or not isinstance(arguments, dict):
        return arguments
    props = input_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return arguments
    out: dict[str, Any] = dict(arguments)
    for key, val in arguments.items():
        if not isinstance(val, str):
            continue
        kind = _schema_kind(props.get(key))
        if kind == "integer" and _INT_LITERAL_RE.match(val):
            out[key] = int(val)
        elif kind == "number" and _NUM_LITERAL_RE.match(val):
            out[key] = float(val)
        elif kind in _JSON_KINDS and (decoded := _decoded(val, kind)) is not None:
            out[key] = decoded
    return out


def argument_refusal(tool: str, arguments: dict[str, Any], input_schema: Any) -> str:
    """Why an MCP server refuses *tool* called with *arguments* whatever anyone answers: an
    argument whose type its *input_schema* does not declare, judged on the arguments as
    :meth:`McpServerConn.call_tool` sends them (:func:`_coerce_args_to_schema`). The schema is
    the server's validator, so a type it does not declare is refused there. ``""`` when the
    schema admits them, or cannot be checked."""
    from personalclaw.tool_providers.arguments import mistyped_arguments, mistyped_arguments_note

    if not isinstance(input_schema, dict):
        return ""
    problems = mistyped_arguments(_coerce_args_to_schema(arguments, input_schema), input_schema)
    if not problems:
        return ""
    return mistyped_arguments_note(tool, problems, input_schema)


@dataclass
class _Call:
    """One tool call on its way to a server: what it calls, the answer its caller waits on, and
    whether the connection's worker has handed it to the session. From that moment it may reach the
    server, whatever becomes of its answer."""

    tool: str
    arguments: dict[str, Any]
    answer: asyncio.Future[tuple[bool, str]]
    sent: bool = False


def _not_sent_text(tool: str, why: str) -> str:
    """What a call that never reached its server says: that, and *why*."""
    return f"MCP tool '{tool}' was not sent: {why}. Nothing reached the server."


def _may_have_gone_through_text(tool: str, server: str, what_followed: str) -> str:
    """What a call says that its server was given and never answered: it may have done it, so
    calling it again may do it twice."""
    return (
        f"MCP tool '{tool}' may have gone through: PersonalClaw sent it to '{server}' and "
        f"{what_followed}. Check whether it did what it was meant to before you call it again, "
        "or it may happen twice."
    )


class McpServerConn:
    """An actor owning one MCP server's live connection.

    The connection is established lazily on first use and held open by a
    background task. Calls are marshalled to that task so the SDK's anyio task
    scope stays on a single task (its context managers are not reentrant across
    tasks). The task sends them one at a time, each at most once (:meth:`_serve`).
    """

    def __init__(self, name: str, spec: dict[str, Any], scope: str = "") -> None:
        self.name = name
        self.spec = spec
        # "" = shared (poolable server); else the owning session key (isolation).
        self.scope = scope
        self._task: asyncio.Task | None = None
        self._requests: asyncio.Queue[_Call] | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._tools: list[McpToolSpec] = []
        self._error: str = ""
        # The last connection failed because the server wants its owner to sign in (again): it
        # answered 401 with a Bearer challenge, or its sign-in has ended (mcp_oauth).
        self._sign_in_needed = False
        self._closing = False
        # Idle reaping: bump on every use; the registry sweeper reaps when stale.
        self._last_used: float = time.monotonic()
        # How long a start may take to answer: the agent's ceiling, or the probe's (`try_start`).
        self._connect_timeout: float = _CONNECT_TIMEOUT_SECS
        # The last start of a stdio server: how its program ended, and its error output.
        self._stdio: StdioRun | None = None
        # Why the last start failed, in `mcp_status`'s words; None after one that connected.
        self._failure: StartFailure | None = None
        # Whether the last start ran out of `_connect_timeout`.
        self._timed_out = False
        # The definition this connection starts (`mcp_discovery.definition_seal`), worked out at
        # the first start; the probe passes the one it read.
        self._seal: str | None = None

    @property
    def error(self) -> str:
        return self._error

    @property
    def sign_in_needed(self) -> bool:
        """Whether the last connection failed because the server wants its owner to sign in."""
        return self._sign_in_needed

    @property
    def last_used(self) -> float:
        return self._last_used

    @property
    def started(self) -> bool:
        return self._task is not None and not self._task.done()

    def touch(self) -> None:
        self._last_used = time.monotonic()

    async def ensure_started(self) -> bool:
        """Start the actor + wait for the handshake. Returns connected-ok.

        A start of a server at a URL is asked of the egress guard first, for the run the call
        that starts it is made for (:meth:`_egress_refusal`): one it refuses sends nothing, and why
        is this connection's error, in the guard's words. Each tool call over the connection asks
        again, for its own run (:meth:`call_tool`), since an open connection serves every run.

        A server that failed to start ``mcp_status.STOP_AFTER`` times in a row — by this
        connection, another chat's, or the probe — is not started (`mcp_discovery.start_refused`):
        nothing is spawned, and why is this connection's error. Its owner's Retry, or a change to
        what it runs, starts it again. The actor decides each start's outcome once and records it;
        every caller waiting on it only reads it."""
        self.touch()
        if not self.started:
            from personalclaw.mcp_discovery import start_refused

            refused = start_refused(self.name, self._definition_seal())
            if refused is not None:
                self._error = refused
                return False
            refused = await self._egress_refusal()
            # Read again after the look-up: another call may have started the connection meanwhile,
            # and neither this call's refusal nor a second start may replace the one it made.
            if refused:
                if not self.started:
                    self._error = refused
                return False
            if not self.started:
                self._begin()
        try:
            await asyncio.wait_for(
                self._ready.wait(), timeout=self._connect_timeout + _ANSWER_SLACK_SECS
            )
        except asyncio.TimeoutError:
            self._error = self._error or did_not_answer(self.name, self._connect_timeout).headline
            return False
        return not self._error

    def _begin(self) -> None:
        """Start the actor that holds the connection, its last start's outcome cleared."""
        self._requests = asyncio.Queue()
        self._ready = asyncio.Event()
        self._error = ""
        self._sign_in_needed = False
        self._closing = False
        self._failure = None
        self._timed_out = False
        if self._remote_url():
            from personalclaw.net.client import audit
            from personalclaw.net.policy import MCP_SERVER

            audit(self._shown_url(), MCP_SERVER, outcome="allowed", door=_EGRESS_DOOR)
        # The connection serves every run that calls over it, so it is made for none: what it sends
        # on its own (a sign-in's renewal) keeps to the owner's Network egress settings, and each
        # run's call is asked of the guard, for that run, before it is sent (:meth:`call_tool`).
        from personalclaw.net.policy import egress_held_to

        with egress_held_to(""):
            self._task = asyncio.create_task(self._run(), name=f"mcp-conn-{self.name}")

    def _definition_seal(self) -> str:
        if self._seal is None:
            from personalclaw.mcp_discovery import definition_seal

            self._seal = definition_seal(self.name, self.spec)
        return self._seal

    def _remote_url(self) -> str:
        """The URL of a server PersonalClaw connects to over the network, ``""`` for a stdio one."""
        from personalclaw.mcp_discovery import mcp_transport

        if mcp_transport(self.spec) not in ("http", "sse"):
            return ""
        return str(self.spec.get("url") or "")

    def _shown_url(self) -> str:
        """The server's URL as a sentence or the audit log may name it: no credential it carries."""
        from personalclaw.mcp_discovery import masked_url

        return masked_url(self._remote_url())

    async def _egress_refusal(self) -> str:
        """Why the egress guard refuses the call being made this server, ``""`` when it may.

        Asked about the server's URL (:data:`~personalclaw.net.policy.MCP_SERVER`, with the owner's
        Network egress settings on it) for the run the call is made for, whose tier the guard
        applies (``net.policy.egress_policy_for_run``): a run whose tier is off reaches no server,
        and one whose tier lists hosts reaches a server only on Allowed hosts. A refusal is in the
        audit log and its sentence is the guard's (``net.guard.refusal_for``), naming the URL
        without a credential in it. A stdio server is a program on this machine and is not asked
        about. A name that does not resolve is not a refusal: the start fails on it, in its own
        words. The look-up runs off the event loop and in this call's context, within the time a
        start has; one that does not finish in it refuses, since its answer is not known."""
        url = self._remote_url()
        if not url:
            return ""
        from personalclaw.net.client import audit
        from personalclaw.net.guard import evaluate, refusal_for
        from personalclaw.net.policy import MCP_SERVER, egress_policy_for

        policy = egress_policy_for(MCP_SERVER)
        shown = self._shown_url()
        try:
            decision = await asyncio.wait_for(
                asyncio.to_thread(evaluate, url, policy), timeout=self._connect_timeout
            )
        except asyncio.TimeoutError:
            said = slow_lookup_text(
                urlsplit(url).hostname or shown, _seconds(self._connect_timeout)
            )
            audit(shown, policy, outcome="denied", reason=said, door=_EGRESS_DOOR)
            return said
        if decision.allow or decision.category == "unresolvable":
            return ""
        audit(shown, policy, outcome="denied", reason=decision.reason, door=_EGRESS_DOOR)
        return refusal_for(shown, decision, then="then try it again")

    async def list_tools(self) -> list[McpToolSpec]:
        if not await self.ensure_started():
            return []
        self.touch()
        return list(self._tools)

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """Invoke ``tool``; returns ``(ok, text)``. ``ok`` is ``False`` only for a call known not
        to have done what was asked.

        A call the egress guard refuses for the run it is made for sends nothing, and its answer is
        the guard's sentence (:meth:`_egress_refusal`). Any other waits its turn on the connection,
        which sends one call at a time (:meth:`_serve`), for ``_CALL_TIMEOUT_SECS`` in all. The
        server's answer is the answer. Without one, the call says what is known:

        * one not yet sent when its caller stops waiting (the deadline passes, or its turn stops)
          is never sent, and says it was not sent; so does one whose connection ended first;
        * one the server was given, and did not answer by the deadline or before its connection
          ended, may have gone through. It says so, with ``ok`` ``True``: it is no failure, and
          calling it again may do it twice."""
        refused = await self._egress_refusal()
        if refused:
            return False, refused
        if not await self.ensure_started():
            return False, f"MCP server '{self.name}' not connected: {self._error}"
        self.touch()
        # Schema-driven arg coercion: heal a model that emitted a numeric arg as a
        # string ("128") for a number/integer-typed field, which strict MCP servers
        # reject with -32602. ensure_started() has populated self._tools, so the
        # cached inputSchema is available. Non-numeric fields are untouched.
        spec = next((t for t in self._tools if t.name == tool), None)
        if spec is not None:
            arguments = _coerce_args_to_schema(arguments, spec.input_schema)
        call = _Call(tool, arguments, asyncio.get_running_loop().create_future())
        if self._requests is None or not self.started:
            # Its connection ended after it answered the start, so nothing is left to send it.
            ok, output = False, _not_sent_text(tool, self._ended_first())
        else:
            self._requests.put_nowait(call)
            try:
                await asyncio.wait({call.answer}, timeout=_CALL_TIMEOUT_SECS)
            finally:
                # Its caller stops waiting here, at the deadline or with its turn: the worker never
                # sends it from now on. An answered call is not changed by this.
                call.answer.cancel()
            if call.answer.cancelled():
                ok, output = self._unanswered(call)
            else:
                ok, output = call.answer.result()
        # Dev-only event-trace tap (Self-Verification §2.1 MCP rider): record the
        # request/response pair so `replay` can serve it back as a fake MCP server for
        # deterministic offline debugging. No-op unless PERSONALCLAW_TRACE_DIR is set.
        if _trace.is_recording():
            _trace.record(
                "mcp",
                self.name,
                "call_tool",
                {"tool": tool, "arguments": arguments, "ok": ok, "output": output},
            )
        return ok, output

    def _unanswered(self, call: _Call) -> tuple[bool, str]:
        """What *call* says when its deadline passed with no answer: it was never sent, or it was
        and may have gone through."""
        waited = _seconds(_CALL_TIMEOUT_SECS)
        if call.sent:
            return True, _may_have_gone_through_text(
                call.tool, self.name, f"had no answer after {waited}"
            )
        return False, _not_sent_text(
            call.tool,
            f"'{self.name}' had still not answered an earlier call after {waited}, "
            "so PersonalClaw dropped it",
        )

    def _ended_first(self) -> str:
        """Why a call its connection ended before sending was not sent."""
        return f"the connection to '{self.name}' ended first"

    def _settle(self, call: _Call, ok: bool, text: str, *, answered: bool = False) -> None:
        """Give *call* its answer. When its caller already stopped waiting, what the server
        *answered* is logged instead, since nobody else hears it."""
        if not call.answer.done():
            call.answer.set_result((ok, text))
        elif answered:
            logger.warning(
                "MCP server '%s' answered '%s' after its caller had stopped waiting: it %s",
                self.name,
                call.tool,
                "succeeded" if ok else "failed",
            )

    async def shutdown(self) -> None:
        """Close the connection. Its task may be starting the server's process, which a cancel
        cannot always interrupt, so the wait for it is bounded (``cancel_and_wait``)."""
        self._closing = True
        await cancel_and_wait([self._task], what=f"MCP server '{self.name}'")

    # ── actor body ──────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Hold the transport+session open, serve queued requests until cancelled or the
        connection ends.

        The start — the transport, ``initialize`` and the first tool list — has
        ``_connect_timeout`` to answer. Its outcome is recorded once, here
        (`mcp_discovery.note_start`): every surface reads it from there. A start that its owner's
        sign-in would answer is not recorded: it is not a failure, and the probe says what it wants.

        However the connection ends, every call still queued on it is answered then, as not sent:
        nothing was left to send it, and its caller is not kept waiting for its deadline.
        """
        from personalclaw.mcp_discovery import note_start

        requests = self._requests
        assert requests is not None
        deadline = asyncio.timeout(self._connect_timeout)
        self._stdio = None
        try:
            from contextlib import AsyncExitStack

            from mcp import ClientSession

            from personalclaw.mcp_elicitation import elicitation_callback_for

            async with AsyncExitStack() as stack:
                try:
                    async with deadline:
                        read, write = await self._open_transport(stack)
                        # The ONE place the elicitation grant is consulted, resolved per
                        # server at handshake time. `None` (the default — the grant list ships
                        # empty) leaves the SDK's own default callback in place, and
                        # `ClientSession.initialize` then sends `elicitation=None`, so the
                        # capability is absent from THIS server's advertised set while a granted
                        # sibling's session advertises it. One expression, both behaviours: a
                        # branch here would be two session-construction paths to keep in step.
                        session = await stack.enter_async_context(
                            ClientSession(
                                read,
                                write,
                                elicitation_callback=elicitation_callback_for(self.name),
                            )
                        )
                        await session.initialize()
                        await self._refresh_tools(session)
                except TimeoutError:
                    # Said before the stack puts the program away: one still starting (installing
                    # what it runs) is left to finish rather than cut off part-way (`mcp_stdio`).
                    if deadline.expired() and self._stdio is not None:
                        self._stdio.stop_waiting()
                    raise
                note_start(self.name, self._definition_seal(), tools=self._tools)
                self._ready.set()
                await self._serve(session, requests)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Read once the transport has closed: a stdio program has been put away by then, so
            # how it ended is known (`mcp_stdio`).
            self._timed_out = deadline.expired()
            failure = self._start_failure(exc, timed_out=self._timed_out)
            self._error = failure.headline
            self._failure = failure
            self._sign_in_needed = _wants_sign_in(exc)
            if failure.pending:
                logger.info("MCP server '%s' is still starting: %s", self.name, self._error)
            else:
                logger.warning("MCP server '%s' connection failed: %s", self.name, self._error)
            if not self._ready.is_set() and not self._sign_in_needed:
                note_start(self.name, self._definition_seal(), failure=failure)
            self._ready.set()  # unblock waiters with the error recorded
        finally:
            # Last, with nothing awaited after it: a call queued while the connection was being
            # put away is answered here too, and none is queued once the task has ended.
            while not requests.empty():
                left = requests.get_nowait()
                self._settle(left, False, _not_sent_text(left.tool, self._ended_first()))

    def _start_failure(self, exc: BaseException, *, timed_out: bool) -> StartFailure:
        """Why this start failed, in `mcp_status`'s words. A stdio program that ended on its own
        exited; one still starting at the deadline, and left to finish, is still starting, as is a
        start that waited for an earlier one still finishing; one still running at the deadline
        did not answer; one that closed its output and had to be stopped closed the connection.
        Any other failure is the connection's own (:func:`_failure_text`), with the program's error
        output as its detail."""
        run = self._stdio
        leaf = _leaf(exc)
        if isinstance(leaf, CommandNotFound):
            return command_not_found(leaf.command)
        if run is not None:
            if run.waiting or run.left_to_finish:
                return still_starting(
                    self.name,
                    self._connect_timeout,
                    run.stderr,
                    allowance=mcp_stdio.FINISH_SECS,
                    earlier=run.waiting,
                )
            if run.exited and run.returncode is not None:
                return exited(self.name, run.returncode, run.stderr)
            if timed_out:
                return did_not_answer(self.name, self._connect_timeout, run.stderr)
            if _connection_closed(leaf):
                return closed(self.name, run.stderr)
            return StartFailure(_failure_text(exc, "", self.name), detail=run.stderr.strip())
        if timed_out:
            return did_not_answer(self.name, self._connect_timeout)
        return StartFailure(_failure_text(exc, str(self.spec.get("url") or ""), self.name))

    async def _open_transport(self, stack: Any):
        """Enter the right transport context for this server's spec: the one
        :func:`~personalclaw.mcp_discovery.mcp_transport` names."""
        from personalclaw.mcp_discovery import mcp_transport

        transport = mcp_transport(self.spec)
        if transport in ("http", "sse"):
            url = str(self.spec.get("url") or "")
            if not url:
                raise ValueError(f"an {transport} server needs a url")
            # How a remote server authenticates, sent on every request of the connection. The
            # registry's specs are resolved already (`_personalclaw_mcp_specs`), so these are the
            # values, read from the credential store when the spec was loaded. A server its owner
            # signed in to also gets the sign-in's bearer token, which the auth reads from the
            # store at each request and renews when it expires (`mcp_oauth`).
            from personalclaw.mcp_oauth import connection_auth

            headers = {str(k): str(v) for k, v in (self.spec.get("headers") or {}).items()}
            auth = connection_auth(self.name, self.spec)
            if transport == "http":
                from mcp.client.streamable_http import streamablehttp_client

                read, write, _ = await stack.enter_async_context(
                    streamablehttp_client(url, headers=headers, auth=auth)
                )
                return read, write
            from mcp.client.sse import sse_client

            read, write = await stack.enter_async_context(
                sse_client(url, headers=headers, auth=auth)
            )
            return read, write
        if transport != "stdio":
            raise ValueError(f"PersonalClaw cannot connect over the {transport!r} transport")

        # stdio: started by `mcp_stdio` — in the one environment a server is started in, through
        # the resource ceiling — which keeps how the program ended and what it wrote to its error
        # output, so a failed start can say why.
        from personalclaw.mcp_stdio import stdio_streams

        self._stdio = StdioRun()
        return await stack.enter_async_context(stdio_streams(self.name, self.spec, run=self._stdio))

    async def _refresh_tools(self, session: Any) -> None:
        result = await session.list_tools()
        tools: list[McpToolSpec] = []
        for t in getattr(result, "tools", None) or []:
            tools.append(
                McpToolSpec(
                    name=getattr(t, "name", ""),
                    description=getattr(t, "description", "") or "",
                    input_schema=getattr(t, "inputSchema", None)
                    or {"type": "object", "properties": {}},
                    annotations=_annotations_of(t),
                )
            )
        self._tools = tools

    async def _serve(self, session: Any, requests: asyncio.Queue[_Call]) -> None:
        """Send the queued calls one at a time, until the connection ends.

        A call whose caller already stopped waiting is never sent: it was told it was not. Any
        other is marked sent as it is handed to the session, before anything can reach the server,
        and from then on it is answered with what is known: the server's answer; that it may have
        gone through, when the connection ends with it out or what came back cannot be read as an
        answer; or that it was not sent, when the session's connection had already ended. A
        connection that ended is not served again, so its next call starts the server again
        (:meth:`ensure_started`)."""
        import anyio
        from mcp.shared.exceptions import McpError

        ended_out = "the connection ended before an answer came"
        while not self._closing:
            call = await requests.get()
            if call.answer.done():
                continue
            call.sent = True
            try:
                result = await session.call_tool(call.tool, call.arguments)
            except asyncio.CancelledError:
                # The connection is closing with the call out: shut down, or its transport failed.
                self._settle(
                    call, True, _may_have_gone_through_text(call.tool, self.name, ended_out)
                )
                raise
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                # The session refuses to send on a connection that has already ended.
                call.sent = False
                self._settle(call, False, _not_sent_text(call.tool, self._ended_first()))
                logger.warning(
                    "MCP server '%s' had closed its connection; its next call starts it again",
                    self.name,
                )
                return
            except McpError as exc:
                if _connection_closed(exc):
                    self._settle(
                        call, True, _may_have_gone_through_text(call.tool, self.name, ended_out)
                    )
                    logger.warning(
                        "MCP server '%s' closed its connection with '%s' out; its next call "
                        "starts it again",
                        self.name,
                        call.tool,
                    )
                    return
                # The server's own refusal of the call.
                self._settle(call, False, str(exc)[:500], answered=True)
            except Exception as exc:  # noqa: BLE001 - what came back is not an answer to read
                said = _failure_text(exc, self._remote_url(), self.name)
                self._settle(
                    call,
                    True,
                    _may_have_gone_through_text(
                        call.tool, self.name, f"could not read its answer: {said}"
                    ),
                )
            else:
                ok = not getattr(result, "isError", False)
                self._settle(call, ok, _coerce_output(result), answered=True)


def _connection_closed(exc: BaseException) -> bool:
    """Whether *exc* is the SDK session's "the other side closed the connection"."""
    from mcp.shared.exceptions import McpError
    from mcp.types import CONNECTION_CLOSED

    return isinstance(exc, McpError) and getattr(exc.error, "code", None) == CONNECTION_CLOSED


@dataclass(frozen=True)
class StartResult:
    """What one start of a server found (:func:`try_start`)."""

    tools: list[McpToolSpec]
    #: Why it did not connect, or ``""``: the start's failure headline (`mcp_status`).
    error: str
    #: The tail of a stdio program's error output when it failed.
    detail: str
    #: It refused the connection until its owner signs in.
    sign_in_needed: bool
    #: It did not answer within its deadline.
    timed_out: bool
    #: It is still starting: it was left to finish, or it waited for an earlier start that was
    #: (`mcp_stdio`), and it is looked at again once that start ends.
    starting: bool = False


async def try_start(name: str, spec: dict[str, Any], *, deadline: float, seal: str) -> StartResult:
    """Start server *name* once, as the probe does, and put it away again.

    The same start an agent's connection makes, recorded the same way (`mcp_discovery.note_start`)
    and said in the same words, given *deadline* to answer (the probe's own,
    ``dashboard.mcp_probe_timeout_secs``). *seal* is the definition the probe read the server as.
    """
    conn = McpServerConn(name, spec)
    conn._connect_timeout = float(deadline)
    conn._seal = seal
    try:
        tools = await conn.list_tools()
    finally:
        await conn.shutdown()
    failure = conn._failure
    return StartResult(
        tools=tools,
        error=conn.error,
        detail=failure.detail if failure is not None else "",
        sign_in_needed=conn.sign_in_needed,
        timed_out=conn._timed_out,
        starting=failure is not None and failure.pending,
    )


def _annotations_of(tool: Any) -> dict[str, Any]:
    """A listed tool's ``annotations`` as a plain dict (the SDK hands a model object), or ``{}``."""
    raw = getattr(tool, "annotations", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        try:
            out = dump(exclude_none=True)
        except Exception:  # noqa: BLE001 - an unreadable annotation declares nothing
            return {}
        return out if isinstance(out, dict) else {}
    return {}


def _leaf(exc: BaseException) -> BaseException:
    """The cause of a transport failure: the SDK raises one out of an anyio task group, so what
    arrives is an exception group whose first leaf is what went wrong."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _wants_sign_in(exc: BaseException) -> bool:
    """Whether a connection failed because the server wants its owner to sign in: its sign-in has
    ended (``mcp_oauth.SignInRequired``), or it answered 401 with a Bearer challenge."""
    from personalclaw.mcp_oauth import SignInRequired, bearer_challenge

    leaf = _leaf(exc)
    if isinstance(leaf, SignInRequired):
        return True
    response = getattr(leaf, "response", None)
    return getattr(response, "status_code", None) == 401 and bearer_challenge(response) is not None


def _lookup_failure(exc: BaseException) -> BaseException | None:
    """The failed name lookup under a connection error, or ``None``: the ``socket.gaierror`` that
    httpx and aiohttp raise their connect errors from, anywhere in its chain."""
    import socket

    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, socket.gaierror):
            return cur
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return None


def unresolved_host_text(host: str) -> str:
    """What a server whose host's name could not be looked up says, wherever it is said: the
    lookup is the cause, and nothing past it was tried."""
    return (
        f"PersonalClaw could not look up {host}, so it never reached the server. Check the URL, "
        f"and that this computer can reach the network {host} is on."
    )


def slow_lookup_text(host: str, waited: str) -> str:
    """What a server says whose host's name was still being looked up when the probe gave up."""
    return (
        f"Looking up {host} did not finish within {waited}, so PersonalClaw never reached the "
        f"server. Check the URL, and that this computer can reach the network {host} is on."
    )


def _failure_text(exc: BaseException, url: str = "", server: str = "") -> str:
    """One line saying why a connection failed — the Tools page shows it, and so does the log.

    The cause is the first leaf of the SDK's exception group (:func:`_leaf`), whose own text is
    only "unhandled errors in a TaskGroup (1 sub-exception)". An HTTP refusal reads as its status
    (``HTTP 401 Unauthorized``) rather than httpx's sentence, which spells out the URL, and a URL
    can carry a token — so any other message has the server's URL replaced by its masked form. A
    401 with a Bearer challenge is the server asking for a token: one naming its resource metadata
    asks its owner to sign in, and says so, and a bare one may want a sign-in or a token, which the
    probe finds out (`mcp_discovery._probe_remote`). A name that could not be looked up says that,
    not the resolver's own words.
    """
    exc = _leaf(exc)
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        if status == 401 and _wants_sign_in(exc):
            from personalclaw.mcp_oauth import (
                bearer_challenge,
                sign_in_needed_text,
                token_or_sign_in_text,
            )

            if (bearer_challenge(response) or {}).get("resource_metadata"):
                return sign_in_needed_text(server or "This server")
            return token_or_sign_in_text(server or "This server")
        return f"HTTP {status} {getattr(response, 'reason_phrase', '') or ''}".strip()
    host = urlsplit(url).hostname if url else None
    if host and _lookup_failure(exc) is not None:
        return unresolved_host_text(host)
    text = str(exc) or exc.__class__.__name__
    if url and url in text:
        from personalclaw.mcp_discovery import masked_url

        text = text.replace(url, masked_url(url))
    return text[:300]


def _coerce_output(result: Any) -> str:
    """Flatten an MCP ``CallToolResult`` into the text string the loop feeds back."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
            continue
        data = getattr(block, "data", None)
        if data is not None:
            parts.append(f"[{getattr(block, 'mimeType', 'binary')} data]")
    if parts:
        return "\n".join(parts)
    # Structured-only result (no content blocks) → serialize the model.
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        import json

        try:
            return json.dumps(dump(), default=str)
        except Exception:  # noqa: BLE001
            pass
    return str(result)


def _is_poolable(spec: dict[str, Any]) -> bool:
    """Whether a server is safe to SHARE across sessions.

    Safe-by-default means *not* shared: a server is pooled (one connection for
    all sessions) only when it explicitly declares ``poolable: true``. Stateful
    servers (a browser with a logged-in page, a shell with a cwd) must default to
    per-session isolation so one session's state can't leak into another's."""
    return bool(spec.get("poolable", False))


def _spec_hash(spec: dict[str, Any]) -> str:
    """A stable content hash of the connection-defining fields of a server spec, so two
    servers sharing a NAME but differing in command/args/env/url/transport/headers get DISTINCT
    pool entries instead of colliding on one connection (P23e) — and a remote server whose token
    was rotated behind an unchanged reference reconnects with the new one, as a stdio server's
    environment does. Uses sha256 over a sort-keyed JSON of only the fields that change what
    process/endpoint we talk to — NEVER Python ``hash()`` (its per-process salt would give a
    different key every run, breaking any cross-process/cross-surface sharing that keys off
    this).

    A sign-in counts by who issued it to which client for which resource
    (:func:`~personalclaw.mcp_oauth.sign_in_identity`): signing in opens a new connection, and a
    renewal, which replaces the tokens while the connection is open, does not."""
    import hashlib
    import json

    from personalclaw.mcp_discovery import mcp_transport
    from personalclaw.mcp_oauth import sign_in_identity

    material = {
        "command": spec.get("command", ""),
        "args": spec.get("args", []),
        "env": spec.get("env", {}),
        "url": spec.get("url", ""),
        "transport": mcp_transport(spec),
        "headers": spec.get("headers", {}),
        "signIn": sign_in_identity(spec),
    }
    blob = json.dumps(material, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# Registry key = (name, scope, spec_hash):
#   name       — the configured server name (listing / reconcile / removal key on k[0]).
#   scope      — "" for a poolable (shared) server, else the owning session_key (isolation
#                / eviction key on k[1]); unchanged semantics.
#   spec_hash  — content hash (P23e) so same-name/different-config servers don't collide.
_ConnKey = tuple[str, str, str]


def _conn_key(name: str, spec: dict[str, Any], session_key: str) -> _ConnKey:
    """Registry key for a (server, caller). Poolable → shared scope ``""``; otherwise
    scoped to the session. The content hash disambiguates same-name/different-spec."""
    scope = "" if _is_poolable(spec) else (session_key or "")
    return (name, scope, _spec_hash(spec))


class McpClientRegistry:
    """Process-wide registry of live MCP server connections (PClaw scope).

    Connections are keyed by ``(server_name, scope)``. Poolable servers share one
    connection (scope ``""``) across every session — the optimal, already-pooled
    path. Non-poolable (stateful) servers get one connection PER session so their
    state can't leak across sessions (safe-by-default isolation). Idle connections
    are reaped on a sweep so resident memory tracks *active* (server, session)
    pairs, not every session that ever touched a server."""

    def __init__(self) -> None:
        self._conns: dict[_ConnKey, McpServerConn] = {}
        self._specs: dict[str, dict[str, Any]] = {}
        self._sweeper: asyncio.Task | None = None
        # P23d observability counters (process-lifetime totals).
        self._stats = {"spawns": 0, "reaps": 0, "served": 0, "evicted": 0}

    def _canonical_key(self, name: str) -> _ConnKey | None:
        """The shared (scope ``""``) key for a configured server, or None if unknown.
        Computed via the current spec so it tracks a content-hash change on reconcile."""
        spec = self._specs.get(name)
        return _conn_key(name, spec, "") if spec is not None else None

    def items(self):
        """Canonical (scope ``""``) connection per configured server — the Tools
        page lists each server once. The tool *surface* is identical across scopes,
        so listing from the canonical connection is correct; per-session isolation
        only matters for stateful ``call_tool`` traffic."""
        out = []
        for name in self._specs:
            key = self._canonical_key(name)
            if key is not None and key in self._conns:
                out.append((name, self._conns[key]))
        return out

    def get(self, name: str, session_key: str = "") -> McpServerConn | None:
        """Resolve the connection a caller should use for ``name``.

        - Poolable server → the shared canonical connection (one for all sessions).
        - Stateful server + a session key → a per-session connection, created on
          demand, so that session's state can't leak into another's.
        - Stateful server + no session key → the canonical connection (listing /
          no session to isolate).

        Returns ``None`` for an unknown server."""
        spec = self._specs.get(name)
        if spec is None:
            # directly-registered / legacy: match the first key for this name.
            return next((c for k, c in self._conns.items() if k[0] == name), None)
        key = _conn_key(name, spec, session_key)
        conn = self._conns.get(key)
        if conn is None:
            conn = McpServerConn(name, spec, scope=key[1])
            self._conns[key] = conn
            self._stats["spawns"] += 1
        self._stats["served"] += 1
        return conn

    def load_from_specs(self, specs: dict[str, dict[str, Any]]) -> None:
        """Reconcile the registry to ``specs`` ({name: spec}). Each configured
        server gets its canonical (scope ``""``) connection eagerly (lazy-connected
        on first use); per-session connections for stateful servers are added on
        demand by :meth:`get`. Removed servers — AND servers whose spec content
        changed (new content hash) — have their stale connections dropped."""
        self._specs = {
            n: s for n, s in specs.items() if isinstance(s, dict) and not switched_off(s, n)
        }
        # The set of keys that SHOULD exist for the current specs (canonical scope).
        want_canonical = {self._canonical_key(n) for n in self._specs}
        for name, spec in self._specs.items():
            key = _conn_key(name, spec, "")
            if key not in self._conns:
                self._conns[key] = McpServerConn(name, spec, scope="")
                self._stats["spawns"] += 1
        # Drop connections whose server is gone OR whose spec content changed (a
        # canonical key no longer in want_canonical is a stale-hash orphan). Per-session
        # (scoped) conns of a still-configured server are left to evict_session/sweep.
        for key in list(self._conns):
            name, scope, _hash = key
            gone = name not in self._specs
            stale_hash = scope == "" and key not in want_canonical
            if gone or stale_hash:
                conn = self._conns.pop(key)
                asyncio.ensure_future(conn.shutdown())
        # A program left to finish starting for a server that is gone, or switched off, goes too.
        stop_finishing_soon(lambda name: name not in self._specs)

    def evict_session(self, session_key: str) -> None:
        """Shut down + drop all connections scoped to an ending session. Shared
        (poolable, scope ``""``) connections are untouched."""
        if not session_key:
            return
        for key in [k for k in self._conns if k[1] == session_key]:
            conn = self._conns.pop(key)
            self._stats["evicted"] += 1
            asyncio.ensure_future(conn.shutdown())

    def sweep_idle(self, ttl_secs: float = _IDLE_TTL_SECS) -> int:
        """Reap connections unused for longer than ``ttl_secs``. Returns the count
        reaped. A reaped server simply re-lazy-starts on its next use."""
        now = time.monotonic()
        reaped = 0
        for key in [k for k, c in self._conns.items() if now - c.last_used > ttl_secs]:
            conn = self._conns.pop(key)
            asyncio.ensure_future(conn.shutdown())
            reaped += 1
        self._stats["reaps"] += reaped
        if reaped:
            logger.debug("MCP idle sweep reaped %d connection(s)", reaped)
        return reaped

    def start_sweeper(self) -> None:
        """Start the periodic idle-eviction loop (idempotent)."""
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep_loop(), name="mcp-idle-sweeper")

    async def _sweep_loop(self) -> None:
        from personalclaw import shutdown_event

        while not shutdown_event.is_set():
            try:
                await asyncio.sleep(_SWEEP_INTERVAL_SECS)
                self.sweep_idle()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("MCP idle sweep failed", exc_info=True)

    def pool_stats(self) -> dict[str, Any]:
        """P23d observability: process-lifetime counters + a live snapshot of the pool.

        ``shared_conns`` counts poolable (scope ``""``) connections — the ones serving
        many callers from ONE process; ``session_conns`` counts per-session (isolated)
        ones. ``dedup_saved`` estimates connections avoided by pooling: every ``served``
        beyond the number of live connections is a call that reused an existing conn
        instead of spawning. Pure/read-only — safe to call from an API handler."""
        live = len(self._conns)
        shared = sum(1 for k in self._conns if k[1] == "")
        served = self._stats["served"]
        return {
            "live_connections": live,
            "shared_conns": shared,
            "session_conns": live - shared,
            "configured_servers": len(self._specs),
            "spawns": self._stats["spawns"],
            "reaps": self._stats["reaps"],
            "served": served,
            "evicted": self._stats["evicted"],
            # calls that reused a live conn rather than spawning (pooling payoff).
            "reused": max(0, served - self._stats["spawns"]),
        }

    async def shutdown_all(self) -> None:
        if self._sweeper and not self._sweeper.done():
            self._sweeper.cancel()
            self._sweeper = None
        await asyncio.gather(*(c.shutdown() for c in self._conns.values()), return_exceptions=True)
        self._conns.clear()
        await stop_finishing(lambda _name: True)

    def _close(
        self,
        match: Callable[[str], bool],
        *,
        timeout: float = _CLOSE_TIMEOUT_SECS,
        serving_only: bool = False,
    ) -> None:
        """Close every connection to the servers *match* names — shared and per-session.

        Callable from any thread. :meth:`load_from_specs` keeps a connection whose spec did not
        change, and an app updated in place keeps the same command and arguments, so the
        process its server spawned would go on answering with the code it started with. An
        app's unload closes its servers here; the next read starts them from the files on
        disk. Each connection is shut down on the loop its task runs on: awaited from any other
        thread, scheduled when called on that loop itself (which cannot block on it).

        With *serving_only* nothing about what the servers run changed (:func:`close_connections`):
        their specs are kept, and only the connections that listed their tools are closed. One
        whose start is still under way lists them as they are once it has started, and closing
        it could cut off an install part-way (`mcp_stdio`), so it is left to start.
        """
        if not serving_only:
            for name in [n for n in self._specs if match(n)]:
                del self._specs[name]
        for key in [k for k in self._conns if match(k[0])]:
            held = self._conns[key]
            # Serving: started, and its start has listed the tools (one that failed has ended).
            if serving_only and not (held.started and held._ready.is_set()):
                continue
            conn = self._conns.pop(key)
            task = conn._task
            if task is None or task.done():
                continue
            loop = task.get_loop()
            try:
                on_loop = asyncio.get_running_loop() is loop
            except RuntimeError:
                on_loop = False
            if on_loop:
                loop.create_task(conn.shutdown())
                continue
            try:
                asyncio.run_coroutine_threadsafe(conn.shutdown(), loop).result(timeout)
            except Exception:  # noqa: BLE001 — a server that will not close must not block the rest
                logger.warning("MCP server %r did not close cleanly", key[0], exc_info=True)


_registry: McpClientRegistry | None = None


def _personalclaw_mcp_specs() -> dict[str, dict[str, Any]]:
    """Load the PClaw-scope server specs from ``~/.personalclaw/mcp.json``, resolved.

    This is the single store the native client spawns from — the one the MCP
    Tools provider card writes and ``/api/mcp/apply`` imports into.

    The file holds each ``env``/``headers`` secret as a ``{{secret:…}}`` reference; the specs
    returned here carry the values, read from the credential store on every load. Resolving on
    load rather than inside the spawn keeps rotation working: the registry keys a connection by
    its spec's content hash, so a token changed behind an unchanged reference must change the
    spec it is compared by, or the live connection would keep the old one.

    A server whose spec names a credential its owner does not hold is left out — never spawned,
    and no value read for it. The refusal is logged and in the security log, and the server's
    probe (the Tools page) reports it as that server's error. So is one whose arguments or URL name
    a credential this machine's store does not have (a home restored here without its
    credentials): its probe asks for the value.

    So is a server the owner has not allowed as it is defined now (`mcp_grants`): it waits, and
    the Tools page says so with Allow. And a server named as PersonalClaw's own is never read
    from here: PersonalClaw defines that one itself (`agent._MANAGED_MCP_SERVERS`).
    """
    import json

    from personalclaw.config.loader import config_dir
    from personalclaw.config.secret_refs import (
        ForeignSecretReference,
        MissingSecretValue,
        resolve_mcp_spec,
    )

    # `config_dir()`, not `Path.home()`: this is the store the NATIVE agent loop spawns
    # from, so a `Path.home()` hardcode made a dev session with PERSONALCLAW_HOME set read
    # the operator's REAL servers and their credentials while the dashboard wrote the dev
    # home — and every dev-side edit looked like it did nothing.
    path = config_dir() / "mcp.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return {}
    from personalclaw import mcp_grants
    from personalclaw.mcp_discovery import _MANAGED_SERVER_NAMES, server_name_problem

    servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
    specs: dict[str, dict[str, Any]] = {}
    for name, spec in servers.items() if isinstance(servers, dict) else ():
        if not isinstance(spec, dict) or name in _MANAGED_SERVER_NAMES:
            continue
        problem = server_name_problem(str(name))
        if problem is not None:
            # Its tools would be named as another server's (the probe reports it as its error).
            logger.warning("MCP server %r not started: %s", name, problem)
            continue
        if not mcp_grants.allowed(mcp_grants.server_of(str(name), spec)):
            logger.info("MCP server %r not started: waiting for the owner's Allow", name)
            continue
        try:
            specs[name] = resolve_mcp_spec(name, spec)
        except (ForeignSecretReference, MissingSecretValue) as exc:
            logger.warning("MCP server %r not started: %s", name, exc)
    return specs


def close_connections(match: Callable[[str], bool]) -> None:
    """Close every connection to the servers *match* names that listed their tools, shared and
    per-session (:meth:`McpClientRegistry._close`): a connection lists a server's tools when it
    starts, so each one's next use starts the server again and lists them as they are then. A
    start still under way, or left to finish (`mcp_stdio`), is left to: it lists them as they are
    once it has started."""
    if _registry is not None:
        _registry._close(match, serving_only=True)  # noqa: SLF001 — the module's own registry


def close_servers(match: Callable[[str], bool]) -> None:
    """Close every live connection to the servers *match* names (see
    :meth:`McpClientRegistry._close`), and stop what was left to finish starting for them
    (`mcp_stdio`), which a probe leaves before any read built a connection."""
    stop_finishing_soon(match)
    if _registry is not None:
        _registry._close(match)  # noqa: SLF001 — the module's own registry


def get_mcp_client_registry() -> McpClientRegistry:
    """Return the process-wide registry, its servers re-read from ``mcp.json`` on every call."""
    global _registry
    if _registry is None:
        _registry = McpClientRegistry()
    _registry.load_from_specs(_personalclaw_mcp_specs())
    return _registry


def with_mcp_session_eviction(
    prior: "Callable[[str], Awaitable[object]] | None",
) -> "Callable[[str], Awaitable[None]]":
    """Wrap a session-expire callback so it also evicts that session's per-session
    MCP connections (stateful servers). Composed onto the existing expire chain so
    it runs ALONGSIDE consolidation + workflow cleanup, never instead of them.
    Best-effort: a failure here never blocks the rest of session teardown."""

    async def _expire(session_key: str) -> None:
        if prior is not None:
            try:
                await prior(session_key)
            except Exception:
                logger.warning(
                    "session-expire prior callback failed for %s", session_key, exc_info=True
                )
        try:
            if _registry is not None:
                _registry.evict_session(session_key)
        except Exception:
            logger.debug("MCP session eviction failed for %s", session_key, exc_info=True)

    return _expire
