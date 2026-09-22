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
import time
from dataclasses import dataclass

from personalclaw.guardrails.audit import caller_scope
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


# ── Stumble detection (LEARNING-VISIBILITY S3) ───────────────────────────────
#
# A stumble is a turn where a skill was LOADED and the turn still went wrong. It is the
# trigger for a refine proposal, and it is a CLASSIFIER — so what it declines to fire on is
# as much of the design as what it fires on. Stated plainly:
#
#   FIRES on, and only on, three observable signals:
#     * `correction`     — the user pushed back on the previous turn (`is_correction_signal`).
#     * `failure_retry`  — one tool FAILED and the same tool was invoked again afterwards.
#                          The retry is the evidence: a single failure is a fact of life, a
#                          failure the agent had to work around is a gap in the procedure.
#     * `rejection`      — the user DENIED an action the agent asked to take (a `denied`
#                          tool outcome). The strongest signal of the three: it is the user
#                          saying no to the skill's own instruction, not to its wording.
#
#   DELIBERATELY IGNORES (each of these is a false positive this classifier must not produce):
#     * Any turn with no skill loaded — there is nothing to refine, and inventing a target
#       would file a refine proposal against a skill that had no part in the turn.
#     * Any turn whose text reads as an ENVIRONMENT failure (`is_environment_failure_claim`,
#       either side). A flaky network must never harden into a refinement, which is the same
#       guardrail the ladder applies at its own entry.
#     * A tool that failed and was never retried — an abandoned step is not a worked-around
#       procedure gap, and treating every red tool call as a stumble would fire on most turns.
#     * A `denied` outcome for a tool that also succeeded later in the turn: the user steered
#       rather than refused, and the correction (if any) is the signal that carries it.
#     * Mid-sentence negations ("…do not use tools"), which `is_correction_signal` already
#       excludes by requiring directional negations to OPEN the message.
#
# It never calls a model. A stumble is decided from the turn's own record, so the refinement
# arm has a full no-model floor: it degrades to *not proposing*, never to guessing.

STUMBLE_TRIGGERS = ("correction", "failure_retry", "rejection")


@dataclass(frozen=True)
class StumbleSignal:
    """A turn that used a skill and still went wrong.

    ``trigger`` is one of :data:`STUMBLE_TRIGGERS`; ``detail`` is a short, non-user-text
    fragment for the log (a tool name, or the empty string) — never the user's message, so a
    log line can be quoted in a bug report without carrying session content.
    """

    trigger: str
    detail: str = ""


def detect_stumble(
    *,
    user_message: str,
    assistant_text: str,
    used_skills: list[str],
    tool_outcomes: list[tuple[str, str]] | None = None,
) -> StumbleSignal | None:
    """Classify this turn as a stumble, or ``None``. See the block comment above for the
    full fires-on / ignores contract.

    ``used_skills`` must be the skills whose content actually REACHED the prompt (the
    ``ADMITTED``/``REDUCED`` allocation, which is what ``chat_runner`` already narrows for
    the LV-2 chip) — not the candidate index. The candidate list is every indexed skill, so
    passing it would make "a skill was loaded" true on every turn.

    ``tool_outcomes`` is the turn's ``(tool, outcome)`` sequence in ORDER, as drained from
    the provider. Order is load-bearing: `failure_retry` is a failure *followed by* another
    call to the same tool, and the same multiset with the calls reversed is a success the
    agent then broke, which is not a procedure gap.
    """
    if not used_skills:
        return None
    # Same guardrail, same predicate, both sides — an env failure can no more refine a skill
    # than it can teach a lesson.
    if is_environment_failure_claim(user_message) or is_environment_failure_claim(assistant_text):
        return None
    outcomes = list(tool_outcomes or [])
    if is_correction_signal(user_message):
        return StumbleSignal("correction")
    denied = _denied_without_recovery(outcomes)
    if denied:
        return StumbleSignal("rejection", denied)
    retried = _failed_then_retried(outcomes)
    if retried:
        return StumbleSignal("failure_retry", retried)
    return None


def _failed_then_retried(outcomes: list[tuple[str, str]]) -> str:
    """The first tool that FAILED and was then invoked again, or ``""``.

    The retry is what makes this a signal rather than noise: it says the agent had to work
    around the procedure it was given. A failure at the very end of the turn — nothing after
    it — is an abandoned step, and returns ``""``.
    """
    for i, (tool, outcome) in enumerate(outcomes):
        if outcome != "failed":
            continue
        if any(later == tool for later, _o in outcomes[i + 1 :]):
            return tool
    return ""


def _denied_without_recovery(outcomes: list[tuple[str, str]]) -> str:
    """The first tool the user DENIED and that never succeeded afterwards, or ``""``.

    A denial the agent recovered from (the same tool succeeding later) is the user steering a
    parameter, not refusing the procedure — so it is not a stumble. Only a denial that stood
    is the user saying no to what the skill told the agent to do.
    """
    for i, (tool, outcome) in enumerate(outcomes):
        if outcome != "denied":
            continue
        if not any(later == tool and o == "success" for later, o in outcomes[i + 1 :]):
            return tool
    return ""


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


#: Tokens that cannot be a glossary TERM's head. Closed-class words plus the discourse
#: fillers that make a definitional frame read as ordinary prose ("that means we should
#: ship", "this refers to the other one"). Deliberately a separate list from
#: `preference_facets._NON_ACTION_HEADS`, which vetoes verb-phrase heads: the two answer
#: different questions and collapsing them would make each wrong for the other's job.
_NON_TERM_HEADS = frozenset("""
    it its this that these those they them their he him his she her hers we us our ours
    you your yours i me my mine one ones thing things something anything nothing
    which what who whom whose where when why how
    and but or nor so then also just only the a an any some all both each every
    is are was were be been being am do does did doing done have has had having
    will would shall should can could may might must
    """.split())

#: Interrogative / auxiliary heads that make a clause a QUESTION about a term rather than a
#: definition of it ("what does CR mean?", "does CR mean code review?"). A question must never
#: teach the glossary, because the answer is not in the user's message.
_QUESTION_HEADS = frozenset("""
    what which who whom whose where when why how
    is are was were do does did can could may might shall should will would
    """.split())

#: An article may LEAD a term without condemning it — "the run ledger means …" defines a term;
#: the token ceiling in :func:`_clean_term`, not the article, is what keeps a whole clause out
#: ("the thing I said yesterday about the build" is eight tokens). It is STRIPPED rather than
#: merely tolerated because it is not part of the term's identity: "by the run ledger I mean …"
#: and "run ledger means …" define the same term, and `reinforce` keys on the rendered line, so
#: keeping the article would file one definition as two entries.
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.I)

#: Heads that make a DEFINITION a consequence rather than a meaning. "the build means we
#: should wait" and "the run ledger means the append-only event store" are grammatically
#: identical, and this is the signal that separates them: a definition is a noun phrase, a
#: consequence opens with a pronoun or a modal. Deliberately excludes articles, which open
#: the most common definition shape of all ("a code review").
_NON_DEFINITION_HEADS = frozenset("""
    i we you they he she it that this there
    will would shall should can could may might must do does did
    """.split())

#: One clause plus the punctuation that ended it, so a trailing `?` is still observable.
_CLAUSE_RE = re.compile(r"[^.!?;\n]+[.!?;\n]?")

#: `by <term> I mean <definition>` — searched anywhere in a clause, because "by … I mean" is
#: itself a strong anchor that does not need clause-initial position.
_GLOSSARY_BY_RE = re.compile(r"\bby\s+(?P<term>.{1,60}?)\s+i\s+mean\s+(?P<definition>.+)$", re.I)

#: `<term> stands for|means|refers to <definition>` — anchored at the clause start, because a
#: bare "means" mid-sentence is far more often ordinary prose than a definition. One optional
#: comma-delimited discourse prefix is allowed to precede the term ("FYI, SEL stands for …"),
#: because the clause splitter deliberately does not break on commas — a definition may contain
#: one ("the append-only store, one row per event") — and without this the prefix would be
#: swallowed INTO the term, yielding "FYI, SEL". The term itself is comma-free: a term is a noun
#: phrase, so a comma inside one means the anchor landed in the wrong place.
_GLOSSARY_IS_RE = re.compile(
    r"^(?:[^,]{0,40},\s*)?(?P<term>[^,]{1,60}?)"
    r"\s+(?:stands\s+for|means|refers\s+to)\s+(?P<definition>.+)$",
    re.I,
)

#: Cap on one rendered glossary line. The `glossary` slot's own cap is 600 chars for the whole
#: slot, so a single line must stay well inside it or the first definition fills the register.
_GLOSSARY_LINE_CAP = 120

_TERM_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-_/.]*")


def _clean_term(raw: str) -> str:
    """A candidate glossary term, or "" when it cannot be one.

    Strips the quoting a user naturally puts around a term being defined, then requires 1-4
    tokens whose head is not closed-class. The token ceiling is what separates a term from a
    clause: "CR" and "the run ledger" are terms, "the thing I said yesterday about the build"
    is prose that happens to contain "means".
    """
    term = raw.strip().strip("\"'`“”‘’*").strip()
    tokens = _TERM_TOKEN_RE.findall(term)
    if not (1 <= len(tokens) <= 4):
        return ""
    if len(tokens) > 1:
        stripped = _LEADING_ARTICLE_RE.sub("", term, count=1)
        if stripped != term:
            term, tokens = stripped, _TERM_TOKEN_RE.findall(stripped)
    if len(term) < 2 or not tokens:
        return ""
    if tokens[0].casefold() in _NON_TERM_HEADS:
        return ""
    return term


def detect_glossary_definition(user_message: str) -> tuple[str, str] | None:
    """A ``(term, definition)`` the user explicitly DEFINED, or None. No LLM call.

    The `glossary` slot is workspace-scoped project vocabulary — "terms that mean something
    specific here" — and it is the one built-in slot with no producer: a user could only ever
    fill it by hand. This is the cheap heuristic that lets the after-turn pass offer one, in
    the same shape as :func:`preference_facets.detect_facet_candidate` (regex, conservative,
    no new model call).

    Precision over recall, deliberately, for the same reason the veto rule is strict: a slot
    is injected on EVERY turn, so a false positive costs every future turn until the user
    deletes it. Three explicit definitional frames are recognized, each requiring a term that
    is 1-4 tokens with a non-closed-class head and a definition of at least two tokens:

    * ``by <term> I mean <definition>``
    * ``<term> stands for <definition>``   (clause-initial)
    * ``<term> means|refers to <definition>``   (clause-initial)

    A clause that is a QUESTION is never a definition — "what does CR mean?" asks for the
    answer rather than supplying it, and "CR means what?" supplies an interrogative. Both are
    rejected, from opposite sides.
    """
    for clause_raw in _CLAUSE_RE.findall(user_message or ""):
        interrogative = clause_raw.rstrip().endswith("?")
        clause = clause_raw.strip().rstrip(".!?;").strip()
        if not clause:
            continue
        head = (_TERM_TOKEN_RE.findall(clause) or [""])[0].casefold()
        if head in _QUESTION_HEADS:
            continue  # a question ABOUT a term, not a definition of one
        match = _GLOSSARY_BY_RE.search(clause) or _GLOSSARY_IS_RE.match(clause)
        if match is None:
            continue
        term = _clean_term(match.group("term"))
        if not term:
            continue
        definition = match.group("definition").strip().strip("\"'`“”‘’*").strip()
        def_tokens = _TERM_TOKEN_RE.findall(definition)
        if len(def_tokens) < 2:
            continue  # a bare word is a restatement, not a definition
        if def_tokens[0].casefold() in _NON_DEFINITION_HEADS:
            continue  # a consequence ("… means we should wait"), not a meaning
        if interrogative and def_tokens[-1].casefold() in _QUESTION_HEADS:
            continue  # "CR means what?" — the definition is the question
        return term, definition[:_GLOSSARY_LINE_CAP]
    return None


def capture_glossary_term(service, user_message: str) -> str | None:
    """No-LLM glossary capture: offer an explicitly-defined term to the `glossary` slot.

    The reflection half of the slots feature, wired to the one slot that has no other
    producer. Returns the line written (for a caller that surfaces it), or None — including
    when the line was refused, because every refusal path in
    :func:`capture_slot_lines` is one the user already decided (a human tombstone) or one
    that needs the user's decision (an over-cap trim proposal), and neither is something to
    report as learned.
    """
    found = detect_glossary_definition(user_message or "")
    if not found:
        return None
    term, definition = found
    line = f"{term} — {definition}"[:_GLOSSARY_LINE_CAP]
    # reinforce=True: a term the user re-explains is the same entry with more evidence, not a
    # second line. Without a trim handler an over-cap glossary logs the proposal and declines
    # the write — the after-turn pass has no user in front of it to answer one.
    return line if capture_slot_lines(service, "glossary", [line], reinforce=True) else None


def capture_slot_lines(
    service,
    slot: str,
    lines,
    *,
    reinforce: bool = True,
    source: str = "after_turn_review",
    on_trim_needed=None,
) -> int:
    """Append reflection output into a memory slot. Append-only (MGAV-8). Returns lines written.

    The reflection half of the slots feature: the after-turn pass observes something worth
    keeping as standing state and offers it here. Three constraints, all delegated to
    `memory_slots.append` so they cannot drift apart from the primitive that owns them:

    * **Append-only.** Existing lines are never rewritten or reordered — a change is a new line
      plus a tombstone. That is what makes the memory event log (WAL) and `undo_event` a real
      history of the slot rather than a series of overwrites.
    * **Human tombstones are final.** A line the user deleted is never re-added, however many
      times the reflection pass re-derives it. Re-adding it does not read as a duplicate row to
      a user; it reads as the assistant overruling them.
    * **Over-cap is reported, not swallowed.** A full slot raises a trim proposal, which is
      handed to *on_trim_needed* if the caller supplied one. Without a handler the proposal is
      logged at WARNING and the line is not written — the one thing never done is a silent
      truncation of what the user just said.

    *reinforce* bumps an existing line's `reinforcements` count instead of duplicating it, which
    is how repeated observation accumulates evidence without growing the slot.

    *source* stamps the memory event. It defaults to this module rather than to
    ``memory_slots.append``'s own ``user_explicit`` default, because everything written through
    here is the ASSISTANT reflecting — recording it as the user's own words would misattribute
    it in the WAL and in every reader that treats a user-set entry as more durable than an
    inferred one.
    """
    if service is None or not getattr(service, "has_vector", False):
        return 0
    vs = getattr(service, "_vs", None)
    if vs is None:
        return 0
    from personalclaw import memory_slots

    written = 0
    for text in lines or []:
        candidate = str(text or "").strip()
        if not candidate:
            continue
        before = len(memory_slots.live_lines(memory_slots.load(vs, slot)))
        try:
            after = memory_slots.append(vs, slot, candidate, source=source, reinforce=reinforce)
        except memory_slots.SlotCapExceeded as exc:
            if on_trim_needed is not None:
                on_trim_needed(exc.proposal)
            else:
                logger.warning("slot %r append refused: %s", slot, exc.proposal.message)
            continue
        except Exception:
            logger.debug("slot append failed for %r", slot, exc_info=True)
            continue
        if len(memory_slots.live_lines(after)) > before:
            written += 1
    return written


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

    Also runs the two no-LLM detectors on EVERY reviewed turn (not just corrections):
    the preference-facet detector (C15) — a style nudge / veto becomes a typed decaying
    facet that the ambient USER PROFILE block renders — and the glossary detector, which
    offers an explicitly-defined project term to the workspace-scoped `glossary` slot.
    """
    # Cheap no-LLM captures: both run regardless of the correction gate (a style nudge like
    # "keep it shorter", or "by CR I mean a code review", is not a correction-signal but IS
    # worth keeping). The dashboard hot path runs both BEFORE this expensive-review gate (so
    # a toolless conversational hint isn't dropped) and passes capture_facets=False to avoid
    # a double-write; direct/test callers keep the default.
    if capture_facets:
        capture_preference_facet(service, user_message)
        capture_glossary_term(service, user_message)
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


#: Every verdict one ladder pass can end on, mapped to the level its ONE terminal line is
#: emitted at (ACP-AGENT-PARITY `G47`). This dict IS the enumeration — the verdicts and the
#: severity decision live in one place rather than in a second enum plus a lookup.
#:
#: 🔴 WHY WARNING AND NOT ALL-INFO, WHICH IS WHAT `G47` ASKED FOR. Measured: the shipped
#: default log level is **WARNING**, not INFO — ``AgentConfig.log_level`` defaults to
#: ``"WARNING"`` (``config/loader.py``) and ``cli.py`` applies it when ``--verbose`` is
#: absent. So an INFO line would have been an INERT fix: still invisible on a default
#: install, which is the exact property `G47` reports. The verdicts that mean THE PASS
#: PRODUCED NOTHING AND COST MONEY are therefore WARNING (visible as shipped), and the
#: verdicts that mean the pass worked — including ``no_action``, the common one — are INFO.
#:
#: 🔴 WHY THIS IS NOT SPAM. Exactly one line per pass, and a pass only happens on a
#: learning-worthy turn (``learning_decision_for_turn`` plus the ``skill_ladder`` cadence
#: flag), so the WARNING rate is bounded by *failing* gated turns. A provider that is down
#: warns once per gated turn, which is the alarm the operator wants; a healthy install emits
#: nothing at the default level.
#:
#: An UNMAPPED verdict logs at WARNING, deliberately: an unrecognised outcome should get
#: louder, never quieter (a default branch that swallows into DEBUG is how this defect class
#: reappears).
_LADDER_VERDICT_LEVEL: dict[str, int] = {
    # the pass produced nothing and the money is spent
    "provider_error": logging.WARNING,
    "unparsable": logging.WARNING,
    "incomplete_decision": logging.WARNING,
    "enqueue_failed": logging.WARNING,
    "internal_error": logging.WARNING,
    # the pass worked (including deciding there was nothing to learn)
    "env_failure_claim": logging.INFO,
    "no_action": logging.INFO,
    "enqueue_skipped": logging.INFO,
    "filed": logging.INFO,
    "template_filed": logging.INFO,
    "template_declined": logging.INFO,
}


def _log_ladder_verdict(verdict: str, elapsed_ms: float, session_key: str, detail: str) -> None:
    """Emit EXACTLY ONE line per ladder pass, carrying its verdict (`G47`).

    Before this, a pass had eight silent exits: the failure path was ``logger.debug`` and the
    ``action == "none"`` path — the common one — logged nothing at all, so a ladder pass that
    died as ``provider_error`` at 60,010 ms and a ladder pass that ran fine were the same
    observation (namely, none). The elapsed time is on the line because "expensive and dead"
    and "cheap and idle" need opposite responses.
    """
    logger.log(
        _LADDER_VERDICT_LEVEL.get(verdict, logging.WARNING),
        "skill-ladder review: %s in %d ms (session=%s)%s",
        verdict,
        int(elapsed_ms),
        session_key or "-",
        f" — {detail}" if detail else "",
    )


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

    Attributed and LOGGED (`G47`): the pass runs inside ``audit.caller_scope("skill_ladder")``
    so every model attempt it makes carries its subsystem on the ledger row, and it emits
    exactly one terminal line naming its verdict and its elapsed time. Both halves are the
    same defect — an expensive unattended pass that can be dead in production with no surface
    saying so — and neither half alone closes it: the log line says the pass died, the ledger
    row says what it spent dying.
    """
    started = time.monotonic()
    verdict, detail, summary = "internal_error", "", None
    try:
        with caller_scope("skill_ladder"):
            verdict, detail, summary = await _ladder_pass(
                session_key=session_key,
                user_message=user_message,
                assistant_text=assistant_text,
                loaded_skills=loaded_skills,
                completion=completion,
            )
    finally:
        # In a `finally` so an exception escaping the pass is still reported as a verdict
        # rather than vanishing — the invisibility this exists to fix.
        _elapsed_ms = (time.monotonic() - started) * 1000.0
        _log_ladder_verdict(verdict, _elapsed_ms, session_key, detail)
        # Same choke point, second surface. The log line above answers "what happened"
        # for an operator tailing logs at INFO; the marker answers "did this ever run"
        # for anyone reading the API, which is the only surface the proposals queue has.
        # `G44`: without it, `{"proposals": []}` means both "ran, proposed nothing" and
        # "never ran", and no drive from outside can tell them apart. Recorded here
        # rather than at the enqueue site on purpose — enqueueing is one of eleven
        # verdicts, and the ten that file nothing are exactly the invisible ones.
        _record_ladder_review(verdict, _elapsed_ms, session_key, detail)
    return summary


def _record_ladder_review(verdict: str, elapsed_ms: float, session_key: str, detail: str) -> None:
    """Persist the pass's verdict for the API. Never raises: see `record_review`."""
    try:
        from personalclaw.skills import proposals as _proposals

        _proposals.record_review(
            verdict=verdict, elapsed_ms=elapsed_ms, session_key=session_key, detail=detail
        )
    except Exception:  # pragma: no cover - defence in depth around a `finally`
        logger.debug("skill-ladder review: last-run marker failed", exc_info=True)


async def _ladder_pass(
    *,
    session_key: str,
    user_message: str,
    assistant_text: str,
    loaded_skills: list[str],
    completion=None,
) -> tuple[str, str, str | None]:
    """One ladder pass → ``(verdict, detail, summary)``.

    Split out from :func:`run_skill_ladder_review` so every exit names its verdict: the
    wrapper owns the single log line, and a `return` that named nothing is what made a dead
    pass unobservable. ``verdict`` is a key of :data:`_LADDER_VERDICT_LEVEL`; ``summary`` is
    the chip text (or None), unchanged.

    The fifth tier is the ad-hoc→template branch: it routes a procedure-shaped turn through
    ``learning.template_gate`` instead of the skill queue, because a repeatable plan and a how-to
    are different artifacts and the gate that judges plans is deterministic.

    ``completion`` is an injectable ``async (prompt)->str`` (defaults to
    ``one_shot_completion``) so tests drive it without a real model. Best-effort;
    never raises into the turn."""
    # Guardrail up front: an env-failure turn can't teach a skill.
    if is_environment_failure_claim(user_message) or is_environment_failure_claim(assistant_text):
        return "env_failure_claim", "", None
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
    except Exception as exc:
        # Stays DEBUG for the traceback; the WARNING that says the pass died is the
        # wrapper's single verdict line, so a failure is not reported twice.
        logger.debug("skill-ladder review: completion failed", exc_info=True)
        return "provider_error", type(exc).__name__, None
    decision = _parse_ladder_json(raw)
    if not decision:
        return "unparsable", f"{len(raw or '')} chars returned", None
    action = str(decision.get("action", "none")).strip().lower()
    if action == "template":
        summary = await _review_template_candidate(decision, session_key=session_key)
        return ("template_filed" if summary else "template_declined"), "", summary
    if action not in ("refine", "support_file", "create"):
        return "no_action", f"action={action or '-'}", None  # nothing to learn

    slug = str(decision.get("slug", "")).strip()
    description = str(decision.get("description", "")).strip()
    procedure_md = str(decision.get("procedure_md", "")).strip()
    triggers = str(decision.get("triggers", "")).strip()
    target = str(decision.get("target", "")).strip()
    if not slug or not description or not procedure_md:
        return "incomplete_decision", f"action={action}", None
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
    except Exception as exc:
        logger.debug("skill-ladder review: enqueue failed", exc_info=True)
        return "enqueue_failed", type(exc).__name__, None
    if prop is None:
        return "enqueue_skipped", f"{action} {slug}", None
    verb = {"refine": "refine", "support_file": "add file to", "create": "new skill"}[action]
    return "filed", f"{action} {prop.slug}", f"Proposed skill ({verb}): {slug}"
