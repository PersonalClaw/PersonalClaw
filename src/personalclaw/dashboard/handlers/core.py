"""Core handlers — page serving, branding, STT transcribe, config, SEL, auth, session workspace."""

import asyncio
import hmac
import json
import logging
import os
from pathlib import Path

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

import personalclaw.validation as _validation_mod
from personalclaw.atomic_write import atomic_write
from personalclaw.config.edit_spec import (
    ConfigValueError,
    app_write_refusal,
    coerce_edit_value,
    unconsented_loosening,
)
from personalclaw.config.editable import _EDITABLE_CONFIG
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.state import DashboardState
from personalclaw.dashboard.token_auth import MAX_SESSION_TTL_SECS, generate_token, parse_duration
from personalclaw.http_errors import consent_required, json_error
from personalclaw.request_validation import json_object_body
from personalclaw.safety_flags import confirm_granted
from personalclaw.security import SUSPICIOUS_BASH_PATTERNS

logger = logging.getLogger(__name__)

_DIST_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "dist"

# The composer mic-recording transcribe cap: a short voice clip (~25 MB ≈ 30+ min
# of speech), deliberately far below the audio-file-upload category so a runaway
# recording can't fill disk. Large audio FILES transcribe via the Files/Knowledge
# upload path + the ffmpeg-segmented STT flow, not this endpoint.
_STT_MIC_CAP_BYTES = 25 * 1024 * 1024


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811 — circular import

    return _pkg.sel()


# ── Page ──

_UNBUNDLED_PAGE = """\
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PersonalClaw — Build the dashboard</title>
<style>
*{box-sizing:border-box;margin:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(145deg,#0f172a 0%,#1e293b 50%,#0f172a 100%);
  color:#e2e8f0;padding:32px}
.card{max-width:540px;width:100%;background:rgba(30,41,59,.85);
  border:1px solid rgba(148,163,184,.15);border-radius:20px;
  padding:48px 40px;backdrop-filter:blur(12px);
  box-shadow:0 25px 50px -12px rgba(0,0,0,.5)}
.icon{width:64px;height:64px;margin:0 auto 24px;display:flex;
  align-items:center;justify-content:center;
  background:linear-gradient(135deg,#6366f1,#8b5cf6);
  border-radius:16px;box-shadow:0 8px 24px rgba(99,102,241,.3)}
.icon svg{width:32px;height:32px;fill:none;stroke:#fff;stroke-width:2;
  stroke-linecap:round;stroke-linejoin:round}
h1{font-size:1.5rem;font-weight:700;text-align:center;margin-bottom:8px;
  background:linear-gradient(135deg,#c7d2fe,#e0e7ff);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.sub{text-align:center;color:#94a3b8;font-size:.925rem;margin-bottom:32px}
.steps{display:flex;flex-direction:column;gap:12px}
.step{display:flex;align-items:flex-start;gap:12px;
  background:rgba(15,23,42,.6);border:1px solid rgba(148,163,184,.1);
  border-radius:12px;padding:14px 16px;transition:border-color .2s}
.step:hover{border-color:rgba(99,102,241,.4)}
.num{width:24px;height:24px;border-radius:50%;display:flex;
  align-items:center;justify-content:center;font-size:.75rem;
  font-weight:700;background:rgba(99,102,241,.2);color:#a5b4fc;flex-shrink:0}
.step-body{flex:1;min-width:0}
.step-title{font-weight:600;font-size:.875rem;margin-bottom:2px}
.step-cmd{font-family:'SF Mono',Menlo,monospace;font-size:.8rem;
  color:#a5b4fc;background:rgba(99,102,241,.08);border-radius:6px;
  padding:6px 10px;margin-top:6px;display:inline-block;letter-spacing:-.01em}
.note{text-align:center;color:#64748b;font-size:.8rem;margin-top:28px}
.note a{color:#818cf8;text-decoration:none}
.note a:hover{text-decoration:underline}
.pulse{animation:pulse 2s cubic-bezier(.4,0,.6,1) infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
</style></head><body>
<div class="card">
  <div class="icon">
    <svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5z"/>
    <path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
  </div>
  <h1>PersonalClaw dashboard isn't built yet</h1>
  <p class="sub">The gateway is running <span class="pulse">●</span> &mdash;
  build the web UI to get started.</p>
  <div class="steps">
    <div class="step">
      <div class="num">1</div>
      <div class="step-body">
        <div class="step-title">Install dependencies</div>
        <code class="step-cmd">cd web &amp;&amp; npm install</code>
      </div>
    </div>
    <div class="step">
      <div class="num">2</div>
      <div class="step-body">
        <div class="step-title">Build the dashboard</div>
        <code class="step-cmd">npm run build</code>
      </div>
    </div>
    <div class="step">
      <div class="num">3</div>
      <div class="step-body">
        <div class="step-title">Reload this page</div>
        <code class="step-cmd">⌘R or F5</code>
      </div>
    </div>
  </div>
  <p class="note">Or install from a
    <a href="https://github.com/PersonalClaw/PersonalClaw/releases">release</a>
  that bundles the dashboard pre-built.</p>
</div>
</body></html>"""


async def index(request: web.Request) -> web.Response:
    """Serve the React dashboard HTML."""
    react_index = _DIST_DIR / "index.html"
    if not react_index.is_file():
        return web.Response(
            text=_UNBUNDLED_PAGE,
            content_type="text/html",
            status=503,
        )
    html = react_index.read_text(encoding="utf-8")
    # Safe-surfaces mode (§6) is stamped into the document itself, so the SPA knows its
    # layer ceiling BEFORE the first app module loads. A fetch would land after that
    # decision, which would make `--safe-surfaces` advisory rather than a recovery mode.
    from personalclaw.surface_layers import inject_safe_meta

    html = inject_safe_meta(html)
    # A `?token=` link: the token middleware sets the session cookie on THIS response, so by
    # the time the document runs the URL copy of the credential is only something to leak.
    # The scrub is inlined first in <head> — ahead of every resource declaration, and
    # independent of whether the app bundle then loads at all.
    if request.query.get("token"):
        from personalclaw.dashboard.owner_token_url import inject_scrub

        html = inject_scrub(html)
    return web.Response(text=html, content_type="text/html")


async def favicon(request: web.Request) -> web.StreamResponse:
    """Serve /claw.svg — the favicon index.html declares. Dist-root files have no
    static route (only /assets, /fonts, …), so without this the request fell
    through to the SPA fallback and the "icon" came back as index.html HTML."""
    path = _DIST_DIR / "claw.svg"
    if path.is_file():
        return web.FileResponse(path)
    raise web.HTTPNotFound()


def _dist_root_file(name: str, content_type: str) -> web.StreamResponse:
    """Serve a dist-ROOT file with an explicit content type.

    Two things here are load-bearing for the PWA (MOBILE-COMPANION T3.1):

    * **The content type is stated, never guessed.** ``.webmanifest`` is absent from
      Python's ``mimetypes`` table on a stock install, so ``FileResponse`` would send
      ``application/octet-stream`` and the browser would discard the manifest.
    * **A missing file returns a 404 *response*, it does not raise.** ``spa_fallback``
      converts a raised ``HTTPNotFound`` into ``index.html``, and HTML served for
      ``/sw.js`` fails registration on a MIME check while HTML served for the manifest
      fails to parse — both silently, with the app still looking fine. Returning the
      status directly bypasses that middleware entirely.
    """
    path = _DIST_DIR / name
    if path.is_file():
        return web.FileResponse(path, headers={"Content-Type": content_type})
    return web.Response(status=404, text=f"{name} not built", content_type="text/plain")


async def manifest_webmanifest(request: web.Request) -> web.StreamResponse:
    """Serve /manifest.webmanifest — the PWA manifest index.html declares.

    Stays behind session auth (it is not in ``token_auth._BYPASS_*``), which is why
    index.html declares the link with ``crossorigin="use-credentials"``.
    """
    return _dist_root_file("manifest.webmanifest", "application/manifest+json")


async def service_worker(request: web.Request) -> web.StreamResponse:
    """Serve /sw.js — the service worker, from the dist ROOT.

    The path is the scope: a worker served from ``/assets/`` could only control
    ``/assets/``, so this one must stay at the origin root to control the SPA.
    """
    return _dist_root_file("sw.js", "text/javascript")


# Web-font content types, stated explicitly (issue #2916). aiohttp's ``FileResponse``
# resolves the type from its OWN private ``mimetypes`` table
# (``web_fileresponse.CONTENT_TYPES``), which lacks the woff/woff2 entries and does not
# consult ``mimetypes.add_type`` — so a plain static route emits
# ``application/octet-stream``. Same "state the type, never guess" contract that
# ``_dist_root_file`` applies to the PWA root files.
_FONT_CONTENT_TYPES: dict[str, str] = {
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}


async def font_asset(request: web.Request) -> web.StreamResponse:
    """Serve /fonts/<name> with an explicitly stated Content-Type.

    ``fonts.css`` references these at the absolute path ``/fonts/*.woff2``. Served
    through ``add_static``, aiohttp's ``FileResponse`` defaults ``.woff2`` to
    ``application/octet-stream`` (see ``_FONT_CONTENT_TYPES`` above) — an incorrect
    Content-Type for a first-party asset that diverges from the project's own tested
    convention and bites under a stricter proxy/CDN or a ``nosniff``-tightening.

    A missing (or non-font) file RETURNS 404, it does not raise: ``spa_fallback`` turns
    a raised ``HTTPNotFound`` for a non-``/fonts/``-excluded GET into ``index.html``, and
    HTML decoded as a font is the "invalid sfntVersion" failure this route exists to
    prevent. Returning the status directly bypasses that middleware.
    """
    name = request.match_info["name"]
    fonts_dir = (_DIST_DIR / "fonts").resolve()
    path = (fonts_dir / name).resolve()
    # Containment guard: ``{name}`` is a single URL-decoded segment, so reject anything
    # that resolves outside the fonts directory before touching the filesystem.
    if fonts_dir not in path.parents or not path.is_file():
        return web.Response(status=404, text="font not found", content_type="text/plain")
    content_type = _FONT_CONTENT_TYPES.get(path.suffix.lower())
    if content_type is None:
        return web.Response(status=404, text="unsupported font type", content_type="text/plain")
    return web.FileResponse(path, headers={"Content-Type": content_type})


# ── STT (Speech-to-Text) ──


async def api_stt_transcribe(request: web.Request) -> web.Response:
    """POST /api/stt/transcribe — transcribe uploaded audio via the active STT model.

    Two duplex-loop behaviors ride on this endpoint (MULTIMODAL-IO §4). Both keyed
    off the query string, because the body is a streamed multipart upload whose
    first part must stay the audio:

    * ``?duplex=true&session=<key>`` — a hands-free capture. The transcript is
      checked against the last text spoken for that session; speaker bleed comes
      back as ``{"text": "", "filtered": "echo"}`` so the dashboard can say why
      nothing happened instead of looking deaf.
    * The response carries ``input_origin: "voice"`` and, when the disclaimer is
      enabled, the line the frontend submits with the turn (§4.4).
    """
    import tempfile  # noqa: F811

    from personalclaw.transcribe import is_available, transcribe_audio_detailed  # noqa: F811
    from personalclaw.voice.duplex import VOICE_DISCLAIMER, is_echo

    if not await is_available():
        return web.json_response({"error": "STT not available"}, status=503)

    ctype = request.headers.get("Content-Type", "")
    if not ctype.lower().startswith("multipart/"):
        return web.json_response(
            {"error": "multipart/form-data with an 'audio' field is required"},
            status=400,
        )
    try:
        reader = await request.multipart()
    except (ValueError, AssertionError, RuntimeError) as exc:
        return web.json_response(
            {"error": f"failed to parse multipart body: {exc}"},
            status=400,
        )
    field = await reader.next()
    if field is None or not hasattr(field, "name") or field.name != "audio":  # type: ignore[union-attr]  # noqa: E501
        return web.json_response({"error": "missing audio field"}, status=400)

    # Use uploaded filename extension (recording.webm / .mp4 / .ogg)
    fname = getattr(field, "filename", None) or "recording.webm"
    ext = os.path.splitext(fname)[1] or ".webm"
    # This is the composer's mic-recording transcribe path — a short voice clip,
    # NOT a large-audio-file upload (those go through Files/Knowledge and get the
    # ffmpeg-segmented STT path). Cap it well below the audio-upload category via
    # the shared policy's per-surface override so a runaway mic blob can't fill disk.
    from personalclaw.uploads import check_upload

    _stt_cap = _STT_MIC_CAP_BYTES
    field_mime = (getattr(field, "headers", {}) or {}).get("Content-Type") or None
    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        os.close(fd)
        size = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = await field.read_chunk(8192)  # type: ignore[union-attr]
                if not chunk:
                    break
                size += len(chunk)
                if size > _stt_cap:
                    return web.json_response(
                        {
                            "error": check_upload(
                                fname, field_mime, size=size, override_limit=_stt_cap
                            ).reason
                        },
                        status=413,
                    )
                f.write(chunk)

        # Lexicon wiring: the mic is a transcription surface like any other, so it
        # gets the same two Lexicon halves knowledge ingestion has — bias the
        # decoder toward the user's terms before decoding, and run the
        # learned-corrections pass after. Both halves are best-effort: a Lexicon
        # failure must never break dictation.
        bias_terms: list[str] | None = None
        try:
            from personalclaw.lexicon import select_bias_terms  # noqa: F811

            bias_terms = (await select_bias_terms()) or None
        except Exception:
            logger.debug("lexicon bias-term selection failed (non-fatal)", exc_info=True)

        result = await transcribe_audio_detailed(tmp, bias_terms=bias_terms)
        if result is not None and result.segments:
            try:
                from personalclaw.lexicon import get_lexicon_service  # noqa: F811

                svc = get_lexicon_service()
                if svc.store.count_terms() > 0:
                    svc.correct(result)
            except Exception:
                logger.debug("lexicon correction failed (non-fatal)", exc_info=True)
        text = result.text if result is not None else None
        # The redaction below MUST stay downstream of correction: correct()
        # re-derives the flat text from raw segment words, which would undo any
        # redaction applied further up the transcribe layer.
        if text:
            from personalclaw.security import (  # noqa: F811
                redact_credentials,
                redact_exfiltration_urls,
            )

            text, _ = redact_exfiltration_urls(text)
            text, _ = redact_credentials(text)
        text = text or ""

        cfg = AppConfig.load().voice
        duplex = str(request.query.get("duplex", "")).strip().lower() in ("1", "true", "yes")
        if duplex and text and cfg.echo_filter_enabled:
            spoken = request.app["state"].last_spoken(request.query.get("session", ""))
            if spoken and is_echo(text, spoken):
                return web.json_response({"text": "", "filtered": "echo"})

        payload: dict[str, object] = {"text": text, "input_origin": "voice"}
        if text and cfg.voice_disclaimer_enabled:
            payload["disclaimer"] = VOICE_DISCLAIMER
        return web.json_response(payload)
    except Exception:
        logger.exception("STT transcribe failed")
        return web.json_response({"error": "transcription failed"}, status=500)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── Security Event Log API ──


async def api_sel_rotate(request: web.Request) -> web.Response:
    """POST /api/sel/rotate — archive existing SEL log and start a fresh chain.

    Recovers from a broken HMAC chain. The previous log file moves into ``sel_archive/``
    under a UTC timestamp (where retention and snapshots cover it) unless
    ``{"archive": false}`` is sent. An archive that could not be written leaves the log where
    it was and answers ``sel_archive_failed``: a 200 there rendered as "a fresh chain has
    started" when nothing had changed.
    """
    body = await json_object_body(request)
    archive = body.get("archive") is not False
    result = _sel().rotate(archive=archive)
    if not result.get("rotated"):
        return json_error("sel_archive_failed", status=500)
    return web.json_response(result)


async def api_security_stats(_request: web.Request) -> web.Response:
    """GET /api/security/stats — live security feature counts."""
    from personalclaw.security import denied_command_patterns

    denied = len(denied_command_patterns())

    # Tools, not constants: the panel's hint says "Tools with enforced argument
    # validation", so the number must come from the enforcement maps themselves
    # (issue 592 — the old dir() sweep over *_SCHEMA names both undercounted the
    # gated set and read as a coverage figure it wasn't).
    schemas = len(_validation_mod.validated_tool_names())

    # 5 output paths where redaction is applied (architectural constant from
    # security-deep-dive.md): dashboard streaming mid-flush, dashboard streaming
    # trailing, dashboard non-chunk messages, dashboard history save, channel final.
    return web.json_response(
        {
            "denied_commands": denied,
            "suspicious_patterns": len(SUSPICIOUS_BASH_PATTERNS),
            "tool_schemas": schemas,
            "redaction_paths": 5,
        }
    )


async def api_security_denied_commands(_request: web.Request) -> web.Response:
    """GET /api/security/denied-commands — the bash denylist for the Security panel.

    ``builtin`` is the packaged baseline: always-on, read-only, and served with the
    ``baseline`` block the panel needs to say *which* baseline is in force — its
    ``version``, the ``sha256`` captured at import, how many patterns that covers, and
    whether the packaged file on disk still matches (``verified``). A file that has
    diverged is reported, not adopted, so ``count`` stays the number actually enforced.

    ``user_additions`` is the number of user patterns that genuinely *widen* the
    effective set, derived as ``len(effective) - len(baseline)`` rather than by
    counting the config list, because a user entry equal to a built-in is deduped away
    by :func:`denied_command_patterns` and adds nothing. ``user`` is still the raw
    editable list, persisted at ``security.denied_commands`` (edit via PATCH
    /api/config/personalclaw); the baseline has no write path at all.

    Reading this re-verifies the baseline, so viewing the panel while the packaged file
    is diverged writes the same SEL ``baseline_denylist_tamper_attempt`` the periodic
    doctor probe writes. That is deliberate: an owner looking at a diverged baseline is
    an auditable event.
    """
    from personalclaw.security import (
        baseline_denied_command_patterns,
        denied_command_patterns,
        verify_baseline_denylist,
    )

    user = list(AppConfig.load().security.denied_commands)
    report = verify_baseline_denylist()
    baseline = list(baseline_denied_command_patterns())
    effective = denied_command_patterns()
    return web.json_response(
        {
            "builtin": baseline,
            "user": user,
            "baseline": {
                "version": report["version"],
                "sha256": report["sha256"],
                "count": report["count"],
                "verified": report["file_verified"],
                "detail": report["detail"],
            },
            "user_additions": len(effective) - len(baseline),
        }
    )


async def api_security_egress(_request: web.Request) -> web.Response:
    """GET /api/security/egress — the operator's outbound-egress overrides for the
    Security panel. Defaults (public-only, no allow/deny) are enforced in code; these
    are the self-hoster's relaxations, edited via PATCH /api/config/personalclaw
    ``security.egress``."""
    eg = AppConfig.load().security.egress
    return web.json_response(
        {
            "allow_hosts": list(eg.allow_hosts),
            "deny_hosts": list(eg.deny_hosts),
            "allow_private": bool(eg.allow_private),
        }
    )


async def api_security_outside_home(_request: web.Request) -> web.Response:
    """GET /api/security/outside-home — the places outside the home it may be allowed to read.

    Each place says whether it is allowed. Written through PATCH /api/config/personalclaw
    ``security.outside_home``, which asks the owner to confirm an addition.

    ``allowed`` is the whole saved list, which can name a place no longer offered (the sign-in
    of an app since removed), so a write that changes one place keeps the others as they are."""
    from personalclaw import outside_home

    allowed = outside_home.allowed_ids()
    return web.json_response(
        {
            "places": [p.to_dict(allowed=p.id in allowed) for p in outside_home.places()],
            "allowed": sorted(allowed),
        }
    )


# ── PersonalClaw Config API ──
#: The three `agent.*` fields `PUT /api/config/personalclaw` owns. Their bounds are NOT restated
#: here: all three are declared in `_EDITABLE_CONFIG`, so this used to be a second copy of the
#: same numbers — identical, one edit away from disagreeing. None is a security control, and
#: `tests/test_security_posture_rail.py` keeps it that way: this path carries neither the app
#: refusal nor the consent check, which live on the PATCH, the one writer of those fields.
_AGENT_PUT_FIELDS = ("subagent_max_turns", "max_subagents", "orchestrator_skill")


def _app_config_fields(app_name: str) -> list[str]:
    """The settings the app *app_name* declared in ``permissions.config`` — ``[]`` when none, or
    when its manifest cannot be read (the middleware already refused that case)."""
    from personalclaw.apps.permissions import checker_for

    checker = checker_for(app_name)
    return list(checker.permissions.config) if checker is not None else []


def _app_config_refusal(app_name: str, field: str, operation: str) -> web.Response:
    """``403 config_field_not_declared`` + an SEL row naming the app and the setting.

    ``permissions.api: ["/api/config"]`` reaches the route and says nothing about WHICH setting
    — the same prefix-says-nothing-about-power problem #3602 met for writes, here for reads as
    well: the config holds the owner's whole posture, where their notifications are delivered
    and where their memory vault lives. ``permissions.config`` is the list install consent
    showed, so it is the list an app reaches.
    """
    _sel().log_api_access(
        caller=f"app:{app_name}",
        operation=operation,
        outcome="denied",
        source="app_permissions",
        resources=field,
        error="setting not declared in permissions.config",
    )
    return json_error(
        "config_field_not_declared",
        message=(
            f"{field} is not in this app's permissions.config — declare it in the manifest, "
            "where install consent shows it"
        ),
        status=403,
    )


def _declared_settings(full: dict, fields: list[str]) -> dict:
    """*full* (``AppConfig.to_dict()``) pruned to the dotted *fields*, keeping its nesting, so an
    app reads ``voice.echo_filter_enabled`` at the same place the owner's full read has it."""
    picked: dict = {}
    for dotted in fields:
        node: object = full
        parts = dotted.split(".")
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                break
            node = node[part]
        else:
            cursor = picked
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = node
    return picked


async def api_personalclaw_config(request: web.Request) -> web.Response:
    """GET/PUT /api/config/personalclaw — read or update PersonalClaw config."""
    from personalclaw.config.loader import config_path  # noqa: F811

    if request.method == "PUT":
        caller = request.get("user", "dashboard")

        def _deny(error: str, status: int = 400) -> web.Response:
            _sel().log_api_access(
                caller=caller,
                operation="config.update",
                outcome="denied",
                error=error,
            )
            return web.json_response({"error": error}, status=status)

        try:
            body = await request.json()
        except Exception:
            return _deny("invalid JSON")
        if not isinstance(body, dict):
            return _deny("JSON body must be an object")
        agent_settings = body.get("agent")
        if not isinstance(agent_settings, dict):
            return _deny("agent must be an object")
        agent_fields = _AGENT_PUT_FIELDS
        # An unrecognised key used to be dropped in silence whenever at least one recognised
        # key rode along, so `{"max_subagents": 4, "subagent_max_tunrs": 999}` returned 200
        # and applied half of what was asked.
        unknown = sorted(k for k in agent_settings if k not in agent_fields)
        if unknown:
            return _deny(
                f"unknown agent settings: {', '.join(unknown)} "
                f"(writable: {', '.join(agent_fields)})"
            )
        # An app writes only the settings its manifest declares — none of these three is a
        # security setting (`test_the_put_endpoint_writes_no_security_setting`), so the
        # declaration is the whole rule here.
        app_name = request.get("app", "")
        if app_name:
            declared = set(_app_config_fields(app_name))
            for key in agent_settings:
                if f"agent.{key}" not in declared:
                    return _app_config_refusal(app_name, f"agent.{key}", "config.update")
        # Coerce BEFORE taking the lock, into a staging dict. Validation depends only on the
        # request body and `_EDITABLE_CONFIG`, so holding the lock across it would serialise
        # every rejected request behind whoever is writing, for no benefit — and a test asserts
        # a refused PUT answers 400 while the lock is held.
        staged: dict[str, object] = {}
        for key in agent_fields:
            if key not in agent_settings:
                continue
            try:
                staged[key] = coerce_edit_value(
                    f"agent.{key}", agent_settings[key], _EDITABLE_CONFIG[f"agent.{key}"]
                )
            except ConfigValueError as exc:
                # The message names the field: this endpoint can carry several at once, so
                # a bare "must be between 0 and 16" would not say which one was refused.
                return _deny(f"{key} {exc}", exc.status)
        if not staged:
            return _deny("no recognized settings provided")
        applied = list(staged)

        # 🔴 The SAME lock PATCH holds. Both endpoints do read-current-config → mutate a field →
        # write-the-whole-file, and `atomic_write` only guarantees the file is never half-written
        # — not that a concurrent modifier's change survives. Interleaved, the later writer's
        # read predates the earlier writer's write, so it serialises a `data` that never saw it
        # and one field silently reverts (#754). PUT took no lock at all.
        #
        # Spans the READ as well as the write, deliberately: locking only `atomic_write` would
        # still let two handlers read the same base and both write a complete file, which IS the
        # lost update. The critical section is exactly read → apply → write and nothing else.
        from personalclaw.dashboard.handlers.agents import _get_config_lock  # noqa: F811

        path = config_path()
        async with _get_config_lock():
            try:
                data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            except Exception:
                _sel().log_api_access(
                    caller=caller,
                    operation="config.update",
                    outcome="error",
                    error="config.json is corrupt",
                )
                return web.json_response({"error": "config.json is corrupt"}, status=500)
            if not isinstance(data.get("agent"), dict):
                data["agent"] = {}
            agent = data["agent"]
            agent.update(staged)
            atomic_write(path, json.dumps(data, indent=2) + "\n", fsync=True)
        _sel().log_api_access(
            caller=caller,
            operation="config.update",
            outcome="ok",
            resources=",".join(applied),
        )
        # Regenerate or clean up orchestrator skill on toggle.
        if "orchestrator_skill" in applied:
            if agent.get("orchestrator_skill"):
                from personalclaw.dashboard.handlers.agents import _regen_orchestrator  # noqa: F811

                _regen_orchestrator()
            else:
                # Clean up both the current orchestrator/ and the pre-rename
                # conductor/ always-loaded skill dirs.
                try:
                    from personalclaw.skills import SkillsLoader  # noqa: F811

                    for legacy in ("orchestrator", "conductor"):
                        p = SkillsLoader()._dir / legacy / "SKILL.md"
                        if p.exists():
                            p.unlink()
                except Exception:
                    logger.exception("Failed to clean up orchestrator skill")
        return web.json_response({"ok": True})

    full = AppConfig.load().to_dict()
    # 🔴 The full config is the OWNER's read. An app declaring `/api/config` reached this route
    # and was handed every setting — the whole posture, the ntfy topic URL that delivers the
    # owner's notifications, the vault paths — while the reference docs said "owner-only". An
    # app now reads the fields its manifest declares in `permissions.config`, nested as the
    # owner's read nests them, and one that declared none is refused.
    app_name = request.get("app", "")
    if app_name:
        fields = _app_config_fields(app_name)
        if not fields:
            return _app_config_refusal(app_name, "permissions.config", "config.read")
        full = _declared_settings(full, fields)
    return web.json_response(full)


def _value_in_effect(path_key: str) -> object:
    """What *path_key* holds right now as the runtime reads it — defaults included.

    Read through `AppConfig.load()` rather than off the raw file, because "does this write
    loosen the control?" is a question about the value in effect: an absent key IS its default,
    and a hand-edited value `load()` normalises is the normalised one. ``None`` when the path
    does not resolve, which every `loosens` rule reads as "cannot prove this is not looser".
    """
    node: object = AppConfig.load().to_dict()
    for part in path_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


async def api_personalclaw_config_patch(request: web.Request) -> web.Response:
    """PATCH /api/config/personalclaw — update a single config field."""
    from personalclaw.config.loader import config_path  # noqa: F811

    caller = request.get("user")
    if not caller:
        logger.warning(
            "config.patch called without authenticated user; falling back to 'dashboard'"
        )
        caller = "dashboard"

    def _log_sel(outcome: str, resources: str) -> None:
        _sel().log_api_access(
            caller=caller,
            operation="config.patch",
            outcome=outcome,
            source="dashboard",
            resources=resources,
        )

    def _deny(msg: str, resources: str = "", status: int = 400) -> web.Response:
        _log_sel("denied", resources or msg)
        return web.json_response({"error": msg}, status=status)

    try:
        body = await request.json()
    except Exception:
        return _deny("invalid JSON", "invalid JSON body")
    if not isinstance(body, dict):
        return _deny("JSON body must be an object", "non-dict body")

    # `path` is the field SELECTOR, not a value, so a missing or unusable one is a malformed
    # REQUEST — a different failure from "that field exists but is not editable", and it needs
    # its own arm. Falling through to the allowlist check produced `field not editable: ` with
    # a blank field name, which reads as a truncated string and made a forgotten `path`
    # indistinguishable from a genuine unknown-field rejection (#2926). A non-string `path` was
    # worse than illegible: `dict.get(["a"])` raises on an unhashable key, so the request-shape
    # boundary caught the TypeError and answered a generic `bad_request` that named nothing.
    path_key = body.get("path")
    value = body.get("value")
    if path_key is None:
        return _deny(
            "missing required 'path' (the config field to edit, e.g. agent.yolo)", "missing path"
        )
    if not isinstance(path_key, str):
        return _deny(
            "'path' must be a string naming the config field to edit",
            f"non-string path ({type(path_key).__name__})",
        )
    if not path_key.strip():
        return _deny(
            "'path' is empty — name the config field to edit, e.g. agent.yolo", "empty path"
        )
    spec = _EDITABLE_CONFIG.get(path_key)
    if not spec:
        return _deny(f"field not editable: {path_key}", f"{path_key}={value}")

    # 🔴 AN APP CAN NEVER WRITE A SECURITY SETTING. `permissions.api: ["/api/config"]` is a path
    # prefix: it let an app PATCH `agent.yolo: true` — every tool call auto-approved — or turn the
    # 2FA requirement off, and `confirm: true` did not stop it, because anything holding a session
    # can send the flag. Decided on WHO asks and WHICH field only (`edit_spec.app_write_refusal`),
    # before the value is validated, so a refused app learns nothing about either.
    app_name = request.get("app", "")
    refused = app_write_refusal(path_key, spec, app_name)
    if refused:
        _sel().log_api_access(
            caller=f"app:{app_name}",
            operation="config.patch",
            outcome="denied",
            source="app_permissions",
            resources=path_key,
            error="security setting is owner-only",
        )
        return json_error("security_setting_owner_only", message=refused, status=403)
    # An ordinary setting is still the app's only if its manifest names it — the list install
    # consent showed. Checked after the security refusal so a security setting is refused as
    # what it is, not as merely undeclared (a manifest cannot declare one: validate refuses it).
    if app_name and path_key not in _app_config_fields(app_name):
        return _app_config_refusal(app_name, path_key, "config.patch")

    # Validate value. The rules live in `config/edit_spec.py` because three other write
    # paths need exactly these ones — see that module for why they are one function.
    try:
        value = coerce_edit_value(path_key, value, spec)
    except ConfigValueError as exc:
        return _deny(str(exc), exc.resources, exc.status)

    # Read, update, write
    cfg_path = config_path()
    from personalclaw.dashboard.handlers.agents import _get_config_lock  # noqa: F811

    async with _get_config_lock():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        except Exception:
            _log_sel("error", f"{path_key}=read_failed")
            return web.json_response({"error": "failed to read config file"}, status=500)

        # 🔴 A WRITE THAT LOOSENS A SECURITY SETTING NEEDS THE OWNER'S CONSENT ON THE WIRE, not
        # only in a dialog. The Settings hub's YOLO tile turned YOLO on ~40 ms after one click
        # because the panel's dialog was the only gate and a second writer never called it (#3596,
        # which closed that for YOLO alone). Any field holding a `SecurityControl` answers
        # `400 confirmation_required` without `confirm: true`, and the refusal carries the consent
        # sentence, so a surface that never heard the field was sensitive still asks the right
        # question. Tightening never needs it: revoking a grant is the direction a broken or
        # confused client must always be able to take.
        #
        # Under the lock, and against the value IN EFFECT (defaults included): "is this looser?"
        # is a question about what is stored at the moment of the write, and a check before the
        # lock could compare against a value a concurrent write has already replaced.
        #
        # A record that the owner was asked, not authorization — anything holding the owner's
        # session can send the flag. The authorization half is `app_write_refusal` above.
        consent = unconsented_loosening(
            path_key, spec, current=_value_in_effect(path_key), new=value, body=body
        )
        if consent:
            _log_sel("denied", f"{path_key}: loosening without confirm")
            return consent_required(path_key, consent)

        # Walk the dotted path, creating intermediate objects — supports any depth
        # (e.g. the 1-part `auto_update`, 2-part `agent.yolo`, 3-part
        # `dashboard.terminal.persist`). Every non-leaf segment must be an object.
        parts = path_key.split(".")
        cursor = data
        for seg in parts[:-1]:
            child = cursor.setdefault(seg, {})
            if not isinstance(child, dict):
                _log_sel("error", f"{path_key}=section_not_dict")
                return web.json_response(
                    {"error": f"config section '{seg}' is not an object"}, status=500
                )
            cursor = child
        cursor[parts[-1]] = value

        try:
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            # Through `atomic_write`, whose post-write hook is the ONE seam time-travel's
            # debounced committer subscribes to. This used to go through a JSON writer that did
            # its own mkstemp+rename, and with that bypass every config change made from
            # Settings — the primary writer of this file — landed on disk without ever reaching
            # the `config` state-history root, so "roll back my settings" had nothing to roll
            # back to. The PUT path two hundred lines up writes this same file this same way.
            atomic_write(cfg_path, json.dumps(data, indent=2) + "\n", fsync=True)
        except OSError:
            _log_sel("error", f"{path_key}=write_failed")
            return web.json_response({"error": "failed to write config file"}, status=500)

    _log_sel("success", f"{path_key}={value}")

    # Log level carries a live side effect — the same one POST /api/logs/level
    # applies — so set every logger + handler now. Without this, the Agent-defaults
    # row only persisted the key and the change took effect at the next restart,
    # diverging from the Diagnostics control that writes the identical key live.
    if path_key == "agent.log_level":
        try:
            from personalclaw.dashboard.handlers.updates import apply_log_level  # noqa: F811

            apply_log_level(value)
        except Exception:
            logger.warning("Failed to apply log level live after config patch", exc_info=True)

    # YOLO is an authorization control with a live runtime mechanism (trust_mode),
    # and this config field's only other reader is the startup seed — so this PATCH
    # used to change the file while the running instance kept its previous posture.
    # The OFF direction is the security-relevant one: a user revoking the bypass got
    # a UI reading false while approvals stayed bypassed until restart (#672).
    if path_key == "agent.yolo":
        state = request.app.get("state")
        if state is None:
            logger.warning("agent.yolo patched with no dashboard state; live apply skipped")
        elif value:
            # Mirror the startup path exactly: the grant is SEL-audit-GATED (an
            # unauditable permission change is refused, config stays saved for the
            # restart path) and permanent (from_config — the same semantics this
            # same key produces at boot, deliberately unlike the chat pill's TTL).
            try:
                from personalclaw.sel import sel

                sel().log_api_access(
                    caller="dashboard:config",
                    operation="mode_change:yolo",
                    outcome="enabled",
                    resources="config:agent.yolo",
                )
            except Exception:
                logger.error(
                    "SEL audit failed; config saved but YOLO NOT enabled live "
                    "(matches the startup path's refusal)",
                    exc_info=True,
                )
            else:
                state.enable_yolo(from_config=True)
        else:
            # Revocation is deliberately NOT audit-gated: a broken audit sink must
            # never keep the bypass alive. disable_yolo() runs the trust_mode
            # on-disable callback, clearing untrusted per-session auto-approve
            # policies exactly like a TTL expiry would.
            state.disable_yolo()
            try:
                from personalclaw.sel import sel

                sel().log_api_access(
                    caller="dashboard:config",
                    operation="mode_change:yolo",
                    outcome="disabled",
                    resources="config:agent.yolo",
                )
            except Exception:
                logger.warning("SEL audit failed for YOLO disable via config patch", exc_info=True)

    # Orchestrator skill toggle: generate the always-loaded routing skill when
    # enabled, or remove it (incl. the pre-rename conductor/ dir) when disabled —
    # so the single-field toggle actually takes effect (the FE patches via this
    # endpoint, not the PUT handler).
    if path_key == "agent.orchestrator_skill":
        try:
            from personalclaw.skills import SkillsLoader  # noqa: F811

            if value:
                from personalclaw.dashboard.handlers.agents import _regen_orchestrator  # noqa: F811

                _regen_orchestrator()
            else:
                import shutil

                for legacy in ("orchestrator", "conductor"):
                    d = SkillsLoader()._dir / legacy
                    if d.is_dir():
                        shutil.rmtree(d, ignore_errors=True)
        except Exception:
            logger.exception("Failed to apply orchestrator skill toggle")

    # Live-apply LAN discovery (COMPANION-APPS S2): start or stop the mDNS advertiser to
    # match the new value. Without this the toggle would be a control that needs a gateway
    # restart to mean anything — and worse, the status route beside it would keep reporting
    # the old reality while the switch read "on".
    if path_key in ("companion.discovery_enabled", "companion.instance_name"):
        try:
            from personalclaw.companion import discovery as _discovery  # noqa: F811

            _discovery.reconcile()
        except Exception:
            logger.exception("Failed to apply the LAN discovery setting")

    # Converge the Self-QA commit watcher (SELF-VERIFICATION §3.1) the moment the toggle or the
    # watched path changes, mirroring the startup reconcile. Without this the switch would need a
    # gateway restart to mean anything, and the trigger list beside it would keep showing the old
    # reality while the control read "on" — the same defect the LAN-discovery hook above fixes.
    if path_key.startswith("agent.self_qa."):
        try:
            from personalclaw.config.loader import config_dir as _config_dir
            from personalclaw.selfqa.install import reconcile as _reconcile_selfqa
            from personalclaw.triggers.store import TriggerStore as _TriggerStore

            _reconcile_selfqa(_TriggerStore(base_dir=_config_dir()))
        except Exception:
            logger.exception("Failed to apply the Self-QA companion setting")

    # Live-apply tool-output projection rules (TokenJuice OP6) so an edit takes effect
    # immediately (no restart) — mirrors the startup install into the projection engine.
    if path_key == "tools.projection_rules":
        try:
            from personalclaw.tool_providers import projection  # noqa: F811

            projection.set_user_rules(
                [
                    projection.ProjectionRule(
                        name=r.get("name", ""),
                        match_regex=r.get("match_regex", ""),
                        strategy=r.get("strategy", "log"),
                        head=int(r.get("head", 0) or 0),
                        tail=int(r.get("tail", 0) or 0),
                        keep=str(r.get("keep", "") or ""),
                        skip=str(r.get("skip", "") or ""),
                        count=str(r.get("count", "") or ""),
                    )
                    for r in (value or [])
                ]
            )
        except Exception:
            logger.exception("Failed to live-apply projection rules")

    full = AppConfig.load().to_dict()
    # The answer is a READ of the config, so an app gets the scope the GET gives it: without
    # this, writing the one setting it declared handed back every other setting too.
    if app_name:
        full = _declared_settings(full, _app_config_fields(app_name))
    return web.json_response(full)


# ── Incident kill switch (AUTONOMY-GUARDRAILS §1.3) ────────────────────


async def api_incident(request: web.Request) -> web.Response:
    """GET /api/incident — current state; POST /api/incident — activate.

    POST body: ``{reason?: str}``. Activation is SEL-audited and suspends all
    unattended work within one poll interval; interactive chat is untouched.
    """
    from personalclaw.guardrails import incident as _incident

    if request.method == "GET":
        st = _incident.get_incident()
        return web.json_response(
            {"active": st.active, "reason": st.reason, "started_at": st.started_at}
        )
    # POST — activate.
    body = await json_object_body(request)
    reason = str(body.get("reason", "")) if isinstance(body, dict) else ""
    st = _incident.activate(reason)
    return web.json_response(
        {"active": st.active, "reason": st.reason, "started_at": st.started_at}
    )


async def api_incident_resume(request: web.Request) -> web.Response:
    """POST /api/incident/resume — turn incident mode OFF.

    Resume is EXPLICIT: requires ``{confirm: true}`` so a stray request can't
    silently re-enable unattended work. SEL-audited.
    """
    from personalclaw.guardrails import incident as _incident

    body = await json_object_body(request)
    if not confirm_granted(body):
        return web.json_response({"error": 'resume requires {"confirm": true}'}, status=400)
    st = _incident.resume()
    return web.json_response({"active": st.active})


async def api_project_trust(request: web.Request) -> web.Response:
    """GET /api/guardrails/project-trust — the whole store;
    POST /api/guardrails/project-trust — record a Trust/Preview decision.

    POST body: ``{dir: str, trusted: bool}``. ``trusted=true`` is the explicit **Trust**
    (the folder may run/write project scripts); ``trusted=false`` keeps **Preview**
    (read-only). Recording is SEL-audited and keyed by the RESOLVED directory.
    """
    from personalclaw.guardrails import project_trust as _pt

    if request.method == "GET":
        return web.json_response({"projects": _pt._read_store()})
    body = await json_object_body(request)
    if not isinstance(body, dict):
        body = {}
    directory = str(body.get("dir", "") or "").strip()
    if not directory:
        return web.json_response({"error": "dir is required"}, status=400)
    trusted = body.get("trusted")
    if not isinstance(trusted, bool):
        return web.json_response({"error": "trusted must be a boolean"}, status=400)
    record = _pt.record_project_trust(directory, trusted=trusted)
    return web.json_response({"dir": _pt.resolve_dir(directory), **record})


# ── Provider health view (AUTONOMY-GUARDRAILS §2.5) ────────────────────


async def api_models_health(request: web.Request) -> web.Response:
    """GET /api/models/health — derived per-provider health (breaker state, latency
    percentiles, failure-mode distribution) from the model-call audit + breakers.

    Derived, not collected: reads ``model_calls.jsonl`` + in-memory breaker state,
    no telemetry infrastructure."""
    from personalclaw.guardrails.health import provider_health

    return web.json_response(await asyncio.to_thread(provider_health))


# ── Local token bootstrap (Electron / local apps) ─────────────────────


async def api_token_local(request: web.Request) -> web.Response:
    """GET /api/token/local — issue a token for local apps.

    Requires a per-session secret written to ~/.personalclaw/.local_secret at
    gateway startup. Only processes on the same machine can read the file.
    Secret passed via ``X-Local-Secret`` header (not query string, to avoid
    leaking in logs).
    """
    import personalclaw.dashboard.handlers as _h  # noqa: F811

    if not _h.is_loopback(request.remote or ""):
        _sel().log_api_access(
            caller=request.remote or "unknown",
            operation="token.local",
            outcome="denied",
            source="local-bootstrap",
            resources="non-loopback",
        )
        return web.json_response({"error": "loopback only"}, status=403)

    expected = request.app.get("local_secret", "")
    if not expected:
        return web.json_response({"error": "not available"}, status=503)
    provided = request.headers.get("X-Local-Secret", "")
    if not provided or not hmac.compare_digest(expected, provided):
        _sel().log_api_access(
            caller=request.remote or "unknown",
            operation="token.local",
            outcome="denied",
            source="local-bootstrap",
            resources="invalid-secret",
        )
        return web.json_response({"error": "invalid secret"}, status=403)
    ttl = MAX_SESSION_TTL_SECS
    ttl_param = request.query.get("ttl", "")
    if ttl_param:
        parsed = parse_duration(ttl_param)
        if parsed:
            ttl = parsed
    token = generate_token("local-app", ttl_seconds=ttl)
    _sel().log_api_access(
        caller=request.remote or "unknown",
        operation="token.local",
        outcome="success",
        source="local-bootstrap",
        resources="token-issued",
    )
    return web.json_response({"token": token, "expires_in": ttl})


# ── Session workspace (Orchestrated Chat) ────────────────────────────


async def api_session_agents_list(request: web.Request) -> web.Response:
    """GET /api/sessions/{id}/agents — list sub-agent results for a session.

    Resolves the PARENT session first (#2940), with the same predicate and the same
    ``session_not_found`` code ``GET /api/sessions/{key}`` already answers: ``list_results``
    returns ``[]`` for any id whose workspace directory is absent, so a mistyped or deleted
    session read as a real one that has run no sub-agents.
    """
    from personalclaw.dashboard.handlers.sessions import _session_exists

    session_id = request.match_info["id"]
    if not _session_exists(request.app["state"], session_id):
        return json_error("session_not_found", status=404)
    from personalclaw.session_workspace import list_results  # noqa: F811

    results = list_results(session_id)
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="session.agents.list",
        outcome="ok",
        source="dashboard",
        resources=session_id,
    )
    return web.json_response({"results": results})


async def api_session_agent_result(request: web.Request) -> web.Response:
    """GET /api/sessions/{id}/agents/{agent_id} — read sub-agent result."""
    session_id = request.match_info["id"]
    agent_id = request.match_info["agent_id"]
    from personalclaw.session_workspace import read_result  # noqa: F811

    content = read_result(session_id, agent_id)
    if not content:
        return web.json_response({"error": "not found"}, status=404)
    from personalclaw.security import redact_credentials, redact_exfiltration_urls  # noqa: F811

    content, _ = redact_exfiltration_urls(content)
    content, _ = redact_credentials(content)
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="session.agent.result",
        outcome="ok",
        source="dashboard",
        resources=f"{session_id}/{agent_id}",
    )
    return web.json_response({"agent_id": agent_id, "content": content})


async def api_session_agent_stream(request: web.Request) -> web.StreamResponse:
    """GET /api/sessions/{id}/agents/{agent_id}/stream — SSE stream of result file."""
    session_id = request.match_info["id"]
    agent_id = request.match_info["agent_id"]
    _sel().log_api_access(
        caller=request.get("user", "dashboard"),
        operation="session.agent.stream",
        outcome="ok",
        source="dashboard",
        resources=f"{session_id}/{agent_id}",
    )
    from personalclaw.session_workspace import result_path  # noqa: F811

    path = result_path(session_id, agent_id)
    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    await resp.prepare(request)

    last_pos = 0
    from personalclaw.security import redact_credentials, redact_exfiltration_urls  # noqa: F811

    for _ in range(1200):  # 20 min max
        try:
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if len(content) > last_pos:
                    chunk = content[last_pos:]
                    last_pos = len(content)
                    chunk, _ = redact_exfiltration_urls(chunk)
                    chunk, _ = redact_credentials(chunk)
                    await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
            # Check if the subagent is done.
            state: DashboardState = request.app["state"]
            if state.subagents:
                info = state.subagents.get(agent_id)
                if info and info.done:
                    await resp.write(b"event: done\ndata: {}\n\n")
                    break
        except (ConnectionResetError, ClientConnectionResetError):
            break
        await asyncio.sleep(1)
    return resp


async def api_logout(request: web.Request) -> web.Response:
    """POST /api/logout — revoke all active dashboard sessions.

    Called by ``personalclaw logout`` CLI. Requires loopback + local secret
    (same auth as /api/token/local) to prevent unauthorized revocation.
    """
    import personalclaw.dashboard.handlers as _h  # noqa: F811
    from personalclaw.dashboard.token_auth import revoke_all_sessions  # noqa: F811

    if not _h.is_loopback(request.remote or ""):
        _sel().log_api_access(
            caller=request.remote or "unknown",
            operation="logout",
            outcome="denied",
            source="cli",
            resources="non-loopback",
        )
        return web.json_response({"error": "loopback only"}, status=403)

    expected = request.app.get("local_secret", "")
    provided = request.headers.get("X-Local-Secret", "")
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        _sel().log_api_access(
            caller=request.remote or "unknown",
            operation="logout",
            outcome="denied",
            source="cli",
            resources="invalid-secret",
        )
        return web.json_response({"error": "invalid secret"}, status=403)

    revoke_all_sessions()
    _sel().log_api_access(
        caller=request.remote or "unknown",
        operation="logout",
        outcome="success",
        source="cli",
        resources="all-sessions-revoked",
    )
    return web.json_response({"ok": True})
