"""HTTP handlers for the unified Loop engine — ``/api/loops`` with ``kind``.

The one route family for every loop kind (general/goal/code/design). The body
carries ``kind`` + the shared spine fields; kind-specific fields fold into
``kind_config`` (goal_type/granularity/sub_goals/rubric…; entry_stage/project_kind/
verify_command/test_command…) defaulting from the kind strategy. Operates on the
unified store/manager/watchdog.

Registered at the cutover (Slice 2e) in place of the legacy loops + code routes;
kept unregistered + import-clean until then so there is never a dual LIVE path.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from personalclaw.config.loader import AppConfig
from personalclaw.loop import kinds, manager, store, validation
from personalclaw.loop.loop import ACTION_SOURCE_STATES, KINDS, Loop, LoopStatus
from personalclaw.loop.watchdog import registry_key

logger = logging.getLogger(__name__)


# ── shared helpers ──


def _as_list(v) -> list:
    return v if isinstance(v, list) else []


async def _json_body(request: web.Request) -> dict | web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    return body


def _agent_exists(body: dict) -> bool:
    """True if the chosen worker agent resolves. A loop worker is either a native
    agent (``agent`` = a key in the agent pool) or a discovered ACP agent bound to an
    ``acp:<cli>`` runtime (accepted on the runtime alone — ``provider_agent`` is
    optional, e.g. claude-code's effort rungs leave it empty). An empty ``agent``
    means the kind's default worker (always seeded), applied at launch."""
    if str(body.get("provider", "")).startswith("acp:"):
        return True
    name = str(body.get("agent", ""))
    if not name:
        return True
    try:
        return name in AppConfig.load().agents
    except Exception:
        return True  # don't block on a config-load hiccup


# Top-level spine fields a create body may set directly on the Loop.
_SPINE_FIELDS = (
    "summary",
    "intake_rigor",
    "execution",
    "agent",
    "model",
    "provider",
    "provider_agent",
    "reasoning_effort",
    "strategy_id",
    "workspace_dir",
)
# Fields that, when present in the body, fold into kind_config (the kind owns them).
_KIND_CONFIG_FIELDS = (
    "goal_type",
    "granularity",
    "sub_goals",
    "deliverables",
    "scope",
    "rubric",
    "ratchet_mode",
    "verify_command",
    "test_command",
    "entry_stage",
    "project_kind",
    "token_overrides",
    "targets",
    "exports",
)


def _build_loop_from_body(body: dict) -> Loop:
    """Map a create body → a Loop, folding kind-specific fields into kind_config on
    top of the kind's defaults. ``task`` accepts the legacy ``goal`` alias."""
    kinds.ensure_loaded()
    kind = str(body.get("kind", "goal")).strip().lower() or "goal"
    strat = kinds.get_or_none(kind)
    task = str(body.get("task") or body.get("goal") or "")
    kc = dict(strat.default_kind_config() if strat else {})
    # A whole kind_config blob (the natural classify→create round-trip — the classify
    # result's kind_config carries fields no flat _KIND_CONFIG_FIELDS entry names, e.g.
    # goal execution_plan / code queued_task_ids) merges over the defaults FIRST; the
    # individual flat fields below then override, so a partial patch still works.
    if isinstance(body.get("kind_config"), dict):
        kc.update(body["kind_config"])
    for f in _KIND_CONFIG_FIELDS:
        if f in body:
            kc[f] = body[f]
    loop = Loop(
        id="",
        kind=kind,
        task=task,
        name=str(body.get("name") or "").strip(),
        project_id=str(body.get("project_id", "")),
        plan=[
            p for p in _as_list(body.get("plan") or body.get("stage_plan")) if isinstance(p, dict)
        ],
        roster=[r for r in _as_list(body.get("roster")) if isinstance(r, dict)],
        strategy_config=(_sc if isinstance((_sc := body.get("strategy_config")), dict) else {}),
        skill_ids=[str(s) for s in _as_list(body.get("skill_ids")) if str(s).strip()],
        workflow_ids=[str(w) for w in _as_list(body.get("workflow_ids")) if str(w).strip()],
        attended=bool(body.get("attended", False)),
        autopilot=bool(body.get("autopilot", True)),
        auto_teardown_on_complete=bool(body.get("auto_teardown_on_complete", False)),
        max_cycles=int(body.get("max_cycles", 30)),
        idle_secs=int(body.get("idle_secs", AppConfig.load().loops.default_idle_secs)),
        success_criteria=(str(body["success_criteria"]) if body.get("success_criteria") else None),
        kind_config=kc,
    )
    for f in _SPINE_FIELDS:
        if f in body and body[f] is not None:
            setattr(
                loop,
                f,
                (
                    body[f]
                    if f != "execution"
                    else ("multi_agent" if str(body[f]) == "multi_agent" else "solo")
                ),
            )
    # A loop scoped under a project runs in that project's working area: when the
    # caller didn't pick a workspace, inherit the project's bound workspace_dir so the
    # loop's outputs land alongside the project's other loops + chats (the cohesive
    # per-project context the unified model promises). Mirrors the chat create handler.
    if loop.project_id and not str(loop.workspace_dir or "").strip():
        try:
            from personalclaw.tasks.hierarchy import HierarchyStore

            proj = HierarchyStore().get_project(loop.project_id)
            pws = str(getattr(proj, "workspace_dir", "") or "").strip() if proj else ""
            if pws:
                loop.workspace_dir = pws
        except Exception:
            logger.debug("project workspace inheritance failed for loop", exc_info=True)
    # Name precedence: explicit name → the classifier's clean `title` → a word-aware
    # derivation from the raw task. The classify→create round-trip passes the whole
    # classification (which carries `title`, not `name`); without the title fallback the
    # name derived from a long/URL-y task truncates mid-prose ("There are planned roadmap
    # items on https://code.am…") instead of using the planner's human title.
    if not loop.name:
        loop.name = str(body.get("title") or "").strip() or _derive_name(task)
    return loop


def _derive_name(task: str, limit: int = 60) -> str:
    text = " ".join((task or "").split())
    if len(text) <= limit:
        return text or "Loop"
    head = text[:limit].rstrip()
    cut = head.rfind(" ")
    if cut >= limit // 2:
        head = head[:cut].rstrip()
    return head + "…"


async def _installed_capability_catalogs() -> tuple[list[dict], list[dict]]:
    """Installed skills + workflows ({id,name,description}) the classifier may rank.
    Best-effort — empty on any failure."""
    skills: list[dict] = []
    workflows: list[dict] = []
    try:
        from personalclaw.skills.marketplace import list_local_skills

        skills = [
            {
                "id": s.get("name", ""),
                "name": s.get("name", ""),
                "description": s.get("description", ""),
            }
            for s in list_local_skills()
        ]
    except Exception:
        logger.debug("skills catalog for classify failed", exc_info=True)
    try:
        from personalclaw.workflows.registry import list_all_workflows

        wfs, _ = await list_all_workflows()
        workflows = [
            {"id": w.id, "name": w.name, "description": getattr(w, "description", "")} for w in wfs
        ]
    except Exception:
        logger.debug("workflows catalog for classify failed", exc_info=True)
    return skills, workflows


# ── validate (deterministic pre-flight) ──


async def api_loop_validate(request: web.Request) -> web.Response:
    """POST /api/loops/validate — deterministic pre-flight on a create payload
    (can_start + errors/warnings + cycle/duration estimate). Kind-aware via the
    strategy's validate_config; the composer calls it before launch."""
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    result = validation.validate(body, agent_exists=_agent_exists(body))
    return web.json_response(result.to_dict())


# ── classify (intake brain) ──


async def api_loop_classify(request: web.Request) -> web.Response:
    """POST /api/loops/classify {kind, task|goal} — the kind-aware intake analyze
    pass. Dispatches to the kind strategy's classifier; returns the NORMALIZED
    classification (every field a recommendation the user overrides on Plan Review)."""
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    kinds.ensure_loaded()
    kind = str(body.get("kind", "goal")).strip().lower() or "goal"
    if kind not in KINDS:
        return web.json_response({"error": f"Unknown loop kind: {kind!r}"}, status=400)
    task = str(body.get("task") or body.get("goal") or "").strip()
    if len(task) < 12:
        return web.json_response({"error": "Task too short"}, status=400)
    # Reject an oversized paste BEFORE it hits the classifier LLM (a whole repo / binary
    # / MBs would blow up the prompt). Same cap the create validator enforces.
    if len(task) > validation._MAX_TASK_LEN:
        return web.json_response(
            {
                "error": f"Task is too large ({len(task):,} characters) — trim it to under "
                f"{validation._MAX_TASK_LEN:,} characters."
            },
            status=400,
        )
    strat = kinds.get(kind)
    from personalclaw.llm_helpers import one_shot_completion

    async def _ask(prompt: str) -> str:
        return await one_shot_completion(prompt, use_case="background")

    skills_catalog, workflows_catalog = await _installed_capability_catalogs()
    try:
        _reserved = {"personalclaw-lite", "personalclaw-goal-planner", "personalclaw-code-planner"}
        agents_catalog = [n for n in AppConfig.load().agents if n not in _reserved]
    except Exception:
        agents_catalog = []
    result = await strat.classify(
        task, _ask, skills=skills_catalog, workflows=workflows_catalog, agents=agents_catalog
    )
    result["kind"] = kind
    return web.json_response(result)


async def api_loop_grill_tree(request: web.Request) -> web.Response:
    """POST /api/loops/{id}/grill-tree — guided-decomposition intake (grill's ``tree``
    shape). Runs the memory-checked scoping pipeline over the loop's goal and returns
    PHASES of clarifying questions (2-4 phases × 2-5 questions each) that build on one
    another before the work is broken into tasks.

    This is the richer intake behind ``intake_rigor='thorough'``: unlike the flat
    ``clarifying_questions`` from classify, it groups questions into phases AND checks
    the memory store during decomposition so it doesn't re-ask what the user already
    settled (grill's headline advantage, previously unreachable — the pipeline had no
    caller). Like classify, it only COMPUTES: the FE folds the answered phases into the
    task text + persists ``{grill_phases, phase_answers}`` into ``kind_config`` at
    launch (a pre-launch ``update_spec`` replaces ``kind_config`` wholesale, so
    persisting here would be clobbered anyway — one write path, no dual state)."""
    cid = request.match_info["id"]
    loop = store.get(cid)
    if loop is None:
        return web.json_response({"error": "Not found"}, status=404)
    goal = (loop.task or "").strip()
    if len(goal) < 12:
        return web.json_response({"error": "Goal too short to decompose"}, status=400)

    from personalclaw import grill
    from personalclaw.llm_helpers import one_shot_completion

    async def _ask(prompt: str) -> str:
        return await one_shot_completion(prompt, use_case="background")

    # Wire recall to the SAME L3 seam the Memory Studio recall uses (semantic_context),
    # so the decomposition sees the agent's own memory view + can't drift from it.
    # Best-effort: a recall failure just yields un-memory-checked phases (grill swallows
    # it and continues). semantic_context is a sync call — matching the existing recall
    # handler, which calls it inline in an async handler too.
    state = request.app["state"]

    async def _recall(query: str) -> str:
        try:
            from personalclaw.dashboard.handlers.memory import _get_provider
            from personalclaw.memory_service import MemoryService

            svc = MemoryService.over_vector_store(_get_provider(state))
            return svc.semantic_context(query, cap=1500) or ""
        except Exception:
            logger.debug("grill-tree recall failed", exc_info=True)
            return ""

    # assess=False: the phases ARE the clarifying pass, so a separate assess step would
    # double-ask. save=None: nothing is settled at GENERATION time (the user hasn't
    # answered yet) — the answered phases fold into the launched loop's task instead.
    result = await grill.grill(goal, shape="tree", ask=_ask, recall=_recall, assess=False)
    return web.json_response({"phases": result.phases, "memory_hits": result.memory_hits})


# ── CRUD ──


async def api_loop_create(request: web.Request) -> web.Response:
    """POST /api/loops {kind, task|goal, …} — create a READY loop of any kind."""
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    kind = str(body.get("kind", "goal")).strip().lower() or "goal"
    if kind not in KINDS:
        return web.json_response({"error": f"Unknown loop kind: {kind!r}"}, status=400)
    task = str(body.get("task") or body.get("goal") or "").strip()
    if len(task) < 12:
        return web.json_response(
            {"error": "Task is too short — describe it in more detail."}, status=400
        )
    # Validate BEFORE persisting — this is the gate that stops a dangerous verify/test
    # command (rm -rf /, …) from ever landing in the store where the watchdog would
    # auto-run it unattended. Workspace-binding is a warning here (picked later, then
    # the launch action re-validates via launch_blocker), so a draft still creates.
    v = validation.validate(body, agent_exists=_agent_exists(body))
    if not v.can_start:
        return web.json_response({"error": "Validation failed", **v.to_dict()}, status=400)
    loop = _build_loop_from_body(body)
    created = store.create(loop)
    return web.json_response(store.get_redacted(created.id), status=201)


async def api_loop_list(request: web.Request) -> web.Response:
    """GET /api/loops[?project_id=…][?kind=…] — loops (redacted), newest first."""
    project_id = request.query.get("project_id", "").strip()
    kind = request.query.get("kind", "").strip().lower()
    return web.json_response({"loops": store.list_redacted(project_id=project_id, kind=kind)})


async def api_loop_get(request: web.Request) -> web.Response:
    cid = request.match_info["id"]
    # A malformed id is a 400 (client bug), distinct from a well-formed-but-missing
    # loop's 404 — matching every sibling endpoint (report/stream/plan-session/nudge/
    # queue). Without this, get_redacted's internal valid_loop_id guard returned None
    # for a malformed id and this endpoint alone reported it as 404.
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    view = store.get_redacted(cid)
    if view is None:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response(view)


async def api_loop_report(request: web.Request) -> web.Response:
    """GET /api/loops/{id}/report — the document deliverable + working log. ``report``
    is the kind's ongoing deliverable doc; ``log`` is the cumulative FINDINGS.md."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    # Existence check before reading the (empty-on-missing) deliverable/log files —
    # without it a nonexistent or deleted loop returned 200 {"report":"","log":""},
    # indistinguishable from a real loop that just hasn't written its docs yet. A
    # client polling a deleted loop's report would never learn it's gone. 404 to match
    # every sibling endpoint (get/action/nudge/queue).
    if store.get(cid) is None:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response({"report": store.read_deliverable(cid), "log": store.read_log(cid)})


async def api_loop_update(request: web.Request) -> web.Response:
    """PUT /api/loops/{id} — edit a pre-launch spec, or a name-only rename in any
    state (frozen spec → 409 unless it's just a name)."""
    cid = request.match_info["id"]
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    existing = store.get(cid)
    if existing is None:
        return web.json_response({"error": "Not found"}, status=404)
    # Re-screen a spec edit before persisting — mirrors the create gate so an edit
    # can't smuggle in a destructive verify/test command or a sensitive workspace
    # that create rejects. A name-only patch skips this (nothing security-relevant).
    if set(body) - {"name"}:
        edit_errs = validation.spec_edit_errors(
            body, kind=existing.kind, existing_kind_config=existing.kind_config or {}
        )
        if edit_errs:
            return web.json_response(
                {"error": " · ".join(edit_errs), "errors": edit_errs}, status=400
            )
    updated = store.update_spec(cid, body)
    if updated is None:
        # spec frozen — allow a name-only patch via rename
        if set(body) <= {"name"}:
            renamed = store.rename(cid, str(body.get("name", "")))
            return (
                web.json_response(store.get_redacted(cid))
                if renamed
                else web.json_response({"error": "Not found"}, status=404)
            )
        # workspace_dir-only re-bind: the recovery path when a brownfield workspace
        # went missing mid-run and the loop paused to NEEDS_INPUT/BLOCKED. The spec is
        # frozen, but this single field must be re-pickable or the user is stuck (the
        # launch_blocker/reaper/nudge guards prompt a re-pick with nowhere to do it).
        # Already path-validated above; rebind_workspace gates by state (non-running).
        if set(body) == {"workspace_dir"}:
            rebound = store.rebind_workspace(cid, str(body.get("workspace_dir", "")))
            if rebound is not None:
                return web.json_response(store.get_redacted(cid))
            return web.json_response(
                {"error": "Workspace can't be changed while the loop is running or finished."},
                status=409,
            )
        return web.json_response({"error": "Loop spec is frozen (already started)"}, status=409)
    return web.json_response(store.get_redacted(cid))


async def api_loop_action(request: web.Request) -> web.Response:
    """PATCH /api/loops/{id} {action: start|pause|resume|stop}."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    action = str(body.get("action", ""))
    if action not in ACTION_SOURCE_STATES:
        return web.json_response({"error": f"Unknown action: {action}"}, status=400)
    loop = store.get(cid)
    if loop is None:
        return web.json_response({"error": "Not found"}, status=404)
    if LoopStatus(loop.status) not in ACTION_SOURCE_STATES[action]:
        return web.json_response(
            {"error": f"Cannot {action} a loop in '{loop.status}' state"}, status=409
        )
    # Launch-time re-validation: a kind may block start (e.g. a brownfield code loop
    # with no bound workspace). Generic — the rule lives in the strategy, not here.
    # Only on a fresh start (resume of a paused, already-launched loop is exempt).
    if action == "start":
        kinds.ensure_loaded()
        strat = kinds.get_or_none(loop.kind)
        blocker = getattr(strat, "launch_blocker", None)
        reason = blocker(loop) if blocker else None
        if reason:
            return web.json_response({"error": reason}, status=422)
    state = request.app["state"]
    from personalclaw.autonudge import get_instance

    svc = get_instance()
    if svc is None:
        return web.json_response({"error": "autonudge unavailable"}, status=503)
    if action in ("start", "resume"):
        await manager.start(state, svc, cid)
    elif action == "pause":
        await manager.pause(state, svc, cid)
    elif action == "stop":
        await manager.stop(state, svc, cid)
    return web.json_response(store.get_redacted(cid))


async def _reap_loop_sessions(state, loop_id: str) -> None:
    """Remove a deleted loop's dashboard sessions + their on-disk transcripts: the
    worker (loop-<id>), the stepwise planner (loop-plan-<id>), and a code design
    planner (code-plan-<id>). Reuses the chat session-delete primitives so the
    in-memory _sessions entry AND the history .jsonl both go — no orphan leak."""
    from personalclaw.dashboard.chat_utils import _history_key_for
    from personalclaw.dashboard.handlers.sessions import _remove_session_for_history_key

    keys = [f"loop-{loop_id}", f"loop-plan-{loop_id}", f"code-plan-{loop_id}"]
    for k in keys:
        try:
            await _remove_session_for_history_key(state, k)  # in-memory session (+ cancels task)
        except Exception:
            logger.debug("reap: in-memory session drop failed for %s", k, exc_info=True)
        if state.conversation_log is not None:
            try:
                state.conversation_log.delete_session(_history_key_for(k))  # the .jsonl
            except Exception:
                logger.debug("reap: transcript delete failed for %s", k, exc_info=True)


async def api_loop_delete(request: web.Request) -> web.Response:
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    from personalclaw.autonudge import get_instance

    svc = get_instance()
    if svc is not None:
        try:
            await manager.teardown_for_delete(svc, cid)
        except Exception:
            logger.debug("loop teardown-for-delete failed for %s", cid, exc_info=True)
    deleted = store.delete(cid)
    # Reap the loop's dashboard sessions + their transcripts. teardown_for_delete stops
    # the autonudge WORKER loop + cleans worktrees, but the worker/planner chat sessions
    # (and their .jsonl history) lingered registered after delete — orphans that pile up
    # over create/delete churn. Drop the worker (loop-<id>), the stepwise planner
    # (loop-plan-<id>), and a code design-planner (code-plan-<id>) the same way the chat
    # session-delete does: remove the in-memory session + delete the history file.
    try:
        await _reap_loop_sessions(request.app["state"], cid)
    except Exception:
        logger.debug("loop session reap failed for %s", cid, exc_info=True)
    try:
        request.app["state"].loop_sse().publish(registry_key(cid), "deleted", {"loop_id": cid})
        request.app["state"].push_refresh("loops")
    except Exception:
        logger.debug("loop delete publish failed", exc_info=True)
    return web.json_response({"ok": deleted})


async def api_loop_nudge(request: web.Request) -> web.Response:
    """POST /api/loops/{id}/nudge {text, task_id?} — steer; resume if awaiting input."""
    cid = request.match_info["id"]
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    text = str(body.get("text", "")).strip()
    if not text:
        return web.json_response({"error": "text required"}, status=400)
    proj = store.get(cid)
    if proj is None:
        return web.json_response({"error": "Not found"}, status=404)
    from personalclaw.loop.loop import PRELAUNCH_STATUSES, TERMINAL_STATUSES

    if LoopStatus(proj.status) in TERMINAL_STATUSES:
        return web.json_response(
            {"error": f"Cannot steer a loop in '{proj.status}' state"}, status=409
        )
    # A steer only reaches a worker that's running or resumable. A pre-launch loop
    # (ready/review/intake/planning) has no worker + no cycle loop, so a nudge here is
    # orphaned — persisted with applied_cycle=null and never seen (the worker applies
    # nudges queued DURING a run, not before one). Reject it with an actionable reason
    # instead of a misleading {"ok": true}. The cockpit already hides the steer box for
    # these states (STEERABLE); this closes the same gap at the API for any other caller
    # (chat tools / direct API / a future surface). Edit the plan before launch instead.
    if LoopStatus(proj.status) in PRELAUNCH_STATUSES:
        return web.json_response(
            {
                "error": f"Can't steer a loop that hasn't started (it's '{proj.status}'). "
                "Launch it first, or edit the plan before launch."
            },
            status=409,
        )
    task_id = str(body.get("task_id", "")).strip()
    if task_id and not store.valid_task_guidance_id(task_id):
        return web.json_response({"error": "Invalid task_id"}, status=400)
    # A task-scoped steer must target a task that BELONGS to this loop. valid_task_
    # guidance_id only checks the id FORMAT — a well-formed id from another loop (or a
    # stale/deleted task) would otherwise write guidance_<id>.txt that this loop's
    # worker never reads, returned as a misleading {"ok": true} (the orphaned-steer
    # failure mode the pre-launch gate above also closes). Mirror the queue handler's
    # guard: only reject when the loop HAS provisioned lists AND the id isn't among them
    # (empty → can't-confirm-during-provision → allow, same as queue).
    if task_id:
        known = await _loop_task_ids(cid)
        if known and task_id not in known:
            return web.json_response(
                {"error": f"Unknown task id for this loop: {task_id}"}, status=400
            )
    from personalclaw.autonudge import get_instance

    svc = get_instance()
    if svc is None:
        return web.json_response({"error": "autonudge unavailable"}, status=503)
    result = await manager.nudge(request.app["state"], svc, cid, text, task_id=task_id)
    if result is None:
        return web.json_response({"error": "Not found"}, status=404)
    return web.json_response({"ok": True})


async def api_loop_stream(request: web.Request) -> web.StreamResponse:
    """GET /api/loops/{id}/stream — per-loop live SSE; replays a snapshot on connect."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    view = store.get_redacted(cid)
    if view is None:
        return web.json_response({"error": "Not found"}, status=404)
    from personalclaw.dashboard.sse import stream_response

    registry = request.app["state"].loop_sse()
    key = registry_key(cid)
    hub = registry.hub(key)
    return await stream_response(
        request, hub, on_connect=[("snapshot", view)], registry_evict=(registry, key)
    )


# ── queue / autopilot (execution drive) ──


async def _loop_task_ids(loop_id: str) -> set[str]:
    """All task ids across a loop's per-phase TaskLists. Empty (→ "can't confirm,
    ALLOW") pre-provision or on any failure, so a queue is only ever rejected when
    the loop HAS real lists AND the id isn't among them."""
    ids: set[str] = set()
    try:
        loop = store.get(loop_id)
        if loop is None:
            return ids
        from personalclaw.tasks import registry

        for list_id in (loop.task_list_ids or {}).values():
            if not list_id:
                continue
            tasks, _ = await registry.list_all_tasks(task_list_id=list_id, limit=500)
            ids.update(t.id for t in tasks)
    except Exception:
        logger.debug("loop task-id gather failed for %s", loop_id, exc_info=True)
    return ids


async def api_loop_queue(request: web.Request) -> web.Response:
    """POST /api/loops/{id}/queue {task_ids, action: queue|unqueue} — queue tasks for
    the parallel scheduler (code kind). Returns the full updated queue."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    loop = store.get(cid)
    if loop is None:
        return web.json_response({"error": "Not found"}, status=404)
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    task_ids = [str(t) for t in _as_list(body.get("task_ids")) if str(t).strip()]
    if not task_ids:
        return web.json_response({"error": "task_ids required"}, status=400)
    action = str(body.get("action", "queue"))
    if action not in ("queue", "unqueue"):
        return web.json_response({"error": f"Unknown action: {action}"}, status=400)
    from personalclaw.loop.loop import PRELAUNCH_STATUSES, TERMINAL_STATUSES

    if action == "queue":
        # A terminal loop has no scheduler — a queued task would sit forever. Reject
        # honestly (unqueue is never guarded — clearing a stale id must always work).
        if LoopStatus(loop.status) in TERMINAL_STATUSES:
            return web.json_response(
                {"error": f"Cannot queue tasks on a loop in '{loop.status}' state"}, status=409
            )
        # A pre-launch loop (ready/review/intake/planning) hasn't provisioned its
        # per-phase TaskLists yet, so _loop_task_ids is empty → the unknown-id guard
        # below ALLOWs by design (can't-confirm-during-provision). That bypass let a
        # bogus id persist into queued_task_ids pre-launch, where it becomes a landmine
        # for the scheduler at start. There are no real tasks to queue before launch
        # anyway, so reject queueing here (mirrors the nudge pre-launch gate).
        if LoopStatus(loop.status) in PRELAUNCH_STATUSES:
            return web.json_response(
                {
                    "error": f"Can't queue tasks before the loop starts (it's '{loop.status}'). "
                    "Launch it first — tasks are provisioned at launch."
                },
                status=409,
            )
        known = await _loop_task_ids(cid)
        if known:
            unknown = [t for t in task_ids if t not in known]
            if unknown:
                return web.json_response(
                    {"error": f"Unknown task id(s) for this loop: {', '.join(unknown[:5])}"},
                    status=400,
                )
    queue = (
        store.unqueue_tasks(cid, task_ids)
        if action == "unqueue"
        else store.queue_tasks(cid, task_ids)
    )
    try:
        request.app["state"].loop_sse().publish(registry_key(cid), "queued", {"loop_id": cid})
        request.app["state"].push_refresh("loops")
    except Exception:
        logger.debug("loop queue publish failed", exc_info=True)
    return web.json_response({"ok": True, "queued_task_ids": queue})


async def api_loop_autopilot(request: web.Request) -> web.Response:
    """POST /api/loops/{id}/autopilot {on: bool} — toggle the execution drive live.
    ON → the scheduler auto-queues + drives the phased plan; OFF → one-by-one."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    loop = store.get(cid)
    if loop is None:
        return web.json_response({"error": "Not found"}, status=404)
    from personalclaw.loop.loop import TERMINAL_STATUSES

    if LoopStatus(loop.status) in TERMINAL_STATUSES:
        return web.json_response(
            {"error": f"Cannot change autopilot on a loop in '{loop.status}' state"}, status=409
        )
    # Require an explicit boolean `on`. A defaulted bool(body.get("on", True)) silently
    # turned autopilot ON for a missing/malformed body — and ON is the consequential
    # direction (the system takes over driving the plan). A toggle must say which way.
    raw = body.get("on")
    if not isinstance(raw, bool):
        return web.json_response(
            {"error": "'on' must be a boolean (true to enable autopilot, false for one-by-one)"},
            status=400,
        )
    on = raw
    updated = store.set_autopilot(cid, on)
    if updated is None:
        return web.json_response({"error": "Not found"}, status=404)
    try:
        request.app["state"].loop_sse().publish(
            registry_key(cid), "autopilot", {"loop_id": cid, "on": on}
        )
        request.app["state"].push_refresh("loops")
    except Exception:
        logger.debug("loop autopilot publish failed", exc_info=True)
    return web.json_response({"ok": True, "autopilot": updated.autopilot})


# ── plan walkthrough (stepwise, gated planning) ──


def _kick_plan_advance(request: web.Request, cid: str) -> web.Response:
    """Run ONE planning walkthrough pass in the background (it spawns the planner —
    minutes), publish a refresh when it lands, return 202. The client watches the
    planner session WS (``loop-plan-<id>``) + polls the plan-session."""
    from personalclaw.autonudge import get_instance
    from personalclaw.loop import plan_walkthrough as pw

    svc = get_instance()
    if svc is None:
        return web.json_response({"error": "autonudge unavailable"}, status=503)
    state = request.app["state"]

    async def _run() -> None:
        try:
            for _ in range(2):  # design + first artifact, or advance, until gated/done
                outcome = await pw.advance_plan(state, svc, cid)
                if outcome in ("gated", "finalized", "failed"):
                    break
        except Exception:
            pw.mark_design_error(cid)
        finally:
            try:
                state.loop_sse().publish(registry_key(cid), "plan_step", {"loop_id": cid})
                state.push_refresh("loops")
            except Exception:
                logger.debug("loop plan advance publish failed", exc_info=True)

    task = asyncio.create_task(_run())
    tasks = request.app.setdefault("_loop_plan_tasks", set())
    tasks.add(task)
    task.add_done_callback(lambda t: tasks.discard(t))
    return web.json_response({"ok": True, "planning": True}, status=202)


async def api_loop_plan_session(request: web.Request) -> web.Response:
    """GET /api/loops/{id}/plan-session — the stepwise planning walkthrough state."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    # 404 a GONE loop, distinct from a live loop with no session yet ({session:null}).
    # Without this the planning walkthrough can't tell "deleted mid-plan" from "session
    # not started" — it polls the dead loop forever instead of exiting.
    if store.get(cid) is None:
        return web.json_response({"error": "Not found"}, status=404)
    session = store.read_plan_session(cid)
    return web.json_response({"session": session.to_dict() if session else None})


async def api_loop_plan_start(request: web.Request) -> web.Response:
    """POST /api/loops/{id}/plan/start — begin (or resume) the walkthrough."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    loop = store.get(cid)
    if loop is None:
        return web.json_response({"error": "Not found"}, status=404)
    from personalclaw.loop.loop import PRELAUNCH_STATUSES

    if LoopStatus(loop.status) not in PRELAUNCH_STATUSES:
        return web.json_response({"error": "Loop spec is frozen (already started)"}, status=409)
    return _kick_plan_advance(request, cid)


async def api_loop_plan_retry(request: web.Request) -> web.Response:
    """POST /api/loops/{id}/plan/retry — clear a recorded design failure + re-run the
    design pass (dynamic kinds only; an explicit user action)."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    if store.get(cid) is None:
        return web.json_response({"error": "Not found"}, status=404)
    from personalclaw.loop import plan_walkthrough as pw

    pw.clear_design_error(cid)
    return _kick_plan_advance(request, cid)


async def api_loop_plan_approve(request: web.Request) -> web.Response:
    """POST /api/loops/{id}/plan/approve {step_id} — approve a step + advance."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    step_id = str(body.get("step_id", "")).strip()
    if not step_id:
        return web.json_response({"error": "step_id required"}, status=400)
    from personalclaw.planning import session as PS

    session = store.read_plan_session(cid)
    if session is None:
        return web.json_response({"error": "No planning session"}, status=404)
    if not PS.approve_step(session, step_id):
        return web.json_response({"error": "Step not awaiting review"}, status=409)
    store.write_plan_session(session)
    return _kick_plan_advance(request, cid)


async def api_loop_plan_comment(request: web.Request) -> web.Response:
    """POST /api/loops/{id}/plan/comment {step_id, text} — comment + re-draft."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    step_id = str(body.get("step_id", "")).strip()
    if not step_id:
        return web.json_response({"error": "step_id required"}, status=400)
    text = str(body.get("text", "")).strip()
    if not text:
        return web.json_response({"error": "Comment text required"}, status=400)
    import time as _time

    from personalclaw.planning import session as PS

    session = store.read_plan_session(cid)
    if session is None:
        return web.json_response({"error": "No planning session"}, status=404)
    if not PS.comment_step(session, step_id, text, at=_time.time()):
        return web.json_response({"error": "Step not awaiting review"}, status=409)
    store.write_plan_session(session)
    return _kick_plan_advance(request, cid)


async def api_loop_plan_edit(request: web.Request) -> web.Response:
    """POST /api/loops/{id}/plan/edit {step_id, markdown} — the user directly edits a
    step artifact's body (no planner round-trip). Stays awaiting review."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    body = await _json_body(request)
    if isinstance(body, web.Response):
        return body
    step_id = str(body.get("step_id", "")).strip()
    if not step_id:
        return web.json_response({"error": "step_id required"}, status=400)
    markdown = str(body.get("markdown", ""))
    from personalclaw.planning import session as PS

    session = store.read_plan_session(cid)
    if session is None:
        return web.json_response({"error": "No planning session"}, status=404)
    if not PS.edit_artifact(session, step_id, markdown):
        return web.json_response({"error": "Step not awaiting review"}, status=409)
    store.write_plan_session(session)
    return web.json_response({"ok": True, "session": session.to_dict()})


# ── design tokens (Design kind) ──


async def api_design_default_tokens(request: web.Request) -> web.Response:
    """GET /api/design/tokens/default[?scheme=light|dark] — PersonalClaw's canonical
    default token set. Returns both the raw set (`tokens`, with {ref}s intact) + its
    JSON schema, AND a fully-RESOLVED tree (`resolved`) + ready CSS-variable block
    (`css`) for the requested scheme — the same shape as the per-loop token endpoint
    (overrides empty). This lets the Design composer preview the default design system
    (swatches, contrast) before any loop exists, reusing the cockpit's token views.
    Global (not per-loop)."""
    from personalclaw.loop import design_tokens as dt

    scheme = request.query.get("scheme", "light").strip().lower()
    if scheme not in ("light", "dark"):
        scheme = "light"
    return web.json_response(
        {
            "tokens": dt.default_tokens(),
            "schema": dt.tokens_schema(),
            "resolved": dt.resolve(),
            "css": dt.to_css_variables(scheme=scheme),
            "overrides": {},
            "scheme": scheme,
        }
    )


async def api_loop_design_tokens(request: web.Request) -> web.Response:
    """GET /api/loops/{id}/design/tokens[?scheme=light|dark] — the RESOLVED token tree
    for a design loop (defaults deep-merged with the loop's token_overrides, every
    {ref} resolved) plus a ready-to-inject CSS-variable block for the live canvas."""
    cid = request.match_info["id"]
    if not store.valid_loop_id(cid):
        return web.json_response({"error": "Invalid loop id"}, status=400)
    loop = store.get(cid)
    if loop is None:
        return web.json_response({"error": "Not found"}, status=404)
    if loop.kind != "design":
        return web.json_response({"error": "Not a design loop"}, status=400)
    scheme = request.query.get("scheme", "light").strip().lower()
    if scheme not in ("light", "dark"):
        scheme = "light"
    from personalclaw.loop import design_tokens as dt

    overrides = (
        loop.kind_config.get("token_overrides") if isinstance(loop.kind_config, dict) else {}
    )
    overrides = overrides if isinstance(overrides, dict) else {}
    return web.json_response(
        {
            "resolved": dt.resolve(overrides),
            "css": dt.to_css_variables(overrides, scheme=scheme),
            "overrides": overrides,
            "scheme": scheme,
        }
    )


def register_unified_loop_routes(app: web.Application) -> None:
    """Register the unified ``/api/loops`` routes (CRUD + lifecycle core). The
    plan-walkthrough + queue/autopilot routes register in the next sub-step. Called
    at the cutover in place of the legacy loops + code route registration."""
    app.router.add_post("/api/loops/validate", api_loop_validate)
    app.router.add_post("/api/loops/classify", api_loop_classify)
    app.router.add_post("/api/loops/{id}/grill-tree", api_loop_grill_tree)
    app.router.add_get("/api/loops", api_loop_list)
    app.router.add_post("/api/loops", api_loop_create)
    app.router.add_get("/api/loops/{id}", api_loop_get)
    app.router.add_get("/api/loops/{id}/report", api_loop_report)
    app.router.add_put("/api/loops/{id}", api_loop_update)
    app.router.add_patch("/api/loops/{id}", api_loop_action)
    app.router.add_delete("/api/loops/{id}", api_loop_delete)
    app.router.add_post("/api/loops/{id}/nudge", api_loop_nudge)
    app.router.add_post("/api/loops/{id}/queue", api_loop_queue)
    app.router.add_post("/api/loops/{id}/autopilot", api_loop_autopilot)
    app.router.add_get("/api/loops/{id}/plan-session", api_loop_plan_session)
    app.router.add_post("/api/loops/{id}/plan/start", api_loop_plan_start)
    app.router.add_post("/api/loops/{id}/plan/retry", api_loop_plan_retry)
    app.router.add_post("/api/loops/{id}/plan/approve", api_loop_plan_approve)
    app.router.add_post("/api/loops/{id}/plan/comment", api_loop_plan_comment)
    app.router.add_post("/api/loops/{id}/plan/edit", api_loop_plan_edit)
    app.router.add_get("/api/loops/{id}/stream", api_loop_stream)
    app.router.add_get("/api/design/tokens/default", api_design_default_tokens)
    app.router.add_get("/api/loops/{id}/design/tokens", api_loop_design_tokens)
