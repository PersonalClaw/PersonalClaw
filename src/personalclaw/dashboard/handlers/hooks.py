"""Action catalog, agent-scoped lifecycle view, and the external-webhook→agent
runner. Lifecycle/schedule trigger CRUD lives in handlers/triggers.py."""

import asyncio
import json
import logging
import time

from aiohttp import web

from personalclaw import memory_writes, notification_kinds, session_keys
from personalclaw.constants import HOOK_SESSION_PREFIX
from personalclaw.dashboard.state import DashboardState
from personalclaw.guardrails.failure import BudgetExceededError
from personalclaw.request_validation import bool_field
from personalclaw.turn_streams import closing_stream

logger = logging.getLogger(__name__)


def _sel():
    """Late-binding _sel() for test monkeypatch compatibility."""
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811

    return _pkg.sel()


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
    list). Each entry: {name, display_name, supports_blocking, settingsSchema,
    internal, invokes_model}. The schema comes from each provider's bundled
    extension manifest.

    `internal` marks plumbing a system-owned automation dispatches (the Self-QA steps): it stays
    in this catalog because a row that already names one must still render its label, and the
    create form's picker is what leaves it out. `invokes_model` is the trigger substrate's own
    classification (`triggers.models.ZERO_TOKEN_PROVIDERS`), so the form's cadence-floor hint
    and the list's "check schedule" warning answer the same question the same way."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
        list_action_providers,
    )
    from personalclaw.triggers.models import provider_is_zero_token

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
        result.append(
            {
                "name": name,
                "display_name": getattr(prov, "display_name", name),
                "supports_blocking": bool(getattr(prov, "supports_blocking", False)),
                "settingsSchema": _schema_for(name),
                "internal": bool(getattr(prov, "internal", False)),
                "invokes_model": not provider_is_zero_token(name),
            }
        )
    return web.json_response({"providers": result})


async def api_agent_hooks(request: web.Request) -> web.Response:
    """GET /api/agent-hooks — the agent CLI's hooks in effect, and the ones waiting for the owner.

    In effect: what ``personalclaw.json`` merges. Waiting: the ones it leaves out until the owner
    allows them (`agent.user_hooks_waiting`)."""
    from personalclaw.agent import (
        _VALID_HOOK_EVENTS,
        _shipped_defaults,
        agents_dir,
        user_hooks_waiting,
    )
    from personalclaw.security import redact

    agent_cfg = agents_dir() / "personalclaw.json"
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
                tagged.append(
                    {
                        "command": redact(e.get("command") or ""),
                        "matcher": redact(e.get("matcher") or ""),
                        "source": "bundled" if key in bundled_keys else "user",
                    }
                )
        if tagged:
            result[event] = tagged
    waiting = [
        {
            "event": w["event"],
            "command": w["command"],
            "matcher": w["matcher"],
            "seal": w["seal"],
        }
        for w in user_hooks_waiting()
    ]
    return web.json_response({"hooks": result, "waiting": waiting})


async def api_agent_hook_allow(request: web.Request) -> web.Response:
    """POST /api/agent-hooks/allow — the owner's yes to one waiting agent hook.

    Body: ``{event, command, matcher, seal, confirm}``, the first four from ``waiting``. Asks first
    (``400 confirmation_required``), then records the yes sealed to the file as the page read it
    (`agent_hook_grants`) — 409 when the file changed since, so a yes is never to bytes the page
    did not show — rebuilds the agent CLI's config and restarts its sessions, so the hook is in
    effect at once. Owner-only: no app declaration reaches it (`apps/permissions`). Both answers
    are written to the security audit.
    """
    from personalclaw import agent_hook_grants
    from personalclaw.agent import rebuild_agent_config, user_hooks_waiting
    from personalclaw.http_errors import consent_required, json_error
    from personalclaw.safety_flags import confirm_granted

    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_request", message="The body must be a JSON object.", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_request", message="The body must be a JSON object.", status=400)
    event, command = str(body.get("event") or ""), str(body.get("command") or "")
    matcher = str(body.get("matcher") or "")
    hook = next(
        (
            w
            for w in user_hooks_waiting()
            if (w["event"], w["command"], w["matcher"]) == (event, command, matcher)
        ),
        None,
    )
    if hook is None:
        return json_error(
            "not_found", message="No agent hook with that event and file is waiting.", status=404
        )
    caller = request.get("user", "dashboard")
    resource = f"agent_hooks.{event}: {command}"
    if body.get("seal") != hook["seal"]:
        return json_error(
            "stale_write",
            message=f"“{command}” changed since this page read it. Look at it again first.",
            status=409,
        )
    if not confirm_granted(body):
        _sel().log_api_access(
            caller=caller,
            operation="agent_hook.grant",
            outcome="denied",
            source="dashboard",
            resources=f"{resource}: allowing without confirm",
        )
        return consent_required(
            f"agent_hooks.{event}",
            agent_hook_grants.consent(event, command),
            title=agent_hook_grants.CONSENT_TITLE,
        )
    if not agent_hook_grants.allow(event, command, matcher, seen=hook["seal"]):
        return json_error(
            "stale_write",
            message=f"“{command}” changed since this page read it. Look at it again first.",
            status=409,
        )
    _sel().log_api_access(
        caller=caller,
        operation="agent_hook.grant",
        outcome="success",
        source="dashboard",
        resources=resource,
    )
    rebuild_agent_config()
    from personalclaw.dashboard.handlers.sessions import _reset_all_sessions

    await _reset_all_sessions(request)
    return web.json_response({"ok": True})


# ── Webhook Hooks — external triggers run an agent turn via /hooks/agent ──

_HOOK_TIMEOUT_DEFAULT = 599  # ~10 min — prime to avoid thundering herd with cron intervals
_HOOK_TIMEOUT_MAX = 3593  # ~1 hour — prime for same reason
_HOOK_MESSAGE_MAX_LEN = 49_999  # ~50K chars — leave 1 char headroom
_HOOK_MAX_CONCURRENT = 6
_hook_semaphore = asyncio.Semaphore(_HOOK_MAX_CONCURRENT)


def _hook_token_refusal(request: web.Request) -> str:
    """Why the request's webhook token does not admit it, or ``""`` when it does.

    The token is ``hooks.webhook_token``: ``config.json`` holds a ``{{secret:…}}`` reference, and
    the token itself is in the credential store, resolved here, where it is used. Every request is
    refused while none is set, while the reference cannot be answered or names a credential
    another owner holds (refused and logged by ``resolve``), and while the token is shorter than
    ``auth.MIN_TOKEN_BYTES`` or is a credential that opens something else: it is the one thing
    between this route and an agent turn. Compared in constant time, as bytes, so a header that is
    not ASCII is a refusal and not a fault. The answer is the audit row's reason; the caller is told
    one sentence, whichever it was (``webhook.WEBHOOK_TOKEN_NEEDED``).
    """
    import hmac

    from personalclaw.config.loader import AppConfig
    from personalclaw.config.secret_refs import ForeignSecretReference, config_owner, resolve
    from personalclaw.inbound import auth, webhook

    try:
        token = resolve(AppConfig.load().hooks, owner=config_owner("hooks")).get("webhook_token")
    except ForeignSecretReference:
        return "hooks.webhook_token names a credential another owner holds"
    if not isinstance(token, str) or not token:
        return "no webhook token is set (hooks.webhook_token)"
    weak = auth.token_strength_problem(token, webhook.SURFACE)
    if weak:
        return f"the webhook token cannot be used: {weak}"
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        presented = header[len("Bearer ") :]
    else:
        presented = request.headers.get("x-personalclaw-token", "")
    if not presented:
        return "no webhook token presented"
    if not hmac.compare_digest(
        presented.encode("utf-8", "surrogateescape"), token.encode("utf-8", "surrogateescape")
    ):
        return "the token presented is not the webhook token"
    return ""


async def api_hooks_agent(request: web.Request) -> web.Response:
    """POST /api/hooks/agent — run an agent turn for an outside program's call.

    One of the webhook's two doors (``inbound.webhook``, which holds the rules and the words): it
    signs its caller in itself, with the owner's webhook token, so the dashboard's sign-in lets it
    through and the gateway's internal credential does not open it. Before it reads a body it asks,
    in order: the incident switch (503), that the request comes from this machine (403, and how to
    reach it from another), a rate (429), and the token (401). Every answer is a row of the inbound
    audit, and a refusal or an accepted call a row of the Security log too.

    Runs in an isolated session keyed by ``sessionKey``. Reuses live sessions,
    resumes expired ones via session/load, or creates fresh sessions as fallback.

    A caller that names each delivery with an ``Idempotency-Key`` header (``inbound.webhook``'s
    ``delivery_name``) has one it makes again for the same ``sessionKey``, inside a week, answered
    ``already_received``, with no second turn; its name is taken only with a turn that starts, so
    a retry of one refused for capacity runs. A header that names no delivery is refused (400).

    Payload:
        message (str, required): prompt for the agent
        sessionKey (str): session routing key (must start with "hook:")
        name (str): human-readable label for notifications
        agent (str): agent name for routing (default: personalclaw)
        deliver (bool): send result to the channel DM + dashboard notification
        timeoutSeconds (int): max agent run duration
    """
    from personalclaw.http_errors import json_error
    from personalclaw.inbound import caps as caps_mod
    from personalclaw.inbound import webhook as door
    from personalclaw.inbound.gate import incident_problem

    route = door.HOOK_ROUTE
    incident = incident_problem()
    if incident:
        return door.answer(
            json_error("service_unavailable", message=door.SUSPENDED, status=503),
            route=route,
            refused=incident,
        )
    away = door.off_machine(request)
    if away:
        return door.answer(
            json_error("forbidden", message=away, status=403),
            route=route,
            refused="a request from another address",
        )
    # Before the token, so a caller guessing at it is held to the rate.
    caps = caps_mod.caps_for(None)
    peer = request.headers.get("Host", "") + "|" + (request.remote or "")
    if not caps_mod.check_rate_for_client(door.SURFACE, "", peer, caps):
        wait = caps_mod.retry_after_for_client(door.SURFACE, "", peer, caps)
        return door.answer(
            json_error(
                "rate_limited",
                message=door.too_often(wait),
                status=429,
                headers={"Retry-After": str(wait)},
            ),
            route=route,
            refused="rate limit",
            rate_limited=True,
        )
    refused = _hook_token_refusal(request)
    if refused:
        return door.answer(
            json_error("unauthorized", message=door.WEBHOOK_TOKEN_NEEDED, status=401),
            route=route,
            refused=refused,
        )
    delivery, unnamed = door.delivery_name(request)
    if unnamed:
        return door.answer(
            json_error("invalid_request", message=unnamed, status=400),
            route=route,
            refused="a delivery name that names none",
        )

    state: DashboardState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return door.answer(
            json_error("invalid_json", message="The body must be JSON.", status=400),
            route=route,
            refused="invalid JSON",
        )
    if not isinstance(body, dict):
        return door.answer(
            json_error("invalid_body", message="The body must be a JSON object.", status=400),
            route=route,
            refused="the body is not a JSON object",
        )

    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        return door.answer(
            json_error("field_required", message="message is required, as a string.", status=400),
            route=route,
            refused="no message",
        )
    message = message.strip()
    if len(message) > _HOOK_MESSAGE_MAX_LEN:
        return door.answer(
            json_error(
                "invalid_request",
                message=f"message is longer than {_HOOK_MESSAGE_MAX_LEN} characters.",
                status=400,
            ),
            route=route,
            refused="message too long",
        )

    session_key = body.get("sessionKey", "")
    if not session_key:
        session_key = session_keys.WEBHOOK.key(f"default:{int(time.time())}")
    if not isinstance(session_key, str) or not session_key.startswith(HOOK_SESSION_PREFIX):
        return door.answer(
            json_error(
                "invalid_request",
                message=f"sessionKey must start with '{HOOK_SESSION_PREFIX}'.",
                status=400,
            ),
            route=route,
            refused="a sessionKey that is not a webhook's",
        )
    # 🔴 A callback the agent registered (`hook_register`) runs only once the owner allowed it
    # (`webhook_callbacks`): its turn starts from context the agent wrote and runs with the agent's
    # tools, so it follows the trigger rule. Refused before anything runs, and said so, so the
    # sender can tell a callback waiting for the owner from one that failed. A key nobody
    # registered is the owner's own integration and starts from nothing the agent wrote.
    from personalclaw import webhook_callbacks

    callback = webhook_callbacks.get(session_key.removeprefix(HOOK_SESSION_PREFIX))
    if callback is not None and not webhook_callbacks.allowed(callback):
        return door.answer(
            json_error(
                "not_allowed",
                message=(
                    "This callback has not been allowed to run. The owner allows it on the "
                    "Triggers page."
                ),
                status=403,
            ),
            route=route,
            refused=f"{session_key}: callback not allowed by the owner",
        )
    # What the agent saved for this callback's turn, read back through the injection screen and
    # fenced as data (`webhook_callbacks.restored_context`). Refused here, before anything runs,
    # when the screen refuses it, naming the pattern class and never the words, so the sender can
    # tell a callback whose context was refused from one that failed.
    from personalclaw.outside_text import Admitted

    restored = webhook_callbacks.restored_context(callback) if callback is not None else Admitted()
    if restored.refused:
        groups = ", ".join(restored.refused)
        return door.answer(
            json_error(
                "callback_context_refused",
                message=(
                    f"The injection screen refused the context this callback saved ({groups}), "
                    "so its turn did not start. The agent can register it again with other "
                    "context."
                ),
                status=409,
            ),
            route=route,
            refused=f"{session_key}: the injection screen refused its saved context ({groups})",
        )

    name = body.get("name", "Webhook")
    agent = body.get("agent", "") or None
    deliver = bool_field(body, "deliver", default=True)
    try:
        timeout_secs = max(
            60,
            min(int(body.get("timeoutSeconds", _HOOK_TIMEOUT_DEFAULT)), _HOOK_TIMEOUT_MAX),
        )
    except (ValueError, TypeError):
        return door.answer(
            json_error(
                "invalid_request", message="timeoutSeconds must be a whole number.", status=400
            ),
            route=route,
            refused="timeoutSeconds is not a number",
        )

    from personalclaw import received

    named = (door.HOOK_DELIVERIES, session_key, delivery)
    if delivery and received.seen(*named):
        return door.received_again(
            web.json_response(
                {"status": "accepted", "sessionKey": session_key, "already_received": True}
            ),
            route=route,
            resources=session_key,
        )

    # Fire-and-forget: run agent in background, return immediately
    if _hook_semaphore.locked():
        return door.answer(
            json_error(
                "too_many_concurrent_requests",
                message=(
                    f"PersonalClaw is already running {_HOOK_MAX_CONCURRENT} webhook turns. Try "
                    "again when one ends."
                ),
                status=429,
            ),
            route=route,
            refused="capacity reached",
        )
    await _hook_semaphore.acquire()  # immediate — no race in single-threaded asyncio
    try:
        task = asyncio.create_task(
            _run_hook_agent(
                state, session_key, message, name, agent, deliver, timeout_secs, restored.text
            )
        )
    except BaseException:
        _hook_semaphore.release()
        raise
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    if delivery:
        received.note(*named)

    door.accepted(route=route, resources=session_key, bytes_in=request.content_length or 0)
    return web.json_response({"status": "accepted", "sessionKey": session_key})


class HookTurnRefused(Exception):
    """A webhook's turn that cannot run under the headless profile: its reason, said as it is."""


async def _run_hook_inner(
    state: DashboardState, session_key: str, message: str, agent: str | None
) -> str:
    """Inner agent turn — called within timeout wrapper.

    It runs under the headless profile, as every turn nobody watches does (a ``hook:`` key is one,
    ``guardrails.policy``). Its tool grants are the profile's: a call whose tool does not declare
    it only reads is refused, whatever would approve it. And the runtime runs unattended: a tool
    that asks a person something is not offered, and a call that needs approval is declined at
    once, since nobody would see the prompt. A runtime that cannot be held to the grants (an agent
    CLI runs tools the host never sees) is refused before the message is sent.
    """
    from personalclaw.guardrails.policy import profile_for_session, tool_grants_held
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK  # noqa: F811
    from personalclaw.llm.events import EVENT_SPENT

    # The headless profile, the operator's ceiling applied. Read before anything starts: a ceiling
    # that cannot be read raises here, and the turn fails closed.
    profile = profile_for_session(session_key)
    # A webhook's agent turn is automation — an ephemeral agent session, as a subagent is — so it
    # rides the orchestration axis a subagent rides, which is what puts the spend guard on its
    # calls. On the chat binding it had none, and the daily cap never counted a webhook's spend.
    client, is_new, resumed = await state.sessions.get_or_create(
        session_key, agent=agent, model_axis="orchestration", unattended=True
    )
    with tool_grants_held(client, profile) as held:
        if not held:
            raise HookTurnRefused(
                f"{agent or 'The default agent'} runs on an agent CLI, which runs tools the host "
                f"never sees, so a webhook's turn on it cannot be held to the {profile.name} "
                "profile's tool grants. Point the webhook at an agent on the native runtime."
            )
        full_message = message
        if is_new and state.context_builder:
            from personalclaw.context_headroom import resolve_window

            # On a worker thread: building the message embeds it with the embedding model.
            full_message, _ = await asyncio.to_thread(
                state.context_builder.build_message,
                message,
                is_new,
                session_key,
                agent=agent,
                resumed=resumed,
                window=await resolve_window(serving=client),
            )
        from personalclaw.usage_ledger import Attribution, recorder

        # Unattended: an outside system asked, and nobody watches the turn.
        record = recorder(
            client, Attribution(source="background", session_key=session_key, agent=agent or "")
        )
        result_text = ""
        async with closing_stream(client.stream(full_message)) as events:
            async for event in events:
                if event.kind == EVENT_TEXT_CHUNK:
                    result_text += event.text
                elif event.kind == EVENT_SPENT:
                    record(event)
                elif event.kind == EVENT_COMPLETE:
                    record(event)
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
    restored: str = "",
) -> None:
    """Execute a webhook-triggered agent turn in an ephemeral session.

    Sessions are always destroyed after the turn completes (like subagents). Context continuity
    across webhook calls is the callback's (`webhook_callbacks`): the agent saves it with
    ``hook_register``, the owner allows it, and *restored* is that context as the door read it
    back (`webhook_callbacks.restored_context`: masked, screened and fenced as data), put in
    front of the next fresh session's message.
    """
    from personalclaw.security import (  # noqa: F811
        redact_credentials,
        redact_exfiltration_urls,
    )

    if restored:
        # Read back from a prior session and put in front of the webhook's own message, which is
        # sent as it came.
        message = (
            f"=== Restored Context (from prior session) ===\n"
            f"{restored}\n"
            f"=== End Restored Context ===\n\n"
            f"{message}"
        )

    result_text = ""
    outcome = "completed"
    try:
        # The callback's own work: one registered for a turn someone other than you asked for is
        # held to them in everything it does, as its registration says (`lasting_work`).
        with memory_writes.as_work_of(session_key):
            result_text = await asyncio.wait_for(
                _run_hook_inner(state, session_key, message, agent), timeout=timeout_secs
            )
    except asyncio.TimeoutError:
        outcome = "timeout"
        result_text = f"Hook agent timed out after {timeout_secs}s"
        logger.warning("Hook agent timeout: %s", session_key)
        await state.sessions.record_failure(session_key)
    except BudgetExceededError as exc:
        # The spend guard refused a call (`_run_hook_inner` rides a metered axis): a ceiling the
        # owner set, not a failure of the session, so it is said as the refusal it is.
        outcome = "refused_budget_exceeded"
        result_text = f"Hook agent stopped: {exc.sentence()}"
        logger.info("Hook agent refused by the spend budget: %s — %s", session_key, exc)
    except HookTurnRefused as exc:
        # Not run: the headless profile could not be held (`_run_hook_inner`). A setting to change,
        # so it is said, and it is no failure of the session.
        outcome = "refused_not_headless"
        result_text = f"Hook agent not run: {exc}"
        logger.info("Hook agent refused: %s — %s", session_key, exc)
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
        title = f"Hook: {name_safe}"
        state.notify(
            notification_kinds.HOOK, title, result_text[:2000], meta={"session_key": session_key}
        )
        from personalclaw.channel_delivery import deliver_to_owner

        body = result_text[:3000]
        try:
            await deliver_to_owner(
                lambda delivery, dm: delivery.deliver_text(dm, f"*{title}*\n{body}"),
                title=title,
                text=body,
                state=state,
            )
        except Exception:
            logger.exception("Hook agent: channel delivery failed")
