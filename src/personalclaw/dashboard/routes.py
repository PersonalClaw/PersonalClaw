"""The dashboard's route table: the page and ``/api`` routes ``start_dashboard`` registers, in the
order the router matches them.

``start_dashboard`` calls :func:`register_dashboard_routes` once, after the routes the MCP tools
call (``server._register_mcp_routes``) and before the provider extensions load: the extension,
instance and entity routes, the knowledge routes, the static files and the SPA fallback are
registered after it. Order is part of the contract, as the comments below
say where it matters: a literal path segment is registered before a dynamic one that would capture
it, and the inbound surfaces that carry their own credential come early.
"""

import logging
import os

from aiohttp import web

from personalclaw.config import config_dir
from personalclaw.dashboard import chat, chat_questions, handlers, handlers_inbox, ws
from personalclaw.suggestions import api_suggestions


def _register_upload_routes(app: web.Application) -> None:
    """Register the resumable large-file upload protocol routes."""
    from personalclaw.dashboard.handlers import uploads as _up

    app.router.add_get("/api/uploads/limits", _up.api_uploads_limits)
    app.router.add_post("/api/uploads/init", _up.api_uploads_init)
    app.router.add_put("/api/uploads/{id}/part", _up.api_uploads_part)
    app.router.add_get("/api/uploads/{id}", _up.api_uploads_status)
    app.router.add_delete("/api/uploads/{id}", _up.api_uploads_drop)
    app.router.add_post("/api/uploads/{id}/complete", _up.api_uploads_complete)


def _register_pages(app: web.Application) -> None:
    """The pages a browser opens: the dashboard, its PWA files, the licence notices and the login
    page. They match before every API route, as they always have."""
    app.router.add_get("/", handlers.index)
    app.router.add_get("/claw.svg", handlers.favicon)
    # PWA. Both live at the origin ROOT by necessity, not
    # convention: `/sw.js` because a service worker's scope is its path (served from
    # `/assets/` it could only control `/assets/`), and the manifest because
    # `start_url`/`scope` are resolved relative to it. Both remain session-gated —
    # they are NOT in token_auth's bypass sets — so only the authenticated owner can
    # install the companion; index.html declares the manifest link with
    # `crossorigin="use-credentials"` so the browser sends the cookie.
    app.router.add_get("/manifest.webmanifest", handlers.manifest_webmanifest)
    app.router.add_get("/sw.js", handlers.service_worker)
    # The licence notices the dashboard ships, at stable root URLs that Settings → Updates links:
    # the fonts' and the bundled npm packages'. Session-gated like every page here.
    app.router.add_get("/THIRD_PARTY_NOTICES.txt", handlers.third_party_notices)
    app.router.add_get("/THIRD_PARTY_NOTICES_NPM.txt", handlers.third_party_notices_npm)
    # The owner's login page. What it posts to is the API's (`/api/auth/login`).
    from personalclaw.dashboard.handlers import auth as _auth_h

    app.router.add_get("/login", _auth_h.login_page)


def register_dashboard_routes(app: web.Application, *, pages: bool = True) -> None:
    """Register the dashboard's page and API routes on *app*, in the order they match.

    ``pages=False`` registers the API alone, for the headless gateway: none of the dashboard's
    pages is served, and nothing hands out a link to one, so the device pairing flow, whose link
    opens one, goes with them."""
    if pages:
        _register_pages(app)

    # Owner login. `/login`, `/api/auth/login` and
    # `/api/auth/status` are token-auth EXEMPT — they are how a remote browser obtains a
    # session in the first place, so requiring one would be circular. They carry their own
    # guards instead (origin check, per-IP lockout, fail-closed verify). Everything else here
    # sits behind the normal middleware: logout/session/password all require a live session.
    from personalclaw.dashboard.handlers import auth as _auth_h

    app.router.add_post("/api/auth/login", _auth_h.api_auth_login)
    app.router.add_get("/api/auth/status", _auth_h.api_login_status)
    app.router.add_post("/api/auth/logout", _auth_h.api_auth_logout)
    app.router.add_get("/api/auth/session", _auth_h.api_auth_session)
    app.router.add_post("/api/auth/password", _auth_h.api_auth_set_password)
    app.router.add_post("/api/auth/enroll/start", _auth_h.api_auth_enroll_start)
    app.router.add_post("/api/auth/enroll/complete", _auth_h.api_auth_enroll_complete)

    # Device pairing + the Devices registry. Registered next to the auth
    # routes because they mint the same credential: a paired device holds an ordinary session,
    # and `pair/complete` carries login's guards for the same reason it shares its exemption.
    from personalclaw.dashboard.handlers.devices import register_device_routes

    register_device_routes(app, pairing=pages)

    # The browse user-browser connector. Beside the device routes because the
    # connector IS a paired device — it announces its CDP page-target endpoint over loopback
    # on the SAME dashboard server (no new listener) and is listed by the same registry.
    from personalclaw.dashboard.handlers.browse_connector import register_browse_connector_routes

    register_browse_connector_routes(app)

    # Push subscriptions. Next to the device routes because a
    # subscription is per-DEVICE state keyed on the same device id pairing writes.
    from personalclaw.dashboard.handlers.push import register_push_routes

    register_push_routes(app)

    # Browse mirror + kill switch + auth_needed surfacing and the per-task
    # grant answer. The live `browse_step` relay and the `browse_grant` signal ride the
    # multiplexed WS registered just below; these are its read model (`/api/browse/status`), the
    # one-click kill controls, and the Allow/Deny that resolves a pending grant.
    from personalclaw.dashboard.handlers.browse_mirror import register_browse_mirror_routes

    register_browse_mirror_routes(app)

    # WebSocket (multiplexed real-time events)
    app.router.add_get("/api/ws", ws.api_ws)

    # Inbound read-only MCP surface. Mounts unconditionally and refuses per request, like
    # the three below, so a surface turned on in Settings serves its next request and one
    # turned off refuses it (404, the audit trail naming the switch). Registered here so it
    # sits outside the dashboard's cookie-auth world — it carries its own bearer
    # credential and its own loopback rail.
    try:
        from personalclaw.inbound.mcp_http import mount as _mount_inbound_mcp

        _mount_inbound_mcp(app)
    except Exception:  # noqa: BLE001 — an inbound fault must never block startup
        logging.getLogger(__name__).warning("inbound: /mcp mount failed", exc_info=True)

    # External-agent capture proxy. Two literal POST paths under
    # /capture/v1 that another agent on this machine points OPENAI_BASE_URL /
    # ANTHROPIC_BASE_URL at. Registered HERE for the same reasons as /mcp above — its own
    # bearer, its own loopback rail, outside the cookie-auth world — and this early so no
    # `{...}` pattern below can capture the literal `capture` segment (the hazard the
    # `bulk`/`templates` comments further down describe). Like /mcp it mounts
    # unconditionally and refuses per-request, so toggling the surface in Settings needs
    # no restart; a disabled surface answers 404 either way.
    try:
        from personalclaw.inbound.capture_proxy import register_routes as _register_capture

        _register_capture(app)
    except Exception:  # noqa: BLE001 — an inbound fault must never block startup
        logging.getLogger(__name__).warning("inbound: /capture mount failed", exc_info=True)

    # OpenAI-compatible inbound dialect — `/v1/*`, where `model`
    # names one of the user's AGENTS. Registered HERE for the same three reasons as the
    # two surfaces above: its own bearer, its own peer rail, and outside the dashboard's
    # cookie-auth world. Like /capture and /mcp it mounts unconditionally and
    # refuses per request, so the Settings toggle needs no restart; a disabled surface
    # answers 404 either way. Early, so no `{...}` pattern below can capture `v1`.
    # The turn runner and the change of a session's agent are handed IN rather than imported by
    # the dialect: `inbound/` is domain code, and an `inbound/` -> `dashboard/` import is the
    # `core-must-not-import-the-http-surface` inversion (see that dialect's
    # `register_routes`). The dashboard's route table is part of its composition root and
    # legitimately faces downward, so the dependency belongs here.
    try:
        from personalclaw.dashboard.chat_handlers import _run_chat_scoped
        from personalclaw.dashboard.running_turn import move_to_agent
        from personalclaw.inbound.openai_dialect import register_routes as _register_openai

        _register_openai(app, turn_runner=_run_chat_scoped, agent_mover=move_to_agent)
    except Exception:  # noqa: BLE001 — an inbound fault must never block startup
        logging.getLogger(__name__).warning("inbound: /v1 mount failed", exc_info=True)
    # A2A gateway. Three literal paths under /a2a — the agent card
    # plus task start/poll. Registered HERE for the same reasons as the two above: its own
    # bearer, outside the cookie-auth world, and early enough that no `{...}` pattern below
    # can capture the literal `a2a` segment. Mounts unconditionally and refuses per
    # request, like /capture, so toggling the surface in Settings needs no restart.
    try:
        from personalclaw.inbound.a2a import register_routes as _register_a2a

        _register_a2a(app)
    except Exception:  # noqa: BLE001 — an inbound fault must never block startup
        logging.getLogger(__name__).warning("inbound: /a2a mount failed", exc_info=True)

    # Status / system
    app.router.add_get("/api/healthz", handlers.api_healthz)
    app.router.add_get("/api/status", handlers.api_status)
    app.router.add_get("/api/system", handlers.api_system)
    app.router.add_get("/api/auth-status", handlers.api_auth_status)
    app.router.add_get("/api/onboarding", handlers.api_onboarding)
    # Onboarding progress is ENTITY state (entity_settings/onboarding.json), so it gets its
    # own write path rather than riding the config PATCH allowlist.
    app.router.add_post("/api/onboarding/state", handlers.api_onboarding_state)
    # The onboarding import step's GET (scan) + POST (import). Its own module
    # because the handler owns the client-supplied-items refusal and the report shape.
    from personalclaw.dashboard.handlers.onboarding_import import (
        register_onboarding_import_routes,
    )

    register_onboarding_import_routes(app)
    # The local + LAN Ollama zero-key on-ramp: detect a local Ollama, an
    # opt-in RFC-1918 LAN scan, and a credential-free one-click bind. Its own module
    # because the scan is a security-relevant network action gated behind an explicit
    # POST, and the bind re-validates the endpoint as loopback/private.
    from personalclaw.dashboard.handlers.local_model import register_local_model_routes

    register_local_model_routes(app)
    # The model step's VERIFICATION: run chat's real resolution and relay the bridge's own
    # cause. Separate from `/api/onboarding` above because that route's `needs_model` is a
    # no-instantiate probe by contract (it is also the workflow preflight's), and a
    # declaration is not a build — see the handler module's docstring for the measured
    # state where the two disagree.
    from personalclaw.dashboard.handlers.model_check import register_model_check_routes

    register_model_check_routes(app)
    # Doctor — tiered read-only health probes
    # Scheduled-backup status, the archive list with its
    # retention plan, and on-demand jobs. Restore is deliberately NOT here (see the
    # handler module docstring).
    app.router.add_get("/api/durability/status", handlers.api_durability_status)
    app.router.add_post("/api/durability/run", handlers.api_durability_run)
    # The DSAR surface. These four RETIRED `/api/durability/snapshots`,
    # `/api/durability/restore` and the whole `/api/portability/*` trio: one export
    # endpoint, one import endpoint, one archive list, one restore.
    app.router.add_post("/api/durability/export", handlers.api_durability_export)
    app.router.add_post("/api/durability/import", handlers.api_durability_import)
    app.router.add_get("/api/durability/archive", handlers.api_durability_archive)
    app.router.add_post(
        "/api/durability/archive/{id}/restore", handlers.api_durability_archive_restore
    )
    # The conflict review queue. `durability/conflicts.py` shipped the
    # detector and the durable queue with no route at all, so a both-sides-edited
    # divergence held the local row and was then invisible. Owner-only; the resolve
    # writes a chosen row into the live store, so it is confirm-gated.
    app.router.add_get("/api/durability/conflicts", handlers.api_durability_conflicts)
    app.router.add_post(
        "/api/durability/conflicts/{id}/resolve", handlers.api_durability_conflict_resolve
    )
    # Workspace time travel. The operate route is two-phase: no
    # `confirm` returns the preview, and confirming requires echoing the
    # `expected_head` that preview handed back, so a destructive call cannot be
    # made without having seen what it would do.
    app.router.add_get("/api/durability/history", handlers.api_durability_history)
    app.router.add_get(
        "/api/durability/history/{root}/timeline", handlers.api_durability_history_timeline
    )
    app.router.add_post(
        "/api/durability/history/{root}/{op}", handlers.api_durability_history_operate
    )
    # The Electron shell seam. The three POSTs are
    # loopback-only and credential-bearing (see handlers/desktop.py); the GETs are
    # the truth surface for Settings → Security and for apps holding a manifest
    # ``desktop`` grant. Register the specific /capabilities/{cap} path after
    # /state so neither shadows the other.
    app.router.add_post("/api/desktop/register", handlers.api_desktop_register)
    app.router.add_post("/api/desktop/unregister", handlers.api_desktop_unregister)
    app.router.add_get("/api/desktop/state", handlers.api_desktop_state)
    app.router.add_post("/api/desktop/state", handlers.api_desktop_state_push)
    app.router.add_get("/api/desktop/capabilities/{cap}", handlers.api_desktop_capability)
    app.router.add_get("/api/doctor", handlers.api_doctor)
    # Specific GET sub-paths BEFORE the {capability} catch-all (aiohttp matches in
    # registration order — otherwise "fixes"/"crash"/"remediation" bind as a capability).
    app.router.add_get("/api/doctor/fixes", handlers.api_doctor_fixes)
    app.router.add_get("/api/doctor/crash/{filename}", handlers.api_doctor_crash)
    app.router.add_get("/api/doctor/remediation", handlers.api_doctor_remediation)
    app.router.add_get("/api/doctor/{capability}", handlers.api_doctor_capability)
    # No-model degraded-mode contract
    app.router.add_get("/api/resilience/degraded", handlers.api_degraded)
    # Feedback Signal — 👍/👎 capture + per-producer accuracy
    from personalclaw.dashboard.handlers.feedback import register_feedback_routes

    register_feedback_routes(app)
    # The installed-pack ledger reader + the re-runnable
    # "Finish setup" chip backend. Export/import UI + store cards land.
    from personalclaw.dashboard.handlers.packs import register_pack_routes

    register_pack_routes(app)
    # Cost & token observability — read-only rollup/totals over the usage ledger.
    from personalclaw.dashboard.handlers.usage import register_usage_routes

    register_usage_routes(app)
    # The read-only per-model efficiency view (routing fold +
    # a bounded model_calls.jsonl tail); the Routing & Efficiency tab renders it.
    from personalclaw.dashboard.handlers.model_telemetry import register_model_telemetry_routes

    register_model_telemetry_routes(app)
    # The prices model calls are counted at (Settings → Usage → Model prices): the owner's.
    from personalclaw.dashboard.handlers.model_rates import register_model_rates_routes

    register_model_rates_routes(app)
    # The requests you are waiting for while a local model is busy, and moving one on now.
    from personalclaw.dashboard.handlers.model_waits import register_model_waits_routes

    register_model_waits_routes(app)
    # Learning Flywheel §6.1 — the Proposal Inbox + the staging week panel. Its accept route is the
    # HTTP half of the human-installs invariant: the actor is derived from the request, never the
    # body, so an app-scoped token cannot name itself a reviewer.
    from personalclaw.dashboard.handlers.learning import register_learning_routes

    register_learning_routes(app)
    # The judge tier-recommendation table. Read-only: the RUN is
    # `personalclaw judge-bench` (540 judge calls on the full matrix), so no route starts one.
    from personalclaw.dashboard.handlers.evals import register_evals_routes

    register_evals_routes(app)
    # Investigate Anywhere — chat-with-context from any entity row
    from personalclaw.dashboard.handlers.investigate import register_investigate_routes

    register_investigate_routes(app)
    # Confirm-gated fixes + trust simulators.
    # POST routes don't collide with the {capability} GET; the two GETs above are
    # ordered before it.
    app.router.add_post("/api/doctor/fix/{fix_id}", handlers.api_doctor_fix_apply)
    app.router.add_post("/api/doctor/simulate/surfacing", handlers.api_doctor_simulate_surfacing)
    app.router.add_post("/api/doctor/simulate/automation", handlers.api_doctor_simulate_automation)
    app.router.add_post("/api/doctor/remediation/run", handlers.api_doctor_remediation_run)
    # Skills marketplace
    from personalclaw.dashboard.handlers.skills import (
        api_ephemeral_skill_discard,
        api_ephemeral_skill_promote,
        api_ephemeral_skills_list,
        api_skill_files,
        api_skill_overlay_revert,
        api_skill_proposal_accept,
        api_skill_proposal_detail,
        api_skill_proposal_reject,
        api_skill_proposals_list,
        api_skill_verify,
        api_skills_delete,
        api_skills_install,
        api_skills_list,
        api_skills_marketplace_detail,
        api_skills_marketplaces,
        api_skills_search,
    )

    app.router.add_get("/api/skills", api_skills_list)
    app.router.add_get("/api/skills/marketplaces", api_skills_marketplaces)
    app.router.add_get("/api/skills/search", api_skills_search)
    app.router.add_get("/api/skills/marketplace/detail", api_skills_marketplace_detail)
    app.router.add_post("/api/skills/install", api_skills_install)
    # Ephemeral session-skill drafts (skill-ephemeral-promotion) — literal
    # 'ephemeral' segment precedes the catch-all /{name} routes below.
    app.router.add_get("/api/skills/ephemeral/{session}", api_ephemeral_skills_list)
    app.router.add_post("/api/skills/ephemeral/{session}/promote", api_ephemeral_skill_promote)
    app.router.add_delete("/api/skills/ephemeral/{session}/{slug}", api_ephemeral_skill_discard)
    # Skill-proposals inbox (skill-evolution-proposal-only) — propose-only review.
    app.router.add_get("/api/skills/proposals", api_skill_proposals_list)
    app.router.add_get("/api/skills/proposals/{id}", api_skill_proposal_detail)
    app.router.add_post("/api/skills/proposals/{id}/accept", api_skill_proposal_accept)
    app.router.add_delete("/api/skills/proposals/{id}", api_skill_proposal_reject)
    # Accepted-refinement sidecar overlays — revert = delete one file. Literal
    # 'overlay' segment, registered before the catch-all /{name} routes below.
    app.router.add_post("/api/skills/overlay/revert", api_skill_overlay_revert)
    # Provider-backed file browser — must precede the catch-all skill-detail GET.
    app.router.add_get("/api/skills/{name}/files", api_skill_files)
    app.router.add_post("/api/skills/{name}/verify", api_skill_verify)
    app.router.add_delete("/api/skills/{name}", api_skills_delete)

    # App Platform (A4) — lifecycle REST + backend reverse-proxy.
    from personalclaw.dashboard.handlers.apps import register_app_routes

    register_app_routes(app)
    from personalclaw.dashboard.handlers.providers import (
        api_agent_provider_agents,
        api_agent_provider_test,
        api_agent_providers_list,
        api_agent_runner_allow,
        api_agent_runner_check,
        api_agent_runners_list,
        api_provider_create,
        api_provider_delete,
        api_provider_model_delete,
        api_provider_model_pull,
        api_provider_model_search,
        api_provider_model_show,
        api_provider_models,
        api_provider_test,
        api_provider_types,
        api_provider_update,
        api_providers_list,
    )

    app.router.add_get("/api/model-providers", api_providers_list)
    app.router.add_get("/api/model-provider-types", api_provider_types)
    app.router.add_get("/api/agent-providers", api_agent_providers_list)
    app.router.add_post("/api/agent-providers/{id}/test", api_agent_provider_test)
    app.router.add_get("/api/agent-providers/{id}/agents", api_agent_provider_agents)
    app.router.add_get("/api/agent-runners", api_agent_runners_list)
    app.router.add_post("/api/agent-runners/{id}/check", api_agent_runner_check)
    app.router.add_post("/api/agent-runners/{id}/allow", api_agent_runner_allow)
    app.router.add_post("/api/model-providers", api_provider_create)
    app.router.add_put("/api/model-providers/{name}", api_provider_update)
    app.router.add_delete("/api/model-providers/{name}", api_provider_delete)
    app.router.add_post("/api/model-providers/{name}/test", api_provider_test)
    app.router.add_get("/api/model-providers/{name}/models", api_provider_models)
    app.router.add_get("/api/model-providers/{name}/search", api_provider_model_search)
    app.router.add_get("/api/model-providers/{name}/show", api_provider_model_show)
    app.router.add_post("/api/model-providers/{name}/pull", api_provider_model_pull)
    app.router.add_post("/api/model-providers/{name}/models/delete", api_provider_model_delete)

    # Model registry (unified model discovery + active model assignments)
    from personalclaw.dashboard.handlers.model_registry import register_model_registry_routes

    register_model_registry_routes(app)

    # Search registry (the Search entity — providers + per-use-case bindings)
    from personalclaw.dashboard.handlers.search_registry import register_search_registry_routes

    register_search_registry_routes(app)

    # Async bundled-model downloads (embedding/STT/TTS) — one job/SSE path for all
    from personalclaw.dashboard.handlers.model_downloads import register_model_download_routes

    register_model_download_routes(app)

    # Embedding re-index jobs (triggered when the active embedding model changes)
    from personalclaw.dashboard.handlers.embedding_reindex import register_embedding_reindex_routes

    register_embedding_reindex_routes(app)

    # Suggestions (pre-computed contextual prompts)
    app.router.add_get("/api/suggestions", api_suggestions)

    # Memory
    app.router.add_get("/api/memory/preferences", handlers.api_memory_preferences)
    app.router.add_put("/api/memory/preferences", handlers.api_memory_preferences)
    app.router.add_get("/api/memory/projects", handlers.api_memory_projects)
    app.router.add_put("/api/memory/projects", handlers.api_memory_projects)
    app.router.add_get("/api/memory/history", handlers.api_memory_history)
    app.router.add_get("/api/memory/history/{day}", handlers.api_memory_history_day)
    app.router.add_put("/api/memory/history/{day}", handlers.api_memory_history_day)
    app.router.add_get("/api/memory/settings", handlers.api_memory_settings)
    app.router.add_put("/api/memory/settings", handlers.api_memory_settings)

    # STT (Speech-to-Text) — the active model is set via /api/models/active; this
    # endpoint transcribes uploaded audio with it. Behavior lives in
    # use_case_settings/stt.json.
    app.router.add_post("/api/stt/transcribe", handlers.api_stt_transcribe)

    # STT-only reads: which ffmpeg transcription runs (the Speech settings show it)
    from personalclaw.stt.handlers import register_stt_routes

    register_stt_routes(app)

    # Lexicon / Vocabulary (LEX.6): terms + learned corrections
    from personalclaw.lexicon.handlers import register_lexicon_routes

    register_lexicon_routes(app)

    # The triage digest. Registered beside the approval
    # rules on purpose: the digest card and the rules manager read one system, and the rules
    # endpoints below are the manager's half of it.
    app.router.add_get("/api/proactive/digest", handlers.api_proactive_digest)
    app.router.add_post("/api/proactive/digest/reply", handlers.api_proactive_reply)
    app.router.add_post("/api/proactive/install", handlers.api_proactive_install)

    # The Decision Journal. Under `/api/knowledge/`
    # rather than `/api/proactive/` because a decision IS a knowledge item and the view is a
    # lens on the library, not a new destination — the path is the IA. A literal segment, so it
    # cannot be shadowed by `/api/knowledge/items/{id}`.
    app.router.add_get("/api/knowledge/decisions", handlers.api_decision_journal)

    # Vector Memory (Semantic)
    app.router.add_get("/api/memory/approval-rules", handlers.api_memory_approval_rules)
    app.router.add_post("/api/memory/approval-rules", handlers.api_memory_approval_rule_add)
    app.router.add_delete(
        "/api/memory/approval-rules/{key:.+}", handlers.api_memory_approval_rule_delete
    )
    app.router.add_get("/api/memory/semantic", handlers.api_memory_semantic)
    app.router.add_put("/api/memory/semantic", handlers.api_memory_semantic_write)
    app.router.add_delete("/api/memory/semantic/{key:.+}", handlers.api_memory_semantic_delete)
    app.router.add_get("/api/memory/events", handlers.api_memory_events)
    app.router.add_post("/api/memory/events/{event_id}/undo", handlers.api_memory_event_undo)
    app.router.add_get("/api/memory/lint", handlers.api_memory_lint)
    app.router.add_get("/api/memory/episodic/search", handlers.api_memory_episodic_search)
    app.router.add_get("/api/memory/recall", handlers.api_memory_recall)
    app.router.add_get("/api/memory/episodic", handlers.api_memory_episodic_list)
    app.router.add_delete("/api/memory/episodic/{id}", handlers.api_memory_episodic_delete)
    app.router.add_get("/api/memory/stats", handlers.api_memory_stats)
    # Every memory she has, the global one and each folder's (each route above takes ?partition=).
    app.router.add_get("/api/memory/partitions", handlers.api_memory_partitions)
    app.router.add_delete("/api/memory/partitions/{id}", handlers.api_memory_partition_delete)
    app.router.add_get("/api/memory/vault", handlers.api_memory_vault_status)
    app.router.add_post("/api/memory/vault/sync", handlers.api_memory_vault_sync)
    app.router.add_get("/api/memory/daily-digests", handlers.api_memory_daily_digests)
    app.router.add_post("/api/memory/migrate", handlers.api_memory_migrate)
    app.router.add_post("/api/memory/import", handlers.api_memory_import)
    app.router.add_get("/api/memory/context-preview", handlers.api_memory_context_preview)
    app.router.add_post("/api/memory/consolidate", handlers.api_memory_consolidate)
    app.router.add_get("/api/session/archive", handlers.api_session_archive_list)
    app.router.add_get("/api/session/archive/{name}", handlers.api_session_archive_read)
    app.router.add_get("/api/memory/observability", handlers.api_memory_observability)
    app.router.add_get("/api/memory/graph", handlers.api_memory_graph)
    app.router.add_post("/api/memory/promote", handlers.api_memory_promote)
    # The typed entity graph (distinct from
    # /api/memory/graph, which renders the record visualization).
    app.router.add_get("/api/memory/entities", handlers.api_memory_entities)
    app.router.add_post("/api/memory/entities", handlers.api_memory_entity_create)
    app.router.add_post("/api/memory/entities/proposals", handlers.api_memory_entity_proposals)
    app.router.add_get("/api/memory/entities/proposals", handlers.api_memory_entity_proposals_list)
    app.router.add_get(
        "/api/memory/entities/{entity_id}/backlinks", handlers.api_memory_entity_backlinks
    )
    app.router.add_delete("/api/memory/entities/{entity_id}", handlers.api_memory_entity_delete)
    app.router.add_post("/api/memory/graph/rebuild", handlers.api_memory_graph_rebuild)
    app.router.add_get("/api/memory/volunteer-stats", handlers.api_memory_volunteer_stats)
    # The entity topology behind the graph canvas
    # and its one-file export. Registered BEFORE the record-graph catch-alls above would
    # matter: both live under /api/memory/graph, so the more specific paths are explicit.
    app.router.add_get("/api/memory/graph/entities", handlers.api_memory_entity_graph)
    app.router.add_get("/api/memory/record-links", handlers.api_memory_record_links)
    app.router.add_get("/api/memory/graph/export", handlers.api_memory_graph_export)
    # The Slots editor. GET lists every register (built-ins included, even
    # unmaterialized); the writes ride MemoryService so the WAL/undo cover them.
    app.router.add_get("/api/memory/slots", handlers.api_memory_slots)
    app.router.add_post("/api/memory/slots/{name}/lines", handlers.api_memory_slot_append)
    app.router.add_post(
        "/api/memory/slots/{name}/lines/retire", handlers.api_memory_slot_line_retire
    )
    # C15 — the facet overrides. `{key:.+}` because a facet key is dot-separated
    # (`pref.facet.style.<md5>`) and the default segment match would stop at the first dot.
    app.router.add_get("/api/memory/facets", handlers.api_memory_facets)
    app.router.add_post("/api/memory/facets/{key:.+}/pin", handlers.api_memory_facet_pin)
    app.router.add_post("/api/memory/facets/{key:.+}/forget", handlers.api_memory_facet_forget)

    # Crons, lessons, spawn, send-message, notifications
    # are registered via server.py's _register_mcp_routes(), before this table.

    # Action providers (the action catalog) + agent-scoped lifecycle view. The
    # lifecycle-trigger CRUD lives under /api/triggers now (registered above).
    app.router.add_get("/api/action-providers", handlers.api_action_providers)
    app.router.add_get("/api/agent-hooks", handlers.api_agent_hooks)
    app.router.add_post("/api/agent-hooks/allow", handlers.api_agent_hook_allow)
    # The HEARTBEAT.md queue: which tasks wait for the owner, and the owner's yes to one.
    app.router.add_get("/api/heartbeat/tasks", handlers.api_heartbeat_tasks)
    app.router.add_post("/api/heartbeat/tasks/allow", handlers.api_heartbeat_task_allow)

    # Prompts (Agent SOPs)
    app.router.add_get("/api/prompts", handlers.api_prompts)
    app.router.add_post("/api/prompts", handlers.api_prompt_create)
    # Bindings routes registered BEFORE the {name:.+} catch-all so the literal
    # path isn't swallowed by the prompt-detail matcher.
    app.router.add_get("/api/prompts/bindings", handlers.api_prompt_bindings)
    app.router.add_put("/api/prompts/bindings", handlers.api_prompt_bindings_save)
    # Live authoring helpers — literal paths registered BEFORE the {name:.+}
    # catch-all so they aren't swallowed by the prompt-detail matcher.
    app.router.add_post("/api/prompts/preview", handlers.api_prompt_preview)
    app.router.add_get("/api/prompts/syntax", handlers.api_prompt_syntax)
    app.router.add_post("/api/prompts/{name:.+}/render", handlers.api_prompt_render)
    # Runnable "campaign template" launch (#17) — render + create + start a loop. Sits
    # with the other {name:.+}/<verb> routes, BEFORE the bare {name:.+} catch-all.
    app.router.add_post("/api/prompts/{name:.+}/launch", handlers.api_campaign_template_launch)
    app.router.add_put("/api/prompts/{name:.+}", handlers.api_prompt_save)
    app.router.add_delete("/api/prompts/{name:.+}", handlers.api_prompt_delete)
    app.router.add_get("/api/prompts/{name:.+}", handlers.api_prompt_detail)

    # Prompt snippets — reusable {{> name}} fragments. A distinct path tree so it's
    # not swallowed by the /api/prompts/{name:.+} catch-all above.
    app.router.add_get("/api/prompt-snippets", handlers.api_snippets)
    app.router.add_post("/api/prompt-snippets", handlers.api_snippet_create)
    app.router.add_post("/api/prompt-snippets/{name:.+}/render", handlers.api_snippet_render)
    app.router.add_put("/api/prompt-snippets/{name:.+}", handlers.api_snippet_save)
    app.router.add_delete("/api/prompt-snippets/{name:.+}", handlers.api_snippet_delete)
    app.router.add_get("/api/prompt-snippets/{name:.+}", handlers.api_snippet_detail)

    # Skills (CRUD detail — list/search/install are handled by the marketplace routes above)
    app.router.add_post("/api/skills", handlers.api_skills_create)
    app.router.add_get("/api/skills/{name:.+}", handlers.api_skill_detail)
    app.router.add_put("/api/skills/{name:.+}", handlers.api_skill_detail)

    # Custom Themes (CRUD)
    app.router.add_get("/api/themes", handlers.api_themes)
    app.router.add_post("/api/themes", handlers.api_themes_create)
    app.router.add_get("/api/themes/{slug}", handlers.api_theme_detail)
    app.router.add_put("/api/themes/{slug}", handlers.api_theme_detail)
    app.router.add_delete("/api/themes/{slug}", handlers.api_theme_detail)

    # Agent config
    app.router.add_get("/api/agent/config", handlers.api_agent_config)
    app.router.add_put("/api/agent/config", handlers.api_agent_config)
    app.router.add_get("/api/config/default-agent", handlers.api_default_agent)
    app.router.add_put("/api/config/default-agent", handlers.api_default_agent)
    app.router.add_get("/api/config/schema", handlers.api_config_schema)
    app.router.add_get("/api/config/personalclaw", handlers.api_personalclaw_config)
    app.router.add_put("/api/config/personalclaw", handlers.api_personalclaw_config)
    app.router.add_patch("/api/config/personalclaw", handlers.api_personalclaw_config_patch)
    # Companion apps: whether the LAN advertiser is actually running,
    # which is not the same question as whether the config flag is set.
    app.router.add_get("/api/companion/discovery", handlers.api_companion_discovery)
    app.router.add_get("/api/incident", handlers.api_incident)
    app.router.add_post("/api/incident", handlers.api_incident)
    app.router.add_post("/api/incident/resume", handlers.api_incident_resume)
    app.router.add_get("/api/guardrails/project-trust", handlers.api_project_trust)
    app.router.add_post("/api/guardrails/project-trust", handlers.api_project_trust)
    # Settings → External Access. Read + client lifecycle only:
    # the surface switches ride the existing `_EDITABLE_CONFIG` PATCH path, and there is
    # deliberately NO route here that can write `public_url`, `allow_remote` or a token.
    app.router.add_get("/api/external-access", handlers.api_external_access)
    app.router.add_post("/api/external-access/clients", handlers.api_external_access_client)
    app.router.add_delete(
        "/api/external-access/clients/{client_id}", handlers.api_external_access_client
    )
    app.router.add_post(
        "/api/external-access/clients/{client_id}/disabled",
        handlers.api_external_access_client_toggle,
    )
    # Your answer to a control-bridge action that waits for you (`inbound/bridge.py`).
    app.router.add_post(
        "/api/external-access/bridge/confirmations/{id}", handlers.api_bridge_confirmation
    )
    app.router.add_get("/api/models/health", handlers.api_models_health)
    # The earned-autonomy ladder. One read + three writes, and only ONE of the three
    # increases autonomy — see handlers/autonomy.py for why that asymmetry is the design.
    app.router.add_get("/api/autonomy", handlers.api_autonomy)
    app.router.add_post("/api/autonomy/grant", handlers.api_autonomy_grant)
    app.router.add_post("/api/autonomy/demote", handlers.api_autonomy_demote)
    app.router.add_post("/api/autonomy/undo", handlers.api_autonomy_undo)
    app.router.add_get("/api/dashboard/config", handlers.api_dashboard_config)
    app.router.add_put("/api/dashboard/config", handlers.api_dashboard_config)
    # Dashboard-as-views registry (AMBIENT-SURFACES §1 / A2-1). Literal /views first,
    # then the {view_id} routes + tile sub-routes; tiles/resolve is registered before
    # the bare {view_id} tiles POST so the more-specific literal wins. Presets are
    # read-only (PUT/DELETE on a preset → 403).
    from personalclaw.dashboard.handlers.views import (
        api_dashboard_view_detail,
        api_dashboard_view_tile_action,
        api_dashboard_view_tile_binding,
        api_dashboard_view_tile_refresh,
        api_dashboard_view_tile_resolve,
        api_dashboard_view_tiles,
        api_dashboard_views,
        api_genui_library,
    )

    # Generative-UI component catalog — read-only.
    app.router.add_get("/api/genui/library", api_genui_library)
    # The L2 user/agent surface overlays. Read-only by
    # design: an overlay is authored with the ordinary file tools, so an HTTP writer here
    # would be a second producer with a second set of refusals.
    from personalclaw.dashboard.handlers.surfaces import api_surface_overlays

    app.router.add_get("/api/surfaces/overlays", api_surface_overlays)
    app.router.add_get("/api/dashboard/views", api_dashboard_views)
    app.router.add_post("/api/dashboard/views", api_dashboard_views)
    app.router.add_post(
        "/api/dashboard/views/{view_id}/tiles/resolve", api_dashboard_view_tile_resolve
    )
    # Chatless refresh. Literal sub-routes, registered before the bare
    # {view_id}/tiles POST for the same more-specific-wins reason as tiles/resolve.
    app.router.add_put(
        "/api/dashboard/views/{view_id}/tiles/binding", api_dashboard_view_tile_binding
    )
    app.router.add_post(
        "/api/dashboard/views/{view_id}/tiles/refresh", api_dashboard_view_tile_refresh
    )
    app.router.add_get(
        "/api/dashboard/views/{view_id}/tiles/refresh", api_dashboard_view_tile_refresh
    )
    # A genui control inside a tile widget re-firing the tile's bound workflow, fenced by
    # that tile's frozen capability set.
    app.router.add_post(
        "/api/dashboard/views/{view_id}/tiles/action", api_dashboard_view_tile_action
    )
    app.router.add_post("/api/dashboard/views/{view_id}/tiles", api_dashboard_view_tiles)
    app.router.add_get("/api/dashboard/views/{view_id}", api_dashboard_view_detail)
    app.router.add_put("/api/dashboard/views/{view_id}", api_dashboard_view_detail)
    app.router.add_delete("/api/dashboard/views/{view_id}", api_dashboard_view_detail)

    # MCP servers
    app.router.add_get("/api/mcp", handlers.api_mcp_servers)
    app.router.add_get("/api/mcp/active", handlers.api_mcp_active)
    app.router.add_post("/api/mcp/probe", handlers.api_mcp_probe)
    app.router.add_get("/api/mcp/probe", handlers.api_mcp_probe_cached)
    app.router.add_post("/api/mcp/probe/{name}", handlers.api_mcp_probe_one)
    app.router.add_get("/api/mcp/pool-stats", handlers.api_mcp_pool_stats)
    app.router.add_get("/api/mcp/importable", handlers.api_mcp_importable)
    app.router.add_post("/api/mcp/sync", handlers.api_mcp_sync)
    app.router.add_post("/api/mcp/apply", handlers.api_mcp_apply)
    app.router.add_post("/api/mcp/toggle", handlers.api_mcp_toggle)
    app.router.add_post("/api/mcp/toggle-tool", handlers.api_mcp_toggle_tool)
    app.router.add_post("/api/mcp/toggle-all", handlers.api_mcp_toggle_all)
    # One MCP server: the edit form's read, add or edit, remove
    app.router.add_get("/api/mcp/servers/{name}", handlers.api_mcp_server_detail)
    app.router.add_put("/api/mcp/servers/{name}", handlers.api_mcp_server_detail)
    app.router.add_delete("/api/mcp/servers/{name}", handlers.api_mcp_server_detail)
    # The owner's yes to a server that waits for it (`mcp_grants`).
    app.router.add_post("/api/mcp/servers/{name}/allow", handlers.api_mcp_server_allow)
    # Signing in to a server at a URL with OAuth: start (POST), sign out (DELETE), and the page
    # the authorization server sends the browser back to.
    app.router.add_post("/api/mcp/servers/{name}/sign-in", handlers.api_mcp_server_sign_in)
    app.router.add_delete("/api/mcp/servers/{name}/sign-in", handlers.api_mcp_server_sign_in)
    app.router.add_get("/api/mcp/oauth/callback", handlers.api_mcp_oauth_callback)
    # Skills marketplace integration

    # Chat
    app.router.add_post("/api/chat", chat.api_chat)
    app.router.add_get("/api/chat/sessions", chat.api_chat_sessions)
    app.router.add_post("/api/chat/sessions", chat.api_chat_session_create)
    app.router.add_post("/api/chat/sessions/cleanup", chat.api_chat_sessions_cleanup)
    app.router.add_get("/api/chat/screen-frame", chat.api_chat_screen_state)
    app.router.add_get("/api/chat/image-input", chat.api_chat_image_input)
    app.router.add_post("/api/chat/screen-frame", chat.api_chat_screen_frame)
    app.router.add_post("/api/chat/screen-frame/pin", chat.api_chat_screen_frame_pin)
    # Bulk ops + the session lifecycle (archive/restore/auto-archive). Registered
    # BEFORE the `{session}` routes below so the literal `bulk`/`auto-archive` paths
    # aren't captured as a session name by the dynamic pattern.
    from personalclaw.dashboard import session_bulk, session_starters

    session_bulk.register_routes(app)
    # Session templates + transcript export (S3). The literal `templates` segment has
    # the same capture hazard as `bulk` above, so it registers here too.
    session_starters.register_routes(app)
    # The calling session's bound Project, keyed off `X-Session-Key` (ACP-AGENT-PARITY
    # §2.6 gap 10). An ACP CLI's tools run in a separate `mcp-core` process where the
    # native runtime's per-turn contextvar is empty, so `artifact_save` asks the gateway
    # instead of having a new argument threaded through the protocol. Same literal-segment
    # capture hazard as `bulk`/`templates` above, hence this position — and `bound-project`
    # rather than `project` so it can never be misread as a session named "project".
    app.router.add_get("/api/chat/sessions/bound-project", chat.api_chat_session_bound_project)
    # Whether the calling session keeps nothing, for the same `mcp-core` process: its tools run
    # outside the gateway's session scope, so each call asks before it hands anything to a model.
    # Registered here for the same capture hazard.
    app.router.add_get("/api/chat/sessions/model-reach", chat.api_chat_session_model_reach)
    app.router.add_get("/api/chat/sessions/{session}", chat.api_chat_session_detail)
    # The durable session map: the in-session index's marks + per-turn telemetry,
    # served without hydrating the whole transcript client-side.
    app.router.add_get("/api/chat/sessions/{session}/map", chat.api_chat_session_map)
    app.router.add_get("/api/chat/sessions/{session}/tool-result/{rid}", chat.api_chat_tool_result)
    app.router.add_post("/api/chat/sessions/{session}/stop", chat.api_chat_session_stop)
    app.router.add_post("/api/chat/sessions/{session}/interrupt", chat.api_chat_session_interrupt)
    app.router.add_delete(
        "/api/chat/sessions/{session}/queue/{queue_id}", chat.api_chat_session_queue_cancel
    )
    app.router.add_delete("/api/chat/sessions/{session}", chat.api_chat_session_delete)
    app.router.add_post("/api/chat/sessions/{session}/agent", chat.api_chat_session_agent)
    app.router.add_post("/api/chat/sessions/{session}/acp-agent", chat.api_chat_session_acp_agent)

    # Optimizer
    app.router.add_post("/api/optimizer/optimize", handlers.handle_optimize)
    app.router.add_post("/api/chat/sessions/{session}/model", chat.api_chat_session_model)
    app.router.add_post(
        "/api/chat/sessions/{session}/reasoning-effort", chat.api_chat_session_reasoning_effort
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/workspace-dir", chat.api_chat_session_workspace_dir
    )
    app.router.add_get("/api/recent-projects", chat.api_recent_projects)
    app.router.add_patch("/api/chat/sessions/{session}/color", chat.api_chat_session_color)
    app.router.add_patch(
        "/api/chat/sessions/{session}/natural-voice", chat.api_chat_session_natural_voice
    )
    # Context injection (App Kit — silent background context)
    app.router.add_post("/api/chat/sessions/{session}/context", chat.api_chat_session_context)
    app.router.add_post("/api/chat/sessions/{session}/fork", chat.api_chat_session_fork)
    # Restore a rewind tail as a NEW fork (restore = fork, never swap)
    app.router.add_post(
        "/api/chat/sessions/{session}/fork-rewound", chat.api_chat_session_fork_rewound
    )
    app.router.add_post("/api/chat/sessions/{session}/undo", chat.api_chat_session_undo)
    # /rewind-to-turn — the FILESYSTEM counterpart of /undo. GET
    # previews (read-only, no writes); POST applies and requires confirm:true.
    app.router.add_get("/api/chat/sessions/{session}/rewind", chat.api_chat_session_rewind_preview)
    app.router.add_post("/api/chat/sessions/{session}/rewind", chat.api_chat_session_rewind)
    # Side chat (ephemeral, isolated Q&A against a frozen parent snapshot)
    app.router.add_post("/api/chat/sessions/{session}/side/open", chat.api_side_open)
    app.router.add_post("/api/chat/sessions/{session}/side/turn", chat.api_side_turn)
    app.router.add_post("/api/chat/sessions/{session}/side/close", chat.api_side_close)
    # Agents
    app.router.add_get("/api/agents/installed", handlers.api_agents_installed)
    app.router.add_get("/api/slash-commands", handlers.api_slash_commands)
    app.router.add_get("/api/agents/detail/{name}", handlers.api_agent_detail)
    app.router.add_patch("/api/agents/detail/{name}", handlers.api_agent_detail)
    app.router.add_delete("/api/agents/detail/{name}", handlers.api_agent_detail)
    # PersonalClaw Agent CRUD
    app.router.add_get("/api/agents", handlers.api_personalclaw_agents)
    app.router.add_post("/api/agents", handlers.api_personalclaw_agents_create)
    app.router.add_post("/api/agents/sync", handlers.api_personalclaw_agents_sync)
    from personalclaw.dashboard.handlers.agent_export import api_agents_export

    app.router.add_post("/api/agents/export", api_agents_export)
    # Agent routing suppression endpoints — registered BEFORE the
    # /api/agents/{name} CRUD routes so "routing" is never captured as an agent name.
    from personalclaw.dashboard.handlers.routing import (
        api_routing_dismiss,
        api_routing_status,
        api_routing_unmute,
    )

    app.router.add_get("/api/agents/routing/status", api_routing_status)
    app.router.add_post("/api/agents/routing/dismiss", api_routing_dismiss)
    app.router.add_post("/api/agents/routing/unmute", api_routing_unmute)
    app.router.add_put("/api/agents/{name}", handlers.api_personalclaw_agent_update)
    app.router.add_delete("/api/agents/{name}", handlers.api_personalclaw_agent_delete)
    # Agent marketplace — local filesystem + extensible registry
    from personalclaw.dashboard.handlers.agent_marketplace import (
        api_agent_marketplace_activate,
        api_agent_marketplace_create,
        api_agent_marketplace_delete,
        api_agent_marketplace_get,
        api_agent_marketplace_list,
        api_agent_marketplace_list_marketplaces,
        api_agent_marketplace_test,
        api_agent_marketplace_update,
    )

    app.router.add_get(
        "/api/agent-marketplace/marketplaces", api_agent_marketplace_list_marketplaces
    )
    app.router.add_get("/api/agent-marketplace/agents", api_agent_marketplace_list)
    app.router.add_post("/api/agent-marketplace/agents", api_agent_marketplace_create)
    app.router.add_get("/api/agent-marketplace/agents/{name}", api_agent_marketplace_get)
    app.router.add_put("/api/agent-marketplace/agents/{name}", api_agent_marketplace_update)
    app.router.add_delete("/api/agent-marketplace/agents/{name}", api_agent_marketplace_delete)
    app.router.add_post(
        "/api/agent-marketplace/agents/{name}/activate", api_agent_marketplace_activate
    )
    app.router.add_post("/api/agent-marketplace/agents/{name}/test", api_agent_marketplace_test)
    # Agent metadata
    app.router.add_get("/api/agent-metadata/{name}", handlers.api_agent_metadata_get)
    app.router.add_put("/api/agent-metadata/{name}", handlers.api_agent_metadata_put)
    app.router.add_delete("/api/agent-metadata/{name}", handlers.api_agent_metadata_delete)
    # Session workspace (Orchestrated Chat)
    app.router.add_get("/api/sessions/{id}/agents", handlers.api_session_agents_list)
    app.router.add_get("/api/sessions/{id}/agents/{agent_id}", handlers.api_session_agent_result)
    app.router.add_get(
        "/api/sessions/{id}/agents/{agent_id}/stream", handlers.api_session_agent_stream
    )
    app.router.add_post("/api/chat/sessions/{session}/resume", chat.api_chat_session_resume)
    app.router.add_post("/api/chat/sessions/{session}/approve", chat.api_chat_session_approve)
    app.router.add_post(
        "/api/chat/sessions/{session}/questions/{question}/answer",
        chat_questions.api_chat_question_answer,
    )
    app.router.add_post("/api/chat/mode", chat.api_chat_mode)
    app.router.add_post("/api/chat/task-mode", chat.api_chat_task_mode)
    # Chat plan mode — the composer affordance + the shared planning
    # walkthrough's approve/comment/edit gates, chat-owned.
    app.router.add_get("/api/chat/sessions/{session}/plan-session", chat.api_chat_plan_session)
    app.router.add_post("/api/chat/sessions/{session}/plan/activate", chat.api_chat_plan_activate)
    app.router.add_post("/api/chat/sessions/{session}/plan/edit", chat.api_chat_plan_edit)
    app.router.add_post("/api/chat/sessions/{session}/plan/comment", chat.api_chat_plan_comment)
    app.router.add_post("/api/chat/sessions/{session}/plan/approve", chat.api_chat_plan_approve)
    app.router.add_post("/api/chat/sessions/{session}/plan/cancel", chat.api_chat_plan_cancel)
    app.router.add_post("/api/chat/nav/resolve-links", chat.api_nav_resolve_links)
    app.router.add_post(
        "/api/chat/sessions/{session}/generate-title", chat.api_chat_session_generate_title
    )
    app.router.add_patch("/api/chat/sessions/{session}/title", chat.api_chat_session_rename)
    app.router.add_post("/api/chat/sessions/{session}/regenerate", chat.api_chat_session_regenerate)
    app.router.add_post(
        "/api/chat/sessions/{session}/switch-variant", chat.api_chat_session_switch_variant
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/edit-resend", chat.api_chat_session_edit_resend
    )
    # Folders
    app.router.add_get("/api/chat/folders", chat.api_chat_folders)
    app.router.add_post("/api/chat/folders", chat.api_chat_folder_create)
    app.router.add_patch("/api/chat/folders/{id}", chat.api_chat_folder_update)
    app.router.add_delete("/api/chat/folders/{id}", chat.api_chat_folder_delete)
    app.router.add_patch("/api/chat/sessions/{session}/folder", chat.api_chat_session_folder)
    app.router.add_patch("/api/chat/sessions/{session}/pin", chat.api_chat_session_pin)
    # Tags
    app.router.add_get("/api/chat/tags", chat.api_chat_tags)
    app.router.add_post("/api/chat/tags", chat.api_chat_tag_create)
    app.router.add_patch("/api/chat/tags/{id}", chat.api_chat_tag_update)
    app.router.add_delete("/api/chat/tags/{id}", chat.api_chat_tag_delete)
    app.router.add_put("/api/chat/sessions/{session}/tags", chat.api_chat_session_tags)
    app.router.add_post("/api/chat/sessions/{session}/drop", chat.api_chat_session_drop)
    # Suggested organization (SM T2.1) — the GET is read-only; only /accept mutates.
    from personalclaw.dashboard.handlers import session_organize as _sess_org

    app.router.add_get(
        "/api/chat/sessions/{session}/organize", _sess_org.api_session_organize_suggest
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/organize/accept", _sess_org.api_session_organize_accept
    )
    app.router.add_post(
        "/api/chat/sessions/{session}/organize/decline", _sess_org.api_session_organize_decline
    )
    # Magic re-tag — batch AI re-evaluation of every session's tags (board's
    # sparkle button). Progress streams over /api/ws (retag_progress/retag_done).
    from personalclaw.dashboard import chat_retag

    app.router.add_post("/api/sessions/retag-all", chat_retag.api_retag_all)
    app.router.add_get("/api/sessions/retag-all", chat_retag.api_retag_status)
    app.router.add_post("/api/sessions/retag-all/cancel", chat_retag.api_retag_cancel)
    app.router.add_get("/api/chat/tag-columns", chat.api_chat_tag_columns)
    app.router.add_post("/api/chat/tag-columns", chat.api_chat_tag_column_create)
    app.router.add_put("/api/chat/tag-columns/order", chat.api_chat_tag_columns_reorder)
    app.router.add_patch("/api/chat/tag-columns/{id}", chat.api_chat_tag_column_update)
    app.router.add_delete("/api/chat/tag-columns/{id}", chat.api_chat_tag_column_delete)
    app.router.add_post("/api/voice/synthesize", chat.api_voice_synthesize)

    # Voice profiles + per-surface bindings.
    from personalclaw.dashboard.handlers import voice_profiles as _vprof

    app.router.add_get("/api/voice/profiles", _vprof.api_voice_profiles_list)
    app.router.add_post("/api/voice/profiles", _vprof.api_voice_profile_create)
    app.router.add_get("/api/voice/bindings", _vprof.api_voice_bindings_get)
    app.router.add_put("/api/voice/bindings", _vprof.api_voice_bindings_put)
    app.router.add_delete("/api/voice/bindings", _vprof.api_voice_bindings_delete)
    app.router.add_post("/api/voice/migrate", _vprof.api_voice_migrate)
    app.router.add_get("/api/voice/resolve", _vprof.api_voice_resolve)
    app.router.add_get("/api/voice/profiles/{id}", _vprof.api_voice_profile_get)
    app.router.add_put("/api/voice/profiles/{id}", _vprof.api_voice_profile_update)
    app.router.add_delete("/api/voice/profiles/{id}", _vprof.api_voice_profile_delete)
    app.router.add_get("/api/voice/profiles/{id}/audio", _vprof.api_voice_profile_audio)
    app.router.add_post("/api/voice/profiles/{id}/lock", _vprof.api_voice_profile_lock)
    app.router.add_post("/api/voice/profiles/{id}/unlock", _vprof.api_voice_profile_unlock)
    app.router.add_post("/api/voice/profiles/{id}/consent", _vprof.api_voice_profile_consent_record)
    app.router.add_post(
        "/api/voice/profiles/{id}/consent/verify", _vprof.api_voice_profile_consent_verify
    )
    app.router.add_delete(
        "/api/voice/profiles/{id}/consent", _vprof.api_voice_profile_consent_revoke
    )
    app.router.add_post("/api/chat/sessions/{session}/handoff", chat.api_chat_session_handoff)
    app.router.add_post(
        "/api/chat/sessions/{session}/channel-link", chat.api_chat_session_channel_link
    )
    app.router.add_get("/api/channels/reply-targets", chat.api_channel_reply_targets)

    app.router.add_post("/api/reveal", handlers.api_reveal_path)
    app.router.add_get("/api/file-read", handlers.api_file_read)
    app.router.add_get("/api/file-raw", handlers.api_file_raw)
    app.router.add_get("/api/file-watch", handlers.api_file_watch)
    app.router.add_get("/api/config-fs/stream", handlers.api_config_fs_watch)
    app.router.add_post("/api/file-write", handlers.api_file_write)
    app.router.add_get("/api/file-search", handlers.api_file_search)
    app.router.add_get("/api/file-list", handlers.api_file_list)
    app.router.add_get("/api/file-git-status", handlers.api_file_git_status)
    app.router.add_get("/api/file-git-log", handlers.api_file_git_log)
    app.router.add_get("/api/file-git-commit", handlers.api_file_git_commit)
    app.router.add_get("/api/file-git-original", handlers.api_file_git_original)
    app.router.add_get("/api/file-content-search", handlers.api_file_content_search)
    app.router.add_get("/api/file-complete", handlers.api_file_complete)
    app.router.add_post("/api/file-create", handlers.api_file_create)
    app.router.add_post("/api/file-move", handlers.api_file_move)
    app.router.add_post("/api/file-delete", handlers.api_file_delete)
    app.router.add_post("/api/file-upload", handlers.api_file_upload)
    app.router.add_get("/api/browse-dirs", handlers.api_browse_dirs)
    app.router.add_post("/api/create-dir", handlers.api_create_dir)
    app.router.add_post("/api/upload", handlers.api_upload)
    app.router.add_post("/api/upload/file", handlers.api_upload_file)
    # Resumable large-file upload protocol (init/part/status/complete). The part
    # bodies stream via request.content, which bypasses client_max_size (that only
    # gates buffered .read()/.post()) — so a 2 GB upload flows through the tight
    # main-app ceiling without relaxing it for any buffered endpoint. Registered
    # here rather than on a sub-app because an aiohttp sub-app's client_max_size is
    # ignored (the request is created with the TOP app's limit); streaming is the
    # real isolation, not a sub-app.
    _register_upload_routes(app)
    app.router.add_get("/api/attachment-extract", handlers.api_attachment_extract)
    app.router.add_post("/api/channel/upload-file", handlers.api_channel_upload_file)
    app.router.add_post("/api/outbox/notify", handlers.api_outbox_notify)
    app.router.add_get("/api/outbox", handlers.api_outbox_list)
    app.router.add_get("/api/outbox/{filename}", handlers.api_outbox_download)
    app.router.add_post("/api/screenshot", handlers.api_screenshot)

    # Portability (export/import config+memory as zip)

    # Terminal (CLI panel)
    app.router.add_get("/api/ws/terminal/{session_id}", handlers.api_terminal_ws)
    app.router.add_post("/api/terminal/sessions", handlers.api_terminal_create)
    app.router.add_get("/api/terminal/sessions", handlers.api_terminal_list)
    app.router.add_delete("/api/terminal/sessions/{session_id}", handlers.api_terminal_delete)
    app.router.add_get("/api/sandbox/providers", handlers.api_sandbox_providers)

    # Channels (comms transports) — management surface over registered transports
    from personalclaw.dashboard.handlers.channel_owner import (
        api_channel_owner,
        api_channel_owner_pairing_cancel,
        api_channel_owner_pairing_start,
    )
    from personalclaw.dashboard.handlers.channel_trust import (
        api_channel_trust,
        api_channel_trust_pairing_cancel,
        api_channel_trust_pairing_start,
        api_channel_trust_policies,
        api_channel_trust_revoke,
        api_channel_trust_track,
        api_channel_trust_untrack,
    )
    from personalclaw.dashboard.handlers.channels import (
        api_channel_connect,
        api_channel_disconnect,
        api_channel_get,
        api_channel_test,
        api_channels_list,
    )

    app.router.add_get("/api/channels", api_channels_list)
    # BEFORE the `{name}` route below, deliberately: aiohttp resolves in registration
    # order, so a later `/api/channels/trust` would be swallowed by `/api/channels/{name}`
    # and answer "unknown transport" instead of the trust posture. Railed by
    # `tests/test_channel_trust_api.py::test_trust_route_is_not_shadowed_by_the_name_route`.
    app.router.add_get("/api/channels/trust", api_channel_trust)
    app.router.add_delete(
        "/api/channels/trust/{provider}/senders/{sender_id}", api_channel_trust_revoke
    )
    app.router.add_put("/api/channels/trust/{provider}/policies", api_channel_trust_policies)
    app.router.add_post("/api/channels/trust/{provider}/channels", api_channel_trust_track)
    app.router.add_delete(
        "/api/channels/trust/{provider}/channels/{channel_id}", api_channel_trust_untrack
    )
    app.router.add_post("/api/channels/trust/{provider}/pairing", api_channel_trust_pairing_start)
    app.router.add_delete(
        "/api/channels/trust/{provider}/pairing", api_channel_trust_pairing_cancel
    )
    app.router.add_get("/api/channels/{name}", api_channel_get)
    app.router.add_post("/api/channels/{name}/connect", api_channel_connect)
    app.router.add_post("/api/channels/{name}/disconnect", api_channel_disconnect)
    app.router.add_post("/api/channels/{name}/test", api_channel_test)
    # A channel's owner: who core reaches you as there, and pairing it from its Configure page.
    app.router.add_get("/api/channels/{name}/owner", api_channel_owner)
    app.router.add_post("/api/channels/{name}/owner/pairing", api_channel_owner_pairing_start)
    app.router.add_delete("/api/channels/{name}/owner/pairing", api_channel_owner_pairing_cancel)

    # Agent Rooms — shared transcripts several bound agents deliberate in. Gated by
    # `rooms.enabled` inside each handler rather than by skipping registration, so
    # flipping the config takes effect without a gateway restart.
    from personalclaw.dashboard.handlers.rooms import (
        api_room_archive,
        api_room_continue,
        api_room_export,
        api_room_get,
        api_room_member_add,
        api_room_member_remove,
        api_room_message_post,
        api_room_update,
        api_rooms_create,
        api_rooms_list,
    )

    app.router.add_get("/api/rooms", api_rooms_list)
    app.router.add_post("/api/rooms", api_rooms_create)
    # Every literal sub-path sits BEFORE `/api/rooms/{room_id}` for the same reason the
    # channel trust route does — aiohttp resolves in registration order — except that
    # here the sub-paths are all two segments deep, so only `{room_id}` itself needs the
    # ordering care. Railed by `tests/test_rooms_api.py`.
    app.router.add_post("/api/rooms/{room_id}/archive", api_room_archive)
    app.router.add_post("/api/rooms/{room_id}/members", api_room_member_add)
    app.router.add_delete("/api/rooms/{room_id}/members/{name}", api_room_member_remove)
    app.router.add_post("/api/rooms/{room_id}/messages", api_room_message_post)
    app.router.add_post("/api/rooms/{room_id}/continue", api_room_continue)
    app.router.add_get("/api/rooms/{room_id}/export", api_room_export)
    app.router.add_get("/api/rooms/{room_id}", api_room_get)
    app.router.add_patch("/api/rooms/{room_id}", api_room_update)

    # Tools — aggregated listing from all tool providers
    from personalclaw.dashboard.handlers.tools import (
        api_providers_toggle,
        api_tool_groups,
        api_tool_invoke,
        api_tools_list,
        api_tools_savings,
        api_tools_toggle,
    )

    app.router.add_get("/api/tools", api_tools_list)
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    app.router.add_post("/api/tools/toggle", api_tools_toggle)
    app.router.add_post("/api/tools/provider-toggle", api_providers_toggle)
    app.router.add_get("/api/tools/savings", api_tools_savings)
    # Static route BEFORE any dynamic sibling would shadow it (registration order
    # is match order) — the group partition + per-surface activation defaults.
    app.router.add_get("/api/tools/groups", api_tool_groups)

    # Desktop computer use — the in-gateway dispatch the stdio shim forwards to. Internal
    # only (loopback + X-Internal-Secret, see the internal routes in server.py); the whole
    # capability is OFF until the operator arms it out-of-band, and every call runs the keystone
    # → app allowlist → index freshness → input-target screen → SEL audit chain.
    from personalclaw.dashboard.handlers.computer_use import (
        api_computer_use_dispatch,
        api_computer_use_live_view,
    )

    app.router.add_post("/api/computer-use/dispatch", api_computer_use_dispatch)
    # The human-facing live view + cursor-motion overlay data. A browser GET under
    # ordinary cookie auth — deliberately NOT internal-only like the dispatch above, and
    # deliberately sharing no verb with it: the one route that can act stays the one POST.
    app.router.add_get("/api/computer-use/live-view", api_computer_use_live_view)

    # Manifest — the generated self-description (tools + routes + providers) an
    # agent reads to drive this instance instead of guessing signatures.
    from personalclaw.dashboard.handlers.manifest import api_manifest

    app.router.add_get("/api/manifest", api_manifest)

    # Legibility — the dashboard "Discover" section + hub: a curated tour of
    # the parts of the system the user hasn't tried yet; dismissals persist and
    # engaged areas auto-hide. Never writes or enables anything on the user's behalf.
    from personalclaw.dashboard.handlers.legibility import (
        api_always_on,
        api_always_on_doc,
        api_always_on_doc_write,
        api_discover,
        api_discover_dismiss,
        api_discover_dismiss_clear,
    )

    app.router.add_get("/api/legibility/discover", api_discover)
    app.router.add_post("/api/legibility/discover/dismiss", api_discover_dismiss)
    app.router.add_delete("/api/legibility/discover/dismiss", api_discover_dismiss_clear)
    # Always-on conventions viewer: what every session receives, with provenance,
    # sliced out of the session's own producers so the viewer cannot drift from the prompt.
    app.router.add_get("/api/legibility/always-on", api_always_on)
    app.router.add_get("/api/legibility/always-on/doc", api_always_on_doc)
    app.router.add_put("/api/legibility/always-on/doc", api_always_on_doc_write)

    # Legibility — PClaw as a routed-context provider for external agents.
    # GET /api/context backs the in-process get_context tool; the per-project
    # regenerate endpoint renders marker-fenced adapters into a bound workspace_dir
    # (opt-in via legibility.context_adapters, SEL-audited).
    from personalclaw.dashboard.handlers.context import (
        api_context_get,
        api_project_context_regenerate,
    )

    app.router.add_get("/api/context", api_context_get)
    app.router.add_post(
        "/api/projects/{project_id}/context-adapters/regenerate",
        api_project_context_regenerate,
    )

    # Tasks — first-class entity with provider-based aggregation
    from personalclaw.tasks.handlers import register_task_routes

    register_task_routes(app)

    # Document comments — the annotation layer over files and artifacts. Server-side
    # because it shipped as ONE `localStorage` key, so clearing site data destroyed the
    # only copy and `personalclaw snapshot` could not carry what the server never saw —
    # while TASK comments next door were a real store the whole time (#429).
    from personalclaw.dashboard.handlers.doc_comments import register_doc_comment_routes

    register_doc_comment_routes(app)

    # Workflows — the v2 run/def API (WORKFLOWS-V2 Slice 7a) over the same
    # `workflows.service` the chat tools use, so the two surfaces cannot diverge.
    from personalclaw.workflows.handlers import register_workflow_routes

    register_workflow_routes(app)

    # The unified Loop engine — ONE /api/loops route family for every kind
    # (general/goal/code/design). Replaces the legacy /api/loops + /api/code routes
    # at the cutover (Slice 2e): the legacy loops/ + code/ packages are deleted.
    from personalclaw.dashboard.handlers.loop_routes import register_unified_loop_routes

    register_unified_loop_routes(app)

    # Artifacts — first-class entity (named/versioned LLM content) over a provider
    from personalclaw.artifacts.handlers import register_artifact_routes

    register_artifact_routes(app)

    # Inbox
    app.router.add_get("/api/inbox", handlers_inbox.api_inbox_list)
    # The open COLLECTION. Distinct from `POST /api/inbox/{id}/open` below (the per-row engagement
    # signal), which is why the handler is `api_inbox_open_list` — the two names collided.
    app.router.add_get("/api/inbox/open", handlers_inbox.api_inbox_open_list)
    app.router.add_get("/api/inbox/kinds", handlers_inbox.api_inbox_kinds)
    # The owner census behind the shared inbox's per-owner filter chips. A literal
    # segment, registered beside `kinds` and before any dynamic `{id}` route, for the same
    # shadowing reason called out below.
    app.router.add_get("/api/inbox/owners", handlers_inbox.api_inbox_owners)
    app.router.add_post("/api/inbox/seen", handlers_inbox.api_inbox_seen)
    app.router.add_get("/api/inbox/status", handlers_inbox.api_inbox_status)
    app.router.add_post("/api/inbox/restart", handlers_inbox.api_inbox_restart)
    app.router.add_post("/api/inbox/dismiss-all", handlers_inbox.api_inbox_dismiss_all)
    # The literal `proposals` and `notes` paths are registered BEFORE
    # `/api/inbox/{id}/...` so a dynamic id segment can never shadow either of them.
    app.router.add_post("/api/inbox/proposals", handlers_inbox.api_inbox_proposal_create)
    app.router.add_post("/api/inbox/notes", handlers_inbox.api_inbox_note_create)
    app.router.add_post("/api/inbox/{id}/apply", handlers_inbox.api_inbox_proposal_apply)
    app.router.add_post("/api/inbox/{id}/restore", handlers_inbox.api_inbox_restore)
    app.router.add_post("/api/inbox/{id}/pair", handlers_inbox.api_inbox_pair)
    app.router.add_post("/api/inbox/send", handlers_inbox.api_inbox_send)
    app.router.add_put("/api/inbox/{id}", handlers_inbox.api_inbox_update)
    app.router.add_post("/api/inbox/{id}/draft", handlers_inbox.api_inbox_draft)
    app.router.add_post("/api/inbox/{id}/sort", handlers_inbox.api_inbox_sort)
    app.router.add_post("/api/inbox/{id}/open", handlers_inbox.api_inbox_open)
    app.router.add_post("/api/inbox/{id}/favorite", handlers_inbox.api_inbox_favorite)
    app.router.add_get("/api/inbox/{id}/attachments/{aid}", handlers_inbox.api_inbox_attachment)
    # POST, not GET (#337). This route CREATES an inbox item and spends a model call, so a
    # browser prefetch, a retry, or a double render manufactured items — and a state-changing
    # GET also sits outside CSRF protection entirely. Registered beside `/{id}/...` above and
    # BEFORE nothing dynamic can shadow it: `digest` is a literal segment, and the dynamic
    # `/api/inbox/{id}` routes are PUT/POST on a different path shape.
    app.router.add_post("/api/inbox/digest", handlers_inbox.api_inbox_digest)
    app.router.add_get("/api/inbox/providers", handlers_inbox.api_inbox_providers)

    # Notifications (GET/clear registered in server.py's _register_mcp_routes; the rest here)
    app.router.add_delete("/api/notifications", handlers.api_notification_delete)
    app.router.add_post("/api/notifications/ack", handlers.api_notification_ack)
    app.router.add_post("/api/notifications/unack", handlers.api_notification_unack)
    app.router.add_post("/api/notifications/trust", handlers.api_notification_trust)
    app.router.add_post("/api/notifications/ack-all", handlers.api_notifications_ack_all)
    app.router.add_get("/api/update/check", handlers.api_update_check)
    app.router.add_post("/api/update/check", handlers.api_update_check_now)
    app.router.add_get("/api/changelog", handlers.api_changelog)
    app.router.add_post("/api/update", handlers.api_update_apply)
    app.router.add_post("/api/update/cancel", handlers.api_update_cancel)
    app.router.add_post("/api/update/dismiss", handlers.api_update_dismiss)
    # Restart-only (no git advance) — apply committed backend changes. GET-less:
    # ?probe=1 returns the active-work snapshot for the confirm gate.
    app.router.add_post("/api/system/restart", handlers.api_restart)
    # Only expose the simulation endpoint in dev/debug environments
    _truthy = {"1", "true", "yes", "on"}
    if (
        config_dir().name.endswith("-dev")
        or os.environ.get("PERSONALCLAW_DEV_MODE", "").lower() in _truthy
    ):
        app.router.add_post("/api/update/simulate", handlers.api_update_simulate)
    app.router.add_get("/api/sessions", handlers.api_sessions)
    app.router.add_delete("/api/sessions", handlers.api_sessions_clear)
    app.router.add_get("/api/sessions/context", handlers.api_sessions_context)
    app.router.add_get("/api/sessions/health", handlers.api_sessions_health)
    app.router.add_post("/api/sessions/restart", handlers.api_sessions_restart)
    # NOTE: /search and /recall must be registered before /{key}, or the path param catches them
    app.router.add_get("/api/sessions/search", handlers.api_sessions_search)
    app.router.add_get("/api/sessions/recall", handlers.api_sessions_recall)
    app.router.add_get("/api/sessions/{key}", handlers.api_session_detail)
    app.router.add_delete("/api/sessions/{key}", handlers.api_session_delete)
    app.router.add_get("/api/logs", handlers.api_logs)
    app.router.add_get("/api/logs/level", handlers.api_log_level_get)
    app.router.add_post("/api/logs/level", handlers.api_log_level)
    app.router.add_post("/api/sel/rotate", handlers.api_sel_rotate)
    app.router.add_get("/api/security/stats", handlers.api_security_stats)
    app.router.add_get("/api/security/denied-commands", handlers.api_security_denied_commands)
    app.router.add_get("/api/security/egress", handlers.api_security_egress)
    app.router.add_get("/api/security/outside-home", handlers.api_security_outside_home)
    # The SEL read surface: paginated + filtered + chain-verify, owner-only. Superseded
    # `/api/sel/{events,verify}` — one audit log, one way to read it.
    from personalclaw.dashboard.handlers.security_audit import register_security_audit_routes

    register_security_audit_routes(app)
    # Where credentials are stored, plus the consented snapshot-backed move between
    # stores. Owner-only for the same reason the audit surface is.
    from personalclaw.dashboard.handlers.security_credentials import (
        register_security_credential_routes,
    )

    register_security_credential_routes(app)
    # The secrets vault — presence-only reads over the same credential store, plus the
    # one-way write path. Owner-only for the same reason the two surfaces above are.
    from personalclaw.dashboard.handlers.secrets import register_secrets_routes

    register_secrets_routes(app)
    app.router.add_get("/api/approvals", handlers.api_approvals)
    app.router.add_post("/api/approvals/{id}/{action}", handlers.api_approval_resolve)

    # Local token bootstrap (file-based secret auth in handler, bypasses middleware)
    app.router.add_get("/api/token/local", handlers.api_token_local)

    # Session revocation (called by `personalclaw logout` CLI)
    app.router.add_post("/api/logout", handlers.api_logout)

    # Webhook hooks (external triggers)
    app.router.add_post("/api/hooks/agent", handlers.api_hooks_agent)
