"""Platform-wide WHAT/WHY/FIX error envelope (PLATFORM-LEGIBILITY §2).

A failure returned into an LLM session is only useful if the model can act on it.
:class:`AgentError` carries the three facts that turn a dead-end into a
self-correction: **what** failed (with the concrete value), **why** (the
mechanism), and the exact **fix** (the next action) — plus optional
``suggestions`` (did-you-mean candidates the model can branch to). The harness
research this follows measured this shape converting failures into recovery
loops; the point is that the model reads structure, not prose.

**Distinct from the HTTP error envelope.** `AGENTS.md` §"Shared conventions" →
**Error envelope (HTTP)** owns the *wire* shape for API-route errors —
``{"error": {"code": "<lowercase_snake>", "message": ...}}`` — a thing a
browser/external client branches on, emitted by the one
:func:`personalclaw.http_errors.json_error` and registered in
:data:`personalclaw.http_errors.HTTP_ERROR_CODES`. ``AgentError`` is the carrier
*into an LLM session* (on ``ToolResult``/``ActionResult`` and the exceptions that
become tool-result text). The two never collide: HTTP codes are
``lowercase_snake``; agent codes are ``ERR_UPPER_SNAKE`` (asserted below). A route
handler keeps returning the wire envelope; a tool surfaces an ``AgentError``.

The registry :data:`ERROR_CODES` is **append-only** — a shipped code is a stable
surface an agent (and its saved prompts/SOPs) branch on, so it is never removed or
reworded once released (``tests/test_error_codes_append_only.py`` enforces it).
New failure paths add a code; they never repurpose one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── The append-only code registry ──────────────────────────────────────────
#
# code → one-line meaning (the STABLE contract; the per-instance ``what``/``why``
# carry the concrete detail). APPEND-ONLY: add a row for a new failure path;
# never delete or reword an existing one (a saved SOP may branch on it). Seeded
# with the codes this slice's seams raise; later slices append their own
# (AMBIENT-SURFACES' ``unknown-component``-class, WORKFLOWS-V2's ``ERR_UNKNOWN_NODE``)
# to the same registry they cite.
ERROR_CODES: dict[str, str] = {
    "ERR_TOOL_ARG_INVALID": (
        "A tool argument failed validation (wrong type, out of range, or not in "
        "the allowed set)."
    ),
    "ERR_MODEL_UNRESOLVED": (
        "The model/provider bound to a use case cannot be resolved — the pin names "
        "a provider absent from config, or no provider is configured."
    ),
    "ERR_HOOK_PROVIDER_UNKNOWN": (
        "A hook/trigger names an action provider that is not registered or not in "
        "the allowed set."
    ),
    "ERR_ACTION_PROVIDER_FAILED": ("An action provider raised while executing a trigger's action."),
    "ERR_COMPUTER_USE_DISABLED": (
        "Desktop computer use is not armed on this machine — the out-of-band keystone "
        "enable file is absent, unreadable, or does not say enabled."
    ),
    "ERR_COMPUTER_USE_APP_NOT_ALLOWED": (
        "The target application is not on the operator's allowlist in the out-of-band "
        "keystone enable file. An empty or absent allowlist permits nothing, so an armed "
        "capability still drives no application until the operator names one."
    ),
    "ERR_COMPUTER_USE_SECURE_FIELD": (
        "The input destination is a secure/password field, a field whose label names a "
        "secret, a field already holding credential-shaped text, or a target shape the "
        "screen does not recognise. An unrecognised destination is refused like a password "
        "field is: a screen that only knows the shapes it was shown has a hole in it."
    ),
    "ERR_COMPUTER_USE_UNKNOWN_TOOL": (
        "The computer-use dispatch was asked for a tool it does not declare. The seven "
        "declared tools are the whole surface; an unknown name is refused rather than "
        "guessed at."
    ),
    "ERR_COMPUTER_USE_BAD_ARGUMENT": (
        "A computer-use argument is missing, the wrong type, or outside the range the "
        "snapshot supports (for example an element index past the last element the walked "
        "window exposes)."
    ),
    "ERR_COMPUTER_USE_STALE_INDEX": (
        "The element index names a snapshot that is unknown, has expired, or whose window "
        "has changed since it was walked. Acting on a stale index would press whatever now "
        "sits at that position, so it is refused and a fresh snapshot is required."
    ),
    "ERR_COMPUTER_USE_DRIVER_UNAVAILABLE": (
        "No accessibility driver is available for this platform, or the driver has no "
        "handler for this operation. A typed refusal, never a silent no-op — nothing was "
        "clicked, typed or changed on the desktop."
    ),
    "ERR_COMPUTER_USE_DRIVER_FAILED": (
        "The ceilinged driver subprocess could not be started, did not answer within its "
        "timeout, or returned something unreadable. Reported as a failure rather than as an "
        "empty result, because a computer-use no-op reads to a model as success."
    ),
    "ERR_COMPUTER_USE_AX_PERMISSION": (
        "The OS has not granted this process the accessibility permission needed to read a "
        "window's element tree or activate an element. Distinct from a driver failure because "
        "only a human can fix it, in the OS's own privacy settings — a program cannot grant "
        "itself input access, and this build never pops the system prompt on its own."
    ),
    "ERR_BROWSE_CONFIG": (
        "A browse action's config is incomplete — it named no goal, or no page to start "
        "from. Refused before a browser is touched."
    ),
    "ERR_BROWSE_NO_TARGET": (
        "There is no Chrome DevTools page target for the browse provider to drive. A typed "
        "refusal rather than a silent no-op: an action that reports success while browsing "
        "nothing is indistinguishable to a workflow from one that did the work."
    ),
    "ERR_BROWSE_CONNECT_FAILED": (
        "Connecting to the configured CDP page target failed. The browser is not running, "
        "or the target's WebSocket URL has gone stale (they are per-tab and short-lived)."
    ),
    "ERR_BROWSE_INCIDENT_ACTIVE": (
        "Incident mode is on, which suspends unattended work, so the browse run was refused "
        "before it started rather than retried against a control someone deliberately pulled."
    ),
    "ERR_BROWSE_FAILED": (
        "A browse run ended without reaching its goal — the first navigation was denied by "
        "the BROWSE egress policy, the page could not be read, or the decision call failed. "
        "Distinct from a PARK, which succeeds with notes and asks for a human."
    ),
}


@dataclass(frozen=True)
class AgentError:
    """One machine-readable failure an agent can recover from.

    ``code`` is a stable :data:`ERROR_CODES` key (branch on it, never on prose).
    ``what``/``why``/``fix`` are the three human/LLM-facing lines, each with the
    concrete value baked in. ``suggestions`` are did-you-mean candidates (nearest
    valid tool/provider/enum values) the model can pick from directly.
    """

    code: str
    what: str
    why: str
    fix: str
    suggestions: tuple[str, ...] = field(default_factory=tuple)

    def render(self) -> str:
        """The three labeled lines fed into the model's context (+ suggestions).

        This is the surfaced string EVERYWHERE the envelope reaches text — a
        tool-result, an exception message, an action's ``error``. Consumers that
        only read a string still get WHAT/WHY/FIX; consumers that branch read the
        structured fields via :meth:`to_dict`. So the envelope is never dead: it
        is the source of the message, not a parallel structure beside it.
        """
        lines = [f"WHAT: {self.what}", f"WHY: {self.why}", f"FIX: {self.fix}"]
        if self.suggestions:
            lines.append(f"DID YOU MEAN: {', '.join(self.suggestions)}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """The structural carrier for the FE tool card + external clients."""
        return {
            "code": self.code,
            "what": self.what,
            "why": self.why,
            "fix": self.fix,
            "suggestions": list(self.suggestions),
        }
