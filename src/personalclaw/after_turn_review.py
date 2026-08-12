"""After-turn self-improvement review — continuous, per-turn learning.

PClaw learns at consolidation time (session-end, batched). This adds a
**continuous** layer: after a learning-worthy turn, a bounded background review
captures durable memory — so a correction at turn 3 is learned before turn 4,
not reconstructed at session end. Absorbs the memory half of the old
"auto-capture corrections" port (the correction heuristic lives here).

Two hard guardrails (the difference between learning and self-sabotage):
- **Never learn environment-dependent failures** — "tool X is broken", "not
  allowed here", "command failed" harden into refusals the agent later cites
  against itself. A deny-filter blocks these from becoming durable memory.
- **Skip sensitive / incognito / temporary sessions.**

**Eligibility is not decided here.** ``LearningGate``
(:mod:`personalclaw.learning.gate`) computes it once per event and every cadence
consumes that one decision — this module used to own a ``should_review`` that
callers recomputed independently, which is how two capture paths in the same turn
came to disagree. What stays here is what is genuinely this module's own: the
correction heuristic, the environment-failure deny-filter, and the capture itself.

Writes flow through ``write_lesson`` (→ the contradiction judge from #18), so a
captured correction is deduped + contradiction-checked like any other lesson.

The heuristic + guardrail are pure, testable functions; the actual capture
(``run_after_turn_review``) is best-effort and never blocks the turn.
"""

from __future__ import annotations

import logging
import re

from personalclaw.security import fence_untrusted

logger = logging.getLogger(__name__)

# Strong correction signals — these read as a correction of the prior turn
# wherever they appear (a user rarely writes "wrong"/"that's not"/"why did you"
# except to push back on what the assistant just did).
_STRONG_CORRECTION_RE = re.compile(
    r"\b(nope|wrong|incorrect|that'?s not|not what|why did you|i said|"
    r"you should(?:n'?t| not))\b",
    re.IGNORECASE,
)

# Directional/negation signals — genuine corrections when they OPEN the message
# ("No, …", "Don't do that", "Actually …", "Stop", "Instead …"), but the SAME
# words mid-sentence are usually task INSTRUCTIONS, not corrections of the prior
# turn (e.g. "… do not use tools", "remember to never commit secrets",
# "summarize and don't include code"). So these only count when the message
# *opens* with them (optionally after a polite lead-in like "please"/"ok"/"hey").
_OPENING_CORRECTION_RE = re.compile(
    r"^(?:please\s+|ok(?:ay)?[,\s]+|hey[,\s]+|well[,\s]+|um+[,\s]+)?"
    r"(no|nope|don'?t|do not|stop|actually|instead|never|rather)\b",
    re.IGNORECASE,
)

# Phrases that mark a candidate lesson as an ENVIRONMENT failure — must NOT be
# learned as durable memory (they become self-citing refusals).
_ENV_FAILURE_RE = re.compile(
    r"\b(is (?:broken|down|unavailable|not (?:working|installed|available|allowed))|"
    r"not allowed here|no permission|permission denied|can'?t access|cannot access|"
    r"failed to (?:connect|run|start)|times? out|timed out|not configured|"
    r"doesn'?t (?:work|exist)|tool .* (?:broken|unavailable|missing)|exit code|"
    r"command (?:failed|not found)|"
    # Transport + throttling vocabulary (S74). Measured against real failure text: the list above
    # caught 1 of 4 environment failures, so "connection refused", ECONNRESET and rate-limit noise
    # would all have become DURABLE LESSONS — the exact outcome this guardrail exists to prevent.
    # LEARNING-FLYWHEEL §3.3 routes every `step_failed` through here, which makes the gap worse
    # than it was: a flaky network would teach the agent to refuse a valid action later.
    r"connection (?:refused|reset|aborted|closed)|"
    r"econnrefused|econnreset|econnaborted|etimedout|ehostunreach|enetunreach|enotfound|"
    # `rate limited` (past tense) is a failure REPORT; bare "rate limiter" is ordinary
    # vocabulary, and a bare `429` filtered the legitimate lesson "the 429 rate limiter
    # config lives in settings.py" — measured. A status code only counts with failure context.
    r"rate ?limited|being rate ?limited|too many requests|"
    r"429 (?:from|error|response|status)|status 429|"
    r"(?:5[0-9]{2}) (?:from|error|response)|bad gateway|service unavailable|"
    r"temporarily unavailable|network (?:error|unreachable|is unreachable)|"
    r"ssl (?:error|handshake)|certificate (?:expired|verify failed)|"
    r"quota exceeded|insufficient quota|no such host|dns (?:failure|error))\b",
    re.IGNORECASE,
)


def is_correction_signal(user_message: str) -> bool:
    """True if a user message looks like a correction of the prior turn.

    Strong signals ("wrong", "that's not", "why did you") count anywhere.
    Directional negations ("no", "don't", "actually", "stop", "never", "instead")
    only count when they LEAD the message — mid-sentence they are almost always
    task instructions ("…do not use tools", "remember to never commit secrets"),
    not corrections, and treating them as corrections poisons the lesson store
    with "User correction to honor: <the whole instruction>".
    """
    if not user_message:
        return False
    if _STRONG_CORRECTION_RE.search(user_message):
        return True
    return bool(_OPENING_CORRECTION_RE.match(user_message.lstrip()))


def is_environment_failure_claim(text: str) -> bool:
    """True if ``text`` is an environment-dependent failure not worth learning.

    The non-negotiable guardrail: these claims must never become a durable
    lesson/skill, or the agent learns to refuse valid actions later.
    """
    return bool(text and _ENV_FAILURE_RE.search(text))


def record_procedural_outcomes(service, outcomes, *, scope_ref: str | None = None) -> int:
    """Mine this turn's ``(tool, outcome)`` pairs into procedural memory (M5d).

    ``outcome`` comes from the runtime's drain and must be a member of
    ``memory_service.PROCEDURAL_OUTCOMES``; an unknown value is DROPPED and logged
    rather than stored, because a row no surfacing rule classifies is a row nothing
    will ever read.

    Records one observation per DISTINCT (tool, outcome) — successes become
    'tool X works for this shape' priors, while failures and denials feed
    failure-synthesis (they are never surfaced raw). Returns the count recorded.
    Best-effort; never raises into the turn."""
    from personalclaw.memory_service import PROCEDURAL_OUTCOMES

    if service is None or not getattr(service, "has_vector", False) or not outcomes:
        return 0
    seen: set[tuple[str, str]] = set()
    n = 0
    for tool, outcome in outcomes:
        if outcome not in PROCEDURAL_OUTCOMES:
            logger.warning("procedural capture: unknown outcome %r for %s", outcome, tool)
            continue
        sig = (tool, outcome)
        if sig in seen:
            continue
        seen.add(sig)
        try:
            # task_shape kept coarse (the tool itself) for v1 — the value is the
            # tool×outcome prior, refined by recurrence/heat, not a per-call log.
            if service.record_procedural(
                tool=tool, task_shape=tool, outcome=outcome, scope_ref=scope_ref
            ):
                n += 1
        except Exception:
            logger.debug("procedural capture failed for %s", tool, exc_info=True)
    if n:
        logger.info("Procedural memory: captured %d tool-outcome prior(s)", n)
    return n


def capture_preference_facet(service, user_message: str) -> str | None:
    """No-LLM preference-facet capture (C15): run the cheap heuristic detector over the
    user message and upsert a typed, decaying facet when it fires — a "never do X" →
    veto (routed to write_lesson), a style nudge → a style facet. Reinforces on
    recurrence via upsert. Best-effort; returns the facet text learned, or None.

    Reuses the after-turn pass (no new LLM call). Vetoes unify with the lesson store
    (upsert_facet returns None for veto; the caller writes the lesson)."""
    if service is None or not getattr(service, "has_vector", False):
        return None
    try:
        from personalclaw.preference_facets import detect_facet_candidate, upsert_facet
    except Exception:
        return None
    cand = detect_facet_candidate(user_message or "")
    if not cand:
        return None
    cls, text, cue = cand
    vs = getattr(service, "_vs", None)
    if vs is None:
        return None
    try:
        if cls == "veto":
            # Vetoes live in ONE place — the lesson store (+ contradiction judge).
            service.write_lesson(f"Never: {text}", category="preference", source="facet_veto")
            return text
        upsert_facet(vs, cls, text, cue=cue)
        return text
    except Exception:
        logger.debug("preference-facet capture failed", exc_info=True)
        return None


def run_after_turn_review(
    *,
    service,
    user_message: str,
    assistant_text: str,
    correction: bool,
    judge=None,
    capture_facets: bool = True,
) -> str | None:
    """Best-effort: capture a durable lesson from a corrected turn. Returns the
    learned text (for the chip) or None.

    Scope (deliberately narrow for v1): the high-signal **correction** case — a
    user correction + the agent's adjusted behavior become a lesson, UNLESS it's
    an environment-failure claim (guardrail). The write goes through
    ``write_lesson`` so it's deduped + contradiction-judged (#18). The broader
    LLM skill-ladder review layers on later; this lands the timely memory win
    + the guardrail that protects the whole learning loop.

    Also runs the no-LLM preference-facet detector (C15) on EVERY reviewed turn (not
    just corrections) — a style nudge / veto becomes a typed decaying facet that the
    ambient USER PROFILE block renders.
    """
    # Preference facets: cheap heuristic, runs regardless of the correction gate (a
    # style nudge like "keep it shorter" isn't a correction-signal but IS a facet).
    # The dashboard hot path captures facets BEFORE this expensive-review gate (so a
    # toolless conversational hint isn't dropped) and passes capture_facets=False to
    # avoid a double-upsert; direct/test callers keep the default.
    if capture_facets:
        capture_preference_facet(service, user_message)
    if service is None or not service.has_vector or not correction:
        return None
    correction_text = (user_message or "").strip()
    if not correction_text:
        return None
    # GUARDRAIL: never learn an environment-dependent failure as durable memory.
    if is_environment_failure_claim(correction_text) or is_environment_failure_claim(
        assistant_text
    ):
        logger.info("after-turn review: skipped env-failure claim (guardrail)")
        return None
    # Frame the correction as a forward-looking lesson.
    rule = f"User correction to honor: {correction_text[:240]}"
    try:
        if judge is not None:
            service.set_contradiction_judge(judge)
        ok = service.write_lesson(rule, category="preference", source="after_turn_review")
    except Exception:
        logger.debug("after-turn review: write_lesson failed", exc_info=True)
        return None
    if ok:
        logger.info("after-turn review: learned a correction")
        return rule
    return None


# ── 4-tier skill ladder (the forked-LLM skill axis) ──────────────────────────
# The deferred skill half of the after-turn review. A bounded one-shot LLM call
# inspects a learning-worthy turn and, following a preference LADDER (bias toward
# refining what exists over minting new), decides at most ONE skill action. Every
# create/refine it proposes routes through the propose-only review QUEUE
# (skill-evolution-proposal-only) — it NEVER writes a skill live, preserving the
# "autonomous synthesis proposes, humans install" invariant.

_LADDER_SCHEMA_HINT = (
    '{"action": "none|refine|support_file|create|template", '
    '"slug": "kebab-case-skill-name", "description": "one line", '
    '"triggers": "comma, separated", "procedure_md": "the steps", '
    '"steps": ["one step per entry (template only)"], '
    '"target": "existing skill name (refine/support_file only)", '
    '"rationale": "why, one line"}'
)


def _build_ladder_prompt(
    *, user_message: str, assistant_text: str, loaded_skills: list[str]
) -> str:
    loaded = ", ".join(loaded_skills) if loaded_skills else "(none loaded this turn)"
    return (
        "You review one completed assistant turn and decide whether a REUSABLE "
        "how-to-do-a-class-of-task skill should be captured. Follow this preference "
        "ladder and pick the EARLIEST that fits (bias hard toward refining what "
        "exists over creating new):\n"
        "  1. refine — improve a currently-loaded skill.\n"
        "  2. refine — improve an existing umbrella skill (name it in 'target').\n"
        "  3. support_file — add a reference/template to an existing skill.\n"
        "  4. create — mint a NEW skill (last resort, only for a genuinely new class).\n"
        "  5. template — the turn ran a repeatable multi-step PROCEDURE (a plan someone "
        "would run again with different inputs) rather than teaching a how-to. Put one "
        "step per entry in 'steps'. A deterministic gate scores these, so list the real "
        "steps and use {{placeholders}} wherever a value would change between runs.\n\n"
        "Return STRICT JSON, no prose:\n" + _LADDER_SCHEMA_HINT + "\n\n"
        "Rules: action='none' unless the turn genuinely taught a reusable procedure "
        "(most turns are 'none'). NEVER capture environment-specific failures, tool "
        "errors, or 'X is broken/not allowed' — those are not skills. Keep procedure_md "
        "concrete and generalizable.\n\n"
        f"Currently-loaded skills: {loaded}\n\n"
        # Fence via `security.fence_untrusted` rather than hand-built markers: the
        # shared helper neutralises an embedded close marker, so turn content that
        # itself contains `</untrusted_content>` cannot close the fence early and
        # smuggle trailing instructions into a review that mints skills.
        + fence_untrusted(
            f"USER: {user_message[:1500]}\n\nASSISTANT: {assistant_text[:2500]}",
            source="turn",
        )
    )


def _parse_ladder_json(raw: str) -> dict | None:
    """Extract the JSON object from a one-shot response (tolerant of code fences)."""
    import json

    if not raw:
        return None
    text = raw.strip()
    # Strip a ```json fence if present.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    # Grab the outermost {...} if there's leading/trailing chatter.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


async def _template_already_surfaced(slug: str) -> bool:
    """Whether a workflow definition for ``slug`` already exists.

    Resolved against the real def registry rather than left at the dataclass default. The gate's
    ``TEMPLATE_EXISTS`` branch is the one pre-gate that depends on library state, so leaving it
    False would make that branch unreachable in production — a skip reason that can never fire is
    indistinguishable from a gate that does not work.
    """
    if not slug:
        return False
    try:
        from personalclaw.workflows.service import list_defs

        result = await list_defs()
    except Exception:
        logger.debug("template gate: def listing unavailable", exc_info=True)
        return False
    wanted = slug.strip().lower()
    return any(str(d.get("name", "")).strip().lower() == wanted for d in result.get("defs") or [])


async def _review_template_candidate(decision: dict, *, session_key: str) -> str | None:
    """The ladder's fifth branch: route a procedure-shaped turn through the ad-hoc→template gate.

    The other four branches enqueue a SKILL proposal directly. This one does not decide anything
    itself — it hands the candidate to ``learning.template_gate``, which owns the chain and its
    typed refusal ledger. A refusal is a real result here: it returns None (no chip) but the reason
    is recorded, which is the whole point of §3.2's negative space.
    """
    from personalclaw.learning.detectors import Candidate
    from personalclaw.learning.template_gate import evaluate

    raw_steps = decision.get("steps")
    steps = (
        [str(s).strip() for s in raw_steps if str(s).strip()] if isinstance(raw_steps, list) else []
    )
    slug = str(decision.get("slug", "")).strip()
    description = str(decision.get("description", "")).strip()
    if not steps or not slug:
        return None
    # Redact before the steps reach the gate: an accepted candidate becomes a proposal body, and the
    # same posture the skill branches use applies to a template's text.
    try:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        cleaned: list[str] = []
        for step in steps:
            step, _ = redact_exfiltration_urls(step)
            step, _ = redact_credentials(step)
            cleaned.append(step)
        steps = cleaned
    except Exception:
        pass

    candidate = Candidate(
        run_id=session_key,
        steps=steps,
        template_surfaced=await _template_already_surfaced(slug),
        intent=description,
    )
    outcome = evaluate(
        candidate,
        session_key=session_key,
        title=description or f"Template: {slug}",
        body="\n".join(f"- {s}" for s in steps),
    )
    logger.info(
        "template gate: %s for %s (score %.2f, recorded=%s)",
        outcome.decision.skip_reason or outcome.decision.action,
        slug,
        outcome.decision.score.total,
        outcome.recorded,
    )
    if not outcome.filed:
        return None
    return f"Proposed template: {slug}"


async def run_skill_ladder_review(
    *,
    session_key: str,
    user_message: str,
    assistant_text: str,
    loaded_skills: list[str],
    completion=None,
) -> str | None:
    """Forked-LLM skill-axis review (5-tier ladder). Enqueues at most one skill or
    template PROPOSAL (never writes live) and returns a short summary for the chip, or None.

    The fifth tier is the ad-hoc→template branch: it routes a procedure-shaped turn through
    ``learning.template_gate`` instead of the skill queue, because a repeatable plan and a how-to
    are different artifacts and the gate that judges plans is deterministic.

    ``completion`` is an injectable ``async (prompt)->str`` (defaults to
    ``one_shot_completion``) so tests drive it without a real model. Best-effort;
    never raises into the turn."""
    # Guardrail up front: an env-failure turn can't teach a skill.
    if is_environment_failure_claim(user_message) or is_environment_failure_claim(assistant_text):
        return None
    if completion is None:
        from personalclaw.llm_helpers import one_shot_completion

        async def completion(p: str) -> str:  # noqa: E306
            return await one_shot_completion(p, use_case="background")

    prompt = _build_ladder_prompt(
        user_message=user_message,
        assistant_text=assistant_text,
        loaded_skills=loaded_skills,
    )
    try:
        raw = await completion(prompt)
    except Exception:
        logger.debug("skill-ladder review: completion failed", exc_info=True)
        return None
    decision = _parse_ladder_json(raw)
    if not decision:
        return None
    action = str(decision.get("action", "none")).strip().lower()
    if action == "template":
        return await _review_template_candidate(decision, session_key=session_key)
    if action not in ("refine", "support_file", "create"):
        return None  # 'none' or garbage → nothing to learn

    slug = str(decision.get("slug", "")).strip()
    description = str(decision.get("description", "")).strip()
    procedure_md = str(decision.get("procedure_md", "")).strip()
    triggers = str(decision.get("triggers", "")).strip()
    target = str(decision.get("target", "")).strip()
    if not slug or not description or not procedure_md:
        return None
    # Redact before it touches the queue (same posture as consolidation).
    try:
        from personalclaw.security import redact_credentials, redact_exfiltration_urls

        procedure_md, _ = redact_exfiltration_urls(procedure_md)
        procedure_md, _ = redact_credentials(procedure_md)
    except Exception:
        pass

    # Everything routes through the propose-only queue — never a live write. The
    # ladder tier + target ride along as provenance for the reviewer.
    try:
        from personalclaw.skills import proposals
        from personalclaw.skills.loader import AutoSkillProvenance

        prop = proposals.enqueue(
            slug=slug,
            description=description,
            triggers=triggers,
            procedure_md=procedure_md,
            session_key=session_key,
            created_at=AutoSkillProvenance.now_iso(),
            kind="refine" if action in ("refine", "support_file") else "new",
            refine_target=target if action in ("refine", "support_file") else "",
            source_excerpt=f"[after-turn skill-ladder: {action}] {assistant_text}",
        )
    except Exception:
        logger.debug("skill-ladder review: enqueue failed", exc_info=True)
        return None
    if prop is None:
        return None
    logger.info("skill-ladder review: proposed %s (%s)", prop.slug, action)
    verb = {"refine": "refine", "support_file": "add file to", "create": "new skill"}[action]
    return f"Proposed skill ({verb}): {slug}"
