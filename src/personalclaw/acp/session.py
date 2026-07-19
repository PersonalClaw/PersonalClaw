"""AcpSession — one ACP session's turn loop over a FrameRouter queue (P9 step 2).

The demux consumer side of concurrent ACP. Where today's ``AcpClient`` reads the
process's stdout INLINE during a turn (serializing all turns behind one lock),
an ``AcpSession`` owns a single ``sessionId`` and consumes ONLY that session's
queue — the :class:`FrameRouter` fans the shared stdout out to per-session queues,
so N sessions each await their own queue and run concurrently on one process.

This module is the session-scoped turn loop + its staleness/liveness/cancel logic,
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
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path

from personalclaw.acp import translate
from personalclaw.acp.translate import extract_text_chunk  # noqa: F401 — re-export
from personalclaw.acp.types import (
    EVENT_AGENT_SWITCHED,
    EVENT_CLEAR_STATUS,
    EVENT_COMPACTION_STATUS,
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_THINKING_CHUNK,
    METHOD_AGENT_SWITCHED,
    METHOD_CLEAR_STATUS,
    METHOD_COMPACTION_STATUS,
    METHOD_COMMANDS_EXECUTE,
    METHOD_METADATA,
    METHOD_PROMPT,
    METHOD_REQUEST_PERMISSION,
    METHOD_SESSION_UPDATE,
    OPTION_ALLOW_ONCE,
    STOP_REASON_END_TURN,
    AcpEvent,
    AcpPromptStats,
    JsonRpcMessage,
)

logger = logging.getLogger(__name__)

# ``extract_text_chunk`` lives in ``acp.translate`` (the single translation surface both
# the client and this session share); re-exported above so ``session.extract_text_chunk``
# stays a valid import for existing callers/tests. See translate.py for the full decoder set.


def classify_frame(msg: JsonRpcMessage, req_id: int) -> str:
    """Classify one inbound frame into a turn action — the single classifier for every
    turn loop (the N=1 AcpClient wrapper and the concurrent path both run their turns
    through AcpSession, so this is THE classifier; kept here, importing no client).
    Actions: complete | error | permission | update | metadata | compaction | clear |
    agent_switched | skip."""
    if msg.id == req_id and msg.method is None:
        return "error" if msg.error else "complete"
    if msg.method == METHOD_REQUEST_PERMISSION:
        return "permission"
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
    return "skip"

# Mirror the client's turn tuning (kept local so this module is self-contained).
_STALE_TURN_TIMEOUT = 90.0   # after text streamed, silence this long ⇒ treat turn complete
_QUEUE_POLL = 1.0            # how long to await the queue before re-checking liveness/deadline
_DEFAULT_PROMPT_TIMEOUT = 7200.0  # 2 hours — allow very long tool execution (matches client)


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
        send_request,         # async (method, params) -> (req_id, Future) : alloc id + expect + write
        send_response,        # async (req_id, result) -> None : reply to a server→client request
        cancel_session,       # async () -> None : session/cancel for THIS sid
        is_process_alive,     # () -> bool
        dialect=None,         # ACPDialect : permission-option parsing / approve outcome shape
        session_files_dir: "Path | None" = None,  # opt-in JSONL tool-result tailing
    ) -> None:
        self.session_id = session_id
        self._queue = queue
        self._send_request = send_request
        self._send_response = send_response
        self._cancel_session = cancel_session
        self._is_process_alive = is_process_alive
        from personalclaw.acp.dialect import DefaultDialect
        self._dialect = dialect or DefaultDialect()
        self._turn_lock = asyncio.Lock()   # one prompt in flight PER SESSION (not process-wide)
        self._cancelled = False
        self._closed = False
        # Per-turn caches (owned here, threaded into the shared translate.* decoders)
        # + the cross-turn context-usage % the client also carries between turns.
        self._tool_call_inputs: dict[str, str] = {}
        self._offered_options: dict[str, list[dict[str, str]]] = {}
        self.last_prompt_stats = AcpPromptStats()
        self._last_stop_reason: str = ""
        self._turn_done: asyncio.Event = asyncio.Event()
        # Optional per-session JSONL tool-result tail (vendor opt-in; no-op when unset).
        self._session_files_dir = session_files_dir
        self._jsonl_pos: int = 0

    def close(self) -> None:
        self._closed = True

    async def cancel(self) -> None:
        """Cancel the in-flight turn for THIS session only (co-tenants keep streaming)."""
        self._cancelled = True
        try:
            await self._cancel_session()
        except Exception:
            logger.debug("session %s: cancel_session failed (non-fatal)", self.session_id, exc_info=True)

    async def approve_tool(self, request_id: str | int, option_id: str | None = None) -> None:
        """Approve a pending tool permission for THIS session. Resolves the option id
        from what the agent offered (agent-defined ids need not equal ``allow_once``);
        falls back to ``allow_once`` only when nothing was captured."""
        rid = str(request_id)
        resolved = option_id
        if resolved is None:
            offered = self._offered_options.get(rid, [])
            resolved = self._dialect.select_allow_option_id(offered) or OPTION_ALLOW_ONCE
        self._offered_options.pop(rid, None)
        await self._send_response(request_id, self._dialect.approve_outcome(resolved))

    async def reject_tool(self, request_id: str | int) -> None:
        self._offered_options.pop(str(request_id), None)
        await self._send_response(request_id, self._dialect.reject_outcome())

    async def _drain_turn(
        self, req_id: int, response_future: "asyncio.Future[JsonRpcMessage]", timeout: float
    ) -> AsyncIterator[JsonRpcMessage]:
        """Yield this session's turn frames until the turn's terminal response lands,
        the process dies, or a stale-silence timeout.

        The FrameRouter demuxes stdout into TWO channels: the turn's own response
        (``id == req_id``, no method) resolves ``response_future`` (registered via
        ``router.expect``), while session-scoped NOTIFICATIONS (``session/update``
        chunks, ``session/request_permission``) land on ``self._queue``. So a turn is
        "drain the queue while awaiting the response future" — we select across both.
        Because stdout is in-order, every notification for this turn is enqueued BEFORE
        the response is routed; when the future resolves we flush the buffered queue
        frames first, then yield the terminal response last."""
        deadline = time.monotonic() + timeout
        last_data = time.monotonic()
        streamed = False
        get_task: "asyncio.Task[JsonRpcMessage] | None" = None
        try:
            while time.monotonic() < deadline and not self._closed and not self._cancelled:
                if get_task is None:
                    get_task = asyncio.ensure_future(self._queue.get())
                remaining = deadline - time.monotonic()
                await asyncio.wait(
                    {get_task, response_future},
                    timeout=min(remaining, _QUEUE_POLL),
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
                        if response_future.done():
                            try:
                                yield response_future.result()
                            except Exception:
                                logger.warning("session %s: turn ended on connection error",
                                               self.session_id, exc_info=True)
                        else:
                            logger.warning("session %s: connection closed mid-turn", self.session_id)
                        return
                    last_data = time.monotonic()
                    streamed = True
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
                            return
                        yield buffered
                    try:
                        yield response_future.result()
                    except Exception:
                        logger.warning("session %s: turn ended on connection error",
                                       self.session_id, exc_info=True)
                    return
                # 3. Idle tick — no frame, no response. Check liveness + staleness.
                if not self._is_process_alive():
                    logger.warning("session %s: process died mid-turn", self.session_id)
                    return
                if streamed and (time.monotonic() - last_data) > _STALE_TURN_TIMEOUT:
                    logger.warning("session %s: stale turn (silent %.0fs after streaming) — completing",
                                   self.session_id, time.monotonic() - last_data)
                    return
        finally:
            if get_task is not None and not get_task.done():
                get_task.cancel()

    # ── turn API (the surface acp_agent drives, mirrors AcpClient) ──────────────

    async def stream_events(
        self, message: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        """Send a prompt on THIS session and yield AcpEvents. Holds the per-session
        turn lock (one prompt in flight per session — never process-wide, so co-tenant
        sessions stream concurrently)."""
        async with self._turn_lock:
            self._cancelled = False
            self._turn_done.clear()
            req_id, fut = await self._send_request(
                METHOD_PROMPT,
                {"sessionId": self.session_id, "prompt": translate.encode_prompt_content(message)},
            )
            async for event in self._dispatch_frames(req_id, fut, timeout):
                yield event

    async def stream_command(
        self, command: str, timeout: float = _DEFAULT_PROMPT_TIMEOUT
    ) -> AsyncIterator[AcpEvent]:
        """Execute a slash command on THIS session and yield streaming AcpEvents
        (``commands/execute`` — output arrives in the terminal result, not chunks)."""
        async with self._turn_lock:
            self._cancelled = False
            self._turn_done.clear()
            name, args = _parse_slash_command(command)
            req_id, fut = await self._send_request(
                METHOD_COMMANDS_EXECUTE,
                {"sessionId": self.session_id, "command": {"command": name, "args": args}},
            )
            async for event in self._dispatch_frames(
                req_id, fut, timeout, extract_agent_from_result=True
            ):
                yield event

    async def _dispatch_frames(
        self,
        req_id: int,
        response_future: "asyncio.Future[JsonRpcMessage]",
        timeout: float,
        *,
        extract_agent_from_result: bool = False,
    ) -> AsyncIterator[AcpEvent]:
        """Turn ladder: classify each drained frame and translate it into AcpEvents via
        the shared ``translate.*`` decoders. This is THE turn loop — the N=1 AcpClient
        wrapper delegates here too. Two synthetic-EVENT_COMPLETE paths (tool-interrupted
        marker + stale-turn) and cross-turn ``context_pct`` carry — over the demuxed session
        queue, with NO process-wide lock and no JSONL/SEL/telemetry side-channels (the
        concurrent-capable backend streams tool results via protocol ``tool_call_update``
        frames, already handled by ``translate.extract_tool_update_events``)."""
        from personalclaw.acp.errors import AcpError, AcpTimeoutError

        prev_pct = self.last_prompt_stats.context_pct
        self.last_prompt_stats = AcpPromptStats(context_pct=prev_pct)
        self._tool_call_inputs.clear()
        self._offered_options.clear()
        stale_eligible = False
        got_complete = False
        saw_agent_switch = False

        async for msg in self._drain_turn(req_id, response_future, timeout):
            action = classify_frame(msg, req_id)
            self.last_prompt_stats.event_count += 1
            stale_eligible = False  # any frame resets; only non-thinking text re-enables

            if action == "complete":
                got_complete = True
                result = msg.result or {}
                reason = result.get("stopReason", "") or "" if isinstance(result, dict) else ""
                if extract_agent_from_result and isinstance(result, dict):
                    text = translate.format_command_result(result)
                    if text:
                        yield AcpEvent(kind=EVENT_TEXT_CHUNK, text=text)
                    if not saw_agent_switch:
                        data = result.get("data", {})
                        agent_info = data.get("agent") if isinstance(data, dict) else None
                        name = agent_info.get("name", "") if isinstance(agent_info, dict) else ""
                        if name:
                            yield AcpEvent(kind=EVENT_AGENT_SWITCHED, text=name)
                for tr in self._read_new_tool_results():  # flush remaining JSONL results
                    yield tr
                self._last_stop_reason = reason
                self._turn_done.set()
                yield AcpEvent(kind=EVENT_COMPLETE, stop_reason=reason)
                return
            if action == "error":
                raise AcpError(f"Prompt error: {msg.error}")
            if action == "permission":
                yield translate.build_permission_event(
                    msg, self._dialect, self._tool_call_inputs, self._offered_options
                )
            elif action == "update":
                chunk, is_thinking = translate.extract_text_chunk(msg)
                if chunk:
                    for tr in self._read_new_tool_results():  # results before this text
                        yield tr
                    kind = EVENT_THINKING_CHUNK if is_thinking else EVENT_TEXT_CHUNK
                    if not is_thinking:
                        self.last_prompt_stats.text_chunks += 1
                        stale_eligible = True
                    yield AcpEvent(kind=kind, text=chunk)
                    if not is_thinking and translate.is_tool_interrupted_marker(chunk):
                        # The backend security filter cancelled the turn's tools and will
                        # never send `result` — synthesize a complete so the caller exits.
                        got_complete = True
                        for tr in self._read_new_tool_results():
                            yield tr
                        self._turn_done.set()
                        yield AcpEvent(kind=EVENT_COMPLETE)
                        return
                tool_event = translate.extract_tool_event(
                    msg, self._tool_call_inputs, self.last_prompt_stats.tool_calls
                )
                if tool_event:
                    for tr in self._read_new_tool_results():  # prior tool's results first
                        yield tr
                    yield tool_event
                for upd_event in translate.extract_tool_update_events(msg, self._tool_call_inputs):
                    yield upd_event
            elif action == "metadata":
                pct = translate.extract_context_pct(msg)
                if pct is not None:
                    self.last_prompt_stats.context_pct = pct
            elif action == "compaction":
                params = msg.params or {}
                status = params.get("status", {})
                status_type = status.get("type", "") if isinstance(status, dict) else str(status)
                yield AcpEvent(kind=EVENT_COMPACTION_STATUS, text=status_type,
                               title=params.get("summary", ""))
            elif action == "clear":
                yield AcpEvent(kind=EVENT_CLEAR_STATUS)
            elif action == "agent_switched":
                saw_agent_switch = True
                params = msg.params or {}
                yield AcpEvent(kind=EVENT_AGENT_SWITCHED, text=params.get("agentName", ""))

        # Drain ended without a terminal `complete` frame.
        if not got_complete:
            self._last_stop_reason = ""
            self._turn_done.set()
            if stale_eligible:
                # Text streamed but no `result` — a stale turn; synthesize a complete
                # so callers finalize normally instead of surfacing a timeout.
                logger.info("session %s: stale-synthetic complete (streamed text, no result)",
                            self.session_id)
                yield AcpEvent(kind=EVENT_COMPLETE, stop_reason=STOP_REASON_END_TURN)
                return
            logger.warning("session %s: turn ended with no result and no streamed text — timeout",
                           self.session_id)
            raise AcpTimeoutError()

    async def wait_turn_done(self, timeout: float) -> str:
        """Block until the current turn completes; return its stop reason."""
        await asyncio.wait_for(self._turn_done.wait(), timeout=timeout)
        return self._last_stop_reason

    def has_active_turn(self) -> bool:
        return self._turn_lock.locked() and not self._turn_done.is_set()

    def context_usage_pct(self) -> float:
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
    """One ACP backend process, shared by N concurrent :class:`AcpSession`s (P9 step 2b).

    Owns the process handle + the single :class:`FrameRouter` over its stdout + the
    ``initialize`` handshake + a monotonic request-id counter. ``new_session()`` issues
    ``session/new`` on the SAME process, registers the returned ``sessionId`` with the
    router, and returns an :class:`AcpSession` bound to that session's queue. Multiple
    calls → multiple concurrent sessions on one process — the P9 win, gated by the
    backend dialect's ``supports_concurrent_sessions`` (the caller checks it before
    opening more than one).

    The process is either spawned via :meth:`spawn` (the live path — reuses the shared
    :class:`~personalclaw.acp.transport.AcpProcess`, the SAME machinery the one-session
    client uses, no duplicate spawn/kill) or injected as a raw ``proc`` for unit tests.
    ``request(method, params)`` writes a JSON-RPC request and awaits its response via the
    router's pending-future mechanism (id-correlated)."""

    def __init__(self, proc, router, *, dialect=None, transport=None) -> None:
        # Either a shared AcpProcess transport (live path) OR a raw asyncio subprocess
        # (unit tests inject a fake with .stdin/.stdout/.returncode). The transport is
        # preferred; the raw proc is a test-compat shim writing straight to stdin.
        self._transport = transport
        self._proc = proc                 # None on the transport path
        self._router = router             # a started FrameRouter over the line source
        self._dialect = dialect
        self._next_id = 0
        self._sessions: dict[str, AcpSession] = {}
        self._agent_capabilities: dict = {}
        # Raw ``session/new`` response (modes / models / configOptions) from the most
        # recent new-session — the discovery snapshot the connection pool + the N=1
        # client read off a warmed connection (no second throwaway spawn).
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
    ) -> "AcpConnection":
        """Spawn a backend process (shared AcpProcess transport) + start a FrameRouter
        over its stdout, and return a live AcpConnection ready for ``initialize`` +
        ``new_session``. This is the concurrent path's entry point."""
        from personalclaw.acp.reader import FrameRouter
        from personalclaw.acp.transport import AcpProcess

        transport = AcpProcess(
            command=command, work_dir=work_dir, sandbox_mode=sandbox_mode,
            extra_env=extra_env, session_key=session_key, channel_id=channel_id,
        )
        await transport.spawn()
        router = FrameRouter(transport.readline)
        router.start()
        return cls(None, router, dialect=dialect, transport=transport)

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

    async def request(self, method: str, params: dict, *, timeout: float = 60.0):
        """Write a JSON-RPC request and await its id-correlated response via the router."""
        rid, fut = await self.send_request(method, params)
        return await asyncio.wait_for(fut, timeout=timeout)

    async def initialize(self, params: dict, *, timeout: float = 240.0) -> dict:
        """Do the one-per-process ``initialize`` handshake; capture agentCapabilities."""
        resp = await self.request("initialize", params, timeout=timeout)
        self._agent_capabilities = (resp.result or {}).get("agentCapabilities") or {} if resp.result else {}
        return self._agent_capabilities

    def _bind_session(self, sid: str, session_files_dir=None) -> AcpSession:
        """Register *sid* with the router and construct an AcpSession bound to its queue,
        with the send/response/cancel closures scoped to this connection + sid. Shared by
        :meth:`new_session` and :meth:`load_session`."""
        queue = self._router.register_session(sid)

        async def _send_request(method, req_params):
            return await self.send_request(method, req_params)

        async def _send_response(req_id, result):
            await self.send_response(req_id, result)

        async def _cancel():
            await self._write({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": sid}})

        sess = AcpSession(
            sid, queue,
            send_request=_send_request,
            send_response=_send_response,
            cancel_session=_cancel,
            is_process_alive=self.is_process_alive,
            dialect=self._dialect,
            session_files_dir=session_files_dir,
        )
        self._sessions[sid] = sess
        return sess

    async def new_session(
        self, params: dict, *, timeout: float = 60.0, session_files_dir=None
    ) -> AcpSession:
        """Issue ``session/new`` on this process, register the sessionId with the router,
        return an AcpSession bound to its queue. Multiple calls = concurrent sessions.
        Retains the raw response as :attr:`last_session_new_snapshot` (discovery snapshot)."""
        resp = await self.request("session/new", params, timeout=timeout)
        result = resp.result if resp.result else {}
        sid = result.get("sessionId") if isinstance(result, dict) else None
        if not sid:
            raise RuntimeError(f"session/new returned no sessionId (result={resp.result!r})")
        if isinstance(result, dict):
            self._last_session_new_snapshot = dict(result)
        return self._bind_session(sid, session_files_dir=session_files_dir)

    async def load_session(
        self, params: dict, *, session_id: str, timeout: float = 60.0, session_files_dir=None
    ) -> AcpSession | None:
        """Issue ``session/load`` to resume an existing session. Returns a bound
        AcpSession when the agent confirms the resume (``modes`` present in the reply),
        or ``None`` when the load didn't take (caller falls back to ``session/new``)."""
        resp = await self.request("session/load", params, timeout=timeout)
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
    def last_session_new_snapshot(self) -> dict:
        """The raw ``session/new`` response from the most recent new-session (modes /
        models / configOptions) — the agent-discovery snapshot, read off a live
        connection by the pool + the N=1 client."""
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
