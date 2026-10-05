"""The ONE loop supervisor — one evaluator over a declared policy.

A loop's done-ness used to be pluggable Python: a ``LoopKindStrategy.is_done_signal`` per kind
plus two satellite hooks (``has_done_check``, ``budget_stop_genuine``) the watchdog reached for
with ``getattr``, and a third rule (``_stagnation_disabled``) hard-coded in the watchdog against
one kind's name. Five implementations, three lookup styles, and no single place that answered
"what completes this loop?".

This module is that place. It reads a :class:`~personalclaw.workflows.supervisor_policy.\
SupervisorPolicy` and dispatches its
``convergence.signal`` to the ONE implementation of that mechanism. The domain knowledge that
used to be spread over five modules is now DATA in
:data:`~personalclaw.workflows.supervisor_policy.KIND_CONVERGENCE`; the code below is
kind-agnostic and never branches on ``loop.kind``.

**No new vocabulary.** A done-signal names which MECHANISM produces the answer; what a judge
actually decided still travels in ``judge_contract``'s verdict types, adjudicated by
``judge_contract.adjudicate``. Those dialects are already reconciled and this module adds none.

**The tenet is unchanged: no agent certifies its own work.** Every mechanism here is the
SUPERVISOR's own read — a command it runs, a judge subagent it commissions — never the worker's
self-report. The watchdog still owns the lifecycle decision; this only supplies the signal.
"""

from __future__ import annotations

import logging

from personalclaw.loop import files as loop_files
from personalclaw.loop.loop import Loop
from personalclaw.workflows.judge_contract import verdict_for_cycle
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
    """The deterministic mechanism: RUN the declared command where the loop's work is, and read
    its exit code.

    The folder is :func:`~personalclaw.loop.loop.effective_dir`: where the worker's files land
    (the bound workspace, a greenfield code loop's own folder, the project's context folder, else
    the workspace root), the folder the loop's page names and the worker's brief tells it the
    check runs in. Run with no folder, a relative check ran wherever the gateway process had been
    started, failed every cycle on a file that was there, and the loop could never finish.

    Every cycle's check, and the judge's answer when one is asked, is recorded as that cycle's
    verdict on the loop's ledger (:func:`_record_check`), including why a check that could not
    run could not. An unset command on a kind whose check is optional (a General loop with none)
    yields ``None`` with nothing recorded, which is how it defers to budget by design.
    """
    from personalclaw.loop.gates import CheckReport, refusal, run_verify_command
    from personalclaw.loop.loop import effective_dir

    command = _command(loop, spec)
    if not command.strip() and spec.done_check_optional:
        return None
    where = effective_dir(loop)
    report = CheckReport()
    ok = await run_verify_command(command, where or None, label="verify", report=report)
    if ok is True:
        outcome = "passed"
    elif ok is False:
        outcome = "failed"
    else:
        # Refused by the shell denylist is not "could not run": no later cycle changes it, and the
        # watchdog pauses the loop with the rule (`refused_check`).
        outcome = "refused" if refusal(command, cwd=where or "") else "not_run"
    check = {
        "command": command,
        "dir": where,
        "outcome": outcome,
        "exit_code": report.exit_code,
        "output": report.output,
        "not_run": report.not_run,
    }
    # The check passed — but a worker can point the command at a SUBSET of a multi-criterion goal
    # (e.g. `npm test` green after only the engine phase, while the AI / UI sub-goals are unbuilt).
    # A green command on a partial build then falsely completes the whole goal (observed live: goal
    # b7abd778 marked done after phase 1/3). So when the loop declares MORE THAN ONE criterion, the
    # command passing is necessary but not sufficient — a separate judge must confirm every
    # criterion is met before we call it done.
    criteria = _criteria(loop, spec)
    if ok is not True or len(criteria) <= 1:
        # False (the check ran + failed) / None (couldn't run) → not done yet; True with a
        # single/no criterion → the command IS the whole goal.
        _record_check(loop, findings, check, judged=len(criteria) > 1)
        return ok
    done, judge = await _all_criteria_met(loop, criteria, findings, check)
    _record_check(loop, findings, check, judged=True, judge=judge)
    return done


async def _all_criteria_met(
    loop: Loop, criteria: list[str], findings: list[dict], check: dict
) -> tuple[bool | None, dict]:
    """A strict judge over a verifiable loop's criteria: PASS only if the evidence from completed
    cycles shows EVERY criterion is met. Guards against a green command on a partial build. It is
    shown the check's own result beside the cycles' reports: what it judges is the work's result,
    not only the worker's account of it.

    Returns its decision — True (all met), False (>=1 unmet → keep going), or None (no answer →
    defer; the watchdog still bounds by budget) — and its answer for the cycle's verdict.
    Conservative: any ambiguity is NOT a pass.
    """
    from personalclaw.loop.gates import judge_verdict, verdict_is_pass, verdict_rendered
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    recent = findings[-6:]
    evidence = "\n".join(
        f"- cycle {f.get('cycle')}: {str(f.get('summary', '') or f.get('key_insight', ''))[:300]}"
        for f in recent
    )
    printed = f", printing:\n{check['output']}" if check["output"] else "."
    evidence += (
        f"\n\nThe supervisor ran the check `{check['command']}` in {check['dir']} this cycle: "
        f"it passed (exit 0){printed}"
    )
    criteria_block = "\n".join(f"- {s}" for s in criteria)
    # The completion gate lives in the prompt system (bundled ``task-subgoal-judge``, bindable in
    # Settings → Prompts).
    prompt = render_use_case_prompt(
        "subgoal_judge",
        {"task": loop.task, "criteria": criteria_block, "evidence": evidence},
    )
    if not prompt:
        return None, {"outcome": "no_answer", "why": "its prompt could not be loaded"}
    raw = await judge_verdict(prompt, loop_id=loop.id)
    answer = " ".join((raw or "").split())[:300]
    if verdict_is_pass(raw):
        return True, {"outcome": "pass", "answer": answer}
    # A real FAIL → keep cycling. A non-verdict (judge/provider unavailable) → defer (None), NOT a
    # clean False, so the watchdog can flag a degraded done-ness brain rather than silently spin;
    # budget still caps the loop.
    if verdict_rendered(raw):
        return False, {"outcome": "fail", "answer": answer}
    why = (
        f"its answer was neither PASS nor FAIL (“{answer}”)"
        if answer
        else f"its model (the {_judge_setting()} setting in Settings → Models) could not "
        "be reached, or answered nothing"
    )
    return None, {"outcome": "no_answer", "answer": answer, "why": why}


def _judge_setting() -> str:
    """The Models page's name for the model setting a loop's judges ride."""
    from personalclaw.loop.judge import judge_use_case
    from personalclaw.providers.use_cases import USE_CASE_NAMES

    axis = judge_use_case()
    return f"“{USE_CASE_NAMES.get(axis, axis)}”"


def _record_check(
    loop: Loop, findings: list[dict], check: dict, *, judged: bool, judge: dict | None = None
) -> None:
    """Write this cycle's check — and the judge's answer, when it was asked — as the cycle's
    verdict on the loop's ledger, with the sentence its page shows.

    A check scores nothing, so the verdict carries no marginal or quality score: the returns rail
    plots judged cycles only. ``cannot_judge`` says, in words, why this cycle could not be decided
    (a check that could not run, a judge that gave no answer), and that the loop keeps going. A
    refused check carries none: the loop does not keep going, it pauses with the rule as its
    question (``gates.pause_for_refusal``)."""
    where = check["dir"]
    if check["outcome"] == "passed":
        state, sentence = "passed (exit 0)", f"The check passed in {where}"
    elif check["outcome"] == "failed":
        state = f"failed (exit {check['exit_code']})"
        sentence = f"The check failed in {where} (exit {check['exit_code']})"
    elif check["outcome"] == "refused":
        state = "refused"
        sentence = f"The check did not run in {where}: {check['not_run']}"
    else:
        state = "could not run"
        sentence = f"The check could not run in {where}: {check['not_run']}"
    cannot_judge = ""
    if judge is None:
        sentence += ", so the judge was not asked." if judged else "."
        if check["outcome"] == "not_run":
            cannot_judge = (
                f"{sentence} The loop keeps going and runs it again after its next cycle."
            )
    elif judge["outcome"] == "pass":
        sentence += ", and the judge found every sub-goal met."
    elif judge["outcome"] == "fail":
        sentence += ", but the judge found a sub-goal not met yet."
    else:
        sentence += f", but the judge gave no answer: {judge['why']}."
        cannot_judge = f"{sentence} The loop keeps going and asks again after its next cycle."
    done = check["outcome"] == "passed" and (judge is None or judge["outcome"] == "pass")
    record = {
        "verdict": verdict_for_cycle(done, False).value,
        "passed": done,
        "done": done,
        "done_reason": sentence,
        "evidence_refs": [f"command:{check['command']} → {state}"],
        "check": check,
    }
    if judge is not None:
        record["judge"] = judge
    if cannot_judge:
        record["cannot_judge"] = cannot_judge
    cycle = int(findings[-1].get("cycle", len(findings))) if findings else 0
    loop_files.write_verdict(loop.id, cycle, record)


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
    # Canary: once per loop-run, prove the done-ness judge can tell a strong cycle from an empty
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
            return await judge_mod.assess_cycle(goal, dod, fnd, prior, loop_id=loop.id)

        trustworthy = await instrument.probe_judge(_probe_assess)
        if trustworthy is not None:  # None = probe couldn't run → don't cache, retry next cycle
            store.set_kind_config_key(loop.id, "judge_calibrated", bool(trustworthy))
            if trustworthy is False:
                return None  # blind → defer; watchdog surfaces judge_blind + NEEDS_INPUT
    cycle = int(finding.get("cycle", len(findings)))
    # Give the judge whatever ground-truth anchor the loop declares — a command it
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
    # (observed live: an unbound open-ended loop wrote REPORT.md to the loop dir, so a
    # workspace-only ground-truth read wrongly reported "no proof it exists"). Give the judge the
    # loop dir as a fallback search location + resolve the policy's canonical deliverable when the
    # loop declared none, so the ground-truth read matches the same file the watchdog graduates.
    _loop_dir = loop_files.safe_loop_dir(loop.id)
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
            loop_id=loop.id,
            verify_command=verify_command,
            workspace=effective_dir(loop) or None,
            deliverables=gt_deliverables,
            fallback_dirs=fallback_dirs,
        )
    except Exception:
        verdict = None
    if verdict is None:
        # No verdict → can't quality-assess. None (defer) — NOT a clean False — so the watchdog can
        # flag the done-ness brain as degraded rather than silently never completing. Budget
        # still bounds a capped loop. The cycle says so in words: a cycle left with no verdict
        # reads as one nobody checked. It carries no score, so the returns rail skips it.
        sentence = (
            "The judge gave no verdict for this cycle: its model (the "
            f"{_judge_setting()} setting in Settings → Models) could not be reached, or its "
            "answer could not be read."
        )
        loop_files.write_verdict(
            loop.id,
            cycle,
            {
                "verdict": verdict_for_cycle(False, False).value,
                "passed": False,
                "done": False,
                "done_reason": sentence,
                "cannot_judge": f"{sentence} The loop keeps going and asks again after its "
                "next cycle.",
            },
        )
        return None
    # Adversarial skeptic: a HIGH-stakes verdict (a claimed completion or a claimed regression)
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
                loop_id=loop.id,
                verify_command=verify_command,
                workspace=effective_dir(loop) or None,
                deliverables=gt_deliverables,
                fallback_dirs=fallback_dirs,
            )
        except Exception:
            skeptic = None
        # The asymmetric merge is the CONTRACT's rule, not a loop-local one: a done
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
    loop_files.write_verdict(loop.id, cycle, {"cycle": cycle, **verdict.to_dict()})
    if verdict.done:
        return True
    # Variance-aware exhaustion: the per-cycle bar is max(2σ, dial-threshold), so a noisy
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


def refused_check(loop: Loop, policy: SupervisorPolicy) -> str:
    """Why the shell denylist refuses the command this loop's done-check runs, or "".

    A refused check is the one ``None`` from :func:`done_signal` that no later cycle changes: the
    same command is refused every time until the command or the pattern changes, so the watchdog
    pauses the loop with this reason rather than cycling on toward its budget."""
    spec = policy.convergence
    if spec.signal != DONE_VERIFY_COMMAND:
        return ""
    from personalclaw.loop.gates import refusal
    from personalclaw.loop.loop import effective_dir

    return refusal(_command(loop, spec), cwd=effective_dir(loop) or "")


def budget_stop_is_genuine(policy: SupervisorPolicy) -> bool:
    """Whether reaching the cycle budget is a CLEAN completion rather than the error-flavoured
    "stopped before the goal was met". True where the budget IS the intended stopping condition
    (a monitor's watch window), so the cockpit shows a clean completion for an inherently-ongoing
    loop that ran its course."""
    return bool(policy.convergence.budget_stop_is_genuine)


def stagnation_enabled(policy: SupervisorPolicy) -> bool:
    """Whether the stall signal applies. A monitor goal's quiet cycle is a valid no-op."""
    return bool(policy.convergence.stagnation_enabled)
