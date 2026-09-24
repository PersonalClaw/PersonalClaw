"""Self-updating project context, reviewed and gated (LEARN E1.4 — WF2LEA-12).

`project_context.py` owns what a project KNOWS — the living overview, the wayfinder ledgers,
the operating instructions the block injects. This module is the propose-half of the pattern
the plan names in §(c): you ask the assistant to review a conversation, it proposes updates to
the project's **instructions**, **files**, and **skills** with a reason per item, and *nothing
is written until you accept*.

**Propose, never write.** Like `self_model_observer`, this files typed PROPOSALS through the one
shared human-gated queue (`learning.proposals`) and installs nothing at review time. The three
`project_*` kinds map to the three real sinks a human accept then writes to:

* ``project_instruction`` → appended to the project's ``agent_instructions_template`` (the "how"
  the block renders as instructions);
* ``project_file`` → an inlined context file under the project's ``context/`` dir;
* ``project_skill`` → a new skill via the existing ``SkillsLoader.create_skill`` rail (no new
  store — the plan's E1.3 was explicit that a second skill path is out of scope).

**Deterministic, no model at review time.** The reviewing agent has the conversation in-context
and identifies the candidate changes; this module VALIDATES and ROUTES them — it does not run an
LLM to re-extract them. That keeps the review a pure function over typed candidates plus the
transcript it grounds each proposal in, and keeps this file off the degraded-LLM surface map.

**Decision memory is reused, not reinvented.** Every candidate goes through `proposals.enqueue`,
which already fingerprints the change, suppresses one a prior decision ACCEPTED or REJECTED, and
reinforces a pending duplicate. So a suggestion the user declined does not re-surface on a second
review — clause 4 of the contract is the queue's existing machinery, wired through, not rebuilt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from personalclaw.learning import proposals

logger = logging.getLogger(__name__)

#: The three kinds this reviewer owns, mapped to their human-accept sink. Closed so the tool and
#: the installer agree on exactly which kinds route here — a kind in one set but not the other is
#: how a proposal would file and then have nowhere to install.
PROJECT_KINDS = frozenset(
    {
        proposals.Kind.PROJECT_INSTRUCTION.value,
        proposals.Kind.PROJECT_FILE.value,
        proposals.Kind.PROJECT_SKILL.value,
    }
)

#: Separates the project id from the sub-target (filename / skill name) inside a proposal's
#: ``target``. The unit separator, not a path char, so a filename with a slash cannot be confused
#: for the project boundary. An instruction has no sub-target — its target is the bare project id.
_TARGET_SEP = "\x1f"

#: Transcript excerpt cap fed to `enqueue` as review evidence. `enqueue` fences and re-caps it at
#: `_EXCERPT_MAX`; this bound just keeps the join cheap on a long session.
_EXCERPT_MAX = 4_000


@dataclass
class ReviewCandidate:
    """One reviewer-identified project-context change, before it is a proposal.

    `rationale` is the WHY the reviewer must supply — it becomes the proposal's title, which is
    what a human reads in the queue before deciding. `body` is the exact content a later accept
    writes; keeping the two apart is what stops the rationale leaking into the installed file.
    `name` is the filename (`project_file`) or skill name (`project_skill`); an instruction has
    none because it appends to the single instructions template.
    """

    kind: str
    body: str
    rationale: str
    name: str = ""


def _encode_target(project_id: str, name: str = "") -> str:
    return f"{project_id}{_TARGET_SEP}{name}" if name else project_id


def _decode_target(target: str) -> tuple[str, str]:
    """Split a proposal's target back into ``(project_id, name)``.

    Tolerant of a bare project id (an instruction), so the installer never has to know which kind
    it came from to read the project out.
    """
    if _TARGET_SEP in target:
        project_id, name = target.split(_TARGET_SEP, 1)
        return project_id, name
    return target, ""


def _excerpt(transcript: list[dict] | None) -> str:
    """A review-only excerpt of the conversation, or "".

    The user turns only: the review is about what the USER established for the project, and an
    assistant turn pasted here would let the model's own words become the evidence for a standing
    instruction — the injection surface the queue's fencing exists to bound.
    """
    if not transcript:
        return ""
    parts = [
        str(m.get("content") or "").strip()
        for m in transcript
        if isinstance(m, dict) and m.get("role") == "user"
    ]
    return "\n".join(p for p in parts if p)[:_EXCERPT_MAX]


def project_context_review(
    candidates: list[ReviewCandidate],
    *,
    project_id: str,
    transcript: list[dict] | None = None,
    session_key: str = "",
    run_id: str = "",
) -> list[proposals.Proposal]:
    """Review a conversation's identified changes into typed proposals. Writes NOTHING.

    Each candidate is routed through `proposals.enqueue`, which dedups against pending rows and
    SKIPs anything a prior decision already accepted or rejected — so declining a suggestion once
    keeps it off a second review. Returns the proposals that were actually filed (a SKIP or an
    empty/invalid candidate contributes nothing), so the tool can report exactly what reached the
    queue.
    """
    pid = str(project_id or "").strip()
    if not pid:
        return []
    excerpt = _excerpt(transcript)
    filed: list[proposals.Proposal] = []
    for candidate in candidates:
        kind = str(candidate.kind or "").strip()
        body = str(candidate.body or "").strip()
        rationale = str(candidate.rationale or "").strip()
        if kind not in PROJECT_KINDS or not body or not rationale:
            # A candidate missing its rationale is one a reviewer could not weigh — the whole
            # point of the queue — so it is dropped rather than filed as an unexplained row.
            continue
        name = str(candidate.name or "").strip()
        _verdict, proposal = proposals.enqueue(
            kind=kind,
            title=rationale[:120],
            body=body,
            target=_encode_target(pid, name),
            provenance="inferred",
            # The accept-installer discriminates project-context proposals on THIS cadence — a
            # durable record field, not a tag a reviewer could strip.
            source_cadence="project_context_review",
            session_key=session_key,
            run_id=run_id,
            source_excerpt=excerpt,
            tags=["project_context", kind],
        )
        if proposal is not None:
            filed.append(proposal)
    return filed


# ── the accept-time installer (the one path that WRITES) ──


def is_project_context_proposal(proposal_dict: dict) -> bool:
    """Whether an accepted proposal is a project-context change this installer owns.

    Keyed on the kind (one of `PROJECT_KINDS`) rather than the tag, matching how the self-model
    installer keys on `source_cadence`: a durable record field, so a reviewer editing tags cannot
    reroute an accept away from its writer.
    """
    return str(proposal_dict.get("kind") or "") in PROJECT_KINDS


def install_accepted_project_context(proposal_dict: dict) -> bool:
    """Apply EXACTLY one accepted project-context proposal. Called only AFTER `require_human`.

    `proposals.accept` runs this per-proposal, so per-item granularity is structural: accepting
    one row writes one change, and a pending or rejected sibling is untouched. Raises on a write
    it cannot complete (an unsafe/duplicate skill name, a project that no longer resolves) so
    `accept` reports the failure and does NOT record the decision — a failed install stays
    retryable rather than silently suppressing itself.
    """
    kind = str(proposal_dict.get("kind") or "")
    project_id, name = _decode_target(str(proposal_dict.get("target") or ""))
    body = str(proposal_dict.get("body") or "").strip()
    if not project_id or not body:
        raise ValueError("project-context install needs a project id and a body")

    if kind == proposals.Kind.PROJECT_INSTRUCTION.value:
        return _install_instruction(project_id, body)
    if kind == proposals.Kind.PROJECT_FILE.value:
        return _install_file(project_id, name, body)
    if kind == proposals.Kind.PROJECT_SKILL.value:
        return _install_skill(name, body)
    raise ValueError(f"not a project-context kind: {kind!r}")


def _install_instruction(project_id: str, body: str) -> bool:
    """Append an operating instruction to the project's ``agent_instructions_template``.

    Append, not replace: the template is a standing procedure, and overwriting it on every accept
    would drop the instructions a user accepted earlier. Read-modify-write through the store so the
    same validation (default-project rules) applies as any other project edit.
    """
    from personalclaw.tasks.hierarchy import HierarchyStore

    store = HierarchyStore()
    project = store.get_project(project_id)
    if project is None:
        raise ValueError(f"project {project_id!r} no longer exists")
    existing = str(getattr(project, "agent_instructions_template", "") or "").strip()
    merged = f"{existing}\n\n{body}".strip() if existing else body
    if store.update_project(project_id, agent_instructions_template=merged) is None:
        raise ValueError(f"could not update instructions for project {project_id!r}")
    return True


def _install_file(project_id: str, name: str, body: str) -> bool:
    """Write an inlined context file under the project's ``context/`` dir.

    The filename is sanitized to a single path segment: a proposal's target is agent-authored, and
    an accepted `../` would let a review write outside the project's own context space — the exact
    traversal the surfacing dismissal path also guards.
    """
    from personalclaw.atomic_write import atomic_write
    from personalclaw.tasks.hierarchy import HierarchyStore

    safe = _safe_filename(name)
    if not safe:
        raise ValueError(f"unsafe or empty context filename: {name!r}")
    store = HierarchyStore()
    if store.get_project(project_id) is None:
        raise ValueError(f"project {project_id!r} no longer exists")
    atomic_write(store.context_dir(project_id) / safe, body)
    return True


def _install_skill(name: str, body: str) -> bool:
    """Create a new skill via the existing loader rail (no new store — E1.3).

    `create_skill` returns False on an unsafe name or an existing skill; both become a raised
    error so the accept fails rather than recording a decision for a change that did not land.
    """
    from personalclaw.skills.loader import SkillsLoader

    if not name:
        raise ValueError("a project skill needs a name")
    if not SkillsLoader().create_skill(name, body):
        raise ValueError(f"could not create skill {name!r} (unsafe name or it already exists)")
    return True


def _safe_filename(name: str) -> str:
    """Reduce a proposed filename to one safe path segment, or "".

    Rejects traversal and separators outright rather than stripping them — a name that had to be
    scrubbed to be safe is one whose author's intent is unclear, and guessing at it is how a write
    lands somewhere the reviewer did not read.
    """
    import re

    candidate = (name or "").strip()
    if not candidate or "/" in candidate or "\\" in candidate or ".." in candidate:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", candidate):
        return ""
    return candidate
