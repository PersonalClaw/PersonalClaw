"""Headless one-shot CLI turn — ``personalclaw run`` (EXTERNAL-ACCESS §9.5).

The CLI face of the inbound-access story the rest of that plan builds over HTTP: a
non-interactive caller runs ONE agent turn and consumes structured output. It is a
*client*, not a second engine — the turn runs inside the gateway through the same
``POST /api/chat`` + ``/api/ws`` pair the dashboard drives, so there is exactly one
turn path and one stream contract.

Why not extend ``personalclaw chat -m``: that command talks to a provider factory
directly (``cli_chat._chat``), which means no gateway, no session store, no safety
profile, no tool-approval gate and no spend attribution. It is a provider smoke test.
A scripted/CI caller needs the gated path, and §9.5 says so explicitly ("executes one
turn against the local gateway"). ``chat -m`` keeps its behaviour; ``run`` is the
gated sibling, and the two are told apart in ``docs/reference/cli.md``.

Safety posture (fail-CLOSED, and the reason this module exists at all):

* The session key is ``inbound:cli:<...>``, which ``guardrails.policy`` classifies as
  unattended, so the run resolves through the ``HEADLESS`` SafetyProfile by
  construction rather than by anything this module remembers to pass.
* Read-only default is enforced by the session's TASK MODE (``ask``), not by
  ``SafetyProfile.tool_grants``. That field has no enforcement point anywhere in the
  tree today (see ``guardrails/policy.py``'s module docstring: it lands "when that
  engine lands and consumes ``tool_grants``"), so trusting it would have shipped a
  read-only promise that denies nothing. ``task_mode_denies`` is deny-by-default,
  runs BEFORE the approval gate, and is documented as un-bypassable by Trust/YOLO —
  it is the only read-only posture in this codebase that actually holds.
* ``--allow`` is the explicit write grant (task mode ``agent``), printed to stderr at
  start so a script is self-documenting about the posture it asked for.
* ``run`` never sets an approval mode. ``HEADLESS`` resolves to ``HOOK_BASED``, whose
  fall-through in ``llm_helpers._resolve_permission`` is auto-approve — so approval
  alone is NOT a containment boundary here, and the task-mode gate is what contains.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

#: Session-key prefix for a headless CLI turn. ``guardrails.policy`` classifies the
#: ``inbound:`` family as unattended, so this prefix is what makes the run HEADLESS.
CLI_SESSION_PREFIX = "inbound:cli:"

#: SpendMeter run scope for CLI turns (§9.5 "budgets ride SpendMeter scope_key=cli").
#: The meter's real parameter is ``run_key``, not ``scope_key`` — see the DEVIATION note
#: in the plan's execution log.
CLI_RUN_KEY = "cli"

VALID_FORMATS = ("plain", "json", "streaming-json")

#: How long to wait for a transient gateway to print its ``PERSONALCLAW_READY:`` line.
_BOOT_TIMEOUT_SECS = 90.0

#: Default ceiling on one headless turn. Overridable with ``--timeout``.
_DEFAULT_TURN_TIMEOUT_SECS = 600.0

#: TTL for the token minted for one CLI invocation. Short on purpose: a headless run is
#: seconds-to-minutes, and ``generate_token`` evicts the oldest of five concurrent
#: nonces — a long-lived CLI token would push the operator's browser session out.
_TOKEN_TTL_SECS = 3600


class RunError(Exception):
    """A headless run could not be set up or completed. Message is user-facing."""


# ── Gateway discovery / bootstrap ────────────────────────────────────────────────


#: Per-attempt timeout and attempt count for the liveness probe.
#:
#: 🔴 Both numbers are load-bearing and were MEASURED, not chosen. At the 2s single-shot
#: this probe first shipped with, a gateway that was alive and serving answered
#: ``/api/healthz`` in >2s while it was busy — three consecutive probes read False,
#: then the fourth returned True in 0.67s. Misreading a live gateway as absent is not a
#: cosmetic error here: ``run`` responds by booting a SECOND gateway on the same home,
#: and because ``.local_secret`` is a per-process random value written to one shared
#: path, the newcomer overwrites it and the ORIGINAL gateway can no longer mint a token
#: — breaking ``personalclaw token`` and this command for the operator's real gateway.
#: So the probe must be biased hard toward "present": slow-but-alive has to win.
_PROBE_TIMEOUT_SECS = 10.0
_PROBE_ATTEMPTS = 3


def probe_gateway(
    port: int, *, timeout: float = _PROBE_TIMEOUT_SECS, attempts: int = _PROBE_ATTEMPTS
) -> bool:
    """True when a gateway answers on ``port``.

    Probes ``/api/healthz``, which is in ``token_auth._BYPASS_EXACT`` and so answers
    without a token. ``doctor`` and ``status`` both probe ``/api/status`` instead and
    then have to read 401/403 as "up" — an auth-gated liveness check needing a
    treat-the-error-as-success branch. This is the same readiness question asked at the
    route that exists to answer it; nothing here reads a 401 as alive.

    Retries because a timeout is ambiguous (absent vs. busy) while a refused connection
    is not. Only a *connection* failure short-circuits to False; a timeout is retried,
    because concluding "absent" from a slow answer is the expensive mistake.
    """
    for attempt in range(max(1, attempts)):
        try:
            with urllib.request.urlopen(  # noqa: S310 — fixed loopback scheme
                urllib.request.Request(f"http://127.0.0.1:{port}/api/healthz"), timeout=timeout
            ) as resp:
                return 200 <= int(resp.status) < 300
        except urllib.error.HTTPError:
            # Answering at all means a server is bound. A non-2xx healthz is a gateway in
            # trouble, not an absent one — reusing it beats booting a second one on top.
            return True
        except (urllib.error.URLError, OSError) as exc:
            # ConnectionRefusedError means nothing is listening — a definite answer, so
            # stop early rather than burning the remaining attempts on it. Anything else
            # (a timeout) is ambiguous and gets another try.
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ConnectionRefusedError):
                return False
            if attempt == attempts - 1:
                return False
    return False  # pragma: no cover — the loop always returns


def mint_local_token(port: int, *, timeout: float = 5.0) -> str:
    """Mint a dashboard token for a gateway already running on ``port``.

    Same handshake as ``personalclaw token``: read ``$PERSONALCLAW_HOME/.local_secret``
    and present it as ``X-Local-Secret`` to the loopback-only ``/api/token/local``. This
    is why ``run`` cannot drive a gateway whose home it does not share — and that is the
    correct limit, not a gap: the secret IS the proof that the caller owns the home.
    """
    from personalclaw.config.loader import config_dir

    secret_path = config_dir() / ".local_secret"
    try:
        secret = secret_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RunError(
            f"cannot read {secret_path} — a gateway is running on port {port} but this "
            f"process does not share its PERSONALCLAW_HOME, so no token can be minted. "
            f"Set PERSONALCLAW_HOME to that gateway's home, or stop it and let `run` "
            f"start its own."
        ) from exc
    if not secret:
        raise RunError(f"{secret_path} is empty — cannot mint a token.")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/token/local?ttl={_TOKEN_TTL_SECS}",
        headers={"X-Local-Secret": secret},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            token = str(json.loads(resp.read()).get("token", ""))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RunError(f"token mint failed against port {port}: {exc}") from exc
    if not token:
        raise RunError(f"gateway on port {port} returned an empty token.")
    return token


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
    gateway running behind the operator's back.
    """
    cmd = [
        sys.executable,
        "-m",
        "personalclaw",
        "gateway",
        "--port",
        "auto",
        "--no-open",
        "--json-ready",
    ]
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ},
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


def acp_readonly_refusal(agent: str) -> str:
    """A refusal message when read-only cannot be enforced for ``agent``, else ``""``.

    🔴 The read-only rail does NOT hold for an ACP-backed agent, and this refuses rather
    than pretending otherwise. Three facts compose into the hole:

    1. ``SessionManager.set_task_mode``'s own docstring: "ACP runtimes are gated in the
       dashboard permission handler instead (they have no such setter)" — so an ACP
       runtime never receives the task mode.
    2. That dashboard-side gate (``chat_runner``'s ``task_mode_denies`` call) fires only
       on an ``EVENT_PERMISSION_REQUEST`` frame.
    3. An unattended turn — which every ``inbound:cli:`` turn is, by construction —
       makes ``chat_runner`` set ``acp_mode = "bypassPermissions"``, whose entire purpose
       is to stop the dialect asking so a background run cannot wedge on a human.

    So the one gate that could enforce read-only on an ACP runtime is the one the
    unattended posture switches off. Announcing "read-only" and then running an ACP
    agent with permissions bypassed would be a worse defect than not offering the mode:
    the operator would have a written promise and no enforcement. Suppressing the bypass
    instead is not an option — the turn would hang forever waiting for an approval no
    human is there to give.

    Fail-closed: refuse, and name ``--allow`` as the explicit way to proceed. ``--allow``
    is honest about what an ACP headless turn actually is (a full grant), so the operator
    opts into the posture they are really getting instead of inheriting it silently.
    """
    try:
        from personalclaw.config import AppConfig
        from personalclaw.config.loader import resolve_agent_bindings

        cfg = AppConfig.load()
        kind = getattr(resolve_agent_bindings(cfg, agent or None), "provider", "") or ""
    except Exception:  # noqa: BLE001 — an unresolvable binding is not this gate's call
        return ""
    if not kind.startswith("acp"):
        return ""
    return (
        f"personalclaw run: refusing a read-only headless turn on ACP-backed agent "
        f"{agent or '(default)'} (runtime {kind!r}).\n"
        f"  WHY: an unattended ACP turn runs with permissions bypassed, so the task-mode "
        f"gate that enforces read-only never sees a tool call. The rail cannot hold, and "
        f"announcing it anyway would be a promise with no enforcement.\n"
        f"  FIX: pass --allow to run with an explicit full grant, or --agent <name> to "
        f"pick a native-runtime agent, where read-only IS enforced before approval."
    )


def grant_notice(session_key: str, task_mode: str) -> str:
    """The stderr posture line. Printed for BOTH modes, not only for ``--allow``.

    §9.5 asks for the write grant to be printed "so scripts are self-documenting".
    Printing only the grant would make the read-only default the silent case — the one
    a reader cannot distinguish from "no posture was applied at all". Announcing both
    means the absence of this line is itself a signal.
    """
    if task_mode == "agent":
        return (
            f"personalclaw run: WRITE GRANT active (--allow) — session {session_key} runs "
            f"with full tool access under the headless safety profile."
        )
    return (
        f"personalclaw run: read-only — session {session_key} denies every non-read-only "
        f"tool. Pass --allow to grant writes."
    )


# ── HTTP helpers (loopback, token-authenticated) ─────────────────────────────────


def _authed(path: str, token: str) -> str:
    """Append ``token=`` to ``path``'s query string.

    The token MUST ride the query string. ``token_auth`` reads primary owner auth from
    ``?token=`` or the ``pc_token_<port>`` cookie ONLY — its ``Authorization: Bearer``
    branch is the app-token NARROWING path (it adopts an ``app`` claim for an
    already-authenticated owner) and never authenticates on its own. Measured: sending
    the readiness token as a Bearer header returned ``403 {"error": "Token required"}``.
    """
    return f"{path}{'&' if '?' in path else '?'}token={token}"


def _api(port: int, token: str, path: str, body: dict | None = None) -> dict:
    """One loopback API call. Returns the decoded JSON object."""
    data = json.dumps(body or {}).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{_authed(path, token)}",
        data=data,
        method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
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
    """Accumulates the WS frames of one turn into the §9.5 ``json`` document."""

    def __init__(self, session_key: str, fmt: str) -> None:
        self.session_key = session_key
        self.fmt = fmt
        self.text_parts: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.done = False

    def feed(self, envelope: dict) -> None:
        """Consume one ``{"type", "data"}`` envelope. Ignores other sessions' frames."""
        kind = str(envelope.get("type", ""))
        data = envelope.get("data")
        if not isinstance(data, dict) or data.get("session") != self.session_key:
            return
        if self.fmt == "streaming-json":
            # NDJSON of the SAME envelopes the dashboard consumes — one stream contract.
            # Emitted only for the three §9.5 frame kinds so the contract is a promise
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
            # A denied tool is reported as a tool_result carrying the deny reason; mark
            # the matching call not-ok so `tool_calls[].ok` is a measured field rather
            # than a constant True (which would make the json doc's `ok` decorative).
            out = str(data.get("output", ""))
            if self.tool_calls and ("mode —" in out or "denied" in out.lower()):
                self.tool_calls[-1]["ok"] = False
        elif kind == "chat_message" and str(data.get("role", "")) == "error":
            self.errors.append(str(data.get("content", "")))
        elif kind == "chat_done":
            self.done = True

    def result_text(self) -> str:
        return "".join(self.text_parts).strip()


async def _consume(
    port: int, token: str, collector: _Collector, prompt: str, timeout: float
) -> None:
    """Open the WS, POST the turn, and consume frames until ``chat_done``.

    The WS is opened BEFORE the POST: ``POST /api/chat?ws=1`` returns as soon as the
    turn task is created, so a reader attached afterwards races the first chunk. No
    ``Origin`` header is sent — ``dashboard.origin.check_origin`` trusts a loopback peer
    that sends none, and sending a wrong one is a 403.
    """
    import aiohttp

    base = f"http://127.0.0.1:{port}"
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(base + _authed("/api/ws", token)) as ws:
            resp = await http.post(
                base + _authed("/api/chat?ws=1", token),
                json={"message": prompt, "session": collector.session_key},
            )
            async with resp:
                if resp.status != 200:
                    raise RunError(f"POST /api/chat failed: HTTP {resp.status} {await resp.text()}")
            deadline = time.monotonic() + timeout
            while not collector.done:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RunError(f"turn did not finish within {timeout:.0f}s")
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                except TimeoutError as exc:
                    raise RunError(f"turn did not finish within {timeout:.0f}s") from exc
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
                    raise RunError("gateway closed the websocket before the turn finished")


def _token_total(session_key: str) -> int:
    """Total tokens this session billed, from the usage ledger. 0 when unreadable.

    No WS frame carries token counts, so the count comes from the ledger the gateway
    writes under ``config_dir()/usage/turns.jsonl`` — readable here precisely because
    ``run`` requires sharing the gateway's home (see ``mint_local_token``). Best-effort:
    a missing ledger reports 0 rather than failing a turn that already succeeded.

    🔴 The ledger keys rows by the DASHBOARD-WRAPPED provider key
    (``dashboard:inbound:cli:<id>``), not by the session name. Querying the bare key
    matched nothing and reported a confident ``"tokens": 0`` on a turn that had really
    billed 22,979 — a decorative field, not a measured one. ``dashboard_session_key`` is
    the shared wrapper, so the query cannot drift from the write site. It is imported from
    ``constants``, NOT from ``dashboard.chat_utils``: reaching up into the HTTP surface for
    a naming rule is the inversion ``core-must-not-import-the-http-surface`` exists to
    catch, and the gate caught it here.
    """
    try:
        from personalclaw import usage_ledger
        from personalclaw.constants import dashboard_session_key

        agg = usage_ledger.totals(session_key=dashboard_session_key(session_key))
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
    if task_mode == "ask":
        refusal = acp_readonly_refusal(getattr(args, "agent", "") or "")
        if refusal:
            print(refusal, file=sys.stderr)
            return 2
    print(grant_notice(session_key, task_mode), file=sys.stderr, flush=True)

    from personalclaw.cli_server import resolve_client_port

    port = resolve_client_port(getattr(args, "port", None))
    transient: subprocess.Popen | None = None
    started = time.monotonic()
    try:
        if probe_gateway(port):
            token = mint_local_token(port)
        else:
            print(
                f"personalclaw run: no gateway on port {port} — starting a transient one.",
                file=sys.stderr,
                flush=True,
            )
            port, token, transient = start_transient_gateway()

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
        # session's posture AND the runtime's, via `set_task_mode`). Measured: creating
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

        collector = _Collector(session_key, fmt)
        timeout = float(getattr(args, "timeout", 0) or _DEFAULT_TURN_TIMEOUT_SECS)
        asyncio.run(_consume(port, token, collector, prompt, timeout))
    except RunError as exc:
        print(f"personalclaw run: {exc}", file=sys.stderr)
        return 1
    finally:
        _shutdown_transient(transient)

    duration_ms = int((time.monotonic() - started) * 1000)
    ok = not collector.errors
    if fmt == "json":
        print(
            json.dumps(
                {
                    "result": collector.result_text(),
                    "session": session_key,
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

    for err in collector.errors:
        print(f"personalclaw run: turn failed: {err}", file=sys.stderr)
    return 0 if ok else 1


def _run(args) -> None:
    """``personalclaw run`` entry point — dispatched from ``cli.main``."""
    raise SystemExit(_run_one(args))
