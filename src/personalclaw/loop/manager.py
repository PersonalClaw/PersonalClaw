"""Unified Loop manager — the shared lifecycle for every kind.

Owns the kind-agnostic orchestration: arm/start/resume, pause, stop, nudge,
teardown, and the startup orphan-reap. The per-kind framing (the durable brief +
the per-cycle trigger) is delegated to the loop's :class:`LoopKindStrategy`
(``build_brief`` / ``cycle_nudge``); the manager resolves the project context dir
+ tool roots and owns the file write, so the strategy stays pure.

Kept free of the dashboard + autonudge concretions (passed in as ``state`` /
``svc``) so it's unit-testable and import-cycle-free — same discipline as the
legacy loops/code managers it unifies.
"""

from __future__ import annotations

import logging

from personalclaw.config.loader import AppConfig
from personalclaw.loop import kinds, store
from personalclaw.loop.loop import Loop, LoopStatus

logger = logging.getLogger(__name__)


def session_key(loop_id: str) -> str:
    """The hidden worker session key for a loop (filtered from the chat sidebar)."""
    return f"loop-{loop_id}"


def loop_id_from_session_key(key: str) -> str:
    """Inverse of :func:`session_key` — '' if ``key`` isn't a loop worker key."""
    return key[len("loop-") :] if key.startswith("loop-") else ""


def _context_dir(loop: Loop) -> str:
    """The loop's containing-project shared context dir (or '' if none/unresolvable)."""
    if not loop.project_id:
        return ""
    try:
        from personalclaw import projects as projects_svc

        return projects_svc.context_dir(loop.project_id) or ""
    except Exception:
        logger.debug("context_dir lookup failed for %s", loop.id, exc_info=True)
        return ""


def _project_brief_block(loop: Loop) -> str:
    """The user-authored brief of the loop's containing project — the project's
    WHAT/WHY, injected as shared context for every loop scoped under it (the vision's
    'available as context for each agent working on any session or loop in that
    project'). Empty when the loop isn't project-scoped or the project has no brief."""
    if not loop.project_id:
        return ""
    try:
        from personalclaw.tasks.hierarchy import HierarchyStore

        project = HierarchyStore().get_project(loop.project_id)
        brief = (getattr(project, "brief", "") or "").strip() if project else ""
        if not brief:
            return ""
        return (
            "**Project brief** — the goal/scope/background of the project this loop "
            f"belongs to. Treat it as foundational context for everything you do:\n\n{brief}"
        )
    except Exception:
        logger.debug("project-brief lookup failed for %s", loop.id, exc_info=True)
        return ""


def _sibling_loops_block(loop: Loop) -> str:
    """A shared brief footer listing the OTHER loops on this loop's project (excluding
    self) — so a project-scoped loop worker knows what sibling loops + their outcomes
    exist in the shared context dir, mirroring the project-chat preamble's loop history.
    This is the loop-side of the vision's cohesive per-project context. Empty when the
    loop isn't project-scoped or has no siblings. Best-effort."""
    if not loop.project_id:
        return ""
    try:
        siblings = [lp for lp in store.list_for_project(loop.project_id) if lp.id != loop.id]
        if not siblings:
            return ""
        lines = [
            f"**Other loops on this project ({len(siblings)})** — their outcomes live in "
            "the shared context dir above; read them for continuity:"
        ]
        for lp in siblings[:12]:
            lines.append(f"    • [{lp.kind}] {lp.name or lp.task[:60]} — {lp.status}")
        return "\n".join(lines)
    except Exception:
        logger.debug("sibling-loops block failed for %s", loop.id, exc_info=True)
        return ""


def write_brief(loop: Loop) -> None:
    """Render the kind's brief + write it to the loop dir. The strategy builds the
    text (pure); the manager owns the file + the resolved project context dir + the
    shared sibling-loops footer (so the per-project context is symmetric with chats)."""
    d = store.loop_dir(loop.id)
    if d is None:
        return
    kinds.ensure_loaded()
    strat = kinds.get_or_none(loop.kind)
    if strat is None:
        return
    body = strat.build_brief(loop, context_dir=_context_dir(loop))
    pb = _project_brief_block(loop)
    if pb:
        # Project brief leads — it's the foundational WHAT/WHY the worker reads first.
        body = f"{pb}\n\n---\n\n{body}"
    sib = _sibling_loops_block(loop)
    if sib:
        body = f"{body}\n\n{sib}"
    store.write_brief(loop.id, body)


async def start(state, svc, loop_id: str) -> Loop:
    """Start (or resume) a loop: write the brief, arm the worker session, grant
    per-session trust, and arm the autonudge loop. Used for both ``start`` and
    ``resume`` — both transition to RUNNING + (re)arm on a fresh/idempotent worker.
    """
    loop = store.get(loop_id)
    if loop is None:
        raise KeyError(loop_id)
    kinds.ensure_loaded()
    strat = kinds.get_or_none(loop.kind)
    if strat is None:
        raise ValueError(f"no strategy for loop kind {loop.kind!r}")

    # Provision the backing Tasks Project + per-phase TaskLists, then seed the
    # planner's tasks (idempotent — a resume re-provisions harmlessly + never
    # re-seeds a non-empty list). The loop's plan becomes real, trackable Tasks.
    # GATED by kind: only task-driven kinds (code; design later) provision at launch.
    # goal/general are NOT task-driven — the legacy goal engine never auto-created a
    # Tasks Project at start (sub-goals become Tasks only via an explicit user
    # decompose), so provisioning every goal loop would spawn an unwanted Project +
    # empty per-sub-goal TaskLists the user never asked for.
    if getattr(strat, "provisions_tasks", False):
        from personalclaw.loop import tasks_link

        provisioned = tasks_link.provision(loop_id)
        if provisioned is not None:
            loop = provisioned
        try:
            await tasks_link.seed_phase_tasks(loop_id)
            loop = store.get(loop_id) or loop
        except Exception:
            logger.debug("seed_phase_tasks failed for %s", loop_id, exc_info=True)

    write_brief(loop)
    updated = store.update_status(loop_id, LoopStatus.RUNNING)

    d = store.loop_dir(loop_id)
    cfg = AppConfig.load().loops

    session = state.get_or_create_session(
        name=session_key(loop_id),
        agent=loop.agent or strat.default_agent,
        model=loop.model,
        workspace_dir=loop.workspace_dir,
        app="loop",  # hidden worker — filtered from the chat sidebar
        # Scope the worker's artifacts to the loop's Project (S5): a resolved
        # tasks_project_id (the backing Tasks Project) else the chosen project_id.
        project_id=loop.tasks_project_id or loop.project_id or "",
    )
    store.set_session_key(loop_id, session.key)

    # The loop's engine files live in the loop dir (≠ the worker cwd when a
    # workspace is bound); grant the loop dir + the project context dir as extra
    # native-tool roots so the worker can read its brief / write findings / share
    # durable project context.
    extra_roots = [str(d)] if d is not None else []
    ctx = _context_dir(loop)
    if ctx:
        extra_roots.append(ctx)
    if extra_roots:
        session._extra_tool_roots = extra_roots

    # ACP runtime override — bind a discovered ACP agent onto the worker session
    # exactly like the chat picker; empty leaves it native.
    if loop.provider:
        session.acp_provider = loop.provider
        session.acp_provider_agent = loop.provider_agent
        session.reasoning_effort = loop.reasoning_effort
        if not loop.attended:
            # Unattended: an ACP agent in its default permission mode would self-gate
            # file writes; bypass so it executes (host gate + SEL audit still govern).
            session.acp_mode = "bypassPermissions"

    # Per-session tool trust so the loop never stalls on per-tool approval. The
    # watchdog expires it after trust_ttl_secs → NEEDS_INPUT re-auth. Mirror it
    # onto the SessionManager approval_policy ("auto") — the same field a chat's
    # Trust/YOLO toggle sets — so subagents this loop spawns INHERIT auto-approval
    # (parent_policy=="auto") and run their tools instead of stalling/denying.
    session._trust = True
    try:
        state.sessions.set_approval_policy(session.key, "auto")
    except Exception:
        logger.warning(
            "loop: failed to set auto approval_policy for %s", session.key, exc_info=True
        )
    # Unattended loop: strip interactive tools from the worker's toolset (T5) so a
    # cycle can't wedge on an option-prompt-shaped tool. Pairs with the watchdog's
    # "unattended NEVER pauses" question-discard — that handles a stray question
    # post-hoc; this removes the tool that would ask it in the first place.
    if not loop.attended:
        session._unattended = True
    state.push_sessions_update()

    msg = _build_nudge_message(strat, loop, d)
    await svc.add(
        session_name=session.key,
        message=msg,
        idle_secs=loop.idle_secs or cfg.default_idle_secs,
        max_cycles=loop.max_cycles,
        stop_sentinel_path=str(d / store.STOP_SENTINEL) if d else "",
    )
    logger.info("loop: started %s (kind=%s) on session %s", loop_id, loop.kind, session.key)
    return updated


def _build_nudge_message(strat, loop: Loop, d) -> str:
    """The per-cycle autonudge message: the kind's cycle_nudge, with autonomous
    framing for an unattended loop. Single source so start + a re-arm produce the
    SAME message shape."""
    msg = strat.cycle_nudge(loop, str(d) if d else "")
    if not loop.attended:
        # Counter the base chat prompt's [OPTIONS: …] rule so it doesn't leak into
        # the loop's narration. Attended loops keep their own ask path.
        from personalclaw.autonomous_framing import with_autonomous_framing

        msg = with_autonomous_framing(msg)
    return msg


async def rearm_nudge_message(svc, loop_id: str) -> None:
    """Refresh the LIVE worker's autonudge message from the loop's CURRENT state. The
    cycle_nudge embeds kind-specific per-cycle context (e.g. code's stage directive,
    `[Stage plan — stage N/M …]`) captured once at start; when that context changes
    mid-run (a code stage advances) the brief.md is rewritten but the nudge message
    would otherwise stay STALE — telling the worker the old stage while brief.md says
    the new one. Rebuild + update it so both agree. No-op if no live worker. Never
    raises into the cycle hook."""
    try:
        from personalclaw.loop import kinds

        kinds.ensure_loaded()
        loop = store.get(loop_id)
        if loop is None:
            return
        nl = svc.get_by_session(session_key(loop_id))
        if nl is None:
            return
        strat = kinds.get_or_none(loop.kind)
        if strat is None:
            return
        await svc.update(nl.id, message=_build_nudge_message(strat, loop, store.loop_dir(loop_id)))
    except Exception:
        logger.debug("rearm_nudge_message failed for %s", loop_id, exc_info=True)


async def pause(state, svc, loop_id: str) -> Loop:
    """Pause: deactivate the main worker AND any parallel task-workers; each stops
    after its current cycle. Deactivate (not remove) so a resume re-arms them. A
    parallel code/design loop left only its main worker paused would otherwise keep
    its task-workers burning cycles + editing worktrees while the user thinks it's
    paused."""
    main = svc.get_by_session(session_key(loop_id))
    if main is not None:
        await svc.update(main.id, active=False)
    prefix = f"{session_key(loop_id)}-"
    for lp in list(getattr(svc, "_loops", {}).values()):
        if str(getattr(lp, "session_name", "")).startswith(prefix):
            await svc.update(lp.id, active=False)
    return store.update_status(loop_id, LoopStatus.PAUSED)


async def stop(state, svc, loop_id: str) -> Loop:
    """Stop (terminal): tear down + drop the STOP sentinel."""
    await _teardown(svc, loop_id)
    store.write_stop_sentinel(loop_id)
    return store.update_status(loop_id, LoopStatus.STOPPED)


async def nudge(state, svc, loop_id: str, text: str, task_id: str = "") -> Loop | None:
    """Queue guidance for the next cycle; resume if the worker awaited input.

    A ``task_id`` scopes the steer to one parallel task-worker (code/design kinds);
    the shared guidance.txt is also written so a sequential main worker picks it up
    — UNLESS that would leak a per-task steer into a re-armed main worker's channel
    in parallel mode (then only the per-task file gets it). Answering a project-
    level NEEDS_INPUT question always writes the shared channel (the resume reads it)."""
    loop = store.get(loop_id)
    if loop is None:
        return None
    answering_question = loop.status == LoopStatus.NEEDS_INPUT.value
    if task_id:
        store.write_task_guidance(loop_id, task_id, text)
        # Sequential main worker reads the shared file; parallel task-workers read
        # only their own. Write the shared file unless we're in parallel mode and not
        # answering a project-level question (avoid leaking a per-task steer).
        if answering_question or not _is_parallel(loop):
            store.write_guidance(loop_id, text)
    else:
        store.write_guidance(loop_id, text)
        # A project-level steer in parallel mode: fan out to every LIVE task-worker,
        # since the shared file is read by no one there.
        if _is_parallel(loop):
            for tid in loop.kind_config.get("queued_task_ids", []) or []:
                try:
                    if svc.get_by_session(task_session_key(loop_id, tid)) is not None:
                        store.write_task_guidance(loop_id, tid, text)
                except Exception:
                    logger.debug(
                        "fan-out steer to task %s failed for %s", tid, loop_id, exc_info=True
                    )
    store.append_nudge(loop_id, text, sent_at_cycle=loop.total_cycles)
    # A steer on a NEEDS_INPUT (awaiting answer) or BLOCKED (stall-paused, its nudge
    # loop deactivated) loop must RE-ARM the worker so the steer is actually consumed
    # (guidance.txt is read by no one while the loop is stopped).
    if loop.status in (LoopStatus.NEEDS_INPUT.value, LoopStatus.BLOCKED.value):
        # Don't re-arm into a missing workspace: start() re-provisions against it and
        # would run against nothing. If the user typed an answer instead of re-picking
        # a gone brownfield folder, keep them on NEEDS_INPUT with the re-pick prompt.
        # Kind-agnostic — reuses the kind's launch precondition (also enforced by the
        # start action + the reaper).
        from personalclaw.loop import kinds

        kinds.ensure_loaded()
        strat = kinds.get_or_none(loop.kind)
        blocker = getattr(strat, "launch_blocker", None)
        reason = blocker(loop) if blocker else None
        if reason:
            store.write_question(loop_id, reason)
            store.update_status(loop_id, LoopStatus.NEEDS_INPUT)
            return store.get(loop_id)
        store.clear_question(loop_id)
        return await start(state, svc, loop_id)
    return loop


def task_session_key(loop_id: str, task_id: str) -> str:
    """The session key for a parallel task-worker of a code/design loop."""
    return f"{session_key(loop_id)}-{task_id}"


_FIRST_CYCLE_IDLE_SECS = 5  # a freshly-spawned task-worker fires its first cycle fast


def _task_cycle_nudge(loop: Loop, task, worktree_dir: str, loop_dir: str) -> str:
    """The per-cycle trigger for a parallel task-worker: it works ONLY its task, in
    its own worktree, marks the task done, and writes a task finding. Ported from
    code/manager._task_cycle_nudge."""
    plan = "\n".join(
        f"   - {a.get('content', '')}"
        for a in (getattr(task, "action_plan", None) or [])
        if a.get("content")
    )
    crit = "\n".join(
        f"   - {c.get('description', '')}"
        for c in (getattr(task, "exit_criteria", None) or [])
        if c.get("description")
    )
    pending = store.read_task_guidance(loop.id, task.id)
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    rendered = render_use_case_prompt(
        "parallel_worker_nudge",
        {
            "loop_id": loop.id,
            "task_title": task.title,
            "task_id": task.id,
            "worktree_dir": worktree_dir,
            "loop_dir": loop_dir,
            "task_description": getattr(task, "description", ""),
            "plan": plan,
            "criteria": crit,
            "guidance": pending.strip(),
        },
    )
    if rendered is not None:
        return rendered
    lines = [
        f"You are one of several parallel workers on loop {loop.id}. Your ENTIRE job "
        f"is the single task below — work ONLY on it, in this checkout ({worktree_dir}). "
        "Do not touch other tasks.",
        "",
        f"TASK: {task.title}",
    ]
    if getattr(task, "description", ""):
        lines.append(task.description)
    if plan:
        lines += ["Action plan:", plan]
    if crit:
        lines += ["Done when:", crit]
    if pending.strip():
        lines += ["", f"USER STEERING FOR THIS TASK — apply it:\n{pending.strip()}"]
    lines += [
        "",
        f"ALSO: if {loop_dir}/guidance_{task.id}.txt exists (a steer arriving mid-run), "
        "read it — the user's steering for THIS task — apply it, then delete the file.",
        "",
        f"Mark the task in_progress now (task_update {task.id} in_progress). Implement it "
        "end-to-end in this checkout, validate its done-conditions, then mark it done "
        f"(task_update {task.id} done). Before you end the turn you MUST write "
        f"{loop_dir}/findings/task_{task.id}_NNN.json (next sequential N) with "
        "{cycle, stage, task_id, summary, key_insight, files_touched, evidence}. Write "
        "real code with your file tools; end the turn.",
    ]
    return "\n".join(lines)


async def spawn_task_worker(state, svc, loop: Loop, task, worktree_dir: str) -> str | None:
    """Start a dedicated worker session for ``task`` in its own ``worktree_dir``.
    Returns the session key, or None. Idempotent (a live task session is returned as-is).
    Ported from code/manager.spawn_task_worker onto the Loop entity."""
    cfg = AppConfig.load().loops
    skey = task_session_key(loop.id, task.id)
    existing = state._sessions.get(skey)
    if existing is not None and getattr(existing, "running", False):
        return skey
    kinds.ensure_loaded()
    strat = kinds.get_or_none(loop.kind)
    session = state.get_or_create_session(
        name=skey,
        agent=loop.agent or (strat.default_agent if strat else ""),
        model=loop.model,
        workspace_dir=worktree_dir,
        app="loop",
    )
    if loop.provider:
        session.acp_provider = loop.provider
        session.acp_provider_agent = loop.provider_agent
        session.reasoning_effort = loop.reasoning_effort
        if not loop.attended:
            session.acp_mode = "bypassPermissions"
    session._trust = True
    # Mirror trust onto approval_policy so loop-spawned subagents inherit auto-approve.
    try:
        state.sessions.set_approval_policy(session.key, "auto")
    except Exception:
        logger.warning(
            "loop: failed to set auto approval_policy for %s", session.key, exc_info=True
        )
    d = store.loop_dir(loop.id)
    roots = [str(d)] if d is not None else []
    ctx = _context_dir(loop)
    if ctx:
        roots.append(ctx)
    if roots:
        session._extra_tool_roots = roots
    state.push_sessions_update()
    msg = _task_cycle_nudge(loop, task, worktree_dir, str(d) if d else "")
    if not loop.attended:
        from personalclaw.autonomous_framing import with_autonomous_framing

        msg = with_autonomous_framing(msg)
    await svc.add(
        session_name=skey,
        message=msg,
        idle_secs=loop.idle_secs or cfg.default_idle_secs,
        max_cycles=loop.max_cycles,
        stop_sentinel_path=str(d / store.STOP_SENTINEL) if d else "",
        first_idle_secs=_FIRST_CYCLE_IDLE_SECS,
    )
    try:
        from personalclaw.tasks import registry

        await registry.update_task(task.id, provider_name="native", status="in_progress")
    except Exception:
        logger.debug("mark in_progress failed for task %s", task.id, exc_info=True)
    logger.info("loop: spawned task worker %s for task %s", skey, task.id)
    return skey


async def teardown_task_worker(svc, loop_id: str, task_id: str) -> None:
    """Remove a finished/cancelled task-worker's loop + clear its per-task guidance
    (so stale steering isn't re-applied on a later re-queue)."""
    nudge_loop = svc.get_by_session(task_session_key(loop_id, task_id))
    if nudge_loop is not None:
        await svc.remove(nudge_loop.id)
    store.clear_task_guidance(loop_id, task_id)


def _is_parallel(loop: Loop) -> bool:
    """Whether the loop runs parallel task-workers (code/design with a git
    workspace + autopilot + queued work). The full gate lives in the watchdog
    (2c); here it only decides steer routing — conservative: needs queued tasks."""
    if loop.kind not in ("code", "design"):
        return False
    if not (loop.workspace_dir and loop.autopilot):
        return False
    return bool(loop.kind_config.get("queued_task_ids"))


async def teardown_worker(svc, loop_id: str) -> None:
    """Stop the loop's worker(s) WITHOUT touching its Tasks — used by complete/stop/
    fail (the loop ends but its decomposed Tasks remain for review)."""
    await _teardown(svc, loop_id)


async def teardown_for_delete(svc, loop_id: str) -> None:
    """Full teardown before the loop row + dir are DELETED: stop the worker AND
    delete the backing Tasks Project (else each create-and-delete orphans a Project
    + its lists + tasks). Must run BEFORE store.delete (reads links off the row)."""
    await _teardown(svc, loop_id)
    try:
        from personalclaw.loop import tasks_link

        await tasks_link.teardown_tasks(loop_id)
    except Exception:
        logger.debug("teardown_tasks failed for %s", loop_id, exc_info=True)


async def _teardown(svc, loop_id: str) -> None:
    """Deactivate + remove the main worker loop AND any parallel task-workers, then
    clean up the loop's git worktrees + branches. Without the worktree cleanup, every
    parallel code loop that's torn down (stop/delete) leaks its `.worktrees/<id>` dirs
    + `pclaw/task-*` branches in the user's repo."""
    main = svc.get_by_session(session_key(loop_id))
    if main is not None:
        await svc.remove(main.id)
    prefix = f"{session_key(loop_id)}-"
    for lp in list(getattr(svc, "_loops", {}).values()):
        if str(getattr(lp, "session_name", "")).startswith(prefix):
            await svc.remove(lp.id)
    loop = store.get(loop_id)
    if loop is not None and (loop.workspace_dir or "").strip():
        try:
            from personalclaw.loop import worktree

            worktree.cleanup_all(loop.workspace_dir, loop.tasks_project_id)
        except Exception:
            logger.debug("worktree cleanup failed for %s", loop_id, exc_info=True)


async def reap_orphaned_loops(state, svc) -> int:
    """Re-arm loops left mid-flight by a crash/restart (startup sweep). A worker —
    and the planner — session lives only in memory, so a loop persisted RUNNING or
    PLANNING at startup has lost it. Re-arm each before the watchdog's first poll:
      • RUNNING  → :func:`start` (re-arm the execution worker).
      • PLANNING → re-kick one ``advance_plan`` pass (the stepwise walkthrough runs
        as a background task spawned from an HTTP request; a restart strands it in
        PLANNING with no live planner. advance_plan is idempotent + self-healing —
        it re-runs the in-flight step / design pass and stops at the next gate).
    PAUSED/STAGNANT/BLOCKED/NEEDS_INPUT/REVIEW await a deliberate action. Idempotent —
    a genuinely-live worker is skipped. Also GCs orphan file dirs with no backing row."""
    from personalclaw.concurrency import reap_orphans
    from personalclaw.loop import kinds

    kinds.ensure_loaded()
    running_orphans: list[Loop] = []
    planning_orphans: list[Loop] = []
    for loop in store.list_all():
        if loop.status == LoopStatus.RUNNING.value:
            sess = state._sessions.get(session_key(loop.id))
            if sess is not None and getattr(sess, "running", False):
                continue
            running_orphans.append(loop)
        elif loop.status == LoopStatus.PLANNING.value:
            planning_orphans.append(loop)

    async def _reap_running(loop: Loop) -> None:
        # A workspace-needing loop (brownfield code) can have its bound dir moved/
        # deleted during downtime. start() would re-provision against the gone path;
        # re-validate via the kind's launch precondition (the same one the start
        # action enforces) and pause for the user instead of resurrecting nothing.
        strat = kinds.get_or_none(loop.kind)
        blocker = getattr(strat, "launch_blocker", None)
        reason = blocker(loop) if blocker else None
        if reason:
            store.write_question(
                loop.id, f"{reason} (the workspace went missing during a restart)."
            )
            store.update_status(loop.id, LoopStatus.NEEDS_INPUT)
            logger.warning(
                "loop: orphaned %s blocked from re-arm (%s) — paused for the user", loop.id, reason
            )
            return
        await start(state, svc, loop.id)
        logger.info("loop: re-armed orphaned %s after restart", loop.id)

    async def _reap_planning(loop: Loop) -> None:
        # Re-kick the walkthrough so a restart-stranded PLANNING loop resumes instead
        # of freezing on a spinner forever. Lazy import (plan_walkthrough → store, no
        # manager cycle, but kept lazy for symmetry + cheap startup).
        from personalclaw.loop import plan_walkthrough as pw

        await pw.advance_plan(state, svc, loop.id)
        logger.info("loop: re-kicked stranded planning loop %s after restart", loop.id)

    n = await reap_orphans("loop", running_orphans, _reap_running)
    n += await reap_orphans("loop-planning", planning_orphans, _reap_planning)
    try:
        reaped = store.reap_orphan_dirs()
        if reaped:
            logger.info("loop: reaped %d orphan dir(s) with no DB row", reaped)
    except Exception:
        logger.warning("loop: orphan-dir GC failed", exc_info=True)
    return n
