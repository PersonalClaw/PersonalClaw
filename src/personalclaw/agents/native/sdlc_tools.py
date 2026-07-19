"""Chat-callable tools to create + launch a Code project or Goal Loop.

The chat agent collaborates with the user on the plan IN the conversation, then —
on the user's explicit go-ahead — materializes the executable entity and starts it.
The flow is deliberately two-step (the user asked for "create the final executable
version ... and start it on user's direction"):

  • ``code_project_create`` / ``goal_loop_create`` — materialize a DRAFT (status
    READY: validated, persisted, NOT running). Returns the id + a deep link.
  • ``code_project_start`` / ``goal_loop_start`` — launch a created draft (or resume),
    only when the user says go.
  • ``sdlc_status`` — read the live progress of a project/loop (the data the chat
    progress widget renders: status, stage/phase, tasks, recent activity).

Code projects and Goal Loops are both kinds of the ONE unified Loop (kind ``code`` /
``goal``) on the unified store/manager/validation — these tools build a create body
and reuse the same mapper (`_build_loop_from_body`) + launch path the HTTP API uses,
so a chat-created entity can't bypass any gate a UI-created one gets.

State access mirrors the inbox sink: these run outside an HTTP request, so they reach
the process-wide dashboard state via ``native_source.get_dashboard_state()`` and the
autonudge service via ``autonudge.get_instance()`` — the same primitives the HTTP
handlers use (``request.app['state']`` + the service singleton).

This module holds ONLY the tool logic; the ToolDefinitions + dispatch live in
``builtin_tools`` so the agent loop discovers them with the rest of the builtin set.
"""

from __future__ import annotations

import logging

from personalclaw.tool_providers.base import ToolResult

logger = logging.getLogger(__name__)


def _state():
    """The process-wide dashboard state, or None if the gateway isn't up."""
    from personalclaw.inbox_providers.native_source import get_dashboard_state

    return get_dashboard_state()


def _svc():
    """The autonudge service that drives loop workers, or None."""
    from personalclaw.autonudge import get_instance

    return get_instance()


def _agent_exists(body: dict) -> bool:
    """Mirror the HTTP layer's worker-agent resolution (loop_routes._agent_exists):
    an acp:<cli> runtime is accepted on the runtime alone; an empty agent means the
    kind default (always seeded); else the name must be in the agent pool."""
    from personalclaw.config.loader import AppConfig

    if str(body.get("provider", "")).startswith("acp:"):
        return True
    name = str(body.get("agent", ""))
    if not name:
        return True
    try:
        return name in AppConfig.load().agents
    except Exception:
        return True


async def _launch(kind: str, lid: str, deep_path: str, label: str) -> ToolResult:
    """Shared launch path for both *_start tools: re-validate a fresh start (the
    launch gate), honor the kind's launch_blocker, then run via the unified manager."""
    from personalclaw.loop import kinds, manager, store, validation
    from personalclaw.loop.loop import ACTION_SOURCE_STATES, LoopStatus

    if not store.valid_loop_id(lid):
        return ToolResult(success=False, error=f"{label}_id is required + must be a valid id.")
    loop = store.get(lid)
    if loop is None:
        return ToolResult(success=False, error=f"no {label} {lid!r}.")
    launch_warnings: list[str] = []
    if LoopStatus(loop.status) in ACTION_SOURCE_STATES["start"]:
        # A FRESH start re-validates (the launch gate) like the HTTP start action — a
        # config invalidated since create can't launch. A RESUME (paused/stagnant/…)
        # skips it: already validated at start, don't re-block on a transient.
        v = validation.validate(loop.to_dict(), agent_exists=_agent_exists(loop.to_dict()))
        if not v.can_start:
            return ToolResult(success=False, error="; ".join(v.errors) or "validation failed",
                              recovery_hints=["Fix the listed issues, then start again."])
        # Surface non-blocking warnings at the moment autonomous work (+ spend) begins —
        # the cost estimate matters MOST here, and a UI-created project started via chat
        # never saw the create-time relay. RESUME skips re-validation, so no warnings there.
        launch_warnings = list(v.warnings)
        kinds.ensure_loaded()
        strat = kinds.get_or_none(loop.kind)
        blocker = getattr(strat, "launch_blocker", None) if strat else None
        reason = blocker(loop) if blocker else None
        if reason:
            return ToolResult(success=False, error=reason,
                              recovery_hints=["A brownfield code loop needs a workspace dir; have the user pick one first."])
    elif LoopStatus(loop.status) == LoopStatus.RUNNING:
        # Already running — not an error to relay as a failure; tell the agent it's live
        # so it reassures the user + offers sdlc_status rather than implying a problem.
        return ToolResult(success=False, error=f"this {label} is already running.",
                          recovery_hints=["It's live — call sdlc_status with this id to report progress, no need to start it again."])
    elif LoopStatus(loop.status) in (LoopStatus.COMPLETE, LoopStatus.STOPPED):
        return ToolResult(success=False, error=f"this {label} already finished ('{loop.status}') — a terminal run can't be restarted.",
                          recovery_hints=["Create a new project/loop for follow-up work."])
    elif LoopStatus(loop.status) not in ACTION_SOURCE_STATES["resume"]:
        return ToolResult(success=False, error=f"can't start a {label} in '{loop.status}' state.")
    state, svc = _state(), _svc()
    if state is None or svc is None:
        return ToolResult(success=False, error="execution service unavailable (gateway not ready).")
    try:
        await manager.start(state, svc, lid)
    except Exception as exc:  # noqa: BLE001
        logger.debug("%s start failed", label, exc_info=True)
        return ToolResult(success=False, error=f"could not start {label}: {exc}")
    warn = (" ⚠ " + " ".join(launch_warnings)) if launch_warnings else ""
    return ToolResult(success=True, output=(
        f"Started {label} `{lid}` — it's now running. Watch progress at {deep_path}{lid} "
        f"or call sdlc_status with this id.{warn}"
    ))


# ── Code projects ──────────────────────────────────────────────────────────────

async def code_project_create(a: dict) -> ToolResult:
    """Create a Code project DRAFT from a plan the agent shaped with the user.

    Args: task (str, required), name?, project_kind? (greenfield|brownfield),
    entry_stage?, workspace_dir? (required for brownfield to start), stage_plan?
    (list of {stage,title,objective,exit_criteria?,tasks?}), attended?, max_cycles?,
    verify_command?, test_command?, success_criteria?. Validates like the create API;
    returns the project id + cockpit link. Does NOT start it."""
    from personalclaw.loop import store, validation
    from personalclaw.loop.code_classify import _normalize_plan
    from personalclaw.dashboard.handlers.loop_routes import _build_loop_from_body, _installed_capability_catalogs

    task = str(a.get("task", "")).strip()
    if len(task) < 12:
        return ToolResult(success=False, error="task is too vague — describe it in more detail (min 12 chars).",
                          recovery_hints=["Give a concrete SDLC task: an idea, a bugfix, a refactor, a feature."])
    project_kind = str(a.get("project_kind", "greenfield")) or "greenfield"
    # Normalize the agent-supplied stage_plan through the SAME guard the classify path
    # uses (_normalize_plan): dedupe rows by their effective downstream key (stage ||
    # title — else two same-key rows silently share one TaskList + status entry and
    # corrupt stage advancement), drop unkeyable/invalid-stage rows, and clean per-stage
    # capability ids against the installed catalogs. The chat tool bypasses Plan Review's
    # dup-guard, so without this an agent-authored plan could persist colliding /
    # garbage stage rows that the cockpit then renders as duplicate or dead rows.
    skills_cat, workflows_cat = await _installed_capability_catalogs()
    skill_ids = {str(c.get("id", "")).strip() for c in skills_cat if isinstance(c, dict)}
    workflow_ids = {str(c.get("id", "")).strip() for c in workflows_cat if isinstance(c, dict)}
    body = {
        "kind": "code",
        "task": task,
        "name": str(a.get("name") or "").strip(),
        "workspace_dir": str(a.get("workspace_dir", "")),
        # agent_names=None → don't strip a per-stage agent_name the agent set (lenient,
        # matching the classify call); stage/capability hygiene is the point here.
        "plan": _normalize_plan(a.get("stage_plan") or [], skill_ids, workflow_ids, None),
        "max_cycles": a.get("max_cycles", 60),
        "attended": bool(a.get("attended", False)),
        "success_criteria": (str(a["success_criteria"]) if a.get("success_criteria") else None),
        # Kind-config fields the code strategy owns.
        "project_kind": project_kind,
        "entry_stage": str(a.get("entry_stage", "ideation")) or "ideation",
        "verify_command": str(a.get("verify_command", "")),
        "test_command": str(a.get("test_command", "")),
    }
    # Validate as a resumable DRAFT (workspace optional at create — pickable later),
    # exactly like POST /api/loops, so a brownfield project can be saved without a dir.
    v = validation.validate(body, agent_exists=_agent_exists(body))
    if not v.can_start:
        return ToolResult(success=False, error="; ".join(v.errors) or "validation failed",
                          recovery_hints=["Fix the listed issues and call code_project_create again."])
    try:
        loop = store.create(_build_loop_from_body(body))
    except Exception as exc:  # noqa: BLE001
        logger.debug("code_project_create failed", exc_info=True)
        return ToolResult(success=False, error=f"could not create project: {exc}")
    needs_ws = project_kind == "brownfield" and not body["workspace_dir"]
    note = (" It's a brownfield project with no workspace yet — ask the user to pick the "
            "codebase directory (or pass workspace_dir) before starting." if needs_ws else "")
    # Relay non-blocking validation warnings (e.g. duplicate stages that drop at launch,
    # a not-yet-existing workspace) so the agent can flag them to the user / fix the plan
    # — they were silently discarded before, surfacing only in the FE.
    warn = (" ⚠ " + " ".join(v.warnings)) if v.warnings else ""
    return ToolResult(success=True, output=(
        f"Created Code project draft `{loop.id}` — \"{loop.name}\" "
        f"({project_kind}, entry stage {body['entry_stage']}, {len(body['plan'])} stages). "
        f"Open: /#/code/{loop.id} . Not started yet — call code_project_start when the user gives the go.{note}{warn}"
    ))


async def code_project_start(a: dict) -> ToolResult:
    """Launch a created Code project draft (or resume a paused/failed one). Args:
    project_id (str, required). Re-validates before running, like the start action."""
    return await _launch("code", str(a.get("project_id", "")).strip(), "/#/code/", "code project")


# ── Goal Loops ───────────────────────────────────────────────────────────────

# The non-code loop kinds a chat agent can spin up. Code has its own specialized tool
# (distinct args: project_kind, verify/test commands, entry_stage). Goal/general/design
# share the goal-shaped arg surface (task + sub_goals/deliverables/scope), so ONE tool
# covers them via a `kind` arg rather than three near-duplicate tools.
_LOOP_CREATE_KINDS = ("goal", "general", "design", "research")
_KIND_LABEL = {"goal": "Goal Loop", "general": "Loop", "design": "Design loop", "research": "Research loop"}


async def goal_loop_create(a: dict) -> ToolResult:
    """Create a Goal / General / Design LOOP draft from a plan the agent shaped with
    the user.

    Args: goal (str, required), kind? ('goal'|'general'|'design'|'research', default
    'goal'), name?, sub_goals? ([str]), deliverables? ([str]), scope? ([str]),
    goal_type? (goal kind only), attended?, max_cycles?, success_criteria?,
    rubric? ([str]). For kind 'research', sub_goals seed the initial subtopics and the
    loop does deep web research → a synthesized report. Returns the loop id + link.
    Does NOT start it."""
    from personalclaw.loop import store, validation
    from personalclaw.dashboard.handlers.loop_routes import _build_loop_from_body

    goal = str(a.get("goal", "")).strip()
    if len(goal) < 12:
        return ToolResult(success=False, error="goal is too vague — describe it in more detail (min 12 chars).",
                          recovery_hints=["State a concrete outcome the loop should drive toward."])
    kind = str(a.get("kind", "goal")).strip().lower() or "goal"
    if kind not in _LOOP_CREATE_KINDS:
        return ToolResult(success=False, error=f"kind must be one of {', '.join(_LOOP_CREATE_KINDS)} (code uses code_project_create).",
                          recovery_hints=["Pick goal for research/action, general for a generic iterative task, research for deep web research → a report, design for a design system."])
    body = {
        "kind": kind,
        "task": goal,
        "name": str(a.get("name") or "").strip(),
        "max_cycles": a.get("max_cycles", 30),
        "attended": bool(a.get("attended", False)),
        "success_criteria": (str(a["success_criteria"]) if a.get("success_criteria") else None),
        # Kind-config fields the goal strategy owns (ignored by general/design defaults
        # when not applicable — _build_loop_from_body layers them over the kind defaults).
        "goal_type": str(a.get("goal_type", "open_ended")) or "open_ended",
        "sub_goals": [str(s) for s in (a.get("sub_goals") or []) if str(s).strip()],
        "deliverables": [str(s) for s in (a.get("deliverables") or []) if str(s).strip()],
        "scope": [str(s) for s in (a.get("scope") or []) if str(s).strip()],
        "rubric": [str(c) for c in (a.get("rubric") or []) if str(c).strip()],
    }
    # A chat-created loop skips the LLM classify pass (the agent already shaped the plan
    # in conversation), so a Design loop would have NO phased breakdown — violating the
    # vision's "break into phased executions". Seed the design kind's deterministic
    # canonical phases (the same fallback classify uses when the LLM is unavailable) so a
    # chat-created Design loop always has its real phase plan. (Goal advances by cycles;
    # general self-phases at run — neither needs a pre-seeded plan here.)
    if kind == "design":
        from personalclaw.loop import kinds as _kinds
        _kinds.ensure_loaded()
        _strat = _kinds.get_or_none("design")
        _phases = _strat.default_phases() if _strat and hasattr(_strat, "default_phases") else []
        if _phases:
            body["plan"] = _phases
            body["kind_config"] = {"design_steps": [p["title"] for p in _phases]}
    # Validate before persisting — same gate the HTTP create + code_project_create use,
    # so a chat-created loop can't bypass the checks (bad goal_type, over-cap cycles, a
    # screened verify command) that a UI-created one gets.
    v = validation.validate(body, agent_exists=_agent_exists(body))
    if not v.can_start:
        return ToolResult(success=False, error="; ".join(v.errors) or "validation failed",
                          recovery_hints=["Fix the listed issues and call goal_loop_create again."])
    try:
        loop = store.create(_build_loop_from_body(body))
    except Exception as exc:  # noqa: BLE001
        logger.debug("goal_loop_create failed", exc_info=True)
        return ToolResult(success=False, error=f"could not create {kind} loop: {exc}")
    n_sub = len(loop.kind_config.get("sub_goals", []) or [])
    label = _KIND_LABEL.get(kind, "Loop")
    # Goal's defining trait is its goal_type; general/design lead with their phase plan.
    detail = (f"{loop.kind_config.get('goal_type', 'open_ended')}, {n_sub} sub-goals"
              if kind == "goal" else f"{len(loop.plan or [])} phases")
    # Relay non-blocking validation warnings (e.g. the high-cycle-count COST estimate,
    # a not-yet-existing workspace) so the agent can flag them to the user — they were
    # silently discarded before, surfacing only in the FE. Mirrors code_project_create.
    warn = (" ⚠ " + " ".join(v.warnings)) if v.warnings else ""
    return ToolResult(success=True, output=(
        f"Created {label} draft `{loop.id}` — \"{loop.name}\" "
        f"({detail}). Open: /#/loops/{loop.id} . "
        f"Not started yet — call goal_loop_start when the user gives the go.{warn}"
    ))


async def goal_loop_start(a: dict) -> ToolResult:
    """Launch a created Goal / General / Design Loop draft (or resume one). Args:
    loop_id (str, required). The kind is read from the stored loop."""
    from personalclaw.loop import store
    lid = str(a.get("loop_id", "")).strip()
    loop = store.get(lid) if store.valid_loop_id(lid) else None
    kind = loop.kind if loop else "goal"
    return await _launch(kind, lid, "/#/loops/", _KIND_LABEL.get(kind, "loop").lower())


# ── Unified Project tools (the agent-facing surface) ─────────────────────────────
# A Project is the uber work-unit; a loop is one KIND of project-scoped autonomous
# run. The agent operates everything through ONE cohesive ``project_*`` set whose
# ``kind`` selects code (SDLC stage-gated) vs goal/general/design/research. These
# wrap the SAME validated create/launch/status bodies above (no gate bypass); they
# exist so the tool vocabulary matches the current Project+Loop architecture rather
# than the old split code_project_* / goal_loop_* names.
_PROJECT_KINDS = ("code", "goal", "general", "design", "research")


async def project_create(a: dict) -> ToolResult:
    """Create a Project (an autonomous, multi-cycle run) of any kind from a plan you
    shaped with the user. Does NOT start it — call project_start on the user's go.

    Args: kind (str: 'code'|'goal'|'general'|'design'|'research'), task (str, required,
    12+ chars — the goal/work), name?, optional project_id (bind under an existing
    Project container), attended?, max_cycles?, success_criteria?.
      • kind 'code' (SDLC work in a codebase): project_kind? (greenfield|brownfield),
        entry_stage?, workspace_dir? (brownfield needs one to start), stage_plan?
        ([{stage,title,objective,exit_criteria?,tasks?}]), verify_command?, test_command?.
      • kind 'goal'|'general'|'design'|'research': sub_goals? ([str]), deliverables?
        ([str]), scope? ([str]), goal_type? (goal only), rubric? ([str]).
    """
    kind = str(a.get("kind", "")).strip().lower()
    if kind not in _PROJECT_KINDS:
        return ToolResult(success=False, error=f"kind must be one of {', '.join(_PROJECT_KINDS)}.",
                          recovery_hints=["'code' for SDLC work in a codebase; 'goal' for research/action toward "
                                          "an outcome; 'research' for deep web research → a report; 'design' for a "
                                          "design system; 'general' for a generic iterative task."])
    if kind == "code":
        return await code_project_create(a)
    # goal/general/design/research share the goal-shaped surface; goal_loop_create
    # reads `goal`, so map task→goal for a uniform `task` arg across all kinds.
    body = dict(a)
    body.setdefault("goal", str(a.get("task", "")).strip())
    return await goal_loop_create(body)


async def project_start(a: dict) -> ToolResult:
    """Launch a created Project (any kind), or resume a paused/failed one. Args:
    project_id (str, required). The kind is read from the stored project."""
    from personalclaw.loop import store
    pid = str(a.get("project_id", "")).strip()
    loop = store.get(pid) if store.valid_loop_id(pid) else None
    kind = loop.kind if loop else "goal"
    deep = "/#/code/" if kind == "code" else "/#/loops/"
    label = "code project" if kind == "code" else _KIND_LABEL.get(kind, "loop").lower()
    return await _launch(kind, pid, deep, label)


async def project_status(a: dict) -> ToolResult:
    """Read live progress of any Project — status, stage/phase progress, cycles,
    latest finding, and any blocker/needs-input. Args: project_id (str, required)."""
    return await sdlc_status({"id": str(a.get("project_id", "")).strip()})


async def project_list(a: dict) -> ToolResult:
    """List the user's Projects (autonomous runs) with their kind + live status, so
    you can find one to report on or resume. Args: optional kind (filter), limit
    (int, default 25). Newest first."""
    from personalclaw.loop import store
    kind_filter = str(a.get("kind", "")).strip().lower()
    try:
        limit = max(1, int(a.get("limit", 25) or 25))
    except (ValueError, TypeError):
        limit = 25
    try:
        rows = store.list_redacted(kind=kind_filter if kind_filter in _PROJECT_KINDS else "")
    except Exception:
        logger.debug("project_list failed", exc_info=True)
        return ToolResult(success=False, error="could not list projects (execution service unavailable).")
    if not rows:
        return ToolResult(success=True, output="(no projects yet — use project_create to start one)")
    rows = rows[:limit]
    body = "\n".join(
        f"- [{r.get('kind', '?')}] {r.get('name') or r.get('id')} (id={r.get('id')}) — {r.get('status', '?')}"
        for r in rows
    )
    return ToolResult(success=True, output=f"{len(rows)} project(s):\n{body}")


# ── shared status read (powers the chat progress widget) ────────────────────────

async def sdlc_status(a: dict) -> ToolResult:
    """Read the live progress of any loop — Code project, Goal, General, or Design.
    Args: id (str, required). Returns a compact status summary (status, stage/phase
    progress, recent findings) the agent can relay — the same data the in-chat
    progress widget renders."""
    cid = str(a.get("id", "")).strip()
    if not cid:
        return ToolResult(success=False, error="id is required (a loop id).")
    from personalclaw.loop import store

    if not store.valid_loop_id(cid):
        return ToolResult(success=False, error=f"no loop with id {cid!r}.")
    red = store.get_redacted(cid)
    if red is None:
        return ToolResult(success=False, error=f"no loop with id {cid!r}.")
    kind = red.get("kind", "goal")
    status = red.get("status", "?")
    findings = red.get("findings", []) or []
    # Goal is the only sub-goal-driven kind (advances by cycles, no per-step done-state).
    # code/general/design are all phase-planned (plan[] + phase_status{}) — report real
    # stage progress for them, not the empty sub_goals that mislabels them "open-ended".
    if kind == "goal":
        subs = (red.get("kind_config", {}) or {}).get("sub_goals", []) or []
        prog = f"{len(subs)} sub-goals" if subs else "open-ended"
    else:
        stages = red.get("plan", []) or []
        done = sum(1 for s in (red.get("phase_status") or {}).values() if s == "done")
        prog = f"{done}/{len(stages)} {'stages' if kind == 'code' else 'phases'} done" if stages else "no phase plan"
    last = findings[-1].get("summary", "") if findings else ""
    # When the run is parked ON the user, the WHY is the whole point of asking for
    # status — surface it so the agent can relay what to do, not just "it's waiting".
    # needs_input → the pending question (+ its rationale); blocked/failed → the
    # persisted reason (the stall/gate explanation). Bare status word alone left the
    # agent unable to tell the user what the worker actually needs.
    waiting = ""
    if status == "needs_input":
        pq = red.get("pending_question") or {}
        q = str(pq.get("question") or "").strip()
        why = str(pq.get("why") or "").strip()
        if q:
            waiting = f"⚠ Needs your input: {q[:300]}" + (f" (why: {why[:200]})" if why else "") + " "
        else:
            waiting = "⚠ Waiting on your input. "
    elif status in ("blocked", "failed", "stagnant"):
        reason = str(red.get("error_message") or "").strip()
        if reason:
            waiting = f"⚠ {status.capitalize()}: {reason[:300]} "
    elif status == "complete" and str(red.get("error_message") or "").strip():
        # A 'complete' run carrying an error_message finished NON-genuinely (cycle budget
        # exhausted before all stages cleared) — the FE shows this as "Ended early", so
        # relay the distinction here too instead of a misleading bare "complete". Mirrors
        # effectiveLoopStatus on the chat card / list / cockpit.
        waiting = f"⚠ Ended early (didn't fully finish): {str(red.get('error_message')).strip()[:300]} "
    # Emit the cockpit deep-link so the in-chat progress widget (sdlcRefFromTool)
    # recognizes this segment + renders the live auto-refreshing card — same as the
    # create/start tools. The FE still routes code kinds under /#/code and the rest
    # under /#/loops (unified front door lands in step 10).
    link = f"/#/{'code' if kind == 'code' else 'loops'}/{cid}"
    return ToolResult(success=True, output=(
        f"{kind} `{cid}` — status: {status}; {prog}; {len(findings)} cycles. "
        + waiting
        + (f"Latest: {last[:200]} " if last else "No findings yet. ")
        + f"Open: {link}"
    ))
