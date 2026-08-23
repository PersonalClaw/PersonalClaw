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
