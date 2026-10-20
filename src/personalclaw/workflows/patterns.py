"""Proven graph shapes — pick a pattern and fill its slots, rather than inventing a graph.

Freeform whole-graph generation is the failure mode the plan measured: a model asked for "a
workflow spec" produces a plausible tree with invented node kinds and no verification step. A
model asked "which of these seven shapes is this, and what goes in each slot" produces a spec
whose STRUCTURE is already known-good and whose content is the only thing it had to get right.

So freeform is the explicit FALLBACK, never the default.

Each shape carries its slots and — more importantly — the condition under which it is the wrong
choice. A registry of shapes with no `when_not` is a registry that always matches something,
which is how a research question ends up as a sequential procedure.

The shapes are structural, not domain-specific. `convergent-research` is not "the research
template"; it is the shape where several independent readings converge into one answer, and that
covers a literature review, a bug hunt, and a vendor comparison alike. Domain lives in the slot
text, which is what keeps seven shapes sufficient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Shape:
    """One proven graph shape.

    `skeleton` is a real node tree with `<<slot>>` placeholders, so slot-fill is a substitution
    rather than a generation — the planner never has to reproduce the container nesting, which is
    where invented structure came from.

    Every skeleton carries a STOPPING CONDITION. Measured by the A/B harness: three of these
    shipped ending on a synthesis or selection stage, which passes the engine's structural
    validator and fails the plan's minimal goal/verification/stopping triple — a "proven shape"
    that teaches the planner to omit the thing the hard requirements demand is not proven.
    """

    name: str
    summary: str
    #: What the planner must supply. Named so a missing one is nameable in a repair note.
    slots: tuple[str, ...]
    #: When this shape is the WRONG choice. A registry without these always matches.
    when_not: str
    skeleton: dict[str, Any] = field(default_factory=dict)
    #: Intent signals that suggest this shape, for the deterministic pre-pick.
    signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "slots": list(self.slots),
            "when_not": self.when_not,
        }


def _stage(node_id: str, slot: str, tier: str = "standard") -> dict[str, Any]:
    return {
        "kind": "stage",
        "id": node_id,
        "config": {"prompt": f"<<{slot}>>", "model_tier": tier},
    }


#: The registry. Seven shapes, and the plan's list is deliberate — an eighth would overlap one of
#: these, and overlapping shapes make the pick arbitrary.
SHAPES: tuple[Shape, ...] = (
    Shape(
        name="staged-with-gates",
        summary="Do the work in ordered stages, with a check between each. The default for "
        "anything consequential.",
        slots=("stages", "gate_criteria"),
        when_not="not when the stages are independent — use fan-out-synthesis and stop paying "
        "for a sequence you do not need",
        signals=("then", "after", "step", "phase", "first", "finally"),
        skeleton={
            "kind": "sequence",
            "id": "root",
            "children": [
                _stage("work", "the work this stage does"),
                {
                    "kind": "gate",
                    "id": "check",
                    "config": {"kind": "judge", "prompt": "<<gate_criteria>>", "risk": "safe"},
                },
            ],
        },
    ),
    Shape(
        name="convergent-research",
        summary="Several independent readings of one question, converging into a single answer "
        "with its disagreements preserved.",
        slots=("question", "angles", "synthesis", "answered_when"),
        when_not="not when one source settles it — a single stage is cheaper and just as right",
        signals=("research", "compare", "evaluate", "options", "investigate", "why"),
        skeleton={
            "kind": "sequence",
            "id": "root",
            "children": [
                {
                    "kind": "parallel",
                    "id": "angles",
                    "config": {"join": "all"},
                    "children": [
                        _stage("angle-a", "the first angle on the question", "fast"),
                        _stage("angle-b", "a genuinely different angle", "fast"),
                    ],
                },
                _stage("synthesize", "synthesis: reconcile the angles, keep disagreements"),
                {
                    "kind": "gate",
                    "id": "answered",
                    "config": {
                        "kind": "judge",
                        "prompt": "<<answered_when>>",
                        "risk": "safe",
                    },
                },
            ],
        },
    ),
    Shape(
        name="fan-out-synthesis",
        summary="The same operation over many items, then one pass over the results.",
        slots=("items", "per_item", "synthesis", "covered_when"),
        when_not="not when the items are few and cheap — the fan-out overhead exceeds the work",
        signals=("each", "every", "all of", "across", "per ", "for all"),
        skeleton={
            "kind": "sequence",
            "id": "root",
            "children": [
                {
                    "kind": "foreach",
                    "id": "items",
                    "config": {"items": "<<items>>", "on_item_error": "skip"},
                    "body": _stage("per-item", "what to do with one item", "fast"),
                },
                _stage("synthesize", "synthesis over the collected results"),
                {
                    "kind": "gate",
                    "id": "covered",
                    "config": {
                        "kind": "judge",
                        "prompt": "<<covered_when>>",
                        "risk": "safe",
                    },
                },
            ],
        },
    ),
    Shape(
        name="iterative-refinement",
        summary="Produce, critique, revise — until a check passes or a streak of rounds finds "
        "nothing new.",
        slots=("produce", "critique", "stop_condition"),
        when_not="not without a stopping condition. A refinement loop with no exit is a budget "
        "burn that looks like progress",
        signals=("until", "refine", "improve", "iterate", "keep going", "polish"),
        skeleton={
            "kind": "loop",
            "id": "root",
            "config": {"mode": "until_dry", "streak": 2, "max_iterations": 6},
            "body": {
                "kind": "sequence",
                "id": "round",
                "children": [
                    _stage("produce", "produce or revise the deliverable"),
                    _stage("critique", "critique it against the goal", "reasoning"),
                ],
            },
        },
    ),
    Shape(
        name="sequential-procedure",
        summary="A known series of steps with no judgement calls. The cheapest real shape.",
        slots=("steps",),
        when_not="not when any step could fail in a way that should stop the rest — that needs "
        "staged-with-gates",
        signals=("run", "execute", "deploy", "install", "procedure", "checklist"),
        skeleton={
            "kind": "sequence",
            "id": "root",
            "children": [_stage("step-1", "the first step", "fast")],
        },
    ),
    Shape(
        name="creative-exploration",
        summary="Generate several genuinely different attempts, then pick, rather than "
        "iterating one.",
        slots=("brief", "variants", "selection", "chosen_when"),
        when_not="not when there is one right answer — variety is a cost when correctness is "
        "the criterion",
        signals=("brainstorm", "ideas", "options", "explore", "concepts", "variations"),
        skeleton={
            "kind": "sequence",
            "id": "root",
            "children": [
                {
                    "kind": "parallel",
                    "id": "variants",
                    "config": {"join": "all"},
                    "children": [
                        _stage("variant-a", "one distinct direction"),
                        _stage("variant-b", "a deliberately different direction"),
                    ],
                },
                _stage("select", "selection: pick one and say why the others lost"),
                {
                    "kind": "gate",
                    "id": "chosen",
                    "config": {"kind": "judge", "prompt": "<<chosen_when>>", "risk": "safe"},
                },
            ],
        },
    ),
    Shape(
        name="debate-macro",
        summary="Analysts fan out, then argue against each other, then a judge decides. For "
        "questions where the disagreement IS the information.",
        slots=("question", "positions", "judge_criteria"),
        when_not="not for factual lookups — staging a debate about a checkable fact spends "
        "three calls to produce an opinion",
        signals=("should we", "worth it", "trade-off", "tradeoff", "argue", "disagree", "risk"),
        skeleton={
            "kind": "sequence",
            "id": "root",
            "children": [
                {
                    "kind": "parallel",
                    "id": "positions",
                    "config": {"join": "all"},
                    "children": [
                        _stage("for", "the strongest case FOR", "reasoning"),
                        _stage("against", "the strongest case AGAINST", "reasoning"),
                    ],
                },
                {
                    "kind": "gate",
                    "id": "judge",
                    "config": {"kind": "judge", "prompt": "<<judge_criteria>>", "risk": "safe"},
                },
            ],
        },
    ),
)

SHAPES_BY_NAME = {s.name: s for s in SHAPES}


def pick_shape(intent_text: str, *, classifier_shape: str = "") -> tuple[Shape | None, str]:
    """Suggest a shape from the intent, deterministically. Returns `(shape, reason)`.

    A SUGGESTION, not a decision: the planner may override it, and `(None, reason)` when nothing
    scores is a real answer that routes to freeform. Forcing a shape on an intent that matches
    none is how a question becomes the wrong kind of workflow.

    The classifier's own shape reading (monitor/review/triage/compare) is honoured first when it
    maps — it read the whole intent, while this scores keywords.
    """
    text = f" {' '.join((intent_text or '').lower().split())} "
    if not text.strip():
        return None, "no intent text"

    mapped = {
        "compare": "debate-macro",
        "review": "staged-with-gates",
        "triage": "sequential-procedure",
        "monitor": "iterative-refinement",
    }.get(classifier_shape or "")
    if mapped:
        return SHAPES_BY_NAME[mapped], f"intent shape `{classifier_shape}` maps to {mapped}"

    scored: list[tuple[int, Shape]] = []
    for shape in SHAPES:
        hits = [sig for sig in shape.signals if sig in text]
        if hits:
            scored.append((len(hits), shape))
    if not scored:
        # Freeform is a legitimate destination. A shape picked because it scored 0 like everything
        # else is worse than no shape, because the skeleton then constrains the wrong thing.
        return None, "no shape signal matched — freeform generation with strict validation"

    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    best_score, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0
    if best_score == runner_up:
        # A tie means the signals do not distinguish, and picking the alphabetically-first shape
        # would be arbitrary precision. Say so and let the planner choose with the full list.
        tied = [s.name for n, s in scored if n == best_score]
        return None, f"shape signals tied between {', '.join(tied)} — planner should choose"
    return best, f"signals {best_score} vs {runner_up} favour {best.name}"


def catalog() -> str:
    """The shape registry as the planner sees it.

    `when_not` is included for every shape, because a planner that only reads what each shape is
    FOR will match the first plausible one.
    """
    lines = ["## Proven shapes — pick one and fill its slots", ""]
    for shape in SHAPES:
        lines.append(f"### `{shape.name}`")
        lines.append(shape.summary)
        lines.append(f"Slots: {', '.join(f'`{s}`' for s in shape.slots)}")
        lines.append(f"NOT for: {shape.when_not}")
        lines.append("")
    lines.append(
        "If none of these fits, say so and generate freeform — but a shape that nearly fits is "
        "better than a freeform tree, because its structure is already known to run."
    )
    return "\n".join(lines)


def unfilled_slots(shape: Shape, spec: dict[str, Any]) -> list[str]:
    """Which `<<slot>>` placeholders survived into the spec.

    Checked mechanically rather than trusted: a placeholder that reaches the engine becomes a
    prompt literally containing `<<synthesis>>`, and a stage given that produces confident output
    about nothing. Cheaper to catch here than to notice in a run.
    """
    import json

    text = json.dumps(spec)
    return [slot for slot in shape.slots if f"<<{slot}>>" in text]
