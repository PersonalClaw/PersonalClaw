"""Voice-profile + voice-binding HTTP handlers (MULTIMODAL-IO §1, §3).

CRUD over the :mod:`personalclaw.voice.profiles` entity store, the lock-from-history
transition, the consent record/verify/revoke trio, the per-surface binding map, and
one gated artifact read.

Three behaviours here are deliberate and tested rather than incidental:

* **Consent transitions are SEL-audited.** record / verify / revoke each append an
  ``api_access`` event with the profile id and the recomputed verdict — ids and
  outcomes only, never the consent text and never audio bytes.
* **Consent verification is a recompute, not a lookup.** ``POST …/consent/verify``
  re-derives the verdict from the artifacts on disk, so the endpoint's answer cannot
  be forged by editing the record.
* **Serving a cloned voice's audio is gated.** ``GET …/audio`` refuses a clone-kind
  profile whose consent is unverified (403 ``consent_required``), which is what makes
  revocation an actual block instead of a flag nobody reads.

Mutations broadcast typed WS events (``voice_profile_created`` / ``updated`` /
``locked`` / ``deleted``) through ``DashboardState.broadcast_ws`` — the same single
eventing surface chat uses.
"""

from __future__ import annotations

import logging

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.voice import bindings as vb
from personalclaw.voice import profiles as vp

logger = logging.getLogger(__name__)


def _sel():
    """Late-binding ``sel()`` for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg

    return _pkg.sel()


def _caller(request: web.Request) -> str:
    return str(request.get("user", "dashboard") or "dashboard")


def _broadcast(request: web.Request, event: str, profile: vp.VoiceProfile | None, **extra) -> None:
    state = request.app.get("state")
    if state is None:
        return
    payload: dict = dict(extra)
    if profile is not None:
        payload.update(vp.profile_payload(profile))
    try:
        state.broadcast_ws(event, payload)
    except Exception:
        logger.debug("voice profile broadcast failed for %s", event, exc_info=True)


async def _body(request: web.Request) -> dict:
    try:
        raw = await request.json()
    except Exception as exc:
        raise vp.VoiceProfileError("invalid JSON", 400, "invalid_json") from exc
    if not isinstance(raw, dict):
        raise vp.VoiceProfileError("JSON body must be an object", 400, "invalid_json")
    return raw


# ── CRUD ────────────────────────────────────────────────────────────────────


async def api_voice_profiles_list(request: web.Request) -> web.Response:
    """GET /api/voice/profiles — every profile plus the binding map."""
    return web.json_response(
        {
            "profiles": [vp.profile_payload(p) for p in vp.list_profiles()],
            "bindings": vb.load_bindings(),
        }
    )


async def api_voice_profile_create(request: web.Request) -> web.Response:
    """POST /api/voice/profiles {name, kind, provider, model, …}."""
    try:
        body = await _body(request)
        profile = vp.create_profile(**body)
    except vp.VoiceProfileError as exc:
        return json_error(exc.reason, message=exc.message, status=exc.status)
    _broadcast(request, "voice_profile_created", profile)
    return web.json_response(vp.profile_payload(profile), status=201)


async def api_voice_profile_get(request: web.Request) -> web.Response:
    """GET /api/voice/profiles/{id}."""
    try:
        profile = vp.require_profile(request.match_info["id"])
    except vp.VoiceProfileError as exc:
        return json_error(exc.reason, message=exc.message, status=exc.status)
    return web.json_response(vp.profile_payload(profile))


async def api_voice_profile_update(request: web.Request) -> web.Response:
    """PUT /api/voice/profiles/{id} — patch the mutable fields."""
    try:
        body = await _body(request)
        profile = vp.update_profile(request.match_info["id"], **body)
    except vp.VoiceProfileError as exc:
        return json_error(exc.reason, message=exc.message, status=exc.status)
    _broadcast(request, "voice_profile_updated", profile)
    return web.json_response(vp.profile_payload(profile))


async def api_voice_profile_delete(request: web.Request) -> web.Response:
    """DELETE /api/voice/profiles/{id} — record, artifacts, and any bindings."""
    pid = request.match_info["id"]
    try:
        existed = vp.delete_profile(pid)
        vb.forget_profile(pid)
    except vp.VoiceProfileError as exc:
        return json_error(exc.reason, message=exc.message, status=exc.status)
    if not existed:
        return web.json_response(
            {"error": "no such voice profile", "reason": "not_found"}, status=404
        )
    _broadcast(request, "voice_profile_deleted", None, id=pid)
    return web.json_response({"ok": True, "id": pid})


# ── lock-from-history ───────────────────────────────────────────────────────


async def api_voice_profile_lock(request: web.Request) -> web.Response:
    """POST /api/voice/profiles/{id}/lock {history_index} — pin seed + locked.wav."""
    try:
        body = await _body(request)
        profile = vp.lock_profile(request.match_info["id"], body.get("history_index", 0))
    except vp.VoiceProfileError as exc:
        return json_error(exc.reason, message=exc.message, status=exc.status)
    _broadcast(request, "voice_profile_locked", profile)
    return web.json_response(vp.profile_payload(profile))


async def api_voice_profile_unlock(request: web.Request) -> web.Response:
    """POST /api/voice/profiles/{id}/unlock — variation returns."""
    try:
        profile = vp.unlock_profile(request.match_info["id"])
    except vp.VoiceProfileError as exc:
        return json_error(exc.reason, message=exc.message, status=exc.status)
    _broadcast(request, "voice_profile_updated", profile)
    return web.json_response(vp.profile_payload(profile))


# ── consent (record / verify / revoke), SEL-audited ─────────────────────────


async def api_voice_profile_consent_record(request: web.Request) -> web.Response:
    """POST /api/voice/profiles/{id}/consent {consent_text}.

    The recording itself arrives through the resumable upload target
    ``voice_profile`` (``target_key: consent``); this endpoint records the statement
    and re-derives the verdict.
    """
    pid = request.match_info["id"]
    try:
        body = await _body(request)
        profile = vp.record_consent(pid, consent_text=str(body.get("consent_text") or ""))
    except vp.VoiceProfileError as exc:
        _sel().log_api_access(
            caller=_caller(request),
            operation="voice_profile.consent.record",
            outcome="denied",
            resources=pid,
            error=exc.reason,
        )
        return json_error(exc.reason, message=exc.message, status=exc.status)
    _sel().log_api_access(
        caller=_caller(request),
        operation="voice_profile.consent.record",
        outcome="success",
        resources=f"{pid} verified={profile.verified_own_voice}",
    )
    _broadcast(request, "voice_profile_updated", profile)
    return web.json_response(vp.profile_payload(profile))


async def api_voice_profile_consent_verify(request: web.Request) -> web.Response:
    """POST /api/voice/profiles/{id}/consent/verify — recompute from the artifacts."""
    pid = request.match_info["id"]
    try:
        profile = vp.require_profile(pid)
    except vp.VoiceProfileError as exc:
        return json_error(exc.reason, message=exc.message, status=exc.status)
    verified = vp.recompute_verified(profile)
    _sel().log_api_access(
        caller=_caller(request),
        operation="voice_profile.consent.verify",
        outcome="success" if verified else "denied",
        resources=f"{pid} verified={verified}",
    )
    return web.json_response(
        {
            "id": pid,
            "verified_own_voice": verified,
            "consent_recorded_at": profile.consent_recorded_at,
        }
    )


async def api_voice_profile_consent_revoke(request: web.Request) -> web.Response:
    """DELETE /api/voice/profiles/{id}/consent — delete the recording, clear the fields."""
    pid = request.match_info["id"]
    try:
        profile = vp.revoke_consent(pid)
    except vp.VoiceProfileError as exc:
        _sel().log_api_access(
            caller=_caller(request),
            operation="voice_profile.consent.revoke",
            outcome="denied",
            resources=pid,
            error=exc.reason,
        )
        return json_error(exc.reason, message=exc.message, status=exc.status)
    _sel().log_api_access(
        caller=_caller(request),
        operation="voice_profile.consent.revoke",
        outcome="success",
        resources=f"{pid} verified={profile.verified_own_voice}",
    )
    _broadcast(request, "voice_profile_updated", profile)
    return web.json_response(vp.profile_payload(profile))


# ── gated artifact read ─────────────────────────────────────────────────────


async def api_voice_profile_audio(request: web.Request) -> web.StreamResponse:
    """GET /api/voice/profiles/{id}/audio?artifact=ref_audio|locked.

    The consent gate's teeth: a clone-kind profile must be verified for its audio to
    leave the store, so revoking consent stops the bytes rather than only flipping a
    field. An abandoned partial upload has no artifact on disk, so it 404s here —
    never served as a complete reference clip.
    """
    pid = request.match_info["id"]
    artifact = str(request.query.get("artifact") or vp.READABLE_ARTIFACTS[0])
    try:
        profile = vp.require_profile(pid)
        vp.assert_artifact_release_allowed(profile, artifact)
        rel = "locked.wav" if artifact == "locked" else profile.ref_audio
        if not rel:
            raise vp.VoiceProfileError("no such artifact", 404, "artifact_missing")
        path = vp.artifact_path(pid, rel)
    except vp.VoiceProfileError as exc:
        return json_error(exc.reason, message=exc.message, status=exc.status)
    if not path.is_file():
        return web.json_response(
            {"error": "no such artifact", "reason": "artifact_missing"}, status=404
        )
    return web.FileResponse(path)


# ── per-surface bindings ────────────────────────────────────────────────────


async def api_voice_bindings_get(request: web.Request) -> web.Response:
    """GET /api/voice/bindings — the surface → profile map."""
    return web.json_response({"bindings": vb.load_bindings()})


async def api_voice_bindings_put(request: web.Request) -> web.Response:
    """PUT /api/voice/bindings {surface, profile_id} — bind one surface.

    A clone-kind profile bound to an agentic/off-machine surface without verified
    consent returns a ``warning`` (§1.3 warns, never blocks: local synthesis is not
    an ethics checkpoint).
    """
    try:
        body = await _body(request)
        surface = str(body.get("surface") or "")
        pid = str(body.get("profile_id") or "")
        bindings = vb.set_binding(surface, pid)
        profile = vp.require_profile(pid)
    except vp.VoiceProfileError as exc:
        return json_error(exc.reason, message=exc.message, status=exc.status)
    return web.json_response(
        {
            "bindings": bindings,
            "warning": vb.binding_warning(profile, surface),
        }
    )


async def api_voice_bindings_delete(request: web.Request) -> web.Response:
    """DELETE /api/voice/bindings?surface=… — unbind one surface."""
    try:
        bindings = vb.clear_binding(str(request.query.get("surface") or ""))
    except vp.VoiceProfileError as exc:
        return json_error(exc.reason, message=exc.message, status=exc.status)
    return web.json_response({"bindings": bindings})


# ── one-click migration (§6, never automatic) ───────────────────────────────


async def api_voice_migrate(request: web.Request) -> web.Response:
    """POST /api/voice/migrate {name?} — profile from the current voice, then default.

    The explicit action §6 requires: it captures the active flat TTS selection into a
    new design-kind profile and binds it ``default``. Nothing calls this on a
    startup/first-run path, so migration only ever happens on a user's click. An empty
    body is fine — the button carries no fields beyond an optional name.
    """
    from personalclaw.voice import migration as vm

    try:
        raw = await request.json()
    except Exception:
        raw = {}
    name = str(raw.get("name") or "") if isinstance(raw, dict) else ""
    try:
        profile = vm.migrate_active_to_default_profile(name=name)
    except vp.VoiceProfileError as exc:
        _sel().log_api_access(
            caller=_caller(request),
            operation="voice_profile.migrate",
            outcome="denied",
            resources="active_tts",
            error=exc.reason,
        )
        return json_error(exc.reason, message=exc.message, status=exc.status)
    _sel().log_api_access(
        caller=_caller(request),
        operation="voice_profile.migrate",
        outcome="success",
        resources=f"{profile.id} provider={profile.provider}",
    )
    _broadcast(request, "voice_profile_created", profile, migrated=True)
    return web.json_response(vp.profile_payload(profile), status=201)


async def api_voice_resolve(request: web.Request) -> web.Response:
    """GET /api/voice/resolve?surface=…[&profile_id=…] — which level wins, and why.

    The resolver made legible: the FE bindings table shows the effective voice per
    surface without re-implementing the precedence chain client-side.
    """
    from personalclaw.tts.registry import active_voice_params

    surface = str(request.query.get("surface") or "")
    explicit = str(request.query.get("profile_id") or "")
    try:
        params = active_voice_params(surface=surface, profile_id=explicit)
    except vp.VoiceProfileError as exc:
        return json_error(exc.reason, message=exc.message, status=exc.status)
    if params is None:
        return web.json_response({"surface": surface, "resolved": False, "level": vb.LEVEL_BUILTIN})
    provider = params.get("provider")
    return web.json_response(
        {
            "surface": surface,
            "resolved": True,
            "level": params.get("profile_level", vb.LEVEL_BUILTIN),
            "profile_id": params.get("profile_id", ""),
            "provider": getattr(provider, "name", ""),
            "voice": params.get("voice", ""),
            "speed": params.get("speed", 1.0),
            "seed": params.get("seed", 0),
            "locked": params.get("locked", False),
            # Path presence, not the path: the FE only needs to know a clip is there.
            "has_ref_audio": bool(params.get("ref_audio", "")),
        }
    )
