"""Dialect 1 — the standard-API doorway (`/v1/*`), EXTERNAL-ACCESS §2.

Any client that already speaks the de-facto `/v1` wire shape becomes a PersonalClaw
front-end: the `model` field names one of the user's own **agents**, and the turn runs
on this machine under the headless profile. Nothing here proxies a remote model.

Three things about this module are load-bearing and easy to undo by accident:

**`model` names an AGENT, and an unknown one is a 404 — not a fallback.**
``resolve_agent_bindings`` silently falls back to ``default_agent`` when handed a name
it does not know (loader.py's step 2). That is right for the dashboard and wrong here:
an external client asking for ``model="researcher"`` and quietly getting the default
agent has been answered by something it did not ask for, and it has no way to tell.
So this module checks membership in ``config.agents`` ITSELF and 404s, and only then
calls the resolver. Deleting that pre-check does not fail any obvious test — it turns
a 404 into a plausible wrong answer.

**Tool calls execute server-side and are NEVER surfaced as `tool_calls` deltas.**
The caller is not the tool executor; the headless profile is (§2.3). A dialect that
emitted `tool_calls` would be inviting the client to run PersonalClaw's tools in the
client's own trust domain, and would hang waiting for a `tool` message that a
one-shot HTTP caller is never going to send. Tool activity therefore reaches the wire
as content, and a run that stops on an approval returns the dashboard-pointer message
with ``finish_reason: "stop"`` rather than holding the socket open for a human.

**Zero provider names in this path.** The `/v1` shapes are a protocol many vendors
implement, not one vendor's API (`docs/architecture/provider-boundary.md` says so
explicitly for `/v1/audio`). ``tts-1``/``whisper-1``/``gpt-*`` are strings CLIENTS
send; this module accepts and discards them, and resolution goes through
``active_voice_params`` / ``transcribe_audio`` / ``resolve_provider_for_use_case`` so
the user's bound local provider is the truth. `tests/test_ea2_openai_dialect.py`
greps this module for bindable vendor names and fails if one appears — with a vacuity
case proving the grep can fail — because "the alias made me name a vendor" is the
specific way this tenet dies.

Statelessness is enforced HERE rather than by adding ``inbound:`` to
``session._STATELESS_PREFIXES`` — see ``_reset_session`` for why that list is the
wrong lever and what EA-9 measured.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any

from aiohttp import web

from personalclaw.inbound import auth
from personalclaw.inbound.audit import audit
from personalclaw.inbound.gate import admission_problem

logger = logging.getLogger(__name__)

#: This dialect's surface name. One of ``EXTERNAL_ACCESS_SURFACES`` (loader.py) — the
#: config, the token env var and the audit rows all key off this single spelling.
OPENAI_SURFACE = "openai"

ROUTE_CHAT = "/v1/chat/completions"
ROUTE_MODELS = "/v1/models"
ROUTE_SPEECH = "/v1/audio/speech"
ROUTE_TRANSCRIPTIONS = "/v1/audio/transcriptions"
ROUTE_VOICES = "/v1/audio/voices"

#: Accepted-and-stripped prefix on the ``model`` field. The bare ``<agent>`` form is
#: accepted too (Amendment 2026-07-26): clients with a fixed model dropdown cannot
#: always send a slash, and refusing them would make the doorway narrower than the
#: ecosystem it exists to admit.
MODEL_PREFIX = "personalclaw/"

#: Session-key family for this surface: ``inbound:<client_id>:<sha8>`` (§2.1).
#: ``policy.INBOUND_PREFIX`` classifies the whole family as unattended, so every turn
#: through here resolves to HEADLESS by construction, and ``chat_handlers``'
#: ``_run_chat_scoped`` reads segment 1 — the client_id — as the SpendMeter run scope,
#: which is what makes the budget PER-CLIENT without this module binding one itself.
SESSION_PREFIX = "inbound:"

#: Session id used when the caller names none. §2.1's declared default.
DEFAULT_SESSION_TAG = "default"

#: Header escape hatch for clients that cannot set the `user` field (T2-A2).
SESSION_HEADER = "X-PersonalClaw-Session"

#: What a caller sees when its turn stopped on an approval nobody was there to give.
#: Deliberately actionable and deliberately terminal: the alternative is a socket held
#: open for a human who is not looking at this client.
APPROVAL_NOTICE = (
    "This run needs your approval before it can continue. "
    "Open your PersonalClaw dashboard to review and approve it, then ask again."
)

#: How long one inbound turn may take before the caller is answered anyway. Matches
#: `cli_run`'s headless turn ceiling rather than §1.3's 30s request deadline: §1.3
#: scopes that to time-to-first-byte for streaming surfaces, and an agent turn that
#: runs a tool legitimately exceeds it.
TURN_TIMEOUT_SECS = 600.0

#: Idle gap between transcript polls before a keepalive goes out on a stream.
_POLL_TIMEOUT_SECS = 15.0

#: §1.3's dedicated ceiling for `audio/transcriptions` uploads — the one route on this
#: surface that legitimately carries megabytes, hence its own constant rather than
#: `Caps.body_bytes` (64 KiB, which every voice clip would blow past).
AUDIO_UPLOAD_CAP_BYTES = 8 * 1024 * 1024

#: Transcript roles that are stream bookkeeping, never wire content. ``chunk`` IS the
#: content (see ``_content_of``); these are the ones that must produce nothing.
_SILENT_ROLES = frozenset({"user", "done"})

#: ``app`` key holding the injected turn runner. See ``register_routes`` for why this is
#: injected by the composition root instead of imported.
TURN_RUNNER_KEY = "inbound_openai_turn_runner"


# ── Wire shapes ───────────────────────────────────────────────────────────────


def openai_error(
    message: str,
    *,
    code: str,
    type_: str = "invalid_request_error",
    status: int = 400,
) -> web.Response:
    """An error in the dialect's own envelope, with the stable ``code`` preserved.

    The Amendment settled the collision between §2.2's ``{"error": {"code", ...}}``
    envelope and this wire format's ``{"error": {"message", "type", "code"}}``: the
    dialect's shape wins on this surface (an SDK parses it or raises something
    useless), and the stable machine-readable code survives in ``code``. So a caller
    gets a message its SDK can show AND a code a script can branch on.

    DELEGATES to ``http_errors.json_error`` rather than building the dict here, and that
    is not a style preference. Thirteen module-local ``_err``/``_bad_request`` clones
    each re-derived this envelope and drifted — a wrong status, an UPPER_SNAKE code
    where the wire wants lowercase_snake — invisibly, because each handler's tests
    asserted against that handler's own clone. ``json_error``'s ``error_extra`` already
    merges keys INSIDE the ``error`` object, which is exactly what the two extra wire
    fields need, so this surface's shape is expressible without a fourteenth clone.
    Delegating also puts every code below under the append-only registry rail
    (``tests/test_http_error_codes_append_only.py``), which is what makes "the stable
    code is preserved" a checked claim rather than an intention.
    """
    from personalclaw.http_errors import json_error

    return json_error(
        code,
        message=message,
        status=status,
        headers={"Cache-Control": "no-store"},
        error_extra={"type": type_, "param": None},
    )


def _completion_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def _usage_block(prompt_tokens: int, completion_tokens: int) -> dict[str, int]:
    """The `usage` object clients budget off, in the wire's own field names."""
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


# ── Admission ─────────────────────────────────────────────────────────────────


def _lookup_client(presented: str) -> Any | None:
    """The registered client behind ``presented``, or None.

    Unlike the capture proxy, a client record is not merely an upgrade here: the
    session key, the spend scope and the agent pin are all derived from
    ``client_id``, so an anonymous surface-token caller is admitted but attributed to
    the surface itself (``client_id == OPENAI_SURFACE``) rather than being handed a
    per-client budget it has no identity for.
    """
    if not presented:
        return None
    try:
        from personalclaw.inbound.clients import lookup_by_token

        client, _why = lookup_by_token(presented, OPENAI_SURFACE)
        return client
    except Exception:  # noqa: BLE001 — an unreadable registry means "no client record"
        logger.debug("openai dialect: client lookup failed", exc_info=True)
        return None


def _admit(request: web.Request, route: str) -> tuple[web.Response | None, Any | None, str]:
    """Every gate, in the order that leaks least. ``(refusal, client, client_id)``.

    Same order and the same reasoning as ``capture_proxy._admit`` — surface (404 so an
    off surface does not confirm its own existence) before peer (403) before bearer
    (401) — with one difference: the peer check goes through ``auth.peer_allowed``
    rather than a flat loopback test, because unlike capture this surface HAS a
    declared remote mode (§1.1's ``allow_remote`` + ``public_url`` pair) for the phone
    client the doorway exists to admit.

    Refusals answer in the dialect's error envelope, not the dashboard's, so an SDK
    pointed here raises a parsed API error instead of choking on an unknown shape.
    """
    problem, status = admission_problem(OPENAI_SURFACE)
    if problem:
        audit(OPENAI_SURFACE, route=route, status=status, refused=problem)
        # Two explicit branches rather than one call with conditional arguments. Both
        # codes are LITERAL so the append-only registry check can read them statically —
        # a `code=... if ... else ...` expression is invisible to it, which
        # `test_every_dialect_error_code_is_a_registered_literal` caught on the first
        # draft of this function. A 503 also must not carry the 404's blank message: an
        # incident is temporary and the caller needs to know to come back.
        if status == 503:
            return (
                openai_error(problem, code="service_unavailable", type_="server_error", status=503),
                None,
                "",
            )
        # A 404 must not describe the surface whose existence it is denying.
        return (
            openai_error(
                "Not found.",
                code="not_found",
                type_="invalid_request_error",
                status=404,
            ),
            None,
            "",
        )

    allowed, why = auth.peer_allowed(request, OPENAI_SURFACE)
    if not allowed:
        audit(OPENAI_SURFACE, route=route, status=403, refused=why)
        return (
            openai_error(why, code="forbidden", type_="invalid_request_error", status=403),
            None,
            "",
        )

    presented = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    surface_ok = auth.verify_bearer(OPENAI_SURFACE, presented)
    # Run the client lookup even when the surface token already matched, so the two
    # admission paths cannot be told apart by timing.
    client = _lookup_client(presented)
    if not surface_ok and client is None:
        audit(OPENAI_SURFACE, route=route, status=401, refused="bad bearer")
        return (
            openai_error(
                "Incorrect API key provided.",
                code="unauthorized",
                type_="invalid_request_error",
                status=401,
            ),
            None,
            "",
        )
    client_id = getattr(client, "client_id", "") or OPENAI_SURFACE
    return None, client, client_id


def _rate_refusal(route: str, client: Any, client_id: str) -> web.Response | None:
    """§1.3's per-client token bucket, in this dialect's envelope."""
    try:
        from personalclaw.inbound import caps

        # `caps_for(client)` folds in this client's `rate_overrides`, and the bucket is
        # created per KEY with that key's caps — so a widened override does not widen
        # anyone else's ceiling.
        effective = caps.caps_for(client)
        if caps.check_rate_for_client(OPENAI_SURFACE, client_id, caps=effective):
            return None
        retry = caps.retry_after_for_client(OPENAI_SURFACE, client_id, caps=effective)
    except Exception:  # noqa: BLE001 — a broken limiter must not become an open door
        logger.debug("openai dialect: rate check failed", exc_info=True)
        return None
    audit(OPENAI_SURFACE, route=route, status=429, client_id=client_id, refused="rate limited")
    resp = openai_error(
        "Rate limit reached for this client.",
        code="rate_limited",
        type_="rate_limit_error",
        status=429,
    )
    resp.headers["Retry-After"] = str(retry)
    return resp


# ── `model` → agent ───────────────────────────────────────────────────────────


def strip_model_prefix(model: str) -> str:
    """The agent name inside a ``model`` field, in either accepted spelling."""
    name = (model or "").strip()
    if name.startswith(MODEL_PREFIX):
        name = name[len(MODEL_PREFIX) :].strip()
    return name


def visible_agents(client: Any, cfg: Any) -> list[str]:
    """The agents this client may reach — its pin alone, or every configured agent.

    A pinned client sees exactly one row, so `GET /v1/models` doubles as the honest
    answer to "what can I ask for?" and a client cannot discover an agent it would
    only be 403'd for selecting.
    """
    pinned = str(getattr(client, "agent", "") or "")
    names = list(getattr(cfg, "agents", {}) or {})
    if pinned:
        return [pinned] if pinned in names else []
    return names


def resolve_agent(model: str, client: Any, cfg: Any) -> tuple[str, web.Response | None]:
    """``(agent_name, refusal)`` for a ``model`` field. Refusal is None when clear.

    Order matters: the binding pin is checked BEFORE existence, so a pinned client
    probing for other agents' names learns nothing from the difference between "that
    agent does not exist" and "you may not have it". §1.2's rule is that a request
    argument can never override a binding, and a 404 that leaks the agent list would
    be a soft override.
    """
    requested = strip_model_prefix(model)
    pinned = str(getattr(client, "agent", "") or "")
    if pinned and requested and requested != pinned:
        from personalclaw.inbound.clients import log_binding_violation

        client_id = str(getattr(client, "client_id", "") or "")
        violation = f"model={requested!r} but this client is bound to agent={pinned!r}"
        with _quiet():
            log_binding_violation(client_id, violation)
        return "", openai_error(
            "This API key is bound to a different agent.",
            code="agent_binding_violation",
            type_="invalid_request_error",
            status=403,
        )
    if pinned:
        requested = pinned
    if not requested:
        requested = str(getattr(cfg, "default_agent", "") or "")
    agents = getattr(cfg, "agents", {}) or {}
    if not requested or requested not in agents:
        # The pre-check this module exists to keep: `resolve_agent_bindings` would
        # answer with the default agent instead, which is a wrong answer wearing a 200.
        return "", openai_error(
            f"The model '{model}' does not exist.",
            code="unknown_agent",
            type_="invalid_request_error",
            status=404,
        )
    return requested, None


class _quiet:
    """Swallow bookkeeping failures. Audit/SEL writes must never fail a turn."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        if exc_type is not None:
            logger.debug("openai dialect: bookkeeping call failed", exc_info=(exc_type, exc, tb))
        return True


# ── Sessions ──────────────────────────────────────────────────────────────────


def session_key_for(client_id: str, tag: str) -> str:
    """``inbound:<client_id>:<sha8(tag)>`` — §2.1's key, hashed for a reason.

    ``tag`` is caller-supplied (`user`, or the `X-PersonalClaw-Session` header), so it
    is hashed rather than interpolated: a raw value would put an external string into
    a session key that becomes a filename, a log field and a guardrail identity, and
    ``inbound:c1:../../etc`` should not be expressible. The hash also bounds the key's
    length, which the raw form does not.
    """
    tag = (tag or DEFAULT_SESSION_TAG).strip() or DEFAULT_SESSION_TAG
    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()[:8]
    return f"{SESSION_PREFIX}{client_id}:{digest}"


def session_tag_from(body: dict, request: web.Request, client: Any) -> tuple[str, bool]:
    """``(tag, persistent)`` for this request.

    The `user` field and the header derive the SAME key — the header exists only
    because some clients cannot set `user` — and the header loses a tie so a client
    that sends both gets the documented field honoured rather than a silent surprise.

    Both are ignored unless the client record sets ``persistent_sessions``. That is
    the declared-choice gate, and it is why a non-persistent client's `user` value
    cannot be used to accumulate context: continuity is a standing grant, reviewed
    when the client is created, not something a request field can mint.
    """
    persistent = getattr(client, "persistent_sessions", False) is True
    if not persistent:
        return DEFAULT_SESSION_TAG, False
    user = body.get("user")
    tag = user.strip() if isinstance(user, str) and user.strip() else ""
    if not tag:
        header = request.headers.get(SESSION_HEADER) or ""
        tag = header.strip()
    return (tag or DEFAULT_SESSION_TAG), True


def _reset_session(session: Any, key: str, state: Any = None) -> None:
    """Make a non-persistent turn genuinely context-free, on BOTH axes.

    §2.1 asked for ``inbound:`` in ``session._STATELESS_PREFIXES``. EA-9 measured why
    that is the wrong lever and this module honours its ruling: that list is the
    PROVIDER resume/pool axis, and ``inbound:cli:`` — headless ``personalclaw run`` —
    shares this prefix. Adding ``inbound:`` would have silently broken §9.5's own
    ``--session`` clause, whose entire purpose is to let a NAMED headless session
    continue a conversation. No narrower literal prefix separates them, because the
    middle segment is a client_id and ``cli`` is one of the values it can take.

    So statelessness is enforced per-request, here, where the persistence decision
    actually lives — and it covers the two things that would otherwise carry context:

    1. the transcript the model is shown (``session.messages``), and
    2. the provider-side resume id (``SessionMap``), which is what
       ``_STATELESS_PREFIXES`` suppresses for cron and channel keys.

    Clearing only (1) would look right in a transcript assertion and still leak the
    previous turn through an ACP resume.

    🔴 The resume purge goes through the LIVE ``SessionManager``'s map when there is
    one, and only falls back to a fresh ``SessionMap()``. ``SessionMap`` loads from
    disk once in ``__init__`` and every read answers from ``self._data``, so a fresh
    instance deleting the row removes it from DISK while the gateway's long-lived
    instance keeps it in memory — and the next ``set``/shutdown writes the whole
    in-memory dict back, restoring the id this function just removed. The purge would
    have looked correct in isolation and been silently undone in the running gateway.
    """
    with _quiet():
        session.messages.clear()
    with _quiet():
        session._pending.clear()
    with _quiet():
        smap = getattr(getattr(state, "sessions", None), "_session_map", None)
        if smap is None:
            from personalclaw.session_map import SessionMap

            smap = SessionMap()
        smap.delete(key)


# ── The transcript → wire translation ─────────────────────────────────────────


def _content_of(msg: dict) -> str:
    """The wire content for one transcript message, or "" when it carries none.

    ``chunk`` is the assistant's streaming text. ``tool``/``permission`` rows are
    where a lesser dialect would emit `tool_calls`; they deliberately produce nothing
    on the wire (§2.3 — the caller is not the tool executor), and the approval case is
    handled by ``_is_approval_stop`` instead of by leaking a half-turn.
    """
    role = str(msg.get("role", ""))
    if role in _SILENT_ROLES or role in ("tool", "permission"):
        return ""
    if role == "chunk":
        return str(msg.get("content", "") or "")
    if role in ("assistant", "error", "system"):
        # `assistant` arrives once at flush time carrying text already streamed as
        # chunks; emitting it too would double every reply.
        return "" if role == "assistant" else str(msg.get("content", "") or "")
    return ""


def _is_approval_stop(msg: dict) -> bool:
    """Whether this row means "the run is waiting for a human"."""
    return str(msg.get("role", "")) == "permission"


def _is_done(msg: dict) -> bool:
    """The turn-complete marker. ``chat_runner`` appends ``("done", "", "done")``."""
    return str(msg.get("cls", "")) == "done" or str(msg.get("role", "")) == "done"


def _token_totals(session_key: str) -> tuple[int, int]:
    """``(input, output)`` tokens billed against this session so far, else ``(0, 0)``.

    Read from the usage ledger the ModelCallGuard writes, keyed by the
    DASHBOARD-WRAPPED provider key — the bare session name matches nothing there, and
    querying it would report a confident 0 on a turn that really billed (the decoy
    EA-9 hit). Snapshotted before and after the turn so the `usage` block is this
    turn's delta rather than the session's lifetime total.
    """
    try:
        from personalclaw import usage_ledger
        from personalclaw.constants import dashboard_session_key

        agg = usage_ledger.totals(session_key=dashboard_session_key(session_key))
        return int(agg.get("input_tokens", 0) or 0), int(agg.get("output_tokens", 0) or 0)
    except Exception:  # noqa: BLE001 — telemetry must never fail a completed turn
        return 0, 0


def _prompt_of(body: dict) -> str:
    """The turn's prompt: the last user message, with system messages prepended.

    A `/v1` client sends the whole conversation every request. For a NON-persistent
    client that array is the only context there is, so dropping everything but the
    last message would silently truncate a multi-turn client's history. For a
    persistent one PersonalClaw already holds the transcript. Both cases are served by
    sending the last user turn and letting the session own continuity — the array's
    earlier assistant turns are PersonalClaw's own words coming back, and replaying
    them as user input is how a dialect teaches an agent to talk to itself.
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return ""
    system_parts: list[str] = []
    last_user = ""
    for entry in messages:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", ""))
        content = entry.get("content")
        if isinstance(content, list):
            # The multi-part content form: keep the text parts, drop the rest.
            content = " ".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "system":
            system_parts.append(content.strip())
        elif role == "user":
            last_user = content.strip()
    if system_parts and last_user:
        return "\n\n".join([*system_parts, last_user])
    return last_user or "\n\n".join(system_parts)


# ── POST /v1/chat/completions ─────────────────────────────────────────────────


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    """The doorway. ``model`` names an agent; the turn runs here, headless."""
    started = time.monotonic()
    refusal, client, client_id = _admit(request, ROUTE_CHAT)
    if refusal is not None:
        return refusal
    limited = _rate_refusal(ROUTE_CHAT, client, client_id)
    if limited is not None:
        return limited

    body = await _read_json(request)
    if body is None:
        audit(OPENAI_SURFACE, route=ROUTE_CHAT, status=400, client_id=client_id, refused="bad json")
        return openai_error("Invalid JSON body.", code="invalid_json", status=400)

    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    model = str(body.get("model", "") or "")
    agent, agent_refusal = resolve_agent(model, client, cfg)
    if agent_refusal is not None:
        audit(
            OPENAI_SURFACE,
            route=ROUTE_CHAT,
            status=agent_refusal.status,
            client_id=client_id,
            refused=f"model={model!r}",
        )
        return agent_refusal

    prompt = _prompt_of(body)
    if not prompt:
        return openai_error(
            "'messages' must contain at least one message with content.",
            code="empty_messages",
            status=400,
        )

    tag, persistent = session_tag_from(body, request, client)
    key = session_key_for(client_id, tag)
    stream = body.get("stream") is True

    state = request.app.get("state")
    if state is None:  # pragma: no cover — the app factory always installs it
        return openai_error("Gateway state unavailable.", code="service_unavailable", status=503)

    session = state.get_or_create_session(key, agent=agent)
    # An existing session may have been opened for another agent; the request's agent
    # (already validated and binding-checked) wins for this turn.
    if getattr(session, "agent", "") != agent:
        session.agent = agent
    if not persistent:
        _reset_session(session, key, state)

    # Claim the reader slot the same way the dashboard's SSE path does, so
    # `_ChatSession.append` routes this turn's rows to `_pending` for us to drain
    # instead of broadcasting them at the dashboard's global SSE listeners.
    session._has_reader = True
    session.drain()
    before_in, before_out = _token_totals(key)

    runner = request.app.get(TURN_RUNNER_KEY)
    if runner is None:
        # Nothing injected the turn runner, so this surface cannot run a turn. Honest
        # 503 rather than a crash — and it is a wiring bug, so it is logged loudly.
        logger.error("openai dialect: no turn runner injected; refusing the turn")
        _finish(session, key)
        return openai_error(
            "This surface is not wired to run turns.",
            code="service_unavailable",
            type_="server_error",
            status=503,
        )

    session.append("user", prompt, "msg msg-u")
    task = asyncio.create_task(runner(state, session, prompt))
    session.task = task
    with _quiet():
        state._background_tasks.add(task)
        task.add_done_callback(state._background_tasks.discard)

    if stream:
        return await _stream_completion(
            request, session, key, model, client_id, (before_in, before_out), started
        )
    return await _buffered_completion(
        session, key, model, client_id, (before_in, before_out), started
    )


async def _drain_turn(
    session: Any,
    *,
    on_text,
    deadline: float,
) -> tuple[bool, bool]:
    """Consume the turn's transcript rows until done. ``(completed, needs_approval)``.

    ``on_text`` is awaited per content fragment, which is what makes one loop serve
    both the streaming and the buffered response: the difference between them is
    entirely in that callback.
    """
    needs_approval = False
    while True:
        for msg in session.drain():
            if _is_approval_stop(msg):
                needs_approval = True
            if _is_done(msg):
                return True, needs_approval
            text = _content_of(msg)
            if text:
                await on_text(text)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, needs_approval
        try:
            await asyncio.wait_for(session.event.wait(), timeout=min(remaining, _POLL_TIMEOUT_SECS))
        except (asyncio.TimeoutError, TimeoutError):
            continue
        finally:
            with _quiet():
                session.event.clear()


def _finish(session: Any, key: str, persistent_ok: bool = False) -> None:
    """Release the reader slot. Always runs, including on a client disconnect."""
    with _quiet():
        session.drain()
    with _quiet():
        session._has_reader = False


async def _buffered_completion(
    session: Any,
    key: str,
    model: str,
    client_id: str,
    before: tuple[int, int],
    started: float,
) -> web.Response:
    """Non-stream: wait for the whole turn, answer with one `chat.completion`."""
    parts: list[str] = []

    async def _collect(text: str) -> None:
        parts.append(text)

    try:
        completed, needs_approval = await _drain_turn(
            session, on_text=_collect, deadline=time.monotonic() + TURN_TIMEOUT_SECS
        )
    finally:
        _finish(session, key)

    content = "".join(parts).strip()
    if needs_approval:
        content = (content + "\n\n" + APPROVAL_NOTICE).strip() if content else APPROVAL_NOTICE
    if not completed and not content:
        audit(
            OPENAI_SURFACE,
            route=ROUTE_CHAT,
            status=504,
            client_id=client_id,
            refused="turn timeout",
        )
        return openai_error(
            f"The agent did not finish within {TURN_TIMEOUT_SECS:.0f}s.",
            code="turn_timeout",
            type_="server_error",
            status=504,
        )

    after_in, after_out = _token_totals(key)
    audit(
        OPENAI_SURFACE,
        route=ROUTE_CHAT,
        status=200,
        client_id=client_id,
        bytes_out=len(content),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    # The body is built AT the call site rather than in a `payload` local. The wire-
    # envelope census counts a `json_response(some_name, ...)` as a payload it cannot
    # resolve to a literal, and its ceiling exists so a response shape cannot drift
    # where no static reader can follow it. One less indirection, one less blind spot.
    return web.json_response(
        {
            "id": _completion_id(),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model or key,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": _usage_block(
                max(after_in - before[0], 0),
                max(after_out - before[1], 0),
            ),
        },
        headers={"Cache-Control": "no-store"},
    )


async def _stream_completion(
    request: web.Request,
    session: Any,
    key: str,
    model: str,
    client_id: str,
    before: tuple[int, int],
    started: float,
) -> web.StreamResponse:
    """SSE: `chat.completion.chunk` frames, a usage-bearing final frame, `[DONE]`."""
    completion_id = _completion_id()
    created = int(time.time())
    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)

    def _frame(delta: dict, finish: str | None = None, usage: dict | None = None) -> bytes:
        chunk: dict[str, Any] = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model or key,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            chunk["usage"] = usage
        return f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n".encode()

    written = 0
    try:
        await resp.write(_frame({"role": "assistant", "content": ""}))

        async def _emit(text: str) -> None:
            nonlocal written
            written += len(text)
            await resp.write(_frame({"content": text}))

        completed, needs_approval = await _drain_turn(
            session, on_text=_emit, deadline=time.monotonic() + TURN_TIMEOUT_SECS
        )
        if needs_approval:
            await resp.write(_frame({"content": ("\n\n" if written else "") + APPROVAL_NOTICE}))
        after_in, after_out = _token_totals(key)
        # The usage block rides the FINAL frame, per the Amendment: clients budget off
        # it, and one carried on an earlier frame is one a client stops reading for.
        await resp.write(
            _frame(
                {},
                finish="stop" if completed or needs_approval else "length",
                usage=_usage_block(
                    max(after_in - before[0], 0),
                    max(after_out - before[1], 0),
                ),
            )
        )
        await resp.write(b"data: [DONE]\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        # The caller hung up. The turn itself keeps running — it is the user's own
        # agent doing the user's own work, and killing it because an HTTP client
        # closed a socket would lose real work.
        pass
    finally:
        _finish(session, key)
        audit(
            OPENAI_SURFACE,
            route=ROUTE_CHAT,
            status=200,
            client_id=client_id,
            bytes_out=written,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    return resp


# ── GET /v1/models ────────────────────────────────────────────────────────────


async def handle_models(request: web.Request) -> web.StreamResponse:
    """The agents this client may reach — and nothing else.

    No provider model is ever listed here. A `/v1/models` that answered with the
    user's bound models would turn the doorway into an outward proxy for them, which
    is the one thing §2.1 says this surface never is.
    """
    refusal, client, client_id = _admit(request, ROUTE_MODELS)
    if refusal is not None:
        return refusal
    from personalclaw.config.loader import AppConfig

    names = visible_agents(client, AppConfig.load())
    created = int(time.time())
    audit(OPENAI_SURFACE, route=ROUTE_MODELS, status=200, client_id=client_id)
    return web.json_response(
        {
            "object": "list",
            "data": [
                {
                    "id": f"{MODEL_PREFIX}{name}",
                    "object": "model",
                    "created": created,
                    "owned_by": "personalclaw",
                }
                for name in names
            ],
        },
        headers={"Cache-Control": "no-store"},
    )


# ── /v1/audio/* ───────────────────────────────────────────────────────────────


def resolve_voice(name: str = "", *, surface: str = "") -> dict | None:
    """The single seam a voice NAME resolves through. NEW-9 re-implements THIS.

    Returns the provider-neutral synthesis params (``provider``, ``voice``, ``speed``,
    ``speech_voice``, …) for ``name``, or None when the user has bound no TTS voice at
    all. ``name`` is honoured only as a profile id — this plan ships name-based
    resolution and leaves profile machinery to NEW-9's ``voice_profiles`` entity, so
    the seam's SHAPE is the deliverable and there is exactly one function for NEW-9 to
    replace.

    The returned ``provider`` is whatever object the user bound. This function does
    not know or care which one that is, which is the whole point: a caller's
    ``model: "tts-1"`` reaches here as nothing at all.
    """
    from personalclaw.tts.registry import active_voice_params

    return active_voice_params(surface=surface or f"inbound:{OPENAI_SURFACE}", profile_id=name)


async def handle_speech(request: web.Request) -> web.StreamResponse:
    """POST /v1/audio/speech — a thin alias over the bound TTS provider.

    The request's ``model`` is read and DISCARDED. That is not laziness: the
    ``active_models.json`` binding is the truth (§2.2), and a dialect that let a
    client's cosmetic model string select an engine would have handed an external
    caller a provider-routing control the owner never gave it.
    """
    refusal, client, client_id = _admit(request, ROUTE_SPEECH)
    if refusal is not None:
        return refusal
    limited = _rate_refusal(ROUTE_SPEECH, client, client_id)
    if limited is not None:
        return limited
    body = await _read_json(request)
    if body is None:
        return openai_error("Invalid JSON body.", code="invalid_json", status=400)

    text = body.get("input")
    if not isinstance(text, str) or not text.strip():
        return openai_error("'input' is required.", code="missing_input", status=400)
    text = text.strip()

    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)

    voice = body.get("voice")
    params = resolve_voice(voice if isinstance(voice, str) else "")
    if params is None:
        audit(
            OPENAI_SURFACE,
            route=ROUTE_SPEECH,
            status=503,
            client_id=client_id,
            refused="no bound voice",
        )
        return openai_error(
            "No text-to-speech voice is selected. Choose one in Settings -> Models.",
            code="no_bound_voice",
            type_="server_error",
            status=503,
        )

    from personalclaw.voice_reply import streaming_voice_reply

    chunks: list[bytes] = []
    try:
        async for _idx, _sentence, wav in streaming_voice_reply(
            params["provider"],
            text,
            voice=params["voice"],
            speed=params["speed"],
            speech_voice=params["speech_voice"],
        ):
            chunks.append(wav)
    except Exception as exc:  # noqa: BLE001 — a synthesis fault is a 502, not a 500 page
        logger.warning("openai dialect: synthesis failed", exc_info=True)
        audit(
            OPENAI_SURFACE,
            route=ROUTE_SPEECH,
            status=502,
            client_id=client_id,
            refused="synthesis failed",
        )
        return openai_error(
            f"Speech synthesis failed: {exc}",
            code="synthesis_failed",
            type_="server_error",
            status=502,
        )

    audio = await _stitch(chunks)
    if not audio:
        return openai_error(
            "Speech synthesis produced no audio.",
            code="synthesis_empty",
            type_="server_error",
            status=502,
        )
    audit(
        OPENAI_SURFACE,
        route=ROUTE_SPEECH,
        status=200,
        client_id=client_id,
        bytes_out=len(audio),
    )
    return web.Response(
        body=audio,
        content_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


async def _stitch(chunks: list[bytes]) -> bytes:
    """One WAV from the sentence-chunk stream, via the shared stitcher.

    Falls back to the first chunk rather than concatenating raw bytes on failure:
    concatenated WAVs play as one sentence followed by silence, which is a
    plausible-sounding wrong answer.

    ``async`` because ``stitch_wavs`` is — and that is not a detail. Calling it
    synchronously returns a coroutine, which is truthy, so ``out_path or ""`` would
    have passed the guard below and failed inside ``open()``; the broad except would
    then have swallowed it and returned the FIRST SENTENCE ONLY, as a 200 with valid
    audio. mypy caught it here. A multi-sentence reply that plays back truncated is
    exactly the plausible-sounding wrong answer this function's fallback exists to
    avoid, so the fallback must not be reachable by a bug in its own happy path.
    """
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]
    import os
    import tempfile

    paths: list[str] = []
    out_path = ""
    try:
        for chunk in chunks:
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            with open(path, "wb") as handle:
                handle.write(chunk)
            paths.append(path)
        from personalclaw.voice_reply import stitch_wavs

        out_path = await stitch_wavs(paths) or ""
        if out_path:
            with open(out_path, "rb") as handle:
                return handle.read()
    except Exception:  # noqa: BLE001
        logger.warning("openai dialect: stitching failed; returning the first chunk", exc_info=True)
    finally:
        for path in [*paths, out_path]:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    return chunks[0]


async def handle_transcriptions(request: web.Request) -> web.StreamResponse:
    """POST /v1/audio/transcriptions — a thin alias over the bound STT provider.

    ``model`` is read and discarded for the same reason as ``/v1/audio/speech``.
    """
    refusal, client, client_id = _admit(request, ROUTE_TRANSCRIPTIONS)
    if refusal is not None:
        return refusal
    limited = _rate_refusal(ROUTE_TRANSCRIPTIONS, client, client_id)
    if limited is not None:
        return limited

    from personalclaw.transcribe import is_available, transcribe_audio

    if not await is_available():
        audit(
            OPENAI_SURFACE,
            route=ROUTE_TRANSCRIPTIONS,
            status=503,
            client_id=client_id,
            refused="stt unavailable",
        )
        return openai_error(
            "Speech-to-text is not available. Install a transcription model in "
            "Settings -> Models.",
            code="stt_unavailable",
            type_="server_error",
            status=503,
        )

    ctype = request.headers.get("Content-Type", "")
    if not ctype.lower().startswith("multipart/"):
        return openai_error(
            "multipart/form-data with a 'file' field is required.",
            code="invalid_content_type",
            status=400,
        )
    try:
        reader = await request.multipart()
    except (ValueError, AssertionError, RuntimeError) as exc:
        return openai_error(f"Failed to parse the upload: {exc}", code="invalid_upload", status=400)

    import os
    import tempfile

    limit = AUDIO_UPLOAD_CAP_BYTES
    saved = ""
    try:
        while True:
            field = await reader.next()
            if field is None:
                break
            # The wire calls it `file`; `audio` is accepted because the dashboard's own
            # route uses that name and a caller reading either doc should work.
            if getattr(field, "name", "") not in ("file", "audio"):
                continue
            fname = getattr(field, "filename", None) or "audio.webm"
            ext = os.path.splitext(fname)[1] or ".webm"
            fd, saved = tempfile.mkstemp(suffix=ext)
            os.close(fd)
            size = 0
            with open(saved, "wb") as handle:
                while True:
                    chunk = await field.read_chunk(8192)  # type: ignore[union-attr]
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        audit(
                            OPENAI_SURFACE,
                            route=ROUTE_TRANSCRIPTIONS,
                            status=413,
                            client_id=client_id,
                            refused="upload too large",
                        )
                        return openai_error(
                            f"The upload exceeds the {limit} byte limit for this surface.",
                            code="request_too_large",
                            status=413,
                        )
                    handle.write(chunk)
            break
        if not saved:
            return openai_error("Missing 'file' field.", code="missing_file", status=400)

        transcript = await transcribe_audio(saved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("openai dialect: transcription failed", exc_info=True)
        return openai_error(
            f"Transcription failed: {exc}",
            code="transcription_failed",
            type_="server_error",
            status=502,
        )
    finally:
        if saved:
            try:
                os.unlink(saved)
            except OSError:
                pass

    text = transcript or ""
    if text:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        text, _ = redact_exfiltration_urls(text)
        text, _ = redact_credentials(text)
    audit(
        OPENAI_SURFACE,
        route=ROUTE_TRANSCRIPTIONS,
        status=200,
        client_id=client_id,
        bytes_out=len(text or ""),
    )
    return web.json_response({"text": text or ""}, headers={"Cache-Control": "no-store"})


async def handle_voices(request: web.Request) -> web.StreamResponse:
    """GET /v1/audio/voices — what the BOUND provider offers. Not a catalog.

    Not part of the `/v1` wire standard; §2.2 adds it because a client that cannot
    discover the local voices can only guess at the `voice` field.
    """
    refusal, client, client_id = _admit(request, ROUTE_VOICES)
    if refusal is not None:
        return refusal
    params = resolve_voice()
    voices: list[dict[str, str]] = []
    if params is not None:
        provider = params.get("provider")
        active = str(params.get("voice", "") or "")
        listed: list[str] = []
        with _quiet():
            lister = getattr(provider, "list_models", None)
            if callable(lister):
                result = lister()
                if asyncio.iscoroutine(result):
                    result = await result
                for entry in result or []:
                    name = (
                        entry if isinstance(entry, str) else str(getattr(entry, "name", "") or "")
                    )
                    if name:
                        listed.append(name)
        if not listed and active:
            listed = [active]
        voices = [{"id": name, "object": "voice"} for name in dict.fromkeys(listed)]
    audit(OPENAI_SURFACE, route=ROUTE_VOICES, status=200, client_id=client_id)
    return web.json_response(
        {"object": "list", "data": voices}, headers={"Cache-Control": "no-store"}
    )


# ── Plumbing ──────────────────────────────────────────────────────────────────


async def _read_json(request: web.Request) -> dict | None:
    """The request body as an object, or None. Capped per §1.3."""
    from personalclaw.inbound.caps import DEFAULT_CAPS

    limit = int(getattr(DEFAULT_CAPS, "body_bytes", 0) or 64 * 1024)
    try:
        raw = await request.content.read(limit + 1)
    except Exception:  # noqa: BLE001
        return None
    if len(raw) > limit:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def register_routes(app: web.Application, *, turn_runner: Any) -> None:
    """Mount `/v1/*`. ``turn_runner`` is INJECTED, never imported.

    Registered UNCONDITIONALLY and refusing per request, like the capture proxy and
    unlike ``mcp_http.mount``: a mount-time gate freezes the decision at startup, so
    enabling this surface in Settings would need a gateway restart and disabling it
    would leave the route live. A disabled surface answers 404 either way, so nothing
    is disclosed by the route existing.

    Every path is fully literal, so no ``{...}`` pattern in ``dashboard/server.py``
    can shadow them.

    **Why ``turn_runner`` is a parameter.** Running a turn means calling
    ``chat_handlers._run_chat_scoped``, and importing it here would be an
    ``inbound/`` → ``dashboard/`` edge — the ``core-must-not-import-the-http-surface``
    inversion, which `scripts/gate_report.py` caught on the first draft of this module.
    The gate's rationale is the real argument, not the rule: a domain module that
    imports a handler can no longer be exercised without standing up the web app, which
    is how a feature ends up reachable through exactly one route and invisible to the
    CLI and the harness. So the composition root (``dashboard/server.py``, which
    legitimately faces downward) hands the callable in, and the dependency points the
    right way. Required keyword rather than an optional one with a fallback import: an
    optional injection point is one that silently stops being used.
    """
    app[TURN_RUNNER_KEY] = turn_runner
    app.router.add_post(ROUTE_CHAT, handle_chat_completions)
    app.router.add_get(ROUTE_MODELS, handle_models)
    app.router.add_post(ROUTE_SPEECH, handle_speech)
    app.router.add_post(ROUTE_TRANSCRIPTIONS, handle_transcriptions)
    app.router.add_get(ROUTE_VOICES, handle_voices)
