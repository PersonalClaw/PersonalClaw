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
from personalclaw.loop import files as loop_files
from personalclaw.loop import kinds, posture, store
from personalclaw.loop.loop import Loop, LoopStatus, LoopStopReason

logger = logging.getLogger(__name__)


def session_key(loop_id: str) -> str:
    """The hidden worker session key for a loop (filtered from the chat sidebar)."""
    return f"loop-{loop_id}"


def worker_loop_id(key: str) -> str:
    """The loop whose worker *key* is, or ``""``: ``loop-<id>`` is its stage worker and
    ``loop-<id>-<task>`` a parallel task worker (a loop id carries no dash). The planner's
    ``loop-plan-<id>`` is not a worker, and ``plan`` is not a loop id, so it names none."""
    if not key.startswith("loop-"):
        return ""
    loop_id = key[len("loop-") :].split("-", 1)[0]
    return loop_id if loop_files.valid_loop_id(loop_id) else ""


#: Loops whose owner said "every tool, for the rest of this run" on one of their workers'
#: approval cards (:func:`grant_every_worker`). In memory, like the per-session grant it
#: extends: a run of the loop that begins again (a resume, a restart) asks again.
_LOOP_GRANTS: set[str] = set()


def _arm_posture(worker, loop: Loop) -> None:
    """Arm *worker* from its loop's Mode (``posture``), each time the loop arms it, with "This
    loop" when one of this run's own approval cards gave it (:func:`grant_every_worker`).

    **Unattended:** the worker runs under a standing grant until the trust window ends
    (``loops.trust_ttl_secs``), and a call nothing can approve is declined at once rather than left
    waiting. **Attended:** a person answers each call the way a chat's calls are answered, and the
    grants that already stand for chats stand here too: an agent's "Always allow", Trust reads,
    YOLO, an operator's hook pattern.
    """
    posture.arm(worker, posture.of(loop), granted=loop.id in _LOOP_GRANTS)


def grant_every_worker(state, loop_id: str) -> None:
    """ "This loop" on an approval card: every worker of *loop_id* runs its tools without asking
    until this run of the loop ends (a pause, a stop, or a restart ends it; see
    :func:`_arm_posture`). Its live workers take the grant now, mid-turn included; a task
    worker the scheduler starts later takes it when it is armed. Each is bounded by the
    operator ceiling where it is applied (``SessionManager.set_approval_policy``)."""
    from personalclaw.constants import dashboard_session_key

    _LOOP_GRANTS.add(loop_id)
    for key in worker_session_keys(state, loop_id):
        worker = state._sessions.get(key)
        if worker is None:
            continue
        worker._trust = True
        worker._trust_from_floor = ""
        try:
            state.sessions.set_approval_policy(dashboard_session_key(key), "auto")
        except Exception:
            logger.warning("loop: could not extend the grant to %s", key, exc_info=True)


def end_unattended_grant(state, loop_id: str) -> None:
    """An Unattended run's trust window ended: none of its workers runs a call unasked any more,
    its task workers included, and an agent CLI is no longer told to skip its asks. With nobody
    there to answer, their calls are declined until the owner resumes the loop, which arms each
    worker afresh (:func:`_arm_posture`). A standing grant of the owner's own (an agent's "Always
    allow") re-seeds at the worker's next turn, as it does in a chat."""
    for key in worker_session_keys(state, loop_id):
        worker = state._sessions.get(key)
        if worker is None:
            continue
        worker._trust = False
        worker._trust_reads = False
        worker._trust_from_floor = ""
        worker._agent_floor_seeded = False
        if getattr(worker, "acp_mode", "") == "bypassPermissions":
            worker.acp_mode = ""


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
    d = loop_files.loop_dir(loop.id)
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
    loop_files.write_brief(loop.id, body)


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

    d = loop_files.loop_dir(loop_id)
    cfg = AppConfig.load().loops
    # A run of the loop beginning (again) asks again: "This loop" was for the run it was given in.
    _LOOP_GRANTS.discard(loop_id)

    session = state.get_or_create_session(
        name=session_key(loop_id),
        agent=loop.agent or strat.default_agent,
        model=loop.model,
        workspace_dir=loop.workspace_dir,
        app="loop",  # hidden worker — filtered from the chat sidebar
        # Scope the worker's artifacts to the loop's Project: a resolved
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

    # Who answers the worker's tool calls: its loop's Mode (`_arm_posture`). The chat runner
    # syncs the session's approval policy from it at the start of each turn, which is also what
    # a subagent the worker spawns inherits (an Unattended worker's "auto"; an Attended one's
    # asks go to its owner).
    _arm_posture(session, loop)
    state.push_sessions_update()

    msg = _build_nudge_message(strat, loop, d)
    await svc.add(
        session_name=session.key,
        message=msg,
        idle_secs=loop.idle_secs or cfg.default_idle_secs,
        max_cycles=loop.max_cycles,
        stop_sentinel_path=str(d / loop_files.STOP_SENTINEL) if d else "",
    )
    # A pause stood the parallel task workers down along with the stage worker (`pause`); a
    # resume stands them back up, each on its loop's Mode as it is now.
    prefix = f"{session_key(loop_id)}-"
    for task_loop in svc.list_all():
        name = str(getattr(task_loop, "session_name", ""))
        if not name.startswith(prefix):
            continue
        worker = state._sessions.get(name)
        if worker is not None:
            _arm_posture(worker, loop)
        await svc.update(task_loop.id, active=True)
    logger.info("loop: started %s (kind=%s) on session %s", loop_id, loop.kind, session.key)
    return updated


def _build_nudge_message(strat, loop: Loop, d) -> str:
    """The per-cycle autonudge message: the kind's cycle_nudge, with autonomous
    framing for an unattended loop. Single source so start + a re-arm produce the
    SAME message shape."""
    return posture.frame(posture.of(loop), strat.cycle_nudge(loop, str(d) if d else ""))


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
        await svc.update(
            nl.id, message=_build_nudge_message(strat, loop, loop_files.loop_dir(loop_id))
        )
    except Exception:
        logger.debug("rearm_nudge_message failed for %s", loop_id, exc_info=True)


def worker_session_keys(state, loop_id: str) -> list[str]:
    """The live worker sessions of ``loop_id``: the main worker and every parallel task-worker."""
    main = session_key(loop_id)
    return [
        k
        for k in list(getattr(state, "_sessions", {}) or {})
        if k == main or k.startswith(f"{main}-")
    ]


async def halt_worker_turns(state, loop_id: str) -> int:
    """Stop the turn in flight on every worker session of ``loop_id``. Returns how many it stopped.

    🔴 Deactivating the nudge loop only stops the NEXT cycle from being fired. A cycle already in
    flight is one task that runs a turn and then up to `_MAX_CYCLE_REPROMPTS` re-prompt turns when
    no finding appeared (gateway `_run_turn_bounded`), and nothing told it the loop had changed. So
    a paused loop kept working — measured 2026-09-25, it wrote ``findings/cycle_2.json`` 3.5 minutes
    after the cockpit said "Paused" — and a DELETED loop's in-flight cycle re-prompted itself onto
    the session the delete had just removed, re-saving a 219k-token transcript as an orphan chat.

    So this is the second half of pause/stop/delete: the queued turns go (a cancelled turn's
    `run_chat` finally-block would otherwise start the next one) and the running turn is stopped
    through the chat Stop's own path (`SessionManager.stop_turn`: cooperative cancel, kill
    fallback, and it stops the subagents that turn spawned). The re-prompt loop itself checks that
    its loop is still armed before each re-prompt, which is what keeps it from starting a new turn
    once this one ends.
    """
    halted = 0
    for key in worker_session_keys(state, loop_id):
        if await halt_turn(state, key):
            halted += 1
    return halted


async def halt_turn(state, key: str) -> bool:
    """Drop session *key*'s queued turns and stop the one running, through the chat Stop's own
    path (:func:`halt_worker_turns` says why both). Returns whether a running turn was stopped.

    Also what holds a loop's planner session for incident mode (``planning.runner``): the same
    stop, on a session that is not one of the loop's workers."""
    from personalclaw.constants import dashboard_session_key

    session = state._sessions.get(key)
    if session is None:
        return False
    queue = getattr(session, "_queue", None)
    if queue:
        queue.clear()
    sessions = getattr(state, "sessions", None)
    if not getattr(session, "running", False) or sessions is None:
        return False
    try:
        await sessions.stop_turn(dashboard_session_key(key), force=False)
        return True
    except Exception:
        # A worker that will not stop must not keep the loop from reaching the state the user
        # asked for — the nudge loop is already disarmed, so no further cycle starts.
        logger.warning("loop: stopping the worker turn failed for %s", key, exc_info=True)
        return False


async def _deactivate_workers(svc, loop_id: str) -> None:
    """Switch off the nudge loop of the main worker AND every parallel task-worker, keeping them:
    a resume (:func:`start`) switches them back on, each with its session, budget and worktree."""
    main = svc.get_by_session(session_key(loop_id))
    if main is not None:
        await svc.update(main.id, active=False)
    prefix = f"{session_key(loop_id)}-"
    # `list_all()` (the public surface) rather than the old `svc._loops` peek — the nudge
    # service no longer keeps an in-memory dict (its rows live in the trigger store).
    for lp in svc.list_all():
        if str(getattr(lp, "session_name", "")).startswith(prefix):
            await svc.update(lp.id, active=False)


async def pause(state, svc, loop_id: str) -> Loop:
    """Pause: deactivate the main worker AND any parallel task-workers, and STOP the cycle in
    flight (:func:`halt_worker_turns`) — so nothing more is done until Resume, which re-arms them.
    Deactivate (not remove) so a resume re-arms them. A parallel code/design loop left only its
    main worker paused would otherwise keep its task-workers burning cycles + editing worktrees
    while the user thinks it's paused."""
    await _deactivate_workers(svc, loop_id)
    # The status goes first, so every surface already reads "Paused" while the turn is winding
    # down — and the halt comes second, so it is disarmed nudge loops the stopping turn sees.
    paused = store.update_status(loop_id, LoopStatus.PAUSED)
    await halt_worker_turns(state, loop_id)
    return paused


async def stand_down(state, svc, loop_id: str) -> None:
    """Stop a loop that FAILED without throwing away what its workers made.

    A failed loop says "Resume to retry", so it ends the way a pause does: every worker's nudge
    loop is switched off and kept, the turn in flight is stopped, and each task worker keeps its
    worktree and branch, edits its owner approved included. Resume (:func:`start`) switches the
    workers back on where they were. Only a Stop or a delete removes the worktrees
    (:func:`_teardown`), and the Stop dialog says so. No worker holds a task now, so one held in
    progress goes back to open."""
    from personalclaw.loop import tasks_link

    await _deactivate_workers(svc, loop_id)
    await halt_worker_turns(state, loop_id)
    await tasks_link.release_in_progress(loop_id)


async def stop(state, svc, loop_id: str) -> Loop:
    """Stop (terminal): tear down, drop the STOP sentinel, and stop the turn in flight. Then no
    worker has a task any more, so each task one held in progress goes back to open."""
    from personalclaw.loop import tasks_link

    await _teardown(svc, loop_id)
    loop_files.write_stop_sentinel(loop_id)
    stopped = store.update_status(loop_id, LoopStatus.STOPPED, stop_reason=LoopStopReason.USER)
    await halt_worker_turns(state, loop_id)
    # A loop stopped while it is still planning has a planner and no workers yet.
    await halt_planner(state, svc, loop_id)
    await tasks_link.release_in_progress(loop_id)
    return stopped


async def halt_planner(state, svc, loop_id: str) -> None:
    """End loop *loop_id*'s planner: its nudge row goes and its turn in flight is stopped.

    The row is kept on disk, so one left behind fires planner turns again after a restart, for a
    loop nothing drives any more. A walkthrough pass still polling sees its planner gone, and
    finding the loop no longer planning it starts no retry (``plan_walkthrough._still_planning``).
    """
    from personalclaw.loop.plan_walkthrough import planner_session_key

    key = planner_session_key(loop_id)
    row = svc.get_by_session(key)
    if row is not None:
        await svc.remove(row.id)
    await halt_turn(state, key)


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
        loop_files.write_task_guidance(loop_id, task_id, text)
        # Sequential main worker reads the shared file; parallel task-workers read
        # only their own. Write the shared file unless we're in parallel mode and not
        # answering a project-level question (avoid leaking a per-task steer).
        if answering_question or not _is_parallel(loop):
            loop_files.write_guidance(loop_id, text)
    else:
        loop_files.write_guidance(loop_id, text)
        # A project-level steer in parallel mode: fan out to every LIVE task-worker,
        # since the shared file is read by no one there.
        if _is_parallel(loop):
            for tid in loop.kind_config.get("queued_task_ids", []) or []:
                try:
                    if svc.get_by_session(task_session_key(loop_id, tid)) is not None:
                        loop_files.write_task_guidance(loop_id, tid, text)
                except Exception:
                    logger.debug(
                        "fan-out steer to task %s failed for %s", tid, loop_id, exc_info=True
                    )
    # The cycle this steer was sent at, projected off the ledger rather than read
    # from the retired `loops.total_cycles` column.
    loop_files.append_nudge(loop_id, text, sent_at_cycle=loop_files.cycles_completed(loop_id))
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
            loop_files.write_question(loop_id, reason)
            store.update_status(loop_id, LoopStatus.NEEDS_INPUT)
            return store.get(loop_id)
        loop_files.clear_question(loop_id)
        return await start(state, svc, loop_id)
    return loop


def task_session_key(loop_id: str, task_id: str) -> str:
    """The session key for a parallel task-worker of a code/design loop."""
    return f"{session_key(loop_id)}-{task_id}"


def usage_key(loop_id: str) -> str:
    """The key a loop's spend is booked under in the usage ledger.

    The worker is a dashboard chat session named :func:`session_key`, so the chat seam writes each
    of its turns under :func:`~personalclaw.constants.dashboard_history_key` of that name, and a
    task worker's under the same key extended at the separator. :func:`loop_spend` reads through
    this, and anything else that books money to a loop must write through it, or its rows sit
    outside the loop's total.
    """
    from personalclaw.constants import dashboard_history_key

    return dashboard_history_key(session_key(loop_id))


def loop_spend(loop_id: str) -> dict:
    """What one loop cost, read from the per-turn ledger (MRT-3).

    Lives here because this module owns every session key a loop spends under, and the figure is
    exactly "the spend booked against those keys". A loop's worker turns reach
    ``usage/turns.jsonl`` through the ordinary chat seam (``chat_runner`` passes
    ``session._app or "chat"``, and the worker's ``_app`` is ``"loop"``), so no loop-specific
    writer is involved — only a loop-specific READ, and it keys through the seam's own rule
    (:func:`usage_key`), never the bare session names this module mints. The seam writes the
    wrapped key; a read of the bare name matched no row at all, so a loop that had spent real
    money read $0.00 here, and the watchdog's cost cap, which reads this figure, never tripped.

    Two figures, deliberately not summed into one:

    * ``worker`` — :func:`session_key` and every :func:`task_session_key` under it, via a
      separator-aware prefix query on :func:`usage_key`. A fan-out loop that under-counted its
      task workers would report a confidently-low number, so the prefix is the point.
    * ``planning`` — ``plan_walkthrough.planner_session_key``, which is ``loop-plan-<id>`` and
      therefore NOT under the worker prefix; its turns are read under the same rule. Reported
      beside the worker figure rather than folded into it: the planner is a distinct session doing
      distinct work, and a surface that silently omitted it would imply a completeness the number
      does not have. The caller renders both.

    NOT ``ledger.run_totals``. That reads ``loop/journal.py``'s ``step_completed`` rows, which
    carry no ``tokens`` and no ``cost_usd``, so making it work would mean copying turn dollars
    into a second store — one dollar in two records, in a subsystem whose own money doctrine
    (``routing/usage.py`` module docstring) is that a total which can double-count is worse than
    one that admits a gap. See MODEL-ROUTING-TELEMETRY's MRT-3 log for the recorded deviation.
    """
    from personalclaw import usage_ledger
    from personalclaw.constants import dashboard_history_key
    from personalclaw.loop.plan_walkthrough import planner_session_key

    worker = usage_ledger.totals(session_prefix=usage_key(loop_id))
    planning = usage_ledger.totals(session_key=dashboard_history_key(planner_session_key(loop_id)))
    return {
        "dollars_est": round(float(worker["cost_usd"]), 6),
        "turns": int(worker["turns"]),
        "tokens": int(worker["input_tokens"]) + int(worker["output_tokens"]),
        # False when ANY constituent turn had no price row, so the caller can state a FLOOR
        # instead of a total. A money figure that hides its own incompleteness is the defect.
        "priced": bool(worker["priced"]),
        "planning": {
            "dollars_est": round(float(planning["cost_usd"]), 6),
            "turns": int(planning["turns"]),
        },
    }


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
    pending = loop_files.read_task_guidance(loop.id, task.id)
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
    ]
    if loop_dir:
        lines += [
            "",
            f"The loop's own files are in {loop_dir}, not in this checkout, so never search "
            f'for them. First read {loop_dir}/status.json: if its status is not "running", '
            f"end the turn. The loop's brief is {loop_dir}/brief.md.",
        ]
    lines += ["", f"TASK: {task.title}"]
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
    Returns the session key, or None. Idempotent: a task whose worker loop is armed is returned
    as-is. A session with no worker loop gets one even mid-turn (a turn that outlived its loop),
    or nothing would run the task again; its first cycle fires once that turn ends."""
    cfg = AppConfig.load().loops
    skey = task_session_key(loop.id, task.id)
    if svc.get_by_session(skey) is not None:
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
    # The same posture as the stage worker: its loop's Mode, and "This loop" when this run of
    # the loop was given it (`_arm_posture`).
    _arm_posture(session, loop)
    d = loop_files.loop_dir(loop.id)
    roots = [str(d)] if d is not None else []
    ctx = _context_dir(loop)
    if ctx:
        roots.append(ctx)
    if roots:
        session._extra_tool_roots = roots
    state.push_sessions_update()
    nudge = _task_cycle_nudge(loop, task, worktree_dir, str(d) if d else "")
    msg = posture.frame(posture.of(loop), "\n".join([nudge, *kinds.workspace_rules_lines()]))
    await svc.add(
        session_name=skey,
        message=msg,
        idle_secs=loop.idle_secs or cfg.default_idle_secs,
        max_cycles=loop.max_cycles,
        stop_sentinel_path=str(d / loop_files.STOP_SENTINEL) if d else "",
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
    (so stale steering isn't re-applied on a later re-queue). A task the worker leaves
    unfinished goes back to open, so a resumed loop can take it again."""
    from personalclaw.loop import tasks_link

    nudge_loop = svc.get_by_session(task_session_key(loop_id, task_id))
    if nudge_loop is not None:
        await svc.remove(nudge_loop.id)
    loop_files.clear_task_guidance(loop_id, task_id)
    await tasks_link.release_in_progress(loop_id, task_ids=[task_id])


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
    """Stop the loop's worker(s) WITHOUT deleting its Tasks — used by complete, and stop keeps
    them the same way (only :func:`teardown_for_delete` removes them). A failure ends through
    :func:`stand_down` instead, which keeps the workers' work for a resume. The decomposed Tasks
    remain for review; only a task a worker held in progress goes back to open, since none holds
    it now."""
    from personalclaw.loop import tasks_link

    await _teardown(svc, loop_id)
    await tasks_link.release_in_progress(loop_id)


async def teardown_for_delete(state, svc, loop_id: str) -> None:
    """Full teardown before the loop row + dir are DELETED: stop the worker AND its turn in
    flight AND delete the backing Tasks Project (else each create-and-delete orphans a Project
    + its lists + tasks). Must run BEFORE store.delete (reads links off the row), and before the
    worker session is reaped — a turn still running there would re-save the transcript the reap
    just deleted."""
    await _teardown(svc, loop_id)
    await halt_worker_turns(state, loop_id)
    await halt_planner(state, svc, loop_id)
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
    _LOOP_GRANTS.discard(loop_id)
    main = svc.get_by_session(session_key(loop_id))
    if main is not None:
        await svc.remove(main.id)
    prefix = f"{session_key(loop_id)}-"
    # `list_all()`, the service's public surface — the same fix `pause` got. The nudge service
    # keeps no `_loops` dict any more, so the old `getattr(svc, "_loops", {})` peek read
    # an empty dict and a stopped or deleted parallel loop's task-workers were never removed.
    for lp in svc.list_all():
        if str(getattr(lp, "session_name", "")).startswith(prefix):
            await svc.remove(lp.id)
    loop = store.get(loop_id)
    if loop is not None and (loop.workspace_dir or "").strip():
        try:
            from personalclaw.loop import worktree

            worktree.cleanup_all(loop.workspace_dir, loop.tasks_project_id)
        except Exception:
            logger.debug("worktree cleanup failed for %s", loop_id, exc_info=True)
