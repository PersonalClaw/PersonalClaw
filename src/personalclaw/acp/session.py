"""AcpSession — one ACP session's turn loop over a FrameRouter queue.

The demux consumer side of concurrent ACP. Where today's ``AcpClient`` reads the
process's stdout INLINE during a turn (serializing all turns behind one lock),
an ``AcpSession`` owns a single ``sessionId`` and consumes ONLY that session's
queue — the :class:`FrameRouter` fans the shared stdout out to per-session queues,
so N sessions each await their own queue and run concurrently on one process.

This module is the session-scoped turn loop + its liveness/cancel logic,
pulled out of the monolithic client so it can be built and tested standalone against
a fake router queue — no real process, no stdout. The connection shell that owns the
process + router + spawns these sessions composes it next (step 2b). Gated by
``ACPDialect.supports_concurrent_sessions`` — the one-session ``AcpClient`` path
stays authoritative until a proven backend opts in.

Key invariant preserved from the inline loop: a session keeps a per-*session* turn
lock (one ``session/prompt`` in flight per session is still true — ACP answers one
prompt per session at a time), but there is NO process-wide lock, so co-tenant
sessions are never blocked by each other.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from personalclaw.acp import translate
from personalclaw.acp.translate import extract_text_chunk  # noqa: F401 — re-export
from personalclaw.acp.types import (
    CAP_COMMANDS,
    EVENT_AGENT_SWITCHED,
    EVENT_CARRIED_ON,
    EVENT_CLEAR_STATUS,
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_STEER,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    METHOD_AGENT_SWITCHED,
    METHOD_CLEAR_STATUS,
    METHOD_COMMANDS_EXECUTE,
    METHOD_COMPACTION_STATUS,
    METHOD_ELICITATION_CREATE,
    METHOD_METADATA,
    METHOD_PROMPT,
    METHOD_REQUEST_PERMISSION,
    METHOD_SESSION_UPDATE,
    OPTION_ALLOW_ONCE,
    STOP_REASON_CANCELLED,
    AcpEvent,
    AcpPromptStats,
    JsonRpcMessage,
)
from personalclaw.constants import JSONRPC_METHOD_NOT_FOUND
from personalclaw.security import redact_credentials
from personalclaw.textfmt import clip_words
from personalclaw.turn_streams import closing_stream

logger = logging.getLogger(__name__)

# ``extract_text_chunk`` lives in ``acp.translate`` (the single translation surface both
# the client and this session share); re-exported above so ``session.extract_text_chunk``
# stays a valid import for existing callers/tests. See translate.py for the full decoder set.


def classify_frame(msg: JsonRpcMessage, req_id: int) -> str:
    """Classify one inbound frame into a turn action — the single classifier for every
    turn loop (the N=1 AcpClient wrapper and the concurrent path both run their turns
    through AcpSession, so this is THE classifier; kept here, importing no client).
    Actions: complete | error | permission | elicitation | update | metadata | compaction |
    clear | agent_switched | request (any other request, which still needs its answer) | skip."""
    if msg.id == req_id and msg.method is None:
        return "error" if msg.error else "complete"
    if msg.method == METHOD_REQUEST_PERMISSION:
        return "permission"
    if msg.method == METHOD_ELICITATION_CREATE:
        return "elicitation"
    if msg.method == METHOD_SESSION_UPDATE:
        return "update"
    if msg.method == METHOD_METADATA:
        return "metadata"
    if msg.method == METHOD_COMPACTION_STATUS:
        return "compaction"
    if msg.method == METHOD_CLEAR_STATUS:
        return "clear"
    if msg.method == METHOD_AGENT_SWITCHED:
        return "agent_switched"
    if msg.id is not None and msg.method:
        return "request"
    return "skip"


# Turn tuning. A turn ends on the agent's answer to its prompt, its connection closing, its
# cancel, or its own deadline — never on silence: a model may think for minutes after its last
# step, and while the prompt is pending and the connection is alive the turn is not over.
_QUEUE_POLL = 1.0  # how long to await the queue before re-checking liveness/deadline
_DEFAULT_PROMPT_TIMEOUT = 7200.0  # 2 hours — allow very long tool execution (matches client)
# How long a cancelled turn waits for the agent's answer to ``session/cancel`` (the prompt's
# ``stopReason: cancelled``). A Stop escalates sooner, on its own budget
# (``agent.soft_stop_budget_secs``); this bounds a cancel nobody escalates, such as an abort.
_CANCEL_ACK_GRACE = 30.0

# Why a turn's drain ended without the agent's answer to the prompt (`_drain_turn`).
_ENDED_CLOSED = "closed"  # the connection closed under the turn
_ENDED_DIED = "died"  # the process exited
_ENDED_DEADLINE = "deadline"  # the prompt's own timeout
_ENDED_UNACKED = "unacked"  # cancelled, and the agent never answered the cancel

# Cap mid-turn steer deliveries per turn so a message flood cannot keep one turn alive
# forever. Same value and same reason as the native loop's ``_MAX_STEERS_PER_TURN``
# (agents/native/runtime.py); duplicated rather than imported because ``acp`` is a lower
# layer than ``agents`` and must not import upward. Test parity: the two are asserted
# equal in tests/test_mid_turn_steer.py, so a change to one is a red, not a drift.
_MAX_STEERS_PER_TURN = 4

# How many times one turn is carried on after a refusal ended it (`AcpSession._dispatch_frames`).
# Each carry-on follows a refusal, and a refusal nobody pressed (a policy's, an unattended run's)
# could otherwise meet an agent that asks for the same step again, forever.
_MAX_CARRY_ONS_PER_TURN = 4
# How much of a refused step's command the carry-on names it by.
_STEP_WORDS_LIMIT = 200


#: Yielded by :meth:`AcpSession._turn_events` (never past :meth:`AcpSession._dispatch_frames`)
#: when the agent declared its turn over without answering the prompt, so no answer is owed.
_RELEASED = AcpEvent(kind="released")
#: Yielded by :meth:`AcpSession._turn_events` (never past :meth:`AcpSession._dispatch_frames`)
#: when a refusal ended the agent's turn and the turn is to be carried on.
_CARRY_ON = AcpEvent(kind="carry_on")


def step_words(title: str, tool_input: str) -> str:
    """A refused step as the carry-on names it: its title and, when its input carries one, the
    command it would have run (a string, or an argument list), clipped and with credentials
    masked."""
    command = ""
    try:
        parsed = json.loads(tool_input) if tool_input else None
    except (TypeError, ValueError):
        parsed = None
    raw = parsed.get("command") if isinstance(parsed, dict) else None
    if isinstance(raw, list):
        raw = " ".join(str(part) for part in raw)
    if isinstance(raw, str):
        command = raw.strip()
    title = (title or "").strip() or "a step"
    words = f"{title} `{command}`" if command and command not in title else title
    return clip_words(redact_credentials(words)[0], _STEP_WORDS_LIMIT)


def carry_on_prompt(steps: list[str]) -> str:
    """What the agent is told when a refusal of *steps* ended its turn: that they did not run, and
    to carry on without them. Sent as the next prompt of the same session, so the agent goes on
    from where it stopped with everything it already has."""
    named = "; ".join(steps) or "the step you asked to run"
    was, it = ("were", "they") if len(steps) > 1 else ("was", "it")
    them = "them" if len(steps) > 1 else "it"
    return (
        f"PersonalClaw here, for the user: {named} {was} refused, so {it} did not run, and the "
        f"refusal ended your turn. Carry on without {them}: finish what the user asked with what "
        f"you already have, say what you could not do or check without {them}, and do not ask to "
        f"run {them} again."
    )


def read_when_settled(fut: "asyncio.Future[JsonRpcMessage]") -> None:
    """Read a request's reply future once it settles. A sender that stopped waiting for it (a
    turn that was cancelled, a configuration frame sent without awaiting its answer) leaves it
    pending, and the connection closing later fails it; read here, that failure is not reported
    as an exception nobody retrieved."""
    if not fut.cancelled():
        fut.exception()


class AcpSession:
    """One ACP session bound to a FrameRouter queue. Owns its turn lock + streaming.

    Construct with the ``sessionId``, the session's router queue, a ``send`` callable
    (writes a JSON-RPC request to the shared process stdin), a ``cancel_session``
    callable (issues ``session/cancel`` scoped to THIS sessionId only), and an
    ``is_process_alive`` predicate. The router + process are owned by the connection;
    the session only reads its queue and writes via the injected ``send``."""

    def __init__(
        self,
        session_id: str,
        queue: "asyncio.Queue[JsonRpcMessage]",
        *,
        send_request,  # async (method, params) -> (req_id, Future) : alloc id + expect + write
        send_response,  # async (req_id, result) -> None : reply to a server→client request
        cancel_session,  # async () -> None : session/cancel for THIS sid
        is_process_alive,  # () -> bool
        dialect=None,  # ACPDialect : permission-option parsing / approve outcome shape
        session_files_dir: "Path | None" = None,  # opt-in JSONL tool-result tailing
        describe_exit: "Callable[[], Awaitable[str]] | None" = None,  # how the process ended
        send_error=None,  # async (req_id, code, message) -> None : refuse a server→client request
    ) -> None:
        self.session_id = session_id
        self._queue = queue
        self._send_request = send_request
        self._send_response = send_response
        self._send_error = send_error
        self._cancel_session = cancel_session
        self._is_process_alive = is_process_alive
        self._describe_exit = describe_exit
        from personalclaw.acp.dialect import DefaultDialect

        self._dialect = dialect or DefaultDialect()
        self._turn_lock = asyncio.Lock()  # one prompt in flight PER SESSION (not process-wide)
        self._cancelled = False
        self._closed = False
        # Per-turn caches (owned here, threaded into the shared translate.* decoders)
        # + the cross-turn context-usage % the client also carries between turns.
        self._tool_call_inputs: dict[str, str] = {}
        # toolCallId -> the kind the `tool_call` frame declared, so the permission
        # frame that follows can name what it is gating even when the adapter omits
        # `kind` from its own payload. Same key, same lifetime as the inputs
        # cache above.
        self._tool_call_seen: dict[str, translate.SeenToolCall] = {}
        self._offered_options: dict[str, list[dict[str, str]]] = {}
        # This turn's permission requests still waiting for an answer (id as text → the id as
        # the agent sent it, which the answer must echo), the ones answered (a request is
        # answered once), and the option each refusal was answered with (`refusal_answer`).
        self._unanswered: dict[str, object] = {}
        self._answered: set[str] = set()
        # This turn's questions (``elicitation/create``) still waiting for an answer, by id as
        # text, and who answers them: the chat the turn runs for (``set_question_handler``).
        # With nobody armed, a question is answered ``cancel`` at once.
        self._unanswered_asks: dict[str, object] = {}
        self._question_handler: "Callable[[dict], Awaitable[dict]] | None" = None
        self._refusals: dict[str, dict[str, str]] = {}
        # This turn's asked-about steps, by request id, as (title, the carry-on's words for it —
        # `step_words`); the ones refused since the agent was last prompted; and how often the turn
        # was carried on.
        self._asked: dict[str, tuple[str, str]] = {}
        self._declined: list[tuple[str, str]] = []
        self._carry_ons = 0
        # Why the last drain ended without the agent's answer (one of the `_ENDED_*` values).
        self._drain_end: str = ""
        # A turn that ended before the agent answered its prompt leaves that answer owed: the
        # reply future, settled before the next turn (`settle_owed_answer`). And the cancels
        # sent for such a turn, held so they are not collected mid-send.
        self._owed_answer: "asyncio.Future[JsonRpcMessage] | None" = None
        self._owed_cancels: set[asyncio.Task] = set()
        self.last_prompt_stats = AcpPromptStats()
        # The stopReason the AGENT answered the last prompt with; "" when it never answered.
        self._last_stop_reason: str = ""
        self._turn_done: asyncio.Event = asyncio.Event()
        # Optional per-session JSONL tool-result tail (vendor opt-in; no-op when unset).
        self._session_files_dir = session_files_dir
        self._jsonl_pos: int = 0
        # ── mid-turn steering ──
        # ``_steer_pull`` is the drain SOURCE (the dispatcher's pull over the session's
        # buffer); None = no steering, which is the state for every dialect that does not
        # declare ``supports_mid_turn_prompt`` because :meth:`set_steer_source` refuses to
        # wire one. ``_steer_pending`` holds text that was pulled but NOT yet written to
        # the CLI, so a delivery that fails is retried at the next boundary and whatever
        # is left is readable at turn end (:meth:`undelivered_steers`) — a pulled steer
        # must never evaporate between the buffer and the wire.
        # ``_steer_inflight`` maps a written frame's request id to its text until the agent
        # answers, and ``_steer_rejected`` collects the ones it REFUSED. Measured live
        # against an authenticated kiro-cli: the write succeeds and the CLI answers
        # ``-32603 "Prompt already in progress"``, so a design that only tracked write
        # failures reported ``{"steered": true}`` for a steer the agent threw away. An
        # async refusal has to reach the same visible path as a failed write.
        self._steer_pull: "Callable[[], list[str]] | None" = None
        self._steer_pending: list[str] = []
        self._steer_inflight: dict[object, str] = {}
        self._steer_rejected: list[str] = []
        self._steers_delivered = 0

    def close(self) -> None:
        self._closed = True

    # ── the agent's questions to the user ──────────────────────────────────────────────
    def set_question_handler(self, handler: "Callable[[dict], Awaitable[dict]] | None") -> None:
        """Arm (or with ``None`` disarm) what answers the agent's ``elicitation/create``: it is
        handed the request's params and returns the response (``acp/elicitation.py``)."""
        self._question_handler = handler

    async def _answer_question(self, msg: JsonRpcMessage) -> None:
        """Answer one ``elicitation/create`` — through the armed handler, which waits on the
        user, or ``cancel`` when nobody can be asked. Never raises: an unanswered request is a
        turn that waits forever."""
        from personalclaw.acp.elicitation import CANCEL

        rid = str(msg.id)
        if rid in self._answered:
            return
        self._unanswered_asks[rid] = msg.id
        result: dict = dict(CANCEL)
        handler = self._question_handler
        if handler is not None and not self._cancelled:
            try:
                result = await handler(msg.params if isinstance(msg.params, dict) else {})
            except Exception:
                logger.warning(
                    "session %s: a question could not be asked", self.session_id, exc_info=True
                )
                result = dict(CANCEL)
        else:
            logger.info(
                "session %s: nobody can answer the agent's question here; cancelled",
                self.session_id,
            )
        await self._answer_ask(rid, result)

    async def _answer_ask(self, rid: str, result: dict) -> None:
        raw_id = self._unanswered_asks.pop(rid, None)
        if raw_id is None or not self._mark_answered(rid):
            return
        logger.info(
            "session %s: answered the agent's question %s (%s)",
            self.session_id,
            rid,
            result.get("action"),
        )
        try:
            await self._send_response(raw_id, result)
        except Exception:
            logger.debug(
                "session %s: answering question %s failed", self.session_id, rid, exc_info=True
            )

    async def _refuse_request(self, msg: JsonRpcMessage) -> None:
        """Answer a request this client does not serve with JSON-RPC's method-not-found, so the
        agent learns at once instead of waiting on an answer that never comes."""
        if self._send_error is None:
            logger.debug("session %s: no way to refuse %s", self.session_id, msg.method)
            return
        try:
            await self._send_error(msg.id, JSONRPC_METHOD_NOT_FOUND, f"{msg.method} is not served")
        except Exception:
            logger.debug(
                "session %s: refusing %s failed", self.session_id, msg.method, exc_info=True
            )

    # ── mid-turn steering ──────────────────────────────────────────────────────
    def steer_capable(self) -> bool:
        """Whether a mid-turn steer can reach the turn THIS session is running — the
        dialect's :attr:`~personalclaw.acp.dialect.ACPDialect.supports_mid_turn_prompt`.

        Read from the bound dialect rather than a dialect *id*, so every wrapper
        (:class:`AcpClient`, :class:`~personalclaw.llm.acp_session_provider.AcpSessionProvider`)
        answers from the one object that also builds the frame — a wrapper cannot report a
        capability the frame builder disagrees with."""
        return bool(self._dialect.supports_mid_turn_prompt)

    def set_steer_source(self, pull: "Callable[[], list[str]] | None") -> bool:
        """Arm (or with ``None`` disarm) the mid-turn steer drain. Returns whether a drain
        is now armed — the caller records THAT, never the declared capability.

        A dialect that does not declare mid-turn support is REFUSED here and the source is
        left unset, so the dispatcher learns ``False``, marks the session non-draining, and
        the message routes to the visible queue. This is the gate that keeps the invariant
        intact now that a drain path exists at all: nothing may buffer a steer against a
        turn that has no reader."""
        if pull is not None and not self.steer_capable():
            self._steer_pull = None
            return False
        self._steer_pull = pull
        return pull is not None

    def undelivered_steers(self) -> list[str]:
        """Steers this turn owes the user: never written (``_steer_pending``) plus written
        and REFUSED by the agent (``_steer_rejected``). Empty on the happy path.

        Read by the dispatcher at turn end so an undeliverable steer is surfaced instead of
        vanishing. A frame that is still awaiting its answer counts as delivered — we wrote
        it and the agent has not refused it — the same standard
        :meth:`AcpClient._watch_dialect_reply` applies to every other fire-and-forget
        session frame. Requeueing on silence instead would fabricate a duplicate of a steer
        that DID land."""
        return list(self._steer_pending) + list(self._steer_rejected)

    def _watch_steer_reply(self, rid: object, fut: "asyncio.Future") -> None:
        """Consume the interleaved prompt's own terminal response, and treat a refusal as a
        NON-delivery rather than a log line.

        A mid-turn ``session/prompt`` gets its own JSON-RPC id, so the router resolves a
        SECOND future that the running turn's drain never selects on. Left unread it is both
        a lost rejection and an "exception was never retrieved" warning.

        This is the path the live spike found: an authenticated kiro-cli accepts the WRITE
        and then answers ``-32603 "Prompt already in progress"``, so tracking write failures
        alone reported ``{"steered": true}`` for a steer the agent discarded. The refusal
        moves the text onto ``_steer_rejected``, which :meth:`undelivered_steers` reports and
        the dispatcher requeues — the same visible path a failed write takes."""

        def _on_done(f: "asyncio.Future") -> None:
            text = self._steer_inflight.pop(rid, "")
            # Cancelled / process gone before an answer: we cannot claim this landed, so it
            # stays owed. Visible-and-maybe-redundant beats silently-lost. Checked BEFORE
            # ``result()`` because ``CancelledError`` is a BaseException — an ``except
            # Exception`` here would let it escape the callback and skip this bookkeeping.
            if f.cancelled():
                if text:
                    self._steer_rejected.append(text)
                return
            try:
                resp = f.result()
            except Exception:
                if text:
                    self._steer_rejected.append(text)
                return
            err = getattr(resp, "error", None)
            if err:
                from personalclaw.acp.errors import rpc_error_words

                if text:
                    self._steer_rejected.append(text)
                logger.warning(
                    "session %s: agent REJECTED the mid-turn steer (rid=%s): %s — the steer "
                    "did NOT reach the running answer; it is owed back to the user",
                    self.session_id,
                    rid,
                    rpc_error_words(err),
                )

        fut.add_done_callback(_on_done)

    async def _deliver_steers_at_tool_boundary(self) -> list[str]:
        """Write every pending steer to the CLI as the dialect's mid-turn request.

        THE delivery path of a mid-turn steer. Called from :meth:`_dispatch_frames` at a
        tool boundary — the point mid-turn where the agent is between decisions, so an
        extra prompt can still change the answer being written rather than arriving after
        it. Returns the steers written, in order.

        Failure is retained, never swallowed: text stays on ``_steer_pending`` when the
        dialect builds no frame, when the cap is reached, or when the write raises, so the
        next boundary retries it and :meth:`undelivered_steers` still names it at turn end.
        A frame the agent WRITES but then refuses is caught asynchronously by
        :meth:`_watch_steer_reply`, which is why the text is parked on ``_steer_inflight``
        rather than simply dropped once the write returns.
        """
        if self._steer_pull is not None:
            try:
                pulled = self._steer_pull()
            except Exception:
                logger.debug("session %s: steer pull failed", self.session_id, exc_info=True)
                pulled = []
            self._steer_pending.extend(t for t in (pulled or []) if t and t.strip())
        delivered: list[str] = []
        while self._steer_pending and self._steers_delivered < _MAX_STEERS_PER_TURN:
            text = self._steer_pending[0]
            req = self._dialect.mid_turn_prompt_request(session_id=self.session_id, text=text)
            if req is None:
                logger.warning(
                    "session %s: dialect %s built no mid-turn frame — steer NOT delivered",
                    self.session_id,
                    getattr(self._dialect, "name", "?"),
                )
                break
            try:
                rid, fut = await self._send_request(req.method, req.params)
            except Exception:
                logger.warning(
                    "session %s: mid-turn steer write failed — steer NOT delivered",
                    self.session_id,
                    exc_info=True,
                )
                break
            self._steer_inflight[rid] = text  # owed until the agent answers or refuses
            self._watch_steer_reply(rid, fut)
            self._steer_pending.pop(0)
            self._steers_delivered += 1
            delivered.append(text)
            logger.info(
                "session %s: delivered a mid-turn steer via %s", self.session_id, req.method
            )
        return delivered

    async def cancel(self) -> None:
        """Cancel the in-flight turn for THIS session only (co-tenants keep streaming).

        Sends ``session/cancel`` and answers every permission request still waiting with the
        ``cancelled`` outcome, as the protocol asks of a client cancelling a turn. The turn
        itself ends when the agent answers the prompt (``stopReason: cancelled``), which
        :meth:`_drain_turn` waits for, so a cancel the agent acknowledges reads as one."""
        self._cancelled = True
        try:
            await self._cancel_session()
        except Exception:
            logger.debug(
                "session %s: cancel_session failed (non-fatal)", self.session_id, exc_info=True
            )
        for rid, raw_id in list(self._unanswered.items()):
            self._mark_answered(rid)
            try:
                await self._send_response(raw_id, self._dialect.reject_outcome(""))
            except Exception:
                logger.debug(
                    "session %s: answering request %s on cancel failed",
                    self.session_id,
                    rid,
                    exc_info=True,
                )
        from personalclaw.acp.elicitation import CANCEL

        for rid in list(self._unanswered_asks):
            await self._answer_ask(rid, dict(CANCEL))

    def _owe_answer(self, fut: "asyncio.Future[JsonRpcMessage]") -> None:
        """This turn ended while its prompt is still unanswered: her Stop went unanswered, its
        deadline passed, or whoever read it stopped reading. The agent can still answer, and what
        it streams on the way would be read by the next turn as that turn's own — a late answer
        spliced into another conversation turn. So the agent is told to stop (``session/cancel``,
        unless it already was) and the next turn first settles the answer
        (:meth:`settle_owed_answer`). Never sets ``_cancelled`` itself: how THIS turn ended is
        still being decided by the caller."""
        self._owed_answer = fut
        if self._closed or self._cancelled or not self._is_process_alive():
            return
        task = asyncio.ensure_future(self.cancel())
        self._owed_cancels.add(task)
        task.add_done_callback(self._owed_cancels.discard)

    async def settle_owed_answer(self, grace: float = _CANCEL_ACK_GRACE) -> bool:
        """Wait for the answer a turn that ended early still owes, and discard what came with it.

        Everything on this session's queue before that answer belongs to the turn it answers
        (stdout is in order, and no new prompt has been sent), so it is dropped, and a permission
        request among it is answered ``cancelled``. Returns ``False`` when the agent has still not
        answered after *grace* seconds: it is busy with a turn nobody is reading, and this session
        cannot take another until it is restarted."""
        owed = self._owed_answer
        if owed is None:
            return True
        if not owed.done():
            try:
                await asyncio.wait_for(asyncio.shield(owed), timeout=grace)
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning(
                    "session %s: the agent never answered the turn it was told to stop",
                    self.session_id,
                )
                return False
            except Exception:  # noqa: BLE001 - the connection failed under it: nothing is owed
                pass
        self._owed_answer = None
        dropped = 0
        while not self._queue.empty():
            msg = self._queue.get_nowait()
            if msg.method == "_router/closed":
                self._queue.put_nowait(msg)  # the connection is gone: the next turn must see it
                break
            dropped += 1
            if msg.method == METHOD_REQUEST_PERMISSION and msg.id is not None:
                try:
                    await self._send_response(msg.id, self._dialect.reject_outcome(""))
                except Exception:  # noqa: BLE001 - a dead agent asks nothing more
                    logger.debug("session %s: answering a stale request failed", self.session_id)
            elif msg.method == METHOD_ELICITATION_CREATE and msg.id is not None:
                from personalclaw.acp.elicitation import CANCEL

                try:
                    await self._send_response(msg.id, dict(CANCEL))
                except Exception:  # noqa: BLE001 - a dead agent asks nothing more
                    logger.debug("session %s: answering a stale question failed", self.session_id)
        if dropped:
            logger.info(
                "session %s: dropped %d frame(s) the agent sent for the turn before",
                self.session_id,
                dropped,
            )
        return True

    def _mark_answered(self, rid: str) -> bool:
        """Record that request *rid* has its answer. False when it already had one: a request
        is answered once, and a second answer to the same id is a protocol error."""
        if rid in self._answered:
            return False
        self._answered.add(rid)
        self._unanswered.pop(rid, None)
        return True

    async def approve_tool(self, request_id: str | int, option_id: str | None = None) -> None:
        """Approve a pending tool permission for THIS session. Resolves the option id
        from what the agent offered (agent-defined ids need not equal ``allow_once``);
        falls back to ``allow_once`` only when nothing was captured. A request the turn's
        cancel already answered is not approved after the fact."""
        rid = str(request_id)
        if not self._mark_answered(rid):
            logger.info("session %s: request %s was already answered", self.session_id, rid)
            return
        resolved = option_id
        if resolved is None:
            offered = self._offered_options.get(rid, [])
            resolved = self._dialect.select_allow_option_id(offered) or OPTION_ALLOW_ONCE
        self._offered_options.pop(rid, None)
        await self._send_response(request_id, self._dialect.approve_outcome(resolved))

    async def reject_tool(self, request_id: str | int) -> None:
        """Deny a pending tool permission for THIS session.

        Answers with the refusal the dialect picks from what the agent offered — the one that
        declines the call and lets the agent continue (:meth:`ACPDialect.select_reject_option_id`)
        — and remembers it for :meth:`refusal_answer`. READ the offered options before popping
        them: an order that discarded them first had nothing to resolve, and sent every denial
        as ``cancelled``. In a cancelled turn the answer IS ``cancelled``, which is what the
        protocol asks for there; a request already answered gets no second answer.

        Outside a cancelled turn the step is remembered as refused, so a turn the refusal ends is
        carried on without it (:meth:`_dispatch_frames`)."""
        rid = str(request_id)
        if not self._mark_answered(rid):
            logger.debug("session %s: request %s was already answered", self.session_id, rid)
            return
        offered = self._offered_options.pop(rid, [])
        resolved = "" if self._cancelled else self._dialect.select_reject_option_id(offered)
        if resolved:
            self._refusals[rid] = next(o for o in offered if o.get("id") == resolved)
        if not self._cancelled:
            self._declined.append(self._asked.get(rid) or ("", step_words("", "")))
        await self._send_response(request_id, self._dialect.reject_outcome(resolved))

    def refusal_answer(self, request_id: str | int) -> dict[str, str] | None:
        """The offered option a refusal of *request_id* was answered with (``{id, label,
        kind}``), or ``None`` when it was answered ``cancelled`` or not refused at all."""
        return self._refusals.get(str(request_id))

    def deny_outcome(self, request_id: str | int) -> str:
        """What a Deny of the pending *request_id* would do, before it is pressed: ``declines``
        the call and the agent goes on (the agent offered a refusal that lets it), ``carries_on``
        (every refusal it offered ends its turn, and the turn is then carried on without the
        call), or ``ends`` (it ends the turn, and this turn has been carried on as often as it
        may). ``""`` for a request this session is not waiting on."""
        offered = self._offered_options.get(str(request_id))
        if offered is None:
            return ""
        if not self._dialect.deny_ends_turn(offered):
            return "declines"
        return "carries_on" if self._carry_ons < _MAX_CARRY_ONS_PER_TURN else "ends"

    def _carries_on(self, stop_reason: str, method: str) -> bool:
        """Whether the agent's answer to its prompt (*stop_reason*) ends a turn that a refusal
        ended and that goes on: it answered ``cancelled`` though nobody stopped it, a refusal was
        sent since it was last prompted, and the turn has not been carried on as often as it may.
        Only a prompt's turn is carried on; a command's ends where it ends."""
        return (
            method == METHOD_PROMPT
            and stop_reason == STOP_REASON_CANCELLED
            and not self._cancelled
            and bool(self._declined)
            and self._carry_ons < _MAX_CARRY_ONS_PER_TURN
            and self._is_process_alive()
        )

    async def _drain_turn(
        self, req_id: int, response_future: "asyncio.Future[JsonRpcMessage]", timeout: float
    ) -> AsyncIterator[JsonRpcMessage]:
        """Yield this session's turn frames until the turn's terminal response lands, the
        connection closes, the process dies, or the turn's deadline passes.

        The FrameRouter demuxes stdout into TWO channels: the turn's own response
        (``id == req_id``, no method) resolves ``response_future`` (registered via
        ``router.expect``), while session-scoped NOTIFICATIONS (``session/update``
        chunks, ``session/request_permission``) land on ``self._queue``. So a turn is
        "drain the queue while awaiting the response future" — we select across both.
        Because stdout is in-order, every notification for this turn is enqueued BEFORE
        the response is routed; when the future resolves we flush the buffered queue
        frames first, then yield the terminal response last.

        A cancel does not end the drain: the agent answers ``session/cancel`` by answering
        the prompt (``stopReason: cancelled``), and that answer is how the turn is known to
        have stopped. It is awaited for :data:`_CANCEL_ACK_GRACE` at most.

        A drain that ends without the response sets :attr:`_drain_end` to why, which
        :meth:`_dispatch_frames` maps to the turn's ending. Either way the response future
        is read when it settles, so a connection closing under it later is never reported as
        an exception nobody retrieved."""
        deadline = time.monotonic() + timeout
        cancel_deadline: float | None = None
        self._drain_end = ""
        get_task: "asyncio.Task[JsonRpcMessage] | None" = None
        try:
            while not self._closed:
                now = time.monotonic()
                if self._cancelled and cancel_deadline is None:
                    cancel_deadline = now + _CANCEL_ACK_GRACE
                limit = deadline if cancel_deadline is None else min(deadline, cancel_deadline)
                if now >= limit:
                    unacked = cancel_deadline is not None and now >= cancel_deadline
                    self._drain_end = _ENDED_UNACKED if unacked else _ENDED_DEADLINE
                    return
                if get_task is None:
                    get_task = asyncio.ensure_future(self._queue.get())
                await asyncio.wait(
                    {get_task, response_future},
                    timeout=min(limit - now, _QUEUE_POLL),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # 1. Yield a ready NOTIFICATION first — preserves ordering (updates
                #    before the terminal) even when both complete the same tick.
                if get_task.done():
                    msg = get_task.result()
                    get_task = None
                    if msg.method == "_router/closed":  # connection closed — poison frame
                        # A backend that sends its terminal `result` and immediately closes
                        # stdout (EOF) is a NORMAL end-of-turn, not a mid-turn death: the
                        # response future is already resolved. Yield that terminal frame so
                        # the turn completes on `result` rather than being lost to the close.
                        self._drain_end = _ENDED_CLOSED
                        if response_future.done():
                            try:
                                yield response_future.result()
                            except Exception:
                                logger.warning(
                                    "session %s: turn ended on connection error",
                                    self.session_id,
                                    exc_info=True,
                                )
                        else:
                            logger.warning(
                                "session %s: connection closed mid-turn", self.session_id
                            )
                        return
                    yield msg
                    continue
                # 2. Terminal response landed (and no notification is ready): flush any
                #    buffered notifications, then yield the response as the final frame.
                if response_future.done():
                    get_task.cancel()  # safe: not done (checked above) → hasn't dequeued
                    get_task = None
                    while not self._queue.empty():
                        buffered = self._queue.get_nowait()
                        if buffered.method == "_router/closed":
                            self._drain_end = _ENDED_CLOSED
                            return
                        yield buffered
                    try:
                        yield response_future.result()
                    except Exception:
                        self._drain_end = _ENDED_CLOSED
                        logger.warning(
                            "session %s: turn ended on connection error",
                            self.session_id,
                            exc_info=True,
                        )
                    return
                # 3. Idle tick — no frame, no response. A quiet agent is still working; only
                #    its process dying ends the turn here.
                if not self._is_process_alive():
                    logger.warning("session %s: process died mid-turn", self.session_id)
                    self._drain_end = _ENDED_DIED
                    return
            self._drain_end = _ENDED_CLOSED  # the session was closed under the turn
        finally:
            if get_task is not None and not get_task.done():
                get_task.cancel()
            response_future.add_done_callback(read_when_settled)

    # ── turn API (the surface acp_agent drives, mirrors AcpClient) ──────────────

    def stream_events(
        self, message: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        """Send a prompt on THIS session and yield AcpEvents: one turn (:meth:`_turn`)."""
        prompt = translate.encode_prompt_content(message)
        return self._turn(METHOD_PROMPT, {"sessionId": self.session_id, "prompt": prompt}, timeout)

    def stream_command(
        self, command: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        """Execute a slash command on THIS session and yield streaming AcpEvents
        (``commands/execute`` — output arrives in the terminal result, not chunks): one turn
        (:meth:`_turn`).

        This is the raw wire write; the capability gate lives one layer up, at the two
        provider seams that own the ``agentCapabilities`` snapshot (``AcpClient`` and
        ``AcpSessionProvider``). Callers reaching a session directly are asking for the
        frame they asked for."""
        name, args = _parse_slash_command(command)
        return self._turn(
            METHOD_COMMANDS_EXECUTE,
            {"sessionId": self.session_id, "command": {"command": name, "args": args}},
            timeout,
            extract_agent_from_result=True,
        )

    async def _turn(
        self,
        method: str,
        params: dict,
        timeout: float,
        *,
        extract_agent_from_result: bool = False,
    ) -> AsyncIterator[AcpEvent]:
        """One turn on THIS session: the request *method* with *params* sent, and its frames
        yielded as AcpEvents. The turn holds the session's turn lock, one request in flight per
        session (never process-wide, so co-tenant sessions stream concurrently).

        The lock is given back where the turn ends: before its terminal ``EVENT_COMPLETE`` is
        handed on, or as the error that ends it is raised, or as the reader closes the stream
        part way. Never later: a reader that stops at the terminal event and keeps the stream
        would otherwise hold the session until the interpreter collected the stream, and the
        session's next prompt would wait that long, unsent."""
        from personalclaw.acp.errors import AcpProcessDied

        await self._turn_lock.acquire()
        held = True
        try:
            if not await self.settle_owed_answer():
                raise AcpProcessDied("the agent is still busy with a turn it was told to stop")
            self._cancelled = False
            self._turn_done.clear()
            req_id, fut = await self._send_request(method, params)
            frames = self._dispatch_frames(
                req_id,
                fut,
                timeout,
                extract_agent_from_result=extract_agent_from_result,
                method=method,
            )
            async with closing_stream(frames) as events:
                async for event in events:
                    if event.kind == EVENT_COMPLETE and held:
                        held = False
                        self._turn_lock.release()
                    yield event
        finally:
            if held:
                self._turn_lock.release()

    async def _dispatch_frames(
        self,
        req_id: int,
        response_future: "asyncio.Future[JsonRpcMessage]",
        timeout: float,
        *,
        extract_agent_from_result: bool = False,
        method: str = "",
    ) -> AsyncIterator[AcpEvent]:
        """Turn ladder: classify each drained frame and translate it into AcpEvents via
        the shared ``translate.*`` decoders. This is THE turn loop — the N=1 AcpClient
        wrapper delegates here too. One synthetic EVENT_COMPLETE path (the tool-interrupted
        marker) and cross-turn ``context_pct`` carry — over the demuxed session
        queue, with NO process-wide lock and no JSONL/SEL/telemetry side-channels (the
        concurrent-capable backend streams tool results via protocol ``tool_call_update``
        frames, already handled by ``translate.extract_tool_update_events``).

        A turn a refusal ended is carried on (:meth:`_carries_on`): the agent is prompted again
        on this session to go on without the refused steps (:func:`carry_on_prompt`), an
        ``EVENT_CARRIED_ON`` naming them is yielded, and the turn goes on, under the same deadline
        and stats, until the agent answers that prompt. A refusal is what a Deny sends, and an
        agent can offer only a refusal that ends its turn (an escalation request, a file
        change); left there, her Deny would end the work she asked for instead of one call."""
        from personalclaw.acp.errors import AcpProcessDied, AcpTimeoutError

        prev_pct = self.last_prompt_stats.context_pct
        self.last_prompt_stats = AcpPromptStats(context_pct=prev_pct)
        self._tool_call_inputs.clear()
        self._tool_call_seen.clear()
        self._offered_options.clear()
        self._unanswered.clear()
        self._answered.clear()
        self._unanswered_asks.clear()
        self._refusals.clear()
        self._asked.clear()
        self._declined.clear()
        self._carry_ons = 0
        # Per-turn steer state. Clearing ``_steer_pending`` at the START is deliberate: a
        # steer that could not be delivered belongs to the turn it was aimed at, and letting
        # it survive into the next one would leak it across turns. The dispatcher
        # reads ``undelivered_steers()`` at the end of the SAME turn.
        self._steers_delivered = 0
        self._steer_pending.clear()
        self._steer_inflight.clear()
        self._steer_rejected.clear()
        got_complete = False
        # The agent declared the turn over without answering its prompt (the interrupted marker).
        released = False
        deadline = time.monotonic() + timeout
        while True:
            carry_on = False
            turn = self._turn_events(
                req_id,
                response_future,
                max(deadline - time.monotonic(), 0.0),
                extract_agent_from_result,
                method,
            )
            try:
                async with closing_stream(turn) as events:
                    async for event in events:
                        if event is _RELEASED:
                            released = True
                            continue
                        if event is _CARRY_ON:
                            carry_on = True
                            continue
                        got_complete = got_complete or event.kind == EVENT_COMPLETE
                        yield event
            finally:
                if not response_future.done() and not released:
                    self._owe_answer(response_future)
            if not carry_on:
                break
            # A refusal ended the agent's turn: it answered its prompt `cancelled` with nobody
            # stopping it. A Deny means "go on without this call", so the same session is asked
            # to carry on without the refused steps, and what it does next is this turn's.
            declined, self._declined = self._declined, []
            self._carry_ons += 1
            logger.info(
                "session %s: a refusal ended the agent's turn — carrying it on (%d of %d)",
                self.session_id,
                self._carry_ons,
                _MAX_CARRY_ONS_PER_TURN,
            )
            # Plain text, never `encode_prompt_content`: that reads any image a path in the
            # message names, and a refused command can name one.
            prompt = carry_on_prompt([words for _title, words in declined])
            req_id, response_future = await self._send_request(
                METHOD_PROMPT,
                {"sessionId": self.session_id, "prompt": [{"type": "text", "text": prompt}]},
            )
            titles = dict.fromkeys(title.strip() for title, _words in declined if title.strip())
            yield AcpEvent(kind=EVENT_CARRIED_ON, title=", ".join(titles))
        if got_complete:
            return
        # Drain ended without the agent's answer to the prompt. How the turn ended, by why:
        #
        #   why the drain ended                    the turn
        #   ─────────────────────────────────────  ────────────────────────────────────────────
        #   any, after a cancel                    stopped: EVENT_COMPLETE(cancelled)
        #   the connection closed / process died   AcpProcessDied — not a timeout, and not an
        #                                          answer, whatever text had streamed
        #   the prompt's own deadline              AcpTimeoutError — the only timeout
        #
        # Silence is in no row: while the prompt is pending and the connection is alive, the
        # agent is still working. `_last_stop_reason` stays "" in every row: it is what the AGENT
        # answered, and here it answered nothing — which is how a cancel that went unacknowledged
        # reads as one to `wait_turn_done`, and is escalated by the caller that asked for it. An
        # answer still owed is settled before the session takes another turn (`_owe_answer`).
        self._last_stop_reason = ""
        self._turn_done.set()
        if self._cancelled:
            logger.info(
                "session %s: stopped turn ended without the agent's answer (%s)",
                self.session_id,
                self._drain_end or "no answer",
            )
            yield AcpEvent(kind=EVENT_COMPLETE, stop_reason=STOP_REASON_CANCELLED)
            return
        if self._drain_end in (_ENDED_CLOSED, _ENDED_DIED):
            ended = await self._describe_exit() if self._describe_exit is not None else ""
            raise AcpProcessDied(
                "the agent's process ended before it finished the turn"
                + (f": {ended}" if ended else "")
            )
        logger.warning(
            "session %s: the agent did not answer within the turn's %.0fs deadline",
            self.session_id,
            timeout,
        )
        raise AcpTimeoutError()

    async def _turn_events(
        self,
        req_id: int,
        response_future: "asyncio.Future[JsonRpcMessage]",
        timeout: float,
        extract_agent_from_result: bool,
        method: str,
    ) -> AsyncIterator[AcpEvent]:
        """The turn's frames as events, until its terminal frame (:meth:`_dispatch_frames`)."""
        from personalclaw.acp.errors import AcpMethodNotFound, AcpRequestError

        saw_agent_switch = False
        frames = self._drain_turn(req_id, response_future, timeout)
        async with closing_stream(frames) as drained:
            async for msg in drained:
                action = classify_frame(msg, req_id)
                self.last_prompt_stats.event_count += 1

                if action == "complete":
                    result = msg.result or {}
                    reason = result.get("stopReason", "") or "" if isinstance(result, dict) else ""
                    if extract_agent_from_result and isinstance(result, dict):
                        text = translate.format_command_result(result)
                        if text:
                            yield AcpEvent(kind=EVENT_TEXT_CHUNK, text=text)
                        if not saw_agent_switch:
                            data = result.get("data", {})
                            agent_info = data.get("agent") if isinstance(data, dict) else None
                            name = (
                                agent_info.get("name", "") if isinstance(agent_info, dict) else ""
                            )
                            if name:
                                yield AcpEvent(kind=EVENT_AGENT_SWITCHED, text=name)
                    for tr in self._read_new_tool_results():  # flush remaining JSONL results
                        yield tr
                    if self._carries_on(reason, method):
                        yield _CARRY_ON
                        return
                    self._last_stop_reason = reason
                    self._turn_done.set()
                    yield AcpEvent(kind=EVENT_COMPLETE, stop_reason=reason)
                    return
                if action == "error":
                    if self._cancelled:
                        # The agent answered the cancel with an error rather than with
                        # `stopReason: cancelled`. It answered, and the turn is stopped either way.
                        self._last_stop_reason = STOP_REASON_CANCELLED
                        self._turn_done.set()
                        yield AcpEvent(kind=EVENT_COMPLETE, stop_reason=STOP_REASON_CANCELLED)
                        return
                    _err = msg.error if isinstance(msg.error, dict) else {}
                    if _err.get("code") == JSONRPC_METHOD_NOT_FOUND:
                        # The ONE error that means "this agent cannot do that at all", so the
                        # only one a caller may answer by substituting another path.
                        # Typed here rather than string-matched upstairs so the substitution
                        # can never widen to errors that mean a real attempt failed.
                        raise AcpMethodNotFound(method, msg.error)
                    raise AcpRequestError(method or METHOD_PROMPT, msg.error)
                if action == "permission":
                    permission = translate.build_permission_event(
                        msg,
                        self._dialect,
                        self._tool_call_inputs,
                        self._tool_call_seen,
                        self._offered_options,
                    )
                    rid = str(permission.request_id)
                    if permission.request_id != "" and rid not in self._answered:
                        self._unanswered[rid] = permission.request_id
                        self._asked[rid] = (
                            permission.title,
                            step_words(permission.title, permission.tool_input),
                        )
                    if self._cancelled:
                        # Asked after the turn was stopped: nobody is asked any more, and the
                        # protocol's answer for a cancelled turn's request is `cancelled`.
                        if self._mark_answered(rid):
                            await self._send_response(
                                permission.request_id, self._dialect.reject_outcome("")
                            )
                        continue
                    yield permission
                elif action == "elicitation":
                    await self._answer_question(msg)
                elif action == "request":
                    await self._refuse_request(msg)
                elif action == "update":
                    chunk, is_thinking = translate.extract_text_chunk(msg)
                    if chunk:
                        for tr in self._read_new_tool_results():  # results before this text
                            yield tr
                        kind = EVENT_THINKING_CHUNK if is_thinking else EVENT_TEXT_CHUNK
                        if not is_thinking:
                            self.last_prompt_stats.text_chunks += 1
                        yield AcpEvent(kind=kind, text=chunk)
                        if not is_thinking and translate.is_tool_interrupted_marker(chunk):
                            # The backend security filter cancelled the turn's tools and will
                            # never send `result` — synthesize a complete so the caller exits. The
                            # agent said the turn is over, so no answer is owed for it.
                            yield _RELEASED
                            for tr in self._read_new_tool_results():
                                yield tr
                            self._turn_done.set()
                            yield AcpEvent(kind=EVENT_COMPLETE)
                            return
                    tool_event = translate.extract_tool_event(
                        msg,
                        self._tool_call_inputs,
                        self._tool_call_seen,
                        self.last_prompt_stats.tool_calls,
                    )
                    if tool_event:
                        for tr in self._read_new_tool_results():  # prior tool's results first
                            yield tr
                        yield tool_event
                    upd_events = translate.extract_tool_update_events(
                        msg, self._tool_call_inputs, self._tool_call_seen
                    )
                    for upd_event in upd_events:
                        yield upd_event
                    if tool_event or upd_events:
                        # ── THE ACP TOOL BOUNDARY ──
                        # A tool frame is the one point mid-turn where this host knows the agent
                        # is between decisions, so a steer written now can still change the
                        # answer being written. Everything else in this loop is text already
                        # committed to the transcript. No-op unless a drain source is armed,
                        # which only a dialect declaring `supports_mid_turn_prompt` can do. Each
                        # steer written is said, for the chat to write it where the turn took it.
                        for steer in await self._deliver_steers_at_tool_boundary():
                            yield AcpEvent(kind=EVENT_STEER, text=steer)
                elif action == "metadata":
                    pct = translate.extract_context_pct(msg)
                    if pct is not None:
                        self.last_prompt_stats.context_pct = pct
                elif action == "compaction":
                    params = msg.params or {}
                    status = params.get("status", {})
                    status_type = (
                        status.get("type", "") if isinstance(status, dict) else str(status)
                    )
                    yield AcpEvent(
                        kind=EVENT_COMPACTION_STATUS,
                        text=status_type,
                        title=params.get("summary", ""),
                    )
                elif action == "clear":
                    yield AcpEvent(kind=EVENT_CLEAR_STATUS)
                elif action == "agent_switched":
                    saw_agent_switch = True
                    params = msg.params or {}
                    yield AcpEvent(kind=EVENT_AGENT_SWITCHED, text=params.get("agentName", ""))

    async def wait_turn_done(self, timeout: float) -> str:
        """Block until the current turn completes; return its stop reason."""
        await asyncio.wait_for(self._turn_done.wait(), timeout=timeout)
        return self._last_stop_reason

    def has_active_turn(self) -> bool:
        return self._turn_lock.locked() and not self._turn_done.is_set()

    def context_usage_pct(self) -> float | None:
        """Last reported context usage, or ``None`` when the adapter reported none."""
        return self.last_prompt_stats.context_pct

    def _read_new_tool_results(self) -> list[AcpEvent]:
        """Tail this session's JSONL tool-result file (opt-in via ``session_files_dir``;
        no-op otherwise). Delegates to the shared ``translate.read_new_tool_results``,
        advancing the read position."""
        if self._session_files_dir is None:
            return []
        jsonl_path = self._session_files_dir / f"{self.session_id}.jsonl"
        results, self._jsonl_pos = translate.read_new_tool_results(jsonl_path, self._jsonl_pos)
        return results


def _parse_slash_command(command: str) -> tuple[str, dict]:
    """Parse ``/foo bar baz`` into a TuiCommand ``(name, args)`` pair."""
    parts = command.strip().split(None, 1)
    name = parts[0].lstrip("/") if parts else command.lstrip("/")
    value = parts[1] if len(parts) > 1 else None
    return name, ({"value": value} if value else {})


class AcpConnection:
    """One ACP backend process, shared by N concurrent :class:`AcpSession`s.

    Owns the process handle + the single :class:`FrameRouter` over its stdout + the
    ``initialize`` handshake + a monotonic request-id counter. ``new_session()`` issues
    ``session/new`` on the SAME process, registers the returned ``sessionId`` with the
    router, and returns an :class:`AcpSession` bound to that session's queue. Multiple
    calls → multiple concurrent sessions on one process — what the single reader buys, gated by the
    backend dialect's ``supports_concurrent_sessions`` (the caller checks it before
    opening more than one).

    The process is either spawned via :meth:`spawn` (the live path — reuses the shared
    :class:`~personalclaw.acp.transport.AcpProcess`, the SAME machinery the one-session
    client uses, no duplicate spawn/kill) or injected as a raw ``proc`` for unit tests.
    ``request(method, params)`` writes a JSON-RPC request and awaits its response via the
    router's pending-future mechanism (id-correlated)."""

    def __init__(self, proc, router, *, dialect=None, transport=None, session_meta=None) -> None:
        # Either a shared AcpProcess transport (live path) OR a raw asyncio subprocess
        # (unit tests inject a fake with .stdin/.stdout/.returncode). The transport is
        # preferred; the raw proc is a test-compat shim writing straight to stdin.
        self._transport = transport
        self._proc = proc  # None on the transport path
        self._router = router  # a started FrameRouter over the line source
        self._dialect = dialect
        # The ``_meta`` the entry's app declared for its CLI's sessions
        # (``register_acp_cli_entry(session_meta=...)``). Added here, where every
        # ``session/new`` and ``session/load`` on this process is written, so no caller can
        # open a session without it.
        self._session_meta: dict = dict(session_meta or {})
        self._next_id = 0
        self._sessions: dict[str, AcpSession] = {}
        self._agent_capabilities: dict = {}
        # Raw ``session/new`` response (modes / models / configOptions) from the most
        # recent new-session — the discovery snapshot the client reads off this
        # connection, so a runtime's Test lists its agents without a second spawn.
        self._last_session_new_snapshot: dict = {}

    @classmethod
    async def spawn(
        cls,
        *,
        command: list[str],
        work_dir,
        dialect=None,
        sandbox_mode: str = "auto",
        extra_env: dict | None = None,
        session_key: str | None = None,
        channel_id: str | None = None,
        session_meta: dict | None = None,
    ) -> "AcpConnection":
        """Spawn a backend process (shared AcpProcess transport) + start a FrameRouter
        over its stdout, and return a live AcpConnection ready for ``initialize`` +
        ``new_session``. This is the concurrent path's entry point."""
        from personalclaw.acp.reader import FrameRouter
        from personalclaw.acp.transport import AcpProcess

        transport = AcpProcess(
            command=command,
            work_dir=work_dir,
            sandbox_mode=sandbox_mode,
            extra_env=extra_env,
            session_key=session_key,
            channel_id=channel_id,
        )
        await transport.spawn()
        router = FrameRouter(transport.readline)
        router.start()
        return cls(None, router, dialect=dialect, transport=transport, session_meta=session_meta)

    def _req_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def is_process_alive(self) -> bool:
        if self._transport is not None:
            return self._transport.is_alive()
        return self._proc is not None and self._proc.returncode is None

    async def _write(self, obj: dict) -> None:
        data = __import__("json").dumps(obj) + "\n"
        if self._transport is not None:
            await self._transport.write(data)
            return
        self._proc.stdin.write(data.encode())
        await self._proc.stdin.drain()

    async def send_request(self, method: str, params: dict):
        """Write a JSON-RPC request and return ``(req_id, future)`` WITHOUT awaiting —
        the caller (a session turn loop) selects on the future alongside its queue.
        Registers the pending future BEFORE writing (avoids a fast-reply race)."""
        rid = self._req_id()
        fut = self._router.expect(rid)
        await self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return rid, fut

    async def send_response(self, req_id, result: dict) -> None:
        """Reply to a server→client request (e.g. a permission prompt) by id."""
        await self._write({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def send_error(self, req_id, code: int, message: str) -> None:
        """Refuse a server→client request by id, with a JSON-RPC error."""
        await self._write(
            {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
        )

    async def request(self, method: str, params: dict, *, timeout: float = 60.0):
        """Write a JSON-RPC request and await its id-correlated response via the router.

        An error answer raises, in the agent's own words (:class:`AcpRequestError`: its
        message and data) — or as :class:`AcpMethodNotFound`, the one error a caller may answer
        by taking another path. A caller that read only ``result`` reported a refused
        ``session/new`` as "returned no sessionId" and dropped why."""
        from personalclaw.acp.errors import AcpMethodNotFound, AcpRequestError

        rid, fut = await self.send_request(method, params)
        resp = await asyncio.wait_for(fut, timeout=timeout)
        if resp.error:
            code = resp.error.get("code") if isinstance(resp.error, dict) else None
            if code == JSONRPC_METHOD_NOT_FOUND:
                raise AcpMethodNotFound(method, resp.error)
            raise AcpRequestError(method, resp.error)
        return resp

    async def describe_exit(self, grace: float = 1.0) -> str:
        """How the agent's process ended — its exit code and its last stderr lines, masked —
        for an error about it, giving a process whose output just closed *grace* seconds to
        finish exiting; "" while it runs or when nothing is known."""
        from personalclaw.acp.errors import exit_words

        transport = self._transport
        wait_exit = getattr(transport, "wait_exit", None)
        code = await wait_exit(grace) if wait_exit is not None else None
        if not isinstance(code, int):
            return ""
        return exit_words(transport.program_name, code, transport.stderr_tail())

    async def initialize(self, params: dict, *, timeout: float = 240.0) -> dict:
        """Do the one-per-process ``initialize`` handshake; capture agentCapabilities."""
        resp = await self.request("initialize", params, timeout=timeout)
        self._agent_capabilities = (
            (resp.result or {}).get("agentCapabilities") or {} if resp.result else {}
        )
        return self._agent_capabilities

    def _with_session_meta(self, params: dict) -> dict:
        """*params* with the declared ``_meta`` added beside any the caller set (a resume's
        session-file hint); the caller's own key wins a clash, so core's hint is never lost."""
        if not self._session_meta:
            return params
        return {**params, "_meta": {**self._session_meta, **(params.get("_meta") or {})}}

    def _bind_session(self, sid: str, session_files_dir=None) -> AcpSession:
        """Register *sid* with the router and construct an AcpSession bound to its queue,
        with the send/response/cancel closures scoped to this connection + sid. Shared by
        :meth:`new_session` and :meth:`load_session`."""
        queue = self._router.register_session(sid)

        async def _send_request(method, req_params):
            return await self.send_request(method, req_params)

        async def _send_response(req_id, result):
            await self.send_response(req_id, result)

        async def _send_error(req_id, code, message):
            await self.send_error(req_id, code, message)

        async def _cancel():
            await self._write(
                {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": sid}}
            )

        sess = AcpSession(
            sid,
            queue,
            send_request=_send_request,
            send_response=_send_response,
            cancel_session=_cancel,
            is_process_alive=self.is_process_alive,
            dialect=self._dialect,
            session_files_dir=session_files_dir,
            describe_exit=self.describe_exit,
            send_error=_send_error,
        )
        self._sessions[sid] = sess
        return sess

    async def new_session(
        self, params: dict, *, timeout: float = 60.0, session_files_dir=None
    ) -> AcpSession:
        """Issue ``session/new`` on this process, register the sessionId with the router,
        return an AcpSession bound to its queue. Multiple calls = concurrent sessions.
        Retains the raw response as :attr:`last_session_new_snapshot` (discovery snapshot)."""
        resp = await self.request("session/new", self._with_session_meta(params), timeout=timeout)
        result = resp.result if resp.result else {}
        sid = result.get("sessionId") if isinstance(result, dict) else None
        if not sid:
            from personalclaw.acp.errors import AcpError
            from personalclaw.security import mask_child_output

            raise AcpError(
                "session/new answered without a session id: "
                + mask_child_output(repr(resp.result), limit=300)
            )
        if isinstance(result, dict):
            self._last_session_new_snapshot = dict(result)
        return self._bind_session(sid, session_files_dir=session_files_dir)

    async def load_session(
        self, params: dict, *, session_id: str, timeout: float = 60.0, session_files_dir=None
    ) -> AcpSession | None:
        """Issue ``session/load`` to resume an existing session. Returns a bound
        AcpSession when the agent confirms the resume (``modes`` present in the reply),
        or ``None`` when the load didn't take (caller falls back to ``session/new``)."""
        resp = await self.request("session/load", self._with_session_meta(params), timeout=timeout)
        result = resp.result if resp.result else {}
        if not isinstance(result, dict) or "modes" not in result:
            return None
        return self._bind_session(session_id, session_files_dir=session_files_dir)

    async def drain_init_notifications(self, *, duration: float = 10.0) -> None:
        """Best-effort drain of MCP-server init notifications after the handshake.

        These are id-less broadcast frames (no sessionId), so the router hands them to
        its broadcast sink rather than a session queue — there is nothing to actively
        read here (the single reader loop already consumes stdout continuously). We just
        yield the event loop briefly so those frames are read + logged before the first
        prompt, matching the old inline drain's ordering without blocking a turn."""
        deadline = time.monotonic() + min(duration, 3.0)
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            if not self.is_process_alive():
                return

    async def wait_for_session_frame(
        self,
        session_id: str,
        *,
        method: str,
        terminal_types: tuple[str, ...],
        timeout: float,
        also_track: tuple[str, ...] = (),
    ) -> dict:
        """Read this session's queue until a *method* frame whose ``status.type`` is in
        *terminal_types* arrives (e.g. compaction completed/failed). Returns
        ``{type, summary}`` or ``{type: "timeout"}``. Frames in *also_track* (e.g.
        metadata) update context stats in passing; everything else is ignored."""
        q = self._router.register_session(session_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                msg = await asyncio.wait_for(q.get(), timeout=min(remaining, _QUEUE_POLL))
            except (asyncio.TimeoutError, TimeoutError):
                if not self.is_process_alive():
                    break
                continue
            if msg.method == "_router/closed":
                break
            if msg.method == method:
                params = msg.params or {}
                status = params.get("status", {})
                s_type = status.get("type", "") if isinstance(status, dict) else str(status)
                if s_type in terminal_types:
                    return {"type": s_type, "summary": params.get("summary", "")}
        return {"type": "timeout"}

    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def agent_capabilities(self) -> dict:
        return self._agent_capabilities

    @property
    def supports_native_commands(self) -> bool:
        """Did THIS agent advertise the slash-command extension in ``initialize``?

        THE single derivation of that flag — the N=1 client and the concurrent session
        provider both read it here, so there is one answer per process rather than two
        that can disagree. Same one-key shape as ``AcpClient._can_load_session``
        (``loadSession``), and the same allowlist direction: an agent that said nothing
        gets no ``commands/execute`` request, because a ``-32601`` reply fails the whole
        turn instead of degrading."""
        return bool(self._agent_capabilities.get(CAP_COMMANDS, False))

    @property
    def last_session_new_snapshot(self) -> dict:
        """The raw ``session/new`` response from the most recent new-session (modes /
        models / configOptions) — the agent-discovery snapshot, read off this connection
        by the client (a runtime's Test keeps it as the agents that runtime offers)."""
        return self._last_session_new_snapshot

    async def close_session(self, session_id: str) -> None:
        s = self._sessions.pop(session_id, None)
        if s is not None:
            s.close()
        self._router.unregister_session(session_id)

    async def close(self) -> None:
        """Close all sessions + the router, and (on the live transport path) kill the
        backend process + reap its tree."""
        for s in list(self._sessions.values()):
            s.close()
        self._sessions.clear()
        await self._router.close()
        if self._transport is not None:
            await self._transport.kill(force=True)
            self._transport.teardown()
