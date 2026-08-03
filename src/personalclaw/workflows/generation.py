"""Spec generation — the prompt, the self-check, and repair-not-regenerate.

Three mechanisms, each answering a measured failure:

**A GENERATED prompt.** The hand-written YAML reference block in the plan's first revision was
stale the moment a provider changed, and a stale reference fails exactly like a hallucination:
the planner emits something the validator rejects and the error blames the spec. So the prompt is
assembled from `grounding.build_bundle()` every time, with the hard requirements placed ABOVE the
intent — a constraint after the task reads as an afterthought to a model working top-down.

**A mechanical self-check before anything is presented.** Unique ids, a terminal node, gates with
criteria, foreach with a binding, no surviving slot placeholders. These are all cheap and all
catchable without a model, and catching them here rather than at validation means the repair note
can name the fix rather than the symptom.

**Repair, not regenerate.** An invalid spec comes back with a failure-specific correction note and
the ORIGINAL spec attached, so the model edits rather than restarts. Regeneration throws away the
90% that was right and re-rolls the same dice on it — measured elsewhere in this program as the
difference between converging in one retry and oscillating between two wrong answers.

The planner may also **honestly decline**: `{cannot_plan: reason}` is a valid emission, and a
declining planner is worth far more than one that produces a plausible spec for an impossible
request.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Repair attempts before giving up and presenting the failure. Three because the measured
#: distribution is "fixed on the first retry or never" — a fourth attempt spends tokens to
#: re-learn that.
MAX_REPAIR_ATTEMPTS = 3

#: The self-check names a limited number of problems. A note listing forty issues is one nobody
#: acts on, and the first few are usually the cause of the rest.
MAX_REPORTED_ISSUES = 6


@dataclass
class SelfCheck:
    """The mechanical pre-output check. Zero tokens."""

    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def note(self) -> str:
        """The correction note handed back for repair. Names the FIX, not just the symptom."""
        if self.ok:
            return ""
        shown = self.issues[:MAX_REPORTED_ISSUES]
        more = len(self.issues) - len(shown)
        lines = ["This spec was rejected. Fix exactly these and return the CORRECTED spec:"]
        lines.extend(f"{n}. {issue}" for n, issue in enumerate(shown, start=1))
        if more:
            lines.append(f"({more} further issues will be reported after these are fixed.)")
        lines.append(
            "Return the whole corrected spec, not a diff, and change nothing that was not listed."
        )
        return "\n".join(lines)


def self_check(spec: dict[str, Any], *, shape_name: str = "") -> SelfCheck:
    """Everything wrong with a spec that can be found without a model or a validator run.

    Deliberately overlapping the validator rather than deferring to it: the validator's message
    names a spec path, which is right for an author and useless as a repair instruction to a
    model. These messages name the action.
    """
    check = SelfCheck()
    root = spec.get("root")
    if not isinstance(root, dict):
        check.issues.append("the spec has no `root` object — every spec needs one root node")
        return check

    nodes = _walk(root)
    if not nodes:
        check.issues.append("`root` has no nodes")
        return check

    _check_ids(nodes, check)
    _check_kinds(nodes, check)
    _check_gates(nodes, check)
    _check_foreach(nodes, check)
    _check_loops(nodes, check)
    _check_terminal(root, nodes, check)
    _check_stopping_condition(nodes, check)
    _check_slots(spec, check, shape_name=shape_name)
    _check_bindings(spec, nodes, check)
    return check


def _walk(node: Any, out: list[dict] | None = None) -> list[dict]:
    """Every node dict in the tree. Tolerant of a malformed tree, because this runs ON malformed
    trees — raising here would turn a repairable spec into an exception."""
    out = [] if out is None else out
    if not isinstance(node, dict):
        return out
    out.append(node)
    for child in node.get("children") or []:
        _walk(child, out)
    if isinstance(node.get("body"), dict):
        _walk(node["body"], out)
    cases = node.get("cases")
    if isinstance(cases, dict):
        for case in cases.values():
            _walk(case, out)
    if isinstance(node.get("default"), dict):
        _walk(node["default"], out)
    return out


def _check_ids(nodes: list[dict], check: SelfCheck) -> None:
    seen: set[str] = set()
    for node in nodes:
        node_id = str(node.get("id", "") or "")
        if not node_id:
            # Not fatal for containers, but a node without an id cannot be bound to, and a
            # generated spec whose stages cannot reference each other is a sequence of monologues.
            if node.get("kind") in ("stage", "infer", "action", "transform", "branch"):
                check.issues.append(
                    f"a `{node.get('kind')}` node has no `id` — give every work node an id so "
                    "later nodes can bind to its output"
                )
            continue
        if node_id in seen:
            check.issues.append(
                f"node id `{node_id}` is used more than once — ids must be unique, because "
                f"`{{{{nodes.{node_id}.output}}}}` would be ambiguous"
            )
        seen.add(node_id)


def _check_kinds(nodes: list[dict], check: SelfCheck) -> None:
    from personalclaw.workflows.models import NodeKind

    valid = {k.value for k in NodeKind}
    for node in nodes:
        kind = str(node.get("kind", "") or "")
        if kind not in valid:
            check.issues.append(
                f"`{kind or '(missing)'}` is not a node kind. The only kinds are: "
                + ", ".join(sorted(valid))
            )


def _check_gates(nodes: list[dict], check: SelfCheck) -> None:
    for node in nodes:
        if node.get("kind") != "gate":
            continue
        cfg = node.get("config") or {}
        gate_kind = str(cfg.get("kind", "") or "")
        if not gate_kind:
            check.issues.append(
                f"gate `{node.get('id', '?')}` has no `config.kind` — set it to approval, judge, "
                "expression, verify_command, verify_script, event or ladder"
            )
        if gate_kind == "judge" and not cfg.get("prompt"):
            check.issues.append(
                f"judge gate `{node.get('id', '?')}` has no `config.prompt` — a judge with no "
                "criteria approves everything, which is worse than no gate"
            )
        if gate_kind == "expression" and not cfg.get("expr"):
            check.issues.append(f"expression gate `{node.get('id', '?')}` has no `config.expr`")


def _check_foreach(nodes: list[dict], check: SelfCheck) -> None:
    for node in nodes:
        if node.get("kind") != "foreach":
            continue
        if not (node.get("config") or {}).get("items"):
            check.issues.append(
                f"foreach `{node.get('id', '?')}` has no `config.items` binding — it needs to "
                "know what to iterate over"
            )
        if not isinstance(node.get("body"), dict):
            check.issues.append(f"foreach `{node.get('id', '?')}` has no `body`")


def _check_loops(nodes: list[dict], check: SelfCheck) -> None:
    for node in nodes:
        if node.get("kind") != "loop":
            continue
        cfg = node.get("config") or {}
        mode = str(cfg.get("mode", "counted") or "counted")
        if mode == "until" and not cfg.get("condition"):
            check.issues.append(
                f"loop `{node.get('id', '?')}` is mode `until` with no `config.condition` — it "
                "would exit immediately"
            )
        if mode == "counted" and not cfg.get("n"):
            check.issues.append(f"loop `{node.get('id', '?')}` is mode `counted` with no `n`")
        if mode == "until_cancelled" and not cfg.get("max_iterations"):
            # The watcher rule from the engine's own validator, restated as a repair instruction.
            check.issues.append(
                f"loop `{node.get('id', '?')}` is `until_cancelled` — it needs either a "
                "`join: any` parallel sibling to stop it or a `max_iterations` cap, or the run "
                "never ends"
            )
        if not isinstance(node.get("body"), dict):
            check.issues.append(f"loop `{node.get('id', '?')}` has no `body`")


def _check_terminal(root: dict, nodes: list[dict], check: SelfCheck) -> None:
    """Is there a node that actually produces the deliverable?

    A spec that is all containers and gates does nothing. The check is for a WORK node rather
    than a specific one, because which node is the deliverable is a judgement the planner makes.
    """
    work_kinds = {"stage", "infer", "action", "transform", "subworkflow"}
    if not any(str(n.get("kind", "")) in work_kinds for n in nodes):
        check.issues.append(
            "this spec has no work node (stage/infer/action/transform/subworkflow) — containers "
            "and gates alone produce nothing"
        )


def _check_stopping_condition(nodes: list[dict], check: SelfCheck) -> None:
    """Does anything in this spec establish that the work is DONE?

    The plan calls goal / verification / stopping-condition the minimal triple, and it is the one
    self-check rule that is about the plan rather than the syntax: a sequence of stages runs to the
    end and reports success whether or not it achieved anything, because "the last node returned"
    is not the same claim as "the goal was met".

    A single-node spec is exempt. One stage IS its own deliverable, and demanding a judge over it
    would make the cheapest legitimate plan the most ceremonious.
    """
    work_kinds = {"stage", "infer", "action", "transform", "subworkflow"}
    work = [n for n in nodes if str(n.get("kind", "")) in work_kinds]
    if len(work) <= 1:
        return

    for node in nodes:
        kind = str(node.get("kind", ""))
        cfg = node.get("config") or {}
        if kind == "gate":
            return  # any gate is a stopping condition
        if kind == "loop":
            mode = str(cfg.get("mode", "counted") or "counted")
            # `until`, `until_dry` and `counted` all terminate on a stated condition.
            # `until_cancelled` does NOT — it is checked separately, and a watcher alone is
            # the one loop shape that establishes nothing about doneness.
            if mode != "until_cancelled":
                return
        if kind == "branch":
            return  # a branch routes on a computed answer, which is a decision point

    check.issues.append(
        "nothing in this spec says when the work is DONE. Add a gate (judge, expression or "
        "verify_command) after the work, or bound a loop with a condition — a sequence of stages "
        "reports success whether or not it achieved the goal"
    )


def _check_slots(spec: dict[str, Any], check: SelfCheck, *, shape_name: str) -> None:
    """Did any `<<slot>>` placeholder survive?

    A placeholder that reaches the engine becomes a prompt literally containing `<<synthesis>>`,
    and a stage handed that produces confident output about nothing. This is the single cheapest
    catch in the file.
    """
    text = json.dumps(spec)
    survivors = sorted(set(re.findall(r"<<([a-z_]+)>>", text)))
    for slot in survivors:
        check.issues.append(
            f"the `{slot}` slot was never filled — replace `<<{slot}>>` with the real content"
        )


def _check_bindings(spec: dict[str, Any], nodes: list[dict], check: SelfCheck) -> None:
    """Every `{{nodes.X.output}}` must name a node that exists, and every root must be real.

    Measured in session 31: five templates shipped referencing `{{defaults.*}}`, which is not a
    binding root. The validator caught it, but only after the specs were written — checking here
    means the planner is told before anything is presented.
    """
    from personalclaw.workflows.bindings import refs_in

    ids = {str(n.get("id", "") or "") for n in nodes if n.get("id")}
    valid_roots = {
        "inputs",
        "nodes",
        "item",
        "iter",
        "last",
        "siblings",
        "previous",
        "brief",
        "secret",
    }
    for expr in set(refs_in(spec)):
        head = expr.split("|")[0].strip()
        root = head.split(".")[0].split(":")[0]
        if root not in valid_roots:
            check.issues.append(
                f"`{{{{{expr}}}}}` starts with `{root}`, which is not a binding root. The roots "
                "are: " + ", ".join(sorted(valid_roots))
            )
            continue
        segments = [s for s in head.split(".") if s]
        if root == "nodes" and len(segments) >= 2 and segments[1] not in ids:
            check.issues.append(
                f"`{{{{{expr}}}}}` references node `{segments[1]}`, which does not exist in this "
                "spec. Existing ids: " + ", ".join(sorted(ids))
            )


# ── the generated prompt ──


def planning_prompt(
    intent: str,
    *,
    bundle: Any,
    shape: Any = None,
    shape_reason: str = "",
    brief: str = "",
    codebase_context: str = "",
) -> str:
    """Assemble the planning prompt from live grounding.

    Order is deliberate: HARD REQUIREMENTS, then grounding, then the shape, then the intent. A
    model reading top-down treats the last thing it read as the task and everything before it as
    context — so constraints go first, and the intent goes last where it belongs.
    """
    from personalclaw.workflows import patterns

    parts: list[str] = [
        "# Produce a workflow spec",
        "",
        "## Hard requirements — not suggestions",
        "",
        "1. Use ONLY the node kinds, providers and binding roots listed below. Anything else "
        "does not exist and the spec will be rejected.",
        "2. Every work node needs an `id`. Ids must be unique.",
        "3. Action arguments go under `config.with` — never flat beside `provider`.",
        "4. Every plan needs a machine-checkable stopping condition: a gate, a verification, or "
        "a bounded loop. A plan that cannot say when it is done is not a plan.",
        "5. If you cannot plan this with what exists, return "
        '`{"cannot_plan": "<why>"}` instead of a spec. Declining is a correct answer; a '
        "plausible spec for an impossible request is not.",
        "",
    ]

    if bundle is not None:
        parts.append(bundle.index())
        parts.append("")
        if not getattr(bundle, "structured_output", False):
            parts.append(
                "Return ONE JSON object and nothing else — no prose before or after it, no "
                "markdown fence."
            )
            parts.append("")

    if shape is not None:
        parts.extend(
            [
                f"## Use this shape: `{shape.name}`",
                shape.summary,
                f"(chosen because: {shape_reason})" if shape_reason else "",
                "",
                "Fill its slots. The structure below already runs — replace the `<<slot>>` "
                "placeholders and adjust ids/prompts, but do not restructure it without reason:",
                "```json",
                json.dumps(shape.skeleton, indent=2),
                "```",
                f"NOT the right shape when: {shape.when_not}",
                "",
                "If this shape genuinely does not fit, say so in one line and generate freeform.",
                "",
            ]
        )
    else:
        parts.extend([patterns.catalog(), ""])

    if brief:
        parts.extend(["## What is already known about this project", brief, ""])
    if codebase_context:
        parts.extend(["## The codebase this targets", codebase_context, ""])

    parts.extend(["## The intent", intent])
    return "\n".join(p for p in parts if p is not None)


def repair_prompt(spec: dict[str, Any], check: SelfCheck, *, attempt: int) -> str:
    """The correction turn. Carries the ORIGINAL spec so the model edits rather than restarts.

    Regenerating throws away everything that was right and re-rolls the same dice on it. The
    attempt number is stated because a model on its third correction should tighten rather than
    explore.
    """
    return "\n".join(
        [
            f"## Correction (attempt {attempt} of {MAX_REPAIR_ATTEMPTS})",
            "",
            check.note(),
            "",
            "The spec you produced:",
            "```json",
            json.dumps(spec, indent=2),
            "```",
        ]
    )


# ── the decline path ──


def parse_emission(raw: Any) -> tuple[dict[str, Any] | None, str]:
    """Read a planner emission. Returns `(spec, decline_reason)`.

    Exactly one is populated. A `cannot_plan` is a SUCCESSFUL outcome of the planning step — the
    caller reports the reason rather than treating it as a failure, because a planner that
    declines has told the user something true.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None, ""
    if not isinstance(raw, dict):
        return None, ""
    if raw.get("cannot_plan"):
        return None, str(raw["cannot_plan"])
    # A spec may arrive bare or wrapped. Accepting both because the wrapper is a model's choice
    # and rejecting a correct spec over its envelope would be a repair loop about nothing.
    if isinstance(raw.get("root"), dict):
        return raw, ""
    inner = raw.get("spec")
    if isinstance(inner, dict) and isinstance(inner.get("root"), dict):
        return inner, ""
    return None, ""


def spec_json_schema() -> dict[str, Any]:
    """The `oneOf[WorkflowSpec, {cannot_plan}]` schema for structured emission.

    Deliberately shallow on the node tree: a fully recursive JSON Schema for the twelve node kinds
    is large enough to crowd out the grounding it is meant to complement, and the mechanical
    self-check catches structural problems more cheaply than a schema does. The schema's job here
    is to make DECLINING a first-class option the model can see in its output contract.
    """
    from personalclaw.workflows.models import NodeKind

    node = {
        "type": "object",
        "required": ["kind"],
        "properties": {
            "kind": {"type": "string", "enum": [k.value for k in NodeKind]},
            "id": {"type": "string"},
            "config": {"type": "object"},
            "children": {"type": "array", "items": {"type": "object"}},
            "body": {"type": "object"},
            "cases": {"type": "object"},
        },
    }
    return {
        "oneOf": [
            {
                "type": "object",
                "required": ["root"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "inputs": {"type": "object"},
                    "root": node,
                },
            },
            {
                "type": "object",
                "required": ["cannot_plan"],
                "properties": {"cannot_plan": {"type": "string"}},
            },
        ]
    }
