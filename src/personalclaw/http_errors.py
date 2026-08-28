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
    "invalid_id": "A record id is not a single path segment (separators, '..' or over-long).",
    # ── dashboard file I/O (handlers/files.py) ──
    # The refusal `_validate_dashboard_path` produces: not a path under any root the dashboard
    # surfaces, or a blocked basename inside one. 400 rather than 403/404 deliberately — the answer
    # must not confirm whether the path exists. `files.py`'s six other sites emit this same sentence
    # FLAT today; this is the code they convert to, and the census ratchet is what moves them.
    "invalid_path": "The path is not one the dashboard may touch.",
    # A DIFFERENT check from `invalid_path`, and the distinction is load-bearing: `_reject_name`
    # judges a single NAME (separators, `..`, over-long) before any root is consulted, so it fires
    # on input the allowlist never sees. Its three call sites — mkdir, upload, and the create-file
    # route — all emitted the refusal FLAT, which is what pushed the census one over its ceiling.
    # Not folded into `invalid_id`: that one says "a record id", and telling a client its filename
    # is a bad id is the wrong noun for a file browser.
    "invalid_name": "A file or directory name is not a single safe path segment "
    "(separators, '..' or over-long).",
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
    # ── channel sender trust (handlers/channel_trust.py) ──
    "channel_trust_sender_unknown": "That sender is not on this channel's allowlist.",
    # ── push subscriptions (handlers/push.py) ──
    "push_subscription_invalid": "The push subscription is missing an https endpoint or its keys.",
    "push_not_subscribed": "That device has no push subscription.",
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
    # ── credential store (handlers/security_credentials.py) ──
    "credentials_owner_only": "The credential store is owner-only; an app-scoped token may "
    "neither read where secrets live nor move them.",
    "migration_refused": "The keychain is not the active credential backend, so moving "
    "secrets out of .env would leave them nowhere. Nothing was changed.",
    "rollback_refused": "There is no pre-migration .env snapshot to roll back to.",
    # ── evals (handlers/evals.py) ──
    "evals_disabled": "The evals surface is switched off in config.",
    "learning_disabled": "Learning is switched off in config, so there is nothing learned to "
    "report on.",
    "judge_bench_absent": "No judge benchmark artifact has been produced yet.",
    "judge_bench_unreadable": "The judge benchmark artifacts could not be read.",
    "studies_unreadable": "The pre-registered study artifacts could not be read.",
    "study_absent": "No study is registered under that id.",
    "ablation_absent": "No harness-ablation report has been produced yet.",
    "ablation_unreadable": "The ablation artifacts could not be read.",
    "retrieval_absent": "No retrieval-ablation report has been produced yet.",
    "retrieval_unreadable": "The retrieval benchmark artifacts could not be read.",
    "learning_benchmark_absent": "No skill-impact benchmark report has been produced yet.",
    "learning_benchmark_unreadable": "The skill-impact benchmark report could not be read.",
    "store_required": "The request must name one retrieval store (knowledge or memory).",
    "card_unavailable": "That retrieval store could not be read for labelling.",
    "store_mutated": "A read-only harness pass wrote to a store and was refused.",
    "labels_required": "The request carries no qrels label mapping.",
    "labels_rejected": "The submitted qrels labels were refused.",
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
    # ── the triage digest (handlers/proactive.py — PROACTIVE-ASSISTANT §5.1/§5.4) ──
    # `triage_digest_unreadable` is a 500 whose `error` object travels BESIDE the digest view's own
    # `state: "error"`, so a client that only reads the code and one that renders the card both get
    # a usable answer. `triage_digest_expired` is a 409 and is load-bearing: an ordinal numbers ONE
    # digest window, so a reply against a stale run must be REFUSED by code rather than executed
    # best-effort against whatever is third today.
    "triage_digest_unreadable": "The triage digest could not be read.",
    "triage_digest_expired": "That digest is no longer the current one, so its item numbers no longer address the items it listed.",  # noqa: E501
    "triage_schedule_write_failed": "The triage digest schedule could not be written.",
    # ── the decision journal (handlers/decisions.py — PROACTIVE-ASSISTANT §2.5/§5.3) ──
    # A 500, and the code is the whole reason it is not a 200 with an empty list: ONE payload
    # carries the rows AND the calibration strip aggregated from them, so a read that raised has
    # no honest partial answer to give. An empty journal would say "you have never logged a
    # decision" — the most confident possible way to say the opposite of what is known, on the
    # one surface whose entire value is not overclaiming. The `message` carries the raiser's own
    # type and text, so the sentence the user reads names the real fault.
    "decision_journal_unreadable": (
        "The decision journal could not be read, so neither your decisions nor the calibration "
        "strip are shown. This is a failed read, NOT an empty journal — nothing you logged was "
        "lost, and the read changes nothing, so retrying is safe. Fix: the message names the "
        "underlying fault; `personalclaw doctor` checks the sqlite build the store needs."
    ),
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
    # ── inbound control bridge (inbound/bridge.py — EXTERNAL-ACCESS §1.1/§4) ──
    #
    # The ADMISSION layers deliberately reuse the generic `not_found`/`forbidden` rows
    # above rather than adding a `bridge_disabled`-style code. `inbound/gate.py` answers
    # 404 precisely so a disabled surface does not confirm its own existence to a
    # prober, and a code that named the surface would hand back exactly what the status
    # withholds. The rows below are the ones a caller who ALREADY authenticated needs in
    # order to tell its own mistakes apart.
    "unauthorized": "The request carried no usable bearer credential.",
    # ── capture proxy (inbound/capture_proxy.py — EXTERNAL-ACCESS §7) ──
    # A 502 here is always about the UPSTREAM the caller's own agent named, never about
    # PersonalClaw's own state, so the three are kept apart: `unavailable` is "nothing named
    # an upstream", `denied` is "the operator's egress allow-list refused it" (a decision
    # somebody can change), and `failed` is "the call was made and the far side broke".
    "upstream_unavailable": "No upstream base URL could be resolved for this dialect.",
    "upstream_denied": "The resolved upstream is not on the operator's egress allow-list.",
    "upstream_failed": "The upstream call was attempted and did not complete.",
    "service_unavailable": "The service is temporarily suspended; retry later.",
    "unknown_action": "The named control-bridge action does not exist.",
    "action_not_bound": "The calling client's bindings do not include this action.",
    "action_failed": "The control-bridge action raised while running.",
    "confirm_token_invalid": "The confirmation token is unknown, already used, or expired.",
    # ── OpenAI-compatible inbound dialect (inbound/openai_dialect.py — EXTERNAL-ACCESS §2) ──
    #
    # ADMISSION reuses the generic rows for the same reason the MCP surface below does: a
    # code naming this surface, or naming which kill switch fired, hands a prober exactly
    # what the 404 status is chosen to withhold. So 404/403/401/503 admission answers carry
    # `not_found`/`forbidden`/`unauthorized`/`service_unavailable`, and `rate_limited` and
    # `request_too_large` are reused for the caps.
    #
    # The rows below are the ones a caller PAST admission needs to tell its own mistakes
    # apart. They ride inside the `/v1` wire envelope — that surface's shape wins per the
    # 2026-07-26 amendment — with `type`/`param` added via `json_error`'s `error_extra`, so
    # the stable code survives in `code` exactly where a script branches on it.
    #
    # `unknown_agent` is the load-bearing one: `model` names an AGENT on this surface, and
    # `resolve_agent_bindings` would answer an unknown name with the DEFAULT agent. This
    # code is how a client learns it asked for something that does not exist instead of
    # being quietly served by something else.
    "unknown_agent": "The requested model does not name an agent on this instance.",
    "agent_binding_violation": "This client is pinned to a different agent than it requested.",
    "empty_messages": "The request carried no message with content.",
    "turn_timeout": "The agent did not finish the turn within this surface's deadline.",
    # Audio aliases (§2.2). "No voice is bound" and "synthesis broke" are separate codes
    # because the first is fixed in Settings and the second is a fault to report; a client
    # that cannot tell them apart will retry a configuration problem forever.
    "no_bound_voice": "No text-to-speech voice is selected on this instance.",
    "synthesis_failed": "Speech synthesis was attempted and raised.",
    "synthesis_empty": "Speech synthesis completed but produced no audio.",
    "missing_input": "The synthesis request carried no input text.",
    "stt_unavailable": "No speech-to-text model is installed on this instance.",
    "transcription_failed": "Transcription was attempted and raised.",
    "missing_file": "The upload carried no file field.",
    "invalid_content_type": "The request's Content-Type is not supported on this route.",
    "invalid_upload": "The multipart upload could not be parsed.",
    # ── inbound MCP surface (inbound/mcp_http.py — MCP-READONLY-INBOUND §C2) ──
    #
    # Same reasoning as the bridge above, and the same conclusion: ADMISSION reuses the
    # generic `not_found`/`service_unavailable`/`forbidden`/`unauthorized` rows, because a
    # code naming this surface — or naming which of the three kill switches fired — hands a
    # prober exactly what the 404 status is chosen to withhold. The rows below are the ones
    # a caller who already got past admission needs in order to tell its own mistakes
    # apart: each names a CAP the caller can respect or a method it can stop using, and
    # none of them is a fact about the instance.
    #
    # `too_many_concurrent_requests` shares its 503 with `service_unavailable` and is a
    # separate code on purpose: "you have too many in flight" is fixed by the client
    # serialising its calls, "the instance is suspended" is fixed by waiting, and a client
    # that cannot tell them apart will retry the wrong one forever.
    "method_not_allowed": "The HTTP method is not supported on this route.",
    "rate_limited": "The caller exceeded its request rate cap; see Retry-After.",
    "too_many_concurrent_requests": (
        "The caller already has the maximum number of requests in flight."
    ),
    "request_too_large": "The request body exceeds this surface's size cap.",
    # ── inbound A2A gateway (inbound/a2a.py — EXTERNAL-ACCESS §5) ──
    #
    # Admission reuses the generic rows above for the reason the MCP block states. This
    # ONE code exists because a 200 card with no skills is a legitimate, meaningful answer
    # ("nobody published a template"), so a catalog that could not be READ must be able to
    # say so instead of borrowing that answer. Without it, an empty card and a broken card
    # are the same bytes — and a client would stop asking.
    "a2a_catalog_unavailable": "The published-workflow catalog could not be read.",
    # ── desktop computer use (handlers/computer_use.py) ──
    # Both rows additionally carry agent_code/what/why/fix INSIDE the `error` object: the
    # AgentError the dispatch composed has to reach the model unchanged, and the wire code is
    # what an HTTP client branches on. Two codes, not one, because the two mean different
    # things to an operator: `refused` is a decision somebody can change, `unavailable` is a
    # driver that could not run and is nobody's misconfiguration.
    "computer_use_refused": (
        "A computer-use call was refused — the out-of-band keystone is off, the target "
        "application is not on the operator's allowlist, the destination is a secure field, "
        "the element index is stale, or the request named no valid tool."
    ),
    "computer_use_unavailable": (
        "The computer-use call was permitted but no accessibility driver could run it on this "
        "platform, or the ceilinged driver subprocess failed. Nothing was changed on the "
        "desktop."
    ),
    # ── binary artifact write + document model (artifacts/handlers.py — DFE §C3) ──
    #
    # Every row here is a DIFFERENT thing for the caller to do, which is the whole test
    # for a distinct code. A document editor that could only see "409" would have to
    # guess between "reload the document", "you sent the wrong file type" and "this
    # artifact has no binary body at all" — three remedies, one of which destroys the
    # user's work if guessed wrong.
    # ── React artifact deploy (artifacts/handlers.py — PEP-9) ────────────────────
    #
    # Two codes, not one, because the remedies are opposite: a build failure is the
    # user's SOURCE to fix, a bad slug is the request to fix. The `message=` on both
    # carries the raiser's own "WHAT — WHY. Fix: FIX" sentence verbatim.
    "artifact_build_failed": (
        "Bundling the artifact's React source failed, so nothing was published. Fix: read the "
        "build message — it names the file and the reason."
    ),
    "artifact_slug_invalid": (
        "The artifact slug is not a usable directory name, so no served path could be built for "
        "it. Fix: rename the artifact to something slug-safe."
    ),
    "kind_not_binary": (
        "The artifact's kind stores its body as text, so it has no binary body to replace. "
        "Fix: PATCH the artifact instead."
    ),
    "if_match_required": (
        "A whole-body write must declare the version it is replacing via `If-Match`. Fix: read "
        "the artifact, then resend with `If-Match: <version>`."
    ),
    "if_match_malformed": (
        "The `If-Match` header is not an artifact version number. Fix: send the integer "
        "`version` the artifact reported."
    ),
    "version_conflict": (
        "The artifact moved since it was read, so the write would have destroyed somebody "
        "else's edit. Fix: reload and re-apply. The `error` object names the current version."
    ),
    # Same family as `version_conflict` above: a write refused because it would have
    # destroyed data the caller never saw. An intent's id is DERIVED from its goal, so two
    # differently-worded goals can slugify onto one id — and the create path used to
    # overwrite the first goal and answer 201. The message names the other intent's goal
    # rather than the id, which no user ever sees.
    "intent_id_taken": (
        "Another intent already covers this goal. Fix: edit that intent, or reword this "
        "goal so the two are distinguishable."
    ),
    # A referenced id the caller named that does not exist. Both are 400 and not 404: the
    # addressed resources (the sessions) are real; it is a field INSIDE the body that names
    # something unknown, so 404 would say the wrong thing about the wrong noun. Added because
    # `POST /api/chat/sessions/bulk` used to persist a dangling id while the single-session
    # paths beside it validated (#771).
    "unknown_tag_id": "The request names a tag id that does not exist.",
    "unknown_folder_id": "The request names a folder id that does not exist.",
    # Same family as `intent_id_taken`: a name-uniqueness refusal on a surface whose sibling
    # (tags) enforced it at the DB while this one had no guard at all (#755).
    "collection_name_taken": (
        "A shelf with that name already exists. Fix: open that shelf, or pick another name."
    ),
    "content_length_required": (
        "The request declared no `Content-Length`, so its size cannot be checked before the "
        "body is read. Fix: send a length-delimited body, not a chunked one."
    ),
    "mime_kind_mismatch": (
        "The body's `Content-Type` belongs to a different artifact kind than the addressed "
        "artifact. Fix: send the format this artifact already is, or create a new artifact."
    ),
    "unsupported_media_type": (
        "The body's `Content-Type` is not a binary artifact format this build can store."
    ),
    "model_unavailable": (
        "No document parser or writer ships for this artifact's kind, so it has no editable "
        "model. Fix: read the bytes via the raw route instead."
    ),
    "model_parse_failed": (
        "The stored bytes could not be parsed as a document of this artifact's kind."
    ),
    "invalid_model": (
        "The posted document model is not a valid model. The message names the offending path."
    ),
    "render_failed": "The document model was valid but the writer could not render it.",
    # A CONSENT refusal, not a capability one: the model was fine and the writer exists, but
    # in-place document editing is switched off and re-rendering is lossy. Distinct from
    # `forbidden` so the UI can say WHICH switch to turn on instead of "not allowed".
    "document_editing_off": (
        "In-place document editing is off, so this document cannot be re-rendered. Fix: turn "
        "on Settings › Documents › 'Edit documents in place'."
    ),
    # ── automation would-execute simulator (handlers/doctor.py — PLATFORM-RESILIENCE §3.3) ──
    # Two codes, not the generic `bad_request`/`not_found` pair, because the trust surface
    # branches on them: a missing id is a client bug the UI can fix by disabling its own
    # button, while an id that resolves to nothing means the automation was deleted under a
    # stale list and the panel has to refetch. `unknown_trigger` also carries the id it looked
    # up (`error_extra`), so a support log says WHICH automation vanished.
    "trigger_id_required": "A trigger id is required to describe what an automation would do.",
    "unknown_trigger": "No automation exists with that id.",
    # ── onboarding import (handlers/onboarding_import.py — PEP-5) ──
    # ONE code for both halves: a scan that could not read, and an import that stopped
    # after a write raised. Both mean "the machinery failed", both carry the failure's own
    # sentence, and both are safe to retry (the fingerprint ledger records each write as it
    # lands). A `conflict` or `rejected` ITEM is not this — those are 200 rows of the report.
    "onboarding_import_failed": "Scanning for or importing from another agent tool failed.",
    # ── direct tool invocation (handlers/tools.py) ──
    # 403 and not 404: the tool exists and this caller may reach the route. The user
    # turned it off, which is a policy answer, and a 404 would read as "no such tool" to a
    # cron script whose next move is to reinstall something.
    "tool_disabled": "The tool is disabled on the Tools page and will not be executed.",
    # Speech synthesis refused because the owner turned it off. Same family as `tool_disabled`:
    # a switched-off capability, not a malformed request. 503 rather than 403 to match the
    # sibling refusal on the same route ("no TTS voice selected"), which is also a
    # configuration-absent answer (#651).
    "tts_disabled": (
        "Text-to-speech is switched off. Fix: turn on “Speak replies aloud” in "
        "Settings → Speech & Transcription."
    ),
    # ── capture telemetry import (inbound/capture_proxy.py — EXTERNAL-ACCESS §8) ──
    # ONE code, for the store failing under the import — NOT for a file that parsed badly.
    # A malformed export is a 200 whose `reasons` name each skipped line (§8's
    # skipped-and-counted), so a caller that branches on this code branches on "the
    # machinery broke", which is retryable, and never on "your file was rubbish", which
    # is not. A refused `file` is `invalid_request`: the name, not the machinery.
    "capture_import_failed": "Staging an exported agent log into the capture store failed.",
    # ── secrets vault (handlers/secrets.py — EI-10) ──
    # Each sentence says what to DO, because the vault is a surface a user reaches by hand and a
    # restatement of the identifier ("the secret name is invalid") leaves them guessing which
    # character offended. `secret_absent` is a 404 on DELETE only: a GET never 404s, because an
    # empty vault is a legitimate state with its own empty-state copy, not an error.
    "secret_name_invalid": "A secret's name must look like an environment variable — letters, "
    "digits and underscores, not starting with a digit.",
    "secret_value_required": "Storing a secret needs a non-empty value; to remove one, "
    "use DELETE instead.",
    "secret_project_invalid": "The project id cannot be used to scope a secret — it must be a "
    "plain id without '__' in it.",
    "secret_absent": "No secret is stored under that name in the scope you asked for.",
    "secret_host_readonly": "That row is inherited from the host environment, so the vault "
    "cannot change or remove it — unset it where the gateway's environment is defined.",
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
