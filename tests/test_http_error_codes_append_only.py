"""Append-only guard for the WIRE error-code registry (`personalclaw.http_errors`).

The peer of ``tests/test_error_codes_append_only.py``, which guards the *agent*
registry (``errors.ERROR_CODES``, ``ERR_UPPER_SNAKE``). This one guards the *wire*
registry (``http_errors.HTTP_ERROR_CODES``, ``lowercase_snake``).

**Why it exists.** `AGENTS.md` §"Shared conventions" → **Error envelope (HTTP)** has
always declared the wire ``code`` "append-only and never reworded once shipped" — a
stable surface an agent, a saved SOP, or a browser client branches on. But no
registry existed and no rail existed, so the declaration was unverifiable: a
*declared* stable surface that nothing could check. The registry and this suite are
that check.

The mechanism mirrors the agent-code rail exactly: a frozen baseline
(:data:`_RELEASED`) embedded here, deliberately a COPY rather than a subset of the
live dict — the copy is what detects an in-place reword. Adding a code = add a row
to ``HTTP_ERROR_CODES`` (baseline untouched → still a subset → still green).
Removing or rewording one = the baseline no longer matches → red, which is the
point.

**Vacuity.** Two of these tests read the *code*, not the registry: they walk every
``json_error`` call site and assert its code is registered. A matcher that silently
stops matching would find zero call sites and pass, so both are floored against the
population the census measured (``test_wire_error_envelope_census.EMITTER_SITE_FLOOR``).
A rail that inspects nothing must never read as clean.
"""

from __future__ import annotations

import re

from test_wire_error_envelope_census import EMITTER_SITE_FLOOR, scan

from personalclaw.errors import ERROR_CODES
from personalclaw.http_errors import HTTP_ERROR_CODES, json_error

# The wire codes released as of PL-8. APPEND a row here only when a code is actually
# released; never edit or delete an existing row.
_RELEASED: dict[str, str] = {
    "bad_request": "The request was malformed or carried an unusable parameter.",
    "invalid_request": "The request was well-formed JSON but failed validation.",
    "invalid_json": "The request body is not valid JSON.",
    "invalid_body": "The request body is valid JSON but not the expected object.",
    "not_found": "The addressed resource does not exist.",
    "forbidden": "The caller is not permitted to touch this resource.",
    "confirmation_required": "The operation is destructive and needs an explicit confirm.",
    "auth_not_enabled": "Owner authentication is not enabled on this instance.",
    "auth_invalid_credentials": "The submitted credential did not verify.",
    "auth_locked_out": "Too many failed attempts from this address; try again later.",
    "auth_totp_required": "A second factor is required to finish this login.",
    "auth_enroll_code_invalid": "The enrollment code did not verify.",
    "device_pair_code_invalid": "The pairing code did not verify.",
    "device_pair_expired": "The pairing code has expired.",
    "device_pair_origin_rejected": "The request origin is not allowed to pair a device.",
    "device_pair_locked_out": "Too many failed pairing attempts; try again later.",
    "device_unknown": "No such paired device.",
    "invalid_turn": "The addressed turn does not exist or is not rewindable.",
    "turn_running": "The turn is still executing; it cannot be rewound yet.",
    "session_not_found": "No such chat session.",
    "plan_session_missing": "The session has no plan in progress.",
    "step_id_required": "A step id is required.",
    "step_not_awaiting_review": "That plan step is not awaiting review.",
    "markdown_required": "A non-empty markdown body is required.",
    "comment_text_required": "A non-empty comment body is required.",
    "audit_owner_only": "The audit trail is owner-only; an app-scoped token may not read it.",
    "invalid_cursor": "The pagination cursor is malformed.",
    "invalid_limit": "The limit parameter is out of range or not an integer.",
    "invalid_time_filter": "A since/until filter is not a recognized timestamp.",
    "unknown_filter": "The request names a filter this endpoint does not support.",
    "evals_disabled": "The evals surface is switched off in config.",
    "judge_bench_absent": "No judge benchmark artifact has been produced yet.",
    "judge_bench_unreadable": "The judge benchmark artifacts could not be read.",
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
    "research_reports_unavailable": ("Scheduled research reports are not available in this build."),
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
}

_CODE_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")

#: Emitter call sites whose code is an expression (``exc.reason``, an f-string, a
#: local). A CEILING: an f-string is the one place a brand-new, unregistered wire
#: code can enter without this rail noticing, so the number of such sites may not
#: grow. Measured at PL-8: 13 in voice_profiles (``exc.reason``), 2 in packs
#: (``f"pack_refused_{...}"``), 1 in devices (a local chosen from its constants).
#:
#: 17th (EA-2): ``inbound/openai_dialect.openai_error`` forwards a keyword-only ``code``
#: parameter into :func:`json_error`. It is NOT the hazard this ceiling describes — no
#: code is COMPUTED anywhere on that path; every one of its call sites passes a bare
#: literal, so nothing can be minted that a static reader cannot see. The scanner's
#: existing wrapper-following resolves ``json_response`` PAYLOAD wrappers and module-level
#: string constants, neither of which covers a forwarded code parameter, so the site reads
#: as dynamic despite being fully enumerable.
#:
#: The hole that indirection would otherwise open is closed one level up instead, by
#: ``tests/test_ea2_openai_dialect.py::test_every_dialect_error_code_is_a_registered_literal``
#: — it parses that module, asserts every ``openai_error`` call passes a literal, and
#: asserts each of those literals is in ``HTTP_ERROR_CODES``. That is the same guarantee
#: this ceiling protects, proven at the indirection level that actually exists. Raising
#: this number for a computed code would still be wrong.
_DYNAMIC_CODE_SITE_CEILING = 17


def test_every_released_code_is_still_present():
    """No released wire code may be removed — a client branching on it must not break."""
    missing = [c for c in _RELEASED if c not in HTTP_ERROR_CODES]
    assert not missing, f"released wire codes removed (append-only violation): {missing}"


def test_released_meanings_are_unchanged():
    """A released code's meaning is its contract — it is never reworded in place."""
    for code, meaning in _RELEASED.items():
        assert HTTP_ERROR_CODES[code] == meaning, (
            f"{code}: meaning changed (append-only violation). A released wire code is a "
            f"stable surface — add a NEW code instead of rewording an existing one."
        )


def test_all_codes_follow_the_lowercase_snake_convention():
    """lowercase_snake keeps wire codes disjoint from the agent ERR_UPPER_SNAKE space."""
    bad = [c for c in HTTP_ERROR_CODES if not _CODE_RE.match(c)]
    assert not bad, f"wire codes violate the lowercase_snake convention: {bad}"


def test_the_two_envelopes_stay_disjoint():
    """The case of a code is what tells a consumer which surface it belongs to.

    ``AgentError`` (``ERR_UPPER_SNAKE``, the carrier into an LLM session) and the wire
    envelope (``lowercase_snake``, what a branching HTTP client reads) are deliberately
    NOT merged. An overlapping key would make the two indistinguishable.
    """
    overlap = set(HTTP_ERROR_CODES) & set(ERROR_CODES)
    assert not overlap, f"a code appears in BOTH the wire and agent registries: {overlap}"
    assert all(c != c.upper() for c in HTTP_ERROR_CODES), "a wire code is UPPER_SNAKE"


def test_every_code_has_a_nonempty_meaning():
    empty = [c for c, m in HTTP_ERROR_CODES.items() if not (m and m.strip())]
    assert not empty, f"wire codes with an empty meaning: {empty}"


def test_every_emitted_code_is_registered():
    """The registry cannot fall behind the emitter.

    Walks every ``json_error`` call site in ``src/personalclaw`` and resolves its code
    statically (a literal, or one level of module-constant indirection). Every resolved
    code must have a registry row — that is what makes the registry the contract rather
    than a decorative list.
    """
    census = scan()
    assert len(census.emitter_sites) >= EMITTER_SITE_FLOOR, (
        f"this rail inspected only {len(census.emitter_sites)} json_error call sites, "
        f"below the {EMITTER_SITE_FLOOR} the census counted — the matcher stopped "
        f"matching, so a clean result here means nothing. Fix the scan before trusting it."
    )
    assert census.emitter_literal_codes, "no code resolved statically — the resolver is broken"
    unregistered = sorted(
        {
            (code, f"{f}:{ln}")
            for f, ln, code in census.emitter_literal_codes
            if code not in HTTP_ERROR_CODES
        }
    )
    assert not unregistered, (
        "these json_error codes have no HTTP_ERROR_CODES row. A wire code is a stable "
        "surface; register it (with its one-line meaning) in the same change that ships "
        "it:\n  " + "\n  ".join(f"{code}  ({site})" for code, site in unregistered)
    )


def test_dynamic_code_sites_do_not_grow():
    """An expression in the code slot is the one hole in the check above.

    A ``f"pack_refused_{exc.reason}"`` mints a wire code the static check cannot see.
    Sixteen such sites exist and are accounted for; a seventeenth is a new code
    entering unregistered, so the ceiling is the rail.
    """
    census = scan()
    assert len(census.emitter_sites) >= EMITTER_SITE_FLOOR, (
        f"inspected only {len(census.emitter_sites)} json_error sites (floor "
        f"{EMITTER_SITE_FLOOR}) — the matcher is broken, not the code."
    )
    assert len(census.emitter_dynamic_sites) <= _DYNAMIC_CODE_SITE_CEILING, (
        f"{len(census.emitter_dynamic_sites)} json_error sites compute their code "
        f"(ceiling {_DYNAMIC_CODE_SITE_CEILING}). Pass a literal so the registry check "
        f"can see it:\n  " + "\n  ".join(f"{f}:{ln}" for f, ln in census.emitter_dynamic_sites)
    )


def test_an_omitted_message_falls_back_to_the_registry_meaning():
    """The registry is load-bearing at runtime, not just documentation.

    The auth and device-pairing routes deliberately answer with a fixed sentence per
    code — never one derived from the request — so the response cannot be used to
    enumerate users or devices. That fixed sentence IS the registry row.
    """
    resp = json_error("auth_locked_out", status=429)
    assert resp.status == 429
    assert resp.body is not None
    body = resp.body.decode()
    assert '"code": "auth_locked_out"' in body
    assert HTTP_ERROR_CODES["auth_locked_out"] in body


def test_an_unregistered_code_still_emits_rather_than_raising():
    """A typo must degrade to a weaker message, never to a 500."""
    resp = json_error("not_a_registered_code", status=400)
    assert resp.status == 400
    assert resp.body is not None
    assert '"message": "not_a_registered_code"' in resp.body.decode()
