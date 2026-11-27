"""Whether a subagent may start now, and what stops a fan-out from starting more.

A spawn is let in only past the host's free memory, an incident, the day's token budget, the
folder it is to work in (:func:`validate_cwd`) and the agent it names, each refusal audited where
it is made: ``SubagentManager.spawn`` asks :func:`admission` before it takes a slot, and a restart
takes a waiting spawn back through the same door. A fan-out's children can stop it: each finished
child is folded into its consecutive-failure breaker (:func:`note_child_outcome`) and each child's
cost into its run budget (:func:`charge_child_and_check_budget`), and a stop either one sets
refuses the fan-out's next spawns.

Moved out of ``subagent`` unchanged, the manager's methods as functions that take it, so that
module stays the manager's lifecycle; ``subagent`` re-exports the folder checks, as it did.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from personalclaw.config.loader import AppConfig
from personalclaw.hooks import safe_read_file
from personalclaw.sel import sel
from personalclaw.subagent_tier import run_agent

if TYPE_CHECKING:
    from personalclaw.subagent import SubagentInfo, SubagentManager

logger = logging.getLogger(__name__)


def validate_cwd(cwd: str, allowed_roots: list[str], *, run_workdir: str = "") -> tuple[str, str]:
    """Validate a caller-supplied ``cwd`` for a spawn.

    Resolves symlinks, then checks that the path is inside the workspace (the folder the parent
    session itself works in), under a root the operator added in
    ``agent.subagent_cwd_allowed_roots``, or inside *run_workdir*: the folder of the workflow run
    the spawn is a step of (``workflows.provisioning.run_workdir``: its scratch folder, its own
    worktree, the folder its project is bound to when it works in place, or its project's context
    folder), which the engine put the run's steps in. The list is empty by default, so the
    workspace and a run's own folder are where a spawn may work until the owner names another.
    Only then is the path read as a directory: a path outside every root is refused before
    anything reads it, with the one refusal every such path gets.

    Args:
        cwd: Caller-supplied absolute path (may contain ``~``).
        allowed_roots: Extra permitted root paths from config (may contain ``~``).
        run_workdir: The spawning run's own folder, ``""`` for any other spawn.

    Returns:
        ``(resolved_cwd, error)``. On success ``error`` is empty and
        ``resolved_cwd`` is the canonical absolute path (realpath-resolved).
        On failure ``error`` is a reason string and ``resolved_cwd`` is empty.
    """
    if not cwd:
        return ("", "")
    try:
        expanded = os.path.expanduser(cwd)
        if not os.path.isabs(expanded):
            return ("", "cwd must be an absolute path")
        resolved = os.path.realpath(expanded)
    except (OSError, ValueError) as exc:
        return ("", f"cwd resolution failed: {exc}")
    # The workspace defaults to the home's own folder, which no configured root names: a subagent
    # may always work where the session that spawns it works.
    from personalclaw.config.loader import workspace_root

    workspace = os.path.realpath(workspace_root())
    resolved_roots = [workspace] + [os.path.realpath(os.path.expanduser(r)) for r in allowed_roots]
    if run_workdir:
        resolved_roots.append(os.path.realpath(run_workdir))
    for root in resolved_roots:
        # The root itself or a folder below it, in one check: a sibling whose name only starts
        # with the root's (`allow-extra` beside `allow`) is not in it.
        if resolved.startswith(root) and resolved[len(root) :][:1] in ("", os.sep):
            if not os.path.isdir(resolved):
                return ("", "cwd does not exist or is not a directory")
            return (resolved, "")
    return (
        "",
        f"cwd is not in the workspace ({workspace}) or under any allowed root: {allowed_roots}. "
        "Add its folder in Settings → Agent defaults → Allowed working directories.",
    )


def _run_workdir_for(parent_run: str) -> str:
    """The folder of the workflow run a spawn is a step of, or ``""``.

    ``parent_run`` is ``workflow:<run_id>`` only when the workflow engine dispatches the run's own
    step: no caller of ``/api/spawn`` or of an agent's tool can set it. The folder itself comes
    from that run's record (``workflows.provisioning.run_workdir``), never from the request, so a
    spawn is admitted to its own run's folder and to no other."""
    from personalclaw.workflows.ownership import OWNED_PREFIX

    if not parent_run.startswith(OWNED_PREFIX):
        return ""
    from personalclaw.workflows.provisioning import run_workdir

    return run_workdir(parent_run[len(OWNED_PREFIX) :])


def _validate_agent(requested: str) -> tuple[str, str]:
    """Validate an agent name against the configured agents in AppConfig.

    Returns ``(agent_name, error)``. An empty ``requested`` resolves to the
    default agent with no error (``("", "")``). A KNOWN agent returns its name.
    An UNKNOWN agent returns a TYPED error naming the valid agents — never a
    silent downgrade to the default: a fan-out that named the wrong agent
    used to run entirely on ``personalclaw`` with nothing but a log line, so the
    caller must fail loudly and let the user fix the name.
    """
    if not requested:
        return "", ""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    if requested in cfg.agents:
        return requested, ""
    available = sorted(cfg.agents.keys() - {"personalclaw", "personalclaw-orchestrator"})
    listed = ", ".join(available) if available else "(none configured)"
    return "", f"unknown agent {requested!r}; valid agents: {listed}"


def check_memory_available(min_gb: float = 4.0, path: str = "/proc/meminfo") -> tuple[bool, float]:
    """Check if enough memory is available to spawn a subagent.

    Reads /proc/meminfo MemAvailable via ``safe_read_file`` (hooks.py)
    and compares against *min_gb*.
    Returns (ok, available_gb).  On read failure returns (True, -1.0)
    to avoid blocking spawns on non-Linux systems.
    """
    try:
        text = safe_read_file(path)
    except PermissionError:
        logger.warning("Memory check blocked: sensitive path %s", path)
        return (True, -1.0)
    except OSError:
        return (True, -1.0)
    try:
        for line in text.splitlines():
            if line.startswith("MemAvailable:"):
                kb = int(line.split()[1])
                avail = kb / (1024 * 1024)
                return (avail >= min_gb, round(avail, 2))
    except (ValueError, IndexError):
        return (True, -1.0)
    return (True, -1.0)


#: Why :func:`admission` refuses a spawn while incident mode is on.
INCIDENT_REFUSAL = "spawn refused: incident mode active (resume with `personalclaw incident off`)"


def note_refused(why: str, agent_id: str) -> None:
    """Count the spawn *agent_id* in the incident when *why* is the incident's refusal
    (:data:`INCIDENT_REFUSAL`): a helper start it refused never runs later. The id is the one
    the work that asked for it was given."""
    if why != INCIDENT_REFUSAL:
        return
    from personalclaw.guardrails.incident import note_suppressed
    from personalclaw.workref import work_ref

    note_suppressed("spawns_refused", work_ref("subagent", agent_id))


def admission(
    task: str,
    parent_session_key: str,
    agent: str,
    cwd: str,
    parent_run: str,
    capability_class: str | None,
) -> tuple[str, str, str]:
    """Whether a spawn may be taken in now: ``(why not, its folder, its agent)``, ``why not``
    empty when it may. The limits a new spawn meets, and one a restart took back
    (``subagent_waiting.take_back``), each refusal audited where it is made. *task* is its
    masked task."""
    # --- Memory guard: refuse to spawn if system memory is critically low ---
    try:
        min_mem = AppConfig.load().agent.spawn_min_memory_gb
    except Exception:
        min_mem = 4.0
    mem_ok, avail_gb = check_memory_available(min_gb=min_mem)
    if not mem_ok:
        logger.warning(
            "Subagent spawn refused: only %.2f GB available (min %.1f GB required)",
            avail_gb,
            min_mem,
        )
        sel().log_tool_invocation(
            session_key=parent_session_key or "",
            source="subagent",
            tool_name="subagent_run",
            outcome="refused_low_memory",
            metadata={"available_gb": avail_gb, "min_gb": min_mem, "task": task[:120]},
        )
        return (
            f"spawn refused: only {avail_gb:.1f} GB memory available (need {min_mem:.0f} GB)",
            "",
            agent,
        )

    # --- Incident kill switch: refuse spawns during an incident ----------
    # A subagent is unattended work. Interactive chat is untouched; a spawn is
    # not the chat stream, so it is suspended.
    try:
        from personalclaw.guardrails.incident import incident_active

        if incident_active():
            logger.info("Subagent spawn refused: incident mode active")
            sel().log_tool_invocation(
                session_key=parent_session_key or "",
                source="subagent",
                tool_name="subagent_run",
                outcome="refused_incident",
                metadata={"task": task[:120]},
            )
            return (INCIDENT_REFUSAL, "", agent)
    except Exception:
        logger.debug("subagent spawn incident check failed (fail-open)", exc_info=True)

    # --- Budget guard: refuse to spawn if the day-scope token ceiling is hit ---
    # A subagent is unattended work; if the day's token budget is already
    # exhausted, don't start another one (pause-into-refuse). An UNREADABLE
    # ceiling refuses too (#3458) — it follows `proactive/autoexec.py`'s "an unverified
    # ceiling authorises nothing" rather than this seam's old blanket fail-open,
    # because a spawn is exactly the unattended spend the ceiling exists to bound.
    # Any other error still fails open: the budget is a guardrail, not a hard gate.
    try:
        from personalclaw.guardrails.budgets import (
            BudgetConfigUnreadable,
            BudgetVerdict,
            budget_from_config,
            get_meter,
        )

        try:
            _day_budget = budget_from_config()
        except BudgetConfigUnreadable as _exc:
            logger.warning("Subagent spawn refused: %s", _exc)
            sel().log_tool_invocation(
                session_key=parent_session_key or "",
                source="subagent",
                tool_name="subagent_run",
                outcome="refused_budget_unverified",
                metadata={"reason": str(_exc), "task": task[:120]},
            )
            return (
                f"spawn refused: {_exc}, so nothing ran (fix `guardrails.budgets` in "
                "config.json)",
                "",
                agent,
            )
        if not _day_budget.is_unlimited:
            # The token ceiling only: a spent dollar one refuses the spawn's calls that cost
            # money where they are made, and one on a model that costs nothing still runs.
            _verdict, _reason = get_meter().check_day_before_work(_day_budget)
            if _verdict is BudgetVerdict.EXCEEDED:
                logger.warning("Subagent spawn refused: %s", _reason)
                sel().log_tool_invocation(
                    session_key=parent_session_key or "",
                    source="subagent",
                    tool_name="subagent_run",
                    outcome="refused_budget_exceeded",
                    metadata={"reason": _reason, "task": task[:120]},
                )
                return (
                    f"spawn refused: {_reason} (resets next day, or raise the budget in "
                    "Settings → Security → Guardrails)",
                    "",
                    agent,
                )
    except Exception:
        logger.debug("subagent spawn budget check failed (fail-open)", exc_info=True)

    # --- CWD validation: reject bad paths before consuming a session ---
    resolved_cwd = ""
    if cwd:
        try:
            allowed_roots = AppConfig.load().agent.subagent_cwd_allowed_roots
        except Exception:
            # Fail closed: with the config unavailable, no folder the owner might have added
            # counts, so a subagent may work in the workspace (and a run's step in its own
            # run's folder) and nowhere else. `[]` is also the field's default, so a
            # config.json the loader had to discard (`AppConfig.load` returns defaults and
            # never raises for that) lands on the same narrow list without this arm. What
            # remains here is defence in depth for an unexpected raise.
            allowed_roots = []
        resolved_cwd, cwd_err = validate_cwd(
            cwd, allowed_roots, run_workdir=_run_workdir_for(parent_run)
        )
        if cwd_err:
            logger.warning("Subagent spawn refused: invalid cwd %r: %s", cwd, cwd_err)
            sel().log_tool_invocation(
                session_key=parent_session_key or "",
                source="subagent",
                tool_name="subagent_run",
                outcome="rejected_invalid_cwd",
                metadata={"cwd": cwd[:200], "reason": cwd_err, "task": task[:120]},
            )
            return f"spawn refused: {cwd_err}", "", agent

    # --- Agent validation: an unknown agent is a TYPED error, never a
    # silent downgrade. Done BEFORE any queue slot so a bad name fails fast and a
    # queued spawn is never drained onto the wrong agent. ---
    if agent:
        agent, err = _validate_agent(agent)
        if err:
            return err, "", ""
    return "", resolved_cwd, run_agent(capability_class, agent)


# ── a fan-out's stops ──────────────────────────────────────────────────────────────────────

# Consecutive child failures within one fan-out before its breaker trips.
# Mirrors ``session._CIRCUIT_BREAKER_THRESHOLD`` — the per-child ``subagent:<id>``
# session is released before it can fail, so the breaker must key on the FAN-OUT.
_CIRCUIT_BREAKER_THRESHOLD = 5


def note_child_outcome(manager: SubagentManager, info: SubagentInfo) -> None:
    """Fold one finished child into its fan-out's consecutive-failure breaker.

    The per-child ``subagent:<id>`` session is already released by the time a
    child fails, so the session-level breaker (``session._CIRCUIT_BREAKER_``) can
    never trip for sub-agents — the real guards were only the turn limit and the
    reaper. This keys the breaker on the FAN-OUT instead: a SUCCESS resets
    the count; a genuine FAILURE increments it, and at
    ``_CIRCUIT_BREAKER_THRESHOLD`` consecutive failures the whole fan-out is
    stopped so a broken run cannot keep burning children. A user-CANCELLED child
    is neither (an intentional stop is not a failure).
    """
    if info._outcome_noted:
        return
    info._outcome_noted = True
    from personalclaw.subagent import _fanout_key

    fkey = _fanout_key(info)
    if info.cancelled:
        return
    if info.error:
        count = manager._fanout_failures.get(fkey, 0) + 1
        manager._fanout_failures[fkey] = count
        if count >= _CIRCUIT_BREAKER_THRESHOLD and fkey not in manager._fanout_stops:
            reason = f"fan-out breaker tripped after {count} consecutive child failures"
            manager._fanout_stops[fkey] = reason
            logger.error("Subagent fan-out breaker tripped for %s: %s", fkey or "-", reason)
            try:
                sel().log_tool_invocation(
                    session_key=info.parent_session_key or "",
                    source="subagent",
                    tool_name="subagent_run",
                    outcome="fanout_breaker_tripped",
                    metadata={"fanout": fkey, "failures": count},
                )
            except Exception:
                logger.debug("SEL audit failed for breaker trip %s", fkey, exc_info=True)
    else:
        # A success clears the consecutive-failure streak for this fan-out.
        manager._fanout_failures.pop(fkey, None)


def charge_child_and_check_budget(manager: SubagentManager, info: SubagentInfo) -> None:
    """Fold one child's cost into the fan-out's RUN-scoped budget and, if the
    run ceiling is now exceeded, STOP the fan-out mid-flight.

    Consumes the per-child cost/tokens already captured at ``EVENT_COMPLETE``
    (NOT a second ledger) and composes with
    the guardrails' :class:`SpendMeter` run scope: it charges the meter
    keyed on the fan-out key and re-reads ``check_run`` after each child. The
    day-scope check at spawn is a point-in-time snapshot; N children each spend
    under it, so the run scope is what actually bounds a fan-out. On EXCEEDED the
    fan-out is stopped with a TYPED reason (refusing queued/new spawns) and its
    in-flight children are cancelled. Fail-open: a budget is a guardrail — except for
    an UNREADABLE ceiling, which stops the fan-out (#3458), because the run scope is
    the only thing that bounds N children each spending under one day snapshot.

    The DAY scope is charged where each child's calls are made, once: by the spend guard on
    a native child's model, and on an agent CLI's own turns (``acp.spend``), since every
    spawn rides the metered orchestration axis. So only the fan-out's run scope is charged
    here. Charging the day here as well counted a child twice, so the cap bit at half the
    real spend and its refusal named a total the day had not spent.
    """
    from personalclaw.subagent import _fanout_key

    fkey = _fanout_key(info)
    try:
        from personalclaw.guardrails.budgets import (
            BudgetConfigUnreadable,
            BudgetVerdict,
            get_meter,
            run_budget_from_config,
        )

        meter = get_meter()
        tokens = info.input_tokens + info.output_tokens
        try:
            budget = run_budget_from_config()
        except BudgetConfigUnreadable as exc:
            if fkey not in manager._fanout_stops:
                manager._fanout_stops[fkey] = f"{exc}, so the fan-out stopped"
                logger.warning("Subagent fan-out %s stopped: %s", fkey or "-", exc)
            return
        if budget.is_unlimited:
            return
        meter.charge_run(fkey, tokens, info.cost_usd)
        verdict, reason = meter.check_run(fkey, budget)
        if verdict is BudgetVerdict.EXCEEDED and fkey not in manager._fanout_stops:
            typed = f"run budget exceeded ({reason})"
            manager._fanout_stops[fkey] = typed
            logger.warning("Subagent fan-out %s stopped mid-flight: %s", fkey or "-", typed)
            try:
                sel().log_tool_invocation(
                    session_key=info.parent_session_key or "",
                    source="subagent",
                    tool_name="subagent_run",
                    outcome="fanout_budget_exceeded",
                    metadata={"fanout": fkey, "reason": reason},
                )
            except Exception:
                logger.debug("SEL audit failed for budget stop %s", fkey, exc_info=True)
            # Kill the in-flight children of this fan-out so "stops mid-flight"
            # is literal, not just "refuses the next one". Later, and only while this stop
            # still stands: the child that crossed the ceiling may be the fan-out's last.
            asyncio.ensure_future(cancel_if_still_stopped(manager, fkey, typed))
    except Exception:
        logger.debug("fan-out budget check failed (fail-open)", exc_info=True)


async def cancel_if_still_stopped(manager: SubagentManager, fkey: str, reason: str) -> int:
    """The run budget's cancel of *fkey*, which runs after the child that crossed the ceiling
    has ended. It stops the fan-out only while the stop that scheduled it, *reason*, still
    stands. When that child was a chat fan-out's last, its ending has drained the fan-out and
    ended the stop, and the chat's next helper, started under *fkey* since, must not be ended
    by a stop that is gone. A workflow run's stop stands until the run ends
    (:meth:`SubagentManager._maybe_clear_fanout`), so its next agent step is refused, not
    started."""
    if manager._fanout_stops.get(fkey) != reason:
        return 0
    return await manager.cancel_fanout(fkey, reason=reason)
