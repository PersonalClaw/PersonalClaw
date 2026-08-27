"""The ONE loop supervisor — one evaluator over a declared policy (`PP-16`, seam 3).

A loop's done-ness used to be pluggable Python: a ``LoopKindStrategy.is_done_signal`` per kind
plus two satellite hooks (``has_done_check``, ``budget_stop_genuine``) the watchdog reached for
with ``getattr``, and a third rule (``_stagnation_disabled``) hard-coded in the watchdog against
one kind's name. Five implementations, three lookup styles, and no single place that answered
"what completes this loop?".

This module is that place. It reads a :class:`~personalclaw.workflows.supervisor_policy.\
SupervisorPolicy` — the object `PP-14` declared and `PP-15` wired — and dispatches its
``convergence.signal`` to the ONE implementation of that mechanism. The domain knowledge that
used to be spread over five modules is now DATA in
:data:`~personalclaw.workflows.supervisor_policy.KIND_CONVERGENCE`; the code below is
kind-agnostic and never branches on ``loop.kind``.

**No new vocabulary.** A done-signal names which MECHANISM produces the answer; what a judge
actually decided still travels in ``judge_contract``'s verdict types, adjudicated by
``judge_contract.adjudicate``. `WF2LOO-16` reconciled those dialects and this module adds none.

**The tenet is unchanged: no agent certifies its own work.** Every mechanism here is the
SUPERVISOR's own read — a command it runs, a judge subagent it commissions — never the worker's
self-report. The watchdog still owns the lifecycle decision; this only supplies the signal.
"""

from __future__ import annotations

import logging

from personalclaw.loop.loop import Loop
from personalclaw.workflows.supervisor_policy import (
    DONE_NEVER,
    DONE_ORCHESTRATED,
    DONE_SIGNALS,
    DONE_VERIFY_COMMAND,
    ConvergenceSpec,
    SupervisorPolicy,
)

logger = logging.getLogger(__name__)


def _cfg(loop: Loop) -> dict:
    return loop.kind_config or {}


def _command(loop: Loop, spec: ConvergenceSpec) -> str:
    """The command the policy points at, read off the loop's own config."""
    if not spec.command_key:
        return ""
    return str(_cfg(loop).get(spec.command_key, "") or "")


def _criteria(loop: Loop, spec: ConvergenceSpec) -> list[str]:
    """The criteria list the policy points at (a verifiable goal's sub-goals)."""
    if not spec.criteria_key:
        return []
    raw = _cfg(loop).get(spec.criteria_key, []) or []
    if not isinstance(raw, list):
        return []
    return [str(s).strip() for s in raw if str(s).strip()]


async def done_signal(loop: Loop, findings: list[dict], policy: SupervisorPolicy) -> bool | None:
    """The loop's done-ness read for the CURRENT state, produced by something other than the
    worker. ``True`` = complete, ``False`` = keep going, ``None`` = can't tell (defer).

    Dispatches the policy's declared mechanism. An unknown mechanism is a programming error in
    the declaration table, not a runtime condition, so it raises rather than deferring — a
    silently-deferring loop is exactly the failure the closed
    :data:`~personalclaw.workflows.supervisor_policy.DONE_SIGNALS` set exists to make impossible.
    """
    spec = policy.convergence
    if spec.signal not in DONE_SIGNALS:
        raise ValueError(f"unknown done signal {spec.signal!r}; known: {sorted(DONE_SIGNALS)}")
    if spec.signal == DONE_ORCHESTRATED:
        # The kind's per-cycle orchestration hook owns done-ness (code advances the SDLC stage and
        # runs its gate; design advances the design step). There is no point-in-time signal here.
        return None
    if spec.signal == DONE_NEVER:
        # A monitor loop never self-completes — only a user Stop (or its budget) ends it.
        return False
    if spec.signal == DONE_VERIFY_COMMAND:
        return await _verify_command_signal(loop, findings, spec)
    return await _judge_assessment_signal(loop, findings, spec)


async def _verify_command_signal(
    loop: Loop, findings: list[dict], spec: ConvergenceSpec
) -> bool | None:
    """The deterministic mechanism: RUN the declared command and read its exit code.

    An unset command yields ``None`` (``run_verify_command`` returns the can't-tell tristate for
    an empty command), which is how a General loop with no check defers to budget by design.
    """
    from personalclaw.loop.gates import run_verify_command

    ok = await run_verify_command(_command(loop, spec), loop.workspace_dir or None, label="verify")
    if ok is not True:
        return ok  # False (the check ran + failed) / None (couldn't run) → not done yet
    # The check passed — but a worker can point the command at a SUBSET of a multi-criterion goal
    # (e.g. `npm test` green after only the engine phase, while the AI / UI sub-goals are unbuilt).
    # A green command on a partial build then falsely completes the whole goal (observed live: goal
    # b7abd778 marked done after phase 1/3). So when the loop declares MORE THAN ONE criterion, the
    # command passing is necessary but not sufficient — a separate judge must confirm every
    # criterion is met before we call it done.
    criteria = _criteria(loop, spec)
    if len(criteria) <= 1:
        return True  # single/no criterion → the command IS the whole goal
    return await _all_criteria_met(loop, criteria, findings)


async def _all_criteria_met(loop: Loop, criteria: list[str], findings: list[dict]) -> bool | None:
    """A strict judge over a verifiable loop's criteria: PASS only if the evidence from completed
    cycles shows EVERY criterion is met. Guards against a green command on a partial build.

    Returns True (all met), False (>=1 unmet → keep going), or None (judge unavailable → defer;
    the watchdog still bounds by budget). Conservative: any ambiguity is NOT a pass.
    """
    if not findings:
        return None
    from personalclaw.loop.gates import judge_verdict, verdict_is_pass, verdict_rendered
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    recent = findings[-6:]
    evidence = "\n".join(
        f"- cycle {f.get('cycle')}: {str(f.get('summary', '') or f.get('key_insight', ''))[:300]}"
        for f in recent
    )
    criteria_block = "\n".join(f"- {s}" for s in criteria)
    # The completion gate lives in the prompt system (bundled ``task-subgoal-judge``, bindable in
    # Settings → Prompts).
    prompt = render_use_case_prompt(
        "subgoal_judge",
        {"task": loop.task, "criteria": criteria_block, "evidence": evidence},
    )
    if not prompt:
        return None
    raw = await judge_verdict(prompt)
    if verdict_is_pass(raw):
        return True
    # A real FAIL → keep cycling. A non-verdict (judge/provider unavailable) → defer (None), NOT a
    # clean False, so the watchdog can flag a degraded done-ness brain rather than silently spin;
    # budget still caps the loop.
    return False if verdict_rendered(raw) else None


async def _judge_assessment_signal(
    loop: Loop, findings: list[dict], spec: ConvergenceSpec
) -> bool | None:
    """A SEPARATE judge subagent (never the worker) scores the latest cycle's done-ness + marginal
    value; the deterministic granularity dial decides returns-exhaustion. The judge advises; the
    supervisor (watchdog) decides.

    Returns True (done / returns-exhausted), False (keep going), or None (defer — a judge failure
    is observable-but-not-a-clean-False so the watchdog can surface degradation).
    """
    if not findings:
        return None
    from personalclaw.loop import instrument
    from personalclaw.loop import judge as judge_mod
    from personalclaw.loop import store
    from personalclaw.loop.granularity import returns_exhausted_calibrated

    finding = findings[-1]
    # P4 canary: once per loop-run, prove the done-ness judge can tell a strong cycle from an empty
    # one before trusting ANY of its verdicts. A blind judge (mis-bound model / broken rubric) would
    # otherwise complete the loop on plausible garbage. On a confirmed-blind judge we DEFER (return
    # None — never a clean False/True) and record ``judge_calibrated=False``; the watchdog reads
    # that flag and halts the loop to NEEDS_INPUT with a judge_blind event (the assessment can't
    # publish, so the flag is the seam). A probe that can't run defers without caching (retry next
    # cycle).
    cfg0 = _cfg(loop)
    if cfg0.get("judge_calibrated") is False:
        return None  # previously confirmed blind → defer; watchdog owns the halt
    if "judge_calibrated" not in cfg0:

        async def _probe_assess(goal, dod, fnd, prior):
            return await judge_mod.assess_cycle(goal, dod, fnd, prior)

        trustworthy = await instrument.probe_judge(_probe_assess)
        if trustworthy is not None:  # None = probe couldn't run → don't cache, retry next cycle
            store.set_kind_config_key(loop.id, "judge_calibrated", bool(trustworthy))
            if trustworthy is False:
                return None  # blind → defer; watchdog surfaces judge_blind + NEEDS_INPUT
    cycle = int(finding.get("cycle", len(findings)))
    # Slice C (O-E2): give the judge whatever ground-truth anchor the loop declares — a command it
    # can run itself and/or named deliverable files it can read — so a goal that names concrete
    # artifacts is scored on observed ground truth, not the worker's narration. A loop with neither
    # stays transcript-only.
    cfg = cfg0
    deliverables = [str(d).strip() for d in (cfg.get("deliverables", []) or []) if str(d).strip()]
    # The judge must read the SAME dir the worker wrote to — workspace_dir when the loop bound a
    # codebase, else the project context dir (an open-ended goal usually has no explicit
    # workspace_dir). Using loop.workspace_dir directly would miss the deliverable for the common
    # context-dir case and silently defeat the ground-truth read (observed live: goal 0fef190e had
    # workspace_dir='' + a deliverable).
    from personalclaw.loop.loop import effective_dir

    # The worker may write the deliverable to the loop's OWN dir when no workspace is bound
    # (observed live V6: an unbound open-ended loop wrote REPORT.md to the loop dir, so a
    # workspace-only ground-truth read wrongly reported "no proof it exists"). Give the judge the
    # loop dir as a fallback search location + resolve the policy's canonical deliverable when the
    # loop declared none, so the ground-truth read matches the same file the watchdog graduates.
    _loop_dir = store.safe_loop_dir(loop.id)
    fallback_dirs = [str(_loop_dir)] if _loop_dir is not None else []
    primary = str(cfg.get("primary_deliverable", "") or "").strip()
    canonical = primary or spec.ground_truth_deliverable
    gt_deliverables = deliverables or ([canonical] if canonical else [])
    verify_command = _command(loop, spec)
    try:
        verdict = await judge_mod.assess_cycle(
            loop.task,
            loop.success_criteria or "",
            finding,
            findings[:-1],
            verify_command=verify_command,
            workspace=effective_dir(loop) or None,
            deliverables=gt_deliverables,
            fallback_dirs=fallback_dirs,
        )
    except Exception:
        verdict = None
    if verdict is None:
        # No verdict → can't quality-assess. None (defer) — NOT a clean False — so the watchdog can
        # flag the done-ness brain as degraded (G3) rather than silently never completing. Budget
        # still bounds a capped loop.
        return None
    # P4 adversarial-skeptic: a HIGH-stakes verdict (a claimed completion or a claimed regression)
    # must survive a second independent judge told to REFUTE it before the supervisor acts on it. A
    # lone judge that hallucinates "done" would otherwise complete the loop on
    # plausible-but-wrong grounds; the skeptic is the majority-of-two guard. Non-consequential
    # cycles skip it (cost is paid only where it changes a decision).
    if verdict.done or verdict.regressed:
        try:
            skeptic = await judge_mod.assess_cycle_skeptic(
                loop.task,
                loop.success_criteria or "",
                finding,
                findings[:-1],
                verify_command=verify_command,
                workspace=effective_dir(loop) or None,
                deliverables=gt_deliverables,
                fallback_dirs=fallback_dirs,
            )
        except Exception:
            skeptic = None
        # The asymmetric merge is the CONTRACT's rule, not a loop-local one (WF2LOO-16): a done
        # needs two yeses, a regression needs only one.
        from personalclaw.workflows.judge_contract import adjudicate

        verdict = adjudicate(verdict, skeptic)
    trail = store.record_marginal_score(loop.id, verdict.marginal_value)
    # Keep the quality trail alongside (the calibrated band's variance sample + a future
    # quality-regression signal); we read the marginal trail for exhaustion.
    store.record_quality_score(loop.id, verdict.quality_score)
    granularity = str(cfg.get("granularity", "balanced"))
    # Record the calibrated band on the verdict for observability (what bar this cycle's marginal
    # value was actually judged against), then persist the verdict.
    from personalclaw.loop.granularity import calibrated_band, dial_for

    _setting = dial_for(granularity)
    if _setting is not None:
        verdict.band_used = calibrated_band(trail, _setting.threshold)
    store.write_verdict(loop.id, cycle, {"cycle": cycle, **verdict.to_dict()})
    if verdict.done:
        return True
    # P4 variance-aware exhaustion: the per-cycle bar is max(2σ, dial-threshold), so a noisy
    # marginal signal must fall further below the line before the loop calls it done — guarding
    # against completing on a variance dip. Falls back to the fixed dial until the trail is long
    # enough to trust its own σ.
    if returns_exhausted_calibrated(trail, granularity):
        return True
    return False


def has_done_check(loop: Loop, policy: SupervisorPolicy) -> bool:
    """Whether this loop HAS a point-in-time done-check at all.

    ``None`` from :func:`done_signal` has two meanings, and only one is a degradation: (a) a loop
    that HAS a check genuinely couldn't assess (judge errored / command un-runnable) → surface it;
    (b) a loop that has NO such check for its config (a General loop with no command) → deferring
    to budget BY DESIGN, not a failure. Only (a) may raise "done-ness check unavailable".
    """
    spec = policy.convergence
    if spec.signal == DONE_ORCHESTRATED:
        # Done-ness is the orchestration hook's, so there is no point-in-time check to be
        # unavailable. This is also the no-registered-strategy case, which published nothing.
        return False
    if spec.signal == DONE_VERIFY_COMMAND and spec.done_check_optional:
        return bool(_command(loop, spec).strip())
    return True


def budget_stop_is_genuine(policy: SupervisorPolicy) -> bool:
    """Whether reaching the cycle budget is a CLEAN completion rather than the error-flavoured
    "stopped before the goal was met". True where the budget IS the intended stopping condition
    (a monitor's watch window), so the cockpit shows a clean completion for an inherently-ongoing
    loop that ran its course."""
    return bool(policy.convergence.budget_stop_is_genuine)


def stagnation_enabled(policy: SupervisorPolicy) -> bool:
    """Whether the stall signal applies. A monitor goal's quiet cycle is a valid no-op."""
    return bool(policy.convergence.stagnation_enabled)
