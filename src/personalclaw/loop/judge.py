"""The judge subagent — the third-party cycle assessor (§5.4).

A dedicated, **separate** agent — never the worker, never a member of a
multi-agent roster — assesses each cycle's output for the open-ended (§5.2) and
stuck-detector (§5.1) paths. It is the mechanism that lets us assess done-ness
with an LLM *without* violating VISION's tenet: it has no stake in the work,
having not produced it. The worker only *produces*; the judge *advises*; the
supervisor (deterministic) *decides*.

It receives the goal/DoD + the cycle's finding + a compact digest of prior
findings and emits a structured verdict:

    {done, done_reason, marginal_value (0-5), quality_score (0-5), regressed}

It runs in its own session with no write tools — it reads and judges, it doesn't
act. Built on the eval :class:`LLMJudge` (the same provider seam the ratchet
already uses) but with its own done-ness + marginal-value rubric.

**The verdict object is the contract's, not this module's (WF2LOO-16).** There used to be a
private ``CycleVerdict`` here — a third vocabulary over one decision, alongside the two
`WF2LOO-13` already merged. It is DELETED: `assess_cycle` returns a
:class:`~personalclaw.workflows.judge_contract.JudgeVerdict`, the boolean ``done`` above is
projected onto the closed enum by the contract's single `verdict_for_cycle`, and the asymmetric
skeptic merge lives with the fields it merges (`judge_contract.adjudicate`). The loop's own
addition to the contract is evidence: `_observe_ground_truth` is unchanged, and
:func:`evidence_refs_from_observation` turns what it observed into the contract's
``evidence_refs`` — proof the SUPERVISOR gathered, not a claim the worker made.
"""

from __future__ import annotations

import json
import logging
import re

from personalclaw.workflows.judge_contract import (
    JudgeVerdict,
    clamp_marginal,
    verdict_for_cycle,
)

logger = logging.getLogger(__name__)


def _digest(prior_findings: list[dict], limit: int = 8) -> str:
    """A compact digest of prior cycles' summaries (so the judge sees the trail)."""
    lines = []
    for f in prior_findings[-limit:]:
        cycle = f.get("cycle", "?")
        summary = str(f.get("summary") or f.get("key_insight") or "")[:160]
        lines.append(f"- cycle {cycle}: {summary}")
    return "\n".join(lines) or "(no prior cycles)"


async def _observe_ground_truth(
    verify_command: str,
    workspace: str | None,
    deliverables: list[str],
    fallback_dirs: list[str] | None = None,
) -> str:
    """Independently observe ground truth for the judge (Slice C / O-E2): run the goal's
    verify command and read the exit code, and read any named deliverable file's real
    content — rather than trusting the worker's narration. Returns a block to inject into
    the judge prompt (labeled supervisor-observed), or "" when there's nothing runnable/
    readable. Best-effort + bounded; never raises.

    The deliverable is searched across ``workspace`` PLUS ``fallback_dirs`` (e.g. the loop's
    own dir) — the worker may write the deliverable to the loop dir when no workspace is bound,
    exactly as the watchdog's deliverable-graduation resolves it (workspace-first, else loop dir).
    Searching only ``workspace`` made the skeptic wrongly conclude 'no proof the file exists'
    for an unbound open-ended loop whose REPORT.md lived in the loop dir (found live, V6)."""
    import os

    parts: list[str] = []
    cmd = (verify_command or "").strip()
    if cmd:
        from personalclaw.loop.gates import run_verify_command

        try:
            ok = await run_verify_command(cmd, workspace or None, label="judge-verify")
        except Exception:
            ok = None
        if ok is True:
            state = "PASSED (exit 0)"
        elif ok is False:
            state = "FAILED (non-zero exit)"
        else:
            state = "could not run (tool missing / blocked / timed out)"
        parts.append(f"Ran `{cmd}` → {state}.")
    search_dirs = [
        d for d in [(workspace or "").strip(), *(fallback_dirs or [])] if d and os.path.isdir(d)
    ]
    if search_dirs:
        for label in deliverables:
            names = re.findall(r"[\w./-]+\.[A-Za-z0-9]+", label or "")
            for n in names:
                # Resolve the file across every search dir (workspace, then fallbacks like
                # the loop dir); first hit wins.
                p = ""
                for d in search_dirs:
                    cand = os.path.join(d, n.lstrip("./"))
                    if os.path.isfile(cand):
                        p = cand
                        break
                    base = os.path.join(d, os.path.basename(n))
                    if os.path.isfile(base):
                        p = base
                        break
                if not p:
                    continue
                try:
                    with open(p, encoding="utf-8", errors="strict") as fh:
                        text = fh.read(4001)
                except (OSError, UnicodeDecodeError):
                    continue
                if not text:
                    continue
                if len(text) > 4000:
                    text = text[:4000] + "\n… (truncated)"
                parts.append(f"Read `{n}`:\n{text}")
                break  # one file per deliverable label is enough
    if not parts:
        return ""
    body = "\n\n".join(parts)
    return (
        "\n\nGROUND TRUTH the supervisor observed DIRECTLY this cycle "
        "(authoritative — weigh over the worker's report):\n" + body
    )


#: The two shapes :func:`_observe_ground_truth` emits, matched so the refs are read back out of
#: the very block the judge was shown.
_OBSERVED_COMMAND_RE = re.compile(r"^Ran `(.+?)` → (.+)$", re.M)
_OBSERVED_FILE_RE = re.compile(r"^Read `(.+?)`:$", re.M)


def evidence_refs_from_observation(observed: str) -> list[str]:
    """The ``evidence_refs`` a loop cycle can honestly cite, derived from the observation block.

    This is the loop side of the contract's proof requirement (WF2LOO-16). It PARSES what
    :func:`_observe_ground_truth` returned rather than re-deriving refs from ``verify_command``
    /``deliverables``, and that choice is the whole guarantee: a ref built from the inputs would
    claim an observation that may not have happened — the command can be missing, the file
    unreadable, the deliverable absent — and a proof that can be wrong is worse than no proof.
    Because the string parsed here is the same string the judge was shown, the refs can never
    cite evidence the judge did not see.

    Empty for a transcript-only cycle, which is honest: nothing was observed, so nothing is
    cited. That is also why the contract's PASS precondition is not applied to a loop cycle —
    see `judge_contract`'s population measurement.
    """
    text = observed or ""
    refs = [
        f"command:{command} → {state.rstrip('.')}"
        for command, state in _OBSERVED_COMMAND_RE.findall(text)
    ]
    refs += [f"file:{name}" for name in _OBSERVED_FILE_RE.findall(text)]
    return refs


def _build_prompt(
    goal: str, success_criteria: str, finding: dict, prior_findings: list[dict], observed: str = ""
) -> str:
    from personalclaw.loop.loop import finding_content
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    cycle = finding.get("cycle", "?")
    # Same canonical extraction the ratchet uses, so both score identical text.
    evidence = finding_content(finding)
    # Slice C: append the supervisor's own observation (ran the command / read the file)
    # so the judge scores ground truth, not just the worker's narration.
    if observed:
        evidence = f"{evidence}{observed}"
    metric = finding.get("metric")
    metric_line = f"\nReported metric this cycle: {json.dumps(metric)}" if metric else ""
    dod = f"\nDefinition of done: {success_criteria}" if success_criteria else ""
    # The judge prompt lives in the prompt system (bundled ``task-cycle-judge``,
    # bindable in Settings → Prompts). The conditionally-empty ``dod``/``metric_line``
    # are pre-assembled (with their leading newline) and passed through as variables.
    return (
        render_use_case_prompt(
            "cycle_judge",
            {
                "goal": goal,
                "dod": dod,
                "digest": _digest(prior_findings),
                "cycle": cycle,
                "evidence": evidence,
                "metric_line": metric_line,
            },
        )
        or ""
    )


async def assess_cycle(
    goal: str,
    success_criteria: str,
    finding: dict,
    prior_findings: list[dict],
    *,
    provider_factory=None,
    verify_command: str = "",
    workspace: str | None = None,
    deliverables: list[str] | None = None,
    fallback_dirs: list[str] | None = None,
) -> JudgeVerdict | None:
    """Assess one cycle with a separate judge subagent. Returns None on failure.

    ``provider_factory`` is injected for testing; in production it resolves the
    'reasoning' use-case (a stronger third-party check than the worker's model),
    matching the ratchet's existing judge wiring.

    Slice C (O-E2): when ``verify_command`` and/or ``deliverables`` are supplied, the
    judge INDEPENDENTLY observes ground truth — it runs the command + reads the named
    artifact files itself — and weighs that over the worker's reported finding. Absent
    those, it stays transcript-only (unchanged behavior for goals with no runnable/
    readable anchor).
    """
    if provider_factory is None:

        def provider_factory(_session_key):
            from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

            return resolve_provider_for_use_case("loops")

    from personalclaw.eval.judge import LLMJudge

    observed = ""
    if verify_command or deliverables:
        observed = await _observe_ground_truth(
            verify_command, workspace, deliverables or [], fallback_dirs
        )

    judge = LLMJudge(provider_factory)
    try:
        await judge.start()
    except Exception:
        # A degraded done-ness brain must be VISIBLE, not silent: the judge is what
        # enforces "no agent certifies its own work", so if it can't even start (its
        # 'reasoning' provider is unresolvable / unavailable) the caller returns None
        # (defer, never a false complete) — but we log WARNING so the degradation is
        # diagnosable rather than an invisible forever-defer.
        logger.warning(
            "loop judge: provider failed to start — cycle assessed as degraded "
            "(no verdict); check the 'reasoning'/'chat' model binding",
            exc_info=True,
        )
        return None
    try:
        prompt = _build_prompt(goal, success_criteria, finding, prior_findings, observed)
        # judge_turn just streams the prompt + parses {score, reason}; we reuse its
        # provider but parse our own richer verdict from the raw stream. Simpler:
        # send via judge_turn's provider directly.
        raw = await _stream(judge, prompt)
    except Exception:
        logger.warning(
            "loop judge: stream failed — cycle assessed as degraded (no verdict)", exc_info=True
        )
        return None
    finally:
        try:
            await judge.shutdown()
        except Exception:
            pass
    return _parse_verdict(raw, evidence_refs_from_observation(observed))


async def assess_cycle_skeptic(
    goal: str,
    success_criteria: str,
    finding: dict,
    prior_findings: list[dict],
    *,
    provider_factory=None,
    verify_command: str = "",
    workspace: str | None = None,
    deliverables: list[str] | None = None,
    fallback_dirs: list[str] | None = None,
) -> JudgeVerdict | None:
    """A second, adversarial judge (P4) — same third-party independence as
    :func:`assess_cycle`, but prompted to REFUTE a claimed completion/regression: it
    defaults to *not done* / *not regressed* unless the evidence is undeniable. Used to
    cross-check a high-stakes primary verdict via :func:`adjudicate`. Same ground-truth
    observation (runs the command / reads the deliverable) so the skeptic argues from the
    same facts, not a weaker view. Returns None on failure (adjudicate treats that as
    'no refutation available')."""
    if provider_factory is None:

        def provider_factory(_session_key):
            from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

            return resolve_provider_for_use_case("loops")

    from personalclaw.eval.judge import LLMJudge

    observed = ""
    if verify_command or deliverables:
        observed = await _observe_ground_truth(
            verify_command, workspace, deliverables or [], fallback_dirs
        )

    judge = LLMJudge(provider_factory)
    try:
        await judge.start()
    except Exception:
        logger.warning(
            "loop judge (skeptic): provider failed to start — no refutation available",
            exc_info=True,
        )
        return None
    try:
        prompt = _build_skeptic_prompt(goal, success_criteria, finding, prior_findings, observed)
        raw = await _stream(judge, prompt)
    except Exception:
        logger.warning(
            "loop judge (skeptic): stream failed — no refutation available", exc_info=True
        )
        return None
    finally:
        try:
            await judge.shutdown()
        except Exception:
            pass
    return _parse_verdict(raw, evidence_refs_from_observation(observed))


def _build_skeptic_prompt(
    goal: str, success_criteria: str, finding: dict, prior_findings: list[dict], observed: str = ""
) -> str:
    """The refute-prompt for the skeptic judge. Bundled ``cycle_judge_skeptic`` (bindable in
    Settings → Prompts); falls back to wrapping the primary judge prompt with an explicit
    refutation directive if the skeptic prompt isn't registered, so the feature never silently
    no-ops on a fresh prompt store."""
    from personalclaw.loop.loop import finding_content
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    cycle = finding.get("cycle", "?")
    evidence = finding_content(finding)
    if observed:
        evidence = f"{evidence}{observed}"
    metric = finding.get("metric")
    metric_line = f"\nReported metric this cycle: {json.dumps(metric)}" if metric else ""
    dod = f"\nDefinition of done: {success_criteria}" if success_criteria else ""
    vars_ = {
        "goal": goal,
        "dod": dod,
        "digest": _digest(prior_findings),
        "cycle": cycle,
        "evidence": evidence,
        "metric_line": metric_line,
    }
    rendered = render_use_case_prompt("cycle_judge_skeptic", vars_)
    if rendered:
        return rendered
    # Fallback: reuse the primary rubric + a hard refutation preamble (same JSON verdict shape).
    base = render_use_case_prompt("cycle_judge", vars_) or ""
    return (
        "You are a SKEPTICAL reviewer. Another judge claimed this cycle is DONE or has "
        "REGRESSED. Your job is to REFUTE that claim. Set done=true ONLY if completion is "
        "undeniable from the evidence/ground-truth; set regressed=true ONLY if a regression "
        "is unmistakable. When in doubt, done=false and regressed=false. Return the SAME JSON "
        "verdict shape.\n\n" + base
    )


async def _stream(judge, prompt: str) -> str:
    """Stream a prompt through the judge's provider and collect the text."""
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_PERMISSION_REQUEST, EVENT_TEXT_CHUNK

    provider = judge._provider
    if provider is None:
        raise RuntimeError("judge provider not started")
    chunks: list[str] = []
    async for event in provider.stream(prompt):
        if event.kind == EVENT_TEXT_CHUNK:
            chunks.append(event.text)
        elif event.kind == EVENT_PERMISSION_REQUEST:
            # The judge has no write tools; reject anything it tries to call.
            if event.request_id:
                await provider.reject_tool(event.request_id)
        elif event.kind == EVENT_COMPLETE:
            break
    return "".join(chunks)


def _parse_verdict(raw: str, evidence_refs: list[str] | None = None) -> JudgeVerdict | None:
    """Parse the loop judge's JSON answer into the CONTRACT's verdict record.

    The prompt asks for ``done``/``regressed`` booleans, so the closed enum is derived here by
    the contract's one projection (`verdict_for_cycle`) rather than being asked for a second
    time in a second spelling — asking a model to restate a fact it already stated is how a
    third vocabulary starts. ``evidence_refs`` are the supervisor's, threaded in from
    :func:`evidence_refs_from_observation`; the judge is never asked to cite proof, because the
    bundled prompt does not offer it the field.
    """
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        logger.warning("judge returned unparseable verdict: %s", (raw or "")[:200])
        return None
    try:
        data = json.loads(m.group())
    except (json.JSONDecodeError, ValueError):
        logger.warning("judge returned unparseable verdict: %s", (raw or "")[:200])
        return None
    if not isinstance(data, dict):
        return None
    done = bool(data.get("done") is True)
    regressed = bool(data.get("regressed") is True)
    return JudgeVerdict(
        verdict=verdict_for_cycle(done, regressed),
        done_reason=str(data.get("done_reason", "")).strip()[:500],
        marginal_value=clamp_marginal(data.get("marginal_value")),
        quality_score=clamp_marginal(data.get("quality_score")),
        regressed=regressed,
        reasoning=str(data.get("reasoning", "")).strip()[:1000],
        evidence_refs=list(evidence_refs or []),
    )
