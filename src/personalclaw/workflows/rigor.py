"""The rigor axis: the CHEAP end of planning (UP-R10, S45).

Session 40 built the classifier that routes rigor; session 44 built the machinery the deep end
enters. This module is the other direction — the paths that exist so that spec-driven planning does
not become a new waterfall.

The plan states the risk plainly: *planner over-machinery for a single user*. Every heavyweight
mechanism in Universal Planning (the grill, contracts, risk gates) is entered only by classifier or
risk escalation. What keeps the cheap paths cheap is here:

* **`rigor: fast`** — an explicit "ten-minute inferior spec, start now". It skips interrogation and
  auto-schedules a refinement gate AFTER the first stage output, so refinement happens against a
  built artifact instead of up-front guessing. This is the anti-waterfall mechanism, and the
  scheduled gate is what makes it honest rather than just "skip the questions".
* **Specify** — one-click single-intent rewrite: a rough intent becomes a runnable one-stage spec.
  For exploratory work, starting is cheaper than planning.
* **revise-spec-from-artifact** — run output plus user reaction feed back into the spec, and each
  fixed defect appends to the acceptance criteria. Append-only, because a ratchet that can be
  loosened is not a ratchet.

Pure functions over spec dicts. No LLM call happens here; `specify_prompt` returns the prompt and
the caller owns the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows.intent import Intent, Rigor

#: The node id of the auto-scheduled refinement gate. Fixed, because a fast plan's refinement gate
#: must be addressable by a caller that did not build the plan.
REFINE_GATE_ID = "refine-spec"

#: Where the ratchet lives in a spec. One key, top-level, so a diff of two specs shows criteria
#: growth as a growing list rather than as prose changes scattered through prompts.
CRITERIA_KEY = "acceptance_criteria"


#: The `workflow_plan` tool's published rigor vocabulary is three-valued (`minimal`/`standard`/
#: `deep`) and `minimal` is where both TRIVIAL and FAST land. Measured: matching only the literal
#: `"fast"` made the whole fast path INERT from the one surface that can request it — a caller
#: asking for `minimal` got the standard path and a note saying so. The alias is named here rather
#: than translated at the call site so every caller gets the same answer.
_FAST_WORDS = frozenset({Rigor.FAST.value, "minimal"})


def is_fast(intent: Intent | None = None, *, requested: str = "") -> bool:
    """Whether the fast path applies.

    An explicit request wins over the classifier: `rigor: fast` is the user saying "I know this is
    a worse spec and I want to start anyway", and a classifier that overrode it would be arguing
    with a decision the user already made about their own time.
    """
    if (requested or "").strip().lower() in _FAST_WORDS:
        return True
    return bool(intent and intent.rigor is Rigor.FAST)


def schedule_refinement(spec: dict[str, Any]) -> dict[str, Any]:
    """Insert the refinement gate directly after the FIRST work-bearing node.

    After the first output, not at the end: the whole premise of the fast path is that refining
    against something built beats guessing up front, and a gate at the end refines nothing — the
    work is already done by then.

    Idempotent. A fast plan that got re-planned would otherwise accumulate a gate per pass, and a
    plan with four identical refinement gates is one a user learns to click through.
    """
    root = spec.get("root")
    if not isinstance(root, dict):
        return spec
    if _has_node(root, REFINE_GATE_ID):
        return spec
    gate = {
        "kind": "gate",
        "id": REFINE_GATE_ID,
        "config": {
            "kind": "approval",
            "prompt": (
                "First output is in. Refine the plan against it, or continue as-is?\n"
                "This is the fast path's refinement point — the spec was deliberately thin."
            ),
        },
    }
    out = dict(spec)
    out["root"] = _insert_after_first_work(root, gate)
    out["rigor"] = Rigor.FAST.value
    return out


def _has_node(node: Any, node_id: str) -> bool:
    if not isinstance(node, dict):
        return False
    if node.get("id") == node_id:
        return True
    for key in ("children", "branches"):
        if any(_has_node(c, node_id) for c in (node.get(key) or [])):
            return True
    return any(_has_node(node.get(key), node_id) for key in ("body", "then", "otherwise"))


def _insert_after_first_work(root: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    """Wrap or splice so the gate follows the first work-bearing node.

    A bare single-node root becomes a sequence — the alternative is refusing to schedule the gate
    on exactly the shape the fast path produces most often (one stage), which would make the
    mechanism inert where it matters most.
    """
    if root.get("kind") == "sequence" and isinstance(root.get("children"), list):
        children = list(root["children"])
        for index, child in enumerate(children):
            if isinstance(child, dict) and _is_work(child):
                children.insert(index + 1, gate)
                break
        else:
            children.append(gate)
        out = dict(root)
        out["children"] = children
        return out
    return {"kind": "sequence", "id": "root", "children": [root, gate]}


def _is_work(node: dict[str, Any]) -> bool:
    """A node that produces something to refine AGAINST. A gate produces a decision, not an
    artifact, so refining after one would refine against nothing new."""
    return node.get("kind") in ("stage", "action", "foreach", "loop", "parallel", "sequence")


def specify_prompt(intent_text: str) -> str:
    """The Specify prompt: a rough intent → one runnable stage.

    Deliberately constrained to ONE stage. Specify exists for the case where planning costs more
    than doing, and a Specify that emitted a five-node graph would have quietly become the planner
    it was meant to bypass.
    """
    return (
        "Rewrite this rough intent as ONE runnable instruction for a single work stage.\n\n"
        "HARD REQUIREMENTS:\n"
        "- Exactly one stage. Do not decompose, do not add review or verification steps.\n"
        "- Keep the user's own words and scope. Do not broaden the task or add goals.\n"
        "- State what the output should BE (a file, an answer, a list), since that is what makes "
        "the single stage checkable.\n"
        "- If the intent is too vague for one stage to be meaningful, say exactly "
        "`TOO_VAGUE` and nothing else — a one-stage spec built on a guess wastes the run it was "
        "meant to save.\n\n"
        f"INTENT: {intent_text}\n\n"
        "Respond with ONLY the instruction text."
    )


def specify_spec(instruction: str, *, name: str = "specified") -> dict[str, Any] | None:
    """A one-stage runnable spec from a Specify instruction.

    Returns None for the `TOO_VAGUE` sentinel, because a spec built from "TOO_VAGUE" would run a
    stage whose prompt is the model's refusal.
    """
    text = (instruction or "").strip()
    if not text or text == "TOO_VAGUE":
        return None
    return {
        "name": name,
        "rigor": Rigor.FAST.value,
        "root": {
            "kind": "stage",
            "id": "do",
            "config": {"prompt": text, "model_tier": "standard"},
        },
    }


@dataclass
class Defect:
    """One defect observed in a run's output, plus what fixed it.

    `fix` is what becomes an acceptance criterion. A defect recorded without its fix is a complaint;
    with it, it is a check the next run has to pass.
    """

    observed: str
    fix: str = ""
    node_id: str = ""

    def criterion(self) -> str:
        """The acceptance criterion this defect earns.

        Phrased as a requirement rather than as a bug report: "the summary cites its sources" is
        checkable by a judge, "the summary had no sources" is a description of one past run.
        """
        if self.fix:
            return self.fix
        return f"does not repeat: {self.observed}"


def ratchet_criteria(spec: dict[str, Any], defects: list[Defect]) -> dict[str, Any]:
    """Append each defect's criterion. APPEND-ONLY, and deduplicated.

    Append-only is the whole mechanism: a spec whose criteria can shrink is one where a later
    revision silently drops the check an earlier failure earned. Deduplication is by exact text —
    the same defect found twice must not double the list, but two similar criteria are two checks
    and collapsing them would be the planner deciding they are the same.
    """
    out = dict(spec)
    existing = list(out.get(CRITERIA_KEY) or [])
    seen = set(existing)
    for defect in defects:
        criterion = defect.criterion()
        if criterion and criterion not in seen:
            existing.append(criterion)
            seen.add(criterion)
    if existing:
        out[CRITERIA_KEY] = existing
    return out


@dataclass
class ArtifactRevision:
    """A revision derived from what a run actually produced.

    The three fields are separate because they have different authority: the run's output is a
    fact, the user's reaction is a judgement, and the criteria are the durable residue. Merging
    them would make it impossible to tell a defect the user reported from one the system inferred.
    """

    from_run: str = ""
    reaction: str = ""
    defects: list[Defect] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_run": self.from_run,
            "reaction": self.reaction,
            "defects": [
                {"observed": d.observed, "fix": d.fix, "node_id": d.node_id} for d in self.defects
            ],
            "criteria": [d.criterion() for d in self.defects],
        }


def revise_from_artifact(spec: dict[str, Any], revision: ArtifactRevision) -> dict[str, Any]:
    """Fold a run's outcome back into the spec.

    Only the ratchet and the provenance are applied here — NOT node edits. Node edits go through
    `revision.merge_patches` (session 43), whose merge-by-id is what guarantees an untouched stage
    cannot drift. A second edit path here would be a second chance to silently rewrite a stage
    nobody complained about.
    """
    out = ratchet_criteria(spec, revision.defects)
    if revision.from_run:
        provenance = dict(out.get("extra") or {})
        history = list(provenance.get("revised_from_runs") or [])
        if revision.from_run not in history:
            history.append(revision.from_run)
        provenance["revised_from_runs"] = history
        out["extra"] = provenance
    return out


def rigor_note(intent: Intent, *, requested: str = "") -> str:
    """One line for the review surface saying which rigor path ran, and why.

    A user who got a thin plan needs to know it was the fast path rather than the planner doing
    badly, and a user who got interrogated needs to know what earned it. An unexplained rigor level
    is the same legibility failure either direction.
    """
    if is_fast(intent, requested=requested):
        # Same alias set as `is_fast`, for the same measured reason: an explicit `minimal` request
        # was printing the CLASSIFIER's reason ("rigor=standard …") under a "Fast path" heading, so
        # the note contradicted its own headline and read as a bug in the router.
        explicit = (requested or "").strip().lower() in _FAST_WORDS
        why = "you asked for it" if explicit else intent.reason or "classified fast"
        return (
            f"Fast path ({why}) — deliberately thin spec, with a refinement gate after the first "
            "output rather than questions up front."
        )
    if intent.rigor is Rigor.DEEP:
        return f"Deep path ({intent.reason or 'classified deep'}) — structured interrogation first."
    if intent.rigor is Rigor.TRIVIAL:
        return "No workflow needed — this is answerable directly."
    return f"Standard path ({intent.reason or 'default'}) — grounding and contracts, no grill."
