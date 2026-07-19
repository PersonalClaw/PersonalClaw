"""Action catalog, agent-scoped lifecycle view, and the external-webhook→agent
runner. Lifecycle/schedule trigger CRUD lives in handlers/triggers.py."""

import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import web

from personalclaw.dashboard.state import DashboardState

logger = logging.getLogger(__name__)


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811
    return _pkg.sel()


def _path_home_pclaw() -> Path:
    """Resolve PersonalClaw home dir, honoring PERSONALCLAW_HOME."""
    try:
        from personalclaw.config.loader import config_dir as _cd
        return _cd()
    except Exception:
        return Path.home() / ".personalclaw"


# ── Script Hooks ──


def _get_hook_store(state: DashboardState):
    """Lazy-init ScriptHookStore on DashboardState."""
    if state._hook_store is None:
        from personalclaw.hooks import (  # noqa: F811  # circular import
            ScriptHookStore,
            set_global_hook_store,
        )

        state._hook_store = ScriptHookStore()
        set_global_hook_store(state._hook_store)
    return state._hook_store


async def api_action_providers(request: web.Request) -> web.Response:
    """GET /api/action-providers — the registered action providers + their
    config schemas, so the Hooks UI is schema-driven (no hardcoded provider
    list). Each entry: {name, display_name, supports_blocking, settingsSchema}.
    The schema comes from each provider's bundled extension manifest."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
        list_action_providers,
    )

    _ensure_default_providers_registered()

    # Manifest schemas keyed by provider name (from the bundled action extensions).
    schemas: dict[str, dict] = {}
    try:
        from personalclaw.providers.registry import get_provider_registry

        for ext in get_provider_registry().list_extensions():
            pc = ext.provider_config
            if getattr(pc, "type", "") != "action":
                continue
            # The bundled manifest's name maps 1:1 to the provider by capability.
            schemas[ext.name] = getattr(pc, "settingsSchema", {}) or {}
    except Exception:
        logger.debug("action-providers: manifest schema lookup failed", exc_info=True)

    # Map provider runtime name → its manifest schema. Bundled action manifests
    # are named "<provider>-action" (bash-action, notify-action, …); fall back to {}.
    def _schema_for(provider_name: str) -> dict:
        return schemas.get(f"{provider_name}-action", {})

    result: list[dict] = []
    for name in sorted(list_action_providers()):
        prov = get_action_provider(name)
        if prov is None:
            continue
        result.append({
            "name": name,
            "display_name": getattr(prov, "display_name", name),
            "supports_blocking": bool(getattr(prov, "supports_blocking", False)),
            "settingsSchema": _schema_for(name),
        })
    return web.json_response({"providers": result})


async def api_agent_hooks(request: web.Request) -> web.Response:
    """GET /api/agent-hooks — read-only view of agent hooks from personalclaw.json."""
    from personalclaw.agent import _VALID_HOOK_EVENTS, AGENTS_DIR, _shipped_defaults
    from personalclaw.security import redact

    agent_cfg = AGENTS_DIR / "personalclaw.json"
    try:
        raw = json.loads(agent_cfg.read_text())
        hooks = raw.get("hooks", {}) if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        hooks = {}
    # Load bundled defaults to tag source
    try:
        raw = json.loads(_shipped_defaults().read_text())
        bundled = raw.get("hooks", {}) if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        bundled = {}
    bundled_keys: set[tuple[str, str, str]] = set()
    for event, entries in bundled.items():
        for e in entries if isinstance(entries, list) else []:
            if isinstance(e, dict):
                bundled_keys.add((event, e.get("command") or "", e.get("matcher") or ""))
    result: dict[str, list[dict]] = {}
    for event, entries in hooks.items():
        if event not in _VALID_HOOK_EVENTS:
            continue  # drop unknown/injected event keys
        tagged = []
        for e in entries if isinstance(entries, list) else []:
            if isinstance(e, dict):
                key = (event, e.get("command") or "", e.get("matcher") or "")
                # redact() wraps redact_exfiltration_urls + redact_credentials
                tagged.append({
                    "command": redact(e.get("command") or ""),
                    "matcher": redact(e.get("matcher") or ""),
                    "source": "bundled" if key in bundled_keys else "user",
                })
        if tagged:
            result[event] = tagged
    return web.json_response({"hooks": result})


# ── Webhook Hooks — external triggers run an agent turn via /hooks/agent ──

_HOOK_SESSION_PREFIX = "hook:"
_HOOK_TIMEOUT_DEFAULT = 599  # ~10 min — prime to avoid thundering herd with cron intervals
_HOOK_TIMEOUT_MAX = 3593  # ~1 hour — prime for same reason
_HOOK_STORE_PATH = _path_home_pclaw() / "hooks.json"
_HOOK_MESSAGE_MAX_LEN = 49_999  # ~50K chars — leave 1 char headroom
_HOOK_MAX_CONCURRENT = 6
_hook_semaphore = asyncio.Semaphore(_HOOK_MAX_CONCURRENT)


def _load_hook_context(hook_id: str) -> str:
    """Load context_summary from hooks.json for a registered hook.

    Uses a three-horizon decay strategy for context freshness:
    Horizon 1 (< 1h): full context injected verbatim
    Horizon 2 (1-24h): context injected with staleness warning
    Horizon 3 (> 24h): context skipped (too stale to be useful)
    """
    if not _HOOK_STORE_PATH.exists():
        return ""
    try:
        hooks = json.loads(_HOOK_STORE_PATH.read_text(encoding="utf-8"))
        entry = hooks.get(hook_id, {})
        ctx = entry.get("context_summary", "") or entry.get("summary", "")
        if not ctx:
            return ""
        registered = entry.get("registered_at", 0)
        if not registered:
            return ""  # unknown age — treat as expired
        age_hours = (time.time() - registered) / 3600
        if age_hours > 24:
            return ""  # horizon 3: too stale
        if age_hours > 1:
            return f"[Context from {age_hours:.0f}h ago — may be outdated]\n{ctx}"
        return ctx
    except (ValueError, OSError):
        return ""


def _verify_hook_token(request: web.Request) -> bool:
    """Verify Bearer token against hooks.webhook_token in config."""
    import hmac  # noqa: F811

    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    token = cfg.hooks.get("webhook_token", "")
    if not token:
        return False
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return hmac.compare_digest(auth[7:], token)
    return hmac.compare_digest(request.headers.get("x-personalclaw-token", ""), token)


async def api_hooks_agent(request: web.Request) -> web.Response:
    """POST /api/hooks/agent — run an agent turn from an external webhook.

    Runs in an isolated session keyed by ``sessionKey``. Reuses live sessions,
    resumes expired ones via session/load, or creates fresh sessions as fallback.

    Payload:
        message (str, required): prompt for the agent
        sessionKey (str): session routing key (must start with "hook:")
        name (str): human-readable label for notifications
        agent (str): agent name for routing (default: personalclaw)
        deliver (bool): send result to the channel DM + dashboard notification
        timeoutSeconds (int): max agent run duration
    """

    if not _verify_hook_token(request):
        _sel().log_api_access(
            caller="webhook",
            operation="hooks.agent",
            outcome="denied",
            source="webhook",
            error="invalid token",
        )
        return web.json_response({"error": "unauthorized"}, status=401)

    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    message = (body.get("message") or "").strip()
    if not message:
        return web.json_response({"error": "message required"}, status=400)
    if len(message) > _HOOK_MESSAGE_MAX_LEN:
        return web.json_response(
            {"error": f"message exceeds {_HOOK_MESSAGE_MAX_LEN} chars"}, status=400
        )

    session_key = body.get("sessionKey", "")
    if not session_key:
        session_key = f"hook:default:{int(time.time())}"
    if not session_key.startswith(_HOOK_SESSION_PREFIX):
        return web.json_response(
            {"error": f"sessionKey must start with '{_HOOK_SESSION_PREFIX}'"}, status=400
        )

    name = body.get("name", "Webhook")
    agent = body.get("agent", "") or None
    deliver = body.get("deliver", True)
    try:
        timeout_secs = max(
            60,
            min(int(body.get("timeoutSeconds", _HOOK_TIMEOUT_DEFAULT)), _HOOK_TIMEOUT_MAX),
        )
    except (ValueError, TypeError):
        return web.json_response({"error": "timeoutSeconds must be an integer"}, status=400)

    # Fire-and-forget: run agent in background, return immediately
    if _hook_semaphore.locked():
        _sel().log_api_access(
            caller="webhook",
            operation="hooks.agent",
            outcome="rejected",
            source="webhook",
            resources=session_key,
            error="capacity reached",
        )
        return web.json_response(
            {"error": f"hook capacity reached ({_HOOK_MAX_CONCURRENT})"}, status=429
        )
    await _hook_semaphore.acquire()  # immediate — no race in single-threaded asyncio
    _sel().log_api_access(
        caller="webhook",
        operation="hooks.agent",
        outcome="accepted",
        source="webhook",
        resources=session_key,
    )
    try:
        task = asyncio.create_task(
            _run_hook_agent(state, session_key, message, name, agent, deliver, timeout_secs)
        )
    except BaseException:
        _hook_semaphore.release()
        raise
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)

    return web.json_response({"status": "accepted", "sessionKey": session_key})


async def _run_hook_inner(
    state: DashboardState, session_key: str, message: str, agent: str | None
) -> str:
    """Inner agent turn — called within timeout wrapper."""
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK  # noqa: F811

    client, is_new, resumed = await state.sessions.get_or_create(session_key, agent=agent)
    full_message = message
    if is_new and state.context_builder:
        full_message, _ = state.context_builder.build_message(
            message, is_new, session_key, agent=agent, resumed=resumed,
        )
    result_text = ""
    async for event in client.stream(full_message):
        if event.kind == EVENT_TEXT_CHUNK:
            result_text += event.text
        elif event.kind == EVENT_COMPLETE:
            break
    state.sessions.record_success(session_key)  # sync; record_failure is async
    return result_text


async def _run_hook_agent(
    state: DashboardState,
    session_key: str,
    message: str,
    name: str,
    agent: str | None,
    deliver: bool,
    timeout_secs: int,
) -> None:
    """Execute a webhook-triggered agent turn in an ephemeral session.

    Sessions are always destroyed after the turn completes (like subagents).
    Context continuity across webhook calls is provided by hooks.json —
    the agent calls ``hook_register`` to persist context_summary, and this
    handler injects it into the next fresh session.
    """
    from personalclaw.security import redact_credentials, redact_exfiltration_urls  # noqa: F811

    # Load persisted context from hooks.json (written by hook_register MCP tool)
    hook_id = session_key.removeprefix(_HOOK_SESSION_PREFIX)
    saved_context = _load_hook_context(hook_id)
    if saved_context:
        message = (
            f"=== Restored Context (from prior session) ===\n"
            f"{saved_context}\n"
            f"=== End Restored Context ===\n\n"
            f"{message}"
        )

    result_text = ""
    outcome = "completed"
    try:
        result_text = await asyncio.wait_for(
            _run_hook_inner(state, session_key, message, agent), timeout=timeout_secs
        )
    except asyncio.TimeoutError:
        outcome = "timeout"
        result_text = f"Hook agent timed out after {timeout_secs}s"
        logger.warning("Hook agent timeout: %s", session_key)
        await state.sessions.record_failure(session_key)
    except Exception:
        outcome = "error"
        result_text = f"Hook agent error: internal failure (session {session_key})"
        logger.exception("Hook agent failed for %s", session_key)
        await state.sessions.record_failure(session_key)
    finally:
        try:
            state.sessions.release(session_key)
        except Exception:
            logger.exception("Hook session release failed: %s", session_key)
        try:
            await state.sessions.reset(session_key)
        except Exception:
            logger.exception("Hook session reset failed: %s", session_key)
        finally:
            _hook_semaphore.release()

    _sel().log_tool_invocation(
        session_key=session_key,
        source="webhook",
        tool_name="hooks.agent",
        outcome=outcome,
        downstream_service="channel" if deliver else "internal",
    )
    logger.info("Hook agent %s: %s (%d chars)", outcome, session_key, len(result_text))

    if not result_text:
        return

    # Sanitize before delivery
    result_text, _ = redact_exfiltration_urls(result_text)
    result_text, _ = redact_credentials(result_text)

    if deliver:
        name_safe, _ = redact_exfiltration_urls(name)
        name_safe, _ = redact_credentials(name_safe)
        title = f"🪝 {name_safe}"
        state.notify("hook", title, result_text[:2000], meta={"session_key": session_key})
        if state.channel_delivery and state.owner_id:
            try:
                channel = await state.channel_delivery.open_dm(state.owner_id)
                if channel:
                    await state.channel_delivery.deliver_text(
                        channel, f"*{title}*\n{result_text[:3000]}"
                    )
            except Exception:
                logger.exception("Hook agent: channel delivery failed")
