"""Shared planner-pass runner — spawn a tool-equipped planner agent, arm a bounded
autonudge run with a brief, poll for a sentinel file, return its text, tear down.

Used by BOTH the Code feature and Goal Loop for every pass of the stepwise
walkthrough (design pass + each step pass). The runner is feature-agnostic: callers
pass the resolved primitives (session key, agent name, the agent's cwd, the dir
where the sentinel + STOP file live, the model/ACP binding). Feature modules wrap
this with their own project/loop lookup.

The autonudge loop self-halts the moment the sentinel appears (via the STOP file),
so the planner is a bounded one-author task, never a runaway loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

# Bounded planner-loop tuning (a planner investigates + authors, it is not a long
# execution loop). Module-level so tests can monkeypatch the poll interval.
PLANNER_MAX_CYCLES = 3
PLANNER_FIRST_IDLE = 10
PLANNER_POLL_SECS = 4
PLANNER_TIMEOUT_SECS = 600


def read_sentinel(workspace_dir: str, files_dir: str, sentinel: str) -> str:
    """Read ``sentinel`` from the agent's cwd (``workspace_dir``) or the feature's
    files dir, whichever has it. Returns '' if neither exists."""
    for base in [(workspace_dir or "").strip(), (files_dir or "").strip()]:
        if not base:
            continue
        try:
            p = os.path.join(base, sentinel)
            if os.path.isfile(p):
                return open(p, encoding="utf-8").read()
        except OSError:
            continue
    return ""


def clear_sentinels(workspace_dir: str, files_dir: str, sentinels: list[str]) -> None:
    """Remove stale sentinel files from both dirs so a pass never reads a prior
    pass's output. Best-effort."""
    for base in [(workspace_dir or "").strip(), (files_dir or "").strip()]:
        if not base:
            continue
        for s in sentinels:
            try:
                p = os.path.join(base, s)
                os.path.isfile(p) and os.unlink(p)
            except OSError:
                pass


async def run_planner_pass(
    state, svc, *,
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
    ``sentinel`` in workspace/files dir, writes the STOP file the moment it lands to
    halt the loop, and tears the loop down in ``finally``. Never raises.

    ``workspace_dir`` is the agent's cwd; ``files_dir`` is where the sentinel + STOP
    file are looked for/written (often == workspace_dir, or a feature's own dir when
    no workspace is bound). The two are searched in that order.

    ``extra_sentinels`` names OTHER walkthrough sentinels this pass might leave as
    scratch in the cwd — a step pass (``step_artifact.json``) commonly has the planner
    re-create the decomposition file (``plan_steps.json``) while it works. They're
    cleared alongside ``sentinel`` (pre-pass + teardown) so none of the walkthrough's
    scratch files survive in the user's bound workspace repo. Never READ from — only
    the active ``sentinel`` is the pass's output.
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
    clear_sentinels(workspace_dir, files_dir, _scratch)
    stop_path = os.path.join(files_dir, stop_sentinel_name) if files_dir else ""
    if stop_path:
        try:
            os.path.isfile(stop_path) and os.unlink(stop_path)
        except OSError:
            pass
    try:
        session = state.get_or_create_session(
            name=skey, agent=agent_name, model=model, workspace_dir=cwd, app=app,
        )
        session._trust = True
        if provider:
            session.acp_provider = provider
            session.acp_provider_agent = provider_agent
            session.reasoning_effort = reasoning_effort
            session.acp_mode = "bypassPermissions"
        try:
            state.push_sessions_update()
        except Exception:
            pass
        # The planner runs UNATTENDED — but the base chat system prompt carries a
        # "present choices as [OPTIONS: …]" CRITICAL rule (context.py) aimed at live
        # chat. Without an explicit counter that leaks into the planner's investigation
        # narration (the user saw [OPTIONS: Approve… | Merge… | Add…] menus). Prepend the
        # shared autonomous-run framing so the planner never offers menus / waits for a
        # user. One choke point → fixes BOTH the Code and Goal Loop planners.
        from personalclaw.autonomous_framing import with_autonomous_framing
        framed_brief = with_autonomous_framing(brief)
        await svc.add(
            session_name=skey, message=framed_brief, idle_secs=60,
            max_cycles=PLANNER_MAX_CYCLES, first_idle_secs=PLANNER_FIRST_IDLE,
            stop_sentinel_path=stop_path,
        )
        deadline = time.time() + (timeout_secs if timeout_secs is not None else PLANNER_TIMEOUT_SECS)
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
            raw = read_sentinel(workspace_dir, files_dir, sentinel)
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
                        "— stopping poll early", skey, sentinel)
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
        # Remove the output sentinel too: its content was already captured into the
        # return value above. The planner runs cwd'd to the workspace, so for a
        # brownfield project the sentinel (e.g. plan_steps.json) is written INTO the
        # user's repo — leaving it would pollute their git status + the cockpit
        # Changes tab with planning scratch files they never authored. We clear ALL
        # walkthrough scratch sentinels (not just this pass's active one): a step pass
        # outputs step_artifact.json but its planner routinely re-creates the
        # decomposition file plan_steps.json as scratch — clearing only the active
        # sentinel orphaned that file in the user's source tree.
        clear_sentinels(workspace_dir, files_dir, _scratch)
