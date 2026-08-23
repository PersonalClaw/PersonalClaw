"""The skills/templates impact bench (EVALUATION-SUBSTRATE §3.3).

OpenJarvis' ``jarvis bench skills`` measurement, on this repo's machinery: for a given
skill, replay the runs that ACTUALLY loaded it — the WF2-R13 ``consulted`` ledger event
says which — with the skill **surfaced vs suppressed**, and report the outcome delta.

Two things make this bench honest rather than decorative:

1. **Suppression is verified, not assumed.** A "suppressed" arm whose prompt still carries
   the skill body measures nothing and reports a delta of ~0 — which reads exactly like a
   skill that does not matter, the wrong conclusion drawn confidently.
   :func:`verify_suppression` assembles the real prompt blocks through the real allocator
   (:func:`personalclaw.skills.allocation.allocate_skills`) for both arms and checks that
   the body is present in the surfaced arm and absent from the suppressed one. The
   PRESENCE half is not decoration: an empty prompt set satisfies "the body is absent"
   trivially, so the negative is only evidence when paired with the positive.
2. **An unverified suppression refuses to produce a verdict.** :func:`bench_skill` returns
   ``INCONCLUSIVE`` with a reason instead of a delta, on the §1.2 principle that a
   measurement that could not run is never reported as a zero.

Arm vocabulary: "surfaced"/"suppressed" is how §3.3 SAYS on/off for a skill, so it is an
ALIAS of the shared :mod:`personalclaw.evals.overlay` arms, not a second dialect. Minting a
second set of arm names would give the substrate two vocabularies for one axis.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from personalclaw.evals import ablation
from personalclaw.evals import overlay as overlay_lib
from personalclaw.evals.matrix import MatrixSpec, aggregate_by

logger = logging.getLogger(__name__)

#: §3.3's names for the shared arms. Aliases, deliberately — one axis, one vocabulary.
ARM_SURFACED = overlay_lib.ARM_ON
ARM_SUPPRESSED = overlay_lib.ARM_OFF

#: How many recent runs the consulted-event scan walks back through. A bench replays a
#: skill's own history, not the whole ledger.
DEFAULT_RUN_SCAN = 200

#: How much of the body has to appear in a prompt for the skill to count as present. The
#: allocator may REDUCE a skill to a summary at a lower tier, so requiring the whole body
#: would report a legitimately budgeted surfaced arm as suppressed.
_BODY_PROBE_CHARS = 120


# ── the consulted-run source (WF2-R13) ───────────────────────────────────────


def consulted_runs(skill_name: str, *, limit: int = DEFAULT_RUN_SCAN) -> list[dict[str, Any]]:
    """Runs whose ledger says they CONSULTED ``skill_name``.

    Reads the WF2-R13 ``consulted`` event (``personalclaw.ledger.kinds.CONSULTED``) written
    by :meth:`personalclaw.workflows.journal.WorkflowJournal.consulted`, matching on the
    event's ``ref``. Returns ``[]`` when nothing consulted the skill — and callers must
    treat that as "no population to bench", NOT as a zero delta.
    """
    if not skill_name:
        return []
    from personalclaw.ledger.kinds import CONSULTED
    from personalclaw.workflows import store as wf_store

    out: list[dict[str, Any]] = []
    try:
        runs, _total = wf_store.list_runs(limit=max(1, int(limit)))
    except Exception:
        logger.debug("consulted-run scan could not list runs", exc_info=True)
        return []
    for run in runs:
        run_id = str(getattr(run, "id", "") or "")
        if not run_id:
            continue
        try:
            events = wf_store.read_jsonl(run_id, "events.jsonl")
        except Exception:
            continue
        for event in events:
            if event.get("kind") != CONSULTED:
                continue
            ref = str(event.get("ref") or "")
            if not _ref_names_skill(ref, skill_name):
                continue
            out.append(
                {
                    "run_id": run_id,
                    "node_id": str(event.get("node_id") or ""),
                    "ref": ref,
                    "ts": str(event.get("ts") or ""),
                    "workflow_name": str(getattr(run, "workflow_name", "") or ""),
                }
            )
    return out


def _ref_names_skill(ref: str, skill_name: str) -> bool:
    """Does a ``consulted`` event's ``ref`` name this skill?

    Matched on the whole ref or on its last path segment, so both ``skill:code/foo`` and a
    bare ``code/foo`` resolve. Deliberately NOT a substring test: ``foo`` must not match
    ``foo-bar``, or one skill's bench would replay another's runs.
    """
    if not ref:
        return False
    candidates = {ref, ref.split(":", 1)[-1]}
    return skill_name in candidates or skill_name == ref.rsplit("/", 1)[-1]


# ── suppression verification (the §3.3 honesty rail) ─────────────────────────


@contextmanager
def _suppressing(skill_name: str):
    """Suppress ``skill_name`` for the duration, then restore the env EXACTLY.

    Used by the in-process verification probe. The real bench arms suppress inside a
    spawned child (:func:`personalclaw.evals.overlay.apply_in_child`); this context manager
    exists so the verification can run without a subprocess, and it restores absence as
    absence rather than as an empty string — an empty string would leave the env var
    present, which is a different state from never having been set.
    """
    from personalclaw.skills.suppression import SUPPRESSED_SKILLS_ENV

    had = SUPPRESSED_SKILLS_ENV in os.environ
    previous = os.environ.get(SUPPRESSED_SKILLS_ENV)
    os.environ[SUPPRESSED_SKILLS_ENV] = skill_name
    try:
        yield
    finally:
        if had:
            os.environ[SUPPRESSED_SKILLS_ENV] = previous or ""
        else:
            os.environ.pop(SUPPRESSED_SKILLS_ENV, None)


def arm_prompt(loader, skill_name: str, *, query: str = "") -> str:
    """The prompt text ``skill_name`` contributes, assembled the way a turn assembles it.

    Mirrors :mod:`personalclaw.context`'s seam exactly — ``load_skill`` for the body, the
    ``if content:`` guard, then :func:`allocate_skills` for the block — so what this returns
    is what the model would see, not a reconstruction of it. A suppressed skill yields
    ``""`` because ``load_skill`` returns ``None`` and the guard drops the request.
    """
    from personalclaw.skills.allocation import SkillRequest, allocate_skills

    content = loader.load_skill(skill_name)
    if not content:
        return ""
    alloc = allocate_skills(loader, [SkillRequest(name=skill_name, content=content)], query=query)
    return "\n".join(block for _name, block in alloc.blocks)


def body_probe(loader, skill_name: str) -> str:
    """A distinctive slice of the skill's own body, for the presence test.

    Taken from the END of the body rather than the start: a skill's opening lines are its
    title and description, which the allocator's REDUCED tier also emits — so a probe taken
    from the top would report a summarized skill as fully present.
    """
    body = loader.load_skill(skill_name) or ""
    stripped = body.strip()
    if len(stripped) <= _BODY_PROBE_CHARS:
        return stripped
    return stripped[-_BODY_PROBE_CHARS:]


@dataclass
class SuppressionCheck:
    """Did suppression actually remove the skill from the prompt?"""

    skill: str
    probe_chars: int = 0
    present_surfaced: bool = False
    present_suppressed: bool = False
    surfaced_prompt_chars: int = 0
    suppressed_prompt_chars: int = 0
    reason: str = ""

    @property
    def verified(self) -> bool:
        """True only when the body is IN the surfaced prompt and OUT of the suppressed one.

        Both halves are required. ``not present_suppressed`` alone is satisfied by an empty
        prompt (or a skill that never loads at all), which is why the positive half is part
        of the predicate rather than a separate nice-to-have.
        """
        return bool(self.probe_chars) and self.present_surfaced and not self.present_suppressed

    def to_dict(self) -> dict:
        return {**asdict(self), "verified": self.verified}


def verify_suppression(loader, skill_name: str, *, query: str = "") -> SuppressionCheck:
    """Assemble both arms in-process and report whether suppression really removes the body."""
    probe = body_probe(loader, skill_name)
    if not probe:
        return SuppressionCheck(skill=skill_name, reason="skill has no loadable body")
    surfaced = arm_prompt(loader, skill_name, query=query)
    with _suppressing(skill_name):
        suppressed = arm_prompt(loader, skill_name, query=query)
    check = SuppressionCheck(
        skill=skill_name,
        probe_chars=len(probe),
        present_surfaced=probe in surfaced,
        present_suppressed=probe in suppressed,
        surfaced_prompt_chars=len(surfaced),
        suppressed_prompt_chars=len(suppressed),
    )
    if not check.present_surfaced:
        check.reason = "the surfaced arm's prompt does not carry the body — nothing to remove"
    elif check.present_suppressed:
        check.reason = "the suppressed arm's prompt STILL carries the body — arms are identical"
    return check


# ── the bench ────────────────────────────────────────────────────────────────


@dataclass
class SkillBenchReport:
    """One skill's surfaced-vs-suppressed impact report."""

    skill: str
    subject: str
    verdict: str = ablation.INCONCLUSIVE
    reason: str = ""
    consulted_run_ids: list[str] = field(default_factory=list)
    suppression: dict = field(default_factory=dict)
    arms: dict[str, dict] = field(default_factory=dict)
    delta: float | None = None
    epsilon: float = ablation.DEFAULT_EPSILON
    matrix_id: str = ""
    trials: int = 0
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def build_spec(
    skill_name: str,
    *,
    subject: str,
    trials: int = 3,
    budget_usd: float = 0.0,
) -> MatrixSpec:
    """The matrix spec for one skill: surfaced vs suppressed on the ``arm_mask`` axis."""
    component = overlay_lib.ComponentOverlay(
        component_id=f"skill:{skill_name}",
        kind=overlay_lib.KIND_SKILL,
        target=skill_name,
        arm=overlay_lib.ARM_ON,
        notes={"bench": "skills", "subject": subject},
    )
    return MatrixSpec(
        subject=subject,
        axes={overlay_lib.ARM_AXIS: [ARM_SURFACED, ARM_SUPPRESSED]},
        trial_count=max(1, int(trials)),
        scorer="assertion",
        budget_usd=float(budget_usd),
        component=component.to_dict(),
    )


def bench_skill(
    skill_name: str,
    *,
    subject: str = "",
    loader=None,
    trials: int = 3,
    budget_usd: float = 0.0,
    epsilon: float = ablation.DEFAULT_EPSILON,
    limit: int = DEFAULT_RUN_SCAN,
    query: str = "",
    now: datetime | None = None,
    run_matrix=None,
) -> SkillBenchReport:
    """Bench one skill over the runs that consulted it, surfaced vs suppressed.

    Refuses (``INCONCLUSIVE`` + a reason, no delta) when: nothing consulted the skill, or
    suppression could not be verified. Both refusals exist because the alternative is a
    fabricated 0.0 delta that reads as "this skill does not earn its place".
    """
    moment = now or datetime.now(tz=timezone.utc)
    report = SkillBenchReport(
        skill=skill_name,
        subject=subject,
        trials=max(1, int(trials)),
        epsilon=float(epsilon),
        created_at=moment.isoformat(),
    )

    consulted = consulted_runs(skill_name, limit=limit)
    report.consulted_run_ids = sorted({row["run_id"] for row in consulted})
    if not report.consulted_run_ids:
        report.reason = (
            "no run's ledger records consulting this skill — there is no replay population, "
            "which is not the same as a zero delta"
        )
        return report

    if loader is None:  # pragma: no cover - the default wiring
        from personalclaw.skills import SkillsLoader

        loader = SkillsLoader()
    check = verify_suppression(loader, skill_name, query=query)
    report.suppression = check.to_dict()
    if not check.verified:
        report.reason = check.reason or "suppression could not be verified"
        return report

    if not subject:
        report.reason = "no benchmark subject: the bench needs a scenario to replay"
        return report

    if run_matrix is None:  # pragma: no cover - the default wiring
        from personalclaw.evals.runner import run_matrix as _default

        run_matrix = _default
    matrix_id = f"skillbench-{skill_name.replace('/', '-')}-{moment.strftime('%Y%m%dT%H%M%SZ')}"
    spec = build_spec(skill_name, subject=subject, trials=trials, budget_usd=budget_usd)
    # The same non-mutation guard the §3.1 runner uses: a bench is an ablation with one
    # component kind, and it must not be the surface where live state gets edited.
    with ablation.live_state_unchanged():
        result = run_matrix(spec, matrix_id=matrix_id)

    report.matrix_id = matrix_id
    report.arms = aggregate_by(list(result.cells), overlay_lib.ARM_AXIS)
    surfaced = (report.arms.get(ARM_SURFACED) or {}).get("mean_score")
    suppressed = (report.arms.get(ARM_SUPPRESSED) or {}).get("mean_score")
    report.verdict = ablation.classify(surfaced, suppressed, None, epsilon=epsilon)
    if surfaced is not None and suppressed is not None:
        report.delta = round(float(surfaced) - float(suppressed), 6)
        report.reason = ""
    else:
        report.reason = "an arm produced no scored cell"
    return report
