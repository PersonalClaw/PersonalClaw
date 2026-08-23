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
from personalclaw.config.edit_spec import ConfigValueError, coerce_edit_value
from personalclaw.config.loader import MEMORY_VAULT_MODES, AppConfig
from personalclaw.dashboard.state import DashboardState
from personalclaw.dashboard.token_auth import MAX_SESSION_TTL_SECS, generate_token, parse_duration
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

    from personalclaw.transcribe import is_available, transcribe_audio  # noqa: F811
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

        text = await transcribe_audio(tmp)
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

    Recovers from a broken HMAC chain. The previous log file is renamed with
    a UTC timestamp suffix unless ``{"archive": false}`` is sent.
    """
    archive = True
    if request.can_read_body:
        try:
            body = await request.json()
            if isinstance(body, dict) and body.get("archive") is False:
                archive = False
        except Exception:
            pass
    result = _sel().rotate(archive=archive)
    return web.json_response(result)


async def api_security_stats(_request: web.Request) -> web.Response:
    """GET /api/security/stats — live security feature counts."""
    from personalclaw.security import denied_command_patterns

    denied = len(denied_command_patterns())

    schemas = sum(1 for name in dir(_validation_mod) if name.endswith("_SCHEMA") and name.isupper())

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


# ── PersonalClaw Config API ──
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
        path = config_path()
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
        # The three `agent.*` fields this endpoint owns. Their bounds are NOT restated here:
        # all three are already declared in `_EDITABLE_CONFIG`, so this used to be a second
        # copy of the same numbers — identical today, one edit away from disagreeing, and
        # nothing would have caught the divergence because each path tested its own copy.
        agent_fields = ("subagent_max_turns", "max_subagents", "orchestrator_skill")
        # An unrecognised key used to be dropped in silence whenever at least one recognised
        # key rode along, so `{"max_subagents": 4, "subagent_max_tunrs": 999}` returned 200
        # and applied half of what was asked.
        unknown = sorted(k for k in agent_settings if k not in agent_fields)
        if unknown:
            return _deny(
                f"unknown agent settings: {', '.join(unknown)} "
                f"(writable: {', '.join(agent_fields)})"
            )
        applied: list[str] = []
        for key in agent_fields:
            if key not in agent_settings:
                continue
            try:
                agent[key] = coerce_edit_value(
                    f"agent.{key}", agent_settings[key], _EDITABLE_CONFIG[f"agent.{key}"]
                )
            except ConfigValueError as exc:
                # The message names the field: this endpoint can carry several at once, so
                # a bare "must be between 0 and 16" would not say which one was refused.
                return _deny(f"{key} {exc}", exc.status)
            applied.append(key)
        if not applied:
            return _deny("no recognized settings provided")
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

    cfg = AppConfig.load()
    return web.json_response(cfg.to_dict())


# Allowed editable config paths and their validators
def _agent_values() -> set[str]:
    """Return allowed pool_agent values: empty string + all configured agent names."""
    from personalclaw.config.loader import AppConfig

    return {"", *AppConfig.load().agents}


def _bot_name_sanitizer(value: str) -> str:
    """The loader's bot_name sanitizer (single source of truth)."""
    from personalclaw.config.loader import _sanitize_bot_name

    return _sanitize_bot_name(value)


def _push_to_talk_chord_sanitizer(value: str) -> str:
    """Normalize a push-to-talk accelerator at the WRITE boundary (DC-3 T3.1).

    ``" Command + Shift + Space "`` and ``"Command+Shift+Space"`` are the same chord to
    a user and different strings to `globalShortcut.register`. Collapsing the spacing
    here means the value the shell is handed is byte-identical to the value stored and
    to the value the Settings control redisplays — otherwise a chord round-trips as
    "saved" while binding nothing.

    Empty stays empty: `load()` turns that into the shipped default, which is the one
    place that decision belongs.
    """
    return "+".join(part.strip() for part in value.split("+") if part.strip())


def _scratchpad_path_sanitizer(value: str) -> str:
    """Canonicalize the watched-scratchpad path at the WRITE boundary.

    `pathguard.canonicalize` is the same realpath+expanduser the trigger capability fence uses, so
    a stored path can never differ from the one a fence would compare — a config file holding
    ``~/notes/../.ssh/id_rsa`` while the runtime resolved something else is the split-brain S118
    documented. Empty stays empty: "" is how the feature is turned off.
    """
    if not value.strip():
        return ""
    from personalclaw.triggers.pathguard import canonicalize

    return canonicalize(value.strip())


_EDITABLE_CONFIG: dict[str, dict] = {
    "agent.approval_mode": {"type": "enum", "values": ["auto", "interactive", "trust_reads"]},
    "agent.yolo": {"type": "bool"},
    "agent.sandbox": {"type": "enum", "values": ["auto", "off"]},
    "agent.soft_stop_budget_secs": {"type": "float", "min": 0.5, "max": 60.0},
    "agent.max_subagents": {"type": "int", "min": 0, "max": 16},
    "agent.subagent_max_turns": {"type": "int", "min": 1, "max": 200},
    "agent.subagent_timeout_secs": {"type": "int", "min": 60, "max": 7200},
    "agent.spawn_min_memory_gb": {"type": "float", "min": 0.0, "max": 64.0},
    "agent.subagent_cwd_allowed_roots": {"type": "str_list", "max_items": 20},
    # PLATFORM-HARDENING-FLOORS §1 — resource ceilings for agent-influenced child
    # processes, delivered post-exec by the ceiling shim. 0 disables an individual
    # limit. session_host (ACP) is exempt from the NOFILE cap by profile, not config.
    "sandbox.nofile": {"type": "int", "min": 0, "max": 1_048_576},
    "sandbox.max_pids": {"type": "int", "min": 0, "max": 100_000},
    "sandbox.max_rss_mb": {"type": "int", "min": 0, "max": 1_048_576},
    # PHF-2 — opt into the Linux-only second enforcement tier (a transient systemd user
    # scope carrying TasksMax/MemoryMax derived from the ceilings above, so they bound the
    # whole child subtree). Editable because it is the one knob that changes HOW the
    # ceilings are delivered; it is a no-op where systemd user scopes are unavailable.
    "sandbox.cgroup_scopes": {"type": "bool"},
    # PHF-4 — the declared-needs seam for the child-env allowlist. Names only; the
    # credential floor still refuses a sensitive name at spawn, so a write here cannot
    # hand a hook the gateway's AWS session.
    "sandbox.env_passthrough": {"type": "str_list", "max_items": 40},
    # EXECUTION-ISOLATION §6 — bounds on the turn-bound file checkpoint store. Only the
    # BOUNDS are editable: which files may never be copied is a code-level floor
    # (turn_checkpoints.NEVER_CAPTURE_GLOBS) with no config field, so no PATCH can widen it.
    "checkpoints.enabled": {"type": "bool"},
    "checkpoints.max_mb": {"type": "int", "min": 0, "max": 100_000},
    "checkpoints.max_turns": {"type": "int", "min": 1, "max": 1_000},
    "checkpoints.max_file_mb": {"type": "int", "min": 0, "max": 10_000},
    "security.denied_commands": {"type": "str_list", "max_items": 100, "each_regex": True},
    "security.egress": {"type": "egress"},
    # AUTONOMY-GUARDRAILS: the runtime-editable guardrail subset (§7). Incident is
    # NOT here — it's its own endpoint (a later session). Budgets/breaker/scan are
    # plain scalars edited via Settings.
    "guardrails.budgets.max_tokens_per_run": {"type": "int", "min": 0, "max": 100_000_000},
    "guardrails.budgets.max_tokens_per_day": {"type": "int", "min": 0, "max": 1_000_000_000},
    "guardrails.budgets.max_dollars_per_day": {"type": "float", "min": 0.0, "max": 100_000.0},
    "guardrails.breaker.failure_threshold": {"type": "int", "min": 1, "max": 100},
    "guardrails.breaker.recovery_secs": {"type": "float", "min": 0.0, "max": 3600.0},
    "guardrails.scan_mode": {"type": "enum", "values": ["warn", "redact", "block"]},
    # Model routing (MODEL-ROUTING-TELEMETRY §7 wiring point (d)) — the runtime-editable
    # subset: the master switch plus the tuning numbers a user reaches for after watching
    # what routing actually did. Per-use-case mode/pin are NOT here: they live in
    # use_case_settings/{uc}.json + routing_policy.json, beside the other bindings state.
    "routing.enabled": {"type": "bool"},
    "routing.local_timeout_secs": {"type": "float", "min": 0.0, "max": 600.0},
    "routing.min_samples": {"type": "int", "min": 1, "max": 10_000},
    "routing.hysteresis": {"type": "float", "min": 0.0, "max": 1.0},
    "routing.cloud_quality_margin": {"type": "float", "min": 0.0, "max": 1.0},
    "routing.energy_sampling": {"type": "bool"},
    "routing.reproposal_cooldown_days": {"type": "int", "min": 0, "max": 365},
    # §5 earned-autonomy thresholds. Runtime-editable because these are the knobs a
    # user reaches for after seeing what the ladder actually proposed. Bounded on both
    # sides: `clean_approvals` floors at 1 (a bar of zero would offer a promotion to a
    # type with no record), and every ceiling keeps a typo from budgeting a decade.
    "guardrails.autonomy.clean_approvals": {"type": "int", "min": 1, "max": 1000},
    "guardrails.autonomy.min_days": {"type": "int", "min": 0, "max": 365},
    "guardrails.autonomy.max_rejections": {"type": "int", "min": 0, "max": 100},
    "guardrails.autonomy.cooldown_days": {"type": "int", "min": 0, "max": 365},
    "guardrails.autonomy.evidence_window_days": {"type": "int", "min": 1, "max": 365},
    # MULTIMODAL-IO §4.5 — the hands-free voice loop. All six are convenience
    # knobs (comfort, not safety): the phrase lists drive frontend gating, the
    # booleans switch echo filtering, mute-during-playback, pre-speech cleaning
    # and the voice-origin disclaimer.
    "voice.confirmation_phrases": {"type": "str_list", "max_items": 20},
    "voice.exit_phrases": {"type": "str_list", "max_items": 20},
    "voice.echo_filter_enabled": {"type": "bool"},
    "voice.duplex_mute_enabled": {"type": "bool"},
    "voice.clean_for_speech_enabled": {"type": "bool"},
    "voice.voice_disclaimer_enabled": {"type": "bool"},
    # DC-3 T3.1 — the desktop push-to-talk chord. An Electron accelerator string, so
    # `max_len` and a whitespace normalise are all this boundary can usefully check:
    # whether the chord is BINDABLE is a question only the shell can answer (another
    # app may already own it), and it answers with a reason the Settings control
    # renders. Normalising here keeps the stored value byte-identical to what the
    # shell is asked to bind.
    "voice.push_to_talk_chord": {
        "type": "str",
        "max_len": 64,
        "sanitize": _push_to_talk_chord_sanitizer,
    },
    "resilience.doctor_enabled": {"type": "bool"},
    "resilience.degraded_indicator": {"type": "bool"},
    "resilience.mid_turn_policy": {
        "type": "enum",
        "values": ["queue", "steer", "cancel_and_replace"],
    },
    "resilience.cancel_replace_min_interval_secs": {"type": "float", "min": 0.0, "max": 60.0},
    "resilience.remediation.enabled": {"type": "bool"},
    "resilience.remediation.target_score": {"type": "int", "min": 0, "max": 100},
    "resilience.remediation.max_cost_usd": {"type": "float", "min": 0.0, "max": 100.0},
    "resilience.remediation.idle_minutes_healthy": {"type": "int", "min": 1, "max": 1440},
    "resilience.remediation.tick_minutes_degraded": {"type": "int", "min": 1, "max": 1440},
    # DURABILITY-AND-SYNC §3 — the scheduled-backup contract. Runtime-editable
    # because these are the knobs a user reaches for after seeing what the schedule
    # actually produced (the snapshot list shows keep-vs-prune before anything is
    # deleted). Retention caps are bounded, not unbounded: 0 disables a tier, and the
    # ceilings keep a typo from budgeting a decade of archives.
    "durability.auto_backup": {"type": "bool"},
    "durability.keep_daily": {"type": "int", "min": 0, "max": 365},
    "durability.keep_weekly": {"type": "int", "min": 0, "max": 260},
    "durability.keep_monthly": {"type": "int", "min": 0, "max": 120},
    "durability.restore_drills": {"type": "bool"},
    "durability.time_travel": {"type": "bool"},
    # DURABILITY-AND-SYNC §4 — sync knobs. sync_enabled is fail-closed in load(); the
    # transport is a free-text provider name (validated against installed transports at
    # cycle time, not here — an unknown name simply leaves sync idle).
    "durability.sync_enabled": {"type": "bool"},
    "durability.sync_transport": {"type": "str", "max_len": 64},
    "durability.sync_stale_after_secs": {"type": "int", "min": 30, "max": 86400},
    # §4.4 — the encryption tri-state. `values` is closed, so an out-of-scale value is
    # REFUSED at the boundary rather than silently landing as the safe default: a user who
    # mistypes a security control should be told, not quietly overruled. (load() still
    # falls back to "auto" for a hand-edited file, which this endpoint never produces.)
    "durability.sync_encrypt": {"type": "str", "max_len": 8, "values": ("auto", "on", "off")},
    # EVALUATION-SUBSTRATE §10 — the runtime-editable evals subset. These are the
    # knobs a user reaches for from Settings; each is a plain scalar. Deliberately
    # EXCLUDED: `evals.bakeoff_capture_enabled` — a privacy-sensitive input-capture
    # flag, off by default and SEL-audited, kept out of the one-click PATCH allowlist
    # (mirroring `inbound.mcp.allow_remote`); flipping it is a deliberate config edit.
    "evals.enabled": {"type": "bool"},
    "evals.study_default_k": {"type": "int", "min": 1, "max": 50},
    "evals.judge_agreement_floor": {"type": "float", "min": 0.0, "max": 1.0},
    "evals.ablation_cadence_days": {"type": "int", "min": 1, "max": 365},
    "evals.default_budget_usd": {"type": "float", "min": 0.0, "max": 1000.0},
    # PROACTIVE-ASSISTANT §"Config Map" (PA-1) — the runtime-editable triage subset.
    # `auto_execute_enabled` IS here on purpose: it is the plan's one-click revoke, so
    # a user who dislikes what the digest did must be able to switch acting off from
    # the surface that showed them. Its blast radius is bounded elsewhere (the frozen
    # capability set, the per-run cap, the guardrails budget floor), not by hiding it.
    "proactive.triage_enabled": {"type": "bool"},
    "proactive.digest_schedule": {"type": "str", "max_len": 64},
    "proactive.auto_execute_enabled": {"type": "bool"},
    "proactive.max_auto_actions_per_run": {"type": "int", "min": 0, "max": 50},
    "proactive.classifier_gate_enabled": {"type": "bool"},
    "proactive.decision_default_horizon_days": {"type": "int", "min": 1, "max": 3650},
    "tools.projection_rules": {"type": "projection_rules"},
    # Context Economy §4 — background compression feature flags (runtime-editable).
    "tools.bg_compress_enabled": {"type": "bool"},
    "tools.bg_compress_idle_days": {"type": "float", "min": 0.0, "max": 365.0},
    # Context Economy §5 — dynamic tool-group activation (runtime-editable). Takes
    # effect for sessions created after the change (activation state is per-runtime).
    "tools.groups_enabled": {"type": "bool"},
    # EXTERNAL-ACCESS §1.1/§11 — the runtime-editable subset of the inbound access seam.
    # The kill switches are here so turning a surface OFF takes effect on the next
    # request without a restart (that is the whole point of a kill switch).
    #
    # 🔴 Deliberately NOT PATCH-editable, and asserted as REFUSALS in
    # `test_external_access_seam.py` rather than merely absent from this dict:
    #   · `external_access.public_url` — the public URL is the security boundary for
    #     every non-loopback peer check (`inbound/auth.peer_allowed` compares the
    #     request Host to it). A boundary that moves on one PATCH is not a boundary.
    #   · `external_access.<surface>.allow_remote` — widening a network surface from
    #     loopback to the world is a deliberate config-file edit.
    #   · the per-surface TOKENS — they are not in `config.json` at all (they live in
    #     the credential store via `save_credential`), so there is no path here even in
    #     principle; token lifecycle is `personalclaw inbound token create <surface>`.
    "external_access.enabled": {"type": "bool"},
    "external_access.openai.enabled": {"type": "bool"},
    "external_access.mcp.enabled": {"type": "bool"},
    "external_access.a2a.enabled": {"type": "bool"},
    "external_access.capture.enabled": {"type": "bool"},
    "external_access.bridge.enabled": {"type": "bool"},
    "external_access.rate_rps": {"type": "float", "min": 0.01, "max": 1000.0},
    "external_access.rate_burst": {"type": "int", "min": 1, "max": 10000},
    "external_access.rate_concurrent": {"type": "int", "min": 1, "max": 256},
    "external_access.auto_disable_after_breaches": {"type": "int", "min": 0, "max": 10000},
    "external_access.capture_retention_days": {"type": "int", "min": 0, "max": 3650},
    # MEMORY-GRAPH-AND-VAULT §1 — entity linking. Runtime-editable: turning it off
    # stops new links immediately (existing links are kept, so re-enabling doesn't
    # need a backfill).
    "memory.graph_enabled": {"type": "bool"},
    # MEMORY-GRAPH-AND-VAULT §3 — the push reflex. Both runtime-editable: the reflex
    # reads them per turn, so a change takes effect on the next message with no restart.
    "memory.push_context": {"type": "bool"},
    "memory.push_min_confidence": {"type": "float", "min": 0.0, "max": 1.0},
    # MEMORY-GRAPH-AND-VAULT §2.4 / §4.2 — the topology block and the holder axis. Both
    # runtime-editable: the block is read per new session and the axis per write, so a
    # change takes effect without a restart. Turning the axis OFF stops NEW attribution
    # and stops rendering it; already-attributed rows keep their holder, so flipping it
    # back on does not need a backfill.
    "memory.graph_topology_in_context": {"type": "bool"},
    "memory.holder_attribution": {"type": "bool"},
    # MEMORY-GRAPH-AND-VAULT §6 (MGAV-9) — the Slots block budget. Runtime-editable because
    # it is read per session build, so a change takes effect on the next new session. The
    # bounds mirror `memory_slots.SLOTS_BLOCK_MIN/HARD_MAX_CHARS`: the consumer clamps to the
    # same range, so a value that got past this allowlist by another route still cannot widen
    # the always-injected block. The per-slot caps are NOT here — which individual register is
    # full is a per-class judgment fixed in code, not a number to tune from Settings.
    "memory.slot_size_cap": {"type": "int", "min": 200, "max": 4000},
    # The retention / behaviour / vault fields that `PUT /api/memory/settings` writes.
    # Declared HERE rather than validated a second time in that handler: they are editable
    # config fields by definition (the Memory panel writes them), and an endpoint carrying
    # its own private copy of the rules is how `graph_enabled` ended up with two answers —
    # this allowlist demanded a real boolean while the PUT ran `bool(body[flag])`, so
    # `{"graph_enabled": "false"}` turned the feature ON. Bounds are stated, not clamped:
    # a request that "succeeded" having stored something else is the one outcome a caller
    # can never notice. The two floors (0.5h idle, 7d retention) are the ones the PUT
    # already enforced by clamping; the ceilings are new and deliberately generous —
    # they exist so an accidental extra digit is a 400 rather than "retention: never".
    "memory.history_idle_hours": {"type": "float", "min": 0.5, "max": 8760.0},
    "memory.history_max_days": {"type": "int", "min": 7, "max": 3650},
    "memory.l1_manifest": {"type": "bool"},
    "memory.active_recall": {"type": "bool"},
    "memory.proactive_commitments": {"type": "bool"},
    # The one-time "legacy memory has been migrated" marker. Editable because the Memory
    # panel clears it to re-offer the migration; a bool, so it cannot be set by typing a
    # word that happens to be truthy.
    "memory.migrated": {"type": "bool"},
    # MEMORY-GRAPH-AND-VAULT §5.1 — the vault. `vault_mode` is a closed three-value enum
    # (an unrecognised value must be a 400, never a silent fall back to "off", or mirroring
    # stops while the setting looks saved). `vault_path` normalises empty → the default at
    # the write boundary so config.json matches what load() reads back.
    "memory.vault_mode": {"type": "enum", "values": list(MEMORY_VAULT_MODES)},
    "memory.vault_path": {
        "type": "str",
        "max_len": 256,
        "sanitize": lambda v: v.strip() or "memory-vault",
    },
    "feedback.enabled": {"type": "bool"},
    "feedback.retire_threshold": {"type": "float", "min": 0.1, "max": 0.9},
    "feedback.min_n": {"type": "int", "min": 3, "max": 50},
    "feedback.window_days": {"type": "int", "min": 7, "max": 365},
    # AGENT-ROUTING — suggest-first specialist routing (runtime-editable).
    # WORKFLOWS-V2-UNIVERSAL-PLANNING UP-R18 — the watched scratchpad. "" = off, which is
    # the default; canonicalized on write so the stored path is the one the fence compares.
    "planning.scratchpad_path": {
        "type": "str",
        "max_len": 512,
        "sanitize": _scratchpad_path_sanitizer,
    },
    "agents_routing.enabled": {"type": "bool"},
    "agents_routing.min_confidence": {"type": "float", "min": 0.3, "max": 0.95},
    "agents_routing.cooldown_hours": {"type": "float", "min": 0.0, "max": 720.0},
    "agent.orchestrator_skill": {"type": "bool"},
    "agent.acp_concurrent_sessions": {"type": "bool"},
    # EXECUTION-ISOLATION §3.2 (EI-5) — require verified adapter provenance for an
    # UNATTENDED spawn onto an external runner. Runtime-editable because it is a
    # posture choice, not a floor: which provenance counts as verified is code
    # (agents/runners.verify_adapter), so a PATCH here can only turn the requirement
    # on or off, never widen what "verified" means.
    "agent.unattended_requires_verified_adapter": {"type": "bool"},
    # EI-5 — how long a runner's measured health evidence counts as current. Bounded
    # below at a minute (a shorter window would mark every row overdue between two
    # clicks) and above at a day.
    "agent.runner_health_check_secs": {"type": "int", "min": 60, "max": 86_400},
    # PROMPT-CACHE-SUBSTRATE §C6 — the prompt-cache switch (default ON). Off collapses
    # the provider's declared cache mode to NONE, which is the byte-identical no-marker
    # path an undeclared provider already takes. It does NOT revert the §C2/§C3 wire
    # ordering repairs, which are unconditional.
    "agent.prompt_cache_enabled": {"type": "bool"},
    # The assistant's display name — consumed by the prompt engine ({{bot_name}}
    # template var + ContextBuilder). Sanitized at the write boundary (strip
    # markdown/braces, ≤50 chars) so the FILE matches what load() produces —
    # load() applies the same function, defense in depth for hand-edits.
    "agent.bot_name": {"type": "str", "max_len": 50, "sanitize": _bot_name_sanitizer},
    "agent.log_level": {"type": "enum", "values": ["DEBUG", "INFO", "WARNING", "ERROR"]},
    # Self-QA companion (SELF-VERIFICATION §5 wiring point (d)). All four fields are editable,
    # not just the two toggles: a companion you can enable but cannot point at a repo is
    # enabled and inert, which reads as broken. `max_scenarios_per_fire` is clamped to the same
    # [1, 20] window ``AppConfig.load()`` applies, so the file and the dashboard agree.
    "agent.self_qa.enabled": {"type": "bool"},
    "agent.self_qa.watched_repo": {"type": "str", "max_len": 512},
    "agent.self_qa.fix_branch_enabled": {"type": "bool"},
    "agent.self_qa.max_scenarios_per_fire": {"type": "int", "min": 1, "max": 20},
    "session.timeout_secs": {"type": "int", "min": 0, "max": 86400},
    "session.autocompact_pct": {"type": "float", "min": 5.0, "max": 90.0},
    "session.pool_size": {"type": "int", "min": 0, "max": 10},
    "session.pool_agent": {"type": "str", "values_fn": _agent_values},
    "session.pool_ttl_secs": {"type": "int", "min": 0, "max": 7200},
    # 0 = off; the ceiling is generous on purpose (a year) since "archive rarely"
    # is a legitimate preference and archiving is non-destructive.
    "session.auto_archive_days": {"type": "int", "min": 0, "max": 3650},
    "auto_update": {"type": "bool"},
    "dashboard.mcp_probe_timeout_secs": {"type": "int", "min": 5, "max": 120},
    # MI-4 — the screen-context master switch (default OFF). Runtime-editable because
    # it is the consent knob: a user turns it on for one working session and off again,
    # and having to restart the gateway to withdraw that permission would be the wrong
    # shape for a privacy control. Flipping it off takes effect on the NEXT frame POST
    # (the route re-reads config per request) and on the next drain, so a slot staged
    # while it was on is dropped rather than delivered.
    "dashboard.screen_share_enabled": {"type": "bool"},
    # P25: opt-in tmux-backed terminal persistence (survives a gateway restart). Read as a
    # raw dict from config.json by handlers/terminal.py::_get_config — a 3-part nested path.
    "dashboard.terminal.persist": {"type": "bool"},
    # WF2LOO-17 — the loop JUDGE's model axis, independent of the `loops` axis the
    # graded worker rides. Runtime-editable because it is the knob a user reaches for
    # after reading a verdict they disagree with (pin the strongest model to judgment).
    # The closed set is the chat-family axes only: a non-chat capability (stt, embedding,
    # image_gen…) cannot render a verdict, so offering it would be a footgun. `loops` IS
    # offered — collapsing judge onto the worker's binding is a legitimate, explicit
    # choice; it just stops being the silent default.
    "loops.judge_use_case": {
        "type": "enum",
        "values": ["reasoning", "chat", "code_tools", "background", "orchestration", "loops"],
    },
    # WF2LOO-18 — how many consecutive no-progress cycles stall a loop. Runtime-editable
    # because it is the knob a user reaches for after a loop either nagged too early or
    # burned cycles before asking for direction. Floor of 2 is structural (the
    # worker-independent signals compare findings BETWEEN cycles); the ceiling keeps a
    # typo from parking the detector past any realistic cycle budget.
    "loops.stagnation_window": {"type": "int", "min": 2, "max": 50},
    # HARNESS-CRAFT §3.2 — the SDLC post-gate check-work hook. Off by default; on, a
    # passing stage gate additionally re-derives checks from the stage's own claims.
    "loops.check_work_stages": {"type": "bool"},
    "loops.worktree_sparse": {"type": "bool"},
    "inbox.engagement_ranking_enabled": {"type": "bool"},
    "inbox.engagement_half_life_days": {"type": "float", "min": 0.0, "max": 365.0},
    # Gates the poll-based message sources (filesystem/channel apps). The UI
    # toggle calls /api/inbox/restart after flipping so the service re-attaches.
    "inbox.enabled": {"type": "bool"},
    # WORKFLOWS-V2 Slice 0. Runtime-editable: these are the knobs a user reaches for
    # WHILE something is going wrong — capping concurrency because a fan-out is starving
    # the box, or shortening a stall timeout because a node is wedged. Requiring a
    # restart to change them would mean restarting mid-run to fix a run.
    "workflows.enabled": {"type": "bool"},
    "workflows.max_active_runs": {"type": "int", "min": 1, "max": 100},
    "workflows.self_schedule_max_outstanding": {"type": "int", "min": 0, "max": 200},
    "workflows.max_concurrent_nodes": {"type": "int", "min": 1, "max": 64},
    "workflows.default_node_timeout_total_secs": {"type": "int", "min": 0, "max": 86400},
    "workflows.default_node_timeout_stall_secs": {"type": "int", "min": 0, "max": 86400},
    "workflows.retention_per_def": {"type": "int", "min": 1, "max": 10000},
    "workflows.max_concurrent_llm_nodes": {"type": "int", "min": 1, "max": 32},
    "workflows.max_concurrent_io_nodes": {"type": "int", "min": 1, "max": 32},
    "workflows.model_tier_reasoning": {"type": "str", "max_len": 32},
    "workflows.model_tier_standard": {"type": "str", "max_len": 32},
    "workflows.model_tier_fast": {"type": "str", "max_len": 32},
    # WF2UNI-11: the T4 embedding tie-break floor. Live-editable — the dial an owner turns when the
    # matcher composes too readily or ignores a genuine semantic near-match, tuned while watching.
    "workflows.match_threshold": {"type": "float", "min": 0.0, "max": 1.0},
    # TASKS-SOPS §8 (S61k). All four are live-editable: each changes how much the system does on
    # its own, which is exactly the class of setting an owner reaches for mid-session rather than
    # after a restart.
    #
    # The bounds are the ones the code already enforces, restated here so the API refuses out-of-
    # range values instead of storing one the runtime silently clamps — a stored value that does not
    # match the behaviour is worse than a rejection, because the user reads the stored one.
    "workflows.surface_mode_default": {"type": "enum", "values": ["off", "passive", "suggest"]},
    "workflows.max_materialized_per_foreach": {"type": "int", "min": 1, "max": 500},
    # 0 = never expires (an author writing `0` means "wait for me"), and the upper bound is 30 days:
    # a gate held longer than that is an abandoned run, not a patient one.
    "workflows.confirmation_ttl_secs": {"type": "int", "min": 0, "max": 30 * 24 * 3600},
    # Capped at MAX_LEASE_SECS (1h) — the ceiling `pool.Lease.expires_at` clamps to. Accepting a
    # larger number here would store a week-long lease that the runtime silently shortens.
    "workflows.lease_ttl_secs": {"type": "int", "min": 30, "max": 3600},
    # AUTO-A1/A2 (S70) gate defaults. Strings rather than enums: a quiet window is an `HH:MM-HH:MM`
    # range and a duty gate is a provider name an app can supply, so neither has a closed value set
    # the API could check. `triggers.calendar.parse_default_window` validates the format and treats
    # an unparseable value as NO default — the fail-safe reading, since a malformed window that
    # accidentally matched all day would look exactly like a broken scheduler.
    "workflows.default_quiet_windows": {"type": "str", "max_len": 64},
    "workflows.duty_gate_default": {"type": "str", "max_len": 64},
    # WORK-CONTAINERS §4.1 (WF2WOR-4). Both live-editable, and each for a concrete reason: the
    # default mode is what a user changes after watching a run touch their real tree, and the
    # teardown switch is what they reach for when a teardown command is itself the problem — both
    # mid-session decisions. `container` is in the enum because it is in `workspace.Mode`; it
    # degrades to an isolated scratch dir until §4.4 lands, so accepting the word here never
    # promises a runtime the engine does not have.
    "workflows.workspace_default_mode": {
        "type": "enum",
        "values": ["scratch", "worktree", "in_place", "container"],
    },
    "workflows.workspace_teardown_on_expiry": {"type": "bool"},
    # LEARNING-FLYWHEEL capture: the knobs worth changing without a restart. The
    # evidence floor and the session-score threshold are how an owner tunes how
    # eagerly the system learns, and staging can be turned off if the log is
    # unwanted — so all three are live-editable.
    "learning.min_evidence": {"type": "int", "min": 1, "max": 20},
    # WF2LEA-15: the lesson injection floor. Live-editable for the same reason as the
    # evidence floor beside it — this is the knob for "why is it still doing that" /
    # "why did it stop doing that", and a user chasing either answer should be able to
    # move the gate and re-read the Memory studio without restarting the gateway.
    "learning.min_lesson_confidence": {"type": "float", "min": 0.0, "max": 1.0},
    "learning.staging_enabled": {"type": "bool"},
    # LEARN-R21 (S72): the self-model gate. Live-editable because it is the one learning path
    # that acts on what WORKED rather than on corrections — a user who finds that presumptuous
    # should be able to stop it without a restart.
    "learning.self_model_enabled": {"type": "bool"},
    "learning.min_session_score": {"type": "float", "min": 0.0, "max": 1.0},
    "learning.propose_quota_per_run": {"type": "int", "min": 1, "max": 25},
    "learning.curator_enabled": {"type": "bool"},
    "learning.context_budget_tokens": {"type": "int", "min": 500, "max": 100000},
    # WF2LEA-4: learn from terminal workflow-run failures. Live-editable because a user
    # who finds run-end lesson proposals noisy should be able to silence them without a
    # restart, the same as every other learning-eagerness knob above.
    "learning.run_end_enabled": {"type": "bool"},
    # WF2LEA-5: grade accepted changes against their predictions and auto-file HARMFUL
    # reverts. Live-editable for the same reason — a user who does not want the flywheel
    # measuring its own accepted proposals should be able to stop it without a restart.
    "learning.attribution_enabled": {"type": "bool"},
    # KNOWLEDGE-SYNTHESIS: the write-semantics knobs worth changing without a restart.
    # `require_citations` is here deliberately — an owner mid-research may need to store an
    # unsourced note and should not have to restart the gateway to do it.
    "knowledge.idempotent_persist": {"type": "bool"},
    "knowledge.require_citations": {"type": "bool"},
    "knowledge.report_budget_chars": {"type": "int", "min": 1000, "max": 500000},
    "knowledge.max_mentions_per_claim": {"type": "int", "min": 1, "max": 200},
    # The long-run + maintenance cadences. Runtime-editable because the right value depends on
    # what a store is being used for, and finding it means adjusting and watching — which a
    # restart per attempt makes nobody do.
    "knowledge.synthesis_window": {"type": "int", "min": 1, "max": 200},
    "knowledge.lint_every_n_persists": {"type": "int", "min": 1, "max": 1000},
    # KL-14's anti-starvation window. Floor 60s rather than 1: below a minute this stops
    # being a staleness window and becomes "run every tick", which defeats the coalescing
    # the watermark exists for. `maintenance.max_staleness_secs()` enforces its own floor
    # too, because config.json is hand-editable and this bound only guards the PATCH path.
    "knowledge.maintenance_max_staleness_secs": {"type": "int", "min": 60, "max": 86400},
    # KL-20's projection gate. PATCH-editable rather than a dedicated PUT (which is what
    # `memory.vault_mode` has) because there is nothing to do at the moment of the flip: the
    # projection is a maintenance pass, so turning the mode on arms the pass and the next tick
    # writes the vault. `off` is the shipped default and stays reachable, so a user who
    # changes their mind stops the writer with the same control that started it.
    #
    # `two_way` is a strictly larger grant than `mirror` — it is what makes an edited file
    # win over the database — and both are refused by anything except this validated write,
    # because a mode outside the tuple resolves to `off` in `load()`.
    "knowledge.vault_mode": {"type": "enum", "values": ["off", "mirror", "two_way"]},
    "knowledge.vault_path": {"type": "str", "max_len": 512},
    # KL-13's similarity-edge shape. Runtime-editable because the right floor depends on the
    # embedding model and on what a store holds, and finding it means changing the value and
    # LOOKING at the edges it produced — a restart per attempt means nobody tunes it at all.
    # The floor's own floor is 0.05, not 0.0: this path REJECTS out-of-bounds rather than
    # clamping, and a 0.0 accepted here would be silently replaced by the shipped default on
    # the next load (a PATCH that reports success and changes nothing). Refusing it says so.
    # The ceiling is 1.0 because the value is a cosine similarity and nothing above 1.0 is
    # satisfiable. Note these three are validated INDEPENDENTLY: a degree cap set below the
    # top-K is accepted by both entries and only the pass can see that they disagree.
    "knowledge.similarity_min_score": {"type": "float", "min": 0.05, "max": 1.0},
    "knowledge.similarity_top_k": {"type": "int", "min": 1, "max": 64},
    "knowledge.similarity_degree_cap": {"type": "int", "min": 1, "max": 512},
    # KL-15. The ceiling is generous because the real limit is the PROVIDER's and core cannot
    # know it — an oversized batch is discovered by bisection and costs a few extra calls, not
    # a failed import, so the bound here only needs to stop a typo, not to be correct.
    "knowledge.embed_batch_size": {"type": "int", "min": 1, "max": 2048},
    "knowledge.embed_retry_budget": {"type": "int", "min": 1, "max": 10},
    "knowledge.consolidate_min_cluster": {"type": "int", "min": 2, "max": 100},
    "knowledge.consolidate_min_hours": {"type": "int", "min": 0, "max": 720},
    "knowledge.session_brief_max_tokens": {"type": "int", "min": 0, "max": 8000},
    "knowledge.conflict_model_pass": {"type": "bool"},
    # PEP-7: the artifact→knowledge mirror's master switch. Live-editable deliberately — the
    # listener reads it per artifact save, so turning it on backfills and turning it off stops
    # new indexing without a restart. Nothing already indexed is removed by turning it off;
    # that would delete search state on a settings toggle.
    "knowledge.auto_ingest_artifacts": {"type": "bool"},
    # REMOTE-USER-AUTH C4 — the owner-login knobs. Runtime-editable so turning login on
    # or off, or loosening a lockout you tripped, takes effect on the next request without
    # a restart. The PASSWORD is deliberately NOT here and never will be: a credential is
    # not a setting, it goes through `personalclaw auth set-password` / the enroll flow so
    # the plaintext never rides in a PATCH body that lands in a request log. `public_url`
    # is likewise excluded — widening a network surface should be a deliberate file edit.
    "auth.login_enabled": {"type": "bool"},
    "auth.require_totp": {"type": "bool"},
    "auth.session_ttl": {"type": "duration"},
    "auth.lockout_threshold": {"type": "int", "min": 1, "max": 100},
    "auth.lockout_window": {"type": "duration"},
    # Platform-legibility toggles (§6 Discover tips, §7 context adapters).
    # discover_tips gates the propose-don't-write Discover section + hub;
    # context_adapters gates writing adapter files into opted-in project workspaces.
    "legibility.discover_tips": {"type": "bool"},
    "legibility.context_adapters": {"type": "bool"},
    # Ambient surfaces (AMBIENT-SURFACES) — the composable home + generative-UI +
    # surface-layer + tray knobs. surfaces_max_layer is the safe-mode ceiling.
    "ambient.tiles_enabled": {"type": "bool"},
    "ambient.max_tiles": {"type": "int", "min": 1, "max": 48},
    "ambient.default_refresh_ttl_secs": {"type": "int", "min": 30, "max": 86400},
    "ambient.genui_enabled": {"type": "bool"},
    "ambient.surfaces_max_layer": {"type": "int", "min": 0, "max": 2},
    "ambient.tray_enabled": {"type": "bool"},
    # Companion apps (COMPANION-APPS CA-4) — LAN discovery advertisement + the friendly
    # instance name a client shows. discovery_enabled is off by default; toggling it here
    # is the opt-in to announcing this gateway on the local network.
    "companion.discovery_enabled": {"type": "bool"},
    "companion.instance_name": {"type": "str", "max_len": 64},
    # Local models (LOCAL-MODEL-MANAGER-V2 §9) — the memory-pressure warning threshold the
    # loaded-models bar reads, and the crashed-sidecar respawn budget. Both are advisory
    # knobs on the user's own machine: the threshold blocks nothing, and the restart bound
    # only decides when the runner stops retrying a genuinely broken install.
    "local_models.pressure_warn_pct": {"type": "int", "min": 1, "max": 100},
    "local_models.sidecar_restart_max": {"type": "int", "min": 0, "max": 20},
    # LMMV-8: the fit budget's OS/runtime reserve and the browse filter's default. The
    # reserve is bounded at 64 GB — the same window the loader clamps to — so a UI edit
    # cannot make every model read as unrunnable; out-of-range values are REJECTED here
    # rather than clamped, so a PATCH that "succeeded" never means a different number
    # than the one the user typed. Neither knob blocks a download or a load.
    "local_models.memory_reserve_gb": {"type": "float", "min": 0.0, "max": 64.0},
    "local_models.hide_unrunnable_models": {"type": "bool"},
    # Watched sources (WATCHED-SOURCES SC#12) — the poll engine's runtime knobs. The
    # network floor is bounded at 300s (the R1-class rate floor) so a UI edit cannot make
    # the engine poll a third party abusively.
    "sources.enabled": {"type": "bool"},
    "sources.poll_interval_default_secs": {"type": "int", "min": 300, "max": 604800},
    "sources.network_floor_secs": {"type": "int", "min": 300, "max": 604800},
    "sources.max_sources": {"type": "int", "min": 1, "max": 1000},
    "sources.max_items_per_poll": {"type": "int", "min": 1, "max": 1000},
    "sources.daily_request_budget": {"type": "int", "min": 1, "max": 100000},
    # Packs (AGENT-PACKS §8) — the runtime-editable subset. The fingerprint toggle and the
    # skill-catalog list are the knobs a user reaches for from Settings; the catalog-refresh
    # URL is a plain string. No credential rides any of these (a connector credential goes to
    # the credential store, never a config field).
    "packs.fingerprint_enabled": {"type": "bool"},
    "packs.connector_catalog_url": {"type": "str", "max_len": 512},
    "packs.skill_catalogs": {"type": "skill_catalogs"},
    # Apps (ECOSYSTEM-TOOLING T2.2) — whether the curated registry ships as a default Store
    # source. Editable because it is the operator's opt-out for a shipped NETWORK source; it
    # only gates SEEDING, so a PATCH cannot retract a row already in app-sources.json (the
    # Store's remove control does that, and that removal persists).
    "apps.registry_source_enabled": {"type": "bool"},
}


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

    path_key = body.get("path", "")
    value = body.get("value")
    spec = _EDITABLE_CONFIG.get(path_key)
    if not spec:
        return _deny(f"field not editable: {path_key}", f"{path_key}={value}")

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
            # Through `atomic_write`, NOT `agent._atomic_json_write`: the latter does its own
            # mkstemp+rename and so never fires the post-write hook, which is the ONE seam
            # time-travel's debounced committer subscribes to. With the bypass, every config
            # change made from Settings — the primary writer of this file — landed on disk
            # without ever reaching the `config` state-history root, so the root stayed empty
            # and "roll back my settings" had nothing to roll back to. The PUT path two
            # hundred lines up already writes this same file this same way.
            atomic_write(cfg_path, json.dumps(data, indent=2) + "\n", fsync=True)
        except OSError:
            _log_sel("error", f"{path_key}=write_failed")
            return web.json_response({"error": "failed to write config file"}, status=500)

    _log_sel("success", f"{path_key}={value}")

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

    cfg = AppConfig.load()
    return web.json_response(cfg.to_dict())


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
    try:
        body = await request.json()
    except Exception:
        body = {}
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

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not (isinstance(body, dict) and body.get("confirm") is True):
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
    try:
        body = await request.json()
    except Exception:
        body = {}
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
    """GET /api/sessions/{id}/agents — list sub-agent results for a session."""
    session_id = request.match_info["id"]
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
