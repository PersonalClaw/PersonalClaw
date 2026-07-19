"""Loop ↔ Tasks integration — provision the backing Tasks Project + per-phase
TaskLists for a loop, seed/decompose the planner's tasks, and reconcile them.

The unified version of the legacy ``code/tasks_link.py`` (and the goal intake's
task creation), operating on the :class:`personalclaw.loop.loop.Loop` entity:
phases come from ``loop.plan`` keyed by the kind strategy's ``phase_key`` (SDLC
stage for code, title for goal/design), and the parallel-execution queue lives in
``kind_config['queued_task_ids']``. Drives the REAL Tasks system — no parallel
store. Never raises into the engine (Tasks-store failures are logged; the loop
proceeds).
"""

from __future__ import annotations

import logging

from personalclaw.loop import kinds, store
from personalclaw.loop.loop import Loop

logger = logging.getLogger(__name__)


def _phase_key(loop: Loop, phase: dict) -> str:
    """The stable key for a plan phase, via the loop's kind strategy (stage-or-title
    for code, title for goal/design). Falls back to a plain stage/title read if the
    kind has no registered strategy (defensive)."""
    strat = kinds.get_or_none(loop.kind)
    if strat is not None:
        return strat.phase_key(phase)
    return (str(phase.get("stage", "")).strip() or str(phase.get("title", "")).strip())


def _str_list(raw) -> list[str]:
    """Clean a task spec's list-of-strings field; a bare string is WRAPPED, not
    iterated (else it char-shreds)."""
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, list):
        raw = []
    return [s for s in raw if isinstance(s, str) and s.strip()]


def _hierarchy():
    from personalclaw.tasks.hierarchy import HierarchyStore
    return HierarchyStore()


def _is_done(status) -> bool:
    return getattr(status, "value", status) in ("done", "completed")


def _is_resolved(status) -> bool:
    """Terminal (done/completed/cancelled) — a cancelled blocker resolves its
    dependents (matches the canonical task graph + the cockpit). C432."""
    return getattr(status, "value", status) in ("done", "completed", "cancelled")


def _loop_project_name(loop: Loop) -> str:
    return (loop.name or loop.task or "Loop")[:80].strip()


def ensure_project(loop: Loop) -> str:
    """Resolve the containing Project this loop scopes under; return its id.
    Precedence: an already-resolved ``tasks_project_id`` (a prior launch) → the user's
    explicitly chosen ``project_id`` (the composer's ProjectPicker scope — e.g. nest
    this code work under "Website Redesign") → a fresh auto-named project. Without the
    project_id fallback the user's deliberate scoping was dropped: the loop spawned a
    new auto-project instead of nesting its per-stage TaskLists under the chosen one.
    Idempotent."""
    from personalclaw import projects as projects_svc
    chosen = loop.tasks_project_id or loop.project_id
    return projects_svc.resolve_project_id(chosen, auto_name=_loop_project_name(loop))


def ensure_phase_lists(loop: Loop, tasks_project_id: str) -> dict:
    """Find-or-create one TaskList per plan phase under the Tasks Project; return a
    ``{phase_key: task_list_id}`` map. Idempotent for the SAME loop (re-launch keeps
    the links it already persisted; ``key in links`` short-circuits).

    Phase lists are namespaced PER-LOOP, not just per project. A project can host many
    loops, and several Code loops share the canonical SDLC phase names (Implementation,
    Verification…). Matching existing lists by bare name (the old behavior) made a NEW
    loop's 'Implementation' phase silently REUSE a PRIOR loop's 'Implementation'
    TaskList under the same project — so the new loop inherited the old loop's tasks
    (e.g. a 'write a README' loop picked up a sibling app-build loop's Scaffold/AI/UI
    tasks, which then blocked its gate forever). The per-loop suffix keeps each loop's
    phases isolated while staying human-readable in the project's Tasks view."""
    h = _hierarchy()
    existing_by_name = {tl.name: tl.id for tl in h.list_task_lists(project_id=tasks_project_id)}
    links: dict = dict(loop.task_list_ids or {})
    # Short, stable per-loop tag so two loops' same-named phases don't collide. Loop ids
    # are uuid4().hex[:8] — already short; a leading '#' reads as a label, not a path.
    loop_tag = f" #{loop.id}"
    for phase in (loop.plan or []):
        if not isinstance(phase, dict):
            continue
        key = _phase_key(loop, phase)
        if not key or key in links:
            continue
        base_name = (str(phase.get("task_list_name", "")).strip()
                     or str(phase.get("title", "")).strip() or key.title())
        list_name = f"{base_name}{loop_tag}"
        if list_name in existing_by_name:
            links[key] = existing_by_name[list_name]
            continue
        tl = h.create_task_list(name=list_name, project_id=tasks_project_id)
        links[key] = tl.id
        existing_by_name[list_name] = tl.id
    return links


async def decompose_sub_goals(loop_id: str) -> list[str]:
    """Turn a goal loop's approved sub-goals into linked, trackable Tasks — the modern
    replacement for the dropped ``/decompose`` endpoint, run from the goal walkthrough's
    finalize. Creates (idempotently) the backing Tasks Project + a single 'Sub-goals'
    list, materializes each ``kind_config.sub_goals`` entry as a native Task, and records
    the ids in ``linked_task_ids`` (so the brief's 'tracked tasks' block renders + the
    completion reconcile closes them). No-op if already linked or no sub-goals. Returns
    the created task ids. Never raises — Tasks failures are logged + return []."""
    loop = store.get(loop_id)
    if loop is None or loop.linked_task_ids:
        return []
    sub_goals = [str(s).strip() for s in (loop.kind_config or {}).get("sub_goals", []) if str(s).strip()]
    if not sub_goals:
        return []
    try:
        from personalclaw import projects as projects_svc
        from personalclaw.tasks import registry
        tasks_project_id = ensure_project(loop)
        # Only auto-name a project WE created — never rename the user's explicitly
        # chosen Project (project_id from the composer's ProjectPicker), which is
        # unlocked by default and would otherwise be clobbered to the loop's name.
        if not (loop.tasks_project_id or loop.project_id):
            projects_svc.maybe_rename_from(tasks_project_id, _loop_project_name(loop))
        h = _hierarchy()
        existing = {tl.name: tl.id for tl in h.list_task_lists(project_id=tasks_project_id)}
        list_id = existing.get("Sub-goals") or h.create_task_list(name="Sub-goals", project_id=tasks_project_id).id
        created: list[str] = []
        for sg in sub_goals:
            task = await registry.create_task(provider_name="native", title=sg, task_list_id=list_id)
            created.append(task.id)
        store.set_tasks_links(loop_id, tasks_project_id=tasks_project_id,
                              task_list_ids={**(loop.task_list_ids or {}), "sub_goals": list_id})
        store.link_tasks(loop_id, created)
        return created
    except Exception:
        logger.warning("sub-goal decompose failed for loop %s", loop_id, exc_info=True)
        return []


def provision(loop_id: str) -> Loop | None:
    """Provision the Tasks Project + per-phase TaskLists for a loop, persist the
    links. The manager calls this once at launch. Returns the updated loop, or None
    if missing. Never raises — Tasks failures are logged + the loop returned as-is."""
    loop = store.get(loop_id)
    if loop is None:
        return None
    try:
        from personalclaw import projects as projects_svc
        tasks_project_id = ensure_project(loop)
        # Only auto-name a project WE created — never rename the user's chosen Project.
        if not (loop.tasks_project_id or loop.project_id):
            projects_svc.maybe_rename_from(tasks_project_id, _loop_project_name(loop))
        links = ensure_phase_lists(loop, tasks_project_id)
        return store.set_tasks_links(loop_id, tasks_project_id=tasks_project_id, task_list_ids=links)
    except Exception:
        logger.warning("Tasks provisioning failed for loop %s", loop_id, exc_info=True)
        return loop


def phase_list_id(loop: Loop, phase_key: str) -> str:
    return str((loop.task_list_ids or {}).get(phase_key, ""))


async def resolved_stage_task_count(loop: Loop, phase_key: str) -> int:
    """How many of a stage's tasks are terminal (done/completed/cancelled) right now.
    A monotonically-rising count across cycles is the ground-truth signal that the
    stage is making real forward progress (each module task completing), used to keep
    the anti-spin stall guard from false-pausing a working multi-task stage. Never
    raises — returns 0 on any error (the caller treats 0 as 'no observable progress')."""
    try:
        list_id = phase_list_id(loop, phase_key)
        if not list_id:
            return 0
        from personalclaw.tasks import registry
        tasks, _ = await registry.list_all_tasks(task_list_id=list_id, limit=500)
        return sum(1 for t in tasks if _is_resolved(t.status))
    except Exception:
        logger.debug("resolved_stage_task_count failed for %s phase %s", loop.id, phase_key, exc_info=True)
        return 0


async def ready_queued_tasks(loop: Loop, phase_key: str) -> list:
    """Queued, not-terminal, not-running tasks in a phase whose deps are all
    resolved — what the scheduler runs next. Queue order. Never raises."""
    try:
        list_id = phase_list_id(loop, phase_key)
        if not list_id:
            return []
        from personalclaw.tasks import registry
        tasks, _ = await registry.list_all_tasks(task_list_id=list_id, limit=500)
        by_id = {t.id: t for t in tasks}
        resolved_ids = {t.id for t in tasks if _is_resolved(t.status)}
        queued = set((loop.kind_config or {}).get("queued_task_ids", []) or [])

        def is_ready(t) -> bool:
            if t.id not in queued:
                return False
            if _is_resolved(t.status) or getattr(t.status, "value", t.status) == "in_progress":
                return False
            deps = [d.depends_on_task_id for d in (t.dependencies or [])]
            return all(d in resolved_ids or d not in by_id for d in deps)

        ready = [t for t in tasks if is_ready(t)]
        order = {tid: i for i, tid in enumerate((loop.kind_config or {}).get("queued_task_ids", []) or [])}
        ready.sort(key=lambda t: order.get(t.id, 1_000_000))
        return ready
    except Exception:
        logger.debug("ready_queued_tasks failed for %s phase %s", loop.id, phase_key, exc_info=True)
        return []


async def mark_task_done(task_id: str) -> bool:
    from personalclaw.tasks import registry
    try:
        await registry.update_task(task_id, provider_name="native", status="done")
        return True
    except Exception:
        return False


async def seed_phase_tasks(loop_id: str) -> int:
    """Materialize the planner's per-phase ``tasks`` into each phase's TaskList — the
    loop's upfront 'final set of tasks'. Idempotent: only seeds an EMPTY list, so a
    re-launch never duplicates. Returns the count created. Never raises."""
    from personalclaw.tasks import registry
    loop = store.get(loop_id)
    if loop is None:
        return 0
    created = 0
    for phase in (loop.plan or []):
        if not isinstance(phase, dict):
            continue
        key = _phase_key(loop, phase)
        specs = [t for t in (phase.get("tasks") or []) if isinstance(t, dict) and str(t.get("title", "")).strip()]
        if not key or not specs:
            continue
        list_id = phase_list_id(loop, key)
        if not list_id:
            continue
        try:
            existing, _ = await registry.list_all_tasks(task_list_id=list_id, limit=1)
            if existing:
                continue
            created += len(await decompose_phase(loop_id, key, specs))
        except Exception:
            logger.debug("seed_phase_tasks failed for phase %r of %s", key, loop_id, exc_info=True)
    return created


async def decompose_phase(loop_id: str, phase_key: str, tasks: list[dict]) -> list[str]:
    """Create ``tasks`` as native Tasks under a phase's TaskList, materializing the
    rich planning fields + resolving ``depends_on`` (0-based backward indices within
    the ORIGINAL spec list) into a typed dependency DAG. Returns created ids in
    order. Ported faithfully from code.tasks_link.decompose_stage (incl. the
    index-stability + bare-scalar/forward/self-edge guards)."""
    loop = store.get(loop_id)
    if loop is None:
        return []
    list_id = phase_list_id(loop, phase_key)
    if not list_id:
        return []
    from personalclaw.tasks import registry
    created: list[str] = []
    by_orig: dict[int, str] = {}
    spec_by_orig: dict[int, dict] = {}
    for orig_idx, spec in enumerate(tasks):
        if not isinstance(spec, dict):
            continue
        title = str(spec.get("title", "")).strip()
        if not title:
            continue
        task = await registry.create_task(
            provider_name="native", title=title,
            description=str(spec.get("description", "")),
            priority=str(spec.get("priority", "medium")),
            task_list_id=list_id,
            action_plan=_str_list(spec.get("action_plan")),
            exit_criteria=_str_list(spec.get("exit_criteria")),
        )
        created.append(task.id)
        by_orig[orig_idx] = task.id
        spec_by_orig[orig_idx] = spec
    for orig_idx, spec in spec_by_orig.items():
        raw_deps = spec.get("depends_on")
        if isinstance(raw_deps, (int, float, str)):
            raw_deps = [raw_deps]
        elif not isinstance(raw_deps, list):
            raw_deps = []
        dep_ids = [
            by_orig[j] for j in raw_deps
            if isinstance(j, int) and not isinstance(j, bool) and j != orig_idx and j < orig_idx and j in by_orig
        ]
        if dep_ids:
            try:
                await registry.update_task(by_orig[orig_idx], provider_name="native", depends_on=dep_ids)
            except Exception:
                logger.debug("decompose_phase: failed to set deps for %s", by_orig[orig_idx], exc_info=True)
    return created


async def reconcile_phase_done(loop_id: str, phase_key: str) -> int:
    """Mark every not-done task in a completed phase's TaskList done (the watchdog's
    gate is the authority; the worker is only asked to keep tasks honest). Forces
    past unmet exit criteria (the supervisor already judged the phase met). Returns
    the count closed; never raises."""
    try:
        loop = store.get(loop_id)
        if loop is None:
            return 0
        list_id = phase_list_id(loop, phase_key)
        if not list_id:
            return 0
        from personalclaw.tasks import registry
        tasks, _ = await registry.list_all_tasks(task_list_id=list_id, limit=500)
        closed = 0
        for t in tasks:
            if getattr(t.status, "value", t.status) in ("done", "completed"):
                continue
            try:
                await registry.update_task(t.id, status="done")
                closed += 1
            except ValueError:
                try:
                    met = [{**c, "status": "complete", "met": True}
                           for c in (getattr(t, "exit_criteria", None) or [])]
                    await registry.update_task(t.id, exit_criteria=met, status="done")
                    closed += 1
                except Exception:
                    logger.debug("reconcile_phase_done: couldn't force-close %s", t.id, exc_info=True)
            except Exception:
                logger.debug("reconcile_phase_done: update failed for %s", t.id, exc_info=True)
        return closed
    except Exception:
        logger.warning("reconcile_phase_done failed for %s phase %s", loop_id, phase_key, exc_info=True)
        return 0


async def teardown_tasks(loop_id: str) -> int:
    """Delete the backing Tasks Project (lists + tasks) for a loop being deleted.
    Must run BEFORE the loop store row is deleted. Returns the task count removed;
    never raises."""
    try:
        loop = store.get(loop_id)
        if loop is None:
            return 0
        from personalclaw.tasks import registry
        removed = 0
        for list_id in (loop.task_list_ids or {}).values():
            if not list_id:
                continue
            tasks, _ = await registry.list_all_tasks(task_list_id=str(list_id), limit=500)
            for t in tasks:
                if await registry.delete_task(t.id):
                    removed += 1
        # Delete the backing Tasks Project ONLY when the loop OWNS it (auto-created
        # because the user supplied no project_id). When the user scoped the loop under
        # an existing Project (project_id set → ensure_project nested it there, so
        # tasks_project_id == project_id), that Project is SHARED — deleting it would
        # destroy the user's project + any other work under it. In that case just drop
        # this loop's own TaskLists (above) and leave the Project. Defaults are never
        # deleted regardless.
        h = _hierarchy()
        if loop.tasks_project_id and not loop.project_id:
            h.delete_project(loop.tasks_project_id)
        elif loop.tasks_project_id and loop.project_id:
            # Shared user Project: remove only this loop's per-phase TaskLists, not the
            # Project itself (its other loops/chats/tasks must survive).
            for list_id in (loop.task_list_ids or {}).values():
                if list_id:
                    try:
                        h.delete_task_list(str(list_id))
                    except Exception:
                        logger.debug("teardown: drop task list %s failed", list_id, exc_info=True)
        return removed
    except Exception:
        logger.warning("teardown_tasks failed for %s", loop_id, exc_info=True)
        return 0
