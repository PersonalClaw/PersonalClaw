"""The external-agent capture proxy — TRANSPORT half (EXTERNAL-ACCESS §7.1).

Two loopback-only routes another agent on this machine points its API base URL at:

* ``POST /capture/v1/chat/completions`` — the OpenAI dialect
* ``POST /capture/v1/messages``        — the Anthropic dialect

The agent sets ``OPENAI_BASE_URL=http://127.0.0.1:10000/capture/v1`` and puts the
**capture surface bearer** (§1.1) in ``OPENAI_API_KEY``. Two consequences fall out of
that single choice, and both are the point:

1. A misconfigured agent fails LOUD (401 from us) rather than open — it cannot reach
   any upstream at all without the token we minted.
2. The user's real upstream key never appears in the external agent's config. We hold
   it; the agent holds a revocable local credential. That is a strict improvement over
   pasting a provider key into every agent's dotfile.

Three rails are load-bearing here and each one is placed where it is on purpose:

**Loopback, always.** Capture never consults ``allow_remote``. It is not a data read —
it proxies the user's paid upstream credential and records full prompts, so a remote
caller reaching it is both a billing compromise and a transcript disclosure. The check
runs before any config read so no combination of settings can widen it. (The control
bridge earned the same exception at `auth.peer_allowed`; this surface takes it here
rather than there because `peer_allowed`'s remote branch is about *declared public
URLs*, a concept capture has no use for.)

**The egress guard pre-flights, before any connection.** ``net.fetch``'s buffered read
is byte-capped and cannot stream SSE, so this module dials upstream itself — which
means it also inherits the obligation `web/render.py` spells out for the headless
browser: a client that bypasses ``net.fetch`` must run ``guard.evaluate`` at its own
layer, before it opens anything. :func:`_forward` is the SOLE place a socket is opened
and the guard runs strictly before it, so a denied host is never dialed. The policy is
`net.policy.LISTED` (``allow_only=True``), which makes the operator's allow-list
**exclusive** — and therefore makes an EMPTY list refuse every host. That direction is
deliberate: for an egress allow-list, "nothing named yet" must mean "nowhere to go",
not "anywhere". A permissive empty list would turn this route into an open relay that
spends the operator's credential on any host a local agent names.

**Recording is off the hot path.** The response reaches the caller FIRST; the turn
record is handed to a background task afterwards. Sync storage inside the proxy loop is
the named anti-pattern (§7.1 "latency honesty") — it makes every captured turn slower
than an un-captured one, which is how a recording feature gets turned off. A recorder
that raises, or a `capture_store` that has not landed yet, degrades to a logged warning:
the caller always gets its bytes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aiohttp import web

from personalclaw.inbound import auth
from personalclaw.inbound.audit import audit
from personalclaw.inbound.gate import admission_problem
from personalclaw.net.guard import evaluate
from personalclaw.net.policy import LISTED, EgressPolicy

logger = logging.getLogger(__name__)

#: The surface string, already a member of ``config.loader.EXTERNAL_ACCESS_SURFACES``.
#: Named rather than spelled inline for the same reason `auth.BRIDGE_SURFACE` is: this
#: module's whole behavioural exception (loopback forever) is keyed on it, and an
#: exception keyed on a bare literal is one rename away from silently not applying.
CAPTURE_SURFACE = "capture"

DIALECT_OPENAI = "openai"
DIALECT_ANTHROPIC = "anthropic"

ROUTE_OPENAI = "/capture/v1/chat/completions"
ROUTE_ANTHROPIC = "/capture/v1/messages"

#: Passthrough mode (§7.1): an agent PClaw has no ProviderEntry for supplies its OWN
#: upstream key here. A *second* header rather than reusing ``Authorization`` because
#: that slot already carries the capture bearer — one header cannot be two credentials,
#: and overloading it is how a surface token ends up forwarded to a third party.
UPSTREAM_KEY_HEADER = "X-PClaw-Upstream-Key"
UPSTREAM_BASE_HEADER = "X-PClaw-Upstream-Base-Url"

#: Well-known bases used only when nothing else names one. Still guard-evaluated
#: against the operator's allow-list, so a default is a convenience, never a waiver.
_DIALECT_DEFAULT_BASE = {
    DIALECT_OPENAI: "https://api.openai.com/v1",
    DIALECT_ANTHROPIC: "https://api.anthropic.com/v1",
}

#: Response headers we must NOT copy back. ``content-length``/``transfer-encoding``
#: describe a framing we are re-doing, and aiohttp has already decoded
#: ``content-encoding`` by the time we read the body — echoing it would tell the caller
#: to gunzip plaintext.
_DROP_RESPONSE_HEADERS = frozenset(
    {
        "content-length",
        "content-encoding",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "server",
        "date",
    }
)

_DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


def _json(payload: dict, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


# ── Configuration reads ───────────────────────────────────────────────────────


def upstream_allowlist(cfg: Any) -> tuple[str, ...]:
    """The operator's allow-list of upstream hosts, or ``()``.

    Read TOLERANTLY across two spellings on purpose. The nested form
    (``external_access.capture.upstream_allowlist``) is what §7.1 describes; the flat
    form (``external_access.capture_upstream_allowlist``) is the convention the
    neighbouring ``capture_retention_days`` already follows. Accepting either costs one
    ``getattr`` and removes a whole class of "the field landed under the other name and
    the proxy silently saw an empty list" — which, because the empty list REFUSES, would
    present as a total outage rather than as a config typo.

    Every failure path returns ``()``, i.e. deny-everything. There is no branch here
    that can widen egress.
    """
    ext = getattr(cfg, "external_access", None)
    if ext is None:
        return ()
    raw: Any = None
    surface = getattr(ext, CAPTURE_SURFACE, None)
    if surface is not None:
        raw = getattr(surface, "upstream_allowlist", None)
    if raw is None:
        raw = getattr(ext, "capture_upstream_allowlist", None)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(h).strip().lower() for h in raw if str(h).strip())


def capture_policy(cfg: Any) -> EgressPolicy:
    """The egress posture for a capture forward: EXCLUSIVELY the allow-listed hosts.

    Built from `LISTED` (``allow_only=True``) rather than from `STRICT` plus a
    hand-rolled host check. STRICT's ``allow_hosts`` is ADDITIVE — it merely waives the
    private-range block — so a STRICT-based policy would have reached every public host
    regardless of the list, and the allow-list would have been decorative. `LISTED`
    inverts that to "only these", which is the only reading under which an empty list
    means what it must mean here.
    """
    return LISTED.with_overrides(name="capture", allow_hosts=upstream_allowlist(cfg))


# ── Admission ─────────────────────────────────────────────────────────────────


def _admit(request: web.Request, route: str) -> tuple[web.Response | None, str, str]:
    """Every gate, in the order that leaks least. Returns ``(refusal, reason, client_id)``.

    Order and its rationale:

    1. **Surface admission → 404.** A disabled surface must not confirm its own
       existence to a prober, and it must refuse before the request body is ever read —
       an off surface that consumes a prompt has already handled the data it was turned
       off to stop handling.
    2. **Peer → 403.** Loopback only, unconditionally. ``allow_remote`` is never read.
    3. **Bearer → 401.** Accepted from the surface token (the documented path — it is
       what sits in ``OPENAI_API_KEY``) or from a registered per-client token bound to
       ``capture``. A matched client record is what gives the turn an attributable
       ``client_id`` and names its ``upstream``; the surface token alone is anonymous.

    Both bearer comparisons are constant-time (`auth.verify_bearer` /
    `clients.lookup_by_token`), and the client lookup runs even when the surface token
    already matched, so the two paths cannot be told apart by timing.
    """
    problem, status = admission_problem(CAPTURE_SURFACE)
    if problem:
        audit(CAPTURE_SURFACE, route=route, status=status, refused=problem)
        return _json({"error": "not available"}, status=status), problem, ""

    # §7.1: loopback-only, ALWAYS. Checked here and not via `peer_allowed` because
    # `peer_allowed`'s non-loopback branch exists to honour a declared public URL, and
    # capture has no remote mode for a public URL to describe. Reading `allow_remote`
    # at all would be the bug — so this function never does.
    if not auth.is_loopback(request):
        why = "the capture proxy is loopback-only by construction"
        audit(CAPTURE_SURFACE, route=route, status=403, refused=why)
        return _json({"error": "forbidden"}, status=403), why, ""

    presented = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    surface_ok = auth.verify_bearer(CAPTURE_SURFACE, presented)
    client, _why = _lookup_client(presented)
    if not surface_ok and client is None:
        audit(CAPTURE_SURFACE, route=route, status=401, refused="bad bearer")
        return _json({"error": "unauthorized"}, status=401), "bad bearer", ""
    return None, "", getattr(client, "client_id", "") or CAPTURE_SURFACE


def _lookup_client(presented: str) -> tuple[Any | None, str]:
    """The registered client behind ``presented``, or ``(None, reason)``.

    Wrapped because the registry is optional here: the documented setup is the surface
    token alone, and a client record is an *upgrade* (attribution + a pinned upstream),
    not a precondition. A registry fault must therefore not deny a request the surface
    token already admitted.
    """
    if not presented:
        return None, "no bearer token presented"
    try:
        from personalclaw.inbound.clients import lookup_by_token

        return lookup_by_token(presented, CAPTURE_SURFACE)
    except Exception:  # noqa: BLE001 — an unreadable registry means "no client record"
        logger.debug("capture: client lookup failed", exc_info=True)
        return None, "client registry unreadable"


# ── Upstream resolution ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Upstream:
    """Where this turn goes and what it is authorised with."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    mode: str = "provider"  # "provider" | "passthrough"
    provider: str = ""


def _client_upstream_name(client: Any) -> str:
    """The ProviderEntry name a client record pins, across both storage shapes.

    ``InboundClient`` may carry ``upstream`` as its own field or inside the generic
    ``scope`` bag depending on which lands first; this module owns neither, so it reads
    both rather than betting on one. An unset value is "" — which means "no pinned
    upstream", and the caller then requires passthrough. It never means "pick one".
    """
    if client is None:
        return ""
    direct = str(getattr(client, "upstream", "") or "")
    if direct:
        return direct
    scope = getattr(client, "scope", None)
    if isinstance(scope, dict):
        return str(scope.get("upstream", "") or "")
    return ""


def _join(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _dialect_suffix(dialect: str) -> str:
    return "chat/completions" if dialect == DIALECT_OPENAI else "messages"


def _auth_headers(dialect: str, secret: str, request: web.Request) -> dict[str, str]:
    """The upstream auth headers for ``dialect``.

    Built fresh, never derived from the inbound request's ``Authorization`` — that slot
    holds OUR capture bearer, and forwarding it would hand a PClaw credential to a third
    party while failing to authenticate the call.
    """
    if dialect == DIALECT_ANTHROPIC:
        return {
            "x-api-key": secret,
            "anthropic-version": request.headers.get("anthropic-version")
            or _DEFAULT_ANTHROPIC_VERSION,
        }
    return {"Authorization": f"Bearer {secret}"}


def _resolve_upstream(
    cfg: Any, client: Any, dialect: str, request: web.Request
) -> tuple[_Upstream | None, str]:
    """Resolve base URL + credential for this turn. Returns ``(upstream, refusal)``.

    Passthrough is checked FIRST and is exclusive: when the client supplies its own key,
    the operator's credential is not resolved at all — not resolved-then-unused, never
    read. That is the difference between "we didn't send it" and "we couldn't have".
    """
    passthrough_key = (request.headers.get(UPSTREAM_KEY_HEADER) or "").strip()
    if passthrough_key:
        base = (request.headers.get(UPSTREAM_BASE_HEADER) or "").strip() or (
            _DIALECT_DEFAULT_BASE[dialect]
        )
        return (
            _Upstream(
                url=_join(base, _dialect_suffix(dialect)),
                headers=_auth_headers(dialect, passthrough_key, request),
                mode="passthrough",
                provider="",
            ),
            "",
        )

    name = _client_upstream_name(client)
    if not name:
        return None, (
            "no upstream is bound for this caller: register an inbound client whose "
            f"`upstream` names a config.json provider, or send {UPSTREAM_KEY_HEADER}"
        )
    try:
        base, secret = _provider_upstream(name)
    except Exception as exc:  # noqa: BLE001 — a resolution fault is a 502, not a 500
        logger.debug("capture: upstream %r did not resolve", name, exc_info=True)
        return None, f"upstream provider {name!r} did not resolve: {exc}"
    if not secret:
        return None, f"upstream provider {name!r} has no usable credential configured"
    return (
        _Upstream(
            url=_join(base or _DIALECT_DEFAULT_BASE[dialect], _dialect_suffix(dialect)),
            headers=_auth_headers(dialect, secret, request),
            mode="provider",
            provider=name,
        ),
        "",
    )


def _provider_upstream(name: str) -> tuple[str, str]:
    """``(base_url, secret)`` for the ProviderEntry ``name``, via the SHARED ladder.

    The order is not re-derived here. It is the one
    `sdk.provider_helpers.create_provider`'s registry factory uses, reusing the same two
    functions so this surface cannot drift into a fourth spelling of it:

      1. ``entry.credential`` through the credential store — `_resolve_credential`
      2. ``entry.options["api_key"]`` / ``["apiKey"]``, then the spec's subscription
         source, then ``spec.api_key_env`` — `_resolve_spec_secret`

    and base URL the same way the factory reads it: ``options["base_url"]``, else
    ``options["endpoint"]``, else ``spec.default_base_url``. A private-name import is
    the deliberate trade: importing the ladder is what makes it ONE ladder, and a
    hand-copied re-implementation here is precisely the drift that made a subscription
    provider 401 at first use (see that function's docstring).
    """
    from personalclaw.config import config_dir
    from personalclaw.llm.branded_specs import (
        _resolve_credential,
        _resolve_spec_secret,
        registered_spec,
    )
    from personalclaw.llm.credentials import CredentialStore
    from personalclaw.llm.registry import get_default_registry

    entry = get_default_registry().get_entry(name)
    spec = registered_spec(entry.type)
    options = dict(entry.options or {})
    base = str(options.get("base_url") or options.get("endpoint") or "")
    if not base and spec is not None:
        base = str(spec.default_base_url or "")

    cred = _resolve_credential(
        entry, {"credential_store": CredentialStore(config_dir())}, label=name
    )
    if cred is None and spec is not None:
        explicit = str(options.get("api_key", "") or "") or str(options.get("apiKey", "") or "")
        cred, _reason = _resolve_spec_secret(spec, explicit_key=explicit)
    secret = str(getattr(cred, "secret", "") or "") if cred is not None else ""
    return base, secret


# ── Egress: the one place a socket is opened ──────────────────────────────────


@dataclass
class _UpstreamReply:
    status: int
    headers: dict[str, str]
    body: bytes = b""
    chunks: list[bytes] = field(default_factory=list)


def _response_headers(raw: Any) -> dict[str, str]:
    return {k: v for k, v in raw.items() if k.lower() not in _DROP_RESPONSE_HEADERS}


async def _forward(
    upstream: _Upstream,
    body: bytes,
    *,
    stream: bool,
    timeout_s: float,
    on_chunk: Callable[[bytes], Awaitable[None]] | None = None,
    on_start: Callable[[int, dict[str, str]], Awaitable[None]] | None = None,
) -> _UpstreamReply:
    """Dial upstream and relay. **The only egress point in this module.**

    Kept as one module-level function precisely so the guard's placement is checkable:
    everything above this line is decision-making that touches no network, and the
    guard runs before this is called. A second place that opened a connection would
    make the pre-flight an unverifiable claim.

    ``on_start``/``on_chunk`` let the caller write to its client as bytes arrive, so the
    caller is never waiting on a buffer we chose to fill. Note the pinning gap this
    shares with `web/render.py`: ``decision.pinned_ips`` are resolved but this client
    dials by hostname, so a rebind between evaluate and connect is possible. Same
    trade-off, same layer, stated rather than implied.
    """
    import aiohttp

    headers = {"content-type": "application/json", **upstream.headers}
    timeout = aiohttp.ClientTimeout(
        total=None if stream else timeout_s, sock_connect=timeout_s, sock_read=timeout_s
    )
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(upstream.url, data=body, headers=headers) as resp:
            out = _response_headers(resp.headers)
            reply = _UpstreamReply(status=resp.status, headers=out)
            if on_start is not None:
                await on_start(resp.status, out)
            if not stream or on_chunk is None:
                reply.body = await resp.read()
                return reply
            async for chunk in resp.content.iter_any():
                # Caller first, buffer second — the ordering §7.1 asks for.
                await on_chunk(chunk)
                reply.chunks.append(chunk)
            return reply


# ── Recording, off the hot path ───────────────────────────────────────────────

_pending: set[asyncio.Task] = set()


def _recorder() -> Callable[..., Awaitable[str]] | None:
    """`capture_store.record_turn_async`, or None when it has not landed.

    Imported lazily and tolerantly for the ordering reason §7.1 names in the other
    direction: the transport and the store ship independently, and the transport must
    not be un-runnable because its consumer is one commit behind. A missing store means
    traffic still forwards and one warning is logged — never a 500 for the caller.
    """
    try:
        from personalclaw.inbound.capture_store import record_turn_async

        return record_turn_async
    except Exception:  # noqa: BLE001
        logger.debug("capture: capture_store is unavailable; not recording", exc_info=True)
        return None


async def _record(**kwargs: Any) -> None:
    record = _recorder()
    if record is None:
        logger.warning("capture: recorded nothing — capture_store.record_turn_async is unavailable")
        return
    try:
        await record(**kwargs)
    except Exception:  # noqa: BLE001 — the caller already has its bytes; never re-raise
        logger.warning("capture: turn recording failed", exc_info=True)


def _schedule_record(**kwargs: Any) -> None:
    """Hand recording to a background task and return immediately.

    A strong reference is held in ``_pending`` because a bare `create_task` result is
    only weakly referenced by the loop and may be garbage-collected mid-flight — the
    failure mode is a turn that silently never records, which is indistinguishable from
    the feature being off.
    """
    try:
        task = asyncio.get_running_loop().create_task(_record(**kwargs))
    except RuntimeError:  # pragma: no cover — no loop means no request either
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def drain_recordings() -> None:
    """Await every in-flight recording. For shutdown flush and for tests that assert a
    record eventually landed without asserting *when* — the whole point being that the
    response did not wait for it."""
    while _pending:
        await asyncio.gather(*tuple(_pending), return_exceptions=True)


# ── Handlers ──────────────────────────────────────────────────────────────────


def _wants_stream(body: bytes) -> bool:
    """Whether the caller asked for SSE.

    Parsed for INSPECTION only — the bytes forwarded upstream are the bytes we read, so
    this never touches verbatimness. An unparseable body reads as non-streaming and is
    still forwarded untouched: deciding the dialect's semantics is upstream's job, not
    a reason for us to reject.
    """
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return bool(isinstance(parsed, dict) and parsed.get("stream"))


def _model_of(body: bytes) -> str:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    return str(parsed.get("model", "")) if isinstance(parsed, dict) else ""


async def _handle(request: web.Request, dialect: str, route: str) -> web.StreamResponse:
    refusal, _reason, client_id = _admit(request, route)
    if refusal is not None:
        return refusal

    body = await request.read()
    started = time.monotonic()
    cfg = _load_config()
    client, _ = _lookup_client(
        (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    )
    upstream, why = _resolve_upstream(cfg, client, dialect, request)
    if upstream is None:
        audit(CAPTURE_SURFACE, route=route, status=502, refused=why, client_id=client_id)
        return _json({"error": "upstream unavailable", "reason": why}, status=502)

    # ── Egress pre-flight. Nothing above this line opened a connection, and nothing
    # below it runs on a deny: the guard decides before `_forward` (the sole socket)
    # is reachable. An empty allow-list denies here, which is the fail-closed
    # direction for an egress list.
    policy = capture_policy(cfg)
    decision = evaluate(upstream.url, policy)
    if not decision.allow:
        audit(
            CAPTURE_SURFACE, route=route, status=502, refused=decision.reason, client_id=client_id
        )
        return _json(
            {
                "error": "upstream denied",
                "reason": decision.reason,
                "recovery_hints": list(decision.recovery_hints),
            },
            status=502,
        )

    stream = _wants_stream(body)
    model = _model_of(body)
    sent: web.StreamResponse | None = None

    async def _start(status: int, headers: dict[str, str]) -> None:
        nonlocal sent
        sent = web.StreamResponse(status=status, headers=headers)
        await sent.prepare(request)

    async def _chunk(data: bytes) -> None:
        if sent is not None:
            await sent.write(data)

    try:
        reply = await _forward(
            upstream,
            body,
            stream=stream,
            timeout_s=float(policy.timeout_s),
            on_chunk=_chunk if stream else None,
            on_start=_start if stream else None,
        )
    except Exception as exc:  # noqa: BLE001 — an upstream fault is a 502, not a crash
        logger.warning("capture: upstream call failed", exc_info=True)
        audit(CAPTURE_SURFACE, route=route, status=502, refused=str(exc), client_id=client_id)
        if sent is not None:  # already streaming — the caller keeps what it got
            await sent.write_eof()
            return sent
        return _json({"error": "upstream failed", "reason": str(exc)}, status=502)

    duration_ms = int((time.monotonic() - started) * 1000)
    stream_text = ""
    response_body: Any = None
    if stream and sent is not None:
        await sent.write_eof()  # the caller is DONE before anything is recorded
        stream_text = b"".join(reply.chunks).decode("utf-8", "replace")
        out_bytes = sum(len(c) for c in reply.chunks)
        result: web.StreamResponse = sent
    else:
        response_body = _decode_json(reply.body)
        out_bytes = len(reply.body)
        result = web.Response(
            body=reply.body,
            status=reply.status,
            headers={k: v for k, v in reply.headers.items() if k.lower() != "content-type"},
            content_type=reply.headers.get("Content-Type", "application/json").split(";")[0],
        )

    audit(
        CAPTURE_SURFACE,
        route=route,
        status=reply.status,
        bytes_in=len(body),
        bytes_out=out_bytes,
        duration_ms=duration_ms,
        client_id=client_id,
    )
    _schedule_record(
        client_id=client_id,
        dialect=dialect,
        model_requested=model,
        request_body=_decode_json(body),
        response_body=response_body,
        stream_text=stream_text,
        tokens=None,
        latency_ms=duration_ms,
    )
    return result


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _load_config() -> Any:
    from personalclaw.config.loader import AppConfig

    return AppConfig.load()


async def handle_openai_chat_completions(request: web.Request) -> web.StreamResponse:
    """POST /capture/v1/chat/completions — the OpenAI dialect."""
    return await _handle(request, DIALECT_OPENAI, ROUTE_OPENAI)


async def handle_anthropic_messages(request: web.Request) -> web.StreamResponse:
    """POST /capture/v1/messages — the Anthropic dialect."""
    return await _handle(request, DIALECT_ANTHROPIC, ROUTE_ANTHROPIC)


def register_routes(app: web.Application) -> None:
    """Mount both dialects.

    Registered UNCONDITIONALLY, unlike `mcp_http.mount`'s enablement-gated mount, and
    the difference matters: a mount-time gate freezes the decision at startup, so
    enabling capture in Settings would need a gateway restart and disabling it would
    leave the route live. Per-request `admission_problem` makes the config the truth at
    the moment of the request — and a disabled surface still answers 404 (aiohttp's own
    answer for an unmounted path), so nothing is disclosed by the route existing.

    Both paths are fully literal, so ordering against the `{...}` patterns in
    `dashboard/server.py` cannot shadow them; they are registered early regardless,
    beside the other inbound surfaces and outside the dashboard's cookie-auth world.
    """
    app.router.add_post(ROUTE_OPENAI, handle_openai_chat_completions)
    app.router.add_post(ROUTE_ANTHROPIC, handle_anthropic_messages)
