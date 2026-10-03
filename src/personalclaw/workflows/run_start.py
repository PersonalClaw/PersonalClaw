"""What a run binds before its first node, and the context its work runs in.

Called from `RunController._prepare`: the declared `workspace:`, the
project's context dir as the memory cwd, a restricted origin's memory posture, and the document a
run that continues another starts from. Each is idempotent, because `_prepare` runs again on every
resume. Its tick loop runs in `run_context`, wherever it is started from.
"""

from __future__ import annotations

import contextvars
import logging
from typing import TYPE_CHECKING

from personalclaw.workflows import ownership
from personalclaw.workflows.models import RunStatus

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController
    from personalclaw.workflows.models import WorkflowRun

logger = logging.getLogger(__name__)


async def provision_workspace(ctl: RunController) -> bool:
    """Stand up the run's declared workspace before the first node (WORK-CONTAINERS §4.1).

    Returns False when the run was refused. This is the first production caller of
    `workspace.plan_provisioning` / `worktrees.pending_setup`: before it, a spec's
    `workspace:` block was parsed nowhere, so every run ran in place no matter what its
    template declared — the whole §4.1 mechanism was a decision layer with no call site.

    **A FATAL declaration REFUSES the run.** `parse_workspace` marks an unknown mode and a
    greedy preserve pattern fatal precisely because they cannot be honored, and honoring
    neither means running in a mode nobody chose. An ignored fatal issue is the inert-control
    shape this program keeps finding, so it terminates the run through `_finish` (the single
    terminal writer) instead of degrading quietly.

    **The lock is taken and RELEASED here, not held for the run.** Holding a flock across a
    multi-hour run would tie the workspace to this process's lifetime, so a gateway restart
    would strand it. What the lock actually protects is the provisioning WINDOW — the
    preserve+setup pass, where two processes writing the same tree corrupt each other. Live
    contention refuses the run rather than queueing: two runs interleaving writes in one
    worktree is worse than telling the second one now.

    Idempotent, because `_prepare` runs again on resume: `add_worktree` returns the same path
    for an existing run id (measured), setup is marker-guarded and content-addressed, and
    preserve is an overwriting copy. The second pass is therefore cheap and safe rather than a
    second workspace.

    Guarded on everything except the deliberate refusal: a provisioning bug must cost the
    isolation, never the run — a run that cannot start because a scratch dir failed to `mkdir`
    would be strictly worse than one that runs in the project workspace and says so.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.workflows import containers, provisioning
    from personalclaw.workflows.workspace import Mode

    if not provisioning.declares_workspace(ctl.spec):
        # No `workspace:` block, no managed workspace. The default mode fills in a block that
        # declared the OTHER fields; it does not opt every run in — see `declares_workspace`
        # for the boot-sweep interaction that measurement caught.
        return True
    try:
        cfg = AppConfig.load().workflows
        spec, issues = provisioning.resolve_spec(ctl.spec, default_mode=cfg.workspace_default_mode)
    except Exception:
        logger.debug("run %s: workspace spec unreadable", ctl.run.id, exc_info=True)
        return True

    fatal = [i for i in issues if i.fatal]
    if fatal:
        reason = "; ".join(i.message for i in fatal)
        ctl.journal.workspace_provisioned(
            {"ok": False, "issues": [i.to_dict() for i in fatal], "refused": True}
        )
        async with ctl._lock:
            await ctl._finish(
                RunStatus.FAILED, error=f"workspace declaration refused: {reason}"[:500]
            )
        return False

    lock = provisioning.acquire_workspace_lock(ctl.run.id, name=spec.name)
    if not lock.acquired:
        # A named workspace another live run holds. Refused, not queued — see the docstring.
        ctl.journal.workspace_provisioned(
            {"ok": False, "contended": True, "degraded_reason": lock.reason}
        )
        async with ctl._lock:
            await ctl._finish(RunStatus.FAILED, error=lock.reason[:500])
        return False
    try:
        result = await provisioning.provision(
            spec,
            run_id=ctl.run.id,
            project_id=ctl.run.project_id,
            workspace_dir=_project_workspace(ctl),
            issues=issues,
            runner=ctl.services.teardown_runner,
            # The fork anchor: a child forked from a checkpoint provisions its
            # container FROM the parent's committed workspace state. Empty for ordinary
            # runs, forks from head, and forks whose backend could not snapshot.
            from_snapshot=str(
                (getattr(ctl.run, "forked_from", None) or {}).get("workspace_snapshot", "") or ""
            ),
            # EI-6 §5.1 SPAWN half: the run's durable worker name — the SAME derivation the
            # boot sweep recomputes (`watchdog._durable_substrate`), so a session opened
            # under it is exactly the session a restarted gateway reattaches to. Passed
            # unconditionally (a cheap string); whether it is USED is `run_step`'s
            # flag+binary gate, kept next to the spawn it gates.
            durable_session=containers.durable_worker_name(ctl.run),
        )
    except Exception:
        logger.warning("run %s: workspace provisioning failed", ctl.run.id, exc_info=True)
        return True
    finally:
        lock.release()

    async with ctl._lock:
        provisioning.stamp_run(ctl.run, result, spec)
        ctl._save_run()
    ctl.journal.workspace_provisioned(result.to_dict())
    if result.path and (result.isolated or result.mode is Mode.IN_PLACE):
        # The stage dispatcher's cwd, so a code-kind run's subagents actually work IN the
        # worktree, or in the scratch folder a run declared or fell back to when its worktree or
        # container could not be made. Without this the isolation would be a directory nothing
        # ran in — the mechanism would look provisioned and be decorative. An in-place run's path
        # is the tree its project is bound to, which is what in place means: without this its
        # steps worked in the project's context folder (`bind_project_memory_cwd`) instead.
        ctl.services.cwd = result.path
    return True


def _project_workspace(ctl: RunController) -> str:
    """The codebase this run's project binds, or the services cwd.

    A project's `workspace_dir` is the tree a worktree branches from, the tree
    `preserve_patterns` copies out of, and the tree an in-place run works in. It is read the one
    way the spawn allowlist reads it too (`provisioning.project_tree`), so the folder an in-place
    run is pointed at is the folder its steps are admitted to. Falling back to `services.cwd`
    keeps a project-less run (a chat-launched batch) provisionable — its workspace is simply
    wherever the gateway is rooted, which is what every other cwd-consuming node already assumes.
    """
    from personalclaw.workflows import provisioning

    return provisioning.project_tree(ctl.run.project_id) or ctl.services.cwd


def bind_project_memory_cwd(ctl: RunController) -> None:
    """Default a project-owned run's cwd to the project's `context_dir` (§1.6).

    Memory is partitioned by cwd (`memory_dir_for_cwd`), so a project-owned run whose cwd
    is empty writes everything it learns into the shared `_ext/_default` partition — one
    pile every project's runs stir together. Binding the project's context dir (which §1.2
    already calls "the default cwd fallback for stage nodes", and which the hierarchy store
    documents as "the working area when no external workspace is bound") makes that memory
    project-local for free: no second partitioning mechanism, just the seam that exists.

    Runs LAST in `_prepare`, and only when nothing more specific has claimed the cwd:
    an isolated workspace (`result.path`, set just above) and a caller-supplied
    `services.cwd` are deliberate bindings, and a memory-locality default that overrode
    them would move a code-kind run out of the worktree it was provisioned into.
    """
    if ctl.services.cwd:
        return
    from personalclaw.memory_locality import project_memory_cwd

    cwd = project_memory_cwd(ctl.run.project_id)
    if not cwd:
        return
    ctl.services.cwd = cwd
    logger.info(
        "run %s: cwd bound to project context dir for memory locality (%s)", ctl.run.id, cwd
    )


async def carry_over_document(ctl: RunController) -> None:
    """Start a run that continues another (its `continue_from` input) from a copy of that run's
    document, before its first step reads it (`deliverable.carry_over`).

    Recorded on the run (`deliverable.CONTINUED_KEY`) the moment it is made, so a resume never
    copies twice, the run's record says which run it continued, and the Document panel says so.
    Best-effort like the rest of `_prepare`: a copy that fails leaves the run starting from nothing
    and says so in the log, rather than refusing a run its start already admitted.
    """
    from personalclaw.workflows import deliverable

    try:
        record = deliverable.carry_over(ctl.run, ctl.spec)
    except Exception:
        logger.warning("run %s: could not carry its document over", ctl.run.id, exc_info=True)
        return
    if record is None:
        return
    async with ctl._lock:
        ctl.run.extra[deliverable.CONTINUED_KEY] = record
        ctl._save_run()


def enforce_inherited_mode(ctl: RunController) -> None:
    """Apply a restricted origin's memory posture at run start (WORK-CONTAINERS §5.1).

    The run already carries the inherited mode in `extra` (stamped by `start_run`); this is the
    moment it becomes ENFORCED in the process-global `session_restrictions` registry — the fast
    path the knowledge/learning writers consult during the run. The chat layer never marks the
    registry (it enforces off `session.is_restricted` on the LIVE session object, which a
    background run no longer holds), so this is the registry's FIRST writer for a run's keys.

    The mark is made for BOTH the launching session key AND the run-owned key: the origin key is
    what the run-end LearningGate reads (`run_finish.capture_run_end` keys `for_session` off
    `origin.session_key`), and the owned key is what any run-scoped write would carry. A
    `temporary` run gets both marks per `restriction_calls`, because `is_temporary` gates reads
    while `is_restricted` gates writes.

    **Durability lives in `run.extra`, not a session JSONL.** A run owns no `ConversationLog`
    file — stage subagents persist under their own `subagent:<id>` keys, and the `workflow:`
    owned string is a provenance ref, not a JSONL key. The run record's `extra["memory_mode"]`
    IS the run's durable metadata head: `start_run` stamps it via `ownership.stamp_run_mode`,
    which is `ownership.durable_metadata` (same `memory_mode` key by construction, not two
    literals). It round-trips through the run record on disk and is what a gateway restart
    replays — after which `_prepare` runs again and re-marks the registry from it, and the
    engine's node-skip + the run-end gate keep reading it. Materializing an owned-session JSONL
    line would create a file no reader consumes (the reindex path forgets restricted sessions on
    sight), so the durable write is deliberately the `extra` head. DEVIATION from the literal
    "JSONL write" phrasing, recorded in the plan's Execution log.

    Idempotent and best-effort: `_prepare` runs once per live controller and again on resume,
    and re-marking a key already in the LRU is a no-op. A NORMAL run does nothing — an
    unrestricted origin has nothing to suppress. Guarded because `_prepare` must not fail the
    run over a registry mutation.
    """
    mode = ownership.run_mode(ctl.run)
    if mode is ownership.MemoryMode.NORMAL:
        return
    owned = ownership.own_session(ctl.run.id, "run", inherited_mode=mode)
    try:
        from personalclaw import session_restrictions

        for call in ownership.restriction_calls(owned):
            mark = getattr(session_restrictions, call)
            if ctl.run.origin.session_key:
                mark(ctl.run.origin.session_key)
            mark(owned.key)
    except Exception:
        logger.debug("run %s: registry mark failed", ctl.run.id, exc_info=True)


def run_context(run: WorkflowRun) -> contextvars.Context:
    """The context *run*'s tick loop runs in, whichever request, answer or restart starts it.

    A run that inherited a temporary/incognito origin runs as its own work under that mode and on
    the one model its origin runs on, as its record holds them (`ownership.run_mode`,
    `ownership.run_model`): its nodes reach no other model, its stages are marked and handed that
    model when they are spawned, and the stores refuse its writes. A loop started inside the chat's
    own request had that from the request; one resumed after a restart had nothing, so its stages
    started unmarked and on any model. Any other run runs in the context that starts it.
    """
    mode = ownership.run_mode(run)
    if mode is ownership.MemoryMode.NORMAL:
        return contextvars.copy_context()
    from personalclaw import memory_writes

    return memory_writes.work_context(ownership.owned_key(run.id, "run"), memory_mode=mode.value)
