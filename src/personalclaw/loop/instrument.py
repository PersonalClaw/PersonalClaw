"""Loop self-instrumentation — the P4 "prove-the-instrument" gates.

Before an open-ended loop trusts a verdict about its own work, two guards run:

* :func:`probe_judge` (the **canary**) — feed the judge a synthetic obviously-strong
  and obviously-null cycle and assert it separates them. A judge that can't tell good
  from empty is *blind*; trusting its verdicts would emit plausible-but-wrong
  completions. On a blind judge the loop halts for the user (NEEDS_INPUT) rather than
  self-completing on garbage.
* :func:`reproduce_confirm` (the **independent REPRODUCE**) — before a completed loop's
  deliverable is graduated to a permanent artifact, re-observe ground truth (re-run the
  verify command and/or re-read + re-score the deliverable) with a *fresh* judge pass.
  If that second, independent observation disagrees with the completing verdict, the
  ship is blocked and the discrepancy surfaced — a completion is never shipped on a
  single observation.

Both are best-effort and fail SAFE: a probe that can't run defers (never a false
"blind"); a reproduce that can't run does not block a ship (never a false "blocked").
The point is to catch a *confidently wrong* instrument, not to add a new failure mode.
"""

from __future__ import annotations

import logging

from personalclaw.loop.loop import Loop, effective_dir

logger = logging.getLogger(__name__)

# The canary pair. The judge must score the strong finding's quality clearly above the
# null finding's — by at least ``_CANARY_MIN_SEPARATION`` on the 0-5 scale. These are
# deliberately unambiguous: a working judge separates them trivially; a blind/degraded
# one (mis-bound model, broken rubric) collapses them.
_CANARY_MIN_SEPARATION = 1.5

_CANARY_GOAL = "Produce a correct, well-tested implementation of the requested feature."
_CANARY_DOD = "The feature works end-to-end and its tests pass."
_CANARY_STRONG = {
    "cycle": 0,
    "summary": (
        "Implemented the feature in full, added 12 unit tests covering the happy path and "
        "edge cases, ran the suite — all 12 pass — and verified the end-to-end flow works. "
        "Concrete evidence: test output shows 12 passed; the command exits 0."
    ),
}
_CANARY_NULL = {
    "cycle": 0,
    "summary": "Did nothing this cycle. No code written, no tests, no progress. Empty.",
}


async def probe_judge(assess_fn) -> bool | None:
    """Canary-test the done-ness judge. Returns True if the judge clearly separates a
    strong cycle from a null one (instrument trustworthy), False if it does NOT (blind
    judge — the caller should halt rather than trust verdicts), or None if the probe
    itself couldn't run (defer — never a false blind).

    ``assess_fn`` is an async callable ``(goal, dod, finding, prior) -> CycleVerdict|None``
    — injected so this is unit-testable without a live model and so it reuses whichever
    judge the caller already resolves.
    """
    try:
        strong = await assess_fn(_CANARY_GOAL, _CANARY_DOD, _CANARY_STRONG, [])
        null = await assess_fn(_CANARY_GOAL, _CANARY_DOD, _CANARY_NULL, [])
    except Exception:
        logger.warning(
            "loop canary: probe could not run — deferring (not declared blind)", exc_info=True
        )
        return None
    if strong is None or null is None:
        # The judge couldn't produce a verdict for the probe → can't assess the instrument.
        # Defer (None), consistent with assess_cycle's degraded-not-error contract.
        return None
    separation = strong.quality_score - null.quality_score
    trustworthy = separation >= _CANARY_MIN_SEPARATION
    if not trustworthy:
        logger.warning(
            "loop canary: judge did NOT separate strong (q=%.1f) from null (q=%.1f) "
            "— separation %.1f < %.1f; treating judge as BLIND",
            strong.quality_score,
            null.quality_score,
            separation,
            _CANARY_MIN_SEPARATION,
        )
    return trustworthy


async def reproduce_confirm(loop: Loop) -> bool | None:
    """Independently RE-confirm a loop's completion before its deliverable ships.

    Re-observe ground truth with a fresh judge pass over the loop's latest finding
    (re-running the verify command + re-reading the named deliverables, exactly as the
    completing assessment did). Returns True if the fresh observation agrees the work is
    done, False if it DISAGREES (ship should be blocked), or None if it couldn't run
    (defer — do NOT block a ship on an un-runnable reproduce).

    The reproduce anchor is, in precedence: the goal's explicit ``verify_command`` and/or
    ``kind_config["deliverables"]``, else the KIND's canonical deliverable file (e.g. an
    open-ended goal's ``REPORT.md``) — the very file the watchdog is about to graduate. Using
    the kind deliverable as the fallback is what makes the gate real for the common open-ended
    case (which rarely declares explicit deliverables but always writes a document). A goal with
    no anchor at all (e.g. verifiable/code, where the code/check IS the output) returns None.
    """
    from personalclaw.loop import judge as judge_mod
    from personalclaw.loop import kinds as kinds_mod
    from personalclaw.loop import store

    cfg = loop.kind_config or {}
    verify_command = str(cfg.get("verify_command", "")).strip()
    deliverables = [str(d).strip() for d in (cfg.get("deliverables", []) or []) if str(d).strip()]
    if not deliverables:
        # Fall back to the kind's canonical deliverable (open_ended → REPORT.md, etc.) so the
        # reproduce gate actually covers the common open-ended path, not just the rare goal
        # that declared an explicit deliverable. Best-effort; a kind with no deliverable_name
        # (verifiable) contributes nothing here.
        try:
            kinds_mod.ensure_loaded()
            strat = kinds_mod.get_or_none(loop.kind)
            namer = getattr(strat, "deliverable_name", None)
            kind_deliverable = (namer(loop) if namer else "") or ""
            if kind_deliverable:
                deliverables = [kind_deliverable]
        except Exception:
            logger.debug(
                "reproduce: kind deliverable_name lookup failed for %s", loop.id, exc_info=True
            )
    if not verify_command and not deliverables:
        return None  # no independent anchor to reproduce against

    findings = store.get_findings(loop.id)
    if not findings:
        return None
    finding = findings[-1]
    # The deliverable may live in the loop's own dir (unbound loop) — give the fresh pass the
    # loop dir as a fallback search location, matching the watchdog's deliverable resolution.
    loop_dir = store.safe_loop_dir(loop.id)
    fallback_dirs = [str(loop_dir)] if loop_dir is not None else []
    try:
        verdict = await judge_mod.assess_cycle(
            loop.task,
            loop.success_criteria or "",
            finding,
            findings[:-1],
            verify_command=verify_command,
            workspace=effective_dir(loop) or None,
            deliverables=deliverables,
            fallback_dirs=fallback_dirs,
        )
    except Exception:
        logger.warning("loop reproduce: fresh judge pass failed — not blocking ship", exc_info=True)
        return None
    if verdict is None:
        return None
    # The fresh pass agrees the work is shippable if it independently reads done OR it
    # scores the deliverable at genuine quality (a completing loop that the reproduce
    # scores as empty is the disagreement we want to catch).
    return bool(verdict.done or verdict.quality_score >= 2.0)
