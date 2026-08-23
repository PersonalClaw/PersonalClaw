"""The ONE wire error emitter, and the append-only registry of wire codes.

`AGENTS.md` §"Shared conventions" → **Error envelope (HTTP)** owns this shape:
a failed API route answers ``{"error": {"code": "<stable_snake_code>", "message":
"<human>"}}``, and ``code`` is *append-only and never reworded once shipped* — a
stable surface an agent (or a saved SOP, or a browser client) branches on.

**Why this module exists.** The declaration above shipped long before anything
could check it: thirteen module-local ``_err``/``_error``/``_bad_request``
helpers each re-derived the envelope, four of them emitted a *flat*
``{"error": "<prose>"}`` carrying no code at all, and their signatures disagreed
in argument ORDER (``_err(code, message, status)``, ``_err(message, code,
status)`` and ``_err(code, status)`` all shipped), so a copy-paste between two
handlers silently swapped the machine code and the human message. There was also
no registry of wire codes and no rail, so "append-only" was unverifiable.
:func:`json_error` is the single emitter that replaced all thirteen, and
:data:`HTTP_ERROR_CODES` is the registry the rail
(``tests/test_http_error_codes_append_only.py``) checks.

**No positional ambiguity, by construction.** ``code`` is the ONLY positional
parameter. ``message``, ``status`` and everything else are keyword-only, so the
copy-paste hazard that motivated this module cannot recur: there is no second
positional slot for a code and a message to swap between.

**Two envelopes, deliberately distinct — do not merge them.** This module owns
the *wire* envelope (``lowercase_snake`` codes; what a branching HTTP client
reads). :mod:`personalclaw.errors` owns :class:`~personalclaw.errors.AgentError`
(``ERR_UPPER_SNAKE`` codes; the carrier *into an LLM session*, on
``ToolResult``/``ActionResult``). The two vocabularies never overlap by
construction — the case of the code tells you which surface you are on — and the
rails on both registries assert that disjointness. A third, transport-independent
vocabulary (``WF_UPPER_SNAKE``, the workflows *service-result* codes) is
translated into this one by ``workflows/handlers.py``'s ``_STATUS_MAP``; it is not
a wire vocabulary and does not belong here.

**Success envelopes are out of scope.** The same convention says success bodies
imitate the neighboring handler and are *not* standardized retroactively.
"""

from __future__ import annotations

from typing import Any, Mapping

from aiohttp import web

# ── The append-only wire-code registry ─────────────────────────────────────
#
# code → one-line meaning. This is the peer of :data:`personalclaw.errors.ERROR_CODES`
# for the HTTP surface, and it obeys the same rule: APPEND a row for a new failure
# path; never delete or reword a released one (a saved SOP or a browser client may
# branch on it). ``tests/test_http_error_codes_append_only.py`` enforces that, and
# also asserts that every *literal* code passed to :func:`json_error` appears here —
# so the registry cannot quietly fall behind the emitter.
#
# The meaning doubles as the default ``message`` when a call site has no
# per-instance detail to add (the auth/device-pairing routes, which deliberately
# answer with a fixed string per code so the response cannot be used to enumerate
# users or devices).
HTTP_ERROR_CODES: dict[str, str] = {
    # ── generic request-shape failures ──
    "bad_request": "The request was malformed or carried an unusable parameter.",
    "invalid_request": "The request was well-formed JSON but failed validation.",
    "invalid_json": "The request body is not valid JSON.",
    "invalid_body": "The request body is valid JSON but not the expected object.",
    "not_found": "The addressed resource does not exist.",
    "forbidden": "The caller is not permitted to touch this resource.",
    "confirmation_required": "The operation is destructive and needs an explicit confirm.",
    # ── API version negotiation (dashboard/api_version_gate.py) ──
    # The `error` object additionally carries client_version, server_version,
    # min_supported_version and upgrade ("client"|"server") — a refusal that named
    # neither number nor a direction would be no more actionable than a 500.
    "api_version_unsupported": (
        "The client's declared API version is outside the window this gateway supports."
    ),
    # ── auth (handlers/auth.py) — fixed message per code, never request-derived ──
    "auth_not_enabled": "Owner authentication is not enabled on this instance.",
    "auth_invalid_credentials": "The submitted credential did not verify.",
    "auth_locked_out": "Too many failed attempts from this address; try again later.",
    "auth_totp_required": "A second factor is required to finish this login.",
    "auth_enroll_code_invalid": "The enrollment code did not verify.",
    # ── device pairing (handlers/devices.py) — fixed message per code ──
    "device_pair_code_invalid": "The pairing code did not verify.",
    "device_pair_expired": "The pairing code has expired.",
    "device_pair_origin_rejected": "The request origin is not allowed to pair a device.",
    "device_pair_locked_out": "Too many failed pairing attempts; try again later.",
    "device_unknown": "No such paired device.",
    # ── chat rewind (dashboard/chat_file_rewind.py) ──
    "invalid_turn": "The addressed turn does not exist or is not rewindable.",
    "turn_running": "The turn is still executing; it cannot be rewound yet.",
    # ── chat plan mode (dashboard/chat_plan.py) ──
    "session_not_found": "No such chat session.",
    "plan_session_missing": "The session has no plan in progress.",
    "step_id_required": "A step id is required.",
    "step_not_awaiting_review": "That plan step is not awaiting review.",
    "markdown_required": "A non-empty markdown body is required.",
    "comment_text_required": "A non-empty comment body is required.",
    # ── reasoning effort (dashboard/chat_handlers.py) ──
    "invalid_reasoning_effort": "The reasoning effort is not a short lowercase token.",
    "reasoning_effort_not_declared": (
        "The bound runtime did not declare that reasoning effort, so it cannot be honored."
    ),
    # ── security audit (handlers/security_audit.py) ──
    "audit_owner_only": "The audit trail is owner-only; an app-scoped token may not read it.",
    "invalid_cursor": "The pagination cursor is malformed.",
    "invalid_limit": "The limit parameter is out of range or not an integer.",
    "invalid_time_filter": "A since/until filter is not a recognized timestamp.",
    "unknown_filter": "The request names a filter this endpoint does not support.",
    # ── evals (handlers/evals.py) ──
    "evals_disabled": "The evals surface is switched off in config.",
    "judge_bench_absent": "No judge benchmark artifact has been produced yet.",
    "judge_bench_unreadable": "The judge benchmark artifacts could not be read.",
    "studies_unreadable": "The pre-registered study artifacts could not be read.",
    "study_absent": "No study is registered under that id.",
    # ── packs (handlers/packs.py) ──
    "pack_not_installed": "No such installed pack.",
    "pack_not_bundled": "The pack is not bundled with this build.",
    "pack_build_failed": "Building the pack artifact failed.",
    "pack_update_refused": "The pack update was refused.",
    "pack_has_no_roster": "The pack declares no agent roster.",
    "binding_key_required": "A binding key is required.",
    "binding_rejected": "The submitted binding was rejected.",
    "one_link_required": "A one-link target is required.",
    "one_link_rejected": "The submitted one-link target was rejected.",
    "project_not_found": "No such project.",
    "prompt_card_failed": "Rendering the prompt card failed.",
    "prompt_card_rejected": "The submitted prompt card was rejected.",
    "rejection_incomplete": "A rejection must carry a reason.",
    # ── research reports (handlers/research_reports.py) ──
    "research_reports_unavailable": "Scheduled research reports are not available in this build.",
    # ── voice profiles (handlers/voice_profiles.py) — VoiceProfileError.reason ──
    "consent_required": "The cloned voice's consent record is not verified.",
    "artifact_missing": "The addressed voice artifact does not exist.",
    "artifact_not_readable": "The voice artifact exists but is not readable.",
    "invalid_artifact": "The artifact path is not a recognized artifact reference.",
    "invalid_profile_id": "The voice profile id is malformed.",
    "path_escape": "The submitted path escapes the voice_profiles directory.",
    "invalid_seed": "The seed must be an integer.",
    "invalid_speed": "The speed must be a number.",
    "invalid_kind": "The voice profile kind is not one of the supported kinds.",
    "kind_immutable": "A voice profile's kind cannot be changed after creation.",
    "invalid_extension": "The audio file extension is not supported.",
    "name_required": "A non-empty name is required.",
    "consent_text_required": "Consent text is required for a cloned voice.",
    "empty_history": "The profile has no generation history.",
    "history_audio_missing": "That generation's audio file is gone.",
    # ── session organize (handlers/session_organize.py) ──
    # (uses the generic bad_request/not_found rows above)
}


def json_error(
    code: str,
    *,
    message: str | None = None,
    status: int,
    headers: Mapping[str, str] | None = None,
    error_extra: Mapping[str, Any] | None = None,
    **extra: Any,
) -> web.Response:
    """The one wire error envelope: ``{"error": {"code", "message"}}``.

    :param code: The stable ``lowercase_snake`` code a client branches on. The ONLY
        positional parameter, so it can never be confused with ``message``.
    :param message: The human sentence. Omit it to use the :data:`HTTP_ERROR_CODES`
        meaning — which is what the auth and device-pairing routes do, because a
        fixed message per code is what keeps those responses from being usable to
        enumerate users or devices.
    :param status: The HTTP status. Keyword-only and required: an error envelope
        served with an accidental 200 is worse than no envelope.
    :param headers: Response headers (``Retry-After`` on a lockout, for instance).
    :param error_extra: Extra keys merged INSIDE the ``error`` object — the
        actionable half of a failure (a preflight's findings, a service code).
    :param extra: Extra keys merged at the TOP level, beside ``error``.

    An unregistered ``code`` still emits (falling back to the code as its own
    message) rather than raising: a typo must not turn a 400 into a 500. The
    append-only rail is what catches the typo, statically, before it ships.
    """
    err: dict[str, Any] = {
        "code": code,
        "message": message if message is not None else HTTP_ERROR_CODES.get(code, code),
    }
    if error_extra:
        err.update(error_extra)
    return web.json_response({"error": err, **extra}, status=status, headers=dict(headers or {}))
