"""Core handlers — page serving, branding, STT transcribe, config, SEL, auth, session workspace."""

import asyncio
import hmac
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

import personalclaw.validation as _validation_mod
from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import AppConfig
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
  <p class="note">Or install from a <a href="https://github.com/PersonalClaw/PersonalClaw/releases">release</a>
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


# ── STT (Speech-to-Text) ──


async def api_stt_transcribe(request: web.Request) -> web.Response:
    """POST /api/stt/transcribe — transcribe uploaded audio via the active STT model."""
    import tempfile  # noqa: F811

    from personalclaw.transcribe import is_available, transcribe_audio  # noqa: F811

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
            {"error": f"failed to parse multipart body: {exc}"}, status=400,
        )
    field = await reader.next()
    if field is None or not hasattr(field, "name") or field.name != "audio":  # type: ignore[union-attr]
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
                        {"error": check_upload(fname, field_mime, size=size, override_limit=_stt_cap).reason},
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
        return web.json_response({"text": text or ""})
    except Exception:
        logger.exception("STT transcribe failed")
        return web.json_response({"error": "transcription failed"}, status=500)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ── Security Event Log API ──


async def api_sel_events(request: web.Request) -> web.Response:
    """GET /api/sel/events — recent security events."""

    try:
        limit = min(int(request.query.get("limit", "100")), 1000)
    except (TypeError, ValueError):
        limit = 100
    events = _sel().recent(limit=limit)
    return web.json_response({"events": events, "count": len(events)})


async def api_sel_verify(request: web.Request) -> web.Response:
    """GET /api/sel/verify — verify HMAC chain integrity over the recent window.

    The SEL log is append-only and unbounded, so we sample-verify the most recent
    entries (fast, bounded) rather than walking the whole chain. ``full=1`` forces
    an exhaustive check.
    """
    from personalclaw.sel import _VERIFY_WINDOW

    full = request.query.get("full") in ("1", "true", "yes")
    checked, valid = _sel().verify_integrity(max_entries=None if full else _VERIFY_WINDOW)
    return web.json_response(
        {
            "valid": checked == valid,
            "count": checked,
            "tampered": checked - valid,
            "integrity": "ok" if checked == valid else "compromised",
            "windowed": not full,
        }
    )


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

    ``builtin`` is always-on and read-only; ``user`` is the editable list
    persisted at ``security.denied_commands`` (edit via PATCH /api/config/personalclaw).
    """
    from personalclaw.security import BUILTIN_DENIED_COMMAND_PATTERNS

    user = list(AppConfig.load().security.denied_commands)
    return web.json_response(
        {"builtin": list(BUILTIN_DENIED_COMMAND_PATTERNS), "user": user}
    )


async def api_security_egress(_request: web.Request) -> web.Response:
    """GET /api/security/egress — the operator's outbound-egress overrides for the
    Security panel. Defaults (public-only, no allow/deny) are enforced in code; these
    are the self-hoster's relaxations, edited via PATCH /api/config/personalclaw
    ``security.egress``."""
    eg = AppConfig.load().security.egress
    return web.json_response({
        "allow_hosts": list(eg.allow_hosts),
        "deny_hosts": list(eg.deny_hosts),
        "allow_private": bool(eg.allow_private),
    })


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
        # (lower, upper) per field. max_subagents accepts 0 = auto-size from host.
        limits = {"subagent_max_turns": (1, 200), "max_subagents": (0, 16)}
        applied: list[str] = []
        for key, (lower, upper) in limits.items():
            if key in agent_settings:
                val = agent_settings[key]
                if isinstance(val, bool) or not isinstance(val, int) or val < lower or val > upper:
                    return _deny(f"{key} must be an integer between {lower} and {upper}")
                agent[key] = val
                applied.append(key)
        # Boolean toggles
        for key in ("orchestrator_skill",):
            if key in agent_settings:
                val = agent_settings[key]
                if not isinstance(val, bool):
                    return _deny(f"{key} must be a boolean")
                agent[key] = val
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
    "security.denied_commands": {"type": "str_list", "max_items": 100, "each_regex": True},
    "security.egress": {"type": "egress"},
    "tools.projection_rules": {"type": "projection_rules"},
    "agent.orchestrator_skill": {"type": "bool"},
    "agent.acp_concurrent_sessions": {"type": "bool"},
    # The assistant's display name — consumed by the prompt engine ({{bot_name}}
    # template var + ContextBuilder). Sanitized at the write boundary (strip
    # markdown/braces, ≤50 chars) so the FILE matches what load() produces —
    # load() applies the same function, defense in depth for hand-edits.
    "agent.bot_name": {"type": "str", "max_len": 50, "sanitize": _bot_name_sanitizer},
    "agent.log_level": {"type": "enum", "values": ["DEBUG", "INFO", "WARNING", "ERROR"]},
    "session.timeout_secs": {"type": "int", "min": 0, "max": 86400},
    "session.autocompact_pct": {"type": "float", "min": 5.0, "max": 90.0},
    "session.pool_size": {"type": "int", "min": 0, "max": 10},
    "session.pool_agent": {"type": "str", "values_fn": _agent_values},
    "session.pool_ttl_secs": {"type": "int", "min": 0, "max": 7200},
    "auto_update": {"type": "bool"},
    "dashboard.mcp_probe_timeout_secs": {"type": "int", "min": 5, "max": 120},
    # P25: opt-in tmux-backed terminal persistence (survives a gateway restart). Read as a
    # raw dict from config.json by handlers/terminal.py::_get_config — a 3-part nested path.
    "dashboard.terminal.persist": {"type": "bool"},
    "inbox.engagement_ranking_enabled": {"type": "bool"},
    "inbox.engagement_half_life_days": {"type": "float", "min": 0.0, "max": 365.0},
    # Gates the poll-based message sources (filesystem/channel apps). The UI
    # toggle calls /api/inbox/restart after flipping so the service re-attaches.
    "inbox.enabled": {"type": "bool"},
}


async def api_personalclaw_config_patch(request: web.Request) -> web.Response:
    """PATCH /api/config/personalclaw — update a single config field."""
    from personalclaw.agent import _atomic_json_write  # noqa: F811
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

    # Validate value
    if spec["type"] == "enum":
        if value not in spec["values"]:
            return _deny(f"invalid value, must be one of {spec['values']}", f"{path_key}={value}")
    elif spec["type"] == "int":
        try:
            value = int(value)
        except (TypeError, ValueError):
            return _deny("must be an integer", f"{path_key}={value}")
        lo, hi = spec.get("min", 0), spec.get("max", 999999)
        if value < lo or value > hi:
            return _deny(f"must be between {lo} and {hi}", f"{path_key}={value}")
    elif spec["type"] == "bool":
        if not isinstance(value, bool):
            return _deny("must be a boolean", f"{path_key}={value}")
    elif spec["type"] == "float":
        try:
            value = float(value)
        except (TypeError, ValueError):
            return _deny("must be a number", f"{path_key}={value}")
        if not math.isfinite(value):
            return _deny("must be a finite number", f"{path_key}={value}")
        lo, hi = spec.get("min", 0.0), spec.get("max", 999999.0)
        if value < lo or value > hi:
            return _deny(f"must be between {lo} and {hi}", f"{path_key}={value}")
    elif spec["type"] == "str_list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return _deny("must be a list of strings", f"{path_key}={value}")
        max_items = spec.get("max_items", 20)
        if len(value) > max_items:
            return _deny(f"must have at most {max_items} items", f"{path_key}={value}")
        if spec.get("each_regex"):
            for v in value:
                try:
                    re.compile(v)
                except re.error as exc:
                    return _deny(f"invalid regex {v!r}: {exc}", f"{path_key}={value}")
    elif spec["type"] == "str":
        if not isinstance(value, str):
            return _deny("must be a string", f"{path_key}={value}")
        max_len = spec.get("max_len", 256)
        if len(value) > max_len:
            return _deny(f"must be at most {max_len} characters", f"{path_key}={value}")
        if "values" in spec and value not in spec["values"]:
            return _deny(f"invalid value, must be one of {spec['values']}", f"{path_key}={value}")
        values_fn = spec.get("values_fn")
        if values_fn and value not in values_fn():
            return _deny(f"invalid value for {path_key}", f"{path_key}={value}")
        # Normalise at the WRITE boundary so the file matches what load() will
        # produce — otherwise the file carries the raw value (e.g. markdown/brace
        # syntax in bot_name) while runtime sees the sanitized one: split-brain.
        sanitize = spec.get("sanitize")
        if sanitize:
            value = sanitize(value)
    elif spec["type"] == "egress":
        # The operator egress overrides object: {allow_hosts:[str], deny_hosts:[str],
        # allow_private:bool}. Normalise to exactly those keys so a stray field can't be
        # smuggled into config. Hosts are bare domains/hostnames (no scheme/path).
        if not isinstance(value, dict):
            return _deny("must be an object", f"{path_key}={value}")
        clean: dict[str, Any] = {}
        for key in ("allow_hosts", "deny_hosts"):
            hosts = value.get(key, [])
            if not isinstance(hosts, list) or not all(isinstance(h, str) for h in hosts):
                return _deny(f"{key} must be a list of strings", f"{path_key}.{key}")
            if len(hosts) > 100:
                return _deny(f"{key} must have at most 100 items", f"{path_key}.{key}")
            # A host entry is a bare domain/hostname — reject anything with a scheme,
            # path, or whitespace (a URL in the allow-list would be a footgun).
            for h in hosts:
                if "/" in h or ":" in h or " " in h or len(h) > 253:
                    return _deny(f"invalid host {h!r} (bare domain/hostname only)", f"{path_key}.{key}")
            clean[key] = hosts
        ap = value.get("allow_private", False)
        if not isinstance(ap, bool):
            return _deny("allow_private must be a boolean", f"{path_key}.allow_private")
        clean["allow_private"] = ap
        value = clean
    elif spec["type"] == "projection_rules":
        # A list of user-taught tool-output projection rules (TokenJuice OP6):
        # [{name, match_regex, strategy}]. Normalise to exactly those keys; each regex
        # must compile + each strategy must be a known builtin projector. Declarative
        # only (no code) — a bad rule is rejected here, never at dispatch time.
        from personalclaw.tool_providers.projection import _PROJECTORS  # noqa: F811
        if not isinstance(value, list):
            return _deny("must be a list", f"{path_key}={value}")
        if len(value) > 50:
            return _deny("must have at most 50 rules", f"{path_key}")
        strategies = set(_PROJECTORS)  # log/diff/json/test/csv
        clean_rules: list[dict[str, str]] = []
        for i, r in enumerate(value):
            if not isinstance(r, dict):
                return _deny("each rule must be an object", f"{path_key}[{i}]")
            name = str(r.get("name", "")).strip()[:80]
            rx = str(r.get("match_regex", "")).strip()
            strat = str(r.get("strategy", "")).strip().lower()
            if not rx:
                return _deny("each rule needs a match_regex", f"{path_key}[{i}]")
            if len(rx) > 500:
                return _deny("match_regex too long (max 500)", f"{path_key}[{i}]")
            try:
                re.compile(rx)
            except re.error as exc:
                return _deny(f"invalid regex {rx!r}: {exc}", f"{path_key}[{i}]")
            if strat not in strategies:
                return _deny(f"strategy must be one of {sorted(strategies)}", f"{path_key}[{i}]")
            clean_rules.append({"name": name, "match_regex": rx, "strategy": strat})
        value = clean_rules
    else:
        return _deny("unsupported config type", f"{path_key}={value}", 500)

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
            _atomic_json_write(cfg_path, data)
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

    # Live-apply tool-output projection rules (TokenJuice OP6) so an edit takes effect
    # immediately (no restart) — mirrors the startup install into the projection engine.
    if path_key == "tools.projection_rules":
        try:
            from personalclaw.tool_providers import projection  # noqa: F811
            projection.set_user_rules([
                projection.ProjectionRule(
                    name=r.get("name", ""), match_regex=r.get("match_regex", ""),
                    strategy=r.get("strategy", "log"))
                for r in value
            ])
        except Exception:
            logger.exception("Failed to live-apply projection rules")

    cfg = AppConfig.load()
    return web.json_response(cfg.to_dict())


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
