"""Shared planner-pass runner — spawn a tool-equipped planner agent, arm a bounded
autonudge run with a brief, poll for a sentinel file, return its text, tear down.

Used by every kind's stepwise planning walkthrough (design pass + each step pass). The
runner is feature-agnostic: callers pass the resolved primitives (session key, agent
name, the agent's cwd, the loop's own folder where the sentinel + STOP file live, the
model/ACP binding).

The planner works in the bound workspace, and its files belong to the loop: its brief
names the absolute path in the loop's own folder, that folder is the only place a
sentinel is read or cleared, and nothing in the workspace is read or removed, whatever
its name. A file the planner writes into the workspace anyway (the bare name lands in its
working directory) is moved to the loop's folder, and only when this pass created it.

The planner is armed from its loop's Mode like every session the loop runs (``loop.posture``):
an Attended loop's planner asks a person for its tool calls the way a chat does, and an
Unattended one runs on its grant, framed as an autonomous run. Its scratch work (a throwaway copy
of the workspace, a test run's output) goes in the loop's own folder (:data:`SCRATCH_DIR`), which
the pass removes when it ends.

The autonudge loop self-halts the moment the sentinel appears (via the STOP file),
so the planner is a bounded one-author task, never a runaway loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass

from personalclaw.guardrails.incident import incident_active
from personalclaw.loop import posture as loop_posture

logger = logging.getLogger(__name__)

# Bounded planner-loop tuning (a planner investigates + authors, it is not a long
# execution loop). Module-level so tests can monkeypatch the poll interval.
PLANNER_MAX_CYCLES = 3
PLANNER_FIRST_IDLE = 10
PLANNER_POLL_SECS = 4
PLANNER_TIMEOUT_SECS = 600

#: The planner's scratch folder, inside the loop's own folder: where a throwaway copy of the
#: workspace or a test run's output goes, never a shared temporary folder or the workspace itself.
SCRATCH_DIR = "scratch"

#: How a planner pass ended (:attr:`PlannerPass.ended`).
WROTE = "wrote"  # the file appeared; ``text`` is what it holds
TIMED_OUT = "timed_out"  # the pass ran out of time before the file appeared
STOPPED = "stopped"  # the planner's loop stopped (its cycles spent, an error) without writing it
FAILED = "failed"  # the pass itself could not run


@dataclass(frozen=True)
class PlannerPass:
    """What one planner pass came back with: the file's text, and how the pass ended — so the
    caller tells the planner and its owner what really happened (a file it cannot read is not a
    file never written, and neither is a pass that ran out of time)."""

    text: str = ""
    ended: str = FAILED
    limit_secs: float = 0.0


def read_sentinel(files_dir: str, sentinel: str) -> str:
    """The text of ``sentinel`` in the loop's own folder, or '' when it is not there."""
    base = (files_dir or "").strip()
    if not base:
        return ""
    try:
        with open(os.path.join(base, sentinel), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def clear_sentinels(files_dir: str, sentinels: list[str]) -> None:
    """Remove the walkthrough's files from the loop's own folder so a pass never reads a
    prior pass's output. Best-effort."""
    base = (files_dir or "").strip()
    if not base:
        return
    for s in sentinels:
        try:
            p = os.path.join(base, s)
            if os.path.isfile(p):
                os.unlink(p)
        except OSError:
            pass


def scratch_rule(files_dir: str) -> str:
    """The brief's line on where the planner's scratch work goes: its own folder in the loop's."""
    where = os.path.join(files_dir, SCRATCH_DIR) if files_dir else SCRATCH_DIR
    return (
        f"\n\nScratch work — a throwaway copy of the workspace, a test run's output — goes in "
        f"`{where}` (create it), never in a shared temporary folder such as /tmp and never in "
        "the workspace. It is removed when this pass ends."
    )


def clear_scratch(files_dir: str) -> None:
    """Remove the planner's scratch folder from the loop's own folder. Best-effort; a link
    there is unlinked, never followed."""
    base = (files_dir or "").strip()
    if not base:
        return
    path = os.path.join(base, SCRATCH_DIR)
    try:
        if os.path.islink(path):
            os.unlink(path)
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        logger.debug("planner: could not clear its scratch folder %s", path, exc_info=True)


def _present(folder: str, names: list[str]) -> set[str]:
    """Which of ``names`` exist in ``folder`` (as anything: a file, a folder, a link)."""
    base = (folder or "").strip()
    if not base:
        return set()
    return {name for name in names if os.path.lexists(os.path.join(base, name))}


def reclaim_misplaced(
    workspace_dir: str, files_dir: str, names: list[str], present_before: set[str]
) -> None:
    """Move a walkthrough file the planner wrote into the workspace to the loop's own folder.

    Only a regular file that was NOT in the workspace when the pass began is moved
    (``present_before``): one that was is the user's own, and is never read, moved or
    removed. Best-effort.
    """
    ws = (workspace_dir or "").strip()
    base = (files_dir or "").strip()
    if not ws or not base or os.path.realpath(ws) == os.path.realpath(base):
        return
    for name in names:
        if name in present_before:
            continue
        src = os.path.join(ws, name)
        if os.path.islink(src) or not os.path.isfile(src):
            continue
        try:
            shutil.move(src, os.path.join(base, name))
        except OSError:
            logger.debug("planner: could not move %s out of the workspace", src, exc_info=True)


async def run_planner_pass(
    state,
    svc,
    *,
    session_key: str,
    agent_name: str,
    workspace_dir: str,
    files_dir: str,
    sentinel: str,
    brief: str,
    app: str,
    model: str = "",
    provider: str = "",
    provider_agent: str = "",
    reasoning_effort: str = "",
    stop_sentinel_name: str = "STOP",
    timeout_secs: int | None = None,
    extra_sentinels: tuple[str, ...] = (),
    posture: loop_posture.Posture = loop_posture.UNREADABLE,
    granted: bool = False,
) -> PlannerPass:
    """Run ONE planner pass and return what it came back with (:class:`PlannerPass`: the
    sentinel's raw text, and how the pass ended). Spawns (or reuses) the planner session cwd'd to
    ``workspace_dir``, arms a bounded autonudge run with ``brief``, polls for
    ``sentinel`` in ``files_dir``, writes the STOP file the moment it lands to halt
    the loop, and tears the loop down in ``finally``. Never raises.

    ``workspace_dir`` is the agent's cwd; ``files_dir`` is the loop's own folder, where
    the sentinel + STOP file live (the cwd too when no workspace is bound). The planner
    session reaches it with its file tools.

    ``extra_sentinels`` names OTHER walkthrough files this pass might write — a step
    pass (``step_artifact.json``) commonly has the planner re-create the decomposition
    file (``plan_steps.json``) while it works. They're cleared from ``files_dir``
    alongside ``sentinel`` (pre-pass + teardown), and moved there first when the
    planner wrote them into the workspace (:func:`reclaim_misplaced`). Never READ from —
    only the active ``sentinel`` is the pass's output.

    ``posture`` is the planner's loop's (``loop.posture.of``), and ``granted`` "This loop" from one
    of this planning's own approval cards. A caller that names none gets the cautious posture: the
    planner asks a person, and its spend counts. The time a turn spends waiting on its owner's
    answer is not the pass's to spend.
    """
    skey = session_key
    _scratch = [sentinel, *(s for s in extra_sentinels if s and s != sentinel)]
    # The agent's cwd is the bound workspace, but a brownfield workspace can be
    # moved/deleted while the project sits paused mid-walkthrough — spawning into a
    # non-existent cwd breaks the planner. Fall back to files_dir (the store always
    # materializes it) when workspace_dir is set but no longer on disk.
    _ws = (workspace_dir or "").strip()
    if _ws and not os.path.isdir(_ws):
        _ws = ""
    cwd = (_ws or files_dir or "").strip()
    clear_sentinels(files_dir, _scratch)
    # The user's own files of these names, if any: never read, moved or removed.
    users_own = _present(_ws, _scratch)
    stop_path = os.path.join(files_dir, stop_sentinel_name) if files_dir else ""
    if stop_path:
        try:
            if os.path.isfile(stop_path):
                os.unlink(stop_path)
        except OSError:
            pass
    try:
        session = state.get_or_create_session(
            name=skey,
            agent=agent_name,
            model=model,
            workspace_dir=cwd,
            app=app,
        )
        if files_dir and files_dir not in (session._extra_tool_roots or []):
            session._extra_tool_roots = [*(session._extra_tool_roots or []), files_dir]
        if provider:
            session.acp_provider = provider
            session.acp_provider_agent = provider_agent
            session.reasoning_effort = reasoning_effort
        # Who answers the planner's calls, and whose spend it is: its loop's Mode, the way the
        # loop arms its workers. One choke point for every kind's planner.
        loop_posture.arm(session, posture, granted=granted)
        try:
            state.push_sessions_update()
        except Exception:
            pass
        await svc.add(
            session_name=skey,
            message=loop_posture.frame(posture, brief + scratch_rule(files_dir)),
            idle_secs=60,
            max_cycles=PLANNER_MAX_CYCLES,
            first_idle_secs=PLANNER_FIRST_IDLE,
            stop_sentinel_path=stop_path,
        )
        limit = timeout_secs if timeout_secs is not None else PLANNER_TIMEOUT_SECS
        deadline = time.time() + limit
        # Once the planner loop is gone OR deactivated (it exhausted PLANNER_MAX_CYCLES
        # without writing the sentinel — narrated but never authored the file, or hit a
        # model error), no further cycle will run, so polling to the full deadline is a
        # dead wait (a 10-min spinner). Bail after a short grace — enough polls to cover
        # a filesystem-flush lag between the agent's final write and loop deactivation —
        # so the caller reverts the step to PENDING + the FE offers Retry promptly instead
        # of after 600s.
        dead_polls = 0
        _GRACE_POLLS = 2
        # The planner is loop work, so incident mode holds it with every other runner: its turn
        # in flight stops, its next one is held where it fires (the idle runtime), and the time
        # the switch is on is not the pass's to spend — it carries on once the switch is off
        # rather than report a time-out that never happened. Nor is the time its turn waits on
        # its owner's answer to one of its calls: a person deciding is not a planner running out.
        last = time.time()
        stopped_for_incident = False
        waiting_on_owner = getattr(state, "waiting_on_owner", None)
        while time.time() < deadline:
            await asyncio.sleep(PLANNER_POLL_SECS)
            now = time.time()
            if incident_active():
                deadline += now - last
                last = now
                if not stopped_for_incident:
                    stopped_for_incident = True
                    from personalclaw.loop.manager import halt_turn

                    await halt_turn(state, skey)
                continue
            if callable(waiting_on_owner) and waiting_on_owner(skey):
                deadline += now - last
            last = now
            stopped_for_incident = False
            reclaim_misplaced(_ws, files_dir, _scratch, users_own)
            raw = read_sentinel(files_dir, sentinel)
            if raw:
                if stop_path:
                    try:
                        open(stop_path, "w").close()
                    except OSError:
                        pass
                return PlannerPass(raw, WROTE, limit)
            loop = svc.get_by_session(skey)
            if loop is None or not getattr(loop, "active", True):
                dead_polls += 1
                if dead_polls >= _GRACE_POLLS:
                    logger.info(
                        "run_planner_pass: planner loop for %s ended without writing %s "
                        "— stopping poll early",
                        skey,
                        sentinel,
                    )
                    return PlannerPass(ended=STOPPED, limit_secs=limit)
            else:
                dead_polls = 0
        logger.info(
            "run_planner_pass: %s ran out of time (%ss) before writing %s", skey, limit, sentinel
        )
        return PlannerPass(ended=TIMED_OUT, limit_secs=limit)
    except Exception:
        logger.warning("run_planner_pass failed for %s (%s)", skey, sentinel, exc_info=True)
        return PlannerPass(ended=FAILED)
    finally:
        try:
            loop = svc.get_by_session(skey)
            if loop is not None:
                await svc.remove(loop.id)
        except Exception:
            pass
        try:
            if stop_path and os.path.isfile(stop_path):
                os.unlink(stop_path)
        except OSError:
            pass
        # The output was already captured into the return value above. Every walkthrough
        # file goes, not just this pass's: a step pass outputs step_artifact.json, and
        # its planner routinely re-creates plan_steps.json as well. One the planner put
        # in the workspace during this pass is moved out first, so none is left in the
        # user's tree (their git status, the cockpit's Changes tab).
        reclaim_misplaced(_ws, files_dir, _scratch, users_own)
        clear_sentinels(files_dir, _scratch)
        clear_scratch(files_dir)
