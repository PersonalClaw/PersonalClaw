"""Calibration — is the judge actually judging, or just nodding?

A judge that always passes is worse than no judge. No judge is an absence you can see;
a judge with a 100% pass rate looks like a working control, reads as evidence in the
ledger, and licenses everything downstream to trust output nobody checked. So the
question "does this instrument discriminate?" has to be asked of the instrument itself,
with data rather than intent.

Three mechanisms, all free of model calls:

**The nodding-loop detector.** Over N runs, a gate that has never once rejected is
statistical evidence of a fake check. It blocks the template from becoming its kind's
default and surfaces as a warning badge. The threshold is deliberately generous — a
genuinely good template on easy work will pass a lot — but "never, across enough runs to
matter" is a different claim from "usually".

**Divergence events.** When a human overrides a verdict, that disagreement is the single
most valuable calibration datum available: a labelled example of the judge being wrong,
free, from the one source that outranks it. Recorded as a first-class ledger event so
the judge prompt can later be patched at exactly those points.

**Stuck detection off journal data.** Byte-identical scores across N cycles, or N
consecutive failed cycles, auto-pauses the run. Zero LLM calls — a loop grinding at the
same score for five iterations has told you it is stuck, and paying a model to confirm
it is paying to be told what the numbers already say.

The verdict ledger underneath is what makes all three possible, which is why it records
REJECTS and discarded iterations rather than only the kept ones. A ledger of successes
is a ledger that cannot answer whether the judge works.
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

#: Runs a gate must have before a 100%-pass record means anything. Below this, a clean
#: sweep is small-sample noise rather than evidence — accusing a new template of nodding
#: on its third run would train authors to distrust the detector.
NODDING_MIN_RUNS = 8

#: Consecutive identical scores that mark a loop as stuck. Five is the plan's proven
#: value: three still happens on genuinely plateaued-then-improving work.
STUCK_IDENTICAL_SCORES = 5

#: Consecutive outright failures before auto-pause.
STUCK_FAILED_CYCLES = 3

#: The strong-vs-null separation a judge must show to be considered calibrated. Carried
#: from `loop/instrument.py`'s existing canary so the two instruments agree on what
#: "blind" means — a second, different threshold would make the same judge trustworthy
#: to one caller and blind to another.
CANARY_MIN_SEPARATION = 1.5


class GateHealth(str, Enum):
    """What the evidence says about a gate."""

    #: Rejects sometimes, passes sometimes. The only healthy state.
    DISCRIMINATING = "discriminating"
    #: Never rejected across enough runs to matter.
    NODDING = "nodding"
    #: Never passed — the mirror failure, and just as broken.
    OBSTRUCTING = "obstructing"
    #: Not enough runs yet to say.
    UNPROVEN = "unproven"


@dataclass
class VerdictRecord:
    """One judge verdict, as journaled. The unit the calibration reads.

    Discarded iterations are recorded too (`status="discard"`). A ledger that keeps only
    the verdicts that stuck cannot answer "does this judge ever reject?", which is the
    one question the detector exists to ask.
    """

    run_id: str
    node_id: str
    template: str
    verdict: str
    #: Engine-computed overall, never the model's self-report.
    overall: float = 0.0
    scores: dict[str, int] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    proof: str = ""
    #: "kept" | "discard" — a rewound or superseded iteration is still evidence.
    status: str = "kept"
    #: The prompt version that produced it, so a verdict is attributable to the wording
    #: that caused it. Patching a judge prompt invalidates comparisons across the change.
    prompt_version: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "node_id": self.node_id,
            "template": self.template,
            "verdict": self.verdict,
            "overall": self.overall,
            "scores": dict(self.scores),
            "evidence_refs": list(self.evidence_refs),
            "proof": self.proof[:2000],
            "status": self.status,
            "prompt_version": self.prompt_version,
            "created_at": self.created_at or _now(),
        }


@dataclass
class DivergenceRecord:
    """A human overriding a judge. The most valuable calibration datum there is.

    Free, labelled, and from the one source that outranks the judge. `reason` is captured
    verbatim rather than categorised: the user's own words are what a later few-shot
    exemplar needs, and a dropdown would collapse exactly the detail that makes the
    example teach anything.
    """

    run_id: str
    node_id: str
    template: str
    judge_verdict: str
    human_verdict: str
    reason: str = ""
    prompt_version: str = ""
    created_at: str = ""

    @property
    def direction(self) -> str:
        """Which way the judge was wrong.

        `false_pass` is the dangerous direction — the judge approved something the human
        rejected, which is the failure that ships. `false_reject` costs a cycle.
        """
        judged_pass = self.judge_verdict.upper() == "PASS"
        human_pass = self.human_verdict.upper() == "PASS"
        if judged_pass and not human_pass:
            return "false_pass"
        if human_pass and not judged_pass:
            return "false_reject"
        return "agreement"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "node_id": self.node_id,
            "template": self.template,
            "judge_verdict": self.judge_verdict,
            "human_verdict": self.human_verdict,
            "direction": self.direction,
            "reason": self.reason[:1000],
            "prompt_version": self.prompt_version,
            "created_at": self.created_at or _now(),
        }


# ── The nodding-loop detector ──


@dataclass
class GateReport:
    """The verdict on a gate's own trustworthiness."""

    template: str
    node_id: str
    health: GateHealth
    runs: int = 0
    passes: int = 0
    rejects: int = 0
    detail: str = ""

    @property
    def pass_rate(self) -> float | None:
        """None when there is no data — NOT 0.0, which would read as "always fails"."""
        return (self.passes / self.runs) if self.runs else None

    @property
    def blocks_default(self) -> bool:
        """May this template become its kind's default replacement?

        Only a NODDING gate blocks. An OBSTRUCTING one is broken too, but in the safe
        direction — it fails work that should pass, which is visible and annoying rather
        than invisible and trusted.
        """
        return self.health is GateHealth.NODDING


def assess_gate(
    records: list[VerdictRecord],
    *,
    template: str = "",
    node_id: str = "",
    min_runs: int = NODDING_MIN_RUNS,
) -> GateReport:
    """Does this gate discriminate? Reads the ledger; calls no model.

    Counts DISCARDED verdicts too. A rewound iteration's rejection really happened, and
    excluding it would let a template look like a nodder precisely because its judge was
    doing its job and forcing rewinds.
    """
    relevant = [
        r
        for r in records
        if (not template or r.template == template) and (not node_id or r.node_id == node_id)
    ]
    passes = sum(1 for r in relevant if r.verdict.upper() == "PASS")
    rejects = len(relevant) - passes
    report = GateReport(
        template=template,
        node_id=node_id,
        health=GateHealth.UNPROVEN,
        runs=len(relevant),
        passes=passes,
        rejects=rejects,
    )

    if len(relevant) < max(1, min_runs):
        report.detail = f"only {len(relevant)} verdict(s); need {min_runs} to judge the judge"
        return report

    if rejects == 0:
        report.health = GateHealth.NODDING
        report.detail = (
            f"passed {passes}/{passes} — a gate that has never rejected across {passes} runs is "
            "statistical evidence of a check that does not check"
        )
        return report
    if passes == 0:
        report.health = GateHealth.OBSTRUCTING
        report.detail = f"rejected {rejects}/{rejects} — nothing has ever passed this gate"
        return report

    report.health = GateHealth.DISCRIMINATING
    report.detail = f"{passes} pass / {rejects} reject — the instrument discriminates"
    return report


def assess_all_gates(
    records: list[VerdictRecord], *, min_runs: int = NODDING_MIN_RUNS
) -> list[GateReport]:
    """One report per (template, node) pair seen in the ledger."""
    pairs = sorted({(r.template, r.node_id) for r in records})
    return [assess_gate(records, template=t, node_id=n, min_runs=min_runs) for t, n in pairs]


def may_become_default(
    records: list[VerdictRecord], *, template: str, min_runs: int = NODDING_MIN_RUNS
) -> tuple[bool, str]:
    """R6a: may this template become its kind's default replacement?

    A template is blocked when ANY of its judge gates is a nodding loop — a 100% pass rate
    over ≥ `min_runs` real verdicts is statistical proof of a check that does not check
    (LOOPS-EVOLUTION R6 criterion 1). Returns `(allowed, reason)`; the reason is empty when
    allowed, else names the offending gate so it can surface as the template's warning badge.
    A template with too few verdicts to judge is NOT blocked — the detector reports UNPROVEN
    rather than punishing a template for being new.
    """
    for report in assess_all_gates(records, min_runs=min_runs):
        if report.template == template and report.blocks_default:
            return False, f"nodding gate {report.node_id!r}: {report.detail}"
    return True, ""


# ── Stuck detection ──


@dataclass
class StuckVerdict:
    stuck: bool = False
    reason: str = ""
    detail: str = ""


def detect_stuck(
    scores: list[float],
    *,
    failures: int = 0,
    identical_window: int = STUCK_IDENTICAL_SCORES,
    failure_window: int = STUCK_FAILED_CYCLES,
) -> StuckVerdict:
    """Is this loop stuck? Pure arithmetic over journal data — no model call.

    A loop grinding at the same score for five iterations has already told you it is
    stuck; paying a model to confirm that is paying to be told what the numbers say.

    Byte-identical is the test, not "similar": a score that moves by 0.01 each cycle is
    converging slowly, which is a different situation from one that has not moved at all,
    and conflating them would pause runs that were still making progress.
    """
    if failures >= max(1, failure_window):
        return StuckVerdict(
            True,
            "consecutive_failures",
            f"{failures} cycles failed in a row",
        )
    if len(scores) >= max(2, identical_window):
        recent = scores[-identical_window:]
        if len(set(recent)) == 1:
            return StuckVerdict(
                True,
                "identical_scores",
                f"score {recent[0]} unchanged across {identical_window} cycles",
            )
    return StuckVerdict(False)


# ── Judge calibration probe ──


@dataclass
class CalibrationResult:
    """Whether a judge separates good work from nothing."""

    calibrated: bool | None
    separation: float | None = None
    detail: str = ""

    @property
    def blind(self) -> bool:
        """True only when the probe RAN and failed.

        `None` (the probe could not run) is deliberately not blind: declaring a judge
        untrustworthy because the probe itself broke would halt runs for an
        infrastructure problem, and that is a false accusation with real cost.
        """
        return self.calibrated is False


def assess_separation(
    strong_score: float | None,
    null_score: float | None,
    *,
    min_separation: float = CANARY_MIN_SEPARATION,
) -> CalibrationResult:
    """Judge the judge from its own two probe scores.

    Pure, so it can be tested without a model — the probe that FEEDS it needs one, but
    the decision does not, and separating them means the threshold is testable.
    """
    if strong_score is None or null_score is None:
        return CalibrationResult(
            None, None, "the probe could not produce both scores — deferring, not declaring blind"
        )
    separation = round(float(strong_score) - float(null_score), 4)
    if separation >= min_separation:
        return CalibrationResult(True, separation, f"separation {separation} >= {min_separation}")
    return CalibrationResult(
        False,
        separation,
        f"separation {separation} < {min_separation} — this judge does not distinguish "
        "strong work from nothing, so its verdicts carry no information",
    )


# ── The hardening loop ──


def divergence_exemplars(
    divergences: list[DivergenceRecord], *, limit: int = 5
) -> list[dict[str, Any]]:
    """Turn overrides into few-shot exemplars for patching a judge prompt.

    False passes come first. A judge that approves bad work ships it; a judge that
    rejects good work costs a cycle and gets noticed. Ordering by which error is worse
    means a bounded exemplar list spends its budget on the dangerous direction.
    """
    ordered = sorted(
        divergences,
        key=lambda d: (0 if d.direction == "false_pass" else 1, d.created_at),
    )
    out: list[dict[str, Any]] = []
    for record in ordered[:limit]:
        if record.direction == "agreement":
            continue
        out.append(
            {
                "the_judge_said": record.judge_verdict,
                "the_user_said": record.human_verdict,
                "why_the_user_was_right": record.reason,
                "lesson": (
                    "Do not approve work of this shape."
                    if record.direction == "false_pass"
                    else "Do not reject work of this shape."
                ),
            }
        )
    return out


def calibration_summary(
    verdicts: list[VerdictRecord], divergences: list[DivergenceRecord]
) -> dict[str, Any]:
    """The one-glance answer to "is the flywheel's instrument working?".

    Reports `false_pass_rate` separately from a combined accuracy number: an instrument
    that is 90% accurate overall but wrong in the dangerous direction every time is not
    90% good, and a single averaged figure would hide exactly that.
    """
    kept = [v for v in verdicts if v.status == "kept"]
    passes = sum(1 for v in kept if v.verdict.upper() == "PASS")
    false_passes = sum(1 for d in divergences if d.direction == "false_pass")
    false_rejects = sum(1 for d in divergences if d.direction == "false_reject")
    overalls = [v.overall for v in kept if v.overall]
    return {
        "verdicts": len(verdicts),
        "kept": len(kept),
        "discarded": len(verdicts) - len(kept),
        "pass_rate": round(passes / len(kept), 4) if kept else None,
        "median_overall": round(statistics.median(overalls), 4) if overalls else None,
        "divergences": len(divergences),
        "false_passes": false_passes,
        "false_rejects": false_rejects,
        "false_pass_rate": (round(false_passes / len(kept), 4) if kept else None),
        "nodding_gates": [
            {"template": r.template, "node": r.node_id, "detail": r.detail}
            for r in assess_all_gates(verdicts)
            if r.health is GateHealth.NODDING
        ],
    }


# ── Persistence ──


def journal_verdict(record: VerdictRecord) -> dict[str, Any]:
    """The ledger entry for a verdict. Shaped for `journal.JUDGE_VERDICT`."""
    return {"kind": "judge_verdict", **record.to_dict()}


def journal_divergence(record: DivergenceRecord) -> dict[str, Any]:
    """The ledger entry for an override. Shaped for `journal.JUDGE_DIVERGENCE`."""
    return {"kind": "judge_divergence", **record.to_dict()}


def verdicts_from_journal(entries: list[dict[str, Any]]) -> list[VerdictRecord]:
    """Read verdict records back out of journal entries, tolerating malformed rows.

    One unreadable row must not make the whole calibration unreadable — the detector's
    answers degrade gracefully with missing data, and refusing to report at all because
    of a single bad line is the worse failure.
    """
    out: list[VerdictRecord] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("kind") != "judge_verdict":
            continue
        try:
            out.append(
                VerdictRecord(
                    run_id=str(entry.get("run_id", "")),
                    node_id=str(entry.get("node_id", "")),
                    template=str(entry.get("template", "")),
                    verdict=str(entry.get("verdict", "")),
                    overall=float(entry.get("overall", 0.0) or 0.0),
                    scores=(
                        {
                            str(k): int(val)
                            for k, val in entry["scores"].items()
                            if isinstance(val, (int, float))
                        }
                        if isinstance(entry.get("scores"), dict)
                        else {}
                    ),
                    evidence_refs=[str(x) for x in (entry.get("evidence_refs") or [])],
                    proof=str(entry.get("proof", "")),
                    status=str(entry.get("status", "kept") or "kept"),
                    prompt_version=str(entry.get("prompt_version", "")),
                    created_at=str(entry.get("created_at", "")),
                )
            )
        except (AttributeError, TypeError, ValueError):
            # AttributeError included deliberately: a field holding the WRONG TYPE (a
            # string where a dict belongs) raises it, and the whole point of this loop is
            # that one unreadable row cannot make the calibration unreadable.
            logger.debug("skipping malformed verdict entry", exc_info=True)
    return out


def divergences_from_journal(entries: list[dict[str, Any]]) -> list[DivergenceRecord]:
    out: list[DivergenceRecord] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or entry.get("kind") != "judge_divergence":
            continue
        try:
            out.append(
                DivergenceRecord(
                    run_id=str(entry.get("run_id", "")),
                    node_id=str(entry.get("node_id", "")),
                    template=str(entry.get("template", "")),
                    judge_verdict=str(entry.get("judge_verdict", "")),
                    human_verdict=str(entry.get("human_verdict", "")),
                    reason=str(entry.get("reason", "")),
                    prompt_version=str(entry.get("prompt_version", "")),
                    created_at=str(entry.get("created_at", "")),
                )
            )
        except (AttributeError, TypeError, ValueError):
            logger.debug("skipping malformed divergence entry", exc_info=True)
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def prompt_version(prompt: str) -> str:
    """A short stable fingerprint of a judge prompt.

    Verdicts are attributable to the wording that produced them, so a prompt patch does
    not silently invalidate the comparison across it. Without this, the hardening loop's
    own improvements would look like judge drift.
    """
    import hashlib

    normalized = " ".join((prompt or "").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def as_json(payload: Any) -> str:
    """Stable JSON for a ledger row."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
