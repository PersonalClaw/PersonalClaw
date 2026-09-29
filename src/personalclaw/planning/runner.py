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

The autonudge loop self-halts the moment the sentinel appears (via the STOP file),
so the planner is a bounded one-author task, never a runaway loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time

logger = logging.getLogger(__name__)

# Bounded planner-loop tuning (a planner investigates + authors, it is not a long
# execution loop). Module-level so tests can monkeypatch the poll interval.
PLANNER_MAX_CYCLES = 3
PLANNER_FIRST_IDLE = 10
PLANNER_POLL_SECS = 4
PLANNER_TIMEOUT_SECS = 600


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
) -> str | None:
    """Run ONE planner pass and return the sentinel's raw text (or None on
    timeout/no-output). Spawns (or reuses) the planner session cwd'd to
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
        session._trust = True
        if files_dir and files_dir not in (session._extra_tool_roots or []):
            session._extra_tool_roots = [*(session._extra_tool_roots or []), files_dir]
        if provider:
            session.acp_provider = provider
            session.acp_provider_agent = provider_agent
            session.reasoning_effort = reasoning_effort
            session.acp_mode = "bypassPermissions"
        try:
            state.push_sessions_update()
        except Exception:
            pass
        # The planner runs UNATTENDED — no one is present to answer a question or pick
        # an option, and its output is read later as a plan. Prepend the shared
        # autonomous-run framing so the planner never offers menus / waits for a
        # user. One choke point → fixes BOTH the Code and Goal Loop planners.
        from personalclaw.autonomous_framing import with_autonomous_framing

        framed_brief = with_autonomous_framing(brief)
        await svc.add(
            session_name=skey,
            message=framed_brief,
            idle_secs=60,
            max_cycles=PLANNER_MAX_CYCLES,
            first_idle_secs=PLANNER_FIRST_IDLE,
            stop_sentinel_path=stop_path,
        )
        deadline = time.time() + (
            timeout_secs if timeout_secs is not None else PLANNER_TIMEOUT_SECS
        )
        # Once the planner loop is gone OR deactivated (it exhausted PLANNER_MAX_CYCLES
        # without writing the sentinel — narrated but never authored the file, or hit a
        # model error), no further cycle will run, so polling to the full deadline is a
        # dead wait (a 10-min spinner). Bail after a short grace — enough polls to cover
        # a filesystem-flush lag between the agent's final write and loop deactivation —
        # returning None so the caller reverts the step to PENDING + the FE offers Retry
        # promptly instead of after 600s.
        dead_polls = 0
        _GRACE_POLLS = 2
        while time.time() < deadline:
            await asyncio.sleep(PLANNER_POLL_SECS)
            reclaim_misplaced(_ws, files_dir, _scratch, users_own)
            raw = read_sentinel(files_dir, sentinel)
            if raw:
                if stop_path:
                    try:
                        open(stop_path, "w").close()
                    except OSError:
                        pass
                return raw
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
                    return None
            else:
                dead_polls = 0
        return None
    except Exception:
        logger.warning("run_planner_pass failed for %s (%s)", skey, sentinel, exc_info=True)
        return None
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
