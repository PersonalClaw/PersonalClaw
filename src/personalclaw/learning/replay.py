"""Local A/B replay harness — evidence on captured sessions (EXTERNAL-ACCESS §9, EA-6).

The Proposal Inbox asks a user to accept a skill or template the system wrote itself. The
only evidence it can carry today is *provenance* (where the idea came from) and, for a
template diff, the Loop-2 gate's before/after over the shipped scenario library. Neither
answers the question a reviewer actually has: **would this have helped on MY work?**

This module answers it from the one corpus that is genuinely the user's own — the capture
sessions the inbound proxy already writes (``inbound/capture_store.py``, EA-5). It mines a
handful of real turns, replays each one twice (without the candidate, then with it), scores
both arms with the same judge, and attaches the pair to the proposal.

## What this is NOT

**It is not a gate.** :func:`attach` writes onto a PENDING proposal and touches neither
``status`` nor ``updated_at``; ``proposals.accept`` never reads the field. A ``regressed``
verdict is a sentence on a card, and the human still decides — exactly the contract
``Proposal.gate`` already documents for the Loop-2 gate ("never a reason to refuse the
accept"). A measurement that could block would make this the thing that stops a user
shipping a change because the *harness* broke, and would quietly convert the flywheel's
human-installs invariant into a machine veto. ``tests/test_ea6_replay_harness.py`` asserts
it directly: a ``regressed`` verdict still accepts.

**It never touches** :mod:`personalclaw.evals.runner`. That runner is the right tool for the
Loop-2 gate — it spawns a child process against a throwaway ``PERSONALCLAW_HOME`` so a staged
artifact cannot contaminate the real one. Here it would be the hazard rather than the
isolation: this pass runs INSIDE the gateway's consolidation tick, in the same process as
every live session, and an env mutation on that path is visible to every concurrent reader of
``config_dir()``. So the two halves are composed directly instead —
:func:`~personalclaw.llm_helpers.one_shot_completion` for the arms and
:class:`~personalclaw.eval.judge.LLMJudge` for the score — the same composition
``sampling._judge_candidates`` and ``loop/judge.assess_cycle`` already use.
``test_never_imports_the_eval_runner`` is the regression.

## What an arm actually is

The baseline arm sends the captured prompt as it was. The candidate arm sends the same
prompt with the proposal's body prepended as available context, under a fence. That is not a
simulation of skill injection — it *is* how a skill reaches a turn (the learning block is
prompt text), so the difference between the arms is precisely the candidate's presence. Both
arms run at ``use_case="background"``, both are judged against the SAME criteria derived from
the proposal, and the criteria are built once per proposal rather than per arm: a comparison
whose rubric moves between arms measures the rubric.

## Three states, and why the third exists

``state`` is two-valued (``replayed`` / ``unreplayed``) and ``verdict`` four-valued, giving a
card three distinct things to say — the same shape ``evals.gate.summary`` established and
``learningMeta.gateLabel`` renders:

* **replayed, with means** — ``improved`` / ``neutral`` / ``regressed``.
* **replayed, no scored case** — every mined case was rejected. ``candidate_mean`` and
  ``baseline_mean`` are ``None`` and the verdict is ``unmeasured``.
* **unreplayed** — no cases were mined, the budget was exhausted, or replay is off. Carries a
  ``reason`` and, for the budget case, ``deferred=True`` so the card says "deferred on the
  learning replay budget" rather than going quiet.

The means are ``None``, never ``0.0``, whenever nothing was scored. An empty scored set that
publishes a mean publishes a fabricated one: ``0.0`` reads as "the candidate answered
nothing correctly", which is a measurement nobody made and points the reviewer the wrong
way. This is the house rule ``optimize.SCORE_UNSCORED`` and ``learningMeta.evidenceLabel``
already hold, and :func:`summary` is where it is enforced for this surface.

## parse-failure → 0, and REJECT

``LLMJudge.judge_turn`` returns ``JudgeVerdict(score=0, reason="parse_error: …")`` when the
judge's output cannot be parsed. This module reads that as a **rejected case**, not a scored
zero and not a skip:

* counting it as ``0.0`` would drag whichever arm failed to parse toward a regression verdict
  on the strength of a broken *judge*;
* dropping it silently would let a proposal claim a clean two-case mean when four cases ran.

So a case whose either arm fails to parse is excluded from both means and counted in
``rejected``, which the card renders. Zero is a legitimate score when the judge *says* zero;
the discriminator is the ``parse_error`` reason, and ``test_a_parse_failure_rejects_the_case``
pins that the two are not the same outcome.

## The bound

Replay spends real money on the maintenance tick, so the ceiling is structural rather than
promised. :func:`replay_proposal` binds a learning-scope run key and a
:class:`~personalclaw.guardrails.budgets.Budget` into the two ContextVars
:class:`~personalclaw.guardrails.model_call.ModelCallGuard` reads, and the guard — which
already wraps every provider at the ``provider_bridge`` seam — does both halves itself:
``check_run`` before each call, ``charge(run_key=…)`` after. There is no second tally here,
and no way for a call on this path to escape the meter.

Following ``evals/gate.py``'s doctrine exactly: an **unbudgeted** replay does not run
unbounded, it does not run at all. ``Budget(max_dollars=0)`` is UNLIMITED, which is the one
thing a pass on the maintenance cadence must never be, so a non-positive
``learning.replay_max_dollars`` yields ``unreplayed`` + :data:`UNREPLAYED_NO_BUDGET`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> str:
    """UTC ISO timestamp — the same shape ``proposals._now`` writes, so a card can sort a
    replay's ``ran_at`` against the proposal's own timestamps without parsing two formats."""
    return datetime.now(timezone.utc).isoformat()


# ── the two states, and the four verdicts ────────────────────────────────────

#: A replay ran and at least one case was mined. Whether anything SCORED is a separate
#: question the means answer, exactly as ``gate.GATE_GATED`` leaves ``before``/``after``
#: nullable.
REPLAY_REPLAYED = "replayed"

#: No replay ran. Always accompanied by a ``reason``.
REPLAY_UNREPLAYED = "unreplayed"

VERDICT_IMPROVED = "improved"
VERDICT_NEUTRAL = "neutral"
VERDICT_REGRESSED = "regressed"

#: The candidate/baseline pair does not exist. Distinct from ``neutral``: "the two arms
#: scored the same" and "nothing was scored" are different facts, and collapsing them would
#: let an unmeasured proposal read as a measured tie.
VERDICT_UNMEASURED = "unmeasured"

_VERDICTS = (VERDICT_IMPROVED, VERDICT_NEUTRAL, VERDICT_REGRESSED, VERDICT_UNMEASURED)


# ── why a replay did not happen ──────────────────────────────────────────────

UNREPLAYED_NOT_RUN = (
    "no replay run stands behind this proposal — accept on the evidence above, or wait for "
    "the next maintenance pass"
)
UNREPLAYED_NO_BUDGET = (
    "no learning replay budget is set, so a replay would have had no ceiling at all — set "
    "learning.replay_max_dollars to get candidate/baseline scores"
)
UNREPLAYED_NO_CASES = (
    "no replay cases could be mined from captured sessions — nothing the candidate could be "
    "compared on"
)
UNREPLAYED_DISABLED = "replay evidence is off (learning.replay_enabled)"
UNREPLAYED_BUDGET_EXHAUSTED = (
    "deferred on the learning replay budget — the ceiling was reached before this proposal's "
    "cases ran, and it will be replayed on a later pass"
)
UNREPLAYED_NO_JUDGE = (
    "the replay judge could not start, so neither arm could be scored — the eval_judge model "
    "binding is what to check"
)


# ── mining bounds ────────────────────────────────────────────────────────────

#: At most three cases per capture session (the atom's own bound). A cap per SESSION rather
#: than only overall, because one chatty session would otherwise supply every case and the
#: evidence would describe that session instead of the user's work.
MAX_CASES_PER_SESSION = 3

#: Total cases one replay spends on. Each case is FOUR model calls (two arms × one judge
#: each), so this is the number that decides whether the maintenance tick is cheap.
MAX_CASES_PER_PROPOSAL = 6

#: A captured prompt shorter than this is not a task, it is an acknowledgement ("thanks",
#: "yes", "continue"). Replaying one measures nothing and still costs four calls.
MIN_PROMPT_CHARS = 80

#: A captured prompt longer than this is SKIPPED, not clipped. A 200k-token context replayed
#: twice on the maintenance tick is the shape that turns a cheap evidence pass into the reason
#: someone turns learning off — but clipping the stored text would sever the sidecar's closing
#: ``</untrusted_content>`` tag, which is a fence BREAK: the tail of a captured page would land
#: in the prompt outside the fence and read as instructions. So the bound drops the case.
MAX_PROMPT_CHARS = 12_000


#: The scored margin below which a difference is called ``neutral``. Reused from the
#: refiner's ``MIN_TARGET_IMPROVEMENT`` rather than minted here, so "this change is worth
#: shipping" means one thing across the flywheel. Read through a function so the two cannot
#: drift by a stale copy.
def neutral_margin() -> float:
    """The improved/neutral/regressed band, from ``refiner.MIN_TARGET_IMPROVEMENT``."""
    from personalclaw.learning.refiner import MIN_TARGET_IMPROVEMENT

    return float(MIN_TARGET_IMPROVEMENT)


#: The run scope replay spend accrues to. One key for the whole feature rather than one per
#: proposal: the ceiling is meant to bound the maintenance PASS, and a per-proposal key would
#: reset the total on every proposal and never bind.
RUN_KEY = "learning_replay"

#: The proposal kinds whose body IS the candidate text, so prepending it is a faithful A/B.
#: ``template_diff`` is deliberately absent — its candidate is a typed ops list that only the
#: template applier can turn into text, and replaying the ops list verbatim would measure a
#: JSON blob rather than the change.
REPLAYABLE_KINDS: frozenset[str] = frozenset({"skill", "template"})


# ── a mined case ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReplayCase:
    """One captured turn, mineable into an A/B pair.

    ``session_id`` + ``record_hash`` is the provenance pointer, and it is required rather
    than nice-to-have: a case a reviewer cannot trace back to the turn it came from is an
    anecdote, and this whole module exists to replace anecdotes with something checkable.
    """

    session_id: str
    record_hash: str
    prompt: str
    #: Whether the captured turn made NO tool calls. Tool-free turns are preferred because
    #: a replay cannot re-run the tools: a turn whose answer came from reading three files
    #: is one the replay would have to hallucinate, and both arms would be judged on a
    #: fiction. A tool-using turn is still usable evidence — it is just weaker, so it sorts
    #: last and is only reached when tool-free ones run out.
    tool_free: bool
    captured_at: float = 0.0

    @property
    def provenance(self) -> str:
        """The pointer back to the capture record. ``capture:<session>#<record_hash>``."""
        return f"capture:{self.session_id}#{self.record_hash}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "record_hash": self.record_hash,
            "provenance": self.provenance,
            "tool_free": self.tool_free,
            "captured_at": self.captured_at,
        }


def _session_files(root: Any) -> list[Any]:
    """Capture record files, newest-modified first. Sidecars are excluded by name."""
    from pathlib import Path

    root = Path(root)
    if not root.is_dir():
        return []
    files = [p for p in root.glob("*.jsonl") if not p.name.endswith(".content.jsonl")]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _read_jsonl(path: Any) -> list[dict]:
    """Parse a capture file, skipping unparseable lines.

    A truncated final line is normal for an append-only log written by a live proxy, so one
    bad line drops that record rather than the session.
    """
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        logger.debug("replay: could not read %s", path, exc_info=True)
    return rows


def _payload_chars(fenced: str) -> int:
    """Length of the fenced payload, ignoring the ``<untrusted_content …>`` wrapper.

    Needed only for the :data:`MIN_PROMPT_CHARS` bound. The wrapper is ~60-200 characters
    depending on its attributes, so measuring the fenced string would let a bare "thanks"
    clear a threshold meant to exclude it.

    The prompt itself is replayed **fenced, verbatim** — this function deliberately does not
    return the payload. ``capture_store`` fences the sidecar precisely so "the flywheel reads
    this file" without an injection in it becoming actionable, and unwrapping here would
    relocate that decision into this module and silently drop the defence for the one reader
    that sends the content to a model.
    """
    from personalclaw.security import UNTRUSTED_CLOSE

    text = fenced or ""
    body = text.rsplit(UNTRUSTED_CLOSE, 1)[0] if UNTRUSTED_CLOSE in text else text
    # Drop the opening tag line, whatever its attributes.
    if body.lstrip().startswith("<untrusted_content"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
    return len(body.strip())


def mine_cases(*, max_sessions: int = 8, limit: int = MAX_CASES_PER_PROPOSAL) -> list[ReplayCase]:
    """Extract replay cases from capture sessions. Never raises.

    Reads the RECORD file for the shape of the turn (``tool_calls`` decides ``tool_free``)
    and the SIDECAR for the prompt text, joined on ``record_hash`` — the record carries only
    digests, so neither file alone yields a replayable case.

    Bounded four ways, and each bound excludes a different bad case: ``MAX_CASES_PER_SESSION``
    stops one session dominating, ``max_sessions`` stops a long-lived home turning the
    maintenance tick into a directory walk, ``MIN_PROMPT_CHARS`` drops acknowledgements that
    would cost four model calls to learn nothing from, and ``MAX_PROMPT_CHARS`` drops a
    captured mega-context rather than clipping it into a broken fence.

    Tool-free-PREFERRING, not tool-free-only: the returned list is ordered tool-free first,
    then most recent. A caller taking a prefix therefore gets the strongest cases available
    without the mining silently returning nothing on a home whose every turn used a tool.
    """
    try:
        from personalclaw.inbound.capture_store import capture_dir

        root = capture_dir()
    except Exception:
        logger.debug("replay: no capture dir", exc_info=True)
        return []

    cases: list[ReplayCase] = []
    for path in _session_files(root)[: max(0, max_sessions)]:
        session_id = path.name[: -len(".jsonl")]
        records = _read_jsonl(path)
        if not records:
            continue
        sidecars = {
            str(row.get("record_hash") or ""): row
            for row in _read_jsonl(path.with_name(f"{session_id}.content.jsonl"))
        }
        picked = 0
        # Newest turn first WITHIN the session: a recent turn describes what the user is
        # working on now, which is what a candidate skill has to help with.
        for record in sorted(records, key=lambda r: float(r.get("ts") or 0.0), reverse=True):
            if picked >= MAX_CASES_PER_SESSION:
                break
            rhash = str(record.get("record_hash") or "")
            side = sidecars.get(rhash)
            if not rhash or not isinstance(side, dict):
                continue
            prompt = str(side.get("prompt") or "")
            if not MIN_PROMPT_CHARS <= _payload_chars(prompt) <= MAX_PROMPT_CHARS:
                continue
            cases.append(
                ReplayCase(
                    session_id=session_id,
                    record_hash=rhash,
                    prompt=prompt,
                    tool_free=not (record.get("tool_calls") or []),
                    captured_at=float(record.get("ts") or 0.0),
                )
            )
            picked += 1

    cases.sort(key=lambda c: (0 if c.tool_free else 1, -c.captured_at))
    return cases[: max(0, limit)]


# ── the verdict ──────────────────────────────────────────────────────────────


@dataclass
class CaseScore:
    """One case's two arms. ``rejected`` means it contributes to NEITHER mean."""

    provenance: str
    session_id: str
    record_hash: str
    tool_free: bool = True
    baseline: float | None = None
    candidate: float | None = None
    rejected: bool = False
    reason: str = ""

    @property
    def scored(self) -> bool:
        return not self.rejected and self.baseline is not None and self.candidate is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance,
            "session_id": self.session_id,
            "record_hash": self.record_hash,
            "tool_free": self.tool_free,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "rejected": self.rejected,
            "reason": self.reason,
            "scored": self.scored,
        }


@dataclass
class ReplayReport:
    """The A/B evidence for one proposal. Evidence, never a gate.

    ``candidate_mean`` / ``baseline_mean`` are ``None`` unless at least one case SCORED.
    That is the load-bearing part of the type: ``0.0`` is a legitimate mean (a candidate
    that genuinely scored zero on every case) and must stay distinguishable from "no case
    produced a number". The two render as different sentences and lead to opposite reviews,
    so they are different values here rather than the same one with a flag beside it.
    """

    state: str = REPLAY_UNREPLAYED
    reason: str = UNREPLAYED_NOT_RUN
    verdict: str = VERDICT_UNMEASURED
    cases: list[CaseScore] = field(default_factory=list)
    candidate_mean: float | None = None
    baseline_mean: float | None = None
    #: True only for the budget case. A deferral is a promise to come back, and it reads
    #: differently on a card from "there was nothing to measure".
    deferred: bool = False
    ran_at: str = ""
    judge_model: str = ""
    budget_dollars: float = 0.0

    @property
    def scored_cases(self) -> list[CaseScore]:
        return [c for c in self.cases if c.scored]

    @property
    def rejected_cases(self) -> int:
        return sum(1 for c in self.cases if c.rejected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "verdict": self.verdict,
            "cases": [c.to_dict() for c in self.cases],
            "candidate_mean": self.candidate_mean,
            "baseline_mean": self.baseline_mean,
            "deferred": self.deferred,
            "ran_at": self.ran_at,
            "judge_model": self.judge_model,
            "budget_dollars": self.budget_dollars,
        }


def unreplayed(reason: str, **extra: Any) -> ReplayReport:
    """An ``unreplayed`` report carrying WHY. The only constructor for the absence.

    Mirrors ``evals.gate.ungated``: a single constructor means no call site can produce a
    half-built absence whose ``verdict`` says ``neutral`` while its means are ``None``.
    """
    report = ReplayReport(state=REPLAY_UNREPLAYED, reason=reason, verdict=VERDICT_UNMEASURED)
    for key, value in extra.items():
        setattr(report, key, value)
    return report


def _mean(values: list[float]) -> float | None:
    """The mean, or ``None`` for an EMPTY set — never ``0.0``.

    The one-line version of this module's central rule. A filtered-to-empty scored set that
    returns ``0.0`` publishes a number nobody measured, and every reader downstream (the
    verdict, the card, LEARN-R2) then treats a fabrication as data.
    """
    if not values:
        return None
    return sum(values) / len(values)


def classify(baseline_mean: float | None, candidate_mean: float | None) -> str:
    """The verdict for a candidate/baseline pair.

    An absent mean on EITHER side is ``unmeasured``, not ``neutral``. Reading a missing
    number as "no difference" is the same dishonesty as drawing it as ``0.0``, just pointing
    the other way: it would let a proposal nobody could measure carry the same verdict as one
    measured to be a wash.
    """
    if baseline_mean is None or candidate_mean is None:
        return VERDICT_UNMEASURED
    delta = candidate_mean - baseline_mean
    margin = neutral_margin()
    if delta >= margin:
        return VERDICT_IMPROVED
    if delta <= -margin:
        return VERDICT_REGRESSED
    return VERDICT_NEUTRAL


def finalize(report: ReplayReport) -> ReplayReport:
    """Compute the means and the verdict from the recorded cases. Pure.

    Separated from the async replay so the arithmetic that decides what a card SAYS is
    testable without a model: the honesty rules live here, and a test that had to stand up a
    provider to check them would be a test nobody runs.
    """
    scored = report.scored_cases
    report.baseline_mean = _mean([c.baseline for c in scored if c.baseline is not None])
    report.candidate_mean = _mean([c.candidate for c in scored if c.candidate is not None])
    report.verdict = classify(report.baseline_mean, report.candidate_mean)
    if report.state == REPLAY_REPLAYED and not scored:
        rejected = report.rejected_cases
        why = f" ({rejected} rejected by the judge)" if rejected else ""
        report.reason = (
            f"{len(report.cases)} case(s) ran but none scored{why} — there is no "
            "candidate/baseline pair to compare"
        )
    return report


# ── the compact projection the inbox ROW carries ─────────────────────────────


def summary(report: dict | None) -> dict[str, Any]:
    """The compact replay clause for an inbox row.

    An ABSENT report (``None`` / ``{}``) projects to ``unreplayed`` + :data:`UNREPLAYED_NOT_RUN`
    — never a blank cell and never a ``0.0``. Same contract, same reason, as
    ``evals.gate.summary``: the row is the surface a user decides from, so an absence has to be
    legible THERE and not only in the stored record.

    Both means are passed through as ``None`` when absent. Coercing them to floats here would
    undo the whole point of :func:`_mean` at the last hop, and the frontend would have no way
    left to tell a measured zero from an unmeasured one.
    """
    data = report or {}
    state = str(data.get("state") or REPLAY_UNREPLAYED)
    state = state if state in (REPLAY_REPLAYED, REPLAY_UNREPLAYED) else REPLAY_UNREPLAYED
    verdict = str(data.get("verdict") or VERDICT_UNMEASURED)
    cases = data.get("cases") or []
    cand = data.get("candidate_mean")
    base = data.get("baseline_mean")
    return {
        "state": state,
        "reason": str(
            data.get("reason") or (UNREPLAYED_NOT_RUN if state != REPLAY_REPLAYED else "")
        ),
        # An unrecognized verdict reads as unmeasured rather than as itself: a card that
        # renders a word this build does not know is a card the reader fills in with a guess.
        "verdict": verdict if verdict in _VERDICTS else VERDICT_UNMEASURED,
        "candidate_mean": None if cand is None else float(cand),
        "baseline_mean": None if base is None else float(base),
        "cases": len(cases),
        "scored": sum(1 for c in cases if isinstance(c, dict) and c.get("scored")),
        "rejected": sum(1 for c in cases if isinstance(c, dict) and c.get("rejected")),
        "tool_free": sum(1 for c in cases if isinstance(c, dict) and c.get("tool_free")),
        "deferred": bool(data.get("deferred")),
        "provenance": [
            str(c.get("provenance") or "")
            for c in cases
            if isinstance(c, dict) and c.get("provenance")
        ][:20],
        "ran_at": str(data.get("ran_at") or ""),
    }


# ── the arms ─────────────────────────────────────────────────────────────────


def _candidate_prompt(case_prompt: str, body: str, *, kind: str) -> str:
    """The candidate arm's prompt: the captured request, plus the candidate as context.

    Both spans stay fenced. ``case_prompt`` arrives fenced from the capture sidecar and is
    passed through untouched; ``body`` is machine-authored text from a proposal nobody has
    reviewed yet, which is exactly the class of content that must not be able to instruct the
    replay model about its own behaviour — so it is fenced here rather than trusted because it
    came from inside the process.
    """
    from personalclaw.security import fence_untrusted

    staged = fence_untrusted(body, source="learning_proposal", source_type=kind)
    return (
        f"A candidate {kind} is available to you for this request:\n\n{staged}\n\n"
        f"Now answer the following captured request:\n\n{case_prompt}"
    )


def criteria_for(prop: Any) -> str:
    """The judging rubric for one proposal's replay. Built ONCE, used by both arms.

    Derived from the proposal rather than fixed, because "did this help" depends on what the
    proposal claimed: a skill whose manifest predicts it will stop a specific failure should be
    judged on that failure, not on generic helpfulness. Falls back to a generic rubric when the
    manifest is thin — a proposal with an incomplete manifest still deserves evidence, the same
    lenient-but-recording stance ``ChangeManifest.issues`` takes.
    """
    manifest = getattr(prop, "change_manifest", None) or {}
    predicted = [str(p) for p in (manifest.get("predicted_fixes") or []) if str(p).strip()]
    lines = [
        "Score the assistant's response to the captured request on a 1-5 scale.",
        "Judge only the response's usefulness for the request as stated.",
    ]
    if predicted:
        lines.append("The change under test predicts it will: " + "; ".join(predicted[:5]) + ".")
    fix = manifest.get("targeted_fix")
    if isinstance(fix, str) and fix.strip():
        lines.append(f"Its stated targeted fix is: {fix.strip()[:400]}")
    return "\n".join(lines)


async def _score(judge: Any, *, description: str, criteria: str, prompt: str, answer: str):
    """One judge call. Returns the ``JudgeVerdict``, or ``None`` when the call itself failed.

    A failed CALL and an unparseable RESPONSE are different outcomes and stay different: the
    call failing is a transport problem that says nothing about either arm, while an
    unparseable response is ``JudgeVerdict(score=0, reason="parse_error: …")`` — a verdict the
    caller must read and reject. Collapsing them would either hide a broken judge or let a
    network blip reject a case.
    """
    try:
        return await judge.judge_turn(description, criteria, prompt, answer)
    except Exception:
        logger.debug("replay: judge call failed", exc_info=True)
        return None


def _is_parse_failure(verdict: Any) -> bool:
    """Whether a verdict is ``LLMJudge``'s parse-failure sentinel rather than a real zero.

    ``LLMJudge`` signals an unparseable response with ``score=0`` and a ``reason`` prefixed
    ``parse_error:``. Reading the REASON rather than the score is the whole point: a judge that
    genuinely scored a response zero is data, and treating every zero as a parse failure would
    discard the strongest possible evidence that a candidate made things worse.
    """
    return bool(
        verdict is not None
        and float(getattr(verdict, "score", 0.0) or 0.0) == 0.0
        and str(getattr(verdict, "reason", "") or "").startswith("parse_error")
    )


async def _run_case(
    case: ReplayCase,
    *,
    prop: Any,
    criteria: str,
    judge: Any,
    completion: Any,
) -> CaseScore:
    """Replay one case on both arms and score both. Raises only ``BudgetExceededError``.

    The budget error is deliberately NOT caught here: it means the ceiling is reached, so every
    remaining case would fail the same way, and swallowing it per case would burn the caller's
    loop producing identical rejections instead of one honest deferral.
    """
    from personalclaw.guardrails.failure import BudgetExceededError

    score = CaseScore(
        provenance=case.provenance,
        session_id=case.session_id,
        record_hash=case.record_hash,
        tool_free=case.tool_free,
    )
    kind = str(getattr(prop, "kind", "") or "skill")
    description = (
        f"A captured turn from the user's own coding session, replayed to test a proposed {kind}."
    )
    try:
        baseline_answer = await completion(case.prompt, use_case="background")
        candidate_answer = await completion(
            _candidate_prompt(case.prompt, str(getattr(prop, "body", "") or ""), kind=kind),
            use_case="background",
        )
    except BudgetExceededError:
        raise
    except Exception:
        logger.debug("replay: an arm failed for %s", case.provenance, exc_info=True)
        score.rejected = True
        score.reason = "one arm's completion failed, so the pair is incomplete"
        return score

    if not (baseline_answer or "").strip() or not (candidate_answer or "").strip():
        score.rejected = True
        score.reason = "an arm returned an empty completion, so there is nothing to score"
        return score

    base_v = await _score(
        judge,
        description=description,
        criteria=criteria,
        prompt=case.prompt,
        answer=baseline_answer,
    )
    cand_v = await _score(
        judge,
        description=description,
        criteria=criteria,
        prompt=case.prompt,
        answer=candidate_answer,
    )
    if base_v is None or cand_v is None:
        score.rejected = True
        score.reason = "the judge could not be reached for one arm"
        return score
    if _is_parse_failure(base_v) or _is_parse_failure(cand_v):
        # parse-failure → 0 REJECT. Not a scored zero (that would blame the arm for a broken
        # judge) and not a skip (that would let the mean claim more cases than it had).
        score.rejected = True
        score.reason = (
            "the judge's response could not be parsed, so the case scores 0 and is rejected"
        )
        return score
    score.baseline = float(base_v.score)
    score.candidate = float(cand_v.score)
    return score


# ── the replay ───────────────────────────────────────────────────────────────


def replay_budget() -> float:
    """The replay ceiling in dollars, from ``learning.replay_max_dollars``. 0 = no replay.

    Fail-CLOSED on an unreadable config, unlike ``budget_from_config``'s fail-open. The
    directions are not symmetric: a day budget failing open leaves the breaker and the scan as
    the hard controls, while this failing open would put unbounded LLM spend on a background
    maintenance tick nobody is watching.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return float(getattr(AppConfig.load().learning, "replay_max_dollars", 0.0) or 0.0)
    except Exception:
        logger.debug("replay: could not read learning.replay_max_dollars", exc_info=True)
        return 0.0


def replay_enabled() -> bool:
    """Whether replay evidence runs at all. Fail-closed for the same reason as the budget."""
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load().learning
        return bool(getattr(cfg, "enabled", True)) and bool(getattr(cfg, "replay_enabled", False))
    except Exception:
        logger.debug("replay: could not read learning.replay_enabled", exc_info=True)
        return False


async def replay_proposal(
    prop: Any,
    cases: list[ReplayCase],
    *,
    completion: Any = None,
    judge_factory: Any = None,
    budget_dollars: float | None = None,
) -> ReplayReport:
    """Run the A/B for one proposal. NEVER raises for a condition a user could hit.

    ``completion`` and ``judge_factory`` are injected for tests, and injected as the WHOLE
    composition rather than as mocks of its pieces: the default ``completion`` is
    ``llm_helpers.one_shot_completion`` itself and the default judge is a real
    :class:`~personalclaw.eval.judge.LLMJudge` over the ``eval_judge`` binding, so a test can
    drive the genuine two-call-per-arm shape against a scripted provider.

    The spend bound is bound HERE, around the whole proposal, rather than per call: the
    ContextVars are what ``ModelCallGuard`` reads, and a per-call binding would reset the run
    total before every check and never refuse anything — the inert shape ``budgets``' own
    docstring records for ``check_run``.
    """
    from personalclaw.guardrails.budgets import (
        Budget,
        reset_current_run_budget,
        reset_current_run_key,
        set_current_run_budget,
        set_current_run_key,
    )
    from personalclaw.guardrails.failure import BudgetExceededError

    ceiling = replay_budget() if budget_dollars is None else float(budget_dollars)
    if ceiling <= 0.0:
        return unreplayed(UNREPLAYED_NO_BUDGET)
    if not cases:
        return unreplayed(UNREPLAYED_NO_CASES, budget_dollars=ceiling)

    if completion is None:
        from personalclaw.llm_helpers import one_shot_completion

        completion = one_shot_completion
    if judge_factory is None:

        def judge_factory():
            from personalclaw.eval.judge import LLMJudge
            from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

            # `eval_judge` is the session key LLMJudge passes its factory, and the binding the
            # atom names. Resolved through the bridge so the judge rides the same
            # ModelCallGuard — and therefore the same meter and ceiling — the arms do.
            return LLMJudge(lambda _key: resolve_provider_for_use_case("reasoning"))

    judge = judge_factory()
    try:
        await judge.start()
    except Exception:
        logger.warning(
            "replay: judge provider failed to start — proposal left UNREPLAYED rather than "
            "scored on one arm; check the eval_judge model binding",
            exc_info=True,
        )
        return unreplayed(UNREPLAYED_NO_JUDGE, budget_dollars=ceiling)

    report = ReplayReport(state=REPLAY_REPLAYED, reason="", budget_dollars=ceiling)
    criteria = criteria_for(prop)
    key_token = set_current_run_key(RUN_KEY)
    budget_token = set_current_run_budget(Budget(max_dollars=ceiling))
    try:
        for case in cases[:MAX_CASES_PER_PROPOSAL]:
            try:
                report.cases.append(
                    await _run_case(
                        case, prop=prop, criteria=criteria, judge=judge, completion=completion
                    )
                )
            except BudgetExceededError:
                # The ceiling bit. Everything already scored STAYS on the report — a partial
                # measurement is real evidence — but the report is marked deferred so the card
                # says why it is thin instead of implying this was the whole plan.
                logger.info("replay: learning replay budget exhausted; deferring the rest")
                report.deferred = True
                report.reason = UNREPLAYED_BUDGET_EXHAUSTED
                if not report.scored_cases:
                    report.state = REPLAY_UNREPLAYED
                break
    finally:
        reset_current_run_budget(budget_token)
        reset_current_run_key(key_token)
        try:
            await judge.shutdown()
        except Exception:
            logger.debug("replay: judge shutdown failed", exc_info=True)

    report.ran_at = _now()
    finalized = finalize(report)
    # `finalize` writes a "ran but nothing scored" reason; the deferral reason is the more
    # specific truth and must win, because "the budget stopped it" is actionable and "nothing
    # scored" is not.
    if report.deferred:
        finalized.reason = UNREPLAYED_BUDGET_EXHAUSTED
    return finalized


# ── attaching, and the curator-cadence pass ──────────────────────────────────


def attach(pid: str, report: ReplayReport) -> bool:
    """Persist a replay report onto a PENDING proposal. Returns True if it landed.

    Deliberately does not touch ``status`` or ``updated_at``, for the reason
    ``proposals.attach_gate`` states: a measurement is not a decision, and bumping the
    timestamp would re-sort the queue for something the user did not do. It also cannot make a
    proposal unacceptable — there is no status this writes and no field ``accept`` reads.
    """
    from personalclaw.learning import proposals as proposals_mod

    return proposals_mod.attach_replay(pid, report.to_dict())


async def run_pass(*, max_proposals: int = 3) -> dict[str, Any]:
    """One curator-cadence replay pass. Returns a summary dict. Never raises.

    Mines once and reuses the case set across proposals on purpose: the cases are the user's
    work, not the proposal's, and re-mining per proposal would compare two candidates on
    different corpora — which makes the two verdicts incomparable for LEARN-R2 downstream.

    Attaches an ``unreplayed`` report rather than nothing when replay is off or unbudgeted. A
    proposal with no ``replay`` key and one with a report saying "no budget is set" look
    identical on a card otherwise, and only one of them is something the user can fix.
    """
    from personalclaw.learning import proposals as proposals_mod

    out: dict[str, Any] = {"considered": 0, "replayed": 0, "deferred": 0, "unreplayed": 0}
    try:
        pending = [
            p
            for p in proposals_mod.list_pending()
            if str(getattr(p, "kind", "")) in REPLAYABLE_KINDS
            and not (getattr(p, "replay", None) or {})
        ][: max(0, max_proposals)]
    except Exception:
        logger.debug("replay pass: could not list pending proposals", exc_info=True)
        return out
    if not pending:
        return out
    out["considered"] = len(pending)

    if not replay_enabled():
        for prop in pending:
            attach(str(prop.id), unreplayed(UNREPLAYED_DISABLED))
            out["unreplayed"] += 1
        return out

    cases = mine_cases()
    for prop in pending:
        try:
            report = await replay_proposal(prop, cases)
        except Exception:
            logger.warning("replay pass: failed for %s", getattr(prop, "id", "?"), exc_info=True)
            continue
        attach(str(prop.id), report)
        if report.deferred:
            out["deferred"] += 1
        elif report.state == REPLAY_REPLAYED:
            out["replayed"] += 1
        else:
            out["unreplayed"] += 1
        if report.deferred:
            # The ceiling is pass-scoped, so the next proposal would defer identically. Stop
            # and leave the rest with no report at all — an absent report is honestly
            # "not run yet", while attaching a deferral to a proposal whose cases never
            # started would claim a measurement attempt that did not happen.
            break
    return out


def summarize_pass(result: dict[str, Any]) -> str:
    """A one-line note for the curator tick's log, or "" when nothing happened."""
    if not result or not result.get("considered"):
        return ""
    parts = [f"considered={result['considered']}"]
    for key in ("replayed", "deferred", "unreplayed"):
        if result.get(key):
            parts.append(f"{key}={result[key]}")
    return "replay " + " ".join(parts)
