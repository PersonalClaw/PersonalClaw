"""Retroactive promotion — what already WORKED becomes a skill PROPOSAL (LEARN E1.3 — WF2LEA-11).

The pieces around this already existed, which is why this module is small:

* ``skill_remember`` captures a skill at the moment the user TEACHES one — a session-live
  ephemeral draft (`skills/ephemeral.py`) the end-of-chat prompt persists or forgets. Prompted,
  in-the-moment, and it already never writes a live skill on its own.
* ``learning.proposals`` owns the one human-gated queue (§2.2), with the content fingerprint and
  the prior-decision block that make a declined suggestion stay declined.
* §3.2 already mines finished runs — but for *templates*: ``mining.positive_path_candidates`` →
  ``mining.file_positive_trace`` files recurring successful step sequences as ``Kind.TEMPLATE``.

The gap is the skill half of that last one: nothing turned a completed run or conversation into a
skill. ``Kind.SKILL`` was declared for exactly this and had **no writer** — the inbox even carries
its label ("New skill proposed") — so what this module does is fill the reserved slot in the queue
that already exists. No second queue and no new store: filing goes through ``proposals.enqueue``,
and installing goes through ``SkillsLoader.create_auto_skill``, the same rail every autonomously
authored skill has always been written by.

**Propose, never write.** Promotion produces a PENDING row and nothing else — no skill directory,
no ``SKILL.md``. The write lives in :func:`install_accepted_skill`, which ``proposals.accept`` runs
only after ``require_human``, so the agent that proposed a skill cannot be the actor that installs
it. That asymmetry is the whole point of the retroactive path: mining what worked is cheap and
often wrong, and a cheap wrong suggestion is only safe if a person stands between it and the
library.

**Deterministic, no model here.** The agent has the run or the conversation in context and names
the candidate; this module VALIDATES and ROUTES it. Same stance as ``project_context_review``: no
LLM at promotion time, so the path stays off the degraded-LLM surface map and a promotion is a pure
function of its inputs plus the run's recorded status.

**Unprompted is the same call.** An agent that noticed it re-derived a procedure may promote it
without being asked, because the outcome is identical either way — one reviewable row. Provenance
is stamped ``inferred`` regardless of who initiated it: an agent's report that the user asked is
not evidence that the user asked, and ``inferred`` is the conservative label (it decays faster and
scores below a real human correction).

**Decision memory is reused, not reinvented.** ``proposals.enqueue`` fingerprints (kind, target,
body) and returns ``SKIP`` when a prior decision ACCEPTED or REJECTED that fingerprint. So a
promotion the user rejected does not come back on the next run of the same procedure — clause 3 of
the contract is the queue's existing machinery, wired through rather than rebuilt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from personalclaw.learning import proposals

logger = logging.getLogger(__name__)

#: Separates the skill slug from its description inside a proposal's ``target``. The unit
#: separator, mirroring `project_context_review`: it is neither a slug nor a prose character, so
#: neither half can be mistaken for the other when the installer decodes it back.
_TARGET_SEP = "\x1f"

#: Evidence-excerpt cap fed to `enqueue`, which fences and re-caps it. This bound just keeps the
#: join cheap on a long session.
_EXCERPT_MAX = 4_000

#: Stamped on every promotion. Durable provenance for "where did this row come from", and what
#: tells a promotion apart from §3.2's template miner inside the same queue.
SOURCE_CADENCE = "skill_promotion"


class Refusal(str, Enum):
    """Why a promotion filed nothing.

    Typed for the same reason `mining.Miss` is: the caller reports the refusal back to the agent
    that asked, and a vague no teaches it nothing — the model that named a bad candidate is the one
    that can fix it. A refusal is never an error; nothing was written either way.
    """

    NEEDS_NAME = "needs_name"
    NEEDS_DESCRIPTION = "needs_description"
    NEEDS_PROCEDURE = "needs_procedure"
    NEEDS_RATIONALE = "needs_rationale"
    #: The name could not be reduced to a slug `create_auto_skill` would accept.
    UNUSABLE_NAME = "unusable_name"
    #: Longer than the auto-skill procedure cap — filing it would queue a row that cannot install.
    PROCEDURE_TOO_LONG = "procedure_too_long"
    RUN_NOT_FOUND = "run_not_found"
    #: Terminal but not COMPLETE (or still running): only success is worth promoting.
    RUN_NOT_SUCCESSFUL = "run_not_successful"
    #: A prior accept/reject of this exact content blocks the re-file — the anti-nag path.
    ALREADY_DECIDED = "already_decided"
    QUEUE_REFUSED = "queue_refused"


@dataclass
class Promotion:
    """The outcome of one promotion attempt. Exactly one of `proposal` / `refusal` is set."""

    proposal: proposals.Proposal | None = None
    refusal: str = ""
    #: The `Verdict` the queue returned when a row was filed or reinforced.
    verdict: str = ""

    @property
    def filed(self) -> bool:
        return self.proposal is not None


def _encode_target(slug: str, description: str) -> str:
    return f"{slug}{_TARGET_SEP}{description}"


def _decode_target(target: str) -> tuple[str, str]:
    """Split a proposal's target back into ``(slug, description)``.

    Tolerant of a bare slug so a hand-edited record still installs with the slug as its own
    description — `create_auto_skill` already falls back to the name when the description is empty.
    """
    if _TARGET_SEP in target:
        slug, description = target.split(_TARGET_SEP, 1)
        return slug, description
    return target, ""


def _evidence(run_id: str, transcript: list[dict] | None) -> str:
    """A review-only excerpt of what drove the promotion, or "".

    The user turns only. The promotion's evidence is what the USER asked for and confirmed worked;
    an assistant turn pasted here would let the model's own prose become the justification for a
    standing procedure — the injection surface the queue's fencing exists to bound. A run needs no
    transcript: its id is the pointer, and the ledger behind it is the evidence.
    """
    parts = [f"run: {run_id}"] if run_id else []
    for message in transcript or []:
        if isinstance(message, dict) and message.get("role") == "user":
            text = str(message.get("content") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)[:_EXCERPT_MAX]


def _run_refusal(run_id: str) -> str:
    """Check a run is real and COMPLETE. Returns a `Refusal` value, or "" when it may be promoted.

    Success is the gate the plan's positive-path mining uses (`mining._is_successful`) and it is
    checked against the STORE rather than trusted from the caller: "promote what worked" is only
    meaningful if something other than the proposing agent decides what worked. Compared against
    `RunStatus.COMPLETE` so this cannot drift from the engine's own notion of a successful run.
    """
    try:
        from personalclaw.workflows import store as run_store
        from personalclaw.workflows.models import RunStatus
    except Exception:  # pragma: no cover - import failure is not a runtime path
        logger.debug("skill promotion: run store unavailable", exc_info=True)
        return Refusal.RUN_NOT_FOUND.value
    try:
        run = run_store.get(run_id)
    except Exception:
        logger.debug("skill promotion: run lookup failed for %s", run_id, exc_info=True)
        return Refusal.RUN_NOT_FOUND.value
    if run is None:
        return Refusal.RUN_NOT_FOUND.value
    status = getattr(run, "status", "")
    if str(getattr(status, "value", status) or "").lower() != RunStatus.COMPLETE.value:
        return Refusal.RUN_NOT_SUCCESSFUL.value
    return ""


def _manifest(slug: str, rationale: str, evidence_ref: str) -> proposals.ChangeManifest:
    """The change manifest for a promotion (LEARN-R16).

    `enqueue` flags a ``skill`` proposal carrying no manifest as ``manifest_valid=False``, so every
    promotion would otherwise render permanently warning in the inbox. The fields read oddly at
    first because they are named for fix-shaped proposals — but a promotion IS gap-shaped, and §3.2
    names the gap outright: the library had no entry, so the procedure got re-derived ad hoc. That
    registry miss is the failure pattern, and installing the skill is the targeted fix.
    """
    return proposals.ChangeManifest(
        component=f"skills/auto/{slug}",
        files=[f"skills/auto/{slug}/SKILL.md"],
        failure_pattern="no skill covered this procedure, so it was worked out from scratch",
        evidence_refs=[evidence_ref] if evidence_ref else [],
        root_cause=rationale,
        targeted_fix=f"install auto/{slug} so the next occurrence loads the procedure",
    )


def promote(
    *,
    name: str,
    description: str,
    procedure: str,
    rationale: str,
    run_id: str = "",
    session_key: str = "",
    transcript: list[dict] | None = None,
) -> Promotion:
    """Promote a completed run or conversation into a skill PROPOSAL. Writes NO skill.

    ``run_id`` is optional and that asymmetry is deliberate: a run has a recorded status this can
    verify, a conversation does not, so a run must be COMPLETE while a conversation is promoted on
    the strength of the reviewer reading it. Either way the result is one PENDING row.

    `rationale` becomes the proposal title — the line a human reads before deciding — and is kept
    out of `body`, which is the exact procedure a later accept writes. Conflating them would leak
    "why I proposed this" into the installed skill.
    """
    slug_source = str(name or "").strip()
    description = str(description or "").strip()
    procedure = str(procedure or "").strip()
    rationale = str(rationale or "").strip()
    run_id = str(run_id or "").strip()

    if not slug_source:
        return Promotion(refusal=Refusal.NEEDS_NAME.value)
    if not description:
        return Promotion(refusal=Refusal.NEEDS_DESCRIPTION.value)
    if not procedure:
        return Promotion(refusal=Refusal.NEEDS_PROCEDURE.value)
    if not rationale:
        # A promotion without a reason is one a reviewer cannot weigh, and weighing it is the
        # entire function of the queue.
        return Promotion(refusal=Refusal.NEEDS_RATIONALE.value)

    from personalclaw.skills import AUTO_SKILL_MAX_PROCEDURE_CHARS, _auto_name_from_title

    slug = _auto_name_from_title(slug_source)
    if not slug:
        return Promotion(refusal=Refusal.UNUSABLE_NAME.value)
    if len(procedure) > AUTO_SKILL_MAX_PROCEDURE_CHARS:
        # Pre-checked because the install rail rejects it: filing here would queue a row whose
        # accept can only ever fail, which is a worse outcome than saying so now.
        return Promotion(refusal=Refusal.PROCEDURE_TOO_LONG.value)

    if run_id:
        refusal = _run_refusal(run_id)
        if refusal:
            return Promotion(refusal=refusal)

    target = _encode_target(slug, description)
    verdict, proposal = proposals.enqueue(
        kind=proposals.Kind.SKILL.value,
        title=rationale[:120],
        body=procedure,
        target=target,
        provenance="inferred",
        source_cadence=SOURCE_CADENCE,
        session_key=session_key,
        run_id=run_id,
        source_excerpt=_evidence(run_id, transcript),
        evidence_refs=[run_id] if run_id else [],
        change_manifest=_manifest(slug, rationale, run_id or session_key),
        tags=["skill_promotion", "positive_path"],
    )
    if proposal is None:
        # `enqueue` returns SKIP for a prior decision AND for a write it could not complete, so the
        # verdict alone cannot tell them apart. Decision memory is asked directly instead: reporting
        # a failed write as "you already decided this" would send the agent away from a retry it
        # should make, and reporting the reverse would nag about a settled question.
        fingerprint = proposals.content_fingerprint(proposals.Kind.SKILL.value, target, procedure)
        decided = fingerprint in proposals.load_decisions()
        return Promotion(
            refusal=(Refusal.ALREADY_DECIDED.value if decided else Refusal.QUEUE_REFUSED.value)
        )
    return Promotion(proposal=proposal, verdict=verdict.value)


# ── the accept-time installer (the one path that WRITES) ──


def is_skill_promotion_proposal(proposal_dict: dict) -> bool:
    """Whether an accepted proposal is a skill this installer owns.

    Keyed on the KIND, like the project-context installer, not on the cadence or a tag: those are
    reviewer-editable, and a record whose writer can be re-routed by editing a label is a record
    whose write path is not actually pinned. ``Kind.SKILL`` has exactly one producer — `promote` —
    so the kind alone is unambiguous.
    """
    return str(proposal_dict.get("kind") or "") == proposals.Kind.SKILL.value


def install_accepted_skill(proposal_dict: dict) -> str:
    """Write EXACTLY one accepted skill. Called only AFTER ``require_human``. Returns its name.

    Through ``create_auto_skill`` — the existing auto-skill rail, so a promoted skill lands in the
    same ``auto/`` namespace, carries the same ``source: auto`` provenance frontmatter, and ages
    under the same curator as every other autonomously authored skill. Raises on a write it cannot
    complete (an invalid slug, a name already taken) so ``accept`` reports the failure and does NOT
    record the decision — a failed install stays retryable instead of silently suppressing itself.
    """
    from personalclaw.skills import AutoSkillProvenance, SkillsLoader

    slug, description = _decode_target(str(proposal_dict.get("target") or ""))
    procedure = str(proposal_dict.get("body") or "").strip()
    if not slug or not procedure:
        raise ValueError("a skill promotion needs a slug and a procedure")
    provenance = AutoSkillProvenance(
        session_key=str(proposal_dict.get("session_key") or ""),
        created_at=str(proposal_dict.get("created_at") or "") or AutoSkillProvenance.now_iso(),
    )
    created = SkillsLoader(install_builtins=False).create_auto_skill(
        slug,
        description=description,
        # No triggers. The description is what lists a skill for the model to reach for; triggers
        # AUTO-INJECT it, and a freshly promoted guess should earn its way in by being chosen, not
        # arrive already claiming every turn that mentions one of its words.
        triggers="",
        procedure_md=procedure,
        provenance=provenance,
    )
    if not created:
        raise ValueError(f"could not create skill auto/{slug} (invalid name or it already exists)")
    return created


def candidate_files(proposal_dict: dict) -> dict[str, str]:
    """What an accept of this proposal WOULD write: home-relative path → content.

    ES-6's gate needs the candidate artifact to stage in a throwaway home so a planted
    regression is measurable before the user accepts. It lives here rather than in
    :mod:`personalclaw.evals.gate` because this module owns the target encoding and the
    install rail — the gate must not learn to spell an ``auto/`` skill a second way.

    The content comes from :meth:`~personalclaw.skills.SkillsLoader.create_auto_skill` writing
    into a TEMP skills root, then being read back. Going through the real rail is the whole
    point: the frontmatter, the ``source: auto`` provenance and the name are byte-for-byte what
    :func:`install_accepted_skill` would produce, so the gate scores the artifact that would
    ship and not a re-rendered approximation of it. Nothing under the live home is touched.

    Returns ``{}`` — never raises — when the proposal cannot render one (no slug, no procedure,
    an invalid name). The caller reads that as "ungated", which is the honest outcome: a
    candidate nobody can stage is a candidate nobody can score.
    """
    import tempfile

    from personalclaw.skills import AutoSkillProvenance, SkillsLoader
    from personalclaw.skills.loader import SKILLS_DIR_NAME

    slug, description = _decode_target(str(proposal_dict.get("target") or ""))
    procedure = str(proposal_dict.get("body") or "").strip()
    if not slug or not procedure:
        return {}
    provenance = AutoSkillProvenance(
        session_key=str(proposal_dict.get("session_key") or ""),
        created_at=str(proposal_dict.get("created_at") or "") or AutoSkillProvenance.now_iso(),
    )
    with tempfile.TemporaryDirectory(prefix="pclaw_gate_candidate_") as tmp:
        root = Path(tmp)
        try:
            created = SkillsLoader(skills_path=root, install_builtins=False).create_auto_skill(
                slug,
                description=description,
                triggers="",
                procedure_md=procedure,
                provenance=provenance,
            )
        except Exception:
            logger.debug("candidate render failed for %r", slug, exc_info=True)
            return {}
        if not created:
            return {}
        rendered = root / created / "SKILL.md"
        if not rendered.is_file():
            return {}
        # The relpath the CHILD will stage: the skills dir's own name plus what the rail chose.
        # Derived from the rail's return value rather than re-spelled, so a namespace change in
        # `create_auto_skill` moves both halves at once.
        relpath = f"{SKILLS_DIR_NAME}/{created}/SKILL.md"
        return {relpath: rendered.read_text(encoding="utf-8")}
