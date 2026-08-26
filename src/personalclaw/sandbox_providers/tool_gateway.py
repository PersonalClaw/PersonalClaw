"""Sandbox-internal tool gateway: zero listening ports, zero credentials inside (§5.2).

Memoh's answer to "a sandboxed agent still needs tools" is an in-container HTTP proxy on
127.0.0.1. Ours is stricter, and the difference is the whole point of this module:

* **Zero listening ports inside the sandbox.** The transport is the sandbox handle's own exec
  channel — a pipe pair the HOST created before the child existed. The shim (:mod:`pclaw_tool`)
  writes one JSON request line to its request fd and reads one JSON response line back. Nothing
  inside the sandbox can be connected TO: there is no socket to scan and no port-forward to
  misconfigure. A test asserts the shim's source imports no network module at all, because the
  property has to hold by construction rather than by policy.
* **Zero credentials inside.** The host authorises by construction — it created the channel, so
  possession of the fd *is* the authorisation. There is no shared secret to leak because there is
  no network hop to authenticate. The same trust basis as the internal ``X-Internal-Secret`` HTTP
  path (``messaging.py``), minus the secret. Tool results crossing into the sandbox are data;
  anything a tool touched host-side is redacted on the way out (:func:`personalclaw.security.redact`
  wraps the channel writer as defence in depth).
* **Policy at the HOST end.** The shim offers whatever the sandbox spec's safety profile allows
  and the host refuses the rest — a decision the sandbox cannot influence, because it is taken
  after the request arrives and before the tool is looked up. A research-class profile
  (``tool_grants="read"``) is refused every write-class tool.

🔴 **This is the first enforcement point for** ``SafetyProfile.tool_grants``. ``cli_run.py`` says
so plainly of the pre-existing tree: "that field has no enforcement point anywhere in the tree
today … so trusting it would have shipped a read-only promise that denies nothing." Enforcing it
HERE is safe precisely because this surface is new — no existing automation can be broken by a
control that had no call sites. And the classification is not a second dialect: the read/write
question is answered by :func:`personalclaw.task_modes.task_mode_denies`, the deny-by-default
classifier that is already the only read-only posture in this codebase that actually holds.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from personalclaw.guardrails.policy import TOOL_CUSTOM, TOOL_READ, SafetyProfile
from personalclaw.security import redact
from personalclaw.task_modes import task_mode_denies

logger = logging.getLogger(__name__)

#: Wire protocol version. The shim sends it; a mismatch is refused rather than guessed at, because
#: a shim copied into a long-lived sandbox can outlive the host that put it there.
PROTOCOL_VERSION = 1

#: One request/response per line, so the channel needs no framing beyond a newline.
_MAX_LINE_BYTES = 1 << 20

ERR_PROTOCOL = "ERR_TOOL_PROTOCOL"
ERR_UNKNOWN_TOOL = "ERR_TOOL_NOT_ON_SURFACE"
ERR_REFUSED = "ERR_TOOL_CLASS_REFUSED"
ERR_FAILED = "ERR_TOOL_FAILED"

#: ``tool_grants`` → the task mode whose deny rule expresses it. ``read`` maps to ``ask``, which
#: permits read-only tools and denies everything else DENY-BY-DEFAULT; ``read_write`` maps to
#: ``agent``, which permits everything. Mapping instead of re-deriving is deliberate: a second
#: read/write classifier would be a second answer to a question this tree already answers.
_GRANT_TO_TASK_MODE = {TOOL_READ: "ask", "read_write": "agent"}


@dataclass(frozen=True)
class ToolSpec:
    """One tool the gateway can serve, and enough about it to classify the request.

    ``kind`` is a :mod:`personalclaw.task_modes` tool kind (``read``/``search``/``edit``/…) — the
    input to the shared read/write classifier, so a tool's class is declared once, here, and not
    re-guessed at each policy check.
    """

    name: str
    kind: str
    handler: Callable[[dict[str, Any], "ToolContext"], str]
    summary: str = ""


@dataclass(frozen=True)
class ToolContext:
    """What a tool handler is allowed to know: the workspace, and who is asking."""

    workspace: str
    session_key: str
    sandbox: str = "none"


def _memory_recall(args: dict[str, Any], ctx: ToolContext) -> str:
    from personalclaw.memory import MemoryStore

    query = str(args.get("query", "") or "").strip()
    if not query:
        raise ValueError("memory_recall requires 'query'")
    limit = max(1, min(int(args.get("limit", 5) or 5), 50))
    store = MemoryStore(Path(ctx.workspace) if ctx.workspace else None)
    hits = store.search(query, limit=limit)
    if not hits:
        return f"No memory matched {query!r}."
    return "\n".join(f"[{h.get('path', '?')}] {h.get('snippet', '')}" for h in hits)


def _memory_read(args: dict[str, Any], ctx: ToolContext) -> str:
    from personalclaw.memory import MemoryStore

    store = MemoryStore(Path(ctx.workspace) if ctx.workspace else None)
    return store.read_preferences() or "(no preferences recorded)"


def _memory_remember(args: dict[str, Any], ctx: ToolContext) -> str:
    from personalclaw.memory import MemoryStore

    text = str(args.get("text", "") or "").strip()
    if not text:
        raise ValueError("memory_remember requires 'text'")
    store = MemoryStore(Path(ctx.workspace) if ctx.workspace else None)
    store.init()
    store.add_preference(text)
    return f"Remembered: {text}"


#: The tools every sandbox gets offered, subject to its profile. Deliberately small and
#: dependency-light: each one runs entirely in-process on the host with no network hop, which is
#: what keeps "no credentials inside the sandbox" true rather than aspirational.
#:
#: The write-class member (``memory_remember``) is here on purpose. A read-only default surface
#: would make the write refusal below unreachable in production — a control present but inert,
#: which reads identically to a control that works right up until the day it matters.
DEFAULT_SURFACE: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="memory_recall",
        kind="search",
        handler=_memory_recall,
        summary="Full-text search over the workspace's memory index.",
    ),
    ToolSpec(
        name="memory_read",
        kind="read",
        handler=_memory_read,
        summary="Read the recorded preferences.",
    ),
    ToolSpec(
        name="memory_remember",
        kind="edit",
        handler=_memory_remember,
        summary="Append one preference line to memory (write-class).",
    ),
)


def surface_map(surface: tuple[ToolSpec, ...] = DEFAULT_SURFACE) -> dict[str, ToolSpec]:
    return {spec.name: spec for spec in surface}


class ToolGateway:
    """The HOST end of the exec channel. Owns the policy, runs the tool, writes the SEL row."""

    def __init__(
        self,
        *,
        profile: SafetyProfile,
        context: ToolContext,
        surface: tuple[ToolSpec, ...] = DEFAULT_SURFACE,
    ) -> None:
        self._profile = profile
        self._ctx = context
        self._surface = surface_map(surface)

    @property
    def offered(self) -> tuple[str, ...]:
        """The tool names this profile may actually call — what the shim is told it has.

        Computed from the same policy that enforces at call time, so the advertised surface and
        the enforced surface cannot drift. A tool the profile would refuse is not advertised, and
        a tool that is advertised is not refused.
        """
        return tuple(
            sorted(name for name, spec in self._surface.items() if not self._refusal(spec))
        )

    def _refusal(self, spec: ToolSpec) -> str:
        """Why *spec* is refused under this profile, or ``""`` when it is permitted."""
        grants = self._profile.tool_grants
        if grants == TOOL_CUSTOM:
            allow = tuple(self._profile.tool_allowlist)
            if spec.name in allow:
                return ""
            return (
                f"{spec.name} is not in the {self._profile.name!r} profile's tool allowlist "
                f"({', '.join(allow) or 'empty'})"
            )
        mode = _GRANT_TO_TASK_MODE.get(grants, "ask")
        deny = task_mode_denies(mode, spec.name, spec.kind, {})
        if deny:
            return (
                f"{spec.name} is write-class and the {self._profile.name!r} profile grants "
                f"{grants!r} tools only — {deny}"
            )
        return ""

    def handle_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """One request in, one response out. Never raises; a failure IS a response."""
        from personalclaw.sel import sel

        name = str(request.get("tool", "") or "").strip()
        raw_args = request.get("args")
        args: dict[str, Any] = dict(raw_args) if isinstance(raw_args, dict) else {}
        request_id = str(request.get("id", "") or "")
        version = request.get("protocol", PROTOCOL_VERSION)

        def _audit(outcome: str, error: str = "") -> None:
            sel().log_tool_invocation(
                session_key=self._ctx.session_key or "sandbox-tool",
                tool_name=name or "(unnamed)",
                tool_kind="sandbox_tool",
                outcome=outcome,
                request_id=request_id,
                downstream_service=f"sandbox:{self._ctx.sandbox}",
                resources=self._profile.name,
                error=error,
            )

        def _fail(code: str, message: str) -> dict[str, Any]:
            _audit("denied" if code in (ERR_REFUSED, ERR_UNKNOWN_TOOL) else "error", message)
            return {"id": request_id, "ok": False, "code": code, "error": redact(message)}

        if version != PROTOCOL_VERSION:
            return _fail(
                ERR_PROTOCOL,
                f"shim speaks protocol {version!r}, host speaks {PROTOCOL_VERSION} — "
                "reinstall the shim into this sandbox",
            )
        if not name:
            return _fail(ERR_PROTOCOL, "request names no tool")
        spec = self._surface.get(name)
        if spec is None:
            return _fail(
                ERR_UNKNOWN_TOOL,
                f"{name!r} is not on this sandbox's tool surface "
                f"(offered: {', '.join(self.offered) or 'nothing'})",
            )
        refusal = self._refusal(spec)
        if refusal:
            # Refused HOST-side, after the request arrived. The sandbox cannot influence this
            # decision — it does not hold the profile and never sees the surface table.
            return _fail(ERR_REFUSED, refusal)
        try:
            result = spec.handler(args, self._ctx)
        except Exception as exc:  # a tool's own failure is data, not a gateway crash
            logger.debug("sandbox tool %s failed", name, exc_info=True)
            return _fail(ERR_FAILED, f"{type(exc).__name__}: {exc}")
        _audit("allowed")
        # Redact on the way OUT: a tool that read a host file may have picked up a credential,
        # and the channel is the boundary where that must not cross.
        return {"id": request_id, "ok": True, "result": redact(str(result))}

    # ── transports (the channel the sandbox handle already owns) ────────────────

    def serve_fileobjs(self, rfile: Any, wfile: Any, *, max_requests: int = 0) -> int:
        """Serve newline-delimited JSON over a blocking pipe pair. Returns requests served.

        This is the shape a sandbox handle's exec channel has: two file objects the HOST created.
        There is no bind, no listen and no accept anywhere in this function — that absence is the
        security property, so it is worth reading for.
        """
        served = 0
        while True:
            line = rfile.readline()
            if not line:
                return served
            if len(line) > _MAX_LINE_BYTES:
                response: dict[str, Any] = {
                    "id": "",
                    "ok": False,
                    "code": ERR_PROTOCOL,
                    "error": "request exceeds the channel line limit",
                }
            else:
                try:
                    request = json.loads(line.decode("utf-8") if isinstance(line, bytes) else line)
                except (ValueError, UnicodeDecodeError) as exc:
                    response = {
                        "id": "",
                        "ok": False,
                        "code": ERR_PROTOCOL,
                        "error": f"unparseable request: {exc}",
                    }
                else:
                    response = self.handle_request(
                        request if isinstance(request, dict) else {"tool": ""}
                    )
            payload = (json.dumps(response) + "\n").encode("utf-8")
            wfile.write(payload)
            wfile.flush()
            served += 1
            if max_requests and served >= max_requests:
                return served

    async def serve_streams(self, reader: Any, writer: Any, *, max_requests: int = 0) -> int:
        """The asyncio-stream form of :meth:`serve_fileobjs`, for an async exec channel."""
        served = 0
        while True:
            line = await reader.readline()
            if not line:
                return served
            try:
                request = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                response: dict[str, Any] = {
                    "id": "",
                    "ok": False,
                    "code": ERR_PROTOCOL,
                    "error": f"unparseable request: {exc}",
                }
            else:
                response = self.handle_request(
                    request if isinstance(request, dict) else {"tool": ""}
                )
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
            served += 1
            if max_requests and served >= max_requests:
                return served
