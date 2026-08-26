"""Dialect 4 — the A2A gateway (EXTERNAL-ACCESS §5).

Three literal routes, all outside the dashboard's cookie-auth world, all behind the
shared inbound seam (`gate.admission_problem` → `auth.peer_allowed` → bearer → caps):

* ``GET  /a2a/agent-card``      — the agent card. Its *skills* are the workflow
  templates the user PUBLISHED (``metadata.a2a_published``).
* ``POST /a2a/tasks``           — start one task. Maps onto a ``WorkflowRun`` through
  ``workflows.service.start_run`` — the v2 run-start seam, not a parallel launcher.
* ``GET  /a2a/tasks/{task_id}`` — poll one task.

Four decisions here are load-bearing and each is placed where it is on purpose.

**Publication is OPT-IN, per template, and defaults to FALSE.** ``a2a_published`` is a
typed ``DefMetadata`` field defaulting to ``False``, so a template that has never heard
of A2A is not on the card. The inverse — publish-by-default with an opt-out — would mean
enabling this surface silently exposes every template the user ever authored, including
ones whose *inputs* are the interesting part. The default is asserted by a test with a
vacuity floor precisely because a default is the easiest thing in this file to flip by
accident.

**An EMPTY card and a BROKEN card must not look alike.** ``workflows.service.list_defs``
swallows a provider failure by design ("one broken provider must not hide every other
pack's templates"), so a total catalog failure returns ``{"defs": []}`` with ``ok:
True``. Served straight through, that renders as a well-formed card advertising no
skills — indistinguishable from "the user published nothing", which is a *correct*
answer this surface must be able to give. :func:`published_skills` therefore returns a
``problem`` string alongside the skills, and a problem answers **503**. A 200 card with
``skills: []`` is then a positive claim: the catalog was read and nobody opted in.

**A task id IS the run id.** No side table maps one to the other. The alternative —
minting an A2A id and remembering the pairing — puts the mapping in a process-local
cache that a restart drops, so ``GET /a2a/tasks/<id>`` would 404 on a run that plainly
exists. The A2A spec has the *server* assign ``Task.id``, so nothing is owed to the
client here. A client's ``messageId`` (or an explicit ``idempotencyKey``) becomes
``start_run``'s caller key instead, which is what makes a retried send return the same
task rather than starting a second run.

**Headless is inherited, not configured.** The run's session key is
``inbound:a2a:<client>``, and ``guardrails.policy.INBOUND_PREFIX`` already classifies
that family as unattended → ``HEADLESS``. There is deliberately no profile argument on
this path: a surface that *chooses* its own safety profile is a surface that can choose
wrong, and an A2A caller must inherit exactly the ceiling an inbound OpenAI client gets.
The request-side ceiling comes from ``caps.caps_for(client)`` — the same three-layer
resolution every other dialect uses.

Every returned artifact goes through ``framing.fence_payload``, which is the one wrapper
over ``security.fence_untrusted``. This module contains no second fencing helper.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.inbound import auth
from personalclaw.inbound import caps as caps_mod
from personalclaw.inbound import framing
from personalclaw.inbound.audit import audit
from personalclaw.inbound.gate import admission_problem

logger = logging.getLogger(__name__)

#: The surface string, already a member of ``config.loader.EXTERNAL_ACCESS_SURFACES``.
#: Named rather than spelled inline: every kill switch this module obeys is keyed on it.
SURFACE = "a2a"

ROUTE_CARD = "/a2a/agent-card"
ROUTE_TASKS = "/a2a/tasks"
ROUTE_TASK = "/a2a/tasks/{task_id}"

#: The A2A protocol revision this card claims. A version string rather than a range: a
#: card that declines to name its protocol is a card a conforming client cannot use.
PROTOCOL_VERSION = "0.2.5"

#: The keys an agent card MUST carry to be usable. Exported because it is the whole
#: mechanism that separates "the card is empty" from "the card is broken": a test can
#: assert every one of these is present on a zero-skill card, which a broken card
#: (or a 503) cannot satisfy.
CARD_REQUIRED_KEYS: tuple[str, ...] = (
    "protocolVersion",
    "name",
    "description",
    "version",
    "url",
    "capabilities",
    "defaultInputModes",
    "defaultOutputModes",
    "skills",
)

#: A2A task states. Only the ones a workflow run can actually reach are listed — a
#: declared state nothing writes is the "enum member nobody writes" defect.
STATE_SUBMITTED = "submitted"
STATE_WORKING = "working"
STATE_INPUT_REQUIRED = "input-required"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"

#: ``RunStatus`` value → A2A task state. EXHAUSTIVE over ``models.RunStatus`` — every
#: member has a row, and ``test_inbound_a2a`` asserts that, so adding a run status without
#: deciding what an external caller should see reds a test instead of silently landing on
#: the fallback. The keys are the literal enum VALUES rather than the members so this
#: module does not import the workflow models at import time.
#:
#: ``escalated`` maps to ``input-required``, not ``failed``: the run did not break, it
#: handed off to a human. Note that ``escalated`` is nonetheless TERMINAL on the run side
#: while ``input-required`` is non-final on the A2A side — which is exactly why finality is
#: decided by :func:`run_is_final` against ``TERMINAL_RUN_STATUSES`` and never by reading
#: the A2A state back. Two answers to "is this over?" is how a stream hangs on a run that
#: already stopped.
_RUN_STATE_MAP: dict[str, str] = {
    "draft": STATE_SUBMITTED,
    "running": STATE_WORKING,
    "paused": STATE_INPUT_REQUIRED,
    "needs_input": STATE_INPUT_REQUIRED,
    "complete": STATE_COMPLETED,
    "failed": STATE_FAILED,
    "cancelled": STATE_CANCELED,
    "escalated": STATE_INPUT_REQUIRED,
}

#: How long a streamed task is followed before the stream closes with whatever state the
#: run has reached. Bounded because an unbounded SSE hold is a socket a client cannot
#: reason about; the client re-polls ``GET /a2a/tasks/{id}`` to continue.
STREAM_WINDOW_S = 25.0
_STREAM_POLL_S = 0.25

_NO_STORE = {"Cache-Control": "no-store"}


def task_state_for(run_status: str) -> str:
    """Map a ``RunStatus`` value onto an A2A task state.

    An UNKNOWN status resolves to ``working``, never to a terminal state. A run status
    nobody remembered to map must not make a client believe the task finished — "still
    going" is wrong-but-recoverable, "completed" is wrong-and-final.
    """
    return _RUN_STATE_MAP.get(str(run_status or "").strip().lower(), STATE_WORKING)


def run_is_final(run_status: str) -> bool:
    """Whether the RUN has stopped — the single source of finality for this surface.

    Reads ``models.TERMINAL_RUN_STATUSES`` rather than checking the mapped A2A state,
    because the two disagree for ``escalated`` (terminal run, non-final A2A state) and the
    run is the one that knows. An unreadable enum reads as NOT final: a stream that closes
    early strands the client, but one that stays open a few more seconds costs a poll.
    """
    try:
        from personalclaw.workflows.models import TERMINAL_RUN_STATUSES

        return str(run_status or "").strip().lower() in {s.value for s in TERMINAL_RUN_STATUSES}
    except Exception:  # noqa: BLE001
        logger.debug("a2a: terminal-status set unreadable", exc_info=True)
        return False


# ── Admission ─────────────────────────────────────────────────────────────────


def _refuse(
    response: web.Response, *, route: str, refused: str, client_id: str = ""
) -> web.Response:
    audit(SURFACE, route=route, status=response.status, refused=refused, client_id=client_id)
    return response


def _admit(request: web.Request, route: str) -> tuple[web.Response | None, str, caps_mod.Caps]:
    """Every gate, in the order that leaks least. ``(refusal, client_id, caps)``.

    1. **Surface admission → 404/503.** Re-checked per request, never frozen at mount
       time, so flipping ``external_access.a2a.enabled`` in Settings takes effect on the
       next call instead of needing a restart.
    2. **Peer → 403.** Through ``auth.peer_allowed``, so A2A honours ``allow_remote`` +
       ``public_url`` like the OpenAI and MCP dialects. It is deliberately NOT
       loopback-forever: unlike the capture proxy this surface neither proxies the
       operator's paid credential nor records prompts, and exposing published workflows
       to a declared public URL is the point of the dialect.
    3. **Bearer → 401.** Client token first, then the surface token, so a registered
       client is never mistaken for the un-pinned surface principal.
    4. **Rate → 429**, per client.
    """
    problem, status = admission_problem(SURFACE)
    if problem:
        # Two literal calls rather than one with a computed code. A code in a variable is
        # invisible to the registry rail in `test_http_error_codes_append_only`, which is
        # why that rail ceilings dynamic sites — a surface that mints its code at runtime
        # can ship an unregistered one. The status still comes from `admission_problem`,
        # which is the layer that decides 404-vs-503.
        refusal = (
            json_error("service_unavailable", status=status, headers=_NO_STORE)
            if status == 503
            else json_error("not_found", status=status, headers=_NO_STORE)
        )
        return (
            _refuse(refusal, route=route, refused=problem),
            "",
            caps_mod.DEFAULT_CAPS,
        )

    peer_ok, peer_reason = auth.peer_allowed(request, SURFACE)
    if not peer_ok:
        return (
            _refuse(
                json_error("forbidden", status=403, headers=_NO_STORE),
                route=route,
                refused=peer_reason,
            ),
            "",
            caps_mod.DEFAULT_CAPS,
        )

    presented = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    client, client_reason = _lookup_client(presented)
    client_id = getattr(client, "client_id", "") or ""
    if client is None and not auth.verify_bearer(SURFACE, presented):
        return (
            _refuse(
                json_error("unauthorized", status=401, headers=_NO_STORE),
                route=route,
                refused=client_reason or "bad or missing bearer token",
            ),
            "",
            caps_mod.DEFAULT_CAPS,
        )

    caps = caps_mod.caps_for(client)
    peer_fallback = (request.headers.get("Host", "") or "") + "|" + (request.remote or "")
    if not caps_mod.check_rate_for_client(SURFACE, client_id, peer_fallback, caps):
        retry = caps_mod.retry_after_for_client(SURFACE, client_id, peer_fallback, caps)
        return (
            _refuse(
                json_error(
                    "rate_limited",
                    status=429,
                    headers={**_NO_STORE, "Retry-After": str(retry)},
                ),
                route=route,
                refused="rate limit",
                client_id=client_id,
            ),
            client_id,
            caps,
        )
    return None, client_id, caps


def _lookup_client(presented: str) -> tuple[Any | None, str]:
    """The registered client behind ``presented``, or ``(None, reason)``.

    Wrapped because the registry is OPTIONAL on this surface: the documented setup is
    the surface token alone and a client record is an upgrade (attribution + its own
    caps), so a registry fault must not deny a request the surface token would admit.
    """
    if not presented:
        return None, "no bearer token presented"
    try:
        from personalclaw.inbound.clients import lookup_by_token

        return lookup_by_token(presented, SURFACE)
    except Exception:  # noqa: BLE001 — an unreadable registry means "no client record"
        logger.debug("a2a: client lookup failed", exc_info=True)
        return None, "client registry unreadable"


def outbound_policy() -> Any:
    """The egress posture for an OUTBOUND A2A call (EXTERNAL-ACCESS §5, outbound half).

    Lives here rather than in the ``a2a-action`` app so the *policy* decision is in core,
    reviewable, and testable, while the app supplies only the URL. An app that composed its
    own policy could compose a permissive one.

    🔴 **Not** ``CONNECTOR`` layered by ``egress_policy_for``, which is what §5's prose
    says. Measured: ``CONNECTOR`` has ``allow_only=False``, and ``egress_policy_for``
    UNIONS the operator's ``allow_hosts`` onto the profile's — an additive waiver, not a
    restriction. So that composition reaches EVERY public host and the allow-list is
    decorative, which is the exact defect ``capture_proxy.capture_policy`` records for a
    STRICT-based build. The clause's own words are "deny-by-default host allowlist", and
    only ``allow_only=True`` delivers that: an empty list must mean "nowhere to go", not
    "anywhere". ``CONNECTOR``'s size and timeout ceilings are kept by copying them onto the
    LISTED base, so nothing is lost by not using it as the base.
    """
    from personalclaw.net.policy import CONNECTOR, LISTED, egress_policy_for

    return egress_policy_for(
        LISTED.with_overrides(
            name="a2a-outbound",
            max_bytes=CONNECTOR.max_bytes,
            timeout_s=CONNECTOR.timeout_s,
        )
    )


def session_key_for(client_id: str) -> str:
    """The guardrail identity for an A2A-started run.

    ``inbound:`` is what ``guardrails.policy`` already classifies as unattended, so this
    one string is the whole of "runs execute under the headless profile". The prefix is
    IMPORTED from the classifier rather than spelled here: a private copy of the literal
    is free to drift from the module that decides what it means, and the drift would
    present as an A2A run silently resolving INTERACTIVE.
    """
    from personalclaw.guardrails.policy import INBOUND_PREFIX

    return f"{INBOUND_PREFIX}{SURFACE}:{client_id or SURFACE}"


# ── The card ──────────────────────────────────────────────────────────────────


async def published_skills(*, limit: int = 0) -> tuple[list[dict[str, Any]], str]:
    """``(skills, problem)`` — the published templates as A2A skills.

    ``problem`` is the empty string when the catalog was READ successfully, whatever it
    contained. A non-empty ``problem`` must become a 503, never a 200 with no skills:
    see the module docstring. The distinction is why this returns a pair instead of a
    list.

    A template is a skill only when ``metadata.a2a_published`` is exactly ``True``.

    Providers are enumerated DIRECTLY here rather than through
    ``workflows.service.list_defs``, and both halves of that choice are the point:

    * ``list_defs`` strips ``metadata`` off its rows, so the publish flag is not visible
      through it at all.
    * ``list_defs`` also swallows a per-provider exception ("one broken provider must not
      hide every other pack's templates" — right for a UI listing). But when the ONLY
      provider raises, the swallow turns a broken catalog into ``{"defs": []}`` with
      ``ok: True``, which is the fake-clean an empty card cannot be distinguished from.
      Counting the failures here is what makes the 503 reachable.

    Each published name is then re-read through ``service.get_def`` — the STRIPPED read —
    for rendering, because a card goes to an external caller and must never carry a
    credential binding. The publish flag is checked again on the stripped copy so the
    document that is actually served is the one that authorised serving it.
    """
    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows import service as wf

    providers = defs_mod.list_providers()
    if not providers:
        # Zero registered providers is not "no templates" — it is a catalog that cannot
        # be consulted. The engine registers its provider at import; none means the
        # engine is not there.
        return [], "no workflow definition provider is registered"

    published: list[str] = []
    list_failures = 0
    for provider_name in providers:
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            list_failures += 1
            continue
        try:
            found, _total = await provider.list_defs(limit=500)
        except Exception:  # noqa: BLE001 — counted, not swallowed; see the docstring
            logger.debug("a2a: provider %s failed to list", provider_name, exc_info=True)
            list_failures += 1
            continue
        for item in found:
            row = item if isinstance(item, dict) else getattr(item, "to_dict", lambda: {})()
            if not isinstance(row, dict) or not row.get("name"):
                continue
            if (row.get("metadata") or {}).get("a2a_published") is not True:
                continue
            published.append(str(row["name"]))
    if list_failures == len(providers):
        return [], f"all {len(providers)} template provider(s) failed to list"

    skills: list[dict[str, Any]] = []
    read_failures = 0
    names = list(dict.fromkeys(published))
    for name in names:
        try:
            got = await wf.get_def(name)
        except Exception:  # noqa: BLE001 — one unreadable def must not hide the rest
            logger.debug("a2a: def read failed for %s", name, exc_info=True)
            read_failures += 1
            continue
        if not got.get("ok"):
            read_failures += 1
            continue
        definition = got.get("definition") or {}
        metadata = definition.get("metadata") or {}
        if metadata.get("a2a_published") is not True:
            continue
        skills.append(_skill_of(name, definition, metadata))
        if limit and len(skills) >= limit:
            break
    if names and read_failures == len(names):
        # Every published template failed to read. Returning an empty card here would
        # claim "nothing is published" on the strength of no successful read at all.
        return [], f"none of the {len(names)} published template(s) could be read"
    skills.sort(key=lambda s: str(s.get("id")))
    return skills, ""


def _skill_of(name: str, definition: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """One template rendered as an A2A skill.

    Inputs are advertised by NAME and type only. A default value can be a hostname, a
    project path or an account handle, so echoing declared defaults onto a public card
    would make the card an inventory of the user's environment.
    """
    inputs = definition.get("inputs") or {}
    return {
        "id": name,
        "name": str(metadata.get("summary") or "").strip() or name,
        "description": str(
            metadata.get("when_to_use") or definition.get("description") or ""
        ).strip(),
        "tags": [str(t) for t in (definition.get("tags") or [])],
        "inputModes": ["text/plain", "application/json"],
        "outputModes": ["text/plain"],
        "examples": [str(e) for e in (metadata.get("example_outputs") or [])][:3],
        "inputs": [
            {
                "name": str(key),
                "type": str((spec or {}).get("type") or "string"),
                "required": bool((spec or {}).get("required")),
                "help": str((spec or {}).get("help") or ""),
            }
            for key, spec in sorted(inputs.items())
            if isinstance(spec, dict)
        ],
    }


def _card_base_url() -> str:
    """The URL this card advertises. The operator's declared ``public_url`` or loopback.

    Never derived from the request's ``Host`` header: a card is a document other agents
    persist, and letting a caller's own header decide what URL it records is how a
    forwarded request mints a card pointing somewhere the operator never declared.
    """
    try:
        from personalclaw.config.loader import AppConfig

        declared = str(AppConfig.load().external_access.public_url or "").strip()
    except Exception:  # noqa: BLE001
        declared = ""
    return f"{declared.rstrip('/')}/a2a" if declared else "http://127.0.0.1:10000/a2a"


def build_card(skills: list[dict[str, Any]]) -> dict[str, Any]:
    """The agent card around ``skills``. Every :data:`CARD_REQUIRED_KEYS` entry present.

    Kept separate from the handler so the shape is testable without a request, and so an
    empty card is provably the SAME document with a shorter ``skills`` list rather than a
    different, degraded one.
    """
    from personalclaw import __version__

    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": "PersonalClaw",
        "description": (
            "A personal AI assistant. Each skill is a workflow template its owner "
            "explicitly published; nothing else on this instance is reachable here."
        ),
        "version": str(__version__),
        "url": _card_base_url(),
        "preferredTransport": "HTTP+JSON",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain"],
        "skills": skills,
    }


async def handle_agent_card(request: web.Request) -> web.Response:
    """GET /a2a/agent-card — the published-workflow card."""
    started = time.monotonic()
    refusal, client_id, caps = _admit(request, ROUTE_CARD)
    if refusal is not None:
        return refusal
    try:
        skills, problem = await published_skills(limit=caps.max_items)
    except Exception:  # noqa: BLE001 — a catalog fault is a 503, never an empty card
        logger.warning("a2a: skill catalog read failed", exc_info=True)
        skills, problem = [], "the template catalog could not be read"
    if problem:
        return _refuse(
            json_error("a2a_catalog_unavailable", message=problem, status=503, headers=_NO_STORE),
            route=ROUTE_CARD,
            refused=problem,
            client_id=client_id,
        )
    card = build_card(skills)
    body = json.dumps(card)
    audit(
        SURFACE,
        route=ROUTE_CARD,
        status=200,
        bytes_out=len(body.encode("utf-8")),
        duration_ms=int((time.monotonic() - started) * 1000),
        client_id=client_id,
    )
    return web.json_response(card, headers=_NO_STORE)


# ── Tasks ─────────────────────────────────────────────────────────────────────


def _skill_id_of(body: dict[str, Any]) -> str:
    """The requested skill, across the spellings a client may send.

    A2A carries the target in ``metadata`` on ``message/send``; a plain REST client will
    send ``skillId`` or ``skill``. All three read the same field rather than one being
    the "real" one, because a caller that named the skill and got "no skill named ''"
    has no way to discover which spelling this server wanted.
    """
    for key in ("skillId", "skill", "skill_id"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        for key in ("skillId", "skill", "skill_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _inputs_of(body: dict[str, Any]) -> dict[str, Any]:
    for key in ("inputs", "input", "params", "parameters"):
        value = body.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {}


def _caller_key_of(body: dict[str, Any]) -> str:
    """The client's own retry key. ``messageId`` is A2A's; ``idempotencyKey`` is explicit."""
    for key in ("idempotencyKey", "idempotency_key", "messageId", "message_id"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return f"a2a:{value.strip()}"
    message = body.get("message")
    if isinstance(message, dict):
        value = message.get("messageId") or message.get("message_id")
        if isinstance(value, str) and value.strip():
            return f"a2a:{value.strip()}"
    return ""


def _task_envelope(
    task_id: str,
    *,
    state: str,
    context_id: str = "",
    skill_id: str = "",
    message: str = "",
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One A2A Task object. The only shape this surface emits."""
    from datetime import datetime, timezone

    envelope: dict[str, Any] = {
        "id": task_id,
        "contextId": context_id or task_id,
        "kind": "task",
        "status": {
            "state": state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "artifacts": artifacts or [],
        "metadata": {"skillId": skill_id, "runId": task_id},
    }
    if message:
        envelope["status"]["message"] = {
            "role": "agent",
            "parts": [{"kind": "text", "text": message}],
        }
    return envelope


def task_artifacts(
    run_id: str, *, client_id: str, caps: caps_mod.Caps | None = None
) -> list[dict[str, Any]]:
    """The run's node outputs as FENCED A2A artifacts.

    Fencing goes through ``framing.fence_payload`` — the single inbound wrapper over
    ``security.fence_untrusted``. There is no second fencing helper in this module and
    there must not be one: two spellings of "make this data-not-instructions" is how one
    of them ends up missing the preamble.
    """
    from personalclaw.workflows import service as wf

    effective = caps or caps_mod.DEFAULT_CAPS
    status = wf.status(run_id)
    if not status.get("ok"):
        return []
    seen: list[str] = []
    for row in status.get("nodes") or []:
        node_id = str((row or {}).get("node_id") or "")
        if not node_id or str((row or {}).get("state")) != "succeeded" or node_id in seen:
            continue
        seen.append(node_id)
    artifacts: list[dict[str, Any]] = []
    for node_id in seen[: effective.max_items]:
        try:
            got = wf.output(run_id, node_id)
        except Exception:  # noqa: BLE001 — one unreadable output must not void the rest
            logger.debug("a2a: output read failed for %s/%s", run_id, node_id, exc_info=True)
            continue
        if not got.get("ok"):
            continue
        text = got.get("output")
        if not isinstance(text, str) or not text.strip():
            continue
        artifacts.append(
            {
                "artifactId": f"{run_id}:{node_id}",
                "name": node_id,
                "parts": [
                    {
                        "kind": "text",
                        "text": framing.fence_payload(
                            text,
                            surface=SURFACE,
                            client_id=client_id,
                            detail=node_id,
                            caps=effective,
                        ),
                    }
                ],
            }
        )
    return artifacts


def task_snapshot(
    run_id: str, *, client_id: str, skill_id: str = "", caps: caps_mod.Caps | None = None
) -> tuple[dict[str, Any], bool] | None:
    """``(task, final)`` for ``run_id``, or None when there is no such run.

    Artifacts are attached only once the run is FINAL. A mid-run node output is not the
    task's answer, and handing one over as an artifact would let a client act on a partial
    result a later node still intends to replace.
    """
    from personalclaw.workflows import service as wf

    status = wf.status(run_id)
    if not status.get("ok"):
        return None
    run_status = str(status.get("status") or "")
    final = run_is_final(run_status)
    task = _task_envelope(
        run_id,
        state=task_state_for(run_status),
        skill_id=skill_id or str(status.get("workflow") or ""),
        message=str(status.get("error") or ""),
        artifacts=task_artifacts(run_id, client_id=client_id, caps=caps) if final else [],
    )
    return task, final


async def _start_task(
    body: dict[str, Any], *, client_id: str
) -> tuple[dict[str, Any] | None, web.Response | None]:
    """Start one run for an A2A task. ``(started_status, refusal)``."""
    from personalclaw.workflows.models import OriginKind
    from personalclaw.workflows.service import start_run

    skill_id = _skill_id_of(body)
    if not skill_id:
        return None, json_error(
            "invalid_request",
            message="the task must name a skill (skillId)",
            status=400,
            headers=_NO_STORE,
        )
    skills, problem = await published_skills()
    if problem:
        return None, json_error(
            "a2a_catalog_unavailable", message=problem, status=503, headers=_NO_STORE
        )
    if skill_id not in {str(s.get("id")) for s in skills}:
        # 404 rather than 403, and the SAME 404 an unknown name gets: an unpublished
        # template must not be distinguishable from one that does not exist, or the card
        # becomes optional and this route becomes the real (enumerable) catalog.
        return None, json_error(
            "not_found",
            message=f"no published skill named {skill_id!r}",
            status=404,
            headers=_NO_STORE,
        )
    started = await start_run(
        name=skill_id,
        inputs=_inputs_of(body),
        mode="background",
        origin_kind=OriginKind.API,
        session_key=session_key_for(client_id),
        idempotency_key=_caller_key_of(body),
    )
    if not started.get("ok"):
        # `workflows.service._service_failure` returns a FLAT envelope — `{"ok": False,
        # "code": ..., "message": ...}` — not a nested `{"error": {...}}`. Measured: reading
        # `started["error"]` produced "the run could not be started" for every refusal,
        # discarding the one sentence (a missing required input, a preflight finding) the
        # caller could act on. The service code rides in `error_extra` so a client can branch
        # on it without parsing prose.
        return None, json_error(
            "invalid_request",
            message=str(started.get("message") or "the run could not be started"),
            status=400,
            headers=_NO_STORE,
            error_extra={"service_code": str(started.get("code") or "")},
        )
    return started, None


async def handle_tasks(request: web.Request) -> web.StreamResponse:
    """POST /a2a/tasks — map an A2A task onto a WorkflowRun.

    Streams the task lifecycle when the client asks for ``text/event-stream``; otherwise
    answers once with the submitted task. Both paths take the SAME start seam, so a
    streaming client and a polling client cannot get different runs for one request.
    """
    started_at = time.monotonic()
    refusal, client_id, caps = _admit(request, ROUTE_TASKS)
    if refusal is not None:
        return refusal

    raw = await request.content.read(caps.body_bytes + 1)
    if len(raw) > caps.body_bytes:
        return _refuse(
            json_error("request_too_large", status=413, headers=_NO_STORE),
            route=ROUTE_TASKS,
            refused="body over cap",
            client_id=client_id,
        )
    try:
        body = json.loads(raw or b"{}")
    except (ValueError, UnicodeDecodeError):
        return _refuse(
            json_error("invalid_json", status=400, headers=_NO_STORE),
            route=ROUTE_TASKS,
            refused="unparseable body",
            client_id=client_id,
        )
    if not isinstance(body, dict):
        return _refuse(
            json_error("invalid_body", status=400, headers=_NO_STORE),
            route=ROUTE_TASKS,
            refused="body is not an object",
            client_id=client_id,
        )

    started, start_refusal = await _start_task(body, client_id=client_id)
    if start_refusal is not None:
        return _refuse(
            start_refusal,
            route=ROUTE_TASKS,
            refused="task start refused",
            client_id=client_id,
        )
    assert started is not None  # noqa: S101 — narrowed by the branch above
    run_id = str(started.get("run_id") or "")
    skill_id = _skill_id_of(body)

    if "text/event-stream" in (request.headers.get("Accept") or ""):
        return await _stream_task(
            request, run_id, client_id=client_id, skill_id=skill_id, caps=caps
        )

    snapshot = task_snapshot(run_id, client_id=client_id, skill_id=skill_id, caps=caps)
    task = (
        snapshot[0]
        if snapshot
        else _task_envelope(run_id, state=STATE_SUBMITTED, skill_id=skill_id)
    )
    payload = json.dumps(task)
    audit(
        SURFACE,
        route=ROUTE_TASKS,
        status=200,
        bytes_in=len(raw),
        bytes_out=len(payload.encode("utf-8")),
        duration_ms=int((time.monotonic() - started_at) * 1000),
        client_id=client_id,
        tool=skill_id,
    )
    return web.json_response(task, headers=_NO_STORE)


async def _sse(response: web.StreamResponse, event: dict[str, Any]) -> None:
    """One SSE frame. The only place this module writes to a stream."""
    await response.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))


async def _stream_task(
    request: web.Request,
    run_id: str,
    *,
    client_id: str,
    skill_id: str,
    caps: caps_mod.Caps,
) -> web.StreamResponse:
    """SSE the task's lifecycle as A2A ``status-update`` / ``artifact-update`` events.

    Only STATE TRANSITIONS are emitted, not a heartbeat per poll: a client that receives
    the same ``working`` frame a hundred times cannot tell progress from a stuck run. The
    ``final`` flag comes from :func:`run_is_final`, so a stream closing and the task being
    over are the same fact rather than two. If the bounded window expires first the last
    frame carries ``final: false`` — honest, and the client resumes on the poll route.
    """
    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
        }
    )
    await response.prepare(request)
    deadline = time.monotonic() + STREAM_WINDOW_S
    last = ""
    try:
        while True:
            snapshot = task_snapshot(run_id, client_id=client_id, skill_id=skill_id, caps=caps)
            task = (
                snapshot[0]
                if snapshot
                else _task_envelope(run_id, state=STATE_SUBMITTED, skill_id=skill_id)
            )
            final = bool(snapshot[1]) if snapshot else False
            timed_out = time.monotonic() >= deadline
            state = str(task["status"]["state"])
            if state != last or final:
                await _sse(
                    response,
                    {
                        "taskId": run_id,
                        "contextId": task["contextId"],
                        "kind": "status-update",
                        "status": task["status"],
                        "final": final,
                    },
                )
                last = state
            if final:
                for artifact in task.get("artifacts") or []:
                    await _sse(
                        response,
                        {
                            "taskId": run_id,
                            "contextId": task["contextId"],
                            "kind": "artifact-update",
                            "artifact": artifact,
                            "lastChunk": True,
                        },
                    )
                break
            if timed_out:
                break
            await asyncio.sleep(_STREAM_POLL_S)
    except (ConnectionResetError, asyncio.CancelledError):
        # A client that hung up mid-run is not an error to report; the run keeps going
        # and is still readable through the poll route.
        logger.debug("a2a: task stream for %s disconnected", run_id)
    audit(
        SURFACE,
        route=ROUTE_TASKS,
        status=200,
        client_id=client_id,
        tool=skill_id,
    )
    return response


async def handle_task_get(request: web.Request) -> web.Response:
    """GET /a2a/tasks/{task_id} — poll one task. The id is the run id."""
    refusal, client_id, caps = _admit(request, ROUTE_TASK)
    if refusal is not None:
        return refusal
    task_id = str(request.match_info.get("task_id") or "")
    snapshot = task_snapshot(task_id, client_id=client_id, caps=caps)
    if snapshot is None:
        return _refuse(
            json_error("not_found", status=404, headers=_NO_STORE),
            route=ROUTE_TASK,
            refused="no such task",
            client_id=client_id,
        )
    task, _final = snapshot
    audit(SURFACE, route=ROUTE_TASK, status=200, client_id=client_id)
    return web.json_response(task, headers=_NO_STORE)


def register_routes(app: web.Application) -> None:
    """Mount the three A2A routes.

    Registered UNCONDITIONALLY and refused per request, matching the capture proxy and
    NOT ``mcp_http.mount``'s enablement-gated mount. A mount-time gate freezes the
    decision at startup: enabling the surface in Settings would then need a gateway
    restart, and disabling it would leave the route live. ``_admit`` re-reads the config
    on every call, and a disabled surface answers 404 — the same answer an unmounted
    path gives — so nothing is disclosed by the routes existing.

    All three paths are literal apart from the trailing ``{task_id}``, which sits under
    the literal ``/a2a/tasks/`` prefix, so no ``{...}`` pattern in ``dashboard/server.py``
    can shadow them.
    """
    app.router.add_get(ROUTE_CARD, handle_agent_card)
    app.router.add_post(ROUTE_TASKS, handle_tasks)
    app.router.add_get(ROUTE_TASK, handle_task_get)
